"""Task 5/6: index the initial (~80%) corpus into a configured system and record throughput/size/memory.

Requires systems.<system>.base_url to be set in config/benchmark.yaml.
"""
from __future__ import annotations

import argparse
import time

import pandas as pd

from scripts.lib import report, system_info
from scripts.lib.config import data_dir, load_config, reports_dir
from scripts.lib.opensearch_client import SystemNotConfiguredError, client, fetch_stats, index_batch, require_write_url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["local_index", "shared_index"])
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
    batch_size = config["workload"]["index_batch_size"]

    print(f"Indexing {len(df):,} documents into '{args.system}' at {write_url} (batch_size={batch_size}) ...")
    start = time.perf_counter()
    indexed = 0
    for start_i in range(0, len(df), batch_size):
        batch = df.iloc[start_i : start_i + batch_size].to_dict(orient="records")
        indexed += index_batch(os_client, index_name, "asin", batch)
    duration_s = time.perf_counter() - start

    stats = fetch_stats(os_client, index_name)

    result = {
        "system": args.system,
        "write_base_url": write_url,
        "documents_indexed": indexed,
        "duration_s": round(duration_s, 3),
        "docs_per_s": round(indexed / duration_s, 2) if duration_s > 0 else None,
        "reported_index_stats": stats,
        "system_info": system_info.snapshot(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    out_dir = reports_dir(config)
    out_path = out_dir / f"index_run_{args.system}_{int(time.time())}.json"
    report.write_json(out_path, result)
    print(result)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
