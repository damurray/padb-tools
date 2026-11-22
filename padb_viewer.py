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
import time
import webbrowser
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pandas as pd
from flask import Flask, Response, jsonify, request

import padb_bands

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


def _parse_group_dims(g: str) -> dict:
    """Group text -> {key: value}. Fragments split on 2+ spaces; a fragment with no ':'
    is PADB's value-padding ("Frequency (MHz):  10") and is merged into the previous value."""
    out, last = {}, None
    for frag in re.split(r"\s{2,}", g or ""):
        frag = frag.strip()
        if not frag:
            continue
        if ":" in frag:
            k, v = frag.split(":", 1)
            last = k.strip()
            if last:
                out[last] = v.strip()
        elif last is not None:
            out[last] = (out[last] + " " + frag).strip()
    return out


MAX_DIM_VALUES = 40   # a condition dimension with more distinct values isn't a usable filter


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
        self.df = df[["x", "y", "site", "serial", "temp", "yhi", "ylo", "scored", "fail"]] \
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


def _qi(name: str) -> str:
    """Quote a column name for DuckDB SQL."""
    return '"' + str(name).replace('"', '""') + '"'


class DuckDataSet:
    """DuckDB-backed twin of PandasDataSet (same public interface).

    The parquet is read ONCE by DuckDB (streamed, parallel, bigger-than-RAM capable) into
    a small derived columnar table in a temp database file -- x, y, site, serial, temp,
    limits, fail flag, plus the source row number -- sorted by x so range filters prune
    on zone maps. Every query (filter + min/max-envelope decimation, per-band counts,
    spec segments, band slices) then runs in SQL inside the engine; nothing but the
    decimated result is ever materialised in Python, so a multi-million-row analytic
    needs neither a big pandas frame nor the whole raw table held in memory.

    Group/Test Step parsing (Site/Serial/Temp) is done in Python over the DISTINCT
    values only (tens of thousands, not millions) with the SAME regexes as the pandas
    path, then joined in -- so the two engines classify every row identically."""

    def __init__(self, parquet_path: Path, x_override=None, value_override=None):
        import duckdb
        self.path = parquet_path
        names = pq.ParquetFile(parquet_path).schema.names
        self.x_col, self.value_col, self.serial_col, self.group_col, self.temp_col = \
            _detect_columns(names, x_override, value_override)
        if not self.x_col or not self.value_col:
            sys.exit(f"Could not detect x/value columns in {parquet_path.name} "
                     f"(cols: {names}). Use --x / --value to set them.")
        _low = {n.lower(): n for n in names}

        def _find(*subs):
            for k, orig in _low.items():
                if all(s in k for s in subs):
                    return orig
            return None
        self.hi_col = _find("upper", "limit") or _find("upper", "spec")
        self.lo_col = _find("lower", "limit") or _find("lower", "spec")

        self._tmpdir = Path(tempfile.mkdtemp(prefix="padb_duck_"))
        import atexit
        import shutil
        atexit.register(shutil.rmtree, str(self._tmpdir), True)
        self.con = duckdb.connect(str(self._tmpdir / "viewer.duckdb"))
        self.con.execute("SET temp_directory = ?", [str(self._tmpdir / "spill")])
        self._src = "read_parquet('" + str(parquet_path).replace("\\", "/").replace("'", "''") + "')"
        self._build()
        self.engine = "duckdb"
        self.value_label = self.value_col
        self.x_label = self.x_col
        m = re.search(r"\(([^)]+)\)", self.x_label)
        self.x_unit = m.group(1) if m else ""
        self.bands: list[dict] = []
        self.band_counts: list[int] = []
        self.max_band_rows: int = 0

    # -- build ------------------------------------------------------------------
    def _distinct_map(self, col, fn, out_cols):
        """DISTINCT values of `col` (null -> 'nan', as pandas astype(str) does) mapped
        through python fn -> tuple, registered as a join table. Returns table name."""
        rows = self.con.execute(
            f"SELECT DISTINCT COALESCE(CAST({_qi(col)} AS VARCHAR), 'nan') FROM {self._src}"
        ).fetchall()
        vals = [r[0] for r in rows]
        mapped = [fn(v) for v in vals]
        return vals, mapped

    def _build(self):
        con = self.con
        # Group -> site / serial (python regexes over distinct groups only)
        site_expr, serial_expr = "'(all)'", "'?'"
        gjoin = ""
        self.dims: list = []
        gid_expr = "CAST(NULL AS BIGINT)"
        if self.group_col:
            vals, _ = self._distinct_map(self.group_col, lambda v: v, None)
            site_re = re.compile(r"Site:\s*([^\s]+)")
            ser_re1 = re.compile(r"Serial\s*(?:Number|No|Num)?\s*:\s*([^\s|]+)")
            ser_re2 = re.compile(r"\b([A-Za-z]{2,3}\d{5,})\b")
            sites = []
            sers1 = []
            for v in vals:
                m = site_re.search(v)
                sites.append(m.group(1) if m else "(all)")
                m = ser_re1.search(v)
                sers1.append(m.group(1) if m else None)
            if all(x is None for x in sers1):
                sers = []
                for v in vals:
                    m = ser_re2.search(v)
                    sers.append(m.group(1) if m else None)
            else:
                sers = sers1
            gdict = {"g": vals, "g_site": sites,
                     "g_serial": [x if x is not None else "?" for x in sers]}
            # Condition dimensions (every "Key: value" in Group with 2..MAX_DIM_VALUES values):
            # parsed over DISTINCT groups only, served as filter checkboxes.
            kvs = [_parse_group_dims(v) for v in vals]
            seen: dict = {}
            for kv in kvs:
                for k, v in kv.items():
                    seen.setdefault(k, set()).add(v)
            ser_pat = re.compile(r"^[A-Z]{2,3}\d{5,}$")
            for k in sorted(seen):
                kl = k.lower()
                vs = seen[k]
                xb = self.x_col.lower().split("(")[0].strip()
                if (kl == "site" or "temp" in kl or "frequency" in kl
                        or kl.split("(")[0].strip() == xb
                        or any(t in kl for t in ("serial", "unit id", "dut id", "s/n"))
                        or len(vs) < 2 or len(vs) > MAX_DIM_VALUES
                        or sum(1 for x in vs if ser_pat.match(x)) * 2 > len(vs)):
                    continue
                i = len(self.dims)
                self.dims.append({"id": i, "name": k, "values": sorted(vs)})
                gdict[f"d{i}"] = [kv.get(k) for kv in kvs]
            gdict["gid"] = list(range(len(vals)))
            gdf = pd.DataFrame(gdict)
            con.register("gmap_df", gdf)
            con.execute("CREATE TABLE gmap AS SELECT * FROM gmap_df")
            con.unregister("gmap_df")
            gjoin = (f"LEFT JOIN gmap ON gmap.g = COALESCE(CAST(r.{_qi(self.group_col)} "
                     f"AS VARCHAR), 'nan')")
            site_expr = "gmap.g_site"
            serial_expr = "gmap.g_serial"
            gid_expr = "gmap.gid"
        dsel = "".join(f", gmap.d{d['id']} AS d{d['id']}" for d in self.dims)
        dout = "".join(f", d{d['id']}" for d in self.dims) + ", gid"
        dsel += f", {gid_expr} AS gid"
        if self.serial_col:
            serial_expr = f"CAST(r.{_qi(self.serial_col)} AS VARCHAR)"
        tjoin, temp_expr = "", "'Room'"
        if self.temp_col:
            vals, _ = self._distinct_map(self.temp_col, lambda v: v, None)
            tdf = pd.DataFrame({"t": vals,
                                "t_temp": [(v.strip() or "Room") for v in vals]})
            con.register("tmap_df", tdf)
            con.execute("CREATE TABLE tmap AS SELECT * FROM tmap_df")
            con.unregister("tmap_df")
            tjoin = (f"LEFT JOIN tmap ON tmap.t = COALESCE(CAST(r.{_qi(self.temp_col)} "
                     f"AS VARCHAR), 'nan')")
            temp_expr = "tmap.t_temp"

        def num(col):
            return f"TRY_CAST(r.{_qi(col)} AS DOUBLE)"
        hi = f"CASE WHEN isnan({num(self.hi_col)}) THEN NULL ELSE {num(self.hi_col)} END" \
            if self.hi_col else "CAST(NULL AS DOUBLE)"
        lo = f"CASE WHEN isnan({num(self.lo_col)}) THEN NULL ELSE {num(self.lo_col)} END" \
            if self.lo_col else "CAST(NULL AS DOUBLE)"
        con.execute(f"""
            CREATE TABLE pts AS
            SELECT x, y, site, serial, temp, yhi, ylo{dout},
                   (yhi IS NOT NULL OR ylo IS NOT NULL) AS scored,
                   COALESCE((yhi IS NOT NULL AND y > yhi) OR (ylo IS NOT NULL AND y < ylo), false) AS fail,
                   rid
            FROM (
              SELECT {num(self.x_col)} AS x, {num(self.value_col)} AS y,
                     {site_expr} AS site, {serial_expr} AS serial, {temp_expr} AS temp,
                     {hi} AS yhi, {lo} AS ylo, r.file_row_number AS rid{dsel}
              FROM read_parquet('{str(self.path).replace(chr(92), '/').replace("'", "''")}',
                                file_row_number=true) r
              {gjoin} {tjoin}
            ) WHERE x IS NOT NULL AND y IS NOT NULL AND NOT isnan(x) AND NOT isnan(y)
            ORDER BY x, rid""")
        r = con.execute("SELECT count(*), min(x), max(x), count(DISTINCT serial), "
                        "bool_or(scored) FROM pts").fetchone()
        self.n_rows, self.x_min, self.x_max, self.n_serials = int(r[0]), r[1], r[2], int(r[3])
        self.has_limits = bool(r[4])
        if self.n_rows == 0:
            sys.exit(f"No usable (x, value) rows in {self.path.name}")
        self.x_min, self.x_max = float(self.x_min), float(self.x_max)
        self.sites = [str(v[0]) for v in con.execute(
            "SELECT DISTINCT site FROM pts ORDER BY site").fetchall() if v[0] is not None]
        self.temps = [str(v[0]) for v in con.execute(
            "SELECT DISTINCT temp FROM pts ORDER BY temp").fetchall() if v[0] is not None]
        self.is_room_only = len(self.temps) < 2

    def _all(self, sql, params=None):
        return self.con.cursor().execute(sql, params or []).fetchall()

    # -- interface shared with PandasDataSet --------------------------------------
    meta = PandasDataSet.meta

    def distinct_x(self):
        return [r[0] for r in self._all("SELECT DISTINCT x FROM pts ORDER BY x")]

    def compute_band_counts(self):
        self.band_counts, self.max_band_rows = [], 0
        for b in self.bands:
            n = int(self._all("SELECT count(*) FROM pts WHERE x >= ? AND x <= ?",
                              [b["lo"], b["hi"]])[0][0])
            self.band_counts.append(n)
            b["count"] = n
        self.max_band_rows = max(self.band_counts) if self.band_counts else 0

    def spec_segments(self):
        if not self.has_limits:
            return []
        rows = self._all(
            "SELECT x, arg_min(yhi, rid) FILTER (WHERE yhi IS NOT NULL), "
            "arg_min(ylo, rid) FILTER (WHERE ylo IS NOT NULL) FROM pts GROUP BY x ORDER BY x")
        import collections
        R = collections.namedtuple("R", "x yhi ylo")
        return _spec_segments_from((R(*r) for r in rows), self.x_max)

    def _where(self, flo, fhi, sites, temps, pf, conds):
        where, args = ["x >= ?", "x <= ?"], [flo, fhi]
        if sites:
            where.append("site IN (" + ",".join("?" * len(sites)) + ")")
            args += list(sites)
        if temps:
            where.append("temp IN (" + ",".join("?" * len(temps)) + ")")
            args += list(temps)
        if pf == "fail":
            where.append("fail")
        elif pf == "pass":
            where.append("NOT fail")
        ok = {d["id"] for d in self.dims}
        for k, vals in (conds or {}).items():
            k = int(k)
            if k not in ok:
                continue
            if not vals:
                where.append("false")      # every value of this dim unticked -> nothing
                continue
            # blank/absent value for a dim = not applicable = no constraint (never excluded)
            where.append(f"(d{k} IS NULL OR d{k} IN (" + ",".join("?" * len(vals)) + "))")
            args += list(vals)
        return " AND ".join(where), args

    def _group_expr(self, by):
        """SQL expression naming each point's group: the chosen keys joined by ' / '.
        Keys: site, serial, temp, d<i> (condition dim). Empty/unknown -> site."""
        ok = {"site": "site", "serial": "serial", "temp": "temp"}
        ok.update({f"d{d['id']}": f"COALESCE(d{d['id']}, '-')" for d in self.dims})
        cols = [ok[k] for k in (by or []) if k in ok]
        if not cols:
            return "site"
        return " || ' / ' || ".join(f"CAST({c} AS VARCHAR)" for c in cols)

    MAX_GROUPS = 60

    def scatter(self, flo, fhi, sites, maxpts, temps=None, pf="all", conds=None, by=None):
        w, args = self._where(flo, fhi, sites, temps, pf, conds)
        g = self._group_expr(by)
        counts = self._all(f"SELECT {g}, count(*) FROM pts WHERE {w} GROUP BY 1 ORDER BY 1", args)
        if len(counts) > self.MAX_GROUPS:
            return {"traces": [], "n_total": int(sum(c for _, c in counts)), "n_returned": 0,
                    "groups": {}, "too_many": len(counts)}
        n_total = int(sum(c for _, c in counts))
        nb = max(1, maxpts // 2)
        span = (fhi - flo) or 1.0
        traces = []
        for site, n in counts:
            if n == 0:
                continue
            if n <= maxpts:
                rows = self._all(f"SELECT x, y, gid, serial, temp FROM pts WHERE {w} AND ({g}) = ? ORDER BY x, rid",
                                 args + [site])
            else:
                rows = self._all(f"""
                    WITH b AS (
                      SELECT x, y, rid, gid, serial, temp,
                             LEAST({nb - 1}, GREATEST(0, CAST(floor((x - ?) / ? * {nb}) AS BIGINT))) AS bk
                      FROM pts WHERE {w} AND ({g}) = ?),
                    m AS (SELECT arg_min(rid, y) AS r1, arg_max(rid, y) AS r2 FROM b GROUP BY bk)
                    SELECT x, y, gid, serial, temp FROM b
                    WHERE rid IN (SELECT r1 FROM m UNION SELECT r2 FROM m)
                    ORDER BY x, rid""", [flo, span] + args + [site])
            xs = [r[0] for r in rows]
            ys = [r[1] for r in rows]
            # hover: serial + temp + the point's condition (Group) text; the long Group
            # strings are sent once in `groups` and referenced by id per point.
            traces.append({"site": str(site), "x": xs, "y": ys, "n": int(n),
                           "gi": [r[2] for r in rows], "sn": [r[3] for r in rows],
                           "tp": [r[4] for r in rows]})
        used = sorted({g for t in traces for g in t["gi"] if g is not None})
        groups = {}
        if used:
            for i in range(0, len(used), 5000):
                part = used[i:i + 5000]
                for gid, txt in self._all(
                        "SELECT gid, g FROM gmap WHERE gid IN (" + ",".join("?" * len(part)) + ")", part):
                    groups[int(gid)] = re.sub(r"\s{2,}", " | ", str(txt).strip())
        n_ret = sum(len(t["x"]) for t in traces)
        return {"traces": traces, "n_total": n_total, "n_returned": n_ret, "groups": groups}

    # -- band slices of the RAW parquet (for the padb_v2 band views) ---------------
    def _band_pred(self):
        return f"TRY_CAST({_qi(self.x_col)} AS DOUBLE) BETWEEN ? AND ?"

    def _filt_where(self, flo, fhi, filt):
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
  <label id="limbox" style="display:none" title="Draw the spec/limit line(s) from the data's Upper/Lower Limit columns (a step where the limit changes along x). Display only -- the limit shown is the first one in file order at each x."><input type="checkbox" id="showlim" onchange="update()"> Show limits</label>
  <button id="grpbtn" onclick="toggleGrp()"
    title="Colour/split the overview points by Site, Serial Number, Temperature or any condition. Display only -- does not filter.">Group by &#9662;</button>
  <button id="condbtn" onclick="toggleConds()" style="display:none"
    title="Filter by condition (every Key: value in the Group text). Unticking a value hides those points in the plot AND in the band views.">Conditions &#9662;</button>
  <span class="sitebox" id="pfbox" title="Filter the overview points by pass/fail against each point's own limit. Failing = crosses a present limit; Passing = everything else (pass + no-limit). Needs a limit column in the data.">
    Show:
    <label><input type="radio" name="pf" value="all" checked onchange="update()">All</label>
    <label><input type="radio" name="pf" value="pass" onchange="update()">Passing</label>
    <label><input type="radio" name="pf" value="fail" onchange="update()">Failing</label></span>
  <button onclick="update()">Update</button>
  <button onclick="resetView()">Reset</button>
  <span id="status"></span>
</div>
<div id="grppanel" style="display:none;padding:6px 14px;background:#f2fff2;border-bottom:1px solid #ddd;font-size:12px"></div>
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
  if(META.has_limits){ document.getElementById('limbox').style.display=''; }
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
// --- Condition filters (every Key: value dimension in Group). Only NARROWED dims are sent. ---
function buildConds(){
  var dims=(META&&META.dims)||[]; if(!dims.length) return;
  var p=document.getElementById('condpanel'), h='';
  dims.forEach(function(d){
    h+='<div style="margin:3px 0"><b>'+d.name+':</b> ';
    d.values.forEach(function(v){
      h+='<label style="margin-right:8px"><input type="checkbox" checked class="condchk" data-dim="'+d.id+
         '" value="'+String(v).replace(/"/g,'&quot;')+'" onchange="update()"> '+v+'</label>';
    });
    h+=' <a href="#" onclick="condAll('+d.id+',true);return false">all</a> / <a href="#" onclick="condAll('+d.id+',false);return false">none</a></div>';
  });
  h+='<div style="margin-top:4px"><a href="#" onclick="condReset();return false">Reset condition filters</a></div>';
  p.innerHTML=h;
  document.getElementById('condbtn').style.display='';
}
function buildGrp(){
  var h='<b>Group by:</b> ', opts=[['site','Site'],['serial','Serial Number'],['temp','Temperature']];
  ((META&&META.dims)||[]).forEach(function(d){ opts.push(['d'+d.id,d.name]); });
  opts.forEach(function(o){
    h+='<label style="margin-right:10px"><input type="checkbox" class="grpchk" value="'+o[0]+'"'+(o[0]==='site'?' checked':'')+
       ' onchange="update()"> '+o[1]+'</label>'; });
  h+=' <span style="color:#888">(none checked = one colour; more than '+60+' groups is refused -- pick fewer keys)</span>';
  document.getElementById('grppanel').innerHTML=h;
}
function toggleGrp(){ var p=document.getElementById('grppanel'); p.style.display=(p.style.display==='none')?'block':'none'; }
function selectedBy(){ return [...document.querySelectorAll('.grpchk:checked')].map(function(c){return c.value;}); }
function toggleConds(){ var p=document.getElementById('condpanel'); p.style.display=(p.style.display==='none')?'block':'none'; }
function condAll(id,on){ document.querySelectorAll('.condchk[data-dim="'+id+'"]').forEach(function(c){c.checked=on;}); update(); }
function condReset(){ document.querySelectorAll('.condchk').forEach(function(c){c.checked=true;}); update(); }
function selectedConds(){
  var by={}, tot={};
  document.querySelectorAll('.condchk').forEach(function(c){
    var k=c.getAttribute('data-dim'); tot[k]=(tot[k]||0)+1; if(!by[k]) by[k]=[]; if(c.checked) by[k].push(c.value); });
  var out={}; Object.keys(by).forEach(function(k){ if(by[k].length<tot[k]) out[k]=by[k]; });
  return out;
}
function filterQS(){
  var q='&sites='+encodeURIComponent(selectedSites().join(','))+'&temps='+encodeURIComponent(selectedTemps().join('|'))+
        '&pf='+((document.querySelector('input[name="pf"]:checked')||{}).value||'all');
  var c=selectedConds(); if(Object.keys(c).length) q+='&conds='+encodeURIComponent(JSON.stringify(c));
  return q;
}
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
  f.src='/view?view='+encodeURIComponent(v)+'&flo='+flo+'&fhi='+fhi+'&full='+full+filterQS();
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
  const t0=performance.now();
  const seq=++_updSeq;
  const r=await (await fetch(`/api/scatter?flo=${flo}&fhi=${fhi}&maxpts=${maxpts}&by=${encodeURIComponent(selectedBy().join(','))}`+filterQS())).json();
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
    return Response(_PAGE, mimetype="text/html")


@app.route("/plotly.js")
def plotlyjs():
    return Response(_PLOTLY, mimetype="text/javascript")


@app.route("/api/meta")
def api_meta():
    return jsonify(DS.meta())


def _conds_arg():
    """Condition filters from the query string: conds = JSON {dimId: [values]} holding
    only the dimensions the user has NARROWED (an open dim never constrains)."""
    raw = request.args.get("conds", "")
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return {int(k): [str(x) for x in v] for k, v in d.items()} if isinstance(d, dict) else None
    except Exception:
        return None


def _filt_arg():
    """The full filter set (sites/temps/pass-fail/conditions) the band views must honour."""
    sites = [s for s in (request.args.get("sites", "") or "").split(",") if s]
    temps = [t for t in (request.args.get("temps", "") or "").split("|") if t]
    pf = request.args.get("pf", "all")
    conds = _conds_arg()
    if not conds and not sites and not temps and pf == "all":
        return None
    return {"sites": sites, "temps": temps, "pf": pf if pf in ("pass", "fail") else "all",
            "conds": conds}


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
    by = [b for b in (request.args.get("by", "") or "").split(",") if b]
    return jsonify(DS.scatter(flo, fhi, sites, maxpts, temps, pf, _conds_arg(), by))


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
           json.dumps(filt, sort_keys=True, default=str) if filt else "")
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
        path, n = _render_view_band(view, flo, fhi, full=full, filt=_filt_arg())
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
    ap.add_argument("--auto-bands", dest="auto_bands", action="store_true", default=True,
                    help="If no band file is found, auto-generate an editable starter "
                         "from the swept data (ON by default; an existing file always loads)")
    ap.add_argument("--no-auto-bands", dest="auto_bands", action="store_false",
                    help="Do NOT auto-generate a band file (an existing one still loads)")
    args = ap.parse_args(argv)

    global DS
    target = Path(args.target).resolve() if args.target else _default_target()
    pqpath = _find_parquet(target)
    print(f"Loading {pqpath} ...", flush=True)
    t0 = time.time()
    DS = DataSet(pqpath, x_override=args.x, value_override=args.value, engine=args.engine)
    print(f"  [engine] {DS.engine}: ready in {time.time() - t0:.1f}s", flush=True)

    if args.bands:
        DS.bands = _load_bands(Path(args.bands).resolve(), DS.x_unit)
    else:
        # Shared discovery: existing sidecar wins; otherwise auto-generate an editable
        # starter from the actual swept data (David 2026-09-24) and invite edits.
        DS.bands, bpath, created = padb_bands.find_or_create_bands(
            DS.distinct_x(), DS.x_unit, [pqpath.parent], allow_create=args.auto_bands,
            x_label=DS.x_label)
        if created and bpath:
            print(f"  [bands] no band file found -- auto-generated {len(DS.bands)} starter "
                  f"band(s) -> {Path(bpath).name}. Edit it to rename/re-range, then reload.",
                  flush=True)
        elif DS.bands and bpath:
            print(f"  [bands] loaded {len(DS.bands)} band(s) from {Path(bpath).name} "
                  f"(data unit {DS.x_unit or '?'})", flush=True)
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
