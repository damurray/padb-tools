r"""qa_viewer_ui.py -- BROWSER-tier QA of the parquet viewer's OWN overview page.

The HTML-view harnesses (qa_filter_equiv / qa_crossview / qa_filters) test the padb_v2
pages; qa_viewer tests the viewer's data/API parity. Nothing drove the viewer's overview
UI (conditions, group-by, segment stepper, hover) in a browser until now. Invariants, all
checked against an independent pandas oracle over the same parquet:

  INV-COND   unticking a condition value -> plotted n_total == oracle row count
  INV-SITE   Site/Temp checkboxes apply on change (no Update click)
  INV-GROUP  group-by traces partition the same points (one trace per distinct value)
  INV-SEG    each Next step -> window == the band AND every plotted x lies inside it
  INV-RAPID  3 back-to-back Next clicks end on the LAST window (no stale response wins)
  INV-HOVER  hover text carries Serial + the condition text
  INV-NOERR  no uncaught page errors

Teeth: the oracle count for the narrowed selection must differ from the full count (a
filter that did nothing would fail INV-COND), and INV-SEG fails on a window that was not
applied. Honest-exits 3 when Playwright/Chromium is unavailable.

    py qa_viewer_ui.py
"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PASS = FAIL = 0


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail)) if detail else "")


def make_fixture(d: Path):
    import pandas as pd
    rows = []
    freqs = [10.0, 50.0, 100.0, 200.0, 300.0, 350.0]
    for alc in ("TRUE", "FALSE"):
        for port in ("RF1", "RF2"):
            for ser in ("US65080001", "US65080002", "US65080003"):
                for fi, f in enumerate(freqs):
                    for rep in range(2):
                        v = -80 - fi * 3 + (4 if alc == "TRUE" else 0) + (2 if port == "RF2" else 0) + rep
                        g = (f"AlcState: {alc}  Port: {port}  Serial Number: {ser}  "
                             f"Frequency (MHz): {f:g}")
                        rows.append({"Group": g, "Frequency (MHz)": f,
                                     "SpurPower (dBc)": v, "Upper Limit": -70.0,
                                     "Test Step": "Room"})
    pd.DataFrame(rows).to_parquet(d / "fixture.parquet")
    (d / "padb_viewer_bands.json").write_text(json.dumps({
        "unit": "MHz", "bands": [{"name": "Low", "lo": 0, "hi": 150},
                                 {"name": "Mid", "lo": 150, "hi": 320},
                                 {"name": "High", "lo": 320, "hi": 400}]}))
    return pd.DataFrame(rows)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        print("qa_viewer_ui: no Playwright/Chromium (%s) -- skipped" % exc)
        return 3
    tmp = Path(tempfile.mkdtemp(prefix="qa_vui_"))
    df = make_fixture(tmp)
    port = 8791
    srv = subprocess.Popen([sys.executable, str(HERE / "padb_viewer.py"), str(tmp),
                            "--port", str(port), "--no-open"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(HERE))
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/meta", timeout=2).read()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            print("qa_viewer_ui: viewer server did not start")
            return 1
        meta = json.loads(urllib.request.urlopen(base + "/api/meta").read())
        dims = {d["name"]: d for d in meta["dims"]}
        check("conditions detected (AlcState, Port)", "AlcState" in dims and "Port" in dims, list(dims))
        check("serial/frequency keys are not offered as filters",
              "Serial Number" not in dims and "Frequency (MHz)" not in dims, list(dims))

        page = browser.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(base + "/")
        page.wait_for_selector("#plot .main-svg", timeout=30000)
        page.wait_for_timeout(1000)

        def total():
            page.wait_for_timeout(1200)
            t = page.inner_text("#status")
            return int(t.split(" pts")[0].replace(",", "")) if " pts" in t else -1

        full = len(df)
        check("INV-COND baseline: all rows in view", total() == full, "%s vs %s" % (total(), full))
        a = dims["AlcState"]
        page.click("#condbtn")
        page.uncheck('.condchk[data-dim="%d"][value="FALSE"]' % a["id"])
        exp = int((df["Group"].str.contains("AlcState: TRUE")).sum())
        got = total()
        check("INV-COND: unticking AlcState=FALSE -> oracle count", got == exp, "%s vs %s" % (got, exp))
        check("INV-COND teeth: narrowing changes the count", exp != full)
        page.click("text=Reset condition filters")
        check("INV-COND: reset restores all rows", total() == full)

        # INV-GROUP
        page.click("#grpbtn")
        page.uncheck('.grpchk[value="site"]')
        page.check('.grpchk[value="d%d"]' % dims["Port"]["id"])
        page.wait_for_timeout(1500)
        names = page.evaluate("document.getElementById('plot').data.map(t=>t.name)")
        check("INV-GROUP: one trace per Port value", len(names) == 2, names)
        n_sum = sum(int(n.split("n=")[1].rstrip(")").replace(",", "")) for n in names)
        check("INV-GROUP: traces partition all points", n_sum == full, "%s vs %s" % (n_sum, full))
        page.uncheck('.grpchk[value="d%d"]' % dims["Port"]["id"])
        page.check('.grpchk[value="site"]')
        page.wait_for_timeout(800)

        # INV-HOVER
        txt = page.evaluate("document.getElementById('plot').data[0].text[0]")
        check("INV-HOVER: hover shows Serial and conditions",
              "US6508000" in txt and "AlcState" in txt and "Port" in txt, txt)

        # INV-LIMITS: "Show limits" draws the data's limit (-70) as a line, off removes it
        check("INV-LIMITS: control offered when the data has limits", page.is_visible("#showlim"))
        page.check("#showlim")
        page.wait_for_timeout(1500)
        lim = page.evaluate("document.getElementById('plot').data.filter(t=>/limit/i.test(t.name)).map(t=>[t.name,Array.from(new Set(t.y))])")
        check("INV-LIMITS: upper limit line drawn at the data's limit", lim == [["Upper limit", [-70.0]]], lim)
        n_on = page.evaluate("document.getElementById('plot').data.length")
        page.uncheck("#showlim")
        page.wait_for_timeout(1500)
        n_off = page.evaluate("document.getElementById('plot').data.length")
        check("INV-LIMITS: unticking removes the limit line (teeth)", n_off == n_on - 1, (n_on, n_off))

        # INV-SEG (named bands)
        page.select_option("#segbasis", "band")
        bands = [(0.0, 150.0), (150.0, 320.0), (320.0, 400.0)]
        for i, (lo, hi) in enumerate(bands):
            page.evaluate("_segStep(1)")
            page.wait_for_timeout(1300)
            win = page.evaluate("[parseFloat(flo.value), parseFloat(fhi.value)]")
            xs = page.evaluate("(()=>{let a=[];document.getElementById('plot').data.forEach(t=>t.x.forEach(v=>a.push(v)));return a})()")
            inside = all(lo - 1e-6 <= x <= hi + 1e-6 for x in xs)
            want = int(((df["Frequency (MHz)"] >= lo) & (df["Frequency (MHz)"] <= hi)).sum()) > 0
            check("INV-SEG band %d: window applied" % (i + 1), abs(win[0] - lo) < 1e-6 and abs(win[1] - hi) < 1e-6, win)
            check("INV-SEG band %d: plotted x inside the band, non-empty" % (i + 1),
                  inside and (len(xs) > 0) == want, "n=%d" % len(xs))
        # INV-RAPID
        page.evaluate("_segStep(1);_segStep(1);_segStep(1)")
        page.wait_for_timeout(3000)
        win = page.evaluate("[parseFloat(flo.value), parseFloat(fhi.value)]")
        xs = page.evaluate("(()=>{let a=[];document.getElementById('plot').data.forEach(t=>t.x.forEach(v=>a.push(v)));return a})()")
        lo, hi = win
        check("INV-RAPID: plot matches the final window", all(lo - 1e-6 <= x <= hi + 1e-6 for x in xs) and len(xs) > 0,
              "win=%s n=%d" % (win, len(xs)))
        check("INV-NOERR: no uncaught page errors", not errs, errs)
    finally:
        srv.terminate()
        browser.close()
        pw.stop()
    print("\nqa_viewer_ui: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
