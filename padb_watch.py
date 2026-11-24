"""padb_watch.py -- watch a folder for new parquet files and run the analysis on each, once.

v3.0 stage 7. PadbLoader (the Oracle extractor) writes parquet files on a schedule; this tool is the other half:
it notices each new, COMPLETE file and runs `padb_analyze` on it into its own results folder, so nobody has to
carry a file from one tool to the other.

    py padb_watch.py --padbloader --out D:\\padb\\watch          # follow PadbLoader's scheduled output (loops)
    py padb_watch.py --watch "D:\\exports" --out D:\\padb\\watch --once   # one pass, for Task Scheduler
    py padb_watch.py --discover                                   # print where PadbLoader writes, and why

WHERE PadbLoader WRITES (read from its source, 2026-11-24; see reference_padbloader_output_locations):
  export folder = the folder picked in its Data Source Editor (`user-settings.json` -> ExportDirectory), else
  `Export:OutputDirectory` (appsettings.local.json / appsettings.json), else `<root>\\Exports`, with
  root = %LOCALAPPDATA%\\PadbLoader (or env PADBLOADER_DATA_DIR). Parquet goes to `<export>\\PadbLoader Data\\`
  (manual pulls) and `<export>\\PadbLoader Data\\Scheduled\\<schedule> - <owner>\\<name>_<yyyyMMdd-HHmmss>.parquet`
  (scheduled pulls, one new file per run). `--padbloader` watches the Scheduled folder.

WHAT THAT MEANS FOR A WATCHER (each is handled, and pinned by qa/qa_watch.py):
  * the final parquet is written IN PLACE at its real name (no temp-then-rename): a half-written file is visible
    until its footer is written -> a file counts as COMPLETE only when it is not being modified (age >= --settle,
    size and mtime unchanged on a re-stat) AND it ends with the PAR1 magic AND its footer parses;
  * parts live in `.staging-*` and `<file>.parquet.parts` folders (they hold *.parquet!) and the merged-export
    writer uses `.partial`: dot-prefixed folders/files, `*.parts` folders and non-`.parquet` names are ignored;
  * an empty / failed / cancelled run DELETES its file and the pruner deletes runs beyond KeepLastRuns (default
    14): a file may vanish at any time, and an analysis reading it in place would lose its source -> every input
    is SNAPSHOT (hard link when on one volume, else a copy) into the output folder BEFORE it is analysed;
  * a file is identified by (path, size, mtime): a re-written file is a new version and is analysed again.

RETENTION: snapshots are the heavy part (a snapshot is the only copy once PadbLoader prunes the original), so they are
trimmed: `--keep-inputs N` (default 14 = PadbLoader's own window) keeps the newest N runs' snapshots per stream and
`--keep-results N` (default 0 = keep every result) the result folders; newest = by the run stamp in the file name. Only
FINISHED runs are pruned, the ledger entry stays (marked pruned) so a pruned run is not analysed again, a delete is only
ever done on a folder at exactly the expected place inside the output folder with no symlink / junction on the way, and
`--prune-now [--dry-run]` applies it once.

RELIABILITY: polling (works on shares); a ledger (`watch_ledger.json`, atomic writes) so nothing is processed
twice across restarts; the file is marked `running` before the analysis starts, so a crash mid-run is retried;
retry with a delay, give up after --max-attempts (and say so); one analysis at a time; one watcher per output
folder (a lock with a liveness check); a hung analysis is killed (whole process tree) after --timeout.
Outputs: `<out>/<stream>/<file stem>/` (what `padb_analyze` writes), `<out>/_inputs/` (the snapshots),
`<out>/index.html` (every run, newest first), `watch_log.txt`, `watch_status.json`.

Exit codes (--once): 0 = nothing failed (or nothing to do), 1 = at least one analysis failed, 2 = bad arguments /
another watcher holds the output folder.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html as _html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = "watch_ledger.json"
LOCK = "watch.lock"
STATUS = "watch_status.json"
LOG = "watch_log.txt"
SCHEMA = "padb-watch-ledger/1"
_STAMP = re.compile(r"^(?P<base>.+)_(?P<stamp>\d{8}-\d{6})(?:_[^.\\/]+)?\.parquet$", re.I)
_MANUAL = re.compile(r"^(?P<base>padbloader)_(?P<stamp>\d{8}_\d{6})\.parquet$", re.I)


def _ascii(s) -> str:
    return str(s).encode("ascii", "replace").decode("ascii")


def _now_iso() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------------------------------ discovery
def discover_padbloader(env=None, local_appdata=None, extra_config=None) -> dict:
    """Where PadbLoader writes parquet, resolved the way ITS code does (ExportFolder.Resolve): the folder the user
    picked (user-settings.json ExportDirectory) > Export:OutputDirectory (appsettings.local.json, then any
    `extra_config` appsettings file) > <root>\\Exports. -> {root, export_dir, data_dir, scheduled_dir, source, notes}."""
    env = os.environ if env is None else env
    local = local_appdata if local_appdata is not None else env.get("LOCALAPPDATA", "")
    override = (env.get("PADBLOADER_DATA_DIR") or "").strip()
    root = Path(override) if override else Path(local) / "PadbLoader"
    notes = []

    def read_json(p):
        try:
            return json.loads(Path(p).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None

    def ci(d, key):
        if isinstance(d, dict):
            for k, v in d.items():
                if k.lower() == key.lower():
                    return v
        return None

    choice = ci(read_json(root / "user-settings.json"), "ExportDirectory")
    configured = None
    for cfg in [root / "appsettings.local.json"] + ([Path(extra_config)] if extra_config else []):
        v = ci(ci(read_json(cfg), "Export"), "OutputDirectory")
        if isinstance(v, str) and v.strip():
            configured = v
            break
    if isinstance(choice, str) and choice.strip():
        export, source = choice, "the export folder chosen in PadbLoader (user-settings.json ExportDirectory)"
    elif configured:
        export, source = configured, "Export:OutputDirectory in an appsettings file"
    else:
        export, source = str(root / "Exports"), "PadbLoader's default (<root>\\Exports)"
    if not Path(export).is_absolute():
        notes.append("the export folder %r is relative; PadbLoader resolves it against its own folder, which this "
                     "tool cannot know -- pass --watch with the real path" % export)
    data = Path(export) / "PadbLoader Data"
    return {"root": str(root), "export_dir": export, "data_dir": str(data), "scheduled_dir": str(data / "Scheduled"),
            "source": source, "notes": notes}


# ------------------------------------------------------------------------------------------ scanning
def is_ignored_dir(name: str) -> bool:
    n = name.lower()
    return n.startswith(".") or n.endswith(".parts")


def is_candidate(name: str) -> bool:
    n = name.lower()
    return n.endswith(".parquet") and not n.startswith(".")


def parse_name(name: str):
    """'Spurs_20261124-115925.parquet' -> ('Spurs', '20261124-115925'); manual 'padbloader_20261120_090341' -> likewise."""
    m = _STAMP.match(name) or _MANUAL.match(name)
    return (m.group("base"), m.group("stamp")) if m else (None, None)


def scan(root, depth: int = 1) -> list:
    """Candidate parquet files under `root`: the root itself and sub-folders down to `depth`, skipping dot-prefixed and
    `*.parts` folders. -> [{path, rel, stream, size, mtime_ns}] oldest first."""
    root = Path(root)
    out = []

    def walk(d: Path, level: int):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            try:
                if e.is_file(follow_symlinks=False):
                    if is_candidate(e.name):
                        st = e.stat()
                        rel = e.path[len(str(root)):].lstrip("\\/").replace("\\", "/")
                        out.append({"path": e.path, "rel": rel, "stream": rel.rsplit("/", 1)[0] if "/" in rel else "",
                                    "size": st.st_size, "mtime_ns": st.st_mtime_ns})
                elif e.is_dir(follow_symlinks=False) and level < depth and not is_ignored_dir(e.name):
                    walk(Path(e.path), level + 1)
            except OSError:
                continue
    walk(root, 0)
    out.sort(key=lambda c: (c["mtime_ns"], c["rel"]))
    return out


# ------------------------------------------------------------------------------------------ completeness
def is_complete(path, settle_s: float = 30.0, recheck_s: float = 1.0):
    """-> (ok, reason). A parquet is complete when it is not being modified (age >= settle_s, and size + mtime
    unchanged across a re-stat) AND it starts and ends with the PAR1 magic AND its footer parses. PadbLoader writes
    the final file in place, so a half-written file is visible at its real name until the footer is written."""
    p = Path(path)
    try:
        st1 = p.stat()
    except OSError as exc:
        return False, "cannot stat: %s" % exc
    if st1.st_size < 12:
        return False, "empty or too small (%d bytes)" % st1.st_size
    age = time.time() - st1.st_mtime
    if age < settle_s:
        return False, "still being written (modified %.0f s ago, settle %.0f s)" % (age, settle_s)
    if recheck_s > 0:
        time.sleep(recheck_s)
        try:
            st2 = p.stat()
        except OSError as exc:
            return False, "cannot stat: %s" % exc
        if (st2.st_size, st2.st_mtime_ns) != (st1.st_size, st1.st_mtime_ns):
            return False, "size or modification time changed while checking"
    try:
        with open(p, "rb") as f:
            head = f.read(4)
            f.seek(-4, os.SEEK_END)
            tail = f.read(4)
    except OSError as exc:
        return False, "cannot read: %s" % exc
    if head != b"PAR1" or tail != b"PAR1":
        return False, "not a finished parquet (missing PAR1 magic -- truncated or still being written)"
    try:
        import pyarrow.parquet as pq
        md = pq.ParquetFile(str(p)).metadata
        _ = md.num_rows
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 -- any failure to parse the footer means "not usable yet"
        return False, "footer unreadable: %s" % _ascii(exc)[:120]
    return True, "complete"


# ------------------------------------------------------------------------------------------ ledger
class Ledger:
    """JSON ledger of what has been done, keyed by the file's path relative to its watch root. Atomic writes; a
    corrupt file is kept as `.corrupt-<time>` and a fresh ledger started (never silently re-analyse nothing)."""

    def __init__(self, path):
        self.path = Path(path)
        self.entries = {}
        self.warning = None
        self.load()

    def load(self):
        if not self.path.is_file():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(d, dict) or not isinstance(d.get("entries"), dict):
                raise ValueError("not a ledger")
            self.entries = d["entries"]
        except (OSError, ValueError) as exc:
            keep = self.path.with_name(self.path.name + ".corrupt-" + time.strftime("%Y%m%d%H%M%S"))
            try:
                os.replace(self.path, keep)
            except OSError:
                pass
            self.warning = "ledger unreadable (%s); kept as %s and started a new one" % (_ascii(exc)[:80], keep.name)
            self.entries = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"schema": SCHEMA, "entries": self.entries}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def key(root, rel) -> str:
        return "%s|%s" % (str(Path(root)).replace("\\", "/").lower(), rel)

    def needs_work(self, key, sig, max_attempts, now=None, retry_failed=False) -> bool:
        e = self.entries.get(key)
        if e is None or e.get("sig") != list(sig):
            return True                                   # never seen, or the file was re-written (a new version)
        st = e.get("status")
        if st == "done":
            return False
        if st == "running":
            return True                                   # the watcher died mid-run: try again
        if st == "failed":
            return e.get("attempts", 0) < max_attempts and (now or time.time()) >= e.get("next_retry_ts", 0)
        if st == "gave_up":
            return bool(retry_failed)
        return st == "vanished"                           # it came back


# ------------------------------------------------------------------------------------------ processes
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259             # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_tree(pid: int):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def acquire_lock(out_dir):
    """One watcher per output folder. -> (ok, message). A lock whose process is gone is taken over."""
    lock = Path(out_dir) / LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.is_file():
        try:
            held = json.loads(lock.read_text(encoding="utf-8"))
            if _pid_alive(int(held.get("pid", 0))) and int(held["pid"]) != os.getpid():
                return False, "another watcher (pid %s, started %s) already holds %s" % (held["pid"], held.get("started"), out_dir)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    lock.write_text(json.dumps({"pid": os.getpid(), "started": _now_iso()}), encoding="utf-8")
    return True, "locked"


def release_lock(out_dir):
    lock = Path(out_dir) / LOCK
    try:
        if lock.is_file() and json.loads(lock.read_text(encoding="utf-8")).get("pid") == os.getpid():
            lock.unlink()
    except (OSError, ValueError):
        pass


def _link_below(base, path) -> bool:
    """True when any component of `path` below `base` is a symlink / junction (never delete through one)."""
    cur = Path(base)
    try:
        rel = Path(path).relative_to(base)
    except ValueError:
        return True
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(cur)):
            return True
    return False


def _tree_size(path) -> int:
    total = 0
    for dp, _dn, fn in os.walk(path):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def snapshot_input(src, dest_dir):
    """Keep our own copy of the input before analysing it (PadbLoader prunes old runs and deletes failed ones):
    a hard link when source and destination share a volume (instant, no extra disk), else a copy. -> (path, how)."""
    src = Path(src)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        try:
            dest.unlink()
        except OSError:
            pass
    try:
        os.link(src, dest)
        how = "hard link"
    except (OSError, NotImplementedError):
        shutil.copy2(src, dest)
        how = "copy"
    if dest.stat().st_size != src.stat().st_size:
        raise OSError("snapshot size differs from the source (the file changed while copying)")
    return dest, how


def run_analysis(snapshot, out_dir, opts, timeout_s, argv_prefix=None):
    """Run `padb_analyze` on one input in its own process (a crash or runaway cannot take the watcher down; the whole
    process tree is killed on timeout). -> {ok, error, analyses: [{name,status,views,index,tier,issues}]}."""
    argv = list(argv_prefix or [sys.executable, str(HERE / "padb_analyze.py")])
    argv += ["--input", str(snapshot), "--out", str(out_dir)]
    for flag, val in (("--views", opts.get("views")), ("--limits-file", opts.get("limits_file")),
                      ("--filters-file", opts.get("filters_file")), ("--publish-to", opts.get("publish_to"))):
        if val:
            argv += [flag, str(val)]
    if opts.get("pdf_report"):
        argv.append("--pdf-report")
    flags = 0x00000200 if os.name == "nt" else 0           # CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=flags)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            proc.communicate(timeout=15)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": "timed out after %d s (analysis killed)" % timeout_s, "analyses": []}
    try:
        res = json.loads(stdout)
    except ValueError:
        tail = _ascii((stderr or stdout or "").strip().splitlines()[-1:] or ["no output"])
        return {"ok": False, "error": "padb_analyze produced no result (exit %s): %s" % (proc.returncode, tail[:200]), "analyses": []}
    analyses = [{"name": a.get("name"), "status": a.get("status"), "views": a.get("views", []), "index": a.get("index"),
                 "tier": (a.get("tier") or {}).get("tier"), "error": a.get("error"),
                 "issues": len([i for i in (a.get("job_issues") or []) if i.get("level") == "error"])}
                for a in res.get("analyses", [])]
    ok = bool(res.get("ok")) and bool(analyses) and all(a["status"] == "ok" for a in analyses)
    err = None if ok else (res.get("error") or "; ".join("%s: %s" % (a["name"], a["error"]) for a in analyses if a["status"] != "ok")
                           or "no analysis was produced")
    return {"ok": ok, "error": err, "analyses": analyses}


# ------------------------------------------------------------------------------------------ the watcher
class Watcher:
    def __init__(self, roots, out_dir, depth=1, settle_s=30.0, recheck_s=1.0, max_attempts=3, retry_delay_s=300.0,
                 timeout_s=7200, retry_failed=False, snapshot=True, opts=None, runner=None, log=None,
                 keep_inputs=14, keep_results=0):
        self.roots = [Path(r) for r in roots]
        self.out = Path(out_dir)
        self.keep_inputs, self.keep_results = keep_inputs, keep_results
        self.depth, self.settle_s, self.recheck_s = depth, settle_s, recheck_s
        self.max_attempts, self.retry_delay_s, self.timeout_s = max_attempts, retry_delay_s, timeout_s
        self.retry_failed, self.snapshot, self.opts = retry_failed, snapshot, opts or {}
        self.runner = runner or run_analysis
        self.ledger = Ledger(self.out / LEDGER)
        self._log = log or (lambda m: print(m, flush=True))
        if self.ledger.warning:
            self.log("WARNING: " + self.ledger.warning)

    def log(self, msg):
        line = "[%s] %s" % (_now_iso(), _ascii(msg))
        self._log(line)
        try:
            self.out.mkdir(parents=True, exist_ok=True)
            with open(self.out / LOG, "a", encoding="ascii", errors="replace") as f:
                f.write(line + "\n")
        except OSError:
            pass

    @staticmethod
    def _stream_dir(stream: str) -> str:
        return re.sub(r'[<>:"|?*]', "_", stream) or "_top"

    def run_pass(self, dry_run=False) -> dict:
        s = {"scanned": 0, "new": 0, "ok": 0, "failed": 0, "gave_up": 0, "incomplete": 0, "vanished": 0}
        seen = set()
        for root in self.roots:
            if not root.is_dir():
                self.log("watch folder not found: %s" % root)
                continue
            for c in scan(root, self.depth):
                s["scanned"] += 1
                key = Ledger.key(root, c["rel"])
                seen.add(key)
                sig = (c["size"], c["mtime_ns"])
                if not self.ledger.needs_work(key, sig, self.max_attempts, retry_failed=self.retry_failed):
                    continue
                ok, why = is_complete(c["path"], self.settle_s, self.recheck_s)
                if not ok:
                    s["incomplete"] += 1
                    self.log("waiting: %s -- %s" % (c["rel"], why))
                    continue
                s["new"] += 1
                if dry_run:
                    self.log("would analyse: %s" % c["rel"])
                    continue
                self._process(root, c, key, sig, s)
        for key, e in self.ledger.entries.items():       # work that was pending but whose file has gone
            if key not in seen and e.get("status") in ("failed", "running") and key.split("|")[0] in {
                    str(r).replace("\\", "/").lower() for r in self.roots}:
                e["status"] = "vanished"
                e["error"] = "the file disappeared before it could be analysed (PadbLoader deletes failed / pruned runs)"
                s["vanished"] += 1
                self.log("vanished: %s" % key.split("|", 1)[1])
        r = self.apply_retention(dry_run=dry_run)
        s["pruned"] = r["inputs_removed"] + r["results_removed"]
        if not dry_run:
            self.ledger.save()
            self._write_status(s)
            self.write_overview()
        return s

    # ---- retention -------------------------------------------------------------------------------------
    def _prune_dir(self, d, base, depth_parent, dry_run, res, what, rel) -> bool:
        """Delete one recorded folder, but ONLY if it is exactly where the watcher puts such folders
        (`<base>/<stream>/<folder>`), strictly inside the output folder, with no symlink / junction on the way: a
        damaged or tampered ledger must never be able to delete anything else. -> True when it was (or would be) removed."""
        d = Path(d)
        if not d.exists():
            return True                                       # already gone: just record it
        try:
            ok = (d.parent.parent == Path(base) and d.parent.parent != d and not _link_below(self.out, d)
                  and (Path(base) != self.out or d.parent.name != "_inputs")        # a result folder is never in _inputs
                  and Path(base).resolve() in d.resolve().parents and self.out.resolve() in d.resolve().parents)
        except OSError:
            ok = False
        if not ok:
            res["skipped"] += 1
            self.log("retention: REFUSED to delete %s (not a folder this watcher created)" % d)
            return False
        size = _tree_size(d)
        res["bytes"] += size
        if dry_run:
            self.log("retention (dry run): would remove the %s of %s (%.1f MB)" % (what, rel, size / 1e6))
            return False
        shutil.rmtree(d, ignore_errors=True)
        self.log("retention: removed the %s of %s (%.1f MB)" % (what, rel, size / 1e6))
        return not d.exists()

    def apply_retention(self, dry_run=False) -> dict:
        """Keep the newest `keep_inputs` input snapshots and `keep_results` result folders PER STREAM (newest by the run
        stamp in the file name, else by finish time); 0 keeps all. Only finished (`done`) runs are ever pruned; the
        ledger entry stays (marked pruned) so a pruned run is not analysed again while its source file still exists."""
        res = {"inputs_removed": 0, "results_removed": 0, "bytes": 0, "skipped": 0}
        if not (self.keep_inputs or self.keep_results):
            return res
        groups = {}
        for key, e in self.ledger.entries.items():
            if e.get("status") == "done":
                groups.setdefault((e.get("root"), e.get("stream")), []).append(e)
        for items in groups.values():
            items.sort(key=lambda e: (parse_name(Path(e.get("rel", "")).name)[1] or "", e.get("finished") or ""), reverse=True)
            for rank, e in enumerate(items):
                if self.keep_inputs and rank >= self.keep_inputs and e.get("input_snapshot") and not e.get("inputs_pruned"):
                    if self._prune_dir(Path(e["input_snapshot"]).parent, self.out / "_inputs", None, dry_run, res, "input snapshot", e.get("rel")):
                        if not dry_run:
                            e["inputs_pruned"], e["input_snapshot"] = _now_iso(), None
                            res["inputs_removed"] += 1
                if self.keep_results and rank >= self.keep_results and e.get("result_dir") and not e.get("results_pruned"):
                    if self._prune_dir(e["result_dir"], self.out, None, dry_run, res, "results", e.get("rel")):
                        if not dry_run:
                            e["results_pruned"] = _now_iso()
                            res["results_removed"] += 1
        return res

    def _process(self, root, c, key, sig, s):
        prev = self.ledger.entries.get(key)
        attempts = (prev.get("attempts", 0) if prev and prev.get("sig") == list(sig) else 0) + 1
        stream = self._stream_dir(c["stream"])
        stem = Path(c["rel"]).stem
        entry = {"sig": list(sig), "status": "running", "attempts": attempts, "rel": c["rel"], "root": str(root),
                 "stream": stream, "stem": stem, "first_seen": (prev or {}).get("first_seen") or _now_iso(),
                 "last_attempt": _now_iso()}
        self.ledger.entries[key] = entry
        self.ledger.save()                                 # `running` is on disk BEFORE the work: a crash is retried
        self.log("analysing: %s (attempt %d)" % (c["rel"], attempts))
        t0 = time.time()
        try:
            src = c["path"]
            if self.snapshot:
                sig8 = hashlib.sha1(("%d:%d" % sig).encode()).hexdigest()[:8]
                src, how = snapshot_input(c["path"], self.out / "_inputs" / stream / ("%s__%s" % (stem, sig8)))
                entry["input_snapshot"] = str(src)
                entry["snapshot_how"] = how
            res_dir = self.out / stream / stem
            res = self.runner(src, res_dir, self.opts, self.timeout_s)
            entry["result_dir"] = str(res_dir)
            entry["analyses"] = res.get("analyses", [])
            ok, err = bool(res.get("ok")), res.get("error")
        except Exception as exc:  # noqa: BLE001 -- one file failing must not stop the watcher
            ok, err = False, "%s: %s" % (type(exc).__name__, _ascii(exc)[:200])
        entry["seconds"] = round(time.time() - t0, 1)
        entry["finished"] = _now_iso()
        if ok:
            entry["status"], entry["error"] = "done", None
            s["ok"] += 1
            self.log("done: %s in %.0f s" % (c["rel"], entry["seconds"]))
        else:
            entry["error"] = err
            if attempts >= self.max_attempts:
                entry["status"] = "gave_up"
                s["gave_up"] += 1
                self.log("GAVE UP on %s after %d attempts: %s" % (c["rel"], attempts, err))
            else:
                entry["status"] = "failed"
                entry["next_retry_ts"] = time.time() + self.retry_delay_s
                s["failed"] += 1
                self.log("failed: %s (attempt %d of %d, retry in %.0f s): %s" % (c["rel"], attempts, self.max_attempts,
                                                                                 self.retry_delay_s, err))
        self.ledger.save()

    def _write_status(self, s):
        counts = {}
        for e in self.ledger.entries.values():
            counts[e.get("status")] = counts.get(e.get("status"), 0) + 1
        try:
            (self.out / STATUS).write_text(json.dumps({"updated": _now_iso(), "pid": os.getpid(), "watching": [str(r) for r in self.roots],
                                                      "last_pass": s, "ledger": counts}, indent=1), encoding="utf-8")
        except OSError:
            pass

    def write_overview(self):
        """`<out>/index.html`: every run, newest first, with a link to each result index. Static, no scripts."""
        rows = []
        for e in sorted(self.ledger.entries.values(), key=lambda x: x.get("finished") or x.get("last_attempt") or "", reverse=True):
            link = "(results pruned)" if e.get("results_pruned") else ""
            for a in [] if e.get("results_pruned") else (e.get("analyses") or []):
                if a.get("index") and e.get("status") == "done":
                    try:
                        rel = os.path.relpath(a["index"], self.out).replace("\\", "/")
                        link = '<a href="%s">%s</a>' % (_html.escape(rel, quote=True), _html.escape(a.get("name") or "open"))
                        break
                    except ValueError:
                        pass
            tier = next((a.get("tier") for a in e.get("analyses") or [] if a.get("tier")), "") or ""
            color = {"done": "#2a7a2a", "failed": "#b26a00", "gave_up": "#c00", "running": "#555", "vanished": "#777"}.get(e.get("status"), "#222")
            rows.append("<tr><td>%s</td><td>%s</td><td style='color:%s;font-weight:700'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                _html.escape(e.get("stream", "")), _html.escape(e.get("stem", "")), color, _html.escape(str(e.get("status"))),
                _html.escape(str(e.get("finished") or "")), _html.escape(tier), link, _html.escape(_ascii(e.get("error") or ""))[:200]))
        try:
            import padb_brand
            logo = padb_brand.logo_img(34)
        except Exception:  # noqa: BLE001
            logo = ""
        page = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>padb watch</title><style>body{font-family:sans-serif;"
                "margin:24px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 10px;text-align:left}"
                "th{background:#eef}</style></head><body>%s<h2>Watched analyses</h2><p>Updated %s. Newest first.</p><table><tr><th>Stream</th>"
                "<th>File</th><th>Status</th><th>Finished</th><th>Tier</th><th>Results</th><th>Error</th></tr>%s</table></body></html>"
                % (logo, _now_iso(), "".join(rows)))
        try:
            (self.out / "index.html").write_text(page, encoding="utf-8")
        except OSError:
            pass


# ------------------------------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Watch a folder for new parquet files and analyse each one once.")
    ap.add_argument("--watch", action="append", default=[], metavar="DIR", help="a folder to watch (repeatable)")
    ap.add_argument("--padbloader", action="store_true", help="watch PadbLoader's scheduled output (found the way PadbLoader finds it)")
    ap.add_argument("--padbloader-manual", action="store_true", help="with --padbloader: also watch the top level of its data folder (manual pulls)")
    ap.add_argument("--padbloader-config", metavar="FILE", help="PadbLoader's appsettings.json (for Export:OutputDirectory)")
    ap.add_argument("--discover", action="store_true", help="print where PadbLoader writes parquet and why, then exit")
    ap.add_argument("--out", metavar="DIR", help="output folder (results, snapshots, ledger, index.html)")
    ap.add_argument("--once", action="store_true", help="one pass then exit (for Task Scheduler)")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between passes (default 60)")
    ap.add_argument("--settle", type=float, default=30.0, help="a file must be unmodified this long (default 30 s)")
    ap.add_argument("--depth", type=int, default=1, help="sub-folder levels to look in (default 1)")
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--retry-delay", type=float, default=300.0, help="seconds before a failed file is retried (default 300)")
    ap.add_argument("--retry-failed", action="store_true", help="also retry files that were given up on")
    ap.add_argument("--timeout", type=int, default=7200, help="seconds before one analysis is killed (default 7200)")
    ap.add_argument("--no-snapshot", action="store_true", help="analyse the file in place (it may be pruned later)")
    ap.add_argument("--keep-inputs", type=int, default=14, metavar="N",
                    help="keep the input snapshots of the newest N runs per stream, delete older ones (default 14; 0 = keep all)")
    ap.add_argument("--keep-results", type=int, default=0, metavar="N",
                    help="keep the result folders of the newest N runs per stream, delete older ones (default 0 = keep all)")
    ap.add_argument("--prune-now", action="store_true", help="apply the retention settings once and exit (with --dry-run: report only)")
    ap.add_argument("--dry-run", action="store_true", help="report what would be analysed; do nothing")
    ap.add_argument("--list", action="store_true", help="print the ledger and exit")
    ap.add_argument("--views"), ap.add_argument("--limits-file"), ap.add_argument("--filters-file"), ap.add_argument("--publish-to")
    ap.add_argument("--pdf-report", action="store_true")
    a = ap.parse_args(argv)

    if a.discover:
        d = discover_padbloader(extra_config=a.padbloader_config)
        print("PadbLoader root        : %s" % d["root"])
        print("export folder          : %s   (%s)" % (d["export_dir"], d["source"]))
        print("data folder (manual)   : %s  %s" % (d["data_dir"], "(exists)" if Path(d["data_dir"]).is_dir() else "(not there yet)"))
        print("scheduled output       : %s  %s" % (d["scheduled_dir"], "(exists)" if Path(d["scheduled_dir"]).is_dir() else "(not there yet)"))
        for n in d["notes"]:
            print("NOTE: " + n)
        return 0
    roots = [Path(w) for w in a.watch]
    if a.padbloader:
        d = discover_padbloader(extra_config=a.padbloader_config)
        roots.append(Path(d["scheduled_dir"]))
        if a.padbloader_manual:
            roots.append(Path(d["data_dir"]))
        for n in d["notes"]:
            print("NOTE: " + n, file=sys.stderr)
    if not a.out:
        ap.error("--out is required")
    out = Path(a.out)
    if a.list:
        led = Ledger(out / LEDGER)
        for k, e in sorted(led.entries.items(), key=lambda kv: kv[1].get("last_attempt") or ""):
            print("%-9s attempts=%s  %s  %s" % (e.get("status"), e.get("attempts"), e.get("rel"), _ascii(e.get("error") or "")[:80]))
        print("%d entries" % len(led.entries))
        return 0
    if not roots:
        ap.error("nothing to watch: give --watch DIR and/or --padbloader")
    ok, msg = acquire_lock(out)
    if not ok:
        print(msg, file=sys.stderr)
        return 2
    opts = {"views": a.views, "limits_file": a.limits_file, "filters_file": a.filters_file,
            "publish_to": a.publish_to, "pdf_report": a.pdf_report}
    w = Watcher(roots, out, depth=a.depth, settle_s=a.settle, max_attempts=a.max_attempts, retry_delay_s=a.retry_delay,
                timeout_s=a.timeout, retry_failed=a.retry_failed, snapshot=not a.no_snapshot, opts=opts,
                keep_inputs=max(0, a.keep_inputs), keep_results=max(0, a.keep_results))
    if a.prune_now:
        try:
            r = w.apply_retention(dry_run=a.dry_run)
            if not a.dry_run:
                w.ledger.save()
                w.write_overview()
            w.log("retention%s: %d input snapshot(s) and %d result folder(s) removed, %d refused, %.1f MB"
                  % (" (dry run)" if a.dry_run else "", r["inputs_removed"], r["results_removed"], r["skipped"], r["bytes"] / 1e6))
            return 0
        finally:
            release_lock(out)
    w.log("watching %s -> %s%s" % (", ".join(str(r) for r in roots), out, " (once)" if a.once else " (every %.0f s; Ctrl+C to stop)" % a.interval))
    try:
        while True:
            s = w.run_pass(dry_run=a.dry_run)
            w.log("pass: %d scanned, %d new, %d ok, %d failed, %d gave up, %d waiting (incomplete), %d vanished"
                  % (s["scanned"], s["new"], s["ok"], s["failed"], s["gave_up"], s["incomplete"], s["vanished"]))
            if a.once:
                return 1 if (s["failed"] or s["gave_up"]) else 0
            time.sleep(a.interval)
    except KeyboardInterrupt:
        w.log("stopped")
        return 0
    finally:
        release_lock(out)


if __name__ == "__main__":
    sys.exit(main())
