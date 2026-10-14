"""Evaluate ripple: after late transfers arrive, does refresh_case re-run only what changed, and does it end up
with the same answers as recomputing everything from scratch?

For each held-out case: build findings (region, flow, plan, verification) at data version 0, add one late batch
(bypass route, dead-end recipient, unrelated transfer, or an old transfer before the window), refresh the case,
and compare with an independent twin case computed fresh at the new version ("recompute everything").

  uv run python scripts/eval_ripple.py --limit 40

This edits the demo data (simulated batches) and resets it after every case; do not run it while other work
needs the saved cases. Writes scratch/ripple_eval.json.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from chokepoint.cases import make_cases
from chokepoint.data_ibm import load_transactions
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox, fmt_ts

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=40)
ap.add_argument("--frac", type=float, default=0.5)
ap.add_argument("--split", default="test")
args = ap.parse_args()

db = connect()
tb = Toolbox(db)
oracle = Toolbox(db, salt="oracle")
df = load_transactions()
SCENARIOS = ["bypass", "dead_end", "unrelated", "old"]
KINDS = ["region", "flow", "freeze_plan", "verification"]


def build(t: Toolbox, case) -> tuple[dict, dict] | None:
    c = t.call("open_case", {"account": case.origin, "alert_ts": fmt_ts(case.alert_ts)})
    if "error" in c or not c["has_route_to_perimeter"] or c["accounts"] > 400:
        return None
    t.call("flow", {"case_id": c["case_id"]})
    p = t.call("freeze_plan", {"case_id": c["case_id"]})
    if "error" in p or not p["actionable"]:
        return None
    t.call("verify_plan", {"plan_id": p["plan_id"]})
    return c, p


def current_summaries(t: Toolbox, case_id: str) -> dict:
    rows = t.g.ro_query("MATCH (:Case {id: $c})-[:HAS]->(f:Finding {status: 'current'}) RETURN f.kind, f.value",
                        params={"c": case_id}).result_set
    return {k: t._summary(k, json.loads(v)) for k, v in rows}


results = []
cases = make_cases(df, args.frac, args.split)
i = 0
for case in cases:
    if len(results) >= args.limit:
        break
    tb.reset_demo_state()
    built = build(tb, case)
    if not built:
        continue
    c, _ = built
    scenario = SCENARIOS[i % len(SCENARIOS)]
    i += 1
    before = current_summaries(tb, c["case_id"])
    tb.inject_scenario(c["case_id"], scenario)
    ref = tb.call("refresh_case", {"case_id": c["case_id"]})
    after = current_summaries(tb, c["case_id"])
    # independent twin: recompute everything at the new data version
    built_o = build(oracle, case)
    truth = current_summaries(oracle, built_o[0]["case_id"]) if built_o else None
    flagged = {ch["kind"] for ch in ref.get("changes", [])} if ref.get("refreshed") else set()
    changed = {k for k in KINDS if truth and before.get(k) != truth.get(k)}
    results.append({
        "origin": case.origin, "scenario": scenario, "refreshed": ref.get("refreshed"),
        "rerun": ref.get("findings_rerun", 0), "carried": ref.get("findings_carried_forward", 0),
        "flagged": sorted(flagged), "truly_changed": sorted(changed),
        "matches_recompute": bool(truth) and after == truth,
        "missed_changes": sorted(changed - flagged), "wasted_reruns": sorted(flagged - changed),
    })
    print(f"{len(results):3d} {scenario:9s} rerun {ref.get('findings_rerun', 0)} carried {ref.get('findings_carried_forward', 0)} "
          f"changed {sorted(changed)} match {results[-1]['matches_recompute']}", flush=True)
tb.reset_demo_state()
Path("scratch").mkdir(exist_ok=True)
Path("scratch/ripple_eval.json").write_text(json.dumps(results, indent=1), encoding="utf-8")

n = len(results)
print(f"\ncases {n}")
print("final answers equal to recompute-everything:", sum(r["matches_recompute"] for r in results), "/", n)
print("changes missed by the scope rule:", sum(len(r["missed_changes"]) for r in results))
rerun = sum(r["rerun"] for r in results)
total = sum(r["rerun"] + r["carried"] for r in results)
print(f"findings re-run: {rerun} of {total} ({rerun / max(1, total):.0%}); recompute-everything would re-run {total}")
by = defaultdict(lambda: Counter())
for r in results:
    by[r["scenario"]]["cases"] += 1
    by[r["scenario"]]["rerun"] += r["rerun"]
    by[r["scenario"]]["carried"] += r["carried"]
    by[r["scenario"]]["wasted"] += len(r["wasted_reruns"])
    by[r["scenario"]]["match"] += r["matches_recompute"]
for k, v in by.items():
    print(f"  {k:9s} cases {v['cases']:2d} re-run {v['rerun']:3d} carried {v['carried']:3d} re-run but unchanged {v['wasted']:3d} correct {v['match']}")
