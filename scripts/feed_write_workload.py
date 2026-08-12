"""Task 5: replay the held-out (~20%) corpus as a concurrent-write indexing feed.

Requires systems.<system>.base_url to be set in config/benchmark.yaml. Uses
the same batching/metrics approach as index_initial_corpus.py, at a smaller
batch size to emulate incremental writes rather than a bulk initial load.
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
    corpus_path = d_dir / "corpus_writes.parquet"
    if not corpus_path.exists():
        raise SystemExit(f"Blocker: {corpus_path} does not exist yet. Run `python -m scripts.prepare_products` first.")

    df = pd.read_parquet(corpus_path)
    batch_size = config["workload"]["write_feed_batch_size"]

    print(f"Feeding {len(df):,} write documents into '{args.system}' at {write_url} (batch_size={batch_size}) ...")
    batch_log: list[dict] = []
    start = time.perf_counter()
    indexed = 0
    for start_i in range(0, len(df), batch_size):
        batch_start = time.perf_counter()
        batch = df.iloc[start_i : start_i + batch_size].to_dict(orient="records")
        indexed += index_batch(os_client, index_name, "asin", batch)
        batch_log.append({"batch_start_index": start_i, "batch_size": len(batch), "duration_s": round(time.perf_counter() - batch_start, 4)})
    duration_s = time.perf_counter() - start

    stats = fetch_stats(os_client, index_name)

    result = {
        "system": args.system,
        "write_base_url": write_url,
        "documents_indexed": indexed,
        "duration_s": round(duration_s, 3),
        "docs_per_s": round(indexed / duration_s, 2) if duration_s > 0 else None,
        "batch_count": len(batch_log),
        "reported_index_stats": stats,
        "system_info": system_info.snapshot(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    out_dir = reports_dir(config)
    out_path = out_dir / f"write_feed_{args.system}_{int(time.time())}.json"
    report.write_json(out_path, {"summary": result, "batch_log": batch_log})
    print(result)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
