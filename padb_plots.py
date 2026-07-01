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

/* ---------- log X toggle ---------- */
function isLogX(){ return document.getElementById('log_x_chk').checked; }
function toggleLogX(){
  var log=isLogX();
  var lo=parseFloat(document.getElementById('freq_lo').value);
  var hi=parseFloat(document.getElementById('freq_hi').value);
  var range=log?[Math.log10(Math.max(lo,1e-9)),Math.log10(Math.max(hi,1e-9))]:[lo,hi];
  Plotly.relayout('plot',{'xaxis.type':log?'log':'linear','xaxis.range':range});
}

/* ---------- frequency sliders ---------- */
function syncFreq(){
  var lo=document.getElementById('freq_lo');
  var hi=document.getElementById('freq_hi');
  var loV=parseFloat(lo.value),hiV=parseFloat(hi.value);
  if(loV>hiV){lo.value=hiV;loV=hiV;}
  document.getElementById('freq_lo_val').textContent=loV.toFixed(3);
  document.getElementById('freq_hi_val').textContent=parseFloat(hi.value).toFixed(3);
  var log=isLogX();
  var range=log?[Math.log10(Math.max(loV,1e-9)),Math.log10(Math.max(hiV,1e-9))]:[loV,hiV];
  Plotly.relayout('plot',{'xaxis.range':range});
}

/* ---------- save filtered CSV ---------- */
function saveCSV(){
  var filtered=applyFilters(DATA);
  if(!filtered.length){alert('No data matches current filters.');return;}
  var colMap={'Frequency_MHz':'Frequency_MHz','Value':Y_LABEL.replace(/[,"\n]/g,'')};
  GROUP_COLS.forEach(function(p){colMap[p[0]]=p[1].replace(/[,"\n]/g,'');});
  var cols=Object.keys(filtered[0]).filter(function(c){return colMap[c];});
  var rows=[cols.map(function(c){return colMap[c];}).join(',')];
  filtered.forEach(function(r){
    rows.push(cols.map(function(c){
      var v=r[c];
      if(v===null||v===undefined) return '';
      var s=String(v);
      return (s.indexOf(',')>=0||s.indexOf('"')>=0)?'"'+s.replace(/"/g,'""')+'"':s;
    }).join(','));
  });
  var blob=new Blob([rows.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  a.href=url;a.download=(TITLE+'_filtered').replace(/[^a-zA-Z0-9_\-]/g,'_')+'.csv';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/* ---------- filter & render ---------- */
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
  return entries.map(function(entry){
    var key=entry[0],rows=entry[1];
    var sorted=rows.slice().sort(function(a,b){return a.Frequency_MHz-b.Frequency_MHz;});
    return {
      type:'scattergl',
      x:sorted.map(function(r){return r.Frequency_MHz;}),
      y:sorted.map(function(r){return r.Value;}),
      mode:'lines+markers',marker:{size:3},name:key,
      hovertemplate:'<b>'+key+'</b><br>Freq: %{x:.3f} MHz<br>'+Y_LABEL+': %{y:.4f}<extra></extra>'
    };
  });
}

function buildLayout(){
  var shapes=[],annotations=[];
  if(LO_SPEC!==null){
    shapes.push({type:'line',xref:'paper',x0:0,x1:1,y0:LO_SPEC,y1:LO_SPEC,line:{color:'red',dash:'dash',width:1.5}});
    annotations.push({xref:'paper',yref:'y',x:0.01,y:LO_SPEC,text:'Lo='+LO_SPEC,showarrow:false,xanchor:'left',font:{color:'red',size:11}});
  }
  if(HI_SPEC!==null){
    shapes.push({type:'line',xref:'paper',x0:0,x1:1,y0:HI_SPEC,y1:HI_SPEC,line:{color:'red',dash:'dash',width:1.5}});
    annotations.push({xref:'paper',yref:'y',x:0.99,y:HI_SPEC,text:'Hi='+HI_SPEC,showarrow:false,xanchor:'right',font:{color:'red',size:11}});
  }
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
  GROUP_COLS.forEach(function(pair){
    var col=pair[0];
    document.querySelectorAll('.fchk[data-col="'+col+'"]').forEach(function(c){c.checked=true;});
    var allChk=document.getElementById('all_'+col);
    if(allChk){allChk.checked=true;allChk.indeterminate=false;}
    document.getElementById('badge_'+col).classList.remove('active');
  });
  document.getElementById('freq_lo').value=FREQ_MIN;
  document.getElementById('freq_hi').value=FREQ_MAX;
  document.getElementById('freq_lo_val').textContent=parseFloat(FREQ_MIN).toFixed(3);
  document.getElementById('freq_hi_val').textContent=parseFloat(FREQ_MAX).toFixed(3);
  update();
}

function update(){
  var filtered=applyFilters(DATA);
  document.getElementById('n_points').textContent=filtered.length.toLocaleString()+' pts';
  Plotly.react('plot',buildTraces(filtered),buildLayout());
}

Plotly.newPlot('plot',buildTraces(DATA),buildLayout());
document.getElementById('n_points').textContent=DATA.length.toLocaleString()+' pts';
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
        for k, _ in re.findall(r'(\w+):\s*(\S+)', str(g)):
            all_keys.add(k)
    df = df.copy()
    for key in sorted(all_keys):
        df[f"_grp_{key}"] = df["Group"].str.extract(rf'{re.escape(key)}:\s*(\S+)')
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

    # Columns to embed as JSON
    json_cols = ["Frequency_MHz", "Value"] + [c for c, _ in group_cols]
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

    # Checkbox panels — one per filter dimension
    panels: list[str] = []
    for col, label in group_cols:
        vals = sorted(str(v) for v in df[col].dropna().replace("", pd.NA).dropna().unique())
        if vals:
            panels.append(_checkbox_panel(col, label, vals))
    panels_html = "\n  ".join(panels)

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
        f"var GROUP_COLS={json.dumps([[c, l] for c, l in group_cols])};",
        f"var FREQ_MIN={freq_min!r};",
        f"var FREQ_MAX={freq_max!r};",
    ])

    css = (
        "body{font-family:Arial,sans-serif;margin:0;padding:8px;}"
        ".ctrl-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
        "padding:8px 14px;background:#f0f2f5;border-radius:6px;margin-bottom:8px;font-size:13px;}"
        ".ctrl-bar label{white-space:nowrap;}"
        ".ctrl-bar select{font-size:13px;padding:2px 4px;border:1px solid #bbb;border-radius:3px;}"
        ".ctrl-bar input[type=range]{vertical-align:middle;width:100px;}"
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
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<span id="freq_lo_val">{freq_min:.3f}</span>&nbsp;MHz</label>\n'
        f'  <label>Freq&nbsp;max:<input type="range" id="freq_hi"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_max:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<span id="freq_hi_val">{freq_max:.3f}</span>&nbsp;MHz</label>\n'
        f'  <label><input type="checkbox" id="log_x_chk"'
        + (' checked' if log_x else '')
        + ' onchange="toggleLogX()"> Log&nbsp;X</label>\n'
        '  <div class="sep"></div>\n'
        '  <button class="reset-btn" onclick="resetFilters()">Reset</button>\n'
        '  <button class="reset-btn" style="background:#e8f4ff;border-color:#0066cc;color:#0066cc"'
        ' onclick="saveCSV()">&#8595;&nbsp;CSV</button>\n'
        '  <span id="n_points"></span>\n'
        "</div>\n"
        '<div id="plot"></div>\n'
        f"<script>{_get_plotlyjs()}</script>\n"
        "<script>\n"
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
        hovertemplate="Freq: %{x:.1f} MHz<br>Median: %{y:.4f}<extra></extra>",
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
    Distribution analysis: histogram + KDE + best-fit parametric PDF + spec lines.
    Uses all Value data (across all frequencies and serials).
    """
    df = _load_scatter_csv(csv_path)
    lo_spec, hi_spec = _get_spec(df, cfg)
    y_label = cfg.get("y_label", df["_val_col_name"].iloc[0] if len(df) else "Value")
    data = pst._clean(df["Value"].values)

    if len(data) < 5:
        go.Figure().write_html(str(output_html), include_plotlyjs=_PLOTLY_JS)
        return

    x_kde, y_kde = pst.kde(data)
    _, pdf_vals, fit_info = pst.best_fit_pdf(data, x_kde)

    fig = go.Figure()

    fig.add_trace(go.Histogram(
        x=data, nbinsx=40, histnorm="probability density",
        marker_color="steelblue", opacity=0.5, name="Data",
    ))
    fig.add_trace(go.Scatter(
        x=x_kde, y=y_kde, mode="lines",
        line=dict(color="navy", width=2), name="KDE",
    ))
    if fit_info:
        fig.add_trace(go.Scatter(
            x=x_kde, y=pdf_vals, mode="lines",
            line=dict(color="darkorange", width=2, dash="dash"),
            name=f"Best fit: {fit_info['name']} (AIC={fit_info['aic']:.1f})",
        ))

    if not np.isnan(lo_spec):
        fig.add_vline(x=lo_spec, line=dict(color="red", dash="dash"),
                      annotation_text=f"Lo={lo_spec:g}")
    if not np.isnan(hi_spec):
        fig.add_vline(x=hi_spec, line=dict(color="red", dash="dash"),
                      annotation_text=f"Hi={hi_spec:g}")

    fig.update_xaxes(title_text=y_label)
    fig.update_yaxes(title_text="Probability Density")
    n = len(data)
    fig.add_annotation(
        xref="paper", yref="paper", x=0.98, y=0.98, xanchor="right", yanchor="top",
        text=f"n={n} | mean={np.mean(data):.4f} | std={np.std(data,ddof=1):.4f}",
        showarrow=False, font=dict(size=11), bgcolor="white", bordercolor="#ccc",
    )
    _write(fig, output_html, cfg)


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
                "Freq: %{x:.1f} MHz<br>"
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

    P_vals = [0.80, 0.85, 0.90, 0.95, 0.99, 0.999]
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
    out["Frequency_MHz"] = df_raw[freq_col].map(_sfloat) if freq_col else np.nan
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
            # look for patterns like "20.0 Deg C" or "Room" inside the string
            m = re.search(r'([\d.]+)\s*[Dd]eg\s*[Cc]', s)
            if m:
                return _parse_temp_tag(m.group(0))
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
    Parse Group strings with multi-word keys separated by 2+ spaces.
    E.g. "AlcState: TRUE  OA State: 0  Mode: 0  Serial Number: US65080401"
    → {"AlcState": "TRUE", "OA State": "0", "Mode": "0", "Serial Number": "US65080401"}
    Falls back to single-word key regex for strings without double-space separators.
    """
    s = str(group_str).strip()
    # Try double-space splitting first (preserves multi-word keys)
    parts = re.split(r'  +', s)
    result: dict = {}
    for part in parts:
        m = re.match(r'(.+?):\s*(\S+)\s*$', part.strip())
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

    # Parse all unique Group strings → kv dicts
    unique_groups = df["Group"].dropna().unique()
    group_kv: dict[str, dict] = {g: _parse_group_kv(g) for g in unique_groups}

    # Collect all keys across all groups
    all_keys: set[str] = set()
    for kv in group_kv.values():
        all_keys.update(kv.keys())

    # Classify keys: serial vs condition vs constant
    serial_keys: set[str] = set()
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
        # Include as condition only if it varies (cardinality 2-20)
        if 1 < len(vals) <= 20:
            cond_keys.append(key)

    # Build serial ID and condition label per row
    def _serial_id(group_str: str) -> str:
        kv = group_kv.get(group_str, {})
        for k in serial_keys:
            if k in kv:
                return kv[k]
        return group_str  # entire group as fallback

    def _cond_label(group_str: str) -> str:
        kv = group_kv.get(group_str, {})
        parts = [f"{k}: {kv[k]}" for k in cond_keys if k in kv]
        return "  ".join(parts) if parts else "All"

    df = df.copy()
    if "Group" in df.columns and not df["Group"].isna().all():
        df["_serial_id"] = df["Group"].map(_serial_id).fillna("unknown")
        df["_cond"]      = df["Group"].map(_cond_label).fillna("All")
    else:
        df["_serial_id"] = "unknown"
        df["_cond"]      = "All"

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
        room_dut = (room_df.groupby(["_serial_id", "Frequency_MHz"])["Value"]
                    .mean().reset_index())
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
            dut_vals = _fdf["Value"].values
            dut_sers = _fdf["_serial_id"].values if "_serial_id" in _fdf.columns else ["unknown"] * len(_fdf)
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
                [{"s": str(ser), "v": round(float(v), 6)}
                 for v, ser in zip(dut_vals, dut_sers) if v < lo_w or v > hi_w],
                key=lambda x: x["v"])
            outliers = [d["v"] for d in outlier_det]
            dut_detail = [{"s": str(ser), "v": round(float(v), 6)}
                          for ser, v in zip(dut_sers, dut_vals)]
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
    spec_hi_override: numOrNull('stat_spec_hi')
  };
}

/* ---- active conditions (filtered by checkboxes) ---- */
function getActiveConditions(){
  if(!COND_DIMS||!COND_DIMS.length) return STAT_DATA;
  return STAT_DATA.filter(function(cd){
    return COND_DIMS.every(function(dim){
      var allowed=getSelected('cond_'+dim.col_id);
      var safe=dim.col.replace(/[-\/\\^$*+?.()|[\]{}]/g,'\\$&');
      var m=cd.condition.match(new RegExp(safe+':?\\s*(\\S+)'));
      return m&&allowed.indexOf(m[1])>=0;
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
  var tll_up=(spec_up!=null)?spec_up-g_up:null;
  var tll_lo=(spec_lo_mag!=null)?-spec_lo_mag+g_lo:null;
  return {n_use:n_use,k:k,ti_up:ti_up,ti_lo:ti_lo,np_active:useNp,
          tll_up:tll_up,tll_lo:tll_lo,
          denv_up:dev_up,denv_lo:dev_lo,
          spec_lo:-spec_lo_mag,spec_up:spec_up,
          pass_up:tll_up===null||ti_up<=tll_up,
          pass_lo:tll_lo===null||ti_lo>=tll_lo};
}

/* ---- CSV export ---- */
function saveCSV(){
  var conds=getActiveConditions();
  var fLo=parseFloat(document.getElementById('freq_lo').value);
  var fHi=parseFloat(document.getElementById('freq_hi').value);
  conds=conds.map(function(cd){
    return Object.assign({},cd,{
      freq_stats:(cd.freq_stats||[]).filter(function(fs){return fs.freq>=fLo&&fs.freq<=fHi;})
    });
  });
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
  var blob=new Blob([rows.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  a.href=url;a.download=(TITLE+'_stat').replace(/[^a-zA-Z0-9_\-]/g,'_')+'.csv';
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
    // Always push 4 traces per condition to keep index stable for Plotly.react
    if(!sorted.length){
      for(var i=0;i<4;i++)
        traces.push({type:'scatter',x:[],y:[],mode:'lines',showlegend:false,hoverinfo:'skip'});
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
        'Freq: '+fs.freq.toFixed(3)+' MHz<br>'+
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

    // Trace 1: TI upper bound — use 'scatter' (not scattergl) so fill works
    traces.push({
      type:'scatter',x:freqs,y:ti_ups,mode:'lines',
      line:{color:color,dash:'dash',width:1},
      name:cd.condition+' TI↑',legendgroup:cd.condition,showlegend:false,
      hoverinfo:'skip'
    });
    // Trace 2: TI lower bound + fill to trace above
    traces.push({
      type:'scatter',x:freqs,y:ti_los,mode:'lines',
      fill:'tonexty',fillcolor:fillColor,
      line:{color:color,dash:'dash',width:1},
      name:cd.condition+' band',legendgroup:cd.condition,showlegend:false,
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
  });

  // Spec + TLL reference lines
  if(!all_freqs.length) return traces;
  var fMin=Math.min.apply(null,all_freqs),fMax=Math.max.apply(null,all_freqs);
  function hline(y,name,color,dash){
    return {type:'scatter',x:[fMin,fMax],y:[y,y],mode:'lines',
            line:{color:color,dash:dash,width:1.5},name:name,
            hovertemplate:name+': '+y.toFixed(4)+'<extra></extra>'};
  }
  var specLoRaw=(LO_SPEC!==null)?LO_SPEC:params.spec_lo_override;
  var specHi=(HI_SPEC!==null)?HI_SPEC:params.spec_hi_override;
  var specLo=specLoRaw!==null?-Math.abs(specLoRaw):null;
  if(specLo!==null) traces.push(hline(specLo,'Spec Lo','red','dash'));
  if(specHi!==null) traces.push(hline(specHi,'Spec Hi','red','dash'));

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
function recomputeFreqStat(fs,selSers){
  var dv=(fs.dut_vals||[]).filter(function(d){return selSers.indexOf(d.s)>=0;});
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
function buildLayout(){
  var flt=getDataFilter();
  var yRange=Y_LIM;
  if(flt.mode==='range'){
    var ylo=isFinite(flt.ylo)?flt.ylo:(Y_LIM?Y_LIM[0]:null);
    var yhi=isFinite(flt.yhi)?flt.yhi:(Y_LIM?Y_LIM[1]:null);
    if(ylo!==null&&yhi!==null) yRange=[ylo,yhi];
  }
  return {
    title:{text:TITLE,x:0.5,font:{size:15}},
    template:'plotly_white',
    xaxis:{title:'Frequency (MHz)',type:isLogX()?'log':'linear'},
    yaxis:{title:Y_LABEL,range:yRange},
    height:450,
    legend:{bgcolor:'rgba(255,255,255,0.85)',bordercolor:'#ccc',borderwidth:1},
    margin:{l:60,r:30,t:55,b:60}
  };
}

/* ---- TLL display ---- */
function updateTLLDisplay(conds,params){
  var el=document.getElementById('tll_display');if(!el)return;
  var ups=[],los=[];
  conds.forEach(function(cd){
    (cd.freq_stats||[]).forEach(function(fs){
      var r=computeFreqResult(fs,params);
      if(r.tll_up!==null) ups.push(r.tll_up);
      if(r.tll_lo!==null) los.push(r.tll_lo);
    });
  });
  function rng(arr){
    if(!arr.length) return '—';
    var mn=Math.min.apply(null,arr),mx=Math.max.apply(null,arr);
    return Math.abs(mx-mn)<0.001?mn.toFixed(4):mn.toFixed(4)+' to '+mx.toFixed(4);
  }
  el.textContent='TLL↑: '+rng(ups)+'  |  TLL↓: '+rng(los);
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
      var pass=r.pass_up&&r.pass_lo;
      if(!pass) nFail++;
      var bg=pass?'':'background:#fff0f0';
      var tiStr=(r.np_active?'[NP] ':'')+
               '['+r.ti_lo.toFixed(4)+', '+r.ti_up.toFixed(4)+']';
      var tllStr=(r.tll_lo!==null&&r.tll_up!==null)?
        '['+r.tll_lo.toFixed(4)+', '+r.tll_up.toFixed(4)+']':'—';
      var outCells='';
      if(fs.outliers&&fs.outliers.length){
        outCells='<td class="out"><b>'+fs.outliers.length+'</b>: '+
          fs.outliers.map(function(v){return v.toFixed(4);}).join(', ')+'</td>';
      } else {
        outCells='<td style="color:#aaa">—</td>';
      }
      rows.push('<tr style="'+bg+'">'+
        '<td>'+cd.condition+'</td>'+
        '<td>'+fs.freq.toFixed(2)+'</td>'+
        '<td>'+r.n_use+'</td>'+
        '<td>'+fs.mean.toFixed(4)+'</td>'+
        '<td>'+fs.s.toFixed(4)+'</td>'+
        '<td>'+tiStr+'</td>'+
        '<td>'+tllStr+'</td>'+
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
    '<th>Pass</th><th>Method</th><th>Normality</th><th>Outliers</th></tr></thead><tbody>';
  el.innerHTML=banner+hdr+rows.join('')+'</tbody></table>';
}

/* ---- data filter ---- */
function getDataFilter(){
  var mode='all';
  document.querySelectorAll('input[name="data_flt"]').forEach(function(r){if(r.checked)mode=r.value;});
  var ylo=parseFloat(document.getElementById('flt_ylo').value);
  var yhi=parseFloat(document.getElementById('flt_yhi').value);
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
      if(flt.mode==='range') return r.ti_up>=flt.ylo&&r.ti_lo<=flt.yhi;
      return true;
    });
    return Object.assign({},cd,{freq_stats:fs2});
  });
}

/* ---- freq slider ---- */
function syncFreq(){
  var lo=parseFloat(document.getElementById('freq_lo').value);
  var hi=parseFloat(document.getElementById('freq_hi').value);
  document.getElementById('freq_lo_val').textContent=lo.toFixed(2);
  document.getElementById('freq_hi_val').textContent=hi.toFixed(2);
  update();
}

/* ---- main update ---- */
function update(){
  var conds=getActiveConditions();
  var fLo=parseFloat(document.getElementById('freq_lo').value);
  var fHi=parseFloat(document.getElementById('freq_hi').value);
  conds=conds.map(function(cd){
    return Object.assign({},cd,{
      freq_stats:(cd.freq_stats||[]).filter(function(fs){return fs.freq>=fLo&&fs.freq<=fHi;})
    });
  });
  var params=getParams();
  var selSers=getSelectedSerials();var allSers=getAllSerials();
  if(allSers.length>1&&selSers.length<allSers.length){
    conds=conds.map(function(cd){
      var nfs=[];
      (cd.freq_stats||[]).forEach(function(fs){var r=recomputeFreqStat(fs,selSers);if(r) nfs.push(r);});
      return Object.assign({},cd,{freq_stats:nfs});
    });
  }
  var flt=getDataFilter();
  conds=applyDataFilter(conds,params,flt);
  Plotly.react('plot',buildTraces(conds,params),buildLayout());
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
}

Plotly.newPlot('plot',buildTraces(STAT_DATA,getParams()),buildLayout(),{responsive:true});
updateTLLDisplay(STAT_DATA,getParams());
updateStatPanel(STAT_DATA,getParams());
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

    lo_spec, hi_spec = _get_spec(df, cfg)

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
    spec_lo_val = "" if np.isnan(lo_spec) else str(float(lo_spec))
    spec_hi_val = "" if np.isnan(hi_spec) else str(float(hi_spec))

    lo_js = "null" if np.isnan(lo_spec) else repr(float(lo_spec))
    hi_js = "null" if np.isnan(hi_spec) else repr(float(hi_spec))

    # Build COND_DIMS from condition strings in stat_data
    # Each condition is e.g. "OA State: 0  Mode: 1"; parse key→set of values
    dim_vals: dict[str, set] = {}
    for cd in stat_data:
        for part in re.split(r'  +', cd["condition"]):
            m = re.match(r'(.+?):\s*(\S+)', part.strip())
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
    )

    sep = '<div class="sep"></div>'

    freq_lo_html = (
        f'<label>Freq&nbsp;min:<input type="range" id="freq_lo"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_min:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<span id="freq_lo_val">{freq_min:.2f}</span>&nbsp;MHz</label>'
    )
    freq_hi_html = (
        f'<label>Freq&nbsp;max:<input type="range" id="freq_hi"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_max:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<span id="freq_hi_val">{freq_max:.2f}</span>&nbsp;MHz</label>'
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

    ctrl_bar = (
        '<div class="ctrl-bar">\n'
        + (f'  {panels_html}\n  {sep}\n' if panels_html else '')
        + (f'  {serial_panel_html}\n  {sep}\n' if serial_panel_html else '')
        + f'  {freq_lo_html}\n'
        + f'  {freq_hi_html}\n'
        + f'  {log_x_html}\n'
        + '</div>\n'
    )

    stat_bar = (
        '<div class="stat-bar" onclick="event.stopPropagation()">\n'
        f'  <b style="margin-right:4px">P:</b>'
        f'<input type="range" id="stat_P" min="0.80" max="0.999" value="{default_P:.3f}" step="0.005"'
        f' oninput="document.getElementById(\'lbl_P\').textContent=parseFloat(this.value).toFixed(3);update()">'
        f'<span id="lbl_P">{default_P:.3f}</span>\n'
        f'  <b style="margin-right:4px">C:</b>'
        f'<input type="range" id="stat_C" min="0.75" max="0.95" value="{default_C:.2f}" step="0.01"'
        f' oninput="document.getElementById(\'lbl_C\').textContent=parseFloat(this.value).toFixed(2);update()">'
        f'<span id="lbl_C">{default_C:.2f}</span>\n'
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
        ' onchange="toggleRangeInputs();update()"> Y&nbsp;range</label>\n'
        '  <span id="flt_range_inputs" style="display:none;align-items:center;gap:4px">\n'
        f'    <input type="number" id="flt_ylo" placeholder="Y min" step="0.001" value="{y_lim_lo}"'
        ' oninput="update()">\n'
        '    &ndash;\n'
        f'    <input type="number" id="flt_yhi" placeholder="Y max" step="0.001" value="{y_lim_hi}"'
        ' oninput="update()">\n'
        '    <small style="color:#666">(hides freqs where TI band is entirely outside range)</small>\n'
        '  </span>\n'
        '  <span class="sep"></span>\n'
        '  <label title="Use non-parametric (distribution-free) order-statistic TI'
        ' for Non-normal and Marginal frequencies">'
        '<input type="checkbox" id="np_ti_chk" onchange="update()">'
        '&nbsp;Non-parametric&nbsp;TI</label>\n'
        '  <button class="csv-btn" onclick="saveCSV()">&#8595;&nbsp;CSV</button>\n'
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
      var chks=Array.from(document.querySelectorAll('.'+dim.col_id+':checked')).map(function(c){return c.value;});
      if(!chks.length) return true;
      var safe=dim.col.replace(/[-\/\\^$*+?.()|[\]{}]/g,'\\$&');
      var m=cd.condition.match(new RegExp(safe+':?\\s*(\\S+)'));
      return !m||chks.indexOf(m[1])>=0;
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
  var yLo=parseFloat(document.getElementById('env_y_lo').value);
  var yHi=parseFloat(document.getElementById('env_y_hi').value);
  return {mode:mode,yLo:isNaN(yLo)?-Infinity:yLo,yHi:isNaN(yHi)?Infinity:yHi};
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
      var ok=(cd.spec_hi===null||cd.ttu[j]===null||cd.ttu[j]<=cd.spec_hi)&&
             (cd.spec_lo===null||cd.ttl[j]===null||cd.ttl[j]>=cd.spec_lo);
      if(!ok) return;
    } else if(flt.mode==='yrange'){
      if(cd.ude[j]>flt.yHi||-cd.lde[j]<flt.yLo) return;
    }
    idxs.push(j);
  });
  return idxs;
}
function hexAlpha(hex,a){
  var r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
  return 'rgba('+r+','+g+','+b+','+a+')';
}
function buildTraces(selConds){
  var traces=[];
  var fr=getFreqRange();
  var flt=getEnvDataFilter();
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
      var lines=[cd.condition,'Freq: '+f.toFixed(3)+' MHz'];
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
  var lv=document.getElementById('env_freq_lo_val');
  var hv=document.getElementById('env_freq_hi_val');
  if(lv) lv.textContent=lo.toFixed(2);
  if(hv) hv.textContent=hi.toFixed(2);
  update();
}
function saveCSV(){
  var selConds=getSelectedConds();
  var fr=getFreqRange();
  var hdrs=['Condition','Freq_MHz','UDE','LDE','Min_Env','Max_Env','Mean_Env','TTL_up','TTL_lo','Spec_lo','Spec_hi'];
  var rows=[hdrs.join(',')];
  function esc(v){var s=String(v==null?'':v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;}
  var flt=getEnvDataFilter();
  selConds.forEach(function(cd){
    getFilteredIdxs(cd,fr,flt).forEach(function(j){
      var f=cd.freqs[j];
      rows.push([esc(cd.condition),f,
        cd.ude[j],cd.lde[j],cd.min_env[j],cd.max_env[j],cd.mean_env[j],
        cd.ttu[j],cd.ttl[j],cd.spec_lo,cd.spec_hi
      ].map(esc).join(','));
    });
  });
  var blob=new Blob([rows.join('\n')],{type:'text/csv'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=ENV_TITLE.replace(/[^a-z0-9_\-]/gi,'_')+'.csv';
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
          +'<td>'+f.toFixed(3)+'</td>'
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
  Plotly.react('plot',buildTraces(getSelectedConds()),buildLayout());
  updateEnvStatsTable(getSelectedConds());
}
Plotly.newPlot('plot',buildTraces(getSelectedConds()),buildLayout(),{responsive:true,scrollZoom:true});
"""


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
        f'<span id="env_freq_lo_val">{freq_min:.2f}</span>&nbsp;MHz</label>'
    )
    freq_hi_html = (
        f'<label>Freq&nbsp;max:<input type="range" id="env_freq_hi"'
        f' min="{freq_min:.4f}" max="{freq_max:.4f}" value="{freq_max:.4f}"'
        f' step="{freq_step:.4f}" oninput="syncFreq()">'
        f'<span id="env_freq_hi_val">{freq_max:.2f}</span>&nbsp;MHz</label>'
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
        ' onchange="toggleEnvYRange()">&nbsp;Y&nbsp;range</label>'
        '<span id="env_y_range_inputs" style="display:none;gap:4px;align-items:center">'
        '&nbsp;<label>lo:<input type="number" id="env_y_lo" class="env-yin"'
        ' oninput="update()"></label>'
        '&nbsp;<label>hi:<input type="number" id="env_y_hi" class="env-yin"'
        ' oninput="update()"></label>'
        '</span>'
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
        + f'  <button class="csv-btn" onclick="saveCSV()">&#8595;&nbsp;CSV</button>\n'
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

    constants = "\n".join([
        f"var ENV_DATA={json.dumps(env_data)};",
        f"var ENV_TITLE={json.dumps(title)};",
        f"var ENV_Y_LABEL={json.dumps(y_label)};",
        f"var ENV_Y_LIM={json.dumps(y_lim)};",
        f"var ENV_FREQ_MIN={freq_min!r};",
        f"var ENV_FREQ_MAX={freq_max!r};",
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

    all_freqs = [f for cd in env_data for f in cd["freqs"] if f is not None]
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
        if 1 < len(vals) <= 20:
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
            if 1 < len(vals) <= 20:
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
        "        '<td>'+fs.freq.toFixed(2)+'</td>'+\n"
        "        '<td>'+fs.n+'</td>'+\n"
        "        '<td>'+fs.mean.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.s.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.q1.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.q2.toFixed(4)+'</td>'+\n"
        "        '<td>'+fs.q3.toFixed(4)+'</td>'+\n"
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
        "function saveBoxCSV(){\n"
        "  var hdrs=['Condition','Freq_MHz','n','Mean','Std','Q1','Median','Q3',\n"
        "            'Normality','W','p','DEnv_up','DEnv_lo','Spec_lo','Spec_hi','Outliers'];\n"
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
        "  var blob=new Blob([rows.join('\\r\\n')],{type:'text/csv;charset=utf-8;'});\n"
        "  var url=URL.createObjectURL(blob);\n"
        "  var a=document.createElement('a');\n"
        "  a.href=url;a.download=(BOX_TITLE+'_boxplot').replace(/[^a-zA-Z0-9_\\-]/g,'_')+'.csv';\n"
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
        + '<button class="toggle-btn" style="background:#e8f4ff;border-color:#0066cc;color:#0066cc"'
        ' onclick="saveBoxCSV()">&#8595;&nbsp;CSV</button>\n'
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
function toggleAll(col){
  var allChk=document.getElementById('all_'+col);
  document.querySelectorAll('.'+col).forEach(function(c){c.checked=allChk.checked;});
  updateBadge(col);update();
}
function chkChanged(col){
  var all=document.querySelectorAll('.'+col);
  var chk=Array.from(all).filter(function(c){return c.checked;});
  var allEl=document.getElementById('all_'+col);
  if(allEl){allEl.checked=chk.length===all.length;allEl.indeterminate=chk.length>0&&chk.length<all.length;}
  updateBadge(col);update();
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
  if(!COND_DIMS||!COND_DIMS.length) return allConds;
  return allConds.filter(function(cond){
    return COND_DIMS.every(function(dim){
      var allowed=getSelected('box_cond_'+dim.col_id);
      var safe=dim.col.replace(/[-\/\\^$*+?.()|[\]{}]/g,'\\$&');
      var m=cond.match(new RegExp(safe+':?\\s*(\\S+)'));
      return m&&allowed.indexOf(m[1])>=0;
    });
  });
}
function getSelectedTemps(){
  return Array.from(document.querySelectorAll('.box_env_chk:checked')).map(function(c){return c.value;});
}
function getYFilter(){
  var mode='all';
  document.querySelectorAll('input[name="box_flt"]').forEach(function(r){if(r.checked)mode=r.value;});
  var ylo=parseFloat(document.getElementById('box_flt_ylo').value);
  var yhi=parseFloat(document.getElementById('box_flt_yhi').value);
  return {mode:mode,ylo:isNaN(ylo)?-Infinity:ylo,yhi:isNaN(yhi)?Infinity:yhi};
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
function computeBoxStats(vals){
  if(!vals||!vals.length) return null;
  var n=vals.length;
  var sorted=vals.slice().sort(function(a,b){return a-b;});
  var q1=percentileSorted(sorted,25),q2=percentileSorted(sorted,50),q3=percentileSorted(sorted,75);
  var mean=0;for(var i=0;i<n;i++) mean+=sorted[i];mean/=n;
  var iqr=q3-q1,loF=q1-1.5*iqr,hiF=q3+1.5*iqr;
  return {n:n,mean:mean,q1:q1,q2:q2,q3:q3,
    lo_w:loF,hi_w:hiF,
    outliers:sorted.filter(function(v){return v<loF||v>hiF;})};
}
function buildBoxTraces(selConds,selTemps,yFlt,selBoxSers){
  var allSers=getAllBoxSerials();
  var serActive=selBoxSers&&allSers.length>1&&selBoxSers.length<allSers.length;
  var traces=[];
  var condIdxMap={};var ci=0;
  BOX_DATA.forEach(function(cd){if(condIdxMap[cd.condition]===undefined) condIdxMap[cd.condition]=ci++;});
  BOX_DATA.forEach(function(cd){
    if(selConds.indexOf(cd.condition)<0) return;
    if(selTemps.indexOf(cd.temp)<0) return;
    var fs;
    var passActive=yFlt&&yFlt.mode==='passing';
    var yActive=yFlt&&yFlt.mode==='range'&&(isFinite(yFlt.ylo)||isFinite(yFlt.yhi));
    if(serActive||yActive||passActive){
      var rlo=yActive&&isFinite(yFlt.ylo)?yFlt.ylo:-Infinity;
      var rhi=yActive&&isFinite(yFlt.yhi)?yFlt.yhi:Infinity;
      fs=[];
      cd.freq_stats.forEach(function(f){
        var detail=(f.vals_detail||f.vals.map(function(v){return {s:'unknown',v:v};}))
          .filter(function(d){
            return (!serActive||selBoxSers.indexOf(d.s)>=0)&&d.v>=rlo&&d.v<=rhi
              &&(!passActive||(LO_SPEC===null||d.v>=LO_SPEC)&&(HI_SPEC===null||d.v<=HI_SPEC));
          });
        if(!detail.length) return;
        var fv=detail.map(function(d){return d.v;});
        var s=computeBoxStats(fv);
        if(!s) return;
        var outDet=detail.filter(function(d){return d.v<s.lo_w||d.v>s.hi_w;});
        fs.push({freq:f.freq,freq_label:f.freq_label,
          n:s.n,mean:s.mean,q1:s.q1,q2:s.q2,q3:s.q3,lo_w:s.lo_w,hi_w:s.hi_w,
          outlier_detail:outDet,outliers:outDet.map(function(d){return d.v;}),vals_detail:detail});
      });
    } else {
      fs=cd.freq_stats.slice();
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
    if(oxArr.length){
      traces.push({type:'scatter',x:oxArr,y:oyArr,mode:'markers',text:oText,
        marker:{symbol:'circle-open',size:7,color:color,line:{width:2,color:color}},
        name:name+' outliers',showlegend:false,
        hovertemplate:'%{text}<extra></extra>'});
    }
  });
  if(LO_SPEC!==null) traces.push({type:'scatter',mode:'lines',
    x:BOX_FREQ_ORDER,y:BOX_FREQ_ORDER.map(function(){return LO_SPEC;}),
    line:{color:'red',dash:'dash',width:1.5},name:'Spec Lo',
    hovertemplate:'Spec Lo: '+LO_SPEC.toFixed(4)+'<extra></extra>'});
  if(HI_SPEC!==null) traces.push({type:'scatter',mode:'lines',
    x:BOX_FREQ_ORDER,y:BOX_FREQ_ORDER.map(function(){return HI_SPEC;}),
    line:{color:'red',dash:'dash',width:1.5},name:'Spec Hi',
    hovertemplate:'Spec Hi: '+HI_SPEC.toFixed(4)+'<extra></extra>'});
  return traces;
}
function buildLayout(){
  return {
    title:{text:BOX_TITLE,x:0.5,font:{size:15}},
    template:'plotly_white',
    xaxis:{title:'Frequency',categoryorder:'array',categoryarray:BOX_FREQ_ORDER,tickangle:-45},
    yaxis:{title:Y_LABEL,range:Y_LIM,autorange:Y_LIM?false:true},
    height:540,boxmode:'group',boxgap:0.2,boxgroupgap:0.15,
    legend:{bgcolor:'rgba(255,255,255,0.85)',bordercolor:'#ccc',borderwidth:1,font:{size:11}},
    margin:{l:60,r:30,t:60,b:90},
  };
}
function updateStatsTable(selConds,yFlt,selBoxSers){
  var el=document.getElementById('box_stat_panel');
  if(!el||el.style.display==='none') return;
  var showNp=isBoxNpTI();
  var allSers=getAllBoxSerials();
  var serActive=selBoxSers&&allSers.length>1&&selBoxSers.length<allSers.length;
  var passActive=yFlt&&yFlt.mode==='passing';
  var yFltActive=yFlt&&yFlt.mode==='range'&&(isFinite(yFlt.ylo)||isFinite(yFlt.yhi));
  var rows=[];
  if(serActive||yFltActive||passActive){
    var rlo=yFltActive&&isFinite(yFlt.ylo)?yFlt.ylo:-Infinity;
    var rhi=yFltActive&&isFinite(yFlt.yhi)?yFlt.yhi:Infinity;
    var fltLabel=(serActive&&yFltActive)?'Serial+Y-filtered':
                 serActive?'Serial-filtered':
                 passActive?'Passing only':
                 'Y-filtered ['+rlo.toFixed(3)+','+rhi.toFixed(3)+']';
    BOX_DATA.forEach(function(cd){
      if(selConds.indexOf(cd.condition)<0) return;
      (cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;}).forEach(function(f){
        var detail=(f.vals_detail||f.vals.map(function(v){return {s:'unknown',v:v};}))
          .filter(function(d){
            return (!serActive||selBoxSers.indexOf(d.s)>=0)&&d.v>=rlo&&d.v<=rhi
              &&(!passActive||(LO_SPEC===null||d.v>=LO_SPEC)&&(HI_SPEC===null||d.v<=HI_SPEC));
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
        rows.push('<tr><td>'+cd.condition+' / '+cd.temp+'</td><td>'+f.freq.toFixed(2)+'</td><td>'+s.n+'</td>'+
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
        rows.push('<tr><td>'+cd.condition+'</td><td>'+fs.freq.toFixed(2)+'</td><td>'+fs.n+'</td>'+
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
function saveBoxCSV(){
  var selConds=getSelectedConds();var selTemps=getSelectedTemps();
  var hdrs=['Condition','Temperature','Freq_MHz','Freq_Label','n_raw','Mean','Q1','Median','Q3','LowerFence','UpperFence','Outliers'];
  var rows=[hdrs.join(',')];
  function esc(v){var s=String(v==null?'':v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;}
  BOX_DATA.forEach(function(cd){
    if(selConds.indexOf(cd.condition)<0) return;
    if(selTemps.indexOf(cd.temp)<0) return;
    (cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;}).forEach(function(fs){
      rows.push([esc(cd.condition),esc(cd.temp),fs.freq.toFixed(4),esc(fs.freq_label),
        fs.n,fs.mean.toFixed(6),fs.q1.toFixed(6),fs.q2.toFixed(6),fs.q3.toFixed(6),
        fs.lo_w.toFixed(6),fs.hi_w.toFixed(6),
        esc((fs.outliers||[]).map(function(v){return v.toFixed(4);}).join('; '))
      ].join(','));
    });
  });
  var blob=new Blob([rows.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  a.href=url;a.download=(BOX_TITLE+'_boxplot_filtered').replace(/[^a-zA-Z0-9_\-]/g,'_')+'.csv';
  document.body.appendChild(a);a.click();document.body.removeChild(a);URL.revokeObjectURL(url);
}
function update(){
  var selConds=getSelectedConds();var selTemps=getSelectedTemps();var yFlt=getYFilter();
  var selBoxSers=getSelectedBoxSerials();
  Plotly.react('plot',buildBoxTraces(selConds,selTemps,yFlt,selBoxSers),buildLayout());
  updateStatsTable(selConds,yFlt,selBoxSers);
}
(function init(){
  var allConds=[];
  BOX_DATA.forEach(function(cd){if(allConds.indexOf(cd.condition)<0) allConds.push(cd.condition);});
  Plotly.newPlot('plot',buildBoxTraces(allConds,TEMPS_PRESENT,{mode:'all'},getAllBoxSerials()),buildLayout({mode:'all'}),{responsive:true,scrollZoom:true});
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
    results: list = []
    for cond, cdf in df.groupby("_cond", sort=True):
        for temp, tdf in cdf.groupby("Temperature"):
            freq_stats = []
            for freq in sorted_freqs:
                if has_ser:
                    _fdf = tdf.loc[tdf["Frequency_MHz"] == freq, ["Value", "_serial_id"]].dropna(subset=["Value"])
                    vals = _fdf["Value"].tolist()
                    vals_detail = [{"s": str(row["_serial_id"]), "v": round(float(row["Value"]), 6)}
                                   for _, row in _fdf.iterrows()]
                else:
                    vals = tdf.loc[tdf["Frequency_MHz"] == freq, "Value"].dropna().tolist()
                    vals_detail = [{"s": "unknown", "v": round(float(v), 6)} for v in vals]
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
        ".stbl{border-collapse:collapse;font-size:12px;width:100%;}"
        ".stbl th{background:#e8eaf6;padding:4px 8px;text-align:left;border:1px solid #ccc;"
        "white-space:nowrap;position:sticky;top:0;}"
        ".stbl td{padding:3px 8px;border:1px solid #ddd;white-space:nowrap;}"
        ".stbl tr:hover td{background:#f5f5ff;}"
        ".out{color:#c00;font-size:11px;}"
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

    sep_div = '<div class="sep"></div>'
    ctrl_parts = []
    if cond_dims:
        ctrl_parts.append(panels_html)
    if box_serial_panel_html:
        if ctrl_parts:
            ctrl_parts.append(sep_div)
        ctrl_parts.append(box_serial_panel_html)
    ctrl_bar = (f'<div class="ctrl-bar">\n  ' + '\n  '.join(ctrl_parts) + '\n</div>\n') if ctrl_parts else ""

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
        ' onchange="toggleRangeInputs();update()">&nbsp;Y&nbsp;range</label>\n'
        '  <span id="box_flt_range_inputs" style="display:none;align-items:center;gap:4px">\n'
        f'    <input type="number" id="box_flt_ylo" placeholder="Y min" step="0.001"'
        f' value="{y_lo_val}" oninput="update()">\n'
        '    &ndash;\n'
        f'    <input type="number" id="box_flt_yhi" placeholder="Y max" step="0.001"'
        f' value="{y_hi_val}" oninput="update()">\n'
        '    <small style="color:#666">(removes raw samples outside range before computing Q1/Q2/Q3/whiskers)</small>\n'
        '  </span>\n'
        '  <span class="sep"></span>\n'
        '  <label title="Show non-parametric (order-statistic) TI bounds in the Statistics Table'
        ' for Non-normal and Marginal frequencies">'
        '<input type="checkbox" id="box_np_ti_chk"'
        ' onchange="updateStatsTable(getSelectedConds(),getYFilter())">'
        '&nbsp;Non-parametric&nbsp;TI</label>\n'
        '  <label title="Overlay individual DUT measurement points on each box">'
        '<input type="checkbox" id="box_show_pts_chk" onchange="update()">'
        '&nbsp;Show&nbsp;points</label>\n'
        '  <button class="csv-btn" onclick="saveBoxCSV()">&#8595;&nbsp;CSV</button>\n'
        '</div>\n'
    )

    constants = "\n".join([
        f"var BOX_DATA={json.dumps(box_data)};",
        f"var BOX_STATS={json.dumps(stat_data_box)};",
        f"var BOX_TITLE={json.dumps(title)};",
        f"var BOX_FREQ_ORDER={json.dumps(freq_cat_order)};",
        f"var LO_SPEC={lo_js};",
        f"var HI_SPEC={hi_js};",
        f"var Y_LIM={json.dumps(y_lim)};",
        f"var Y_LABEL={json.dumps(y_label)};",
        f"var COND_DIMS={json.dumps(cond_dims)};",
        f"var TEMPS_PRESENT={json.dumps(all_temps)};",
        f"var PALETTE={json.dumps(palette)};",
        f"var ALL_BOX_SERIALS={json.dumps(all_box_serials or [])};",
    ])

    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f'<meta charset="utf-8"><title>{title}</title>\n'
        f"<style>{css}</style>\n"
        f"<script>{_get_plotlyjs()}</script>\n"
        "</head>\n<body>\n"
        + ctrl_bar + env_bar + filter_bar
        + '<div id="plot"></div>\n'
        + '<div style="display:flex;gap:8px;align-items:center;padding:4px 8px">\n'
        + '  <button class="toggle-btn" id="box_stat_toggle_btn"'
        ' onclick="toggleStatPanel()">&#9658; Statistics Table</button>\n'
        + '</div>\n'
        + '<div id="box_stat_panel" style="display:none;overflow-x:auto;padding:4px"></div>\n'
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
        unique_groups = df["Group"].dropna().unique()
        group_kv = {g: _parse_group_kv(g) for g in unique_groups}
        all_keys = set(k for kv in group_kv.values() for k in kv)
        cond_keys: list = []
        serial_keys: list = []
        for key in sorted(all_keys):
            if any(kw in key.lower() for kw in _serial_kws):
                serial_keys.append(key)
                continue
            vals = {kv.get(key, "") for kv in group_kv.values() if key in kv}
            if vals and sum(_serial_val.match(v) is not None for v in vals) / len(vals) > 0.5:
                serial_keys.append(key)
                continue
            if 1 < len(vals) <= 20:
                cond_keys.append(key)

        def _make_cond(g: str) -> str:
            kv = group_kv.get(g, {})
            parts = [f"{k}: {kv[k]}" for k in cond_keys if k in kv]
            return "  ".join(parts) if parts else "All"

        def _make_serial(g: str) -> str:
            kv = group_kv.get(g, {})
            for k in serial_keys:
                if k in kv:
                    return kv[k]
            return g

        df["_cond"] = df["Group"].map(_make_cond).fillna("All")
        df["_serial_id"] = df["Group"].map(_make_serial).fillna("unknown")
    else:
        df["_cond"] = "All"
        df["_serial_id"] = "unknown"

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
            m = re.match(r"(.+?):\s*(\S+)", part.strip())
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

    lo_js = "null" if np.isnan(lo_spec) else repr(float(lo_spec))
    hi_js = "null" if np.isnan(hi_spec) else repr(float(hi_spec))
    palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
               "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]

    all_box_serials = sorted({d["s"]
                              for cd in box_data
                              for fs in cd.get("freq_stats", [])
                              for d in fs.get("vals_detail", [])})

    html = _build_box_interactive_html(
        box_data, stat_data_box, freq_cat_order, all_temps,
        cond_dims, lo_js, hi_js, title, y_label, y_lim, palette, all_box_serials,
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")
