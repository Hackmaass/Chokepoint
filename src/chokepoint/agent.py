"""The investigation agent: a manual tool loop over the Toolbox. The model chooses tools and arguments and
writes a short narrative; every number on screen comes from tool results (see ``answer_card``)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from .llm import LLMClient, LLMError
from .toolbox import Toolbox

TOOL_SPECS = [
    {"name": "open_case",
     "description": "Open an investigation for an alerted account at an alert time. Builds the region the money could "
                    "have reached in the window before the alert. Returns a case_id.",
     "parameters": {"type": "object", "required": ["account", "alert_ts"], "additionalProperties": False,
                    "properties": {"account": {"type": "string", "description": "account id, bank:account"},
                                   "alert_ts": {"type": "string", "description": "ISO time, e.g. 2022-09-03T10:15"},
                                   "window_days": {"type": "integer", "minimum": 1, "maximum": 14,
                                                   "description": "history to use before the alert (default 7)"},
                                   "perimeter_hops": {"type": "integer", "minimum": 2, "maximum": 4,
                                                      "description": "money this many hops away counts as out of "
                                                                     "reach (default 3)"}}}},
    {"name": "flow",
     "description": "How much money could still move from the alerted account to the perimeter (USD cents).",
     "parameters": {"type": "object", "required": ["case_id"], "additionalProperties": False,
                    "properties": {"case_id": {"type": "string"}}}},
    {"name": "freeze_plan",
     "description": "Cheapest set of accounts to freeze so no route from the alerted account to the perimeter is "
                    "left, with a same-size rule-of-thumb plan (freeze the most recent recipients) for comparison. "
                    "lambda_weight 0 minimises the number of accounts; higher values avoid busy accounts.",
     "parameters": {"type": "object", "required": ["case_id"], "additionalProperties": False,
                    "properties": {"case_id": {"type": "string"},
                                   "lambda_weight": {"type": "number", "minimum": 0, "maximum": 8},
                                   "perimeter_hops": {"type": "integer", "minimum": 2, "maximum": 4}}}},
    {"name": "verify_plan",
     "description": "Independently check a plan: remove its accounts from a copy of the graph and look for any "
                    "route to the perimeter. Always call this before recommending a plan.",
     "parameters": {"type": "object", "required": ["plan_id"], "additionalProperties": False,
                    "properties": {"plan_id": {"type": "string"}}}},
    {"name": "resume_case",
     "description": "Reload a case and its stored findings from an earlier session, and say whether newer data exists.",
     "parameters": {"type": "object", "required": ["case_id"], "additionalProperties": False,
                    "properties": {"case_id": {"type": "string"}}}},
    {"name": "refresh_case",
     "description": "After newer data has arrived (resume_case says has_newer_data), find which saved findings used "
                    "accounts that received new transfers, re-run only those, and report before and after.",
     "parameters": {"type": "object", "required": ["case_id"], "additionalProperties": False,
                    "properties": {"case_id": {"type": "string"}}}},
    {"name": "explain_finding",
     "description": "Show the receipt behind a finding: which tool run, parameters, data version and solver produced it.",
     "parameters": {"type": "object", "required": ["finding_id"], "additionalProperties": False,
                    "properties": {"finding_id": {"type": "string"}}}},
]

SYSTEM_PROMPT = """You are Chokepoint's investigation agent for a financial-crime team. An account has been alerted; its \
money has already moved on, so the alerted account itself is treated as frozen. Your job is to find which other \
accounts to freeze so the money cannot keep moving, and to show your work.

Rules:
1. Use the tools. Never state a number, account id or case id that did not come from a tool result.
2. Normal workflow: open_case, then flow, then freeze_plan, then verify_plan. For a case id the user already has, \
call resume_case first; if has_newer_data is true, call refresh_case and report what changed, including \
whether the recommended plan changed and whether it is still verified.
3. If open_case reports no route to the perimeter, or freeze_plan returns NO_PATH, widen window_days (up to 14) or \
perimeter_hops (up to 4), open a new case, and say what you changed and why. Stop after two widenings and say so.
4. If freeze_plan says actionable is false, do not recommend it: try perimeter_hops 2 or a shorter window, or say \
the plan is not actionable.
5. Only recommend a plan after verify_plan returns verified true. Say what verification does and does not show.
6. Final answer, at most 120 words, plain language: the accounts to freeze (how many and why those), money that could \
move before and after (USD from the tools), how the rule-of-thumb plan compares, and one limit (static view of past \
transfers; synthetic data). Do not say the plan "finds hidden accounts".
Write amounts in dollars from usd_cents / 100."""


@dataclass
class AgentRun:
    task: str
    events: list[dict] = field(default_factory=list)
    final_text: str = ""
    results: list[tuple[str, dict, dict]] = field(default_factory=list)  # (tool, args, result)
    steps: int = 0
    cached_calls: int = 0
    stopped: str = ""  # "", "max_steps", "llm_error"


def summarize(result: dict) -> str:
    """One short line for the trace."""
    if "error" in result:
        e = result["error"]
        return f"ERROR {e['code']}: {e['message']}"
    keys = ("case_id", "plan_id", "accounts", "perimeter_accounts", "has_route_to_perimeter", "max_movable_usd_cents",
            "verified", "perimeter_reachable_after", "actionable")
    out = {k: result[k] for k in keys if k in result}
    if "chokepoint_plan" in result:
        out["chokepoint_accounts"] = result["chokepoint_plan"]["count"]
        out["rule_residual_usd_cents"] = result["rule_of_thumb_plan"]["residual_movable_usd_cents"]
    return json.dumps(out)


def run_agent(task: str, toolbox: Toolbox, llm: LLMClient, max_steps: int = 10,
              on_event: Callable[[dict], None] | None = None) -> AgentRun:
    run = AgentRun(task=task)

    def emit(ev: dict) -> None:
        ev["t"] = time.time()
        run.events.append(ev)
        if on_event:
            on_event(ev)

    tools = [{"type": "function", "function": t} for t in TOOL_SPECS]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": task}]
    emit({"type": "start", "task": task})
    for step in range(1, max_steps + 1):
        run.steps = step
        try:
            res = llm.chat(messages, tools)
        except LLMError as e:
            run.stopped = "llm_error"
            emit({"type": "error", "message": str(e)})
            return run
        run.cached_calls += int(res.cached)
        msg = res.message
        calls = msg.get("tool_calls") or []
        text = (msg.get("content") or "").strip()
        if not calls:
            run.final_text = text
            emit({"type": "final", "text": text, "cached": res.cached})
            return run
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        if text:
            emit({"type": "thought", "step": step, "text": text})
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = None
            emit({"type": "tool_call", "step": step, "tool": name, "args": args, "cached": res.cached})
            t0 = time.perf_counter()
            result = ({"error": {"code": "BAD_ARGUMENT", "message": "arguments were not valid JSON", "hint": ""}}
                      if args is None else toolbox.call(name, args))
            ms = int((time.perf_counter() - t0) * 1000)
            run.results.append((name, args or {}, result))
            emit({"type": "tool_result", "step": step, "tool": name, "ms": ms, "ok": "error" not in result,
                  "summary": summarize(result), "result": result})
            model_view = {k: v for k, v in result.items() if k not in ("finding_id", "tool_call_id", "already_computed", "already_open", "data_version")}
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(model_view, sort_keys=True)[:12000]})
    run.stopped = "max_steps"
    emit({"type": "error", "message": f"stopped after {max_steps} steps without a final answer"})
    return run


def answer_card(run: AgentRun) -> dict:
    """Numbers for the UI, taken only from tool results (never from the model's text)."""
    card: dict = {}
    for name, _args, r in run.results:
        if "error" in r:
            continue
        if name == "open_case":
            card["case"] = {k: r[k] for k in ("case_id", "alerted_account", "alert_ts", "window_days",
                                              "perimeter_hops", "accounts", "perimeter_accounts",
                                              "has_route_to_perimeter", "data_version")}
        elif name == "flow":
            card["movable_usd_cents"] = r["max_movable_usd_cents"]
            card["top_flow_edges"] = r["top_flow_edges"]
        elif name == "freeze_plan":
            card["plan"] = {"plan_id": r["plan_id"], "finding_id": r["finding_id"], "actionable": r["actionable"],
                            "perimeter_hops": r["perimeter_hops"], "lambda_weight": r["lambda_weight"],
                            "before_usd_cents": r["money_movable_before_usd_cents"],
                            "chokepoint": r["chokepoint_plan"], "rule_of_thumb": r["rule_of_thumb_plan"]}
        elif name == "verify_plan":
            card["verification"] = {k: r[k] for k in ("plan_id", "verified", "statement", "method",
                                                      "perimeter_reachable_before", "perimeter_reachable_after")}
    return card
