"""One-time canonical baseline build for the formal experiment.

    python -m scripts.formal.build_baseline --system local_index
    python -m scripts.formal.build_baseline --system shared_index

Brings the system up, creates a fresh index, indexes the full initial
corpus, waits for quiescence, verifies the cluster, then stops the stack and
snapshots its Docker volumes so every formal run can restore this exact
state instead of reindexing from scratch. Run once per architecture before
any formal run. Re-running it overwrites that architecture's snapshot —
don't do this after formal runs have started without documenting why.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid

from benchmark_api import run_state
from scripts.formal import baseline
from scripts.lib.config import PROJECT_ROOT, data_dir, load_config
from scripts.lib.opensearch_client import client, require_write_url

DATA_FILES = ["corpus_initial.parquet", "corpus_writes.parquet", "queries_fixed_5000_corpus_relevant.parquet"]

# Modules invoked below that are also registered benchmark_api/CLI steps —
# write-through their run to run_state under the SAME step_id so a baseline
# (re)build shows up live in the web UI exactly like a step triggered from
# there, instead of being invisible just because it was launched by this
# harness instead of cli.py/the "Run" button.
MODULE_TO_STEP_ID = {
    "scripts.opensearch.create_index": "create_index",
    "scripts.index_initial_corpus": "index_initial_corpus",
    "scripts.opensearch.verify_cluster": "verify_cluster",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True)
    return out.stdout.strip()


def _run_module(module: str, *args: str, system: str | None = None) -> None:
    cmd = [sys.executable, "-m", module, *args]
    print(f"$ {' '.join(cmd)}")

    step_id = MODULE_TO_STEP_ID.get(module)
    if step_id is None:
        subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)
        return

    run_id = uuid.uuid4().hex[:12]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        cmd, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
    )
    run_state.start_run(step_id, system, run_id, process.pid)
    seq = 0
    for raw_line in iter(process.stdout.readline, ""):
        text = raw_line.rstrip("\n")
        print(text)
        run_state.append_log_line(run_id, "stdout", text, seq, _now())
        seq += 1
    process.stdout.close()
    returncode = process.wait()
    run_state.finish_run(step_id, system, run_id, "done" if returncode == 0 else "failed")
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, cmd)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True, choices=["local_index", "shared_index"])
    parser.add_argument("--limit", type=int, default=None, help="Index only the first N rows (dry-run/testing only, not a real baseline)")
    args = parser.parse_args()

    config = load_config()
    system_config = config["systems"][args.system]
    d_dir = data_dir(config)

    baseline.bring_up(args.system)

    _run_module("scripts.opensearch.create_index", "--system", args.system, system=args.system)
    index_args = ["--system", args.system]
    if args.limit is not None:
        index_args += ["--limit", str(args.limit)]
    _run_module("scripts.index_initial_corpus", *index_args, system=args.system)

    baseline.wait_for_quiescence(args.system)
    _run_module("scripts.opensearch.verify_cluster", "--system", args.system, system=args.system)

    write_url = require_write_url(system_config, args.system)
    os_client = client(write_url)
    index_name = system_config["index_name"]
    os_client.indices.refresh(index=index_name)
    doc_count = os_client.count(index=index_name).get("count")

    checksums = {}
    for name in DATA_FILES:
        path = d_dir / name
        if path.exists():
            checksums[name] = baseline._sha256_file(path)

    baseline.bring_down(args.system)
    volume_checksums = baseline.snapshot_volumes(args.system)
    baseline.bring_up(args.system)

    manifest = {
        "system": args.system,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_sha": _git_sha(),
        "doc_count": doc_count,
        "limit": args.limit,
        "data_file_checksums": checksums,
        "volume_checksums": volume_checksums,
    }
    baseline.write_manifest(args.system, manifest)
    print(f"Baseline built for '{args.system}': doc_count={doc_count}")
    print(f"Wrote {baseline.manifest_path(args.system)}")


if __name__ == "__main__":
    main()
