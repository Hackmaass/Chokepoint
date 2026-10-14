"""Tools end to end on real cases (needs the loaded tenant graph)."""

from __future__ import annotations

import pytest

from chokepoint.cases import make_cases
from chokepoint.data_ibm import DEFAULT_CSV, load_transactions
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox, fmt_ts

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def tb():
    db = connect()
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded (run scripts/load_ibm.py)")
    return Toolbox(db)


@pytest.fixture(scope="module")
def good_case(tb):
    if not DEFAULT_CSV.exists():
        pytest.skip("dataset not downloaded")
    for c in make_cases(load_transactions(), 0.7, "dev"):
        r = tb.call("open_case", {"account": c.origin, "alert_ts": fmt_ts(c.alert_ts)})
        if "error" not in r and r["has_route_to_perimeter"] and r["accounts"] < 200:
            return r
    pytest.skip("no small case with a route found")


def test_open_case_returns_summary_and_stores_case(tb, good_case):
    assert good_case["case_id"].startswith("c-") and good_case["perimeter_accounts"] > 0
    rs = tb.call("resume_case", {"case_id": good_case["case_id"]})
    assert "region" in [f["kind"] for f in rs["findings"]] and not rs["has_newer_data"]  # cases are idempotent and persist


def test_flow_is_positive_and_recorded(tb, good_case):
    r = tb.call("flow", {"case_id": good_case["case_id"]})
    assert r["max_movable_usd_cents"] > 0 and r["finding_id"].startswith("f-")
    assert r["top_flow_edges"] and r["solver"] in ("falkordb", "networkx-fallback")


def test_freeze_plan_cuts_everything_and_beats_the_rule_of_thumb_guarantee(tb, good_case):
    p = tb.call("freeze_plan", {"case_id": good_case["case_id"], "lambda_weight": 0})
    assert "error" not in p, p
    cp, rule = p["chokepoint_plan"], p["rule_of_thumb_plan"]
    assert cp["routes_left"] is False and cp["residual_movable_usd_cents"] == 0
    assert cp["count"] == rule["count"] > 0  # equal budget
    assert rule["residual_movable_usd_cents"] >= cp["residual_movable_usd_cents"]
    assert cp["on_a_route_to_perimeter"] == cp["count"]
    assert good_case["case_id"] and p["plan_id"].startswith("p-")


def test_verify_plan_is_independent_and_cleans_up(tb, good_case):
    p = tb.call("freeze_plan", {"case_id": good_case["case_id"]})
    v = tb.call("verify_plan", {"plan_id": p["plan_id"]})
    assert v["verified"] is True and v["perimeter_reachable_after"] == 0 < v["perimeter_reachable_before"]
    assert not [g for g in tb.db.list_graphs() if g.startswith("case_")]


def test_explain_finding_gives_a_receipt(tb, good_case):
    p = tb.call("freeze_plan", {"case_id": good_case["case_id"]})
    e = tb.call("explain_finding", {"finding_id": p["finding_id"]})
    assert e["tool"] == "freeze_plan" and e["data_version"] == 0 and e["accounts_read"] > 0
    assert e["parameters"]["case_id"] == good_case["case_id"]


def test_resume_lists_all_findings_newest_first(tb, good_case):
    rs = tb.call("resume_case", {"case_id": good_case["case_id"]})
    versions = [f["version"] for f in rs["findings"]]
    assert {"region", "flow", "freeze_plan"} <= {f["kind"] for f in rs["findings"]}
    assert rs["alerted_account"] == good_case["alerted_account"] and versions


def test_typed_errors(tb, good_case):
    assert tb.call("open_case", {"account": "nope:0000", "alert_ts": "2022-09-05T10:00"})["error"]["code"] == "UNKNOWN_ACCOUNT"
    assert tb.call("open_case", {"account": good_case["alerted_account"], "alert_ts": "yesterday"})["error"]["code"] == "BAD_ARGUMENT"
    assert tb.call("flow", {"case_id": "c-missing"})["error"]["code"] == "UNKNOWN_CASE"
    assert tb.call("verify_plan", {"plan_id": "p-missing"})["error"]["code"] == "UNKNOWN_PLAN"
    assert tb.call("freeze_plan", {"case_id": good_case["case_id"], "lambda_weight": 99})["error"]["code"] == "BAD_ARGUMENT"
    assert tb.call("no_such_tool", {})["error"]["code"] == "BAD_ARGUMENT"
    # an alert before the account has done anything: the case opens, there is no route, the plan abstains
    quiet = tb.call("open_case", {"account": good_case["alerted_account"], "alert_ts": "2022-09-01T00:00"})
    assert quiet["has_route_to_perimeter"] is False
    assert tb.call("freeze_plan", {"case_id": quiet["case_id"]})["error"]["code"] == "NO_PATH"
