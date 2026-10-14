"""Late, back-dated transfers: only the findings whose inputs they touched are re-run (needs the loaded graph).
This test edits the database (adds a simulated batch) and resets the demo state before and after."""

from __future__ import annotations

import json

import pytest

from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox

pytestmark = pytest.mark.db
EXAMPLES = json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8"))


@pytest.fixture()
def tb():
    db = connect()
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded")
    t = Toolbox(db)
    t.reset_demo_state()
    yield t
    t.reset_demo_state()


def build(tb, ex):
    case = tb.call("open_case", {"account": ex["account"], "alert_ts": ex["alert_ts"]})
    tb.call("flow", {"case_id": case["case_id"]})
    plan = tb.call("freeze_plan", {"case_id": case["case_id"]})
    ver = tb.call("verify_plan", {"plan_id": plan["plan_id"]})
    assert ver["verified"]
    return case, plan


def test_refresh_reruns_affected_findings_and_changes_the_plan(tb):
    case, plan = build(tb, EXAMPLES[3])
    cid = case["case_id"]
    assert tb.call("resume_case", {"case_id": cid})["has_newer_data"] is False
    assert tb.call("refresh_case", {"case_id": cid})["refreshed"] is False  # nothing new

    inj = tb.inject_late_transfers(cid)
    assert inj["data_version"] == 1
    info = tb.call("resume_case", {"case_id": cid})
    assert info["has_newer_data"] and {f["status"] for f in info["findings"]} == {"stale"}

    ref = tb.call("refresh_case", {"case_id": cid})
    assert ref["refreshed"] and ref["findings_rerun"] == 4 and ref["findings_carried_forward"] == 0
    assert ref["new_transfers_in_scope"] >= 3
    by_kind = {c["kind"]: c for c in ref["changes"]}
    assert by_kind["freeze_plan"]["changed"]
    before_accounts, new_accounts = by_kind["freeze_plan"]["before"]["accounts"], by_kind["freeze_plan"]["after"]["accounts"]
    assert len(set(inj["transfers"][i]["to"] for i in (0, 1))) == 2  # the simulated chain has distinct accounts
    assert any(a.startswith("000000:") for a in new_accounts)  # the plan now covers the new route
    assert len(new_accounts) == len(before_accounts) + 1  # one more account is needed to cut the bypass
    assert by_kind["flow"]["after"]["max_movable_usd_cents"] > by_kind["flow"]["before"]["max_movable_usd_cents"]
    assert by_kind["verification"]["after"]["verified"] is True

    after = tb.call("resume_case", {"case_id": cid})
    statuses = [f["status"] for f in after["findings"]]
    assert "stale" not in statuses and statuses.count("superseded") == 4 and not after["has_newer_data"]
    assert tb.call("refresh_case", {"case_id": cid})["refreshed"] is False  # idempotent
    old = tb.g.ro_query("MATCH (:Finding {status: 'superseded'})-[r:SUPERSEDED_BY]->(:Finding) RETURN count(r)")
    assert old.result_set[0][0] == 4  # history is kept, not deleted


def test_unrelated_case_is_carried_forward_without_rerunning(tb):
    case_a, _ = build(tb, EXAMPLES[3])
    case_b, _ = build(tb, EXAMPLES[1])
    tb.inject_late_transfers(case_a["case_id"])
    ref = tb.call("refresh_case", {"case_id": case_b["case_id"]})
    assert ref["refreshed"] and ref["findings_rerun"] == 0 and ref["findings_carried_forward"] == 4
    again = tb.call("open_case", {"account": EXAMPLES[1]["account"], "alert_ts": EXAMPLES[1]["alert_ts"]})
    assert again["already_open"] is True  # same case, now valid through the newer version
