#!/usr/bin/env python3
"""qa_csv_sweep.py -- run padb_csv_check.py across every extracted CSV under one
or more roots and print a compact pass/warn/fail table.

QA plan "Gate 2": a free pre-flight over the whole dataset library -- no
rendering, no PADB. Catches load failures, wrong x_col auto-detection, missing
serial/temperature/spec, and high-cardinality conditions across every CSV at
once, so you know which datasets are safe to rebuild before spending time on
plots.

Usage:
  py qa_csv_sweep.py                       # scan the standard data roots
  py qa_csv_sweep.py --root C:\\temp\\data   # one or more roots (repeatable)
  py qa_csv_sweep.py --fails-only          # only list CSVs that FAIL or error

Exit code 1 if any CSV FAILs (or errors). A WARN alone does NOT fail the sweep:
most real CSVs carry benign WARNs (placeholder-row drop, crowded legend, no
Spec/Uncertainty grouping items), so failing on those would cry wolf.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

# Default places extracted CSVs live on this workstation (see CLAUDE.md).
_DEFAULT_ROOTS = [
    Path(r"C:\temp\data"),
    Path.home() / "OneDrive - Keysight Technologies" / "Documents" / "Padb" / "Data",
]
# Skip archived / backup copies -- they're stale duplicates of live CSVs.
_EXCLUDE_DIR_PARTS = {"backup", "Job_Archive"}
# Skip tool-generated intermediate/helper CSVs -- these aren't source scatter CSVs
# and would false-FAIL the loader: `_compare_merged.csv` (compare merge intermediate),
# `_v2_tmp_*.csv` (transient per-view temp), `global_filter_*.csv` (a GF export).
# The leading-underscore convention covers the first two; GF exports are named
# explicitly. (Found by the first full sweep, which flagged 7 such files.)
def _is_internal_csv(name: str) -> bool:
    return name.startswith("_") or name.startswith("global_filter")
_SUMMARY_RE = re.compile(r"OK:\s*(\d+)\s+WARN:\s*(\d+)\s+FAIL:\s*(\d+)")
_PER_CSV_TIMEOUT_S = 600  # a 600 MB CSV takes a minute+ to load; give headroom


def _iter_csvs(roots: list[Path]):
    """Yield every *.csv under the roots, de-duplicated by resolved path,
    excluding backup/archive directories."""
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.csv")):
            if _EXCLUDE_DIR_PARTS & set(p.parts):
                continue
            if _is_internal_csv(p.name):
                continue
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            yield p


def _build_xcol_map(roots: list[Path]) -> dict:
    """Map each resolved csv_path -> x_col from every V2 plot job that sets one.
    A non-frequency-x-axis analytic (e.g. an Analog-Mod Flatness sweep over
    'Rate (kHz)') only loads when its job.json's x_col is used; without it the
    loader auto-detects nothing and reports 0 rows -- a false FAIL if the sweep
    tests the CSV blind to its job. Reading x_col here makes the sweep match how
    the analytic is actually built."""
    import json
    mp: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for jp in root.rglob("*_v2_job.json"):
            if _EXCLUDE_DIR_PARTS & set(jp.parts):
                continue
            try:
                cfg = json.loads(jp.read_text(encoding="utf-8"))
            except Exception:
                continue
            xcol = cfg.get("x_col")
            cpath = cfg.get("csv_path")
            if xcol and cpath:
                try:
                    mp[str(Path(cpath).resolve())] = xcol
                except Exception:
                    pass
    return mp


def _check_one(csv_path: Path, x_col: str | None = None) -> tuple:
    """Run padb_csv_check.py on one CSV (passing its job's x_col when known).
    Returns (ok, warn, fail, fail_lines, err) -- counts are None when the check
    crashed before printing its summary line."""
    script = Path(__file__).with_name("padb_csv_check.py")
    cmd = [sys.executable, str(script), str(csv_path)]
    if x_col:
        cmd += ["--x-col", x_col]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_PER_CSV_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return (None, None, None, [], f"timeout (>{_PER_CSV_TIMEOUT_S}s)")
    except Exception as exc:  # pragma: no cover -- defensive
        return (None, None, None, [], f"could not run check: {exc}")
    out = (proc.stdout or "") + (proc.stderr or "")
    fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAIL")]
    m = _SUMMARY_RE.search(out)
    if not m:
        tail = "; ".join(l.strip() for l in out.strip().splitlines()[-2:])
        return (None, None, None, fails, tail or "no summary line produced")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), fails, None)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, action="append", default=None,
                    help="Root dir to scan (repeatable). Defaults to the standard data roots.")
    ap.add_argument("--fails-only", action="store_true",
                    help="Only print CSVs that FAIL or error; skip clean/WARN rows.")
    ap.add_argument("--no-xcol", action="store_true",
                    help="Do NOT apply each CSV's job x_col (test raw auto-detection only). "
                         "By default the sweep passes a matching job's x_col so a "
                         "non-frequency-x-axis analytic isn't false-FAILed.")
    args = ap.parse_args()

    roots = args.root or _DEFAULT_ROOTS
    csvs = list(_iter_csvs(roots))
    if not csvs:
        print("No CSVs found under: " + ", ".join(str(r) for r in roots))
        sys.exit(0)

    xcol_map = {} if args.no_xcol else _build_xcol_map(roots)

    print(f"Sweeping {len(csvs)} CSV(s) with padb_csv_check.py "
          f"(roots: {', '.join(str(r) for r in roots)}"
          f"{'' if args.no_xcol else f'; {len(xcol_map)} job x_col override(s) available'})\n",
          flush=True)

    n_fail = n_err = n_warn = n_clean = 0
    rows = []
    for i, p in enumerate(csvs, 1):
        xc = xcol_map.get(str(p.resolve()))
        print(f"  [{i}/{len(csvs)}] {p.name}{' [x_col='+xc+']' if xc else ''} ...", flush=True)
        ok, warn, fail, fails, err = _check_one(p, xc)
        if err is not None:
            status = "ERROR"; n_err += 1
        elif fail:
            status = "FAIL"; n_fail += 1
        elif warn:
            status = "warn"; n_warn += 1
        else:
            status = "OK"; n_clean += 1
        rows.append((status, p, ok, warn, fail, fails, err))

    print("\n" + "=" * 72)
    print("  RESULTS")
    print("=" * 72)
    for status, p, ok, warn, fail, fails, err in rows:
        if args.fails_only and status not in ("FAIL", "ERROR"):
            continue
        counts = f"OK={ok} WARN={warn} FAIL={fail}" if err is None else "(no summary)"
        print(f"[{status:5s}] {counts:22s} {p}")
        if err is not None:
            print(f"          -> {err}")
        for f in fails:
            print(f"          -> {f}")

    print("\n" + "-" * 72)
    print(f"Totals: {n_clean} clean, {n_warn} warn-only, {n_fail} FAIL, "
          f"{n_err} error  (of {len(csvs)} CSV(s))")
    sys.exit(1 if (n_fail or n_err) else 0)


if __name__ == "__main__":
    main()
