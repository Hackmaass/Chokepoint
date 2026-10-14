# FalkorDB `algo.maxFlow`: findings (2026-10-15, hour 0)

Measured on `falkordb/falkordb:v4.22.0` (digest in `docker-compose.yml`) and re-checked on
`falkordb/falkordb:latest` (module version 60002, i.e. 6.0.2). Client: falkordb-py 1.8.0.

## Result

On node-split networks (account → `in`/`out` pair, see `src/chokepoint/algo/cut.py`),
`algo.maxFlow` sometimes returns a flow value **below the true maximum**, and the flows it
returns for those cases **violate flow conservation** (so they are not a valid flow).

| Check (random node-split networks, source can reach an exit) | v4.22.0 | 6.0.2 |
|---|---|---|
| networks tested | 141 | 108 |
| value equals NetworkX `maximum_flow_value` | 116 (82%) | 90 (83%) |
| value below the true maximum | 24 | 17 (direction not recorded; none above on 4.22.0) |
| value above the true maximum | 0 | not recorded |
| returned flows infeasible (conservation violated) | 26 | not measured |
| query timeouts on ~100-edge graphs (10 s limit) | 1 | 1 |

Plain random graphs (not split networks), cyclic or acyclic, any capacities: 0 mismatches in 260
trials, so the defect depends on structure that node splitting produces, not on cycles or
capacity values alone. Changing the INF value (1000, 30000, 1e9) did not change the wrong answer.

## Minimal reproduction (11 edges, found by shrinking a failing random network)

Source `src:out`, sink `x1:in`. Edges (tail → head, capacity):

```
src:out -> a15:in   3322        a15:in  -> a15:out  158
src:out -> a20:in   3322        a15:out -> a20:in   3322
a20:in  -> a20:out  300         a15:out -> a3:in    3322
a20:out -> x1:in    3322        a3:in   -> a3:out   153
a3:out  -> a12:in   3322        a12:in  -> a12:out  261
a12:out -> x1:in    3322
```

True max flow (NetworkX): **453** (300 through a20, 153 through a3→a12). FalkorDB 4.22.0 and
6.0.2 both return **300**. Nine-edge abstractions of the same shape return the correct value, so
the trigger needs the longer path (length 7) alongside the shorter ones.

## What we do about it

1. The product never trusts the raw `algo.maxFlow` result. A result is accepted only if the
   returned flow is feasible (capacities and conservation) **and** the sink is unreachable in the
   residual graph (max-flow/min-cut certificate: flow value equals the cut capacity).
2. Otherwise the cut is computed exactly in NetworkX on the same network, and the result records
   which solver produced it (`solver: "falkordb" | "networkx-fallback"`).
3. FalkorDB stays load-bearing for storage, windowed extraction, traversal, provenance,
   `GRAPH.COPY`-based verification with `shortestPath`, and the graph algorithms used as tools.
4. The test suite keeps a regression test on the minimal case, so a future FalkorDB fix is noticed.
