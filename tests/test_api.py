"""The HTTP API: whitelist, validation and rate limits (needs the loaded graph)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import chokepoint.api as api
from chokepoint.config import PROJECT_ROOT
from chokepoint.db import connect

pytestmark = pytest.mark.db
EX = json.loads((PROJECT_ROOT / "examples" / "examples.json").read_text(encoding="utf-8"))[3]


@pytest.fixture()
def client():
    if "tenant_ibm" not in connect().list_graphs():
        pytest.skip("tenant_ibm not loaded")
    api.tool_limit.hits.clear()
    api.run_limit.hits.clear()
    return TestClient(api.app)


def test_health_examples_and_tool_whitelist(client):
    assert client.get("/api/health").json()["ok"] is True
    assert len(client.get("/api/examples").json()) == 8
    assert client.post("/api/tools/rm_rf", json={}).status_code == 404  # only the named tools exist
    assert client.post("/api/runs", json={"task": "x"}).status_code == 422  # too short
    assert client.post("/api/runs", json={"task": "a long enough task", "mode": "anything"}).status_code == 422


def test_tools_return_typed_errors_not_exceptions(client):
    r = client.post("/api/tools/open_case", json={"account": "x" * 500, "alert_ts": EX["alert_ts"]}).json()
    assert r["error"]["code"] == "UNKNOWN_ACCOUNT"
    r = client.post("/api/tools/open_case", json={"account": EX["account"]}).json()  # missing argument
    assert r["error"]["code"] == "BAD_ARGUMENT"


def test_rate_limit_applies_per_client(client):
    api.tool_limit.limit = 5
    try:
        codes = [client.post("/api/tools/resume_case", json={"case_id": "c-none"}).status_code for _ in range(8)]
    finally:
        api.tool_limit.limit = 60
    assert codes[:5] == [200] * 5 and codes[5:] == [429] * 3


def test_ready_and_unknown_case_use_http_errors(client):
    r = client.get("/api/ready").json()
    assert r["ready"] is True and r["latest_data_version"] >= 0
    missing = client.get("/api/cases/c-doesnotexist")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
    assert client.get("/api/cases/c-doesnotexist/graph").status_code == 404
    # tool endpoints keep their contract: a failed tool is a 200 whose body is the typed error
    tool = client.post("/api/tools/resume_case", json={"case_id": "c-doesnotexist"})
    assert tool.status_code == 200 and tool.json()["error"]["code"] == "UNKNOWN_CASE"
