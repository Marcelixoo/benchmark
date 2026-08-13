"""Data shapes returned by benchmark_api.api — the only types the CLI and the
FastAPI server exchange with the outside world. Plain dataclasses (not
pydantic) so this package stays framework-agnostic; server.py converts them
to JSON via dataclasses.asdict().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class StepStatus(str, Enum):
    NOT_RUN = "not_run"
    BLOCKED = "blocked"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class StepInfo:
    id: str
    label: str
    needs_system: bool
    depends_on: list[str] = field(default_factory=list)


@dataclass
class StepState:
    step_id: str
    status: StepStatus
    system: str | None
    run_id: str | None
    blocked_reason: str | None
    last_report_path: str | None
    updated_at: str


@dataclass
class RunHandle:
    run_id: str
    step_id: str
    system: str | None


@dataclass
class LogLine:
    run_id: str
    seq: int
    stream: Literal["stdout", "stderr"]
    text: str
    ts: str


@dataclass
class Report:
    step_id: str
    system: str | None
    path: str
    data: dict[str, Any]


@dataclass
class SystemInfo:
    id: str
    label: str
    write_base_url: str | None
    search_base_url: str | None
    configured: bool


@dataclass
class Comparison:
    step_id: str
    systems: dict[str, dict[str, Any] | None]


@dataclass
class BenchmarkConfig:
    seed: int
    systems: list[SystemInfo]
    paths: dict[str, Any]
    workload: dict[str, Any]
