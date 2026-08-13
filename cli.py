"""Benchmark pipeline CLI — thin adapter over `benchmark_api`.

Every command below is a near-trivial call into `benchmark_api` plus
table/text formatting; no business logic lives here (mirrors server.py
against the same API, per the "one clean public API" constraint).

Usage:

    python cli.py steps
    python cli.py systems
    python cli.py config show
    python cli.py status smoke_test --system local_index
    python cli.py run index_initial_corpus --system local_index --calibration
    python cli.py report smoke_test --system local_index
    python cli.py compare index_initial_corpus
"""
from __future__ import annotations

import dataclasses
import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

import benchmark_api as api

app = typer.Typer(help="Benchmark pipeline CLI - thin adapter over benchmark_api.", no_args_is_help=True)
config_app = typer.Typer(help="Read-only view of config/benchmark.yaml.")
app.add_typer(config_app, name="config")

console = Console()
err_console = Console(stderr=True)


def _die(message: str, code: int = 1) -> None:
    err_console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code=code)


def _asdict(obj):
    return dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj


@app.command("steps")
def steps_cmd() -> None:
    """List all pipeline steps."""
    table = Table(title="Pipeline steps")
    table.add_column("id")
    table.add_column("label")
    table.add_column("needs system")
    table.add_column("depends on")
    for step in api.list_steps():
        table.add_row(step.id, step.label, "yes" if step.needs_system else "no", ", ".join(step.depends_on) or "-")
    console.print(table)


@app.command("systems")
def systems_cmd() -> None:
    """List systems (local_index / shared_index) and whether they're configured."""
    table = Table(title="Systems")
    table.add_column("id")
    table.add_column("label")
    table.add_column("write_base_url")
    table.add_column("search_base_url")
    table.add_column("configured")
    for system in api.list_systems():
        table.add_row(
            system.id,
            system.label,
            system.write_base_url or "-",
            system.search_base_url or "-",
            "[green]yes[/green]" if system.configured else "[red]no[/red]",
        )
    console.print(table)


@config_app.command("show")
def config_show() -> None:
    """Print the read-only view of config/benchmark.yaml as JSON."""
    console.print_json(json.dumps(_asdict(api.get_config())))


@app.command("status")
def status_cmd(
    step_id: str = typer.Argument(..., help="Step id, e.g. smoke_test"),
    system: Optional[str] = typer.Option(None, "--system", help="local_index or shared_index"),
) -> None:
    """Show the current status of a step."""
    try:
        state = api.get_step_status(step_id, system)
    except api.UnknownStepError as e:
        _die(str(e))
        return

    color = {
        "done": "green",
        "failed": "red",
        "blocked": "yellow",
        "running": "cyan",
        "not_run": "white",
    }.get(state.status.value, "white")
    console.print(f"[bold]{state.step_id}[/bold] (system={state.system or '-'}): [{color}]{state.status.value}[/{color}]")
    if state.blocked_reason:
        console.print(f"  blocked_reason: {state.blocked_reason}")
    if state.run_id:
        console.print(f"  run_id: {state.run_id}")
    if state.last_report_path:
        console.print(f"  last_report_path: {state.last_report_path}")
    console.print(f"  updated_at: {state.updated_at}")


@app.command("run")
def run_cmd(
    step_id: str = typer.Argument(..., help="Step id, e.g. index_initial_corpus"),
    system: Optional[str] = typer.Option(None, "--system", help="local_index or shared_index"),
    calibration: bool = typer.Option(False, "--calibration", help="Label this run as calibration input"),
) -> None:
    """Run a step and live-tail its output until it finishes."""
    try:
        handle = api.run_step(step_id, system, calibration=calibration)
    except api.UnknownStepError as e:
        _die(str(e))
        return
    except api.StepBlockedError as e:
        _die(f"blocked - {e}")
        return

    console.print(f"[bold]Started[/bold] {handle.step_id} (system={handle.system or '-'}) run_id={handle.run_id}")
    for line in api.stream_step_output(handle.run_id):
        style = "red" if line.stream == "stderr" else "dim"
        console.print(f"[{style}]{line.text}[/{style}]")

    state = api.get_step_status(step_id, system)
    if state.status.value == "done":
        console.print(f"[bold green]done[/bold green] — {state.last_report_path or 'no report file for this step'}")
    elif state.status.value == "failed":
        err_console.print(f"[bold red]failed[/bold red] — run_id={handle.run_id}")
        raise typer.Exit(code=1)
    else:
        console.print(f"[bold]{state.status.value}[/bold]")


@app.command("report")
def report_cmd(
    step_id: str = typer.Argument(..., help="Step id, e.g. smoke_test"),
    system: Optional[str] = typer.Option(None, "--system", help="local_index or shared_index"),
) -> None:
    """Print a step's report (data/reports/*.json) as JSON."""
    try:
        report = api.get_report(step_id, system)
    except api.UnknownStepError as e:
        _die(str(e))
        return
    except FileNotFoundError as e:
        _die(str(e))
        return
    console.print_json(json.dumps(report.data, default=str))


@app.command("compare")
def compare_cmd(step_id: str = typer.Argument(..., help="Step id, e.g. index_initial_corpus")) -> None:
    """Print the latest-per-system comparison for a step as JSON."""
    try:
        comparison = api.compare_systems(step_id)
    except api.UnknownStepError as e:
        _die(str(e))
        return
    console.print_json(json.dumps(comparison.systems, default=str))


if __name__ == "__main__":
    app()
