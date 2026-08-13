"""Run ONE formal-experiment condition end to end: restore baseline -> verify
-> warm-up -> (lead-in +) measurement -> capture stats -> validity check ->
write results.

    python -m scripts.formal.run_formal --system local_index --workload W0 --repetition 1

Not part of the benchmark_api/CLI step registry — this is a standalone
research harness with its own multi-phase timing model, invoked directly or
via scripts.formal.orchestrate.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import pandas as pd

from benchmark_api import run_state
from scripts.formal import baseline, validity
from scripts.formal.infra import CONTAINERS_BY_SYSTEM
from scripts.formal.verify_baseline import run_gate
from scripts.lib.config import PROJECT_ROOT, data_dir, load_config
from scripts.lib.docker_stats import ContainerStatsSampler
from scripts.lib.load_generator import run_query_load, run_write_load
from scripts.lib.opensearch_client import client, fetch_stats, remote_store_nodes_stats, require_search_url, require_write_url, thread_pool_write_stats

RESULTS_ROOT = PROJECT_ROOT / "results" / "formal"
EXPERIMENT_CONFIG_PATH = PROJECT_ROOT / "experiment-config.json"

WARMUP_S = 180.0
LEAD_IN_S = 30.0
MEASUREMENT_S = 300.0

# "formal_run" is not a registered benchmark_api/CLI step (it's a
# multi-phase in-process harness, not a single script), so it never shows
# up in the web UI's step grid. It's still write-through'd to run_state so
# it's observable cross-process by run_id alone: `run_exists`/the SSE
# stream endpoint work off run_state.read_run() directly, independent of
# the step registry.
_FORMAL_STEP_ID = "formal_run"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _RunStateTee:
    """Duplicates stdout writes into run_state's log file so this run's
    progress prints are tailable live via /api/runs/{run_id}/stream, the
    same as any subprocess-backed step."""

    def __init__(self, run_id: str, real_stdout) -> None:
        self._run_id = run_id
        self._real = real_stdout
        self._seq = 0
        self._partial = ""

    def write(self, s: str) -> int:
        self._real.write(s)
        self._partial += s
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            if line:
                run_state.append_log_line(self._run_id, "stdout", line, self._seq, _now())
                self._seq += 1
        return len(s)

    def flush(self) -> None:
        self._real.flush()


def _load_experiment_config() -> dict:
    with open(EXPERIMENT_CONFIG_PATH) as f:
        return json.load(f)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fieldnames = sorted({k for row in rows for k in row.keys()})
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def run_condition(system: str, workload: str, repetition: int, position: int | None = None,
                   warmup_s: float = WARMUP_S, lead_in_s: float = LEAD_IN_S, measurement_s: float = MEASUREMENT_S) -> dict:
    """Cross-process-observable wrapper around `_run_condition`: records a
    run_state slot/run for (formal_run, system) around the call so this
    condition's progress is visible/tailable from any process for the
    duration of the run, then finishes it as done/failed based on the
    result's validity (or failed, on an unhandled exception)."""
    run_id = uuid.uuid4().hex[:12]
    run_state.start_run(_FORMAL_STEP_ID, system, run_id, os.getpid())
    real_stdout = sys.stdout
    sys.stdout = _RunStateTee(run_id, real_stdout)
    try:
        print(f"[run_state] run_id={run_id} (not in the web UI's step grid — tail directly: "
              f"curl -N localhost:8000/api/runs/{run_id}/stream)")
        result = _run_condition(system, workload, repetition, position, warmup_s, lead_in_s, measurement_s)
    except Exception:
        run_state.finish_run(_FORMAL_STEP_ID, system, run_id, "failed")
        raise
    finally:
        sys.stdout = real_stdout
    run_state.finish_run(_FORMAL_STEP_ID, system, run_id, "done" if result["valid"] else "failed")
    return result


def _run_condition(system: str, workload: str, repetition: int, position: int | None = None,
                    warmup_s: float = WARMUP_S, lead_in_s: float = LEAD_IN_S, measurement_s: float = MEASUREMENT_S) -> dict:
    exp_config = _load_experiment_config()
    workload_spec = exp_config["workloads"][workload]
    target_qps = exp_config["query_rate_qps"]
    target_docs_per_s = workload_spec["docs_per_s"] or None

    config = load_config()
    system_config = config["systems"][system]
    d_dir = data_dir(config)
    is_shared = system == "shared_index"

    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    print(f"=== Formal run: system={system} workload={workload} repetition={repetition} position={position} ===")
    baseline.restore_baseline(system)

    gate = run_gate(system)
    if not gate["all_passed"]:
        return _finalize(system, workload, repetition, position, started_at, valid=False,
                          reasons=["baseline verification gate failed"], verification=gate,
                          query_result=None, write_result=None, container_summary=None, container_raw=[],
                          os_stats_before=None, os_stats_after=None, remote_before=None, remote_after=None,
                          warmup_raw=[])

    write_url = require_write_url(system_config, system)
    search_url = require_search_url(system_config, system)
    write_client = client(write_url)
    search_client = client(search_url)
    index_name = system_config["index_name"]

    queries = pd.read_parquet(d_dir / "queries_fixed_5000_corpus_relevant.parquet")["query_text"].tolist()
    write_documents = None
    if target_docs_per_s:
        write_documents = pd.read_parquet(d_dir / "corpus_writes.parquet").to_dict(orient="records")

    sampler = ContainerStatsSampler(CONTAINERS_BY_SYSTEM[system]).start()

    print(f"Warm-up: {warmup_s}s query-only at {target_qps} QPS (discarded)")
    warmup_result = run_query_load(search_client, index_name, queries, target_qps, warmup_s)

    os_stats_before = fetch_stats(write_client, index_name)
    thread_pool_before = thread_pool_write_stats(write_client)
    remote_before = remote_store_nodes_stats(write_client) if is_shared else None

    query_result: dict = {}
    write_result: dict | None = None

    if target_docs_per_s:
        total_duration_s = lead_in_s + measurement_s
        print(f"Lead-in+measurement: {lead_in_s}s discarded + {measurement_s}s measured, "
              f"query@{target_qps}qps + write@{target_docs_per_s}docs/s")

        def _do_query() -> None:
            nonlocal query_result
            query_result = run_query_load(search_client, index_name, queries, target_qps, total_duration_s, measurement_start_s=lead_in_s)

        def _do_write() -> None:
            nonlocal write_result
            write_result = run_write_load(write_client, index_name, "asin", write_documents, target_docs_per_s, total_duration_s, measurement_start_s=lead_in_s)

        query_thread = threading.Thread(target=_do_query)
        write_thread = threading.Thread(target=_do_write)
        query_thread.start()
        write_thread.start()
        query_thread.join()
        write_thread.join()
    else:
        print(f"Measurement: {measurement_s}s query-only at {target_qps} QPS")
        query_result = run_query_load(search_client, index_name, queries, target_qps, measurement_s)

    sampler.stop()
    os_stats_after = fetch_stats(write_client, index_name)
    thread_pool_after = thread_pool_write_stats(write_client)
    remote_after = remote_store_nodes_stats(write_client) if is_shared else None

    valid, reasons = validity.is_run_valid(query_result, write_result, target_qps, target_docs_per_s)

    return _finalize(
        system, workload, repetition, position, started_at, valid=valid, reasons=reasons, verification=gate,
        query_result=query_result, write_result=write_result,
        container_summary=sampler.summary(), container_raw=sampler._samples,
        os_stats_before=os_stats_before, os_stats_after=os_stats_after,
        remote_before=remote_before, remote_after=remote_after,
        warmup_raw=warmup_result.get("raw", []),
        thread_pool_before=thread_pool_before, thread_pool_after=thread_pool_after,
    )


def _finalize(system, workload, repetition, position, started_at, *, valid, reasons, verification,
              query_result, write_result, container_summary, container_raw,
              os_stats_before, os_stats_after, remote_before, remote_after, warmup_raw,
              thread_pool_before=None, thread_pool_after=None) -> dict:
    finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if valid:
        out_dir = RESULTS_ROOT / system / workload.lower() / f"r{repetition}"
    else:
        out_dir = RESULTS_ROOT / "invalid" / system / workload.lower() / f"attempt-{int(time.time())}"

    metadata = {
        "system": system, "workload": workload, "repetition": repetition, "position": position,
        "started_at": started_at, "finished_at": finished_at, "valid": valid, "invalid_reasons": reasons,
    }
    _write_json(out_dir / "metadata.json", metadata)
    _write_json(out_dir / "verification.json", verification)

    if query_result is not None:
        _write_csv(out_dir / "queries.csv", query_result.pop("raw", []))
        _write_json(out_dir / "query_summary.json", query_result)
    if warmup_raw:
        _write_csv(out_dir / "warmup.csv", warmup_raw)
    if write_result is not None:
        _write_csv(out_dir / "indexing.csv", write_result.pop("raw", []))
        _write_json(out_dir / "indexing_summary.json", write_result)
    if container_summary is not None:
        _write_json(out_dir / "container_stats_summary.json", container_summary)
        rows = []
        for container, samples in (container_raw or {}).items():
            for s in samples:
                rows.append({"container": container, **s})
        _write_csv(out_dir / "container_stats.csv", rows)
    if os_stats_before is not None:
        _write_json(out_dir / "opensearch_stats_before.json", os_stats_before)
    if os_stats_after is not None:
        _write_json(out_dir / "opensearch_stats_after.json", os_stats_after)
    if thread_pool_before is not None:
        _write_json(out_dir / "thread_pool_stats_before.json", thread_pool_before)
    if thread_pool_after is not None:
        _write_json(out_dir / "thread_pool_stats_after.json", thread_pool_after)
    if remote_before is not None:
        _write_json(out_dir / "remote_store_stats_before.json", remote_before)
    if remote_after is not None:
        _write_json(out_dir / "remote_store_stats_after.json", remote_after)

    validity_payload = {"valid": valid, "reasons": reasons,
                         "rate_tolerance": validity.RATE_TOLERANCE, "max_error_rate_pct": validity.MAX_ERROR_RATE_PCT}
    _write_json(out_dir / "validity.json", validity_payload)

    print(f"{'VALID' if valid else 'INVALID'} run written to {out_dir}")
    if not valid:
        print(f"Reasons: {reasons}")
    return {"out_dir": str(out_dir), "valid": valid, "reasons": reasons}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["local_index", "shared_index"])
    parser.add_argument("--workload", required=True, choices=["W0", "W1", "W2"])
    parser.add_argument("--repetition", type=int, required=True, choices=[1, 2, 3])
    parser.add_argument("--position", type=int, default=None)
    parser.add_argument("--warmup-s", type=float, default=WARMUP_S)
    parser.add_argument("--lead-in-s", type=float, default=LEAD_IN_S)
    parser.add_argument("--measurement-s", type=float, default=MEASUREMENT_S)
    args = parser.parse_args()

    result = run_condition(args.system, args.workload, args.repetition, args.position,
                            args.warmup_s, args.lead_in_s, args.measurement_s)
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
