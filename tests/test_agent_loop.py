"""Agent loop behaviour with a scripted fake model and a fake toolbox (no database, no network)."""

from __future__ import annotations

import json

from chokepoint.agent import answer_card, run_agent
from chokepoint.llm import ChatResult, LLMError


class FakeLLM:
    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def chat(self, messages, tools=None):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResult(item, False, 0.0)


def call(name, args, cid="1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class FakeToolbox:
    def __init__(self, results):
        self.results, self.seen = results, []

    def call(self, tool, args):
        self.seen.append((tool, args))
        return self.results[tool]


GOOD = {
    "open_case": {"case_id": "c-1", "alerted_account": "a", "alert_ts": "t", "window_days": 7, "perimeter_hops": 3,
                  "accounts": 5, "perimeter_accounts": 2, "has_route_to_perimeter": True, "data_version": 0},
    "flow": {"max_movable_usd_cents": 1000, "top_flow_edges": []},
    "freeze_plan": {"plan_id": "p-1", "finding_id": "f-1", "actionable": True, "perimeter_hops": 3,
                    "lambda_weight": 0.0, "money_movable_before_usd_cents": 1000,
                    "chokepoint_plan": {"count": 1, "residual_movable_usd_cents": 0},
                    "rule_of_thumb_plan": {"count": 1, "residual_movable_usd_cents": 600}},
    "verify_plan": {"plan_id": "p-1", "verified": True, "statement": "ok", "method": "m",
                    "perimeter_reachable_before": 2, "perimeter_reachable_after": 0},
}


def test_normal_workflow_and_answer_card():
    llm = FakeLLM([call("open_case", {"account": "a", "alert_ts": "t"}), call("flow", {"case_id": "c-1"}),
                   call("freeze_plan", {"case_id": "c-1"}), call("verify_plan", {"plan_id": "p-1"}),
                   {"role": "assistant", "content": "Freeze one account."}])
    tb = FakeToolbox(GOOD)
    run = run_agent("task", tb, llm)
    assert [t for t, _ in tb.seen] == ["open_case", "flow", "freeze_plan", "verify_plan"]
    assert run.final_text == "Freeze one account." and not run.stopped and run.steps == 5
    card = answer_card(run)
    assert card["plan"]["rule_of_thumb"]["residual_movable_usd_cents"] == 600 and card["verification"]["verified"]
    kinds = [e["type"] for e in run.events]
    assert kinds[0] == "start" and kinds[-1] == "final" and kinds.count("tool_result") == 4


def test_numbers_in_the_card_come_from_tools_not_from_text():
    llm = FakeLLM([call("flow", {"case_id": "c-1"}), {"role": "assistant", "content": "You can stop $9,999,999."}])
    run = run_agent("task", FakeToolbox(GOOD), llm)
    assert answer_card(run)["movable_usd_cents"] == 1000


def test_tool_errors_are_passed_back_and_the_loop_continues():
    err = {"error": {"code": "NO_PATH", "message": "no route", "hint": "widen"}}
    tb = FakeToolbox({**GOOD, "freeze_plan": err})
    llm = FakeLLM([call("freeze_plan", {"case_id": "c-1"}), {"role": "assistant", "content": "No route."}])
    run = run_agent("task", tb, llm)
    assert run.final_text == "No route."
    assert any(e["type"] == "tool_result" and not e["ok"] and "NO_PATH" in e["summary"] for e in run.events)


def test_invalid_json_arguments_become_a_bad_argument_result():
    bad = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": "flow", "arguments": "{not json"}}]}
    tb = FakeToolbox(GOOD)
    run = run_agent("task", tb, FakeLLM([bad, {"role": "assistant", "content": "done"}]))
    assert tb.seen == [] and run.results[0][2]["error"]["code"] == "BAD_ARGUMENT"


def test_stops_after_max_steps():
    llm = FakeLLM([call("flow", {"case_id": "c-1"}, str(i)) for i in range(5)])
    run = run_agent("task", FakeToolbox(GOOD), llm, max_steps=3)
    assert run.stopped == "max_steps" and run.steps == 3 and llm.calls == 3


def test_llm_failure_is_reported_not_raised():
    run = run_agent("task", FakeToolbox(GOOD), FakeLLM([LLMError("down")]))
    assert run.stopped == "llm_error" and run.events[-1]["type"] == "error"


def test_transport_errors_are_retried_then_reported(monkeypatch):
    import httpx

    from chokepoint.llm import LLMClient, LLMError

    calls = []

    class Boom:
        def post(self, *a, **k):
            calls.append(1)
            raise httpx.ReadTimeout("slow")

    from chokepoint.config import Settings

    settings = Settings("h", 1, None, "http://x", "m", "key", 600)
    client = LLMClient(settings, mode="off")
    client._client = Boom()
    monkeypatch.setattr("chokepoint.llm.time.sleep", lambda s: None)
    try:
        client.chat([{"role": "user", "content": "hi"}])
        raise AssertionError("expected LLMError")
    except LLMError:
        pass
    assert len(calls) == 5
