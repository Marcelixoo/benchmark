"""The public API for the benchmark pipeline — the only module the CLI and
the FastAPI server import from. Wraps the registry (what steps exist, how to
tell if one is blocked) and the runner (subprocess execution + log
streaming) behind a small set of operations shaped around what a UI needs:
list steps/systems, check status, kick off a run, stream its output, and
read back the report it produced.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterator

from scripts.lib.config import load_config

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
from .registry import STEPS, STEPS_BY_ID, StepDefinition
from .runner import job_manager

KNOWN_SYSTEMS = ("local_index", "shared_index")


class UnknownStepError(Exception):
    pass


class StepBlockedError(Exception):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _get_step(step_id: str) -> StepDefinition:
    step = STEPS_BY_ID.get(step_id)
    if step is None:
        raise UnknownStepError(f"Unknown step '{step_id}'. Known steps: {', '.join(STEPS_BY_ID)}")
    return step


def list_steps() -> list[StepInfo]:
    return [StepInfo(id=s.id, label=s.label, needs_system=s.needs_system, depends_on=s.depends_on) for s in STEPS]


def list_systems() -> list[SystemInfo]:
    config = load_config()
    systems = []
    for system_id, system_config in config["systems"].items():
        write_url = system_config.get("write_base_url")
        search_url = system_config.get("search_base_url")
        systems.append(
            SystemInfo(
                id=system_id,
                label=system_id.replace("_", " ").title(),
                write_base_url=write_url,
                search_base_url=search_url,
                configured=bool(write_url and search_url),
            )
        )
    return systems


def get_config() -> BenchmarkConfig:
    config = load_config()
    return BenchmarkConfig(
        seed=config["seed"],
        systems=list_systems(),
        paths=config["paths"],
        workload=config["workload"],
    )


def get_step_status(step_id: str, system: str | None = None) -> StepState:
    step = _get_step(step_id)
    config = load_config()

    run = job_manager.latest_run_for(step_id, system)
    if run is not None and run.status == StepStatus.RUNNING:
        return StepState(
            step_id=step_id,
            status=StepStatus.RUNNING,
            system=system,
            run_id=run.run_id,
            blocked_reason=None,
            last_report_path=None,
            updated_at=run.updated_at,
        )

    report_path = step.report_path(config, system, False)
    has_report = report_path is not None and report_path.exists()

    if run is not None:
        status = run.status
        blocked_reason = None
    else:
        blocked_reason = step.check_blocked(config, system)
        status = StepStatus.BLOCKED if blocked_reason else (StepStatus.DONE if has_report else StepStatus.NOT_RUN)

    return StepState(
        step_id=step_id,
        status=status,
        system=system,
        run_id=run.run_id if run else None,
        blocked_reason=blocked_reason,
        last_report_path=str(report_path) if has_report else None,
        updated_at=run.updated_at if run else _now(),
    )


def run_step(step_id: str, system: str | None = None, calibration: bool = False) -> RunHandle:
    step = _get_step(step_id)

    if step.needs_system:
        if not system:
            raise StepBlockedError(f"Step '{step_id}' requires a --system (one of {KNOWN_SYSTEMS}).")
        if system not in KNOWN_SYSTEMS:
            raise StepBlockedError(f"Unknown system '{system}'. Expected one of {KNOWN_SYSTEMS}.")

    config = load_config()
    blocked_reason = step.check_blocked(config, system)
    if blocked_reason:
        raise StepBlockedError(blocked_reason)

    return job_manager.start(step, system, calibration=calibration)


def stream_step_output(run_id: str) -> Iterator[LogLine]:
    return job_manager.iter_log(run_id)


def get_report(step_id: str, system: str | None = None) -> Report:
    step = _get_step(step_id)
    config = load_config()
    path = step.report_path(config, system, False)
    if path is None or not path.exists():
        suffix = f" (system={system})" if system else ""
        raise FileNotFoundError(f"No report found for step '{step_id}'{suffix}.")
    with open(path) as f:
        data = json.load(f)
    return Report(step_id=step_id, system=system, path=str(path), data=data)


def compare_systems(step_id: str) -> Comparison:
    _get_step(step_id)
    systems: dict[str, Any] = {}
    for system_id in KNOWN_SYSTEMS:
        try:
            systems[system_id] = get_report(step_id, system_id).data
        except FileNotFoundError:
            systems[system_id] = None
    return Comparison(step_id=step_id, systems=systems)
