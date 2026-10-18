"""The Dispatcher: what happens after a person approves a freeze plan.

It runs only inside ``Toolbox.order_freeze`` (the HTTP approval endpoint). It is not a tool, not in MCP and not in the
agent's schema, so the model cannot reach it. It places holds on the frozen accounts in the graph, files one freeze
request as a GitHub issue in a public ops repo, and records the dispatch in the graph. Everything is synthetic: the issue
says so and no bank is called. Without a token, or if GitHub is unreachable, the dispatch stays ``queued`` and says so.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time

import httpx

from .chain import canon, chain_hash

log = logging.getLogger("chokepoint.dispatch")

TIMEOUT_S = 8.0
transport: httpx.BaseTransport | None = None  # tests inject a mock transport here


def _cfg() -> tuple[str, str, str | None]:
    return (os.environ.get("OPS_GITHUB_API", "https://api.github.com").rstrip("/"),
            os.environ.get("OPS_GITHUB_REPO", "Hackmaass/chokepoint-ops"),
            os.environ.get("OPS_GITHUB_TOKEN") or None)


def plain(text: str) -> str:
    """Visitor-typed text goes into a public issue: one code span, so no @mention notifies anyone and no link or image renders."""
    return "`" + " ".join(text.replace("`", "'").split()) + "`"


def usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def issue_payload(plan_id: str, plan_hash: str, order: dict, case: dict, plan: dict, ver: dict) -> dict:
    """The issue as filed, built only from stored tool results and the order. Pure, so the hash of what was sent can be checked."""
    cp, rule = plan["chokepoint_plan"], plan["rule_of_thumb_plan"]
    from .queries import QUERIES  # late import: queries imports the toolbox, which imports this module

    short = plan_hash[:8]
    title = f"Freeze request: {cp['count']} accounts, case {order['case_id']}, plan {short}"
    cypher = "\n\n".join(f"// {q['name']}\n{q['cypher']}" for q in QUERIES if q["tool"] in ("freeze_plan", "verify_plan"))
    rc = ver.get("source_recheck") or {}
    graph = "\n".join(["```mermaid", "graph LR", f'  O(["alerted account {case["origin"][-8:]}"])',
                       *[f'  O --> F{i}["freeze {a[-8:]}"]' for i, a in enumerate(cp["accounts"])],
                       *[f"  F{i} -.-> P" for i in range(len(cp["accounts"]))],
                       f'  P(["perimeter, {ver["perimeter_accounts"]} accounts, unreachable after the freeze"])', "```"])
    body = f"""Filed by Chokepoint's Dispatcher after a person approved a verified plan.

**Accounts to freeze ({cp['count']}):** {', '.join(f'`{a}`' for a in cp['accounts'])}
**Cut cost:** {cp['cost']} · **Money that could still move before the freeze:** {usd(plan['money_movable_before_usd_cents'])} · **after:** {usd(cp['residual_movable_usd_cents'])}
**Same-size rule of thumb** ({rule['rule']}): {usd(rule['residual_movable_usd_cents'])} still movable, routes left: {bool(rule['routes_left'])}

{graph}
*Schematic, not the full graph: the frozen accounts sit between the alerted account and the perimeter.*

**Checks**
- Check on a copy of the graph (`GRAPH.COPY` + `shortestPath`): perimeter reachable {ver['perimeter_reachable_before']} before, {ver['perimeter_reachable_after']} after.
- Second check on the source graph: perimeter reachable {rc.get('perimeter_reachable_before', 'n/a')} before, {rc.get('perimeter_reachable_after', 'n/a')} after.

<details><summary>The exact Cypher behind the plan and its check</summary>

```cypher
{cypher}
```
</details>

**Approved by:** {plain(order['approver'])}{(' · ' + plain(order['reason'])) if order['reason'] else ''}
**Receipt:** case `{order['case_id']}` · plan `{plan_id}` · `plan_hash` `{plan_hash}` · data version {order['data_version']} · order `{order['order_id']}` · receipt chain hash `{order.get('chain_hash') or 'n/a'}`

*Synthetic data. Filed by Chokepoint's Dispatcher after approval by {plain(order['approver'])}. Not a real bank instruction.*
"""
    return {"title": title, "body": body, "labels": [f"plan-{short}"]}


def place_holds(tb, order_id: str, accounts: list[str]) -> int:
    """Mark the frozen accounts as held by this order (an account already held by another order keeps that hold)."""
    tb._write("MATCH (o:FreezeOrder {id: $o})-[:FOR_ACCOUNT]->(a:Account) WHERE a.hold IS NULL "
              "SET a.hold = $o, a.hold_since = $now", {"o": order_id, "now": int(time.time())})
    return len(accounts)


def lift_holds(tb, order_id: str) -> None:
    tb._write("MATCH (a:Account {hold: $o}) REMOVE a.hold, a.hold_since", {"o": order_id})
    # accounts another live order of the same plan still covers stay held
    tb._write("MATCH (o:FreezeOrder {status: 'recorded'})-[:FOR_ACCOUNT]->(a:Account) WHERE a.hold IS NULL "
              "SET a.hold = o.id, a.hold_since = o.created_at", {})


def _github(method: str, path: str, **kw) -> httpx.Response:
    api, _, token = _cfg()
    h = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28",
         **kw.pop("headers", {})}
    with httpx.Client(timeout=TIMEOUT_S, transport=transport) as c:
        return c.request(method, f"{api}{path}", headers=h, **kw)


def _find_issue(repo: str, short: str) -> dict | None:
    # shortcut: first 100 issues only (the list endpoint, not search, which lags); page it if the repo outgrows that
    r = _github("GET", f"/repos/{repo}/issues", params={"state": "all", "per_page": 100})
    r.raise_for_status()
    return next((i for i in r.json() if "pull_request" not in i and f"plan {short}" in i["title"]), None)


def _file(payload: dict, short: str) -> tuple[str, str]:
    """Return (issue_url, payload_hash), reusing an issue already filed for this plan hash."""
    _, repo, _ = _cfg()
    sent = json.dumps(payload, sort_keys=True).encode()
    found = _find_issue(repo, short)
    if found:
        return found["html_url"], ""  # filed earlier: this process sent nothing, so there is no payload hash to record
    r = _github("POST", f"/repos/{repo}/issues", content=sent, headers={"Content-Type": "application/json"})
    if r.status_code == 422 and "labels" in r.text:  # a token without label rights: file it unlabelled; the title still carries the hash
        sent = json.dumps({k: v for k, v in payload.items() if k != "labels"}, sort_keys=True).encode()
        r = _github("POST", f"/repos/{repo}/issues", content=sent, headers={"Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()["html_url"], hashlib.sha256(sent).hexdigest()


def dispatch(tb, order_id: str, plan_id: str, plan_hash: str, order: dict, case: dict, plan: dict, ver: dict) -> dict:
    """Place holds, file the freeze request (once per plan hash) and record the Dispatch node. Never raises on a GitHub
    problem: the dispatch is then ``queued`` and the order stands."""
    holds = place_holds(tb, order_id, order["accounts"])
    dp_id = "dp-" + plan_hash[:12]
    row = tb.g.ro_query("MATCH (d:Dispatch {id: $d}) RETURN d.status, d.url, d.payload_hash", params={"d": dp_id}).result_set
    if row and row[0][0] == "sent":
        status, url, phash = row[0]
    else:
        payload = issue_payload(plan_id, plan_hash, order, case, plan, ver)
        _, _, token = _cfg()
        status, url, phash = "queued", None, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        note = "no OPS_GITHUB_TOKEN configured" if not token else ""
        if token:
            try:
                url, phash = _file(payload, plan_hash[:8])
                status = "sent"
            except (httpx.HTTPError, KeyError, ValueError) as e:
                note = f"GitHub unreachable or refused: {type(e).__name__}"
                log.warning("dispatch for plan %s queued: %s", plan_id, note)
        tb._write("MERGE (d:Dispatch {id: $d}) SET d.channel = 'github-issue', d.plan_hash = $h, d.status = $s, d.url = $u, "
                  "d.payload_hash = $ph, d.note = $n, d.sent_at = $now, d.hash = coalesce(d.hash, $dh), "
                  "d.parent_hash = coalesce(d.parent_hash, $dph)",
                  {"dh": chain_hash(canon("d", dp_id, plan_hash, "github-issue"), order.get("chain_hash", "")),
                   "dph": order.get("chain_hash", ""), "d": dp_id, "h": plan_hash, "s": status, "u": url or "", "ph": phash, "n": note, "now": int(time.time())})
    tb._write("MATCH (o:FreezeOrder {id: $o}), (d:Dispatch {id: $d}) MERGE (o)-[:DISPATCHED_AS]->(d)", {"o": order_id, "d": dp_id})
    note = tb.g.ro_query("MATCH (d:Dispatch {id: $d}) RETURN d.note", params={"d": dp_id}).result_set[0][0] or ""
    return {"dispatch_id": dp_id, "channel": "github-issue", "status": status, "url": url or None, "payload_hash": phash,
            "holds_placed": holds, "note": note}


def comment_withdrawn(url: str, who: str, reason: str) -> None:
    """Best effort: tell the ops repo the request was withdrawn, so a filed issue is never left looking live."""
    _, repo, token = _cfg()
    if not (token and url):
        return
    try:
        n = url.rstrip("/").rsplit("/", 1)[1]
        _github("POST", f"/repos/{repo}/issues/{n}/comments",
                json={"body": f"**Withdrawn** by {plain(who)}. {plain(reason) if reason else ''}\n\n*Synthetic data; no bank was called.*"}).raise_for_status()
    except (httpx.HTTPError, IndexError) as e:
        log.warning("could not comment on the withdrawn request: %s", type(e).__name__)
