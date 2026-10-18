"""'Watch the money stop': the replay follows recorded post-alert transfers, never writes, and its totals reconcile."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import chokepoint.api as api
from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect
from chokepoint.fx import to_usd_cents
from chokepoint.replay import simulate
from chokepoint.toolbox import Toolbox

EX = next(e for e in json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8")) if e["account"] == "015231:803A568D0")


def tr(s, d, ts, usd, tid):
    return {"s": s, "d": d, "ts": ts, "usd": usd, "tid": tid}


def test_following_the_money_with_holds():
    ts = [tr("O", "A", 1, 100, 1), tr("A", "B", 2, 60, 2), tr("A", "P", 3, 40, 3), tr("X", "P", 4, 999, 4), tr("B", "P", 5, 60, 5)]
    _, none = simulate(ts, "O", {"P"}, set())
    assert none == {"held": 0, "crossed": 100, "moved": 160}  # O->A, A->B moved; A->P and B->P crossed; X is not tainted
    ev, held = simulate(ts, "O", {"P"}, {"B"})  # freezing B holds A->B and stops B from ever being tainted
    assert held == {"held": 60, "crossed": 40, "moved": 100} and [e["status"] for e in ev] == ["moved", "held", "crossed"]


@pytest.mark.db
class TestWithDatabase:
    @pytest.fixture()
    def tb(self):
        db = connect()
        if "tenant_ibm" not in db.list_graphs():
            pytest.skip("tenant_ibm not loaded")
        t = Toolbox(db, salt="replay-test")
        t.reset_demo_state()
        yield t
        t.reset_demo_state()

    def verified_plan(self, tb):
        case = tb.call("open_case", {"account": EX["account"], "alert_ts": EX["alert_ts"]})
        plan = tb.call("freeze_plan", {"case_id": case["case_id"]})
        assert tb.call("verify_plan", {"plan_id": plan["plan_id"]})["verified"]
        return case["case_id"], plan["plan_id"]

    def test_totals_reconcile_with_the_database_and_nothing_is_written(self, tb):
        cid, pid = self.verified_plan(tb)
        counts = ("MATCH (n) WHERE NOT n:Account RETURN count(n)")
        holds = "MATCH (a:Account) WHERE a.hold IS NOT NULL RETURN count(a)"
        before, dv = tb.g.ro_query(counts, timeout=30000).result_set, tb._case(cid)["data_version"]
        d = tb.replay_data(cid, pid)
        assert tb.g.ro_query(counts, timeout=30000).result_set == before and tb.g.ro_query(holds, timeout=30000).result_set == [[0]] and tb._case(cid)["data_version"] == dv  # read-only
        assert d["followed"] > 0 and sum(d["chokepoint"].values()) == sum(e["usd"] for e in d["events"])
        rows = tb.g.ro_query(
            "UNWIND $e AS e MATCH (a:Account {id: e.s})-[t:TRANSFER {tid: e.tid}]->(b:Account {id: e.d}) RETURN t.tid, t.amt, t.cur",
            params={"e": [{"s": e["s"], "d": e["d"], "tid": e["tid"]} for e in d["events"]]}).result_set
        want = sum(to_usd_cents(int(a), c) for _, a, c in rows)  # the amounts really are the database's
        assert want == sum(e["usd"] for e in d["events"]) and len(rows) == len(d["events"])
        assert tb.order_freeze(pid, "A. Rao", "")["accounts"]  # the order is still valid after a replay

    def test_the_stream_ends_with_both_plans_and_rejects_unknown_input(self, tb):
        cid, pid = self.verified_plan(tb)
        c = TestClient(api.app)
        api.tool_limit.hits.clear()
        with c.stream("GET", f"/api/cases/{cid}/replay", params={"plan": pid, "speed": 1e9}) as r:
            body = "".join(r.iter_text())
        assert body.count('"type": "transfer"') > 0 and "event: end" in body
        end = json.loads(body.split("event: end\ndata: ")[1].split("\n")[0])
        assert set(end["chokepoint"]) == {"held", "crossed", "moved"} and set(end["rule"]) == {"held", "crossed", "moved"}
        assert c.get(f"/api/cases/{cid}/replay", params={"plan": pid, "which": "nope"}).status_code == 400
        assert c.get(f"/api/cases/{cid}/replay", params={"plan": "p-none"}).status_code == 404
