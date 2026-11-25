r"""qa_jobs_table.py -- the web app's Jobs table: Find box, quick-filter chips and Group by, driven in a real browser
against a throwaway Data folder whose jobs are KNOWN (families, schedules, results, ages, a failed run).

  JT-API      /api/jobs gives each job its test family and its last run status (the family comes from the results folder a
              run job and its plot jobs share, minus the _results / _run_results / _v2_results tail and a padbloader stamp)
  JT-FIND     every word typed must match name / description / pod / family; the count line follows
  JT-CHIPS    Failed / Never run / Scheduled / Ran today / Has results: each chip alone and combined (all must hold) equal an
              independent selection over the fixture; the number on a chip is its own match count; Clear resets
  JT-GROUP    Group by family / kind / mode / scheduled / last run: headers, counts and order equal the fixture; a header
              collapses and re-opens its rows (kept in the DOM so Select Filtered still covers them); "check group" checks
              exactly that group; the choice, chips and collapsed groups survive a reload

    py qa/qa_jobs_table.py        -> exit 1 on a failure, 3 if no browser could run (environment)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "webapp"))
PASS = FAIL = 0


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail))[:600] if detail else "")


_TMP = Path(tempfile.mkdtemp(prefix="qa_jobs_table_"))
import padb_config  # noqa: E402
_d = {**padb_config.load_defaults(), "data_dir": str(_TMP)}
padb_config.load_defaults = lambda: dict(_d)
padb_config.save_config = lambda updates: padb_config.CONFIG_PATH
import padb_batch  # noqa: E402
padb_batch._running_batch_pids = lambda exe: []
padb_batch._process_command_lines = lambda exe: {}
padb_batch._orphaned_host_pids = lambda: []
import padb_web  # noqa: E402

padb_web.DATA_DIR = _TMP
padb_web.query_task = lambda name: {}
padb_web.format_schedule_summary = lambda info: "weekly"

DAY = 86400
# name -> (cfg, scheduled?).  A results folder is SHARED by a run job and its plot jobs, so "has results" and "last run"
# belong to the folder: RESULTS maps folder -> age in days of its index.html (absent = never written).
RESULTS = {"AlphaTest_results": 0, "BetaTest_run_results": 40, "padbloader_20261124_150252_Gamma_Algo_v2_results": 3}
FIX = {
    "AlphaTest_run_job.json":            ({"pod": "AlphaTest.pod", "results_dir": "AlphaTest_results", "description": "Alpha extraction"}, True),
    "AlphaTest_Gain_v2_job.json":        ({"csv_path": "a.csv", "results_dir": "AlphaTest_results", "description": "Alpha gain plot"}, False),
    "AlphaTest_Phase_v2_job.json":       ({"csv_path": "b.csv", "results_dir": "AlphaTest_results", "description": "Alpha phase plot"}, False),
    "AlphaTest_Noise_v2_job.json":       ({"csv_path": "c.csv", "results_dir": "AlphaTest_results", "description": "Alpha noise plot"}, False),
    "BetaTest_run_job.json":             ({"pod": "BetaTest.pod", "results_dir": "BetaTest_run_results", "description": "Beta extraction harmonics", "mode": "simple"}, True),
    "BetaTest_Out_v2_job.json":          ({"csv_path": "d.csv", "results_dir": "BetaTest_run_results", "description": "Beta output plot"}, False),
    "gamma_a_v2_job.json":               ({"parquet_path": "g.parquet", "results_dir": "padbloader_20261124_150252_Gamma_Algo_v2_results", "description": "Gamma harmonics AMC2"}, False),
    "gamma_b_v2_job.json":               ({"parquet_path": "h.parquet", "results_dir": "padbloader_20261124_150252_Gamma_Algo_v2_results", "description": "Gamma other"}, False),
    "NoResultsDir_job.json":             ({"pod": "Solo.pod", "description": "no results dir"}, False),
    "Legacy_job.json":                   ({"pod": "Old.pod", "results_dir": "OldStuff_simple_results", "mode": "legacy", "description": "retired mode"}, False),
}
FAMILY = {"AlphaTest": 4, "BetaTest": 2, "Gamma_Algo": 2, "Solo": 1, "OldStuff": 1}
FAILED = "BetaTest_Out_v2_job.json"


def build_fixture():
    for name, (cfg, _sched) in FIX.items():
        (_TMP / name).write_text(json.dumps(cfg), encoding="utf-8")
    for folder, age in RESULTS.items():
        rd = _TMP / folder
        rd.mkdir(exist_ok=True)
        idx = rd / "index.html"
        idx.write_text("<html></html>")
        t = time.time() - age * DAY
        os.utime(idx, (t, t))
    sched = {"PADB_" + n[:-len(".json")] for n, (_c, s) in FIX.items() if s}
    padb_web.discover_all_padb_tasks = lambda: sched
    with padb_web._jobs_lock:
        padb_web._jobs["x1"] = {"status": "failed", "path": str(_TMP / FAILED), "name": FAILED, "log": [], "started": 5.0}
        padb_web._jobs["x0"] = {"status": "done", "path": str(_TMP / FAILED), "name": FAILED, "log": [], "started": 1.0}   # an OLDER success: the newer failure must win
        padb_web._jobs["x2"] = {"status": "done", "path": str(_TMP / "AlphaTest_run_job.json"), "name": "x", "log": [], "started": 6.0}


def oracle(jobs_api):
    """The expected selection, computed from the FIXTURE (not from the page)."""
    today = time.strftime("%Y-%m-%d")
    out = {}
    for name, (cfg, sched) in FIX.items():
        age = RESULTS.get(cfg.get("results_dir"))
        has_res = age is not None
        out[name] = {"never": not has_res, "scheduled": sched, "results": has_res,
                     "today": has_res and time.strftime("%Y-%m-%d", time.localtime(time.time() - age * DAY)) == today,
                     "failed": name == FAILED}
    return out


def main() -> int:
    build_fixture()
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, padb_web.app, threaded=True)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/" % port
    import urllib.request
    api = json.loads(urllib.request.urlopen(base + "api/jobs").read())["jobs"]
    by = {j["name"]: j for j in api}
    fam = {}
    for j in api:
        fam[j["family"]] = fam.get(j["family"], 0) + 1
    check("JT-API: families and sizes == fixture %s" % FAMILY, fam == FAMILY, fam)
    check("JT-API: last_status is the newest finished run of THIS session (failed / done / none)",
          by[FAILED]["last_status"] == "failed" and by["AlphaTest_run_job.json"]["last_status"] == "done" and by["gamma_a_v2_job.json"]["last_status"] is None,
          [(j["name"], j["last_status"]) for j in api if j["last_status"]])
    check("JT-API: a retired 'legacy' mode reads as interactive", by["Legacy_job.json"]["mode"] == "interactive")

    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        br = pw.chromium.launch()
    except Exception as exc:
        print("\n  SKIP (browser tier): %s" % exc)
        srv.shutdown()
        print("PASS=%d FAIL=%d" % (PASS, FAIL))
        return 1 if FAIL else 3
    page = br.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(base)
    page.wait_for_function("() => document.querySelectorAll('#jobsTable tbody tr').length >= %d" % len(FIX), timeout=30000)

    def rows(visible_only=False):
        return page.evaluate("""(vis) => [...document.querySelectorAll('#jobsTable tbody tr:not(.job-group-row)')]
            .filter(r => !vis || r.style.display !== 'none').map(r => r.children[1].textContent.replace(/^Edit/, '').trim())""", visible_only)

    def headers():
        return page.evaluate("""() => [...document.querySelectorAll('#jobsTable tbody tr.job-group-row')]
            .map(r => [r.dataset.group, r.querySelector('.g-meta').textContent])""")

    def count_line():
        return page.inner_text("#jobCount")

    def set_find(q):
        page.fill("#nameFilter", q)
        page.dispatch_event("#nameFilter", "input")

    def chip(c):
        page.click('#jobChips button[data-chip="%s"]' % c)

    O = oracle(api)
    names = sorted(FIX)
    check("JT-FIND: no filter shows every job and the count line says so", sorted(rows()) == names and count_line() == "Showing %d of %d jobs" % (len(FIX), len(FIX)), count_line())
    set_find("harmonics")
    exp = sorted(n for n, (c, _s) in FIX.items() if "harmonics" in (n + c.get("description", "") + c.get("pod", "")).lower())
    check("JT-FIND: a word in the DESCRIPTION finds the job (not only the file name) -> %s" % exp, sorted(rows()) == exp, rows())
    set_find("alpha plot")
    exp = sorted(n for n, (c, _s) in FIX.items() if all(w in (n + c.get("description", "") + c.get("pod", "") + by[n]["family"]).lower() for w in ("alpha", "plot")))
    check("JT-FIND: every word must match (alpha AND plot) -> %d jobs" % len(exp), sorted(rows()) == exp and len(exp) == 3, rows())
    set_find("gamma_algo")
    check("JT-FIND: the test FAMILY is searchable (gamma_algo is only in the results folder name)", sorted(rows()) == ["gamma_a_v2_job.json", "gamma_b_v2_job.json"], rows())
    set_find("zzz-nothing")
    check("JT-FIND: no match -> no rows, count 0", rows() == [] and count_line().startswith("Showing 0 of"), count_line())
    set_find("alpha")
    cl = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('#jobChips button[data-chip]')].map(b => [b.dataset.chip, b.textContent]))")
    check("JT-CHIPS: a chip's number counts ALL jobs, not just the ones the Find box leaves (scheduled stays 2 while only Alpha jobs show)",
          cl["scheduled"].endswith("(2)") and cl["failed"].endswith("(1)"), cl)
    set_find("")

    # ---- chips
    labels = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('#jobChips button[data-chip]')].map(b => [b.dataset.chip, b.textContent]))")
    for c in ("failed", "never", "scheduled", "today", "results"):
        n = sum(1 for v in O.values() if v[c])
        check("JT-CHIPS: the '%s' chip shows its own count (%d)" % (c, n), labels[c].endswith("(%d)" % n), labels[c])
    for c in ("failed", "never", "scheduled", "today", "results"):
        chip(c)
        exp = sorted(n for n, v in O.items() if v[c])
        check("JT-CHIPS: '%s' alone selects exactly the fixture's %d job(s)" % (c, len(exp)), sorted(rows()) == exp, (rows(), exp))
        chip(c)
    chip("scheduled")
    chip("results")
    exp = sorted(n for n, v in O.items() if v["scheduled"] and v["results"])
    check("JT-CHIPS: two chips together = both must hold (scheduled AND has results) -> %s" % exp, sorted(rows()) == exp, rows())
    chip("never")
    check("JT-CHIPS: contradictory chips (has results AND never run) leave nothing", rows() == [], rows())
    page.click("#chipClear")
    check("JT-CHIPS: Clear switches every chip off and shows every job again", sorted(rows()) == names and page.evaluate("() => document.querySelectorAll('#jobChips button.on').length") == 0)

    # ---- group by
    page.select_option("#groupBy", "family")
    got = {h[0].split("::", 1)[1]: h[1] for h in headers()}
    check("JT-GROUP: family headers and job counts == fixture", {k: int(v.split()[0]) for k, v in got.items()} == FAMILY, got)
    check("JT-GROUP: family headers are alphabetical", [h[0].split("::", 1)[1] for h in headers()] == sorted(FAMILY, key=str.lower), [h[0] for h in headers()])
    check("JT-GROUP: a header counts its scheduled jobs (AlphaTest: 1)", "1 scheduled" in dict(headers())["family::AlphaTest"], dict(headers())["family::AlphaTest"])
    check("JT-GROUP: every job appears exactly once under some header", sorted(rows()) == names)
    # collapse / reopen
    page.click('tr.job-group-row[data-group="family::AlphaTest"]')
    vis = rows(True)
    hidden_ok = all(n not in vis for n in names if n.startswith("AlphaTest")) and all(n in vis for n in names if not n.startswith("AlphaTest"))
    check("JT-GROUP: clicking a header hides ITS rows only (they stay in the DOM: %d rows total)" % len(rows()), hidden_ok and len(rows()) == len(FIX), vis)
    page.click("#selectFilteredBtn")
    n_checked = page.evaluate("() => document.querySelectorAll('#jobsTable tbody input[type=checkbox]:checked').length")
    check("JT-GROUP: Select Filtered still covers the jobs in a collapsed group (%d)" % len(FIX), n_checked == len(FIX), n_checked)
    page.reload()
    page.wait_for_function("() => document.querySelectorAll('#jobsTable tbody tr').length >= %d" % len(FIX))
    check("JT-GROUP: Group by and the collapsed group survive a reload",
          page.input_value("#groupBy") == "family" and "AlphaTest_run_job.json" not in rows(True) and "BetaTest_run_job.json" in rows(True), (page.input_value("#groupBy"), rows(True)))
    page.click('tr.job-group-row[data-group="family::AlphaTest"]')
    check("JT-GROUP: clicking again re-opens it", "AlphaTest_run_job.json" in rows(True))
    page.click('tr.job-group-row[data-group="family::BetaTest"] .g-check')
    chk = page.evaluate("() => [...document.querySelectorAll('#jobsTable tbody input[type=checkbox]:checked')].map(c => c.closest('tr').children[1].textContent.replace(/^Edit/, '').trim()).sort()")
    check("JT-GROUP: 'check group' checks exactly that group's jobs", chk == sorted(n for n in names if n.startswith("BetaTest")), chk)
    page.click('tr.job-group-row[data-group="family::BetaTest"] .g-check')
    check("JT-GROUP: pressing it again unchecks them", page.evaluate("() => document.querySelectorAll('#jobsTable tbody input[type=checkbox]:checked').length") == 0)
    page.select_option("#groupBy", "kind")
    kinds = {h[0].split("::", 1)[1][:3]: int(h[1].split()[0]) for h in headers()}
    check("JT-GROUP: kind -> Run jobs 4, Plot jobs 6 (fixture)", kinds == {"Run": 4, "Plo": 6}, kinds)
    page.select_option("#groupBy", "lastrun")
    order = [h[0].split("::", 1)[1] for h in headers()]
    check("JT-GROUP: last run -> Today, Last 7 days, Older, Never run (that order)", order == ["Today", "Last 7 days", "Older", "Never run"], order)
    sizes = {h[0].split("::", 1)[1]: int(h[1].split()[0]) for h in headers()}
    check("JT-GROUP: last-run sizes == fixture (folders aged 0 / 3 / 40 days / none)", sizes == {"Today": 4, "Last 7 days": 2, "Older": 2, "Never run": 2}, sizes)
    page.select_option("#groupBy", "schedule")
    check("JT-GROUP: scheduled -> Scheduled first (2), Not scheduled (8)", [(h[0].split("::", 1)[1], int(h[1].split()[0])) for h in headers()] == [("Scheduled", 2), ("Not scheduled", 8)], headers())
    page.select_option("#groupBy", "mode")
    check("JT-GROUP: mode -> interactive 9 (the retired 'legacy' reads as interactive), simple 1",
          {h[0].split("::", 1)[1]: int(h[1].split()[0]) for h in headers()} == {"interactive": 9, "simple": 1}, headers())
    page.select_option("#groupBy", "none")
    check("JT-GROUP: None removes the headers", headers() == [] and sorted(rows()) == names)
    chip("scheduled")
    page.select_option("#groupBy", "family")
    check("JT-GROUP: chips and groups combine (scheduled jobs, grouped: AlphaTest 1, BetaTest 1)",
          {h[0].split("::", 1)[1]: int(h[1].split()[0]) for h in headers()} == {"AlphaTest": 1, "BetaTest": 1}, headers())
    page.reload()
    page.wait_for_function("() => document.querySelectorAll('#jobsTable tbody tr').length >= 2")
    check("JT-GROUP: an active chip survives a reload", page.evaluate("() => document.querySelectorAll('#jobChips button.on').length") == 1 and len(rows()) == 2, rows())
    check("JT-API: no page errors", not errs, errs[:2])
    br.close()
    pw.stop()
    srv.shutdown()
    print("\nqa_jobs_table: %d passed, %d failed" % (PASS, FAIL))
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
