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
curl -s localhost:8080/api/health                    # {"ok":true,...}
```

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

- **Restart behaviour**: `restart: unless-stopped` on every service; FalkorDB reloads from its append-only file (about a minute for the full graph).
- **Persistence**: the `falkordb_data` volume. RDB snapshots are off on purpose (a background save blocks `GRAPH.COPY`). Back up by copying the volume while the container is stopped.
- **Rebuild from scratch**: `docker compose down -v` then the steps above; the seed job is idempotent (`--force` reloads).
- **Rate limits**: the free model is limited (the client enforces 30 requests per minute and retries). At most two agent runs execute at once; a third gets HTTP 429. Replay mode serves saved runs with no model call.
- **Rotate secrets** after the event: the NVIDIA key and `FALKORDB_PASSWORD`.

## Demo-day checklist

1. `GET /api/health` returns ok; the UI loads from a clean browser over HTTPS.
2. `POST /api/demo/reset` once (local), then open an example in **Replay** and **Tools only**; both work with no model.
3. One **Live agent** run end to end (checks the key and the model).
4. `uv run pytest` is green on the host (or in CI) and `replays/` matches the current tools (the replay test fails loudly otherwise).
5. The repository README links the live URL, the video and the blog post.
