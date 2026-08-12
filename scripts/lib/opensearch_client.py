"""Thin opensearch-py wrapper used by every script that talks to a real
OpenSearch cluster (L1 or S1). Replaces scripts/lib/http_client.py's generic
placeholder REST contract now that both systems under test are concretely
OpenSearch.
"""
from __future__ import annotations

from typing import Any

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk


class SystemNotConfiguredError(RuntimeError):
    pass


def require_write_url(system_config: dict[str, Any], system_name: str) -> str:
    url = system_config.get("write_base_url")
    if not url:
        raise SystemNotConfiguredError(
            f"'{system_name}' has no write_base_url configured in config/benchmark.yaml "
            f"(systems.{system_name}.write_base_url is null). Bring up its OpenSearch "
            f"stack under infra/ first."
        )
    return url


def require_search_url(system_config: dict[str, Any], system_name: str) -> str:
    url = system_config.get("search_base_url")
    if not url:
        raise SystemNotConfiguredError(
            f"'{system_name}' has no search_base_url configured in config/benchmark.yaml "
            f"(systems.{system_name}.search_base_url is null). Bring up its OpenSearch "
            f"stack under infra/ first."
        )
    return url


def client(base_url: str) -> OpenSearch:
    return OpenSearch(hosts=[base_url], use_ssl=False, verify_certs=False, timeout=30)


def index_batch(os_client: OpenSearch, index_name: str, id_field: str, documents: list[dict[str, Any]]) -> int:
    actions = (
        {"_index": index_name, "_id": doc[id_field], "_source": doc}
        for doc in documents
    )
    success, errors = bulk(os_client, actions, raise_on_error=True)
    return success


def search(os_client: OpenSearch, index_name: str, query_text: str, size: int = 10) -> list[dict[str, Any]]:
    resp = os_client.search(
        index=index_name,
        body={"query": {"match": {"title": query_text}}, "size": size},
    )
    return [hit["_source"] for hit in resp["hits"]["hits"]]


def fetch_stats(os_client: OpenSearch, index_name: str) -> dict[str, Any] | None:
    try:
        return os_client.indices.stats(index=index_name)
    except Exception:
        return None
