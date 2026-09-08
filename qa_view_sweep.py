#!/usr/bin/env python3
r"""qa_view_sweep.py -- rebuild a curated set of real V2 job.json files into a
throwaway output dir and headlessly verify each generated view, so a QA pass is
"a couple of commands" instead of clicking through pages.

QA plan "Gate 3": exercises the whole plot/JS surface with NO new PADB
extraction (it drives existing job.json files against their already-extracted
CSVs via `padb_v2.py <job> --out <tmp> --no-publish`). For every job it:

  1. runs the real build and captures stdout signals (auto-binary_encode NOTE,
     oversized-view warning, "no matching test data", any ERROR/FAIL);
  2. lists the views actually built (confirms auto view-selection);
  3. loads each view in headless Edge and asserts objective markers --
       * the Plotly plot actually rendered (no fatal JS error aborted it),
       * no error/placeholder sentinel in the DOM,
       * optional per-job needles (e.g. boxplot shows real serials, a compare
         page has the Site Population Check panel).

Because it runs REAL job.json files, it uses each pod's real cfg (x_col,
spec_direction, compare_csv, ...) with no guessing -- it just needs the jobs to
exist. Missing jobs are reported as SKIP, not failures.

Usage:
  py qa_view_sweep.py                      # run the default coverage set
  py qa_view_sweep.py --list               # show which jobs resolve, run nothing
  py qa_view_sweep.py --job PATH ...        # add ad-hoc job.json(s) to the run
  py qa_view_sweep.py --keep                # keep the temp output for inspection
  py qa_view_sweep.py --max-headless-mb 200 # DOM-dump larger views too (slow)

Exit code 1 if any built view fails a hard check (didn't render, or hit an
error sentinel, or a required needle was missing/forbidden one present).
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOTS = [
    Path(r"C:\temp\data"),
    Path.home() / "OneDrive - Keysight Technologies" / "Documents" / "Padb" / "Data",
]
_EXCLUDE_DIR_PARTS = {"backup", "Job_Archive"}

_EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# A view is "rendered" if Plotly injected its SVG / plot container. This is the
# authoritative pass signal: if the plot drew, no fatal JS error aborted it, and
# dormant error-handler strings baked into the view's JS (e.g. a collapsed panel's
# catch template) are irrelevant.
_RENDER_MARKERS = ("js-plotly-plot", "main-svg")
# Placeholder-page phrases that _write_placeholder emits into the DOM -- used ONLY
# to explain a genuine non-render, never as a standalone fail signal (several of
# these also appear as static text inside view JS, so matching them on a page that
# DID render would be a false positive).
_ERROR_SENTINELS = (
    "No usable rows",
    "no matching test data",
    "the analytic wrote no output",
)

# --- Coverage set --------------------------------------------------------
# Each entry names a real *_v2_job.json (newest match wins) and the axis it
# covers. `needles` (optional) are checked against the raw HTML of any view
# whose filename contains the key: {"boxplot": {"present": [...], "absent": [...]}}.
# Tune the globs to your own job names; missing ones are SKIPped, not failed.
_DEFAULT_JOBS = [
    {"label": "multi-temp + serial + staircase spec",
     "glob": "NonHarmonics_close_In_Spec_Setting_Data2_v2_job.json",
     "needles": {"boxplot": {"present": ['ALL_BOX_SERIALS=["US'],
                             "absent": ['ALL_BOX_SERIALS=["unknown"]']}}},
    {"label": "Room-only + serial (auto view-selection)",
     "glob": "NonHarmonics_close_In_Spec_Setting_Data2-AMC2_v2_job.json",
     "needles": {"boxplot": {"present": ['ALL_BOX_SERIALS=["MY'],
                             "absent": ['ALL_BOX_SERIALS=["unknown"]']}}},
    {"label": "no spec limits (spec_direction / Limit-display)",
     "glob": ["*[Mm]ax*[Pp]ower*[Ll]og*_v2_job.json", "maxpower*_v2_job.json",
              "*[Mm]ax*[Pp]ower*_v2_job.json"]},
    {"label": "non-frequency x-axis (x_col/x_label)",
     "glob": ["*IddVsVgg*_v2_job.json", "*Relative_Amplitude*_v2_job.json",
              "*Vgg*_v2_job.json"]},
    {"label": "high-cardinality / fragmented conditions",
     "glob": "*[Cc]lock*[Ss]purs*_v2_job.json"},
    {"label": "cross-site compare (Site Population Check)",
     "glob": "compare_*_v2_job.json",
     "needles": {"boxplot": {"present": ["Site Population Check"]}}},
    {"label": "large / dense continuous sweep (binary_encode/size guard)",
     "glob": "*Phase_Noise_DCFM*_v2_job.json"},
    # Anchored to the start of the name so it can't match *Non*Harmonics.
    {"label": "multi-analytic Harmonics family",
     "glob": ["[Hh]armonics*_v2_job.json", "[Hh]armonics_and_[Ss]ub*_v2_job.json"]},
]


def _find_edge() -> str | None:
    for cand in _EDGE_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


def _resolve_job(pattern, roots) -> Path | None:
    """Newest *_v2_job.json matching pattern (a glob string or list of them,
    tried in order) under the roots, excluding backups / run jobs."""
    patterns = [pattern] if isinstance(pattern, str) else list(pattern)
    for pat in patterns:
        hits: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            for p in root.rglob(pat):
                if _EXCLUDE_DIR_PARTS & set(p.parts):
                    continue
                if p.name.endswith("_run_job.json"):
                    continue
                hits.append(p)
        if hits:
            return max(hits, key=lambda p: p.stat().st_mtime)
    return None


def _dump_dom(edge: str, html_path: Path, budget_ms: int, timeout_s: int) -> str | None:
    """Headless-render html_path and return the post-JS DOM, or None on failure.
    Mirrors qa_js_segments.py's Edge invocation (kill-after-timeout is normal --
    msedge lingers after --dump-dom has already written its output)."""
    with tempfile.TemporaryDirectory() as udd:
        dom_path = Path(udd) / "dom.html"
        with dom_path.open("w", encoding="utf-8") as dom_f:
            proc = subprocess.Popen(
                [edge, "--headless", "--disable-gpu", "--disable-crash-reporter",
                 f"--virtual-time-budget={budget_ms}", f"--user-data-dir={udd}",
                 "--dump-dom", str(html_path)],
                stdout=dom_f, stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            return dom_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None


def _view_of(name: str) -> str:
    for v in ("scatter", "stat_summary", "boxplot", "distribution",
              "env_coverage", "summary"):
        if name.endswith(f"_{v}.html"):
            return v
    return name


def _build(job: Path, out_dir: Path) -> tuple[int, str]:
    """Run padb_v2.py for one job into out_dir; return (returncode, stdout+stderr)."""
    v2 = Path(__file__).with_name("padb_v2.py")
    proc = subprocess.run(
        [sys.executable, str(v2), str(job), "--out", str(out_dir), "--no-publish"],
        capture_output=True, text=True, timeout=1800,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _stdout_signals(text: str) -> list[str]:
    keys = ("NOTE:", "WARNING", "auto-enabled binary_encode", "MB --",
            "no matching test data", "[ERROR]", "FAIL", "build_failures")
    seen, out = set(), []
    for ln in text.splitlines():
        s = ln.strip()
        if any(k in s for k in keys) and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, action="append", default=None,
                    help="Root dir to search for the coverage-set jobs (repeatable). "
                         "Defaults to the standard data roots -- point this at your own "
                         "data dir to run the sweep on another machine's jobs.")
    ap.add_argument("--job", action="append", default=[],
                    help="Extra job.json to run (repeatable), added to the default set.")
    ap.add_argument("--no-defaults", action="store_true",
                    help="Run only the --job argument(s), skipping the built-in coverage set.")
    ap.add_argument("--list", action="store_true",
                    help="Resolve the default job set and print what would run; run nothing.")
    ap.add_argument("--keep", action="store_true",
                    help="Keep the temp output dir (prints its path) instead of deleting it.")
    ap.add_argument("--budget", type=int, default=15000,
                    help="Edge --virtual-time-budget in ms for headless render (default 15000).")
    ap.add_argument("--timeout", type=int, default=90,
                    help="Per-view headless wait in seconds before killing msedge (default 90).")
    ap.add_argument("--max-headless-mb", type=float, default=120.0,
                    help="Views larger than this are reported built + size but not DOM-dumped "
                         "(a giant page can take minutes to serialize). Default 120.")
    args = ap.parse_args()

    roots = args.root or _ROOTS
    entries = [] if args.no_defaults else list(_DEFAULT_JOBS)
    for j in args.job:
        entries.append({"label": "ad-hoc", "glob": None, "path": Path(j)})

    # Resolve jobs
    resolved = []
    for e in entries:
        path = e.get("path") or (_resolve_job(e["glob"], roots) if e.get("glob") else None)
        resolved.append((e, path))

    print("Coverage set:")
    for e, path in resolved:
        print(f"  {'[found]' if path else '[SKIP ]'} {e['label']:48s} "
              f"{path if path else '(no matching job.json)'}")
    if args.list:
        return

    edge = _find_edge()
    if not edge:
        print("\n[WARN] msedge.exe not found -- builds will run but views won't be "
              "headless-rendered (marker checks skipped).")

    tmp_root = Path(tempfile.mkdtemp(prefix="qa_view_sweep_"))
    hard_fail = 0
    checked = 0
    print("\n" + "=" * 72)
    for i, (e, job) in enumerate(resolved, 1):
        if not job:
            continue
        print(f"\n[{i}] {e['label']}\n    job: {job}")
        out_dir = tmp_root / f"job{i}"
        try:
            rc, log = _build(job, out_dir)
        except subprocess.TimeoutExpired:
            print("    BUILD FAIL: padb_v2.py timed out (>1800s)")
            hard_fail += 1
            continue
        for sig in _stdout_signals(log):
            print(f"    · {sig}")
        if rc != 0:
            print(f"    BUILD FAIL: padb_v2.py exited {rc} (see signals above / build_failures.log)")
            hard_fail += 1
            continue
        views = sorted(p for p in out_dir.rglob("*.html") if p.name != "index.html")
        if not views:
            print("    BUILD FAIL: no view HTML produced")
            hard_fail += 1
            continue
        print(f"    built {len(views)} view(s): {', '.join(sorted({_view_of(v.name) for v in views}))}")
        needles = e.get("needles", {})
        for v in views:
            mb = v.stat().st_size / (1024 * 1024)
            vname = _view_of(v.name)
            raw = v.read_text(encoding="utf-8", errors="ignore")

            # optional per-job needle checks (raw HTML -- reliable for JS constants)
            needle_notes = []
            spec = next((needles[k] for k in needles if k in v.name), None)
            if spec:
                for s in spec.get("present", []):
                    if s not in raw:
                        needle_notes.append(f"MISSING required: {s!r}")
                for s in spec.get("absent", []):
                    if s in raw:
                        needle_notes.append(f"FORBIDDEN present: {s!r}")

            # render check (headless), unless the page is too big / edge missing
            render_note = ""
            if not edge:
                render_note = "render check skipped (no Edge)"
            elif mb > args.max_headless_mb:
                render_note = f"render check skipped ({mb:.0f} MB > {args.max_headless_mb:.0f} MB cap)"
            else:
                dom = _dump_dom(edge, v, args.budget, args.timeout)
                checked += 1
                if dom is None:
                    render_note = "render check ERROR (no DOM dumped)"
                    needle_notes.append("headless dump failed")
                elif any(m in dom for m in _RENDER_MARKERS):
                    # Plot drew -> working, regardless of dormant catch strings in the JS.
                    render_note = "rendered OK"
                else:
                    diag = next((s for s in _ERROR_SENTINELS if s in dom), None)
                    render_note = (f"NOT rendered -- {diag}" if diag
                                   else "NOT rendered (no Plotly plot)")
                    needle_notes.append(render_note)

            status = "FAIL" if needle_notes else "ok"
            if status == "FAIL":
                hard_fail += 1
            print(f"      [{status:4s}] {vname:13s} {mb:6.1f} MB  {render_note}")
            for n in needle_notes:
                print(f"               -> {n}")

    print("\n" + "=" * 72)
    print(f"Headless-checked {checked} view(s); {hard_fail} hard failure(s).")
    if args.keep:
        print(f"Temp output kept at: {tmp_root}")
    else:
        import shutil
        shutil.rmtree(tmp_root, ignore_errors=True)
    sys.exit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
