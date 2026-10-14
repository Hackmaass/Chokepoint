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
