"""Run the agent on one task and print the trace.

  uv run python scripts/run_agent.py "Account 021174:800737690 was alerted at 2022-09-10T10:17. Investigate."
  uv run python scripts/run_agent.py --mode replay "..."     # serve recorded answers only
"""

from __future__ import annotations

import argparse
import json

from chokepoint.agent import answer_card, run_agent
from chokepoint.db import connect
from chokepoint.llm import LLMClient
from chokepoint.toolbox import Toolbox

ap = argparse.ArgumentParser()
ap.add_argument("task")
ap.add_argument("--mode", choices=["live", "replay", "off"], default="live")
ap.add_argument("--temperature", type=float, default=1.0)
ap.add_argument("--max-steps", type=int, default=10)
args = ap.parse_args()

run = run_agent(args.task, Toolbox(connect()), LLMClient(mode=args.mode, temperature=args.temperature),
                max_steps=args.max_steps, on_event=lambda ev: print(
                    f"[{ev['type']}]", ev.get("tool", ""), json.dumps(ev.get("args")) if ev["type"] == "tool_call" else "",
                    ev.get("summary", ev.get("text", ev.get("message", "")))[:300] if ev["type"] != "tool_call" else "",
                    flush=True))
print("\nFINAL:", run.final_text)
print("steps", run.steps, "cached calls", run.cached_calls, "stopped", run.stopped or "-")
print("answer card keys:", sorted(answer_card(run)))
