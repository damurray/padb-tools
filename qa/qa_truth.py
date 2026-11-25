r"""qa_truth.py -- a synthetic padbloader file whose every number is KNOWN, pushed through the real pipeline, and checked
against independent calculations: limits, pass/fail classification (including values exactly ON a limit), per-frequency
statistics, and named-band edges.

The truth is closed-form (no random numbers), so nothing is derived from the tool's own output:

    value(DUT i, port, repeat, frequency index k, step) = -60 + 0.5 k + 0.7 i + 0.3 [port RF2] + 0.05 [repeat 2] + 1 [hot]
    upper limit(x) = -55 below 375, -52 from 375 to below 3200, -49 from 3200;   lower limit(x) = none below 8, -80 from 8

The frequencies include every band edge (8, 375, 3200) AND a point a hair either side of it (7.999999, 8.000001 ...), because
an edge that lands in the wrong band silently moves a failing point -- the class of bug that made scatter and boxplot
disagree on a per-band fail count (see CLAUDE.md, binary_encode).  A few cells are placed exactly ON a limit (a pass) and one
epsilon past it (a fail).  The same truth is built twice: x in MHz and x in Hz (band file stays in MHz, so unit conversion of
the bands is exercised).

  TR-CONV    the converted file carries every value, limit and step exactly (cell for cell vs the truth)
  TR-LOAD    the loader keeps the limits per row and drops nothing
  TR-FAIL    failing / passing counts in the REAL scatter page (browser) equal the truth, edge cells included
  TR-STATS   per-frequency n / mean / s / quartiles / whiskers / tolerance interval equal an independent recompute
  TR-BANDS   a named-band step shows exactly the points inside [lo, hi] (both edges inclusive), band by band; the viewer
             engines (DuckDB, pandas) count the same rows; the spec-segment edges are the truth's step positions
  TR-LIMITS  a limit override (edges inclusive, later range wins) gives the effective limit the truth says

    py qa/qa_truth.py          -> exit 1 on a failure, 3 if no browser could run (environment)
"""
import json
import os
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent   # the repository root (this file lives in qa/)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "qa"))
PASS = FAIL = 0
SKIPPED = False
# The fixture is 960 rows: this suite checks numbers, not the memory guard. On a starved machine the guard would turn the
# build into a viewer-only result (no pages) and every page check would crash; switch it off for this process and its children.
os.environ["PADB_SIZE_GUARD"] = "off"


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail))[:700] if detail else "")


# ---------------------------------------------------------------------------------------------------- the truth
XS = [0.009, 0.5, 7.999999, 8.0, 8.000001, 100.0, 374.999999, 375.0, 375.000001, 1000.0, 3199.999999, 3200.0,
      3200.000001, 10000.0, 20000.0]                     # MHz
STEPS = ("Room", "55.0 Deg C")
N_SER = 8
PORTS = ("RF1", "RF2")
BANDS_MHZ = [("DAC", 0.009, 8.0), ("Low", 8.0, 375.0), ("Mid", 375.0, 3200.0), ("High", 3200.0, 20000.0)]


def hi_lim(x):
    return -55.0 if x < 375 else (-52.0 if x < 3200 else -49.0)


def lo_lim(x):
    return None if x < 8 else -80.0


# cells put exactly on / just past a limit: (serial, port, rep, k, step) -> value
SPECIAL = {
    (0, "RF1", 0, 5, "Room"): -55.0,                 # x=100,  hi -55   : ON the limit  -> pass
    (1, "RF1", 0, 6, "Room"): -55.0 + 1e-4,          # x=374.999999 hi -55 : just past  -> FAIL
    (2, "RF1", 0, 7, "Room"): -52.0,                 # x=375,  hi -52   : ON            -> pass
    (3, "RF1", 0, 8, "Room"): -52.0 + 1e-4,          # x=375.000001     : just past     -> FAIL
    (4, "RF1", 0, 11, "Room"): -49.0 + 1e-4,         # x=3200  hi: -49 (3200 is the High step) -> FAIL
    (5, "RF1", 0, 3, "Room"): -80.0,                 # x=8,   lo -80    : ON            -> pass
    (6, "RF1", 0, 4, "Room"): -80.0 - 1e-4,          # x=8.000001       : just below    -> FAIL
    (7, "RF1", 0, 1, "Room"): -200.0,                # x=0.5: NO lower limit there      -> pass
    (0, "RF2", 1, 12, "55.0 Deg C"): -48.0,          # hot, x=3200.000001 hi -49        -> FAIL (hot rows count too)
}


def value(i, port, rep, k, step):
    if (i, port, rep, k, step) in SPECIAL:
        return SPECIAL[(i, port, rep, k, step)]
    return -60 + 0.5 * k + 0.7 * i + (0.3 if port == "RF2" else 0.0) + (0.05 if rep else 0.0) + (1.0 if step != "Room" else 0.0)


# limit overrides used by the 'limits' build (MHz, edges inclusive, the LATER range wins at the shared edge 375)
OVERRIDES = [{"lo": 8.0, "hi": 375.0, "upper": -50.0}, {"lo": 375.0, "hi": 3200.0, "upper": -53.0, "lower": -70.0}]


def eff_limits(x, ov=None):
    """Effective (upper, lower) at x (MHz) -- the data's own, replaced per side by the last override range covering x."""
    hi, lo = hi_lim(x), lo_lim(x)
    for e in ov or []:
        if e["lo"] <= x <= e["hi"]:
            if e.get("upper") is not None:
                hi = e["upper"]
            if e.get("lower") is not None:
                lo = e["lower"]
    return hi, lo


def truth_rows(scale=1.0, ov=None):
    rows = []
    for i in range(N_SER):
        for port in PORTS:
            for step in STEPS:
                for rep in (0, 1):
                    for k, x in enumerate(XS):
                        v = value(i, port, rep, k, step)
                        rows.append(dict(serial="US%05d" % (100 + i), port=port, step=step, rep=rep, k=k, x=x * scale, x_mhz=x, v=v,
                                         hi=hi_lim(x), lo=lo_lim(x), ehi=eff_limits(x, ov)[0], elo=eff_limits(x, ov)[1]))
    t = pd.DataFrame(rows)
    t["fail"] = (t["v"] > t["ehi"]) | (t["elo"].notna() & (t["v"] < t["elo"]))
    return t


def write_parquet(path, scale=1.0, unit="MHz", ov=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    t = truth_rows(scale, ov)
    df = pd.DataFrame({
        "algorithm": "Truth Spur", "result": "Truth Spur", "units": "dBc", "device": "SG6311A", "serial": t["serial"],
        "server": "PADB ORACLE SR", "step": t["step"], "run_status": "P", "run_datetime": pd.Timestamp("2026-10-01 06:00:00"),
        "value": t["v"], "limit_upper": t["hi"], "limit_lower": t["lo"].astype(float), "c_frequency": t["x"], "c_port": t["port"]})
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    md = dict(tbl.schema.metadata or {})
    md.update({b"label.c_frequency": b"Frequency", b"units.c_frequency": unit.encode(), b"label.c_port": b"Port"})
    pq.write_table(tbl.replace_schema_metadata(md), str(path))
    return t


def band_rows(t, lo, hi, scale):
    """Truth: rows with x in [lo, hi] (MHz band edges, both inclusive), x scaled to the data unit."""
    return t[(t["x"] >= lo * scale) & (t["x"] <= hi * scale)]


# ---------------------------------------------------------------------------------------------------- build
def build(work: Path, tag, scale, unit, ov=None):
    import padb_loader_adapter as A
    d = work / tag
    d.mkdir(parents=True)
    pqp = d / "truth.parquet"
    t = write_parquet(pqp, scale, unit, ov)
    made = A.convert(str(pqp), str(d / "out"), str(d / "jobs"), publish_to="", views=["scatter", "stat_summary", "boxplot", "summary"],
                     log=lambda *a, **k: None)[0]
    jp = Path(made["job_path"])
    res = jp.parent / json.loads(jp.read_text())["results_dir"]
    res.mkdir(parents=True, exist_ok=True)
    if ov:
        (jp.parent / "limits.json").write_text(json.dumps({"unit": "MHz", "limits": ov}))
        job = json.loads(jp.read_text())
        job["limits_file"] = "limits.json"
        jp.write_text(json.dumps(job))
    (res / "bands.json").write_text(json.dumps({"unit": "MHz", "bands": [{"name": n, "lo": lo, "hi": hi} for n, lo, hi in BANDS_MHZ]}))
    r = subprocess.run([sys.executable, str(HERE / "padb_v2.py"), str(jp), "--no-publish"], capture_output=True, text=True, cwd=str(HERE))
    return dict(dir=d, truth=t, made=made, job=jp, res=res, rc=r.returncode, out=r.stdout + r.stderr, scale=scale, unit=unit, ov=ov)


# ---------------------------------------------------------------------------------------------------- checks
def check_conv(B):
    t, scale = B["truth"], B["scale"]
    o = pd.read_parquet(B["made"]["data_path"])
    xcol = [c for c in o.columns if c.startswith("Frequency")][0]
    vcol = [c for c in o.columns if c.startswith("Truth Spur")][0]
    check("TR-CONV[%s]: every row converted (%d)" % (B["unit"], len(t)), len(o) == len(t), (len(o), len(t)))
    key = lambda df, s, p, st, x, v: sorted(zip(df[s], df[p], df[st], df[x].round(9), df[v].round(12)))
    o["_port"] = o["Group"].str.extract(r"Port: (RF\d)")[0]
    got = sorted(zip(o["Serial Number"], o["_port"], o["Test Step"], o[xcol].round(9), o[vcol].round(12)))
    exp = sorted(zip(t["serial"], t["port"], t["step"].map(lambda s: "Room" if s == "Room" else "55.0 Deg C"), t["x"].round(9), t["v"].round(12)))
    check("TR-CONV[%s]: serial / port / step / x / value identical cell for cell (incl. the on-limit and epsilon cells)" % B["unit"],
          got == exp, next(((a, b) for a, b in zip(got, exp) if a != b), None))
    m = o.assign(_x=o[xcol].round(9)).groupby("_x")
    hi_ok = all(set(g["Upper Limit"]) == {hi_lim(x / scale)} for x, g in m)
    check("TR-CONV[%s]: Upper Limit is the truth's step function at every x (hi edges %s)" % (B["unit"], "-55/-52/-49"), hi_ok)
    lo_ok = all((g["Lower Limit"].isna().all() if lo_lim(x / scale) is None else set(g["Lower Limit"]) == {lo_lim(x / scale)}) for x, g in m)
    check("TR-CONV[%s]: Lower Limit is absent below 8 and -80 from 8 up" % B["unit"], lo_ok)
    check("TR-CONV[%s]: x header names the unit" % B["unit"], xcol == "Frequency (%s)" % B["unit"], xcol)


def load_frame(B):
    import padb_v2 as V
    cfg = json.loads(Path(B["job"]).read_text())
    return V.load_scatter(Path(B["made"]["data_path"]), cfg), cfg


def check_load(B, df):
    t = B["truth"]
    check("TR-LOAD[%s]: loader keeps all rows" % B["unit"], len(df) == len(t), (len(df), len(t)))
    x = df["Frequency_MHz"].to_numpy()
    hi_exp = np.array([hi_lim(v / B["scale"]) for v in x])
    check("TR-LOAD[%s]: Upper_Limit per row == truth step function" % B["unit"], np.array_equal(df["Upper_Limit"].to_numpy(), hi_exp))
    lo_exp = np.array([np.nan if lo_lim(v / B["scale"]) is None else lo_lim(v / B["scale"]) for v in x])
    check("TR-LOAD[%s]: Lower_Limit per row == truth (NaN below 8)" % B["unit"], np.array_equal(df["Lower_Limit"].to_numpy(), lo_exp, equal_nan=True))
    check("TR-LOAD[%s]: temperatures Room and the hot step both present, split exactly" % B["unit"],
          df["Temperature"].value_counts().to_dict() == {"Room": len(t) // 2, df["Temperature"].unique()[0] if False else [k for k in df["Temperature"].unique() if k != "Room"][0]: len(t) // 2})


def indep(vals):
    import qa_stats_recompute as S
    return S._indep_desc(vals), S._indep_np_ti


def check_stats(B, df, cfg):
    import padb_plots as P
    import qa_stats_recompute as S
    t = B["truth"]
    out = P._aggregate_stat_data(df, {"proportion": 0.90, "confidence": 0.90, "x_unit": cfg.get("x_unit", "MHz")})
    check("TR-STATS[%s]: one condition (nothing varies but port/serial)" % B["unit"], len(out) == 1, [o["condition"] for o in out][:5])
    if len(out) != 1:
        return
    fs = {round(f["freq"], 9): f for f in out[0]["freq_stats"]}
    room = t[t["step"] == "Room"]
    bad = []
    for k, x in enumerate(XS):
        xs = round(x * B["scale"], 9)
        f = fs.get(xs)
        if f is None:
            bad.append((x, "missing"))
            continue
        g = room[room["k"] == k]
        # DUT = serial + port (RF1 and RF2 are distinct units under test); repeats averaged first
        duts = g.groupby(["serial", "port"])["v"].mean().tolist()
        e, ti = S._indep_desc(duts), S._indep_np_ti(duts, 0.9, 0.9)
        got = (f["n"], f["mean"], f["s"], f["q1"], f["q2"], f["q3"], f["lo_w"], f["hi_w"])
        exp = (e["n"], e["mean"], e["s"], e["q1"], e["q2"], e["q3"], e["lo_w"], e["hi_w"])
        if not (got[0] == exp[0] and all(abs(a - b) < 1e-6 for a, b in zip(got[1:], exp[1:]))):
            bad.append((x, "stats", got, exp))
        if (f["np_ti_lo"], f["np_ti_up"]) != (None, None) and ti != (None, None):
            if abs(f["np_ti_lo"] - ti[0]) > 1e-6 or abs(f["np_ti_up"] - ti[1]) > 1e-6:
                bad.append((x, "ti", (f["np_ti_lo"], f["np_ti_up"]), ti))
        elif (f["np_ti_lo"], f["np_ti_up"]) != ti:
            bad.append((x, "ti-none", (f["np_ti_lo"], f["np_ti_up"]), ti))
        su, sl = f["spec_up"], f["spec_lo"]
        if (su, sl) != eff_limits(x, B.get("ov")):
            bad.append((x, "spec", (su, sl)))
        if f["dup_runs"] is None:
            pass
    check("TR-STATS[%s]: stat_summary n / mean / s / Q1 / median / Q3 / whiskers / spec at all 15 frequencies == independent recompute "
          "(a DUT = serial + port, repeats averaged)" % B["unit"], not bad, bad[:3])


def _var(html, name):
    import re
    m = re.search(r"^var %s\s*=\s*(.*);\s*$" % name, html, re.M)
    return json.loads(m.group(1)) if m else None


def _page(B, suffix):
    f = [p for p in B["res"].glob("*.html") if p.name.endswith("_%s.html" % suffix) and not (suffix == "summary" and "stat_summary" in p.name)]
    return f[0].read_text(encoding="utf-8") if f else ""


def check_box(B):
    import qa_stats_recompute as S
    t = B["truth"]
    data = _var(_page(B, "boxplot"), "BOX_DATA") or []
    check("TR-BOX[%s]: boxplot data carries the one condition at both temperatures" % B["unit"],
          len(data) == 2 and "Room" in [d["temp"] for d in data], [d["temp"] for d in data])
    bad, nbad = [], []
    for d in data:
        step = "Room" if d["temp"] == "Room" else "55.0 Deg C"
        for k, f in enumerate(d["freq_stats"]):
            vals = t[(t["step"] == step) & (t["k"] == k)]["v"].tolist()          # every raw measurement (no DUT averaging in a box plot)
            e = S._indep_box(vals)
            got = (f["n"], f["mean"], f["q1"], f["q2"], f["q3"], f["lo_w"], f["hi_w"])
            exp = (e["n"], e["mean"], e["q1"], e["q2"], e["q3"], e["lo_w"], e["hi_w"])
            go, eo = sorted(f["outliers"]), e["outliers"]
            if not (got[0] == exp[0] and all(abs(a - b) < 1e-6 for a, b in zip(got[1:], exp[1:])) and len(go) == len(eo)
                    and all(abs(a - b) < 1e-6 for a, b in zip(go, eo))):
                bad.append((d["temp"], k, got, exp))
            # per-point limit verdict from the data the page embeds == the truth
            nf = sum(1 for p in f["vals_detail"] if (p["upper_limit"] is not None and p["v"] > p["upper_limit"])
                     or (p["lower_limit"] is not None and p["v"] < p["lower_limit"]))
            tf = int(t[(t["step"] == step) & (t["k"] == k)]["fail"].sum())
            if nf != tf:
                nbad.append((d["temp"], k, nf, tf))
    check("TR-BOX[%s]: every box (2 temps x 15 x) n / mean / quartiles / whiskers / outliers == independent recompute" % B["unit"], not bad, bad[:2])
    check("TR-BOX[%s]: per-box failing-point count == truth (the epsilon cells included)" % B["unit"], not nbad, nbad[:4])


def check_summary(B):
    t = B["truth"]
    data = _var(_page(B, "summary"), "DATA") or []
    bad = []
    for c in data:
        port = c["cond_keys"]["Port"]
        for tn, bt in c["by_temp"].items():
            step = "Room" if tn == "Room" else "55.0 Deg C"
            for k in range(len(XS)):
                v = t[(t["port"] == port) & (t["step"] == step) & (t["k"] == k)]["v"].to_numpy()
                got = (bt["n"][k], bt["mean"][k], bt["min_data"][k], bt["max_data"][k])
                exp = (len(v), v.mean(), v.min(), v.max())
                if got[0] != exp[0] or any(abs(a - b) > 1e-6 for a, b in zip(got[1:], exp[1:])):
                    bad.append((port, tn, k, got, exp))
        for k, x in enumerate(XS):
            if (c["spec_hi_list"][k], c["spec_lo_list"][k]) != eff_limits(x, B.get("ov")):
                bad.append((port, "spec", k, c["spec_hi_list"][k], c["spec_lo_list"][k]))
    check("TR-SUMMARY[%s]: per port and temperature n / mean / min / max, and the per-x spec lists == truth" % B["unit"], bool(data) and not bad, bad[:2])


_COUNT_JS = r"""
() => {
  const pl = document.getElementById('plot');
  let n = 0;
  (pl.data || []).forEach(tr => {
    if (!tr || !tr.x) return;
    if ((tr.mode || '').indexOf('markers') < 0) return;
    if (/^spec/i.test(String(tr.name || ''))) return;
    for (let i = 0; i < tr.x.length; i++) if (tr.x[i] !== null && tr.x[i] !== undefined) n++;
  });
  return n;
}
"""

_PF_JS = r"""
async (v) => {
  const r = document.querySelector('input[name="scat_flt"][value="' + v + '"]');
  if (!r) return 'no radio';
  r.checked = true; r.dispatchEvent(new Event('change', {bubbles: true}));
  if (typeof update === 'function') update();
  await new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)));
  await new Promise(res => setTimeout(res, 200));
  return 'ok';
}
"""

_STEP_JS = r"""
async (what) => {
  const raf = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const sel = document.getElementById('segKeySel');
  if (what.first) {
    if (!sel) return {error: 'no segKeySel'};
    if (!Array.from(sel.options).some(o => o.value === 'bands')) return {error: 'bands not offered', opts: Array.from(sel.options).map(o => o.value)};
    sel.value = 'bands'; sel.dispatchEvent(new Event('change', {bubbles: true})); await raf();
  }
  const btn = document.getElementById('segTabNext');
  if (what.first) {
    segTab(0);                       // the first band: the stepper starts ON it but shows the full range until told to apply it
  } else {
    if (!btn || btn.disabled) return {error: 'no next'};
    btn.click();
  }
  await raf(); await new Promise(r => setTimeout(r, 200));
  return {label: (document.getElementById('segTabLabel') || {}).textContent,
          lo: document.getElementById('freq_lo_txt').value, hi: document.getElementById('freq_hi_txt').value};
}
"""


def check_browser(B, br):
    t, scale, tag = B["truth"], B["scale"], B["unit"]
    page = br.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    f = [p for p in B["res"].glob("*.html") if p.name.endswith("_scatter.html")][0]
    page.goto(f.as_uri())
    page.wait_for_function("() => document.getElementById('plot') && document.getElementById('plot').data", timeout=60000)
    page.wait_for_timeout(500)
    n_all = page.evaluate(_COUNT_JS)
    check("TR-FAIL[%s]: scatter plots every measurement (%d)" % (tag, len(t)), n_all == len(t), n_all)
    exp_fail = int(t["fail"].sum())
    page.evaluate(_PF_JS, "failing")
    n_fail = page.evaluate(_COUNT_JS)
    check("TR-FAIL[%s]: 'Failing only' shows exactly the truth's %d failing points (epsilon-past cells fail, on-limit cells pass)" % (tag, exp_fail),
          n_fail == exp_fail, (n_fail, exp_fail))
    page.evaluate(_PF_JS, "passing")
    n_pass = page.evaluate(_COUNT_JS)
    check("TR-FAIL[%s]: 'Passing only' shows exactly the other %d" % (tag, len(t) - exp_fail), n_pass == len(t) - exp_fail, (n_pass, len(t) - exp_fail))
    page.evaluate(_PF_JS, "all")
    for i, (name, lo, hi) in enumerate(BANDS_MHZ):
        r = page.evaluate(_STEP_JS, {"first": i == 0})
        if "error" in r:
            check("TR-BANDS[%s]: step to band %s" % (tag, name), False, r)
            continue
        exp = band_rows(t, lo, hi, scale)
        got = page.evaluate(_COUNT_JS)
        check("TR-BANDS[%s]: band %-4s [%g..%g] MHz shows %d points == truth (both edges inclusive)" % (tag, name, lo, hi, len(exp)),
              got == len(exp), (got, len(exp), r))
        page.evaluate(_PF_JS, "failing")
        gf = page.evaluate(_COUNT_JS)
        page.evaluate(_PF_JS, "all")
        check("TR-BANDS[%s]: band %-4s failing points == truth (%d)" % (tag, name, int(exp["fail"].sum())), gf == int(exp["fail"].sum()), (gf, int(exp["fail"].sum())))
    check("TR-FAIL[%s]: no page errors" % tag, not errs, errs[:2])
    page.close()


def check_limits(B):
    """TR-LIMITS: the override file (unit MHz, data in Hz) gives the truth's effective limit on every row, edges inclusive,
    the later range winning at the shared edge; and the pages' per-point verdicts follow it (checked in check_box/browser)."""
    import padb_v2 as V
    t = B["truth"]
    df, cfg = load_frame(B)
    cfg["_job_dir"] = str(B["job"].parent)
    n = V._apply_limit_overrides(df, cfg)
    exp_n = int(sum(1 for x in t["x_mhz"] if any(e["lo"] <= x <= e["hi"] for e in B["ov"])))
    check("TR-LIMITS: rows covered by an override == truth (%d), edges inclusive" % exp_n, n == exp_n, (n, exp_n))
    hi = df["Upper_Limit"].to_numpy()
    lo = df["Lower_Limit"].to_numpy()
    exp_hi = np.array([eff_limits(x / B["scale"], B["ov"])[0] for x in df["Frequency_MHz"]])
    exp_lo = np.array([np.nan if eff_limits(x / B["scale"], B["ov"])[1] is None else eff_limits(x / B["scale"], B["ov"])[1] for x in df["Frequency_MHz"]])
    check("TR-LIMITS: effective Upper_Limit on every row == truth (blank side keeps the data's value)", np.array_equal(hi, exp_hi), None)
    check("TR-LIMITS: effective Lower_Limit on every row == truth (375 and 3200 are inside the second range)", np.array_equal(lo, exp_lo, equal_nan=True), None)
    at = lambda x: df[np.isclose(df["Frequency_MHz"], x * B["scale"], rtol=0, atol=1e-8 * B["scale"])]
    check("TR-LIMITS: the shared edge 375 takes the LATER range (-53), the point just below it the first (-50)",
          set(at(375.0)["Upper_Limit"]) == {-53.0} and set(at(374.999999)["Upper_Limit"]) == {-50.0}, (set(at(375.0)["Upper_Limit"]), set(at(374.999999)["Upper_Limit"])))
    check("TR-LIMITS: just outside the ranges the data's own limit stays (7.999999 -> -55, 3200.000001 -> -49)",
          set(at(7.999999)["Upper_Limit"]) == {-55.0} and set(at(3200.000001)["Upper_Limit"]) == {-49.0})


def check_viewer(B):
    """TR-VIEWER: the large-dataset viewer's two engines count the same rows per band and find the same spec stairs as the truth."""
    import padb_viewer as PV
    import padb_bands
    t, scale, tag = B["truth"], B["scale"], B["unit"]
    path = Path(B["made"]["data_path"])
    bands = None
    for eng in ("duckdb", "pandas"):
        try:
            D = PV.DataSet(path, engine=eng)
        except Exception as exc:                                    # engine not installed
            print("  SKIP  viewer engine %s: %s" % (eng, exc))
            continue
        bands = padb_bands.load_bands_file(B["res"] / "bands.json", D.x_unit or B["unit"])
        D.bands = [dict(b) for b in bands]
        D.compute_band_counts()
        exp = [len(band_rows(t, lo, hi, scale)) for _n, lo, hi in BANDS_MHZ]
        check("TR-VIEWER[%s/%s]: bands converted to the data unit (%s): edges %s" % (tag, eng, D.x_unit, [round(b["lo"], 3) for b in D.bands]),
              [round(b["lo"] / scale, 6) for b in D.bands] == [lo for _n, lo, _h in BANDS_MHZ], [b["lo"] for b in D.bands])
        check("TR-VIEWER[%s/%s]: rows per band %s == truth (a point ON a shared edge counts in both bands)" % (tag, eng, exp), D.band_counts == exp, (D.band_counts, exp))
        if hasattr(D, "band_row_count"):
            got = [D.band_row_count(b["lo"], b["hi"]) for b in D.bands]
            check("TR-VIEWER[%s/%s]: the raw band slice the band views are built from has the same rows" % (tag, eng), got == exp, (got, exp))
        fails = [D.scatter(b["lo"], b["hi"], [], 10 ** 7, None, "fail", None)["n_total"] for b in D.bands]
        expf = [int(band_rows(t, lo, hi, scale)["fail"].sum()) for _n, lo, hi in BANDS_MHZ]
        check("TR-VIEWER[%s/%s]: failing rows per band %s == truth" % (tag, eng, expf), fails == expf, (fails, expf))
        segs = D.spec_segments()
        got_lo = [round(s["lo"] / scale, 6) for s in segs]
        check("TR-VIEWER[%s/%s]: spec stairs start at the truth's step positions 0.009 / 8 / 375 / 3200" % (tag, eng),
              got_lo == [0.009, 8.0, 375.0, 3200.0], got_lo)
        check("TR-VIEWER[%s/%s]: stairs carry the truth's limits (-55 / -55,-80 / -52,-80 / -49,-80)" % (tag, eng),
              [(s["hi_val"], s["lo_val"]) for s in segs] == [(-55.0, None), (-55.0, -80.0), (-52.0, -80.0), (-49.0, -80.0)], [(s["hi_val"], s["lo_val"]) for s in segs])


def main() -> int:
    global SKIPPED
    work = Path(tempfile.mkdtemp(prefix="qa_truth_"))
    print("qa_truth -- known-answer data through the real pipeline  (%s)" % work)
    builds = {}
    for tag, scale, unit, ov in (("mhz", 1.0, "MHz", None), ("hz", 1e6, "Hz", None), ("limits", 1e6, "Hz", OVERRIDES)):
        B = build(work, tag, scale, unit, ov)
        B["unit"] = unit if not ov else unit + "+limits"
        builds[tag] = B
        check("BUILD[%s]: padb_v2 ran (rc 0, four views)" % unit, B["rc"] == 0 and len(list(B["res"].glob("*.html"))) >= 4, B["out"][-400:])
    for tag, B in builds.items():
        if B["rc"] != 0:
            continue
        if B["ov"]:
            check_limits(B)
            df, cfg = load_frame(B)
            cfg["_job_dir"] = str(B["job"].parent)
            import padb_v2 as V
            V._apply_limit_overrides(df, cfg)
            check_stats(B, df, cfg)
            check_box(B)
            check_summary(B)
            continue
        check_conv(B)
        df, cfg = load_frame(B)
        check_load(B, df)
        check_stats(B, df, cfg)
        check_viewer(B)
        check_box(B)
        check_summary(B)
    try:
        from playwright.sync_api import sync_playwright
        pw_cm = sync_playwright().start()
        br = pw_cm.chromium.launch()
    except Exception as exc:                                        # no browser: environment, not a defect
        print("\n  SKIP (browser tier): %s" % exc)
        SKIPPED = True
        br = None
    if br:
        for tag, B in builds.items():
            if B["rc"] == 0:
                check_browser(B, br)
        br.close()
        pw_cm.stop()
    print("\nqa_truth: %d passed, %d failed" % (PASS, FAIL))
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else (3 if SKIPPED else 0)


if __name__ == "__main__":
    sys.exit(main())
