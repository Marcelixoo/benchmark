"""Runs pipeline steps as subprocesses (the exact `python -m scripts.x`
invocation a human would type), captures their stdout/stderr live, and tracks
run status. This is the only place that shells out to scripts/ — everything
else in benchmark_api and its callers goes through the run/status/log API.

Status and logs are write-through'd to `run_state` (plain JSON files on
disk) so a run started by *this* process can still be observed — status
polled, logs tailed — from any other process (a separate `cli.py run ...`
invocation, another server instance, etc). The in-memory `_Run` bookkeeping
below remains the fast path for a run's own process; `run_state` is what
makes it visible everywhere else.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator

from . import run_state
from .models import LogLine, RunHandle, StepStatus
from .registry import StepDefinition

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FOREIGN_LOG_POLL_S = 0.4


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

    def append_line(self, stream: str, text: str) -> LogLine:
        with self._lock:
            line = LogLine(run_id=self.run_id, seq=len(self._lines), stream=stream, text=text, ts=_now())
            self._lines.append(line)
            for q in self._subscribers:
                q.put(line)
            return line

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

        # PYTHONUNBUFFERED matters for live tailing specifically: stderr is
        # already unbuffered by default in CPython, but stdout is
        # block-buffered whenever it isn't a tty (i.e. always, once piped
        # here) — without this, a script's stdout prints would only reach
        # us in one big flush right before it exits, defeating the point
        # of a *live* log panel regardless of which process is watching.
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        process = subprocess.Popen(
            argv,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

        with self._lock:
            self._runs[run_id] = run
            self._latest_by_step[(step.id, system)] = run_id

        run_state.start_run(step.id, system, run_id, process.pid)

        threading.Thread(target=self._pump, args=(run, process.stdout, "stdout"), daemon=True).start()
        threading.Thread(target=self._pump, args=(run, process.stderr, "stderr"), daemon=True).start()
        threading.Thread(target=self._watch, args=(run, process), daemon=True).start()

        return RunHandle(run_id=run_id, step_id=step.id, system=system)

    @staticmethod
    def _pump(run: _Run, pipe, stream: str) -> None:
        for raw_line in iter(pipe.readline, ""):
            line = run.append_line(stream, raw_line.rstrip("\n"))
            run_state.append_log_line(run.run_id, line.stream, line.text, line.seq, line.ts)
        pipe.close()

    @staticmethod
    def _watch(run: _Run, process: subprocess.Popen) -> None:
        returncode = process.wait()
        status = StepStatus.DONE if returncode == 0 else StepStatus.FAILED
        # Persist the terminal state BEFORE waking up in-memory subscribers
        # (run.finish below). Callers that block on the log stream (e.g.
        # cli.py's `run` command) resume the instant run.finish fires and
        # immediately call get_step_status — which now only reads
        # run_state. Writing run_state first guarantees they never observe
        # the narrow "process already dead, but still marked running"
        # window that would otherwise get self-healed into a false
        # "crashed" / failed status by get_step_status's liveness check.
        run_state.finish_run(run.step_id, run.system, run.run_id, status.value)
        run.finish(status)

    def get_run(self, run_id: str) -> _Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def latest_run_for(self, step_id: str, system: str | None) -> _Run | None:
        with self._lock:
            run_id = self._latest_by_step.get((step_id, system))
        return self._runs.get(run_id) if run_id else None

    def iter_log(self, run_id: str) -> Iterator[LogLine]:
        run = self.get_run(run_id)
        if run is not None:
            buffered, q = run.subscribe()
            yield from buffered
            while True:
                item = q.get()
                if item is None:
                    break
                yield item
            return

        # Not owned by this process — tail the persisted log file instead,
        # so a run started elsewhere (another `cli.py run`, another server
        # instance) is still watchable live from here.
        yield from self._iter_foreign_log(run_id)

    @staticmethod
    def _iter_foreign_log(run_id: str) -> Iterator[LogLine]:
        if not run_state.run_log_exists(run_id):
            return
        offset = 0
        while True:
            lines, offset = run_state.read_new_log_lines(run_id, offset)
            for raw in lines:
                yield LogLine(**raw)
            record = run_state.read_run(run_id)
            if record is not None and record.get("status") != "running":
                # One more drain in case lines landed between the read
                # above and the status check.
                lines, offset = run_state.read_new_log_lines(run_id, offset)
                for raw in lines:
                    yield LogLine(**raw)
                break
            time.sleep(_FOREIGN_LOG_POLL_S)

    def run_exists(self, run_id: str) -> bool:
        return self.get_run(run_id) is not None or run_state.read_run(run_id) is not None


job_manager = JobManager()
