#!/usr/bin/env python3
"""qa_sentinel.py -- teeth for padb_sentinel (run-to-run audit-diff, 2026-09-16).

Builds prepped run-indexed frames with KNOWN run-to-run patterns and asserts the
classifier: fail->pass = confirmed sentinel; pass->fail = regression; still-failing
= unresolved; quiet = redundancy candidate; a run failing ~every point = test issue
(excluded); primary-clean + other-site-fail = cross-site re-expansion. Negative
controls prove each rule is load-bearing and that the analysis is PER-DUT (not
pooled) and PER-SITE.
"""
import sys

import pandas as pd

import padb_sentinel as ps

_PASS, _FAIL = [], []


def check(desc, cond, detail=""):
    (_PASS if cond else _FAIL).append(desc)
    print(f"  {'PASS' if cond else 'FAIL'}  {desc}" + ("" if cond or not detail else f" -- {detail}"))


def _prepped(rows):
    """rows: (site, serial, run, freq, fail_bool). Build the frame analyze() wants
    (limits are implicit -- fail encoded directly, hi/lo present so scored=True)."""
    recs = []
    for site, serial, run, freq, fail in rows:
        v = -40.0 if fail else -70.0
        recs.append({"freq": float(freq), "value": v, "hi": -50.0, "lo": -110.0,
                     "serial": serial, "site": site, "run": float(run),
                     "run_label": f"{site}-{serial}-r{run}",
                     "scored": True, "rowfail": bool(fail)})
    return pd.DataFrame(recs)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("qa_sentinel -- teeth for the run-to-run audit-diff detector")

    # 1) confirmed sentinel: US001 fails f20 @run1, passes @run2/3.
    #    redundancy: f10 quiet everywhere. unresolved: f50 fails @last run.
    base = []
    for run in (1, 2, 3):
        base.append(("SR", "US001", run, 10, False))            # quiet
        base.append(("SR", "US001", run, 20, run == 1))         # fail@1 -> pass -> confirmed
        base.append(("SR", "US001", run, 50, run == 3))         # fail@last -> unresolved
        base.append(("SR", "US002", run, 10, False))
        base.append(("SR", "US002", run, 20, False))
        base.append(("SR", "US002", run, 50, False))
    r = ps.analyze(_prepped(base), "SR", 0.8)
    check("confirmed sentinel = fail then pass on the same unit", 20 in r["confirmed"])
    check("unresolved = still failing on the latest run", 50 in r["unresolved"])
    check("redundancy candidate = quiet on every run of every unit",
          10 in r["redundancy"] and 20 not in r["redundancy"] and 50 not in r["redundancy"])
    check("a still-failing point is NOT a redundancy candidate", 50 not in r["redundancy"])

    # 2) PER-DUT teeth: A fail->pass, B pass->fail at the SAME freq. Per-unit =>
    #    confirmed AND regression. If pooled by site, both vanish (every run has a
    #    fail from one of them) -> proves the analysis is per-DUT. (Each unit also
    #    has quiet freqs 31-34 so a single fail isn't a whole-run/test-issue.)
    perdut = []
    for run in (1, 2):
        for f in (31, 32, 33, 34):
            perdut.append(("SR", "A", run, f, False))
            perdut.append(("SR", "B", run, f, False))
        perdut.append(("SR", "A", run, 30, run == 1))   # fail@1 -> pass@2
        perdut.append(("SR", "B", run, 30, run == 2))   # pass@1 -> fail@2
    r2 = ps.analyze(_prepped(perdut), None, 0.8)
    check("TEETH: per-DUT -- A's fail->pass is confirmed even as B regresses same freq",
          30 in r2["confirmed"] and 30 in r2["regression"])

    # 3) TEST-ISSUE run excluded: US101 run2 fails ALL freqs -> test issue; its
    #    fails must not create a spurious sentinel/redundancy verdict.
    ti = []
    for run in (1, 2, 3):
        for f in (10, 20, 30):
            ti.append(("MY", "US101", run, f, run == 2))  # run2 fails everything
    r3 = ps.analyze(_prepped(ti), None, 0.8)
    tirun = [(t["site"], t["serial"], int(t["run"])) for t in r3["test_issue_runs"]]
    check("test-issue run (fails ~every point) is detected", ("MY", "US101", 2) in tirun)
    check("TEETH: test-issue run's fails don't fake a confirmed sentinel",
          not r3["confirmed"])
    check("TEETH: with the test-issue run excluded, its freqs read as redundancy",
          set(r3["redundancy"]) == {10, 20, 30})

    # 4) CROSS-SITE re-expansion: f40 clean at SR (primary), fails at MY. Each unit
    #    also has quiet freqs 41-44 so MY's single fail isn't a whole-run test issue.
    cs = []
    for run in (1, 2):
        for f in (41, 42, 43, 44):
            cs.append(("SR", "S1", run, f, False))
            cs.append(("MY", "M1", run, f, False))
        cs.append(("SR", "S1", run, 40, False))   # SR clean
        cs.append(("MY", "M1", run, 40, True))     # MY fails
    r4 = ps.analyze(_prepped(cs), "SR", 0.8)
    check("cross-site re-expansion: clean at primary, fails at another site",
          40 in r4["reexpansion"])
    # TEETH: if SR ALSO fails f40, it's not a clean cross-site trigger.
    cs2 = cs + [("SR", "S1", 1, 40, True)]
    r5 = ps.analyze(_prepped(cs2), "SR", 0.8)
    check("TEETH: not a cross-site trigger when the primary site fails too",
          40 not in r5["reexpansion"])

    print(f"\n{'=' * 60}\n  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
