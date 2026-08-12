from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import kagglehub
import numpy as np
import pandas as pd

QUERY_TEXT_CANDIDATES = ["query", "query_text", "search_term", "keyword"]
QUERY_ID_CANDIDATES = ["query_id", "id"]
LOCALE_CANDIDATES = ["product_locale", "locale", "market"]
PRODUCT_ID_CANDIDATES = ["product_id", "asin"]


def local_dataset_dir(handle: str) -> Path:
    return Path(kagglehub.dataset_download(handle))


def discover_candidate_files(handle: str) -> list[Path]:
    root = local_dataset_dir(handle)
    return sorted(root.rglob("*.csv")) + sorted(root.rglob("*.parquet"))


def _first_present(columns: list[str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c in columns:
            return c
    return None


def _read_head(path: Path, n: int = 5) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path).head(n)
    return pd.read_csv(path, nrows=n)


def discover_examples_file(handle: str) -> dict[str, Any]:
    """Scans every CSV/parquet file in the dataset for one that looks like the
    query-judgment file (has both a query-text column and a query-id column).
    Returns the file path plus the resolved column names, since the exact
    packaged layout for this dataset isn't hardcoded."""
    best: dict[str, Any] | None = None
    for path in discover_candidate_files(handle):
        try:
            head = _read_head(path)
        except Exception:
            continue
        columns = head.columns.tolist()
        query_col = _first_present(columns, QUERY_TEXT_CANDIDATES)
        query_id_col = _first_present(columns, QUERY_ID_CANDIDATES)
        if query_col and query_id_col:
            best = {
                "path": path,
                "columns": columns,
                "query_col": query_col,
                "query_id_col": query_id_col,
                "locale_col": _first_present(columns, LOCALE_CANDIDATES),
                "product_id_col": _first_present(columns, PRODUCT_ID_CANDIDATES),
            }
            break
    if best is None:
        raise RuntimeError(
            f"Could not find a query-judgment CSV/parquet file in dataset '{handle}' "
            f"(looked for columns among {QUERY_TEXT_CANDIDATES} and {QUERY_ID_CANDIDATES})."
        )
    return best


def load_raw(file_info: dict[str, Any]) -> pd.DataFrame:
    usecols = [
        c
        for c in [
            file_info["query_col"],
            file_info["query_id_col"],
            file_info["locale_col"],
            file_info["product_id_col"],
        ]
        if c
    ]
    path = file_info["path"]
    if path.suffix == ".parquet":
        return pd.read_parquet(path, columns=usecols)
    return pd.read_csv(path, usecols=usecols)


def normalize_text(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)


def profile(df: pd.DataFrame, file_info: dict[str, Any]) -> dict[str, Any]:
    query_col = file_info["query_col"]
    query_id_col = file_info["query_id_col"]
    locale_col = file_info["locale_col"]

    locale_breakdown = df[locale_col].value_counts().to_dict() if locale_col else None
    null_pct = (df.isna().mean() * 100).round(2)

    return {
        "source_file": str(file_info["path"]),
        "raw_row_count": len(df),
        "columns": file_info["columns"],
        "resolved_query_col": query_col,
        "resolved_query_id_col": query_id_col,
        "resolved_locale_col": locale_col,
        "null_pct": {c: v for c, v in null_pct.items() if v > 0},
        "unique_query_id_count": int(df[query_id_col].nunique()),
        "unique_normalized_text_count": int(normalize_text(df[query_col]).nunique()),
        "locale_breakdown": locale_breakdown,
    }


def restrict_to_corpus_relevant(df: pd.DataFrame, file_info: dict[str, Any], corpus_asins: set[str]) -> pd.DataFrame:
    """Keeps only judgment rows whose judged product_id is present in the given
    product corpus. Because a query can have many judged products, a query
    survives this filter as soon as at least one of its judgments matches —
    which is exactly what downstream dedup-by-query_id needs. This is a
    row-level filter on the raw judgments file, applied before locale
    filtering/dedup, not an assumption of ID alignment: it only asks whether
    *this specific query* has *any* judged product that happens to exist in
    *this specific corpus snapshot*."""
    product_id_col = file_info["product_id_col"]
    if not product_id_col:
        raise RuntimeError(
            f"Cannot restrict to corpus-relevant queries: no product-id-like column found "
            f"(looked for {PRODUCT_ID_CANDIDATES})."
        )
    mask = df[product_id_col].astype(str).isin(corpus_asins)
    return df[mask].reset_index(drop=True)


def clean_and_dedupe(df: pd.DataFrame, file_info: dict[str, Any], preferred_locale: str | None) -> pd.DataFrame:
    """Filters to preferred_locale if a locale column exists, then de-duplicates
    to one row per unique query (by query_id, falling back to normalized text).
    Repeated judgment rows for the same query_id are collapsed, never treated
    as a frequency signal."""
    query_col = file_info["query_col"]
    query_id_col = file_info["query_id_col"]
    locale_col = file_info["locale_col"]

    work = df.copy()
    if locale_col and preferred_locale:
        mask = work[locale_col].astype(str).str.lower() == preferred_locale.lower()
        if mask.any():
            work = work[mask]

    work = work.dropna(subset=[query_col])
    work["_normalized_query"] = normalize_text(work[query_col])

    dedupe_key = query_id_col if query_id_col in work.columns else "_normalized_query"
    deduped = work.drop_duplicates(subset=dedupe_key, keep="first").reset_index(drop=True)

    cols = [c for c in [query_id_col, query_col, locale_col] if c]
    return deduped[cols].rename(columns={query_col: "query_text", query_id_col: "query_id"})


def sample_fixed(df: pd.DataFrame, seed: int, n: int) -> pd.DataFrame:
    """Deterministic fixed-size sample drawn from the full deduplicated query set."""
    rng = np.random.default_rng(seed)
    n = min(n, len(df))
    idx = rng.permutation(len(df))[:n]
    return df.iloc[idx].reset_index(drop=True)
