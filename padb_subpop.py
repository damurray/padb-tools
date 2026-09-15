#!/usr/bin/env python3
r"""padb_subpop.py -- subpopulation / dual-distribution detector.

Answers the engineer's question "are a handful of DUTs behaving as a SEPARATE
distribution from the rest of the population?" -- the signal that a per-unit
calibration/correction failed to make those units look identical, i.e. a real
DUT / test-station / test-condition problem rather than ordinary M.U. + drift.

DESIGN (worked out with the user, deliberately advisory + non-fragile):

* Assess modality only WITHIN a condition-matched slice (one condition at a time;
  a "condition" already pins temperature/site/etc.). Buckets are the frequencies
  within that condition. Never pool across mismatched conditions -- legitimate
  temp-driven spread must not masquerade as a defect.
* Per bucket, detect a separated minority cluster with a ROBUST median/MAD gap
  test, NOT a model fit (dip test / GMM are fragile at small n). Two independent
  criteria must both hold:
    1. magnitude  -- the split gap exceeds a THRESHOLD, and
    2. shape      -- the gap is DOMINANT (clearly the largest, not the tail of a
                     continuous spread).
* THRESHOLD is BUDGET-ANCHORED when the caller supplies the per-bucket physical
  budget (M.U. + env-drift): a healthy corrected population should sit within that
  budget, so a mode separated by MORE than it is anomalous. Without a budget it
  falls back to a relative gap_k*MAD threshold, and the result is flagged
  shape-only (with a "re-extract with Spec+Uncertainty" caveat).
* Require AGREEMENT before flagging anything: >= min_minority DUTs in the minority
  mode AND the same serials recurring across >= recurrence_frac of the assessable
  buckets. A one-off noisy bucket never flags.
* NEVER decides "bad DUT vs station vs test" on its own -- it reports the flagged
  serials, the numbers, and any shared metadata (station/port) so the engineer
  adjudicates. It also NEVER removes data (advisory only).

This module is the reference implementation + test oracle for the client-side JS
port (Workflow & Recommendations advisory) and can also feed the server-side PDF
report's distribution-health section. Pure Python + stdlib only.
"""
from __future__ import annotations

import math
from typing import Optional


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return float("nan")
    m = n // 2
    return s[m] if n % 2 else 0.5 * (s[m - 1] + s[m])


def _mad(xs: list[float], med: float) -> float:
    """Median absolute deviation, scaled to a robust sigma estimate (1.4826)."""
    if not xs:
        return 0.0
    return 1.4826 * _median([abs(x - med) for x in xs])


def _bucket_split(vals: list[tuple[int, float]], threshold: float,
                  min_minority: int, dom_ratio: float):
    """Given [(dut_idx, value), ...] for one bucket, return the minority cluster
    as (minority_idxs, direction, gap) if a dominant, above-threshold gap splits
    the sorted values into a genuine minority (>= min_minority, strictly smaller
    than the majority); else None.

    direction: +1 if the minority sits ABOVE the majority median, -1 if below."""
    n = len(vals)
    if n < 2 * min_minority:            # need a real minority AND majority
        return None
    sv = sorted(vals, key=lambda t: t[1])
    ys = [v for _, v in sv]
    gaps = [ys[i + 1] - ys[i] for i in range(n - 1)]
    gmax = max(gaps)
    if gmax <= 0:
        return None
    gi = gaps.index(gmax)               # split BETWEEN index gi and gi+1
    low_n = gi + 1
    high_n = n - low_n
    # minority = the smaller side; must be a genuine minority, not half the set
    if low_n <= high_n:
        minority = sv[:low_n]
        majority = sv[low_n:]
        direction = -1
    else:
        minority = sv[low_n:]
        majority = sv[:low_n]
        direction = +1
    if len(minority) < min_minority or len(minority) >= len(majority):
        return None
    if gmax < threshold:               # magnitude criterion
        return None
    # shape criterion: the split gap must dominate the other gaps (a real gap,
    # not the largest step of a smoothly-spread continuum).
    others = [g for j, g in enumerate(gaps) if j != gi]
    ref = _median(others) if others else 0.0
    if ref > 0 and gmax < dom_ratio * ref:
        return None
    return ([idx for idx, _ in minority], direction, gmax)


def detect_subpopulations(
    values_by_freq: list[list[Optional[float]]],
    serials: list[str],
    budget_by_freq: Optional[list[Optional[float]]] = None,
    station_by_dut: Optional[list[Optional[str]]] = None,
    *,
    min_minority: int = 2,
    min_bucket_n: int = 4,
    min_buckets: int = 3,
    recurrence_frac: float = 0.5,
    gap_k: float = 3.0,
    dom_ratio: float = 2.0,
) -> dict:
    """Detect a recurring minority subpopulation within ONE condition-matched slice.

    values_by_freq[fi][di] = DUT di's aggregate at frequency-bucket fi (None/NaN
        if that DUT has no value there). serials[di] names DUT di.
    budget_by_freq[fi]     = optional per-bucket physical budget (M.U. + env-drift). When
        given, a mode must be separated by MORE than this to flag (budget-anchored);
        when None, a relative gap_k*MAD threshold is used and result.shape_only=True.
    station_by_dut[di]     = optional metadata (station/port/run) for correlation.

    Returns a dict:
      flagged:        [serial, ...] of the subpopulation (empty if none / inconclusive)
      status:         "flagged" | "clean" | "inconclusive"
      n_buckets:      assessable buckets (>= min_bucket_n DUTs)
      n_flagged_buckets: buckets that registered a split
      direction:      +1 (above population) / -1 (below) / 0 (mixed)
      median_offset:  median signed offset of the flagged set from the majority,
                      averaged over flagged buckets (in data units)
      gap_over_budget: mean gap / mean threshold across flagged buckets (>1 = beyond budget)
      correlation:    {"field": value} when all flagged DUTs share one station value, else None
      shape_only:     True when no budget was supplied (heuristic; weaker claim)
      caveats:        [str, ...] plain-language caveats
      message:        one-line advisory string (or "" when clean/inconclusive)
    """
    nfreq = len(values_by_freq)
    ndut = len(serials)
    caveats: list[str] = []
    if budget_by_freq is None:
        caveats.append("No M.U./env-drift budget in this data -- this is a distribution-SHAPE "
                       "heuristic only; re-extract with Upper/Lower Spec + Uncertainty for a "
                       "budget-anchored verdict.")
    caveats.append("Valid only when the measurement conditions match the conditions the "
                   "DUT corrections were calibrated for; off-condition separation can be legitimate.")

    minority_count = [0] * ndut          # buckets where DUT di was in a flagged minority
    dirs: list[int] = []
    offsets: list[float] = []
    gaps: list[float] = []
    thrs: list[float] = []
    n_assess = 0
    n_flagged_buckets = 0

    for fi in range(nfreq):
        row = values_by_freq[fi] if fi < nfreq else []
        vals = [(di, float(row[di])) for di in range(min(ndut, len(row)))
                if _finite(row[di])]
        if len(vals) < min_bucket_n:
            continue
        n_assess += 1
        ys = [v for _, v in vals]
        med = _median(ys)
        mad = _mad(ys, med)
        if budget_by_freq is not None and fi < len(budget_by_freq) and _finite(budget_by_freq[fi]):
            threshold = float(budget_by_freq[fi])
        else:
            threshold = gap_k * mad
            if threshold <= 0:           # perfectly-clustered majority: any real gap counts
                threshold = 0.0
        split = _bucket_split(vals, threshold, min_minority, dom_ratio)
        if split is None:
            continue
        min_idxs, direction, gap = split
        n_flagged_buckets += 1
        for di in min_idxs:
            minority_count[di] += 1
        maj_med = _median([v for di, v in vals if di not in set(min_idxs)])
        min_med = _median([v for di, v in vals if di in set(min_idxs)])
        offsets.append(min_med - maj_med)
        dirs.append(direction)
        gaps.append(gap)
        thrs.append(threshold if threshold > 0 else gap)

    if n_assess < min_buckets:
        return {"flagged": [], "status": "inconclusive", "n_buckets": n_assess,
                "n_flagged_buckets": n_flagged_buckets, "direction": 0,
                "median_offset": 0.0, "gap_over_budget": 0.0, "correlation": None,
                "shape_only": budget_by_freq is None,
                "caveats": caveats + ["Too few assessable frequency buckets (need >= "
                                      f"{min_buckets}, have {n_assess}) to judge distribution shape."],
                "message": ""}

    # A DUT is a subpopulation member if it stood apart in a meaningful fraction of
    # the ASSESSABLE buckets -- i.e. of the frequencies where it COULD have been
    # observed, not merely of the buckets that happened to flag. Using n_assess (not
    # n_flagged_buckets) is what makes a one-off separation in a single bucket NOT a
    # recurring subpopulation (1/10 assessable < 50%), while a genuine mode present
    # across most frequencies clears the bar.
    denom = n_assess
    flagged_idx = [di for di in range(ndut)
                   if denom > 0 and minority_count[di] >= recurrence_frac * denom
                   and minority_count[di] > 0]
    if not flagged_idx:
        return {"flagged": [], "status": "clean", "n_buckets": n_assess,
                "n_flagged_buckets": n_flagged_buckets, "direction": 0,
                "median_offset": 0.0, "gap_over_budget": 0.0, "correlation": None,
                "shape_only": budget_by_freq is None, "caveats": caveats, "message": ""}

    flagged = [serials[di] for di in flagged_idx]
    net_dir = 0
    if dirs:
        s = sum(dirs)
        net_dir = 1 if s > 0 else (-1 if s < 0 else 0)
    median_offset = _median(offsets) if offsets else 0.0
    gob = (sum(gaps) / len(gaps)) / (sum(thrs) / len(thrs)) if thrs and sum(thrs) > 0 else 0.0

    correlation = None
    if station_by_dut is not None:
        vals = {station_by_dut[di] for di in flagged_idx
                if di < len(station_by_dut) and station_by_dut[di] not in (None, "")}
        if len(vals) == 1:
            only = next(iter(vals))
            # only meaningful if the majority does NOT all share it too
            maj = {station_by_dut[di] for di in range(ndut)
                   if di not in set(flagged_idx) and di < len(station_by_dut)}
            if only not in maj:
                correlation = {"field": only}

    frac_pct = round(100.0 * n_flagged_buckets / max(n_assess, 1))
    dir_word = "above" if net_dir > 0 else ("below" if net_dir < 0 else "off")
    # Drop the "across N of M frequencies" clause for a single-bucket population
    # (e.g. the histogram, which has no frequency axis) -- it would read "1 of 1".
    across = (f" across {n_flagged_buckets} of {n_assess} frequencies ({frac_pct}%)"
              if n_assess > 1 else "")
    msg = (f"{len(flagged)} DUT(s) form a separate distribution ({dir_word} the population)"
           f"{across}, median offset {median_offset:+.4g}")
    if budget_by_freq is not None:
        msg += f", ~{gob:.1f}x the M.U./env-drift budget"
    if correlation:
        msg += f"; all share {correlation['field']}"
    msg += ". Action required -- investigate DUT/station/test. Not auto-filtered."

    return {"flagged": flagged, "status": "flagged", "n_buckets": n_assess,
            "n_flagged_buckets": n_flagged_buckets, "direction": net_dir,
            "median_offset": median_offset, "gap_over_budget": gob,
            "correlation": correlation, "shape_only": budget_by_freq is None,
            "caveats": caveats, "message": msg}
