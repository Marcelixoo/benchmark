"""Task 2: dedupe, select required columns, and produce the deterministic 80/20 product split."""
from __future__ import annotations

import time

from scripts.lib import products, report
from scripts.lib.config import data_dir, load_config
from scripts.lib.kaggle_auth import ensure_kaggle_credentials


def main() -> None:
    ensure_kaggle_credentials()
    config = load_config()
    seed = config["seed"]
    offline_fraction = config["split"]["offline_fraction"]
    out_dir = data_dir(config)

    print("Loading raw product corpus ...")
    raw_df = products.load_raw(config)
    raw_rows = len(raw_df)

    print("De-duplicating by asin and selecting required columns ...")
    selected_df, dup_dropped = products.clean_and_select(raw_df)

    print("Writing category lookup (id -> category_name) ...")
    categories_df = products.load_categories(config)
    categories_df.to_parquet(out_dir / "category_lookup.parquet", index=False)

    print(f"Splitting {len(selected_df):,} rows with seed={seed}, offline_fraction={offline_fraction} ...")
    initial_df, writes_df = products.split(selected_df, seed=seed, offline_fraction=offline_fraction)

    initial_path = out_dir / "corpus_initial.parquet"
    writes_path = out_dir / "corpus_writes.parquet"
    initial_df.to_parquet(initial_path, index=False)
    writes_df.to_parquet(writes_path, index=False)

    metadata = {
        "dataset_handle": config["datasets"]["products"]["handle"],
        "random_seed": seed,
        "offline_fraction": offline_fraction,
        "raw_row_count": raw_rows,
        "duplicate_asin_rows_dropped": dup_dropped,
        "deduplicated_row_count": len(selected_df),
        "initial_corpus_rows": len(initial_df),
        "write_corpus_rows": len(writes_df),
        "columns": list(selected_df.columns),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    report.write_json(out_dir / "split_metadata.json", metadata)

    print(f"Wrote {initial_path} ({len(initial_df):,} rows)")
    print(f"Wrote {writes_path} ({len(writes_df):,} rows)")
    print(f"Wrote {out_dir / 'split_metadata.json'}")
    print(metadata)


if __name__ == "__main__":
    main()
