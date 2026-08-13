"""Rate-controlled load generators for the W1 (query QPS) / W2 (write docs/s)
calibration checks. Deliberately not Locust (see README.md's "Locust is not
included" note) and deliberately not async — target rates here (tens of QPS,
low hundreds of docs/s) are comfortably held by a monotonic-clock tick
scheduler dispatching into a bounded ThreadPoolExecutor, reusing the same
synchronous opensearch-py client used everywhere else in this repo.
"""
from __future__ import annotations

import itertools
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterator

import numpy as np
from opensearchpy import OpenSearch
from opensearchpy.exceptions import ConnectionTimeout, TransportError

from scripts.lib.opensearch_client import index_batch_tolerant, search as os_search


def _ticks(rate_per_s: float, duration_s: float) -> Iterator[int]:
    """Yield tick indices at 1/rate_per_s intervals for duration_s, scheduled
    off an absolute start time so ticks don't drift under load.
    """
    interval = 1.0 / rate_per_s
    start = time.monotonic()
    n = 0
    while True:
        now = time.monotonic()
        if now - start >= duration_s:
            return
        target = start + n * interval
        if target > now:
            time.sleep(target - now)
        yield n
        n += 1


def run_query_load(
    os_client: OpenSearch,
    index_name: str,
    queries: list[str],
    qps: float,
    duration_s: float,
    max_workers: int = 20,
    measurement_start_s: float = 0.0,
) -> dict[str, Any]:
    """Read-only load: one query per tick, cycling through `queries` (never
    mutated/filtered), dispatched into a thread pool so a slow request can't
    stall the ticker and suppress the offered rate.

    `measurement_start_s` lets one continuous call cover a discarded lead-in
    followed by the real measurement window (e.g. formal W1/W2 runs): ticks
    with `tick_offset_s < measurement_start_s` are still issued (so the
    system sees continuous load) but excluded from every summary statistic
    below. The full raw per-request log (including the lead-in) is always
    returned under `raw` for CSV export / auditing.
    """
    query_cycle = itertools.cycle(queries)
    raw: list[dict[str, Any]] = []  # {tick_offset_s, latency_ms, error, timeout}
    attempted = 0
    run_start = time.monotonic()

    def do_one(query_text: str, tick_offset_s: float) -> None:
        t0 = time.perf_counter()
        try:
            os_search(os_client, index_name, query_text)
        except (ConnectionTimeout,) as e:
            raw.append({"tick_offset_s": tick_offset_s, "latency_ms": None, "query": query_text, "error": str(e), "timeout": True})
            return
        except TransportError as e:
            is_timeout = "timeout" in str(e).lower()
            raw.append({"tick_offset_s": tick_offset_s, "latency_ms": None, "query": query_text, "error": str(e), "timeout": is_timeout})
            return
        except Exception as e:
            raw.append({"tick_offset_s": tick_offset_s, "latency_ms": None, "query": query_text, "error": str(e), "timeout": False})
            return
        raw.append({"tick_offset_s": tick_offset_s, "latency_ms": (time.perf_counter() - t0) * 1000.0, "query": query_text, "error": None, "timeout": False})

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = []
        for _ in _ticks(qps, duration_s):
            attempted += 1
            tick_offset_s = time.monotonic() - run_start
            futures.append(pool.submit(do_one, next(query_cycle), tick_offset_s))
        for f in futures:
            f.result()
    actual_duration_s = time.monotonic() - run_start

    measured = [r for r in raw if r["tick_offset_s"] >= measurement_start_s]
    measured_attempted = len(measured)
    measured_latencies = [r for r in measured if r["error"] is None]
    measured_errors = [r for r in measured if r["error"] is not None]
    measured_timeouts = sum(1 for r in measured_errors if r["timeout"])
    measured_duration_s = max(actual_duration_s - measurement_start_s, 0.0)

    succeeded = len(measured_latencies)
    lat_values = np.array([r["latency_ms"] for r in measured_latencies]) if measured_latencies else np.array([])
    midpoint = measurement_start_s + measured_duration_s / 2.0
    first_half = np.array([r["latency_ms"] for r in measured_latencies if r["tick_offset_s"] < midpoint])
    second_half = np.array([r["latency_ms"] for r in measured_latencies if r["tick_offset_s"] >= midpoint])

    return {
        "requests_attempted": measured_attempted,
        "requests_succeeded": succeeded,
        "duration_s": round(measured_duration_s, 3),
        "target_qps": qps,
        "achieved_qps": round(succeeded / measured_duration_s, 2) if measured_duration_s > 0 else None,
        "errors_count": len(measured_errors),
        "timeout_count": measured_timeouts,
        "error_rate_pct": round(100 * len(measured_errors) / measured_attempted, 2) if measured_attempted else None,
        "latency_ms_p50_overall": float(np.percentile(lat_values, 50)) if lat_values.size else None,
        "latency_ms_p95_overall": float(np.percentile(lat_values, 95)) if lat_values.size else None,
        "latency_ms_p95_first_half": float(np.percentile(first_half, 95)) if first_half.size else None,
        "latency_ms_p95_second_half": float(np.percentile(second_half, 95)) if second_half.size else None,
        "error_samples": [r for r in measured_errors[:10]],
        "measurement_start_s": measurement_start_s,
        "total_requests_attempted_including_lead_in": attempted,
        "raw": raw,
    }


def run_write_load(
    os_client: OpenSearch,
    index_name: str,
    id_field: str,
    documents: list[dict[str, Any]],
    docs_per_s: float,
    duration_s: float,
    batch_interval_s: float = 0.2,
    max_workers: int = 10,
    measurement_start_s: float = 0.0,
) -> dict[str, Any]:
    """Write load: dispatches one batch per tick (every `batch_interval_s`),
    sized so that batch_rate * batch_size ~= docs_per_s, pulled sequentially
    (cycling) from `documents`. Each batch is submitted to a thread pool
    rather than awaited inline — a slow bulk() call must not itself throttle
    the offered rate below the target, or the harness (not the system under
    test) would be the bottleneck, which would invalidate the backlog check
    this function exists for. Uses index_batch_tolerant so a handful of bad
    items don't abort an in-progress run.

    `measurement_start_s` mirrors `run_query_load`'s param: batches with
    `tick_offset_s < measurement_start_s` are still issued (continuous write
    pressure across a discarded lead-in) but excluded from the summary. The
    full raw per-batch log is always returned under `raw`.
    """
    batch_size = max(1, round(docs_per_s * batch_interval_s))
    batch_rate = 1.0 / batch_interval_s
    doc_cycle = itertools.cycle(documents)

    docs_offered = 0
    raw: list[dict[str, Any]] = []  # {tick_offset_s, batch_size, success, errors, error_samples}
    run_start = time.monotonic()

    def do_batch(batch: list[dict[str, Any]], tick_offset_s: float) -> None:
        result = index_batch_tolerant(os_client, index_name, id_field, batch)
        raw.append({
            "tick_offset_s": tick_offset_s,
            "batch_size": len(batch),
            "success": result["success"],
            "errors": result["errors"],
            "error_samples": result["error_samples"],
        })

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = []
        for _ in _ticks(batch_rate, duration_s):
            batch = [next(doc_cycle) for _ in range(batch_size)]
            docs_offered += len(batch)
            tick_offset_s = time.monotonic() - run_start
            futures.append(pool.submit(do_batch, batch, tick_offset_s))
        for f in futures:
            f.result()

    actual_duration_s = time.monotonic() - run_start
    measured = [r for r in raw if r["tick_offset_s"] >= measurement_start_s]
    measured_duration_s = max(actual_duration_s - measurement_start_s, 0.0)
    measured_docs_offered = sum(r["batch_size"] for r in measured)
    measured_docs_indexed = sum(r["success"] for r in measured)
    measured_item_errors = sum(r["errors"] for r in measured)
    error_samples: list[Any] = []
    for r in measured:
        if len(error_samples) >= 10:
            break
        error_samples.extend(r["error_samples"])

    return {
        "docs_offered": measured_docs_offered,
        "docs_indexed": measured_docs_indexed,
        "duration_s": round(measured_duration_s, 3),
        "target_docs_per_s": docs_per_s,
        "achieved_docs_per_s": round(measured_docs_indexed / measured_duration_s, 2) if measured_duration_s > 0 else None,
        "batch_size": batch_size,
        "item_errors_count": measured_item_errors,
        "item_error_samples": error_samples[:10],
        "measurement_start_s": measurement_start_s,
        "total_docs_offered_including_lead_in": docs_offered,
        "raw": raw,
    }
