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
import math
import os
import re
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pandas as pd
from flask import Flask, Response, jsonify, request

import padb_bands
import padb_brand
import padb_limits
from padb_core import Dataset as _CoreDataset, FilterSpec, FilterConfig, lockio
from padb_core import filterconfig as _filterconfig
from padb_core.columns import detect_columns as _detect_columns, qi as _qi
from padb_core.conditions import MAX_DIM_VALUES, parse_group as _parse_group_dims

try:
    import plotly.offline as _plo
    _PLOTLY = _plo.get_plotlyjs()
except Exception:  # pragma: no cover
    _PLOTLY = "console.error('plotly.js not available');"

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
    """Read a named-band JSON and convert each band's lo/hi into the data's x-axis
    unit. Delegates to padb_bands.load_bands_file (single source of truth shared
    with the HTML views); a bad/missing file yields no bands."""
    out = padb_bands.load_bands_file(path, data_unit)
    if out:
        print(f"  [bands] loaded {len(out)} band(s) from {Path(path).name} "
              f"(data unit {data_unit or '?'})", flush=True)
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


def _spec_segments_from(rows, x_max):
    """Contiguous x-bands of constant (yhi, ylo). `rows` yields objects/tuples with
    .x/.yhi/.ylo (None or NaN = no limit), ordered by x. Shared by both engines."""
    def _k(v):
        return round(float(v), 4) if pd.notna(v) else None
    def _v(v):
        return float(v) if pd.notna(v) else None
    segs, cur_lo, cur_key, cur_val, prev_x = [], None, None, None, None
    for row in rows:
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
        segs[-1]["hi"] = x_max
    return segs


def _limits_list(ds):
    """The dataset's limit overrides as a plain list (the pandas engine keeps a list, the core
    engine a LimitTable)."""
    lim = getattr(ds, "limits", None)
    if lim is None:
        return []
    return lim.to_list() if hasattr(lim, "to_list") else list(lim)


class PandasDataSet:
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
        df["yhi0"] = df["yhi"]
        df["ylo0"] = df["ylo"]
        self.limits: list = []
        self.df = df[["x", "y", "site", "serial", "temp", "yhi", "ylo", "yhi0", "ylo0", "scored", "fail"]] \
            .sort_values("x", kind="stable").reset_index(drop=True)
        self.x_min = float(self.df["x"].min())
        self.x_max = float(self.df["x"].max())
        self.sites = list(map(str, self.df["site"].cat.categories))
        self.temps = list(map(str, self.df["temp"].cat.categories))
        self.is_room_only = len(self.temps) < 2
        self.n_rows = int(len(self.df))
        self.n_serials = int(self.df["serial"].cat.categories.size)
        self.engine = "pandas"
        self.value_label = self.value_col
        self.x_label = self.x_col
        m = re.search(r"\(([^)]+)\)", self.x_label)
        self.x_unit = m.group(1) if m else ""
        self.bands: list[dict] = []  # filled by _load_bands() in main()
        self.band_counts: list[int] = []  # rows per band, filled by compute_band_counts()
        self.max_band_rows: int = 0       # largest single band's row count

    def distinct_x(self):
        return self.df["x"].unique().tolist()

    def set_limits(self, limits):
        """Apply (or, with [], remove) limit overrides over the data's own limits. One
        semantic definition: padb_limits.effective. Recomputes scored / fail / has_limits."""
        hi, lo = padb_limits.effective(self.df["x"].to_numpy(), self.df["yhi0"].to_numpy(dtype=float),
                                       self.df["ylo0"].to_numpy(dtype=float), limits)
        d = self.df
        d["yhi"], d["ylo"] = hi, lo
        hi_ok, lo_ok = ~np.isnan(hi), ~np.isnan(lo)
        y = d["y"].to_numpy()
        d["scored"] = hi_ok | lo_ok
        d["fail"] = (hi_ok & (y > hi)) | (lo_ok & (y < lo))
        self.has_limits = bool(d["scored"].any())
        self.limits = list(limits)

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
            "rows": int(self.n_rows),
            "engine": self.engine,
            "x_label": self.x_label,
            "value_label": self.value_label,
            "x_min": self.x_min,
            "x_max": self.x_max,
            "sites": self.sites,
            "temps": self.temps,
            "n_serials": int(self.n_serials),
            "x_unit": self.x_unit,
            "bands": self.bands,
            "band_counts": self.band_counts,
            "max_band_rows": self.max_band_rows,
            "band_cap": _effective_band_cap(self),
            "slow_band_rows": SLOW_BAND_ROWS,
            "slow_band_warn": self.max_band_rows > SLOW_BAND_ROWS,
            "is_room_only": bool(self.is_room_only),
            "has_limits": bool(self.has_limits),
            "limits": _limits_list(self),
            "all_dims": self.all_dims() if hasattr(self, "all_dims") else [],
            "unavailable": (self.index.unavailable if getattr(self, "index", None) else []),
            "filters": (self.index.config.to_dict() if getattr(self, "index", None) else {}),
            "dims": getattr(self, "dims", []),
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
        return _spec_segments_from(per.itertuples(index=False), self.x_max)

    def scatter(self, flo, fhi, sites, maxpts, temps=None, pf="all", conds=None, by=None):
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


class DuckDataSet(_CoreDataset):
    """The viewer's DuckDB engine = padb_core.Dataset (v3 core: one condition parser, FilterSpec,
    limits as a query-time view) plus the few viewer-only parts: named-band bookkeeping, band slices
    of the RAW parquet for the padb_v2 band views, and the PandasDataSet-shaped scatter()/_where()
    wrappers. Everything else (build, filters, envelope decimation, spec stairs, limit overrides) is
    the core -- there is no second copy in this file."""

    def __init__(self, parquet_path: Path, x_override=None, value_override=None):
        try:
            super().__init__(parquet_path, x_override, value_override)
        except ValueError as exc:
            sys.exit(str(exc))
        self._src = "read_parquet('" + str(parquet_path).replace("\\", "/").replace("'", "''") + "')"
        self.engine = "duckdb"
        self.bands: list[dict] = []
        self.band_counts: list[int] = []
        self.max_band_rows: int = 0

    meta = PandasDataSet.meta

    def compute_band_counts(self):
        self.band_counts = self.count_in_bands(self.bands)
        for b, n in zip(self.bands, self.band_counts):
            b["count"] = n
        self.max_band_rows = max(self.band_counts) if self.band_counts else 0

    # -- PandasDataSet-shaped wrappers over the core's FilterSpec ---------------------------------
    @staticmethod
    def _as_spec(flo, fhi, sites, temps, pf, conds):
        return FilterSpec(lo=flo, hi=fhi, sites=list(sites or []), temps=list(temps or []), pf=pf or "all",
                          conds={int(k): [str(x) for x in v] for k, v in (conds or {}).items()})

    def _where(self, flo, fhi, sites, temps, pf, conds):
        return self._as_spec(flo, fhi, sites, temps, pf, conds).to_sql(self.dim_ids())

    def scatter(self, flo, fhi, sites, maxpts, temps=None, pf="all", conds=None, by=None):
        return self.query(self._as_spec(flo, fhi, sites, temps, pf, conds), maxpts, by)

    # -- band slices of the RAW parquet (for the padb_v2 band views) ------------------------------
    MAX_GROUPS = 60

    def _band_pred(self):
        return f"TRY_CAST({_qi(self.x_col)} AS DOUBLE) BETWEEN ? AND ?"

    def _filt_where(self, flo, fhi, filt):
        """filt = a FilterSpec (window ignored) or a legacy dict {sites,temps,pf,conds}."""
        if isinstance(filt, FilterSpec):
            import dataclasses
            return dataclasses.replace(filt, lo=flo, hi=fhi).to_sql(self.dim_ids())
        return self._where(flo, fhi, filt.get("sites"), filt.get("temps"),
                           filt.get("pf", "all"), filt.get("conds"))

    def band_row_count(self, flo, fhi, filt=None):
        if filt:
            w, args = self._filt_where(flo, fhi, filt)
            return int(self._all(f"SELECT count(*) FROM pts WHERE {w}", args)[0][0])
        return int(self._all(f"SELECT count(*) FROM {self._src} WHERE {self._band_pred()}",
                             [flo, fhi])[0][0])

    def band_table(self, flo, fhi, filt=None):
        """Raw rows (all original columns) with x in [flo,fhi], as a pyarrow Table.
        With a filter, rows are chosen by source row id from the SAME predicate the
        overview plot uses, so a band view shows exactly what the plot shows."""
        if filt:
            w, qargs = self._filt_where(flo, fhi, filt)
            pth = str(self.path).replace(chr(92), "/").replace("'", "''")
            q = (f"SELECT r.* EXCLUDE (file_row_number) FROM read_parquet('{pth}', "
                 f"file_row_number=true) r WHERE r.file_row_number IN "
                 f"(SELECT rid FROM pts WHERE {w})")
        else:
            q = f"SELECT * FROM {self._src} WHERE {self._band_pred()}"
            qargs = [flo, fhi]
        cur = self.con.cursor()
        try:
            rel = cur.sql(q, params=qargs)           # relation API: no extra row copy
            t = rel.to_arrow_table()
        except Exception:
            cur = cur.execute(q, qargs)
            for name in ("to_arrow_table", "fetch_arrow_table", "arrow"):  # API differs by version
                fn = getattr(cur, name, None)
                if fn:
                    t = fn()
                    break
            else:
                raise RuntimeError("this duckdb version has no arrow export")
        return t.read_all() if hasattr(t, "read_all") else t


def DataSet(parquet_path, x_override=None, value_override=None, engine=None):
    """Factory: DuckDB engine when available (default), else/or the pandas engine.
    Override with engine='pandas'|'duckdb' or env PADB_VIEWER_ENGINE."""
    engine = (engine or os.environ.get("PADB_VIEWER_ENGINE") or "duckdb").lower()
    if engine == "duckdb":
        try:
            import duckdb  # noqa: F401
        except Exception:
            print("  [engine] duckdb not installed -- falling back to pandas "
                  "(py -m pip install duckdb)", flush=True)
            engine = "pandas"
    if engine == "pandas":
        return PandasDataSet(parquet_path, x_override, value_override)
    return DuckDataSet(parquet_path, x_override, value_override)


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
 header{background:#fff;color:#222;padding:6px 14px;font-weight:600;display:flex;align-items:center;gap:14px;border-bottom:3px solid #e8002d}
 header .kslogo{height:28px;width:auto}
 header .apptitle{font-size:15px;letter-spacing:.02em;color:#333}
 #bar{padding:8px 14px;background:#fff;border-bottom:1px solid #ddd;display:flex;
      gap:16px;align-items:center;flex-wrap:wrap}
 #bar label{font-size:12px}
 input[type=number]{width:90px}
 #status{margin-left:auto;color:#666;font-size:12px}
 #plot{height:calc(100vh - 96px)}
 .sitebox{display:inline-flex;gap:8px;align-items:center}
 #condrow{display:none;padding:5px 14px;background:#fffdf7;border-bottom:1px solid #ddd;font-size:12px;align-items:center;flex-wrap:wrap;gap:6px}
 .dd-wrap{position:relative;display:inline-block}
 .dd-btn{font-size:13px;padding:3px 10px;border:1px solid #bbb;border-radius:3px;cursor:pointer;background:#fff;white-space:nowrap}
 .dd-btn:hover{background:#e8e8e8}
 .dd-btn.narrowed{border-color:#0066cc;background:#eaf3ff}
 .dd-panel{display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:200;background:#fff;border:1px solid #ccc;border-radius:4px;box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:160px;max-height:280px;overflow-y:auto;padding:6px 8px}
 .dd-panel.open{display:block}
 .dd-item{display:block;padding:2px 0;cursor:pointer;white-space:nowrap;font-size:13px}
 .dd-div{margin:4px 0;border:none;border-top:1px solid #eee}
 .dd-badge{font-size:11px;background:#0066cc;color:#fff;border-radius:10px;padding:1px 6px;margin-right:2px;display:none}
 .dd-badge.active{display:inline}
</style></head><body>
<header><img class="kslogo" alt="Keysight" src="__LOGO__"><span class="apptitle">PADB Viewer</span> <span id="ttl" style="font-weight:400;color:#555;font-size:13px"></span></header>
<div id="bar">
  <span><b id="xlbl">x</b> min <input type="number" id="flo" step="any"></span>
  <span>max <input type="number" id="fhi" step="any"></span>
  <span>max points <input type="number" id="maxpts" value="4000" step="500"></span>
  <span class="sitebox" id="sites"></span>
  <span class="sitebox" id="temps"></span>
  <label id="limbox" style="display:none" title="Draw the spec/limit line(s) from the data's Upper/Lower Limit columns (a step where the limit changes along x). Display only -- the limit shown is the first one in file order at each x."><input type="checkbox" id="showlim" onchange="update()"> Show limits</label>
  <button id="limbtn" onclick="toggleLimEdit()" title="Override the data's Upper/Lower Limit over x-ranges. Changes the limit line, Passing/Failing and every band view; never the parquet. Saved to a limits file beside the data.">Edit limits</button>
  <span id="limflag" style="display:none;color:#b26a00;font-weight:600" title="The limits shown and used for pass/fail come from your override file, not (only) from the data."></span>
  <button id="grpbtn" onclick="toggleGrp()"
    title="Colour/split the overview points by Site, Serial Number, Temperature or any condition. Display only -- does not filter.">Group by &#9662;</button>
  <button id="fltbtn" onclick="toggleFltEdit()" style="display:none" title="Choose which conditions are offered as filters, in what order and under what names -- including ones the automatic rules hide (single-valued, serial-like, many values). A condition that is not offered puts no restriction on the data.">Edit filters</button>
  <button id="condbtn" onclick="toggleConds()" style="display:none"
    title="Filter by condition (every Key: value in the Group text). Unticking a value hides those points in the plot AND in the band views.">Conditions &#9662;</button>
  <span class="sitebox" id="pfbox" title="Filter the overview points by pass/fail against each point's own limit. Failing = crosses a present limit; Passing = everything else (pass + no-limit). Needs a limit column in the data.">
    Show:
    <label><input type="radio" name="pf" value="all" checked onchange="update()">All</label>
    <label><input type="radio" name="pf" value="pass" onchange="update()">Passing</label>
    <label><input type="radio" name="pf" value="fail" onchange="update()">Failing</label></span>
  <button onclick="update()">Update</button>
  <button onclick="resetView()" title="Return to the full x-range (filters kept)">Reset</button>
  <button onclick="exportFilter()" title="Save the current filter (conditions, sites, temperatures, window, pass/fail) as a file the HTML pages can import (their lock bar -> Import), or another viewer.">Export filter</button>
  <button onclick="document.getElementById('filein').click()" title="Apply a filter file exported from an HTML page's lock bar or from another viewer. Conditions or values that do not exist in this dataset are skipped (and listed).">Import filter</button>
  <input type="file" id="filein" accept=".json,application/json" style="display:none" onchange="importFilter(this.files[0]);this.value=''">
  <button onclick="resetAll()" title="Clear every saved filter/window/group-by choice for this dataset and reload the defaults">Reset all</button>
  <span id="status"></span>
</div>
<div id="grppanel" style="display:none;padding:6px 14px;background:#f2fff2;border-bottom:1px solid #ddd;font-size:12px"></div>
<div id="limedit" style="display:none;padding:6px 14px;background:#fff6f0;border-bottom:1px solid #ddd;font-size:12px"></div>
<div id="fltedit" style="display:none;padding:6px 14px;background:#f4f8ff;border-bottom:1px solid #ddd;font-size:12px"></div>
<div id="condrow"></div>
<div id="condpanel" style="display:none;padding:6px 14px;background:#fffaf0;border-bottom:1px solid #ddd;font-size:12px"></div>
<div id="segbar" style="padding:6px 14px;background:#f6f6ff;border-bottom:1px solid #ddd;font-size:12px">
  <b title="Step the frequency window through segments to minimize the data shown -- by named band, or by each spec/limit 'stair' (contiguous frequencies with a constant limit).">Segment step:</b>
  <select id="segbasis" onchange="_segLoad(true)">
    <option value="spec">Spec/limit stairs</option>
    <option value="band">Named bands</option>
  </select>
  <button onclick="_segStep(-1)">&#9664;&nbsp;Prev</button>
  <button onclick="_segStep(1)">Next&nbsp;&#9654;</button>
  <button onclick="resetView()" title="Return to the full x-range">Full range</button>
  <button onclick="toggleBandEdit()" title="Add / rename / re-range the named bands live. Saved to the band file shared with the HTML builds.">Edit bands</button>
  <span id="seginfo" style="color:#888"></span>
</div>
<div id="bandedit" style="display:none;padding:6px 14px;background:#f6fff6;border-bottom:1px solid #ddd;font-size:12px"></div>
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
      '<label><input type="checkbox" checked value="'+s+'" class="sitechk" onchange="update()"> '+s+'</label>');
  });
  // Temperature filter (only meaningful with >1 temp) -- read from the real Test Step
  // column in the parquet, so it's reliable.
  if(META.temps && META.temps.length>1){
    const tb=document.getElementById('temps');
    tb.insertAdjacentHTML('beforeend','<b style="margin-left:8px">Temp:</b> ');
    META.temps.forEach(function(tp){
      tb.insertAdjacentHTML('beforeend',
        '<label><input type="checkbox" checked value="'+tp+'" class="tempchk" onchange="update()"> '+tp+'</label>');
    });
  }
  buildConds(); buildGrp();
  if((META.all_dims||[]).length){ document.getElementById('fltbtn').style.display=''; }
  if(META.has_limits){ document.getElementById('limbox').style.display=''; }
  await loadState();   // restore the last filters/window/group-by for THIS dataset (Reset all clears it)
  // Band-view buttons render the full interactive view for the current x-range.
  // scatter/boxplot/stat_summary/summary always; env_coverage/distribution only for
  // multi-temp data (they need a Room baseline + non-Room) -- matches the html build's
  // auto view-selection, which omits them for Room-only.
  var vbtns=[['scatter','Scatter'],['boxplot','Boxplot'],['stat_summary','Stat Summary'],['summary','Summary']];
  if(META.is_room_only===false){ vbtns.push(['env_coverage','Env Coverage'],['distribution','Distribution']); }
  var vb=document.getElementById('viewbtns');
  vbtns.forEach(function(v){ var b=document.createElement('button'); b.textContent=v[1];
    b.style.marginRight='6px'; b.onclick=function(){openView(v[0]);}; vb.appendChild(b); });
  buildBands();
  refreshLimitUI();
  update();
}
// --- State persistence (browser localStorage, per dataset). Saves what the user narrowed
//     (window, sites/temps/conditions as the UNTICKED values so new values default on, group-by,
//     pass/fail, limits, segment basis+index) so a reload / revisit shows the same slice.
//     "Reset all" clears it. Every access is try/catch: private windows / blocked storage must
//     never break the page. ----------------------------------------------------------------
function _stKey(){ return 'padb_viewer_state:'+(META.parquet||'')+':'+META.rows; }
function saveState(){
  try{
    var unc=function(sel){ return [...document.querySelectorAll(sel)].filter(function(c){return !c.checked;}).map(function(c){return c.value;}); };
    var conds={};
    document.querySelectorAll('.condchk').forEach(function(c){
      if(!c.checked){ var k=c.getAttribute('data-dim'); (conds[k]=conds[k]||[]).push(c.value); } });
    var o={flo:document.getElementById('flo').value, fhi:document.getElementById('fhi').value,
      maxpts:document.getElementById('maxpts').value,
      sites_off:unc('.sitechk'), temps_off:unc('.tempchk'), conds_off:conds,
      by:selectedBy(), lim:document.getElementById('showlim').checked,
      pf:(document.querySelector('input[name="pf"]:checked')||{}).value||'all',
      segbasis:document.getElementById('segbasis').value, segIdx:_segIdx,
      full:document.getElementById('fullrender').checked};
    localStorage.setItem(_stKey(), JSON.stringify(o));
  }catch(e){}
}
async function loadState(){
  try{
    var o=JSON.parse(localStorage.getItem(_stKey())||'null'); if(!o) return;
    var has=function(a,v){ return (a||[]).indexOf(v)>=0; };
    if(o.flo!==''&&o.flo!=null) document.getElementById('flo').value=o.flo;
    if(o.fhi!==''&&o.fhi!=null) document.getElementById('fhi').value=o.fhi;
    if(o.maxpts) document.getElementById('maxpts').value=o.maxpts;
    document.querySelectorAll('.sitechk').forEach(function(c){ c.checked=!has(o.sites_off,c.value); });
    document.querySelectorAll('.tempchk').forEach(function(c){ c.checked=!has(o.temps_off,c.value); });
    document.querySelectorAll('.condchk').forEach(function(c){
      c.checked=!has((o.conds_off||{})[c.getAttribute('data-dim')], c.value); });
    syncCondRow();
    if(o.by){ document.querySelectorAll('.grpchk').forEach(function(c){ c.checked=has(o.by,c.value); }); }
    if(META.has_limits){ document.getElementById('showlim').checked=!!o.lim;
      var r=document.querySelector('input[name="pf"][value="'+o.pf+'"]'); if(r&&!r.disabled) r.checked=true; }
    document.getElementById('fullrender').checked=!!o.full;
    var sb=document.getElementById('segbasis'), opt=sb.querySelector('option[value="'+o.segbasis+'"]');
    if(opt&&!opt.disabled){ sb.value=o.segbasis; }
    await _segLoad(true);
    if(o.segIdx>=0 && o.segIdx<_segs.length){
      _segIdx=o.segIdx; var s=_segs[_segIdx];
      document.getElementById('seginfo').textContent='segment '+(_segIdx+1)+'/'+_segs.length+
        (s.name?(' ('+s.name+')'):'')+': '+(+s.lo).toPrecision(5)+' .. '+(+s.hi).toPrecision(5)+' '+(META.x_unit||'')+_segSpecStr(s);
    }
  }catch(e){}
}
function resetAll(){ try{ localStorage.removeItem(_stKey()); }catch(e){} location.reload(); }
function buildBands(){
  const bb=document.getElementById('bands'); bb.innerHTML='';
  const bar=document.getElementById('bandbar');
  if(!(META.bands && META.bands.length)){ bar.style.display='none'; return; }
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
  bar.style.display='block';
}
// --- Live band editor: edit name / lo / hi, Apply saves the shared band file and everything
//     (buttons, counts, the Named-bands stepper) follows without a restart. -----------------
function toggleBandEdit(){
  var p=document.getElementById('bandedit');
  if(p.style.display==='none'||!p.style.display){ renderBandEdit(META.bands||[]); p.style.display='block'; }
  else p.style.display='none';
}
function _beRow(b){
  var nm=String(b.name==null?'':b.name).replace(/"/g,'&quot;');
  return '<tr class="berow"><td><input class="be_name" type="text" value="'+nm+'" style="width:190px"></td>'+
    '<td><input class="be_lo" type="number" step="any" value="'+(b.lo==null?'':b.lo)+'"></td>'+
    '<td><input class="be_hi" type="number" step="any" value="'+(b.hi==null?'':b.hi)+'"></td>'+
    '<td><a href="#" onclick="this.closest(\'tr\').remove();return false" title="Remove this band">delete</a></td></tr>';
}
function renderBandEdit(bands){
  var p=document.getElementById('bandedit');
  p.innerHTML='<b>Named bands</b> <span style="color:#666">(x in '+(META.x_unit||'data units')+'; edges are inclusive. '+
    'Apply saves <code>'+'the band file'+'</code> used by the HTML builds too)</span>'+
    '<table id="betbl" style="margin:4px 0"><tr><th align="left">Name</th><th align="left">'+(META.x_label||'x')+' min</th><th align="left">max</th><th></th></tr>'+
    bands.map(_beRow).join('')+'</table>'+
    '<button onclick="beAdd()">Add band</button> <button onclick="beApply()" style="font-weight:600">Apply</button> '+
    '<button onclick="renderBandEdit(META.bands||[])">Revert</button> <button onclick="toggleBandEdit()">Close</button> '+
    '<span id="bemsg" style="margin-left:8px"></span>';
}
function beAdd(){
  var t=document.getElementById('betbl'); t.insertAdjacentHTML('beforeend',_beRow({name:'New band',lo:META.x_min,hi:META.x_max}));
}
function beRead(){
  return [...document.querySelectorAll('#betbl .berow')].map(function(r){
    return {name:r.querySelector('.be_name').value, lo:r.querySelector('.be_lo').value, hi:r.querySelector('.be_hi').value}; });
}
async function beApply(){
  var msg=document.getElementById('bemsg'); msg.style.color='#666'; msg.textContent='saving...';
  var r, j;
  try{
    r=await fetch('/api/bands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bands:beRead()})});
    j=await r.json();
  }catch(e){ msg.style.color='#c00'; msg.textContent='could not reach the server'; return; }
  if(!j.ok){ msg.style.color='#c00'; msg.textContent=j.error||'rejected'; return; }
  ['bands','band_counts','max_band_rows','band_cap','slow_band_warn'].forEach(function(k){ META[k]=j[k]; });
  buildBands();
  _segIdx=-1; await _segLoad(true);
  renderBandEdit(META.bands);
  var m2=document.getElementById('bemsg'); m2.style.color='#060'; m2.textContent='saved '+META.bands.length+' band(s) -> '+j.path;
}
// --- Live limit-override editor. Rows = x-range + upper and/or lower limit. A blank side keeps
//     the DATA's own value for that side; rows outside every range keep the data's limits; where
//     ranges overlap or share an edge the LATER row wins (per side). Display + pass/fail + band
//     views follow on Apply; the parquet is never modified. -------------------------------------
function refreshLimitUI(){
  var has=!!META.has_limits, n=(META.limits||[]).length;
  document.getElementById('limbox').style.display=has?'':'none';
  document.querySelectorAll('input[name="pf"]').forEach(function(r){ r.disabled=!has&&r.value!=='all'; });
  var pfb=document.getElementById('pfbox'); if(pfb){ pfb.style.opacity=has?'1':'0.5'; }
  var so=document.getElementById('segbasis').querySelector('option[value="spec"]'); if(so) so.disabled=!has;
  var f=document.getElementById('limflag');
  f.style.display=n?'':'none'; f.textContent=n?('limit overrides ACTIVE ('+n+' range'+(n>1?'s':'')+')'):'';
}
function toggleLimEdit(){
  var p=document.getElementById('limedit');
  if(p.style.display==='none'||!p.style.display){ renderLimEdit(META.limits||[]); p.style.display='block'; }
  else p.style.display='none';
}
function _leRow(e){
  var v=function(x){ return x==null?'':x; };
  return '<tr class="lerow"><td><input class="le_lo" type="number" step="any" value="'+v(e.lo)+'"></td>'+
    '<td><input class="le_hi" type="number" step="any" value="'+v(e.hi)+'"></td>'+
    '<td><input class="le_up" type="number" step="any" value="'+v(e.upper)+'" placeholder="(data)"></td>'+
    '<td><input class="le_lw" type="number" step="any" value="'+v(e.lower)+'" placeholder="(data)"></td>'+
    '<td><input class="le_note" type="text" value="'+String(e.note||'').replace(/"/g,'&quot;')+'" style="width:150px"></td>'+
    '<td><a href="#" onclick="this.closest(\'tr\').remove();return false" title="Remove this range">delete</a></td></tr>';
}
function renderLimEdit(list){
  var p=document.getElementById('limedit');
  p.innerHTML='<b>Limit overrides</b> <span style="color:#666">(x in '+(META.x_unit||'data units')+'; edges inclusive; '+
    'a blank limit keeps the data\'s own value on that side; later row wins where ranges overlap; '+
    'overrides the limit line, Passing/Failing and the band views &mdash; never the parquet)</span>'+
    '<table id="letbl" style="margin:4px 0"><tr><th align="left">'+(META.x_label||'x')+' min</th><th align="left">max</th>'+
    '<th align="left">Upper limit</th><th align="left">Lower limit</th><th align="left">Note</th><th></th></tr>'+
    list.map(_leRow).join('')+'</table>'+
    '<button onclick="leAdd()">Add range</button> <button onclick="leApply()" style="font-weight:600">Apply</button> '+
    '<button onclick="renderLimEdit(META.limits||[])">Revert</button> '+
    '<button onclick="leClear()" title="Remove every override: back to the data\'s own limits">Clear all</button> '+
    '<button onclick="toggleLimEdit()">Close</button> <span id="lemsg" style="margin-left:8px"></span>';
}
function leAdd(){ document.getElementById('letbl').insertAdjacentHTML('beforeend',_leRow({lo:META.x_min,hi:META.x_max,upper:'',lower:''})); }
function leRead(){
  return [...document.querySelectorAll('#letbl .lerow')].map(function(r){
    return {lo:r.querySelector('.le_lo').value, hi:r.querySelector('.le_hi').value,
            upper:r.querySelector('.le_up').value, lower:r.querySelector('.le_lw').value,
            note:r.querySelector('.le_note').value}; });
}
async function _lePost(list){
  var msg=document.getElementById('lemsg'); msg.style.color='#666'; msg.textContent='saving...';
  var j;
  try{
    var r=await fetch('/api/limits',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({limits:list})});
    j=await r.json();
  }catch(e){ msg.style.color='#c00'; msg.textContent='could not reach the server'; return; }
  if(!j.ok){ msg.style.color='#c00'; msg.textContent=j.error||'rejected'; return; }
  META.limits=j.limits; META.has_limits=j.has_limits;
  _specCache=null; refreshLimitUI();
  if(document.getElementById('segbasis').value==='spec'){ _segIdx=-1; await _segLoad(true); }
  update();
  if(_curView && document.getElementById('viewwrap').style.display!=='none') openView(_curView);
  renderLimEdit(META.limits);
  var m2=document.getElementById('lemsg'); m2.style.color='#060';
  m2.textContent=(META.limits.length?('saved '+META.limits.length+' range(s)'):'overrides cleared')+' -> '+j.path;
}
function leApply(){ return _lePost(leRead()); }
function leClear(){ return _lePost([]); }
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
// --- Condition filters (every Key: value dimension in Group). Only NARROWED dims are sent. ---
function buildConds(){
  var dims=(META&&META.dims)||[];
  if(!dims.length){ var _p=document.getElementById('condpanel'), _r=document.getElementById('condrow');
    if(_p){ _p.innerHTML=''; _p.style.display='none'; } if(_r){ _r.innerHTML=''; _r.style.display='none'; }
    document.getElementById('condbtn').style.display='none'; return; }
  var p=document.getElementById('condpanel'), h='';
  dims.forEach(function(d){
    h+='<div style="margin:3px 0"><b>'+(d.label||d.name)+':</b> ';
    d.values.forEach(function(v){
      h+='<label style="margin-right:8px"><input type="checkbox" checked class="condchk" data-dim="'+d.id+
         '" value="'+String(v).replace(/"/g,'&quot;')+'" onchange="syncCondRow();update()"> '+v+'</label>';
    });
    h+=' <a href="#" onclick="condAll('+d.id+',true);return false">all</a> / <a href="#" onclick="condAll('+d.id+',false);return false">none</a></div>';
  });
  h+='<div style="margin-top:4px"><a href="#" onclick="condReset();return false">Reset condition filters</a></div>';
  p.innerHTML=h;
  document.getElementById('condbtn').style.display='';
  buildCondRow();
}
function buildGrp(){
  var h='<b>Group by:</b> ', opts=[['site','Site'],['serial','Serial Number'],['temp','Temperature']];
  ((META&&META.dims)||[]).forEach(function(d){ opts.push(['d'+d.id,d.label||d.name]); });
  opts.forEach(function(o){
    h+='<label style="margin-right:10px"><input type="checkbox" class="grpchk" value="'+o[0]+'"'+(o[0]==='site'?' checked':'')+
       ' onchange="update()"> '+o[1]+'</label>'; });
  h+=' <span style="color:#888">(none checked = one colour; more than '+60+' groups is refused -- pick fewer keys)</span>';
  document.getElementById('grppanel').innerHTML=h;
}
// --- Condition filters as a ROW of dropdowns (same look/behaviour as the HTML views' filter
//     buttons: a button per dimension, a tick panel with "Select all", a n/total badge when
//     narrowed). The full checkbox list (Conditions button) stays; BOTH are views of the same
//     .condchk state, so selectedConds()/saveState()/the server query never see two truths. ----
function buildCondRow(){
  var dims=(META&&META.dims)||[], row=document.getElementById('condrow'); if(!dims.length||!row) return;
  var h='<b style="margin-right:4px" title="Filter by condition. Same state as the full list under the Conditions button.">Conditions:</b>';
  dims.forEach(function(d){
    h+='<span class="dd-wrap"><button class="dd-btn" id="ddbtn_'+d.id+'" onclick="ddToggle('+d.id+',event)">'+
       String(d.label||d.name).replace(/</g,'&lt;')+' <span class="dd-badge" id="ddb_'+d.id+'"></span>&#9662;</button>'+
       '<div class="dd-panel" id="ddp_'+d.id+'" onclick="event.stopPropagation()">'+
       (d.values.length>12?'<input type="search" class="dd-search" placeholder="search '+d.values.length+' values..." '+
         'oninput="ddSearch('+d.id+',this.value)" style="width:100%;box-sizing:border-box;margin-bottom:4px">':'')+
       '<label class="dd-item"><input type="checkbox" class="ddall" data-dim="'+d.id+'" checked onchange="ddAll('+d.id+',this.checked)"><b>Select&nbsp;all</b></label><hr class="dd-div">';
    d.values.forEach(function(v){
      h+='<label class="dd-item"><input type="checkbox" class="ddchk" data-dim="'+d.id+'" value="'+String(v).replace(/"/g,'&quot;')+
         '" checked onchange="ddChanged('+d.id+')">'+String(v).replace(/</g,'&lt;')+'</label>'; });
    h+='</div></span>';
  });
  h+=' <a href="#" onclick="condReset();return false" title="Tick every value of every condition">reset</a>';
  row.innerHTML=h; row.style.display='flex';
  if(!window.__ddClickBound){ window.__ddClickBound=true;
    document.addEventListener('click',function(){ document.querySelectorAll('.dd-panel.open').forEach(function(p){p.classList.remove('open');}); }); }
  syncCondRow();
}
function ddSearch(id,q){
  q=String(q||'').toLowerCase();
  document.querySelectorAll('#ddp_'+id+' .dd-item').forEach(function(l){
    if(l.querySelector('.ddall')) return;
    l.style.display=(!q||l.textContent.toLowerCase().indexOf(q)>=0)?'':'none'; });
}
function ddToggle(id,ev){
  if(ev) ev.stopPropagation();
  var p=document.getElementById('ddp_'+id); if(!p) return;
  document.querySelectorAll('.dd-panel.open').forEach(function(el){ if(el!==p) el.classList.remove('open'); });
  p.classList.toggle('open');
}
function _condBox(id,val){
  var a=document.querySelectorAll('.condchk[data-dim="'+id+'"]');
  for(var i=0;i<a.length;i++) if(a[i].value===val) return a[i];
  return null;
}
function ddChanged(id){
  document.querySelectorAll('.ddchk[data-dim="'+id+'"]').forEach(function(c){ var t=_condBox(id,c.value); if(t) t.checked=c.checked; });
  syncCondRow(); update();
}
function ddAll(id,on){
  document.querySelectorAll('.condchk[data-dim="'+id+'"]').forEach(function(c){c.checked=on;});
  syncCondRow(); update();
}
// .condchk (the state) -> the dropdown row: tick boxes, n/total badge, highlight when narrowed.
function syncCondRow(){
  var dims=(META&&META.dims)||[];
  dims.forEach(function(d){
    var tot=0, on=0;
    document.querySelectorAll('.ddchk[data-dim="'+d.id+'"]').forEach(function(c){
      var t=_condBox(d.id,c.value); var v=t?t.checked:true; c.checked=v; tot++; if(v) on++; });
    var all=document.querySelector('.ddall[data-dim="'+d.id+'"]'); if(all) all.checked=(on===tot);
    var b=document.getElementById('ddb_'+d.id), btn=document.getElementById('ddbtn_'+d.id);
    if(b){ if(on<tot){ b.textContent=on+'/'+tot; b.classList.add('active'); } else { b.textContent=''; b.classList.remove('active'); } }
    if(btn) btn.classList.toggle('narrowed', on<tot);
  });
}
// --- Export / import the filter as the shared lock file (mapping done server-side, once) -------
async function exportFilter(){
  var flo=parseFloat(document.getElementById('flo').value), fhi=parseFloat(document.getElementById('fhi').value);
  var r=await (await fetch('/api/filter/export',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({spec:currentSpec(isFinite(flo)?flo:META.x_min,isFinite(fhi)?fhi:META.x_max)})})).json();
  if(!r.ok){ document.getElementById('status').textContent='export failed: '+(r.error||''); return; }
  var blob=new Blob([JSON.stringify(r.lock,null,2)],{type:'application/json'});
  var a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='padb_filter.json';
  document.body.appendChild(a); a.click(); setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); },500);
  window.__lastExportedLock=r.lock;
}
function applySpecToUI(sp){
  var has=function(a,v){ return !a||!a.length||a.indexOf(v)>=0; };
  document.getElementById('flo').value=(sp.lo!=null)?sp.lo:META.x_min;
  document.getElementById('fhi').value=(sp.hi!=null)?sp.hi:META.x_max;
  document.querySelectorAll('.sitechk').forEach(function(c){ c.checked=has(sp.sites,c.value); });
  document.querySelectorAll('.tempchk').forEach(function(c){ c.checked=has(sp.temps,c.value); });
  var pf=document.querySelector('input[name="pf"][value="'+(sp.pf||'all')+'"]'); if(pf&&!pf.disabled) pf.checked=true;
  document.querySelectorAll('.condchk').forEach(function(c){
    var k=c.getAttribute('data-dim'); var allowed=(sp.conds||{})[k]; c.checked=!allowed||allowed.indexOf(c.value)>=0; });
  syncCondRow(); update();
}
async function importFilter(file){
  if(!file) return;
  var st=document.getElementById('status'), txt=await file.text(), lock;
  try{ lock=JSON.parse(txt); }catch(e){ st.textContent='import failed: not a JSON file'; return; }
  var r=await (await fetch('/api/filter/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lock:lock})})).json();
  if(!r.ok){ st.textContent='import failed: '+(r.error||''); return; }
  applySpecToUI(r.spec);
  window.__lastImport={applied:r.applied,skipped:r.skipped};
  setTimeout(function(){
    var m='filter imported ('+r.applied.join(', ')+')';
    if(r.skipped.length) m+=' -- skipped: '+r.skipped.map(function(s){return s.label+' ('+s.reason+')';}).join('; ');
    st.textContent=m+'  |  '+st.textContent; },300);
}
// --- Edit filters: which conditions are exposed (padb_core.filterconfig). Rows = every filterable condition
//     (exposed ones first, in display order). Tick = exposed; the name you give is cosmetic (the KEY is the
//     identity saved filters use). A condition that is not exposed puts NO restriction on the data: hiding a
//     narrowed filter clears it. Apply is instant -- no reload; selections on conditions that stay exposed
//     are kept. ------------------------------------------------------------------------------------------
function toggleFltEdit(){
  var p=document.getElementById('fltedit');
  if(p.style.display==='none'||!p.style.display){ renderFltEdit(); p.style.display='block'; } else p.style.display='none';
}
function _fltRows(){
  var all=(META.all_dims||[]).slice(), pos={};
  (META.dims||[]).forEach(function(d,i){ pos[d.id]=i; });
  var exposed=all.filter(function(a){ return pos[a.id]!==undefined; }).sort(function(a,b){ return pos[a.id]-pos[b.id]; });
  var others=all.filter(function(a){ return pos[a.id]===undefined; });
  return exposed.concat(others);
}
function _fltRow(a){
  var tag=a.auto?'<span style="color:#060">automatic</span>':'<span style="color:#b26a00" title="The automatic rules do not offer this one">extra: '+String(a.reason).replace(/</g,'&lt;')+'</span>';
  return '<tr class="fltrow" data-name="'+String(a.name).replace(/"/g,'&quot;')+'"><td><input type="checkbox" class="flt_on"'+(a.exposed?' checked':'')+'></td>'+
    '<td>'+String(a.name).replace(/</g,'&lt;')+'</td><td>'+a.n_values+' value'+(a.n_values===1?'':'s')+'</td><td>'+tag+'</td>'+
    '<td><input type="text" class="flt_label" placeholder="(same)" value="'+String(a.label||'').replace(/"/g,'&quot;')+'" style="width:140px"></td>'+
    '<td><a href="#" onclick="fltMove(this,-1);return false" title="Move up">&#9650;</a> <a href="#" onclick="fltMove(this,1);return false" title="Move down">&#9660;</a></td></tr>';
}
function renderFltEdit(){
  var p=document.getElementById('fltedit'), un=(META.unavailable||[]);
  p.innerHTML='<b>Filters</b> <span style="color:#666">(tick = offered as a filter; a condition that is not offered restricts nothing. '+
    'Site, temperature and the x-axis have their own controls'+(un.length?' -- not listed: '+un.map(function(u){return String(u.name).replace(/</g,'&lt;');}).join(', '):'')+')</span>'+
    '<table id="flttbl" style="margin:4px 0"><tr><th></th><th align="left">Condition</th><th align="left">Values</th><th align="left">Offered by default?</th><th align="left">Name shown</th><th></th></tr>'+
    _fltRows().map(_fltRow).join('')+'</table>'+
    '<button onclick="fltApply()" style="font-weight:600">Apply</button> <button onclick="renderFltEdit()">Revert</button> '+
    '<button onclick="fltReset()" title="Back to the automatic set, no renames">Reset to automatic</button> '+
    '<button onclick="toggleFltEdit()">Close</button> <span id="fltmsg" style="margin-left:8px"></span>';
}
function fltMove(a,d){
  var tr=a.closest('tr'), sib=(d<0)?tr.previousElementSibling:tr.nextElementSibling;
  if(!sib||!sib.classList.contains('fltrow')) return;
  if(d<0) tr.parentNode.insertBefore(tr,sib); else tr.parentNode.insertBefore(sib,tr);
}
function fltRead(){
  var show=[], labels={};
  document.querySelectorAll('#flttbl .fltrow').forEach(function(r){
    var name=r.getAttribute('data-name');
    if(r.querySelector('.flt_on').checked) show.push(name);
    var lab=r.querySelector('.flt_label').value.trim(); if(lab) labels[name]=lab; });
  return {show:show, labels:labels};
}
async function _fltPost(cfg){
  var msg=document.getElementById('fltmsg'); msg.style.color='#666'; msg.textContent='applying...';
  var prev=selectedConds(), prevBy=selectedBy(), j;
  try{
    var r=await fetch('/api/filters',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:cfg})});
    j=await r.json();
  }catch(e){ msg.style.color='#c00'; msg.textContent='could not reach the server'; return; }
  if(!j.ok){ msg.style.color='#c00'; msg.textContent=j.error||'rejected'; return; }
  META.dims=j.dims; META.all_dims=j.all_dims; META.filters=j.config; META.unavailable=j.unavailable;
  buildConds(); buildGrp();
  var kept=0, cleared=[];
  Object.keys(prev).forEach(function(k){
    if(!(META.dims||[]).some(function(d){ return String(d.id)===String(k); })){ cleared.push(k); return; }
    document.querySelectorAll('.condchk[data-dim="'+k+'"]').forEach(function(c){ c.checked=prev[k].indexOf(c.value)>=0; }); kept++; });
  var okBy=prevBy.filter(function(v){ return v==='site'||v==='serial'||v==='temp'||(META.dims||[]).some(function(d){return 'd'+d.id===v;}); });
  document.querySelectorAll('.grpchk').forEach(function(c){ c.checked=okBy.indexOf(c.value)>=0; });
  syncCondRow(); update();
  renderFltEdit();
  var m2=document.getElementById('fltmsg'); m2.style.color='#060';
  var nm={}; (j.all_dims||[]).forEach(function(a){ nm[a.id]=a.name; });
  m2.textContent=(META.dims||[]).length+' filter(s) offered'+(cleared.length?' -- cleared the selection on hidden: '+cleared.map(function(k){return nm[k]||k;}).join(', '):'')+
    (j.unknown&&j.unknown.length?' -- not in this data: '+j.unknown.join(', '):'')+'  ->  '+j.path;
}
function fltApply(){ return _fltPost(fltRead()); }
function fltReset(){ return _fltPost({}); }
function toggleGrp(){ var p=document.getElementById('grppanel'); p.style.display=(p.style.display==='none')?'block':'none'; }
function selectedBy(){ return [...document.querySelectorAll('.grpchk:checked')].map(function(c){return c.value;}); }
function toggleConds(){ var p=document.getElementById('condpanel'); p.style.display=(p.style.display==='none')?'block':'none'; }
function condAll(id,on){ document.querySelectorAll('.condchk[data-dim="'+id+'"]').forEach(function(c){c.checked=on;}); syncCondRow(); update(); }
function condReset(){ document.querySelectorAll('.condchk').forEach(function(c){c.checked=true;}); syncCondRow(); update(); }
function selectedConds(){
  var by={}, tot={};
  document.querySelectorAll('.condchk').forEach(function(c){
    var k=c.getAttribute('data-dim'); tot[k]=(tot[k]||0)+1; if(!by[k]) by[k]=[]; if(c.checked) by[k].push(c.value); });
  var out={}; Object.keys(by).forEach(function(k){ if(by[k].length<tot[k]) out[k]=by[k]; });
  return out;
}
// ONE filter state: the same FilterSpec JSON the server turns into SQL (padb_core.filterspec).
function currentSpec(flo,fhi){
  var o={lo:flo,hi:fhi}, s=selectedSites(), tp=selectedTemps();
  if(s.length) o.sites=s;
  if(tp.length) o.temps=tp;
  var pf=(document.querySelector('input[name="pf"]:checked')||{}).value||'all'; if(pf!=='all') o.pf=pf;
  var c=selectedConds(); if(Object.keys(c).length) o.conds=c;
  return o;
}
function specQS(flo,fhi){ return '&spec='+encodeURIComponent(JSON.stringify(currentSpec(+flo,+fhi))); }
function selectedSites(){return [...document.querySelectorAll('.sitechk:checked')].map(c=>c.value);}
function selectedTemps(){return [...document.querySelectorAll('.tempchk:checked')].map(c=>c.value);}
function resetView(){document.getElementById('flo').value=META.x_min;document.getElementById('fhi').value=META.x_max;update();}
// The main plot is server-side decimated for the current [flo,fhi] band, so a
// drag-zoom / Autoscale / Reset-axes must drive the frequency filter (and re-query)
// rather than just re-viewport the already-drawn points. Mirrors the html views'
// _onPlotRelayout. _syncing swallows the react-induced relayout so it can't loop.
let _specCache=null, _updSeq=0, _relayoutHooked=false, _syncing=false, _curView=null, _vRefreshTimer=null;
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
  f.src='/view?view='+encodeURIComponent(v)+'&full='+full+specQS(flo,fhi);
  wrap.scrollIntoView({behavior:'smooth'});
}
async function update(){
  const floN=parseFloat(document.getElementById('flo').value),
        fhiN=parseFloat(document.getElementById('fhi').value);
  const flo=isFinite(floN)?floN:META.x_min, fhi=isFinite(fhiN)?fhiN:META.x_max;
  const maxpts=document.getElementById('maxpts').value;
  const sites=selectedSites().join(',');
  const temps=selectedTemps().join('|');
  document.getElementById('status').textContent='querying...';
  saveState();
  const t0=performance.now();
  const seq=++_updSeq;
  const r=await (await fetch(`/api/scatter?maxpts=${maxpts}&by=${encodeURIComponent(selectedBy().join(','))}`+specQS(flo,fhi))).json();
  if(seq!==_updSeq) return;   // a newer update() (e.g. a fast Next click) owns the plot now
  if(r.too_many){
    document.getElementById('status').textContent='Group by gives '+r.too_many+' groups (max 60) -- untick Serial Number or another high-cardinality key.';
    return;
  }
  const G=r.groups||{};
  const traces=r.traces.map(t=>({x:t.x,y:t.y,mode:'markers',type:'scattergl',
      name:t.site+' (n='+t.n.toLocaleString()+')',marker:{size:4,opacity:0.55},
      text:t.x.map(function(_,i){
        var gtxt=(t.gi&&t.gi[i]!=null&&G[t.gi[i]])?G[t.gi[i]]:'';
        return (t.sn&&t.sn[i]&&t.sn[i]!=='?'?('Serial: '+t.sn[i]+'<br>'):'')+
               (t.tp&&t.tp[i]?('Temp: '+t.tp[i]+'<br>'):'')+gtxt.split(' | ').join('<br>');}),
      hovertemplate:'%{text}<br><b>'+META.x_label+'</b>: %{x}<br><b>Value</b>: %{y}<extra>'+t.site+'</extra>'}));
  if(META.has_limits && document.getElementById('showlim').checked){
    if(!_specCache){ try{ _specCache=(await (await fetch('/api/segments')).json()).spec||[]; }catch(e){ _specCache=[]; } }
    ['hi','lo'].forEach(function(k){
      var xs=[],ys=[];
      _specCache.forEach(function(sg){ var v=sg[k+'_val']; if(v==null) return;
        xs.push(sg.lo,sg.hi); ys.push(v,v); });
      if(xs.length) traces.push({x:xs,y:ys,mode:'lines',type:'scatter',
        name:(k==='hi'?'Upper limit':'Lower limit'),line:{color:'#d00',width:1.6,dash:'dash'},
        hovertemplate:(k==='hi'?'Upper':'Lower')+' limit: %{y}<extra></extra>'});
    });
  }
  _syncing=true;
  // Pin x to the queried band so react() doesn't autorange-fire a spurious relayout
  // that _onRelayout would read as a Reset. Modebar Autoscale/Reset still emit
  // xaxis.autorange and are handled above.
  await Plotly.react('plot',traces,{margin:{t:10,r:10},
      xaxis:{title:{text:META.x_label},range:[flo,fhi]},
      yaxis:{title:{text:META.value_label}},legend:{orientation:'h'}},{responsive:true});
  if(!_relayoutHooked){ document.getElementById('plot').on('plotly_relayout',_onRelayout); _relayoutHooked=true; }
  // Keep the guard up until Plotly has settled: a late react-induced relayout (slow WebGL on
  // big pages) must not be read as a user Reset/zoom and overwrite the stepped window.
  setTimeout(()=>{ if(seq===_updSeq) _syncing=false; },150);
  document.getElementById('status').textContent =
     r.n_total.toLocaleString()+' pts in view -> '+r.n_returned.toLocaleString()+
     ' drawn ('+Math.round(performance.now()-t0)+' ms)'+(r.n_total===0?'  -- NO POINTS in this window with the current filters':'');
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
    return Response(_PAGE.replace("__LOGO__", padb_brand.LOGO_DATA_URI), mimetype="text/html")


@app.route("/plotly.js")
def plotlyjs():
    return Response(_PLOTLY, mimetype="text/javascript")


@app.route("/api/meta")
def api_meta():
    return jsonify(DS.meta())


def _conds_arg():
    """Legacy `conds` query param: JSON {dimId: [values]} of only the NARROWED dimensions."""
    raw = request.args.get("conds", "")
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return {int(k): [str(x) for x in v] for k, v in d.items()} if isinstance(d, dict) else None
    except Exception:
        return None


class _BadSpec(ValueError):
    pass


def _spec_from_request() -> FilterSpec:
    """THE filter state of a request: a single `spec` JSON (a FilterSpec) -- or, for older callers, the
    legacy flo/fhi/sites/temps/pf/conds params. Raises _BadSpec (-> 400) on junk. The window defaults to
    the dataset's x-range."""
    raw = request.args.get("spec")
    try:
        if raw:
            spec = FilterSpec.from_dict(json.loads(raw))
        else:
            spec = FilterSpec.from_dict({
                "lo": float(request.args.get("flo") or DS.x_min),
                "hi": float(request.args.get("fhi") or DS.x_max),
                "sites": [s for s in (request.args.get("sites", "") or "").split(",") if s],
                "temps": [x for x in (request.args.get("temps", "") or "").split("|") if x],
                "pf": request.args.get("pf", "all") if request.args.get("pf", "all") in ("all", "pass", "fail") else "all",
                "conds": _conds_arg() or {}})
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _BadSpec(str(exc))
    import dataclasses
    return dataclasses.replace(spec, lo=DS.x_min if spec.lo == -float("inf") else spec.lo,
                               hi=DS.x_max if spec.hi == float("inf") else spec.hi)


def _filt_of(spec: FilterSpec):
    """The part of a spec the band views must honour (everything except the window), or None when it
    constrains nothing."""
    import dataclasses
    f = dataclasses.replace(spec, lo=-float("inf"), hi=float("inf"))
    return f if (f.sites or f.temps or f.conds or f.pf != "all") else None


@app.errorhandler(_BadSpec)
def _bad_spec(exc):
    return jsonify({"ok": False, "error": "bad filter spec: %s" % exc}), 400


@app.route("/api/scatter")
def api_scatter():
    spec = _spec_from_request()
    maxpts = max(100, min(50000, int(float(request.args.get("maxpts", 4000)))))
    by = [b for b in (request.args.get("by", "") or "").split(",") if b]
    if hasattr(DS, "query"):
        return jsonify(DS.query(spec, maxpts, by))
    return jsonify(DS.scatter(spec.lo, spec.hi, spec.sites, maxpts, spec.temps, spec.pf,
                              {k: v for k, v in spec.conds.items()} or None, by))


# --- Which conditions are exposed as filters (padb_core.filterconfig) ----------------------------
# A per-dataset config beside the data (show / order / labels, incl. "expose anyway" for keys the
# automatic rules drop). Saved atomically; applying is metadata only (instant, no reload, ids stable so
# saved selections stay valid). A condition that is not exposed puts no restriction on the data.
FILTERS_PATH: Path | None = None


def _filters_payload():
    m = DS.meta()
    return {"config": m["filters"], "dims": m["dims"], "all_dims": m["all_dims"],
            "unavailable": m["unavailable"], "supported": hasattr(DS, "set_filters"),
            "path": str(FILTERS_PATH or (DS.path.parent / _filterconfig.DEFAULT_NAME))}


@app.route("/api/filters", methods=["GET", "POST"])
def api_filters():
    if request.method == "GET":
        return jsonify(_filters_payload())
    if not hasattr(DS, "set_filters"):
        return jsonify({"ok": False, "error": "customising filters needs the DuckDB engine"}), 400
    body = request.get_json(silent=True)
    try:
        cfg = FilterConfig.from_dict((body or {}).get("config"))
    except (ValueError, TypeError, AttributeError) as exc:
        return jsonify({"ok": False, "error": "bad filter configuration: %s" % exc}), 400
    path = FILTERS_PATH or (DS.path.parent / _filterconfig.DEFAULT_NAME)
    with _bands_lock:
        try:
            _filterconfig.save_file(path, cfg)
        except OSError as exc:
            return jsonify({"ok": False, "error": "could not save %s: %s" % (path, exc)}), 500
        unknown = DS.set_filters(cfg)
    return jsonify({"ok": True, "unknown": unknown, **_filters_payload()})


# --- One filter file across tools (padb_core.lockio) ---------------------------------------------
# Export the current filter as the HTML pages' "lock" JSON, or import one (from a page or another
# viewer). The mapping lives ONCE in padb_core.lockio; the page just posts and applies the result.
def _lock_ctx():
    m = DS.meta()
    exposed = {d["name"] for d in m.get("dims", [])}
    return {"dims": m.get("dims", []), "sites": m["sites"], "temps": m["temps"],
            "x_min": m["x_min"], "x_max": m["x_max"],
            "hidden": [a["name"] for a in m.get("all_dims", []) if a["name"] not in exposed]}


@app.route("/api/filter/export", methods=["POST"])
def api_filter_export():
    body = request.get_json(silent=True) or {}
    try:
        spec = FilterSpec.from_dict(body.get("spec") or {})
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return jsonify({"ok": False, "error": "bad filter spec: %s" % exc}), 400
    return jsonify({"ok": True, "lock": lockio.to_lock(spec, _lock_ctx(), title=DS.path.stem)})


@app.route("/api/filter/import", methods=["POST"])
def api_filter_import():
    body = request.get_json(silent=True) or {}
    try:
        spec, applied, skipped = lockio.from_lock(body.get("lock"), _lock_ctx())
    except (ValueError, TypeError, AttributeError) as exc:
        return jsonify({"ok": False, "error": "not a usable filter file: %s" % exc}), 400
    import dataclasses
    spec = dataclasses.replace(spec, lo=DS.x_min if spec.lo == -float("inf") else spec.lo,
                               hi=DS.x_max if spec.hi == float("inf") else spec.hi)
    return jsonify({"ok": True, "spec": spec.to_dict(), "applied": applied,
                    "skipped": [{"label": a, "reason": b} for a, b in skipped]})


@app.route("/api/segments")
def api_segments():
    return jsonify({"spec": DS.spec_segments(), "bands": DS.bands})


# --- Live-editable named bands --------------------------------------------------------
# The band file (padb_viewer_bands.json / bands.json) is the single source of truth shared
# with the HTML views. The overview's editor POSTs the full list; we validate, write the
# file atomically (so the next load, the HTML builds and this server all agree), then
# update the live DataSet so buttons / counts / the stepper / the band-view cap follow
# without a restart. A failed write changes NOTHING in memory.
BANDS_PATH: Path | None = None
_bands_lock = threading.Lock()
MAX_BANDS = 64


def _validate_bands(raw):
    """-> (clean sorted band list, None) or (None, human-readable error)."""
    if not isinstance(raw, list):
        return None, "bands must be a list"
    if len(raw) > MAX_BANDS:
        return None, "at most %d bands" % MAX_BANDS
    out, names = [], set()
    for i, b in enumerate(raw, 1):
        if not isinstance(b, dict):
            return None, "band %d: not an object" % i
        name = str(b.get("name", "")).strip()
        if not name:
            return None, "band %d: the name is empty" % i
        if len(name) > 60:
            return None, "band %d: name longer than 60 characters" % i
        try:
            lo, hi = float(b["lo"]), float(b["hi"])
        except (KeyError, TypeError, ValueError):
            return None, "band %d (%s): lo and hi must be numbers" % (i, name)
        if not (math.isfinite(lo) and math.isfinite(hi)):
            return None, "band %d (%s): lo and hi must be finite" % (i, name)
        if not lo < hi:
            return None, "band %d (%s): lo must be below hi" % (i, name)
        if name in names:
            return None, "band %d: duplicate name '%s'" % (i, name)
        names.add(name)
        out.append({"name": name, "lo": lo, "hi": hi})
    out.sort(key=lambda d: d["lo"])
    return out, None


def _bands_payload():
    m = DS.meta()
    return {"bands": m["bands"], "band_counts": m["band_counts"], "max_band_rows": m["max_band_rows"],
            "band_cap": m["band_cap"], "slow_band_rows": m["slow_band_rows"],
            "slow_band_warn": m["slow_band_warn"], "x_unit": m["x_unit"],
            "path": str(BANDS_PATH or (DS.path.parent / "padb_viewer_bands.json"))}


@app.route("/api/bands", methods=["GET", "POST"])
def api_bands():
    if request.method == "GET":
        return jsonify(_bands_payload())
    body = request.get_json(silent=True) or {}
    clean, err = _validate_bands(body.get("bands"))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    path = BANDS_PATH or (DS.path.parent / "padb_viewer_bands.json")
    with _bands_lock:
        try:
            tmp = Path(str(path) + ".part")
            tmp.write_text(json.dumps({"unit": DS.x_unit or "", "bands": clean}, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            return jsonify({"ok": False, "error": "could not save %s: %s" % (path, exc)}), 500
        DS.bands = clean
        DS.compute_band_counts()
    return jsonify({"ok": True, **_bands_payload()})


# --- Live-editable LIMIT overrides ---------------------------------------------------------
# Same pattern as the bands: validate, write the shared file atomically, then update the live
# DataSet so the overview (limit line, Passing/Failing, spec-stair stepper) and every band view
# built from here on follow without a restart. Overrides never touch the parquet.
LIMITS_PATH: Path | None = None


def _limits_payload():
    m = DS.meta()
    return {"limits": m["limits"], "has_limits": m["has_limits"], "x_unit": m["x_unit"],
            "path": str(LIMITS_PATH or (DS.path.parent / padb_limits.DEFAULT_NAME))}


@app.route("/api/limits", methods=["GET", "POST"])
def api_limits():
    if request.method == "GET":
        return jsonify(_limits_payload())
    body = request.get_json(silent=True) or {}
    clean, err = padb_limits.validate(body.get("limits"))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    path = LIMITS_PATH or (DS.path.parent / padb_limits.DEFAULT_NAME)
    with _bands_lock:
        try:
            padb_limits.save_file(path, clean, DS.x_unit)
        except OSError as exc:
            return jsonify({"ok": False, "error": "could not save %s: %s" % (path, exc)}), 500
        DS.set_limits(clean)
    return jsonify({"ok": True, **_limits_payload()})


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


def _render_view_band(view: str, flo: float, fhi: float, full: bool = False,
                      filt=None) -> tuple[str, int]:
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

    if not hasattr(DS, "band_row_count"):
        filt = None            # pandas engine: band views are unfiltered
    key = (view, round(flo, 6), round(fhi, 6), bool(full),
           (filt.key() if isinstance(filt, FilterSpec) else json.dumps(filt, sort_keys=True, default=str)) if filt else "",
           json.dumps(_limits_list(DS), sort_keys=True))
    cached = _band_cache.get(key)
    if cached and os.path.exists(cached[0]):
        return cached

    if hasattr(DS, "band_row_count"):
        # DuckDB engine: count in-engine; only materialise the slice if it will be rendered.
        n = DS.band_row_count(flo, fhi, filt)
        t2 = None
    else:
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
    if t2 is None:
        t2 = DS.band_table(flo, fhi, filt)

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
    if _limits_list(DS):
        cfg["limit_overrides"] = _limits_list(DS)   # data x-units; padb_v2 applies them before any view
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
    try:
        spec = _spec_from_request()
    except _BadSpec as exc:
        return Response("<pre>bad filter spec: %s</pre>" % exc, mimetype="text/html", status=400)
    flo, fhi = spec.lo, spec.hi
    full = request.args.get("full", "0") in ("1", "true", "True")
    if view in ("env_coverage", "distribution") and getattr(DS, "is_room_only", False):
        return Response(
            f"<pre>{view} needs multi-temperature data (a Room baseline + non-Room "
            f"readings) -- this dataset is Room-only, so it isn't available (same as the "
            f"html build, which omits it for Room-only data).</pre>", mimetype="text/html")
    try:
        path, n = _render_view_band(view, flo, fhi, full=full, filt=_filt_of(spec))
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
    ap.add_argument("--engine", choices=["duckdb", "pandas"], default=None,
                    help="Query engine (default duckdb if installed, else pandas; "
                         "env PADB_VIEWER_ENGINE also works)")
    ap.add_argument("--bands", help="JSON file of named frequency bands "
                    "(default: bands.json / padb_viewer_bands.json next to the parquet)")
    ap.add_argument("--filters", help="JSON file saying which conditions are exposed as filters "
                    "(default: padb_viewer_filters.json / filters.json next to the parquet)")
    ap.add_argument("--limits", help="JSON file of limit overrides "
                    "(default: padb_viewer_limits.json / limits.json next to the parquet)")
    ap.add_argument("--auto-bands", dest="auto_bands", action="store_true", default=True,
                    help="If no band file is found, auto-generate an editable starter "
                         "from the swept data (ON by default; an existing file always loads)")
    ap.add_argument("--no-auto-bands", dest="auto_bands", action="store_false",
                    help="Do NOT auto-generate a band file (an existing one still loads)")
    args = ap.parse_args(argv)

    global DS, BANDS_PATH, LIMITS_PATH, FILTERS_PATH
    target = Path(args.target).resolve() if args.target else _default_target()
    pqpath = _find_parquet(target)
    print(f"Loading {pqpath} ...", flush=True)
    t0 = time.time()
    DS = DataSet(pqpath, x_override=args.x, value_override=args.value, engine=args.engine)
    print(f"  [engine] {DS.engine}: ready in {time.time() - t0:.1f}s", flush=True)

    if args.bands:
        BANDS_PATH = Path(args.bands).resolve()
        DS.bands = _load_bands(BANDS_PATH, DS.x_unit)
    else:
        # Shared discovery: existing sidecar wins; otherwise auto-generate an editable
        # starter from the actual swept data (David 2026-09-24) and invite edits.
        DS.bands, bpath, created = padb_bands.find_or_create_bands(
            DS.distinct_x(), DS.x_unit, [pqpath.parent], allow_create=args.auto_bands,
            x_label=DS.x_label)
        BANDS_PATH = Path(bpath) if bpath else None
        if created and bpath:
            print(f"  [bands] no band file found -- auto-generated {len(DS.bands)} starter "
                  f"band(s) -> {Path(bpath).name}. Edit it to rename/re-range, then reload.",
                  flush=True)
        elif DS.bands and bpath:
            print(f"  [bands] loaded {len(DS.bands)} band(s) from {Path(bpath).name} "
                  f"(data unit {DS.x_unit or '?'})", flush=True)
    DS.compute_band_counts()
    fpath = Path(args.filters).resolve() if args.filters else _filterconfig.find_file([pqpath.parent])
    FILTERS_PATH = fpath if fpath else None
    if fpath and hasattr(DS, "set_filters"):
        fcfg, ferr = _filterconfig.load_file(fpath)
        if ferr:
            print(f"  [filters] {ferr} -- ignored", flush=True)
        elif not fcfg.is_default():
            unknown = DS.set_filters(fcfg)
            print(f"  [filters] {fpath.name} applied ({len(DS.dims)} condition filter(s) exposed"
                  + (f"; not in this data: {', '.join(unknown)}" if unknown else "") + ")", flush=True)
    lpath = Path(args.limits).resolve() if args.limits else padb_limits.find_file([pqpath.parent])
    LIMITS_PATH = lpath if lpath else None
    if lpath:
        lims, lerr = padb_limits.load_file(lpath, DS.x_unit)
        if lerr:
            print(f"  [limits] {lerr} -- ignored", flush=True)
        elif lims:
            DS.set_limits(lims)
            print(f"  [limits] {len(lims)} limit override range(s) from {lpath.name} applied", flush=True)
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
