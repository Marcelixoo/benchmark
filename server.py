"""FastAPI server over benchmark_api — thin HTTP wrapper, same public
functions the CLI calls. See docs/api-contract.md for the full HTTP contract
consumed by the frontend.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

from benchmark_api import api

app = FastAPI(title="Benchmark API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    system: str | None = None
    calibration: bool = False


@app.get("/api/steps")
def list_steps():
    return [asdict(s) for s in api.list_steps()]


@app.get("/api/systems")
def list_systems():
    return [asdict(s) for s in api.list_systems()]


@app.get("/api/config")
def get_config():
    return asdict(api.get_config())


@app.get("/api/steps/{step_id}/status")
def get_step_status(step_id: str, system: str | None = None):
    try:
        return asdict(api.get_step_status(step_id, system))
    except api.UnknownStepError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/steps/{step_id}/run", status_code=202)
def run_step(step_id: str, body: RunRequest):
    try:
        return asdict(api.run_step(step_id, body.system, calibration=body.calibration))
    except api.UnknownStepError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except api.StepBlockedError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str):
    async def event_generator():
        loop = asyncio.get_event_loop()
        iterator = api.stream_step_output(run_id)
        while True:
            line = await loop.run_in_executor(None, lambda: next(iterator, None))
            if line is None:
                break
            yield {"data": json.dumps(asdict(line))}

    return EventSourceResponse(event_generator())


@app.get("/api/steps/{step_id}/report")
def get_report(step_id: str, system: str | None = None):
    try:
        return asdict(api.get_report(step_id, system))
    except api.UnknownStepError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/steps/{step_id}/compare")
def compare_systems(step_id: str):
    try:
        return asdict(api.compare_systems(step_id))
    except api.UnknownStepError as e:
        raise HTTPException(status_code=404, detail=str(e))
