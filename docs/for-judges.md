# For judges: a 60-second path

Chokepoint answers one question after an alert: **where can the stolen money still go, and which few accounts do we freeze to
stop it?** It then checks its own plan and keeps a receipt. Everything below uses synthetic data.

## See it in 60 seconds (no key, no model calls)

On a first visit the app offers a one-minute guided tour (the **Tour** button reopens it): it plays the saved example and points at the money at risk, the accounts to freeze, the check on a copy, the timing check and the Compare, Receipt and Audit tabs.

1. Open the live app (the root address is a short landing page; **Open the demo** or `#/app` goes to the app. It holds the neighbourhoods of the 8 example alerts, built from `Dockerfile.hosted`; any other account
   needs the full stack, [`deploy.md`](deploy.md)), or run it locally: follow steps 2 and 3 of [`deploy.md`](deploy.md) (copy `.env.example` to `.env`, start
   `falkordb`, run the `seed` job, then start the `app` profile). The seed step downloads the dataset (checksum verified) and
   loads it, which takes a few minutes; wait until `http://127.0.0.1:8080/api/ready` says `"ready":true`. **Replay** is the default
   mode: it plays back a saved agent run and says so on screen.
2. Click **Run the example alert**. The steps appear one at a time; the map shows the routes the money could take, hop by hop.
3. The card shows how much could still move and the accounts to freeze; the green chip says the plan was checked on a copy of
   the graph (frozen accounts deleted, then `shortestPath` to every perimeter account). The text under the chip says what that
   check is and is not.
4. **Compare** puts the plan next to a same-size rule of thumb. **Audit** lets a second agent, which sees only what the first
   stored in the graph, record a verdict. **Receipt** shows which tool run produced the plan, with its parameters, data version
   and the accounts it read.
5. Optional: **Simulate late transfers**, then **Re-run affected findings**: only the findings that read the touched accounts
   are re-run; the old ones are kept and marked superseded (**Changes** tab).

No browser? Once the stack is ready, `docker exec chokepoint-api python scripts/demo.py` prints the same chain in the terminal in
about 4 seconds. Want your own client? From a checkout, `uv run python -m chokepoint.mcp_server` serves the same tools over MCP
(stdio, or token-protected HTTP with `--http`).

## Check every number in one command

```bash
docker exec chokepoint-api python scripts/judge_check.py      # or: uv run python scripts/judge_check.py
```

No key, no model, about a minute. It re-measures the figures the landing page and this page state (all 8 example plans verified on a
copy of the graph with $0 left, $22,410 and 3 accounts for the default alert, the timing check on the 8 examples, the 11-edge
`algo.maxFlow` repro, and the certificate on random node-split networks, `--networks 120` for the full batch) and exits non-zero if
any measured line fails. The held-out evaluation figures are printed as RECORDED, never as PASS: they come from the full dataset
and the original protocol (`docs/pilot-results.md`).

## What is new here, and why FalkorDB is load-bearing

- **A FalkorDB finding with a workaround.** `algo.maxFlow` returned a non-maximal, flow-violating answer on 12 to 17% of random
  node-split networks (two batches: 14 of 120 and 24 of about 140) (an 11-edge repro is in `docs/falkordb-bug-report-draft.md`; it still returns 300 instead of 453 on
  v4.22.0). We use it as a first pass and accept an answer only with a feasibility-and-residual certificate, otherwise an exact
  solver runs: 0 false accepts on 120 random networks (`tests/test_cut_oracle.py`).
- **The graph is the working memory, not a cache.** Cases, tool runs, findings, plans, receipts, supersession of stale findings and
  the auditor's verdict are nodes and edges in the same FalkorDB graph that holds the transfers; resuming a case or re-running only
  the findings that read late-arriving accounts is a graph query.
- **Verification uses the database, not the planner.** The plan is checked on a `GRAPH.COPY` of the graph with the frozen accounts
  deleted, then `shortestPath` to every perimeter account. Take FalkorDB out and the region queries, the first-pass max flow, the
  independent check and the receipts all go with it.
- **"Traced in (max)"** in the Compare tab is the money an account received from the alerted account's downstream network inside
  the window: an upper bound on what a hold would have to cover. The dataset has no balances, so it is not a share of any balance.

## Where each thing is

| If you are looking for | Look at |
|---|---|
| The graph doing the work | FalkorDB runs the hop-by-hop region queries, `algo.maxFlow`, `GRAPH.COPY` and `shortestPath`; cases, findings, plans and receipts are nodes and edges (`src/chokepoint/toolbox.py`, README "Queries and algorithms") |
| An agent that acts | A plan to freeze specific accounts, verified before it is recommended; it declines plans that are too large (`docs/agent-eval.md`) |
| The agent's reasoning | The Steps list (each step is a stored tool run), the Receipt tab, the route animation, `scripts/demo.py` |
| Memory across sessions | Header menu "Resume a saved case…"; `resume_case`; late data via `refresh_case` (`docs/ripple-eval.md`) |
| A second agent sharing state | The auditor reads only what is in the graph (`audit_checks`, `submit_audit`); code refuses "agree" while a blocking check fails |
| Rigour of the numbers | `docs/pilot-protocol.md` (written before the held-out run), `docs/pilot-results.md`, `docs/guarantee-and-latency.md` |
| A FalkorDB finding | `docs/falkordb-maxflow-findings.md`: `algo.maxFlow` returns a non-maximal flow on 12 to 17% of random node-split networks (two batches); results are accepted only with a certificate, otherwise an exact solver runs |

## What we claim, and what we do not

- **Claim:** at equal budget the plan blocks about as much escaped laundering as the best simple rule of thumb (difference
  -0.002, 95% interval [-0.09, +0.08] on the held-out half), is more precise (+0.19 [+0.07, +0.32]), and comes with a check that
  no route to the perimeter is left in the recorded transfers.
- **Not claimed:** that it blocks more than the best simple rule, that the plan itself is time-aware (it uses a static view of
  past transfers; the Compare tab adds a timing check that keeps only time-ordered routes: on the 8 examples, 3 have no
  time-ordered route at all and 2 need fewer accounts; it was added after the held-out run, so no claim beyond those 8), or that
  results carry over to real data. The example alerts in the picker were kept where the rule of thumb leaves
  money movable; the typical case is a tie on blocked share.
- The model chooses tools and writes a short summary; every figure on the cards comes from a tool, and a figure in the summary
  that no tool returned is flagged in the interface.
- An AI coding assistant wrote most of the code under the author's direction; `docs/ai-work-log.md` lists it.
