#!/usr/bin/env python3
r"""qa_webapp.py -- hermetic Flask test-client coverage for the web app routes.

The web app (webapp/padb_web.py) is a thin layer over the CLI tools: every route
parses a request, does some bookkeeping, and either shells out or calls a pure
function. The bugs that actually happened there were in the ROUTE logic --
sibling-glob stem collisions, a run job's Results link pointing at the wrong page,
deleting a results_dir still shared by a surviving job.json, the publish-gate
flag precedence -- not in the CLI tools it calls. This gate exercises that route
logic with the Flask test client, with every real side effect neutralized:

  * DATA_DIR is redirected to a throwaway temp dir (patched BEFORE importing
    padb_web, so the module-level DATA_DIR and the import-time resume scan both
    see the empty temp dir -- never this workstation's real job folder).
  * The worker's subprocess launcher (_stream) is replaced with a recorder that
    returns success without spawning anything -- so /api/execute-job exercises the
    real queue + worker + run/plot dispatch WITHOUT running PADB-R.exe or padb_v2.
  * schtasks (create_task/delete_task/discover), taskkill (subprocess.run),
    padb_config.save_config (writes under $HOME), and padb_batch's process-table
    probes are all stubbed -- no Windows Scheduled Task, no killed process, no
    config file written, no dependence on what's actually running.

Nothing here touches the network share, the real data dir, Task Scheduler, or any
real process. Browser-free and deterministic, so it belongs in qa_selfcheck's core.

Usage:  python qa_webapp.py
Exit codes: 0 = all checks pass, 1 = one or more failures.
"""
from __future__ import annotations

import json
import subprocess as _subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEBAPP = HERE / "webapp"
for _p in (str(HERE), str(WEBAPP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_PASS: list[str] = []
_FAIL: list[str] = []


def check(desc: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASS.append(desc)
        print(f"  PASS  {desc}")
    else:
        _FAIL.append(desc)
        print(f"  FAIL  {desc}" + (f" -- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Redirect DATA_DIR to a temp dir BEFORE importing padb_web (its module-level
# DATA_DIR and the import-time resume scan both read padb_config.load_defaults()).
# ---------------------------------------------------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="qa_webapp_"))
import padb_config  # noqa: E402
_orig_defaults = padb_config.load_defaults()
_test_defaults = {**_orig_defaults, "data_dir": str(_TMP)}
padb_config.load_defaults = lambda: dict(_test_defaults)
_saved_config: list[dict] = []
padb_config.save_config = lambda updates: (_saved_config.append(dict(updates)) or padb_config.CONFIG_PATH)

import padb_batch  # noqa: E402
# Read-only process probes -> deterministic empties (no dependence on what's running).
padb_batch._running_batch_pids = lambda exe: []
padb_batch._process_command_lines = lambda exe: {}
padb_batch._orphaned_host_pids = lambda: []

import padb_web  # noqa: E402  (starts the worker thread + resume scan against the temp dir)

# Neutralise every side-effecting seam in padb_web's own namespace.
_stream_calls: list[list[str]] = []


def _fake_stream(cmd, job_id):
    _stream_calls.append(list(cmd))
    return 0   # pretend the subprocess ran and succeeded, without spawning it


padb_web._stream = _fake_stream
padb_web.create_task = lambda *a, **k: (True, "")
padb_web.delete_task = lambda *a, **k: (True, "")
padb_web.discover_all_padb_tasks = lambda: []
padb_web.query_task = lambda name: {}
padb_web.format_schedule_summary = lambda info: ""

_subprocess_calls: list[list[str]] = []


def _fake_run(cmd, *a, **k):
    _subprocess_calls.append(list(cmd))
    return _subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


padb_web.subprocess.run = _fake_run   # generate-job cmd build, taskkill, _pid_running

padb_web.DATA_DIR = _TMP
client = padb_web.app.test_client()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_job(name: str, cfg: dict) -> Path:
    p = _TMP / name
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def _clear_jobs_dir() -> None:
    for p in _TMP.glob("*_job.json"):
        p.unlink()


def _wait_terminal(job_id: str, timeout: float = 5.0) -> str:
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/job-status/{job_id}")
        if r.status_code == 200 and r.get_json()["status"] in ("done", "failed", "cancelled"):
            return r.get_json()["status"]
        time.sleep(0.05)
    return "timeout"


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def test_index():
    r = client.get("/")
    check("GET / renders the page", r.status_code == 200 and b"<" in r.data, f"status={r.status_code}")


def test_sites():
    r = client.get("/api/sites")
    j = r.get_json() if r.status_code == 200 else {}
    sites = j.get("sites", {})
    primary = [n for n, suf in sites.items() if suf == ""]
    check("GET /api/sites returns the site registry (one primary)",
          r.status_code == 200 and len(sites) >= 1 and len(primary) == 1,
          f"status={r.status_code} sites={list(sites)} primary={primary}")


def test_config():
    r = client.get("/api/config")
    j = r.get_json() if r.status_code == 200 else {}
    check("GET /api/config returns publish roots + derived interactive root",
          r.status_code == 200 and "publish_root" in j and "publish_root_interactive" in j
          and j["publish_root_interactive"] == j["publish_root"].replace("PADB-Simple", "PADB-Interactive"),
          f"j={j}")
    # POST empty -> 400
    r = client.post("/api/config", json={"publish_root": ""})
    check("POST /api/config rejects empty publish_root", r.status_code == 400, f"status={r.status_code}")
    # POST valid PADB-Simple root -> saved, no warning
    _saved_config.clear()
    r = client.post("/api/config", json={"publish_root": r"\\srv\prod\SG6311A\PADB-Simple"})
    j = r.get_json()
    check("POST /api/config saves a valid root (no warning, save_config called)",
          r.status_code == 200 and j.get("ok") and j.get("warning") is None and len(_saved_config) == 1
          and _saved_config[0]["publish_root"].endswith("PADB-Simple"),
          f"status={r.status_code} saved={_saved_config} j={j}")
    # POST non-conventional root -> saved WITH warning
    r = client.post("/api/config", json={"publish_root": r"\\srv\prod\Other"})
    j = r.get_json()
    check("POST /api/config warns on a root not ending in PADB-Simple (still saves)",
          r.status_code == 200 and j.get("ok") and bool(j.get("warning")),
          f"j={j}")


def test_jobs_listing_and_kind():
    _clear_jobs_dir()
    _write_job("aaa_run_job.json", {"pod": "x.pod", "mode": "legacy", "description": "a run job"})
    _write_job("bbb_plot_v2_job.json", {"csv_path": str(_TMP / "b.csv"), "description": "a plot job"})
    _write_job("ccc_compare_v2_job.json",
               {"compare_csv": {"SR": str(_TMP / "sr.csv"), "AMC": str(_TMP / "amc.csv")},
                "primary_site": "SR", "description": "a compare job"})
    r = client.get("/api/jobs")
    j = r.get_json() if r.status_code == 200 else {}
    jobs = {x["name"]: x for x in j.get("jobs", [])}
    check("GET /api/jobs lists all job files", r.status_code == 200 and len(jobs) == 3,
          f"names={list(jobs)}")
    check("GET /api/jobs classifies a pod job as kind=run",
          jobs.get("aaa_run_job.json", {}).get("kind") == "run", f"{jobs.get('aaa_run_job.json')}")
    check("GET /api/jobs classifies a csv_path job as kind=plot",
          jobs.get("bbb_plot_v2_job.json", {}).get("kind") == "plot", f"{jobs.get('bbb_plot_v2_job.json')}")
    # is_compare drives the PDF "site" dropdown greying (compare jobs only).
    check("GET /api/jobs flags a compare_csv job is_compare=True",
          jobs.get("ccc_compare_v2_job.json", {}).get("is_compare") is True,
          f"{jobs.get('ccc_compare_v2_job.json')}")
    check("GET /api/jobs flags a non-compare job is_compare=False",
          jobs.get("bbb_plot_v2_job.json", {}).get("is_compare") is False,
          f"{jobs.get('bbb_plot_v2_job.json')}")


def test_results_token_roundtrip():
    _clear_jobs_dir()
    rd = _TMP / "rt_results"
    rd.mkdir(exist_ok=True)
    (rd / "index.html").write_text("<html>RESULT-SENTINEL</html>", encoding="utf-8")
    _write_job("rt_plot_v2_job.json", {"csv_path": str(_TMP / "rt.csv"), "results_dir": str(rd)})
    r = client.get("/api/jobs")
    job = next((x for x in r.get_json()["jobs"] if x["name"] == "rt_plot_v2_job.json"), {})
    url = job.get("index_url")
    check("GET /api/jobs sets a served index_url when the results index exists",
          bool(url) and url.startswith("/results/"), f"index_url={url}")
    if url:
        r2 = client.get(url)
        check("GET /results/<token>/index.html serves the real file (token round-trip)",
              r2.status_code == 200 and b"RESULT-SENTINEL" in r2.data, f"status={r2.status_code}")
    # a bad token -> 404
    r3 = client.get("/results/deadbeefdeadbeef/index.html")
    check("GET /results/<bad-token>/... is 404", r3.status_code == 404, f"status={r3.status_code}")


def test_schedule():
    p = _write_job("sch_run_job.json", {"pod": "x.pod"})
    check("POST /api/schedule rejects empty paths",
          client.post("/api/schedule", json={"paths": []}).status_code == 400)
    check("POST /api/schedule rejects a bad schedule_type",
          client.post("/api/schedule", json={"paths": [str(p)], "schedule_type": "HOURLY",
                                             "start_time": "02:00"}).status_code == 400)
    check("POST /api/schedule rejects a missing start_time",
          client.post("/api/schedule", json={"paths": [str(p)], "schedule_type": "DAILY"}).status_code == 400)
    r = client.post("/api/schedule", json={"paths": [str(p)], "schedule_type": "DAILY", "start_time": "02:00"})
    j = r.get_json()
    res = (j.get("results") or [{}])[0]
    check("POST /api/schedule (valid) returns ok with TASK_PREFIX task name",
          r.status_code == 200 and res.get("ok") and res.get("task_name") == padb_web.TASK_PREFIX + "sch_run_job",
          f"j={j}")


def test_unschedule():
    p = _write_job("unsch_run_job.json", {"pod": "x.pod"})
    check("POST /api/unschedule rejects empty paths",
          client.post("/api/unschedule", json={"paths": []}).status_code == 400)
    r = client.post("/api/unschedule", json={"paths": [str(p)]})
    res = (r.get_json().get("results") or [{}])[0]
    check("POST /api/unschedule (valid) returns ok", r.status_code == 200 and res.get("ok"),
          f"j={r.get_json()}")


def test_delete_shared_results_dir():
    _clear_jobs_dir()
    shared = _TMP / "shared_v2_results"
    shared.mkdir(exist_ok=True)
    (shared / "index.html").write_text("x", encoding="utf-8")
    a = _write_job("pod_A_v2_job.json", {"csv_path": str(_TMP / "a.csv"), "results_dir": str(shared)})
    b = _write_job("pod_B_v2_job.json", {"csv_path": str(_TMP / "b.csv"), "results_dir": str(shared)})
    # missing paths -> 400
    check("POST /api/delete-job rejects empty paths",
          client.post("/api/delete-job", json={"paths": []}).status_code == 400)
    # delete A with data: shared results_dir must be KEPT (B still references it)
    r = client.post("/api/delete-job", json={"paths": [str(a)], "delete_data": True})
    entry = (r.get_json().get("results") or [{}])[0]
    check("delete-job keeps a results_dir still referenced by a surviving job.json",
          entry.get("ok") and not entry.get("deleted_results") and "kept" in (entry.get("note") or "")
          and shared.is_dir() and not a.exists() and b.exists(),
          f"entry={entry} shared_exists={shared.is_dir()}")
    # now delete B with data: nothing else references it -> removed
    r = client.post("/api/delete-job", json={"paths": [str(b)], "delete_data": True})
    entry = (r.get_json().get("results") or [{}])[0]
    check("delete-job removes a results_dir once no job.json references it",
          entry.get("ok") and entry.get("deleted_results") and not shared.is_dir(),
          f"entry={entry} shared_exists={shared.is_dir()}")
    # not-found path -> per-entry error, not a 500
    r = client.post("/api/delete-job", json={"paths": [str(_TMP / "nope_job.json")]})
    entry = (r.get_json().get("results") or [{}])[0]
    check("delete-job reports 'not found' for a missing path (no crash)",
          r.status_code == 200 and entry.get("error") == "not found", f"entry={entry}")


def test_execute_and_status():
    _clear_jobs_dir()
    _stream_calls.clear()
    run = _write_job("ex_run_job.json", {"pod": "x.pod", "mode": "legacy", "results_dir": str(_TMP / "ex_run_results")})
    plot = _write_job("ex_plot_v2_job.json", {"csv_path": str(_TMP / "ex.csv"), "results_dir": str(_TMP / "ex_plot_results")})
    check("POST /api/execute-job rejects empty paths",
          client.post("/api/execute-job", json={"paths": []}).status_code == 400)
    check("POST /api/execute-job rejects a missing job path",
          client.post("/api/execute-job", json={"paths": [str(_TMP / "ghost_job.json")]}).status_code == 400)
    # Submit plot BEFORE run; execute-job must reorder run ahead of plot (kind-rank).
    r = client.post("/api/execute-job", json={"paths": [str(plot), str(run)], "publish": False})
    j = r.get_json()
    ids = j.get("job_ids", [])
    check("POST /api/execute-job returns a job_id per path", r.status_code == 200 and len(ids) == 2, f"j={j}")
    states = [_wait_terminal(i) for i in ids]
    check("execute-job's queued jobs reach a terminal state via the worker",
          all(s == "done" for s in states), f"states={states}")
    # dispatch: the run job -> padb_run.py, the plot job -> padb_v2.py, both --no-publish
    scripts = [c for c in _stream_calls]
    ran_run = any(any("padb_run.py" in a for a in c) and "--no-publish" in c for c in scripts)
    ran_plot = any(any("padb_v2.py" in a for a in c) and "--no-publish" in c for c in scripts)
    check("worker dispatches a pod job via padb_run.py --no-publish", ran_run, f"stream_calls={scripts}")
    check("worker dispatches a plot job via padb_v2.py --no-publish", ran_plot, f"stream_calls={scripts}")
    # unknown job-status id -> 404
    check("GET /api/job-status/<unknown> is 404",
          client.get("/api/job-status/does-not-exist").status_code == 404)
    # queue-position ("N ahead") is derived from the real FIFO queue + the
    # actively-running job, so it stays sequential and monotonic. Regression for
    # the insertion-order scan, which counted side-thread resume jobs and could
    # be non-sequential / non-monotonic. _queue_ahead is pure -> test directly.
    qa = padb_web._queue_ahead
    fifo = ["a", "b", "c", "d"]  # worker-queue FIFO snapshot (front = a)
    # nothing running yet: positions equal the queue index -> 0,1,2,3 (sequential)
    check("queue-pos: FIFO index with idle worker is 0,1,2,3",
          [qa(j, fifo, None) for j in fifo] == [0, 1, 2, 3])
    # a job running (not in the FIFO): every waiting job gains exactly one -> 1,2,3,4
    check("queue-pos: a running job adds exactly one to each waiter",
          [qa(j, fifo, "run") for j in fifo] == [1, 2, 3, 4])
    # monotonic drain: as the front runs and leaves the FIFO, a fixed job's count
    # only ever decreases (never jumps around).
    seq = [qa("c", ["a", "b", "c", "d"], None),   # 2 (a,b ahead)
           qa("c", ["b", "c", "d"], "a"),          # 1 (b) + 1 (a running) = 2
           qa("c", ["c", "d"], "b"),               # 0 + 1 (b running) = 1
           qa("c", ["d"], "c")]                     # c now running: 0 (+0, it's itself)
    check("queue-pos: a job's count is monotonic non-increasing as the queue drains",
          all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1)) and seq == [2, 2, 1, 0],
          f"seq={seq}")
    # The helper considers ONLY the FIFO snapshot + the single worker-current
    # job -- it never looks at _jobs. So a startup auto-resume job (which lives in
    # _jobs and may be "running" on its own thread, but is neither in _job_queue
    # nor the worker-current slot) cannot inflate any waiter's count: the front of
    # an otherwise-idle queue is 0 regardless of how many such jobs exist.
    check("queue-pos: front of an idle queue is 0 (side-thread resume jobs can't inflate it)",
          qa("a", fifo, None) == 0)
    # de-dup: re-submitting the SAME path while its entry exists reuses the id
    r2 = client.post("/api/execute-job", json={"paths": [str(run)]})
    _wait_terminal(r2.get_json()["job_ids"][0])
    r3 = client.post("/api/execute-job", json={"paths": [str(run)]})
    # (can't assert same id across terminal states, but must still succeed and be a single id)
    check("re-submitting a job path returns exactly one job_id",
          r3.status_code == 200 and len(r3.get_json().get("job_ids", [])) == 1, f"j={r3.get_json()}")


def test_generate_job_cmd_build():
    pod = _TMP / "demo.pod"
    pod.write_text("[Extract]\nDevice_Device='SG6311A'\n", encoding="utf-8")
    check("POST /api/generate-job rejects a missing pod_path",
          client.post("/api/generate-job", json={"pod_path": str(_TMP / "nope.pod")}).status_code == 400)
    check("POST /api/generate-job rejects an invalid mode",
          client.post("/api/generate-job", json={"pod_path": str(pod), "mode": "bogus"}).status_code == 400)

    def _cmd_for(body):
        _subprocess_calls.clear()
        client.post("/api/generate-job", json={**{"pod_path": str(pod)}, **body})
        return _subprocess_calls[-1] if _subprocess_calls else []

    c = _cmd_for({"mode": "simple", "module": "MyMod"})
    check("generate-job: module (no override) -> --module, no --no-publish",
          any("padb_make_job.py" in a for a in c) and "--module" in c and "--no-publish" not in c, f"cmd={c}")
    c = _cmd_for({"mode": "simple"})
    check("generate-job: no module/override -> --no-publish",
          "--no-publish" in c and "--module" not in c, f"cmd={c}")
    c = _cmd_for({"mode": "simple", "publish_to": r"\\srv\custom\path"})
    check("generate-job: explicit share-path -> --publish-to (wins over --no-publish)",
          "--publish-to" in c and "--no-publish" not in c, f"cmd={c}")
    c = _cmd_for({"mode": "interactive", "module": "MyMod"})
    check("generate-job: interactive -> padb_make_v2_job.py, no --mode",
          any("padb_make_v2_job.py" in a for a in c) and "--mode" not in c, f"cmd={c}")


def test_convert_validation():
    check("POST /api/convert-pod rejects a missing pod_path",
          client.post("/api/convert-pod", json={"pod_path": str(_TMP / "no.pod"), "target_site": "X"}).status_code == 400)
    pod = _TMP / "conv.pod"
    pod.write_text("[Extract]\n", encoding="utf-8")
    check("POST /api/convert-pod rejects an unknown target site",
          client.post("/api/convert-pod", json={"pod_path": str(pod), "target_site": "NoSuchSite"}).status_code == 400)
    check("POST /api/convert-job rejects empty paths",
          client.post("/api/convert-job", json={"paths": [], "target_site": "X"}).status_code == 400)
    check("POST /api/convert-job rejects an unknown target site",
          client.post("/api/convert-job", json={"paths": [str(_TMP / "j_job.json")], "target_site": "NoSuchSite"}).status_code == 400)


def test_orphaned_and_active():
    r = client.get("/api/orphaned-padb")
    j = r.get_json() if r.status_code == 200 else {}
    check("GET /api/orphaned-padb returns processes + has_running_job_here",
          r.status_code == 200 and isinstance(j.get("processes"), list) and "has_running_job_here" in j,
          f"j={j}")
    # kill with no pids -> empty results, no taskkill invoked
    _subprocess_calls.clear()
    r = client.post("/api/orphaned-padb/kill", json={"pids": []})
    check("POST /api/orphaned-padb/kill with no pids is a no-op (no taskkill)",
          r.status_code == 200 and r.get_json().get("results") == [] and not _subprocess_calls,
          f"j={r.get_json()} calls={_subprocess_calls}")
    # kill a specific pid -> taskkill invoked (mocked rc 0 -> ok)
    r = client.post("/api/orphaned-padb/kill", json={"pids": [999999]})
    res = (r.get_json().get("results") or [{}])[0]
    check("POST /api/orphaned-padb/kill invokes taskkill per pid",
          res.get("pid") == "999999" and res.get("ok")
          and any("taskkill" in a for c in _subprocess_calls for a in c),
          f"res={res} calls={_subprocess_calls}")
    r = client.get("/api/active-jobs")
    check("GET /api/active-jobs returns a job_ids list",
          r.status_code == 200 and isinstance(r.get_json().get("job_ids"), list), f"j={r.get_json()}")


def test_upload_validation():
    check("POST /api/upload-pod rejects a request with no file",
          client.post("/api/upload-pod", data={}).status_code == 400)


def test_generate_reduce():
    """PROTOTYPE test-point reduce action: /api/generate-reduce resolves the job's
    extracted CSV, queues a 'reduce' action, and the worker dispatches
    padb_testpoint_reduce.py --mode target --target-pct (subprocess mocked)."""
    _clear_jobs_dir()
    _stream_calls.clear()
    rd = _TMP / "red_run_results"
    (rd / "padb").mkdir(parents=True, exist_ok=True)
    csvp = rd / "padb" / "MyAnalytic.csv"
    csvp.write_text("Frequency (MHz),Value,Group\n1,-70,Amp State: 0\n", encoding="utf-8")
    run = _write_job("red_run_job.json", {"pod": "x.pod", "mode": "interactive",
                                          "results_dir": "red_run_results"})
    # _reduce_targets resolves the run job's padb CSV (and skips reducer output)
    tgts = padb_web._reduce_targets(run, json.loads(run.read_text(encoding="utf-8")))
    check("_reduce_targets finds the run job's extracted CSV",
          len(tgts) == 1 and tgts[0].name == "MyAnalytic.csv", f"tgts={tgts}")
    # validation
    check("POST /api/generate-reduce rejects empty paths",
          client.post("/api/generate-reduce", json={"paths": []}).status_code == 400)
    check("POST /api/generate-reduce rejects a missing job path",
          client.post("/api/generate-reduce", json={"paths": [str(_TMP / "ghost_job.json")]}).status_code == 400)
    # happy path: queue + worker dispatch
    r = client.post("/api/generate-reduce", json={"paths": [str(run)], "target_pct": 30})
    j = r.get_json()
    ids = j.get("job_ids", [])
    check("POST /api/generate-reduce returns a job_id", r.status_code == 200 and len(ids) == 1, f"j={j}")
    state = _wait_terminal(ids[0]) if ids else "?"
    check("reduce job reaches a terminal state via the worker", state == "done", f"state={state}")
    call = next((c for c in _stream_calls if any("padb_testpoint_reduce.py" in a for a in c)), None)
    check("worker dispatches padb_testpoint_reduce.py --mode target --target-pct",
          call is not None and "--mode" in call and "target" in call
          and "--target-pct" in call and "30" in " ".join(call), f"call={call}")
    # Auto (adaptive) mode: --mode adaptive, and NO --target-pct (the % is an outcome).
    _stream_calls.clear()
    r2 = client.post("/api/generate-reduce", json={"paths": [str(run)], "mode": "adaptive"})
    ids2 = r2.get_json().get("job_ids", [])
    if ids2:
        _wait_terminal(ids2[0])
    call2 = next((c for c in _stream_calls if any("padb_testpoint_reduce.py" in a for a in c)), None)
    check("adaptive mode dispatches --mode adaptive with NO --target-pct",
          call2 is not None and "adaptive" in call2 and "--target-pct" not in call2, f"call2={call2}")


def test_compare_reduce_on_merged():
    """PROTOTYPE (2026-09-17): the compare panel can request test-point reduction on
    the MERGED (both-sites-combined) data. compare_create persists reduce_on_merged/
    reduce_pct (panel authoritative -- unchecking clears it); _reduce_targets resolves
    _compare_merged.csv for the compare job; and the worker chains the reducer after a
    successful compare build."""
    _clear_jobs_dir()
    a = _TMP / "siteA.csv"; a.write_text(
        "Frequency (MHz),Value,Group\n1,-70,Amp State: 0\n2,-71,Amp State: 0\n", encoding="utf-8")
    b = _TMP / "siteB.csv"; b.write_text(
        "Frequency (MHz),Value,Group\n1,-69,Amp State: 0\n2,-68,Amp State: 0\n", encoding="utf-8")
    r = client.post("/api/compare-create", json={
        "csv_a": str(a), "site_a": "SR", "csv_b": str(b), "site_b": "AMC",
        "primary_site": "SR", "reduce_on_merged": True, "reduce_pct": 30,
        "reduce_mode": "adaptive"})
    j = r.get_json()
    check("compare-create with reduce_on_merged succeeds", r.status_code == 200 and j.get("path"), f"j={j}")
    jobp = Path(j["path"])
    cfg = json.loads(jobp.read_text(encoding="utf-8"))
    check("compare job persists reduce_on_merged=True, reduce_pct=30, reduce_mode=adaptive",
          cfg.get("reduce_on_merged") is True and cfg.get("reduce_pct") == 30
          and cfg.get("reduce_mode") == "adaptive", f"cfg={cfg}")
    check("compare job persists compare_csv (two sites)",
          isinstance(cfg.get("compare_csv"), dict) and len(cfg["compare_csv"]) == 2, f"cfg={cfg}")
    # _reduce_targets resolves the merged CSV for a compare job once it exists.
    rd = jobp.parent / cfg["results_dir"]; rd.mkdir(parents=True, exist_ok=True)
    (rd / "_compare_merged.csv").write_text("Frequency (MHz),Value,Group\n1,-70,Site: SR\n", encoding="utf-8")
    tgts = padb_web._reduce_targets(jobp, cfg)
    check("_reduce_targets resolves _compare_merged.csv for a compare job",
          len(tgts) == 1 and tgts[0].name == "_compare_merged.csv", f"tgts={tgts}")
    # TEETH: unchecking the box on a re-create clears the flag (panel authoritative).
    r2 = client.post("/api/compare-create", json={
        "csv_a": str(a), "site_a": "SR", "csv_b": str(b), "site_b": "AMC",
        "primary_site": "SR", "reduce_on_merged": False})
    cfg2 = json.loads(Path(r2.get_json()["path"]).read_text(encoding="utf-8"))
    check("re-create with box unchecked clears reduce_on_merged (panel authoritative)",
          cfg2.get("reduce_on_merged") is False, f"cfg2={cfg2}")
    # Worker source pin: the post-build reduce chain exists, gated on compare + flag.
    src = Path(padb_web.__file__).read_text(encoding="utf-8")
    check("worker chains the reducer after a successful compare build",
          'cfg.get("reduce_on_merged")' in src and 'cfg.get("compare_csv")' in src
          and "_generate_reduce_task(" in src)


def test_link_reduce_report_in_index():
    """The reduction report must be reachable from the results folder: after the
    reducer runs, its .txt/.csv is linked into the results index.html. Idempotent --
    a re-run replaces the marked block, never duplicates it."""
    rd = _TMP / "rr_link_results"; rd.mkdir(parents=True, exist_ok=True)
    (rd / "index.html").write_text(
        "<html><body><h1>Results</h1></body></html>", encoding="utf-8")
    rep_txt = rd / "_compare_merged_testpoint_reduction.txt"; rep_txt.write_text("report", encoding="utf-8")
    rep_csv = rd / "_compare_merged_testpoint_reduction.csv"; rep_csv.write_text("a,b\n", encoding="utf-8")
    padb_web._link_reduce_reports_in_index(rd, [rep_txt, rep_csv])
    html = (rd / "index.html").read_text(encoding="utf-8")
    check("reduction report section injected with links to both report files",
          "Test-point reduction report" in html
          and 'href="_compare_merged_testpoint_reduction.txt"' in html
          and 'href="_compare_merged_testpoint_reduction.csv"' in html)
    # TEETH: a second call must not duplicate the block.
    padb_web._link_reduce_reports_in_index(rd, [rep_txt, rep_csv])
    html2 = (rd / "index.html").read_text(encoding="utf-8")
    check("re-linking is idempotent (single marked block, no duplication)",
          html2.count("<!--padb-reduce-report-start-->") == 1
          and html2.count("Test-point reduction report") == 1)
    # A missing report / missing index is a safe no-op.
    (rd / "index.html").unlink()
    padb_web._link_reduce_reports_in_index(rd, [rep_txt])  # no index -> no crash
    check("no index.html -> safe no-op", not (rd / "index.html").exists())


def test_single_instance_guard():
    """A second webapp instance must NOT linger + run jobs in parallel with the real
    one (the 'multiple webapp generations' hole). main() refuses to start when :5000
    is already served, and the resume scan (which would re-queue/run jobs) moved out
    of module-import into main() behind that guard -- so only the port-owning server
    resumes work. The worker still starts at import (this test client needs it)."""
    import socket
    from pathlib import Path as _P
    # free port -> not in use
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); free_port = probe.getsockname()[1]
    check("_port_in_use False for a free port", padb_web._port_in_use(port=free_port) is False)
    # a live listener -> in use
    lsock = socket.socket(); lsock.bind(("127.0.0.1", 0)); lsock.listen(1)
    used = lsock.getsockname()[1]
    try:
        check("_port_in_use True when a server is listening", padb_web._port_in_use(port=used) is True)
    finally:
        lsock.close()
    src = _P(padb_web.__file__).read_text(encoding="utf-8")
    check("main() refuses a second instance (port guard + exit)",
          "if _port_in_use():" in src and "sys.exit(1)" in src)
    check("resume scan is gated in main(), not run at module import",
          "\n_resume_incomplete_v2_chains()\n" not in src
          and "    _resume_incomplete_v2_chains()" in src)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("qa_webapp -- Flask test-client route coverage (hermetic)")
    for fn in (test_index, test_sites, test_config, test_jobs_listing_and_kind,
               test_results_token_roundtrip, test_schedule, test_unschedule,
               test_delete_shared_results_dir, test_execute_and_status,
               test_generate_reduce, test_compare_reduce_on_merged,
               test_link_reduce_report_in_index, test_single_instance_guard,
               test_generate_job_cmd_build, test_convert_validation,
               test_orphaned_and_active, test_upload_validation):
        try:
            fn()
        except Exception as exc:
            check(f"{fn.__name__} raised", False, repr(exc))
    print(f"\n{'=' * 60}\n  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}")
    if _FAIL:
        print("\nFailed checks:")
        for f in _FAIL:
            print(f"  - {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
