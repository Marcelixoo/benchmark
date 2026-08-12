# Calibration run: full-corpus indexing + full fixed-query-set baseline (L1 vs S1)

## Context

The L1/S1 OpenSearch infrastructure is built, verified (9/9 checks each), and the
review feedback's three concrete corrections are committed: `index.refresh_interval`
is now explicitly pinned to `1s` (not the implicit default), the README's shard-copy
and cross-dataset-compatibility wording is fixed, and a locked-parameters table +
host/MinIO environment snapshot + a Threats-to-Validity placeholder note are recorded.

Before freezing final workload numbers (QPS / docs-per-second), you asked for one
round of **calibration** — not a benchmark run — to see how indexing the real 1.14M-doc
corpus and running the real 5,000-query fixed set actually behave under the resource
reality here (2 CPU / 2.5 GB per node, not the originally-sketched 4 GB). This plan
covers exactly that calibration pass and nothing else:

1. Index the full initial corpus (1,141,069 docs) into L1, then into S1 (one at a time
   — running both concurrently would itself confound CPU measurements, and doesn't fit
   Docker Desktop's ~7.75 GB VM cap anyway), recording indexing duration, docs/s, final
   store size, segment count, and container-level CPU/memory over the run (plus
   remote-store upload stats for S1).
2. Run the full fixed 5,000-query set once against each now-populated system, recording
   queries executed, ≥1-result count, zero-result %, median/p95 result count, and
   **errors tracked separately from legitimate zero-hit queries** (today `smoke_test.py`
   silently folds a request exception into "0 results," which would misreport a
   connectivity/timeout problem as a relevance-quality one).

**Explicitly out of scope / not to be done as a side effect:** this does not filter,
reorder, or mutate the fixed 5,000-query set — it stays exactly as
`data/queries_fixed_5000_corpus_relevant.parquet` was written by `prepare_queries.py`.
The numbers produced here are calibration input for later QPS/docs/s decisions, not
benchmark results, and will be labeled as such in every output file so they can't be
mistaken for a real run later.

## What already exists and will be reused, not duplicated

- `scripts/index_initial_corpus.py` already indexes the full corpus batch-by-batch,
  times it, calls `fetch_stats()` (index-level doc count/store size/segment count via
  `_stats`), and writes `data/reports/index_run_<system>_<ts>.json`. This is ~90% of
  calibration step 1 already — it just needs container-resource sampling and (for S1)
  remote-store stats added, not a rewrite.
- `scripts/smoke_test.py` already runs N queries from the fixed set through
  `opensearch_client.search()` and computes zero-result-rate/median/p95. It already
  accepts `--n-queries`, so `--n-queries 5000` runs the *entire* fixed set — no new
  script needed for step 2, just the error-vs-zero-hit fix and a `calibration_run: true`
  label so the report can't later be confused with an actual benchmark result.
- `scripts/lib/opensearch_client.py` (`client`, `search`, `fetch_stats`) and
  `scripts/lib/report.write_json` are the only client/report helpers needed.
- `scripts/opensearch/verify_cluster.py`'s `CONTAINERS_BY_SYSTEM` dict is the existing,
  single source of truth for which container names belong to which system — reused by
  the new resource sampler rather than redefined.

## Changes to make

1. **`scripts/lib/docker_stats.py`** (new, small): a `ContainerStatsSampler` that polls
   `docker stats --no-stream --format '{{json .}}' <container...>` on a background
   thread every ~2s while indexing runs, parsing CPU% and mem usage per container, and
   exposes `.summary()` → `{container: {cpu_pct_avg, cpu_pct_max, mem_mb_avg, mem_mb_max}}`
   on stop. Pure stdlib (`subprocess`, `threading`, `json`) — no new dependency.

2. **`scripts/index_initial_corpus.py`**: wrap the existing indexing loop with the new
   sampler (containers from `scripts.opensearch.verify_cluster.CONTAINERS_BY_SYSTEM[args.system]`,
   imported rather than redefined), and add its `.summary()` to the result dict as
   `container_resource_usage`. For `shared_index` only, capture `_nodes/stats` remote-store
   metrics before and after indexing (inspect the real response shape from OpenSearch's
   Nodes Stats API against a live S1 cluster first, and cite the exact field names once
   confirmed, rather than guessing) and record the diff as `remote_store_stats_delta`.
   Add `"calibration_run": true` to the result payload and change the output filename
   prefix to `calibration_index_<system>_<ts>.json` so it can never be mistaken for a
   later real benchmark ingest run.

3. **`scripts/smoke_test.py`**: change the per-query `try/except` so a request exception
   is recorded as a distinct `error` (with the exception's message) rather than folded
   into `result_counts` as 0; report `errors_count` and `error_rate_pct` alongside the
   existing zero-result-rate metric. Add a `--calibration` flag that also writes to
   `calibration_queries_<system>.json` with `"calibration_run": true` in the summary —
   default smoke-test behavior/filename (the existing 1,000-query dev smoke test)
   stays unchanged when the flag is omitted.

No changes to `feed_write_workload.py`, `index_spec.py`, `create_index.py`, or
`verify_cluster.py` — the write-feed (online 20%) calibration and workload-parameter
freezing are explicitly the *next* step after this one, not part of this pass.

## Execution (after the above lands)

```bash
docker compose --env-file infra/.env -f infra/l1/docker-compose.yml up -d
python -m scripts.opensearch.verify_cluster --system local_index
python -m scripts.opensearch.create_index --system local_index
python -m scripts.index_initial_corpus --system local_index      # ~1.14M docs — long-running, run via Monitor/background
python -m scripts.smoke_test --system local_index --n-queries 5000 --calibration
docker compose --env-file infra/.env -f infra/l1/docker-compose.yml down

docker compose --env-file infra/.env -f infra/s1/docker-compose.yml up -d
python -m scripts.opensearch.verify_cluster --system shared_index
python -m scripts.opensearch.create_index --system shared_index
python -m scripts.index_initial_corpus --system shared_index
python -m scripts.smoke_test --system shared_index --n-queries 5000 --calibration
docker compose --env-file infra/.env -f infra/s1/docker-compose.yml down
```

Given 1.14M documents at `index_batch_size: 500`, indexing duration is currently
unknown — that's precisely what calibration measures, so no time budget is assumed in
advance. Each stack's indexing step will run via a background task, and I'll report
back with the resulting `calibration_index_<system>_*.json` /
`calibration_queries_<system>.json` numbers rather than blocking the conversation on
it, per the tool guidance for long-running operations.

## Verification

- `docker_stats.py`'s sampler tested standalone against a running container before
  wiring it into `index_initial_corpus.py` (confirm parsed CPU%/mem fields match
  `docker stats`'s actual output format on this Docker version).
- After the code changes, do a **small-scale dry run first**: temporarily point
  `index_initial_corpus.py` at a truncated corpus (e.g. `df.head(2000)`) against L1 to
  confirm the sampler thread starts/stops cleanly and the result JSON has the new
  fields, before committing to the full 1.14M-document / 5,000-query runs.
- Full calibration run output reviewed against the "not locked yet" list from the
  feedback (indexing throughput, zero-result %, resource ceilings) before any workload
  numbers are frozen — that freezing step is explicitly deferred to a follow-up
  conversation, not part of this plan.
