"""Cross-process run-state persistence.

Why this exists: `runner.JobManager` originally tracked runs purely in
memory, which meant only the process that *started* a run (e.g. the FastAPI
server backing a browser tab) could see it as "running" or tail its logs.
A run kicked off via `cli.py run ...` or a bare `python -m scripts.x` in a
separate terminal was invisible to the web UI until it finished and wrote
its report — the UI's whole purpose (watching a run/calibration live)
didn't work for anything not started from that exact browser session.

This module makes run status and logs observable from *any* process by
writing them to plain JSON files under `data/.runs/` — the same "just JSON
on disk" convention the rest of this project already uses for reports.
Two kinds of records, both cheap to read/write for a 2s poll cadence:

- `slots/<step_id>__<system>.json`: the *current* run for a given
  (step_id, system) pair — what `get_step_status` reads.
- `runs/<run_id>.json`: a status record addressable by run_id alone — what
  lets the log-stream endpoint find/tail a run regardless of who started
  it, or 404 on a run_id nobody knows about.
- `logs/<run_id>.jsonl`: one JSON line per LogLine, appended as the
  subprocess produces output, and tailed by other processes via a byte
  offset.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_STATE_DIR = PROJECT_ROOT / "data" / ".runs"
SLOTS_DIR = RUN_STATE_DIR / "slots"
RUNS_DIR = RUN_STATE_DIR / "runs"
LOGS_DIR = RUN_STATE_DIR / "logs"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slot_path(step_id: str, system: str | None) -> Path:
    return SLOTS_DIR / f"{step_id}__{system or 'none'}.json"


def _run_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def _log_path(run_id: str) -> Path:
    return LOGS_DIR / f"{run_id}.jsonl"


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)  # atomic on POSIX — readers never see a partial write


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def is_pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    except OSError:
        return False
    return True


def start_run(step_id: str, system: str | None, run_id: str, pid: int) -> None:
    now = _now()
    record = {
        "run_id": run_id,
        "step_id": step_id,
        "system": system,
        "status": "running",
        "pid": pid,
        "started_at": now,
        "updated_at": now,
    }
    _atomic_write_json(_slot_path(step_id, system), record)
    _atomic_write_json(_run_path(run_id), record)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _log_path(run_id).touch()


def finish_run(step_id: str, system: str | None, run_id: str, status: str) -> None:
    now = _now()
    run_record = _read_json(_run_path(run_id)) or {"run_id": run_id, "step_id": step_id, "system": system}
    run_record.update(status=status, pid=None, updated_at=now)
    _atomic_write_json(_run_path(run_id), run_record)

    # Only update the (step, system) slot if it still points at this run —
    # a newer run for the same slot may have started in the meantime.
    slot = read_slot(step_id, system)
    if slot is None or slot.get("run_id") == run_id:
        _atomic_write_json(_slot_path(step_id, system), run_record)


def mark_crashed(step_id: str, system: str | None) -> dict:
    """The process that owned a 'running' slot is gone without a clean
    finish (e.g. killed). Heal the record to 'failed' so status polling
    doesn't show a permanently-spinning chip."""
    slot = read_slot(step_id, system) or {"step_id": step_id, "system": system, "run_id": None}
    slot["status"] = "failed"
    slot["pid"] = None
    slot["updated_at"] = _now()
    _atomic_write_json(_slot_path(step_id, system), slot)
    if slot.get("run_id"):
        _atomic_write_json(_run_path(slot["run_id"]), slot)
    return slot


def read_slot(step_id: str, system: str | None) -> Optional[dict]:
    return _read_json(_slot_path(step_id, system))


def read_run(run_id: str) -> Optional[dict]:
    return _read_json(_run_path(run_id))


def run_log_exists(run_id: str) -> bool:
    return _log_path(run_id).exists()


def append_log_line(run_id: str, stream: str, text: str, seq: int, ts: str) -> None:
    path = _log_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"run_id": run_id, "seq": seq, "stream": stream, "text": text, "ts": ts}
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()


def read_new_log_lines(run_id: str, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read whatever's been appended to a run's log file since `offset`
    (a byte offset), returning the parsed lines and the new offset."""
    path = _log_path(run_id)
    if not path.exists():
        return [], offset
    with open(path) as f:
        f.seek(offset)
        raw = f.read()
        new_offset = f.tell()
    lines = [json.loads(chunk) for chunk in raw.splitlines() if chunk.strip()]
    return lines, new_offset
