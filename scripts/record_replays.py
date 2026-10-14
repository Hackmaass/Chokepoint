"""Record one live agent run per example alert into replays/ (committed), so the demo works without the model.

The task text must match the UI exactly, because replay is keyed by the full request.
Run it again after changing prompts, tools or data (the recordings are then rebuilt).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from chokepoint.agent import run_agent
from chokepoint.db import connect
from chokepoint.llm import REPLAY_DIR, LLMClient
from chokepoint.toolbox import Toolbox

examples = json.loads(Path("examples/examples.json").read_text(encoding="utf-8"))
if REPLAY_DIR.exists():
    shutil.rmtree(REPLAY_DIR)
tb = Toolbox(connect())
llm = LLMClient(mode="live", cache_dir=REPLAY_DIR)
for ex in examples:
    task = f"Account {ex['account']} was alerted at {ex['alert_ts']}. Investigate and recommend which accounts to freeze."
    run = run_agent(task, tb, llm)
    tools = [t for t, _, _ in run.results]
    print(f"{ex['account']:20s} steps {run.steps} tools {tools} stopped {run.stopped or '-'} words {len((run.final_text or '').split())}")
print("recordings:", len(list(REPLAY_DIR.glob('*.json'))))
