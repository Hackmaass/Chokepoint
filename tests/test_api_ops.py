"""Operational behaviour of the API: probes, error shape, headers, run lifecycle, SSE resume.

None of these need the dataset; the database is replaced with fakes where a failure is the point."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import BusyLoadingError
from redis.exceptions import ConnectionError as RedisConnectionError

import chokepoint.api as api


@pytest.fixture()
def client():
    api.tool_limit.hits.clear()
    api.run_limit.hits.clear()
    return TestClient(api.app, raise_server_exceptions=False)


def slots_free() -> bool:
    """True when every concurrent-run slot can be taken (then they are handed back)."""
    got = [api._run_slots.acquire(blocking=False) for _ in range(api.MAX_CONCURRENT_RUNS)]
    for ok in got:
        if ok:
            api._run_slots.release()
    return all(got)


def test_health_is_liveness_only_and_never_touches_the_database(client, monkeypatch):
    def boom():
        raise AssertionError("health must not use the database")

    monkeypatch.setattr(api, "database", boom)
    monkeypatch.setattr(api, "toolbox", boom)
    body = client.get("/api/health").json()
    assert body["ok"] is True and set(body) >= {"version", "demo", "live_available"}


def test_ready_reports_why_it_is_not_ready(client, monkeypatch):
    class Loading:
        def list_graphs(self):
            raise BusyLoadingError("Redis is loading the dataset in memory")

    monkeypatch.setattr(api, "probe", lambda: Loading())
    r = client.get("/api/ready")
    assert r.status_code == 503 and r.json()["state"] == "loading" and r.headers["retry-after"] == "5"

    def down():
        raise RedisConnectionError("refused")

    monkeypatch.setattr(api, "probe", down)
    assert client.get("/api/ready").json()["state"] == "db_unavailable"

    class Empty:
        def list_graphs(self):
            return []

    monkeypatch.setattr(api, "probe", lambda: Empty())
    assert client.get("/api/ready").json()["state"] == "dataset_missing"


def test_errors_share_one_shape_and_carry_a_request_id(client):
    r = client.post("/api/tools/rm_rf", json={}, headers={"X-Request-ID": "abc-123"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert r.headers["x-request-id"] == "abc-123" and r.json()["error"]["request_id"] == "abc-123"
    bad = client.post("/api/runs", json={"task": "x"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_REQUEST"
    assert "task" in bad.json()["error"]["message"]
    # a hostile request id is replaced, not echoed into logs or headers
    r = client.get("/api/health", headers={"X-Request-ID": "has spaces<script>"})
    assert "<" not in r.headers["x-request-id"] and " " not in r.headers["x-request-id"]


def test_security_headers_and_csp(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "content-security-policy" not in client.get("/openapi.json").headers  # Swagger needs its CDN


def test_database_outage_is_a_503_not_a_traceback(client, monkeypatch):
    def down():
        raise RedisConnectionError("Connection refused")

    monkeypatch.setattr(api, "toolbox", down)
    r = client.get("/api/cases")
    assert r.status_code == 503 and r.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert r.headers["retry-after"] == "5"


def test_unexpected_exception_becomes_a_typed_500(client, monkeypatch):
    def broken():
        raise RuntimeError("secret internals")

    monkeypatch.setattr(api, "toolbox", broken)
    r = client.get("/api/cases")
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret internals" not in r.text and r.json()["error"]["request_id"]


def test_demo_reset_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(api, "DEMO", True)
    monkeypatch.setattr(api.tool_limit, "limit", 0)
    assert client.post("/api/demo/reset").status_code == 429


def test_live_run_is_refused_cleanly_without_a_key(client, monkeypatch):
    monkeypatch.setattr(api, "live_available", lambda: False)
    r = client.post("/api/runs", json={"task": "investigate account 1:2", "mode": "live"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "LIVE_UNAVAILABLE"
    assert slots_free()


def test_a_failing_run_ends_with_an_error_event_and_frees_its_slot(client, monkeypatch):
    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(api, "toolbox", broken)
    run_id = client.post("/api/runs", json={"task": "investigate account 1:2", "mode": "replay"}).json()["run_id"]
    body = client.get(f"/api/runs/{run_id}/events").text  # the stream ends when the run is done
    assert '"type": "error"' in body and "event: end" in body
    deadline = time.time() + 5
    while not slots_free() and time.time() < deadline:
        time.sleep(0.05)
    assert slots_free()


def test_database_outage_during_a_run_is_reported_in_plain_words(client, monkeypatch):
    def down():
        raise RedisConnectionError("refused")

    monkeypatch.setattr(api, "toolbox", down)
    run_id = client.post("/api/runs", json={"task": "investigate account 1:2", "mode": "replay"}).json()["run_id"]
    assert "still loading" in client.get(f"/api/runs/{run_id}/events").text


def test_a_run_that_overruns_its_budget_is_stopped(monkeypatch):
    monkeypatch.setattr(api, "RUN_TIMEOUT_S", 0.05)

    def slow_agent(task, tb, llm, on_event, **_):
        for _ in range(3):
            time.sleep(0.06)
            on_event({"type": "tick"})

    monkeypatch.setattr(api, "run_agent", slow_agent)
    monkeypatch.setattr(api, "toolbox", lambda: object())
    st = {"events": [], "done": False, "created": time.time()}
    assert api._run_slots.acquire(blocking=False)
    api._execute("t1", st, api.RunRequest(task="investigate account 1:2", mode="replay"))
    assert st["done"] and "longer than" in st["events"][-1]["message"] and slots_free()


def test_event_stream_resumes_after_last_event_id(client):
    api._runs["resume-me"] = {"events": [{"type": "a"}, {"type": "b"}, {"type": "c"}], "done": True, "created": 0}
    try:
        body = client.get("/api/runs/resume-me/events", headers={"Last-Event-ID": "0"}).text
    finally:
        api._runs.pop("resume-me", None)
    assert "id: 1\n" in body and "id: 2\n" in body and "id: 0\n" not in body and body.rstrip().endswith("data: {}")


def test_eviction_never_drops_a_run_that_is_still_streaming(monkeypatch):
    monkeypatch.setattr(api, "MAX_RUNS_KEPT", 3)
    api._runs.clear()
    try:
        api._runs["live-one"] = {"events": [], "done": False, "created": 0}  # the oldest, but not finished
        for i in range(5):
            api._runs[f"done-{i}"] = {"events": [], "done": True, "created": i + 1}
        api._evict_finished_runs()
        assert "live-one" in api._runs and len(api._runs) == 3
    finally:
        api._runs.clear()


def test_toolbox_is_created_once_under_concurrent_first_requests(monkeypatch):
    made = []

    class FakeBox:
        def __init__(self, db):
            made.append(1)
            time.sleep(0.05)

    monkeypatch.setattr(api, "Toolbox", FakeBox)
    monkeypatch.setattr(api, "database", lambda: object())
    monkeypatch.setitem(api._state, "tb", None)
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(api.toolbox())) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len({id(x) for x in seen}) == 1  # every caller ends up with the one stored instance
    monkeypatch.setitem(api._state, "tb", None)


def test_database_busy_is_a_503_and_other_database_errors_are_typed_500s(client, monkeypatch):
    from redis.exceptions import ResponseError

    def busy():
        raise ResponseError("Max pending queries exceeded")

    monkeypatch.setattr(api, "toolbox", busy)
    r = client.get("/api/cases")
    assert r.status_code == 503 and r.json()["error"]["code"] == "DATABASE_BUSY"

    def refused():
        raise ResponseError("Unknown command")

    monkeypatch.setattr(api, "toolbox", refused)
    r = client.get("/api/cases")
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "Unknown command" not in r.text


def test_rate_limit_bucket_uses_the_address_the_trusted_proxy_appended(monkeypatch):
    monkeypatch.setattr(api, "TRUST_PROXY", True)
    limiter = api.RateLimiter(1)

    def req(xff):
        class R:
            headers = {"x-forwarded-for": xff}
            client = None

        return R()

    limiter(req("1.1.1.1, 9.9.9.9"))
    for spoofed in ("2.2.2.2, 9.9.9.9", "3.3.3.3, 9.9.9.9"):  # rotating the left entry must not escape the limit
        with pytest.raises(api.HTTPException) as e:
            limiter(req(spoofed))
        assert e.value.status_code == 429


def test_a_database_timeout_during_a_run_is_not_called_an_outage(client, monkeypatch):
    from redis.exceptions import TimeoutError as RedisTimeout

    def slow():
        raise RedisTimeout("Timeout reading from socket")

    monkeypatch.setattr(api, "toolbox", slow)
    run_id = client.post("/api/runs", json={"task": "investigate account 1:2", "mode": "replay"}).json()["run_id"]
    assert "took too long" in client.get(f"/api/runs/{run_id}/events").text
