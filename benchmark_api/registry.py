"""Static registry of pipeline steps: which script backs each step, whether it
needs a --system flag, and how to detect an unmet precondition (matching the
exact `Blocker: ...` file-existence checks already in each script) so the API
can report "blocked" without spending a subprocess launch. The scripts' own
Blocker checks remain the source of truth; this is a pre-flight convenience.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scripts.lib.config import data_dir, reports_dir


@dataclass
class StepDefinition:
    id: str
    label: str
    module: str
    needs_system: bool
    depends_on: list[str]
    # Returns a human-readable blocker reason, or None if runnable.
    check_blocked: Callable[[dict[str, Any], str | None], str | None]
    # Resolves the report file for this step, given config + system (may be
    # a fixed path or "latest file matching a prefix"); returns None if the
    # step doesn't produce a report file of its own (e.g. create_index).
    report_path: Callable[[dict[str, Any], str | None, bool], Path | None]
    supports_calibration: bool = False


def _no_blocker(config: dict[str, Any], system: str | None) -> str | None:
    return None


def _require_file(relative_to: Callable[[dict[str, Any]], Path], filename: str, hint: str):
    def check(config: dict[str, Any], system: str | None) -> str | None:
        path = relative_to(config) / filename
        if not path.exists():
            return f"{path} does not exist yet. Run `{hint}` first."
        return None

    return check


def _require_system_url(url_key: str):
    def check(config: dict[str, Any], system: str | None) -> str | None:
        if system is None:
            return "This step requires --system to be specified."
        system_config = config["systems"].get(system, {})
        if not system_config.get(url_key):
            return (
                f"'{system}' has no {url_key} configured in config/benchmark.yaml. "
                f"Bring up its OpenSearch stack under infra/ first."
            )
        return None

    return check


def _combine(*checks: Callable[[dict[str, Any], str | None], str | None]):
    def check(config: dict[str, Any], system: str | None) -> str | None:
        for c in checks:
            reason = c(config, system)
            if reason:
                return reason
        return None

    return check


def _fixed_report(dir_fn: Callable[[dict[str, Any]], Path], filename_fn: Callable[[str | None, bool], str]):
    def resolve(config: dict[str, Any], system: str | None, calibration: bool) -> Path | None:
        return dir_fn(config) / filename_fn(system, calibration)

    return resolve


def _latest_report(dir_fn: Callable[[dict[str, Any]], Path], prefix_fn: Callable[[str | None, bool], str]):
    def resolve(config: dict[str, Any], system: str | None, calibration: bool) -> Path | None:
        directory = dir_fn(config)
        prefix = prefix_fn(system, calibration)
        candidates = sorted(directory.glob(f"{prefix}*.json"), key=lambda p: p.stat().st_mtime)
        return candidates[-1] if candidates else None

    return resolve


STEPS: list[StepDefinition] = [
    StepDefinition(
        id="inspect_products",
        label="Inspect product corpus",
        module="scripts.inspect_products",
        needs_system=False,
        depends_on=[],
        check_blocked=_no_blocker,
        report_path=_fixed_report(reports_dir, lambda system, calibration: "product_corpus_report.json"),
    ),
    StepDefinition(
        id="inspect_queries",
        label="Inspect query corpus",
        module="scripts.inspect_queries",
        needs_system=False,
        depends_on=[],
        check_blocked=_no_blocker,
        report_path=_fixed_report(reports_dir, lambda system, calibration: "query_corpus_report.json"),
    ),
    StepDefinition(
        id="prepare_products",
        label="Prepare 80/20 product split",
        module="scripts.prepare_products",
        needs_system=False,
        depends_on=[],
        check_blocked=_no_blocker,
        report_path=_fixed_report(data_dir, lambda system, calibration: "split_metadata.json"),
    ),
    StepDefinition(
        id="prepare_queries",
        label="Prepare fixed query set",
        module="scripts.prepare_queries",
        needs_system=False,
        depends_on=["prepare_products"],
        check_blocked=_require_file(data_dir, "corpus_initial.parquet", "benchmark run prepare_products"),
        report_path=_fixed_report(data_dir, lambda system, calibration: "query_metadata.json"),
    ),
    StepDefinition(
        id="validate_compatibility",
        label="Validate cross-dataset compatibility",
        module="scripts.validate_compatibility",
        needs_system=False,
        depends_on=["prepare_products"],
        check_blocked=_require_file(data_dir, "corpus_initial.parquet", "benchmark run prepare_products"),
        report_path=_fixed_report(reports_dir, lambda system, calibration: "compatibility_report.json"),
    ),
    StepDefinition(
        id="create_index",
        label="Create OpenSearch index",
        module="scripts.opensearch.create_index",
        needs_system=True,
        depends_on=[],
        check_blocked=_require_system_url("write_base_url"),
        report_path=lambda config, system, calibration: None,
    ),
    StepDefinition(
        id="smoke_test",
        label="Smoke-test search endpoint",
        module="scripts.smoke_test",
        needs_system=True,
        depends_on=["prepare_queries", "create_index"],
        check_blocked=_combine(
            _require_system_url("search_base_url"),
            _require_file(
                data_dir,
                "queries_fixed_5000_corpus_relevant.parquet",
                "benchmark run prepare_queries",
            ),
        ),
        report_path=_fixed_report(
            reports_dir,
            lambda system, calibration: f"calibration_queries_{system}.json" if calibration else f"smoke_test_{system}.json",
        ),
        supports_calibration=True,
    ),
    StepDefinition(
        id="index_initial_corpus",
        label="Index initial corpus",
        module="scripts.index_initial_corpus",
        needs_system=True,
        depends_on=["prepare_products", "create_index"],
        check_blocked=_combine(
            _require_system_url("write_base_url"),
            _require_file(data_dir, "corpus_initial.parquet", "benchmark run prepare_products"),
        ),
        report_path=_latest_report(
            reports_dir,
            lambda system, calibration: f"{'calibration_index' if calibration else 'index_run'}_{system}_",
        ),
    ),
    StepDefinition(
        id="feed_write_workload",
        label="Feed write workload",
        module="scripts.feed_write_workload",
        needs_system=True,
        depends_on=["index_initial_corpus"],
        check_blocked=_combine(
            _require_system_url("write_base_url"),
            _require_file(data_dir, "corpus_writes.parquet", "benchmark run prepare_products"),
        ),
        report_path=_latest_report(reports_dir, lambda system, calibration: f"write_feed_{system}_"),
    ),
    StepDefinition(
        id="verify_cluster",
        label="Verify cluster (9-point checklist)",
        module="scripts.opensearch.verify_cluster",
        needs_system=True,
        depends_on=["create_index"],
        check_blocked=_combine(
            _require_system_url("write_base_url"),
            _require_system_url("search_base_url"),
        ),
        report_path=_fixed_report(reports_dir, lambda system, calibration: f"verify_{system}.json"),
    ),
]

STEPS_BY_ID: dict[str, StepDefinition] = {s.id: s for s in STEPS}
