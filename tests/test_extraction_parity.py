"""Cypher extraction vs an independent Polars implementation, on 20 dev cases (needs the loaded tenant graph)."""

from __future__ import annotations

import pytest
import polars as pl

from chokepoint.casegraph import CaseTooLarge, extract_region
from chokepoint.cases import make_cases
from chokepoint.data_ibm import DEFAULT_CSV, load_transactions
from chokepoint.db import connect

pytestmark = pytest.mark.db

DAY = 1440
HOPS = 3
MAX_NODES = 4000


@pytest.fixture(scope="module")
def db():
    return connect()


@pytest.fixture(scope="module")
def df():
    if not DEFAULT_CSV.exists():
        pytest.skip("dataset not downloaded")
    return load_transactions()


@pytest.fixture(scope="module")
def graph(db):
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded (run scripts/load_ibm.py)")
    return db.select_graph("tenant_ibm")


def polars_region(df: pl.DataFrame, origin: str, t: int, window: int, hops: int):
    win = df.filter((pl.col("ts_min") >= t - window) & (pl.col("ts_min") <= t) & (pl.col("a_src") != pl.col("a_dst")))
    depth = {origin: 0}
    frontier = [origin]
    parts = []
    for hop in range(hops):
        if not frontier:
            break
        e = win.filter(pl.col("a_src").is_in(frontier))
        parts.append(e)
        new = [d for d in e["a_dst"].unique().to_list() if d not in depth]
        for d in new:
            depth[d] = hop + 1
        frontier = new
        if len(depth) > MAX_NODES:
            return None
    if not parts:
        return depth, {}
    agg = pl.concat(parts).group_by("a_src", "a_dst").agg(pl.len().alias("n"), pl.col("ts_min").max().alias("last"))
    return depth, {(s, d): (n, last) for s, d, n, last in agg.iter_rows()}


def test_cypher_extraction_matches_polars_on_20_dev_cases(df, graph):
    cases = make_cases(df, frac=0.5, split="dev")
    compared = skipped = 0
    for case in cases:
        want = polars_region(df, case.origin, case.alert_ts, 7 * DAY, HOPS)
        if want is None:
            with pytest.raises(CaseTooLarge):
                extract_region(graph, case.origin, case.alert_ts, 7 * DAY, HOPS, max_nodes=MAX_NODES)
            skipped += 1
            continue
        got = extract_region(graph, case.origin, case.alert_ts, 7 * DAY, HOPS, max_nodes=MAX_NODES)
        assert got.depth == want[0], f"comp {case.comp}: depth differs"
        assert got.edges == want[1], f"comp {case.comp}: edges differ"
        compared += 1
        if compared == 20:
            break
    assert compared >= 20, (compared, skipped)
