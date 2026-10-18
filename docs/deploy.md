# Deployment

The whole stack is one `docker compose` project: FalkorDB, the API with the built web UI, and a one-off seed job.
Nothing in it needs a managed service. Sizing and the steps below are for a single always-on Linux VM.

## Sizing

| Item | Need |
|---|---|
| RAM | FalkorDB holds the graph in memory: about 1.9 GiB for the full dataset (4.49 M transfers). Use **4 GB or more** (2 GB is not enough). |
| Disk | about 6 GB (images, the append-only file, the dataset download of 91 MB plus 475 MB extracted). |
| CPU | 2 vCPU is plenty; the API is light and the solver runs on small graphs. |
| Network | outbound HTTPS to `integrate.api.nvidia.com` (live agent) and `huggingface.co` (one-time data download). |

Always-on hosts that fit (any one will do; pick one you already have an account with): a small VPS (for example a
4 GB instance at a European provider), an Oracle Cloud always-free Arm instance, or a spare machine behind a tunnel.
Free tiers that sleep or delete idle data do not work: the graph must persist, and the demo must be awake when judges look.

## Steps (Ubuntu 22.04 or later)

```bash
# 1. Docker
curl -fsSL https://get.docker.com | sh

# 2. Code and secrets (never commit .env)
git clone https://github.com/Hackmaass/Chokepoint.git && cd Chokepoint
cp .env.example .env
#   set FALKORDB_PASSWORD to a long random value
#   set NVIDIA_API_KEY (free key from build.nvidia.com); leave it empty to serve replay and tools-only modes only
#   set DEMO_MODE=0 for a public host (see "Demo endpoints" below)

# 3. Database, then the data (about 2 minutes), then the app
docker compose up -d falkordb
docker compose --profile seed run --rm seed          # downloads the dataset (checksum verified) and loads FalkorDB
docker compose --profile app up -d --build           # API and UI on 127.0.0.1:8080
curl -s localhost:8080/api/health                    # liveness: {"ok":true,...}
curl -s localhost:8080/api/ready                     # readiness: {"ready":true,...}; 503 while the graph loads
```

## Small public host (no dataset, about 100 MB of memory)

`Dockerfile.hosted` is one container: FalkorDB, a 4,453-transfer slice of the dataset (`examples/slice`, every transfer that
touches an account in the 8 example alerts' regions), the API and the UI. `scripts/make_slice.py` builds the slice; all 8
example plans (accounts, residual money, baseline activity, traced-in amounts) were checked to be identical to the full
dataset, and Replay reaches Verified for all 8 inside the container (idle memory 98 MB, tested with a 512 MB limit).
Custom alerts on accounts outside the slice answer "unknown account"; the full stack above handles any account.

```bash
docker build -f Dockerfile.hosted -t chokepoint-hosted . && docker run -p 7860:7860 chokepoint-hosted
```

It works as a Docker Space on Hugging Face (port 7860), or on Render, Fly or Railway (it reads `PORT`). The graph lives in
memory and is rebuilt from the slice in under a second on every start, so a host that sleeps and wakes loses nothing.
`DEMO_MODE=0` is the default here. Set `NVIDIA_API_KEY` on the host only if you want the live agent.

## Terminal walkthrough (no browser, no key)

With the stack up and `/api/ready` true, print one alert end to end (open case, money that could move, plan, check on a
copy of the graph, comparison with the rule of thumb, receipt) in about 4 seconds:

```bash
docker exec chokepoint-api python scripts/demo.py          # from the container, nothing to install on the host
uv run python scripts/demo.py                              # from a checkout with uv; reads FALKORDB_* from .env
```

It calls the same tools as the UI's Tools-only mode and opens the example case in the database, which does not affect
Replay for that alert (checked); after "Simulate late transfers" use Reset demo data first.

## TLS and a public URL

Put a reverse proxy in front of `127.0.0.1:8080`. With Caddy (automatic certificates):

```
demo.example.com {
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1        # keep server-sent events streaming
    }
}
```

FalkorDB (6379) and the FalkorDB Browser (3000) are bound to localhost only; do not publish them.

## Demo endpoints

`POST /api/demo/late-transfers/{case}` and `POST /api/demo/reset` edit the graph (they add or delete simulated data).
They are on by default for local use. On a **public** host set `DEMO_MODE=0`: the late-transfer button disappears and the
ripple behaviour is shown in the video and the evaluation instead. Everything else is read-mostly: tools write
only their own receipts, and ids are derived from inputs so repeated requests do not duplicate anything.

## Operations

- **Restart behaviour**: `restart: unless-stopped` on every service; FalkorDB reloads from its append-only file (about a minute for the full graph). Compose gates the API and the seed job on the database's health check, and `/api/ready` returns `503` with `state: loading` until the graph is in memory (the UI shows this and enables itself).
- **One worker only**: agent runs and their event streams live in the API process's memory, so run a single uvicorn worker (the image does) and a single API container. Scaling out would first need a shared run store.
- **Probes**: use `/api/health` for liveness (process up, no database call) and `/api/ready` for readiness (database loaded). The image's `HEALTHCHECK` uses the former, so a database restart does not make Docker kill a healthy API.
- **Logs**: one line per API call with method, path, status, milliseconds and a request id (`X-Request-ID`, echoed to the client and quoted in error bodies), plus one line per tool call and per agent run. Set `LOG_LEVEL`. Keys and passwords are never logged. Uvicorn's own access log is off to avoid duplicates.
- **Non-root**: the container runs as an unprivileged user; it writes only to `/app/llm_cache` and `/app/data`.
- **Behind a proxy**: set `TRUST_PROXY=1` so rate limits use the real client address: the **rightmost** `X-Forwarded-For` entry, which is what a single trusted proxy (Caddy, or nginx with `$proxy_add_x_forwarded_for`) appends; entries to its left are client-controlled and ignored. Without it every visitor shares the proxy's address and its limit. Do not enable it when the app is reachable without the proxy.
- **Browser smoke test**: with the stack up, `cd web && npm run e2e` (uses replay mode; no model calls).
- **Persistence**: the `falkordb_data` volume. RDB snapshots are off on purpose (a background save blocks `GRAPH.COPY`). Back up by copying the volume while the container is stopped.
- **Rebuild from scratch**: `docker compose down -v` then the steps above; the seed job is idempotent (`--force` reloads).
- **Rate limits**: the free model is limited (the client enforces 30 requests per minute and retries). At most two agent runs execute at once; a third gets HTTP 429. Replay mode serves saved runs with no model call.
- **Rotate secrets** after the event: the NVIDIA key and `FALKORDB_PASSWORD`.

## Verified path

This exact path was run from a clean clone into an isolated stack (own database, volume and ports) on 2026-10-15:
`docker compose --profile seed run --rm seed` downloaded the dataset, verified its checksum and loaded 4,487,133 transfers
(172 s); `--profile app` served the UI and API; all eight saved agent runs replayed from `replays/` against the freshly loaded
graph; a tools-only run produced the same plan id as on the development database (ids are derived from inputs); the replay and
extraction-parity tests passed against it. The stack was then removed. A public TLS host was not tested (no host chosen yet).

## Demo-day checklist

1. `GET /api/ready` returns `ready: true`; the UI loads from a clean browser over HTTPS and "Run the example alert" reaches a verified plan.
2. `POST /api/demo/reset` once (local), then open an example in **Replay** and **Tools only**; both work with no model.
3. One **Live agent** run end to end (checks the key and the model).
4. `uv run pytest` is green on the host (or in CI) and `replays/` matches the current tools (the replay test fails loudly otherwise).
5. The repository README links the live URL, the video and the blog post.
