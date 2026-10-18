"""The public MCP endpoint: the same nine tools over streamable HTTP, and nothing that approves or dispatches."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastmcp import Client
from fastmcp.exceptions import ToolError

import chokepoint.api as api
from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect

pytestmark = pytest.mark.db
EX = next(e for e in json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8")) if e["account"] == "021174:800737690")
NINE = {"open_case", "flow", "freeze_plan", "verify_plan", "resume_case", "refresh_case", "explain_finding", "audit_checks", "submit_audit"}


@pytest.fixture()
def server():
    if "tenant_ibm" not in connect().list_graphs():
        pytest.skip("tenant_ibm not loaded")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    api.tool_limit.hits.clear()
    yield f"http://127.0.0.1:{port}/mcp"
    srv.should_exit = True
    t.join(10)


def _data(res):
    return res.structured_content or json.loads(res.content[0].text)


def test_the_nine_tools_work_over_http_and_nothing_can_approve_or_dispatch(server):
    async def go():
        async with Client(server) as c:
            names = {t.name for t in await c.list_tools()}
            assert names == NINE and not [n for n in names if any(w in n for w in ("order", "dispatch", "withdraw", "hold"))]
            case = _data(await c.call_tool("open_case", {"account": EX["account"], "alert_ts": EX["alert_ts"]}))
            plan = _data(await c.call_tool("freeze_plan", {"case_id": case["case_id"]}))
            ver = _data(await c.call_tool("verify_plan", {"plan_id": plan["plan_id"]}))
            assert ver["verified"] is True and plan["chokepoint_plan"]["routes_left"] is False
            with pytest.raises(ToolError):
                await c.call_tool("order_freeze", {"plan_id": plan["plan_id"], "approver": "x y", "reason": ""})

    asyncio.run(go())


def test_the_public_endpoint_is_rate_limited(server, monkeypatch):
    import httpx

    monkeypatch.setattr(api, "tool_limit", api.RateLimiter(3))
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    codes = [httpx.post(server, headers=hdr, json={"jsonrpc": "2.0", "id": i, "method": "ping"}).status_code for i in range(6)]
    assert codes[:3] != [429] * 3 and 429 in codes[3:]
