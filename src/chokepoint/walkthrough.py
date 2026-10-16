"""A terminal walkthrough of one alert: open case, flow, freeze plan, verification, comparison, receipt.

It calls the same tools as the web UI's "Tools only" mode and prints figures only from tool results. No model and no
API key are involved; it needs the FalkorDB container with the transfer graph loaded (see docs/deploy.md).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from .config import PROJECT_ROOT
from .toolbox import Toolbox

DEFAULT_ACCOUNT = "021174:800737690"


def usd(cents: int) -> str:
    return f"${cents / 100:,.0f}"


def default_alert() -> tuple[str, str]:
    """The example alert used in the demo script: its account and alert time from examples/examples.json."""
    examples = json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8"))
    pick = next((e for e in examples if e["account"] == DEFAULT_ACCOUNT), examples[0])
    return pick["account"], pick["alert_ts"]


def run_walkthrough(tb: Toolbox, account: str, alert_ts: str, say: Callable[[str], None] = print) -> dict:
    """Run the five steps and narrate them through ``say``. Returns the raw tool results keyed by step."""
    out: dict = {}

    def step(n: int, title: str, tool: str) -> None:
        say(f"\n[{n}/6] {title}   (tool: {tool})")

    def fail(res: dict) -> dict:
        say(f"      stopped: {res['error']['code']}: {res['error']['message']}")
        if res["error"].get("hint"):
            say(f"      hint: {res['error']['hint']}")
        return out

    say(f"Alert: account {account} at {alert_ts}")
    say("Every figure below is returned by a tool and stored in the graph; nothing is typed in by hand.")

    step(1, "Open the case", "open_case")
    case = out["open_case"] = tb.call("open_case", {"account": account, "alert_ts": alert_ts})
    if "error" in case:
        return fail(case)
    say(f"      case {case['case_id']}, data version {case['data_version']}")
    say(f"      {case['accounts']} accounts within {case['perimeter_hops']} hops, window {case['window_days']} days")
    if not case.get("has_route_to_perimeter", True):
        say(f"      {case.get('note', '')}")
        return out

    step(2, "How much money could still move", "flow")
    flow = out["flow"] = tb.call("flow", {"case_id": case["case_id"]})
    if "error" in flow:
        return fail(flow)
    say(f"      up to {usd(flow['max_movable_usd_cents'])} could reach the perimeter along recorded transfers")
    say(f"      solver: {flow['solver']}")

    step(3, "Build a freeze plan", "freeze_plan")
    plan = out["freeze_plan"] = tb.call("freeze_plan", {"case_id": case["case_id"]})
    if "error" in plan:
        return fail(plan)
    cp, rule = plan["chokepoint_plan"], plan["rule_of_thumb_plan"]
    say(f"      freeze {cp['count']} account(s): {', '.join(cp['accounts'])}")
    say(f"      money that could still move afterwards, in the recorded transfers: "
        f"{usd(cp['residual_movable_usd_cents'])} of {usd(plan['money_movable_before_usd_cents'])}")
    if not plan["actionable"]:
        say(f"      warning: {plan.get('warning', '')}")

    step(4, "Check the plan on a copy of the graph", "verify_plan")
    ver = out["verify_plan"] = tb.call("verify_plan", {"plan_id": plan["plan_id"]})
    if "error" in ver:
        return fail(ver)
    say(f"      perimeter accounts reachable: {ver['perimeter_reachable_before']} before, "
        f"{ver['perimeter_reachable_after']} after")
    say(f"      verified: {'yes' if ver['verified'] else 'NO'}. {ver['statement']}")
    say(f"      method: {ver['method']}")

    step(5, "Compare with the rule of thumb", "freeze_plan")
    say(f"      rule of thumb: {rule['rule']}")
    say(f"      {'':<22}{'plan':>14}{'rule of thumb':>16}")
    say(f"      {'accounts frozen':<22}{cp['count']:>14}{rule['count']:>16}")
    say(f"      {'money still movable':<22}{usd(cp['residual_movable_usd_cents']):>14}"
        f"{usd(rule['residual_movable_usd_cents']):>16}")
    say(f"      {'on a route to exit':<22}{cp['on_a_route_to_perimeter']:>14}{rule['on_a_route_to_perimeter']:>16}")
    say(f"      {'routes left':<22}{'yes' if cp['routes_left'] else 'no':>14}{'yes' if rule['routes_left'] else 'no':>16}")
    say("      Read this fairly: on the held-out evaluation the two block about the same share of escaped money;")
    say("      the plan is more precise and comes with the check above (docs/pilot-results.md). This example was")
    say("      picked because the rule leaves money movable; the typical case is a tie on blocked share.")

    step(6, "Receipt for the plan", "explain_finding")
    rec = out["explain_finding"] = tb.call("explain_finding", {"finding_id": plan["finding_id"]})
    if "error" in rec:
        return fail(rec)
    say(f"      finding {rec['finding_id']} was produced by tool '{rec['tool']}' with {json.dumps(rec['parameters'])}")
    say(f"      data version {rec['data_version']}, solver {rec['solver']}, {rec['accounts_read']} accounts read, "
        f"{rec['duration_ms']} ms")
    say("      Run it again: the same case id comes back and the stored findings are reused.")
    return out
