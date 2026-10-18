"""The MCP server lists the tools and returns typed results (in-process client)."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client

from chokepoint.config import PROJECT_ROOT
from chokepoint.mcp_server import mcp

pytestmark = pytest.mark.db


def _payload(res):
    if getattr(res, "structured_content", None):
        return res.structured_content
    return json.loads(res.content[0].text)


def test_tools_are_listed_and_work_through_mcp():
    from chokepoint.db import connect

    if "tenant_ibm" not in connect().list_graphs():
        pytest.skip("tenant_ibm not loaded")
    example = next(e for e in json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8")) if e["account"] == "021174:800737690")

    async def go():
        async with Client(mcp) as c:
            names = {t.name for t in await c.list_tools()}
            assert names == {"open_case", "flow", "freeze_plan", "verify_plan", "resume_case", "refresh_case", "explain_finding",
                             "audit_checks", "submit_audit"}
            case = _payload(await c.call_tool("open_case", {"account": example["account"], "alert_ts": example["alert_ts"]}))
            assert case["has_route_to_perimeter"] is True
            plan = _payload(await c.call_tool("freeze_plan", {"case_id": case["case_id"]}))
            assert plan["chokepoint_plan"]["routes_left"] is False
            ver = _payload(await c.call_tool("verify_plan", {"plan_id": plan["plan_id"]}))
            assert ver["verified"] is True
            err = _payload(await c.call_tool("flow", {"case_id": "c-missing"}))
            assert err["error"]["code"] == "UNKNOWN_CASE"

    asyncio.run(go())
