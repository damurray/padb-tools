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

    py qa_viewer_ui.py
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

HERE = Path(__file__).resolve().parent
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
        check("INV-NOERR: no uncaught page errors", not errs, errs)
    finally:
        srv.terminate()
        browser.close()
        pw.stop()
    print("\nqa_viewer_ui: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
