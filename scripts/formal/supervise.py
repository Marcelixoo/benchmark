"""Autonomous babysitting mode for the full 18-run formal sequence.

Unlike orchestrate.py (which stops on the first invalid run so a human can
look at it before more wall-clock time is spent — the default for a
carefully-supervised first pass), this script is the "keep going" mode:
on an invalid result or a crash (docker flake, transient connection error,
etc.) it retries the same condition, with a fresh baseline restore, up to
MAX_RETRIES times before giving up on that slot and moving on to the next
one. Every attempt (including failed ones) is logged; nothing is retried
silently without a trace.

    python -m scripts.formal.supervise
"""
from __future__ import annotations

import json
import time
import traceback

from scripts.formal.order import run_order
from scripts.formal.run_formal import LEAD_IN_S, MEASUREMENT_S, RESULTS_ROOT, WARMUP_S, run_condition

MAX_RETRIES = 3
RETRY_BACKOFF_S = 30


def _existing_validity(system: str, workload: str, repetition: int) -> dict | None:
    path = RESULTS_ROOT / system / workload.lower() / f"r{repetition}" / "validity.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def main() -> None:
    gave_up: list[str] = []
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"=== Supervised formal run starting at {started}: 18 slots, up to {MAX_RETRIES} attempts each ===")

    for slot in run_order():
        label = f"{slot.system} {slot.workload} r{slot.repetition} (position {slot.position}/18)"
        existing = _existing_validity(slot.system, slot.workload, slot.repetition)
        if existing is not None and existing.get("valid"):
            print(f"[{label}] SKIP (already valid)")
            continue

        succeeded = False
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[{label}] attempt {attempt}/{MAX_RETRIES}")
            try:
                result = run_condition(slot.system, slot.workload, slot.repetition, slot.position,
                                        WARMUP_S, LEAD_IN_S, MEASUREMENT_S)
            except Exception:
                print(f"[{label}] CRASHED on attempt {attempt}/{MAX_RETRIES}:")
                traceback.print_exc()
                if attempt < MAX_RETRIES:
                    print(f"[{label}] retrying in {RETRY_BACKOFF_S}s")
                    time.sleep(RETRY_BACKOFF_S)
                continue

            if result["valid"]:
                print(f"[{label}] VALID on attempt {attempt}/{MAX_RETRIES} -> {result['out_dir']}")
                succeeded = True
                break

            print(f"[{label}] INVALID on attempt {attempt}/{MAX_RETRIES}: {result['reasons']} -> {result['out_dir']}")
            if attempt < MAX_RETRIES:
                print(f"[{label}] retrying in {RETRY_BACKOFF_S}s")
                time.sleep(RETRY_BACKOFF_S)

        if not succeeded:
            print(f"[{label}] GIVING UP after {MAX_RETRIES} attempts — moving on to the next slot")
            gave_up.append(label)

    finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if gave_up:
        print(f"=== Supervised run finished at {finished} with {len(gave_up)} slot(s) unresolved: ===")
        for label in gave_up:
            print(f"  - {label}")
        raise SystemExit(1)

    print(f"=== Supervised run finished at {finished}: all 18 slots valid ===")


if __name__ == "__main__":
    main()
