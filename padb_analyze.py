r"""padb_analyze.py -- the stable ENTRY POINT of padb-tools' analysis layer (v3.0).

Data in, artifacts out. Nothing here extracts data, schedules anything, or touches PADB-R / Oracle: give it a
parquet (padbloader's own extract, or padb-tools' PADB-style table) or a PADB scatter CSV and it generates the
analysis objects (plot jobs) and the plots (self-contained interactive HTML views + index, optionally the PDF
report). It is how padbloader -- or any other tool, in any language -- uses the analysis capability: write a
request JSON, run this, read the result JSON. It can equally be imported (`analyze(request) -> result`).

    py padb_analyze.py request.json [--result result.json]        # '-' reads the request from stdin
    py padb_analyze.py --input data.parquet --out results_dir [--views scatter,summary] [--limits-file f.json] [--filters-file filters.json]

REQUEST  (schema "padb-analysis-request/1"; only `input` and `out` are required)
    {"schema": "padb-analysis-request/1",
     "input":  "C:/data/extract.parquet",       # padbloader parquet | padb-tools parquet | PADB scatter .csv
     "out":    "C:/data/results",               # artifacts go under <out>/jobs and <out>/results/<analysis>
     "views":  ["scatter", "stat_summary"],      # optional; default = automatic per dataset
     "algorithms": ["..."],                      # optional; padbloader parquet: which results to analyse
     "x_col":  "Frequency (MHz)",                # optional; override the swept-x column
     "limits_file": "C:/data/limits.json",       # optional; limit overrides (see padb_limits.py)
     "publish_to": "",                           # optional; default "" = write locally, publish nowhere
     "pdf_report": false}                        # optional; also build the multi-view PDF

RESULT  (stdout, and --result FILE; schema "padb-analysis-result/1"; progress text goes to stderr)
    {"schema": "padb-analysis-result/1", "ok": true,
     "analyses": [{"name": "...", "job": ".../jobs/x_v2_job.json", "results_dir": "...", "index": ".../index.html",
                   "views": ["scatter", ...], "files": ["...html", ...], "status": "ok" | "failed", "error": null}],
     "warnings": []}
Exit code: 0 all analyses ok, 1 at least one failed, 2 bad request.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

REQUEST_SCHEMA = "padb-analysis-request/1"
RESULT_SCHEMA = "padb-analysis-result/1"
_VIEWS = ("scatter", "stat_summary", "boxplot", "distribution", "env_coverage", "summary", "reference", "histogram")


class BadRequest(ValueError):
    pass


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(s)).strip("_") or "analysis"


def validate_request(req) -> dict:
    """-> the normalised request (absolute paths) or raises BadRequest."""
    if not isinstance(req, dict):
        raise BadRequest("the request must be a JSON object")
    sch = req.get("schema", REQUEST_SCHEMA)
    if sch != REQUEST_SCHEMA:
        raise BadRequest("unsupported schema %r (this build speaks %r)" % (sch, REQUEST_SCHEMA))
    if not req.get("input"):
        raise BadRequest("`input` is required (a parquet or a PADB scatter .csv)")
    if not req.get("out"):
        raise BadRequest("`out` is required (the output folder)")
    inp = Path(req["input"]).expanduser().resolve()
    if not inp.is_file():
        raise BadRequest("input not found: %s" % inp)
    if inp.suffix.lower() not in (".parquet", ".csv"):
        raise BadRequest("input must be a .parquet or .csv file: %s" % inp.name)
    views = req.get("views")
    if views is not None and (not isinstance(views, list) or not views or not all(isinstance(v, str) for v in views)):
        raise BadRequest("`views` must be a non-empty list of view names")
    algos = req.get("algorithms")
    if algos is not None and (not isinstance(algos, list) or not all(isinstance(a, str) for a in algos)):
        raise BadRequest("`algorithms` must be a list of strings")
    lim = req.get("limits_file")
    if lim:
        lp = Path(lim).expanduser().resolve()
        if not lp.is_file():
            raise BadRequest("limits_file not found: %s" % lp)
        lim = str(lp)
    flt = req.get("filters_file")
    if flt:
        fp = Path(flt).expanduser().resolve()
        if not fp.is_file():
            raise BadRequest("filters_file not found: %s" % fp)
        flt = str(fp)
    unknown = set(req) - {"schema", "input", "out", "views", "algorithms", "x_col", "limits_file", "filters_file",
                          "publish_to", "pdf_report"}
    if unknown:
        raise BadRequest("unknown request field(s): %s" % ", ".join(sorted(unknown)))
    return {"input": inp, "out": Path(req["out"]).expanduser().resolve(), "views": views, "algorithms": algos,
            "x_col": req.get("x_col") or None, "limits_file": lim, "filters_file": flt, "publish_to": req.get("publish_to") or "",
            "pdf_report": bool(req.get("pdf_report"))}


def _make_jobs(r, log):
    """-> [(name, job_path)] -- the analysis objects for this input."""
    out, jobs_dir = r["out"], r["out"] / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    inp = r["input"]
    if inp.suffix.lower() == ".csv":
        job = {"csv_path": str(inp), "title_prefix": inp.stem, "description": "padb_analyze: " + inp.name}
        if r["views"]:
            job["views"] = r["views"]
        if r["x_col"]:
            job["x_col"] = r["x_col"]
        jp = jobs_dir / ("%s_v2_job.json" % _slug(inp.stem))
        jp.write_text(json.dumps(job, indent=2), encoding="utf-8")
        return [(inp.stem, jp)]
    import padb_loader_adapter as ad
    kind = ad.sniff_parquet(str(inp))
    if kind is None:
        raise BadRequest("not a recognised parquet: neither a padbloader extract (algorithm + value columns) "
                         "nor a padb-tools table (a Group column plus a Frequency / X value column)")
    if kind == "padb":
        made = ad.convert_padb_parquet(str(inp), str(jobs_dir), r["x_col"], "", r["views"], True)
    else:
        made = ad.convert(str(inp), str(out), str(jobs_dir), r["x_col"], None, "", True, r["algorithms"],
                          "parquet", True, log=log, views=r["views"])
    return [(m["algorithm"], Path(m["job_path"])) for m in made if m.get("job_path") and m.get("status") != "exists"]


def analyze(request, log=None) -> dict:
    """Run one analysis request. Raises BadRequest on a malformed request; per-analysis build failures are
    reported in the result (status 'failed'), never raised."""
    log = log or (lambda *a, **k: print(*a, file=sys.stderr, **k))
    r = validate_request(request)
    r["out"].mkdir(parents=True, exist_ok=True)
    result = {"schema": RESULT_SCHEMA, "ok": True, "analyses": [], "warnings": []}
    jobs = _make_jobs(r, log)
    if not jobs:
        result["warnings"].append("no analyses were generated from this input (nothing usable, or filtered out)")
    import padb_v2
    for name, jp in jobs:
        job = json.loads(jp.read_text(encoding="utf-8-sig"))
        rd = r["out"] / "results" / _slug(jp.stem.replace("_v2_job", ""))
        job["results_dir"] = str(rd)
        job["publish_to"] = r["publish_to"]
        if r["limits_file"]:
            job["limits_file"] = r["limits_file"]
        if r["filters_file"]:
            job["filters_file"] = r["filters_file"]
        if r["pdf_report"]:
            job["build_pdf_report"] = True
        jp.write_text(json.dumps(job, indent=2), encoding="utf-8")
        entry = {"name": name, "job": str(jp), "results_dir": str(rd), "index": None, "views": [], "tier": None,
                 "files": [], "status": "ok", "error": None}
        code = 0
        try:
            with contextlib.redirect_stdout(sys.stderr):
                padb_v2.main([str(jp)] + ([] if r["publish_to"] else ["--no-publish"]))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            if code not in (0, None):
                entry["error"] = str(e.code)
        except Exception as e:  # noqa: BLE001 -- one analysis failing must not hide the others
            code, entry["error"] = 1, "%s: %s" % (type(e).__name__, e)
        files = sorted(p for p in rd.glob("*") if p.suffix.lower() in (".html", ".pdf")) if rd.is_dir() else []
        entry["files"] = [str(p) for p in files]
        entry["views"] = sorted({v for v in _VIEWS for p in files
                                 if p.suffix == ".html" and p.stem.endswith("_" + v)
                                 and not any(p.stem.endswith("_" + w) and len(w) > len(v) for w in _VIEWS)})
        idx = rd / "index.html"
        entry["index"] = str(idx) if idx.is_file() else None
        tj = rd / "tier.json"
        if tj.is_file():
            try:
                entry["tier"] = json.loads(tj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                entry["tier"] = None
        if code or not entry["index"]:
            entry["status"] = "failed"
            entry["error"] = entry["error"] or "no index.html was produced"
            result["ok"] = False
        result["analyses"].append(entry)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", nargs="?", help="request JSON file ('-' = stdin); or use --input/--out")
    ap.add_argument("--input")
    ap.add_argument("--out")
    ap.add_argument("--views", help="comma-separated views")
    ap.add_argument("--limits-file")
    ap.add_argument("--filters-file")
    ap.add_argument("--x-col")
    ap.add_argument("--publish-to", default="")
    ap.add_argument("--pdf-report", action="store_true")
    ap.add_argument("--result", help="also write the result JSON to this file")
    a = ap.parse_args(argv)
    try:
        if a.request:
            raw = sys.stdin.read() if a.request == "-" else Path(a.request).read_text(encoding="utf-8-sig")
            req = json.loads(raw)
        elif a.input and a.out:
            req = {"input": a.input, "out": a.out, "publish_to": a.publish_to, "pdf_report": a.pdf_report}
            if a.views:
                req["views"] = [v.strip() for v in a.views.split(",") if v.strip()]
            if a.limits_file:
                req["limits_file"] = a.limits_file
            if a.filters_file:
                req["filters_file"] = a.filters_file
            if a.x_col:
                req["x_col"] = a.x_col
        else:
            ap.error("give a request file, or --input and --out")
        res = analyze(req)
    except (BadRequest, ValueError, OSError) as exc:
        res = {"schema": RESULT_SCHEMA, "ok": False, "analyses": [], "warnings": [], "error": str(exc)}
        print(json.dumps(res, indent=2))
        if a.result:
            Path(a.result).write_text(json.dumps(res, indent=2), encoding="utf-8")
        return 2
    text = json.dumps(res, indent=2)
    print(text)
    if a.result:
        Path(a.result).write_text(text, encoding="utf-8")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
