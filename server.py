"""FastAPI server — thin HTTP adapter over `benchmark_api`.

Implements exactly the 8 endpoints documented in docs/api-contract.md. No
business logic lives here: every route is a near-trivial call into
`benchmark_api` plus response/error formatting. This module (like cli.py)
imports only from the `benchmark_api` package — never from `scripts/lib`
or the script modules directly.

Run with:

    uvicorn server:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import queue as thread_queue
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import benchmark_api as api

app = FastAPI(title="Benchmark API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _jsonable(obj: Any) -> Any:
    return dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj


class RunRequest(BaseModel):
    system: str | None = None
    calibration: bool = False


@app.get("/api/steps")
def list_steps() -> list[dict]:
    return [_jsonable(s) for s in api.list_steps()]


@app.get("/api/systems")
def list_systems() -> list[dict]:
    return [_jsonable(s) for s in api.list_systems()]


@app.get("/api/config")
def get_config() -> dict:
    return _jsonable(api.get_config())


@app.get("/api/steps/{step_id}/status")
def get_step_status(step_id: str, system: str | None = Query(default=None)) -> dict:
    try:
        return _jsonable(api.get_step_status(step_id, system))
    except api.UnknownStepError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@app.post("/api/steps/{step_id}/run", status_code=202)
def run_step(step_id: str, body: RunRequest | None = None) -> dict:
    body = body or RunRequest()
    try:
        handle = api.run_step(step_id, body.system, calibration=body.calibration)
    except api.UnknownStepError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except api.StepBlockedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return _jsonable(handle)


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str) -> EventSourceResponse:
    # api.run_exists checks both this process's in-memory runs and the
    # cross-process run_state on disk, so a run_id for a step started via
    # `cli.py run ...` in another terminal streams here too — not just
    # ones this exact server instance happened to launch.
    if not api.run_exists(run_id):
        raise HTTPException(status_code=404, detail=f"Unknown run '{run_id}'.")

    async def event_generator():
        loop = asyncio.get_event_loop()
        log_iter: Iterator = api.stream_step_output(run_id)
        q: thread_queue.Queue = thread_queue.Queue()

        def pump() -> None:
            try:
                for line in log_iter:
                    q.put(line)
            finally:
                q.put(None)

        loop.run_in_executor(None, pump)

        while True:
            item = await loop.run_in_executor(None, q.get)
            if item is None:
                break
            yield {"event": "message", "data": json.dumps(_jsonable(item))}

    return EventSourceResponse(event_generator())


@app.get("/api/steps/{step_id}/report")
def get_report(step_id: str, system: str | None = Query(default=None)) -> dict:
    try:
        return _jsonable(api.get_report(step_id, system))
    except api.UnknownStepError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@app.get("/api/steps/{step_id}/compare")
def compare_systems(step_id: str) -> dict:
    try:
        return _jsonable(api.compare_systems(step_id))
    except api.UnknownStepError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


# Serve the plain HTML/CSS/JS frontend, if present, as static files. Mounted
# last so it never shadows the /api/* routes above.
_WEB_DIR = Path(__file__).resolve().parent / "web"
if _WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
