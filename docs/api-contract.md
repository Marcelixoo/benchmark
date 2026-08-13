# Benchmark API contract (backend for the Pencil UI)

Status: implemented in `benchmark_api/` + `server.py`, backing the CLI
(`cli.py`) and the FastAPI HTTP server. This file is the frontend-facing
spec — update it if the contract changes.

Base URL: `http://localhost:8000` (run via `uvicorn server:app --reload
--port 8000`). All responses are JSON except the SSE stream endpoint. CORS is
open for local development.

`step_id` is one of: `inspect_products`, `inspect_queries`,
`prepare_products`, `prepare_queries`, `validate_compatibility`,
`create_index`, `smoke_test`, `index_initial_corpus`, `feed_write_workload`,
`verify_cluster`.

`system` (where applicable) is `local_index` or `shared_index`.

## Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/steps` | — | `StepInfo[]` |
| GET | `/api/systems` | — | `SystemInfo[]` |
| GET | `/api/config` | — | `BenchmarkConfig` |
| GET | `/api/steps/{step_id}/status?system=` | — | `StepState` |
| POST | `/api/steps/{step_id}/run` | `{"system"?: string, "calibration"?: bool}` | `RunHandle` (202) |
| GET | `/api/runs/{run_id}/stream` | — | SSE stream of `LogLine`, closes when the run reaches a terminal state |
| GET | `/api/steps/{step_id}/report?system=` | — | `Report` (404 if never run) |
| GET | `/api/steps/{step_id}/compare` | — | `Comparison` |

Error responses are `{"detail": "<message>"}` with status 404 (unknown
step/run, no report yet) or 409 (step blocked / bad run request).

## Shapes

```jsonc
// StepInfo
{ "id": "smoke_test", "label": "Smoke-test search endpoint", "needs_system": true, "depends_on": ["prepare_queries", "create_index"] }

// SystemInfo
{ "id": "local_index", "label": "Local Index", "write_base_url": "http://localhost:9200", "search_base_url": "http://localhost:9200", "configured": true }

// BenchmarkConfig
{ "seed": 42, "systems": [ /* SystemInfo[] */ ], "paths": { "data_dir": "data", "reports_dir": "data/reports" }, "workload": { "index_batch_size": 500, "...": "..." } }

// StepState — status: not_run | blocked | running | done | failed
{
  "step_id": "smoke_test", "status": "blocked", "system": "shared_index",
  "run_id": null, "blocked_reason": "'shared_index' has no search_base_url configured in config/benchmark.yaml. Bring up its OpenSearch stack under infra/ first.",
  "last_report_path": null, "updated_at": "2026-08-12T10:00:00Z"
}

// RunHandle — returned immediately by POST .../run
{ "run_id": "a1b2c3d4e5f6", "step_id": "smoke_test", "system": "local_index" }

// LogLine — one SSE `data:` event per line, in order (seq starts at 0)
{ "run_id": "a1b2c3d4e5f6", "seq": 3, "stream": "stdout", "text": "Wrote data/reports/smoke_test_local_index.json", "ts": "2026-08-12T10:00:05Z" }

// Report — data is exactly what the underlying script already wrote to data/reports/ or data/, untouched
{ "step_id": "smoke_test", "system": "local_index", "path": "data/reports/smoke_test_local_index.json", "data": { "summary": { "...": "..." }, "sample_for_manual_review": [ ], "errors": [] } }

// Comparison
{ "step_id": "verify_cluster", "systems": { "local_index": { "...": "..." }, "shared_index": null } }
```

`Comparison.systems[<id>]` is `null` when that system has no report yet
(don't assume both sides are always present).

## Frontend integration notes

- **Status polling**: `GET /api/steps/{id}/status?system=` for each step's
  badge. `blocked_reason` is human-readable and safe to render directly —
  it's the same message the underlying script would print.
- **Running a step**: `POST .../run` → get `run_id` → open
  `new EventSource("/api/runs/{run_id}/stream")` for live logs → on stream
  close, re-fetch `status` (now `done`/`failed`) and `report`.
- **Report shape varies per step** — `Report.data` is passed through
  unmodified from each script's own JSON output (see `README.md` "Results"
  section for what each one contains), not normalized into one common shape.
  Each report view needs its own renderer.
- Steps that don't take a system (`inspect_products`, `inspect_queries`,
  `prepare_products`, `prepare_queries`, `validate_compatibility`) ignore the
  `system` query param — omit it or pass anything, response is the same.
- `smoke_test` and `index_initial_corpus` accept `calibration: true` in the
  run body (matches the scripts' `--calibration` flag) — everything else
  ignores it.

## Example session

```bash
curl localhost:8000/api/steps
curl localhost:8000/api/systems
curl -X POST localhost:8000/api/steps/inspect_products/run
# => {"run_id": "...", "step_id": "inspect_products", "system": null}
curl -N localhost:8000/api/runs/<run_id>/stream
curl localhost:8000/api/steps/inspect_products/status
curl localhost:8000/api/steps/inspect_products/report
```
