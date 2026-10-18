"""The agent's tools. Every tool is a fixed, parameterised operation: the LLM chooses tools and arguments,
never Cypher, and never produces a number. Results are plain JSON-serialisable dicts; every result that
matters is stored in the tenant graph as a Finding with a receipt (ToolCall, parameters, data version,
solver, accounts read). Typed errors are returned as ``{"error": {...}}`` by ``Toolbox.call``.

Scenario: at alert time the alerted account is already frozen, so it is the source and is never freezable.
The perimeter is the set of accounts first reached at exactly ``perimeter_hops`` hops in the pre-alert graph.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from calendar import timegm
from collections import defaultdict
from datetime import datetime, timezone

import networkx as nx
from redis.exceptions import ResponseError

from .algo.cut import (
    FlowNetwork,
    NoFiniteCut,
    NoPath,
    build_split_network,
    reaches_exit,
    solve,
)
from .algo.cut import (
    freeze_plan as solve_freeze_plan,
)
from .casegraph import (
    CaseTooLarge,
    Region,
    TruncatedResult,
    UnknownAccount,
    extract_region,
    time_ordered_pairs,
    transfer_times,
)

DAY = 1440
DEFAULT_WINDOW_DAYS = 7
MAX_ACTIONABLE_ACCOUNTS = 25  # a plan larger than this is flagged, not presented as a recommendation
MAX_CASE_NODES = 4000
HEAVY_TIMEOUT_MS = 30_000  # the server default (3 s) is short on purpose; known-heavy queries ask for more


NO_ROUTE_NOTE = ("no route from the alerted account to the perimeter in this window; widen the window or the "
                 "perimeter")


class ToolError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def as_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "hint": self.hint}}


def parse_ts(value: str) -> int:
    """'2022-09-03T10:15' or '2022-09-03 10:15:00' (UTC, naive) -> epoch minutes."""
    try:
        dt = datetime.fromisoformat(str(value).strip().replace(" ", "T"))
    except ValueError as e:
        raise ToolError("BAD_ARGUMENT", f"alert_ts {value!r} is not an ISO date-time",
                        "use YYYY-MM-DDTHH:MM, e.g. 2022-09-03T10:15") from e
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return timegm(dt.timetuple()) // 60


def fmt_ts(minutes: int) -> str:
    return datetime.fromtimestamp(minutes * 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _short(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _det(prefix: str, *parts) -> str:
    """Deterministic id: the same inputs always give the same id, so tools are idempotent and a recorded
    agent run can be replayed against a database holding the same data."""
    return f"{prefix}-" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8]


BASE_QUERY = """
MATCH (a:Account)-[t:TRANSFER]-()
WHERE a.id IN $ids AND t.ts <= $t_to AND t.batch <= $dv
RETURN a.id AS id, count(t) AS n
"""


class Toolbox:
    def __init__(self, db, tenant: str = "ibm", salt: str = ""):
        self.salt = salt  # evaluation only: gives a twin case with identical content but different ids
        self.db = db
        self.tenant = tenant
        self.g = db.select_graph(f"tenant_{tenant}")
        self._ensure_schema()

    # ------------------------------------------------------------------ infrastructure
    def _ensure_schema(self) -> None:
        for label in ("Case", "ToolCall", "Finding", "FreezePlan", "Batch"):
            try:
                self.g.query(f"CREATE INDEX FOR (n:{label}) ON (n.id)")
            except Exception:
                pass  # already exists
        if not self.g.ro_query("MATCH (b:Batch) RETURN count(b)").result_set[0][0]:
            self.g.query("CREATE (:Batch {id: 0, loaded_at: $now})", params={"now": int(time.time())})

    def latest_batch(self) -> int:
        return int(self.g.ro_query("MATCH (b:Batch) RETURN max(b.id)").result_set[0][0] or 0)

    def _write(self, query: str, params: dict) -> None:
        self.g.query(query, params=params, timeout=HEAVY_TIMEOUT_MS)

    def call(self, tool: str, args: dict) -> dict:
        fn = getattr(self, f"tool_{tool}", None)
        if fn is None:
            return ToolError("BAD_ARGUMENT", f"unknown tool {tool!r}").as_dict()
        try:
            return fn(**args)
        except ToolError as e:
            return e.as_dict()
        except TypeError as e:
            return ToolError("BAD_ARGUMENT", str(e)).as_dict()
        except UnknownAccount as e:
            return ToolError("UNKNOWN_ACCOUNT", f"account {e} not found", "pick one of the example alerts; a small public demo holds only their neighbourhoods (the full dataset is in docs/deploy.md)").as_dict()
        except CaseTooLarge as e:
            return ToolError(
                "CASE_TOO_LARGE", str(e),
                "the smallest settings are window_days=1 and perimeter_hops=2; if those are still too large this "
                "account is a high-volume hub that cannot be analysed here, so stop and say so").as_dict()
        except TruncatedResult as e:
            return ToolError("TRUNCATED_RESULT", str(e)).as_dict()
        except NoPath as e:
            return ToolError("NO_PATH", str(e), "widen the window or the perimeter and try again").as_dict()
        except NoFiniteCut as e:
            return ToolError("NO_FINITE_CUT", str(e)).as_dict()
        except ResponseError as e:  # the database refused or timed out: report it, do not crash the run
            if "timed out" in str(e).lower():
                return ToolError("TIMEOUT", "the database took too long", "try a smaller window or fewer hops").as_dict()
            return ToolError("DATABASE_ERROR", str(e)[:200]).as_dict()

    # ------------------------------------------------------------------ case helpers
    def _case(self, case_id: str) -> dict:
        res = self.g.ro_query("MATCH (c:Case {id: $id}) RETURN c", params={"id": case_id}).result_set
        if not res:
            raise ToolError("UNKNOWN_CASE", f"case {case_id!r} not found", "call open_case first")
        p = res[0][0].properties
        return dict(p)

    def _region(self, case: dict, hops: int | None = None) -> Region:
        return extract_region(
            self.g, case["origin"], int(case["alert_ts"]), int(case["window_min"]),
            hops or int(case["hops"]), int(case["data_version"]), MAX_CASE_NODES,
        )

    def _network_parts(self, region: Region):
        """Selected accounts, transfers (from region edges), perimeter and freezable accounts."""
        K = region.hops
        depth = region.depth
        perimeter = {a for a, d in depth.items() if d == K}
        trans = {(s, d): v for (s, d), v in region.edges.items()
                 if depth.get(s, K) < K and d != region.origin and d in depth}
        freezable = [a for a in depth if a != region.origin and a not in perimeter]
        return perimeter, trans, freezable

    def _base_activity(self, region: Region, trans: dict, freezable: list[str]) -> dict[str, int]:
        if not freezable:
            return {}
        rows = self.g.ro_query(BASE_QUERY, params={"ids": freezable, "t_to": region.t_to, "dv": region.data_version},
                               timeout=HEAVY_TIMEOUT_MS).result_set
        total = {a: int(n) for a, n in rows}
        inside: dict[str, int] = defaultdict(int)
        for (s, d), (n, _last, _usd) in trans.items():
            inside[s] += n
            inside[d] += n
        return {a: max(0, total.get(a, 0) - inside.get(a, 0)) for a in freezable}

    def _money_flow(self, region: Region, trans: dict, perimeter: set[str], removed: set[str], tag: str):
        edges = {(s, d): usd for (s, d), (_n, _l, usd) in trans.items()
                 if usd > 0 and s not in removed and d not in removed}
        sinks = tuple(sorted({d for (_, d) in edges if d in perimeter}))
        if not sinks or region.origin not in {s for (s, _) in edges}:
            return 0, {}, "none", edges
        net = FlowNetwork(source_node=region.origin, sink_nodes=sinks, edges=edges)
        res = solve(self.db, net, f"flow_{tag}_{uuid.uuid4().hex[:6]}")
        return res.value, res.flows, res.solver, edges

    # ------------------------------------------------------------------ provenance
    def _cached(self, case_id: str, key: str, dv: int):
        """A finding already recorded for this case, key and data version: (finding_id, tool_call_id, value)."""
        rows = self.g.ro_query(
            "MATCH (:Case {id: $c})-[:HAS]->(f:Finding {key: $k}) WHERE (f.data_version = $dv OR f.checked_dv = $dv) AND coalesce(f.complete, true) "
            "MATCH (tc:ToolCall)-[:PRODUCED]->(f) RETURN f.id, tc.id, f.value ORDER BY f.version DESC LIMIT 1",
            params={"c": case_id, "k": key, "dv": dv}).result_set
        return (rows[0][0], rows[0][1], json.loads(rows[0][2])) if rows else None

    def _record(self, case_id: str, tool: str, params: dict, region: Region, solver: str, kind: str,
                value: dict, started: float, scope: list[str], key: str = "", salt: str = "",
                complete: bool = True) -> tuple[str, str]:
        """Write a ToolCall and its Finding. With ``complete=False`` the caller finishes extra writes (a plan node)
        and then calls ``_complete``; until then the finding is invisible to ``_cached`` and is cleaned up and redone
        by the next identical call, so a crash mid-write can never leave a half-recorded result behind."""
        key = key or kind
        tc_id = _det("tc", case_id, key, region.data_version, salt)
        f_id = _det("f", case_id, key, region.data_version, salt)
        row = self.g.ro_query("MATCH (f:Finding {id: $f}) RETURN coalesce(f.complete, true)", params={"f": f_id}).result_set
        if row and row[0][0]:
            return tc_id, f_id  # identical result already recorded: idempotent
        if row:  # a partial write from an earlier crash: remove it and start again
            self._write("MATCH (f:Finding {id: $f}) OPTIONAL MATCH (tc:ToolCall)-[:PRODUCED]->(f) "
                        "OPTIONAL MATCH (f)-[:HAS_PLAN]->(p:FreezePlan) DETACH DELETE p, tc, f", {"f": f_id})
        version = int(self.g.ro_query(
            "MATCH (:Case {id: $c})-[:HAS]->(f:Finding {kind: $k}) RETURN count(f)",
            params={"c": case_id, "k": kind}).result_set[0][0]) + 1
        self._write(
            "MATCH (c:Case {id: $case_id}) "
            "CREATE (tc:ToolCall {id: $tc, tool: $tool, params: $params, data_version: $dv, solver: $solver, "
            "ran_at: $now, duration_ms: $ms}) "
            "CREATE (tc)-[:RUN_IN]->(c) "
            "CREATE (f:Finding {id: $f, kind: $kind, key: $key, value: $value, status: 'current', version: $ver, "
            "data_version: $dv, complete: $complete}) "
            "CREATE (tc)-[:PRODUCED]->(f) CREATE (c)-[:HAS]->(f)",
            {"case_id": case_id, "tc": tc_id, "tool": tool, "params": json.dumps(params, sort_keys=True),
             "dv": region.data_version, "solver": solver, "now": int(time.time()),
             "ms": int((time.perf_counter() - started) * 1000), "f": f_id, "kind": kind, "key": key, "complete": False,
             "value": json.dumps(value, sort_keys=True), "ver": version},
        )
        for i in range(0, len(scope), 500):  # chunked: thousands of READ edges in one query can time out
            self._write("MATCH (tc:ToolCall {id: $tc}) UNWIND $scope AS acct "
                        "MATCH (a:Account {id: acct}) CREATE (tc)-[:READ]->(a)",
                        {"tc": tc_id, "scope": scope[i : i + 500]})
        if complete:
            self._complete(f_id)
        return tc_id, f_id

    def _complete(self, finding_id: str) -> None:
        self._write("MATCH (f:Finding {id: $f}) SET f.complete = true", {"f": finding_id})

    # ------------------------------------------------------------------ tools
    def _case_or_none(self, case_id: str) -> dict | None:
        res = self.g.ro_query("MATCH (c:Case {id: $id}) RETURN c", params={"id": case_id}).result_set
        return dict(res[0][0].properties) if res else None

    def _region_result(self, case_id: str, case: dict, region: Region, started: float) -> dict:
        perimeter, trans, _ = self._network_parts(region)
        plain = nx.DiGraph(list(trans))
        has_route = bool(perimeter) and region.origin in plain and any(
            p in plain and nx.has_path(plain, region.origin, p) for p in perimeter)
        value = {"accounts": len(region.depth), "transfer_pairs": len(trans), "first_hop": len(region.hop1),
                 "perimeter_accounts": len(perimeter), "has_route_to_perimeter": has_route}
        tc, f = self._record(
            case_id, "open_case",
            {"account": case["origin"], "alert_ts": fmt_ts(int(case["alert_ts"])),
             "window_days": int(case["window_min"]) // DAY, "perimeter_hops": int(case["hops"])},
            region, "falkordb-cypher", "region", value, started,
            [a for a, d in region.depth.items() if d < region.hops])  # scope: accounts whose out-edges were read
        return {"value": value, "finding_id": f, "tool_call_id": tc}

    def tool_open_case(self, account: str, alert_ts: str, window_days: int = DEFAULT_WINDOW_DAYS,
                       perimeter_hops: int = 3) -> dict:
        started = time.perf_counter()
        if not 2 <= int(perimeter_hops) <= 4:
            raise ToolError("BAD_ARGUMENT", "perimeter_hops must be 2, 3 or 4")
        if not 1 <= int(window_days) <= 14:
            raise ToolError("BAD_ARGUMENT", "window_days must be between 1 and 14")
        if not isinstance(account, str) or not 3 <= len(account) <= 40 or ":" not in account:
            raise ToolError("UNKNOWN_ACCOUNT", "account ids look like bank:account", "check the id")
        t = parse_ts(alert_ts)
        # the same account, time, window and perimeter is the same case in every data version
        case_id = _det("c", account, t, int(window_days), int(perimeter_hops), *([self.salt] if self.salt else []))
        case = self._case_or_none(case_id)
        dv = int(case["data_version"]) if case else self.latest_batch()
        head = {"case_id": case_id, "alerted_account": account, "alert_ts": fmt_ts(t), "data_version": dv,
                "window_days": int(window_days), "perimeter_hops": int(perimeter_hops)}
        if case:
            known = self._cached(case_id, "region", dv)
            if known:
                return {**head, **known[2], "already_open": True,
                        "note": "" if known[2]["has_route_to_perimeter"] else NO_ROUTE_NOTE}
        region = extract_region(self.g, account, t, int(window_days) * DAY, int(perimeter_hops), dv, MAX_CASE_NODES)
        if not case:
            self._write(
                "CREATE (:Case {id: $id, origin: $o, alert_ts: $t, window_min: $w, hops: $k, data_version: $dv, "
                "status: 'open', created_at: $now})",
                {"id": case_id, "o": account, "t": t, "w": int(window_days) * DAY, "k": int(perimeter_hops),
                 "dv": dv, "now": int(time.time())})
            case = self._case(case_id)
        r = self._region_result(case_id, case, region, started)
        return {**head, **r["value"], "note": "" if r["value"]["has_route_to_perimeter"] else NO_ROUTE_NOTE}

    def tool_flow(self, case_id: str) -> dict:
        started = time.perf_counter()
        case = self._case(case_id)
        known = self._cached(case_id, "flow", int(case["data_version"]))
        if known:
            return {**known[2], "finding_id": known[0], "tool_call_id": known[1], "already_computed": True}
        region = self._region(case)
        perimeter, trans, _ = self._network_parts(region)
        value, flows, solver, edges = self._money_flow(region, trans, perimeter, set(), "open")
        top = sorted(((s, d, f) for (s, d), f in flows.items()), key=lambda x: -x[2])[:10]
        result = {
            "case_id": case_id, "max_movable_usd_cents": int(value),
            "meaning": "upper bound on money that could move from the alerted account to the perimeter along "
                       "transfers seen in the window (static flow; ignores timing)",
            "accounts_carrying_flow": len({a for s, d, _ in top for a in (s, d)}),
            "top_flow_edges": [{"from": s, "to": d, "usd_cents": int(f)} for s, d, f in top],
            "solver": solver,
        }
        scope = [a for a, d in region.depth.items() if d < region.hops]
        tc, f = self._record(case_id, "flow", {"case_id": case_id}, region, solver, "flow", result, started, scope)
        return {**result, "finding_id": f, "tool_call_id": tc}

    def _rule_of_thumb(self, region: Region, trans: dict, freezable: list[str], k: int) -> list[str]:
        """What an analyst does without a graph: freeze the accounts the alerted account paid most recently."""
        deg: dict[str, int] = defaultdict(int)
        last: dict[str, int] = {}
        for (s, d), (_n, ts, _usd) in trans.items():
            deg[s] += 1
            deg[d] += 1
            if s == region.origin:
                last[d] = ts
        fz = set(freezable)
        recent = sorted((a for a in last if a in fz), key=lambda a: (-last[a], a))
        rest = sorted((a for a in freezable if a not in last), key=lambda a: (-deg.get(a, 0), a))
        return (recent + rest)[:k]

    def _time_ordered(self, region: Region, trans: dict, perimeter: set[str], costs: dict[str, int]) -> dict:
        """The same question with transfer times respected: money cannot go back in time, so a link that only works
        out of order is not a route. Card-only (kept out of the model's view, so saved agent runs stay valid) and
        checked in code on the recorded transfers, not on the graph copy."""
        times = transfer_times(self.g, region, trans)
        keep = time_ordered_pairs(times, region.origin, region.t_from)
        kept = {pair: v for pair, v in trans.items() if pair in keep}
        reachable = bool(perimeter) and any(d in perimeter for (_, d) in keep)
        out = {"transfer_pairs": len(trans), "time_ordered_pairs": len(keep), "has_time_ordered_route": reachable,
               "accounts": [], "count": 0, "routes_left": False, "money_movable_usd_cents": 0, "residual_movable_usd_cents": 0}
        if not reachable:
            return out
        net = build_split_network(costs, list(kept), region.origin, sorted(perimeter))
        frozen = sorted(solve_freeze_plan(self.db, net, f"tplan_{uuid.uuid4().hex[:6]}").frozen)
        left = time_ordered_pairs(times, region.origin, region.t_from, frozenset(frozen))
        out.update(
            accounts=frozen, count=len(frozen), routes_left=any(d in perimeter for (_, d) in left),
            money_movable_usd_cents=int(self._money_flow(region, kept, perimeter, set(), "t_before")[0]),
            residual_movable_usd_cents=int(self._money_flow(region, kept, perimeter, set(frozen), "t_after")[0]))
        return out

    def tool_freeze_plan(self, case_id: str, lambda_weight: float = 0.0, perimeter_hops: int | None = None) -> dict:
        started = time.perf_counter()
        case = self._case(case_id)
        hops = int(perimeter_hops) if perimeter_hops else int(case["hops"])
        if not 2 <= hops <= 4:
            raise ToolError("BAD_ARGUMENT", "perimeter_hops must be 2, 3 or 4")
        lam = float(lambda_weight)
        if not 0 <= lam <= 8:
            raise ToolError("BAD_ARGUMENT", "lambda_weight must be between 0 and 8")
        key = f"freeze_plan:{lam:g}:{hops}"
        plan_id = _det("p", case_id, lam, hops, case["data_version"])
        known = self._cached(case_id, key, int(case["data_version"]))
        if known:
            return {**known[2], "plan_id": plan_id, "finding_id": known[0], "tool_call_id": known[1],
                    "already_computed": True}
        region = self._region(case, hops)
        perimeter, trans, freezable = self._network_parts(region)
        base = self._base_activity(region, trans, freezable)
        maxb = max(base.values(), default=0) or 1
        costs = {a: 100 + round(lam * 100 * math.log1p(base[a]) / math.log1p(maxb)) for a in freezable}
        net = build_split_network(costs, list(trans), region.origin, sorted(perimeter))
        plan = solve_freeze_plan(self.db, net, f"plan_{uuid.uuid4().hex[:6]}")  # raises NoPath / NoFiniteCut
        frozen = sorted(plan.frozen)
        k = len(frozen)
        rule = self._rule_of_thumb(region, trans, freezable, k)

        # on a route from the alerted account to the perimeter?
        g = nx.DiGraph(list(trans))
        reach = nx.descendants(g, region.origin) | {region.origin}
        anc = set()
        for p in perimeter:
            if p in g:
                anc |= nx.ancestors(g, p) | {p}
        onroute = reach & anc
        # money each account received from the alerted account's downstream network in the window; an upper bound on
        # what a hold would have to cover (the dataset has no balances, so this is not a share of any balance)
        traced_in: dict[str, int] = defaultdict(int)
        for (s_, d_), (_n, _l, usd) in trans.items():
            if s_ in reach:
                traced_in[d_] += int(usd)

        before, _, _, _ = self._money_flow(region, trans, perimeter, set(), "before")
        after_cp, _, _, _ = self._money_flow(region, trans, perimeter, set(frozen), "cp")
        after_rule, _, _, _ = self._money_flow(region, trans, perimeter, set(rule), "rule")

        def describe(accts: list[str], residual: int) -> dict:
            return {
                "accounts": accts, "count": len(accts),
                "residual_movable_usd_cents": int(residual),
                "routes_left": reaches_exit(build_split_network(costs, list(trans), region.origin,
                                                                sorted(perimeter)), accts),
                "on_a_route_to_perimeter": sum(1 for a in accts if a in onroute),
                "baseline_activity_transfers": int(sum(base.get(a, 0) for a in accts)),
                "traced_in_usd_cents": {a: traced_in.get(a, 0) for a in accts},
            }

        time_ordered = self._time_ordered(region, trans, perimeter, costs)

        result = {
            "case_id": case_id, "lambda_weight": lam, "perimeter_hops": hops,
            "time_ordered": time_ordered,
            "money_movable_before_usd_cents": int(before),
            "chokepoint_plan": {**describe(frozen, after_cp), "cost": int(plan.cost), "solver": plan.solver},
            "rule_of_thumb_plan": {**describe(rule, after_rule),
                                   "rule": "freeze the accounts the alerted account paid most recently, same number of accounts"},
            "actionable": k <= MAX_ACTIONABLE_ACCOUNTS,
        }
        if not result["actionable"]:
            result["warning"] = (f"the cheapest cut needs {k} accounts, more than {MAX_ACTIONABLE_ACCOUNTS}; "
                                 "treat it as not actionable and try a smaller perimeter or a shorter window")
        scope = [a for a, d in region.depth.items() if d < hops]
        tc, f = self._record(case_id, "freeze_plan", {"case_id": case_id, "lambda_weight": lam, "perimeter_hops": hops},
                             region, plan.solver, "freeze_plan", result, started, scope, key=key, complete=False)
        self._write(
            "MATCH (f:Finding {id: $f}) "
            "CREATE (p:FreezePlan {id: $p, cost: $cost, lambda: $lam, size: $k, hops: $hops, verified: false}) "
            "CREATE (f)-[:HAS_PLAN]->(p)",
            {"f": f, "p": plan_id, "cost": int(plan.cost), "lam": lam, "k": k, "hops": hops})
        if frozen:
            self._write("MATCH (p:FreezePlan {id: $p}) UNWIND $fz AS acct MATCH (a:Account {id: acct}) "
                        "CREATE (p)-[:FREEZES]->(a)", {"p": plan_id, "fz": frozen})
        self._complete(f)
        return {**result, "plan_id": plan_id, "finding_id": f, "tool_call_id": tc}

    def _plan(self, plan_id: str) -> dict:
        res = self.g.ro_query(
            "MATCH (c:Case)-[:HAS]->(f:Finding)-[:HAS_PLAN]->(p:FreezePlan {id: $id}) "
            "OPTIONAL MATCH (p)-[:FREEZES]->(a:Account) RETURN c.id, p, f.id, collect(a.id)",
            params={"id": plan_id}).result_set
        if not res:
            raise ToolError("UNKNOWN_PLAN", f"plan {plan_id!r} not found", "call freeze_plan first")
        case_id, p, f_id, frozen = res[0]
        return {"case_id": case_id, "plan": dict(p.properties), "finding_id": f_id, "frozen": sorted(frozen)}

    def _copy_graph(self, scratch, copy_name: str) -> str:
        """GRAPH.COPY needs a fork, which the server refuses while a background save or rewrite is running.
        Retry with backoff; return "GRAPH.COPY" on success, or "rebuild" if the caller must build the copy itself."""
        for attempt in range(5):
            try:
                scratch.copy(copy_name)
                return "GRAPH.COPY"
            except Exception as e:  # redis ResponseError: "GRAPH.COPY failed, could not fork"
                if "fork" not in str(e).lower():
                    raise
                try:
                    self.db.select_graph(copy_name).delete()
                except Exception:
                    pass
                time.sleep(0.3 * 2**attempt)
        return "rebuild"

    def tool_verify_plan(self, plan_id: str) -> dict:
        """Independent check: rebuild the region from the database at the stored data version, delete the
        frozen accounts from a COPY of the scratch graph, and ask whether any route to the perimeter is left."""
        started = time.perf_counter()
        info = self._plan(plan_id)
        case = self._case(info["case_id"])
        known = self._cached(info["case_id"], f"verify:{plan_id}", int(case["data_version"]))
        if known:
            return {**known[2], "finding_id": known[0], "tool_call_id": known[1], "already_computed": True}
        hops = int(info["plan"]["hops"])
        region = self._region(case, hops)
        perimeter = sorted(a for a, d in region.depth.items() if d == hops)
        run = uuid.uuid4().hex[:6]
        base_name, copy_name = f"case_{info['case_id']}_run_{run}", f"case_{info['case_id']}_run_{run}_v"
        scratch = self.db.select_graph(base_name)
        try:
            scratch.query("CREATE INDEX FOR (a:A) ON (a.id)")
            scratch.query("UNWIND $ids AS i CREATE (:A {id: i})", params={"ids": list(region.depth)})
            scratch.query("UNWIND $rows AS r MATCH (a:A {id: r.s}), (b:A {id: r.d}) CREATE (a)-[:T]->(b)",
                          params={"rows": [{"s": s, "d": d} for (s, d) in region.edges]})
            q = ("MATCH (s:A {id: $o}) MATCH (t:A) WHERE t.id IN $perim "
                 "WITH t, shortestPath((s)-[:T*]->(t)) AS p RETURN t.id AS id, p IS NOT NULL AS reachable")
            before = scratch.ro_query(q, params={"o": region.origin, "perim": perimeter}).result_set
            copy_method = self._copy_graph(scratch, copy_name)
            copy = self.db.select_graph(copy_name)
            if copy_method == "GRAPH.COPY":
                copy.query("MATCH (a:A) WHERE a.id IN $f DELETE a", params={"f": info["frozen"]})
            else:  # the server could not fork: build the second graph without the frozen accounts instead
                frozen = set(info["frozen"])
                keep = [a for a in region.depth if a not in frozen]
                copy.query("CREATE INDEX FOR (a:A) ON (a.id)")
                copy.query("UNWIND $ids AS i CREATE (:A {id: i})", params={"ids": keep})
                copy.query("UNWIND $rows AS r MATCH (a:A {id: r.s}), (b:A {id: r.d}) CREATE (a)-[:T]->(b)",
                           params={"rows": [{"s": s_, "d": d_} for (s_, d_) in region.edges
                                            if s_ not in frozen and d_ not in frozen]})
            after = copy.ro_query(q, params={"o": region.origin, "perim": perimeter}).result_set
        finally:
            for n in (base_name, copy_name):
                try:
                    self.db.select_graph(n).delete()
                except Exception:
                    pass
        reachable_before = sum(1 for _, ok in before if ok)
        reachable_after = sum(1 for _, ok in after if ok)
        verified = reachable_after == 0 and reachable_before > 0
        self._write("MATCH (p:FreezePlan {id: $p}) SET p.verified = $v, p.verified_at = $now",
                    {"p": plan_id, "v": verified, "now": int(time.time())})
        result = {
            "plan_id": plan_id, "verified": verified, "perimeter_accounts": len(perimeter),
            "perimeter_reachable_before": reachable_before, "perimeter_reachable_after": reachable_after,
            "frozen_accounts": info["frozen"],
            "statement": (f"no route from the alerted account to the {hops}-hop perimeter is left after freezing "
                          f"{len(info['frozen'])} account(s)") if verified else
                         ("routes to the perimeter remain" if reachable_after else "no route existed before the freeze"),
            "method": "rebuilt the region from the database at the stored data version, " + (
                "deleted the frozen accounts from a copy of the graph (GRAPH.COPY), " if copy_method == "GRAPH.COPY"
                else "built a second graph without the frozen accounts, ") +
                      "ran shortestPath to every perimeter account "
                      "(a consistency check on the same data, not independent evidence about reality)",
        }
        tc, f = self._record(info["case_id"], "verify_plan", {"plan_id": plan_id}, region, "falkordb-shortestPath",
                             "verification", result, started, [], key=f"verify:{plan_id}")
        return {**result, "finding_id": f, "tool_call_id": tc}

    def tool_resume_case(self, case_id: str) -> dict:
        case = self._case(case_id)
        latest = self.latest_batch()
        rows = self.g.ro_query(
            "MATCH (c:Case {id: $id})-[:HAS]->(f:Finding) "
            "OPTIONAL MATCH (f)-[:HAS_PLAN]->(p:FreezePlan) "
            "RETURN f.id, f.kind, f.status, f.version, coalesce(f.checked_dv, f.data_version), f.value, p.id, p.verified "
            "ORDER BY f.version DESC", params={"id": case_id}).result_set
        findings = []
        for fid, kind, status, ver, dv, value, pid, verified in rows:
            findings.append({
                "finding_id": fid, "kind": kind,
                "status": "stale" if (int(dv) < latest and status == "current") else status,
                "version": int(ver), "data_version": int(dv), "plan_id": pid, "verified": verified,
                "summary": json.loads(value) if kind in ("region", "flow") else
                           {k: v for k, v in json.loads(value).items()
                            if k in ("statement", "verified", "money_movable_before_usd_cents", "perimeter_hops", "verdict", "note")},
            })
        return {
            "case_id": case_id, "alerted_account": case["origin"], "alert_ts": fmt_ts(int(case["alert_ts"])),
            "window_days": int(case["window_min"]) // DAY, "perimeter_hops": int(case["hops"]),
            "case_data_version": int(case["data_version"]), "latest_data_version": latest,
            "has_newer_data": latest > int(case["data_version"]), "findings": findings,
        }

    def tool_explain_finding(self, finding_id: str) -> dict:
        res = self.g.ro_query(
            "MATCH (tc:ToolCall)-[:PRODUCED]->(f:Finding {id: $id}) "
            "OPTIONAL MATCH (tc)-[r:READ]->(a:Account) RETURN tc, f, count(a)",
            params={"id": finding_id}).result_set
        if not res:
            raise ToolError("UNKNOWN_FINDING", f"finding {finding_id!r} not found")
        tc, f, n = res[0]
        return {
            "finding_id": finding_id, "kind": f.properties["kind"], "tool": tc.properties["tool"],
            "parameters": json.loads(tc.properties["params"]), "data_version": int(tc.properties["data_version"]),
            "solver": tc.properties["solver"], "ran_at": int(tc.properties["ran_at"]),
            "duration_ms": int(tc.properties["duration_ms"]), "accounts_read": int(n),
            "result": json.loads(f.properties["value"]),
        }

    # ------------------------------------------------------------------ views for the UI (not agent tools)
    def list_cases(self, limit: int = 20) -> list[dict]:
        rows = self.g.ro_query(
            "MATCH (c:Case) OPTIONAL MATCH (c)-[:HAS]->(f:Finding) "
            "RETURN c.id, c.origin, c.alert_ts, c.hops, c.created_at, count(f) ORDER BY c.created_at DESC LIMIT $n",
            params={"n": int(limit)}).result_set
        return [{"case_id": cid, "alerted_account": o, "alert_ts": fmt_ts(int(t)), "perimeter_hops": int(h),
                 "created_at": int(ca), "findings": int(n)} for cid, o, t, h, ca, n in rows]

    def graph_view(self, case_id: str, plan_id: str | None = None, max_nodes: int = 250) -> dict:
        """Evidence subgraph for the screen: the alerted account, where money can flow, and the plan."""
        case = self._case(case_id)
        frozen: set[str] = set()
        rule: set[str] = set()
        hops = int(case["hops"])
        verified = None
        if plan_id:
            info = self._plan(plan_id)
            hops = int(info["plan"]["hops"])
            frozen = set(info["frozen"])
            verified = bool(info["plan"].get("verified"))
            val = self.g.ro_query("MATCH (f:Finding {id: $f}) RETURN f.value",
                                  params={"f": info["finding_id"]}).result_set[0][0]
            rule = set(json.loads(val)["rule_of_thumb_plan"]["accounts"])
        region = self._region(case, hops)
        perimeter, trans, _ = self._network_parts(region)
        _, flows, _, _ = self._money_flow(region, trans, perimeter, set(), "view")
        if len(region.depth) <= max_nodes:
            keep = set(region.depth)
        else:
            keep = {region.origin} | frozen | rule | {a for e in flows for a in e}
            hop1_by_usd = sorted((d for (s, d) in trans if s == region.origin),
                                 key=lambda d: -trans[(region.origin, d)][2])
            for a in hop1_by_usd:
                if len(keep) >= max_nodes:
                    break
                keep.add(a)
        edges = [(s, d) for (s, d) in trans if s in keep and d in keep]
        edges.sort(key=lambda e: -trans[e][2])
        edges = edges[: 3 * max_nodes]

        def role(a: str) -> str:
            if a == region.origin:
                return "origin"
            if a in frozen:
                return "frozen"
            if a in rule:
                return "rule"
            return "perimeter" if a in perimeter else "other"

        return {
            "case_id": case_id, "plan_id": plan_id, "verified": verified, "perimeter_hops": hops,
            "total_accounts": len(region.depth), "shown_accounts": len(keep), "truncated": len(keep) < len(region.depth),
            "nodes": [{"id": a, "label": a.split(":")[-1][-6:], "depth": region.depth[a], "role": role(a)}
                      for a in sorted(keep)],
            "edges": [{"from": s, "to": d, "usd_cents": trans[(s, d)][2], "transfers": trans[(s, d)][0],
                       "flow_usd_cents": int(flows.get((s, d), 0))} for (s, d) in edges],
        }

    def delete_case(self, case_id: str) -> None:
        """Remove a case with its findings, plans and receipts (used by scripts and cleanup)."""
        self._write(
            "MATCH (c:Case {id: $id}) OPTIONAL MATCH (c)-[:HAS]->(f:Finding) "
            "OPTIONAL MATCH (f)-[:HAS_PLAN]->(p:FreezePlan) OPTIONAL MATCH (p)-[:ORDERED_AS]->(o:FreezeOrder) "
            "OPTIONAL MATCH (tc:ToolCall)-[:RUN_IN]->(c) "
            "DETACH DELETE o, p, f, tc, c", {"id": case_id})

    # ------------------------------------------------------------------ late data: ripple
    AFFECTED_QUERY = (
        "MATCH (:Case {id: $c})-[:HAS]->(f:Finding {status: 'current'}) "
        "MATCH (tc:ToolCall)-[:PRODUCED]->(f) "
        "MATCH (tc)-[:READ]->(a:Account)-[t:TRANSFER]-() "
        "WHERE t.batch > coalesce(f.checked_dv, f.data_version) AND t.batch <= $latest AND t.ts <= $t_to "
        "AND (t.ts >= $t_from OR (f.kind = 'freeze_plan' AND NOT f.key STARTS WITH 'freeze_plan:0:')) "
        "RETURN f.id AS id, count(t) AS n"
    )

    @staticmethod
    def _summary(kind: str, value: dict) -> dict:
        if kind == "region":
            return {k: value[k] for k in ("accounts", "transfer_pairs", "first_hop", "perimeter_accounts",
                                          "has_route_to_perimeter")}
        if kind == "flow":
            return {"max_movable_usd_cents": value["max_movable_usd_cents"]}
        if kind == "freeze_plan":
            return {"accounts": value["chokepoint_plan"]["accounts"], "cost": value["chokepoint_plan"]["cost"],
                    "movable_before_usd_cents": value["money_movable_before_usd_cents"],
                    "actionable": value["actionable"]}
        return {"verified": value.get("verified"), "perimeter_reachable_after": value.get("perimeter_reachable_after")}

    def _supersede(self, old_id: str, new_id: str) -> None:
        self._write("MATCH (o:Finding {id: $o}), (n:Finding {id: $n}) SET o.status = 'superseded' "
                    "CREATE (o)-[:SUPERSEDED_BY]->(n)", {"o": old_id, "n": new_id})

    def tool_refresh_case(self, case_id: str) -> dict:
        """Late or back-dated transfers arrived: find the findings whose inputs they touched (through the READ
        scope edges), re-run only those, supersede the old ones, and carry the rest forward unchanged."""
        started = time.perf_counter()
        case = self._case(case_id)
        latest = self.latest_batch()
        rows = self.g.ro_query(
            "MATCH (:Case {id: $c})-[:HAS]->(f:Finding {status: 'current'}) "
            "RETURN f.id, f.kind, f.key, coalesce(f.checked_dv, f.data_version), f.value ORDER BY f.version",
            params={"c": case_id}).result_set
        cur = [{"id": i, "kind": k, "key": key, "checked": int(c), "value": json.loads(v)} for i, k, key, c, v in rows]
        if not cur or all(f["checked"] >= latest for f in cur):
            return {"case_id": case_id, "refreshed": False, "latest_data_version": latest,
                    "message": "no newer data than this case's findings; nothing to re-run"}
        t_to = int(case["alert_ts"])
        hits = {r[0]: int(r[1]) for r in self.g.ro_query(
            self.AFFECTED_QUERY, params={"c": case_id, "latest": latest, "t_to": t_to,
                                         "t_from": t_to - int(case["window_min"])},
            timeout=HEAVY_TIMEOUT_MS).result_set}
        plan_of = {pid: fid for fid, pid in self.g.ro_query(
            "MATCH (:Case {id: $c})-[:HAS]->(f:Finding {status: 'current'})-[:HAS_PLAN]->(p:FreezePlan) "
            "RETURN f.id, p.id", params={"c": case_id}).result_set}
        affected = set(hits)
        for f in cur:  # a verification is only as current as the plan it checks
            if f["kind"] == "verification" and plan_of.get(f["key"].split(":", 1)[1]) in affected:
                affected.add(f["id"])
        carried = [f for f in cur if f["id"] not in affected]
        if carried:
            self._write("MATCH (f:Finding) WHERE f.id IN $ids SET f.checked_dv = $v",
                        {"ids": [f["id"] for f in carried], "v": latest})
        self._write("MATCH (c:Case {id: $c}) SET c.data_version = $v, c.refreshed_at = $now",
                    {"c": case_id, "v": latest, "now": int(time.time())})
        case = self._case(case_id)
        changes: list[dict] = []
        new_plan_id = None
        old_to_new_plan: dict[str, str] = {}

        def log(old: dict, new_value: dict | None, new_id: str | None, note: str = "") -> None:
            before = self._summary(old["kind"], old["value"])
            after = self._summary(old["kind"], new_value) if new_value else None
            changes.append({"kind": old["kind"], "before": before, "after": after, "changed": before != after,
                            "note": note})
            if new_id:
                self._supersede(old["id"], new_id)

        for f in (x for x in cur if x["id"] in affected and x["kind"] == "region"):
            region = self._region(case)
            r = self._region_result(case_id, case, region, started)
            log(f, r["value"], r["finding_id"])
        for f in (x for x in cur if x["id"] in affected and x["kind"] == "flow"):
            r = self.tool_flow(case_id)
            log(f, {"max_movable_usd_cents": r["max_movable_usd_cents"]}, r["finding_id"])
        for f in (x for x in cur if x["id"] in affected and x["kind"] == "freeze_plan"):
            _, lam, hops = f["key"].split(":")
            try:
                r = self.tool_freeze_plan(case_id, float(lam), int(hops))
            except (NoPath, NoFiniteCut) as e:
                log(f, None, None, f"no plan any more: {e}")
                self._write("MATCH (o:Finding {id: $o}) SET o.status = 'superseded'", {"o": f["id"]})
                continue
            old_pid = next((p for p, fid in plan_of.items() if fid == f["id"]), None)
            if old_pid:
                old_to_new_plan[old_pid] = r["plan_id"]
            new_plan_id = r["plan_id"]
            log(f, {k: r[k] for k in r if k not in ("plan_id", "finding_id", "tool_call_id")}, r["finding_id"])
        for f in (x for x in cur if x["id"] in affected and x["kind"] == "verification"):
            old_pid = f["key"].split(":", 1)[1]
            if old_pid in old_to_new_plan:
                r = self.tool_verify_plan(old_to_new_plan[old_pid])
                log(f, {k: r[k] for k in ("verified", "perimeter_reachable_after")}, r["finding_id"])
            else:
                self._write("MATCH (o:Finding {id: $o}) SET o.status = 'superseded'", {"o": f["id"]})
        return {
            "case_id": case_id, "refreshed": True, "from_data_version": min(f["checked"] for f in cur),
            "to_data_version": latest, "findings_checked": len(cur), "findings_rerun": len(affected),
            "findings_carried_forward": len(carried), "new_transfers_in_scope": sum(hits.values()),
            "changes": changes, "new_plan_id": new_plan_id,
            "summary": (f"{len(affected)} of {len(cur)} saved findings used accounts that received new transfers and "
                        f"were re-run" + (f"; {len(carried)} were unaffected and carried forward" if carried else "")),
        }

    # Demo support: simulate late, back-dated transfers (synthetic, clearly labelled) and reset the demo state.
    def inject_late_transfers(self, case_id: str) -> dict:
        """Simulate a late, back-dated route of the same length as the perimeter distance that bypasses the
        frozen accounts: alerted account -> X1 -> ... -> perimeter account (synthetic, for the demo)."""
        case = self._case(case_id)
        region = self._region(case)
        hops = int(case["hops"])
        if not region.perimeter:
            raise ToolError("NO_PATH", "this case has no perimeter to route late transfers to")
        batch = self.latest_batch() + 1
        tag = _det("L", case_id, batch)[2:].upper()
        mids = [f"000000:L{tag[:6]}{i}" for i in range(1, hops)]  # unique per hop and per injection
        chain = [region.origin, *mids, region.perimeter[0]]
        ts = int(case["alert_ts"]) - 90
        rows = [{"s": a, "d": b, "amt": 2_500_000 - 50_000 * i, "tid": 900_000_000 + batch * 10 + i}
                for i, (a, b) in enumerate(zip(chain, chain[1:]))]
        self._write("CREATE (:Batch {id: $b, loaded_at: $now, simulated: true})", {"b": batch, "now": int(time.time())})
        self._write("UNWIND $ids AS i MERGE (:Account {id: i})", {"ids": mids})
        self._write(
            "UNWIND $rows AS r MATCH (a:Account {id: r.s}), (b:Account {id: r.d}) "
            "CREATE (a)-[:TRANSFER {tid: r.tid, ts: $ts, amt: r.amt, cur: 'US Dollar', fmt: 'ACH', batch: $b}]->(b)",
            {"rows": rows, "ts": ts, "b": batch})
        return {"data_version": batch, "simulated": True, "back_dated_to": fmt_ts(ts),
                "transfers": [{"from": r["s"], "to": r["d"], "usd_cents": r["amt"]} for r in rows],
                "note": "synthetic late transfers added for the demo; they form a route that bypasses the plan"}

    # ------------------------------------------------------------------ human approval (not a tool: the model cannot call it)
    def order_freeze(self, plan_id: str, approver: str, reason: str) -> dict:
        """A person approves the plan. Refused unless the plan was verified at the case's current data version and no
        later transfers have arrived, so an approval cannot outlive its evidence. Records a FreezeOrder in the graph,
        bound by a hash to the plan, its accounts, the data version and the verification finding. No bank is called."""
        approver, reason = (approver or "").strip(), (reason or "").strip()
        if not 2 <= len(approver) <= 60:
            raise ToolError("BAD_ARGUMENT", "approver must be 2 to 60 characters")
        if len(reason) > 300:
            raise ToolError("BAD_ARGUMENT", "reason must be at most 300 characters")
        info = self._plan(plan_id)
        case = self._case(info["case_id"])
        dv = int(case["data_version"])
        if self.latest_batch() > dv:
            raise ToolError("STALE_EVIDENCE", "later transfers arrived after this plan was checked",
                            "re-run the affected findings, then approve the refreshed plan")
        ver = self._cached(info["case_id"], f"verify:{plan_id}", dv)
        if not ver or not ver[2].get("verified"):
            raise ToolError("NOT_VERIFIED", "the plan has no passing check on a copy of the graph at the current data version",
                            "run the check first; an unchecked plan cannot be approved")
        if not info["frozen"]:
            raise ToolError("NOTHING_TO_FREEZE", "this plan freezes no accounts")
        plan_hash = hashlib.sha256(json.dumps(
            {"plan": plan_id, "accounts": info["frozen"], "data_version": dv, "verification": ver[0]}, sort_keys=True).encode()).hexdigest()
        order_id = f"fo-{uuid.uuid4().hex[:8]}"
        now = int(time.time())
        self._write(
            "MATCH (p:FreezePlan {id: $p}) CREATE (o:FreezeOrder {id: $o, approver: $who, reason: $why, plan_hash: $h, "
            "data_version: $dv, verification: $vf, created_at: $now, status: 'recorded'}) CREATE (p)-[:ORDERED_AS]->(o)",
            {"p": plan_id, "o": order_id, "who": approver, "why": reason, "h": plan_hash, "dv": dv, "vf": ver[0], "now": now})
        self._write("MATCH (o:FreezeOrder {id: $o}) UNWIND $fz AS acct MATCH (a:Account {id: acct}) CREATE (o)-[:FOR_ACCOUNT]->(a)",
                    {"o": order_id, "fz": info["frozen"]})
        return {"order_id": order_id, "plan_id": plan_id, "approver": approver, "reason": reason, "accounts": info["frozen"],
                "plan_hash": plan_hash, "data_version": dv, "verification_finding_id": ver[0], "recorded_at": now,
                "statement": "Freeze order recorded in the graph. No bank is called; a person would send it on."}

    def reset_demo_state(self) -> dict:
        """Remove simulated late data and all saved cases, returning the graph to its loaded state."""
        self._write("MATCH ()-[t:TRANSFER]->() WHERE t.batch > 0 DELETE t", {})
        self._write("MATCH (a:Account) WHERE a.id STARTS WITH '000000:' DETACH DELETE a", {})
        self._write("MATCH (b:Batch) WHERE b.id > 0 DELETE b", {})
        self._write("MATCH (c:Case) OPTIONAL MATCH (c)-[:HAS]->(f:Finding) OPTIONAL MATCH (f)-[:HAS_PLAN]->(p:FreezePlan) "
                    "OPTIONAL MATCH (p)-[:ORDERED_AS]->(o:FreezeOrder) "
                    "OPTIONAL MATCH (tc:ToolCall)-[:RUN_IN]->(c) DETACH DELETE o, p, f, tc, c", {})
        return {"reset": True, "latest_data_version": self.latest_batch()}

    # ------------------------------------------------------------------ second agent: audit through shared state
    BLOCKING_CHECKS = ("verified", "current", "actionable", "no_routes_left")

    def _audit_checks(self, plan_id: str) -> dict:
        info = self._plan(plan_id)
        row = self.g.ro_query(
            "MATCH (f:Finding {id: $f}) RETURN f.value, f.status, coalesce(f.checked_dv, f.data_version)",
            params={"f": info["finding_id"]}).result_set[0]
        value, status, checked = json.loads(row[0]), row[1], int(row[2])
        cp, rule = value["chokepoint_plan"], value["rule_of_thumb_plan"]
        latest = self.latest_batch()
        checks = [
            ("verified", bool(info["plan"].get("verified")), "the independent route check passed"),
            ("current", status == "current" and checked >= latest,
             f"findings checked through data version {checked}, latest is {latest}"),
            ("actionable", bool(value["actionable"]), f"{cp['count']} account(s) to freeze (limit {MAX_ACTIONABLE_ACCOUNTS})"),
            ("no_routes_left", not cp["routes_left"], "no route from the alerted account to the perimeter remains"),
            ("frozen_accounts_on_a_route", cp["on_a_route_to_perimeter"] == cp["count"],
             f"{cp['on_a_route_to_perimeter']} of {cp['count']} frozen accounts lie on a route to the perimeter"),
            ("not_worse_than_rule_of_thumb", cp["residual_movable_usd_cents"] <= rule["residual_movable_usd_cents"],
             f"money left movable: plan {cp['residual_movable_usd_cents']} cents, rule of thumb "
             f"{rule['residual_movable_usd_cents']} cents"),
        ]
        out = [{"check": n, "passed": ok, "detail": d, "blocking": n in self.BLOCKING_CHECKS} for n, ok, d in checks]
        return {"plan_id": plan_id, "case_id": info["case_id"], "checks": out,
                "blocking_failed": [c["check"] for c in out if c["blocking"] and not c["passed"]]}

    def tool_audit_checks(self, plan_id: str) -> dict:
        """Deterministic checks on a stored plan. Read-only: the auditor sees the shared graph, not the investigator."""
        return self._audit_checks(plan_id)

    def tool_submit_audit(self, plan_id: str, verdict: str, note: str = "") -> dict:
        started = time.perf_counter()
        if verdict not in ("agree", "disagree", "needs_review"):
            raise ToolError("BAD_ARGUMENT", "verdict must be agree, disagree or needs_review")
        checks = self._audit_checks(plan_id)
        if verdict == "agree" and checks["blocking_failed"]:
            raise ToolError("AUDIT_INCONSISTENT",
                            f"cannot agree while blocking checks fail: {', '.join(checks['blocking_failed'])}",
                            "choose disagree or needs_review and say why")
        case = self._case(checks["case_id"])
        dv = int(case["data_version"])
        region = Region(origin=case["origin"], t_to=int(case["alert_ts"]), t_from=0, hops=0, data_version=dv)
        value = {"plan_id": plan_id, "verdict": verdict, "note": str(note)[:400], "author": "auditor agent",
                 "blocking_failed": checks["blocking_failed"],
                 "passed": [c["check"] for c in checks["checks"] if c["passed"]]}
        tc, f = self._record(checks["case_id"], "submit_audit", {"plan_id": plan_id, "verdict": verdict}, region,
                             "auditor", "audit", value, started, [], key=f"audit:{plan_id}",
                             salt=hashlib.sha1(f"{verdict}|{value['note']}".encode()).hexdigest()[:8])
        return {**value, "finding_id": f, "tool_call_id": tc, "recorded": True}

    def inject_scenario(self, case_id: str, kind: str, seed: int = 0) -> dict:
        """Evaluation helper: add one late batch of a given kind. 'bypass' is inject_late_transfers; 'dead_end' adds a
        recipient with no onward transfers; 'unrelated' adds a transfer between two new accounts; 'old' adds a
        transfer from the alerted account dated before the window."""
        if kind == "bypass":
            return self.inject_late_transfers(case_id)
        case = self._case(case_id)
        batch = self.latest_batch() + 1
        tag = _det("S", case_id, batch, kind)[2:].upper()
        a, b = f"000000:S{tag[:6]}1", f"000000:S{tag[:6]}2"
        t_to = int(case["alert_ts"])
        if kind == "dead_end":
            rows, ts, accts = [{"s": case["origin"], "d": a}], t_to - 60, [a]
        elif kind == "old":
            rows, ts, accts = [{"s": case["origin"], "d": a}], t_to - int(case["window_min"]) - 1440, [a]
        elif kind == "unrelated":
            rows, ts, accts = [{"s": a, "d": b}], t_to - 60, [a, b]
        else:
            raise ToolError("BAD_ARGUMENT", f"unknown scenario {kind!r}")
        self._write("CREATE (:Batch {id: $b, loaded_at: $now, simulated: true})", {"b": batch, "now": int(time.time())})
        self._write("UNWIND $ids AS i MERGE (:Account {id: i})", {"ids": accts})
        self._write(
            "UNWIND $rows AS r MATCH (a:Account {id: r.s}), (b:Account {id: r.d}) "
            "CREATE (a)-[:TRANSFER {tid: $tid, ts: $ts, amt: 100000, cur: 'US Dollar', fmt: 'ACH', batch: $b}]->(b)",
            {"rows": rows, "ts": ts, "b": batch, "tid": 900_000_000 + batch * 10})
        return {"data_version": batch, "scenario": kind}
