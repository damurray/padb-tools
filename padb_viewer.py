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
import re
import sys
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
    value_col = value_override
    if not value_col:
        skip = _META_COLS | {"", (x_col or "").lower(), (serial_col or "").lower()}
        for n in schema_names:
            l = n.lower()
            if l in skip or "limit" in l:
                continue
            value_col = n  # first plausible non-meta, non-limit column
            break
    return x_col, value_col, serial_col, group_col


class DataSet:
    """Loads the needed columns once, keeps them in memory (parquet is tiny;
    the in-memory frame is a few numeric/category columns) and answers
    filtered+decimated scatter queries."""

    def __init__(self, parquet_path: Path, x_override=None, value_override=None):
        self.path = parquet_path
        names = pq.ParquetFile(parquet_path).schema.names
        self.x_col, self.value_col, self.serial_col, self.group_col = _detect_columns(
            names, x_override, value_override)
        if not self.x_col or not self.value_col:
            sys.exit(f"Could not detect x/value columns in {parquet_path.name} "
                     f"(cols: {names}). Use --x / --value to set them.")
        cols = [c for c in (self.x_col, self.value_col, self.serial_col, self.group_col) if c]
        df = pq.read_table(parquet_path, columns=cols).to_pandas()
        df = df.rename(columns={self.x_col: "x", self.value_col: "y"})
        df["x"] = pd.to_numeric(df["x"], errors="coerce")
        df["y"] = pd.to_numeric(df["y"], errors="coerce")
        df = df.dropna(subset=["x", "y"])
        # Site is embedded in the Group text as "Site: <name>" (see
        # _build_compare_csv). Parse it out; absent -> single "(all)" site.
        if self.group_col and self.group_col in df.columns:
            site = df[self.group_col].astype(str).str.extract(r"Site:\s*([^\s]+)", expand=False)
            df["site"] = site.fillna("(all)").astype("category")
        else:
            df["site"] = pd.Categorical(["(all)"] * len(df))
        if self.serial_col and self.serial_col in df.columns:
            df["serial"] = df[self.serial_col].astype("category")
        else:
            df["serial"] = pd.Categorical(["?"] * len(df))
        self.df = df[["x", "y", "site", "serial"]].sort_values("x").reset_index(drop=True)
        self.x_min = float(self.df["x"].min())
        self.x_max = float(self.df["x"].max())
        self.sites = list(map(str, self.df["site"].cat.categories))
        self.value_label = self.value_col
        self.x_label = self.x_col

    def meta(self):
        return {
            "parquet": self.path.name,
            "rows": int(len(self.df)),
            "x_label": self.x_label,
            "value_label": self.value_label,
            "x_min": self.x_min,
            "x_max": self.x_max,
            "sites": self.sites,
            "n_serials": int(self.df["serial"].cat.categories.size),
        }

    def scatter(self, flo, fhi, sites, maxpts):
        d = self.df
        m = (d["x"] >= flo) & (d["x"] <= fhi)
        if sites:
            m &= d["site"].isin(sites)
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
  <button onclick="update()">Update</button>
  <button onclick="resetView()">Reset</button>
  <span id="status"></span>
</div>
<div id="plot"></div>
<script>
let META=null;
async function boot(){
  META = await (await fetch('/api/meta')).json();
  document.getElementById('ttl').textContent = ' -- '+META.parquet+'  ('+META.rows.toLocaleString()+' rows, '+META.n_serials+' serials)';
  document.getElementById('xlbl').textContent = META.x_label;
  document.getElementById('flo').value = META.x_min;
  document.getElementById('fhi').value = META.x_max;
  const sb=document.getElementById('sites');
  META.sites.forEach(s=>{
    const id='site_'+s;
    sb.insertAdjacentHTML('beforeend',
      '<label><input type="checkbox" checked value="'+s+'" class="sitechk"> '+s+'</label>');
  });
  update();
}
function selectedSites(){return [...document.querySelectorAll('.sitechk:checked')].map(c=>c.value);}
function resetView(){document.getElementById('flo').value=META.x_min;document.getElementById('fhi').value=META.x_max;update();}
async function update(){
  const flo=document.getElementById('flo').value, fhi=document.getElementById('fhi').value;
  const maxpts=document.getElementById('maxpts').value;
  const sites=selectedSites().join(',');
  document.getElementById('status').textContent='querying...';
  const t0=performance.now();
  const r=await (await fetch(`/api/scatter?flo=${flo}&fhi=${fhi}&maxpts=${maxpts}&sites=${encodeURIComponent(sites)}`)).json();
  const traces=r.traces.map(t=>({x:t.x,y:t.y,mode:'markers',type:'scattergl',
      name:t.site+' (n='+t.n.toLocaleString()+')',marker:{size:4,opacity:0.55}}));
  Plotly.react('plot',traces,{margin:{t:10,r:10},xaxis:{title:META.x_label},
      yaxis:{title:META.value_label},legend:{orientation:'h'}},{responsive:true});
  document.getElementById('status').textContent =
     r.n_total.toLocaleString()+' pts in view -> '+r.n_returned.toLocaleString()+
     ' drawn ('+Math.round(performance.now()-t0)+' ms)';
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
    return jsonify(DS.scatter(flo, fhi, sites, maxpts))


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
    args = ap.parse_args(argv)

    global DS
    target = Path(args.target).resolve() if args.target else _default_target()
    pqpath = _find_parquet(target)
    print(f"Loading {pqpath} ...", flush=True)
    DS = DataSet(pqpath, x_override=args.x, value_override=args.value)
    m = DS.meta()
    print(f"  {m['rows']:,} rows | x={m['x_label']} [{m['x_min']:.4g}..{m['x_max']:.4g}] "
          f"| value={m['value_label']} | sites={m['sites']}", flush=True)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Serving {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
