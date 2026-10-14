"""Compact report of scratch/g7_pilot_results.csv (pilot output)."""

from __future__ import annotations

import sys

import polars as pl

path = sys.argv[1] if len(sys.argv) > 1 else "scratch/g7_pilot_results.csv"
res = pl.read_csv(path)
print("rows", res.height)
print("\nstatus by K, lambda (cases)")
for (K, lam), g in res.group_by("K", "lam", maintain_order=True):
    c = g.group_by("status").agg(pl.len()).sort("status")
    print(f"  K={K} lam={lam}: " + ", ".join(f"{s}={n}" for s, n in c.iter_rows()))
cut = res.filter(pl.col("status") == "cut")
print("\nfinite-cut share among cases with a route (K, lam): cut / (cut + no_finite_cut)")
print("\nK lam cases | k med | hop1 med | cut==hop1 | cut within hop1 | ceiling |"
      " blocked  CP  deg  thr | collateral CP  deg  thr | precision CP deg")
for (K, lam), g in cut.group_by("K", "lam", maintain_order=True):
    m = lambda c: g[c].mean()  # noqa: E731
    print(f"{K} {lam:3.1f} {g.height:5d} | {g['k'].median():5.1f} | {g['hop1'].median():8.1f} |"
          f" {m('trivial'):9.2f} | {m('contains_hop1_only'):15.2f} | {m('post_in_region'):7.2f} |"
          f" {m('cp_blocked'):.3f} {m('deg_blocked'):.3f} {m('thr_blocked'):.3f} |"
          f" {m('cp_coll'):6.1f} {m('deg_coll'):6.1f} {m('thr_coll'):6.1f} |"
          f" {m('cp_prec'):.2f} {m('deg_prec'):.2f}")
# paired comparison per case: CP vs degree
print("\npaired (per case): share where CP blocks more / equal / fewer than degree; collateral lower / equal / higher")
for (K, lam), g in cut.group_by("K", "lam", maintain_order=True):
    more = g.filter(pl.col("cp_blocked") > pl.col("deg_blocked") + 1e-9).height
    eq = g.filter((pl.col("cp_blocked") - pl.col("deg_blocked")).abs() <= 1e-9).height
    fewer = g.height - more - eq
    lo = g.filter(pl.col("cp_coll") < pl.col("deg_coll")).height
    eqc = g.filter(pl.col("cp_coll") == pl.col("deg_coll")).height
    print(f"  K={K} lam={lam}: blocked more/eq/fewer = {more}/{eq}/{fewer}; collateral lower/eq/higher = {lo}/{eqc}/{g.height - lo - eqc}")
