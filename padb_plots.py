"""
padb_plots.py — Plotly-based interactive plot library for PADB CSV outputs

Each public function signature:
    fn(csv_path: Path, cfg: dict, output_html: Path) -> None

cfg keys (all optional unless noted):
    title: str
    y_label: str
    x_label: str
    y_lim: [min, max]
    log_x: bool
    spec_limits: [lower, upper]   — override spec from CSV columns
    proportion: float             — for TI/stats (default 0.90)
    confidence: float             — for TI/stats (default 0.90)
    freq_bands: [[label,lo,hi]]   — for spec_derivation
    group_col_filter: str         — substring to keep in Group column
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.offline as _plo
from plotly.subplots import make_subplots

import padb_stats as pst

INT_SENTINEL = 2_147_483_647
_PLOTLY_JS = True  # embed plotly.js; each HTML is self-contained (~4 MB)
_PLOTLYJS_CACHE: str = ""


def _get_plotlyjs() -> str:
    global _PLOTLYJS_CACHE
    if not _PLOTLYJS_CACHE:
        _PLOTLYJS_CACHE = _plo.get_plotlyjs()
    return _PLOTLYJS_CACHE


# ---------------------------------------------------------------------------
# Shared: CSV dropdown button (two options: filtered vs filtered+excluded)
# ---------------------------------------------------------------------------

_CSV_DROPDOWN_CSS = (
    ".csv-wrap{position:relative;display:inline-block;vertical-align:middle;}"
    ".csv-menu{display:none;position:absolute;right:0;top:100%;background:#fff;"
    "border:1px solid #0066cc;border-radius:3px;min-width:190px;z-index:600;"
    "box-shadow:0 2px 8px rgba(0,0,0,.15);margin-top:2px;}"
    ".csv-wrap:hover .csv-menu{display:block;}"
    ".csv-menu button{display:block;width:100%;text-align:left;padding:7px 14px;"
    "font-size:12px;cursor:pointer;background:none;border:none;color:#0066cc;"
    "white-space:nowrap;border-bottom:1px solid #e8f0ff;}"
    ".csv-menu button:last-child{border-bottom:none;}"
    ".csv-menu button:hover{background:#e8f4ff;}"
)


def _csv_btn(fn: str = "saveCSV") -> str:
    """Return HTML for a two-option CSV dropdown button."""
    return (
        f'<div class="csv-wrap">'
        f'<button class="csv-btn" onclick="return false">&#8595;&nbsp;CSV&thinsp;&#9662;</button>'
        f'<div class="csv-menu">'
        f'<button onclick="{fn}(false)">Filtered data</button>'
        f'<button onclick="{fn}(true)">Filtered&nbsp;+&nbsp;excluded</button>'
        f'</div></div>'
    )


# ---------------------------------------------------------------------------
# JS for accuracy_vs_freq interactive plot (raw string — no Python escaping)
# ---------------------------------------------------------------------------
_AV_FREQ_JS = r"""
function median(arr){
  var s=[].concat(arr).sort(function(a,b){return a-b;});
  var m=Math.floor(s.length/2);
  return s.length%2?s[m]:(s[m-1]+s[m])/2;
}

/* ---------- checkbox panel open/close ---------- */
function togglePanel(col){
  var panel=document.getElementById('panel_'+col);
  var isOpen=panel.classList.contains('open');
  document.querySelectorAll('.filter-panel').forEach(function(p){p.classList.remove('open');});
  if(!isOpen) panel.classList.add('open');
}
document.addEventListener('click',function(e){
  if(!e.target.closest('.filter-wrap'))
    document.querySelectorAll('.filter-panel').forEach(function(p){p.classList.remove('open');});
});

/* ---------- checkbox logic ---------- */
function toggleAll(col){
  var allChk=document.getElementById('all_'+col);
  document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){c.checked=allChk.checked;});
  updateBadge(col);
  update();
}
function chkChanged(col){
  var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
  var allChk=document.getElementById('all_'+col);
  var nChecked=chks.filter(function(c){return c.checked;}).length;
  allChk.checked=(nChecked===chks.length);
  allChk.indeterminate=(nChecked>0&&nChecked<chks.length);
  updateBadge(col);
  update();
}
function updateBadge(col){
  var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
  var n=chks.filter(function(c){return c.checked;}).length;
  var badge=document.getElementById('badge_'+col);
  if(n<chks.length){badge.textContent=n+'/'+chks.length;badge.classList.add('active');}
  else badge.classList.remove('active');
}
function getSelected(col){
  return Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]:checked')).map(function(c){return c.value;});
}
function getHoverSelected(){
  return Array.from(document.querySelectorAll('.hchk:checked')).map(function(c){return c.value;});
}
function getSelectedTemps(){
  if(!TEMPS||TEMPS.length<=1) return null;
  return Array.from(document.querySelectorAll('.env_chk:checked')).map(function(c){return c.value;});
}
function toggleAllHover(){
  var allChk=document.getElementById('all_hover');
  document.querySelectorAll('.hchk').forEach(function(c){c.checked=allChk.checked;});
  update();
}

/* ---------- log X toggle ---------- */
function isLogX(){ return document.getElementById('log_x_chk').checked; }
function toggleLogX(){
  var log=isLogX();
  var _lt=document.getElementById('freq_lo_txt'),_ht=document.getElementById('freq_hi_txt');
  var lo=_lt&&_lt.value!==''?parseFloat(_lt.value):parseFloat(document.getElementById('freq_lo').value);
  var hi=_ht&&_ht.value!==''?parseFloat(_ht.value):parseFloat(document.getElementById('freq_hi').value);
  var range=log?[Math.log10(Math.max(lo,1e-9)),Math.log10(Math.max(hi,1e-9))]:[lo,hi];
  Plotly.relayout('plot',{'xaxis.type':log?'log':'linear','xaxis.range':range});
}

/* ---------- frequency sliders + text entry ---------- */
function syncFreq(){
  var lo=document.getElementById('freq_lo');
  var hi=document.getElementById('freq_hi');
  var loV=parseFloat(lo.value),hiV=parseFloat(hi.value);
  if(loV>hiV){lo.value=hiV;loV=hiV;}
  document.getElementById('freq_lo_txt').value=loV.toFixed(3);
  document.getElementById('freq_hi_txt').value=parseFloat(hi.value).toFixed(3);
  var log=isLogX();
  var range=log?[Math.log10(Math.max(loV,1e-9)),Math.log10(Math.max(hiV,1e-9))]:[loV,hiV];
  Plotly.relayout('plot',{'xaxis.range':range});
}
function freqTxtChange(which){
  var txt=document.getElementById('freq_'+which+'_txt');
  var slider=document.getElementById('freq_'+which);
  var v=parseFloat(txt.value);
  if(isNaN(v)){txt.value=parseFloat(slider.value).toFixed(3);return;}
  v=Math.max(parseFloat(slider.min),Math.min(parseFloat(slider.max),v));
  if(which==='lo'){var h=parseFloat(document.getElementById('freq_hi').value);if(v>h)v=h;}
  else{var l=parseFloat(document.getElementById('freq_lo').value);if(v<l)v=l;}
  txt.value=v.toFixed(3);slider.value=v;syncFreq();update();
}
function freqStep(which,dir){
  var fv=FREQ_VALS,txt=document.getElementById('freq_'+which+'_txt'),slider=document.getElementById('freq_'+which);
  var cur=parseFloat(txt.value),idx=-1;
  for(var i=0;i<fv.length;i++){if(Math.abs(fv[i]-cur)<0.0001){idx=i;break;}}
  if(idx<0){var best=0;for(var i=1;i<fv.length;i++){if(Math.abs(fv[i]-cur)<Math.abs(fv[best]-cur))best=i;}idx=best;}
  var ni=Math.max(0,Math.min(fv.length-1,idx+dir)),nv=fv[ni];
  if(which==='lo'){if(nv>parseFloat(document.getElementById('freq_hi').value))return;}
  else{if(nv<parseFloat(document.getElementById('freq_lo').value))return;}
  txt.value=nv.toFixed(3);slider.value=nv;update();
}
function freqKeyDown(e,which){
  if(e.key==='Enter')freqTxtChange(which);
  else if(e.key==='ArrowUp'){e.preventDefault();freqStep(which,1);}
  else if(e.key==='ArrowDown'){e.preventDefault();freqStep(which,-1);}
}
function setFreqBand(lo,hi){
  var s1=document.getElementById('freq_lo'),s2=document.getElementById('freq_hi');
  s1.value=Math.max(parseFloat(s1.min),lo);
  s2.value=Math.min(parseFloat(s2.max),hi);
  syncFreq();update();
}

/* ---------- save filtered CSV ---------- */
function saveCSV(withExcluded){
  var freqLo=parseFloat(document.getElementById('freq_lo').value);
  var freqHi=parseFloat(document.getElementById('freq_hi').value);
  var gfChk=document.getElementById('gf_chk');
  var wasChecked=gfChk&&gfChk.checked;
  if(withExcluded&&gfChk) gfChk.checked=false;
  var filtered=applyFilters(DATA);
  if(withExcluded&&gfChk) gfChk.checked=wasChecked;
  if(!filtered.length){alert('No data matches current filters.');return;}
  /* GF set for exclusion tagging (non-null only when GF was active and is now bypassed) */
  var gfSet=(withExcluded&&wasChecked&&_gfCoarseExcluded&&_gfCoarseExcluded.size>0)?_gfCoarseExcluded:null;
  var colMap={'Frequency_MHz':'Frequency_MHz','Value':Y_LABEL.replace(/[,"\n]/g,'')};
  if(TEMPS&&TEMPS.length>1) colMap['Test_Step']='Temperature';
  GROUP_COLS.forEach(function(p){colMap[p[0]]=p[1].replace(/[,"\n]/g,'');});
  var cols=Object.keys(filtered[0]).filter(function(c){return colMap[c];});
  var hdrs=cols.map(function(c){return colMap[c];});
  if(withExcluded) hdrs.push('Excluded');
  var rows=[hdrs.join(',')];
  function esc(v){var s=String(v===null||v===undefined?'':v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;}
  filtered.forEach(function(r){
    var vals=cols.map(function(c){return esc(r[c]);});
    if(withExcluded) vals.push(gfSet&&_isInGfFull(r)?'global':'');
    rows.push(vals.join(','));
  });
  /* Metadata block */
  var ts=new Date().toISOString().replace('T',' ').replace(/\.\d+Z$/,' UTC');
  var gfSers=[];
  if(_gfExcluded&&_gfExcluded.size>0) _gfExcluded.forEach(function(k){var s=k.split('||')[0];if(gfSers.indexOf(s)<0) gfSers.push(s);});
  var condLines=[];
  GROUP_COLS.forEach(function(p){var sel=getSelected(p[0]);condLines.push('# '+p[1]+': '+(sel.length?sel.join(', '):'(none)'));});
  var meta=['# PADB Export','# Plot: '+TITLE,'# Generated: '+ts,
    '# Export: '+(withExcluded?'Filtered + excluded (GF-excluded rows flagged in Excluded column)':'Filtered data'),
    '# Freq range: '+freqLo.toFixed(2)+' - '+freqHi.toFixed(2)+' MHz']
    .concat(condLines)
    .concat(['# GF excluded DUTs ('+gfSers.length+'): '+(gfSers.length?gfSers.join(', '):'None'),'#'])
    .join('\r\n');
  var blob=new Blob([meta+'\r\n'+rows.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  var suffix=withExcluded?'_with_excl':'_filtered';
  a.href=url;a.download=(TITLE+suffix).replace(/[^a-zA-Z0-9_\-]/g,'_')+'.csv';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/* ---------- filter & render ---------- */
function applyFilters(data){
  var _flt=document.getElementById('freq_lo_txt'),_fht=document.getElementById('freq_hi_txt');
  var freqLo=_flt&&_flt.value!==''?parseFloat(_flt.value):parseFloat(document.getElementById('freq_lo').value);
  var freqHi=_fht&&_fht.value!==''?parseFloat(_fht.value):parseFloat(document.getElementById('freq_hi').value);
  var selTemps=getSelectedTemps();
  var gfChk=document.getElementById('gf_chk');
  var applyGf=gfChk&&gfChk.checked&&_gfParsed&&_gfParsed.size>0;
  var selections={};
  GROUP_COLS.forEach(function(pair){selections[pair[0]]=getSelected(pair[0]);});
  return data.filter(function(r){
    if(r.Frequency_MHz<freqLo||r.Frequency_MHz>freqHi) return false;
    if(selTemps){
      var t=String(r.Test_Step===null||r.Test_Step===undefined?'':r.Test_Step);
      if(selTemps.indexOf(t)<0) return false;
    }
    if(applyGf){var _inGf=_isInGfFull(r);var _gfFcs=document.getElementById('gf_focus_chk')&&document.getElementById('gf_focus_chk').checked;if(_gfFcs?!_inGf:_inGf) return false;}
    for(var col in selections){
      var allowed=selections[col];
      if(!allowed.length) return false;
      var v=String(r[col]===null||r[col]===undefined?'':r[col]);
      if(allowed.indexOf(v)<0) return false;
    }
    return true;
  });
}

function buildTraces(filtered){
  var groupCol=document.getElementById('groupby').value;
  var sortBy=document.getElementById('sortby').value;
  var groups={};
  filtered.forEach(function(r){
    var key=String(r[groupCol]===null||r[groupCol]===undefined?'(none)':r[groupCol]);
    if(!groups[key]) groups[key]=[];
    groups[key].push(r);
  });
  var entries=Object.keys(groups).map(function(k){return [k,groups[k]];});
  if(sortBy==='name_asc') entries.sort(function(a,b){return a[0].localeCompare(b[0]);});
  else if(sortBy==='name_desc') entries.sort(function(a,b){return b[0].localeCompare(a[0]);});
  else if(sortBy==='worst_desc') entries.sort(function(a,b){
    return Math.max.apply(null,b[1].map(function(r){return r.Value;}))-Math.max.apply(null,a[1].map(function(r){return r.Value;}));
  });
  else if(sortBy==='median_asc') entries.sort(function(a,b){
    return median(a[1].map(function(r){return r.Value;}))-median(b[1].map(function(r){return r.Value;}));
  });
  else if(sortBy==='median_desc') entries.sort(function(a,b){
    return median(b[1].map(function(r){return r.Value;}))-median(a[1].map(function(r){return r.Value;}));
  });
  var hoverSel=getHoverSelected();
  return entries.map(function(entry){
    var key=entry[0],rows=entry[1];
    var sorted=rows.slice().sort(function(a,b){return a.Frequency_MHz-b.Frequency_MHz;});
    var customdata=sorted.map(function(r){
      return HOVER_COLS.map(function(hc){var v=r[hc[0]];return (v===null||v===undefined)?'':v;});
    });
    var tmpl='<b>'+key+'</b><br>Freq: %{x:.4f} MHz<br>'+Y_LABEL+': %{y:.4f}';
    HOVER_COLS.forEach(function(hc,i){
      if(hoverSel.indexOf(hc[0])>=0) tmpl+='<br>'+hc[1]+': %{customdata['+i+']}';
    });
    tmpl+='<extra></extra>';
    return {
      type:'scattergl',
      x:sorted.map(function(r){return r.Frequency_MHz;}),
      y:sorted.map(function(r){return r.Value;}),
      customdata:customdata,
      mode:TRACE_MODE,marker:{size:3},name:key,
      hovertemplate:tmpl
    };
  });
}

function buildLayout(filtered){
  var shapes=[],annotations=[];
  var hiSpecs={},loSpecs={};
  /* Use integer-rounded keys to deduplicate nominal vs MU-adjusted spec values that differ by <1 dBc.
     Keep the most stringent value per 1-dBc bin (min for upper limits, max for lower limits). */
  (filtered||DATA).forEach(function(r){
    if(r.Upper_Limit!==null&&r.Upper_Limit!==undefined&&r.Upper_Limit!==''&&!isNaN(Number(r.Upper_Limit))){
      var k=Math.round(Number(r.Upper_Limit)),v=Number(r.Upper_Limit);
      if(!(k in hiSpecs)||v<hiSpecs[k]) hiSpecs[k]=v;
    }
    if(r.Lower_Limit!==null&&r.Lower_Limit!==undefined&&r.Lower_Limit!==''&&!isNaN(Number(r.Lower_Limit))){
      var k=Math.round(Number(r.Lower_Limit)),v=Number(r.Lower_Limit);
      if(!(k in loSpecs)||v>loSpecs[k]) loSpecs[k]=v;
    }
  });
  if(!Object.keys(hiSpecs).length&&HI_SPEC!==null){var _hk=Math.round(HI_SPEC);hiSpecs[_hk]=HI_SPEC;}
  if(!Object.keys(loSpecs).length&&LO_SPEC!==null){var _lk=Math.round(LO_SPEC);loSpecs[_lk]=LO_SPEC;}
  Object.values(hiSpecs).sort(function(a,b){return a-b;}).forEach(function(v){
    shapes.push({type:'line',xref:'paper',x0:0,x1:1,y0:v,y1:v,line:{color:'red',dash:'dash',width:1.5}});
    annotations.push({xref:'paper',yref:'y',x:0.99,y:v,text:'Spec '+v.toFixed(2),showarrow:false,xanchor:'right',yanchor:'bottom',font:{color:'red',size:11}});
  });
  Object.values(loSpecs).sort(function(a,b){return b-a;}).forEach(function(v){
    shapes.push({type:'line',xref:'paper',x0:0,x1:1,y0:v,y1:v,line:{color:'red',dash:'dash',width:1.5}});
    annotations.push({xref:'paper',yref:'y',x:0.01,y:v,text:'Spec '+v.toFixed(2),showarrow:false,xanchor:'left',yanchor:'bottom',font:{color:'red',size:11}});
  });
  return {
    title:{text:TITLE,x:0.5,font:{size:15}},
    template:'plotly_white',
    xaxis:{title:'Frequency (MHz)',type:isLogX()?'log':'linear'},
    yaxis:{title:Y_LABEL,range:Y_LIM},
    shapes:shapes,annotations:annotations,height:520,
    legend:{bgcolor:'rgba(255,255,255,0.8)',bordercolor:'#ccc',borderwidth:1},
    margin:{l:60,r:30,t:60,b:60}
  };
}

function resetFilters(){
  _stClear();
  document.querySelectorAll('.env_chk').forEach(function(c){if(!c.disabled)c.checked=true;});
  GROUP_COLS.forEach(function(pair){
    var col=pair[0];
    document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){c.checked=true;});
    var allChk=document.getElementById('all_'+col);
    if(allChk){allChk.checked=true;allChk.indeterminate=false;}
    document.getElementById('badge_'+col).classList.remove('active');
  });
  document.querySelectorAll('.hchk').forEach(function(c){c.checked=true;});
  var allHover=document.getElementById('all_hover');
  if(allHover){allHover.checked=true;allHover.indeterminate=false;}
  document.getElementById('freq_lo').value=FREQ_MIN;
  document.getElementById('freq_hi').value=FREQ_MAX;
  document.getElementById('freq_lo_txt').value=parseFloat(FREQ_MIN).toFixed(3);
  document.getElementById('freq_hi_txt').value=parseFloat(FREQ_MAX).toFixed(3);
  update();
}

/* ---------- global filter (localStorage) ---------- */
var _gfExcluded=null;
var _gfCoarseExcluded=null;
var _gfParsed=null; /* Map<baseSer,[{condKvs,isManual,temp,freq}]> pre-indexed for O(1) serial lookup */
/* Extract serial from a data row: prefer r.Serial column, fall back to serial-like _grp_ column */
function _rowSerial(r){
  if(r.Serial!=null&&r.Serial!=='') return String(r.Serial);
  var serKws=['serial','unit id','dut id','s/n'];
  var found=null;
  GROUP_COLS.forEach(function(p){
    if(!found&&p[0].indexOf('_grp_')===0){
      var lo=p[1].toLowerCase();
      if(serKws.some(function(kw){return lo.indexOf(kw)===0;}))
        found=String(r[p[0]]!=null?r[p[0]]:'');
    }
  });
  return found||'unknown';
}
function _buildPointKey(r){
  var condParts=[];
  GROUP_COLS.forEach(function(p){
    if(p[0].indexOf('_grp_')===0){
      condParts.push(p[1]+'='+String(r[p[0]]===null||r[p[0]]===undefined?'':r[p[0]]));
    }
  });
  condParts.sort();
  var tmp=String(r.Test_Step===null||r.Test_Step===undefined?'Room':r.Test_Step);
  var frq=typeof r.Frequency_MHz==='number'?r.Frequency_MHz.toFixed(3):String(r.Frequency_MHz);
  return _rowSerial(r)+'||'+condParts.join('|')+'||'+tmp+'||'+frq;
}
/* Strip serial-number-like parts from a pipe-joined condition key string */
function _coarseCondKey(condKey){
  var serKws=['serial','unit id','dut id','s/n'];
  return condKey.split('|').filter(function(p){
    var lo=p.toLowerCase();
    return !serKws.some(function(kw){return lo.indexOf(kw)===0;});
  }).join('|');
}
/* DUT+condition coarse key: serial + non-serial condition parts (no temp, no freq) */
function _buildCoarseKey(r){
  var condParts=[];
  GROUP_COLS.forEach(function(p){
    if(p[0].indexOf('_grp_')===0){
      condParts.push(p[1]+'='+String(r[p[0]]===null||r[p[0]]===undefined?'':r[p[0]]));
    }
  });
  condParts.sort();
  return _rowSerial(r)+'||'+_coarseCondKey(condParts.join('|'));
}
/* GF membership check with dimension-intersection matching (coarse: serial+condition only).
   Used for CSV export tagging and as a fast pre-check. */
function _isInGfCoarse(fullKey){
  if(!_gfCoarseExcluded||!_gfCoarseExcluded.size) return false;
  if(_gfCoarseExcluded.has(fullKey)) return true;
  var sep=fullKey.indexOf('||');if(sep<0) return false;
  var ser=fullKey.slice(0,sep);
  var rowCondMap={};
  fullKey.slice(sep+2).split('|').filter(Boolean).forEach(function(kv){
    var i=kv.indexOf('=');if(i<0) return;
    rowCondMap[kv.slice(0,i)]=kv.slice(i+1);
  });
  var found=false;
  _gfCoarseExcluded.forEach(function(gk){
    if(found) return;
    var gs=gk.indexOf('||');if(gs<0) return;
    if(gk.slice(0,gs)!==ser) return;
    var allMatch=true;
    gk.slice(gs+2).split('|').filter(Boolean).forEach(function(kv){
      if(!allMatch) return;
      var i=kv.indexOf('=');if(i<0) return;
      var dim=kv.slice(0,i);
      if(rowCondMap.hasOwnProperty(dim)&&rowCondMap[dim]!==kv.slice(i+1)) allMatch=false;
    });
    if(allMatch) found=true;
  });
  return found;
}
/* Fine GF membership check using pre-indexed _gfParsed Map.
   O(1) serial lookup: rows not in the GF serial index return immediately.
   For outlier entries (real temp+freq): matches serial+condition+temp+freq.
   For manual entries (||manual||0): matches serial+condition only (coarse). */
function _isInGfFull(r){
  if(!_gfParsed||!_gfParsed.size) return false;
  var ser=_rowSerial(r);
  var entries=_gfParsed.get(ser);
  if(!entries||!entries.length) return false; /* fast path: serial not in GF */
  var tmp=String(r.Test_Step===null||r.Test_Step===undefined?'Room':r.Test_Step);
  var frq=typeof r.Frequency_MHz==='number'?r.Frequency_MHz.toFixed(3):String(r.Frequency_MHz);
  var rowCondMap={};
  GROUP_COLS.forEach(function(p){
    if(p[0].indexOf('_grp_')===0)
      rowCondMap[p[1]]=String(r[p[0]]===null||r[p[0]]===undefined?'':r[p[0]]);
  });
  for(var i=0;i<entries.length;i++){
    var e=entries[i];
    var condMatch=true;
    for(var j=0;j<e.condKvs.length;j++){
      var kv=e.condKvs[j];
      if(rowCondMap.hasOwnProperty(kv.dim)&&rowCondMap[kv.dim]!==kv.val){condMatch=false;break;}
    }
    if(!condMatch) continue;
    if(!e.isManual&&(e.temp!==tmp||e.freq!==frq)) continue;
    return true;
  }
  return false;
}
function _loadGlobalFilter(){
  try{
    var raw=localStorage.getItem('padb_v2_excluded');
    if(!raw){_gfExcluded=null;_gfCoarseExcluded=null;}
    else{
      var obj=JSON.parse(raw);
      _gfExcluded=new Set(obj.excluded||[]);
      _gfCoarseExcluded=new Set();
      _gfParsed=new Map();
      _gfExcluded.forEach(function(k){
        var parts=k.split('||');
        if(parts.length<2) return;
        /* Coarse set (serial+condition, no temp/freq) for badge/CSV tagging */
        _gfCoarseExcluded.add(parts[0]+'||'+_coarseCondKey(parts[1]));
        /* Parsed index keyed by base serial (strip port suffix from old-format keys) */
        var baseSer=parts[0].replace(/_[A-Z]+\d*$/,'');
        var condKvs=[];
        parts[1].split('|').filter(Boolean).forEach(function(kv){
          var i=kv.indexOf('=');if(i>=0) condKvs.push({dim:kv.slice(0,i),val:kv.slice(i+1)});
        });
        var entry={condKvs:condKvs,isManual:parts.length>=3&&parts[2]==='manual',
                   temp:parts.length>=3?parts[2]:'',freq:parts.length>=4?parts[3]:''};
        if(!_gfParsed.has(baseSer)) _gfParsed.set(baseSer,[]);
        _gfParsed.get(baseSer).push(entry);
      });
    }
  }catch(e){_gfExcluded=null;_gfCoarseExcluded=null;_gfParsed=null;}
  _updateGfIndicator();
}
function _updateGfIndicator(){
  var badge=document.getElementById('gf_badge');
  var lbl=document.getElementById('gf_label');
  var focusLbl=document.getElementById('gf_focus_label');
  if(!badge||!lbl) return;
  var chk=document.getElementById('gf_chk');
  var hasGf=_gfExcluded&&_gfExcluded.size>0;
  var active=chk&&chk.checked&&_gfCoarseExcluded&&_gfCoarseExcluded.size>0;
  lbl.style.display=hasGf?'':'none';
  if(focusLbl) focusLbl.style.display=(hasGf&&active)?'':'none';
  var dutSers=new Set();
  if(_gfExcluded) _gfExcluded.forEach(function(k){dutSers.add(k.split('||')[0]);});
  var mode=(localStorage.getItem('padb_v2_gf_mode')||'exclude');
  var isFocus=active&&mode==='focus';
  /* Keep Focus checkbox in sync with padb_v2_gf_mode (set by any plot) */
  var focusChk=document.getElementById('gf_focus_chk');
  if(focusChk) focusChk.checked=mode==='focus';
  var n=dutSers.size,pts=_gfExcluded?_gfExcluded.size:0;
  badge.textContent=n>0?(isFocus?'Inspect: ':'')+pts+' pts in GF ('+n+' DUT'+(n!==1?'s':')')+(isFocus?' — INSPECT':''):'';
  badge.style.background=active?(isFocus?'#e8f0ff':'#ffeaea'):'#f0f0f0';
  badge.style.color=active?(isFocus?'#0044aa':'#900'):'#666';
  badge.style.borderColor=active?(isFocus?'#6688cc':'#c88'):'#ccc';
}
window.addEventListener('storage',function(e){
  if(e.key==='padb_v2_excluded'){_loadGlobalFilter();update();}
  else if(e.key==='padb_v2_gf_mode'){_updateGfIndicator();update();}
});

function update(){
  var filtered=applyFilters(DATA);
  document.getElementById('n_points').textContent=filtered.length.toLocaleString()+' pts';
  Plotly.react('plot',buildTraces(filtered),buildLayout(filtered));
  saveState();
}

/* ---- localStorage state persistence ---- */
function _stGet(k){try{return localStorage.getItem(STATE_KEY+k);}catch(e){return null;}}
function _stSet(k,v){try{localStorage.setItem(STATE_KEY+k,v);}catch(e){}}
function _stClear(){try{var keys=[];for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);if(k&&k.indexOf(STATE_KEY)===0)keys.push(k);}keys.forEach(function(k){localStorage.removeItem(k);});}catch(e){}}
function saveState(){
  _stSet('freq_lo',document.getElementById('freq_lo').value);
  _stSet('freq_hi',document.getElementById('freq_hi').value);
  document.querySelectorAll('.env_chk').forEach(function(c){_stSet('temp_'+c.value,c.checked?'1':'0');});
  GROUP_COLS.forEach(function(pair){
    var col=pair[0];
    document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){_stSet('cond_'+col+'_'+encodeURIComponent(c.value),c.checked?'1':'0');});
  });
}
function loadState(){
  var lo=_stGet('freq_lo'),hi=_stGet('freq_hi');
  if(lo!==null){var sl=document.getElementById('freq_lo');if(sl){sl.value=lo;var tx=document.getElementById('freq_lo_txt');if(tx)tx.value=parseFloat(lo).toFixed(3);}}
  if(hi!==null){var sh=document.getElementById('freq_hi');if(sh){sh.value=hi;var th=document.getElementById('freq_hi_txt');if(th)th.value=parseFloat(hi).toFixed(3);}}
  document.querySelectorAll('.env_chk').forEach(function(c){var s=_stGet('temp_'+c.value);if(s!==null&&!c.disabled)c.checked=(s==='1');});
  GROUP_COLS.forEach(function(pair){
    var col=pair[0];
    document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){var s=_stGet('cond_'+col+'_'+encodeURIComponent(c.value));if(s!==null)c.checked=(s==='1');});
    var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
    var allChk=document.getElementById('all_'+col);
    if(allChk){var n=chks.filter(function(c){return c.checked;}).length;allChk.checked=(n===chks.length);allChk.indeterminate=(n>0&&n<chks.length);}
    updateBadge(col);
  });
}

_loadGlobalFilter();
loadState();
var _initData=applyFilters(DATA);
Plotly.newPlot('plot',buildTraces(_initData),buildLayout(_initData));
document.getElementById('n_points').textContent=_initData.length.toLocaleString()+' pts';
"""


# ---------------------------------------------------------------------------
# CSV loaders
# ---------------------------------------------------------------------------

def _sfloat(v):
    try:
        f = float(v)
        return np.nan if abs(f) >= INT_SENTINEL else f
    except (TypeError, ValueError):
        return np.nan


def _load_scatter_csv(csv_path: Path) -> pd.DataFrame:
    """
    Load a PADB ScatterPlot (Type=80) CSV.
    Returns DataFrame with standardised columns:
        Frequency_MHz, Value, Serial, Station, Lower_Limit, Upper_Limit, Group
    """
    df = pd.read_csv(csv_path, dtype=str)
    df.columns = df.columns.str.strip()

    col_lower = {c.lower(): c for c in df.columns}

    freq_col    = next((col_lower[k] for k in col_lower if "frequency" in k or "x value" in k), None)
    serial_col  = next((col_lower[k] for k in col_lower
                        if any(kw in k for kw in ("serial num", "serial no", "sn", "unit id", "dut id"))
                        and "station" not in k), None)
    if serial_col is None:
        serial_col = next((col_lower[k] for k in col_lower
                           if k == "serial" or (k.startswith("serial") and "station" not in k)), None)
    station_col = next((col_lower[k] for k in col_lower if "station" in k), None)
    lo_col      = next((col_lower[k] for k in col_lower if "lower limit" in k), None)
    hi_col      = next((col_lower[k] for k in col_lower if "upper limit" in k), None)
    group_col   = next((col_lower[k] for k in col_lower if k == "group"), None)

    skip = {c.lower() for c in [freq_col, serial_col, station_col, lo_col, hi_col, group_col] if c}
    skip |= {"analysis type", "model(s)", "algorithm -> result", "units"}
    val_col = None
    if freq_col:
        cols = list(df.columns)
        fi = cols.index(freq_col)
        for c in cols[fi + 1:]:
            if c.lower() not in skip:
                val_col = c
                break

    out = pd.DataFrame()
    out["Frequency_MHz"] = df[freq_col].map(_sfloat) if freq_col else np.nan
    out["Value"]         = df[val_col].map(_sfloat)  if val_col  else np.nan
    out["Serial"]        = df[serial_col].str.strip()  if serial_col  else ""
    out["Station"]       = df[station_col].str.strip() if station_col else ""
    out["Lower_Limit"]   = df[lo_col].map(_sfloat)   if lo_col  else np.nan
    out["Upper_Limit"]   = df[hi_col].map(_sfloat)   if hi_col  else np.nan
    out["Group"]         = df[group_col].str.strip()  if group_col else ""
    test_step_col = next((col_lower[k] for k in col_lower if k == "test step"), None)
    out["Test_Step"]     = df[test_step_col].str.strip() if test_step_col else ""
    out["_val_col_name"] = val_col or "Value"

    out = out.dropna(subset=["Frequency_MHz", "Value"])
    return out


def _load_env_csv(csv_path: Path) -> pd.DataFrame:
    """
    Load a PADB Environmental (Type=60) CSV.
    Returns DataFrame with standardised columns:
        Frequency_MHz, Min_Env, Max_Env, Mean_Env, Lower_Limit, Upper_Limit, Group
    """
    df = pd.read_csv(csv_path, dtype=str)
    df.columns = df.columns.str.strip()
    col_lower = {c.lower(): c for c in df.columns}

    group_col = next((col_lower[k] for k in col_lower if k == "group"), None)
    xval_col  = next((col_lower[k] for k in col_lower if "x value" in k), None)
    freq_col  = xval_col or next((col_lower[k] for k in col_lower if "frequency" in k), None)
    min_col   = next((col_lower[k] for k in col_lower if "min" in k and "env" in k), None)
    max_col   = next((col_lower[k] for k in col_lower if "max" in k and "env" in k and "std" not in k), None)
    mean_col  = next((col_lower[k] for k in col_lower if "mean" in k and "env" in k), None)
    lo_col    = next((col_lower[k] for k in col_lower if "lower limit" in k), None)
    hi_col    = next((col_lower[k] for k in col_lower if "upper limit" in k), None)

    out = pd.DataFrame()
    out["Group"]        = df[group_col].str.strip() if group_col else ""
    out["Frequency_MHz"]= df[freq_col].map(_sfloat) if freq_col else np.nan
    out["Min_Env"]      = df[min_col].map(_sfloat)  if min_col  else np.nan
    out["Max_Env"]      = df[max_col].map(_sfloat)  if max_col  else np.nan
    out["Mean_Env"]     = df[mean_col].map(_sfloat) if mean_col else np.nan
    out["Lower_Limit"]  = df[lo_col].map(_sfloat)   if lo_col   else np.nan
    out["Upper_Limit"]  = df[hi_col].map(_sfloat)   if hi_col   else np.nan

    missing_mean = out["Mean_Env"].isna() & out["Min_Env"].notna() & out["Max_Env"].notna()
    out.loc[missing_mean, "Mean_Env"] = (out.loc[missing_mean, "Min_Env"] + out.loc[missing_mean, "Max_Env"]) / 2

    out = out.dropna(subset=["Frequency_MHz"])
    return out


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_spec(df: pd.DataFrame, cfg: dict) -> tuple[float, float]:
    sl = cfg.get("spec_limits")
    if sl and len(sl) == 2:
        lo = float(sl[0]) if sl[0] is not None else np.nan
        hi = float(sl[1]) if sl[1] is not None else np.nan
        return lo, hi
    lo = df["Lower_Limit"].dropna()
    hi = df["Upper_Limit"].dropna()
    return (float(lo.iloc[0]) if len(lo) else np.nan,
            float(hi.iloc[0]) if len(hi) else np.nan)


def _write(fig: go.Figure, output_html: Path, cfg: dict) -> None:
    output_html.parent.mkdir(parents=True, exist_ok=True)
    title = cfg.get("title", output_html.stem)
    fig.update_layout(
        title=dict(text=title, x=0.5, font=dict(size=15)),
        template="plotly_white",
        legend=dict(bgcolor="rgba(255,255,255,0.8)", bordercolor="#ccc", borderwidth=1),
        margin=dict(l=60, r=30, t=60, b=60),
        height=480,
    )
    fig.write_html(str(output_html), include_plotlyjs=_PLOTLY_JS)


def _spec_lines(fig, lo: float, hi: float, row=None, col=None) -> None:
    kw = dict(row=row, col=col) if row is not None else {}
    if not np.isnan(lo):
        fig.add_hline(y=lo, line=dict(color="red", dash="dash", width=1.5),
                      annotation_text=f"Lo={lo:g}", annotation_position="left", **kw)
    if not np.isnan(hi):
        fig.add_hline(y=hi, line=dict(color="red", dash="dash", width=1.5),
                      annotation_text=f"Hi={hi:g}", annotation_position="right", **kw)


def _log_x_button():
    return dict(
        buttons=[
            dict(label="Linear X", method="relayout", args=[{"xaxis.type": "linear"}]),
            dict(label="Log X",    method="relayout", args=[{"xaxis.type": "log"}]),
        ],
        direction="left", showactive=True, x=0.0, xanchor="left", y=1.12, yanchor="top",
    )


# ---------------------------------------------------------------------------
# accuracy_vs_freq helpers
# ---------------------------------------------------------------------------

def _parse_group_fields(df: pd.DataFrame) -> pd.DataFrame:
    """
    Parse the 'Group' column (e.g. 'AlcState: FALSE  HarmonicNumber: 0.2  Mode: 0')
    into individual _grp_<Field> columns.
    """
    if "Group" not in df.columns or df["Group"].isna().all():
        return df
    all_keys: set[str] = set()
    for g in df["Group"].dropna().unique():
        for k in _parse_group_kv(str(g)):
            all_keys.add(k)
    df = df.copy()
    for key in sorted(all_keys):
        df[f"_grp_{key}"] = df["Group"].apply(
            lambda g: _parse_group_kv(str(g)).get(key) if pd.notna(g) else None
        )
    return df


def _detect_group_cols(df: pd.DataFrame) -> list[tuple[str, str]]:
    """Return (col_name, display_label) pairs with cardinality > 1, suitable for group-by."""
    result: list[tuple[str, str]] = []
    for col in df.columns:
        if col.startswith("_grp_"):
            label = col[5:]
            if 1 < df[col].nunique(dropna=True) <= 100:
                result.append((col, label))
    if "Station" in df.columns and df["Station"].replace("", pd.NA).nunique(dropna=True) > 1:
        result.append(("Station", "Test Station"))
    if "Serial" in df.columns and df["Serial"].replace("", pd.NA).nunique(dropna=True) > 1:
        result.append(("Serial", "Serial"))
    if "Test_Step" in df.columns and df["Test_Step"].replace("", pd.NA).nunique(dropna=True) > 1:
        result.append(("Test_Step", "Temperature"))
    return result


def _checkbox_panel(col: str, label: str, vals: list[str]) -> str:
    """Build a collapsible checkbox-dropdown filter widget for one dimension."""
    items = "".join(
        f'<label class="fitem"><input type="checkbox" class="fchk" data-col="{col}"'
        f' value="{v}" checked onchange="chkChanged(\'{col}\')">{v}</label>'
        for v in vals
    )
    return (
        f'<div class="filter-wrap">'
        f'<button class="filter-btn" onclick="togglePanel(\'{col}\')">'
        f'{label}&thinsp;<span id="badge_{col}" class="badge"></span>&#9662;</button>'
        f'<div class="filter-panel" id="panel_{col}">'
        f'<label class="fitem fall"><input type="checkbox" id="all_{col}"'
        f' checked onchange="toggleAll(\'{col}\')"><b>Select&nbsp;all</b></label>'
        f'<hr class="fdiv">{items}</div></div>'
    )


def _build_av_freq_html(df: pd.DataFrame, cfg: dict, title: str) -> str:
    """Build a fully self-contained interactive HTML for accuracy-vs-frequency."""
    lo_spec, hi_spec = _get_spec(df, cfg)
    y_label   = cfg.get("y_label", df["_val_col_name"].iloc[0] if len(df) else "Value")
    y_lim     = cfg.get("y_lim")
    log_x_cfg = cfg.get("log_x", None)   # None = auto-detect

    group_cols = _detect_group_cols(df)

    # Temperature env_bar: if Test_Step has >1 unique value, show it as an inline
    # env_bar (green bar) rather than as a collapsible filter-panel dropdown.
    temps_present: list[str] = []
    if "Test_Step" in df.columns:
        _raw = sorted(str(v) for v in df["Test_Step"].replace("", pd.NA).dropna().unique())
        # Room always first (disabled), then remaining temps in sort order
        temps_present = (["Room"] if "Room" in _raw else []) + [v for v in _raw if v != "Room"]
    show_env_bar = len(temps_present) > 1
    # filter_cols = group_cols used for GROUP_COLS JS constant (drives applyFilters).
    # Exclude Test_Step here because it's filtered via env_chk checkboxes instead.
    filter_cols = [(c, l) for c, l in group_cols if not (c == "Test_Step" and show_env_bar)]

    # Columns available for hover tooltip (group dims + spec limits)
    hover_col_list = list(group_cols)
    if "Upper_Limit" in df.columns and df["Upper_Limit"].notna().any():
        hover_col_list.append(("Upper_Limit", "Spec Hi"))
    if "Lower_Limit" in df.columns and df["Lower_Limit"].notna().any():
        hover_col_list.append(("Lower_Limit", "Spec Lo"))

    # Columns to embed as JSON
    json_cols = ["Frequency_MHz", "Value", "Upper_Limit", "Lower_Limit"] + [c for c, _ in group_cols]
    json_cols = [c for c in json_cols if c in df.columns]
    records = json.loads(df[json_cols].to_json(orient="records"))

    freq_min  = float(df["Frequency_MHz"].min())
    freq_max  = float(df["Frequency_MHz"].max())
    freq_step = max(round((freq_max - freq_min) / 1000, 4), 0.001)

    # Auto log-X when range spans >= 2 decades and not explicitly configured
    if log_x_cfg is not None:
        log_x = log_x_cfg
    else:
        log_x = freq_min > 0 and (freq_max / freq_min) >= 100

    # Group-by <option> tags
    grp_opts = "\n".join(
        f'<option value="{c}">{lbl}</option>' for c, lbl in group_cols
    ) if group_cols else '<option value="Station">Test Station</option>'

    # Checkbox panels — one per filter dimension (skip Test_Step; handled by env_bar)
    panels: list[str] = []
    for col, label in group_cols:
        if col == "Test_Step" and show_env_bar:
            continue
        vals = sorted(str(v) for v in df[col].dropna().replace("", pd.NA).dropna().unique())
        if vals:
            panels.append(_checkbox_panel(col, label, vals))
    panels_html = "\n  ".join(panels)

    # Hover-field selector panel
    hover_items = "".join(
        f'<label class="fitem"><input type="checkbox" class="hchk" value="{c}"'
        f' onchange="update()" checked> {lbl}</label>'
        for c, lbl in hover_col_list
    )
    hover_panel_html = (
        '<div class="filter-wrap">'
        '<button class="filter-btn" onclick="togglePanel(\'hover\')">'
        'Hover&thinsp;&#9662;</button>'
        '<div class="filter-panel" id="panel_hover">'
        '<label class="fitem fall"><input type="checkbox" id="all_hover" checked'
        ' onchange="toggleAllHover()"><b>Select&nbsp;all</b></label>'
        f'<hr class="fdiv">{hover_items}</div></div>'
    ) if hover_col_list else ""

    # Frequency band preset buttons
    freq_bands = cfg.get("freq_bands", [])
    band_btns_html = ""
    for _b in freq_bands:
        band_btns_html += (
            f'  <button class="reset-btn" onclick="setFreqBand({_b["lo"]},{_b["hi"]})">'
            f'{_b["label"]}</button>\n'
        )
    band_section_html = (f'  <div class="sep"></div>\n{band_btns_html}') if freq_bands else ""

    lo_js = "null" if np.isnan(lo_spec) else repr(float(lo_spec))
    hi_js = "null" if np.isnan(hi_spec) else repr(float(hi_spec))

    constants = "\n".join([
        f"var DATA={json.dumps(records)};",
        f"var LO_SPEC={lo_js};",
        f"var HI_SPEC={hi_js};",
        f"var Y_LABEL={json.dumps(y_label)};",
        f"var Y_LIM={json.dumps(y_lim)};",
        f"var LOG_X={'true' if log_x else 'false'};",
        f"var TITLE={json.dumps(title)};",
        f"var GROUP_COLS={json.dumps([[c, l] for c, l in filter_cols])};",
        f"var HOVER_COLS={json.dumps([[c, l] for c, l in hover_col_list])};",
        f"var TRACE_MODE={json.dumps(cfg.get('mode', 'lines+markers'))};",
        f"var FREQ_MIN={freq_min!r};",
        f"var FREQ_MAX={freq_max!r};",
        f"var FREQ_VALS={json.dumps(sorted(float(f) for f in df['Frequency_MHz'].dropna().unique()))};",
        f"var TEMPS={json.dumps(temps_present)};",
        f"var STATE_KEY='padb_{cfg.get('results_dir', '')}';",
    ])

    env_chks_html = "\n  ".join(
        f'<label><input type="checkbox" class="env_chk" value="{t}"'
        + (' checked disabled' if t == "Room" else ' checked onchange="update()"')
        + f'>&nbsp;{t}</label>'
        for t in temps_present
    )
    env_bar_html = (
        '\n<div class="env-bar" onclick="event.stopPropagation()">\n'
        '  <b>Temperature&nbsp;steps:</b>\n'
        f'  {env_chks_html}\n'
        '</div>'
    ) if show_env_bar else ""

    css = (
        "body{font-family:Arial,sans-serif;margin:0;padding:8px;}"
        ".ctrl-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:8px 14px;background:#f0f2f5;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".env-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:5px 14px;background:#edf7ee;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".env-bar label{white-space:nowrap;cursor:pointer;}"
        ".ctrl-bar label{white-space:nowrap;}"
        ".ctrl-bar select{font-size:13px;padding:2px 4px;border:1px solid #bbb;border-radius:3px;}"
        ".ctrl-bar input[type=range]{vertical-align:middle;width:100px;}"
        "input.freq-txt{font-size:12px;width:72px;padding:1px 3px;border:1px solid #bbb;"
        "border-radius:3px;text-align:right;margin-left:2px;}"
        ".sep{border-left:2px solid #ccc;height:22px;margin:0 2px;}"
        ".filter-wrap{position:relative;display:inline-block;}"
        ".filter-btn{font-size:13px;padding:3px 10px;border:1px solid #bbb;border-radius:3px;"
        "cursor:pointer;background:#fff;white-space:nowrap;}"
        ".filter-btn:hover{background:#e8e8e8;}"
        ".filter-panel{display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:200;"
        "background:#fff;border:1px solid #ccc;border-radius:4px;"
        "box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:160px;max-height:280px;"
        "overflow-y:auto;padding:6px 8px;}"
        ".filter-panel.open{display:block;}"
        ".fitem{display:block;padding:2px 0;cursor:pointer;white-space:nowrap;font-size:13px;}"
        ".fall{padding-bottom:2px;}"
        ".fdiv{margin:4px 0;border:none;border-top:1px solid #eee;}"
        ".badge{font-size:11px;background:#0066cc;color:#fff;border-radius:10px;"
        "padding:1px 6px;margin-right:2px;display:none;}"
        ".badge.active{display:inline;}"
        "button.reset-btn{font-size:12px;padding:2px 10px;border:1px solid #999;"
        "border-radius:3px;cursor:pointer;background:#fff;}"
        "button.reset-btn:hover{background:#e8e8e8;}"
        "#n_points{font-size:12px;color:#666;margin-left:auto;}"
        + _CSV_DROPDOWN_CSS
    )

    html = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f"<style>{css}</style>\n"
        "</head>\n<body>\n"
        '<div class="ctrl-bar">\n'
        f'  <label>Group&nbsp;by:<select id="groupby" onchange="update()">{grp_opts}</select></label>\n'
        '  <label>Sort:<select id="sortby" onchange="update()">\n'
        '    <option value="name_asc">Name A&#8594;Z</option>\n'
        '    <option value="name_desc">Name Z&#8594;A</option>\n'
        '    <option value="worst_desc">Worst first &#8595;</option>\n'
        '    <option value="median_asc">Median low&#8594;high</option>\n'
        '    <option value="median_desc">Median high&#8594;low</option>\n'
        '  </select></label>\n'
        '  <div class="sep"></div>\n'
        f'  {panels_html}\n'
        '  <div class="sep"></div>\n'
        f'  <label>Freq&nbsp;min:<input type="range" id="freq_lo"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_min:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()" onchange="update()">'
        f'<input class="freq-txt" id="freq_lo_txt" type="text" value="{freq_min:.3f}"'
        f' onchange="freqTxtChange(\'lo\')"'
        f' onkeydown="freqKeyDown(event,\'lo\')">&nbsp;MHz</label>\n'
        f'  <label>Freq&nbsp;max:<input type="range" id="freq_hi"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_max:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()" onchange="update()">'
        f'<input class="freq-txt" id="freq_hi_txt" type="text" value="{freq_max:.3f}"'
        f' onchange="freqTxtChange(\'hi\')"'
        f' onkeydown="freqKeyDown(event,\'hi\')">&nbsp;MHz</label>\n'
        f'  <label><input type="checkbox" id="log_x_chk"'
        + (' checked' if log_x else '')
        + ' onchange="toggleLogX()"> Log&nbsp;X</label>\n'
        f'{band_section_html}'
        '  <div class="sep"></div>\n'
        f'  {hover_panel_html}\n'
        '  <div class="sep"></div>\n'
        '  <button class="reset-btn" onclick="resetFilters()">Reset</button>\n'
        f'  {_csv_btn("saveCSV")}\n'
        '  <label id="gf_label" style="display:none;white-space:nowrap">'
        '<input type="checkbox" id="gf_chk" checked onchange="_updateGfIndicator();update()">'
        '&nbsp;<span id="gf_badge" style="font-size:12px;border:1px solid #ccc;'
        'border-radius:3px;padding:1px 6px"></span></label>\n'
        '  <label id="gf_focus_label" style="display:none;white-space:nowrap" '
        'title="Inspect mode: show only GF-flagged data points">'
        '<input type="checkbox" id="gf_focus_chk" onchange="try{localStorage.setItem(\'padb_v2_gf_mode\',this.checked?\'focus\':\'exclude\');}catch(e){}update()">'
        '&nbsp;Inspect</label>\n'
        '  <span id="n_points"></span>\n'
        "</div>\n"
        + env_bar_html + "\n"
        + '<div id="plot"></div>\n'
        + f"<script>{_get_plotlyjs()}</script>\n"
        + "<script>\n"
        + constants + "\n"
        + _AV_FREQ_JS
        + "</script>\n</body>\n</html>"
    )
    return html


# ---------------------------------------------------------------------------
# Plot functions
# ---------------------------------------------------------------------------

def accuracy_vs_freq(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """
    Interactive scatter: measurement vs frequency.

    Controls embedded in HTML (no server required):
      - Group by: any parsed Group field, Test Station, or Serial
      - Sort traces: name A/Z, worst-first, median low/high
      - Filter dropdowns: one per group dimension
      - Frequency range sliders: zoom the X-axis
      - Reset button
      - Point count display
    """
    df = _load_scatter_csv(csv_path)
    df = _parse_group_fields(df)
    title = cfg.get("title", output_html.stem)
    html = _build_av_freq_html(df, cfg, title)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


def population_envelope(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """
    Population envelope: min/max/median/P5-P95 band across all serials at each frequency.
    Overlays non-parametric tolerance interval bounds.
    """
    df = _load_scatter_csv(csv_path)
    lo_spec, hi_spec = _get_spec(df, cfg)
    y_label = cfg.get("y_label", df["_val_col_name"].iloc[0] if len(df) else "Value")
    proportion = cfg.get("proportion", 0.90)
    confidence = cfg.get("confidence", 0.90)

    grp = df.groupby("Frequency_MHz")["Value"]
    freq_vals = np.array(sorted(grp.groups.keys()))
    p05 = grp.quantile(0.05).reindex(freq_vals).values
    p50 = grp.quantile(0.50).reindex(freq_vals).values
    p95 = grp.quantile(0.95).reindex(freq_vals).values
    vmin = grp.min().reindex(freq_vals).values
    vmax = grp.max().reindex(freq_vals).values

    ti_lo = np.full(len(freq_vals), np.nan)
    ti_hi = np.full(len(freq_vals), np.nan)
    for i, f in enumerate(freq_vals):
        band_data = df[df["Frequency_MHz"] == f]["Value"].dropna().values
        if len(band_data) >= 3:
            tl, th, _ = pst.nonparam_tolerance_interval(band_data, proportion, confidence)
            ti_lo[i] = tl
            ti_hi[i] = th

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=np.concatenate([freq_vals, freq_vals[::-1]]),
        y=np.concatenate([vmax, vmin[::-1]]),
        fill="toself", fillcolor="rgba(100,149,237,0.15)",
        line=dict(color="rgba(0,0,0,0)"), name="Min-Max", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=np.concatenate([freq_vals, freq_vals[::-1]]),
        y=np.concatenate([p95, p05[::-1]]),
        fill="toself", fillcolor="rgba(100,149,237,0.30)",
        line=dict(color="rgba(0,0,0,0)"), name="P5-P95", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=freq_vals, y=p50, mode="lines",
        line=dict(color="royalblue", width=2), name="Median",
        hovertemplate="Freq: %{x:.4f} MHz<br>Median: %{y:.4f}<extra></extra>",
    ))

    mask = ~np.isnan(ti_hi)
    if mask.any():
        fig.add_trace(go.Scatter(
            x=freq_vals[mask], y=ti_hi[mask], mode="lines",
            line=dict(color="darkorange", width=1.5, dash="dot"),
            name=f"TI P{100*proportion:.0f}/C{100*confidence:.0f} upper",
        ))
        fig.add_trace(go.Scatter(
            x=freq_vals[mask], y=ti_lo[mask], mode="lines",
            line=dict(color="darkorange", width=1.5, dash="dot"),
            name=f"TI P{100*proportion:.0f}/C{100*confidence:.0f} lower",
        ))

    _spec_lines(fig, lo_spec, hi_spec)
    fig.update_xaxes(title_text=cfg.get("x_label", "Frequency (MHz)"),
                     type="log" if cfg.get("log_x") else "linear")
    fig.update_yaxes(title_text=y_label, range=cfg.get("y_lim"))
    fig.update_layout(updatemenus=[_log_x_button()])
    _write(fig, output_html, cfg)


def distribution(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """
    Interactive distribution: histogram + spec lines with group-dimension filters and pass-only toggle.
    Filters are derived from the Group column (HarmonicNumber, AlcState, Mode, Serial, etc.)
    and the Test_Step column when present.
    """
    df = _load_scatter_csv(csv_path)
    df = _parse_group_fields(df)
    lo_spec, hi_spec = _get_spec(df, cfg)
    title   = cfg.get("title", output_html.stem)
    y_label = cfg.get("y_label", df["_val_col_name"].iloc[0] if len(df) else "Value")
    y_lim   = cfg.get("y_lim")

    if len(df) < 5:
        go.Figure().write_html(str(output_html), include_plotlyjs=_PLOTLY_JS)
        return

    group_cols = _detect_group_cols(df)
    json_cols  = ["Frequency_MHz", "Value", "Upper_Limit", "Lower_Limit"] + [c for c, _ in group_cols]
    json_cols  = [c for c in json_cols if c in df.columns]
    records    = json.loads(df[json_cols].to_json(orient="records"))

    freq_min  = float(df["Frequency_MHz"].min())
    freq_max  = float(df["Frequency_MHz"].max())
    freq_step = max(round((freq_max - freq_min) / 1000, 4), 0.001)

    lo_js = "null" if np.isnan(lo_spec) else repr(float(lo_spec))
    hi_js = "null" if np.isnan(hi_spec) else repr(float(hi_spec))

    panels: list[str] = []
    for col, label in group_cols:
        vals = sorted(str(v) for v in df[col].dropna().replace("", pd.NA).dropna().unique())
        if vals:
            panels.append(_checkbox_panel(col, label, vals))
    panels_html = "\n  ".join(panels)

    freq_bands = cfg.get("freq_bands", [])
    band_btns_html = ""
    for _b in freq_bands:
        band_btns_html += (
            f'  <button class="reset-btn" onclick="setFreqBand({_b["lo"]},{_b["hi"]})">'
            f'{_b["label"]}</button>\n'
        )
    band_section_html = f'  <div class="sep"></div>\n{band_btns_html}' if freq_bands else ""

    constants = "\n".join([
        f"var DATA={json.dumps(records)};",
        f"var LO_SPEC={lo_js};",
        f"var HI_SPEC={hi_js};",
        f"var Y_LABEL={json.dumps(y_label)};",
        f"var Y_LIM={json.dumps(y_lim)};",
        f"var TITLE={json.dumps(title)};",
        f"var GROUP_COLS={json.dumps([[c, l] for c, l in group_cols])};",
        f"var FREQ_MIN={freq_min!r};",
        f"var FREQ_MAX={freq_max!r};",
        f"var FREQ_VALS={json.dumps(sorted(float(f) for f in df['Frequency_MHz'].dropna().unique()))};",
        f"var STATE_KEY='padb_{cfg.get('results_dir', '')}';",
    ])

    css = (
        "body{font-family:Arial,sans-serif;margin:0;padding:8px;}"
        ".ctrl-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:8px 14px;background:#f0f2f5;border-radius:6px;margin-bottom:8px;font-size:13px;}"
        ".ctrl-bar label{white-space:nowrap;}"
        ".ctrl-bar select{font-size:13px;padding:2px 4px;border:1px solid #bbb;border-radius:3px;}"
        ".ctrl-bar input[type=range]{vertical-align:middle;width:100px;}"
        "input.freq-txt{font-size:12px;width:72px;padding:1px 3px;border:1px solid #bbb;"
        "border-radius:3px;text-align:right;margin-left:2px;}"
        ".sep{border-left:2px solid #ccc;height:22px;margin:0 2px;}"
        ".filter-wrap{position:relative;display:inline-block;}"
        ".filter-btn{font-size:13px;padding:3px 10px;border:1px solid #bbb;border-radius:3px;"
        "cursor:pointer;background:#fff;white-space:nowrap;}"
        ".filter-btn:hover{background:#e8e8e8;}"
        ".filter-panel{display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:200;"
        "background:#fff;border:1px solid #ccc;border-radius:4px;"
        "box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:160px;max-height:280px;"
        "overflow-y:auto;padding:6px 8px;}"
        ".filter-panel.open{display:block;}"
        ".fitem{display:block;padding:2px 0;cursor:pointer;white-space:nowrap;font-size:13px;}"
        ".fall{padding-bottom:2px;}"
        ".fdiv{margin:4px 0;border:none;border-top:1px solid #eee;}"
        ".badge{font-size:11px;background:#0066cc;color:#fff;border-radius:10px;"
        "padding:1px 6px;margin-right:2px;display:none;}"
        ".badge.active{display:inline;}"
        "button.reset-btn{font-size:12px;padding:2px 10px;border:1px solid #999;"
        "border-radius:3px;cursor:pointer;background:#fff;}"
        "button.reset-btn:hover{background:#e8e8e8;}"
        "#n_points{font-size:12px;color:#666;margin-left:auto;}"
        ".stbl{border-collapse:collapse;font-size:12px;margin:4px 0;}"
        ".stbl th{background:#e8eaf6;padding:4px 10px;text-align:left;border:1px solid #ccc;white-space:nowrap;}"
        ".stbl td{padding:3px 10px;border:1px solid #ddd;white-space:nowrap;}"
    )

    dist_js = r"""
function togglePanel(col){
  var panel=document.getElementById('panel_'+col);
  var isOpen=panel.classList.contains('open');
  document.querySelectorAll('.filter-panel').forEach(function(p){p.classList.remove('open');});
  if(!isOpen) panel.classList.add('open');
}
document.addEventListener('click',function(e){
  if(!e.target.closest('.filter-wrap'))
    document.querySelectorAll('.filter-panel').forEach(function(p){p.classList.remove('open');});
});
function toggleAll(col){
  var allChk=document.getElementById('all_'+col);
  document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){c.checked=allChk.checked;});
  updateBadge(col); update();
}
function chkChanged(col){
  var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
  var allChk=document.getElementById('all_'+col);
  var nChecked=chks.filter(function(c){return c.checked;}).length;
  allChk.checked=(nChecked===chks.length);
  allChk.indeterminate=(nChecked>0&&nChecked<chks.length);
  updateBadge(col); update();
}
function updateBadge(col){
  var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
  var n=chks.filter(function(c){return c.checked;}).length;
  var badge=document.getElementById('badge_'+col);
  if(n<chks.length){badge.textContent=n+'/'+chks.length;badge.classList.add('active');}
  else badge.classList.remove('active');
}
function getSelected(col){
  return Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]:checked')).map(function(c){return c.value;});
}
function syncFreq(){
  var lo=document.getElementById('freq_lo');
  var hi=document.getElementById('freq_hi');
  var loV=parseFloat(lo.value),hiV=parseFloat(hi.value);
  if(loV>hiV){lo.value=hiV;loV=hiV;}
  document.getElementById('freq_lo_txt').value=loV.toFixed(3);
  document.getElementById('freq_hi_txt').value=parseFloat(hi.value).toFixed(3);
  update();
}
function freqTxtChange(which){
  var txt=document.getElementById('freq_'+which+'_txt');
  var slider=document.getElementById('freq_'+which);
  var v=parseFloat(txt.value);
  if(isNaN(v)){txt.value=parseFloat(slider.value).toFixed(3);return;}
  v=Math.max(parseFloat(slider.min),Math.min(parseFloat(slider.max),v));
  if(which==='lo'){var h=parseFloat(document.getElementById('freq_hi').value);if(v>h)v=h;}
  else{var l=parseFloat(document.getElementById('freq_lo').value);if(v<l)v=l;}
  txt.value=v.toFixed(3);slider.value=v;update();
}
function freqStep(which,dir){
  var fv=FREQ_VALS,txt=document.getElementById('freq_'+which+'_txt'),slider=document.getElementById('freq_'+which);
  var cur=parseFloat(txt.value),idx=-1;
  for(var i=0;i<fv.length;i++){if(Math.abs(fv[i]-cur)<0.0001){idx=i;break;}}
  if(idx<0){var best=0;for(var i=1;i<fv.length;i++){if(Math.abs(fv[i]-cur)<Math.abs(fv[best]-cur))best=i;}idx=best;}
  var ni=Math.max(0,Math.min(fv.length-1,idx+dir)),nv=fv[ni];
  if(which==='lo'){if(nv>parseFloat(document.getElementById('freq_hi').value))return;}
  else{if(nv<parseFloat(document.getElementById('freq_lo').value))return;}
  txt.value=nv.toFixed(3);slider.value=nv;update();
}
function freqKeyDown(e,which){
  if(e.key==='Enter')freqTxtChange(which);
  else if(e.key==='ArrowUp'){e.preventDefault();freqStep(which,1);}
  else if(e.key==='ArrowDown'){e.preventDefault();freqStep(which,-1);}
}
function setFreqBand(lo,hi){
  var s1=document.getElementById('freq_lo'),s2=document.getElementById('freq_hi');
  s1.value=Math.max(parseFloat(s1.min),lo);
  s2.value=Math.min(parseFloat(s2.max),hi);
  document.getElementById('freq_lo_txt').value=parseFloat(s1.value).toFixed(3);
  document.getElementById('freq_hi_txt').value=parseFloat(s2.value).toFixed(3);
  update();
}
function applyFilters(data){
  var freqLo=parseFloat(document.getElementById('freq_lo').value);
  var freqHi=parseFloat(document.getElementById('freq_hi').value);
  var selections={};
  GROUP_COLS.forEach(function(pair){selections[pair[0]]=getSelected(pair[0]);});
  return data.filter(function(r){
    if(r.Frequency_MHz<freqLo||r.Frequency_MHz>freqHi) return false;
    for(var col in selections){
      var allowed=selections[col];
      if(!allowed.length) return false;
      var v=String(r[col]===null||r[col]===undefined?'':r[col]);
      if(allowed.indexOf(v)<0) return false;
    }
    return true;
  });
}
function applyPassOnly(data){
  var passOnly=document.getElementById('pass_only').checked;
  if(!passOnly) return data;
  return data.filter(function(r){
    var hi=r.Upper_Limit!=null?Number(r.Upper_Limit):NaN;
    var lo=r.Lower_Limit!=null?Number(r.Lower_Limit):NaN;
    if(isNaN(hi)&&isNaN(lo)) return false;
    if(!isNaN(hi)&&r.Value>hi) return false;
    if(!isNaN(lo)&&r.Value<lo) return false;
    return true;
  });
}
function update(){
  var freqLo=parseFloat(document.getElementById('freq_lo').value);
  var groupFiltered=applyFilters(DATA);
  var filtered=applyPassOnly(groupFiltered);
  var values=filtered.map(function(r){return r.Value;});
  document.getElementById('n_points').textContent=filtered.length.toLocaleString()+' pts';
  var shapes=[],annotations=[];
  var hiSpecs={},loSpecs={};
  groupFiltered.forEach(function(r){
    if(r.Frequency_MHz<=freqLo) return;
    if(r.Upper_Limit!=null&&!isNaN(Number(r.Upper_Limit)))
      hiSpecs[Math.round(Number(r.Upper_Limit)*100)/100]=true;
    if(r.Lower_Limit!=null&&!isNaN(Number(r.Lower_Limit)))
      loSpecs[Math.round(Number(r.Lower_Limit)*100)/100]=true;
  });
  if(!Object.keys(hiSpecs).length&&HI_SPEC!==null) hiSpecs[Math.round(HI_SPEC*100)/100]=true;
  if(!Object.keys(loSpecs).length&&LO_SPEC!==null) loSpecs[Math.round(LO_SPEC*100)/100]=true;
  Object.keys(hiSpecs).map(Number).sort(function(a,b){return a-b;}).forEach(function(v){
    shapes.push({type:'line',xref:'x',x0:v,x1:v,y0:0,y1:1,yref:'paper',line:{color:'red',dash:'dash',width:1.5}});
    annotations.push({xref:'x',yref:'paper',x:v,y:0.98,text:'Spec '+v,showarrow:false,xanchor:'left',yanchor:'top',font:{color:'red',size:11}});
  });
  Object.keys(loSpecs).map(Number).sort(function(a,b){return b-a;}).forEach(function(v){
    shapes.push({type:'line',xref:'x',x0:v,x1:v,y0:0,y1:1,yref:'paper',line:{color:'red',dash:'dash',width:1.5}});
    annotations.push({xref:'x',yref:'paper',x:v,y:0.98,text:'Spec '+v,showarrow:false,xanchor:'right',yanchor:'top',font:{color:'red',size:11}});
  });
  var layout={
    title:{text:TITLE,x:0.5,font:{size:15}},
    template:'plotly_white',
    xaxis:{title:Y_LABEL,range:Y_LIM},
    yaxis:{title:'Count'},
    shapes:shapes,annotations:annotations,height:480,
    margin:{l:60,r:30,t:60,b:60}
  };
  Plotly.react('plot',[{type:'histogram',x:values,nbinsx:60,marker:{color:'steelblue',opacity:0.7},name:'Data'}],layout);
  updateDistStats(values);
  saveState();
}
function mean(arr){var s=0;for(var i=0;i<arr.length;i++)s+=arr[i];return arr.length?s/arr.length:NaN;}
function stddev(arr,mu){
  if(arr.length<2) return 0;
  var s=0;for(var i=0;i<arr.length;i++)s+=Math.pow(arr[i]-mu,2);
  return Math.sqrt(s/(arr.length-1));
}
function updateDistStats(values){
  var el=document.getElementById('dist_stats_panel');
  if(!el) return;
  var n=values.length;
  if(!n){el.innerHTML='<i>No data</i>';return;}
  var sorted=values.slice().sort(function(a,b){return a-b;});
  var mu=mean(values);
  var s=stddev(values,mu);
  var median=n%2?sorted[Math.floor(n/2)]:(sorted[n/2-1]+sorted[n/2])/2;
  /* skewness (adjusted Fisher–Pearson) */
  var sk=0,ku=0;
  for(var i=0;i<n;i++){var z=(values[i]-mu)/s;sk+=Math.pow(z,3);ku+=Math.pow(z,4);}
  var skew=n>2?(n/((n-1)*(n-2)))*sk:NaN;
  var kurt=n>3?((n*(n+1))/((n-1)*(n-2)*(n-3)))*ku - (3*(n-1)*(n-1))/((n-2)*(n-3)):NaN;
  /* normality classification */
  var label,color;
  if(Math.abs(skew)<=0.5&&Math.abs(kurt)<=1){label='Normal';color='#2ca02c';}
  else if(Math.abs(skew)>1.5){
    label=skew>0?'Right-skewed (positive)':'Left-skewed (negative)';color='#d62728';
  } else if(Math.abs(kurt)>3){label='Heavy-tailed (leptokurtic)';color='#d62728';}
  else if(kurt<-1){label='Light-tailed (platykurtic)';color='#ff7f0e';}
  else {label='Mild skew / non-normal';color='#ff7f0e';}
  function fmt(v,d){return isNaN(v)?'—':v.toFixed(d!==undefined?d:4);}
  el.innerHTML='<table class="stbl"><tr>'
    +'<th>n</th><th>Mean</th><th>Std</th><th>Median</th><th>Skewness</th><th>Excess Kurt.</th><th>Distribution</th></tr>'
    +'<tr><td>'+n+'</td><td>'+fmt(mu,4)+'</td><td>'+fmt(s,4)+'</td><td>'+fmt(median,4)+'</td>'
    +'<td>'+fmt(skew,3)+'</td><td>'+fmt(kurt,3)+'</td>'
    +'<td style="color:'+color+';font-weight:bold">'+label+'</td></tr></table>';
}
function resetFilters(){
  _stClear();
  GROUP_COLS.forEach(function(pair){
    var col=pair[0];
    document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){c.checked=true;});
    var allChk=document.getElementById('all_'+col);
    if(allChk){allChk.checked=true;allChk.indeterminate=false;}
    document.getElementById('badge_'+col).classList.remove('active');
  });
  document.getElementById('pass_only').checked=false;
  document.getElementById('freq_lo').value=FREQ_MIN;
  document.getElementById('freq_hi').value=FREQ_MAX;
  document.getElementById('freq_lo_txt').value=parseFloat(FREQ_MIN).toFixed(3);
  document.getElementById('freq_hi_txt').value=parseFloat(FREQ_MAX).toFixed(3);
  update();
}
/* ---- localStorage state persistence ---- */
function _stGet(k){try{return localStorage.getItem(STATE_KEY+k);}catch(e){return null;}}
function _stSet(k,v){try{localStorage.setItem(STATE_KEY+k,v);}catch(e){}}
function _stClear(){try{var keys=[];for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);if(k&&k.indexOf(STATE_KEY)===0)keys.push(k);}keys.forEach(function(k){localStorage.removeItem(k);});}catch(e){}}
function saveState(){
  _stSet('freq_lo',document.getElementById('freq_lo').value);
  _stSet('freq_hi',document.getElementById('freq_hi').value);
  document.querySelectorAll('.env_chk').forEach(function(c){_stSet('temp_'+c.value,c.checked?'1':'0');});
}
function loadState(){
  var lo=_stGet('freq_lo'),hi=_stGet('freq_hi');
  if(lo!==null){var sl=document.getElementById('freq_lo');if(sl){sl.value=lo;var tx=document.getElementById('freq_lo_txt');if(tx)tx.value=parseFloat(lo).toFixed(3);}}
  if(hi!==null){var sh=document.getElementById('freq_hi');if(sh){sh.value=hi;var th=document.getElementById('freq_hi_txt');if(th)th.value=parseFloat(hi).toFixed(3);}}
  document.querySelectorAll('.env_chk').forEach(function(c){var s=_stGet('temp_'+c.value);if(s!==null&&!c.disabled)c.checked=(s==='1');});
}
loadState();
update();
"""

    html = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f"<style>{css}</style>\n"
        "</head>\n<body>\n"
        '<div class="ctrl-bar">\n'
        f'  {panels_html}\n'
        '  <div class="sep"></div>\n'
        '  <label><input type="checkbox" id="pass_only" onchange="update()"> Pass&nbsp;only</label>\n'
        '  <div class="sep"></div>\n'
        f'  <label>Freq&nbsp;min:<input type="range" id="freq_lo"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_min:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<input class="freq-txt" id="freq_lo_txt" type="text" value="{freq_min:.3f}"'
        f' onchange="freqTxtChange(\'lo\')"'
        f' onkeydown="freqKeyDown(event,\'lo\')">&nbsp;MHz</label>\n'
        f'  <label>Freq&nbsp;max:<input type="range" id="freq_hi"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_max:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<input class="freq-txt" id="freq_hi_txt" type="text" value="{freq_max:.3f}"'
        f' onchange="freqTxtChange(\'hi\')"'
        f' onkeydown="freqKeyDown(event,\'hi\')">&nbsp;MHz</label>\n'
        f'{band_section_html}'
        '  <div class="sep"></div>\n'
        '  <button class="reset-btn" onclick="resetFilters()">Reset</button>\n'
        '  <span id="n_points"></span>\n'
        "</div>\n"
        '<div id="plot"></div>\n'
        '<div id="dist_stats_panel" style="padding:4px 8px;overflow-x:auto"></div>\n'
        f"<script>{_get_plotlyjs()}</script>\n"
        "<script>\n"
        + constants + "\n"
        + dist_js
        + "</script>\n</body>\n</html>"
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


def _build_env_distribution_html(df: pd.DataFrame, cfg: dict, title: str) -> str:
    """
    Pre-computed KDE distribution plot.

    Computes KDE curves server-side per (SpurType, Temperature) and embeds compact
    (x, y, n) arrays instead of raw measurement values, keeping HTML well under 10 MB
    regardless of dataset size.

    Supports SpurType / Serial / Port / Temperature filters and Abs / ΔTemp modes.
    """
    if df.empty or len(df) < 5:
        return (
            "<!DOCTYPE html><html><body style='font-family:Arial;padding:16px'>"
            f"<h3>{title}</h3><p><i>No data available.</i></p></body></html>"
        )

    # -------------------------------------------------------------------------
    # 0.  KDE helpers
    # -------------------------------------------------------------------------
    try:
        from scipy.stats import gaussian_kde as _gkde
        _has_scipy = True
    except ImportError:
        _has_scipy = False

    def _hist_density(arr, xs):
        nb = min(50, max(5, len(arr) // 4))
        counts, edges = np.histogram(arr, bins=nb, density=True)
        ctrs = (edges[:-1] + edges[1:]) / 2
        return np.interp(xs, ctrs, counts, left=0.0, right=0.0)

    def _kde_curve(vals_list, n_pts: int = 250):
        """Return {x, y, n} KDE curve dict or None if insufficient data."""
        arr = np.array([v for v in vals_list if v == v], dtype=float)
        n = len(arr)
        if n < 4:
            return None
        lo, hi = float(arr.min()), float(arr.max())
        if hi <= lo:
            hi = lo + 0.001
        pad = max((hi - lo) * 0.25, 0.01)
        xs = np.linspace(lo - pad, hi + pad, n_pts)
        if _has_scipy:
            try:
                ys = _gkde(arr, bw_method="silverman")(xs)
            except Exception:
                ys = _hist_density(arr, xs)
        else:
            ys = _hist_density(arr, xs)
        return {
            "x": [round(float(v), 4) for v in xs],
            "y": [round(float(v), 6) for v in ys],
            "n": n,
        }

    # -------------------------------------------------------------------------
    # 1.  Metadata
    # -------------------------------------------------------------------------
    lo_spec, hi_spec = _get_spec(df, cfg)
    y_label = cfg.get("y_label", "Value")
    y_lim   = cfg.get("y_lim")

    temps_all = sorted(str(t) for t in df["Temperature"].dropna().unique())
    temps_present = (["Room"] if "Room" in temps_all else []) + [
        t for t in temps_all if t != "Room"
    ]
    non_room_temps = [t for t in temps_present if t != "Room"]

    all_serials = sorted(str(s) for s in df["Serial"].dropna().unique())
    port_col = "_grp_Port" if "_grp_Port" in df.columns else None
    all_ports = sorted(str(p) for p in df[port_col].dropna().unique()) if port_col else []

    # SpurType — use pre-parsed column if available, else HarmonicNumber, else regex on Group string
    df = df.copy()
    if "_grp_SpurType" in df.columns:
        df["_spur"] = df["_grp_SpurType"].fillna("").astype(str)
    elif "_grp_HarmonicNumber" in df.columns:
        def _fmt_hn(v):
            try:
                return f"H{float(v):g}" if v else ""
            except (ValueError, TypeError):
                return str(v) if v else ""
        df["_spur"] = df["_grp_HarmonicNumber"].fillna("").astype(str).apply(_fmt_hn)
    else:
        _spur_re_dist = re.compile(r"SpurType:\s*(.+?)(?:\s{2,}|$)", re.IGNORECASE)
        def _extr_spur(g):
            m = _spur_re_dist.search(g)
            return m.group(1).strip() if m else ""
        df["_spur"] = df["Group"].fillna("").astype(str).apply(_extr_spur)

    spur_types = sorted(s for s in df["_spur"].unique() if s)
    if not spur_types:
        spur_types = ["(all)"]
        df["_spur"] = "(all)"

    # Port per serial (used in delta table)
    dut_port_map: dict = {}
    if port_col:
        for serial in all_serials:
            pts = df[df["Serial"] == serial][port_col].dropna().unique()
            dut_port_map[serial] = str(pts[0]) if len(pts) == 1 else ""
    else:
        dut_port_map = {s: "" for s in all_serials}

    _pal = ["#2196F3", "#F44336", "#4CAF50", "#FF9800", "#9C27B0",
            "#00BCD4", "#795548", "#E91E63", "#8BC34A", "#607D8B"]
    temp_colors = {t: _pal[i % len(_pal)] for i, t in enumerate(temps_present)}
    spur_colors = {s: _pal[i % len(_pal)] for i, s in enumerate(spur_types)}

    # -------------------------------------------------------------------------
    # 2.  Abs mode KDE: aggregate per (SpurType, Temperature)
    # -------------------------------------------------------------------------
    print(
        f"    Computing KDE curves ({len(spur_types)} spurs x {len(temps_present)} temps)...",
        flush=True,
    )
    kde_abs: list = []
    for spur in spur_types:
        row: list = []
        spur_df = df[df["_spur"] == spur]
        for temp in temps_present:
            vals = spur_df[spur_df["Temperature"] == temp]["Value"].dropna().tolist()
            row.append(_kde_curve(vals))
        kde_abs.append(row)

    # -------------------------------------------------------------------------
    # 3.  Delta from Room: merge non-room rows with room rows
    # -------------------------------------------------------------------------
    room_base = (
        df[df["Temperature"] == "Room"][["Serial", "_spur", "Frequency_MHz", "Value"]]
        .copy()
        .rename(columns={"Value": "Room_Value"})
    )
    non_room_base = df[df["Temperature"] != "Room"].copy()

    merged = pd.DataFrame()
    if len(room_base) > 0 and len(non_room_base) > 0:
        merged = non_room_base.merge(
            room_base, on=["Serial", "_spur", "Frequency_MHz"], how="inner"
        )
        merged["delta"] = merged["Value"] - merged["Room_Value"]

    # -------------------------------------------------------------------------
    # 4.  Delta mode KDE + per-DUT delta stats
    # -------------------------------------------------------------------------
    print(
        f"    Computing delta KDE ({len(spur_types)} spurs x {len(non_room_temps)} non-room temps)...",
        flush=True,
    )
    kde_delta: list = []    # [si][di] = curve or None
    dut_delta: list = []    # [si][di] = [{serial,port,mean,p10,p90,n}|None, ...]

    for spur in spur_types:
        row_kde: list = []
        row_dut: list = []
        spur_m = merged[merged["_spur"] == spur] if len(merged) > 0 else pd.DataFrame()
        for temp in non_room_temps:
            temp_m = spur_m[spur_m["Temperature"] == temp] if len(spur_m) > 0 else pd.DataFrame()
            agg_vals = temp_m["delta"].dropna().tolist() if len(temp_m) > 0 else []
            row_kde.append(_kde_curve(agg_vals))
            dut_entries: list = []
            for serial in all_serials:
                s_m = temp_m[temp_m["Serial"] == serial] if len(temp_m) > 0 else pd.DataFrame()
                dv = s_m["delta"].dropna().tolist() if len(s_m) > 0 else []
                if dv:
                    dut_entries.append({
                        "serial": serial,
                        "port": dut_port_map.get(serial, ""),
                        "mean": round(float(np.mean(dv)), 4),
                        "p10":  round(float(np.percentile(dv, 10)), 4),
                        "p90":  round(float(np.percentile(dv, 90)), 4),
                        "n":    len(dv),
                    })
                else:
                    dut_entries.append(None)
            row_dut.append(dut_entries)
        kde_delta.append(row_kde)
        dut_delta.append(row_dut)

    # -------------------------------------------------------------------------
    # 5.  Overall delta stats per non-room temperature
    # -------------------------------------------------------------------------
    delta_stats: dict = {}
    for temp in non_room_temps:
        temp_m = merged[merged["Temperature"] == temp] if len(merged) > 0 else pd.DataFrame()
        if len(temp_m) > 0:
            dv = temp_m["delta"].dropna().tolist()
            delta_stats[temp] = {
                "n":      len(dv),
                "mean":   round(float(np.mean(dv)), 4) if dv else 0.0,
                "std":    round(float(np.std(dv, ddof=1)), 4) if len(dv) > 1 else 0.0,
                "median": round(float(np.median(dv)), 4) if dv else 0.0,
            }

    # -------------------------------------------------------------------------
    # 5b.  Raw data for client-side freq-filtered KDE
    # -------------------------------------------------------------------------
    all_freqs_sorted = sorted(float(f) for f in df["Frequency_MHz"].dropna().unique())
    dist_freq_min = round(all_freqs_sorted[0], 1) if all_freqs_sorted else 0.0
    dist_freq_max = round(all_freqs_sorted[-1], 1) if all_freqs_sorted else 1000.0

    _abs_cols = ["Frequency_MHz", "Value", "Upper_Limit", "Lower_Limit", "Serial"]
    if port_col:
        _abs_cols.append(port_col)

    raw_abs: list = []
    for spur in spur_types:
        spur_df = df[df["_spur"] == spur]
        row: list = []
        for temp in temps_present:
            t_df = spur_df[spur_df["Temperature"] == temp][_abs_cols].dropna(subset=["Frequency_MHz", "Value"])
            row.append({
                "f":  [round(float(x), 1) for x in t_df["Frequency_MHz"]],
                "v":  [round(float(x), 2) for x in t_df["Value"]],
                "s":  [str(x) for x in t_df["Serial"]],
                "p":  [str(x) for x in t_df[port_col]] if port_col else [""] * len(t_df),
                "hi": [None if pd.isna(x) else round(float(x), 2) for x in t_df["Upper_Limit"]],
                "lo": [None if pd.isna(x) else round(float(x), 2) for x in t_df["Lower_Limit"]],
            })
        raw_abs.append(row)

    raw_delta: list = []
    for spur in spur_types:
        spur_m = merged[merged["_spur"] == spur] if len(merged) > 0 else pd.DataFrame()
        row = []
        for temp in non_room_temps:
            t_m = spur_m[spur_m["Temperature"] == temp] if len(spur_m) > 0 else pd.DataFrame()
            if len(t_m) > 0:
                row.append({
                    "f": [round(float(x), 1) for x in t_m["Frequency_MHz"]],
                    "d": [round(float(x), 3) for x in t_m["delta"]],
                    "s": [str(x) for x in t_m["Serial"]],
                    "p": [str(x) for x in t_m[port_col]] if port_col and port_col in t_m.columns else [""] * len(t_m),
                })
            else:
                row.append({"f": [], "d": [], "s": [], "p": []})
        raw_delta.append(row)

    # -------------------------------------------------------------------------
    # 6.  JS constants (compact — no raw measurement values)
    # -------------------------------------------------------------------------
    lo_js = "null" if np.isnan(lo_spec) else repr(float(lo_spec))
    hi_js = "null" if np.isnan(hi_spec) else repr(float(hi_spec))

    constants = "\n".join([
        f"var STATE_KEY='padb_{cfg.get('results_dir', '')}';",
        f"var SPUR_TYPES={json.dumps(spur_types)};",
        f"var SPUR_COLORS={json.dumps(spur_colors)};",
        f"var TEMPS={json.dumps(temps_present)};",
        f"var NON_ROOM_TEMPS={json.dumps(non_room_temps)};",
        f"var TEMP_COLORS={json.dumps(temp_colors)};",
        f"var SERIALS={json.dumps(all_serials)};",
        f"var PORTS={json.dumps(all_ports)};",
        f"var KDE_ABS={json.dumps(kde_abs)};",
        f"var KDE_DELTA={json.dumps(kde_delta)};",
        f"var DUT_DELTA={json.dumps(dut_delta)};",
        f"var DELTA_STATS={json.dumps(delta_stats)};",
        f"var LO_SPEC={lo_js};",
        f"var HI_SPEC={hi_js};",
        f"var Y_LABEL={json.dumps(y_label)};",
        f"var Y_LIM={json.dumps(y_lim)};",
        f"var TITLE={json.dumps(title)};",
        f"var RAW_ABS={json.dumps(raw_abs)};",
        f"var RAW_DELTA={json.dumps(raw_delta)};",
        f"var DUT_PORT_MAP={json.dumps(dut_port_map)};",
        f"var DIST_FREQ_MIN={dist_freq_min};",
        f"var DIST_FREQ_MAX={dist_freq_max};",
    ])

    # -------------------------------------------------------------------------
    # 7.  CSS
    # -------------------------------------------------------------------------
    css = (
        "body{font-family:Arial,sans-serif;margin:0;padding:8px;}"
        ".env-bar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;"
        "padding:6px 14px;background:#edf7ee;border-radius:6px;margin-bottom:4px;"
        "font-size:13px;border:1px solid #c8e6c9;}"
        ".filter-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;"
        "padding:5px 14px;background:#f5f5f5;border-radius:6px;margin-bottom:4px;"
        "font-size:13px;border:1px solid #e0e0e0;}"
        ".filter-lbl{font-weight:600;color:#444;margin-right:4px;white-space:nowrap;}"
        "button.sel-btn{font-size:11px;padding:1px 7px;border:1px solid #bbb;"
        "border-radius:3px;cursor:pointer;background:#fff;white-space:nowrap;}"
        "button.sel-btn:hover{background:#e8e8e8;}"
        ".dist-filter-wrap{position:relative;display:inline-block;}"
        ".dist-filter-btn{font-size:12px;padding:2px 9px;border:1px solid #bbb;border-radius:3px;"
        "cursor:pointer;background:#fff;white-space:nowrap;}"
        ".dist-filter-btn:hover{background:#e8e8e8;}"
        ".dist-filter-panel{display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:200;"
        "background:#fff;border:1px solid #ccc;border-radius:4px;"
        "box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:140px;max-height:300px;"
        "overflow-y:auto;padding:5px 7px;}"
        ".dist-filter-panel.open{display:block;}"
        ".dist-fitem{display:block;padding:2px 0;cursor:pointer;white-space:nowrap;font-size:12px;}"
        ".dist-fall{padding-bottom:2px;}"
        ".dist-fdiv{margin:3px 0;border:none;border-top:1px solid #eee;}"
        ".dist-badge{font-size:10px;background:#0066cc;color:#fff;border-radius:10px;"
        "padding:1px 5px;margin-right:2px;display:none;}"
        ".dist-badge.active{display:inline;}"
        ".ctrl-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:8px 14px;background:#f0f2f5;border-radius:6px;margin-bottom:8px;font-size:13px;}"
        ".ctrl-bar label{white-space:nowrap;}"
        ".sep{border-left:2px solid #ccc;height:22px;margin:0 2px;}"
        "button.reset-btn{font-size:12px;padding:2px 10px;border:1px solid #999;"
        "border-radius:3px;cursor:pointer;background:#fff;}"
        "button.reset-btn:hover{background:#e8e8e8;}"
        "#n_pts{font-size:12px;color:#666;margin-left:auto;}"
        ".stbl{border-collapse:collapse;font-size:12px;margin:4px 0;}"
        ".stbl th{background:#e8eaf6;padding:4px 10px;text-align:left;border:1px solid #ccc;white-space:nowrap;}"
        ".stbl td{padding:3px 10px;border:1px solid #ddd;white-space:nowrap;}"
        ".panel-section{padding:4px 14px 8px 14px;}"
        ".panel-title{font-size:13px;font-weight:600;margin-bottom:4px;color:#444;}"
        "#multimodal_warn{background:#fff3e0;border:1px solid #ffcc02;border-radius:4px;"
        "padding:4px 10px;font-size:12px;color:#e65100;display:none;"
        "margin:0 14px 4px 14px;}"
        "#kde_plot{margin:4px 0;height:450px;}"
    )

    # -------------------------------------------------------------------------
    # 8.  Filter panel HTML helper
    # -------------------------------------------------------------------------
    def _filter_panel(panel_id, label, opts_list, trigger_fn="update"):
        items = "".join(
            f'<label class="dist-fitem">'
            f'<input type="checkbox" class="dist_{panel_id}_chk"'
            f' value="{v}" checked onchange="{trigger_fn}()">&nbsp;{v}</label>\n'
            for v in opts_list
        )
        return (
            f'<div class="dist-filter-wrap">'
            f'<button class="dist-filter-btn" onclick="toggleDistPanel(\'{panel_id}\')">'
            f'<span id="dist_badge_{panel_id}" class="dist-badge"></span>{label}&thinsp;&#9662;</button>'
            f'<div class="dist-filter-panel" id="dist_panel_{panel_id}">'
            f'<label class="dist-fitem dist-fall">'
            f'<input type="checkbox" id="dist_all_{panel_id}"'
            f' checked onchange="distToggleAll(\'{panel_id}\')">&nbsp;<b>All</b></label>'
            f'<hr class="dist-fdiv">{items}</div></div>'
        )

    env_chks = "".join(
        f'<label><input type="checkbox" class="env_chk" value="{t}"'
        f' checked onchange="update()">&nbsp;{t}</label>\n'
        for t in temps_present
    )
    spur_panel_html  = _filter_panel("spur", "Spur Type", spur_types) if len(spur_types) > 1 else ""
    ser_panel_html   = _filter_panel("ser", "Serial", all_serials, "update")
    port_panel_html  = _filter_panel("port", "Port", all_ports, "update") if all_ports else ""

    # -------------------------------------------------------------------------
    # 9.  JavaScript (raw string — no f-string interpolation)
    # -------------------------------------------------------------------------
    dist_js = r"""
function _fd(v,d){return(v===null||v===undefined||isNaN(+v))?'--':Number(v).toFixed(d!==undefined?d:3);}

/* ---- localStorage helpers ---- */
function _stGet(k){try{return localStorage.getItem(STATE_KEY+k);}catch(e){return null;}}
function _stSet(k,v){try{localStorage.setItem(STATE_KEY+k,v);}catch(e){}}
function saveState(){
  var vm=document.querySelector('input[name="view_mode"]:checked');
  if(vm)_stSet('dist_view',vm.value);
  var lo=document.getElementById('dist_freq_lo'),hi=document.getElementById('dist_freq_hi');
  if(lo)_stSet('dist_freq_lo',lo.value);if(hi)_stSet('dist_freq_hi',hi.value);
  document.querySelectorAll('.env_chk').forEach(function(c){_stSet('temp_'+c.value,c.checked?'1':'0');});
  document.querySelectorAll('.dist_spur_chk').forEach(function(c){_stSet('dist_spur_'+c.value,c.checked?'1':'0');});
  document.querySelectorAll('.dist_port_chk').forEach(function(c){_stSet('dist_port_'+c.value,c.checked?'1':'0');});
}
function loadState(){
  var vm=_stGet('dist_view');
  if(vm){var el=document.querySelector('input[name="view_mode"][value="'+vm+'"]');if(el)el.checked=true;}
  var lo=_stGet('dist_freq_lo');
  if(lo!=null){var sl=document.getElementById('dist_freq_lo');if(sl){sl.value=lo;var lt=document.getElementById('dist_freq_lo_txt');if(lt)lt.value=parseFloat(lo).toFixed(1);}}
  var hi=_stGet('dist_freq_hi');
  if(hi!=null){var sh=document.getElementById('dist_freq_hi');if(sh){sh.value=hi;var ht=document.getElementById('dist_freq_hi_txt');if(ht)ht.value=parseFloat(hi).toFixed(1);}}
  document.querySelectorAll('.env_chk').forEach(function(c){var s=_stGet('temp_'+c.value);if(s!==null&&!c.disabled)c.checked=(s==='1');});
  document.querySelectorAll('.dist_spur_chk').forEach(function(c){var s=_stGet('dist_spur_'+c.value);if(s!==null)c.checked=(s==='1');});
  /* If all spur checkboxes ended up deselected (stale saved state), restore all */
  var spurChks=Array.from(document.querySelectorAll('.dist_spur_chk'));
  if(spurChks.length&&!spurChks.some(function(c){return c.checked;}))
    spurChks.forEach(function(c){c.checked=true;});
  document.querySelectorAll('.dist_port_chk').forEach(function(c){var s=_stGet('dist_port_'+c.value);if(s!==null)c.checked=(s==='1');});
  ['spur','ser','port'].forEach(_distUpdateBadge);
}

/* ---- dropdown filter panels ---- */
function toggleDistPanel(id){
  var p=document.getElementById('dist_panel_'+id);
  if(!p) return;
  document.querySelectorAll('.dist-filter-panel').forEach(function(el){
    if(el!==p) el.classList.remove('open');
  });
  p.classList.toggle('open');
}
document.addEventListener('click',function(e){
  if(!e.target.closest('.dist-filter-wrap'))
    document.querySelectorAll('.dist-filter-panel').forEach(function(p){p.classList.remove('open');});
});
function distToggleAll(id){
  var allChk=document.getElementById('dist_all_'+id);
  var v=allChk?allChk.checked:true;
  document.querySelectorAll('.dist_'+id+'_chk').forEach(function(c){c.checked=v;});
  _distUpdateBadge(id);
  update();
}
function _distUpdateBadge(id){
  var chks=Array.from(document.querySelectorAll('.dist_'+id+'_chk'));
  var total=chks.length,checked=chks.filter(function(c){return c.checked;}).length;
  var badge=document.getElementById('dist_badge_'+id);
  var allEl=document.getElementById('dist_all_'+id);
  if(allEl) allEl.checked=(checked===total);
  if(!badge) return;
  if(checked<total){badge.textContent=checked+'/'+total;badge.classList.add('active');}
  else{badge.textContent='';badge.classList.remove('active');}
}

/* ---- selection helpers ---- */
function _getChecked(cls){
  return Array.from(document.querySelectorAll('.'+cls+':checked')).map(function(c){return c.value;});
}
function getSelSpurIdxs(){
  var chks=document.querySelectorAll('.dist_spur_chk');
  if(!chks.length) return SPUR_TYPES.map(function(_,i){return i;});
  var sel=new Set(_getChecked('dist_spur_chk'));
  return SPUR_TYPES.map(function(_,i){return i;}).filter(function(i){return sel.has(SPUR_TYPES[i]);});
}
function getSelTempIdxs(){
  var sel=new Set(_getChecked('env_chk'));
  return TEMPS.map(function(_,i){return i;}).filter(function(i){return sel.has(TEMPS[i]);});
}
function getSelNonRoomIdxs(){
  var sel=new Set(_getChecked('env_chk'));
  return NON_ROOM_TEMPS.map(function(_,i){return i;}).filter(function(i){return sel.has(NON_ROOM_TEMPS[i]);});
}
function getSelSerials(){
  var chks=document.querySelectorAll('.dist_ser_chk');
  if(!chks.length) return new Set(SERIALS);
  return new Set(_getChecked('dist_ser_chk'));
}
function getSelPorts(){
  var chks=document.querySelectorAll('.dist_port_chk');
  if(!chks.length) return new Set(PORTS.length?PORTS:['']);
  var sel=new Set(_getChecked('dist_port_chk'));
  return sel.size?sel:new Set(PORTS.length?PORTS:['']);
}

/* ---- freq range helpers ---- */
function getFreqRange(){
  var lo=parseFloat(document.getElementById('dist_freq_lo').value);
  var hi=parseFloat(document.getElementById('dist_freq_hi').value);
  return {lo:isNaN(lo)?-Infinity:lo,hi:isNaN(hi)?Infinity:hi};
}
function syncFreqDist(){
  var lo=document.getElementById('dist_freq_lo');
  var hi=document.getElementById('dist_freq_hi');
  if(!lo||!hi) return;
  var loV=parseFloat(lo.value),hiV=parseFloat(hi.value);
  if(loV>hiV){lo.value=hiV;loV=hiV;}
  document.getElementById('dist_freq_lo_txt').value=loV.toFixed(1);
  document.getElementById('dist_freq_hi_txt').value=parseFloat(hi.value).toFixed(1);
}
function freqDistTxtChange(which){
  var txt=document.getElementById('dist_freq_'+which+'_txt');
  var slider=document.getElementById('dist_freq_'+which);
  if(!txt||!slider) return;
  var v=parseFloat(txt.value);
  if(isNaN(v)){txt.value=parseFloat(slider.value).toFixed(1);return;}
  v=Math.max(parseFloat(slider.min),Math.min(parseFloat(slider.max),v));
  if(which==='lo'){var h=parseFloat(document.getElementById('dist_freq_hi').value);if(v>h)v=h;}
  else{var l=parseFloat(document.getElementById('dist_freq_lo').value);if(v<l)v=l;}
  txt.value=v.toFixed(1);slider.value=v;update();
}
function freqDistKeyDown(e,which){if(e.key==='Enter')freqDistTxtChange(which);}

/* ---- client-side Gaussian KDE (Silverman bandwidth) ---- */
function jsKde(vals,nPts){
  nPts=nPts||200;
  var n=vals.length;
  if(n<4) return null;
  var sorted=vals.slice().sort(function(a,b){return a-b;});
  var lo=sorted[0],hi=sorted[n-1];
  if(hi<=lo) hi=lo+0.001;
  var mn=vals.reduce(function(a,b){return a+b;},0)/n;
  var vr=vals.reduce(function(a,b){return a+(b-mn)*(b-mn);},0)/(n>1?n-1:1);
  var std=Math.sqrt(vr);
  var q1=sorted[Math.floor(0.25*n)],q3=sorted[Math.min(n-1,Math.ceil(0.75*n))];
  var iqr=q3-q1;
  var bw=0.9*Math.min(std,(iqr>0?iqr/1.34:std))*Math.pow(n,-0.2);
  if(bw<=0||!isFinite(bw)) bw=(hi-lo)/10;
  var pad=Math.max((hi-lo)*0.25,0.01);
  var x0=lo-pad,x1=hi+pad,dx=(x1-x0)/(nPts-1);
  var xs=[],ys=[],c1=1/(n*bw*Math.sqrt(2*Math.PI)),c2=-0.5/(bw*bw);
  for(var j=0;j<nPts;j++){
    var x=x0+j*dx;
    xs.push(Math.round(x*1e4)/1e4);
    var y=0;
    for(var i=0;i<n;i++){var z=x-vals[i];y+=Math.exp(c2*z*z);}
    ys.push(Math.round(y*c1*1e6)/1e6);
  }
  return {x:xs,y:ys,n:n};
}

/* ---- main KDE plot update ---- */
function update(){
  _distUpdateBadge('spur');
  var modeEl=document.querySelector('input[name="view_mode"]:checked');
  var isAbs=modeEl?modeEl.value==='abs':false;
  var spurs=getSelSpurIdxs();
  var traces=[];
  var multiSpur=spurs.length>1;
  var fr=getFreqRange();
  var freqFlt=(typeof DIST_FREQ_MIN!=='undefined')&&
    (fr.lo>DIST_FREQ_MIN+0.09||fr.hi<DIST_FREQ_MAX-0.09);

  var selSer=getSelSerials();
  var selPor=getSelPorts();
  var serFlt=(selSer.size<SERIALS.length)||(PORTS.length>0&&selPor.size<PORTS.length);

  if(isAbs){
    var tempIdxs=getSelTempIdxs();
    spurs.forEach(function(si){
      tempIdxs.forEach(function(ti){
        var kde;
        if((freqFlt||serFlt)&&RAW_ABS&&RAW_ABS[si]&&RAW_ABS[si][ti]){
          var raw=RAW_ABS[si][ti],vals=[];
          for(var i=0;i<raw.f.length;i++){
            if(raw.f[i]>=fr.lo&&raw.f[i]<=fr.hi){
              if(!serFlt){vals.push(raw.v[i]);}
              else{
                var ser=raw.s?raw.s[i]:'';
                var port=raw.p?raw.p[i]:'';
                if(selSer.has(ser)&&(!PORTS.length||selPor.has(port))) vals.push(raw.v[i]);
              }
            }
          }
          kde=jsKde(vals);
        } else {
          kde=KDE_ABS[si]&&KDE_ABS[si][ti];
        }
        if(!kde) return;
        var col=TEMP_COLORS[TEMPS[ti]]||'#999';
        var spurLabel=SPUR_TYPES[si],tempLabel=TEMPS[ti];
        var name=multiSpur?(spurLabel+' — '+tempLabel):tempLabel;
        traces.push({x:kde.x,y:kde.y,type:'scatter',mode:'lines',name:name,
          line:{color:col,width:multiSpur?1:2},opacity:multiSpur?0.65:1.0,
          hovertemplate:'<b>'+spurLabel+'</b><br>'+tempLabel+'<br>'+Y_LABEL+': %{x:.3f}<br>density: %{y:.5f}<extra></extra>'});
      });
    });
    var warn=document.getElementById('multimodal_warn');
    if(warn) warn.style.display=multiSpur?'block':'none';
  } else {
    var nrIdxs=getSelNonRoomIdxs();
    spurs.forEach(function(si){
      nrIdxs.forEach(function(di){
        var kde;
        if((freqFlt||serFlt)&&RAW_DELTA&&RAW_DELTA[si]&&RAW_DELTA[si][di]){
          var raw=RAW_DELTA[si][di],vals=[];
          for(var i=0;i<raw.f.length;i++){
            if(raw.f[i]>=fr.lo&&raw.f[i]<=fr.hi){
              if(!serFlt){vals.push(raw.d[i]);}
              else{
                var ser=raw.s?raw.s[i]:'';
                var port=raw.p?raw.p[i]:'';
                if(selSer.has(ser)&&(!PORTS.length||selPor.has(port))) vals.push(raw.d[i]);
              }
            }
          }
          kde=jsKde(vals);
        } else {
          kde=KDE_DELTA[si]&&KDE_DELTA[si][di];
        }
        if(!kde) return;
        var temp=NON_ROOM_TEMPS[di],col=TEMP_COLORS[temp]||'#999';
        var spurLabel=SPUR_TYPES[si];
        var name=multiSpur?(spurLabel+' — Δ'+temp):('Δ'+temp);
        traces.push({x:kde.x,y:kde.y,type:'scatter',mode:'lines',name:name,
          line:{color:col,width:multiSpur?1:2},opacity:multiSpur?0.65:1.0,
          hovertemplate:'<b>'+spurLabel+'</b><br>Δ'+temp+'<br>Δ: %{x:.3f} dB<br>density: %{y:.5f}<extra></extra>'});
      });
    });
    var warn=document.getElementById('multimodal_warn');
    if(warn) warn.style.display='none';
  }

  /* Spec limit lines */
  var shapes=[];
  if(!isAbs){
    shapes.push({type:'line',xref:'x',yref:'paper',x0:0,x1:0,y0:0,y1:1,
      line:{color:'#888',width:1,dash:'dot'}});
  } else {
    var curHi=HI_SPEC,curLo=LO_SPEC;
    if(freqFlt&&RAW_ABS){
      var hiSet=[],loSet=[];
      spurs.forEach(function(si){
        if(!RAW_ABS[si]) return;
        RAW_ABS[si].forEach(function(raw){
          if(!raw||!raw.hi) return;
          for(var i=0;i<raw.f.length;i++){
            if(raw.f[i]>=fr.lo&&raw.f[i]<=fr.hi){
              if(raw.hi[i]!=null&&hiSet.indexOf(raw.hi[i])<0) hiSet.push(raw.hi[i]);
              if(raw.lo&&raw.lo[i]!=null&&loSet.indexOf(raw.lo[i])<0) loSet.push(raw.lo[i]);
            }
          }
        });
      });
      curHi=hiSet.length?Math.min.apply(null,hiSet):null;
      curLo=loSet.length?Math.max.apply(null,loSet):null;
    }
    if(curHi!==null) shapes.push({type:'line',xref:'x',yref:'paper',
      x0:curHi,x1:curHi,y0:0,y1:1,line:{color:'#F44336',width:1.5,dash:'dash'}});
    if(curLo!==null) shapes.push({type:'line',xref:'x',yref:'paper',
      x0:curLo,x1:curLo,y0:0,y1:1,line:{color:'#2196F3',width:1.5,dash:'dash'}});
  }

  var layout={
    title:{text:TITLE+(freqFlt?' ['+fr.lo.toFixed(1)+'–'+fr.hi.toFixed(1)+' MHz]':''),font:{size:14}},
    xaxis:{title:{text:isAbs?Y_LABEL:('ΔTemp ('+Y_LABEL+')')},zeroline:false},
    yaxis:{title:{text:'Density'},zeroline:false},
    legend:{orientation:'v',x:1.01,y:1,xanchor:'left',font:{size:11}},
    margin:{l:60,r:200,t:50,b:50},
    hovermode:'closest',
    shapes:shapes,
    height:450,
  };
  if(Y_LIM&&isAbs) layout.xaxis.range=Y_LIM;

  var nEl=document.getElementById('n_pts');
  if(nEl) nEl.textContent='Curves: '+traces.length+(freqFlt?' (freq filtered)':'');

  Plotly.react('kde_plot',traces,layout,{responsive:true});
  updateDeltaTable();
  saveState();
}

/* ---- delta summary table ---- */
function updateDeltaTable(){
  _distUpdateBadge('ser');_distUpdateBadge('port');
  var el=document.getElementById('delta_tbl');
  if(!el) return;
  if(!NON_ROOM_TEMPS.length){
    el.innerHTML='<p style="color:#999;font-size:12px">No non-room temperatures.</p>';
    return;
  }
  var selSer=getSelSerials();
  var selPor=getSelPorts();
  var selSpurIdxs=getSelSpurIdxs();
  var selNrIdxs=getSelNonRoomIdxs();
  if(!selNrIdxs.length) selNrIdxs=NON_ROOM_TEMPS.map(function(_,i){return i;});
  var nrTemps=selNrIdxs.map(function(i){return NON_ROOM_TEMPS[i];});
  var fr=getFreqRange();
  var freqFlt=(typeof DIST_FREQ_MIN!=='undefined')&&
    (fr.lo>DIST_FREQ_MIN+0.09||fr.hi<DIST_FREQ_MAX-0.09);

  /* Accumulate per-DUT deltas across selected spurs, respecting freq/serial/port filters */
  var dutAcc={};   /* serial -> {port, by_temp:{temp:[delta,...]}} */
  var allAcc={};   /* temp -> [delta,...] for overall stats */

  selSpurIdxs.forEach(function(si){
    selNrIdxs.forEach(function(di){
      var temp=NON_ROOM_TEMPS[di];
      if(!allAcc[temp]) allAcc[temp]=[];
      var raw=RAW_DELTA&&RAW_DELTA[si]&&RAW_DELTA[si][di];
      if(freqFlt&&raw&&raw.s&&raw.s.length){
        /* Freq-filtered path: iterate raw (f,d,s) triples */
        for(var i=0;i<raw.f.length;i++){
          if(raw.f[i]<fr.lo||raw.f[i]>fr.hi) continue;
          var ser=raw.s[i],dv=raw.d[i];
          if(!selSer.has(ser)) continue;
          var port=DUT_PORT_MAP&&DUT_PORT_MAP[ser]||'';
          if(selPor.size&&port&&!selPor.has(port)) continue;
          if(!dutAcc[ser]) dutAcc[ser]={serial:ser,port:port,by_temp:{}};
          if(!dutAcc[ser].by_temp[temp]) dutAcc[ser].by_temp[temp]=[];
          dutAcc[ser].by_temp[temp].push(dv);
          allAcc[temp].push(dv);
        }
      } else {
        /* No freq filter: use pre-computed per-DUT means */
        var entries=DUT_DELTA[si]&&DUT_DELTA[si][di];
        if(!entries) return;
        entries.forEach(function(d){
          if(!d) return;
          if(!selSer.has(d.serial)) return;
          if(selPor.size&&d.port&&!selPor.has(d.port)) return;
          if(!dutAcc[d.serial]) dutAcc[d.serial]={serial:d.serial,port:d.port,by_temp:{}};
          if(!dutAcc[d.serial].by_temp[temp]) dutAcc[d.serial].by_temp[temp]=[];
          dutAcc[d.serial].by_temp[temp].push(d.mean);
          allAcc[temp].push(d.mean);
        });
      }
    });
  });

  var rows=Object.values(dutAcc).sort(function(a,b){
    return a.serial<b.serial?-1:a.serial>b.serial?1:0;
  });

  var freqNote=freqFlt?(' <span style="font-size:11px;color:#888">['+fr.lo.toFixed(1)+'–'+fr.hi.toFixed(1)+' MHz]</span>'):'';
  var html='<table class="stbl"><thead><tr><th>Serial</th><th>Port</th>';
  nrTemps.forEach(function(t){html+='<th>'+t+' mean Δ (dB)'+freqNote+'</th>';});
  html+='</tr></thead><tbody>';

  rows.forEach(function(row){
    html+='<tr><td><b>'+row.serial+'</b></td><td>'+row.port+'</td>';
    nrTemps.forEach(function(t){
      var arr=row.by_temp[t]||[];
      var v=arr.length?arr.reduce(function(a,b){return a+b;},0)/arr.length:null;
      var style=(v!==null&&Math.abs(v)>0.5)?'color:#d62728':'';
      html+='<td style="'+style+'">'+_fd(v,3)+'</td>';
    });
    html+='</tr>';
  });

  if(!rows.length){
    var nc=2+nrTemps.length;
    html+='<tr><td colspan="'+nc+'" style="text-align:center;color:#999;padding:8px">'
         +'No data for current selection</td></tr>';
  }
  html+='</tbody></table>';

  /* Overall stats — computed from the same filtered data, not pre-computed DELTA_STATS */
  function _tblMean(a){return a.length?a.reduce(function(x,y){return x+y;},0)/a.length:null;}
  function _tblMed(a){if(!a.length)return null;var s=a.slice().sort(function(x,y){return x-y;});var m=s.length;return m%2?s[Math.floor(m/2)]:(s[m/2-1]+s[m/2])/2;}
  function _tblStd(a){var mu=_tblMean(a);if(mu===null||a.length<2)return null;return Math.sqrt(a.reduce(function(s,x){return s+(x-mu)*(x-mu);},0)/(a.length-1));}
  var hasSome=nrTemps.some(function(t){return allAcc[t]&&allAcc[t].length;});
  if(hasSome){
    html+='<table class="stbl" style="margin-top:8px"><thead><tr>'
         +'<th>Overall stat (filtered selection)</th>';
    nrTemps.forEach(function(t){html+='<th>'+t+freqNote+'</th>';});
    html+='</tr></thead><tbody>';
    [['n',function(a){return a.length||null;}],['mean',_tblMean],['median',_tblMed],['std (σ)',_tblStd]]
      .forEach(function(pair){
        html+='<tr><td>'+pair[0]+'</td>';
        nrTemps.forEach(function(t){html+='<td>'+_fd(pair[1](allAcc[t]||[]),pair[0]==='n'?0:4)+'</td>';});
        html+='</tr>';
      });
    html+='</tbody></table>';
  }

  el.innerHTML=html;
  saveState();
}

/* ---- reset ---- */
function resetView(){
  document.querySelectorAll('.env_chk').forEach(function(c){c.checked=true;});
  var dm=document.querySelector('input[name="view_mode"][value="delta"]');
  if(dm) dm.checked=true;
  document.querySelectorAll('.dist_spur_chk,.dist_ser_chk,.dist_port_chk')
    .forEach(function(c){c.checked=true;});
  ['spur','ser','port'].forEach(_distUpdateBadge);
  var fl=document.getElementById('dist_freq_lo'),fh=document.getElementById('dist_freq_hi');
  if(fl&&typeof DIST_FREQ_MIN!=='undefined'){
    fl.value=DIST_FREQ_MIN;
    var flt=document.getElementById('dist_freq_lo_txt');if(flt)flt.value=DIST_FREQ_MIN.toFixed(1);
  }
  if(fh&&typeof DIST_FREQ_MAX!=='undefined'){
    fh.value=DIST_FREQ_MAX;
    var fht=document.getElementById('dist_freq_hi_txt');if(fht)fht.value=DIST_FREQ_MAX.toFixed(1);
  }
  update();
}

window.addEventListener('DOMContentLoaded',function(){loadState();update();});
"""

    # -------------------------------------------------------------------------
    # 10.  Assemble HTML
    # -------------------------------------------------------------------------
    html = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f"<style>{css}</style>\n"
        "</head>\n<body>\n"
        '<div class="env-bar">\n'
        '<span style="font-size:12px;font-weight:600;color:#2e7d32;margin-right:8px">'
        "Temperatures:</span>\n"
        + env_chks
        + "</div>\n"
        + '<div class="filter-bar" style="gap:6px;align-items:center">\n'
        + (spur_panel_html + "\n" if spur_panel_html else "")
        + ser_panel_html + "\n"
        + (port_panel_html + "\n" if port_panel_html else "")
        + '<button class="sel-btn" style="margin-left:6px" onclick="resetView()">Reset</button>\n'
        + "</div>\n"
        + '<div class="ctrl-bar">\n'
        '  <span>View:</span>\n'
        '  <label><input type="radio" name="view_mode" value="abs"'
        ' onchange="update()">&nbsp;Absolute</label>\n'
        '  <label><input type="radio" name="view_mode" value="delta"'
        ' checked onchange="update()">&nbsp;ΔTemp</label>\n'
        '  <div class="sep"></div>\n'
        + f'  <label>Freq&nbsp;min:<input type="range" id="dist_freq_lo"'
        f' min="{dist_freq_min:.1f}" max="{dist_freq_max:.1f}" value="{dist_freq_min:.1f}"'
        f' step="0.1" style="width:100px" oninput="syncFreqDist()" onchange="update()">'
        f'<input type="text" id="dist_freq_lo_txt" value="{dist_freq_min:.1f}"'
        f' style="width:55px;font-size:12px;border:1px solid #bbb;border-radius:3px;padding:1px 3px"'
        f' onchange="freqDistTxtChange(\'lo\')" onkeydown="freqDistKeyDown(event,\'lo\')">&nbsp;MHz</label>\n'
        f'  <label>Freq&nbsp;max:<input type="range" id="dist_freq_hi"'
        f' min="{dist_freq_min:.1f}" max="{dist_freq_max:.1f}" value="{dist_freq_max:.1f}"'
        f' step="0.1" style="width:100px" oninput="syncFreqDist()" onchange="update()">'
        f'<input type="text" id="dist_freq_hi_txt" value="{dist_freq_max:.1f}"'
        f' style="width:55px;font-size:12px;border:1px solid #bbb;border-radius:3px;padding:1px 3px"'
        f' onchange="freqDistTxtChange(\'hi\')" onkeydown="freqDistKeyDown(event,\'hi\')">&nbsp;MHz</label>\n'
        '  <div class="sep"></div>\n'
        '  <span id="n_pts"></span>\n'
        "</div>\n"
        '<div id="multimodal_warn">'
        '&#9888; Multiple Spur Types selected in Absolute mode '
        '&#8212; distributions span different spur levels and will appear multimodal. '
        'Select a single Spur Type, or switch to ΔTemp view.'
        '</div>\n'
        '<div id="kde_plot"></div>\n'
        '<div class="panel-section">\n'
        '  <div class="panel-title">'
        'ΔEnv Summary &#8212; mean shift from Room per DUT '
        '(averaged across selected Spur Types)</div>\n'
        '  <div id="delta_tbl"></div>\n'
        "</div>\n"
        f"<script>{_get_plotlyjs()}</script>\n"
        f"<script>\n{constants}\n{dist_js}</script>\n"
        "</body>\n</html>"
    )
    return html


def empirical_cdf(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """
    Empirical CDF, one trace per serial. Spec limits shown as vertical lines.
    """
    df = _load_scatter_csv(csv_path)
    lo_spec, hi_spec = _get_spec(df, cfg)
    y_label = cfg.get("y_label", df["_val_col_name"].iloc[0] if len(df) else "Value")

    fig = go.Figure()

    serials = df["Serial"].dropna().unique()
    for serial in sorted(serials):
        vals = np.sort(pst._clean(df[df["Serial"] == serial]["Value"].values))
        n = len(vals)
        ecdf_y = np.arange(1, n + 1) / n
        fig.add_trace(go.Scatter(
            x=vals, y=ecdf_y, mode="lines",
            name=serial,
            hovertemplate=f"<b>{serial}</b><br>{y_label}: %{{x:.4f}}<br>CDF: %{{y:.3f}}<extra></extra>",
        ))

    if not np.isnan(lo_spec):
        fig.add_vline(x=lo_spec, line=dict(color="red", dash="dash"),
                      annotation_text=f"Lo={lo_spec:g}")
    if not np.isnan(hi_spec):
        fig.add_vline(x=hi_spec, line=dict(color="red", dash="dash"),
                      annotation_text=f"Hi={hi_spec:g}")

    fig.update_xaxes(title_text=y_label)
    fig.update_yaxes(title_text="Cumulative Probability", range=[0, 1])
    _write(fig, output_html, cfg)


def spec_derivation(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """
    Per-frequency-band tolerance interval analysis for spec derivation.
    Shows n, median, P5/P95, TI bounds, and proposed spec vs actual limit per band.

    cfg must include 'freq_bands': [[label, lo_MHz, hi_MHz], ...]
    """
    df = _load_scatter_csv(csv_path)
    lo_spec, hi_spec = _get_spec(df, cfg)
    y_label = cfg.get("y_label", df["_val_col_name"].iloc[0] if len(df) else "Value")
    proportion = cfg.get("proportion", 0.90)
    confidence = cfg.get("confidence", 0.90)

    freq_bands = cfg.get("freq_bands", [])
    if not freq_bands:
        freq_bands = [["All", df["Frequency_MHz"].min(), df["Frequency_MHz"].max()]]

    band_results = []
    for label, flo, fhi in freq_bands:
        band_df = df[(df["Frequency_MHz"] >= flo) & (df["Frequency_MHz"] <= fhi)]
        data = pst._clean(band_df["Value"].values)
        summary = pst.band_summary(data, proportion, confidence)
        summary["label"] = label
        band_results.append(summary)

    labels      = [r["label"]      for r in band_results]
    medians     = [r["median"]     for r in band_results]
    p05s        = [r["p05"]        for r in band_results]
    p95s        = [r["p95"]        for r in band_results]
    ti_los      = [r["ti_lower"]   for r in band_results]
    ti_his      = [r["ti_upper"]   for r in band_results]
    ns          = [r["n"]          for r in band_results]
    warnings_flag = [r["ti_warning"] for r in band_results]

    fig = make_subplots(rows=2, cols=1,
                        subplot_titles=["Band Statistics", "Tolerance Interval vs Spec Limit"],
                        vertical_spacing=0.15)

    fig.add_trace(go.Scatter(
        x=labels, y=medians, mode="markers",
        error_y=dict(type="data", symmetric=False,
                     array=[p95s[i] - medians[i] if not np.isnan(p95s[i]) else 0 for i in range(len(labels))],
                     arrayminus=[medians[i] - p05s[i] if not np.isnan(p05s[i]) else 0 for i in range(len(labels))]),
        marker=dict(size=10, color="steelblue"), name="Median ± P5/P95",
        hovertemplate="<b>%{x}</b><br>Median: %{y:.4f}<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=labels, y=ti_his, mode="markers",
        marker=dict(symbol="triangle-up", size=10, color="darkorange"),
        name=f"TI upper P{100*proportion:.0f}/C{100*confidence:.0f}",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=labels, y=ti_los, mode="markers",
        marker=dict(symbol="triangle-down", size=10, color="darkorange"),
        name=f"TI lower P{100*proportion:.0f}/C{100*confidence:.0f}",
    ), row=1, col=1)

    _spec_lines(fig, lo_spec, hi_spec, row=1, col=1)

    margin_hi = [hi_spec - ti_his[i] if not np.isnan(ti_his[i]) and not np.isnan(hi_spec) else np.nan
                 for i in range(len(labels))]
    bar_colors = ["green" if (not np.isnan(m) and m >= 0) else "red" for m in margin_hi]

    fig.add_trace(go.Bar(
        x=labels,
        y=[max(m, 0) if not np.isnan(m) else 0 for m in margin_hi],
        marker_color=bar_colors,
        name="Upper margin (spec − TI)",
        hovertemplate="<b>%{x}</b><br>Margin: %{y:.4f}<extra></extra>",
    ), row=2, col=1)

    for i, (label, n, warn) in enumerate(zip(labels, ns, warnings_flag)):
        fig.add_annotation(
            x=label, y=0, text=f"n={n}" + (" ⚠" if warn else ""),
            xref="x2", yref="y2", yanchor="bottom", showarrow=False,
            font=dict(size=9, color="red" if warn else "gray"),
        )

    fig.update_yaxes(title_text=y_label, row=1, col=1)
    fig.update_yaxes(title_text="Spec margin (dB)", row=2, col=1)
    fig.update_layout(height=700)
    _write(fig, output_html, cfg)


def de_summary(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """
    Delta Environmental summary: Mean Env. deviation vs frequency, one line per Group.
    Spec limit lines shown.  Log/linear X toggle.
    """
    df = _load_env_csv(csv_path)
    lo_spec, hi_spec = _get_spec(df, cfg)
    y_label = cfg.get("y_label", "Mean Env. deviation (dB)")

    filt = cfg.get("group_col_filter")
    if filt:
        df = df[df["Group"].str.contains(filt, case=False, na=False)]

    groups = df["Group"].dropna().unique()
    fig = go.Figure()

    for grp in sorted(groups):
        sub = df[df["Group"] == grp].sort_values("Frequency_MHz")
        fig.add_trace(go.Scatter(
            x=sub["Frequency_MHz"],
            y=sub["Mean_Env"],
            mode="lines+markers",
            marker=dict(size=4),
            name=grp if len(grp) <= 40 else grp[:37] + "...",
            hovertemplate=(
                f"<b>{grp}</b><br>"
                "Freq: %{x:.4f} MHz<br>"
                f"{y_label}: %{{y:.4f}}<extra></extra>"
            ),
        ))

    _spec_lines(fig, lo_spec, hi_spec)
    fig.update_xaxes(title_text=cfg.get("x_label", "Frequency (MHz)"),
                     type="log" if cfg.get("log_x") else "linear")
    fig.update_yaxes(title_text=y_label, range=cfg.get("y_lim"))
    fig.update_layout(updatemenus=[_log_x_button()])
    _write(fig, output_html, cfg)


def de_heatmap(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """
    Delta Environmental heatmap: Group × Frequency, coloured by Mean Env. deviation.
    """
    df = _load_env_csv(csv_path)

    filt = cfg.get("group_col_filter")
    if filt:
        df = df[df["Group"].str.contains(filt, case=False, na=False)]

    pivot = df.pivot_table(index="Group", columns="Frequency_MHz",
                           values="Mean_Env", aggfunc="mean")

    row_labels = [r[:40] if len(r) > 40 else r for r in pivot.index]
    col_labels  = [f"{f:.1f}" for f in pivot.columns]

    z = pivot.values
    lo_spec, hi_spec = _get_spec(df, cfg)
    zmax = max(abs(np.nanmax(z)), abs(np.nanmin(z)), 0.1)
    if not np.isnan(hi_spec):
        zmax = max(zmax, abs(hi_spec))

    text = [[f"{z[i, j]:.3f}" if not np.isnan(z[i, j]) else ""
             for j in range(z.shape[1])]
            for i in range(z.shape[0])]

    fig = go.Figure(go.Heatmap(
        z=z, x=col_labels, y=row_labels,
        text=text, texttemplate="%{text}",
        colorscale="RdBu_r",
        zmid=0, zmin=-zmax, zmax=zmax,
        colorbar=dict(title="Mean Env. (dB)"),
        hovertemplate="Group: %{y}<br>Freq: %{x} MHz<br>Mean Env.: %{z:.4f}<extra></extra>",
    ))

    fig.update_xaxes(title_text=cfg.get("x_label", "Frequency (MHz)"))
    fig.update_yaxes(title_text="Group")
    fig.update_layout(height=max(400, 40 * len(row_labels) + 120))
    _write(fig, output_html, cfg)


# ---------------------------------------------------------------------------
# Statistical tolerance-interval analysis helpers
# ---------------------------------------------------------------------------

def _k_one_sided(n: int, P: float, C: float) -> float:
    """One-sided normal tolerance interval k-factor via noncentral-t distribution."""
    try:
        from scipy.stats import nct, norm
        import math
        k = nct.ppf(C, df=n - 1, nc=norm.ppf(P) * math.sqrt(n)) / math.sqrt(n)
        return float(k)
    except ImportError:
        import math
        from scipy.stats import norm  # type: ignore
        zp = norm.ppf(P)
        zc = norm.ppf(C)
        return max(zp + zc * math.sqrt(1 / n + 0.5 * zp ** 2 / (n - 1)), 1.0)


def _build_k_table() -> dict:
    """Precompute k-factor table for one-sided normal tolerance intervals."""
    import math
    try:
        from scipy.stats import nct, norm
    except ImportError:
        return {}

    P_vals = [0.80, 0.85, 0.90, 0.95, 0.99, 0.9973, 0.999]
    C_vals = [0.75, 0.80, 0.85, 0.90, 0.95]
    n_vals = list(range(2, 51))

    table: dict = {"P": P_vals, "C": C_vals, "n": n_vals, "k": {}}
    for pi in P_vals:
        for ci in C_vals:
            key = f"{pi}_{ci}"
            ks = []
            for n in n_vals:
                try:
                    k = nct.ppf(ci, df=n - 1, nc=norm.ppf(pi) * math.sqrt(n)) / math.sqrt(n)
                    ks.append(round(float(k), 5))
                except Exception:
                    ks.append(round(max(norm.ppf(pi) + norm.ppf(ci) * math.sqrt(1 / n + 0.5 * norm.ppf(pi) ** 2 / (n - 1)), 1.0), 5))
            table["k"][key] = ks
    return table


def _parse_temp_tag(val: str) -> str:
    """Parse temperature strings: 'Room' -> 'Room', '20.0 Deg C' -> '20°C', etc."""
    if val is None:
        return "Room"
    s = str(val).strip()
    if not s or s.lower() in ("room", "ambient", ""):
        return "Room"
    m = re.match(r'([\d.]+)\s*[Dd]eg\s*[Cc]', s)
    if m:
        t = float(m.group(1))
        return f"{t:.0f}°C" if t == int(t) else f"{t}°C"
    return s


def _load_scatter_for_stats(csv_path: Path) -> pd.DataFrame:
    """
    Load scatter CSV (Type=80) for statistical analysis.
    Like _load_scatter_csv but also extracts Temperature from 'Test Step' column.
    Returns DataFrame with: Frequency_MHz, Value, Group, Station, Temperature,
    Upper_Limit (numeric), Lower_Limit (numeric).
    """
    df_raw = pd.read_csv(csv_path, dtype=str)
    df_raw.columns = df_raw.columns.str.strip()
    col_lower = {c.lower(): c for c in df_raw.columns}

    freq_col    = next((col_lower[k] for k in col_lower if "frequency" in k or "x value" in k), None)
    serial_col  = next((col_lower[k] for k in col_lower
                        if any(kw in k for kw in ("serial num", "serial no", "sn", "unit id", "dut id"))
                        and "station" not in k), None)
    if serial_col is None:
        serial_col = next((col_lower[k] for k in col_lower
                           if k == "serial" or (k.startswith("serial") and "station" not in k)), None)
    station_col = next((col_lower[k] for k in col_lower if "station" in k), None)
    lo_col      = next((col_lower[k] for k in col_lower if "lower limit" in k), None)
    hi_col      = next((col_lower[k] for k in col_lower if "upper limit" in k), None)
    group_col   = next((col_lower[k] for k in col_lower if k == "group"), None)
    step_col    = next((col_lower[k] for k in col_lower if "test step" in k), None)

    skip = {c.lower() for c in [freq_col, serial_col, station_col, lo_col, hi_col, group_col] if c}
    skip |= {"analysis type", "model(s)", "algorithm -> result", "units"}
    val_col = None
    if freq_col:
        cols = list(df_raw.columns)
        fi = cols.index(freq_col)
        for c in cols[fi + 1:]:
            if c.lower() not in skip:
                val_col = c
                break

    # Parse limits: strip "<=" / ">=" prefixes
    def _parse_limit(series):
        def _pl(v):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return np.nan
            s = str(v).strip().lstrip('<=>').strip()
            try:
                f = float(s)
                return np.nan if abs(f) >= INT_SENTINEL else f
            except (ValueError, TypeError):
                return np.nan
        return series.map(_pl)

    out = pd.DataFrame()
    out["Frequency_MHz"] = pd.to_numeric(df_raw[freq_col], errors='coerce') if freq_col else np.nan
    out["Value"]         = df_raw[val_col].map(_sfloat)  if val_col  else np.nan
    out["Serial"]        = df_raw[serial_col].str.strip()  if serial_col  else ""
    out["Station"]       = df_raw[station_col].str.strip() if station_col else ""
    out["Group"]         = df_raw[group_col].str.strip()   if group_col   else ""
    out["Upper_Limit"]   = _parse_limit(df_raw[hi_col]) if hi_col else np.nan
    out["Lower_Limit"]   = _parse_limit(df_raw[lo_col]) if lo_col else np.nan
    out["_val_col_name"] = val_col or "Value"

    # Extract temperature from Test Step column if present
    if step_col:
        def _extract_temp(v):
            if v is None:
                return "Room"
            s = str(v).strip()
            # "20.0 Deg C" — PADB native format
            m = re.search(r'([\d.]+)\s*[Dd]eg\s*[Cc]', s)
            if m:
                return _parse_temp_tag(m.group(0))
            # "20°C" — already-parsed format written by _df_to_scatter_csv
            m2 = re.search(r'([\d.]+)\s*°', s)
            if m2:
                t = float(m2.group(1))
                return f"{t:.0f}°C" if t == int(t) else f"{t}°C"
            if re.search(r'\broom\b|\bambient\b', s, re.IGNORECASE):
                return "Room"
            return "Room"
        out["Temperature"] = df_raw[step_col].map(_extract_temp)
    else:
        out["Temperature"] = "Room"

    out = out.dropna(subset=["Frequency_MHz", "Value"])
    return out


def _parse_group_kv(group_str: str) -> dict:
    """
    Parse Group strings with multi-word keys/values separated by 2+ spaces.
    E.g. "AlcState: TRUE  OA State: 0  SpurType: Close-in 10KHz high"
    → {"AlcState": "TRUE", "OA State": "0", "SpurType": "Close-in 10KHz high"}
    Falls back to single-word key regex for strings without double-space separators.
    """
    s = str(group_str).strip()
    # Try double-space splitting first (preserves multi-word keys and values)
    parts = re.split(r'  +', s)
    result: dict = {}
    for part in parts:
        m = re.match(r'(.+?):\s*(.+?)\s*$', part.strip())
        if m:
            result[m.group(1).strip()] = m.group(2).strip()
    # Fallback: single-word keys if nothing was parsed
    if not result:
        for k, v in re.findall(r'(\w+):\s*(\S+)', s):
            result[k] = v
    return result


def _nonparametric_ti(sorted_vals: list, P: float, C: float):
    """
    Distribution-free (P, C) tolerance interval using symmetric order statistics.

    For bounds [x_(d+1), x_(n-d)], achieved confidence is:
      C(d) = scipy.stats.beta.cdf(1-P, 2*(d+1), n-2*(d+1)+1)
    derived from the distribution of coverage of i.i.d. Uniform order statistics.

    Returns (lo, hi) using the tightest d satisfying confidence, or (None, None)
    when n is too small to achieve the required (P, C).
    """
    try:
        from scipy.stats import beta as beta_dist
    except ImportError:
        return None, None
    n = len(sorted_vals)
    if n < 2:
        return None, None
    best_d = None
    for d in range((n - 2) // 2 + 1):
        a = 2 * (d + 1)
        b = n - 2 * (d + 1) + 1
        if b < 1:
            break
        if float(beta_dist.cdf(1.0 - P, a, b)) >= C:
            best_d = d
        else:
            break
    if best_d is None:
        return None, None
    return round(float(sorted_vals[best_d]), 6), round(float(sorted_vals[n - best_d - 1]), 6)


def _aggregate_stat_data(df: pd.DataFrame, cfg: dict) -> list:
    """
    Aggregate scatter data into per-condition, per-frequency statistics for stat_summary.

    Conditions are test configuration dimensions (e.g. OA State, Mode), excluding serial
    numbers. Each DUT is one data point per (condition × frequency): we first average each
    DUT's repeated measurements, then compute population statistics across DUTs.

    Returns list of condition dicts.
    """
    try:
        from scipy.stats import shapiro
    except ImportError:
        shapiro = None  # type: ignore

    proportion_env = cfg.get("proportion_env", cfg.get("proportion", 0.90))
    confidence_env = cfg.get("confidence_env", cfg.get("confidence", 0.90))

    _serial_val_pat = re.compile(r'^[A-Z]{2,3}\d{5,}$')
    _serial_key_kws = ("serial", "unit id", "dut id", "s/n")
    _port_key_kws   = ("port",)

    # Parse all unique Group strings → kv dicts
    unique_groups = df["Group"].dropna().unique()
    group_kv: dict[str, dict] = {g: _parse_group_kv(g) for g in unique_groups}

    # Collect all keys across all groups
    all_keys: set[str] = set()
    for kv in group_kv.values():
        all_keys.update(kv.keys())

    # Classify keys: serial vs port (pooled) vs condition vs constant
    serial_keys: set[str] = set()
    port_keys: set[str] = set()
    cond_keys: list[str] = []

    for key in sorted(all_keys):
        # Serial key by name heuristic
        if any(kw in key.lower() for kw in _serial_key_kws):
            serial_keys.add(key)
            continue
        # Collect values for this key
        vals = {kv.get(key, "") for kv in group_kv.values() if key in kv}
        # Serial key by value heuristic
        if vals and sum(_serial_val_pat.match(v) is not None for v in vals) / len(vals) > 0.5:
            serial_keys.add(key)
            continue
        # Port key: pool into same population (not a condition grouping dim)
        if any(kw in key.lower() for kw in _port_key_kws):
            port_keys.add(key)
            continue
        # Include as condition only if it varies (cardinality 2-20)
        if 1 < len(vals) <= 50:
            cond_keys.append(key)

    def _port_val(group_str: str) -> str:
        kv = group_kv.get(group_str, {})
        for k in port_keys:
            if k in kv:
                return str(kv[k])
        return ""

    # Build serial ID (includes port so RF1/RF2 are separate data points) and condition label
    def _serial_id(group_str: str) -> str:
        kv = group_kv.get(group_str, {})
        base = None
        for k in serial_keys:
            if k in kv:
                base = kv[k]
                break
        if base is None:
            base = group_str
        pv = _port_val(group_str)
        return f"{base}_{pv}" if pv else base

    def _cond_label(group_str: str) -> str:
        kv = group_kv.get(group_str, {})
        parts = [f"{k}: {kv[k]}" for k in cond_keys if k in kv]
        return "  ".join(parts) if parts else "All"

    df = df.copy()
    if "Group" in df.columns and not df["Group"].isna().all():
        df["_serial_id"] = df["Group"].map(_serial_id).fillna("unknown")
        df["_cond"]      = df["Group"].map(_cond_label).fillna("All")
        df["_port"]      = df["Group"].map(_port_val).fillna("")
    else:
        df["_serial_id"] = "unknown"
        df["_cond"]      = "All"
        df["_port"]      = ""

    # When serial is not embedded in the Group string (e.g. phase noise where Serial Number
    # is a separate TData column), the fallback serial_id is the whole group string — every
    # DUT in a group gets the same id, collapsing n to 1.  Override with the Serial column.
    if not serial_keys and "Serial" in df.columns:
        valid = df["Serial"].str.strip().str.match(r'^[A-Z]{2,3}\d{5,}$')
        if valid.any():
            df["_serial_id"] = df["Serial"].str.strip().where(valid, df["_serial_id"])

    results = []

    for cond, cdf in df.groupby("_cond", sort=True):
        temps_present = sorted(cdf["Temperature"].dropna().unique().tolist())
        room_df = cdf[cdf["Temperature"] == "Room"]
        all_freqs = sorted(cdf["Frequency_MHz"].dropna().unique())

        # Per-DUT means at Room for each frequency (n = DUT count, not measurement count)
        room_dut = (room_df.groupby(["_serial_id", "Frequency_MHz"])
                    .agg(Value=("Value", "mean"), _port=("_port", "first"))
                    .reset_index())
        # Per-DUT means at each temp for DEnv
        temp_dut: dict[str, pd.DataFrame] = {}
        for temp_name in temps_present:
            if temp_name == "Room":
                continue
            tdf = cdf[cdf["Temperature"] == temp_name]
            temp_dut[temp_name] = (tdf.groupby(["_serial_id", "Frequency_MHz"])["Value"]
                                   .mean().reset_index())

        # Spec limits from data
        spec_by_freq: dict[float, tuple] = {}
        for freq in all_freqs:
            fd = cdf[cdf["Frequency_MHz"] == freq]
            up_vals = fd["Upper_Limit"].dropna()
            lo_vals = fd["Lower_Limit"].dropna()
            su = float(up_vals.iloc[0]) if len(up_vals) else None
            sl = float(lo_vals.iloc[0]) if len(lo_vals) else None
            cfg_spec = cfg.get("spec_limits")
            if cfg_spec and len(cfg_spec) == 2:
                if cfg_spec[0] is not None:
                    sl = float(cfg_spec[0])
                if cfg_spec[1] is not None:
                    su = float(cfg_spec[1])
            spec_by_freq[freq] = (sl, su)

        freq_stats = []
        for freq in all_freqs:
            _fdf = room_dut[room_dut["Frequency_MHz"] == freq].dropna(subset=["Value"])
            dut_vals  = _fdf["Value"].values
            dut_sers  = _fdf["_serial_id"].values if "_serial_id" in _fdf.columns else ["unknown"] * len(_fdf)
            dut_ports = _fdf["_port"].values if "_port" in _fdf.columns else [""] * len(_fdf)
            n = len(dut_vals)
            if n == 0:
                continue

            mean_v = float(np.mean(dut_vals))
            s_v    = float(np.std(dut_vals, ddof=1)) if n > 1 else 0.0
            q1     = float(np.percentile(dut_vals, 25))
            q2     = float(np.median(dut_vals))
            q3     = float(np.percentile(dut_vals, 75))
            iqr    = q3 - q1
            lo_w   = float(max(np.min(dut_vals), q1 - 1.5 * iqr))
            hi_w   = float(min(np.max(dut_vals), q3 + 1.5 * iqr))

            if shapiro is not None and n >= 3:
                try:
                    W, p_val = shapiro(dut_vals)
                    W, p_val = float(W), float(p_val)
                    norm_label = ("Normal" if p_val > 0.10 else
                                  "Marginal" if p_val > 0.05 else "Non-normal")
                except Exception:
                    W, p_val, norm_label = 1.0, 1.0, "error"
            else:
                W, p_val, norm_label = 1.0, 1.0, "n<3"

            # DEnv: per-DUT paired deltas from temp data — store per-temp AND combined
            denv_up = 0.0
            denv_lo = 0.0
            denv_by_temp: dict = {}
            for temp_name, tdt in temp_dut.items():
                t_dut_vals = tdt[tdt["Frequency_MHz"] == freq]["Value"].dropna().values
                if len(t_dut_vals) == 0:
                    continue
                deltas = t_dut_vals - dut_vals if len(t_dut_vals) == n else t_dut_vals - mean_v
                n_env = len(deltas)
                dm = float(np.mean(deltas))
                ds = float(np.std(deltas, ddof=1)) if n_env > 1 else 0.0
                k_env = _k_one_sided(max(n_env, 2), proportion_env, confidence_env)
                this_up = max(0.0, dm + k_env * ds)
                this_lo = max(0.0, -(dm - k_env * ds))
                denv_by_temp[temp_name] = {"up": round(this_up, 6), "lo": round(this_lo, 6)}
                denv_up = max(denv_up, this_up)
                denv_lo = max(denv_lo, this_lo)

            outlier_det = sorted(
                [{"s": str(ser), "p": str(prt), "v": round(float(v), 6)}
                 for v, ser, prt in zip(dut_vals, dut_sers, dut_ports) if v < lo_w or v > hi_w],
                key=lambda x: x["v"])
            outliers = [d["v"] for d in outlier_det]
            dut_detail = [{"s": str(ser), "p": str(prt), "v": round(float(v), 6)}
                          for ser, prt, v in zip(dut_sers, dut_ports, dut_vals)]
            spec_lo, spec_up = spec_by_freq[freq]
            _P = cfg.get("proportion", 0.90)
            _C = cfg.get("confidence", 0.90)
            np_lo, np_up = _nonparametric_ti(sorted(float(v) for v in dut_vals), _P, _C)
            freq_stats.append({
                "freq":     float(freq),
                "n":        int(n),
                "mean":     round(mean_v, 6),
                "s":        round(s_v, 6),
                "q1":       round(q1, 6),
                "q2":       round(q2, 6),
                "q3":       round(q3, 6),
                "lo_w":     round(lo_w, 6),
                "hi_w":     round(hi_w, 6),
                "outliers": outliers,
                "outlier_detail": outlier_det,
                "dut_vals": dut_detail,
                "W":        round(W, 5),
                "p":        round(p_val, 5),
                "norm":     norm_label,
                "denv_up":      round(denv_up, 6),
                "denv_lo":      round(denv_lo, 6),
                "denv_by_temp": denv_by_temp,
                "spec_up":      spec_up,
                "spec_lo":      spec_lo,
                "np_ti_lo":     np_lo,
                "np_ti_up":     np_up,
            })

        results.append({
            "condition":    str(cond),
            "temps_present": temps_present,
            "freq_stats":   freq_stats,
        })

    return results


# ---------------------------------------------------------------------------
# JS for stat_summary interactive page (raw string — no Python escaping)
# ---------------------------------------------------------------------------
_STAT_SUMMARY_JS = r"""
/* Globals injected before this block:
   STAT_DATA, KT, TITLE, Y_LABEL, Y_LIM, LOG_X, FREQ_MIN, FREQ_MAX,
   LO_SPEC, HI_SPEC, DEFAULT_P, DEFAULT_C, DEFAULT_MU, DEFAULT_GB, COND_DIMS
   Each COND_DIMS entry: {col, col_id, label, vals}
*/

/* ---- filter panel helpers ---- */
function togglePanel(col){
  var p=document.getElementById('panel_'+col);
  var open=p.classList.contains('open');
  document.querySelectorAll('.filter-panel').forEach(function(x){x.classList.remove('open');});
  if(!open)p.classList.add('open');
}
document.addEventListener('click',function(e){
  if(!e.target.closest('.filter-wrap'))
    document.querySelectorAll('.filter-panel').forEach(function(p){p.classList.remove('open');});
});
function toggleAll(col){
  var a=document.getElementById('all_'+col);
  document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){c.checked=a.checked;});
  updateBadge(col);update();
}
function chkChanged(col){
  var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
  var a=document.getElementById('all_'+col);
  var n=chks.filter(function(c){return c.checked;}).length;
  a.checked=(n===chks.length);a.indeterminate=(n>0&&n<chks.length);
  updateBadge(col);update();
}
function updateBadge(col){
  var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
  var n=chks.filter(function(c){return c.checked;}).length;
  var b=document.getElementById('badge_'+col);
  if(b){if(n<chks.length){b.textContent=n+'/'+chks.length;b.classList.add('active');}
        else b.classList.remove('active');}
}
function getSelected(col){
  return Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]:checked'))
    .map(function(c){return c.value;});
}

/* ---- k-factor bilinear interpolation ---- */
function kLookup(n,P,C){
  var Pv=KT.P,Cv=KT.C,nv=KT.n;
  var Pi=Pv.reduce(function(b,v,i){return Math.abs(v-P)<Math.abs(Pv[b]-P)?i:b;},0);
  var Ci=Cv.reduce(function(b,v,i){return Math.abs(v-C)<Math.abs(Cv[b]-C)?i:b;},0);
  var Pi2=Math.min(Pi+1,Pv.length-1),Ci2=Math.min(Ci+1,Cv.length-1);
  var tP=Pv[Pi]===Pv[Pi2]?0:(P-Pv[Pi])/(Pv[Pi2]-Pv[Pi]);
  var tC=Cv[Ci]===Cv[Ci2]?0:(C-Cv[Ci])/(Cv[Ci2]-Cv[Ci]);
  function lookAt(pi,ci){
    var key=KT.P[pi]+'_'+KT.C[ci],arr=KT.k[key];
    if(!arr)return 2.0;
    var nC=Math.max(nv[0],Math.min(nv[nv.length-1],n));
    var idx=nC-nv[0],lo=Math.max(0,Math.min(Math.floor(idx),arr.length-2));
    var t=idx-lo;
    return arr[lo]*(1-t)+arr[lo+1]*t;
  }
  var k00=lookAt(Pi,Ci),k10=lookAt(Pi2,Ci),k01=lookAt(Pi,Ci2),k11=lookAt(Pi2,Ci2);
  return k00*(1-tP)*(1-tC)+k10*tP*(1-tC)+k01*(1-tP)*tC+k11*tP*tC;
}

/* ---- read controls ---- */
function getParams(){
  function numOrNull(id){
    var el=document.getElementById(id);
    if(!el||el.value==='') return null;
    var v=parseFloat(el.value);
    return isNaN(v)?null:v;
  }
  return {
    P:   parseFloat(document.getElementById('stat_P').value)||0.90,
    C:   parseFloat(document.getElementById('stat_C').value)||0.90,
    n_override: parseInt(document.getElementById('stat_n').value)||0,
    mu:  parseFloat(document.getElementById('stat_mu').value)||0,
    denv_up_override: parseFloat(document.getElementById('stat_denv_up').value)||0,
    denv_lo_override: parseFloat(document.getElementById('stat_denv_lo').value)||0,
    gb:  parseFloat(document.getElementById('stat_gb').value)||0,
    drift: parseFloat(document.getElementById('stat_drift').value)||0,
    spec_lo_override: numOrNull('stat_spec_lo'),
    spec_hi_override: numOrNull('stat_spec_hi'),
    tll_hi_override: numOrNull('stat_tll_hi'),
    tll_lo_override: numOrNull('stat_tll_lo')
  };
}

/* ---- active conditions (filtered by checkboxes) ---- */
function getActiveConditions(){
  if(!COND_DIMS||!COND_DIMS.length) return STAT_DATA;
  return STAT_DATA.filter(function(cd){
    return COND_DIMS.every(function(dim){
      var all=document.querySelectorAll('.fchk[data-col="cond_'+dim.col_id+'"]');
      if(!all.length) return true;
      var allowed=Array.from(all).filter(function(c){return c.checked;}).map(function(c){return c.value;});
      if(!allowed.length) return false;
      var safe=dim.col.replace(/[-\/\\^$*+?.()|[\]{}]/g,'\\$&');
      var m=cd.condition.match(new RegExp(safe+':\\s*(.+?)(?=\\s{2,}|$)'));
      return m&&allowed.indexOf(m[1].trim())>=0;
    });
  });
}

/* ---- environmental step selection ---- */
function getSelectedTemps(){
  return Array.from(document.querySelectorAll('.env_chk:checked'))
    .map(function(c){return c.value;})
    .filter(function(v){return v!=='Room';});
}

/* ---- per-freq TI/TLL result ---- */
function computeFreqResult(fs,params){
  var n_use=params.n_override>0?params.n_override:fs.n;
  var k=kLookup(n_use,params.P,params.C);
  var useNp=isNpTI()&&(fs.norm==='Non-normal'||fs.norm==='Marginal')
             &&fs.np_ti_lo!=null&&fs.np_ti_up!=null;
  var ti_up,ti_lo;
  if(useNp){ti_lo=fs.np_ti_lo;ti_up=fs.np_ti_up;}
  else{ti_up=fs.mean+k*fs.s;ti_lo=fs.mean-k*fs.s;}
  // DEnv: manual override > per-temp selection > full auto
  var dev_up,dev_lo;
  if(params.denv_up_override>0){
    dev_up=params.denv_up_override;
  } else {
    var selTemps=getSelectedTemps();
    var dbt=fs.denv_by_temp||{};
    dev_up=0;
    selTemps.forEach(function(t){if(dbt[t]) dev_up=Math.max(dev_up,dbt[t].up);});
  }
  if(params.denv_lo_override>0){
    dev_lo=params.denv_lo_override;
  } else {
    var selTemps2=getSelectedTemps();
    var dbt2=fs.denv_by_temp||{};
    dev_lo=0;
    selTemps2.forEach(function(t){if(dbt2[t]) dev_lo=Math.max(dev_lo,dbt2[t].lo);});
  }
  var spec_up=(fs.spec_up!=null)?fs.spec_up:params.spec_hi_override;
  // spec_lo stored/entered as either signed (−0.15) or magnitude (0.15) — always apply as lower limit
  var spec_lo_raw=(fs.spec_lo!=null)?fs.spec_lo:params.spec_lo_override;
  var spec_lo_mag=(spec_lo_raw!=null)?Math.abs(spec_lo_raw):null;
  var g_up=params.mu+dev_up+params.gb+params.drift;
  var g_lo=params.mu+dev_lo+params.gb+params.drift;
  var tll_up=(params.tll_hi_override!==null)?params.tll_hi_override:((spec_up!=null)?spec_up-g_up:null);
  var tll_lo=(params.tll_lo_override!==null)?params.tll_lo_override:((spec_lo_mag!=null)?-spec_lo_mag+g_lo:null);
  var ssu_up=ti_up+g_up;   // spec supportable from data (upper): TI_up + full budget
  var ssu_lo=ti_lo-g_lo;   // spec supportable (lower): TI_lo - full budget
  var margin_up=(spec_up!==null)?spec_up-ssu_up:null;   // positive = passing with margin
  var margin_lo=(spec_lo_mag!==null)?ssu_lo-(-spec_lo_mag):null;
  return {n_use:n_use,k:k,ti_up:ti_up,ti_lo:ti_lo,np_active:useNp,
          tll_up:tll_up,tll_lo:tll_lo,
          ssu_up:ssu_up,ssu_lo:ssu_lo,
          margin_up:margin_up,margin_lo:margin_lo,
          denv_up:dev_up,denv_lo:dev_lo,
          spec_lo:-spec_lo_mag,spec_up:spec_up,
          pass_up:tll_up===null||ti_up<=tll_up,
          pass_lo:tll_lo===null||ti_lo>=tll_lo,
          ssu_pass_up:spec_up===null||ssu_up<=spec_up,
          ssu_pass_lo:spec_lo_mag===null||ssu_lo>=-spec_lo_mag};
}

/* ---- CSV export ---- */
function saveCSV(withExcluded){
  /* Temporarily bypass global filter when withExcluded=true */
  var savedGf=_gfExcluded;
  if(withExcluded) _gfExcluded=null;
  var conds=getActiveConditions();
  var fLo=parseFloat(document.getElementById('freq_lo').value);
  var fHi=parseFloat(document.getElementById('freq_hi').value);
  conds=conds.map(function(cd){
    return Object.assign({},cd,{
      freq_stats:(cd.freq_stats||[]).filter(function(fs){return fs.freq>=fLo&&fs.freq<=fHi;})
    });
  });
  /* Apply serial + GF filtering consistent with the current plot state */
  var allSers=getAllSerials();var selSers=getSelectedSerials();
  var serFlt=allSers.length>1&&selSers.length<allSers.length;
  var hasGf=_gfExcluded&&_gfExcluded.size>0;
  if(serFlt||hasGf){
    var activeSers=serFlt?selSers:allSers;
    conds=conds.map(function(cd){
      var nfs=[];
      (cd.freq_stats||[]).forEach(function(fs){
        var r=recomputeFreqStat(fs,activeSers,cd.condition,fs.freq);if(r) nfs.push(r);
      });
      return Object.assign({},cd,{freq_stats:nfs});
    });
  }
  if(withExcluded) _gfExcluded=savedGf;
  var params=getParams();
  var flt=getDataFilter();
  conds=applyDataFilter(conds,params,flt);
  var hdrs=['Condition','Freq_MHz','n','Mean','Std','k',
            'TI_lower','TI_upper','TLL_lower','TLL_upper',
            'Pass','Method','Normality','W','p',
            'DEnv_up','DEnv_lo','Spec_lo','Spec_hi','Outliers'];
  var rows=[hdrs.join(',')];
  function esc(v){var s=String(v==null?'':v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;}
  conds.forEach(function(cd){
    (cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;}).forEach(function(fs){
      var r=computeFreqResult(fs,params);
      rows.push([
        esc(cd.condition),
        fs.freq.toFixed(4),
        r.n_use,
        fs.mean.toFixed(6),
        fs.s.toFixed(6),
        r.k.toFixed(5),
        r.ti_lo.toFixed(6),
        r.ti_up.toFixed(6),
        r.tll_lo!=null?r.tll_lo.toFixed(6):'',
        r.tll_up!=null?r.tll_up.toFixed(6):'',
        (r.pass_up&&r.pass_lo)?'PASS':'FAIL',
        r.np_active?'NP-order-stat':'parametric-k',
        fs.norm,fs.W,fs.p,
        r.denv_up.toFixed(6),r.denv_lo.toFixed(6),
        fs.spec_lo!=null?fs.spec_lo:'',
        fs.spec_up!=null?fs.spec_up:'',
        esc((fs.outliers||[]).join('; '))
      ].join(','));
    });
  });
  /* Metadata block */
  var ts=new Date().toISOString().replace('T',' ').replace(/\.\d+Z$/,' UTC');
  var gfSers=[];
  if(savedGf&&savedGf.size>0) savedGf.forEach(function(k){var s=k.split('||')[0];if(gfSers.indexOf(s)<0) gfSers.push(s);});
  var activeConds=conds.map(function(cd){return cd.condition;});
  var allConds=STAT_DATA.map(function(cd){return cd.condition;}).filter(function(v,i,a){return a.indexOf(v)===i;});
  var excConds=allConds.filter(function(c){return activeConds.indexOf(c)<0;});
  var meta=['# PADB Export','# Plot: '+TITLE,'# Generated: '+ts,
    '# Export: '+(withExcluded?'Filtered + excluded (GF bypassed; n reflects serial filter only)':'Filtered data'),
    '# Freq range: '+fLo.toFixed(2)+' - '+fHi.toFixed(2)+' MHz',
    '# Active conditions ('+activeConds.length+'): '+activeConds.join(', '),
    '# Excluded conditions ('+excConds.length+'): '+(excConds.length?excConds.join(', '):'None'),
    '# Active serials: '+(serFlt?selSers.join(', '):'All ('+allSers.length+')'),
    '# GF excluded DUTs ('+gfSers.length+'): '+(gfSers.length?gfSers.join(', '):'None'),
    '#'
  ].join('\r\n');
  var blob=new Blob([meta+'\r\n'+rows.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  var suffix=withExcluded?'_with_excl':'_filtered';
  a.href=url;a.download=(TITLE+suffix).replace(/[^a-zA-Z0-9_\-]/g,'_')+'.csv';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

var PALETTE=['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd',
             '#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf'];
function normColor(lbl){
  return lbl==='Normal'?'green':lbl==='Marginal'?'orange':'red';
}

/* ---- build Plotly traces ---- */
function buildTraces(conds,params){
  var traces=[];
  var all_freqs=[];

  conds.forEach(function(cd,ci){
    var color=PALETTE[ci%PALETTE.length];
    var sorted=(cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;});
    // Always push 6 traces per condition to keep index stable for Plotly.react
    if(!sorted.length){
      for(var i=0;i<6;i++)
        traces.push({type:'scatter',x:[],y:[],mode:'markers',showlegend:false,hoverinfo:'skip'});
      return;
    }
    var freqs=[],ti_ups=[],ti_los=[],means=[],nmCols=[],markerSyms=[],hover=[];
    var fail_x=[],fail_y=[];
    var any_fail=false;

    sorted.forEach(function(fs){
      var r=computeFreqResult(fs,params);
      freqs.push(fs.freq);
      ti_ups.push(r.ti_up);
      ti_los.push(r.ti_lo);
      means.push(fs.mean);
      nmCols.push(normColor(fs.norm));
      markerSyms.push(r.np_active?'diamond':'circle');
      var outStr='';
      if(fs.outlier_detail&&fs.outlier_detail.length){
        outStr='Outliers: '+fs.outlier_detail.map(function(d){return d.v.toFixed(4)+(d.s&&d.s!=='unknown'?' ('+d.s+')':'');}).join(', ');
      } else if(fs.outliers&&fs.outliers.length){
        outStr='Outliers: '+fs.outliers.map(function(v){return v.toFixed(4);}).join(', ');
      }
      var tiLabel=r.np_active?'TI (NP): ':'TI: ';
      hover.push(
        'Freq: '+fs.freq.toFixed(4)+' MHz<br>'+
        'Mean: '+fs.mean.toFixed(4)+'  Std: '+fs.s.toFixed(4)+'<br>'+
        'n='+r.n_use+(r.np_active?'  (NP TI)':'  k='+r.k.toFixed(3))+'<br>'+
        tiLabel+'['+r.ti_lo.toFixed(4)+', '+r.ti_up.toFixed(4)+']<br>'+
        (r.tll_lo!==null?'TLL: ['+r.tll_lo.toFixed(4)+', '+r.tll_up.toFixed(4)+']<br>':'')+
        'Norm: '+fs.norm+' (W='+fs.W+', p='+fs.p+')<br>'+outStr
      );
      var pass=r.pass_up&&r.pass_lo;
      if(!r.pass_up){fail_x.push(fs.freq);fail_y.push(r.ti_up);}
      if(!r.pass_lo){fail_x.push(fs.freq);fail_y.push(r.ti_lo);}
      if(!pass) any_fail=true;
      all_freqs.push(fs.freq);
    });

    var fillColor=any_fail?'rgba(220,50,50,0.12)':'rgba(0,160,0,0.12)';

    // Trace 1: TI band polygon — fill:'toself' re-renders correctly with Plotly.react when
    //   only y-values change. fill:'tonexty' only updates line positions, not the fill area.
    var band_x=freqs.concat(freqs.slice().reverse());
    var band_y=ti_ups.concat(ti_los.slice().reverse());
    traces.push({
      type:'scatter',x:band_x,y:band_y,mode:'lines',
      fill:'toself',fillcolor:fillColor,
      line:{width:0},
      name:cd.condition+' band',legendgroup:cd.condition,showlegend:false,
      hoverinfo:'skip'
    });
    // Trace 2: dashed TI bound lines (upper then lower, NaN-separated)
    var ti_line_x=freqs.concat([null]).concat(freqs.slice());
    var ti_line_y=ti_ups.concat([null]).concat(ti_los.slice());
    traces.push({
      type:'scatter',x:ti_line_x,y:ti_line_y,mode:'lines',
      line:{color:color,dash:'dash',width:1},
      name:cd.condition+' TI↑↓',legendgroup:cd.condition,showlegend:false,
      hoverinfo:'skip'
    });
    // Trace 3: Mean line + markers colored by normality; diamond = NP TI active
    traces.push({
      type:'scatter',x:freqs,y:means,mode:'markers+lines',
      marker:{size:7,color:nmCols,symbol:markerSyms,line:{color:'white',width:1}},
      line:{color:color,width:1.5},
      name:cd.condition,legendgroup:cd.condition,
      text:hover,
      hovertemplate:'<b>'+cd.condition+'</b><br>%{text}<extra></extra>'
    });
    // Trace 4: Fail markers (empty when all pass — keeps index stable)
    traces.push({
      type:'scatter',x:fail_x,y:fail_y,mode:'markers',
      marker:{symbol:'x-thin',size:14,color:'red',line:{color:'darkred',width:3}},
      name:cd.condition+' FAIL',legendgroup:cd.condition,
      showlegend:fail_x.length>0,hoverinfo:'skip'
    });
    // Trace 5: Spec supportable = TI_up + MU + DEnv + GB (bottom-up view)
    // Shows what spec this population can commit to at each frequency.
    // Green markers where ssu <= spec (passing with margin); red where ssu > spec.
    var ssu_x=[],ssu_y=[],ssu_cols=[],ssu_hover=[];
    var any_ssu_fail=false;
    sorted.forEach(function(fs){
      var r=computeFreqResult(fs,params);
      ssu_x.push(fs.freq);
      ssu_y.push(r.ssu_up);
      var col=r.spec_up!==null?(r.ssu_pass_up?'#2ca02c':'#d62728'):'#9467bd';
      if(!r.ssu_pass_up) any_ssu_fail=true;
      ssu_cols.push(col);
      var budgetStr='MU='+params.mu.toFixed(2)+' ΔEnv='+r.denv_up.toFixed(2)+' GB='+params.gb.toFixed(2);
      var mStr=r.margin_up!==null?
        'Margin: '+(r.margin_up>=0?'+':'')+r.margin_up.toFixed(3)+' dB '+(r.margin_up>=0?'✔':'✘'):
        'No spec defined';
      ssu_hover.push('Spec supportable: '+r.ssu_up.toFixed(3)+'<br>'+budgetStr+'<br>'+mStr);
    });
    traces.push({
      type:'scatter',x:ssu_x,y:ssu_y,mode:'lines+markers',
      line:{color:color,width:1,dash:'dot'},
      marker:{size:6,color:ssu_cols,line:{color:'white',width:0.5}},
      name:cd.condition+' ▲Spec',legendgroup:cd.condition,
      showlegend:any_ssu_fail,
      text:ssu_hover,
      hovertemplate:'<b>'+cd.condition+'</b><br>%{text}<extra></extra>'
    });
    // Trace 6: Individual dut_vals scatter points — toggle via "Show points"
    var showPts=document.getElementById('show_pts_chk');
    showPts=showPts?showPts.checked:false;
    var selSers2=getAllSerials().length>1?getSelectedSerials():[];
    var useSerFlt=selSers2.length>0&&selSers2.length<getAllSerials().length;
    var selPorts2=getSsPorts().length>1?getSelSsPorts():[];
    var usePortFlt=selPorts2.length>0&&selPorts2.length<getSsPorts().length;
    var hasGfForPts=_gfExcluded&&_gfExcluded.size>0;
    var pt_x=[],pt_y=[],pt_cols=[],pt_txt=[];
    if(showPts){
      sorted.forEach(function(fs){
        (fs.dut_vals||[]).forEach(function(dv){
          var serExcl=useSerFlt&&selSers2.indexOf(dv.s)<0;
          var portExcl=usePortFlt&&selPorts2.indexOf(dv.p||'')<0;
          var gfExcl=hasGfForPts&&_isStatGfExcl(dv.s,cd.condition,fs.freq);
          pt_x.push(fs.freq);
          pt_y.push(dv.v);
          pt_cols.push(gfExcl?'rgba(220,80,40,0.5)':(serExcl||portExcl)?'rgba(160,160,160,0.4)':color);
          pt_txt.push((dv.p?'['+dv.p+'] ':'')+dv.s);
        });
      });
    }
    traces.push(showPts&&pt_x.length?{
      type:'scatter',x:pt_x,y:pt_y,mode:'markers',
      marker:{size:5,color:pt_cols,opacity:0.7,line:{color:'white',width:0.5}},
      name:cd.condition+' pts',legendgroup:cd.condition,showlegend:false,
      text:pt_txt,
      hovertemplate:'<b>%{text}</b>: %{y:.4f}<extra></extra>'
    }:{type:'scatter',x:[],y:[],mode:'markers',showlegend:false,hoverinfo:'skip'});
  });

  // TLL reference lines (spec lines are now layout shapes — see buildLayout)
  if(!all_freqs.length) return traces;

  // TLL: per-frequency worst-case across all visible conditions
  // Collecting worst-case (min tll_up, max tll_lo) per frequency avoids a single
  // outlier condition (e.g. n=2 with huge DEnv) distorting the entire horizontal line.
  var freqSet={};
  conds.forEach(function(cd){
    (cd.freq_stats||[]).forEach(function(fs){freqSet[fs.freq]=true;});
  });
  var sortedTllFreqs=Object.keys(freqSet).map(Number).sort(function(a,b){return a-b;});
  var tllUpByFreq={},tllLoByFreq={};
  conds.forEach(function(cd){
    (cd.freq_stats||[]).forEach(function(fs){
      var r=computeFreqResult(fs,params);
      if(r.tll_up!==null)
        tllUpByFreq[fs.freq]=tllUpByFreq[fs.freq]===undefined?r.tll_up:Math.min(tllUpByFreq[fs.freq],r.tll_up);
      if(r.tll_lo!==null)
        tllLoByFreq[fs.freq]=tllLoByFreq[fs.freq]===undefined?r.tll_lo:Math.max(tllLoByFreq[fs.freq],r.tll_lo);
    });
  });
  var tllFx=[],tllFyUp=[],tllFyLo=[];
  sortedTllFreqs.forEach(function(f){
    tllFx.push(f);
    tllFyUp.push(tllUpByFreq[f]!==undefined?tllUpByFreq[f]:null);
    tllFyLo.push(tllLoByFreq[f]!==undefined?tllLoByFreq[f]:null);
  });
  if(tllFyUp.some(function(v){return v!==null;}))
    traces.push({type:'scatter',x:tllFx,y:tllFyUp,mode:'lines',connectgaps:false,
      line:{color:'darkorange',dash:'dashdot',width:2},
      name:'TLL↑',hovertemplate:'TLL↑: %{y:.4f}<extra></extra>'});
  if(tllFyLo.some(function(v){return v!==null;}))
    traces.push({type:'scatter',x:tllFx,y:tllFyLo,mode:'lines',connectgaps:false,
      line:{color:'darkorange',dash:'dashdot',width:2},
      name:'TLL↓',hovertemplate:'TLL↓: %{y:.4f}<extra></extra>'});
  return traces;
}

function isLogX(){return document.getElementById('log_x_chk').checked;}
function isNpTI(){var c=document.getElementById('np_ti_chk');return c?c.checked:false;}
/* ---- serial filter ---- */
function getAllSerials(){return Array.from(document.querySelectorAll('.ser_chk')).map(function(c){return c.value;});}
function getSelectedSerials(){return Array.from(document.querySelectorAll('.ser_chk:checked')).map(function(c){return c.value;});}
function serChkChanged(){
  var all=document.querySelectorAll('.ser_chk');
  var chk=Array.from(all).filter(function(c){return c.checked;}).length;
  var allEl=document.getElementById('all_ser_panel');
  if(allEl){allEl.checked=chk===all.length;allEl.indeterminate=chk>0&&chk<all.length;}
  var b=document.getElementById('badge_ser_panel');
  if(b){b.textContent=chk<all.length?chk+'/'+all.length:'';b.classList.toggle('active',chk<all.length);}
  update();
}
function toggleAllSer(){
  var allEl=document.getElementById('all_ser_panel');
  document.querySelectorAll('.ser_chk').forEach(function(c){c.checked=allEl?allEl.checked:true;});
  serChkChanged();
}
/* ---- port filter (display-only: dots dimmed, TI bounds unchanged) ---- */
function getSsPorts(){return Array.from(document.querySelectorAll('.ss_port_chk')).map(function(c){return c.value;});}
function getSelSsPorts(){return Array.from(document.querySelectorAll('.ss_port_chk:checked')).map(function(c){return c.value;});}
function ssPortChkChanged(){
  var all=document.querySelectorAll('.ss_port_chk');
  var chk=Array.from(all).filter(function(c){return c.checked;}).length;
  var allEl=document.getElementById('all_ss_port_panel');
  if(allEl){allEl.checked=chk===all.length;allEl.indeterminate=chk>0&&chk<all.length;}
  var b=document.getElementById('badge_ss_port_panel');
  if(b){b.textContent=chk<all.length?chk+'/'+all.length:'';b.classList.toggle('active',chk<all.length);}
  update();
}
function toggleAllSsPort(){
  var allEl=document.getElementById('all_ss_port_panel');
  document.querySelectorAll('.ss_port_chk').forEach(function(c){c.checked=allEl?allEl.checked:true;});
  ssPortChkChanged();
}
/* ---- global filter (localStorage) ---- */
var _gfExcluded=null;
var _gfCoarseExcluded=null;
var _statGfFocusMode=false;
var _plotRev=0;
function _loadStatGlobalFilter(){
  try{
    var raw=localStorage.getItem('padb_v2_excluded');
    if(!raw){_gfExcluded=null;_gfCoarseExcluded=null;}
    else{
      var obj=JSON.parse(raw);
      _gfExcluded=new Set(obj.excluded||[]);
      _gfCoarseExcluded=new Set();
      var serKws=['serial','unit id','dut id','s/n'];
      _gfExcluded.forEach(function(k){
        var parts=k.split('||');
        if(parts.length>=2){
          var coarseCond=parts[1].split('|').filter(function(p){
            var lo=p.toLowerCase();
            return !serKws.some(function(kw){return lo.indexOf(kw)===0;});
          }).join('|');
          _gfCoarseExcluded.add(parts[0]+'||'+coarseCond);
        }
      });
    }
  }catch(e){_gfExcluded=null;_gfCoarseExcluded=null;}
  _statGfFocusMode=(localStorage.getItem('padb_v2_gf_mode')||'exclude')==='focus';
  _updateStatGfBadge();
}
function _updateStatGfBadge(){
  var el=document.getElementById('stat_gf_badge');
  var lbl=document.getElementById('stat_gf_label');
  if(!el) return;
  var dutSers=new Set();
  if(_gfExcluded) _gfExcluded.forEach(function(k){dutSers.add(k.split('||')[0]);});
  var n=dutSers.size,pts=_gfExcluded?_gfExcluded.size:0;
  var isFocus=_statGfFocusMode;
  var chk=document.getElementById('stat_gf_chk');
  var active=chk?chk.checked:true;
  if(n>0){
    if(lbl) lbl.style.display='';
    el.textContent=(active?(isFocus?'GF Inspect ON':'GF ON'):'GF OFF')+': '+pts+' pts ('+n+' DUT'+(n!==1?'s':')')+''+(isFocus&&active?' [inspect]':'');
    el.style.background=!active?'#f0f0f0':isFocus?'#e8f0ff':'#ffeaea';
    el.style.color=!active?'#888':isFocus?'#0044aa':'#900';
    el.style.borderColor=!active?'#ccc':isFocus?'#6688cc':'#c88';
  } else {
    if(lbl) lbl.style.display='none';
    el.textContent='';
  }
}
window.addEventListener('storage',function(e){
  if(e.key==='padb_v2_excluded'||e.key==='padb_v2_gf_mode'){_loadStatGlobalFilter();update();}
});
/* Build a condition key without serial parts (matches boxplot key format) */
function _condKeyForStat(cond){
  var serKws=['serial','unit id','dut id','s/n'];
  return (cond||'').split(/  +/).map(function(p){
    return p.replace(/\s*:\s*/,'=').trim();
  }).filter(function(p){
    var lo=p.toLowerCase();
    return p&&!serKws.some(function(kw){return lo.indexOf(kw)===0;});
  }).sort().join('|');
}
function _isStatGfExcl(serial,cond,freq){
  if(!_gfCoarseExcluded||!_gfCoarseExcluded.size) return false;
  /* Coarse match: DUT serial + condition WITHOUT temperature or frequency */
  return _gfCoarseExcluded.has((serial||'unknown')+'||'+_condKeyForStat(cond));
}
function recomputeFreqStat(fs,selSers,cond,freq,applyGf){
  var f=freq!==undefined?freq:(fs.freq||0);
  var c=cond||'';
  var dv=(fs.dut_vals||[]).filter(function(d){
    if(selSers.indexOf(d.s)<0) return false;
    if(applyGf===false) return true;
    var _excl=_isStatGfExcl(d.s,c,f);
    return _statGfFocusMode?_excl:!_excl;
  });
  if(!dv.length) return null;
  var n=dv.length,vals=dv.map(function(d){return d.v;});
  var mean=0;vals.forEach(function(v){mean+=v;});mean/=n;
  var ss=0;vals.forEach(function(v){ss+=(v-mean)*(v-mean);});
  var s=n>1?Math.sqrt(ss/(n-1)):0;
  var sorted=vals.slice().sort(function(a,b){return a-b;});
  function pct(p){var i=(p/100)*(n-1),lo=Math.floor(i);return lo+1<n?sorted[lo]+(sorted[lo+1]-sorted[lo])*(i-lo):sorted[lo];}
  var q1=pct(25),q2=pct(50),q3=pct(75),iqr=q3-q1;
  var loF=q1-1.5*iqr,hiF=q3+1.5*iqr;
  var outDet=dv.filter(function(d){return d.v<loF||d.v>hiF;});
  return Object.assign({},fs,{n:n,mean:mean,s:s,q1:q1,q2:q2,q3:q3,
    lo_w:Math.max(sorted[0],loF),hi_w:Math.min(sorted[n-1],hiF),
    outlier_detail:outDet,outliers:outDet.map(function(d){return d.v;}),
    np_ti_lo:null,np_ti_up:null});
}
function buildLayout(conds,params){
  var yRange=Y_LIM;
  /* Spec lines as layout shapes — always replaced by Plotly.react, never stale */
  var shapes=[],annotations=[];
  var hiSpecs={},loSpecs={};
  (conds||[]).forEach(function(cd){
    (cd.freq_stats||[]).forEach(function(fs){
      if(fs.spec_up!==null&&fs.spec_up!==undefined) hiSpecs[Math.round(fs.spec_up*100)/100]=true;
      if(fs.spec_lo!==null&&fs.spec_lo!==undefined) loSpecs[Math.round(fs.spec_lo*100)/100]=true;
    });
  });
  if(params&&params.spec_hi_override!==null&&params.spec_hi_override!==undefined){
    hiSpecs={};hiSpecs[Math.round(params.spec_hi_override*100)/100]=true;
  }
  if(params&&params.spec_lo_override!==null&&params.spec_lo_override!==undefined){
    loSpecs={};loSpecs[Math.round(-Math.abs(params.spec_lo_override)*100)/100]=true;
  }
  Object.keys(hiSpecs).map(Number).sort(function(a,b){return a-b;}).forEach(function(v){
    shapes.push({type:'line',xref:'paper',x0:0,x1:1,y0:v,y1:v,line:{color:'red',dash:'dash',width:1.5}});
    annotations.push({xref:'paper',yref:'y',x:0.99,y:v,text:'Spec Hi '+v,showarrow:false,xanchor:'right',yanchor:'bottom',font:{color:'red',size:11}});
  });
  Object.keys(loSpecs).map(Number).sort(function(a,b){return b-a;}).forEach(function(v){
    shapes.push({type:'line',xref:'paper',x0:0,x1:1,y0:v,y1:v,line:{color:'red',dash:'dash',width:1.5}});
    annotations.push({xref:'paper',yref:'y',x:0.01,y:v,text:'Spec Lo '+v,showarrow:false,xanchor:'left',yanchor:'bottom',font:{color:'red',size:11}});
  });
  return {
    title:{text:TITLE,x:0.5,font:{size:15}},
    template:'plotly_white',
    xaxis:{title:'Frequency (MHz)',type:isLogX()?'log':'linear'},
    yaxis:{title:Y_LABEL,range:yRange},
    height:450,
    legend:{bgcolor:'rgba(255,255,255,0.85)',bordercolor:'#ccc',borderwidth:1},
    margin:{l:60,r:30,t:55,b:60},
    shapes:shapes,annotations:annotations
  };
}

/* ---- TLL display ---- */
function updateTLLDisplay(conds,params){
  var el=document.getElementById('tll_display');if(!el)return;
  var ups=[],los=[],margins_up=[],margins_lo=[];
  conds.forEach(function(cd){
    (cd.freq_stats||[]).forEach(function(fs){
      var r=computeFreqResult(fs,params);
      if(r.tll_up!==null) ups.push(r.tll_up);
      if(r.tll_lo!==null) los.push(r.tll_lo);
      if(r.margin_up!==null) margins_up.push(r.margin_up);
      if(r.margin_lo!==null) margins_lo.push(r.margin_lo);
    });
  });
  function rng(arr){
    if(!arr.length) return '—';
    var mn=Math.min.apply(null,arr),mx=Math.max.apply(null,arr);
    return Math.abs(mx-mn)<0.001?mn.toFixed(4):mn.toFixed(4)+' to '+mx.toFixed(4);
  }
  var parts=['TLL↑: '+rng(ups)+'  |  TLL↓: '+rng(los)];
  if(margins_up.length){
    var wm=Math.min.apply(null,margins_up);
    var pass=wm>=0;
    parts.push('Margin↑ (worst): '+(pass?'+':'')+wm.toFixed(3)+' dB '+(pass?'✔':'✘'));
  }
  if(margins_lo.length){
    var wml=Math.min.apply(null,margins_lo);
    parts.push('Margin↓ (worst): '+(wml>=0?'+':'')+wml.toFixed(3)+' dB '+(wml>=0?'✔':'✘'));
  }
  el.textContent=parts.join('  |  ');
}

/* ---- statistics summary table ---- */
function normTag(fs){
  var c=normColor(fs.norm);
  return '<span style="color:'+c+';font-weight:bold">'+fs.norm+'</span> W='+fs.W.toFixed(3);
}
function toggleStatPanel(){
  var el=document.getElementById('stat_panel');
  var btn=document.getElementById('stat_toggle_btn');
  if(!el||!btn) return;
  if(el.style.display==='none'){
    el.style.display='';btn.textContent='&#9660; Statistics Table';
    var conds=getActiveConditions();var params=getParams();
    updateStatPanel(conds,params);
  } else {
    el.style.display='none';btn.textContent='&#9658; Statistics Table';
  }
}
function updateStatPanel(conds,params){
  var el=document.getElementById('stat_panel');if(!el||el.style.display==='none')return;
  var nFail=0,rows=[];
  conds.forEach(function(cd){
    var sorted=(cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;});
    sorted.forEach(function(fs){
      var r=computeFreqResult(fs,params);
      var pass=r.pass_up&&r.pass_lo&&r.ssu_pass_up&&r.ssu_pass_lo;
      if(!pass) nFail++;
      var bg=pass?'':'background:#fff0f0';
      var tiStr=(r.np_active?'[NP] ':'')+
               '['+r.ti_lo.toFixed(4)+', '+r.ti_up.toFixed(4)+']';
      var tllStr=(r.tll_lo!==null&&r.tll_up!==null)?
        '['+r.tll_lo.toFixed(4)+', '+r.tll_up.toFixed(4)+']':'—';
      var ssuStr=r.ssu_up.toFixed(4);
      var marginStr=r.margin_up!==null?
        '<span style="color:'+(r.margin_up>=0?'green':'red')+';font-weight:bold">'+
        (r.margin_up>=0?'+':'')+r.margin_up.toFixed(4)+'</span>':'—';
      var outCells='';
      if(fs.outliers&&fs.outliers.length){
        outCells='<td class="out"><b>'+fs.outliers.length+'</b>: '+
          fs.outliers.map(function(v){return v.toFixed(4);}).join(', ')+'</td>';
      } else {
        outCells='<td style="color:#aaa">—</td>';
      }
      rows.push('<tr style="'+bg+'">'+
        '<td>'+cd.condition+'</td>'+
        '<td>'+fs.freq.toFixed(4)+'</td>'+
        '<td>'+r.n_use+'</td>'+
        '<td>'+fs.mean.toFixed(4)+'</td>'+
        '<td>'+fs.s.toFixed(4)+'</td>'+
        '<td>'+tiStr+'</td>'+
        '<td>'+tllStr+'</td>'+
        '<td>'+ssuStr+'</td>'+
        '<td>'+marginStr+'</td>'+
        '<td style="text-align:center">'+(pass?
          '<span style="color:green">✔</span>':
          '<span style="color:red;font-weight:bold">✘ FAIL</span>')+'</td>'+
        (r.np_active
          ?'<td style="font-size:11px;color:#a06000;font-weight:bold">NP order-stat</td>'
          :'<td style="font-size:11px;color:#555">k·s ('+r.k.toFixed(3)+')</td>')+
        '<td>'+normTag(fs)+'</td>'+
        outCells+
        '</tr>');
    });
  });
  var banner='';
  if(nFail>0)
    banner='<div class="fail-banner">⚠️ '+nFail+
      ' freq/condition pair'+(nFail>1?'s':'')+' exceed TLL</div>';
  var hdr='<table class="stbl"><thead><tr>'+
    '<th>Condition</th><th>Freq (MHz)</th><th>n</th>'+
    '<th>Mean</th><th>Std</th>'+
    '<th>TI Bounds</th><th>TLL Bounds</th>'+
    '<th>Spec Spt↑</th><th>Margin↑</th>'+
    '<th>Pass</th><th>Method</th><th>Normality</th><th>Outliers</th></tr></thead><tbody>';
  el.innerHTML=banner+hdr+rows.join('')+'</tbody></table>';
}

/* ---- data filter ---- */
function getDataFilter(){
  var mode='all';
  document.querySelectorAll('input[name="data_flt"]').forEach(function(r){if(r.checked)mode=r.value;});
  var yloEl=document.getElementById('flt_ylo'),yhiEl=document.getElementById('flt_yhi');
  var ylo=parseFloat(yloEl?yloEl.value:'');
  var yhi=parseFloat(yhiEl?yhiEl.value:'');
  return {mode:mode, ylo:isNaN(ylo)?-Infinity:ylo, yhi:isNaN(yhi)?Infinity:yhi};
}
function toggleRangeInputs(){
  var el=document.getElementById('flt_range_inputs');
  if(el){
    var r=document.querySelector('input[name="data_flt"][value="range"]');
    el.style.display=(r&&r.checked)?'inline-flex':'none';
  }
}
function applyDataFilter(conds,params,flt){
  if(flt.mode==='all') return conds;
  return conds.map(function(cd){
    var fs2=(cd.freq_stats||[]).filter(function(fs){
      var r=computeFreqResult(fs,params);
      if(flt.mode==='passing') return r.pass_up&&r.pass_lo;
      if(flt.mode==='range') return r.ti_up<=flt.yhi;
      return true;
    });
    return Object.assign({},cd,{freq_stats:fs2});
  });
}

/* ---- freq slider ---- */
function syncFreq(){
  var lo=parseFloat(document.getElementById('freq_lo').value);
  var hi=parseFloat(document.getElementById('freq_hi').value);
  document.getElementById('freq_lo_txt').value=lo.toFixed(3);
  document.getElementById('freq_hi_txt').value=hi.toFixed(3);
  update();
}
function freqTxtChange(which){
  var txt=document.getElementById('freq_'+which+'_txt');
  var slider=document.getElementById('freq_'+which);
  var v=parseFloat(txt.value);
  if(isNaN(v)){txt.value=parseFloat(slider.value).toFixed(3);return;}
  v=Math.max(parseFloat(slider.min),Math.min(parseFloat(slider.max),v));
  if(which==='lo'){var h=parseFloat(document.getElementById('freq_hi').value);if(v>h)v=h;}
  else{var l=parseFloat(document.getElementById('freq_lo').value);if(v<l)v=l;}
  txt.value=v.toFixed(3);slider.value=v;update();
}
function freqStep(which,dir){
  var fv=FREQ_VALS,txt=document.getElementById('freq_'+which+'_txt'),slider=document.getElementById('freq_'+which);
  var cur=parseFloat(txt.value),idx=-1;
  for(var i=0;i<fv.length;i++){if(Math.abs(fv[i]-cur)<0.0001){idx=i;break;}}
  if(idx<0){var best=0;for(var i=1;i<fv.length;i++){if(Math.abs(fv[i]-cur)<Math.abs(fv[best]-cur))best=i;}idx=best;}
  var ni=Math.max(0,Math.min(fv.length-1,idx+dir)),nv=fv[ni];
  if(which==='lo'){if(nv>parseFloat(document.getElementById('freq_hi').value))return;}
  else{if(nv<parseFloat(document.getElementById('freq_lo').value))return;}
  txt.value=nv.toFixed(3);slider.value=nv;update();
}
function freqKeyDown(e,which){
  if(e.key==='Enter')freqTxtChange(which);
  else if(e.key==='ArrowUp'){e.preventDefault();freqStep(which,1);}
  else if(e.key==='ArrowDown'){e.preventDefault();freqStep(which,-1);}
}

/* ---- main update ---- */
function update(){
  var conds=getActiveConditions();
  var fLoTxt=document.getElementById('freq_lo_txt'),fHiTxt=document.getElementById('freq_hi_txt');
  var fLo=fLoTxt&&fLoTxt.value!==''?parseFloat(fLoTxt.value):parseFloat(document.getElementById('freq_lo').value);
  var fHi=fHiTxt&&fHiTxt.value!==''?parseFloat(fHiTxt.value):parseFloat(document.getElementById('freq_hi').value);
  if(isNaN(fLo)){var _sl=document.getElementById('freq_lo');fLo=_sl?parseFloat(_sl.value):-Infinity;}
  if(isNaN(fHi)){var _sh=document.getElementById('freq_hi');fHi=_sh?parseFloat(_sh.value):Infinity;}
  conds=conds.map(function(cd){
    return Object.assign({},cd,{
      freq_stats:(cd.freq_stats||[]).filter(function(fs){return fs.freq>=fLo&&fs.freq<=fHi;})
    });
  });
  var params=getParams();
  var selSers=getSelectedSerials();var allSers=getAllSerials();
  var serFlt=allSers.length>1&&selSers.length<allSers.length;
  var gfToggle=document.getElementById('stat_gf_chk');
  var gfEnabled=gfToggle?gfToggle.checked:true;
  var hasGf=gfEnabled&&_gfExcluded&&_gfExcluded.size>0;
  if(serFlt||hasGf){
    var activeSers;
    if(serFlt){
      activeSers=selSers;
    } else {
      /* GF-only: DOM may have no .ser_chk when dataset has ≤1 serial — collect from data */
      var _ads=new Set();
      conds.forEach(function(cd){(cd.freq_stats||[]).forEach(function(fs){(fs.dut_vals||[]).forEach(function(d){if(d.s!=null)_ads.add(d.s);});});});
      activeSers=_ads.size?Array.from(_ads):allSers;
    }
    conds=conds.map(function(cd){
      var nfs=[];
      (cd.freq_stats||[]).forEach(function(fs){
        var r=recomputeFreqStat(fs,activeSers,cd.condition,fs.freq,hasGf);if(r) nfs.push(r);
      });
      return Object.assign({},cd,{freq_stats:nfs});
    });
  }
  var flt=getDataFilter();
  conds=applyDataFilter(conds,params,flt);
  Plotly.purge('plot');Plotly.newPlot('plot',buildTraces(conds,params),buildLayout(conds,params),{responsive:true});
  updateTLLDisplay(conds,params);
  updateStatPanel(conds,params);
  var nEl=document.getElementById('n_footnote');
  if(nEl){
    var no=params.n_override;
    if(no>0){nEl.textContent='n override='+no+' (applied to all freqs)';}
    else{
      var ns=conds.reduce(function(a,cd){
        return a.concat((cd.freq_stats||[]).map(function(fs){return fs.n;}));
      },[]);
      if(ns.length){
        var mn=Math.min.apply(null,ns),mx=Math.max.apply(null,ns);
        nEl.textContent='Population n per freq: '+(mn===mx?mn:mn+'–'+mx)+' DUTs';
      }
    }
  }
  saveState();
}

/* ---- localStorage state persistence ---- */
function _stGet(k){try{return localStorage.getItem(STATE_KEY+k);}catch(e){return null;}}
function _stSet(k,v){try{localStorage.setItem(STATE_KEY+k,v);}catch(e){}}
function _stClear(){try{var keys=[];for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);if(k&&k.indexOf(STATE_KEY)===0)keys.push(k);}keys.forEach(function(k){localStorage.removeItem(k);});}catch(e){}}
function saveState(){
  _stSet('freq_lo',document.getElementById('freq_lo').value);
  _stSet('freq_hi',document.getElementById('freq_hi').value);
  document.querySelectorAll('.env_chk').forEach(function(c){_stSet('temp_'+c.value,c.checked?'1':'0');});
  if(typeof COND_DIMS!=='undefined') COND_DIMS.forEach(function(dim){
    var col='cond_'+dim.col_id;
    document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){_stSet('cond_'+col+'_'+encodeURIComponent(c.value),c.checked?'1':'0');});
  });
  var fltEl=document.querySelector('input[name="data_flt"]:checked');if(fltEl)_stSet('stat_filter_mode',fltEl.value);
  var yhiEl=document.getElementById('flt_yhi');if(yhiEl)_stSet('stat_filter_yhi',yhiEl.value);
  var tllEl=document.getElementById('stat_tll_hi');if(tllEl)_stSet('stat_tll_hi',tllEl.value);
  var tllLoEl=document.getElementById('stat_tll_lo');if(tllLoEl)_stSet('stat_tll_lo',tllLoEl.value);
  var pEl=document.getElementById('stat_P');if(pEl)_stSet('stat_P',pEl.value);
  var cEl=document.getElementById('stat_C');if(cEl)_stSet('stat_C',cEl.value);
  var muEl=document.getElementById('stat_mu');if(muEl)_stSet('stat_mu',muEl.value);
  var gbEl=document.getElementById('stat_gb');if(gbEl)_stSet('stat_gb',gbEl.value);
  var drEl=document.getElementById('stat_drift');if(drEl)_stSet('stat_drift',drEl.value);
}
function loadState(){
  var lo=_stGet('freq_lo'),hi=_stGet('freq_hi');
  if(lo!==null){var sl=document.getElementById('freq_lo');if(sl)sl.value=lo;var txlo=document.getElementById('freq_lo_txt');if(txlo)txlo.value=parseFloat(lo).toFixed(3);}
  if(hi!==null){var sh=document.getElementById('freq_hi');if(sh)sh.value=hi;var txhi=document.getElementById('freq_hi_txt');if(txhi)txhi.value=parseFloat(hi).toFixed(3);}
  document.querySelectorAll('.env_chk').forEach(function(c){var s=_stGet('temp_'+c.value);if(s!==null&&!c.disabled)c.checked=(s==='1');});
  if(typeof COND_DIMS!=='undefined') COND_DIMS.forEach(function(dim){
    var col='cond_'+dim.col_id;
    document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){var s=_stGet('cond_'+col+'_'+encodeURIComponent(c.value));if(s!==null)c.checked=(s==='1');});
    var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
    var allChk=document.getElementById('all_'+col);
    if(allChk){var n=chks.filter(function(c){return c.checked;}).length;allChk.checked=(n===chks.length);allChk.indeterminate=(n>0&&n<chks.length);}
    updateBadge(col);
  });
  var fm=_stGet('stat_filter_mode');
  if(fm){var fr=document.querySelector('input[name="data_flt"][value="'+fm+'"]');if(fr){fr.checked=true;toggleRangeInputs();}}
  var fyhi=_stGet('stat_filter_yhi');var fyhiEl=document.getElementById('flt_yhi');if(fyhi!==null&&fyhiEl)fyhiEl.value=fyhi;
  var tll=_stGet('stat_tll_hi');var tllEl=document.getElementById('stat_tll_hi');if(tll!==null&&tllEl)tllEl.value=tll;
  var tllLo=_stGet('stat_tll_lo');var tllLoEl=document.getElementById('stat_tll_lo');if(tllLo!==null&&tllLoEl)tllLoEl.value=tllLo;
  var sp=_stGet('stat_P');if(sp!==null){var pEl=document.getElementById('stat_P');if(pEl)pEl.value=sp;var lpEl=document.getElementById('lbl_P');if(lpEl)lpEl.value=sp;}
  var sc=_stGet('stat_C');if(sc!==null){var cEl=document.getElementById('stat_C');if(cEl)cEl.value=sc;var lcEl=document.getElementById('lbl_C');if(lcEl)lcEl.value=sc;}
  var smu=_stGet('stat_mu');if(smu!==null){var muEl=document.getElementById('stat_mu');if(muEl)muEl.value=smu;}
  var sgb=_stGet('stat_gb');if(sgb!==null){var gbEl=document.getElementById('stat_gb');if(gbEl)gbEl.value=sgb;}
  var sdr=_stGet('stat_drift');if(sdr!==null){var drEl=document.getElementById('stat_drift');if(drEl)drEl.value=sdr;}
}

_loadStatGlobalFilter();
loadState();
Plotly.newPlot('plot',buildTraces(getActiveConditions(),getParams()),buildLayout(getActiveConditions(),getParams()),{responsive:true});
/* END */

"""


def _build_stat_summary_html(
    stat_data: list,
    k_table: dict,
    df: pd.DataFrame,
    cfg: dict,
    title: str,
    all_serials: list = None,
) -> str:
    """Build a fully self-contained interactive HTML for stat_summary."""
    y_label = cfg.get("y_label", df["_val_col_name"].iloc[0] if len(df) else "Value")
    y_lim = cfg.get("y_lim")
    log_x_cfg = cfg.get("log_x", None)

    freq_min = float(df["Frequency_MHz"].min()) if len(df) else 0.0
    freq_max = float(df["Frequency_MHz"].max()) if len(df) else 1.0
    freq_step = max(round((freq_max - freq_min) / 1000, 4), 0.001)

    if log_x_cfg is not None:
        log_x = bool(log_x_cfg)
    else:
        log_x = freq_min > 0 and (freq_max / freq_min) >= 100

    default_P  = float(cfg.get("proportion", 0.90))
    default_C  = float(cfg.get("confidence", 0.90))
    default_mu = float(cfg.get("meas_unc", 0.0))
    default_gb = float(cfg.get("guard_band", 0.0))
    # Spec inputs are intentionally left empty so each condition's per-frequency
    # spec_up/spec_lo drives the display. A global first-row spec would be wrong
    # for datasets with different specs per condition (e.g. harmonics vs sub-harmonics).
    spec_lo_val = ""
    spec_hi_val = ""

    lo_js = "null"
    hi_js = "null"

    # Build COND_DIMS from condition strings in stat_data
    # Each condition is e.g. "OA State: 0  Mode: 1  SpurType: Close-in 10KHz high"
    dim_vals: dict[str, set] = {}
    for cd in stat_data:
        for part in re.split(r'  +', cd["condition"]):
            m = re.match(r'(.+?):\s*(.+?)\s*$', part.strip())
            if m:
                key = m.group(1).strip()
                val = m.group(2).strip()
                dim_vals.setdefault(key, set()).add(val)

    def _sort_numeric(vals):
        try:
            return sorted(vals, key=float)
        except (ValueError, TypeError):
            return sorted(vals)

    cond_dims = []
    for key in sorted(dim_vals.keys()):
        vals = _sort_numeric(dim_vals[key])
        if len(vals) > 1:
            col_id = re.sub(r'\W+', '_', key)  # "OA State" → "OA_State"
            cond_dims.append({"col": key, "col_id": col_id, "label": key, "vals": vals})

    # Build checkbox filter panels
    panels: list[str] = []
    for dim in cond_dims:
        panel_id = "cond_" + dim["col_id"]
        items = "".join(
            f'<label class="fitem"><input type="checkbox" class="fchk" data-col="{panel_id}"'
            f' value="{v}" checked onchange="chkChanged(\'{panel_id}\')">{v}</label>'
            for v in dim["vals"]
        )
        panels.append(
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'{panel_id}\')">'
            f'{dim["label"]}&thinsp;<span id="badge_{panel_id}" class="badge"></span>&#9662;</button>'
            f'<div class="filter-panel" id="panel_{panel_id}">'
            f'<label class="fitem fall"><input type="checkbox" id="all_{panel_id}"'
            f' checked onchange="toggleAll(\'{panel_id}\')"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{items}</div></div>'
        )
    panels_html = "\n  ".join(panels)

    # Collect all temperature names across all conditions (union)
    all_temps: set[str] = set()
    for cd in stat_data:
        for t in cd.get("temps_present", []):
            all_temps.add(t)
    non_room_temps = sorted(t for t in all_temps if t != "Room")

    # Derive ports from stat_data dut_vals — used in both constants block and port panel HTML
    all_ports_ss = sorted({
        d["p"] for cd in stat_data
        for fs in cd.get("freq_stats", [])
        for d in fs.get("dut_vals", [])
        if d.get("p")
    })

    constants = "\n".join([
        f"var STAT_DATA={json.dumps(stat_data)};",
        f"var KT={json.dumps(k_table)};",
        f"var TITLE={json.dumps(title)};",
        f"var Y_LABEL={json.dumps(y_label)};",
        f"var Y_LIM={json.dumps(y_lim)};",
        f"var LOG_X={'true' if log_x else 'false'};",
        f"var FREQ_MIN={freq_min!r};",
        f"var FREQ_MAX={freq_max!r};",
        f"var LO_SPEC={lo_js};",
        f"var HI_SPEC={hi_js};",
        f"var DEFAULT_P={default_P!r};",
        f"var DEFAULT_C={default_C!r};",
        f"var DEFAULT_MU={default_mu!r};",
        f"var DEFAULT_GB={default_gb!r};",
        f"var COND_DIMS={json.dumps(cond_dims)};",
        f"var TEMPS_PRESENT={json.dumps(['Room'] + non_room_temps)};",
        f"var FREQ_VALS={json.dumps(sorted(float(f) for f in df['Frequency_MHz'].dropna().unique()))};",
        f"var SS_ALL_PORTS={json.dumps(all_ports_ss)};",
        f"var STATE_KEY='padb_{cfg.get('results_dir', '')}';",
    ])

    css = (
        "body{font-family:Arial,sans-serif;margin:0;padding:8px;background:#fafafa;}"
        ".ctrl-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:8px 14px;background:#f0f2f5;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".stat-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:6px 14px;background:#eef0f8;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".ctrl-bar label,.stat-bar label{white-space:nowrap;}"
        ".ctrl-bar input[type=range],.stat-bar input[type=range]{vertical-align:middle;width:90px;}"
        ".ctrl-bar input[type=number],.stat-bar input[type=number]{width:64px;font-size:13px;"
        "padding:2px 4px;border:1px solid #bbb;border-radius:3px;}"
        ".sep{border-left:2px solid #ccc;height:22px;margin:0 2px;}"
        ".filter-wrap{position:relative;display:inline-block;}"
        ".filter-btn{font-size:13px;padding:3px 10px;border:1px solid #bbb;border-radius:3px;"
        "cursor:pointer;background:#fff;white-space:nowrap;}"
        ".filter-btn:hover{background:#e8e8e8;}"
        ".filter-panel{display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:200;"
        "background:#fff;border:1px solid #ccc;border-radius:4px;"
        "box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:140px;max-height:280px;"
        "overflow-y:auto;padding:6px 8px;}"
        ".filter-panel.open{display:block;}"
        ".fitem{display:block;padding:2px 0;cursor:pointer;white-space:nowrap;font-size:13px;}"
        ".fall{padding-bottom:2px;}"
        ".fdiv{margin:4px 0;border:none;border-top:1px solid #eee;}"
        ".badge{font-size:11px;background:#0066cc;color:#fff;border-radius:10px;"
        "padding:1px 6px;margin-right:2px;display:none;}"
        ".badge.active{display:inline;}"
        ".tll-display{font-size:13px;font-weight:bold;color:#333;"
        "background:#fffbe6;border:1px solid #e6c200;border-radius:4px;padding:2px 10px;}"
        ".footnote{font-size:11px;color:#888;margin-top:2px;padding:2px 14px;}"
        ".toggle-btn{font-size:13px;padding:4px 14px;border:1px solid #bbb;border-radius:4px;"
        "cursor:pointer;background:#f0f2f5;margin:4px 4px;}"
        ".toggle-btn:hover{background:#e0e2e8;}"
        ".norm-legend{font-size:11px;}"
        ".nl-dot{display:inline-block;width:10px;height:10px;border-radius:50%;"
        "margin-right:3px;vertical-align:middle;}"
        "#stat_panel{margin-top:8px;overflow-x:auto;}"
        ".stbl{border-collapse:collapse;font-size:12px;width:100%;}"
        ".stbl th{background:#e8eaf6;padding:4px 8px;text-align:left;border:1px solid #ccc;"
        "white-space:nowrap;position:sticky;top:0;}"
        ".stbl td{padding:3px 8px;border:1px solid #ddd;white-space:nowrap;}"
        ".stbl tr:hover td{background:#f5f5ff;}"
        ".out{color:#c00;font-size:11px;}"
        ".fail-banner{background:#ffeaea;border:1px solid #f88;border-radius:4px;"
        "padding:6px 12px;margin-bottom:6px;font-weight:bold;color:#900;}"
        ".flt-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:5px 14px;background:#f5f5e8;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".flt-bar label{white-space:nowrap;cursor:pointer;}"
        ".flt-bar input[type=number]{width:70px;font-size:13px;padding:2px 4px;"
        "border:1px solid #bbb;border-radius:3px;}"
        ".env-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:5px 14px;background:#edf7ee;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".env-bar label{white-space:nowrap;cursor:pointer;}"
        ".csv-btn{font-size:13px;padding:3px 12px;border:1px solid #0066cc;border-radius:3px;"
        "cursor:pointer;background:#e8f4ff;color:#0066cc;margin-left:6px;}"
        ".csv-btn:hover{background:#cce4ff;}"
        "input.freq-txt{font-size:12px;width:72px;padding:1px 3px;border:1px solid #bbb;"
        "border-radius:3px;text-align:right;margin-left:2px;}"
        + _CSV_DROPDOWN_CSS
    )

    sep = '<div class="sep"></div>'

    freq_lo_html = (
        f'<label>Freq&nbsp;min:<input type="range" id="freq_lo"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_min:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()" onchange="update()">'
        f'<input class="freq-txt" id="freq_lo_txt" type="text" value="{freq_min:.3f}"'
        f' onchange="freqTxtChange(\'lo\')" onkeydown="freqKeyDown(event,\'lo\')">&nbsp;MHz</label>'
    )
    freq_hi_html = (
        f'<label>Freq&nbsp;max:<input type="range" id="freq_hi"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_max:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()" onchange="update()">'
        f'<input class="freq-txt" id="freq_hi_txt" type="text" value="{freq_max:.3f}"'
        f' onchange="freqTxtChange(\'hi\')" onkeydown="freqKeyDown(event,\'hi\')">&nbsp;MHz</label>'
    )
    log_x_html = (
        f'<label><input type="checkbox" id="log_x_chk"'
        + (' checked' if log_x else '')
        + ' onchange="update()"> Log&nbsp;X</label>'
    )

    serial_panel_html = ""
    if all_serials and len(all_serials) > 1:
        ser_items = "".join(
            f'<label class="fitem"><input type="checkbox" class="ser_chk" value="{s}"'
            f' checked onchange="serChkChanged()">&nbsp;{s}</label>'
            for s in all_serials
        )
        serial_panel_html = (
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'ser_panel\')">'
            f'Serial&thinsp;<span id="badge_ser_panel" class="badge"></span>&#9662;</button>'
            f'<div class="filter-panel" id="panel_ser_panel">'
            f'<label class="fitem fall"><input type="checkbox" id="all_ser_panel"'
            f' checked onchange="toggleAllSer()"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{ser_items}</div></div>'
        )

    port_panel_html_ss = ""
    if len(all_ports_ss) > 1:
        port_items_ss = "".join(
            f'<label class="fitem"><input type="checkbox" class="ss_port_chk" value="{p}"'
            f' checked onchange="ssPortChkChanged()">&nbsp;{p}</label>'
            for p in all_ports_ss
        )
        port_panel_html_ss = (
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'ss_port_panel\')">'
            f'Port&thinsp;<span id="badge_ss_port_panel" class="badge"></span>&#9662;</button>'
            f'<div class="filter-panel" id="panel_ss_port_panel">'
            f'<label class="fitem fall"><input type="checkbox" id="all_ss_port_panel"'
            f' checked onchange="toggleAllSsPort()"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{port_items_ss}</div></div>'
        )

    ctrl_bar = (
        '<div class="ctrl-bar">\n'
        + (f'  {panels_html}\n  {sep}\n' if panels_html else '')
        + (f'  {serial_panel_html}\n' if serial_panel_html else '')
        + (f'  {port_panel_html_ss}\n  {sep}\n' if port_panel_html_ss else
           (f'  {sep}\n' if serial_panel_html else ''))
        + f'  {freq_lo_html}\n'
        + f'  {freq_hi_html}\n'
        + f'  {log_x_html}\n'
        + '</div>\n'
    )

    _snap_P = min([0.80, 0.95, 0.9973], key=lambda x: abs(x - default_P))
    _snap_C = min([0.90, 0.95], key=lambda x: abs(x - default_C))
    stat_bar = (
        '<div class="stat-bar" onclick="event.stopPropagation()">\n'
        f'  <label><b>P:</b>&nbsp;<select id="stat_P" onchange="update()">'
        f'<option value="0.80"{"  selected" if abs(_snap_P-0.80)<0.001 else ""}>80%</option>'
        f'<option value="0.95"{"  selected" if abs(_snap_P-0.95)<0.001 else ""}>95%</option>'
        f'<option value="0.9973"{"  selected" if abs(_snap_P-0.9973)<0.001 else ""}>99.73%</option>'
        f'</select></label>\n'
        f'  <label><b>C:</b>&nbsp;<select id="stat_C" onchange="update()">'
        f'<option value="0.90"{"  selected" if abs(_snap_C-0.90)<0.001 else ""}>90%</option>'
        f'<option value="0.95"{"  selected" if abs(_snap_C-0.95)<0.001 else ""}>95%</option>'
        f'</select></label>\n'
        f'  <label title="Override sample size (0=auto from data)">n&nbsp;override:'
        f'<input type="number" id="stat_n" min="0" max="50" value="0" oninput="update()"></label>\n'
        f'  {sep}\n'
        f'  <label title="Measurement Uncertainty">M.U.:'
        f'<input type="number" id="stat_mu" value="{default_mu}" min="0" step="0.001" oninput="update()"></label>\n'
        f'  <label title="Upper DEnv (0 = use computed from temperature data)">'
        f'&#916;Env&#8593;:<input type="number" id="stat_denv_up" value="0" step="0.001" oninput="update()"></label>\n'
        f'  <label title="Lower DEnv (0 = use computed)">'
        f'&#916;Env&#8595;:<input type="number" id="stat_denv_lo" value="0" step="0.001" oninput="update()"></label>\n'
        f'  <label>Guard&nbsp;Band:<input type="number" id="stat_gb"'
        f' value="{default_gb}" step="0.001" oninput="update()"></label>\n'
        f'  <label>Drift:<input type="number" id="stat_drift" value="0" step="0.001" oninput="update()"></label>\n'
        f'  {sep}\n'
        f'  <label title="Lower spec magnitude, e.g. 0.15 for a &#177;0.15 spec (sign applied automatically)">'
        f'&#124;Spec&#8595;&#124;:<input type="number" id="stat_spec_lo" value="{spec_lo_val}" min="0" step="0.001"'
        f' style="width:74px" placeholder="0.15" oninput="update()"></label>\n'
        f'  <label title="Upper spec limit, e.g. 0.15 for a &#177;0.15 spec">'
        f'Spec&#8593;:<input type="number" id="stat_spec_hi" value="{spec_hi_val}" step="0.001"'
        f' style="width:74px" placeholder="0.15" oninput="update()"></label>\n'
        f'  {sep}\n'
        f'  <span class="tll-display" id="tll_display">TLL&#8593;: —&nbsp;&nbsp;TLL&#8595;: —</span>\n'
        f'  {sep}\n'
        f'  <label title="Override computed TLL upper limit directly (bypasses Spec - guard band)">'
        f'TLL&#8593;&nbsp;override:<input type="number" id="stat_tll_hi" step="0.001" placeholder="auto"'
        f' style="width:74px" oninput="update()"></label>\n'
        f'  <label title="Override computed TLL lower limit directly (bypasses Spec - guard band)">'
        f'TLL&#8595;&nbsp;override:<input type="number" id="stat_tll_lo" step="0.001" placeholder="auto"'
        f' style="width:74px" oninput="update()"></label>\n'
        f'  <span class="norm-legend">'
        f'&nbsp;<span class="nl-dot" style="background:green"></span>Normal'
        f'&nbsp;<span class="nl-dot" style="background:orange"></span>Marginal'
        f'&nbsp;<span class="nl-dot" style="background:red"></span>Non-normal</span>\n'
        '</div>\n'
        '<div class="footnote"><span id="n_footnote"></span></div>\n'
    )

    y_lim_lo = y_lim[0] if y_lim else ""
    y_lim_hi = y_lim[1] if y_lim else ""

    # Environmental step selector bar (only shown when non-Room temps present)
    if non_room_temps:
        env_items = '<label><input type="checkbox" class="env_chk" value="Room" checked disabled>&nbsp;Room</label>\n'
        for t in non_room_temps:
            env_items += (
                f'  <label><input type="checkbox" class="env_chk" value="{t}" checked'
                f' onchange="update()">&nbsp;{t}</label>\n'
            )
        env_bar = (
            '<div class="env-bar" onclick="event.stopPropagation()">\n'
            '  <b>&#916;Env&nbsp;steps:</b>\n'
            '  ' + env_items +
            '  <small style="color:#666">(uncheck to exclude a temp step from DEnv; override with &#916;Env inputs above)</small>\n'
            '</div>\n'
        )
    else:
        env_bar = ""

    filter_bar = (
        '<div class="flt-bar" onclick="event.stopPropagation()">\n'
        '  <b>Data&nbsp;filter:</b>\n'
        '  <label><input type="radio" name="data_flt" value="all" checked'
        ' onchange="toggleRangeInputs();update()"> All&nbsp;data</label>\n'
        '  <label><input type="radio" name="data_flt" value="passing"'
        ' onchange="toggleRangeInputs();update()"> Passing&nbsp;only&nbsp;(TI&#8838;TLL)</label>\n'
        '  <label><input type="radio" name="data_flt" value="range"'
        ' onchange="toggleRangeInputs();update()"> Upper&nbsp;limit</label>\n'
        '  <span id="flt_range_inputs" style="display:none;align-items:center;gap:4px">\n'
        f'    <input type="number" id="flt_yhi" placeholder="dBc limit" step="0.001" value="{y_lim_hi}"'
        ' oninput="update()">\n'
        '    <small style="color:#666">(hides freqs where TI upper bound exceeds this limit; Y scale unchanged)</small>\n'
        '  </span>\n'
        '  <span class="sep"></span>\n'
        '  <label title="Use non-parametric (distribution-free) order-statistic TI'
        ' for Non-normal and Marginal frequencies">'
        '<input type="checkbox" id="np_ti_chk" onchange="update()">'
        '&nbsp;Non-parametric&nbsp;TI</label>\n'
        '  <span class="sep"></span>\n'
        '  <label title="Overlay individual measurement points on the plot">'
        '<input type="checkbox" id="show_pts_chk" onchange="update()">'
        '&nbsp;Show&nbsp;points</label>\n'
        '  <label id="stat_gf_label" style="display:none;white-space:nowrap;margin-left:4px">'
        '<input type="checkbox" id="stat_gf_chk" checked '
        'onchange="_updateStatGfBadge();update()">'
        '&nbsp;<span id="stat_gf_badge" style="font-size:11px;background:#fff0e8;'
        'border:1px solid #e0905a;border-radius:3px;padding:1px 7px;color:#c04000"></span>'
        '</label>\n'
        f'  {_csv_btn("saveCSV")}\n'
        '</div>\n'
    )

    html = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f"<style>{css}</style>\n"
        "</head>\n<body>\n"
        + ctrl_bar
        + stat_bar
        + env_bar
        + filter_bar
        + '<div style="padding:4px 8px 2px">'
        + '<button class="toggle-btn" id="stat_toggle_btn" onclick="toggleStatPanel()">'
        + '&#9658; Statistics Table</button></div>\n'
        + '<div id="stat_panel" style="display:none;padding:0 4px 16px"></div>\n'
        + '<div id="plot"></div>\n'
        + f"<script>{_get_plotlyjs()}</script>\n"
        + "<script>\n"
        + constants + "\n"
        + _STAT_SUMMARY_JS
        + "</script>\n</body>\n</html>"
    )
    return html


# ---------------------------------------------------------------------------
# de_summary — Environmental (Type=60) delta plot
# ---------------------------------------------------------------------------

_ENV_SUMMARY_JS = r"""
function isLogX(){return document.getElementById('env_log_x_chk').checked;}
function getSelectedConds(){
  return ENV_DATA.filter(function(cd){
    return COND_DIMS.every(function(dim){
      var all=document.querySelectorAll('.'+dim.col_id);
      if(!all.length) return true;
      var chks=Array.from(all).filter(function(c){return c.checked;}).map(function(c){return c.value;});
      if(!chks.length) return false;
      var safe=dim.col.replace(/[-\/\\^$*+?.()|[\]{}]/g,'\\$&');
      var m=cd.condition.match(new RegExp(safe+':\\s*(.+?)(?=\\s{2,}|$)'));
      return !m||chks.indexOf(m[1].trim())>=0;
    });
  });
}
function getFreqRange(){
  var lo=parseFloat(document.getElementById('env_freq_lo').value);
  var hi=parseFloat(document.getElementById('env_freq_hi').value);
  return {lo:isNaN(lo)?-Infinity:lo,hi:isNaN(hi)?Infinity:hi};
}
function getEnvDataFilter(){
  var r=document.querySelector('input[name="env_dfilt"]:checked');
  var mode=r?r.value:'all';
  var yHi=parseFloat(document.getElementById('env_y_hi').value);
  var tll_el=document.getElementById('env_tll_hi');
  var tll_hi=(tll_el&&tll_el.value!=='')?parseFloat(tll_el.value):null;
  if(tll_hi!==null&&isNaN(tll_hi)) tll_hi=null;
  return {mode:mode,yHi:isNaN(yHi)?Infinity:yHi,tll_hi:tll_hi};
}
function toggleEnvYRange(){
  var r=document.querySelector('input[name="env_dfilt"]:checked');
  var yr=document.getElementById('env_y_range_inputs');
  if(yr) yr.style.display=(r&&r.value==='yrange')?'inline-flex':'none';
  update();
}
function getFilteredIdxs(cd,fr,flt){
  var idxs=[];
  cd.freqs.forEach(function(f,j){
    if(f<fr.lo||f>fr.hi||cd.ude[j]===null||cd.lde[j]===null) return;
    if(flt.mode==='passing'){
      var effHi=(flt.tll_hi!==null&&flt.tll_hi!==undefined)?flt.tll_hi:cd.spec_hi;
      var ok=(effHi===null||cd.ttu[j]===null||cd.ttu[j]<=effHi)&&
             (cd.spec_lo===null||cd.ttl[j]===null||cd.ttl[j]>=cd.spec_lo);
      if(!ok) return;
    } else if(flt.mode==='yrange'){
      if(cd.ude[j]>flt.yHi) return;
    }
    idxs.push(j);
  });
  return idxs;
}
function hexAlpha(hex,a){
  var r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
  return 'rgba('+r+','+g+','+b+','+a+')';
}
function buildTraces(selConds,exclConds){
  exclConds=exclConds||[];
  var traces=[];
  var fr=getFreqRange();
  var flt=getEnvDataFilter();
  /* Excluded conditions — dim gray bands rendered first (behind selected) */
  exclConds.forEach(function(cd){
    var idxs=getFilteredIdxs(cd,fr,flt);
    if(!idxs.length) return;
    var freqs=idxs.map(function(j){return cd.freqs[j];});
    var ude=idxs.map(function(j){return cd.ude[j];});
    var neg_lde=idxs.map(function(j){return -cd.lde[j];});
    traces.push({type:'scatter',x:freqs,y:ude,mode:'lines',
      line:{color:'rgba(190,190,190,0.55)',width:0.8},showlegend:false,hoverinfo:'skip'});
    traces.push({type:'scatter',x:freqs,y:neg_lde,mode:'lines',
      fill:'tonexty',fillcolor:'rgba(200,200,200,0.09)',
      line:{color:'rgba(190,190,190,0.55)',width:0.8},
      name:cd.condition,showlegend:false,
      hovertemplate:cd.condition+' (excluded)<extra></extra>'});
  });
  selConds.forEach(function(cd,i){
    var color=PALETTE[i%PALETTE.length];
    var idxs=getFilteredIdxs(cd,fr,flt);
    if(!idxs.length) return;
    var freqs=idxs.map(function(j){return cd.freqs[j];});
    var ude=idxs.map(function(j){return cd.ude[j];});
    var neg_lde=idxs.map(function(j){return -cd.lde[j];});
    var ttu=idxs.map(function(j){return cd.ttu[j];});
    var ttl=idxs.map(function(j){return cd.ttl[j];});
    var hov=idxs.map(function(j,k){
      var f=cd.freqs[j];
      var lines=[cd.condition,'Freq: '+f.toFixed(4)+' MHz'];
      if(cd.ude[j]!==null) lines.push('UDE: +'+cd.ude[j].toFixed(4));
      if(cd.lde[j]!==null) lines.push('LDE: −'+cd.lde[j].toFixed(4));
      if(cd.mean_env[j]!==null) lines.push('Mean(ΔEnv): '+cd.mean_env[j].toFixed(4));
      if(cd.min_env[j]!==null&&cd.max_env[j]!==null)
        lines.push('ObsΔ: ['+cd.min_env[j].toFixed(4)+', '+cd.max_env[j].toFixed(4)+']');
      if(cd.ttu[j]!==null&&cd.ttl[j]!==null)
        lines.push('TTL: ['+cd.ttl[j].toFixed(4)+', '+cd.ttu[j].toFixed(4)+']');
      return lines.join('<br>');
    });
    /* UDE upper — fill reference, hover suppressed */
    traces.push({type:'scatter',x:freqs,y:ude,mode:'lines',
      line:{color:color,width:1.5},
      name:cd.condition+' UDE',legendgroup:cd.condition,showlegend:false,
      hoverinfo:'skip'});
    /* -LDE lower + fill to create env contribution band */
    traces.push({type:'scatter',x:freqs,y:neg_lde,mode:'lines',
      fill:'tonexty',fillcolor:hexAlpha(color,0.18),
      line:{color:color,width:1.5},
      name:cd.condition,legendgroup:cd.condition,showlegend:true,
      text:hov,hovertemplate:'%{text}<extra></extra>'});
    /* TTL dashed lines */
    if(ttu.some(function(v){return v!==null;})){
      traces.push({type:'scatter',x:freqs,y:ttu,mode:'lines',
        line:{color:color,dash:'dot',width:1},
        name:cd.condition+' TTL↑',legendgroup:cd.condition,showlegend:false,
        hoverinfo:'skip'});
    }
    if(ttl.some(function(v){return v!==null;})){
      traces.push({type:'scatter',x:freqs,y:ttl,mode:'lines',
        line:{color:color,dash:'dot',width:1},
        name:cd.condition+' TTL↓',legendgroup:cd.condition,showlegend:false,
        hoverinfo:'skip'});
    }
  });
  /* Spec lines — use first condition with valid values */
  var spec_lo=null,spec_hi=null;
  ENV_DATA.forEach(function(cd){
    if(spec_lo===null&&cd.spec_lo!==null) spec_lo=cd.spec_lo;
    if(spec_hi===null&&cd.spec_hi!==null) spec_hi=cd.spec_hi;
  });
  var xAll=selConds.reduce(function(a,cd){return a.concat(cd.freqs);},
    []).filter(function(v){return v!==null;});
  if(!xAll.length) xAll=[ENV_FREQ_MIN,ENV_FREQ_MAX];
  var xMin=Math.min.apply(null,xAll),xMax=Math.max.apply(null,xAll);
  if(spec_lo!==null) traces.push({type:'scatter',x:[xMin,xMax],y:[spec_lo,spec_lo],mode:'lines',
    line:{color:'red',dash:'dash',width:1.5},name:'Spec Lo',
    hovertemplate:'Spec Lo: '+spec_lo.toFixed(4)+'<extra></extra>'});
  if(spec_hi!==null) traces.push({type:'scatter',x:[xMin,xMax],y:[spec_hi,spec_hi],mode:'lines',
    line:{color:'red',dash:'dash',width:1.5},name:'Spec Hi',
    hovertemplate:'Spec Hi: '+spec_hi.toFixed(4)+'<extra></extra>'});
  /* Manual TLL override line */
  var envFlt=getEnvDataFilter();
  if(envFlt.tll_hi!==null&&envFlt.tll_hi!==undefined) traces.push({type:'scatter',x:[xMin,xMax],y:[envFlt.tll_hi,envFlt.tll_hi],
    mode:'lines',line:{color:'darkred',dash:'dot',width:2},name:'TLL↑ (manual)',
    hovertemplate:'TLL↑ (manual): '+envFlt.tll_hi.toFixed(4)+'<extra></extra>'});
  return traces;
}
function buildLayout(){
  return {
    title:{text:ENV_TITLE,x:0.5,font:{size:15}},
    template:'plotly_white',
    xaxis:{title:'Frequency (MHz)',type:isLogX()?'log':'linear'},
    yaxis:{title:ENV_Y_LABEL,range:ENV_Y_LIM},
    height:480,
    legend:{bgcolor:'rgba(255,255,255,0.85)',bordercolor:'#ccc',borderwidth:1},
    margin:{l:60,r:30,t:55,b:60},
    shapes:[{type:'line',x0:0,x1:1,xref:'paper',y0:0,y1:0,
      line:{color:'#bbb',width:1,dash:'dot'}}]
  };
}
function closeAllFilterPanels(){
  document.querySelectorAll('.filter-panel').forEach(function(p){p.style.display='none';});
  var bd=document.getElementById('filter-backdrop');
  if(bd) bd.style.display='none';
}
function togglePanel(col){
  var el=document.getElementById('panel_'+col);
  if(!el) return;
  var open=el.style.display==='block';
  closeAllFilterPanels();
  if(!open){
    el.style.display='block';
    var bd=document.getElementById('filter-backdrop');
    if(bd) bd.style.display='block';
  }
}
function toggleAll(col){
  var allEl=document.getElementById('all_'+col);
  document.querySelectorAll('.'+col).forEach(function(c){c.checked=allEl?allEl.checked:true;});
  updateBadge(col);update();
}
function chkChanged(col){updateBadge(col);update();}
function updateBadge(col){
  var all=document.querySelectorAll('.'+col);
  var chk=Array.from(all).filter(function(c){return c.checked;}).length;
  var allEl=document.getElementById('all_'+col);
  if(allEl){allEl.checked=chk===all.length;allEl.indeterminate=chk>0&&chk<all.length;}
  var b=document.getElementById('badge_'+col);
  if(b){b.textContent=chk<all.length?chk+'/'+all.length:'';b.classList.toggle('active',chk<all.length);}
}
function syncFreq(){
  var lo=parseFloat(document.getElementById('env_freq_lo').value);
  var hi=parseFloat(document.getElementById('env_freq_hi').value);
  var lv=document.getElementById('env_freq_lo_txt');
  var hv=document.getElementById('env_freq_hi_txt');
  if(lv) lv.value=lo.toFixed(3);
  if(hv) hv.value=hi.toFixed(3);
  update();
}
function freqTxtChange(which){
  var txt=document.getElementById('env_freq_'+which+'_txt');
  var slider=document.getElementById('env_freq_'+which);
  var v=parseFloat(txt.value);
  if(isNaN(v)){txt.value=parseFloat(slider.value).toFixed(3);return;}
  v=Math.max(parseFloat(slider.min),Math.min(parseFloat(slider.max),v));
  if(which==='lo'){var h=parseFloat(document.getElementById('env_freq_hi').value);if(v>h)v=h;}
  else{var l=parseFloat(document.getElementById('env_freq_lo').value);if(v<l)v=l;}
  txt.value=v.toFixed(3);slider.value=v;update();
}
function freqStep(which,dir){
  var fv=ENV_FREQ_VALS,txt=document.getElementById('env_freq_'+which+'_txt'),slider=document.getElementById('env_freq_'+which);
  var cur=parseFloat(txt.value),idx=-1;
  for(var i=0;i<fv.length;i++){if(Math.abs(fv[i]-cur)<0.0001){idx=i;break;}}
  if(idx<0){var best=0;for(var i=1;i<fv.length;i++){if(Math.abs(fv[i]-cur)<Math.abs(fv[best]-cur))best=i;}idx=best;}
  var ni=Math.max(0,Math.min(fv.length-1,idx+dir)),nv=fv[ni];
  if(which==='lo'){if(nv>parseFloat(document.getElementById('env_freq_hi').value))return;}
  else{if(nv<parseFloat(document.getElementById('env_freq_lo').value))return;}
  txt.value=nv.toFixed(3);slider.value=nv;update();
}
function freqKeyDown(e,which){
  if(e.key==='Enter')freqTxtChange(which);
  else if(e.key==='ArrowUp'){e.preventDefault();freqStep(which,1);}
  else if(e.key==='ArrowDown'){e.preventDefault();freqStep(which,-1);}
}
function saveCSV(withExcluded){
  var selConds=getSelectedConds();
  var exportConds=withExcluded?ENV_DATA:selConds;
  var fr=getFreqRange();
  var hdrs=['Condition','Freq_MHz','UDE','LDE','Min_Env','Max_Env','Mean_Env','TTL_up','TTL_lo','Spec_lo','Spec_hi'];
  if(withExcluded) hdrs.push('Included');
  var rows=[hdrs.join(',')];
  function esc(v){var s=String(v==null?'':v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;}
  var flt=getEnvDataFilter();
  var selCondNames=selConds.map(function(cd){return cd.condition;});
  exportConds.forEach(function(cd){
    var isIncluded=selCondNames.indexOf(cd.condition)>=0;
    getFilteredIdxs(cd,fr,flt).forEach(function(j){
      var row=[esc(cd.condition),cd.freqs[j],
        cd.ude[j],cd.lde[j],cd.min_env[j],cd.max_env[j],cd.mean_env[j],
        cd.ttu[j],cd.ttl[j],cd.spec_lo,cd.spec_hi
      ].map(esc);
      if(withExcluded) row.push(isIncluded?'true':'false');
      rows.push(row.join(','));
    });
  });
  /* Metadata block */
  var ts=new Date().toISOString().replace('T',' ').replace(/\.\d+Z$/,' UTC');
  var allCondNames=ENV_DATA.map(function(cd){return cd.condition;});
  var excCondNames=allCondNames.filter(function(c){return selCondNames.indexOf(c)<0;});
  var frLo=isFinite(fr.lo)?fr.lo.toFixed(2):'min';
  var frHi=isFinite(fr.hi)?fr.hi.toFixed(2):'max';
  var meta=['# PADB Export','# Plot: '+ENV_TITLE,'# Generated: '+ts,
    '# Export: '+(withExcluded?'Filtered + excluded (excluded conditions flagged in Included column)':'Filtered data'),
    '# Freq range: '+frLo+' - '+frHi+' MHz',
    '# Active conditions ('+selCondNames.length+'): '+selCondNames.join(', '),
    '# Excluded conditions ('+excCondNames.length+'): '+(excCondNames.length?excCondNames.join(', '):'None'),
    '#'
  ].join('\r\n');
  var blob=new Blob([meta+'\r\n'+rows.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  var a=document.createElement('a');
  var suffix=withExcluded?'_with_excl':'_filtered';
  a.href=URL.createObjectURL(blob);
  a.download=ENV_TITLE.replace(/[^a-z0-9_\-]/gi,'_')+suffix+'.csv';
  a.click();
}
function toggleEnvStatPanel(){
  var el=document.getElementById('env_stat_panel');
  var btn=document.getElementById('env_stat_btn');
  if(!el||!btn) return;
  if(el.style.display==='none'||el.style.display===''){
    el.style.display='block';
    btn.textContent='▼ Statistics';
    updateEnvStatsTable(getSelectedConds());
    el.scrollIntoView({behavior:'smooth',block:'nearest'});
  } else {
    el.style.display='none';
    btn.textContent='▶ Statistics';
  }
}
function fmt(v,d){return v===null||v===undefined?'—':v.toFixed(d!==undefined?d:4);}
function updateEnvStatsTable(selConds){
  var el=document.getElementById('env_stat_panel');
  if(!el||el.style.display==='none') return;
  try{
    var fr=getFreqRange();
    var hdrs=['Condition','Freq (MHz)','UDE','LDE','Min(Env.)','Max(Env.)','Mean(Env.)',
              'TTL↑','TTL↓','Spec Lo','Spec Hi'];
    var hrow='<tr>'+hdrs.map(function(h){return '<th>'+h+'</th>';}).join('')+'</tr>';
    var rows=[];
    var flt=getEnvDataFilter();
    selConds.forEach(function(cd){
      getFilteredIdxs(cd,fr,flt).forEach(function(j){
        var f=cd.freqs[j];
        var ttu=cd.ttu[j],ttl=cd.ttl[j];
        var sh=cd.spec_hi,sl=cd.spec_lo;
        var fail=(sh!==null&&ttu!==null&&ttu>sh)||(sl!==null&&ttl!==null&&ttl<sl);
        var cls=fail?'ttl-fail':'';
        rows.push('<tr class="'+cls+'">'
          +'<td class="lbl">'+cd.condition+'</td>'
          +'<td>'+f.toFixed(4)+'</td>'
          +'<td>'+fmt(cd.ude[j])+'</td>'
          +'<td>'+fmt(cd.lde[j])+'</td>'
          +'<td>'+fmt(cd.min_env[j])+'</td>'
          +'<td>'+fmt(cd.max_env[j])+'</td>'
          +'<td>'+fmt(cd.mean_env[j])+'</td>'
          +'<td>'+fmt(ttu)+'</td>'
          +'<td>'+fmt(ttl)+'</td>'
          +'<td>'+fmt(sl)+'</td>'
          +'<td>'+fmt(sh)+'</td>'
          +'</tr>');
      });
    });
    el.innerHTML='<table class="env-tbl"><thead>'+hrow+'</thead><tbody>'+rows.join('')+'</tbody></table>';
  }catch(e){
    el.innerHTML='<div style="color:red;padding:8px">Error building table: '+e.message+'</div>';
  }
}
function update(){
  var selConds=getSelectedConds();
  var showExcl=document.getElementById('show_excl_chk');
  showExcl=showExcl?showExcl.checked:false;
  var exclConds=showExcl?ENV_DATA.filter(function(cd){return selConds.indexOf(cd)<0;}):[];
  Plotly.react('plot',buildTraces(selConds,exclConds),buildLayout());
  updateEnvStatsTable(selConds);
}
Plotly.newPlot('plot',buildTraces(getSelectedConds(),[]),buildLayout(),{responsive:true,scrollZoom:true});
"""


# ---------------------------------------------------------------------------
# env_coverage — interactive env-delta TI plot built from raw scatter data
# ---------------------------------------------------------------------------

_ENV_COVERAGE_JS = r"""
function isLogX(){return document.getElementById('ec_log_x_chk').checked;}
function getSelectedConds(){
  return ENV_DATA.filter(function(cd){
    return COND_DIMS.every(function(dim){
      var all=document.querySelectorAll('.'+dim.col_id);
      if(!all.length) return true;
      var chks=Array.from(all).filter(function(c){return c.checked;}).map(function(c){return c.value;});
      if(!chks.length) return false;
      var safe=dim.col.replace(/[-\/\\^$*+?.()|[\]{}]/g,'\\$&');
      var m=cd.condition.match(new RegExp(safe+':\\s*(.+?)(?=\\s{2,}|$)'));
      return !m||chks.indexOf(m[1].trim())>=0;
    });
  });
}
function getFreqRange(){
  var loTxt=document.getElementById('ec_freq_lo_txt'),hiTxt=document.getElementById('ec_freq_hi_txt');
  var lo=loTxt&&loTxt.value!==''?parseFloat(loTxt.value):parseFloat(document.getElementById('ec_freq_lo').value);
  var hi=hiTxt&&hiTxt.value!==''?parseFloat(hiTxt.value):parseFloat(document.getElementById('ec_freq_hi').value);
  return {lo:isNaN(lo)?-Infinity:lo,hi:isNaN(hi)?Infinity:hi};
}
function _getNovr(id){var el=document.getElementById(id);if(!el||el.value==='') return 0;var v=parseInt(el.value)||0;return v>1?v:0;}
function nOvrStep(id,dir){
  var el=document.getElementById(id);if(!el) return;
  var v=parseInt(el.value)||0;v=Math.max(0,v+dir);
  el.value=v>1?String(v):'';update();
}
function getParams(){
  function _numOrNull(id){var el=document.getElementById(id);if(!el||el.value==='') return null;var v=parseFloat(el.value);return isNaN(v)?null:v;}
  return {
    P_room:parseFloat(document.getElementById('ec_P_room').value)||0.90,
    C_room:parseFloat(document.getElementById('ec_C_room').value)||0.90,
    P_env:parseFloat(document.getElementById('ec_P_env').value)||0.90,
    C_env:parseFloat(document.getElementById('ec_C_env').value)||0.90,
    n_room_ovr:_getNovr('ec_n_room'),
    n_env_ovr:_getNovr('ec_n_env'),
    mu:parseFloat(document.getElementById('ec_mu').value)||0,
    spec_hi_ovr:_numOrNull('ec_spec_hi'),
    spec_lo_ovr:_numOrNull('ec_spec_lo'),
  };
}
function kLookup(n,P,C){
  if(!KT||!KT.P) return 2.0;
  var Pv=KT.P,Cv=KT.C,nv=KT.n;
  var pi=0;for(var i=1;i<Pv.length;i++){if(Math.abs(Pv[i]-P)<Math.abs(Pv[pi]-P))pi=i;}
  var ci=0;for(var i=1;i<Cv.length;i++){if(Math.abs(Cv[i]-C)<Math.abs(Cv[ci]-C))ci=i;}
  var key=KT.P[pi]+'_'+KT.C[ci],arr=KT.k[key];
  if(!arr||!arr.length) return 2.0;
  if(n<=nv[0]) return arr[0];
  if(n>=nv[nv.length-1]) return arr[arr.length-1];
  for(var i=0;i<nv.length-1;i++){
    if(n>=nv[i]&&n<=nv[i+1]){var t=(n-nv[i])/(nv[i+1]-nv[i]);return arr[i]+t*(arr[i+1]-arr[i]);}
  }
  return arr[arr.length-1];
}
function getSelectedTemps(){
  var chks=document.querySelectorAll('.ec_temp_chk');
  if(!chks.length) return ALL_TEMPS.slice();
  return Array.from(chks).filter(function(c){return c.checked;}).map(function(c){return c.value;});
}
/* ---- serial filter ---- */
function getAllSerials(){return Array.from(document.querySelectorAll('.ec_ser_chk')).map(function(c){return c.value;});}
function getSelectedSerials(){return Array.from(document.querySelectorAll('.ec_ser_chk:checked')).map(function(c){return c.value;});}
function serChkChanged(){
  var all=document.querySelectorAll('.ec_ser_chk');
  var chk=Array.from(all).filter(function(c){return c.checked;}).length;
  var allEl=document.getElementById('all_ec_ser_panel');
  if(allEl){allEl.checked=chk===all.length;allEl.indeterminate=chk>0&&chk<all.length;}
  var b=document.getElementById('badge_ec_ser_panel');
  if(b){b.textContent=chk<all.length?chk+'/'+all.length:'';b.classList.toggle('active',chk<all.length);}
  update();
}
function toggleAllSer(){
  var allEl=document.getElementById('all_ec_ser_panel');
  document.querySelectorAll('.ec_ser_chk').forEach(function(c){c.checked=allEl?allEl.checked:true;});
  serChkChanged();
}
/* ---- port filter ---- */
function getAllPorts(){return Array.from(document.querySelectorAll('.ec_port_chk')).map(function(c){return c.value;});}
function getSelectedPorts(){return Array.from(document.querySelectorAll('.ec_port_chk:checked')).map(function(c){return c.value;});}
function portChkChanged(){
  var all=document.querySelectorAll('.ec_port_chk');
  var chk=Array.from(all).filter(function(c){return c.checked;}).length;
  var allEl=document.getElementById('all_ec_port_panel');
  if(allEl){allEl.checked=chk===all.length;allEl.indeterminate=chk>0&&chk<all.length;}
  var b=document.getElementById('badge_ec_port_panel');
  if(b){b.textContent=chk<all.length?chk+'/'+all.length:'';b.classList.toggle('active',chk<all.length);}
  update();
}
function toggleAllPort(){
  var allEl=document.getElementById('all_ec_port_panel');
  document.querySelectorAll('.ec_port_chk').forEach(function(c){c.checked=allEl?allEl.checked:true;});
  portChkChanged();
}
/* ---- global filter (localStorage) ---- */
var _gfExcluded=null;
var _gfCoarseExcluded=null;
var _gfFocusMode=false;
var _ecGfEnabled=true;
function toggleEcGf(){_ecGfEnabled=!_ecGfEnabled;_updateEcGfBadge();update();}
function _loadEcGlobalFilter(){
  try{
    var raw=localStorage.getItem('padb_v2_excluded');
    if(!raw){_gfExcluded=null;_gfCoarseExcluded=null;}
    else{
      var obj=JSON.parse(raw);
      _gfExcluded=new Set(obj.excluded||[]);
      _gfCoarseExcluded=new Set();
      _gfExcluded.forEach(function(k){
        var parts=k.split('||');
        if(parts.length>=2) _gfCoarseExcluded.add(parts[0]+'||'+parts[1]);
      });
    }
  }catch(e){_gfExcluded=null;_gfCoarseExcluded=null;}
  _gfFocusMode=(localStorage.getItem('padb_v2_gf_mode')||'exclude')==='focus';
  _updateEcGfBadge();
}
function _updateEcGfBadge(){
  var el=document.getElementById('ec_gf_badge');
  var btn=document.getElementById('ec_gf_toggle_btn');
  if(!el) return;
  var dutSers=new Set();
  if(_gfExcluded) _gfExcluded.forEach(function(k){dutSers.add(k.split('||')[0]);});
  var n=dutSers.size,pts=_gfExcluded?_gfExcluded.size:0;
  if(n>0){
    var suffix=_ecGfEnabled?'':' [OFF]';
    el.textContent=(_gfFocusMode?'Inspect: ':'')+pts+' pts in GF ('+n+' DUT'+(n!==1?'s':')')+(_gfFocusMode?' — INSPECT':'')+suffix;
    el.style.background=_ecGfEnabled?(_gfFocusMode?'#e8f0ff':'#ffeaea'):'#f5f5f5';
    el.style.color=_ecGfEnabled?(_gfFocusMode?'#0044aa':'#900'):'#888';
    el.style.borderColor=_ecGfEnabled?(_gfFocusMode?'#6688cc':'#c88'):'#ccc';
    el.style.display='';
  }else{el.textContent='';el.style.display='none';}
  if(btn){
    btn.textContent='GF: '+(_ecGfEnabled?'ON':'OFF');
    btn.style.background=_ecGfEnabled?'#ffe8e8':'#f5f5f5';
    btn.style.borderColor=_ecGfEnabled?'#c88':'#ccc';
    btn.style.color=_ecGfEnabled?'#900':'#666';
  }
}
window.addEventListener('storage',function(e){
  if(e.key==='padb_v2_excluded'||e.key==='padb_v2_gf_mode'){_loadEcGlobalFilter();update();}
});
function _isEcGfExcl(serial,gfKey){
  if(!_gfCoarseExcluded||!_gfCoarseExcluded.size) return false;
  /* Exact match: serial||gfKey must be in the set.
     Both _make_ec_gf_key (Python) and _boxFullCondKey (JS) produce sorted keys,
     so exact matching is safe and avoids false positives from old/partial GF entries. */
  return _gfCoarseExcluded.has((serial||'unknown')+'||'+gfKey);
}
/* ---- aggregate per-DUT stats for one condition ---- */
function _vecMean(vals){if(!vals.length) return 0;var s=0;vals.forEach(function(v){s+=v;});return s/vals.length;}
function _vecStd(vals){
  if(vals.length<2) return 0;
  var m=_vecMean(vals),ss=0;vals.forEach(function(v){ss+=(v-m)*(v-m);});
  return Math.sqrt(ss/(vals.length-1));
}
function getActiveDuts(cd){
  var selSers=getSelectedSerials();var allSers=getAllSerials();
  var selPorts=getSelectedPorts();var allPorts=getAllPorts();
  var serFlt=allSers.length>1&&selSers.length<allSers.length;
  var portFlt=allPorts.length>1&&selPorts.length<allPorts.length;
  var hasGf=_ecGfEnabled&&_gfCoarseExcluded&&_gfCoarseExcluded.size>0;
  var result=[];
  Object.keys(cd.duts).forEach(function(dutKey){
    var dut=cd.duts[dutKey];
    var baseSer=dut.serial||dutKey;
    if(serFlt&&selSers.indexOf(baseSer)<0) return;
    if(portFlt&&selPorts.indexOf(dut.port||'')<0) return;
    if(hasGf){
      var excl=_isEcGfExcl(baseSer,dut.gf_key);
      if(_gfFocusMode?!excl:excl) return;
    }
    result.push([dutKey,dut]);
  });
  return result;
}
/* Delta DUTs: serial+GF filter only — port filter excluded so delta TI uses the full
   port population, matching the room TI policy (allDuts ignores port filter too). */
function getDeltaDuts(cd){
  var selSers=getSelectedSerials();var allSers=getAllSerials();
  var serFlt=allSers.length>1&&selSers.length<allSers.length;
  var hasGf=_ecGfEnabled&&_gfCoarseExcluded&&_gfCoarseExcluded.size>0;
  var result=[];
  Object.keys(cd.duts).forEach(function(dutKey){
    var dut=cd.duts[dutKey];
    var baseSer=dut.serial||dutKey;
    if(serFlt&&selSers.indexOf(baseSer)<0) return;
    if(hasGf){
      var excl=_isEcGfExcl(baseSer,dut.gf_key);
      if(_gfFocusMode?!excl:excl) return;
    }
    result.push([dutKey,dut]);
  });
  return result;
}
function hexAlpha(hex,a){
  var r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
  return 'rgba('+r+','+g+','+b+','+a+')';
}
function computeStats(cd,params,fr,selTemps){
  var activeDuts=getActiveDuts(cd);
  /* Room TI and delta TI both use port-filter-agnostic DUT lists so that selecting a
     single port does not collapse n below the TI threshold. Room uses allDuts; delta uses
     getDeltaDuts (serial+GF only, no port filter). activeDuts (full filter) is reserved for
     future per-port display. */
  var deltaDuts=getDeltaDuts(cd);
  var allDuts=Object.keys(cd.duts).map(function(k){return [k,cd.duts[k]];});
  var freqs=[],room_lo=[],room_hi=[],ude=[],lde=[],room_means=[],room_ns=[],delta_ns=[];
  for(var j=0;j<cd.freqs.length;j++){
    var f=cd.freqs[j];
    if(f<fr.lo||f>fr.hi) continue;
    freqs.push(f);
    var roomVals=[];
    allDuts.forEach(function(sd){var v=sd[1].room[j];if(v!==null&&v!==undefined)roomVals.push(v);});
    var n_r=roomVals.length;
    room_means.push(n_r?_vecMean(roomVals):null);
    room_ns.push(n_r);
    if(n_r>=2){
      var sig_r=_vecStd(roomVals);
      var n_r_eff=params.n_room_ovr>0?params.n_room_ovr:n_r;
      var k_r=kLookup(n_r_eff,params.P_room,params.C_room);
      room_lo.push(-(k_r*sig_r));room_hi.push(k_r*sig_r);
    }else{room_lo.push(null);room_hi.push(null);}
    var u_j=null,l_j=null,max_ne=0;
    selTemps.forEach(function(temp){
      var dVals=[];
      deltaDuts.forEach(function(sd){
        var dt=sd[1].deltas[temp];
        if(!dt) return;
        var v=dt[j];if(v!==null&&v!==undefined)dVals.push(v);
      });
      var n_e=dVals.length;if(n_e>max_ne) max_ne=n_e;if(n_e<2) return;
      var mu_e=_vecMean(dVals),sig_e=_vecStd(dVals);
      var n_e_eff=params.n_env_ovr>0?params.n_env_ovr:n_e;
      var k_e=kLookup(n_e_eff,params.P_env,params.C_env);
      var u=mu_e+k_e*sig_e,l=mu_e-k_e*sig_e;
      if(u_j===null||u>u_j) u_j=u;
      var ld=Math.max(0,-l);if(l_j===null||ld>l_j) l_j=ld;
    });
    ude.push(u_j);lde.push(l_j);delta_ns.push(max_ne);
  }
  var eff_hi=(params.spec_hi_ovr!==null&&params.spec_hi_ovr!==undefined)?params.spec_hi_ovr:cd.spec_hi;
  var eff_lo=(params.spec_lo_ovr!==null&&params.spec_lo_ovr!==undefined)?params.spec_lo_ovr:cd.spec_lo;
  var ttu=ude.map(function(u){return (u!==null&&eff_hi!==null)?eff_hi-u-params.mu:null;});
  var ttl=lde.map(function(l){return (l!==null&&eff_lo!==null)?eff_lo+l+params.mu:null;});
  return {freqs:freqs,room_lo:room_lo,room_hi:room_hi,ude:ude,lde:lde,ttu:ttu,ttl:ttl,
          room_means:room_means,room_ns:room_ns,delta_ns:delta_ns};
}
function buildTraces(selConds,exclConds){
  exclConds=exclConds||[];
  var params=getParams();var fr=getFreqRange();var selTemps=getSelectedTemps();
  var traces=[];
  /* Collect y values from UDE/LDE/room data only (not TTU/TTL) to compute axis range.
     TTU/TTL may be in absolute spec units (e.g. dBm) while the data is in delta (dB),
     so excluding them prevents the axis from being dominated by distant spec values. */
  var yVals=[];
  exclConds.forEach(function(cd){
    var st=computeStats(cd,params,fr,selTemps);
    if(!st.ude.some(function(v){return v!==null;})) return;
    var neg_lde=st.lde.map(function(v){return v!==null?-v:null;});
    traces.push({type:'scatter',x:st.freqs,y:st.ude,mode:'lines',
      line:{color:'rgba(190,190,190,0.5)',width:0.8},showlegend:false,hoverinfo:'skip'});
    traces.push({type:'scatter',x:st.freqs,y:neg_lde,mode:'lines',
      fill:'tonexty',fillcolor:'rgba(200,200,200,0.07)',
      line:{color:'rgba(190,190,190,0.5)',width:0.8},
      name:cd.condition,showlegend:false,hoverinfo:'skip'});
  });
  selConds.forEach(function(cd,ci){
    var color=PALETTE[ci%PALETTE.length];
    var st=computeStats(cd,params,fr,selTemps);
    if(!st.freqs.length) return;
    /* Accumulate range-relevant y values */
    st.ude.forEach(function(v){if(v!==null)yVals.push(v);});
    st.lde.forEach(function(v){if(v!==null)yVals.push(-v);});
    st.room_hi.forEach(function(v){if(v!==null)yVals.push(v);});
    st.room_lo.forEach(function(v){if(v!==null)yVals.push(v);});
    var hov=st.freqs.map(function(f,k){
      var lines=[cd.condition,'Freq: '+f.toFixed(4)+' MHz'];
      if(st.ude[k]!==null) lines.push('UDE: '+st.ude[k].toFixed(4));
      if(st.lde[k]!==null) lines.push('LDE: '+st.lde[k].toFixed(4));
      if(st.ttu[k]!==null) lines.push('TTU: '+st.ttu[k].toFixed(4));
      if(st.ttl[k]!==null) lines.push('TTL: '+st.ttl[k].toFixed(4));
      if(st.room_means[k]!==null) lines.push('Roomμ: '+st.room_means[k].toFixed(4)+' (n='+st.room_ns[k]+')');
      return lines.join('<br>');
    });
    if(st.room_hi.some(function(v){return v!==null;})){
      traces.push({type:'scatter',x:st.freqs,y:st.room_hi,mode:'lines',
        line:{color:color,width:0.8,dash:'dash'},
        name:cd.condition+' Room↑',legendgroup:cd.condition,showlegend:false,hoverinfo:'skip'});
      traces.push({type:'scatter',x:st.freqs,y:st.room_lo,mode:'lines',
        fill:'tonexty',fillcolor:hexAlpha(color,0.12),
        line:{color:color,width:0.8,dash:'dash'},
        name:cd.condition+' Room TI',legendgroup:cd.condition,showlegend:true,hoverinfo:'skip'});
    }
    var neg_lde=st.lde.map(function(v){return v!==null?-v:null;});
    if(st.ude.some(function(v){return v!==null;})){
      traces.push({type:'scatter',x:st.freqs,y:st.ude,mode:'lines',
        line:{color:color,width:2},
        name:cd.condition+' UDE',legendgroup:cd.condition,showlegend:false,
        text:hov,hovertemplate:'%{text}<extra></extra>'});
      traces.push({type:'scatter',x:st.freqs,y:neg_lde,mode:'lines',
        fill:'tonexty',fillcolor:hexAlpha(color,0.22),
        line:{color:color,width:2},
        name:cd.condition,legendgroup:cd.condition,showlegend:true,
        text:hov,hovertemplate:'%{text}<extra></extra>'});
    }
    if(st.ttu.some(function(v){return v!==null;})){
      traces.push({type:'scatter',x:st.freqs,y:st.ttu,mode:'lines',
        line:{color:color,dash:'dot',width:1.5},
        name:cd.condition+' TTU',legendgroup:cd.condition,showlegend:false,hoverinfo:'skip'});
    }
    if(st.ttl.some(function(v){return v!==null;})){
      traces.push({type:'scatter',x:st.freqs,y:st.ttl,mode:'lines',
        line:{color:color,dash:'dot',width:1.5},
        name:cd.condition+' TTL',legendgroup:cd.condition,showlegend:false,hoverinfo:'skip'});
    }
  });
  var yRange=null;
  if(EC_Y_LIM){yRange=EC_Y_LIM;}
  else if(yVals.length){
    var yMin=Math.min.apply(null,yVals),yMax=Math.max.apply(null,yVals);
    var pad=Math.max(Math.abs(yMax-yMin)*0.15,0.1);
    yRange=[yMin-pad,yMax+pad];
  }
  return {traces:traces,yRange:yRange};
}
function buildLayout(yRange){
  return {
    title:{text:EC_TITLE,x:0.5,font:{size:15}},
    template:'plotly_white',
    xaxis:{title:'Frequency (MHz)',type:isLogX()?'log':'linear'},
    yaxis:{title:EC_Y_LABEL,range:yRange||null},
    height:480,
    legend:{bgcolor:'rgba(255,255,255,0.85)',bordercolor:'#ccc',borderwidth:1},
    margin:{l:60,r:30,t:55,b:60},
    shapes:[{type:'line',x0:0,x1:1,xref:'paper',y0:0,y1:0,
      line:{color:'#bbb',width:1,dash:'dot'}}]
  };
}
function closeAllFilterPanels(){
  document.querySelectorAll('.filter-panel').forEach(function(p){p.style.display='none';});
  var bd=document.getElementById('filter-backdrop');
  if(bd) bd.style.display='none';
}
function togglePanel(col){
  var el=document.getElementById('panel_'+col);
  if(!el) return;
  var open=el.style.display==='block';
  closeAllFilterPanels();
  if(!open){el.style.display='block';var bd=document.getElementById('filter-backdrop');if(bd)bd.style.display='block';}
}
function toggleAll(col){
  var allEl=document.getElementById('all_'+col);
  document.querySelectorAll('.'+col).forEach(function(c){c.checked=allEl?allEl.checked:true;});
  updateBadge(col);update();
}
function chkChanged(col){updateBadge(col);update();}
function updateBadge(col){
  var all=document.querySelectorAll('.'+col);
  var chk=Array.from(all).filter(function(c){return c.checked;}).length;
  var allEl=document.getElementById('all_'+col);
  if(allEl){allEl.checked=chk===all.length;allEl.indeterminate=chk>0&&chk<all.length;}
  var b=document.getElementById('badge_'+col);
  if(b){b.textContent=chk<all.length?chk+'/'+all.length:'';b.classList.toggle('active',chk<all.length);}
}
function syncFreq(){
  var lo=parseFloat(document.getElementById('ec_freq_lo').value);
  var hi=parseFloat(document.getElementById('ec_freq_hi').value);
  var lv=document.getElementById('ec_freq_lo_txt');var hv=document.getElementById('ec_freq_hi_txt');
  if(lv) lv.value=lo.toFixed(3);if(hv) hv.value=hi.toFixed(3);
  update();
}
function freqTxtChange(which){
  var txt=document.getElementById('ec_freq_'+which+'_txt');
  var slider=document.getElementById('ec_freq_'+which);
  var v=parseFloat(txt.value);
  if(isNaN(v)){txt.value=parseFloat(slider.value).toFixed(3);return;}
  v=Math.max(parseFloat(slider.min),Math.min(parseFloat(slider.max),v));
  if(which==='lo'){var h=parseFloat(document.getElementById('ec_freq_hi').value);if(v>h)v=h;}
  else{var l=parseFloat(document.getElementById('ec_freq_lo').value);if(v<l)v=l;}
  txt.value=v.toFixed(3);slider.value=v;update();
}
function freqStep(which,dir){
  var fv=EC_FREQ_VALS,txt=document.getElementById('ec_freq_'+which+'_txt'),slider=document.getElementById('ec_freq_'+which);
  var cur=parseFloat(txt.value),idx=-1;
  for(var i=0;i<fv.length;i++){if(Math.abs(fv[i]-cur)<0.0001){idx=i;break;}}
  if(idx<0){var best=0;for(var i=1;i<fv.length;i++){if(Math.abs(fv[i]-cur)<Math.abs(fv[best]-cur))best=i;}idx=best;}
  var ni=Math.max(0,Math.min(fv.length-1,idx+dir)),nv=fv[ni];
  if(which==='lo'&&nv>parseFloat(document.getElementById('ec_freq_hi').value))return;
  if(which==='hi'&&nv<parseFloat(document.getElementById('ec_freq_lo').value))return;
  txt.value=nv.toFixed(3);slider.value=nv;update();
}
function freqKeyDown(e,which){
  if(e.key==='Enter')freqTxtChange(which);
  else if(e.key==='ArrowUp'){e.preventDefault();freqStep(which,1);}
  else if(e.key==='ArrowDown'){e.preventDefault();freqStep(which,-1);}
}
function fmt(v,d){return v===null||v===undefined?'—':v.toFixed(d!==undefined?d:4);}
function updateStatsTable(selConds){
  var el=document.getElementById('ec_stat_panel');
  if(!el||el.style.display==='none') return;
  var params=getParams();var fr=getFreqRange();var selTemps=getSelectedTemps();
  var hdrs=['Condition','Freq (MHz)','UDE','LDE','TTU','TTL','Room μ','Room n','ΔEnv n'];
  var hrow='<tr>'+hdrs.map(function(h){return '<th>'+h+'</th>';}).join('')+'</tr>';
  var rows=[];
  selConds.forEach(function(cd){
    var st=computeStats(cd,params,fr,selTemps);
    st.freqs.forEach(function(f,k){
      var fail=(cd.spec_hi!==null&&st.ttu[k]!==null&&st.ttu[k]>cd.spec_hi)||
               (cd.spec_lo!==null&&st.ttl[k]!==null&&st.ttl[k]<cd.spec_lo);
      var cls=fail?' class="ec-fail"':'';
      var dn=st.delta_ns[k];
      var dnCell=dn<2?'<td style="color:#c00;font-weight:bold">'+dn+'</td>':'<td>'+dn+'</td>';
      rows.push('<tr'+cls+'>'
        +'<td class="lbl">'+cd.condition+'</td>'
        +'<td>'+f.toFixed(4)+'</td>'
        +'<td>'+fmt(st.ude[k])+'</td>'
        +'<td>'+fmt(st.lde[k])+'</td>'
        +'<td>'+fmt(st.ttu[k])+'</td>'
        +'<td>'+fmt(st.ttl[k])+'</td>'
        +'<td>'+fmt(st.room_means[k])+'</td>'
        +'<td>'+st.room_ns[k]+'</td>'
        +dnCell
        +'</tr>');
    });
  });
  el.innerHTML='<table class="ec-tbl"><thead>'+hrow+'</thead><tbody>'+rows.join('')+'</tbody></table>';
}
function toggleStatsPanel(){
  var el=document.getElementById('ec_stat_panel');
  var btn=document.getElementById('ec_stat_btn');
  if(!el||!btn) return;
  if(el.style.display==='none'||el.style.display===''){
    el.style.display='block';btn.textContent='▼ Statistics';
    updateStatsTable(getSelectedConds());
    el.scrollIntoView({behavior:'smooth',block:'nearest'});
  }else{el.style.display='none';btn.textContent='▶ Statistics';}
}
function saveCSV(){
  var selConds=getSelectedConds();
  var params=getParams();var fr=getFreqRange();var selTemps=getSelectedTemps();
  var hdrs=['Condition','Freq_MHz','UDE','LDE','TTU','TTL','Room_mean','Room_n','DeltaEnv_n','Spec_hi','Spec_lo'];
  var rows=[hdrs.join(',')];
  function esc(v){var s=String(v==null?'':v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;}
  selConds.forEach(function(cd){
    var st=computeStats(cd,params,fr,selTemps);
    st.freqs.forEach(function(f,k){
      rows.push([esc(cd.condition),f,st.ude[k],st.lde[k],st.ttu[k],st.ttl[k],
        st.room_means[k],st.room_ns[k],st.delta_ns[k],cd.spec_hi,cd.spec_lo].map(esc).join(','));
    });
  });
  var ts=new Date().toISOString().replace('T',' ').replace(/\.\d+Z$/,' UTC');
  var selSers=getSelectedSerials(),allSers=getAllSerials();
  var meta=['# PADB Export','# Plot: '+EC_TITLE,'# Generated: '+ts,
    '# P_room: '+params.P_room+' C_room: '+params.C_room,
    '# P_env: '+params.P_env+' C_env: '+params.C_env,
    '# Temps: '+selTemps.join(', '),
    '# Serials ('+selSers.length+'/'+allSers.length+'): '+selSers.join(', '),'#'].join('\r\n');
  var blob=new Blob([meta+'\r\n'+rows.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);a.download=EC_TITLE.replace(/[^a-z0-9_\-]/gi,'_')+'.csv';a.click();
}
function updateSummaryBar(selConds){
  var el=document.getElementById('ec_summary_bar');
  if(!el) return;
  var params=getParams();var fr=getFreqRange();var selTemps=getSelectedTemps();
  var maxUde=null,maxLde=null,minTtu=null,minTtl=null;
  selConds.forEach(function(cd){
    var st=computeStats(cd,params,fr,selTemps);
    st.ude.forEach(function(v){if(v!==null&&(maxUde===null||v>maxUde))maxUde=v;});
    st.lde.forEach(function(v){if(v!==null&&(maxLde===null||v>maxLde))maxLde=v;});
    st.ttu.forEach(function(v){if(v!==null&&(minTtu===null||v<minTtu))minTtu=v;});
    st.ttl.forEach(function(v){if(v!==null&&(minTtl===null||v<minTtl))minTtl=v;});
  });
  var parts=[];
  parts.push('<span class="smry-item"><span class="smry-lbl">Max UDE:</span>'
    +'<span class="smry-val">'+(maxUde!==null?maxUde.toFixed(4):'—')+'</span></span>');
  parts.push('<span class="smry-item"><span class="smry-lbl">Max LDE:</span>'
    +'<span class="smry-val">'+(maxLde!==null?maxLde.toFixed(4):'—')+'</span></span>');
  if(minTtu!==null) parts.push('<span class="smry-item"><span class="smry-lbl">Min TTU:</span>'
    +'<span class="smry-val">'+minTtu.toFixed(4)+'</span></span>');
  if(minTtl!==null) parts.push('<span class="smry-item"><span class="smry-lbl">Min TTL:</span>'
    +'<span class="smry-val">'+minTtl.toFixed(4)+'</span></span>');
  el.innerHTML=parts.join('');
}
function update(){
  var selConds=getSelectedConds();
  var showExcl=document.getElementById('ec_show_excl');
  showExcl=showExcl?showExcl.checked:false;
  var exclConds=showExcl?ENV_DATA.filter(function(cd){return selConds.indexOf(cd)<0;}):[];
  var _r=buildTraces(selConds,exclConds);
  Plotly.react('plot',_r.traces,buildLayout(_r.yRange));
  updateStatsTable(selConds);
  updateSummaryBar(selConds);
  saveState();
}

/* ---- localStorage state persistence ---- */
function _stGet(k){try{return localStorage.getItem(STATE_KEY+k);}catch(e){return null;}}
function _stSet(k,v){try{localStorage.setItem(STATE_KEY+k,v);}catch(e){}}
function _stClear(){try{var keys=[];for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);if(k&&k.indexOf(STATE_KEY)===0)keys.push(k);}keys.forEach(function(k){localStorage.removeItem(k);});}catch(e){}}
function saveState(){
  _stSet('freq_lo',document.getElementById('ec_freq_lo')?document.getElementById('ec_freq_lo').value:'');
  _stSet('freq_hi',document.getElementById('ec_freq_hi')?document.getElementById('ec_freq_hi').value:'');
  document.querySelectorAll('.ec_temp_chk').forEach(function(c){_stSet('temp_'+c.value,c.checked?'1':'0');});
  if(typeof COND_DIMS!=='undefined') COND_DIMS.forEach(function(dim){
    document.querySelectorAll('.'+dim.col_id).forEach(function(c){_stSet('cond_cond_'+dim.col_id+'_'+encodeURIComponent(c.value),c.checked?'1':'0');});
  });
  var fltEl=document.querySelector('input[name="env_dfilt"]:checked');if(fltEl)_stSet('env_filter_mode',fltEl.value);
  var yhiEl=document.getElementById('env_y_hi');if(yhiEl)_stSet('env_filter_yhi',yhiEl.value);
  var tllEl=document.getElementById('env_tll_hi');if(tllEl)_stSet('env_tll_hi',tllEl.value);
  var pREl=document.getElementById('ec_P_room');if(pREl)_stSet('ec_P_room',pREl.value);
  var cREl=document.getElementById('ec_C_room');if(cREl)_stSet('ec_C_room',cREl.value);
  var pEEl=document.getElementById('ec_P_env');if(pEEl)_stSet('ec_P_env',pEEl.value);
  var cEEl=document.getElementById('ec_C_env');if(cEEl)_stSet('ec_C_env',cEEl.value);
  var muEl=document.getElementById('ec_mu');if(muEl)_stSet('ec_mu',muEl.value);
  var shEl=document.getElementById('ec_spec_hi');if(shEl)_stSet('ec_spec_hi',shEl.value);
  var slEl=document.getElementById('ec_spec_lo');if(slEl)_stSet('ec_spec_lo',slEl.value);
}
function loadState(){
  var lo=_stGet('freq_lo'),hi=_stGet('freq_hi');
  if(lo!==null){var sl=document.getElementById('ec_freq_lo');if(sl){sl.value=lo;var tx=document.getElementById('ec_freq_lo_txt');if(tx)tx.value=parseFloat(lo).toFixed(3);}}
  if(hi!==null){var sh=document.getElementById('ec_freq_hi');if(sh){sh.value=hi;var th=document.getElementById('ec_freq_hi_txt');if(th)th.value=parseFloat(hi).toFixed(3);}}
  document.querySelectorAll('.ec_temp_chk').forEach(function(c){var s=_stGet('temp_'+c.value);if(s!==null&&!c.disabled)c.checked=(s==='1');});
  if(typeof COND_DIMS!=='undefined') COND_DIMS.forEach(function(dim){
    document.querySelectorAll('.'+dim.col_id).forEach(function(c){var s=_stGet('cond_cond_'+dim.col_id+'_'+encodeURIComponent(c.value));if(s!==null)c.checked=(s==='1');});
    var chks=Array.from(document.querySelectorAll('.'+dim.col_id));
    var allChk=document.getElementById('all_'+dim.col_id);
    if(allChk){var n=chks.filter(function(c){return c.checked;}).length;allChk.checked=(n===chks.length);allChk.indeterminate=(n>0&&n<chks.length);}
  });
  var fm=_stGet('env_filter_mode');
  if(fm){var fr=document.querySelector('input[name="env_dfilt"][value="'+fm+'"]');if(fr)fr.checked=true;}
  var fyhi=_stGet('env_filter_yhi');var fyhiEl=document.getElementById('env_y_hi');if(fyhi!==null&&fyhiEl)fyhiEl.value=fyhi;
  var tll=_stGet('env_tll_hi');var tllEl=document.getElementById('env_tll_hi');if(tll!==null&&tllEl)tllEl.value=tll;
  var pR=_stGet('ec_P_room');if(pR!==null){var pREl=document.getElementById('ec_P_room');if(pREl)pREl.value=pR;var lpREl=document.getElementById('lbl_ec_P_room');if(lpREl)lpREl.value=pR;}
  var cR=_stGet('ec_C_room');if(cR!==null){var cREl=document.getElementById('ec_C_room');if(cREl)cREl.value=cR;var lcREl=document.getElementById('lbl_ec_C_room');if(lcREl)lcREl.value=cR;}
  var pE=_stGet('ec_P_env');if(pE!==null){var pEEl=document.getElementById('ec_P_env');if(pEEl)pEEl.value=pE;var lpEEl=document.getElementById('lbl_ec_P_env');if(lpEEl)lpEEl.value=pE;}
  var cE=_stGet('ec_C_env');if(cE!==null){var cEEl=document.getElementById('ec_C_env');if(cEEl)cEEl.value=cE;var lcEEl=document.getElementById('lbl_ec_C_env');if(lcEEl)lcEEl.value=cE;}
  var mu=_stGet('ec_mu');if(mu!==null){var muEl=document.getElementById('ec_mu');if(muEl)muEl.value=mu;}
  var sh=_stGet('ec_spec_hi');if(sh!==null){var shEl=document.getElementById('ec_spec_hi');if(shEl)shEl.value=sh;}
  var sl=_stGet('ec_spec_lo');if(sl!==null){var slEl=document.getElementById('ec_spec_lo');if(slEl)slEl.value=sl;}
}

_loadEcGlobalFilter();
loadState();
var _ir=buildTraces(getSelectedConds(),[]);
Plotly.newPlot('plot',_ir.traces,buildLayout(_ir.yRange),{responsive:true,scrollZoom:true});
updateSummaryBar(getSelectedConds());
"""


def _make_ec_gf_key(group_str: str) -> str:
    """Convert a full Group label to a GF condition key (matches _condKeyForStat format)."""
    _serial_kws = ("serial", "unit id", "dut id", "s/n")
    parts = re.split(r"  +", group_str.strip())
    filtered = []
    for p in parts:
        key_part = p.split(":")[0].strip().lower()
        if any(kw in key_part for kw in _serial_kws):
            continue
        kv = re.sub(r"\s*:\s*", "=", p.strip())
        filtered.append(kv)
    return "|".join(sorted(filtered))


def _aggregate_env_coverage_data(df: pd.DataFrame, cfg: dict) -> tuple:
    """
    Build ENV_DATA for the interactive env_coverage plot from raw scatter data.

    Stores per-DUT raw values (room measurements and paired deltas) so JS can
    recompute UDE/LDE interactively when P/C sliders, serial filter, or GF change.

    Returns (env_data, cond_dims, non_room_temps, all_serials).
    """
    import numpy as _np

    room_values = cfg.get("room_values", ["Room"])

    _serial_kws  = ("serial", "unit id", "dut id", "s/n")
    _port_kws    = ("port",)
    grp_cols = [c for c in df.columns if c.startswith("_grp_")]
    serial_cols = {
        c for c in grp_cols
        if any(kw in c.removeprefix("_grp_").lower() for kw in _serial_kws)
    }
    # Port-like columns are pooled across (not split into separate conditions)
    port_cols = {
        c for c in grp_cols
        if c not in serial_cols
        and any(kw in c.removeprefix("_grp_").lower() for kw in _port_kws)
    }
    cond_cols = [
        c for c in grp_cols
        if c not in serial_cols and c not in port_cols
        and 1 < df[c].nunique(dropna=True) <= 50
    ]

    # Find the serial column (for DUT labels)
    ser_col = next(
        (c for c in serial_cols if "serial" in c.removeprefix("_grp_").lower()),
        None
    )
    # Find the port column (for DUT sub-keying so RF1/RF2 are separate data points)
    port_col = next(
        (c for c in port_cols if "port" in c.removeprefix("_grp_").lower()),
        None
    )

    def _super_label(row):
        parts = [
            f"{c.removeprefix('_grp_')}: {row[c]}"
            for c in cond_cols if pd.notna(row[c])
        ]
        return "  ".join(parts) if parts else "All"

    df = df.copy()
    df["_super_cond"] = df.apply(_super_label, axis=1)

    cond_dims: list = []
    for col in cond_cols:
        label = col.removeprefix("_grp_")
        col_id = re.sub(r"\W+", "_", label)
        vals = sorted(str(v) for v in df[col].dropna().unique() if str(v).strip())
        if len(vals) > 1:
            cond_dims.append({"col": label, "col_id": col_id, "label": label, "vals": vals})

    all_temps = sorted(str(t) for t in df["Temperature"].dropna().unique())
    non_room_temps = [t for t in all_temps if t not in room_values]

    def _safe(v):
        return None if (v is None or (isinstance(v, float) and _np.isnan(v))) else round(float(v), 6)

    all_serials_set: set = set()
    all_ports_set: set = set()
    results: list = []

    for sc, sc_df in df.groupby("_super_cond", sort=True):
        all_freqs = sorted(float(f) for f in sc_df["Frequency_MHz"].dropna().unique())
        n_f = len(all_freqs)
        freq_idx = {f: i for i, f in enumerate(all_freqs)}

        # Room data pivot: Group → Frequency → Value
        room_df = sc_df[sc_df["Temperature"].isin(room_values)]
        if len(room_df):
            room_pivot = room_df.pivot_table(
                index="Group", columns="Frequency_MHz", values="Value", aggfunc="first"
            )
        else:
            room_pivot = pd.DataFrame()

        # Temp data pivots
        temp_pivots: dict = {}
        for temp in non_room_temps:
            tdf = sc_df[sc_df["Temperature"] == temp]
            if len(tdf):
                temp_pivots[temp] = tdf.pivot_table(
                    index="Group", columns="Frequency_MHz", values="Value", aggfunc="first"
                )

        # Build per-DUT data
        duts: dict = {}
        groups_in_sc = sc_df["Group"].dropna().unique()

        for grp in groups_in_sc:
            # Extract serial label (from _grp_Serial Number column or Group string)
            grp_rows = sc_df[sc_df["Group"] == grp]
            if ser_col and ser_col in grp_rows.columns:
                ser_vals = grp_rows[ser_col].dropna().unique()
                serial = str(ser_vals[0]) if len(ser_vals) else str(grp)
            else:
                # Parse from Group string
                m = re.search(r'Serial Number:\s*(\S+)', str(grp), re.IGNORECASE)
                serial = m.group(1) if m else str(grp)

            all_serials_set.add(serial)
            gf_key = _make_ec_gf_key(str(grp))

            # Extract port value so RF1/RF2 are separate data points in same population
            port_val = None
            if port_col and port_col in grp_rows.columns:
                pvals = grp_rows[port_col].dropna().unique()
                if len(pvals):
                    port_val = str(pvals[0])
            dut_key = f"{serial}_{port_val}" if port_val else serial
            if port_val:
                all_ports_set.add(port_val)

            # Room values per freq
            room_vals = [None] * n_f
            if len(room_pivot) and grp in room_pivot.index:
                for f, idx in freq_idx.items():
                    if f in room_pivot.columns:
                        v = room_pivot.at[grp, f]
                        room_vals[idx] = _safe(v)

            # Delta values per temp per freq
            dut_deltas: dict = {}
            for temp, tp in temp_pivots.items():
                if grp not in tp.index or grp not in room_pivot.index:
                    continue
                d_vals = [None] * n_f
                for f, idx in freq_idx.items():
                    if f in tp.columns and f in room_pivot.columns:
                        rv = room_pivot.at[grp, f] if grp in room_pivot.index else None
                        tv = tp.at[grp, f]
                        if (rv is not None and not (isinstance(rv, float) and _np.isnan(rv)) and
                                tv is not None and not (isinstance(tv, float) and _np.isnan(tv))):
                            d_vals[idx] = _safe(float(tv) - float(rv))
                if any(v is not None for v in d_vals):
                    dut_deltas[temp] = d_vals

            duts[dut_key] = {"serial": serial, "port": port_val, "gf_key": gf_key, "room": room_vals, "deltas": dut_deltas}

        hi_vals = sc_df["Upper_Limit"].dropna()
        lo_vals = sc_df["Lower_Limit"].dropna()
        spec_hi = _safe(float(hi_vals.mode().iloc[0])) if len(hi_vals) else None
        spec_lo = _safe(float(lo_vals.mode().iloc[0])) if len(lo_vals) else None

        cond_keys_dict: dict = {}
        for col in cond_cols:
            label = col.removeprefix("_grp_")
            uv = sc_df[col].dropna().unique()
            cond_keys_dict[label] = str(uv[0]) if len(uv) == 1 else ""

        results.append({
            "condition":  sc,
            "cond_keys":  cond_keys_dict,
            "freqs":      [round(f, 6) for f in all_freqs],
            "spec_hi":    spec_hi,
            "spec_lo":    spec_lo,
            "duts":       duts,
        })

    all_serials = sorted(all_serials_set)
    all_ports = sorted(all_ports_set)
    return results, cond_dims, non_room_temps, all_serials, all_ports


def _build_env_coverage_html(
    env_data: list,
    cond_dims: list,
    title: str,
    y_label: str,
    y_lim,
    log_x: bool,
    freq_min: float,
    freq_max: float,
    palette: list,
    freq_vals: list,
    k_table: dict,
    non_room_temps: list,
    all_serials: list = None,
    all_ports: list = None,
    default_P: float = 0.90,
    default_C: float = 0.90,
    results_dir: str = '',
) -> str:
    css = (
        "body{font-family:Arial,sans-serif;margin:0;padding:8px;background:#fafafa;}"
        ".ctrl-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:8px 14px;background:#f0f2f5;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".ctrl-bar label{white-space:nowrap;}"
        ".ctrl-bar input[type=range]{vertical-align:middle;width:90px;}"
        ".pc-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:5px 14px;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".pc-bar.room-bar{background:#edf5ff;}"
        ".pc-bar.env-bar{background:#edf7ee;}"
        ".pc-bar label{white-space:nowrap;}"
        ".pc-bar input[type=range]{vertical-align:middle;width:90px;}"
        ".temp-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:5px 14px;background:#f5f5e8;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".temp-bar label{white-space:nowrap;cursor:pointer;}"
        ".sep{border-left:2px solid #ccc;height:22px;margin:0 2px;}"
        ".filter-wrap{position:relative;display:inline-block;}"
        ".filter-btn{font-size:13px;padding:3px 10px;border:1px solid #bbb;border-radius:3px;"
        "cursor:pointer;background:#fff;white-space:nowrap;}"
        ".filter-btn:hover{background:#e8e8e8;}"
        ".filter-panel{display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:200;"
        "background:#fff;border:1px solid #ccc;border-radius:4px;"
        "box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:140px;max-height:280px;"
        "overflow-y:auto;padding:6px 8px;}"
        ".filter-panel.open{display:block;}"
        "#filter-backdrop{position:fixed;inset:0;z-index:150;display:none;}"
        ".fitem{display:block;padding:2px 0;cursor:pointer;white-space:nowrap;font-size:13px;}"
        ".fall{padding-bottom:2px;}"
        ".fdiv{margin:4px 0;border:none;border-top:1px solid #eee;}"
        ".badge{font-size:11px;background:#0066cc;color:#fff;border-radius:10px;"
        "padding:1px 6px;margin-right:2px;display:none;}"
        ".badge.active{display:inline;}"
        ".csv-btn{font-size:13px;padding:3px 12px;border:1px solid #0066cc;border-radius:3px;"
        "cursor:pointer;background:#e8f4ff;color:#0066cc;margin-left:6px;}"
        ".csv-btn:hover{background:#cce4ff;}"
        ".gf-toggle-btn{font-size:13px;padding:3px 10px;border:1px solid #c88;border-radius:3px;"
        "cursor:pointer;background:#ffe8e8;color:#900;margin-left:4px;}"
        ".gf-toggle-btn:hover{filter:brightness(0.94);}"
        ".stat-btn{font-size:13px;padding:3px 12px;border:1px solid #666;border-radius:3px;"
        "cursor:pointer;background:#f5f5f5;}"
        ".stat-btn:hover{background:#e0e0e0;}"
        "#ec_stat_panel{margin-top:6px;overflow:auto;max-height:400px;padding:0 4px;}"
        ".ec-tbl{border-collapse:collapse;font-size:12px;width:100%;}"
        ".ec-tbl th{background:#e8ecf0;text-align:center;padding:4px 8px;"
        "border:1px solid #ccc;white-space:nowrap;position:sticky;top:0;z-index:1;}"
        ".ec-tbl td{padding:3px 8px;border:1px solid #e0e0e0;text-align:right;white-space:nowrap;}"
        ".ec-tbl td.lbl{text-align:left;font-weight:bold;max-width:260px;"
        "overflow:hidden;text-overflow:ellipsis;}"
        ".ec-tbl tr:nth-child(even) td{background:#f7f9fc;}"
        ".ec-tbl tr.ec-fail td{background:#ffd0d0 !important;}"
        "input.freq-txt{font-size:12px;width:72px;padding:1px 3px;border:1px solid #bbb;"
        "border-radius:3px;text-align:right;margin-left:2px;}"
        ".footnote{font-size:11px;color:#888;padding:2px 14px;}"
        "#ec_summary_bar{font-size:12px;padding:4px 14px;background:#f0f2f5;border-radius:4px;"
        "margin-bottom:4px;display:flex;flex-wrap:wrap;gap:20px;align-items:center;}"
        "#ec_summary_bar .smry-item{white-space:nowrap;}"
        "#ec_summary_bar .smry-lbl{font-weight:bold;color:#555;margin-right:3px;}"
        "#ec_summary_bar .smry-val{font-family:monospace;color:#222;}"
    )

    panels: list = []
    for dim in cond_dims:
        col_id = dim["col_id"]
        items = "".join(
            f'<label class="fitem"><input type="checkbox" class="{col_id}"'
            f' value="{v}" checked onchange="chkChanged(\'{col_id}\')">&nbsp;{v}</label>'
            for v in dim["vals"]
        )
        panels.append(
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'{col_id}\')">'
            f'<span id="badge_{col_id}" class="badge"></span>{dim["label"]}&thinsp;&#9662;</button>'
            f'<div class="filter-panel" id="panel_{col_id}">'
            f'<label class="fitem fall"><input type="checkbox" id="all_{col_id}"'
            f' checked onchange="toggleAll(\'{col_id}\')"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{items}</div></div>'
        )
    panels_html = "\n  ".join(panels)

    if all_serials and len(all_serials) > 1:
        ser_items = "".join(
            f'<label class="fitem"><input type="checkbox" class="ec_ser_chk" value="{s}"'
            f' checked onchange="serChkChanged()">&nbsp;{s}</label>'
            for s in all_serials
        )
        serial_panel_html = (
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'ec_ser_panel\')">'
            f'Serial&thinsp;<span id="badge_ec_ser_panel" class="badge"></span>&#9662;</button>'
            f'<div class="filter-panel" id="panel_ec_ser_panel">'
            f'<label class="fitem fall"><input type="checkbox" id="all_ec_ser_panel"'
            f' checked onchange="toggleAllSer()"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{ser_items}</div></div>'
        )
    else:
        serial_panel_html = ""

    if all_ports and len(all_ports) > 1:
        port_items = "".join(
            f'<label class="fitem"><input type="checkbox" class="ec_port_chk" value="{p}"'
            f' checked onchange="portChkChanged()">&nbsp;{p}</label>'
            for p in all_ports
        )
        port_panel_html = (
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'ec_port_panel\')">'
            f'Port&thinsp;<span id="badge_ec_port_panel" class="badge"></span>&#9662;</button>'
            f'<div class="filter-panel" id="panel_ec_port_panel">'
            f'<label class="fitem fall"><input type="checkbox" id="all_ec_port_panel"'
            f' checked onchange="toggleAllPort()"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{port_items}</div></div>'
        )
    else:
        port_panel_html = ""

    gf_badge_html = (
        '<span id="ec_gf_badge" style="display:none;font-size:11px;background:#ffeaea;'
        'border:1px solid #c88;border-radius:3px;padding:1px 7px;color:#900;'
        'margin-left:4px"></span>'
    )

    freq_step = max(round((freq_max - freq_min) / 1000, 4), 0.001)
    sep = '<div class="sep"></div>'

    freq_lo_html = (
        f'<label>Freq&nbsp;min:<input type="range" id="ec_freq_lo"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_min:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<input class="freq-txt" id="ec_freq_lo_txt" type="text" value="{freq_min:.3f}"'
        f' onchange="freqTxtChange(\'lo\')" onkeydown="freqKeyDown(event,\'lo\')">&nbsp;MHz</label>'
    )
    freq_hi_html = (
        f'<label>Freq&nbsp;max:<input type="range" id="ec_freq_hi"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_max:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<input class="freq-txt" id="ec_freq_hi_txt" type="text" value="{freq_max:.3f}"'
        f' onchange="freqTxtChange(\'hi\')" onkeydown="freqKeyDown(event,\'hi\')">&nbsp;MHz</label>'
    )
    log_x_html = (
        f'<label><input type="checkbox" id="ec_log_x_chk"'
        + (' checked' if log_x else '')
        + ' onchange="update()"> Log&nbsp;X</label>'
    )

    _all_panels = []
    if serial_panel_html:
        _all_panels.append(serial_panel_html)
    if port_panel_html:
        _all_panels.append(port_panel_html)
    if panels_html:
        _all_panels.append(panels_html)
    _combined_panels_html = "\n  ".join(_all_panels)

    ctrl_bar = (
        '<div class="ctrl-bar">\n'
        + (f'  {_combined_panels_html}\n  {sep}\n' if _combined_panels_html else '')
        + f'  {freq_lo_html}\n'
        + f'  {freq_hi_html}\n'
        + f'  {sep}\n'
        + f'  {log_x_html}\n'
        + f'  {sep}\n'
        + f'  <label title="Show non-selected conditions as dim gray bands">'
        + f'<input type="checkbox" id="ec_show_excl" onchange="update()">'
        + f'&nbsp;Show&nbsp;excluded</label>\n'
        + f'  <button class="csv-btn" onclick="saveCSV()">&#8595;&nbsp;CSV</button>\n'
        + f'  <button class="stat-btn" id="ec_stat_btn" onclick="toggleStatsPanel()">&#9658;&nbsp;Statistics</button>\n'
        + f'  <button class="gf-toggle-btn" id="ec_gf_toggle_btn" onclick="toggleEcGf()">GF:&nbsp;ON</button>\n'
        + f'  {gf_badge_html}\n'
        + '</div>\n'
    )

    def _slider(el_id, label, default_val, on_change, title=""):
        t = f' title="{title}"' if title else ""
        sn = min([0.80, 0.95, 0.9973], key=lambda x: abs(x - default_val))
        return (
            f'<label{t}>{label}&nbsp;'
            f'<select id="{el_id}" onchange="update()">'
            f'<option value="0.80"{"  selected" if abs(sn-0.80)<0.001 else ""}>80%</option>'
            f'<option value="0.95"{"  selected" if abs(sn-0.95)<0.001 else ""}>95%</option>'
            f'<option value="0.9973"{"  selected" if abs(sn-0.9973)<0.001 else ""}>99.73%</option>'
            f'</select></label>'
        )

    def _c_slider(el_id, label, default_val, on_change, title=""):
        t = f' title="{title}"' if title else ""
        sn = min([0.90, 0.95], key=lambda x: abs(x - default_val))
        return (
            f'<label{t}>{label}&nbsp;'
            f'<select id="{el_id}" onchange="update()">'
            f'<option value="0.90"{"  selected" if abs(sn-0.90)<0.001 else ""}>90%</option>'
            f'<option value="0.95"{"  selected" if abs(sn-0.95)<0.001 else ""}>95%</option>'
            f'</select></label>'
        )

    def _update_lbl(el_id, digits):
        return f"document.getElementById('lbl_{el_id}').value=parseFloat(this.value).toFixed({digits});update()"

    def _n_ovr(el_id, title=""):
        t = f' title="{title}"' if title else ""
        _step_style = "font-size:11px;padding:0 4px;border:1px solid #bbb;border-radius:3px;margin-left:1px;cursor:pointer;line-height:1.4"
        return (
            f'<span{t} style="white-space:nowrap">n&nbsp;ovr:&nbsp;'
            f'<input type="text" id="{el_id}" value="" placeholder="auto"'
            f' style="width:44px;text-align:right;font-size:12px;border:1px solid #bbb;'
            f'border-radius:3px;padding:1px 3px" oninput="update()">'
            f'<button style="{_step_style}" onclick="nOvrStep(\'{el_id}\',1)">&#9650;</button>'
            f'<button style="{_step_style}" onclick="nOvrStep(\'{el_id}\',-1)">&#9660;</button>'
            f'</span>'
        )

    room_bar = (
        '<div class="pc-bar room-bar">\n'
        f'  <b>Room:</b>\n'
        f'  {_slider("ec_P_room", "P", default_P, _update_lbl("ec_P_room", 4), "Proportion for Room TI")}\n'
        f'  {_c_slider("ec_C_room", "C", default_C, _update_lbl("ec_C_room", 2), "Confidence for Room TI")}\n'
        f'  {_n_ovr("ec_n_room", "Override n used for k-factor lookup (extrapolation to larger population)")}\n'
        '</div>\n'
    )

    env_bar = (
        '<div class="pc-bar env-bar">\n'
        f'  <b>&#916;Env:</b>\n'
        f'  {_slider("ec_P_env", "P", default_P, _update_lbl("ec_P_env", 4), "Proportion for ΔEnv TI")}\n'
        f'  {_c_slider("ec_C_env", "C", default_C, _update_lbl("ec_C_env", 2), "Confidence for ΔEnv TI")}\n'
        f'  {_n_ovr("ec_n_env", "Override n used for k-factor lookup (extrapolation to larger population)")}\n'
        f'  <label title="Measurement Uncertainty (dB) — subtracted from spec limits to give TTU/TTL">'
        f'M.U.:&nbsp;<input type="number" id="ec_mu" value="0" min="0" step="0.001"'
        f' style="width:58px" oninput="update()"></label>\n'
        f'  <label title="Upper spec limit override — required when CSV has no spec limits (enables TTU line)">'
        f'Spec&nbsp;hi:&nbsp;<input type="number" id="ec_spec_hi" placeholder="auto"'
        f' step="0.001" style="width:70px" oninput="update()"></label>\n'
        f'  <label title="Lower spec limit override — required when CSV has no spec limits (enables TTL line)">'
        f'Spec&nbsp;lo:&nbsp;<input type="number" id="ec_spec_lo" placeholder="auto"'
        f' step="0.001" style="width:70px" oninput="update()"></label>\n'
        '</div>\n'
    )

    if non_room_temps:
        temp_items = "".join(
            f'<label><input type="checkbox" class="ec_temp_chk" value="{t}"'
            f' checked onchange="update()">&nbsp;{t}</label>\n'
            for t in non_room_temps
        )
        temp_bar = (
            '<div class="temp-bar">\n'
            '  <b>&#916;Env&nbsp;temps:</b>\n  '
            + temp_items
            + '  <small style="color:#666">(uncheck to exclude a temp from UDE/LDE computation)</small>\n'
            '</div>\n'
        )
    else:
        temp_bar = ""

    footnote = (
        '<div class="footnote">'
        'Dashed filled band: Room TI &nbsp;|&nbsp; '
        'Solid filled band: &#916;Env combined [&minus;LDE,&nbsp;+UDE] &nbsp;|&nbsp; '
        'Dotted line: TTU/TTL (= Spec &minus; UDE &minus; M.U. / Spec + LDE + M.U.)'
        '</div>\n'
    )

    _freq_vals_js = json.dumps(sorted(set(freq_vals))) if freq_vals else "[]"
    constants = "\n".join([
        f"var ENV_DATA={json.dumps(env_data)};",
        f"var KT={json.dumps(k_table)};",
        f"var EC_TITLE={json.dumps(title)};",
        f"var EC_Y_LABEL={json.dumps(y_label)};",
        f"var EC_Y_LIM={json.dumps(y_lim)};",
        f"var EC_FREQ_MIN={freq_min!r};",
        f"var EC_FREQ_MAX={freq_max!r};",
        f"var EC_FREQ_VALS={_freq_vals_js};",
        f"var COND_DIMS={json.dumps(cond_dims)};",
        f"var PALETTE={json.dumps(palette)};",
        f"var ALL_TEMPS={json.dumps(non_room_temps)};",
        f"var EC_ALL_SERIALS={json.dumps(all_serials or [])};",
        f"var EC_ALL_PORTS={json.dumps(all_ports or [])};",
        f"var STATE_KEY='padb_{results_dir}';",
    ])

    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f"<style>{css}</style>\n"
        f"<script>{_get_plotlyjs()}</script>\n"
        "</head>\n<body>\n"
        '<div id="filter-backdrop" onclick="closeAllFilterPanels()"></div>\n'
        + ctrl_bar
        + room_bar
        + env_bar
        + temp_bar
        + footnote
        + '<div id="ec_summary_bar"></div>\n'
        + '<div id="plot"></div>\n'
        + '<div id="ec_stat_panel" style="display:none"></div>\n'
        + f"<script>\n{constants}\n{_ENV_COVERAGE_JS}</script>\n"
        "</body>\n</html>"
    )


def _load_env_csv(csv_path: Path) -> pd.DataFrame:
    """Load a PADB Environmental (Type=60) summary CSV into a DataFrame."""
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    _INT_MAX = 2_000_000_000
    num_cols = [
        "X value", "Min (Env.)", "Max (Env.)", "mean (Env.)", "variation",
        "UDE", "LDE", "Meas. Unc.", "Min (Std.)", "Max (Std.)",
        "Upper TTL (est)", "Lower TTL (est)", "Std. Deviation", "mean (Sum.)",
        "UDE (Max)", "LDE (Max)", "Lower Limit", "Upper Limit",
    ]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("UDE", "LDE", "UDE (Max)", "LDE (Max)"):
        if col in df.columns:
            df.loc[df[col].abs() > _INT_MAX, col] = np.nan
    return df


def _aggregate_env_data(df: pd.DataFrame) -> list:
    """Convert environmental CSV rows into per-condition frequency series for JS."""
    results: list = []
    for group, gdf in df.groupby("Group", sort=False):
        gdf = gdf.sort_values("X value").reset_index(drop=True)

        def _col(col: str) -> list:
            if col not in gdf.columns:
                return [None] * len(gdf)
            return [None if (v is None or (isinstance(v, float) and np.isnan(v)))
                    else round(float(v), 6) for v in gdf[col]]

        def _first(col: str):
            if col not in gdf.columns:
                return None
            v = gdf[col].dropna()
            return round(float(v.iloc[0]), 6) if len(v) else None

        results.append({
            "condition": str(group),
            "freqs":    [round(float(v), 6) for v in gdf["X value"]],
            "ude":      _col("UDE"),
            "lde":      _col("LDE"),
            "min_env":  _col("Min (Env.)"),
            "max_env":  _col("Max (Env.)"),
            "mean_env": _col("mean (Env.)"),
            "min_std":  _col("Min (Std.)"),
            "max_std":  _col("Max (Std.)"),
            "ttu":      _col("Upper TTL (est)"),
            "ttl":      _col("Lower TTL (est)"),
            "spec_lo":  _first("Lower Limit"),
            "spec_hi":  _first("Upper Limit"),
            "ude_max":  _first("UDE (Max)"),
            "lde_max":  _first("LDE (Max)"),
            "units":    str(gdf["Units"].iloc[0]) if "Units" in gdf.columns and len(gdf) else "",
        })
    return results


def _build_env_summary_html(
    env_data: list,
    cond_dims: list,
    title: str,
    y_label: str,
    y_lim,
    log_x: bool,
    freq_min: float,
    freq_max: float,
    palette: list,
    freq_vals: list = None,
    p_std=None,
    p_env=None,
    c_std=None,
    c_env=None,
) -> str:
    css = (
        "body{font-family:Arial,sans-serif;margin:0;padding:8px;background:#fafafa;}"
        ".ctrl-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:8px 14px;background:#f0f2f5;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".ctrl-bar label{white-space:nowrap;}"
        ".ctrl-bar input[type=range]{vertical-align:middle;width:90px;}"
        ".sep{border-left:2px solid #ccc;height:22px;margin:0 2px;}"
        ".filter-wrap{position:relative;display:inline-block;}"
        ".filter-btn{font-size:13px;padding:3px 10px;border:1px solid #bbb;border-radius:3px;"
        "cursor:pointer;background:#fff;white-space:nowrap;}"
        ".filter-btn:hover{background:#e8e8e8;}"
        ".filter-panel{display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:200;"
        "background:#fff;border:1px solid #ccc;border-radius:4px;"
        "box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:140px;max-height:280px;"
        "overflow-y:auto;padding:6px 8px;}"
        ".filter-panel.open{display:block;}"
        "#filter-backdrop{position:fixed;inset:0;z-index:150;display:none;}"
        ".fitem{display:block;padding:2px 0;cursor:pointer;white-space:nowrap;font-size:13px;}"
        ".fall{padding-bottom:2px;}"
        ".fdiv{margin:4px 0;border:none;border-top:1px solid #eee;}"
        ".badge{font-size:11px;background:#0066cc;color:#fff;border-radius:10px;"
        "padding:1px 6px;margin-right:2px;display:none;}"
        ".badge.active{display:inline;}"
        ".csv-btn{font-size:13px;padding:3px 12px;border:1px solid #0066cc;border-radius:3px;"
        "cursor:pointer;background:#e8f4ff;color:#0066cc;margin-left:6px;"
        "position:relative;z-index:201;}"
        ".csv-btn:hover{background:#cce4ff;}"
        + _CSV_DROPDOWN_CSS +
        ".footnote{font-size:11px;color:#888;padding:2px 14px;}"
        ".stat-btn{font-size:13px;padding:3px 12px;border:1px solid #666;border-radius:3px;"
        "cursor:pointer;background:#f5f5f5;position:relative;z-index:201;}"
        ".stat-btn:hover{background:#e0e0e0;}"
        "#env_stat_panel{margin-top:6px;overflow:auto;max-height:400px;padding:0 4px;}"
        ".env-tbl{border-collapse:collapse;font-size:12px;width:100%;}"
        ".env-tbl th{background:#e8ecf0;text-align:center;padding:4px 8px;"
        "border:1px solid #ccc;white-space:nowrap;position:sticky;top:0;z-index:1;}"
        ".env-tbl td{padding:3px 8px;border:1px solid #e0e0e0;text-align:right;white-space:nowrap;}"
        ".env-tbl td.lbl{text-align:left;font-weight:bold;max-width:280px;"
        "overflow:hidden;text-overflow:ellipsis;}"
        ".env-tbl tr:nth-child(even) td{background:#f7f9fc;}"
        ".env-tbl tr.ttl-fail td{background:#ffd0d0 !important;}"
        ".env-yin{width:68px;font-size:12px;padding:1px 3px;}"
        ".pc-lbl{font-size:11px;color:#555;white-space:nowrap;}"
        ".pc-lbl b{color:#333;}"
        "input.freq-txt{font-size:12px;width:72px;padding:1px 3px;border:1px solid #bbb;"
        "border-radius:3px;text-align:right;margin-left:2px;}"
    )

    # Condition filter panels
    panels: list[str] = []
    for dim in cond_dims:
        col_id = dim["col_id"]
        items = "".join(
            f'<label class="fitem"><input type="checkbox" class="{col_id}"'
            f' value="{v}" checked onchange="chkChanged(\'{col_id}\')">&nbsp;{v}</label>'
            for v in dim["vals"]
        )
        panels.append(
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'{col_id}\')">'
            f'<span id="badge_{col_id}" class="badge"></span>{dim["label"]}&thinsp;&#9662;</button>'
            f'<div class="filter-panel" id="panel_{col_id}">'
            f'<label class="fitem fall"><input type="checkbox" id="all_{col_id}"'
            f' checked onchange="toggleAll(\'{col_id}\')"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{items}</div></div>'
        )
    panels_html = "\n  ".join(panels)

    freq_step = max(round((freq_max - freq_min) / 1000, 4), 0.001)
    sep = '<div class="sep"></div>'
    freq_lo_html = (
        f'<label>Freq&nbsp;min:<input type="range" id="env_freq_lo"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_min:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<input class="freq-txt" id="env_freq_lo_txt" type="text" value="{freq_min:.3f}"'
        f' onchange="freqTxtChange(\'lo\')" onkeydown="freqKeyDown(event,\'lo\')">&nbsp;MHz</label>'
    )
    freq_hi_html = (
        f'<label>Freq&nbsp;max:<input type="range" id="env_freq_hi"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_max:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<input class="freq-txt" id="env_freq_hi_txt" type="text" value="{freq_max:.3f}"'
        f' onchange="freqTxtChange(\'hi\')" onkeydown="freqKeyDown(event,\'hi\')">&nbsp;MHz</label>'
    )
    log_x_html = (
        f'<label><input type="checkbox" id="env_log_x_chk"'
        + (' checked' if log_x else '')
        + ' onchange="update()"> Log&nbsp;X</label>'
    )

    data_filter_html = (
        '<span style="position:relative;z-index:201;">'
        '<label><input type="radio" name="env_dfilt" value="all" checked'
        ' onchange="toggleEnvYRange()">&nbsp;All&nbsp;data</label>'
        '&nbsp;'
        '<label><input type="radio" name="env_dfilt" value="passing"'
        ' onchange="toggleEnvYRange()">&nbsp;Passing&nbsp;only</label>'
        '&nbsp;'
        '<label><input type="radio" name="env_dfilt" value="yrange"'
        ' onchange="toggleEnvYRange()">&nbsp;Upper&nbsp;limit</label>'
        '<span id="env_y_range_inputs" style="display:none;gap:4px;align-items:center">'
        '&nbsp;<label><input type="number" id="env_y_hi" class="env-yin"'
        ' placeholder="dBc limit" oninput="update()"></label>'
        '&nbsp;<small style="color:#666">hides freqs where UDE exceeds limit</small>'
        '</span>'
        '&nbsp;&nbsp;<label title="Override TLL for Passing only filter and draw TLL line">'
        'TLL&nbsp;override:<input type="number" id="env_tll_hi" step="0.001" placeholder="auto"'
        ' style="width:74px" oninput="update()"></label>'
        '</span>'
    )
    pc_parts = []
    if p_std is not None or c_std is not None:
        std_str = "/".join(filter(None, [
            f"P{int(p_std)}" if p_std is not None else None,
            f"C{int(c_std)}" if c_std is not None else None,
        ]))
        pc_parts.append(f'<span class="pc-lbl">Std:&nbsp;<b>{std_str}</b></span>')
    if p_env is not None or c_env is not None:
        env_str = "/".join(filter(None, [
            f"P{int(p_env)}" if p_env is not None else None,
            f"C{int(c_env)}" if c_env is not None else None,
        ]))
        pc_parts.append(f'<span class="pc-lbl">Env:&nbsp;<b>{env_str}</b></span>')
    pc_html = ("  " + "&nbsp;&nbsp;".join(pc_parts) + "\n") if pc_parts else ""

    ctrl_bar = (
        '<div class="ctrl-bar">\n'
        + (f'  {panels_html}\n  {sep}\n' if panels_html else '')
        + f'  {freq_lo_html}\n'
        + f'  {freq_hi_html}\n'
        + f'  {sep}\n'
        + f'  {log_x_html}\n'
        + f'  {sep}\n'
        + f'  {data_filter_html}\n'
        + f'  <label title="Show non-selected conditions as dim gray bands">'
        + f'<input type="checkbox" id="show_excl_chk" onchange="update()">'
        + f'&nbsp;Show&nbsp;excluded</label>\n'
        + f'  {_csv_btn("saveCSV")}\n'
        + f'  <button class="stat-btn" id="env_stat_btn" onclick="toggleEnvStatPanel()">&#9658;&nbsp;Statistics</button>\n'
        + (f'  {sep}\n{pc_html}' if pc_html else '')
        + '</div>\n'
    )

    # UDE_max footnote
    ude_max_vals = [(cd["condition"], cd["ude_max"]) for cd in env_data if cd["ude_max"] is not None]
    if ude_max_vals:
        worst = max(ude_max_vals, key=lambda x: x[1])
        footnote = (
            f'<div class="footnote">Shaded band: environmental contribution [&minus;LDE, +UDE]'
            f' &nbsp;|&nbsp; Dotted lines: estimated TTL'
            f' &nbsp;|&nbsp; Peak UDE: {worst[1]:.4f} ({worst[0]})</div>\n'
        )
    else:
        footnote = (
            '<div class="footnote">Shaded band: environmental contribution'
            ' [&minus;LDE, +UDE] &nbsp;|&nbsp; Dotted lines: estimated TTL</div>\n'
        )

    _freq_vals_js = json.dumps(sorted(set(freq_vals))) if freq_vals else "[]"
    constants = "\n".join([
        f"var ENV_DATA={json.dumps(env_data)};",
        f"var ENV_TITLE={json.dumps(title)};",
        f"var ENV_Y_LABEL={json.dumps(y_label)};",
        f"var ENV_Y_LIM={json.dumps(y_lim)};",
        f"var ENV_FREQ_MIN={freq_min!r};",
        f"var ENV_FREQ_MAX={freq_max!r};",
        f"var ENV_FREQ_VALS={_freq_vals_js};",
        f"var COND_DIMS={json.dumps(cond_dims)};",
        f"var PALETTE={json.dumps(palette)};",
    ])

    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f"<style>{css}</style>\n"
        f"<script>{_get_plotlyjs()}</script>\n"
        "</head>\n<body>\n"
        '<div id="filter-backdrop" onclick="closeAllFilterPanels()"></div>\n'
        + ctrl_bar + footnote
        + '<div id="plot"></div>\n'
        + '<div id="env_stat_panel" style="display:none"></div>\n'
        + f"<script>\n{constants}\n{_ENV_SUMMARY_JS}</script>\n"
        "</body>\n</html>"
    )


def de_summary(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """
    Interactive environmental delta plot from a PADB Type=60 CSV.

    Shows the UDE/LDE environmental contribution band per frequency per condition,
    with optional TTL lines and spec limits. Supports group filtering, freq range
    sliders, log X, and CSV export.
    """
    df = _load_env_csv(csv_path)
    env_data = _aggregate_env_data(df)
    title = cfg.get("title", output_html.stem)
    y_label = cfg.get("y_label", "Delta (dB)")
    y_lim = cfg.get("y_lim")

    all_freqs = sorted(set(f for cd in env_data for f in cd["freqs"] if f is not None))
    freq_min = float(min(all_freqs)) if all_freqs else 0.0
    freq_max = float(max(all_freqs)) if all_freqs else 1.0

    log_x_cfg = cfg.get("log_x")
    log_x = bool(log_x_cfg) if log_x_cfg is not None else (freq_min > 0 and freq_max / freq_min >= 100)

    # Build COND_DIMS — same serial-excluding logic as other plots
    unique_groups = df["Group"].dropna().unique()
    group_kv = {g: _parse_group_kv(g) for g in unique_groups}
    all_keys = {k for kv in group_kv.values() for k in kv}
    _serial_val = re.compile(r"^[A-Z]{2,3}\d{5,}$")
    _serial_kws = ("serial", "unit id", "dut id", "s/n")
    cond_keys: list = []
    for key in sorted(all_keys):
        if any(kw in key.lower() for kw in _serial_kws):
            continue
        vals = {kv.get(key, "") for kv in group_kv.values() if key in kv}
        if vals and sum(_serial_val.match(v) is not None for v in vals) / len(vals) > 0.5:
            continue
        if 1 < len(vals) <= 50:
            cond_keys.append(key)

    def _sort_numeric(vals):
        try:
            return sorted(vals, key=float)
        except (ValueError, TypeError):
            return sorted(vals)

    dim_vals: dict = {}
    for g in unique_groups:
        kv = group_kv.get(g, {})
        for k in cond_keys:
            if k in kv:
                dim_vals.setdefault(k, set()).add(kv[k])

    cond_dims = [
        {"col": key, "col_id": re.sub(r"\W+", "_", key), "label": key, "vals": _sort_numeric(v)}
        for key, v in sorted(dim_vals.items()) if len(v) > 1
    ]

    # Add serial filter — extracted from condition strings (excluded by default serial-detection logic)
    _ser_re_sum = re.compile(r'Serial Number:\s*(\S+)', re.IGNORECASE)
    _ser_vals_sum = sorted(set(
        m.group(1) for cd in env_data for m in [_ser_re_sum.search(cd["condition"])] if m
    ))
    if len(_ser_vals_sum) > 1:
        cond_dims.append({
            "col": "Serial Number",
            "col_id": "Serial_Number",
            "label": "Serial",
            "vals": _ser_vals_sum,
        })

    palette = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
    ]

    def _first_int(col):
        if col not in df.columns:
            return None
        v = pd.to_numeric(df[col], errors="coerce").dropna()
        return int(round(v.iloc[0])) if len(v) else None

    html = _build_env_summary_html(
        env_data, cond_dims, title, y_label, y_lim, log_x, freq_min, freq_max, palette,
        freq_vals=all_freqs,
        p_std=_first_int("Proportion (Std.)"),
        p_env=_first_int("Proportion (Env.)"),
        c_std=_first_int("Confidence (Std.)"),
        c_env=_first_int("Confidence (Env.)"),
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def stat_summary(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """
    Interactive statistical tolerance-interval analysis.

    For each condition group and frequency, computes:
      - Normal tolerance interval bounds (parametric, one-sided k-factor)
      - Shapiro-Wilk normality test
      - Delta-environmental contribution from non-Room temperature data
      - Test limit lines (TLL = Spec - Guard band)

    Controls allow adjusting P, C, n override, MU, DEnv override, guard band, drift.
    Requires scipy.
    """
    df = _load_scatter_for_stats(csv_path)
    df = _parse_group_fields(df)
    k_table = _build_k_table()
    stat_data = _aggregate_stat_data(df, cfg)
    all_serials = sorted({d["s"]
                          for cd in stat_data
                          for fs in cd.get("freq_stats", [])
                          for d in fs.get("dut_vals", [])})
    title = cfg.get("title", output_html.stem)
    html = _build_stat_summary_html(stat_data, k_table, df, cfg, title, all_serials)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


def stat_boxplot(csv_path: Path, cfg: dict, output_html: Path, interactive: bool = True) -> None:
    """
    Box plots per frequency per condition.

    Parameters
    ----------
    interactive : bool
        True (default) = fully interactive HTML with condition/temperature/Y-range
        filter controls and a live-updating statistics table.
        False = static Plotly-rendered HTML (no JS filtering).
    """
    if interactive:
        _stat_boxplot_interactive(csv_path, cfg, output_html)
        return
    df = _load_scatter_for_stats(csv_path)
    df = _parse_group_fields(df)
    title = cfg.get("title", output_html.stem)
    y_label = cfg.get("y_label", df["_val_col_name"].iloc[0] if len(df) else "Value")
    y_lim = cfg.get("y_lim")
    lo_spec, hi_spec = _get_spec(df, cfg)

    # Build condition label using _parse_group_kv (handles multi-word keys like "OA State")
    df = df.copy()
    if "Group" in df.columns and not df["Group"].isna().all():
        _serial_val_box = re.compile(r'^[A-Z]{2,3}\d{5,}$')
        _serial_key_kws = ("serial", "unit id", "dut id", "s/n")
        unique_groups_box = df["Group"].dropna().unique()
        group_kv_box = {g: _parse_group_kv(g) for g in unique_groups_box}
        all_keys_box = set(k for kv in group_kv_box.values() for k in kv)
        cond_keys_box = []
        for key in sorted(all_keys_box):
            if any(kw in key.lower() for kw in _serial_key_kws):
                continue
            vals = {kv.get(key, "") for kv in group_kv_box.values() if key in kv}
            if vals and sum(_serial_val_box.match(v) is not None for v in vals) / len(vals) > 0.5:
                continue
            if 1 < len(vals) <= 50:
                cond_keys_box.append(key)
        def _cond_box(g):
            kv = group_kv_box.get(g, {})
            parts = [f"{k}: {kv[k]}" for k in cond_keys_box if k in kv]
            return "  ".join(parts) if parts else "All"
        df["_cond"] = df["Group"].map(_cond_box).fillna("All")
    else:
        df["_cond"] = "All"

    # Convert numeric frequency to labeled string category for equal box spacing
    sorted_freqs = sorted(df["Frequency_MHz"].dropna().unique())
    def _freq_label(f):
        if f >= 1000:
            return f"{f/1000:.3g} GHz"
        return f"{f:.4g} MHz"
    freq_cat_map = {f: _freq_label(f) for f in sorted_freqs}
    df["_freq_cat"] = df["Frequency_MHz"].map(freq_cat_map)
    freq_cat_order = [_freq_label(f) for f in sorted_freqs]

    palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
               "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]

    fig = go.Figure()
    for i, (cond, cdf) in enumerate(df.groupby("_cond")):
        color = palette[i % len(palette)]
        temps_in_cond = cdf["Temperature"].dropna().unique()
        for temp, tdf in cdf.groupby("Temperature"):
            name = f"{cond} ({temp})" if len(temps_in_cond) > 1 else str(cond)
            fig.add_trace(go.Box(
                x=tdf["_freq_cat"],
                y=tdf["Value"],
                name=name,
                boxpoints="outliers",
                marker=dict(color=color, size=5, opacity=0.7),
                line=dict(color=color, width=2),
                whiskerwidth=0.6,
                fillcolor=color.replace("#", "rgba(").rstrip(")") if False else color,
                opacity=0.85,
            ))

    if not np.isnan(hi_spec):
        fig.add_hline(
            y=hi_spec, line_dash="dash", line_color="red",
            annotation_text=f"Spec Hi={hi_spec:g}",
            annotation_position="top right",
        )
    if not np.isnan(lo_spec):
        fig.add_hline(
            y=lo_spec, line_dash="dash", line_color="red",
            annotation_text=f"Spec Lo={lo_spec:g}",
            annotation_position="bottom right",
        )

    fig.update_xaxes(
        title_text="Frequency",
        categoryorder="array",
        categoryarray=freq_cat_order,
        tickangle=-45,
    )
    fig.update_yaxes(title_text=y_label, range=y_lim)
    fig.update_layout(
        title=dict(text=title, x=0.5),
        template="plotly_white",
        height=540,
        boxmode="group",
        boxgap=0.2,
        boxgroupgap=0.15,
        legend=dict(bgcolor="rgba(255,255,255,0.85)", bordercolor="#ccc", borderwidth=1),
        margin=dict(l=60, r=30, t=60, b=90),
    )

    # Compute per-DUT aggregate stats for summary table
    stat_data_box = _aggregate_stat_data(df, cfg)

    # Build custom HTML with embedded stats table
    plot_div = fig.to_html(full_html=False, include_plotlyjs=False)
    box_css = (
        "body{font-family:Arial,sans-serif;margin:0;padding:8px;background:#fafafa;}"
        ".toggle-btn{font-size:13px;padding:4px 14px;border:1px solid #bbb;border-radius:4px;"
        "cursor:pointer;background:#f0f2f5;margin:8px 4px 4px;}"
        ".toggle-btn:hover{background:#e0e2e8;}"
        "#box_stat_panel{overflow-x:auto;padding:4px;}"
        ".stbl{border-collapse:collapse;font-size:12px;width:100%;}"
        ".stbl th{background:#e8eaf6;padding:4px 8px;text-align:left;border:1px solid #ccc;"
        "white-space:nowrap;position:sticky;top:0;}"
        ".stbl td{padding:3px 8px;border:1px solid #ddd;white-space:nowrap;}"
        ".stbl tr:hover td{background:#f5f5ff;}"
        ".out{color:#c00;font-size:11px;}"
    )
    box_title_js = json.dumps(title)
    box_js = (
        f"var STAT_DATA_BOX={json.dumps(stat_data_box)};\n"
        f"var BOX_TITLE={box_title_js};\n"
        "(function(){\n"
        "  var rows=[];\n"
        "  STAT_DATA_BOX.forEach(function(cd){\n"
        "    var sorted=(cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;});\n"
        "    sorted.forEach(function(fs){\n"
        "      var nc=fs.norm==='Normal'?'green':fs.norm==='Marginal'?'orange':'red';\n"
        "      var nrmStr='<span style=\"color:'+nc+';font-weight:bold\">'+fs.norm+'</span> W='+fs.W.toFixed(3)+' p='+fs.p.toFixed(3);\n"
        "      var outStr=(fs.outliers&&fs.outliers.length)?\n"
        "        '<span class=\"out\"><b>'+fs.outliers.length+'</b>: '+fs.outliers.map(function(v){return v.toFixed(4);}).join(', ')+'</span>':'<span style=\"color:#aaa\">—</span>';\n"
        "      rows.push('<tr>'+\n"
        "        '<td>'+cd.condition+'</td>'+\n"
        "        '<td>'+fs.freq.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.n+'</td>'+\n"
        "        '<td>'+fs.mean.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.s.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.q1.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.q2.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.q3.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.lo_w.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.hi_w.toFixed(4)+'</td>'+\n"
        "        '<td>'+nrmStr+'</td>'+\n"
        "        '<td>'+outStr+'</td>'+\n"
        "        '</tr>');\n"
        "    });\n"
        "  });\n"
        "  var el=document.getElementById('box_stat_panel');\n"
        "  if(el){\n"
        "    el.innerHTML='<table class=\"stbl\"><thead><tr>'+\n"
        "      '<th>Condition</th><th>Freq(MHz)</th><th>n&nbsp;DUTs</th>'+\n"
        "      '<th>Mean</th><th>Std</th>'+\n"
        "      '<th>Q1</th><th>Median</th><th>Q3</th>'+\n"
        "      '<th>Lo&nbsp;Whisker</th><th>Hi&nbsp;Whisker</th>'+\n"
        "      '<th>Normality</th><th>Outliers</th></tr></thead><tbody>'+\n"
        "      rows.join('')+'</tbody></table>';\n"
        "  }\n"
        "})();\n"
        "function toggleStatPanel(){\n"
        "  var el=document.getElementById('box_stat_panel');\n"
        "  var btn=document.getElementById('stat_toggle_btn');\n"
        "  if(el.style.display==='none'){el.style.display='';btn.textContent='▼ Statistics Table';}\n"
        "  else{el.style.display='none';btn.textContent='► Statistics Table';}\n"
        "}\n"
        "function saveBoxCSV(withExcluded){\n"
        "  var hdrs=['Condition','Freq_MHz','n','Mean','Std','Q1','Median','Q3',\n"
        "            'Lo_Whisker','Hi_Whisker','Normality','W','p','DEnv_up','DEnv_lo','Spec_lo','Spec_hi','Outliers'];\n"
        "  var rows=[hdrs.join(',')];\n"
        "  function esc(v){var s=String(v==null?'':v);\n"
        "    return s.indexOf(',')>=0||s.indexOf('\"')>=0?'\"'+s.replace(/\"/g,'\"\"')+'\"':s;}\n"
        "  STAT_DATA_BOX.forEach(function(cd){\n"
        "    (cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;}).forEach(function(fs){\n"
        "      rows.push([\n"
        "        esc(cd.condition),\n"
        "        fs.freq.toFixed(4),\n"
        "        fs.n,\n"
        "        fs.mean.toFixed(6),\n"
        "        fs.s.toFixed(6),\n"
        "        fs.q1.toFixed(6),\n"
        "        fs.q2.toFixed(6),\n"
        "        fs.q3.toFixed(6),\n"
        "        fs.lo_w.toFixed(6),\n"
        "        fs.hi_w.toFixed(6),\n"
        "        fs.norm,\n"
        "        fs.W.toFixed(4),\n"
        "        fs.p.toFixed(4),\n"
        "        fs.denv_up!=null?fs.denv_up.toFixed(6):'',\n"
        "        fs.denv_lo!=null?fs.denv_lo.toFixed(6):'',\n"
        "        fs.spec_lo!=null?fs.spec_lo:'',\n"
        "        fs.spec_up!=null?fs.spec_up:'',\n"
        "        esc((fs.outliers||[]).map(function(v){return v.toFixed(4);}).join('; '))\n"
        "      ].join(','));\n"
        "    });\n"
        "  });\n"
        "  var ts=new Date().toISOString().replace('T',' ').replace(/\\.\\d+Z$/,' UTC');\n"
        "  var meta=['# PADB Export','# Plot: '+BOX_TITLE,'# Generated: '+ts,\n"
        "    '# Export: '+(withExcluded?'Filtered + excluded':'Filtered data'),\n"
        "    '# Note: Static box plot — all conditions included regardless of filter',\n"
        "    '#'].join('\\r\\n');\n"
        "  var blob=new Blob([meta+'\\r\\n'+rows.join('\\r\\n')],{type:'text/csv;charset=utf-8;'});\n"
        "  var url=URL.createObjectURL(blob);\n"
        "  var a=document.createElement('a');\n"
        "  var suffix=withExcluded?'_with_excl':'_filtered';\n"
        "  a.href=url;a.download=(BOX_TITLE+'_boxplot'+suffix).replace(/[^a-zA-Z0-9_\\-]/g,'_')+'.csv';\n"
        "  document.body.appendChild(a);a.click();document.body.removeChild(a);\n"
        "  URL.revokeObjectURL(url);\n"
        "}\n"
    )
    html = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f"<style>{box_css}</style>\n"
        f"<script>{_get_plotlyjs()}</script>\n"
        "</head>\n<body>\n"
        + plot_div + "\n"
        + '<div style="display:flex;gap:8px;align-items:center;padding:4px 8px">\n'
        + '<button class="toggle-btn" id="stat_toggle_btn"'
        ' onclick="toggleStatPanel()">&#9660; Statistics Table</button>\n'
        + f'{_csv_btn("saveBoxCSV")}\n'
        + '</div>\n'
        + '<div id="box_stat_panel"></div>\n'
        + f"<script>\n{box_js}</script>\n"
        "</body>\n</html>"
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Interactive box plot (JS-driven with condition/temperature/Y-range filters)
# ---------------------------------------------------------------------------

_STAT_BOXPLOT_INTERACTIVE_JS = r"""
function getSelected(col){
  return Array.from(document.querySelectorAll('.'+col+':checked')).map(function(c){return c.value;});
}
function togglePanel(id){
  var p=document.getElementById('panel_'+id);
  if(p) p.classList.toggle('open');
}
/* Sync longform checkboxes from the intersection of all COND_DIMS selections */
function _syncLfFromAllDims(){
  if(!COND_DIMS||!COND_DIMS.length) return;
  document.querySelectorAll('.box_cond_lf_row').forEach(function(row){
    var cond=row.querySelector('.box_cond_lf_chk').value;
    var ok=COND_DIMS.every(function(dim){
      var sel=getSelected('box_cond_'+dim.col_id);
      if(!sel.length) return true;
      var safe=dim.col.replace(/[-\/\\^$*+?.()|[\]{}]/g,'\\$&');
      /* Use lookahead for double-space or end-of-string so multi-word values are captured */
      var m=cond.match(new RegExp(safe+':\\s*(.+?)(?=  |$)'));
      return m&&sel.indexOf(m[1].trim())>=0;
    });
    row.querySelector('.box_cond_lf_chk').checked=ok;
  });
}
function toggleAll(col){
  var allChk=document.getElementById('all_'+col);
  document.querySelectorAll('.'+col).forEach(function(c){c.checked=allChk.checked;});
  updateBadge(col);
  _syncLfFromAllDims();
  var primaryColEl=document.getElementById('box_lf_primary_col');
  var primaryClass=primaryColEl?'box_cond_'+primaryColEl.value:'box_cond_HarmonicNumber';
  if(col===primaryClass){var hs=document.getElementById('box_harm_sel');if(hs&&allChk&&allChk.checked)hs.value='all';}
  update();
}
function chkChanged(col){
  var all=document.querySelectorAll('.'+col);
  var chk=Array.from(all).filter(function(c){return c.checked;});
  var allEl=document.getElementById('all_'+col);
  if(allEl){allEl.checked=chk.length===all.length;allEl.indeterminate=chk.length>0&&chk.length<all.length;}
  updateBadge(col);
  _syncLfFromAllDims();
  var primaryColEl2=document.getElementById('box_lf_primary_col');
  var primaryClass2=primaryColEl2?'box_cond_'+primaryColEl2.value:'box_cond_HarmonicNumber';
  if(col===primaryClass2){
    var hs=document.getElementById('box_harm_sel');
    if(hs){var hSel=getSelected(col);hs.value=(hSel.length===1)?hSel[0]:'all';}
  }
  update();
}
function updateBadge(col){
  var all=document.querySelectorAll('.'+col);
  var chk=Array.from(all).filter(function(c){return c.checked;}).length;
  var b=document.getElementById('badge_'+col);
  if(b){b.textContent=chk+'/'+all.length;b.classList.toggle('active',chk<all.length);}
}
function getSelectedConds(){
  var allConds=[];
  BOX_DATA.forEach(function(cd){if(allConds.indexOf(cd.condition)<0) allConds.push(cd.condition);});
  /* Longform checkboxes take precedence when present */
  var lfChks=document.querySelectorAll('.box_cond_lf_chk');
  if(lfChks.length){
    var checked=Array.from(document.querySelectorAll('.box_cond_lf_chk:checked')).map(function(c){return c.value;});
    return checked.length?checked:allConds;
  }
  /* Fallback: per-dimension dropdowns */
  if(!COND_DIMS||!COND_DIMS.length) return allConds;
  return allConds.filter(function(cond){
    return COND_DIMS.every(function(dim){
      var allowed=getSelected('box_cond_'+dim.col_id);
      var safe=dim.col.replace(/[-\/\\^$*+?.()|[\]{}]/g,'\\$&');
      var m=cond.match(new RegExp(safe+':\\s*(.+?)(?=\\s{2,}|$)'));
      return m&&allowed.indexOf(m[1].trim())>=0;
    });
  });
}
function getSelectedTemps(){
  return Array.from(document.querySelectorAll('.box_env_chk:checked')).map(function(c){return c.value;});
}
function getYFilter(){
  var mode='all';
  document.querySelectorAll('input[name="box_flt"]').forEach(function(r){if(r.checked)mode=r.value;});
  var yhi_el=document.getElementById('box_flt_yhi');
  var yhi=yhi_el?parseFloat(yhi_el.value):NaN;
  var tll_el=document.getElementById('box_tll_hi');
  var tll_hi=(tll_el&&tll_el.value!=='')?parseFloat(tll_el.value):null;
  if(tll_hi!==null&&isNaN(tll_hi)) tll_hi=null;
  return {mode:mode,yhi:isNaN(yhi)?Infinity:yhi,tll_hi:tll_hi};
}
function getBoxFreqRange(){
  var lo=parseFloat(document.getElementById('box_freq_lo').value);
  var hi=parseFloat(document.getElementById('box_freq_hi').value);
  return {lo:isNaN(lo)?-Infinity:lo,hi:isNaN(hi)?Infinity:hi};
}
function isBoxNpTI(){var c=document.getElementById('box_np_ti_chk');return c?c.checked:false;}
function isShowPoints(){var c=document.getElementById('box_show_pts_chk');return c?c.checked:false;}
/* ---- serial filter ---- */
function getAllBoxSerials(){return Array.from(document.querySelectorAll('.box_ser_chk')).map(function(c){return c.value;});}
function getSelectedBoxSerials(){return Array.from(document.querySelectorAll('.box_ser_chk:checked')).map(function(c){return c.value;});}
function boxSerChkChanged(){
  var all=document.querySelectorAll('.box_ser_chk');
  var chk=Array.from(all).filter(function(c){return c.checked;}).length;
  var allEl=document.getElementById('all_box_ser');
  if(allEl){allEl.checked=chk===all.length;allEl.indeterminate=chk>0&&chk<all.length;}
  var b=document.getElementById('badge_box_ser');
  if(b){b.textContent=chk<all.length?chk+'/'+all.length:'';b.classList.toggle('active',chk<all.length);}
  update();
}
function toggleAllBoxSer(){
  var allEl=document.getElementById('all_box_ser');
  document.querySelectorAll('.box_ser_chk').forEach(function(c){c.checked=allEl?allEl.checked:true;});
  boxSerChkChanged();
}
/* ---- port filter ---- */
function getAllBoxPorts(){return Array.from(document.querySelectorAll('.box_port_chk')).map(function(c){return c.value;});}
function getSelectedBoxPorts(){return Array.from(document.querySelectorAll('.box_port_chk:checked')).map(function(c){return c.value;});}
function boxPortChkChanged(){
  var all=document.querySelectorAll('.box_port_chk');
  var chk=Array.from(all).filter(function(c){return c.checked;}).length;
  var allEl=document.getElementById('all_box_port');
  if(allEl){allEl.checked=chk===all.length;allEl.indeterminate=chk>0&&chk<all.length;}
  var b=document.getElementById('badge_box_port');
  if(b){b.textContent=chk<all.length?chk+'/'+all.length:'';b.classList.toggle('active',chk<all.length);}
  update();
}
function toggleAllBoxPort(){
  var allEl=document.getElementById('all_box_port');
  document.querySelectorAll('.box_port_chk').forEach(function(c){c.checked=allEl?allEl.checked:true;});
  boxPortChkChanged();
}
function toggleRangeInputs(){
  var el=document.getElementById('box_flt_range_inputs');
  if(el){var r=document.querySelector('input[name="box_flt"][value="range"]');
    el.style.display=(r&&r.checked)?'inline-flex':'none';}
}
function percentileSorted(sorted,p){
  var idx=(p/100)*(sorted.length-1);
  var lo=Math.floor(idx),hi=Math.ceil(idx);
  return lo===hi?sorted[lo]:sorted[lo]+(sorted[hi]-sorted[lo])*(idx-lo);
}
function computeBoxStats(vals,k){
  if(!vals||!vals.length) return null;
  k=k||1.5;
  var n=vals.length;
  var sorted=vals.slice().sort(function(a,b){return a-b;});
  var q1=percentileSorted(sorted,25),q2=percentileSorted(sorted,50),q3=percentileSorted(sorted,75);
  var mean=0;for(var i=0;i<n;i++) mean+=sorted[i];mean/=n;
  var iqr=q3-q1,loF=q1-k*iqr,hiF=q3+k*iqr;
  return {n:n,mean:mean,q1:q1,q2:q2,q3:q3,
    lo_w:loF,hi_w:hiF,
    outliers:sorted.filter(function(v){return v<loF||v>hiF;})};
}
function getIqrK(){var el=document.getElementById('box_iqr_k');return el?Math.max(0.5,parseFloat(el.value)||1.5):1.5;}
function isExclRoom(){var el=document.getElementById('box_excl_room_chk');return el&&el.checked;}
function isExclDEnv(){var el=document.getElementById('box_excl_denv_chk');return el&&el.checked;}
/* Normalize "_cond" string "A: 1  B: 2" → "A=1|B=2" for cross-plot global filter keys */
function condToKey(cond){
  return (cond||'').split(/  +/).map(function(p){return p.replace(/\s*:\s*/,'=').trim();}).filter(Boolean).sort().join('|');
}
/* Build full sorted condition key including Port dimension (cross-plot GF key compatibility).
   Scatter _buildCoarseKey includes Port as a _grp_ column; boxplot excludes Port from cond_keys.
   Adding Port here so boxplot GF keys match what other plots produce. */
function _boxFullCondKey(rawCond, port){
  var parts=condToKey(rawCond).split('|').filter(Boolean);
  if(port) parts.push('Port='+port);
  parts.sort();
  return parts.join('|');
}
/* GF membership check with dimension-intersection + serial normalisation.
   Handles old-format keys (port-qualified serial, missing Port/AlcState/Mode in condKey)
   by: (1) exact match first, (2) strip port suffix from stored serial and compare base,
   (3) only check dimensions present in BOTH stored key and check key. */
function _boxIsInGf(checkKey){
  if(!_boxGfCoarseExcluded||!_boxGfCoarseExcluded.size) return false;
  if(_boxGfCoarseExcluded.has(checkKey)) return true;
  var sep=checkKey.indexOf('||');if(sep<0) return false;
  var ser=checkKey.slice(0,sep);
  var rowCondMap={};
  checkKey.slice(sep+2).split('|').filter(Boolean).forEach(function(kv){
    var i=kv.indexOf('=');if(i<0) return;
    rowCondMap[kv.slice(0,i)]=kv.slice(i+1);
  });
  var found=false;
  _boxGfCoarseExcluded.forEach(function(gk){
    if(found) return;
    var gs=gk.indexOf('||');if(gs<0) return;
    if(_boxBaseSerial(gk.slice(0,gs))!==ser) return;
    var allMatch=true;
    gk.slice(gs+2).split('|').filter(Boolean).forEach(function(kv){
      if(!allMatch) return;
      var i=kv.indexOf('=');if(i<0) return;
      var dim=kv.slice(0,i);
      if(rowCondMap.hasOwnProperty(dim)&&rowCondMap[dim]!==kv.slice(i+1)) allMatch=false;
    });
    if(allMatch) found=true;
  });
  return found;
}
/* Risk metrics for one outlier removed from allVals */
function outlierRisk(outlierVal,allVals){
  var n=allVals.length;
  if(n<2) return {sigma:0,dNpTi:0};
  var mean=0;for(var i=0;i<n;i++) mean+=allVals[i];mean/=n;
  var v2=0;for(var i=0;i<n;i++) v2+=(allVals[i]-mean)*(allVals[i]-mean);
  var std=Math.sqrt(v2/(n-1));
  var sigma=std>0?(outlierVal-mean)/std:0;
  var sorted=allVals.slice().sort(function(a,b){return a-b;});
  var oldRange=sorted[n-1]-sorted[0];
  var removed=false;
  var newArr=sorted.filter(function(v){if(!removed&&v===outlierVal){removed=true;return false;}return true;});
  var dNpTi=newArr.length>0?(oldRange-(newArr[newArr.length-1]-newArr[0]))/2:oldRange/2;
  return {sigma:sigma,dNpTi:dNpTi};
}
/* Build freq-numeric → freq-label map from box data (done once at call time) */
function _getFreqToLabel(){
  var m={};
  BOX_DATA.forEach(function(cd){
    cd.freq_stats.forEach(function(fs){if(fs.freq!==undefined&&fs.freq_label)m[fs.freq]=fs.freq_label;});
  });
  return m;
}
/* Compute per-freq-cat spec limits from BOX_STATS for the active conditions.
   Uses the most restrictive limit: min(upper), max(lower) across active conditions. */
function _specFromStats(selConds,freqToLabel){
  var hi={},lo={};
  BOX_STATS.forEach(function(cd){
    if(selConds.indexOf(cd.condition)<0) return;
    (cd.freq_stats||[]).forEach(function(fs){
      var fl=freqToLabel[fs.freq];if(!fl) return;
      if(fs.spec_up!==null&&fs.spec_up!==undefined){
        hi[fl]=(hi[fl]===undefined)?fs.spec_up:Math.min(hi[fl],fs.spec_up);
      }
      if(fs.spec_lo!==null&&fs.spec_lo!==undefined){
        lo[fl]=(lo[fl]===undefined)?fs.spec_lo:Math.max(lo[fl],fs.spec_lo);
      }
    });
  });
  return {hi:hi,lo:lo};
}
function buildBoxTraces(selConds,selTemps,yFlt,selBoxSers){
  var k=getIqrK();
  var exclRoom=isExclRoom();var exclDEnv=isExclDEnv();
  var allSers=getAllBoxSerials();
  var serActive=selBoxSers&&allSers.length>1&&selBoxSers.length<allSers.length;
  var allPorts=getAllBoxPorts();var selPorts=getSelectedBoxPorts();
  var portActive=allPorts.length>1&&selPorts.length<allPorts.length;
  var passActive=yFlt&&yFlt.mode==='passing';
  var yActive=yFlt&&yFlt.mode==='range'&&isFinite(yFlt.yhi);
  var kChanged=Math.abs(k-1.5)>0.001;
  var gfActive=_boxGfCoarseExcluded&&_boxGfCoarseExcluded.size>0;
  var boxGfFocus=(localStorage.getItem('padb_v2_gf_mode')||'exclude')==='focus';
  var passHi=passActive?(yFlt.tll_hi!==null&&yFlt.tll_hi!==undefined?yFlt.tll_hi:HI_SPEC):null;
  var passLo=passActive?LO_SPEC:null;
  var rhi=yActive&&isFinite(yFlt.yhi)?yFlt.yhi:Infinity;
  var fr=getBoxFreqRange();
  var traces=[];
  var condIdxMap={};var ci=0;
  BOX_DATA.forEach(function(cd){if(condIdxMap[cd.condition]===undefined) condIdxMap[cd.condition]=ci++;});
  BOX_DATA.forEach(function(cd){
    if(selConds.indexOf(cd.condition)<0) return;
    if(selTemps.indexOf(cd.temp)<0) return;
    var condKey=condToKey(cd.condition);
    var excl=cd.temp==='Room'?exclRoom:exclDEnv;
    /* Always recompute from vals_detail — avoids Plotly.react stale-data issue when
       switching from a filtered trace back to the pre-computed fast path */
    var needsRecompute=true;
    var fs;
    if(needsRecompute){
      fs=[];
      cd.freq_stats.forEach(function(f){
        if(f.freq<fr.lo||f.freq>fr.hi) return;
        var allDet=(f.vals_detail||f.vals.map(function(v){return {s:'unknown',v:v};}));
        var detail=allDet.filter(function(d){
          if(serActive&&selBoxSers.indexOf(d.s)<0) return false;
          if(portActive&&selPorts.indexOf(d.p||'')<0) return false;
          if(d.v>rhi) return false;
          if(passActive&&((passLo!==null&&d.v<passLo)||(passHi!==null&&d.v>passHi))) return false;
          if(gfActive){var _ig=_boxIsInGf(_boxBaseSerial(d.s)+'||'+_boxFullCondKey(cd.condition,d.p)+'|Temp='+cd.temp);if(boxGfFocus?!_ig:_ig) return false;}
          return true;
        });
        if(!detail.length) return;
        var fv=detail.map(function(d){return d.v;});
        var s=computeBoxStats(fv,k);
        if(!s) return;
        var outDet=detail.filter(function(d){return d.v<s.lo_w||d.v>s.hi_w;});
        var boxDet=excl?detail.filter(function(d){return d.v>=s.lo_w&&d.v<=s.hi_w;}):detail;
        if(!boxDet.length) boxDet=detail; /* safety: if all values are outliers, show all */
        var bsVals=boxDet.map(function(d){return d.v;});
        var bsRaw=computeBoxStats(bsVals,k);
        if(!bsRaw) return;
        /* Box stats from active set; whiskers span active set extent.
           Exclude mode → inlier-only set → shorter whiskers, no floating specs. */
        var whiskerVals=excl?bsVals:fv;
        var bs={n:bsRaw.n,mean:bsRaw.mean,q1:bsRaw.q1,q2:bsRaw.q2,q3:bsRaw.q3,
                lo_w:Math.min.apply(null,whiskerVals),hi_w:Math.max.apply(null,whiskerVals),
                outliers:bsRaw.outliers};
        fs.push({freq:f.freq,freq_label:f.freq_label,
          n:detail.length,mean:bs.mean,q1:bs.q1,q2:bs.q2,q3:bs.q3,lo_w:bs.lo_w,hi_w:bs.hi_w,
          outlier_detail:outDet,outliers:outDet.map(function(d){return d.v;}),vals_detail:detail});
      });
    } else {
      fs=cd.freq_stats.filter(function(f){return f.freq>=fr.lo&&f.freq<=fr.hi;});
    }
    if(!fs.length) return;
    var color=PALETTE[(condIdxMap[cd.condition]||0)%PALETTE.length];
    var showTemp=selTemps.length>1;
    var name=showTemp?cd.condition+' ('+cd.temp+')':cd.condition;
    traces.push({
      type:'box',
      x:fs.map(function(f){return f.freq_label;}),
      q1:fs.map(function(f){return f.q1;}),
      median:fs.map(function(f){return f.q2;}),
      q3:fs.map(function(f){return f.q3;}),
      lowerfence:fs.map(function(f){return f.lo_w;}),
      upperfence:fs.map(function(f){return f.hi_w;}),
      mean:fs.map(function(f){return f.mean;}),
      boxpoints:false,
      name:name,
      marker:{color:color,opacity:0.7},
      line:{color:color,width:2},
      whiskerwidth:0.6,
      opacity:cd.temp==='Room'?0.85:0.65,
      hovertemplate:'<b>'+name+'</b><br>Freq: %{x}<br>Q1: %{q1:.4f}<br>Median: %{median:.4f}<br>'+
        'Q3: %{q3:.4f}<br>Whiskers: [%{lowerfence:.4f}, %{upperfence:.4f}]<extra></extra>',
    });
    if(isShowPoints()){
      var pxP=[],pyP=[],ptP=[];
      fs.forEach(function(f){
        var vd=f.vals_detail||[];
        vd.forEach(function(d){
          pxP.push(f.freq_label);pyP.push(d.v);
          ptP.push((d.s&&d.s!=='unknown'?d.s+': ':'')+d.v.toFixed(4));
        });
      });
      if(pxP.length){
        traces.push({type:'scatter',x:pxP,y:pyP,mode:'markers',
          marker:{size:5,color:color,opacity:0.55},
          name:name+' pts',showlegend:false,text:ptP,
          hovertemplate:'%{text}<extra></extra>'});
      }
    }
    var oxArr=[],oyArr=[],oText=[];
    fs.forEach(function(f){
      var det=f.outlier_detail||[];
      det.forEach(function(d){
        oxArr.push(f.freq_label);oyArr.push(d.v);
        oText.push(name+' outlier: '+d.v.toFixed(4)+(d.s&&d.s!=='unknown'?' ('+d.s+')':''));
      });
      if(!det.length){
        (f.outliers||[]).forEach(function(v){oxArr.push(f.freq_label);oyArr.push(v);oText.push(name+' outlier: '+v.toFixed(4));});
      }
    });
    /* Only draw outlier circles in normal mode; in exclude mode the shorter
       whisker itself signals what was removed — no circles dangling beyond it. */
    if(oxArr.length&&!excl){
      traces.push({type:'scatter',x:oxArr,y:oyArr,mode:'markers',text:oText,
        marker:{symbol:'circle-open',size:7,color:color,opacity:0.9,
          line:{width:2,color:color}},
        name:name+' outliers',showlegend:false,
        hovertemplate:'%{text}<extra></extra>'});
    }
  });
  /* Dynamic per-freq-cat spec lines derived from active conditions (filtered to current freq range) */
  var f2l=_getFreqToLabel();
  var specMaps=_specFromStats(selConds,f2l);
  var _specActiveLabels=new Set();
  BOX_DATA.forEach(function(cd){
    (cd.freq_stats||[]).forEach(function(fs){if(fs.freq>=fr.lo&&fs.freq<=fr.hi)_specActiveLabels.add(fs.freq_label);});
  });
  var _specOrder=BOX_FREQ_ORDER.filter(function(l){return _specActiveLabels.has(l);});
  var hiX=[],hiY=[],loX=[],loY=[];
  _specOrder.forEach(function(fl){
    var hv=specMaps.hi[fl]; if(hv===undefined&&HI_SPEC!==null) hv=HI_SPEC;
    var lv=specMaps.lo[fl]; if(lv===undefined&&LO_SPEC!==null) lv=LO_SPEC;
    if(hv!==undefined){hiX.push(fl);hiY.push(hv);}
    if(lv!==undefined){loX.push(fl);loY.push(lv);}
  });
  if(loX.length) traces.push({type:'scatter',mode:'lines',x:loX,y:loY,
    line:{color:'red',dash:'dash',width:1.5},name:'Spec Lo',
    hovertemplate:'Spec Lo: %{y:.4f}<extra></extra>'});
  if(hiX.length) traces.push({type:'scatter',mode:'lines',x:hiX,y:hiY,
    line:{color:'red',dash:'dash',width:1.5},name:'Spec Hi',
    hovertemplate:'Spec Hi: %{y:.4f}<extra></extra>'});
  /* Manual TLL override line */
  if(yFlt&&yFlt.tll_hi!==null&&yFlt.tll_hi!==undefined&&_specOrder.length){
    var tllX=[_specOrder[0],_specOrder[_specOrder.length-1]];
    traces.push({type:'scatter',mode:'lines',x:tllX,y:[yFlt.tll_hi,yFlt.tll_hi],
      line:{color:'darkred',dash:'dot',width:2},name:'TLL↑ (manual)',
      hovertemplate:'TLL↑ (manual): '+yFlt.tll_hi.toFixed(4)+'<extra></extra>'});
  }
  return traces;
}
function buildLayout(){
  /* Filter category order by current freq range */
  var fr=getBoxFreqRange();
  var activeLabels=new Set();
  BOX_DATA.forEach(function(cd){
    (cd.freq_stats||[]).forEach(function(fs){if(fs.freq>=fr.lo&&fs.freq<=fr.hi)activeLabels.add(fs.freq_label);});
  });
  var filteredOrder=BOX_FREQ_ORDER.filter(function(l){return activeLabels.has(l);});
  return {
    title:{text:BOX_TITLE,x:0.5,font:{size:15}},
    template:'plotly_white',
    xaxis:{title:'Frequency',categoryorder:'array',categoryarray:filteredOrder,tickangle:-45},
    yaxis:{title:Y_LABEL,range:Y_LIM,autorange:Y_LIM?false:true},
    height:540,boxmode:'group',boxgap:0.2,boxgroupgap:0.15,
    legend:{bgcolor:'rgba(255,255,255,0.85)',bordercolor:'#ccc',borderwidth:1,font:{size:11}},
    margin:{l:60,r:30,t:60,b:90},
  };
}
function updateStatsTable(selConds,yFlt,selBoxSers,selTemps){
  var el=document.getElementById('box_stat_panel');
  if(!el||el.style.display==='none') return;
  var showNp=isBoxNpTI();
  var allSers=getAllBoxSerials();
  var serActive=selBoxSers&&allSers.length>1&&selBoxSers.length<allSers.length;
  var passActive=yFlt&&yFlt.mode==='passing';
  var yFltActive=yFlt&&yFlt.mode==='range'&&isFinite(yFlt.yhi);
  var stPassHi=passActive?(yFlt.tll_hi!==null&&yFlt.tll_hi!==undefined?yFlt.tll_hi:HI_SPEC):null;
  var stPassLo=passActive?LO_SPEC:null;
  var fr=getBoxFreqRange();
  var rows=[];
  var tempActive=selTemps&&selTemps.length<TEMPS_PRESENT.length;
  if(serActive||yFltActive||passActive||tempActive){
    var rhi=yFltActive&&isFinite(yFlt.yhi)?yFlt.yhi:Infinity;
    var fltLabel=(serActive&&yFltActive)?'Serial+Y-filtered':
                 serActive?'Serial-filtered':
                 passActive?'Passing only':
                 tempActive?'Temp-filtered':
                 'Y-filtered [hi='+rhi.toFixed(3)+']';
    BOX_DATA.forEach(function(cd){
      if(selConds.indexOf(cd.condition)<0) return;
      if(selTemps&&selTemps.indexOf(cd.temp)<0) return;
      (cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;}).forEach(function(f){
        if(f.freq<fr.lo||f.freq>fr.hi) return;
        var detail=(f.vals_detail||f.vals.map(function(v){return {s:'unknown',v:v};}))
          .filter(function(d){
            return (!serActive||selBoxSers.indexOf(d.s)>=0)&&d.v<=rhi
              &&(!passActive||(stPassLo===null||d.v>=stPassLo)&&(stPassHi===null||d.v<=stPassHi));
          });
        if(!detail.length) return;
        var fv=detail.map(function(d){return d.v;});
        var s=computeBoxStats(fv);
        if(!s) return;
        var variance=fv.reduce(function(acc,v){return acc+(v-s.mean)*(v-s.mean);},0)/(fv.length>1?fv.length-1:1);
        var std=Math.sqrt(variance);
        var outDet=detail.filter(function(d){return d.v<s.lo_w||d.v>s.hi_w;});
        var outStr=outDet.length?
          '<span class="out"><b>'+outDet.length+'</b>: '+outDet.map(function(d){return d.v.toFixed(4)+(d.s&&d.s!=='unknown'?' ('+d.s+')':'');}).join(', ')+'</span>':
          '<span style="color:#aaa">&#8212;</span>';
        rows.push('<tr><td>'+cd.condition+' / '+cd.temp+'</td><td>'+f.freq.toFixed(4)+'</td><td>'+s.n+'</td>'+
          '<td>'+s.mean.toFixed(4)+'</td><td>'+std.toFixed(4)+'</td>'+
          '<td>'+s.q1.toFixed(4)+'</td><td>'+s.q2.toFixed(4)+'</td><td>'+s.q3.toFixed(4)+'</td>'+
          '<td><em style="color:#888">'+fltLabel+'</em></td>'+
          (showNp?'<td style="color:#aaa;font-size:11px">&#8212;</td>':'')+
          '<td>'+outStr+'</td></tr>');
      });
    });
  } else {
    BOX_STATS.forEach(function(cd){
      if(selConds.indexOf(cd.condition)<0) return;
      (cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;}).forEach(function(fs){
        if(fs.freq<fr.lo||fs.freq>fr.hi) return;
        var nc=fs.norm==='Normal'?'green':fs.norm==='Marginal'?'orange':'red';
        var nrmStr='<span style="color:'+nc+';font-weight:bold">'+fs.norm+'</span> W='+fs.W.toFixed(3)+' p='+fs.p.toFixed(3);
        var outDet=fs.outlier_detail||[];
        var outStr=outDet.length?
          '<span class="out"><b>'+outDet.length+'</b>: '+outDet.map(function(d){return d.v.toFixed(4)+(d.s&&d.s!=='unknown'?' ('+d.s+')':'');}).join(', ')+'</span>':
          '<span style="color:#aaa">&#8212;</span>';
        var npCell='';
        if(showNp){
          if((fs.norm==='Non-normal'||fs.norm==='Marginal')&&fs.np_ti_lo!=null&&fs.np_ti_up!=null)
            npCell='<td style="color:#a06000;font-weight:bold">['+fs.np_ti_lo.toFixed(4)+', '+fs.np_ti_up.toFixed(4)+']</td>';
          else
            npCell='<td style="color:#aaa;font-size:11px">'+(fs.np_ti_lo==null?'n&nbsp;too&nbsp;small':'Normal&nbsp;(k·s)')+'</td>';
        }
        rows.push('<tr><td>'+cd.condition+'</td><td>'+fs.freq.toFixed(4)+'</td><td>'+fs.n+'</td>'+
          '<td>'+fs.mean.toFixed(4)+'</td><td>'+fs.s.toFixed(4)+'</td>'+
          '<td>'+fs.q1.toFixed(4)+'</td><td>'+fs.q2.toFixed(4)+'</td><td>'+fs.q3.toFixed(4)+'</td>'+
          '<td>'+nrmStr+'</td>'+npCell+'<td>'+outStr+'</td></tr>');
      });
    });
  }
  var npHdr=showNp?'<th>NP&nbsp;TI&nbsp;Bounds</th>':'';
  el.innerHTML='<table class="stbl"><thead><tr>'+
    '<th>Condition</th><th>Freq(MHz)</th><th>n&nbsp;DUTs</th>'+
    '<th>Mean</th><th>Std</th><th>Q1</th><th>Median</th><th>Q3</th>'+
    '<th>Normality</th>'+npHdr+'<th>Outliers</th></tr></thead><tbody>'+rows.join('')+'</tbody></table>';
}
function toggleStatPanel(){
  var el=document.getElementById('box_stat_panel');
  var btn=document.getElementById('box_stat_toggle_btn');
  if(!el||!btn) return;
  if(el.style.display==='none'){
    el.style.display='';btn.textContent='&#9660; Statistics Table';
    updateStatsTable(getSelectedConds(),getYFilter(),getSelectedBoxSerials());
  } else {
    el.style.display='none';btn.textContent='&#9658; Statistics Table';
  }
}
function saveBoxCSV(withExcluded){
  var selConds=getSelectedConds();var selTemps=getSelectedTemps();
  /* withExcluded: include all conditions/temps regardless of current filter */
  var exportConds=withExcluded?BOX_DATA.map(function(cd){return cd.condition;}).filter(function(v,i,a){return a.indexOf(v)===i;}):selConds;
  var exportTemps=withExcluded?TEMPS_PRESENT:selTemps;
  var hdrs=['Condition','Temperature','Freq_MHz','Freq_Label','n_raw','Mean','Q1','Median','Q3','LowerFence','UpperFence','Outliers'];
  if(withExcluded) hdrs.push('Included');
  var rows=[hdrs.join(',')];
  function esc(v){var s=String(v==null?'':v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;}
  var fr=getBoxFreqRange();
  BOX_DATA.forEach(function(cd){
    if(exportConds.indexOf(cd.condition)<0) return;
    if(exportTemps.indexOf(cd.temp)<0) return;
    var isIncluded=selConds.indexOf(cd.condition)>=0&&selTemps.indexOf(cd.temp)>=0;
    (cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;}).forEach(function(fs){
      if(!withExcluded&&(fs.freq<fr.lo||fs.freq>fr.hi)) return;
      var row=[esc(cd.condition),esc(cd.temp),fs.freq.toFixed(4),esc(fs.freq_label),
        fs.n,fs.mean.toFixed(6),fs.q1.toFixed(6),fs.q2.toFixed(6),fs.q3.toFixed(6),
        fs.lo_w.toFixed(6),fs.hi_w.toFixed(6),
        esc((fs.outliers||[]).map(function(v){return v.toFixed(4);}).join('; '))
      ];
      if(withExcluded) row.push(isIncluded?'true':'false');
      rows.push(row.join(','));
    });
  });
  /* Metadata block */
  var ts=new Date().toISOString().replace('T',' ').replace(/\.\d+Z$/,' UTC');
  var allConds=BOX_DATA.map(function(cd){return cd.condition;}).filter(function(v,i,a){return a.indexOf(v)===i;});
  var excConds=allConds.filter(function(c){return selConds.indexOf(c)<0;});
  var allBoxSers=getAllBoxSerials();var selBoxSers=getSelectedBoxSerials();
  var serFlt=allBoxSers.length>1&&selBoxSers.length<allBoxSers.length;
  var gfKeys=[];
  try{var gfRaw=localStorage.getItem('padb_v2_excluded');if(gfRaw) gfKeys=JSON.parse(gfRaw).excluded||[];}catch(e){}
  var gfSers=[];
  gfKeys.forEach(function(k){var s=k.split('||')[0];if(gfSers.indexOf(s)<0) gfSers.push(s);});
  var meta=['# PADB Export','# Plot: '+BOX_TITLE,'# Generated: '+ts,
    '# Export: '+(withExcluded?'Filtered + excluded (excluded condition/temp rows flagged in Included column)':'Filtered data'),
    '# Active conditions ('+selConds.length+'): '+selConds.join(', '),
    '# Excluded conditions ('+excConds.length+'): '+(excConds.length?excConds.join(', '):'None'),
    '# Active temperatures: '+selTemps.join(', '),
    '# Active serials: '+(serFlt?selBoxSers.join(', '):'All ('+allBoxSers.length+')'),
    '# GF current DUTs ('+gfSers.length+'): '+(gfSers.length?gfSers.join(', '):'None'),
    '#'
  ].join('\r\n');
  var blob=new Blob([meta+'\r\n'+rows.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  var suffix=withExcluded?'_with_excl':'_filtered';
  a.href=url;a.download=(BOX_TITLE+'_boxplot'+suffix).replace(/[^a-zA-Z0-9_\-]/g,'_')+'.csv';
  document.body.appendChild(a);a.click();document.body.removeChild(a);URL.revokeObjectURL(url);
}
/* ---- outlier risk panel ---- */
function _collectOutliers(selConds,selTemps,yFlt,selBoxSers){
  var k=getIqrK();
  var allSers=getAllBoxSerials();
  var serActive=selBoxSers&&allSers.length>1&&selBoxSers.length<allSers.length;
  var passActive=yFlt&&yFlt.mode==='passing';
  var yActive=yFlt&&yFlt.mode==='range'&&isFinite(yFlt.yhi);
  var olPassHi=passActive?(yFlt.tll_hi!==null&&yFlt.tll_hi!==undefined?yFlt.tll_hi:HI_SPEC):null;
  var olPassLo=passActive?LO_SPEC:null;
  var rhi=yActive&&isFinite(yFlt.yhi)?yFlt.yhi:Infinity;
  var fr=getBoxFreqRange();
  var result=[];
  BOX_DATA.forEach(function(cd){
    if(selConds.indexOf(cd.condition)<0) return;
    if(selTemps.indexOf(cd.temp)<0) return;
    cd.freq_stats.forEach(function(f){
      if(f.freq<fr.lo||f.freq>fr.hi) return;
      var allDet=(f.vals_detail||f.vals.map(function(v){return {s:'unknown',v:v};}));
      var detail=allDet.filter(function(d){
        return (!serActive||selBoxSers.indexOf(d.s)>=0)&&d.v<=rhi
          &&(!passActive||(olPassLo===null||d.v>=olPassLo)&&(olPassHi===null||d.v<=olPassHi));
      });
      if(detail.length<2) return;
      var fv=detail.map(function(d){return d.v;});
      var s=computeBoxStats(fv,k);
      if(!s) return;
      detail.filter(function(d){return d.v<s.lo_w||d.v>s.hi_w;}).forEach(function(d){
        var risk=outlierRisk(d.v,fv);
        result.push({cond:cd.condition,temp:cd.temp,freq:f.freq,freqLabel:f.freq_label,
          serial:d.s,port:d.p||'',value:d.v,sigma:risk.sigma,dNpTi:risk.dNpTi,
          key:_boxBaseSerial(d.s)+'||'+_boxFullCondKey(cd.condition,d.p)+'||'+cd.temp+'||'+f.freq.toFixed(3)});
      });
    });
  });
  return result;
}
function updateOutlierPanel(selConds,selTemps,yFlt,selBoxSers){
  var panel=document.getElementById('box_outlier_panel');
  if(!panel||panel.style.display==='none') return;
  var outliers=_collectOutliers(selConds,selTemps,yFlt,selBoxSers);
  window._currentOutlierKeys=outliers.map(function(o){return o.key;});
  var applyBtn=document.getElementById('box_apply_gf_btn');
  if(applyBtn) applyBtn.textContent='Set as global filter ('+outliers.length+' pt'+(outliers.length!==1?'s':'')+')';
  if(!outliers.length){
    panel.innerHTML='<div style="padding:6px 14px;color:#888;font-size:12px">No outliers at current k×IQR threshold.</div>';
    return;
  }
  outliers.sort(function(a,b){return Math.abs(b.sigma)-Math.abs(a.sigma);});
  var hasSpec=LO_SPEC!==null||HI_SPEC!==null;
  var hasPort=outliers.some(function(o){return !!o.port;});
  var rows=outliers.map(function(o){
    var absSig=Math.abs(o.sigma);
    var sigStyle=absSig>=3?'color:#c00;font-weight:bold':absSig>=2?'color:#c80;font-weight:bold':'color:#555';
    var specCell=hasSpec?'<td style="'+(((LO_SPEC!==null&&o.value<LO_SPEC)||(HI_SPEC!==null&&o.value>HI_SPEC))?'color:#c00;font-weight:bold':'color:#080')+'">'+(((LO_SPEC!==null&&o.value<LO_SPEC)||(HI_SPEC!==null&&o.value>HI_SPEC))?'FAIL':'Pass')+'</td>':'';
    var portCell=hasPort?'<td>'+o.port+'</td>':'';
    return '<tr><td>'+o.cond+'</td><td>'+o.temp+'</td><td>'+o.freqLabel+'</td>'+
      '<td>'+(o.serial==='unknown'?'&mdash;':o.serial)+'</td>'+portCell+
      '<td>'+o.value.toFixed(4)+'</td>'+
      '<td style="'+sigStyle+'">'+o.sigma.toFixed(2)+'σ</td>'+
      '<td>'+o.dNpTi.toFixed(4)+'</td>'+specCell+'</tr>';
  });
  var specHdr=hasSpec?'<th>Pass/Fail</th>':'';
  var portHdr=hasPort?'<th>Port</th>':'';
  panel.innerHTML='<table class="stbl"><thead><tr>'+
    '<th>Condition</th><th>Temp</th><th>Frequency</th><th>Serial</th>'+portHdr+
    '<th>Value</th><th>σ from mean</th><th>Δ NP TI/2</th>'+specHdr+
    '</tr></thead><tbody>'+rows.join('')+'</tbody></table>';
}
function toggleOutlierPanel(){
  var panel=document.getElementById('box_outlier_panel');
  var btn=document.getElementById('box_outlier_toggle_btn');
  if(!panel||!btn) return;
  var show=panel.style.display==='none';
  panel.style.display=show?'':'none';
  btn.textContent=(show?'▼':'▶')+' Outlier Detail';
  if(show){
    var selConds=getSelectedConds();var selTemps=getSelectedTemps();var yFlt=getYFilter();
    updateOutlierPanel(selConds,selTemps,yFlt,getSelectedBoxSerials());
  }
}
/* ---- delta-outlier (temperature sensitivity) detection ---- */
function _collectDeltaOutliers(selConds,selTemps,selBoxSers){
  var k=getIqrK();
  var fr=getBoxFreqRange();
  var result=[];
  selConds.forEach(function(cond){
    /* Build Room baseline: roomMap[serial][freq] = value */
    var roomMap={};
    BOX_DATA.forEach(function(cd){
      if(cd.condition!==cond||cd.temp!=='Room') return;
      (cd.freq_stats||[]).forEach(function(fs){
        if(fs.freq<fr.lo||fs.freq>fr.hi) return;
        (fs.vals_detail||[]).forEach(function(d){
          if(selBoxSers.indexOf(d.s)<0) return;
          if(!roomMap[d.s]) roomMap[d.s]={};
          roomMap[d.s][fs.freq]=d.v;
        });
      });
    });
    if(!Object.keys(roomMap).length) return;
    /* For each non-Room temp, compute per-DUT deltas and apply IQR */
    selTemps.forEach(function(temp){
      if(temp==='Room') return;
      BOX_DATA.forEach(function(cd){
        if(cd.condition!==cond||cd.temp!==temp) return;
        (cd.freq_stats||[]).forEach(function(fs){
          if(fs.freq<fr.lo||fs.freq>fr.hi) return;
          var deltas=[];
          (fs.vals_detail||[]).forEach(function(d){
            if(selBoxSers.indexOf(d.s)<0) return;
            if(!roomMap[d.s]||roomMap[d.s][fs.freq]===undefined) return;
            deltas.push({s:d.s,p:d.p||'',delta:d.v-roomMap[d.s][fs.freq],absVal:d.v,roomVal:roomMap[d.s][fs.freq]});
          });
          if(deltas.length<4) return;
          var dv=deltas.map(function(d){return d.delta;}).sort(function(a,b){return a-b;});
          var n=dv.length;
          function pct(p){var i=(p/100)*(n-1),lo=Math.floor(i);return lo+1<n?dv[lo]+(dv[lo+1]-dv[lo])*(i-lo):dv[lo];}
          var q1=pct(25),q3=pct(75),iqr=q3-q1;
          var loF=q1-k*iqr,hiF=q3+k*iqr;
          var mean=0;dv.forEach(function(v){mean+=v;});mean/=n;
          deltas.forEach(function(d){
            if(d.delta>=loF&&d.delta<=hiF) return;
            var sigma=iqr>0?(d.delta-mean)/(iqr/1.35):0;
            result.push({
              cond:cond,temp:temp,freq:fs.freq,freqLabel:fs.freq_label,
              serial:d.s,port:d.p||'',delta:d.delta,absVal:d.absVal,roomVal:d.roomVal,
              q1:q1,q3:q3,iqr:iqr,loF:loF,hiF:hiF,sigma:sigma,
              key:_boxBaseSerial(d.s)+'||'+_boxFullCondKey(cond,d.p)+'||'+temp+'||'+fs.freq.toFixed(3)
            });
          });
        });
      });
    });
  });
  return result;
}
function updateDeltaOutlierPanel(selConds,selTemps,selBoxSers){
  var panel=document.getElementById('box_delta_panel');
  if(!panel||panel.style.display==='none') return;
  var outliers=_collectDeltaOutliers(selConds,selTemps,selBoxSers);
  window._currentDeltaOutlierKeys=outliers.map(function(o){return o.key;});
  var applyBtn=document.getElementById('box_apply_delta_gf_btn');
  if(applyBtn) applyBtn.textContent='Set delta outliers as GF ('+outliers.length+' pt'+(outliers.length!==1?'s':'')+')';
  if(!outliers.length){
    panel.innerHTML='<div style="padding:6px 14px;color:#888;font-size:12px">No delta outliers at current k\xd7IQR threshold. (Requires Room data + ≥1 other temperature.)</div>';
    return;
  }
  outliers.sort(function(a,b){return Math.abs(b.sigma)-Math.abs(a.sigma);});
  var hasPort=outliers.some(function(o){return !!o.port;});
  var rows=outliers.map(function(o){
    var absSig=Math.abs(o.sigma);
    var sigStyle=absSig>=3?'color:#c00;font-weight:bold':absSig>=2?'color:#c80;font-weight:bold':'color:#555';
    var dir=o.delta>0?'+':'';
    var portCell=hasPort?'<td>'+o.port+'</td>':'';
    return '<tr>'+
      '<td>'+o.cond+'</td>'+
      '<td>'+o.temp+'</td>'+
      '<td>'+o.freqLabel+'</td>'+
      '<td>'+(o.serial==='unknown'?'&mdash;':o.serial)+'</td>'+portCell+
      '<td>'+o.absVal.toFixed(4)+'</td>'+
      '<td>'+o.roomVal.toFixed(4)+'</td>'+
      '<td style="'+(o.delta>0?'color:#c00':'color:#00c')+';font-weight:bold">'+dir+o.delta.toFixed(4)+'</td>'+
      '<td style="'+sigStyle+'">'+ o.sigma.toFixed(2)+'σ</td>'+
      '<td>'+o.loF.toFixed(4)+' to '+o.hiF.toFixed(4)+'</td>'+
      '</tr>';
  });
  var portHdr=hasPort?'<th>Port</th>':'';
  panel.innerHTML='<table class="stbl"><thead><tr>'+
    '<th>Condition</th><th>Temp</th><th>Frequency</th><th>Serial</th>'+portHdr+
    '<th>Value(T)</th><th>Value(Room)</th><th>Δ(T−Room)</th>'+
    '<th>σ from meanΔ</th><th>k×IQR fence</th>'+
    '</tr></thead><tbody>'+rows.join('')+'</tbody></table>';
}
  var selBoxSers=getSelectedBoxSerials();
  var outliers=_collectDeltaOutliers(selConds,selTemps,selBoxSers);
  var keys=outliers.map(function(o){return o.key;});
  if(!keys.length){alert('No delta outliers at current k\xd7IQR threshold.');return;}
  window._currentDeltaOutlierKeys=keys;
  _mergeGf(keys);
}
function toggleDeltaPanel(){
  var panel=document.getElementById('box_delta_panel');
  var btn=document.getElementById('box_delta_toggle_btn');
  if(!panel||!btn) return;
  var show=panel.style.display==='none';
  panel.style.display=show?'':'none';
  btn.textContent=(show?'▼':'▶')+' Delta Outlier Detail';
  if(show){
    var selConds=getSelectedConds();var selTemps=getSelectedTemps();
    updateDeltaOutlierPanel(selConds,selTemps,getSelectedBoxSerials());
  }
}
/* ---- Longform primary-dim filter (HarmonicNumber or SpurType) ---- */
function updateBoxHarmonic(){
  var harm=document.getElementById('box_harm_sel').value;
  var primaryColEl=document.getElementById('box_lf_primary_col');
  var primaryColId=primaryColEl?primaryColEl.value:'HarmonicNumber';
  /* Sync the matching COND_DIMS panel so _syncLfFromAllDims reads it */
  if(COND_DIMS){
    COND_DIMS.forEach(function(dim){
      if(dim.col_id!==primaryColId) return;
      var col='box_cond_'+dim.col_id;
      document.querySelectorAll('.'+col).forEach(function(c){c.checked=(harm==='all'||c.value===harm);});
      var allEl=document.getElementById('all_'+col);if(allEl)allEl.checked=(harm==='all');
      updateBadge(col);
    });
  }
  _syncLfFromAllDims();
  update();
}
function selAllBoxLf(on){
  document.querySelectorAll('.box_cond_lf_chk').forEach(function(c){c.checked=on;});
  update();
}

/* ---- Clear everything ---- */
function clearEverything(){
  _stClear();
  /* Longform harmonic + checkboxes */
  var hs=document.getElementById('box_harm_sel');
  if(hs) hs.value='all';
  document.querySelectorAll('.box_cond_lf_chk').forEach(function(c){c.checked=true;});
  /* Per-dimension dropdowns */
  if(COND_DIMS) COND_DIMS.forEach(function(dim){
    var id='box_cond_'+dim.col_id;
    document.querySelectorAll('.'+id).forEach(function(c){c.checked=true;});
    var allEl=document.getElementById('all_'+id);if(allEl)allEl.checked=true;
    var b=document.getElementById('badge_'+id);if(b){b.textContent='';b.classList.remove('active');}
  });
  /* Temperature */
  document.querySelectorAll('.box_env_chk').forEach(function(c){c.checked=true;});
  /* Serial */
  document.querySelectorAll('.box_ser_chk').forEach(function(c){c.checked=true;});
  var allSer=document.getElementById('all_box_ser');if(allSer)allSer.checked=true;
  var bSer=document.getElementById('badge_box_ser');if(bSer){bSer.textContent='';bSer.classList.remove('active');}
  /* Port */
  document.querySelectorAll('.box_port_chk').forEach(function(c){c.checked=true;});
  var allPort=document.getElementById('all_box_port');if(allPort)allPort.checked=true;
  var bPort=document.getElementById('badge_box_port');if(bPort){bPort.textContent='';bPort.classList.remove('active');}
  /* Data filter */
  var allFlt=document.querySelector('input[name="box_flt"][value="all"]');
  if(allFlt)allFlt.checked=true;
  toggleRangeInputs();
  /* IQR k */
  var kEl=document.getElementById('box_iqr_k');if(kEl)kEl.value='1.5';
  /* Checkboxes */
  var exEl=document.getElementById('box_excl_room_chk');if(exEl)exEl.checked=false;
  var exEl2=document.getElementById('box_excl_denv_chk');if(exEl2)exEl2.checked=false;
  var ptEl=document.getElementById('box_show_pts_chk');if(ptEl)ptEl.checked=false;
  /* Freq filter */
  var flo=document.getElementById('box_freq_lo');if(flo)flo.value=BOX_FREQ_MIN;
  var fhi=document.getElementById('box_freq_hi');if(fhi)fhi.value=BOX_FREQ_MAX;
  /* Global filter */
  try{localStorage.removeItem('padb_v2_excluded');}catch(e){}
  _loadBoxGlobalFilter();_updateBoxGfStatus();
  update();
}

/* ---- global filter (localStorage) ---- */
var _boxGfCoarseExcluded=null;
/* Strip port suffix so GF keys store base serial (cross-plot compatible with scatter/stat_summary) */
function _boxBaseSerial(s){
  for(var i=0;i<ALL_BOX_PORTS.length;i++){var sfx='_'+ALL_BOX_PORTS[i];if(s&&s.length>sfx.length&&s.slice(-sfx.length)===sfx)return s.slice(0,-sfx.length);}
  return s||'unknown';
}
function _loadBoxGlobalFilter(){
  try{
    var raw=localStorage.getItem('padb_v2_excluded');
    if(!raw){_boxGfCoarseExcluded=null;return;}
    var obj=JSON.parse(raw);
    var serKws=['serial','unit id','dut id','s/n'];
    _boxGfCoarseExcluded=new Set();
    (obj.excluded||[]).forEach(function(k){
      var parts=k.split('||');
      if(parts.length>=2){
        var condKey=parts[1].split('|').filter(function(p){
          var lo=p.toLowerCase();
          return !serKws.some(function(kw){return lo.indexOf(kw)===0;});
        }).join('|');
        _boxGfCoarseExcluded.add(parts[0]+'||'+condKey+(parts.length>=3&&parts[2]?'|Temp='+parts[2]:''));
      }
    });
    if(!_boxGfCoarseExcluded.size) _boxGfCoarseExcluded=null;
  }catch(e){_boxGfCoarseExcluded=null;}
}
function _updateBoxGfStatus(){
  var s=document.getElementById('box_gf_status');
  var btn=document.getElementById('box_gf_mode_btn');
  var mode=(localStorage.getItem('padb_v2_gf_mode')||'exclude');
  if(btn) btn.textContent='GF Mode: '+(mode==='focus'?'Inspect ◆':'Exclude ◇');
  if(!s) return;
  try{
    var raw=localStorage.getItem('padb_v2_excluded');
    if(!raw){s.textContent='';s.style.color='#555';return;}
    var obj=JSON.parse(raw);
    var keys=obj.excluded||[];
    if(!keys.length){s.textContent='';return;}
    var dutSers=new Set();
    keys.forEach(function(k){dutSers.add(k.split('||')[0]);});
    s.textContent=keys.length+' pts in GF ('+dutSers.size+' DUTs)'+(mode==='focus'?' — INSPECT MODE':'');
    s.style.color=mode==='focus'?'#0044aa':'#900';
  }catch(e){s.textContent='';}
}
function toggleGfMode(){
  var cur=(localStorage.getItem('padb_v2_gf_mode')||'exclude');
  try{localStorage.setItem('padb_v2_gf_mode',cur==='exclude'?'focus':'exclude');}catch(e){}
  _updateBoxGfStatus();
  update();
}
/* Merge newKeys into the existing GF (union), preserving previously added entries */
function _mergeGf(newKeys){
  try{
    var raw=localStorage.getItem('padb_v2_excluded');
    var existing=raw?JSON.parse(raw).excluded||[]:[];
    var merged=new Set(existing);
    newKeys.forEach(function(k){merged.add(k);});
    localStorage.setItem('padb_v2_excluded',JSON.stringify({v:1,excluded:Array.from(merged)}));
    _loadBoxGlobalFilter();_updateBoxGfStatus();update();
  }catch(e){alert('localStorage write failed: '+e.message);}
}
/* Set currently selected filter (conditions × serials) directly as GF — no outlier threshold needed.
   Serial is in vals_detail[].s, NOT in the condition string (BOX_DATA groups without serial). */
function setFilterAsGf(){
  var selConds=getSelectedConds();
  var selBoxSers=getSelectedBoxSerials();
  var allBoxSers=getAllBoxSerials();
  var serFlt=allBoxSers.length>1&&selBoxSers.length<allBoxSers.length;
  var keys=[],seen=new Set();
  BOX_DATA.forEach(function(cd){
    if(selConds.indexOf(cd.condition)<0) return;
    var condKey=condToKey(cd.condition);
    /* Collect (baseSer, port) pairs that pass the port-qualified serial filter.
       Port is included in the GF condKey so scatter _buildCoarseKey can match it. */
    var spSeen=new Set();var condSerPorts=[];
    (cd.freq_stats||[]).forEach(function(f){
      (f.vals_detail||[]).forEach(function(d){
        if(!d.s) return;
        if(serFlt&&selBoxSers.indexOf(d.s)<0) return; /* filter by port-qualified serial */
        var baseSer=_boxBaseSerial(d.s);
        var spKey=baseSer+'\x00'+(d.p||'');
        if(!spSeen.has(spKey)){spSeen.add(spKey);condSerPorts.push({ser:baseSer,port:d.p||''});}
      });
    });
    condSerPorts.forEach(function(sp){
      var fullCondKey=_boxFullCondKey(cd.condition,sp.port);
      var k=sp.ser+'||'+fullCondKey;
      if(seen.has(k)) return;
      seen.add(k);
      keys.push(sp.ser+'||'+fullCondKey+'||manual||0');
    });
  });
  if(!keys.length){alert('No data matches the current condition and serial filter.');return;}
  _mergeGf(keys);
}
function applyGlobalFilter(){
  var selConds=getSelectedConds(),selTemps=getSelectedTemps();
  var yFlt=getYFilter(),selBoxSers=getSelectedBoxSerials();
  var outliers=_collectOutliers(selConds,selTemps,yFlt,selBoxSers);
  var keys=outliers.map(function(o){return o.key;});
  if(!keys.length){alert('No outliers at current k\xd7IQR threshold.');return;}
  window._currentOutlierKeys=keys;
  _mergeGf(keys);
}
function clearGlobalFilter(){
  try{localStorage.removeItem('padb_v2_excluded');}catch(e){}
  _loadBoxGlobalFilter();_updateBoxGfStatus();update();
}
function update(){
  var selConds=getSelectedConds();var selTemps=getSelectedTemps();var yFlt=getYFilter();
  var selBoxSers=getSelectedBoxSerials();
  Plotly.react('plot',buildBoxTraces(selConds,selTemps,yFlt,selBoxSers),buildLayout());
  updateStatsTable(selConds,yFlt,selBoxSers,selTemps);
  updateOutlierPanel(selConds,selTemps,yFlt,selBoxSers);
  updateDeltaOutlierPanel(selConds,selTemps,selBoxSers);
  saveState();
}
window.addEventListener('storage',function(e){
  if(e.key==='padb_v2_excluded'||e.key==='padb_v2_gf_mode'){_loadBoxGlobalFilter();_updateBoxGfStatus();update();}
});
/* ---- localStorage state persistence ---- */
function _stGet(k){try{return localStorage.getItem(STATE_KEY+k);}catch(e){return null;}}
function _stSet(k,v){try{localStorage.setItem(STATE_KEY+k,v);}catch(e){}}
function _stClear(){try{var keys=[];for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);if(k&&k.indexOf(STATE_KEY)===0)keys.push(k);}keys.forEach(function(k){localStorage.removeItem(k);});}catch(e){}}
function saveState(){
  var flo=document.getElementById('box_freq_lo');if(flo)_stSet('freq_lo',flo.value);
  var fhi=document.getElementById('box_freq_hi');if(fhi)_stSet('freq_hi',fhi.value);
  document.querySelectorAll('.box_env_chk').forEach(function(c){_stSet('temp_'+c.value,c.checked?'1':'0');});
  if(typeof COND_DIMS!=='undefined') COND_DIMS.forEach(function(dim){
    var col='box_cond_'+dim.col_id;
    document.querySelectorAll('.'+col).forEach(function(c){_stSet('cond_cond_'+dim.col_id+'_'+encodeURIComponent(c.value),c.checked?'1':'0');});
  });
  var fltEl=document.querySelector('input[name="box_flt"]:checked');if(fltEl)_stSet('box_filter_mode',fltEl.value);
  var yhiEl=document.getElementById('box_flt_yhi');if(yhiEl)_stSet('box_filter_yhi',yhiEl.value);
  var tllEl=document.getElementById('box_tll_hi');if(tllEl)_stSet('box_tll_hi',tllEl.value);
  var kEl=document.getElementById('box_iqr_k');if(kEl)_stSet('box_iqr_k',kEl.value);
}
function loadState(){
  var lo=_stGet('freq_lo'),hi=_stGet('freq_hi');
  if(lo!==null){var sl=document.getElementById('box_freq_lo');if(sl)sl.value=lo;}
  if(hi!==null){var sh=document.getElementById('box_freq_hi');if(sh)sh.value=hi;}
  document.querySelectorAll('.box_env_chk').forEach(function(c){var s=_stGet('temp_'+c.value);if(s!==null&&!c.disabled)c.checked=(s==='1');});
  if(typeof COND_DIMS!=='undefined') COND_DIMS.forEach(function(dim){
    var col='box_cond_'+dim.col_id;
    document.querySelectorAll('.'+col).forEach(function(c){var s=_stGet('cond_cond_'+dim.col_id+'_'+encodeURIComponent(c.value));if(s!==null)c.checked=(s==='1');});
    var chks=Array.from(document.querySelectorAll('.'+col));
    var allChk=document.getElementById('all_'+col);
    if(allChk){var n=chks.filter(function(c){return c.checked;}).length;allChk.checked=(n===chks.length);allChk.indeterminate=(n>0&&n<chks.length);}
    updateBadge(col);
  });
  if(typeof COND_DIMS!=='undefined'&&typeof _syncLfFromAllDims==='function') _syncLfFromAllDims();
  var fm=_stGet('box_filter_mode');
  if(fm){var fr=document.querySelector('input[name="box_flt"][value="'+fm+'"]');if(fr){fr.checked=true;if(typeof toggleRangeInputs==='function')toggleRangeInputs();}}
  var fyhi=_stGet('box_filter_yhi');var fyhiEl=document.getElementById('box_flt_yhi');if(fyhi!==null&&fyhiEl)fyhiEl.value=fyhi;
  var tll=_stGet('box_tll_hi');var tllEl=document.getElementById('box_tll_hi');if(tll!==null&&tllEl)tllEl.value=tll;
  var iqr=_stGet('box_iqr_k');if(iqr!==null){var kEl=document.getElementById('box_iqr_k');if(kEl)kEl.value=iqr;}
}
(function init(){
  _loadBoxGlobalFilter();
  loadState();
  var allConds=[];
  BOX_DATA.forEach(function(cd){if(allConds.indexOf(cd.condition)<0) allConds.push(cd.condition);});
  Plotly.newPlot('plot',buildBoxTraces(allConds,TEMPS_PRESENT,{mode:'all'},getAllBoxSerials()),buildLayout({mode:'all'}),{responsive:true,scrollZoom:true});
  _updateBoxGfStatus();
})();
"""


def _aggregate_box_data_by_temp(df: pd.DataFrame) -> list:
    """Raw-measurement IQR box stats grouped by (condition, temperature, frequency)."""
    sorted_freqs = sorted(df["Frequency_MHz"].dropna().unique())

    def _freq_label(f: float) -> str:
        return f"{f / 1000:.3g} GHz" if f >= 1000 else f"{f:.4g} MHz"

    def _box_stats(vals: list) -> dict:
        arr = np.sort(np.array(vals, dtype=float))
        n = len(arr)
        if n == 0:
            return {"n": 0, "mean": 0.0, "q1": 0.0, "q2": 0.0, "q3": 0.0,
                    "lo_w": 0.0, "hi_w": 0.0, "outliers": []}
        q1, q2, q3 = (float(np.percentile(arr, p)) for p in (25, 50, 75))
        iqr = q3 - q1
        inliers = arr[(arr >= q1 - 1.5 * iqr) & (arr <= q3 + 1.5 * iqr)]
        lo_w = float(inliers.min()) if len(inliers) else float(arr.min())
        hi_w = float(inliers.max()) if len(inliers) else float(arr.max())
        outliers = arr[(arr < q1 - 1.5 * iqr) | (arr > q3 + 1.5 * iqr)].tolist()
        return {
            "n": n, "mean": round(float(np.mean(arr)), 6),
            "q1": round(q1, 6), "q2": round(q2, 6), "q3": round(q3, 6),
            "lo_w": round(lo_w, 6), "hi_w": round(hi_w, 6),
            "outliers": [round(v, 6) for v in outliers],
        }

    has_ser = "_serial_id" in df.columns
    has_port = "_port" in df.columns
    results: list = []
    for cond, cdf in df.groupby("_cond", sort=True):
        for temp, tdf in cdf.groupby("Temperature"):
            freq_stats = []
            for freq in sorted_freqs:
                if has_ser:
                    cols = ["Value", "_serial_id"] + (["_port"] if has_port else [])
                    _fdf = tdf.loc[tdf["Frequency_MHz"] == freq, cols].dropna(subset=["Value"])
                    vals = _fdf["Value"].tolist()
                    vals_detail = [
                        {"s": str(row["_serial_id"]),
                         "p": str(row["_port"]) if has_port else "",
                         "v": round(float(row["Value"]), 6)}
                        for _, row in _fdf.iterrows()
                    ]
                else:
                    vals = tdf.loc[tdf["Frequency_MHz"] == freq, "Value"].dropna().tolist()
                    vals_detail = [{"s": "unknown", "p": "", "v": round(float(v), 6)} for v in vals]
                if not vals:
                    continue
                s = _box_stats(vals)
                iq = s["q3"] - s["q1"]
                lf, hf = s["q1"] - 1.5 * iq, s["q3"] + 1.5 * iq
                s["outlier_detail"] = [d for d in vals_detail if d["v"] < lf or d["v"] > hf]
                s["vals_detail"] = vals_detail
                s["freq"] = float(freq)
                s["freq_label"] = _freq_label(freq)
                s["vals"] = [round(v, 6) for v in vals]
                freq_stats.append(s)
            if freq_stats:
                results.append({"condition": str(cond), "temp": str(temp), "freq_stats": freq_stats})
    return results


def _build_box_interactive_html(
    box_data: list, stat_data_box: list, freq_cat_order: list, all_temps: list,
    cond_dims: list, lo_js: str, hi_js: str, title: str, y_label: str, y_lim, palette: list,
    all_box_serials: list = None,
    all_box_ports: list = None,
    box_cond_harm=None, harm_orders_box=None, all_conds_ordered=None,
    box_freq_min: float = 0.0, box_freq_max: float = 1.0,
    box_cond_spur=None, spur_orders_box=None,
    results_dir: str = '',
) -> str:
    css = (
        "body{font-family:Arial,sans-serif;margin:0;padding:8px;background:#fafafa;}"
        ".ctrl-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:8px 14px;background:#f0f2f5;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".env-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:5px 14px;background:#edf7ee;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".env-bar label{white-space:nowrap;cursor:pointer;}"
        ".flt-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:5px 14px;background:#f5f5e8;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".flt-bar label{white-space:nowrap;cursor:pointer;}"
        ".flt-bar input[type=number]{width:70px;font-size:13px;padding:2px 4px;"
        "border:1px solid #bbb;border-radius:3px;}"
        ".sep{border-left:2px solid #ccc;height:22px;margin:0 2px;}"
        ".filter-wrap{position:relative;display:inline-block;}"
        ".filter-btn{font-size:13px;padding:3px 10px;border:1px solid #bbb;border-radius:3px;"
        "cursor:pointer;background:#fff;white-space:nowrap;}"
        ".filter-btn:hover{background:#e8e8e8;}"
        ".filter-panel{display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:200;"
        "background:#fff;border:1px solid #ccc;border-radius:4px;"
        "box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:140px;max-height:280px;"
        "overflow-y:auto;padding:6px 8px;}"
        ".filter-panel.open{display:block;}"
        ".fitem{display:block;padding:2px 0;cursor:pointer;white-space:nowrap;font-size:13px;}"
        ".fall{padding-bottom:2px;}"
        ".fdiv{margin:4px 0;border:none;border-top:1px solid #eee;}"
        ".badge{font-size:11px;background:#0066cc;color:#fff;border-radius:10px;"
        "padding:1px 6px;margin-right:2px;display:none;}"
        ".badge.active{display:inline;}"
        ".toggle-btn{font-size:13px;padding:4px 14px;border:1px solid #bbb;border-radius:4px;"
        "cursor:pointer;background:#f0f2f5;margin:8px 4px 4px;}"
        ".toggle-btn:hover{background:#e0e2e8;}"
        ".csv-btn{font-size:13px;padding:3px 12px;border:1px solid #0066cc;border-radius:3px;"
        "cursor:pointer;background:#e8f4ff;color:#0066cc;margin-left:6px;}"
        ".csv-btn:hover{background:#cce4ff;}"
        + _CSV_DROPDOWN_CSS +
        ".stbl{border-collapse:collapse;font-size:12px;width:100%;}"
        ".stbl th{background:#e8eaf6;padding:4px 8px;text-align:left;border:1px solid #ccc;"
        "white-space:nowrap;position:sticky;top:0;}"
        ".stbl td{padding:3px 8px;border:1px solid #ddd;white-space:nowrap;}"
        ".stbl tr:hover td{background:#f5f5ff;}"
        ".out{color:#c00;font-size:11px;}"
        ".box_lf_bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;"
        "padding:5px 14px;background:#f5f5f5;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".box_lf_panel{display:flex;flex-wrap:wrap;gap:4px;padding:4px 14px;"
        "background:#fafafa;border-radius:6px;margin-bottom:4px;font-size:12px;"
        "border:1px solid #e8e8e8;max-height:200px;overflow-y:auto;}"
    )

    panels_html = ""
    for dim in cond_dims:
        col_id = "box_cond_" + dim["col_id"]
        items = "".join(
            f'<label class="fitem"><input type="checkbox" class="{col_id}"'
            f' value="{v}" checked onchange="chkChanged(\'{col_id}\')">&nbsp;{v}</label>'
            for v in dim["vals"]
        )
        panels_html += (
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'{col_id}\')">'
            f'<span id="badge_{col_id}" class="badge"></span>{dim["label"]}&thinsp;&#9662;</button>'
            f'<div class="filter-panel" id="panel_{col_id}">'
            f'<label class="fitem fall"><input type="checkbox" id="all_{col_id}"'
            f' checked onchange="toggleAll(\'{col_id}\')">&nbsp;<b>Select all</b></label>'
            f'<hr class="fdiv">{items}</div></div>\n  '
        )
    box_serial_panel_html = ""
    if all_box_serials and len(all_box_serials) > 1:
        bser_items = "".join(
            f'<label class="fitem"><input type="checkbox" class="box_ser_chk" value="{s}"'
            f' checked onchange="boxSerChkChanged()">&nbsp;{s}</label>'
            for s in all_box_serials
        )
        box_serial_panel_html = (
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'box_ser_panel\')">'
            f'Serial&thinsp;<span id="badge_box_ser" class="badge"></span>&#9662;</button>'
            f'<div class="filter-panel" id="panel_box_ser_panel">'
            f'<label class="fitem fall"><input type="checkbox" id="all_box_ser"'
            f' checked onchange="toggleAllBoxSer()"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{bser_items}</div></div>'
        )

    box_port_panel_html = ""
    if all_box_ports and len(all_box_ports) > 1:
        bport_items = "".join(
            f'<label class="fitem"><input type="checkbox" class="box_port_chk" value="{p}"'
            f' checked onchange="boxPortChkChanged()">&nbsp;{p}</label>'
            for p in all_box_ports
        )
        box_port_panel_html = (
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'box_port_panel\')">'
            f'Port&thinsp;<span id="badge_box_port" class="badge"></span>&#9662;</button>'
            f'<div class="filter-panel" id="panel_box_port_panel">'
            f'<label class="fitem fall"><input type="checkbox" id="all_box_port"'
            f' checked onchange="toggleAllBoxPort()"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{bport_items}</div></div>'
        )

    sep_div = '<div class="sep"></div>'
    ctrl_parts = []
    if cond_dims:
        ctrl_parts.append(panels_html)
    if box_serial_panel_html:
        if ctrl_parts:
            ctrl_parts.append(sep_div)
        ctrl_parts.append(box_serial_panel_html)
    if box_port_panel_html:
        if ctrl_parts:
            ctrl_parts.append(sep_div)
        ctrl_parts.append(box_port_panel_html)
    ctrl_bar = (f'<div class="ctrl-bar">\n  ' + '\n  '.join(ctrl_parts) + '\n</div>\n') if ctrl_parts else ""

    # Longform condition checkboxes — primary filter is HarmonicNumber or SpurType
    use_spur_lf = bool(spur_orders_box) and not harm_orders_box
    if use_spur_lf:
        box_lf_primary_label = "Spur Type"
        box_lf_primary_col = "SpurType"
        box_lf_opts = '<option value="all">All spur types</option>\n'
        box_lf_opts += "\n".join(
            f'<option value="{s}">{s}</option>' for s in (spur_orders_box or [])
        )
        box_lf_cond_vals = box_cond_spur or ([""] * len(all_conds_ordered or []))
    else:
        box_lf_primary_label = "Harmonic"
        box_lf_primary_col = "HarmonicNumber"
        box_lf_opts = '<option value="all">All harmonics</option>\n'
        if harm_orders_box:
            box_lf_opts += "\n".join(
                f'<option value="{h}">Harmonic {h}</option>' for h in harm_orders_box
            )
        box_lf_cond_vals = box_cond_harm or ([""] * len(all_conds_ordered or []))

    box_lf_rows = ""
    if all_conds_ordered:
        for i, cond in enumerate(all_conds_ordered):
            primary_val = box_lf_cond_vals[i] if i < len(box_lf_cond_vals) else ""
            box_lf_rows += (
                f'<div class="box_cond_lf_row" data-harm="{primary_val}">'
                f'<label style="white-space:nowrap;font-size:12px">'
                f'<input type="checkbox" class="box_cond_lf_chk" value="{cond}" checked'
                f' onchange="update()">&nbsp;{cond}</label></div>\n'
            )

    env_bar = ""
    if all_temps:
        env_items = "".join(
            f'  <label><input type="checkbox" class="box_env_chk" value="{t}" checked'
            f' onchange="update()">&nbsp;{t}</label>\n'
            for t in all_temps
        )
        env_bar = f'<div class="env-bar">\n  <b>Temperature&nbsp;steps:</b>\n{env_items}</div>\n'

    y_lo_val = y_lim[0] if y_lim else ""
    y_hi_val = y_lim[1] if y_lim else ""
    filter_bar = (
        '<div class="flt-bar">\n'
        '  <b>Data&nbsp;filter:</b>\n'
        '  <label><input type="radio" name="box_flt" value="all" checked'
        ' onchange="toggleRangeInputs();update()">&nbsp;All&nbsp;data</label>\n'
        '  <label><input type="radio" name="box_flt" value="passing"'
        ' onchange="toggleRangeInputs();update()">&nbsp;Passing&nbsp;only</label>\n'
        '  <label><input type="radio" name="box_flt" value="range"'
        ' onchange="toggleRangeInputs();update()">&nbsp;Upper&nbsp;limit</label>\n'
        '  <span id="box_flt_range_inputs" style="display:none;align-items:center;gap:4px">\n'
        f'    <input type="number" id="box_flt_yhi" placeholder="dBc limit" step="0.001"'
        f' value="{y_hi_val}" oninput="update()">\n'
        '    <small style="color:#666">(removes raw samples above limit before computing Q1/Q2/Q3/whiskers)</small>\n'
        '  </span>\n'
        '  <span class="sep"></span>\n'
        '  <label title="Override TLL for Passing only filter and draw TLL line (bypasses Spec Hi)">'
        'TLL&nbsp;override:<input type="number" id="box_tll_hi" step="0.001" placeholder="auto"'
        ' style="width:74px" oninput="update()"></label>\n'
        '  <span class="sep"></span>\n'
        '  <label title="Show non-parametric (order-statistic) TI bounds in the Statistics Table'
        ' for Non-normal and Marginal frequencies">'
        '<input type="checkbox" id="box_np_ti_chk"'
        ' onchange="updateStatsTable(getSelectedConds(),getYFilter())">'
        '&nbsp;Non-parametric&nbsp;TI</label>\n'
        '  <label title="Overlay individual DUT measurement points on each box">'
        '<input type="checkbox" id="box_show_pts_chk" onchange="update()">'
        '&nbsp;Show&nbsp;points</label>\n'
        '  <span class="sep"></span>\n'
        '  <label title="IQR fence multiplier: points beyond Q1 - k×IQR or Q3 + k×IQR are flagged as outliers">'
        'k&thinsp;&times;&thinsp;IQR:&nbsp;<input type="number" id="box_iqr_k" value="1.5"'
        ' min="0.5" max="5" step="0.1" style="width:52px" oninput="update()"></label>\n'
        '  <label title="Exclude Room outliers from Q1/Q2/Q3 calculation">'
        '<input type="checkbox" id="box_excl_room_chk" onchange="update()">'
        '&nbsp;Excl&nbsp;outliers:&nbsp;Room</label>\n'
        '  <label title="Exclude non-room temperature outliers from Q1/Q2/Q3 calculation">'
        '<input type="checkbox" id="box_excl_denv_chk" onchange="update()">'
        '&nbsp;&#916;Env&nbsp;temps</label>\n'
        '  <span class="sep"></span>\n'
        '  <label>Freq&thinsp;min&thinsp;(MHz):&thinsp;<input type="number" id="box_freq_lo"'
        f' value="{box_freq_min:.3f}" step="any"'
        ' style="width:90px;font-size:12px;padding:1px 3px;border:1px solid #bbb;border-radius:3px"'
        ' oninput="update()"></label>\n'
        '  <label>Freq&thinsp;max&thinsp;(MHz):&thinsp;<input type="number" id="box_freq_hi"'
        f' value="{box_freq_max:.3f}" step="any"'
        ' style="width:90px;font-size:12px;padding:1px 3px;border:1px solid #bbb;border-radius:3px"'
        ' oninput="update()"></label>\n'
        f'  {_csv_btn("saveBoxCSV")}\n'
        '</div>\n'
    )

    constants = "\n".join([
        f"var BOX_DATA={json.dumps(box_data)};",
        f"var BOX_STATS={json.dumps(stat_data_box)};",
        f"var BOX_TITLE={json.dumps(title)};",
        f"var BOX_FREQ_ORDER={json.dumps(freq_cat_order)};",
        f"var BOX_FREQ_MIN={box_freq_min!r};",
        f"var BOX_FREQ_MAX={box_freq_max!r};",
        f"var LO_SPEC={lo_js};",
        f"var HI_SPEC={hi_js};",
        f"var Y_LIM={json.dumps(y_lim)};",
        f"var Y_LABEL={json.dumps(y_label)};",
        f"var COND_DIMS={json.dumps(cond_dims)};",
        f"var TEMPS_PRESENT={json.dumps(all_temps)};",
        f"var PALETTE={json.dumps(palette)};",
        f"var ALL_BOX_SERIALS={json.dumps(all_box_serials or [])};",
        f"var ALL_BOX_PORTS={json.dumps(all_box_ports or [])};",
        f"var BOX_COND_HARM={json.dumps(box_cond_harm or [])};",
        f"var STATE_KEY='padb_{results_dir}';",
    ])

    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f"<style>{css}</style>\n"
        f"<script>{_get_plotlyjs()}</script>\n"
        "</head>\n<body>\n"
        + '<div class="box_lf_bar">\n'
        + f'<span style="font-weight:600;color:#444;margin-right:4px">{box_lf_primary_label}:</span>\n'
        + f'<input type="hidden" id="box_lf_primary_col" value="{box_lf_primary_col}">\n'
        + f'<select id="box_harm_sel" style="font-size:12px;padding:1px 4px;border:1px solid #bbb;border-radius:3px" onchange="updateBoxHarmonic()">\n'
        + box_lf_opts + "\n</select>\n"
        + '<button style="font-size:11px;padding:1px 7px;border:1px solid #bbb;border-radius:3px;cursor:pointer;background:#fff;margin-left:6px" onclick="selAllBoxLf(true)">All</button>\n'
        + '<button style="font-size:11px;padding:1px 7px;border:1px solid #bbb;border-radius:3px;cursor:pointer;background:#fff" onclick="selAllBoxLf(false)">None</button>\n'
        + '</div>\n'
        + (('<div class="box_lf_panel">\n' + box_lf_rows + '</div>\n') if box_lf_rows else "")
        + ctrl_bar + env_bar + filter_bar
        + '<div id="plot"></div>\n'
        + '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:4px 8px">\n'
        + '  <button class="toggle-btn" id="box_stat_toggle_btn"'
        ' onclick="toggleStatPanel()">&#9658; Statistics Table</button>\n'
        + '  <button class="toggle-btn" id="box_outlier_toggle_btn"'
        ' onclick="toggleOutlierPanel()">&#9658; Outlier Detail</button>\n'
        + '  <button class="toggle-btn" id="box_delta_toggle_btn"'
        ' onclick="toggleDeltaPanel()">&#9658; Delta Outlier Detail</button>\n'
        + '  <button class="toggle-btn"'
        ' style="background:#e8f4ff;border-color:#0066cc;color:#0066cc;font-weight:600"'
        ' title="Set currently selected conditions + serials as the global exclusion filter"'
        ' onclick="setFilterAsGf()">Set filter as GF</button>\n'
        + '  <button class="toggle-btn" id="box_apply_gf_btn"'
        ' style="background:#e8f4ff;border-color:#0066cc;color:#0066cc"'
        ' title="Set IQR outlier points as the global exclusion filter"'
        ' onclick="applyGlobalFilter()">Set outliers as GF</button>\n'
        + '  <button class="toggle-btn" id="box_apply_delta_gf_btn"'
        ' style="background:#e8f4ff;border-color:#0066cc;color:#0066cc"'
        ' onclick="applyDeltaGlobalFilter()">Set delta outliers as GF</button>\n'
        + '  <button class="toggle-btn"'
        ' style="background:#fff0f0;border-color:#c00;color:#c00"'
        ' onclick="clearGlobalFilter()">Clear global filter</button>\n'
        + '  <button class="toggle-btn"'
        ' style="background:#fff0f0;border-color:#c00;color:#c00;font-weight:600"'
        ' onclick="clearEverything()">Clear everything</button>\n'
        + '  <button class="toggle-btn" id="box_gf_mode_btn"'
        ' style="background:#f0f4ff;border-color:#6688cc;color:#0044aa"'
        ' title="Toggle GF mode: Exclude hides flagged data; Inspect shows only flagged data"'
        ' onclick="toggleGfMode()">GF Mode: Exclude ◇</button>\n'
        + '  <span id="box_gf_status" style="font-size:12px;color:#555"></span>\n'
        + '</div>\n'
        + '<div id="box_stat_panel" style="display:none;overflow-x:auto;padding:4px"></div>\n'
        + '<div id="box_outlier_panel" style="display:none;overflow-x:auto;padding:4px 8px"></div>\n'
        + '<div id="box_delta_panel" style="display:none;overflow-x:auto;padding:4px 8px"></div>\n'
        + f"<script>\n{constants}\n{_STAT_BOXPLOT_INTERACTIVE_JS}</script>\n"
        "</body>\n</html>"
    )


def _stat_boxplot_interactive(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """Fully interactive box plot — condition/temperature/Y-range filter controls."""
    df = _load_scatter_for_stats(csv_path)
    df = _parse_group_fields(df)
    title = cfg.get("title", output_html.stem)
    y_label = cfg.get("y_label", df["_val_col_name"].iloc[0] if len(df) else "Value")
    y_lim = cfg.get("y_lim")
    lo_spec, hi_spec = _get_spec(df, cfg)

    df = df.copy()
    if "Group" in df.columns and not df["Group"].isna().all():
        _serial_val = re.compile(r'^[A-Z]{2,3}\d{5,}$')
        _serial_kws = ("serial", "unit id", "dut id", "s/n")
        _port_kws   = ("port",)
        unique_groups = df["Group"].dropna().unique()
        group_kv = {g: _parse_group_kv(g) for g in unique_groups}
        all_keys = set(k for kv in group_kv.values() for k in kv)
        cond_keys: list = []
        serial_keys: list = []
        port_keys: list = []
        for key in sorted(all_keys):
            if any(kw in key.lower() for kw in _serial_kws):
                serial_keys.append(key)
                continue
            if any(kw in key.lower() for kw in _port_kws):
                port_keys.append(key)
                continue
            vals = {kv.get(key, "") for kv in group_kv.values() if key in kv}
            if vals and sum(_serial_val.match(v) is not None for v in vals) / len(vals) > 0.5:
                serial_keys.append(key)
                continue
            if 1 < len(vals) <= 50:
                cond_keys.append(key)

        def _make_cond(g: str) -> str:
            kv = group_kv.get(g, {})
            parts = [f"{k}: {kv[k]}" for k in cond_keys if k in kv]
            return "  ".join(parts) if parts else "All"

        def _make_serial(g: str) -> str:
            kv = group_kv.get(g, {})
            base = None
            for k in serial_keys:
                if k in kv:
                    base = kv[k]
                    break
            if base is None:
                base = g
            for k in port_keys:
                if k in kv:
                    base = f"{base}_{kv[k]}"
                    break
            return base

        def _make_port(g: str) -> str:
            kv = group_kv.get(g, {})
            for k in port_keys:
                if k in kv:
                    return str(kv[k])
            return ""

        df["_cond"] = df["Group"].map(_make_cond).fillna("All")
        df["_serial_id"] = df["Group"].map(_make_serial).fillna("unknown")
        df["_port"] = df["Group"].map(_make_port).fillna("")
    else:
        df["_cond"] = "All"
        df["_serial_id"] = "unknown"
        df["_port"] = ""

    sorted_freqs = sorted(df["Frequency_MHz"].dropna().unique())

    def _freq_label(f: float) -> str:
        return f"{f / 1000:.3g} GHz" if f >= 1000 else f"{f:.4g} MHz"

    df["_freq_cat"] = df["Frequency_MHz"].map({f: _freq_label(f) for f in sorted_freqs})
    freq_cat_order = [_freq_label(f) for f in sorted_freqs]

    box_data = _aggregate_box_data_by_temp(df)
    stat_data_box = _aggregate_stat_data(df, cfg)
    all_temps = sorted(df["Temperature"].dropna().unique().tolist())

    dim_vals: dict = {}
    for cd in stat_data_box:
        for part in re.split(r"  +", cd["condition"]):
            m = re.match(r"(.+?):\s*(.+?)\s*$", part.strip())
            if m:
                dim_vals.setdefault(m.group(1).strip(), set()).add(m.group(2).strip())

    def _sort_numeric(vals: set) -> list:
        try:
            return sorted(vals, key=float)
        except (ValueError, TypeError):
            return sorted(vals)

    cond_dims = [
        {"col": key, "col_id": re.sub(r"\W+", "_", key), "label": key, "vals": _sort_numeric(v)}
        for key, v in sorted(dim_vals.items()) if len(v) > 1
    ]

    # Harmonic extraction for longform panel
    _harm_re_box = re.compile(r'HarmonicNumber:\s*(\S+)', re.IGNORECASE)
    def _extract_harm_box(cond: str) -> str:
        m = _harm_re_box.search(cond)
        return m.group(1) if m else ""
    all_conds_ordered = [cd["condition"] for cd in stat_data_box]
    box_cond_harm = [_extract_harm_box(c) for c in all_conds_ordered]
    harm_orders_box = sorted(
        set(h for h in box_cond_harm if h),
        key=lambda x: float(x) if x else float("inf")
    )

    # SpurType extraction for longform panel (used when no HarmonicNumber is present)
    _spur_re_box = re.compile(r'SpurType:\s*(.+?)(?:\s{2,}|$)', re.IGNORECASE)
    def _extract_spur_box(cond: str) -> str:
        m = _spur_re_box.search(cond)
        return m.group(1).strip() if m else ""
    box_cond_spur = [_extract_spur_box(c) for c in all_conds_ordered]
    spur_orders_box = sorted(set(s for s in box_cond_spur if s))

    lo_js = "null" if np.isnan(lo_spec) else repr(float(lo_spec))
    hi_js = "null" if np.isnan(hi_spec) else repr(float(hi_spec))
    palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
               "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]

    all_box_serials = sorted({d["s"]
                              for cd in box_data
                              for fs in cd.get("freq_stats", [])
                              for d in fs.get("vals_detail", [])})
    all_box_ports = sorted({d["p"]
                            for cd in box_data
                            for fs in cd.get("freq_stats", [])
                            for d in fs.get("vals_detail", [])
                            if d.get("p")})

    all_box_freqs = sorted({fs["freq"] for cd in box_data for fs in cd.get("freq_stats", [])})
    box_freq_min = all_box_freqs[0] if all_box_freqs else 0.0
    box_freq_max = all_box_freqs[-1] if all_box_freqs else 1.0

    html = _build_box_interactive_html(
        box_data, stat_data_box, freq_cat_order, all_temps,
        cond_dims, lo_js, hi_js, title, y_label, y_lim, palette, all_box_serials,
        all_box_ports=all_box_ports,
        box_cond_harm=box_cond_harm,
        harm_orders_box=harm_orders_box,
        all_conds_ordered=all_conds_ordered,
        box_freq_min=box_freq_min,
        box_freq_max=box_freq_max,
        box_cond_spur=box_cond_spur,
        spur_orders_box=spur_orders_box,
        results_dir=cfg.get('results_dir', ''),
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


_SUMPLOT_JS = r"""
/* globals: DATA, COND_DIMS, FREQ_MIN, FREQ_MAX, HI_SPEC, LO_SPEC,
            Y_LABEL, Y_LIM, TITLE, LOG_X */
var PALETTE=['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd',
             '#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf',
             '#aec7e8','#ffbb78','#98df8a','#ff9896','#c5b0d5'];

/* ---- panel open/close ---- */
function togglePanel(col){
  var p=document.getElementById('panel_'+col);
  var open=p.classList.contains('open');
  document.querySelectorAll('.filter-panel').forEach(function(x){x.classList.remove('open');});
  if(!open)p.classList.add('open');
}
document.addEventListener('click',function(e){
  if(!e.target.closest('.filter-wrap'))
    document.querySelectorAll('.filter-panel').forEach(function(p){p.classList.remove('open');});
});

/* ---- checkbox logic ---- */
function toggleAll(col){
  var a=document.getElementById('all_'+col);
  document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){c.checked=a.checked;});
  updateBadge(col);update();
}
function chkChanged(col){
  var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
  var a=document.getElementById('all_'+col);
  var n=chks.filter(function(c){return c.checked;}).length;
  a.checked=(n===chks.length);a.indeterminate=(n>0&&n<chks.length);
  updateBadge(col);update();
}
function updateBadge(col){
  var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
  var n=chks.filter(function(c){return c.checked;}).length;
  var b=document.getElementById('badge_'+col);
  if(b){if(n<chks.length){b.textContent=n+'/'+chks.length;b.classList.add('active');}
        else b.classList.remove('active');}
}
function getSelected(col){
  return Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]:checked'))
    .map(function(c){return c.value;});
}

/* ---- log X ---- */
function isLogX(){return document.getElementById('log_x_chk').checked;}
function toggleLogX(){
  var log=isLogX();
  var lo=parseFloat(document.getElementById('freq_lo').value);
  var hi=parseFloat(document.getElementById('freq_hi').value);
  Plotly.relayout('plot',{'xaxis.type':log?'log':'linear',
    'xaxis.range':log?[Math.log10(Math.max(lo,1e-9)),Math.log10(Math.max(hi,1e-9))]:[lo,hi]});
}

/* ---- freq sliders + text entry ---- */
function syncFreq(){
  var lo=document.getElementById('freq_lo');
  var hi=document.getElementById('freq_hi');
  var loV=parseFloat(lo.value),hiV=parseFloat(hi.value);
  if(loV>hiV){lo.value=hiV;loV=hiV;}
  document.getElementById('freq_lo_txt').value=loV.toFixed(3);
  document.getElementById('freq_hi_txt').value=parseFloat(hi.value).toFixed(3);
  var log=isLogX();
  var range=log?[Math.log10(Math.max(loV,1e-9)),Math.log10(Math.max(hiV,1e-9))]:[loV,hiV];
  Plotly.relayout('plot',{'xaxis.range':range});
}
function freqTxtChange(which){
  var txt=document.getElementById('freq_'+which+'_txt');
  var slider=document.getElementById('freq_'+which);
  var v=parseFloat(txt.value);
  if(isNaN(v)){txt.value=parseFloat(slider.value).toFixed(3);return;}
  v=Math.max(parseFloat(slider.min),Math.min(parseFloat(slider.max),v));
  if(which==='lo'){var h=parseFloat(document.getElementById('freq_hi').value);if(v>h)v=h;}
  else{var l=parseFloat(document.getElementById('freq_lo').value);if(v<l)v=l;}
  txt.value=v.toFixed(3);slider.value=v;update();
}
function freqStep(which,dir){
  var fv=FREQ_VALS,txt=document.getElementById('freq_'+which+'_txt'),slider=document.getElementById('freq_'+which);
  var cur=parseFloat(txt.value),idx=-1;
  for(var i=0;i<fv.length;i++){if(Math.abs(fv[i]-cur)<0.0001){idx=i;break;}}
  if(idx<0){var best=0;for(var i=1;i<fv.length;i++){if(Math.abs(fv[i]-cur)<Math.abs(fv[best]-cur))best=i;}idx=best;}
  var ni=Math.max(0,Math.min(fv.length-1,idx+dir)),nv=fv[ni];
  if(which==='lo'){if(nv>parseFloat(document.getElementById('freq_hi').value))return;}
  else{if(nv<parseFloat(document.getElementById('freq_lo').value))return;}
  txt.value=nv.toFixed(3);slider.value=nv;update();
}
function freqKeyDown(e,which){
  if(e.key==='Enter')freqTxtChange(which);
  else if(e.key==='ArrowUp'){e.preventDefault();freqStep(which,1);}
  else if(e.key==='ArrowDown'){e.preventDefault();freqStep(which,-1);}
}
function setFreqBand(lo,hi){
  var s1=document.getElementById('freq_lo'),s2=document.getElementById('freq_hi');
  s1.value=Math.max(parseFloat(s1.min),lo);
  s2.value=Math.min(parseFloat(s2.max),hi);
  syncFreq();update();
}

/* ---- active groups ---- */
function getActive(){
  return DATA.filter(function(cd){
    return COND_DIMS.every(function(dim){
      var allowed=getSelected('cond_'+dim.col_id);
      var v=String(cd.cond_keys[dim.col]!==undefined?cd.cond_keys[dim.col]:'');
      return allowed.length>0&&allowed.indexOf(v)>=0;
    });
  });
}

/* ---- k-factor bilinear interpolation ---- */
function kLookup(n,P,C){
  if(!KT||!KT.P) return 2.0;
  var Pv=KT.P,Cv=KT.C,nv=KT.n;
  var Pi=Pv.reduce(function(b,v,i){return Math.abs(v-P)<Math.abs(Pv[b]-P)?i:b;},0);
  var Ci=Cv.reduce(function(b,v,i){return Math.abs(v-C)<Math.abs(Cv[b]-C)?i:b;},0);
  var Pi2=Math.min(Pi+1,Pv.length-1),Ci2=Math.min(Ci+1,Cv.length-1);
  var tP=Pv[Pi]===Pv[Pi2]?0:(P-Pv[Pi])/(Pv[Pi2]-Pv[Pi]);
  var tC=Cv[Ci]===Cv[Ci2]?0:(C-Cv[Ci])/(Cv[Ci2]-Cv[Ci]);
  function lookAt(pi,ci){
    var key=KT.P[pi]+'_'+KT.C[ci],arr=KT.k[key];
    if(!arr)return 2.0;
    var nC=Math.max(nv[0],Math.min(nv[nv.length-1],n));
    var idx=nC-nv[0],lo=Math.max(0,Math.min(Math.floor(idx),arr.length-2));
    var t=idx-lo;
    return arr[lo]*(1-t)+arr[lo+1]*t;
  }
  var k00=lookAt(Pi,Ci),k10=lookAt(Pi2,Ci),k01=lookAt(Pi,Ci2),k11=lookAt(Pi2,Ci2);
  return k00*(1-tP)*(1-tC)+k10*tP*(1-tC)+k01*(1-tP)*tC+k11*tP*tC;
}

/* ---- temperature filter ---- */
function getSelTemps(){
  var chks=Array.from(document.querySelectorAll('.sum_temp_chk:checked'));
  return chks.length?chks.map(function(c){return c.value;}):(TEMPS_ALL||[]);
}

/* ---- stat parameter controls ---- */
function getSumParams(){
  function _n(id,def){var el=document.getElementById(id);return el?(parseFloat(el.value)||def):def;}
  function _i(id,def){var el=document.getElementById(id);return el?(parseInt(el.value)||def):def;}
  function _nn(id){var el=document.getElementById(id);if(!el||el.value==='')return null;var v=parseFloat(el.value);return isNaN(v)?null:v;}
  return {P:_n('sum_P',0.90),C:_n('sum_C',0.90),n_override:_i('sum_n',0),
          mu:_n('sum_mu',0),denv:_n('sum_denv',0),tll_hi_override:_nn('sum_tll_hi')};
}

/* ---- get temp-filtered + param-adjusted stats for a condition ---- */
function getSumCondData(cd,selTemps,params){
  var allTemps=TEMPS_ALL||[];
  var filtering=selTemps&&allTemps.length>0&&selTemps.length<allTemps.length;
  /* No by_temp data (old record format) — use pre-computed, offset by budget */
  if(!cd.by_temp){
    if(!params.mu&&!params.denv)
      return {mean:cd.mean,min_data:cd.min_data,max_data:cd.max_data,
              uttl:cd.uttl,lttl:cd.lttl,uttl_is_estimate:cd.uttl_is_estimate||false};
    return {mean:cd.mean,min_data:cd.min_data,max_data:cd.max_data,
            uttl:cd.uttl.map(function(v){return v!==null?v+params.mu+params.denv:null;}),
            lttl:cd.lttl.map(function(v){return v!==null?v-params.mu-params.denv:null;}),
            uttl_is_estimate:true};
  }
  /* by_temp available — always recompute parametric k-TI so all controls take effect */
  var temps=filtering?selTemps:allTemps;
  var nFreqs=cd.freqs.length;
  var out_mean=[],out_min=[],out_max=[],out_uttl=[],out_lttl=[];
  for(var fi=0;fi<nFreqs;fi++){
    var tot_n=0,w_mean=0,pool_ss=0,mn=null,mx=null;
    temps.forEach(function(t){
      var bt=cd.by_temp[t];if(!bt)return;
      var n=bt.n[fi],m=bt.mean[fi],s=bt.std[fi];
      if(!n||m===null||m===undefined)return;
      w_mean+=n*m;pool_ss+=(n>1?(n-1)*s*s:0);tot_n+=n;
      if(mn===null||bt.min_data[fi]<mn)mn=bt.min_data[fi];
      if(mx===null||bt.max_data[fi]>mx)mx=bt.max_data[fi];
    });
    if(!tot_n){out_mean.push(null);out_min.push(null);out_max.push(null);
               out_uttl.push(null);out_lttl.push(null);continue;}
    var mu=w_mean/tot_n;
    var sigma=tot_n>1?Math.sqrt(pool_ss/Math.max(tot_n-1,1)):0;
    var n_use=params.n_override>0?params.n_override:tot_n;
    var k=kLookup(n_use,params.P,params.C);
    out_mean.push(Math.round(mu*1e6)/1e6);
    out_min.push(mn);out_max.push(mx);
    out_uttl.push(Math.round((mu+k*sigma+params.mu+params.denv)*1e4)/1e4);
    out_lttl.push(Math.round((mu-k*sigma-params.mu-params.denv)*1e4)/1e4);
  }
  return {mean:out_mean,min_data:out_min,max_data:out_max,
          uttl:out_uttl,lttl:out_lttl,uttl_is_estimate:true};
}

/* ---- build traces ---- */
function hexToRgba(hex,a){
  var r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
  return 'rgba('+r+','+g+','+b+','+a+')';
}
function buildTraces(active,excl){
  excl=excl||[];
  var freqLo=parseFloat(document.getElementById('freq_lo').value);
  var freqHi=parseFloat(document.getElementById('freq_hi').value);
  var traces=[];
  /* Excluded conditions — dim gray bands rendered first (behind active) */
  excl.forEach(function(cd){
    var idxs=[];
    cd.freqs.forEach(function(f,i){if(f>=freqLo&&f<=freqHi)idxs.push(i);});
    if(!idxs.length)return;
    var freqs=idxs.map(function(i){return cd.freqs[i];});
    var mins=idxs.map(function(i){return cd.min_data[i];});
    var maxs=idxs.map(function(i){return cd.max_data[i];});
    var means=idxs.map(function(i){return cd.mean[i];});
    traces.push({
      type:'scatter',
      x:freqs.concat(freqs.slice().reverse()),
      y:maxs.concat(mins.slice().reverse()),
      fill:'toself',fillcolor:'rgba(210,210,210,0.18)',
      line:{width:0.5,color:'rgba(190,190,190,0.4)'},
      showlegend:false,name:cd.condition,legendgroup:cd.condition+'_excl',
      hoverinfo:'skip'
    });
    traces.push({
      type:'scatter',x:freqs,y:means,mode:'lines',
      line:{color:'rgba(170,170,170,0.55)',width:1.2},
      name:cd.condition+' (excl)',legendgroup:cd.condition+'_excl',showlegend:false,
      hovertemplate:'<b>'+cd.condition+'</b> (excluded)<br>Freq: %{x:.4f} MHz<br>Mean: %{y:.2f}<extra></extra>'
    });
  });
  var _selTemps=getSelTemps();
  var _sumParams=getSumParams();
  active.forEach(function(cd,ci){
    var color=PALETTE[ci%PALETTE.length];
    var idxs=[];
    cd.freqs.forEach(function(f,i){if(f>=freqLo&&f<=freqHi)idxs.push(i);});
    if(!idxs.length)return;
    var freqs=idxs.map(function(i){return cd.freqs[i];});
    var _stats=getSumCondData(cd,_selTemps,_sumParams);
    var means=idxs.map(function(i){return _stats.mean[i];});
    var mins=idxs.map(function(i){return _stats.min_data[i];});
    var maxs=idxs.map(function(i){return _stats.max_data[i];});
    var uttls=idxs.map(function(i){return _stats.uttl[i];});
    var lttls=idxs.map(function(i){return _stats.lttl[i];});
    /* min-max band */
    traces.push({
      type:'scatter',
      x:freqs.concat(freqs.slice().reverse()),
      y:maxs.concat(mins.slice().reverse()),
      fill:'toself',fillcolor:hexToRgba(color,0.28),
      line:{width:0.5,color:hexToRgba(color,0.4)},
      showlegend:false,name:cd.condition,legendgroup:cd.condition,
      hoverinfo:'skip'
    });
    /* mean line */
    traces.push({
      type:'scatter',x:freqs,y:means,mode:'lines',
      line:{color:color,width:2},
      name:cd.condition,legendgroup:cd.condition,
      hovertemplate:'<b>'+cd.condition+'</b><br>Freq: %{x:.4f} MHz<br>Mean: %{y:.2f}<extra></extra>'
    });
    /* TTL upper */
    if(uttls.some(function(v){return v!==null&&v!==undefined;})){
      var ttlLabel=_stats.uttl_is_estimate?' TTL↑ (est)':' TTL↑';
      traces.push({
        type:'scatter',x:freqs,y:uttls,mode:'lines',
        line:{color:color,width:1.5,dash:_stats.uttl_is_estimate?'dot':'dash'},
        name:cd.condition+ttlLabel,legendgroup:cd.condition,showlegend:false,
        hovertemplate:'<b>'+cd.condition+'</b><br>Freq: %{x:.4f} MHz<br>'+ttlLabel.trim()+': %{y:.2f}<extra></extra>'
      });
    }
  });
  /* spec lines — per-frequency, drawn as segments per unique spec value */
  var fLo=parseFloat(document.getElementById('freq_lo').value);
  var fHi=parseFloat(document.getElementById('freq_hi').value);
  // specRanges[val] = {fMin, fMax} — frequency extent where that spec value applies
  function buildSpecRanges(listKey,fallback){
    var ranges={};
    active.forEach(function(cd){
      var slist=cd[listKey]||[];
      cd.freqs.forEach(function(f,i){
        if(f<fLo||f>fHi) return;
        var sv=slist[i];
        if(sv===null||sv===undefined) return;
        var k=Math.round(sv*100)/100;
        if(!ranges[k]) ranges[k]={fMin:f,fMax:f};
        else{ranges[k].fMin=Math.min(ranges[k].fMin,f);ranges[k].fMax=Math.max(ranges[k].fMax,f);}
      });
    });
    if(!Object.keys(ranges).length&&fallback!==null)
      ranges[Math.round(fallback*100)/100]={fMin:fLo,fMax:fHi};
    return ranges;
  }
  var hiRanges=buildSpecRanges('spec_hi_list',HI_SPEC);
  var loRanges=buildSpecRanges('spec_lo_list',LO_SPEC);
  Object.keys(hiRanges).map(Number).sort(function(a,b){return a-b;}).forEach(function(v){
    var r=hiRanges[v];
    traces.push({type:'scatter',x:[r.fMin,r.fMax],y:[v,v],mode:'lines',
      line:{color:'red',dash:'dash',width:1.5},name:'Spec Hi '+v,
      hovertemplate:'Spec Hi: '+v.toFixed(4)+'<extra></extra>'});
  });
  Object.keys(loRanges).map(Number).sort(function(a,b){return b-a;}).forEach(function(v){
    var r=loRanges[v];
    traces.push({type:'scatter',x:[r.fMin,r.fMax],y:[v,v],mode:'lines',
      line:{color:'red',dash:'dash',width:1.5},name:'Spec Lo '+v,
      hovertemplate:'Spec Lo: '+v.toFixed(4)+'<extra></extra>'});
  });
  /* Manual TLL override line */
  var _sumPar=getSumParams();
  if(_sumPar.tll_hi_override!==null&&isFinite(fLo)&&isFinite(fHi))
    traces.push({type:'scatter',x:[fLo,fHi],y:[_sumPar.tll_hi_override,_sumPar.tll_hi_override],
      mode:'lines',line:{color:'darkred',dash:'dot',width:2},name:'TLL↑ (manual)',
      hovertemplate:'TLL↑ (manual): '+_sumPar.tll_hi_override.toFixed(4)+'<extra></extra>'});
  return traces;
}

function buildLayout(){
  var log=isLogX();
  var lo=parseFloat(document.getElementById('freq_lo').value);
  var hi=parseFloat(document.getElementById('freq_hi').value);
  var range=log?[Math.log10(Math.max(lo,1e-9)),Math.log10(Math.max(hi,1e-9))]:[lo,hi];
  return {
    title:{text:TITLE,x:0.5,font:{size:15}},
    template:'plotly_white',
    xaxis:{title:'Frequency (MHz)',type:log?'log':'linear',range:range},
    yaxis:{title:Y_LABEL,range:Y_LIM},
    height:520,
    legend:{bgcolor:'rgba(255,255,255,0.8)',bordercolor:'#ccc',borderwidth:1},
    margin:{l:60,r:30,t:60,b:60}
  };
}

/* ---- reset ---- */
function resetFilters(){
  _stClear();
  COND_DIMS.forEach(function(dim){
    var col='cond_'+dim.col_id;
    document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){c.checked=true;});
    var a=document.getElementById('all_'+col);
    if(a){a.checked=true;a.indeterminate=false;}
    var b=document.getElementById('badge_'+col);
    if(b)b.classList.remove('active');
  });
  document.getElementById('freq_lo').value=FREQ_MIN;
  document.getElementById('freq_hi').value=FREQ_MAX;
  document.getElementById('freq_lo_txt').value=parseFloat(FREQ_MIN).toFixed(3);
  document.getElementById('freq_hi_txt').value=parseFloat(FREQ_MAX).toFixed(3);
  var allRad=document.querySelector('input[name="sum_flt"][value="all"]');
  if(allRad){allRad.checked=true;toggleRangeInputs();}
  var yhi=document.getElementById('sum_yhi');if(yhi)yhi.value='';
  var ec=document.getElementById('sum_show_excl_chk');if(ec)ec.checked=false;
  document.querySelectorAll('.sum_temp_chk').forEach(function(c){c.checked=true;});
  var pEl=document.getElementById('sum_P');if(pEl)pEl.value='0.95';
  var cEl=document.getElementById('sum_C');if(cEl)cEl.value='0.90';
  var nEl=document.getElementById('sum_n');if(nEl)nEl.value='0';
  var muEl=document.getElementById('sum_mu');if(muEl)muEl.value='0';
  var dvEl=document.getElementById('sum_denv');if(dvEl)dvEl.value='0';
  update();
}

/* ---- data filter (pass/Y-range) ---- */
function getDataFilter(){
  var mode='all';
  document.querySelectorAll('input[name="sum_flt"]').forEach(function(r){if(r.checked)mode=r.value;});
  var yhi=parseFloat(document.getElementById('sum_yhi').value);
  return {mode:mode,yhi:isNaN(yhi)?Infinity:yhi};
}
function toggleRangeInputs(){
  var el=document.getElementById('sum_range_inputs');
  if(el){var r=document.querySelector('input[name="sum_flt"][value="range"]');
    el.style.display=(r&&r.checked)?'inline-flex':'none';}
}
function applyDataFilter(active){
  var flt=getDataFilter();
  if(flt.mode==='all') return active;
  var fLo=parseFloat(document.getElementById('freq_lo').value);
  var fHi=parseFloat(document.getElementById('freq_hi').value);
  return active.filter(function(cd){
    var vis=[];cd.freqs.forEach(function(f,i){if(f>=fLo&&f<=fHi)vis.push(i);});
    if(!vis.length) return false;
    if(flt.mode==='passing'){
      var sumPar=getSumParams();
      var tllHiOv=sumPar.tll_hi_override;
      return vis.every(function(i){
        var hi=tllHiOv!==null?tllHiOv:((cd.spec_hi_list&&cd.spec_hi_list[i]!=null)?cd.spec_hi_list[i]:cd.spec_hi);
        var lo=(cd.spec_lo_list&&cd.spec_lo_list[i]!=null)?cd.spec_lo_list[i]:cd.spec_lo;
        var uOk=(hi===null||cd.uttl[i]===null||Number(cd.uttl[i])<=hi);
        var lOk=(lo===null||cd.lttl[i]===null||Number(cd.lttl[i])>=lo);
        return uOk&&lOk;
      });
    }
    if(flt.mode==='range'){
      return vis.some(function(i){
        return cd.max_data[i]<=flt.yhi;
      });
    }
    return true;
  });
}
function saveCSV(withExcluded){
  var savedGf=_sumGfExcluded;
  var active=_getFilteredActive(!withExcluded);
  var fLo=parseFloat(document.getElementById('freq_lo').value);
  var fHi=parseFloat(document.getElementById('freq_hi').value);
  var rows=['Condition,Freq_MHz,Mean,Min,Max,TTL_upper,TTL_lower,Spec_hi,Spec_lo'];
  function esc(v){var s=String(v==null?'':v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;}
  active.forEach(function(cd){
    cd.freqs.forEach(function(f,i){
      if(f<fLo||f>fHi) return;
      var hi=(cd.spec_hi_list&&cd.spec_hi_list[i]!=null)?cd.spec_hi_list[i]:cd.spec_hi;
      var lo=(cd.spec_lo_list&&cd.spec_lo_list[i]!=null)?cd.spec_lo_list[i]:cd.spec_lo;
      rows.push([
        esc(cd.condition),
        f.toFixed(4),
        cd.mean[i]!=null?Number(cd.mean[i]).toFixed(4):'',
        cd.min_data[i]!=null?Number(cd.min_data[i]).toFixed(4):'',
        cd.max_data[i]!=null?Number(cd.max_data[i]).toFixed(4):'',
        cd.uttl[i]!=null?Number(cd.uttl[i]).toFixed(4):'',
        cd.lttl[i]!=null?Number(cd.lttl[i]).toFixed(4):'',
        hi!=null?hi:'',
        lo!=null?lo:''
      ].join(','));
    });
  });
  /* Metadata block */
  var ts=new Date().toISOString().replace('T',' ').replace(/\.\d+Z$/,' UTC');
  var gfSers=[];
  if(savedGf&&savedGf.size>0) savedGf.forEach(function(k){var s=k.split('||')[0];if(gfSers.indexOf(s)<0) gfSers.push(s);});
  var activeConds=active.map(function(cd){return cd.condition;});
  var meta=['# PADB Export','# Plot: '+TITLE,'# Generated: '+ts,
    '# Export: '+(withExcluded?'Filtered + excluded (GF bypassed; aggregated stats reflect all non-serial-filtered DUTs)':'Filtered data'),
    '# Freq range: '+fLo.toFixed(2)+' - '+fHi.toFixed(2)+' MHz',
    '# Active conditions ('+activeConds.length+'): '+activeConds.join(', '),
    '# GF excluded DUTs ('+gfSers.length+'): '+(gfSers.length?gfSers.join(', '):'None'),
    '#'
  ].join('\r\n');
  var blob=new Blob([meta+'\r\n'+rows.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  var suffix=withExcluded?'_with_excl':'_filtered';
  a.href=url;a.download=(TITLE+suffix).replace(/[^a-zA-Z0-9_\-]/g,'_')+'.csv';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/* ---- global filter (localStorage) ---- */
var _sumGfExcluded=null;
var _sumGfCoarseExcluded=null;
/* Convert "Key: Value  Key2: Value2" cond string to "Key=Value|Key2=Value2" without serial parts */
function _sumCoarseCondKey(cond){
  /* Use same format as condToKey() — preserve spaces in key names so GF entries match.
     Also strip serial and temperature dimensions (summary aggregates both away). */
  var stripKws=['serial','unit id','dut id','s/n','temperature','temp','deg c','deg f'];
  return (cond||'').split(/  +/).map(function(p){
    return p.replace(/\s*:\s*/,'=').trim();
  }).filter(function(p){
    if(!p) return false;
    var lo=p.toLowerCase();
    return !stripKws.some(function(kw){return lo.indexOf(kw)===0;});
  }).sort().join('|');
}
function _loadSumGlobalFilter(){
  try{
    var raw=localStorage.getItem('padb_v2_excluded');
    if(!raw){_sumGfExcluded=null;_sumGfCoarseExcluded=null;}
    else{
      var obj=JSON.parse(raw);
      _sumGfExcluded=new Set(obj.excluded||[]);
      _sumGfCoarseExcluded=new Set();
      /* Strip serial AND temperature from GF condKey — summary aggregates both.
         Temperature is included in boxplot GF keys but not in summary conditions. */
      var stripKws=['serial','unit id','dut id','s/n','temperature','temp','deg c','deg f'];
      _sumGfExcluded.forEach(function(k){
        var parts=k.split('||');
        if(parts.length>=2){
          var coarseCond=parts[1].split('|').filter(function(p){
            var lo=p.toLowerCase();
            return !stripKws.some(function(kw){return lo.indexOf(kw)===0;});
          }).join('|');
          _sumGfCoarseExcluded.add(parts[0]+'||'+coarseCond);
        }
      });
    }
  }catch(e){_sumGfExcluded=null;_sumGfCoarseExcluded=null;}
  _updateSumGfBadge();
}
function _updateSumGfBadge(){
  var el=document.getElementById('sum_gf_badge');
  if(!el) return;
  if(!_sumGfExcluded||!_sumGfExcluded.size){el.textContent='';el.style.display='none';return;}
  var dutSers=new Set();
  _sumGfExcluded.forEach(function(k){dutSers.add(k.split('||')[0]);});
  var n=dutSers.size,pts=_sumGfExcluded.size;
  var mode=(localStorage.getItem('padb_v2_gf_mode')||'exclude');
  var isFocus=mode==='focus';
  el.textContent=n>0?(isFocus?'Inspect: ':'')+pts+' pts in GF ('+n+' DUT'+(n!==1?'s':')')+(isFocus?' — INSPECT':''):'';
  el.style.background=isFocus?'#e8f0ff':'#ffeaea';
  el.style.borderColor=isFocus?'#6688cc':'#c88';
  el.style.color=isFocus?'#0044aa':'#900';
  el.style.display=n>0?'':'none';
}
window.addEventListener('storage',function(e){
  if(e.key==='padb_v2_excluded'||e.key==='padb_v2_gf_mode'){_loadSumGlobalFilter();update();}
});
/* Extract serial from a condition string (Serial Number: X pattern) */
function _sumSerFromCond(cond){
  var m=(cond||'').match(/Serial Number:\s*(\S+)/i);
  return m?m[1]:null;
}
/* ---- filtered active helper (useGf defaults to sum_gf_chk state) ---- */
function _getFilteredActive(useGf){
  var active=applyDataFilter(getActive());
  if(useGf===undefined){var t=document.getElementById('sum_gf_chk');useGf=!t||t.checked;}
  if(useGf&&_sumGfCoarseExcluded&&_sumGfCoarseExcluded.size){
    /* Summary conditions are always aggregate (render_summary strips serial from cond_cols).
       The GF is a per-DUT serial filter — it cannot meaningfully hide an aggregate condition
       that spans all DUTs just because one DUT was excluded in another view.
       Only apply GF filtering to conditions that embed a serial number in their label
       (future-proof: if a summary is ever broken out per-serial). */
    var _isGfCond=function(cd){
      var condSerial=_sumSerFromCond(cd.condition);
      if(!condSerial) return false;
      var coarseCond=_sumCoarseCondKey(cd.condition);
      return _sumGfCoarseExcluded.has(condSerial+'||'+coarseCond);
    };
    var focus=(localStorage.getItem('padb_v2_gf_mode')||'exclude')==='focus';
    if(focus){
      active=active.filter(function(cd){return _isGfCond(cd);});
    } else {
      active=active.filter(function(cd){return !_isGfCond(cd);});
    }
  }
  return active;
}
/* ---- results table helpers ---- */
function _buildCondRows(condList,gfLabel,selTemps,params){
  var fLo=parseFloat(document.getElementById('freq_lo').value);
  var fHi=parseFloat(document.getElementById('freq_hi').value);
  var rows=[];
  condList.forEach(function(cd){
    var stats=getSumCondData(cd,selTemps,params);
    cd.freqs.forEach(function(f,fi){
      if(f<fLo||f>fHi) return;
      var tot_n=0;
      selTemps.forEach(function(t){
        var bt=cd.by_temp&&cd.by_temp[t];
        if(bt&&bt.n&&bt.n[fi]) tot_n+=bt.n[fi];
      });
      var sHi=(cd.spec_hi_list&&cd.spec_hi_list[fi]!=null)?cd.spec_hi_list[fi]:
              (cd.spec_hi!==undefined&&cd.spec_hi!==null?cd.spec_hi:null);
      var tUp=stats.uttl[fi];
      rows.push({
        condition:cd.condition,freq:f,n:tot_n,gf:gfLabel,
        mean:stats.mean[fi],min:stats.min_data[fi],max:stats.max_data[fi],
        ttl_up:tUp,mu:params.mu,denv:params.denv,spec_hi:sHi,
        margin_up:(sHi!==null&&tUp!==null)?sHi-tUp:null,
      });
    });
  });
  return rows;
}
function buildTable(){
  var wrap=document.getElementById('sum_table_wrap');
  if(!wrap) return;
  var active=_getFilteredActive();
  var selTemps=getSelTemps();
  var params=getSumParams();
  var rows=_buildCondRows(active,'',selTemps,params);
  if(!rows.length){
    wrap.innerHTML='<p style="color:#888;font-size:12px;margin:4px 8px">No data in current view.</p>';
    return;
  }
  function fmt(v,d){return v===null||v===undefined?'—':Number(v).toFixed(d!==undefined?d:4);}
  var cols=['Condition','Freq (MHz)','n','Mean','Min','Max',
            'TTL↑','M.U.','ΔEnv','Spec Hi','Margin↑'];
  var th=cols.map(function(c){
    return '<th style="border:1px solid #ccc;padding:3px 8px;background:#f0f2f5;white-space:nowrap">'
           +c+'</th>';
  }).join('');
  var trs=rows.map(function(r){
    var fail=r.margin_up!==null&&r.margin_up<0;
    var bg=fail?'background:#ffe8e8;border-left:3px solid #cc0000;':'';
    function td(v,d){
      return '<td style="border:1px solid #eee;padding:2px 8px;text-align:right;white-space:nowrap">'
             +fmt(v,d)+'</td>';
    }
    function tdL(v){
      return '<td style="border:1px solid #eee;padding:2px 8px;white-space:nowrap">'+v+'</td>';
    }
    var mColor=r.margin_up===null?'':r.margin_up>=0?'color:#006600;font-weight:bold':'color:#cc0000;font-weight:bold';
    var mStr=r.margin_up===null?'—':((r.margin_up>=0?'+':'')+r.margin_up.toFixed(4)+' '
              +(r.margin_up>=0?'\u2714':'\u2718'));
    return '<tr style="'+bg+'">'
      +tdL(r.condition)
      +td(r.freq,4)+td(r.n,0)
      +td(r.mean,4)+td(r.min,4)+td(r.max,4)
      +td(r.ttl_up,4)+td(r.mu,4)+td(r.denv,4)
      +td(r.spec_hi,4)
      +'<td style="border:1px solid #eee;padding:2px 8px;white-space:nowrap;'+mColor+'">'+mStr+'</td>'
      +'</tr>';
  });
  wrap.innerHTML='<table style="border-collapse:collapse;width:100%;font-size:12px">'
    +'<thead><tr>'+th+'</tr></thead>'
    +'<tbody>'+trs.join('')+'</tbody></table>';
}
function exportTableCSV(){
  var active=_getFilteredActive();
  var excluded=DATA.filter(function(cd){return active.indexOf(cd)<0;});
  var selTemps=getSelTemps();
  var params=getSumParams();
  var rows=_buildCondRows(active,'',selTemps,params)
           .concat(_buildCondRows(excluded,'GF Excluded',selTemps,params));
  rows.sort(function(a,b){
    if(a.condition<b.condition) return -1;
    if(a.condition>b.condition) return 1;
    return a.freq-b.freq;
  });
  function fv(v,d){return v===null||v===undefined?'':Number(v).toFixed(d!==undefined?d:4);}
  var hdrs=['Condition','GF_Status','Freq (MHz)','n','Mean','Min','Max',
            'TTL Up','MU','DEnv','Spec Hi','Margin Up'];
  var lines=[hdrs.join(',')];
  rows.forEach(function(r){
    var cond='"'+String(r.condition).replace(/"/g,'""')+'"';
    var gf='"'+(r.gf||'')+'"';
    lines.push([cond,gf,fv(r.freq),r.n,fv(r.mean),fv(r.min),fv(r.max),
                fv(r.ttl_up),fv(r.mu),fv(r.denv),
                fv(r.spec_hi),fv(r.margin_up)].join(','));
  });
  var blob=new Blob([lines.join('\r\n')],{type:'text/csv'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=(typeof TITLE!=='undefined'?TITLE.replace(/[^\w-]/g,'_'):'summary')+'_table.csv';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
}
/* ---- main update ---- */
function update(){
  var active=_getFilteredActive();
  document.getElementById('n_groups').textContent=active.length+' groups';
  var showExcl=document.getElementById('sum_show_excl_chk');
  showExcl=showExcl?showExcl.checked:false;
  var excl=showExcl?DATA.filter(function(cd){return active.indexOf(cd)<0;}):[];
  Plotly.react('plot',buildTraces(active,excl),buildLayout());
  var rb=document.getElementById('sum_refresh_table_btn');if(rb)rb.textContent='Refresh table (stale)';
  saveState();
}

/* ---- localStorage state persistence ---- */
function _stGet(k){try{return localStorage.getItem(STATE_KEY+k);}catch(e){return null;}}
function _stSet(k,v){try{localStorage.setItem(STATE_KEY+k,v);}catch(e){}}
function _stClear(){try{var keys=[];for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);if(k&&k.indexOf(STATE_KEY)===0)keys.push(k);}keys.forEach(function(k){localStorage.removeItem(k);});}catch(e){}}
function saveState(){
  _stSet('freq_lo',document.getElementById('freq_lo').value);
  _stSet('freq_hi',document.getElementById('freq_hi').value);
  document.querySelectorAll('.sum_temp_chk').forEach(function(c){_stSet('temp_'+c.value,c.checked?'1':'0');});
  if(typeof COND_DIMS!=='undefined') COND_DIMS.forEach(function(dim){
    var col='cond_'+dim.col_id;
    document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){_stSet('cond_'+col+'_'+encodeURIComponent(c.value),c.checked?'1':'0');});
  });
  var fltEl=document.querySelector('input[name="sum_flt"]:checked');if(fltEl)_stSet('sum_filter_mode',fltEl.value);
  var yhiEl=document.getElementById('sum_yhi');if(yhiEl)_stSet('sum_filter_yhi',yhiEl.value);
  var tllEl=document.getElementById('sum_tll_hi');if(tllEl)_stSet('sum_tll_hi',tllEl.value);
  var pEl=document.getElementById('sum_P');if(pEl)_stSet('sum_P',pEl.value);
  var cEl=document.getElementById('sum_C');if(cEl)_stSet('sum_C',cEl.value);
  var nEl=document.getElementById('sum_n');if(nEl)_stSet('sum_n',nEl.value);
  var muEl=document.getElementById('sum_mu');if(muEl)_stSet('sum_mu',muEl.value);
  var dvEl=document.getElementById('sum_denv');if(dvEl)_stSet('sum_denv',dvEl.value);
}
function loadState(){
  var lo=_stGet('freq_lo'),hi=_stGet('freq_hi');
  if(lo!==null){var sl=document.getElementById('freq_lo');if(sl){sl.value=lo;var tx=document.getElementById('freq_lo_txt');if(tx)tx.value=parseFloat(lo).toFixed(3);}}
  if(hi!==null){var sh=document.getElementById('freq_hi');if(sh){sh.value=hi;var th=document.getElementById('freq_hi_txt');if(th)th.value=parseFloat(hi).toFixed(3);}}
  document.querySelectorAll('.sum_temp_chk').forEach(function(c){var s=_stGet('temp_'+c.value);if(s!==null&&!c.disabled)c.checked=(s==='1');});
  if(typeof COND_DIMS!=='undefined') COND_DIMS.forEach(function(dim){
    var col='cond_'+dim.col_id;
    document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){var s=_stGet('cond_'+col+'_'+encodeURIComponent(c.value));if(s!==null)c.checked=(s==='1');});
    var chks=Array.from(document.querySelectorAll('.fchk[data-col="'+col+'"]'));
    var allChk=document.getElementById('all_'+col);
    if(allChk){var n=chks.filter(function(c){return c.checked;}).length;allChk.checked=(n===chks.length);allChk.indeterminate=(n>0&&n<chks.length);}
    updateBadge(col);
  });
  var fm=_stGet('sum_filter_mode');
  if(fm){var fr=document.querySelector('input[name="sum_flt"][value="'+fm+'"]');if(fr){fr.checked=true;if(typeof toggleRangeInputs==='function')toggleRangeInputs();}}
  var fyhi=_stGet('sum_filter_yhi');var fyhiEl=document.getElementById('sum_yhi');if(fyhi!==null&&fyhiEl)fyhiEl.value=fyhi;
  var tll=_stGet('sum_tll_hi');var tllEl=document.getElementById('sum_tll_hi');if(tll!==null&&tllEl)tllEl.value=tll;
  var sp=_stGet('sum_P');if(sp!==null){var pEl=document.getElementById('sum_P');if(pEl)pEl.value=sp;var lpEl=document.getElementById('lbl_sum_P');if(lpEl)lpEl.value=sp;}
  var sc=_stGet('sum_C');if(sc!==null){var cEl=document.getElementById('sum_C');if(cEl)cEl.value=sc;var lcEl=document.getElementById('lbl_sum_C');if(lcEl)lcEl.value=sc;}
  var sn=_stGet('sum_n');if(sn!==null){var nEl=document.getElementById('sum_n');if(nEl)nEl.value=sn;}
  var smu=_stGet('sum_mu');if(smu!==null){var muEl=document.getElementById('sum_mu');if(muEl)muEl.value=smu;}
  var sdv=_stGet('sum_denv');if(sdv!==null){var dvEl=document.getElementById('sum_denv');if(dvEl)dvEl.value=sdv;}
}

_loadSumGlobalFilter();
loadState();
var _sumInitActive=_getFilteredActive();
Plotly.newPlot('plot',buildTraces(_sumInitActive,[]),buildLayout());
document.getElementById('n_groups').textContent=_sumInitActive.length+' groups';
buildTable();
"""


def _build_summary_html(
    records: list,
    cond_dims: list,
    cfg: dict,
    output_html: Path,
    *,
    hi_spec: float = float("nan"),
    lo_spec: float = float("nan"),
    freq_min: float,
    freq_max: float,
    freq_vals: list,
    temps_all: list = [],
) -> None:
    """Assemble and write the interactive summary-plot HTML from pre-built records."""
    title   = cfg.get("title", output_html.stem)
    y_label = cfg.get("y_label", "Level (dBc)")
    y_lim   = cfg.get("y_lim")

    log_x_cfg = cfg.get("log_x")
    log_x = bool(log_x_cfg) if log_x_cfg is not None else (
        freq_min > 0 and freq_max / freq_min >= 100
    )

    lo_js = "null" if np.isnan(lo_spec) else repr(float(lo_spec))
    hi_js = "null" if np.isnan(hi_spec) else repr(float(hi_spec))

    freq_step = max(round((freq_max - freq_min) / 1000, 4), 0.001)

    panels: list[str] = []
    for dim in cond_dims:
        pid   = "cond_" + dim["col_id"]
        items = "".join(
            f'<label class="fitem"><input type="checkbox" class="fchk" data-col="{pid}"'
            f' value="{v}" checked onchange="chkChanged(\'{pid}\')">{v}</label>'
            for v in dim["vals"]
        )
        panels.append(
            f'<div class="filter-wrap">'
            f'<button class="filter-btn" onclick="togglePanel(\'{pid}\')">'
            f'{dim["label"]}&thinsp;<span id="badge_{pid}" class="badge"></span>&#9662;</button>'
            f'<div class="filter-panel" id="panel_{pid}">'
            f'<label class="fitem fall"><input type="checkbox" id="all_{pid}"'
            f' checked onchange="toggleAll(\'{pid}\')"><b>Select&nbsp;all</b></label>'
            f'<hr class="fdiv">{items}</div></div>'
        )
    panels_html = "\n  ".join(panels)

    freq_bands = cfg.get("freq_bands", [])
    band_btns_html = ""
    for _b in freq_bands:
        band_btns_html += (
            f'  <button class="reset-btn" onclick="setFreqBand({_b["lo"]},{_b["hi"]})">'
            f'{_b["label"]}</button>\n'
        )
    band_section_html = (f'  <div class="sep"></div>\n{band_btns_html}') if freq_bands else ""

    k_table = _build_k_table()
    constants = "\n".join([
        f"var DATA={json.dumps(records)};",
        f"var COND_DIMS={json.dumps(cond_dims)};",
        f"var KT={json.dumps(k_table)};",
        f"var TEMPS_ALL={json.dumps(list(temps_all))};",
        f"var HI_SPEC={hi_js};",
        f"var LO_SPEC={lo_js};",
        f"var Y_LABEL={json.dumps(y_label)};",
        f"var Y_LIM={json.dumps(y_lim)};",
        f"var TITLE={json.dumps(title)};",
        f"var LOG_X={'true' if log_x else 'false'};",
        f"var FREQ_MIN={freq_min!r};",
        f"var FREQ_MAX={freq_max!r};",
        f"var FREQ_VALS={json.dumps(sorted(float(f) for f in freq_vals))};",
        f"var STATE_KEY='padb_{cfg.get('results_dir', '')}';",
    ])

    css = (
        "body{font-family:Arial,sans-serif;margin:0;padding:8px;}"
        ".ctrl-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:8px 14px;background:#f0f2f5;border-radius:6px;margin-bottom:8px;font-size:13px;}"
        ".ctrl-bar label{white-space:nowrap;}"
        ".ctrl-bar input[type=range]{vertical-align:middle;width:100px;}"
        "input.freq-txt{font-size:12px;width:72px;padding:1px 3px;border:1px solid #bbb;"
        "border-radius:3px;text-align:right;margin-left:2px;}"
        ".sep{border-left:2px solid #ccc;height:22px;margin:0 2px;}"
        ".filter-wrap{position:relative;display:inline-block;}"
        ".filter-btn{font-size:13px;padding:3px 10px;border:1px solid #bbb;border-radius:3px;"
        "cursor:pointer;background:#fff;white-space:nowrap;}"
        ".filter-btn:hover{background:#e8e8e8;}"
        ".filter-panel{display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:200;"
        "background:#fff;border:1px solid #ccc;border-radius:4px;"
        "box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:160px;max-height:280px;"
        "overflow-y:auto;padding:6px 8px;}"
        ".filter-panel.open{display:block;}"
        ".fitem{display:block;padding:2px 0;cursor:pointer;white-space:nowrap;font-size:13px;}"
        ".fall{padding-bottom:2px;}"
        ".fdiv{margin:4px 0;border:none;border-top:1px solid #eee;}"
        ".badge{font-size:11px;background:#0066cc;color:#fff;border-radius:10px;"
        "padding:1px 6px;margin-right:2px;display:none;}"
        ".badge.active{display:inline;}"
        "button.reset-btn{font-size:12px;padding:2px 10px;border:1px solid #999;"
        "border-radius:3px;cursor:pointer;background:#fff;}"
        "button.reset-btn:hover{background:#e8e8e8;}"
        "#n_groups{font-size:12px;color:#666;margin-left:auto;}"
        ".flt-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:5px 14px;background:#f5f5e8;border-radius:6px;margin-bottom:4px;font-size:13px;}"
        ".flt-bar label{white-space:nowrap;cursor:pointer;}"
        ".flt-bar input[type=number]{width:72px;font-size:13px;padding:2px 4px;"
        "border:1px solid #bbb;border-radius:3px;}"
        ".csv-btn{font-size:13px;padding:3px 12px;border:1px solid #0066cc;border-radius:3px;"
        "cursor:pointer;background:#e8f4ff;color:#0066cc;}"
        ".csv-btn:hover{background:#cce4ff;}"
        + _CSV_DROPDOWN_CSS
    )

    sep = '<div class="sep"></div>'
    if temps_all:
        _temp_section = ""
        if len(temps_all) > 1:
            _temp_chks = "".join(
                f'  <label style="white-space:nowrap"><input type="checkbox" class="sum_temp_chk"'
                f' value="{t}" checked onchange="update()">&nbsp;{t}</label>\n'
                for t in temps_all
            )
            _temp_section = (
                '  <b>Temperature:</b>&nbsp;\n'
                + _temp_chks
                + f'  {sep}\n'
            )
        temp_stat_bar_html = (
            '<div class="flt-bar" onclick="event.stopPropagation()">\n'
            + _temp_section
            + '  <b>Stat:</b>&nbsp;\n'
            + '  <label>P:&nbsp;<select id="sum_P" onchange="update()">'
            + '<option value="0.80">80%</option>'
            + '<option value="0.95" selected>95%</option>'
            + '<option value="0.9973">99.73%</option>'
            + '</select></label>\n'
            + '  <label>C:&nbsp;<select id="sum_C" onchange="update()">'
            + '<option value="0.90" selected>90%</option>'
            + '<option value="0.95">95%</option>'
            + '</select></label>\n'
            + '  <label title="Override sample size (0=auto)">n override:'
            + '<input type="number" id="sum_n" min="0" max="9999" value="0"'
            + ' style="width:55px" oninput="update()"></label>\n'
            + f'  {sep}\n'
            + '  <label title="Measurement Uncertainty (dB)">M.U.:'
            + '<input type="number" id="sum_mu" value="0" min="0" step="0.001"'
            + ' style="width:60px" oninput="update()"></label>\n'
            + '  <label title="Environmental Delta (dB)">&#916;Env:'
            + '<input type="number" id="sum_denv" value="0" min="0" step="0.001"'
            + ' style="width:60px" oninput="update()"></label>\n'
            + '</div>\n'
        )
    else:
        temp_stat_bar_html = ""
    html = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f"<style>{css}</style>\n"
        "</head>\n<body>\n"
        '<div class="ctrl-bar">\n'
        + (f'  {panels_html}\n  {sep}\n' if panels_html else "")
        + f'  <label>Freq&nbsp;min:<input type="range" id="freq_lo"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_min:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()" onchange="update()">'
        f'<input class="freq-txt" id="freq_lo_txt" type="text" value="{freq_min:.3f}"'
        f' onchange="freqTxtChange(\'lo\')"'
        f' onkeydown="freqKeyDown(event,\'lo\')">&nbsp;MHz</label>\n'
        f'  <label>Freq&nbsp;max:<input type="range" id="freq_hi"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_max:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()" onchange="update()">'
        f'<input class="freq-txt" id="freq_hi_txt" type="text" value="{freq_max:.3f}"'
        f' onchange="freqTxtChange(\'hi\')"'
        f' onkeydown="freqKeyDown(event,\'hi\')">&nbsp;MHz</label>\n'
        f'  <label><input type="checkbox" id="log_x_chk"'
        + (" checked" if log_x else "")
        + ' onchange="toggleLogX()"> Log&nbsp;X</label>\n'
        + band_section_html
        + f'  {sep}\n'
        + '  <button class="reset-btn" onclick="resetFilters()">Reset</button>\n'
        + '  <span id="n_groups"></span>\n'
        + "</div>\n"
        + temp_stat_bar_html
        + '<div class="flt-bar" onclick="event.stopPropagation()">\n'
        + '  <b>Data&nbsp;filter:</b>\n'
        + '  <label><input type="radio" name="sum_flt" value="all" checked'
        + ' onchange="toggleRangeInputs();update()"> All&nbsp;data</label>\n'
        + '  <label><input type="radio" name="sum_flt" value="passing"'
        + ' onchange="toggleRangeInputs();update()"> Passing&nbsp;only&nbsp;(TTL&nbsp;&#8804;&nbsp;Spec)</label>\n'
        + '  <label><input type="radio" name="sum_flt" value="range"'
        + ' onchange="toggleRangeInputs();update()"> Upper&nbsp;limit</label>\n'
        + '  <span id="sum_range_inputs" style="display:none;align-items:center;gap:4px">\n'
        + '    <input type="number" id="sum_yhi" placeholder="dBc limit" step="0.001" oninput="update()">\n'
        + '    <small style="color:#666">(hides conditions where max data exceeds limit)</small>\n'
        + '  </span>\n'
        + '  <span class="sep"></span>\n'
        + '  <label title="Override TLL for Passing only filter and draw TLL line">'
        + 'TLL&nbsp;override:<input type="number" id="sum_tll_hi" step="0.001" placeholder="auto"'
        + ' style="width:74px" oninput="update()"></label>\n'
        + '  <span class="sep"></span>\n'
        + '  <label title="Show non-selected conditions as dim gray bands">'
        + '<input type="checkbox" id="sum_show_excl_chk" onchange="update()">'
        + '&nbsp;Show&nbsp;excluded</label>\n'
        + '  <label title="Apply global exclusion filter from boxplot (excludes whole DUTs)">'
        + '<input type="checkbox" id="sum_gf_chk" checked onchange="update()">'
        + '&nbsp;Apply&nbsp;GF</label>\n'
        + '  <span id="sum_gf_badge" style="display:none;font-size:11px;background:#fff0e8;'
        + 'border:1px solid #e0905a;border-radius:3px;padding:1px 7px;color:#c04000;'
        + 'margin-left:4px"></span>\n'
        + f'  {_csv_btn("saveCSV")}\n'
        + '</div>\n'
        + '<p style="font-size:12px;color:#666;margin:0 8px 4px">'
        + 'Shaded band&nbsp;=&nbsp;Min–Max &nbsp;|&nbsp; Solid&nbsp;=&nbsp;Mean'
        + ' &nbsp;|&nbsp; Dashed&nbsp;=&nbsp;TTL estimate'
        + ' &nbsp;|&nbsp; Red dashed&nbsp;=&nbsp;Spec limit</p>\n'
        + '<div id="plot"></div>\n'
        + '<div style="margin:4px 8px 2px;display:flex;gap:8px;align-items:center">\n'
        + '  <b style="font-size:13px">Results Table</b>\n'
        + '  <button id="sum_refresh_table_btn" class="reset-btn"'
        + ' onclick="buildTable();this.textContent=\'Refresh table\'">Refresh&nbsp;table</button>\n'
        + '  <button class="csv-btn" onclick="exportTableCSV()">Export&nbsp;CSV</button>\n'
        + '  <small style="color:#888;font-size:11px">Active conditions within current freq range</small>\n'
        + '</div>\n'
        + '<div id="sum_table_wrap" style="overflow-x:auto;margin:0 8px 12px"></div>\n'
        + f"<script>{_get_plotlyjs()}</script>\n"
        + "<script>\n"
        + constants + "\n"
        + _SUMPLOT_JS
        + "</script>\n</body>\n</html>"
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


def summary_plot(csv_path: Path, cfg: dict, output_html: Path) -> None:
    """
    Interactive summary statistics plot from a PADB Type=90 SummaryPlot CSV.

    Checkbox panels filter by each condition dimension (HarmonicNumber, AlcState, Mode, etc.)
    parsed from the Group column.  Freq sliders (with text entry) trim the X axis.
    Log X auto-detected when range spans >= 2 decades.
    """
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]

    x_col      = "X value"
    mean_col   = "mean (Sum.)"
    min_col    = "Min Data"
    max_col    = "Max Data"
    uttl_col   = "Upper TTL (est)"
    lttl_col   = "Lower TTL (est)"
    # Fallback TTL columns when PADB didn't compute Upper/Lower TTL (est)
    uttl_fallback = "Inner Proportion (upper)"
    lttl_fallback = "Inner Proportion (lower)"
    hi_spec_col = "Upper Limit"
    lo_spec_col = "Lower Limit"

    required = {x_col, mean_col, min_col, max_col, "Group"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"summary_plot: missing columns {missing}")

    for c in [x_col, mean_col, min_col, max_col, uttl_col, lttl_col, hi_spec_col, lo_spec_col]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    title   = cfg.get("title", output_html.stem)
    y_label = cfg.get("y_label", "Level (dBc)")
    y_lim   = cfg.get("y_lim")

    # Parse group key-value pairs (exclude serial-like keys/values)
    _serial_re  = re.compile(r"^[A-Z]{2,3}\d{5,}$")
    _serial_kws = ("serial", "unit id", "dut id", "s/n")
    unique_groups = df["Group"].dropna().unique()
    group_kv: dict[str, dict] = {g: _parse_group_kv(str(g)) for g in unique_groups}
    all_keys: set[str] = {k for kv in group_kv.values() for k in kv}

    cond_keys: list[str] = []
    for key in sorted(all_keys):
        if any(kw in key.lower() for kw in _serial_kws):
            continue
        vals = {kv.get(key, "") for kv in group_kv.values() if key in kv}
        if vals and sum(_serial_re.match(v) is not None for v in vals) / len(vals) > 0.5:
            continue
        if len(vals) >= 1:
            cond_keys.append(key)

    # Collect distinct values per condition key
    dim_vals: dict[str, set] = {}
    for kv in group_kv.values():
        for k, v in kv.items():
            if k in cond_keys:
                dim_vals.setdefault(k, set()).add(v)

    def _sort_num(vals):
        try:
            return sorted(vals, key=float)
        except (ValueError, TypeError):
            return sorted(vals)

    cond_dims = []
    for key in cond_keys:
        vals = _sort_num(dim_vals.get(key, []))
        if len(vals) > 1:
            col_id = re.sub(r"\W+", "_", key)
            cond_dims.append({"col": key, "col_id": col_id, "label": key, "vals": vals})

    # Build per-group JSON records
    hi_spec = float("nan")
    lo_spec = float("nan")
    records: list[dict] = []

    for glabel in sorted(str(g) for g in df["Group"].dropna().unique()):
        sub = df[df["Group"] == glabel].dropna(subset=[x_col]).sort_values(x_col)
        kv  = group_kv.get(glabel, {})

        def _to_list(col: str, _sub=sub) -> list:
            if col not in _sub.columns:
                return [None] * len(_sub)
            return [None if pd.isna(v) else round(float(v), 6) for v in _sub[col]]

        spec_hi_list = _to_list(hi_spec_col)
        spec_lo_list = _to_list(lo_spec_col)
        g_hi = next((v for v in spec_hi_list if v is not None), None)
        g_lo = next((v for v in spec_lo_list if v is not None), None)

        uttl_data = _to_list(uttl_col)
        lttl_data = _to_list(lttl_col)
        uttl_from_fallback = all(v is None for v in uttl_data)
        lttl_from_fallback = all(v is None for v in lttl_data)
        if uttl_from_fallback:
            uttl_data = _to_list(uttl_fallback)
        if lttl_from_fallback:
            lttl_data = _to_list(lttl_fallback)

        records.append({
            "condition":         glabel,
            "cond_keys":         {k: kv.get(k, "") for k in cond_keys},
            "freqs":             [round(float(v), 6) for v in sub[x_col]],
            "mean":              _to_list(mean_col),
            "min_data":          _to_list(min_col),
            "max_data":          _to_list(max_col),
            "uttl":              uttl_data,
            "lttl":              lttl_data,
            "uttl_is_estimate":  uttl_from_fallback,
            "spec_hi":           g_hi,
            "spec_lo":           g_lo,
            "spec_hi_list":      spec_hi_list,
            "spec_lo_list":      spec_lo_list,
        })

        if np.isnan(hi_spec) and g_hi is not None:
            hi_spec = g_hi
        if np.isnan(lo_spec) and g_lo is not None:
            lo_spec = g_lo

    all_freqs_s = df[x_col].dropna()
    freq_min  = float(all_freqs_s.min()) if len(all_freqs_s) else 0.0
    freq_max  = float(all_freqs_s.max()) if len(all_freqs_s) else 1.0
    freq_vals = sorted(float(f) for f in all_freqs_s.unique())

    _build_summary_html(
        records, cond_dims, cfg, output_html,
        hi_spec=hi_spec, lo_spec=lo_spec,
        freq_min=freq_min, freq_max=freq_max, freq_vals=freq_vals,
    )
