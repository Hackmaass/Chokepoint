"""MCP server exposing the same tools the built-in agent uses (stdio by default).

  uv run python -m chokepoint.mcp_server                 # stdio, for Claude Desktop, Cursor, etc.
  uv run python -m chokepoint.mcp_server --http 8765     # streamable HTTP on localhost (set MCP_TOKEN to require a token)

Every tool is a fixed, parameterised operation over FalkorDB; there is no way to send Cypher. Errors come back
as {"error": {"code", "message", "hint"}} so a client can react to NO_PATH and the other typed codes.
"""

from __future__ import annotations

import argparse
import os
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from .db import connect
from .toolbox import Toolbox

mcp = FastMCP(
    "chokepoint",
    instructions=(
        "Tools for investigating where stolen money can still move and which accounts to freeze. Workflow: "
        "open_case, flow, freeze_plan, verify_plan. The alerted account is assumed already frozen. Never state a "
        "number that did not come from a tool. A plan is only a recommendation once verify_plan returns verified=true."
    ),
)
_tb: Toolbox | None = None


def tb() -> Toolbox:
    global _tb
    if _tb is None:
        _tb = Toolbox(connect())
    return _tb


@mcp.tool
def open_case(
    account: Annotated[str, Field(description="alerted account, bank:account")],
    alert_ts: Annotated[str, Field(description="ISO UTC time, e.g. 2022-09-03T10:15")],
    window_days: Annotated[int, Field(ge=1, le=14, description="days of history before the alert")] = 7,
    perimeter_hops: Annotated[int, Field(ge=2, le=4, description="money this many hops away counts as out of reach")] = 3,
) -> dict:
    """Open an investigation: build the region the money could have reached before the alert. Returns a case_id."""
    return tb().call("open_case", {"account": account, "alert_ts": alert_ts, "window_days": window_days,
                                   "perimeter_hops": perimeter_hops})


@mcp.tool
def flow(case_id: str) -> dict:
    """Upper bound on money (USD cents) that could still move from the alerted account to the perimeter."""
    return tb().call("flow", {"case_id": case_id})


@mcp.tool
def freeze_plan(
    case_id: str,
    lambda_weight: Annotated[float, Field(ge=0, le=8, description="0 = fewest accounts; higher avoids busy accounts")] = 0.0,
    perimeter_hops: Annotated[int | None, Field(ge=2, le=4)] = None,
) -> dict:
    """Cheapest accounts to freeze so no route to the perimeter is left, with a same-size rule-of-thumb plan to compare."""
    args: dict = {"case_id": case_id, "lambda_weight": lambda_weight}
    if perimeter_hops:
        args["perimeter_hops"] = perimeter_hops
    return tb().call("freeze_plan", args)


@mcp.tool
def verify_plan(plan_id: str) -> dict:
    """Check a plan on a copy of the graph with the frozen accounts removed: is any route to the perimeter left?"""
    return tb().call("verify_plan", {"plan_id": plan_id})


@mcp.tool
def resume_case(case_id: str) -> dict:
    """Reload a case and its stored findings from an earlier session; says whether newer data exists."""
    return tb().call("resume_case", {"case_id": case_id})


@mcp.tool
def refresh_case(case_id: str) -> dict:
    """After newer data arrived: re-run only the findings that used accounts with new transfers, and report what changed."""
    return tb().call("refresh_case", {"case_id": case_id})


@mcp.tool
def explain_finding(finding_id: str) -> dict:
    """The receipt behind a finding: which tool run, parameters, data version and solver produced it."""
    return tb().call("explain_finding", {"finding_id": finding_id})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, default=0, help="serve streamable HTTP on this port instead of stdio")
    args = ap.parse_args()
    if not args.http:
        mcp.run()
        return
    token = os.environ.get("MCP_TOKEN")
    if token:
        from fastmcp.server.auth import StaticTokenVerifier

        mcp.auth = StaticTokenVerifier(tokens={token: {"client_id": "chokepoint", "scopes": []}})
    mcp.run(transport="http", host="127.0.0.1", port=args.http)


if __name__ == "__main__":
    main()
