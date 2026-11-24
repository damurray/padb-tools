r"""qa_limits_inpage.py -- limit overrides APPLIED IN THE PAGE (stage 4a), in a real browser.

A limits file (the one the viewer's Edit limits panel saves, or a job's `limits_file`) can now be imported
on a built HTML page. The page applies it to the limit line and to every Passing/Failing verdict (scatter
view), and tells you loudly when a view does NOT apply it yet. The semantics exist in three renderings --
numpy / DuckDB SQL (padb_limits.py, qa_limits.py) and this JavaScript -- so the main job here is
CONFORMANCE: the JS must accept and reject exactly what Python does, with the same messages, and compute the
same override at every x.

  LIMP-CONF   PADB_limovValidate == padb_limits.validate (accept/reject, message, normalised list) on a
              corpus + 300 random mutations; PADB_limovFor == padb_limits.override_arrays at random x incl.
              the edges; PADB_limovUnitScale == padb_bands.unit_scale
  LIMP-E2E    on a REAL built scatter page: Import via the bar -> reload -> Failing count == a pandas oracle,
              rows carry the override (Upper_Limit AND Spec_Hi, uncertainty 0); a kHz file is converted;
              Export round-trips; Clear restores the data's own verdicts; a bare list imports; a bad file is
              rejected with the SAME message and stores nothing
  LIMP-NOTICE a view that does not apply overrides (boxplot) says so on the bar instead of silently
              showing the data's limits; Clear works from there

Honest-exits 3 without Playwright/Chromium.     py qa_limits_inpage.py
"""
import json
import os
import random
import subprocess
import sys
import tempfile
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
        print("qa_limits_inpage: no Playwright/Chromium (%s) -- ENV-UNAVAILABLE, skipped" % exc)
        return 3
    import padb_bands
    import padb_limits as PL
    import qa_viewer_ui as fx

    tmp = Path(tempfile.mkdtemp(prefix="qa_limp_"))
    df = fx.make_fixture(tmp)
    r = subprocess.run([sys.executable, str(HERE / "padb_analyze.py"), "--input", str(tmp / "fixture.parquet"),
                        "--out", str(tmp / "o"), "--views", "scatter,boxplot"], capture_output=True, text=True, timeout=900)
    res = json.loads(r.stdout)
    files = {Path(f).name.split("_")[-1].replace(".html", ""): Path(f) for f in res["analyses"][0]["files"] if f.endswith(".html")}
    scatter, boxplot = files["scatter"], files["boxplot"]
    try:
        pg = browser.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        dialogs = []
        pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        pg.goto(scatter.as_uri())
        pg.wait_for_selector("#plot .main-svg", timeout=120000)
        pg.wait_for_timeout(800)

        # ---------------- LIMP-CONF ----------------
        def js_validate(raw):
            return pg.evaluate("(r) => PADB_limovValidate(r)", raw)

        corpus = {
            "valid stair": [{"lo": 0, "hi": 100, "upper": -76}, {"lo": 100, "hi": 350, "upper": -74, "note": "x"}],
            "unsorted input is sorted by lo": [{"lo": 100, "hi": 350, "upper": -74}, {"lo": 0, "hi": 100, "upper": -76}],
            "blank side + numeric strings": [{"lo": " 5 ", "hi": "1e2", "upper": "", "lower": "-3"}],
            "equal lo keeps the user's order": [{"lo": 1, "hi": 5, "upper": 1}, {"lo": 1, "hi": 9, "upper": 2}],
            "empty list": [],
            "min above max": [{"lo": 5, "hi": 1, "upper": 0}],
            "no limit on either side": [{"lo": 1, "hi": 2}],
            "non-numeric upper": [{"lo": 1, "hi": 2, "upper": "abc"}],
            "non-numeric range": [{"lo": "a", "hi": 2, "upper": 1}],
            "lower above upper": [{"lo": 1, "hi": 2, "upper": 1, "lower": 5}],
            "infinite string": [{"lo": 1, "hi": 2, "upper": "inf"}],
            "infinite range": [{"lo": "-inf", "hi": 2, "upper": 1}],
            "boolean is not a number": [{"lo": True, "hi": 2, "upper": 1}],
            "boolean limit": [{"lo": 1, "hi": 2, "upper": False}],
            "row not an object": [3],
            "row is a list": [[1, 2]],
            "null row": [None],
            "not a list": {"lo": 1},
            "string": "abc",
            "number": 5,
            "too many ranges": [{"lo": 0, "hi": 1, "upper": 1}] * 201,
            "exactly the maximum": [{"lo": 0, "hi": 1, "upper": 1}] * 200,
            "missing keys": [{"hi": 2, "upper": 1}],
        }

        def same_verdict(raw):
            py_list, py_err = PL.validate(raw)
            j = js_validate(raw)
            if py_err is not None:
                return j.get("error") == py_err, (py_err, j)
            js_list = j.get("list")
            norm = lambda L: json.loads(json.dumps(L))   # noqa: E731 -- floats/None through JSON, like the page stores them
            return (js_list is not None and norm(js_list) == norm(py_list)), (py_list, j)

        for label, raw in corpus.items():
            ok, det = same_verdict(raw)
            check("LIMP-CONF: validate parity -- %s" % label, ok, det)
        rr = random.Random(41)
        bad = None
        for _ in range(300):
            raw = []
            for _k in range(rr.randint(0, 4)):
                e = {"lo": rr.choice([0, 5, 10.5, "7", " 3 ", "x", None, -2, 1e3, "inf"]),
                     "hi": rr.choice([20, 100, 400, "50", "y", None, 2, float(rr.randint(0, 300))])}
                if rr.random() < 0.8:
                    e["upper"] = rr.choice([-70, -76.5, "-80", "", None, "z", 5, 0])
                if rr.random() < 0.5:
                    e["lower"] = rr.choice([-95, "", None, "-99.5", "q", -60, 3])
                if rr.random() < 0.2:
                    e["note"] = rr.choice(["spec", "", " n "])
                raw.append(e)
            ok, det = same_verdict(raw)
            if not ok:
                bad = (raw, det)
                break
        check("LIMP-CONF: validate parity on 300 random mutations (valid and invalid)", bad is None, bad)

        rng = random.Random(7)
        ok_for, badx = True, None
        for _ in range(60):
            raw = []
            for _k in range(rng.randint(1, 5)):
                a = round(rng.uniform(0, 100), 1)
                e = {"lo": a, "hi": round(a + rng.uniform(0, 40), 1)}
                if rng.random() < 0.7:
                    e["upper"] = round(rng.normal(-72, 4), 1) if hasattr(rng, "normal") else round(rng.uniform(-80, -65), 1)
                if rng.random() < 0.5 or "upper" not in e:
                    e["lower"] = round(rng.uniform(-100, -90), 1)
                raw.append(e)
            lst, err = PL.validate(raw)
            if err:
                continue
            xs = [round(rng.uniform(-5, 150), 1) for _ in range(40)] + [e["lo"] for e in lst] + [e["hi"] for e in lst]
            import numpy as np
            hv, hm, lv, lm = PL.override_arrays(np.array(xs), lst)
            got = pg.evaluate("([xs, lst]) => xs.map(x => { const o = PADB_limovFor(x, lst); return [o.hi === undefined ? null : o.hi, o.lo === undefined ? null : o.lo]; })", [xs, lst])
            for i, x in enumerate(xs):
                want = [float(hv[i]) if hm[i] else None, float(lv[i]) if lm[i] else None]
                if got[i] != want:
                    ok_for, badx = False, (x, got[i], want, lst)
                    break
            if not ok_for:
                break
        check("LIMP-CONF: PADB_limovFor == padb_limits.override_arrays at random x incl. every range edge (60 sets)", ok_for, badx)
        pairs = [("MHz", "MHz"), ("kHz", "MHz"), ("GHz", "MHz"), ("Hz", "kHz"), ("MHz", "GHz"), ("", "MHz"), ("foo", "MHz"), ("MHz", "")]
        got_s = pg.evaluate("(p) => p.map(a => PADB_limovUnitScale(a[0], a[1]))", [list(x) for x in pairs])
        want_s = [padb_bands.unit_scale(a, b) for a, b in pairs]
        check("LIMP-CONF: PADB_limovUnitScale == padb_bands.unit_scale (incl. unknown units -> 1)",
              all(abs(g - w) <= 1e-12 * max(1, abs(w)) for g, w in zip(got_s, want_s)), (got_s, want_s))
        check("LIMP-CONF teeth: the corpus really exercises both outcomes",
              any(PL.validate(v)[1] for v in corpus.values()) and any(PL.validate(v)[1] is None for v in corpus.values()))

        # ---------------- LIMP-E2E ----------------
        y, lim = df["SpurPower (dBc)"], df["Upper Limit"]
        want_data = int((y > lim).sum())
        want_ov = int((y > -90.0).sum())
        probe = """() => { try {
          var fr=document.querySelector('input[name="scat_flt"][value="failing"]'); if(fr) fr.checked=true;
          if(typeof update==='function') update();
          var n=applyFilters(DATA).length;
          var al=document.querySelector('input[name="scat_flt"][value="all"]'); if(al) al.checked=true; update();
          return n; } catch(e){ return 'ERR '+e; } }"""

        def failing():
            pg.wait_for_timeout(500)
            return pg.evaluate(probe)

        check("LIMP-E2E baseline: Failing count on the page == oracle on the data's own limits, and the bar offers Import limits",
              failing() == want_data and want_data > 0 and "Import limits" in pg.inner_text("#padb_lock_bar"),
              (failing(), want_data))
        check("LIMP-E2E teeth: the override changes the number of failing points", want_ov != want_data, (want_ov, want_data))

        def do_import(path):
            with pg.expect_navigation(timeout=60000):
                pg.set_input_files("#padb_limov_import", str(path))
            pg.wait_for_selector("#plot .main-svg", timeout=120000)
            pg.wait_for_timeout(800)

        f_mhz = tmp / "limits_mhz.json"
        PL.save_file(f_mhz, PL.validate([{"lo": 0, "hi": 400, "upper": -90, "note": "test"}])[0], "MHz")
        do_import(f_mhz)
        bar = pg.inner_text("#padb_lock_bar")
        check("LIMP-E2E: after Import the bar says the override is APPLIED (ranges and points)",
              "applied: 1 range, %d points" % len(df) in bar, bar)
        check("LIMP-E2E: Failing count follows the override (== pandas oracle, differs from the data's)",
              failing() == want_ov != want_data, (failing(), want_ov, want_data))
        row = pg.evaluate("() => ({u: DATA[0].Upper_Limit, s: DATA[0].Spec_Hi, unc: DATA[0].Unc_Hi, all: DATA.every(r => r.Spec_Hi === -90 && r.Upper_Limit === -90)})")
        check("LIMP-E2E: every row carries the override in Upper_Limit AND Spec_Hi (the classifier prefers Spec), uncertainty 0",
              row["all"] and row["unc"] == 0, row)

        # export round trip
        with pg.expect_download() as dl:
            pg.click('#padb_lock_bar span[title^="Export the overrides"]')
        exported = json.loads(Path(dl.value.path()).read_text())
        py_back, py_err = PL.validate(exported["limits"])
        check("LIMP-E2E: Export writes the viewer's limits-file format ({unit, limits}) that Python validates back to the same ranges",
              exported["unit"] == "MHz" and py_err is None and [(e["lo"], e["hi"], e["upper"]) for e in py_back] == [(0.0, 400.0, -90.0)],
              exported)

        # Clear
        with pg.expect_navigation(timeout=60000):
            pg.click('#padb_lock_bar span[title^="Remove the overrides"]')
        pg.wait_for_selector("#plot .main-svg", timeout=120000)
        pg.wait_for_timeout(800)
        check("LIMP-E2E: Clear restores the data's own verdicts exactly (the page reloads)",
              failing() == want_data and "applied:" not in pg.inner_text("#padb_lock_bar"), failing())

        # a kHz file is converted into the page's unit; a bare list imports
        f_khz = tmp / "limits_khz.json"
        PL.save_file(f_khz, PL.validate([{"lo": 0, "hi": 400000, "upper": -90}])[0], "kHz")
        do_import(f_khz)
        check("LIMP-E2E: a kHz limits file is converted to the page's MHz (same verdicts as the MHz file)", failing() == want_ov, (failing(), want_ov))
        stored = pg.evaluate("() => JSON.parse(localStorage.getItem(PADB_LIMOV_KEY)).limits")
        check("LIMP-E2E: ... and stored in MHz (0..400, not 0..400000)", stored[0]["hi"] == 400, stored)
        with pg.expect_navigation(timeout=60000):
            pg.click('#padb_lock_bar span[title^="Remove the overrides"]')
        pg.wait_for_selector("#plot .main-svg", timeout=120000)
        pg.wait_for_timeout(500)
        f_bare = tmp / "limits_bare.json"
        f_bare.write_text(json.dumps([{"lo": 0, "hi": 400, "upper": -90}]))
        do_import(f_bare)
        check("LIMP-E2E: a bare list (no {unit, limits} wrapper) imports too", failing() == want_ov, failing())
        with pg.expect_navigation(timeout=60000):
            pg.click('#padb_lock_bar span[title^="Remove the overrides"]')
        pg.wait_for_selector("#plot .main-svg", timeout=120000)
        pg.wait_for_timeout(500)

        # a bad file: rejected with Python's own message, nothing stored, no reload
        f_bad = tmp / "limits_bad.json"
        f_bad.write_text(json.dumps({"unit": "MHz", "limits": [{"lo": 9, "hi": 1, "upper": -80}]}))
        dialogs.clear()
        pg.set_input_files("#padb_limov_import", str(f_bad))
        pg.wait_for_timeout(800)
        py_msg = PL.validate([{"lo": 9, "hi": 1, "upper": -80}])[1]
        check("LIMP-E2E: a bad limits file is rejected with the SAME message Python gives and stores nothing",
              dialogs and py_msg in dialogs[0] and pg.evaluate("() => PADB_limovGet()") is None, (dialogs, py_msg))
        f_junk = tmp / "junk.json"
        f_junk.write_text("{not json")
        dialogs.clear()
        pg.set_input_files("#padb_limov_import", str(f_junk))
        pg.wait_for_timeout(800)
        check("LIMP-E2E: a file that is not JSON is refused with a message (page untouched)",
              dialogs and "not JSON" in dialogs[0] and failing() == want_data, dialogs)

        # ---------------- LIMP-NOTICE ----------------
        pg2 = browser.new_page()
        pg2.on("dialog", lambda d: d.dismiss())
        pg2.goto(boxplot.as_uri())
        pg2.wait_for_selector("#plot .main-svg", timeout=120000)
        pg2.wait_for_timeout(800)
        pg2.evaluate("(l) => PADB_limovSet(l)", PL.validate([{"lo": 0, "hi": 400, "upper": -90}])[0])
        pg2.reload()
        pg2.wait_for_selector("#plot .main-svg", timeout=120000)
        pg2.wait_for_timeout(1000)
        bar2 = pg2.inner_text("#padb_lock_bar")
        check("LIMP-NOTICE: a view that does NOT apply overrides says so loudly (not silently the data's limits)",
              "NOT applied in this view" in bar2 and "applied:" not in bar2.replace("NOT applied", ""), bar2)
        check("LIMP-NOTICE: ... it offers Export and Clear but not Import (it could not apply it)",
              "Export" in bar2 and "Clear" in bar2 and "Import limits" not in bar2, bar2)
        with pg2.expect_navigation(timeout=60000):
            pg2.click('#padb_lock_bar span[title^="Remove the overrides"]')
        pg2.wait_for_selector("#plot .main-svg", timeout=120000)
        pg2.wait_for_timeout(800)
        check("LIMP-NOTICE: Clear from that view removes the overrides for the whole test",
              "Limit overrides" not in pg2.inner_text("#padb_lock_bar")
              and pg2.evaluate("() => PADB_limovGet()") is None)
        check("LIMP-NOERR: no uncaught page errors", not errs, errs[:3])
    finally:
        browser.close()
        pw.stop()
    print("\nqa_limits_inpage: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
