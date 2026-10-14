"""Evaluation case selection from laundering-labelled transfers (labels used for selection only).

A case is the origin of a laundering pattern (connected component of laundering transfers with enough
transfers) and an alert time at a fraction of that pattern's transfers. Components are shuffled with a
fixed seed and split 50/50 into dev and test (docs/pilot-protocol.md).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import networkx as nx
import polars as pl

SPLIT_SEED = 20261015


@dataclass(frozen=True)
class EvalCase:
    comp: int
    split: str
    origin: str
    alert_ts: int  # epoch minutes
    first_laundering_ts: int
    pattern_size: int


def laundering_components(df: pl.DataFrame, min_transfers: int = 5) -> dict[int, pl.DataFrame]:
    laund = df.filter(pl.col("laundering") == 1).sort("ts_min", "tid")
    g = nx.Graph()
    g.add_edges_from(zip(laund["a_src"].to_list(), laund["a_dst"].to_list()))
    comp_of = {a: i for i, c in enumerate(nx.connected_components(g)) for a in c}
    laund = laund.with_columns(pl.col("a_src").replace_strict(comp_of, return_dtype=pl.Int64).alias("comp"))
    return {
        k[0]: sub
        for k, sub in laund.partition_by("comp", as_dict=True).items()
        if sub.height >= min_transfers
    }


def split_of(comp_ids: list[int]) -> dict[int, str]:
    """Same split as scripts/g7_pilot2.py: sort, seeded shuffle, first half dev."""
    ids = sorted(comp_ids)
    random.Random(SPLIT_SEED).shuffle(ids)
    half = len(ids) // 2
    return {c: ("dev" if i < half else "test") for i, c in enumerate(ids)}


def make_cases(df: pl.DataFrame, frac: float = 0.5, split: str = "dev", min_transfers: int = 5) -> list[EvalCase]:
    comps = laundering_components(df, min_transfers)
    sp = split_of(list(comps))
    out = []
    for comp, sub in sorted(comps.items()):
        if split != "all" and sp[comp] != split:
            continue
        sub = sub.sort("ts_min", "tid")
        n = sub.height
        k_t = max(1, math.ceil(frac * n))
        t = int(sub["ts_min"][k_t - 1])
        receivers = set(sub["a_dst"].to_list())
        origin = next((s for s in sub["a_src"].to_list() if s not in receivers), sub["a_src"][0])
        first = int(sub.filter(pl.col("a_src") == origin)["ts_min"].min())
        out.append(EvalCase(comp, sp[comp], origin, t, first, n))
    return out
