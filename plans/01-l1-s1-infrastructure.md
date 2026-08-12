# OpenSearch L1 (Local Index) vs S1 (Shared Index) benchmark infrastructure

## Context

The data-prep scaffold (product corpus split, corpus-relevant query set, compatibility
report) is committed (`3cfe065`). It intentionally left `systems.local_index.base_url` /
`systems.shared_index.base_url` unset because no actual search system existed yet.

This task builds those two systems, using **OpenSearch for both**, so the experiment
isolates the storage/compute architecture variable (co-located node-local durable
storage vs. remote-backed storage with dedicated search compute) instead of comparing
different search engines — per your explicit instruction not to compare unrelated
engines in the primary experiment.

- **L1 (Local Index):** ordinary OpenSearch cluster, remote store disabled, data nodes
  do both indexing and search, durable Lucene data on local Docker volumes.
- **S1 (Shared Index):** OpenSearch with remote-backed storage (segment replication +
  S3-compatible remote store for segments/translog/cluster state), a dedicated
  `node.roles: [search]` node serving a search replica, strict search-replica routing.

Two things discovered during research/setup, reported rather than silently worked around:

1. **Docker Desktop's VM is capped at ~7.75 GB RAM** (`docker info` → `MemTotal:
   8319213568`), even though the host has 64 GB. Your suggested budget (2×4GB for L1
   alone = 8GB) doesn't fit *concurrently* with S1 inside that cap. Since only one
   architecture is benchmarked at a time anyway (running both simultaneously would
   itself confound CPU measurements), this isn't a real blocker — each stack (L1 *or*
   S1) fits in 7.75GB with room for Locust/MinIO. But if you want to run L1 and S1
   **concurrently** later, or want the full symmetric 8GB+8GB, you'll need to raise
   Docker Desktop's memory limit in its settings (Resources → Memory) — trivial given
   the 64GB host, just a manual GUI step I can't do from the CLI. I'll size the compose
   files to the current 7.75GB cap and call this out in the README as a documented
   resource decision, not a silent one.
2. **`repository-s3` is not bundled** in the official `opensearchproject/opensearch`
   Docker image — it must be installed via a custom Dockerfile layer. Needed only for
   S1's two nodes (MinIO is the S3-compatible backend, not real AWS S3, since this is a
   local lab environment — also called out explicitly, matching your "MinIO if stable"
   fallback).
3. **Search replicas (`node.roles: [search]`, `index.number_of_search_replicas`,
   `cluster.routing.search_replica.strict`)** were introduced experimental in 2.17 and
   are documented today (docs.opensearch.org, no experimental-flag caveat) as part of
   the stable "Separate index and search workloads" feature — I could not find a
   changelog line pinpointing the exact GA release. I'm pinning **OpenSearch 3.7.0** as
   you suggested and treating "does the search-replica feature actually activate
   without an experimental flag" as pre-experiment verification checklist item #5
   (below), not an assumption — if it turns out 3.7.0 still needs a feature flag, that's
   a blocker I'll report and fix by adding the flag, not by silently switching designs.

## Layout to add

```
infra/
├── .env                              # OPENSEARCH_VERSION=3.7.0, MinIO creds (local-only, not real secrets), ports
├── opensearch-s1.Dockerfile          # FROM opensearchproject/opensearch:${OPENSEARCH_VERSION} + repository-s3 plugin (S1 nodes only; L1 doesn't need it)
├── l1/
│   └── docker-compose.yml            # os-l1-1, os-l1-2 (data+ingest+cluster_manager, remote store OFF, local volumes), locust
└── s1/
    ├── docker-compose.yml            # minio, minio-init (bucket + mc alias), os-s1-data (data+ingest+cluster_manager, remote store ON), os-s1-search (node.roles:[search]), locust
    └── minio-init.sh                 # one-shot: wait for MinIO, create bucket via `mc`

scripts/
├── opensearch/
│   ├── __init__.py
│   ├── index_spec.py                 # shared mapping/analyzers for the `products` index; only the replication/search-replica settings differ between L1 and S1
│   ├── create_index.py               # `python -m scripts.opensearch.create_index --system local_index|shared_index` — deletes+creates the index with the right per-system settings
│   └── verify_cluster.py             # implements your 10-point pre-experiment checklist against a given system, writes data/reports/verify_<system>.json, exits non-zero + prints failing checks (does not fabricate a pass)
└── lib/
    └── opensearch_client.py          # thin wrapper (bulk index via NDJSON, match-query search, health/cat helpers) using `opensearch-py`

config/benchmark.yaml                 # systems.<name> gets write_base_url / search_base_url / index_name (replaces the old generic base_url/index_path/search_path/stats_path contract, now that the real target is known)
scripts/lib/http_client.py            # replaced by scripts/lib/opensearch_client.py (the generic placeholder contract is dead now that both systems are concretely OpenSearch)
scripts/{smoke_test,index_initial_corpus,feed_write_workload}.py   # updated to call opensearch_client instead of http_client
requirements.txt                      # + opensearch-py
README.md                             # new "Search infrastructure (L1 vs S1)" section: architecture, version pin, resource decisions, bring-up/verify/teardown commands, checklist results
```

## Key design decisions

- **Version pin:** `OPENSEARCH_VERSION=3.7.0` in `infra/.env`, read by both compose files — change once, in one place, never mid-experiment.
- **Shard/replica parity:** both architectures get `number_of_shards: 1`. L1:
  `number_of_replicas: 1` (normal read/write replica on the second data node) → 2 shard
  copies. S1: `number_of_replicas: 0`, `number_of_search_replicas: 1` (on the dedicated
  search node) → also 2 shard copies. Same total shard-copy count, different
  role split — isolates the variable being studied instead of confounding it with shard
  count.
- **Identical mapping/analyzers:** defined once in `scripts/opensearch/index_spec.py`
  and reused for both systems' `create_index.py` calls — no hand-duplicated JSON that
  could drift.
- **Security plugin disabled** (`plugins.security.disabled: true`) on all four
  OpenSearch nodes, symmetrically. This is a lab-simplicity decision (no TLS/auth
  overhead to configure) applied identically to L1 and S1, so it doesn't confound the
  storage/compute comparison — documented explicitly in the README as a deviation from
  a production deployment, not silently done.
- **MinIO, not AWS S3:** matches your explicit fallback ("MinIO via OpenSearch's S3
  repository interface if stable in the environment"). `minio-init.sh` creates the
  bucket via the `mc` client so `docker compose up` is one command, no manual console
  step.
- **client-side write/search separation:** `config/benchmark.yaml` gives each system a
  `write_base_url` and `search_base_url` (identical for L1's homogeneous nodes, pointed
  at the data node vs. the search node for S1) so `index_initial_corpus.py` /
  `feed_write_workload.py` always write through the write endpoint and `smoke_test.py`
  always searches through the search endpoint — matching your topology diagrams
  literally, rather than relying solely on server-side strict routing to paper over a
  client hitting the wrong node.
- **`verify_cluster.py` implements your 10-point checklist as real checks**, not a
  narrative doc: cluster health, shard allocation vs. expected, remote-store
  disabled/enabled per system, search-role node + search replica present (S1),
  `cluster.routing.search_replica.strict` value, mapping/setting diff between L1 and S1
  (excluding the intentionally-different replication settings), document count parity
  after a test index, a query smoke test, and reported container resource limits from
  `docker inspect`. Any failing check is printed and the script exits non-zero — it does
  not print a green summary if a check couldn't be verified.
- **Existing scripts get updated, not duplicated:** `scripts/lib/http_client.py`'s
  generic `{"documents": [...]}` / `{"q": ...}` contract was explicitly a placeholder
  for an unknown future API; now that both systems are concretely OpenSearch, keeping
  it around as unused dead code would violate "no half-finished implementations" — it's
  replaced by `opensearch_client.py`, and `smoke_test.py` / `index_initial_corpus.py` /
  `feed_write_workload.py` are updated in place to call it.

## Verification

1. `docker compose -f infra/l1/docker-compose.yml up -d` → `python -m
   scripts.opensearch.verify_cluster --system local_index` passes all 10 checks (with
   remote-store-related checks correctly reporting "disabled" for L1).
2. `python -m scripts.opensearch.create_index --system local_index` then `python -m
   scripts.smoke_test --system local_index` and `python -m scripts.index_initial_corpus
   --system local_index` run against the real cluster (no more "Blocker: base_url not
   configured").
3. Tear down L1, bring up S1 the same way; `verify_cluster --system shared_index` must
   show remote store enabled, the search node + search replica present, and strict
   routing on, or the script reports exactly which check failed.
4. Confirm `docker inspect`-reported CPU/memory limits match what's declared in each
   compose file, for both stacks.
5. `git status` review before any commit — `infra/.env` contains only local MinIO lab
   credentials (not real secrets), but I'll still confirm nothing sensitive slipped in
   before staging.
