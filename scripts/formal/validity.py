"""Post-hoc validity check for a formal run's measured-window results.
Thresholds carry forward the same tolerance convention already established
in experiment-config.json's calibration_verdicts (e.g. S1's write check
accepted 496.35/500 = 99.3% of target as a pass) rather than inventing new
numbers: achieved rate >= 95% of target, error/timeout rate < 1%.
"""
from __future__ import annotations

RATE_TOLERANCE = 0.95
MAX_ERROR_RATE_PCT = 1.0


def is_run_valid(query_result: dict, write_result: dict | None, target_qps: float, target_docs_per_s: float | None) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    achieved_qps = query_result.get("achieved_qps")
    if achieved_qps is None or achieved_qps < RATE_TOLERANCE * target_qps:
        reasons.append(f"achieved_qps={achieved_qps} below {RATE_TOLERANCE * 100:.0f}% of target_qps={target_qps}")

    error_rate_pct = query_result.get("error_rate_pct") or 0.0
    if error_rate_pct > MAX_ERROR_RATE_PCT:
        reasons.append(f"query error_rate_pct={error_rate_pct} exceeds {MAX_ERROR_RATE_PCT}%")

    if write_result is not None and target_docs_per_s is not None:
        achieved_docs_per_s = write_result.get("achieved_docs_per_s")
        if achieved_docs_per_s is None or achieved_docs_per_s < RATE_TOLERANCE * target_docs_per_s:
            reasons.append(
                f"achieved_docs_per_s={achieved_docs_per_s} below {RATE_TOLERANCE * 100:.0f}% of target_docs_per_s={target_docs_per_s}"
            )
        docs_offered = write_result.get("docs_offered") or 0
        item_error_rate_pct = 100 * write_result.get("item_errors_count", 0) / docs_offered if docs_offered else 0.0
        if item_error_rate_pct > MAX_ERROR_RATE_PCT:
            reasons.append(f"write item_error_rate_pct={item_error_rate_pct} exceeds {MAX_ERROR_RATE_PCT}%")

    return (len(reasons) == 0, reasons)
