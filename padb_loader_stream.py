"""padb_loader_stream.py -- convert a padbloader parquet of ANY size, a slice of rows at a time.

The in-memory conversion (`padb_loader_adapter.convert`) reads the whole file into pandas: ~3.7 KB of memory per row,
so a few-million-row pull needs tens of GB (measured; see padb_sizeguard). This module does the same job with bounded
memory in two passes:

  PASS 1 (facts)  DuckDB aggregates over the parquet, out of core, answer the questions the conversion asks of a WHOLE
                  group: which condition columns are kept (cardinality cap), which column is the swept x, the units, the
                  main result, how many test temperatures ... (`_primitives`, the same dict `_primitives_from_df` builds
                  from a frame, so everything derived from it is shared code);
  PASS 2 (rows)   the file is read in batches of rows; each batch goes through the SAME per-row builder
                  (`padb_loader_adapter._build_output`) and is appended to that group's output file.

Because every step in the builder is per row and the whole-group decisions come from pass 1, a chunk of rows yields
exactly the rows the whole group would: `qa/qa_bigdata.py` proves the output equals the in-memory path's, cell for cell.
A row RANGE is safe for this (it is a reformat); it is NOT safe for statistics -- those need whole (condition x
frequency) groups, which is what slicing by site / serial / temperature or by x-band is for.

Slicing (`slice_`): {'servers': [...], 'serials': [...] | N, 'steps': [...]} converts only that part of the data.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import padb_loader_adapter as ad

BATCH_ROWS = 400_000
AGG_BATCH = 8                  # aggregates per query: ~65 at once need far more memory than a few at a time


class StreamMemoryError(MemoryError):
    """DuckDB ran out of the memory it was allowed. str() says what to do."""


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _id(c: str) -> str:
    return '"' + c.replace('"', '""') + '"'


def _is_float(t) -> bool:
    return pa.types.is_floating(t)


class _Src:
    """A DuckDB view over the parquet with the file row number and the datum, plus SQL helpers that mirror pandas' notion
    of 'present' (non-null AND, for floats, non-NaN)."""

    def __init__(self, parquet, tmp_dir=None):
        import duckdb
        self.parquet = str(parquet)
        self.schema = pq.ParquetFile(self.parquet).schema_arrow
        self.names = list(self.schema.names)
        self.types = {n: self.schema.field(n).type for n in self.names}
        self.con = duckdb.connect()
        try:
            import padb_sizeguard
            avail = padb_sizeguard.memory_status()[1]
        except Exception:  # noqa: BLE001
            avail = None
        lim = max(1.5, min(6.0, 0.30 * (avail / 1e9))) if avail else 2.0
        try:
            lim = float(os.environ.get("PADB_STREAM_MEM_GB", lim))      # explicit budget (also how the tests squeeze it)
        except ValueError:
            pass
        self.mem_gb = lim
        self.con.execute("SET memory_limit='%.2fGB'" % lim)
        self.con.execute("SET threads=%d" % max(1, min(4, os.cpu_count() or 2)))
        self.con.execute("SET preserve_insertion_order=false")           # every query orders by file_row_number itself
        self.tmp = tmp_dir or tempfile.mkdtemp(prefix="padb_duck_")
        self._own_tmp = tmp_dir is None
        os.makedirs(self.tmp, exist_ok=True)
        self.con.execute("SET temp_directory=%s" % _q(self.tmp))
        dat = ("COALESCE(NULLIF(CAST(%s AS VARCHAR), ''), algorithm)" % _id("result")) if "result" in self.names else "CAST(algorithm AS VARCHAR)"
        self.con.execute("CREATE TEMP VIEW src AS SELECT *, %s AS _datum FROM read_parquet(%s, file_row_number=true)"
                         % (dat, _q(self.parquet)))

    def present(self, c) -> str:
        if _is_float(self.types[c]):
            return "(%s IS NOT NULL AND NOT isnan(%s))" % (_id(c), _id(c))
        return "(%s IS NOT NULL)" % _id(c)

    def one(self, sql, params=None):
        import duckdb
        try:
            return self.con.execute(sql, params or []).fetchall()
        except duckdb.OutOfMemoryException as exc:
            raise StreamMemoryError(
                "Not enough memory to read %s out of core: the analysis engine was allowed %.1f GB and ran out (this machine "
                "has little memory free right now). Close other programs and retry, or give it more with the environment "
                "variable PADB_STREAM_MEM_GB (e.g. 4). Detail: %s" % (os.path.basename(self.parquet), self.mem_gb,
                                                                       str(exc).splitlines()[0][:140])) from None

    def close(self):
        try:
            self.con.close()
        finally:
            if self._own_tmp:
                shutil.rmtree(self.tmp, ignore_errors=True)


def _filters(src: _Src, algorithms, algo_filter, slice_names, serial_list, step_raw):
    """-> (sql WHERE text, params) for the algorithm / substring / slice filters (NOT the value-present test)."""
    where, params = ["TRUE"], []
    if algorithms:
        where.append("_datum IN (%s)" % ",".join("?" * len(algorithms)))
        params += [str(a) for a in algorithms]
    if algo_filter:
        where.append("(contains(lower(_datum), lower(?)) OR contains(lower(CAST(algorithm AS VARCHAR)), lower(?)))")
        params += [algo_filter, algo_filter]
    sl = slice_names or {}
    if sl.get("algorithms"):
        where.append("CAST(algorithm AS VARCHAR) IN (%s)" % ",".join("?" * len(sl["algorithms"])))
        params += list(sl["algorithms"])
    if sl.get("servers"):
        where.append("(" + " OR ".join("contains(lower(CAST(server AS VARCHAR)), ?)" for _ in sl["servers"]) + ")")
        params += [s.lower() for s in sl["servers"]]
    if serial_list is not None:
        where.append("CAST(serial AS VARCHAR) IN (%s)" % (",".join("?" * len(serial_list)) if serial_list else "NULL"))
        params += list(serial_list)
    if step_raw is not None:
        parts = []
        vals = [v for v in step_raw if v is not None]
        if vals:
            parts.append("CAST(step AS VARCHAR) IN (%s)" % ",".join("?" * len(vals)))
            params += [str(v) for v in vals]
        if None in step_raw:
            parts.append("step IS NULL")
        where.append("(" + (" OR ".join(parts) if parts else "FALSE") + ")")
    return " AND ".join(where), params


def _resolve_slice(src: _Src, algorithms, algo_filter, slice_):
    """Turn a slice request into explicit lists: (normalised slice, serial list or None, raw step values or None)."""
    sl = ad._slice_names(slice_)
    if not sl:
        return None, None, None
    base_where, base_params = _filters(src, algorithms, algo_filter, None, None, None)
    serial_list = None
    if sl.get("serials") is not None:
        if isinstance(sl["serials"], int):
            serial_list = [r[0] for r in src.one(
                "SELECT DISTINCT CAST(serial AS VARCHAR) s FROM src WHERE %s AND serial IS NOT NULL ORDER BY s LIMIT %d"
                % (base_where, sl["serials"]), base_params)]
        else:
            serial_list = list(sl["serials"])
    step_raw = None
    if sl.get("steps"):
        want = set(sl["steps"])
        raws = [r[0] for r in src.one("SELECT DISTINCT step FROM src WHERE %s" % base_where, base_params)]
        step_raw = [r for r in raws if (ad._norm_step(r) or str(r)) in want]
    return sl, serial_list, step_raw


def _agg(src, exprs, where, valid, group_sql, params):
    """Run the aggregate expressions a few at a time (see AGG_BATCH) and return their values in order."""
    out = []
    for i in range(0, len(exprs), AGG_BATCH):
        chunk = exprs[i:i + AGG_BATCH]
        out += list(src.one("SELECT %s FROM src WHERE %s AND %s AND %s" % (",".join(chunk), where, valid, group_sql), params)[0])
    return out


def _distinct_present(src, c, where):
    return "count(DISTINCT CASE WHEN %s THEN %s END)" % (src.present(c), _id(c))


def _primitives(src: _Src, group_sql, gparams, where, params, x_col, all_where, all_params):
    """The duckdb twin of ad._primitives_from_df for one group (rows with a value, matching the filters)."""
    valid = src.present("value")
    cols = src.names
    c_cols = [c for c in cols if c.startswith("c_")]
    sel = ["count(*)", "count(DISTINCT serial)",
           "arg_min(units, file_row_number) FILTER (WHERE units IS NOT NULL)" if "units" in cols else "NULL"]
    for c in c_cols:
        sel += ["count(CASE WHEN %s THEN 1 END)" % src.present(c), _distinct_present(src, c, where)]
    run_cols = [c for c in ad._RUN_KEEP if c in cols]
    for c in run_cols:
        sel.append("count(CASE WHEN %s THEN 1 END)" % src.present(c))
    xs = [c for c in ("c_frequency", "c_frequency_offset") if c in cols]
    extra = x_col if (x_col and x_col in cols and x_col not in c_cols) else None
    if extra:
        xs.append(extra)
    extra_sel = [_distinct_present(src, c, where) for c in xs if c not in c_cols]
    sel += extra_sel
    lim_cols = [c for c, _d in ad.CARRY_COLS if c in cols]
    for c in lim_cols:
        sel.append("count(CASE WHEN %s THEN 1 END)" % src.present(c))
    row = _agg(src, sel, where, valid, group_sql, params + gparams)
    it = iter(row)
    n, n_serials, units_first = next(it), next(it), next(it)
    c_stats = {}
    for c in c_cols:
        c_stats[c] = (int(next(it)), int(next(it)))
    run_nonnull = {c: int(next(it)) for c in run_cols}
    extra_d = {c: int(next(it)) for c in xs if c not in c_cols}
    lim_nn = {c: int(next(it)) for c in lim_cols}
    x_distinct = {c: c_stats[c][1] for c in xs if c in c_stats}
    x_distinct.update(extra_d)
    # per datum: units (first non-null in file order), rows, first appearance
    dat = src.one("SELECT _datum, count(*), min(file_row_number), %s FROM src WHERE %s AND %s AND %s GROUP BY _datum"
                  % ("arg_min(units, file_row_number) FILTER (WHERE units IS NOT NULL)" if "units" in cols else "NULL",
                     where, valid, group_sql), params + gparams)
    dat_units = {str(d): (str(u) if u is not None else ad._units_from_label(str(d))) for d, _n, _f, u in dat}
    main = sorted(dat, key=lambda r: (-r[1], r[2]))[0][0]
    steps = None
    if "step" in cols:
        sv = [r[0] for r in src.one("SELECT DISTINCT step FROM src WHERE %s AND %s AND %s" % (where, valid, group_sql), params + gparams)]
        if any(v is not None for v in sv):
            steps = sv
    dev = src.one("SELECT arg_min(device, file_row_number) FROM src WHERE %s" % all_where, all_params)[0][0]
    base = src.one("SELECT arg_min(CAST(algorithm AS VARCHAR), file_row_number) FROM src WHERE %s AND %s AND %s"
                   % (where, valid, group_sql), params + gparams)[0][0]
    return {"base_algo": str(base), "n_rows": int(n), "n_serials": int(n_serials), "device0": str(dev), "columns": cols,
            "dat_units": dat_units, "main": str(main), "units_first": (str(units_first) if units_first is not None else None),
            "c_stats": c_stats, "run_nonnull": run_nonnull, "x_distinct": x_distinct, "steps": steps, "limit_nonnull": lim_nn}


def list_algorithms_duckdb(parquet, x_col=None):
    """The duckdb twin of ad.list_algorithms (any size, bounded memory)."""
    src = _Src(parquet)
    try:
        cols = src.names
        valid = src.present("value")
        xs = [c for c in ("c_frequency", "c_frequency_offset") if c in cols]
        if x_col and x_col in cols and x_col not in xs:
            xs.append(x_col)
        sel = ["count(*)", "arg_min(CAST(algorithm AS VARCHAR), file_row_number)",
               "arg_min(units, file_row_number) FILTER (WHERE units IS NOT NULL)" if "units" in cols else "NULL"]
        sel += [_distinct_present(src, c, "TRUE") for c in xs]
        lim = [c for c in ("limit_upper", "limit_lower") if c in cols]
        sel += ["count(CASE WHEN %s THEN 1 END)" % src.present(c) for c in lim]
        rows = src.one("SELECT _datum, %s FROM src WHERE %s GROUP BY _datum" % (",".join(sel), valid))
        out = []
        for r in sorted(rows, key=lambda r: str(r[0])):
            datum, n, base, units = str(r[0]), int(r[1]), str(r[2]), r[3]
            xd = {c: int(v) for c, v in zip(xs, r[4:4 + len(xs)])}
            lim_n = r[4 + len(xs):]
            if x_col:
                xc = x_col
            else:
                xc = "c_frequency_offset" if xd.get("c_frequency_offset", 0) > 1 else "c_frequency"
            n_x = int(xd.get(xc, 0)) if xc in cols else 0
            if "step" in cols:
                sv = [x[0] for x in src.one("SELECT DISTINCT step FROM src WHERE %s AND _datum = ?" % valid, [datum])]
                n_steps = ad._step_summary(pd.Series(sv, dtype=object))[1]
            else:
                n_steps = 1
            out.append({"algorithm": datum, "base": base, "n_steps": max(n_steps, 1), "rows": n,
                        "units": str(units) if units is not None else ad._units_from_label(datum),
                        "has_limits": any(int(v) > 0 for v in lim_n), "x_col": xc, "n_x": n_x, "wide": n_x > ad.WIDE_X})
        return out
    finally:
        src.close()


def plan_x_bands(parquet, max_rows, algorithms=None, algo_filter=None, slice_=None, x_col=None, combine_results=True):
    """Cut every algorithm's swept-x values into BANDS of at most `max_rows` rows each, for analysing a file too big to
    build in one piece. A band is an inclusive [lo, hi] on that algorithm's own x column (frequency offset, amplitude,
    Vgg ... whatever it sweeps); an x value is never split across bands, so every (condition, x) group -- and with it every
    per-x statistic -- is whole inside one band. (One x value with more than `max_rows` rows gets a band to itself.)
    -> [{'algorithm', 'x_col', 'rows', 'n_x', 'bands': [{'lo', 'hi', 'rows', 'n_x'}]}]"""
    src = _Src(parquet)
    try:
        sl, serial_list, step_raw = _resolve_slice(src, algorithms, algo_filter, slice_)
        where, params = _filters(src, algorithms, algo_filter, sl, serial_list, step_raw)
        valid = src.present("value")
        keyexpr = "CAST(algorithm AS VARCHAR)" if combine_results else "_datum"
        out = []
        groups = sorted({str(r[0]) for r in src.one("SELECT DISTINCT %s FROM src WHERE %s AND %s" % (keyexpr, where, valid), params)})
        for algo in groups:
            gsql, gparams = "%s = ?" % keyexpr, [algo]
            P = _primitives(src, gsql, gparams, where, params, x_col, where, params)
            xc = x_col or ("c_frequency_offset" if P["x_distinct"].get("c_frequency_offset", 0) > 1 else "c_frequency")
            rows = src.one("SELECT %s, count(*) FROM src WHERE %s AND %s AND %s AND %s GROUP BY 1 ORDER BY 1"
                           % (_id(xc), where, valid, gsql, src.present(xc)), params + gparams)
            bands, cur_lo, cur_hi, cur_rows, cur_n = [], None, None, 0, 0
            for x, n in rows:
                if cur_rows and cur_rows + n > max_rows:
                    bands.append({"lo": cur_lo, "hi": cur_hi, "rows": cur_rows, "n_x": cur_n})
                    cur_lo, cur_rows, cur_n = None, 0, 0
                if cur_lo is None:
                    cur_lo = x
                cur_hi, cur_rows, cur_n = x, cur_rows + int(n), cur_n + 1
            if cur_rows:
                bands.append({"lo": cur_lo, "hi": cur_hi, "rows": cur_rows, "n_x": cur_n})
            out.append({"algorithm": algo, "x_col": xc, "rows": sum(b["rows"] for b in bands), "n_x": len(rows), "bands": bands})
        return out
    finally:
        src.close()


def _arrow_schema(F, xtype):
    fields = [("Analysis Type", pa.string()), ("Model(s)", pa.string()), ("Algorithm -> Result", pa.string()),
              ("Units", pa.string()), ("Group", pa.string()), (F["x_header"], xtype), (F["val_header"], pa.float64()),
              ("Serial Number", pa.string())]
    if F["has_step"]:
        fields.append(("Test Step", pa.string()))
    for _src, dst in F["limit_cols"]:
        fields.append((dst, pa.float64()))
    return pa.schema(fields)


def convert_stream(parquet, out_dir, jobs_dir, x_col=None, algo_filter=None, publish_to=None, use_runtime=True,
                   algorithms=None, fmt="parquet", force=False, log=print, combine_results=True, views=None,
                   slice_=None, batch_rows=BATCH_ROWS):
    """Same contract as padb_loader_adapter.convert, with bounded memory (see the module docstring)."""
    pf = pq.ParquetFile(parquet)
    lab, unit = ad._labels(pf.schema_arrow)
    stem = ad._slug(os.path.splitext(os.path.basename(parquet))[0])
    data_dir = os.path.join(out_dir, "padb")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(jobs_dir, exist_ok=True)
    src = _Src(parquet, tmp_dir=os.path.join(out_dir, ".duckdb_tmp"))
    writers = {}
    made = []
    try:
        sl, serial_list, step_raw = _resolve_slice(src, algorithms, algo_filter, slice_)
        where, params = _filters(src, algorithms, algo_filter, sl, serial_list, step_raw)
        if not src.one("SELECT 1 FROM src WHERE %s LIMIT 1" % where, params):
            raise ValueError("no rows after filtering")
        valid = src.present("value")
        key = "algorithm" if combine_results else "_datum"
        keyexpr = "CAST(algorithm AS VARCHAR)" if combine_results else "_datum"
        groups = sorted({str(r[0]) for r in src.one("SELECT DISTINCT %s FROM src WHERE %s AND %s" % (keyexpr, where, valid), params)})
        plans = []                                   # [(name, F, writer-state)] for the groups we will actually write
        xr = (sl or {}).get("x_range")
        log("  streaming %s: %s rows, %d algorithm(s) to convert" % (os.path.basename(parquet), format(pf.metadata.num_rows, ","), len(groups)))
        for gi, algo in enumerate(groups, 1):
            log("  [%d/%d] reading the facts of %s ..." % (gi, len(groups), algo))
            slug = ad._slug(algo) + (ad._range_tag(xr) if xr else "")
            jp = os.path.join(jobs_dir, "%s_%s_v2_job.json" % (stem, slug))
            gsql, gparams = "%s = ?" % keyexpr, [algo]
            if os.path.exists(jp) and not force:
                n, ns = src.one("SELECT count(*), count(DISTINCT serial) FROM src WHERE %s AND %s AND %s" % (where, valid, gsql),
                                params + gparams)[0]
                made.append({"algorithm": algo, "status": "exists", "rows": int(n), "serials": int(ns), "limits": None,
                             "data_path": None, "job_path": jp, "wide": None,
                             "note": "job file exists (use force/--force to overwrite)"})
                continue
            P = _primitives(src, gsql, gparams, where, params, x_col, where, params)
            F = ad._facts_from_primitives(P, algo, combine_results, x_col, lab, unit, log)
            if xr:
                xc = F["xc"]
                lim = [s for s, _d in F["limit_cols"] if s in ad.LIMIT_SRC]
                sel = ["count(*)", "count(DISTINCT serial)", _distinct_present(src, xc, "TRUE")]
                sel += ["count(CASE WHEN %s THEN 1 END)" % src.present(s) for s in lim]
                band = src.one("SELECT %s FROM src WHERE %s AND %s AND %s AND %s AND %s BETWEEN ? AND ?"
                               % (",".join(sel), where, valid, gsql, src.present(xc), _id(xc)),
                               params + gparams + [xr[0], xr[1]])[0]
                if int(band[0]) == 0:
                    continue                          # nothing of this algorithm in the band
                F = dict(F)
                F.update({"n_rows": int(band[0]), "n_serials": int(band[1]), "n_x": int(band[2]), "x_range": list(xr),
                          "wide": int(band[2]) > ad.WIDE_X, "has_lim": any(int(v) > 0 for v in band[3:])})
            ext = "parquet" if fmt == "parquet" else "csv"
            plans.append({"algo": algo, "slug": slug, "jp": jp, "F": F,
                          "data_path": os.path.join(data_dir, "%s_%s.%s" % (stem, slug, ext)), "rows": 0})
        if plans:
            need = {"algorithm", "result", "value", "serial", "device", "run_status", "run_datetime", "units", "step", "server"}
            for pl in plans:
                F = pl["F"]
                need |= {c for c, _ in F["keys"]} | {F["xc"]} | {s for s, _ in F["limit_cols"]}
            cols = [c for c in src.names if c in need]
            by_key = {pl["algo"]: pl for pl in plans}
            total_rows, seen, nb = max(1, pf.metadata.num_rows), 0, 0
            for batch in pf.iter_batches(batch_size=batch_rows, columns=cols):
                seen += batch.num_rows
                nb += 1
                if nb == 1 or nb % 5 == 0:
                    log("  converting: %s of %s rows read (%d%%)" % (format(seen, ","), format(total_rows, ","), 100 * seen // total_rows))
                df = ad._with_datum(batch.to_pandas())
                if algorithms:
                    df = df[ad._datum_mask(df, algorithms)]
                if algo_filter:
                    df = df[df["_datum"].str.contains(algo_filter, case=False, regex=False)
                            | df["algorithm"].str.contains(algo_filter, case=False, regex=False)]
                if sl:
                    df = ad._slice_df(df, sl, serial_list=serial_list)
                df = df.dropna(subset=["value"])
                if df.empty:
                    continue
                for algo, g in df.groupby(key, sort=False):
                    pl = by_key.get(str(algo))
                    if pl is None or g.empty:
                        continue
                    if xr:
                        xcol = pl["F"]["xc"]
                        g = g[(g[xcol] >= xr[0]) & (g[xcol] <= xr[1])]
                        if g.empty:
                            continue
                    o = ad._build_output(g, pl["F"], use_runtime)
                    for c in o.columns:
                        if str(o[c].dtype) == "category":
                            o[c] = o[c].astype(object)
                    pl["rows"] += len(o)
                    if fmt == "parquet":
                        w = writers.get(pl["algo"])
                        if w is None:
                            xt = src.types.get(pl["F"]["xc"], pa.float64())
                            w = writers[pl["algo"]] = pq.ParquetWriter(pl["data_path"], _arrow_schema(pl["F"], xt),
                                                                       compression="zstd", compression_level=9)
                        w.write_table(pa.Table.from_pandas(o, schema=w.schema, preserve_index=False))
                    else:
                        o.to_csv(pl["data_path"], index=False, quoting=1, mode="a" if pl["rows"] > len(o) else "w",
                                 header=pl["rows"] == len(o))
            for w in writers.values():
                w.close()
            writers.clear()
            for pl in plans:
                if pl["rows"] != pl["F"]["n_rows"]:
                    raise RuntimeError("%s: wrote %d rows but the facts pass counted %d -- the file changed while it was "
                                       "being converted?" % (pl["algo"], pl["rows"], pl["F"]["n_rows"]))
                made.append(ad._write_job(pl["F"], pl["algo"], pl["slug"], pl["jp"], stem, lab, publish_to, views, fmt,
                                          pl["data_path"], pl["rows"], log))
        made.sort(key=lambda m: m["algorithm"])
        return made
    finally:
        for w in writers.values():
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
        src.close()
        shutil.rmtree(os.path.join(out_dir, ".duckdb_tmp"), ignore_errors=True)
