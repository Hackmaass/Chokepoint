# AI-assisted work log

AI coding assistant: Claude Code (Anthropic). It did most of the research, planning, code, tests and documentation in this repository on the author's instructions. This log records what it was used for, by time, and whether the author has reviewed each part. The agent inside the product is separate (a free NVIDIA Nemotron model); the numbers on its cards come from deterministic tools, and its short summary is checked against them.

## 2026-10-15 (IST)
- 00:03 Event opened. AI assistant started the hour-0 gates and repo scaffold from planning notes prepared on 2026-10-14 (planning only, no application code).
- 00:17 Scaffold, DB config, oracle tests for the vertex-cut solver. Found and characterised a FalkorDB `algo.maxFlow` defect (see docs); added a certificate + exact fallback. Advisor consulted for the approach.
- 01:50 (about 1.5 hours in) Built by the AI assistant (author review pending): ingest and extraction, tool layer, agent loop, API, web app, MCP server, deployment files, ripple for late data, held-out evaluation and honest write-up. The AI found and documented a FalkorDB algo.maxFlow defect and several robustness bugs (fork failures on GRAPH.COPY, LLM timeouts, non-deterministic tool output breaking replays).
- Afternoon: production-hardening pass by the AI assistant (author review pending): API error handling and readiness probes, request logging, resumable event streams, database timeouts, non-root container with health checks, UI loading/error/phone states, 65 tests and a browser smoke test.

## 2026-10-16 (IST)
- Afternoon: by the AI assistant (author review pending): a terminal walkthrough of one alert (`scripts/demo.py`, `src/chokepoint/walkthrough.py`, 2 tests) that calls the same tools as the web UI's Tools-only mode and prints only tool results; a presentation-only route animation in the graph (routes appear hop by hop, accounts to freeze pulse, a "Play routes" button; it changes no figure); one browser-test check changed to wait up to 5 s for its message instead of reading the page right after the click (it failed on every run after the rebuild; it still checks the same text); a short walkthrough section in docs/deploy.md.
- Evening: by the AI assistant (author review pending): `docs/for-judges.md` (a 60-second path and claims/non-claims); `web/e2e/record-demo.mjs` (silent Replay footage of the demo beats, no model calls); corrected the late-data figures in `docs/demo-script.md` for the example alert (they had been quoted for a different alert).

## 2026-10-18 (IST)
- Afternoon: by the AI assistant (author review pending): a "Traced in (max)" column in the Compare tab (upper bound on money an account received from the alerted account's downstream network; no balances in the data); `Dockerfile.hosted` and `scripts/make_slice.py` (single-container build holding only the 8 example alerts' neighbourhoods, 4,453 transfers, checked identical to the full dataset on all 8 plans); `examples/slice/NOTICE` (data licence); a clearer unknown-account hint; judge notes on what is new and why FalkorDB is load-bearing. A second AI session redesigned the web UI the same day.
