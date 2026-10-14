"""G7 step 3: look at the larger laundering components (read-only)."""

from __future__ import annotations

import networkx as nx
import polars as pl

from chokepoint.data_ibm import load_transactions

pl.Config.set_tbl_formatting("ASCII_FULL_CONDENSED")
pl.Config.set_tbl_rows(60)
pl.Config.set_tbl_cols(12)

df = load_transactions()
laund = df.filter(pl.col("laundering") == 1)
g = nx.Graph()
g.add_edges_from(zip(laund["a_src"].to_list(), laund["a_dst"].to_list()))
comp_id = {a: i for i, c in enumerate(nx.connected_components(g)) for a in c}
laund = laund.with_columns(pl.col("a_src").replace_strict(comp_id, return_dtype=pl.Int64).alias("comp"))
sizes = laund.group_by("comp").agg(pl.len().alias("n")).sort("n", descending=True)
for lo in (1, 2, 3, 5, 10, 20, 50, 100):
    print(f"components with >= {lo:3d} laundering transfers:", sizes.filter(pl.col("n") >= lo).height)

for want in (6, 14, 30):
    row = sizes.filter(pl.col("n") >= want).sort("n").head(1)
    comp = row["comp"][0]
    sub = laund.filter(pl.col("comp") == comp).sort("ts_min")
    print(f"\n--- component {comp}: {sub.height} laundering transfers ---")
    print(sub.select(pl.col("dt").dt.strftime("%m-%d %H:%M").alias("when"), "a_src", "a_dst", "amt_paid", "cur_paid", "fmt").head(30))
    accts = set(sub["a_src"]) | set(sub["a_dst"])
    out_deg = sub.group_by("a_src").agg(pl.len().alias("out")).sort("out", descending=True).head(3)
    in_deg = sub.group_by("a_dst").agg(pl.len().alias("in")).sort("in", descending=True).head(3)
    print("accounts", len(accts), "| top out-degree", out_deg["out"].to_list(), "| top in-degree", in_deg["in"].to_list())
    legit = df.filter((pl.col("laundering") == 0) & (pl.col("a_src").is_in(list(accts)) | pl.col("a_dst").is_in(list(accts))))
    print("legit transfers touching these accounts:", legit.height,
          "| formats:", legit.group_by("fmt").agg(pl.len()).sort("len", descending=True).rows()[:4])

