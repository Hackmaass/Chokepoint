"""Label-free measurements on all laundering cases (no labels used in planning or scoring).

1. Accounts needed to guarantee that no route to the perimeter is left: Chokepoint's minimum cut versus freezing in the
   order of three simple rules (most recent recipients, highest degree, highest throughput) until the routes are cut.
2. Latency from alert to verified plan (tools only: open case, flow, freeze plan, verify plan).

  uv run python scripts/eval_budget_latency.py --split all --frac 0.5
"""

from __future__ import annotations

import argparse
import statistics as st
import time
from collections import defaultdict

import networkx as nx

from chokepoint.cases import make_cases
from chokepoint.data_ibm import load_transactions
from chokepoint.db import connect
from chokepoint.toolbox import Toolbox, fmt_ts

ap = argparse.ArgumentParser()
ap.add_argument("--split", default="all")
ap.add_argument("--frac", type=float, default=0.5)
ap.add_argument("--max-accounts", type=int, default=25)
args = ap.parse_args()

tb = Toolbox(connect())
df = load_transactions()
cases = make_cases(df, args.frac, args.split)

needed = defaultdict(list)  # method -> accounts needed
lat = []
rows = 0
for case in cases:
    t0 = time.perf_counter()
    c = tb.call("open_case", {"account": case.origin, "alert_ts": fmt_ts(case.alert_ts)})
    if "error" in c or not c["has_route_to_perimeter"]:
        continue
    tb.call("flow", {"case_id": c["case_id"]})
    p = tb.call("freeze_plan", {"case_id": c["case_id"]})
    if "error" in p or not p["actionable"]:
        continue
    v = tb.call("verify_plan", {"plan_id": p["plan_id"]})
    lat.append(time.perf_counter() - t0)
    if not v.get("verified"):
        continue
    # rebuild the plain region graph to count the accounts each rule needs
    case_row = tb._case(c["case_id"])
    region = tb._region(case_row)
    perimeter, trans, freezable = tb._network_parts(region)
    k_star = p["chokepoint_plan"]["count"]
    deg, thr, last = defaultdict(int), defaultdict(int), {}
    for (s, d), (n, ts, _usd) in trans.items():
        deg[s] += 1
        deg[d] += 1
        thr[s] += n
        thr[d] += n
        if s == region.origin:
            last[d] = ts
    fz = set(freezable)
    orders = {
        "most recent recipients": sorted((a for a in last if a in fz), key=lambda a: (-last[a], a))
        + sorted((a for a in freezable if a not in last), key=lambda a: (-deg[a], a)),
        "highest degree": sorted(freezable, key=lambda a: (-deg[a], a)),
        "highest throughput": sorted(freezable, key=lambda a: (-thr[a], a)),
    }
    g = nx.DiGraph(list(trans))
    for name, order in orders.items():
        removed, k = set(), 0
        for a in order:
            h = g.copy()
            h.remove_nodes_from(removed)
            if not any(x in h and region.origin in h and nx.has_path(h, region.origin, x) for x in perimeter):
                break
            removed.add(a)
            k += 1
        else:
            k = len(order)
        needed[name].append((k, k_star))
    needed["Chokepoint (minimum cut)"].append((k_star, k_star))
    rows += 1

print(f"cases with an actionable verified plan: {rows}")
print("\nAccounts frozen to guarantee no route to the perimeter is left (median, mean, share needing more than the minimum):")
for name, vals in needed.items():
    ks = [k for k, _ in vals]
    more = sum(1 for k, s in vals if k > s) / len(vals)
    ratios = [k / s for k, s in vals if s]
    print(f"  {name:26s} median {st.median(ks):5.1f}  mean {st.mean(ks):6.1f}  needs more than the minimum in {more:4.0%}  "
          f"median ratio {st.median(ratios):.2f}x")
lat.sort()
q = lambda p: lat[min(len(lat) - 1, int(p * len(lat)))]  # noqa: E731
print(f"\nLatency, alert to verified plan (tools only, {len(lat)} cases): median {q(0.5):.2f} s, p90 {q(0.9):.2f} s, max {lat[-1]:.2f} s")
