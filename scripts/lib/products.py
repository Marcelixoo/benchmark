from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import kagglehub
import numpy as np
import pandas as pd
from kagglehub import KaggleDatasetAdapter

REQUIRED_COLUMNS = [
    "asin",
    "title",
    "category_id",
    "price",
    "stars",
    "reviews",
    "isBestSeller",
]


def local_dataset_dir(handle: str) -> Path:
    """Downloads (or reuses the cache for) the full dataset and returns its local directory."""
    return Path(kagglehub.dataset_download(handle))


def file_size_bytes(handle: str, filename: str) -> int | None:
    root = local_dataset_dir(handle)
    for p in root.rglob(filename):
        return p.stat().st_size
    return None


def load_raw(config: dict[str, Any]) -> pd.DataFrame:
    handle = config["datasets"]["products"]["handle"]
    file = config["datasets"]["products"]["file"]
    return kagglehub.load_dataset(KaggleDatasetAdapter.PANDAS, handle, file)


def load_categories(config: dict[str, Any]) -> pd.DataFrame:
    handle = config["datasets"]["products"]["handle"]
    file = config["datasets"]["products"]["categories_file"]
    return kagglehub.load_dataset(KaggleDatasetAdapter.PANDAS, handle, file)


def profile(df: pd.DataFrame) -> dict[str, Any]:
    null_pct = (df.isna().mean() * 100).round(2)
    return {
        "row_count": len(df),
        "column_count": df.shape[1],
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "null_pct": {c: v for c, v in null_pct.items() if v > 0},
        "duplicate_asin_count": int(df.duplicated(subset="asin").sum()) if "asin" in df.columns else None,
    }


def clean_and_select(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """De-duplicates by asin (keep first) and selects the required columns only."""
    before = len(df)
    deduped = df.drop_duplicates(subset="asin", keep="first").reset_index(drop=True)
    dup_dropped = before - len(deduped)
    selected = deduped[REQUIRED_COLUMNS].copy()
    return selected, dup_dropped


def split(df: pd.DataFrame, seed: int, offline_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deterministic random 80/20 split. Returns (initial_corpus, write_corpus)."""
    rng = np.random.default_rng(seed)
    shuffled_idx = rng.permutation(len(df))
    split_point = int(len(df) * offline_fraction)
    initial = df.iloc[shuffled_idx[:split_point]].reset_index(drop=True)
    writes = df.iloc[shuffled_idx[split_point:]].reset_index(drop=True)
    assert set(initial["asin"]).isdisjoint(set(writes["asin"])), "corpus split overlaps"
    assert len(initial) + len(writes) == len(df)
    return initial, writes
