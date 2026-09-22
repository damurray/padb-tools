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
Increment 2: value-distribution histogram (pass/fail-coloured when a pass/fail
mode exists) + an outlier-points table (the actual 1.5xIQR-fence points behind
the Overall count, sorted by deviation, with CSV export). Also honours the shared
cross-view Global Filter, point-precise, so a DUT cleaned elsewhere drops here too.
Increment 3: auto-filter before/after impact -- reuses the SAME shared engine the
plot views use (_afScorer/_afCompute/_afAnalyze/_afRecommend) to preview how a
chosen basis+level would change the headline numbers (total/pass/fail/outliers/
mean/median/std). Preview only -- it never writes the GF; the real apply stays on
the plot views and flows back here via the GF.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from padb_plots import (
    _get_plotlyjs, _checkbox_panel, _detect_group_cols, _short_x_label,
    _floor_dec, _ceil_dec, _freq_label_map, _AUTO_FILTER_SHARED_JS,
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
    grp_all = [(c, l) for c, l in group_cols if c.startswith("_grp_")]
    # Serial usually lives in the Group text (a "_grp_Serial Number" column), not a
    # dedicated CSV "Serial" column -- the same case _grpSerialFromRow handles on the
    # other views. Detect that serial-like _grp_ col DIRECTLY from df (not the
    # cardinality-capped detect list, so a >100-DUT pod still gets a serial for GF),
    # use it as the effective Serial, and drop it from the condition dims so it isn't
    # double-counted (it's the Serial filter, not a "condition").
    _SER_KWS = ("serial", "unit id", "dut id", "s/n", "sn")
    serial_grp_col = next((c for c in df.columns if c.startswith("_grp_")
                           and any(k in c[5:].lower() for k in _SER_KWS)), None)
    grp_dims = [(c, l) for c, l in grp_all if c != serial_grp_col]
    status_col = next((c for c, l in grp_dims
                       if any(k in l.lower() for k in _STATUS_KWS)), None)

    if "Serial" in df.columns and df["Serial"].replace("", pd.NA).nunique(dropna=True) > 1:
        serial_series = df["Serial"].astype(str)
    elif serial_grp_col is not None:
        serial_series = df[serial_grp_col].fillna("").astype(str)
    else:
        serial_series = None
    has_serial = serial_series is not None and serial_series.replace("", pd.NA).nunique(dropna=True) > 1
    n_serial = int(serial_series.replace("", pd.NA).nunique(dropna=True)) if has_serial else 0
    serial_panel = has_serial and n_serial <= _SERIAL_CHK_CAP
    temps = sorted(str(t) for t in df["Temperature"].dropna().unique()) if "Temperature" in df.columns else []
    has_temp = len(temps) > 1

    # ---- per-point records (NOT decimated -- stats need every point) ----
    json_cols = ["Value", "Frequency_MHz"]
    if "Temperature" in df.columns:
        json_cols.append("Temperature")
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
    # Per-row frequency-BOX label (adaptive-precision, same map every other view
    # uses) so the Global Filter's freq component matches cross-view, point-precise.
    _flabels = _freq_label_map(sorted(df["Frequency_MHz"].dropna().unique()), x_unit)
    cols_obj["freq_label"] = [None if pd.isna(v) else _flabels.get(float(v), "")
                              for v in df["Frequency_MHz"]]
    if has_serial:  # effective per-row serial (dedicated col or serial-like _grp_ col)
        cols_obj["Serial"] = [None if (v is None or v == "") else str(v) for v in serial_series]

    # ---- filter bar ----
    filt_parts = []
    for c, lbl in grp_dims:
        vals = sorted(str(v) for v in df[c].dropna().unique())
        if 1 < len(vals) <= 200:
            filt_parts.append(_checkbox_panel(c, lbl, vals))
    if serial_panel:
        filt_parts.append(_checkbox_panel("Serial", "Serial",
                                          sorted(set(str(v) for v in serial_series if str(v)))))
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

    # Global Filter: shared, dataset-scoped key -- identical derivation to every
    # other view (title is "<prefix> — <view label>"), so a DUT/point cleaned via
    # auto-filter or GF anywhere is excluded from this 1000-ft view too.
    gf_key = "padb_v2_excluded_" + title.rsplit(" — ", 1)[0]

    constants = "\n".join([
        f"var COLS={json.dumps(cols_obj)};",
        f"var GROUP_COLS={json.dumps([[c, l] for c, l in gb_opts_src])};",
        f"var GF_DIMS={json.dumps([[c, l] for c, l in grp_dims])};",
        f"var GF_KEY={json.dumps(gf_key)};",
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
        '  <span class="sep"></span>\n'
        '  <label title="Apply the shared Global Filter (the cross-view per-point exclusion'
        ' written by auto-filter / Set-as-GF on the other views) so this summary reflects the'
        ' same cleaned population."><input type="checkbox" id="ref_gf_chk" checked'
        ' onchange="update()"> Apply Global Filter</label>\n'
        '  <span id="ref_gf_badge" class="gfbadge"></span>\n'
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
 .gfbadge{font-size:11px;font-weight:600;padding:1px 6px;border-radius:4px;border:1px solid transparent}
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
        + '<div class="sect">Value distribution (current filter view)</div>\n'
        + '<div id="distplot" style="max-width:1000px"></div>\n'
        + '<div class="sect">Pareto by group</div>\n'
        + '<div id="pareto" style="max-width:1000px"></div>\n'
        + '<div class="sect">Per-group descriptive statistics</div>\n'
        + '<div id="grouptbl" style="overflow:auto"></div>\n'
        + '<div class="sect">Outlier points (1.5&times;IQR)</div>\n'
        + '<div id="outliers" style="overflow:auto"></div>\n'
        + '<div class="sect">Auto-filter impact (preview)</div>\n'
        + '<div style="font-size:13px;margin-bottom:6px">\n'
          '  Basis: <select id="ref_af_basis" onchange="_refImpactRefresh()">'
          '<option value="dist">Distribution (MAD &sigma;)</option>'
          '<option value="iqr">IQR fence (Tukey)</option>'
          '<option value="dmad">Double-MAD (skew-aware)</option></select>\n'
          '  Level: <select id="ref_af_level" onchange="_refImpactRefresh()">'
          '<option value="off" selected>off</option>'
          '<option value="conservative">Conservative</option>'
          '<option value="moderate">Moderate</option>'
          '<option value="aggressive">Aggressive</option></select>\n'
          '  <span id="ref_af_rec" style="margin-left:10px"></span>\n'
          '</div>\n'
        + '<div id="ref_af_impact"></div>\n'
    )

    return (
        "<!DOCTYPE html>\n<html>\n<head>\n<meta charset='utf-8'>\n"
        f"<title>{title}</title>\n"
        f"<script>{_get_plotlyjs()}</script>\n{style}\n</head>\n<body>\n"
        + body
        + f"<script>\n{constants}\n{_AUTO_FILTER_SHARED_JS}\n{_REF_STATS_JS}</script>\n</body>\n</html>\n"
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

/* ---------- Global Filter (shared, dataset-scoped -- honours the same cross-view
   per-point exclusion every other view does, point-precise via a dims-intersection
   match on serial + condition dims + temperature + frequency-box label) ---------- */
var _gfExcluded=null, _gfCoarse=null;
var _SER_KWS=['serial','unit id','dut id','s/n'];
function _loadRefGlobalFilter(){
  try{
    var raw=(typeof GF_KEY!=='undefined')?localStorage.getItem(GF_KEY):null;
    if(!raw){_gfExcluded=null;_gfCoarse=null;}
    else{
      var obj=JSON.parse(raw); _gfExcluded=new Set(obj.excluded||[]); _gfCoarse=new Set();
      _gfExcluded.forEach(function(k){
        var parts=k.split('||'); if(parts.length<2)return;
        var coarseCond=parts[1].split('|').filter(function(p){
          var lo=p.toLowerCase(); return !_SER_KWS.some(function(kw){return lo.indexOf(kw)===0;});
        }).join('|');
        var tp=(parts.length>=3&&parts[2]&&parts[2]!=='manual')?'|Temp='+parts[2]:'';
        var fq=(parts.length>=4&&parts[3]&&parts[3]!=='0')?'|Freq='+parts[3]:'';
        _gfCoarse.add(parts[0]+'||'+coarseCond+tp+fq);
      });
    }
  }catch(e){_gfExcluded=null;_gfCoarse=null;}
  _updateRefGfBadge();
}
function _gfIsIn(checkKey){ // dims-intersection (mirrors _statGfIsIn/_boxIsInGf)
  if(!_gfCoarse||!_gfCoarse.size)return false;
  if(_gfCoarse.has(checkKey))return true;
  var sep=checkKey.indexOf('||'); if(sep<0)return false;
  var ser=checkKey.slice(0,sep), rowMap={};
  checkKey.slice(sep+2).split('|').filter(Boolean).forEach(function(kv){
    var i=kv.indexOf('='); if(i>=0)rowMap[kv.slice(0,i)]=kv.slice(i+1);});
  var found=false;
  _gfCoarse.forEach(function(gk){
    if(found)return; var gs=gk.indexOf('||'); if(gs<0||gk.slice(0,gs)!==ser)return;
    var ok=true;
    gk.slice(gs+2).split('|').filter(Boolean).forEach(function(kv){
      if(!ok)return; var i=kv.indexOf('='); if(i<0)return;
      var d=kv.slice(0,i); if(rowMap.hasOwnProperty(d)&&rowMap[d]!==kv.slice(i+1))ok=false;});
    if(ok)found=true;});
  return found;
}
function _refGfExcl(r){ // build this row's point key, then match
  if(!_gfCoarse||!_gfCoarse.size)return false;
  var ser=(r.Serial!=null?String(r.Serial):'unknown');
  var toks=[];
  for(var i=0;i<GF_DIMS.length;i++){var v=r[GF_DIMS[i][0]];
    if(v!=null&&v!=='')toks.push(GF_DIMS[i][1]+'='+v);}
  var cond=toks.join('|');
  var temp=(r.Temperature!=null&&r.Temperature!=='')?'|Temp='+r.Temperature:'';
  var fq=(r.freq_label!=null&&r.freq_label!=='')?'|Freq='+r.freq_label:'';
  return _gfIsIn(ser+'||'+cond+temp+fq);
}
function _updateRefGfBadge(){
  var el=document.getElementById('ref_gf_badge'); if(!el)return;
  var chk=document.getElementById('ref_gf_chk'), on=chk?chk.checked:true;
  var duts=new Set(); if(_gfExcluded)_gfExcluded.forEach(function(k){duts.add(k.split('||')[0]);});
  var n=duts.size, pts=_gfExcluded?_gfExcluded.size:0;
  if(n>0){el.textContent=(on?'GF ON':'GF OFF')+': '+pts+' pt'+(pts!==1?'s':'')+' ('+n+' DUT'+(n!==1?'s':'')+')';
    el.style.background=on?'#ffeaea':'#f0f0f0'; el.style.color=on?'#900':'#888';
    el.style.borderColor=on?'#c88':'#ccc';}
  else{el.textContent='';el.style.background='';el.style.borderColor='transparent';}
}
window.addEventListener('storage',function(e){if(typeof GF_KEY!=='undefined'&&e.key===GF_KEY){_loadRefGlobalFilter();update();}});

/* ---------- filtering ---------- */
function _filterCols(){ // every _grp_/Serial/Temperature column that has a checkbox panel
  var cols={}; document.querySelectorAll('.fchk').forEach(function(c){cols[c.getAttribute('data-col')]=1;});
  return Object.keys(cols);
}
function applyFilters(rows){
  var cols=_filterCols(), sel={}; cols.forEach(function(c){sel[c]=_selected(c);});
  var flo=parseFloat(document.getElementById('f_lo').value); if(isNaN(flo))flo=-Infinity;
  var fhi=parseFloat(document.getElementById('f_hi').value); if(isNaN(fhi))fhi=Infinity;
  var gfChk=document.getElementById('ref_gf_chk'), gfOn=gfChk?gfChk.checked:false;
  return rows.filter(function(r){
    if(r.Frequency_MHz<flo||r.Frequency_MHz>fhi)return false;
    for(var i=0;i<cols.length;i++){var c=cols[i];var v=(c==='Serial'||c==='Temperature')?r[c]:r[c];
      /* Blank = this dimension does not apply to the row (a dim one site records
         and another leaves null in a cross-site compare). Blank is never a
         checkbox option, so it must NOT exclude -- excluding it silently dropped
         the whole primary site from the reference view (David 2026-09-22). Same
         absent-dimension rule as scatter/summary/stat_summary. */
      if(v==null)v=''; if(v!==''&&!sel[c][String(v)])return false;}
    if(gfOn&&_refGfExcl(r))return false;
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
  return {n:n,mean:mean,sd:sd,med:pc(50),min:s[0],max:s[n-1],out:out,
          q1:q1,q3:q3,oflo:loF,ofhi:hiF};
}

/* ---------- CSV download (outlier table export) ---------- */
function _csvCell(v){v=String(v==null?'':v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}
function _refDownload(name,text){var b=new Blob([text],{type:'text/csv'});var u=URL.createObjectURL(b);
  var a=document.createElement('a');a.href=u;a.download=name;document.body.appendChild(a);a.click();
  document.body.removeChild(a);URL.revokeObjectURL(u);}
function exportOutliers(){var o=window._refOutliers||[]; if(!o.length)return;
  var hasSer=o.some(function(x){return x.ser;});
  var hdr=['Group'].concat(hasSer?['Serial']:[]).concat([X_SHORT+' ('+X_UNIT+')',Y_LABEL,'Dev from median','Side']);
  var lines=[hdr.map(_csvCell).join(',')];
  o.forEach(function(x){var row=[x.grp].concat(hasSer?[x.ser]:[]).concat([x.freq,x.val,x.dev,x.side]);
    lines.push(row.map(_csvCell).join(','));});
  _refDownload((TITLE||'reference')+'_outliers.csv',lines.join('\n'));}
function _fmt(x,d){return (x==null||isNaN(x))?'—':x.toFixed(d==null?4:d);}

/* ---------- Auto-filter impact preview (increment 3) --------------------------
   Reuses the SAME shared engine every plot view uses (_afScorer / _afCompute /
   _afAnalyze / _afRecommend) so the "what would the data look like cleaned"
   readout is identical to what a real auto-filter would remove -- but this is a
   PREVIEW only: it never writes the Global Filter (the actual apply stays on the
   plot views and flows back here via the GF, which this page already honours). */
function _refCondKey(r){var t=[];for(var i=0;i<GF_DIMS.length;i++){var v=r[GF_DIMS[i][0]];
  if(v!=null&&v!=='')t.push(GF_DIMS[i][1]+'='+v);}return t.sort().join('|');}
function _refPtKey(r){var ser=(r.Serial!=null?String(r.Serial):'unknown');
  return ser+'||'+_refCondKey(r)+'||'+(r.Temperature!=null?String(r.Temperature):'')+
    '||'+(r.freq_label!=null?r.freq_label:'');}
function _refBucketMap(rows){ // group by (condition dims, temp, freq-box) -- same buckets the plot views score
  var b={};
  rows.forEach(function(r){if(r.Value==null)return;
    var ck=_refCondKey(r),tp=(r.Temperature!=null?String(r.Temperature):''),fl=(r.freq_label!=null?r.freq_label:'');
    var k=ck+''+tp+''+fl;
    (b[k]||(b[k]={ck:ck,tp:tp,fl:fl,rows:[]})).rows.push(r);});
  return b;
}
function _refBadPoints(basis){
  var bm=_refBucketMap(applyFilters(DATA)),out=[];
  Object.keys(bm).forEach(function(k){var b=bm[k],fv=b.rows.map(function(r){return r.Value;});
    var score=_afScorer(basis,fv,null,null);
    b.rows.forEach(function(r){var sc=score(r.Value);if(!sc)return;
      var ser=(r.Serial!=null?String(r.Serial):'unknown');
      out.push({serial:ser,key:ser+'||'+b.ck+'||'+b.tp+'||'+b.fl,dir:sc.dir,mag:sc.mag,
                temp:b.tp,freqLabel:b.fl,site:''});});});
  return out;
}
var REF_AF={
  levelSel:'ref_af_level', basisSel:'ref_af_basis', baseSerial:function(s){return s;}, primarySite:null,
  badPoints:_refBadPoints,
  buckets:function(){var bm=_refBucketMap(applyFilters(DATA));return Object.keys(bm).map(function(k){
    return {vals:bm[k].rows.map(function(r){return r.Value;})};});},
  nDuts:function(){var s={};applyFilters(DATA).forEach(function(r){if(r.Serial!=null&&r.Serial!=='')s[String(r.Serial)]=1;});return Object.keys(s).length;},
  nConds:function(){var s={};applyFilters(DATA).forEach(function(r){s[_refCondKey(r)]=1;});return Object.keys(s).length;},
  nFreqs:function(){var s={};applyFilters(DATA).forEach(function(r){if(r.freq_label!=null)s[r.freq_label]=1;});return Object.keys(s).length;},
  multiTemp:function(){var s={};applyFilters(DATA).forEach(function(r){if(r.Temperature!=null)s[String(r.Temperature)]=1;});return Object.keys(s).length>1;},
  hasSpec:function(){return HAS_LIMITS||_ovr('ovr_hi')!=null||_ovr('ovr_lo')!=null;}
};
function _refRecBasis(rec,a){ // impact panel offers peer bases only; map a 'spec' rec onto one
  return (rec.basis==='dist'||rec.basis==='iqr'||rec.basis==='dmad')?rec.basis:(a.skewFrac>0.3?'dmad':'dist');}
function _refUseRec(){
  var a=_afAnalyze(REF_AF),rec=_afRecommend(a);
  document.getElementById('ref_af_basis').value=_refRecBasis(rec,a);
  document.getElementById('ref_af_level').value=rec.level; _refImpactRefresh();
}
function _refImpactRefresh(){
  var recEl=document.getElementById('ref_af_rec'),imp=document.getElementById('ref_af_impact');
  if(!recEl||!imp)return;
  var a=_afAnalyze(REF_AF),rec=_afRecommend(a),BN={dist:'Distribution',iqr:'IQR fence',dmad:'Double-MAD'};
  recEl.innerHTML='Recommended: <b>'+BN[_refRecBasis(rec,a)]+' / '+((_AF_LEVELS[rec.level]||{}).label||rec.level)+'</b> '+
    '<button class="reset-btn" onclick="_refUseRec()">Use</button> '+
    '<span style="color:#777;cursor:help" title="'+rec.why.join('  ').replace(/"/g,'&quot;')+'">why?</span>';
  var level=(document.getElementById('ref_af_level')||{}).value||'off';
  var basis=(document.getElementById('ref_af_basis')||{}).value||'dist';
  if(level==='off'){
    imp.innerHTML='<div style="color:#888;font-size:12px">Pick a level to preview how the chosen auto-filter would '+
      'change these numbers. <b>Preview only</b> — it changes no data. To actually apply a clean, use the auto-filter on a '+
      'plot view (Box plots / Statistical summary / …); it writes the shared Global Filter, which this page then reflects.</div>';
    return;
  }
  var pts=_refBadPoints(basis),r=_afCompute(pts,REF_AF),autoKeys={};
  r.auto.forEach(function(d){d.keys.forEach(function(k){autoKeys[k]=1;});});
  var rows=applyFilters(DATA),kept=[],remPts=0,remDuts={};
  rows.forEach(function(r0){ if(autoKeys[_refPtKey(r0)]){remPts++;remDuts[(r0.Serial!=null?String(r0.Serial):'unknown')]=1;} else kept.push(r0); });
  var mode=_pfMode(rows);
  function ov(set){var vals=[];set.forEach(function(r0){if(r0.Value!=null)vals.push(r0.Value);});
    var st=_stats(vals),pass=0,fail=0;
    if(mode)set.forEach(function(r0){var v=_pf(r0,mode);if(v==='P')pass++;else if(v==='F')fail++;});
    var tot=pass+fail; return {n:set.length,pass:pass,fail:fail,fpct:tot?100*fail/tot:0,
      out:(st.out||0),outpct:st.n?100*(st.out||0)/st.n:0,mean:st.mean,med:st.med,sd:st.sd};}
  var A=ov(rows),B=ov(kept);
  function d1(x){return (x==null||isNaN(x))?'—':x.toFixed(1);}
  function dlt(a2,b2,dec,pct){ if(a2==null||b2==null||isNaN(a2)||isNaN(b2))return '';
    var d=b2-a2; var s=(d>0?'+':'')+d.toFixed(dec==null?4:dec)+(pct?'%':'');
    return '<span style="color:'+(Math.abs(d)<1e-9?'#999':(d>0?'#c04000':'#1a7d3c'))+'">'+s+'</span>';}
  var nRemDuts=Object.keys(remDuts).length;
  var h='<div style="font-size:12px;margin:2px 0 6px">Would remove <b>'+nRemDuts+'</b> DUT'+(nRemDuts!==1?'s':'')+
    ' (<b>'+remPts+'</b> pt'+(remPts!==1?'s':'')+') at basis <b>'+BN[basis]+'</b>, level <b>'+((_AF_LEVELS[level]||{}).label||level)+
    '</b> — the risk-gated <i>auto</i> set only (marginal / review DUTs are left, exactly as the plot views’ workflow would).</div>';
  h+='<table class="stbl"><thead><tr><th class="k">Metric</th><th>As collected</th><th>After auto-filter</th><th>&Delta;</th></tr></thead><tbody>';
  h+='<tr><td class="k">Total points</td><td>'+A.n.toLocaleString()+'</td><td>'+B.n.toLocaleString()+'</td><td>'+dlt(A.n,B.n,0)+'</td></tr>';
  if(mode){
    h+='<tr><td class="k">Pass</td><td>'+A.pass.toLocaleString()+'</td><td>'+B.pass.toLocaleString()+'</td><td>'+dlt(A.pass,B.pass,0)+'</td></tr>';
    h+='<tr><td class="k">Fail</td><td>'+A.fail.toLocaleString()+'</td><td>'+B.fail.toLocaleString()+'</td><td>'+dlt(A.fail,B.fail,0)+'</td></tr>';
    h+='<tr><td class="k">Fail %</td><td>'+d1(A.fpct)+'%</td><td>'+d1(B.fpct)+'%</td><td>'+dlt(A.fpct,B.fpct,1,true)+'</td></tr>';
  }
  h+='<tr><td class="k">Outliers (1.5×IQR)</td><td>'+A.out.toLocaleString()+' ('+d1(A.outpct)+'%)</td><td>'+B.out.toLocaleString()+' ('+d1(B.outpct)+'%)</td><td>'+dlt(A.out,B.out,0)+'</td></tr>';
  h+='<tr><td class="k">Mean</td><td>'+_fmt(A.mean)+'</td><td>'+_fmt(B.mean)+'</td><td>'+dlt(A.mean,B.mean)+'</td></tr>';
  h+='<tr><td class="k">Median</td><td>'+_fmt(A.med)+'</td><td>'+_fmt(B.med)+'</td><td>'+dlt(A.med,B.med)+'</td></tr>';
  h+='<tr><td class="k">Std dev</td><td>'+_fmt(A.sd)+'</td><td>'+_fmt(B.sd)+'</td><td>'+dlt(A.sd,B.sd)+'</td></tr>';
  h+='</tbody></table>';
  imp.innerHTML=h;
}

function update(){
  _updateRefGfBadge();
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
  // ---- Value distribution (pass/fail-coloured overlay when a mode exists) ----
  (function(){
    if(!vals.length){Plotly.react('distplot',[],{margin:{t:10}});return;}
    var bw=(st.q3-st.q1)>0?2*(st.q3-st.q1)/Math.pow(vals.length,1/3):0;
    var xb=bw>0?{start:st.min,end:st.max+bw,size:bw}:undefined;
    var traces;
    if(mode){
      var pv=[],fv=[];
      rows.forEach(function(r){if(r.Value==null)return;var v=_pf(r,mode);
        if(v==='P')pv.push(r.Value);else if(v==='F')fv.push(r.Value);});
      traces=[{type:'histogram',x:pv,name:'Pass',marker:{color:'#4472c4'},opacity:0.6,xbins:xb},
              {type:'histogram',x:fv,name:'Fail',marker:{color:'#c0504d'},opacity:0.6,xbins:xb}];
    } else {
      traces=[{type:'histogram',x:vals,name:Y_LABEL,marker:{color:'#4472c4'},xbins:xb}];
    }
    Plotly.react('distplot',traces,{barmode:'overlay',margin:{t:10,r:20,b:44,l:56},height:300,
      xaxis:{title:Y_LABEL},yaxis:{title:'count'},legend:{orientation:'h',y:1.12}},
      {responsive:true,displayModeBar:false});
  })();
  // ---- Outlier points (overall 1.5xIQR fence -- matches Overall's outlier count) ----
  var outs=[];
  if(st.n){rows.forEach(function(r){if(r.Value==null)return;
    if(r.Value>st.ofhi||r.Value<st.oflo){
      var okey=(gb&&r[gb]!=null&&r[gb]!=='')?String(r[gb]):(gb?'(blank)':'(all)');
      outs.push({grp:okey,ser:(r.Serial!=null?String(r.Serial):''),freq:r.Frequency_MHz,
        val:r.Value,dev:r.Value-st.med,side:(r.Value>st.ofhi?'high':'low')});}});}
  outs.sort(function(a,b){return Math.abs(b.dev)-Math.abs(a.dev);});
  window._refOutliers=outs;
  var OCAP=300, hasSer=outs.some(function(o){return o.ser;});
  var oh='<div style="margin-bottom:5px"><b>'+outs.length.toLocaleString()+
    '</b> outlier point(s) &mdash; |value &minus; median| beyond the 1.5&times;IQR fence'+
    (outs.length>OCAP?(' (showing top '+OCAP+' by deviation)'):'')+
    (outs.length?' <button class="reset-btn" onclick="exportOutliers()">Export CSV</button>':'')+'</div>';
  if(!outs.length){document.getElementById('outliers').innerHTML=oh+'<div style="color:#888">None.</div>';}
  else{
    var ot='<table class="stbl"><thead><tr><th class="k">'+glabel+'</th>'+
      (hasSer?'<th class="k">Serial</th>':'')+'<th>'+X_SHORT+'('+X_UNIT+')</th><th>'+Y_LABEL+
      '</th><th>Dev from median</th><th>Side</th></tr></thead><tbody>';
    outs.slice(0,OCAP).forEach(function(o){
      ot+='<tr><td class="k">'+o.grp+'</td>'+(hasSer?'<td class="k">'+o.ser+'</td>':'')+
        '<td>'+_fmt(o.freq,3)+'</td><td>'+_fmt(o.val)+'</td><td'+(o.side==='high'?'':' class="fail"')+
        '>'+(o.dev>0?'+':'')+_fmt(o.dev)+'</td><td>'+o.side+'</td></tr>';});
    ot+='</tbody></table>';
    document.getElementById('outliers').innerHTML=oh+ot;
  }
  _refImpactRefresh();
}

function resetFilters(){
  document.querySelectorAll('.fchk').forEach(function(c){c.checked=true;});
  document.querySelectorAll('[id^="all_"]').forEach(function(c){c.checked=true;});
  document.querySelectorAll('.badge').forEach(function(b){b.textContent='';});
  document.getElementById('f_lo').value=FREQ_MIN; document.getElementById('f_hi').value=FREQ_MAX;
  document.getElementById('ovr_hi').value=''; document.getElementById('ovr_lo').value='';
  var gfChk=document.getElementById('ref_gf_chk'); if(gfChk)gfChk.checked=true;
  update();
}
window.addEventListener('DOMContentLoaded',function(){_loadRefGlobalFilter();update();});
"""
