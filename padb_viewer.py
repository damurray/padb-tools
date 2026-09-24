"""padb_viewer.py -- local-server viewer for very large PADB/compare datasets.

The self-contained interactive HTML pages this tool normally emits embed every
data point as JSON, so a giant analytic (phase-noise, VSWR, wide sweeps) can
produce a 300 MB - 2 GB page no browser can open. This viewer solves that a
different way: it reads a compact **parquet** of the same data and serves only a
server-side-decimated / filtered slice to the browser over http://localhost --
so the browser never has to load the whole dataset, and there is no ceiling.

Why a local server rather than a static WASM page: a page opened straight off a
\\share is a file:// (null) origin, where Chromium blocks ES-module scripts,
Workers and fetch() -- exactly what an in-browser DuckDB-WASM viewer needs. A
localhost server sidesteps all of that, needs no JS bundler or CDN, and can be
frozen to a single .exe (PyInstaller) so users without Python just double-click.

Prototype scope: the scatter view (value vs swept-x), with per-Site overlay,
a frequency-range filter and server-side min/max-envelope decimation. Other
views (boxplot/summary/...) can be added the same way.

Usage:
    py padb_viewer.py <folder-or-parquet>   # serves that folder's parquet
    py padb_viewer.py <...> --port 8799 --no-open
    py padb_viewer.py <...> --value "Col name" --x "Frequency (MHz)"   # overrides
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pandas as pd
from flask import Flask, Response, jsonify, request

try:
    import plotly.offline as _plo
    _PLOTLY = _plo.get_plotlyjs()
except Exception:  # pragma: no cover
    _PLOTLY = "console.error('plotly.js not available');"

_META_COLS = {
    "analysis type", "model(s)", "algorithm -> result", "units", "group",
    "station", "test step",
}


def _free_port(start: int, tries: int = 50) -> int:
    """First bindable localhost port at/after `start`. Lets several folders'
    viewers run at once (shared exe + a View.bat per folder) without colliding
    on one fixed port."""
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


_UNIT_HZ = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}


def _load_bands(path: Path, data_unit: str) -> list[dict]:
    """Read a named-band JSON and convert each band's lo/hi from the config's
    unit into the data's x-axis unit. Config shape:
        {"unit": "Hz",
         "bands": [{"name": "Low Band", "lo": 8e6, "hi": 1500e6}, ...]}
    `unit` is optional (defaults to the data's own unit -> no conversion).
    A bad/missing file yields no bands (the band bar just doesn't show)."""
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  [bands] ignoring {path}: {exc}", flush=True)
        return []
    conf_u = (cfg.get("unit") or data_unit or "").strip().lower()
    cf, df = _UNIT_HZ.get(conf_u), _UNIT_HZ.get((data_unit or "").strip().lower())
    scale = (cf / df) if (cf and df) else 1.0  # config-unit -> data-unit
    out = []
    for b in cfg.get("bands", []):
        try:
            lo, hi = float(b["lo"]) * scale, float(b["hi"]) * scale
        except (KeyError, TypeError, ValueError):
            continue
        if hi < lo:
            lo, hi = hi, lo
        out.append({"name": str(b.get("name", "band")), "lo": lo, "hi": hi})
    if out:
        print(f"  [bands] loaded {len(out)} band(s) from {Path(path).name} "
              f"(config unit {conf_u or '?'} -> data unit {data_unit or '?'}, x{scale:g})",
              flush=True)
    return out


def _default_target() -> Path:
    """Where to look for a parquet when no target is given. For a frozen .exe
    that's the folder the .exe sits in (so a user drops PADB_Viewer.exe into a
    results folder and double-clicks); otherwise the current directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _find_parquet(target: Path) -> Path:
    """Resolve a folder-or-file arg to a single parquet path."""
    if target.is_file() and target.suffix.lower() == ".parquet":
        return target
    if target.is_dir():
        cands = sorted(target.glob("*.parquet"))
        if not cands:
            sys.exit(f"No .parquet found in {target}")
        # prefer a data/merged parquet over anything else
        for c in cands:
            if "merged" in c.name.lower() or "data" in c.name.lower():
                return c
        return cands[0]
    sys.exit(f"Not a parquet file or folder: {target}")


def _detect_columns(schema_names, x_override=None, value_override=None):
    """Pick x / value / serial / group columns from the parquet schema, using
    the same keyword rules padb_plots' scatter loader uses."""
    lower = {n.lower(): n for n in schema_names}

    def first(pred):
        for n in schema_names:
            if pred(n.lower()):
                return n
        return None

    x_col = x_override or first(lambda l: "frequency" in l or "x value" in l)
    serial_col = first(lambda l: "serial num" in l or "serial no" in l
                       or l == "serial number" or l == "serial")
    group_col = lower.get("group")
    # Temperature is a real column (PADB writes it as "Test Step"; some pods use a
    # "temp"-named key) -- reading it is reliable, unlike parsing it out of Group.
    temp_col = first(lambda l: l == "test step" or "temp" in l)
    value_col = value_override
    if not value_col:
        skip = _META_COLS | {"", (x_col or "").lower(), (serial_col or "").lower(),
                             (temp_col or "").lower()}
        for n in schema_names:
            l = n.lower()
            if l in skip or "limit" in l:
                continue
            value_col = n  # first plausible non-meta, non-limit column
            break
    return x_col, value_col, serial_col, group_col, temp_col


class DataSet:
    """Loads the needed columns once, keeps them in memory (parquet is tiny;
    the in-memory frame is a few numeric/category columns) and answers
    filtered+decimated scatter queries."""

    def __init__(self, parquet_path: Path, x_override=None, value_override=None):
        self.path = parquet_path
        names = pq.ParquetFile(parquet_path).schema.names
        self.x_col, self.value_col, self.serial_col, self.group_col, self.temp_col = \
            _detect_columns(names, x_override, value_override)
        if not self.x_col or not self.value_col:
            sys.exit(f"Could not detect x/value columns in {parquet_path.name} "
                     f"(cols: {names}). Use --x / --value to set them.")
        # Per-point pass/fail limit columns (for the overview's All/Passing/Failing
        # filter and the spec-segment stepper). Optional -- absent -> those controls
        # gray out. Same keyword rule as the html loaders (Upper/Lower Limit, else Spec).
        _low = {n.lower(): n for n in names}
        def _find(*subs):
            for k, orig in _low.items():
                if all(s in k for s in subs):
                    return orig
            return None
        self.hi_col = _find("upper", "limit") or _find("upper", "spec")
        self.lo_col = _find("lower", "limit") or _find("lower", "spec")
        cols = [c for c in (self.x_col, self.value_col, self.serial_col,
                            self.group_col, self.temp_col, self.hi_col, self.lo_col) if c]
        df = pq.read_table(parquet_path, columns=cols).to_pandas()
        df = df.rename(columns={self.x_col: "x", self.value_col: "y"})
        df["x"] = pd.to_numeric(df["x"], errors="coerce")
        df["y"] = pd.to_numeric(df["y"], errors="coerce")
        df["yhi"] = pd.to_numeric(df[self.hi_col], errors="coerce") if self.hi_col else np.nan
        df["ylo"] = pd.to_numeric(df[self.lo_col], errors="coerce") if self.lo_col else np.nan
        df = df.dropna(subset=["x", "y"])
        # Site is embedded in the Group text as "Site: <name>" (see
        # _build_compare_csv). Parse it out; absent -> single "(all)" site.
        if self.group_col and self.group_col in df.columns:
            site = df[self.group_col].astype(str).str.extract(r"Site:\s*([^\s]+)", expand=False)
            df["site"] = site.fillna("(all)").astype("category")
        else:
            df["site"] = pd.Categorical(["(all)"] * len(df))
        # Serial: a dedicated column if present, else parse it out of the Group text
        # ("Serial Number: <s>", else a bare serial-like token) -- the merged compare
        # parquet has no Serial column (serial lives in Group), so without this the header
        # showed "1 serials" (David 2026-09-24). Same source the html/reference views use.
        if self.serial_col and self.serial_col in df.columns:
            df["serial"] = df[self.serial_col].astype("category")
        elif self.group_col and self.group_col in df.columns:
            g = df[self.group_col].astype(str)
            ser = g.str.extract(r"Serial\s*(?:Number|No|Num)?\s*:\s*([^\s|]+)", expand=False)
            if ser.isna().all():
                ser = g.str.extract(r"\b([A-Za-z]{2,3}\d{5,})\b", expand=False)
            df["serial"] = ser.fillna("?").astype("category")
        else:
            df["serial"] = pd.Categorical(["?"] * len(df))
        # Temperature is read from its real column (Test Step / temp-named), NOT parsed
        # out of Group -- the reliable source (David 2026-09-17). env_coverage &
        # distribution need >=2 temperatures (a Room baseline + non-Room); room-only if
        # there are fewer than 2 distinct temperatures -- matches the html build's own
        # auto view-selection, which omits env/distribution for Room-only.
        if self.temp_col and self.temp_col in df.columns:
            df["temp"] = df[self.temp_col].astype(str).str.strip().replace("", "Room").astype("category")
        else:
            df["temp"] = pd.Categorical(["Room"] * len(df))
        # Per-point pass/fail (fail = crosses a PRESENT limit; unscored = no limit).
        _hi = df["yhi"] if "yhi" in df else pd.Series(np.nan, index=df.index)
        _lo = df["ylo"] if "ylo" in df else pd.Series(np.nan, index=df.index)
        df["scored"] = _hi.notna() | _lo.notna()
        df["fail"] = ((_hi.notna() & (df["y"] > _hi)) | (_lo.notna() & (df["y"] < _lo)))
        self.has_limits = bool(df["scored"].any())
        self.df = df[["x", "y", "site", "serial", "temp", "yhi", "ylo", "scored", "fail"]] \
            .sort_values("x").reset_index(drop=True)
        self.x_min = float(self.df["x"].min())
        self.x_max = float(self.df["x"].max())
        self.sites = list(map(str, self.df["site"].cat.categories))
        self.temps = list(map(str, self.df["temp"].cat.categories))
        self.is_room_only = len(self.temps) < 2
        self.value_label = self.value_col
        self.x_label = self.x_col
        m = re.search(r"\(([^)]+)\)", self.x_label)
        self.x_unit = m.group(1) if m else ""
        self.bands: list[dict] = []  # filled by _load_bands() in main()
        self.band_counts: list[int] = []  # rows per band, filled by compute_band_counts()
        self.max_band_rows: int = 0       # largest single band's row count

    def compute_band_counts(self):
        """Row count inside each named band, and the largest (drives the band-view load
        cap: a single band must always be openable, so the cap floors at the biggest band).
        Called from main() after bands are loaded."""
        self.band_counts, self.max_band_rows = [], 0
        x = self.df["x"]
        for b in self.bands:
            n = int(((x >= b["lo"]) & (x <= b["hi"])).sum())
            self.band_counts.append(n)
            b["count"] = n
        self.max_band_rows = max(self.band_counts) if self.band_counts else 0

    def meta(self):
        return {
            "parquet": self.path.name,
            "rows": int(len(self.df)),
            "x_label": self.x_label,
            "value_label": self.value_label,
            "x_min": self.x_min,
            "x_max": self.x_max,
            "sites": self.sites,
            "temps": self.temps,
            "n_serials": int(self.df["serial"].cat.categories.size),
            "x_unit": self.x_unit,
            "bands": self.bands,
            "band_counts": self.band_counts,
            "max_band_rows": self.max_band_rows,
            "band_cap": _effective_band_cap(self),
            "slow_band_rows": SLOW_BAND_ROWS,
            "slow_band_warn": self.max_band_rows > SLOW_BAND_ROWS,
            "is_room_only": bool(self.is_room_only),
            "has_limits": bool(self.has_limits),
        }

    def spec_segments(self):
        """Contiguous frequency bands over which the per-point spec/limit is constant --
        for the overview's spec-segment stepper (jump to each spec 'stair'). Uses the
        first limit at each x (a staircase spec is a function of x); returns [] when the
        data carries no limit. Mirrors the html views' getSpecSegments intent."""
        if not self.has_limits:
            return []
        per = (self.df.groupby("x", observed=True)
               .agg(yhi=("yhi", "first"), ylo=("ylo", "first")).reset_index().sort_values("x"))
        def _k(v):
            return round(float(v), 4) if pd.notna(v) else None
        def _v(v):
            return float(v) if pd.notna(v) else None
        segs, cur_lo, cur_key, cur_val, prev_x = [], None, None, None, None
        for row in per.itertuples(index=False):
            k = (_k(row.yhi), _k(row.ylo))
            if cur_key is None:
                cur_lo, cur_key, cur_val = row.x, k, (_v(row.yhi), _v(row.ylo))
            elif k != cur_key:
                segs.append({"lo": float(cur_lo), "hi": float(prev_x),
                             "hi_val": cur_val[0], "lo_val": cur_val[1]})
                cur_lo, cur_key, cur_val = row.x, k, (_v(row.yhi), _v(row.ylo))
            prev_x = row.x
        if cur_lo is not None:
            segs.append({"lo": float(cur_lo), "hi": float(prev_x),
                         "hi_val": cur_val[0], "lo_val": cur_val[1]})
        # Make contiguous (each segment's hi = next segment's lo; last -> x_max) so a
        # step covers the whole staircase with no gaps.
        for i in range(len(segs) - 1):
            segs[i]["hi"] = segs[i + 1]["lo"]
        if segs:
            segs[-1]["hi"] = self.x_max
        return segs

    def scatter(self, flo, fhi, sites, maxpts, temps=None, pf="all"):
        d = self.df
        m = (d["x"] >= flo) & (d["x"] <= fhi)
        if sites:
            m &= d["site"].isin(sites)
        if temps:
            m &= d["temp"].isin(temps)
        # Pass/fail: failing = true fails; passing = everything that isn't a true fail
        # (pass + no-limit), matching the html scatter's _scatRowFail convention.
        if pf == "fail":
            m &= d["fail"]
        elif pf == "pass":
            m &= ~d["fail"]
        sub = d[m]
        n_total = int(len(sub))
        traces = []
        for site, g in sub.groupby("site", observed=True):
            if g.empty:
                continue
            gx = g["x"].to_numpy()
            gy = g["y"].to_numpy()
            xs, ys = _decimate(gx, gy, flo, fhi, maxpts)
            traces.append({"site": str(site), "x": xs, "y": ys, "n": int(len(gx))})
        n_ret = sum(len(t["x"]) for t in traces)
        return {"traces": traces, "n_total": n_total, "n_returned": n_ret}


def _decimate(x, y, flo, fhi, maxpts):
    """Min/max-envelope decimation: bucket x across [flo,fhi], keep the min and
    max y point in each bucket. Preserves the visible envelope (outliers, spec
    excursions) that plain sampling would drop. Mirrors padb_plots'
    _decimate_dense_series idea, done server-side."""
    n = len(x)
    if n <= maxpts:
        return x.tolist(), y.tolist()
    nb = max(1, maxpts // 2)
    span = (fhi - flo) or 1.0
    idx = np.clip(((x - flo) / span * nb).astype(int), 0, nb - 1)
    keep = np.zeros(n, dtype=bool)
    order = np.argsort(idx, kind="stable")
    idx_s = idx[order]
    # boundaries of each bucket in the sorted-by-bucket order
    starts = np.searchsorted(idx_s, np.arange(nb), side="left")
    ends = np.searchsorted(idx_s, np.arange(nb), side="right")
    for b in range(nb):
        s, e = starts[b], ends[b]
        if s == e:
            continue
        block = order[s:e]
        yb = y[block]
        keep[block[yb.argmin()]] = True
        keep[block[yb.argmax()]] = True
    xs = x[keep]
    ys = y[keep]
    o = np.argsort(xs, kind="stable")
    return xs[o].tolist(), ys[o].tolist()


app = Flask(__name__)
DS: DataSet | None = None

_PAGE = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>PADB Viewer</title>
<script src="/plotly.js"></script>
<style>
 body{font:13px system-ui,Segoe UI,sans-serif;margin:0;background:#f7f7f8;color:#222}
 header{background:#0066cc;color:#fff;padding:8px 14px;font-weight:600}
 #bar{padding:8px 14px;background:#fff;border-bottom:1px solid #ddd;display:flex;
      gap:16px;align-items:center;flex-wrap:wrap}
 #bar label{font-size:12px}
 input[type=number]{width:90px}
 #status{margin-left:auto;color:#666;font-size:12px}
 #plot{height:calc(100vh - 96px)}
 .sitebox{display:inline-flex;gap:8px;align-items:center}
</style></head><body>
<header>PADB Viewer <span id="ttl" style="font-weight:400"></span></header>
<div id="bar">
  <span><b id="xlbl">x</b> min <input type="number" id="flo" step="any"></span>
  <span>max <input type="number" id="fhi" step="any"></span>
  <span>max points <input type="number" id="maxpts" value="4000" step="500"></span>
  <span class="sitebox" id="sites"></span>
  <span class="sitebox" id="temps"></span>
  <span class="sitebox" id="pfbox" title="Filter the overview points by pass/fail against each point's own limit. Failing = crosses a present limit; Passing = everything else (pass + no-limit). Needs a limit column in the data.">
    Show:
    <label><input type="radio" name="pf" value="all" checked onchange="update()">All</label>
    <label><input type="radio" name="pf" value="pass" onchange="update()">Passing</label>
    <label><input type="radio" name="pf" value="fail" onchange="update()">Failing</label></span>
  <button onclick="update()">Update</button>
  <button onclick="resetView()">Reset</button>
  <span id="status"></span>
</div>
<div id="segbar" style="padding:6px 14px;background:#f6f6ff;border-bottom:1px solid #ddd;font-size:12px">
  <b title="Step the frequency window through segments to minimize the data shown -- by named band, or by each spec/limit 'stair' (contiguous frequencies with a constant limit).">Segment step:</b>
  <select id="segbasis" onchange="_segLoad(true)">
    <option value="spec">Spec/limit stairs</option>
    <option value="band">Named bands</option>
  </select>
  <button onclick="_segStep(-1)">&#9664;&nbsp;Prev</button>
  <button onclick="_segStep(1)">Next&nbsp;&#9654;</button>
  <button onclick="resetView()" title="Return to the full x-range">Full range</button>
  <span id="seginfo" style="color:#888"></span>
</div>
<div id="viewbar" style="padding:6px 14px;background:#eef4ff;border-bottom:1px solid #ddd;font-size:12px">
  <b>Band view</b> <span style="color:#888">(the full interactive plot, with all filters, for the current x-range):</span>
  <span id="viewbtns"></span>
  <label style="margin-left:10px" title="Full render embeds every point -- enables Show-points, live serial/Y re-filtering, and the Site Population Check, but is slower and larger. Off (default) = fast lite render: exact boxes/stats/outliers, no per-point overlay.">
    <input type="checkbox" id="fullrender"> Full render (all points / Site check &mdash; slower)</label>
</div>
<div id="bandbar" style="padding:6px 14px;background:#f0f6ff;border-bottom:1px solid #ddd;display:none;font-size:12px">
  <b>Bands:</b> <span id="bands"></span>
</div>
<div id="plot"></div>
<div id="viewwrap" style="display:none;border-top:2px solid #0066cc;margin-top:6px">
  <div style="padding:6px 14px;background:#eef4ff;font-size:12px">
    <b id="vtitle">Band view</b> <span id="vstatus" style="color:#666"></span>
    <button onclick="document.getElementById('viewwrap').style.display='none'" style="float:right">Close</button>
  </div>
  <div style="position:relative">
    <div id="vbusy" style="display:none;position:absolute;inset:0;z-index:5;background:rgba(247,247,248,.92);
      display:none;flex-direction:column;align-items:center;justify-content:center;
      font:14px system-ui,Segoe UI,sans-serif;color:#333">
      <div style="width:40px;height:40px;border:4px solid #cfe0f5;border-top-color:#0066cc;border-radius:50%;
        animation:vspin .8s linear infinite;margin-bottom:12px"></div>
      <div id="vbusytxt">Rendering band view&hellip;</div></div>
    <iframe id="vframe" style="width:100%;height:78vh;border:0"></iframe>
  </div>
</div>
<style>@keyframes vspin{to{transform:rotate(360deg)}}</style>
<script>
let META=null;
async function boot(){
  META = await (await fetch('/api/meta')).json();
  document.getElementById('ttl').textContent = ' -- '+META.parquet+'  ('+META.rows.toLocaleString()+' rows, '+META.n_serials+' serials)';
  document.getElementById('xlbl').textContent = META.x_label;
  document.getElementById('flo').value = META.x_min;
  document.getElementById('fhi').value = META.x_max;
  // Pass/fail filter needs a limit column; gray it out (and default the segment stepper to
  // named bands) when the data carries none.
  if(!META.has_limits){
    document.querySelectorAll('input[name="pf"]').forEach(function(r){ if(r.value!=='all'){ r.disabled=true; }});
    var pfb=document.getElementById('pfbox'); if(pfb){ pfb.style.opacity='0.5'; pfb.title='No per-point limit in this data -- pass/fail filtering is unavailable.'; }
    var sbsel=document.getElementById('segbasis'); if(sbsel){ sbsel.querySelector('option[value="spec"]').disabled=true; sbsel.value='band'; }
  }
  const sb=document.getElementById('sites');
  META.sites.forEach(s=>{
    const id='site_'+s;
    sb.insertAdjacentHTML('beforeend',
      '<label><input type="checkbox" checked value="'+s+'" class="sitechk"> '+s+'</label>');
  });
  // Temperature filter (only meaningful with >1 temp) -- read from the real Test Step
  // column in the parquet, so it's reliable.
  if(META.temps && META.temps.length>1){
    const tb=document.getElementById('temps');
    tb.insertAdjacentHTML('beforeend','<b style="margin-left:8px">Temp:</b> ');
    META.temps.forEach(function(tp){
      tb.insertAdjacentHTML('beforeend',
        '<label><input type="checkbox" checked value="'+tp+'" class="tempchk"> '+tp+'</label>');
    });
  }
  // Band-view buttons render the full interactive view for the current x-range.
  // scatter/boxplot/stat_summary/summary always; env_coverage/distribution only for
  // multi-temp data (they need a Room baseline + non-Room) -- matches the html build's
  // auto view-selection, which omits them for Room-only.
  var vbtns=[['scatter','Scatter'],['boxplot','Boxplot'],['stat_summary','Stat Summary'],['summary','Summary']];
  if(META.is_room_only===false){ vbtns.push(['env_coverage','Env Coverage'],['distribution','Distribution']); }
  var vb=document.getElementById('viewbtns');
  vbtns.forEach(function(v){ var b=document.createElement('button'); b.textContent=v[1];
    b.style.marginRight='6px'; b.onclick=function(){openView(v[0]);}; vb.appendChild(b); });
  if(META.bands && META.bands.length){
    const bb=document.getElementById('bands');
    const slow=META.slow_band_rows||25000;
    META.bands.forEach((b,i)=>{
      const btn=document.createElement('button');
      const n=(META.band_counts&&META.band_counts[i])||b.count||0;
      const heavy=n>slow;
      btn.textContent=b.name+(n?(' ('+n.toLocaleString()+(heavy?' ⚠':'')+')'):'');
      btn.title='Set range to '+b.lo.toPrecision(5)+' .. '+b.hi.toPrecision(5)+' '+(META.x_unit||'')+
        (n?('  ('+n.toLocaleString()+' points'+(heavy?' -- band views may load slowly':'')+')'):'');
      if(heavy) btn.style.color='#b26a00';
      btn.style.marginRight='6px';
      btn.onclick=()=>setBand(b.lo,b.hi);
      bb.appendChild(btn);
    });
    if(META.slow_band_warn){
      bb.insertAdjacentHTML('beforeend',
        '<span style="color:#b26a00;font-size:12px;margin-left:8px">⚠ largest band ~'+
        (META.max_band_rows||0).toLocaleString()+' points; band views may load slowly</span>');
    }
    document.getElementById('bandbar').style.display='block';
  }
  update();
}
function setBand(lo,hi){
  document.getElementById('flo').value=lo;
  document.getElementById('fhi').value=hi;
  update();
}
// --- Segment stepper: jump the x-window to each segment (named band or spec/limit stair),
//     minimizing the data shown per step. Spec segments come from /api/segments. ----------
let _segs=[], _segIdx=-1;
async function _segLoad(reset){
  const basis=document.getElementById('segbasis').value;
  if(basis==='band'){ _segs=(META.bands||[]).map(b=>({lo:b.lo,hi:b.hi,name:b.name})); }
  else { try{ const r=await (await fetch('/api/segments')).json(); _segs=(r.spec||[]); }catch(e){ _segs=[]; } }
  if(reset) _segIdx=-1;
  const info=document.getElementById('seginfo');
  info.textContent=_segs.length? (_segs.length+' '+(basis==='band'?'band':'spec')+' segment(s) -- Prev/Next to step')
    : ('no '+(basis==='band'?'named bands (bands.json)':'per-point limit')+' to segment by');
  return _segs.length;
}
function _segValUnit(){
  // Pull a short unit out of the value label, e.g. "Power (dBc)" -> "dBc".
  var m=/\(([^)]+)\)\s*$/.exec(META.value_label||''); return m?m[1]:'';
}
function _segSpecStr(s){
  // Current spec/TLL limit(s) for this stair, so the user sees the value they're stepping to.
  if(s.hi_val==null && s.lo_val==null) return '';
  var u=_segValUnit(), parts=[];
  if(s.hi_val!=null) parts.push('Upper '+(+s.hi_val).toPrecision(5));
  if(s.lo_val!=null) parts.push('Lower '+(+s.lo_val).toPrecision(5));
  return '  |  spec/TLL: '+parts.join(', ')+(u?(' '+u):'');
}
async function _segStep(d){
  if(!_segs.length){ if(!(await _segLoad(true))) return; }
  _segIdx=(_segIdx + d + _segs.length) % _segs.length;
  const s=_segs[_segIdx];
  document.getElementById('flo').value=s.lo;
  document.getElementById('fhi').value=s.hi;
  document.getElementById('seginfo').textContent='segment '+(_segIdx+1)+'/'+_segs.length+
    (s.name?(' ('+s.name+')'):'')+': '+(+s.lo).toPrecision(5)+' .. '+(+s.hi).toPrecision(5)+' '+(META.x_unit||'')+
    _segSpecStr(s);
  update();
}
function selectedSites(){return [...document.querySelectorAll('.sitechk:checked')].map(c=>c.value);}
function selectedTemps(){return [...document.querySelectorAll('.tempchk:checked')].map(c=>c.value);}
function resetView(){document.getElementById('flo').value=META.x_min;document.getElementById('fhi').value=META.x_max;update();}
// The main plot is server-side decimated for the current [flo,fhi] band, so a
// drag-zoom / Autoscale / Reset-axes must drive the frequency filter (and re-query)
// rather than just re-viewport the already-drawn points. Mirrors the html views'
// _onPlotRelayout. _syncing swallows the react-induced relayout so it can't loop.
let _relayoutHooked=false, _syncing=false, _curView=null, _vRefreshTimer=null;
function _onRelayout(ed){
  if(_syncing||!ed) return;
  // Modebar Autoscale / Reset axes -> return the frequency filter to the full range.
  if(ed['xaxis.autorange']){
    document.getElementById('flo').value=META.x_min;
    document.getElementById('fhi').value=META.x_max;
    update();
    return;
  }
  // Drag-zoom on x -> narrow the frequency filter to the zoomed band and re-query,
  // so server-side decimation refines detail instead of just magnifying the envelope.
  let lo,hi;
  if(ed['xaxis.range']){ lo=ed['xaxis.range'][0]; hi=ed['xaxis.range'][1]; }
  else if(ed['xaxis.range[0]']!==undefined){ lo=ed['xaxis.range[0]']; hi=ed['xaxis.range[1]']; }
  if(lo!==undefined&&hi!==undefined&&isFinite(+lo)&&isFinite(+hi)){
    if(+hi<+lo){ const t=lo; lo=hi; hi=t; }
    document.getElementById('flo').value=+lo;
    document.getElementById('fhi').value=+hi;
    update();
  }
}
function openView(v){
  _curView=v;   // remember which band view is open so a band change (zoom/Update) can refresh it
  const flo=document.getElementById('flo').value, fhi=document.getElementById('fhi').value;
  const full=document.getElementById('fullrender').checked?1:0;
  const wrap=document.getElementById('viewwrap');
  wrap.style.display='block';
  document.getElementById('vtitle').textContent=v+'  ['+flo+' .. '+fhi+' '+META.x_label+']'+(full?'  (full)':'  (lite)');
  document.getElementById('vstatus').textContent=(full?'full':'lite')+' render for this band'+(full?' (embeds all points -- may take a while)...':'...');
  var vb=document.getElementById('vbusy');
  if(vb){ vb.style.display='flex'; var bt=document.getElementById('vbusytxt');
    if(bt) bt.textContent='Rendering '+v+' for ['+flo+'..'+fhi+' '+META.x_label+']'+(full?' (full - all points)':'')+'…'; }
  const f=document.getElementById('vframe');
  f.onload=()=>{document.getElementById('vstatus').textContent='rendered ('+(full?'full: all points, Site check':'lite: exact boxes/stats, no overlay')+').';
    var _vb=document.getElementById('vbusy'); if(_vb) _vb.style.display='none';};
  f.src='/view?view='+encodeURIComponent(v)+'&flo='+flo+'&fhi='+fhi+'&full='+full;
  wrap.scrollIntoView({behavior:'smooth'});
}
async function update(){
  const floN=parseFloat(document.getElementById('flo').value),
        fhiN=parseFloat(document.getElementById('fhi').value);
  const flo=isFinite(floN)?floN:META.x_min, fhi=isFinite(fhiN)?fhiN:META.x_max;
  const maxpts=document.getElementById('maxpts').value;
  const sites=selectedSites().join(',');
  const temps=selectedTemps().join('|');
  const pf=(document.querySelector('input[name="pf"]:checked')||{}).value||'all';
  document.getElementById('status').textContent='querying...';
  const t0=performance.now();
  const r=await (await fetch(`/api/scatter?flo=${flo}&fhi=${fhi}&maxpts=${maxpts}&sites=${encodeURIComponent(sites)}&temps=${encodeURIComponent(temps)}&pf=${pf}`)).json();
  const traces=r.traces.map(t=>({x:t.x,y:t.y,mode:'markers',type:'scattergl',
      name:t.site+' (n='+t.n.toLocaleString()+')',marker:{size:4,opacity:0.55}}));
  _syncing=true;
  // Pin x to the queried band so react() doesn't autorange-fire a spurious relayout
  // that _onRelayout would read as a Reset. Modebar Autoscale/Reset still emit
  // xaxis.autorange and are handled above.
  Plotly.react('plot',traces,{margin:{t:10,r:10},
      xaxis:{title:{text:META.x_label},range:[flo,fhi]},
      yaxis:{title:{text:META.value_label}},legend:{orientation:'h'}},{responsive:true});
  if(!_relayoutHooked){ document.getElementById('plot').on('plotly_relayout',_onRelayout); _relayoutHooked=true; }
  setTimeout(()=>{_syncing=false;},0);
  document.getElementById('status').textContent =
     r.n_total.toLocaleString()+' pts in view -> '+r.n_returned.toLocaleString()+
     ' drawn ('+Math.round(performance.now()-t0)+' ms)';
  // If a band view is open, refresh it for the NEW band (debounced, so a drag-zoom that
  // fires many updates only re-renders once it settles) -- otherwise the open view would
  // stay stale/showing the old band's guard message after you zoom (David 2026-09-24).
  var vw=document.getElementById('viewwrap');
  if(_curView && vw && vw.style.display!=='none'){
    document.getElementById('vstatus').textContent='band changed -> refreshing '+_curView+' for ['+flo+'..'+fhi+']...';
    clearTimeout(_vRefreshTimer);
    _vRefreshTimer=setTimeout(function(){ openView(_curView); }, 700);
  }
}
boot();
</script></body></html>"""


@app.route("/")
def index():
    return Response(_PAGE, mimetype="text/html")


@app.route("/plotly.js")
def plotlyjs():
    return Response(_PLOTLY, mimetype="text/javascript")


@app.route("/api/meta")
def api_meta():
    return jsonify(DS.meta())


@app.route("/api/scatter")
def api_scatter():
    flo = float(request.args.get("flo", DS.x_min))
    fhi = float(request.args.get("fhi", DS.x_max))
    maxpts = max(100, min(50000, int(float(request.args.get("maxpts", 4000)))))
    sites = [s for s in (request.args.get("sites", "") or "").split(",") if s]
    temps = [t for t in (request.args.get("temps", "") or "").split("|") if t]
    pf = request.args.get("pf", "all")
    if pf not in ("all", "pass", "fail"):
        pf = "all"
    return jsonify(DS.scatter(flo, fhi, sites, maxpts, temps, pf))


@app.route("/api/segments")
def api_segments():
    return jsonify({"spec": DS.spec_segments(), "bands": DS.bands})


# --- Band-windowed rendering of the EXISTING interactive views (option B) -----
# The heavy giant HTML is unopenable only because it embeds the whole dataset.
# Here we slice the parquet to one frequency band, write it back out as a CSV
# with the original headers, and run the REAL padb_v2 pipeline for that view on
# just that slice -- so the page the user gets is the identical interactive view
# (all controls, Site compare, stats), just band-sized so it actually loads.
_band_cache: dict = {}

# A band view is a FULL padb_v2 render that embeds every point in the band (per-condition
# stats/KDE for boxplot/distribution; all points for scatter). A full-range band is the
# whole dataset -- exactly the giant the viewer exists to avoid: it renders slowly and the
# resulting iframe can be tens of MB (looks blank/broken while it grinds). Guard: above this
# row count, serve a short "narrow the band" page for ANY band view (David 2026-09-24). The
# main overview plot stays available (server-side decimated) for picking a band.
VIEW_BAND_MAX_ROWS = 60000
# Above this many points in the largest single named band, band views are flagged as
# "may load slowly" in the UI (David 2026-09-24) -- the band itself still opens.
SLOW_BAND_ROWS = 25000


def _effective_band_cap(ds=None) -> int:
    """The band-view row cap. A single named band must ALWAYS be openable, so when bands
    are defined the cap floors at the largest band's row count (David 2026-09-24: "limit
    max load size to the max number of points in the largest band"); otherwise the fixed
    default applies. Arbitrary zooms wider than the biggest band still hit the guard."""
    ds = ds if ds is not None else DS
    mx = getattr(ds, "max_band_rows", 0) if ds is not None else 0
    return max(VIEW_BAND_MAX_ROWS, int(mx or 0))


def _band_guard_html(view: str, n: int, flo: float, fhi: float) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:Arial,sans-serif;margin:0;padding:28px;color:#333;"
        "background:#fff}.card{max-width:640px;margin:6vh auto;border:1px solid #e0905a;"
        "background:#fff7ef;border-radius:8px;padding:20px 24px}h2{margin:0 0 8px;color:#c04000}"
        "code{background:#eee;padding:1px 5px;border-radius:3px}</style></head><body>"
        f"<div class='card'><h2>Band too large for the {view} view</h2>"
        f"<p>This frequency band (<b>{flo:g} - {fhi:g}</b>) has <b>{n:,} points</b>. "
        f"A band view embeds every point in the band, so a band this large renders slowly "
        f"and produces a very heavy page &mdash; the case this viewer exists to avoid.</p>"
        "<p><b>What to do:</b> narrow the frequency band first &mdash; drag-select or use the "
        "modebar Zoom on the main overview plot above (it re-queries as you zoom), or type a "
        "smaller min/max and click <b>Update</b> &mdash; then open this view again. The main "
        "overview plot itself stays fast at any size (it is server-side decimated).</p>"
        f"<p style='color:#777;font-size:12px'>Threshold: {_effective_band_cap():,} points "
        "(a single named band always opens; wider ranges are capped here).</p></div></body></html>"
    )


def _render_view_band(view: str, flo: float, fhi: float, full: bool = False) -> tuple[str, int]:
    """Render `view` (e.g. 'boxplot') for the frequency band [flo,fhi] via the
    real padb_v2 pipeline on a parquet slice. Returns (html_path, n_rows).
    Cached per (view, band, full).

    full=False (default) is the fast "lite" render: for the boxplot it drops the
    per-point overlay (box_drop_points) so the page is small. full=True embeds
    every point -- slower/larger, but enables Show-points, live serial/Y
    re-filtering, and the Site Population Check."""
    import pyarrow as pa
    import pyarrow.parquet as pqm
    import pyarrow.compute as pc
    import pyarrow.csv as pacsv

    key = (view, round(flo, 6), round(fhi, 6), bool(full))
    cached = _band_cache.get(key)
    if cached and os.path.exists(cached[0]):
        return cached

    # Read the raw parquet once and reuse it across band requests (was re-read every call).
    t = getattr(DS, "_raw_table", None)
    if t is None:
        t = pqm.read_table(str(DS.path))
        try: DS._raw_table = t
        except Exception: pass
    col = pc.cast(t.column(DS.x_col), pa.float64())
    mask = pc.and_(pc.greater_equal(col, pa.scalar(flo)),
                   pc.less_equal(col, pa.scalar(fhi)))
    t2 = t.filter(mask)
    n = t2.num_rows

    # Large-band guard: any band view over the row cap gets a "narrow the band" page.
    # The cap floors at the largest named band so single-band views always render.
    if n > _effective_band_cap():
        tmp = Path(tempfile.mkdtemp(prefix="padbview_"))
        out = str(tmp / f"guard_{view}.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(_band_guard_html(view, n, flo, fhi))
        _band_cache[key] = (out, n)
        return out, n

    tmp = Path(tempfile.mkdtemp(prefix="padbview_"))
    band_csv = tmp / "band.csv"
    pacsv.write_csv(t2, str(band_csv))

    import padb_v2  # lazy: pulls in padb_plots/scipy only when a view is rendered
    cfg = {
        "views": [view],
        "title_prefix": f"Band_{flo:g}_{fhi:g}",
        "publish_to": "",          # never publish a scratch render
        "export_parquet": False,   # don't re-emit a parquet for the slice
    }
    if view == "boxplot" and not full:
        cfg["box_drop_points"] = True   # lite: exact stats, no per-point overlay
    if len(DS.sites) >= 2:
        cfg["primary_site"] = DS.sites[0]
    padb_v2.generate_report(band_csv, cfg, tmp)
    cands = sorted(tmp.glob(f"*_{view}.html"))
    out = str(cands[0]) if cands else ""
    # Efficiency: the band HTML is regenerated on every request (unlike the build-once
    # share HTML), and ~4.5 MB of it is inline Plotly.js re-embedded each time. Point it at
    # the viewer's own /plotly.js route instead, so the browser downloads/parses Plotly ONCE
    # and caches it across every band view -- each band HTML drops ~4.5 MB and the iframe
    # loads far faster (David 2026-09-24: "band render longer than the html"). Self-contained
    # inlining only matters for the shared .html files, not these in-server renders.
    if out:
        try:
            import padb_plots as _pp
            marker = "<script>" + _pp._get_plotlyjs() + "</script>"
            _t = Path(out).read_text(encoding="utf-8")
            if marker in _t:
                Path(out).write_text(
                    _t.replace(marker, '<script src="/plotly.js"></script>', 1), encoding="utf-8")
        except Exception:
            pass
    _band_cache[key] = (out, n)
    return out, n


@app.route("/view")
def api_view():
    view = request.args.get("view", "boxplot")
    flo = float(request.args.get("flo", DS.x_min))
    fhi = float(request.args.get("fhi", DS.x_max))
    full = request.args.get("full", "0") in ("1", "true", "True")
    if view in ("env_coverage", "distribution") and getattr(DS, "is_room_only", False):
        return Response(
            f"<pre>{view} needs multi-temperature data (a Room baseline + non-Room "
            f"readings) -- this dataset is Room-only, so it isn't available (same as the "
            f"html build, which omits it for Room-only data).</pre>", mimetype="text/html")
    try:
        path, n = _render_view_band(view, flo, fhi, full=full)
    except Exception as exc:  # surface the real error in the iframe
        return Response(f"<pre>render error: {exc}</pre>", mimetype="text/html", status=500)
    if not path or not os.path.exists(path):
        return Response("<pre>render produced no output</pre>", mimetype="text/html", status=500)
    return Response(Path(path).read_text(encoding="utf-8"), mimetype="text/html")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default=None,
                    help="Results folder or a .parquet file "
                         "(default: the folder the viewer is run from / sits in)")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--x", help="Exact x-axis column name (override auto-detect)")
    ap.add_argument("--value", help="Exact value column name (override auto-detect)")
    ap.add_argument("--no-open", action="store_true", help="Do not open a browser")
    ap.add_argument("--bands", help="JSON file of named frequency bands "
                    "(default: bands.json / padb_viewer_bands.json next to the parquet)")
    args = ap.parse_args(argv)

    global DS
    target = Path(args.target).resolve() if args.target else _default_target()
    pqpath = _find_parquet(target)
    print(f"Loading {pqpath} ...", flush=True)
    DS = DataSet(pqpath, x_override=args.x, value_override=args.value)

    bands_path = Path(args.bands).resolve() if args.bands else None
    if not bands_path:
        for cand in ("bands.json", "padb_viewer_bands.json"):
            p = pqpath.parent / cand
            if p.exists():
                bands_path = p
                break
    if bands_path and bands_path.exists():
        DS.bands = _load_bands(bands_path, DS.x_unit)
    DS.compute_band_counts()
    m = DS.meta()
    print(f"  {m['rows']:,} rows | x={m['x_label']} [{m['x_min']:.4g}..{m['x_max']:.4g}] "
          f"| value={m['value_label']} | sites={m['sites']}", flush=True)
    port = _free_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Serving {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main()
