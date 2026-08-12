"""Task 5/6: index the initial (~80%) corpus into a configured system and record throughput/size/memory.

Requires systems.<system>.base_url to be set in config/benchmark.yaml.
"""
from __future__ import annotations

import argparse
import time

import pandas as pd

from scripts.lib import report, system_info
from scripts.lib.config import data_dir, load_config, reports_dir
from scripts.lib.docker_stats import ContainerStatsSampler
from scripts.lib.opensearch_client import (
    SystemNotConfiguredError,
    client,
    fetch_stats,
    index_batch,
    remote_store_nodes_stats,
    require_write_url,
)
from scripts.opensearch.verify_cluster import CONTAINERS_BY_SYSTEM


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["local_index", "shared_index"])
    parser.add_argument("--limit", type=int, default=None, help="Index only the first N rows (dry-run/testing only)")
    parser.add_argument(
        "--calibration", action="store_true",
        help="Label this run as calibration input (not a final benchmark result) and write to a calibration_* report path",
    )
    args = parser.parse_args()

    config = load_config()
    system_config = config["systems"][args.system]

    try:
        write_url = require_write_url(system_config, args.system)
    except SystemNotConfiguredError as e:
        raise SystemExit(f"Blocker: {e}") from None

    os_client = client(write_url)
    index_name = system_config["index_name"]

    d_dir = data_dir(config)
    corpus_path = d_dir / "corpus_initial.parquet"
    if not corpus_path.exists():
        raise SystemExit(f"Blocker: {corpus_path} does not exist yet. Run `python -m scripts.prepare_products` first.")

    df = pd.read_parquet(corpus_path)
    if args.limit:
        df = df.head(args.limit)
    batch_size = config["workload"]["index_batch_size"]

    is_shared = args.system == "shared_index"
    remote_store_before = remote_store_nodes_stats(os_client) if is_shared else None

    sampler = ContainerStatsSampler(CONTAINERS_BY_SYSTEM[args.system]).start()

    print(f"Indexing {len(df):,} documents into '{args.system}' at {write_url} (batch_size={batch_size}) ...")
    start = time.perf_counter()
    indexed = 0
    try:
        for start_i in range(0, len(df), batch_size):
            batch = df.iloc[start_i : start_i + batch_size].to_dict(orient="records")
            indexed += index_batch(os_client, index_name, "asin", batch)
        duration_s = time.perf_counter() - start
    finally:
        sampler.stop()

    stats = fetch_stats(os_client, index_name)
    remote_store_after = remote_store_nodes_stats(os_client) if is_shared else None

    result = {
        "system": args.system,
        "write_base_url": write_url,
        "calibration_run": args.calibration,
        "documents_indexed": indexed,
        "duration_s": round(duration_s, 3),
        "docs_per_s": round(indexed / duration_s, 2) if duration_s > 0 else None,
        "reported_index_stats": stats,
        "container_resource_usage": sampler.summary(),
        "remote_store_nodes_stats_before": remote_store_before,
        "remote_store_nodes_stats_after": remote_store_after,
        "system_info": system_info.snapshot(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    out_dir = reports_dir(config)
    prefix = "calibration_index" if args.calibration else "index_run"
    out_path = out_dir / f"{prefix}_{args.system}_{int(time.time())}.json"
    report.write_json(out_path, result)
    print(result)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
