"""Capacity-staircase probe: find each architecture's approximate query-QPS
ceiling before running the supplementary Stress-W0/Stress-W2 experiment.

Query-only bursts at geometrically increasing QPS against a warm baseline,
stopping at the first rung where a predeclared "knee" condition trips
(achieved QPS collapses, p95 latency jumps, or errors appear), then a short
binary-search refinement between the last good rung and the knee rung.

    python -m scripts.stress.find_capacity --system local_index
    python -m scripts.stress.find_capacity --system local_index --dry-run-durations

Not part of the formal 18-run experiment or its frozen experiment-config.json
— this is a standalone probe for the supplementary stress experiment. Output
is written under results/stress/capacity/, entirely separate from
results/formal/.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from scripts.formal import baseline, validity
from scripts.formal.verify_baseline import run_gate
from scripts.lib.config import PROJECT_ROOT, data_dir, load_config
from scripts.lib.load_generator import run_query_load
from scripts.lib.opensearch_client import client, require_search_url

RESULTS_ROOT = PROJECT_ROOT / "results" / "stress" / "capacity"

WARMUP_S = 60.0
RUNG_DURATION_S = 45.0
INITIAL_RUNGS = [50.0, 100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 6400.0]
MAX_REFINEMENT_PROBES = 3
REFINEMENT_AGREEMENT_TOLERANCE = 0.10

# Knee-detection rule (predeclared, exact numbers — see plan for rationale).
RATE_COLLAPSE_FRACTION = validity.RATE_TOLERANCE  # 0.95
LATENCY_CLIFF_MULTIPLIER = 3.0
MAX_ERROR_RATE_PCT = validity.MAX_ERROR_RATE_PCT  # 1.0


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _knee_tripped(rung_result: dict, offered_qps: float, prev_p95: float | None) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    achieved_qps = rung_result.get("achieved_qps")
    if achieved_qps is None or achieved_qps < RATE_COLLAPSE_FRACTION * offered_qps:
        reasons.append(f"achieved_qps={achieved_qps} below {RATE_COLLAPSE_FRACTION * 100:.0f}% of offered_qps={offered_qps}")

    p95 = rung_result.get("latency_ms_p95_overall")
    if prev_p95 is not None and p95 is not None and p95 > LATENCY_CLIFF_MULTIPLIER * prev_p95:
        reasons.append(f"latency_ms_p95_overall={p95} exceeds {LATENCY_CLIFF_MULTIPLIER}x previous rung's p95={prev_p95}")

    error_rate_pct = rung_result.get("error_rate_pct") or 0.0
    if error_rate_pct > MAX_ERROR_RATE_PCT:
        reasons.append(f"error_rate_pct={error_rate_pct} exceeds {MAX_ERROR_RATE_PCT}%")

    return (len(reasons) > 0, reasons)


def _run_rung(search_client, index_name: str, queries: list[str], qps: float, duration_s: float) -> dict:
    max_workers = max(20, round(qps / 10))
    print(f"  rung qps={qps} duration_s={duration_s} max_workers={max_workers}")
    result = run_query_load(search_client, index_name, queries, qps, duration_s, max_workers=max_workers)
    result.pop("raw", None)
    print(f"    achieved_qps={result['achieved_qps']} p95={result['latency_ms_p95_overall']} "
          f"error_rate_pct={result['error_rate_pct']}")
    return result


def find_capacity(system: str, max_rungs: int = 12, rung_duration_s: float = RUNG_DURATION_S,
                   warmup_s: float = WARMUP_S) -> dict:
    config = load_config()
    system_config = config["systems"][system]
    d_dir = data_dir(config)
    search_url = require_search_url(system_config, system)
    search_client = client(search_url)
    index_name = system_config["index_name"]
    queries = pd.read_parquet(d_dir / "queries_fixed_5000_corpus_relevant.parquet")["query_text"].tolist()

    started_at = _now()
    print(f"=== Capacity staircase: system={system} ===")
    baseline.restore_baseline(system)

    gate = run_gate(system)
    if not gate["all_passed"]:
        raise RuntimeError(f"Baseline verification gate failed for '{system}': {gate['checks']}")

    print(f"Warm-up: {warmup_s}s query-only at 50 QPS (discarded)")
    run_query_load(search_client, index_name, queries, 50.0, warmup_s)

    rungs: list[dict] = []
    prev_p95: float | None = None
    detected_knee_qps: float | None = None
    last_good_qps: float | None = None

    for offered_qps in INITIAL_RUNGS[:max_rungs]:
        result = _run_rung(search_client, index_name, queries, offered_qps, rung_duration_s)
        tripped, reasons = _knee_tripped(result, offered_qps, prev_p95)
        rungs.append({"offered_qps": offered_qps, "is_refinement": False, "knee_tripped": tripped,
                       "knee_reasons": reasons, **result})
        if tripped:
            detected_knee_qps = offered_qps
            print(f"  KNEE detected at offered_qps={offered_qps}: {reasons}")
            break
        last_good_qps = offered_qps
        prev_p95 = result.get("latency_ms_p95_overall")

    max_sustainable_qps = last_good_qps

    if detected_knee_qps is not None and last_good_qps is not None:
        lo, hi = last_good_qps, detected_knee_qps
        prev_midpoint: float | None = None
        for _ in range(MAX_REFINEMENT_PROBES):
            midpoint = round((lo + hi) / 2, 1)
            result = _run_rung(search_client, index_name, queries, midpoint, rung_duration_s)
            tripped, reasons = _knee_tripped(result, midpoint, prev_p95)
            rungs.append({"offered_qps": midpoint, "is_refinement": True, "knee_tripped": tripped,
                          "knee_reasons": reasons, **result})
            if tripped:
                hi = midpoint
            else:
                lo = midpoint
                max_sustainable_qps = midpoint
                prev_p95 = result.get("latency_ms_p95_overall")
            if prev_midpoint is not None and abs(midpoint - prev_midpoint) <= REFINEMENT_AGREEMENT_TOLERANCE * prev_midpoint:
                print(f"  Refinement converged: consecutive midpoints {prev_midpoint} and {midpoint} agree within "
                      f"{REFINEMENT_AGREEMENT_TOLERANCE * 100:.0f}%")
                break
            prev_midpoint = midpoint

    finished_at = _now()
    report = {
        "system": system,
        "started_at": started_at,
        "finished_at": finished_at,
        "knee_rule": {
            "rate_collapse_fraction": RATE_COLLAPSE_FRACTION,
            "latency_cliff_multiplier": LATENCY_CLIFF_MULTIPLIER,
            "max_error_rate_pct": MAX_ERROR_RATE_PCT,
            "description": "Knee = first rung where achieved_qps < rate_collapse_fraction*offered_qps, "
                            "OR p95 > latency_cliff_multiplier * previous rung's p95, OR error_rate_pct > max_error_rate_pct.",
        },
        "rung_duration_s": rung_duration_s,
        "rungs": rungs,
        "detected_knee_qps": detected_knee_qps,
        "max_sustainable_qps": max_sustainable_qps,
    }

    out_path = RESULTS_ROOT / f"{system}_capacity_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    out_path.write_text(json.dumps(report, indent=2, default=str))

    print(f"\n=== Summary for {system} ===")
    print(f"{'QPS':>10} {'achieved':>10} {'p95_ms':>10} {'err%':>8} {'refine':>7} {'knee':>6}")
    for r in rungs:
        print(f"{r['offered_qps']:>10} {r.get('achieved_qps'):>10} {r.get('latency_ms_p95_overall'):>10} "
              f"{r.get('error_rate_pct'):>8} {str(r['is_refinement']):>7} {str(r['knee_tripped']):>6}")
    print(f"detected_knee_qps={detected_knee_qps} max_sustainable_qps={max_sustainable_qps}")
    print(f"Report written to {out_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["local_index", "shared_index"])
    parser.add_argument("--max-rungs", type=int, default=len(INITIAL_RUNGS))
    parser.add_argument("--rung-duration-s", type=float, default=RUNG_DURATION_S)
    parser.add_argument("--dry-run-durations", action="store_true",
                         help="Use short synthetic rung/warm-up durations and fewer rungs, for pipeline testing")
    args = parser.parse_args()

    rung_duration_s = args.rung_duration_s
    max_rungs = args.max_rungs
    warmup_s = WARMUP_S
    if args.dry_run_durations:
        rung_duration_s = 5.0
        max_rungs = 3
        warmup_s = 5.0
        print("*** DRY RUN: using short synthetic durations, not a real capacity probe ***")

    find_capacity(args.system, max_rungs=max_rungs, rung_duration_s=rung_duration_s, warmup_s=warmup_s)


if __name__ == "__main__":
    main()
