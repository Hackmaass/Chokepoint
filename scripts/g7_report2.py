"""Report for pilot v2 (docs/pilot-protocol.md). Reads one or more CSVs written by g7_pilot2.py."""

from __future__ import annotations

import random
import sys

import polars as pl

paths = sys.argv[1:] or ["scratch/p2_dev_f50.csv"]
res = pl.concat([pl.read_csv(p) for p in paths], how="diagonal_relaxed")
cut = res.filter(pl.col("status") == "cut").filter(pl.col("cp_blk_esc").is_not_nan() & pl.col("cp_blk_esc").is_not_null())
METHODS = ["deg", "thr", "rec", "cnt"]


def boot_ci(vals: list[float], n: int = 2000, seed: int = 1) -> tuple[float, float]:
    rng = random.Random(seed)
    if not vals:
        return (float("nan"), float("nan"))
    means = sorted(sum(rng.choice(vals) for _ in vals) / len(vals) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


print("status counts per (variant, K) at lambda=0")
for (v, K), g in res.filter(pl.col("lam") == 0).group_by("variant", "K", maintain_order=True):
    c = g.group_by("status").agg(pl.len()).sort("status")
    print(f"  {v:8s} K={K}: " + ", ".join(f"{s}={n}" for s, n in c.iter_rows()))

print("\nlambda = 0, blocked share of the money that already escaped (mean), plus paired wins/losses of CP"
      " against each baseline")
header = f"{'variant':8s} K cases | CP    deg   thr   rec   cnt  | strongest | wins/ties/losses vs strongest | wins-losses"
print(header)
summary = []
for (v, K), g in cut.filter(pl.col("lam") == 0).group_by("variant", "K", maintain_order=True):
    means = {m: g[f"{m}_blk_esc"].mean() for m in METHODS}
    strongest = max(means, key=means.get)
    diff = (g["cp_blk_esc"] - g[f"{strongest}_blk_esc"]).to_list()
    w = sum(d > 1e-9 for d in diff)
    l = sum(d < -1e-9 for d in diff)
    t = len(diff) - w - l
    summary.append((v, K, g.height, w - l))
    print(f"{v:8s} {K} {g.height:5d} | {g['cp_blk_esc'].mean():.3f} " + " ".join(f"{means[m]:.3f}" for m in METHODS)
          + f" | {strongest:9s} | {w}/{t}/{l} | {w - l}")

print("\nwhole-pattern share stopped (origin frozen + plan), lambda = 0, mean CP vs strongest-by-escaped")
for (v, K), g in cut.filter(pl.col("lam") == 0).group_by("variant", "K", maintain_order=True):
    print(f"  {v:8s} K={K}: CP {g['cp_whole'].mean():.3f} | deg {g['deg_whole'].mean():.3f} | rec {g['rec_whole'].mean():.3f} | cnt {g['cnt_whole'].mean():.3f}")

print("\nlambda sweep (trade-off) per config: mean blocked-of-escaped CP, median collateral CP, mean k, share of plans that differ from lambda=0")
for (v, K), g in cut.group_by("variant", "K", maintain_order=True):
    base = g.filter(pl.col("lam") == 0).select("comp", pl.col("k").alias("k0"), pl.col("cp_coll").alias("c0"))
    line = []
    for lam in sorted(g["lam"].unique().to_list()):
        gl = g.filter(pl.col("lam") == lam).join(base, on="comp")
        line.append(f"lam={lam:g}: blk {gl['cp_blk_esc'].mean():.3f} coll_med {gl['cp_coll'].median():.0f} k {gl['k'].mean():.1f}")
    print(f"  {v:8s} K={K}: " + " | ".join(line))

print("\nbootstrap 95% CI of mean (CP - strongest baseline), lambda=0")
for (v, K), g in cut.filter(pl.col("lam") == 0).group_by("variant", "K", maintain_order=True):
    means = {m: g[f"{m}_blk_esc"].mean() for m in METHODS}
    strongest = max(means, key=means.get)
    diff = (g["cp_blk_esc"] - g[f"{strongest}_blk_esc"]).to_list()
    lo, hi = boot_ci(diff)
    print(f"  {v:8s} K={K}: mean diff {sum(diff) / len(diff):+.3f}  CI [{lo:+.3f}, {hi:+.3f}]  n={len(diff)} (vs {strongest})")

late = res.filter((pl.col("lam") == 0) & (pl.col("t_day") > 7) & (pl.col("status") == "cut")).height
print(f"\nlate alerts (t after Sep 7) among cut rows at lambda=0: {late}")
print("\nLOCK RULE ranking (needs >= 50 cases):", sorted([s for s in summary if s[2] >= 50], key=lambda s: (-s[3], -s[2])))
