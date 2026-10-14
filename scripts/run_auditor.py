"""Investigator tools build a plan (no model); then the auditor agent (model) reads it from the graph and records a
verdict. Shows two agents sharing state only through the graph.

  uv run python scripts/run_auditor.py            # uses the first example alert
"""

from __future__ import annotations

import json
from pathlib import Path

from chokepoint.agent import AUDITOR_PROMPT, AUDITOR_SPECS, run_agent
from chokepoint.db import connect
from chokepoint.llm import LLMClient
from chokepoint.toolbox import Toolbox

ex = json.loads(Path("examples/examples.json").read_text(encoding="utf-8"))[0]
tb = Toolbox(connect())
case = tb.call("open_case", {"account": ex["account"], "alert_ts": ex["alert_ts"]})
plan = tb.call("freeze_plan", {"case_id": case["case_id"]})
tb.call("verify_plan", {"plan_id": plan["plan_id"]})
task = f"Audit plan {plan['plan_id']} for case {case['case_id']}."
run = run_agent(task, tb, LLMClient(mode="live"), system=AUDITOR_PROMPT, specs=AUDITOR_SPECS, on_event=lambda e: print(
    f"[{e['type']}]", e.get("tool", ""), e.get("summary", e.get("text", ""))[:200] if e["type"] != "tool_call" else json.dumps(e.get("args"))))
print("\nFINAL:", run.final_text)
