"""Shared OpenSearch index definition for the `products` index.

Both L1 and S1 must use byte-identical mappings/analyzers so the comparison
isolates the storage/compute architecture, not indexing behavior. Only the
replication/search-replica settings differ per system — those are supplied
separately by create_index.py, not duplicated here.
"""
from __future__ import annotations

from typing import Any

INDEX_NAME = "products"

MAPPINGS: dict[str, Any] = {
    "properties": {
        "asin": {"type": "keyword"},
        "title": {"type": "text", "analyzer": "standard"},
        "category_id": {"type": "keyword"},
        "price": {"type": "float"},
        "stars": {"type": "float"},
        "reviews": {"type": "integer"},
        "isBestSeller": {"type": "boolean"},
    }
}

# Number of shards is fixed for both systems. Replica/search-replica counts are
# chosen so both architectures end up with the same total shard-copy count
# (2), differing only in whether the second copy is a normal replica (L1) or
# a dedicated search replica (S1) — see README "Search infrastructure".
SHARD_COUNT = 1


def index_settings(*, number_of_replicas: int, number_of_search_replicas: int = 0) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "index": {
            "number_of_shards": SHARD_COUNT,
            "number_of_replicas": number_of_replicas,
            # Pinned explicitly (not left to the OpenSearch default) so it's a
            # documented, self-contained experiment parameter rather than something
            # that silently changes if a future OpenSearch version's default does.
            "refresh_interval": "1s",
        }
    }
    if number_of_search_replicas:
        settings["index"]["number_of_search_replicas"] = number_of_search_replicas
    return settings


# Name must match infra/s1/docker-compose.yml's node.attr.remote_store.*.repository
# value. OpenSearch 3.7.0 makes index.remote_store.* "private" (derived) settings —
# they can't be set explicitly on create_index (confirmed by a 400 validation_exception
# when we tried). Remote store is instead activated implicitly once every node carries
# matching node.attr.remote_store.* attributes (set in infra/s1/docker-compose.yml);
# there is no cluster-wide "enable remote store" setting in 3.7.0. Each index only needs
# replication.type: SEGMENT set explicitly (required alongside remote store).
REMOTE_STORE_REPOSITORY = "s1-repo"

LOCAL_INDEX_SETTINGS = index_settings(number_of_replicas=1)

SHARED_INDEX_SETTINGS = index_settings(number_of_replicas=0, number_of_search_replicas=1)
SHARED_INDEX_SETTINGS["index"]["replication.type"] = "SEGMENT"

SETTINGS_BY_SYSTEM = {
    "local_index": LOCAL_INDEX_SETTINGS,
    "shared_index": SHARED_INDEX_SETTINGS,
}
