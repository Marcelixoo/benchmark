"""W2 calibration: one throwaway S1-only run combining read-only query load
with write load simultaneously, checking only whether S1 sustains the write
rate without an OpenSearch-side backlog (thread-pool rejections) or write
errors. Latency is not evaluated by this check. Assumes shared_index is
already up.
"""
from __future__ import annotations

import argparse
import threading
import time

import pandas as pd

from scripts.lib import report
from scripts.lib.config import data_dir, load_config, reports_dir
from scripts.lib.docker_stats import ContainerStatsSampler
from scripts.lib.load_generator import run_query_load, run_write_load
from scripts.lib.opensearch_client import (
    SystemNotConfiguredError,
    client,
    require_search_url,
    require_write_url,
    thread_pool_write_stats,
)
from scripts.opensearch.verify_cluster import CONTAINERS_BY_SYSTEM


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["shared_index"])
    parser.add_argument("--qps", type=float, required=True)
    parser.add_argument("--docs-per-s", type=float, required=True)
    parser.add_argument("--duration-s", type=float, default=60.0)
    parser.add_argument(
        "--calibration", action="store_true",
        help="Label this run as calibration input (not a final benchmark result)",
    )
    args = parser.parse_args()

    config = load_config()
    system_config = config["systems"][args.system]

    try:
        search_url = require_search_url(system_config, args.system)
        write_url = require_write_url(system_config, args.system)
    except SystemNotConfiguredError as e:
        raise SystemExit(f"Blocker: {e}") from None

    search_client = client(search_url)
    write_client = client(write_url)
    index_name = system_config["index_name"]

    d_dir = data_dir(config)
    fixed_size = config["query_set"]["fixed_size"]
    fixed_path = d_dir / f"queries_fixed_{fixed_size}_corpus_relevant.parquet"
    if not fixed_path.exists():
        raise SystemExit(f"Blocker: {fixed_path} does not exist yet. Run `python -m scripts.prepare_queries` first.")
    queries = pd.read_parquet(fixed_path)["query_text"].tolist()

    writes_path = d_dir / "corpus_writes.parquet"
    if not writes_path.exists():
        raise SystemExit(f"Blocker: {writes_path} does not exist yet. Run `python -m scripts.prepare_products` first.")
    write_documents = pd.read_parquet(writes_path).to_dict(orient="records")

    thread_pool_before = thread_pool_write_stats(write_client)

    sampler = ContainerStatsSampler(CONTAINERS_BY_SYSTEM[args.system]).start()
    print(
        f"Running W2 combined load check on '{args.system}': "
        f"{args.qps} QPS query + {args.docs_per_s} docs/s write, for {args.duration_s}s ..."
    )

    query_result: dict = {}
    write_result: dict = {}

    def _do_query_load() -> None:
        nonlocal query_result
        query_result = run_query_load(search_client, index_name, queries, args.qps, args.duration_s)

    def _do_write_load() -> None:
        nonlocal write_result
        write_result = run_write_load(
            write_client, index_name, "asin", write_documents, args.docs_per_s, args.duration_s
        )

    query_thread = threading.Thread(target=_do_query_load)
    write_thread = threading.Thread(target=_do_write_load)
    try:
        query_thread.start()
        write_thread.start()
        query_thread.join()
        write_thread.join()
    finally:
        sampler.stop()

    thread_pool_after = thread_pool_write_stats(write_client)

    result = {
        "system": args.system,
        "search_base_url": search_url,
        "write_base_url": write_url,
        "calibration_run": args.calibration,
        "check": "w2_combined_load",
        "query_load": query_result,
        "write_load": write_result,
        "thread_pool_write_stats_before": thread_pool_before,
        "thread_pool_write_stats_after": thread_pool_after,
        "container_resource_usage": sampler.summary(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    out_dir = reports_dir(config) / "calibration" if args.calibration else reports_dir(config)
    out_path = out_dir / f"load_check_w2_{args.system}_{int(time.time())}.json"
    report.write_json(out_path, result)
    print(result)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
