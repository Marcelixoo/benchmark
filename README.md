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

Then, once the relevant OpenSearch stack is up (see "Search infrastructure"
below):

```bash
python -m scripts.opensearch.create_index --system local_index
python -m scripts.smoke_test --system local_index
python -m scripts.index_initial_corpus --system local_index
python -m scripts.feed_write_workload --system local_index
```

(repeat with `--system shared_index`). Before infra existed, these scripts
exited immediately with a `Blocker: ...` message instead of running against a
stand-in — that behavior is preserved for any system left unconfigured in
`config/benchmark.yaml`.

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

Overall cross-dataset ASIN overlap is reported descriptively. During
query-corpus construction, this same membership relation is used to retain
queries for which at least one judged product is present in the initial
product corpus. No relevance scores or retrieval results are used for query
selection:

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


### Search infrastructure verification

Both stacks (see "Search infrastructure" below) were brought up and passed
all 9 automated `verify_cluster.py` checks — see
`data/reports/verify_local_index.json` / `verify_shared_index.json`. This
confirms the infrastructure is ready for the benchmark; it does not itself
constitute a benchmark run. `create_index.py`, `index_initial_corpus.py`,
`feed_write_workload.py`, and `smoke_test.py` are wired up against real
`write_base_url`/`search_base_url` endpoints in `config/benchmark.yaml` but
have not yet been run against the full 1.14M-row corpus — that's the next
phase, not part of this data-prep/infra pass.

## Search infrastructure (L1 vs S1)

Both architectures under test are **OpenSearch 3.7.0** (pinned once in
`infra/.env`, never bumped mid-experiment), so the comparison isolates the
storage/compute architecture variable rather than comparing unrelated search
engines:

- **L1 — Local Index** (`infra/l1/docker-compose.yml`): an ordinary 2-node
  cluster. Both nodes do indexing and search, durable Lucene/translog data
  lives on node-local Docker volumes, remote-backed storage is explicitly
  disabled (`cluster.remote_store.state.enabled: "false"`). Shards:
  `number_of_shards: 1`, `number_of_replicas: 1` → 2 shard copies, both
  regular read/write replicas.
- **S1 — Shared Index** (`infra/s1/docker-compose.yml`): a remote-store
  cluster — segment replication + S3-compatible remote store (MinIO, per the
  explicit "MinIO if stable" fallback — not real AWS S3) for segments,
  translog, and cluster state. `os-s1-data` is the dedicated indexing/data
  node (`cluster_manager,data,ingest`); `os-s1-search` is a dedicated
  `node.roles: [search]` node serving 1 OpenSearch search replica, with
  `cluster.routing.search_replica.strict: "true"` so search traffic can only
  land on the search replica, never the primary. Shards:
  `number_of_shards: 1`, `number_of_replicas: 0`,
  `number_of_search_replicas: 1`.

Both configurations use one primary shard and one additional searchable
shard copy. The additional copy differs intentionally in replication and
ownership semantics: L1 uses a conventional replica backed by local durable
storage, whereas S1 uses a dedicated search replica backed by remotely
published segments. These differences constitute the architectural treatment
under evaluation.

Both stacks share identical mappings/analyzers (`scripts/opensearch/index_spec.py`,
one definition reused by both), identical shard count, an identical
explicitly-pinned refresh interval (`index.refresh_interval: "1s"` set in
`index_spec.py` for both systems, not left to the OpenSearch default), and
identical per-node resource limits (`OS_CONTAINER_CPU_LIMIT`,
`OS_CONTAINER_MEM_LIMIT`, `OS_JAVA_HEAP` in `infra/.env`). Bulk/index writes
from `index_initial_corpus.py` / `feed_write_workload.py` never pass
`refresh=true` — they rely on the OpenSearch API default of `refresh=false`
and the scheduled 1s refresh above, so a fixed refresh interval isn't
defeated by forced per-write refreshes. The security plugin
is disabled on all four nodes, symmetrically — a lab-simplicity decision
applied identically to both systems, not a confound.

**Resource sizing:** Docker Desktop's VM on this machine caps out at ~7.75GB
regardless of host RAM (`docker info` → `MemTotal: 8319213568`), so L1 and S1
are sized to each fit that cap independently (2 CPU / 2.5GB per node); they
are not run concurrently — only one architecture is benchmarked at a time
anyway, since running both simultaneously would itself confound CPU
measurements. Raising Docker Desktop's memory limit (Resources → Memory) is a
manual step if a larger symmetric budget is ever needed.

**Locust is not included** in either compose file, despite appearing in the
original topology sketch — the write/search load generation in this repo is
driven by `scripts/feed_write_workload.py` and `scripts/smoke_test.py`
directly against `write_base_url`/`search_base_url`, so a separate load-gen
service would have been unused scaffolding.

### Bring-up / verify / teardown

```bash
docker compose --env-file infra/.env -f infra/l1/docker-compose.yml up -d
python -m scripts.opensearch.verify_cluster --system local_index
docker compose --env-file infra/.env -f infra/l1/docker-compose.yml down

docker compose --env-file infra/.env -f infra/s1/docker-compose.yml up -d
python -m scripts.opensearch.verify_cluster --system shared_index
docker compose --env-file infra/.env -f infra/s1/docker-compose.yml down
```

`verify_cluster.py` implements the pre-experiment checklist as 9 automated
checks against a live cluster (cluster health, unassigned shards, remote
store enabled/disabled as expected, search-node + search-replica present
(S1), strict search-replica routing (S1), mapping parity, document count
parity through a throwaway test index, an end-to-end query, and
`docker inspect`-confirmed resource limits) — it writes
`data/reports/verify_<system>.json` and exits non-zero on any failing check
rather than printing a false green summary. Current result: **all 9 checks
pass for both `local_index` and `shared_index`**, re-verified together under
the same script version (the earlier L1 run predated two check-implementation
fixes below, so it was re-run to confirm those fixes didn't change the
result).

### Corrections discovered during setup (verification, not assumption)

The plan flagged the search-replica feature's stability as something to
verify rather than assume; two more mechanism-level corrections turned up
during actual bring-up, in the same spirit — none of them changed the
intended architecture, only how it's expressed to OpenSearch 3.7.0:

- **No cluster-wide remote-store toggle for segments/translog.**
  `cluster.remote_store.segment.enabled`, `cluster.remote_store.translog.enabled`,
  and even a guessed `cluster.remote_store.enabled` all fail bootstrap with
  `SettingsException: unknown setting`. The only real cluster-level remote
  store setting in 3.7.0 is `cluster.remote_store.state.enabled` (cluster
  state only). Segment/translog remote store is instead activated implicitly:
  once every node carries matching `node.attr.remote_store.*` attributes, the
  cluster becomes a remote-store cluster and `index.remote_store.enabled` /
  `.segment.repository` / `.translog.repository` become **private** (derived,
  read-only) index settings — attempting to set them explicitly on
  `indices.create` fails with a 400 `validation_exception`
  ("private index setting ... can not be set explicitly"). S1's index only
  needs to set `index.replication.type: SEGMENT` explicitly; remote store
  itself comes from the node attributes.
- **Single-node bootstrap quorum.** `cluster.initial_cluster_manager_nodes`
  must list only cluster-manager-*eligible* nodes. Listing `os-s1-search` (a
  `node.roles: [search]`-only node, not eligible) alongside `os-s1-data`
  deadlocked bootstrap: `os-s1-data` alone could never reach quorum, and
  `os-s1-search` never started because it `depends_on: os-s1-data:
  condition: service_healthy`. Fixed by listing only `os-s1-data`.

### Other environment-specific notes

- **Corporate TLS interception** on this network makes `opensearch-plugin
  install repository-s3` fail cert validation inside a fresh container (the
  host's `curl` trusts an injected root CA that a container's JVM truststore
  doesn't). Fixed, per an explicit choice among reported options, by
  downloading `repository-s3-3.7.0.zip` on the host and installing it from
  `file:///tmp/repository-s3.zip` in `infra/s1/opensearch-s1.Dockerfile`
  instead of fetching it during the build.
- **MinIO's host port is remapped to 9010** (`9010:9000` in
  `infra/s1/docker-compose.yml`) because this network's Zscaler tunnel
  already listens on host port 9000. The container-internal port, and every
  in-cluster reference to `http://minio:9000`, is unaffected.

### Locked parameters (this environment, seed=42)

| Parameter | Value |
|---|---|
| Search engine | OpenSearch 3.7.0 (both L1 and S1) |
| Orchestration | Docker Compose |
| Product source rows | 1,426,337 |
| Initial (offline) corpus | 1,141,069 rows |
| Write (online) corpus | 285,268 rows |
| Corpus split | 80/20, `numpy.random.default_rng(42)` |
| Query source judgment rows | 2,621,288 |
| Unique source queries | 130,652 |
| Corpus-relevant eligible query pool | 60,141 |
| Fixed benchmark query set | 5,000, seed=42 |
| L1 nodes | 2 (both data+ingest+cluster_manager) |
| S1 nodes | 1 data + 1 search, + MinIO |
| OpenSearch per-node resources | 2 CPU / 2.5 GB (`OS_CONTAINER_CPU_LIMIT`/`OS_CONTAINER_MEM_LIMIT`) |
| OpenSearch JVM heap | `OS_JAVA_HEAP=1g` (`-Xms1g -Xmx1g`, `infra/.env`) |
| Primary shards | 1 (both systems) |
| L1 replicas | 1 conventional replica |
| S1 replicas | 0 conventional, 1 search replica |
| S1 search routing | `cluster.routing.search_replica.strict: "true"` |
| Refresh interval | explicitly `1s` (`scripts/opensearch/index_spec.py`), both systems |
| Security plugin | disabled, both systems |

### Host environment (this run)

- Mac17,9 (Apple M5 Pro), 64 GB physical RAM, arm64, macOS 26.5.2
- Docker Desktop 4.86.0, engine 29.7.2
- Docker Desktop VM cap: 18 CPUs / 7.75 GB RAM (`docker info` →
  `MemTotal: 8319213568`) — this is what actually bounds L1/S1 sizing, not
  host RAM; see "Resource sizing" above
- `minio/minio:latest` resolved to `RELEASE.2025-09-07T16-13-09Z`
  (image digest `sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`);
  `minio/mc:latest` resolved to digest
  `sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727`.
  Both tags are floating (`:latest`) in `infra/s1/docker-compose.yml` as of
  this writing — not yet pinned to an explicit version tag, and MinIO
  currently has no `deploy.resources.limits` block (unlike the OpenSearch
  services). Recorded here as the exact versions/digests this run used;
  pinning both explicitly is still open before calibration.

### Threats to validity (placeholder)

MinIO (S1's remote-store backend) runs in the same Docker Desktop VM as the
OpenSearch containers it serves, not on physically independent
compute/storage infrastructure. The correct framing for this setup is
**logical compute/storage disaggregation using OpenSearch remote-backed
storage**, not physically independent compute and object-storage
infrastructure — this affects the realism of network-latency and I/O
isolation claims for S1. This is an accepted lab constraint for this thesis,
not something the experiment is being rebuilt around (no move to real AWS
S3/EC2 is planned); it belongs in a full "Threats to Validity" section
alongside the other lab-simplicity decisions already noted above (disabled
security plugin, non-concurrent L1/S1 runs, Docker Desktop's RAM cap).

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
config/benchmark.yaml            # single source of truth: seed, dataset handles, paths, sizes, systems.*.write_base_url/search_base_url
infra/
  .env                            # OPENSEARCH_VERSION pin, MinIO creds (lab-only), container resource limits — shared by both compose files
  l1/docker-compose.yml           # L1: 2-node local OpenSearch cluster, remote store disabled
  s1/
    docker-compose.yml            # S1: remote-store OpenSearch cluster (data node + dedicated search node) + MinIO
    opensearch-s1.Dockerfile      # adds repository-s3 plugin (from a host-fetched zip, see "Search infrastructure")
    os-entrypoint.sh              # seeds MinIO S3 credentials into the OpenSearch keystore before handoff
    minio-init.sh                 # one-shot: creates the MinIO bucket via `mc`
scripts/
  lib/                            # config, kaggle_auth, products, queries, report, opensearch_client, system_info
  opensearch/
    index_spec.py                 # shared `products` mapping + per-system index settings (L1 vs S1)
    create_index.py               # `python -m scripts.opensearch.create_index --system local_index|shared_index`
    verify_cluster.py             # `python -m scripts.opensearch.verify_cluster --system ...` — 9-point pre-experiment checklist
  inspect_products.py             # task 1 (products)
  inspect_queries.py              # task 1 (queries)
  prepare_products.py             # task 2: dedup + 80/20 split (run before prepare_queries.py)
  prepare_queries.py              # task 3: corpus-relevance filter + locale filter + dedup + fixed sample
  validate_compatibility.py       # task 4a: ID overlap measurement
  smoke_test.py                   # task 4b: needs systems.<name>.search_base_url
  index_initial_corpus.py         # task 5/6: needs systems.<name>.write_base_url
  feed_write_workload.py          # task 5: needs systems.<name>.write_base_url
data/                             # generated parquet/JSON outputs (gitignored except *.json under data/ and data/reports/)
notebooks/explore_and_split.ipynb # earlier exploratory/EDA companion notebook (products only); scripts/ is the canonical, reproducible path
```
