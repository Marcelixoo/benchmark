"""Task 3: filter to preferred locale, restrict to corpus-relevant queries,
dedupe queries, and produce the deterministic fixed query set.

"Corpus-relevant" means: at least one of the query's judged product_ids is
present in our prepared offline product corpus (data/corpus_initial.parquet).
This is still an ID-overlap check, not a content-relevance guarantee (see
scripts/validate_compatibility.py) — it exists so the benchmark's queries are
not, by construction, mostly guaranteed to return zero results against a
corpus that was independently sampled from a different scrape.
"""
from __future__ import annotations

import time

import pandas as pd

from scripts.lib import queries, report
from scripts.lib.config import data_dir, load_config
from scripts.lib.kaggle_auth import ensure_kaggle_credentials


def main() -> None:
    ensure_kaggle_credentials()
    config = load_config()
    seed = config["seed"]
    fixed_size = config["query_set"]["fixed_size"]
    preferred_locale = config["datasets"]["queries"]["preferred_locale"]
    out_dir = data_dir(config)

    corpus_path = out_dir / "corpus_initial.parquet"
    if not corpus_path.exists():
        raise SystemExit(f"Blocker: {corpus_path} does not exist yet. Run `python -m scripts.prepare_products` first.")
    corpus_asins = set(pd.read_parquet(corpus_path, columns=["asin"])["asin"].astype(str))

    handle = config["datasets"]["queries"]["handle"]
    print(f"Discovering query-judgment file in {handle} ...")
    file_info = queries.discover_examples_file(handle)
    raw_df = queries.load_raw(file_info)
    raw_rows = len(raw_df)

    print(f"Restricting to queries with >=1 judged product_id present in {corpus_path.name} ...")
    corpus_relevant_df = queries.restrict_to_corpus_relevant(raw_df, file_info, corpus_asins)

    print(f"Filtering to locale={preferred_locale!r} and de-duplicating ...")
    full_df = queries.clean_and_dedupe(corpus_relevant_df, file_info, preferred_locale=preferred_locale)

    print(f"Sampling a deterministic fixed set of {fixed_size} queries with seed={seed} ...")
    fixed_df = queries.sample_fixed(full_df, seed=seed, n=fixed_size)

    full_path = out_dir / "queries_full_corpus_relevant.parquet"
    fixed_path = out_dir / f"queries_fixed_{fixed_size}_corpus_relevant.parquet"
    full_df.to_parquet(full_path, index=False)
    fixed_df.to_parquet(fixed_path, index=False)

    # Replace the earlier, unfiltered outputs so there's exactly one query set
    # in play (avoids someone accidentally consuming the stale unfiltered files).
    for stale_name in ["queries_full.parquet", f"queries_fixed_{fixed_size}.parquet"]:
        stale_path = out_dir / stale_name
        if stale_path.exists():
            stale_path.unlink()

    metadata = {
        "dataset_handle": handle,
        "source_file": str(file_info["path"]),
        "random_seed": seed,
        "preferred_locale": preferred_locale,
        "raw_row_count": raw_rows,
        "corpus_relevance_filter": {
            "applied": True,
            "definition": "query kept iff >=1 judged product_id is present in data/corpus_initial.parquet asins",
            "corpus_file": str(corpus_path),
            "corpus_asin_count": len(corpus_asins),
            "raw_rows_before_filter": raw_rows,
            "raw_rows_after_filter": len(corpus_relevant_df),
        },
        "deduplicated_query_count": len(full_df),
        "fixed_query_set_size": len(fixed_df),
        "dedupe_key": file_info["query_id_col"] if file_info["query_id_col"] in raw_df.columns else "normalized_text",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    report.write_json(out_dir / "query_metadata.json", metadata)

    print(f"Wrote {full_path} ({len(full_df):,} rows)")
    print(f"Wrote {fixed_path} ({len(fixed_df):,} rows)")
    print(f"Wrote {out_dir / 'query_metadata.json'}")
    print(metadata)


if __name__ == "__main__":
    main()
