"""A crash in the middle of recording a plan must not leave a half-recorded result that later calls trust."""

from __future__ import annotations

import json

import pytest

from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox

pytestmark = pytest.mark.db
EX = json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8"))[5]


def test_a_failed_write_is_cleaned_up_and_the_next_call_works():
    db = connect()
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded")
    tb = Toolbox(db, salt="recovery-test")
    case = tb.call("open_case", {"account": EX["account"], "alert_ts": EX["alert_ts"]})
    real_write = tb._write
    state = {"armed": True}

    def flaky(query, params):
        if state["armed"] and "CREATE (p:FreezePlan" in query:  # die after the finding exists, before the plan does
            state["armed"] = False
            raise RuntimeError("simulated crash")
        return real_write(query, params)

    tb._write = flaky
    with pytest.raises(RuntimeError):
        tb.tool_freeze_plan(case["case_id"])
    tb._write = real_write
    plan = tb.call("freeze_plan", {"case_id": case["case_id"]})  # must redo the work, not trust the partial finding
    assert "error" not in plan
    assert tb.call("verify_plan", {"plan_id": plan["plan_id"]})["verified"] is True
    again = tb.call("freeze_plan", {"case_id": case["case_id"]})
    assert again["plan_id"] == plan["plan_id"] and again.get("already_computed")
