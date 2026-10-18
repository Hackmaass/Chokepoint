"""One-command repro of the FalkorDB algo.maxFlow defect (11 edges, true maximum flow 453, FalkorDB returns 300).

    docker run --rm -d -p 6390:6379 --name fdb falkordb/falkordb:latest
    uv run python scripts/falkordb_maxflow_repro.py --port 6390     # or: pip install falkordb && python this file

Prints the module version, FalkorDB's answer, the true maximum (Edmonds-Karp, pure Python) and the flow-conservation
violation in the returned edge flows. Exit code 1 when the bug reproduces, 0 when FalkorDB answers 453.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict, deque

from falkordb import FalkorDB

EDGES = {
    ("src:out", "a15:in"): 3322, ("src:out", "a20:in"): 3322, ("a15:in", "a15:out"): 158, ("a15:out", "a20:in"): 3322,
    ("a20:in", "a20:out"): 300, ("a20:out", "x1:in"): 3322, ("a15:out", "a3:in"): 3322, ("a3:in", "a3:out"): 153,
    ("a3:out", "a12:in"): 3322, ("a12:in", "a12:out"): 261, ("a12:out", "x1:in"): 3322,
}
SRC, SINK = "src:out", "x1:in"


def true_max_flow() -> int:
    cap = defaultdict(int, EDGES)
    adj = defaultdict(set)
    for u, v in EDGES:
        adj[u].add(v)
        adj[v].add(u)
    total = 0
    while True:
        prev, q = {SRC: None}, deque([SRC])
        while q and SINK not in prev:
            u = q.popleft()
            for v in adj[u]:
                if v not in prev and cap[(u, v)] > 0:
                    prev[v] = u
                    q.append(v)
        if SINK not in prev:
            return total
        path, v = [], SINK
        while prev[v] is not None:
            path.append((prev[v], v))
            v = prev[v]
        push = min(cap[e] for e in path)
        for u, v in path:
            cap[(u, v)] -= push
            cap[(v, u)] += push
        total += push


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6379)
    ap.add_argument("--password", default=None)
    a = ap.parse_args()
    db = FalkorDB(host=a.host, port=a.port, password=a.password)
    modules = [m for m in db.connection.execute_command("MODULE", "LIST")]
    g = db.select_graph("maxflow_repro")
    try:
        g.delete()
    except Exception:
        pass
    ids = sorted({n for e in EDGES for n in e})
    g.query("UNWIND $ids AS i CREATE (:N {id: i})", params={"ids": ids})
    g.query("UNWIND $rows AS r MATCH (a:N {id: r.u}), (b:N {id: r.v}) CREATE (a)-[:E {cap: r.c}]->(b)",
            params={"rows": [{"u": u, "v": v, "c": c} for (u, v), c in EDGES.items()]})
    res = g.query("MATCH (s:N {id: $s}) MATCH (t:N {id: $t}) CALL algo.maxFlow({sourceNodes: [s], targetNodes: [t], "
                  "relationshipTypes: ['E'], capacityProperty: 'cap', nodeLabels: ['N']}) YIELD edges, edgeFlows, maxFlow "
                  "RETURN maxFlow, [e IN edges | [startNode(e).id, endNode(e).id]] AS ends, edgeFlows",
                  params={"s": SRC, "t": SINK}).result_set[0]
    got = int(round(res[0]))
    # conservation: at every inner node, flow in must equal flow out
    balance: dict[str, int] = defaultdict(int)
    for (u, v), x in zip(res[1], res[2]):
        balance[u] -= int(round(x))
        balance[v] += int(round(x))
    bad = {n: b for n, b in balance.items() if n not in (SRC, SINK) and b != 0}
    want = true_max_flow()
    print(f"modules: {modules}")
    print(f"FalkorDB algo.maxFlow = {got}; true maximum flow = {want}")
    print(f"flow-conservation violations at inner nodes (flow in minus flow out): {bad or 'none'}")
    g.delete()
    print("BUG REPRODUCES" if got != want else "OK: FalkorDB answers correctly")
    return 1 if got != want else 0


if __name__ == "__main__":
    sys.exit(main())
