"""Task 1: inspect the product corpus and document rows, schema, nulls, dups, file size."""
from __future__ import annotations

from scripts.lib import products, report
from scripts.lib.config import load_config, reports_dir
from scripts.lib.kaggle_auth import ensure_kaggle_credentials


def main() -> None:
    ensure_kaggle_credentials()
    config = load_config()
    handle = config["datasets"]["products"]["handle"]
    file = config["datasets"]["products"]["file"]

    print(f"Loading {handle}/{file} ...")
    df = products.load_raw(config)
    prof = products.profile(df)
    file_size = products.file_size_bytes(handle, file)
    prof["approx_file_size_bytes"] = file_size
    prof["approx_file_size_human"] = report.human_bytes(file_size) if file_size else None

    out_dir = reports_dir(config)
    report.write_json(out_dir / "product_corpus_report.json", prof)
    report.write_markdown(
        out_dir / "product_corpus_report.md",
        "Product corpus report",
        {
            "Summary": (
                f"- rows: {prof['row_count']:,}\n"
                f"- columns: {prof['column_count']}\n"
                f"- duplicate asin rows: {prof['duplicate_asin_count']:,}\n"
                f"- approx file size: {prof['approx_file_size_human']}"
            ),
            "Dtypes": "\n".join(f"- `{c}`: {t}" for c, t in prof["dtypes"].items()),
            "Null %": "\n".join(f"- `{c}`: {v}%" for c, v in prof["null_pct"].items()) or "(no nulls found)",
        },
    )
    print(f"Wrote {out_dir / 'product_corpus_report.json'} and .md")
    print(prof)


if __name__ == "__main__":
    main()
