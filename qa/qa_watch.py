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
    print("\nqa_watch: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
