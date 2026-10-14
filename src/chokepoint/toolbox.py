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

from .algo.cut import (
    FlowNetwork,
    NoFiniteCut,
    NoPath,
    build_split_network,
    freeze_plan as solve_freeze_plan,
    reaches_exit,
    solve,
)
from .casegraph import (
    CaseTooLarge,
    Region,
    TruncatedResult,
    UnknownAccount,
    extract_region,
)

DAY = 1440
DEFAULT_WINDOW_DAYS = 7
MAX_ACTIONABLE_ACCOUNTS = 25  # a plan larger than this is flagged, not presented as a recommendation
MAX_CASE_NODES = 4000


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
    def __init__(self, db, tenant: str = "ibm"):
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
        self.g.query(query, params=params)

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
            return ToolError("UNKNOWN_ACCOUNT", f"account {e} not found").as_dict()
        except CaseTooLarge as e:
            return ToolError("CASE_TOO_LARGE", str(e), "use a shorter window or fewer hops").as_dict()
        except TruncatedResult as e:
            return ToolError("TRUNCATED_RESULT", str(e)).as_dict()
        except NoPath as e:
            return ToolError("NO_PATH", str(e), "widen the window or the perimeter and try again").as_dict()
        except NoFiniteCut as e:
            return ToolError("NO_FINITE_CUT", str(e)).as_dict()

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
        rows = self.g.ro_query(BASE_QUERY, params={"ids": freezable, "t_to": region.t_to,
                                                   "dv": region.data_version}).result_set
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
            "MATCH (:Case {id: $c})-[:HAS]->(f:Finding {key: $k, data_version: $dv}) "
            "MATCH (tc:ToolCall)-[:PRODUCED]->(f) RETURN f.id, tc.id, f.value ORDER BY f.version DESC LIMIT 1",
            params={"c": case_id, "k": key, "dv": dv}).result_set
        return (rows[0][0], rows[0][1], json.loads(rows[0][2])) if rows else None

    def _record(self, case_id: str, tool: str, params: dict, region: Region, solver: str, kind: str,
                value: dict, started: float, scope: list[str], key: str = "") -> tuple[str, str]:
        key = key or kind
        tc_id = _det("tc", case_id, key, region.data_version)
        f_id = _det("f", case_id, key, region.data_version)
        version = int(self.g.ro_query(
            "MATCH (:Case {id: $c})-[:HAS]->(f:Finding {kind: $k}) RETURN count(f)",
            params={"c": case_id, "k": kind}).result_set[0][0]) + 1
        self._write(
            "MATCH (c:Case {id: $case_id}) "
            "CREATE (tc:ToolCall {id: $tc, tool: $tool, params: $params, data_version: $dv, solver: $solver, "
            "ran_at: $now, duration_ms: $ms}) "
            "CREATE (tc)-[:RUN_IN]->(c) "
            "CREATE (f:Finding {id: $f, kind: $kind, key: $key, value: $value, status: 'current', version: $ver, "
            "data_version: $dv}) "
            "CREATE (tc)-[:PRODUCED]->(f) CREATE (c)-[:HAS]->(f)",
            {"case_id": case_id, "tc": tc_id, "tool": tool, "params": json.dumps(params, sort_keys=True),
             "dv": region.data_version, "solver": solver, "now": int(time.time()),
             "ms": int((time.perf_counter() - started) * 1000), "f": f_id, "kind": kind, "key": key,
             "value": json.dumps(value, sort_keys=True), "ver": version},
        )
        if scope:
            self._write("MATCH (tc:ToolCall {id: $tc}) UNWIND $scope AS acct "
                        "MATCH (a:Account {id: acct}) CREATE (tc)-[:READ]->(a)",
                        {"tc": tc_id, "scope": scope})
        return tc_id, f_id

    # ------------------------------------------------------------------ tools
    def tool_open_case(self, account: str, alert_ts: str, window_days: int = DEFAULT_WINDOW_DAYS,
                       perimeter_hops: int = 3) -> dict:
        started = time.perf_counter()
        if not 2 <= int(perimeter_hops) <= 4:
            raise ToolError("BAD_ARGUMENT", "perimeter_hops must be 2, 3 or 4")
        if not 1 <= int(window_days) <= 14:
            raise ToolError("BAD_ARGUMENT", "window_days must be between 1 and 14")
        t = parse_ts(alert_ts)
        dv = self.latest_batch()
        region = extract_region(self.g, account, t, int(window_days) * DAY, int(perimeter_hops), dv, MAX_CASE_NODES)
        case_id = _det("c", account, t, int(window_days), int(perimeter_hops), dv)
        known = self._cached(case_id, "region", dv)
        if known:
            return {"case_id": case_id, "alerted_account": account, "alert_ts": fmt_ts(t), "data_version": dv,
                    "window_days": int(window_days), "perimeter_hops": int(perimeter_hops), **known[2],
                    "note": ("no route from the alerted account to the perimeter in this window; widen the "
                             "window or the perimeter") if not known[2]["has_route_to_perimeter"] else "",
                    "already_open": True}
        perimeter, trans, freezable = self._network_parts(region)
        self._write(
            "CREATE (:Case {id: $id, origin: $o, alert_ts: $t, window_min: $w, hops: $k, data_version: $dv, "
            "status: 'open', created_at: $now})",
            {"id": case_id, "o": account, "t": t, "w": int(window_days) * DAY, "k": int(perimeter_hops),
             "dv": dv, "now": int(time.time())},
        )
        plain = nx.DiGraph(list(trans))
        has_route = bool(perimeter) and region.origin in plain and any(
            p in plain and nx.has_path(plain, region.origin, p) for p in perimeter)
        value = {
            "accounts": len(region.depth), "transfer_pairs": len(trans), "first_hop": len(region.hop1),
            "perimeter_accounts": len(perimeter), "has_route_to_perimeter": has_route,
        }
        self._record(case_id, "open_case",
                     {"account": account, "alert_ts": fmt_ts(t), "window_days": int(window_days),
                      "perimeter_hops": int(perimeter_hops)},
                     region, "falkordb-cypher", "region", value, started, [])
        return {"case_id": case_id, "alerted_account": account, "alert_ts": fmt_ts(t), "data_version": dv,
                "window_days": int(window_days), "perimeter_hops": int(perimeter_hops), **value,
                "note": ("no route from the alerted account to the perimeter in this window; widen the window "
                         "or the perimeter") if not has_route else ""}

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
            }

        result = {
            "case_id": case_id, "lambda_weight": lam, "perimeter_hops": hops,
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
                             region, plan.solver, "freeze_plan", result, started, scope, key=key)
        self._write(
            "MATCH (f:Finding {id: $f}) "
            "CREATE (p:FreezePlan {id: $p, cost: $cost, lambda: $lam, size: $k, hops: $hops, verified: false}) "
            "CREATE (f)-[:HAS_PLAN]->(p)",
            {"f": f, "p": plan_id, "cost": int(plan.cost), "lam": lam, "k": k, "hops": hops})
        if frozen:
            self._write("MATCH (p:FreezePlan {id: $p}) UNWIND $fz AS acct MATCH (a:Account {id: acct}) "
                        "CREATE (p)-[:FREEZES]->(a)", {"p": plan_id, "fz": frozen})
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
            scratch.copy(copy_name)
            copy = self.db.select_graph(copy_name)
            copy.query("MATCH (a:A) WHERE a.id IN $f DELETE a", params={"f": info["frozen"]})
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
            "method": "rebuilt the region from the database at the stored data version, deleted the frozen "
                      "accounts from a copy of the graph, ran shortestPath to every perimeter account "
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
            "RETURN f.id, f.kind, f.status, f.version, f.data_version, f.value, p.id, p.verified "
            "ORDER BY f.version DESC", params={"id": case_id}).result_set
        findings = []
        for fid, kind, status, ver, dv, value, pid, verified in rows:
            findings.append({
                "finding_id": fid, "kind": kind,
                "status": "stale" if (int(dv) < latest and status == "current") else status,
                "version": int(ver), "data_version": int(dv), "plan_id": pid, "verified": verified,
                "summary": json.loads(value) if kind in ("region", "flow") else
                           {k: v for k, v in json.loads(value).items()
                            if k in ("statement", "verified", "money_movable_before_usd_cents", "perimeter_hops")},
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
            "OPTIONAL MATCH (f)-[:HAS_PLAN]->(p:FreezePlan) OPTIONAL MATCH (tc:ToolCall)-[:RUN_IN]->(c) "
            "DETACH DELETE p, f, tc, c", {"id": case_id})
