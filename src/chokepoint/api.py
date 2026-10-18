"""REST + server-sent-events API. The LLM never sees Cypher and the API never accepts it.

  POST /api/runs               start an agent run (mode: live | replay)         -> {run_id}
  GET  /api/runs/{id}/events   SSE stream of the trace (resumable with Last-Event-ID)
  POST /api/tools/{tool}       call one tool directly (tool-only mode, no LLM)
  POST /api/plans/{id}/order   a named person approves a checked plan (holds + freeze request); not a tool
  POST /api/orders/{id}/dispatch   send a queued freeze request again;  DELETE /api/orders/{id}  withdraw an order
  GET  /api/cases              recent cases;  GET /api/cases/{id}  resume
  GET  /api/cases/{id}/graph   evidence subgraph for the screen (optional ?plan_id=)
  GET  /api/examples           curated example alerts
  GET  /api/health             liveness: the process is up (no database call)
  GET  /api/ready              readiness: the database answers and the dataset is loaded (503 otherwise)

Errors are always ``{"error": {"code", "message", "hint"?}}``. The one exception to "errors use an HTTP error
status" is ``POST /api/tools/{tool}``: a tool that fails returns HTTP 200 with the typed error as its result,
exactly what the agent sees, so tool-only mode and the agent loop share one contract.

The server must run as ONE uvicorn worker: agent runs and their event streams live in this process's memory.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import mimetypes
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from redis.exceptions import BusyLoadingError, RedisError, ResponseError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .agent import AUDITOR_PROMPT, AUDITOR_SPECS, answer_card, run_agent
from .config import PROJECT_ROOT, get_settings, load_env
from .db import connect
from .llm import REPLAY_DIR, LLMClient
from .toolbox import Toolbox, ToolError

load_env()
if not logging.getLogger().handlers:  # uvicorn configures its own loggers only
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("chokepoint.api")

EXAMPLES = PROJECT_ROOT / "examples" / "examples.json"
MAX_CONCURRENT_RUNS = 2
MAX_RUNS_KEPT = 50
LIVE_ATTEMPTS = int(os.environ.get("LIVE_LLM_ATTEMPTS", "2"))  # live demo gives up after ~12 s of 429s and shows a saved run
EXAMPLE_TASK = re.compile(r"Account \S+ was alerted at \S+\. Investigate and recommend which accounts to freeze\.")
RUN_TIMEOUT_S = float(os.environ.get("RUN_TIMEOUT_S", "300"))  # checked between agent steps
SSE_PING_S = 15.0
# Socket timeouts for the database. The longest query allowed is the server's TIMEOUT_MAX (60 s); without these a
# restarting database leaves worker threads blocked on a dead connection and the whole API stops answering.
DB_CONNECT_TIMEOUT_S = 3.0
DB_SOCKET_TIMEOUT_S = 75.0
PROBE_TIMEOUT_S = 3.0
TOOLS = {"open_case", "flow", "freeze_plan", "verify_plan", "resume_case", "explain_finding", "refresh_case",
         "audit_checks"}
DEMO = os.environ.get("DEMO_MODE", "1") != "0"  # simulated late transfers and reset (synthetic edits only)
TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"  # behind Caddy or nginx: use X-Forwarded-For
CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
# The UI is served by this app (same origin), so CORS only matters for the Vite dev server. Fonts come from Google.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self'")
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")  # Swagger UI loads its assets from a CDN, so no CSP there
_RID = re.compile(r"[A-Za-z0-9_.-]{1,64}")

app = FastAPI(title="Chokepoint", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])

_state: dict = {"db": None, "tb": None, "probe": None}
_state_lock = threading.Lock()
_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()
_run_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)


def database():
    with _state_lock:
        if _state["db"] is None:
            _state["db"] = connect(DB_SOCKET_TIMEOUT_S, DB_CONNECT_TIMEOUT_S)
        return _state["db"]


def probe():
    """A second client with short timeouts, used only by /api/ready, so readiness answers even if every worker
    thread of the main client is stuck behind a slow query."""
    with _state_lock:
        if _state["probe"] is None:
            _state["probe"] = connect(PROBE_TIMEOUT_S, PROBE_TIMEOUT_S)
        return _state["probe"]


def toolbox() -> Toolbox:
    with _state_lock:
        tb = _state["tb"]
    if tb is None:
        tb = Toolbox(database())  # may raise while the database is down or loading; retried on the next request
        with _state_lock:
            _state["tb"] = _state["tb"] or tb
            tb = _state["tb"]
    return tb


@functools.cache
def live_available() -> bool:
    return bool(get_settings().llm_api_key)


class RateLimiter:
    """A small per-client sliding-window limiter for the endpoints that cost something (tools, runs, demo edits)."""

    def __init__(self, limit: int, window_s: float = 60.0):
        self.limit, self.window, self.hits = limit, window_s, defaultdict(deque)
        self._lock = threading.Lock()

    def __call__(self, request: Request) -> None:
        # Behind exactly one trusted proxy (Caddy, nginx) the RIGHTMOST entry is the address that proxy saw; the
        # entries to its left are whatever the client chose to send, so they must not pick the rate-limit bucket.
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip() if TRUST_PROXY else ""
        ip = forwarded or (request.client.host if request.client else "unknown")
        now = time.monotonic()
        with self._lock:
            if len(self.hits) > 10_000:  # forget clients with nothing in the window
                for k in [k for k, q in self.hits.items() if not q or now - q[-1] > self.window]:
                    del self.hits[k]
            q = self.hits[ip]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                raise HTTPException(429, "too many requests; slow down", headers={"Retry-After": "10"})
            q.append(now)


tool_limit = RateLimiter(int(os.environ.get("TOOL_RATE_LIMIT", "60")))
run_limit = RateLimiter(int(os.environ.get("RUN_RATE_LIMIT", "8")))


# ---------------------------------------------------------------------------------------------- errors, logging
def error_response(status: int, code: str, message: str, hint: str = "", headers: dict | None = None,
                   request: Request | None = None) -> JSONResponse:
    body: dict = {"code": code, "message": message}
    if hint:
        body["hint"] = hint
    rid = getattr(request.state, "request_id", None) if request is not None else None
    if rid:
        body["request_id"] = rid
    return JSONResponse({"error": body}, status_code=status, headers=headers)


_HTTP_CODES = {400: "BAD_REQUEST", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
               429: "RATE_LIMITED", 503: "UNAVAILABLE"}


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return error_response(exc.status_code, _HTTP_CODES.get(exc.status_code, "ERROR"), str(exc.detail),
                          headers=getattr(exc, "headers", None), request=request)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
    return error_response(422, "INVALID_REQUEST", f"{where}: {first.get('msg', 'invalid request')}".strip(": "),
                          request=request)


@app.exception_handler(RedisConnectionError)
@app.exception_handler(RedisTimeoutError)
async def database_down(request: Request, exc: RedisError):
    log.warning("database unavailable rid=%s %s: %s", getattr(request.state, "request_id", "-"),
                type(exc).__name__, exc)
    return error_response(503, "DATABASE_UNAVAILABLE", "The graph database is unavailable or still loading.",
                          "Try again in a few seconds.", {"Retry-After": "5"}, request)


@app.exception_handler(ResponseError)
async def database_refused(request: Request, exc: ResponseError):
    rid = getattr(request.state, "request_id", "-")
    if "pending queries" in str(exc).lower() or "loading" in str(exc).lower():
        log.warning("database busy rid=%s: %s", rid, exc)
        return error_response(503, "DATABASE_BUSY", "The graph database is busy.", "Try again in a few seconds.",
                              {"Retry-After": "5"}, request)
    log.error("database error rid=%s: %s", rid, exc)
    return error_response(500, "INTERNAL_ERROR", "The database rejected a request.",
                          f"Quote request id {rid} when reporting it.", request=request)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Request id, one log line per API call, security headers, and a typed 500 instead of a bare traceback."""
    rid = request.headers.get("x-request-id", "")
    request.state.request_id = rid if _RID.fullmatch(rid) else uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error rid=%s %s %s", request.state.request_id, request.method, request.url.path)
        response = error_response(500, "INTERNAL_ERROR", "Something went wrong on the server.",
                                  f"Quote request id {request.state.request_id} when reporting it.", request=request)
    path = request.url.path
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if path not in DOC_PATHS:
        response.headers["Content-Security-Policy"] = CSP
    if path.startswith("/api/") and path not in ("/api/health", "/api/ready") and not path.endswith("/events"):
        log.info("%s %s -> %s %dms rid=%s", request.method, path, response.status_code,
                 (time.perf_counter() - t0) * 1000, request.state.request_id)
    return response


# ---------------------------------------------------------------------------------------------- probes
@app.get("/api/health")
def health() -> dict:
    """Liveness only: never touches the database, so an orchestrator can tell 'process up' from 'database busy'."""
    return {"ok": True, "version": app.version, "demo": DEMO, "live_available": live_available()}


@app.get("/api/ready")
def ready() -> JSONResponse:
    """Readiness: the database answers and the dataset is loaded. 503 with a reason otherwise (the UI polls this)."""
    def not_ready(state: str, message: str) -> JSONResponse:
        return JSONResponse({"ready": False, "state": state, "message": message}, status_code=503,
                            headers={"Retry-After": "5"})

    try:
        db = probe()
        if "tenant_ibm" not in db.list_graphs():
            return not_ready("dataset_missing", "The transfer graph has not been loaded yet "
                                                "(docker compose --profile seed run --rm seed).")
        batch = db.select_graph("tenant_ibm").ro_query("MATCH (b:Batch) RETURN max(b.id)").result_set[0][0]
        return JSONResponse({"ready": True, "state": "ready", "latest_data_version": int(batch or 0)})
    except BusyLoadingError:
        return not_ready("loading", "The database is loading the graph into memory; this takes about a minute.")
    except (RedisConnectionError, RedisTimeoutError):
        return not_ready("db_unavailable", "The graph database is not reachable.")
    except ResponseError as e:  # "Max pending queries exceeded", or a reply while still starting up
        return not_ready("busy" if "pending" in str(e).lower() else "loading", "The database is busy; retrying.")


# ---------------------------------------------------------------------------------------------- tools and cases
class RunRequest(BaseModel):
    task: str = Field(min_length=5, max_length=600)
    mode: str = Field(default="live", pattern="^(live|replay)$")
    role: str = Field(default="investigator", pattern="^(investigator|auditor)$")


@app.get("/api/examples")
def examples() -> list[dict]:
    return json.loads(EXAMPLES.read_text(encoding="utf-8")) if EXAMPLES.exists() else []


@app.post("/api/tools/{tool}", dependencies=[Depends(tool_limit)])
def call_tool(tool: str, args: dict) -> dict:
    if tool not in TOOLS:
        raise HTTPException(404, "unknown tool")
    t0 = time.perf_counter()
    result = toolbox().call(tool, args)
    log.info("tool=%s ms=%d %s", tool, (time.perf_counter() - t0) * 1000,
             f"error={result['error']['code']}" if "error" in result else "ok")
    return result


def _typed(e: Exception) -> HTTPException | None:
    """Map a tool or case-graph error (they carry a ``code``) to an HTTP error; anything else is a real fault."""
    code = getattr(e, "code", None)
    if not isinstance(code, str):
        return None
    return HTTPException(404 if code.startswith("UNKNOWN") else 400, getattr(e, "message", str(e)))


@app.post("/api/demo/late-transfers/{case_id}", dependencies=[Depends(tool_limit)])
def demo_late_transfers(case_id: str) -> dict:
    """Simulate late, back-dated transfers for a case (synthetic; creates a route that bypasses the plan)."""
    if not DEMO:
        raise HTTPException(403, "demo mode is off")
    try:
        return toolbox().inject_late_transfers(case_id)
    except ToolError as e:
        raise _typed(e) from e


@app.post("/api/demo/reset", dependencies=[Depends(tool_limit)])
def demo_reset() -> dict:
    if not DEMO:
        raise HTTPException(403, "demo mode is off")
    return toolbox().reset_demo_state()


@app.get("/api/cases")
def cases(limit: int = 20) -> list[dict]:
    return toolbox().list_cases(min(max(limit, 1), 50))


class OrderRequest(BaseModel):
    approver: str = Field(min_length=2, max_length=60)
    reason: str = Field(default="", max_length=300)


@app.post("/api/plans/{plan_id}/order", dependencies=[Depends(tool_limit)])
def order_freeze(plan_id: str, req: OrderRequest) -> dict:
    """A person approves a verified plan. Not a tool and not in MCP: the model cannot call it."""
    return _order_call(toolbox().order_freeze, plan_id, req.approver, req.reason)


def _order_call(fn, *args) -> dict:
    try:
        return fn(*args)
    except ToolError as e:
        status = 404 if e.code.startswith("UNKNOWN") else 409 if e.code in ("STALE_EVIDENCE", "NOT_VERIFIED", "ORDER_WITHDRAWN") else 400
        return error_response(status, e.code, e.message, e.hint)


@app.post("/api/orders/{order_id}/dispatch", dependencies=[Depends(tool_limit)])
def retry_dispatch(order_id: str) -> dict:
    """Send a queued freeze request again. HTTP only, like the approval: not a tool and not in MCP."""
    return _order_call(toolbox().retry_dispatch, order_id)


@app.delete("/api/orders/{order_id}", dependencies=[Depends(tool_limit)])
def withdraw_order(order_id: str, req: OrderRequest) -> dict:
    """A named person withdraws an order: holds are lifted and the withdrawal is recorded."""
    return _order_call(toolbox().withdraw_order, order_id, req.approver, req.reason)


@app.get("/api/queries")
def queries() -> dict:
    """The fixed Cypher behind each tool (static text; no database call)."""
    from .queries import QUERIES

    return {"queries": QUERIES}


@app.get("/api/cases/{case_id}")
def resume(case_id: str) -> dict:
    result = toolbox().call("resume_case", {"case_id": case_id})
    if "error" in result:
        raise HTTPException(404 if result["error"]["code"].startswith("UNKNOWN") else 400, result["error"]["message"])
    return result


@app.get("/api/cases/{case_id}/graph")
def graph(case_id: str, plan_id: str | None = None) -> dict:
    try:
        return toolbox().graph_view(case_id, plan_id)
    except Exception as e:
        http = _typed(e)
        if http is None:
            raise
        raise http from e


# ---------------------------------------------------------------------------------------------- agent runs
class RunTimeout(Exception):
    pass


def _execute(run_id: str, st: dict, req: RunRequest) -> None:
    t0 = time.time()
    outcome = "ok"

    def on_event(ev: dict) -> None:
        if time.time() - t0 > RUN_TIMEOUT_S:
            raise RunTimeout(f"the run took longer than {int(RUN_TIMEOUT_S)} s and was stopped")
        st["events"].append(ev)

    try:
        mode = req.mode
        llm = LLMClient(mode=mode, cache_dir=REPLAY_DIR if mode == "replay" else None, attempts=LIVE_ATTEMPTS)
        extra = {"system": AUDITOR_PROMPT, "specs": AUDITOR_SPECS} if req.role == "auditor" else {}
        run = run_agent(req.task, toolbox(), llm, on_event=on_event, **extra)
        if run.stopped == "llm_error" and mode == "live" and req.role == "investigator" and EXAMPLE_TASK.fullmatch(req.task):
            # the provider is rate-limited or down: say so, then play the saved run for this example alert instead
            st["events"].append({"type": "fallback", "t": time.time(),
                                 "message": "Live model unavailable right now, showing a recorded run."})
            mode = "replay"
            run = run_agent(req.task, toolbox(), LLMClient(mode="replay", cache_dir=REPLAY_DIR), on_event=on_event)
        outcome = run.stopped or "ok"
        st["events"].append({"type": "card", "card": answer_card(run), "stopped": run.stopped,
                             "cached_calls": run.cached_calls, "mode": mode, "t": time.time()})
    except RedisTimeoutError as e:  # a query outlived the socket timeout; the database itself may be fine
        outcome = "db_timeout"
        st["events"].append({"type": "error", "t": time.time(),
                             "message": "The database took too long to answer. Try again, or use a shorter window."})
        log.warning("run=%s database timeout: %s", run_id, e)
    except RedisConnectionError as e:
        outcome = "db_unavailable"
        st["events"].append({"type": "error", "t": time.time(),
                             "message": "The graph database is unavailable or still loading. Try again in a minute."})
        log.warning("run=%s database unavailable: %s", run_id, e)
    except Exception as e:  # never leave a stream hanging
        outcome = type(e).__name__
        if not isinstance(e, RunTimeout):
            log.exception("run=%s failed", run_id)
        st["events"].append({"type": "error", "message": f"{type(e).__name__}: {e}", "t": time.time()})
    finally:
        st["done"] = True
        _run_slots.release()
        log.info("run=%s mode=%s role=%s outcome=%s seconds=%.1f", run_id, req.mode, req.role, outcome,
                 time.time() - t0)


def _evict_finished_runs() -> None:
    """Keep memory bounded, never dropping a run that is still streaming."""
    with _runs_lock:
        extra = len(_runs) - MAX_RUNS_KEPT
        if extra > 0:
            for k in sorted((k for k, v in _runs.items() if v["done"]), key=lambda k: _runs[k]["created"])[:extra]:
                del _runs[k]


@app.post("/api/runs", dependencies=[Depends(run_limit)], response_model=None)
def start_run(req: RunRequest) -> JSONResponse | dict:
    if req.mode == "live" and not live_available():
        return error_response(503, "LIVE_UNAVAILABLE", "This server has no language-model key, so live runs are off.",
                              "Use Replay (saved runs) or Tools only.")
    if not _run_slots.acquire(blocking=False):
        raise HTTPException(429, "too many runs in progress; try again in a moment", headers={"Retry-After": "10"})
    run_id = uuid.uuid4().hex[:10]
    st = {"events": [], "done": False, "created": time.time()}
    with _runs_lock:
        _runs[run_id] = st
    try:
        threading.Thread(target=_execute, args=(run_id, st, req), daemon=True).start()
    except Exception:
        _run_slots.release()
        raise
    _evict_finished_runs()
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    st = _runs.get(run_id)
    if st is None:
        raise HTTPException(404, "unknown run")
    try:  # a reconnecting EventSource sends the id of the last event it saw
        start = max(0, int(request.headers.get("last-event-id", "-1")) + 1)
    except ValueError:
        start = 0

    async def stream():
        i, idle = start, 0.0
        yield "retry: 2000\n\n"
        while True:
            while i < len(st["events"]):
                yield f"id: {i}\ndata: {json.dumps(st['events'][i], default=str)}\n\n"
                i, idle = i + 1, 0.0
            if st["done"] and i >= len(st["events"]):
                yield "event: end\ndata: {}\n\n"
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.15)
            idle += 0.15
            if idle >= SSE_PING_S:  # keeps proxies from closing an idle stream while the model thinks
                yield ": ping\n\n"
                idle = 0.0

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


mimetypes.add_type("image/webp", ".webp")  # some Python builds lack it, and the landing page serves a .webp
# Serve the built web app (npm run build) from the same process, after every API route is registered.
_DIST = PROJECT_ROOT / "web" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="web")
