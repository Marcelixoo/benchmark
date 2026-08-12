"""Task 4a: measure ASIN/product_id overlap between the two datasets.

This is a measurement only — it never assumes or relies on ID alignment
between the two datasets, which were scraped independently. It exists purely
to document how much (if any) overlap exists, per the instruction not to
assume alignment.
"""
from __future__ import annotations

import time

import pandas as pd

from scripts.lib import queries as queries_lib
from scripts.lib import report
from scripts.lib.config import data_dir, load_config, reports_dir
from scripts.lib.kaggle_auth import ensure_kaggle_credentials


def main() -> None:
    ensure_kaggle_credentials()
    config = load_config()
    d_dir = data_dir(config)

    initial_path = d_dir / "corpus_initial.parquet"
    if not initial_path.exists():
        raise SystemExit(
            f"Blocker: {initial_path} does not exist yet. Run `python -m scripts.prepare_products` first."
        )
    product_asins = set(pd.read_parquet(initial_path, columns=["asin"])["asin"])

    handle = config["datasets"]["queries"]["handle"]
    file_info = queries_lib.discover_examples_file(handle)
    product_id_col = file_info.get("product_id_col")

    if not product_id_col:
        result = {
            "compatible_check": "skipped",
            "reason": f"No product-id-like column found in {file_info['path']} "
            f"(looked for {queries_lib.PRODUCT_ID_CANDIDATES}).",
        }
    else:
        path = file_info["path"]
        if path.suffix == ".parquet":
            raw_df = pd.read_parquet(path, columns=[product_id_col])
        else:
            raw_df = pd.read_csv(path, usecols=[product_id_col])
        query_dataset_ids = set(raw_df[product_id_col].astype(str))
        overlap = product_asins & {str(a) for a in query_dataset_ids}
        result = {
            "product_id_column_in_query_dataset": product_id_col,
            "initial_corpus_asin_count": len(product_asins),
            "query_dataset_unique_product_id_count": len(query_dataset_ids),
            "overlap_count": len(overlap),
            "overlap_pct_of_initial_corpus": round(100 * len(overlap) / len(product_asins), 4) if product_asins else 0,
            "overlap_pct_of_query_dataset_ids": (
                round(100 * len(overlap) / len(query_dataset_ids), 4) if query_dataset_ids else 0
            ),
            "note": (
                "Overlap is reported for information only. The two datasets were scraped "
                "independently and product IDs are NOT assumed to align — queries are used "
                "purely as realistic search-query text, not for ID-based relevance evaluation."
            ),
        }

    result["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out_dir = reports_dir(config)
    report.write_json(out_dir / "compatibility_report.json", result)
    print(f"Wrote {out_dir / 'compatibility_report.json'}")
    print(result)


if __name__ == "__main__":
    main()
