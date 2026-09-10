#!/usr/bin/env python3
"""
qa_filters.py -- automated filter / Global-Filter self-consistency gate for the
interactive boxplot pages (single-site AND cross-site compare).

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
from pathlib import Path

_EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_EXCLUDE_DIR_PARTS = {"backup", ".git", "__pycache__"}


def _find_edge() -> str | None:
    for cand in _EDGE_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


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
  // Generic invariants for ANY view. `runViewSetup` optionally primes the view
  // (e.g. box: turn on Show Points) and returns a label. Returns pushes into R.
  function runGeneric(R,chk,skip){
    var S0=plotSig();
    chk('baseline-not-blank', S0!=='[]' && JSON.parse(S0).some(function(s){return parseInt(s.split(':').pop(),10)>0;}), 'sig='+S0.slice(0,120));
    // Per distinct filter class, exercise ONE checkbox: off -> on must restore
    // the exact baseline (reversibility), and unchecking should change something
    // (a filter that does nothing is itself suspicious -- reported soft).
    var boxes=filterBoxes(), byClass={};
    boxes.forEach(function(c){var k=(c.className||'').trim(); if(!byClass[k]) byClass[k]=c;});
    var classes=Object.keys(byClass);
    if(!classes.length) skip('filter-reversibility','no recognised filter checkboxes');
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
      var phantom=Object.keys(tc).filter(function(c){ return c!=='All' && names.indexOf(c)<0
          && !names.some(function(n){return n.indexOf(c)===0;}); });
      chk('table-conds-are-plotted', phantom.length===0, phantom.length?('not plotted: '+phantom.slice(0,4).join(' | ')):('conds='+Object.keys(tc).length));
    } else skip('table-conds-are-plotted','no Condition column');
    // (a3) reverse: every box/histogram PRIMARY trace has a table row (clean by type)
    var gd=_gd(), prim=(gd&&gd.data?gd.data:[]).filter(function(t){return t.type==='box'||t.type==='histogram';})
        .map(function(t){return _norm(t.name||'');}).filter(function(n){return n;});
    if(prim.length){
      var missing=prim.filter(function(n){return !tc[n];});
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
    function digest(){ var T2=readTable(); if(!T2) return null;
      return T2.rows.length+'|'+T2.rows.map(function(r){return r.join('␟');}).join('‖'); }
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
      // Generic invariants for every view.
      runGeneric(R,chk,skip);
      // Table cross-check for every view that has a Statistics/Results table.
      try{ runTableChecks(R,chk,skip); }catch(e){ chk('TABLE-HARNESS-ERROR',false,String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); }
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
      function reset(){ if(typeof clearEverything!=='undefined'){clearEverything();} if(typeof clearGlobalFilter!=='undefined'){clearGlobalFilter();} ensurePts(); }

      reset();
      var P0=cnt(); var base=ppPts();
      chk('baseline-not-blank', P0>0, 'P0='+P0);
      if(P0===0){emit({view:'boxplot',results:R});return;}

      // ---- outliers-GF-precise ----
      if(typeof _collectOutliers!=='undefined'&&typeof applyGlobalFilter!=='undefined'){
        var outs=_collectOutliers(getSelectedConds(),getSelectedTemps(),getYFilter(),getSelectedBoxSerials());
        // outlier identity by (baseSerial, condition, freqLabel)
        var outId={}; outs.forEach(function(o){ outId[(typeof _boxBaseSerial!=='undefined'?_boxBaseSerial(o.serial):o.serial)+'||'+o.cond+'||'+(o.freqLabel!=null?o.freqLabel:o.freq)]=1; });
        var b0=ppPts();
        applyGlobalFilter(); update();
        var a0=ppPts(); var P1=a0.length;
        chk('outliers-GF-not-blank', P1>0, 'P1='+P1+' P0='+P0);
        var rem=removedBetween(b0,a0);
        // every removed point must belong to an outlier (serial,cond,freqLabel)
        // identity -- catches whole-DUT / cross-frequency over-exclusion.
        var outside=rem.filter(function(k){var p=k.split('||'); return !outId[p[1]+'||'+p[0]+'||'+p[2]];});
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
            allSer.forEach(function(s){setCbx(serCbxCls,s,s===ser);});   // narrow to just `ser`
            setFilterAsGf();
            allSer.forEach(function(s){setCbx(serCbxCls,s,true);});      // restore serial selection
            chk('filter-GF-whole-dut-excludes-serial', ppPts().every(function(p){return p.s!==ser;}),
                'ser='+ser+' leftover='+ppPts().filter(function(p){return p.s===ser;}).length);
            chk('filter-GF-keeps-others', cnt()===P0-serCnt0, 'expected='+(P0-serCnt0)+' got='+cnt());
            clearGlobalFilter(); ensurePts();
            chk('clear-after-filter-GF-restores', cnt()===P0, 'after='+cnt()+' P0='+P0);
          }
        } else skip('deselect-serial','serial checkbox not settable');
      } else skip('deselect-serial','no serial checkboxes / single serial');

      // ---- reset-restores ----
      reset();
      chk('reset-restores', cnt()===P0, 'after='+cnt()+' P0='+P0);

    }catch(e){ chk('HARNESS-ERROR', false, String(e)+' @ '+String((e&&e.stack||'').split('\n')[1]||'')); }
    emit({view:'boxplot', results:R});
  }
  if(document.readyState==='complete') setTimeout(run,400);
  else window.addEventListener('load',function(){setTimeout(run,400);});
})();
"""


def _inject(html: str) -> str:
    """Append the harness script just before </body> (falls back to end)."""
    tag = "<script>\n" + _HARNESS_JS + "\n</script>\n"
    idx = html.rfind("</body>")
    if idx < 0:
        return html + tag
    return html[:idx] + tag + html[idx:]


def _run_page(edge: str, html_path: Path, budget_ms: int, timeout_s: int) -> dict:
    """Inject the harness, headless-render, parse #__qa_results. Returns
    {'ok':bool,'results':[...]} or {'error':...}."""
    try:
        src = html_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return {"error": f"read failed: {e}"}
    injected = _inject(src)
    # ignore_cleanup_errors: msedge can linger holding dom.html open a moment
    # after --dump-dom has already written it (a real WinError 32 on cleanup).
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        test_html = Path(td) / "qa_test.html"
        test_html.write_text(injected, encoding="utf-8")
        dom_path = Path(td) / "dom.html"
        with dom_path.open("w", encoding="utf-8") as dom_f:
            proc = subprocess.Popen(
                [edge, "--headless", "--disable-gpu", "--disable-crash-reporter",
                 f"--virtual-time-budget={budget_ms}", f"--user-data-dir={td}",
                 "--dump-dom", str(test_html)],
                stdout=dom_f, stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=10)   # let it actually die + release dom.html
                except subprocess.TimeoutExpired:
                    pass
        dom = dom_path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'<pre id="__qa_results"[^>]*>(.*?)</pre>', dom, re.DOTALL)
    if not m:
        return {"error": "no __qa_results sentinel (page did not run / render timed out)"}
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
        if "boxplot" not in p.name.lower():
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


def main(argv=None) -> None:
    # Details can carry table text with arrows/checkmarks (↑↓✔✘) -- this Windows
    # console's cp1252 codepage can't encode them, which would crash print().
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Filter/GF self-consistency gate for boxplot pages.")
    ap.add_argument("--root", default=r"C:\temp\data", help="Data root to scan (default C:\\temp\\data).")
    ap.add_argument("--glob", default="*boxplot*.html", help="Filename glob (default *boxplot*.html).")
    ap.add_argument("--page", action="append", default=[], help="Test a specific HTML page (repeatable).")
    ap.add_argument("--include-single-site", action="store_true",
                    help="Also test non-compare boxplots (default: compare pages only).")
    ap.add_argument("--budget", type=int, default=20000, help="Edge --virtual-time-budget ms (default 20000).")
    ap.add_argument("--timeout", type=int, default=120, help="Per-page headless kill timeout s (default 120).")
    ap.add_argument("--limit", type=int, default=0, help="Test at most N pages (0 = all).")
    ap.add_argument("--verbose", action="store_true", help="Print every check (pass/skip too), not just failures.")
    args = ap.parse_args(argv)

    edge = _find_edge()
    if not edge:
        print("[FATAL] msedge.exe not found -- cannot run headless self-tests.")
        sys.exit(2)

    if args.page:
        pages = [Path(p) for p in args.page]
    else:
        pages = _discover(Path(args.root), args.glob, args.include_single_site)
    if args.limit:
        pages = pages[: args.limit]
    if not pages:
        print("No boxplot pages found to test.")
        sys.exit(0)

    print(f"qa_filters: {len(pages)} page(s), edge={Path(edge).name}\n")
    total_pass = total_fail = total_skip = 0
    failed_pages = []

    for pg in pages:
        try:
            res = _run_page(edge, pg, args.budget, args.timeout)
        except Exception as e:   # one page's harness crash must not abort the sweep
            res = {"error": f"harness exception: {e}"}
        label = pg.parent.name + "/" + pg.name
        if "error" in res:
            print(f"  [ERROR] {label}: {res['error']}")
            total_fail += 1
            failed_pages.append(label)
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
        print(f"  [{status}] {label}  ({len(ppass)} ok, {len(pfail)} fail, {len(pskip)} skip)")
        if args.verbose:
            for r in rows:
                tag = "skip" if r.get("skip") else ("ok  " if r.get("ok") else "FAIL")
                print(f"           {tag}: {r['name']} -- {r.get('detail','')}")
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
    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
