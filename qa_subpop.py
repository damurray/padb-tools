#!/usr/bin/env python3
r"""qa_subpop.py -- deterministic tests for the subpopulation detector.

Proves the STATISTICS before any view UI is wired (the "detector + QA only first"
increment). Browser-free and fully deterministic (hand-built synthetic slices, no
randomness). Has teeth two ways: (1) it must FLAG a planted subpopulation, and
(2) it must NOT flag the false-positive shapes (unimodal, continuous spread,
one-off, sub-budget, small-n) -- verified by also showing a permissive/broken
parameterization DOES flag them, so the guards aren't vacuous.

Usage:  python qa_subpop.py
Exit codes: 0 = all pass, 1 = any failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import padb_subpop as sp   # noqa: E402

_PASS: list[str] = []
_FAIL: list[str] = []


def check(desc: str, cond: bool, detail: str = "") -> None:
    (_PASS if cond else _FAIL).append(desc)
    print((f"  PASS  {desc}" if cond else f"  FAIL  {desc}" + (f" -- {detail}" if detail else "")))


# --- synthetic slice builders -------------------------------------------------
def _serials(n):
    return [f"D{i:02d}" for i in range(n)]


def unimodal(nfreq=10, ndut=12, jitter=0.01):
    """Tight single population: base drifts per freq, tiny deterministic jitter."""
    rows = []
    for fi in range(nfreq):
        base = -100.0 + fi * 2.0
        rows.append([base + ((di % 3) - 1) * jitter for di in range(ndut)])
    return rows, _serials(ndut)


def bimodal(nfreq=10, ndut=12, offset=10.0, minority=(0, 1, 2), jitter=0.01):
    """Majority tight; the `minority` DUTs sit `offset` above, every bucket."""
    rows = []
    mset = set(minority)
    for fi in range(nfreq):
        base = -100.0 + fi * 2.0
        row = []
        for di in range(ndut):
            v = base + ((di % 3) - 1) * jitter
            if di in mset:
                v += offset
            row.append(v)
        rows.append(row)
    return rows, _serials(ndut)


def continuous(nfreq=10, ndut=12, span=6.0):
    """Evenly-spread values (no gap) -- a wide but single distribution."""
    rows = []
    for fi in range(nfreq):
        base = -100.0 + fi * 2.0
        rows.append([base + (di / (ndut - 1)) * span for di in range(ndut)])
    return rows, _serials(ndut)


def main() -> int:
    # 1) unimodal -> clean
    v, s = unimodal()
    r = sp.detect_subpopulations(v, s)
    check("unimodal tight population is CLEAN (no false subpopulation)",
          r["status"] == "clean" and not r["flagged"], f"{r['status']} {r['flagged']}")

    # 2) bimodal -> flags EXACTLY the planted minority
    v, s = bimodal(minority=(0, 1, 2))
    r = sp.detect_subpopulations(v, s)
    check("bimodal population is FLAGGED (detector has teeth)",
          r["status"] == "flagged", f"{r['status']}")
    check("bimodal flags EXACTLY the planted 3 serials",
          set(r["flagged"]) == {"D00", "D01", "D02"}, f"flagged={r['flagged']}")
    check("bimodal reports direction ABOVE the population (+1) and a positive offset",
          r["direction"] == 1 and r["median_offset"] > 5.0, f"dir={r['direction']} off={r['median_offset']}")
    check("bimodal recurs across most buckets", r["n_flagged_buckets"] >= 8, str(r["n_flagged_buckets"]))

    # teeth for the CLEAN guards: a permissive parameterization DOES flag the
    # false-positive shapes, so the strict defaults rejecting them isn't vacuous.
    v, s = continuous()
    r_strict = sp.detect_subpopulations(v, s)
    r_loose = sp.detect_subpopulations(v, s, dom_ratio=1.0, gap_k=0.0, recurrence_frac=0.0, min_minority=1)
    check("continuous spread is CLEAN under strict defaults (no gap)",
          r_strict["status"] == "clean", f"{r_strict['status']} {r_strict['flagged']}")
    check("continuous spread WOULD flag under permissive params (guard is not vacuous)",
          r_loose["status"] == "flagged", f"{r_loose['status']}")

    # 3) budget anchoring: same 0.5 dB separation, flagged only when it EXCEEDS budget
    v, s = bimodal(offset=0.5, minority=(0, 1, 2), jitter=0.005)
    big_budget = [1.0] * 10        # 0.5 < 1.0 -> within budget -> not a defect
    small_budget = [0.2] * 10      # 0.5 > 0.2 -> beyond budget -> defect
    r_big = sp.detect_subpopulations(v, s, budget_by_freq=big_budget)
    r_small = sp.detect_subpopulations(v, s, budget_by_freq=small_budget)
    check("budget-anchored: separation WITHIN the M.U./env-drift budget is CLEAN",
          r_big["status"] == "clean", f"{r_big['status']} {r_big['flagged']}")
    check("budget-anchored: separation BEYOND the budget is FLAGGED",
          r_small["status"] == "flagged" and set(r_small["flagged"]) == {"D00", "D01", "D02"},
          f"{r_small['status']} {r_small['flagged']}")
    check("budget-anchored result is NOT shape_only; no-budget result IS shape_only",
          r_small["shape_only"] is False and sp.detect_subpopulations(v, s)["shape_only"] is True)

    # 4) one-off (subpop only in 1 of 10 buckets) -> clean (recurrence gate)
    v, s = unimodal()
    v[0] = [x + 10.0 if di in (0, 1, 2) else x for di, x in enumerate(v[0])]  # spike only bucket 0
    r = sp.detect_subpopulations(v, s)
    check("one-off separation in a single bucket is CLEAN (recurrence gate)",
          r["status"] == "clean", f"{r['status']} {r['flagged']} flaggedBuckets={r['n_flagged_buckets']}")

    # 5) small-n per bucket -> inconclusive
    v, s = bimodal(ndut=3, minority=(0,))
    r = sp.detect_subpopulations(v, s)
    check("too few DUTs per bucket -> INCONCLUSIVE (no flag)",
          r["status"] == "inconclusive" and not r["flagged"], f"{r['status']}")

    # 6) too few buckets -> inconclusive
    v, s = bimodal(nfreq=2)
    r = sp.detect_subpopulations(v, s)
    check("too few frequency buckets -> INCONCLUSIVE",
          r["status"] == "inconclusive", f"{r['status']} nb={r['n_buckets']}")

    # 7) cross-field correlation: flagged DUTs all share a station the majority lacks
    v, s = bimodal(minority=(0, 1, 2))
    station = ["B", "B", "B"] + ["A"] * 9
    r = sp.detect_subpopulations(v, s, station_by_dut=station)
    check("correlation: flagged serials sharing one station is reported",
          r["correlation"] == {"field": "B"}, f"corr={r['correlation']}")
    # and NOT reported when the majority shares it too (not discriminating)
    station2 = ["A"] * 12
    r2 = sp.detect_subpopulations(v, s, station_by_dut=station2)
    check("correlation: a station shared by everyone is NOT reported as discriminating",
          r2["correlation"] is None, f"corr={r2['correlation']}")

    # 8) caveats always present; no-budget adds the shape-only caveat
    v, s = bimodal()
    r = sp.detect_subpopulations(v, s)
    check("caveats include the calibration-condition caveat",
          any("calibrat" in c.lower() for c in r["caveats"]))
    check("no-budget run adds the shape-only / re-extract caveat",
          any("shape" in c.lower() and "re-extract" in c.lower() for c in r["caveats"]))
    check("flagged message is a plain advisory ending in 'Not auto-filtered.'",
          r["message"].endswith("Not auto-filtered.") and "Action required" in r["message"], r["message"])

    print("\n" + "=" * 60)
    print(f"  PASS: {len(_PASS)}   FAIL: {len(_FAIL)}")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
