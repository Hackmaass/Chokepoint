"""Evaluate the agent itself (not the plans): does it follow the workflow, finish, verify, and report only
numbers and ids that came from tool results?

  uv run python scripts/eval_agent.py --split test --frac 0.5 --limit 40

Checks per run
- completed: a final answer and no loop failure
- workflow: tools called in a valid order (open_case first; flow/freeze_plan/verify_plan only after a case exists)
- verified_before_recommending: if the final answer recommends accounts, verify_plan was called and returned verified
- faithful: every dollar amount and every account id in the final text appears in a tool result
- widening: on cases with no route, the agent widens at most twice and does not recommend freezing anything
"""

from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from chokepoint.agent import run_agent
from chokepoint.cases import make_cases
from chokepoint.data_ibm import load_transactions
from chokepoint.db import connect
from chokepoint.llm import LLMClient
from chokepoint.toolbox import Toolbox, fmt_ts

ap = argparse.ArgumentParser()
ap.add_argument("--split", default="test")
ap.add_argument("--frac", type=float, default=0.5)
ap.add_argument("--limit", type=int, default=40)
ap.add_argument("--workers", type=int, default=3)
ap.add_argument("--out", default="scratch/agent_eval.json")
args = ap.parse_args()

AMOUNT = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
ACCOUNT = re.compile(r"\b[0-9]{2,7}:[0-9A-F]{9}\b")


def numbers_in(obj, out: set):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "usd_cents" in k and isinstance(v, (int, float)):
                out.add(round(v / 100, 2))
                out.add(float(round(v / 100)))
            numbers_in(v, out)
    elif isinstance(obj, list):
        for v in obj:
            numbers_in(v, out)


def accounts_in(obj, out: set):
    if isinstance(obj, str):
        out.update(ACCOUNT.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            accounts_in(v, out)
    elif isinstance(obj, list):
        for v in obj:
            accounts_in(v, out)


def evaluate(case, tb, llm) -> dict:
    task = f"Account {case.origin} was alerted at {fmt_ts(case.alert_ts)}. Investigate and recommend which accounts to freeze."
    run = run_agent(task, tb, llm)
    tools = [t for t, _, _ in run.results]
    amounts, accts = set(), {case.origin}
    for _, a, r in run.results:
        numbers_in(r, amounts)
        accounts_in(r, accts)
        accounts_in(a, accts)
    text = run.final_text or ""
    bad_amounts = []
    for m in AMOUNT.findall(text):
        v = float(m.replace(",", ""))
        if not any(abs(v - a) < 0.011 for a in amounts) and v != 0:
            bad_amounts.append(m)
    bad_accounts = [a for a in set(ACCOUNT.findall(text)) if a not in accts]
    results = {t: r for t, _, r in run.results}
    opened = [r for t, _, r in run.results if t == "open_case" and "error" not in r]
    has_route = any(r.get("has_route_to_perimeter") for r in opened)
    verified = any(t == "verify_plan" and r.get("verified") for t, _, r in run.results)
    workflow_ok = bool(tools) and tools[0] == "open_case" and all(
        (t == "open_case") or any(u == "open_case" for u in tools[:i]) for i, t in enumerate(tools))
    recommends = bool(re.search(r"\bfreeze\b", text, re.I)) and bool(ACCOUNT.findall(text))
    widenings = sum(1 for t, a, _ in run.results if t == "open_case" and (a.get("window_days", 7) > 7 or a.get("perimeter_hops", 3) > 3))
    return {
        "origin": case.origin, "alert_ts": fmt_ts(case.alert_ts), "steps": run.steps, "tools": tools,
        "has_route": has_route, "completed": bool(text) and not run.stopped, "stopped": run.stopped,
        "workflow_ok": workflow_ok, "verified": verified,
        "recommends_without_verification": recommends and not verified and has_route,
        "recommends_when_no_route": recommends and not has_route,
        "faithful": not bad_amounts and not bad_accounts, "bad_amounts": bad_amounts, "bad_accounts": bad_accounts,
        "widenings": widenings, "text": text,
    }


df = load_transactions()
cases = make_cases(df, args.frac, args.split)[: args.limit]
tb = Toolbox(connect())
llm = LLMClient(mode="live")
with ThreadPoolExecutor(args.workers) as ex:
    rows = list(ex.map(lambda c: evaluate(c, tb, llm), cases))
Path(args.out).write_text(json.dumps(rows, indent=1), encoding="utf-8")

n = len(rows)
route = [r for r in rows if r["has_route"]]
noroute = [r for r in rows if not r["has_route"]]
pct = lambda k, xs: f"{sum(1 for r in xs if r[k])}/{len(xs)}"  # noqa: E731
print(f"cases {n}: with a route {len(route)}, without {len(noroute)}")
print("completed            ", pct("completed", rows))
print("workflow order ok    ", pct("workflow_ok", rows))
print("verified (route)     ", pct("verified", route))
print("faithful numbers/ids ", pct("faithful", rows))
print("recommends unverified", sum(r["recommends_without_verification"] for r in rows))
print("recommends, no route ", sum(r["recommends_when_no_route"] for r in noroute), "of", len(noroute))
print("max widenings        ", max((r["widenings"] for r in rows), default=0))
for r in rows:
    if not r["faithful"] or not r["completed"]:
        print("  issue:", r["origin"], r["stopped"] or "", r["bad_amounts"], r["bad_accounts"])
