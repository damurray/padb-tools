#!/usr/bin/env python3
"""qa_jsrules.py -- behavioral cross-view QA for the shared JS rules (2026-09-15).

The hardening passes deduplicated every view's pass/fail + fence onto ONE shared
definition (PADB_specClass / PADB_isFail / PADB_fence in _COMMON_JS, and
_spSpecClass in _SITE_PANEL_SHARED_JS). Source pins (qa_regressions) prove each
view *delegates* to those; THIS gate proves the shared rule is behaviorally
correct by executing the ACTUAL shipped JS -- so, since all views delegate, every
view agrees by construction.

Runs the real _COMMON_JS (+ shared panel) under Playwright's Chromium (Edge
headless is dead on this box) and evaluates a truth table. Honest env gate: if
Playwright/Chromium isn't available this exits 3 (ENV-UNAVAILABLE), NOT a failure.

    py qa_jsrules.py
"""
import sys
from pathlib import Path

import padb_plots

_PASS, _FAIL = [], []


def check(desc, cond, detail=""):
    (_PASS if cond else _FAIL).append(desc)
    print(f"  {'PASS' if cond else 'FAIL'}  {desc}" + ("" if cond or not detail else f" -- {detail}"))


# The battery is defined in JS and run in-page; each entry returns [label, ok].
_BATTERY = r"""
() => {
  var out=[];
  function eq(a,b){ return (a===b) || (a==null&&b==null); }
  function near(a,b){ return a!=null&&b!=null&&Math.abs(a-b)<1e-9; }
  // ---- PADB_num ----
  out.push(['num: blank->null', PADB_num('')===null && PADB_num(null)===null && PADB_num(undefined)===null]);
  out.push(['num: numeric string', PADB_num('12.5')===12.5]);
  // ---- PADB_specClass: side-aware ----
  var c;
  c=PADB_specClass(10,5,null); out.push(['spec upper-only over -> FAIL high', c.verdict==='OUTSIDE'&&c.dir==='high'&&near(c.dist,5)]);
  c=PADB_specClass(3,5,null);  out.push(['spec upper-only under -> inside', c.verdict==='inside'&&c.dir===null]);
  c=PADB_specClass(-10,null,-5); out.push(['spec lower-only under -> FAIL low', c.verdict==='OUTSIDE'&&c.dir==='low'&&near(c.dist,5)]);
  c=PADB_specClass(-3,null,-5); out.push(['spec lower-only above -> inside', c.verdict==='inside']);
  c=PADB_specClass(0,5,-5);    out.push(['spec two-sided in -> inside', c.verdict==='inside']);
  c=PADB_specClass(9,5,-5);    out.push(['spec two-sided over -> FAIL high', c.dir==='high']);
  c=PADB_specClass(10,null,null); out.push(['spec no-limit -> n/a', c.verdict==='n/a'&&c.dir===null]);
  c=PADB_specClass('',5,null); out.push(['spec no-value -> n/a', c.verdict==='n/a']);
  // exactly-on-bound is NOT a fail (strict >/<)
  c=PADB_specClass(5,5,null);  out.push(['spec value==hi -> inside (strict)', c.verdict==='inside']);
  // string coercion of bounds
  c=PADB_specClass(10,'5',''); out.push(['spec string bound coerced', c.dir==='high']);
  // ---- PADB_isFail derived from PADB_specClass ----
  out.push(['isFail over==true', PADB_isFail(10,5,null)===true]);
  out.push(['isFail under==false', PADB_isFail(3,5,null)===false]);
  out.push(['isFail no-limit==null', PADB_isFail(10,null,null)===null]);
  // ---- PADB_fence: Tukey ----
  var f=PADB_fence([1,2,3,4,5,100],1.5);
  out.push(['fence Q1-k*IQR..Q3+k*IQR', f&&near(f.lo,-1.5)&&near(f.hi,8.5)&&f.n===6]);
  out.push(['fence <4 pts -> null', PADB_fence([1,2,3],1.5)===null]);
  var f2=PADB_fence([1,2,3,4,5,100],3.0);
  out.push(['fence k scales width (k=3 wider)', f2&&f2.hi>f.hi&&f2.lo<f.lo]);
  out.push(['fence default k=1.5', (function(){var d=PADB_fence([1,2,3,4,5,100]);return d&&near(d.hi,8.5);})()]);
  return out;
}
"""

# Cross-view agreement: the shared-panel classifier must return the SAME verdict as
# the single PADB_specClass for the resolved hi/lo -- i.e. it genuinely delegates.
_AGREE = r"""
() => {
  var out=[], HI=5, LO=-5;   // stub page spec that _spSpecClass falls back to
  window.HI_SPEC=HI; window.LO_SPEC=LO;
  var pts=[{value:10},{value:0},{value:-10},{value:5},
           {value:8,limHi:7},{value:8,specHi:9}];  // per-point limit / raw spec resolution
  pts.forEach(function(p,i){
    var a=_spSpecClass(p);
    var hi=(p.limHi!=null?p.limHi:(p.specHi!=null?p.specHi:HI));
    var lo=(p.limLo!=null?p.limLo:(p.specLo!=null?p.specLo:LO));
    var b=PADB_specClass(p.value,hi,lo);
    out.push(['_spSpecClass agrees with PADB_specClass on pt'+i,
              a.verdict===b.verdict && (a.dir===b.dir)]);
  });
  return out;
}
"""


def _chromium_exe():
    """Playwright's default may resolve to a headless-shell revision that isn't
    installed; fall back to the full chromium build under ms-playwright."""
    import glob
    home = Path.home()
    hits = glob.glob(str(home / "AppData/Local/ms-playwright/chromium-*/chrome-win64/chrome.exe"))
    return hits[0] if hits else None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    common = padb_plots._COMMON_JS
    site = padb_plots._SITE_PANEL_SHARED_JS
    print("qa_jsrules -- behavioral test of the shared JS pass/fail + fence rules (Playwright)")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"[ENV-UNAVAILABLE] Playwright not importable ({str(exc)[:120]}) -- behavioral JS checks did NOT run.")
        sys.exit(3)
    exe = _chromium_exe()
    try:
        with sync_playwright() as p:
            launch_kw = {"args": ["--no-sandbox", "--disable-gpu"]}
            try:
                browser = p.chromium.launch(**launch_kw)
            except Exception:
                if not exe:
                    raise
                browser = p.chromium.launch(executable_path=exe, **launch_kw)
            page = browser.new_page()
            page.set_content(f"<!doctype html><html><body><script>{common}\n{site}</script></body></html>")
            results = page.evaluate(_BATTERY) + page.evaluate(_AGREE)
            browser.close()
    except Exception as exc:
        print(f"[ENV-UNAVAILABLE] Chromium launch/eval failed ({str(exc)[:120]}) -- behavioral JS checks did NOT run.")
        sys.exit(3)
    for label, ok in results:
        check(label, bool(ok))
    print(f"\n{'=' * 60}\n  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
