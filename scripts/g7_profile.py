"""G7 step 1: profile the IBM HI-Small transactions file (read-only)."""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

pl.Config.set_tbl_formatting("ASCII_FULL_CONDENSED")
pl.Config.set_tbl_rows(40)

CSV = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw/ibm_hi_small/extracted/HI-Small_Trans.csv")
COLS = ["ts", "from_bank", "src", "to_bank", "dst", "amt_recv", "cur_recv", "amt_paid", "cur_paid", "fmt", "laundering"]

df = pl.read_csv(
    CSV, has_header=True, new_columns=COLS,
    schema_overrides={"from_bank": pl.Utf8, "to_bank": pl.Utf8, "src": pl.Utf8, "dst": pl.Utf8},
).with_columns(pl.col("ts").str.strptime(pl.Datetime, "%Y/%m/%d %H:%M"))

print("rows", df.height, "| laundering rows", int(df["laundering"].sum()),
      f"({df['laundering'].mean():.5%})")
print("time range", df["ts"].min(), "->", df["ts"].max())
accts = pl.concat([df["src"], df["dst"]]).n_unique()
print("distinct accounts", accts, "| distinct banks", pl.concat([df["from_bank"], df["to_bank"]]).n_unique())
print("self transfers (src == dst):", df.filter(pl.col("src") == pl.col("dst")).height)
print("cross-bank transfers:", df.filter(pl.col("from_bank") != pl.col("to_bank")).height)
print("amount paid != received (currency conversion):", df.filter(pl.col("amt_paid") != pl.col("amt_recv")).height)

print("\nPayment format (rows, laundering rows, laundering share)")
print(df.group_by("fmt").agg(pl.len().alias("rows"), pl.col("laundering").sum().alias("laund"),
                             pl.col("laundering").mean().alias("share")).sort("rows", descending=True))
print("\nCurrencies paid (top 8)")
print(df.group_by("cur_paid").agg(pl.len().alias("rows")).sort("rows", descending=True).head(8))
print("\nPer-day rows")
print(df.group_by(pl.col("ts").dt.date().alias("day")).agg(pl.len().alias("rows"),
      pl.col("laundering").sum().alias("laund")).sort("day"))
print("\nAmount paid quantiles (all) and (laundering)")
for name, sub in (("all", df), ("laundering", df.filter(pl.col("laundering") == 1))):
    print(name, sub["amt_paid"].quantile(0.0), sub["amt_paid"].quantile(0.5), sub["amt_paid"].quantile(0.99), sub["amt_paid"].max())
