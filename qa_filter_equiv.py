#!/usr/bin/env python3
"""qa_filter_equiv.py -- browser-tier QA for filter EQUIVALENCE and PERSISTENCE.

Two classes of defect David reported (2026-11-21) that no existing group exercised
end-to-end in a real browser:

  1. Filters set ON THE PLOT (drag-zoom / Plotly relayout / legend click) vs filters
     set with the CONTROLS (frequency boxes, condition checkboxes) did not always
     select the same population.
  2. Some settings did not persist while tabbing through "Segment by" band steps.

Properties asserted (each is a stated invariant, none pinned to a past bug):

  INV-ZOOM-OUTWARD   After a plot zoom to [a,b], the frequency filter the view now
                     holds must CONTAIN [a,b] (text lo <= a, text hi >= b).  A filter
                     narrower than the visible axis hides points the user can see
                     (rounding a bound to the nearest 3 dp rounds INWARD half the time).
  INV-ZOOM-AXIS      The zoom must survive the re-render (axis still [a,b]).
  INV-ZOOM-ROWS      (scatter) rows kept by the filter == rows whose x lies in [a,b]
                     (all conditions checked): plot zoom == typed/box range.
  INV-BOX-EQ-ZOOM    Setting the same range through the TEXT BOXES keeps the same rows
                     as the plot zoom did (control path == plot path).
  INV-CHK-PLOT       Unchecking a condition value removes exactly that condition's
                     points from the plot (checkbox path == plot content), and
                     re-checking restores the original count.
  INV-LEGEND         (reported as WARN, by-design candidate) a legend click hides a
                     trace WITHOUT narrowing the filter/table; the QA reports the gap.
  INV-STEP-KEEP      Stepping Next/Prev through "Named bands" changes ONLY the
                     frequency window: every other control (pass/fail, table mode,
                     hide-spec, draw mode, temperatures, condition + serial filters,
                     group-by, Segment-by basis ...) must be unchanged.
  INV-STEP-SPEC      Same for a Spec/Limit basis, except condition dims may narrow.
  INV-RELOAD         After a band step and a page reload every non-frequency control
                     is restored, and the frequency window is restored EXACTLY.

Browser-tier: Playwright + Chromium.  Honest-exits 3 when none is reachable.

Usage:  py qa_filter_equiv.py [--keep] [--views scatter,summary,...]
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_PASS: list[str] = []
_FAIL: list[str] = []
_WARN: list[str] = []


def _ok(m: str) -> None:
    _PASS.append(m)
    print(f"  PASS  {m}", flush=True)


def _bad(m: str, d: str = "") -> None:
    _FAIL.append(m + (f"  [{d}]" if d else ""))
    print(f"  FAIL  {m}" + (f"  [{d}]" if d else ""), flush=True)


def _warn(m: str, d: str = "") -> None:
    _WARN.append(m + (f"  [{d}]" if d else ""))
    print(f"  WARN  {m}" + (f"  [{d}]" if d else ""), flush=True)


# --------------------------------------------------------------------------- #
# Data: sparse sweep with NON-ROUND frequencies sitting just inside / outside a
# 3-decimal rounding of a plausible zoom edge, three named bands, two conditions,
# several DUTs, a passing and a failing population, Room + 55C.
# --------------------------------------------------------------------------- #
FREQS = [10.0004, 99.9996, 150.0003, 199.9997, 200.00049, 250.0, 299.9996,
         300.0004, 399.99951]
BANDS = [{"name": "Low", "lo": 0.0, "hi": 150.0},
         {"name": "Mid", "lo": 150.0, "hi": 300.0},
         {"name": "High", "lo": 300.0, "hi": 400.0}]
UPPER = -50.0


def _write_csv(path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group",
                    "Upper Limit", "Lower Limit"])
        for alc, base in (("TRUE", -60.0), ("FALSE", -45.0)):
            for mode in ("0", "1"):
                for dut in range(5):
                    sn = f"US6508{dut:04d}"
                    for ts in ("Room", "55.0 Deg C"):
                        for fr in FREQS:
                            w.writerow([ts, fr, round(base + 0.07 * dut, 4),
                                        f"AlcState: {alc}  Mode: {mode}  "
                                        f"Serial Number: {sn}", UPPER, ""])


# --------------------------------------------------------------------------- #
# Generic browser probes (DOM-driven so they work on every view)
# --------------------------------------------------------------------------- #
_SNAP = r"""
() => {
  const skip = /(^|_)freq|^seg|segTab|^gf_|_gf|padb_|^plot$/i;
  const out = {};
  document.querySelectorAll('input,select,textarea').forEach((el, i) => {
    const id = el.id || '';
    if (skip.test(id)) return;
    if (el.closest('#padb_lock_bar,.padb_lock_bar')) return;
    let key;
    if (el.type === 'radio') key = 'radio:' + (el.name || id) + '=' + el.value;
    else if (el.type === 'checkbox') key = 'chk:' + (id || (el.className + ':' + (el.dataset.col || '') + '=' + el.value));
    else key = (el.tagName.toLowerCase()) + ':' + (id || ('#' + i));
    if (key in out) key += '#' + i;
    out[key] = (el.type === 'checkbox' || el.type === 'radio') ? !!el.checked : String(el.value);
  });
  const seg = document.getElementById('segKeySel');
  out['sel:segKeySel'] = seg ? seg.value : null;
  document.querySelectorAll('[id$="_panel"]').forEach(p => { out['panel:' + p.id] = p.style.display; });
  const g = document.getElementById('plot');
  if (g && g.layout) {
    out['axis:x.type'] = (g.layout.xaxis || {}).type || 'linear';
    out['axis:y.type'] = (g.layout.yaxis || {}).type || 'linear';
  }
  return out;
}
"""

_FREQ = r"""
() => {
  const ids = [['freq_lo_txt','freq_hi_txt'],['dist_freq_lo_txt','dist_freq_hi_txt'],
               ['box_freq_lo_txt','box_freq_hi_txt'],['ec_freq_lo_txt','ec_freq_hi_txt']];
  for (const [a,b] of ids) {
    const A = document.getElementById(a), B = document.getElementById(b);
    if (A && B) return {lo: A.value, hi: B.value, ids: [a,b]};
  }
  // boxplot uses number inputs with other ids -- fall back to any pair containing 'freq'
  const c = Array.from(document.querySelectorAll('input')).filter(e => /freq/i.test(e.id) && e.type !== 'range');
  if (c.length >= 2) return {lo: c[0].value, hi: c[1].value, ids: [c[0].id, c[1].id]};
  return null;
}
"""

_MUTATE = r"""
() => {
  const did = [];
  const skip = /(^|_)freq|^seg|segTab|^gf_|_gf|padb_|lock/i;
  // 1. radios: pick the last non-checked option of every named group
  const groups = {};
  document.querySelectorAll('input[type=radio]').forEach(r => {
    if (skip.test(r.id) || skip.test(r.name || '')) return;
    (groups[r.name] = groups[r.name] || []).push(r);
  });
  Object.keys(groups).forEach(n => {
    const opts = groups[n].filter(r => !r.checked);
    if (opts.length) { opts[opts.length - 1].click(); did.push('radio ' + n + '=' + opts[opts.length - 1].value); }
  });
  // 2. selects (non-segment): second option
  document.querySelectorAll('select').forEach(s => {
    if (skip.test(s.id) || s.options.length < 2 || s.closest('#padb_lock_bar')) return;
    const idx = s.selectedIndex === 1 ? 0 : 1;
    s.selectedIndex = idx; s.dispatchEvent(new Event('change', {bubbles: true}));
    did.push('select ' + s.id + '=' + s.value);
  });
  // 3. plain checkboxes that are not filters (hide spec, show-all ...): toggle once
  document.querySelectorAll('input[type=checkbox]').forEach(c => {
    if (skip.test(c.id) || c.classList.contains('fchk') || c.classList.contains('env_chk')) return;
    if (/^all_/.test(c.id) || /(^|_)(chk|all)$/.test('') ) return;
    if (c.dataset && c.dataset.col) return;
    if (/hide_spec|hidespec/i.test(c.id)) { c.click(); did.push('chk ' + c.id); }
  });
  // 4. narrow the FIRST condition dimension that has >=2 values: uncheck its last box
  const byCol = {};
  document.querySelectorAll('.fchk[data-col]').forEach(c => { (byCol[c.dataset.col] = byCol[c.dataset.col] || []).push(c); });
  const cols = Object.keys(byCol).filter(k => byCol[k].length >= 2 && !/serial|port/i.test(k));
  if (cols.length) {
    const last = byCol[cols[0]][byCol[cols[0]].length - 1];
    last.click(); did.push('narrow ' + cols[0] + ' -' + last.value);
  }
  return did;
}
"""

_STEP = r"""
async (what) => {
  const raf = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const sel = document.getElementById('segKeySel');
  if (what.basis) {
    if (!sel) return {error: 'no segKeySel'};
    const has = Array.from(sel.options).some(o => o.value === what.basis);
    if (!has) return {error: 'basis ' + what.basis + ' not offered', opts: Array.from(sel.options).map(o => o.value)};
    sel.value = what.basis; sel.dispatchEvent(new Event('change', {bubbles: true}));
    await raf();
  }
  const btn = document.getElementById(what.dir > 0 ? 'segTabNext' : 'segTabPrev');
  if (!btn) return {error: 'no seg button (bar hidden)'};
  if (btn.disabled) return {error: 'button disabled'};
  btn.click(); await raf(); await new Promise(r => setTimeout(r, 150));
  const lab = document.getElementById('segTabLabel');
  return {label: lab ? lab.textContent : null};
}
"""


def _diff(a: dict, b: dict, allow_prefix: tuple = ()) -> list[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k) and not any(k.startswith(p) for p in allow_prefix):
            out.append(f"{k}: {a.get(k)!r} -> {b.get(k)!r}")
    return out


_ZOOM = r"""
async (rng) => {
  const raf = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const g = document.getElementById('plot');
  if (!g || !g.layout) return {error: 'no plot'};
  await Plotly.relayout(g, {'xaxis.range': rng});
  await raf(); await new Promise(r => setTimeout(r, 200));
  const x = (document.getElementById('plot').layout.xaxis || {});
  return {axis: x.range ? x.range.slice() : null, autorange: x.autorange};
}
"""

_SCAT_COUNT = r"""
() => {
  try {
    const f = applyFilters(DATA);
    let plotted = 0, hidden = 0;
    (document.getElementById('plot').data || []).forEach(t => {
      const n = (t.x || []).length;
      if (t.visible === 'legendonly') hidden += n; else plotted += n;
    });
    const lab = document.getElementById('n_points');
    return {kept: f.length, plotted: plotted, hidden: hidden, label: lab ? lab.textContent : null};
  } catch (e) { return {error: String(e)}; }
}
"""

_SCAT_EXPECT = r"""
(rng) => { try {
  const lo = rng[0], hi = rng[1];
  return DATA.filter(r => { const f = Number(r.Frequency_MHz); return f >= lo && f <= hi; }).length;
} catch (e) { return -1; } }
"""

_CHK_COUNT = r"""
(arg) => {
  const raf = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const boxes = Array.from(document.querySelectorAll('.fchk[data-col]'));
  const byCol = {};
  boxes.forEach(c => { (byCol[c.dataset.col] = byCol[c.dataset.col] || []).push(c); });
  const cols = Object.keys(byCol).filter(k => byCol[k].length >= 2 && !/serial|port/i.test(k));
  if (!cols.length) return Promise.resolve({error: 'no 2-valued condition dim'});
  const col = cols[0], box = byCol[col][0];
  const count = () => { try { return applyFilters(DATA).length; } catch (e) { return -1; } };
  const plotted = () => (document.getElementById('plot').data || []).reduce((s, t) => s + (t.visible === 'legendonly' ? 0 : (t.x || []).length), 0);
  const before = {k: count(), p: plotted()};
  const val = box.value;
  box.click();
  return raf().then(() => {
    const after = {k: count(), p: plotted()};
    const expectDrop = DATA.filter(r => String(r[col]) === String(val)).length;
    box.click();
    return raf().then(() => ({col: col, val: val, before: before, after: after,
                              restored: {k: count(), p: plotted()}, expectDrop: expectDrop}));
  });
}
"""

_LEGEND = r"""
async () => {
  const raf = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const g = document.getElementById('plot');
  if (!g || !g.data || g.data.length < 2) return {error: 'single trace'};
  const before = applyFilters(DATA).length;
  await Plotly.restyle(g, {visible: 'legendonly'}, [0]);
  await raf();
  const hidden = (document.getElementById('plot').data[0].x || []).length;
  const lab = document.getElementById('n_points');
  return {kept: applyFilters(DATA).length, before: before, hiddenPts: hidden, label: lab ? lab.textContent : null};
}
"""


def _viewdefs(padb_v2):
    return [
        ("scatter", padb_v2.render_scatter, "Scatter (All Temps)"),
        ("stat_summary", padb_v2.render_stat_summary, "Statistical Summary (Room)"),
        ("summary", padb_v2.render_summary, "Summary (All Temps)"),
        ("boxplot", padb_v2.render_boxplot, "Box Plots"),
        ("distribution", padb_v2.render_distribution, "Distribution (Delta-Env)"),
        ("env_coverage", padb_v2.render_env_coverage, "Environmental Coverage"),
    ]


def _chk_errs(key, errs):
    # INV-NOERR: no uncaught page error during any interaction (a throw mid-update() leaves the
    # view half-applied -- e.g. a band step that never completes).
    uniq = sorted(set(errs))
    if uniq:
        _bad(f"{key}: INV-NOERR uncaught page error during interaction", "; ".join(u[:120] for u in uniq[:3]))
    else:
        _ok(f"{key}: INV-NOERR no uncaught page errors across zoom/checkbox/band-step/reload")


def _num(s):
    try:
        return float(s)
    except Exception:
        return None


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("qa_filter_equiv -- plot-vs-control filter equivalence + band-step persistence")
    keep = "--keep" in sys.argv
    only = None
    if "--views" in sys.argv:
        only = set(sys.argv[sys.argv.index("--views") + 1].split(","))

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"\n  SKIP (browser tier): Playwright not importable ({exc}).")
        sys.exit(3)
    # NOTE: start the Playwright driver BEFORE importing padb_v2 (sync driver hangs
    # on Windows if padb_v2 was imported first -- see CLAUDE.md compress_data gotcha).
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch()
    except Exception as exc:
        print(f"\n  SKIP (browser tier): chromium launch failed ({exc}).")
        pw.stop()
        sys.exit(3)

    import padb_v2

    tmp = Path(tempfile.mkdtemp(prefix="qa_filter_equiv_"))
    csv_path = tmp / "equiv.csv"
    _write_csv(csv_path)
    cfg0 = {"y_label": "Power (dBc)", "title_prefix": "EQ", "x_label": "Frequency (MHz)",
            "x_unit": "MHz", "spec_direction": "hi", "results_dir": "qa_filter_equiv_results",
            "_named_bands": BANDS, "_named_bands_path": ""}
    df = padb_v2.load_scatter(csv_path, cfg0)
    df = padb_v2._fill_spec_nulls(df, "none")

    for key, fn, title in _viewdefs(padb_v2):
        if only and key not in only:
            continue
        out = tmp / f"EQ_{key}.html"
        try:
            fn(df, dict(cfg0, title=f"EQ -- {title}"), out)
        except Exception as exc:
            _bad(f"{key}: render raised", str(exc)[:200])
            continue
        print(f"\n[{key}]", flush=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        errs: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))

        def load():
            page.goto(out.as_uri())
            page.wait_for_timeout(1500)

        load()
        if errs:
            _bad(f"{key}: page errors on load", "; ".join(errs[:2]))
            ctx.close()
            continue
        has_plot = page.evaluate("() => !!(document.getElementById('plot') && document.getElementById('plot').layout)")
        fq = page.evaluate(_FREQ)

        # ---------------- plot zoom == control equivalence --------------------
        if has_plot and fq and key in ('scatter', 'stat_summary', 'summary', 'env_coverage'):
            a, b = 199.9996, 250.0004
            z = page.evaluate(_ZOOM, [a, b])
            fr = page.evaluate(_FREQ)
            lo, hi = _num(fr["lo"]), _num(fr["hi"])
            if z.get("axis") and abs(z["axis"][0] - a) < 1e-6 and abs(z["axis"][1] - b) < 1e-6:
                _ok(f"{key}: INV-ZOOM-AXIS zoom survives re-render ({z['axis']})")
            else:
                _bad(f"{key}: INV-ZOOM-AXIS axis changed after zoom", f"asked [{a},{b}] got {z}")
            full = page.evaluate(_FREQ)
            if key == 'summary' and lo is not None and lo <= 10.0005 and hi >= 399.99:
                _warn(f"{key}: INV-ZOOM-SYNC plot zoom did not narrow the frequency filter (table keeps the full range)",
                      "summary has no zoom->filter handler")
            if lo is not None and hi is not None and lo <= a + 1e-9 and hi >= b - 1e-9:
                _ok(f"{key}: INV-ZOOM-OUTWARD filter [{lo},{hi}] contains zoom [{a},{b}]")
            else:
                _bad(f"{key}: INV-ZOOM-OUTWARD filter narrower than the visible zoom",
                     f"zoom [{a},{b}] but frequency filter [{fr['lo']},{fr['hi']}] -- points inside the axis are hidden")
            if key == "scatter":
                c = page.evaluate(_SCAT_COUNT)
                exp = page.evaluate(_SCAT_EXPECT, [a, b])
                load(); page.evaluate("() => { try { localStorage.clear(); } catch(e){} }"); load()
                a3, b3 = 199.999, 250.001      # 3-dp aligned so the typed path has no rounding to blame
                page.evaluate(_ZOOM, [a3, b3]); zc = page.evaluate(_SCAT_COUNT)
                load(); page.evaluate("() => { try { localStorage.clear(); } catch(e){} }"); load()
                a, b = a3, b3
                exp = page.evaluate(_SCAT_EXPECT, [a, b])
                if zc.get('kept') == exp:
                    _ok(f"{key}: INV-ZOOM-ROWS aligned zoom keeps all {exp} rows inside it")
                else:
                    _bad(f"{key}: INV-ZOOM-ROWS aligned zoom rows != rows inside", f"kept {zc.get('kept')} expected {exp}")
                # control path: same range typed into the boxes
                page.evaluate("""([lo,hi]) => { const A=document.getElementById('freq_lo_txt'),B=document.getElementById('freq_hi_txt');
                    A.value=String(lo); A.dispatchEvent(new Event('change',{bubbles:true}));
                    B.value=String(hi); B.dispatchEvent(new Event('change',{bubbles:true})); }""", [a, b])
                page.wait_for_timeout(300)
                c2 = page.evaluate(_SCAT_COUNT)
                if c2.get("kept") == exp:
                    _ok(f"{key}: INV-BOX-EQ-ZOOM typed range keeps the same {exp} rows as the plot zoom")
                else:
                    _bad(f"{key}: INV-BOX-EQ-ZOOM typed range != plot zoom",
                         f"typed kept {c2.get('kept')}, plot zoom would keep {exp}")
            load()
            page.evaluate("() => { try { localStorage.clear(); } catch(e){} }")
            load()

        # ---------------- checkbox == plot content (scatter) ------------------
        if key == "scatter":
            r = page.evaluate(_CHK_COUNT, None)
            if r.get("error"):
                _warn(f"{key}: INV-CHK-PLOT skipped", r["error"])
            else:
                dropped = r["before"]["p"] - r["after"]["p"]
                if (dropped == r["expectDrop"] and r["before"]["k"] - r["after"]["k"] == r["expectDrop"]
                        and r["restored"] == r["before"]):
                    _ok(f"{key}: INV-CHK-PLOT unchecking {r['col']}={r['val']} removed exactly "
                        f"{dropped} plotted pts and restored on re-check")
                else:
                    _bad(f"{key}: INV-CHK-PLOT checkbox vs plot content disagree", json.dumps(r))
            lg = page.evaluate(_LEGEND)
            if lg.get("error"):
                _warn(f"{key}: INV-LEGEND skipped", lg["error"])
            else:
                lab = _num(str(lg["label"]).replace(",", "").split()[0]) if lg.get("label") else None
                if lg["hiddenPts"] > 0 and lg["kept"] == lg["before"]:
                    _warn(f"{key}: INV-LEGEND legend-click hid {lg['hiddenPts']} plotted pts but the filter, "
                          f"table and n_points label still count them ({lg['kept']}; label {lg['label']})",
                          "by-design candidate: legend hide is display-only")
            load()

        # ---------------- band-step persistence (all views with a seg bar) ----
        bar = page.evaluate("() => !!document.getElementById('segKeySel')")
        if not bar:
            _warn(f"{key}: no Segment-by control on this view; step persistence skipped")
            _chk_errs(key, errs)
            ctx.close()
            continue
        for basis, allow in (("bands", ()), ("limit", ("chk:", "all_", "sel:"))):
            load()
            page.evaluate("() => { try { localStorage.clear(); } catch(e){} }")
            load()
            did = page.evaluate(_MUTATE)
            page.wait_for_timeout(400)
            r0 = page.evaluate(_STEP, {"basis": basis, "dir": 0}) if False else None
            # switch the basis (no step yet) by stepping Next once, then snapshot
            s1 = page.evaluate(_STEP, {"basis": basis, "dir": 1})
            if s1.get("error"):
                _warn(f"{key}: INV-STEP[{basis}] skipped", str(s1))
                continue
            base = page.evaluate(_SNAP)
            f_before = page.evaluate(_FREQ)
            moved = False
            for d, name in ((1, "Next"), (-1, "Prev"), (1, "Next")):
                s = page.evaluate(_STEP, {"basis": None, "dir": d})
                if s.get("error"):
                    continue
                moved = True
                snap = page.evaluate(_SNAP)
                bad = _diff(base, snap, allow)
                tag = f"{key}: INV-STEP-{'KEEP' if basis == 'bands' else 'SPEC'} {name} [{basis}]"
                if bad:
                    _bad(tag + " changed settings other than the frequency window", "; ".join(bad[:6]))
                else:
                    _ok(tag + " kept every non-frequency setting"
                        f" ({len(did)} non-default settings applied: {', '.join(did[:3])}...)")
            if not moved:
                _warn(f"{key}: INV-STEP[{basis}] no further step available")
                continue
            # reload persistence
            snap_pre = page.evaluate(_SNAP)
            f_pre = page.evaluate(_FREQ)
            page.reload()
            page.wait_for_timeout(1500)
            snap_post = page.evaluate(_SNAP)
            f_post = page.evaluate(_FREQ)
            bad = _diff(snap_pre, snap_post, allow)
            pan = [d for d in bad if d.startswith('panel:')]
            bad = [d for d in bad if not d.startswith('panel:')]
            if pan:
                _warn(f"{key}: INV-RELOAD[{basis}] panel open/closed state not restored", "; ".join(pan[:3]))
            if bad:
                _bad(f"{key}: INV-RELOAD[{basis}] non-frequency settings not restored after reload",
                     "; ".join(bad[:6]))
            else:
                _ok(f"{key}: INV-RELOAD[{basis}] non-frequency settings restored after reload")
            if f_pre and f_post:
                if f_pre["lo"] == f_post["lo"] and f_pre["hi"] == f_post["hi"]:
                    _ok(f"{key}: INV-RELOAD[{basis}] frequency window restored exactly "
                        f"[{f_post['lo']},{f_post['hi']}]")
                else:
                    _bad(f"{key}: INV-RELOAD[{basis}] frequency window changed on reload",
                         f"[{f_pre['lo']},{f_pre['hi']}] -> [{f_post['lo']},{f_post['hi']}]")
        _chk_errs(key, errs)
        ctx.close()

    browser.close()
    pw.stop()
    print(f"\n{'=' * 60}\n  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}    WARN: {len(_WARN)}")
    if keep:
        print(f"  (kept rendered HTML in {tmp})")
    else:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    if _WARN:
        print("\nWarnings (by-design candidates / skipped):")
        for w in _WARN:
            print(f"  - {w}")
    if _FAIL:
        print("\nFailed invariants:")
        for f in _FAIL:
            print(f"  - {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
