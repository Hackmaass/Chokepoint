# AI-assisted work log

I use an AI coding assistant (Claude Code) throughout this project. This log records, per day, what it was used for. I review all code and can explain the graph model, queries, algorithm and evaluation. The agent inside the product uses a free NVIDIA Nemotron model; all numbers shown to users come from deterministic tools.

## 2026-10-15 (IST)
- 00:03 Event opened. AI assistant started the hour-0 gates and repo scaffold from planning notes prepared on 2026-10-14 (planning only, no application code).
- 00:17 Scaffold, DB config, oracle tests for the vertex-cut solver. Found and characterised a FalkorDB `algo.maxFlow` defect (see docs); added a certificate + exact fallback. Advisor consulted for the approach.
- 01:50 (about 1.5 hours in) Built, with AI assistance and my review: ingest and extraction, tool layer, agent loop, API, web app, MCP server, deployment files, ripple for late data, held-out evaluation and honest write-up. The AI found and documented a FalkorDB algo.maxFlow defect and several robustness bugs (fork failures on GRAPH.COPY, LLM timeouts, non-deterministic tool output breaking replays).
