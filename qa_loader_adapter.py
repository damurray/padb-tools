"""qa_loader_adapter.py -- QA for padb_loader_adapter.py and its webapp drop-zone flow.

Synthetic, seeded padbloader-style parquet (long format, c_* / r_* columns, label/units
in the schema metadata), two algorithms: one narrow sweep, one WIDE sweep.

  ADAPTER   list_algorithms; parquet vs csv output hold the same data; the generated
            job loads through the real loader; wide sweeps default to scatter+summary
            and narrow ones carry no "views"; high-cardinality condition columns stay
            out of Group; existing job files are kept unless force; "{algo}" publish
            placeholder; a real padb_v2 build from the generated job produces a page.
  ROUTES    Flask test client: /api/upload-parquet (multipart, in-place path, junk
            rejected) and /api/generate-parquet-jobs (jobs written, publish rules,
            skip-existing, validation errors).
  UI        (browser tier, honest exit-3-style SKIP when Playwright/Chromium is
            unavailable) a real drop of the parquet onto the drop zone lists the
            algorithms, Generate writes the jobs, and the jobs table shows them.
  TEETH     the wide-sweep default and the force guard are run against broken
            variants and must be caught.

Run:  py qa_loader_adapter.py        (exit 1 on FAIL; browser tier skipped -> still 0)
"""
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import padb_loader_adapter as ad

HERE = Path(__file__).resolve().parent
_PASS: list = []
_FAIL: list = []
_SKIP: list = []


def check(desc, cond, detail=""):
    if cond:
        _PASS.append(desc)
        print("  PASS  " + desc)
    else:
        _FAIL.append(desc)
        print("  FAIL  " + desc + (" -- " + detail if detail else ""))


def make_padbloader_parquet(path: Path, seed=3) -> Path:
    """Two algorithms: 'Narrow Test' (12 freqs) and 'Wide Test' (400 offsets).  'Narrow Test'
    ALSO emits a second result in other units with a NULL units column (like the harmonics
    'Carrier Power (dBm)'): mixing it into the dBc plot was a real bug."""
    rnd = np.random.default_rng(seed)
    rows = []
    t0 = pd.Timestamp("2026-11-01 06:00:00")
    serials = ["US100001", "US100002", "MY200001"]
    for algo, units, xcol, xs, lim in (
            ("Narrow Test", "dBc", "c_frequency", np.linspace(100, 1200, 12), -60.0),
            ("Wide Test", "dBc/Hz", "c_frequency_offset", np.logspace(1, 6, 400), None)):
        for si, sn in enumerate(serials):
            for port in ("RF1", "RF2"):
                for xi, x in enumerate(xs):
                    r = {"algorithm": algo, "result": algo, "units": units, "device": "SG6311A",
                         "serial": sn, "run_status": "P" if si < 2 else "F",
                         "run_datetime": t0 + pd.Timedelta(hours=si),
                         "value": float(-80 + rnd.normal(0, 3)),
                         "limit_upper": lim, "limit_lower": None,
                         "c_frequency": float(x) if xcol == "c_frequency" else 1000.0,
                         "c_frequency_offset": float(x) if xcol == "c_frequency_offset" else None,
                         "c_port": port, "c_mode": "0",
                         # per-row identifier: must be kept OUT of Group (cardinality)
                         "c_etp_id": "etp-%s-%d-%s-%d" % (algo[:1], si, port, xi),
                         "r_site": "SR", "r_process_center": "SR"}
                    rows.append(r)
    for si, sn in enumerate(serials):
        for xi, x in enumerate(np.linspace(100, 1200, 12)):
            rows.append({"algorithm": "Narrow Test", "result": "Narrow Test:Carrier Power (dBm)",
                         "units": None, "device": "SG6311A", "serial": sn, "run_status": "P",
                         "run_datetime": t0, "value": float(10 + rnd.normal(0, .3)),
                         "limit_upper": None, "limit_lower": None, "c_frequency": float(x),
                         "c_frequency_offset": None, "c_port": "RF1", "c_mode": "0",
                         "c_etp_id": "etp-c-%d-%d" % (si, xi), "r_site": "SR", "r_process_center": "SR"})
    df = pd.DataFrame(rows)
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    md = dict(tbl.schema.metadata or {})
    md.update({b"label.c_frequency": b"Frequency", b"units.c_frequency": b"MHz",
               b"label.c_frequency_offset": b"Frequency Offset", b"units.c_frequency_offset": b"Hz",
               b"label.c_port": b"Port", b"label.c_mode": b"Mode"})
    pq.write_table(tbl.replace_schema_metadata(md), str(path))
    return path


def make_padb_parquet(path: Path, seed=9) -> Path:
    """padb-tools' OWN parquet (the sidecar of a big dataset): PADB scatter-CSV columns,
    including an all-null limit column (parquet type null, like the real EP5 file)."""
    rnd = np.random.default_rng(seed)
    xs = np.logspace(0, 6, 700)
    rows = []
    for sn in ("MY1", "MY2", "MY3"):
        for mode in ("0", "1"):
            for x in xs[::2]:
                rows.append({"Analysis Type": "ScatterPlot", "Model(s)": "SG6311A",
                             "Algorithm -> Result": "Test PN-->Test PN (dBc/Hz)", "Units": "dBc/Hz",
                             "Group": "Mode: %s  Frequency (MHz):  1000  Test Event Status: P" % mode,
                             "Frequency Offset (Hz)": float(x),
                             "Test PN (dBc/Hz)": float(-100 + rnd.normal(0, 2)),
                             "Serial Number": sn, "Upper Limit (<=)": None})
    # one row whose value is missing: must not count as usable
    rows.append({"Analysis Type": "ScatterPlot", "Model(s)": "SG6311A", "Algorithm -> Result": "x",
                 "Units": "dBc/Hz", "Group": "Mode: 0", "Frequency Offset (Hz)": 5.0,
                 "Test PN (dBc/Hz)": None, "Serial Number": "MY1", "Upper Limit (<=)": None})
    df = pd.DataFrame(rows)
    df["Upper Limit (<=)"] = pa.nulls(len(df)).to_pandas()
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False,
                                        schema=pa.schema([
                                            ("Analysis Type", pa.string()), ("Model(s)", pa.string()),
                                            ("Algorithm -> Result", pa.string()), ("Units", pa.string()),
                                            ("Group", pa.string()), ("Frequency Offset (Hz)", pa.float64()),
                                            ("Test PN (dBc/Hz)", pa.float64()), ("Serial Number", pa.string()),
                                            ("Upper Limit (<=)", pa.null())])), str(path))
    return path


def make_steps_parquet(path: Path, steps=("Room", "85.0 Deg C", "0.0 Deg C"), seed=5) -> Path:
    """One narrow algorithm measured at several TEST STEPS (temperatures)."""
    rnd = np.random.default_rng(seed)
    rows = []
    t0 = pd.Timestamp("2026-11-01")
    for sn in ("US1", "US2", "US3", "US4"):
        for si, step in enumerate(steps):
            for x in np.linspace(100, 500, 6):
                rows.append({"algorithm": "Temp Test", "result": "Temp Test", "units": "dBc",
                             "device": "SG6311A", "serial": sn, "step": step, "run_status": "P",
                             "run_datetime": t0, "value": float(-70 + rnd.normal(0, 1) + si),
                             "limit_upper": -60.0, "limit_lower": None, "c_frequency": float(x),
                             "c_frequency_offset": None, "c_port": "RF1"})
    tbl = pa.Table.from_pandas(pd.DataFrame(rows), preserve_index=False)
    tbl = tbl.replace_schema_metadata({b"label.c_frequency": b"Frequency", b"units.c_frequency": b"MHz"})
    pq.write_table(tbl, str(path))
    return path


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()):
        yield


# ---------------------------------------------------------------------------
def test_adapter(td: Path):
    print("\n[ADAPTER]")
    src = make_padbloader_parquet(td / "padbloader_20260101_000000.parquet")
    algos = {a["algorithm"]: a for a in ad.list_algorithms(str(src))}
    check("list_algorithms: both algorithms with rows/units/limits/x/wide flag",
          set(algos) == {"Narrow Test", "Wide Test", "Narrow Test:Carrier Power (dBm)"}
          and algos["Narrow Test"]["rows"] == 72 and algos["Narrow Test"]["units"] == "dBc"
          and algos["Narrow Test:Carrier Power (dBm)"]["units"] == "dBm"
          and algos["Narrow Test:Carrier Power (dBm)"]["rows"] == 36 and algos["Narrow Test"]["has_limits"]
          and algos["Narrow Test"]["x_col"] == "c_frequency" and not algos["Narrow Test"]["wide"]
          and algos["Wide Test"]["x_col"] == "c_frequency_offset" and algos["Wide Test"]["wide"]
          and algos["Wide Test"]["n_x"] == 400 and not algos["Wide Test"]["has_limits"],
          str(algos))
    out, jobs = td / "res", td / "jobs"
    with quiet():
        made = ad.convert(str(src), str(out), str(jobs), publish_to="", log=lambda *a: None,
                          combine_results=False)
    check("split mode: one job per result stream (3), never one job mixing dBc and dBm", len(made) == 3, str(len(made)))
    by = {m["algorithm"]: m for m in made}
    jn = json.loads(Path(by["Narrow Test"]["job_path"]).read_text())
    jw = json.loads(Path(by["Wide Test"]["job_path"]).read_text())
    check("narrow sweep: job has parquet_path, no 'views' override",
          "parquet_path" in jn and "csv_path" not in jn and "views" not in jn
          and Path(jn["parquet_path"]).is_file())
    check("wide sweep: job defaults to scatter + summary only (no boxplot/stat_summary/reference)",
          jw.get("views") == ["scatter", "summary"], str(jw.get("views")))
    # units must never be mixed: the dBc datum holds ONLY dBc values, the dBm one only dBm
    dbc = pq.read_table(jn["parquet_path"]).to_pandas()
    jc = json.loads(Path(by["Narrow Test:Carrier Power (dBm)"]["job_path"]).read_text())
    dbm = pq.read_table(jc["parquet_path"]).to_pandas()
    vcol_c = [c for c in dbc.columns if c.startswith("Narrow Test")][0]
    vcol_m = [c for c in dbm.columns if c.startswith("Narrow Test")][0]
    check("dBc datum has only the dBc rows (72, all < -30); dBm datum only the dBm rows (36, all > 0)",
          len(dbc) == 72 and dbc[vcol_c].max() < -30 and len(dbm) == 36 and dbm[vcol_m].min() > 0
          and set(dbm["Units"]) == {"dBm"} and set(dbc["Units"]) == {"dBc"})
    check("dBm datum: y_label is the label (units not doubled), no spec-limit column",
          jc["y_label"] == "Narrow Test:Carrier Power (dBm)" and "Upper Limit" not in dbm.columns, jc["y_label"])
    # content: the per-row id column must not be in Group; conditions are
    ptab = pq.read_table(jn["parquet_path"]).to_pandas()
    gw = pq.read_table(jw["parquet_path"]).to_pandas()["Group"].iloc[0]
    check("Group carries conditions but not the high-cardinality per-row id (2400 distinct > 100)",
          "Port: RF1" in gw and "Mode: 0" in gw and "etp" not in gw.lower(), gw)
    check("x column labelled from schema metadata (Frequency (MHz)) and limits present",
          "Frequency (MHz)" in ptab.columns and "Upper Limit" in ptab.columns)
    # parquet vs csv: identical data once loaded through the real loader
    out2 = td / "res_csv"
    with quiet():
        mc = ad.convert(str(src), str(out2), str(td / "jobs_csv"), fmt="csv", publish_to="",
                        algorithms=["Narrow Test"], log=lambda *a: None)
    import padb_v2 as v2
    cfg = {"x_col": "Frequency (MHz)"}
    a = v2.load_scatter(Path(mc[0]["data_path"]), cfg).reset_index(drop=True)
    b = v2.load_scatter(Path(jn["parquet_path"]), cfg).reset_index(drop=True)
    check("parquet output loads identically to the csv output (real loader)",
          list(a.columns) == list(b.columns) and a.equals(b))
    check("loaded data is complete: all 72 narrow rows, 3 serials",
          len(b) == 72 and b["Serial"].nunique() == 3)
    # existing job kept unless force
    Path(by["Narrow Test"]["job_path"]).write_text(json.dumps({"hand": "tuned"}))
    with quiet():
        m2 = ad.convert(str(src), str(out), str(jobs), publish_to="", algorithms=["Narrow Test"],
                        log=lambda *a: None)
    kept = json.loads(Path(by["Narrow Test"]["job_path"]).read_text()) == {"hand": "tuned"}
    check("re-run keeps an existing (hand-tuned) job file", m2[0]["status"] == "exists" and kept)
    with quiet():
        m3 = ad.convert(str(src), str(out), str(jobs), publish_to="", algorithms=["Narrow Test"],
                        force=True, log=lambda *a: None)
    check("force overwrites it",
          m3[0]["status"] == "ok" and "parquet_path" in json.loads(Path(m3[0]["job_path"]).read_text()))
    # {algo} publish placeholder
    with quiet():
        mp = ad.convert(str(src), str(td / "r3"), str(td / "j3"), publish_to=r"\\srv\share\Mod\x_{algo}",
                        log=lambda *a: None)
    pubs = sorted(json.loads(Path(m["job_path"]).read_text())["publish_to"] for m in mp)
    check("{algo} expands per job so publishes never collide (combined: one job per algorithm)",
          pubs == [r"\\srv\share\Mod\x_Narrow_Test", r"\\srv\share\Mod\x_Wide_Test"], str(pubs))
    # combined mode (default): ONE job per algorithm, results share a dataset with a Result filter
    out4, jobs4 = td / "res4", td / "jobs4"
    with quiet():
        mcb = ad.convert(str(src), str(out4), str(jobs4), publish_to="", log=lambda *a: None)
    byc = {m["algorithm"]: m for m in mcb}
    jcn = json.loads(Path(byc["Narrow Test"]["job_path"]).read_text())
    jcw = json.loads(Path(byc["Wide Test"]["job_path"]).read_text())
    cmb = pq.read_table(jcn["parquet_path"]).to_pandas()
    check("combined mode: 2 jobs (one per algorithm); the multi-result one carries result_default = main result",
          len(mcb) == 2 and jcn.get("result_default") == "Narrow Test (dBc)" and "result_default" not in jcw,
          str(jcn.get("result_default")))
    check("combined mode: Result key in Group, per-row Units (dBc and dBm), 108 rows, value column unit-free",
          len(cmb) == 108 and set(cmb["Units"]) == {"dBc", "dBm"}
          and cmb["Group"].str.startswith("Result: ").all()
          and set(cmb["Group"].str.extract(r"^Result: (.*?)  ")[0])
          == {"Narrow Test (dBc)", "Narrow Test:Carrier Power (dBm)"}
          and "Narrow Test" in cmb.columns, str(list(cmb.columns)))
    check("combined mode: a single-result algorithm gets no Result key",
          not pq.read_table(jcw["parquet_path"]).to_pandas()["Group"].str.contains("Result:").any())
    with quiet():
        msel = ad.convert(str(src), str(td / "res5"), str(td / "jobs5"), publish_to="",
                          algorithms=["Narrow Test"], log=lambda *a: None)
    check("selecting only the dBc datum does NOT drag in the algorithm's dBm results (exact datum match)",
          len(msel) == 1 and len(pq.read_table(json.loads(Path(msel[0]["job_path"]).read_text())["parquet_path"])) == 72)
    # a real build from the generated job
    jobp = Path(by["Narrow Test"]["job_path"])
    r = subprocess.run([sys.executable, str(HERE / "padb_v2.py"), str(jobp), "--no-publish"],
                       capture_output=True, text=True, cwd=str(HERE), timeout=600)
    res_dir = jobp.parent / json.loads(jobp.read_text())["results_dir"]
    check("padb_v2 builds the plots from the generated parquet_path job",
          r.returncode == 0 and any(res_dir.glob("*scatter*.html")), (r.stdout + r.stderr)[-300:])
    return src


def test_views_and_steps(td: Path, src: Path):
    print("\n[VIEWS + TEST STEPS]")
    stp = make_steps_parquet(td / "steps.parquet")
    check("list_algorithms: n_steps = 3 for the multi-temperature file, 1 for Room-only data",
          ad.list_algorithms(str(stp))[0]["n_steps"] == 3
          and all(a["n_steps"] == 1 for a in ad.list_algorithms(str(src))))
    with quiet():
        m = ad.convert(str(stp), str(td / "sres"), str(td / "sjobs"), publish_to="",
                       views=ad.ALL_VIEWS, log=lambda *a: None)
    job = json.loads(Path(m[0]["job_path"]).read_text())
    tbl = pq.read_table(job["parquet_path"]).to_pandas()
    check("multi-step data: all 7 views kept (environmental ones included) in job['views']",
          job["views"] == ad.ALL_VIEWS, str(job["views"]))
    check("Test Step written in the loader's native form (Room / NN.N Deg C)",
          set(tbl["Test Step"]) == {"Room", "85.0 Deg C", "0.0 Deg C"}, str(set(tbl["Test Step"])))
    import padb_v2 as v2
    df = v2.load_scatter(Path(job["parquet_path"]), {"x_col": "Frequency (MHz)"})
    check("the real loader sees three temperatures (so env views are meaningful)",
          set(df["Temperature"]) == {"Room", "85°C", "0°C"}, str(set(df["Temperature"])))
    with quiet():
        m1 = ad.convert(str(src), str(td / "sres1"), str(td / "sjobs1"), publish_to="",
                        algorithms=["Narrow Test"], views=ad.ALL_VIEWS, log=lambda *a: None)
    j1 = json.loads(Path(m1[0]["job_path"]).read_text())
    check("single-temperature data: environmental views dropped, with a note",
          j1["views"] == [v for v in ad.ALL_VIEWS if v not in ad.ENV_VIEWS] and "dropped" in m1[0]["note"],
          str(j1["views"]) + m1[0]["note"])
    with quiet():
        m2 = ad.convert(str(src), str(td / "sres2"), str(td / "sjobs2"), publish_to="",
                        algorithms=["Narrow Test"], views=["scatter", "summary"], log=lambda *a: None)
    check("explicit views list is written verbatim (user's choice wins over the wide default)",
          json.loads(Path(m2[0]["job_path"]).read_text())["views"] == ["scatter", "summary"])
    raised = []
    for bad in (["scatter", "nonsense"], []):
        try:
            with quiet():
                ad.convert(str(src), str(td / "sres3"), str(td / "sjobs3"), publish_to="", views=bad,
                           log=lambda *a: None)
        except ValueError:
            raised.append(bad)
    check("an unknown view / empty list is rejected (ValueError)", len(raised) == 2)
    # an unrecognised step label ('Hot') must not silently count as a temperature
    hot = make_steps_parquet(td / "hot.parquet", steps=("Room", "Hot"))
    assert ad.list_algorithms(str(hot))[0]["n_steps"] == 1
    with quiet():
        mh = ad.convert(str(hot), str(td / "hres"), str(td / "hjobs"), publish_to="", views=ad.ALL_VIEWS,
                        log=lambda *a: None)
    jh = json.loads(Path(mh[0]["job_path"]).read_text())
    check("unrecognised step name ('Hot'): warned, counted as Room (not offered as a temperature), env dropped",
          "WARNING" in mh[0]["note"] and "Hot" in mh[0]["note"]
          and not any(v in jh["views"] for v in ad.ENV_VIEWS), mh[0]["note"])
    # all seven views really build from the multi-step job
    r = subprocess.run([sys.executable, str(HERE / "padb_v2.py"), m[0]["job_path"], "--no-publish"],
                       capture_output=True, text=True, cwd=str(HERE), timeout=900)
    rd = Path(m[0]["job_path"]).parent / job["results_dir"]
    names = {p.name for p in rd.glob("*.html") if p.name != "index.html"}
    check("padb_v2 builds all 7 views (incl. distribution + env_coverage) from the multi-step job",
          r.returncode == 0 and len(names) == 7
          and any("distribution" in n for n in names) and any("env_coverage" in n for n in names),
          (r.stdout + r.stderr)[-300:])


def test_padb_parquet(td: Path, src: Path):
    """padb-tools' OWN parquet (the sidecar for large files) must be accepted too: sniffed,
    described, turned into a job that points at it IN PLACE, loaded and built."""
    print("\n[PADB-TOOLS PARQUET]")
    pp_ = make_padb_parquet(td / "Test_PN_sidecar.parquet")
    pq.write_table(pa.table({"a": [1, 2], "b": ["x", "y"]}), str(td / "junk.parquet"))
    check("sniff: padb-tools table -> 'padb', padbloader extract -> 'padbloader', anything else -> None",
          ad.sniff_parquet(str(pp_)) == "padb" and ad.sniff_parquet(str(src)) == "padbloader"
          and ad.sniff_parquet(str(td / "junk.parquet")) is None)
    d = ad.describe_padb_parquet(str(pp_))[0]
    check("describe: usable rows exclude the row with no value; value column, x, units, no limits, wide",
          d["rows"] == 2100 and d["algorithm"] == "Test PN (dBc/Hz)" and d["x_col"] == "Frequency Offset (Hz)"
          and d["units"] == "dBc/Hz" and d["has_limits"] is False and d["wide"] and d["n_steps"] == 1, str(d))
    jobs = td / "pjobs"
    with quiet():
        m = ad.convert_padb_parquet(str(pp_), str(jobs), publish_to="")
    job = json.loads(Path(m[0]["job_path"]).read_text())
    check("job points at the parquet IN PLACE (no copy), wide sweep -> scatter+summary, labels set",
          job["parquet_path"] == str(pp_.resolve()) and job["views"] == ["scatter", "summary"]
          and job["x_col"] == "Frequency Offset (Hz)" and job["x_unit"] == "Hz"
          and job["y_label"] == "Test PN (dBc/Hz)" and "csv_path" not in job, str(job))
    with quiet():
        m2 = ad.convert_padb_parquet(str(pp_), str(jobs), publish_to="")
    check("a second run keeps the existing job unless force", m2[0]["status"] == "exists")
    with quiet():
        m3 = ad.convert_padb_parquet(str(pp_), str(jobs), publish_to="", force=True,
                                     views=["scatter", "boxplot", "env_coverage"])
    j3 = json.loads(Path(m3[0]["job_path"]).read_text())
    check("explicit views honoured; environmental views dropped for single-temperature data",
          j3["views"] == ["scatter", "boxplot"] and "dropped" in m3[0]["note"], str(j3["views"]))
    import padb_v2 as v2
    df = v2.load_scatter(pp_, {"x_col": "Frequency Offset (Hz)"})
    check("the loader reads it: 2100 usable rows, null-typed limit column harmless, 3 serials",
          len(df) == 2100 and df["Serial"].nunique() == 3 and df["Upper_Limit"].isna().all())
    j3["views"] = ["scatter"]
    j3["results_dir"] = str(td / "pout")
    Path(m3[0]["job_path"]).write_text(json.dumps(j3))
    r = subprocess.run([sys.executable, str(HERE / "padb_v2.py"), m3[0]["job_path"], "--no-publish"],
                       capture_output=True, text=True, cwd=str(HERE), timeout=600)
    check("padb_v2 builds a scatter from the padb-tools parquet; the source parquet is untouched",
          r.returncode == 0 and any((td / "pout").glob("*scatter.html")) and pp_.is_file(),
          (r.stdout + r.stderr)[-250:])
    # CLI
    rc = subprocess.run([sys.executable, str(HERE / "padb_loader_adapter.py"), str(pp_), "--list"],
                        capture_output=True, text=True, cwd=str(HERE))
    check("CLI --list works on a padb-tools parquet", rc.returncode == 0 and "2100 usable rows" in rc.stdout,
          rc.stdout + rc.stderr)


def test_teeth(td: Path, src: Path):
    print("\n[TEETH]")
    # (1) if WIDE_X were huge, the wide job would lose its views override -> check catches it
    old = ad.WIDE_X
    ad.WIDE_X = 10 ** 9
    try:
        with quiet():
            m = ad.convert(str(src), str(td / "t1"), str(td / "tj1"), publish_to="",
                           algorithms=["Wide Test"], log=lambda *a: None)
        v = json.loads(Path(m[0]["job_path"]).read_text()).get("views")
    finally:
        ad.WIDE_X = old
    check("teeth: with the wide-sweep rule disabled the views override disappears (test would fail)",
          v is None)
    # (2) a job file that already exists is overwritten when the guard is bypassed
    with quiet():
        m = ad.convert(str(src), str(td / "t2"), str(td / "tj2"), publish_to="", algorithms=["Narrow Test"],
                       log=lambda *a: None)
    Path(m[0]["job_path"]).write_text("{}")
    with quiet():
        ad.convert(str(src), str(td / "t2"), str(td / "tj2"), publish_to="", algorithms=["Narrow Test"],
                   force=True, log=lambda *a: None)
    check("teeth: force really does overwrite (so the keep-existing check is meaningful)",
          Path(m[0]["job_path"]).read_text() != "{}")


# ---------------------------------------------------------------------------
def test_routes(td: Path, src: Path):
    print("\n[ROUTES]")
    sys.path.insert(0, str(HERE / "webapp"))
    import padb_web
    data = td / "data"
    data.mkdir()
    old = padb_web.DATA_DIR
    padb_web.DATA_DIR = data
    try:
        c = padb_web.app.test_client()
        with open(src, "rb") as fh:
            r = c.post("/api/upload-parquet", data={"parquet": (fh, src.name)},
                       content_type="multipart/form-data")
        j = r.get_json()
        check("upload-parquet (multipart): staged under DATA_DIR/padbloader_parquet, algorithms listed",
              r.status_code == 200 and (data / "padbloader_parquet" / src.name).is_file()
              and {a["algorithm"] for a in j["analytics"]}
              == {"Narrow Test", "Wide Test", "Narrow Test:Carrier Power (dBm)"}
              and any(a["wide"] for a in j["analytics"]), str(j)[:200])
        r = c.post("/api/upload-parquet", json={"path": str(src)})
        check("upload-parquet (path): used in place, nothing copied",
              r.status_code == 200 and r.get_json()["parquet_path"] == str(src))
        r = c.post("/api/upload-parquet", data={"parquet": (io.BytesIO(b"junk"), "x.parquet")},
                   content_type="multipart/form-data")
        check("upload-parquet: a non-parquet file is rejected with 400", r.status_code == 400)
        r = c.post("/api/upload-parquet", data={"parquet": (io.BytesIO(b"x"), "x.csv")},
                   content_type="multipart/form-data")
        check("upload-parquet: wrong extension rejected", r.status_code == 400)
        r = c.post("/api/upload-parquet", json={"path": str(td / "nope.parquet")})
        check("upload-parquet: missing path rejected", r.status_code == 400)

        r = c.post("/api/generate-parquet-jobs", json={"parquet_path": str(src), "algorithms": []})
        check("generate: no algorithm selected -> 400", r.status_code == 400)
        r = c.post("/api/generate-parquet-jobs",
                   json={"parquet_path": str(src), "algorithms": ["Narrow Test", "Wide Test"]})
        j = r.get_json()
        names = sorted(x["name"] for x in j["jobs"]) if r.status_code == 200 else []
        check("generate: one *_v2_job.json per ticked algorithm in DATA_DIR, local-only (no publish)",
              r.status_code == 200 and len(names) == 2
              and all((data / n).is_file() for n in names)
              and all(json.loads((data / n).read_text())["publish_to"] == "" for n in names), str(j)[:300])
        r = c.post("/api/generate-parquet-jobs",
                   json={"parquet_path": str(src), "algorithms": ["Narrow Test"]})
        check("generate: re-run without force reports the job as skipped, file untouched",
              r.status_code == 200 and "skipped" in r.get_json()["stdout"])
        r = c.post("/api/generate-parquet-jobs",
                   json={"parquet_path": str(src), "algorithms": ["Narrow Test", "Wide Test"],
                         "module": "ModX", "force": True})
        pubs = sorted(json.loads((data / x["name"]).read_text())["publish_to"] for x in r.get_json()["jobs"])
        check("generate: Folder name -> <Interactive root>\\<module>\\<stem>_<algo>, one per algorithm",
              len(pubs) == 2 and all("PADB-Interactive" in p and "\\ModX\\" in p for p in pubs)
              and pubs[0] != pubs[1], str(pubs))
        r = c.post("/api/generate-parquet-jobs",
                   json={"parquet_path": str(src), "algorithms": ["Narrow Test", "Wide Test"],
                         "publish_to": r"\\srv\o\Dest", "force": True})
        pubs = sorted(json.loads((data / x["name"]).read_text())["publish_to"] for x in r.get_json()["jobs"])
        check("generate: Share-path override wins; with 2 algorithms each gets its own subfolder",
              pubs == [r"\\srv\o\Dest\Narrow_Test", r"\\srv\o\Dest\Wide_Test"], str(pubs))
        # padb-tools' OWN parquet (sidecar of a big dataset): accepted, one entry, job in place
        pdb = make_padb_parquet(td / "Route_PN_sidecar.parquet")
        with open(pdb, "rb") as fh:
            r = c.post("/api/upload-parquet", data={"parquet": (fh, pdb.name)},
                       content_type="multipart/form-data")
        j = r.get_json()
        check("upload-parquet: a padb-tools table is recognised (kind 'padb', ONE entry, usable rows)",
              r.status_code == 200 and j["kind"] == "padb" and len(j["analytics"]) == 1
              and j["analytics"][0]["rows"] == 2100 and j["analytics"][0]["wide"], str(j)[:200])
        r = c.post("/api/generate-parquet-jobs",
                   json={"parquet_path": j["parquet_path"], "algorithms": [j["analytics"][0]["algorithm"]],
                         "views": ["scatter", "summary"], "force": True})
        jj = r.get_json()
        jb = json.loads((data / jj["jobs"][0]["name"]).read_text()) if r.status_code == 200 else {}
        check("generate for a padb-tools table: job written, points at the staged parquet in place",
              r.status_code == 200 and jb.get("parquet_path") == j["parquet_path"]
              and jb.get("views") == ["scatter", "summary"] and jj["jobs"][0]["status"] == "ok", str(jj)[:300])
        r = c.post("/api/upload-parquet", data={"parquet": (io.BytesIO(open(td / "junk.parquet", "rb").read()),
                                                            "junk.parquet")},
                   content_type="multipart/form-data")
        check("upload-parquet: a parquet that is neither kind is rejected with a clear message",
              r.status_code == 400 and "recognised" in r.get_json()["error"], str(r.get_json()))
        three = ["Narrow Test", "Narrow Test:Carrier Power (dBm)", "Wide Test"]
        r = c.post("/api/generate-parquet-jobs", json={"parquet_path": str(src), "algorithms": three,
                                                        "combine_results": True, "force": True})
        jobs_c = [json.loads((data / x["name"]).read_text()) for x in r.get_json()["jobs"]]
        check("generate (combine on): ONE job per algorithm, the multi-result one has a result_default",
              len(jobs_c) == 2 and sum("result_default" in j for j in jobs_c) == 1, str(len(jobs_c)))
        r = c.post("/api/generate-parquet-jobs", json={"parquet_path": str(src), "algorithms": three,
                                                        "combine_results": False, "force": True})
        jobs_s = [json.loads((data / x["name"]).read_text()) for x in r.get_json()["jobs"]]
        check("generate (combine off): a separate job per result, none with a Result filter",
              len(jobs_s) == 3 and not any("result_default" in j for j in jobs_s), str(len(jobs_s)))
        r = c.post("/api/generate-parquet-jobs", json={"parquet_path": str(src), "algorithms": ["Wide Test"],
                                                        "views": ["scatter", "nonsense"]})
        check("generate: an unknown view is rejected with 400", r.status_code == 400)
        r = c.post("/api/generate-parquet-jobs", json={"parquet_path": str(src), "algorithms": ["Wide Test"],
                                                        "views": []})
        check("generate: an empty views list is rejected with 400", r.status_code == 400)
        r = c.post("/api/generate-parquet-jobs",
                   json={"parquet_path": str(src), "algorithms": ["Wide Test"], "force": True,
                         "views": ["scatter", "boxplot", "env_coverage"]})
        jr = r.get_json()
        jv = json.loads((data / jr["jobs"][0]["name"]).read_text())["views"]
        check("generate: the chosen views are written to the job; environmental ones dropped for Room-only data",
              r.status_code == 200 and jv == ["scatter", "boxplot"] and jr["jobs"][0]["status"] == "ok", str(jv))
        r = c.post("/api/generate-parquet-jobs", json={"parquet_path": str(td / "none.parquet"),
                                                        "algorithms": ["x"]})
        check("generate: missing parquet -> 400", r.status_code == 400)
        r = c.post("/api/generate-parquet-jobs", json={"parquet_path": str(src), "algorithms": ["No Such"]})
        check("generate: unknown algorithm -> clean error, not a traceback", r.status_code == 500
              and "error" in r.get_json())
        kinds = {padb_web._job_kind(json.loads((data / n).read_text())) for n in names}
        check("webapp treats a parquet_path job as a plot job", kinds == {"plot"})
    finally:
        padb_web.DATA_DIR = old


# ---------------------------------------------------------------------------
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_result_filter(td: Path, src: Path):
    """A combined (multi-result) page must open with ONLY the main result selected (units
    never mixed), and ticking another result must bring it into the plot."""
    print("\n[RESULT FILTER] (browser tier)")
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        _SKIP.append("playwright not installed")
        print("  SKIP  playwright not installed (env, not a gap)")
        return
    with quiet():
        m = ad.convert(str(src), str(td / "rf_res"), str(td / "rf_jobs"), publish_to="",
                       algorithms=["Narrow Test", "Narrow Test:Carrier Power (dBm)"],
                       log=lambda *a: None)
    jp = Path(m[0]["job_path"])
    job = json.loads(jp.read_text())
    job["views"] = ["scatter"]
    job["results_dir"] = str(td / "rf_out")
    jp.write_text(json.dumps(job))
    r = subprocess.run([sys.executable, str(HERE / "padb_v2.py"), str(jp), "--no-publish"],
                       capture_output=True, text=True, cwd=str(HERE), timeout=600)
    pages = list((td / "rf_out").glob("*scatter.html"))
    check("combined job builds a scatter page", r.returncode == 0 and len(pages) == 1,
          (r.stdout + r.stderr)[-200:])
    if not pages:
        return
    js_state = """() => {
      const o = {boxes: {}};
      document.querySelectorAll('.fchk').forEach(c => {
        const col = c.getAttribute('data-col') || '';
        if (/result/i.test(col)) o.boxes[c.value] = c.checked; });
      const gd = document.getElementById('plot'); let lo = null, hi = null;
      (gd && gd.data || []).forEach(t => { if (t.y && t.y.length) { const a = Array.from(t.y).filter(v => typeof v === 'number' && isFinite(v));
        if (a.length) { const l = Math.min(...a), h = Math.max(...a); lo = lo === null ? l : Math.min(lo, l); hi = hi === null ? h : Math.max(hi, h); } } });
      o.ymin = lo; o.ymax = hi; return o; }"""
    try:
        pw = sync_playwright().start()
        br = pw.chromium.launch()
    except Exception as e:  # noqa: BLE001
        _SKIP.append("chromium unavailable")
        print("  SKIP  chromium unavailable (%s)" % str(e)[:80])
        return
    try:
        pg = br.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(pages[0].as_uri(), wait_until="load", timeout=120000)
        pg.wait_for_timeout(2500)
        st = pg.evaluate(js_state)
        boxes = st["boxes"]
        check("page opens with ONLY the main result ticked (Result filter lists both results)",
              boxes == {"Narrow Test (dBc)": True, "Narrow Test:Carrier Power (dBm)": False}, str(boxes))
        check("plot shows only dBc values on first load (all < -30, nothing near +10 dBm)",
              st["ymax"] is not None and st["ymax"] < -30, str(st))
        # (the filter panel is collapsed, so click via the DOM rather than Playwright's visibility wait)
        pg.evaluate("""() => { const el = document.querySelector(
            '.fchk[data-col="_grp_Result"][value="Narrow Test:Carrier Power (dBm)"]'); el.click(); }""")
        pg.wait_for_timeout(2500)
        st2 = pg.evaluate(js_state)
        check("ticking the dBm result brings it into the plot (max now > 0)",
              st2["ymax"] is not None and st2["ymax"] > 0, str(st2))
        check("no page errors", not errs, "; ".join(errs[:2]))
    finally:
        br.close()
        pw.stop()


def test_ui(td: Path, src: Path):
    print("\n[UI] (browser tier)")
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        _SKIP.append("playwright not installed")
        print("  SKIP  playwright not installed (env, not a gap)")
        return
    data = td / "uidata"
    data.mkdir()
    port = _free_port()
    code = ("import sys; sys.path.insert(0, r'%s'); import padb_web; from pathlib import Path; "
            "padb_web.DATA_DIR = Path(r'%s'); "
            "padb_web.app.run(host='127.0.0.1', port=%d, threaded=True, debug=False)"
            % (HERE / "webapp", data, port))
    srv = subprocess.Popen([sys.executable, "-c", code], cwd=str(HERE),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close()
                break
            except OSError:
                time.sleep(0.5)
        try:
            pw = sync_playwright().start()
            br = pw.chromium.launch()
        except Exception as e:  # noqa: BLE001
            _SKIP.append("chromium unavailable")
            print("  SKIP  chromium unavailable (%s) (env, not a gap)" % str(e)[:80])
            return
        try:
            pg = br.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("dialog", lambda d: (errs.append("dialog: " + d.message), d.dismiss()))
            pg.goto("http://127.0.0.1:%d/" % port)
            pg.set_input_files("#fileInput", str(src))
            pg.wait_for_selector(".algoChk", timeout=20000)
            rows = pg.locator("#analyticsTable tbody tr").count()
            txt = pg.locator("#analyticsTable").inner_text()
            check("UI: dropping a parquet lists its datums (3 rows incl. the dBm one, wide sweep flagged)",
                  rows == 3 and "Carrier Power (dBm)" in txt and "Narrow Test" in txt and "Wide Test" in txt and "wide sweep" in txt, txt[:200])
            check("UI: pod-only controls hidden, button relabelled",
                  not pg.locator("#modeSelect").is_visible()
                  and pg.locator("#generateForm button[type=submit]").inner_text() == "Generate Plot Jobs")
            # --- Plots-to-generate row: Room-only data -> 5 views, no environmental ones
            vals = [c.get_attribute("value") for c in pg.locator(".viewChk").all()]
            check("UI: Plots-to-generate offers the 5 Room-data views, all ticked, NO environmental views",
                  vals == ["scatter", "boxplot", "reference", "summary", "stat_summary"]
                  and pg.locator(".viewChk:checked").count() == 5, str(vals))
            wtxt = pg.locator("#viewsWarn").inner_text() if pg.locator("#viewsWarn").is_visible() else ""
            check("UI: a wide sweep ticked -> heavy views shown TICKED with a warning (not hidden)",
                  "Wide sweep" in wtxt and "Box plot" in wtxt and "Reference statistics" in wtxt, wtxt)
            pg.locator('.algoChk[data-algo="Wide Test"]').uncheck()
            pg.locator('.algoChk[data-algo="Narrow Test:Carrier Power (dBm)"]').uncheck()
            check("UI: with only a narrow result ticked the wide-sweep warning disappears",
                  not pg.locator("#viewsWarn").is_visible())
            pg.locator('.viewChk[value="boxplot"]').uncheck()      # user opts out of the box plot
            pg.locator("#generateForm button[type=submit]").click()
            pg.wait_for_selector("#generatedJobs details", timeout=60000)
            jobs = sorted(p.name for p in data.glob("*_v2_job.json"))
            check("UI: Generate writes a job only for the ticked algorithm",
                  len(jobs) == 1 and "Narrow_Test" in jobs[0], str(jobs))
            jv = json.loads((data / jobs[0]).read_text())["views"] if jobs else None
            check("UI: the job's views list is exactly the ticked plots (box plot opted out)",
                  jv == ["scatter", "reference", "summary", "stat_summary"], str(jv))
            pg.wait_for_selector("#statusPanels > *", timeout=20000)
            check("UI: 'Run the plots now' queued the job -- it shows in the Running Jobs panel",
                  "Narrow_Test" in pg.locator("#statusPanels").inner_text(),
                  pg.locator("#statusPanels").inner_text()[:200])
            # (the jobs list also queries Windows Task Scheduler, which can take a few seconds)
            try:
                pg.wait_for_function(
                    "() => document.getElementById('jobsTable').innerText.includes('Narrow_Test')",
                    timeout=45000)
            except Exception:  # noqa: BLE001
                pass
            jt = pg.locator("#jobsTable").inner_text()
            check("UI: the new job appears in the jobs table", "Narrow_Test" in jt, jt[:200])
            check("UI: no page errors or unexpected dialogs", not errs, "; ".join(errs))
            # a server OLDER than the page (route missing -> HTML 404) must say so loudly,
            # not look like "nothing happens"
            pg2 = br.new_page()
            seen = []
            pg2.on("dialog", lambda d: (seen.append(d.message), d.dismiss()))
            pg2.route("**/api/upload-parquet",
                      lambda r: r.fulfill(status=404, content_type="text/html", body="<html>404</html>"))
            pg2.goto("http://127.0.0.1:%d/" % port)
            pg2.set_input_files("#fileInput", str(src))
            pg2.wait_for_timeout(1500)
            check("UI: a stale server (route 404) shows a 'restart the web server' message",
                  any("older than this page" in m and "Start_web.bat" in m for m in seen), str(seen))
            # multi-temperature data: the environmental views are offered AND ticked (opt-out)
            stp = make_steps_parquet(td / "steps_ui.parquet")
            pg3 = br.new_page()
            pg3.goto("http://127.0.0.1:%d/" % port)
            pg3.set_input_files("#fileInput", str(stp))
            pg3.wait_for_selector(".viewChk", timeout=20000)
            v3 = [c.get_attribute("value") for c in pg3.locator(".viewChk").all()]
            check("UI: multi-temperature data offers the environmental views too, all 7 ticked (opt-out)",
                  v3 == ["scatter", "boxplot", "reference", "summary", "stat_summary", "distribution",
                         "env_coverage"] and pg3.locator(".viewChk:checked").count() == 7, str(v3))
            check("UI: no wide-sweep warning on a narrow sweep", not pg3.locator("#viewsWarn").is_visible())
            # padb-tools' own parquet: one entry, nothing to combine, wide-sweep warning
            pdb = make_padb_parquet(td / "ui_PN_sidecar.parquet")
            pg4 = br.new_page()
            pg4.goto("http://127.0.0.1:%d/" % port)
            pg4.set_input_files("#fileInput", str(pdb))
            pg4.wait_for_selector(".algoChk", timeout=20000)
            check("UI: a padb-tools parquet lists ONE entry and hides the 'combine results' option",
                  pg4.locator("#analyticsTable tbody tr").count() == 1
                  and not pg4.locator("#combineResultsLabel").is_visible()
                  and pg4.locator("#viewsRow").is_visible())
            check("UI: padb-tools parquet (wide sweep) shows the heavy-view warning",
                  "Wide sweep" in pg4.locator("#viewsWarn").inner_text())
        finally:
            br.close()
            pw.stop()
    finally:
        srv.terminate()


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = test_adapter(td)
        test_views_and_steps(td, src)
        test_padb_parquet(td, src)
        test_teeth(td, src)
        test_routes(td, src)
        test_result_filter(td, src)
        test_ui(td, src)
    print("\n" + "=" * 60)
    print("  PASS: %d    FAIL: %d%s" % (len(_PASS), len(_FAIL),
                                         ("    SKIPPED tiers: " + "; ".join(_SKIP)) if _SKIP else ""))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
