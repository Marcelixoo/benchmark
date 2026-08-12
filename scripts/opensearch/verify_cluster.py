"""python -m scripts.opensearch.verify_cluster --system local_index|shared_index

Implements the 10-point pre-experiment verification checklist as real,
automated checks against a running cluster (not a narrative doc). Writes
data/reports/verify_<system>.json and exits non-zero if any check fails —
never prints a green summary for a check it couldn't actually verify.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time

from opensearchpy.exceptions import NotFoundError

from scripts.lib import report
from scripts.lib.config import load_config, reports_dir
from scripts.lib.opensearch_client import SystemNotConfiguredError, client, require_search_url, require_write_url
from scripts.opensearch.index_spec import MAPPINGS

TEST_INDEX = "verify_test_products"

CONTAINERS_BY_SYSTEM = {
    "local_index": ["os-l1-1", "os-l1-2"],
    "shared_index": ["os-s1-data", "os-s1-search"],
}


def docker_inspect(container: str) -> dict | None:
    try:
        out = subprocess.run(
            ["docker", "inspect", container], capture_output=True, text=True, timeout=10, check=True
        )
        return json.loads(out.stdout)[0]
    except Exception:
        return None


def get_cluster_setting(os_client, key: str):
    settings = os_client.cluster.get_settings(include_defaults=True)
    for scope in ("persistent", "transient", "defaults"):
        node = settings.get(scope, {})
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is not None:
            return node
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["local_index", "shared_index"])
    args = parser.parse_args()

    config = load_config()
    system_config = config["systems"][args.system]

    try:
        write_url = require_write_url(system_config, args.system)
        search_url = require_search_url(system_config, args.system)
    except SystemNotConfiguredError as e:
        raise SystemExit(f"Blocker: {e}") from None

    write_client = client(write_url)
    search_client = client(search_url)
    is_shared = args.system == "shared_index"

    checks: list[dict] = []

    def check(name: str, passed: bool, detail: object) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    # 1. Cluster health
    health = write_client.cluster.health()
    check("cluster_health_not_red", health.get("status") in ("green", "yellow"), health)

    # 2. Expected shard allocation (no unassigned shards)
    check("no_unassigned_shards", health.get("unassigned_shards", 1) == 0, health.get("unassigned_shards"))

    # 3 / 4. Remote store enabled/disabled as expected for this system
    remote_state_enabled = get_cluster_setting(write_client, "cluster.remote_store.state.enabled")
    remote_state_enabled_bool = str(remote_state_enabled).lower() == "true"
    if is_shared:
        check("remote_store_enabled", remote_state_enabled_bool, remote_state_enabled)
    else:
        check("remote_store_disabled", not remote_state_enabled_bool, remote_state_enabled)

    # Create a throwaway test index with this system's real settings/mappings to
    # exercise checks 5-9 against actual cluster behavior, not just static config.
    from scripts.opensearch.index_spec import SETTINGS_BY_SYSTEM

    try:
        if write_client.indices.exists(index=TEST_INDEX):
            write_client.indices.delete(index=TEST_INDEX)
        write_client.indices.create(
            index=TEST_INDEX, body={"settings": SETTINGS_BY_SYSTEM[args.system], "mappings": MAPPINGS}
        )
        created_settings = write_client.indices.get_settings(index=TEST_INDEX)[TEST_INDEX]["settings"]["index"]

        # 5. Search-role node + search replica present (S1 only)
        if is_shared:
            nodes = write_client.cat.nodes(params={"h": "name,node.role"}, format="json")
            # cat.nodes abbreviates roles (e.g. "dim" = data+ingest+cluster_manager,
            # "s" = search) rather than spelling them out — checking for the substring
            # "search" here never matches and silently marked this check "no search
            # node found" even when one was present.
            has_search_node = any("s" in n.get("node.role", "") for n in nodes)
            has_search_replica = int(created_settings.get("number_of_search_replicas", 0)) >= 1
            check("search_node_and_search_replica_present", has_search_node and has_search_replica,
                  {"nodes": nodes, "number_of_search_replicas": created_settings.get("number_of_search_replicas")})
        else:
            check("search_node_and_search_replica_present", True, "not applicable for local_index")

        # 6. Strict search-replica routing setting
        strict_routing = get_cluster_setting(write_client, "cluster.routing.search_replica.strict")
        if is_shared:
            check("strict_search_replica_routing", str(strict_routing).lower() == "true", strict_routing)
        else:
            check("strict_search_replica_routing", True, "not applicable for local_index")

        # 7. Mappings match the shared spec (settings intentionally differ by design)
        created_mappings = write_client.indices.get_mapping(index=TEST_INDEX)[TEST_INDEX]["mappings"]
        check("mappings_match_shared_spec", created_mappings.get("properties", {}).keys() == MAPPINGS["properties"].keys(),
              {"expected_fields": sorted(MAPPINGS["properties"].keys()), "actual_fields": sorted(created_mappings.get("properties", {}).keys())})

        # 8. Document count parity: index N docs, refresh, count must equal N
        test_docs = [{"asin": f"TESTASIN{i}", "title": f"test product {i}", "category_id": "test",
                      "price": 1.0, "stars": 5.0, "reviews": 0, "isBestSeller": False} for i in range(5)]
        for doc in test_docs:
            write_client.index(index=TEST_INDEX, id=doc["asin"], body=doc)
        write_client.indices.refresh(index=TEST_INDEX)
        # On S1, strict search-replica routing means count() (a search-type request)
        # can be served by the search replica, which lags the primary by up to one
        # segment-replication cycle after refresh — without this wait the count is
        # read before that replication completes.
        time.sleep(1)
        count_resp = write_client.count(index=TEST_INDEX)
        check("document_count_matches_indexed", count_resp.get("count") == len(test_docs),
              {"expected": len(test_docs), "actual": count_resp.get("count")})

        # 9. Query smoke test via the search endpoint (may be a different node for S1)
        time.sleep(1)  # let segment replication / search replica catch up before searching
        search_resp = search_client.search(index=TEST_INDEX, body={"query": {"match_all": {}}})
        hit_count = search_resp.get("hits", {}).get("total", {}).get("value", 0)
        check("query_smoke_test_succeeds", hit_count >= 1, {"hits_returned": hit_count})
    finally:
        try:
            write_client.indices.delete(index=TEST_INDEX)
        except NotFoundError:
            pass

    # 10. Resource limits actually applied, per container
    resource_checks = {}
    for container in CONTAINERS_BY_SYSTEM[args.system]:
        info = docker_inspect(container)
        if info is None:
            resource_checks[container] = None
        else:
            host_config = info.get("HostConfig", {})
            resource_checks[container] = {
                "NanoCpus": host_config.get("NanoCpus"),
                "Memory": host_config.get("Memory"),
            }
    check("resource_limits_applied", all(v is not None and v.get("Memory") for v in resource_checks.values()), resource_checks)

    out_dir = reports_dir(config)
    all_passed = all(c["passed"] for c in checks)
    result = {"system": args.system, "all_passed": all_passed, "checks": checks,
              "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    report.write_json(out_dir / f"verify_{args.system}.json", result)

    for c in checks:
        print(f"{'PASS' if c['passed'] else 'FAIL'}  {c['name']}")

    if not all_passed:
        raise SystemExit(f"Blocker: verify_cluster failed for '{args.system}' — see failing checks above and data/reports/verify_{args.system}.json")

    print(f"All checks passed for '{args.system}'. Wrote {out_dir / f'verify_{args.system}.json'}")


if __name__ == "__main__":
    main()
