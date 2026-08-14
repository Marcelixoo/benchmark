"""Pre-run verification gate: run before every formal run (after
baseline.restore_baseline) to confirm the cluster is actually in the
canonical baseline state before spending 180s+ of measurement time on it.
Extends scripts.opensearch.verify_cluster's checks (imported, not
reimplemented) with baseline-specific ones: exact doc count, quiescence,
and dataset/query file checksum match against the frozen manifest.
"""
from __future__ import annotations

import time

from scripts.formal import baseline
from scripts.formal.build_baseline import DATA_FILES
from scripts.formal.infra import CONTAINERS_BY_SYSTEM
from scripts.lib.config import data_dir, load_config
from scripts.lib.opensearch_client import client, require_write_url
from scripts.opensearch.verify_cluster import docker_inspect, get_cluster_setting

# Docker reports containers "healthy" before OpenSearch has necessarily
# finished allocating shards / before search-capable nodes can reliably
# serve requests -- observed both as a red cluster_health with unassigned
# shards (local_index) and as TransportError(503,
# 'search_phase_execution_exception') on count() (shared_index), both
# within a ~10-20s window right after restore_baseline() returns. Poll
# for real readiness before running any gate checks, instead of failing
# the whole gate on this transient.
READINESS_RETRY_TIMEOUT_S = 30.0
READINESS_RETRY_INTERVAL_S = 2.0
COUNT_RETRY_TIMEOUT_S = 20.0
COUNT_RETRY_INTERVAL_S = 2.0


def _wait_for_cluster_ready(os_client, timeout_s: float = READINESS_RETRY_TIMEOUT_S,
                             interval_s: float = READINESS_RETRY_INTERVAL_S) -> dict:
    deadline = time.monotonic() + timeout_s
    health = os_client.cluster.health()
    while True:
        if health.get("status") in ("green", "yellow") and health.get("unassigned_shards", 1) == 0:
            return health
        if time.monotonic() >= deadline:
            return health
        time.sleep(interval_s)
        health = os_client.cluster.health()


def _count_with_retry(os_client, index_name: str, timeout_s: float = COUNT_RETRY_TIMEOUT_S,
                       interval_s: float = COUNT_RETRY_INTERVAL_S):
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return os_client.count(index=index_name)
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(interval_s)


def run_gate(system: str) -> dict:
    config = load_config()
    system_config = config["systems"][system]
    d_dir = data_dir(config)
    write_url = require_write_url(system_config, system)
    os_client = client(write_url)
    index_name = system_config["index_name"]
    is_shared = system == "shared_index"

    checks: list[dict] = []

    def check(name: str, passed: bool, detail: object) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    try:
        manifest = baseline.load_manifest(system)
    except baseline.BaselineError as e:
        check("baseline_manifest_exists", False, str(e))
        return {"system": system, "all_passed": False, "checks": checks,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    check("baseline_manifest_exists", True, str(baseline.manifest_path(system)))

    health = _wait_for_cluster_ready(os_client)
    check("cluster_health_not_red", health.get("status") in ("green", "yellow"), health)
    check("no_unassigned_shards", health.get("unassigned_shards", 1) == 0, health.get("unassigned_shards"))

    remote_state_enabled = get_cluster_setting(os_client, "cluster.remote_store.state.enabled")
    remote_state_enabled_bool = str(remote_state_enabled).lower() == "true"
    if is_shared:
        check("remote_store_enabled", remote_state_enabled_bool, remote_state_enabled)
    else:
        check("remote_store_disabled", not remote_state_enabled_bool, remote_state_enabled)

    os_client.indices.refresh(index=index_name)
    time.sleep(1)
    doc_count = _count_with_retry(os_client, index_name).get("count")
    check("document_count_matches_baseline", doc_count == manifest["doc_count"],
          {"expected": manifest["doc_count"], "actual": doc_count})

    for name in DATA_FILES:
        path = d_dir / name
        expected = manifest["data_file_checksums"].get(name)
        actual = baseline._sha256_file(path) if path.exists() else None
        check(f"checksum_matches::{name}", expected is not None and expected == actual,
              {"expected": expected, "actual": actual})

    tp = os_client.transport.perform_request("GET", "/_nodes/stats/thread_pool")
    queues = [node.get("thread_pool", {}).get("write", {}).get("queue", 0) for node in tp.get("nodes", {}).values()]
    check("write_thread_pool_queue_empty", all(q == 0 for q in queues), queues)

    stats = os_client.indices.stats(index=index_name)
    merges_current = stats.get("_all", {}).get("primaries", {}).get("merges", {}).get("current", 0)
    check("no_active_merges", merges_current == 0, merges_current)

    resource_checks = {}
    for container in CONTAINERS_BY_SYSTEM[system]:
        info = docker_inspect(container)
        resource_checks[container] = None if info is None else {
            "NanoCpus": info.get("HostConfig", {}).get("NanoCpus"),
            "Memory": info.get("HostConfig", {}).get("Memory"),
        }
    check("resource_limits_applied", all(v is not None and v.get("Memory") for v in resource_checks.values()), resource_checks)

    all_passed = all(c["passed"] for c in checks)
    return {"system": system, "all_passed": all_passed, "checks": checks,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["local_index", "shared_index"])
    args = parser.parse_args()
    result = run_gate(args.system)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result["all_passed"] else 1)
