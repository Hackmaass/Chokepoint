"""Pick example alerts for the demo screen (and delete the trial cases it creates).

Selection uses laundering labels (case selection only, disclosed in the README): the origin of a laundering
pattern and an alert part-way through it. Examples must be readable (small region), have a route, an actionable
plan, and show a clear gap: the rule-of-thumb plan of the same size leaves money movable.
"""

from __future__ import annotations

import json
from pathlib import Path

from chokepoint.cases import make_cases
from chokepoint.data_ibm import load_transactions
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox, fmt_ts

tb = Toolbox(connect())
df = load_transactions()
seen, picks = set(), []
for frac in (0.7, 0.5):
    for case in make_cases(df, frac, "all"):
        if case.origin in seen:
            continue
        r = tb.call("open_case", {"account": case.origin, "alert_ts": fmt_ts(case.alert_ts)})
        keep = False
        if "error" not in r and r["has_route_to_perimeter"] and r["accounts"] <= 150:
            p = tb.call("freeze_plan", {"case_id": r["case_id"]})
            if "error" not in p and p["actionable"]:
                cp, rule, before = p["chokepoint_plan"], p["rule_of_thumb_plan"], p["money_movable_before_usd_cents"]
                if (2 <= cp["count"] <= 8 and cp["residual_movable_usd_cents"] == 0 and before >= 100_000
                        and rule["residual_movable_usd_cents"] >= 0.25 * before):
                    keep = True
                    seen.add(case.origin)
                    picks.append({"account": case.origin, "alert_ts": fmt_ts(case.alert_ts), "split": case.split,
                                  "frac": frac, "accounts_in_region": r["accounts"], "plan_accounts": cp["count"],
                                  "movable_usd_cents": before,
                                  "rule_leaves_usd_cents": rule["residual_movable_usd_cents"]})
        tb.delete_case(r["case_id"]) if "case_id" in r else None
        if len(picks) >= 12:
            break
    if len(picks) >= 12:
        break
picks.sort(key=lambda x: (-x["rule_leaves_usd_cents"] / x["movable_usd_cents"], x["accounts_in_region"]))
out = Path("examples/examples.json")
out.write_text(json.dumps(picks[:8], indent=2), encoding="utf-8")
print("wrote", out, "with", len(picks[:8]), "examples (of", len(picks), "found)")
for x in picks[:8]:
    print(f"  {x['account']:22s} {x['alert_ts']} region {x['accounts_in_region']:3d} plan {x['plan_accounts']} "
          f"movable ${x['movable_usd_cents']/100:,.0f} rule leaves ${x['rule_leaves_usd_cents']/100:,.0f}")
