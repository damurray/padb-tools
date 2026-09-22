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
