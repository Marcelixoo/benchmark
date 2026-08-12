"""Task 1: inspect the query corpus and document rows, schema, nulls, dups, locale mix, file size."""
from __future__ import annotations

from scripts.lib import queries, report
from scripts.lib.config import load_config, reports_dir
from scripts.lib.kaggle_auth import ensure_kaggle_credentials


def main() -> None:
    ensure_kaggle_credentials()
    config = load_config()
    handle = config["datasets"]["queries"]["handle"]

    print(f"Discovering query-judgment file in {handle} ...")
    file_info = queries.discover_examples_file(handle)
    print(f"Found: {file_info['path']}")

    df = queries.load_raw(file_info)
    prof = queries.profile(df, file_info)
    prof["approx_file_size_bytes"] = file_info["path"].stat().st_size
    prof["approx_file_size_human"] = report.human_bytes(prof["approx_file_size_bytes"])

    out_dir = reports_dir(config)
    report.write_json(out_dir / "query_corpus_report.json", prof)
    report.write_markdown(
        out_dir / "query_corpus_report.md",
        "Query corpus report",
        {
            "Summary": (
                f"- source file: `{prof['source_file']}`\n"
                f"- raw rows (query-product judgments): {prof['raw_row_count']:,}\n"
                f"- unique query_id: {prof['unique_query_id_count']:,}\n"
                f"- unique normalized query text: {prof['unique_normalized_text_count']:,}\n"
                f"- approx file size: {prof['approx_file_size_human']}\n\n"
                "Note: raw row count counts one row per query-product judgment "
                "(up to ~40 per query), not per unique query — do not read it as query frequency."
            ),
            "Locale breakdown": (
                "\n".join(f"- `{k}`: {v:,}" for k, v in prof["locale_breakdown"].items())
                if prof["locale_breakdown"]
                else "(no locale column found)"
            ),
            "Null %": "\n".join(f"- `{c}`: {v}%" for c, v in prof["null_pct"].items()) or "(no nulls found)",
        },
    )
    print(f"Wrote {out_dir / 'query_corpus_report.json'} and .md")
    print(prof)


if __name__ == "__main__":
    main()
