"""Receipts are hash-chained: the chain verifies, and a changed stored value breaks it at that record."""

from __future__ import annotations

import json

import pytest

from chokepoint.chain import chain_hash, verify_chain
from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox

pytestmark = pytest.mark.db
EX = next(e for e in json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8")) if e["account"] == "021174:800737690")


@pytest.fixture()
def tb():
    db = connect()
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded")
    t = Toolbox(db, salt="chain-test")
    t.reset_demo_state()
    yield t
    t.reset_demo_state()


def ordered_case(tb):
    case = tb.call("open_case", {"account": EX["account"], "alert_ts": EX["alert_ts"]})
    plan = tb.call("freeze_plan", {"case_id": case["case_id"]})
    assert tb.call("verify_plan", {"plan_id": plan["plan_id"]})["verified"]
    order = tb.order_freeze(plan["plan_id"], "A. Rao", "reason ünïcode")
    return case["case_id"], plan, order


def test_the_whole_chain_verifies_and_the_order_hash_is_the_one_in_the_issue(tb):
    cid, plan, order = ordered_case(tb)
    recs = tb.chain(cid)
    assert {r["type"] for r in recs} == {"ToolCall", "Finding", "FreezePlan", "FreezeOrder", "Dispatch"}
    res = verify_chain(recs)
    assert res["ok"] and res["checked"] == len(recs) and res["unchained"] == 0
    assert next(r for r in recs if r["type"] == "FreezeOrder")["hash"] == order["chain_hash"]
    assert chain_hash(next(r for r in recs if r["type"] == "Finding")["canon"], recs[0]["hash"]) in {r["hash"] for r in recs}


def test_changing_one_stored_finding_breaks_the_chain_at_that_finding(tb):
    cid, plan, _ = ordered_case(tb)
    fid = plan["finding_id"]
    tb._write("MATCH (f:Finding {id: $f}) SET f.value = $v", {"f": fid, "v": '{"tampered": true}'})
    res = verify_chain(tb.chain(cid))
    assert not res["ok"] and res["first_broken"] == {"type": "Finding", "id": fid}


def test_changing_an_order_breaks_the_chain_at_the_order(tb):
    cid, _, order = ordered_case(tb)
    tb._write("MATCH (o:FreezeOrder {id: $o}) SET o.approver = 'Someone Else'", {"o": order["order_id"]})
    res = verify_chain(tb.chain(cid))
    assert res["first_broken"] == {"type": "FreezeOrder", "id": order["order_id"]}


def test_withdrawing_an_order_does_not_break_the_chain(tb):
    cid, _, order = ordered_case(tb)
    tb.withdraw_order(order["order_id"], "A. Rao", "wrong case")  # status is mutable and not part of the hash
    assert verify_chain(tb.chain(cid))["ok"]
