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
  TR-UNC     the database's datasheet spec and ONE rolled-up uncertainty (spec = limit + uncertainty) are carried through
             (columns, loader, temp-CSV round trip, hover / table / CSV labels), a file without them is unchanged, and the
             Statistical Summary's "Limit already includes" setting decides what is subtracted (checked in all four modes)

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
# "Gap" holds no data at all (between the points at 10000 and 20000) and "Edge" touches the data at ONE point only (20000, also the last
# point of High): a stepper must not offer the empty one and must keep the edge-only one (bands are inclusive at both ends)
BANDS_MHZ = [("DAC", 0.009, 8.0), ("Low", 8.0, 375.0), ("Mid", 375.0, 3200.0), ("High", 3200.0, 20000.0),
             ("Gap", 11000.0, 15000.0), ("Edge", 20000.0, 30000.0)]


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
    # the datasheet spec and the ONE rolled-up uncertainty, as the database stores them: spec = limit + uncertainty (Room 2.0, hot 1.0)
    unc = t["step"].map(lambda s: 2.0 if s == "Room" else 1.0)
    df["uncert_upper"] = unc
    df["uncert_lower"] = unc
    df["cuspec_upper"] = t["hi"] + unc
    df["cuspec_lower"] = t["lo"].astype(float) - unc
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
    non_empty = [b for b in BANDS_MHZ if len(band_rows(t, b[1], b[2], scale)) > 0]
    seg_names = page.evaluate("() => PADB_bandSegs().map(s => s.name)")
    check("TR-BANDS[%s]: the stepper offers exactly the bands that hold a point -- %s; the empty 'Gap' band is not a step, the edge-only 'Edge' band (one point, at its lower edge) is"
          % (tag, [b[0] for b in non_empty]), seg_names == [b[0] for b in non_empty], seg_names)
    for i, (name, lo, hi) in enumerate(non_empty):
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


UNC_LABEL = "Uncertainty Hi (M.U.+DEnv+GB as stored)"


def check_unc(B, df):
    """TR-UNC python half: columns written by the adapter, read by the loader, and the job's default."""
    t = B["truth"]
    o = pd.read_parquet(B["made"]["data_path"])
    tag = B["unit"]
    for c in ("Upper Spec", "Lower Spec", "Upper Uncertainty", "Lower Uncertainty"):
        check("TR-UNC[%s]: the converted table carries '%s'" % (tag, c), c in o.columns, list(o.columns))
    xcol = [c for c in o.columns if c.startswith("Frequency")][0]
    exp_unc = o["Test Step"].map(lambda s: 2.0 if s == "Room" else 1.0)
    check("TR-UNC[%s]: the uncertainty is the stored value per row (Room 2.0, hot 1.0)" % tag, np.array_equal(o["Upper Uncertainty"].to_numpy(), exp_unc.to_numpy()))
    check("TR-UNC[%s]: the identity spec = limit + uncertainty holds on every row (upper side)" % tag,
          np.allclose(o["Upper Spec"].to_numpy(), (o["Upper Limit"] + o["Upper Uncertainty"]).to_numpy(), atol=1e-12))
    low = o["Lower Limit"].notna()
    check("TR-UNC[%s]: the lower spec exists exactly where the lower limit does" % tag, np.array_equal(o["Lower Spec"].notna().to_numpy(), low.to_numpy()))
    check("TR-UNC[%s]: 'has limits' still means the TEST limit (spec and uncertainty are not limits)" % tag, B["made"]["limits"] is True)
    # the loader: Spec_Hi/Lo and Unc_Hi/Lo from the columns, row for row
    check("TR-UNC[%s]: the loader fills Spec_Hi / Spec_Lo / Unc_Hi / Unc_Lo from the columns" % tag,
          all(c in df.columns for c in ("Spec_Hi", "Spec_Lo", "Unc_Hi", "Unc_Lo")) and df["Spec_Hi"].notna().all() and df["Unc_Hi"].notna().all(), list(df.columns))
    hot = df["Temperature"] != "Room"
    check("TR-UNC[%s]: loaded Unc_Hi is 2.0 at Room and 1.0 hot; Spec_Hi == Upper_Limit + Unc_Hi on every row" % tag,
          set(df.loc[~hot, "Unc_Hi"]) == {2.0} and set(df.loc[hot, "Unc_Hi"]) == {1.0} and np.allclose(df["Spec_Hi"], df["Upper_Limit"] + df["Unc_Hi"], atol=1e-12))
    check("TR-UNC[%s]: loaded Spec_Lo is blank where there is no lower limit" % tag, np.array_equal(df["Spec_Lo"].isna().to_numpy(), df["Lower_Limit"].isna().to_numpy()))
    job = json.loads(Path(B["job"]).read_text())
    check("TR-UNC[%s]: the job says the limit already includes the stored uncertainty, split unknown (uncertainty_includes = unknown)" % tag,
          job.get("uncertainty_includes") == "unknown", job.get("uncertainty_includes"))


def check_unc_compat(work):
    """A padbloader file WITHOUT spec / uncertainty columns converts exactly as before: no extra columns, no uncertainty_includes."""
    import padb_loader_adapter as A
    d = work / "nounc"
    d.mkdir()
    pqp = d / "t.parquet"
    write_parquet(pqp, 1.0, "MHz")
    import pyarrow.parquet as pq
    tbl = pq.read_table(str(pqp))
    keep = [n for n in tbl.schema.names if n not in ("cuspec_upper", "cuspec_lower", "uncert_upper", "uncert_lower")]
    pq.write_table(tbl.select(keep).replace_schema_metadata(tbl.schema.metadata), str(pqp))
    made = A.convert(str(pqp), str(d / "o"), str(d / "j"), publish_to="", log=lambda *a, **k: None)[0]
    o = pd.read_parquet(made["data_path"])
    job = json.loads(Path(made["job_path"]).read_text())
    check("TR-UNC: a file with no spec / uncertainty columns converts as before -- no Spec / Uncertainty columns and no uncertainty_includes in the job",
          not any(("Spec" in c or "Uncertainty" in c) for c in o.columns) and "uncertainty_includes" not in job and made["limits"] is True, (list(o.columns), job.get("uncertainty_includes")))
    import padb_v2 as V
    cfg = json.loads(Path(made["job_path"]).read_text())
    dfl = V.load_scatter(Path(made["data_path"]), cfg)
    check("TR-UNC: ... and the loader leaves Spec / Uncertainty empty for it", dfl["Spec_Hi"].isna().all() and dfl["Unc_Hi"].isna().all())
    # spec + uncertainty but NO test limit: that is not "has limits" (a limit is the test's pass criterion; spec and uncertainty are not)
    d2 = work / "nolimit"
    d2.mkdir()
    p2 = d2 / "t.parquet"
    write_parquet(p2, 1.0, "MHz")
    tb = pq.read_table(str(p2))
    pq.write_table(tb.select([n for n in tb.schema.names if n not in ("limit_upper", "limit_lower")]).replace_schema_metadata(tb.schema.metadata), str(p2))
    m2 = A.convert(str(p2), str(d2 / "o"), str(d2 / "j"), publish_to="", log=lambda *a, **k: None)[0]
    j2 = json.loads(Path(m2["job_path"]).read_text())
    o2 = pd.read_parquet(m2["data_path"])
    check("TR-UNC: spec + uncertainty WITHOUT a test limit are carried, but the file does not count as having limits (limits False)",
          m2["limits"] is False and "Upper Spec" in o2.columns and "Upper Limit" not in o2.columns and j2.get("uncertainty_includes") == "unknown", (m2["limits"], list(o2.columns)))


_INCL_JS = r"""
(arg) => {
  const k = arg[0], specOv = arg[1];
  const fs = STAT_DATA[0].freq_stats[k];
  const out = {};
  ['nothing', 'mu', 'mu_denv', 'unknown'].forEach(m => {
    const p = getParams();
    p.mu = 0.5; p.gb = 0.2; p.drift = 0; p.denv_up_override = 0.3; p.denv_lo_override = 0.3; p.incl = m;
    if (specOv !== null) p.spec_hi_override = specOv;
    const r = computeFreqResult(fs, p);
    out[m] = {tll_up: r.tll_up, g_up: r.g_up, spec_up: r.spec_up, ti_up: r.ti_up, pass_up: r.pass_up, ssu_up: r.ssu_up};
  });
  return out;
}
"""


def check_unc_browser(B, br):
    """TR-UNC browser half: labels on the scatter, and the Statistical Summary's 'Limit already includes' in all four modes."""
    tag = B["unit"]
    sc = br.new_page()
    errs = []
    sc.on("pageerror", lambda e: errs.append(str(e)))
    sc.goto([p for p in B["res"].glob("*_scatter.html")][0].as_uri())
    sc.wait_for_function("() => document.getElementById('plot') && document.getElementById('plot').data")
    bounds = sc.evaluate("() => _scatterBounds()")
    labels = sc.evaluate("() => HOVER_COLS.map(c => c[1])")
    check("TR-UNC[%s]: the scatter knows it has spec, limit AND uncertainty" % tag, bounds == {"spec": True, "limit": True, "unc": True}, bounds)
    check("TR-UNC[%s]: the hover names the uncertainty honestly -- 'Uncertainty Hi/Lo (M.U.+DEnv+GB as stored)' -- beside Spec and Limit" % tag,
          UNC_LABEL in labels and "Uncertainty Lo (M.U.+DEnv+GB as stored)" in labels and "Spec Hi" in labels and "Limit Hi" in labels, labels)
    check("TR-UNC[%s]: the scatter CSV / table code names the stored uncertainty too" % tag,
          sc.evaluate("() => typeof saveCSV === 'function' && saveCSV.toString().indexOf('M.U.+DEnv+GB as stored') > 0 && updateScatterTable.toString().indexOf('M.U.+DEnv+GB as stored') > 0"))
    sc.close()
    pg = br.new_page()
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto([p for p in B["res"].glob("*_stat_summary.html")][0].as_uri())
    pg.wait_for_function("() => typeof STAT_DATA !== 'undefined' && document.getElementById('stat_incl')")
    pg.wait_for_timeout(600)
    sel = pg.evaluate("() => document.getElementById('stat_incl').value")
    check("TR-UNC[%s]: the Statistical Summary opens on 'stored uncertainty (split unknown)' (the job's setting)" % tag, sel == "unknown", sel)
    st = pg.evaluate("() => ({mu: document.getElementById('stat_mu').disabled, gb: document.getElementById('stat_gb').disabled, du: document.getElementById('stat_denv_up').disabled, dl: document.getElementById('stat_denv_lo').disabled, note: document.getElementById('stat_incl_note').textContent})")
    check("TR-UNC[%s]: ... M.U., guard band and DEnv boxes are greyed out (not applied) and the note says why" % tag,
          st["mu"] and st["gb"] and st["du"] and st["dl"] and "already has the stored uncertainty" in st["note"] and "none is subtracted again" in st["note"], st)
    ks = [k for k in range(len(XS))]
    bad = []
    for k in ks:
        r = pg.evaluate(_INCL_JS, [k, None])
        su = r["nothing"]["spec_up"]
        exp = {"nothing": su - (0.5 + 0.3 + 0.2), "mu": su - (0.3 + 0.2), "mu_denv": su, "unknown": su}
        for m, e in exp.items():
            if abs(r[m]["tll_up"] - e) > 1e-9 or abs(r[m]["g_up"] - (su - e)) > 1e-9:
                bad.append((k, m, r[m]["tll_up"], e))
            if r[m]["pass_up"] != (r[m]["ti_up"] <= r[m]["tll_up"]):
                bad.append((k, m, "verdict"))
        # the limit already has the uncertainty off: in the included modes the TLL is the LIMIT itself
        if abs(r["unknown"]["tll_up"] - su) > 1e-9:
            bad.append((k, "unknown tll != limit"))
    check("TR-UNC[%s]: TLL arithmetic at all 15 frequencies -- nothing: limit - (M.U. 0.5 + DEnv 0.3 + GB 0.2); mu: limit - (DEnv + GB); mu_denv and unknown: the limit itself; the verdict follows the TLL" % tag, not bad, bad[:3])
    # teeth: with the page's own Spec override at -47 the data (TI up -47.3 at the last frequency) passes if nothing more is subtracted
    # and fails if M.U. + DEnv + GB (1.0) are taken off AGAIN -- the setting changes a verdict
    r = pg.evaluate(_INCL_JS, [14, -47.0])
    check("TR-UNC[%s]: ... and the setting changes a verdict (spec -47, TI up %.2f: pass when the limit already includes the budget, FAIL if it is subtracted again)"
          % (tag, r["unknown"]["ti_up"]), r["unknown"]["pass_up"] is True and r["mu_denv"]["pass_up"] is True and r["nothing"]["pass_up"] is False, r)
    pg.select_option("#stat_incl", "nothing")        # the M.U. box is greyed out while the limit includes it: enable it first
    pg.wait_for_timeout(300)
    pg.fill("#stat_mu", "0.5")
    pg.dispatch_event("#stat_mu", "input")
    tll = {}
    for mode in ("unknown", "nothing"):
        pg.select_option("#stat_incl", mode)
        pg.wait_for_timeout(500)
        tll[mode] = pg.evaluate("() => ({incl: getParams().incl, txt: document.getElementById('tll_display').textContent})")
    import re as _re
    nums = {m: [float(x) for x in _re.findall(r"-?\d+\.?\d*", tll[m]["txt"].split("TLL")[1])] for m in tll}
    check("TR-UNC[%s]: through the REAL control: with M.U. 0.5 typed, the page's own TLL range is exactly the limits (-55 .. -49) when the limit "
          "already includes it (unknown), and moves down by at least that 0.5 when it does not (nothing); getParams() reports the chosen setting" % tag,
          tll["unknown"]["incl"] == "unknown" and tll["nothing"]["incl"] == "nothing" and len(nums["unknown"]) >= 2 and len(nums["nothing"]) >= 2
          and abs(nums["unknown"][0] - (-55.0)) < 1e-6 and abs(nums["unknown"][1] - (-49.0)) < 1e-6
          and nums["nothing"][1] <= -49.0 - 0.5 + 1e-9 and nums["nothing"][0] < nums["unknown"][0], ascii((tll, nums)))
    pg.select_option("#stat_incl", "nothing")
    pg.wait_for_timeout(500)
    st2 = pg.evaluate("() => ({mu: document.getElementById('stat_mu').disabled, gb: document.getElementById('stat_gb').disabled, du: document.getElementById('stat_denv_up').disabled, note: document.getElementById('stat_incl_note').textContent})")
    check("TR-UNC[%s]: 'nothing (limit = datasheet spec)' enables every box and says M.U., DEnv and GB are subtracted" % tag,
          not st2["mu"] and not st2["gb"] and not st2["du"] and "datasheet spec" in st2["note"], st2)
    pg.select_option("#stat_incl", "mu")
    pg.wait_for_timeout(400)
    st3 = pg.evaluate("() => ({mu: document.getElementById('stat_mu').disabled, gb: document.getElementById('stat_gb').disabled, du: document.getElementById('stat_denv_up').disabled})")
    check("TR-UNC[%s]: 'M.U. only' greys out only the M.U. box" % tag, st3["mu"] and not st3["gb"] and not st3["du"], st3)
    url = pg.url
    pg.goto("about:blank")
    pg.goto(url)          # a fresh navigation: a reload lets the browser restore the form controls itself, which would hide a missing save
    pg.wait_for_function("() => typeof STAT_DATA !== 'undefined' && document.getElementById('stat_incl')")
    pg.wait_for_timeout(500)
    kept = pg.evaluate("() => document.getElementById('stat_incl').value")
    check("TR-UNC[%s]: the choice survives a reload" % tag, kept == "mu", kept)
    pg.evaluate("() => resetFilters()")
    pg.wait_for_timeout(400)
    back = pg.evaluate("() => document.getElementById('stat_incl').value")
    check("TR-UNC[%s]: Reset returns it to the job's setting (unknown)" % tag, back == "unknown", back)
    check("TR-UNC[%s]: no page errors" % tag, not errs, errs[:2])
    pg.close()


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
        check_unc(B, df)
        check_stats(B, df, cfg)
        check_viewer(B)
        check_box(B)
        check_summary(B)
    check_unc_compat(work)
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
                if not B["ov"]:
                    check_unc_browser(B, br)
        br.close()
        pw_cm.stop()
    print("\nqa_truth: %d passed, %d failed" % (PASS, FAIL))
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else (3 if SKIPPED else 0)


if __name__ == "__main__":
    sys.exit(main())
