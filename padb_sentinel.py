#!/usr/bin/env python3
"""padb_sentinel.py -- run-to-run sentinel / audit-diff detector (2026-09-16).

The companion to padb_testpoint_reduce.py. Given an all-runs, run-dated reduction
CSV (from a reduction_extraction study -- ideally the merged multi-site compare),
it looks at each test frequency ACROSS a unit's ordered runs and classifies it:

  * CONFIRMED SENTINEL -- some unit FAILS at this frequency on an early run, then
    PASSES on a later run (after a repair/cal). The point caught a real, fixable
    defect: it earned its keep. NEVER a reduction candidate.
  * UNRESOLVED FAIL -- still failing on the unit's latest run (defect not yet
    fixed, or a persistent issue). Investigate; not a clean sentinel signal.
  * REGRESSION / RE-EXPANSION TRIGGER -- passed on earlier runs (or on the primary
    site) but FAILS on a later run (or on the onboarding site). A NEW fault mode
    emerged -> the reduced screen must re-expand to cover it. This is the
    empirical answer to "how do we catch unknown future modes."
  * REDUNDANCY CANDIDATE -- passes on every run of every unit. Only these are
    trimmable (still only characterization-redundancy, see padb_testpoint_reduce).

A run in which a unit fails at ~every frequency is treated as a TEST/FIXTURE issue
(not a hardware/cal fault) and excluded from the per-frequency sentinel logic --
per the rule "fails every point => test issue, not a hardware issue."

Report only; nothing is modified. Writes <stem>_sentinel_audit.txt / .csv.

    py padb_sentinel.py <reduction.csv> [--primary-site SR] [--x-col ...]
       [--test-issue-frac 0.8] [--out DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import padb_plots


def _prep(csv_path: Path, x_col: str | None) -> pd.DataFrame:
    """Load + parse into (site, serial, freq, run, value, hi, lo) with a per-row
    pass/fail, reusing the same loader + Group parsing (incl. the derived per-DUT
    run index) the plots use, so this can't drift from what the views show."""
    df = padb_plots._load_scatter_for_stats(csv_path, x_col=x_col)
    df = padb_plots._parse_group_fields(df)
    if "_grp_Run" not in df.columns:
        raise SystemExit(
            "[FAIL] this CSV has no per-run identity -- run a reduction_extraction "
            "study (all-runs + 'Test Run Datetime' grouping) first. See "
            "padb_run.apply_reduction_extraction / the reduction workflow.")
    # Resolve serial the SAME way _derive_run_index does (so per-DUT run indexing
    # and per-DUT sentinel analysis agree): _serial_id, else a _grp_Serial* column,
    # else the Serial column. Without this, DUTs whose serial lives only in the
    # Group would all collapse to "" and be pooled -- a fail on one unit + a pass
    # on another would falsely look like a fail->pass recovery on one unit.
    if "_serial_id" in df.columns:
        _serial = df["_serial_id"].astype(str)
    else:
        _sc = next((c for c in df.columns if c.startswith("_grp_")
                    and any(k in c[5:].lower() for k in ("serial", "unit id", "dut id"))), None)
        _serial = (df[_sc].astype(str) if _sc
                   else (df["Serial"].astype(str) if "Serial" in df.columns
                         else pd.Series([""] * len(df), index=df.index)))
    out = pd.DataFrame({
        "freq": pd.to_numeric(df["Frequency_MHz"], errors="coerce"),
        "value": pd.to_numeric(df["Value"], errors="coerce"),
        "hi": pd.to_numeric(df.get("Upper_Limit"), errors="coerce"),
        "lo": pd.to_numeric(df.get("Lower_Limit"), errors="coerce"),
        "serial": _serial,
        "site": (df["_grp_Site"].astype(str) if "_grp_Site" in df.columns else ""),
        "run": df["_grp_Run"].str.extract(r"(\d+)").iloc[:, 0].astype(float),
        "run_label": (df["_grp_Test Run Datetime"].astype(str)
                      if "_grp_Test Run Datetime" in df.columns else ""),
    }).dropna(subset=["freq", "value", "run"])
    has = out["hi"].notna() | out["lo"].notna()
    fail = (out["hi"].notna() & (out["value"] > out["hi"])) | \
           (out["lo"].notna() & (out["value"] < out["lo"]))
    out["scored"] = has
    out["rowfail"] = fail & has
    out.attrs["summary_lines"] = padb_plots.dataset_summary_lines(df)
    return out


def analyze(df: pd.DataFrame, primary_site: str | None, test_issue_frac: float) -> dict:
    # 1) collapse to per (site, serial, freq, run): fail = any measurement fails.
    pt = (df.groupby(["site", "serial", "freq", "run"], sort=False)
            .agg(fail=("rowfail", "any"), scored=("scored", "any"),
                 run_label=("run_label", "first")).reset_index())
    pt = pt[pt["scored"]]
    if pt.empty:
        return {"empty": True}

    # 2) TEST-ISSUE runs: a (site, serial, run) that fails at >= frac of its
    #    scored frequencies is a test/fixture problem, not a hardware fault.
    runstat = (pt.groupby(["site", "serial", "run"], sort=False)
                 .agg(nfail=("fail", "sum"), n=("fail", "size"),
                      run_label=("run_label", "first")).reset_index())
    runstat["frac"] = runstat["nfail"] / runstat["n"]
    runstat["test_issue"] = runstat["frac"] >= test_issue_frac
    ti = runstat[runstat["test_issue"]].copy()
    ti_keys = set(zip(ti["site"], ti["serial"], ti["run"]))
    pt["test_issue"] = [(s, r, rn) in ti_keys for s, r, rn in
                        zip(pt["site"], pt["serial"], pt["run"])]

    seq = pt[~pt["test_issue"]]

    # 3) per (site, serial, freq): the ordered fail/pass sequence over real runs.
    confirmed, unresolved, regression = set(), set(), set()
    quiet_units: dict[float, bool] = {}   # freq -> all its units all-pass so far
    fail_any: dict[float, bool] = {}
    site_pass = {}   # (freq) -> primary-site all-pass?  site_fail -> other-site fails
    prim_pass_freqs, other_fail_freqs = {}, {}

    for (site, serial, freq), grp in seq.groupby(["site", "serial", "freq"], sort=False):
        g = grp.sort_values("run")
        fails = g["fail"].tolist()
        runs = g["run"].tolist()
        any_fail = any(fails)
        # fail -> later pass  ?
        ftp = any(fails[i] and (False in fails[i + 1:]) for i in range(len(fails)))
        # pass -> later fail  ?
        ptf = any((not fails[i]) and (True in fails[i + 1:]) for i in range(len(fails)))
        last_fail = fails[-1]
        if ftp:
            confirmed.add(freq)
        if ptf:
            regression.add(freq)
        if last_fail:
            unresolved.add(freq)
        fail_any[freq] = fail_any.get(freq, False) or any_fail
        quiet_units[freq] = quiet_units.get(freq, True) and (not any_fail)
        # cross-site tracking
        if primary_site is not None:
            if site == primary_site:
                prim_pass_freqs[freq] = prim_pass_freqs.get(freq, True) and (not any_fail)
            else:
                other_fail_freqs[freq] = other_fail_freqs.get(freq, False) or any_fail

    all_freqs = sorted(pt["freq"].unique())
    reexpansion = set()
    if primary_site is not None:
        for f in all_freqs:
            if prim_pass_freqs.get(f, True) and other_fail_freqs.get(f, False):
                reexpansion.add(f)
    # redundancy candidates: quiet on every unit/run (never failed anywhere).
    redundancy = {f for f in all_freqs if quiet_units.get(f, True) and not fail_any.get(f, False)}

    return {
        "empty": False, "freqs": all_freqs,
        "confirmed": sorted(confirmed), "unresolved": sorted(unresolved),
        "regression": sorted(regression), "reexpansion": sorted(reexpansion),
        "redundancy": sorted(redundancy),
        "test_issue_runs": ti.sort_values(["site", "serial", "run"]).to_dict("records"),
        "n_runs": int(pt.groupby(["site", "serial", "run"]).ngroups),
        "n_units": int(pt.groupby(["site", "serial"]).ngroups),
        "primary_site": primary_site,
    }


def _fmt(v, d=4):
    return f"{v:.{d}f}"


def build_report(csv_path: Path, r: dict, summary_lines=None) -> tuple[str, str]:
    freqs = r["freqs"]
    conf, unres, regr = set(r["confirmed"]), set(r["unresolved"]), set(r["regression"])
    reexp, redun = set(r["reexpansion"]), set(r["redundancy"])
    L = [f"Sentinel / audit-diff -- {csv_path.name}", "=" * 66]
    if summary_lines:
        L.extend(summary_lines); L.append("")
    L += [f"Frequencies analysed : {len(freqs)}",
         f"Units x runs         : {r['n_units']} units, {r['n_runs']} (unit,run) test events",
         f"Primary site         : {r['primary_site'] or '(single site)'}", ""]
    L.append(f"CONFIRMED SENTINELS  : {len(conf)}   (fail -> pass across a repair/cal -- NEVER trim)")
    L.append(f"UNRESOLVED FAILS     : {len(unres)}   (still failing on the latest run -- investigate)")
    L.append(f"REGRESSIONS          : {len(regr)}   (passed earlier, fails later -- new fault mode)")
    if r["primary_site"]:
        L.append(f"  of which CROSS-SITE re-expansion triggers: {len(reexp)}   "
                 f"(clean at {r['primary_site']}, fails at another site)")
    L.append(f"REDUNDANCY CANDIDATES: {len(redun)}   (pass on every run of every unit -- trimmable*)")
    L.append(f"TEST-ISSUE RUNS      : {len(r['test_issue_runs'])}   (a unit failing ~every point -> fixture/cal, excluded)")
    L.append("")
    L.append("* trimmable only as CHARACTERIZATION redundancy, and only from a screen backed by")
    L.append("  periodic full characterization + escape monitoring -- a quiet point may still be a")
    L.append("  dormant sentinel for an unseen fault mode (see the reduction methodology notes).")
    L.append("")

    def _list(title, fs):
        L.append(f"{title} ({len(fs)}):")
        if fs:
            L.append("  " + ", ".join(_fmt(x) for x in sorted(fs)[:80])
                     + (" ..." if len(fs) > 80 else ""))
        L.append("")
    _list("Confirmed sentinels (protect)", conf)
    _list("Re-expansion triggers (add back to the screen)", reexp if r["primary_site"] else regr)
    _list("Unresolved fails (investigate)", unres)

    if r["test_issue_runs"]:
        L.append("Test-issue runs (excluded from sentinel logic):")
        L.append(f"  {'site':<8}{'serial':<16}{'run':>4}  {'fail%':>6}  run label")
        for t in r["test_issue_runs"][:40]:
            L.append(f"  {str(t['site'])[:7]:<8}{str(t['serial'])[:15]:<16}{int(t['run']):>4}  "
                     f"{t['frac']*100:>5.0f}%  {t.get('run_label','')}")
        if len(r["test_issue_runs"]) > 40:
            L.append(f"  ... and {len(r['test_issue_runs'])-40} more")
        L.append("")
    L.append("Advisory only -- nothing modified. Confirmed sentinels + re-expansion triggers")
    L.append("feed padb_testpoint_reduce as hard-protected points.")

    # per-frequency CSV
    rows = ["Frequency,Classification,Confirmed_Sentinel,Unresolved,Regression,Reexpansion,Redundancy_Candidate"]
    for f in freqs:
        tags = []
        if f in conf: tags.append("sentinel")
        if f in reexp: tags.append("reexpansion")
        elif f in regr: tags.append("regression")
        if f in unres: tags.append("unresolved")
        if f in redun: tags.append("redundancy_candidate")
        cls = tags[0] if tags else "quiet"
        rows.append(f"{_fmt(f)},{cls},{f in conf},{f in unres},{f in regr},{f in reexp},{f in redun}")
    return "\n".join(L) + "\n", "\n".join(rows) + "\n"


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Run-to-run sentinel / audit-diff detector (report only).")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--primary-site", default=None, help="reference site name (enables cross-site re-expansion triggers)")
    ap.add_argument("--test-issue-frac", type=float, default=0.8, help="a unit failing >= this fraction of points in a run is a test issue (default 0.8)")
    ap.add_argument("--x-col", default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if not args.csv.exists():
        print(f"[FAIL] CSV not found: {args.csv}")
        sys.exit(1)
    df = _prep(args.csv, args.x_col)
    r = analyze(df, args.primary_site, float(args.test_issue_frac))
    if r.get("empty"):
        print("[NOTE] no scored (limit-bearing) measurements -- nothing to audit.")
        sys.exit(0)
    txt, csv_txt = build_report(args.csv, r, df.attrs.get("summary_lines"))
    out_dir = args.out or args.csv.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.csv.stem
    (out_dir / f"{stem}_sentinel_audit.txt").write_text(txt, encoding="utf-8")
    (out_dir / f"{stem}_sentinel_audit.csv").write_text(csv_txt, encoding="utf-8")
    if not args.quiet:
        print(txt)
    print(f"[OK] wrote {stem}_sentinel_audit.txt / .csv to {out_dir}")
    sys.exit(0)


if __name__ == "__main__":
    main()
