// Frontend for the benchmark pipeline — talks ONLY to the 8 endpoints
// documented in docs/api-contract.md. No endpoint here that isn't in that
// contract; anything the mockup needs that the contract can't supply is
// deliberately left out rather than faked (see the plan's cut list).

const API = "";

const SYSTEMS = [
  { id: "local_index", short: "L1", badge: "badge-l1", accent: "l1" },
  { id: "shared_index", short: "S1", badge: "badge-s1", accent: "s1" },
];

// The mockup's 4-chip pipeline lane. `feed_write_workload` and the 5
// system-independent prep steps have no slot in the design and stay
// CLI-only for this iteration.
const PIPELINE_STEPS = [
  { id: "verify_cluster", label: "Verify cluster" },
  { id: "create_index", label: "Create index" },
  { id: "index_initial_corpus", label: "Index corpus" },
  { id: "smoke_test", label: "Run query set" },
];

const ICONS = {
  check: '<path d="M20 6L9 17l-5-5"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 1 1 8 0v4"/>',
  loader: '<path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"/>',
  circle: '<circle cx="12" cy="12" r="9"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 8v.01M11 12h1v5h1"/>',
  alert: '<path d="M12 9v4M12 17h.01M10.3 3.9L2.7 17a2 2 0 0 0 1.7 3h15.2a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
};

function icon(name, extra = "") {
  return `<svg viewBox="0 0 24 24" ${extra}>${ICONS[name] || ""}</svg>`;
}

// ---------- fetch helpers ----------

async function apiGet(path) {
  const res = await fetch(API + path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function tryGet(path) {
  try {
    return await apiGet(path);
  } catch (e) {
    return null;
  }
}

// ---------- formatting helpers ----------

function fmtNum(n) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString();
}

function fmtBytes(n) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = Number(n);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

function fmtDuration(s) {
  if (s === null || s === undefined) return "—";
  s = Math.round(Number(s));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function get(obj, path, fallback) {
  try {
    const v = path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
    return v === undefined || v === null ? fallback : v;
  } catch {
    return fallback;
  }
}

// ---------- router ----------

const main = document.getElementById("main");
let pollTimer = null;
const activeStreams = new Set();
// Bumped on every navigation. Async render functions (renderOverview,
// renderComparison) capture the generation active when they were invoked
// and check it before touching the DOM — otherwise a fetch that resolves
// after the user has already navigated away would clobber whatever the
// new route just rendered.
let routeGeneration = 0;
function isStale(generation) {
  return generation !== routeGeneration;
}
// Log lines survive the 2s repaint cycle (repaint rebuilds lane HTML from
// scratch, so the panel content is restored from here rather than lost).
const logBuffers = {}; // systemId -> [{stream, text}]

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

function setActiveNav(route) {
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.classList.toggle("active", a.dataset.route === route);
  });
}

const ROUTES = {
  overview: renderOverview,
  comparison: renderComparison,
  history: renderHistory,
};

function router() {
  const route = (location.hash || "#overview").slice(1);
  routeGeneration++;
  const generation = routeGeneration;
  stopPolling();
  setActiveNav(route);
  (ROUTES[route] || renderOverview)(generation);
}

window.addEventListener("hashchange", router);
window.addEventListener("DOMContentLoaded", router);

// ================= Run Overview =================

function skeletonOverview() {
  main.innerHTML = `
    <div class="topbar">
      <div><h1 class="page-title">Run Overview</h1></div>
    </div>
    <div class="card">
      <div class="skeleton" style="height:14px;width:40%;margin-bottom:10px;"></div>
      <div class="skeleton" style="height:60px;margin-bottom:10px;"></div>
      <div class="skeleton" style="height:60px;"></div>
    </div>
    <div class="loading-row">${icon("loader")}<span>Loading pipeline status…</span></div>
  `;
}

function stepIconFor(status) {
  switch (status) {
    case "done":
      return icon("check");
    case "failed":
      return icon("x");
    case "blocked":
      return icon("lock");
    case "running":
      return icon("loader");
    default:
      return icon("circle");
  }
}

function stepMeta(stepId, status, state, report) {
  if (status === "blocked") return { text: state.blocked_reason, isError: false };
  if (status === "running") return { text: "Running…", isError: false };
  if (status === "not_run") return { text: null, isError: false };
  if (!report) return { text: status === "failed" ? "Failed — no report available" : null, isError: status === "failed" };

  if (stepId === "verify_cluster") {
    const checks = get(report, "data.checks", []);
    const passed = checks.filter((c) => c.passed).length;
    return { text: `${passed}/${checks.length} passed`, isError: status === "failed" };
  }
  if (stepId === "index_initial_corpus") {
    const dps = get(report, "data.docs_per_s", null);
    return { text: dps ? `${fmtNum(Math.round(dps))} docs/s` : null, isError: status === "failed" };
  }
  if (stepId === "smoke_test") {
    const attempted = get(report, "data.summary.queries_attempted", null);
    const errors = get(report, "data.summary.errors_count", 0);
    return { text: attempted !== null ? `${fmtNum(attempted)} · ${errors} errors` : null, isError: status === "failed" };
  }
  return { text: null, isError: false };
}

async function loadStepState(stepId, systemId) {
  const state = await apiGet(`/api/steps/${stepId}/status?system=${systemId}`);
  let report = null;
  if (state.status === "done" || state.status === "failed") {
    report = await tryGet(`/api/steps/${stepId}/report?system=${systemId}`);
  }
  return { state, report };
}

async function renderOverview(generation) {
  skeletonOverview();

  let data;
  try {
    const perSystem = await Promise.all(
      SYSTEMS.map(async (sys) => {
        const steps = await Promise.all(PIPELINE_STEPS.map((s) => loadStepState(s.id, sys.id)));
        return { sys, steps };
      })
    );
    data = perSystem;
  } catch (e) {
    if (isStale(generation)) return;
    main.innerHTML = `
      <div class="topbar"><h1 class="page-title">Run Overview</h1></div>
      <div class="alert error">${icon("alert")}<span>Failed to load pipeline status — ${esc(e.message)}. Is the server running?</span></div>
    `;
    return;
  }
  if (isStale(generation)) return;

  paintOverview(data);

  // Polls every 2s regardless of run state, matching the mockup's own
  // "Polling · 2s interval" label on Run Overview. Stops as soon as this
  // route is no longer the active one.
  pollTimer = setInterval(async () => {
    if (isStale(generation)) {
      stopPolling();
      return;
    }
    try {
      const refreshed = await Promise.all(
        SYSTEMS.map(async (sys) => {
          const steps = await Promise.all(PIPELINE_STEPS.map((s) => loadStepState(s.id, sys.id)));
          return { sys, steps };
        })
      );
      if (isStale(generation)) return;
      paintOverview(refreshed);
    } catch {
      /* transient — keep showing last good render, try again next tick */
    }
  }, 2000);
}

function verifyFailureDetailHtml(sys, verifyEntry) {
  if (!verifyEntry || verifyEntry.state.status !== "failed" || !verifyEntry.report) return "";
  const checks = get(verifyEntry.report, "data.checks", []);
  const failed = checks.filter((c) => !c.passed);
  const passed = checks.length - failed.length;
  if (failed.length === 0) return "";
  return `
    <div class="card">
      <div class="card-header">
        <p class="card-title">${esc(sys.short)} verify checks (${passed}/${checks.length} passed)</p>
        <p class="card-desc">Failed checks must pass before indexing can start.</p>
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;">
        ${failed
          .map(
            (c) =>
              `<div style="display:flex;gap:8px;align-items:center;color:var(--error);font-family:var(--font-mono);font-size:11px;">${icon(
                "x",
                'style="width:14px;height:14px;"'
              )}<span>${esc(c.name)} — FAIL</span></div>`
          )
          .join("")}
      </div>
    </div>`;
}

function paintOverview(data) {
  const anyCalibration = data.some((d) =>
    d.steps.some(({ report }) => get(report, "data.calibration_run", get(report, "data.summary.calibration_run", false)))
  );

  const verifyIdx = PIPELINE_STEPS.findIndex((s) => s.id === "verify_cluster");
  const failedVerify = data.filter((d) => d.steps[verifyIdx].state.status === "failed");
  const verifyDetails = data.map((d) => verifyFailureDetailHtml(d.sys, d.steps[verifyIdx])).join("");

  main.innerHTML = `
    <div class="topbar">
      <div>
        <h1 class="page-title">Run Overview</h1>
        <div class="page-sub">Status polled from benchmark_api — live per-step data only, no synthesized fields.</div>
      </div>
      <div class="meta-row">
        ${anyCalibration ? `<span class="badge badge-warning">Calibration run</span>` : ""}
        <span class="poll-text">Polling · 2s interval</span>
      </div>
    </div>

    ${
      anyCalibration
        ? `<div class="alert">${icon("info")}<span>Calibration run — metrics shown here are exploratory, not locked benchmark results.</span></div>`
        : ""
    }
    ${failedVerify
      .map(
        (d) =>
          `<div class="alert error">${icon("alert")}<span>Verify failed for ${esc(d.sys.short)} — fix cluster configuration before continuing.</span></div>`
      )
      .join("")}

    <div class="card">
      <div class="card-header">
        <p class="card-title">Pipeline progress</p>
        <p class="card-desc">Verify → Create index → Index corpus → Run query set · systems run independently</p>
      </div>
      ${data.map((d) => laneHtml(d.sys, d.steps)).join("")}
    </div>

    ${verifyDetails}
    <div id="run-message"></div>
  `;

  bindOverviewHandlers(data);
  autoTailRunningSteps(data);
}

// A step can be "running" because ANY process started it — this browser's
// own Run button, a `cli.py run ...` in another terminal, another server
// instance. get_step_status now surfaces that run's run_id regardless of
// who owns it (see benchmark_api/run_state.py), so whenever polling finds
// a running step we haven't attached a log panel to yet, attach one.
function autoTailRunningSteps(data) {
  for (const { sys, steps } of data) {
    const runningStep = steps.find((s) => s.state.status === "running" && s.state.run_id);
    if (runningStep && !activeStreams.has(runningStep.state.run_id)) {
      tailRun(sys.id, runningStep.state.run_id);
    }
  }
}

function laneHtml(sys, steps) {
  const runningStep = steps.find((s) => s.state.status === "running");
  const blockedStep = steps.find((s) => s.state.status === "blocked");
  let statusText = "";
  if (runningStep) {
    const label = PIPELINE_STEPS[steps.indexOf(runningStep)].label;
    statusText = `${label} · running`;
  } else if (blockedStep) {
    statusText = "Blocked";
  } else if (steps.every((s) => s.state.status === "done")) {
    statusText = "Complete";
  }

  return `
    <div class="lane" data-system="${sys.id}">
      <div class="lane-header">
        <div class="lane-title">
          <span class="badge ${sys.badge}">${sys.short}</span>
          <span class="lane-name">${sys.id === "local_index" ? "Local Index" : "Shared Index"}</span>
        </div>
        <div style="display:flex;align-items:center;gap:10px;">
          ${statusText ? `<span class="lane-status ${blockedStep ? "is-blocked" : ""}">${esc(statusText)}</span>` : ""}
          <label class="calibration-toggle">
            <input type="checkbox" class="calibration-checkbox" data-system="${sys.id}" />
            calibration
          </label>
        </div>
      </div>
      <div class="steps-row">
        ${steps
          .map((s, i) => {
            const step = PIPELINE_STEPS[i];
            const status = s.state.status;
            const meta = stepMeta(step.id, status, s.state, s.report);
            const canRun = ["not_run", "done", "failed"].includes(status);
            return `
              <div class="step-chip status-${status}">
                <div class="step-icon">${stepIconFor(status)}</div>
                <div class="step-body">
                  <div class="step-label">${esc(step.label)}</div>
                  ${meta.text ? `<div class="step-meta ${meta.isError ? "is-error" : ""}">${esc(meta.text)}</div>` : ""}
                  ${canRun ? `<button class="step-run-btn" data-step="${step.id}" data-system="${sys.id}">Run</button>` : ""}
                </div>
              </div>`;
          })
          .join("")}
      </div>
      ${logPanelHtml(sys.id)}
    </div>
  `;
}

function logPanelHtml(systemId) {
  const buffer = logBuffers[systemId];
  if (!buffer || buffer.length === 0) {
    return `<div class="log-panel" data-log-for="${systemId}" style="display:none;"></div>`;
  }
  const lines = buffer
    .map((line) => `<div${line.stream === "stderr" ? ' class="stderr"' : ""}>${esc(line.text)}</div>`)
    .join("");
  return `<div class="log-panel" data-log-for="${systemId}" style="display:block;">${lines}</div>`;
}

function bindOverviewHandlers(data) {
  main.querySelectorAll(".step-run-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const stepId = btn.dataset.step;
      const systemId = btn.dataset.system;
      const calibBox = main.querySelector(`.calibration-checkbox[data-system="${systemId}"]`);
      const calibration = calibBox ? calibBox.checked : false;
      btn.disabled = true;
      btn.textContent = "Starting…";
      const msgBox = document.getElementById("run-message");
      try {
        const handle = await apiPost(`/api/steps/${stepId}/run`, { system: systemId, calibration });
        msgBox.innerHTML = "";
        tailRun(systemId, handle.run_id);
      } catch (e) {
        msgBox.innerHTML = `<div class="alert error">${icon("alert")}<span>${esc(e.message)}</span></div>`;
        btn.disabled = false;
        btn.textContent = "Run";
      }
    });
  });
}

function tailRun(systemId, runId) {
  if (activeStreams.has(runId)) return;
  activeStreams.add(runId);
  logBuffers[systemId] = [];

  const scrollLogPanel = () => {
    const panel = main.querySelector(`.log-panel[data-log-for="${systemId}"]`);
    if (panel) {
      panel.style.display = "block";
      panel.scrollTop = panel.scrollHeight;
    }
  };
  scrollLogPanel();

  const source = new EventSource(`/api/runs/${runId}/stream`);
  source.onmessage = (evt) => {
    try {
      const line = JSON.parse(evt.data);
      logBuffers[systemId].push({ stream: line.stream, text: line.text });
      const panel = main.querySelector(`.log-panel[data-log-for="${systemId}"]`);
      if (panel) {
        const div = document.createElement("div");
        if (line.stream === "stderr") div.className = "stderr";
        div.textContent = line.text;
        panel.appendChild(div);
        panel.style.display = "block";
        panel.scrollTop = panel.scrollHeight;
      }
    } catch {
      /* ignore malformed line */
    }
  };
  source.onerror = () => {
    source.close();
    activeStreams.delete(runId);
  };
}

// ================= Run Detail — Comparison =================

function skeletonComparison() {
  main.innerHTML = `
    <div class="topbar"><h1 class="page-title">Run Detail — L1 vs S1</h1></div>
    <div class="card">
      <div class="skeleton" style="height:70px;margin-bottom:10px;"></div>
      <div class="skeleton" style="height:120px;"></div>
    </div>
    <div class="loading-row">${icon("loader")}<span>Reading comparison reports…</span></div>
  `;
}

async function renderComparison(generation) {
  skeletonComparison();
  let indexCompare, queryCompare, verifyCompare;
  try {
    [indexCompare, queryCompare, verifyCompare] = await Promise.all([
      apiGet("/api/steps/index_initial_corpus/compare"),
      apiGet("/api/steps/smoke_test/compare"),
      apiGet("/api/steps/verify_cluster/compare"),
    ]);
  } catch (e) {
    if (isStale(generation)) return;
    main.innerHTML = `
      <div class="topbar"><h1 class="page-title">Run Detail — L1 vs S1</h1></div>
      <div class="alert error">${icon("alert")}<span>Failed to load comparison — ${esc(e.message)}</span></div>
    `;
    return;
  }
  if (isStale(generation)) return;

  const l1Index = indexCompare.systems.local_index;
  const s1Index = indexCompare.systems.shared_index;
  const l1Query = queryCompare.systems.local_index;
  const s1Query = queryCompare.systems.shared_index;
  const l1Verify = verifyCompare.systems.local_index;
  const s1Verify = verifyCompare.systems.shared_index;

  const bothIndexed = l1Index && s1Index;
  const calibration = get(l1Index, "calibration_run", get(l1Query, "summary.calibration_run", false));

  main.innerHTML = `
    <div class="topbar">
      <div>
        <h1 class="page-title">Run Detail — ${bothIndexed ? "L1 vs S1" : "L1 (S1 pending)"}</h1>
        <div class="page-sub">Comparison always uses the latest report per system (no run picker in the current API).</div>
      </div>
      <div class="meta-row">
        ${calibration ? `<span class="badge badge-warning">Calibration run — not final benchmark</span>` : ""}
      </div>
    </div>

    ${calibration ? `<div class="alert">${icon("info")}<span>These numbers are from calibration runs. Do not treat them as locked benchmark results.</span></div>` : ""}

    ${!s1Index ? `
      <div class="empty-state">
        ${icon("clock")}
        <div>S1 not yet run</div>
        <div style="font-size:11px;margin-top:4px;">Shared Index results will appear here once index_initial_corpus completes for shared_index.</div>
      </div>` : ""}

    ${indexingMetricsCard(l1Index, s1Index)}
    ${queryQualityCard(l1Query, s1Query)}
    ${resourceUsageCard(l1Index, s1Index)}
    ${sampleQueryCard(l1Query, s1Query)}
    ${infraVerificationCard(l1Verify, s1Verify)}
  `;
}

function indexStatsBreakdown(report) {
  const stats = get(report, "reported_index_stats", null);
  if (!stats) return { storeBytes: null, segmentCount: null, mergeMs: null };
  return {
    storeBytes: get(stats, "_all.primaries.store.size_in_bytes", null),
    segmentCount: get(stats, "_all.primaries.segments.count", null),
    mergeMs: get(stats, "_all.primaries.merges.total_time_in_millis", null),
  };
}

function remoteStoreDeltaBytes(report) {
  const before = get(report, "remote_store_nodes_stats_before.nodes", null);
  const after = get(report, "remote_store_nodes_stats_after.nodes", null);
  if (!before || !after) return null;
  const sum = (nodes) =>
    Object.values(nodes).reduce((acc, n) => acc + (get(n, "indices.segments.remote_store.upload.total_upload_size_in_bytes", 0) || 0), 0);
  try {
    return sum(after) - sum(before);
  } catch {
    return null;
  }
}

function indexingMetricsCard(l1, s1) {
  if (!l1 && !s1) return "";
  const maxDocsPerS = Math.max(get(l1, "docs_per_s", 0) || 0, get(s1, "docs_per_s", 0) || 0, 1);
  const maxDuration = Math.max(get(l1, "duration_s", 0) || 0, get(s1, "duration_s", 0) || 0, 1);
  const l1Stats = indexStatsBreakdown(l1 || {});
  const s1Stats = indexStatsBreakdown(s1 || {});
  const delta = s1 ? remoteStoreDeltaBytes(s1) : null;

  const tile = (label, value, unit, accent) => `
    <div class="metric-tile">
      <div class="metric-label">${esc(label)}</div>
      <div class="metric-value" style="color:var(--${accent})">${value}</div>
      <div class="metric-unit">${esc(unit)}</div>
    </div>`;

  const bar = (label, l1v, s1v, max) => `
    <div class="bar-row">
      <div class="bar-label">${esc(label)}</div>
      <div class="bar-track"><div class="bar-fill l1" style="width:${Math.max(3, (l1v / max) * 240)}px"></div><span style="font-size:10px;color:var(--muted-foreground)">L1</span></div>
      <div class="bar-track"><div class="bar-fill s1" style="width:${Math.max(3, (s1v / max) * 240)}px"></div><span style="font-size:10px;color:var(--muted-foreground)">S1</span></div>
    </div>`;

  const tableRow = (label, l1v, s1v) => `
    <tr><td>${esc(label)}</td><td class="mono l1">${l1v}</td><td class="mono s1">${s1v}</td></tr>`;

  return `
    <div class="card">
      <div class="card-header">
        <p class="card-title">Indexing metrics</p>
        <p class="card-desc">Headline comparison: throughput (docs/s) and duration</p>
      </div>
      <div class="metrics-row">
        ${tile("L1 throughput", fmtNum(get(l1, "docs_per_s", null)), "docs/s", "l1")}
        ${tile("S1 throughput", fmtNum(get(s1, "docs_per_s", null)), "docs/s", "s1")}
        ${tile("L1 duration", fmtDuration(get(l1, "duration_s", null)), "wall time", "l1")}
        ${tile("S1 duration", fmtDuration(get(s1, "duration_s", null)), "wall time", "s1")}
      </div>
      <div class="bars">
        ${bar("Docs/s (headline)", get(l1, "docs_per_s", 0) || 0, get(s1, "docs_per_s", 0) || 0, maxDocsPerS)}
        ${bar("Duration (relative)", get(l1, "duration_s", 0) || 0, get(s1, "duration_s", 0) || 0, maxDuration)}
      </div>
      <table class="data-table">
        <thead><tr><th>Metric</th><th>L1</th><th>S1</th></tr></thead>
        <tbody>
          ${tableRow("Documents indexed", fmtNum(get(l1, "documents_indexed", null)), fmtNum(get(s1, "documents_indexed", null)))}
          ${tableRow("Final store size", fmtBytes(l1Stats.storeBytes), fmtBytes(s1Stats.storeBytes))}
          ${tableRow("Segment count", fmtNum(l1Stats.segmentCount), fmtNum(s1Stats.segmentCount))}
          ${tableRow("Merge time", l1Stats.mergeMs !== null ? fmtDuration(l1Stats.mergeMs / 1000) : "—", s1Stats.mergeMs !== null ? fmtDuration(s1Stats.mergeMs / 1000) : "—")}
        </tbody>
      </table>
      ${delta !== null ? `
        <div class="callout">
          <div class="callout-title">S1 only — remote store upload delta</div>
          <div class="callout-value">${fmtBytes(delta)} shipped to MinIO</div>
        </div>` : ""}
    </div>`;
}

function queryQualityCard(l1, s1) {
  if (!l1 && !s1) return "";
  const l1s = get(l1, "summary", {});
  const s1s = get(s1, "summary", {});
  const row = (label, l1v, s1v) => `<tr><td>${esc(label)}</td><td class="mono l1">${l1v}</td><td class="mono s1">${s1v}</td></tr>`;
  return `
    <div class="card">
      <div class="card-header">
        <p class="card-title">Query quality metrics</p>
        <p class="card-desc">Errors flagged distinctly from zero-result rate</p>
      </div>
      <table class="data-table">
        <thead><tr><th>Metric</th><th>L1</th><th>S1</th></tr></thead>
        <tbody>
          ${row("Queries attempted / succeeded", `${fmtNum(l1s.queries_attempted)} / ${fmtNum(l1s.queries_succeeded)}`, `${fmtNum(s1s.queries_attempted)} / ${fmtNum(s1s.queries_succeeded)}`)}
          ${row("Error rate %", l1s.error_rate_pct ?? "—", s1s.error_rate_pct ?? "—")}
          ${row("% with ≥1 result", l1s.pct_with_at_least_one_result ?? "—", s1s.pct_with_at_least_one_result ?? "—")}
          ${row("Zero-result rate %", l1s.zero_result_rate_pct ?? "—", s1s.zero_result_rate_pct ?? "—")}
          ${row("Median / p95 result count", `${l1s.median_result_count ?? "—"} / ${l1s.p95_result_count ?? "—"}`, `${s1s.median_result_count ?? "—"} / ${s1s.p95_result_count ?? "—"}`)}
        </tbody>
      </table>
    </div>`;
}

function resourceUsageCard(l1, s1) {
  const l1c = get(l1, "container_resource_usage", {});
  const s1c = get(s1, "container_resource_usage", {});
  const containers = { ...l1c, ...s1c };
  const names = Object.keys(containers);
  if (names.length === 0) return "";
  return `
    <div class="card">
      <div class="card-header">
        <p class="card-title">Resource usage summary</p>
        <p class="card-desc">Per-container CPU% and memory avg vs max — did we hit the ceiling?</p>
      </div>
      <table class="data-table">
        <thead><tr><th>Container</th><th>CPU avg</th><th>CPU max</th><th>Mem avg</th><th>Mem max</th></tr></thead>
        <tbody>
          ${names
            .map((name) => {
              const c = containers[name];
              return `<tr>
                <td class="mono">${esc(name)}</td>
                <td class="mono">${c.cpu_pct_avg ?? "—"}%</td>
                <td class="mono">${c.cpu_pct_max ?? "—"}%</td>
                <td class="mono">${c.mem_mb_avg ? fmtNum(c.mem_mb_avg) + " MB" : "—"}</td>
                <td class="mono">${c.mem_mb_max ? fmtNum(c.mem_mb_max) + " MB" : "—"}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>
    </div>`;
}

function formatTopResult(doc) {
  if (!doc) return "—";
  const parts = [];
  if (doc.title) parts.push(String(doc.title).slice(0, 40) + (String(doc.title).length > 40 ? "…" : ""));
  if (doc.price !== undefined && doc.price !== null) parts.push(`$${doc.price}`);
  if (doc.stars !== undefined && doc.stars !== null) parts.push(`★${doc.stars}`);
  return parts.join(" · ") || "—";
}

function sampleQueryCard(l1, s1) {
  const l1Rows = get(l1, "sample_for_manual_review", []);
  const s1Rows = get(s1, "sample_for_manual_review", []);
  if (l1Rows.length === 0 && s1Rows.length === 0) return "";

  const rowsHtml = (rows, sysLabel, badgeClass) =>
    rows
      .map(
        (r) => `
        <tr>
          <td><span class="badge ${badgeClass}" style="margin-right:6px;">${sysLabel}</span>${esc(r.query)}</td>
          <td class="mono">${r.result_count}</td>
          <td>${r.result_count === 0 ? "— (zero results, not error)" : esc(formatTopResult((r.results || [])[0]))}</td>
        </tr>`
      )
      .join("");

  return `
    <div class="card">
      <div class="card-header">
        <p class="card-title">Sample query inspector</p>
        <p class="card-desc">From sample_for_manual_review — spot-check relevance by eye</p>
      </div>
      <div class="query-search">
        ${icon("search")}
        <input type="text" id="sample-query-filter" placeholder="Filter sample queries…" />
      </div>
      <table class="data-table" id="sample-query-table">
        <thead><tr><th>Query</th><th>Count</th><th>Top result</th></tr></thead>
        <tbody>
          ${rowsHtml(l1Rows, "L1", "badge-l1")}
          ${rowsHtml(s1Rows, "S1", "badge-s1")}
        </tbody>
      </table>
    </div>`;
}

function infraVerificationCard(l1, s1) {
  if (!l1 && !s1) return "";
  const summarize = (report) => {
    const checks = get(report, "checks", []);
    const passed = checks.filter((c) => c.passed).length;
    return { passed, total: checks.length, failed: checks.filter((c) => !c.passed) };
  };
  const l1s = l1 ? summarize(l1) : null;
  const s1s = s1 ? summarize(s1) : null;
  const allFailed = [...(l1s ? l1s.failed.map((c) => `L1: ${c.name}`) : []), ...(s1s ? s1s.failed.map((c) => `S1: ${c.name}`) : [])];

  return `
    <div class="card">
      <div class="collapsible-summary">
        <div>
          <p class="card-title" style="margin:0;">Infra verification (collapsed)</p>
          <div class="hint">${l1s ? `L1: ${l1s.passed}/${l1s.total} passed` : "L1: not run"} · ${s1s ? `S1: ${s1s.passed}/${s1s.total} passed` : "S1: not run"}</div>
        </div>
      </div>
      ${allFailed.length
        ? `<details style="margin-top:10px;">
            <summary style="font-size:11px;color:var(--muted-foreground);cursor:pointer;">Expand to view failed checks (${allFailed.length})</summary>
            <div style="margin-top:8px;display:flex;flex-direction:column;gap:4px;">
              ${allFailed.map((f) => `<div style="font-family:var(--font-mono);font-size:11px;color:var(--error);">${esc(f)}</div>`).join("")}
            </div>
          </details>`
        : `<div class="hint" style="margin-top:6px;">Expand to view the 9-check checklist per system (cluster health, shards, mappings, …)</div>`}
    </div>`;
}

document.addEventListener("input", (e) => {
  if (e.target && e.target.id === "sample-query-filter") {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll("#sample-query-table tbody tr").forEach((tr) => {
      tr.style.display = tr.textContent.toLowerCase().includes(q) ? "" : "none";
    });
  }
});

// ================= Run History (stub — see plan section 2, item 3) =================

function renderHistory() {
  main.innerHTML = `
    <div class="topbar">
      <div>
        <h1 class="page-title">Run History</h1>
        <div class="page-sub">data/reports/*.json</div>
      </div>
    </div>
    <div class="empty-state">
      ${icon("info")}
      <div>Run History is not available yet</div>
      <div style="font-size:11px;margin-top:6px;max-width:420px;margin-left:auto;margin-right:auto;">
        The current API contract (docs/api-contract.md) only exposes per-step
        <code>report</code>/<code>compare</code> endpoints — there's no
        endpoint yet that lists/scans <code>data/reports/*.json</code> across
        steps and systems. Deferred to a future iteration rather than
        approximated here.
      </div>
    </div>
  `;
}

router();
