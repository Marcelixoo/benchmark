"""Background sampler for container-level CPU/memory usage during a long-running
operation (e.g. bulk indexing), using `docker stats` — no extra dependency, no
Docker SDK.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Any


def _parse_cpu_pct(raw: str) -> float | None:
    try:
        return float(raw.strip().rstrip("%"))
    except (ValueError, AttributeError):
        return None


def _parse_mem_mb(raw: str) -> float | None:
    # docker stats MemUsage looks like "512MiB / 2.5GiB" — we only want the used side.
    used = raw.split("/")[0].strip()
    try:
        if used.endswith("GiB"):
            return float(used[:-3]) * 1024
        if used.endswith("MiB"):
            return float(used[:-3])
        if used.endswith("KiB"):
            return float(used[:-3]) / 1024
        if used.endswith("B"):
            return float(used[:-1]) / (1024 * 1024)
    except ValueError:
        return None
    return None


class ContainerStatsSampler:
    """Polls `docker stats --no-stream` for the given containers on a background
    thread every `interval_s` seconds until `.stop()` is called."""

    def __init__(self, containers: list[str], interval_s: float = 2.0) -> None:
        self._containers = containers
        self._interval_s = interval_s
        self._samples: dict[str, list[dict[str, float]]] = {c: [] for c in containers}
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _poll_once(self) -> None:
        try:
            out = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", *self._containers],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            return
        for line in out.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = row.get("Name") or row.get("Container")
            if name not in self._samples:
                continue
            cpu = _parse_cpu_pct(row.get("CPUPerc", ""))
            mem = _parse_mem_mb(row.get("MemUsage", ""))
            if cpu is not None or mem is not None:
                self._samples[name].append({"cpu_pct": cpu, "mem_mb": mem})

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            self._stop_event.wait(self._interval_s)

    def start(self) -> "ContainerStatsSampler":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=self._interval_s * 2)

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for container, samples in self._samples.items():
            cpu_vals = [s["cpu_pct"] for s in samples if s["cpu_pct"] is not None]
            mem_vals = [s["mem_mb"] for s in samples if s["mem_mb"] is not None]
            result[container] = {
                "sample_count": len(samples),
                "cpu_pct_avg": round(sum(cpu_vals) / len(cpu_vals), 2) if cpu_vals else None,
                "cpu_pct_max": round(max(cpu_vals), 2) if cpu_vals else None,
                "mem_mb_avg": round(sum(mem_vals) / len(mem_vals), 1) if mem_vals else None,
                "mem_mb_max": round(max(mem_vals), 1) if mem_vals else None,
            }
        return result
