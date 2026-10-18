"""Minimum-cost vertex cut by node splitting, with FalkorDB ``algo.maxFlow`` as a certified first pass.

A vertex cut ("freeze these accounts") needs capacity on the account, so each account ``v`` becomes
``v:in -> v:out`` with capacity equal to its freeze cost. A transfer ``u -> v`` becomes
``u:out -> v:in`` with capacity INF. The alerted account (source) and every exit are terminal:
the source is ``src:out`` and each exit is the sink ``x:in``; neither can be frozen.

FalkorDB returns a max-flow value and edge flows, not a cut, and on some node-split networks its
flow is not maximal and violates conservation (see ``docs/falkordb-maxflow-findings.md``). So a
FalkorDB result is accepted only with a certificate (feasible flow, and no sink reachable in the
residual graph, which by max-flow/min-cut duality proves optimality). Anything else (error,
timeout, empty result, failed certificate) is repaired inside FalkorDB (``residual.py``: Edmonds-Karp on a
residual scratch graph with ``shortestPath``) and certified again; NetworkX is only the test oracle and an
emergency path, labelled "networkx-emergency", for when the in-database repair itself fails.

All capacities are integers. INF = sum of finite capacities + 1 (a huge constant would break
FalkorDB's capacity-range rule ``max < min * 2^32``).
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Literal

import networkx as nx

from .residual import repair_in_db

log = logging.getLogger("chokepoint.cut")
IN, OUT = ":in", ":out"
MAX_CAP_RATIO = 2**32
SINK = "__SINK__"

Solver = Literal["falkordb", "falkordb+repair", "networkx-fallback", "networkx-emergency"]


class NoFiniteCut(Exception):
    """Some route from the source to an exit avoids every freezable account."""

    code = "NO_FINITE_CUT"


class NoPath(Exception):
    """The source cannot reach any exit at all."""

    code = "NO_PATH"


class CapacityRangeError(Exception):
    code = "CAPACITY_RANGE"


@dataclass(frozen=True)
class SplitNetwork:
    """Node-split network. ``edges`` maps (tail_node, head_node) -> capacity."""

    source: str
    exits: tuple[str, ...]
    costs: dict[str, int]
    edges: dict[tuple[str, str], int]
    inf: int
    accounts: tuple[str, ...] = field(default_factory=tuple)

    @property
    def source_node(self) -> str:
        return self.source + OUT

    @property
    def sink_nodes(self) -> tuple[str, ...]:
        return tuple(e + IN for e in self.exits)


def build_split_network(
    costs: dict[str, int],
    transfers: Iterable[tuple[str, str]],
    source: str,
    exits: Iterable[str],
) -> SplitNetwork:
    """Build the node-split network.

    ``costs`` maps every freezable account to a positive integer freeze cost. ``transfers`` are
    (from, to) pairs; duplicates are merged (FalkorDB rejects multi-edges), self-transfers are
    dropped, and transfers out of an exit or into the source are dropped (money that reached an
    exit has left the system; the alerted account is where the money starts).
    """
    exits = tuple(dict.fromkeys(exits))
    if source in exits:
        raise ValueError("the alerted account cannot also be an exit")
    for a, c in costs.items():
        if not isinstance(c, int) or c <= 0:
            raise ValueError(f"freeze cost for {a!r} must be a positive integer, got {c!r}")
    special = {source, *exits}
    accounts = tuple(dict.fromkeys([*costs, source, *exits]))
    known = set(accounts)
    exit_set = set(exits)
    pairs = set()
    for u, v in transfers:
        if u == v or u in exit_set or v == source:
            continue
        if u not in known or v not in known:
            raise ValueError(f"transfer {u!r}->{v!r} references an account outside the case")
        pairs.add((u, v))
    inf = sum(costs[a] for a in costs if a not in special) + 1
    edges: dict[tuple[str, str], int] = {}
    for a in costs:
        if a not in special:
            edges[(a + IN, a + OUT)] = costs[a]
    for u, v in pairs:
        edges[(u + OUT, v + IN)] = inf
    return SplitNetwork(source=source, exits=exits, costs=dict(costs), edges=edges, inf=inf,
                        accounts=accounts)


def check_capacity_range(net: SplitNetwork) -> None:
    caps = [c for c in net.edges.values() if c > 0]
    if caps and max(caps) >= min(caps) * MAX_CAP_RATIO:
        raise CapacityRangeError(f"max/min capacity ratio {max(caps) / min(caps):.3g} >= 2^32")


def _account_of(node: str) -> str:
    return node[: -len(IN)] if node.endswith(IN) else node[: -len(OUT)]


def reaches_exit(net: SplitNetwork, frozen: Iterable[str] = ()) -> bool:
    """Plain reachability on the account graph with ``frozen`` accounts removed."""
    frozen = set(frozen)
    adj: dict[str, list[str]] = {}
    for t, h in net.edges:
        if t.endswith(OUT) and h.endswith(IN):
            adj.setdefault(_account_of(t), []).append(_account_of(h))
    seen = {net.source}
    queue = deque([net.source])
    exits = set(net.exits)
    while queue:
        u = queue.popleft()
        for v in adj.get(u, ()):
            if v in frozen or v in seen:
                continue
            if v in exits:
                return True
            seen.add(v)
            queue.append(v)
    return False


@dataclass(frozen=True)
class FlowResult:
    value: int
    flows: dict[tuple[str, str], int]  # positive-flow edges only
    solver: Solver = "falkordb"
    reach: frozenset[str] | None = None  # residual-reachable nodes, when the database computed them (repair path)


# ---------------------------------------------------------------------------
# Exact solver (NetworkX): oracle in tests and fallback in the product.
# ---------------------------------------------------------------------------


def solve_networkx(net: SplitNetwork) -> FlowResult:
    g = nx.DiGraph()
    for (t, h), c in net.edges.items():
        g.add_edge(t, h, capacity=c)
    if net.source_node not in g:
        return FlowResult(0, {}, "networkx-fallback")
    for s in net.sink_nodes:
        if s in g:
            g.add_edge(s, SINK)  # no capacity attribute = infinite
    if SINK not in g:
        return FlowResult(0, {}, "networkx-fallback")
    value, flow = nx.maximum_flow(g, net.source_node, SINK)
    flows = {
        (t, h): int(f)
        for t, nbrs in flow.items()
        for h, f in nbrs.items()
        if f > 0 and h != SINK and (t, h) in net.edges
    }
    return FlowResult(int(value), flows, "networkx-fallback")


# ---------------------------------------------------------------------------
# FalkorDB solver and certificate
# ---------------------------------------------------------------------------


def _load_network(graph, net: SplitNetwork) -> None:
    nodes = sorted({n for e in net.edges for n in e})
    graph.query("CREATE INDEX FOR (n:N) ON (n.id)")
    graph.query("UNWIND $ids AS i CREATE (:N {id: i})", params={"ids": nodes})
    rows = [{"t": t, "h": h, "cap": c} for (t, h), c in net.edges.items()]
    graph.query(
        "UNWIND $rows AS r MATCH (a:N {id: r.t}), (b:N {id: r.h}) "
        "CREATE (a)-[:E {cap: r.cap}]->(b)",
        params={"rows": rows},
    )


_MAXFLOW_QUERY = """
MATCH (s:N {id: $source})
MATCH (t:N) WHERE t.id IN $sinks
WITH s, collect(t) AS ts
CALL algo.maxFlow({sourceNodes: [s], targetNodes: ts, relationshipTypes: ['E'],
                   capacityProperty: 'cap', nodeLabels: ['N']})
YIELD edges, edgeFlows, maxFlow
RETURN maxFlow, [e IN edges | [startNode(e).id, endNode(e).id]] AS ends, edgeFlows
"""


def run_falkordb_maxflow(db, net: SplitNetwork, graph_name: str) -> FlowResult | None:
    """Run ``algo.maxFlow`` on a disposable scratch graph; the graph is always deleted.

    Returns None when FalkorDB gives no usable answer (error, timeout, empty result).
    """
    check_capacity_range(net)
    try:
        db.select_graph(graph_name).delete()
    except Exception:
        pass
    graph = db.select_graph(graph_name)
    try:
        _load_network(graph, net)
        res = graph.ro_query(
            _MAXFLOW_QUERY, params={"source": net.source_node, "sinks": list(net.sink_nodes)}
        )
        if not res.result_set:
            return None
        value, ends, flows = res.result_set[0]
        flow_map = {(t, h): int(round(f)) for (t, h), f in zip(ends, flows) if round(f) > 0}
        return FlowResult(int(round(value)), flow_map, "falkordb")
    except Exception:
        return None
    finally:
        try:
            graph.delete()
        except Exception:
            pass


def _residual_reach(net: SplitNetwork, flows: dict[tuple[str, str], int]) -> set[str]:
    residual: dict[str, list[str]] = {}
    for (t, h), cap in net.edges.items():
        f = flows.get((t, h), 0)
        if cap - f > 0:
            residual.setdefault(t, []).append(h)
        if f > 0:
            residual.setdefault(h, []).append(t)
    reach = {net.source_node}
    queue = deque([net.source_node])
    while queue:
        u = queue.popleft()
        for v in residual.get(u, ()):
            if v not in reach:
                reach.add(v)
                queue.append(v)
    return reach


def is_feasible(net: SplitNetwork, result: FlowResult) -> bool:
    """Every flow edge exists, 0 < flow <= capacity, flow is conserved at every node other than the source and
    the sinks, and the value equals the net flow into the sinks and out of the source."""
    sinks = set(net.sink_nodes)
    inflow: dict[str, int] = {}
    outflow: dict[str, int] = {}
    for (t, h), f in result.flows.items():
        cap = net.edges.get((t, h))
        if cap is None or f <= 0 or f > cap:
            return False
        outflow[t] = outflow.get(t, 0) + f
        inflow[h] = inflow.get(h, 0) + f
    for n in set(inflow) | set(outflow):
        if n == net.source_node or n in sinks:
            continue
        if inflow.get(n, 0) != outflow.get(n, 0):
            return False
    if outflow.get(net.source_node, 0) - inflow.get(net.source_node, 0) != result.value:
        return False
    into_sinks = sum(inflow.get(s, 0) - outflow.get(s, 0) for s in sinks)
    return into_sinks == result.value


def certify(net: SplitNetwork, result: FlowResult) -> bool:
    """True iff ``result`` is a feasible flow whose value is provably the maximum: feasible (is_feasible), and no
    sink is reachable from the source in the residual graph (max-flow/min-cut duality)."""
    return is_feasible(net, result) and not (_residual_reach(net, result.flows) & set(net.sink_nodes))


@dataclass(frozen=True)
class FlowNetwork:
    """A plain capacitated network (no node splitting), e.g. money moved between accounts in cents."""

    source_node: str
    sink_nodes: tuple[str, ...]
    edges: dict[tuple[str, str], int]


def solve(db, net: SplitNetwork | FlowNetwork, graph_name: str) -> FlowResult:
    """FalkorDB first, accepted only with a certificate. If the certificate fails (or FalkorDB gives no answer, or the
    capacity range is one it cannot take) the flow is repaired inside FalkorDB (residual graph + shortestPath), starting
    from FalkorDB's flow when that flow is at least feasible, from nothing otherwise, and certified again. NetworkX runs
    only if the in-database repair itself fails, and is labelled "networkx-emergency" so it is counted and shown.
    """
    try:
        got = run_falkordb_maxflow(db, net, graph_name) if db is not None else None
    except CapacityRangeError:
        got = None
    if got is not None and certify(net, got):
        return got
    if db is None:
        return solve_networkx(net)
    try:
        fixed = repair_in_db(db, net, got.flows if got is not None and is_feasible(net, got) else {}, graph_name + "_res")
        if certify(net, fixed) and _residual_reach(net, fixed.flows) == fixed.reach:
            return fixed
    except Exception:
        log.exception("in-database repair failed on %s; using the exact solver", graph_name)
    return replace(solve_networkx(net), solver="networkx-emergency")


def extract_cut(net: SplitNetwork, result: FlowResult) -> frozenset[str]:
    """Client-side cut extraction: split edges leaving the residual-reachable set."""
    reach = result.reach if result.reach is not None else _residual_reach(net, result.flows)
    special = {net.source, *net.exits}
    return frozenset(
        a for a in net.accounts
        if a not in special and a + IN in reach and a + OUT not in reach
    )


@dataclass(frozen=True)
class FreezePlan:
    frozen: frozenset[str]
    cost: int
    max_flow: int
    solver: Solver


def freeze_plan(db, net: SplitNetwork, graph_name: str) -> FreezePlan:
    """Minimum-cost set of accounts whose removal disconnects the source from every exit.

    Raises NoPath if no route exists, NoFiniteCut if a route avoids every freezable account.
    """
    if not reaches_exit(net):
        raise NoPath("the alerted account cannot reach any exit")
    result = solve(db, net, graph_name)
    if result.value >= net.inf:
        raise NoFiniteCut("a route to an exit avoids every freezable account")
    frozen = extract_cut(net, result)
    cost = sum(net.costs[a] for a in frozen)
    if cost != result.value:
        raise AssertionError(f"cut cost {cost} != max-flow {result.value}")
    return FreezePlan(frozen=frozen, cost=cost, max_flow=result.value, solver=result.solver)
