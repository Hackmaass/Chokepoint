"""G10: does the free NVIDIA Nemotron model call our tools correctly?

Sends 20 scripted prompts with a tool list, validates each returned tool call against the schema
and the expected tool, caches every response to llm_cache/ (git-ignored), and stays under the
configured requests-per-minute limit. The API key is read from the environment and never printed.

Usage:  uv run python scripts/g10_llm_probe.py [--temperature 1.0] [--top-p 0.95]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

from chokepoint.config import PROJECT_ROOT, get_settings

CACHE = PROJECT_ROOT / "llm_cache"

TOOLS = [
    {"name": "open_case", "description": "Open an investigation for an alerted account at an alert time.",
     "parameters": {"type": "object", "required": ["account", "alert_ts"], "additionalProperties": False,
                    "properties": {"account": {"type": "string"}, "alert_ts": {"type": "string"}}}},
    {"name": "flow", "description": "How much money could still move from the alerted account to the exits.",
     "parameters": {"type": "object", "required": ["case_id"], "additionalProperties": False,
                    "properties": {"case_id": {"type": "string"}}}},
    {"name": "freeze_plan", "description": "Cheapest set of accounts to freeze so no money reaches an exit.",
     "parameters": {"type": "object", "required": ["case_id", "lambda_weight"], "additionalProperties": False,
                    "properties": {"case_id": {"type": "string"},
                                   "lambda_weight": {"type": "number", "minimum": 0, "maximum": 4},
                                   "exit_rule": {"type": "string", "enum": ["cash_like", "other_bank", "dead_end"]}}}},
    {"name": "verify_plan", "description": "Check a stored freeze plan by removing its accounts and looking for routes.",
     "parameters": {"type": "object", "required": ["plan_id"], "additionalProperties": False,
                    "properties": {"plan_id": {"type": "string"}}}},
    {"name": "tradeoff_curve", "description": "Laundering stopped versus legitimate activity frozen across lambda values.",
     "parameters": {"type": "object", "required": ["case_id"], "additionalProperties": False,
                    "properties": {"case_id": {"type": "string"}}}},
    {"name": "resume_case", "description": "Reload a case and its stored findings from an earlier session.",
     "parameters": {"type": "object", "required": ["case_id"], "additionalProperties": False,
                    "properties": {"case_id": {"type": "string"}}}},
    {"name": "explain_finding", "description": "Show the receipt behind a finding: tool call, parameters, data version.",
     "parameters": {"type": "object", "required": ["finding_id"], "additionalProperties": False,
                    "properties": {"finding_id": {"type": "string"}}}},
]

# (prompt, expected tool, {argument: expected value})
PROMPTS = [
    ("Account 8001F2A10 was flagged at 2022-09-03T10:15:00. Open a case for it.", "open_case", {"account": "8001F2A10", "alert_ts": "2022-09-03T10:15:00"}),
    ("Start an investigation on account 100428660 alerted 2022-09-05 14:30.", "open_case", {"account": "100428660"}),
    ("We got an alert on 80021A8B0 at 2022-09-01T08:00:00, please open it.", "open_case", {"account": "80021A8B0"}),
    ("How much money can still move out of case c-17?", "flow", {"case_id": "c-17"}),
    ("For case c-9, what is the maximum amount that could still reach the exits?", "flow", {"case_id": "c-9"}),
    ("Compute the exposure for case c-204.", "flow", {"case_id": "c-204"}),
    ("Which accounts should we freeze in case c-17? Protect busy accounts moderately, lambda 1.", "freeze_plan", {"case_id": "c-17", "lambda_weight": 1}),
    ("Give me the cheapest freeze plan for case c-3 with lambda 0.", "freeze_plan", {"case_id": "c-3", "lambda_weight": 0}),
    ("Make a freeze plan for c-55 with lambda 2.5 using the cash_like exit rule.", "freeze_plan", {"case_id": "c-55", "lambda_weight": 2.5, "exit_rule": "cash_like"}),
    ("Freeze plan for case c-8, lambda 0.5, exits are accounts at another bank.", "freeze_plan", {"case_id": "c-8", "lambda_weight": 0.5, "exit_rule": "other_bank"}),
    ("Verify plan p-42.", "verify_plan", {"plan_id": "p-42"}),
    ("Please check that freeze plan p-7 really cuts every route.", "verify_plan", {"plan_id": "p-7"}),
    ("Show the trade-off between laundering stopped and collateral for case c-17.", "tradeoff_curve", {"case_id": "c-17"}),
    ("I want the lambda sweep curve for c-90.", "tradeoff_curve", {"case_id": "c-90"}),
    ("Pick up where we left off on case c-12.", "resume_case", {"case_id": "c-12"}),
    ("Reload case c-300 from yesterday.", "resume_case", {"case_id": "c-300"}),
    ("Resume c-5 and tell me what is stale.", "resume_case", {"case_id": "c-5"}),
    ("Why did finding f-88 say that? Show its receipt.", "explain_finding", {"finding_id": "f-88"}),
    ("Explain finding f-3: which run and data version produced it?", "explain_finding", {"finding_id": "f-3"}),
    ("What produced finding f-1204?", "explain_finding", {"finding_id": "f-1204"}),
]
assert len(PROMPTS) == 20

SYSTEM = ("You are Chokepoint's investigation agent. Use exactly one tool per request, with the "
          "arguments the user gave. Never invent ids.")


def check_args(schema: dict, args: dict) -> list[str]:
    errs = []
    for k in schema.get("required", []):
        if k not in args:
            errs.append(f"missing {k}")
    for k, v in args.items():
        spec = schema["properties"].get(k)
        if spec is None:
            errs.append(f"unexpected {k}")
            continue
        if spec["type"] == "string" and not isinstance(v, str):
            errs.append(f"{k} not string")
        if spec["type"] == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
            errs.append(f"{k} not number")
        if "enum" in spec and v not in spec["enum"]:
            errs.append(f"{k} not in enum")
        if "minimum" in spec and isinstance(v, (int, float)) and not (spec["minimum"] <= v <= spec["maximum"]):
            errs.append(f"{k} out of range")
    return errs


def call(client: httpx.Client, base: str, key: str, body: dict, min_gap: float, state: dict) -> dict:
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]
    path = CACHE / f"{digest}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    wait = state["last"] + min_gap - time.time()
    if wait > 0:
        time.sleep(wait)
    for attempt in range(5):
        state["last"] = time.time()
        r = client.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=120)
        if r.status_code == 429:
            time.sleep(min(60, 5 * 2**attempt))
            state["429s"] += 1
            continue
        r.raise_for_status()
        data = r.json()
        CACHE.mkdir(exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data
    raise RuntimeError("rate limited after retries")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--tool-choice", default="auto")
    a = ap.parse_args()
    s = get_settings()
    if not s.llm_api_key:
        print("no NVIDIA_API_KEY / LLM_API_KEY found; skipping G10")
        return 2
    tools = [{"type": "function", "function": t} for t in TOOLS]
    by_name = {t["name"]: t for t in TOOLS}
    min_gap = 60.0 / s.llm_rpm_limit
    state = {"last": 0.0, "429s": 0}
    ok = valid_args = right_tool = right_values = 0
    latencies = []
    with httpx.Client() as client:
        for i, (prompt, tool, expect) in enumerate(PROMPTS, 1):
            body = {"model": s.llm_model, "temperature": a.temperature, "top_p": a.top_p, "max_tokens": 1024,
                    "tools": tools, "tool_choice": a.tool_choice,
                    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
            t0 = time.time()
            try:
                data = call(client, s.llm_base_url, s.llm_api_key, body, min_gap, state)
            except Exception as e:  # noqa: BLE001
                print(f"{i:2d} ERROR {type(e).__name__}: {str(e)[:80]}")
                continue
            latencies.append(time.time() - t0)
            msg = data["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            if len(calls) != 1:
                print(f"{i:2d} FAIL expected 1 tool call, got {len(calls)} (text: {(msg.get('content') or '')[:60]!r})")
                continue
            fn = calls[0]["function"]
            try:
                args = json.loads(fn["arguments"])
            except Exception:  # noqa: BLE001
                print(f"{i:2d} FAIL bad JSON arguments")
                continue
            name_ok = fn["name"] == tool
            errs = check_args(by_name[fn["name"]]["parameters"], args) if fn["name"] in by_name else ["unknown tool"]
            vals_ok = all(args.get(k) == v for k, v in expect.items())
            right_tool += name_ok
            valid_args += not errs
            right_values += vals_ok
            ok += name_ok and not errs and vals_ok
            flag = "ok  " if name_ok and not errs and vals_ok else "FAIL"
            print(f"{i:2d} {flag} {fn['name']} {json.dumps(args)[:90]} {'; '.join(errs)}")
    n = len(PROMPTS)
    print(f"\nvalid tool calls: {ok}/{n}  (right tool {right_tool}, schema-valid {valid_args}, right values {right_values})")
    print(f"mean latency {sum(latencies)/max(1,len(latencies)):.1f}s, 429 responses {state['429s']}, "
          f"temperature {a.temperature}, top_p {a.top_p}, tool_choice {a.tool_choice}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
