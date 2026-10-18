# Upstream issue, ready to file on FalkorDB/FalkorDB (the author files it from their own account)

Checked 2026-10-19 (IST): a search of `FalkorDB/FalkorDB` for "maxFlow" finds no issue about wrong or non-maximal results
(the only correctness issue, #2037, concerns empty relationship-type filters and is closed). Re-search before filing.

**Title:** `algo.maxFlow` returns a non-maximal flow that violates conservation on a small 11-edge network (4.22.0, latest, edge)

**Versions** (each reproduces; script output below):

| image | `MODULE LIST` graph version | `algo.maxFlow` | true maximum |
|---|---|---|---|
| `falkordb/falkordb:v4.22.0` | 42200 | 300 | 453 |
| `falkordb/falkordb:latest` | 60002 | 300 | 453 |
| `falkordb/falkordb:edge` | 999999 | 300 | 453 |

**One-command repro** (needs Docker and `pip install falkordb`; the script is self-contained):

```sh
docker run --rm -d -p 6390:6379 --name fdb falkordb/falkordb:latest
python falkordb_maxflow_repro.py --port 6390     # exit code 1 when the bug reproduces
```

(`scripts/falkordb_maxflow_repro.py` in https://github.com/Hackmaass/Chokepoint.) Output on every version above:

```
FalkorDB algo.maxFlow = 300; true maximum flow = 453
flow-conservation violations at inner nodes (flow in minus flow out): {'a20:in': 158}
BUG REPRODUCES
```

**Network** (source `src:out`, sink `x1:in`, label `N`, relationship `E`, property `cap`): 11 edges, listed in the script
and in `docs/falkordb-bug-report-draft.md`. Two disjoint-ish routes: through `a20` (cap 300) and through
`a15 -> a3 -> a12` (cap 153 at `a3`), so the true maximum is 453; FalkorDB returns 300 and its `edgeFlows` put 158 more
into `a20:in` than leave it, so the returned flow is not even feasible.

**Query used**

```cypher
MATCH (s:N {id: 'src:out'}) MATCH (t:N {id: 'x1:in'})
CALL algo.maxFlow({sourceNodes: [s], targetNodes: [t], relationshipTypes: ['E'], capacityProperty: 'cap', nodeLabels: ['N']})
YIELD edges, edgeFlows, maxFlow
RETURN maxFlow, [e IN edges | [startNode(e).id, endNode(e).id]] AS ends, edgeFlows
```

**Rate:** on random node-split networks (each vertex split into `v:in -> v:out`) 12% to 17% of results were below the true
maximum, none above (see `docs/falkordb-maxflow-findings.md`; plain random graphs were correct in 260 trials).

**Workaround we use:** accept a result only if the flow is feasible and no sink is reachable in the residual graph (the
max-flow/min-cut certificate), otherwise solve exactly.
