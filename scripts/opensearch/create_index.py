"""python -m scripts.opensearch.create_index --system local_index|shared_index

Deletes (if present) and recreates the `products` index against a running
cluster, using the shared mapping/analyzers from index_spec.py and the
per-system replication settings (normal replica for L1, search replica for
S1) that are the very thing this experiment studies.
"""
from __future__ import annotations

import argparse

from scripts.lib.config import load_config
from scripts.lib.opensearch_client import SystemNotConfiguredError, client, require_write_url
from scripts.opensearch.index_spec import INDEX_NAME, MAPPINGS, SETTINGS_BY_SYSTEM


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["local_index", "shared_index"])
    args = parser.parse_args()

    config = load_config()
    system_config = config["systems"][args.system]

    try:
        write_url = require_write_url(system_config, args.system)
    except SystemNotConfiguredError as e:
        raise SystemExit(f"Blocker: {e}") from None

    index_name = system_config.get("index_name", INDEX_NAME)
    os_client = client(write_url)

    if os_client.indices.exists(index=index_name):
        os_client.indices.delete(index=index_name)
        print(f"Deleted existing index '{index_name}'")

    settings = SETTINGS_BY_SYSTEM[args.system]
    os_client.indices.create(index=index_name, body={"settings": settings, "mappings": MAPPINGS})
    print(f"Created index '{index_name}' on '{args.system}' ({write_url}) with settings={settings}")


if __name__ == "__main__":
    main()
