from __future__ import annotations

import platform
import resource
import sys
from typing import Any

import numpy
import pandas
import pyarrow


def snapshot() -> dict[str, Any]:
    """Best-effort system/version info and this process's peak memory (RSS)."""
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, KB on Linux.
    peak_rss_mb = peak_rss / (1024 * 1024) if sys.platform == "darwin" else peak_rss / 1024
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "hostname": platform.node(),
        "pandas_version": pandas.__version__,
        "numpy_version": numpy.__version__,
        "pyarrow_version": pyarrow.__version__,
        "process_peak_rss_mb": round(peak_rss_mb, 1),
    }
