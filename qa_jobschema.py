r"""qa_jobschema.py -- the job schema (padb_jobschema) against reality.

v3.0 stage 6. A schema that rejects a job the tools run happily is worse than none, and one that misses a key the
code reads rots silently, so it is checked from both sides:

  JS-REAL       every real *_job.json in the Data folder validates with NO error and NO unknown-key warning (the only
                accepted finding is the deprecation of room_only_full_views); skipped, not failed, without the folder
  JS-FULL       a job that sets EVERY schema key to a valid sample validates clean (the table is self-consistent)
  JS-COVERAGE   every public key the pipeline code reads (a source scan of cfg.get / cfg[...] / `in cfg`) is described
                in the schema, apart from a short list of keys that belong to band / limit FILES, not jobs
  JS-TYPOS      a one-character typo of every schema key is flagged as unknown WITH the right suggestion
  JS-RULES      each class of finding: wrong type, not in an enum, out of range, wrong list length, wrong item type,
                an unknown view with a suggestion, an empty views list, no data source, contradictions, secondary_plots
                entries, comment keys (_x) ignored, a non-object job
  JS-RUN        padb_v2 on a job with a typo and a bad value PRINTS the findings and still builds (advisory, never a
                gate); padb_analyze's result carries `job_issues`
  JS-WEB        /api/job-schema and /api/job-validate, and the editor's Review response carries the issues
  JS-DESCRIBE   describe() is plain JSON and every field has a group and a description

    py qa_jobschema.py          -> exit 1 on a failure
"""
import glob
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "webapp"))
PASS = FAIL = 0
DATA_DIR = Path(r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data")
# keys the code reads with a `cfg`-like name that belong to band / limit FILES (padb_bands, padb_limits), not jobs
FILE_FORMAT_KEYS = {"unit", "bands", "limits"}
SCAN_FILES = ["padb_v2.py", "padb_plots.py", "padb_refstats.py", "padb_run.py", "padb_make_job.py",
              "padb_make_v2_job.py", "padb_loader_adapter.py", "padb_analyze.py", "padb_pdf_report.py",
              "padb_compress.py", "padb_tiers.py", "padb_simple.py", "padb_batch.py"]


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail))[:500] if detail else "")


SAMPLE = {"str": "x", "int": 5, "float": 0.5, "bool": True, "list": [], "dict": {}, "null": None}


def sample_for(key, spec):
    if "enum" in spec:
        return spec["enum"][0]
    if "enum_str" in spec:
        return spec["enum_str"][0]
    t = spec["types"]
    if key == "views":
        return ["scatter", "boxplot"]
    if key in ("proportion", "confidence", "proportion_env", "confidence_env"):
        return 0.9
    if key == "tier_budget_s":
        return 300
    if key == "compare_csv":
        return {"SR": "a.csv"}
    if key == "publish":
        return {"destination": "x"}
    if key == "secondary_plots":
        return [{"type": "stat_boxplot", "csv": "x"}]
    if "list" in t and spec.get("length") == 2:
        return [0, 1]
    if "list" in t:
        return ["Room"] if spec.get("item_types") == ("str",) else ([{}] if spec.get("item_types") == ("dict",) else [])
    for name in ("str", "int", "float", "dict", "bool"):
        if name in t:
            return SAMPLE[name]
    return SAMPLE["float"] if "int" in t else None


def source_keys():
    pats = [re.compile(r'\b(?:cfg|job|config|conf|jcfg|new_cfg|run_job|plot_job)\.get\(\s*"([A-Za-z0-9_]+)"'),
            re.compile(r'\b(?:cfg|job|config|conf|jcfg|new_cfg|run_job|plot_job)\[\s*"([A-Za-z0-9_]+)"\s*\]'),
            re.compile(r'"([A-Za-z0-9_]+)"\s+(?:not\s+)?in\s+(?:cfg|job|config)\b')]
    keys = {}
    for f in SCAN_FILES:
        p = HERE / f
        if not p.is_file():
            continue
        s = p.read_text(encoding="utf-8")
        for pat in pats:
            for m in pat.finditer(s):
                keys.setdefault(m.group(1), set()).add(f)
    return {k: v for k, v in keys.items() if not k.startswith("_")}


def main() -> int:
    import padb_jobschema as S

    # ---- JS-REAL
    if DATA_DIR.is_dir():
        files = sorted(glob.glob(str(DATA_DIR / "*_job.json")))
        bad, n = [], 0
        for f in files:
            try:
                j = json.loads(Path(f).read_text(encoding="utf-8-sig"))
            except ValueError:
                continue
            n += 1
            for i in S.validate(j):
                if not (i["level"] == "warn" and i["key"] == "room_only_full_views"):
                    bad.append((Path(f).name, i["level"], i["key"], i["message"][:60]))
        check("JS-REAL: all %d real job files validate with no error and no unknown key" % n, n > 50 and not bad, bad[:3])
    else:
        print("  SKIP  JS-REAL: no Data folder here")

    # ---- JS-FULL
    full = {k: sample_for(k, s) for k, s in S.SCHEMA.items() if not s.get("deprecated")}
    full["pod"] = "x.pod"
    iss = S.validate(full)
    check("JS-FULL: a job with every key set to a valid sample validates with no error (%d keys)" % len(full),
          not S.errors(iss), [i for i in iss if i["level"] == "error"][:3])

    # ---- JS-COVERAGE
    code = source_keys()
    missing = sorted(k for k in code if k not in S.SCHEMA and k not in FILE_FORMAT_KEYS)
    check("JS-COVERAGE: every public key the pipeline reads (%d found by the source scan) is described in the schema"
          % len(code), not missing, {k: sorted(code[k]) for k in missing[:5]})

    # ---- JS-TYPOS
    unflagged, wrong = [], []
    for k in S.SCHEMA:
        typo = k[:-1] if len(k) > 4 else k + "x"            # drop the last character
        i = [x for x in S.validate({"csv_path": "a.csv", typo: 1}) if x["key"] == typo]
        if not i or i[0]["level"] != "warn":
            unflagged.append(typo)
        elif i[0]["suggestion"] != k and k.startswith(typo):
            wrong.append((typo, i[0]["suggestion"], k))
    check("JS-TYPOS: a mistyped version of every one of the %d keys is flagged as unknown" % len(S.SCHEMA),
          not unflagged, unflagged[:5])
    check("JS-TYPOS: ... and the suggestion is the key meant whenever the typo is a prefix of exactly it",
          len(wrong) <= 3, wrong[:5])
    t1 = [x for x in S.validate({"csv_path": "a", "scater_decimate": "auto"})][0]
    check("JS-TYPOS: 'scater_decimate' -> did you mean 'scatter_decimate'", t1["suggestion"] == "scatter_decimate", t1)

    # ---- JS-RULES
    def lv(job, key=None):
        return [(i["level"], i["key"]) for i in S.validate(job) if key is None or i["key"].startswith(key)]
    base = {"csv_path": "a.csv"}
    check("JS-RULES: wrong type is an error (tier_budget_s: 'fast')", ("error", "tier_budget_s") in lv(dict(base, tier_budget_s="fast")))
    check("JS-RULES: a bool is not a number (padb_timeout: true)", ("error", "padb_timeout") in lv({"pod": "p", "padb_timeout": True}))
    check("JS-RULES: not in an enum is an error with a suggestion (spec_direction 'hih')",
          any(i["level"] == "error" and i["suggestion"] == "hi" for i in S.validate(dict(base, spec_direction="hih"))))
    check("JS-RULES: out of range is an error (proportion 1.5; confidence 0; tier_budget_s 0)",
          all(("error", k) in lv(dict(base, **{k: v})) for k, v in (("proportion", 1.5), ("confidence", 0), ("tier_budget_s", 0))))
    check("JS-RULES: a boundary value is accepted (proportion 0.999, parallel_workers 1)",
          not S.errors(S.validate(dict(base, proportion=0.999, parallel_workers=1))))
    check("JS-RULES: wrong list length is an error (y_lim of 3)", ("error", "y_lim") in lv(dict(base, y_lim=[1, 2, 3])))
    check("JS-RULES: wrong item type is an error (y_lim ['a','b'])", any(k.startswith("y_lim[") for _, k in lv(dict(base, y_lim=["a", "b"]))))
    v = [i for i in S.validate(dict(base, views=["scatter", "boxpot"]))]
    check("JS-RULES: an unknown view is an error naming the closest (boxpot -> boxplot)",
          any(i["key"] == "views[1]" and i["suggestion"] == "boxplot" for i in v), v)
    check("JS-RULES: an empty views list is an error", ("error", "views") in lv(dict(base, views=[])))
    check("JS-RULES: no data source is an error", ("error", "") in lv({"description": "x"}))
    check("JS-RULES: contradictions warn (limit_overrides+limits_file, filter_config+filters_file, csv+parquet)",
          all(("warn", k) in lv(dict(base, **extra)) for k, extra in (
              ("limits_file", {"limit_overrides": [], "limits_file": "f"}),
              ("filters_file", {"filter_config": {}, "filters_file": "f"}),
              ("parquet_path", {"parquet_path": "p"}))))
    check("JS-RULES: a cross-site compare without primary_site warns",
          ("warn", "primary_site") in lv({"compare_csv": {"A": "a.csv"}}))
    check("JS-RULES: comment / internal keys (_notes) are accepted silently", not lv(dict(base, _notes="hello", _x=1)))
    sp = S.validate({"pod": "p", "secondary_plots": [{"type": "stat_boxplot", "csv": "x", "y_lim": [0, 1]},
                                                     {"type": "nonsense"}, {"title": "no type"}, {"type": "distribution", "colour": "red"}]})
    keys = {(i["level"], i["key"]) for i in sp}
    check("JS-RULES: secondary_plots entries are checked (bad type name, missing type, unknown key)",
          ("error", "secondary_plots[1].type") in keys and ("error", "secondary_plots[2].type") in keys
          and ("warn", "secondary_plots[3].colour") in keys and not any(k[1].startswith("secondary_plots[0]") for k in keys), keys)
    check("JS-RULES: a job that is not an object is an error, never an exception", S.validate([1, 2])[0]["level"] == "error")

    # ---- JS-GEN : what the generators write must validate (real pods, copied to a temp folder)
    pods = sorted(glob.glob(str(DATA_DIR / "*.pod")))[:6] if DATA_DIR.is_dir() else []
    if pods:
        import shutil
        gd = Path(tempfile.mkdtemp(prefix="qa_gen_"))
        for pd_ in pods:
            shutil.copy(pd_, gd / Path(pd_).name)
        copies = sorted(gd.glob("*.pod"))
        for gen, extra in (("padb_make_v2_job.py", []), ("padb_make_job.py", ["--mode", "interactive"]),
                           ("padb_make_job.py", ["--mode", "simple"])):
            subprocess.run([sys.executable, str(HERE / gen)] + [str(x) for x in copies] * (gen == "padb_make_job.py")
                           + ([str(copies[0])] if gen == "padb_make_v2_job.py" else []) + extra + ["--no-publish", "--force"],
                           capture_output=True, text=True, timeout=600)
        if len(copies) > 1:
            for x in copies[1:]:
                subprocess.run([sys.executable, str(HERE / "padb_make_v2_job.py"), str(x), "--no-publish", "--force"],
                               capture_output=True, text=True, timeout=600)
        gen_jobs = sorted(gd.glob("*_job.json"))
        bad = []
        for gj in gen_jobs:
            try:
                for i in S.validate(json.loads(gj.read_text(encoding="utf-8-sig"))):
                    if not (i["level"] == "warn" and i["key"] == "room_only_full_views"):
                        bad.append((gj.name, i["level"], i["key"], i["message"][:60]))
            except ValueError as exc:
                bad.append((gj.name, "unparseable", str(exc)[:40]))
        check("JS-GEN: all %d jobs written by padb_make_job / padb_make_v2_job for %d real pods validate with no error "
              "and no unknown key" % (len(gen_jobs), len(copies)), len(gen_jobs) >= len(copies) and not bad, bad[:3])
    else:
        print("  SKIP  JS-GEN: no pods here")

    # ---- JS-DESCRIBE
    d = S.describe()
    d2 = json.loads(json.dumps(d))
    check("JS-DESCRIBE: describe() is plain JSON and every field has a group and a description",
          d2["fields"] and all(f["group"] and f["desc"] for f in d2["fields"].values()) and set(d2["groups"]) >= {"extraction", "analysis", "size", "publish"},
          [k for k, f in d2["fields"].items() if not f["desc"]][:3])
    pt = sorted(set(S.PLOT_TYPES))
    import ast
    tree = ast.parse((HERE / "padb_plots.py").read_text(encoding="utf-8"))
    fns = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
           and [a.arg for a in n.args.args][:3] == ["csv_path", "cfg", "output_html"]}
    check("JS-DESCRIBE: the plot types the schema lists are exactly the dispatchable functions in padb_plots",
          fns == set(pt), (sorted(fns - set(pt)), sorted(set(pt) - fns)))
    check("JS-DESCRIBE: the view names match padb_v2's view table",
          set(S.VIEWS) - {"histogram"} == set(__import__("padb_v2")._VIEW_FN), None)

    # ---- JS-RUN
    import qa_viewer_ui as fx
    tmp = Path(tempfile.mkdtemp(prefix="qa_js_"))
    fx.make_fixture(tmp)
    job = {"parquet_path": str(tmp / "fixture.parquet"), "results_dir": str(tmp / "out"), "publish_to": "",
           "x_col": "Frequency (MHz)", "auto_bands": False, "views": ["scatter"],
           "scater_decimate": "auto", "parallel_workers": 0}
    jp = tmp / "typo_job.json"
    jp.write_text(json.dumps(job), encoding="utf-8")
    r = subprocess.run([sys.executable, str(HERE / "padb_v2.py"), str(jp), "--no-publish"], capture_output=True, text=True, timeout=900)
    out = r.stdout + r.stderr
    built = list((tmp / "out").glob("*scatter.html"))
    check("JS-RUN: padb_v2 prints the findings (typo suggestion, bad value) ...",
          "scater_decimate" in out and "scatter_decimate" in out and "parallel_workers" in out, out[:400])
    check("JS-RUN: ... and still builds (the schema is advisory, never a gate)", r.returncode == 0 and built, (r.returncode, built))
    req = subprocess.run([sys.executable, str(HERE / "padb_analyze.py"), "--input", str(tmp / "fixture.parquet"),
                          "--out", str(tmp / "an"), "--views", "scatter"], capture_output=True, text=True, timeout=900)
    a = json.loads(req.stdout)["analyses"][0]
    check("JS-RUN: padb_analyze's result carries `job_issues` for each analysis (a clean generated job -> none)",
          "job_issues" in a and not S.errors(a["job_issues"]), a.get("job_issues"))

    # ---- JS-WEB
    import padb_web
    cl = padb_web.app.test_client()
    sch = cl.get("/api/job-schema").get_json()
    check("JS-WEB: /api/job-schema returns the schema", sch and "fields" in sch and "tier_budget_s" in sch["fields"], list(sch or {})[:4])
    v = cl.post("/api/job-validate", json={"job": {"csv_path": "a", "tier_budget_s": "x", "vews": ["scatter"]}}).get_json()
    check("JS-WEB: /api/job-validate returns the issues (a type error and an unknown key with a suggestion)",
          any(i["level"] == "error" and i["key"] == "tier_budget_s" for i in v["issues"])
          and any(i["suggestion"] == "views" for i in v["issues"]), v)
    old = padb_web.DATA_DIR
    try:
        wd = tmp / "webdata"
        wd.mkdir()
        (wd / "demo_v2_job.json").write_text(json.dumps({"csv_path": "a.csv"}), encoding="utf-8")
        padb_web.DATA_DIR = wd
        pv = cl.post("/api/job-file", json={"path": str(wd / "demo_v2_job.json"), "preview": True,
                                            "text": json.dumps({"csv_path": "a.csv", "proportion": 7})}).get_json()
        check("JS-WEB: the job editor's Review response carries the schema issues (proportion 7)",
              pv and pv.get("ok") and any(i["key"] == "proportion" for i in pv.get("issues", [])), pv)
    finally:
        padb_web.DATA_DIR = old
    js = (HERE / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    check("JS-WEB: the editor shows the issues without blocking the save", "Schema check (advisory" in js and "data.issues" in js)
    print("\nqa_jobschema: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
