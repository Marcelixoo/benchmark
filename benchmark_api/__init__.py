"""Public package surface — the only import path the CLI and the FastAPI
server are allowed to use (`from benchmark_api import ...`), per the
architectural constraint that nothing outside this package reaches into
`scripts/lib/*` or the script modules directly.

Re-exports only; all logic lives in `api.py`/`registry.py`/`runner.py`.
"""
from __future__ import annotations

from .api import (
    StepBlockedError,
    UnknownStepError,
    compare_systems,
    get_config,
    get_report,
    get_step_status,
    list_steps,
    list_systems,
    run_exists,
    run_step,
    stream_step_output,
)
from .models import (
    BenchmarkConfig,
    Comparison,
    LogLine,
    Report,
    RunHandle,
    StepInfo,
    StepState,
    StepStatus,
    SystemInfo,
)

__all__ = [
    "BenchmarkConfig",
    "Comparison",
    "LogLine",
    "Report",
    "RunHandle",
    "StepBlockedError",
    "StepInfo",
    "StepState",
    "StepStatus",
    "SystemInfo",
    "UnknownStepError",
    "compare_systems",
    "get_config",
    "get_report",
    "get_step_status",
    "list_steps",
    "list_systems",
    "run_exists",
    "run_step",
    "stream_step_output",
]
