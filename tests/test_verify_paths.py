"""Verification must not depend on max-flow: after deleting the frozen accounts from a copy of the
graph, FalkorDB's ``shortestPath`` must agree with plain NetworkX reachability."""

from __future__ import annotations

import random

import networkx as nx
import pytest

from chokepoint.db import connect

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def db():
    return connect()


def _build(db, name, nodes, edges):
    try:
        db.select_graph(name).delete()
    except Exception:
        pass
    g = db.select_graph(name)
    g.query("CREATE INDEX FOR (a:A) ON (a.id)")
    g.query("UNWIND $ids AS i CREATE (:A {id: i})", params={"ids": nodes})
    g.query(
        "UNWIND $rows AS r MATCH (a:A {id: r.u}), (b:A {id: r.v}) CREATE (a)-[:T]->(b)",
        params={"rows": [{"u": u, "v": v} for u, v in edges]},
    )
    return g


NO_ROUTE = "MATCH (s:A {id: $s}), (t:A {id: $t}) RETURN shortestPath((s)-[:T*]->(t)) IS NULL"


def test_shortest_path_agrees_with_networkx_after_deleting_frozen_accounts(db):
    rng = random.Random(2026)
    checks = routes = 0
    for case in range(40):
        n = rng.randint(6, 25)
        nodes = ["src", "x0", *[f"a{i}" for i in range(n)]]
        edges = [(u, v) for u in nodes for v in nodes if u != v and v != "src" and u != "x0"
                 and rng.random() < rng.choice([0.08, 0.15, 0.25])]
        base = _build(db, f"test_vp_{case}", nodes, edges)
        copy_name = f"test_vp_{case}_v"
        try:
            db.select_graph(copy_name).delete()
        except Exception:
            pass
        for trial in range(4):
            frozen = [x for x in nodes if x not in ("src", "x0") and rng.random() < 0.25]
            base.copy(copy_name)
            g = db.select_graph(copy_name)
            if frozen:
                g.query("MATCH (a:A) WHERE a.id IN $f DELETE a", params={"f": frozen})
            got_no_route = g.ro_query(NO_ROUTE, params={"s": "src", "t": "x0"}).result_set[0][0]
            nxg = nx.DiGraph([(u, v) for u, v in edges if u not in frozen and v not in frozen])
            nxg.add_nodes_from(["src", "x0"])
            want_route = nx.has_path(nxg, "src", "x0")
            assert got_no_route == (not want_route), (case, trial, frozen)
            routes += want_route
            checks += 1
            g.delete()
        base.delete()
    assert checks == 160 and 10 < routes < checks - 10, (checks, routes)
