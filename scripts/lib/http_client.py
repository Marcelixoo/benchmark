from __future__ import annotations

from typing import Any

import requests


class SystemNotConfiguredError(RuntimeError):
    pass


def require_base_url(system_config: dict[str, Any], system_name: str) -> str:
    base_url = system_config.get("base_url")
    if not base_url:
        raise SystemNotConfiguredError(
            f"'{system_name}' has no base_url configured in config/benchmark.yaml "
            f"(systems.{system_name}.base_url is null). The Local/Shared Index "
            f"service isn't built/deployed yet — fill this in once it exists."
        )
    return base_url


def index_batch(base_url: str, path: str, documents: list[dict[str, Any]]) -> requests.Response:
    """POSTs a batch of documents to the configured index endpoint.

    NOTE: the request body ({"documents": [...]}) is a placeholder contract.
    Adapt this to match whatever bulk-index API the actual Local/Shared Index
    services expose once they're built.
    """
    return requests.post(f"{base_url.rstrip('/')}{path}", json={"documents": documents}, timeout=30)


def search(base_url: str, path: str, query: str) -> requests.Response:
    """Issues a single search request.

    NOTE: the request body ({"q": query}) and the expected response shape
    ({"results": [...]}) are placeholder contracts — adapt to the real API.
    """
    return requests.post(f"{base_url.rstrip('/')}{path}", json={"q": query}, timeout=10)


def fetch_stats(base_url: str, path: str) -> dict[str, Any] | None:
    try:
        resp = requests.get(f"{base_url.rstrip('/')}{path}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None
