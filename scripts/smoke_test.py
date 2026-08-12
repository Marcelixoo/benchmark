"""Task 4b: smoke-test a configured system with a sample of the fixed query set.

Requires systems.<system>.base_url to be set in config/benchmark.yaml. Exits
with an explicit blocker message (no fabricated results) if it isn't.
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from scripts.lib import report
from scripts.lib.config import data_dir, load_config, reports_dir
from scripts.lib.http_client import SystemNotConfiguredError, require_base_url, search


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["local_index", "shared_index"])
    parser.add_argument("--n-queries", type=int, default=None)
    args = parser.parse_args()

    config = load_config()
    system_config = config["systems"][args.system]

    try:
        base_url = require_base_url(system_config, args.system)
    except SystemNotConfiguredError as e:
        raise SystemExit(f"Blocker: {e}") from None

    d_dir = data_dir(config)
    fixed_size = config["query_set"]["fixed_size"]
    fixed_path = d_dir / f"queries_fixed_{fixed_size}_corpus_relevant.parquet"
    if not fixed_path.exists():
        raise SystemExit(f"Blocker: {fixed_path} does not exist yet. Run `python -m scripts.prepare_queries` first.")

    n = args.n_queries or config["workload"]["smoke_test_query_count"]
    queries_df = pd.read_parquet(fixed_path)
    sample_df = queries_df.head(min(n, len(queries_df)))

    result_counts: list[int] = []
    inspect_rows: list[dict] = []
    inspect_count = config["workload"]["smoke_test_sample_inspect_count"]

    for i, row in enumerate(sample_df.itertuples(index=False)):
        resp = search(base_url, system_config["search_path"], row.query_text)
        try:
            payload = resp.json()
            results = payload.get("results", [])
        except Exception:
            results = []
        result_counts.append(len(results))
        if i < inspect_count:
            inspect_rows.append({"query": row.query_text, "result_count": len(results), "results": results[:5]})

    counts = np.array(result_counts)
    summary = {
        "system": args.system,
        "base_url": base_url,
        "queries_run": len(sample_df),
        "pct_with_at_least_one_result": round(100 * float((counts >= 1).mean()), 2) if len(counts) else None,
        "zero_result_rate_pct": round(100 * float((counts == 0).mean()), 2) if len(counts) else None,
        "median_result_count": float(np.median(counts)) if len(counts) else None,
        "p95_result_count": float(np.percentile(counts, 95)) if len(counts) else None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    out_dir = reports_dir(config)
    report.write_json(out_dir / f"smoke_test_{args.system}.json", {"summary": summary, "sample_for_manual_review": inspect_rows})
    print(summary)
    print(f"Wrote {out_dir / f'smoke_test_{args.system}.json'} — manually review the {inspect_count} sampled queries in it.")


if __name__ == "__main__":
    main()
