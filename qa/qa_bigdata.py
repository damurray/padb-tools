r"""qa_bigdata.py -- very large padbloader files: the size guard, the streaming conversion, slicing and bands.

A pull of several million rows cannot be loaded whole into pandas (conversion ~3.7 KB per row, an HTML build 10-20 KB
per row -- measured; see padb_sizeguard). This suite proves the machinery that deals with that:

  BD-GUARD    the memory estimate and its verdicts (ok / warn / refuse) against an injected machine, the overrides,
              the row budget, and that padb_v2 REFUSES a too-big parquet cleanly (exit 3, nothing built)
  BD-STREAM   the streaming conversion equals the in-memory conversion CELL FOR CELL -- combined and split results,
              filters, an explicit view list, CSV, a non-frequency x axis, a tie for the main result, stored NaNs --
              and does so with tiny batches that cut every group in pieces (a chunk must give exactly the rows the
              whole group would); list_algorithms agrees between the pandas and DuckDB engines
  BD-SLICE    slices (algorithm EXACT name, server, serials, steps, x band) equal an independent pandas filter of the
              source; an exact-name slice does not over-match a name that merely contains it
  BD-BANDS    the band planner partitions each algorithm's OWN swept x (offset, amplitude, Vgg ... -- not assumed to
              be frequency) with every x value whole inside one band, rows adding up exactly, and converting the bands
              reproduces the unsliced data
  BD-ROUTE    padb_analyze routes a dataset too big for pages to a 'giant' result (converted, viewer command, no
              build, exit 0), runs per-band analyses on request, and validates the new request fields; the web app
              flags giant jobs; the watcher treats a giant result as a finished outcome
  BD-MEM      DuckDB running out of memory is a plain message, not a traceback; and a ~1.5M-row file converts with
              a peak far below what the in-memory path would need (measured with psutil)

    py qa/qa_bigdata.py          -> exit 1 on a failure
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the repository root (this file lives in qa/)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "webapp"))
PASS = FAIL = 0


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail))[:600] if detail else "")


# ------------------------------------------------------------------------------------------------ fixture
def make_fixture(path, n_serials=6, reps=2, big=False):
    """A padbloader-schema parquet with the awkward cases a big real pull has:
       'PN Closed Loop' and 'PN Closed Loop EFC' (one name inside the other), a frequency-OFFSET x axis, 'Amp Test' whose x
       is NOT a frequency (c_vgg) with two results in different units and EQUAL row counts (the main-result tie), two
       servers, two test steps, repeats per DUT, a per-row id column (cardinality), values stored as NaN as well as null,
       and a limit column that is entirely NaN."""
    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    rnd = np.random.default_rng(11)
    t0 = pd.Timestamp("2026-10-01 06:00:00")
    parts = []

    def block(algo, result, units, xcol, xs, lim, with_steps):
        n = len(xs)
        rows = []
        for si in range(n_serials):
            server = "PADB ORACLE SR" if si % 2 == 0 else "PADB ORACLE AMC2"
            for port in ("RF1", "RF2"):
                for step in (("Room", "55.0 Deg C") if with_steps else ("Room",)):
                    for rep in range(reps):
                        for xi, x in enumerate(xs):
                            rows.append((algo, result, units, "SG6311A", "US%05d" % (100 + si), server, step, "P" if (si + xi) % 7 else "F",
                                         t0 + pd.Timedelta(hours=si), float(-90 + rnd.normal(0, 2)), lim,
                                         float(x) if xcol == "c_frequency" else 1000.0,
                                         float(x) if xcol == "c_frequency_offset" else np.nan,
                                         float(x) if xcol == "c_vgg" else np.nan, port,
                                         "etp-%s-%d-%s-%d-%d" % (algo[:3], si, port, rep, xi), "SR"))
        return pd.DataFrame(rows, columns=["algorithm", "result", "units", "device", "serial", "server", "step", "run_status",
                                           "run_datetime", "value", "limit_upper", "c_frequency", "c_frequency_offset", "c_vgg",
                                           "c_port", "c_etp_id", "r_site"])
    nx = 400 if big else 14
    off = np.logspace(1, 6, nx)
    parts.append(block("PN Closed Loop", "PN Closed Loop", "dBc/Hz", "c_frequency_offset", off, -80.0, True))
    parts.append(block("PN Closed Loop EFC", "PN Closed Loop EFC", "dBc/Hz", "c_frequency_offset", off, -80.0, False))
    # 'Amp Test': x is a VOLTAGE; main result first in file order, second result has the same number of rows (a tie)
    vg = np.linspace(0.0, 4.0, 9)
    a1 = block("Amp Test", "Amp Test", "dBm", "c_vgg", vg, None, False)
    a2 = block("Amp Test", "Amp Test:Current (mA)", None, "c_vgg", vg, None, False)
    # 'Dual Test': two results, NO result named like the algorithm, EQUAL row counts -> the main result is decided by the tie rule
    d1 = block("Dual Test", "Dual Test:Gain (dB)", "dB", "c_frequency_offset", off[:5], None, False)
    d2 = block("Dual Test", "Dual Test:Phase (deg)", "deg", "c_frequency_offset", off[:5], None, False)
    # 'Single Test': ONE value per unit, no swept x at all (a voltage / resistance / pass-fail test)
    s1 = block("Single Test", "Single Test", "V", "c_vgg", [0.0], None, False)
    s1["c_frequency"] = np.nan
    parts += [a1, a2, d1, d2, s1]
    df = pd.concat(parts, ignore_index=True)
    df["limit_lower"] = np.nan                                   # all-NaN limit column: no lower limit exists
    # the datasheet spec and the rolled-up uncertainty (spec = limit + uncertainty) where there is a limit -- so the streaming and the
    # in-memory conversions are compared on these columns too
    df["uncert_upper"] = np.where(df["limit_upper"].notna(), 0.5, np.nan)
    df["uncert_lower"] = df["uncert_upper"]
    df["cuspec_upper"] = df["limit_upper"] + df["uncert_upper"]
    df["cuspec_lower"] = np.nan
    df.loc[df.index[::97], "value"] = np.nan                     # stored NaN values (dropped like nulls)
    value = pa.array(df["value"].to_numpy(), type=pa.float64(), from_pandas=False)      # NaN stays NaN
    tbl = pa.Table.from_pandas(df.drop(columns=["value"]), preserve_index=False)
    tbl = tbl.add_column(9, "value", value)
    nulls = np.zeros(len(df), dtype=bool)
    nulls[1::211] = True
    tbl = tbl.set_column(tbl.schema.get_field_index("value"), "value",
                         pa.array(df["value"].to_numpy(), mask=nulls, type=pa.float64()))
    md = dict(tbl.schema.metadata or {})
    md.update({b"label.c_frequency_offset": b"Frequency Offset", b"units.c_frequency_offset": b"Hz",
               b"label.c_vgg": b"Vgg", b"units.c_vgg": b"V", b"label.c_port": b"Port"})
    pq.write_table(tbl.replace_schema_metadata(md), str(path), row_group_size=1500)
    return Path(path)


def compare_outputs(made_a, made_b, root_a, root_b):
    """Differences between two conversions' results: returned dicts, every data cell, every job file."""
    import pandas as pd
    bad = []
    norm = lambda made, root: [{k: (str(v).replace(str(root), "<R>") if isinstance(v, str) else v) for k, v in m.items()} for m in made]
    if norm(made_a, root_a) != norm(made_b, root_b):
        bad.append("returned dicts differ")
    for a, b in zip(made_a, made_b):
        if a.get("status") != "ok" or b.get("status") != "ok":
            continue
        if a["data_path"].lower().endswith(".csv"):
            if Path(a["data_path"]).read_bytes() != Path(b["data_path"]).read_bytes():
                bad.append("%s csv bytes differ" % a["algorithm"])
        else:
            da, db = pd.read_parquet(a["data_path"]), pd.read_parquet(b["data_path"])
            if list(da.columns) != list(db.columns) or len(da) != len(db):
                bad.append("%s shape/columns differ" % a["algorithm"])
                continue
            for c in da.columns:
                x, y = da[c].astype(object).reset_index(drop=True), db[c].astype(object).reset_index(drop=True)
                if not bool(((x == y) | (x.isna() & y.isna())).all()):
                    bad.append("%s column %r differs" % (a["algorithm"], c))
        fa = json.loads(Path(a["job_path"]).read_text().replace(str(root_a).replace("\\", "\\\\"), "<R>"))
        fb = json.loads(Path(b["job_path"]).read_text().replace(str(root_b).replace("\\", "\\\\"), "<R>"))
        if fa != fb:
            bad.append("%s job file differs: %s" % (a["algorithm"], {k: (fa.get(k), fb.get(k)) for k in set(fa) | set(fb) if fa.get(k) != fb.get(k)}))
    return bad


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def main() -> int:
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq
    import padb_loader_adapter as ad
    import padb_loader_stream as st
    import padb_sizeguard as G

    tmp = Path(tempfile.mkdtemp(prefix="qa_big_"))
    src = make_fixture(tmp / "bigfx_20261101_000000.parquet")
    src_df = pq.read_table(str(src)).to_pandas()
    print("  fixture: %s rows, algorithms %s" % (format(len(src_df), ","), sorted(src_df["algorithm"].unique())))

    # =============================================================================================== BD-GUARD
    fp0 = G.footprint(src)
    est_c = G.estimate_bytes("convert", fp0)
    mem_small, mem_big = (64e9, est_c * 0.5), (64e9, 48e9)          # the fixture is tiny: size the machine to IT
    a_small = G.assess(src, "convert", mem=mem_small)
    a_big = G.assess(src, "convert", mem=mem_big)
    check("BD-GUARD: the same file is 'refuse' on a nearly-full machine and 'ok' on a roomy one",
          a_small["level"] == "refuse" and a_big["level"] == "ok", (a_small["level"], a_big["level"]))
    fp = G.footprint(src)
    check("BD-GUARD: the footprint is read from the footer (rows match the file) and the convert estimate scales with its bytes",
          fp["rows"] == len(src_df) and G.estimate_bytes("convert", dict(fp, uncompressed_bytes=fp["uncompressed_bytes"] * 100000))
          > 5 * G.estimate_bytes("convert", fp), fp)
    e_light = G.estimate_bytes("analyze", fp, ["scatter"])
    e_full = G.estimate_bytes("analyze", fp)
    check("BD-GUARD: the HTML estimate is higher for the full view set than for one or two views, and per row", e_full > e_light > G.BASE_BYTES,
          (e_light, e_full))
    check("BD-GUARD: scatter+boxplot is costed like the full set, not as 'light' (measured on the real EP6 band: 27 KB/row, 7.7 GB for 283,866 "
          "rows -- the one-or-two-views rule had under-estimated it 2.5x)",
          G.estimate_bytes("analyze", fp, ["scatter", "boxplot"]) == e_full and G.estimate_bytes("analyze", fp, ["scatter"]) == e_light, (e_light, e_full))
    mid = G.assess(src, "analyze", mem=(64e9, e_full / 0.7))                       # estimate is 70% of what is available
    check("BD-GUARD: between half and 85% of what is free the verdict is 'warn' (allowed, but said)", mid["level"] == "warn" and mid["ok"], mid["level"])
    total_cap = G.assess(src, "analyze", mem=(e_full / 0.7, 1e12))                # plenty free, but the file needs > 60% of ALL ram
    check("BD-GUARD: it also refuses when the estimate exceeds 60%% of ALL the RAM, however much is free right now",
          total_cap["level"] == "refuse", total_cap["level"])
    try:
        G.require(src, "analyze", mem=mem_small)
        raised = None
    except G.TooBig as exc:
        raised = str(exc)
    check("BD-GUARD: require() raises TooBig, and the message says what to do (viewer / slice / the override)",
          bool(raised) and "padb_viewer.py" in raised and "PADB_SIZE_GUARD=off" in raised, raised)
    check("BD-GUARD: PADB_SIZE_GUARD=off disables it; PADB_MAX_LOAD_GB replaces 'what is free' with a fixed budget",
          G.assess(src, "convert", mem=mem_small, env={"PADB_SIZE_GUARD": "off"})["level"] == "ok"
          and G.assess(src, "convert", mem=mem_big, env={"PADB_MAX_LOAD_GB": "0.1"})["level"] == "refuse"
          and G.assess(src, "convert", mem=mem_small, env={"PADB_MAX_LOAD_GB": "40"})["level"] == "ok")
    mr = G.max_rows("analyze", mem=(64e9, 20e9))
    near = G.assess(src, "analyze", mem=(64e9, 20e9))
    check("BD-GUARD: max_rows() is the budget divided by the per-row cost (more free memory -> more rows; light views -> more rows)",
          G.max_rows("analyze", mem=(64e9, 40e9)) > mr and G.max_rows("analyze", ["scatter"], mem=(64e9, 20e9)) > mr
          and 100_000 < mr < 3_000_000, mr)
    # padb_v2 refuses a parquet it cannot build, cleanly
    ok_rows = ad.convert(str(src), str(tmp / "v2r"), str(tmp / "v2j"), algorithms=["PN Closed Loop"], stream="off", log=lambda *_: None)
    job = json.loads(Path(ok_rows[0]["job_path"]).read_text())
    job.update({"publish_to": "", "views": ["scatter"], "results_dir": str(tmp / "v2out_refused")})
    jp = tmp / "v2_refuse_job.json"
    jp.write_text(json.dumps(job))
    env = dict(os.environ, PADB_MAX_LOAD_GB="0.2")
    r = subprocess.run([sys.executable, str(HERE / "padb_v2.py"), str(jp), "--no-publish"], capture_output=True, text=True, env=env, timeout=300)
    vo = tmp / "v2out_refused"
    pages = [q for q in vo.glob("*.html") if q.name != "index.html"]
    idx_t = (vo / "index.html").read_text(encoding="utf-8") if (vo / "index.html").is_file() else ""
    check("BD-GUARD: padb_v2 on a parquet that will not fit says '[TOO BIG]' and '[VIEWER ONLY]', exits 0 and builds NO HTML pages",
          r.returncode == 0 and "[TOO BIG]" in r.stdout and "[VIEWER ONLY]" in r.stdout and not pages, (r.returncode, r.stdout[-300:], pages))
    check("BD-GUARD: ... it builds the viewer objects instead: the parquet beside an index that says no pages were built and offers the viewer",
          list(vo.glob("*.parquet")) and (vo / "viewer_only.json").is_file() and "no pages were built" in idx_t and "Open in viewer" in idx_t
          and list(vo.glob("*.parquet"))[0].name in idx_t, (list(vo.iterdir()), idx_t[:200]))
    job["too_big_viewer"] = False
    job["results_dir"] = str(tmp / "v2out_refused2")
    jp.write_text(json.dumps(job))
    r0 = subprocess.run([sys.executable, str(HERE / "padb_v2.py"), str(jp), "--no-publish"], capture_output=True, text=True, env=env, timeout=300)
    check("BD-GUARD: with too_big_viewer=false the old behaviour stays: exit 3, nothing built",
          r0.returncode == 3 and "[TOO BIG]" in r0.stdout and "[VIEWER ONLY]" not in r0.stdout and not list((tmp / "v2out_refused2").glob("*.html")), (r0.returncode, r0.stdout[-200:]))
    job.pop("too_big_viewer")
    job["results_dir"] = str(tmp / "v2out_ok")
    jp.write_text(json.dumps(job))
    r2 = subprocess.run([sys.executable, str(HERE / "padb_v2.py"), str(jp), "--no-publish"], capture_output=True, text=True, timeout=300)
    check("BD-GUARD: ... and the same job builds normally on this machine (the guard is not in the way of normal use)",
          r2.returncode == 0 and list((tmp / "v2out_ok").glob("*scatter.html")), (r2.returncode, r2.stdout[-200:]))

    # =============================================================================================== BD-STREAM
    def both(tag, **kw):
        ro, rs = tmp / (tag + "_mem"), tmp / (tag + "_str")
        with quiet():
            ma = ad.convert(str(src), str(ro / "res"), str(ro / "jobs"), stream="off", **kw)
            mb = ad.convert(str(src), str(rs / "res"), str(rs / "jobs"), stream="always", **kw)
        return ma, mb, ro, rs
    cases = [("combined", {}), ("split", {"combine_results": False}), ("filter", {"algo_filter": "EFC"}),
             ("exact", {"algorithms": ["Amp Test:Current (mA)"]}), ("views", {"views": ["scatter", "boxplot", "distribution"]}),
             ("csv", {"fmt": "csv"}), ("xcol", {"x_col": "c_vgg", "algo_filter": "Amp"}), ("noruntime", {"use_runtime": False})]
    for name, kw in cases:
        ma, mb, ro, rs = both(name, **kw)
        bad = compare_outputs(ma, mb, ro, rs)
        check("BD-STREAM[%s]: the streaming conversion equals the in-memory one (%d job(s), rows %s) -- every cell, every job file"
              % (name, len(ma), [m["rows"] for m in ma]), not bad and len(ma) == len(mb) and len(ma) > 0, bad[:2])
    # tiny batches: every group is cut in pieces, batches straddle algorithms, DUTs and x values
    ro = tmp / "tiny_mem"
    rb = tmp / "tiny_str"
    with quiet():
        ma = ad.convert(str(src), str(ro / "res"), str(ro / "jobs"), stream="off")
        mb = st.convert_stream(str(src), str(rb / "res"), str(rb / "jobs"), None, None, None, True, None, "parquet", True,
                               lambda *_: None, True, None, None, batch_rows=313)
    bad = compare_outputs(ma, mb, ro, rb)
    check("BD-STREAM: with 313-row batches (cutting every group in pieces) the output is STILL identical -- a chunk gives exactly "
          "the rows the whole group would", not bad, bad[:2])
    # facts: the tie for the main result and the dropped high-cardinality column came out the same
    jobs_a = {m["algorithm"]: json.loads(Path(m["job_path"]).read_text()) for m in ma}
    mb_ = {m["algorithm"]: json.loads(Path(m["job_path"]).read_text()) for m in
           both("tie")[1]}
    dv = src_df[(src_df["algorithm"] == "Dual Test") & src_df["value"].notna()]
    cnt = dv["result"].value_counts()
    want = sorted(cnt.index, key=lambda r: (-cnt[r], dv.index[dv["result"] == r][0]))[0]
    want_lbl = "%s (%s)" % (want, dv[dv["result"] == want]["units"].dropna().iloc[0]) if "(" not in want else want
    check("BD-STREAM: the main result of a multi-result algorithm with no result named like it (most valid rows, then first in file) "
          "is the same on both paths and equals an independent computation (%s; counts %s)" % (want, dict(cnt)),
          jobs_a["Dual Test"].get("result_default") == want_lbl and mb_["Dual Test"].get("result_default") == want_lbl,
          (jobs_a["Dual Test"].get("result_default"), mb_["Dual Test"].get("result_default"), want_lbl))
    xj = json.loads(Path(both("xj", x_col="c_vgg", algo_filter="Amp")[0][0]["job_path"]).read_text())
    check("BD-STREAM: an explicit non-frequency x column (c_vgg) is labelled from the file metadata (Vgg), not assumed to be frequency",
          "Vgg" in json.dumps(xj.get("x_col", "")) + json.dumps(xj.get("x_label", "")), (xj.get("x_col"), xj.get("x_label")))
    check("BD-STREAM: a test with NO swept x gets the histogram view (every x-based view fails on it: 'no plottable data'), a swept one does not "
          "-- on both paths (found on the real 32- and 75-algorithm padbloader files: 31/32 and 67/75 algorithms had no swept x)",
          jobs_a["Single Test"].get("views") == ["histogram"] and jobs_a["PN Closed Loop"].get("views") != ["histogram"]
          and json.loads(Path(both("nx", views=["scatter", "boxplot"])[1][-1]["job_path"]).read_text()).get("views") == ["histogram"],
          jobs_a["Single Test"].get("views"))
    gcol = pd.read_parquet(ma[0]["data_path"], columns=["Group"])["Group"].iloc[0]
    check("BD-STREAM: the per-row id column (c_etp_id, high cardinality) is kept OUT of Group on both paths", "etp" not in gcol.lower(), gcol)
    lm = ad.list_algorithms(str(src), engine="pandas")
    ld = ad.list_algorithms(str(src), engine="duckdb")
    check("BD-STREAM: list_algorithms gives the same answer from pandas and from DuckDB (rows, units, limits, x, n_x, steps, wide)",
          lm == ld, [(x, y) for x, y in zip(lm, ld) if x != y][:1])
    nan_rows = int(src_df["value"].isna().sum())
    check("BD-STREAM: rows whose value is NULL *or* a stored NaN are dropped identically (%d such rows)" % nan_rows,
          nan_rows > 20 and sum(m["rows"] for m in ma) == len(src_df) - nan_rows, (nan_rows, sum(m["rows"] for m in ma)))

    # =============================================================================================== BD-SLICE
    def conv_slice(tag, sl, stream, **kw):
        root = tmp / tag
        with quiet():
            return ad.convert(str(src), str(root / "res"), str(root / "jobs"), stream=stream, slice_=sl, **kw), root

    def total_rows(made):
        return sum(m["rows"] for m in made)
    valid = src_df[src_df["value"].notna()]
    slice_cases = [
        ("alg_exact", {"algorithms": ["PN Closed Loop"]}, valid[valid["algorithm"] == "PN Closed Loop"]),
        ("server", {"servers": ["amc2"]}, valid[valid["server"].str.lower().str.contains("amc2")]),
        ("serials_n", {"serials": 2}, valid[valid["serial"].isin(sorted(valid["serial"].unique())[:2])]),
        ("serials_list", {"serials": ["US00101", "US00103"]}, valid[valid["serial"].isin(["US00101", "US00103"])]),
        ("steps", {"steps": ["55.0 Deg C"]}, valid[valid["step"] == "55.0 Deg C"]),
        ("combo", {"algorithms": ["PN Closed Loop"], "servers": ["SR"], "steps": ["Room"]},
         valid[(valid["algorithm"] == "PN Closed Loop") & valid["server"].str.lower().str.contains("sr") & (valid["step"] == "Room")]),
    ]
    for name, sl, expect in slice_cases:
        ma, ra = conv_slice("sl_" + name + "_m", sl, "off")
        mb, rb_ = conv_slice("sl_" + name + "_s", sl, "always")
        bad = compare_outputs(ma, mb, ra, rb_)
        check("BD-SLICE[%s]: both paths agree, and the rows are exactly an independent pandas filter of the source (%s)"
              % (name, format(len(expect), ",")), not bad and total_rows(ma) == len(expect) and len(expect) > 0, (bad[:1], total_rows(ma), len(expect)))
    ma, _ = conv_slice("sl_sub", None, "always", algo_filter="PN Closed Loop")
    mb, _ = conv_slice("sl_exact", {"algorithms": ["PN Closed Loop"]}, "always")
    check("BD-SLICE: a SUBSTRING filter matches 'PN Closed Loop EFC' as well, the EXACT-name slice does not (the reason the slice key exists)",
          len(ma) == 2 and len(mb) == 1 and mb[0]["algorithm"] == "PN Closed Loop", ([m["algorithm"] for m in ma], [m["algorithm"] for m in mb]))
    for bad_sl in ({"x_range": [5, 1]}, {"x_range": [1]}):
        try:
            ad._slice_names(bad_sl)
            err = None
        except ValueError as exc:
            err = str(exc)
        check("BD-SLICE: a malformed x_range %s is rejected with a message" % (bad_sl,), err and "x_range" in err, err)

    # =============================================================================================== BD-BANDS
    xs_pn = sorted(valid[valid["algorithm"] == "PN Closed Loop"]["c_frequency_offset"].dropna().unique())
    xs_amp = sorted(valid[valid["algorithm"] == "Amp Test"]["c_vgg"].dropna().unique())
    plan = {p["algorithm"]: p for p in st.plan_x_bands(str(src), 150)}
    pn = plan["PN Closed Loop"]
    amp = {p["algorithm"]: p for p in st.plan_x_bands(str(src), 40, x_col="c_vgg")}["Amp Test"]
    check("BD-BANDS: the planner uses each algorithm's swept x -- frequency OFFSET for the phase-noise ones, and an explicit x_col "
          "(Vgg, a voltage) when given", pn["x_col"] == "c_frequency_offset" and amp["x_col"] == "c_vgg", (pn["x_col"], amp["x_col"]))

    def partition_ok(p, xs, rows_per_x, max_rows):
        bands = p["bands"]
        covered = [x for x in xs for b in bands if b["lo"] <= x <= b["hi"]]
        no_split = all(bands[i]["hi"] < bands[i + 1]["lo"] for i in range(len(bands) - 1))
        fits = all(b["rows"] <= max_rows or b["n_x"] == 1 for b in bands)
        return (covered == list(xs) and no_split and fits and sum(b["rows"] for b in bands) == int(sum(rows_per_x.values())),
                (len(bands), covered == list(xs), no_split, fits))
    rpx_pn = valid[valid["algorithm"] == "PN Closed Loop"].groupby("c_frequency_offset").size().to_dict()
    ok1, d1 = partition_ok(pn, xs_pn, rpx_pn, 150)
    rpx_amp = valid[valid["algorithm"] == "Amp Test"].groupby("c_vgg").size().to_dict()
    ok2, d2 = partition_ok(amp, xs_amp, rpx_amp, 40)
    check("BD-BANDS: the bands PARTITION the x values -- each x in exactly one band, no x split across bands, every band within the "
          "row budget, rows adding up to the algorithm's total (offset axis: %s)" % (d1,), ok1 and len(pn["bands"]) > 1, d1)
    check("BD-BANDS: ... also on the non-frequency (voltage) axis (%s)" % (d2,), ok2 and len(amp["bands"]) > 1, d2)
    one = st.plan_x_bands(str(src), 10)                     # budget smaller than one x value's rows
    check("BD-BANDS: an x value with more rows than the budget gets a band of its own instead of being split",
          all(b["n_x"] == 1 for p in one for b in p["bands"] if b["rows"] > 10) and any(b["rows"] > 10 for p in one for b in p["bands"]))
    # converting the bands reproduces the unsliced data
    full, rf = conv_slice("bands_full", {"algorithms": ["PN Closed Loop"]}, "always")
    dfull = pd.read_parquet(full[0]["data_path"])
    pieces, rows_sum = [], 0
    for i, b in enumerate(pn["bands"]):
        mm, rr = conv_slice("band_%d" % i, {"algorithms": ["PN Closed Loop"], "x_range": [b["lo"], b["hi"]]}, "always")
        mmm, rrr = conv_slice("band_%d_m" % i, {"algorithms": ["PN Closed Loop"], "x_range": [b["lo"], b["hi"]]}, "off")
        if compare_outputs(mm, mmm, rr, rrr):
            check("BD-BANDS: band %d identical on both paths" % i, False, compare_outputs(mm, mmm, rr, rrr)[:1])
        pieces.append(pd.read_parquet(mm[0]["data_path"]))
        rows_sum += mm[0]["rows"]
        check("BD-BANDS: band %d's job counts are the BAND's (rows %d, x values %d), and its file name carries the range"
              % (i, mm[0]["rows"], b["n_x"]), mm[0]["rows"] == b["rows"] and "_x" in Path(mm[0]["job_path"]).name, mm[0]["rows"])
    cat = pd.concat(pieces, ignore_index=True)
    key = list(cat.columns)
    same = len(cat) == len(dfull) and cat.astype(str).sort_values(key).reset_index(drop=True).equals(dfull.astype(str).sort_values(key).reset_index(drop=True))
    check("BD-BANDS: the bands together are EXACTLY the unsliced data (%s rows, as a multiset of rows)" % format(len(dfull), ","), same and rows_sum == len(dfull),
          (len(cat), len(dfull)))
    cols_same = all(list(p.columns) == list(dfull.columns) for p in pieces)
    check("BD-BANDS: every band has the SAME columns and Group keys as the whole (column decisions come from the whole algorithm)", cols_same)

    # =============================================================================================== BD-ROUTE
    def cli(args, env=None, timeout=900):
        return subprocess.run([sys.executable, str(HERE / "padb_analyze.py")] + args, capture_output=True, text=True,
                              env=dict(os.environ, **(env or {})), timeout=timeout)
    out_g = tmp / "route_giant"
    r = cli(["--input", str(src), "--out", str(out_g), "--algorithm", "PN Closed Loop"], env={"PADB_MAX_LOAD_GB": "0.2"})
    res = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    an = (res.get("analyses") or [{}])[0]
    pages_g = [q for q in out_g.rglob("*.html") if q.name != "index.html"]
    check("BD-ROUTE: a dataset too big for pages is reported as 'giant' -- converted, a viewer command, a band command, NO pages built "
          "(only the viewer's index), ok=true, exit 0", r.returncode == 0 and res.get("ok") and an.get("status") == "giant" and "padb_viewer.py" in (an.get("giant") or {}).get("viewer", "")
          and "--bands auto" in an["giant"]["bands"] and not pages_g and list((out_g / "padb").glob("*.parquet")) and an.get("files") == [],
          (r.returncode, r.stderr[-300:], an.get("status"), pages_g))
    check("BD-ROUTE: ... and the giant result carries the viewer index (so the page can link it) with the parquet beside it",
          an.get("index") and Path(an["index"]).is_file() and list(Path(an["index"]).parent.glob("*.parquet")), an.get("index"))
    r = cli(["--input", str(src), "--out", str(tmp / "route_normal"), "--algorithm", "PN Closed Loop", "--views", "scatter", "--stream", "always"])
    res = json.loads(r.stdout)
    check("BD-ROUTE: forced streaming through padb_analyze builds the normal pages on a dataset that fits",
          r.returncode == 0 and res["ok"] and res["analyses"][0]["status"] == "ok" and res["analyses"][0]["index"], (r.returncode, r.stderr[-200:]))
    r = cli(["--input", str(src), "--out", str(tmp / "route_bands"), "--algorithm", "PN Closed Loop", "--views", "scatter", "--bands", "150"])
    res = json.loads(r.stdout)
    n_expect = len(pn["bands"])
    check("BD-ROUTE: --bands <rows> runs one analysis per band (%d expected), each with its own results folder, all ok" % n_expect,
          r.returncode == 0 and res["ok"] and len(res["analyses"]) == n_expect and all(a["status"] == "ok" for a in res["analyses"])
          and len({a["results_dir"] for a in res["analyses"]}) == n_expect, (r.returncode, len(res.get("analyses", [])), r.stderr[-300:]))
    for bad_req, frag in (({"slice": {"colour": "red"}}, "slice"), ({"stream": "sometimes"}, "stream"), ({"bands": -3}, "bands"),
                          ({"bands": "lots"}, "bands"), ({"slice": {"x_range": [9, 1]}}, "x_range")):
        req = dict({"input": str(src), "out": str(tmp / "route_bad")}, **bad_req)
        rr = cli([json.dumps(req)]) if False else subprocess.run([sys.executable, str(HERE / "padb_analyze.py"), "-"], input=json.dumps(req),
                                                                  capture_output=True, text=True, timeout=120)
        out = json.loads(rr.stdout)
        check("BD-ROUTE: a bad request field %s is rejected (exit 2) with a message naming it" % list(bad_req), rr.returncode == 2 and frag in out.get("error", ""),
              (rr.returncode, out.get("error")))
    # the watcher treats 'giant' as a finished outcome
    import padb_watch as W
    s_dir = tmp / "wscripts"
    s_dir.mkdir()
    (s_dir / "giant.py").write_text("import json\nprint(json.dumps({'ok': True, 'analyses': [{'name': 'n', 'status': 'giant', 'views': [], 'index': None,"
                                    "'giant': {'viewer': 'py padb_viewer.py X'}}]}))")
    rw = W.run_analysis(src, tmp / "wout", {}, 30, [sys.executable, str(s_dir / "giant.py")])
    check("BD-ROUTE: the watcher counts a 'giant' analysis as DONE (viewer only), not as a failure", rw["ok"] and rw["analyses"][0]["status"] == "giant", rw)
    wd = tmp / "wwatch" / "S - u"
    wd.mkdir(parents=True)
    shutil.copy(src, wd / "bigfx_20261101-000000.parquet")
    t = time.time() - 3600
    os.utime(wd / "bigfx_20261101-000000.parquet", (t, t))

    def giant_runner(s_, o_, opts, to_):
        return {"ok": True, "error": None, "analyses": [{"name": "n", "status": "giant", "views": [], "index": None, "tier": None,
                                                       "viewer": "py padb_viewer.py X", "issues": 0}]}
    w = W.Watcher([tmp / "wwatch"], tmp / "wwatch_out", settle_s=0, recheck_s=0, runner=giant_runner, log=lambda m: None)
    w.run_pass()
    page = (tmp / "wwatch_out" / "index.html").read_text(encoding="utf-8")
    check("BD-ROUTE: the watcher's overview says how to open a giant result (viewer command), where a normal run has a link",
          "too big for HTML pages" in page and "padb_viewer.py" in page, page[-300:])
    # the web app flags giant jobs and the front end does not queue them
    import padb_web
    data = tmp / "webdata"
    data.mkdir()
    padb_web.DATA_DIR = data
    cl = padb_web.app.test_client()
    os.environ["PADB_MAX_LOAD_GB"] = "0.2"
    try:
        resp = cl.post("/api/generate-parquet-jobs", json={"parquet_path": str(src), "algorithms": ["PN Closed Loop"], "force": True}).get_json()
    finally:
        os.environ.pop("PADB_MAX_LOAD_GB", None)
    js = (HERE / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    check("BD-ROUTE: the web app marks a giant job (message + viewer command) in its reply and says so in the output text; the page "
          "no longer skips it -- it queues it as viewer-only", bool(resp and any(j.get("giant") for j in resp.get("jobs", []))) and "TOO BIG FOR HTML PAGES" in resp.get("stdout", "")
          and "!j.giant" not in js and "viewer-only" in js, resp.get("stdout", "")[-300:] if resp else resp)
    # ... and a queued too-big job runs to 'done', flagged for the Running Jobs card, with the viewer index as its result
    gj = [j for j in resp["jobs"] if j.get("giant")][0]["path"]
    os.environ["PADB_MAX_LOAD_GB"] = "0.2"
    try:
        q = cl.post("/api/execute-job", json={"paths": [gj], "dry_run": False, "publish": False}).get_json()
        stt = {}
        for _ in range(120):
            stt = cl.get("/api/job-status/" + q["job_ids"][0]).get_json()
            if stt["status"] not in ("queued", "running"):
                break
            time.sleep(1)
    finally:
        os.environ.pop("PADB_MAX_LOAD_GB", None)
    check("BD-ROUTE: a queued too-big job ends 'done' (not failed), flagged too_big with the [VIEWER ONLY] line, result = the viewer index",
          stt.get("status") == "done" and stt.get("too_big") and "[VIEWER ONLY]" in stt["too_big"] and stt.get("result_index"), stt)
    check("BD-ROUTE: the Running Jobs card shows the flag (badge text + note) from that field",
          "data.too_big" in js and "TOO BIG, viewer only" in js and ".toobig-note" in js)
    resp2 = cl.post("/api/generate-parquet-jobs", json={"parquet_path": str(src), "algorithms": ["PN Closed Loop"], "force": True}).get_json()
    check("BD-ROUTE: ... and on this machine, with room to spare, the same request is NOT flagged", resp2 and not any(j.get("giant") for j in resp2.get("jobs", [])), resp2)

    # ---- the conversion runs in the background and reports progress (a big file used to leave the page silent for minutes)
    def run_async(body, tries=120):
        a = cl.post("/api/generate-parquet-jobs", json=dict(body, **{"async": True}))
        if a.status_code != 200:
            return a.status_code, a.get_json(), None
        tid = a.get_json()["task"]
        tk = {}
        for _ in range(tries):
            tk = cl.get("/api/generate-task/" + tid).get_json()
            if tk["state"] != "running":
                break
            time.sleep(0.5)
        return 200, tk, tid
    code, tk, tid = run_async({"parquet_path": str(src), "algorithms": ["PN Closed Loop"], "force": True})
    check("BD-ASYNC: an async request returns a task at once and finishes 'done' with the SAME jobs as the synchronous route",
          code == 200 and tk.get("state") == "done" and tk.get("http_status") == 200
          and [j["name"] for j in tk["result"]["jobs"]] == [j["name"] for j in resp2["jobs"]], (code, tk.get("state"), str(tk)[:300]))
    check("BD-ASYNC: ... and the task carries progress lines (reading / building / streaming) and an elapsed time",
          tk.get("log_lines", 0) > 0 and any(("building" in l or "streaming" in l or "reading" in l) for l in tk.get("log", [])) and tk.get("elapsed_s") is not None, tk.get("log"))
    check("BD-ASYNC: a missing parquet is refused at once (400), not after a wait",
          cl.post("/api/generate-parquet-jobs", json={"parquet_path": str(tmp / "nope.parquet"), "algorithms": ["x"], "async": True}).status_code == 400)
    check("BD-ASYNC: an unknown task id is a clear 404", cl.get("/api/generate-task/zzzz").status_code == 404)
    real_convert = padb_web.padb_loader_adapter.convert

    def _boom(*a, **k):
        raise SystemExit("boom: the adapter gave up")
    padb_web.padb_loader_adapter.convert = _boom
    try:
        code, tk, _ = run_async({"parquet_path": str(src), "algorithms": ["PN Closed Loop"], "force": True}, tries=40)
        sync_r = cl.post("/api/generate-parquet-jobs", json={"parquet_path": str(src), "algorithms": ["PN Closed Loop"], "force": True})
    finally:
        padb_web.padb_loader_adapter.convert = real_convert
    check("BD-ASYNC: an adapter that exits (SystemExit) ends the task 'error' with its message -- never a silent hang",
          tk.get("state") == "error" and "boom" in (tk.get("result") or {}).get("error", "") and tk.get("http_status") == 500, str(tk)[:300])
    check("BD-ASYNC: ... and the synchronous route answers that as JSON 500 too (it used to escape as a server error page)",
          sync_r.status_code == 500 and "boom" in (sync_r.get_json() or {}).get("error", ""), sync_r.status_code)
    check("BD-ASYNC: the page polls the task, shows a live status line with a timer and the result, and keeps working if the page waits",
          "generate-task" in js and 'getElementById("generateStatus")' in js and "async: true" in js and "fmtT(st.elapsed_s)" in js)

    # =============================================================================================== BD-MEM
    os.environ["PADB_STREAM_MEM_GB"] = "0.02"
    try:
        try:
            with quiet():
                st.convert_stream(str(src), str(tmp / "oom" / "res"), str(tmp / "oom" / "jobs"), None, None, None, True, None, "parquet", True,
                                  lambda *_: None, True, None, None)
            outcome = "completed"
        except st.StreamMemoryError as exc:
            outcome = "message: " + str(exc)
        except Exception as exc:  # noqa: BLE001
            outcome = "RAW %s: %s" % (type(exc).__name__, str(exc)[:100])
    finally:
        os.environ.pop("PADB_STREAM_MEM_GB", None)
    check("BD-MEM: with the analysis engine squeezed to 20 MB the result is either success (it spills) or a plain message that says what "
          "to do -- never a raw DuckDB exception (%s)" % outcome[:60], not outcome.startswith("RAW") and (outcome == "completed" or "PADB_STREAM_MEM_GB" in outcome), outcome)

    # DuckDB's own out-of-memory exception (forced: the engine happily spills for this small file) becomes the plain message
    import duckdb

    class _OomCon:
        def execute(self, *a, **k):
            raise duckdb.OutOfMemoryException("Out of Memory Error: could not allocate block")
    s_ = st._Src(str(src))
    s_.con = _OomCon()
    try:
        s_.one("SELECT 1")
        oom = "no exception"
    except st.StreamMemoryError as exc:
        oom = str(exc)
    except Exception as exc:  # noqa: BLE001
        oom = "RAW %s" % type(exc).__name__
    check("BD-MEM: DuckDB's out-of-memory exception is turned into a plain StreamMemoryError that names PADB_STREAM_MEM_GB",
          "PADB_STREAM_MEM_GB" in oom and not oom.startswith("RAW"), oom[:120])

    # a ~1.5M-row file: the peak memory of the streaming conversion vs what the in-memory path would need
    big = tmp / "scale_20261101_000000.parquet"
    make_fixture(big, n_serials=40, reps=3, big=True)
    fpb = G.footprint(big)
    est_mem = G.estimate_bytes("convert", fpb)
    sys.path.insert(0, str(tmp))
    (tmp / "peak.py").write_text("import subprocess,sys,time,psutil\n"
                                 "p=subprocess.Popen(sys.argv[1:],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                                 "ps=psutil.Process(p.pid);pk=0\n"
                                 "while p.poll() is None:\n"
                                 "    try: pk=max(pk,ps.memory_info().rss+sum(c.memory_info().rss for c in ps.children(recursive=True)))\n"
                                 "    except psutil.Error: pass\n"
                                 "    time.sleep(0.1)\n"
                                 "print(pk,p.returncode)\n")
    t0 = time.time()
    pr = subprocess.run([sys.executable, str(tmp / "peak.py"), sys.executable, str(HERE / "padb_loader_adapter.py"), str(big), "--out", str(tmp / "scale" / "res"),
                         "--jobs-dir", str(tmp / "scale" / "jobs"), "--no-publish", "--stream", "always"], capture_output=True, text=True, timeout=900)
    peak, rc = (int(x) for x in pr.stdout.split()[-2:]) if pr.stdout.split() else (0, -1)
    got = sum(pq.ParquetFile(f).metadata.num_rows for f in (tmp / "scale" / "res" / "padb").glob("*.parquet"))
    import duckdb
    want = duckdb.connect().execute("select count(*) from read_parquet(?) where value is not null and not isnan(value)", [str(big)]).fetchone()[0]
    check("BD-MEM: a %s-row file streams to the exact row count an independent DuckDB count gives (%s) in %.0f s"
          % (format(fpb["rows"], ","), format(want, ","), time.time() - t0), rc == 0 and got == want, (rc, got, want))
    check("BD-MEM: its peak memory (%.0f MB) is under 60%% of the %.0f MB the in-memory path measured to need (3.65 KB/row) -- bounded"
          % (peak / 1e6, 3650 * fpb["rows"] / 1e6), 0 < peak < 0.6 * 3650 * fpb["rows"], (peak, est_mem))
    print("\nqa_bigdata: PASS=%d FAIL=%d" % (PASS, FAIL))
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 -- a crash is a FAIL, shown as one
        import traceback
        print("  FAIL  CRASH: %s: %s" % (type(exc).__name__, str(exc)[:300]))
        traceback.print_exc()
        sys.exit(1)
