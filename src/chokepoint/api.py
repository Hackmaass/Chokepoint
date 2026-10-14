"""REST + server-sent-events API. The LLM never sees Cypher and the API never accepts it.

  POST /api/runs               start an agent run (mode: live | replay)         -> {run_id}
  GET  /api/runs/{id}/events   SSE stream of the trace (replayable, reconnect-safe)
  POST /api/tools/{tool}       call one tool directly (tool-only mode, no LLM)
  GET  /api/cases              recent cases;  GET /api/cases/{id}  resume
  GET  /api/cases/{id}/graph   evidence subgraph for the screen (optional ?plan_id=)
  GET  /api/examples           curated example alerts
  GET  /api/health
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import AUDITOR_PROMPT, AUDITOR_SPECS, answer_card, run_agent
from .config import PROJECT_ROOT
from .db import connect
from .llm import REPLAY_DIR, LLMClient
from .toolbox import Toolbox

EXAMPLES = PROJECT_ROOT / "examples" / "examples.json"
MAX_CONCURRENT_RUNS = 2
TOOLS = {"open_case", "flow", "freeze_plan", "verify_plan", "resume_case", "explain_finding", "refresh_case",
         "audit_checks"}
DEMO = os.environ.get("DEMO_MODE", "1") != "0"  # simulated late transfers and reset (synthetic edits only)

app = FastAPI(title="Chokepoint", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["GET", "POST"], allow_headers=["*"])

_state: dict = {"tb": None}
_runs: dict[str, dict] = {}
_run_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)


def toolbox() -> Toolbox:
    if _state["tb"] is None:
        _state["tb"] = Toolbox(connect())
    return _state["tb"]


class RunRequest(BaseModel):
    task: str = Field(min_length=5, max_length=600)
    mode: str = Field(default="live", pattern="^(live|replay)$")
    role: str = Field(default="investigator", pattern="^(investigator|auditor)$")


@app.get("/api/health")
def health() -> dict:
    tb = toolbox()
    return {"ok": True, "latest_data_version": tb.latest_batch(), "demo": DEMO}


@app.get("/api/examples")
def examples() -> list[dict]:
    return json.loads(EXAMPLES.read_text(encoding="utf-8")) if EXAMPLES.exists() else []


@app.post("/api/tools/{tool}")
def call_tool(tool: str, args: dict) -> dict:
    if tool not in TOOLS:
        raise HTTPException(404, "unknown tool")
    return toolbox().call(tool, args)


@app.post("/api/demo/late-transfers/{case_id}")
def demo_late_transfers(case_id: str) -> dict:
    """Simulate late, back-dated transfers for a case (synthetic; creates a route that bypasses the plan)."""
    if not DEMO:
        raise HTTPException(403, "demo mode is off")
    try:
        return toolbox().inject_late_transfers(case_id)
    except Exception as e:
        raise HTTPException(400, getattr(e, "message", str(e))) from e


@app.post("/api/demo/reset")
def demo_reset() -> dict:
    if not DEMO:
        raise HTTPException(403, "demo mode is off")
    return toolbox().reset_demo_state()


@app.get("/api/cases")
def cases(limit: int = 20) -> list[dict]:
    return toolbox().list_cases(min(max(limit, 1), 50))


@app.get("/api/cases/{case_id}")
def resume(case_id: str) -> dict:
    return toolbox().call("resume_case", {"case_id": case_id})


@app.get("/api/cases/{case_id}/graph")
def graph(case_id: str, plan_id: str | None = None) -> dict:
    try:
        return toolbox().graph_view(case_id, plan_id)
    except Exception as e:  # typed tool errors and unknown ids
        code = getattr(e, "code", "ERROR")
        raise HTTPException(404 if code.startswith("UNKNOWN") else 400, getattr(e, "message", str(e))) from e


def _execute(run_id: str, req: RunRequest) -> None:
    st = _runs[run_id]
    try:
        llm = LLMClient(mode=req.mode, cache_dir=REPLAY_DIR if req.mode == "replay" else None)
        extra = {"system": AUDITOR_PROMPT, "specs": AUDITOR_SPECS} if req.role == "auditor" else {}
        run = run_agent(req.task, toolbox(), llm, on_event=lambda ev: st["events"].append(ev), **extra)
        st["events"].append({"type": "card", "card": answer_card(run), "stopped": run.stopped,
                             "cached_calls": run.cached_calls, "mode": req.mode, "t": time.time()})
    except Exception as e:  # never leave a stream hanging
        st["events"].append({"type": "error", "message": f"{type(e).__name__}: {e}", "t": time.time()})
    finally:
        st["done"] = True
        _run_slots.release()


@app.post("/api/runs")
def start_run(req: RunRequest) -> dict:
    if not _run_slots.acquire(blocking=False):
        raise HTTPException(429, "too many runs in progress; try again in a moment")
    run_id = uuid.uuid4().hex[:10]
    _runs[run_id] = {"events": [], "done": False, "created": time.time()}
    if len(_runs) > 50:  # keep memory bounded
        for k in sorted(_runs, key=lambda k: _runs[k]["created"])[:10]:
            _runs.pop(k, None)
    threading.Thread(target=_execute, args=(run_id, req), daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    st = _runs.get(run_id)
    if st is None:
        raise HTTPException(404, "unknown run")

    async def stream():
        i = 0
        while True:
            while i < len(st["events"]):
                yield f"data: {json.dumps(st['events'][i], default=str)}\n\n"
                i += 1
            if st["done"] and i >= len(st["events"]):
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(0.15)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Serve the built web app (npm run build) from the same process, after every API route is registered.
_DIST = PROJECT_ROOT / "web" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="web")
