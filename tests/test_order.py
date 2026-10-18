"""The human approval step: refused without a passing check or after late data, bound to the plan by a hash, input validated."""

from __future__ import annotations

import json

import pytest

from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox, ToolError

pytestmark = pytest.mark.db
EX = next(e for e in json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8")) if e["account"] == "021174:800737690")


@pytest.fixture()
def tb():
    db = connect()
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded")
    t = Toolbox(db, salt="order-test")
    t.reset_demo_state()
    yield t
    t.reset_demo_state()


def plan_of(tb, verify=True):
    case = tb.call("open_case", {"account": EX["account"], "alert_ts": EX["alert_ts"]})
    plan = tb.call("freeze_plan", {"case_id": case["case_id"]})
    if verify:
        assert tb.call("verify_plan", {"plan_id": plan["plan_id"]})["verified"]
    return case, plan


def test_the_model_cannot_reach_it(tb):
    _, plan = plan_of(tb)
    r = tb.call("order_freeze", {"plan_id": plan["plan_id"], "approver": "A. Rao", "reason": ""})
    assert r["error"]["code"] == "BAD_ARGUMENT"  # not a tool: only the HTTP endpoint behind a named approver reaches it


def test_an_unchecked_plan_cannot_be_approved(tb):
    _, plan = plan_of(tb, verify=False)
    with pytest.raises(ToolError) as e:
        tb.order_freeze(plan["plan_id"], "A. Rao", "")
    assert e.value.code == "NOT_VERIFIED"


def test_an_approval_is_bound_to_the_plan_by_a_stable_hash(tb):
    _, plan = plan_of(tb)
    a = tb.order_freeze(plan["plan_id"], "A. Rao", "three accounts carry the money")
    b = tb.order_freeze(plan["plan_id"], "B. Iyer", "")
    assert a["plan_hash"] == b["plan_hash"] and a["order_id"] != b["order_id"]  # each approval is its own order
    assert a["accounts"] == plan["chokepoint_plan"]["accounts"]
    assert "No bank is called" in a["statement"]
    n = tb.g.ro_query("MATCH (:FreezePlan {id: $p})-[:ORDERED_AS]->(o:FreezeOrder) RETURN count(o)", params={"p": plan["plan_id"]}).result_set[0][0]
    assert n == 2


def test_bad_input_is_rejected(tb):
    _, plan = plan_of(tb)
    for who, why in [("a", ""), ("x" * 61, ""), ("A. Rao", "y" * 301)]:
        with pytest.raises(ToolError) as e:
            tb.order_freeze(plan["plan_id"], who, why)
        assert e.value.code == "BAD_ARGUMENT"


def test_an_approval_cannot_outlive_its_evidence(tb):
    case, plan = plan_of(tb)
    tb.inject_late_transfers(case["case_id"])
    with pytest.raises(ToolError) as e:
        tb.order_freeze(plan["plan_id"], "A. Rao", "")
    assert e.value.code == "STALE_EVIDENCE"


def test_orders_go_with_the_case(tb):
    case, plan = plan_of(tb)
    tb.order_freeze(plan["plan_id"], "A. Rao", "")
    tb.delete_case(case["case_id"])
    assert tb.g.ro_query("MATCH (o:FreezeOrder) RETURN count(o)").result_set[0][0] == 0


def test_a_second_independent_check_on_the_source_graph_agrees(tb):
    case = tb.call("open_case", {"account": EX["account"], "alert_ts": EX["alert_ts"]})
    plan = tb.call("freeze_plan", {"case_id": case["case_id"]})
    v = tb.call("verify_plan", {"plan_id": plan["plan_id"]})
    rc = v["source_recheck"]
    assert v["verified"] and rc["perimeter_reachable_before"] > 0 and rc["perimeter_reachable_after"] == 0
