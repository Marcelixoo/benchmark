"""Canonical-baseline build/restore for the formal experiment: build the
fully-indexed corpus once per architecture, snapshot its Docker volumes, and
restore that exact snapshot before every one of the 18 formal runs — instead
of a ~25min full reindex before each run.

Never resets by deleting documents: restore always replaces volume contents
wholesale from a tarball captured right after a verified, quiescent initial
index build.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from scripts.formal.infra import COMPOSE_FILE_BY_SYSTEM, CONTAINERS_BY_SYSTEM, ENV_FILE, OTHER_SYSTEM, VOLUMES_BY_SYSTEM
from scripts.lib.config import PROJECT_ROOT, load_config
from scripts.lib.opensearch_client import client, require_write_url

BASELINE_ROOT = PROJECT_ROOT / "results" / "formal" / "baselines"


class BaselineError(RuntimeError):
    pass


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=PROJECT_ROOT, check=True, **kwargs)


def _compose(system: str, *args: str) -> None:
    _run(["docker", "compose", "--env-file", str(ENV_FILE), "-f", str(COMPOSE_FILE_BY_SYSTEM[system]), *args])


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _container_health(container: str) -> str | None:
    try:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    status = out.stdout.strip()
    return status or None


def wait_healthy(system: str, timeout_s: float = 240.0, poll_s: float = 5.0) -> None:
    containers = CONTAINERS_BY_SYSTEM[system]
    deadline = time.monotonic() + timeout_s
    statuses: dict[str, str | None] = {}
    while time.monotonic() < deadline:
        statuses = {c: _container_health(c) for c in containers}
        if all(s == "healthy" for s in statuses.values()):
            print(f"All containers healthy for '{system}': {statuses}")
            return
        print(f"Waiting for '{system}' containers to become healthy: {statuses}")
        time.sleep(poll_s)
    raise BaselineError(f"'{system}' containers did not become healthy within {timeout_s}s: {statuses}")


def bring_down(system: str) -> None:
    _compose(system, "down")


def bring_up(system: str) -> None:
    """Brings `system` up, first bringing the other system down — L1 and S1
    are never run concurrently (README "Resource sizing": both are sized to
    Docker Desktop's memory cap independently, running both at once would
    also confound CPU measurements).
    """
    bring_down(OTHER_SYSTEM[system])
    _compose(system, "up", "-d")
    wait_healthy(system)


def wait_for_quiescence(system: str, consecutive_required: int = 3, interval_s: float = 5.0, max_attempts: int = 60) -> None:
    """Polls until the write thread pool queue is empty and no merges are
    in progress, `consecutive_required` times in a row. Raises if it never
    stabilizes within `max_attempts` polls.
    """
    config = load_config()
    system_config = config["systems"][system]
    write_url = require_write_url(system_config, system)
    index_name = system_config["index_name"]
    os_client = client(write_url)

    clean_streak = 0
    for attempt in range(max_attempts):
        tp = os_client.transport.perform_request("GET", "/_nodes/stats/thread_pool")
        queues = [
            node.get("thread_pool", {}).get("write", {}).get("queue", 0)
            for node in tp.get("nodes", {}).values()
        ]
        stats = os_client.indices.stats(index=index_name)
        merges_current = stats.get("_all", {}).get("primaries", {}).get("merges", {}).get("current", 0)
        clean = all(q == 0 for q in queues) and merges_current == 0
        print(f"Quiescence poll {attempt + 1}: write_queues={queues} merges_current={merges_current} clean={clean}")
        clean_streak = clean_streak + 1 if clean else 0
        if clean_streak >= consecutive_required:
            return
        time.sleep(interval_s)
    raise BaselineError(f"'{system}' never reached quiescence after {max_attempts} polls")


def snapshot_dir(system: str) -> Path:
    d = BASELINE_ROOT / system
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_volumes(system: str) -> dict[str, str]:
    """Tars each of this system's Docker volumes into snapshot_dir(system).
    Must be called while the system's containers are stopped (volumes not
    in use). Returns {volume_name: sha256} for the manifest.
    """
    out_dir = snapshot_dir(system)
    checksums: dict[str, str] = {}
    for volume in VOLUMES_BY_SYSTEM[system]:
        tar_name = f"{volume}.tar.gz"
        _run([
            "docker", "run", "--rm",
            "-v", f"{volume}:/from:ro",
            "-v", f"{out_dir}:/to",
            "alpine",
            "sh", "-c", f"tar czf /to/{tar_name} -C /from .",
        ])
        checksums[volume] = _sha256_file(out_dir / tar_name)
    return checksums


def restore_volumes(system: str) -> None:
    """Wipes and restores each of this system's Docker volumes from the
    snapshot tarballs in snapshot_dir(system). Must be called while the
    system's containers are stopped. Never deletes documents via the
    OpenSearch API — the volume itself is replaced wholesale.
    """
    out_dir = snapshot_dir(system)
    for volume in VOLUMES_BY_SYSTEM[system]:
        tar_path = out_dir / f"{volume}.tar.gz"
        if not tar_path.exists():
            raise BaselineError(f"No baseline snapshot for volume '{volume}' at {tar_path}. Run build_baseline first.")
        subprocess.run(["docker", "volume", "rm", volume], cwd=PROJECT_ROOT, capture_output=True, text=True)
        _run(["docker", "volume", "create", volume])
        _run([
            "docker", "run", "--rm",
            "-v", f"{volume}:/to",
            "-v", f"{out_dir}:/from:ro",
            "alpine",
            "sh", "-c", f"tar xzf /from/{volume}.tar.gz -C /to",
        ])


def manifest_path(system: str) -> Path:
    return snapshot_dir(system) / "manifest.json"


def load_manifest(system: str) -> dict[str, Any]:
    path = manifest_path(system)
    if not path.exists():
        raise BaselineError(f"No baseline manifest for '{system}' at {path}. Run build_baseline first.")
    with open(path) as f:
        return json.load(f)


def write_manifest(system: str, manifest: dict[str, Any]) -> None:
    manifest_path(system).write_text(json.dumps(manifest, indent=2, default=str))


def restore_baseline(system: str) -> None:
    """The per-run reset step: stop this system, wipe+restore its volumes
    from the canonical baseline snapshot, bring it back up healthy. Fails
    loudly if a manifest doesn't exist rather than silently reindexing.
    """
    load_manifest(system)
    bring_down(system)
    restore_volumes(system)
    bring_up(system)
