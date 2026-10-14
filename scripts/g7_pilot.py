"""G7 / H3 pilot (offline, NetworkX only; labels are used for case selection and scoring only).

Case = (origin of a laundering pattern, alert time t at a fraction of that pattern's transfers).
Plan graph = all transfers in [t - W, t], forward from the origin up to K hops; accounts at hop K
are the exits (the perimeter beyond which money is considered out of reach). Costs are label-free.
Score = post-t laundering transfers of the pattern blocked, and post-t legitimate transfers hit.

Usage: python -I scripts/g7_pilot.py [--frac 0.5] [--min-transfers 5] [--limit 0]
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict

import networkx as nx
import polars as pl

from chokepoint.algo.cut import (
    build_split_network,
    extract_cut,
    reaches_exit,
    solve_networkx,
)
from chokepoint.data_ibm import load_transactions

ap = argparse.ArgumentParser()
ap.add_argument("--frac", type=float, default=0.5)
ap.add_argument("--min-transfers", type=int, default=5)
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--window-days", type=float, default=3.0)
ap.add_argument("--max-nodes", type=int, default=4000)
ap.add_argument("--ks", default="2,3,4")
ap.add_argument("--lambdas", default="0,1,2")
ap.add_argument("--out", default="scratch/g7_pilot_results.csv")
args = ap.parse_args()
KS = [int(x) for x in args.ks.split(",")]
LAMS = [float(x) for x in args.lambdas.split(",")]
W = int(args.window_days * 1440)
HORIZON = 3 * 1440

df = load_transactions()
with pl.StringCache():
    df = df.with_columns(pl.col("a_src").cast(pl.Categorical).to_physical().alias("s"),
                         pl.col("a_dst").cast(pl.Categorical).to_physical().alias("d"))
df = df.select("tid", "ts_min", "s", "d", "fmt", "laundering", "a_src", "a_dst")
laund = df.filter(pl.col("laundering") == 1).sort("ts_min", "tid")
legit = df.filter(pl.col("laundering") == 0)

# laundering components (account level)
g = nx.Graph()
g.add_edges_from(zip(laund["s"].to_list(), laund["d"].to_list()))
comp_of = {a: i for i, c in enumerate(nx.connected_components(g)) for a in c}
laund = laund.with_columns(pl.col("s").replace_strict(comp_of, return_dtype=pl.Int64).alias("comp"))
sizes = laund.group_by("comp").agg(pl.len().alias("n"))
comps = sizes.filter(pl.col("n") >= args.min_transfers)["comp"].to_list()
if args.limit:
    comps = comps[: args.limit]
print(f"components with >= {args.min_transfers} laundering transfers: {len(comps)}", flush=True)

results = []
skipped = defaultdict(int)
for ci, comp in enumerate(comps):
    sub = laund.filter(pl.col("comp") == comp).sort("ts_min", "tid")
    n = sub.height
    k_t = max(1, math.ceil(args.frac * n))
    t = int(sub["ts_min"][k_t - 1])
    # origin: an account that sends laundering money but never receives it; earliest sender wins
    receivers = set(sub["d"].to_list())
    origin = next((s for s in sub["s"].to_list() if s not in receivers), sub["s"][0])
    post = sub.filter((pl.col("ts_min") > t) | ((pl.col("ts_min") == t) & (pl.col("tid") > sub["tid"][k_t - 1])))
    if post.height == 0:
        skipped["no post-t laundering"] += 1
        continue
    laund_accts = set(sub["s"].to_list()) | set(sub["d"].to_list())

    win = df.filter((pl.col("ts_min") >= t - W) & (pl.col("ts_min") <= t))
    # forward BFS from the origin, K_max hops
    kmax = max(KS)
    depth = {origin: 0}
    frontier = [origin]
    edges_by_level = []
    over = False
    for hop in range(kmax):
        if not frontier:
            break
        e = (win.filter(pl.col("s").is_in(frontier))
                .group_by("s", "d").agg(pl.len().alias("n")))
        edges_by_level.append((hop, e))
        new = [d for d in e["d"].to_list() if d not in depth]
        for d in new:
            depth[d] = hop + 1
        frontier = list(dict.fromkeys(new))
        if len(depth) > args.max_nodes:
            over = True
            break
    if over:
        skipped["region too large"] += 1
        continue
    all_edges = pl.concat([e for _, e in edges_by_level]) if edges_by_level else pl.DataFrame()
    if all_edges.height == 0:
        skipped["origin has no pre-t outgoing transfers"] += 1
        continue

    region = set(depth)
    # post-t laundering touched accounts known in region (holdout ceiling)
    post_in_region = post.filter(pl.col("s").is_in(list(region)) | pl.col("d").is_in(list(region))).height
    # baseline activity (label-free): pre-t transfers touching the account, minus those inside the region
    reg_list = list(region)
    pre = df.filter(pl.col("ts_min") <= t)
    touch = (pre.filter(pl.col("s").is_in(reg_list)).group_by("s").agg(pl.len().alias("n")).rename({"s": "a"})
             .join(pre.filter(pl.col("d").is_in(reg_list)).group_by("d").agg(pl.len().alias("m")).rename({"d": "a"}),
                   on="a", how="full", coalesce=True).fill_null(0))
    total_touch = {a: int(x + y) for a, x, y in zip(touch["a"], touch["n"], touch["m"])}
    inside = defaultdict(int)
    for s_, d_, c in zip(all_edges["s"], all_edges["d"], all_edges["n"]):
        inside[s_] += int(c)
        inside[d_] += int(c)
    base = {a: max(0, total_touch.get(a, 0) - inside.get(a, 0)) for a in region}
    maxb = max(base.values()) or 1

    # post-t legitimate transfers within the horizon, for collateral
    post_legit = legit.filter((pl.col("ts_min") > t) & (pl.col("ts_min") <= t + HORIZON)
                              & (pl.col("s").is_in(reg_list) | pl.col("d").is_in(reg_list)))
    pl_pairs = list(zip(post_legit["s"].to_list(), post_legit["d"].to_list()))
    post_laund_pairs = list(zip(post["s"].to_list(), post["d"].to_list()))

    def score(frozen: set[int]):
        blocked = sum(1 for s_, d_ in post_laund_pairs if s_ in frozen or d_ in frozen)
        coll = sum(1 for s_, d_ in pl_pairs if s_ in frozen or d_ in frozen)
        prec = (sum(1 for a in frozen if a in laund_accts) / len(frozen)) if frozen else 0.0
        return blocked / len(post_laund_pairs), coll, prec

    for K in KS:
        sel = {a for a, dp in depth.items() if dp <= K}
        exits = [a for a, dp in depth.items() if dp == K]
        trans = [(s_, d_) for s_, d_ in zip(all_edges["s"], all_edges["d"]) if s_ in sel and d_ in sel and depth[s_] < K]
        # degree/throughput inside the case graph for baselines
        deg = defaultdict(int)
        thr = defaultdict(int)
        for (s_, d_), c in zip(zip(all_edges["s"], all_edges["d"]), all_edges["n"]):
            if s_ in sel and d_ in sel and depth[s_] < K:
                deg[s_] += 1
                deg[d_] += 1
                thr[s_] += int(c)
                thr[d_] += int(c)
        freezable = [a for a in sel if a != origin and a not in set(exits)]
        hop1 = {a for a in freezable if depth[a] == 1}
        for lam in LAMS:
            costs = {a: 100 + round(lam * 100 * math.log1p(base[a]) / math.log1p(maxb)) for a in freezable}
            try:
                net = build_split_network({str(a): c for a, c in costs.items()}, [(str(a), str(b)) for a, b in trans], str(origin), [str(a) for a in exits])
            except ValueError:
                continue
            if not reaches_exit(net):
                results.append(dict(comp=comp, K=K, lam=lam, status="no_path"))
                continue
            flow = solve_networkx(net)
            if flow.value >= net.inf:
                results.append(dict(comp=comp, K=K, lam=lam, status="no_finite_cut"))
                continue
            frozen = {int(a) for a in extract_cut(net, flow)}
            k = len(frozen)
            cp_blocked, cp_coll, cp_prec = score(frozen)
            row = dict(comp=comp, K=K, lam=lam, status="cut", k=k, region=len(sel), exits=len(exits),
                       hop1=len(hop1), trivial=(frozen == hop1), contains_hop1_only=frozen <= hop1,
                       cp_blocked=cp_blocked, cp_coll=cp_coll, cp_prec=cp_prec,
                       post_in_region=post_in_region / max(1, post.height), post_n=post.height,
                       cost=flow.value)
            for name, rank in (("deg", deg), ("thr", thr)):
                pick = set(sorted(freezable, key=lambda a: (-rank.get(a, 0), a))[:k])
                b, c_, p_ = score(pick)
                row[f"{name}_blocked"], row[f"{name}_coll"], row[f"{name}_prec"] = b, c_, p_
            results.append(row)
    if (ci + 1) % 20 == 0:
        print(f"  processed {ci + 1}/{len(comps)} components", flush=True)

print("\nskipped:", dict(skipped))
res = pl.DataFrame(results, infer_schema_length=None)
pl.Config.set_tbl_formatting("ASCII_FULL_CONDENSED")
pl.Config.set_tbl_rows(40)
pl.Config.set_tbl_cols(20)
print(res.group_by("K", "lam", "status").agg(pl.len().alias("cases")).sort("K", "lam", "status"))
cut = res.filter(pl.col("status") == "cut")
summary = cut.group_by("K", "lam").agg(
    pl.len().alias("cases"),
    pl.col("k").median().alias("med_k"),
    pl.col("hop1").median().alias("med_hop1"),
    pl.col("trivial").mean().alias("share_trivial"),
    pl.col("contains_hop1_only").mean().alias("share_hop1_subset"),
    pl.col("post_in_region").mean().alias("ceiling"),
    pl.col("cp_blocked").mean().alias("cp_blocked"), pl.col("deg_blocked").mean().alias("deg_blocked"), pl.col("thr_blocked").mean().alias("thr_blocked"),
    pl.col("cp_coll").mean().alias("cp_coll"), pl.col("deg_coll").mean().alias("deg_coll"), pl.col("thr_coll").mean().alias("thr_coll"),
    pl.col("cp_prec").mean().alias("cp_prec"), pl.col("deg_prec").mean().alias("deg_prec"),
).sort("K", "lam")
print(summary)
res.write_csv(args.out)
