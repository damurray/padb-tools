#!/usr/bin/env python3
"""qa_crossview.py -- cross-view INVARIANT harness (proactive, adversarial data).

Unlike qa_regressions (which pins *specific known fixes* -- reactive), this harness
encodes *properties every view must satisfy* and feeds them adversarial data shaped
to trip the silent-wrong-data bug class. It generates a synthetic cross-site compare
with the two shapes that produced real bugs on 2026-09-22:

  (a) a Room-only NON-primary site (AMC, MY-serials) alongside a multi-temp primary
      site (SR)                    -> the distribution "erased a Room-only site" bug;
  (b) a condition dimension (Test Event Status) recorded by one site and left NULL by
      the other                    -> the scatter "blank dim drops a whole site" bug.

It renders EVERY view the real pipeline builds and asserts cross-view invariants:

  INV-SITE : with both sites selected (the default), every applicable view's
             rendered/plottable data includes BOTH sites. env_coverage is EXEMPT --
             it is delta-vs-Room only, and a Room-only site legitimately has no delta.
  INV-PART : for the pass/fail-filter views (summary, stat_summary), Passing and
             Failing PARTITION All (disjoint AND union == All).

This catches a *class* of divergence (a site or population silently dropped in one
view) without anyone having pinned the specific bug first -- the gap qa_regressions
structurally cannot cover. It cannot beat the oracle problem (an invariant nobody
stated), and it only proves the properties on the generated data shapes.

Browser-tier: needs Playwright + a Chromium. Honest-exits 3 when none is reachable
(environment, not a gap), matching the other browser-dependent QA groups.

Usage:  py qa_crossview.py            (generate data, render, verify)
        py qa_crossview.py --keep     (leave the rendered HTML for inspection)
"""
from __future__ import annotations

import csv
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_PASS: list[str] = []
_FAIL: list[str] = []
_INFO: list[str] = []


def _ok(msg: str) -> None:
    _PASS.append(msg)
    print(f"  PASS  {msg}", flush=True)


def _bad(msg: str, detail: str = "") -> None:
    _FAIL.append(msg + (f"  [{detail}]" if detail else ""))
    print(f"  FAIL  {msg}" + (f"  [{detail}]" if detail else ""), flush=True)


def _note(msg: str) -> None:
    _INFO.append(msg)
    print(f"  ..    {msg}", flush=True)


# --------------------------------------------------------------------------- #
# 1. Adversarial synthetic compare data
# --------------------------------------------------------------------------- #
PRIMARY = "SR"
ONBOARD = "AMC"
SITES = {PRIMARY, ONBOARD}
UPPER_LIMIT = -50.0  # values <= -50 pass (spec_direction hi); > -50 fail


def _write_adversarial_csv(path: Path) -> None:
    """SR: multi-temp (Room + 55C), NO Test Event Status (null).
       AMC: Room-only, WITH Test Event Status (P/F) -- the asymmetric dim.
       AlcState TRUE conditions pass the -50 spec (~-60), FALSE fail (~-45),
       so both sites carry a passing AND a failing population."""
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group",
                    "Upper Limit", "Lower Limit"])
        # SR -- multi-temp, no Test Event Status key in Group
        for alc, base in (("TRUE", -60.0), ("FALSE", -45.0)):
            for dut in range(6):
                sn = f"US6508{dut:04d}"
                for ts in ("Room", "55.0 Deg C"):
                    for fr in (100.0, 200.0, 300.0):
                        w.writerow([ts, fr, round(base + 0.05 * dut, 4),
                                    f"AlcState: {alc}  Mode: 0  "
                                    f"Serial Number: {sn}  Site: {PRIMARY}",
                                    UPPER_LIMIT, ""])
        # AMC -- Room only, WITH Test Event Status (P for pass conds, F for fail)
        for alc, base, pf in (("TRUE", -60.0, "P"), ("FALSE", -45.0, "F")):
            for dut in range(5):
                sn = f"MY6625{dut:04d}"
                for fr in (100.0, 200.0, 300.0):
                    w.writerow(["Room", fr, round(base + 0.05 * dut, 4),
                                f"AlcState: {alc}  Mode: 0  "
                                f"Serial Number: {sn}  Site: {ONBOARD}  "
                                f"Test Event Status: {pf}",
                                UPPER_LIMIT, ""])


# --------------------------------------------------------------------------- #
# 2. Per-view JS probes -- return the set of sites actually in the plottable
#    data (what the user would see), plus partition inputs where relevant.
# --------------------------------------------------------------------------- #
_SITE_RE = r"Site:\\s*([^\\s|]+)"

# scatter / reference: both embed DATA + applyFilters()
_PROBE_APPLYFILTERS = """
() => { try {
  var f = applyFilters(DATA);
  var s = new Set();
  f.forEach(function(r){ var v=r['_grp_Site']; if(v!=null&&v!=='') s.add(String(v)); });
  return { sites: Array.from(s), n: f.length };
} catch(e){ return { error: String(e) }; } }
"""

# distribution: RAW_ABS is the per-point Absolute source (site tagged in .g)
_PROBE_DIST = """
() => { try {
  var s = new Set();
  (typeof RAW_ABS!=='undefined'?RAW_ABS:[]).forEach(function(sp){
    sp.forEach(function(cell){
      (cell && cell.g || []).forEach(function(g){
        var m = /Site:\\s*([^\\s|]+)/.exec(g); if(m) s.add(m[1]);
      });
    });
  });
  return { sites: Array.from(s) };
} catch(e){ return { error: String(e) }; } }
"""

# boxplot: getSelectedConds() = all condition labels at default (all selected)
_PROBE_BOX = """
() => { try {
  var conds = getSelectedConds();
  var s = new Set();
  conds.forEach(function(c){ var m=/Site:\\s*([^\\s|]+)/.exec(c); if(m) s.add(m[1]); });
  return { sites: Array.from(s), nConds: conds.length };
} catch(e){ return { error: String(e) }; } }
"""

# summary: _getFilteredActive(true) per mode -> condition labels (Site is a dim)
_PROBE_SUM = """
(mode) => { try {
  var r = document.querySelector('input[name="sum_flt"][value="'+mode+'"]');
  if(r){ r.checked = true; }
  if (typeof update === 'function') update();
  var act = _getFilteredActive(true);
  var s = new Set();
  var conds = act.map(function(c){
    var m=/Site:\\s*([^\\s|]+)/.exec(c.condition||''); if(m) s.add(m[1]);
    return c.condition;
  });
  return { sites: Array.from(s), conds: conds };
} catch(e){ return { error: String(e) }; } }
"""

# stat_summary: getFilteredCondsAndParams() per mode -> (condition, freq) ids
_PROBE_STAT = """
(mode) => { try {
  var r = document.querySelector('input[name="data_flt"][value="'+mode+'"]');
  if(r){ r.checked = true; }
  if (typeof update === 'function') update();
  var res = getFilteredCondsAndParams();
  var s = new Set(); var ids = [];
  res.conds.forEach(function(c){
    var m=/Site:\\s*([^\\s|]+)/.exec(c.condition||''); if(m) s.add(m[1]);
    (c.freq_stats||[]).forEach(function(fs){ ids.push((c.condition||'')+'@'+fs.freq); });
  });
  return { sites: Array.from(s), ids: ids };
} catch(e){ return { error: String(e) }; } }
"""

# ---- INV-LOCK: read a lock on scatter, apply on another view, read back its state ----
# Narrow scatter to ONE site + Failing, save the cross-view lock, then confirm the
# other views auto-apply it (their active conditions restrict to that site + failing).
_LOCK_READ_SCATTER = r"""
() => { try {
  var sc=null; (GROUP_COLS||[]).forEach(function(p){ if(/site/i.test(p[1])) sc=p[0]; });
  var first=null;
  if(sc){ var bx=document.querySelectorAll('.fchk[data-col="'+sc+'"]');
    if(bx.length){ first=String(bx[0].value);
      bx.forEach(function(c){ c.checked=(String(c.value)===first); }); } }
  var fr=document.querySelector('input[name="scat_flt"][value="failing"]'); if(fr) fr.checked=true;
  // Also narrow the FREQUENCY to a strict sub-range so INV-LOCK exercises freq propagation
  // (the boxplot NaN-clamp bug applied dims but silently dropped freq -- David 2026-09-24).
  var fLoT=null;
  if(typeof FREQ_MIN!=='undefined'&&typeof FREQ_MAX!=='undefined'&&FREQ_MAX>FREQ_MIN){
    var span=FREQ_MAX-FREQ_MIN, lo=FREQ_MIN+span*0.25, hi=FREQ_MAX-span*0.25;
    var l=document.getElementById('freq_lo_txt'), h=document.getElementById('freq_hi_txt');
    if(l&&h){ l.value=String(lo); h.value=String(hi); fLoT={lo:lo,hi:hi}; }
  }
  if(typeof update==='function') update();
  return { lock:_avLockRead(), site:first, wantFreq:fLoT };
} catch(e){ return { error:String(e) }; } }
"""
_LOCK_APPLY_SUM = r"""
(L) => { try { var o=JSON.parse(L); PADB_lockSet(o); var rep=PADB_lockApply();
  var pf=document.querySelector('input[name="sum_flt"]:checked');
  var act=(typeof getActive==='function')?getActive():[]; var s={};
  act.forEach(function(cd){ var m=/Site:\s*([^\s|]+)/.exec(cd.condition||''); if(m) s[m[1]]=1; });
  return { rep:rep, sites:Object.keys(s), passfail:pf?pf.value:null };
} catch(e){ return { error:String(e) }; } }
"""
_LOCK_APPLY_STAT = r"""
(L) => { try { var o=JSON.parse(L); PADB_lockSet(o); var rep=PADB_lockApply();
  var pf=document.querySelector('input[name="data_flt"]:checked');
  var act=(typeof getActiveConditions==='function')?getActiveConditions():[]; var s={};
  act.forEach(function(cd){ var m=/Site:\s*([^\s|]+)/.exec(cd.condition||''); if(m) s[m[1]]=1; });
  return { rep:rep, sites:Object.keys(s), passfail:pf?pf.value:null };
} catch(e){ return { error:String(e) }; } }
"""
_LOCK_APPLY_BOX = r"""
(L) => { try { var o=JSON.parse(L); PADB_lockSet(o); var rep=PADB_lockApply();
  var pf=document.querySelector('input[name="box_flt"]:checked');
  var sel=(typeof getSelectedConds==='function')?getSelectedConds():[]; var s={};
  sel.forEach(function(c){ var m=/Site:\s*([^\s|]+)/.exec(c||''); if(m) s[m[1]]=1; });
  // boxplot freq inputs are plain number inputs with empty min/max -- the NaN-clamp bug
  // silently dropped the locked freq here. Read them back so INV-LOCK proves freq applied.
  var bl=document.getElementById('box_freq_lo'), bh=document.getElementById('box_freq_hi');
  var freq=(bl&&bh)?{lo:parseFloat(bl.value),hi:parseFloat(bh.value)}:null;
  return { rep:rep, sites:Object.keys(s), passfail:pf?pf.value:null, freq:freq };
} catch(e){ return { error:String(e) }; } }
"""
# reference/distribution/env_coverage have no pass/fail axis; verify Site propagated
# by reading which Site checkboxes remain checked after apply.
_LOCK_APPLY_REF = r"""
(L) => { try { var o=JSON.parse(L); PADB_lockSet(o); var rep=PADB_lockApply();
  var sc=null;(GROUP_COLS||[]).forEach(function(p){if(/site/i.test(p[1]))sc=p[0];});
  var chk=[]; if(sc) document.querySelectorAll('.fchk[data-col="'+sc+'"]:checked').forEach(function(c){chk.push(c.value);});
  return { rep:rep, sites:chk, passfail:null };
} catch(e){ return { error:String(e) }; } }
"""
_LOCK_APPLY_DIST = r"""
(L) => { try { var o=JSON.parse(L); PADB_lockSet(o); var rep=PADB_lockApply();
  var idx=-1;(DIST_COND_DIMS||[]).forEach(function(d,i){if(/site/i.test(d.label))idx=i;});
  var chk=[]; if(idx>=0) document.querySelectorAll('.dist_cond'+idx+'_chk:checked').forEach(function(c){chk.push(c.value);});
  return { rep:rep, sites:chk, passfail:null };
} catch(e){ return { error:String(e) }; } }
"""
_LOCK_APPLY_EC = r"""
(L) => { try { var o=JSON.parse(L); PADB_lockSet(o); var rep=PADB_lockApply();
  var cid=null;(COND_DIMS||[]).forEach(function(d){if(/site/i.test(d.label))cid=d.col_id;});
  var chk=[]; if(cid) document.querySelectorAll('.'+cid+':checked').forEach(function(c){chk.push(c.value);});
  return { rep:rep, sites:chk, passfail:null };
} catch(e){ return { error:String(e) }; } }
"""




def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("qa_crossview -- cross-view invariant harness (adversarial compare data)")

    keep = "--keep" in sys.argv

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception as exc:  # pragma: no cover - env gate
        print(f"\n  SKIP (browser tier): Playwright not importable ({exc}).")
        print("  Install once: py -m pip install playwright && py -m playwright install chromium")
        sys.exit(3)

    try:
        import padb_v2
    except Exception as exc:
        print(f"  FAIL  could not import padb_v2: {exc}")
        sys.exit(1)

    tmp = Path(tempfile.mkdtemp(prefix="qa_crossview_"))
    csv_path = tmp / "adversarial_compare.csv"
    _write_adversarial_csv(csv_path)

    base_cfg = {
        "y_label": "Power (dBc)",
        "title_prefix": "XV",
        "primary_site": PRIMARY,
        "x_label": "Frequency (MHz)",
        "x_unit": "MHz",
        "spec_direction": "hi",
        "results_dir": "qa_crossview_results",
    }

    # Load once via the real loader (promotes serial-in-Group, parses Site dim).
    try:
        df = padb_v2.load_scatter(csv_path, base_cfg)
        df = padb_v2._fill_spec_nulls(df, "none")
    except Exception as exc:
        _bad("load_scatter on adversarial compare", str(exc))
        _finish(keep, tmp)
        return

    sites_loaded = set()
    if "Group" in df.columns:
        for g in df["Group"].dropna().astype(str):
            m = re.search(r"Site:\s*([^\s|]+)", g)
            if m:
                sites_loaded.add(m.group(1))
    if sites_loaded >= SITES:
        _ok(f"adversarial data loaded with both sites present ({sorted(sites_loaded)})")
    else:
        _bad("adversarial data missing a site after load", str(sorted(sites_loaded)))

    # (view_key, render_fn, view_title, probe, kind)
    #   kind 'site'   -> INV-SITE only
    #   kind 'site+part_sum'  -> INV-SITE + INV-PART via sum_flt
    #   kind 'site+part_stat' -> INV-SITE + INV-PART via data_flt
    #   kind 'exempt' -> render only (must not crash); no site invariant
    plan = [
        ("scatter", padb_v2.render_scatter, "Scatter (All Temps)", _PROBE_APPLYFILTERS, "site"),
        ("reference", padb_v2.render_reference_stats, "Reference Statistics", _PROBE_APPLYFILTERS, "site"),
        ("boxplot", padb_v2.render_boxplot, "Box Plots", _PROBE_BOX, "site"),
        ("distribution", padb_v2.render_distribution, "Distribution (Delta-Env)", _PROBE_DIST, "site"),
        ("summary", padb_v2.render_summary, "Summary (All Temps)", _PROBE_SUM, "site+part_sum"),
        ("stat_summary", padb_v2.render_stat_summary, "Statistical Summary (Room)", _PROBE_STAT, "site+part_stat"),
        ("env_coverage", padb_v2.render_env_coverage, "Environmental Coverage", None, "exempt"),
    ]

    rendered: list[tuple] = []
    for key, fn, title, probe, kind in plan:
        out = tmp / f"XV_{key}.html"
        cfg = dict(base_cfg, title=f"XV — {title}")
        try:
            fn(df, cfg, out)
            if out.exists() and out.stat().st_size > 0:
                rendered.append((key, out, probe, kind))
            else:
                _bad(f"{key}: render produced no file")
        except Exception as exc:
            _bad(f"{key}: render raised", str(exc))

    # Browser pass
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # pragma: no cover
                print(f"\n  SKIP (browser tier): chromium launch failed ({exc}).")
                sys.exit(3)
            for key, out, probe, kind in rendered:
                page = browser.new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
                page.on("console", lambda m: errs.append(f"console: {m.text}")
                        if m.type == "error" else None)
                try:
                    page.goto(out.as_uri())
                    page.wait_for_timeout(1400)
                except Exception as exc:
                    _bad(f"{key}: page load raised", str(exc))
                    page.close()
                    continue

                if errs:
                    _bad(f"{key}: console/page errors on load", "; ".join(errs[:3]))
                else:
                    _ok(f"{key}: renders with no console/page errors")

                if kind == "exempt":
                    _note(f"{key}: exempt from INV-SITE (delta-only; a Room-only site has no delta)")
                    page.close()
                    continue

                # INV-SITE
                if kind.startswith("site+part_sum"):
                    r = page.evaluate(probe, "all")
                elif kind.startswith("site+part_stat"):
                    r = page.evaluate(probe, "all")
                else:
                    r = page.evaluate(probe)
                if not isinstance(r, dict) or r.get("error"):
                    _bad(f"{key}: INV-SITE probe error", str(r))
                    page.close()
                    continue
                got = set(r.get("sites", []))
                if got >= SITES:
                    _ok(f"INV-SITE {key}: both sites present ({sorted(got)})")
                else:
                    _bad(f"INV-SITE {key}: a site is missing from the view",
                         f"expected {sorted(SITES)}, got {sorted(got)}")

                # INV-PART (summary / stat_summary)
                if kind == "site+part_sum":
                    a = page.evaluate(probe, "all")
                    p = page.evaluate(probe, "passing")
                    fpart = page.evaluate(probe, "failing")
                    _check_partition(key, set(a.get("conds", [])),
                                     set(p.get("conds", [])), set(fpart.get("conds", [])))
                elif kind == "site+part_stat":
                    a = page.evaluate(probe, "all")
                    p = page.evaluate(probe, "passing")
                    fpart = page.evaluate(probe, "failing")
                    _check_partition(key, set(a.get("ids", [])),
                                     set(p.get("ids", [])), set(fpart.get("ids", [])))
                page.close()

            # ---- INV-LOCK: a lock saved on one view applies to the others ----
            rmap = {k: out for (k, out, _pr, _kd) in rendered}
            if all(k in rmap for k in ("scatter", "summary", "stat_summary")):
                try:
                    sp = browser.new_page()
                    sp.goto(rmap["scatter"].as_uri()); sp.wait_for_timeout(1200)
                    rd = sp.evaluate(_LOCK_READ_SCATTER)
                    sp.close()
                    if not isinstance(rd, dict) or rd.get("error") or not rd.get("site"):
                        _bad("INV-LOCK: could not read a lock on scatter", str(rd))
                    else:
                        want = rd["site"]
                        want_freq = rd.get("wantFreq")
                        L = json.dumps(rd["lock"])
                        # (key, reader, strict?) -- strict views carry both sites here;
                        # env_coverage is delta-only (a Room-only site has no delta), so a
                        # lenient check: adapter runs, never wrongly selects the other site.
                        _targets = [("summary", _LOCK_APPLY_SUM, True),
                                    ("stat_summary", _LOCK_APPLY_STAT, True)]
                        for k2, rdr in (("boxplot", _LOCK_APPLY_BOX), ("reference", _LOCK_APPLY_REF),
                                        ("distribution", _LOCK_APPLY_DIST)):
                            if k2 in rmap:
                                _targets.append((k2, rdr, True))
                        if "env_coverage" in rmap:
                            _targets.append(("env_coverage", _LOCK_APPLY_EC, False))
                        for key, reader, strict in _targets:
                            pg = browser.new_page(); pg.goto(rmap[key].as_uri()); pg.wait_for_timeout(1200)
                            st = pg.evaluate(reader, L); pg.close()
                            if not isinstance(st, dict) or st.get("error"):
                                _bad(f"INV-LOCK {key}: apply probe error", str(st)); continue
                            sites = set(st.get("sites", [])); pf = st.get("passfail")
                            rep = st.get("rep") or {}
                            applied = rep.get("applied") or []
                            pf_ok = (pf is None) or (pf == "failing")
                            if strict:
                                if sites == {want} and pf_ok and "Site" in applied:
                                    _ok(f"INV-LOCK {key}: scatter's lock (Site={want}) auto-applied")
                                else:
                                    _bad(f"INV-LOCK {key}: scatter's lock did NOT propagate",
                                         f"want Site={{{want}}}; got sites={sorted(sites)} pf={pf} applied={applied}")
                            else:
                                # lenient: must not have wrongly selected the OTHER site, and no crash
                                if not (sites - {want}):
                                    _ok(f"INV-LOCK {key}: lock applied best-effort (delta view; sites={sorted(sites)})")
                                else:
                                    _bad(f"INV-LOCK {key}: lock selected an unexpected site",
                                         f"want subset of {{{want}}}; got {sorted(sites)}")
                            # INV-LOCK-FREQ (David 2026-09-24): the locked freq sub-range must
                            # propagate too. boxplot's number inputs (empty min/max) were the
                            # NaN-clamp casualty -- assert its freq narrowed to the locked band.
                            if key == "boxplot" and want_freq and isinstance(st.get("freq"), dict):
                                gf = st["freq"]; wl, wh = want_freq["lo"], want_freq["hi"]
                                tol = max(1e-6, (wh - wl) * 0.02)
                                if (gf.get("lo") is not None and gf.get("hi") is not None
                                        and abs(gf["lo"] - wl) <= tol and abs(gf["hi"] - wh) <= tol):
                                    _ok(f"INV-LOCK {key}: locked freq range propagated "
                                        f"({gf['lo']:.4g}..{gf['hi']:.4g})")
                                else:
                                    _bad(f"INV-LOCK {key}: locked freq range did NOT propagate",
                                         f"want {wl:.4g}..{wh:.4g}; got {gf.get('lo')}..{gf.get('hi')}")
                except Exception as exc:
                    _bad("INV-LOCK: probe raised", str(exc))
            browser.close()
    except SystemExit:
        raise
    except Exception as exc:
        _bad("browser pass raised", str(exc))

    _finish(keep, tmp)


def _check_partition(key: str, A: set, P: set, F: set) -> None:
    if not A:
        _bad(f"INV-PART {key}: 'All' set empty (probe/render issue)")
        return
    disjoint = not (P & F)
    union_all = (P | F) == A
    if disjoint and union_all:
        _ok(f"INV-PART {key}: Passing+Failing partition All "
            f"(all={len(A)}, pass={len(P)}, fail={len(F)})")
    else:
        _bad(f"INV-PART {key}: Passing/Failing do NOT partition All",
             f"all={len(A)} pass={len(P)} fail={len(F)} disjoint={disjoint} union==all={union_all}")


def _finish(keep: bool, tmp: Path) -> None:
    print(f"\n{'=' * 60}\n  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}")
    if keep:
        print(f"  (kept rendered HTML in {tmp})")
    else:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    if _FAIL:
        print("\nFailed invariants:")
        for f in _FAIL:
            print(f"  - {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
