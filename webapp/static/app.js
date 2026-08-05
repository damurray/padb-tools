"use strict";

let currentPodPath = null;
let allJobs = [];
const activePolls = new Set();

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

document.getElementById("runSelectedBtn").addEventListener("click", async () => {
  const paths = selectedJobPaths();
  if (!paths.length) {
    alert("Select at least one job to run");
    return;
  }
  const dryRun = document.getElementById("dryRunCheckbox").checked;
  const res = await fetch("/api/execute-job", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths, dry_run: dryRun }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert("Execute failed: " + data.error);
    return;
  }
  for (const jobId of data.job_ids) startPolling(jobId);
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
      `<span class="results-link"></span><pre class="logtail"></pre>`;
    document.getElementById("statusPanels").prepend(card);
  }
  return card;
}

function startPolling(jobId) {
  if (activePolls.has(jobId)) return;
  activePolls.add(jobId);
  ensureStatusCard(jobId);
  poll(jobId);
}

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
  badge.textContent = data.status;
  badge.className = "badge " + data.status;
  card.querySelector(".elapsed").textContent = `  ${data.elapsed_s}s`;
  const resultsLink = card.querySelector(".results-link");
  resultsLink.innerHTML = data.result_index_url
    ? `  <a href="${data.result_index_url}" target="_blank" title="${data.result_index}">Open results</a>`
    : "";
  const logEl = card.querySelector(".logtail");
  logEl.textContent = data.log_tail;
  logEl.scrollTop = logEl.scrollHeight;

  if (data.status === "queued" || data.status === "running") {
    setTimeout(() => poll(jobId), 2000);
  } else {
    activePolls.delete(jobId);
    loadJobs();
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadJobs();
loadSites();
