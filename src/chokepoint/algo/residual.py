"""Repair a max-flow inside FalkorDB when its first pass fails the certificate.

``algo.maxFlow`` is sometimes wrong on node-split networks (docs/falkordb-maxflow-findings.md). The repair is Edmonds-Karp
on a residual scratch graph that lives in the database: each round asks FalkorDB for the shortest augmenting path
(``shortestPath`` over edges that still have residual capacity), updates the residual edges with parameterised Cypher, and
the final cut comes from one reachability query (``algo.BFS``) on the last residual graph. Python only carries the numbers
from one round to the next. The caller re-checks the result with ``certify``, so a bug here can cost time, never correctness.
"""

from __future__ import annotations

SUPER_SINK = "__T__"
MAX_AUGMENTATIONS = 20_000  # a guard against a loop that never ends; real networks need tens to hundreds

_PATH = ("MATCH (s:N {id: $s}), (t:N {id: $t}) WITH shortestPath((s)-[:R*]->(t)) AS p WHERE p IS NOT NULL "
         "RETURN [n IN nodes(p) | n.id], [r IN relationships(p) | r.c]")
_REVERSE = ("UNWIND $steps AS st MATCH (a:N {id: st.u}), (b:N {id: st.v}) "
            "MERGE (b)-[r:R]->(a) ON CREATE SET r.c = $b ON MATCH SET r.c = r.c + $b")
_FORWARD = "UNWIND $steps AS st MATCH (:N {id: st.u})-[f:R]->(:N {id: st.v}) SET f.c = f.c - $b"
_DROP_EMPTY = "UNWIND $steps AS st MATCH (:N {id: st.u})-[f:R]->(:N {id: st.v}) WHERE f.c <= 0 DELETE f"
_REACH = "MATCH (s:N {id: $s}) CALL algo.BFS(s, -1, 'R') YIELD nodes RETURN [n IN nodes | n.id]"


def repair_in_db(db, net, seed: dict[tuple[str, str], int], graph_name: str):
    """Maximum flow of ``net`` found inside FalkorDB, starting from ``seed`` (a feasible flow, or {} for none).

    Returns a FlowResult labelled "falkordb+repair" that carries the residual-reachable node set read from the database.
    """
    from .cut import FlowResult  # late import: cut imports this module

    flows = {e: f for e, f in seed.items() if f > 0}
    big = sum(net.edges.values()) + 1
    residual: dict[tuple[str, str], int] = {}
    for (u, v), cap in net.edges.items():
        f = flows.get((u, v), 0)
        if cap - f > 0:
            residual[(u, v)] = cap - f
        if f > 0:
            residual[(v, u)] = f
    for s in net.sink_nodes:
        residual[(s, SUPER_SINK)] = big
    graph = db.select_graph(graph_name)
    try:
        graph.delete()
    except Exception:
        pass  # no leftover scratch graph
    try:
        graph.query("CREATE INDEX FOR (n:N) ON (n.id)")
        graph.query("UNWIND $ids AS i CREATE (:N {id: i})",
                    params={"ids": sorted({n for e in residual for n in e} | {net.source_node})})
        graph.query("UNWIND $rows AS r MATCH (a:N {id: r.u}), (b:N {id: r.v}) CREATE (a)-[:R {c: r.c}]->(b)",
                    params={"rows": [{"u": u, "v": v, "c": c} for (u, v), c in residual.items()]})
        for _ in range(MAX_AUGMENTATIONS):
            rows = graph.query(_PATH, params={"s": net.source_node, "t": SUPER_SINK}).result_set
            if not rows:
                break
            ids, caps = rows[0]
            push = int(min(caps))
            steps = [{"u": a, "v": b} for a, b in zip(ids, ids[1:])]
            graph.query(_REVERSE, params={"steps": steps, "b": push})
            graph.query(_FORWARD, params={"steps": steps, "b": push})
            graph.query(_DROP_EMPTY, params={"steps": steps})
            for u, v in zip(ids, ids[1:]):
                if v == SUPER_SINK:
                    continue
                if (u, v) in net.edges:
                    flows[(u, v)] = flows.get((u, v), 0) + push
                else:  # a step back along an edge that carries flow: cancel some of it
                    flows[(v, u)] = flows.get((v, u), 0) - push
        else:
            raise RuntimeError("residual repair did not converge")
        rs = graph.query(_REACH, params={"s": net.source_node}).result_set  # no row when nothing is reachable
        reach = rs[0][0] if rs else []
    finally:
        try:
            graph.delete()
        except Exception:
            pass
    flows = {e: f for e, f in flows.items() if f > 0}
    value = sum(f for (u, _), f in flows.items() if u == net.source_node) - sum(
        f for (_, v), f in flows.items() if v == net.source_node)
    return FlowResult(int(value), flows, "falkordb+repair", frozenset(reach) | {net.source_node})
