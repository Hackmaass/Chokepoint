"""Check the numbers the landing page and docs/for-judges.md state, in one command. No model, no key.

  uv run python scripts/judge_check.py                  # about a minute
  uv run python scripts/judge_check.py --networks 120   # the full certificate batch
  docker exec chokepoint-api python scripts/judge_check.py

Lines marked MEASURED are computed now against the running database. Lines marked RECORDED are results from the
held-out evaluation, which needs the full dataset and the original protocol; they are printed with the command that
reproduces them and are never marked PASS. The script exits non-zero if any MEASURED line FAILS.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import httpx

from chokepoint import dispatch as _dispatch
from chokepoint.algo.cut import build_split_network, certify, run_falkordb_maxflow, solve_networkx
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--claims", action="store_true", help="also re-derive the measured claims in claims.json and fail on any mismatch")
ap.add_argument("--networks", type=int, default=40, help="random node-split networks for the certificate check")
args = ap.parse_args()

failures = 0


def line(kind: str, label: str, detail: str = "") -> None:
    global failures
    failures += kind == "FAIL"
    print(f"[{kind:<4}] {label}" + (f"\n         {detail}" if detail else ""))


db = connect(socket_timeout=120, socket_connect_timeout=5)
try:
    if "tenant_ibm" not in db.list_graphs():
        sys.exit("The transfer graph is not loaded yet. Start the stack and wait until /api/ready says ready.")
except Exception as exc:
    sys.exit(f"Cannot reach FalkorDB ({type(exc).__name__}). Is the container running? See docs/deploy.md.")

examples = json.loads((Path(__file__).resolve().parent.parent / "examples" / "examples.json").read_text(encoding="utf-8"))
tb = Toolbox(db, tenant="ibm", salt="judgecheck")
print(f"Chokepoint claims check: {len(examples)} example alerts, {args.networks} random networks\n")

# 1. every example: a plan, checked on a copy, nothing left to move
rows = []
case_ids = []
for ex in examples:
    c = tb.call("open_case", {"account": ex["account"], "alert_ts": ex["alert_ts"]})
    case_ids.append(c["case_id"])
    p = tb.call("freeze_plan", {"case_id": c["case_id"]})
    v = tb.call("verify_plan", {"plan_id": p["plan_id"]})
    rows.append((ex["account"], p, v))
bad = [a for a, p, v in rows if not (v["verified"] and v["perimeter_reachable_after"] == 0 and p["chokepoint_plan"]["residual_movable_usd_cents"] == 0)]
line("FAIL" if bad else "PASS", "MEASURED  every example plan is verified on a copy of the graph with $0 left to move",
     f"failing: {', '.join(bad)}" if bad else f"{len(rows)} of {len(rows)} plans: no route left, residual 0")

# 2. the figure on the landing page
default = next(r for r in rows if r[0] == "021174:800737690")
before = default[1]["money_movable_before_usd_cents"]
ok = round(before / 100) == 22410 and default[1]["chokepoint_plan"]["count"] == 3
line("PASS" if ok else "FAIL", "MEASURED  the landing page alert 021174:800737690: $22,410 could move, 3 accounts to freeze",
     f"movable ${before / 100:,.2f}, accounts {default[1]['chokepoint_plan']['count']}")

# 2b. the second, independent check on the source graph (not the copy) agrees on every example
bad = [a for a, _, v in rows if not (v["source_recheck"]["perimeter_reachable_after"] == 0 and v["source_recheck"]["perimeter_reachable_before"] > 0)]
line("FAIL" if bad else "PASS", "MEASURED  a second check that re-expands the source graph (not the copy) agrees: nothing reaches the perimeter after the freeze, something did before",
     f"disagree: {', '.join(bad)}" if bad else f"{len(rows)} of {len(rows)} examples agree")

# 2c. gentler-on-everyday-activity options (the same tool at a higher weight on everyday activity)
differ = []
for (acct, p, _), cid in zip(rows, case_ids):
    alt = tb.call("freeze_plan", {"case_id": cid, "lambda_weight": 8})
    if alt["chokepoint_plan"]["accounts"] != p["chokepoint_plan"]["accounts"]:
        differ.append((acct, p["chokepoint_plan"]["count"], p["chokepoint_plan"]["baseline_activity_transfers"],
                       alt["chokepoint_plan"]["count"], alt["chokepoint_plan"]["baseline_activity_transfers"]))
line("PASS" if len(differ) == 2 else "FAIL", "MEASURED  on 2 of the 8 examples a gentler plan exists (more accounts, less everyday activity); the other 6 pick the same accounts at any weight",
     "; ".join(f"{a}: {c1} accounts / {e1} everyday transfers -> {c2} / {e2}" for a, c1, e1, c2, e2 in differ))

# 3. timing check across the examples
no_route = smaller = same = 0
for _, p, _ in rows:
    t = p["time_ordered"]
    if not t["has_time_ordered_route"]:
        no_route += 1
    elif t["count"] < p["chokepoint_plan"]["count"]:
        smaller += 1
    else:
        same += 1
line("PASS" if (no_route, smaller, same) == (3, 2, 3) else "FAIL",
     "MEASURED  timing check on the 8 examples: 3 have no time-ordered route, 2 need fewer accounts, 3 unchanged",
     f"measured: {no_route} no route, {smaller} fewer accounts, {same} unchanged")
bad = [a for a, p, _ in rows if p["time_ordered"]["routes_left"]]
line("FAIL" if bad else "PASS", "MEASURED  no time-ordered route is left after the timing-check plan", ", ".join(bad) if bad else "")

# 4. the FalkorDB repro from docs/falkordb-bug-report-draft.md
g = db.select_graph("judge_check_repro")
try:
    g.query("""CREATE (a:N {id:'src:out'}), (b:N {id:'a15:in'}), (c:N {id:'a15:out'}), (d:N {id:'a20:in'}),
       (e:N {id:'a20:out'}), (f:N {id:'a3:in'}), (g:N {id:'a3:out'}), (h:N {id:'a12:in'}),
       (i:N {id:'a12:out'}), (t:N {id:'x1:in'}),
       (a)-[:E {cap:3322}]->(b), (b)-[:E {cap:158}]->(c), (c)-[:E {cap:3322}]->(d),
       (a)-[:E {cap:3322}]->(d), (d)-[:E {cap:300}]->(e), (e)-[:E {cap:3322}]->(t),
       (c)-[:E {cap:3322}]->(f), (f)-[:E {cap:153}]->(g), (g)-[:E {cap:3322}]->(h),
       (h)-[:E {cap:261}]->(i), (i)-[:E {cap:3322}]->(t)""")
    got = g.query("MATCH (s:N {id:'src:out'}), (t:N {id:'x1:in'}) CALL algo.maxFlow({sourceNodes:[s], targetNodes:[t], "
                  "relationshipTypes:['E'], capacityProperty:'cap'}) YIELD maxFlow RETURN maxFlow").result_set[0][0]
finally:
    g.delete()
measured_claims = {"repro_first_pass": str(int(got))}
if int(got) == 453:
    line("INFO", "MEASURED  the 11-edge algo.maxFlow repro returns 453, the correct value, on this FalkorDB version",
         "the wrong answer (300) was seen on v4.22.0 and 6.0.2; the certificate stays in place either way")
else:
    line("PASS" if int(got) == 300 else "FAIL", "MEASURED  the 11-edge algo.maxFlow repro returns 300 where the exact answer is 453",
         f"FalkorDB returned {int(got)}")

# 5. the certificate on random node-split networks
def random_net(seed: int):
    rng = random.Random(seed)
    names = [f"a{i}" for i in range(rng.randint(4, 22))]
    exits = [f"x{i}" for i in range(rng.randint(1, 3))]
    everyone = ["src", *names, *exits]
    p = rng.choice([0.08, 0.12, 0.2, 0.3])
    transfers = [(u, v) for u in everyone for v in everyone if u != v and rng.random() < p]
    transfers += transfers[: rng.randint(0, 5)]
    return build_split_network({a: rng.randint(1, 300) for a in names}, transfers, "src", exits)


ran = accepted = rejected = nothing = false_accepts = raw_wrong = 0
t0 = time.time()
for seed in range(args.networks):
    net = random_net(seed)
    exact = solve_networkx(net)
    got = run_falkordb_maxflow(db, net, f"judge_check_{seed}")
    ran += 1
    if got is None:
        nothing += 1
        continue
    raw_wrong += got.value != exact.value
    if certify(net, got):
        accepted += 1
        false_accepts += got.value != exact.value
    else:
        rejected += 1
line("FAIL" if false_accepts else "PASS", "MEASURED  the certificate never accepts a wrong FalkorDB max-flow answer (0 false accepts)",
     f"{ran} random networks in {time.time() - t0:.0f}s: {accepted} accepted, {rejected} rejected, {nothing} with no answer, {false_accepts} false accepts")
line("INFO", f"MEASURED  FalkorDB's raw answer differed from the exact value on {raw_wrong} of {ran} networks ({100 * raw_wrong / max(ran, 1):.0f}%)",
     "docs say 12 to 17% across two batches (14 of 120 here, 24 of about 140 earlier); small batches wander")

# 5b. the same test on the real example networks (lambda 0: every freezable account costs 100)
acc = rej = none = wrong_raw = 0
for cid in case_ids:
    region = tb._region(tb._case(cid))
    perimeter, trans, freezable = tb._network_parts(region)
    net = build_split_network({a: 100 for a in freezable}, list(trans), region.origin, sorted(perimeter))
    exact = solve_networkx(net)
    got = run_falkordb_maxflow(db, net, f"judge_check_ex_{cid[-6:]}")
    if got is None:
        none += 1
        continue
    wrong_raw += got.value != exact.value
    ok = certify(net, got)
    acc += ok
    rej += not ok
line("INFO", f"MEASURED  on the {len(case_ids)} real example networks FalkorDB's max-flow was accepted {acc}, rejected {rej}, gave no answer {none}; its raw value was wrong {wrong_raw} times",
     "real networks, not random ones: shows how often the exact fallback is needed on these cases; no claim beyond these 8")

solvers: dict[str, int] = {}
for _, p, _ in rows:
    k = p["chokepoint_plan"]["solver"]
    solvers[k] = solvers.get(k, 0) + 1
line("INFO", "MEASURED  solver recorded in the 8 example plans: " + ", ".join(f"{k} x{n}" for k, n in sorted(solvers.items())),
     "every plan says falkordb when FalkorDB's first pass was certified, falkordb+repair when it was repaired in the database")

# 5b2. the in-database repair: a new batch of random node-split networks (seeds 1000+, labelled new; not the held-out set)
from chokepoint.algo.cut import reaches_exit, solve  # noqa: E402

labels: dict[str, int] = {}
mismatch = 0
t0 = time.time()
for seed in range(1000, 1000 + args.networks):
    net = random_net(seed)
    if not reaches_exit(net):
        continue
    got = solve(db, net, f"judge_check_fix_{seed}")
    labels[got.solver] = labels.get(got.solver, 0) + 1
    mismatch += got.value != solve_networkx(net).value or not certify(net, got)
n_solved = sum(labels.values())
line("PASS" if not mismatch and not labels.get("networkx-emergency") else "FAIL",
     f"MEASURED  every max-flow is certified inside FalkorDB: {n_solved} new random networks, 0 wrong, 0 networkx-emergency",
     "solver labels: " + ", ".join(f"{k} x{n}" for k, n in sorted(labels.items())) + f" ({time.time() - t0:.0f}s); NetworkX is only the oracle here")

# 5c. an approval cannot outlive its evidence, and the model cannot give one
first_plan = rows[0][1]["plan_id"]
refused_unverified = None
try:
    tb.order_freeze(rows[0][1]["plan_id"], "judge check", "")  # verified above: this one succeeds
    ok_order = True
except Exception as exc:  # noqa: BLE001
    ok_order = False
    print("         unexpected:", exc)
ex2 = examples[1]
c2 = tb.call("open_case", {"account": ex2["account"], "alert_ts": ex2["alert_ts"], "window_days": 6})  # a plan that is not checked
case_ids.append(c2["case_id"])
p2 = tb.call("freeze_plan", {"case_id": c2["case_id"]})
try:
    tb.order_freeze(p2["plan_id"], "judge check", "")
except Exception as exc:  # noqa: BLE001
    refused_unverified = getattr(exc, "code", type(exc).__name__)
tool_reply = tb.call("order_freeze", {"plan_id": first_plan, "approver": "judge check", "reason": ""})
line("PASS" if ok_order and refused_unverified == "NOT_VERIFIED" and "error" in tool_reply else "FAIL",
     "MEASURED  a verified plan can be approved by a person; an unchecked plan is refused; the tool route does not know the approval",
     f"verified plan approved: {ok_order}; unchecked plan: {refused_unverified}; as a tool: {tool_reply.get('error', {}).get('code')}")

# 5d. the Dispatcher files one freeze request per approved plan (GitHub mocked here: this run never contacts it)
_posted: list[dict] = []


def _fake_github(req):
    if req.method == "GET":
        return httpx.Response(200, json=[{"title": i["title"], "html_url": f"https://example.invalid/issues/{n}"} for n, i in enumerate(_posted, 1)])
    _posted.append(json.loads(req.content))
    return httpx.Response(201, json={"html_url": f"https://example.invalid/issues/{len(_posted)}"})


_dispatch.transport = httpx.MockTransport(_fake_github)
os.environ["OPS_GITHUB_TOKEN"] = "judge-check-mock"
os.environ["OPS_GITHUB_API"] = "https://mock.invalid"
d_orders = [tb.order_freeze(rows[0][1]["plan_id"], f"judge check {i}", "") for i in range(2)]
sent = {o["dispatch"]["url"] for o in d_orders if o["dispatch"]["status"] == "sent"}
line("PASS" if len(_posted) == 1 and len(sent) == 1 else "FAIL",
     f"MEASURED  Dispatch: {len(d_orders)} orders, {len(sent)} request filed, {len(_posted) - len({i["title"] for i in _posted})} duplicates (GitHub mocked)",
     "the same plan approved twice files one freeze request")

# 6. recorded, not re-run
for label, detail in [
    ("RECORDED blocked share vs the rule of thumb: difference -0.002, 95% interval [-0.09, +0.08], held-out half",
     "docs/pilot-results.md lists the commands; they need the full 475 MB dataset and the original protocol"),
    ("RECORDED precision of the frozen accounts: +0.19, interval [+0.07, +0.32]",
     "docs/pilot-results.md, same commands"),
]:
    line("INFO", label, detail)

if args.claims:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import check_claims
    from falkordb_maxflow_repro import true_max_flow

    measured_claims["repro_true"] = str(true_max_flow())
    for key, c in check_claims.load().items():
        if c["kind"] == "measured":
            line("PASS" if measured_claims.get(key) == c["value"] else "FAIL",
                 f"CLAIM     {key} = {c['value']} (re-derived: {measured_claims.get(key)})", c.get("what", ""))
    for msg in check_claims.problems():
        line("FAIL", "CLAIM     " + msg)

for cid in case_ids:
    tb.delete_case(cid)
print(f"\n{'ALL MEASURED CLAIMS PASS' if not failures else str(failures) + ' MEASURED CLAIM(S) FAILED'}")
sys.exit(1 if failures else 0)
