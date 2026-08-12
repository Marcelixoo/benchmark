from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def write_markdown(path: Path, title: str, sections: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", ""]
    for heading, body in sections.items():
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(body)
        lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def df_to_markdown_table(df, max_rows: int | None = None) -> str:
    view = df if max_rows is None else df.head(max_rows)
    try:
        return view.to_markdown()
    except ImportError:
        return view.to_string()
