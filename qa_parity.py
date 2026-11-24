r"""qa_parity.py -- ONE definition of the numbers: the engine, the aggregators and every page's embedded payload
must agree with an independent recompute from the raw data.

v3.0 stage 4c (parity oracle). The engine-payload rewrite (pages rendering numbers the engine bakes) was
deliberately not built: the static pages recompute live from embedded per-point data, which is what lets filters,
the Global Filter, Group-by and manual Spec overrides work offline. What a rewrite would have bought -- one
definition of every number -- is proven here instead, so any drift between renderings fails the gate.

Ground truth G is computed from the raw fixture rows with plain pandas and a hand-written limit-override rule
(NOT padb_limits, NOT the engine, NOT the pages). It is compared, per frequency, with:

  engine      padb_core.Dataset (DuckDB, effective-limit view; with and without the override)
  scatter     the embedded DATA rows
  boxplot     the embedded BOX_DATA vals_detail (raw points of every condition, pooled)
  stat_summary the embedded STAT_DATA dut_vals (per-DUT means of every condition, pooled)
  summary     the embedded summary DATA dut_vals matrices (per-DUT means, pooled)

Compared per frequency: raw point count, min, max, sum, failing count (value vs the effective limit);
DUT-level: count of DUT means, their sum, failing DUT means. Run for the plain data and with a limits file
(override applied at BUILD time). Browser-free.

Teeth: (1) the override changes G's failing counts (so agreement on the override build is not vacuous);
(2) the comparer is shown to FAIL on a corrupted copy of each payload; (3) mutation of the code under test is
recorded in QA_GUIDE (engine set_limits a no-op; build-time override skipped).

    py qa_parity.py          -> exit 1 on a failure
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PASS = FAIL = 0
OVERRIDE = [{"lo": 0, "hi": 120, "upper": -82}, {"lo": 150, "hi": 400, "upper": -72, "lower": -100}]
TOL = 1e-9


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail))[:600] if detail else "")


def const(path, name):
    h = Path(path).read_text(encoding="utf-8")
    m = re.search(r"^var %s\s*=\s*" % name, h, re.M)
    return json.loads(h[m.end():h.index(";\n", m.end())])


def build(src, out, limits=None):
    cmd = [sys.executable, str(HERE / "padb_analyze.py"), "--input", str(src), "--out", str(out),
           "--views", "scatter,boxplot,stat_summary,summary"]
    if limits:
        cmd += ["--limits-file", str(limits)]
    res = json.loads(subprocess.run(cmd, capture_output=True, text=True, timeout=1800).stdout)
    assert res["ok"], res
    return {Path(f).stem.split("parityfx_")[-1]: Path(f) for f in res["analyses"][0]["files"] if f.endswith(".html")}


# --------------------------------------------------------------------------------------------- ground truth
def limit_at(x, data_upper, override):
    """Effective (upper, lower) at x: the data's upper limit unless a range covers x; later range wins per side."""
    up, lo = data_upper, None
    for r in override or []:
        if r["lo"] <= x <= r["hi"]:
            if r.get("upper") is not None:
                up = r["upper"]
            if r.get("lower") is not None:
                lo = r["lower"]
    return up, lo


def fails(v, up, lo):
    return bool((up is not None and v > up) or (lo is not None and v < lo))


def truth(df, override):
    """{freq: {raw:(n,min,max,sum,nfail), dut:(n,sum,nfail)}} from the raw rows."""
    import pandas as pd
    d = df.rename(columns={"Frequency (MHz)": "x", "SpurPower (dBc)": "y", "Upper Limit": "u"}).copy()
    out = {}
    for x, g in d.groupby("x"):
        ys = g["y"].tolist()
        lim = [limit_at(float(x), (None if pd.isna(u) else float(u)), override) for u in g["u"]]
        nf = sum(fails(v, up, lo) for v, (up, lo) in zip(ys, lim))
        raw = (len(ys), min(ys), max(ys), sum(ys), nf)
        means = g.groupby("Group")["y"].mean()                     # one DUT mean per (serial, condition)
        up0 = None if pd.isna(g["u"].iloc[0]) else float(g["u"].iloc[0])
        up, lo = limit_at(float(x), up0, override)
        dut = (len(means), float(means.sum()), sum(fails(float(m), up, lo) for m in means))
        out[float(x)] = {"raw": raw, "dut": dut}
    return out


# --------------------------------------------------------------------------------------------- renderings
def _eff(rec, hi_keys, lo_keys):
    up = next((rec[k] for k in hi_keys if rec.get(k) is not None), None)
    lo = next((rec[k] for k in lo_keys if rec.get(k) is not None), None)
    return up, lo


def from_engine(parquet, entries):
    from padb_core.engine import Dataset
    ds = Dataset(parquet)
    ds.set_limits(entries)
    rows = ds._all("SELECT x, count(*), min(y), max(y), sum(y), sum(CASE WHEN fail THEN 1 ELSE 0 END) "
                   "FROM pts GROUP BY x ORDER BY x")
    return {float(x): {"raw": (int(n), float(mn), float(mx), float(s), int(nf))} for x, n, mn, mx, s, nf in rows}


def from_scatter(page):
    acc = {}
    for r in const(page, "DATA"):
        up, lo = _eff(r, ("Spec_Hi", "Upper_Limit"), ("Spec_Lo", "Lower_Limit"))
        a = acc.setdefault(float(r["Frequency_MHz"]), [0, None, None, 0.0, 0])
        v = float(r["Value"])
        a[0] += 1
        a[1] = v if a[1] is None else min(a[1], v)
        a[2] = v if a[2] is None else max(a[2], v)
        a[3] += v
        a[4] += fails(v, up, lo)
    return {x: {"raw": tuple(a)} for x, a in acc.items()}


def from_boxplot(page):
    acc = {}
    for cd in const(page, "BOX_DATA"):
        for fs in cd["freq_stats"]:
            for d in fs["vals_detail"]:
                up, lo = _eff(d, ("spec_hi", "upper_limit"), ("spec_lo", "lower_limit"))
                a = acc.setdefault(float(fs["freq"]), [0, None, None, 0.0, 0])
                v = float(d["v"])
                a[0] += 1
                a[1] = v if a[1] is None else min(a[1], v)
                a[2] = v if a[2] is None else max(a[2], v)
                a[3] += v
                a[4] += fails(v, up, lo)
    return {x: {"raw": tuple(a)} for x, a in acc.items()}


def from_stat_summary(page):
    acc = {}
    for cd in const(page, "STAT_DATA"):
        for fs in cd["freq_stats"]:
            for d in fs["dut_vals"]:
                up, lo = _eff(d, ("spec_hi", "upper_limit"), ("spec_lo", "lower_limit"))
                a = acc.setdefault(float(fs["freq"]), [0, 0.0, 0])
                a[0] += 1
                a[1] += float(d["v"])
                a[2] += fails(float(d["v"]), up, lo)
    return {x: {"dut": tuple(a)} for x, a in acc.items()}


def from_summary(page):
    acc = {}
    for cd in const(page, "DATA"):
        sv = cd.get("dut_spec_vals") or {}
        for fi, x in enumerate(cd["freqs"]):
            for di, v in enumerate(cd["dut_vals"][fi]):
                if v is None:
                    continue

                def cell(k):
                    m = sv.get(k)
                    return m[fi][di] if m and fi < len(m) and di < len(m[fi]) else None
                up, lo = _eff({"a": cell("spec_hi"), "b": cell("upper_limit")}, ("a", "b"), ())
                lo = cell("spec_lo") if cell("spec_lo") is not None else cell("lower_limit")
                a = acc.setdefault(float(x), [0, 0.0, 0])
                a[0] += 1
                a[1] += float(v)
                a[2] += fails(float(v), up, lo)
    return {x: {"dut": tuple(a)} for x, a in acc.items()}


def same(a, b):
    return all(abs(p - q) <= TOL * max(1.0, abs(p), abs(q)) for p, q in zip(a, b)) and len(a) == len(b)


def compare(name, got, want, key):
    """-> list of readable differences of `got[key]` vs `want[key]` per frequency."""
    diffs = []
    if sorted(got) != sorted(want):
        diffs.append("frequencies differ: %s vs %s" % (sorted(got)[:4], sorted(want)[:4]))
    for x in sorted(set(got) & set(want)):
        if not same(got[x][key], want[x][key]):
            diffs.append("x=%g %s: %s != truth %s" % (x, name, tuple(round(v, 6) for v in got[x][key]),
                                                      tuple(round(v, 6) for v in want[x][key])))
    return diffs


def ragged(df):
    """Uneven repeats and gaps: every 5th row missing, one measurement repeated three more times with a shifted value
    (a DUT mean over unequal repeats, plus a failing outlier) -- what a uniform fixture cannot tell apart."""
    import pandas as pd
    r = df.drop(df.index[::5]).copy()
    extra = df.iloc[[40]].copy()
    extra = extra.loc[extra.index.repeat(3)].copy()
    extra["SpurPower (dBc)"] = extra["SpurPower (dBc)"] + 7.0
    return pd.concat([r, extra], ignore_index=True)


def main() -> int:
    import qa_viewer_ui as fx
    import padb_limits as PL

    tmp = Path(tempfile.mkdtemp(prefix="qa_par_"))
    df0 = fx.make_fixture(tmp)
    lf = tmp / "limits.json"
    entries = PL.validate(OVERRIDE)[0]
    PL.save_file(lf, entries, "MHz")
    for dname, df in (("even", df0), ("ragged", ragged(df0))):
        d_dir = tmp / dname
        d_dir.mkdir()
        src = d_dir / "parityfx.parquet"
        df.to_parquet(src)
        run_dataset(dname, df, src, d_dir, entries, lf)
    print("\nqa_parity: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


def run_dataset(dname, df, src, tmp, entries, lf):
    for label, ovr, ents, limits in (("plain", None, [], None), ("override", OVERRIDE, entries, lf)):
        G = truth(df, ovr)
        pages = build(src, tmp / label, limits)
        R = {"engine": (from_engine(src, ents), "raw"), "scatter": (from_scatter(pages["scatter"]), "raw"),
             "boxplot": (from_boxplot(pages["boxplot"]), "raw"),
             "stat_summary": (from_stat_summary(pages["stat_summary"]), "dut"),
             "summary": (from_summary(pages["summary"]), "dut")}
        for name, (got, key) in R.items():
            d = compare(name, got, G, key)
            what = ("raw count/min/max/sum/failing" if key == "raw" else "DUT-mean count/sum/failing")
            check("PARITY[%s/%s][%s]: %s per frequency == independent recompute (%d frequencies)"
                  % (dname, label, name, what, len(G)), not d, d[:4])
        if label == "plain":
            G0, R0 = G, R
        else:
            nf0 = sum(v["raw"][4] for v in G0.values())
            nf1 = sum(v["raw"][4] for v in G.values())
            check("PARITY teeth[%s]: the override changes the failing count (%d -> %d), so agreement on the override build is not vacuous"
                  % (dname, nf0, nf1), nf0 != nf1)
            # comparer teeth: a corrupted copy of every payload must be reported
            for name, (got, key) in R.items():
                bad = {x: {k: list(v) for k, v in rec.items()} for x, rec in got.items()}
                x0 = sorted(bad)[len(bad) // 2]
                bad[x0][key][-1] += 1                                   # one extra failing point / DUT
                check("PARITY teeth[%s/%s]: the comparer reports a corrupted payload" % (dname, name),
                      bool(compare(name, bad, G, key)))
            # the plain pages must NOT satisfy the override truth (a page that ignored the override would pass otherwise)
            for name, (got, key) in R0.items():
                if name in ("engine",):
                    continue
                check("PARITY teeth[%s/%s]: the plain page does not match the override truth" % (dname, name),
                      bool(compare(name, got, G, key)))


if __name__ == "__main__":
    sys.exit(main())
