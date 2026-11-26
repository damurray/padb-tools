"""padb_loader_adapter.py -- convert a direct-from-Oracle "padbloader" parquet
(long format: one row per measurement, c_* = conditions, r_* = run attributes)
into PADB-style Type=80 scatter tables + matching *_v2_job.json files, so the
normal padb_v2.py pipeline (all interactive views) can plot them unchanged.

By default each algorithm becomes a PADB-style PARQUET (typed, ~100x smaller than
the CSV, and read directly by padb_v2 through the job's "parquet_path"); --format
csv writes the classic CSV instead.

Usage:
  py padb_loader_adapter.py <file.parquet> --out <results_dir> [--jobs-dir <dir>]
        [--no-publish | --publish-to <path>] [--x-col c_frequency_offset]
        [--algorithm "substring"] [--no-run-datetime] [--format parquet|csv]
        [--force] [--split-results] [--views scatter,boxplot,...] [--list]

  --list   print the algorithms in the file (rows, units, limits, swept x) and exit
  --split-results  one job per RESULT stream; by default an algorithm's results (often in
           different units) share one job with a "Result" filter, the main one pre-selected
  --force  overwrite existing job files (default: keep them, so a hand-tuned
           job -- e.g. an edited "views" -- is never clobbered by a re-run)

Writes <out>/padb/<slug>.parquet|csv per algorithm and
<jobs-dir>/<stem>_<slug>_v2_job.json.  Wide sweeps (more than WIDE_X distinct x
values) get "views": ["scatter","summary"] -- boxplot/stat_summary/reference over
thousands of x values are hours long and hundreds of MB.
ASCII-only output (cp1252 console).
"""
import argparse
import json
import os
import re
import sys

import pandas as pd
import pyarrow.parquet as pq

# Condition columns put into the Group string, in order: (column, label)
# Labels come from the parquet's own label.<col> metadata when present.
_SKIP = {
    "server", "schema", "serial", "device", "device_revision", "family", "suite",
    "step", "centers", "station", "run_id", "run_label", "run_datetime",
    "run_status", "point_seq", "algorithm", "result", "units", "tpt_revision",
    "limit_version", "limit_upper", "limit_nominal", "limit_lower",
    "limit_eff_date", "uncert_upper", "uncert_lower", "cuspec_upper",
    "cuspec_lower", "value", "status",
}
# per-run attributes worth a Group key (low cardinality, useful filters)
_RUN_KEEP = ["r_device_mode", "r_site", "r_process_center", "r_datapoint_set",
             "r_sequencing_mode", "r_test_group", "r_datapack"]

# A per-row identifier (e.g. c_etp_id: ~1 distinct value per 27 rows) in Group makes
# every sweep its own "condition" -> tens of thousands of conditions, unusable
# filters and an impossibly large boxplot.  Skip condition columns above this.
MAX_COND_CARD = 100
# A sweep with more distinct x values than this is "wide": the per-x views
# (boxplot / stat_summary / reference) are inherently huge, so the job defaults to
# scatter + summary.  (Harmonics, 696 x: boxplot 11 min, 70 MB; EFC, 6860 x: far worse.)
WIDE_X = 300
WIDE_VIEWS = ["scatter", "summary"]

_X_CANDIDATES = ("c_frequency_offset", "c_frequency")

# The data's own limit (the test limit, TLL), the datasheet spec ("cuspec") and ONE rolled-up uncertainty (M.U. + DEnv + GB as the
# database stores it; spec = limit + uncertainty, checked exactly on real files).  All six are carried into the PADB-style table
# as columns the loader reads (Spec_Hi/Lo, Unc_Hi/Lo), so the views have Spec, Limit and Uncertainty side by side.
LIMIT_SRC = ("limit_upper", "limit_lower")
CARRY_COLS = (("limit_upper", "Upper Limit"), ("limit_lower", "Lower Limit"),
              ("cuspec_upper", "Upper Spec"), ("cuspec_lower", "Lower Spec"),
              ("uncert_upper", "Upper Uncertainty"), ("uncert_lower", "Lower Uncertainty"))

# Views a job can ask for.  The two ENVIRONMENTAL views need data at more than one test
# step (padbloader `step`: Room / Hot / Cold ...); padb_v2 itself never builds them for
# Room-only data, so convert() drops them when the data has a single step.
ALL_VIEWS = ["scatter", "boxplot", "reference", "summary", "stat_summary",
             "distribution", "env_coverage"]
ENV_VIEWS = ["distribution", "env_coverage"]


def _slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def _labels(schema):
    md = {k.decode(): v.decode() for k, v in (schema.metadata or {}).items()}
    lab, unit = {}, {}
    for k, v in md.items():
        if k.startswith("label."):
            lab[k[6:]] = v
        elif k.startswith("units."):
            unit[k[6:]] = v.strip()
    return lab, unit


def _fmt(v):
    if isinstance(v, float):
        return ("%.10g" % v)
    return str(v)


def _units_from_label(label):
    """'Carrier Power (dBm)' -> 'dBm' (padbloader leaves `units` null on some results)."""
    m = re.search(r"\(([^()]+)\)\s*$", label or "")
    return m.group(1).strip() if m else ""


def _datum_units(g, datum):
    """Units of one datum: its `units` column, else the '(units)' tail of its label."""
    if "units" in g.columns and g["units"].notna().any():
        return str(g["units"].dropna().iloc[0])
    return _units_from_label(datum)


def _with_datum(df):
    """One datum = one measurement stream = (algorithm, result).  An algorithm can emit
    several results in DIFFERENT units (e.g. harmonics: the dBc level plus 'Carrier Power
    (dBm)' and 'Impurity Power (dBm)'); plotting them together mixes dBc with dBm.
    The datum name is the result label (falls back to the algorithm name)."""
    df = df.copy()
    res = df["result"] if "result" in df.columns else pd.Series(index=df.index, dtype=object)
    df["_datum"] = res.where(res.notna() & (res.astype(str) != ""), df["algorithm"]).astype(str)
    return df


def _datum_mask(df, names):
    """Rows whose datum is EXACTLY one of `names` (a base-algorithm name must not drag
    in the algorithm's other results -- ticking the dBc datum must not add the dBm ones)."""
    return df["_datum"].isin(set(names))


def _norm_step(v):
    """padbloader `step` -> the PADB-native "Test Step" text the loader understands:
    'Room' / 'NN.N Deg C'.  Returns None for a label that names no temperature (e.g.
    'Hot'): the loader would silently read that as Room, so callers keep the original
    text and report it.  (NB the loader drops a leading minus -- '-40.0 Deg C' loads as
    40C -- a known loader limitation, deliberately not changed here.)"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "Room"
    t = str(v).strip()
    if not t or re.search(r"\b(room|ambient)\b", t, re.I):
        return "Room"
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:\u00b0|deg)?\s*c\b", t, re.I)
    if m:
        return "%.1f Deg C" % float(m.group(1))
    return None


def _step_summary(steps):
    """(normalised Series, n_effective_temps, unrecognised labels) for a `step` column:
    n counts distinct temperatures AS THE LOADER WILL SEE THEM (unrecognised -> Room)."""
    norm = steps.map(_norm_step)
    bad = sorted({str(x) for x, n in zip(steps, norm) if pd.isna(n)})
    eff = norm.fillna("Room")
    return norm.where(norm.notna(), steps.astype(str)), int(eff.nunique()), bad


def _pick_x(g, x_col=None):
    """The swept-x column: explicit, else Frequency Offset if it varies, else Frequency."""
    if x_col:
        return x_col
    off = g.get("c_frequency_offset", pd.Series(dtype=float))
    return "c_frequency_offset" if off.nunique() > 1 else "c_frequency"


def _nn_pd(s):
    """Non-null, non-NaN mask of a pandas Series (what pandas' notna / nunique treat as present)."""
    return s.notna()


def _primitives_from_df(g, df_all, x_col):
    """The few whole-group facts conversion needs, read from an in-memory group `g` (rows with a value).  The streaming
    path produces the same dict from DuckDB aggregates (padb_loader_stream), so everything built from it is shared."""
    cols = list(g.columns)
    P = {"base_algo": str(g["algorithm"].iloc[0]), "n_rows": int(len(g)),
         "n_serials": int(g["serial"].nunique()), "device0": str(df_all["device"].iloc[0]), "columns": cols,
         "dat_units": {d: _datum_units(gd, d) for d, gd in g.groupby("_datum")},
         "main": str(g["_datum"].value_counts().index[0])}
    P["units_first"] = (str(g["units"].dropna().iloc[0]) if "units" in g.columns and g["units"].notna().any() else None)
    P["c_stats"] = {c: (int(g[c].notna().sum()), int(g[c].nunique())) for c in cols if c.startswith("c_")}
    P["run_nonnull"] = {c: int(g[c].notna().sum()) for c in _RUN_KEEP if c in cols}
    extra = x_col if (x_col and x_col in cols and x_col not in P["c_stats"]) else None
    P["x_distinct"] = {c: int(g[c].nunique()) for c in set(list(P["c_stats"]) + ([extra] if extra else []))
                       if c in ("c_frequency", "c_frequency_offset") or c == extra}
    P["steps"] = (list(g["step"].drop_duplicates()) if "step" in cols and g["step"].notna().any() else None)
    P["limit_nonnull"] = {c: int(g[c].notna().sum()) for c, _d in CARRY_COLS if c in cols}
    return P


def _facts_from_primitives(P, algo, combine_results, x_col, lab, unit, log):
    """Everything the output builder and the job writer need, from the primitives (shared by both paths)."""
    dat_units = P["dat_units"]
    multi = combine_results and len(dat_units) > 1
    F = {"algo": algo, "base_algo": P["base_algo"], "multi": multi, "dat_units": dat_units,
         "n_rows": P["n_rows"], "n_serials": P["n_serials"], "device0": P["device0"]}
    if multi:
        def _rlabel(d, u=None):
            u = dat_units[d] if u is None else u
            return d if (not u or _units_from_label(d) == u) else "%s (%s)" % (d, u)
        F["rlabel"] = {d: _rlabel(d) for d in dat_units}
        F["runits"] = dict(dat_units)
        main = algo if algo in dat_units else P["main"]
        F["result_default"] = _rlabel(main)
    units = P["units_first"] if P["units_first"] is not None else _units_from_label(algo)
    F["units"] = "" if multi else units
    if x_col:
        xc = x_col
    else:
        xc = "c_frequency_offset" if P["x_distinct"].get("c_frequency_offset", 0) > 1 else "c_frequency"
    F["xc"] = xc
    xlab = lab.get(xc, xc)
    xu = unit.get(xc, "")
    F["x_header"] = "%s (%s)" % (xlab, xu) if xu else xlab
    F["xu"] = xu
    F["val_header"] = ("%s (%s)" % (algo, F["units"])
                       if F["units"] and _units_from_label(algo) != F["units"] else algo)
    ccols = []
    for c in P["columns"]:
        if not c.startswith("c_") or c == xc or P["c_stats"].get(c, (0, 0))[0] == 0:
            continue
        if P["c_stats"][c][1] > MAX_COND_CARD:
            log("  note: %s dropped from Group (%d distinct values > %d)" % (c, P["c_stats"][c][1], MAX_COND_CARD))
            continue
        ccols.append(c)
    rcols = [c for c in _RUN_KEEP if P["run_nonnull"].get(c, 0) > 0]
    F["keys"] = [(c, "%s (%s)" % (lab.get(c, c), unit.get(c, "")) if unit.get(c, "") else lab.get(c, c))
                 for c in ccols + rcols]
    if P["steps"] is not None:
        _norm, F["n_steps"], F["bad_steps"] = _step_summary(pd.Series(P["steps"], dtype=object))
        F["has_step"] = True
    else:
        F["n_steps"], F["bad_steps"], F["has_step"] = 1, [], False
    F["limit_cols"] = [(src, dst) for src, dst in CARRY_COLS if P["limit_nonnull"].get(src, 0) > 0]
    F["has_lim"] = any(s in LIMIT_SRC for s, _d in F["limit_cols"])           # "has limits" means the TEST limit, not spec / uncertainty
    F["has_unc"] = any(s.startswith("uncert") for s, _d in F["limit_cols"])
    F["n_x"] = int(P["x_distinct"].get(xc, 0))
    F["wide"] = F["n_x"] > WIDE_X
    return F


def _build_output(g, F, use_runtime):
    """The PADB-style table for rows `g` (any subset of one group's valid rows -- every step is per row, the
    whole-group decisions are in the facts F), so a chunk gives exactly the rows the whole group would."""
    g = g.copy()
    multi = F["multi"]
    if multi:
        g["_rlabel"] = g["_datum"].map(F["rlabel"])
        g["_runits"] = g["_datum"].map(F["runits"])
    grp = pd.Series("", index=g.index)
    if multi:
        # the Result dimension first: the views' condition filter selects the result.
        # (Group is split on 2+ spaces; labels have single spaces only)
        grp = "Result: " + g["_rlabel"].str.replace(r"\s{2,}", " ", regex=True) + "  "
    for c, l in F["keys"]:
        col = g[c]
        txt = col.map(lambda v: "" if pd.isna(v) else _fmt(v))
        # Group is split on 2+ spaces; values with 2+ spaces would corrupt it
        txt = txt.str.replace(r"\s{2,}", " ", regex=True)
        part = (l + ": " + txt)
        part[txt == ""] = ""
        grp = grp + part + "  "
    grp = grp + "Test Event Status: " + g["run_status"].astype(str)
    if use_runtime:
        grp = grp + "  Test Run Datetime: " + g["run_datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    grp = grp.str.replace(r"\s{3,}", "  ", regex=True)
    units, algo, base_algo = F["units"], F["algo"], F["base_algo"]
    o = pd.DataFrame({
        "Analysis Type": "ScatterPlot",
        "Model(s)": g["device"],
        "Algorithm -> Result": (base_algo + "-->" + g["_rlabel"]) if multi else
                               (base_algo + "-->" + g["result"].fillna(algo) + (" (%s)" % units if units else "")),
        "Units": g["_runits"] if multi else units,
        "Group": grp,
        F["x_header"]: g[F["xc"]],
        F["val_header"]: g["value"],
        "Serial Number": g["serial"],
    })
    if F["has_step"]:
        # the PADB "Test Step" column: the loader turns it into Temperature, which is
        # what makes a multi-step (hot/cold) file offer the environmental views
        o["Test Step"] = _step_summary(g["step"])[0]
    for src, dst in F["limit_cols"]:
        o[dst] = g[src]
    return o


def _range_tag(xr):
    """File / job name suffix for an x-range slice: _x<lo>_<hi>."""
    return "_x%s_%s" % (_slug(_fmt(float(xr[0]))), _slug(_fmt(float(xr[1]))))


def _facts_apply_range(F, g, xr):
    """Counts of a band (rows of one group inside the x-range); the column decisions stay those of the WHOLE group,
    so every band of one dataset has the same columns and filters."""
    F = dict(F)
    F["n_rows"] = int(len(g))
    F["n_serials"] = int(g["serial"].nunique())
    F["n_x"] = int(g[F["xc"]].nunique())
    F["wide"] = F["n_x"] > WIDE_X
    F["has_lim"] = any(bool(g[s].notna().any()) for s, _d in F["limit_cols"] if s in LIMIT_SRC)
    F["x_range"] = [float(xr[0]), float(xr[1])]
    return F


def _slice_names(sl):
    """Normalise a slice request. -> {'servers': [...]|None, 'serials': [...]|int|None, 'steps': [...]|None,
    'x_range': [lo, hi]|None} or None.  `x_range` is on each algorithm's OWN swept x column (frequency offset, amplitude,
    Vgg ... -- never assumed to be frequency), in the units stored in the file, ends inclusive."""
    if not sl:
        return None
    out = {}
    xr = sl.get("x_range")
    if xr not in (None, [], ()):
        if len(xr) != 2 or float(xr[0]) > float(xr[1]):
            raise ValueError("x_range must be [lo, hi] with lo <= hi")
        out["x_range"] = [float(xr[0]), float(xr[1])]
    for k in ("algorithms", "servers", "serials", "steps"):
        v = sl.get(k)
        if v in (None, [], ""):
            continue
        if k == "serials" and isinstance(v, int):
            out[k] = int(v)
        else:
            out[k] = [str(x) for x in (v if isinstance(v, (list, tuple)) else [v])]
    return out or None


def _slice_df(df, sl, serial_list=None):
    """Apply a slice to an in-memory frame (the streaming path applies the same rules in SQL)."""
    sl = _slice_names(sl)
    if not sl:
        return df
    m = pd.Series(True, index=df.index)
    if sl.get("algorithms"):                    # EXACT algorithm names (algo_filter is a substring and would over-match)
        m &= df["algorithm"].astype(str).isin(set(sl["algorithms"]))
    if sl.get("servers"):
        low = [s.lower() for s in sl["servers"]]
        m &= df["server"].astype(str).str.lower().map(lambda v: any(s in v for s in low))
    if sl.get("serials") is not None:
        wanted = serial_list if serial_list is not None else (
            sorted(df["serial"].dropna().astype(str).unique())[:sl["serials"]] if isinstance(sl["serials"], int) else sl["serials"])
        m &= df["serial"].astype(str).isin(set(wanted))
    if sl.get("steps"):
        want = set(sl["steps"])
        norm = df["step"].map(lambda v: _norm_step(v) or str(v))
        m &= norm.isin(want)
    return df[m]


def list_algorithms(parquet, x_col=None, engine="auto"):
    """Summarise the algorithms in a padbloader parquet.  Returns a list of dicts
    {algorithm, rows, units, has_limits, x_col, n_x, wide}.  engine 'pandas' reads a handful of columns
    into memory; 'duckdb' aggregates out of core (any size); 'auto' picks by the memory the pandas read needs."""
    pf = pq.ParquetFile(parquet)
    names = set(pf.schema_arrow.names)
    if "algorithm" not in names:
        raise ValueError("not a padbloader parquet (no 'algorithm' column)")
    if engine == "auto":
        import padb_sizeguard
        fp = padb_sizeguard.footprint(parquet)
        engine = "duckdb" if (fp["rows"] > 2_000_000 or not padb_sizeguard.assess(parquet, "convert")["ok"]) else "pandas"
    if engine == "duckdb":
        import padb_loader_stream
        return padb_loader_stream.list_algorithms_duckdb(parquet, x_col)
    want = [c for c in ("algorithm", "result", "value", "units", "limit_upper", "limit_lower",
                        "c_frequency", "c_frequency_offset", "step") if c in names]
    df = _with_datum(pf.read(columns=want).to_pandas())
    out = []
    for algo, g in df.groupby("_datum", sort=True):
        g = g.dropna(subset=["value"]) if "value" in g.columns else g
        if g.empty:
            continue
        xc = _pick_x(g, x_col)
        n_x = int(g[xc].nunique()) if xc in g.columns else 0
        units = _datum_units(g, algo)
        has_lim = any(c in g.columns and g[c].notna().any() for c in ("limit_upper", "limit_lower"))
        n_steps = _step_summary(g["step"])[1] if "step" in g.columns else 1
        out.append({"algorithm": str(algo), "base": str(g["algorithm"].iloc[0]),
                    "n_steps": max(n_steps, 1),
                    "rows": int(len(g)), "units": units,
                    "has_limits": bool(has_lim), "x_col": xc, "n_x": n_x,
                    "wide": n_x > WIDE_X})
    return out


def _write_job(F, algo, slug, jp, stem, lab, publish_to, views, fmt, data_path, n_out_rows, log):
    """Write the v2 job file for one converted group and return its result dict (shared by both paths)."""
    device = F["device0"]
    wide, n_steps, bad_steps = F["wide"], F["n_steps"], F["bad_steps"]
    job = {
        "description": "%s %s (padbloader parquet)" % (device, algo),
        "title_prefix": "%s %s" % (device, algo),
        "y_label": F["val_header"],
        ("parquet_path" if fmt == "parquet" else "csv_path"): os.path.abspath(data_path),
        "results_dir": "%s_%s_v2_results" % (stem, slug),
        "index_title": "%s %s" % (device, stem),
        "spec_direction": "auto",
        "x_col": F["x_header"],
        "x_label": F["x_header"],
        "x_unit": F["xu"],
    }
    if F.get("has_unc"):
        # the stored limit already has the (rolled-up, M.U.+DEnv+GB) uncertainty taken off: the Statistical Summary must not
        # subtract M.U. / DEnv again.  "unknown" = apply none of its own; the live "Limit already includes" control can change it.
        job["uncertainty_includes"] = "unknown"
    note_extra = ""
    if bad_steps:
        note_extra += ("  [WARNING: test step(s) %s name no temperature; the loader reads them "
                       "as Room]" % ", ".join(bad_steps))
    if F["n_x"] <= 1:
        # a test with no swept x (one value per unit: a voltage, a resistance, a pass/fail, a time): every x-based view
        # would fail ("no plottable data"), the value distribution is what there is to look at
        job["views"] = ["histogram"]
        note_extra += "  [no swept x -> histogram view]"
    elif views is not None:
        vv = [v for v in views if n_steps > 1 or v not in ENV_VIEWS]
        if len(vv) != len(views):
            note_extra += "  [environmental views dropped: data has a single test temperature]"
        job["views"] = vv
    elif wide:
        job["views"] = list(WIDE_VIEWS)
    if F.get("x_range"):
        job["description"] += " [x %s..%s]" % (_fmt(F["x_range"][0]), _fmt(F["x_range"][1]))
        job["index_title"] += " [x %s..%s]" % (_fmt(F["x_range"][0]), _fmt(F["x_range"][1]))
    if F["multi"]:
        job["result_default"] = F["result_default"]
        job["description"] += " -- %d results (pick with the Result filter)" % len(F["dat_units"])
    if publish_to is not None:
        # "{algo}" in publish_to expands to this algorithm's slug, so several
        # algorithms from one parquet never publish into the same folder
        job["publish_to"] = publish_to.replace("{algo}", slug)
    with open(jp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(job, fh, indent=2)
    return {"algorithm": algo, "status": "ok", "rows": n_out_rows,
            "serials": F["n_serials"], "limits": F["has_lim"],
            "data_path": data_path, "job_path": jp, "wide": wide,
            "note": (("wide sweep (%d x values) -> views %s" % (F["n_x"], WIDE_VIEWS))
                     if wide and views is None else "") + note_extra}


def convert(parquet, out_dir, jobs_dir, x_col=None, algo_filter=None,
            publish_to=None, use_runtime=True, algorithms=None, fmt="parquet",
            force=False, log=print, combine_results=True, views=None, stream="auto", slice_=None):
    """Convert a padbloader parquet.  `algorithms` = exact datum (result) names to
    convert (None = all, narrowed by the `algo_filter` substring if given).

    combine_results=True (default): ONE job per algorithm; its results (possibly in
    different units, e.g. the dBc level plus 'Carrier Power (dBm)') share one dataset
    with a leading "Result: <label (units)>" Group key, so the views' normal condition
    filter selects the result, and the job's "result_default" pre-selects the main one
    (so units are never mixed on one axis until the user ticks another).
    combine_results=False: one job per result stream (the datum).

    views: explicit list of views to build (any of ALL_VIEWS) written to each job's
    "views"; the environmental views are dropped for a job whose data has a single test
    step.  None = the default (a wide sweep gets scatter+summary, others let padb_v2
    auto-select).

    stream: 'auto' (default) converts in memory when the file fits and STREAMS it (a slice of rows at a time, with
    the whole-group decisions computed first by DuckDB) when it would not; 'always' / 'off' force one or the other.
    Both paths share the same per-row builder, so the output is the same either way (qa/qa_bigdata proves it).
    slice_: {'algorithms': [...exact...], 'servers': [...], 'serials': [...] | N, 'steps': [...], 'x_range': [lo, hi]} -- convert only that part
    of the data (x_range is on each algorithm's own swept x, in the stored units).

    Returns a list of dicts, one per job: {algorithm, status, rows, serials, limits,
    data_path, job_path, wide, note}; status is 'ok' or 'exists' (job file already
    present and force is False -- left untouched)."""
    if fmt not in ("parquet", "csv"):
        raise ValueError("fmt must be 'parquet' or 'csv'")
    if stream not in ("auto", "always", "off"):
        raise ValueError("stream must be 'auto', 'always' or 'off'")
    if views is not None:
        bad = [v for v in views if v not in ALL_VIEWS]
        if bad or not views:
            raise ValueError("views must be a non-empty list of %s (got %s)" % (ALL_VIEWS, views))
    use_stream = stream == "always"
    if stream == "auto":
        import padb_sizeguard
        a = padb_sizeguard.assess(parquet, "convert")
        use_stream = not a["ok"] or a["level"] == "warn"
        if use_stream:
            log("  note: %s -- converting by streaming, a slice of rows at a time" % a["message"].split(" -- ")[0])
    if use_stream:
        import padb_loader_stream
        return padb_loader_stream.convert_stream(parquet, out_dir, jobs_dir, x_col, algo_filter, publish_to,
                                                 use_runtime, algorithms, fmt, force, log, combine_results, views, slice_)
    if stream == "off":
        import padb_sizeguard
        padb_sizeguard.require(parquet, "convert")        # the user forced the in-memory path: still refuse if it cannot fit
    pf = pq.ParquetFile(parquet)
    lab, unit = _labels(pf.schema_arrow)
    log("  reading %s (%s rows) into memory ..." % (os.path.basename(parquet), format(pf.metadata.num_rows, ",")))
    df = _with_datum(pf.read().to_pandas())
    if algorithms:
        df = df[_datum_mask(df, algorithms)]
    if algo_filter:
        df = df[df["_datum"].str.contains(algo_filter, case=False, regex=False)
                | df["algorithm"].str.contains(algo_filter, case=False, regex=False)]
    if slice_:
        df = _slice_df(df, slice_)
    if df.empty:
        raise ValueError("no rows after filtering")
    xr = (_slice_names(slice_) or {}).get("x_range")
    stem = _slug(os.path.splitext(os.path.basename(parquet))[0])
    data_dir = os.path.join(out_dir, "padb")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(jobs_dir, exist_ok=True)
    made = []
    _groups = list(df.groupby("algorithm" if combine_results else "_datum", sort=True))
    for _gi, (algo, g) in enumerate(_groups, 1):
        g = g.dropna(subset=["value"]).copy()
        if g.empty:
            continue
        log("  [%d/%d] building %s (%s rows) ..." % (_gi, len(_groups), algo, format(len(g), ",")))
        slug = _slug(algo) + (_range_tag(xr) if xr else "")
        jp = os.path.join(jobs_dir, "%s_%s_v2_job.json" % (stem, slug))
        if os.path.exists(jp) and not force:
            made.append({"algorithm": algo, "status": "exists", "rows": len(g),
                         "serials": int(g["serial"].nunique()), "limits": None,
                         "data_path": None, "job_path": jp, "wide": None,
                         "note": "job file exists (use force/--force to overwrite)"})
            continue
        P = _primitives_from_df(g, df, x_col)
        F = _facts_from_primitives(P, str(algo), combine_results, x_col, lab, unit, log)
        if xr:
            g = g[(g[F["xc"]] >= xr[0]) & (g[F["xc"]] <= xr[1])]
            if g.empty:
                continue                       # this algorithm has nothing in the band
            F = _facts_apply_range(F, g, xr)
        o = _build_output(g, F, use_runtime)
        ext = "parquet" if fmt == "parquet" else "csv"
        data_path = os.path.join(data_dir, "%s_%s.%s" % (stem, slug, ext))
        if fmt == "parquet":
            o.to_parquet(data_path, compression="zstd", compression_level=9, index=False)
        else:
            o.to_csv(data_path, index=False, quoting=1)
        made.append(_write_job(F, str(algo), slug, jp, stem, lab, publish_to, views, fmt, data_path, len(o), log))
    return made


# ---------------------------------------------------------------------------
# padb-tools' OWN parquet (the sidecar written for large datasets, or by
# padb_csv_to_parquet.py): the same columns as a PADB Type=80 scatter CSV.  It needs no
# conversion -- a job just points at it ("parquet_path", used in place).
# ---------------------------------------------------------------------------
def sniff_parquet(path):
    """'padbloader' (long format: algorithm + value columns), 'padb' (PADB scatter-CSV
    columns: a Group column and a Frequency / X value column), or None."""
    names = pq.ParquetFile(path).schema_arrow.names
    low = [n.lower() for n in names]
    if "algorithm" in names and "value" in names:
        return "padbloader"
    if "group" in low and any(("frequency" in n or "x value" in n) for n in low):
        return "padb"
    return None


def _padb_cols(names, x_col=None):
    """Column roles in a PADB scatter table, by the same keyword rules the loader uses."""
    low = {n.lower(): n for n in names}
    freq = x_col if x_col in names else next(
        (low[k] for k in low if "frequency" in k or "x value" in k), None)
    serial = next((low[k] for k in low if any(w in k for w in ("serial num", "serial no", "sn", "unit id", "dut id"))
                   and "station" not in k), None)
    if serial is None:
        serial = next((low[k] for k in low if k == "serial" or (k.startswith("serial") and "station" not in k)), None)
    station = next((low[k] for k in low if "station" in k), None)
    lo = next((low[k] for k in low if "lower limit" in k), None)
    hi = next((low[k] for k in low if "upper limit" in k), None)
    group = next((low[k] for k in low if k == "group"), None)
    skip = {c.lower() for c in (freq, serial, station, lo, hi, group) if c}
    skip |= {"analysis type", "model(s)", "algorithm -> result", "units"}
    val = None
    if freq:
        cols = list(names)
        for c in cols[cols.index(freq) + 1:]:
            if c.lower() not in skip:
                val = c
                break
    step = next((low[k] for k in low if k == "test step"), None)
    units = low.get("units")
    return {"x": freq, "value": val, "hi": hi, "lo": lo, "step": step, "units": units}


def describe_padb_parquet(path, x_col=None):
    """One-entry analytics list for a PADB-style parquet (same dict shape as
    list_algorithms): rows USABLE by the loader (numeric x and value), units, whether
    it has numeric limits, the swept x, distinct x values, distinct test temperatures."""
    pf = pq.ParquetFile(path)
    roles = _padb_cols(pf.schema_arrow.names, x_col)
    if not roles["x"] or not roles["value"]:
        raise ValueError("could not find a swept x column and a value column")
    want = [c for c in (roles["x"], roles["value"], roles["hi"], roles["lo"], roles["step"], roles["units"]) if c]
    df = pf.read(columns=want).to_pandas()
    x = pd.to_numeric(df[roles["x"]], errors="coerce")
    v = pd.to_numeric(df[roles["value"]], errors="coerce")
    ok = x.notna() & v.notna()
    if not ok.any():
        raise ValueError("no rows with a numeric %r and %r" % (roles["x"], roles["value"]))

    def _lim(c):
        if not c:
            return False
        t = df[c].astype(str).str.strip().str.lstrip("<=>").str.strip()
        return bool(pd.to_numeric(t, errors="coerce").notna().any())
    if roles["units"] and df[roles["units"]].notna().any():
        units = str(df[roles["units"]].dropna().iloc[0])
    else:
        units = _units_from_label(roles["value"])
    n_steps = _step_summary(df[roles["step"]][ok].astype(object))[1] if roles["step"] else 1
    n_x = int(x[ok].nunique())
    return [{"algorithm": roles["value"], "base": roles["value"], "n_steps": max(n_steps, 1),
             "rows": int(ok.sum()), "units": units, "has_limits": _lim(roles["hi"]) or _lim(roles["lo"]),
             "x_col": roles["x"], "n_x": n_x, "wide": n_x > WIDE_X}]


def convert_padb_parquet(parquet, jobs_dir, x_col=None, publish_to=None, views=None, force=False):
    """Make a v2 job for a padb-tools parquet (used IN PLACE via parquet_path).  Same return
    shape as convert().  `{algo}` in publish_to expands to the file's slug."""
    info = describe_padb_parquet(parquet, x_col)[0]
    stem = _slug(os.path.splitext(os.path.basename(parquet))[0])
    os.makedirs(jobs_dir, exist_ok=True)
    jp = os.path.join(jobs_dir, "%s_v2_job.json" % stem)
    if os.path.exists(jp) and not force:
        return [{"algorithm": info["algorithm"], "status": "exists", "rows": info["rows"], "serials": None,
                 "limits": None, "data_path": None, "job_path": jp, "wide": None,
                 "note": "job file exists (use force/--force to overwrite)"}]
    if views is not None:
        bad = [w for w in views if w not in ALL_VIEWS]
        if bad or not views:
            raise ValueError("views must be a non-empty list of %s (got %s)" % (ALL_VIEWS, views))
    xh = info["x_col"]
    job = {
        "description": "%s (parquet)" % stem,
        "title_prefix": stem.replace("_", " "),
        "y_label": info["algorithm"],
        "parquet_path": os.path.abspath(parquet),
        "results_dir": "%s_v2_results" % stem,
        "index_title": stem.replace("_", " "),
        "spec_direction": "auto",
        "x_col": xh,
        "x_label": xh,
        "x_unit": _units_from_label(xh),
    }
    note = ""
    if info["n_x"] <= 1:
        job["views"] = ["histogram"]
        note = "  [no swept x -> histogram view]"
    elif views is not None:
        vv = [w for w in views if info["n_steps"] > 1 or w not in ENV_VIEWS]
        if len(vv) != len(views):
            note = "  [environmental views dropped: data has a single test temperature]"
        job["views"] = vv
    elif info["wide"]:
        job["views"] = list(WIDE_VIEWS)
        note = "wide sweep (%d x values) -> views %s" % (info["n_x"], WIDE_VIEWS)
    if publish_to is not None:
        job["publish_to"] = publish_to.replace("{algo}", stem)
    with open(jp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(job, fh, indent=2)
    return [{"algorithm": info["algorithm"], "status": "ok", "rows": info["rows"], "serials": None,
             "limits": info["has_limits"], "data_path": os.path.abspath(parquet), "job_path": jp,
             "wide": info["wide"], "note": note}]


def _cli_padb(a):
    if a.list:
        for r in describe_padb_parquet(a.parquet, a.x_col):
            print("%s: %d usable rows, units=%s, limits=%s, x=%s (%d values%s)" % (
                r["algorithm"], r["rows"], r["units"] or "-", r["has_limits"], r["x_col"],
                r["n_x"], ", WIDE" if r["wide"] else ""))
        return 0
    jobs_dir = a.jobs_dir or (os.path.dirname(os.path.abspath(a.out)) if a.out
                              else os.path.dirname(os.path.abspath(a.parquet)))
    pub = "" if a.no_publish else a.publish_to
    views = [v.strip() for v in a.views.split(",") if v.strip()] if a.views else None
    for r in convert_padb_parquet(a.parquet, jobs_dir, a.x_col, pub, views, a.force):
        print("%s: %s rows%s" % (r["algorithm"], format(r["rows"], ","),
                                 ("  [" + r["note"].strip() + "]") if r["note"] else ""))
        print("  job: %s" % r["job_path"])
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parquet")
    ap.add_argument("--out", help="results folder (data files go in <out>/padb)")
    ap.add_argument("--jobs-dir", default=None, help="where to write v2 jobs (default: --out parent)")
    ap.add_argument("--x-col", default=None, help="parquet column for the swept x (default auto)")
    ap.add_argument("--algorithm", default=None, help="only algorithms containing this text")
    ap.add_argument("--no-run-datetime", action="store_true")
    ap.add_argument("--format", choices=("parquet", "csv"), default="parquet")
    ap.add_argument("--force", action="store_true", help="overwrite existing job files")
    ap.add_argument("--views", default=None,
                    help="comma-separated views to build (%s); default: auto" % ",".join(ALL_VIEWS))
    ap.add_argument("--split-results", action="store_true",
                    help="one job per result stream instead of one job per algorithm with a Result filter")
    ap.add_argument("--list", action="store_true", help="list the algorithms and exit")
    ap.add_argument("--stream", choices=("auto", "always", "off"), default="auto",
                    help="auto (default): convert in memory when the file fits, else stream it a slice of rows at a time")
    ap.add_argument("--server", default=None, help="convert only rows whose server contains one of these (comma list), e.g. SR")
    ap.add_argument("--serials", default=None, help="convert only these serials: a count N (the first N, sorted) or a comma list")
    ap.add_argument("--steps", default=None, help="convert only these test steps (comma list), e.g. Room,55.0 Deg C")
    ap.add_argument("--x-range", nargs=2, type=float, metavar=("LO", "HI"), default=None,
                    help="convert only this band of each algorithm's OWN swept x column (inclusive, stored units)")
    ap.add_argument("--plan-bands", type=int, metavar="MAX_ROWS", default=None,
                    help="print how each algorithm's swept x splits into bands of at most MAX_ROWS rows, and exit")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--no-publish", action="store_true")
    g.add_argument("--publish-to", default=None)
    a = ap.parse_args()
    kind = sniff_parquet(a.parquet)
    if kind is None:
        ap.error("not a recognised parquet (neither padbloader long format nor PADB scatter columns)")
    if kind == "padb":
        return _cli_padb(a)       # padb-tools' own parquet: no conversion, job points at it in place
    if a.list:
        for r in list_algorithms(a.parquet, a.x_col):
            print("%s: %d rows, units=%s, limits=%s, x=%s (%d values%s)" % (
                r["algorithm"], r["rows"], r["units"] or "-", r["has_limits"], r["x_col"],
                r["n_x"], ", WIDE" if r["wide"] else ""))
        return 0
    sl = {}
    if a.server:
        sl["servers"] = [s.strip() for s in a.server.split(",") if s.strip()]
    if a.serials:
        sl["serials"] = int(a.serials) if a.serials.strip().isdigit() else [s.strip() for s in a.serials.split(",") if s.strip()]
    if a.steps:
        sl["steps"] = [s.strip() for s in a.steps.split(",") if s.strip()]
    if a.x_range:
        sl["x_range"] = list(a.x_range)
    if a.plan_bands:
        import padb_loader_stream
        for p in padb_loader_stream.plan_x_bands(a.parquet, a.plan_bands, None, a.algorithm, sl or None, a.x_col,
                                                 not a.split_results):
            print("%s: %s rows, x = %s (%d values) -> %d band(s)" % (p["algorithm"], format(p["rows"], ","), p["x_col"],
                                                                      p["n_x"], len(p["bands"])))
            for b in p["bands"]:
                print("    --x-range %s %s    (%s rows, %d x values)" % (_fmt(b["lo"]), _fmt(b["hi"]), format(b["rows"], ","), b["n_x"]))
        return 0
    if not a.out:
        ap.error("--out is required (unless --list)")
    pub = "" if a.no_publish else a.publish_to
    jobs_dir = a.jobs_dir or os.path.dirname(os.path.abspath(a.out))
    made = convert(a.parquet, a.out, jobs_dir, a.x_col, a.algorithm, pub,
                   not a.no_run_datetime, None, a.format, a.force, combine_results=not a.split_results,
                   views=[v.strip() for v in a.views.split(",") if v.strip()] if a.views else None,
                   stream=a.stream, slice_=sl or None)
    for r in made:
        if r["status"] == "exists":
            print("%s: skipped -- %s" % (r["algorithm"], r["note"]))
            print("  job: %s" % r["job_path"])
            continue
        print("%s: %d rows, %d serials, limits=%s%s" % (
            r["algorithm"], r["rows"], r["serials"], r["limits"],
            ("  [" + r["note"] + "]") if r["note"] else ""))
        print("  data: %s" % r["data_path"])
        print("  job: %s" % r["job_path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
