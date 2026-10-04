from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from traceweaver.api.graph import make_router
from traceweaver.eval.runner import compare_task, load_tasks, run_task
from traceweaver.runtime import TraceWeaverRuntime

DASHBOARD_DIST = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
DASHBOARD_HTML = Path(__file__).resolve().parent / "dashboard.html"


def create_app(runtime: TraceWeaverRuntime | None = None) -> FastAPI:
    runtime = runtime or TraceWeaverRuntime()
    app = FastAPI(title="TraceWeaver Runtime", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def _push(event: dict) -> None:
        try:
            queue.put_nowait(event)
        except Exception:
            pass

    runtime.collector.add_listener(_push)
    app.state.runtime = runtime
    app.include_router(make_router(runtime))

    @app.get("/api/health")
    def health():
        return {"ok": True, "repo": str(runtime.cfg.repo_root)}

    @app.get("/api/lake")
    def lake_view():
        from traceweaver.lake.pipeline import lake_summary

        return lake_summary(runtime.cfg)

    @app.get("/api/audit")
    def audit_view():
        from traceweaver.audit import load_audit_for_dashboard

        return load_audit_for_dashboard(runtime.cfg)

    @app.get("/api/debt")
    def debt_view():
        from traceweaver.graphalg.debt import debt_for_runtime

        return debt_for_runtime(runtime)

    @app.get("/api/clones")
    def clones_view():
        from traceweaver.graphalg.clones import clones_for_runtime

        return clones_for_runtime(runtime)

    @app.get("/api/vectors")
    def vectors():
        from traceweaver.index.embeddings import vector_status

        return vector_status(runtime.cfg, runtime.conn)

    @app.get("/api/state")
    def state(session_id: str | None = None):
        return runtime.dashboard_state(session_id)

    @app.get("/api/events")
    def events(session_id: str | None = None, limit: int = 400):
        return runtime.collector.list_events(session_id, limit)

    @app.get("/api/working-set")
    def working_set(session_id: str | None = None):
        sid = session_id or runtime.latest_session()
        return runtime.controller.working_set(sid) if sid else []

    @app.get("/api/metrics")
    def metrics(session_id: str | None = None):
        sid = session_id or runtime.latest_session()
        return runtime.controller.metrics(sid) if sid else {}

    @app.post("/api/session")
    def new_session(agent: str = "claude", condition: str = "traceweaver", task_id: str | None = None):
        sid = runtime.new_session(agent=agent, condition=condition, task_id=task_id)
        return {"session_id": sid}

    @app.post("/api/context")
    def context(objective: str, seed: str | None = None, budget: int | None = None):
        sid = runtime.ensure_session()
        return runtime.controller.build_bundle(sid, objective=objective, seed=seed, budget=budget)

    @app.get("/api/tasks")
    def tasks():
        return load_tasks(runtime.cfg.repo_root)

    @app.post("/api/eval/run")
    def eval_run(task_id: str = Query(...), condition: str = Query("traceweaver")):
        tasks = {t["id"]: t for t in load_tasks(runtime.cfg.repo_root)}
        if task_id not in tasks:
            return {"error": "unknown task"}
        return run_task(runtime, tasks[task_id], use_traceweaver=condition != "baseline")

    @app.post("/api/eval/compare")
    def eval_compare(task_id: str = Query("renewal-discount")):
        tasks = {t["id"]: t for t in load_tasks(runtime.cfg.repo_root)}
        if task_id not in tasks:
            return {"error": "unknown task"}
        return compare_task(runtime.cfg, tasks[task_id])

    @app.get("/api/stream")
    async def stream():
        async def gen():
            # snapshot first
            yield f"data: {json.dumps({'type': 'snapshot', 'state': runtime.dashboard_state()})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield f"data: {json.dumps({'type': 'event', 'event': event})}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    dist_index = DASHBOARD_DIST / "index.html"
    if dist_index.is_file():
        assets = DASHBOARD_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        def index():
            return FileResponse(dist_index)
    else:

        @app.get("/")
        def index():
            return HTMLResponse(DASHBOARD_HTML.read_text(encoding="utf-8"))

    return app
