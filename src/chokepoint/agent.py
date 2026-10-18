"""The investigation agent: a manual tool loop over the Toolbox. The model chooses tools and arguments and
writes a short narrative; the numbers on the cards come from tool results (see ``answer_card``) and the narrative is
checked against them (see ``faithful.py``)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .faithful import check as check_faithful
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
6. If open_case returns CASE_TOO_LARGE even with window_days 1 and perimeter_hops 2, stop: the alerted account is a \
high-volume hub that cannot be analysed here. Say so; do not keep retrying. Never repeat a call that already failed \
with the same arguments.
7. Final answer, at most 120 words, plain language: the accounts to freeze (how many and why those), money that could \
move before and after (USD from the tools), how the rule-of-thumb plan compares, and one limit (static view of past \
transfers; synthetic data). Do not say the plan "finds hidden accounts".
Write amounts in dollars from usd_cents / 100."""


AUDITOR_SPECS = [
    {"name": "audit_checks",
     "description": "Run the deterministic checks on a stored freeze plan: verified, current, actionable, no routes "
                    "left, frozen accounts on a route, not worse than the rule of thumb. Read-only.",
     "parameters": {"type": "object", "required": ["plan_id"], "additionalProperties": False,
                    "properties": {"plan_id": {"type": "string"}}}},
    {"name": "explain_finding",
     "description": "Show the receipt behind a finding: tool run, parameters, data version and solver.",
     "parameters": {"type": "object", "required": ["finding_id"], "additionalProperties": False,
                    "properties": {"finding_id": {"type": "string"}}}},
    {"name": "resume_case",
     "description": "Read the case and all its stored findings, including earlier audits.",
     "parameters": {"type": "object", "required": ["case_id"], "additionalProperties": False,
                    "properties": {"case_id": {"type": "string"}}}},
    {"name": "submit_audit",
     "description": "Record your verdict on the plan in the shared graph. You cannot agree while a blocking check fails.",
     "parameters": {"type": "object", "required": ["plan_id", "verdict", "note"], "additionalProperties": False,
                    "properties": {"plan_id": {"type": "string"},
                                   "verdict": {"type": "string", "enum": ["agree", "disagree", "needs_review"]},
                                   "note": {"type": "string", "description": "at most 60 words, naming the checks"}}}},
]

AUDITOR_PROMPT = """You are the auditor in a two-agent team. The investigator agent has stored a case and a freeze plan \
in a shared graph. You can see only what is stored there, not the investigator's messages or reasoning.
Given a plan id: call audit_checks, read the receipt with explain_finding or the case with resume_case if you need \
more, then call submit_audit once with a verdict (agree, disagree or needs_review) and a note of at most 60 words \
that names the checks you relied on. Never agree when a blocking check fails. Do not state numbers that did not come \
from a tool. After submit_audit succeeds, reply with one sentence for the user."""


@dataclass
class AgentRun:
    task: str
    events: list[dict] = field(default_factory=list)
    final_text: str = ""
    results: list[tuple[str, dict, dict]] = field(default_factory=list)  # (tool, args, result)
    steps: int = 0
    cached_calls: int = 0
    stopped: str = ""  # "", "max_steps", "llm_error"
    faithful: dict = field(default_factory=dict)  # figures in the narrative that no tool returned


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
              on_event: Callable[[dict], None] | None = None, system: str = SYSTEM_PROMPT,
              specs: list[dict] | None = None) -> AgentRun:
    run = AgentRun(task=task)

    def emit(ev: dict) -> None:
        ev["t"] = time.time()
        run.events.append(ev)
        if on_event:
            on_event(ev)

    tools = [{"type": "function", "function": t} for t in (specs or TOOL_SPECS)]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    emit({"type": "start", "task": task})
    errors_in_a_row, nudged = 0, False
    for step in range(1, max_steps + 1):
        run.steps = step
        if not nudged and (step == max_steps or errors_in_a_row >= 3):
            # a loop guard: repeated failures or the last step force a plain-language answer instead of more calls
            messages.append({"role": "user", "content": "Stop calling tools now. Give your final answer in plain "
                             "language: what you found, what you tried, and what you could not do."})
            nudged = True
        try:
            res = llm.chat(messages, tools, tool_choice="none" if nudged else "auto")
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
            run.faithful = check_faithful(text, [(a, r) for _, a, r in run.results])
            emit({"type": "final", "text": text, "cached": res.cached, "faithful": run.faithful})
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
            errors_in_a_row = errors_in_a_row + 1 if "error" in result else 0
            emit({"type": "tool_result", "step": step, "tool": name, "ms": ms, "ok": "error" not in result,
                  "summary": summarize(result), "result": result})
            model_view = {k: v for k, v in result.items() if k not in ("finding_id", "tool_call_id", "already_computed", "already_open", "data_version")}
            model_view.pop("time_ordered", None)  # card-only, like traced_in below
            model_view.pop("source_recheck", None)
            for side in ("chokepoint_plan", "rule_of_thumb_plan"):  # card-only detail; keeps recorded replays valid
                if side in model_view:
                    model_view[side] = {k: v for k, v in model_view[side].items() if k != "traced_in_usd_cents"}
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(model_view, sort_keys=True)[:12000]})
    run.stopped = "max_steps"
    last = next((r["error"] for _, _, r in reversed(run.results) if "error" in r), None)
    run.final_text = ("I could not finish this investigation" + (f": {last['code']} - {last['message']}." if last else "."))
    emit({"type": "final", "text": run.final_text, "cached": False})
    emit({"type": "error", "message": f"stopped after {max_steps} steps without a model answer"})
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
        elif name == "audit_checks":
            card["audit_checks"] = {"checks": r["checks"], "blocking_failed": r["blocking_failed"]}
        elif name == "submit_audit":
            card["audit"] = {k: r[k] for k in ("plan_id", "verdict", "note", "blocking_failed", "passed", "finding_id")}
        elif name == "verify_plan":
            card["verification"] = {k: r[k] for k in ("plan_id", "verified", "statement", "method",
                                                      "perimeter_reachable_before", "perimeter_reachable_after")}
    return card
