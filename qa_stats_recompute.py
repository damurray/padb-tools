#!/usr/bin/env python3
r"""qa_stats_recompute.py -- gap #2: independent recompute of the tool's statistics.

The self-consistency checks (qa_filters / qa_gf_crossview: table<->plot agreement,
cross-view GF agreement, reversibility, determinism) have ONE structural blind
spot: a value that is *uniformly wrong everywhere*. If the tool feeds the wrong
population into a statistic -- raw rows instead of per-DUT means, all temperatures
instead of Room-only, an off-by-one order statistic for the tolerance interval --
every view agrees with every other view and every table matches its own plot,
because they all read the same wrong number. Self-consistency cannot catch that;
only an INDEPENDENT recompute against known ground truth can.

This gate does exactly that. It builds a deterministic synthetic dataset whose
true per-measurement values come from a closed-form function (qa_padb._synth_value),
so the correct membership of every (condition, temperature, frequency, DUT) bucket
and every true value is known analytically -- NOT re-derived from the tool's own
dataframe. It then:

  * calls the tool's real aggregators directly (padb_plots._aggregate_stat_data and
    _aggregate_box_data_by_temp -- the exact functions stat_summary and boxplot ship),
  * independently recomputes mean / std / Q1 / Q2 / Q3 / whiskers / outliers /
    Shapiro W,p / non-parametric tolerance interval / delta-env from the analytic
    ground truth,
  * and compares field-by-field within tolerance.

The independence that matters here is in the DATA SELECTION and the ground-truth
values (which a uniformly-wrong bug corrupts), not in re-deriving the arithmetic of
np.mean() -- so a mismatch means the tool selected the wrong data or plumbed it
wrongly, precisely the class self-consistency is blind to.

Browser-free and deterministic, so it belongs in qa_selfcheck's core suite.

Usage:
    python qa_stats_recompute.py
Exit codes: 0 = all checks pass, 1 = one or more failures.
"""
from __future__ import annotations

import sys
from pathlib import Path

import warnings

import numpy as np
import pandas as pd

# scipy.stats.shapiro emits a benign "invalid value in scalar divide" RuntimeWarning
# on some degenerate populations (e.g. a perfectly linear ramp); it doesn't affect
# the returned W/p we compare. Keep the gate's output clean.
warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"scipy\.stats.*")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import padb_plots
from padb_plots import (
    _aggregate_stat_data,
    _aggregate_box_data_by_temp,
    _k_one_sided,
)
import qa_padb
from qa_padb import SERIALS, HARMONICS, PORTS, TEMPS, FREQS, _synth_value

TOL = 1e-6  # tool rounds every stat to 6 decimals

# ---------------------------------------------------------------------------
# Result tracking (same style as qa_padb.py / qa_js_segments.py)
# ---------------------------------------------------------------------------
_PASS: list[str] = []
_FAIL: list[str] = []


def check(desc: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASS.append(desc)
        print(f"  PASS  {desc}")
    else:
        _FAIL.append(desc)
        print(f"  FAIL  {desc}" + (f" -- {detail}" if detail else ""))


def _close(a, b, tol: float = TOL) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


# ---------------------------------------------------------------------------
# Independent statistics -- implemented from scratch (not calling the tool's).
# np.mean/std/percentile are the SAME library the tool uses on purpose: the
# suspected-bug class is wrong data SELECTION, so feeding the analytically-known
# value set through the standard formula is the independent check. The fence /
# whisker / NP-TI logic below is re-implemented to also cover formula drift.
# ---------------------------------------------------------------------------

def _indep_desc(vals: list[float]) -> dict:
    """Independent descriptive stats matching _aggregate_stat_data's definitions."""
    a = np.asarray(vals, dtype=float)
    n = len(a)
    mean = float(np.mean(a))
    s = float(np.std(a, ddof=1)) if n > 1 else 0.0
    q1 = float(np.percentile(a, 25))
    q2 = float(np.median(a))
    q3 = float(np.percentile(a, 75))
    iqr = q3 - q1
    # stat_summary whisker convention: clamp the fence to the actual data extent.
    lo_w = float(max(np.min(a), q1 - 1.5 * iqr))
    hi_w = float(min(np.max(a), q3 + 1.5 * iqr))
    outliers = sorted(float(v) for v in a if v < lo_w or v > hi_w)
    return {"n": n, "mean": mean, "s": s, "q1": q1, "q2": q2, "q3": q3,
            "lo_w": lo_w, "hi_w": hi_w, "outliers": outliers}


def _indep_box(vals: list[float]) -> dict:
    """Independent box stats matching _aggregate_box_data_by_temp._box_stats
    (fence/inlier whisker convention -- differs from stat_summary above)."""
    a = np.sort(np.asarray(vals, dtype=float))
    q1, q2, q3 = (float(np.percentile(a, p)) for p in (25, 50, 75))
    iqr = q3 - q1
    inl = a[(a >= q1 - 1.5 * iqr) & (a <= q3 + 1.5 * iqr)]
    lo_w = float(inl.min()) if len(inl) else float(a.min())
    hi_w = float(inl.max()) if len(inl) else float(a.max())
    outliers = sorted(float(v) for v in a if v < q1 - 1.5 * iqr or v > q3 + 1.5 * iqr)
    return {"n": len(a), "mean": float(np.mean(a)),
            "q1": q1, "q2": q2, "q3": q3, "lo_w": lo_w, "hi_w": hi_w,
            "outliers": outliers}


def _indep_shapiro(vals: list[float]):
    """Independent Shapiro-Wilk + the tool's Normal/Marginal/Non-normal labelling.
    Uses scipy on the analytically-known value set (data-selection independence)."""
    try:
        from scipy.stats import shapiro
    except ImportError:
        return None
    if len(vals) < 3:
        return (1.0, 1.0, "n<3")
    W, p = shapiro(np.asarray(vals, dtype=float))
    W, p = float(W), float(p)
    label = "Normal" if p > 0.10 else "Marginal" if p > 0.05 else "Non-normal"
    return (W, p, label)


def _indep_np_ti(vals: list[float], P: float, C: float):
    """Independent distribution-free (P,C) tolerance interval via symmetric order
    statistics -- re-implemented from the documented beta-cdf coverage formula."""
    try:
        from scipy.stats import beta as beta_dist
    except ImportError:
        return (None, None)
    sv = sorted(float(v) for v in vals)
    n = len(sv)
    if n < 2:
        return (None, None)
    best_d = None
    for d in range((n - 2) // 2 + 1):
        a = 2 * (d + 1)
        b = n - 2 * (d + 1) + 1
        if b < 1:
            break
        if float(beta_dist.cdf(1.0 - P, a, b)) >= C:
            best_d = d
        else:
            break
    if best_d is None:
        return (None, None)
    return (round(sv[best_d], 6), round(sv[n - best_d - 1], 6))


# ---------------------------------------------------------------------------
# Synthetic dataframes, built DIRECTLY from the analytic value function.
# Group carries HarmonicNumber (a condition), Port (pooled into serial id), and
# Serial Number -- so the tool's own _serial_id/_cond/_port derivation runs, and
# a condition == one HarmonicNumber pools all ports x serials as distinct DUTs.
# ---------------------------------------------------------------------------

def _group(h: int, port: str, serial: str) -> str:
    return f"HarmonicNumber: {h}  Port: {port}  Serial Number: {serial}"


def _build_main_df() -> pd.DataFrame:
    rows = []
    for temp_label, off in TEMPS.items():
        for h in HARMONICS:
            for port in PORTS:
                for i, ser in enumerate(SERIALS):
                    for f in FREQS:
                        rows.append({
                            "Frequency_MHz": f,
                            "Value": _synth_value(h, port, i, off, f),
                            "Group": _group(h, port, ser),
                            "Temperature": "Room" if temp_label == "Room" else temp_label,
                            "Upper_Limit": -50.0, "Lower_Limit": -110.0,
                            "Spec_Hi": np.nan, "Spec_Lo": np.nan,
                            "Unc_Hi": np.nan, "Unc_Lo": np.nan,
                        })
    return pd.DataFrame(rows)


def _room_duts(h: int, f: float) -> list[float]:
    """The correct Room per-DUT value set for condition HarmonicNumber=h at f:
    every (port, serial) is a distinct DUT (port is pooled into the serial id)."""
    return [_synth_value(h, p, i, 0.0, f) for p in PORTS for i in range(len(SERIALS))]


def _temp_rows(h: int, off: float, f: float) -> list[float]:
    """Raw measurement set for (condition h, temperature-offset off, f) -- boxplot."""
    return [_synth_value(h, p, i, off, f) for p in PORTS for i in range(len(SERIALS))]


def _cond_h(condition: str) -> int | None:
    import re
    m = re.search(r"HarmonicNumber:\s*(\d+)", condition)
    return int(m.group(1)) if m else None


def _sole_fs(out: list) -> dict | None:
    """Return the single freq_stats entry from a one-population aggregation.
    The single-cardinality mini-datasets in sections C/D/E have only one
    HarmonicNumber, so the tool correctly drops it as a non-varying condition
    (cardinality-1 -> _cond='All'); look the entry up without a condition filter."""
    for cd in out:
        for fs in cd["freq_stats"]:
            return fs
    return None


# ---------------------------------------------------------------------------
# Section A -- stat_summary aggregation vs independent recompute
# ---------------------------------------------------------------------------

def section_stat(df: pd.DataFrame) -> None:
    print("\n[A] stat_summary (_aggregate_stat_data) vs independent recompute")
    cfg = {"proportion": 0.90, "confidence": 0.90}
    out = _aggregate_stat_data(df, cfg)

    # Collect field-level agreement across every (condition, Room-frequency).
    fields = ["n", "mean", "s", "q1", "q2", "q3", "lo_w", "hi_w"]
    mism = {fld: [] for fld in fields}
    out_mism, shap_mism, npti_none = [], [], []
    compared = 0

    for cd in out:
        h = _cond_h(cd["condition"])
        if h is None:
            continue
        for fs in cd["freq_stats"]:
            f = fs["freq"]
            exp = _indep_desc(_room_duts(h, f))
            compared += 1
            for fld in fields:
                if fld == "n":
                    if int(fs["n"]) != int(exp["n"]):
                        mism["n"].append(f"h{h}@{f}: tool {fs['n']} vs {exp['n']}")
                elif not _close(fs[fld], exp[fld]):
                    mism[fld].append(f"h{h}@{f}: tool {fs[fld]} vs {exp[fld]:.6f}")
            # outliers (value set, sorted)
            if sorted(round(v, 6) for v in fs["outliers"]) != [round(v, 6) for v in exp["outliers"]]:
                out_mism.append(f"h{h}@{f}: tool {fs['outliers']} vs {exp['outliers']}")
            # Shapiro W/p/label
            sh = _indep_shapiro(_room_duts(h, f))
            if sh is not None:
                if not (_close(fs["W"], sh[0], 1e-4) and _close(fs["p"], sh[1], 1e-4)
                        and fs["norm"] == sh[2]):
                    shap_mism.append(f"h{h}@{f}: tool W={fs['W']},p={fs['p']},{fs['norm']} "
                                     f"vs W={sh[0]:.5f},p={sh[1]:.5f},{sh[2]}")
            # NP-TI: n=6 here (< ~39), must be None -- a number would be a bug.
            expnp = _indep_np_ti(_room_duts(h, f), 0.90, 0.90)
            if (fs["np_ti_lo"], fs["np_ti_up"]) != expnp:
                npti_none.append(f"h{h}@{f}: tool {(fs['np_ti_lo'], fs['np_ti_up'])} vs {expnp}")

    check(f"stat: aggregation produced condition rows (compared {compared} cond x freq)",
          compared > 0)
    for fld in fields:
        check(f"stat: {fld} matches independent recompute (all {compared})",
              not mism[fld], "; ".join(mism[fld][:3]))
    check("stat: outlier set matches independent recompute",
          not out_mism, "; ".join(out_mism[:3]))
    check("stat: Shapiro W/p/label match independent scipy on the same population",
          not shap_mism, "; ".join(shap_mism[:3]))
    check("stat: NP-TI correctly None at n=6 (below (P,C) feasibility)",
          not npti_none, "; ".join(npti_none[:3]))


# ---------------------------------------------------------------------------
# Section B -- boxplot aggregation vs independent recompute (all temps)
# ---------------------------------------------------------------------------

def section_box(df: pd.DataFrame) -> None:
    print("\n[B] boxplot (_aggregate_box_data_by_temp) vs independent recompute")
    # The box aggregator groups by _cond/_serial_id/_port/Temperature; those cols
    # are prepared by _aggregate_stat_data. Reuse that exact prep so we feed the
    # box aggregator the same shape its real caller does (rather than re-deriving).
    prepped = _prep_cond_cols(df)
    out = _aggregate_box_data_by_temp(prepped, x_unit="MHz")

    off_by_label = {("Room" if k == "Room" else k): v for k, v in TEMPS.items()}
    fields = ["n", "mean", "q1", "q2", "q3", "lo_w", "hi_w"]
    mism = {fld: [] for fld in fields}
    out_mism = []
    compared = 0

    for cd in out:
        h = _cond_h(cd["condition"])
        if h is None:
            continue
        off = off_by_label.get(cd["temp"])
        if off is None:
            check(f"box: temperature label recognised ({cd['temp']})", False,
                  f"unexpected temp {cd['temp']!r}")
            continue
        for fs in cd["freq_stats"]:
            f = fs["freq"]
            exp = _indep_box(_temp_rows(h, off, f))
            compared += 1
            for fld in fields:
                if fld == "n":
                    if int(fs["n"]) != int(exp["n"]):
                        mism["n"].append(f"h{h}/{cd['temp']}@{f}: {fs['n']} vs {exp['n']}")
                elif not _close(fs[fld], exp[fld]):
                    mism[fld].append(f"h{h}/{cd['temp']}@{f}: {fs[fld]} vs {exp[fld]:.6f}")
            if sorted(round(v, 6) for v in fs["outliers"]) != [round(v, 6) for v in exp["outliers"]]:
                out_mism.append(f"h{h}/{cd['temp']}@{f}: {fs['outliers']} vs {exp['outliers']}")

    check(f"box: aggregation produced rows (compared {compared} cond x temp x freq)",
          compared > 0)
    for fld in fields:
        check(f"box: {fld} matches independent recompute (all {compared})",
              not mism[fld], "; ".join(mism[fld][:3]))
    check("box: outlier set matches independent recompute",
          not out_mism, "; ".join(out_mism[:3]))


def _prep_cond_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Add _cond/_serial_id/_port exactly as _aggregate_stat_data does, by running
    it once and reusing the derivation -- keeps this test faithful to the shipped
    prep instead of re-implementing the Group parser. We re-derive here rather than
    mutating df in place inside the aggregator (which copies internally)."""
    # _aggregate_stat_data copies and derives the cols; the simplest faithful reuse
    # is to replicate its public derivation via the same helpers. It doesn't expose
    # the prepped df, so re-run the identical mapping through _parse_group_kv.
    from padb_plots import _parse_group_kv
    import re
    d = df.copy()
    kv = {g: _parse_group_kv(g) for g in d["Group"].dropna().unique()}

    def serial_id(g):
        k = kv.get(g, {})
        base = k.get("Serial Number", g)
        port = k.get("Port", "")
        return f"{base}_{port}" if port else base

    def cond(g):
        k = kv.get(g, {})
        # Only HarmonicNumber varies across conditions here (Port is pooled).
        return f"HarmonicNumber: {k['HarmonicNumber']}" if "HarmonicNumber" in k else "All"

    d["_serial_id"] = d["Group"].map(serial_id).fillna("unknown")
    d["_cond"] = d["Group"].map(cond).fillna("All")
    d["_port"] = d["Group"].map(lambda g: kv.get(g, {}).get("Port", "")).fillna("")
    return d


# ---------------------------------------------------------------------------
# Section C -- per-DUT averaging: n counts DUTs, not raw rows; repeats averaged
# ---------------------------------------------------------------------------

def section_averaging() -> None:
    print("\n[C] per-DUT averaging (repeats averaged before stats; n = DUT count)")
    rows = []
    # Condition h=9, one port, 3 serials at Room, freq 100.
    # S1 has TWO rows (10.0, 12.0 -> mean 11.0); S2 and S3 one row each (11.0).
    spec = [("S1", 10.0), ("S1", 12.0), ("S2", 11.0), ("S3", 11.0)]
    for ser, val in spec:
        rows.append({"Frequency_MHz": 100.0, "Value": val,
                     "Group": _group(9, "RF1", ser), "Temperature": "Room",
                     "Upper_Limit": -50.0, "Lower_Limit": -110.0,
                     "Spec_Hi": np.nan, "Spec_Lo": np.nan, "Unc_Hi": np.nan, "Unc_Lo": np.nan})
    df = pd.DataFrame(rows)
    out = _aggregate_stat_data(df, {"proportion": 0.90, "confidence": 0.90})
    fs = _sole_fs(out)
    check("avg: found the single-condition freq_stats entry", fs is not None)
    if fs is None:
        return
    check("avg: n == 3 DUTs (not 4 raw rows)", int(fs["n"]) == 3, f"n={fs['n']}")
    check("avg: dup_runs == 1 (S1's one extra run counted)", int(fs["dup_runs"]) == 1,
          f"dup_runs={fs['dup_runs']}")
    s1 = next((d for d in fs["dut_vals"] if str(d["s"]).startswith("S1")), None)
    check("avg: S1's per-DUT value == mean(10,12) == 11.0",
          s1 is not None and _close(s1["v"], 11.0), f"s1={s1}")
    # Mean across DUTs [11,11,11] == 11.0 (a summed-instead-of-averaged bug would differ).
    check("avg: population mean == 11.0", _close(fs["mean"], 11.0), f"mean={fs['mean']}")


# ---------------------------------------------------------------------------
# Section D -- NP-TI at large n: real bounds, independently reproduced
# ---------------------------------------------------------------------------

def section_np_ti() -> None:
    print("\n[D] non-parametric tolerance interval at large n (real bounds)")
    N = 50
    vals = [10.0 + i * 0.1 for i in range(N)]  # distinct, sorted, one per DUT
    rows = []
    for i, v in enumerate(vals):
        rows.append({"Frequency_MHz": 100.0, "Value": v,
                     "Group": _group(7, "RF1", f"NP{i:03d}"), "Temperature": "Room",
                     "Upper_Limit": -50.0, "Lower_Limit": -110.0,
                     "Spec_Hi": np.nan, "Spec_Lo": np.nan, "Unc_Hi": np.nan, "Unc_Lo": np.nan})
    df = pd.DataFrame(rows)
    out = _aggregate_stat_data(df, {"proportion": 0.90, "confidence": 0.90})
    fs = _sole_fs(out)
    check("npti: found the n=50 freq_stats entry", fs is not None)
    if fs is None:
        return
    check("npti: n == 50", int(fs["n"]) == 50, f"n={fs['n']}")
    exp_lo, exp_up = _indep_np_ti(vals, 0.90, 0.90)
    check("npti: bounds are real (not None) at n=50", exp_lo is not None and fs["np_ti_lo"] is not None,
          f"tool=({fs['np_ti_lo']},{fs['np_ti_up']}) indep=({exp_lo},{exp_up})")
    check("npti: lower bound matches independent order-statistic selection",
          _close(fs["np_ti_lo"], exp_lo), f"tool {fs['np_ti_lo']} vs {exp_lo}")
    check("npti: upper bound matches independent order-statistic selection",
          _close(fs["np_ti_up"], exp_up), f"tool {fs['np_ti_up']} vs {exp_up}")
    # Invariants: the bounds must be actual data points and bracket the median.
    if fs["np_ti_lo"] is not None:
        check("npti: bounds are actual order statistics present in the data",
              any(_close(fs["np_ti_lo"], v) for v in vals) and any(_close(fs["np_ti_up"], v) for v in vals))
        check("npti: lo <= median <= up",
              fs["np_ti_lo"] <= fs["q2"] <= fs["np_ti_up"],
              f"{fs['np_ti_lo']} <= {fs['q2']} <= {fs['np_ti_up']}")


# ---------------------------------------------------------------------------
# Section E -- outlier detection: a planted outlier must be flagged, and only it
# ---------------------------------------------------------------------------

def section_outlier() -> None:
    print("\n[E] outlier detection (planted outlier flagged in both aggregators)")
    tight = [10.0, 10.1, 9.9, 10.05]
    plant = 20.0
    rows = []
    for i, v in enumerate(tight + [plant]):
        rows.append({"Frequency_MHz": 100.0, "Value": v,
                     "Group": _group(8, "RF1", f"O{i}"), "Temperature": "Room",
                     "Upper_Limit": -50.0, "Lower_Limit": -110.0,
                     "Spec_Hi": np.nan, "Spec_Lo": np.nan, "Unc_Hi": np.nan, "Unc_Lo": np.nan})
    df = pd.DataFrame(rows)
    allvals = tight + [plant]
    exp = _indep_desc(allvals)
    check("outlier: independent recompute flags exactly the planted point",
          exp["outliers"] == [plant], f"indep outliers={exp['outliers']}")

    out = _aggregate_stat_data(df, {"proportion": 0.90, "confidence": 0.90})
    fs = _sole_fs(out)
    check("outlier(stat): found the entry", fs is not None)
    if fs is not None:
        check("outlier(stat): exactly the planted 20.0 flagged",
              [round(v, 6) for v in fs["outliers"]] == [plant], f"tool={fs['outliers']}")
        od = fs.get("outlier_detail", [])
        check("outlier(stat): outlier_detail attributes it to serial O4",
              len(od) == 1 and str(od[0]["s"]).startswith("O4") and _close(od[0]["v"], plant),
              f"detail={od}")

    boxout = _aggregate_box_data_by_temp(_prep_cond_cols(df), x_unit="MHz")
    bfs = next((fs for cd in boxout for fs in cd["freq_stats"] if _cond_h(cd["condition"]) == 8), None)
    check("outlier(box): found the entry", bfs is not None)
    if bfs is not None:
        check("outlier(box): exactly the planted 20.0 flagged",
              [round(v, 6) for v in bfs["outliers"]] == [plant], f"tool={bfs['outliers']}")


# ---------------------------------------------------------------------------
# Section F -- delta-env (paired temperature deltas): sign, clamp, combine
# ---------------------------------------------------------------------------

def section_denv(df: pd.DataFrame) -> None:
    print("\n[F] delta-env paired deltas (sign / clamp / per-temp / combined)")
    out = _aggregate_stat_data(df, {"proportion": 0.90, "confidence": 0.90})
    # For the main synthetic, every DUT's delta at a given temp is a constant
    # (= that temp's offset), so ds == 0 and denv is fully determined:
    #   this_up = max(0, offset),  this_lo = max(0, -offset)
    non_room = {("Room" if k == "Room" else k): v for k, v in TEMPS.items() if k != "Room"}
    up_mism, lo_mism, comb_mism = [], [], []
    compared = 0
    for cd in out:
        h = _cond_h(cd["condition"])
        if h is None:
            continue
        for fs in cd["freq_stats"]:
            compared += 1
            for tlabel, off in non_room.items():
                bt = fs["denv_by_temp"].get(tlabel)
                if bt is None:
                    up_mism.append(f"h{h}@{fs['freq']}: missing denv temp {tlabel}")
                    continue
                exp_up = max(0.0, off)
                exp_lo = max(0.0, -off)
                if not _close(bt["up"], exp_up):
                    up_mism.append(f"h{h}/{tlabel}@{fs['freq']}: up {bt['up']} vs {exp_up}")
                if not _close(bt["lo"], exp_lo):
                    lo_mism.append(f"h{h}/{tlabel}@{fs['freq']}: lo {bt['lo']} vs {exp_lo}")
            exp_comb_up = max(max(0.0, off) for off in non_room.values())
            exp_comb_lo = max(max(0.0, -off) for off in non_room.values())
            if not _close(fs["denv_up"], exp_comb_up):
                comb_mism.append(f"h{h}@{fs['freq']}: denv_up {fs['denv_up']} vs {exp_comb_up}")
            if not _close(fs["denv_lo"], exp_comb_lo):
                comb_mism.append(f"h{h}@{fs['freq']}: denv_lo {fs['denv_lo']} vs {exp_comb_lo}")

    check(f"denv: per-temp UP delta matches independent recompute (all {compared})",
          not up_mism, "; ".join(up_mism[:3]))
    check("denv: per-temp LO delta matches independent recompute",
          not lo_mism, "; ".join(lo_mism[:3]))
    check("denv: combined denv_up/denv_lo == max across temps",
          not comb_mism, "; ".join(comb_mism[:3]))


# ---------------------------------------------------------------------------
# Section G -- cross-aggregator agreement at Room (self-consistency bonus)
# ---------------------------------------------------------------------------

def section_cross(df: pd.DataFrame) -> None:
    print("\n[G] stat (Room) vs box (Room) agree on the same population")
    cfg = {"proportion": 0.90, "confidence": 0.90}
    stat = _aggregate_stat_data(df, cfg)
    box = _aggregate_box_data_by_temp(_prep_cond_cols(df), x_unit="MHz")
    # Index box Room entries by (condition-h, freq)
    box_room = {}
    for cd in box:
        if cd["temp"] != "Room":
            continue
        h = _cond_h(cd["condition"])
        for fs in cd["freq_stats"]:
            box_room[(h, fs["freq"])] = fs
    mism = []
    compared = 0
    for cd in stat:
        h = _cond_h(cd["condition"])
        for fs in cd["freq_stats"]:
            b = box_room.get((h, fs["freq"]))
            if b is None:
                continue
            compared += 1
            # mean/q1/q2/q3 are over the identical Room population (no repeats).
            for fld in ("mean", "q1", "q2", "q3"):
                if not _close(fs[fld], b[fld]):
                    mism.append(f"h{h}@{fs['freq']} {fld}: stat {fs[fld]} vs box {b[fld]}")
    check(f"cross: stat and box agree on mean/quartiles at Room (all {compared})",
          compared > 0 and not mism, "; ".join(mism[:3]))


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("qa_stats_recompute -- independent statistics recompute (gap #2)")
    df = _build_main_df()
    section_stat(df)
    section_box(df)
    section_averaging()
    section_np_ti()
    section_outlier()
    section_denv(df)
    section_cross(df)

    print(f"\n{'=' * 60}")
    print(f"  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}")
    if _FAIL:
        print("\nFailed checks:")
        for f in _FAIL:
            print(f"  - {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
