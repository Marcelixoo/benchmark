"""Fixed, balanced run order for the 12 supplementary stress runs: 2 systems
x 2 conditions (Stress-W0, Stress-W2) x 3 repetitions. Each block alternates
which system goes first, mirroring scripts/formal/order.py's approach to
cancelling out order effects, adapted to a 4-slot-per-block/3-block shape
instead of formal's 6-slot/3-block shape.

    Block 1 (rep 1): L1(Stress-W0), L1(Stress-W2), S1(Stress-W0), S1(Stress-W2)
    Block 2 (rep 2): S1(Stress-W2), S1(Stress-W0), L1(Stress-W2), L1(Stress-W0)
    Block 3 (rep 3): L1(Stress-W0), L1(Stress-W2), S1(Stress-W0), S1(Stress-W2)
"""
from __future__ import annotations

from dataclasses import dataclass

_BLOCKS = [
    (1, [("local_index", "STRESS_W0"), ("local_index", "STRESS_W2"),
         ("shared_index", "STRESS_W0"), ("shared_index", "STRESS_W2")]),
    (2, [("shared_index", "STRESS_W2"), ("shared_index", "STRESS_W0"),
         ("local_index", "STRESS_W2"), ("local_index", "STRESS_W0")]),
    (3, [("local_index", "STRESS_W0"), ("local_index", "STRESS_W2"),
         ("shared_index", "STRESS_W0"), ("shared_index", "STRESS_W2")]),
]


@dataclass(frozen=True)
class RunSlot:
    position: int  # 1-12, execution order
    system: str
    workload: str  # "STRESS_W0" or "STRESS_W2"
    repetition: int  # 1, 2, or 3 -> the "r<n>" this run belongs to


def run_order() -> list[RunSlot]:
    slots: list[RunSlot] = []
    position = 1
    for repetition, pairs in _BLOCKS:
        for system, workload in pairs:
            slots.append(RunSlot(position=position, system=system, workload=workload, repetition=repetition))
            position += 1
    return slots


assert len(run_order()) == 12
assert len({(s.system, s.workload, s.repetition) for s in run_order()}) == 12
