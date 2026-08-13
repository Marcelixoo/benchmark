"""Typer CLI over benchmark_api — a thin wrapper, same public functions the
FastAPI server calls. `python -m scripts.x` still works unchanged; this is
just the documented, higher-level way to drive the pipeline.
"""
from __future__ import annotations

import json

import typer

from benchmark_api import api
from benchmark_api.models import StepStatus

app = typer.Typer(add_completion=False, help="Benchmark pipeline CLI")


@app.command("list")
def list_steps() -> None:
    """List all pipeline steps and their current status."""
    for step in api.list_steps():
        if step.needs_system:
            for system in api.KNOWN_SYSTEMS:
                state = api.get_step_status(step.id, system)
                typer.echo(f"{step.id:<24} {system:<14} {state.status.value}")
        else:
            state = api.get_step_status(step.id)
            typer.echo(f"{step.id:<24} {'':<14} {state.status.value}")


@app.command("systems")
def list_systems() -> None:
    """List configured systems."""
    for system in api.list_systems():
        typer.echo(f"{system.id}: configured={system.configured} write={system.write_base_url} search={system.search_base_url}")


@app.command("config")
def show_config() -> None:
    """Print the resolved benchmark config."""
    from dataclasses import asdict

    typer.echo(json.dumps(asdict(api.get_config()), indent=2))


@app.command("status")
def status(step_id: str, system: str = typer.Option(None, "--system")) -> None:
    """Show the status of a step."""
    try:
        state = api.get_step_status(step_id, system)
    except api.UnknownStepError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    from dataclasses import asdict

    typer.echo(json.dumps(asdict(state), indent=2))


@app.command("run")
def run(
    step_id: str,
    system: str = typer.Option(None, "--system"),
    calibration: bool = typer.Option(False, "--calibration"),
    follow: bool = typer.Option(True, "--follow/--no-follow"),
) -> None:
    """Run a pipeline step, optionally streaming its live output."""
    try:
        handle = api.run_step(step_id, system, calibration=calibration)
    except (api.UnknownStepError, api.StepBlockedError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    typer.echo(f"Started run {handle.run_id}")
    if not follow:
        return

    for line in api.stream_step_output(handle.run_id):
        typer.echo(f"[{line.stream}] {line.text}")

    state = api.get_step_status(step_id, system)
    if state.status == StepStatus.FAILED:
        typer.echo(f"Run {handle.run_id} failed.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Run {handle.run_id} finished: {state.status.value}")


@app.command("report")
def report(step_id: str, system: str = typer.Option(None, "--system")) -> None:
    """Print the JSON report produced by a step."""
    try:
        rep = api.get_report(step_id, system)
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(rep.data, indent=2))


@app.command("compare")
def compare(step_id: str) -> None:
    """Compare a step's report across both systems."""
    comparison = api.compare_systems(step_id)
    typer.echo(json.dumps(comparison.systems, indent=2))


if __name__ == "__main__":
    app()
