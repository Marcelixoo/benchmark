from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "benchmark.yaml"


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def data_dir(config: dict[str, Any]) -> Path:
    d = PROJECT_ROOT / config["paths"]["data_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir(config: dict[str, Any]) -> Path:
    d = PROJECT_ROOT / config["paths"]["reports_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d
