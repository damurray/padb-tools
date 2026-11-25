r"""qa_watch.py -- the directory watcher (padb_watch).

v3.0 stage 7. The hazards are the ones PadbLoader's own source creates (see padb_watch's docstring), so each is a
test, and each test is shown to be able to fail (mutations in QA_GUIDE).

  W-DISCOVER   where PadbLoader writes, resolved the way ITS code does: user-chosen folder > Export:OutputDirectory
               (appsettings.local.json, then a given appsettings.json) > <root>\Exports; PADBLOADER_DATA_DIR moves
               the root; blank values ignored; a relative folder is flagged; checked against this PC when present
  W-SCAN       .staging-* and *.parts folders (they hold *.parquet), .partial, dot files, .dbdif / .csv and deeper
               folders are ignored; names parse into schedule + run stamp
  W-COMPLETE   a finished parquet passes; a truncated one, a header-only one, an empty one, one with PAR1 magic but
               no valid footer, one modified a moment ago and one still growing do NOT
  W-LEDGER     the retry / give-up / re-written-file / crash-mid-run rules; a corrupt ledger is kept, not trusted
  W-PASS       processed once, not again after a restart; a re-written file is processed again; failures retry then
               give up; a vanished file is recorded; an incomplete file waits and is picked up when it completes;
               the input is SNAPSHOT so deleting the original (PadbLoader's pruning) loses nothing
  W-OVERVIEW   index.html links every finished run and escapes names
  W-LOCK       one watcher per output folder; a lock whose process died is taken over
  W-RUN        the real subprocess runner: success, junk output, a failed analysis, and a hung analysis is killed
               with its whole process tree
  W-E2E        the real CLI end to end through padb_analyze, for both a padb-tools parquet and a genuine
               padbloader-schema parquet: results, ledger, overview, no reprocessing, only the new file next time,
               a truncated file waits, an analysis that cannot work is retried then given up (exit codes)

    py qa/qa_watch.py          -> exit 1 on a failure
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the repository root (this file lives in qa/)
sys.path.insert(0, str(HERE))
PASS = FAIL = 0
# The fixtures are tiny: these tests are about the watcher, not the memory guard. On a starved machine the guard would turn
# every analysis into a viewer-only result, so it is off for this process and its children.
os.environ["PADB_SIZE_GUARD"] = "off"


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail))[:400] if detail else "")


def main() -> int:
    import padb_watch as W
    import qa_viewer_ui as fx
    import qa_loader_adapter as qla

    tmp = Path(tempfile.mkdtemp(prefix="qa_watch_"))
    fx.make_fixture(tmp)
    good = tmp / "fixture.parquet"
    gbytes = good.read_bytes()

    def put(path, data=gbytes, age=3600):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        t = time.time() - age
        os.utime(path, (t, t))
        return path

    # ------------------------------------------------------------------ W-DISCOVER
    def disc(env, local, extra=None):
        return W.discover_padbloader(env=env, local_appdata=str(local), extra_config=extra)
    base = tmp / "disc"
    root = base / "PadbLoader"
    d0 = disc({}, base)
    check("W-DISCOVER: nothing set -> <LOCALAPPDATA>\\PadbLoader\\Exports\\PadbLoader Data (+ \\Scheduled)",
          d0["export_dir"] == str(root / "Exports") and d0["data_dir"] == str(root / "Exports" / "PadbLoader Data")
          and d0["scheduled_dir"] == str(root / "Exports" / "PadbLoader Data" / "Scheduled"), d0)
    d1 = disc({"PADBLOADER_DATA_DIR": str(tmp / "alt")}, base)
    check("W-DISCOVER: PADBLOADER_DATA_DIR moves the whole root", d1["root"] == str(tmp / "alt")
          and d1["export_dir"] == str(tmp / "alt" / "Exports"), d1)
    root.mkdir(parents=True)
    (root / "appsettings.local.json").write_text(json.dumps({"Export": {"OutputDirectory": str(tmp / "cfg")}}))
    d2 = disc({}, base)
    check("W-DISCOVER: Export:OutputDirectory (appsettings.local.json) beats the default", d2["export_dir"] == str(tmp / "cfg"), d2)
    (root / "user-settings.json").write_text(json.dumps({"exportDirectory": str(tmp / "chosen")}))
    d3 = disc({}, base)
    check("W-DISCOVER: the folder chosen in PadbLoader (user-settings.json, any key casing) beats the config",
          d3["export_dir"] == str(tmp / "chosen") and "chosen in PadbLoader" in d3["source"], d3)
    (root / "user-settings.json").write_text(json.dumps({"ExportDirectory": "   "}))
    d4 = disc({}, base)
    check("W-DISCOVER: a blank choice is ignored (as ExportFolder.Resolve ignores whitespace)", d4["export_dir"] == str(tmp / "cfg"), d4)
    (root / "appsettings.local.json").unlink()
    (root / "user-settings.json").unlink()
    extra = tmp / "appsettings.json"
    extra.write_text(json.dumps({"Export": {"OutputDirectory": str(tmp / "fromapp")}}))
    d5 = disc({}, base, extra)
    check("W-DISCOVER: an appsettings.json given with --padbloader-config is read", d5["export_dir"] == str(tmp / "fromapp"), d5)
    (root / "appsettings.local.json").write_text(json.dumps({"Export": {"OutputDirectory": "relative/dir"}}))
    d6 = disc({}, base)
    check("W-DISCOVER: a relative export folder is flagged (PadbLoader resolves it against its own folder)", d6["notes"], d6)
    real_ls = Path(os.environ.get("LOCALAPPDATA", "")) / "PadbLoader" / "Exports" / "PadbLoader Data"
    if real_ls.is_dir() and not os.environ.get("PADBLOADER_DATA_DIR"):
        dr = W.discover_padbloader()
        ud = Path(dr["root"]) / "user-settings.json"
        if not ud.exists():
            check("W-DISCOVER: on THIS machine the discovered data folder is the one PadbLoader really uses",
                  Path(dr["data_dir"]) == real_ls, dr)
    else:
        print("  SKIP  W-DISCOVER (this machine): no PadbLoader data folder here")

    # ------------------------------------------------------------------ W-SCAN
    sc = tmp / "scan"
    put(sc / "manual_20261120_090341.parquet")
    put(sc / "Sched A - u" / "A_20261124-100000.parquet")
    put(sc / "Sched A - u" / "A_20261124-110000.parquet")
    put(sc / "Sched A - u" / ".staging-20261124_1-abc" / "part1.parquet")
    put(sc / "Sched A - u" / "A_20261124-120000.parquet.parts" / "p0.parquet")
    put(sc / ".staging-top" / "part.parquet")
    put(sc / "x.parquet.parts" / "p1.parquet")
    put(sc / "Sched A - u" / "A_20261124-130000.parquet.partial")
    put(sc / "Sched A - u" / ".hidden.parquet")
    put(sc / "Sched A - u" / "A_20261124-100000.dbdif")
    put(sc / "Sched A - u" / "A_20261124-100000.csv")
    put(sc / "Sched A - u" / ".padbloader-write-test-1234")
    put(sc / "Sched A - u" / "deep" / "z_20261124-100000.parquet")
    r1 = sorted(c["rel"] for c in W.scan(sc, 1))
    check("W-SCAN: depth 1 finds exactly the real parquet files; staging / .parts / .partial / dot / dbdif / csv / deeper ignored",
          r1 == ["Sched A - u/A_20261124-100000.parquet", "Sched A - u/A_20261124-110000.parquet", "manual_20261120_090341.parquet"], r1)
    r0 = sorted(c["rel"] for c in W.scan(sc, 0))
    r2 = sorted(c["rel"] for c in W.scan(sc, 2))
    check("W-SCAN: depth 0 = the top level only; depth 2 adds the deeper folder",
          r0 == ["manual_20261120_090341.parquet"] and "Sched A - u/deep/z_20261124-100000.parquet" in r2 and len(r2) == 4, (r0, r2))
    check("W-SCAN: the stream is the schedule folder ('' for the top level)",
          {c["rel"]: c["stream"] for c in W.scan(sc, 1)}["Sched A - u/A_20261124-110000.parquet"] == "Sched A - u"
          and {c["rel"]: c["stream"] for c in W.scan(sc, 1)}["manual_20261120_090341.parquet"] == "")
    check("W-SCAN: names parse into schedule + stamp (scheduled, with a source suffix, manual) and a stranger does not",
          W.parse_name("Spurs_and_Harmonics_2site_20261124-115925.parquet") == ("Spurs_and_Harmonics_2site", "20261124-115925")
          and W.parse_name("X_20261124-115925_SRC1.parquet") == ("X", "20261124-115925")
          and W.parse_name("padbloader_20261120_090341.parquet") == ("padbloader", "20261120_090341")
          and W.parse_name("whatever.parquet") == (None, None))

    # ------------------------------------------------------------------ W-COMPLETE
    cp = tmp / "complete"
    ok, why = W.is_complete(put(cp / "good.parquet"), 0, 0)
    check("W-COMPLETE: a finished parquet passes", ok, why)
    ok, why = W.is_complete(put(cp / "trunc.parquet", gbytes[: int(len(gbytes) * 0.6)]), 0, 0)
    check("W-COMPLETE: a truncated parquet does NOT (no closing PAR1)", not ok and "PAR1" in why, why)
    ok, why = W.is_complete(put(cp / "head.parquet", gbytes[:4]), 0, 0)
    check("W-COMPLETE: a header-only file does NOT", not ok, why)
    ok, why = W.is_complete(put(cp / "empty.parquet", b""), 0, 0)
    check("W-COMPLETE: an empty file does NOT", not ok and "empty" in why, why)
    ok, why = W.is_complete(put(cp / "magic.parquet", b"PAR1" + b"\x00" * 200 + b"PAR1"), 0, 0)
    check("W-COMPLETE: PAR1 magic at both ends but no valid footer does NOT (the footer is actually parsed)",
          not ok and "footer" in why, why)
    ok, why = W.is_complete(put(cp / "fresh.parquet", age=1), 60, 0)
    check("W-COMPLETE: a file modified a moment ago is 'still being written' even if it parses", not ok and "still being written" in why, why)
    grow = put(cp / "grow.parquet")

    def grower():
        time.sleep(0.4)
        with open(grow, "ab") as f:
            f.write(b"x")
    th = threading.Thread(target=grower)
    th.start()
    ok, why = W.is_complete(grow, 0, 1.2)
    th.join()
    check("W-COMPLETE: a file that changes while being checked does NOT pass", not ok and "changed" in why, why)
    check("W-COMPLETE: a missing file does NOT (and does not raise)", not W.is_complete(cp / "nope.parquet", 0, 0)[0])

    # ------------------------------------------------------------------ W-LEDGER
    L = W.Ledger(tmp / "ledger_unit.json")
    sig = (10, 20)
    now = 1000.0
    L.entries = {
        "done": {"sig": [10, 20], "status": "done"},
        "run": {"sig": [10, 20], "status": "running"},
        "failed_wait": {"sig": [10, 20], "status": "failed", "attempts": 1, "next_retry_ts": now + 50},
        "failed_due": {"sig": [10, 20], "status": "failed", "attempts": 1, "next_retry_ts": now - 1},
        "failed_max": {"sig": [10, 20], "status": "failed", "attempts": 3, "next_retry_ts": 0},
        "gave": {"sig": [10, 20], "status": "gave_up", "attempts": 3},
        "gone": {"sig": [10, 20], "status": "vanished"},
    }
    nw = lambda k, s=sig, **kw: L.needs_work(k, s, 3, now=now, **kw)
    check("W-LEDGER: unseen -> work; done -> none; a RE-WRITTEN file (new size / mtime) -> work again",
          nw("new") and not nw("done") and nw("done", (11, 20)) and nw("done", (10, 21)))
    check("W-LEDGER: running (the watcher died mid-run) -> retried", nw("run"))
    check("W-LEDGER: failed waits for its retry time, then retries, never past max attempts",
          not nw("failed_wait") and nw("failed_due") and not nw("failed_max"))
    check("W-LEDGER: gave up -> left alone unless --retry-failed; a vanished file that comes back -> work",
          not nw("gave") and nw("gave", retry_failed=True) and nw("gone"))
    L.save()
    check("W-LEDGER: the ledger is written atomically (no .tmp left) and reloads", not (tmp / "ledger_unit.json.tmp").exists()
          and len(W.Ledger(tmp / "ledger_unit.json").entries) == len(L.entries))
    (tmp / "ledger_bad.json").write_text("{not json", encoding="utf-8")
    Lb = W.Ledger(tmp / "ledger_bad.json")
    check("W-LEDGER: a corrupt ledger is KEPT aside (.corrupt-*) and a new one started with a warning, never trusted",
          Lb.entries == {} and Lb.warning and list(tmp.glob("ledger_bad.json.corrupt-*")), Lb.warning)

    # ------------------------------------------------------------------ W-PASS (stub runner)
    calls = []

    def stub(src, out_dir, opts, timeout_s, fail=False):
        calls.append((str(src), str(out_dir)))
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "index.html").write_text("ok")
        return {"ok": True, "error": None, "analyses": [{"name": Path(src).stem, "status": "ok", "views": ["scatter"],
                                                        "index": str(Path(out_dir) / "index.html"), "tier": "standard", "issues": 0}]}
    pw = tmp / "pass"
    f1 = put(pw / "S1 - u" / "A_20261124-100000.parquet")
    f2 = put(pw / "S1 - u" / "A_20261124-110000.parquet", gbytes + b"")
    quiet = lambda m: None
    out1 = tmp / "out_pass"
    w = W.Watcher([pw], out1, settle_s=0, recheck_s=0, runner=stub, log=quiet)
    s1 = w.run_pass()
    check("W-PASS: first pass analyses both files, in their own results folders", s1["new"] == 2 and s1["ok"] == 2
          and len(calls) == 2 and all("S1 - u" in c[1] for c in calls), (s1, calls))
    check("W-PASS: the runner is given a SNAPSHOT, not the original path",
          all(c[0] != str(f1) and c[0] != str(f2) and "_inputs" in c[0] and Path(c[0]).is_file() for c in calls), calls)
    n_before = len(calls)
    s2 = W.Watcher([pw], out1, settle_s=0, recheck_s=0, runner=stub, log=quiet).run_pass()
    check("W-PASS: after a RESTART (a new Watcher on the same ledger) nothing is processed twice", s2["new"] == 0 and len(calls) == n_before, s2)
    f1.write_bytes(gbytes + b"")      # same content...
    t = time.time() - 3000
    os.utime(f1, (t, t))              # ...but a new mtime = a re-written file
    s3 = W.Watcher([pw], out1, settle_s=0, recheck_s=0, runner=stub, log=quiet).run_pass()
    check("W-PASS: a RE-WRITTEN file (same name, new mtime) is analysed again", s3["new"] == 1 and len(calls) == n_before + 1, s3)
    snap = [c[0] for c in calls if "A_20261124-110000" in c[0]][0]
    f2.unlink()
    check("W-PASS: deleting the original (PadbLoader's pruning) loses nothing -- the snapshot is still a complete parquet",
          Path(snap).is_file() and Path(snap).read_bytes() == gbytes)
    led = json.loads((out1 / "watch_ledger.json").read_text())["entries"]
    check("W-PASS: the ledger says how each input was kept (hard link or copy)",
          all(e.get("snapshot_how") in ("hard link", "copy") for e in led.values()), [e.get("snapshot_how") for e in led.values()])

    # failures: retry, give up, retry-failed
    def failing(src, out_dir, opts, timeout_s):
        return {"ok": False, "error": "boom", "analyses": []}
    pf = tmp / "failpass"
    put(pf / "F - u" / "B_20261124-100000.parquet")
    outf = tmp / "out_fail"
    r = [W.Watcher([pf], outf, settle_s=0, recheck_s=0, retry_delay_s=0, max_attempts=3, runner=failing, log=quiet).run_pass()
         for _ in range(5)]                                     # each pass is a fresh start, as `--once` runs are
    e = list(json.loads((outf / "watch_ledger.json").read_text())["entries"].values())[0]
    check("W-PASS: a failing file is retried, then GIVEN UP after max attempts (3), with the reason kept",
          r[0]["failed"] == 1 and r[1]["failed"] == 1 and r[2]["gave_up"] == 1 and r[3]["new"] == 0
          and e["status"] == "gave_up" and e["attempts"] == 3 and e["error"] == "boom", (r, e))
    rr = W.Watcher([pf], outf, settle_s=0, recheck_s=0, retry_delay_s=0, max_attempts=3, retry_failed=True, runner=failing, log=quiet).run_pass()
    check("W-PASS: --retry-failed tries a given-up file again", rr["new"] == 1, rr)

    def boom(src, out_dir, opts, timeout_s):
        raise RuntimeError("the runner itself blew up")
    pb = tmp / "boompass"
    put(pb / "B - u" / "C_20261124-100000.parquet")
    wb = W.Watcher([pb], tmp / "out_boom", settle_s=0, recheck_s=0, runner=boom, log=quiet)
    sb = wb.run_pass()
    check("W-PASS: an exception inside the runner is recorded as a failure, the watcher carries on",
          sb["failed"] == 1 and "blew up" in json.dumps(wb.ledger.entries), sb)

    # vanish while waiting to retry
    pv = tmp / "vanpass"
    fv = put(pv / "V - u" / "D_20261124-100000.parquet")
    outv = tmp / "out_van"
    W.Watcher([pv], outv, settle_s=0, recheck_s=0, retry_delay_s=9999, runner=failing, log=quiet).run_pass()
    fv.unlink()
    sv = W.Watcher([pv], outv, settle_s=0, recheck_s=0, retry_delay_s=9999, runner=failing, log=quiet).run_pass()
    ev = list(json.loads((outv / "watch_ledger.json").read_text())["entries"].values())[0]
    check("W-PASS: a file that disappears while waiting to be retried is recorded as vanished", sv["vanished"] == 1 and ev["status"] == "vanished", (sv, ev))

    # crash mid-run (a 'running' entry from a dead watcher)
    pc = tmp / "crashpass"
    fc = put(pc / "C - u" / "E_20261124-100000.parquet")
    outc = tmp / "out_crash"
    outc.mkdir()
    st = fc.stat()
    key = W.Ledger.key(pc, "C - u/E_20261124-100000.parquet")
    (outc / "watch_ledger.json").write_text(json.dumps({"schema": W.SCHEMA, "entries": {key: {
        "sig": [st.st_size, st.st_mtime_ns], "status": "running", "attempts": 1}}}))
    calls.clear()
    sc2 = W.Watcher([pc], outc, settle_s=0, recheck_s=0, runner=stub, log=quiet).run_pass()
    check("W-PASS: a 'running' entry left by a crashed watcher is retried (and counted as an attempt)",
          sc2["ok"] == 1 and len(calls) == 1 and json.loads((outc / "watch_ledger.json").read_text())["entries"][key]["attempts"] == 2, sc2)

    # incomplete waits, then completes
    pi = tmp / "incpass"
    fi = put(pi / "I - u" / "G_20261124-100000.parquet", gbytes[: len(gbytes) // 2])
    outi = tmp / "out_inc"
    calls.clear()
    si = W.Watcher([pi], outi, settle_s=0, recheck_s=0, runner=stub, log=quiet).run_pass()
    check("W-PASS: a truncated (still-being-written) file WAITS -- not analysed, not recorded as failed",
          si["incomplete"] == 1 and si["new"] == 0 and not calls
          and not any(e.get("status") == "failed" for e in W.Ledger(outi / "watch_ledger.json").entries.values()), si)
    put(fi, gbytes)
    si2 = W.Watcher([pi], outi, settle_s=0, recheck_s=0, runner=stub, log=quiet).run_pass()
    check("W-PASS: ... and is analysed on the pass after it completes", si2["ok"] == 1 and len(calls) == 1, si2)

    # non-ASCII names never crash the log
    pa = tmp / "asciipass"
    put(pa / "Café - ü" / "H_20261124-100000.parquet")
    outa = tmp / "out_ascii"
    wa = W.Watcher([pa], outa, settle_s=0, recheck_s=0, runner=stub, log=quiet)
    wa.run_pass()
    raw = (outa / "watch_log.txt").read_bytes()
    check("W-PASS: a non-ASCII folder name is logged without error and the log stays ASCII", all(b < 128 for b in raw) and len(raw) > 0)

    # ------------------------------------------------------------------ W-OVERVIEW
    po = tmp / "ovpass"
    put(po / "A&B - u" / "O_20261124-100000.parquet")
    outo = tmp / "out_ov"
    W.Watcher([po], outo, settle_s=0, recheck_s=0, runner=stub, log=quiet).run_pass()
    page = (outo / "index.html").read_text(encoding="utf-8")
    check("W-OVERVIEW: index.html links the finished run's results and escapes the names (A&amp;B)",
          'href="A&amp;B - u/O_20261124-100000/index.html"' in page and "<td>A&amp;B - u</td>" in page
          and "<td>A&B" not in page and "done" in page, page[:300])

    # ------------------------------------------------------------------ W-LOCK
    lk = tmp / "lockdir"
    ok1, _ = W.acquire_lock(lk)
    ok2, _ = W.acquire_lock(lk)
    check("W-LOCK: taking the lock twice from the SAME process is allowed (a restart of the same watcher)", ok1 and ok2)
    W.release_lock(lk)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (lk / "watch.lock").write_text(json.dumps({"pid": sleeper.pid, "started": "x"}))
        ok3, msg3 = W.acquire_lock(lk)
        check("W-LOCK: a lock held by a LIVE other process is refused, and says who", not ok3 and str(sleeper.pid) in msg3, msg3)
    finally:
        sleeper.kill()
        sleeper.wait()
    time.sleep(0.3)
    ok4, _ = W.acquire_lock(lk)
    check("W-LOCK: a lock whose process has died is taken over", ok4)
    W.release_lock(lk)

    # ------------------------------------------------------------------ W-RUN (the real subprocess runner)
    sdir = tmp / "scripts"
    sdir.mkdir()
    (sdir / "ok.py").write_text("import json,sys\nprint(json.dumps({'ok':True,'analyses':[{'name':'n','status':'ok','views':['scatter'],"
                                "'index':'i.html','tier':{'tier':'heavy'},'job_issues':[{'level':'error'},{'level':'warn'}]}]}))")
    (sdir / "junk.py").write_text("print('this is not json')")
    (sdir / "bad.py").write_text("import json\nprint(json.dumps({'ok':True,'analyses':[{'name':'n','status':'failed','error':'no data'}]}))")
    gc = tmp / "grandchild.pid"
    (sdir / "hang.py").write_text("import subprocess,sys,time\n"
                                  "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
                                  "open(r'%s','w').write(str(p.pid))\ntime.sleep(120)\n" % gc)
    pre = lambda n: [sys.executable, str(sdir / n)]
    r_ok = W.run_analysis(good, tmp / "o", {}, 30, pre("ok.py"))
    check("W-RUN: a good result is reported with views, tier and the count of job errors",
          r_ok["ok"] and r_ok["analyses"][0]["tier"] == "heavy" and r_ok["analyses"][0]["issues"] == 1, r_ok)
    r_junk = W.run_analysis(good, tmp / "o", {}, 30, pre("junk.py"))
    check("W-RUN: output that is not a result is a failure with a reason, not an exception", not r_junk["ok"] and "no result" in r_junk["error"], r_junk)
    r_bad = W.run_analysis(good, tmp / "o", {}, 30, pre("bad.py"))
    check("W-RUN: an analysis that reports 'failed' makes the file fail, with its error", not r_bad["ok"] and "no data" in r_bad["error"], r_bad)
    t0 = time.time()
    r_hang = W.run_analysis(good, tmp / "o", {}, 3, pre("hang.py"))
    took = time.time() - t0
    time.sleep(0.5)
    gpid = int(gc.read_text()) if gc.exists() else 0
    check("W-RUN: a hung analysis is KILLED at the timeout (%.1f s)" % took, not r_hang["ok"] and "timed out" in r_hang["error"] and took < 20, (r_hang, took))
    check("W-RUN: ... and so is the process it started (the whole tree), not just the parent", gpid and not W._pid_alive(gpid), gpid)

    # ------------------------------------------------------------------ W-E2E (the real CLI + padb_analyze)
    e2e = tmp / "e2e"
    sch = e2e / "watch" / "Fixture - u"
    put(sch / "fixture_20261124-100000.parquet", gbytes)
    outd = e2e / "out"

    def cli(*args, timeout=900):
        return subprocess.run([sys.executable, str(HERE / "padb_watch.py"), "--watch", str(e2e / "watch"), "--out", str(outd),
                               "--settle", "0", "--retry-delay", "0", "--views", "scatter"] + list(args),
                              capture_output=True, text=True, timeout=timeout)
    r1 = cli("--once")
    res = list((outd / "Fixture - u").glob("fixture_20261124-100000/results/*/index.html"))
    ledger = json.loads((outd / "watch_ledger.json").read_text())["entries"]
    e = list(ledger.values())[0]
    check("W-E2E: --once analyses a padb-tools parquet into <out>/<stream>/<file>/ (rc %s)" % r1.returncode,
          r1.returncode == 0 and res and e["status"] == "done" and e["analyses"][0]["tier"] == "standard", (r1.stdout[-300:], r1.stderr[-300:]))
    check("W-E2E: the input was snapshot and the overview links the results",
          Path(e["input_snapshot"]).is_file() and "results/" in (outd / "index.html").read_text(encoding="utf-8"))
    r2 = cli("--once")
    check("W-E2E: a second pass does nothing (0 new) and exits 0", r2.returncode == 0 and "0 new" in r2.stdout, r2.stdout[-250:])
    put(sch / "fixture_20261124-110000.parquet", gbytes)
    r3 = cli("--once")
    check("W-E2E: a new file next time -> only that file is analysed", r3.returncode == 0 and "1 new" in r3.stdout and "1 ok" in r3.stdout, r3.stdout[-250:])
    put(sch / "fixture_20261124-120000.parquet", gbytes[: len(gbytes) // 2])
    r4 = cli("--once")
    check("W-E2E: a truncated file WAITS (exit 0, listed as waiting, not in the ledger as failed)",
          r4.returncode == 0 and "waiting" in r4.stdout and "1 waiting" in r4.stdout, r4.stdout[-300:])
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]}), str(sch / "unusable_20261124-130000.parquet"))
    os.utime(sch / "unusable_20261124-130000.parquet", (time.time() - 3600,) * 2)
    rc = [cli("--once").returncode for _ in range(3)]
    r_after = cli("--once")
    check("W-E2E: a valid parquet that cannot be analysed fails, is retried, then given up -- exit 1 each time, then quiet (rc %s, then %s)"
          % (rc, r_after.returncode), rc[0] == 1 and rc[1] == 1 and rc[2] == 1 and r_after.returncode == 0 and "GAVE UP" in (outd / "watch_log.txt").read_text(),
          (rc, r_after.stdout[-200:]))
    lst = subprocess.run([sys.executable, str(HERE / "padb_watch.py"), "--out", str(outd), "--list"], capture_output=True, text=True)
    check("W-E2E: --list shows the ledger (done and gave_up entries)", "done" in lst.stdout and "gave_up" in lst.stdout, lst.stdout[-300:])
    # a genuine padbloader-schema parquet through the same watcher
    ploader = e2e / "watch2" / "Loader - u"
    ploader.mkdir(parents=True)
    qla.make_padbloader_parquet(ploader / "Loader_20261124-100000.parquet")
    os.utime(ploader / "Loader_20261124-100000.parquet", (time.time() - 3600,) * 2)
    out2 = e2e / "out2"
    rl = subprocess.run([sys.executable, str(HERE / "padb_watch.py"), "--watch", str(e2e / "watch2"), "--out", str(out2), "--once",
                         "--settle", "0"], capture_output=True, text=True, timeout=900)
    led2 = list(json.loads((out2 / "watch_ledger.json").read_text())["entries"].values())
    check("W-E2E: a genuine PADBLOADER-schema parquet goes through the same watcher and produces analyses (rc %s)" % rl.returncode,
          rl.returncode == 0 and led2 and led2[0]["status"] == "done" and led2[0]["analyses"] and
          all(a["status"] == "ok" for a in led2[0]["analyses"]), (rl.stdout[-300:], rl.stderr[-300:]))
    dsc = subprocess.run([sys.executable, str(HERE / "padb_watch.py"), "--discover"], capture_output=True, text=True)
    check("W-E2E: --discover prints where PadbLoader writes and exits 0", dsc.returncode == 0 and "scheduled output" in dsc.stdout, dsc.stdout[-200:])
    lock_out = e2e / "locked_out"
    lock_out.mkdir()
    sl = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (lock_out / "watch.lock").write_text(json.dumps({"pid": sl.pid, "started": "x"}))
        rlk = subprocess.run([sys.executable, str(HERE / "padb_watch.py"), "--watch", str(e2e / "watch"), "--out", str(lock_out), "--once"],
                             capture_output=True, text=True, timeout=60)
        check("W-E2E: a second watcher on the same output folder exits 2 and says why", rlk.returncode == 2 and "already holds" in rlk.stderr, (rlk.returncode, rlk.stderr[-200:]))
    finally:
        sl.kill()
        sl.wait()
    # ------------------------------------------------------------------ W-RETAIN
    STAMPS = ["20261124-100000", "20261124-110000", "20261124-120000", "20261124-130000", "20261124-140000", "20261124-150000"]

    def retain_run(tag, stamps, keep_inputs, keep_results, streams=("S - u",), runner_=None):
        base = tmp / ("ret_" + tag)
        for s_ in streams:
            for st_ in stamps:
                put(base / s_ / ("R_%s.parquet" % st_))
        outr = tmp / ("out_ret_" + tag)
        w_ = W.Watcher([base], outr, settle_s=0, recheck_s=0, runner=runner_ or stub, log=quiet,
                       keep_inputs=keep_inputs, keep_results=keep_results, max_attempts=1)
        w_.run_pass()
        return base, outr, w_

    def snaps(outr, stream="S - u"):
        d_ = outr / "_inputs" / stream
        return sorted(p.name.split("__")[0] for p in d_.glob("*")) if d_.exists() else []

    def results(outr, stream="S - u"):
        d_ = outr / stream
        return sorted(p.name for p in d_.glob("*") if p.is_dir()) if d_.exists() else []
    newest2 = ["R_20261124-140000", "R_20261124-150000"]

    base1, out_r1, w1r = retain_run("a", STAMPS, 2, 0)
    check("W-RETAIN: --keep-inputs 2 keeps the snapshots of the 2 NEWEST runs only; every result is kept (keep-results 0)",
          snaps(out_r1) == newest2 and len(results(out_r1)) == 6, (snaps(out_r1), len(results(out_r1))))
    ents = list(json.loads((out_r1 / "watch_ledger.json").read_text())["entries"].values())
    check("W-RETAIN: the ledger entries stay (still 'done', the 4 older marked inputs_pruned) so nothing is analysed again",
          len(ents) == 6 and all(e["status"] == "done" for e in ents) and sum(1 for e in ents if e.get("inputs_pruned")) == 4, [e.get("inputs_pruned") for e in ents])
    n_calls = len(calls)
    W.Watcher([base1], out_r1, settle_s=0, recheck_s=0, runner=stub, log=quiet, keep_inputs=2).run_pass()
    check("W-RETAIN: ... a later pass does NOT re-analyse the pruned runs even though their source files still exist", len(calls) == n_calls, len(calls) - n_calls)
    check("W-RETAIN: pruning a snapshot never touches the original file (it may be a hard link to it)",
          all(p.is_file() and p.read_bytes() == gbytes for p in (base1 / "S - u").glob("*.parquet")) and len(list((base1 / "S - u").glob("*.parquet"))) == 6)
    base2, out_r2, _ = retain_run("b", STAMPS, 0, 3)
    pg = (out_r2 / "index.html").read_text(encoding="utf-8")
    check("W-RETAIN: --keep-results 3 removes the 3 oldest result folders, keeps the newest 3, and the overview says 'results pruned'",
          results(out_r2) == ["R_20261124-130000", "R_20261124-140000", "R_20261124-150000"] and pg.count("(results pruned)") == 3
          and pg.count('href="S - u/R_') == 3, (results(out_r2), pg.count("(results pruned)")))
    base3, out_r3, _ = retain_run("c", STAMPS, 0, 0)
    check("W-RETAIN: 0 / 0 keeps everything", len(snaps(out_r3)) == 6 and len(results(out_r3)) == 6)
    base4, out_r4, _ = retain_run("d", STAMPS, 2, 2, streams=("S1 - u", "S2 - u"))
    check("W-RETAIN: each stream keeps its OWN newest N (2 + 2 snapshots, 2 + 2 results)",
          snaps(out_r4, "S1 - u") == newest2 and snaps(out_r4, "S2 - u") == newest2
          and len(results(out_r4, "S1 - u")) == 2 and len(results(out_r4, "S2 - u")) == 2)

    def fail_oldest(src, out_dir, opts, timeout_s):
        if "R_20261124-100000" in str(src):
            return {"ok": False, "error": "nope", "analyses": []}
        return stub(src, out_dir, opts, timeout_s)
    base5, out_r5, _ = retain_run("e", STAMPS, 1, 1, runner_=fail_oldest)
    check("W-RETAIN: only FINISHED runs are pruned -- the snapshot of a run that was given up on is kept",
          "R_20261124-100000" in snaps(out_r5) and "R_20261124-150000" in snaps(out_r5) and len(snaps(out_r5)) == 2, snaps(out_r5))

    base6 = tmp / "ret_f"
    for st_ in (STAMPS[4], STAMPS[5]):
        put(base6 / "S - u" / ("R_%s.parquet" % st_))
    out_r6 = tmp / "out_ret_f"
    W.Watcher([base6], out_r6, settle_s=0, recheck_s=0, runner=stub, log=quiet, keep_inputs=2).run_pass()
    put(base6 / "S - u" / ("R_%s.parquet" % STAMPS[0]))
    W.Watcher([base6], out_r6, settle_s=0, recheck_s=0, runner=stub, log=quiet, keep_inputs=2).run_pass()
    check("W-RETAIN: 'newest' is by the RUN STAMP, not by when it was processed -- a late-arriving OLDER run is the one pruned",
          snaps(out_r6) == ["R_%s" % STAMPS[4], "R_%s" % STAMPS[5]], snaps(out_r6))

    # dry run + CLI
    base7, out_r7, w7 = retain_run("g", STAMPS, 0, 0)
    w7.keep_inputs = 2
    before = (snaps(out_r7), json.dumps(w7.ledger.entries, sort_keys=True))
    rd = w7.apply_retention(dry_run=True)
    check("W-RETAIN: a dry run reports what it would remove and removes / changes nothing",
          (snaps(out_r7), json.dumps(w7.ledger.entries, sort_keys=True)) == before and rd["bytes"] > 0 and rd["inputs_removed"] == 0, rd)
    cli_r = lambda *a: subprocess.run([sys.executable, str(HERE / "padb_watch.py"), "--watch", str(base7), "--out", str(out_r7), "--prune-now"] + list(a),
                                      capture_output=True, text=True, timeout=120)
    rdry = cli_r("--keep-inputs", "2", "--dry-run")
    check("W-RETAIN: `--prune-now --dry-run` reports and deletes nothing", rdry.returncode == 0 and "would remove" in rdry.stdout and len(snaps(out_r7)) == 6, rdry.stdout[-300:])
    rreal = cli_r("--keep-inputs", "2")
    check("W-RETAIN: `--prune-now` applies the retention without scanning or analysing", rreal.returncode == 0
          and snaps(out_r7) == newest2 and "4 input snapshot(s)" in rreal.stdout, (rreal.stdout[-300:], snaps(out_r7)))
    slp = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (out_r7 / "watch.lock").write_text(json.dumps({"pid": slp.pid, "started": "x"}))
        rlk2 = cli_r("--keep-results", "1")
        check("W-RETAIN: `--prune-now` is refused (exit 2) while another watcher holds the output folder", rlk2.returncode == 2, rlk2.returncode)
    finally:
        slp.kill()
        slp.wait()
        (out_r7 / "watch.lock").unlink(missing_ok=True)

    # safety: a damaged / tampered ledger and links must never make it delete anything else
    outside = tmp / "outside" / "keep"
    outside.mkdir(parents=True)
    (outside / "sentinel.txt").write_text("do not delete")
    base8, out_r8, w8 = retain_run("h", STAMPS[:3], 0, 0)
    ents8 = sorted(w8.ledger.entries.values(), key=lambda e: e["rel"])          # oldest .. newest
    ents8[0]["input_snapshot"] = str(outside / "file.parquet")                   # parent.parent is not <out>/_inputs
    ents8[0]["result_dir"] = str(outside)
    ents8[1]["input_snapshot"] = str(out_r8 / "_inputs" / "S - u" / "x")         # depth right, but not what we made -> absent: fine
    ents8[1]["result_dir"] = str(out_r8 / "_inputs" / "S - u")                   # a result folder inside _inputs
    ents8[2]["result_dir"] = str(out_r8)                                         # the output folder itself
    w8.keep_inputs, w8.keep_results = 1, 1
    w8.ledger.entries[next(iter(w8.ledger.entries))]  # (touch)
    keep_before = sorted(p.name for p in (out_r8 / "_inputs" / "S - u").glob("*"))
    r8 = w8.apply_retention()
    check("W-RETAIN: ledger entries pointing OUTSIDE the output folder, at _inputs itself, or at the output folder are REFUSED "
          "(the sentinel file survives, %d refused)" % r8["skipped"],
          (outside / "sentinel.txt").is_file() and r8["skipped"] >= 2 and (out_r8 / "_inputs").is_dir()
          and sorted(p.name for p in (out_r8 / "_inputs" / "S - u").glob("*")) == keep_before and (out_r8 / "S - u").is_dir(), (r8, keep_before))

    # a junction / symlink in the path is never followed
    jt = tmp / "jtarget"
    jt.mkdir()
    (jt / "sentinel.txt").write_text("do not delete")
    base9, out_r9, w9 = retain_run("i", STAMPS[:3], 0, 0)
    ents9 = sorted(w9.ledger.entries.values(), key=lambda e: e["rel"])
    victim = Path(ents9[0]["input_snapshot"]).parent
    shutil.rmtree(victim)
    made = False
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(victim), str(jt)], capture_output=True).returncode == 0
    else:
        try:
            os.symlink(jt, victim, target_is_directory=True)
            made = True
        except OSError:
            made = False
    if made:
        w9.keep_inputs = 1
        r9 = w9.apply_retention()
        check("W-RETAIN: a junction / symlink standing where a snapshot folder was is never followed (target survives, refused)",
              (jt / "sentinel.txt").is_file() and r9["skipped"] >= 1, r9)
    else:
        print("  SKIP  W-RETAIN junction test: could not create a junction / symlink here")

    # the case where ONLY the link check can save it: a junction to ANOTHER snapshot folder INSIDE the output folder
    # (it resolves inside, at the right depth -- every other guard would let it through)
    base10, out_r10, w10 = retain_run("j", STAMPS[:3], 0, 0)
    ents10 = sorted(w10.ledger.entries.values(), key=lambda e: e["rel"])        # oldest .. newest
    victim2 = Path(ents10[0]["input_snapshot"]).parent
    target2 = Path(ents10[2]["input_snapshot"]).parent                          # the newest run's snapshot folder
    (target2 / "sentinel.txt").write_text("do not delete")
    shutil.rmtree(victim2)
    made2 = False
    if os.name == "nt":
        made2 = subprocess.run(["cmd", "/c", "mklink", "/J", str(victim2), str(target2)], capture_output=True).returncode == 0
    else:
        try:
            os.symlink(target2, victim2, target_is_directory=True)
            made2 = True
        except OSError:
            made2 = False
    if made2:
        w10.keep_inputs = 1
        r10 = w10.apply_retention()
        check("W-RETAIN: a junction to ANOTHER snapshot folder inside the output folder is refused and nothing it points at is touched "
              "(only the link check catches this one)",
              (target2 / "sentinel.txt").is_file() and r10["skipped"] >= 1 and any(target2.glob("*.parquet")), r10)
    else:
        print("  SKIP  W-RETAIN inside-junction test: could not create a junction / symlink here")
    # ------------------------------------------------------------------ W-STREAMS (per-stream settings)
    sdir = tmp / "streams"
    sdir.mkdir()
    sfile = sdir / "streams.json"
    sfile.write_text(json.dumps({
        "default": {"views": ["scatter"], "publish_to": "X:/share/{stream}", "pdf_report": False},
        "streams": {"Spurs*": {"views": "scatter, stat_summary", "limits_file": "spur_limits.json"},
                    "phase noise*": {"bands": "auto", "algorithms": ["PN Closed Loop"]},
                    "Spurs_special*": {"views": ["boxplot"]},          # never reached: Spurs* is first
                    "oops": {"nonsense": 1, "views": 5}}}), encoding="utf-8")
    st, probs = W.load_stream_settings(sfile)
    check("W-STREAMS: the file loads; a string list becomes a list; a relative path is relative to the settings file; "
          "patterns keep file order", st["streams"][0][0] == "Spurs*" and st["streams"][0][1]["views"] == ["scatter", "stat_summary"]
          and st["streams"][0][1]["limits_file"] == str(sdir / "spur_limits.json") and [p_ for p_, _ in st["streams"]][:2] == ["Spurs*", "phase noise*"], st["streams"][0])
    check("W-STREAMS: an unknown setting and a wrong-typed one are REPORTED and skipped, not applied",
          any("nonsense" in x for x in probs) and any("'views'" in x for x in probs) and st["streams"][-1][1] == {}, probs)
    for bad_name, bad_text in (("junk", "{not json"), ("array", "[1,2]")):
        (sdir / (bad_name + ".json")).write_text(bad_text)
        try:
            W.load_stream_settings(sdir / (bad_name + ".json"))
            raised = None
        except ValueError as exc:
            raised = str(exc)
        check("W-STREAMS: a settings file that is %s is refused with a message (the watcher must not run on a file it cannot trust)" % bad_name,
              bool(raised), raised)
    base = {"views": ["scatter", "boxplot"], "pdf_report": True, "publish_to": "CLI"}
    o1 = W.opts_for_stream(base, st, "Spurs_and_Harmonics - damurray")
    check("W-STREAMS: a stream gets ITS settings over 'default' over the command line (views from the stream, pdf_report from the CLI, "
          "publish_to from default)", o1["views"] == ["scatter", "stat_summary"] and o1["pdf_report"] is False
          and o1["publish_to"].endswith("Spurs_and_Harmonics - damurray") and o1["limits_file"].endswith("spur_limits.json"), o1)
    o5 = W.opts_for_stream(base, st, "Spurs_special run - damurray")           # matches BOTH 'Spurs*' and 'Spurs_special*'
    check("W-STREAMS: the FIRST matching pattern wins, even though a later one also matches (a stream that fits two patterns)",
          o5["views"] == ["scatter", "stat_summary"], o5["views"])
    o2 = W.opts_for_stream(base, st, "Scheduled/Phase Noise EP6 - damurray")
    check("W-STREAMS: matching is case-insensitive and ignores a leading 'Scheduled/'; another stream's keys do not leak in",
          o2.get("bands") == "auto" and o2["algorithms"] == ["PN Closed Loop"] and "limits_file" not in o2 and o2["views"] == ["scatter"], o2)
    o3 = W.opts_for_stream(base, st, "Unlisted - someone")
    check("W-STREAMS: a stream no pattern matches gets the defaults only; no settings file at all leaves the command line untouched",
          o3["views"] == ["scatter"] and "bands" not in o3 and W.opts_for_stream(base, None, "x") == base, o3)
    o4 = W.opts_for_stream({}, {"default": {"publish_to": "S:/{stream}"}, "streams": []}, 'Bad<>:"|?*name - u')
    check("W-STREAMS: {stream} in publish_to is made folder-safe", "<" not in o4["publish_to"] and "|" not in o4["publish_to"]
          and o4["publish_to"].startswith("S:/Bad"), o4["publish_to"])
    # through a real pass: each stream's run is handed its own options, and the ledger records them
    seen = []

    def rec_runner(src, out_dir, opts, timeout_s):
        seen.append((Path(out_dir).parent.name, dict(opts)))
        return stub(src, out_dir, opts, timeout_s)
    wroot = tmp / "w_streams"
    put(wroot / "Spurs_and_Harmonics - damurray" / "S_20261124-100000.parquet")
    put(wroot / "Phase Noise EP6 - damurray" / "P_20261124-100000.parquet")
    put(wroot / "Other - x" / "O_20261124-100000.parquet")
    out_s = tmp / "out_streams"
    ws = W.Watcher([wroot], out_s, settle_s=0, recheck_s=0, runner=rec_runner, log=quiet, stream_settings=st, opts={"views": None})
    ws.run_pass()
    by_stream = {s_: o_ for s_, o_ in seen}
    check("W-STREAMS: in a real pass each stream is analysed with its own options (Spurs: stat_summary view; Phase Noise: bands auto; "
          "Other: defaults)", by_stream["Spurs_and_Harmonics - damurray"]["views"] == ["scatter", "stat_summary"]
          and by_stream["Phase Noise EP6 - damurray"].get("bands") == "auto" and by_stream["Other - x"]["views"] == ["scatter"], by_stream)
    led = json.loads((out_s / "watch_ledger.json").read_text())
    ent = [e for e in (led.get("entries") or led).values() if isinstance(e, dict) and e.get("stem") == "P_20261124-100000"]
    check("W-STREAMS: the ledger records the options each run used (so a result can be traced to its settings)",
          ent and ent[0].get("opts", {}).get("bands") == "auto", ent[:1])
    # the real runner turns the options into padb_analyze flags
    fake = tmp / "fake_analyze.py"
    fake.write_text("import sys, json, pathlib\n"
                    "a = sys.argv[1:]\n"
                    "out = pathlib.Path(a[a.index('--out') + 1]); out.mkdir(parents=True, exist_ok=True)\n"
                    "(out / 'argv.json').write_text(json.dumps(a))\n"
                    "print(json.dumps({'ok': True, 'analyses': [{'name': 'n', 'status': 'ok', 'views': [], 'index': None}]}))\n")
    out_a = tmp / "argv_out"
    W.run_analysis(good, out_a, {"views": ["scatter", "boxplot"], "bands": "auto", "stream": "always", "algorithms": ["A One", "B Two"],
                                 "limits_file": "L.json", "publish_to": "P", "pdf_report": True}, 60, [sys.executable, str(fake)])
    av = json.loads((out_a / "argv.json").read_text())
    check("W-STREAMS: the real runner passes --views, --bands, --stream, one --algorithm per name, --limits-file, --publish-to, --pdf-report to padb_analyze",
          "--bands" in av and av[av.index("--bands") + 1] == "auto" and av[av.index("--stream") + 1] == "always"
          and [av[i_ + 1] for i_, x_ in enumerate(av) if x_ == "--algorithm"] == ["A One", "B Two"] and "--pdf-report" in av
          and "--limits-file" in av and "--publish-to" in av and av[av.index("--views") + 1] == "scatter,boxplot", av)
    # CLI: a bad file stops the watcher (exit 2); a file with an unknown key warns and still runs
    r_bad = subprocess.run([sys.executable, str(HERE / "padb_watch.py"), "--watch", str(wroot), "--out", str(tmp / "out_cli_bad"), "--once",
                            "--dry-run", "--streams-file", str(sdir / "junk.json")], capture_output=True, text=True, timeout=120)
    check("W-STREAMS: --streams-file with invalid JSON exits 2 with the reason and does not run", r_bad.returncode == 2 and "not valid JSON" in r_bad.stderr,
          (r_bad.returncode, r_bad.stderr[-200:]))
    r_ok = subprocess.run([sys.executable, str(HERE / "padb_watch.py"), "--watch", str(wroot), "--out", str(tmp / "out_cli_ok"), "--once",
                           "--dry-run", "--streams-file", str(sfile)], capture_output=True, text=True, timeout=120)
    check("W-STREAMS: --streams-file with a bad entry warns on stderr and still runs (dry run lists the files)",
          r_ok.returncode == 0 and "WARNING (streams file)" in r_ok.stderr and "would analyse" in r_ok.stdout, (r_ok.returncode, r_ok.stderr[-200:]))
    print("\nqa_watch: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
