"""Gates G0/G1/G2/G5/G8 against a live FalkorDB (docker compose up -d)."""

from __future__ import annotations

import pytest

from chokepoint.db import connect

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def db():
    return connect()


@pytest.fixture()
def graph(db):
    name = "test_gate_scratch"
    try:
        db.select_graph(name).delete()
    except Exception:
        pass
    g = db.select_graph(name)
    yield g
    try:
        g.delete()
    except Exception:
        pass


def test_g0_ping_and_version(db):
    info = db.connection.info("modules")
    mods = info.get("modules") if isinstance(info, dict) else info
    assert any("graph" in str(m).lower() for m in (mods if isinstance(mods, list) else [mods]))


def test_g1_procedures_present(graph):
    res = graph.query("CALL dbms.procedures() YIELD name RETURN name")
    names = {r[0] for r in res.result_set}
    for needed in (
        "algo.maxFlow",
        "algo.betweenness",
        "algo.labelPropagation",
        "algo.WCC",
        "algo.BFS",
        "algo.SPpaths",
    ):
        assert needed in names, f"{needed} missing"


def test_g5_parameter_binding(graph):
    assert graph.query("RETURN $x", params={"x": 7}).result_set[0][0] == 7
    assert graph.query("RETURN size($xs)", params={"xs": [1, 2, 3]}).result_set[0][0] == 3
    rows = [{"id": "a", "n": 1}, {"id": "b", "n": 2}]
    graph.query("UNWIND $rows AS r CREATE (:T {id: r.id, n: r.n})", params={"rows": rows})
    got = graph.ro_query("MATCH (t:T) RETURN t.id, t.n ORDER BY t.id").result_set
    assert got == [["a", 1], ["b", 2]]


def test_g2_docs_toy_example(graph):
    graph.query(
        "CREATE (a:N {id:'A'}), (b:N {id:'B'}), (c:N {id:'C'}),"
        "(a)-[:E {cap:10}]->(b), (a)-[:E {cap:5}]->(c), (b)-[:E {cap:8}]->(c)"
    )
    res = graph.query(
        "MATCH (s:N {id:'A'}), (t:N {id:'C'}) "
        "CALL algo.maxFlow({sourceNodes:[s], targetNodes:[t], relationshipTypes:['E'],"
        " capacityProperty:'cap'}) YIELD maxFlow RETURN maxFlow"
    )
    assert res.result_set[0][0] == 13


def test_g8_readonly_rejects_write(graph):
    graph.query("CREATE (:T {id:'x'})")
    with pytest.raises(Exception):
        graph.ro_query("CREATE (:T {id:'y'})")
