# Local Index vs Shared Index — Benchmark Data Scaffold

Reproducible data preparation and benchmark scaffold for a Bachelor Thesis
experiment comparing two ecommerce search retrieval architectures
("Local Index" vs "Shared Index") under concurrent indexing.

This repo does **not** implement either architecture. It prepares:

- a deterministic 80/20 product corpus split (offline ingestion / online write
  feed) from the Amazon products dataset, and
- a deterministic, deduplicated query set (realistic search-query *text* only,
  never traffic frequency) from the Amazon ESCI query-product dataset,

so that both architectures under test can be fed byte-identical inputs.

## Datasets

| Purpose | Kaggle handle | Role |
|---|---|---|
| Product corpus | `asaniczka/amazon-products-dataset-2023-1-4m-products` (`amazon_products.csv`) | Source of all indexed documents |
| Query text | `abhishekmungoli/amazon-query-product-search` (Amazon ESCI "Shopping Queries Dataset") | Source of realistic query **text** only — **not** used as a production traffic/QPS log |

The query dataset ships as parquet files (`shopping_queries_dataset_examples.parquet`,
2,621,288 rows), one row per query–product **judgment**, not per unique query —
each `query_id` repeats once per judged product (up to ~40x). Query set
preparation collapses this to one row per unique query and never treats
judgment-row repetition as a frequency signal.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Kaggle auth (for public datasets this often isn't required, but set it up to
avoid rate limits): put `KAGGLE_USERNAME` and `KAGGLE_KEY` in `.env` (not
`KAGGLE_API_TOKEN`, which kagglehub does not read), or create
`~/.kaggle/kaggle.json`.

All scripts are run as modules from the repo root and read settings
(random seed, dataset handles, split/query-set sizes, system endpoints) from
`config/benchmark.yaml`.

## Reproduction commands, in order

```bash
python -m scripts.inspect_products        # data/reports/product_corpus_report.{json,md}
python -m scripts.inspect_queries         # data/reports/query_corpus_report.{json,md}
python -m scripts.prepare_products        # data/corpus_initial.parquet, data/corpus_writes.parquet, data/category_lookup.parquet, data/split_metadata.json
python -m scripts.prepare_queries         # data/queries_full_corpus_relevant.parquet, data/queries_fixed_5000_corpus_relevant.parquet, data/query_metadata.json — requires corpus_initial.parquet (run prepare_products first)
python -m scripts.validate_compatibility  # data/reports/compatibility_report.json
```

Then, once a Local Index or Shared Index service exists and its URL is filled
into `config/benchmark.yaml` (`systems.local_index.base_url` /
`systems.shared_index.base_url`, both `null` today):

```bash
python -m scripts.smoke_test --system local_index
python -m scripts.index_initial_corpus --system local_index
python -m scripts.feed_write_workload --system local_index
```

(repeat with `--system shared_index`). Until `base_url` is configured, these
three scripts exit immediately with a `Blocker: ...` message rather than
running against a stand-in — verified during this data-prep pass.

## Results (this run, seed=42)

### Product corpus (`inspect_products`)

- **1,426,337 rows**, 11 columns, **0 duplicate ASINs**, 0% nulls in any column
- Columns: `asin, title, imgUrl, productURL, stars, reviews, price, listPrice, category_id, isBestSeller, boughtInLastMonth`
- Source file size: 375,936,400 bytes (~358.5 MB)

### 80/20 product split (`prepare_products`)

- Deduplicated by `asin` (0 duplicate rows dropped — corpus was already unique)
- Kept columns: `asin, title, category_id, price, stars, reviews, isBestSeller` (no images/URLs)
- `category_id` → `category_name` lookup persisted separately (`data/category_lookup.parquet`), not bloating the lean corpus
- **Offline ingestion corpus (80%): 1,141,069 rows** → `data/corpus_initial.parquet`
- **Online write feed (20%): 285,268 rows** → `data/corpus_writes.parquet`
- Split is a deterministic `numpy.random.default_rng(42)` permutation — identical every run, identical for both systems under test

### Query corpus (`inspect_queries`)

- Source: `shopping_queries_dataset_examples.parquet`
- **2,621,288 raw judgment rows** (not query count — see note above)
- **130,652 unique `query_id`s**, 130,193 unique normalized query strings
- Locale breakdown: `us` 1,818,825 / `jp` 446,053 / `es` 356,410
- 0% nulls in any column

### Prepared query set (`prepare_queries`)

Restricted to **corpus-relevant** queries: a query is kept only if at least
one of its judged `product_id`s is present in `data/corpus_initial.parquet`
(1,141,069 ASINs). This is still an ID-overlap check, not a content-relevance
guarantee — see the compatibility caveat below — but it avoids a benchmark
query set where a large majority of queries are, by construction, guaranteed
to return zero results regardless of search quality, since the two datasets
were scraped independently.

- Raw judgment rows before this filter: 2,621,288 → after: 247,810 (rows, not unique queries)
- Then filtered to `product_locale == "us"` and deduplicated by `query_id`
- **60,141 unique corpus-relevant queries** → `data/queries_full_corpus_relevant.parquet`
- **Fixed deterministic sample: 5,000 queries** (seed=42) → `data/queries_fixed_5000_corpus_relevant.parquet`

The earlier, unfiltered `queries_full.parquet` / `queries_fixed_5000.parquet`
were replaced by these corpus-relevant versions and are no longer produced.

### Cross-dataset compatibility (`validate_compatibility`)

Measurement only — overlap is **not** used to join, filter, or assume ID
alignment between the two independently-scraped datasets:

- Initial corpus ASINs: 1,141,069
- Unique `product_id`s in query dataset: 1,802,772
- **Overlap: 105,118 IDs** — 9.21% of the offline corpus, 5.83% of the query dataset's product IDs

> **Methodology note:** the original spec's query-preparation step drew the
> fixed query sample from the full deduplicated query set with no relevance
> filtering, and `validate_compatibility` was explicitly measurement-only. The
> corpus-relevance filter in `prepare_queries.py` above is a deliberate,
> explicitly-requested deviation from that: it filters the query *pool* by
> corpus ID membership so the benchmark's query set isn't dominated by queries
> that can never return a result. It does not do retrieval-relevance ranking
> or optimization, and `validate_compatibility.py`'s overlap measurement above
> is unchanged and still ID-membership-only.


### Smoke test, initial indexing, write-feed

Not run — `systems.local_index.base_url` and `systems.shared_index.base_url`
are both `null` in `config/benchmark.yaml` because no Local/Shared Index
service exists in this repo yet. Each of the three scripts was verified to
exit immediately with an explicit blocker message
(`Blocker: '<system>' has no base_url configured...`) rather than silently
no-op'ing or running against a placeholder. Fill in a `base_url` once a
service exists, then re-run the "Reproduction commands" second block above.

## Data-prep blockers encountered (for the record)

Downloading `abhishekmungoli/amazon-query-product-search` (a ~1.54 GB
archive) failed 4 times in a row with 4 distinct transport-level errors
(`ConnectionResetError`, `ReadTimeout`, another `ConnectionResetError`, and
finally an MD5 checksum mismatch on a resumed partial download) before a 5th
attempt — after clearing the corrupted cached partial file — completed
cleanly. No dataset substitution, filtering-strategy change, or fabricated
numbers were made while this was unresolved; the numbers above are from the
first fully successful download.

Separately, `scripts/lib/queries.py`'s file-discovery originally only scanned
`*.csv` files; the actual ESCI examples file in this dataset is packaged as
`*.parquet`. This was a genuine bug (not a dataset-methodology change) and
was fixed to scan both formats before any dedup/split logic ran.

## Layout

```
config/benchmark.yaml            # single source of truth: seed, dataset handles, paths, sizes, systems.*.base_url
scripts/
  lib/                            # config, kaggle_auth, products, queries, report, http_client, system_info
  inspect_products.py             # task 1 (products)
  inspect_queries.py              # task 1 (queries)
  prepare_products.py             # task 2: dedup + 80/20 split (run before prepare_queries.py)
  prepare_queries.py              # task 3: corpus-relevance filter + locale filter + dedup + fixed sample
  validate_compatibility.py       # task 4a: ID overlap measurement
  smoke_test.py                   # task 4b: needs systems.<name>.base_url
  index_initial_corpus.py         # task 5/6: needs systems.<name>.base_url
  feed_write_workload.py          # task 5: needs systems.<name>.base_url
data/                             # generated parquet/JSON outputs (gitignored except *.json under data/ and data/reports/)
notebooks/explore_and_split.ipynb # earlier exploratory/EDA companion notebook (products only); scripts/ is the canonical, reproducible path
```
