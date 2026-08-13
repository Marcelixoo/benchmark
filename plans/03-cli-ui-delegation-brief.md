# Delegation brief: CLI + Pencil-designed UI for the benchmark scaffold

Hand this to an LLM session that has a working Pencil MCP connection (desktop
app open, `pencil-new.pen` loadable). It picks up where this session left off
— I could not reach the Pencil desktop app from here, so the design has not
yet been read.

## Context: what this repo is today

Reproducible data-prep + benchmark scaffold for a thesis comparing two
OpenSearch retrieval architectures ("Local Index" L1 vs "Shared Index" S1).
Everything currently runs as standalone scripts invoked with
`python -m scripts.<name>`, each reading `config/benchmark.yaml` itself and
writing files under `data/` / `data/reports/`. There is no CLI framework, no
server, no UI — just modules with `if __name__ == "__main__"` blocks. See
`README.md` for full narrative/results.

Relevant existing structure:

```
config/benchmark.yaml         # seed, dataset handles, paths, sizes, systems.*.write_base_url/search_base_url
scripts/lib/
  config.py                   # load_config(), data_dir(), reports_dir()
  report.py                   # write_json(), write_markdown(), df_to_markdown_table()
  products.py, queries.py     # dataset prep helpers
  opensearch_client.py        # thin OpenSearch HTTP client
  load_generator.py           # write/search load generation primitives
  docker_stats.py, system_info.py
scripts/
  inspect_products.py / inspect_queries.py   # task 1
  prepare_products.py / prepare_queries.py   # tasks 2-3
  validate_compatibility.py                  # task 4a
  smoke_test.py                              # task 4b (needs search_base_url)
  index_initial_corpus.py                    # task 5/6 (needs write_base_url)
  feed_write_workload.py                     # task 5 (needs write_base_url)
  load_check_query.py / load_check_w2.py     # calibration runs
  opensearch/create_index.py, verify_cluster.py, index_spec.py
infra/l1/, infra/s1/           # docker-compose stacks for the two systems under test
pencil-new.pen                 # UI design — NOT YET READ
```

Every script currently: (1) calls `load_config()`, (2) does its work inline,
(3) prints progress/results to stdout, (4) writes JSON/parquet/markdown
reports as a side effect. Some scripts exit early with a `Blocker: ...`
message when a system's `write_base_url`/`search_base_url` isn't configured —
that guard behavior must be preserved.

## The ask

Turn this into a **standalone CLI**, driven by a **UI built to the design in
`pencil-new.pen`**. The user has already chosen the target shape:

> **CLI + local web UI.** A CLI (Typer/Click) exposing each pipeline step as
> a subcommand, plus a local web server (FastAPI) that the Pencil-designed UI
> talks to over HTTP. The UI drives the same underlying operations the CLI
> does — not a separate reimplementation.

## Step 1 — read the design

Use the Pencil MCP tools (`get_app_state` with `include_canvas_design: true`,
then `browser`/`get_screenshot`/`export_html` as needed) to extract, per
screen/frame:

- What screens exist and how they navigate (e.g. dashboard, per-step run
  view, comparison/report view, config/systems view).
- What actions each screen exposes (run a step, pick L1 vs S1, view a
  report, stream logs, compare L1 vs S1 results).
- What state each screen needs to render (status of each pipeline step,
  latest report contents, live log lines, config values).
- Any explicit data shapes implied by the mockup (tables, charts, badges)
  that the API should return pre-shaped for, rather than the UI doing
  client-side transformation of raw script output.

Report this back as a screen-by-screen inventory before designing the API —
the API surface should be derived from what the UI actually needs, not
guessed in advance.

## Step 2 — design one clean public API, shared by CLI and web server

This is the important architectural constraint: **do not let the UI (via the
web server) or the CLI reach into `scripts/lib/*` or the script modules
directly.** Both the CLI and the FastAPI server should be thin adapters over
a single new public API module (e.g. `benchmark/api.py` or
`benchmark_api/__init__.py`), so there is exactly one place that knows how to
run a step, and CLI/UI can never drift out of sync.

The public API should expose only the operations a caller actually needs —
not every helper currently in `scripts/lib`. Concretely, something like:

```python
# benchmark_api/api.py — the ONLY module the CLI and the web server import from

def list_steps() -> list[StepInfo]: ...          # id, label, dependencies, blocked-reason if unconfigured
def get_step_status(step_id: str) -> StepStatus: ...   # not_run | running | done | failed | blocked
def run_step(step_id: str, system: str | None = None) -> RunHandle: ...  # kicks off a step, returns a handle for streaming
def stream_step_output(run_id: str) -> Iterator[LogLine]: ...  # for live log tail in the UI
def get_report(step_id: str, system: str | None = None) -> Report: ...  # parsed data/reports/*.json, not raw files
def list_systems() -> list[SystemInfo]: ...       # local_index / shared_index, configured or blocked
def get_config() -> BenchmarkConfig: ...          # read-only view of config/benchmark.yaml
def compare_systems(step_id: str) -> Comparison: ...  # L1 vs S1 report diff, if the design has a compare screen
```

Everything internal (`load_config`, `write_json`, per-script argument
parsing, OpenSearch client details, docker stats collection) stays private to
`benchmark_api`'s implementation. The CLI's subcommands and the FastAPI
route handlers should each be a near-trivial call into this API plus
formatting (table/JSON for CLI, JSON for HTTP). Treat this API module as the
one piece of the system that needs a real design pass — get its method list
and types right before wiring the CLI or server to it, since both will be
built against it.

Existing script modules (`scripts/inspect_products.py` etc.) should become
thin CLI-callable wrappers around functions the API module calls internally,
or be absorbed into the API layer directly — avoid keeping two parallel ways
to invoke the same step (raw `python -m scripts.x` and `benchmark run x`)
once the CLI exists, unless the user wants the old entrypoints kept for
backward compatibility (ask if unclear).

## Step 3 — propose the concrete plan

Deliver:

1. The screen inventory from Step 1.
2. The public API surface from Step 2 (method signatures + types), justified
   against what the screens need.
3. A file-layout plan: new `benchmark_api/` (or similar) package, new
   `cli.py` entrypoint (Typer), new `server.py` (FastAPI) mounting routes
   over the API, and what happens to the existing `scripts/` modules.
4. How live progress/log streaming works end-to-end (long-running steps like
   `index_initial_corpus`/`feed_write_workload` take real time — the design
   likely has some kind of progress/log view for this).
5. Anything the design implies that doesn't map cleanly onto the current
   scripts (e.g. a comparison view needs both L1 and S1 to have been run —
   flag this rather than papering over it).

Do not start implementing until this plan is reviewed — this is an
architecture/design pass, matching the "propose adjustments" framing of the
original task.
