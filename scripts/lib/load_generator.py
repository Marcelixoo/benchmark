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
) -> dict[str, Any]:
    """Read-only load: one query per tick, cycling through `queries` (never
    mutated/filtered), dispatched into a thread pool so a slow request can't
    stall the ticker and suppress the offered rate.
    """
    query_cycle = itertools.cycle(queries)
    latencies_ms: list[tuple[float, float]] = []  # (tick_offset_s, latency_ms)
    errors: list[dict] = []
    timeout_count = 0
    attempted = 0
    run_start = time.monotonic()

    def do_one(query_text: str, tick_offset_s: float) -> None:
        nonlocal timeout_count
        t0 = time.perf_counter()
        try:
            os_search(os_client, index_name, query_text)
        except (ConnectionTimeout,) as e:
            timeout_count += 1
            errors.append({"query": query_text, "error": str(e), "timeout": True})
            return
        except TransportError as e:
            if "timeout" in str(e).lower():
                timeout_count += 1
                errors.append({"query": query_text, "error": str(e), "timeout": True})
            else:
                errors.append({"query": query_text, "error": str(e), "timeout": False})
            return
        except Exception as e:
            errors.append({"query": query_text, "error": str(e), "timeout": False})
            return
        latencies_ms.append((tick_offset_s, (time.perf_counter() - t0) * 1000.0))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = []
        for _ in _ticks(qps, duration_s):
            attempted += 1
            tick_offset_s = time.monotonic() - run_start
            futures.append(pool.submit(do_one, next(query_cycle), tick_offset_s))
        for f in futures:
            f.result()
    actual_duration_s = time.monotonic() - run_start

    succeeded = len(latencies_ms)
    lat_values = np.array([lat for _, lat in latencies_ms]) if latencies_ms else np.array([])
    midpoint = actual_duration_s / 2.0
    first_half = np.array([lat for off, lat in latencies_ms if off < midpoint])
    second_half = np.array([lat for off, lat in latencies_ms if off >= midpoint])

    return {
        "requests_attempted": attempted,
        "requests_succeeded": succeeded,
        "duration_s": round(actual_duration_s, 3),
        "target_qps": qps,
        "achieved_qps": round(succeeded / actual_duration_s, 2) if actual_duration_s > 0 else None,
        "errors_count": len(errors),
        "timeout_count": timeout_count,
        "error_rate_pct": round(100 * len(errors) / attempted, 2) if attempted else None,
        "latency_ms_p50_overall": float(np.percentile(lat_values, 50)) if lat_values.size else None,
        "latency_ms_p95_overall": float(np.percentile(lat_values, 95)) if lat_values.size else None,
        "latency_ms_p95_first_half": float(np.percentile(first_half, 95)) if first_half.size else None,
        "latency_ms_p95_second_half": float(np.percentile(second_half, 95)) if second_half.size else None,
        "error_samples": errors[:10],
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
) -> dict[str, Any]:
    """Write load: dispatches one batch per tick (every `batch_interval_s`),
    sized so that batch_rate * batch_size ~= docs_per_s, pulled sequentially
    (cycling) from `documents`. Each batch is submitted to a thread pool
    rather than awaited inline — a slow bulk() call must not itself throttle
    the offered rate below the target, or the harness (not the system under
    test) would be the bottleneck, which would invalidate the backlog check
    this function exists for. Uses index_batch_tolerant so a handful of bad
    items don't abort an in-progress run.
    """
    batch_size = max(1, round(docs_per_s * batch_interval_s))
    batch_rate = 1.0 / batch_interval_s
    doc_cycle = itertools.cycle(documents)

    docs_offered = 0
    item_errors = 0
    batch_error_samples: list[Any] = []
    batch_results: list[dict] = []
    run_start = time.monotonic()

    def do_batch(batch: list[dict[str, Any]]) -> None:
        nonlocal item_errors
        result = index_batch_tolerant(os_client, index_name, id_field, batch)
        batch_results.append(result)
        if result["errors"]:
            item_errors += result["errors"]
            if len(batch_error_samples) < 10:
                batch_error_samples.extend(result["error_samples"])

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = []
        for _ in _ticks(batch_rate, duration_s):
            batch = [next(doc_cycle) for _ in range(batch_size)]
            docs_offered += len(batch)
            futures.append(pool.submit(do_batch, batch))
        for f in futures:
            f.result()

    actual_duration_s = time.monotonic() - run_start
    docs_indexed = sum(r["success"] for r in batch_results)

    return {
        "docs_offered": docs_offered,
        "docs_indexed": docs_indexed,
        "duration_s": round(actual_duration_s, 3),
        "target_docs_per_s": docs_per_s,
        "achieved_docs_per_s": round(docs_indexed / actual_duration_s, 2) if actual_duration_s > 0 else None,
        "batch_size": batch_size,
        "item_errors_count": item_errors,
        "item_error_samples": batch_error_samples,
    }
