"""Derive USD-per-unit rates for every currency from the dataset's own cross-currency transfers.

For a transfer paying X and receiving Y with amounts a (paid) and b (received), the implied rate is
b/a units of Y per unit of X. Rows with USD on either side give USD-per-unit directly; other currencies
are reached through USD when no direct row exists. The median over rows is used. Prints a table and
writes data/derived/fx_usd.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from chokepoint.data_ibm import load_transactions

df = load_transactions().filter(pl.col("cur_paid") != pl.col("cur_recv")).filter(
    (pl.col("amt_paid") > 0) & (pl.col("amt_recv") > 0)
)
print("cross-currency rows:", df.height)
usd = "US Dollar"
# USD per unit of X
a = df.filter(pl.col("cur_recv") == usd).select(pl.col("cur_paid").alias("cur"), (pl.col("amt_recv") / pl.col("amt_paid")).alias("usd_per"))
b = df.filter(pl.col("cur_paid") == usd).select(pl.col("cur_recv").alias("cur"), (pl.col("amt_paid") / pl.col("amt_recv")).alias("usd_per"))
rates = pl.concat([a, b]).group_by("cur").agg(
    pl.len().alias("rows"),
    pl.col("usd_per").median().alias("median"),
    pl.col("usd_per").quantile(0.05).alias("p05"),
    pl.col("usd_per").quantile(0.95).alias("p95"),
).sort("rows", descending=True)
pl.Config.set_tbl_formatting("ASCII_FULL_CONDENSED")
pl.Config.set_tbl_rows(40)
print(rates)
all_cur = set(load_transactions()["cur_paid"].unique().to_list())
missing = all_cur - set(rates["cur"].to_list()) - {usd}
print("currencies without a direct USD row:", sorted(missing))
out = {r[0]: r[2] for r in rates.iter_rows()}
out[usd] = 1.0
Path("data/derived").mkdir(parents=True, exist_ok=True)
Path("data/derived/fx_usd.json").write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
print("wrote data/derived/fx_usd.json with", len(out), "currencies")
