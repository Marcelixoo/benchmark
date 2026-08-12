from __future__ import annotations

import os

from dotenv import load_dotenv

from scripts.lib.config import PROJECT_ROOT


def ensure_kaggle_credentials() -> None:
    """Load .env and warn if kagglehub's expected credential vars are missing.

    kagglehub authenticates via KAGGLE_USERNAME + KAGGLE_KEY (or
    ~/.kaggle/kaggle.json) — not a single KAGGLE_API_TOKEN.
    """
    load_dotenv(PROJECT_ROOT / ".env")
    if not (os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY")):
        print(
            "Warning: KAGGLE_USERNAME / KAGGLE_KEY not set. "
            "kagglehub will prompt to authenticate, or fail if running non-interactively. "
            "Set both in .env (not KAGGLE_API_TOKEN) or create ~/.kaggle/kaggle.json."
        )
