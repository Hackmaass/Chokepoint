"""Case-region extraction from the tenant graph with fixed, parameterised Cypher (the LLM never writes Cypher).

A case region is everything the money from the alerted account could have reached within ``hops``
hops, using only transfers in ``[t - window, t]`` and in data versions up to ``data_version``.
Accounts first reached at exactly ``hops`` hops form the perimeter (the exits).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fx import to_usd_cents

RESULT_CAP = 1_000_000  # must stay below the server's RESULTSET_SIZE; hitting it means truncation
QUERY_TIMEOUT_MS = 30_000

# Q4: one hop of forward expansion, aggregated per (from, to). Uses the Account.id and TRANSFER.ts indexes.
FRONTIER_QUERY = """
MATCH (a:Account)-[t:TRANSFER]->(b:Account)
WHERE a.id IN $frontier AND t.ts >= $t_from AND t.ts <= $t_to AND t.batch <= $data_version
RETURN a.id AS s, b.id AS d, t.cur AS cur, sum(t.amt) AS amt, count(t) AS n, max(t.ts) AS last
"""


# Q5: every transfer between region accounts in the window, with its time (for the time-order check).
TRANSFER_TIMES_QUERY = """
MATCH (a:Account)-[t:TRANSFER]->(b:Account)
WHERE a.id IN $ids AND b.id IN $ids AND t.ts >= $t_from AND t.ts <= $t_to AND t.batch <= $data_version
RETURN a.id AS s, b.id AS d, t.ts AS ts
"""


# Q6: one hop of expansion on the source graph that never steps onto an excluded (frozen) account. Used by the second,
# independent check after a freeze: it does not use the scratch graph, the copy or the region built for the plan.
RECHECK_QUERY = """
MATCH (a:Account)-[t:TRANSFER]->(b:Account)
WHERE a.id IN $frontier AND NOT b.id IN $excluded AND t.ts >= $t_from AND t.ts <= $t_to AND t.batch <= $data_version
RETURN DISTINCT b.id AS id
"""


class CaseTooLarge(Exception):
    code = "CASE_TOO_LARGE"


class UnknownAccount(Exception):
    code = "UNKNOWN_ACCOUNT"


class TruncatedResult(Exception):
    code = "TRUNCATED_RESULT"


@dataclass
class Region:
    origin: str
    t_to: int
    t_from: int
    hops: int
    data_version: int
    depth: dict[str, int] = field(default_factory=dict)
    # (from, to) -> (transfer count, last timestamp, total moved in USD cents)
    edges: dict[tuple[str, str], tuple[int, int, int]] = field(default_factory=dict)
    queries: int = 0

    @property
    def perimeter(self) -> list[str]:
        return sorted(a for a, d in self.depth.items() if d == self.hops)

    @property
    def hop1(self) -> list[str]:
        return sorted(a for a, d in self.depth.items() if d == 1)


def account_exists(graph, account: str) -> bool:
    res = graph.ro_query("MATCH (a:Account {id: $id}) RETURN count(a)", params={"id": account})
    return res.result_set[0][0] > 0


def extract_region(
    graph,
    origin: str,
    t_to: int,
    window_minutes: int,
    hops: int = 3,
    data_version: int = 0,
    max_nodes: int = 4000,
) -> Region:
    """Forward, hop-limited, time-windowed expansion from ``origin`` (static: edge times are not ordered)."""
    if not account_exists(graph, origin):
        raise UnknownAccount(origin)
    region = Region(origin=origin, t_to=t_to, t_from=t_to - window_minutes, hops=hops, data_version=data_version)
    region.depth[origin] = 0
    frontier = [origin]
    for hop in range(hops):
        if not frontier:
            break
        res = graph.ro_query(
            FRONTIER_QUERY,
            params={"frontier": frontier, "t_from": region.t_from, "t_to": t_to, "data_version": data_version},
            timeout=QUERY_TIMEOUT_MS,
        )
        region.queries += 1
        rows = res.result_set
        if len(rows) >= RESULT_CAP:
            raise TruncatedResult(f"hop {hop + 1} returned {len(rows)} rows (cap {RESULT_CAP})")
        new: list[str] = []
        for s, d, cur, amt, n, last in rows:
            if s == d:
                continue
            n0, last0, usd0 = region.edges.get((s, d), (0, 0, 0))
            region.edges[(s, d)] = (n0 + int(n), max(last0, int(last)), usd0 + to_usd_cents(int(amt), cur))
            if d not in region.depth:
                region.depth[d] = hop + 1
                new.append(d)
        frontier = new
        if len(region.depth) > max_nodes:
            raise CaseTooLarge(f"{len(region.depth)} accounts after {hop + 1} hops (cap {max_nodes})")
    return region


def transfer_times(graph, region: Region, pairs) -> list[tuple[str, str, int]]:
    """(from, to, time) of every transfer in the region whose (from, to) is one of ``pairs``, oldest first."""
    rows = graph.ro_query(
        TRANSFER_TIMES_QUERY, timeout=QUERY_TIMEOUT_MS,
        params={"ids": list(region.depth), "t_from": region.t_from, "t_to": region.t_to,
                "data_version": region.data_version}).result_set
    if len(rows) >= RESULT_CAP:
        raise TruncatedResult(f"transfer times returned {len(rows)} rows (cap {RESULT_CAP})")
    return sorted(((s, d, int(ts)) for s, d, ts in rows if (s, d) in pairs), key=lambda x: x[2])


def time_ordered_pairs(transfers: list[tuple[str, str, int]], origin: str, t_from: int,
                       removed: frozenset[str] = frozenset()) -> set[tuple[str, str]]:
    """(from, to) pairs that lie on a route whose transfers happen in non-decreasing time, starting at ``origin`` any time
    from ``t_from``. ``transfers`` must be sorted by time. Hop count is ignored (a superset of the hop-limited routes),
    so a cut of these pairs also cuts every real route. Accounts in ``removed`` take no part."""
    arrived = {origin: t_from}  # earliest time money could be at the account
    changed = True
    while changed:  # repeat so same-minute chains settle
        changed = False
        for s, d, ts in transfers:
            if s in arrived and ts >= arrived[s] and s not in removed and d not in removed and ts < arrived.get(d, ts + 1):
                arrived[d] = ts
                changed = True
    return {(s, d) for s, d, ts in transfers if s in arrived and ts >= arrived[s] and s not in removed and d not in removed}


def reachable_targets(graph, origin: str, t_from: int, t_to: int, hops: int, data_version: int,
                      excluded: list[str], targets: set[str]) -> set[str]:
    """Which of ``targets`` can the money reach from ``origin`` within ``hops`` hops, never passing through ``excluded``?
    A fresh expansion on the source graph (the same fixed query shape, a different code path from the region the plan
    was built on), so a mistake in building that region would show up as a disagreement."""
    seen = {origin}
    frontier = [origin]
    for _ in range(hops):
        if not frontier:
            break
        rows = graph.ro_query(RECHECK_QUERY, timeout=QUERY_TIMEOUT_MS,
                              params={"frontier": frontier, "excluded": excluded, "t_from": t_from, "t_to": t_to,
                                      "data_version": data_version}).result_set
        if len(rows) >= RESULT_CAP:
            raise TruncatedResult(f"recheck hop returned {len(rows)} rows (cap {RESULT_CAP})")
        frontier = [r[0] for r in rows if r[0] not in seen]
        seen.update(frontier)
    return seen & targets
