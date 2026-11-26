"""Dataset: the DuckDB analysis engine (v3 core).

Reads a padb-style parquet ONCE into a small derived table (`pts0`: x, y, site, serial, temp, the data's
own limits, typed condition columns, source row id) and exposes the effective-limit view `pts`
(see limits.py). Every query takes a FilterSpec and is answered in SQL, so only the decimated result
reaches Python. Stage-1 equivalent of the v2 viewer's DuckDataSet, built from the core pieces
(GroupIndex, FilterSpec, LimitTable) and proven equal to it by qa/qa_core.py.
"""
from __future__ import annotations

import re
import tempfile
import threading
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from .columns import detect_columns, qi
from .conditions import GroupIndex
from .filterconfig import FilterConfig
from .filterspec import FilterSpec
from .limits import LimitTable

MAX_GROUPS = 60
SERIAL_LIST_MAX = 5000      # more serials than this: the viewer's Serial Number filter is not offered (a list that long is not a dropdown)


def _stairs(rows, x_max):
    """Contiguous x-bands of constant (yhi, ylo). rows: (x, yhi, ylo) ordered by x; None = no limit."""
    def k(v):
        return round(float(v), 4) if v is not None else None

    segs, cur_lo, cur_key, cur_val, prev_x = [], None, None, None, None
    for x, hi, lo in rows:
        key = (k(hi), k(lo))
        val = (None if hi is None else float(hi), None if lo is None else float(lo))
        if cur_key is None:
            cur_lo, cur_key, cur_val = x, key, val
        elif key != cur_key:
            segs.append({"lo": float(cur_lo), "hi": float(prev_x), "hi_val": cur_val[0], "lo_val": cur_val[1]})
            cur_lo, cur_key, cur_val = x, key, val
        prev_x = x
    if cur_lo is not None:
        segs.append({"lo": float(cur_lo), "hi": float(prev_x), "hi_val": cur_val[0], "lo_val": cur_val[1]})
    for i in range(len(segs) - 1):
        segs[i]["hi"] = segs[i + 1]["lo"]
    if segs:
        segs[-1]["hi"] = x_max
    return segs


class Dataset:
    def __init__(self, parquet, x_col=None, value_col=None, filters=None):
        self.path = Path(parquet)
        names = pq.ParquetFile(self.path).schema.names
        (self.x_col, self.value_col, self.serial_col,
         self.group_col, self.temp_col) = detect_columns(names, x_col, value_col)
        if not self.x_col or not self.value_col:
            raise ValueError("could not detect x/value columns in %s (cols: %s)" % (self.path.name, names))
        low = {n.lower(): n for n in names}

        def find(*subs):
            for k, orig in low.items():
                if all(s in k for s in subs):
                    return orig
            return None

        self.hi_col = find("upper", "limit") or find("upper", "spec")
        self.lo_col = find("lower", "limit") or find("lower", "spec")
        self._tmp = tempfile.TemporaryDirectory(prefix="padb_core_")
        self.con = duckdb.connect(str(Path(self._tmp.name) / "core.duckdb"))
        self.con.execute("SET temp_directory = ?", [str(Path(self._tmp.name) / "spill")])
        self._lock = threading.Lock()
        self.index = None
        self.limits = LimitTable()
        self._filters0 = filters or FilterConfig()
        self._build()
        self.x_label, self.value_label = self.x_col, self.value_col
        m = re.search(r"\(([^)]+)\)", self.x_label)
        self.x_unit = m.group(1) if m else ""
        self.engine = "duckdb-core"

    # ---- build ---------------------------------------------------------------------------
    def _all(self, sql, params=None):
        return self.con.cursor().execute(sql, params or []).fetchall()

    def _build(self):
        con = self.con
        src = str(self.path).replace("\\", "/").replace("'", "''")
        site_expr, serial_expr, gid_expr, gjoin = "'(all)'", "'?'", "CAST(NULL AS BIGINT)", ""
        dsel, dout = "", ", gid"
        if self.group_col:
            vals = [r[0] for r in con.execute(
                f"SELECT DISTINCT COALESCE(CAST({qi(self.group_col)} AS VARCHAR), 'nan') FROM read_parquet('{src}')"
            ).fetchall()]
            # sorted -> deterministic group ids (DISTINCT order is arbitrary and differs per run)
            self.index = GroupIndex.build(sorted(vals), x_col=self.x_col, config=self._filters0)
            gdf = self.index.frame()
            con.register("gmap_df", gdf)
            con.execute("CREATE TABLE gmap AS SELECT * FROM gmap_df")
            con.unregister("gmap_df")
            gjoin = (f"LEFT JOIN gmap ON gmap.g = COALESCE(CAST(r.{qi(self.group_col)} AS VARCHAR), 'nan')")
            site_expr, serial_expr, gid_expr = "gmap.g_site", "gmap.g_serial", "gmap.gid"
            # a column for EVERY registry key (not just the exposed ones): exposing / hiding a filter is
            # then metadata only -- instant, no reload, saved selections stay valid
            dsel = "".join(f", gmap.d{r['id']} AS d{r['id']}" for r in self.index.registry)
            dout = "".join(f", d{r['id']}" for r in self.index.registry) + ", gid"
        dsel += f", {gid_expr} AS gid"
        if self.serial_col:
            serial_expr = f"CAST(r.{qi(self.serial_col)} AS VARCHAR)"
        tjoin, temp_expr = "", "'Room'"
        if self.temp_col:
            import pandas as pd
            tv = [r[0] for r in con.execute(
                f"SELECT DISTINCT COALESCE(CAST({qi(self.temp_col)} AS VARCHAR), 'nan') FROM read_parquet('{src}')"
            ).fetchall()]
            con.register("tmap_df", pd.DataFrame({"t": tv, "t_temp": [(v.strip() or "Room") for v in tv]}))
            con.execute("CREATE TABLE tmap AS SELECT * FROM tmap_df")
            con.unregister("tmap_df")
            tjoin = f"LEFT JOIN tmap ON tmap.t = COALESCE(CAST(r.{qi(self.temp_col)} AS VARCHAR), 'nan')"
            temp_expr = "tmap.t_temp"

        def num(c):
            return f"TRY_CAST(r.{qi(c)} AS DOUBLE)"

        hi = (f"CASE WHEN isnan({num(self.hi_col)}) THEN NULL ELSE {num(self.hi_col)} END"
              if self.hi_col else "CAST(NULL AS DOUBLE)")
        lo = (f"CASE WHEN isnan({num(self.lo_col)}) THEN NULL ELSE {num(self.lo_col)} END"
              if self.lo_col else "CAST(NULL AS DOUBLE)")
        con.execute(f"""
            CREATE TABLE pts0 AS
            SELECT x, y, site, serial, temp, yhi0, ylo0{dout}, rid
            FROM (
              SELECT {num(self.x_col)} AS x, {num(self.value_col)} AS y,
                     {site_expr} AS site, {serial_expr} AS serial, {temp_expr} AS temp,
                     {hi} AS yhi0, {lo} AS ylo0, r.file_row_number AS rid{dsel}
              FROM read_parquet('{src}', file_row_number=true) r {gjoin} {tjoin}
            ) WHERE x IS NOT NULL AND y IS NOT NULL AND NOT isnan(x) AND NOT isnan(y)
            ORDER BY x, rid""")
        con.execute(self.limits.view_sql())
        r = con.execute("SELECT count(*), min(x), max(x), count(DISTINCT serial), "
                        "coalesce(bool_or(scored), false) FROM pts").fetchone()
        if not r[0]:
            raise ValueError("no usable (x, value) rows in %s" % self.path.name)
        self.n_rows, self.x_min, self.x_max, self.n_serials = int(r[0]), float(r[1]), float(r[2]), int(r[3])
        self.has_limits = bool(r[4])
        self.sites = [str(v[0]) for v in con.execute("SELECT DISTINCT site FROM pts0 ORDER BY site").fetchall() if v[0] is not None]
        self.temps = [str(v[0]) for v in con.execute("SELECT DISTINCT temp FROM pts0 ORDER BY temp").fetchall() if v[0] is not None]
        self.is_room_only = len(self.temps) < 2

    # ---- properties ----------------------------------------------------------------------
    @property
    def dims(self):
        return self.index.dims if self.index else []

    def dim_ids(self):
        """Ids of the EXPOSED conditions. FilterSpec.to_sql ignores any other id, so a condition that is
        not exposed puts no restriction on the data ("all")."""
        return [d["id"] for d in self.dims]

    @property
    def registry(self):
        return self.index.registry if self.index else []

    def set_filters(self, config):
        """Apply a FilterConfig (which conditions are exposed, in what order, with what labels). Metadata
        only. -> the names the config mentions that this dataset does not have."""
        cfg = config if isinstance(config, FilterConfig) else FilterConfig.from_dict(config)
        with self._lock:
            return self.index.apply_config(cfg) if self.index else []

    def all_dims(self):
        """Every filterable condition with its status, for an editor: id, name, n_values, auto, reason, exposed."""
        exposed = {d["id"]: d for d in self.dims}
        out = []
        for r in self.registry:
            e = exposed.get(r["id"])
            out.append({"id": r["id"], "name": r["name"], "n_values": r["n_values"], "auto": r["auto"],
                        "reason": r["reason"], "exposed": e is not None, "label": (e or {}).get("label", "")})
        return out

    def distinct_x(self):
        return [r[0] for r in self._all("SELECT DISTINCT x FROM pts0 ORDER BY x")]

    def serial_list(self):
        """[[serial, points], ...] sorted by serial, for the viewer's Serial Number filter (cached; the rows never change,
        only the limits do). Capped: a file with more than SERIAL_LIST_MAX serials gets no list (the control is hidden)."""
        if getattr(self, "_serial_list", None) is None:
            rows = self._all("SELECT serial, count(*) FROM pts0 GROUP BY serial ORDER BY serial LIMIT %d" % (SERIAL_LIST_MAX + 1))
            self._serial_list = [] if len(rows) > SERIAL_LIST_MAX else [[str(a), int(b)] for a, b in rows]
        return self._serial_list

    def meta(self):
        return {"parquet": self.path.name, "rows": self.n_rows, "engine": self.engine,
                "x_label": self.x_label, "value_label": self.value_label, "x_min": self.x_min,
                "x_max": self.x_max, "sites": self.sites, "temps": self.temps, "n_serials": self.n_serials,
                "serials": self.serial_list(), "x_unit": self.x_unit, "is_room_only": self.is_room_only, "has_limits": self.has_limits,
                "limits": self.limits.to_list(), "dims": self.dims,
                "all_dims": self.all_dims(), "unavailable": self.index.unavailable if self.index else [],
                "filters": self.index.config.to_dict() if self.index else {}}

    # ---- limits as data ------------------------------------------------------------------
    def set_limits(self, entries):
        """Apply (or, with [], remove) limit overrides: replaces the effective-limit VIEW; no rows are
        rewritten, so clearing is exact and free."""
        table = entries if isinstance(entries, LimitTable) else LimitTable(entries)
        with self._lock:
            self.con.execute(table.view_sql())
            self.limits = table
            self.has_limits = bool(self._all("SELECT coalesce(bool_or(scored), false) FROM pts")[0][0])

    # ---- queries (all take a FilterSpec) -------------------------------------------------
    def _spec(self, spec):
        return spec if isinstance(spec, FilterSpec) else FilterSpec.from_dict(spec)

    def counts(self, spec) -> int:
        w, a = self._spec(spec).to_sql(self.dim_ids())
        return int(self._all(f"SELECT count(*) FROM pts WHERE {w}", a)[0][0])

    def count_in_bands(self, bands):
        return [int(self._all("SELECT count(*) FROM pts WHERE x >= ? AND x <= ?", [b["lo"], b["hi"]])[0][0])
                for b in bands]

    def spec_segments(self):
        if not self.has_limits:
            return []
        rows = self._all("SELECT x, arg_min(yhi, rid) FILTER (WHERE yhi IS NOT NULL), "
                         "arg_min(ylo, rid) FILTER (WHERE ylo IS NOT NULL) FROM pts GROUP BY x ORDER BY x")
        return _stairs(rows, self.x_max)

    def _group_expr(self, by):
        ok = {"site": "site", "serial": "serial", "temp": "temp"}
        ok.update({f"d{d['id']}": f"COALESCE(d{d['id']}, '-')" for d in self.dims})
        cols = [ok[k] for k in (by or []) if k in ok]
        if not cols:
            return "site"
        return " || ' / ' || ".join(f"CAST({c} AS VARCHAR)" for c in cols)

    def query(self, spec, maxpts=4000, by=None):
        """Filtered, min/max-envelope-decimated points, split into traces by the `by` keys. Same result
        shape as the v2 viewer's scatter(): {traces, n_total, n_returned, groups[, too_many]}."""
        spec = self._spec(spec)
        w, args = spec.to_sql(self.dim_ids())
        g = self._group_expr(by)
        counts = self._all(f"SELECT {g}, count(*) FROM pts WHERE {w} GROUP BY 1 ORDER BY 1", args)
        if len(counts) > MAX_GROUPS:
            return {"traces": [], "n_total": int(sum(c for _, c in counts)), "n_returned": 0,
                    "groups": {}, "too_many": len(counts)}
        n_total = int(sum(c for _, c in counts))
        nb = max(1, maxpts // 2)
        flo, fhi = spec.lo, spec.hi
        if not (flo > float("-inf") and fhi < float("inf")):
            flo, fhi = self.x_min, self.x_max
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
            traces.append({"site": str(site), "x": [r[0] for r in rows], "y": [r[1] for r in rows], "n": int(n),
                           "gi": [r[2] for r in rows], "sn": [r[3] for r in rows], "tp": [r[4] for r in rows]})
        used = sorted({gid for t in traces for gid in t["gi"] if gid is not None})
        groups = {int(i): self.index.hover(i) for i in used} if self.index else {}
        return {"traces": traces, "n_total": n_total, "n_returned": sum(len(t["x"]) for t in traces),
                "groups": groups}

    def verdicts(self, spec):
        """Pass/fail tally and where the limit came from, over the filtered rows (the effective limit's
        reason is recorded per row -- 'data' or 'override')."""
        w, a = self._spec(spec).to_sql(self.dim_ids())
        rows = self._all(f"SELECT limit_src, fail, count(*) FROM pts WHERE {w} GROUP BY 1, 2", a)
        out = {"data": {"pass": 0, "fail": 0}, "override": {"pass": 0, "fail": 0}}
        for src, fail, n in rows:
            out[src]["fail" if fail else "pass"] += int(n)
        return out
