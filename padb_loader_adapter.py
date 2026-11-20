"""padb_loader_adapter.py -- convert a direct-from-Oracle "padbloader" parquet
(long format: one row per measurement, c_* = conditions, r_* = run attributes)
into PADB-style Type=80 scatter CSVs + matching *_v2_job.json files, so the
normal padb_v2.py pipeline (all interactive views) can plot them unchanged.

Usage:
  py padb_loader_adapter.py <file.parquet> --out <results_dir> [--jobs-dir <dir>]
        [--no-publish | --publish-to <path>] [--x-col c_frequency_offset]
        [--algorithm "substring"] [--no-run-datetime]

Writes <out>/padb/<slug>.csv per algorithm and <jobs-dir>/<stem>_<slug>_v2_job.json.
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


MAX_COND_CARD = 100


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


def convert(parquet, out_dir, jobs_dir, x_col=None, algo_filter=None,
            publish_to=None, use_runtime=True):
    pf = pq.ParquetFile(parquet)
    lab, unit = _labels(pf.schema_arrow)
    df = pf.read().to_pandas()
    if algo_filter:
        df = df[df["algorithm"].str.contains(algo_filter, case=False, regex=False)]
    if df.empty:
        raise SystemExit("no rows after filtering")
    stem = _slug(os.path.splitext(os.path.basename(parquet))[0])
    csv_dir = os.path.join(out_dir, "padb")
    os.makedirs(csv_dir, exist_ok=True)
    os.makedirs(jobs_dir, exist_ok=True)
    device = str(df["device"].iloc[0])
    made = []
    for algo, g in df.groupby("algorithm", sort=True):
        g = g.dropna(subset=["value"]).copy()
        units = str(g["units"].dropna().iloc[0]) if g["units"].notna().any() else ""
        # swept x: explicit, else Frequency Offset if it varies, else Frequency
        xc = x_col
        if not xc:
            xc = "c_frequency_offset" if g.get("c_frequency_offset", pd.Series(dtype=float)).nunique() > 1 else "c_frequency"
        xlab = lab.get(xc, xc)
        xu = unit.get(xc, "")
        x_header = "%s (%s)" % (xlab, xu) if xu else xlab
        val_header = "%s (%s)" % (algo, units) if units else algo
        # condition columns for Group: every c_* with >=1 non-null (not x)
        # plus selected run attributes; constant ones are harmless (hidden by the views)
        # A per-row identifier (e.g. c_etp_id: ~1 distinct value per 27 rows) in
        # Group makes every sweep its own "condition" -> tens of thousands of
        # conditions, unusable filters and an impossibly large boxplot. Skip any
        # condition column with more than MAX_COND_CARD distinct values.
        ccols = []
        for c in g.columns:
            if not c.startswith("c_") or c == xc or not g[c].notna().any():
                continue
            if g[c].nunique() > MAX_COND_CARD:
                print("  note: %s dropped from Group (%d distinct values > %d)" % (c, g[c].nunique(), MAX_COND_CARD))
                continue
            ccols.append(c)
        rcols = [c for c in _RUN_KEEP if c in g.columns and g[c].notna().any()]
        keys = []
        for c in ccols + rcols:
            l = lab.get(c, c)
            u = unit.get(c, "")
            keys.append((c, "%s (%s)" % (l, u) if u else l))
        grp = pd.Series("", index=g.index)
        for c, l in keys:
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
        o = pd.DataFrame({
            "Analysis Type": "ScatterPlot",
            "Model(s)": g["device"],
            "Algorithm -> Result": algo + "-->" + g["result"].fillna(algo) + (" (%s)" % units if units else ""),
            "Units": units,
            "Group": grp,
            x_header: g[xc],
            val_header: g["value"],
            "Serial Number": g["serial"],
        })
        has_lim = False
        for src, dst in (("limit_upper", "Upper Limit"), ("limit_lower", "Lower Limit")):
            if g[src].notna().any():
                o[dst] = g[src]
                has_lim = True
        csv_path = os.path.join(csv_dir, "%s_%s.csv" % (stem, _slug(algo)))
        o.to_csv(csv_path, index=False, quoting=1)
        slug = _slug(algo)
        res_dir_rel = "%s_%s_v2_results" % (stem, slug)
        job = {
            "description": "%s %s (padbloader parquet)" % (device, algo),
            "title_prefix": "%s %s" % (device, algo),
            "y_label": "%s (%s)" % (algo, units),
            "csv_path": os.path.abspath(csv_path),
            "results_dir": res_dir_rel,
            "index_title": "%s %s" % (device, stem),
            "spec_direction": "auto",
            "x_col": x_header,
            "x_label": x_header,
            "x_unit": xu,
        }
        if publish_to is not None:
            job["publish_to"] = publish_to
        jp = os.path.join(jobs_dir, "%s_%s_v2_job.json" % (stem, slug))
        with open(jp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(job, fh, indent=2)
        made.append((algo, len(o), g["serial"].nunique(), has_lim, csv_path, jp))
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parquet")
    ap.add_argument("--out", required=True, help="results folder (CSVs go in <out>/padb)")
    ap.add_argument("--jobs-dir", default=None, help="where to write v2 jobs (default: --out parent)")
    ap.add_argument("--x-col", default=None, help="parquet column for the swept x (default auto)")
    ap.add_argument("--algorithm", default=None, help="only algorithms containing this text")
    ap.add_argument("--no-run-datetime", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--no-publish", action="store_true")
    g.add_argument("--publish-to", default=None)
    a = ap.parse_args()
    pub = "" if a.no_publish else a.publish_to
    jobs_dir = a.jobs_dir or os.path.dirname(os.path.abspath(a.out))
    made = convert(a.parquet, a.out, jobs_dir, a.x_col, a.algorithm, pub, not a.no_run_datetime)
    for algo, n, ns, lim, cp, jp in made:
        print("%s: %d rows, %d serials, limits=%s" % (algo, n, ns, lim))
        print("  csv: %s" % cp)
        print("  job: %s" % jp)


if __name__ == "__main__":
    sys.exit(main())
