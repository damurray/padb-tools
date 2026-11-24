"""padb_jobschema.py -- the job file as ONE declarative, validated object.

v3.0 stage 6. A padb-tools job (`*_job.json`) accumulated ~80 keys across extraction, analysis, size handling and
publishing, read by `.get()` in a dozen places. Nothing checked them: an unknown key was silently ignored, so a typo
such as "scater_decimate" or a mistyped value ("tier_budget_s": "fast") changed nothing and said nothing. This module
is the single description of every key -- its type, what kind of job uses it, its group, a one-line meaning and its
constraints -- and a `validate()` that reports problems without ever raising:

    issues = validate(job)            -> [{level: 'error'|'warn', key, message, suggestion}]
    describe()                        -> the schema as plain JSON (the web app builds its editor from this)

Rules (deliberately conservative -- the schema must never reject a job the tools run happily):
  * an unknown key is a WARNING (it has no effect) with a did-you-mean when one is close; keys starting with "_"
    are comments / internal and always accepted ("_notes", "_note");
  * a wrong TYPE or an out-of-range / not-in-enum value is an ERROR (the tools would misbehave or fall back);
  * a job needs a data source: `pod` (extraction) and/or `csv_path` / `parquet_path` / `compare_csv` (plot);
  * contradictions that silently resolve one way are WARNINGS ("limit_overrides wins over limits_file").
`secondary_plots` entries (V1 jobs) have their own small schema (`SECONDARY`).

The schema is checked against reality by `qa_jobschema`: every key a real job file in the Data folder uses
validates clean, every key the code reads is described here (a source scan), and every generator output validates.
"""
from __future__ import annotations

import difflib
import json

NUM = ("int", "float")

VIEWS = ("scatter", "stat_summary", "boxplot", "distribution", "env_coverage", "summary", "reference", "histogram")
PLOT_TYPES = ("accuracy_vs_freq", "population_envelope", "distribution", "empirical_cdf", "spec_derivation",
              "de_heatmap", "de_summary", "stat_summary", "stat_boxplot", "summary_plot", "histogram")
DRAW_MODES = ("lines", "markers", "lines+markers", "sticks")


def F(types, kind="both", group="analysis", desc="", **opts):
    t = (types,) if isinstance(types, str) else tuple(types)
    return dict(types=t, kind=kind, group=group, desc=desc, **opts)


SCHEMA = {
    # ---- identity
    "description": F("str", "both", "identity", "Free text shown in the jobs list."),
    "title": F("str", "plot", "identity", "Page title (default: the output file name)."),
    "title_prefix": F("str", "plot", "identity", "Prefix of every page title and file name."),
    "index_title": F("str", "plot", "identity", "Heading of the results index page."),
    "results_dir": F("str", "both", "identity", "Results folder (relative paths resolve against the job file)."),
    # ---- extraction (PADB-R.exe)
    "pod": F("str", "run", "extraction", "The .pod file PADB-R.exe runs; its presence makes this an extraction job."),
    "padb_exe": F("str", "run", "extraction", "Path to PADB-R.exe."),
    "padb_timeout": F("int", "run", "extraction", "Seconds PADB-R may run before it is killed.", lo=1),
    "run_analytics": F("bool", "run", "extraction", "Run the pod's analytics (false = reuse existing output)."),
    "padb_output_dir": F("str", "run", "extraction", "Folder PADB writes raw output to (-dir)."),
    "padb_logs_dir": F("str", "run", "extraction", "Folder holding the PADB error logs."),
    "subex": F("dict", "run", "extraction", "Raw [Extract] overrides patched into the run copy of the pod "
               "(values may be relative-date sentinels such as \"30 days ago\")."),
    "runtime_subex": F("dict", "run", "extraction", "Extract overrides applied only at run time."),
    "unique_output_filenames": F("bool", "run", "extraction", "Force a unique output file per analytic."),
    "force_output_csv": F("bool", "run", "extraction", "Force CSV output on every scatter analytic."),
    "reduction_extraction": F("bool", "run", "extraction", "All-runs extraction for the test-point reduction study."),
    "mode": F("str", "both", "extraction", "legacy / simple (PADB native plots) / interactive (HTML views).",
              enum=("legacy", "simple", "interactive")),
    # ---- data source (plot jobs)
    "csv_path": F("str", "plot", "source", "The PADB scatter CSV to plot."),
    "parquet_path": F("str", "plot", "source", "A parquet with the same columns as the CSV (or a padb-tools sidecar)."),
    "analytic": F("str", "plot", "source", "Analytic name (used to name outputs and match CSVs)."),
    "compare_csv": F("dict", "plot", "source", "{site name: csv path} -- a cross-site comparison.", item_types=("str",)),
    "primary_site": F("str", "plot", "source", "The reference site of a cross-site comparison."),
    "env_coverage_csv": F("str", "plot", "source", "A Type=60 environmental CSV for the env_coverage view."),
    # ---- analysis: views and axes
    "views": F("list", "plot", "analysis", "Which views to build (omitted = chosen automatically).",
               item_enum=VIEWS, nonempty=True),
    "x_col": F("str", "plot", "analysis", "Exact swept-x column name when it is not Frequency / X value."),
    "x_label": F("str", "plot", "analysis", "Full x-axis title."),
    "x_unit": F("str", "plot", "analysis", "Short x unit used in hovers, tables and CSV headers."),
    "y_label": F("str", "plot", "analysis", "Y-axis title."),
    "y_lim": F("list", "plot", "analysis", "Fixed y range [lo, hi].", length=2, item_types=NUM),
    "log_x": F(("bool", "null"), "plot", "analysis", "Force a log (true) or linear (false) x axis; omitted = automatic."),
    "freq_scale": F(NUM, "plot", "analysis", "Multiplier from the CSV frequency to MHz (e.g. 1e-06 for Hz)."),
    "env_coverage_y_label": F("str", "plot", "analysis", "env_coverage Y-axis title."),
    "env_coverage_y_lim": F("list", "plot", "analysis", "env_coverage fixed y range [lo, hi].", length=2, item_types=NUM),
    "env_coverage_freq_scale": F(NUM, "plot", "analysis", "Frequency multiplier for the env_coverage CSV."),
    "freq_bands": F("list", "plot", "analysis", "Named x ranges [{label, lo, hi}] for the V1 plots.", item_types=("dict",)),
    # ---- analysis: limits and statistics
    "spec_direction": F("str", "plot", "analysis", "Which side(s) the spec limits are for when the data has none.",
                        enum=("lo", "hi", "both", "none", "auto")),
    "spec_interp": F("str", "plot", "analysis", "Shape of the scatter spec line between points.",
                     enum=("step", "linear", "spline")),
    "spec_limits": F("list", "plot", "analysis", "Manual [lower, upper] spec for the V1 plots (null = none).",
                     length=2, item_types=NUM + ("null",)),
    "limits_file": F("str", "plot", "analysis", "A limit-overrides file (relative to the job file)."),
    "limit_overrides": F("list", "plot", "analysis", "Limit overrides written inline (already in the data's x unit).",
                         item_types=("dict",)),
    "filters_file": F("str", "plot", "analysis", "Which conditions the pages offer as filters (relative to the job)."),
    "filter_config": F("dict", "plot", "analysis", "The same filter configuration written inline."),
    "room_values": F("list", "plot", "analysis", "Test-step values that count as Room.", item_types=("str",)),
    "proportion": F(NUM, "plot", "analysis", "Tolerance-interval proportion P.", lo=0, hi=1, exclusive=True),
    "confidence": F(NUM, "plot", "analysis", "Tolerance-interval confidence C.", lo=0, hi=1, exclusive=True),
    "proportion_env": F(NUM, "plot", "analysis", "Proportion for the environmental views.", lo=0, hi=1, exclusive=True),
    "confidence_env": F(NUM, "plot", "analysis", "Confidence for the environmental views.", lo=0, hi=1, exclusive=True),
    "meas_unc": F(NUM, "plot", "analysis", "Measurement uncertainty subtracted from the spec (stat_summary default).", lo=0),
    "guard_band": F(NUM, "plot", "analysis", "Guard band subtracted from the spec (stat_summary default).", lo=0),
    "hist_limit_hi": F(NUM, "plot", "analysis", "Upper spec for the histogram view when the CSV has none."),
    "hist_limit_lo": F(NUM, "plot", "analysis", "Lower spec for the histogram view when the CSV has none."),
    "result_default": F("str", "plot", "analysis", "The result pre-selected for a multi-result padbloader dataset."),
    "pod_filter_expression": F("str", "plot", "analysis", "The pod's PADB filter expression, shown in the help panel."),
    "group_col_filter": F(("str", "list", "dict"), "plot", "analysis", "Restricts which Group columns the V1 heat-map uses."),
    "box_drop_points": F("bool", "plot", "analysis", "Omit the per-point overlay of the boxplot (smaller page)."),
    "secondary_plots": F("list", "both", "analysis", "V1 jobs: the list of plots built from the extracted CSVs.",
                         item_types=("dict",)),
    # ---- size and speed
    "binary_encode": F("bool", "plot", "size", "float32-pack the embedded numeric arrays (auto-enabled for big data)."),
    "binary_encode_auto_mb": F(NUM, "plot", "size", "CSV size at which binary_encode switches on automatically.", lo=0),
    "binary_encode_auto_rows": F("int", "plot", "size", "Row count at which binary_encode switches on automatically.", lo=0),
    "compress_data": F(("bool", "str"), "plot", "size", "Deflate very large embedded data (auto / true / false).",
                       enum_str=("auto",)),
    "compress_data_auto_mb": F(NUM, "plot", "size", "Page size at which compression switches on.", lo=0),
    "scatter_decimate": F(("bool", "str", "null"), "plot", "size", "Thin dense scatter series at build time.",
                          enum_str=("auto", "off", "always")),
    "scatter_decimate_target": F("int", "plot", "size", "Points kept per decimated series.", lo=1),
    "scatter_decimate_threshold": F("int", "plot", "size", "Series length above which decimation applies.", lo=1),
    "scatter_decimate_toggle": F("bool", "plot", "size", "Embed all points with a live 'Show all points' checkbox."),
    "export_parquet": F(("bool", "str"), "plot", "size", "Write a parquet sidecar for the viewer (auto / true / false).",
                        enum_str=("auto",)),
    "export_parquet_auto_mb": F(NUM, "plot", "size", "CSV size at which the sidecar is written automatically.", lo=0),
    "export_parquet_auto_rows": F("int", "plot", "size", "Row count at which the sidecar is written automatically.", lo=0),
    "parallel_views": F(("bool", "str"), "plot", "size", "Render views in worker processes (auto / true / false).",
                        enum_str=("auto",)),
    "parallel_views_auto_rows": F("int", "plot", "size", "Row count at which parallel rendering switches on.", lo=0),
    "parallel_workers": F("int", "plot", "size", "Number of worker processes.", lo=1),
    "tiering": F(("bool", "str"), "plot", "size", "Predict build cost and skip over-budget automatic views.",
                 enum_str=("auto", "off", "false")),
    "tier_budget_s": F(NUM, "plot", "size", "Per-view predicted build time above which an automatic view is skipped.",
                       lo=0, exclusive_lo=True),
    "auto_bands": F("bool", "plot", "size", "Generate an editable named-bands file next to the results."),
    # ---- reports and publishing
    "build_pdf_report": F("bool", "plot", "publish", "Also build the multi-view PDF report."),
    "pdf_report_apply_filter": F("bool", "plot", "publish", "Apply each view's auto-filter before printing the PDF."),
    "pdf_report_filter_site": F("str", "plot", "publish", "Site scope of the PDF auto-filter."),
    "publish": F("dict", "run", "publish", "V1 publishing: {\"destination\": path}.", item_types=("str",)),
    "publish_to": F(("str", "bool", "null"), "plot", "publish", "Where to publish (omitted = the default share; "
                    "\"\" / false / null = do not publish)."),
    "room_only_full_views": F("bool", "plot", "analysis", "No longer has any effect.", deprecated="summary and "
                              "stat_summary are Room-only defaults now"),
}

SECONDARY = {
    "type": F("str", "run", "plot", "Which plot function to run.", enum=PLOT_TYPES, required=True),
    "csv": F("str", "run", "plot", "Substring matched against analytic names to find the CSV."),
    "csv_file": F("str", "run", "plot", "Exact CSV file name in results/padb (wins over `csv`)."),
    "title": F("str", "run", "plot", "Page title."),
    "y_label": F("str", "run", "plot", "Y-axis title."),
    "y_lim": F("list", "run", "plot", "Fixed y range [lo, hi].", length=2, item_types=NUM),
    "log_x": F(("bool", "null"), "run", "plot", "Force a log / linear x axis."),
    "mode": F("str", "run", "plot", "Scatter draw mode.", enum=DRAW_MODES),
    "proportion": F(NUM, "run", "plot", "Tolerance-interval proportion.", lo=0, hi=1, exclusive=True),
    "confidence": F(NUM, "run", "plot", "Tolerance-interval confidence.", lo=0, hi=1, exclusive=True),
    "spec_limits": F("list", "run", "plot", "Manual [lower, upper] spec (null = none).", length=2, item_types=NUM + ("null",)),
    "freq_bands": F("list", "run", "plot", "Named x ranges [{label, lo, hi}].", item_types=("dict",)),
    "comment": F("str", "run", "plot", "Free text."),
}

_PYTYPES = {"str": str, "int": int, "float": float, "bool": bool, "list": list, "dict": dict, "null": type(None)}


def _is(v, tname: str) -> bool:
    if tname == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if tname == "float":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _PYTYPES[tname])


def _type_ok(v, types) -> bool:
    return any(_is(v, t) for t in types)


def _tname(v) -> str:
    return "null" if v is None else type(v).__name__


def _issue(level, key, message, suggestion=""):
    return {"level": level, "key": key, "message": message, "suggestion": suggestion}


def _check_value(key, v, spec, where=""):
    out = []
    types = spec["types"]
    label = where + key
    if not _type_ok(v, types):
        out.append(_issue("error", label, "expected %s, got %s (%s)"
                          % (" or ".join("number" if t in ("int", "float") else t for t in dict.fromkeys(
                              "float" if t in ("int", "float") else t for t in types)), _tname(v), json.dumps(v)[:40])))
        return out
    if v is None or isinstance(v, bool):
        return out
    if "enum" in spec and isinstance(v, str) and v not in spec["enum"]:
        out.append(_issue("error", label, "%r is not one of %s" % (v, ", ".join(spec["enum"])),
                          (difflib.get_close_matches(v, spec["enum"], 1, 0.6) or [""])[0]))
    if "enum_str" in spec and isinstance(v, str) and v.lower() not in spec["enum_str"]:
        out.append(_issue("error", label, "%r is not one of %s (or true / false)" % (v, ", ".join(spec["enum_str"]))))
    if isinstance(v, (int, float)):
        lo, hi = spec.get("lo"), spec.get("hi")
        excl = spec.get("exclusive")
        if lo is not None and (v <= lo if (excl or spec.get("exclusive_lo")) else v < lo):
            out.append(_issue("error", label, "%s must be %s %s" % (v, ">" if (excl or spec.get("exclusive_lo")) else ">=", lo)))
        elif hi is not None and (v >= hi if excl else v > hi):
            out.append(_issue("error", label, "%s must be %s %s" % (v, "<" if excl else "<=", hi)))
    if isinstance(v, list):
        if spec.get("nonempty") and not v:
            out.append(_issue("error", label, "must not be an empty list"))
        if spec.get("length") is not None and len(v) != spec["length"]:
            out.append(_issue("error", label, "must have exactly %d entries, has %d" % (spec["length"], len(v))))
        its = spec.get("item_types")
        if its:
            for i, it in enumerate(v):
                if not _type_ok(it, its):
                    out.append(_issue("error", "%s[%d]" % (label, i), "expected %s, got %s" % ("/".join(dict.fromkeys(
                        "number" if t in ("int", "float") else t for t in its)), _tname(it))))
        ie = spec.get("item_enum")
        if ie:
            for i, it in enumerate(v):
                if isinstance(it, str) and it not in ie:
                    out.append(_issue("error", "%s[%d]" % (label, i), "unknown view %r" % it,
                                      (difflib.get_close_matches(it, ie, 1, 0.6) or [""])[0]))
    if isinstance(v, dict) and spec.get("item_types"):
        for k2, it in v.items():
            if not _type_ok(it, spec["item_types"]):
                out.append(_issue("error", "%s.%s" % (label, k2), "expected %s, got %s" % (
                    "/".join(spec["item_types"]), _tname(it))))
    return out


def _unknown(key, known, where=""):
    close = difflib.get_close_matches(key, list(known), 1, 0.75)
    msg = "unknown key (it has no effect)" + ((" -- did you mean %r?" % close[0]) if close else "")
    return _issue("warn", where + key, msg, close[0] if close else "")


def job_kind(job: dict) -> str:
    """'run' (extraction: has a pod), 'plot' (has a data source) or 'unknown'."""
    if "pod" in job:
        return "run"
    if any(k in job for k in ("csv_path", "parquet_path", "compare_csv", "analytic")):
        return "plot"
    return "unknown"


def validate(job) -> list:
    """-> list of issues (never raises). See the module docstring for the rules."""
    if not isinstance(job, dict):
        return [_issue("error", "", "a job must be a JSON object, got %s" % _tname(job))]
    out = []
    for key, v in job.items():
        if key.startswith("_"):
            continue
        spec = SCHEMA.get(key)
        if spec is None:
            out.append(_unknown(key, SCHEMA))
            continue
        if spec.get("deprecated"):
            out.append(_issue("warn", key, "deprecated: " + spec["deprecated"]))
            continue
        out += _check_value(key, v, spec)
    # ---- a data source
    kind = job_kind(job)
    if kind == "unknown":
        out.append(_issue("error", "", "no data source: set `pod` (extraction) or `csv_path` / `parquet_path` / `compare_csv` (plot)"))
    if "pod" in job and not job.get("run_analytics", True) and not any(k in job for k in ("csv_path", "parquet_path")):
        pass  # a run job that reuses existing output is legitimate
    # ---- contradictions that silently resolve one way
    if "limit_overrides" in job and "limits_file" in job:
        out.append(_issue("warn", "limits_file", "both limit_overrides and limits_file are set; limit_overrides wins"))
    if "filter_config" in job and "filters_file" in job:
        out.append(_issue("warn", "filters_file", "both filter_config and filters_file are set; filter_config wins"))
    if "csv_path" in job and "parquet_path" in job:
        out.append(_issue("warn", "parquet_path", "both csv_path and parquet_path are set; parquet_path is the source"))
    if job.get("compare_csv") and not job.get("primary_site"):
        out.append(_issue("warn", "primary_site", "a cross-site compare without primary_site has no reference population"))
    if isinstance(job.get("views"), list) and isinstance(job.get("tiering"), (bool, str)) and job.get("views"):
        pass
    # ---- V1 secondary_plots
    sp = job.get("secondary_plots")
    if isinstance(sp, list):
        for i, e in enumerate(sp):
            where = "secondary_plots[%d]." % i
            if not isinstance(e, dict):
                continue
            for key, v in e.items():
                if key.startswith("_"):
                    continue
                spec = SECONDARY.get(key)
                if spec is None:
                    out.append(_unknown(key, SECONDARY, where))
                    continue
                out += _check_value(key, v, spec, where)
            if "type" not in e:
                out.append(_issue("error", where + "type", "missing: which plot function to run"))
    return out


def errors(issues) -> list:
    return [i for i in issues if i["level"] == "error"]


def format_issues(issues, name="job") -> str:
    lines = []
    for i in issues:
        lines.append("  [%s] %s: %s" % (i["level"].upper(), i["key"] or name, i["message"]))
    return "\n".join(lines)


def report(job, name="job", log=print) -> list:
    """Validate and PRINT the findings (never raises, never stops a run: the schema reports, the tools decide).
    -> the issues."""
    try:
        issues = validate(job)
    except Exception as exc:  # noqa: BLE001 -- a validator bug must not stop a build
        log("  [WARN] job schema check skipped: %s" % exc)
        return []
    if issues:
        log("  Job check (%s): %d error(s), %d warning(s)" % (name, len(errors(issues)), len(issues) - len(errors(issues))))
        log(format_issues(issues, name))
    return issues


def describe() -> dict:
    """The schema as plain JSON: {version, groups: [...], fields: {key: {...}}, secondary: {...}, views, plot_types}."""
    def pub(d):
        return {k: {kk: (list(vv) if isinstance(vv, tuple) else vv) for kk, vv in s.items()} for k, s in d.items()}
    groups = []
    for s in SCHEMA.values():
        if s["group"] not in groups:
            groups.append(s["group"])
    return {"schema": "padb-job-schema/1", "groups": groups, "fields": pub(SCHEMA), "secondary": pub(SECONDARY),
            "views": list(VIEWS), "plot_types": list(PLOT_TYPES)}
