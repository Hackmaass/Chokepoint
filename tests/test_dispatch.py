"""The Dispatcher: holds and one freeze request per plan hash, only after a person approves. GitHub is mocked."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from chokepoint import dispatch
from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox, ToolError

pytestmark = pytest.mark.db
EX = next(e for e in json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8")) if e["account"] == "021174:800737690")


class FakeGitHub:
    """Just enough of the issues API: list, create, comment. ``down`` makes every call fail."""

    def __init__(self):
        self.issues: list[dict] = []
        self.posts: list[bytes] = []
        self.comments: list[str] = []
        self.down = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        if req.method == "GET":
            return httpx.Response(200, json=self.issues)
        if req.url.path.endswith("/comments"):
            self.comments.append(json.loads(req.content)["body"])
            return httpx.Response(201, json={})
        self.posts.append(req.content)
        n = len(self.issues) + 1
        self.issues.append({"title": json.loads(req.content)["title"], "html_url": f"https://github.com/o/ops/issues/{n}"})
        return httpx.Response(201, json=self.issues[-1])


@pytest.fixture()
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(dispatch, "transport", httpx.MockTransport(fake))
    monkeypatch.setenv("OPS_GITHUB_TOKEN", "test-token")
    return fake


@pytest.fixture()
def tb():
    db = connect()
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded")
    t = Toolbox(db, salt="dispatch-test")
    t.reset_demo_state()
    yield t
    t.reset_demo_state()


def checked_plan(tb, verify=True):
    case = tb.call("open_case", {"account": EX["account"], "alert_ts": EX["alert_ts"]})
    plan = tb.call("freeze_plan", {"case_id": case["case_id"]})
    if verify:
        assert tb.call("verify_plan", {"plan_id": plan["plan_id"]})["verified"]
    return case, plan


def held(tb) -> set[str]:
    return {r[0] for r in tb.g.ro_query("MATCH (a:Account) WHERE a.hold IS NOT NULL RETURN a.id").result_set}


def test_nothing_is_dispatched_without_an_approved_order(tb, gh):
    _, plan = checked_plan(tb, verify=False)
    with pytest.raises(ToolError) as e:
        tb.order_freeze(plan["plan_id"], "A. Rao", "")
    assert e.value.code == "NOT_VERIFIED"
    assert not gh.posts and not held(tb)
    assert tb.g.ro_query("MATCH (d:Dispatch) RETURN count(d)").result_set[0][0] == 0


def test_approval_places_holds_and_files_one_issue_whose_hash_matches(tb, gh):
    _, plan = checked_plan(tb)
    o = tb.order_freeze(plan["plan_id"], "A. Rao", "three accounts carry the money")
    d = o["dispatch"]
    assert d["status"] == "sent" and d["url"] == "https://github.com/o/ops/issues/1"
    assert held(tb) == set(plan["chokepoint_plan"]["accounts"]) and d["holds_placed"] == plan["chokepoint_plan"]["count"]
    assert len(gh.posts) == 1 and hashlib.sha256(gh.posts[0]).hexdigest() == d["payload_hash"]
    issue = json.loads(gh.posts[0])
    assert f"plan {o['plan_hash'][:8]}" in issue["title"] and issue["labels"] == [f"plan-{o['plan_hash'][:8]}"]
    for a in plan["chokepoint_plan"]["accounts"]:
        assert a in issue["body"]
    assert "Synthetic data" in issue["body"] and "A. Rao" in issue["body"] and "GRAPH.COPY" in issue["body"]
    assert "test-token" not in issue["body"]
    row = tb.g.ro_query("MATCH (:FreezeOrder {id: $o})-[:DISPATCHED_AS]->(d:Dispatch) RETURN d.channel, d.url, d.status",
                        params={"o": o["order_id"]}).result_set
    assert row == [["github-issue", d["url"], "sent"]]


def test_a_repeated_approval_does_not_file_twice(tb, gh):
    _, plan = checked_plan(tb)
    a = tb.order_freeze(plan["plan_id"], "A. Rao", "")
    b = tb.order_freeze(plan["plan_id"], "B. Iyer", "")
    assert len(gh.posts) == 1 and a["dispatch"]["dispatch_id"] == b["dispatch"]["dispatch_id"]
    # even if the graph forgot the dispatch, the issue already in the repo is found by the plan hash in its title
    tb._write("MATCH (d:Dispatch) DETACH DELETE d", {})
    c = tb.order_freeze(plan["plan_id"], "C. Nair", "")
    assert len(gh.posts) == 1 and c["dispatch"]["url"] == a["dispatch"]["url"]


def test_without_a_token_or_when_github_is_down_it_is_queued_then_retried(tb, gh, monkeypatch):
    _, plan = checked_plan(tb)
    monkeypatch.delenv("OPS_GITHUB_TOKEN")
    o = tb.order_freeze(plan["plan_id"], "A. Rao", "")
    assert o["dispatch"]["status"] == "queued" and o["dispatch"]["url"] is None and "queued" in o["statement"]
    assert not gh.posts and held(tb)  # the holds and the order stand either way
    monkeypatch.setenv("OPS_GITHUB_TOKEN", "test-token")
    gh.down = True
    assert tb.retry_dispatch(o["order_id"])["dispatch"]["status"] == "queued"
    gh.down = False
    r = tb.retry_dispatch(o["order_id"])["dispatch"]
    assert r["status"] == "sent" and len(gh.posts) == 1
    assert tb.retry_dispatch(o["order_id"])["dispatch"]["url"] == r["url"] and len(gh.posts) == 1


def test_withdrawing_lifts_holds_and_leaves_a_record(tb, gh):
    _, plan = checked_plan(tb)
    a = tb.order_freeze(plan["plan_id"], "A. Rao", "")
    b = tb.order_freeze(plan["plan_id"], "B. Iyer", "")
    assert tb.withdraw_order(b["order_id"], "B. Iyer", "changed my mind")["status"] == "withdrawn"
    assert held(tb) == set(a["accounts"]) and not gh.comments  # the first order still stands behind the request
    tb.withdraw_order(a["order_id"], "A. Rao", "wrong case")
    assert not held(tb) and len(gh.comments) == 1 and "Withdrawn" in gh.comments[0]
    assert tb.g.ro_query("MATCH (o:FreezeOrder {status: 'withdrawn'}) RETURN count(o)").result_set[0][0] == 2
    with pytest.raises(ToolError) as e:
        tb.withdraw_order(a["order_id"], "A. Rao", "")
    assert e.value.code == "ORDER_WITHDRAWN"


def test_deleting_a_case_or_resetting_removes_holds_and_dispatches(tb, gh):
    case, plan = checked_plan(tb)
    tb.order_freeze(plan["plan_id"], "A. Rao", "")
    tb.delete_case(case["case_id"])
    assert not held(tb) and tb.g.ro_query("MATCH (d:Dispatch) RETURN count(d)").result_set[0][0] == 0
    _, plan = checked_plan(tb)
    tb.order_freeze(plan["plan_id"], "A. Rao", "")
    tb.reset_demo_state()
    assert not held(tb) and tb.g.ro_query("MATCH (d:Dispatch) RETURN count(d)").result_set[0][0] == 0


def test_the_model_cannot_reach_dispatch_or_approval():
    from chokepoint import api
    from chokepoint.agent import AUDITOR_SPECS, TOOL_SPECS

    forbidden = ("order", "dispatch", "withdraw", "hold")
    names = {t["name"] for t in [*TOOL_SPECS, *AUDITOR_SPECS]} | api.TOOLS
    assert not [n for n in names if any(w in n for w in forbidden)]
    assert not [n for n in dir(Toolbox) if n.startswith("tool_") and any(w in n for w in forbidden)]
    t = Toolbox.__new__(Toolbox)  # Toolbox.call reaches only tool_* methods
    for name in ("order_freeze", "retry_dispatch", "withdraw_order", "dispatch"):
        assert t.call(name, {})["error"]["code"] == "BAD_ARGUMENT"


def test_visitor_text_cannot_mention_or_link_from_the_public_issue(tb, gh):
    _, plan = checked_plan(tb)
    evil = "@octocat [x](http://e) ![i](http://e) `x`"
    tb.order_freeze(plan["plan_id"], evil, "cc @octocat")
    body = json.loads(gh.posts[0])["body"]
    assert "`@octocat [x](http://e) ![i](http://e) 'x'`" in body and "`cc @octocat`" in body
    assert body.count("@octocat") == body.count("`@octocat") + body.count("cc @octocat`")
