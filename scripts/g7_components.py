"""G7 step 2: structure of the laundering-labelled transfers and of bank sizes (read-only)."""

from __future__ import annotations

import networkx as nx
import polars as pl

from chokepoint.data_ibm import load_transactions

pl.Config.set_tbl_formatting("ASCII_FULL_CONDENSED")
pl.Config.set_tbl_rows(30)

df = load_transactions()
print("rows", df.height)

# Is an account string unique across banks? (decides the account key)
pairs = pl.concat([df.select(pl.col("src").alias("acct"), pl.col("from_bank").alias("bank")),
                   df.select(pl.col("dst").alias("acct"), pl.col("to_bank").alias("bank"))]).unique()
print("distinct (acct, bank) pairs", pairs.height, "| distinct acct strings", pairs["acct"].n_unique())

# bank sizes
sizes = pairs.group_by("bank").agg(pl.len().alias("accounts")).sort("accounts", descending=True)
print("\nbank size: top 10, quantiles")
print(sizes.head(10))
print("banks", sizes.height, "median accounts/bank", sizes["accounts"].median(),
      "banks with >= 1000 accounts", sizes.filter(pl.col("accounts") >= 1000).height,
      "accounts in top 20 banks", sizes.head(20)["accounts"].sum(), "of", pairs.height)

# laundering components (undirected, account level)
laund = df.filter(pl.col("laundering") == 1)
g = nx.Graph()
g.add_edges_from(zip(laund["a_src"].to_list(), laund["a_dst"].to_list()))
comps = list(nx.connected_components(g))
print("\nlaundering transfers", laund.height, "| accounts", g.number_of_nodes(), "| components", len(comps))
comp_id = {}
for i, c in enumerate(comps):
    for a in c:
        comp_id[a] = i
laund = laund.with_columns(pl.col("a_src").replace_strict(comp_id, return_dtype=pl.Int64).alias("comp"))
stats = laund.group_by("comp").agg(
    pl.len().alias("transfers"),
    pl.col("a_src").n_unique().alias("srcs"),
    (pl.col("ts_min").max() - pl.col("ts_min").min()).alias("span_min"),
    pl.col("ts_min").min().alias("first"),
    pl.col("from_bank").n_unique().alias("banks"),
    (pl.col("fmt") == "ACH").mean().alias("ach_share"),
)
print("transfers per component quantiles:", [stats["transfers"].quantile(q) for q in (0.0, 0.25, 0.5, 0.75, 0.9, 1.0)])
print("accounts per component quantiles:", sorted(len(c) for c in comps)[:: max(1, len(comps)//10)])
print("span (hours) quantiles:", [round((stats["span_min"].quantile(q) or 0) / 60, 1) for q in (0.0, 0.25, 0.5, 0.75, 0.9, 1.0)])
print("components touching >1 bank:", stats.filter(pl.col("banks") > 1).height, "of", stats.height)

# how many accounts of a laundering component also carry legitimate traffic?
laund_accts = set(g.nodes)
legit = df.filter(pl.col("laundering") == 0)
touch = pl.concat([legit["a_src"], legit["a_dst"]]).unique()
share = touch.is_in(list(laund_accts)).sum()
print("\nlaundering accounts that also have legitimate transfers:", share, "of", len(laund_accts))
