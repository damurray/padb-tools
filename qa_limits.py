r"""qa_limits.py -- QA of the editable limit overrides (padb_limits.py + both viewer engines +
the padb_v2 job keys). No browser.

The semantics have ONE definition (padb_limits.effective) rendered three ways: numpy, DuckDB
SQL (viewer engine) and a pandas frame (padb_v2). The invariants:

  LIM-VALID     bad ranges / non-numbers / lower>upper / empty rows are rejected with a message
  LIM-ORACLE    effective() == an independent scalar re-implementation, incl. the rules:
                edges inclusive, later wins at a shared edge / overlap, PER SIDE, a blank side
                keeps the data's own value, rows outside every range untouched
  LIM-SQL       the DuckDB CASE == numpy for random limit sets
  LIM-ENGINES   pandas and DuckDB DataSets agree after set_limits: fail counts, Passing/Failing
                query totals over several windows, spec-stair segments -- AND equal an oracle
                recomputed from the raw data
  LIM-REVERSIBLE set_limits([]) restores the data's own limits exactly (fail flags identical)
  LIM-FILE      save/load round trip, unit conversion, bad/missing file -> never raises
  LIM-V2        padb_v2._apply_limit_overrides: Upper/Lower_Limit AND Spec_Hi/Lo follow the override
                (the classifier prefers Spec), uncertainty zeroed, columns created when the data had
                none, relative limits_file resolved from the job dir, a bad file never fails a build

Teeth: every group is proven to fail on a broken implementation (see the mutation list in
CHANGELOG); the scalar oracle is deliberately a separate loop, not a call into padb_limits.

    py qa_limits.py
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

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


def oracle(x, hi0, lo0, limits):
    """Independent scalar re-implementation (entries sorted by lo, stable, later wins per side)."""
    ents = sorted(limits, key=lambda e: e["lo"])
    out_hi, out_lo = [], []
    for xi, h, l in zip(x, hi0, lo0):
        for e in ents:
            if e["lo"] <= xi <= e["hi"]:
                if e.get("upper") is not None:
                    h = e["upper"]
                if e.get("lower") is not None:
                    l = e["lower"]
        out_hi.append(h)
        out_lo.append(l)
    return np.array(out_hi, float), np.array(out_lo, float)


def same(a, b):
    return np.array_equal(np.asarray(a, float), np.asarray(b, float), equal_nan=True)


def main() -> int:
    import padb_limits as L

    # ---------------- LIM-VALID ----------------
    bad = {
        "min above max": [{"lo": 5, "hi": 1, "upper": 0}],
        "no limit given": [{"lo": 1, "hi": 2}],
        "non-numeric upper": [{"lo": 1, "hi": 2, "upper": "abc"}],
        "non-numeric range": [{"lo": "a", "hi": 2, "upper": 1}],
        "lower above upper": [{"lo": 1, "hi": 2, "upper": 1, "lower": 5}],
        "infinite": [{"lo": 1, "hi": 2, "upper": float("inf")}],
        "not a list": {"lo": 1},
        "row not an object": [3],
    }
    for label, raw in bad.items():
        c, err = L.validate(raw)
        check("LIM-VALID: rejects '%s' with a message" % label, c is None and bool(err), (c, err))
    c, err = L.validate([{"lo": 0, "hi": 1, "upper": 1}] * (L.MAX_LIMITS + 1))
    check("LIM-VALID: rejects more than %d ranges" % L.MAX_LIMITS, c is None and bool(err))
    for label, raw in {"boolean range": [{"lo": True, "hi": 2, "upper": 1}],
                       "boolean upper": [{"lo": 1, "hi": 2, "upper": True}],
                       "boolean lower": [{"lo": 1, "hi": 2, "lower": False}]}.items():
        c, err = L.validate(raw)
        check("LIM-VALID: a %s is not a number (JSON true/false must not become a limit of 1/0)" % label,
              c is None and bool(err), (c, err))
    c, err = L.validate([{"lo": 5, "hi": 9, "upper": "", "lower": "-3"}, {"lo": 0, "hi": 4, "upper": 2}])
    check("LIM-VALID: blank side -> None, strings parsed, result sorted by lo",
          err is None and [e["lo"] for e in c] == [0.0, 5.0] and c[1]["upper"] is None and c[1]["lower"] == -3.0, (c, err))
    c, err = L.validate([])
    check("LIM-VALID: an empty list is valid (= no overrides)", c == [] and err is None)

    # ---------------- LIM-ORACLE ----------------
    x = np.array([0, 5, 10, 10.5, 20, 20.0001, 30, 99.0])
    hi0 = np.array([-70, -70, -70, np.nan, -65, -65, np.nan, -60.0])
    lo0 = np.array([np.nan, np.nan, -90, -90, np.nan, np.nan, np.nan, -80.0])
    lim, _ = L.validate([{"lo": 0, "hi": 10, "upper": -76},
                         {"lo": 10, "hi": 20, "upper": -74, "lower": -95},
                         {"lo": 25, "hi": 40, "lower": -100}])
    eh, el = L.effective(x, hi0, lo0, lim)
    oh, ol = oracle(x, hi0, lo0, lim)
    check("LIM-ORACLE: effective() == independent scalar oracle (edges, shared edge, per side)",
          same(eh, oh) and same(el, ol), (eh.tolist(), oh.tolist()))
    check("LIM-ORACLE: edges inclusive and the LATER row wins at the shared edge x=10",
          eh[2] == -74 and eh[0] == -76 and eh[1] == -76, eh.tolist())
    check("LIM-ORACLE: a blank side keeps the data's own value (lower stays -90 at x=0..)",
          el[0] != el[0] and el[3] == -95, el.tolist())
    check("LIM-ORACLE: rows outside every range are untouched (x=99)", eh[7] == -60 and el[7] == -80)
    check("LIM-ORACLE: just past an edge is NOT covered (x=20.0001)", eh[5] == -65)
    rng = np.random.default_rng(3)
    ok = True
    for _ in range(40):
        xs = np.round(rng.uniform(0, 100, 60), 1)
        h0 = np.where(rng.random(60) < 0.3, np.nan, rng.normal(-70, 5, 60).round(1))
        l0 = np.where(rng.random(60) < 0.6, np.nan, rng.normal(-95, 3, 60).round(1))
        raw = []
        for _k in range(rng.integers(1, 6)):
            a = float(np.round(rng.uniform(0, 100), 1))
            b = float(np.round(a + rng.uniform(0, 40), 1))
            e = {"lo": a, "hi": b}
            if rng.random() < 0.7:
                e["upper"] = float(np.round(rng.normal(-72, 4), 1))
            if rng.random() < 0.5 or "upper" not in e:
                e["lower"] = float(np.round(rng.normal(-97, 3), 1))
            if "upper" in e and "lower" in e and e["lower"] > e["upper"]:
                e["lower"] = e["upper"] - 1
            raw.append(e)
        cl, err = L.validate(raw)
        a1, b1 = L.effective(xs, h0, l0, cl)
        a2, b2 = oracle(xs, h0, l0, cl)
        ok &= err is None and same(a1, a2) and same(b1, b2)
    check("LIM-ORACLE: 40 random limit sets, effective() == oracle", ok)

    # ---------------- LIM-SQL (DuckDB CASE == numpy) ----------------
    try:
        import duckdb
    except Exception:  # noqa: BLE001
        duckdb = None
    if duckdb is None:
        print("  (duckdb missing -- LIM-SQL / LIM-ENGINES DuckDB half skipped; ENV-UNAVAILABLE)")
    else:
        con = duckdb.connect()
        import pandas as pd
        ok = True
        for _ in range(25):
            xs = np.round(rng.uniform(0, 100, 80), 1)
            h0 = np.where(rng.random(80) < 0.3, np.nan, rng.normal(-70, 5, 80).round(1))
            raw = [{"lo": float(a), "hi": float(a + b), "upper": float(u)}
                   for a, b, u in zip(rng.uniform(0, 80, 3).round(1), rng.uniform(0, 30, 3).round(1), rng.normal(-72, 4, 3).round(1))]
            cl, _e = L.validate(raw)
            con.register("t_df", pd.DataFrame({"i": np.arange(80), "x": xs,
                                               "yhi0": pd.Series(h0).astype(object).where(~np.isnan(h0), None)}))
            rows = con.execute("SELECT i, %s FROM t_df ORDER BY i" % L.sql_case(cl, "x", "upper", "yhi0")).fetchall()
            gv = np.array([np.nan if r[1] is None else r[1] for r in rows], float)
            wv, _wl = L.effective(xs, h0, np.full(80, np.nan), cl)
            ok &= same(gv, wv)
            con.unregister("t_df")
        check("LIM-SQL: DuckDB CASE == numpy effective() on 25 random sets", ok)
        check("LIM-SQL: no overrides -> the base column unchanged", L.sql_case([], "x", "upper", "yhi0") == "yhi0")

    # ---------------- LIM-ENGINES + LIM-REVERSIBLE ----------------
    import padb_viewer as pv
    import pandas as pd
    tmp = Path(tempfile.mkdtemp(prefix="qa_lim_"))
    r2 = np.random.default_rng(11)
    n = 3000
    xs = r2.choice(np.round(np.linspace(1, 400, 80), 2), n)
    y = r2.normal(-80, 6, n).round(2)
    hi_data = np.where(xs <= 100, -76.0, np.where(xs <= 250, -72.0, np.nan))
    lo_data = np.where(xs > 300, -98.0, np.nan)
    ser = r2.integers(0, 6, n)
    df = pd.DataFrame({"Group": ["Port: RF%d  Serial Number: US6508%04d  Frequency (MHz): %g" % (1 + (s % 2), s, xv)
                                 for s, xv in zip(ser, xs)],
                       "Frequency (MHz)": xs, "SpurPower (dBc)": y,
                       "Upper Limit": hi_data, "Lower Limit": lo_data, "Test Step": "Room"})
    pqf = tmp / "t.parquet"
    df.to_parquet(pqf)
    engines = ["pandas"] + (["duckdb"] if duckdb else [])
    ds = {e: pv.DataSet(pqf, engine=e) for e in engines}
    limits_sets = {
        "stair override": L.validate([{"lo": 0, "hi": 150, "upper": -85}, {"lo": 150, "hi": 400, "upper": -78}])[0],
        "lower only": L.validate([{"lo": 200, "hi": 400, "lower": -88}])[0],
        "overlap, later wins": L.validate([{"lo": 0, "hi": 400, "upper": -90}, {"lo": 120, "hi": 180, "upper": -70}])[0],
    }

    def counts(d, flo, fhi, pf):
        r = d.scatter(flo, fhi, None, 4000, None, pf, None, None)
        return r["n_total"]

    for label, lims in limits_sets.items():
        eh, el = oracle(xs, hi_data, lo_data, lims)
        fail = ((~np.isnan(eh)) & (y > eh)) | ((~np.isnan(el)) & (y < el))
        for e, d in ds.items():
            d.set_limits(lims)
            wins = [(0, 400), (50, 130), (140, 310), (320, 400)]
            got = [(counts(d, a, b, "fail"), counts(d, a, b, "pass")) for a, b in wins]
            want = []
            for a, b in wins:
                m = (xs >= a) & (xs <= b)
                want.append((int((fail & m).sum()), int((~fail & m).sum())))
            check("LIM-ENGINES: %s / %s -- Failing and Passing totals == oracle on 4 windows" % (e, label),
                  got == want, (got, want))
        if len(ds) == 2:
            seg = {e: d.spec_segments() for e, d in ds.items()}
            check("LIM-ENGINES: pandas and DuckDB give the same spec stairs (%s)" % label,
                  seg["pandas"] == seg["duckdb"], seg)
    # teeth for the oracle comparison: the overrides really change the verdicts
    base_fail = int((((~np.isnan(hi_data)) & (y > hi_data)) | ((~np.isnan(lo_data)) & (y < lo_data))).sum())
    eh, el = oracle(xs, hi_data, lo_data, limits_sets["stair override"])
    ov_fail = int((((~np.isnan(eh)) & (y > eh)) | ((~np.isnan(el)) & (y < el))).sum())
    check("LIM-ENGINES teeth: the stair override changes the number of fails (%d -> %d)" % (base_fail, ov_fail),
          base_fail != ov_fail)
    for e, d in ds.items():
        d.set_limits([])
        got = (counts(d, 0, 400, "fail"), counts(d, 0, 400, "pass"))
        check("LIM-REVERSIBLE: %s -- set_limits([]) restores the data's own verdicts exactly" % e,
              got == (base_fail, n - base_fail), (got, base_fail))
        check("LIM-REVERSIBLE: %s -- has_limits follows the data again, limits list empty" % e,
              d.has_limits is True and d.meta()["limits"] == [])

    # a dataset with NO limits at all: an override creates a pass/fail basis, clearing removes it
    df2 = df.drop(columns=["Upper Limit", "Lower Limit"])
    pq2 = tmp / "nolim.parquet"
    df2.to_parquet(pq2)
    for e in engines:
        d = pv.DataSet(pq2, engine=e)
        was = d.has_limits
        d.set_limits(L.validate([{"lo": 0, "hi": 400, "upper": -80}])[0])
        got = counts(d, 0, 400, "fail")
        d.set_limits([])
        check("LIM-ENGINES: %s -- limit-less data gains pass/fail from an override (fails == oracle), loses it on clear" % e,
              was is False and got == int((y > -80).sum()) and d.has_limits is False, (was, got, int((y > -80).sum())))

    # ---------------- LIM-FILE ----------------
    f = tmp / "lim.json"
    lims = L.validate([{"lo": 0, "hi": 100, "upper": -76, "note": "spec"}, {"lo": 100, "hi": 350, "upper": -74}])[0]
    L.save_file(f, lims, "MHz")
    back, err = L.load_file(f, "MHz")
    check("LIM-FILE: save/load round trip", back == lims and err is None, (back, err))
    check("LIM-FILE: no .part temp file left behind", not (tmp / "lim.json.part").exists())
    back, err = L.load_file(f, "kHz")
    check("LIM-FILE: file unit MHz loaded into a kHz dataset is converted x1000",
          err is None and back[0]["hi"] == 100000.0 and back[1]["lo"] == 100000.0, (back, err))
    (tmp / "bad.json").write_text("{not json")
    b2, e2 = L.load_file(tmp / "bad.json", "MHz")
    check("LIM-FILE: a corrupt file -> ([], message), never raises", b2 == [] and bool(e2), (b2, e2))
    (tmp / "inv.json").write_text(json.dumps({"limits": [{"lo": 9, "hi": 1, "upper": 0}]}))
    b3, e3 = L.load_file(tmp / "inv.json", "MHz")
    check("LIM-FILE: an invalid file -> ([], message)", b3 == [] and "min must not be above max" in e3, e3)
    check("LIM-FILE: a missing file -> ([], None)", L.load_file(tmp / "nope.json", "MHz") == ([], None))
    check("LIM-FILE: find_file picks padb_viewer_limits.json",
          L.find_file([tmp]) is None and (tmp / L.DEFAULT_NAME).write_text("{}") and L.find_file([tmp]) == tmp / L.DEFAULT_NAME)

    # ---------------- LIM-V2 ----------------
    import padb_v2
    fr = pd.DataFrame({"Frequency_MHz": [10.0, 150.0, 300.0], "Value": [-80.0, -75.0, -70.0],
                       "Upper_Limit": [-76.0, -76.0, -76.0], "Spec_Hi": [-74.0, np.nan, -74.0],
                       "Unc_Hi": [2.0, 2.0, 2.0]})
    lim2 = L.validate([{"lo": 100, "hi": 200, "upper": -90, "lower": -99}])[0]
    cfg = {"limit_overrides": lim2}
    nrow = padb_v2._apply_limit_overrides(fr, cfg)
    check("LIM-V2: only the covered row is overridden (1 of 3)", nrow == 1, nrow)
    check("LIM-V2: Upper_Limit AND Spec_Hi both follow the override (the classifier prefers Spec)",
          fr.loc[1, "Upper_Limit"] == -90 and fr.loc[1, "Spec_Hi"] == -90 and fr.loc[0, "Spec_Hi"] == -74, fr.to_dict("list"))
    check("LIM-V2: uncertainty zeroed on the overridden row only", fr.loc[1, "Unc_Hi"] == 0 and fr.loc[0, "Unc_Hi"] == 2)
    check("LIM-V2: Lower_Limit / Spec_Lo columns created when the data had none",
          fr.loc[1, "Lower_Limit"] == -99 and fr.loc[1, "Spec_Lo"] == -99 and math.isnan(fr.loc[0, "Lower_Limit"]))
    check("LIM-V2: the index note says overrides are active",
          "Limit overrides applied" in cfg.get("_limits_note", "") and "1 of 3" in cfg["_limits_note"], cfg.get("_limits_note"))
    # limits_file relative to the job dir
    jd = tmp / "job"
    jd.mkdir()
    L.save_file(jd / "my_limits.json", lim2, "MHz")
    fr2 = pd.DataFrame({"Frequency_MHz": [10.0, 150.0], "Upper_Limit": [-76.0, -76.0]})
    cfg2 = {"limits_file": "my_limits.json", "_job_dir": str(jd), "x_unit": "MHz"}
    check("LIM-V2: limits_file resolved relative to the job dir", padb_v2._apply_limit_overrides(fr2, cfg2) == 1
          and fr2.loc[1, "Upper_Limit"] == -90, fr2.to_dict("list"))
    fr3 = pd.DataFrame({"Frequency_MHz": [10.0], "Upper_Limit": [-76.0]})
    check("LIM-V2: a missing limits_file is skipped (build continues, data untouched)",
          padb_v2._apply_limit_overrides(fr3, {"limits_file": "nope.json", "_job_dir": str(jd)}) == 0
          and fr3.loc[0, "Upper_Limit"] == -76)
    (jd / "broken.json").write_text("{x")
    check("LIM-V2: a corrupt limits_file is skipped, never raises",
          padb_v2._apply_limit_overrides(fr3, {"limits_file": "broken.json", "_job_dir": str(jd)}) == 0)
    check("LIM-V2: no key -> no change", padb_v2._apply_limit_overrides(fr3, {}) == 0)

    print("\nqa_limits: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
