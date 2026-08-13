"""Runs pipeline steps as subprocesses (the exact `python -m scripts.x`
invocation a human would type), captures their stdout/stderr live, and tracks
run status. This is the only place that shells out to scripts/ — everything
else in benchmark_api and its callers goes through the run/status/log API.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator

from .models import LogLine, RunHandle, StepStatus
from .registry import StepDefinition

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _Run:
    def __init__(self, run_id: str, step_id: str, system: str | None) -> None:
        self.run_id = run_id
        self.step_id = step_id
        self.system = system
        self.status = StepStatus.RUNNING
        self.updated_at = _now()
        self._lock = threading.Lock()
        self._lines: list[LogLine] = []
        self._subscribers: list[queue.Queue] = []

    def append_line(self, stream: str, text: str) -> None:
        with self._lock:
            line = LogLine(run_id=self.run_id, seq=len(self._lines), stream=stream, text=text, ts=_now())
            self._lines.append(line)
            for q in self._subscribers:
                q.put(line)

    def finish(self, status: StepStatus) -> None:
        with self._lock:
            self.status = status
            self.updated_at = _now()
            for q in self._subscribers:
                q.put(None)

    def subscribe(self) -> tuple[list[LogLine], queue.Queue]:
        with self._lock:
            q: queue.Queue = queue.Queue()
            self._subscribers.append(q)
            if self.status != StepStatus.RUNNING:
                q.put(None)
            return list(self._lines), q


class JobManager:
    def __init__(self) -> None:
        self._runs: dict[str, _Run] = {}
        self._latest_by_step: dict[tuple[str, str | None], str] = {}
        self._lock = threading.Lock()

    def start(self, step: StepDefinition, system: str | None, calibration: bool = False) -> RunHandle:
        run_id = uuid.uuid4().hex[:12]
        run = _Run(run_id, step.id, system)

        argv = [sys.executable, "-m", step.module]
        if step.needs_system:
            argv += ["--system", system]
        if calibration and step.supports_calibration:
            argv += ["--calibration"]

        process = subprocess.Popen(
            argv,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        with self._lock:
            self._runs[run_id] = run
            self._latest_by_step[(step.id, system)] = run_id

        threading.Thread(target=self._pump, args=(run, process.stdout, "stdout"), daemon=True).start()
        threading.Thread(target=self._pump, args=(run, process.stderr, "stderr"), daemon=True).start()
        threading.Thread(target=self._watch, args=(run, process), daemon=True).start()

        return RunHandle(run_id=run_id, step_id=step.id, system=system)

    @staticmethod
    def _pump(run: _Run, pipe, stream: str) -> None:
        for raw_line in iter(pipe.readline, ""):
            run.append_line(stream, raw_line.rstrip("\n"))
        pipe.close()

    @staticmethod
    def _watch(run: _Run, process: subprocess.Popen) -> None:
        returncode = process.wait()
        run.finish(StepStatus.DONE if returncode == 0 else StepStatus.FAILED)

    def get_run(self, run_id: str) -> _Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def latest_run_for(self, step_id: str, system: str | None) -> _Run | None:
        with self._lock:
            run_id = self._latest_by_step.get((step_id, system))
        return self._runs.get(run_id) if run_id else None

    def iter_log(self, run_id: str) -> Iterator[LogLine]:
        run = self.get_run(run_id)
        if run is None:
            return
        buffered, q = run.subscribe()
        yield from buffered
        while True:
            item = q.get()
            if item is None:
                break
            yield item


job_manager = JobManager()
