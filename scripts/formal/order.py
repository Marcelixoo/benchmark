"""Fixed, balanced run order for the 18 formal runs: 3 blocks, each block
runs both systems across all 3 workloads once, with the workload order
rotating and which system goes first alternating block-to-block, to cancel
out order effects. Each (system, workload) pair appears exactly once per
block, so its "repetition" number is simply the block index (1, 2, 3) —
this is also the `r<n>` directory name each run's results land in.

    Block 1 (rep 1): L1(W0,W1,W2), S1(W0,W1,W2)
    Block 2 (rep 2): S1(W1,W2,W0), L1(W1,W2,W0)
    Block 3 (rep 3): L1(W2,W0,W1), S1(W2,W0,W1)
"""
from __future__ import annotations

from dataclasses import dataclass

_BLOCKS = [
    (1, [("local_index", "W0"), ("local_index", "W1"), ("local_index", "W2"),
         ("shared_index", "W0"), ("shared_index", "W1"), ("shared_index", "W2")]),
    (2, [("shared_index", "W1"), ("shared_index", "W2"), ("shared_index", "W0"),
         ("local_index", "W1"), ("local_index", "W2"), ("local_index", "W0")]),
    (3, [("local_index", "W2"), ("local_index", "W0"), ("local_index", "W1"),
         ("shared_index", "W2"), ("shared_index", "W0"), ("shared_index", "W1")]),
]


@dataclass(frozen=True)
class RunSlot:
    position: int  # 1-18, execution order
    system: str
    workload: str
    repetition: int  # 1, 2, or 3 -> the "r<n>" this run belongs to


def run_order() -> list[RunSlot]:
    slots: list[RunSlot] = []
    position = 1
    for repetition, pairs in _BLOCKS:
        for system, workload in pairs:
            slots.append(RunSlot(position=position, system=system, workload=workload, repetition=repetition))
            position += 1
    return slots


assert len(run_order()) == 18
assert len({(s.system, s.workload, s.repetition) for s in run_order()}) == 18
