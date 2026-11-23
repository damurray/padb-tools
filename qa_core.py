r"""qa_core.py -- the v3.0 core (padb_core) proven equivalent to the v2 code it replaces.

The RELEASED v2.5 code IS the oracle (loaded from the `v2.5` git tag, so it cannot drift as the live
viewer moves onto the core): nothing may depend on padb_core unless it answers exactly like the
code it replaced. Groups:

  CORE-PARSE   parse_group == padb_plots._parse_group_kv on a corpus of real-world Group strings
               (PADB value padding, multi-word keys/values, no-double-space fallback, colons in
               values, empty values); the v2 viewer's own parser differs ONLY on empty values --
               pinned here so stage 2 unifies it on purpose
  CORE-INDEX   GroupIndex (site / serial / typed condition dims) == the viewer's gmap table
  CORE-SPEC    FilterSpec: validation, JSON round trip, canonical key, and to_sql() == the viewer's
               _where() (string AND args) on 300 random specs
  CORE-QUERY   Dataset.query() == the viewer's scatter() (traces, hover groups, totals) across
               random specs, group-by modes and decimation caps
  CORE-LIMITS  limits as a VIEW: verdicts == a numpy oracle, == the viewer's UPDATE-based engine,
               spec stairs equal, clearing is exact, the recorded reason ('data' / 'override') is
               right per row
  CORE-BANDS   band counts (edges inclusive) == numpy

Honest-exits 3 when duckdb is unavailable.   py qa_core.py
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
PASS = FAIL = 0


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail)) if detail else "")


def _norm(r):
    """Compare what a user sees: group ids are internal (DuckDB's DISTINCT order differs per
    engine instance), so resolve each point's gid to its hover text before comparing."""
    out = dict(r)
    g = r.get("groups") or {}
    out["traces"] = [dict(tr, gi=[g.get(i) if i is not None else None for i in tr["gi"]]) for tr in r["traces"]]
    out["groups"] = sorted(g.values())
    return out


CORPUS = [
    "AlcState: TRUE  OA State: 0  SpurType: Close-in 10KHz high",
    "Frequency (MHz):  10  Port: RF1",                       # PADB value padding
    "Frequency (MHz):   100  Serial Number: US65080001",
    "Test Step: Room  Site: AMC2  Serial Number: MY5001",
    "Note: time 12:30:45  Port: RF2",                         # colon inside a value
    "A: 1 B: 2",                                              # no double space -> one fragment
    "plain text without keys",
    "Key:  Other: x",                                         # empty value for Key
    "Upper Spec: -76  Lower Spec: -90  Upper Uncertainty: 1.5",
    "  Leading: space   Trailing: value  ",
    "Mode: A  B  C  Port: RF1",                               # colon-less fragments merged
    "",
]


def load_oracle():
    """The v2.5 viewer, straight from the `v2.5` git tag: the released code is the oracle. (After stage 2
    the live padb_viewer IS built on padb_core, so using it here would be circular.) Skips the suite
    honestly (exit 3) when git or the tag is unavailable."""
    import importlib.util
    import subprocess
    try:
        src = subprocess.run(["git", "show", "v2.5:padb_viewer.py"], cwd=str(HERE), capture_output=True, check=True).stdout
    except Exception as exc:  # noqa: BLE001
        print("qa_core: cannot read the v2.5 oracle via git (%s) -- ENV-UNAVAILABLE, skipped" % exc)
        sys.exit(3)
    d = Path(tempfile.mkdtemp(prefix="qa_core_oracle_"))
    (d / "padb_viewer_v25.py").write_bytes(src)
    spec = importlib.util.spec_from_file_location("padb_viewer_v25", str(d / "padb_viewer_v25.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["padb_viewer_v25"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    try:
        import duckdb  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print("qa_core: duckdb missing (%s) -- ENV-UNAVAILABLE, skipped" % exc)
        return 3
    import pandas as pd
    import padb_limits as PL
    import padb_plots
    ov = load_oracle()
    from padb_core import Dataset, FilterSpec, GroupIndex, LimitTable, parse_group, site_of, serial_of
    import padb_core.conditions as C
    import qa_viewer_ui as fx

    # ---------------- CORE-PARSE ----------------
    diffs = [g for g in CORPUS if parse_group(g) != padb_plots._parse_group_kv(g)]
    check("CORE-PARSE: parse_group == padb_plots._parse_group_kv on the corpus (%d strings)" % len(CORPUS),
          not diffs, diffs)
    check("CORE-PARSE: PADB value padding re-merged ('Frequency (MHz):  10' keeps its 10)",
          parse_group("Frequency (MHz):  10  Port: RF1") == {"Frequency (MHz)": "10", "Port": "RF1"})
    check("CORE-PARSE: a colon inside a value is kept ('12:30:45')", parse_group("Note: time 12:30:45")["Note"] == "time 12:30:45")
    vdiff = [g for g in CORPUS if ov._parse_group_dims(g) != parse_group(g)]
    check("CORE-PARSE: the v2 viewer parser differs ONLY where a fragment has an empty value (pinned)",
          all(any(f.strip().endswith(":") for f in __import__("re").split(r"\s{2,}", g)) or not parse_group(g) for g in vdiff),
          vdiff)
    rnd = random.Random(5)
    keys = ["AlcState", "Port", "OA State", "Frequency (MHz)", "Serial Number", "Site", "Test Step", "SpurType"]
    vals = ["TRUE", "FALSE", "RF1", "RF2", "10", "100", "US65080001", "Close-in 10KHz high", "AMC2", "-3.5"]
    ok = True
    for _ in range(400):
        frags = ["%s:%s%s" % (rnd.choice(keys), " " * rnd.choice([1, 1, 2, 3]), rnd.choice(vals)) for _ in range(rnd.randint(1, 6))]
        g = ("  " * rnd.randint(1, 2)).join(frags)
        ok &= parse_group(g) == padb_plots._parse_group_kv(g)
    check("CORE-PARSE: 400 random Group strings, parse_group == padb_plots._parse_group_kv", ok)
    check("CORE-PARSE: site_of / serial_of",
          site_of("Site: AMC2  X: 1") == "AMC2" and site_of("X: 1") == "(all)"
          and serial_of("Serial Number: US1  X: 1") == "US1" and serial_of("US65080001 foo") is None
          and serial_of("US65080001 foo", bare_fallback=True) == "US65080001")

    # ---------------- fixtures ----------------
    tmp = Path(tempfile.mkdtemp(prefix="qa_core_"))
    fdir = tmp / "fx"
    fdir.mkdir()
    fx.make_fixture(fdir)
    fxq = fdir / "fixture.parquet"
    r2 = np.random.default_rng(21)
    n = 4000
    xs = r2.choice(np.round(np.linspace(1, 400, 90), 2), n)
    y = r2.normal(-80, 6, n).round(2)
    hi_data = np.where(xs <= 100, -76.0, np.where(xs <= 250, -72.0, np.nan))
    lo_data = np.where(xs > 300, -98.0, np.nan)
    ser = r2.integers(0, 6, n)
    alc = r2.integers(0, 2, n)
    port = r2.integers(0, 3, n)
    site = r2.integers(0, 2, n)
    big = pd.DataFrame({
        # Mode is CONSTANT (must not become a filter), Run has 60 values (> the 40 limit, must not either)
        "Group": ["AlcState: %s  Port: RF%d  Serial Number: US6508%04d  Site: %s  Mode: CW  Run: %d  Frequency (MHz): %g"
                  % ("TRUE" if a else "FALSE", 1 + p, s, "SR" if t else "AMC2", rn, xv)
                  for a, p, s, t, xv, rn in zip(alc, port, ser, site, xs, r2.integers(0, 60, n))],
        "Frequency (MHz)": xs, "SpurPower (dBc)": y, "Upper Limit": hi_data, "Lower Limit": lo_data,
        "Test Step": np.where(r2.random(n) < 0.3, "45.0 Deg C", "Room")})
    bigq = tmp / "big.parquet"
    big.to_parquet(bigq)

    for label, pq_path, x, yv, hi0, lo0 in (
            ("fixture", fxq, None, None, None, None),
            ("synthetic", bigq, xs, y, hi_data, lo_data)):
        core = Dataset(pq_path)
        old = ov.DataSet(pq_path, engine="duckdb")

        # ---------------- CORE-INDEX ----------------
        og = pd.DataFrame(old._all("SELECT g, gid, g_site, g_serial" + "".join(", d%d" % d["id"] for d in old.dims) + " FROM gmap ORDER BY g"),
                          columns=["g", "gid", "g_site", "g_serial"] + ["d%d" % d["id"] for d in old.dims])
        cg = core.index.frame().sort_values("g").reset_index(drop=True)
        check("CORE-INDEX[%s]: condition dimensions == the viewer's (%s)" % (label, [d["name"] for d in core.dims]),
              core.dims == old.dims, (core.dims, old.dims))
        check("CORE-INDEX[%s]: per-group site / serial / dimension columns == the viewer's gmap table" % label,
              list(cg.columns) == list(og.columns)
              and cg.drop(columns=["gid"]).fillna("<NA>").equals(og.drop(columns=["gid"]).fillna("<NA>")),
              (cg.head(3).to_dict("list"), og.head(3).to_dict("list")))
        if label == "synthetic":
            names = [d["name"] for d in core.dims]
            check("CORE-INDEX[synthetic]: dims are exactly AlcState + Port (Mode constant, Run >40 values, Serial/Site/Frequency excluded)",
                  names == ["AlcState", "Port"], names)
        check("CORE-META[%s]: rows / serials / sites / temps / x-range == the viewer's" % label,
              (core.n_rows, core.n_serials, core.sites, core.temps, core.x_min, core.x_max, core.has_limits)
              == (old.n_rows, old.n_serials, old.sites, old.temps, old.x_min, old.x_max, old.has_limits))

        # ---------------- CORE-SPEC ----------------
        dim_ids = core.dim_ids()
        rng = random.Random(9)
        sites_pool, temps_pool = core.sites, core.temps
        ok_sql = True
        bad = None
        for _ in range(300):
            lo = round(rng.uniform(0, 200), 2)
            hi = round(lo + rng.uniform(0, 250), 2)
            sp = FilterSpec(lo=lo, hi=hi,
                            sites=rng.sample(sites_pool, rng.randint(0, len(sites_pool))),
                            temps=rng.sample(temps_pool, rng.randint(0, len(temps_pool))),
                            pf=rng.choice(["all", "pass", "fail"]), conds={})
            for d in core.dims:
                if rng.random() < 0.5:
                    sp.conds[d["id"]] = rng.sample(d["values"], rng.randint(0, len(d["values"])))
            want = old._where(lo, hi, sp.sites, sp.temps, sp.pf, {str(k): v for k, v in sp.conds.items()})
            got = sp.to_sql(dim_ids)
            if want != got:
                ok_sql, bad = False, (want, got)
                break
        check("CORE-SPEC[%s]: FilterSpec.to_sql == the viewer's _where (string and args), 300 random specs" % label,
              ok_sql, bad)

        # ---------------- CORE-QUERY ----------------
        ok_q, bad = True, None
        for _ in range(60):
            lo = round(rng.uniform(0, 150), 2)
            hi = round(lo + rng.uniform(20, 300), 2)
            sp = FilterSpec(lo=lo, hi=hi, sites=rng.sample(sites_pool, rng.randint(0, len(sites_pool))),
                            pf=rng.choice(["all", "pass", "fail"]))
            for d in core.dims:
                if rng.random() < 0.4:
                    sp.conds[d["id"]] = rng.sample(d["values"], max(1, rng.randint(1, len(d["values"]))))
            by = rng.choice([None, ["site"], ["serial"], ["d0"] if core.dims else ["site"], ["site", "temp"]])
            cap = rng.choice([20, 60, 4000])
            a = core.query(sp, cap, by)
            b = old.scatter(lo, hi, sp.sites, cap, sp.temps or None, sp.pf,
                            {str(k): v for k, v in sp.conds.items()} or None, by)
            if _norm(a) != _norm(b):
                ok_q, bad = False, (sp.to_dict(), by, cap, a["n_total"], b["n_total"], a["n_returned"], b["n_returned"])
                break
        check("CORE-QUERY[%s]: Dataset.query == the viewer's scatter() (traces, hover groups, totals), 60 random cases" % label,
              ok_q, bad)
        full_spec = FilterSpec(lo=core.x_min, hi=core.x_max)
        check("CORE-QUERY[%s]: counts() and band_counts() == the viewer / numpy" % label,
              core.counts(full_spec) == core.n_rows
              and core.count_in_bands([{"lo": 0, "hi": 150}, {"lo": 150, "hi": 320}, {"lo": 320, "hi": 400}])
              == [int(old._all("SELECT count(*) FROM pts WHERE x >= ? AND x <= ?", [a, b])[0][0])
                  for a, b in ((0, 150), (150, 320), (320, 400))], label)

    # ---------------- CORE-LIMITS (synthetic) ----------------
    core, old = Dataset(bigq), ov.DataSet(bigq, engine="duckdb")
    sets = {
        "stair": [{"lo": 0, "hi": 150, "upper": -85}, {"lo": 150, "hi": 400, "upper": -78}],
        "lower only": [{"lo": 200, "hi": 400, "lower": -88}],
        "overlap, later wins": [{"lo": 0, "hi": 400, "upper": -90}, {"lo": 120, "hi": 180, "upper": -70}],
    }
    base_f = int((((~np.isnan(hi_data)) & (y > hi_data)) | ((~np.isnan(lo_data)) & (y < lo_data))).sum())
    allspec = FilterSpec(lo=0, hi=1e9)
    check("CORE-LIMITS: before any override the data's own verdicts hold (fails == numpy oracle)",
          core.verdicts(allspec)["data"]["fail"] == base_f and core.verdicts(allspec)["override"] == {"pass": 0, "fail": 0},
          (core.verdicts(allspec), base_f))
    for label, raw in sets.items():
        lims = PL.validate(raw)[0]
        core.set_limits(lims)
        old.set_limits(lims)
        eh, el = PL.effective(xs, hi_data, lo_data, lims)
        fail = ((~np.isnan(eh)) & (y > eh)) | ((~np.isnan(el)) & (y < el))
        hv, hm, lv, lm = PL.override_arrays(xs, lims)
        cov = hm | lm
        v = core.verdicts(allspec)
        want = {"data": {"pass": int((~fail & ~cov).sum()), "fail": int((fail & ~cov).sum())},
                "override": {"pass": int((~fail & cov).sum()), "fail": int((fail & cov).sum())}}
        check("CORE-LIMITS[%s]: verdict tally AND the recorded reason (data/override) == numpy oracle" % label, v == want, (v, want))
        got = [(core.counts(FilterSpec(lo=a, hi=b, pf="fail")), core.counts(FilterSpec(lo=a, hi=b, pf="pass"))) for a, b in ((0, 400), (50, 130), (320, 400))]
        oldn = [(old.scatter(a, b, None, 4000, None, "fail")["n_total"], old.scatter(a, b, None, 4000, None, "pass")["n_total"]) for a, b in ((0, 400), (50, 130), (320, 400))]
        check("CORE-LIMITS[%s]: Passing/Failing totals == the viewer's UPDATE-based engine" % label, got == oldn, (got, oldn))
        check("CORE-LIMITS[%s]: spec stairs == the viewer's" % label, core.spec_segments() == old.spec_segments())
    ov_f = core.verdicts(allspec)["override"]["fail"] + core.verdicts(allspec)["data"]["fail"]
    check("CORE-LIMITS teeth: an override really changes the number of fails (%d -> %d)" % (base_f, ov_f), ov_f != base_f)
    core.set_limits([])
    check("CORE-LIMITS: clearing restores the data's own verdicts EXACTLY and costs no row rewrite (a view)",
          core.verdicts(allspec) == {"data": {"pass": core.n_rows - base_f, "fail": base_f}, "override": {"pass": 0, "fail": 0}}
          and core.limits.to_list() == [] and core.has_limits is True)
    core.set_limits([])
    check("CORE-LIMITS: clearing twice is idempotent", core.verdicts(allspec)["data"]["fail"] == base_f)
    try:
        core.set_limits([{"lo": 5, "hi": 1, "upper": 0}])
        rejected = False
    except ValueError:
        rejected = True
    check("CORE-LIMITS: an invalid override is rejected and leaves the engine unchanged",
          rejected and core.verdicts(allspec)["data"]["fail"] == base_f)
    nolim = tmp / "nolim.parquet"
    big.drop(columns=["Upper Limit", "Lower Limit"]).to_parquet(nolim)
    nl = Dataset(nolim)
    was = nl.has_limits
    nl.set_limits([{"lo": 0, "hi": 400, "upper": -80}])
    got = nl.counts(FilterSpec(lo=0, hi=1e9, pf="fail"))
    nl.set_limits([])
    check("CORE-LIMITS: limit-less data gains a pass/fail basis from an override and loses it on clear",
          was is False and got == int((y > -80).sum()) and nl.has_limits is False, (was, got, int((y > -80).sum())))

    # ---------------- CORE-SPEC (value semantics) ----------------
    s = FilterSpec(lo=1, hi=9, sites=["b", "a"], conds={1: ["y", "x"]}, pf="fail")
    s2 = FilterSpec.from_json(s.to_json())
    check("CORE-SPEC: JSON round trip preserves the spec", s2.to_dict() == s.to_dict(), (s.to_dict(), s2.to_dict()))
    check("CORE-SPEC: the canonical key ignores list order",
          s.key() == FilterSpec(lo=1, hi=9, sites=["a", "b"], conds={1: ["x", "y"]}, pf="fail").key())
    check("CORE-SPEC: different specs give different keys", s.key() != FilterSpec(lo=1, hi=9, pf="fail").key())
    for label, bad in {"lo above hi": {"lo": 9, "hi": 1}, "bad pf": {"pf": "maybe"}}.items():
        try:
            FilterSpec.from_dict(bad)
            raised = False
        except ValueError:
            raised = True
        check("CORE-SPEC: rejects %s" % label, raised)
    check("CORE-SPEC: an empty cond list means NOTHING (every value unticked)",
          core.counts(FilterSpec(lo=0, hi=1e9, conds={core.dims[0]["id"]: []})) == 0)
    check("CORE-SPEC: an unknown dimension id is ignored (not an error)",
          core.counts(FilterSpec(lo=0, hi=1e9, conds={999: ["x"]})) == core.n_rows)

    # ---------------- CORE-ADOPT: the viewer really runs on the core (no second copy) ----------------
    vsrc = (HERE / "padb_viewer.py").read_text(encoding="utf-8")
    import padb_viewer as live
    check("CORE-ADOPT: the viewer's DuckDB engine IS a padb_core.Dataset subclass",
          issubclass(live.DuckDataSet, Dataset), live.DuckDataSet.__mro__)
    check("CORE-ADOPT: the viewer no longer builds WHERE SQL itself (one builder: FilterSpec.to_sql)",
          '"site IN ("' not in vsrc and "IS NULL OR d" not in vsrc, "viewer still builds filter SQL")
    check("CORE-ADOPT: the viewer has no private Group parser / dimension rules (one parser: padb_core)",
          "def _parse_group_dims" not in vsrc and "ser_pat" not in vsrc and "len(vs) < 2" not in vsrc)
    check("CORE-ADOPT: the viewer does not rewrite limit rows (limits are a view: no UPDATE pts)",
          "UPDATE pts" not in vsrc)
    check("CORE-ADOPT: viewer parser == core parser (the empty-value difference from v2.5 is now unified)",
          live._parse_group_dims("Key:  Other: x") == parse_group("Key:  Other: x") == {"Other": "x"})

    # ---------------- CORE-BOUNDARY: analysis must not depend on extraction / scheduling ----------------
    import ast

    EXTRACTION = {"padb_run", "padb_batch", "padb_scheduler", "padb_make_job", "padb_make_v2_job",
                  "padb_convert_site", "padb_config", "padb_sites", "webapp"}
    ANALYSIS = (sorted(str(p.relative_to(HERE)).replace("\\", "/") for p in (HERE / "padb_core").glob("*.py"))
                + ["padb_v2.py", "padb_plots.py", "padb_refstats.py", "padb_pdf_report.py", "padb_compress.py",
                   "padb_bands.py", "padb_limits.py", "padb_loader_adapter.py", "padb_viewer.py",
                   "padb_csv_to_parquet.py", "padb_make_bands.py", "padb_csv_check.py"])

    def imports_of(src):
        found = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
        return found

    check("CORE-BOUNDARY teeth: the scanner finds an extraction import (positive control)",
          imports_of("def f():\n    import padb_run\nfrom padb_batch import x") & EXTRACTION == {"padb_run", "padb_batch"})
    coupled = {}
    for rel in ANALYSIS:
        hit = imports_of((HERE / rel).read_text(encoding="utf-8")) & EXTRACTION
        if hit:
            coupled[rel] = sorted(hit)
    # The ONE known coupling (padb_v2 -> padb_run: filename_stem_variants + the optional PADB-R runner,
    # both to move out in the separation stage). Anything NEW fails; padb_core must have none.
    allowed = {"padb_v2.py": ["padb_run"]}
    check("CORE-BOUNDARY: analysis modules import NO extraction/scheduling module beyond the pinned exception",
          all(rel in allowed and hit == allowed[rel] for rel, hit in coupled.items()), coupled)
    check("CORE-BOUNDARY: padb_core is strictly independent of extraction/scheduling (and of the webapp)",
          not any(r.startswith("padb_core/") for r in coupled), coupled)

    print("\nqa_core: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
