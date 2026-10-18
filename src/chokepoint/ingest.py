"""Load the IBM transactions into the FalkorDB tenant graph (and labels into a separate eval graph).

Tenant graph ``tenant_<name>``: ``(:Account {id})-[:TRANSFER {tid, ts, amt, cur, fmt, batch}]->(:Account)``
- ``id`` is ``"<bank>:<account>"``; ``ts`` is epoch minutes; ``amt`` is the amount paid in integer
  cents of the paying currency (``cur``); ``batch`` is the data version (0 for the initial load).
- Self-transfers (money moved between an account and itself) are dropped.
- Laundering labels are NOT written here. They go to ``eval_<name>`` as ``(:TransferLabel {tid, pattern})``
  so planning tools physically cannot read them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import networkx as nx
import polars as pl

from .data_ibm import load_transactions


@dataclass(frozen=True)
class LoadStats:
    accounts: int
    transfers: int
    seconds_nodes: float
    seconds_edges: float


def prepare(df: pl.DataFrame) -> pl.DataFrame:
    """Drop self-transfers and add integer cents."""
    return (
        df.filter(pl.col("a_src") != pl.col("a_dst"))
        .with_columns((pl.col("amt_paid") * 100).round(0).cast(pl.Int64).alias("cents"))
        .sort("ts_min", "tid")
    )


def load_tenant(db, df: pl.DataFrame, name: str, batch_size: int = 5000, batch: int = 0) -> LoadStats:
    """Create ``tenant_<name>`` from a prepared frame. The graph must not exist yet."""
    gname = f"tenant_{name}"
    if gname in db.list_graphs():
        raise RuntimeError(f"{gname} already exists; delete it first")
    g = db.select_graph(gname)
    g.query("CREATE INDEX FOR (a:Account) ON (a.id)")
    g.query("CREATE INDEX FOR ()-[t:TRANSFER]-() ON (t.ts)")
    accounts = pl.concat([df["a_src"], df["a_dst"]]).unique().sort().to_list()
    t0 = time.time()
    for i in range(0, len(accounts), batch_size):
        g.query("UNWIND $ids AS i CREATE (:Account {id: i})", params={"ids": accounts[i : i + batch_size]})
    t1 = time.time()
    cols = ["tid", "ts_min", "a_src", "a_dst", "cents", "cur_paid", "fmt"]
    n = df.height
    for i in range(0, n, batch_size):
        rows = [
            {"tid": r[0], "ts": r[1], "s": r[2], "d": r[3], "amt": r[4], "cur": r[5], "fmt": r[6], "b": batch}
            for r in df.select(cols).slice(i, batch_size).iter_rows()
        ]
        g.query(
            "UNWIND $rows AS r MATCH (a:Account {id: r.s}), (b:Account {id: r.d}) "
            "CREATE (a)-[:TRANSFER {tid: r.tid, ts: r.ts, amt: r.amt, cur: r.cur, fmt: r.fmt, batch: r.b}]->(b)",
            params={"rows": rows},
        )
    t2 = time.time()
    got_nodes = g.ro_query("MATCH (a:Account) RETURN count(a)").result_set[0][0]
    got_edges = g.ro_query("MATCH ()-[t:TRANSFER]->() RETURN count(t)").result_set[0][0]
    if got_nodes != len(accounts) or got_edges != n:
        raise AssertionError(f"load mismatch: nodes {got_nodes}/{len(accounts)}, edges {got_edges}/{n}")
    return LoadStats(len(accounts), n, t1 - t0, t2 - t1)


def pattern_labels(df: pl.DataFrame) -> pl.DataFrame:
    """Laundering transfers with a pattern id (connected component of laundering transfers)."""
    laund = df.filter(pl.col("laundering") == 1)
    g = nx.Graph()
    g.add_edges_from(zip(laund["a_src"].to_list(), laund["a_dst"].to_list()))
    comp = {a: i for i, c in enumerate(nx.connected_components(g)) for a in c}
    return laund.select("tid", pl.col("a_src").replace_strict(comp, return_dtype=pl.Int64).alias("pattern"))


def load_eval(db, df: pl.DataFrame, name: str) -> int:
    gname = f"eval_{name}"
    if gname in db.list_graphs():
        raise RuntimeError(f"{gname} already exists; delete it first")
    g = db.select_graph(gname)
    g.query("CREATE INDEX FOR (l:TransferLabel) ON (l.tid)")
    labels = pattern_labels(df)
    rows = [{"tid": t, "p": p} for t, p in labels.iter_rows()]
    for i in range(0, len(rows), 2000):
        g.query("UNWIND $rows AS r CREATE (:TransferLabel {tid: r.tid, pattern: r.p, laundering: true})",
                params={"rows": rows[i : i + 2000]})
    return len(rows)


def load_all(db, name: str = "ibm", limit: int | None = None, batch_size: int = 5000, csv: str | None = None):
    df = load_transactions(csv) if csv else load_transactions()
    full = df
    if limit:
        df = df.head(limit)
    stats = load_tenant(db, prepare(df), name, batch_size)
    n_labels = load_eval(db, full, name) if not limit else 0
    return stats, n_labels
