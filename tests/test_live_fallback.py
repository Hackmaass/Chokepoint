"""A live run that hits a rate-limited model falls back to the saved run for an example alert, and says so."""

from __future__ import annotations

import json

import httpx
import pytest

import chokepoint.api as api
from chokepoint import llm as llm_mod
from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect

pytestmark = pytest.mark.db
EX = json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8"))[0]


@pytest.fixture()
def rate_limited(monkeypatch, status=429):
    if "tenant_ibm" not in connect().list_graphs():
        pytest.skip("tenant_ibm not loaded")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_mod.time, "sleep", lambda _s: None)
    real = llm_mod.LLMClient.__init__

    def init(self, *a, **kw):
        real(self, *a, **kw)
        self._client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text="slow down")))

    monkeypatch.setattr(llm_mod.LLMClient, "__init__", init)


def run(task):
    st = {"events": [], "done": False, "created": 0}
    assert api._run_slots.acquire(blocking=False)  # _execute hands the slot back
    api._execute("fb", st, api.RunRequest(task=task, mode="live"))
    return st["events"]


def test_a_429_on_an_example_alert_shows_a_recorded_run_and_says_so(rate_limited):
    events = run(f"Account {EX['account']} was alerted at {EX['alert_ts']}. Investigate and recommend which accounts to freeze.")
    kinds = [e["type"] for e in events]
    assert "fallback" in kinds and "recorded run" in events[kinds.index("fallback")]["message"]
    card = events[-1]
    assert card["type"] == "card" and card["mode"] == "replay" and not card["stopped"]


def test_a_custom_question_has_no_recorded_run_so_it_reports_the_failure(rate_limited):
    events = run(f"Account {EX['account']} was alerted at {EX['alert_ts']}. What if I can only freeze 2 accounts?")
    assert "fallback" not in [e["type"] for e in events]
    assert events[-1]["type"] == "card" and events[-1]["stopped"] == "llm_error" and events[-1]["mode"] == "live"


def test_a_rejected_key_stops_live_mode_from_being_offered(rate_limited, monkeypatch):
    monkeypatch.setattr(llm_mod.LLMClient, "_post", lambda self, body: (_ for _ in ()).throw(llm_mod.LLMError('LLM HTTP 403: {"detail":"Authorization failed"}')))
    api._live_rejected.clear()
    assert api.live_available()
    events = run(f"Account {EX['account']} was alerted at {EX['alert_ts']}. Investigate and recommend which accounts to freeze.")
    assert any(e["type"] == "fallback" for e in events) and events[-1]["mode"] == "replay"  # this alert still gets its saved run
    assert not api.live_available()  # and the health probe now says live is off, so the UI stops defaulting to it
    api._live_rejected.clear()
