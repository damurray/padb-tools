r"""qa_limits_views.py -- limit overrides in the AGGREGATE views: in-page import == build-time override.

The strongest oracle for "does this view apply an imported limits file correctly" is the build itself:
`limits_file` applied at BUILD time (padb_limits.apply_df, the authoritative Python path) must give the same
page as the SAME data built plainly and then given the file IN THE PAGE. So for each view this builds both
pages from one dataset, imports the limits into the plain one, and compares a snapshot of every verdict the
view computes (the per-frequency / per-condition results, the per-DUT limit records, the page constants).

  VIEWS-<view>  snapshot(build-time page) == snapshot(plain page + in-page import)
  teeth         snapshot(plain page, no import) != snapshot(build-time page): the override really changes
                what the view computes, so the equality above is not vacuous
  bar           the shared bar says the override is APPLIED, with a count

Views are added to VIEWS one at a time as they are converted (stage 4b); a view not listed here still shows the
loud 'NOT applied in this view' notice (qa_limits_inpage proves that).

Honest-exits 3 without Playwright/Chromium.     py qa/qa_limits_views.py [--views stat_summary,...]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the repository root (this file lives in qa/)
sys.path.insert(0, str(HERE))
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
PASS = FAIL = 0

# A partial, two-sided override over the fixture's x values (10 50 100 150 200 300 350): upper -82 for x<=120,
# upper -72 AND a lower -100 for x>=150 -- covers both sides, two ranges and different verdicts.
OVERRIDE = [{"lo": 0, "hi": 120, "upper": -82}, {"lo": 150, "hi": 400, "upper": -72, "lower": -100}]

# per view: JS returning a JSON-able snapshot of every limit-dependent thing the view computes
SNAPSHOTS = {
    "stat_summary": r"""() => {
      var p=getParams();
      return {consts:{HI_SPEC:(typeof HI_SPEC!=='undefined'?HI_SPEC:null), LO_SPEC:(typeof LO_SPEC!=='undefined'?LO_SPEC:null)},
        conds: STAT_DATA.map(function(cd){ return {c:cd.condition, f:cd.freq_stats.map(function(fs){
          var r=computeFreqResult(fs,p);
          return {freq:fs.freq, spec_up:fs.spec_up, spec_lo:fs.spec_lo, result:r,
                  duts:(fs.dut_vals||[]).map(function(d){ return [d.s,d.v,d.upper_limit,d.lower_limit,d.spec_hi,d.spec_lo,d.unc_hi,d.unc_lo]; })}; })}; })}; }""",
    # summary: DATA[*] with per-frequency spec lists, scalar spec, DUT limit matrices (all-temp and per-temp), the page
    # constants, and the view's own verdict functions evaluated for every (frequency, DUT) cell
    "summary": r"""() => {
      var p=getSumParams();
      return {consts:{HI_SPEC:HI_SPEC, LO_SPEC:LO_SPEC, SPEC_DIRECTION:SPEC_DIRECTION},
        conds: DATA.map(function(cd){ return {c:cd.condition, spec_hi:cd.spec_hi, spec_lo:cd.spec_lo,
          hi_list:cd.spec_hi_list, lo_list:cd.spec_lo_list, spec:cd.dut_spec_vals, specT:cd.dut_spec_vals_by_temp,
          f:cd.freqs.map(function(x,fi){ return {x:x,
            lim:cd.dut_info.map(function(_,di){ return _sumDutLimit(cd,fi,di,null,null); }),
            pt:cd.dut_info.map(function(_,di){ return _sumPtFail(cd,fi,di,p); }),
            fa:_sumFailAt(cd,fi,p)}; })}; })}; }""",
    # scatter: every row's limit record, the page-level spec constants, and the Failing count the view computes
    "scatter": r"""() => {
      var fr=document.querySelector('input[name="scat_flt"][value="failing"]'); if(fr) fr.checked=true; update();
      var nfail=applyFilters(DATA).length;
      var al=document.querySelector('input[name="scat_flt"][value="all"]'); if(al) al.checked=true; update();
      return {consts:{HI_SPEC:HI_SPEC, LO_SPEC:LO_SPEC}, failing:nfail,
        rows: DATA.map(function(r){ return [r.Frequency_MHz,r.Value,r.Upper_Limit,r.Lower_Limit,r.Spec_Hi,r.Spec_Lo,r.Unc_Hi,r.Unc_Lo]; })}; }""",
    # boxplot: BOX_STATS (stat_summary's shape) + the raw per-point records + the view's verdict per point
    "boxplot": r"""() => {
      return {consts:{HI_SPEC:HI_SPEC, LO_SPEC:LO_SPEC, SPEC_DIRECTION:SPEC_DIRECTION},
        stats: BOX_STATS.map(function(cd){ return {c:cd.condition, f:cd.freq_stats.map(function(fs){
          return {freq:fs.freq, spec_up:fs.spec_up, spec_lo:fs.spec_lo,
                  duts:(fs.dut_vals||[]).map(function(d){ return [d.s,d.v,d.upper_limit,d.lower_limit,d.spec_hi,d.spec_lo,d.unc_hi,d.unc_lo]; })}; })}; }),
        pts: BOX_DATA.map(function(cd){ return {c:cd.condition, f:cd.freq_stats.map(function(fs){
          return {freq:fs.freq, d:(fs.vals_detail||[]).map(function(d){
            var vd; try{ vd=_boxVerdict(cd.condition,d,null); }catch(e){ vd='ERR'; }
            return [d.s,d.v,d.upper_limit,d.lower_limit,d.spec_hi,d.spec_lo,d.unc_hi,d.unc_lo,vd]; })}; })}; })}; }""",
    # reference: the row objects built from COLS (limits) + the view's own pass/fail per row in 'limits' mode
    "reference": r"""() => {
      return {consts:{HAS_LIMITS:HAS_LIMITS},
        rows: DATA.map(function(r){ return [r.Frequency_MHz,r.Value,r.Upper_Limit,r.Lower_Limit,_pf(r,'limits')]; })}; }""",
}
READY = {"reference": "#f_lo"}


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail))[:600] if detail else "")


def build(src, out, views, limits_file=None):
    cmd = [sys.executable, str(HERE / "padb_analyze.py"), "--input", str(src), "--out", str(out), "--views", ",".join(views)]
    if limits_file:
        cmd += ["--limits-file", str(limits_file)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    res = json.loads(r.stdout)
    assert res["ok"], res
    return {Path(f).stem.split("fixture_")[-1]: Path(f) for f in res["analyses"][0]["files"] if f.endswith(".html")}


def const(path, name):
    """A page's embedded `var NAME = <json>;` constant."""
    import re
    h = path.read_text(encoding="utf-8")
    m = re.search(r"^var %s\s*=\s*" % name, h, re.M)
    return json.loads(h[m.end():h.index(";\n", m.end())])


def groupspec_checks(tmp):
    """BUILD-GROUPSPEC: when the spec travels IN THE GROUP TEXT (PADB's Upper Spec / Upper Uncertainty grouping
    items -- the usual case), a limits_file override must still win in EVERY view. The scatter and boxplot views
    reload their data from a temporary CSV that carries only Upper/Lower Limit, so the override used to be lost
    on Spec_Hi / Unc_Hi there (found 2026-11-23 by diffing a build-time-override page against a plain one)."""
    import padb_limits as PL
    import qa_viewer_ui as fx
    df = fx.make_fixture(tmp)
    df["Group"] = df["Group"] + "  Upper Spec (<=): -76.0  Upper Uncertainty (<=): 2.0"
    src = tmp / "specgroup.parquet"
    df.to_parquet(src)
    lf = tmp / "limits_gs.json"
    PL.save_file(lf, PL.validate([{"lo": 0, "hi": 400, "upper": -90}])[0], "MHz")
    vs = ["scatter", "boxplot", "stat_summary", "summary"]

    def b(out, limits=None):
        cmd = [sys.executable, str(HERE / "padb_analyze.py"), "--input", str(src), "--out", str(out), "--views", ",".join(vs)]
        if limits:
            cmd += ["--limits-file", str(limits)]
        res = json.loads(subprocess.run(cmd, capture_output=True, text=True, timeout=1800).stdout)
        return {Path(f).name.split("specgroup_")[-1][:-5]: Path(f) for f in res["analyses"][0]["files"] if f.endswith(".html")}

    A, B = b(tmp / "gA", lf), b(tmp / "gB")
    chk = {
        "scatter": lambda P: (const(P["scatter"], "DATA")[0]["Spec_Hi"], const(P["scatter"], "DATA")[0]["Unc_Hi"]),
        "boxplot": lambda P: (const(P["boxplot"], "BOX_DATA")[0]["freq_stats"][0]["vals_detail"][0]["spec_hi"],
                              const(P["boxplot"], "BOX_DATA")[0]["freq_stats"][0]["vals_detail"][0]["unc_hi"]),
        "stat_summary": lambda P: (const(P["stat_summary"], "STAT_DATA")[0]["freq_stats"][0]["dut_vals"][0]["spec_hi"],
                                   const(P["stat_summary"], "STAT_DATA")[0]["freq_stats"][0]["dut_vals"][0]["unc_hi"]),
        "summary": lambda P: (const(P["summary"], "DATA")[0]["dut_spec_vals"]["spec_hi"][0][0],
                              const(P["summary"], "DATA")[0]["dut_spec_vals"]["unc_hi"][0][0]),
    }
    for v, fn in chk.items():
        with_ov, plain = fn(A), fn(B)
        check("BUILD-GROUPSPEC[%s]: the data carries Spec -76 / uncertainty 2 in its Group text; the override (-90) wins at build time"
              % v, with_ov == (-90.0, 0.0), with_ov)
        check("BUILD-GROUPSPEC[%s] teeth: without the override the page keeps the data's own spec (-76, 2)" % v,
              plain == (-76.0, 2.0), plain)


def main() -> int:
    only = None
    if "--views" in sys.argv:
        only = sys.argv[sys.argv.index("--views") + 1].split(",")
    views = [v for v in SNAPSHOTS if not only or v in only]
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        print("qa_limits_views: no Playwright/Chromium (%s) -- ENV-UNAVAILABLE, skipped" % exc)
        return 3
    import padb_limits as PL
    import qa_viewer_ui as fx

    tmp = Path(tempfile.mkdtemp(prefix="qa_lv_"))
    fx.make_fixture(tmp)
    lf = tmp / "limits.json"
    lims = PL.validate(OVERRIDE)[0]
    PL.save_file(lf, lims, "MHz")
    A = build(tmp / "fixture.parquet", tmp / "A", views, lf)       # override applied at BUILD time (Python)
    B = build(tmp / "fixture.parquet", tmp / "B", views)           # plain
    try:
        groupspec_checks(tmp)
        for v in views:
            snap = SNAPSHOTS[v]
            pa = browser.new_page()
            errs = []
            pa.on("pageerror", lambda e: errs.append(str(e)))
            pa.goto(A[v].as_uri())
            pa.wait_for_selector(READY.get(v, "#plot .main-svg"), timeout=120000)
            pa.wait_for_timeout(800)
            want = pa.evaluate(snap)
            pb = browser.new_page()
            pb.on("pageerror", lambda e: errs.append(str(e)))
            pb.on("dialog", lambda d: d.dismiss())
            pb.goto(B[v].as_uri())
            pb.wait_for_selector(READY.get(v, "#plot .main-svg"), timeout=120000)
            pb.wait_for_timeout(800)
            plain = pb.evaluate(snap)
            pb.evaluate("(l) => PADB_limovSet(l)", lims)
            pb.reload()
            pb.wait_for_selector(READY.get(v, "#plot .main-svg"), timeout=120000)
            pb.wait_for_timeout(1000)
            got = pb.evaluate(snap)
            # which top-level parts differ (to make a failure readable)
            def diff(a, b, path=""):
                if type(a) != type(b):
                    return [path]
                if isinstance(a, dict):
                    out = []
                    for k in sorted(set(a) | set(b)):
                        out += diff(a.get(k), b.get(k), path + "/" + str(k))
                    return out
                if isinstance(a, list):
                    if len(a) != len(b):
                        return [path + "[len]"]
                    out = []
                    for i, (x, y) in enumerate(zip(a, b)):
                        out += diff(x, y, path + "[%d]" % i)
                    return out
                return [] if a == b else [path + ": %r != %r" % (a, b)]
            d = diff(want, got)
            check("VIEWS-%s: snapshot(build-time override) == snapshot(plain page + in-page import) -- %d verdict fields compared"
                  % (v, len(json.dumps(want))), not d, d[:6])
            dp = diff(want, plain)
            check("VIEWS-%s teeth: the override really changes what the view computes (plain != build-time)" % v, bool(dp), "identical!")
            bar = pb.inner_text("#padb_lock_bar")
            check("VIEWS-%s bar: the shared bar says the override is APPLIED (%s)" % (v, "2 ranges"),
                  "applied: 2 ranges" in bar and "NOT applied" not in bar, bar)
            check("VIEWS-%s: no uncaught page errors" % v, not errs, errs[:3])
    finally:
        browser.close()
        pw.stop()
    print("\nqa_limits_views: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
