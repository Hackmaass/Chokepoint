"""Pilot v2 (offline). Implements docs/pilot-protocol.md: dev/test split, origin frozen at alert time,
analyst baselines, and static vs time-respecting (trail) extraction. Labels are used only to pick
cases and to score. Usage:

  python -I scripts/g7_pilot2.py --split dev --frac 0.5 --out scratch/p2_dev_f50.csv
"""

from __future__ import annotations

import argparse
import math
import random
from collections import defaultdict

import networkx as nx
import polars as pl

from chokepoint.algo.cut import build_split_network, extract_cut, reaches_exit, solve_networkx
from chokepoint.data_ibm import load_transactions

ap = argparse.ArgumentParser()
ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
ap.add_argument("--frac", type=float, default=0.5)
ap.add_argument("--min-transfers", type=int, default=5)
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--max-nodes", type=int, default=4000)
ap.add_argument("--ks", default="3,4")
ap.add_argument("--lambdas", default="0,0.5,1,2,4,8")
ap.add_argument("--variants", default="static3,static7,trail")
ap.add_argument("--cost", choices=["log", "linear"], default="log")
ap.add_argument("--out", default="scratch/p2.csv")
args = ap.parse_args()
KS = [int(x) for x in args.ks.split(",")]
LAMS = [float(x) for x in args.lambdas.split(",")]
VARIANTS = args.variants.split(",")
DAY = 1440
HORIZON = 3 * DAY
SEP7_END = 6 * DAY + DAY  # minutes after the dataset start (Sep 1 00:00) for end of Sep 7

df = load_transactions()
with pl.StringCache():
    df = df.with_columns(pl.col("a_src").cast(pl.Categorical).to_physical().alias("s"),
                         pl.col("a_dst").cast(pl.Categorical).to_physical().alias("d"))
df = df.select("tid", "ts_min", "s", "d", "fmt", "laundering")
start_min = int(df["ts_min"].min())
laund = df.filter(pl.col("laundering") == 1).sort("ts_min", "tid")
legit = df.filter(pl.col("laundering") == 0)

g = nx.Graph()
g.add_edges_from(zip(laund["s"].to_list(), laund["d"].to_list()))
comp_of = {a: i for i, c in enumerate(nx.connected_components(g)) for a in c}
laund = laund.with_columns(pl.col("s").replace_strict(comp_of, return_dtype=pl.Int64).alias("comp"))
sizes = laund.group_by("comp").agg(pl.len().alias("n"))
comps = sorted(sizes.filter(pl.col("n") >= args.min_transfers)["comp"].to_list())
random.Random(20261015).shuffle(comps)
half = len(comps) // 2
split_of = {c: ("dev" if i < half else "test") for i, c in enumerate(comps)}
if args.split != "all":
    comps = [c for c in comps if split_of[c] == args.split]
if args.limit:
    comps = comps[: args.limit]
print(f"split={args.split} frac={args.frac}: {len(comps)} components", flush=True)


def extract(win: pl.DataFrame, origin: int, K: int, t0: int | None):
    """Return (depth dict, edges DataFrame s,d,n,last). t0 is None for static (ignore time)."""
    depth = {origin: 0}
    frontier = {origin: t0}
    parts = []
    for hop in range(K):
        if not frontier:
            break
        if t0 is None:
            e = win.filter(pl.col("s").is_in(list(frontier)))
        else:
            fr = pl.DataFrame({"s": list(frontier), "arr": list(frontier.values())})
            e = win.join(fr, on="s").filter(pl.col("ts_min") >= pl.col("arr")).drop("arr")
        if e.height == 0:
            break
        parts.append(e)
        first = e.group_by("d").agg(pl.col("ts_min").min().alias("a"))
        new = {}
        for d_, a_ in zip(first["d"].to_list(), first["a"].to_list()):
            if d_ not in depth:
                depth[d_] = hop + 1
                new[d_] = a_
        frontier = new
        if len(depth) > args.max_nodes:
            return depth, None
    if not parts:
        return depth, pl.DataFrame()
    edges = pl.concat(parts).group_by("s", "d").agg(pl.len().alias("n"), pl.col("ts_min").max().alias("last"))
    return depth, edges


rows = []
skipped = defaultdict(int)
for ci, comp in enumerate(comps):
    sub = laund.filter(pl.col("comp") == comp).sort("ts_min", "tid")
    n = sub.height
    k_t = max(1, math.ceil(args.frac * n))
    t = int(sub["ts_min"][k_t - 1])
    receivers = set(sub["d"].to_list())
    origin = next((s_ for s_ in sub["s"].to_list() if s_ not in receivers), sub["s"][0])
    t0 = int(sub.filter(pl.col("s") == origin)["ts_min"].min()) - 1
    cut_tid = sub["tid"][k_t - 1]
    post = sub.filter((pl.col("ts_min") > t) | ((pl.col("ts_min") == t) & (pl.col("tid") > cut_tid)))
    if post.height == 0:
        skipped["no post-t laundering"] += 1
        continue
    laund_accts = set(sub["s"].to_list()) | set(sub["d"].to_list())
    post_pairs = list(zip(post["s"].to_list(), post["d"].to_list()))
    esc_pairs = [(s_, d_) for s_, d_ in post_pairs if s_ != origin and d_ != origin]
    origin_touch = len(post_pairs) - len(esc_pairs)
    pre = df.filter(pl.col("ts_min") <= t)

    for variant in VARIANTS:
        if variant == "trail":
            win = df.filter((pl.col("ts_min") >= t0) & (pl.col("ts_min") <= t))
            tstart = t0
        else:
            W = int(variant.replace("static", "")) * DAY
            win = df.filter((pl.col("ts_min") >= t - W) & (pl.col("ts_min") <= t))
            tstart = None
        depth, edges = extract(win, origin, max(KS), tstart)
        if edges is None:
            skipped[f"{variant}: region too large"] += 1
            continue
        if edges.height == 0:
            skipped[f"{variant}: origin has no usable outgoing transfers"] += 1
            continue
        region = list(depth)
        touch = (pre.filter(pl.col("s").is_in(region)).group_by("s").agg(pl.len().alias("n")).rename({"s": "a"})
                 .join(pre.filter(pl.col("d").is_in(region)).group_by("d").agg(pl.len().alias("m")).rename({"d": "a"}),
                       on="a", how="full", coalesce=True).fill_null(0))
        total_touch = {a: int(x + y) for a, x, y in zip(touch["a"], touch["n"], touch["m"])}
        inside = defaultdict(int)
        for s_, d_, c in zip(edges["s"], edges["d"], edges["n"]):
            inside[s_] += int(c)
            inside[d_] += int(c)
        base = {a: max(0, total_touch.get(a, 0) - inside.get(a, 0)) for a in region}
        maxb = max(base.values()) or 1
        post_legit = legit.filter((pl.col("ts_min") > t) & (pl.col("ts_min") <= t + HORIZON)
                                  & (pl.col("s").is_in(region) | pl.col("d").is_in(region)))
        pl_pairs = list(zip(post_legit["s"].to_list(), post_legit["d"].to_list()))
        e_s, e_d, e_n, e_last = (edges[c].to_list() for c in ("s", "d", "n", "last"))

        def score(frozen: set[int]) -> dict:
            hit_esc = sum(1 for s_, d_ in esc_pairs if s_ in frozen or d_ in frozen)
            return dict(
                blk_esc=(hit_esc / len(esc_pairs)) if esc_pairs else float("nan"),
                whole=(hit_esc + origin_touch) / n,
                coll=sum(1 for s_, d_ in pl_pairs if s_ in frozen or d_ in frozen),
                prec=(sum(1 for a in frozen if a in laund_accts) / len(frozen)) if frozen else 0.0,
            )

        for K in KS:
            sel = {a for a, dp in depth.items() if dp <= K}
            exits = {a for a, dp in depth.items() if dp == K}
            idx = [i for i in range(len(e_s)) if e_s[i] in sel and e_d[i] in sel and depth[e_s[i]] < K]
            trans = [(e_s[i], e_d[i]) for i in idx]
            deg, thr = defaultdict(int), defaultdict(int)
            hop1_last, hop1_cnt = {}, {}
            for i in idx:
                deg[e_s[i]] += 1
                deg[e_d[i]] += 1
                thr[e_s[i]] += int(e_n[i])
                thr[e_d[i]] += int(e_n[i])
                if e_s[i] == origin:
                    hop1_last[e_d[i]] = int(e_last[i])
                    hop1_cnt[e_d[i]] = int(e_n[i])
            freezable = [a for a in sel if a != origin and a not in exits]
            by_deg = sorted(freezable, key=lambda a: (-deg.get(a, 0), a))
            hop1 = [a for a in freezable if depth[a] == 1]
            recent_order = sorted(hop1, key=lambda a: (-hop1_last.get(a, 0), a)) + [a for a in by_deg if a not in hop1_last]
            cnt_order = sorted(hop1, key=lambda a: (-hop1_cnt.get(a, 0), a)) + [a for a in by_deg if a not in hop1_cnt]
            thr_order = sorted(freezable, key=lambda a: (-thr.get(a, 0), a))
            for lam in LAMS:
                costs = ({a: 100 + round(lam * 100 * math.log1p(base[a]) / math.log1p(maxb)) for a in freezable} if args.cost == "log"
                         else {a: 100 + round(10 * lam * base[a]) for a in freezable})
                try:
                    net = build_split_network({str(a): c for a, c in costs.items()},
                                              [(str(a), str(b)) for a, b in trans], str(origin), [str(a) for a in exits])
                except ValueError:
                    continue
                key = dict(comp=comp, split=split_of[comp], frac=args.frac, variant=variant, K=K, lam=lam,
                           t_day=(t - start_min) / DAY, post_n=post.height, esc_n=len(esc_pairs),
                           region=len(sel), hop1=len(hop1))
                if not reaches_exit(net):
                    rows.append({**key, "status": "no_path"})
                    continue
                flow = solve_networkx(net)
                if flow.value >= net.inf:
                    rows.append({**key, "status": "no_finite_cut"})
                    continue
                frozen = {int(a) for a in extract_cut(net, flow)}
                k = len(frozen)
                row = {**key, "status": "cut", "k": k, "cost": flow.value, "cut_in_hop1": frozen <= set(hop1)}
                for name, order in (("cp", None), ("deg", by_deg), ("thr", thr_order), ("rec", recent_order), ("cnt", cnt_order)):
                    pick = frozen if order is None else set(order[:k])
                    for m, v in score(pick).items():
                        row[f"{name}_{m}"] = v
                rows.append(row)
    if (ci + 1) % 15 == 0:
        print(f"  processed {ci + 1}/{len(comps)}", flush=True)

print("skipped:", dict(skipped))
pl.DataFrame(rows, infer_schema_length=None).write_csv(args.out)
print("wrote", args.out, len(rows), "rows")
