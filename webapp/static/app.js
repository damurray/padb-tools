"use strict";

let currentPodPath = null;
let currentKind = "pod"; // "pod" or "parquet" (a padbloader parquet dropped instead of a pod)
let allJobs = [];
const activePolls = new Set();

// Tooltip help is always on: every control carries a native title-attribute
// tooltip inline in the HTML (static and dynamically-rendered rows alike), so
// there's nothing to toggle -- the old show/hide checkbox was removed
// (David 2026-09-23). No-op kept so late callers after row/card renders don't
// need touching.
function applyTooltipPref() {}

// ---------------------------------------------------------------------------
// Upload / drop
// ---------------------------------------------------------------------------

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");

dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) uploadPod(fileInput.files[0]);
});

["dragenter", "dragover"].forEach(evt =>
  dropzone.addEventListener(evt, e => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach(evt =>
  dropzone.addEventListener(evt, e => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", e => {
  const files = e.dataTransfer.files;
  if (files.length) uploadPod(files[0]);
});

document.getElementById("parquetPathBtn").addEventListener("click", () => {
  const v = document.getElementById("parquetPathInput").value.trim().replace(/^"|"$/g, "");
  if (!v) { alert("Enter the path of a .parquet file"); return; }
  usePathParquet(v);
});

// "Browse PadbLoader files" -- the parquet files in PadbLoader's export folder, newest first; a click uses one in place.
function _esc(s) { return String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c])); }
document.getElementById("padbloaderPickerClose").addEventListener("click", () => {
  document.getElementById("padbloaderPicker").classList.add("hidden");
});
document.getElementById("padbloaderBrowseBtn").addEventListener("click", async () => {
  const box = document.getElementById("padbloaderPicker");
  const body = document.getElementById("padbloaderPickerBody");
  box.classList.remove("hidden");
  body.textContent = "Looking...";
  let r;
  try { r = await (await fetch("/api/padbloader-files")).json(); }
  catch (e) { body.textContent = "Could not list PadbLoader files: " + e + "  (restart Start_web.bat if the app was updated)"; return; }
  document.getElementById("padbloaderPickerTitle").textContent = "PadbLoader files in " + r.data_dir;
  if (!r.exists) {
    body.innerHTML = "<p>That folder does not exist yet (" + _esc(r.source) + "). Run PadbLoader once, or type the path above.</p>";
    return;
  }
  if (!r.files.length) { body.innerHTML = "<p>No parquet files there yet.</p>"; return; }
  let h = (r.notes || []).map(n => "<p><em>" + _esc(n) + "</em></p>").join("");
  h += "<p>" + r.total + " file(s); newest first" + (r.total > r.files.length ? " (showing " + r.files.length + ")" : "")
     + ". Click Use to take a file in place.</p>";
  h += "<table><tr><th>Modified</th><th>File</th><th>Run / schedule</th><th>Rows</th><th>MB</th><th></th></tr>";
  r.files.forEach((f, i) => {
    h += "<tr><td>" + _esc(f.modified) + "</td><td>" + _esc(f.name) + "</td><td>" + _esc(f.stream) + "</td><td>"
       + (f.rows == null ? "" : f.rows.toLocaleString()) + "</td><td>" + f.size_mb + "</td><td>"
       + (f.complete ? '<button type="button" class="pbPick" data-i="' + i + '">Use</button>'
                     : '<span title="' + _esc(f.note) + '">not finished</span>') + "</td></tr>";
  });
  body.innerHTML = h + "</table>";
  body.querySelectorAll(".pbPick").forEach(b => b.addEventListener("click", () => {
    const f = r.files[+b.dataset.i];
    document.getElementById("parquetPathInput").value = f.path;
    box.classList.add("hidden");
    usePathParquet(f.path);
  }));
});

// PadbLoader watcher panel: status read from the watcher's output folder; "Check PadbLoader now" runs one pass.
function _wLoad(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
function _wSave(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable: fine */ } }
document.getElementById("watcherOut").value = _wLoad("padb_watcher_out", "");
document.getElementById("watcherStreams").value = _wLoad("padb_watcher_streams", "");
async function watcherRefresh() {
  const out = document.getElementById("watcherOut").value.trim();
  _wSave("padb_watcher_out", out);
  const body = document.getElementById("watcherBody");
  let r;
  try { r = await (await fetch("/api/watcher?out=" + encodeURIComponent(out))).json(); }
  catch (e) { body.textContent = "Could not read the watcher status: " + e + " (restart Start_web.bat if the app was updated)"; return; }
  document.getElementById("watcherSummary").textContent = r.running ? "-- running (pid " + r.running.pid + ")" : "-- not running";
  let h = "<p>Output folder: <code>" + _esc(r.out) + "</code>" + (r.exists ? "" : " (does not exist yet)") + "<br>Watching PadbLoader's scheduled output: <code>"
        + _esc(r.scheduled_dir) + "</code>" + (r.scheduled_exists ? "" : " (not there yet)") + "</p>";
  if (!r.running) h += "<p>To watch continuously, run in a terminal: <code>" + _esc(r.command) + "</code> (add <code>--once</code> in Task Scheduler).</p>";
  if (r.error) h += "<p><strong>" + _esc(r.error) + "</strong></p>";
  const c = r.counts || {};
  h += "<p>" + (r.total || 0) + " file(s) seen: " + Object.keys(c).map(k => c[k] + " " + _esc(k)).join(", ") + (r.updated ? " -- updated " + _esc(r.updated) : "") + "</p>";
  if ((r.runs || []).length) {
    h += "<table><tr><th>Schedule</th><th>File</th><th>Status</th><th>Finished</th><th>Results</th><th>Used</th></tr>";
    r.runs.forEach(x => {
      const links = (x.results || []).map(a => a.url ? '<a href="' + _esc(a.url) + '" target="_blank">' + _esc(a.name || "open") + "</a>"
        : (a.viewer ? '<span title="' + _esc(a.viewer) + '">too big for pages -- use the viewer</span>' : "")).filter(Boolean).slice(0, 6).join(", ");
      const used = Object.keys(x.opts || {}).map(k => k + "=" + (Array.isArray(x.opts[k]) ? x.opts[k].join("+") : x.opts[k])).join(" ");
      h += "<tr><td>" + _esc(x.stream || "") + "</td><td>" + _esc(x.stem || "") + "</td><td title=\"" + _esc(x.error || "") + "\">" + _esc(x.status || "")
        + (x.attempts > 1 ? " (" + x.attempts + " tries)" : "") + "</td><td>" + _esc(x.finished || "") + "</td><td>" + (x.pruned ? "(results pruned)" : links)
        + "</td><td>" + _esc(used) + "</td></tr>";
    });
    h += "</table>";
  }
  body.innerHTML = h;
}
document.getElementById("watcherRefresh").addEventListener("click", watcherRefresh);
document.getElementById("watcherPanel").addEventListener("toggle", e => { if (e.target.open) watcherRefresh(); });
document.getElementById("watcherRunOnce").addEventListener("click", async () => {
  const out = document.getElementById("watcherOut").value.trim(), sf = document.getElementById("watcherStreams").value.trim();
  _wSave("padb_watcher_streams", sf);
  const msg = document.getElementById("watcherMsg");
  msg.textContent = "Starting...";
  const r = await fetch("/api/watcher/run-once", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ out, streams_file: sf, include_manual: document.getElementById("watcherManual").checked }) });
  const j = await r.json().catch(() => ({}));
  msg.textContent = j.msg || j.error || ("failed (" + r.status + ")");
});

 "<tr><th>#</th><th>Type</th><th>Name</th><th>Output File</th></tr>";

// Switch the shared "dropped file" panel + Generate form between pod mode and
// padbloader-parquet mode: pod-only controls (convert-site, mode, dates) hide for
// a parquet, which has no pod/extraction step.
function setKindUi(kind) {
  currentKind = kind;
  const parquet = kind === "parquet";
  document.querySelectorAll("#uploadResult .toolbar").forEach(el => el.classList.toggle("hidden", parquet));
  document.getElementById("combineResultsLabel").classList.toggle("hidden", !parquet);
  document.getElementById("viewsRow").classList.toggle("hidden", !parquet);
  document.getElementById("runAfterLabel").classList.toggle("hidden", !parquet);
  for (const id of ["modeSelect", "minDateInput", "maxDateInput"]) {
    const lab = document.getElementById(id).closest("label");
    if (lab) lab.classList.toggle("hidden", parquet);
  }
  document.querySelector("#generate-section h2").textContent =
    parquet ? "2. Generate plot jobs from the parquet" : "2. Generate a job.json";
  document.querySelector("#generateForm button[type=submit]").textContent =
    parquet ? "Generate Plot Jobs" : "Generate Job";
}

// fetch + JSON with a LOUD failure when the server is older than this page (the page
// is re-read from disk, padb_web.py is not): an unknown route returns an HTML 404
// that res.json() chokes on, which used to look like "nothing happens".
async function fetchJson(url, opts) {
  let res, data = null;
  try {
    res = await fetch(url, opts);
    data = await res.json();
  } catch (e) {
    const stale = res && res.status === 404;
    alert(stale
      ? "The padb-tools web server is older than this page (it doesn't have " + url + ").\n\n" +
        "Restart it with Start_web.bat, then press Ctrl+F5."
      : "Request to " + url + " failed: " + e);
    return null;
  }
  return { res, data };
}

// Views a padbloader-parquet job can build.  Everything applicable starts TICKED; the
// environmental views are offered only for multi-step (Room/Hot/Cold) data; "heavy" ones
// get a warning on a wide sweep (e.g. harmonics, 696 x: boxplot 11 min / 70 MB).
const VIEW_DEFS = [
  { name: "scatter", label: "Scatter" },
  { name: "boxplot", label: "Box plot", heavy: true },
  { name: "reference", label: "Reference statistics", heavy: true },
  { name: "summary", label: "Summary" },
  { name: "stat_summary", label: "Statistical summary", heavy: true },
  { name: "distribution", label: "Distribution", env: true },
  { name: "env_coverage", label: "Env coverage", env: true },
];
let parquetRows = [];
const viewTicks = {}; // name -> last user choice (kept across refreshes)

function tickedParquetRows() {
  const on = new Set([...document.querySelectorAll(".algoChk:checked")].map(c => c.dataset.algo));
  return parquetRows.filter(r => on.has(r.algorithm));
}

function refreshViewChoices() {
  const rows = tickedParquetRows();
  const anyWide = rows.some(r => r.wide);
  const hasEnv = rows.some(r => (r.n_steps || 1) > 1);
  const box = document.getElementById("viewsChoices");
  box.innerHTML = "";
  const heavyOn = [];
  for (const v of VIEW_DEFS) {
    if (v.env && !hasEnv) continue;
    const ticked = v.name in viewTicks ? viewTicks[v.name] : true;
    const lab = document.createElement("label");
    lab.className = "inline";
    const badge = (v.heavy && anyWide) ? " ⚠" : "";
    lab.innerHTML = `<input type="checkbox" class="viewChk" value="${v.name}"${ticked ? " checked" : ""}> ${esc(v.label)}${badge} `;
    lab.querySelector("input").addEventListener("change", e => {
      viewTicks[v.name] = e.target.checked;
      refreshViewChoices();
    });
    box.appendChild(lab);
    if (v.heavy && anyWide && ticked) heavyOn.push(v.label);
  }
  const warn = document.getElementById("viewsWarn");
  if (anyWide && heavyOn.length) {
    const nx = Math.max(...rows.filter(r => r.wide).map(r => r.n_x));
    warn.textContent = "⚠ Wide sweep (" + nx.toLocaleString() + " x values): " + heavyOn.join(", ") +
      " can take many minutes and produce very large pages (a 696-x harmonics sweep: Box plot 11 min / 70 MB). " +
      "Untick them to skip.";
    warn.classList.remove("hidden");
  } else {
    warn.classList.add("hidden");
  }
}

function selectedViews() {
  return [...document.querySelectorAll(".viewChk:checked")].map(c => c.value);
}

function esc(t) {
  return String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// A padbloader parquet (direct-from-Oracle extract): list its algorithms; Generate
// writes one PADB-style parquet + one *_v2_job.json per ticked algorithm.
async function uploadParquet(file) {
  const form = new FormData();
  form.append("parquet", file);
  const r = await fetchJson("/api/upload-parquet", { method: "POST", body: form });
  if (!r) return;
  if (!r.res.ok) {
    alert("Upload failed: " + r.data.error);
    return;
  }
  showParquetAnalytics(r.data);
}

// "Use this path" -- a parquet already on disk is used in place (no upload/copy).
async function usePathParquet(path) {
  const r = await fetchJson("/api/upload-parquet", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }),
  });
  if (!r) return;
  if (!r.res.ok) {
    alert("Could not use that file: " + r.data.error);
    return;
  }
  showParquetAnalytics(r.data);
}

function showParquetAnalytics(data) {
  setKindUi("parquet");
  // a padb-tools table has ONE value column, so there is nothing to combine
  document.getElementById("combineResultsLabel").classList.toggle("hidden", data.kind === "padb");
  parquetRows = data.analytics;
  currentPodPath = data.parquet_path;
  document.getElementById("uploadPodName").textContent = data.parquet_name;
  document.querySelector("#analyticsTable thead").innerHTML =
    "<tr><th></th><th>Result stream</th><th>Rows</th><th>Units</th><th>Limits</th><th>Swept x</th><th>Default views</th></tr>";
  const tbody = document.querySelector("#analyticsTable tbody");
  tbody.innerHTML = "";
  for (const a of data.analytics) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td><input type="checkbox" class="algoChk" data-algo="${esc(a.algorithm)}" checked></td>` +
      `<td>${esc(a.algorithm)}</td><td>${a.rows.toLocaleString()}</td><td>${esc(a.units)}</td>` +
      `<td>${a.has_limits ? "yes" : "no"}</td><td>${esc(a.x_col)} (${a.n_x.toLocaleString()} values)</td>` +
      `<td>${a.wide ? "scatter + summary (wide sweep)" : "all"}</td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll(".algoChk").forEach(c => c.addEventListener("change", refreshViewChoices));
  refreshViewChoices();
  document.getElementById("uploadResult").classList.remove("hidden");
  document.getElementById("generate-section").classList.remove("hidden");
}

async function uploadPod(file) {
  if (/\.parquet$/i.test(file.name)) return uploadParquet(file);
  setKindUi("pod");
  document.querySelector("#analyticsTable thead").innerHTML = POD_THEAD;
  const form = new FormData();
  form.append("pod", file);
  const res = await fetch("/api/upload-pod", { method: "POST", body: form });
  const data = await res.json();
  if (!res.ok) {
    alert("Upload failed: " + data.error);
    return;
  }
  currentPodPath = data.pod_path;
  document.getElementById("uploadPodName").textContent = data.pod_name;
  const tbody = document.querySelector("#analyticsTable tbody");
  tbody.innerHTML = "";
  for (const a of data.analytics) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${a.index}</td><td>${a.type ?? ""}</td><td>${a.name ?? ""}</td><td>${a.output_file ?? ""}</td>`;
    tbody.appendChild(tr);
  }
  document.getElementById("uploadResult").classList.remove("hidden");
  document.getElementById("generate-section").classList.remove("hidden");
  const convertLog = document.getElementById("convertPodLog");
  convertLog.textContent = "";
  convertLog.classList.add("hidden");
}

// ---------------------------------------------------------------------------
// Generate job
// ---------------------------------------------------------------------------

document.getElementById("generateForm").addEventListener("submit", async e => {
  e.preventDefault();
  if (!currentPodPath) {
    alert("Drop a .pod (or padbloader .parquet) file first");
    return;
  }
  if (currentKind === "parquet") {
    const algorithms = [...document.querySelectorAll(".algoChk:checked")].map(c => c.dataset.algo);
    if (!algorithms.length) {
      alert("Tick at least one algorithm");
      return;
    }
    const views = selectedViews();
    if (!views.length) {
      alert("Tick at least one plot to generate");
      return;
    }
    const pr = await fetchJson("/api/generate-parquet-jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parquet_path: currentPodPath,
        algorithms,
        combine_results: document.getElementById("combineResultsChk").checked,
        views,
        module: document.getElementById("moduleInput").value,
        publish_to: document.getElementById("sharePathInput").value,
        force: document.getElementById("forceCheckbox").checked,
      }),
    });
    if (!pr) return;
    if (!pr.res.ok) {
      alert("Generate failed: " + pr.data.error);
      return;
    }
    renderGenerated(pr.data);
    // Queue the freshly written jobs so they show in Running Jobs (live log / cancel)
    const runAfter = document.getElementById("runAfterChk");
    const fresh = pr.data.jobs.filter(j => j.status === "ok" && !j.giant).map(j => j.path);
    const giant = pr.data.jobs.filter(j => j.giant);
    if (giant.length) {
      alert(giant.length + " of the generated job(s) hold more data than can be built into HTML pages on this machine right now, "
        + "so they were NOT queued:\n\n" + giant.map(j => j.name).join("\n")
        + "\n\nThe data is converted -- open it in the parquet viewer (the command is in the output below), "
        + "or generate a slice (padb_analyze --x-range / --bands auto).");
    }
    if (runAfter && runAfter.checked && fresh.length) {
      const rr = await fetchJson("/api/execute-job", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths: fresh, dry_run: false, publish: false }),
      });
      if (rr && rr.res.ok) {
        for (const jobId of rr.data.job_ids) startPolling(jobId);
        if (rr.data.job_ids.length) scrollJobStatusIntoView(rr.data.job_ids[0]);
      } else if (rr) {
        alert("Jobs were written but could not be queued: " + rr.data.error);
      }
    }
    return;
  }
  const body = {
    pod_path: currentPodPath,
    mode: document.getElementById("modeSelect").value,
    module: document.getElementById("moduleInput").value,
    publish_to: document.getElementById("sharePathInput").value,
    min_date: document.getElementById("minDateInput").value,
    max_date: document.getElementById("maxDateInput").value,
    force: document.getElementById("forceCheckbox").checked,
  };
  const res = await fetch("/api/generate-job", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    alert("Generate failed: " + data.error);
    return;
  }
  renderGenerated(data);
});

function renderGenerated(data) {
  document.getElementById("generateStdout").textContent = data.stdout + (data.stderr || "");
  const container = document.getElementById("generatedJobs");
  container.innerHTML = "";
  for (const job of data.jobs) {
    const details = document.createElement("details");
    details.className = "job-file";
    const summary = document.createElement("summary");
    summary.textContent = job.name;
    const pre = document.createElement("pre");
    pre.textContent = job.content;
    details.appendChild(summary);
    details.appendChild(pre);
    container.appendChild(details);
  }
  document.getElementById("generateResult").classList.remove("hidden");
  loadJobs();
}

// ---------------------------------------------------------------------------
// Compare mode
// ---------------------------------------------------------------------------

let comparePreviewOk = false; // true once a preview call has run against the current selection
let allCompareCsvs = []; // cached /api/compare-csvs list, re-filtered client-side by name

async function loadCompareCsvs() {
  const res = await fetch("/api/compare-csvs");
  const data = await res.json();
  allCompareCsvs = data.csvs || [];
  renderCompareCsvOptions();
}

// Rebuild both site dropdowns from the cached list, honoring the name filter.
// Mirrors the Jobs table's name filter (case-insensitive substring on the
// visible label), just applied to <option>s instead of table rows.
function renderCompareCsvOptions() {
  const filter = document.getElementById("compareCsvNameFilter").value.trim().toLowerCase();
  const visible = filter
    ? allCompareCsvs.filter(c => c.label.toLowerCase().includes(filter))
    : allCompareCsvs;
  let selectionChanged = false;
  for (const id of ["compareCsvA", "compareCsvB"]) {
    const sel = document.getElementById(id);
    const prevValue = sel.value; // preserve selection across a refresh/filter, if it still exists
    sel.innerHTML = "";
    for (const c of visible) {
      const opt = document.createElement("option");
      opt.value = c.path;
      opt.textContent = c.label;
      sel.appendChild(opt);
    }
    if (prevValue && [...sel.options].some(o => o.value === prevValue)) sel.value = prevValue;
    else if (prevValue) selectionChanged = true; // the previously-picked CSV got filtered/refreshed away
  }
  // Rebuilding options programmatically doesn't fire "change", so a selection
  // that silently shifted (its CSV filtered out or removed on refresh) would
  // otherwise leave a stale "Check compatibility" result marked valid.
  if (selectionChanged) invalidateComparePreview();
}
document.getElementById("compareCsvNameFilter").addEventListener("input", renderCompareCsvOptions);
document.getElementById("compareRefreshBtn").addEventListener("click", loadCompareCsvs);

function updateComparePrimarySiteOptions() {
  const nameA = document.getElementById("compareSiteAName").value.trim();
  const nameB = document.getElementById("compareSiteBName").value.trim();
  const sel = document.getElementById("comparePrimarySite");
  const prev = sel.value;
  sel.innerHTML = "";
  for (const name of [nameA, nameB]) {
    if (!name) continue;
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  }
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
}
["compareSiteAName", "compareSiteBName"].forEach(id =>
  document.getElementById(id).addEventListener("input", updateComparePrimarySiteOptions)
);

function invalidateComparePreview() {
  comparePreviewOk = false;
  document.getElementById("compareCreateRunBtn").disabled = true;
  updateCompareCreateHint();
}
["compareCsvA", "compareCsvB", "compareSiteAName", "compareSiteBName"].forEach(id =>
  document.getElementById(id).addEventListener("change", invalidateComparePreview)
);
document.getElementById("compareOverrideChk")?.addEventListener("change", refreshCompareCreateEnabled);

function refreshCompareCreateEnabled() {
  const btn = document.getElementById("compareCreateRunBtn");
  const blocked = !document.getElementById("compareBlock").classList.contains("hidden");
  const overrideChk = document.getElementById("compareOverrideChk");
  btn.disabled = !comparePreviewOk || (blocked && !(overrideChk && overrideChk.checked));
  updateCompareCreateHint();
}

// Spell out WHY Create job is disabled, since a greyed button alone doesn't
// say what to do about it. Shown only while disabled; the message depends on
// whether the gate is "no compatibility check yet" vs. "a hard block is
// active" (the block banner above already explains the latter in detail).
function updateCompareCreateHint() {
  const hint = document.getElementById("compareCreateHint");
  if (!hint) return;
  const btn = document.getElementById("compareCreateRunBtn");
  if (!btn.disabled) { hint.classList.add("hidden"); return; }
  hint.classList.remove("hidden");
  hint.textContent = comparePreviewOk
    ? "Resolve the block above (or check Override) to enable Create job."
    : "← Run “Check compatibility” first to enable Create job.";
}

function fmtCompareStat(s) {
  if (!s || s.rows === 0) return "0 usable rows";
  const freq = `${s.freq_min.toFixed(1)}–${s.freq_max.toFixed(1)} MHz`;
  const temps = s.temps.join(", ") || "—";
  const dut = s.n_dut == null ? "unknown" : s.n_dut;
  return `${s.rows.toLocaleString()} rows | ${freq} | Temps: ${temps} | DUTs: ${dut}`;
}

document.getElementById("compareCheckBtn").addEventListener("click", async () => {
  const csv_a = document.getElementById("compareCsvA").value;
  const csv_b = document.getElementById("compareCsvB").value;
  if (!csv_a || !csv_b) {
    alert("Pick a CSV for both Site A and Site B");
    return;
  }
  // Real reported bug: "Check compatibility doesn't update review data" --
  // the request itself was working, but reading two full CSVs server-side
  // (some of this tool's real CSVs are 700k+ rows) can take long enough,
  // with zero visual feedback, that it looks identical to "nothing
  // happened" rather than "still working". Busy-state on the button is the
  // fix -- it doesn't make the read faster, it just stops a slow response
  // from looking indistinguishable from a broken one.
  const btn = document.getElementById("compareCheckBtn");
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Checking...";
  try {
    const res = await fetch("/api/compare-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ csv_a, csv_b }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Compare check failed: " + data.error);
      return;
    }
    document.getElementById("compareResult").classList.remove("hidden");

    const blockEl = document.getElementById("compareBlock");
    if (data.blocked) {
      blockEl.textContent = data.block_reason;
      blockEl.classList.remove("hidden");
      document.getElementById("compareOverrideWrap").classList.remove("hidden");
      document.getElementById("compareOverrideChk").checked = false;
    } else {
      blockEl.classList.add("hidden");
      document.getElementById("compareOverrideWrap").classList.add("hidden");
    }

    const warnEl = document.getElementById("compareWarnings");
    if (data.warnings && data.warnings.length) {
      warnEl.innerHTML = "<b>Coverage/compatibility warnings:</b><ul>" +
        data.warnings.map(w => `<li>${w}</li>`).join("") + "</ul>";
      warnEl.classList.remove("hidden");
    } else {
      warnEl.classList.add("hidden");
    }

    const statsTable = document.getElementById("compareStatsTable");
    statsTable.querySelector("tbody").innerHTML =
      `<tr><td>Units</td><td>${(data.units.a || []).join(", ") || "—"}</td><td>${(data.units.b || []).join(", ") || "—"}</td></tr>` +
      `<tr><td>Summary</td><td>${fmtCompareStat(data.stats.a)}</td><td>${fmtCompareStat(data.stats.b)}</td></tr>`;
    statsTable.classList.remove("hidden");

    comparePreviewOk = true;
    refreshCompareCreateEnabled();
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
});

document.getElementById("compareForm").addEventListener("submit", async e => {
  e.preventDefault();
  if (!comparePreviewOk) {
    alert("Run \"Check compatibility\" first");
    return;
  }
  const body = {
    csv_a: document.getElementById("compareCsvA").value,
    site_a: document.getElementById("compareSiteAName").value.trim(),
    csv_b: document.getElementById("compareCsvB").value,
    site_b: document.getElementById("compareSiteBName").value.trim(),
    primary_site: document.getElementById("comparePrimarySite").value,
    description: document.getElementById("compareDescription").value.trim(),
    override: document.getElementById("compareOverrideChk")?.checked || false,
  };
  // Real reported bug: "Create and Run does not work if another compare job
  // is already running" -- confirmed by direct testing that queuing itself
  // works fine (the new job correctly sits in "queued" state). The actual
  // gap is the same as Check compatibility's: compare-create re-runs the
  // same potentially-slow compatibility check server-side before writing
  // the job.json, with no visible sign the click did anything until it
  // resolves -- indistinguishable from "doesn't work" when it's just slow.
  const btn = document.getElementById("compareCreateRunBtn");
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Creating...";
  let created = false;
  try {
    // Create the compare job.json only -- do NOT auto-run it. Running from the
    // standard jobs table is the single, consistent run path with all its options
    // (Publish to share, dry-run); auto-running here silently
    // bypassed them (e.g. never published). David's call 2026-09-17.
    const res = await fetch("/api/compare-create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Compare job creation failed: " + data.error);
      return;
    }
    if (data.warnings && data.warnings.length) {
      console.log("Compare job created with warnings:", data.warnings);
    }
    created = true;
    await loadJobs();
    // Surface it: filter the jobs table to the new job so it's easy to find + run.
    const nameFilterEl = document.getElementById("nameFilter");
    const stem = (data.path || "").split(/[\\/]/).pop().replace(/_job\.json$/, "");
    if (nameFilterEl && stem) { nameFilterEl.value = stem; nameFilterEl.dispatchEvent(new Event("input")); }
    const hint = document.getElementById("compareCreateHint");
    if (hint) {
      hint.classList.remove("hidden");
      hint.innerHTML = "&#10003; Created <b>" + (stem || "compare job") +
        "</b>. Select it in the Jobs table below and click <b>Run Selected</b> " +
        "(choose Publish / PDF options there).";
    }
    const jobsAnchor = document.getElementById("jobsTable") || document.getElementById("refreshJobsBtn");
    if (jobsAnchor && jobsAnchor.scrollIntoView) jobsAnchor.scrollIntoView({ behavior: "smooth", block: "start" });
  } finally {
    btn.textContent = origText;
    // On success, leave the button enabled + the success hint visible (do NOT call
    // refreshCompareCreateEnabled, which would hide the hint). On failure, restore
    // the normal gating hint.
    if (created) { btn.disabled = false; } else { refreshCompareCreateEnabled(); }
  }
});

loadCompareCsvs();
// Real reported bug: the CSV list was only ever fetched once, at initial page
// load -- a job that finished (e.g. a fresh extraction) after the page
// loaded but before the panel was opened never appeared in the dropdown
// until a full browser reload. <details> fires a native "toggle" event on
// every open/close; refresh on open so the list is always current.
const compareDetailsEl = document.getElementById("compareDetails");
if (compareDetailsEl) {
  compareDetailsEl.addEventListener("toggle", () => {
    if (compareDetailsEl.open) loadCompareCsvs();
  });
}

// ---------------------------------------------------------------------------
// Jobs list / execute
// ---------------------------------------------------------------------------

async function loadJobs() {
  const res = await fetch("/api/jobs");
  const data = await res.json();
  allJobs = data.jobs;
  await loadJobLists();
  renderJobsTable();
}

// ---- jobs table: find / chips / grouping (display only; never touches job files) ----
const JOB_CHIPS = {
  failed:    j => j.last_status === "failed",
  never:     j => !j.last_run,
  scheduled: j => !!j.scheduled,
  today:     j => !!j.last_run && j.last_run.slice(0, 10) === _todayStr(),
  results:   j => !!j.index_url,
};
function _todayStr() {
  const d = new Date(), z = n => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + z(d.getMonth() + 1) + "-" + z(d.getDate());
}
function _lsGet(k, dflt) { try { const v = localStorage.getItem(k); return v === null ? dflt : v; } catch (e) { return dflt; } }
function _lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
let activeChips = new Set((_lsGet("padb_web_job_chips", "") || "").split(",").filter(c => JOB_CHIPS[c]));
let collapsedGroups = new Set();
try { collapsedGroups = new Set(JSON.parse(_lsGet("padb_web_job_collapsed", "[]"))); } catch (e) {}

// Every word typed must appear in the name, description, pod or family (case-insensitive).
function jobMatchesFind(job, q) {
  if (!q) return true;
  const hay = (job.name + " " + (job.description || "") + " " + (job.pod || "") + " " + (job.family || "")).toLowerCase();
  return q.split(/\s+/).every(w => hay.includes(w));
}

function jobGroupKey(job, by) {
  if (by === "family") return job.family || "(unknown)";
  if (by === "kind") return job.kind === "run" ? "Run jobs (PADB-R extraction)" : "Plot jobs (HTML from a CSV / parquet)";
  if (by === "mode") return job.mode || "(none)";
  if (by === "schedule") return job.scheduled ? "Scheduled" : "Not scheduled";
  if (by === "lastrun") {
    if (!job.last_run) return "Never run";
    const d = job.last_run.slice(0, 10);
    if (d === _todayStr()) return "Today";
    const age = (Date.now() - new Date(d + "T00:00:00").getTime()) / 86400000;
    return age <= 7 ? "Last 7 days" : "Older";
  }
  return "";
}
const GROUP_ORDER = { lastrun: ["Today", "Last 7 days", "Older", "Never run"], schedule: ["Scheduled", "Not scheduled"] };

function _escHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function buildJobRow(job, groupId, visible) {
  const tr = document.createElement("tr");
  if (groupId) tr.dataset.groupOf = groupId;
  if (!visible) tr.style.display = "none";
  const td = document.createElement("td");
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.value = job.path;
  cb.dataset.kind = job.kind;
  cb.dataset.compare = job.is_compare ? "1" : "";
  td.appendChild(cb);
  tr.appendChild(td);
  const resultsCell = job.index_url
    ? `<a href="${job.index_url}" target="_blank" title="${job.index_path}">Open</a>`
    : "";
  const scheduledCell = job.scheduled
    ? `<span class="sched-yes">&#10003; ${job.schedule_summary || ""}</span>`
    : "";
  tr.innerHTML += `<td class="wrap">${job.name}</td><td>${job.mode}</td>` +
    `<td class="wrap">${job.pod}</td><td class="wrap">${job.description}</td>` +
    `<td class="wrap">${scheduledCell}</td><td>${job.last_run || ""}</td><td>${resultsCell}</td>`;
  const editBtn = document.createElement("button");
  editBtn.textContent = "Edit";
  editBtn.title = "Edit this job.json (extraction + analysis options). Changes are reviewed as a diff before saving; a .bak copy is kept.";
  editBtn.addEventListener("click", () => openEditJob(job.path));
  editBtn.style.marginRight = "6px";
  editBtn.style.display = "none"; // revealed only while this row is selected
  editBtn.className = "edit-job-btn";
  const nameCell = tr.children[1];
  nameCell.insertBefore(editBtn, nameCell.firstChild);
  return tr;
}

function renderJobsTable() {
  const modeFilter = document.getElementById("modeFilter").value;
  const kindFilter = document.getElementById("kindFilter").value;
  const nameFilter = document.getElementById("nameFilter").value.trim().toLowerCase();
  const listSel = document.getElementById("listFilter").value;
  const groupBy = (document.getElementById("groupBy") || {}).value || "none";
  const listSet = listSel !== "all" && jobLists[listSel] ? new Set(jobLists[listSel]) : null;
  const tbody = document.querySelector("#jobsTable tbody");
  tbody.innerHTML = "";
  // 1. the jobs that pass every filter (list, mode, kind, find, chips)
  const shown = allJobs.filter(job =>
    !(listSet && !listSet.has(job.name)) &&
    !(modeFilter !== "all" && job.mode !== modeFilter) &&
    !(kindFilter !== "all" && job.kind !== kindFilter) &&
    jobMatchesFind(job, nameFilter) &&
    [...activeChips].every(c => JOB_CHIPS[c](job)));
  for (const b of document.querySelectorAll("#jobChips button.chip[data-chip]")) {
    const c = b.dataset.chip;
    b.textContent = b.textContent.replace(/ \(\d+\)$/, "") + " (" + allJobs.filter(JOB_CHIPS[c]).length + ")";
    b.classList.toggle("on", activeChips.has(c));
  }
  const cnt = document.getElementById("jobCount");
  if (cnt) cnt.textContent = "Showing " + shown.length + " of " + allJobs.length + " jobs";
  // 2. order into groups
  const groups = new Map();
  for (const job of shown) {
    const k = groupBy === "none" ? "" : jobGroupKey(job, groupBy);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(job);
  }
  const keys = [...groups.keys()];
  const fixed = GROUP_ORDER[groupBy];
  keys.sort((a, b) => fixed ? fixed.indexOf(a) - fixed.indexOf(b) : a.toLowerCase().localeCompare(b.toLowerCase()));
  for (const gk of keys) {
    const members = groups.get(gk);
    if (groupBy === "none") {
      for (const job of members) tbody.appendChild(buildJobRow(job, "", true));
      continue;
    }
    const hid = groupBy + "::" + gk;
    const open = !collapsedGroups.has(hid);
    const htr = document.createElement("tr");
    htr.className = "job-group-row";
    htr.dataset.group = hid;
    const hd = document.createElement("td");
    hd.colSpan = 8;
    const nSched = members.filter(j => j.scheduled).length;
    hd.innerHTML = '<span class="g-caret">' + (open ? "&#9662;" : "&#9656;") + "</span> " + _escHtml(gk) +
      '<span class="g-meta">' + members.length + " job" + (members.length === 1 ? "" : "s") +
      (nSched ? ", " + nSched + " scheduled" : "") + "</span>" +
      '<button type="button" class="secondary g-check" title="Check every job in this group (or uncheck them all if they were all checked).">check group</button>';
    htr.appendChild(hd);
    htr.addEventListener("click", (e) => {
      if (e.target && e.target.classList.contains("g-check")) return;
      if (collapsedGroups.has(hid)) collapsedGroups.delete(hid); else collapsedGroups.add(hid);
      _lsSet("padb_web_job_collapsed", JSON.stringify([...collapsedGroups]));
      renderJobsTable();
    });
    hd.querySelector(".g-check").addEventListener("click", () => {
      const boxes = [...tbody.querySelectorAll("tr")].filter(r => r.dataset.groupOf === hid).map(r => r.querySelector("input[type=checkbox]"));
      const all = boxes.length && boxes.every(b => b.checked);
      boxes.forEach(b => { b.checked = !all; });
      updatePdfSiteEnabled();
    });
    tbody.appendChild(htr);
    // rows of a collapsed group are still built (just hidden) so "Select Filtered" and the checked state cover them
    for (const job of members) tbody.appendChild(buildJobRow(job, hid, open));
  }
  applyTooltipPref(); // Results link's title is only known once rendered here
  updatePdfSiteEnabled();
}

// The PDF "site" scope dropdown only does anything on compare jobs (Site
// dimension); grey it out unless a compare job is in the current selection.
// Edit buttons are shown only on selected rows. Called from every path that
// changes a selection (row change, Select All Runnable/Filtered, re-render) --
// bulk selects set .checked programmatically, which fires no change event.
function syncEditButtons() {
  for (const tr of document.querySelectorAll("#jobsTable tbody tr")) {
    const cb = tr.querySelector('input[type="checkbox"]');
    const btn = tr.querySelector(".edit-job-btn");
    if (btn) btn.style.display = cb && cb.checked ? "" : "none";
  }
}

function updatePdfSiteEnabled() {
  syncEditButtons();
  const sel = document.getElementById("pdfFilterSite");
  if (!sel) return;
  const anyCompare = [...document.querySelectorAll("#jobsTable tbody input[type=checkbox]:checked")]
    .some(cb => cb.dataset.compare === "1");
  sel.disabled = !anyCompare;
  sel.title = anyCompare
    ? "Compare jobs only: which site the auto-filter cleans in the report (Reference/Onboarding/Both)."
    : "Compare jobs only — select a compare job to enable. Ignored for non-compare jobs.";
}

function selectedJobPaths() {
  return [...document.querySelectorAll("#jobsTable tbody input[type=checkbox]:checked")]
    .map(cb => cb.value);
}

document.getElementById("refreshJobsBtn").addEventListener("click", loadJobs);
document.getElementById("modeFilter").addEventListener("change", renderJobsTable);
document.getElementById("kindFilter").addEventListener("change", renderJobsTable);
document.getElementById("nameFilter").addEventListener("input", renderJobsTable);
{
  const gb = document.getElementById("groupBy");
  const saved = _lsGet("padb_web_job_group", "none");
  gb.value = saved;
  if (gb.value !== saved) gb.value = "none";
  gb.addEventListener("change", () => { _lsSet("padb_web_job_group", gb.value); renderJobsTable(); });
}
for (const b of document.querySelectorAll("#jobChips button.chip[data-chip]")) {
  b.addEventListener("click", () => {
    const c = b.dataset.chip;
    if (activeChips.has(c)) activeChips.delete(c); else activeChips.add(c);
    _lsSet("padb_web_job_chips", [...activeChips].join(","));
    renderJobsTable();
  });
}
document.getElementById("chipClear").addEventListener("click", () => {
  activeChips.clear();
  _lsSet("padb_web_job_chips", "");
  document.getElementById("nameFilter").value = "";
  renderJobsTable();
});

document.getElementById("selectAllRunnableBtn").addEventListener("click", () => {
  const boxes = document.querySelectorAll("#jobsTable tbody input[type=checkbox]");
  for (const cb of boxes) cb.checked = cb.dataset.kind === "run";
  updatePdfSiteEnabled();
});

// Row checkboxes are recreated on every renderJobsTable (innerHTML +=), so use
// delegation on the table to keep the PDF site dropdown's enabled state in sync.
document.getElementById("jobsTable").addEventListener("change", (e) => {
  if (e.target && e.target.matches('input[type="checkbox"]')) updatePdfSiteEnabled();
});

// Select every job matching the current Mode/Kind/Name filters. renderJobsTable
// only appends rows that pass all three filters, so the rendered checkboxes ARE
// exactly the filtered set (including any scrolled out of the 320px view box) --
// checking them all is robust by construction, no re-derivation of the filters
// needed here.
document.getElementById("selectFilteredBtn").addEventListener("click", () => {
  const boxes = document.querySelectorAll("#jobsTable tbody input[type=checkbox]");
  for (const cb of boxes) cb.checked = true;
  updatePdfSiteEnabled();
});

document.getElementById("runSelectedBtn").addEventListener("click", async () => {
  const paths = selectedJobPaths();
  if (!paths.length) {
    alert("Select at least one job to run");
    return;
  }
  // Disabled for the round trip only -- this guards against a rapid
  // double-click queuing the same job twice (the server also dedups by path
  // against anything already queued/running, since a click well after this
  // button re-enables -- e.g. an impatient re-click while the job is still
  // running -- wouldn't be caught by this alone).
  const btn = document.getElementById("runSelectedBtn");
  const publishEl = document.getElementById("publishCheckbox");
  const doPublish = publishEl ? publishEl.checked : false;
  // PDF report is on-demand only (Generate PDF report button below); the
  // per-run "Build PDF report" checkbox was dropped -- it forced the
  // minutes-long PDF cost onto every run. Server still accepts pdf_report
  // for API/back-compat, defaulting off.
  btn.disabled = true;
  try {
    const res = await fetch("/api/execute-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, dry_run: false, publish: doPublish }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Execute failed: " + data.error);
      return;
    }
    for (const jobId of data.job_ids) startPolling(jobId);
    if (data.job_ids.length) scrollJobStatusIntoView(data.job_ids[0]);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("generatePdfBtn").addEventListener("click", async () => {
  const paths = selectedJobPaths();
  if (!paths.length) {
    alert("Select at least one job to generate a PDF report for");
    return;
  }
  const btn = document.getElementById("generatePdfBtn");
  const faEl = document.getElementById("pdfFilterAwareCheckbox");
  const applyFilter = faEl ? faEl.checked : false;
  const siteEl = document.getElementById("pdfFilterSite");
  const filterSite = siteEl ? siteEl.value : "primary";
  btn.disabled = true;
  try {
    const res = await fetch("/api/generate-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, apply_filter: applyFilter, filter_site: filterSite }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Generate PDF failed: " + data.error);
      return;
    }
    for (const jobId of data.job_ids) startPolling(jobId);
    if (data.job_ids.length) scrollJobStatusIntoView(data.job_ids[0]);
  } finally {
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Test-point reduce (PROTOTYPE) -- recommend redundant swept frequencies to trim
// ---------------------------------------------------------------------------
document.getElementById("reduceBtn").addEventListener("click", async () => {
  const paths = selectedJobPaths();
  if (!paths.length) {
    alert("Select at least one job (Run or Plot) whose extracted CSV to analyse");
    return;
  }
  const btn = document.getElementById("reduceBtn");
  const pctEl = document.getElementById("reducePct");
  const targetPct = pctEl ? parseFloat(pctEl.value) : 25;
  const mode = document.getElementById("reduceMode")?.value || "target";
  btn.disabled = true;
  try {
    const res = await fetch("/api/generate-reduce", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, target_pct: targetPct, mode }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Test-point reduce failed: " + data.error);
      return;
    }
    for (const jobId of data.job_ids) startPolling(jobId);
    if (data.job_ids.length) scrollJobStatusIntoView(data.job_ids[0]);
  } finally {
    btn.disabled = false;
  }
});

// Auto (adaptive) mode ignores the reduction %, so grey the box out to make that
// obvious. Used by the standalone Test-point Reduce control (the compare-embedded
// reduce control was removed -- reduction on the merged CSV is run from the
// standard job-menu Test-point Reduce action, David 2026-09-23).
function _syncReduceModeUi(modeId, pctId) {
  const m = document.getElementById(modeId), p = document.getElementById(pctId);
  if (!m || !p) return;
  const adaptive = m.value === "adaptive";
  p.disabled = adaptive;
  p.style.opacity = adaptive ? "0.45" : "";
  p.title = adaptive ? "Ignored in Auto (adaptive) mode -- the tool chooses the reduction from the data."
                     : "";
}
if (["reduceMode", "reducePct"].every(id => document.getElementById(id))) {
  document.getElementById("reduceMode").addEventListener("change",
    () => _syncReduceModeUi("reduceMode", "reducePct"));
  _syncReduceModeUi("reduceMode", "reducePct");
}

// ---------------------------------------------------------------------------
// Scheduler add/remove
// ---------------------------------------------------------------------------

function updateDayCheckboxVisibility() {
  const isWeekly = document.getElementById("scheduleType").value === "WEEKLY";
  document.getElementById("dayCheckboxes").style.display = isWeekly ? "" : "none";
}
document.getElementById("scheduleType").addEventListener("change", updateDayCheckboxVisibility);
updateDayCheckboxVisibility();

function reportScheduleFailures(results, verb) {
  const failures = results.filter(r => !r.ok);
  if (failures.length) {
    alert(`${failures.length} job(s) failed to ${verb}:\n` +
      failures.map(f => `${f.task_name}: ${f.error}`).join("\n"));
  }
}

document.getElementById("scheduleSelectedBtn").addEventListener("click", async () => {
  const paths = selectedJobPaths();
  if (!paths.length) {
    alert("Select at least one job to schedule");
    return;
  }
  const scheduleType = document.getElementById("scheduleType").value;
  const startTime = document.getElementById("scheduleTime").value;
  if (!startTime) {
    alert("Pick a start time");
    return;
  }
  const days = scheduleType === "WEEKLY"
    ? [...document.querySelectorAll("#dayCheckboxes input:checked")].map(cb => cb.value)
    : [];
  if (scheduleType === "WEEKLY" && !days.length) {
    alert("Pick at least one day for a weekly schedule");
    return;
  }
  const res = await fetch("/api/schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths, schedule_type: scheduleType, days, start_time: startTime }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert("Schedule failed: " + data.error);
    return;
  }
  reportScheduleFailures(data.results, "schedule");
  loadJobs();
});

document.getElementById("unscheduleSelectedBtn").addEventListener("click", async () => {
  const paths = selectedJobPaths();
  if (!paths.length) {
    alert("Select at least one job to unschedule");
    return;
  }
  const res = await fetch("/api/unschedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert("Unschedule failed: " + data.error);
    return;
  }
  reportScheduleFailures(data.results, "unschedule");
  loadJobs();
});

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

document.getElementById("deleteSelectedBtn").addEventListener("click", async () => {
  const paths = selectedJobPaths();
  if (!paths.length) {
    alert("Select at least one job to delete");
    return;
  }
  const deleteData = document.getElementById("deleteDataCheckbox").checked;
  const msg = `Delete ${paths.length} job.json file(s)` +
    (deleteData ? " AND their local results data" : "") +
    `?\n\nThis does not remove the source .pod file or any already-published ` +
    `copy on the network share.\n\n` + paths.join("\n");
  if (!confirm(msg)) return;
  const res = await fetch("/api/delete-job", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths, delete_data: deleteData }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert("Delete failed: " + data.error);
    return;
  }
  const failures = data.results.filter(r => !r.ok);
  const notes = data.results.filter(r => r.note);
  let out = "";
  if (failures.length) out += `Failed:\n` + failures.map(f => `${f.path}: ${f.error}`).join("\n") + "\n";
  if (notes.length) out += `Notes:\n` + notes.map(n => `${n.path}: ${n.note}`).join("\n");
  if (out) alert(out);
  loadJobs();
});

// ---------------------------------------------------------------------------
// Site conversion
// ---------------------------------------------------------------------------

async function loadSites() {
  const res = await fetch("/api/sites");
  const data = await res.json();
  for (const selId of ["convertPodSite", "convertJobSite"]) {
    const sel = document.getElementById(selId);
    sel.innerHTML = "";
    // Neutral placeholder so no site is pre-selected -- conversion is OPTIONAL, and
    // an auto-selected site made it look like a required/pending choice.
    const ph = document.createElement("option");
    ph.value = ""; ph.textContent = "— select site —";
    sel.appendChild(ph);
    for (const name of Object.keys(data.sites)) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    }
  }
}

document.getElementById("convertPodBtn").addEventListener("click", async () => {
  if (!currentPodPath) {
    alert("Drop a .pod file first");
    return;
  }
  const targetSite = document.getElementById("convertPodSite").value;
  if (!targetSite) { alert("Pick a target site to convert to (optional step -- skip it if you don't need a site conversion)."); return; }
  const force = document.getElementById("convertPodForce").checked;
  const res = await fetch("/api/convert-pod", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pod_path: currentPodPath, target_site: targetSite, force }),
  });
  const data = await res.json();
  const logEl = document.getElementById("convertPodLog");
  logEl.classList.remove("hidden");
  if (!res.ok) {
    logEl.textContent = "ERROR: " + data.error + (data.log ? "\n\n" + data.log : "");
    return;
  }
  logEl.textContent = `Wrote ${data.dest_name}\n\n` + data.log;
});

document.getElementById("convertSelectedBtn").addEventListener("click", async () => {
  const paths = selectedJobPaths();
  if (!paths.length) {
    alert("Select at least one job to convert");
    return;
  }
  const targetSite = document.getElementById("convertJobSite").value;
  if (!targetSite) { alert("Pick a target site to convert to (optional step -- skip it if you don't need a site conversion)."); return; }
  const force = document.getElementById("convertJobForce").checked;
  const res = await fetch("/api/convert-job", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths, target_site: targetSite, force }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert("Convert failed: " + data.error);
    return;
  }
  const failures = data.results.filter(r => !r.ok);
  const successes = data.results.filter(r => r.ok);
  let msg = "";
  if (successes.length) msg += `Converted:\n` + successes.map(s => s.dest_name).join("\n") + "\n";
  if (failures.length) msg += `Failed:\n` + failures.map(f => `${f.path}: ${f.error}`).join("\n");
  if (msg) alert(msg);
  loadJobs();
});

// ---------------------------------------------------------------------------
// Status polling
// ---------------------------------------------------------------------------

function ensureStatusCard(jobId) {
  let card = document.getElementById("status-" + jobId);
  if (!card) {
    card = document.createElement("div");
    card.className = "status-card";
    card.id = "status-" + jobId;
    card.innerHTML = `<h4></h4><span class="badge"></span><span class="elapsed"></span>` +
      `<span class="results-link"></span><span class="log-link"></span>` +
      `<button class="abort-btn" title="Kills the running process immediately -- a PADB-R.exe extraction in progress is killed too, not gracefully stopped.">Abort</button>` +
      `<pre class="logtail" title="Tail of this job's own console output, updated every ~2s while queued/running."></pre>`;
    card.querySelector(".abort-btn").addEventListener("click", () => abortJob(jobId));
    document.getElementById("statusPanels").prepend(card);
    applyTooltipPref(); // abort-btn/logtail titles are only known once created here
  }
  return card;
}

function startPolling(jobId) {
  if (activePolls.has(jobId)) return;
  activePolls.add(jobId);
  ensureStatusCard(jobId);
  poll(jobId);
}

// The Running Jobs panel (section 5) sits well below both the compare
// "Run Selected" (section 3) and, less dramatically, "Run Selected"
// (section 4) -- so a freshly-queued job's status card is created off-screen
// and the queue/run looks like it did nothing. Scroll the new card into view
// so the feedback is visible from wherever the triggering button was clicked.
function scrollJobStatusIntoView(jobId) {
  const target = document.getElementById("status-" + jobId)
    || document.getElementById("status-section");
  if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function abortJob(jobId) {
  if (!confirm("Abort this job? A running PADB-R.exe extraction will be killed immediately.")) return;
  const res = await fetch(`/api/job-abort/${jobId}`, { method: "POST" });
  const data = await res.json();
  if (!res.ok) alert("Abort failed: " + data.error);
}

document.getElementById("cleanupPadbBtn").addEventListener("click", async () => {
  const res = await fetch("/api/orphaned-padb");
  const data = await res.json();
  if (!res.ok) {
    alert("Could not check for orphaned PADB-R processes: " + (data.error || res.status));
    return;
  }
  if (!data.processes.length) {
    alert("Nothing to clean up (idle GUI windows opened by hand don't count, and no already-orphaned R-Host.exe processes were found).");
    return;
  }
  const padbR = data.processes.filter(p => p.kind === "padb_r");
  const rHost = data.processes.filter(p => p.kind === "r_host");
  let msg = "";
  if (padbR.length) {
    msg += `${padbR.length} batch PADB-R.exe process(es):\n` +
      padbR.map(p => `PID ${p.pid}: ${p.cmdline}`).join("\n") + "\n\n";
  }
  if (rHost.length) {
    // These have no living PADB-R.exe parent to cascade a kill from --
    // taskkill /T on the PADB-R.exe entries above can't reach them, so
    // they're discovered and killed directly here instead.
    msg += `${rHost.length} already-orphaned R-Host.exe process(es) (parent already gone):\n` +
      rHost.map(p => `PID ${p.pid}`).join("\n") + "\n\n";
  }
  if (data.has_running_job_here) {
    msg += `WARNING: this browser session currently has a job marked as running. ` +
      `If one of the PADB-R.exe PIDs above belongs to it, killing it will fail that job -- check the ` +
      `status panel below before proceeding if you're not sure.\n\n`;
  }
  msg += `Kill all of these now?` +
    (padbR.length ? ` (also kills each PADB-R.exe's own currently-attached R-Host.exe children)` : "") +
    `\n\nNote: if any process can't be killed at normal privilege (common for ` +
    `already-orphaned R-Host.exe), Windows may show one permission (UAC) prompt to elevate the kill.`;
  if (!confirm(msg)) return;
  const killRes = await fetch("/api/orphaned-padb/kill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pids: data.processes.map(p => p.pid) }),
  });
  const killData = await killRes.json();
  if (!killRes.ok) {
    alert("Cleanup failed: " + killData.error);
    return;
  }
  const failed = killData.results.filter(r => !r.ok);
  alert(failed.length
    ? `Killed ${killData.results.length - failed.length} of ${killData.results.length}. Failed:\n` +
      failed.map(f => `PID ${f.pid}: ${f.output}`).join("\n")
    : `Killed all ${killData.results.length} process(es).`);
});

async function poll(jobId) {
  const res = await fetch(`/api/job-status/${jobId}`);
  const data = await res.json();
  if (!res.ok) {
    activePolls.delete(jobId);
    return;
  }
  const card = ensureStatusCard(jobId);
  card.querySelector("h4").textContent = data.name;
  const badge = card.querySelector(".badge");
  // queue_position (only present while status is "queued") distinguishes
  // "waiting behind N other job(s)" from what otherwise looks identical to
  // a stuck job -- real compare jobs here can legitimately queue behind
  // something that takes 40+ minutes, with nothing else changing on screen.
  badge.textContent = (data.status === "queued" && data.queue_position)
    ? `queued (${data.queue_position} ahead)` : data.status;
  badge.className = "badge " + data.status;
  card.querySelector(".elapsed").textContent = `  ${data.elapsed_s}s`;
  const resultsLink = card.querySelector(".results-link");
  resultsLink.innerHTML = data.result_index_url
    ? `  <a href="${data.result_index_url}" target="_blank" title="${data.result_index}">Open results</a>`
    : "";
  // Link to the full captured console (extraction + any chained plot jobs),
  // persisted to the results folder. Emphasized on failure -- e.g. a run job
  // marked "failed" because a chained plot job errored, where the actual
  // reason isn't in padb_run_*.log or the truncated live tail.
  const logLink = card.querySelector(".log-link");
  logLink.innerHTML = data.log_url
    ? `  <a href="${data.log_url}" target="_blank"${data.status === "failed" ? ' style="color:#c00;font-weight:600"' : ''}>View log</a>`
    : "";
  const abortBtn = card.querySelector(".abort-btn");
  const active = data.status === "queued" || data.status === "running";
  abortBtn.style.display = active ? "" : "none";
  const logEl = card.querySelector(".logtail");
  logEl.textContent = data.log_tail;
  logEl.scrollTop = logEl.scrollHeight;

  if (active) {
    setTimeout(() => poll(jobId), 2000);
  } else {
    activePolls.delete(jobId);
    loadJobs();
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function setRootHint(interactiveRoot, msg) {
  const hint = document.getElementById("rootHint");
  if (!hint) return;
  if (msg) { hint.textContent = msg; return; }
  hint.textContent = interactiveRoot ? ("Interactive tier → " + interactiveRoot) : "";
}

async function loadPublishConfig() {
  try {
    const res = await fetch("/api/config");
    const d = await res.json();
    if (!res.ok) return;
    const inp = document.getElementById("publishRootInput");
    if (inp) inp.value = d.publish_root || "";
    setRootHint(d.publish_root_interactive, null);
  } catch (e) { /* leave the placeholder text */ }
}

document.getElementById("saveRootBtn").addEventListener("click", async () => {
  const inp = document.getElementById("publishRootInput");
  const root = (inp.value || "").trim();
  if (!root) { alert("Enter a default share root first."); return; }
  const btn = document.getElementById("saveRootBtn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ publish_root: root }),
    });
    const d = await res.json();
    if (!res.ok) { alert("Save failed: " + (d.error || res.status)); return; }
    inp.value = d.publish_root;
    setRootHint(d.publish_root_interactive, null);
    alert((d.warning ? ("Saved, with a note:\n\n" + d.warning) : "Saved default share root.") +
          "\n\nWritten to: " + d.config_path);
  } finally { btn.disabled = false; }
});

loadJobs();
loadSites();
loadPublishConfig();

// Real report (2026-08-28): a page refresh loses the Running Jobs panel
// entirely, even though the job itself is still running server-side --
// startPolling() was only ever called right after THIS tab's own
// execute-job call, with nothing to rediscover jobs a previous page load
// (or a different tab) already queued/started. Re-subscribing to whatever
// the server reports as still active makes a refresh no longer look like
// it silently dropped a running job.
fetch("/api/active-jobs")
  .then(res => res.json())
  .then(data => (data.job_ids || []).forEach(startPolling))
  .catch(() => {});


// ---------------------------------------------------------------------------
// Edit job.json
// ---------------------------------------------------------------------------

let editJobPath = null;
function _ej(id) { return document.getElementById(id); }
function _ejMode(review) {
  _ej("editJobText").style.display = review ? "none" : "";
  _ej("editJobDiff").style.display = review ? "" : "none";
  _ej("editJobReview").style.display = review ? "none" : "";
  _ej("editJobBack").style.display = review ? "" : "none";
  _ej("editJobSave").style.display = review ? "" : "none";
}
async function openEditJob(path) {
  let res, data;
  try {
    res = await fetch("/api/job-file?path=" + encodeURIComponent(path));
    data = await res.json();
  } catch (e) {
    alert("Cannot edit: the webapp server did not answer the edit request (" +
      (res ? "HTTP " + res.status : e) + "). If you just updated the tool, restart the webapp (Start_web.bat) -- " +
      "the page can be newer than the running server.");
    return;
  }
  if (!res.ok) { alert("Cannot edit: " + data.error); return; }
  editJobPath = data.path;
  _ej("editJobTitle").textContent = "Edit " + data.name;
  _ej("editJobText").value = data.text;
  _ej("editJobErr").textContent = "";
  let note = "";
  if (data.active) note += "This job is queued/running -- saving is blocked until it finishes. ";
  if (data.scheduled) note += "This job is scheduled: saved changes apply to its next scheduled run. ";
  note += "Extraction keys (subex, pod) need a full re-run; analysis keys (views, spec_*, x_*) only need a re-plot. Sibling *_v2_job.json files sharing results_dir/publish_to are NOT changed.";
  _ej("editJobNote").textContent = note;
  _ej("editJobHist").style.display = "none";
  _ejMode(false);
  _ej("editJobModal").style.display = "";
}
_ej("editJobClose").addEventListener("click", () => { _ej("editJobModal").style.display = "none"; });
_ej("editJobBack").addEventListener("click", () => { _ej("editJobErr").textContent = ""; _ejMode(false); });
async function _ejPost(preview) {
  const res = await fetch("/api/job-file", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: editJobPath, text: _ej("editJobText").value, preview }),
  });
  return { ok: res.ok, data: await res.json() };
}
// Colour-coded diff: removed lines red, added lines green, hunk headers blue,
// dark text on solid backgrounds so it stays readable (no inherited grey).
function _ejRenderDiff(diff) {
  const box = _ej("editJobDiff");
  box.textContent = "";
  if (!diff) {
    box.textContent = "(no changes)";
    return;
  }
  for (const line of diff.split(/\r?\n/)) {
    if (line === "") continue;
    const d = document.createElement("div");
    d.textContent = line;
    d.style.whiteSpace = "pre";
    d.style.padding = "0 6px";
    if (line.startsWith("+++") || line.startsWith("---")) {
      d.style.color = "#555"; d.style.fontWeight = "700";
    } else if (line.startsWith("@@")) {
      d.style.color = "#0b3d91"; d.style.background = "#e3eefc";
    } else if (line.startsWith("+")) {
      d.style.color = "#0a3d12"; d.style.background = "#c8f0cf";
    } else if (line.startsWith("-")) {
      d.style.color = "#5a0a0a"; d.style.background = "#f8caca";
    } else {
      d.style.color = "#222";
    }
    box.appendChild(d);
  }
}

_ej("editJobReview").addEventListener("click", async () => {
  const { ok, data } = await _ejPost(true);
  if (!ok) { _ej("editJobErr").textContent = data.error; return; }
  const iss = (data.issues || []).map(i => "[" + i.level.toUpperCase() + "] " + (i.key || "job") + ": " + i.message
    + (i.suggestion ? " (" + i.suggestion + ")" : ""));
  _ej("editJobErr").style.whiteSpace = "pre-wrap";
  _ej("editJobErr").textContent = (data.changed ? "" : "No changes.") +
    (iss.length ? (data.changed ? "" : "\n") + "Schema check (advisory -- saving is not blocked):\n" + iss.join("\n") : "");
  _ejRenderDiff(data.changed ? data.diff : "");
  _ejMode(true);
  _ej("editJobSave").disabled = !data.changed;
});
_ej("editJobSave").addEventListener("click", async () => {
  const { ok, data } = await _ejPost(false);
  if (!ok) { _ej("editJobErr").textContent = data.error; return; }
  _ej("editJobModal").style.display = "none";
  if (data.backup) alert("Saved. Previous version kept at:\n" + data.backup);
  loadJobs();
});

// History / undo: list the .bak versions, load one into the editor, then run the
// normal Review -> Confirm path (so a restore is diffed, backed up, and guarded
// exactly like any other save -- and is itself undoable).
_ej("editJobHistory").addEventListener("click", async () => {
  const box = _ej("editJobHist");
  if (box.style.display !== "none") { box.style.display = "none"; return; }
  _ej("editJobErr").textContent = "";
  let data;
  try {
    const res = await fetch("/api/job-backups?path=" + encodeURIComponent(editJobPath));
    data = await res.json();
    if (!res.ok) { _ej("editJobErr").textContent = data.error; return; }
  } catch (e) {
    _ej("editJobErr").textContent = "Could not load history (is the webapp server up to date? restart it).";
    return;
  }
  box.textContent = "";
  if (!data.backups.length) {
    box.textContent = "No earlier versions yet -- one is saved each time you save an edit here.";
  } else {
    const head = document.createElement("div");
    head.style.cssText = "color:#555;margin-bottom:4px";
    head.textContent = "Newest first. Keeps the last " + data.max + " per job.";
    box.appendChild(head);
    for (const b of data.backups) {
      const row = document.createElement("div");
      row.style.cssText = "display:flex;gap:10px;align-items:center;padding:3px 0;border-top:1px solid #eee";
      const label = document.createElement("span");
      label.style.flex = "1";
      label.textContent = b.when + "  --  " +
        (b.diff_lines ? b.diff_lines + " line(s) differ from current" : "same as current");
      const btn = document.createElement("button");
      btn.textContent = "Load this version";
      btn.title = "Puts this version in the editor and shows a diff against the current file. Nothing is saved until you press Confirm save.";
      btn.addEventListener("click", async () => {
        const r = await fetch("/api/job-backup?path=" + encodeURIComponent(editJobPath) +
                              "&name=" + encodeURIComponent(b.name));
        const d = await r.json();
        if (!r.ok) { _ej("editJobErr").textContent = d.error; return; }
        _ej("editJobText").value = d.text;
        box.style.display = "none";
        _ej("editJobReview").click();
      });
      row.appendChild(label);
      row.appendChild(btn);
      box.appendChild(row);
    }
  }
  box.style.display = "";
});


// ---------------------------------------------------------------------------
// Job lists (named views over the jobs table; job files are never touched)
// ---------------------------------------------------------------------------

let jobLists = {};
const LIST_PREF_KEY = "padb_web_job_list";

async function loadJobLists() {
  try {
    const res = await fetch("/api/job-lists");
    if (res.ok) jobLists = (await res.json()).lists || {};
  } catch (e) { jobLists = {}; }
  const sel = document.getElementById("listFilter");
  let want = sel.value;
  if (want === "all" || !want) {
    try { want = localStorage.getItem(LIST_PREF_KEY) || "all"; } catch (e) { want = "all"; }
  }
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = "all"; all.textContent = "All jobs (" + allJobs.length + ")";
  sel.appendChild(all);
  for (const name of Object.keys(jobLists).sort((a, b) => a.localeCompare(b))) {
    const o = document.createElement("option");
    o.value = name; o.textContent = name + " (" + jobLists[name].length + ")";
    sel.appendChild(o);
  }
  sel.value = jobLists[want] ? want : "all";
  syncListButtons();
}

function syncListButtons() {
  const inList = document.getElementById("listFilter").value !== "all";
  for (const id of ["removeFromListBtn", "renameListBtn", "deleteListBtn"]) {
    document.getElementById(id).disabled = !inList;
  }
}

async function listOp(body) {
  const res = await fetch("/api/job-lists", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) { alert(data.error); return false; }
  return true;
}

function checkedJobNames() {
  return [...document.querySelectorAll("#jobsTable tbody input[type=checkbox]:checked")]
    .map(cb => cb.value.split(/[\\/]/).pop());
}

document.getElementById("listFilter").addEventListener("change", () => {
  try { localStorage.setItem(LIST_PREF_KEY, document.getElementById("listFilter").value); } catch (e) {}
  syncListButtons();
  renderJobsTable();
});

document.getElementById("addToListBtn").addEventListener("click", async () => {
  const names = checkedJobNames();
  if (!names.length) { alert("Tick at least one job first."); return; }
  const cur = document.getElementById("listFilter").value;
  const existing = Object.keys(jobLists);
  const hint = existing.length ? "Existing lists: " + existing.join(", ") + "\n\n" : "";
  const name = prompt(hint + "Add " + names.length + " job(s) to which list?\n(type a new name to create one)",
                      cur !== "all" ? cur : "");
  if (!name || !name.trim()) return;
  if (await listOp({ op: "add", list: name.trim(), names })) {
    try { localStorage.setItem(LIST_PREF_KEY, name.trim()); } catch (e) {}
    await loadJobs();
  }
});

document.getElementById("removeFromListBtn").addEventListener("click", async () => {
  const list = document.getElementById("listFilter").value;
  const names = checkedJobNames();
  if (list === "all" || !names.length) { alert("Pick a list, then tick the jobs to remove from it."); return; }
  if (!confirm("Remove " + names.length + " job(s) from the list '" + list + "'?\n\nThe job files are not deleted.")) return;
  if (await listOp({ op: "remove", list, names })) await loadJobs();
});

document.getElementById("renameListBtn").addEventListener("click", async () => {
  const list = document.getElementById("listFilter").value;
  if (list === "all") return;
  const nn = prompt("New name for the list '" + list + "':", list);
  if (!nn || !nn.trim() || nn.trim() === list) return;
  if (await listOp({ op: "rename", list, new: nn.trim() })) {
    try { localStorage.setItem(LIST_PREF_KEY, nn.trim()); } catch (e) {}
    await loadJobs();
  }
});

document.getElementById("deleteListBtn").addEventListener("click", async () => {
  const list = document.getElementById("listFilter").value;
  if (list === "all") return;
  if (!confirm("Delete the list '" + list + "'?\n\nOnly the list is removed; no job files are deleted.")) return;
  if (await listOp({ op: "delete", list })) {
    try { localStorage.setItem(LIST_PREF_KEY, "all"); } catch (e) {}
    document.getElementById("listFilter").value = "all";
    await loadJobs();
  }
});
