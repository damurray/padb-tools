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
  INV-FILTCFG which conditions are exposed as filters: API validation, hide clears a hidden narrowing,
             expose-anyway (serial-like) filters == oracle, rename/reorder, group-by follows, file persisted,
             reload, Reset to automatic, hidden names in an imported filter are skipped and said so,
             a high-count condition gets a search box
  INV-SPEC   the API takes ONE FilterSpec (`spec` JSON): == the legacy params, window defaults to the
             x-range, junk -> 400 (scatter AND view), and the band page honours it
  INV-DDROW  condition filters as a ROW of dropdowns (same state as the checkbox list):
             one button per dimension, tick panel narrows == oracle, list <-> row stay in sync both
             ways, n/total badge, Select all, survives a reload, closes on an outside click
  INV-LIMEDIT live limit-override editor: bad input 400 + nothing changed; an override changes
             Failing totals == oracle, the drawn limit line, and the band views the viewer builds
             (no stale cache); Clear all restores the data's own limits
  INV-BANDEDIT live band editor: bad input rejected (400, nothing changed), good edit saves the
             shared band file, recounts == oracle, buttons + Named-bands stepper follow
  INV-PERSIST window/conds/group-by/limits/max-pts/segment index survive a reload; Reset all
             clears them; corrupt storage never breaks the page
  INV-ORA-*  independent oracle: band counts, limit stairs, envelope keeps real points + the
             global extremes (and ALL points when the cap allows), Room-only message
  INV-EQ-*   zoom == typed window == oracle; conds+window == oracle; Next keeps conds
  INV-BAND-* band views BUILT BY THE VIEWER: scatter n == oracle (window + conds), only
             selected conditions on every view, Passing+Failing partition == All

Teeth: the oracle count for the narrowed selection must differ from the full count (a
filter that did nothing would fail INV-COND), and INV-SEG fails on a window that was not
applied. Honest-exits 3 when Playwright/Chromium is unavailable.

    py qa/qa_viewer_ui.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the repository root (this file lives in qa/)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"  # never proxy the local server
PASS = FAIL = 0
LIMIT = -76.0   # freq<=100; some freq-10 TRUE rows exceed it -> the fixture has real fails
LIMIT2 = -74.0  # freq>100 -> a limit STAIR along x (tests segments + the drawn limit line)


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
    freqs = [10.0, 50.0, 100.0, 150.0, 200.0, 300.0, 350.0]   # 150 sits exactly ON the Low/Mid band edge
    for alc in ("TRUE", "FALSE"):
        for port in ("RF1", "RF2"):
            for ser in ("US65080001", "US65080002", "US65080003"):
                for fi, f in enumerate(freqs):
                    for rep in range(2):
                        v = -80 - fi * 3 + (4 if alc == "TRUE" else 0) + (2 if port == "RF2" else 0) + rep
                        g = (f"AlcState: {alc}  Port: {port}  Serial Number: {ser}  "
                             f"Frequency (MHz): {f:g}")
                        rows.append({"Group": g, "Frequency (MHz)": f,
                                     "SpurPower (dBc)": v, "Upper Limit": LIMIT if f <= 100 else LIMIT2,
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
        print("qa_viewer_ui: no Playwright/Chromium (%s) -- ENV-UNAVAILABLE, skipped" % exc)
        return 3
    tmp = Path(tempfile.mkdtemp(prefix="qa_vui_"))
    df = make_fixture(tmp)
    port = 8791
    srv = subprocess.Popen([sys.executable, str(HERE / "padb_viewer.py"), str(tmp),
                            "--port", str(port), "--no-open"],
                           stdout=open(str(tmp / "server.log"), "w"), stderr=subprocess.STDOUT,
                           cwd=str(HERE))
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/meta", timeout=10).read()
                break
            except Exception as _e:  # noqa: BLE001
                last = repr(_e)
                time.sleep(0.5)
        else:
            print("qa_viewer_ui: viewer server did not start", locals().get("last"))
            return 1
        meta = json.loads(urllib.request.urlopen(base + "/api/meta").read())
        dims = {d["name"]: d for d in meta["dims"]}
        check("conditions detected (AlcState, Port)", "AlcState" in dims and "Port" in dims, list(dims))
        check("serial/frequency keys are not offered as filters",
              "Serial Number" not in dims and "Frequency (MHz)" not in dims, list(dims))

        # INV-VIEWARGS: empty / junk window args never produce a bare 500
        for qs, ok in (("flo=&fhi=", (200,)), ("flo=abc&fhi=1", (400,))):
            try:
                code = urllib.request.urlopen(base + "/view?view=scatter&full=0&" + qs, timeout=120).status
            except urllib.error.HTTPError as e:
                code = e.code
            check("INV-VIEWARGS: /view?%s -> %s (not 500)" % (qs, ok[0]), code in ok, code)


        # ---- Step 4: independent oracle checks of the API numbers --------------------------
        def api(path):
            return json.loads(urllib.request.urlopen(base + path, timeout=60).read())

        check("INV-ORA-META: rows / serials == oracle",
              meta["rows"] == len(df) and meta["n_serials"] == 3, (meta["rows"], meta["n_serials"]))
        xs_all = sorted(df["Frequency (MHz)"].unique())
        want_bands = []
        for b in meta["bands"]:
            m = (df["Frequency (MHz)"] >= b["lo"]) & (df["Frequency (MHz)"] <= b["hi"])   # edges inclusive both ends (same as the window)
            want_bands.append(int(m.sum()))
        check("INV-ORA-BANDS: per-band counts == oracle (edge row counted in both bands)", meta["band_counts"] == want_bands,
              (meta["band_counts"], want_bands))
        check("INV-ORA-BANDS teeth: bands are not all equal", len(set(want_bands)) > 1)
        # limit stairs: first limit in file order at each x, contiguous runs along x
        per_x = df.groupby("Frequency (MHz)", sort=True)["Upper Limit"].first()
        runs = []
        for x, v in per_x.items():
            if not runs or runs[-1][2] != v:
                runs.append([x, None, v])
        for i, r in enumerate(runs):
            r[1] = runs[i + 1][0] if i + 1 < len(runs) else xs_all[-1]
        got = [(sg["lo"], sg["hi"], sg["hi_val"]) for sg in api("/api/segments")["spec"]]
        check("INV-ORA-LIMITS: spec stairs == oracle runs", got == [tuple(r) for r in runs], (got, runs))
        check("INV-ORA-LIMITS teeth: the fixture really has >1 stair", len(runs) > 1)
        # envelope: capped query returns REAL points, keeps the global extremes; uncapped returns all
        allpts = set(zip(df["Frequency (MHz)"].astype(float), df["SpurPower (dBc)"].astype(float)))
        lo_y, hi_y = float(df["SpurPower (dBc)"].min()), float(df["SpurPower (dBc)"].max())
        small = api("/api/scatter?flo=0&fhi=400&maxpts=100&by=site")
        pts = [(float(x), float(y)) for t in small["traces"] for x, y in zip(t["x"], t["y"])]
        check("INV-ORA-ENV: capped query (100 = the API's minimum cap, 168 rows -> decimated) returns only real rows", len(pts) > 0 and all(q in allpts for q in pts),
              [q for q in pts if q not in allpts][:3])
        check("INV-ORA-ENV: capped query keeps the global min and max",
              min(y for _, y in pts) == lo_y and max(y for _, y in pts) == hi_y, (lo_y, hi_y))
        check("INV-ORA-ENV: capped query respects the cap and reports the true total",
              0 < len(pts) <= 100 and small["n_total"] == len(df) and small["n_returned"] == len(pts),
              (len(pts), small["n_total"], small["n_returned"]))
        big = api("/api/scatter?flo=0&fhi=400&maxpts=4000&by=site")
        pb = sorted((float(x), float(y)) for t in big["traces"] for x, y in zip(t["x"], t["y"]))
        want = sorted(zip(df["Frequency (MHz)"].astype(float), df["SpurPower (dBc)"].astype(float)))
        check("INV-ORA-ENV: a cap above the row count returns EVERY row", pb == want, (len(pb), len(want)))
        try:
            body = urllib.request.urlopen(base + "/view?view=env_coverage&flo=0&fhi=400&full=0", timeout=60).read().decode()
        except urllib.error.HTTPError as e:
            body = "HTTP %s" % e.code
        check("INV-ORA-ROOM: Room-only data says env_coverage is unavailable (not a crash)", "Room-only" in body, body[:120])

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
        hl = page.evaluate("(() => { const i=document.querySelector('header img.kslogo'); return i ? [i.naturalWidth, i.alt, Math.round(i.getBoundingClientRect().height)] : null; })()")
        check("INV-BRAND: the Keysight logo renders in the viewer header (loaded image, alt text, ~28 px high)",
              bool(hl) and hl[0] > 100 and hl[1] == "Keysight" and 20 <= hl[2] <= 40, hl)
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

        # INV-GRPUI: the Group by panel shows each key's size, the button says what is grouped and how many groups result,
        # and the legend is a short vertical list that hides itself when there are too many groups
        page.evaluate("document.getElementById('grppanel').style.display = 'block'")   # (the INV-GROUP block above left it open; a click would close it)
        ptxt = page.inner_text("#grppanel")
        check("INV-GRPUI: every condition is offered with its number of values (AlcState (2), Port (2), Serial Number (3))",
              "AlcState (2" in ptxt and "Port (2" in ptxt and "Serial Number (3" in ptxt, ptxt[:200])
        page.check('.grpchk[value="d%d"]' % dims["AlcState"]["id"])
        page.check('.grpchk[value="d%d"]' % dims["Port"]["id"])
        page.uncheck('.grpchk[value="site"]')
        page.wait_for_timeout(1500)
        btn = page.inner_text("#grpbtn")
        check("INV-GRPUI: two conditions -> 4 groups, and the button names them with the count",
              "AlcState + Port" in btn and "(4 groups)" in btn and len(page.evaluate("document.getElementById('plot').data.filter(t=>!/limit/i.test(t.name)).map(t=>t.name)")) == 4, btn)
        check("INV-GRPUI: the panel states the group count too", "4 groups now" in page.inner_text("#grppanel"), page.inner_text("#grppanel")[-200:])
        lay = page.evaluate("(() => { const l = document.getElementById('plot').layout; return {show: l.showlegend, ori: l.legend && l.legend.orientation, r: l.margin && l.margin.r}; })()")
        check("INV-GRPUI: with few groups the legend is shown, vertical, in a reserved right margin", lay["show"] is True and lay["ori"] == "v" and lay["r"] >= 200, lay)
        page.select_option("#legsel", "off")
        page.wait_for_timeout(1200)
        lay = page.evaluate("(() => { const l = document.getElementById('plot').layout; return {show: l.showlegend, r: l.margin && l.margin.r}; })()")
        check("INV-GRPUI: Legend: off hides it and gives the plot the width back", lay["show"] is False and lay["r"] <= 20, lay)
        page.select_option("#legsel", "auto")
        page.evaluate("LEG_AUTO_MAX = 3")
        page.click("text=Update")
        page.wait_for_timeout(1200)
        lay = page.evaluate("(() => { const l = document.getElementById('plot').layout; return {show: l.showlegend, r: l.margin && l.margin.r}; })()")
        check("INV-GRPUI: Legend: auto hides the legend when there are more groups than the limit (4 > 3) and says so in the status line",
              lay["show"] is False and "legend hidden" in page.inner_text("#status"), (lay, page.inner_text("#status")))
        page.select_option("#legsel", "on")
        page.wait_for_timeout(1200)
        lay = page.evaluate("(() => { const l = document.getElementById('plot').layout; return {show: l.showlegend, r: l.margin && l.margin.r}; })()")
        check("INV-GRPUI: Legend: on forces it back even with many groups", lay["show"] is True and lay["r"] >= 200, lay)
        def _long(route):
            resp = route.fetch()
            body = resp.json()
            if body.get("traces"):
                body["traces"][0]["site"] = "L" * 80          # a very long group label
            route.fulfill(response=resp, json=body)
        page.route("**/api/scatter*", _long)
        page.click("text=Update")
        page.wait_for_timeout(1500)
        page.unroute("**/api/scatter*")
        names = page.evaluate("document.getElementById('plot').data.map(t=>t.name)")
        hov = page.evaluate("document.getElementById('plot').data[0].hovertemplate")
        check("INV-GRPUI: a very long group label (80 chars) is cut to 34 with an ellipsis in the legend, but the hover keeps all of it",
              len(names[0].split(" (n=")[0]) <= 34 and names[0].split(" (n=")[0].endswith("…") and "L" * 80 in hov, (names[0], hov[-120:]))
        page.click("text=Update")
        page.wait_for_timeout(1000)
        page.reload()
        page.wait_for_timeout(1500)
        check("INV-GRPUI: the legend choice survives a reload", page.input_value("#legsel") == "on", page.input_value("#legsel"))
        page.evaluate("() => { META.engine = 'pandas'; META.dims = []; buildGrp(); }")
        check("INV-GRPUI: the pandas fallback says why conditions are missing, instead of silently offering fewer keys",
              "pandas fallback" in page.inner_text("#grppanel") and "DuckDB" in page.inner_text("#grppanel"), page.inner_text("#grppanel")[:200])
        page.reload()
        page.wait_for_timeout(1500)
        page.select_option("#legsel", "auto")
        page.click("#grpbtn")
        for c in page.query_selector_all(".grpchk"):
            if c.get_attribute("value") != "site":
                c.set_checked(False)
        page.check('.grpchk[value="site"]')
        page.wait_for_timeout(1000)

        # INV-HOVER
        txt = page.evaluate("document.getElementById('plot').data[0].text[0]")
        check("INV-HOVER: hover shows Serial and conditions",
              "US6508000" in txt and "AlcState" in txt and "Port" in txt, txt)

        # INV-LIMITS: "Show limits" draws the data's limit (-70) as a line, off removes it
        check("INV-LIMITS: control offered when the data has limits", page.is_visible("#showlim"))
        page.check("#showlim")
        page.wait_for_timeout(1500)
        lim = page.evaluate("document.getElementById('plot').data.filter(t=>/limit/i.test(t.name)).map(t=>[t.name,Array.from(new Set(t.y))])")
        check("INV-LIMITS: upper limit line drawn at the data's limit stair (-76 then -74)",
              len(lim) == 1 and lim[0][0] == "Upper limit" and sorted(lim[0][1]) == [LIMIT, LIMIT2], lim)
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

        # ---- Step 1: filter-equivalence (zoom == typed window == oracle) ----------------
        fcol = df["Frequency (MHz)"]

        def want_n(lo, hi, alc=None):
            m = (fcol >= lo) & (fcol <= hi)
            if alc:
                m &= df["Group"].str.contains("AlcState: %s" % alc)
            return int(m.sum())

        def set_win(lo, hi):
            page.evaluate("(a)=>{flo.value=a[0];fhi.value=a[1];update();}", [lo, hi])
            return total()

        page.evaluate("resetView()")
        n_typed = set_win(60.0, 210.0)
        page.evaluate("resetView()")
        total()
        page.evaluate("Plotly.relayout('plot',{'xaxis.range':[60,210]})")
        n_zoom = total()
        win = page.evaluate("[parseFloat(flo.value), parseFloat(fhi.value)]")
        check("INV-EQ-ZOOM: drag-zoom sets the frequency boxes", win == [60.0, 210.0], win)
        check("INV-EQ-ZOOM: zoom == typed window == oracle",
              n_zoom == n_typed == want_n(60, 210), (n_zoom, n_typed, want_n(60, 210)))
        check("INV-EQ-ZOOM teeth: the window selects a strict subset", 0 < want_n(60, 210) < full)
        page.evaluate("Plotly.relayout('plot',{'xaxis.autorange':true})")
        check("INV-EQ-ZOOM: Autoscale returns to the full range", total() == full)

        # conditions + window together, then Next must change ONLY the window
        page.evaluate("resetView()")
        total()
        if not page.is_visible("#condpanel"):
            page.click("#condbtn")
        page.uncheck('.condchk[data-dim="%d"][value="FALSE"]' % a["id"])
        total()
        n_cw = set_win(60.0, 210.0)
        check("INV-EQ-COND+WIN: conditions + window == oracle",
              n_cw == want_n(60, 210, "TRUE"), (n_cw, want_n(60, 210, "TRUE")))
        page.select_option("#segbasis", "band")
        page.evaluate("_segIdx=-1")
        page.evaluate("_segStep(1)")
        n_step = total()
        win = page.evaluate("[parseFloat(flo.value), parseFloat(fhi.value)]")
        still = page.evaluate("document.querySelector('.condchk[data-dim=\"%d\"][value=\"FALSE\"]').checked" % a["id"])
        check("INV-EQ-STEP: Next changes only the window (conditions untouched)",
              still is False and n_step == want_n(win[0], win[1], "TRUE"), (still, n_step, win))
        page.click("text=Reset condition filters")
        page.evaluate("resetView()")
        total()


        # ---- Step 2: persistence across reloads ----------------------------------------------
        def reload_page():
            page.reload()
            page.wait_for_selector("#plot .main-svg", timeout=30000)
            page.wait_for_timeout(1500)

        def state():
            return page.evaluate("""() => ({
              win:[parseFloat(flo.value), parseFloat(fhi.value)], maxpts:maxpts.value,
              off:[...document.querySelectorAll('.condchk')].filter(c=>!c.checked).map(c=>c.value),
              by:[...document.querySelectorAll('.grpchk:checked')].map(c=>c.value),
              lim:showlim.checked, basis:segbasis.value, idx:_segIdx,
              key:Object.keys(localStorage).filter(k=>k.indexOf('padb_viewer_state:')===0).length})""")

        page.evaluate("localStorage.clear()")
        reload_page()
        d0 = state()
        check("INV-PERSIST baseline: clean defaults", d0["off"] == [] and d0["by"] == ["site"] and not d0["lim"], d0)
        if not page.is_visible("#condpanel"):
            page.click("#condbtn")
        page.uncheck('.condchk[data-dim="%d"][value="FALSE"]' % a["id"])
        if not page.is_visible("#grppanel"):
            page.click("#grpbtn")
        page.uncheck('.grpchk[value="site"]')
        page.check('.grpchk[value="d%d"]' % dims["Port"]["id"])
        page.check("#showlim")
        page.fill("#maxpts", "1234")
        n_before = set_win(60.0, 210.0)
        reload_page()
        d1 = state()
        n_after = total()
        check("INV-PERSIST: window restored", d1["win"] == [60.0, 210.0], d1["win"])
        check("INV-PERSIST: condition filter restored", d1["off"] == ["FALSE"], d1["off"])
        check("INV-PERSIST: group-by, limits and max-points restored",
              d1["by"] == ["d%d" % dims["Port"]["id"]] and d1["lim"] and d1["maxpts"] == "1234", d1)
        check("INV-PERSIST: restored view shows the same population (== oracle)",
              n_after == n_before == want_n(60, 210, "TRUE"), (n_before, n_after))
        xr = page.evaluate("document.getElementById('plot').layout.xaxis.range")
        check("INV-PERSIST: plot axis follows the restored window", [float(xr[0]), float(xr[1])] == [60.0, 210.0], xr)

        # segment position survives, and Next continues from it
        page.evaluate("resetView()")
        page.select_option("#segbasis", "band")
        page.evaluate("_segStep(1);")
        page.wait_for_timeout(600)
        page.evaluate("_segStep(1);")
        page.wait_for_timeout(1200)
        reload_page()
        d2 = state()
        check("INV-PERSIST: segment basis + position restored", d2["basis"] == "band" and d2["idx"] == 1, d2)
        page.evaluate("_segStep(1)")
        page.wait_for_timeout(1500)
        w3 = page.evaluate("[parseFloat(flo.value), parseFloat(fhi.value)]")
        check("INV-PERSIST: Next continues from the restored band (-> High)", w3 == [320.0, 400.0], w3)

        # Reset all clears it
        with page.expect_navigation():
            page.click("text=Reset all")
        page.wait_for_selector("#plot .main-svg", timeout=30000)
        page.wait_for_timeout(1500)
        d4 = state()
        check("INV-PERSIST: Reset all restores every default",
              d4["off"] == [] and d4["by"] == ["site"] and not d4["lim"] and d4["win"] == [10.0, 350.0]
              and d4["maxpts"] == "4000", d4)
        # corrupt / stale storage must never break the page
        page.evaluate("(() => { var k=Object.keys(localStorage).find(k=>k.indexOf('padb_viewer_state:')===0) || ('padb_viewer_state:'+META.parquet+':'+META.rows);"
                      " localStorage.setItem(k, '{not json'); })()")
        reload_page()
        check("INV-PERSIST: corrupt storage falls back to defaults (page still draws)",
              state()["win"] == [10.0, 350.0] and page.evaluate("document.getElementById('plot').data.length") > 0)
        page.evaluate("localStorage.clear()")
        reload_page()


        # ---- Step: condition filters as a row of dropdowns (same state as the list) -----------
        page.evaluate("condReset()")
        total()
        names = page.evaluate("[...document.querySelectorAll('#condrow .dd-btn')].map(b=>b.textContent.replace(/[^A-Za-z]/g,''))")
        import re as _re
        want_names = {_re.sub("[^A-Za-z]", "", k) for k in dims}
        check("INV-DDROW: exactly one dropdown button per condition dimension",
              len(names) == len(dims) and set(names) == want_names, (names, want_names))
        aid, pid = a["id"], dims["Port"]["id"]
        page.click("#ddbtn_%d" % aid)
        check("INV-DDROW: clicking a button opens its tick panel",
              page.evaluate("document.getElementById('ddp_%d').classList.contains('open')" % aid))
        page.uncheck('.ddchk[data-dim="%d"][value="FALSE"]' % aid)
        got = total()
        check("INV-DDROW: unticking in the dropdown narrows the plot == oracle",
              got == want_n(0, 1e9, "TRUE"), (got, want_n(0, 1e9, "TRUE")))
        check("INV-DDROW: the dropdown's change reached the checkbox list (same state)",
              page.evaluate("document.querySelector('.condchk[data-dim=\"%d\"][value=\"FALSE\"]').checked" % aid) is False)
        check("INV-DDROW: badge shows n/total and the button is highlighted when narrowed",
              page.inner_text("#ddb_%d" % aid) == "1/2" and page.evaluate("document.getElementById('ddbtn_%d').classList.contains('narrowed')" % aid))
        check("INV-DDROW: an un-narrowed dimension shows no badge",
              page.inner_text("#ddb_%d" % pid) == "")
        # reverse direction: change the list, the row follows
        if not page.is_visible("#condpanel"):
            page.click("#condbtn")
        page.check('.condchk[data-dim="%d"][value="FALSE"]' % aid)
        total()
        check("INV-DDROW: ticking in the checkbox LIST updates the row (box ticked, badge cleared)",
              page.evaluate("document.querySelector('.ddchk[data-dim=\"%d\"][value=\"FALSE\"]').checked" % aid)
              and page.inner_text("#ddb_%d" % aid) == "")
        page.uncheck('.condchk[data-dim="%d"][value="TRUE"]' % aid)
        total()
        check("INV-DDROW: unticking in the LIST updates the row too (1/2)",
              page.inner_text("#ddb_%d" % aid) == "1/2"
              and page.evaluate("document.querySelector('.ddchk[data-dim=\"%d\"][value=\"TRUE\"]').checked" % aid) is False)
        # Select all
        if not page.evaluate("document.getElementById('ddp_%d').classList.contains('open')" % aid):
            page.click("#ddbtn_%d" % aid)
        page.check('.ddall[data-dim="%d"]' % aid)
        check("INV-DDROW: Select all restores every value (== full count, badge cleared)",
              total() == full and page.inner_text("#ddb_%d" % aid) == "")
        # none ticked shows nothing (same rule as the list)
        page.uncheck('.ddall[data-dim="%d"]' % aid)
        check("INV-DDROW: Select all OFF shows nothing (all values unticked)", total() == 0)
        page.check('.ddall[data-dim="%d"]' % aid)
        total()
        # outside click closes the panel
        page.click("#status")
        check("INV-DDROW: an outside click closes the open panel",
              page.evaluate("document.querySelectorAll('.dd-panel.open').length") == 0)
        # survives a reload (state is the saved .condchk selection, mirrored into the row)
        page.click("#ddbtn_%d" % pid)
        page.uncheck('.ddchk[data-dim="%d"][value="RF2"]' % pid)
        total()
        reload_page()
        check("INV-DDROW: a reload restores the dropdown row (RF2 unticked, badge 1/2)",
              page.inner_text("#ddb_%d" % pid) == "1/2"
              and page.evaluate("document.querySelector('.ddchk[data-dim=\"%d\"][value=\"RF2\"]').checked" % pid) is False)
        page.evaluate("condReset()")
        total()


        # ---- Stage 2: the API takes ONE FilterSpec (legacy params still accepted) ---------------
        import urllib.parse as _up
        aidx = str(dims["AlcState"]["id"])
        aid_i = dims["AlcState"]["id"]
        sp = {"lo": 60, "hi": 330, "conds": {aidx: ["TRUE"]}}
        via_spec = api("/api/scatter?maxpts=4000&by=site&spec=" + _up.quote(json.dumps(sp)))
        via_legacy = api("/api/scatter?flo=60&fhi=330&maxpts=4000&by=site&conds=" + _up.quote(json.dumps({aidx: ["TRUE"]})))
        check("INV-SPEC: /api/scatter?spec= == the legacy params (traces, totals, groups)",
              via_spec == via_legacy and via_spec["n_total"] == want_n(60, 330, "TRUE") > 0,
              (via_spec["n_total"], via_legacy["n_total"], want_n(60, 330, "TRUE")))
        no_win = api("/api/scatter?maxpts=4000&spec=" + _up.quote(json.dumps({"conds": {aidx: ["TRUE"]}})))
        check("INV-SPEC: a spec without a window defaults to the full x-range",
              no_win["n_total"] == want_n(0, 1e9, "TRUE"), (no_win["n_total"], want_n(0, 1e9, "TRUE")))
        for label, junk in {"not JSON": "{bad", "lo above hi": json.dumps({"lo": 9, "hi": 1}),
                            "bad pf": json.dumps({"pf": "maybe"}), "not an object": "[1,2]"}.items():
            body = {}
            try:
                code = urllib.request.urlopen(base + "/api/scatter?spec=" + _up.quote(junk), timeout=30).status
            except urllib.error.HTTPError as e:
                code = e.code
                try:
                    body = json.loads(e.read())
                except ValueError:
                    body = {}          # a 500 HTML page: fall through to a clean FAIL below
            check("INV-SPEC: a junk spec (%s) -> 400 with a message, not a 500" % label,
                  code == 400 and body.get("ok") is False and "bad filter spec" in body.get("error", ""), (code,))
        try:
            code = urllib.request.urlopen(base + "/view?view=scatter&spec=" + _up.quote("{bad"), timeout=30).status
        except urllib.error.HTTPError as e:
            code = e.code
        check("INV-SPEC: /view with a junk spec -> 400 (not a 500)", code == 400, code)

        # ---- Step 3: band views BUILT BY THE VIEWER honour window + filters ---------------
        def view_url(view, lo, hi, conds=None):
            q = "view=%s&flo=%s&fhi=%s&full=0" % (view, lo, hi)
            if conds:
                q += "&conds=" + urllib.parse.quote(json.dumps(conds))
            return base + "/view?" + q

        bp = browser.new_page()
        berrs = []
        bp.on("pageerror", lambda e: berrs.append(str(e)))
        aid = str(a["id"])

        def open_view(view, lo, hi, conds=None):
            bp.goto(view_url(view, lo, hi, conds), timeout=600000)
            bp.wait_for_selector("#plot .main-svg", timeout=180000)
            bp.wait_for_timeout(800)

        # (a) unfiltered Low band: scatter n == oracle, x inside the band
        open_view("scatter", 0, 150)
        r = bp.evaluate("() => { try { var f=applyFilters(DATA); var fr=f.map(function(x){return x.Frequency_MHz;});"
                        " return {n:f.length,lo:Math.min.apply(null,fr),hi:Math.max.apply(null,fr)}; }"
                        " catch(e){ return {error:String(e)}; } }")
        check("INV-BAND-WIN: Low band scatter n == oracle, x inside the band",
              r.get("n") == want_n(0, 150) and r.get("lo", -1) >= 0 and r.get("hi", 1e9) <= 150, r)
        # (b) Mid band + AlcState=TRUE: count and the condition values
        conds = {aid: ["TRUE"]}
        open_view("scatter", 150, 320, conds)
        r = bp.evaluate("() => { try { return {n:applyFilters(DATA).length}; } catch(e){ return {error:String(e)}; } }")
        check("INV-BAND-COND: scatter band view honours the condition filter (n == oracle)",
              r.get("n") == want_n(150, 320, "TRUE"), r)

        # the same band through the ONE-spec form (what the UI now sends)
        bp.goto(base + "/view?view=scatter&full=0&spec=" + urllib.parse.quote(json.dumps(
            {"lo": 150, "hi": 320, "conds": {aid: ["TRUE"]}})), timeout=600000)
        bp.wait_for_selector("#plot .main-svg", timeout=180000)
        bp.wait_for_timeout(600)
        r = bp.evaluate("() => { try { return {n:applyFilters(DATA).length}; } catch(e){ return {error:String(e)}; } }")
        check("INV-SPEC: the band page built from ?spec= honours window + conditions (n == oracle)",
              r.get("n") == want_n(150, 320, "TRUE"), r)
        check("INV-BAND-COND teeth: filter removes rows the window alone keeps",
              want_n(150, 320, "TRUE") < want_n(150, 320))
        # (c) every aggregate view: the filtered page has FEWER conditions than the unfiltered
        # one, none mentions AlcState=FALSE, and the unfiltered page does (teeth). A dim left
        # with one value is dropped from the condition text by design, so compare the two pages.
        probes = {
            "boxplot": "() => { try { return {c:getSelectedConds()}; } catch(e){ return {error:String(e)}; } }",
            "stat_summary": ("() => { try { var r=getFilteredCondsAndParams();"
                             " return {c:r.conds.map(function(c){return c.condition;})}; }"
                             " catch(e){ return {error:String(e)}; } }"),
            "summary": ("() => { try { return {c:_getFilteredActive(true).map(function(c){return c.condition;})}; }"
                        " catch(e){ return {error:String(e)}; } }"),
        }
        for view, probe in probes.items():
            open_view(view, 150, 320)
            un = bp.evaluate(probe).get("c") or []
            open_view(view, 150, 320, conds)
            fl = bp.evaluate(probe).get("c") or []
            check("INV-BAND-COND: %s filtered page has fewer conditions (%d < %d), no AlcState=FALSE"
                  % (view, len(fl), len(un)),
                  0 < len(fl) < len(un) and not any("FALSE" in c for c in fl)
                  and any("FALSE" in c for c in un), (len(fl), len(un)))
        # (d) Passing U Failing == All, disjoint (stat_summary, Low band where fails exist)
        open_view("stat_summary", 0, 150)
        ids_js = """(mode) => { try {
          var r=document.querySelector('input[name="data_flt"][value="'+mode+'"]'); if(r) r.checked=true;
          if(typeof update==='function') update();
          var res=getFilteredCondsAndParams(), ids=[];
          res.conds.forEach(function(c){ (c.freq_stats||[]).forEach(function(fs){
            ids.push((c.condition||'')+'@'+fs.freq); }); });
          return {ids:ids}; } catch(e){ return {error:String(e)}; } }"""
        A = set((bp.evaluate(ids_js, "all") or {}).get("ids") or [])
        P = set((bp.evaluate(ids_js, "passing") or {}).get("ids") or [])
        F = set((bp.evaluate(ids_js, "failing") or {}).get("ids") or [])
        check("INV-BAND-PART: Passing U Failing == All, disjoint (stat_summary band view)",
              len(A) > 0 and (P | F) == A and not (P & F), (len(A), len(P), len(F)))
        check("INV-BAND-PART teeth: the band has real passes AND fails", len(P) > 0 and len(F) > 0, (len(P), len(F)))
        check("INV-BAND-NOERR: no page errors in any rendered band view", not berrs, berrs[:3])
        bp.close()

        # ---- Live band editor ------------------------------------------------------------
        bfile = tmp / "padb_viewer_bands.json"
        before = json.loads(bfile.read_text())

        def post_bands(payload):
            req = urllib.request.Request(base + "/api/bands", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                r = urllib.request.urlopen(req, timeout=30)
                return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        bad_cases = {
            "lo >= hi": [{"name": "X", "lo": 10, "hi": 10}],
            "non-numeric": [{"name": "X", "lo": "abc", "hi": 5}],
            "empty name": [{"name": " ", "lo": 1, "hi": 5}],
            "duplicate name": [{"name": "A", "lo": 1, "hi": 5}, {"name": "A", "lo": 6, "hi": 9}],
            "not a list": {"name": "A"},
        }
        for label, payload in bad_cases.items():
            code, body = post_bands({"bands": payload})
            check("INV-BANDEDIT: invalid input rejected with 400 (%s)" % label,
                  code == 400 and body.get("ok") is False and body.get("error"), (code, body))
        check("INV-BANDEDIT: rejected edits changed neither the file nor the live bands",
              json.loads(bfile.read_text()) == before and api("/api/bands")["bands"] == meta["bands"])

        # UI: edit Mid's upper edge 320 -> 250, rename it, add a new band, apply
        page.click("text=Edit bands")
        page.wait_for_selector("#betbl .berow")
        rows = page.query_selector_all("#betbl .berow")
        check("INV-BANDEDIT: editor lists the 3 current bands", len(rows) == 3, len(rows))
        rows[1].query_selector(".be_name").fill("Mid-narrow")
        rows[1].query_selector(".be_hi").fill("250")
        page.click("text=Add band")
        new = page.query_selector_all("#betbl .berow")[-1]
        new.query_selector(".be_name").fill("Top")
        new.query_selector(".be_lo").fill("250")
        new.query_selector(".be_hi").fill("360")
        page.click("#bandedit button:has-text('Apply')")
        page.wait_for_function("document.getElementById('bemsg').textContent.indexOf('saved')===0", timeout=15000)
        want = [("Low", 0.0, 150.0), ("Mid-narrow", 150.0, 250.0), ("Top", 250.0, 360.0), ("High", 320.0, 400.0)]
        saved = json.loads(bfile.read_text())
        check("INV-BANDEDIT: the shared band file was rewritten (sorted by lo)",
              [(b["name"], b["lo"], b["hi"]) for b in saved["bands"]]
              == [("Low", 0.0, 150.0), ("Mid-narrow", 150.0, 250.0), ("Top", 250.0, 360.0), ("High", 320.0, 400.0)]
              or [(b["name"], b["lo"], b["hi"]) for b in saved["bands"]] == sorted(want, key=lambda t: t[1]),
              saved)
        live = api("/api/bands")
        oracle = [int(((fcol >= b["lo"]) & (fcol <= b["hi"])).sum()) for b in live["bands"]]
        check("INV-BANDEDIT: live band counts == oracle for the NEW edges", live["band_counts"] == oracle,
              (live["band_counts"], oracle))
        check("INV-BANDEDIT teeth: the edit really changed the Mid count",
              live["band_counts"][1] != meta["band_counts"][1], (live["band_counts"], meta["band_counts"]))
        btns = page.evaluate("[...document.querySelectorAll('#bands button')].map(b=>b.textContent)")
        check("INV-BANDEDIT: band buttons rebuilt live with the new names and counts",
              len(btns) == 4 and btns[1].startswith("Mid-narrow (%d" % live["band_counts"][1]) and any("Top" in b for b in btns), btns)
        # the Named-bands stepper walks the NEW bands, window by window
        page.select_option("#segbasis", "band")
        page.evaluate("_segIdx=-1")
        seen = []
        for _ in range(4):
            page.evaluate("_segStep(1)")
            page.wait_for_timeout(900)
            seen.append(page.evaluate("[parseFloat(flo.value), parseFloat(fhi.value)]"))
        check("INV-BANDEDIT: the stepper visits the edited bands in order",
              seen == [[b["lo"], b["hi"]] for b in live["bands"]], (seen, live["bands"]))
        # invalid edit in the UI: error shown, nothing saved
        page.fill("#betbl .berow:nth-child(2) .be_lo", "999")
        page.click("#bandedit button:has-text('Apply')")
        page.wait_for_timeout(800)
        msg = page.inner_text("#bemsg")
        check("INV-BANDEDIT: an invalid UI edit shows the error and saves nothing",
              "lo must be below hi" in msg and json.loads(bfile.read_text()) == saved, msg)
        # delete a band + persist through a reload
        page.click("text=Revert")
        page.click("#betbl .berow:nth-child(2) >> text=delete")
        page.click("#bandedit button:has-text('Apply')")
        page.wait_for_function("document.getElementById('bemsg').textContent.indexOf('saved')===0", timeout=15000)
        check("INV-BANDEDIT: deleting a band saves (3 left)", len(json.loads(bfile.read_text())["bands"]) == 3)
        reload_page()
        check("INV-BANDEDIT: edited bands survive a reload", page.evaluate("META.bands.length") == 3)

        # ---- Live limit-override editor ---------------------------------------------------
        lfile = tmp / "padb_viewer_limits.json"
        yv = df["SpurPower (dBc)"].astype(float)
        xv = df["Frequency (MHz)"].astype(float)
        data_hi = df["Upper Limit"].astype(float)

        def post_limits(payload):
            req = urllib.request.Request(base + "/api/limits", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                r = urllib.request.urlopen(req, timeout=60)
                return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        for label, payload in {"min above max": [{"lo": 9, "hi": 1, "upper": -80}],
                               "no limit on either side": [{"lo": 0, "hi": 400}],
                               "lower above upper": [{"lo": 0, "hi": 400, "upper": -90, "lower": -80}],
                               "non-numeric": [{"lo": 0, "hi": 400, "upper": "abc"}],
                               "not a list": {"lo": 0}}.items():
            code, body = post_limits({"limits": payload})
            check("INV-LIMEDIT: invalid input rejected with 400 (%s)" % label,
                  code == 400 and body.get("ok") is False and body.get("error"), (code, body))
        check("INV-LIMEDIT: rejected edits changed nothing (no file, no live overrides)",
              not lfile.exists() and api("/api/limits")["limits"] == [])

        def fails_in_view():
            page.check('input[name="pf"][value="fail"]')
            n = total()
            page.check('input[name="pf"][value="all"]')
            total()
            return n

        page.evaluate("resetView()")
        total()
        want_data = int((yv > data_hi).sum())
        check("INV-LIMEDIT baseline: Failing total == oracle on the data's own limits",
              fails_in_view() == want_data and want_data > 0, (want_data,))

        # apply a blanket override through the real UI
        if not page.is_visible("#limedit"):
            page.click("#limbtn")
        page.click("#limedit button:has-text('Add range')")
        row = page.query_selector_all("#letbl .lerow")[-1]
        row.query_selector(".le_lo").fill("0")
        row.query_selector(".le_hi").fill("400")
        row.query_selector(".le_up").fill("-90")
        page.click("#limedit button:has-text('Apply')")
        page.wait_for_function("document.getElementById('lemsg').textContent.indexOf('saved')===0", timeout=30000)
        want_ov = int((yv > -90.0).sum())
        got = fails_in_view()
        check("INV-LIMEDIT: Failing total follows the override (== oracle, differs from the data's)",
              got == want_ov and want_ov != want_data, (got, want_ov, want_data))
        check("INV-LIMEDIT: the saved file holds the range",
              [(e["lo"], e["hi"], e["upper"]) for e in json.loads(lfile.read_text())["limits"]] == [(0.0, 400.0, -90.0)])
        flag = page.inner_text("#limflag")
        check("INV-LIMEDIT: an ACTIVE-overrides flag is shown", "ACTIVE" in flag and "1 range" in flag, flag)
        page.check("#showlim")
        page.wait_for_timeout(1500)
        lim = page.evaluate("document.getElementById('plot').data.filter(t=>/limit/i.test(t.name)).map(t=>[t.name,Array.from(new Set(t.y))])")
        check("INV-LIMEDIT: the drawn limit line is the override (-90), not the data's stair",
              lim == [["Upper limit", [-90.0]]], lim)
        page.uncheck("#showlim")
        page.wait_for_timeout(600)

        # band view built by the viewer carries the override (and is not a stale cached page)
        bp2 = browser.new_page()

        def band_probe(lo, hi):
            bp2.goto(view_url("scatter", lo, hi), timeout=600000)
            bp2.wait_for_selector("#plot .main-svg", timeout=180000)
            bp2.wait_for_timeout(600)
            return bp2.evaluate("""() => { try {
              var u={}; DATA.forEach(function(r){ u[String(r.Upper_Limit)]=1; });
              var fr=document.querySelector('input[name="scat_flt"][value="failing"]'); if(fr) fr.checked=true;
              if(typeof update==='function') update();
              return {uniq:Object.keys(u).sort(), nfail:applyFilters(DATA).length};
            } catch(e){ return {error:String(e)}; } }""")

        r = band_probe(0, 150)
        want_b = int(((yv > -90.0) & (xv <= 150)).sum())
        check("INV-LIMEDIT: band view limits == the override, Failing count == oracle",
              r.get("uniq") == ["-90"] and r.get("nfail") == want_b, (r, want_b))
        code, body = post_limits({"limits": [{"lo": 0, "hi": 400, "upper": -95}]})
        r = band_probe(0, 150)
        want_b2 = int(((yv > -95.0) & (xv <= 150)).sum())
        check("INV-LIMEDIT: editing again rebuilds the band view (no stale cached page)",
              code == 200 and r.get("uniq") == ["-95"] and r.get("nfail") == want_b2, (code, r, want_b2))

        # reload keeps the overrides (server-side file + startup load), flag still shown
        reload_page()
        check("INV-LIMEDIT: overrides survive a reload (flag + META)",
              "ACTIVE" in page.inner_text("#limflag") and page.evaluate("META.limits.length") == 1)

        # Clear all restores the data's own limits everywhere
        if not page.is_visible("#limedit"):
            page.click("#limbtn")
        page.click("#limedit button:has-text('Clear all')")
        page.wait_for_function("document.getElementById('lemsg').textContent.indexOf('cleared')>=0", timeout=30000)
        check("INV-LIMEDIT: Clear all -> Failing total back to the data's own", fails_in_view() == want_data)
        check("INV-LIMEDIT: Clear all -> flag hidden, file has no ranges",
              not page.is_visible("#limflag") and json.loads(lfile.read_text())["limits"] == [])
        r = band_probe(0, 150)
        check("INV-LIMEDIT: Clear all -> band view back on the data's stair (-76/-74)",
              r.get("uniq") == ["-74", "-76"], r)
        bp2.close()

        # ---- Which conditions are exposed as filters (stage 3b) -----------------------------------
        ffile = tmp / "padb_viewer_filters.json"
        fl = api("/api/filters")
        ad = {x["name"]: x for x in fl["all_dims"]}
        check("INV-FILTCFG: GET /api/filters lists every filterable condition with its status",
              fl["supported"] and [d["name"] for d in fl["dims"]] == ["AlcState", "Port"]
              and ad["Serial Number"]["auto"] is False and ad["Serial Number"]["reason"] == "serial-like"
              and ad["AlcState"]["exposed"] and not ad["Serial Number"]["exposed"]
              and any(u["name"] == "Frequency (MHz)" for u in fl["unavailable"]), fl)

        def post_filters(payload):
            req = urllib.request.Request(base + "/api/filters", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                r = urllib.request.urlopen(req, timeout=30)
                return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        for label, payload in {"show is not a list": {"config": {"show": "Port"}},
                               "unknown field": {"config": {"colour": 1}},
                               "label too long": {"config": {"labels": {"Port": "x" * 70}}}}.items():
            code, body = post_filters(payload)
            check("INV-FILTCFG: a bad configuration is refused with 400 (%s) and changes nothing" % label,
                  code == 400 and body.get("ok") is False and [d["name"] for d in api("/api/filters")["dims"]] == ["AlcState", "Port"]
                  and not ffile.exists(), (code, body))

        pid, said = dims["Port"]["id"], str(ad["Serial Number"]["id"])
        page.evaluate("localStorage.clear()")
        reload_page()
        page.evaluate("condReset()")
        total()
        # narrow AlcState (list) and Port (dropdown row), so we can see what hiding Port does
        if not page.is_visible("#condpanel"):
            page.click("#condbtn")
        page.uncheck('.condchk[data-dim="%d"][value="FALSE"]' % aid_i)
        page.uncheck('.condchk[data-dim="%d"][value="RF2"]' % pid)
        n_both = total()
        sg = df["Group"]
        o_alc = sg.str.contains("AlcState: TRUE")
        o_rf1 = sg.str.contains("Port: RF1")
        check("INV-FILTCFG baseline: AlcState=TRUE and Port=RF1 narrowed (== oracle)",
              n_both == int((o_alc & o_rf1).sum()), (n_both, int((o_alc & o_rf1).sum())))
        page.click("#fltbtn")
        page.wait_for_selector("#flttbl .fltrow")
        names = page.evaluate("[...document.querySelectorAll('#flttbl .fltrow')].map(r=>r.getAttribute('data-name'))")
        check("INV-FILTCFG: the panel lists every filterable condition, exposed ones first",
              names[:2] == ["AlcState", "Port"] and "Serial Number" in names and len(names) == 3, names)
        page.uncheck('#flttbl .fltrow[data-name="Port"] .flt_on')
        page.check('#flttbl .fltrow[data-name="Serial Number"] .flt_on')
        page.fill('#flttbl .fltrow[data-name="AlcState"] .flt_label', "ALC")
        for _ in range(2):      # move Serial Number to the top
            page.click('#flttbl .fltrow[data-name="Serial Number"] a[title="Move up"]')
        page.click("#fltedit button:has-text('Apply')")
        page.wait_for_function("document.getElementById('fltmsg').textContent.indexOf('filter(s) offered')>=0", timeout=30000)
        msg = page.inner_text("#fltmsg")
        btns = page.evaluate("[...document.querySelectorAll('#condrow .dd-btn')].map(b=>b.textContent.replace(/[^A-Za-z ]/g,'').trim())")
        check("INV-FILTCFG: Apply re-draws the dropdown row live: Serial Number first (expose anyway), ALC (renamed), no Port",
              btns == ["Serial Number", "ALC"], btns)
        check("INV-FILTCFG: the full checkbox list and the group-by follow the exposed set",
              page.evaluate("document.querySelectorAll('.condchk[data-dim=\"%d\"]').length" % pid) == 0
              and page.evaluate("document.querySelectorAll('.condchk[data-dim=\"%s\"]').length" % said) == 3
              and page.evaluate("document.querySelector('.grpchk[value=\"d%d\"]')" % pid) is None
              and page.evaluate("!!document.querySelector('.grpchk[value=\"d%s\"]')" % said), msg)
        n_hidden = total()
        check("INV-FILTCFG: hiding Port CLEARED its narrowing (a hidden filter restricts nothing); AlcState stays narrowed (== oracle)",
              n_hidden == int(o_alc.sum()) and "Port" in msg and "cleared" in msg, (n_hidden, int(o_alc.sum()), msg))
        # an exposed 'serial-like' key really filters
        page.click("#ddbtn_%s" % said)
        page.uncheck('.ddchk[data-dim="%s"][value="US65080003"]' % said)
        n_ser = total()
        o_ser = sg.str.contains("Serial Number: US65080001") | sg.str.contains("Serial Number: US65080002")
        check("INV-FILTCFG: the exposed Serial Number dropdown filters correctly (== oracle)",
              n_ser == int((o_alc & o_ser).sum()) < n_hidden, (n_ser, int((o_alc & o_ser).sum())))
        cfg = json.loads(ffile.read_text())
        check("INV-FILTCFG: the configuration is saved beside the data (show order + labels)",
              cfg == {"show": ["Serial Number", "AlcState"], "labels": {"AlcState": "ALC"}}, cfg)
        reload_page()
        btns2 = page.evaluate("[...document.querySelectorAll('#condrow .dd-btn')].map(b=>b.textContent.replace(/[^A-Za-z ]/g,'').trim())")
        check("INV-FILTCFG: a reload keeps the configuration AND the selection on the still-exposed filters",
              btns2 == ["Serial Number", "ALC"] and total() == n_ser, (btns2,))
        _rq = urllib.request.Request(base + "/api/filter/import", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"lock": {"dims": {"Port": ["RF1"], "AlcState": ["TRUE"]}}}).encode())
        imp = json.loads(urllib.request.urlopen(_rq, timeout=30).read())
        check("INV-FILTCFG: importing a filter file that names the hidden Port skips it and says why",
              imp["spec"]["conds"] == {aid: ["TRUE"]} and imp["skipped"]
              and "hidden by this dataset" in imp["skipped"][0]["reason"], imp)
        page.click("#fltbtn")
        page.click("#fltedit button:has-text('Reset to automatic')")
        page.wait_for_function("document.getElementById('fltmsg').textContent.indexOf('filter(s) offered')>=0", timeout=30000)
        btns3 = page.evaluate("[...document.querySelectorAll('#condrow .dd-btn')].map(b=>b.textContent.replace(/[^A-Za-z ]/g,'').trim())")
        check("INV-FILTCFG: Reset to automatic restores AlcState + Port (no rename) and writes an empty configuration",
              btns3 == ["AlcState", "Port"] and json.loads(ffile.read_text()) == {}, (btns3, ffile.read_text()))
        page.evaluate("condReset()")
        total()

        # ---- a high-count condition gets a search box (second small dataset) ------------------------
        d2 = tmp / "d2"
        d2.mkdir()
        import pandas as _pd
        rows2 = []
        for i in range(180):
            rows2.append({"Group": "AlcState: %s  Run: %d  Frequency (MHz): %g" % ("TRUE" if i % 2 else "FALSE", i % 45, 10 + i),
                          "Frequency (MHz)": 10.0 + i, "SpurPower (dBc)": -80.0 - (i % 7), "Upper Limit": -70.0, "Test Step": "Room"})
        _pd.DataFrame(rows2).to_parquet(d2 / "d2.parquet")
        srv2 = subprocess.Popen([sys.executable, str(HERE / "padb_viewer.py"), str(d2), "--port", "8798", "--no-open"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(HERE))
        try:
            b2 = "http://127.0.0.1:8798"
            for _ in range(60):
                try:
                    urllib.request.urlopen(b2 + "/api/meta", timeout=5).read()
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(0.5)
            m2 = json.loads(urllib.request.urlopen(b2 + "/api/meta").read())
            run = next(x for x in m2["all_dims"] if x["name"] == "Run")
            check("INV-FILTCFG: a 45-value condition is NOT offered by default (automatic rule) but can be exposed",
                  run["auto"] is False and not run["exposed"] and "45 values" in run["reason"], run)
            p2 = browser.new_page()
            p2.goto(b2 + "/")
            p2.wait_for_selector("#plot .main-svg", timeout=60000)
            p2.wait_for_timeout(800)
            p2.click("#fltbtn")
            p2.check('#flttbl .fltrow[data-name="Run"] .flt_on')
            p2.click("#fltedit button:has-text('Apply')")
            p2.wait_for_function("document.getElementById('fltmsg').textContent.indexOf('filter(s) offered')>=0", timeout=30000)
            rid2 = run["id"]
            p2.click("#ddbtn_%d" % rid2)
            check("INV-FILTCFG: a condition with more than 12 values gets a search box in its dropdown",
                  p2.is_visible("#ddp_%d .dd-search" % rid2))
            p2.fill("#ddp_%d .dd-search" % rid2, "4")
            shown = p2.evaluate("[...document.querySelectorAll('#ddp_%d .dd-item')].filter(l=>!l.querySelector('.ddall')&&l.style.display!=='none').map(l=>l.textContent.trim())" % rid2)
            check("INV-FILTCFG: typing in the search box filters the visible values (45 -> only those containing '4')",
                  0 < len(shown) < 45 and all("4" in x for x in shown), shown)
            p2.fill("#ddp_%d .dd-search" % rid2, "")
            for v in [str(i) for i in range(45) if i not in (3, 11)]:
                p2.uncheck('.ddchk[data-dim="%d"][value="%s"]' % (rid2, v))
            p2.wait_for_timeout(1500)
            n2 = int(p2.inner_text("#status").split(" pts")[0].replace(",", ""))
            want2 = sum(1 for i in range(180) if i % 45 in (3, 11))
            check("INV-FILTCFG: narrowing the exposed high-count condition via the dropdown == oracle", n2 == want2, (n2, want2))
            p2.close()
        finally:
            srv2.terminate()
        check("INV-NOERR: no uncaught page errors", not errs, errs)
    finally:
        srv.terminate()
        browser.close()
        pw.stop()
    print("\nqa_viewer_ui: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
