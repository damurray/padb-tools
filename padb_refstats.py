"""Reference Statistics view -- a filter-coupled "1000 ft view" of a scatter
(Type=80) dataset: overall + pass/fail, Pareto by group, and per-group descriptive
statistics (n / mean / std / median / min / max / IQR-outliers), all recomputed
live under the standard filter bar so the Pareto plot and the tables always match.

Descriptive stats are computed CLIENT-SIDE over the full embedded per-point data
(this view aggregates -- it never plots 40k markers -- so it does NOT decimate;
that's the whole point vs. a client table over a decimated scatter, which would
misreport counts). Pass/fail precedence: the pod's own status field (e.g.
"Test Run Status": P/F) if present, else CSV Upper/Lower limits, else a manual
override limit (for a compare with no spec).

Increment 1: filter bar + Overall + Pareto + per-group descriptive table.
(Distribution + outlier table, and the auto-filter before/after impact, follow.)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from padb_plots import (
    _get_plotlyjs, _checkbox_panel, _detect_group_cols, _short_x_label,
    _floor_dec, _ceil_dec,
)

# Keyword match for the pod's pass/fail status field among the group dims (the JS
# validates the values actually look like pass/fail before trusting it).
_STATUS_KWS = ("status", "pass", "fail", "result", "verdict", "disposition")

_SERIAL_CHK_CAP = 400  # don't render a checkbox panel for more serials than this


def _build_reference_stats_html(df: pd.DataFrame, cfg: dict, title: str) -> str:
    y_label = cfg.get("y_label", "Value")
    x_label = cfg.get("x_label", "Frequency (MHz)")
    x_unit = cfg.get("x_unit", "MHz")
    x_short = _short_x_label(x_label)

    group_cols = _detect_group_cols(df)  # [(col, label)] cardinality>1
    # Split into: real condition/group dims (from Group text) vs Serial/Station/Temp.
    grp_dims = [(c, l) for c, l in group_cols if c.startswith("_grp_")]
    status_col = next((c for c, l in grp_dims
                       if any(k in l.lower() for k in _STATUS_KWS)), None)

    has_serial = "Serial" in df.columns and df["Serial"].replace("", pd.NA).nunique(dropna=True) > 1
    n_serial = int(df["Serial"].replace("", pd.NA).nunique(dropna=True)) if has_serial else 0
    serial_panel = has_serial and n_serial <= _SERIAL_CHK_CAP
    temps = sorted(str(t) for t in df["Temperature"].dropna().unique()) if "Temperature" in df.columns else []
    has_temp = len(temps) > 1

    # ---- per-point records (NOT decimated -- stats need every point) ----
    json_cols = ["Value", "Frequency_MHz"]
    if "Temperature" in df.columns:
        json_cols.append("Temperature")
    if has_serial:
        json_cols.append("Serial")
    for c in ("Upper_Limit", "Lower_Limit"):
        if c in df.columns:
            json_cols.append(c)
    json_cols += [c for c, _ in grp_dims]
    # Column-oriented embed (arrays, not per-row dicts) so column NAMES aren't
    # repeated 40k times -- an order-of-magnitude smaller file. JS reconstitutes
    # row objects once on load. NaN embeds as the JS literal NaN (this is a JS
    # source literal, not JSON.parse'd), which is fine; limits become null.
    cols_present = [c for c in json_cols if c in df.columns]
    cols_obj = {}
    for c in cols_present:
        s = df[c]
        if c in ("Upper_Limit", "Lower_Limit", "Value", "Frequency_MHz"):
            cols_obj[c] = [None if pd.isna(v) else float(v) for v in s]
        else:
            cols_obj[c] = [None if pd.isna(v) else str(v) for v in s]

    # ---- filter bar ----
    filt_parts = []
    for c, lbl in grp_dims:
        vals = sorted(str(v) for v in df[c].dropna().unique())
        if 1 < len(vals) <= 200:
            filt_parts.append(_checkbox_panel(c, lbl, vals))
    if serial_panel:
        filt_parts.append(_checkbox_panel("Serial", "Serial",
                                          sorted(str(v) for v in df["Serial"].dropna().unique() if str(v))))
    temp_bar = ""
    if has_temp:
        temp_items = "".join(
            f'<label class="fitem"><input type="checkbox" class="fchk" data-col="Temperature"'
            f' value="{t}" checked onchange="chkChanged(\'Temperature\')">{t}</label>' for t in temps)
        temp_bar = (
            '<div class="filter-wrap"><button class="filter-btn" onclick="togglePanel(\'Temperature\')">'
            'Temperature&thinsp;<span id="badge_Temperature" class="badge"></span>&#9662;</button>'
            '<div class="filter-panel" id="panel_Temperature">'
            '<label class="fitem fall"><input type="checkbox" id="all_Temperature" checked'
            ' onchange="toggleAll(\'Temperature\')"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{temp_items}</div></div>')

    fmin = _floor_dec(float(df["Frequency_MHz"].min()), 3)
    fmax = _ceil_dec(float(df["Frequency_MHz"].max()), 3)

    # Group-by selector (per-group table + pareto). Default: status field if present,
    # else the fewest-cardinality real dim, else Serial.
    gb_opts_src = grp_dims + ([("Serial", "Serial")] if has_serial else [])
    if status_col:
        default_gb = status_col
    elif grp_dims:
        default_gb = min(grp_dims, key=lambda cl: df[cl[0]].replace("", pd.NA).nunique(dropna=True))[0]
    elif has_serial:
        default_gb = "Serial"
    else:
        default_gb = ""
    gb_opts = "".join(
        f'<option value="{c}"{" selected" if c == default_gb else ""}>{l}</option>'
        for c, l in gb_opts_src) or '<option value="">(none)</option>'

    has_lim = (("Upper_Limit" in df.columns and df["Upper_Limit"].notna().any())
               or ("Lower_Limit" in df.columns and df["Lower_Limit"].notna().any()))

    constants = "\n".join([
        f"var COLS={json.dumps(cols_obj)};",
        f"var GROUP_COLS={json.dumps([[c, l] for c, l in gb_opts_src])};",
        f"var STATUS_COL={json.dumps(status_col)};",
        f"var HAS_LIMITS={json.dumps(bool(has_lim))};",
        f"var Y_LABEL={json.dumps(y_label)};",
        f"var X_LABEL={json.dumps(x_label)};",
        f"var X_SHORT={json.dumps(x_short)};",
        f"var X_UNIT={json.dumps(x_unit)};",
        f"var FREQ_MIN={fmin!r};",
        f"var FREQ_MAX={fmax!r};",
        f"var TITLE={json.dumps(title)};",
    ])

    ctrl = (
        '<div class="ctrl-bar">\n'
        '  <b>Filters:</b>\n  ' + "\n  ".join(filt_parts) + "\n"
        + ("  " + temp_bar + "\n" if temp_bar else "")
        + '  <span class="sep"></span>\n'
        f'  <label>Freq min ({x_unit}): <input type="number" step="any" id="f_lo" value="{fmin}"'
        ' oninput="update()" style="width:110px"></label>\n'
        f'  <label>Freq max ({x_unit}): <input type="number" step="any" id="f_hi" value="{fmax}"'
        ' oninput="update()" style="width:110px"></label>\n'
        '  <span class="sep"></span>\n'
        f'  <label>Group by: <select id="groupby" onchange="update()">{gb_opts}</select></label>\n'
        '  <label title="Used to compute pass/fail only where the data has no Upper/Lower limit'
        ' (e.g. a compare with no spec). Leave blank to skip.">&nbsp;override lim'
        ' Hi<input type="number" step="any" id="ovr_hi" style="width:80px" oninput="update()">'
        ' Lo<input type="number" step="any" id="ovr_lo" style="width:80px" oninput="update()"></label>\n'
        '  <button class="reset-btn" onclick="resetFilters()">Reset</button>\n'
        '</div>\n'
    )

    style = """
<style>
 body{font-family:Segoe UI,Arial,sans-serif;margin:14px;color:#222}
 h1{font-size:1.3em;margin:0 0 8px}
 .ctrl-bar{background:#f6f8fb;border:1px solid #dde3ec;border-radius:5px;padding:8px 10px;margin-bottom:10px;line-height:2.1}
 .sep{display:inline-block;width:1px;height:16px;background:#ccd;margin:0 8px;vertical-align:middle}
 .filter-wrap{position:relative;display:inline-block}
 .filter-btn,.reset-btn{background:#eef2f8;border:1px solid #b8c2d4;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:13px}
 .reset-btn{background:#f5f5f5;border-color:#888}
 .filter-panel{display:none;position:absolute;z-index:20;background:#fff;border:1px solid #b8c2d4;border-radius:4px;
   padding:6px;max-height:320px;overflow:auto;min-width:160px;box-shadow:0 2px 8px rgba(0,0,0,.15)}
 .filter-panel.open{display:block}
 .fitem{display:block;font-size:13px;white-space:nowrap;padding:1px 4px}
 .fall{font-weight:600}.fdiv{margin:4px 0;border:none;border-top:1px solid #eee}
 .badge{font-size:11px;color:#a60;font-weight:600}
 .sect{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:#888;border-bottom:1px solid #ddd;
   padding-bottom:3px;margin:16px 0 6px}
 table.stbl{border-collapse:collapse;font-size:12.5px}
 table.stbl th,table.stbl td{border:1px solid #e3e3e3;padding:2px 8px;text-align:right}
 table.stbl th{background:#f2f5fa;text-align:center}
 table.stbl td.k{text-align:left}
 .ov td{padding:2px 14px 2px 0}
 .fail{color:#c04000;font-weight:600}
</style>
"""

    body = (
        f"<h1>{title}</h1>\n" + ctrl
        + '<div class="sect">Overall (current filter view)</div>\n'
        + '<div id="overall"></div>\n'
        + '<div class="sect">Pareto by group</div>\n'
        + '<div id="pareto" style="max-width:1000px"></div>\n'
        + '<div class="sect">Per-group descriptive statistics</div>\n'
        + '<div id="grouptbl" style="overflow:auto"></div>\n'
    )

    return (
        "<!DOCTYPE html>\n<html>\n<head>\n<meta charset='utf-8'>\n"
        f"<title>{title}</title>\n"
        f"<script>{_get_plotlyjs()}</script>\n{style}\n</head>\n<body>\n"
        + body
        + f"<script>\n{constants}\n{_REF_STATS_JS}</script>\n</body>\n</html>\n"
    )


_REF_STATS_JS = r"""
/* Reconstitute per-row objects once from the column-oriented embed. */
var DATA=[];
(function(){var ks=Object.keys(COLS); var n=ks.length?COLS[ks[0]].length:0;
  for(var i=0;i<n;i++){var o={}; for(var j=0;j<ks.length;j++)o[ks[j]]=COLS[ks[j]][i]; DATA.push(o);}})();
/* ---------- filter machinery (mirrors _checkbox_panel's expectations) ---------- */
function togglePanel(col){var p=document.getElementById('panel_'+col);if(!p)return;
  p.classList.toggle('open');}
function _chks(col){return Array.prototype.slice.call(document.querySelectorAll('.fchk[data-col="'+col+'"]'));}
function toggleAll(col){var on=document.getElementById('all_'+col).checked;_chks(col).forEach(function(c){c.checked=on;});chkChanged(col);}
function chkChanged(col){
  var boxes=_chks(col),sel=boxes.filter(function(c){return c.checked;}).length;
  var all=document.getElementById('all_'+col); if(all)all.checked=(sel===boxes.length);
  var b=document.getElementById('badge_'+col); if(b)b.textContent=(sel<boxes.length)?(sel+'/'+boxes.length):'';
  update();
}
function _selected(col){var s={};_chks(col).forEach(function(c){if(c.checked)s[c.value]=1;});return s;}
document.addEventListener('click',function(e){ // close open panels on outside click
  document.querySelectorAll('.filter-panel.open').forEach(function(p){
    if(!p.parentNode.contains(e.target))p.classList.remove('open');});
});

/* ---------- filtering ---------- */
function _filterCols(){ // every _grp_/Serial/Temperature column that has a checkbox panel
  var cols={}; document.querySelectorAll('.fchk').forEach(function(c){cols[c.getAttribute('data-col')]=1;});
  return Object.keys(cols);
}
function applyFilters(rows){
  var cols=_filterCols(), sel={}; cols.forEach(function(c){sel[c]=_selected(c);});
  var flo=parseFloat(document.getElementById('f_lo').value); if(isNaN(flo))flo=-Infinity;
  var fhi=parseFloat(document.getElementById('f_hi').value); if(isNaN(fhi))fhi=Infinity;
  return rows.filter(function(r){
    if(r.Frequency_MHz<flo||r.Frequency_MHz>fhi)return false;
    for(var i=0;i<cols.length;i++){var c=cols[i];var v=(c==='Serial'||c==='Temperature')?r[c]:r[c];
      if(v==null)v=''; if(!sel[c][String(v)])return false;}
    return true;
  });
}

/* ---------- pass/fail: status field, else CSV limits, else override ---------- */
var _PASS={P:1,PASS:1,PASSED:1,'1':1,TRUE:1,OK:1,GOOD:1,Y:1,YES:1};
var _FAIL={F:1,FAIL:1,FAILED:1,'0':1,FALSE:1,BAD:1,N:1,NO:1};
function _ovr(id){var e=document.getElementById(id);if(!e)return null;var v=parseFloat(e.value);return isNaN(v)?null:v;}
function _pfMode(rows){
  // decide which source to use across the filtered set (consistent per view)
  if(STATUS_COL){var p=0,f=0,u=0;rows.forEach(function(r){var raw=r[STATUS_COL];if(raw==null||raw==='')return;
      var t=String(raw).trim().toUpperCase(); if(_PASS[t])p++; else if(_FAIL[t])f++; else u++;});
    if((p+f)>0&&u<=(p+f))return 'status';}
  var oHi=_ovr('ovr_hi'),oLo=_ovr('ovr_lo');
  var hasAny=rows.some(function(r){return r.Upper_Limit!=null||r.Lower_Limit!=null;});
  if(hasAny)return 'limits';
  if(oHi!=null||oLo!=null)return 'override';
  return null;
}
function _pf(r,mode){ // 'P' / 'F' / null(unknown)
  if(mode==='status'){var t=String(r[STATUS_COL]==null?'':r[STATUS_COL]).trim().toUpperCase();
    return _PASS[t]?'P':_FAIL[t]?'F':null;}
  var oHi=_ovr('ovr_hi'),oLo=_ovr('ovr_lo');
  var hi=(r.Upper_Limit!=null)?r.Upper_Limit:oHi, lo=(r.Lower_Limit!=null)?r.Lower_Limit:oLo;
  if(hi==null&&lo==null)return null;
  return ((hi!=null&&r.Value>hi)||(lo!=null&&r.Value<lo))?'F':'P';
}
function _pfSourceLabel(mode){return mode==='status'?('status field ('+(STATUS_COL||'').slice(5)+')'):
  mode==='limits'?'CSV limits':mode==='override'?'override limit':'n/a (no status/limits)';}

/* ---------- descriptive stats ---------- */
function _stats(vals){
  var n=vals.length; if(!n)return {n:0};
  var s=vals.slice().sort(function(a,b){return a-b;});
  function pc(p){var i=(p/100)*(n-1),li=Math.floor(i);return li+1<n?s[li]+(s[li+1]-s[li])*(i-li):s[li];}
  var sum=0;for(var i=0;i<n;i++)sum+=s[i];var mean=sum/n;
  var v=0;for(i=0;i<n;i++)v+=(s[i]-mean)*(s[i]-mean);var sd=n>1?Math.sqrt(v/(n-1)):0;
  var q1=pc(25),q3=pc(75),iqr=q3-q1,loF=q1-1.5*iqr,hiF=q3+1.5*iqr,out=0;
  for(i=0;i<n;i++)if(s[i]>hiF||s[i]<loF)out++;
  return {n:n,mean:mean,sd:sd,med:pc(50),min:s[0],max:s[n-1],out:out};
}
function _fmt(x,d){return (x==null||isNaN(x))?'—':x.toFixed(d==null?4:d);}

function update(){
  var rows=applyFilters(DATA);
  var mode=_pfMode(rows);
  var vals=[];rows.forEach(function(r){if(r.Value!=null)vals.push(r.Value);});
  var st=_stats(vals);
  // ---- Overall ----
  var pass=0,fail=0,unk=0;
  if(mode)rows.forEach(function(r){var v=_pf(r,mode);if(v==='P')pass++;else if(v==='F')fail++;else unk++;});
  var tot=pass+fail,fpct=tot?(100*fail/tot).toFixed(1):'0.0';
  var oh='<table class="stbl ov">';
  function orow(k,v){return '<tr><td class="k" style="color:#555">'+k+'</td><td class="k" style="font-weight:600">'+v+'</td></tr>';}
  oh+=orow('Total points (filtered)',rows.length.toLocaleString());
  if(mode){oh+=orow('Pass',pass.toLocaleString());
    oh+=orow('Fail','<span'+(fail>0?' class="fail"':'')+'>'+fail.toLocaleString()+' ('+fpct+'%)</span>');
    if(unk)oh+=orow('Status not recognized',unk.toLocaleString());
    oh+=orow('Pass/Fail source',_pfSourceLabel(mode));}
  else oh+=orow('Pass/Fail','<span style="color:#a05000">no status field and no limits — set an override limit</span>');
  oh+=orow('Outliers (1.5×IQR)',st.n?(st.out.toLocaleString()+' ('+(100*st.out/st.n).toFixed(1)+'%)'):'—');
  oh+=orow('Value min / mean / median / max',_fmt(st.min)+' / '+_fmt(st.mean)+' / '+_fmt(st.med)+' / '+_fmt(st.max));
  oh+=orow('Std dev',_fmt(st.sd));
  oh+='</table>';
  document.getElementById('overall').innerHTML=oh;
  // ---- group aggregation ----
  var gb=document.getElementById('groupby').value;
  var groups={};
  rows.forEach(function(r){var key=(gb&&r[gb]!=null&&r[gb]!=='')?String(r[gb]):(gb?'(blank)':'(all)');
    var g=groups[key]||(groups[key]={vals:[],fail:0,rows:0});
    if(r.Value!=null)g.vals.push(r.Value); g.rows++;
    if(mode){var v=_pf(r,mode);if(v==='F')g.fail++;}});
  var keys=Object.keys(groups);
  // ---- Pareto (fail count if pass/fail available, else outlier count) ----
  var useFail=!!mode;
  var paretoData=keys.map(function(k){var g=groups[k];
    var metric=useFail?g.fail:_stats(g.vals).out; return {k:k,metric:metric,n:g.rows};});
  paretoData.sort(function(a,b){return b.metric-a.metric;});
  var top=paretoData.slice(0,30);
  var cum=0,total=paretoData.reduce(function(a,d){return a+d.metric;},0)||1,cumPct=[];
  top.forEach(function(d){cum+=d.metric;cumPct.push(100*cum/total);});
  var traces=[{type:'bar',x:top.map(function(d){return d.k;}),y:top.map(function(d){return d.metric;}),
    name:(useFail?'Fail':'Outlier')+' count',marker:{color:'#c0504d'}},
    {type:'scatter',x:top.map(function(d){return d.k;}),y:cumPct,yaxis:'y2',name:'cumulative %',
     mode:'lines+markers',line:{color:'#4472c4'}}];
  var glabel=(GROUP_COLS.filter(function(c){return c[0]===gb;})[0]||[gb,gb])[1];
  Plotly.react('pareto',traces,{margin:{t:26,r:60,b:120,l:50},height:340,
    title:{text:(useFail?'Fails':'Outliers')+' by '+glabel+(top.length<paretoData.length?(' (top '+top.length+' of '+paretoData.length+')'):''),font:{size:13}},
    xaxis:{tickangle:-40,automargin:true},yaxis:{title:(useFail?'Fail':'Outlier')+' count'},
    yaxis2:{overlaying:'y',side:'right',range:[0,105],title:'cum %',showgrid:false},
    legend:{orientation:'h',y:1.12}},{responsive:true,displayModeBar:false});
  // ---- per-group descriptive table ----
  var gkeys=keys.slice().sort(function(a,b){return groups[b].rows-groups[a].rows;});
  var th='<table class="stbl"><thead><tr><th class="k">'+glabel+'</th><th>n</th><th>Mean</th><th>Std</th>'+
    '<th>Median</th><th>Min</th><th>Max</th><th>Outliers</th>'+(mode?'<th>Fail</th><th>%Fail</th>':'')+'</tr></thead><tbody>';
  gkeys.forEach(function(k){var g=groups[k],s=_stats(g.vals);
    th+='<tr><td class="k">'+k+'</td><td>'+s.n+'</td><td>'+_fmt(s.mean)+'</td><td>'+_fmt(s.sd)+'</td>'+
      '<td>'+_fmt(s.med)+'</td><td>'+_fmt(s.min)+'</td><td>'+_fmt(s.max)+'</td><td>'+(s.out||0)+'</td>'+
      (mode?('<td'+(g.fail>0?' class="fail"':'')+'>'+g.fail+'</td><td>'+(g.rows?(100*g.fail/g.rows).toFixed(1):'0.0')+'%</td>'):'')+'</tr>';});
  th+='</tbody></table>';
  document.getElementById('grouptbl').innerHTML=th;
}

function resetFilters(){
  document.querySelectorAll('.fchk').forEach(function(c){c.checked=true;});
  document.querySelectorAll('[id^="all_"]').forEach(function(c){c.checked=true;});
  document.querySelectorAll('.badge').forEach(function(b){b.textContent='';});
  document.getElementById('f_lo').value=FREQ_MIN; document.getElementById('f_hi').value=FREQ_MAX;
  document.getElementById('ovr_hi').value=''; document.getElementById('ovr_lo').value='';
  update();
}
window.addEventListener('DOMContentLoaded',update);
"""
