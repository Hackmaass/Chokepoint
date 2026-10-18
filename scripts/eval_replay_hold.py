"""Replay the 48 h after each example alert with the Chokepoint plan and the same-size rule-of-thumb plan (docs/replay-protocol.md).

  uv run python scripts/eval_replay_hold.py        # prints the table; read-only apart from the throw-away cases it opens
"""

from __future__ import annotations

import json
from pathlib import Path

from chokepoint.db import connect
from chokepoint.toolbox import Toolbox

tb = Toolbox(connect(), salt="eval-replay")
tb.reset_demo_state()
rows = []
for ex in json.loads((Path(__file__).resolve().parents[1] / "examples" / "examples.json").read_text(encoding="utf-8")):
    c = tb.call("open_case", {"account": ex["account"], "alert_ts": ex["alert_ts"]})
    p = tb.call("freeze_plan", {"case_id": c["case_id"]})
    d = tb.replay_data(c["case_id"], p["plan_id"])
    rows.append((ex["account"], p["chokepoint_plan"]["count"], d))
tb.reset_demo_state()

usd = lambda c: f"${c / 100:,.0f}"  # noqa: E731
print("| alert | accounts | followed | Chokepoint held | Chokepoint crossed | rule held | rule crossed |\n|---|---|---|---|---|---|---|")
for acct, k, d in rows:
    print(f"| {acct} | {k} | {d['followed']} | {usd(d['chokepoint']['held'])} | {usd(d['chokepoint']['crossed'])} | "
          f"{usd(d['rule']['held'])} | {usd(d['rule']['crossed'])} |")
active = [r for r in rows if r[2]["followed"]]
tot = {k: sum(r[2][k2][s] for r in active) for k, (k2, s) in {"cp_held": ("chokepoint", "held"), "cp_crossed": ("chokepoint", "crossed"),
                                                                "rl_held": ("rule", "held"), "rl_crossed": ("rule", "crossed")}.items()}
print(f"\nOver the {len(active)} examples with at least one followed transfer: Chokepoint held {usd(tot['cp_held'])}, "
      f"{usd(tot['cp_crossed'])} crossed the perimeter; rule of thumb held {usd(tot['rl_held'])}, {usd(tot['rl_crossed'])} crossed.")
wins = sum(r[2]["chokepoint"]["crossed"] < r[2]["rule"]["crossed"] for r in active)
ties = sum(r[2]["chokepoint"]["crossed"] == r[2]["rule"]["crossed"] for r in active)
print(f"Per example, less crossed with Chokepoint: {wins}; equal: {ties}; more: {len(active) - wins - ties}.")
