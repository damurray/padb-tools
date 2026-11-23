r"""qa_filter_interchange.py -- ONE filter file across tools: the parquet viewer <-> a built HTML page.

The HTML pages save their analysis filters as a "lock" JSON ({dims:{label:[values]}, freq:{lo,hi},
passfail}); v3 made that the interchange format and implemented the mapping once (padb_core.lockio). This is
the proof, in a real browser against an independent pandas oracle, that it works in BOTH directions with a
REAL built page (not a mock):

  X-V2H   a filter set in the viewer -> exported -> applied on a built scatter page: the page shows exactly
          the rows the oracle says (conditions + window + failing)
  X-H2V   a filter set on the page -> its own lock read -> imported by the viewer: the viewer's count ==
          the page's count == the oracle
  X-UI    the viewer's Export button downloads that lock; its Import button applies a file (controls change,
          count == oracle); unknown conditions are skipped AND listed in the status, never emptying the view
  X-HTTP  junk import -> 400 with a message

Teeth: every comparison is against an oracle recomputed from the raw data, and the narrowed filter must
select a strict subset. Honest-exits 3 without Playwright/Chromium.

    py qa_filter_interchange.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
PASS = FAIL = 0


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail)) if detail else "")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        print("qa_filter_interchange: no Playwright/Chromium (%s) -- ENV-UNAVAILABLE, skipped" % exc)
        return 3
    import qa_viewer_ui as fx

    tmp = Path(tempfile.mkdtemp(prefix="qa_fi_"))
    df = fx.make_fixture(tmp)
    pq = tmp / "fixture.parquet"
    # a REAL built HTML page (scatter), built in a subprocess so Playwright and padb_v2 never share a process
    out = tmp / "html"
    r = subprocess.run([sys.executable, str(HERE / "padb_analyze.py"), "--input", str(pq), "--out", str(out),
                        "--views", "scatter"], capture_output=True, text=True, timeout=900)
    res = json.loads(r.stdout)
    page_html = Path(res["analyses"][0]["files"][0]) if res.get("ok") else None
    page_html = next((Path(f) for f in res["analyses"][0]["files"] if f.endswith("_scatter.html")), page_html)
    port = 8796
    srv = subprocess.Popen([sys.executable, str(HERE / "padb_viewer.py"), str(tmp), "--port", str(port), "--no-open"],
                           stdout=open(str(tmp / "server.log"), "w"), stderr=subprocess.STDOUT, cwd=str(HERE))
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/meta", timeout=5).read()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            print("qa_filter_interchange: viewer did not start")
            return 1
        meta = json.loads(urllib.request.urlopen(base + "/api/meta").read())
        aid = str(next(d["id"] for d in meta["dims"] if d["name"] == "AlcState"))

        def post(path, payload):
            req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                rr = urllib.request.urlopen(req, timeout=60)
                return rr.status, json.loads(rr.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        x, y, lim = df["Frequency (MHz)"], df["SpurPower (dBc)"], df["Upper Limit"]
        alc = df["Group"].str.contains("AlcState: TRUE")
        want_all = int((alc & (x >= 60) & (x <= 330)).sum())
        want_fail = int((alc & (x >= 60) & (x <= 330) & (y > lim)).sum())
        full_n = len(df)
        check("X teeth: the narrowed filters select a strict, non-empty subset",
              0 < want_all < full_n and 0 <= want_fail <= want_all, (want_all, want_fail, full_n))

        # ---------------- X-V2H: viewer -> page ----------------
        spec = {"lo": 60, "hi": 330, "conds": {aid: ["TRUE"]}}
        code, body = post("/api/filter/export", {"spec": spec})
        lock = body["lock"]
        check("X-V2H: the viewer exports an HTML-style lock (dims by label, freq, passfail)",
              code == 200 and lock["dims"] == {"AlcState": ["TRUE"]} and lock["freq"] == {"lo": 60, "hi": 330}
              and lock["passfail"] == "all" and lock["v"] == 1, lock)
        pg = browser.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(page_html.as_uri())
        pg.wait_for_selector("#plot .main-svg", timeout=120000)
        pg.wait_for_timeout(800)
        probe = "() => { try { return applyFilters(DATA).length; } catch(e){ return 'ERR '+e; } }"
        base_n = pg.evaluate(probe)
        check("X-V2H baseline: the unfiltered page shows every row", base_n == full_n, (base_n, full_n))
        pg.evaluate("(o) => { PADB_lockSet(o); PADB_lockApply(); }", lock)
        pg.wait_for_timeout(600)
        n = pg.evaluate(probe)
        check("X-V2H: the lock exported from the VIEWER applies on the built PAGE (== oracle)", n == want_all, (n, want_all))
        lock_f = dict(lock, passfail="failing")
        pg.evaluate("(o) => { PADB_lockSet(o); PADB_lockApply(); }", lock_f)
        pg.wait_for_timeout(600)
        n = pg.evaluate(probe)
        check("X-V2H: conditions + window + Failing together (== oracle)", n == want_fail, (n, want_fail))

        # ---------------- X-H2V: page -> viewer ----------------
        pg.reload()
        pg.wait_for_selector("#plot .main-svg", timeout=120000)
        pg.wait_for_timeout(800)
        pg.evaluate("""() => {
          var col=null; (GROUP_COLS||[]).forEach(function(p){ if(/alcstate/i.test(p[1])) col=p[0]; });
          document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){ c.checked=(c.value==='TRUE'); });
          var l=document.getElementById('freq_lo_txt'), h=document.getElementById('freq_hi_txt'); l.value='60'; h.value='330';
          var fr=document.querySelector('input[name="scat_flt"][value="failing"]'); if(fr) fr.checked=true;
          update(); }""")
        pg.wait_for_timeout(600)
        page_n = pg.evaluate(probe)
        page_lock = pg.evaluate("() => _avLockRead()")
        check("X-H2V: the PAGE's own filter selects what the oracle says", page_n == want_fail, (page_n, want_fail))
        code, imp = post("/api/filter/import", {"lock": page_lock})
        check("X-H2V: the viewer imports the page's lock (conditions + window + pass/fail applied, nothing skipped)",
              code == 200 and imp["ok"] and not imp["skipped"] and imp["spec"].get("pf") == "fail"
              and imp["spec"]["conds"] == {aid: ["TRUE"]} and abs(imp["spec"]["lo"] - 60) < 1e-6 and abs(imp["spec"]["hi"] - 330) < 1e-6,
              (code, imp))
        qs = urllib.request.quote(json.dumps(imp["spec"]))
        viewer_n = json.loads(urllib.request.urlopen(base + "/api/scatter?maxpts=4000&spec=" + qs).read())["n_total"]
        check("X-H2V: viewer count == page count == oracle", viewer_n == page_n == want_fail, (viewer_n, page_n, want_fail))

        # ---------------- X-UI: the viewer's buttons ----------------
        vp = browser.new_page(accept_downloads=True)
        verrs = []
        vp.on("pageerror", lambda e: verrs.append(str(e)))
        vp.goto(base + "/")
        vp.wait_for_selector("#plot .main-svg", timeout=60000)
        vp.wait_for_timeout(1000)
        f_in = tmp / "from_page.json"
        f_in.write_text(json.dumps(page_lock))
        vp.set_input_files("#filein", str(f_in))
        vp.wait_for_function("document.getElementById('status').textContent.indexOf('filter imported')>=0", timeout=30000)
        vp.wait_for_timeout(1500)
        st = vp.inner_text("#status")
        ui = vp.evaluate("""() => ({lo:parseFloat(flo.value), hi:parseFloat(fhi.value),
            pf:document.querySelector('input[name="pf"]:checked').value,
            off:[...document.querySelectorAll('.condchk')].filter(c=>!c.checked).map(c=>c.value)})""")
        check("X-UI: Import filter applies the page's filter to the viewer's controls",
              ui["pf"] == "fail" and ui["off"] == ["FALSE"] and abs(ui["lo"] - 60) < 1e-6 and abs(ui["hi"] - 330) < 1e-6, ui)
        check("X-UI: ... and the plotted count == oracle", st.split(" pts")[0].split("|")[-1].strip().replace(",", "").endswith(str(want_fail)) or
              ("%d pts" % want_fail) in st.replace(",", ""), st)
        with vp.expect_download() as dl:
            vp.click("text=Export filter")
        exported = json.loads(Path(dl.value.path()).read_text())
        check("X-UI: Export filter downloads the lock for the CURRENT viewer filter",
              exported["dims"] == {"AlcState": ["TRUE"]} and exported["passfail"] == "failing"
              and exported["freq"] == {"lo": 60, "hi": 330}, exported)
        pg.reload()
        pg.wait_for_selector("#plot .main-svg", timeout=120000)
        pg.wait_for_timeout(800)
        pg.evaluate("(o) => { PADB_lockSet(o); PADB_lockApply(); }", exported)
        pg.wait_for_timeout(600)
        check("X-UI: the file the viewer's button exported applies on the page too (round trip, == oracle)",
              pg.evaluate(probe) == want_fail, pg.evaluate(probe))
        # best-effort: unknown condition / value are skipped and LISTED, the rest still applies
        f_bad = tmp / "partial.json"
        f_bad.write_text(json.dumps({"v": 1, "dims": {"AlcState": ["TRUE"], "NoSuchKey": ["x"], "Port": ["RF9"]},
                                     "passfail": "all"}))
        vp.set_input_files("#filein", str(f_bad))
        vp.wait_for_function("document.getElementById('status').textContent.indexOf('skipped')>=0", timeout=30000)
        msg = vp.inner_text("#status")
        off = vp.evaluate("[...document.querySelectorAll('.condchk')].filter(c=>!c.checked).map(c=>c.value)")
        check("X-UI: unknown condition / value are SKIPPED and listed in the status; the valid part still applies",
              "NoSuchKey" in msg and "Port" in msg and off == ["FALSE"], (msg[:200], off))
        f_junk = tmp / "junk.json"
        f_junk.write_text("[1, 2, 3]")
        vp.set_input_files("#filein", str(f_junk))
        vp.wait_for_function("document.getElementById('status').textContent.indexOf('import failed')>=0", timeout=30000)
        check("X-UI: a junk file is refused with a message (controls untouched)",
              vp.evaluate("[...document.querySelectorAll('.condchk')].filter(c=>!c.checked).map(c=>c.value)") == ["FALSE"])

        # ---------------- X-HTTP ----------------
        code, body = post("/api/filter/import", {"lock": [1]})
        check("X-HTTP: a junk lock -> 400 with a message", code == 400 and body.get("ok") is False and body.get("error"), (code, body))
        code, body = post("/api/filter/export", {"spec": {"pf": "maybe"}})
        check("X-HTTP: a junk spec on export -> 400", code == 400 and body.get("ok") is False, (code, body))
        check("X-NOERR: no uncaught page errors on the page or the viewer", not errs and not verrs, (errs[:2], verrs[:2]))
    finally:
        srv.terminate()
        browser.close()
        pw.stop()
    print("\nqa_filter_interchange: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
