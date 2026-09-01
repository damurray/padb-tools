#!/usr/bin/env python3
"""
padb_csv_check.py — pre-flight sanity check for a PADB Type=80 Scatter CSV,
run BEFORE padb_v2.py to catch data-shape surprises early instead of after
a slow build.

Grew directly out of three real issues found 2026-08-10 while plotting a
complex multi-analytic amplitude-accuracy pod:
  - Relative_Amplitude_Sweep's real x-axis is "Amplitude (dBm)", but
    "Frequency (MHz)" auto-detected instead (it happened to also be present,
    as a secondary column) -- silently produced a plot with 0 usable rows
    until "x_col" was set explicitly.
  - Relative_Frequency_Sweep_Vernier_Power_Per_DUT correctly auto-detected
    Frequency as x-axis, but its OWN secondary numeric column
    ("Amplitude (dBm)", 46 real distinct values) is silently dropped
    entirely -- not wrong, just invisible, with no warning either way.
  - Relative_Amplitude_Sweep separately had 2,388 distinct Group values,
    making the boxplot/stat_summary build take ~19 minutes -- no warning
    before starting, would have been nice to know going in.

This script never re-implements the pipeline's own column-detection logic
(that's exactly the kind of duplication that drifts and lies) -- it calls
padb_plots._load_scatter_for_stats() directly, the same function
padb_v2.py itself uses, and inspects what it actually picked.

A second, independent pre-flight also lives here: a publish-target check on a
job.json (--job). It resolves exactly where a job will publish (mirroring
padb_v2.py's own decision) and classifies it against the standard roots.
Policy for cross-site compare_csv jobs: their plot outputs belong under the
dedicated PADB-Compare share (COMPARE_PUBLISH_ROOT). The check confirms a
compare job resolves there, and WARNs when one is opted out (publish_to:"",
so it won't publish at all -- correct only for a purely-local QA run) or
points somewhere other than PADB-Compare.

Usage:
    python padb_csv_check.py <csv_path> [--x-col "Exact Column Name"]
    python padb_csv_check.py --job <job.json>
    python padb_csv_check.py <csv_path> --job <job.json>   # both

Exit codes: 0 = no WARN/FAIL, 1 = at least one WARN or FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import padb_plots as pp  # noqa: E402  (reuse the real pipeline's own loader)

_KNOWN_METADATA = {"analysis type", "model(s)", "algorithm -> result", "units"}

_PASS: list[str] = []
_WARN: list[str] = []
_FAIL: list[str] = []


def ok(msg: str) -> None:
    _PASS.append(msg)
    print(f"  OK    {msg}")


def warn(msg: str) -> None:
    _WARN.append(msg)
    print(f"  WARN  {msg}")


def fail(msg: str) -> None:
    _FAIL.append(msg)
    print(f"  FAIL  {msg}")


def check_csv(csv_path: Path, x_col: str | None = None) -> None:
    print(f"Checking: {csv_path}\n")

    raw = pd.read_csv(csv_path, dtype=str)
    raw.columns = raw.columns.str.strip()
    print(f"  {len(raw):,} rows, {len(raw.columns)} columns: {list(raw.columns)}\n")

    # ---- 1. Load exactly the way padb_v2.py loads it ----------------------
    df = pp._load_scatter_for_stats(csv_path, x_col=x_col)
    if df.empty:
        fail(
            "_load_scatter_for_stats() returned 0 usable rows -- the "
            "x-axis/value column auto-detection likely picked the wrong "
            "columns (see any [WARN] printed above by the loader itself). "
            'Pass --x-col "Exact Column Name" to test a specific column, '
            'or set "x_col" in job.json once you know the right one.'
        )
        return

    freq_col_picked = df["_freq_col_name"].iloc[0]
    val_col_picked = df["_val_col_name"].iloc[0]
    ok(f"x-axis column resolved to {freq_col_picked!r}")
    ok(f"value column resolved to {val_col_picked!r}")

    n_usable = len(df)
    n_dropped = len(raw) - n_usable
    drop_pct = 100 * n_dropped / len(raw) if len(raw) else 0
    print(f"\n  {n_usable:,} of {len(raw):,} raw rows have a usable Frequency+Value "
          f"({drop_pct:.0f}% dropped) -- this is what padb_v2.py will actually report as \"Rows:\".")
    if drop_pct > 50:
        warn(
            f"{drop_pct:.0f}% of raw rows dropped for missing/unparseable "
            f'"{freq_col_picked}" or "{val_col_picked}". Likely a normal PADB '
            f"extraction characteristic for this analytic (e.g. placeholder "
            f"rows for other measurement types), not necessarily a problem -- "
            f"but worth a quick sanity check if you weren't expecting it."
        )

    # ---- 2. X-axis sanity: any other numeric column look more "swept"? ---
    freq_distinct = int(df["Frequency_MHz"].nunique())
    numeric_candidates: dict[str, int] = {}
    for c in raw.columns:
        if c in (freq_col_picked, val_col_picked):
            continue
        cl = c.lower()
        if cl in _KNOWN_METADATA or cl in ("group", "test step") or "limit" in cl:
            continue
        vals = pd.to_numeric(raw[c], errors="coerce")
        if vals.notna().sum() == 0:
            continue  # not actually a numeric column (e.g. free text)
        n_distinct = int(vals.nunique())
        if n_distinct > 1:
            numeric_candidates[c] = n_distinct

    if not numeric_candidates:
        ok("no orphaned numeric columns found")
    else:
        for col, n_distinct in numeric_candidates.items():
            if n_distinct > freq_distinct:
                warn(
                    f'unused numeric column "{col}" has {n_distinct:,} distinct '
                    f"values -- MORE than the detected x-axis "
                    f'"{freq_col_picked}" ({freq_distinct:,}). This looks like '
                    f'it could be the REAL swept axis -- try --x-col "{col}" '
                    f"(or set \"x_col\" in job.json) and compare row counts."
                )
            else:
                warn(
                    f'unused numeric column "{col}" has {n_distinct:,} distinct '
                    f"values. Not used as the x-axis, a filter, or a condition "
                    f"-- currently invisible everywhere in the interactive "
                    f"views. If this is a real secondary sweep dimension "
                    f"(like a second amplitude/frequency point set), it will "
                    f"stay silently pooled across all its values with no way "
                    f"to isolate one."
                )

    # ---- 3. Group cardinality ----------------------------------------------
    group_col = next((c for c in raw.columns if c.lower() == "group"), None)
    if group_col is None:
        warn("no \"Group\" column found -- no condition filtering will be available at all")
        group_vals: list[str] = []
    else:
        group_vals = sorted(raw[group_col].dropna().unique())
        n_groups = len(group_vals)
        if n_groups > 500:
            warn(
                f"{n_groups:,} distinct Group values -- combinatorial "
                f"aggregation will be slow (a 2,388-condition analytic took "
                f"~19 minutes to build boxplot/stat_summary). Consider "
                f'reviewing which Grouping_Items are really needed, and plan '
                f'to use "Group by" once it\'s built.'
            )
        elif n_groups > 100:
            warn(
                f"{n_groups:,} distinct Group values -- boxplot/stat_summary's "
                f'default legend will be crowded; "Group by" is recommended '
                f"as soon as you open it."
            )
        else:
            ok(f"{n_groups} distinct Group values -- manageable as-is")

    # ---- 4. Grouping-item presence (checked against the real Group text, --
    #         not just _load_scatter_for_stats's simpler dedicated-column-
    #         only Serial detection, since most real pods embed Serial in
    #         Group text rather than a dedicated CSV column -- see
    #         PADB_Analytic_Requirements.md section 7). -----------------------
    parsed = [pp._parse_group_kv(g) for g in group_vals] if group_vals else []
    all_keys: set[str] = set()
    for kv in parsed:
        all_keys.update(kv.keys())

    def _has_key(*keywords: str) -> bool:
        return any(any(kw in k.lower() for kw in keywords) for k in all_keys)

    has_serial_key = _has_key("serial", "unit id", "dut id", "s/n")
    has_serial_col = (df["Serial"] != "").any()
    has_serial = has_serial_key or has_serial_col
    if has_serial:
        ok(
            "Serial identification found "
            f"({'Group text' if has_serial_key else 'dedicated CSV column'}) "
            "-- per-DUT filtering available"
        )
    else:
        warn(
            "no Serial identification found (neither a Group-text key nor a "
            "dedicated CSV column) -- all DUTs will collapse to n=1, "
            "statistical plots (stat_summary/boxplot) will not be meaningful. "
            'Add a "Serial Number" Grouping_Item to the analytic.'
        )

    has_port_key = _has_key("port")
    if has_port_key:
        ok("Port dimension found in Group text -- Port filter will be available")

    has_limit = bool(df["Upper_Limit"].notna().any() or df["Lower_Limit"].notna().any())
    if has_limit:
        ok("Upper/Lower Limit found -- spec lines and pass/fail available")
    else:
        warn(
            "no Upper/Lower Limit found -- no spec lines or pass/fail markers "
            '(can still be entered manually in the HTML, or set "spec_direction" '
            "in job.json for a one-sided default)"
        )

    has_spec = bool(df["Spec_Hi"].notna().any() or df["Spec_Lo"].notna().any())
    has_unc = bool(df["Unc_Hi"].notna().any() or df["Unc_Lo"].notna().any())
    if has_spec or has_unc:
        ok("Upper/Lower Spec and/or Uncertainty found -- full 3-way Segment-by support available")
    elif has_limit:
        warn(
            '"Segment by: Spec" and "Segment by: Uncertainty" will show zero '
            'segments (falls back to "Limit", which is spec adjusted per-unit '
            "and often not frequency-piecewise-constant). Add \"Upper Spec\"/"
            '"Upper Uncertainty" Grouping_Items if you need the full split.'
        )

    # ---- 5. Temperature coverage -------------------------------------------
    temps = sorted(df["Temperature"].dropna().unique()) if "Temperature" in df.columns else []
    if len(temps) > 1:
        ok(f"{len(temps)} temperature steps found ({', '.join(temps)}) -- all 6 views available")
    else:
        warn(
            "Room-only data -- distribution/env_coverage/summary views will "
            'not be built (only scatter+boxplot). Check Environment_TestStep '
            'in the pod\'s [Extract] section if this is unexpected.'
        )


def check_job(job_path: Path) -> None:
    """Pre-flight the publish target of a job.json.

    Mirrors padb_v2.py's own publish decision (generate_report(), the
    `if "publish_to" in cfg: if cfg["publish_to"]: ...` block) exactly, so a
    "will this publish, and where" answer here can't disagree with what the
    real run does. The two share roots are imported from padb_v2, not
    re-hardcoded, so the paths can never drift out of sync.
    """
    print(f"Checking job: {job_path}\n")

    try:
        cfg = json.loads(job_path.read_text(encoding="utf-8"))
    except Exception as e:  # malformed JSON, encoding, etc.
        fail(f"could not parse job JSON ({e}) -- fix the file before running it")
        return
    if not isinstance(cfg, dict):
        fail("job JSON is not an object -- not a valid job file")
        return

    # Import lazily so the pure-CSV path never pays for it and existing
    # usage is unaffected if padb_v2 ever grows an import-time cost.
    import padb_v2 as pv  # noqa: E402

    is_compare = bool(cfg.get("compare_csv"))
    print(f"  job kind: {'cross-site compare (compare_csv)' if is_compare else 'standard'}\n")

    # Resolve the effective publish destination exactly as padb_v2.py does
    # (generate_report()): an explicit truthy "publish_to" is copied to
    # verbatim (no subfolder appended by _publish); an explicit falsy value
    # opts out; an absent key routes to the default root for the job kind,
    # with the results_dir name appended as a per-job subfolder.
    if "publish_to" in cfg:
        pt = cfg["publish_to"]
        if pt:
            dest, mode = str(pt), "explicit"
        else:
            dest, mode = None, "optout"
    else:
        root = pv.COMPARE_PUBLISH_ROOT if is_compare else pv.DEFAULT_PUBLISH_ROOT
        sub = Path(cfg.get("results_dir") or "<results_dir>").name
        dest, mode = str(Path(root) / sub), "default"

    def _under(path: str, root: str) -> bool:
        return path.casefold().startswith(root.casefold())

    if is_compare:
        if mode == "optout":
            warn(
                'compare_csv job opts out of publishing (publish_to:"") -- its '
                "output will NOT reach the PADB-Compare share. Correct only for "
                "a purely-local QA run; set publish_to to a PADB-Compare path "
                "(or remove the key to use the compare default) to publish."
            )
        elif _under(dest, pv.COMPARE_PUBLISH_ROOT):
            note = " (via the implicit compare default -- consider setting publish_to explicitly)" if mode == "default" else ""
            ok(f"publishes to the PADB-Compare share: {dest}{note}")
        else:
            warn(
                f"compare_csv job publishes to {dest} -- NOT under the standard "
                f"compare share ({pv.COMPARE_PUBLISH_ROOT}). Intended?"
            )
    else:
        if mode == "optout":
            ok('publish_to is empty/false/null -- output stays local (no publish)')
        elif mode == "default":
            ok(f"publishes to the default share: {dest}")
        else:
            ok(f"publishes to: {dest}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("csv_path", type=Path, nargs="?", default=None,
                    help="Type=80 Scatter CSV to sanity-check (optional if --job is given)")
    ap.add_argument("--x-col", default=None, help='Exact x-axis column name to test, overriding auto-detection')
    ap.add_argument("--job", type=Path, default=None,
                    help="Job JSON to pre-flight for publish_to safety (independent of the CSV check)")
    args = ap.parse_args()

    if args.csv_path is None and args.job is None:
        ap.error("provide a CSV path, --job <job.json>, or both")

    if args.csv_path is not None:
        if not args.csv_path.exists():
            print(f"[ERROR] not found: {args.csv_path}")
            sys.exit(1)
        check_csv(args.csv_path, x_col=args.x_col)

    if args.job is not None:
        if not args.job.exists():
            print(f"[ERROR] not found: {args.job}")
            sys.exit(1)
        if args.csv_path is not None:
            print()  # spacer between the two reports
        check_job(args.job)

    print(f"\n{'='*55}")
    print(f"  OK: {len(_PASS)}    WARN: {len(_WARN)}    FAIL: {len(_FAIL)}")
    if _WARN or _FAIL:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
