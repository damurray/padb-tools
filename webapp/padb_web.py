"""
padb_web.py -- local web UI for padb-tools, Phase 1.

Covers four of the seven features requested for the web app: (1) drop a
.pod file to auto-generate its job.json, (2) schedule/unschedule jobs in
Windows Task Scheduler, (3) convert a pod/job between database sites, and
(7) execute one or more job files. Every route shells out to the
already-verified CLI scripts (padb_make_job.py, padb_make_v2_job.py,
padb_run.py, padb_v2.py) via subprocess, or imports pure functions directly
from padb_scheduler.py (discover_all_padb_tasks, create_task, delete_task,
query_task, format_schedule_summary) and padb_convert_site.py (load_sites,
convert_pod, convert_job) -- this is a pure orchestration layer, not a
reimplementation of any of that logic.

    py webapp\\padb_web.py

Opens http://127.0.0.1:5000 in the default browser. Local use only: the
Flask dev server here is not meant to be reachable beyond 127.0.0.1.

PADB-R.exe is a WinForms app that must run in a real desktop session and
two instances running concurrently interfere with each other. The single
background worker thread + queue.Queue below is the actual serialization
point -- it guarantees jobs run one at a time regardless of how many HTTP
requests Flask is handling at once.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS_DIR))

import padb_config  # noqa: E402
import padb_convert_site  # noqa: E402
from padb_run import parse_pod_analytics  # noqa: E402
from padb_scheduler import (  # noqa: E402
    TASK_PREFIX, create_task, delete_task, discover_all_padb_tasks,
    format_schedule_summary, query_task,
)

DEFAULTS = padb_config.load_defaults()
DATA_DIR = Path(DEFAULTS["data_dir"])
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Result file serving -- browsers block navigating a rendered http:// page to
# a file:// link (silently, with no console error), so result HTML is served
# through the app itself instead. Each result directory we've ever pointed at
# gets a short token; /results/<token>/<filename> serves any file within that
# one directory (send_from_directory blocks path traversal outside it), and
# because the URL path itself mirrors the directory structure, the generated
# gallery's own relative links between sibling plot HTML files resolve
# correctly in the browser without any rewriting.
# ---------------------------------------------------------------------------
_RESULT_DIRS: dict[str, str] = {}
_result_dirs_lock = threading.Lock()


def _result_url(index_path: str | None) -> str | None:
    if not index_path:
        return None
    idx = Path(index_path)
    token = hashlib.sha1(str(idx.parent).encode("utf-8")).hexdigest()[:16]
    with _result_dirs_lock:
        _RESULT_DIRS[token] = str(idx.parent)
    return f"/results/{token}/{idx.name}"

# ---------------------------------------------------------------------------
# Execution queue
# ---------------------------------------------------------------------------
_job_queue: "queue.Queue[str]" = queue.Queue()
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_next_id = 0
_id_lock = threading.Lock()


def _new_job_id() -> str:
    global _next_id
    with _id_lock:
        _next_id += 1
        return str(_next_id)


def _append_log(job_id: str, line: str) -> None:
    with _jobs_lock:
        _jobs[job_id]["log"].append(line)


def _job_index_path(job_dir: Path, cfg: dict) -> Path | None:
    """Path to that job's results_dir/index.html, if it exists on disk."""
    results_dir = cfg.get("results_dir")
    if not results_dir:
        return None
    idx = job_dir / results_dir / "index.html"
    return idx if idx.is_file() else None


def _remove_results_dir_except_sao(results_dir: Path) -> list[str]:
    """Delete results_dir, preserving any .sao file(s) found inside it in
    place. A .sao is PADB's saved analysis object for that extraction --
    binary, site-specific DUT serial data (see padb_convert_site.py), not
    cheap derived output like the CSVs/HTML/PNGs a job.json's own plotting
    step produces. Losing it means a full re-extraction against the real
    Oracle DB to get it back, so "delete this job's local data" should not
    silently take it with everything else. Returns the relative paths of any
    .sao file(s) kept -- empty if none were found, in which case results_dir
    is removed exactly as before this existed."""
    sao_paths = set(results_dir.rglob("*.sao"))
    if not sao_paths:
        shutil.rmtree(results_dir)
        return []
    for p in sorted(results_dir.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        if p in sao_paths:
            continue
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            try:
                p.rmdir()  # only succeeds once empty -- leaves ancestors of a kept .sao in place
            except OSError:
                pass
    return sorted(str(p.relative_to(results_dir)) for p in sao_paths)


def _resolve_results_dir(job_path: Path, cfg: dict) -> Path | None:
    """Absolute, resolved results_dir for a job.json, or None if it has none.
    Used by delete_jobs() to decide whether a results_dir is safe to remove --
    padb_make_v2_job.py deliberately has every plot job for one pod share a
    single results_dir, so deleting one plot job.json must not destroy
    output that a surviving sibling job.json still points at."""
    results_dir = cfg.get("results_dir")
    if not results_dir:
        return None
    return (job_path.parent / results_dir).resolve()


def _find_v2_siblings(job_path: Path, run_cfg: dict) -> list[Path]:
    """Sibling *_v2_job.json plot jobs for a V2 run job (*_run_job.json). A
    naive glob on "{stem}_*v2_job.json" isn't enough: one job's stem can be a
    literal prefix of a different, unrelated pod's longer stem -- a real
    case, "maxpower3" vs "MaxPower3_v2" -- which would otherwise make
    maxpower3_run_job.json's sibling search also match MaxPower3_v2's own
    plot jobs. Filtered by checking each candidate's own csv_path actually
    points into *this* run job's results_dir, not just name-matches -- but
    only when csv_path is actually set: older plot jobs use an "analytic"
    key instead (padb_v2.py's own CSV-guessing fallback), and those have no
    csv_path to check at all, so they're passed through unfiltered rather
    than incorrectly excluded."""
    name = job_path.name
    if not name.endswith("_run_job.json"):
        return []
    stem = name[: -len("_run_job.json")]
    run_results_dir = (run_cfg.get("results_dir") or "").lower()
    siblings = []
    for candidate in sorted(job_path.parent.glob(f"{stem}_*v2_job.json")):
        try:
            plot_cfg = json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        csv_path = plot_cfg.get("csv_path")
        if csv_path and run_results_dir and run_results_dir not in csv_path.lower():
            continue
        siblings.append(candidate)
    return siblings


def _job_result_index_path(job_path: Path, cfg: dict) -> Path | None:
    """The results index a user actually cares about for this job. For a V2
    run job (*_run_job.json, mode=interactive), that's the merged gallery in
    the sibling plot jobs' shared results_dir, NOT the run job's own
    extraction-summary page -- the two are different results_dir values by
    design (padb_make_v2_job.py: "All plot jobs for one pod share one
    results_dir"). Falls back to the job's own index if no sibling has one
    yet (e.g. extraction ran but plots haven't been built)."""
    if cfg.get("mode") == "interactive" and job_path.name.endswith("_run_job.json"):
        for plot_job in _find_v2_siblings(job_path, cfg):
            try:
                plot_cfg = json.loads(plot_job.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            idx = _job_index_path(plot_job.parent, plot_cfg)
            if idx:
                return idx
    return _job_index_path(job_path.parent, cfg)


def _stream(cmd: list[str], job_id: str) -> int:
    _append_log(job_id, f"$ {' '.join(cmd)}")
    # Without this, Python defaults to block-buffered (not line-buffered)
    # stdout when writing to a pipe rather than a real terminal -- status
    # lines padb_run.py/padb_batch.py print (e.g. "Waiting for existing
    # PADB-R.exe (PID X) to exit...") could sit unflushed for the entire
    # wait, making a job that's correctly queued behind another PADB-R.exe
    # instance look indistinguishable from a real hang in the live status
    # panel. Confirmed root cause 2026-08-13 (project_webapp_stdout_buffering
    # memory); final log content was always complete once a job finished
    # (process exit flushes everything) -- this only affects live visibility.
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(TOOLS_DIR), env=env,
    )
    with _jobs_lock:
        _jobs[job_id]["proc"] = proc
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            _append_log(job_id, line.rstrip("\n"))
        proc.wait()
        return proc.returncode
    finally:
        with _jobs_lock:
            _jobs[job_id]["proc"] = None


def _run_v2_siblings(job_path: Path, job_id: str, run_cfg: dict) -> tuple[bool, str | None]:
    """After a V2 extraction job (*_run_job.json) succeeds, auto-run every
    sibling *_v2_job.json plot job -- completes the full V2 flow instead of
    leaving the plot-build step to be run by hand. Returns (ok, index_path)
    where index_path is the merged V2 results gallery, if one was written."""
    siblings = _find_v2_siblings(job_path, run_cfg)
    if not siblings:
        _append_log(job_id, "(no sibling *_v2_job.json plot jobs found to auto-run)")
        return True, None
    ok = True
    result_index = None
    for plot_job in siblings:
        _append_log(job_id, f"\n--- Building plots: {plot_job.name} ---")
        rc = _stream([sys.executable, str(TOOLS_DIR / "padb_v2.py"), str(plot_job)], job_id)
        if rc != 0:
            ok = False
            continue
        try:
            plot_cfg = json.loads(plot_job.read_text(encoding="utf-8"))
            idx = _job_index_path(plot_job.parent, plot_cfg)
            if idx:
                result_index = str(idx)
        except (json.JSONDecodeError, OSError):
            pass
    return ok, result_index


def _worker() -> None:
    while True:
        job_id = _job_queue.get()
        with _jobs_lock:
            job = _jobs[job_id]
            if job.get("cancel_requested"):
                # Aborted while still queued -- never actually launched, so
                # there's no process to kill, just skip straight to the
                # terminal state.
                job["status"] = "cancelled"
                _job_queue.task_done()
                continue
            job["status"] = "running"
            job["started"] = time.monotonic()
        job_path = Path(job["path"])
        ok = False
        result_index = None
        try:
            cfg = json.loads(job_path.read_text(encoding="utf-8"))
            if "pod" in cfg:
                cmd = [sys.executable, str(TOOLS_DIR / "padb_run.py"), str(job_path)]
                if job.get("dry_run"):
                    cmd.append("--dry-run")
                rc = _stream(cmd, job_id)
                ok = rc == 0
                if ok and cfg.get("mode") == "interactive" and not job.get("dry_run"):
                    ok, result_index = _run_v2_siblings(job_path, job_id, cfg)
            else:
                # V2 plot job (csv_path/analytic key, no pod) -- rebuilds HTML from an
                # already-extracted CSV via padb_v2.py directly. No PADB-R.exe involved,
                # so --dry-run has no equivalent here and is simply ignored.
                rc = _stream([sys.executable, str(TOOLS_DIR / "padb_v2.py"), str(job_path)], job_id)
                ok = rc == 0
            if result_index is None:
                idx = _job_result_index_path(job_path, cfg)
                result_index = str(idx) if idx else None
        except Exception as exc:  # keep the worker alive regardless of what a job does
            _append_log(job_id, f"ERROR: {exc}")
            ok = False
        with _jobs_lock:
            job["status"] = "cancelled" if job.get("cancel_requested") else ("done" if ok else "failed")
            job["elapsed_s"] = round(time.monotonic() - job["started"], 1)
            job["result_index"] = result_index
        _job_queue.task_done()


threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/results/<token>/<path:filename>")
def serve_result(token, filename):
    dir_path = _RESULT_DIRS.get(token)
    if not dir_path:
        abort(404)
    return send_from_directory(dir_path, filename)


@app.route("/api/upload-pod", methods=["POST"])
def upload_pod():
    f = request.files.get("pod")
    if not f or not f.filename:
        return jsonify(error="No file provided"), 400
    filename = secure_filename(f.filename)
    if not filename.lower().endswith(".pod"):
        return jsonify(error="Not a .pod file"), 400
    dest = DATA_DIR / filename
    # Overwriting an existing pod of the same name is fine -- this route only
    # stages the pod for job generation, it doesn't create anything durable.
    # padb_make_job.py/padb_make_v2_job.py already guard the thing that
    # actually matters (not clobbering an existing job.json) behind --force.
    f.save(str(dest))
    try:
        analytics = parse_pod_analytics(dest)
    except Exception as exc:
        return jsonify(error=f"Saved but could not parse: {exc}"), 500
    return jsonify(
        pod_path=str(dest),
        pod_name=dest.name,
        analytics=[
            {"index": a["index"], "type": a["type"], "name": a["name"],
             "output_file": a["output_file"]}
            for a in analytics
        ],
    )


@app.route("/api/sites")
def list_sites():
    sites = padb_convert_site.load_sites()
    return jsonify(sites={name: cfg.get("suffix", "") for name, cfg in sites.items()})


@app.route("/api/convert-pod", methods=["POST"])
def convert_pod_route():
    body = request.get_json(force=True) or {}
    pod_path = body.get("pod_path")
    target_site = body.get("target_site")
    force = bool(body.get("force"))

    if not pod_path or not Path(pod_path).exists():
        return jsonify(error="pod_path missing or does not exist"), 400
    sites = padb_convert_site.load_sites()
    if target_site not in sites:
        return jsonify(error=f"unknown site: {target_site}"), 400

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            dest = padb_convert_site.convert_pod(Path(pod_path).resolve(), target_site, sites, force=force)
    except SystemExit as exc:
        return jsonify(error=str(exc), log=buf.getvalue()), 400
    return jsonify(dest_path=str(dest), dest_name=dest.name, log=buf.getvalue())


@app.route("/api/convert-job", methods=["POST"])
def convert_job_route():
    body = request.get_json(force=True) or {}
    paths = body.get("paths") or []
    target_site = body.get("target_site")
    force = bool(body.get("force"))

    if not paths:
        return jsonify(error="paths must be a non-empty list"), 400
    sites = padb_convert_site.load_sites()
    if target_site not in sites:
        return jsonify(error=f"unknown site: {target_site}"), 400

    results = []
    for p in paths:
        job_path = Path(p).resolve()
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                dest = padb_convert_site.convert_job(job_path, target_site, sites, force=force)
            results.append({"path": p, "ok": True, "dest_name": dest.name, "log": buf.getvalue()})
        except SystemExit as exc:
            results.append({"path": p, "ok": False, "error": str(exc), "log": buf.getvalue()})
        except Exception as exc:
            results.append({"path": p, "ok": False, "error": str(exc), "log": buf.getvalue()})
    return jsonify(results=results)


@app.route("/api/generate-job", methods=["POST"])
def generate_job():
    body = request.get_json(force=True) or {}
    pod_path = body.get("pod_path")
    mode = body.get("mode", "simple")
    module = (body.get("module") or "").strip()
    min_date = (body.get("min_date") or "").strip()
    max_date = (body.get("max_date") or "").strip()
    force = bool(body.get("force"))

    if not pod_path or not Path(pod_path).exists():
        return jsonify(error="pod_path missing or does not exist"), 400
    if mode not in ("legacy", "simple", "interactive"):
        return jsonify(error=f"invalid mode: {mode}"), 400

    script = "padb_make_v2_job.py" if mode == "interactive" else "padb_make_job.py"
    cmd = [sys.executable, str(TOOLS_DIR / script), pod_path]
    if mode != "interactive":
        cmd += ["--mode", mode]
    if module:
        cmd += ["--module", module]
    else:
        cmd += ["--no-publish"]
    if min_date:
        cmd += ["--min-date", min_date]
    if max_date:
        cmd += ["--max-date", max_date]
    if force:
        cmd += ["--force"]

    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(TOOLS_DIR))

    stem = Path(pod_path).stem
    pod_dir = Path(pod_path).parent
    if mode == "interactive":
        candidates = [pod_dir / f"{stem}_run_job.json"] + sorted(pod_dir.glob(f"{stem}_*v2_job.json"))
    else:
        candidates = [pod_dir / f"{stem}_job.json"]

    jobs = []
    for p in candidates:
        if p.exists():
            jobs.append({"path": str(p), "name": p.name, "content": p.read_text(encoding="utf-8")})

    return jsonify(stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode, jobs=jobs)


def _job_kind(cfg: dict) -> str:
    if "pod" in cfg:
        return "run"
    if "csv_path" in cfg or "analytic" in cfg:
        return "plot"
    return "unknown"


@app.route("/api/jobs")
def list_jobs():
    scheduled_tasks = {t.upper() for t in discover_all_padb_tasks()}
    jobs = []
    for p in sorted(DATA_DIR.glob("*_job.json")):
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {}
        idx = _job_result_index_path(p, cfg)
        index_path = str(idx) if idx else None
        last_run = datetime.fromtimestamp(idx.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if idx else None
        task_name = TASK_PREFIX + p.stem
        is_scheduled = task_name.upper() in scheduled_tasks
        schedule_summary = ""
        if is_scheduled:
            info = query_task(task_name)
            if info:
                schedule_summary = format_schedule_summary(info)
        kind = _job_kind(cfg)
        jobs.append({
            "path": str(p),
            "name": p.name,
            "description": cfg.get("description", ""),
            "mode": cfg.get("mode", "legacy" if kind == "run" else "v2 plot"),
            "pod": cfg.get("pod", ""),
            "kind": kind,
            "index_path": index_path,
            "index_url": _result_url(index_path),
            "last_run": last_run,
            "scheduled": is_scheduled,
            "schedule_summary": schedule_summary,
        })
    return jsonify(jobs=jobs)


@app.route("/api/schedule", methods=["POST"])
def schedule_jobs():
    body = request.get_json(force=True) or {}
    paths = body.get("paths") or []
    schedule_type = (body.get("schedule_type") or "").upper()
    days = body.get("days") or []
    start_time = (body.get("start_time") or "").strip()

    if not paths:
        return jsonify(error="paths must be a non-empty list"), 400
    if schedule_type not in ("DAILY", "WEEKLY"):
        return jsonify(error="schedule_type must be DAILY or WEEKLY"), 400
    if not start_time:
        return jsonify(error="start_time (HH:MM) is required"), 400

    results = []
    for p in paths:
        job_path = Path(p)
        task_name = TASK_PREFIX + job_path.stem
        ok, err = create_task(task_name, str(job_path), schedule_type, days, start_time)
        results.append({"path": p, "task_name": task_name, "ok": ok, "error": err})
    return jsonify(results=results)


@app.route("/api/unschedule", methods=["POST"])
def unschedule_jobs():
    body = request.get_json(force=True) or {}
    paths = body.get("paths") or []
    if not paths:
        return jsonify(error="paths must be a non-empty list"), 400

    results = []
    for p in paths:
        job_path = Path(p)
        task_name = TASK_PREFIX + job_path.stem
        ok, err = delete_task(task_name)
        results.append({"path": p, "task_name": task_name, "ok": ok, "error": err})
    return jsonify(results=results)


@app.route("/api/delete-job", methods=["POST"])
def delete_jobs():
    """Delete one or more job.json files, optionally along with their local
    results_dir. Deliberately never touches the source .pod file (shared
    across jobs, not job-specific output), any .sao file(s) found inside
    results_dir (see _remove_results_dir_except_sao), or any already-
    published copy on the network share (a shared location other people may
    rely on -- out of scope for a local delete button, same reasoning as
    _publish() never being invoked from a delete path)."""
    body = request.get_json(force=True) or {}
    paths = body.get("paths") or []
    delete_data = bool(body.get("delete_data"))
    if not paths:
        return jsonify(error="paths must be a non-empty list"), 400

    targets = [Path(p).resolve() for p in paths]
    target_set = {str(p) for p in targets}

    # Results_dir values used by every *other* job.json NOT in this deletion
    # batch -- computed up front, before any deletion happens, so a shared
    # results_dir (see padb_make_v2_job.py: "all plot jobs for one pod share
    # one results_dir") already referenced by a surviving job.json is never
    # rmtree'd out from under it, even if this batch includes some (but not
    # all) of that pod's other job.json files.
    surviving_results_dirs: set[str] = set()
    for p in DATA_DIR.glob("*_job.json"):
        rp = p.resolve()
        if str(rp) in target_set:
            continue
        try:
            other_cfg = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rd = _resolve_results_dir(rp, other_cfg)
        if rd:
            surviving_results_dirs.add(str(rd).lower())

    scheduled_tasks = {t.upper() for t in discover_all_padb_tasks()}
    results = []
    for job_path in targets:
        entry = {"path": str(job_path), "ok": False, "deleted_results": False, "note": ""}
        if not job_path.exists():
            entry["error"] = "not found"
            results.append(entry)
            continue
        try:
            cfg = json.loads(job_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {}

        # Unschedule first -- a deleted job.json left behind in a scheduled
        # task would otherwise fire later against a file that no longer
        # exists, and just fail confusingly (same orphan-task concern
        # padb_scheduler.py's own orphan detection exists for).
        task_name = TASK_PREFIX + job_path.stem
        if task_name.upper() in scheduled_tasks:
            delete_task(task_name)

        if delete_data:
            results_dir = _resolve_results_dir(job_path, cfg)
            if results_dir and results_dir.is_dir():
                if str(results_dir).lower() in surviving_results_dirs:
                    entry["note"] = f"results_dir kept -- still used by another job.json ({results_dir})"
                else:
                    try:
                        preserved = _remove_results_dir_except_sao(results_dir)
                        entry["deleted_results"] = True
                        if preserved:
                            entry["note"] = f"kept .sao file(s): {', '.join(preserved)}"
                    except OSError as exc:
                        entry["note"] = f"could not remove results_dir: {exc}"

        try:
            job_path.unlink()
            entry["ok"] = True
        except OSError as exc:
            entry["error"] = str(exc)
        results.append(entry)
    return jsonify(results=results)


@app.route("/api/execute-job", methods=["POST"])
def execute_job():
    body = request.get_json(force=True) or {}
    paths = body.get("paths") or []
    dry_run = bool(body.get("dry_run"))
    if not paths:
        return jsonify(error="paths must be a non-empty list"), 400

    # Run/extraction jobs must execute before plot jobs regardless of
    # selection order -- a plot job reads the CSV a run job just produced
    # (or refreshed), so queuing a stale-relative-to-selection plot job
    # ahead of its own run job would build from an old or missing CSV.
    # Table order in the UI is alphabetical (see list_jobs()'s sorted glob),
    # which does NOT guarantee "<stem>_run_job.json" sorts before that same
    # pod's "<stem>_<analytic>_v2_job.json" siblings -- e.g. an analytic
    # name starting before "run" alphabetically. Stable sort so relative
    # order within each group (run vs. plot) is otherwise preserved.
    def _kind_rank(p: str) -> int:
        try:
            cfg = json.loads(Path(p).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {}
        return 0 if _job_kind(cfg) == "run" else 1
    paths = sorted(paths, key=_kind_rank)

    job_ids = []
    for p in paths:
        job_path = Path(p)
        if not job_path.exists():
            return jsonify(error=f"job not found: {p}"), 400
        job_id = _new_job_id()
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "queued", "path": str(job_path), "name": job_path.name,
                "log": [], "started": None, "elapsed_s": 0, "dry_run": dry_run,
                "result_index": None, "proc": None, "cancel_requested": False,
            }
        _job_queue.put(job_id)
        job_ids.append(job_id)
    return jsonify(job_ids=job_ids)


@app.route("/api/job-abort/<job_id>", methods=["POST"])
def job_abort(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return jsonify(error="unknown job_id"), 404
        if job["status"] not in ("queued", "running"):
            return jsonify(error=f"job is already {job['status']}, nothing to abort"), 400
        job["cancel_requested"] = True
        proc = job.get("proc")
        if job["status"] == "queued":
            # Nothing has launched yet, so there's nothing for _worker() to
            # kill -- flip straight to the terminal state now rather than
            # waiting for _worker() to dequeue it, which could be minutes
            # away behind a slow PADB-R.exe extraction ahead of it in the
            # single-worker FIFO. _worker() still checks cancel_requested
            # itself (in case it races this and dequeues first) so it never
            # actually launches a job already marked cancelled here.
            job["status"] = "cancelled"
    if proc is not None and proc.poll() is None:
        # taskkill /T kills the whole process tree, not just the immediate
        # python.exe child -- necessary because PADB-R.exe runs as a
        # grandchild (padb_run.py -> padb_batch.py's subprocess.run), and a
        # plain proc.terminate() would only kill the python wrapper, leaving
        # PADB-R.exe orphaned and still running. Two PADB-R.exe instances
        # running concurrently interfere with each other (see module
        # docstring), so an orphan here would corrupt the *next* queued job,
        # not just fail to actually stop this one.
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, text=True,
        )
    # If still queued (never dequeued yet), _worker() checks cancel_requested
    # itself and skips straight to "cancelled" without launching anything.
    return jsonify(ok=True)


@app.route("/api/job-status/<job_id>")
def job_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return jsonify(error="unknown job_id"), 404
        elapsed = job["elapsed_s"]
        if job["status"] == "running" and job["started"] is not None:
            elapsed = round(time.monotonic() - job["started"], 1)
        result_index = job.get("result_index")
        return jsonify(
            status=job["status"], name=job["name"], elapsed_s=elapsed,
            log_tail="\n".join(job["log"][-200:]),
            result_index=result_index,
            result_index_url=_result_url(result_index),
        )


def main() -> None:
    url = "http://127.0.0.1:5000"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, threaded=True, debug=False)


if __name__ == "__main__":
    main()
