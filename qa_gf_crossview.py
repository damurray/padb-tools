r"""qa_gf_crossview.py -- cross-view Global-Filter (GF) consistency check.

WHAT THIS GUARDS (and why it matters)
-------------------------------------
The Global Filter is deliberately ONE browser-global list (localStorage key
`padb_v2_excluded_<analytic>`), shared by all six interactive views of an
analytic (scatter, stat_summary, boxplot, summary, env_coverage, distribution).
The whole promise of the GF is: exclude a DUT/point in ANY view and it is
excluded, identically, in EVERY view -- so the tables and plots across views can
never disagree about which data is in play.

That promise is a pure *self-consistency* invariant (no external oracle needed),
and it lives in the churniest code in the tool -- each view has its own GF
matcher (`_isInGfFull`, `_isStatGfExcl`, `_isEcGfExcl`, ...), and they have
diverged before (e.g. the scatter freq-key regression, and the scatter
"clear GF doesn't work" stale-`_gfParsed` bug this very check surfaced). This
harness turns "did every view honor the same GF key" into a standing, countable
gate.

IMPLICATION OF A FAILURE (what a red result means)
--------------------------------------------------
* A view that does NOT respond to the shared key  -> that view silently ignores
  the Global Filter: its plot/table still include data every other view has
  dropped. Cross-view numbers disagree; a reviewer excluding a bad DUT in the
  boxplot still sees it in the summary. (This is the class the scatter
  freq-key regression fell into.)
* A view that does not RESTORE when the key is cleared -> "Clear global filter"
  appears not to work in that view until a reload (the real stale-`_gfParsed`
  bug). Users can't get back to the full dataset.
Either way it is a correctness defect in what the user is shown, not cosmetic.

HOW IT MEASURES (deterministic, oracle-free)
--------------------------------------------
Builds the 6 synthetic views (qa_padb's known dataset), then per view, in one
page load, toggles the SAME whole-DUT GF key (exclude the lexically-first
serial, which is identical across views) off -> on -> off via the view's own GF
loader + update(), and reads a GF-SENSITIVE signal:
  * a plot fingerprint (sum+count of every plotted y/quantile), AND
  * an active-DUT/serial count (via getActiveDuts / getSelectedBoxSerials /
    embedded per-DUT data).
"Responded" = either signal changed when the DUT was excluded. Two signals
because neither alone is sensitive in every view: env_coverage's ENV bounds can
be DUT-independent on clean data (fingerprint flat) while its active-DUT count
still drops 6->4; a scatter's fingerprint moves but it has no getActiveDuts.

Asserts, across all six views: (a) every view RESPONDED to the shared key,
(b) every view RESTORED when it was cleared, (c) all views targeted the SAME
serial (the shared-key promise). Any view failing (a) or (b) is a real
cross-view GF defect.

Honest environment handling mirrors qa_filters: needs a headless browser to run
the views' JS; if none renders, it reports ENV-UNAVAILABLE (exit 3) -- never a
false pass or fail.

Usage:
  py qa_gf_crossview.py                 # build synthetic views + check
  py qa_gf_crossview.py --keep          # leave the built views on disk
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import qa_filters  # reuse the honest browser gate + render helpers
import qa_padb
import padb_v2

# Injected per-view: toggle the shared whole-DUT GF off->on->off and report a
# GF-sensitive signal. Emits JSON into #__qa_gfx.
_CROSSVIEW_JS = r"""
(function(){
  function emit(o){var p=document.getElementById('__qa_gfx')||document.createElement('pre');
    p.id='__qa_gfx';p.style.display='none';p.textContent=JSON.stringify(o);document.body.appendChild(p);}
  function gd(){return document.getElementById('plot')||document.querySelector('.js-plotly-plot');}
  function loader(){for(var i=0,ns=['_loadGlobalFilter','_loadBoxGlobalFilter','_loadStatGlobalFilter','_loadSumGlobalFilter','_loadEcGlobalFilter','_loadDistGlobalFilter'];i<ns.length;i++){if(typeof window[ns[i]]==='function'){window[ns[i]]();return ns[i];}}return null;}
  // plot fingerprint: every plotted numeric value that a GF change can move
  function fp(){var g=gd();if(!g||!g.data)return '0/0';var s=0,n=0;g.data.forEach(function(t){['y','q1','median','q3','lowerfence','upperfence'].forEach(function(k){var a=t[k];if(Array.isArray(a))a.forEach(function(v){if(typeof v==='number'&&isFinite(v)){s+=v;n++;}});});});return (Math.round(s*100)/100)+'/'+n;}
  // active-DUT/serial count: catches views whose plotted values are DUT-insensitive
  function dutCount(){
    try{ if(typeof ENV_DATA!=='undefined'&&typeof getActiveDuts==='function'){var set={};ENV_DATA.forEach(function(cd){getActiveDuts(cd).forEach(function(d){var s=(d&&d[0]!==undefined)?String(d[0]):String(d);set[s.split('_')[0]]=1;});});return Object.keys(set).length;} }catch(e){}
    try{ if(typeof getSelectedBoxSerials==='function'){/* not GF-driven; skip */} }catch(e){}
    try{ if(typeof STAT_DATA!=='undefined'){var s2={};STAT_DATA.forEach(function(cd){(cd.freq_stats||[]).forEach(function(f){(f.dut_vals||[]).forEach(function(d){if(!_ex(d.s))s2[String(d.s).split('_')[0]]=1;});});});return Object.keys(s2).length;} }catch(e){}
    return -1;
  }
  function _ex(){return false;}
  // pick lexically-first base serial from whatever per-DUT data this view has
  function firstSerial(){
    var cands=[];
    try{ if(typeof STAT_DATA!=='undefined')STAT_DATA.forEach(function(cd){(cd.freq_stats||[]).forEach(function(f){(f.dut_vals||[]).forEach(function(d){cands.push(String(d.s));});});}); }catch(e){}
    try{ if(typeof BOX_DATA!=='undefined')BOX_DATA.forEach(function(cd){(cd.freq_stats||[]).forEach(function(f){(f.vals_detail||[]).forEach(function(d){cands.push(String(d.s));});});}); }catch(e){}
    try{ if(typeof DATA!=='undefined'&&typeof _rowSerial==='function')DATA.slice(0,500).forEach(function(r){cands.push(String(_rowSerial(r)));}); }catch(e){}
    try{ if(typeof DATA!=='undefined')DATA.forEach(function(cd){((cd&&cd.dut_info)||[]).forEach(function(di){cands.push(String(di.s));});}); }catch(e){}  /* summary: per-DUT dut_info */
    try{ if(typeof RAW_ABS!=='undefined')RAW_ABS.forEach(function(col){(col||[]).forEach(function(raw){((raw&&raw.s)||[]).forEach(function(s){cands.push(String(s));});});}); }catch(e){}  /* distribution: RAW_ABS per-point serials */
    try{ if(typeof ENV_DATA!=='undefined')ENV_DATA.forEach(function(cd){Object.keys(cd.duts||{}).forEach(function(k){cands.push(k);});}); }catch(e){}
    var bases=cands.map(function(s){return s.split('_')[0];}).filter(Boolean).sort();
    return bases.length?bases[0]:null;
  }
  function sig(){return fp()+'|d'+dutCount();}
  try{
    if(typeof update==='undefined'){emit({err:'no update()'});return;}
    var view=(typeof BOX_DATA!=='undefined')?'boxplot':(typeof STAT_DATA!=='undefined')?'stat_summary':(typeof ENV_DATA!=='undefined')?'env_coverage':(typeof RAW_ABS!=='undefined')?'distribution':(typeof DATA!=='undefined'?'scatter_or_summary':'?');
    var ser=firstSerial();
    if(!ser){emit({view:view,err:'no serial found'});return;}
    // whole-DUT GF for that serial across a couple of plausible conditions
    var WD=[ser+'||HarmonicNumber=2|Port=RF1||manual||0', ser+'||HarmonicNumber=3|Port=RF1||manual||0',
            ser+'||HarmonicNumber=2|Port=RF2||manual||0', ser+'||HarmonicNumber=3|Port=RF2||manual||0'];
    // point-precise GF: one (serial, cond dims, Room, single freq box). Every view
    // must honor it at THAT freq only and must NOT silently widen it to the whole
    // DUT -- the single-datapoint case (a lone bad point is the common real defect).
    var PP=[ser+'||HarmonicNumber=2|Port=RF1||Room||200 MHz'];
    if(typeof GF_MODE_KEY!=='undefined')localStorage.setItem(GF_MODE_KEY,'exclude');
    function setk(arr){ if(arr===null)localStorage.removeItem(GF_KEY); else localStorage.setItem(GF_KEY,JSON.stringify({v:1,excluded:arr})); loader(); update(); return sig(); }
    var off=setk(null);
    var on=setk(WD); var back=setk(null);
    var ppOn=setk(PP); var ppBack=setk(null);
    emit({view:view, serial:ser, off:off, on:on, back:back, ppOn:ppOn, ppBack:ppBack,
          responded:(off!==on), restored:(off===back),
          pp_responded:(off!==ppOn), pp_restored:(off===ppBack), pp_differs_from_wd:(ppOn!==on)});
  }catch(e){emit({err:String(e)});}
})();
"""


def _build_views(tmp: Path):
    csv = tmp / "synth.csv"
    qa_padb.make_synthetic_csv(csv)
    cfg = {"title_prefix": "GFXView", "publish_to": "", "export_parquet": False}
    with redirect_stdout(io.StringIO()):
        padb_v2.generate_report(csv, cfg, tmp)
    return sorted(tmp.glob("GFXView_*.html"))


def _run_one(browser: str, page: Path) -> dict:
    src = page.read_text(encoding="utf-8", errors="ignore")
    inj = src + "\n<script>\n" + _CROSSVIEW_JS + "\n</script>\n"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        t = Path(td) / "t.html"
        t.write_text(inj, encoding="utf-8")
        dom = qa_filters._render_dom(browser, t, td, 20000, 90)
    if len(dom.strip()) < 400 or "plotly" not in dom.lower():
        return {"env_error": "browser produced no usable DOM"}
    m = re.search(r'<pre id="__qa_gfx"[^>]*>(.*?)</pre>', dom, re.DOTALL)
    if not m:
        return {"error": "no result sentinel"}
    raw = m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"bad JSON: {e}"}


def _parse_sig(s):
    """Parse a view signature 'sum/n|dDUT' into (sum, n, dut) floats/ints.
    Returns None if unparseable."""
    try:
        fp, _, dut = str(s).partition("|d")
        num, _, n = fp.partition("/")
        return (float(num), int(n), int(dut))
    except Exception:
        return None


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Cross-view Global-Filter consistency check.")
    ap.add_argument("--keep", action="store_true", help="Leave the built synthetic views on disk.")
    args = ap.parse_args(argv)

    browser, installed = qa_filters._pick_working_browser()
    if not browser:
        msg = ("no Edge/Chrome found" if not installed else
               "headless browser present but not rendering (" +
               ", ".join(Path(b).name for b in installed) + ")")
        print(f"[ENV-UNAVAILABLE] {msg} -- cross-view GF check did NOT run. "
              "Environment failure, not a product pass/fail.")
        sys.exit(3)

    tmp = Path(tempfile.mkdtemp(prefix="qa_gfx_"))
    pages = _build_views(tmp)
    print(f"qa_gf_crossview: {len(pages)} views, browser={Path(browser).name}\n")

    results, fails, envs, serials = [], [], [], set()
    for pg in pages:
        r = _run_one(browser, pg)
        vname = pg.stem.replace("GFXView_", "")
        if "env_error" in r:
            print(f"  [ENV ] {vname}: {r['env_error']}"); envs.append(vname); continue
        if "err" in r or "error" in r:
            print(f"  [FAIL] {vname}: {r.get('err') or r.get('error')}"); fails.append(vname); continue
        ok = r.get("responded") and r.get("restored")
        serials.add(r.get("serial"))
        # ---- point-precise arm: a single-datapoint key must be honored AND must
        # NOT widen to the whole DUT. Universal checks: it restores, and its effect
        # DIFFERS from the whole-DUT key (pp==wd would mean the view over-excluded a
        # single point into the whole DUT). Plus a directional check by view type:
        #   fp-sensitive views (scatter/boxplot/stat_summary/summary/distribution):
        #     the point-precise key must move the plotted values (responded) and by
        #     LESS than the whole-DUT key (narrower -- genuinely one point).
        #   fp-insensitive views (env_coverage: bands don't move on exclusion):
        #     the point-precise key must RETAIN the DUT in the active set while the
        #     whole-DUT key DROPS it -- proven via the active-DUT count.
        b, w, p = _parse_sig(r.get("off")), _parse_sig(r.get("on")), _parse_sig(r.get("ppOn"))
        pp_ok = bool(r.get("pp_restored")) and bool(r.get("pp_differs_from_wd"))
        pp_why = ""
        if not r.get("pp_restored"):
            pp_ok = False; pp_why = " pp did NOT restore"
        elif not r.get("pp_differs_from_wd"):
            pp_ok = False; pp_why = " pp effect == whole-DUT effect (single point widened to whole DUT!)"
        elif b and w and p:
            fp_sensitive = (w[0] != b[0])
            if fp_sensitive:
                if not (p[0] != b[0] and abs(p[0] - b[0]) < abs(w[0] - b[0])):
                    pp_ok = False; pp_why = f" pp not honored/narrower (fp base={b[0]} pp={p[0]} wd={w[0]})"
            else:
                # fp-insensitive: distinguish via active-DUT count
                if not (p[2] == b[2] and w[2] < b[2]):
                    pp_ok = False; pp_why = f" pp did not retain DUT / wd did not drop it (dut base={b[2]} pp={p[2]} wd={w[2]})"
        ok = ok and pp_ok
        status = "PASS" if ok else "FAIL"
        why = "" if ok else (
            (f"  responded={r.get('responded')} restored={r.get('restored')}" if not (r.get('responded') and r.get('restored')) else "")
            + pp_why)
        print(f"  [{status}] {vname}  serial={r.get('serial')}  wd off={r.get('off')} on={r.get('on')}  pp on={r.get('ppOn')}{why}")
        if not ok:
            fails.append(vname)
        results.append(r)

    # cross-view: all views must have targeted the SAME serial (shared-key promise)
    same_serial = len(serials) <= 1
    print("\n" + "=" * 60)
    if envs and not results:
        print(f"  [ENV-UNAVAILABLE] no view could be rendered -- UNVERIFIED (exit 3).")
        sys.exit(3)
    if not same_serial:
        print(f"  FAIL: views targeted different serials {serials} -- cannot compare cross-view.")
        fails.append("serial-mismatch")
    passed = len(results) - len([f for f in fails if f in [r.get("view") for r in results]])
    print(f"  views responding+restoring consistently: {len(results)-len(fails)} / {len(pages)}"
          + (f"   (shared serial: {next(iter(serials))})" if same_serial and serials else ""))
    if fails:
        print(f"  FAIL: {', '.join(sorted(set(fails)))} -- see 'IMPLICATION OF A FAILURE' in this file's header.")
    if not args.keep:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"  (views kept at {tmp})")
    if fails:
        sys.exit(1)
    if envs:
        print("  NOTE: some views UNVERIFIED (browser env).")
        sys.exit(3)
    print("  VERDICT: GREEN -- every view honors the shared GF key (whole-DUT AND"
          " single-point), restores, and does not widen a single point to the whole DUT.")
    sys.exit(0)


if __name__ == "__main__":
    main()
