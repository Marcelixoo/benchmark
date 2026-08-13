"""Docker/infra constants for the formal-experiment harness. Mirrors the
exact compose invocation documented in README.md ("Bring-up / verify /
teardown") and the container/volume names declared in infra/l1 and infra/s1's
docker-compose.yml files — nothing here is guessed, both were read directly.
"""
from __future__ import annotations

from scripts.lib.config import PROJECT_ROOT

ENV_FILE = PROJECT_ROOT / "infra" / ".env"

COMPOSE_FILE_BY_SYSTEM = {
    "local_index": PROJECT_ROOT / "infra" / "l1" / "docker-compose.yml",
    "shared_index": PROJECT_ROOT / "infra" / "s1" / "docker-compose.yml",
}

OTHER_SYSTEM = {"local_index": "shared_index", "shared_index": "local_index"}

# Reuses the exact same table verify_cluster.py already uses as its source of
# truth for container names per system.
from scripts.opensearch.verify_cluster import CONTAINERS_BY_SYSTEM  # noqa: E402

# Named Docker volumes actually declared by each compose file (project name
# comes from each file's top-level `name:` key — "benchmark-l1"/"benchmark-s1"
# — Compose prefixes every unexternalized volume with it). Confirmed against a
# live `docker volume ls` on this machine.
VOLUMES_BY_SYSTEM = {
    "local_index": ["benchmark-l1_os-l1-data1", "benchmark-l1_os-l1-data2"],
    "shared_index": [
        "benchmark-s1_os-s1-data-vol",
        "benchmark-s1_os-s1-search-vol",
        "benchmark-s1_minio-data",
    ],
}
