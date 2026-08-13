"""Run all 18 formal conditions in the fixed, balanced order from order.py.

    python -m scripts.formal.orchestrate --all
    python -m scripts.formal.orchestrate --all --dry-run-durations   # short synthetic durations for pipeline testing

Idempotent/resumable: skips any (system, workload, repetition) that already
has a valid results/formal/<system>/<workload>/r<n>/validity.json. Stops
(does not auto-retry) on the first invalid result so it can be inspected
before consuming further wall-clock time on a possibly-systemic problem.
"""
from __future__ import annotations

import argparse
import json

from scripts.formal.order import run_order
from scripts.formal.run_formal import LEAD_IN_S, MEASUREMENT_S, RESULTS_ROOT, WARMUP_S, run_condition


def _existing_validity(system: str, workload: str, repetition: int) -> dict | None:
    path = RESULTS_ROOT / system / workload.lower() / f"r{repetition}" / "validity.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", required=True, help="Run the full 18-slot sequence")
    parser.add_argument("--dry-run-durations", action="store_true",
                         help="Use short synthetic warm-up/lead-in/measurement durations for pipeline testing, not a real formal run")
    args = parser.parse_args()

    warmup_s, lead_in_s, measurement_s = WARMUP_S, LEAD_IN_S, MEASUREMENT_S
    if args.dry_run_durations:
        warmup_s, lead_in_s, measurement_s = 20.0, 5.0, 10.0
        print("*** DRY RUN: using short synthetic durations, not a real formal run ***")

    for slot in run_order():
        existing = _existing_validity(slot.system, slot.workload, slot.repetition)
        if existing is not None and existing.get("valid"):
            print(f"[{slot.position}/18] SKIP (already valid): {slot.system} {slot.workload} r{slot.repetition}")
            continue

        result = run_condition(slot.system, slot.workload, slot.repetition, slot.position,
                                warmup_s, lead_in_s, measurement_s)
        if not result["valid"]:
            print(f"[{slot.position}/18] STOPPING: invalid run for {slot.system} {slot.workload} r{slot.repetition}")
            print(f"See {result['out_dir']} for details. Not auto-retrying — review before continuing.")
            raise SystemExit(1)

    print("All 18 formal conditions complete and valid.")


if __name__ == "__main__":
    main()
