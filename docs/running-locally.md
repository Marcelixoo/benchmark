# Running the benchmark server locally

## 1. Install dependencies

The new CLI/server deps (`typer`, `fastapi`, `uvicorn`, `sse-starlette`) may
not be mirrored on an internal package index. If `pip install -r
requirements.txt` fails to resolve them, install from public PyPI:

```bash
pip install -r requirements.txt
# if typer/fastapi/uvicorn/sse-starlette fail to resolve:
pip install --index-url https://pypi.org/simple \
  typer==0.19.0 fastapi==0.119.0 "uvicorn[standard]==0.38.0" sse-starlette==2.1.3
```

## 2. Start the server

From the repo root:

```bash
uvicorn server:app --reload --port 8000
```

You should see `Uvicorn running on http://127.0.0.1:8000`. Leave this
running in its own terminal.

## 3. Check endpoints

### With curl

```bash
curl localhost:8000/api/steps
curl localhost:8000/api/systems
curl localhost:8000/api/config

curl -X POST localhost:8000/api/steps/inspect_products/run \
  -H 'Content-Type: application/json' -d '{}'
# => {"run_id": "...", ...}

curl -N localhost:8000/api/runs/<run_id>/stream   # live log lines, closes on completion

curl "localhost:8000/api/steps/inspect_products/status"
curl "localhost:8000/api/steps/inspect_products/report"
curl "localhost:8000/api/steps/verify_cluster/compare"
```

### With Postman

Import [`postman/benchmark-api.postman_collection.json`](benchmark-api.postman_collection.json).
It defines collection variables `base_url` (defaults to
`http://localhost:8000`), `step_id`, `system`, and `run_id`.

Suggested order:
1. **List steps** / **List systems** / **Get config** — sanity check the
   server is up and reading `config/benchmark.yaml` correctly.
2. **Get step status** — set `step_id`/`system` variables first (e.g.
   `inspect_products` needs no `system`; `smoke_test` does).
3. **Run step** — has a test script that captures the returned `run_id`
   into the collection variable automatically, so the next request can use
   it.
4. **Stream run logs (SSE)** — uses `{{run_id}}` from the previous step.
   Postman's response viewer shows SSE events as they arrive for
   `text/event-stream` responses; if your Postman version doesn't render it
   incrementally, use the curl `-N` form above instead.
5. **Get report** / **Compare systems** — once a run finishes.

### API reference

Full endpoint list, request/response shapes, and status codes:
[`docs/api-contract.md`](api-contract.md).

## 4. Stop the server

`Ctrl-C` in the terminal running uvicorn. This does not affect any
in-progress pipeline runs already recorded on disk (reports under
`data/reports/` and `data/`) — only in-memory run/log tracking is lost on
restart.
