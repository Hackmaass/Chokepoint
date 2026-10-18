"""The fixed Cypher behind each tool, for the Receipt tab. Nothing here is generated: the strings are the ones the tools
run (imported where they are constants), so a reader can see exactly what the database is asked. The agent never writes any."""

from __future__ import annotations

from .algo.cut import _MAXFLOW_QUERY
from .casegraph import FRONTIER_QUERY, TRANSFER_TIMES_QUERY
from .toolbox import BASE_QUERY, Toolbox

_VERIFY = """// verify_plan: on a scratch graph holding the case region (labels A, edges T)
MATCH (s:A {id: $o}) MATCH (t:A) WHERE t.id IN $perim
WITH t, shortestPath((s)-[:T*]->(t)) AS p RETURN t.id AS id, p IS NOT NULL AS reachable
// before the freeze; then GRAPH.COPY of the scratch graph, then on the copy:
MATCH (a:A) WHERE a.id IN $f DELETE a
// and the same shortestPath query again: every perimeter account must now be unreachable"""

QUERIES = [
    {"tool": "open_case", "name": "One hop of forward expansion", "cypher": FRONTIER_QUERY.strip(),
     "purpose": "Run once per hop: the transfers out of the frontier accounts in the window, summed per account pair."},
    {"tool": "freeze_plan", "name": "Everyday activity of the candidate accounts", "cypher": BASE_QUERY.strip(),
     "purpose": "How many transfers each candidate account has overall, shown beside the plan."},
    {"tool": "freeze_plan", "name": "Maximum flow on the node-split network", "cypher": _MAXFLOW_QUERY.strip(),
     "purpose": "First pass at the minimum-cost cut. Accepted only if a certificate proves it maximal; otherwise an exact solver runs."},
    {"tool": "freeze_plan", "name": "Transfer times for the timing check", "cypher": TRANSFER_TIMES_QUERY.strip(),
     "purpose": "Every transfer between region accounts with its time, so only time-ordered routes are kept."},
    {"tool": "verify_plan", "name": "Check on a copy of the graph", "cypher": _VERIFY,
     "purpose": "Delete the frozen accounts from a copy and search again for a route to the perimeter."},
    {"tool": "refresh_case", "name": "Which findings read accounts that late transfers touched", "cypher": Toolbox.AFFECTED_QUERY.strip(),
     "purpose": "Only these findings are re-run when late data arrives; the rest stay."},
]
