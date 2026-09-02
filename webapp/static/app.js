"use strict";

let currentPodPath = null;
let allJobs = [];
const activePolls = new Set();

// ---------------------------------------------------------------------------
// Tooltip help toggle
// ---------------------------------------------------------------------------

// Native title-attribute tooltips can only be suppressed by removing the
// attribute itself (CSS has no effect on them) -- so the real text is
// stashed in a data- attribute the first time an element is seen, and
// title is added/removed from that backup based on the current preference.
// Idempotent and safe to call repeatedly, including on elements whose
// tooltip was created after page load (job table rows, status cards) --
// already-processed elements have no title left to (re-)back up, so a
// second call just re-applies the current preference from the stash.
let _tooltipsEnabled = true;
function applyTooltipPref() {
  document.querySelectorAll("[title]").forEach(el => {
    if (el.dataset.tooltipText === undefined) el.dataset.tooltipText = el.getAttribute("title");
  });
  document.querySelectorAll("[data-tooltip-text]").forEach(el => {
    if (_tooltipsEnabled) el.setAttribute("title", el.dataset.tooltipText);
    else el.removeAttribute("title");
  });
}

const tooltipToggle = document.getElementById("tooltipToggle");
_tooltipsEnabled = localStorage.getItem("padb_web_tooltips") !== "0";
tooltipToggle.checked = _tooltipsEnabled;
applyTooltipPref();
tooltipToggle.addEventListener("change", () => {
  _tooltipsEnabled = tooltipToggle.checked;
  localStorage.setItem("padb_web_tooltips", _tooltipsEnabled ? "1" : "0");
  applyTooltipPref();
});

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

async function uploadPod(file) {
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
    alert("Drop a .pod file first");
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
});

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

// Spell out WHY Create & Run is disabled, since a greyed button alone doesn't
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
    ? "Resolve the block above (or check Override) to enable Create & Run."
    : "← Run “Check compatibility” first to enable Create & Run.";
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
  btn.textContent = "Creating & queuing...";
  try {
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
    const execRes = await fetch("/api/execute-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: [data.path], dry_run: false }),
    });
    const execData = await execRes.json();
    if (!execRes.ok) {
      alert("Compare job created but failed to queue: " + execData.error);
      return;
    }
    for (const jobId of execData.job_ids) startPolling(jobId);
    if (execData.job_ids.length) scrollJobStatusIntoView(execData.job_ids[0]);
    loadJobs();
  } finally {
    btn.textContent = origText;
    refreshCompareCreateEnabled();
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
  renderJobsTable();
}

function renderJobsTable() {
  const modeFilter = document.getElementById("modeFilter").value;
  const kindFilter = document.getElementById("kindFilter").value;
  const nameFilter = document.getElementById("nameFilter").value.trim().toLowerCase();
  const tbody = document.querySelector("#jobsTable tbody");
  tbody.innerHTML = "";
  for (const job of allJobs) {
    if (modeFilter !== "all" && job.mode !== modeFilter) continue;
    if (kindFilter !== "all" && job.kind !== kindFilter) continue;
    if (nameFilter && !job.name.toLowerCase().includes(nameFilter)) continue;
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = job.path;
    cb.dataset.kind = job.kind;
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
    tbody.appendChild(tr);
  }
  applyTooltipPref(); // Results link's title is only known once rendered here
}

function selectedJobPaths() {
  return [...document.querySelectorAll("#jobsTable tbody input[type=checkbox]:checked")]
    .map(cb => cb.value);
}

document.getElementById("refreshJobsBtn").addEventListener("click", loadJobs);
document.getElementById("modeFilter").addEventListener("change", renderJobsTable);
document.getElementById("kindFilter").addEventListener("change", renderJobsTable);
document.getElementById("nameFilter").addEventListener("input", renderJobsTable);

document.getElementById("selectAllRunnableBtn").addEventListener("click", () => {
  const boxes = document.querySelectorAll("#jobsTable tbody input[type=checkbox]");
  for (const cb of boxes) cb.checked = cb.dataset.kind === "run";
});

// Select every job matching the current Mode/Kind/Name filters. renderJobsTable
// only appends rows that pass all three filters, so the rendered checkboxes ARE
// exactly the filtered set (including any scrolled out of the 320px view box) --
// checking them all is robust by construction, no re-derivation of the filters
// needed here.
document.getElementById("selectFilteredBtn").addEventListener("click", () => {
  const boxes = document.querySelectorAll("#jobsTable tbody input[type=checkbox]");
  for (const cb of boxes) cb.checked = true;
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
// "Create & Run" button (section 3) and, less dramatically, "Run Selected"
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
