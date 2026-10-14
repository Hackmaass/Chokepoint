# DRAFT upstream bug report (not filed; the author decides whether and where to post)

**Title:** `algo.maxFlow` returns a non-maximal, non-conserving flow on some node-split networks (4.22.0 and 6.0.2)

**Environment:** `falkordb/falkordb:v4.22.0` (module 42200) and `falkordb/falkordb:latest` (module 60002), Docker, falkordb-py 1.8.0.

**Summary:** On networks where each vertex is split into `v:in -> v:out`, `algo.maxFlow` sometimes
returns a value below the true maximum flow, and the returned `edgeFlows` violate flow
conservation. Plain random graphs (cyclic and acyclic, 260 trials) were correct.

**Rate:** about 17% of ~140 random node-split networks (24 below the true value, none above). One
~60-edge network made the procedure run until the server timeout (the per-query `TIMEOUT` was not
honoured; the server default applied; the server stayed healthy afterwards).

**Minimal repro** (11 edges, source `src:out`, sink `x1:in`, property `cap`):

```cypher
CREATE (a:N {id:'src:out'}), (b:N {id:'a15:in'}), (c:N {id:'a15:out'}), (d:N {id:'a20:in'}),
       (e:N {id:'a20:out'}), (f:N {id:'a3:in'}), (g:N {id:'a3:out'}), (h:N {id:'a12:in'}),
       (i:N {id:'a12:out'}), (t:N {id:'x1:in'}),
       (a)-[:E {cap:3322}]->(b), (b)-[:E {cap:158}]->(c), (c)-[:E {cap:3322}]->(d),
       (a)-[:E {cap:3322}]->(d), (d)-[:E {cap:300}]->(e), (e)-[:E {cap:3322}]->(t),
       (c)-[:E {cap:3322}]->(f), (f)-[:E {cap:153}]->(g), (g)-[:E {cap:3322}]->(h),
       (h)-[:E {cap:261}]->(i), (i)-[:E {cap:3322}]->(t)

MATCH (s:N {id:'src:out'}), (t:N {id:'x1:in'})
CALL algo.maxFlow({sourceNodes:[s], targetNodes:[t], relationshipTypes:['E'], capacityProperty:'cap'})
YIELD maxFlow RETURN maxFlow
```

**Expected:** 453 (300 through `a20`, 153 through `a3 -> a12`; verified with NetworkX).
**Actual:** 300. Renaming the nodes, using an explicit super-sink, or changing the "infinite"
capacity does not change the result. Nine-edge abstractions of the same shape are correct.

**Workaround we use:** accept a FalkorDB result only if the flow is feasible and no sink is
reachable in the residual graph (max-flow/min-cut certificate); otherwise solve exactly with
NetworkX. See `docs/falkordb-maxflow-findings.md` and `tests/test_cut_oracle.py`.
