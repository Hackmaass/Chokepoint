"""One command, no key, no model: show the graph doing the work, from the graph itself.

  uv run python scripts/graph_tour.py                       # local stack
  docker exec chokepoint-api python scripts/graph_tour.py   # the running container

It opens the default example alert, runs the tools, and prints what FalkorDB holds and computed: the region, the check on a copy
of the graph, the solver labels read back from the stored ToolCall nodes, the 11-edge max-flow repro going through the in-database
repair, the receipt chain read with one Cypher path query, and the scratch graphs before and after. It removes its own case
afterwards (holds and dispatch included) and never files a real issue.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    os.environ.pop("OPS_GITHUB_TOKEN", None)  # a demonstration must never file a real issue
    from falkordb_maxflow_repro import EDGES

    from chokepoint.algo.cut import SplitNetwork, certify, run_falkordb_maxflow, solve
    from chokepoint.db import connect
    from chokepoint.toolbox import Toolbox

    db = connect(socket_timeout=120, socket_connect_timeout=5)
    tb = Toolbox(db, salt="graph-tour")
    before = set(db.list_graphs())
    ex = next(e for e in json.loads((ROOT / "examples" / "examples.json").read_text(encoding="utf-8")) if e["account"] == "021174:800737690")
    q = lambda cypher, **p: tb.g.ro_query(cypher, params=p, timeout=60_000).result_set  # noqa: E731

    print("1. What FalkorDB holds (graph tenant_ibm)")
    print(f"   accounts: {q('MATCH (a:Account) RETURN count(a)')[0][0]:,}   transfers: {q('MATCH ()-[t:TRANSFER]->() RETURN count(t)')[0][0]:,}")

    case = tb.call("open_case", {"account": ex["account"], "alert_ts": ex["alert_ts"]})
    tb.call("flow", {"case_id": case["case_id"]})
    plan = tb.call("freeze_plan", {"case_id": case["case_id"]})
    ver = tb.call("verify_plan", {"plan_id": plan["plan_id"]})
    cp = plan["chokepoint_plan"]
    print(f"\n2. One alert, read from the graph hop by hop: {case['accounts']} accounts in the region, {case['transfer_pairs']} transfer pairs, "
          f"{case['perimeter_accounts']} on the perimeter")
    print(f"   freeze plan: {cp['count']} accounts, cost {cp['cost']}, solver recorded: {cp['solver']}")

    print("\n3. The check on a copy of the graph (GRAPH.COPY, delete the frozen accounts, shortestPath again)")
    print(f"   perimeter accounts reachable before: {ver['perimeter_reachable_before']}   after: {ver['perimeter_reachable_after']}   verified: {ver['verified']}")
    rc = ver["source_recheck"]
    print(f"   second check on the source graph:    before {rc['perimeter_reachable_before']}   after {rc['perimeter_reachable_after']}")

    print("\n4. FalkorDB's max-flow on the 11-edge network that it gets wrong, through the certify-and-repair path")
    net = SplitNetwork(source="src", exits=("x1",), costs={"a12": 261, "a15": 158, "a20": 300, "a3": 153}, edges=dict(EDGES), inf=3322,
                       accounts=("a12", "a15", "a20", "a3", "src", "x1"))
    raw = run_falkordb_maxflow(db, net, "tour_raw")
    fixed = solve(db, net, "tour_fix")
    print(f"   algo.maxFlow first pass: {raw.value if raw else None}   certificate passes: {bool(raw and certify(net, raw))}")
    print(f"   after the certificate and the in-database repair: {fixed.value}   solver label: {fixed.solver}   certified: {certify(net, fixed)}")

    order = tb.order_freeze(plan["plan_id"], "graph tour", "demonstration")
    chain = (
        "MATCH (:Case {id: $c})<-[:RUN_IN]-(tc:ToolCall)-[:PRODUCED]->(f:Finding)-[:HAS_PLAN]->(p:FreezePlan)"
        "-[:ORDERED_AS]->(o:FreezeOrder)-[:DISPATCHED_AS]->(d:Dispatch) RETURN tc.tool, tc.solver, f.kind, p.id, o.id, d.status"
    )
    print("\n5. The receipt chain, read back with one Cypher path query:")
    print("   " + chain.replace(" RETURN", "\n   RETURN"))
    for tool, solver, kind, pid, oid, status in q(chain, c=case["case_id"]):
        print(f"   -> ToolCall {tool} (solver {solver}) -> Finding {kind} -> FreezePlan {pid} -> FreezeOrder {oid} -> Dispatch {status}")
    print("   solver labels stored on this case's ToolCall nodes: " + ", ".join(
        f"{t}={s}" for t, s in q("MATCH (tc:ToolCall)-[:RUN_IN]->(:Case {id: $c}) RETURN tc.tool, tc.solver ORDER BY tc.ran_at", c=case["case_id"])))
    held = q("MATCH (a:Account {hold: $o}) RETURN count(a)", o=order["order_id"])[0][0]
    print(f"   holds placed in the graph by the approval: {held}   freeze request: {order['dispatch']['status']} (no token here, so it is queued, never sent)")

    tb.delete_case(case["case_id"])
    after = set(db.list_graphs())
    print("\n6. Cleanup: the case, its hold and its dispatch were removed")
    print(f"   scratch graphs left behind: {len(after - before)}")
    return 0 if ver["verified"] and fixed.solver == "falkordb+repair" and not (after - before) else 1


if __name__ == "__main__":
    sys.exit(main())
