"""Saved demo runs must replay. If a tool's output or the prompt changes, recordings stop matching and this
fails; re-record with scripts/record_replays.py."""

from __future__ import annotations

import json

import pytest

from chokepoint.agent import AUDITOR_PROMPT, AUDITOR_SPECS, run_agent
from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect
from chokepoint.llm import REPLAY_DIR, LLMClient
from chokepoint.toolbox import Toolbox

pytestmark = pytest.mark.db
EXAMPLES = PROJECT_ROOT / "examples" / "examples.json"


@pytest.fixture(scope="module")
def tb():
    db = connect()
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded")
    return Toolbox(db)


def test_every_example_replays_from_recordings(tb):
    assert REPLAY_DIR.exists() and EXAMPLES.exists()
    llm = LLMClient(mode="replay", cache_dir=REPLAY_DIR)
    for ex in json.loads(EXAMPLES.read_text(encoding="utf-8")):
        task = f"Account {ex['account']} was alerted at {ex['alert_ts']}. Investigate and recommend which accounts to freeze."
        run = run_agent(task, tb, llm)
        assert not run.stopped, (ex["account"], run.events[-1])
        assert run.cached_calls == run.steps and run.final_text
        assert [t for t, _, _ in run.results] == ["open_case", "flow", "freeze_plan", "verify_plan"]
        plan = next(r for t, _, r in run.results if t == "freeze_plan")
        audit = run_agent(f"Audit plan {plan['plan_id']} for case {plan['case_id']}.", tb, llm,
                          system=AUDITOR_PROMPT, specs=AUDITOR_SPECS)
        assert not audit.stopped and audit.cached_calls == audit.steps, (ex["account"], audit.events[-1])
        assert [t for t, _, _ in audit.results][-1] == "submit_audit"
