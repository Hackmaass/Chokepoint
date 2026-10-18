"""The auditor works through shared state: deterministic checks on the stored plan, and a verdict code will not
let it contradict (needs the loaded graph)."""

from __future__ import annotations

import json

import pytest

from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox

pytestmark = pytest.mark.db
EX = next(e for e in json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8")) if e["account"] == "021174:800737690")


@pytest.fixture(scope="module")
def tb():
    db = connect()
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded")
    return Toolbox(db)


def test_checks_pass_for_a_verified_plan_and_agree_is_recorded(tb):
    case = tb.call("open_case", {"account": EX["account"], "alert_ts": EX["alert_ts"]})
    plan = tb.call("freeze_plan", {"case_id": case["case_id"]})
    assert tb.call("verify_plan", {"plan_id": plan["plan_id"]})["verified"]
    checks = tb.call("audit_checks", {"plan_id": plan["plan_id"]})
    assert checks["blocking_failed"] == [] and all(c["passed"] for c in checks["checks"])
    res = tb.call("submit_audit", {"plan_id": plan["plan_id"], "verdict": "agree", "note": "all checks passed"})
    assert res["recorded"] and res["verdict"] == "agree"
    summary = tb.call("resume_case", {"case_id": case["case_id"]})
    audits = [f for f in summary["findings"] if f["kind"] == "audit"]
    assert any(a["summary"].get("verdict") == "agree" for a in audits)  # the investigator side sees it in the graph


def test_agree_is_refused_while_a_blocking_check_fails(tb):
    case = tb.call("open_case", {"account": EX["account"], "alert_ts": EX["alert_ts"]})
    plan = tb.call("freeze_plan", {"case_id": case["case_id"], "lambda_weight": 2.0})  # a plan nobody verified
    checks = tb.call("audit_checks", {"plan_id": plan["plan_id"]})
    assert "verified" in checks["blocking_failed"]
    refused = tb.call("submit_audit", {"plan_id": plan["plan_id"], "verdict": "agree", "note": "looks fine"})
    assert refused["error"]["code"] == "AUDIT_INCONSISTENT"
    ok = tb.call("submit_audit", {"plan_id": plan["plan_id"], "verdict": "needs_review", "note": "not verified"})
    assert ok["recorded"] and "verified" in ok["blocking_failed"]
    assert tb.call("submit_audit", {"plan_id": plan["plan_id"], "verdict": "maybe"})["error"]["code"] == "BAD_ARGUMENT"
    assert tb.call("audit_checks", {"plan_id": "p-missing"})["error"]["code"] == "UNKNOWN_PLAN"
