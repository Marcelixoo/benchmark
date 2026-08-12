"""W1 calibration: 60s read-only rate-controlled load check against a
configured, already-populated system. Assumes the target system is already up
(does not bring up/tear down infra itself).
"""
from __future__ import annotations

import argparse
import time

import pandas as pd

from scripts.lib import report
from scripts.lib.config import data_dir, load_config, reports_dir
from scripts.lib.docker_stats import ContainerStatsSampler
from scripts.lib.load_generator import run_query_load
from scripts.lib.opensearch_client import SystemNotConfiguredError, client, require_search_url
from scripts.opensearch.verify_cluster import CONTAINERS_BY_SYSTEM


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["local_index", "shared_index"])
    parser.add_argument("--qps", type=float, required=True)
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
    except SystemNotConfiguredError as e:
        raise SystemExit(f"Blocker: {e}") from None

    os_client = client(search_url)
    index_name = system_config["index_name"]

    d_dir = data_dir(config)
    fixed_size = config["query_set"]["fixed_size"]
    fixed_path = d_dir / f"queries_fixed_{fixed_size}_corpus_relevant.parquet"
    if not fixed_path.exists():
        raise SystemExit(f"Blocker: {fixed_path} does not exist yet. Run `python -m scripts.prepare_queries` first.")

    queries_df = pd.read_parquet(fixed_path)
    queries = queries_df["query_text"].tolist()

    sampler = ContainerStatsSampler(CONTAINERS_BY_SYSTEM[args.system]).start()
    print(f"Running read-only load check on '{args.system}' at {args.qps} QPS for {args.duration_s}s ...")
    try:
        load_result = run_query_load(os_client, index_name, queries, args.qps, args.duration_s)
    finally:
        sampler.stop()

    result = {
        "system": args.system,
        "search_base_url": search_url,
        "calibration_run": args.calibration,
        "check": "w1_query_load",
        **load_result,
        "container_resource_usage": sampler.summary(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    out_dir = reports_dir(config) / "calibration" if args.calibration else reports_dir(config)
    out_path = out_dir / f"load_check_query_{args.system}_{int(time.time())}.json"
    report.write_json(out_path, result)
    print(result)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
