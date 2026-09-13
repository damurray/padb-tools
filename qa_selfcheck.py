r"""qa_selfcheck.py -- one-command self-assessment gate.

Run this after every significant tool change. It runs the fast, deterministic,
browser-free QA suites, compares each suite's verified-correctness counts to a
saved baseline (qa_baseline.json), and prints a scorecard + a single verdict:

  GREEN  -- nothing regressed vs baseline and coverage did not shrink.
  RED    -- a regression: a suite's FAIL count rose or its PASS count dropped
            (a behaviour that was verified-correct at baseline no longer is).
            Exit 1. This is the "further testing warranted" signal.
  AMBER  -- a suite could not run (e.g. dead headless browser), so a regression
            *could* be hidden -- "unverified", not a pass. Exit 3.

Honesty notes (deliberate, do not "improve" into false precision):
  * There is NO single fabricated "correctness %". The only numbers reported are
    real, reproducible check counts. "Correctness" here means *verified*
    correctness -- the behaviours the suites actually encode. GREEN means "no
    known check regressed", NOT "the tool is provably correct"; it is only as
    strong as the checks that exist (see the coverage gaps in CLAUDE.md).
  * qa_padb's baseline includes its 4 known/expected FAILs -- those are the
    baseline, so they are not counted as regressions; only a change from it is.

Usage:
  py qa_selfcheck.py                 # run + compare to baseline + verdict
  py qa_selfcheck.py --update-baseline   # snapshot current counts as the new
                                         # baseline (do this ONLY after a
                                         # deliberate, reviewed coverage change)
  py qa_selfcheck.py --json          # machine-readable scorecard on stdout

The browser-dependent interactive/render tiers (qa_filters, qa_view_sweep) are
NOT part of this deterministic core -- run those against real pages when a view/
JS change needs behavioural verification; they self-report ENV-UNAVAILABLE
(exit 3) when the headless browser is down.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
BASELINE_PATH = HERE / "qa_baseline.json"

# Core modules whose mere import/compile must never break.
_COMPILE_TARGETS = [
    "padb_plots.py", "padb_v2.py", "padb_run.py", "padb_config.py",
    "padb_viewer.py", "build_viewer.py", "qa_padb.py", "qa_viewer.py",
    "qa_js_segments.py", "qa_filters.py", "webapp/padb_web.py",
]


def _run(argv: list[str], timeout: int = 300) -> tuple[int, str]:
    try:
        p = subprocess.run([PY, *argv], cwd=str(HERE), capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        return 124, f"[timeout after {timeout}s] {e}"
    except Exception as e:   # never let one suite abort the gate
        return 125, f"[runner error] {e}"


def _pp(rx: str, out: str) -> tuple[int, int] | None:
    m = re.search(rx, out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _suite_compile() -> dict:
    present = [t for t in _COMPILE_TARGETS if (HERE / t).exists()]
    rc, out = _run(["-m", "py_compile", *present])
    ok = rc == 0
    return {"name": "compile", "passed": len(present) if ok else 0,
            "failed": 0 if ok else len(present), "env": False,
            "detail": f"{len(present)} modules" if ok else out.strip().splitlines()[-1:] or ["compile error"]}


def _suite(name: str, argv: list[str], rx: str, timeout: int = 300) -> dict:
    rc, out = _run(argv, timeout=timeout)
    pf = _pp(rx, out)
    env = ("ENV-UNAVAILABLE" in out) or ("UNVERIFIED" in out) or ("[ENV" in out)
    if pf is None:
        # No summary line parsed -- either it crashed or reported env-only.
        return {"name": name, "passed": 0, "failed": 0 if env else 1,
                "env": env, "detail": (out.strip().splitlines()[-1:] or ["no summary line"])[0]}
    return {"name": name, "passed": pf[0], "failed": pf[1], "env": env,
            "detail": f"{pf[0]} pass / {pf[1]} fail" + (" (some UNVERIFIED)" if env else "")}


def run_suites() -> list[dict]:
    return [
        _suite_compile(),
        _suite("qa_padb", ["qa_padb.py"], r"PASS:\s*(\d+)\s+FAIL:\s*(\d+)"),
        _suite("qa_viewer", ["qa_viewer.py"], r"PASS=(\d+)\s+FAIL=(\d+)"),
        _suite("qa_js_segments", ["qa_js_segments.py"], r"PASS:\s*(\d+)\s+FAIL:\s*(\d+)"),
    ]


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Deterministic self-assessment gate with baseline delta.")
    ap.add_argument("--update-baseline", action="store_true",
                    help="Snapshot current counts as the new baseline (after a reviewed coverage change).")
    ap.add_argument("--json", action="store_true", help="Emit the scorecard as JSON.")
    args = ap.parse_args(argv)

    results = run_suites()
    baseline = {}
    if BASELINE_PATH.exists():
        try:
            baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8")).get("suites", {})
        except (OSError, json.JSONDecodeError):
            baseline = {}

    if args.update_baseline:
        snap = {"suites": {r["name"]: {"passed": r["passed"], "failed": r["failed"]} for r in results}}
        BASELINE_PATH.write_text(json.dumps(snap, indent=2), encoding="utf-8")
        print(f"Baseline written to {BASELINE_PATH.name}:")
        for r in results:
            print(f"  {r['name']:<16} {r['passed']} pass / {r['failed']} fail")
        sys.exit(0)

    # Verdict logic
    regressions, amber = [], []
    rows = []
    for r in results:
        b = baseline.get(r["name"])
        delta = ""
        status = "ok"
        if b is not None:
            dp, df = r["passed"] - b["passed"], r["failed"] - b["failed"]
            delta = f"pass {dp:+d}, fail {df:+d}"
            # Regression: verified-correct behaviour lost (more fails, or fewer passes
            # not explained by env). An env-skip that merely couldn't run isn't a
            # regression -- it's amber.
            if df > 0 or (dp < 0 and not r["env"]):
                status = "REGRESSED"; regressions.append(r["name"])
            elif dp > 0:
                status = "coverage+"    # more checks pass than baseline (good; consider --update-baseline)
        else:
            delta = "(no baseline)"
        # AMBER only when a suite couldn't produce ANY verified result (env, 0 passed).
        # A suite whose browser-free core passed but whose optional browser re-check
        # was env-skipped (passed>0 + env) is GREEN with a footnote, not amber --
        # otherwise the gate is stuck amber on every browser-flaky day for no real
        # loss of guarantee.
        env_blocking = r["env"] and r["passed"] == 0
        if env_blocking:
            if status == "ok":
                status = "UNVERIFIED"
            amber.append(r["name"])
        elif r["env"] and status == "ok":
            status = "ok*"   # core verified; optional browser re-check skipped
        rows.append({**r, "delta": delta, "status": status,
                     "baseline": (f"{b['passed']}/{b['failed']}" if b else "-")})

    if args.json:
        print(json.dumps({"suites": rows,
                          "verdict": ("RED" if regressions else "AMBER" if amber else "GREEN")}, indent=2))
    else:
        print("\n  QA self-check scorecard (verified-correctness vs baseline)")
        print("  " + "-" * 72)
        print(f"  {'suite':<16}{'baseline':>10}{'current':>12}   {'delta':<20}status")
        print("  " + "-" * 72)
        for r in rows:
            cur = f"{r['passed']}/{r['failed']}"
            print(f"  {r['name']:<16}{r['baseline']:>10}{cur:>12}   {r['delta']:<20}{r['status']}")
        print("  " + "-" * 72)

    verdict = "RED" if regressions else ("AMBER" if amber else "GREEN")
    mark = {"GREEN": "GREEN [OK]", "AMBER": "AMBER [unverified]", "RED": "RED [regression]"}[verdict]
    print(f"\n  VERDICT: {mark}")
    if regressions:
        print(f"    Regressed vs baseline: {', '.join(regressions)} -- further testing warranted.")
    if amber and not regressions:
        print(f"    Could not fully verify: {', '.join(amber)} -- treat as unverified, not passed.")
    if any(r["status"] == "ok*" for r in rows):
        print("    (ok* = suite's browser-free core passed; an optional browser re-check "
              "was skipped -- environment, not a gap in the primary guarantee.)")
    if verdict == "GREEN":
        print("    No known check regressed. (GREEN = nothing verified-correct broke, "
              "NOT a proof of overall correctness -- only as strong as the checks that exist.)")
    print("    Interactive/render tiers (qa_filters/qa_view_sweep) are separate -- "
          "run them on affected pages for behavioural changes.")
    sys.exit(1 if regressions else (3 if amber else 0))


if __name__ == "__main__":
    main()
