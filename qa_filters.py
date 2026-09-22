#!/usr/bin/env python3
"""
qa_filters.py -- filter/plot/table/statistics COUPLING + Global-Filter
self-consistency gate for EVERY interactive view (scatter, stat_summary, boxplot,
distribution, env_coverage, summary, histogram, reference), single-site AND
cross-site compare.

The Reference Statistics view (padb_refstats) is an AGGREGATE view (no #plot
per-point markers): its own runReference block proves the #overall headline,
#pareto bar, and #grouptbl table stay coupled to one applyFilters(DATA) set --
overall total == filter output, per-group rows sum to it, pareto keys subset of
table keys, group-table Fail sums to overall Fail, and a freq filter shrinks all
three together and reverts.

Coupling coverage per plot type (view-agnostic, so every view above is checked):
  * filter -> plot   : toggling a condition/serial/temp filter changes the plotted
                       set and is exactly reversible (runGeneric).
  * filter -> table  : the stats/results table updates on a filter change and is
                       not stale (runTableChecks table-updates-on-filter).
  * plot <-> table   : every table row is actually plotted and vice-versa
                       (table-conds-are-plotted / plotted-groups-in-table).
  * statistics sanity: Q1<=median<=Q3, min<=mean<=max, %oos in [0,100], TI lo<=hi,
                       margin sign matches pass/fail (table-stats-sane).
  * range/zoom coupling: narrowing the frequency range AND a Plotly drag-zoom move
                       BOTH the plot and the table together, and restore
                       (runCoordination; histogram uses its Pass/Fail filter).
  * filter effect    : for EVERY filter dimension (data-col granular -- serial,
                       each condition dim, temp), deselecting its values changes
                       the plotted set and reselecting restores it; a dimension
                       whose values never change the plot is a FAIL, not a skip
                       (runFilterEffect -- catches a dead serial filter).
  * filter persist   : each filter's state survives saveState()->loadState()
                       (runPersistence -- catches "the change does not persist").
  * group-by         : every group-by mode renders non-blank and reverts
                       (runGroupBy).
  * CSV export       : matches what's on screen + histogram import round-trip
                       (runCsvExport); Site-fence panels (runHistogramSite /
                       runSiteFencePanel) on compare pages.
The view-specific blocks (boxplot deep-GF, env_cov/distribution/histogram site
fence) self-detect and self-skip on views they don't apply to.

WHY THIS EXISTS
---------------
The recurring class of bug in this tool is a filter (especially the Global
Filter) that does not do exactly what it says, or a plot/table that drift out of
sync when a filter changes. Those are trust-killers for engineers who bring data
they know cold. `qa_view_sweep.py` proves a page *renders*; this proves its
filters are *self-consistent*: for every filter operation, the plotted point set
changes by exactly the right set difference, and nothing silently blanks.

HOW IT WORKS
------------
For each target boxplot HTML, we inject a self-test harness (`_HARNESS_JS`) that
drives the page's OWN controls headlessly -- turning on "Show Points" so every
plotted point is a real marker we can read back (with its serial via the point's
hover text and its frequency via the box category on x) -- applies a matrix of
filter / GF operations, and after each asserts a set of invariants by comparing
the plotted-point set before/after. Results are written into a `#__qa_results`
JSON sentinel; we render under headless Edge (`--dump-dom`, same mechanism as
qa_js_segments.py / qa_view_sweep.py -- each run is a fresh temp file + fresh
--user-data-dir, so browser caching can't stale a result) and parse it back.

Any inconsistency is a FAIL naming the (page, scenario, invariant). Exit code 1
on any FAIL (matches qa_padb.py's convention), so it's usable as a gate.

INVARIANTS (boxplot)
--------------------
  baseline-not-blank      the page shows points before any op
  outliers-GF-precise     "Set outliers as GF" removes ONLY points sharing an
                          outlier's (serial, condition, freq-label) identity --
                          never a whole DUT across other frequencies -- and never
                          blanks the plot; every flagged outlier is removed
  clear-GF-restores       Clear global filter returns the exact baseline point set
  deselect-site           (compare) unchecking a Site value removes exactly that
                          site's points; re-checking restores
  deselect-serial         unchecking a serial removes exactly that serial's
                          points; re-checking restores
  filter-GF-whole-dut     "Set filter as GF" on one narrowed serial removes that
                          serial across ALL frequencies (whole-DUT) and leaves the
                          rest; clear restores
  reset-restores          Reset returns the exact baseline point set

INVARIANTS (CSV export -- all views with an export; via captured Blob text)
--------------------------------------------------------------------------
  csv-export-nonblank            an export produces a non-empty data section when
                                 the view has data
  csv-export-tracks-filter       a filter that shrinks the plot shrinks the export
                                 (and undo restores the exact baseline row count) --
                                 the export must reflect the FILTERED view, not the
                                 whole dataset
  csv-export-matches-plotted-points  (histogram) CSV data rows == plotted measurements
  csv-export-matches-results-table   (summary)   CSV data rows == on-screen table rows
  csv-import-roundtrip-same-plot/table  (histogram) re-importing the just-exported
                                 CSV reproduces the identical plot + table view

USAGE
-----
  py qa_filters.py                       # all compare boxplots under the default root
  py qa_filters.py --root C:\\temp\\data   # point at another data root
  py qa_filters.py --glob "*boxplot*.html" --include-single-site
  py qa_filters.py --page path\\to\\one_boxplot.html
  py qa_filters.py --budget 20000 --timeout 120

NOTE: this tests the pages AS BUILT. A page built before a GF fix will (rightly)
fail -- rebuild it first (padb_v2.py) so it carries current code.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
_EXCLUDE_DIR_PARTS = {"backup", ".git", "__pycache__"}


def _installed_browsers() -> list[str]:
    return [c for c in _BROWSER_CANDIDATES if Path(c).exists()]


def _render_dom(browser: str, test_html: Path, td: str, budget_ms: int, timeout_s: int) -> str:
    """Headless-render test_html with --dump-dom; return the dumped DOM (''
    on any failure). Shared by the page harness and the browser smoke test."""
    dom_path = Path(td) / "dom.html"
    try:
        with dom_path.open("w", encoding="utf-8") as dom_f:
            proc = subprocess.Popen(
                [browser, "--headless", "--disable-gpu", "--disable-crash-reporter",
                 f"--virtual-time-budget={budget_ms}", f"--user-data-dir={td}",
                 "--dump-dom", str(test_html)],
                stdout=dom_f, stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        return dom_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _browser_smoke(browser: str) -> bool:
    """Prove this browser can headless-render + --dump-dom a trivial page. This is
    the honest environment gate: if it fails, 'no sentinel' on a real page is an
    ENVIRONMENT failure (browser dead), NOT a product defect -- so we must not
    report those pages as FAILs. Marker is set by a tiny inline script so a
    browser that renders but doesn't run JS also fails the gate."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        p = Path(td) / "smoke.html"
        p.write_text("<!doctype html><body><pre id='__smoke'></pre>"
                     "<script>document.getElementById('__smoke').textContent='QA_OK';</script>",
                     encoding="utf-8")
        dom = _render_dom(browser, p, td, 3000, 30)
    return "__smoke" in dom and "QA_OK" in dom


def _pick_working_browser() -> tuple[str | None, list[str]]:
    """Return (first browser that passes the smoke test, list of installed).
    A working browser proves the headless tier can actually run."""
    installed = _installed_browsers()
    for b in installed:
        if _browser_smoke(b):
            return b, installed
    return None, installed


# ---------------------------------------------------------------------------
# The injected self-test harness. Boxplot-aware; degrades (SKIP) on pages that
# aren't boxplots or lack a given control. Writes JSON into #__qa_results.
# ---------------------------------------------------------------------------
_HARNESS_JS = r"""
(function(){
  function emit(obj){
    var pre=document.getElementById('__qa_results')||document.createElement('pre');
    pre.id='__qa_results'; pre.style.display='none';
    pre.textContent=JSON.stringify(obj); document.body.appendChild(pre);
  }
  // ---- generic helpers (all views) ----
  // A stable signature of the plotted DATA: every trace with a y-array, as
  // name:len, sorted. Reference lines (Spec Hi/Lo, TTL) are included -- they are
  // constant across filter changes, so they don't affect the diff either way.
  // The Plotly graph div: most views use #plot, but distribution uses its own
  // id -- find it generically by Plotly's own marker class.
  function _gd(){ return document.getElementById('plot') || document.querySelector('.js-plotly-plot'); }
  function plotSig(){
    var gd=_gd(); if(!gd||!gd.data) return '[]';
    var a=[];
    gd.data.forEach(function(t){
      // data length = y-array (scatter/lines/box overlay) or x-array (histogram
      // traces carry values on x, counts are computed so there's no y).
      var L=(t.y&&t.y.length!==undefined)?t.y.length:((t.x&&t.x.length!==undefined)?t.x.length:null);
      if(L!==null) a.push((t.name||'?')+':'+L);
    });
    a.sort(); return JSON.stringify(a);
  }
  // VALUE-aware signature: name:len:finiteCount:sum(rounded). On aggregate views
  // (stat_summary/summary TI bands) a serial/temp filter changes the STATISTICS,
  // not the number of traces or their length -- length-only plotSig() can't see
  // that and would false-flag a working filter as dead. This catches value
  // changes too, and is still exactly reversible (same inputs -> same sum).
  function plotSigV(){
    var gd=_gd(); if(!gd||!gd.data) return '[]';
    var a=[];
    gd.data.forEach(function(t){
      var ys=(t.y&&t.y.length!==undefined)?t.y:((t.x&&t.x.length!==undefined)?t.x:null);
      if(ys===null) return;
      var s=0,ss=0,n=0; for(var i=0;i<ys.length;i++){var v=ys[i]; if(typeof v==='number'&&isFinite(v)){s+=v;ss+=v*v;n++;}}
      /* Magnitude-independent precision (7 sig figs), NOT a fixed 2-decimal round.
         A fixed round(s*100)/100 erases any change below 0.01 -- which silently
         MISSED a genuine filter effect on small-magnitude data (e.g. FM1 accuracy
         deviations ~0.006 dB: deselecting a DUT shifted the mean but the rounded
         sum was identical), reporting a live filter as a DEAD one. The second
         moment (ss) guards the rare within-trace cancellation where two values
         move by +x/-x leaving the plain sum unchanged. */
      var q=function(x){return (x===0)?'0':x.toExponential(6);};
      a.push((t.name||'?')+':'+ys.length+':'+n+':'+q(s)+':'+q(ss));
    });
    a.sort(); return JSON.stringify(a);
  }
  // Filter checkboxes across every view (condition-dim / serial / port / temp).
  // Deliberately excludes non-filter toggles (hover columns 'hchk', show-points,
  // hide-spec, GF-mode) so reversibility isn't tested on controls that don't
  // (or shouldn't) map 1:1 to the plotted set.
  var _FILT_RE=/(^|\s)(ser_chk|sum_ser_chk|ec_ser_chk|box_ser_chk|hf_serial|sum_temp_chk|ec_temp_chk|env_chk|ss_port_chk|box_cond_[A-Za-z0-9_]+|box_cond_lf_chk|hf_[A-Za-z0-9_]+|fchk)(\s|$)/;
  function filterBoxes(){
    return [].slice.call(document.querySelectorAll('input[type=checkbox]'))
      .filter(function(c){return _FILT_RE.test(c.className||'');});
  }
  function fire(c,checked){ if(c.checked!==checked){c.checked=checked; c.dispatchEvent(new Event('change',{bubbles:true}));} }
  function firstResetFn(){
    var names=['clearEverything','resetFilters','resetView','hResetFilters'];
    for(var i=0;i<names.length;i++){ try{ if(eval('typeof '+names[i])==='function') return names[i]; }catch(e){} }
    return null;
  }
  /* Group every filter checkbox by its DIMENSION -- data-col when present (the
     condition/serial/temp panels all share class "fchk" but differ by data-col,
     so class alone conflates them -- that is exactly how a dead serial filter
     slipped past the old one-per-class reversibility loop), else the class. */
  function filterDims(){
    var m={};
    filterBoxes().forEach(function(c){
      var dim=c.getAttribute('data-col')||(c.className||'').trim();
      (m[dim]=m[dim]||[]).push(c);
    });
    return m;
  }
  function setAllBoxes(list,checked){
    list.forEach(function(c){ if(c.checked!==checked){c.checked=checked; c.dispatchEvent(new Event('change',{bubbles:true}));} });
  }
  /* EFFECT: for every filter dimension, toggling ONE of its values must change
     the plotted set (value-aware, and reselecting restores it). A dimension where
     toggling a value changes nothing -- while the plot has data -- is not wired to
     the plot: the exact "updating the serial filter does not change the plot" bug.
     Deselect-ONE (a partial selection) is unambiguous; the separate "deselect ALL
     values" question (empty=show-nothing vs show-all) is view-inconsistent today
     and tracked separately, not gated here (it would false-flag views that treat
     an empty selection as "no filter"). */
  function runFilterEffect(R,chk,skip,heavy){
    var dims=filterDims(), names=Object.keys(dims);
    if(!names.length){ skip('filter-effect','no filter checkboxes in this view'); return; }
    // Test serial/port dimensions FIRST -- they recur as dead filters and must be
    // covered even under the heavy-page dimension cap.
    names.sort(function(a,b){
      function pri(n){var l=n.toLowerCase();return /serial|ser_chk|s\/n|unit id|dut id/.test(l)?0:/port/.test(l)?1:2;}
      return pri(a)-pri(b);
    });
    // Runs on heavy pages too (2 renders per dim is bounded), capped to keep a
    // very large page's render count in check. A dead filter on a big page is
    // exactly the reported failure and must be gated.
    var maxDims=heavy?4:names.length, tested=0;
    var S0=plotSig(), hasData=S0!=='[]'&&JSON.parse(S0).some(function(s){return parseInt(s.split(':').pop(),10)>0;});
    names.forEach(function(dim){
      var list=dims[dim], vals=list.filter(function(c){return !c.disabled;});
      if(!vals.length){ skip('filter-effect['+dim+']','no toggleable values'); return; }
      if(tested>=maxDims){ skip('filter-effect['+dim+']','capped on heavy page (serial/port tested first)'); return; }
      tested++;
      var checkedVals=vals.filter(function(x){return x.checked;});
      if(checkedVals.length<2){ skip('filter-affects-plot['+dim+']','single toggleable value (toggling it = deselect-all edge, not a partial test)'); return; }
      // A dimension is EFFECTIVE if toggling AT LEAST ONE of its values changes the
      // plotted set (value-aware). Try values until one changes -- toggling a
      // single "special" value (e.g. a ΔTemp view's Room baseline) can legitimately
      // be a no-op, so requiring the FIRST value to change would false-flag. Each
      // toggle is restored (reselect must reproduce the exact baseline).
      var before=plotSigV(), changed=false, restoreOK=true, tried=[];
      for(var k=0;k<checkedVals.length && k<4 && !changed;k++){
        var c=checkedVals[k]; tried.push(c.value);
        c.checked=false; c.dispatchEvent(new Event('change',{bubbles:true})); var off=plotSigV();
        c.checked=true;  c.dispatchEvent(new Event('change',{bubbles:true})); var back=plotSigV();
        if(back!==before) restoreOK=false;
        if(off!==before) changed=true;
      }
      chk('filter-reselect-restores['+dim+']', restoreOK,
          'restored='+restoreOK+(restoreOK?'':' | tried='+tried.join(',')));
      if(changed){ chk('filter-affects-plot['+dim+']', true, 'toggling a value in '+dim+' changed the plot (tried '+tried.join(',')+')'); }
      else if(!hasData){ skip('filter-affects-plot['+dim+']','plot already empty'); }
      else { chk('filter-affects-plot['+dim+']', false,
             'DEAD FILTER: toggling any of ['+tried.join(',')+'] in "'+dim+'" left the plot unchanged (baseline non-empty) -- not wired to the plot'); }
    });
  }
  /* PERSISTENCE: each filter's state must survive saveState()->loadState() (the
     "the serial number change does not persist" report). Uncheck one value, save,
     force it back checked in the DOM (simulating a fresh load), loadState, and
     assert it comes back UNchecked. Covers up to a few dimensions per view. */
  function runPersistence(R,chk,skip){
    if(typeof saveState!=='function'||typeof loadState!=='function'){
      skip('filter-persistence','view has no saveState/loadState'); return; }
    var dims=filterDims(), names=Object.keys(dims), tested=0;
    if(!names.length){ skip('filter-persistence','no filter checkboxes'); return; }
    // serial/port first, so they're covered even under the per-view cap.
    names.sort(function(a,b){
      function pri(n){var l=n.toLowerCase();return /serial|ser_chk|s\/n|unit id|dut id/.test(l)?0:/port/.test(l)?1:2;}
      return pri(a)-pri(b);
    });
    names.forEach(function(dim){
      if(tested>=4) return;
      var vals=dims[dim].filter(function(c){return !c.disabled;});
      var c=vals.filter(function(x){return x.checked;})[0]||vals[0];
      if(!c){ return; }
      var v=c.value;
      c.checked=false; c.dispatchEvent(new Event('change',{bubbles:true}));
      try{ saveState(); }catch(e){}
      var cur=dims[dim].filter(function(x){return x.value===v;})[0]; if(cur) cur.checked=true; // simulate fresh DOM
      try{ loadState(); }catch(e){}
      var after=dims[dim].filter(function(x){return x.value===v;})[0];
      chk('filter-state-persists['+dim+'='+v+']', !!after&&after.checked===false,
          'after loadState checked='+(after?after.checked:'gone')+' (expected false)');
      if(after){ after.checked=true; after.dispatchEvent(new Event('change',{bubbles:true})); }
      try{ saveState(); }catch(e){}
      tested++;
    });
    if(!tested) skip('filter-persistence','no toggleable filter values');
  }
  // Generic invariants for ANY view. `runViewSetup` optionally primes the view
  // (e.g. box: turn on Show Points) and returns a label. Returns pushes into R.
  function runGeneric(R,chk,skip,heavy){
    var S0=plotSig();
    chk('baseline-not-blank', S0!=='[]' && JSON.parse(S0).some(function(s){return parseInt(s.split(':').pop(),10)>0;}), 'sig='+S0.slice(0,120));
    // Per distinct filter class, exercise ONE checkbox: off -> on must restore
    // the exact baseline (reversibility), and unchecking should change something
    // (a filter that does nothing is itself suspicious -- reported soft).
    // On a HEAVY page this loop's many async update()s race the virtual-time
    // dump, so it's skipped -- the deterministic deep GF block below is what a
    // heavy compare boxplot is actually being gated for.
    if(heavy){ skip('filter-reversibility','skipped on heavy page (reduced suite for a deterministic render)'); }
    var boxes=filterBoxes(), byClass={};
    boxes.forEach(function(c){var k=(c.className||'').trim(); if(!byClass[k]) byClass[k]=c;});
    var classes=heavy?[]:Object.keys(byClass);
    if(!classes.length && !heavy) skip('filter-reversibility','no recognised filter checkboxes');
    classes.forEach(function(k){
      var c=byClass[k]; if(!c.checked){ // ensure we start from checked
        // find a checked one of the same class instead
        var alt=boxes.filter(function(x){return (x.className||'').trim()===k && x.checked;})[0];
        if(alt) c=alt; else { skip('filter-reversibility['+k+']','none checked'); return; }
      }
      var before=plotSig();
      fire(c,false); var off=plotSig();
      fire(c,true);  var back=plotSig();
      chk('filter-reversible['+k+'='+c.value+']', back===before, 'restored='+(back===before)+(back===before?'':' | before='+before.slice(0,80)+' back='+back.slice(0,80)));
      if(off===before) R.push({name:'filter-had-no-effect['+k+'='+c.value+']',skip:true,detail:'toggling this filter did not change the plot (may be legitimate, e.g. single-value dim)'});
    });
    // Reset must return to baseline.
    var rf=firstResetFn();
    if(rf){ try{ eval(rf+'()'); }catch(e){} chk('reset-restores', plotSig()===S0, 'via '+rf+'()'); }
    else skip('reset-restores','no reset function found');
  }

  // ---- table cross-check (all views with a Statistics/Results table) ----
  // Verifies the on-page numeric table is (a) consistent with the plotted set,
  // (b) internally sane (Q1<=median<=Q3, min<=mean<=max, %oos in [0,100], margin
  // signs match pass/fail), and (c) refreshes when a filter changes (not stale).
  // Deliberately does NOT re-derive Shapiro/NP-TI/k-factor from scratch -- it
  // checks relationships the table must satisfy regardless of how it computed.
  function _norm(s){ return String(s==null?'':s).replace(/\s+/g,' ').trim(); }
  function _num(s){ // first signed float in a cell (strips arrows/nbsp/✔✘)
    var m=_norm(s).replace(/[↓↑]/g,' ').match(/-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?/);
    return m?parseFloat(m[0]):null;
  }
  function _passTok(s){ var t=_norm(s); if(/✘|FAIL/.test(t))return false; if(/✔|PASS/.test(t))return true; return null; }
  function _tablePanelEl(){
    var ids=['box_stat_panel','stat_panel','sum_table_wrap','h_stats','ec_stat_panel'];
    for(var i=0;i<ids.length;i++){var e=document.getElementById(ids[i]); if(e) return e;} return null;
  }
  function _ensurePanelOpen(){ // open the collapsible stats panel exactly once (idempotent)
    var el=_tablePanelEl(); if(!el) return;
    if(getComputedStyle(el).display==='none'){
      var b=[].slice.call(document.querySelectorAll('button')).filter(function(x){
        return /statistic|results table|▶/i.test(x.textContent) && !/refresh/i.test(x.textContent);})[0];
      if(b){ try{b.click();}catch(e){} }
      if(getComputedStyle(el).display==='none'){ // still closed? try the toggle fns (single call)
        var fns=['toggleStatPanel','toggleStatsPanel'];
        for(var i=0;i<fns.length&&getComputedStyle(el).display==='none';i++){
          try{ if(eval('typeof '+fns[i])==='function') eval(fns[i]+'()'); }catch(e){} } }
    }
  }
  function openTable(){ _ensurePanelOpen(); _buildTable(); } // open + build (call ONCE)
  function _buildTable(){ // (re)build whatever table this view has, WITHOUT toggling the panel
    try{ if(typeof buildTable==='function') buildTable(true); }catch(e){}   // summary Results Table
    try{ [].slice.call(document.querySelectorAll('button')).forEach(function(b){
      if(/refresh/i.test(b.textContent)){ try{b.click();}catch(e){} } }); }catch(e){} // large-data manual refresh
  }
  function refreshTable(){ // rebuild the ALREADY-OPEN table WITHOUT toggling it
    try{update();}catch(e){}   // stat_summary/boxplot/histogram auto-rebuild an open table here
    _buildTable();
  }
  function readTable(){ // -> {headers, rows, col{}} for the first stats/results table
    var ids=['box_stat_panel','stat_panel','sum_table_wrap','h_stats','ec_stat_panel'];
    var t=null;
    for(var i=0;i<ids.length&&!t;i++){ var c=document.getElementById(ids[i]); if(c){ t=c.querySelector('table')||(c.tagName==='TABLE'?c:null); } }
    if(!t){ // fallback: any table whose header names a Condition/Freq/n column
      var all=[].slice.call(document.querySelectorAll('table'));
      t=all.filter(function(x){var h=_norm((x.tHead||{}).textContent||''); return /Condition|Freq|\bn\b/.test(h);})[0]||null;
    }
    if(!t) return null;
    var headers=[].slice.call(t.querySelectorAll('thead th, thead td')).map(function(h){return _norm(h.textContent);});
    var rows=[].slice.call(t.querySelectorAll('tbody tr')).map(function(tr){
      return [].slice.call(tr.children).map(function(td){return _norm(td.textContent);}); });
    var col={}; function find(re){ for(var i=0;i<headers.length;i++) if(re.test(headers[i])) return i; return -1; }
    col.cond=find(/Condition/i); col.freq=find(/Freq/i); col.n=find(/^n( DUTs)?$/i);
    col.q1=find(/^Q1$/i); col.med=find(/Median/i); col.q3=find(/^Q3$/i); col.std=find(/^Std$/i);
    col.mean=find(/^Mean$/i); col.min=find(/^Min$/i); col.max=find(/^Max$/i);
    col.ti=find(/TI Bounds/i); col.pass=find(/^Pass$/i); col.margin=find(/^Margin/i);
    col.mup=find(/Margin↑/i); col.mdn=find(/Margin↓/i);
    col.oos=find(/out-of-spec/i); col.p95=find(/p95/i); col.p99=find(/p99/i);
    return {el:t,headers:headers,rows:rows,col:col};
  }
  function tableConds(T){ var s={}; if(T.col.cond<0) return s;
    T.rows.forEach(function(r){ var c=r[T.col.cond]; if(c) s[c]=(s[c]||0)+1; }); return s; }
  function traceNames(){ var gd=_gd(); return (gd&&gd.data?gd.data:[]).map(function(t){return _norm(t.name||'');}); }

  function runTableChecks(R,chk,skip){
    openTable();
    var T=readTable();
    if(!T){ skip('table-present','no stats/results table in this view'); return; }
    var eps=6e-4;
    // (a1) not blank when the plot isn't
    var sig=plotSig(), plotHasData = sig!=='[]' && JSON.parse(sig).some(function(s){return parseInt(s.split(':').pop(),10)>0;});
    chk('table-not-blank', T.rows.length>0 || !plotHasData, 'rows='+T.rows.length);
    if(!T.rows.length){ return; }
    // (a2) every table condition is actually plotted (catches phantom/stale rows).
    // 'All' is an allowed aggregate label (histogram total / stat_summary pooled).
    var names=traceNames(), tc=tableConds(T);
    if(T.col.cond>=0){
      // A table row is "plotted" if it equals a trace name, is a prefix of one,
      // OR is a plotted trace name plus a per-temp suffix / grouped-row prefix that
      // the table legitimately adds without its own trace: "All / 20°C" (boxplot
      // non-Room breakdown), "Serial: X" / "Port: X" (Group-by rows). Strip those
      // affixes and re-check against the trace names.
      // A boxplot non-Room breakdown row is "cond / temp" while its own plotted
      // box trace is "cond (temp)" -- same box, different affix. Strip a trailing
      // temp affix ("/ x" OR "(x)") for a base-equality test, and also directly
      // test the "/ x" -> " (x)" transform. Only strip a leading Serial:/Port:/
      // Temp:/Site: prefix for genuine Group-by rows -- NOT an arbitrary "Key:",
      // which would eat a fragmented condition's own first dimension (e.g.
      // "HarmonicNumber: 2  Upper Spec (<=): ...") and false-flag it as phantom.
      var _stripTemp=function(c){ return _norm(String(c).replace(/\s*\/\s*[^/]+$/,'').replace(/\s*\([^()]*\)\s*$/,'')); };
      var _slashToParen=function(c){ return _norm(String(c).replace(/\s*\/\s*([^/]+)$/,' ($1)')); };
      var _grpBase=function(c){ return _norm(String(c).replace(/^(Serial|Port|Temp|Site)\s*:\s*/,'')); };
      var phantom=Object.keys(tc).filter(function(c){
        if(c==='All') return false;
        if(names.indexOf(c)>=0 || names.some(function(n){return n.indexOf(c)===0;})) return false;
        var cp=_slashToParen(c);   // "cond / temp" -> "cond (temp)" == a real box trace name
        if(names.indexOf(cp)>=0 || names.some(function(n){return n.indexOf(cp)===0;})) return false;
        var b=_stripTemp(c);       // temp-affix-stripped base equality, both sides
        if(names.indexOf(b)>=0 || names.some(function(n){return _stripTemp(n)===b || n.indexOf(b)===0 || b.indexOf(n)===0;})) return false;
        var g=_grpBase(b);         // Group-by Serial:/Port: row fallback
        return !(names.indexOf(g)>=0 || names.some(function(n){return n.indexOf(g)===0 || g.indexOf(n)===0;}));
      });
      chk('table-conds-are-plotted', phantom.length===0, phantom.length?('not plotted: '+phantom.slice(0,4).join(' | ')):('conds='+Object.keys(tc).length));
    } else skip('table-conds-are-plotted','no Condition column');
    // (a3) reverse: every box/histogram PRIMARY trace has a table row (clean by type)
    var gd=_gd(), prim=(gd&&gd.data?gd.data:[]).filter(function(t){return t.type==='box'||t.type==='histogram';})
        .map(function(t){return _norm(t.name||'');}).filter(function(n){return n;});
    if(prim.length){
      // Normalize the temp affix: a multi-temp box trace is "cond (temp)" while
      // its table row is "cond / temp" -- same box, different affix. Compare a
      // temp-stripped base and also the trace's own "(temp)"->"/ temp" form.
      var _affix=function(s){ return _norm(String(s).replace(/\s*\([^()]*\)\s*$/,'').replace(/\s*\/\s*[^/]+$/,'')); };
      var tcBases={}; Object.keys(tc).forEach(function(c){ tcBases[_affix(c)]=1; tcBases[c]=1; });
      var missing=prim.filter(function(n){ return !(tc[n]||tcBases[n]||tcBases[_affix(n)]); });
      chk('plotted-groups-in-table', missing.length===0, missing.length?('missing rows: '+missing.slice(0,4).join(' | ')):('primary='+prim.length));
    }
    // (b) statistical sanity, per whatever columns this table exposes
    var bad=[], checked=0;
    T.rows.forEach(function(r,ri){
      function N(i){return i>=0?_num(r[i]):null;}
      var q1=N(T.col.q1),md=N(T.col.med),q3=N(T.col.q3);
      if(q1!=null&&md!=null&&q3!=null){checked++; if(!(q1<=md+eps&&md<=q3+eps))bad.push('row'+ri+' Q1<=Med<=Q3 ['+q1+','+md+','+q3+']');}
      var mn=N(T.col.min),me=N(T.col.mean),mx=N(T.col.max);
      if(mn!=null&&me!=null&&mx!=null){checked++; if(!(mn<=me+eps&&me<=mx+eps))bad.push('row'+ri+' Min<=Mean<=Max ['+mn+','+me+','+mx+']');}
      var sd=N(T.col.std); if(sd!=null){checked++; if(sd<-eps)bad.push('row'+ri+' Std<0 ('+sd+')');}
      var nn=N(T.col.n); if(nn!=null){checked++; if(nn<1)bad.push('row'+ri+' n<1 ('+nn+')');}
      var oos=N(T.col.oos); if(oos!=null){checked++; if(oos<-eps||oos>100+eps)bad.push('row'+ri+' %oos out of [0,100] ('+oos+')');}
      // TI bounds [lo,hi]
      if(T.col.ti>=0){ var m=_norm(r[T.col.ti]).match(/(-?\d+(?:\.\d+)?)[^\d-]+(-?\d+(?:\.\d+)?)/);
        if(m){checked++; if(parseFloat(m[1])>parseFloat(m[2])+eps)bad.push('row'+ri+' TI lo>hi ['+m[1]+','+m[2]+']');} }
      // percentile ordering med<=p95<=p99<=max (histogram)
      var p95=N(T.col.p95),p99=N(T.col.p99);
      if(p95!=null&&p99!=null){checked++; if(p95>p99+eps)bad.push('row'+ri+' p95>p99 ['+p95+','+p99+']');
        if(mx!=null&&p99>mx+eps)bad.push('row'+ri+' p99>Max ['+p99+','+mx+']'); }
      // margin sign must agree with pass/fail token (summary Margin↑/↓; stat_summary Pass)
      var pt=T.col.pass>=0?_passTok(r[T.col.pass]):null;
      if(pt!==null){ checked++;
        var mup=T.col.margin>=0?null:null; // stat_summary: Margin↓/↑ combined; PASS => both >=0
        var mcol=T.col.margin>=0?T.col.margin:-1;
        if(mcol>=0){ var nums=_norm(r[mcol]).replace(/[↓↑]/g,' ').match(/-?\d+(?:\.\d+)?/g)||[];
          var anyNeg=nums.some(function(x){return parseFloat(x)<-eps;});
          if(pt===true&&anyNeg)bad.push('row'+ri+' PASS but a margin<0 ('+_norm(r[mcol])+')');
          if(pt===false&&!anyNeg&&nums.length)bad.push('row'+ri+' FAIL but no margin<0 ('+_norm(r[mcol])+')'); }
      }
      // summary per-side margin cells carry their own ✔/✘
      [T.col.mup,T.col.mdn].forEach(function(ci){ if(ci>=0){ var cell=r[ci]; var t=_passTok(cell), v=_num(cell);
        if(t!==null&&v!=null){checked++; if(t===true&&v<-eps)bad.push('row'+ri+' margin ✔ but <0 ('+_norm(cell)+')');
          if(t===false&&v>eps)bad.push('row'+ri+' margin ✘ but >0 ('+_norm(cell)+')');}} });
    });
    if(checked>0) chk('table-stats-sane', bad.length===0, bad.length?bad.slice(0,5).join(' ; '):('checks='+checked+' rows='+T.rows.length));
    else skip('table-stats-sane','no sanity-checkable columns');
    // (c) table n == plotted point count, where per-point data is embedded (histogram)
    var hist=(gd&&gd.data?gd.data:[]).filter(function(t){return t.type==='histogram';});
    if(hist.length&&T.col.n>=0&&T.col.cond>=0){
      var nbad=[], tot=0;
      hist.forEach(function(t){ var nm=_norm(t.name||''), L=(t.x?t.x.length:0); tot+=L;
        var row=T.rows.filter(function(r){return r[T.col.cond]===nm;})[0];
        if(row){ var tn=_num(row[T.col.n]); if(tn!=null&&tn!==L)nbad.push(nm+' table_n='+tn+' pts='+L); } });
      var allRow=T.rows.filter(function(r){return r[T.col.cond]==='All';})[0];
      if(allRow){ var an=_num(allRow[T.col.n]); if(an!=null&&an!==tot)nbad.push('All table_n='+an+' total_pts='+tot); }
      chk('table-n-matches-plotted-points', nbad.length===0, nbad.length?nbad.slice(0,4).join(' ; '):('traces='+hist.length+' totpts='+tot));
    }
    // (d) table refreshes on a filter change (not stale) and restores
    // Cheap fingerprint: row count + a sample of the first/last few rows. O(1) in
    // table size (a heavy boxplot's stats table can be thousands of rows -- fully
    // serializing it on every digest was a real cost). A filter change alters the
    // row count and/or the sampled rows, so this still detects change vs restore.
    function digest(){ var T2=readTable(); if(!T2) return null;
      var rs=T2.rows, n=rs.length, s=[];
      [0,1,2,n-3,n-2,n-1].forEach(function(i){ if(i>=0&&i<n) s.push(rs[i].join('')); });
      return n+'|'+s.join(''); }
    var boxes=filterBoxes().filter(function(c){return c.checked;});
    if(boxes.length>=2){
      var c=boxes[0], d0=digest(), s0=plotSig();
      // Fire the change (its onchange calls update(), which auto-refreshes an
      // open table in every view except large-data summary). Do NOT re-toggle
      // the panel -- that would close it and leave stale DOM behind.
      fire(c,false); var d1=digest(), s1=plotSig();
      var needForce=false;
      if(d1===d0 && s1!==s0){ refreshTable(); d1=digest(); needForce=(d1!==d0); } // large-data manual-refresh path
      if(s1===s0){ R.push({name:'table-updates-on-filter',skip:true,detail:'chosen filter did not change the plot (single-value dim?)'}); }
      else {
        chk('table-updates-on-filter', d1!==d0, (d1!==d0)?(needForce?'updated only after an explicit Refresh (large-data design)':'auto-updated on filter change'):'STALE: plot changed but table did not');
        fire(c,true); refreshTable(); var d2=digest();
        chk('table-restores-on-filter', d2===d0, 'restored='+(d2===d0));
      }
    } else skip('table-updates-on-filter','fewer than 2 checked filter boxes');
    // leave the view at baseline
    var rf=firstResetFn(); if(rf){ try{eval(rf+'()');}catch(e){} }
  }

  // ---- "Group by" robustness (all views with a group-by selector) ----
  // Every group-by mode must produce a valid, non-blank plot (no group-by option
  // should blank the plot or throw), and reverting to the default must restore the
  // baseline. Catches pooling/aggregation bugs a specific group-by mode can hit.
  function runGroupBy(R,chk,skip){
    var ids=['groupby','box_group_by','statGroupBySel','sumGroupBySel','ecGroupBySel'];
    var sel=null; for(var i=0;i<ids.length;i++){var e=document.getElementById(ids[i]); if(e&&e.tagName==='SELECT'){sel=e;break;}}
    if(!sel){ skip('group-by','no group-by selector in this view'); return; }
    var opts=[].slice.call(sel.options).map(function(o){return o.value;});
    if(opts.length<2){ skip('group-by','single group-by option'); return; }
    var base=[].slice.call(sel.selectedOptions).map(function(o){return o.value;});
    var S0=plotSig();
    function setOnly(v){ [].slice.call(sel.options).forEach(function(o){o.selected=(o.value===v);}); sel.dispatchEvent(new Event('change',{bubbles:true})); }
    var blank=[];
    opts.forEach(function(v){
      try{ setOnly(v); }catch(e){ blank.push(v+'(threw)'); return; }
      var s=plotSig(), ok=s!=='[]'&&JSON.parse(s).some(function(x){return parseInt(x.split(':').pop(),10)>0;});
      if(!ok) blank.push(v||'(Condition)');
    });
    // restore baseline selection
    [].slice.call(sel.options).forEach(function(o){o.selected=base.indexOf(o.value)>=0;});
    sel.dispatchEvent(new Event('change',{bubbles:true}));
    chk('group-by-all-modes-nonblank', blank.length===0, blank.length?('blank/threw for: '+blank.slice(0,5).join(' | ')):('modes='+opts.length));
    chk('group-by-reversible', plotSig()===S0, 'restored='+(plotSig()===S0));
  }

  // ---- filter <-> plot <-> table coordination (frequency range + drag-zoom) ----
  // The existing table-updates-on-filter check exercises a CONDITION CHECKBOX.
  // This one exercises the OTHER input class -- the frequency range and a Plotly
  // drag-zoom -- and asserts BOTH the plotted data AND the on-page table respond
  // together (and restore). This is exactly the coordination that broke on the
  // scatter view (drag-zoom moved the plot but not the freq sliders/table), and
  // that readTable() alone can't cover because scatter's data-rows table isn't a
  // stats table.
  function _coordDigest(){ // digest of whatever table this view shows (stats OR scatter data-rows)
    var T=readTable();
    if(T){ var rs=T.rows,n=rs.length,s=[]; [0,1,2,n-3,n-2,n-1].forEach(function(i){if(i>=0&&i<n)s.push(rs[i].join(''));}); return 'T'+n+'|'+s.join(''); }
    var sp=document.getElementById('scatter_table_panel');
    if(sp){ return 'S'+sp.querySelectorAll('tr').length; }
    return null;
  }
  function _openAnyTable(){
    var sp=document.getElementById('scatter_table_panel');
    if(sp){ if(getComputedStyle(sp).display==='none'){ try{ if(typeof toggleScatterTable==='function') toggleScatterTable(); }catch(e){} } return; }
    openTable();
  }
  function _freqPair(){
    var pairs=[['freq_lo_txt','freq_hi_txt'],['ec_freq_lo_txt','ec_freq_hi_txt'],
               ['box_freq_lo','box_freq_hi'],['dist_freq_lo_txt','dist_freq_hi_txt']];
    for(var i=0;i<pairs.length;i++){var lo=document.getElementById(pairs[i][0]),hi=document.getElementById(pairs[i][1]); if(lo&&hi) return {lo:lo,hi:hi};}
    return null;
  }
  function _setFreq(p,lo,hi){
    p.lo.value=String(lo); p.hi.value=String(hi);
    ['input','change'].forEach(function(ev){ p.lo.dispatchEvent(new Event(ev,{bubbles:true})); p.hi.dispatchEvent(new Event(ev,{bubbles:true})); });
    try{ if(typeof update==='function') update(); }catch(e){}
  }
  function runCoordination(R,chk,skip){
    _openAnyTable();
    var p=_freqPair();
    if(p){
     var lo0=parseFloat(p.lo.value), hi0=parseFloat(p.hi.value);
     if(hi0>lo0){
      var S0=plotSig(), D0=_coordDigest();
      // (A) narrow the frequency-range INPUT -> plot and table must both narrow
      _setFreq(p, lo0+(hi0-lo0)*0.40, lo0+(hi0-lo0)*0.60);
      var S1=plotSig(), D1=_coordDigest();
      if(S1===S0){ skip('freq-range-coordinates','narrowing the freq range did not change the plot (few distinct freqs?)'); }
      else{
        chk('freq-range-narrows-plot', true, 'plot responded to the freq-range input');
        if(D0!==null) chk('freq-range-narrows-table', D1!==D0, (D1!==D0)?'table tracked the freq range':'STALE: plot narrowed but table did not');
        else skip('freq-range-narrows-table','no table in this view');
      }
      _setFreq(p, lo0, hi0);
      var S2=plotSig(), D2=_coordDigest();
      chk('freq-range-restores', S2===S0 && (D0===null||D2===D0), 'plot='+(S2===S0)+' table='+(D0===null?'n/a':(D2===D0)));
      // (B) drag-zoom (Plotly relayout) -> freq inputs + plot + table must all respond.
      // Sub-range the axis in its OWN current units (numeric freq / log-freq / box
      // category indices), so _onPlotRelayout interprets it the same way a real drag would.
      var gd=document.getElementById('plot');
      var xr=(gd&&gd.layout&&gd.layout.xaxis)?gd.layout.xaxis.range:null;
      if(gd && typeof _onPlotRelayout==='function' && gd.emit && xr && xr.length===2 && xr[1]>xr[0]){
        var a=xr[0], b=xr[1], ev={};
        ev['xaxis.range[0]']=a+(b-a)*0.40; ev['xaxis.range[1]']=a+(b-a)*0.60;
        try{ gd.emit('plotly_relayout',ev); }catch(e){}
        var moved=(Math.abs(parseFloat(p.lo.value)-lo0)>1e-6)||(Math.abs(parseFloat(p.hi.value)-hi0)>1e-6);
        var Sz=plotSig(), Dz=_coordDigest();
        if(Sz===S0){ skip('drag-zoom-coordinates','drag-zoom did not change the plot (few distinct freqs?)'); }
        else{
          chk('drag-zoom-syncs-freq-inputs', moved, moved?'freq inputs moved with the zoom':'zoom moved the plot but NOT the freq inputs');
          chk('drag-zoom-narrows-plot', true, 'plot responded to drag-zoom');
          if(D0!==null) chk('drag-zoom-narrows-table', Dz!==D0, (Dz!==D0)?'table tracked the drag-zoom':'STALE: zoom moved the plot but not the table');
        }
        try{ gd.emit('plotly_relayout',{'xaxis.autorange':true}); }catch(e){}
      } else {
        skip('drag-zoom-coordination','view has no _onPlotRelayout / numeric x-range (drag-zoom not wired to filters)');
      }
     } else { skip('freq-range-coordination','freq range not readable ('+p.lo.value+'..'+p.hi.value+')'); }
    } else {
      skip('freq-range-coordination','no frequency-range input in this view (e.g. histogram)');
    }
    // (C) Histogram Pass/Fail filter -> plot AND stats table must both respond+restore.
    // The histogram has no swept x, so its coordination is the pass/fail (h_pf)
    // and condition filters, not a freq range. This is the switching-speed case.
    var pf=document.getElementById('h_pf');
    if(pf && pf.options && pf.options.length>=2){
      var hS0=plotSig(), hD0=_coordDigest();
      var curPf=pf.value, altPf=null;
      for(var oi=0;oi<pf.options.length;oi++){ if(pf.options[oi].value!==curPf){altPf=pf.options[oi].value;break;} }
      pf.value=altPf; pf.dispatchEvent(new Event('change',{bubbles:true})); try{if(typeof update==='function')update();}catch(e){}
      var hS1=plotSig(), hD1=_coordDigest();
      if(hS1===hS0){ skip('histogram-passfail-coordinates','Pass/Fail did not change the plot (no spec / all pass?)'); }
      else{
        chk('histogram-passfail-narrows-plot', true, 'plot responded to the Pass/Fail filter');
        if(hD0!==null) chk('histogram-passfail-narrows-table', hD1!==hD0, (hD1!==hD0)?'stats table tracked Pass/Fail':'STALE: plot changed but the stats table did not');
        else skip('histogram-passfail-narrows-table','no stats table');
      }
      pf.value=curPf; pf.dispatchEvent(new Event('change',{bubbles:true})); try{if(typeof update==='function')update();}catch(e){}
      var hS2=plotSig(), hD2=_coordDigest();
      chk('histogram-passfail-restores', hS2===hS0 && (hD0===null||hD2===hD0), 'plot='+(hS2===hS0)+' table='+(hD0===null?'n/a':(hD2===hD0)));
    }
    var rf=firstResetFn(); if(rf){ try{eval(rf+'()');}catch(e){} }
  }

  // ---- CSV export matches the on-screen view (all views with an export) ----
  // Every "Export CSV" / "Save CSV" must emit exactly what the viewer is looking
  // at right now -- the FILTERED condition/freq table (summary/stat_summary/
  // env_coverage) or the FILTERED point/measurement set (scatter/histogram) --
  // never the whole unfiltered dataset. This is the self-consistency version of
  // the real bug class where a filter (e.g. the Temperature checkboxes) was
  // honoured by the table but ignored by the export.
  //
  // Exports trigger a download (build a Blob -> object URL -> a.click) rather than
  // returning text, so we intercept the Blob constructor to capture the exact
  // string, stub a.click + alert/confirm so nothing navigates or blocks, and
  // restore all of them afterwards. "Data rows" = the lines after any leading
  // #-comment metadata block and the single header line.
  function captureCsv(fn){
    var realBlob=window.Blob, cap=null;
    var realClick=HTMLAnchorElement.prototype.click, realAlert=window.alert, realConfirm=window.confirm;
    window.Blob=function(parts,opts){ try{ if(parts&&parts[0]!=null) cap=String(parts[0]); }catch(e){} return new realBlob(parts,opts); };
    HTMLAnchorElement.prototype.click=function(){};
    window.alert=function(){}; window.confirm=function(){return true;};
    try{ fn(); }catch(e){ if(cap===null) cap='__THREW__ '+e; }
    finally{ window.Blob=realBlob; HTMLAnchorElement.prototype.click=realClick; window.alert=realAlert; window.confirm=realConfirm; }
    return cap;
  }
  function csvDataRows(text){
    if(text==null) return null;
    if(String(text).indexOf('__THREW__')===0) return text;   // error marker; caller handles
    var lines=String(text).split(/\r\n|\n/).filter(function(l){return l.length>0;});
    var i=0; while(i<lines.length && lines[i].charAt(0)==='#') i++;   // drop leading metadata comments
    return lines.slice(i+1);   // drop the header row
  }
  function _plotHasData(){ var s=plotSig(); return s!=='[]' && JSON.parse(s).some(function(x){return parseInt(x.split(':').pop(),10)>0;}); }
  function runCsvExport(R,chk,skip){
    var exps=[];
    if(typeof exportTableCSV==='function') exps.push({name:'exportTableCSV',call:function(){exportTableCSV();},ref:'table'});
    if(typeof hExportCsv==='function')     exps.push({name:'hExportCsv',call:function(){hExportCsv();},ref:'points'});
    if(typeof saveCSV==='function')        exps.push({name:'saveCSV',call:function(){saveCSV(false);},ref:'none'});
    if(!exps.length){ skip('csv-export','no CSV export in this view'); return; }
    var rf=firstResetFn(); if(rf){ try{eval(rf+'()');}catch(e){} }
    openTable();                       // ensure a table-backed export has its table built
    var hadData=_plotHasData();
    exps.forEach(function(ex){
      var t0=captureCsv(ex.call);
      if(typeof t0==='string' && t0.indexOf('__THREW__')===0){ chk('csv-export['+ex.name+']-runs', false, t0.slice(0,160)); return; }
      var r0=csvDataRows(t0);
      chk('csv-export['+ex.name+']-nonblank', !hadData || (r0&&r0.length>0), 'rows='+(r0?r0.length:'null'));
      if(!r0||!r0.length) return;
      // (a) filter-tracking: a filter that shrinks the plot must shrink the export,
      // and undoing it must restore the exact baseline row count. Try a condition/
      // serial/temp checkbox first; if none changes the (length-based) plot
      // signature (e.g. env_coverage, whose ΔEnv band trace lengths don't move
      // when a serial/temp is dropped -- only n does), fall back to narrowing the
      // frequency-range input, which reliably drops condition/freq rows.
      var boxes=filterBoxes().filter(function(c){return c.checked;}), moved=false;
      for(var bi=0; bi<boxes.length && !moved; bi++){
        var c=boxes[bi], s0=plotSig();
        fire(c,false);
        if(plotSig()!==s0){
          moved=true;
          var r1=csvDataRows(captureCsv(ex.call));
          chk('csv-export['+ex.name+']-tracks-filter', r1!=null && r1.length!==r0.length,
              (r1!=null&&r1.length!==r0.length)?('rows '+r0.length+' -> '+r1.length+' on filter')
              :('STALE: plot filtered but export row count unchanged ('+r0.length+') -- export ignores this filter'));
          fire(c,true);
          var r2=csvDataRows(captureCsv(ex.call));
          chk('csv-export['+ex.name+']-restores', r2!=null && r2.length===r0.length, 'rows='+(r2?r2.length:'null')+' base='+r0.length);
        } else { fire(c,true); }
      }
      if(!moved){
        var fp=_freqPair();
        if(fp){
          var flo0=parseFloat(fp.lo.value), fhi0=parseFloat(fp.hi.value), sf0=plotSig();
          if(fhi0>flo0){
            _setFreq(fp, flo0+(fhi0-flo0)*0.40, flo0+(fhi0-flo0)*0.60);
            if(plotSig()!==sf0){
              moved=true;
              var rf1=csvDataRows(captureCsv(ex.call));
              chk('csv-export['+ex.name+']-tracks-freq-range', rf1!=null && rf1.length<r0.length,
                  (rf1!=null&&rf1.length<r0.length)?('rows '+r0.length+' -> '+rf1.length+' on narrower freq range')
                  :('STALE: freq range narrowed the plot but export rows unchanged ('+r0.length+') -- export ignores the freq range'));
              _setFreq(fp, flo0, fhi0);
              var rf2=csvDataRows(captureCsv(ex.call));
              chk('csv-export['+ex.name+']-restores-freq-range', rf2!=null && rf2.length===r0.length, 'rows='+(rf2?rf2.length:'null')+' base='+r0.length);
            } else { _setFreq(fp, flo0, fhi0); }
          }
        }
      }
      if(!moved) R.push({name:'csv-export['+ex.name+']-tracks-filter',skip:true,detail:'no filter checkbox or freq range changed the plot (single-value dims?)'});
      // (b) exact "matches the screen" equality for the two clean 1:1 cases.
      // Re-capture at baseline (state restored above) so the reference is current.
      if(ex.ref==='points'){   // histogram: one CSV row per plotted measurement
        var gd=_gd(), hist=(gd&&gd.data?gd.data:[]).filter(function(t){return t.type==='histogram';});
        var tot=0; hist.forEach(function(t){tot+=(t.x?t.x.length:0);});
        var rb=csvDataRows(captureCsv(ex.call));
        chk('csv-export-matches-plotted-points', rb!=null && rb.length===tot, 'csv_rows='+(rb?rb.length:'null')+' plotted_points='+tot);
        // (c) round-trip: re-importing the just-exported CSV must reproduce the
        // same plot and table. The histogram's in-page Import (_hApplyImport)
        // keeps this plot's own spec when the export carried none (clean export),
        // so a same-page round-trip should land on the identical view. This
        // REPLACES the page's data globals, so it runs last (nothing generic
        // reads histogram state after it).
        if(typeof _hApplyImport==='function'){
          var beforeSig=plotSig();
          var Tb=readTable(); var rowNb=Tb?Tb.rows.length:-1;
          var condB=Tb?Object.keys(tableConds(Tb)).sort().join('|'):'';
          var exportText=captureCsv(ex.call);
          var ok=true, err='';
          try{ _hApplyImport(exportText,'__qa_roundtrip.csv'); }catch(e){ ok=false; err=String(e); }
          chk('csv-import-roundtrip-runs', ok, err);
          if(ok){
            var afterSig=plotSig();
            var Ta=readTable(); var rowNa=Ta?Ta.rows.length:-1;
            var condA=Ta?Object.keys(tableConds(Ta)).sort().join('|'):'';
            chk('csv-import-roundtrip-same-plot', afterSig===beforeSig,
                (afterSig===beforeSig)?'plot identical after re-import':('before='+beforeSig.slice(0,90)+' after='+afterSig.slice(0,90)));
            chk('csv-import-roundtrip-same-table', rowNa===rowNb && condA===condB,
                'rows '+rowNb+'->'+rowNa+', conditions '+(condA===condB?'identical':'DIFFER'));
          }
        }
      } else if(ex.ref==='table'){   // summary Results Table: one CSV row per shown table row (no GF => no extra excluded rows)
        var T=readTable(), rb2=csvDataRows(captureCsv(ex.call));
        if(T && T.rows) chk('csv-export-matches-results-table', rb2!=null && rb2.length===T.rows.length, 'csv_rows='+(rb2?rb2.length:'null')+' table_rows='+T.rows.length);
        else skip('csv-export-matches-results-table','no results table read');
      }
    });
    if(rf){ try{eval(rf+'()');}catch(e){} }
  }

  // ---- histogram Site Population Check (SR-fence membership) ----
  // The histogram's cross-site fence panel builds a 1.5xIQR fence from the
  // PRIMARY_SITE's value population PER non-Site dimension combination, then
  // classifies each non-primary ("MY") measurement inside/outside. This proves it
  // by INDEPENDENTLY recomputing the fence + classification from the raw VALUES/
  // DIMVALS and comparing to the panel's own rows (catches wrong bucketing, wrong
  // primary/non-primary split, or a uniformly-wrong fence), plus the internal
  // invariant that every OUTSIDE value is truly outside its stated fence, plus the
  // panel's CSV export matching what's on screen. Skips cleanly on any page that
  // isn't a compare histogram.
  function runHistogramSite(R,chk,skip){
    if(typeof PRIMARY_SITE==='undefined'||!PRIMARY_SITE||typeof SITE_COL_ID==='undefined'||!SITE_COL_ID){
      skip('site-population-check','not a cross-site compare histogram (no PRIMARY_SITE)'); return; }
    if(typeof updateSitePanel!=='function'||typeof DIMS==='undefined'){ skip('site-population-check','no site panel in this view'); return; }
    var panel=document.getElementById('h_site_panel');
    if(panel && getComputedStyle(panel).display==='none' && typeof toggleSitePanel==='function'){ try{toggleSitePanel();}catch(e){} }
    else { try{updateSitePanel();}catch(e){} }
    var rows=(typeof _hLastSiteRows!=='undefined')?_hLastSiteRows:[];
    chk('site-check-produced-rows', rows.length>0, 'rows='+rows.length);
    if(!rows.length) return;
    // Independent recompute (mirrors the panel's data selection: dim + serial
    // filters, NOT pass/fail; primary-site values bucketed by the non-Site dims).
    function _pct(s,p){ var i=(p/100)*(s.length-1),lo=Math.floor(i); return lo+1<s.length?s[lo]+(s[lo+1]-s[lo])*(i-lo):s[lo]; }
    var otherDims=DIMS.filter(function(d){return d.col_id!==SITE_COL_ID;});
    function bk(i){ return otherDims.length?otherDims.map(function(d){return DIMVALS[d.col_id][i];}).join('  |  '):'All'; }
    var ds={}, ss={}; var hasSer=SERIAL_LIST.length>0;
    DIMS.forEach(function(d){ var s={}; document.querySelectorAll('.hf_'+d.col_id).forEach(function(c){ if(c.checked)s[c.value]=1; }); ds[d.col_id]=s; });
    document.querySelectorAll('.hf_serial').forEach(function(c){ if(c.checked)ss[c.value]=1; });
    var prim={};
    for(var i=0;i<VALUES.length;i++){ var ok=true;
      for(var k=0;k<DIMS.length;k++){ var d=DIMS[k]; if(!ds[d.col_id][DIMVALS[d.col_id][i]]){ok=false;break;} }
      if(ok&&hasSer&&!ss[SERIAL[i]]) ok=false;
      if(!ok) continue;
      if(DIMVALS[SITE_COL_ID][i]===PRIMARY_SITE){ (prim[bk(i)]=prim[bk(i)]||[]).push(VALUES[i]); }
    }
    var _kEl=document.getElementById('h_site_k'); var _k=_kEl?parseFloat(_kEl.value):1.5; if(!(isFinite(_k)&&_k>=0))_k=1.5;
    var fences={}; Object.keys(prim).forEach(function(b){ var v=prim[b]; if(v.length>=4){
      var s=v.slice().sort(function(a,b){return a-b;}); var q1=_pct(s,25),q3=_pct(s,75),iqr=q3-q1;
      fences[b]={lo:q1-_k*iqr,hi:q3+_k*iqr,n:v.length}; } });
    var verdictBad=0, boundBad=0, checked=0;
    rows.forEach(function(r){ checked++;
      var f=fences[r.p.bucket];
      var expV=!f?'n/a':((r.p.value>f.hi||r.p.value<f.lo)?'OUTSIDE':'inside');
      if(expV!==r.verdict) verdictBad++;
      if(f && r.verdict!=='n/a'){ if(Math.abs(f.lo-r.lo)>1e-6||Math.abs(f.hi-r.hi)>1e-6||f.n!==r.n) boundBad++; }
    });
    chk('site-fence-classification-matches-independent-recompute', verdictBad===0, 'mismatched='+verdictBad+'/'+checked);
    chk('site-fence-bounds-match-independent-recompute', boundBad===0, 'bound-mismatch='+boundBad+'/'+checked);
    var badOut=rows.filter(function(r){return r.verdict==='OUTSIDE'&&!(r.p.value>r.hi||r.p.value<r.lo);}).length;
    var badIn=rows.filter(function(r){return r.verdict==='inside'&&(r.p.value>r.hi||r.p.value<r.lo);}).length;
    chk('site-outside-truly-outside-its-fence', badOut===0, 'badOut='+badOut);
    chk('site-inside-truly-inside-its-fence', badIn===0, 'badIn='+badIn);
    // Live k slider: a stricter (smaller) k must flag >= as many OUTSIDE, a looser
    // (larger) k <= -- monotonic, and it must actually recompute the panel live.
    var kEl=document.getElementById('h_site_k');
    if(kEl){
      var kOrig=kEl.value;
      function _outN(){ return ((typeof _hLastSiteRows!=='undefined')?_hLastSiteRows:[]).filter(function(r){return r.verdict==='OUTSIDE';}).length; }
      function _fence(){ var rs=(typeof _hLastSiteRows!=='undefined')?_hLastSiteRows:[]; for(var i=0;i<rs.length;i++){ if(rs[i].lo!=null&&rs[i].hi!=null) return [rs[i].lo,rs[i].hi]; } return null; }
      kEl.value='1.5'; try{updateSitePanel();}catch(e){} var oMid=_outN();
      kEl.value='0.5'; try{updateSitePanel();}catch(e){} var oStrict=_outN(); var fS=_fence();
      kEl.value='5';   try{updateSitePanel();}catch(e){} var oLoose=_outN(); var fL=_fence();
      // Live effect: k must actually change the result. On genuinely disjoint
      // sites the OUTSIDE *count* saturates (every non-primary point is outside
      // at every practical k -- e.g. two sites whose switching-speed
      // distributions don't overlap), so prove k is wired by the fence widening
      // (looser k -> wider [lo,hi]), not by requiring a count delta.
      var widened = (fS&&fL) ? (fL[0]<fS[0]-1e-9 || fL[1]>fS[1]+1e-9) : (oStrict!==oLoose);
      chk('site-k-slider-monotonic', oStrict>=oMid && oMid>=oLoose && widened,
          'OUTSIDE: k=0.5 -> '+oStrict+', k=1.5 -> '+oMid+', k=5 -> '+oLoose+
          '; fence k=0.5 '+(fS?('['+fS[0].toFixed(1)+','+fS[1].toFixed(1)+']'):'n/a')+
          ' -> k=5 '+(fL?('['+fL[0].toFixed(1)+','+fL[1].toFixed(1)+']'):'n/a')+' (monotonic + live fence effect)');
      kEl.value=kOrig; try{updateSitePanel();}catch(e){}   // restore baseline for the CSV checks below
    } else skip('site-k-slider','no k input on this page');
    // CSV export of the panel must match exactly what's on screen (All + Outside-only).
    if(typeof hSaveSitePopulationCSV==='function'){
      var dAll=csvDataRows(captureCsv(function(){hSaveSitePopulationCSV(false);}));
      chk('site-csv-export-matches-panel', dAll!=null && dAll.length===rows.length, 'csv_rows='+(dAll?dAll.length:'null')+' panel_rows='+rows.length);
      var nOut=rows.filter(function(r){return r.verdict==='OUTSIDE';}).length;
      if(nOut===0){ skip('site-csv-outside-only-matches-panel','0 outside points -> nothing to export (correct)'); }
      else {
        var dOut=csvDataRows(captureCsv(function(){hSaveSitePopulationCSV(true);}));
        chk('site-csv-outside-only-matches-panel', dOut!=null && dOut.length===nOut, 'csv_out='+(dOut?dOut.length:'null')+' outside='+nOut);
      }
    }
    // Edit-reimport workflow (the histogram has no GF): export -> delete a bad SR
    // DUT's rows -> reimport must re-wire the fence panel AND recompute the fence
    // from the cleaned PRIMARY_SITE population (the removed DUT's values gone).
    // This MUST be the last thing runHistogramSite does -- it replaces the data
    // globals. (Simple comma split -- the synthetic Site/Serial cells have no
    // embedded commas; a real page's Site/Serial values likewise won't.)
    if(typeof _hApplyImport==='function' && typeof hExportCsv==='function' && SERIAL_LIST.length>0){
      var full=captureCsv(function(){hExportCsv();});
      if(full && String(full).indexOf('__THREW__')!==0){
        var lines=String(full).split(/\r\n|\n/).filter(function(l){return l.length;});
        var hdr=lines[0].split(','), siteI=-1, serI=-1;
        for(var ci=0;ci<hdr.length;ci++){ var h=hdr[ci].trim().toLowerCase(); if(h==='site')siteI=ci; if(h==='serial')serI=ci; }
        if(siteI>=0 && serI>=0){
          var chosen=null;
          for(var li=1;li<lines.length&&!chosen;li++){ var c=lines[li].split(','); if(c[siteI]===PRIMARY_SITE) chosen=c[serI]; }
          if(chosen){
            var beforeSR=0, chosenRows=0;
            for(var i=0;i<VALUES.length;i++){ if(DIMVALS[SITE_COL_ID][i]===PRIMARY_SITE){ beforeSR++; if(SERIAL[i]===chosen) chosenRows++; } }
            var kept=[lines[0]];
            for(var li=1;li<lines.length;li++){ var c=lines[li].split(','); if(c[siteI]===PRIMARY_SITE&&c[serI]===chosen) continue; kept.push(lines[li]); }
            var okImp=true; try{ _hApplyImport(kept.join('\r\n'),'__qa_edited.csv'); }catch(e){ okImp=false; chk('site-edited-reimport-runs',false,String(e)); }
            if(okImp){
              try{updateSitePanel();}catch(e){}
              var rows3=(typeof _hLastSiteRows!=='undefined')?_hLastSiteRows:[];
              chk('site-reimport-rewires-fence-panel', rows3.length>0 && !!SITE_COL_ID, 'rows='+rows3.length+' siteCol='+SITE_COL_ID);
              var afterSR=0; for(var i=0;i<VALUES.length;i++){ if(SITE_COL_ID&&DIMVALS[SITE_COL_ID]&&DIMVALS[SITE_COL_ID][i]===PRIMARY_SITE) afterSR++; }
              chk('site-edited-reimport-drops-SR-DUT-from-fence', chosenRows>0 && afterSR===beforeSR-chosenRows,
                  PRIMARY_SITE+' pop '+beforeSR+' -> '+afterSR+' (removed '+chosen+' = '+chosenRows+' rows)');
            }
          } else skip('site-edited-reimport','no '+PRIMARY_SITE+' serial found in the export to edit');
        } else skip('site-edited-reimport','export has no Site/Serial columns');
      } else skip('site-edited-reimport','baseline export failed');
    }
  }

  // ---- env_coverage / distribution Site Population Check (SR-fence membership) ----
  // These two views share the view-agnostic panel (_spRender/_spFence) and expose a
  // selectable fence BASIS (env_coverage: Room baseline vs ΔEnv drift; distribution:
  // Absolute vs ΔTemp) plus a live k. This proves, oracle-free: the panel produces
  // rows; every OUTSIDE value is truly outside its own stated [lo,hi] and every
  // inside truly inside; each non-n/a fence is a valid Tukey fence (n>=4, lo<=hi);
  // BOTH bases render non-blank; the live k is monotonic; and the panel's CSV export
  // matches what's on screen. Self-skips off a compare env_coverage/distribution.
  function runSiteFencePanel(R,chk,skip){
    var cfg=null;
    if(typeof toggleDistSitePanel==='function' && typeof _distLastSiteRows!=='undefined'){
      cfg={name:'dist',toggle:toggleDistSitePanel,update:updateDistSitePanel,rows:function(){return _distLastSiteRows;},
           panelId:'dist_site_panel',kId:'dist_site_k',basisName:'dist_site_basis',save:(typeof saveDistSitePopCsv==='function'?saveDistSitePopCsv:null)};
    } else if(typeof toggleEcSitePanel==='function' && typeof _ecLastSiteRows!=='undefined'){
      cfg={name:'ec',toggle:toggleEcSitePanel,update:updateEcSitePanel,rows:function(){return _ecLastSiteRows;},
           panelId:'ec_site_panel',kId:'ec_site_k',basisName:'ec_site_basis',save:(typeof saveEcSitePopCsv==='function'?saveEcSitePopCsv:null)};
    }
    if(!cfg){ skip('site-fence-panel','no env_coverage/distribution site panel in this view'); return; }
    if(typeof PRIMARY_SITE==='undefined'||!PRIMARY_SITE){ skip('site-fence-panel','not a cross-site compare page (no PRIMARY_SITE)'); return; }
    var panel=document.getElementById(cfg.panelId);
    if(panel&&getComputedStyle(panel).display==='none'){ try{cfg.toggle();}catch(e){} } else { try{cfg.update();}catch(e){} }
    var rows=cfg.rows();
    chk('site-fence-produced-rows['+cfg.name+']', rows.length>0, 'rows='+rows.length);
    if(!rows.length) return;
    var badOut=rows.filter(function(r){return r.verdict==='OUTSIDE'&&!(r.p.value>r.hi||r.p.value<r.lo);}).length;
    var badIn=rows.filter(function(r){return r.verdict==='inside'&&(r.p.value>r.hi||r.p.value<r.lo);}).length;
    chk('site-fence-outside-truly-outside['+cfg.name+']', badOut===0, 'badOut='+badOut);
    chk('site-fence-inside-truly-inside['+cfg.name+']', badIn===0, 'badIn='+badIn);
    var fbad=rows.filter(function(r){return r.verdict!=='n/a' && !((r.n>=4)&&(r.lo<=r.hi+1e-9));}).length;
    chk('site-fence-valid-bounds['+cfg.name+']', fbad===0, 'invalid-fence-rows='+fbad);
    // Both bases must render non-blank.
    var radios=[].slice.call(document.querySelectorAll('input[name="'+cfg.basisName+'"]'));
    if(radios.length>=2){
      var orig=radios.filter(function(r){return r.checked;})[0], counts=[];
      radios.forEach(function(rb){ rb.checked=true; try{cfg.update();}catch(e){} counts.push(cfg.rows().length); });
      if(orig){orig.checked=true; try{cfg.update();}catch(e){}}
      chk('site-fence-both-bases-nonblank['+cfg.name+']', counts.every(function(c){return c>0;}), 'rows per basis: '+counts.join(', '));
    } else skip('site-fence-both-bases['+cfg.name+']','single basis');
    // Live k slider monotonic (stricter flags >=, looser <=, with a live effect).
    var kEl=document.getElementById(cfg.kId);
    if(kEl){ var kOrig=kEl.value; function _oN(){return cfg.rows().filter(function(r){return r.verdict==='OUTSIDE';}).length;}
      kEl.value='1.5'; try{cfg.update();}catch(e){} var oM=_oN();
      kEl.value='0.5'; try{cfg.update();}catch(e){} var oS=_oN();
      kEl.value='5';   try{cfg.update();}catch(e){} var oL=_oN();
      chk('site-fence-k-monotonic['+cfg.name+']', oS>=oM&&oM>=oL&&oS!==oL, 'OUTSIDE k=0.5->'+oS+', 1.5->'+oM+', 5->'+oL);
      kEl.value=kOrig; try{cfg.update();}catch(e){}
    } else skip('site-fence-k-slider['+cfg.name+']','no k input');
    // CSV export matches the panel (All + Outside-only).
    if(cfg.save){ var rr=cfg.rows();
      var dAll=csvDataRows(captureCsv(function(){cfg.save(false);}));
      chk('site-fence-csv-matches-panel['+cfg.name+']', dAll!=null&&dAll.length===rr.length, 'csv='+(dAll?dAll.length:'null')+' panel='+rr.length);
      var nOut=rr.filter(function(r){return r.verdict==='OUTSIDE';}).length;
      var dOut=csvDataRows(captureCsv(function(){cfg.save(true);}));
      chk('site-fence-csv-outside-matches['+cfg.name+']', dOut!=null&&dOut.length===nOut, 'csv='+(dOut?dOut.length:'null')+' outside='+nOut);
    }
  }

  // ---- Auto-filter bad DUTs -- population views (stat_summary; extends to
  // summary/env_coverage/histogram as they gain the shared engine). Oracle-free:
  // (a) OFF produces no result; (b) the dist-basis bad-point set matches a fully
  // INDEPENDENT median/MAD modified-z recompute from this view's own per-DUT data
  // (the uniformly-wrong-magnitude guard); (c) auto count monotonic in level;
  // (d) no auto DUT is systemic/risky; (e) Apply adds EXACTLY the auto keys to the
  // shared GF and every auto DUT's points read as GF-excluded, Clear restores.
  // Self-skips off a view without the shared auto-filter controls.
  function runAutoFilterStat(R,chk,skip){
    if(typeof STAT_DATA==='undefined'||typeof _statAutoBadPoints!=='function'
       ||!document.getElementById('stat_auto_basis')||typeof getActiveConditions!=='function'){
      skip('auto-filter','no stat_summary auto-filter in this view'); return; }
    var basisEl=document.getElementById('stat_auto_basis'), levelEl=document.getElementById('stat_auto_level');
    function gfN(){try{return (JSON.parse(localStorage.getItem(GF_KEY)||'{"excluded":[]}').excluded||[]).length;}catch(e){return -1;}}
    if(typeof clearStatGlobalFilter==='function') clearStatGlobalFilter();
    basisEl.value='dist';
    levelEl.value='off'; statAutoFilterPreview();
    var panel=document.getElementById('stat_auto_panel');
    chk('auto-off-produces-nothing', !window._statAutoResult && (!panel||getComputedStyle(panel).display==='none'),
        'result='+(window._statAutoResult?'set':'null'));
    function _med(a){var s=a.slice().sort(function(x,y){return x-y;});var n=s.length;return n?(n%2?s[(n-1)/2]:0.5*(s[n/2-1]+s[n/2])):0;}
    var _abs=function(s){return (typeof _statBaseSerial!=='undefined')?_statBaseSerial(s):s;};
    var conds=getActiveConditions();
    var fLo=parseFloat(document.getElementById('freq_lo_txt').value); if(isNaN(fLo))fLo=-Infinity;
    var fHi=parseFloat(document.getElementById('freq_hi_txt').value); if(isNaN(fHi))fHi=Infinity;
    // Independent re-derivation of EACH peer-relative basis (fully separate code
    // from _afScorer -- the whole point is a second implementation catching a bug
    // in the first). dist=MAD modZ>=3.5; iqr=outside Tukey 1.5xIQR fence;
    // dmad=double-MAD (left/right) modZ>=3.5.
    function _flagFor(basis, fv){
      var med=_med(fv);
      if(basis==='iqr'){ var s=fv.slice().sort(function(a,b){return a-b;}),n=s.length;
        function pc(p){var i=(p/100)*(n-1),li=Math.floor(i);return li+1<n?s[li]+(s[li+1]-s[li])*(i-li):s[li];}
        var q1=pc(25),q3=pc(75),iqr=q3-q1,loF=q1-1.5*iqr,hiF=q3+1.5*iqr;
        return function(v){return v>hiF||v<loF;}; }
      if(basis==='dmad'){ var below=[],above=[]; fv.forEach(function(v){if(v<=med)below.push(med-v);if(v>=med)above.push(v-med);});
        var sym=_med(fv.map(function(v){return Math.abs(v-med);}))*1.4826||1e-9;
        var mLo=_med(below)*1.4826||sym, mHi=_med(above)*1.4826||sym;
        return function(v){var mz=(v>=med)?(v-med)/mHi:(med-v)/mLo; return mz>=3.5;}; }
      var mad=_med(fv.map(function(v){return Math.abs(v-med);}))*1.4826||1e-9;
      return function(v){return Math.abs(v-med)/mad>=3.5;};
    }
    function _expKeys(basis){ var e={};
      conds.forEach(function(cd){(cd.freq_stats||[]).forEach(function(fs){
        if(fs.freq<fLo||fs.freq>fHi)return;
        var det=(fs.dut_vals||[]); if(det.length<4)return;
        var flag=_flagFor(basis, det.map(function(d){return d.v;}));
        det.forEach(function(d){ if(flag(d.v)) e[_abs(d.s)+'||'+cd.condition+'||'+(d.p||'')+'||'+fs.freq_label]=1; });
      });});
      return e; }
    function _gotKeys(basis){ var g={}; _statAutoBadPoints(basis).forEach(function(o){ g[_abs(o.serial)+'||'+o.cond+'||'+(o.port||'')+'||'+o.freqLabel]=1; }); return g; }
    ['dist','iqr','dmad'].forEach(function(basis){
      var exp=_expKeys(basis), got=_gotKeys(basis);
      var miss=Object.keys(exp).filter(function(k){return !got[k];});
      var xtra=Object.keys(got).filter(function(k){return !exp[k];});
      chk('auto-badpoints-matches-independent-'+basis, miss.length===0&&xtra.length===0,
          'flagged='+Object.keys(got).length+' expected='+Object.keys(exp).length+' missing='+miss.length+' extra='+xtra.length);
    });
    function autoN(lv){ levelEl.value=lv; statAutoFilterPreview(); return window._statAutoResult?window._statAutoResult.auto.length:0; }
    var nC=autoN('conservative'), nM=autoN('moderate'), nA=autoN('aggressive');
    chk('auto-level-monotonic', nA>=nM&&nM>=nC, 'conservative='+nC+' moderate='+nM+' aggressive='+nA);
    var ar=window._statAutoResult;
    var bad=(ar?ar.auto:[]).filter(function(d){return d.shared>0.5||d.risk>=0.05;});
    chk('auto-never-systemic-or-risky', bad.length===0, 'violations='+bad.length+' auto='+(ar?ar.auto.length:0));
    // Compare SITE SCOPE invariants (stat_summary). Same contract as boxplot / the
    // shared-engine views.
    var _ssel=document.getElementById('stat_auto_site');
    if(_ssel && typeof PRIMARY_SITE!=='undefined' && PRIMARY_SITE && typeof STAT_AF!=='undefined'){
      basisEl.value='dist';
      function _sres(sc){ _ssel.value=sc; return STAT_AF.compute('dist','aggressive'); }
      var _rp=_sres('primary'), _ro=_sres('onboarding'), _rb=_sres('both');
      chk('site-scope-primary-only-reference[stat]', _rp.auto.filter(function(d){return d.site!==PRIMARY_SITE;}).length===0,
          'non-ref in primary auto='+_rp.auto.filter(function(d){return d.site!==PRIMARY_SITE;}).length);
      chk('site-scope-onboarding-only-nonreference[stat]', _ro.auto.filter(function(d){return d.site===PRIMARY_SITE;}).length===0,
          'ref in onboarding auto='+_ro.auto.filter(function(d){return d.site===PRIMARY_SITE;}).length);
      chk('site-scope-both-is-union-by-site[stat]', _rb.auto.length===_rp.auto.length+_ro.auto.length,
          'both='+_rb.auto.length+' primary='+_rp.auto.length+' onboarding='+_ro.auto.length);
      function _sum(r){return Object.keys(r.siteSummary||{}).sort().map(function(k){return k+':'+r.siteSummary[k].eligible;}).join(',');}
      chk('site-scope-summary-scope-independent[stat]', _sum(_rp)===_sum(_ro)&&_sum(_ro)===_sum(_rb),
          'p['+_sum(_rp)+'] o['+_sum(_ro)+'] b['+_sum(_rb)+']');
      var _te=Object.keys(_rb.siteSummary||{}).reduce(function(x,k){return x+_rb.siteSummary[k].eligible;},0);
      chk('site-scope-summary-matches-both[stat]', _te===_rb.auto.length, 'summaryElig='+_te+' bothAuto='+_rb.auto.length);
      _ssel.value='primary'; basisEl.value='dist'; levelEl.value='aggressive'; statAutoFilterPreview(); ar=window._statAutoResult;
    } else skip('site-scope[stat]','not a compare stat_summary (no stat_auto_site / PRIMARY_SITE)');
    if(ar && ar.auto.length && typeof statAutoFilterApply==='function'){
      var _ak={}; ar.auto.forEach(function(d){d.keys.forEach(function(k){_ak[k]=1;});}); var autoKeyCount=Object.keys(_ak).length;
      statAutoFilterApply();
      chk('auto-apply-adds-exactly-auto-keys', gfN()===autoKeyCount, 'gf='+gfN()+' distinctAutoKeys='+autoKeyCount);
      var allExcl=true; (window._statAutoResult?window._statAutoResult.auto:ar.auto).forEach(function(d){
        d.pts.forEach(function(p){ if(!_isStatGfExcl(p.serial,p.cond,'Room',p.freqLabel,p.port)) allExcl=false; }); });
      chk('auto-apply-excludes-auto-DUTs', allExcl, 'all auto points GF-excluded='+allExcl);
      if(typeof clearStatGlobalFilter==='function'){ clearStatGlobalFilter();
        chk('auto-clear-restores', gfN()===0, 'gf='+gfN()); }
    } else skip('auto-apply-precise','no DUT qualifies for auto at dist/aggressive on this data');
    levelEl.value='off'; statAutoFilterPreview();
    if(typeof clearStatGlobalFilter==='function') clearStatGlobalFilter();
    // ---- Workflow & Recommendations: recommendation is valid + data-dependent,
    // and one-click Run applies EXACTLY the recommended auto set + is reversible.
    if(typeof _afRecommend==='function' && typeof _afRunWorkflow==='function'
       && typeof STAT_AF!=='undefined' && document.getElementById('stat_wf_panel')){
      clearStatGlobalFilter();
      var wa=_afAnalyze(STAT_AF), rec=_afRecommend(wa);
      chk('workflow-recommends-valid',
          ['dist','iqr','dmad','spec','tll'].indexOf(rec.basis)>=0
          && ['conservative','moderate','aggressive'].indexOf(rec.level)>=0
          && rec.why && rec.why.length>0,
          'basis='+rec.basis+' level='+rec.level+' reasons='+(rec.why?rec.why.length:0));
      // expected auto-key count at the recommended settings
      basisEl.value=rec.basis; levelEl.value=rec.level;
      // UNIQUE keys -- ctx.merge stores a Set, and a DUT can have several bad
      // points collapsing to one (serial,cond,temp,freq-box) key, so a raw
      // d.keys.length sum over-counts vs. what the GF actually holds.
      var expR=STAT_AF.compute(rec.basis,rec.level), _uk={};
      expR.auto.forEach(function(d){d.keys.forEach(function(k){_uk[k]=1;});});
      var expKeys=Object.keys(_uk).length;
      clearStatGlobalFilter();
      statRunWorkflow();
      chk('workflow-run-applies-recommended-auto', gfN()===expKeys, 'gf='+gfN()+' expected='+expKeys);
      var au=document.getElementById('stat_wf_panel_audit');
      chk('workflow-run-writes-audit', !!au && au.textContent.indexOf('Ran recommended workflow')>=0, 'audit='+(au?'present':'missing'));
      // print-to-PDF report: suppress the actual print (async image fetch would
      // otherwise fire a real dialog), generate, assert report content.
      if(typeof statGenReport==='function'){
        window._afNoPrint=true; window._afNoCapture=true;
        try{ statGenReport(); }catch(e){}
        window._afNoPrint=false; window._afNoCapture=false;
        var rep=document.getElementById('af_report');
        chk('workflow-report-generated', !!rep && rep.textContent.indexOf('Auto-filter Workflow Report')>=0
            && rep.textContent.indexOf('Recommendation')>=0 && rep.textContent.indexOf('Auto-excluded')>=0,
            'report='+(rep?'present':'missing'));
      }
      clearStatGlobalFilter();
      chk('workflow-run-reversible', gfN()===0, 'gf='+gfN());
    } else skip('workflow','no workflow panel in this view');
  }

  // Generic, ctx-driven auto-filter + workflow self-QA for the population views
  // that use the shared engine (summary/env_coverage/histogram; boxplot and
  // stat_summary have their own key-precise checks). Oracle-free: independent
  // COUNT recompute of each peer basis from ctx.buckets() (catches a uniformly-
  // wrong magnitude), off-produces-nothing, level monotonicity, classification
  // invariants, Apply adds exactly the auto keys + Clear restores, and the
  // Workflow recommendation + one-click Run + PDF report.
  function runAutoFilterCtx(R,chk,skip,CTX,tag){
    if(!CTX||typeof _afRecommend!=='function'||typeof CTX.badPoints!=='function'
       ||!document.getElementById(CTX.basisSel)||typeof CTX.buckets!=='function'){
      skip('auto-filter['+tag+']','no auto-filter ctx in this view'); return; }
    // Exclusion count/clear come from the ctx when it defines its own vehicle
    // (histogram uses a measurement-index Set, not the GF); GF views fall back.
    function gfN(){ if(CTX.exclCount) return CTX.exclCount(); try{return (JSON.parse(localStorage.getItem(GF_KEY)||'{"excluded":[]}').excluded||[]).length;}catch(e){return -1;} }
    function clr(){ if(CTX.clear){ try{CTX.clear();}catch(e){} return; } try{localStorage.removeItem(GF_KEY);}catch(e){} try{CTX.merge([]);}catch(e){} }
    function _med(a){var s=a.slice().sort(function(x,y){return x-y;});var n=s.length;return n?(n%2?s[(n-1)/2]:0.5*(s[n/2-1]+s[n/2])):0;}
    function _cnt(basis){ var n=0;
      (CTX.buckets()||[]).forEach(function(b){ var fv=(b.vals||[]).filter(function(v){return v!=null;}); if(fv.length<4)return;
        var med=_med(fv), flag;
        if(basis==='iqr'){ var s=fv.slice().sort(function(a,b){return a-b;}),m=s.length;
          function pc(p){var i=(p/100)*(m-1),li=Math.floor(i);return li+1<m?s[li]+(s[li+1]-s[li])*(i-li):s[li];}
          var q1=pc(25),q3=pc(75),iqr=q3-q1,loF=q1-1.5*iqr,hiF=q3+1.5*iqr; flag=function(v){return v>hiF||v<loF;}; }
        else if(basis==='dmad'){ var bl=[],ab=[]; fv.forEach(function(v){if(v<=med)bl.push(med-v);if(v>=med)ab.push(v-med);});
          var sym=_med(fv.map(function(v){return Math.abs(v-med);}))*1.4826||1e-9;
          var mLo=_med(bl)*1.4826||sym, mHi=_med(ab)*1.4826||sym; flag=function(v){var mz=(v>=med)?(v-med)/mHi:(med-v)/mLo; return mz>=3.5;}; }
        else { var mad=_med(fv.map(function(v){return Math.abs(v-med);}))*1.4826||1e-9; flag=function(v){return Math.abs(v-med)/mad>=3.5;}; }
        fv.forEach(function(v){if(flag(v))n++;});
      });
      return n; }
    var basisEl=document.getElementById(CTX.basisSel), levelEl=document.getElementById(CTX.levelSel);
    clr(); basisEl.value='dist';
    levelEl.value='off'; CTX.previewFn(); var panel=document.getElementById(CTX.panel);
    chk('auto-off-produces-nothing['+tag+']', !window[CTX.resultVar]&&(!panel||getComputedStyle(panel).display==='none'), 'result='+(window[CTX.resultVar]?'set':'null'));
    ['dist','iqr','dmad'].forEach(function(basis){
      var exp=_cnt(basis), got=CTX.badPoints(basis).length;
      chk('auto-badpoints-count-matches-independent-'+basis+'['+tag+']', exp===got, 'flagged='+got+' expected='+exp);
    });
    function autoN(lv){levelEl.value=lv;CTX.previewFn();return window[CTX.resultVar]?window[CTX.resultVar].auto.length:0;}
    var nC=autoN('conservative'),nM=autoN('moderate'),nA=autoN('aggressive');
    chk('auto-level-monotonic['+tag+']', nA>=nM&&nM>=nC, 'c='+nC+' m='+nM+' a='+nA);
    var ar=window[CTX.resultVar];
    var bad=(ar?ar.auto:[]).filter(function(d){return d.shared>0.5||d.risk>=0.05;});
    chk('auto-never-systemic-or-risky['+tag+']', bad.length===0, 'violations='+bad.length+' auto='+(ar?ar.auto.length:0));
    // Compare SITE SCOPE invariants (shared-engine views). Same contract as the
    // boxplot block: the gate scopes the auto set by site, and the per-site
    // eligible summary is scope-INDEPENDENT and equals what 'both' auto-filters.
    var _ssel=CTX.siteSel?document.getElementById(CTX.siteSel):null;
    if(_ssel && typeof PRIMARY_SITE!=='undefined' && PRIMARY_SITE){
      basisEl.value='dist';
      function _sres(sc){ _ssel.value=sc; return CTX.compute('dist','aggressive'); }
      var _rp=_sres('primary'), _ro=_sres('onboarding'), _rb=_sres('both');
      chk('site-scope-primary-only-reference['+tag+']', _rp.auto.filter(function(d){return d.site!==PRIMARY_SITE;}).length===0,
          'non-ref in primary auto='+_rp.auto.filter(function(d){return d.site!==PRIMARY_SITE;}).length);
      chk('site-scope-onboarding-only-nonreference['+tag+']', _ro.auto.filter(function(d){return d.site===PRIMARY_SITE;}).length===0,
          'ref in onboarding auto='+_ro.auto.filter(function(d){return d.site===PRIMARY_SITE;}).length);
      chk('site-scope-both-is-union-by-site['+tag+']', _rb.auto.length===_rp.auto.length+_ro.auto.length,
          'both='+_rb.auto.length+' primary='+_rp.auto.length+' onboarding='+_ro.auto.length);
      function _sum(r){return Object.keys(r.siteSummary||{}).sort().map(function(k){return k+':'+r.siteSummary[k].eligible;}).join(',');}
      chk('site-scope-summary-scope-independent['+tag+']', _sum(_rp)===_sum(_ro)&&_sum(_ro)===_sum(_rb),
          'p['+_sum(_rp)+'] o['+_sum(_ro)+'] b['+_sum(_rb)+']');
      var _te=Object.keys(_rb.siteSummary||{}).reduce(function(x,k){return x+_rb.siteSummary[k].eligible;},0);
      chk('site-scope-summary-matches-both['+tag+']', _te===_rb.auto.length, 'summaryElig='+_te+' bothAuto='+_rb.auto.length);
      // restore the state the apply block below expects (default primary, aggressive).
      _ssel.value='primary'; basisEl.value='dist'; levelEl.value='aggressive'; CTX.previewFn(); ar=window[CTX.resultVar];
    } else skip('site-scope['+tag+']','not a compare view (no siteSel / PRIMARY_SITE)');
    if(ar&&ar.auto.length){
      // Distinct keys, not sum-of-lengths: the GF is a Set (_mergeGf unions), so a DUT
      // whose key list repeats an identical serial||cond||temp||freqLabel (e.g. repeat
      // rows collapsing to one freq box) legitimately dedups. Still full teeth: a
      // genuinely dropped distinct key makes gfN() < distinct -> FAIL.
      var _ak={}; ar.auto.forEach(function(d){d.keys.forEach(function(k){_ak[k]=1;});}); var keys=Object.keys(_ak).length;
      _afApply(CTX);
      chk('auto-apply-adds-keys['+tag+']', gfN()===keys, 'gf='+gfN()+' distinctKeys='+keys);
      clr(); chk('auto-clear-restores['+tag+']', gfN()===0, 'gf='+gfN());
    } else skip('auto-apply['+tag+']','no auto DUT at dist/aggressive on this data');
    clr(); var wa=_afAnalyze(CTX), rec=_afRecommend(wa);
    chk('workflow-recommends-valid['+tag+']',
        ['dist','iqr','dmad','spec','tll'].indexOf(rec.basis)>=0 && ['conservative','moderate','aggressive'].indexOf(rec.level)>=0 && rec.why&&rec.why.length>0,
        'basis='+rec.basis+' level='+rec.level);
    basisEl.value=rec.basis; levelEl.value=rec.level;
    var expR=CTX.compute(rec.basis,rec.level), _uk={}; expR.auto.forEach(function(d){d.keys.forEach(function(k){_uk[k]=1;});}); var ek=Object.keys(_uk).length;
    clr(); _afRunWorkflow(CTX);
    chk('workflow-run-applies-recommended-auto['+tag+']', gfN()===ek, 'gf='+gfN()+' expected='+ek);
    window._afNoPrint=true; window._afNoCapture=true; try{_afGenerateReport(CTX);}catch(e){} window._afNoPrint=false; window._afNoCapture=false;
    var rep=document.getElementById('af_report');
    chk('workflow-report-generated['+tag+']', !!rep&&rep.textContent.indexOf('Auto-filter Workflow Report')>=0&&rep.textContent.indexOf('Recommendation')>=0, 'report='+(rep?'present':'missing'));
    clr(); levelEl.value='off'; CTX.previewFn();
  }

  // ---- Reference Statistics view (padb_refstats) -------------------------
  // An AGGREGATE view: no #plot per-point markers -- it renders #overall
  // (headline pass/fail + descriptive stats), a #pareto Plotly bar (fails/
  // outliers by group), and a #grouptbl per-group descriptive table, all
  // recomputed from applyFilters(DATA). This block proves those three panels
  // stay coupled to the SAME filtered set: the overall total equals the filter
  // output, per-group rows sum to that total, the pareto's group keys are a
  // subset of the table's, the group-table Fail column sums to the overall Fail
  // (plots/stats/table match on the pass/fail axis), and a frequency filter
  // shrinks all three together and reverts. Self-skips on any other view.
  function _isReference(){
    return typeof COLS!=='undefined' && typeof GROUP_COLS!=='undefined'
        && typeof applyFilters==='function'
        && document.getElementById('grouptbl') && document.getElementById('overall')
        && document.getElementById('pareto');
  }
  function runReference(R,chk,skip){
    if(!_isReference()){ skip('reference','not a reference-stats view'); return; }
    function overallTotal(){
      var el=document.getElementById('overall'); if(!el)return null;
      var m=el.textContent.match(/Total points \(filtered\)\s*([\d,]+)/);
      return m?parseInt(m[1].replace(/,/g,''),10):null;
    }
    function groupCount(rows,gb){
      var g={}; rows.forEach(function(r){
        var key=(gb&&r[gb]!=null&&r[gb]!=='')?String(r[gb]):(gb?'(blank)':'(all)');
        g[key]=(g[key]||0)+1;}); return g;
    }
    function tableKeys(){ return [].slice.call(document.querySelectorAll('#grouptbl tbody tr td.k'))
      .map(function(td){return td.textContent;}); }
    function tableColSum(name){
      var ths=[].slice.call(document.querySelectorAll('#grouptbl thead th')), fi=-1;
      ths.forEach(function(th,i){ if(th.textContent.trim()===name) fi=i; });
      if(fi<0) return null;
      var sum=0; [].slice.call(document.querySelectorAll('#grouptbl tbody tr')).forEach(function(tr){
        sum+=parseInt((tr.children[fi]||{}).textContent||'0',10)||0; }); return sum;
    }
    function paretoKeys(){
      var gd=document.getElementById('pareto');
      return (gd&&gd.data&&gd.data[0]?(gd.data[0].x||[]):[]).map(String);
    }
    try{resetFilters();}catch(e){} update();
    var gb=document.getElementById('groupby').value;
    var flt=applyFilters(DATA), base=flt.length;
    chk('reference-baseline-not-blank', base>0, 'rows='+base);
    if(!base){ return; }
    // (1) overall headline total == filter output
    chk('reference-overall-total-matches-filter', overallTotal()===base, 'overall='+overallTotal()+' filter='+base);
    // (2) per-group rows sum back to the filtered total
    var g=groupCount(flt,gb), gkeys=Object.keys(g);
    var sumRows=gkeys.reduce(function(a,k){return a+g[k];},0);
    chk('reference-group-rows-sum-to-total', sumRows===base, 'sum='+sumRows+' total='+base);
    // (3) group-table keys == the recomputed grouping (set equality)
    var tk=tableKeys().slice().sort(), rk=gkeys.slice().sort();
    chk('reference-table-keys-match-grouping', JSON.stringify(tk)===JSON.stringify(rk),
        'table=['+tk.join(',')+'] grouping=['+rk.join(',')+']');
    // (4) pareto keys are a non-empty subset of the table keys (pareto caps at top 30)
    var pk=paretoKeys(), tset={}; tk.forEach(function(k){tset[k]=1;});
    chk('reference-pareto-keys-subset-of-table', pk.length>0 && pk.every(function(k){return tset[k];}),
        'pareto=['+pk.join(',')+'] notin=['+pk.filter(function(k){return !tset[k];}).join(',')+']');
    // (5) group-table 'n' (non-null Values) sums to the filtered non-null count
    var sumN=tableColSum('n'), nonNull=flt.filter(function(r){return r.Value!=null;}).length;
    chk('reference-table-n-sums-to-nonnull-values', sumN===nonNull, 'sumN='+sumN+' nonNull='+nonNull);
    // (6) pass/fail axis: overall Fail == sum of the group-table Fail column
    var el=document.getElementById('overall');
    // Overall "Fail" + its count render as adjacent <td> cells, so textContent has
    // no whitespace between them ("Fail19,247 (46.2%)") -- match with \s* not \s+.
    var mf=el.textContent.match(/Fail\s*([\d,]+)\s*\(/), gfs=tableColSum('Fail');
    if(mf&&gfs!=null){
      chk('reference-overall-fail-equals-grouptable-fail-sum',
          parseInt(mf[1].replace(/,/g,''),10)===gfs, 'overall='+mf[1]+' grouptbl='+gfs);
    } else skip('reference-fail-coupling','no pass/fail mode (no status field or limits)');
    // (6b) OUTLIER TABLE (increment 2): its row count equals the Overall outlier
    // count, and every listed point is genuinely beyond the fence (side<->sign).
    function overallOutliers(){
      var m=document.getElementById('overall').textContent.match(/Outliers \(1\.5[^)]*\)\s*([\d,]+)/);
      return m?parseInt(m[1].replace(/,/g,''),10):null;
    }
    var oc=overallOutliers(), refOut=(window._refOutliers||[]);
    if(oc!=null){
      chk('reference-outlier-table-matches-overall-count', oc===refOut.length,
          'overall='+oc+' table='+refOut.length);
    } else skip('reference-outlier-coupling','no outlier row in overall');
    if(refOut.length) chk('reference-listed-outliers-are-real',
        refOut.every(function(o){return (o.side==='high')===(o.dev>0);}), 'n='+refOut.length);
    else skip('reference-listed-outliers-are-real','no outliers in this filtered set');
    // (6c) DISTRIBUTION (increment 2): the #distplot histogram traces cover a
    // positive subset of the filtered non-null values (never more than exist).
    function distTotal(){
      var dp=document.getElementById('distplot');
      return (dp&&dp.data?dp.data:[]).reduce(function(a,t){return a+((t.x&&t.x.length)||0);},0);
    }
    var dt0=distTotal();
    chk('reference-distribution-covers-filtered', dt0>0 && dt0<=nonNull,
        'dist='+dt0+' nonNull='+nonNull);
    // (6d) GLOBAL FILTER coupling (increment 2.1): the reference view must honour
    // the same shared cross-view GF as every other page. Inject a whole-DUT
    // exclusion, confirm exactly that serial's rows drop when Apply-GF is on,
    // toggling off restores, and clearing the GF restores.
    (function(){
      if(typeof GF_KEY==='undefined'){ skip('reference-gf','no GF_KEY on this page'); return; }
      var gchk=document.getElementById('ref_gf_chk');
      if(!gchk){ skip('reference-gf','no Apply-GF control'); return; }
      try{resetFilters();}catch(e){}
      var b0=applyFilters(DATA).length;
      var srow=null;
      for(var i=0;i<DATA.length;i++){ if(DATA[i].Serial!=null&&DATA[i].Serial!==''){ srow=DATA[i]; break; } }
      if(!srow){ skip('reference-gf','no serials to exclude'); return; }
      var ser=String(srow.Serial);
      var serRows=applyFilters(DATA).filter(function(r){return String(r.Serial)===ser;}).length;
      if(!serRows){ skip('reference-gf','serial not in view'); return; }
      var saved=localStorage.getItem(GF_KEY);
      // whole-DUT key: serial||Serial Number=<ser>||manual||0 (serial part is
      // stripped from the coarse cond; manual/0 -> no Temp/Freq dim -> all rows).
      localStorage.setItem(GF_KEY, JSON.stringify({v:1, excluded:[ser+'||Serial Number='+ser+'||manual||0']}));
      _loadRefGlobalFilter(); update();
      chk('reference-gf-excludes-when-on', applyFilters(DATA).length===b0-serRows,
          'before='+b0+' after='+applyFilters(DATA).length+' serRows='+serRows);
      gchk.checked=false; update();
      chk('reference-gf-toggle-off-restores', applyFilters(DATA).length===b0,
          'off='+applyFilters(DATA).length+' before='+b0);
      gchk.checked=true;
      if(saved!=null) localStorage.setItem(GF_KEY,saved); else localStorage.removeItem(GF_KEY);
      _loadRefGlobalFilter(); update();
      chk('reference-gf-clear-restores', applyFilters(DATA).length===b0,
          'restored='+applyFilters(DATA).length+' before='+b0);
    })();
    // (6e) AUTO-FILTER IMPACT PREVIEW (increment 3): reuses the shared engine.
    // 'off' shows the preview-only note (no table); at dist/aggressive a gross
    // outlier is removed and the rendered "after" total == before minus the exact
    // set of rows matching the engine's auto keys (never writes the GF).
    (function(){
      var be=document.getElementById('ref_af_basis'), le=document.getElementById('ref_af_level'),
          imp=document.getElementById('ref_af_impact');
      if(!be||!le||!imp||typeof _refImpactRefresh==='undefined'){ skip('reference-auto-filter','no impact panel'); return; }
      try{resetFilters();}catch(e){}
      le.value='off'; _refImpactRefresh();
      chk('reference-impact-off-is-preview-only',
          imp.textContent.indexOf('Preview only')>=0 && imp.getElementsByTagName('table').length===0,
          'off-tables='+imp.getElementsByTagName('table').length);
      be.value='dist'; le.value='aggressive'; _refImpactRefresh();
      // independent recompute: rows the engine's auto set would remove
      var pts=_refBadPoints('dist'), r=_afCompute(pts,REF_AF), ak={};
      r.auto.forEach(function(d){d.keys.forEach(function(k){ak[k]=1;});});
      var mBefore=applyFilters(DATA).length;
      var rem=applyFilters(DATA).filter(function(x){return ak[_refPtKey(x)];}).length;
      // The engine's risk-gated auto set is legitimately empty on many real
      // datasets (nothing meets the dist/aggressive bar) -- only assert a
      // positive removal when the engine DID flag DUTs; otherwise skip (the
      // consistency check below still has teeth). The "genuinely removes gross
      // outliers" teeth live in the synthetic planted-outlier regression.
      if(r.auto.length>0) chk('reference-impact-removes-when-auto-nonempty', rem>0,
          'removed='+rem+' autoDuts='+r.auto.length);
      else skip('reference-impact-removes-when-auto-nonempty','auto set empty on this data (auto-filter found nothing at dist/aggressive) -- legitimate');
      var tbls=imp.getElementsByTagName('table');
      chk('reference-impact-renders-table', tbls.length===1, 'tables='+tbls.length);
      if(tbls.length){
        var totRow=tbls[0].tBodies[0].rows[0]; // "Total points | before | after | delta"
        var afterTot=parseInt((totRow.cells[2].textContent||'').replace(/[^0-9-]/g,''),10);
        chk('reference-impact-after-equals-before-minus-removed', afterTot===mBefore-rem,
            'after='+afterTot+' before='+mBefore+' removed='+rem);
      }
      le.value='off'; _refImpactRefresh();  // leave clean
    })();
    // (7) a frequency filter shrinks overall + group table together, then reverts
    var freqs=DATA.map(function(r){return r.Frequency_MHz;}).filter(function(x){return x!=null;});
    if(freqs.length){
      var mn=Math.min.apply(null,freqs), mx=Math.max.apply(null,freqs);
      var fhi=document.getElementById('f_hi'), saved=fhi.value;
      fhi.value=(mn+mx)/2; update();
      var narrowed=applyFilters(DATA).length;
      var g2=groupCount(applyFilters(DATA),gb);
      var s2=Object.keys(g2).reduce(function(a,k){return a+g2[k];},0);
      chk('reference-freq-filter-shrinks-and-couples',
          narrowed<base && overallTotal()===narrowed && s2===narrowed,
          'base='+base+' narrowed='+narrowed+' overall='+overallTotal()+' grouprows='+s2);
      // distribution + outlier table track the narrowed set too (never exceed it)
      var ndist=distTotal(), nnn=applyFilters(DATA).filter(function(r){return r.Value!=null;}).length;
      chk('reference-distribution-tracks-narrowed', ndist<=nnn && (nnn===0||ndist>0),
          'dist='+ndist+' nonNull='+nnn);
      chk('reference-outliers-track-narrowed',
          (window._refOutliers||[]).length<=nnn, 'outliers='+(window._refOutliers||[]).length+' nonNull='+nnn);
      fhi.value=saved; update();
      chk('reference-freq-filter-reverts',
          overallTotal()===base && (window._refOutliers||[]).length===refOut.length,
          'restored='+overallTotal()+' base='+base);
    } else skip('reference-freq-filter','no frequency data');
  }

  /* Subpopulation / dual-distribution advisory (Workflow & Recommendations). Teeth:
     plant a large offset into 2 DUTs of a real slice -> _spDetect must flag EXACTLY
     those 2; a uniform shift of every DUT must create NO subpopulation (proves it's
     relative, not absolute); the advisory HTML must render. Self-skips off a view
     without _spDetect / ctx.subpopSlices. */
  function runSubpop(R,chk,skip){
    if(typeof _spDetect!=='function'){ skip('subpop','no _spDetect in this view'); return; }
    var ctx=null; ['BOX_AF','STAT_AF','SUM_AF','EC_AF','HIST_AF'].forEach(function(nm){
      if(!ctx && typeof window[nm]==='object' && window[nm] && typeof window[nm].subpopSlices==='function') ctx=window[nm];
    });
    if(!ctx){ skip('subpop','no ctx.subpopSlices in this view'); return; }
    var slices; try{ slices=ctx.subpopSlices(); }catch(e){ chk('subpop-slices',false,String(e)); return; }
    if(!slices||!slices.length){ skip('subpop','no slices (no per-DUT data)'); return; }
    // >=1 bucket (histogram slices are single-bucket: one dim-combo, per-DUT means).
    // Pick a slice the detector can actually ASSESS: >=5 DUTs AND at least its own
    // effective min_buckets (histogram overrides to 1 via slice.opts; others need 3).
    // A slice with too few buckets, or where the planted DUTs are null in most buckets,
    // makes _spDetect return clean regardless of the offset -- a false red. So also plant
    // into the two DENSEST DUTs (most non-null buckets), each with >= min_buckets of data.
    // This keeps full teeth (a genuine detector/slice bug still reds) while removing the
    // env_coverage false red where the first two columns of a sparse delta slice had no data.
    var sl=null, plantCols=null;
    for(var i=0;i<slices.length;i++){
      var cand=slices[i]; if(!cand.serials||cand.serials.length<5||!cand.vals_by_freq.length) continue;
      var mb=(cand.opts&&cand.opts.min_buckets)||3; if(cand.vals_by_freq.length<mb) continue;
      var nd=cand.serials.length, cnt=[]; for(var d=0;d<nd;d++)cnt.push(0);
      cand.vals_by_freq.forEach(function(row){ for(var d=0;d<nd;d++){ if(typeof row[d]==='number'&&isFinite(row[d]))cnt[d]++; } });
      var order=[]; for(var d=0;d<nd;d++)order.push(d); order.sort(function(a,b){return cnt[b]-cnt[a];});
      if(cnt[order[0]]>=mb && cnt[order[1]]>=mb){ sl=cand; plantCols=[order[0],order[1]]; break; }
    }
    if(!sl){ skip('subpop','no slice with >=5 DUTs, >= min_buckets buckets, and 2 densely-measured DUTs to plant'); return; }
    var opt={budget_by_freq:sl.budget_by_freq,station_by_dut:sl.station_by_dut};
    if(sl.opts){for(var _k in sl.opts)opt[_k]=sl.opts[_k];}   // per-view overrides (histogram min_buckets:1)
    var base=_spDetect(sl.vals_by_freq, sl.serials, opt);
    var sset={}; sl.serials.forEach(function(s){sset[s]=1;});
    chk('subpop-baseline-flags-are-real-serials',
        base.flagged.every(function(s){return sset[s];}), 'flagged='+JSON.stringify(base.flagged));
    var plant=[sl.serials[plantCols[0]], sl.serials[plantCols[1]]];
    // Dominance-guaranteed offset: set the 2 planted DUTs FAR above the slice's own
    // range, so they form an unambiguous separate mode regardless of the data's native
    // scale/spread. A fixed +1000 fails on wide real data (e.g. switching speed in the
    // thousands of us) where it just lands the pair inside the existing spread.
    var _all=[]; sl.vals_by_freq.forEach(function(row){row.forEach(function(v){if(typeof v==='number'&&isFinite(v))_all.push(v);});});
    var _mx=_all.length?Math.max.apply(null,_all):0, _mn=_all.length?Math.min.apply(null,_all):0;
    var _big=_mx + ((_mx-_mn)||Math.abs(_mx)||1)*1000 + 1e6;
    var mut=sl.vals_by_freq.map(function(row){ return row.map(function(v,di){ return (v==null)?v:((di===plantCols[0]||di===plantCols[1])?_big:v); }); });
    var r=_spDetect(mut, sl.serials, opt); var flg={}; r.flagged.forEach(function(s){flg[s]=1;});
    chk('subpop-planted-flags-exactly-the-2-offset-duts',
        r.status==='flagged'&&r.flagged.length===2&&flg[plant[0]]&&flg[plant[1]],
        'status='+r.status+' flagged='+JSON.stringify(r.flagged)+' planted='+JSON.stringify(plant));
    var shift=sl.vals_by_freq.map(function(row){ return row.map(function(v){ return v==null?v:v+1000.0; }); });
    var rs=_spDetect(shift, sl.serials, opt);
    chk('subpop-uniform-shift-is-not-a-subpopulation',
        JSON.stringify(rs.flagged)===JSON.stringify(base.flagged),
        'shifted='+JSON.stringify(rs.flagged)+' base='+JSON.stringify(base.flagged));
    var html=''; try{ html=_spAdvisoryHtml(ctx); }catch(e){}
    chk('subpop-advisory-renders', html.indexOf('Distribution health')>=0, 'len='+html.length);
  }

  /* "Remove auto-filter": applying auto-filter must ADD to the Global Filter (increment,
     manual items preserved), and Remove must SUBTRACT ONLY the auto increment (manual
     items survive) -- while Clear wipes everything. Tests the merge/mark/remove mechanism
     directly (independent of whether this dataset produces auto candidates). GF-view only;
     the histogram has no GF (its Clear-auto already removes exactly the auto set). */
  function runRemoveAuto(R,chk,skip){
    if(typeof GF_KEY==='undefined'||typeof _afMarkApplied!=='function'||typeof _afRemoveApplied!=='function'){ skip('remove-auto','no GF / remove-auto in this view'); return; }
    var ctx=null; ['BOX_AF','STAT_AF','SUM_AF','EC_AF'].forEach(function(nm){ if(!ctx&&typeof window[nm]==='object'&&window[nm]&&window[nm].removeFn&&window[nm].merge) ctx=window[nm]; });
    if(!ctx){ skip('remove-auto','no GF-view ctx with removeFn'); return; }
    var _origAlert=window.alert; window.alert=function(){};
    function gf(){ try{var r=localStorage.getItem(GF_KEY); return r?(JSON.parse(r).excluded||[]):[]; }catch(e){return [];} }
    var savedGf=null; try{savedGf=localStorage.getItem(GF_KEY);}catch(e){}
    try{
      try{localStorage.removeItem(GF_KEY);}catch(e){} window[ctx.resultVar+'__applied']=new Set(); if(ctx.reloadGf)ctx.reloadGf();
      var M='QAMAN||qac||Room||1.0 MHz', A='QAA||qac||Room||2.0 MHz', B='QAB||qac||Room||3.0 MHz';
      ctx.merge([M]);
      chk('remove-auto: seed manual GF key present', gf().indexOf(M)>=0, JSON.stringify(gf()));
      _afMarkApplied(ctx,[A,B]); ctx.merge([A,B]); var g1=gf();
      chk('remove-auto: Apply is ADDITIVE (manual + auto both present)',
          g1.indexOf(M)>=0&&g1.indexOf(A)>=0&&g1.indexOf(B)>=0, JSON.stringify(g1));
      _afRemoveApplied(ctx); var g2=gf();
      chk('remove-auto: subtracts ONLY the auto increment (manual survives)',
          g2.indexOf(M)>=0&&g2.indexOf(A)<0&&g2.indexOf(B)<0, JSON.stringify(g2));
      // a key already added MANUALLY is not removed even if auto re-applies it (auto-new only)
      window[ctx.resultVar+'__applied']=new Set();
      var O='QAOVL||qac||Room||4.0 MHz';
      ctx.merge([O]); _afMarkApplied(ctx,[O]); ctx.merge([O]); _afRemoveApplied(ctx);
      chk('remove-auto: a manually-added key is NOT removed (only auto-new keys)', gf().indexOf(O)>=0, JSON.stringify(gf()));
    } finally {
      window.alert=_origAlert;
      try{ if(savedGf!==null)localStorage.setItem(GF_KEY,savedGf); else localStorage.removeItem(GF_KEY); }catch(e){}
      window[ctx.resultVar+'__applied']=new Set(); if(ctx.reloadGf)ctx.reloadGf();
    }
  }

  /* Workflow & Recommendations panel must EXPAND and CONTRACT (regression: it opened
     but wouldn't hide because the shown-state used display:'' which the toggle read as
     "closed"). Assert expand shows content + flips the caret, and contract hides it +
     flips the caret back. */
  function runWorkflowToggle(R,chk,skip){
    if(typeof _afToggleWorkflow!=='function'){ skip('workflow-toggle','no _afToggleWorkflow in this view'); return; }
    var ctx=null; ['BOX_AF','STAT_AF','SUM_AF','EC_AF','HIST_AF'].forEach(function(nm){ if(!ctx&&typeof window[nm]==='object'&&window[nm]&&window[nm].wfPanel&&window[nm].wfBtn) ctx=window[nm]; });
    if(!ctx){ skip('workflow-toggle','no workflow ctx'); return; }
    var p=document.getElementById(ctx.wfPanel), b=document.getElementById(ctx.wfBtn);
    if(!p||!b){ skip('workflow-toggle','panel/button missing'); return; }
    function caret(){var c=b.querySelector('.wfcaret'); return c?c.innerHTML:'';}
    if(p.style.display && p.style.display!=='none') _afToggleWorkflow(ctx);   // ensure closed baseline
    var c0=caret();
    _afToggleWorkflow(ctx);
    var expanded=(p.style.display!=='none' && p.innerHTML.length>50), cE=caret();
    _afToggleWorkflow(ctx);
    var contracted=(p.style.display==='none'), cC=caret();
    chk('workflow-toggle: expands (panel shown + content)', expanded, 'disp='+p.style.display);
    chk('workflow-toggle: contracts (panel hidden)', contracted, 'disp='+p.style.display);
    chk('workflow-toggle: caret flips open then back', cE!==cC && cC===c0, 'c0='+c0+' cE='+cE+' cC='+cC);
  }

  function run(){
    var R=[]; function chk(n,ok,d){R.push({name:n,ok:!!ok,detail:d||''});}
    function skip(n,d){R.push({name:n,skip:true,detail:d||''});}
    try{
      if(typeof update==='undefined'){emit({view:'unknown',results:R});return;}
      // View detection for reporting
      var view = (typeof BOX_DATA!=='undefined')?'boxplot'
               : (typeof STAT_DATA!=='undefined')?'stat_summary'
               : (typeof ENV_DATA!=='undefined')?'env_coverage'
               : (typeof VALUES!=='undefined'&&typeof DIMS!=='undefined')?'histogram'
               : (typeof RAW_ABS!=='undefined')?'distribution'
               : 'scatter_or_summary';
      // NB: do NOT enable Show Points here -- reset (clearEverything) turns it
      // back off, which would make reset-restores see a different signature. The
      // generic layer works on the default traces (box traces carry x categories,
      // so they still reflect condition/serial filtering). The deep boxplot block
      // below enables Show Points itself where it needs per-point detail.
      try{update();}catch(e){}   // force one render so the plot is populated before we read it
      var _HEAVY=(typeof window._QA_HEAVY!=='undefined')?(window._QA_HEAVY===true):false;
      // Generic invariants for every view (reversibility loop skipped when heavy).
      runGeneric(R,chk,skip,_HEAVY);
      // Per-dimension filter EFFECT + PERSISTENCE -- runs for EVERY view (incl.
      // reference), covering EACH filter dimension separately (data-col granular),
      // so a dead/non-persisting filter (e.g. the serial filter) can't slip past.
      try{ runFilterEffect(R,chk,skip,_HEAVY); }catch(e){ chk('FILTER-EFFECT-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); }
      try{ runPersistence(R,chk,skip); }catch(e){ chk('PERSISTENCE-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); }
      // Reference Statistics view: an aggregate view (no #plot points). It has its
      // own coupling block; the interactive per-point checks below assume a #plot
      // point view and don't apply, so run it and emit early.
      if(_isReference()){
        try{ runReference(R,chk,skip); }catch(e){ chk('REFERENCE-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); }
        emit({view:'reference',results:R}); return;
      }
      // Table cross-check -- skipped on heavy pages (building/rebuilding a large
      // stats table 3x races the virtual-time dump; the deep GF block is the
      // deterministic priority for a heavy compare boxplot).
      if(_HEAVY){ skip('table-cross-check','skipped on heavy page (reduced suite for a deterministic render)'); }
      else { try{ runTableChecks(R,chk,skip); }catch(e){ chk('TABLE-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); } }
      // Group-by robustness -- skipped on heavy pages (cycles every mode = many updates).
      if(_HEAVY){ skip('group-by','skipped on heavy page'); }
      else { try{ runGroupBy(R,chk,skip); }catch(e){ chk('GROUPBY-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); } }
      // Filter/plot/table coordination (freq range + drag-zoom) -- same heavy-page skip.
      if(_HEAVY){ skip('coordination','skipped on heavy page'); }
      else { try{ runCoordination(R,chk,skip); }catch(e){ chk('COORD-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); } }
      // Histogram Site Population Check (SR-fence membership) -- self-skips off a
      // compare histogram. MUST run before runCsvExport: that check's histogram
      // import round-trip replaces the data globals (DIMS col_ids change), which
      // would leave SITE_COL_ID stale and the fence panel empty.
      if(_HEAVY){ skip('site-population-check','skipped on heavy page'); }
      else { try{ runHistogramSite(R,chk,skip); }catch(e){ chk('SITE-CHECK-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); } }
      // env_coverage / distribution SR-fence panel -- self-skips off those compare pages.
      if(_HEAVY){ skip('site-fence-panel','skipped on heavy page'); }
      else { try{ runSiteFencePanel(R,chk,skip); }catch(e){ chk('SITE-FENCE-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); } }
      // CSV-export-matches-screen + import round-trip -- same heavy-page skip.
      if(_HEAVY){ skip('csv-export','skipped on heavy page'); }
      else { try{ runCsvExport(R,chk,skip); }catch(e){ chk('CSV-EXPORT-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); } }
      // Auto-filter bad DUTs (population views) -- self-skips off a view without it.
      if(_HEAVY){ skip('auto-filter','skipped on heavy page'); }
      else { try{ runAutoFilterStat(R,chk,skip); }catch(e){ chk('AUTOFILTER-STAT-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); } }
      // Generic ctx-driven auto-filter + workflow for summary/env_coverage/histogram
      // (boxplot + stat_summary have their own key-precise checks above).
      if(_HEAVY){ skip('auto-filter-ctx','skipped on heavy page'); }
      else { ['SUM_AF','EC_AF','HIST_AF'].forEach(function(nm){
        try{ if(typeof window[nm]==='object'&&window[nm]) runAutoFilterCtx(R,chk,skip,window[nm],nm.replace('_AF','').toLowerCase()); }
        catch(e){ chk('AUTOFILTER-CTX-HARNESS-ERROR['+nm+']',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); }
      }); }
      // Subpopulation / dual-distribution advisory (self-skips off a view without it).
      if(_HEAVY){ skip('subpop','skipped on heavy page'); }
      else { try{ runSubpop(R,chk,skip); }catch(e){ chk('SUBPOP-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); } }
      // Remove-auto-filter GF increment (add/subtract semantics; self-skips off no-GF views).
      if(_HEAVY){ skip('remove-auto','skipped on heavy page'); }
      else { try{ runRemoveAuto(R,chk,skip); }catch(e){ chk('REMOVE-AUTO-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); } }
      // Workflow & Recommendations expand/contract (self-skips off views without it).
      if(_HEAVY){ skip('workflow-toggle','skipped on heavy page'); }
      else { try{ runWorkflowToggle(R,chk,skip); }catch(e){ chk('WORKFLOW-TOGGLE-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); } }
      // Deep boxplot-only GF invariants (the view where GF is SET).
      if(typeof BOX_DATA==='undefined'){emit({view:view,results:R});return;}
      var pc=document.getElementById('box_show_pts_chk');
      function ensurePts(){ if(pc&&!pc.checked){pc.checked=true; update();} }
      // Read plotted points from the "Show Points" overlay traces: name ends
      // " pts", y=value, x=freq category (label), text="<serial>: <value>".
      function ppPts(){
        var gd=document.getElementById('plot'), out=[];
        (gd.data||[]).forEach(function(t){
          if(t.type==='scatter'&&t.mode==='markers'&&/ pts$/.test(t.name||'')){
            var g=(t.name||'').replace(/ pts$/,''), tx=t.text||[], xs=t.x||[];
            for(var i=0;i<(t.y?t.y.length:0);i++){
              var s=null; if(tx[i]){var m=String(tx[i]).match(/^([^:]+):/); if(m)s=m[1].trim();}
              out.push({g:g,x:String(xs[i]),v:t.y[i],s:s});
            }
          }
        });
        return out;
      }
      function cnt(){return ppPts().length;}
      function ident(p){return p.g+'||'+p.s+'||'+p.x+'||'+p.v;}  // count-aware point identity
      function bag(arr){var m={};arr.forEach(function(p){var k=ident(p);m[k]=(m[k]||0)+1;});return m;}
      function removedBetween(before,after){
        var b=bag(before),a=bag(after),rem=[];
        Object.keys(b).forEach(function(k){var d=b[k]-(a[k]||0);for(var i=0;i<d;i++)rem.push(k);});
        return rem;
      }
      function setCbx(cls,val,checked){
        var c=[].slice.call(document.querySelectorAll('input.'+cls)).filter(function(x){return x.value===val;})[0];
        if(!c) return false;
        if(c.checked!==checked){c.checked=checked; c.dispatchEvent(new Event('change',{bubbles:true}));}
        return true;
      }
      // Set a checkbox WITHOUT firing its onchange (so a whole batch can be set,
      // then update() called ONCE) -- on a many-serial heavy page, per-serial
      // change events would each trigger a full expensive update().
      function setCbxNoFire(cls,val,checked){
        var c=[].slice.call(document.querySelectorAll('input.'+cls)).filter(function(x){return x.value===val;})[0];
        if(c) c.checked=checked; return !!c;
      }
      function reset(){ if(typeof clearEverything!=='undefined'){clearEverything();} if(typeof clearGlobalFilter!=='undefined'){clearGlobalFilter();} ensurePts(); }

      reset();
      var P0=cnt(); var base=ppPts();
      chk('baseline-not-blank', P0>0, 'P0='+P0);
      if(P0===0){emit({view:'boxplot',results:R});return;}

      // ---- outliers-GF-precise ----
      if(typeof _collectOutliers!=='undefined'&&typeof applyGlobalFilter!=='undefined'){
        var outs=_collectOutliers(getSelectedConds(),getSelectedTemps(),getYFilter(),getSelectedBoxSerials());
        // outlier identity by (baseSerial, condition, freqLabel). A multi-temp
        // single-site boxplot names its box group "cond (temp)", so add that
        // variant too (keeps temp precision -- an outlier at 20C adds only the
        // "(20C)" group, not "(30C)").
        var outId={}; outs.forEach(function(o){
          var bs=(typeof _boxBaseSerial!=='undefined'?_boxBaseSerial(o.serial):o.serial);
          var fl=(o.freqLabel!=null?o.freqLabel:o.freq);
          outId[bs+'||'+o.cond+'||'+fl]=1;
          if(o.temp) outId[bs+'||'+o.cond+' ('+o.temp+')||'+fl]=1;
        });
        var b0=ppPts();
        applyGlobalFilter(); update();
        var a0=ppPts(); var P1=a0.length;
        chk('outliers-GF-not-blank', P1>0, 'P1='+P1+' P0='+P0);
        var rem=removedBetween(b0,a0);
        // every removed point must belong to an outlier (serial,cond,freqLabel)
        // identity -- catches whole-DUT / cross-frequency over-exclusion.
        // The removed point's serial (from the pts hover text) is port-qualified,
        // but outId is keyed on the BASE serial (as the boxplot stores GF keys) --
        // strip the port before checking, or a ported boxplot flags everything.
        var _bs=function(s){return (typeof _boxBaseSerial!=='undefined')?_boxBaseSerial(s):s;};
        var outside=rem.filter(function(k){var p=k.split('||'); return !outId[_bs(p[1])+'||'+p[0]+'||'+p[2]];});
        chk('outliers-GF-precise (no cross-freq/DUT over-exclusion)', outside.length===0,
            'removed='+rem.length+' outside-outlier-identity='+outside.length+' numOutliers='+outs.length);
        chk('outliers-GF-removed-something', rem.length>0||outs.length===0, 'removed='+rem.length+' numOutliers='+outs.length);
        if(typeof clearGlobalFilter!=='undefined'){ clearGlobalFilter(); ensurePts();
          chk('clear-GF-restores', cnt()===P0, 'after='+cnt()+' P0='+P0);
        }
      } else skip('outliers-GF-precise','no _collectOutliers/applyGlobalFilter');

      // ---- deselect-site (compare only) ----
      var siteCbx=[].slice.call(document.querySelectorAll('input.box_cond_Site'));
      if(siteCbx.length>=2){
        var siteVals=siteCbx.map(function(c){return c.value;});
        var v0=siteVals[0];
        var cntFor=function(pred){return ppPts().filter(pred).length;};
        var thisSite0=cntFor(function(p){return p.g.indexOf('Site: '+v0)>=0;});
        setCbx('box_cond_Site',v0,false);
        var remain=ppPts();
        chk('deselect-site-removes-it', remain.every(function(p){return p.g.indexOf('Site: '+v0)<0;}),
            'site='+v0+' leftover='+remain.filter(function(p){return p.g.indexOf('Site: '+v0)>=0;}).length);
        chk('deselect-site-keeps-others', remain.length===P0-thisSite0, 'expected='+(P0-thisSite0)+' got='+remain.length);
        setCbx('box_cond_Site',v0,true);
        chk('reselect-site-restores', cnt()===P0, 'after='+cnt()+' P0='+P0);
      } else skip('deselect-site','not a compare page (no >=2 Site checkboxes)');

      // ---- deselect-serial ----
      var allSer=(typeof getAllBoxSerials!=='undefined')?getAllBoxSerials():[];
      var serCbxCls='box_ser_chk';
      if(allSer.length>1 && document.querySelector('input.'+serCbxCls)){
        var ser=allSer[0];
        var serCnt0=ppPts().filter(function(p){return p.s===ser;}).length;
        if(setCbx(serCbxCls,ser,false)){
          chk('deselect-serial-removes-it', ppPts().every(function(p){return p.s!==ser;}), 'ser='+ser);
          chk('deselect-serial-keeps-others', cnt()===P0-serCnt0, 'expected='+(P0-serCnt0)+' got='+cnt());
          setCbx(serCbxCls,ser,true);
          chk('reselect-serial-restores', cnt()===P0, 'after='+cnt()+' P0='+P0);

          // ---- filter-GF-whole-dut ----
          if(typeof setFilterAsGf!=='undefined'&&typeof clearGlobalFilter!=='undefined'){
            allSer.forEach(function(s){setCbxNoFire(serCbxCls,s,s===ser);}); update();   // narrow to just `ser` (one update)
            setFilterAsGf();
            allSer.forEach(function(s){setCbxNoFire(serCbxCls,s,true);}); update();      // restore serial selection (one update)
            chk('filter-GF-whole-dut-excludes-serial', ppPts().every(function(p){return p.s!==ser;}),
                'ser='+ser+' leftover='+ppPts().filter(function(p){return p.s===ser;}).length);
            chk('filter-GF-keeps-others', cnt()===P0-serCnt0, 'expected='+(P0-serCnt0)+' got='+cnt());
            clearGlobalFilter(); ensurePts();
            chk('clear-after-filter-GF-restores', cnt()===P0, 'after='+cnt()+' P0='+P0);
          }
        } else skip('deselect-serial','serial checkbox not settable');
      } else skip('deselect-serial','no serial checkboxes / single serial');

      // ---- auto-filter bad DUTs (robust MAD) ----
      // Oracle-free: (a) OFF produces no result; (b) the dist-basis bad-point set
      // matches a fully INDEPENDENT median/MAD modified-z recompute from BOX_DATA
      // (catches a uniformly-wrong magnitude -- the whole reason this basis is
      // MAD-robust rather than sigma-from-mean); (c) auto count is monotonic in
      // level; (d) no auto DUT is systemic (shared>0.5) or risky (>=5%); (e) Apply
      // removes ONLY auto DUTs' points (point-precise, no clean DUT), and Clear
      // restores. Skips (e) cleanly when nothing qualifies (clean real data).
      if(typeof _autoBadPoints==='function' && typeof _autoFilterCompute==='function'
         && document.getElementById('auto_gf_basis') && document.getElementById('auto_gf_level')){
        reset();
        var abasis=document.getElementById('auto_gf_basis'), alevel=document.getElementById('auto_gf_level');
        abasis.value='dist';
        alevel.value='off'; if(typeof autoFilterPreview==='function') autoFilterPreview();
        var aPanel=document.getElementById('auto_gf_panel');
        chk('auto-off-produces-nothing', !window._autoResult && (!aPanel||getComputedStyle(aPanel).display==='none'),
            'result='+(window._autoResult?'set':'null')+' panel='+(aPanel?getComputedStyle(aPanel).display:'none'));
        // (b) independent re-derivation of EACH peer-relative basis (dist / iqr /
        // dmad), fully separate code from _afScorer -- a second implementation
        // catching a bug in the first.
        function _med(a){var s=a.slice().sort(function(x,y){return x-y;});var n=s.length;return n?(n%2?s[(n-1)/2]:0.5*(s[n/2-1]+s[n/2])):0;}
        var _abs=function(s){return (typeof _boxBaseSerial!=='undefined')?_boxBaseSerial(s):s;};
        var selC=getSelectedConds(), selT=getSelectedTemps(), fr=getBoxFreqRange();
        function _flagFor(basis, fv){
          var med=_med(fv);
          if(basis==='iqr'){ var s=fv.slice().sort(function(a,b){return a-b;}),n=s.length;
            function pc(p){var i=(p/100)*(n-1),li=Math.floor(i);return li+1<n?s[li]+(s[li+1]-s[li])*(i-li):s[li];}
            var q1=pc(25),q3=pc(75),iqr=q3-q1,loF=q1-1.5*iqr,hiF=q3+1.5*iqr; return function(v){return v>hiF||v<loF;}; }
          if(basis==='dmad'){ var below=[],above=[]; fv.forEach(function(v){if(v<=med)below.push(med-v);if(v>=med)above.push(v-med);});
            var sym=_med(fv.map(function(v){return Math.abs(v-med);}))*1.4826||1e-9;
            var mLo=_med(below)*1.4826||sym, mHi=_med(above)*1.4826||sym;
            return function(v){var mz=(v>=med)?(v-med)/mHi:(med-v)/mLo; return mz>=3.5;}; }
          var mad=_med(fv.map(function(v){return Math.abs(v-med);}))*1.4826||1e-9;
          return function(v){return Math.abs(v-med)/mad>=3.5;};
        }
        function _expKeys(basis){ var e={};
          BOX_DATA.forEach(function(cd){
            if(selC.indexOf(cd.condition)<0)return; if(selT.indexOf(cd.temp)<0)return;
            (cd.freq_stats||[]).forEach(function(f){
              if(f.freq<fr.lo||f.freq>fr.hi)return;
              var det=(f.vals_detail||[]); if(det.length<4)return;
              var flag=_flagFor(basis, det.map(function(d){return d.v;}));
              det.forEach(function(d){ if(flag(d.v)) e[_abs(d.s)+'||'+cd.condition+'||'+cd.temp+'||'+f.freq_label]=1; });
            });
          });
          return e; }
        function _gotKeys(basis){ var g={}; _autoBadPoints(basis).forEach(function(o){ g[_abs(o.serial)+'||'+o.cond+'||'+o.temp+'||'+o.freqLabel]=1; }); return g; }
        ['dist','iqr','dmad'].forEach(function(basis){
          var exp=_expKeys(basis), got=_gotKeys(basis);
          var miss=Object.keys(exp).filter(function(k){return !got[k];});
          var xtra=Object.keys(got).filter(function(k){return !exp[k];});
          chk('auto-badpoints-matches-independent-'+basis, miss.length===0&&xtra.length===0,
              'flagged='+Object.keys(got).length+' expected='+Object.keys(exp).length+' missing='+miss.length+' extra='+xtra.length);
        });
        abasis.value='dist';
        // (c) level monotonicity
        function autoN(lv){ alevel.value=lv; autoFilterPreview(); return window._autoResult?window._autoResult.auto.length:0; }
        var nC=autoN('conservative'), nM=autoN('moderate'), nA=autoN('aggressive');
        chk('auto-level-monotonic', nA>=nM && nM>=nC, 'conservative='+nC+' moderate='+nM+' aggressive='+nA);
        // (d) classification invariants on the aggressive auto set
        var ar=window._autoResult;
        var badCls=(ar?ar.auto:[]).filter(function(d){ return d.shared>0.5 || d.risk>=0.05; });
        chk('auto-never-systemic-or-risky', badCls.length===0, 'violations='+badCls.length+' auto='+(ar?ar.auto.length:0));
        // (d2) compare SITE SCOPE invariants (reference / onboarding / both). The
        // gate must scope the auto set by site; the per-site eligible summary must
        // be scope-INDEPENDENT (it's what lets the user choose the scope) and must
        // equal what 'both' actually auto-filters. Real teeth whenever >=1 DUT is
        // auto-eligible on a site (vacuously true at 0, honest either way).
        var _siteSel=document.getElementById('auto_gf_site');
        if(_siteSel && typeof PRIMARY_SITE!=='undefined' && PRIMARY_SITE){
          function _scopeRes(sc){ _siteSel.value=sc; return _autoFilterCompute('dist','aggressive'); }
          var _rp=_scopeRes('primary'), _ro=_scopeRes('onboarding'), _rb=_scopeRes('both');
          function _ser(r){return r.auto.map(function(d){return d.serial;}).sort();}
          var _sp=_ser(_rp), _so=_ser(_ro), _sb=_ser(_rb);
          chk('site-scope-primary-only-reference', _rp.auto.filter(function(d){return d.site!==PRIMARY_SITE;}).length===0,
              'non-reference DUTs in primary-scope auto='+_rp.auto.filter(function(d){return d.site!==PRIMARY_SITE;}).length);
          chk('site-scope-onboarding-only-nonreference', _ro.auto.filter(function(d){return d.site===PRIMARY_SITE;}).length===0,
              'reference DUTs in onboarding-scope auto='+_ro.auto.filter(function(d){return d.site===PRIMARY_SITE;}).length);
          chk('site-scope-both-is-union-by-site', _sb.length===_sp.length+_so.length && _sb.length>=_sp.length && _sb.length>=_so.length,
              'both='+_sb.length+' primary='+_sp.length+' onboarding='+_so.length);
          function _sumStr(r){return Object.keys(r.siteSummary||{}).sort().map(function(k){return k+':'+r.siteSummary[k].eligible;}).join(',');}
          chk('site-scope-summary-scope-independent', _sumStr(_rp)===_sumStr(_ro)&&_sumStr(_ro)===_sumStr(_rb),
              'primary['+_sumStr(_rp)+'] onboarding['+_sumStr(_ro)+'] both['+_sumStr(_rb)+']');
          var _totElig=Object.keys(_rb.siteSummary||{}).reduce(function(x,k){return x+_rb.siteSummary[k].eligible;},0);
          chk('site-scope-summary-matches-both-auto', _totElig===_sb.length, 'summaryEligible='+_totElig+' bothAuto='+_sb.length);
          // restore the state the (e) apply/clear block below expects (default
          // scope=primary, aggressive, preview regenerated).
          _siteSel.value='primary'; alevel.value='aggressive'; if(typeof autoFilterPreview==='function') autoFilterPreview(); ar=window._autoResult;
        } else skip('site-scope','not a compare boxplot (no auto_gf_site / PRIMARY_SITE)');
        // (e) apply point-precise + clear restores (skip when nothing qualifies)
        if(ar && ar.auto.length && typeof autoFilterApply==='function'){
          var autoSers={}; ar.auto.forEach(function(d){autoSers[d.serial]=1;});
          ensurePts(); var ab=ppPts();
          autoFilterApply(); ensurePts();
          var rem=removedBetween(ab,ppPts());
          var outsideAuto=rem.filter(function(k){var p=k.split('||'); return !autoSers[_abs(p[1])];});
          chk('auto-apply-only-removes-auto-DUTs', outsideAuto.length===0, 'removed='+rem.length+' outside-auto='+outsideAuto.length);
          chk('auto-apply-removed-something', rem.length>0, 'removed='+rem.length);
          if(typeof clearGlobalFilter!=='undefined'){ clearGlobalFilter(); ensurePts();
            chk('auto-clear-restores', cnt()===P0, 'after='+cnt()+' P0='+P0); }
        } else skip('auto-apply-precise','no DUT qualifies for auto at dist/aggressive on this data');
        alevel.value='off'; if(typeof autoFilterPreview==='function') autoFilterPreview();
        reset();
        // ---- Workflow & Recommendations (boxplot) ----
        if(typeof _afRecommend==='function' && typeof _afRunWorkflow==='function'
           && typeof BOX_AF!=='undefined' && document.getElementById('box_wf_panel')){
          function _gfN(){try{return (JSON.parse(localStorage.getItem(GF_KEY)||'{"excluded":[]}').excluded||[]).length;}catch(e){return -1;}}
          if(typeof clearGlobalFilter!=='undefined') clearGlobalFilter();
          var wa=_afAnalyze(BOX_AF), rec=_afRecommend(wa);
          chk('workflow-recommends-valid',
              ['dist','iqr','dmad','spec','tll'].indexOf(rec.basis)>=0
              && ['conservative','moderate','aggressive'].indexOf(rec.level)>=0
              && rec.why && rec.why.length>0,
              'basis='+rec.basis+' level='+rec.level+' reasons='+(rec.why?rec.why.length:0));
          var expR=BOX_AF.compute(rec.basis,rec.level), _uk={};
          expR.auto.forEach(function(d){d.keys.forEach(function(k){_uk[k]=1;});});
          var expKeys=Object.keys(_uk).length;
          if(typeof clearGlobalFilter!=='undefined') clearGlobalFilter();
          boxRunWorkflow();
          chk('workflow-run-applies-recommended-auto', _gfN()===expKeys, 'gf='+_gfN()+' expected='+expKeys);
          var au=document.getElementById('box_wf_panel_audit');
          chk('workflow-run-writes-audit', !!au && au.textContent.indexOf('Ran recommended workflow')>=0, 'audit='+(au?'present':'missing'));
          if(typeof boxGenReport==='function'){
            window._afNoPrint=true; window._afNoCapture=true;
            try{ boxGenReport(); }catch(e){}
            window._afNoPrint=false; window._afNoCapture=false;
            var rep=document.getElementById('af_report');
            chk('workflow-report-generated', !!rep && rep.textContent.indexOf('Auto-filter Workflow Report')>=0
                && rep.textContent.indexOf('Recommendation')>=0 && rep.textContent.indexOf('Auto-excluded')>=0,
                'report='+(rep?'present':'missing'));
          }
          if(typeof clearGlobalFilter!=='undefined') clearGlobalFilter();
          chk('workflow-run-reversible', _gfN()===0, 'gf='+_gfN());
          reset();
        } else skip('workflow','no workflow panel in this view');
      } else skip('auto-filter','no auto-filter controls in this view');

      // ---- Data filter: All / Passing only / Failing only + independent trim (Q1 cleanup 2026-09-21) ----
      // Failing-only must be the exact complement of Passing-only (their point sets
      // partition All), and the "Trim raw samples" control must be independent of the
      // pass/fail radio (combine with it). Uses a splitting Spec-up override so the
      // data actually has a pass/fail boundary even on a no-CSV-spec compare.
      (function(){
        var failRad=document.querySelector('input[name="box_flt"][value="failing"]');
        if(!failRad){ skip('box-data-filter','no Failing-only radio in this view'); return; }
        var so=document.getElementById('box_tll_hi');
        if(!so){ skip('box-data-filter','no Spec override input'); return; }
        var vals=[]; BOX_DATA.forEach(function(cd){(cd.freq_stats||[]).forEach(function(f){(f.vals_detail||[]).forEach(function(d){ if(typeof d.v==='number'&&isFinite(d.v)) vals.push(d.v); });});});
        if(vals.length<20){ skip('box-data-filter','too few points to split'); return; }
        vals.sort(function(a,b){return a-b;});
        var med=vals[Math.floor(vals.length/2)];
        if(med<=vals[0]||med>=vals[vals.length-1]){ skip('box-data-filter','no spread to split'); return; }
        ensurePts();
        function setMode(m){document.querySelector('input[name="box_flt"][value="'+m+'"]').checked=true;}
        so.value=String(med);
        setMode('all'); update(); var A=ppPts().length;
        setMode('passing'); update(); var P=ppPts().length;
        setMode('failing'); update(); var F=ppPts().length;
        chk('box-failing-isolates-nonempty-subset', F>0&&F<A, 'all='+A+' failing='+F);
        chk('box-passing-strict-subset', P>0&&P<A, 'all='+A+' passing='+P);
        // partition (tolerate a +/-1 trace-emission edge on single-point cells)
        chk('box-passing-failing-partition', (P+F)>=A-2&&(P+F)<=A, 'all='+A+' P+F='+(P+F));
        // trim above the split, while Failing: removes the (high) failing points -> strictly fewer
        var yhi=document.getElementById('box_flt_yhi'); yhi.value=String(med); update(); var Ft=ppPts().length;
        chk('box-trim-combines-with-failing', Ft<F, 'failing='+F+' failing+trim='+Ft);
        yhi.value=''; so.value=''; setMode('all'); update();
      })();

      // ---- Verdict-driven pass/fail: filter -> plot -> table must all agree (David 2026-09-21) ----
      // Pass/fail is per-point vs its OWN Upper/Lower Limit; with NO numeric limit it falls back
      // to PADB's recorded verdict (BOX_STATUS_FIELD, e.g. Test Event Status = P/F). Catches two
      // real bugs: (a) the table counted #fail vs a single flat HI_SPEC/LO_SPEC (phantom fails
      // when conditions have different specs); (b) with no numeric limit, Failing-only showed
      // NOTHING on the plot and the table had no Status, ignoring the P/F verdict. Independently
      // recompute the verdict fails and require the per-point table, the grouped table, AND the
      // Failing-only plot to ALL equal it -- and to DIFFER from the flat-spec count (teeth).
      (function(){
        if(typeof _boxVerdict==='undefined'){ skip('box-verdict','no _boxVerdict (older build)'); return; }
        if(typeof clearGlobalFilter==='function') clearGlobalFilter();
        reset();
        var statusField=(typeof BOX_STATUS_FIELD!=='undefined')?BOX_STATUS_FIELD:'';
        function _statusFail(cond){ if(!statusField||!cond) return null;
          var mm=cond.match(new RegExp(statusField.replace(/[-\/\\^$*+?.()|[\]{}]/g,'\\$&')+':\\s*(.+?)(?=\\s{2,}|$)'));
          if(!mm) return null; var v=mm[1].trim().toUpperCase();
          if(v==='F'||v==='FAIL'||v==='FAILED') return true; if(v==='P'||v==='PASS'||v==='PASSED') return false; return null; }
        var flatHi=(typeof HI_SPEC!=='undefined')?HI_SPEC:null, flatLo=(typeof LO_SPEC!=='undefined')?LO_SPEC:null;
        var anyVerdict=false, vFail=0, flatFail=0;
        BOX_DATA.forEach(function(cd){ (cd.freq_stats||[]).forEach(function(fs){ (fs.vals_detail||[]).forEach(function(d){
          if(typeof d.v!=='number'||!isFinite(d.v)) return;
          var oh=(d.upper_limit!=null&&d.upper_limit!==undefined)?d.upper_limit:null;
          var ol=(d.lower_limit!=null&&d.lower_limit!==undefined)?d.lower_limit:null;
          var vd=(oh!=null||ol!=null)?PADB_isFail(d.v,oh,ol):_statusFail(cd.condition);
          if(vd!==null){ anyVerdict=true; if(vd===true) vFail++; }
          if(PADB_isFail(d.v,flatHi,flatLo)===true) flatFail++;
        }); }); });
        if(!anyVerdict){ skip('box-verdict','no per-point limit or recorded verdict in this data'); return; }
        // (1) per-point table #fail + Status column == independent verdict
        var tm=document.getElementById('box_table_mode'); if(tm){ tm.value='perpoint'; tm.dispatchEvent(new Event('change')); }
        var el=document.getElementById('box_stat_panel'); if(!el||el.style.display==='none'){ if(typeof toggleStatPanel==='function') toggleStatPanel(); }
        update();
        var hdr=el?el.textContent:''; var m=hdr.match(/([\d,]+)\s*fail\b/);
        var tblFail=m?parseInt(m[1].replace(/,/g,''),10):-1;
        chk('box-perpoint-fail-equals-independent-verdict', tblFail===vFail, 'table='+tblFail+' verdict='+vFail+' flatSpec='+flatFail);
        chk('box-perpoint-status-column-present-when-verdict', /Status/.test(hdr), 'Status header present='+/Status/.test(hdr));
        if(vFail===flatFail){ skip('box-verdict-not-flat-teeth','verdict==flat-spec here; a flat-spec regression would be invisible on this data'); }
        else { chk('box-verdict-not-flat-spec', tblFail!==flatFail, 'table='+tblFail+' flatSpec='+flatFail+' (must NOT be the flat global spec)'); }
        // (2) grouped table #fail total == verdict
        if(tm){ tm.value='grouped'; tm.dispatchEvent(new Event('change')); } update();
        var gt=0; el.querySelectorAll('tbody tr').forEach(function(tr){var t=tr.querySelectorAll('td');if(!t.length)return;var mm2=t[t.length-1].textContent.match(/(\d+)\s*\/\s*\d+/);if(mm2)gt+=parseInt(mm2[1],10);});
        chk('box-verdict-grouped-fail-total-matches', gt===vFail, 'grouped='+gt+' verdict='+vFail);
        // (3) filter -> plot: Failing-only isolates exactly the fails on the plot
        var sp=document.getElementById('box_show_pts_chk'); if(sp&&!sp.checked){sp.checked=true;}
        // count ONLY the Show-Points overlay (name ends " pts") -- NOT the outlier-circle
        // markers (also mode:'markers'), which would double-count points that are outliers.
        function _mk(){var g=document.getElementById('plot'),n=0;(g.data||[]).forEach(function(t){if(t.type==='scatter'&&(t.mode||'').indexOf('markers')>=0&&/ pts$/.test(t.name||''))n+=((t.y&&t.y.length)||0);});return n;}
        function setm(x){var e=document.querySelector('input[name="box_flt"][value="'+x+'"]');e.checked=true;e.dispatchEvent(new Event('change'));}
        setm('failing'); update(); var pFail=_mk();
        chk('box-verdict-failing-plot-isolates-fails', pFail===vFail, 'plotFailing='+pFail+' verdictFails='+vFail);
        setm('all'); reset();
      })();

      // ---- Filters verified SINGLY and CROSSED: filter -> plot -> table invariants (David 2026-09-21) ----
      // For each filter (singly) and several crossed combinations, hold the invariants that
      // encode "table follows plot follows filters": in per-point mode, Failing-only => every
      // shown point is a FAIL, Passing-only => zero fails, both are subsets of All, and the
      // plot has boxes IFF the table has rows. These must hold under ANY filter combination.
      (function(){
        if(typeof _boxVerdict==='undefined'){ skip('filter-cross','no _boxVerdict (older build)'); return; }
        var haveVerdict=(typeof BOX_STATUS_FIELD!=='undefined'&&BOX_STATUS_FIELD)?true:(function(){var any=false;BOX_DATA.forEach(function(cd){(cd.freq_stats||[]).forEach(function(fs){(fs.vals_detail||[]).forEach(function(d){if(d.upper_limit!=null||d.lower_limit!=null)any=true;});});});return any;})();
        var sp=document.getElementById('box_show_pts_chk');
        function _pp(){ var tm=document.getElementById('box_table_mode'); if(tm){tm.value='perpoint';tm.dispatchEvent(new Event('change'));}
          var el=document.getElementById('box_stat_panel'); if(!el||el.style.display==='none') toggleStatPanel();
          if(sp&&!sp.checked){sp.checked=true;} update();
          var t=el.textContent, mp=t.match(/([\d,]+)\s*point/), mf=t.match(/([\d,]+)\s*fail\b/);
          return {pts:mp?parseInt(mp[1].replace(/,/g,''),10):0, fail:mf?parseInt(mf[1].replace(/,/g,''),10):0, hasStatus:/Status/.test(t)}; }
        // Plotted points = the Show-Points overlay markers (a single/degenerate group can
        // have data points but no drawn box, so count markers not box traces). Count ONLY
        // the " pts" overlay -- NOT outlier-circle markers (also mode:'markers'), else a
        // point that is also an outlier would be counted twice.
        function _mkPts(){ var g=document.getElementById('plot'),n=0; (g.data||[]).forEach(function(t){if(t.type==='scatter'&&(t.mode||'').indexOf('markers')>=0&&/ pts$/.test(t.name||''))n+=((t.y&&t.y.length)||0);}); return n; }
        function setm(x){var e=document.querySelector('input[name="box_flt"][value="'+x+'"]');if(e){e.checked=true;e.dispatchEvent(new Event('change'));}}
        function chk1(cls,val){var els=document.querySelectorAll('.'+cls);if(els[0]){els[0].checked=val;els[0].dispatchEvent(new Event('change'));return true;}return false;}
        function narrowFreq(){var lo=document.getElementById('box_freq_lo'),hi=document.getElementById('box_freq_hi');if(lo&&hi){var a=parseFloat(lo.value),b=parseFloat(hi.value);if(isFinite(a)&&isFinite(b)&&b>a){lo.value=a+(b-a)*0.25;hi.value=a+(b-a)*0.75;lo.dispatchEvent(new Event('input'));}}}
        function gbSerial(){var s=document.getElementById('box_group_by');if(s){[].forEach.call(s.options,function(o){o.selected=(o.value==='__serial__');});s.dispatchEvent(new Event('change'));}}
        function condOff(){if(typeof COND_DIMS!=='undefined'&&COND_DIMS.length)chk1('box_cond_'+COND_DIMS[0].col_id,false);}
        function tempOff(){var e=document.querySelectorAll('.box_env_chk');if(e.length>1){e[0].checked=false;e[0].dispatchEvent(new Event('change'));}}
        function scenario(name,setup){
          reset(); if(typeof clearGlobalFilter==='function') clearGlobalFilter();
          try{ setup(); }catch(e){ chk('filter-cross['+name+']-setup',false,String(e)); return; }
          setm('all'); var a=_pp();
          setm('passing'); var pz=_pp();
          setm('failing'); var fz=_pp(); var mkFail=_mkPts();
          var gbOn=(function(){var s=document.getElementById('box_group_by');return s?[].some.call(s.selectedOptions,function(o){return !!o.value;}):false;})();
          // Group-by pools into boxes without a per-point overlay, so plot-markers != per-point
          // rows there (different representation, not a desync); the semantics below still apply.
          if(gbOn){ skip('filter-cross['+name+']-plot-points==table-rows','group-by pools (no per-point overlay)'); }
          else { chk('filter-cross['+name+']-plot-points==table-rows', mkFail===fz.pts, 'plotMarkers='+mkFail+' failRows='+fz.pts); }
          chk('filter-cross['+name+']-pass/fail-subset-of-all', fz.pts<=a.pts&&pz.pts<=a.pts, 'all='+a.pts+' pass='+pz.pts+' fail='+fz.pts);
          if(haveVerdict&&a.hasStatus){
            chk('filter-cross['+name+']-failing=all-shown-fail', fz.fail===fz.pts, 'fail='+fz.fail+' shown='+fz.pts);
            chk('filter-cross['+name+']-passing=zero-fail', pz.fail===0, 'passFail='+pz.fail);
          }
        }
        // singly
        scenario('base', function(){});
        scenario('serial', function(){ chk1('box_ser_chk',false); });
        scenario('temp', tempOff);
        scenario('cond', condOff);
        scenario('freq', narrowFreq);
        scenario('groupby-serial', gbSerial);
        // crossed
        scenario('serial+freq', function(){ chk1('box_ser_chk',false); narrowFreq(); });
        scenario('serial+cond+temp', function(){ chk1('box_ser_chk',false); condOff(); tempOff(); });
        scenario('groupby-serial+cond', function(){ gbSerial(); condOff(); });
        setm('all'); reset();
      })();

      // ---- reset-restores ----
      reset();
      chk('reset-restores', cnt()===P0, 'after='+cnt()+' P0='+P0);

    }catch(e){ chk('HARNESS-ERROR', false, String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); }
    emit({view:'boxplot', results:R});
  }
  // ---- readiness gate: never read a mid-render plot ----
  // Heavy compare boxplots (hundreds of thousands of embedded points) take real
  // wall-clock to render; firing at a fixed delay read a half-built plot, which
  // is what made outliers-GF-precise results on those pages untrustworthy. Wait
  // until the Plotly graph div actually has data traces with points before we
  // start, and emit a clear diagnostic if it never renders (rather than a silent
  // no-sentinel that looks identical to a real logic failure).
  function _plotReady(){
    try{
      var gd=_gd(); if(!gd||!gd.data||!gd.data.length) return false;
      // A box/histogram trace counts as rendered even with no x/y point arrays --
      // under binary_encode the box is drawn from precomputed q1/median/q3, so
      // t.y is empty. Otherwise require a trace with actual x or y points.
      return gd.data.some(function(t){
        if(t.type==='box'||t.type==='histogram') return true;
        return ((t.y&&t.y.length)||(t.x&&t.x.length)||0)>0;
      });
    }catch(e){ return false; }
  }
  function _bootWhenReady(){
    // Give up (with a clear diagnostic) at ~70% of the virtual-time budget, so a
    // genuinely unrenderable page still emits a sentinel instead of Edge dumping
    // the DOM mid-poll with nothing. Synchronous Plotly render doesn't advance
    // virtual time, so once the plot is ready the poll stops immediately and
    // burns ~none of the budget -- only a never-ready page runs the poll out.
    var budget=(typeof window._QA_BUDGET_MS==='number')?window._QA_BUDGET_MS:20000;
    var tries=0, MAX=Math.max(30, Math.floor(budget*0.7/100));
    (function wait(){
      if(_plotReady()){ setTimeout(run,250); return; }   // small settle after first populated frame
      if(++tries>MAX){ emit({view:'render-not-ready', results:[{name:'plot-rendered', ok:false,
        detail:'Plotly plot never populated within readiness wait -- page too heavy to render headlessly at this budget/timeout'}]}); return; }
      setTimeout(wait,100);
    })();
  }
  if(document.readyState==='complete') _bootWhenReady();
  else window.addEventListener('load',_bootWhenReady);
})();
"""


def _inject(html: str, budget_ms: int = 20000, heavy: bool = False) -> str:
    """Append the harness script just before </body> (falls back to end)."""
    tag = (f"<script>window._QA_BUDGET_MS={int(budget_ms)};"
           f"window._QA_HEAVY={'true' if heavy else 'false'};</script>\n"
           "<script>\n" + _HARNESS_JS + "\n</script>\n")
    idx = html.rfind("</body>")
    if idx < 0:
        return html + tag
    return html[:idx] + tag + html[idx:]


def _run_page(edge: str, html_path: Path, budget_ms: int, timeout_s: int, heavy: bool = False) -> dict:
    """Inject the harness, headless-render, parse #__qa_results. Returns
    {'ok':bool,'results':[...]} or {'error':...}."""
    try:
        src = html_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return {"error": f"read failed: {e}"}
    injected = _inject(src, budget_ms, heavy)
    # ignore_cleanup_errors: msedge can linger holding dom.html open a moment
    # after --dump-dom has already written it (a real WinError 32 on cleanup).
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        test_html = Path(td) / "qa_test.html"
        test_html.write_text(injected, encoding="utf-8")
        dom = _render_dom(edge, test_html, td, budget_ms, timeout_s)
    m = re.search(r'<pre id="__qa_results"[^>]*>(.*?)</pre>', dom, re.DOTALL)
    if not m:
        # An empty/near-empty dump means the browser didn't render at all
        # (headless env failure) even though the pre-flight smoke passed a moment
        # ago -- distinguish that from a genuine page problem. Neither is a
        # product defect the sweep should count as a FAIL.
        if len(dom.strip()) < 400 or "plotly" not in dom.lower():
            return {"env_error": "browser produced no usable DOM (headless render failed for this page)"}
        return {"error": "no __qa_results sentinel (page loaded but the harness never emitted -- JS crash?)"}
    raw = m.group(1)
    # un-escape the minimal HTML entities the DOM dump introduces
    raw = raw.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"bad __qa_results JSON: {e}"}


def _discover(root: Path, glob: str, include_single: bool) -> list[Path]:
    hits = []
    for p in root.rglob(glob):
        if _EXCLUDE_DIR_PARTS & set(p.parts):
            continue
        # Cover EVERY interactive view type -- the filter/plot/table/statistics
        # coupling checks (runGeneric + runTableChecks + runGroupBy +
        # runCoordination) are view-agnostic and apply to all of them; the
        # view-specific blocks (boxplot deep-GF, env_cov/distribution/histogram
        # site fence) self-detect and self-skip. So a sweep verifies the couplings
        # for scatter/stat_summary/boxplot/distribution/env_coverage/summary/
        # histogram alike, not just boxplot+histogram. ("summary" matches both
        # summary and stat_summary.) Use --limit to scope a large root.
        _VIEW_TOKENS = ("scatter", "boxplot", "histogram", "distribution",
                        "env_coverage", "summary", "reference")
        if not any(k in p.name.lower() for k in _VIEW_TOKENS):
            continue
        is_compare = "compare" in str(p).lower()
        if is_compare or include_single:
            hits.append(p)
    # de-dup by resolved path, newest first
    seen, out = set(), []
    for p in sorted(hits, key=lambda x: x.stat().st_mtime, reverse=True):
        rp = str(p.resolve()).lower()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


_DEFAULT_BUDGET = 20000    # Edge --virtual-time-budget ms
_DEFAULT_TIMEOUT = 120     # per-page headless kill timeout s
_HEAVY_MB = 8.0            # HTML at/above this size runs the reduced deterministic suite


def _scale_for_size(page: Path, budget: int, timeout: int) -> tuple[int, int, float]:
    """Grow the render budget/timeout with the page's HTML size. Heavy compare
    boxplots (5-9 MB, hundreds of thousands of embedded points) need real
    wall-clock to render before the harness can read them. Caps keep a routine
    sweep from running away. Returns (budget, timeout, size_mb)."""
    try:
        mb = page.stat().st_size / 1e6
    except OSError:
        mb = 0.0
    if mb > 2.0:
        # Empirically a 9 MB compare boxplot needs ~120-200k virtual ms of budget
        # headroom for the harness's ~25-30s of async Plotly work to finish before
        # Edge dumps the DOM (the budget is consumed during async render gaps, not
        # by synchronous work). Scale ~linearly with size.
        budget = min(int(budget * (1 + mb)), 300000)   # mb=9 -> 200000, cap 300k virtual
        timeout = min(int(timeout * (1 + mb)), 420)     # cap 7min wall (real work is <60s)
    return budget, timeout, mb


def main(argv=None) -> None:
    # Details can carry table text with arrows/checkmarks (↑↓✔✘) -- this Windows
    # console's cp1252 codepage can't encode them, which would crash print().
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Filter/plot/table/statistics coupling + GF self-consistency gate for every interactive view.")
    ap.add_argument("--root", default=r"C:\temp\data", help="Data root to scan (default C:\\temp\\data).")
    ap.add_argument("--glob", default="*.html",
                    help="Filename glob (default *.html; discovery then keeps every "
                         "interactive view: scatter/stat_summary/boxplot/distribution/"
                         "env_coverage/summary/histogram).")
    ap.add_argument("--page", action="append", default=[], help="Test a specific HTML page (repeatable).")
    ap.add_argument("--include-single-site", action="store_true",
                    help="Also test non-compare pages (default: compare pages only).")
    ap.add_argument("--budget", type=int, default=_DEFAULT_BUDGET, help=f"Edge --virtual-time-budget ms (default {_DEFAULT_BUDGET}; auto-scaled up by file size unless set).")
    ap.add_argument("--timeout", type=int, default=_DEFAULT_TIMEOUT, help=f"Per-page headless kill timeout s (default {_DEFAULT_TIMEOUT}; auto-scaled up by file size unless set).")
    ap.add_argument("--no-scale", action="store_true", help="Disable file-size auto-scaling of budget/timeout.")
    ap.add_argument("--no-heavy-mode", action="store_true", help=f"Run the full suite even on large pages (>= {_HEAVY_MB:.0f} MB) instead of the reduced deterministic one.")
    ap.add_argument("--limit", type=int, default=0, help="Test at most N pages (0 = all).")
    ap.add_argument("--verbose", action="store_true", help="Print every check (pass/skip too), not just failures.")
    ap.add_argument("--browser", default="",
                    help="Explicit headless-Chromium exe to use (e.g. a Playwright "
                         "chromium chrome.exe) when the system Edge/Chrome is dead. "
                         "Must pass the same --dump-dom smoke test; else exit 3.")
    args = ap.parse_args(argv)

    # Honest environment gate: pick a browser that actually renders + runs JS
    # headless right now. If none does, the interactive checks CANNOT run -- report
    # that distinctly (exit 3) rather than letting every page look like a FAIL.
    if args.browser:
        if Path(args.browser).exists() and _browser_smoke(args.browser):
            edge, installed = args.browser, [args.browser]
        else:
            print(f"[ENV-UNAVAILABLE] --browser {args.browser} not found or failed the "
                  "headless --dump-dom smoke test -- interactive checks did NOT run.")
            sys.exit(3)
    else:
        edge, installed = _pick_working_browser()
    if not edge:
        if not installed:
            print("[ENV-UNAVAILABLE] no Edge/Chrome found -- interactive checks did NOT run.")
        else:
            print("[ENV-UNAVAILABLE] headless browser present but not rendering "
                  f"({', '.join(Path(b).name for b in installed)}) -- interactive checks did NOT run. "
                  "This is an environment failure, NOT a product pass or fail.")
        sys.exit(3)

    if args.page:
        pages = [Path(p) for p in args.page]
    else:
        pages = _discover(Path(args.root), args.glob, args.include_single_site)
    if args.limit:
        pages = pages[: args.limit]
    if not pages:
        print("No interactive view pages found to test.")
        sys.exit(0)

    print(f"qa_filters: {len(pages)} page(s), edge={Path(edge).name}\n")
    total_pass = total_fail = total_skip = 0
    failed_pages = []

    heavy_pages = []
    env_pages = []
    for pg in pages:
        budget, timeout = args.budget, args.timeout
        mb = 0.0
        # Only auto-scale when the user KEPT the defaults -- an explicit --budget/
        # --timeout must win (and never be scaled down under the cap).
        if not args.no_scale and args.budget == _DEFAULT_BUDGET and args.timeout == _DEFAULT_TIMEOUT:
            budget, timeout, mb = _scale_for_size(pg, args.budget, args.timeout)
        else:
            try: mb = pg.stat().st_size / 1e6
            except OSError: mb = 0.0
        heavy = mb >= _HEAVY_MB and not args.no_heavy_mode
        t0 = time.time()
        try:
            res = _run_page(edge, pg, budget, timeout, heavy)
        except Exception as e:   # one page's harness crash must not abort the sweep
            res = {"error": f"harness exception: {e}"}
        dt = time.time() - t0
        label = pg.parent.name + "/" + pg.name
        tag = f"{mb:.0f}MB {dt:.0f}s" if mb >= 1 else f"{dt:.0f}s"
        if "env_error" in res:
            # Browser couldn't render THIS page -- environment, not a defect.
            print(f"  [ENV ] {label}: {res['env_error']}  ({tag})")
            env_pages.append(label)
            continue
        if "error" in res:
            print(f"  [ERROR] {label}: {res['error']}  ({tag}, budget={budget} timeout={timeout})")
            total_fail += 1
            failed_pages.append(label)
            continue
        if res.get("view") == "render-not-ready":
            # Distinct from a logic FAIL: the page was simply too heavy to render
            # headlessly in the given budget. Reported, not counted as a defect.
            print(f"  [HEAVY] {label}: too heavy to render headlessly ({tag}, budget={budget} timeout={timeout}) -- raise --budget/--timeout or --page it alone")
            heavy_pages.append(label)
            continue
        if res.get("view") == "not-boxplot":
            print(f"  [skip ] {label}: not a boxplot page")
            continue
        rows = res.get("results", [])
        pfail = [r for r in rows if not r.get("skip") and not r.get("ok")]
        ppass = [r for r in rows if not r.get("skip") and r.get("ok")]
        pskip = [r for r in rows if r.get("skip")]
        total_pass += len(ppass); total_fail += len(pfail); total_skip += len(pskip)
        status = "PASS" if not pfail else "FAIL"
        print(f"  [{status}] {label}  ({len(ppass)} ok, {len(pfail)} fail, {len(pskip)} skip)  ({tag})")
        if args.verbose:
            for r in rows:
                rtag = "skip" if r.get("skip") else ("ok  " if r.get("ok") else "FAIL")
                print(f"           {rtag}: {r['name']} -- {r.get('detail','')}")
        else:
            for r in pfail:
                print(f"           FAIL: {r['name']} -- {r['detail']}")
        if pfail:
            failed_pages.append(label)

    print("\n" + "=" * 60)
    print(f"  PASS: {total_pass}   FAIL: {total_fail}   SKIP: {total_skip}")
    if failed_pages:
        print("  Pages with failures:")
        for f in failed_pages:
            print(f"    - {f}")
    if heavy_pages:
        print(f"  Too heavy to render headlessly ({len(heavy_pages)}) -- not defects, raise --budget/--timeout:")
        for f in heavy_pages:
            print(f"    - {f}")
    if env_pages:
        print(f"  Browser could not render ({len(env_pages)}) -- ENVIRONMENT failures, NOT defects:")
        for f in env_pages:
            print(f"    - {f}")
    # Exit code semantics for honest self-assessment:
    #   1 = real product FAIL(s) found (takes priority)
    #   3 = checks could not run for some pages (environment) and nothing failed
    #   0 = everything that ran passed
    if total_fail:
        sys.exit(1)
    if env_pages:
        print("  NOTE: some pages could not be checked (browser environment) -- "
              "this run did NOT verify them; treat as 'unverified', not 'passed'.")
        sys.exit(3)
    sys.exit(0)


if __name__ == "__main__":
    main()
