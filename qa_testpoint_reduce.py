#!/usr/bin/env python3
"""qa_testpoint_reduce.py -- teeth for padb_testpoint_reduce (2026-09-16).

Builds a synthetic sweep with KNOWN structure (flat interior + a real bump + a
spec-limit transition + a planted fail) and asserts the recommender:
  * DROPS flat, reconstructable interior points,
  * PROTECTS endpoints, the transition edge, the planted fail, and the real bump,
  * respects the per-condition floor,
  * derives the reduction ceiling from the data (fails -> tight ceiling).
Negative controls prove each protection is load-bearing (remove the cause -> the
frequency becomes droppable), so this can't pass vacuously.
"""
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

import padb_testpoint_reduce as R

_PASS, _FAIL = [], []


def check(desc, cond, detail=""):
    (_PASS if cond else _FAIL).append(desc)
    print(f"  {'PASS' if cond else 'FAIL'}  {desc}" + ("" if cond or not detail else f" -- {detail}"))


def _args(**over):
    d = dict(mode="adaptive", k=1.0, eps=0.2, mu_frac=0.5, mu=0.0, denv=0.0,
             target_pct=30.0, floor=5, margin_k=3.0, fragile=[])
    d.update(over)
    return types.SimpleNamespace(**d)


def _synth(with_fail=True, bump_at=20, transition_at=30):
    """2 conditions (low-noise + high-noise), 40 freqs. Returns a scatter-style df
    already shaped like padb_plots._load_scatter_for_stats output."""
    rng = np.random.default_rng(7)
    rows = []
    for cond, noise, base in (("0", 0.05, -70.0), ("1", 3.0, -65.0)):
        for f in range(1, 41):
            sig = base + (7.0 if f == bump_at else 0.0)
            hi = -50.0 if f < transition_at else -44.0
            for di in range(6):
                v = sig + rng.normal(0, noise)
                if with_fail and cond == "0" and f == 10 and di == 0:
                    v = -30.0  # above the -50 upper limit -> fail
                rows.append({"Frequency_MHz": float(f), "Value": v,
                             "Group": f"Amp State: {cond}  Serial Number: US6500000{di}",
                             "Upper_Limit": hi, "Lower_Limit": -110.0,
                             "Spec_Hi": np.nan, "Spec_Lo": np.nan,
                             "Unc_Hi": np.nan, "Unc_Lo": np.nan, "Temperature": "Room"})
    return pd.DataFrame(rows)


def _synth_runs(n_runs=60, broad=None, local=None):
    """All-runs shaped data: n_runs unit-runs, each measuring the same 40 freqs of one
    low-noise flat condition, upper limit -50. `broad` {run_idx: n_fail_freqs} makes that
    run fail at its first n freqs (a unit/setup problem); `local` {run_idx: n} does the
    same but meant for a handful (frequency-specific fails). 60 distinct run keys keeps
    'Test Run Datetime' out of the condition labels (>50 distinct), like real data."""
    rng = np.random.default_rng(11)
    broad = broad or {}
    local = local or {}
    rows = []
    for ri in range(n_runs):
        ser = f"US6500000{ri % 6}"
        run = f"2026/06/{1 + ri // 24:02d} {ri % 24:02d}:{ri:02d}:00"
        nfail = broad.get(ri, local.get(ri, 0))
        for f in range(1, 41):
            v = -70.0 + rng.normal(0, 0.3)
            if f <= nfail:
                v = -30.0
            rows.append({"Frequency_MHz": float(f), "Value": v,
                         "Group": f"Amp State: 0  Serial Number: {ser}  Test Run Datetime: {run}",
                         "Upper_Limit": -50.0, "Lower_Limit": np.nan,
                         "Spec_Hi": np.nan, "Spec_Lo": np.nan, "Unc_Hi": np.nan,
                         "Unc_Lo": np.nan, "Temperature": "Room", "Serial": ""})
    return pd.DataFrame(rows)


def _run(df, args):
    conds, grid = R._build_conditions(df)
    prot = R._protected_frequencies(df, grid, conds, float(args.margin_k))
    retained, dropped, eps, ret_count, floor = R.reduce_testpoints(conds, grid, prot, args, 0.0)
    return conds, grid, prot, set(retained), {f for f, _, _ in dropped}, ret_count


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("qa_testpoint_reduce -- teeth for the trim recommender")
    args = _args()
    df = _synth()
    conds, grid, prot, retained, dropped, ret_count = _run(df, args)

    check("drops flat interior points", len(dropped) >= 8, f"dropped={len(dropped)}")
    check("protects both endpoints", 1.0 in retained and 40.0 in retained)
    check("protects the spec-limit transition edge (30)",
          30.0 in prot and 30.0 in retained, f"reason={prot.get(30.0)}")
    check("protects the planted fail (10)",
          prot.get(10.0) == "spec fail" and 10.0 in retained)
    check("protects the real bump (20) via reconstruction",
          20.0 in retained and 20.0 not in dropped)
    check("respects the per-condition floor",
          all(ret_count[c.label] >= args.floor for c in conds))
    # data-derived ceiling: fails present -> tight (0.20) governing ceiling
    ceils = [c.risk_ceiling(args.margin_k) for c in conds]
    gov = min(cl for _, cl in ceils)
    check("data-driven ceiling tightens when fails present (<=0.20)", gov <= 0.20 + 1e-9,
          f"gov={gov}")

    # --- Negative controls: prove protections are load-bearing ---
    # (a) remove the fail -> freq 10 becomes droppable (no longer protected).
    df_nofail = _synth(with_fail=False)
    _, _, prot2, retained2, dropped2, _ = _run(df_nofail, args)
    check("TEETH: without the fail, freq 10 is no longer fail-protected",
          prot2.get(10.0) != "spec fail")
    # (b) the real bump is what keeps 20: flatten it -> 20 becomes droppable.
    df_flat = _synth(bump_at=-1)  # no bump anywhere
    _, _, prot3, retained3, dropped3, _ = _run(df_flat, args)
    check("TEETH: without the bump, freq 20 becomes droppable",
          20.0 in dropped3 and 20.0 not in prot3)
    # (c) fragility override caps a clear condition's ceiling downward.
    frag = R._parse_fragile(["Amp State: 1:0.10"])
    fc = R._fragile_ceiling("Amp State: 1  x", frag)
    check("TEETH: fragility override applies a lower ceiling", fc == 0.10, f"fc={fc}")

    # safety invariant: no protected point was ever dropped
    check("no protected frequency is ever in the dropped set",
          not (set(prot) & dropped))

    # --- Gross-failing runs (2026-11-04) -------------------------------------
    # all-runs data: 60 unit-runs x 40 freqs. A broadband-failing run protects every
    # frequency it touches unless it is recognised as a unit/setup problem.
    def _pipeline(df, frac=0.10, min_n=25, share=0.25):
        d2, info = R._exclude_gross_runs(df, frac, min_n, share)
        conds_, grid_ = R._build_conditions(d2)
        prot_ = R._protected_frequencies(d2, grid_, conds_, float(args.margin_k))
        ret_, drp_, _e, _c, _fl = R.reduce_testpoints(conds_, grid_, prot_, args, 0.0)
        return d2, info, prot_, {f for f, _, _ in drp_}

    bad_run = 7  # fails broadly (30 of 40 freqs)
    dfg = _synth_runs(broad={bad_run: 30})
    _, info0, prot0, drop0 = _pipeline(dfg, frac=0)  # check disabled == old behaviour
    check("without the check, one broadband-failing run protects (nearly) the whole sweep",
          sum(1 for r in prot0.values() if r == "spec fail") >= 30 and len(drop0) <= 10,
          f"spec-fail-protected={sum(1 for r in prot0.values() if r == 'spec fail')} dropped={len(drop0)}")
    d1, info1, prot1, drop1 = _pipeline(dfg)
    check("gross run is detected and excluded (exactly that unit-run)",
          len(info1["excluded"]) == 1 and info1["excluded"][0]["fail_freqs"] == 30
          and len(d1) == len(dfg) - 40, f"{info1['excluded']}")
    check("excluding it frees the sweep to reduce again (many more drops)",
          len(drop1) >= 8 and len(drop1) > len(drop0), f"dropped {len(drop0)} -> {len(drop1)}")
    # frequency-specific fails must STAY protected and the run must NOT be excluded
    dfs = _synth_runs(local={9: 3})
    ds, infos, progs, drops = _pipeline(dfs)
    check("a run failing at only a few frequencies is not excluded",
          infos["excluded"] == [] and len(ds) == len(dfs), f"{infos['excluded']}")
    check("...and its failing frequencies stay protected", sum(
        1 for r in progs.values() if r == "spec fail") == 3)
    # systemic: most runs fail broadly -> exclude nothing, warn
    dfy = _synth_runs(broad={i: 30 for i in range(0, 30)})
    dy, infoy, _p, _d = _pipeline(dfy)
    check("systemic breadth (>25% of runs) excludes nothing and is flagged",
          infoy["systemic"] and infoy["excluded"] == [] and len(dy) == len(dfy),
          f"share={infoy['share']:.2f}")
    # report text carries the exclusion / warning
    txt_ex = "\n".join(R._gross_lines(info1, 0.10, 25, 0.25))
    txt_sy = "\n".join(R._gross_lines(infoy, 0.10, 25, 0.25))
    check("report lists the excluded run", "EXCLUDED" in txt_ex and "US65000001" in txt_ex)
    check("report warns on systemic failures", "WARNING" in txt_sy and "NOT excluded" in txt_sy)
    # no identifiers -> check skipped quietly, nothing excluded
    dfn = dfg.copy()
    dfn["Group"] = "Amp State: 0"
    dfn["Serial"] = ""
    dn, infon, _p, _d = _pipeline(dfn)
    check("no serial/run identifiers -> check skipped, nothing excluded",
          infon["excluded"] == [] and len(dn) == len(dfn) and bool(infon["note"]))

    print(f"\n{'=' * 60}\n  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
