"""G2b/G3: node-split vertex cut and the certified FalkorDB max-flow, checked against NetworkX."""

from __future__ import annotations

import random

import pytest

from chokepoint.algo.cut import (
    FlowResult,
    NoFiniteCut,
    NoPath,
    build_split_network,
    certify,
    freeze_plan,
    reaches_exit,
    run_falkordb_maxflow,
    solve_networkx,
)
from chokepoint.db import connect

pytestmark = pytest.mark.db

SEEDS = range(120)


@pytest.fixture(scope="module")
def db():
    return connect()


def random_case(seed: int):
    rng = random.Random(seed)
    n = rng.randint(4, 22)
    names = [f"a{i}" for i in range(n)]
    source = "src"
    exits = [f"x{i}" for i in range(rng.randint(1, 3))]
    everyone = [source, *names, *exits]
    p = rng.choice([0.08, 0.12, 0.2, 0.3])
    transfers = [(u, v) for u in everyone for v in everyone if u != v and rng.random() < p]
    transfers += transfers[: rng.randint(0, 5)]  # duplicates must collapse
    costs = {a: rng.randint(1, 300) for a in names}
    return build_split_network(costs, transfers, source, exits)


def test_toy_cut_from_notes(db):
    # S -> A,B ; A,B -> C ; C -> X ; A -> X.  costs A=100, B=100, C=300. Unique optimum {A, B}.
    net = build_split_network(
        {"A": 100, "B": 100, "C": 300},
        [("S", "A"), ("S", "B"), ("A", "C"), ("B", "C"), ("C", "X"), ("A", "X")],
        "S",
        ["X"],
    )
    plan = freeze_plan(db, net, "test_cut_toy")
    assert plan.cost == 200
    assert plan.frozen == {"A", "B"}
    assert not reaches_exit(net, plan.frozen)


def test_direct_route_has_no_finite_cut(db):
    net = build_split_network({"A": 100}, [("S", "A"), ("A", "X"), ("S", "X")], "S", ["X"])
    with pytest.raises(NoFiniteCut):
        freeze_plan(db, net, "test_cut_direct")


def test_no_path(db):
    net = build_split_network({"A": 100, "B": 50}, [("S", "A"), ("B", "X")], "S", ["X"])
    with pytest.raises(NoPath):
        freeze_plan(db, net, "test_cut_nopath")


def test_parallel_transfers_are_merged():
    net = build_split_network({"A": 5}, [("S", "A"), ("S", "A"), ("A", "X")], "S", ["X"])
    assert sum(1 for (t, h) in net.edges if t == "S:out" and h == "A:in") == 1


def test_money_that_reached_an_exit_does_not_come_back():
    net = build_split_network({"A": 5}, [("S", "A"), ("A", "X"), ("X", "A"), ("A", "S")], "S", ["X"])
    assert not any(t.startswith("X") or h == "S:in" for (t, h) in net.edges)


def test_random_graphs_final_plans_match_networkx(db):
    """Whatever solver answers, the plan equals the exact optimum and really cuts every route."""
    outcomes = {"cut": 0, "no_finite_cut": 0, "no_path": 0}
    solvers: dict[str, int] = {}
    for seed in SEEDS:
        net = random_case(seed)
        want = solve_networkx(net).value
        try:
            plan = freeze_plan(db, net, f"test_cut_{seed}")
        except NoPath:
            assert not reaches_exit(net), f"seed {seed}"
            outcomes["no_path"] += 1
            continue
        except NoFiniteCut:
            assert want >= net.inf, f"seed {seed}"
            outcomes["no_finite_cut"] += 1
            continue
        assert want < net.inf, f"seed {seed}"
        assert plan.cost == want, f"seed {seed}: cost {plan.cost} != optimum {want}"
        assert not reaches_exit(net, plan.frozen), f"seed {seed}: frozen set leaves a route"
        assert net.source not in plan.frozen and not (set(net.exits) & plan.frozen)
        outcomes["cut"] += 1
        solvers[plan.solver] = solvers.get(plan.solver, 0) + 1
    assert all(v >= 5 for v in outcomes.values()), outcomes
    assert not solvers.get("networkx-emergency"), solvers
    print("outcomes", outcomes, "solvers", solvers)


def test_certificate_has_zero_false_accepts(db):
    """The property that makes the hybrid safe: a certified FalkorDB value is always optimal."""
    accepted = rejected_wrong = rejected_right = 0
    for seed in SEEDS:
        net = random_case(seed)
        if not reaches_exit(net):
            continue
        want = solve_networkx(net).value
        got = run_falkordb_maxflow(db, net, f"test_cert_{seed}")
        if got is None:
            continue
        if certify(net, got):
            assert got.value == want, f"seed {seed}: FALSE ACCEPT {got.value} != {want}"
            accepted += 1
        elif got.value == want:
            rejected_right += 1
        else:
            rejected_wrong += 1
    # The certificate must actually be exercised in both directions.
    assert accepted >= 40 and rejected_wrong >= 5, (accepted, rejected_wrong, rejected_right)
    print("accepted", accepted, "rejected-wrong", rejected_wrong, "rejected-right", rejected_right)


# The 11-edge network from docs/falkordb-maxflow-findings.md. True max flow is 453; FalkorDB
# 4.22.0 and 6.0.2 return 300. If this ever starts passing, FalkorDB fixed the bug (strict xfail).
REPRO_EDGES = {
    ("a12:in", "a12:out"): 261, ("a12:out", "x1:in"): 3322, ("a15:in", "a15:out"): 158,
    ("a15:out", "a20:in"): 3322, ("a15:out", "a3:in"): 3322, ("a20:in", "a20:out"): 300,
    ("a20:out", "x1:in"): 3322, ("a3:in", "a3:out"): 153, ("a3:out", "a12:in"): 3322,
    ("src:out", "a15:in"): 3322, ("src:out", "a20:in"): 3322,
}


def _repro_network():
    from chokepoint.algo.cut import SplitNetwork

    costs = {"a12": 261, "a15": 158, "a20": 300, "a3": 153}
    return SplitNetwork(source="src", exits=("x1",), costs=costs, edges=dict(REPRO_EDGES),
                        inf=3322, accounts=("a12", "a15", "a20", "a3", "src", "x1"))


@pytest.mark.xfail(strict=True, reason="FalkorDB algo.maxFlow returns 300, true maximum is 453")
def test_falkordb_maxflow_bug_repro(db):
    net = _repro_network()
    got = run_falkordb_maxflow(db, net, "test_repro")
    assert got is not None and got.value == 453


def test_repro_is_caught_by_the_certificate_and_solved_exactly(db):
    net = _repro_network()
    assert solve_networkx(net).value == 453
    raw = run_falkordb_maxflow(db, net, "test_repro2")
    if raw is not None and raw.value == 300:  # the known-bad answer must never be certified
        assert not certify(net, raw)
    plan = freeze_plan(db, net, "test_repro3")
    assert plan.cost == 453
    assert not reaches_exit(net, plan.frozen)


def test_certify_rejects_bad_flows():
    net = build_split_network({"A": 5, "B": 7}, [("S", "A"), ("S", "B"), ("A", "X"), ("B", "X")],
                              "S", ["X"])
    good = solve_networkx(net)
    assert certify(net, good)
    # non-maximal: half the flow
    half = FlowResult(good.value - 1, dict(good.flows), "falkordb")
    assert not certify(net, half)
    # flow above capacity
    over = FlowResult(good.value, {k: v + 1 for k, v in good.flows.items()}, "falkordb")
    assert not certify(net, over)
    # empty flow on a connected network is not maximal
    assert not certify(net, FlowResult(0, {}, "falkordb"))


NEW_SEEDS = range(1000, 1450)  # a new batch, labelled as such: not the seeds above and not the held-out evaluation


@pytest.mark.timeout(900)
def test_in_database_repair_matches_networkx_on_300_plus_new_networks(db):
    """Value and cut cost equal the exact oracle on every network, and NetworkX is never needed to get there."""
    from chokepoint.algo.cut import solve

    labels: dict[str, int] = {}
    for seed in NEW_SEEDS:
        net = random_case(seed)
        if not reaches_exit(net):
            continue
        got = solve(db, net, f"test_repair_{seed}")
        labels[got.solver] = labels.get(got.solver, 0) + 1
        assert got.value == solve_networkx(net).value, f"seed {seed}: {got.solver} gave {got.value}"
        assert certify(net, got), f"seed {seed}"
    assert sum(labels.values()) >= 300, labels
    assert not labels.get("networkx-emergency"), labels
    assert labels.get("falkordb+repair", 0) >= 5 and labels.get("falkordb", 0) >= 40, labels  # both paths are exercised
    print("solver labels on 300+ new networks:", labels)


def test_the_repro_is_repaired_in_the_database(db):
    from chokepoint.algo.cut import solve

    net = _repro_network()
    raw = run_falkordb_maxflow(db, net, "test_repair_raw")
    if raw is not None and raw.value == 453:
        pytest.skip("FalkorDB now answers the repro correctly (the strict xfail above will say so)")
    assert raw is None or not certify(net, raw)
    got = solve(db, net, "test_repair_repro")
    assert (got.value, got.solver) == (453, "falkordb+repair") and certify(net, got)
