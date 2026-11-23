r"""qa_separation.py -- the analysis layer works WITHOUT any extraction/scheduling code (v3.0 requirement).

padbloader must be able to use padb-tools' analysis directly. The strongest proof is not a lint rule but a
run: build the analysis bundle (make_analysis_bundle.py -- it refuses to include extraction code), copy it to a
fresh folder, and run real analyses FROM THAT FOLDER in a different working directory, with PYTHONPATH
cleared. A missing file or a hidden dependency on padb_run / padb_batch / the scheduler / the webapp then
fails the run.

  SEP-BUNDLE  the bundle builds, holds none of the extraction modules, and the builder REFUSES when an
              analysis file starts importing one (teeth: tested on a synthetic tree)
  SEP-ISOLATED extraction modules are really not importable from the bundle folder
  SEP-RUN     padb_analyze, run from the bundle, turns each input kind into a working analysis:
              a padbloader parquet, a padb-tools parquet and a PADB scatter CSV -> ok, an index.html and the
              views; a limits file is applied (index banner); stdout is ONE json document (progress on stderr)
  SEP-REQUEST a malformed request is exit 2 with a message (never a traceback), unknown fields/schemas/
              missing files are rejected

  py qa_separation.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PASS = FAIL = 0


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail)) if detail else "")


def run_bundle(bundle: Path, cwd: Path, args, timeout=900):
    """Run padb_analyze.py FROM the bundle, in another cwd, with no PYTHONPATH."""
    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(bundle / "padb_analyze.py")] + [str(a) for a in args],
                          cwd=str(cwd), capture_output=True, text=True, env=env, timeout=timeout)


def main() -> int:
    import make_analysis_bundle as mb
    import qa_loader_adapter as la
    import qa_viewer_ui as fx

    tmp = Path(tempfile.mkdtemp(prefix="qa_sep_"))

    # ---------------- SEP-BUNDLE ----------------
    bundle = tmp / "bundle"
    info = mb.build_bundle(bundle)
    names = {Path(f).stem for f in info["files"]}
    check("SEP-BUNDLE: the bundle builds (%d files) and holds NO extraction/scheduling module" % len(info["files"]),
          not (names & mb.EXTRACTION), sorted(names & mb.EXTRACTION))
    check("SEP-BUNDLE: it carries the entry point, the engine and the plot builders",
          {"padb_analyze", "padb_v2", "padb_plots", "padb_core", "padb_names"} <= {Path(f).parts[0].replace(".py", "") for f in info["files"]},
          sorted(info["files"]))
    check("SEP-BUNDLE: the manifest hashes every file", all(len(h) == 64 for h in info["files"].values()))
    # teeth: the builder refuses when an analysis file imports extraction (synthetic tree)
    fake = tmp / "fake"
    fake.mkdir()
    (fake / "padb_analyze.py").write_text("import padb_v2\n")
    (fake / "padb_v2.py").write_text("def f():\n    import padb_run\n")        # a LAZY import must be caught too
    (fake / "padb_run.py").write_text("x = 1\n")
    try:
        mb.closure(["padb_analyze.py"], fake)
        refused = False
    except mb.BoundaryError as exc:
        refused = "padb_run" in str(exc)
    check("SEP-BUNDLE teeth: the builder REFUSES a closure that reaches padb_run (even via a lazy import)", refused)
    (fake / "padb_v2.py").write_text("import json\n")
    check("SEP-BUNDLE teeth: and accepts the same tree once the import is gone",
          mb.closure(["padb_analyze.py"], fake) == ["padb_analyze.py", "padb_v2.py"])

    # ---------------- SEP-ISOLATED ----------------
    work = tmp / "work"            # a different working directory than the repo
    work.mkdir()
    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    for mod in ("padb_run", "padb_batch", "padb_scheduler", "padb_make_v2_job", "padb_config"):
        r = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, r'%s'); import %s" % (bundle, mod)],
                           cwd=str(work), capture_output=True, text=True, env=env)
        check("SEP-ISOLATED: %s is NOT importable from the bundle folder" % mod,
              r.returncode != 0 and "No module named" in r.stderr and mod in r.stderr, r.stderr[-120:])

    # ---------------- SEP-RUN ----------------
    inputs = tmp / "inputs"
    inputs.mkdir()
    pl = la.make_padbloader_parquet(inputs / "padbloader.parquet")
    fxd = tmp / "fx"
    fxd.mkdir()
    df = fx.make_fixture(fxd)
    pt = fxd / "fixture.parquet"
    csv = inputs / "scatter.csv"
    df.to_csv(csv, index=False)
    cases = {
        "padbloader parquet": (pl, ["scatter", "summary"]),
        "padb-tools parquet": (pt, ["scatter", "stat_summary"]),
        "PADB scatter CSV": (csv, ["scatter"]),
    }
    for label, (src, views) in cases.items():
        out = tmp / ("out_" + label.split()[0])
        req = tmp / ("req_" + label.split()[0] + ".json")
        req.write_text(json.dumps({"schema": "padb-analysis-request/1", "input": str(src), "out": str(out), "views": views}))
        res_file = tmp / ("res_" + label.split()[0] + ".json")
        r = run_bundle(bundle, work, [req, "--result", res_file])
        try:
            res = json.loads(r.stdout)
        except ValueError:
            res = {}
        ok = r.returncode == 0 and res.get("ok") is True and res.get("schema") == "padb-analysis-result/1"
        check("SEP-RUN[%s]: run from the bundle (cwd elsewhere, no PYTHONPATH) -> exit 0, ok, valid result JSON" % label,
              ok, (r.returncode, r.stderr[-300:], r.stdout[:200]))
        a = (res.get("analyses") or [{}])[0]
        check("SEP-RUN[%s]: an index.html and the requested views were produced" % label,
              bool(a.get("index")) and Path(a["index"]).is_file() and set(views) <= set(a.get("views", [])),
              (a.get("views"), a.get("index")))
        check("SEP-RUN[%s]: the --result file == stdout (one JSON document; progress went to stderr)" % label,
              res_file.is_file() and json.loads(res_file.read_text()) == res and r.stderr.strip() != "", "")

    # limits file applied end to end
    lim = tmp / "limits.json"
    lim.write_text(json.dumps({"unit": "MHz", "limits": [{"lo": 0, "hi": 400, "upper": -90}]}))
    out = tmp / "out_limits"
    r = run_bundle(bundle, work, ["--input", pt, "--out", out, "--views", "scatter", "--limits-file", lim])
    res = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    idx = (res.get("analyses") or [{}])[0].get("index")
    body = Path(idx).read_text(encoding="utf-8") if idx and Path(idx).is_file() else ""
    check("SEP-RUN: a limits file is applied from the bundle (index shows 'Limit overrides active')",
          r.returncode == 0 and "Limit overrides active" in body, (r.returncode, r.stderr[-200:]))
    r0 = run_bundle(bundle, work, ["--input", pt, "--out", tmp / "out_nolimits", "--views", "scatter"])
    res0 = json.loads(r0.stdout) if r0.stdout.strip().startswith("{") else {}
    idx0 = (res0.get("analyses") or [{}])[0].get("index")
    check("SEP-RUN teeth: without a limits file the banner is absent",
          idx0 and "Limit overrides active" not in Path(idx0).read_text(encoding="utf-8"))

    # ---------------- SEP-REQUEST ----------------
    def bad(label, payload, want):
        rq = tmp / "bad.json"
        rq.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        r = run_bundle(bundle, work, [rq])
        try:
            res = json.loads(r.stdout)
        except ValueError:
            res = {}
        check("SEP-REQUEST: %s -> exit 2 with a message, no traceback" % label,
              r.returncode == 2 and res.get("ok") is False and want in res.get("error", "") and "Traceback" not in r.stderr,
              (r.returncode, res.get("error"), r.stderr[-120:]))

    bad("missing input", {"out": str(tmp / "o")}, "`input` is required")
    bad("missing out", {"input": str(pt)}, "`out` is required")
    bad("input not found", {"input": str(tmp / "nope.parquet"), "out": str(tmp / "o")}, "input not found")
    bad("unsupported schema", {"schema": "padb-analysis-request/9", "input": str(pt), "out": str(tmp / "o")}, "unsupported schema")
    bad("unknown field", {"input": str(pt), "out": str(tmp / "o"), "colour": "red"}, "unknown request field")
    bad("empty views", {"input": str(pt), "out": str(tmp / "o"), "views": []}, "non-empty list")
    bad("wrong input type", {"input": str(Path(__file__)), "out": str(tmp / "o")}, ".parquet or .csv")
    bad("limits file missing", {"input": str(pt), "out": str(tmp / "o"), "limits_file": str(tmp / "no.json")}, "limits_file not found")

    print("\nqa_separation: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
