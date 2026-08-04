"""
padb_run.py — PADB run orchestrator

Usage:
    py padb_run.py path/to/job.json
    py padb_run.py path/to/job.json --dry-run       # build switches, skip PADB
    py padb_run.py path/to/job.json --no-publish    # skip copy to share
    py padb_run.py path/to/job.json --plots-only    # skip PADB, redo plots from existing CSVs
"""
from __future__ import annotations

import argparse
import calendar
import io
import json
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Locate padb_batch.py relative to this file's location
sys.path.insert(0, str(Path(__file__).parent.parent / "PADBPython"))
sys.path.insert(0, str(Path(__file__).parent))

from padb_batch import PADBBatch
import padb_config


# ---------------------------------------------------------------------------
# Stdout tee — writes to both console and a log file simultaneously
# ---------------------------------------------------------------------------

class _Tee(io.TextIOBase):
    """Wraps two text streams and writes to both."""
    def __init__(self, primary: io.TextIOBase, secondary: io.TextIOBase) -> None:
        self._primary = primary
        self._secondary = secondary

    def write(self, s: str) -> int:
        self._primary.write(s)
        self._secondary.write(s)
        return len(s)

    def flush(self) -> None:
        self._primary.flush()
        self._secondary.flush()


# ---------------------------------------------------------------------------
# Job config
# ---------------------------------------------------------------------------

def _padb_quoted_list(values: list) -> str:
    """Format a Python list as PADB's comma-separated quoted string: 'a','b','c'."""
    return ",".join(f"'{v}'" for v in values)


# Friendly job.json list fields that map to [Extract] subex overrides.
# Each entry: (job_key, Extract_key).
_LIST_FIELD_MAP = [
    ("run_datetimes", "TestRun_RunDateTime"),
    ("serial_nums",   "TestRun_SerialNum"),
    ("run_labels",    "TestRun_RunLabel"),
]

_RELATIVE_DATE_RE = re.compile(
    r"^(\d+)\s+(day|days|week|weeks|month|months|year|years)\s+ago$", re.IGNORECASE
)


def _resolve_date_sentinel(value: str) -> str | None:
    """
    Resolve a subex date placeholder to PADB's YYYY-MM-DD format, evaluated
    at the moment the job actually runs (not whenever job.json was written):

        "today"        -> today's date
        "4 weeks ago"   -> today minus 28 days
        "10 days ago", "3 months ago", "1 year ago" -- same idea

    Returns None if value doesn't match any supported placeholder; the
    caller leaves non-matching values untouched (e.g. literal "2026-07-31").
    """
    stripped = value.strip().lower()
    today = datetime.now().date()
    if stripped == "today":
        return today.isoformat()

    m = _RELATIVE_DATE_RE.match(stripped)
    if not m:
        return None

    n = int(m.group(1))
    unit = m.group(2).rstrip("s")
    if unit == "day":
        result = today - timedelta(days=n)
    elif unit == "week":
        result = today - timedelta(weeks=n)
    elif unit == "month":
        month_index = today.month - 1 - n
        year = today.year + month_index // 12
        month = month_index % 12 + 1
        day = min(today.day, calendar.monthrange(year, month)[1])
        result = today.replace(year=year, month=month, day=day)
    else:  # year
        day = min(today.day, 28) if today.month == 2 and today.day == 29 else today.day
        result = today.replace(year=today.year - n, day=day)
    return result.isoformat()


def load_job(job_path: Path) -> dict:
    """Load job.json, resolving relative paths against the job file location.

    Friendly list fields are converted to PADB-format subex overrides so
    callers don't have to hand-format the 'val1','val2' quoting:

        "run_datetimes": ["06/04/2026 01:06:18 PM", "06/09/2026 11:04:19 AM"]
        "serial_nums":   ["US65080415", "US65080423"]
        "run_labels":    ["DDS Harmonics", "Spectral YTO Mode 0 ALC ON"]

    These are merged into "subex" before any raw subex keys, so an explicit
    subex entry for the same key takes precedence.

    Any subex value can also be a relative-date placeholder, resolved to
    PADB's YYYY-MM-DD format at run time rather than baked in when the
    job.json was written: "today", "4 weeks ago", "3 months ago", etc.
    See _resolve_date_sentinel() for the full supported set.
    """
    with open(job_path, encoding="utf-8") as f:
        cfg = json.load(f)

    base = job_path.parent
    cfg["_base_dir"] = base
    cfg["_job_filename"] = job_path.name

    pod_raw = cfg.get("pod", "")
    cfg["_pod_path"] = (base / pod_raw).resolve() if pod_raw else None

    results_raw = cfg.get("results_dir", "results")
    cfg["_results_dir"] = (base / results_raw).resolve()

    # Per-user defaults (padb_exe, padb_output_dir, padb_logs_dir) -- from
    # padb_config.json if present, else derived from Path.home() so a job.json
    # never has to hardcode a specific username. Only fills in keys this
    # job.json omits -- existing job.json files that already specify these
    # explicitly are unaffected.
    _user_defaults = padb_config.load_defaults()
    cfg.setdefault("padb_exe", _user_defaults["padb_exe"])
    cfg.setdefault("padb_output_dir", _user_defaults["padb_output_dir"])
    cfg.setdefault("padb_logs_dir", _user_defaults["padb_logs_dir"])
    cfg.setdefault("run_analytics", True)
    cfg.setdefault("padb_timeout", 600)
    cfg.setdefault("mode", "legacy")  # "legacy" (V1, default) | "simple" | "interactive"

    # Convert friendly list fields into subex overrides (raw subex wins on conflict)
    list_overrides = {}
    for job_key, extract_key in _LIST_FIELD_MAP:
        vals = cfg.get(job_key)
        if vals:  # non-empty list → inject as subex
            list_overrides[extract_key] = _padb_quoted_list(vals)
    if list_overrides:
        merged = {**list_overrides, **cfg.get("subex", {})}
        cfg["subex"] = merged

    # Resolve "today" / "N weeks ago"-style placeholders in subex to actual
    # dates now, so the same job.json stays correct run after run.
    subex = cfg.get("subex")
    if subex:
        for key, val in list(subex.items()):
            if isinstance(val, str):
                resolved = _resolve_date_sentinel(val)
                if resolved is not None:
                    subex[key] = resolved

    return cfg


# ---------------------------------------------------------------------------
# POD file parsing
# ---------------------------------------------------------------------------

_TYPE_LABELS: dict[int, str] = {
    10: "BarPlot",
    20: "BoxPlot",
    60: "Environmental",
    80: "Scatter",
    90: "SummaryPlot",
}


def _type_label(t: int | None) -> str:
    if t is None:
        return "?"
    return _TYPE_LABELS.get(t, f"Type={t}")


def parse_pod_analytics(pod_path: Path) -> list[dict]:
    """Return list of {index, type, name, output_file, main_title} from .pod file."""
    analytics: list[dict] = []
    current: dict | None = None

    with open(pod_path, encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            m = re.match(r"^\[PADBAnalytic(\d+)\]$", line)
            if m:
                if current:
                    analytics.append(current)
                current = {"index": int(m.group(1)), "type": None,
                           "name": None, "output_file": None,
                           "output_csv": True, "main_title": None}
                continue
            if current is None:
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip()
            if key == "Type":
                current["type"] = int(val) if val.isdigit() else None
            elif key == "AnalyticName":
                current["name"] = val
            elif key == "OutputConfig_OutputFile":
                current["output_file"] = val or None
            elif key == "OutputConfig_OutputCSV":
                current["output_csv"] = (val.strip() not in ("0", ""))
            elif key == "OutputConfig_MainTitle":
                current["main_title"] = val or None

    if current:
        analytics.append(current)

    return analytics


# Simple mode needs PADB-R's own native PNG/PDF rendering turned on per analytic.
_SIMPLE_FORCE_KEYS = {"OutputConfig_OutputGraph": "1", "OutputConfig_GraphFormat": "png,pdf"}


def _slugify_name(name: str) -> str:
    return re.sub(r"[^\w]+", "_", name.strip()).strip("_")


def make_run_pod(src_pod: Path, dest_pod: Path, subex: dict,
                  force_native_render: bool = False,
                  unique_output_filenames: bool = False) -> None:
    """
    Write a copy of the pod to dest_pod with [Extract] key=value lines
    patched from subex overrides. The original pod's relative paths (including
    SaoFile=) are left unchanged so PADB resolves them from its own working
    directory during extraction.

    When force_native_render is True, every [PADBAnalyticN] section also gets
    OutputConfig_OutputGraph/OutputConfig_GraphFormat forced on (Simple mode
    needs PADB-R's own native renders; existing keys are replaced in place,
    missing ones are appended when the section ends). No-op when False.

    When unique_output_filenames is True, every [PADBAnalyticN] section's
    AnalyticName and OutputConfig_OutputFile are both forced to the same
    slugified form of that section's own original AnalyticName -- guarantees
    every analytic in the pod writes a uniquely-named, self-consistent CSV
    without depending on whoever authored the pod to have kept
    OutputConfig_OutputFile unique and matching AnalyticName by hand (e.g. the
    Harmonics_and_Subharmonics pod has 13 of 19 analytics sharing one
    OutputFile despite having 19 distinct AnalyticNames). No-op when False.

    Callers that pass either flag should re-parse analytics from dest_pod
    (not src_pod) afterward -- the analytics list downstream code uses for
    file-collection stem-matching must reflect whatever this function
    actually wrote, not the original pod.

    Slugifying can itself introduce a new collision when two AnalyticNames
    differ only by punctuation style (e.g. "Sub-Harmonics Summary 50MHz-20GHz"
    vs "Sub-Harmonics_Summary_50MHz-20GHz" both slugify to the same string --
    a real case in the Harmonics_and_Subharmonics pod). Any slug that isn't
    unique after the first pass gets that analytic's own index appended, so
    uniqueness is actually guaranteed, not just usually true.
    """
    analytic_slugs: dict[int, str] = {}
    if unique_output_filenames:
        base_slugs = {a["index"]: _slugify_name(a["name"])
                       for a in parse_pod_analytics(src_pod) if a.get("name")}
        slug_counts: dict[str, int] = {}
        for slug in base_slugs.values():
            slug_counts[slug] = slug_counts.get(slug, 0) + 1
        for index, slug in base_slugs.items():
            analytic_slugs[index] = f"{slug}_{index}" if slug_counts[slug] > 1 else slug

    with open(src_pod, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    in_extract = False
    in_analytic = False
    current_analytic_index: int | None = None
    seen_force_keys: set[str] = set()
    out_lines: list[str] = []

    def _flush_analytic_section() -> None:
        if force_native_render and in_analytic:
            for key, val in _SIMPLE_FORCE_KEYS.items():
                if key not in seen_force_keys:
                    out_lines.append(f"{key}={val}\n")

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            _flush_analytic_section()
            in_extract = (stripped.lower() == "[extract]")
            m = re.match(r"^\[PADBAnalytic(\d+)\]$", stripped)
            in_analytic = bool(m)
            current_analytic_index = int(m.group(1)) if m else None
            seen_force_keys = set()

        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            slug = analytic_slugs.get(current_analytic_index) if in_analytic else None
            if in_extract and key in subex:
                line = f"{key}={subex[key]}\n"
            elif force_native_render and in_analytic and key in _SIMPLE_FORCE_KEYS:
                line = f"{key}={_SIMPLE_FORCE_KEYS[key]}\n"
                seen_force_keys.add(key)
            elif slug and key in ("AnalyticName", "OutputConfig_OutputFile"):
                line = f"{key}={slug}\n"

        out_lines.append(line)

    _flush_analytic_section()  # handle the last section in the file

    dest_pod.parent.mkdir(parents=True, exist_ok=True)
    dest_pod.write_text("".join(out_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# PADB execution
# ---------------------------------------------------------------------------

def _analytic_stems(analytics: list[dict]) -> set[str]:
    """
    Build the set of filename stems PADB will write for these analytics.
    PADB replaces spaces with underscores; hyphens are kept (with underscore
    fallback).  Both OutputFile and AnalyticName are included because PADB
    may use either as the base for its output filenames.
    """
    stems: set[str] = set()
    for a in analytics:
        for val in (a.get("output_file") or "", a.get("name") or ""):
            if not val:
                continue
            primary = val.replace(" ", "_")
            stems.add(primary)
            stems.add(primary.replace("-", "_"))
    return stems


def _collect_padb_outputs(cfg: dict, analytics: list[dict], results_padb: Path) -> None:
    """
    Copy files written to padb_output_dir into results_padb, selecting only
    files whose stem matches a known analytic OutputFile or AnalyticName.

    Before copying, clears any existing results_padb files for stems that
    have fresh output this run -- otherwise every invocation (repeated real
    runs of the same job) piles its collected files on top of the last, and
    an analytic that legitimately paginates into many PNGs (one card per PNG
    in Simple mode) accumulates stale duplicates from past runs indefinitely.
    Stems with NO fresh match this run are left untouched -- e.g. a CSV
    manually placed in results_padb because PADB never writes one for that
    analytic (see the clock-spurs SummaryPlot gotcha in CLAUDE.md) survives.

    This replaces the previous timestamp-based sweep so that parallel PADB
    jobs writing to the same R-Plots directory do not cross-contaminate each
    other's results.
    """
    output_dir_raw = cfg.get("padb_output_dir", "")
    if not output_dir_raw:
        return
    output_dir = Path(output_dir_raw)
    if not output_dir.exists():
        print(f"  WARNING: padb_output_dir not found: {output_dir}")
        return

    known_stems = _analytic_stems(analytics)
    if not known_stems:
        print(f"  WARNING: no analytic stems found -- skipping R-Plots collection")
        return

    def _matched_stem(stem: str) -> str | None:
        return next((s for s in known_stems if stem == s or stem.startswith(s + "_")), None)

    results_padb.mkdir(parents=True, exist_ok=True)

    fresh = [(f, _matched_stem(f.stem)) for f in output_dir.iterdir() if f.is_file()]
    fresh = [(f, s) for f, s in fresh if s is not None]
    active_stems = {s for _, s in fresh}

    removed = [
        f for f in results_padb.iterdir()
        if f.is_file() and _matched_stem(f.stem) in active_stems
    ]
    for f in removed:
        f.unlink()

    copied: list[str] = []
    for f, _ in fresh:
        dest = results_padb / f.name
        shutil.copy2(str(f), str(dest))
        copied.append(f.name)

    if removed:
        print(f"  Cleared {len(removed)} stale file(s) from a previous run")
    if copied:
        print(f"  Collected {len(copied)} file(s) from {output_dir.name}/")
        for name in sorted(copied):
            print(f"    {name}")
    else:
        print(f"  No matching files in {output_dir.name}/ -- check PADB log")


def run_padb(cfg: dict, run_pod: Path, results_padb: Path,
             analytics: list[dict] | None = None,
             dry_run: bool = False) -> tuple[int, str, str]:
    """Build and execute PADB-R.exe. Returns (returncode, stdout, stderr)."""
    batch = PADBBatch(exe_path=cfg["padb_exe"])
    batch.set_dir(str(results_padb))
    batch.lpod("d", str(run_pod))

    # Runtime subex overrides (on top of baked-in pod edits)
    runtime_subex = cfg.get("runtime_subex", {})
    if runtime_subex:
        batch.subex(runtime_subex)

    # PADB always extracts from Oracle DB with -ext r.
    # The .sao file is PADB's output (saved after extraction) — not an input
    # source for batch analytics. Oracle connectivity is required.
    batch.ext("r")

    if cfg.get("run_analytics", True):
        batch.an()

    sw_path = cfg["_results_dir"] / "padb_switches.txt"
    cmd, sf, _ = batch.build_command(use_file=True, switch_file_path=sw_path)

    print(f"  Switch file : {sf}")
    print(f"  Command     : {' '.join(cmd)}")

    if dry_run:
        print("  [DRY RUN] PADB not executed.")
        return 0, "", ""

    exe = Path(cfg["padb_exe"])
    if not exe.exists():
        print(f"  WARNING: PADB-R.exe not found at {exe}")
        print("  Skipping PADB execution (exe missing).")
        return -1, "", "PADB-R.exe not found"

    print("  Running PADB-R.exe ...")
    run_start = time.time()
    # PADB-R.exe is a WinForms (GUI) app — stdout/stderr are always empty.
    # Run without capture_output so Windows creates a proper GUI context.
    cp = batch.run(use_file=True, switch_file_path=sw_path,
                   timeout=cfg["padb_timeout"], capture_output=False)
    elapsed = time.time() - run_start
    print(f"  PADB completed in {elapsed:.1f}s, return code: {cp.returncode}")

    log_path = cfg["_results_dir"] / "run.log"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"PADB run: {datetime.now().isoformat()}\n")
        f.write(f"Return code: {cp.returncode}\n")
        f.write(f"Elapsed: {elapsed:.1f}s\n")
        f.write("Note: PADB-R.exe is a GUI app; stdout/stderr unavailable.\n")
        logs_dir = cfg.get("padb_logs_dir", "")
        if logs_dir:
            f.write(f"PADB session logs: {logs_dir}\n")

    # Collect PADB outputs from the actual write location.
    # PADB-R writes to its configured R-Plots directory, not to -dir.
    _collect_padb_outputs(cfg, analytics or [], results_padb)

    return cp.returncode, cp.stdout or "", cp.stderr or ""


# ---------------------------------------------------------------------------
# CSV discovery
# ---------------------------------------------------------------------------

def find_csvs(results_padb: Path, analytics: list[dict]) -> dict[str, Path]:
    """
    Map analytic name → CSV path.

    Priority order per analytic:
      1. AnalyticName → {name}.csv  (spaces→underscores)
      2. OutputConfig_OutputFile → {output_file}.csv
      3. OutputFile with analytic index suffix → {output_file}_{index}.csv
      4. Fuzzy glob on first 20 chars of AnalyticName slug

    This handles pods where multiple analytics share the same OutputFile name
    (PADB uses AnalyticName as the differentiator in those cases).
    """
    if not results_padb.exists():
        return {}

    # Build a stem→path index of all CSVs actually present
    all_csv_stems: dict[str, Path] = {p.stem: p for p in results_padb.glob("*.csv")}

    def _stems(s: str) -> list[str]:
        """
        Return candidate filename stems for a name string.
        PADB replaces spaces with underscores, hyphens kept, and decimal points
        become underscores (e.g. "1.5" → "1_5" in filenames).
        We try four variants: (hyphen kept | hyphen→_) × (dot kept | dot→_).
        """
        if not s:
            return []
        p0 = s.replace(" ", "_")                           # spaces→_, hyphens/dots kept
        p1 = p0.replace(".", "_")                          # spaces→_, dots→_
        p2 = p0.replace("-", "_")                          # spaces→_, hyphens→_
        p3 = p2.replace(".", "_")                          # spaces→_, hyphens→_, dots→_
        seen: list[str] = []
        for stem in (p0, p1, p2, p3):
            if stem not in seen:
                seen.append(stem)
        return seen

    csv_map: dict[str, Path] = {}
    for a in analytics:
        analytic_name = a.get("name") or ""
        output_file = a.get("output_file") or ""
        index = a.get("index", 0)
        key = analytic_name or output_file

        # Skip analytics that don't produce CSVs
        if not key or not a.get("output_csv", True):
            continue

        found: Path | None = None

        # Build ordered list of candidate stems to try
        stems_to_try: list[str] = []
        for n in _stems(analytic_name):             # AnalyticName variants (highest priority)
            stems_to_try.append(n)
        for n in _stems(output_file):               # OutputFile variants
            stems_to_try.append(n)
            stems_to_try.append(f"{n}_{index}")     # OutputFile_N (when multiple analytics share OutputFile)
            stems_to_try.append(f"{n}_{index:03d}") # OutputFile_00N

        for stem in stems_to_try:
            if stem in all_csv_stems:
                found = all_csv_stems[stem]
                break

        # Fuzzy glob fallback: first 15 chars of primary AnalyticName stem
        if found is None and analytic_name:
            slug = analytic_name.replace(" ", "_")[:15]
            matches = sorted(results_padb.glob(f"{slug}*.csv"))
            if matches:
                found = matches[0]

        if found is not None:
            csv_map[key] = found

    return csv_map


# ---------------------------------------------------------------------------
# Secondary plots
# ---------------------------------------------------------------------------

def run_secondary_plots(cfg: dict, csv_map: dict, plots_dir: Path) -> list[dict]:
    """Run secondary plot functions from padb_plots. Returns list of result dicts."""
    import padb_plots  # noqa: imported here so missing plotly gives a clear error

    plots_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    for plot_cfg in cfg.get("secondary_plots", []):
        plot_type = plot_cfg.get("type", "")
        csv_name = plot_cfg.get("csv", "")
        title = plot_cfg.get("title", csv_name)

        # Resolve CSV path — csv_file (literal filename) takes priority over csv (analytic name)
        csv_file_raw = plot_cfg.get("csv_file", "")
        if csv_file_raw:
            csv_path: Path | None = plots_dir.parent / "padb" / csv_file_raw
            if not csv_path.exists():
                csv_path = plots_dir.parent / csv_file_raw
            if not csv_path.exists():
                print(f"  WARNING: csv_file not found: {csv_file_raw}")
                continue
        else:
            csv_path = csv_map.get(csv_name)
            if csv_path is None:
                for k, v in csv_map.items():
                    if csv_name.lower() in k.lower():
                        csv_path = v
                        break
            if csv_path is None:
                print(f"  WARNING: CSV not found for plot '{title}' (csv='{csv_name}')")
                continue

        safe = re.sub(r"[^\w\-]", "_", title)[:50]
        out_html = plots_dir / f"{safe}.html"

        fn = getattr(padb_plots, plot_type, None)
        if fn is None:
            print(f"  WARNING: Unknown plot type '{plot_type}'")
            continue

        try:
            fn(csv_path, plot_cfg, out_html)
            results.append({
                "title": title,
                "html_path": out_html,
                "csv_name": csv_name,
                "plot_type": plot_type,
            })
            print(f"  Plot: {out_html.name}")
        except Exception as exc:
            print(f"  ERROR generating plot '{title}': {exc}")

    return results


# ---------------------------------------------------------------------------
# Index HTML
# ---------------------------------------------------------------------------

def make_index_html(
    cfg: dict,
    analytics: list[dict],
    csv_map: dict,
    plot_results: list[dict],
    results_padb: Path,
) -> Path:
    """Write results/index.html aggregating all outputs."""
    results_dir: Path = cfg["_results_dir"]
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    description = cfg.get("description", "")
    pod_name = cfg["_pod_path"].name if cfg.get("_pod_path") else "N/A"

    pdfs = sorted(results_padb.glob("*.pdf")) if results_padb.exists() else []
    csvs = sorted(results_padb.glob("*.csv")) if results_padb.exists() else []
    csv_names = {c.name for c in csvs}

    # --- Blocks ---

    plot_blocks = ""
    for pr in plot_results:
        rel = pr["html_path"].relative_to(results_dir).as_posix()
        plot_blocks += f"""
    <div class="card plot-card">
      <h3>{pr['title']}</h3>
      <div class="iframe-wrap">
        <iframe src="{rel}" scrolling="no" frameborder="0" loading="lazy"></iframe>
      </div>
      <p class="meta">Type: {pr['plot_type']} &nbsp;|&nbsp; CSV: {pr['csv_name']}
         &nbsp;|&nbsp; <a href="{rel}" target="_blank">Open full-screen ↗</a></p>
    </div>
"""

    # Build set of all CSV stems found (for checking which analytics produced output)
    csv_stems_found = {p.stem for p in results_padb.glob("*.csv")} if results_padb.exists() else set()

    def _a_csv_status(a: dict) -> tuple[str, str]:
        """Returns (css_class, symbol) for the CSV column."""
        if not a.get("output_csv", True):
            return "na", "N/A"  # analytic explicitly has OutputCSV=0
        for name in [a.get("name") or "", a.get("output_file") or ""]:
            if not name:
                continue
            p0 = name.replace(" ", "_")
            for stem in [p0, p0.replace("-", "_"),
                         p0.replace(".", "_"), p0.replace("-", "_").replace(".", "_")]:
                if stem in csv_stems_found:
                    return "ok", "✓"
        return "missing", "✗"

    analytic_rows = "\n".join(
        f'<tr><td>{a["index"]}</td>'
        f'<td>{_type_label(a["type"])}</td>'
        f'<td>{a.get("name", "") or "<em>unnamed</em>"}</td>'
        f'<td style="color:#888;font-size:0.8em">{a.get("main_title") or ""}</td>'
        + (lambda s, sym: f'<td class="{s}">{sym}</td></tr>')(*_a_csv_status(a))
        for a in analytics
    )

    subex_rows = "\n".join(
        f"<tr><td>{k}</td><td><code>{v}</code></td></tr>"
        for k, v in cfg.get("subex", {}).items()
    ) or "<tr><td colspan='2'>None</td></tr>"

    pdf_links = "\n".join(
        f'<li><a href="padb/{p.name}" target="_blank">{p.stem}</a></li>'
        for p in pdfs
    ) or "<li>None</li>"

    csv_links = "\n".join(
        f'<li><a href="padb/{c.name}">{c.stem}</a></li>'
        for c in csvs
    ) or "<li>None</li>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PADB Results — {description or pod_name}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: Arial, Helvetica, sans-serif; background: #f0f2f5; color: #222; font-size: 14px; }}
  .header {{ background: #003366; color: #fff; padding: 18px 32px; }}
  .header h1 {{ font-size: 1.35em; font-weight: 700; }}
  .header p  {{ margin-top: 4px; opacity: 0.75; font-size: 0.9em; }}
  .body  {{ padding: 24px 32px; }}
  .meta-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .card {{ background: #fff; border-radius: 6px; padding: 16px 18px; box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
  .card h2 {{ font-size: 0.95em; color: #003366; border-bottom: 1px solid #e0e0e0; padding-bottom: 6px; margin-bottom: 10px; }}
  .card h3 {{ font-size: 1em; color: #003366; margin-bottom: 10px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.85em; }}
  th {{ background: #f5f5f5; font-weight: 600; color: #555; text-align: left; padding: 5px 8px; }}
  td {{ padding: 4px 8px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }}
  td.ok {{ color: green; font-weight: bold; }}
  td.na {{ color: #aaa; }}
  td.missing {{ color: #cc4400; font-weight: bold; }}
  code {{ font-size: 0.85em; background: #f5f5f5; padding: 1px 4px; border-radius: 3px; }}
  ul {{ padding-left: 16px; font-size: 0.88em; line-height: 1.7; }}
  a {{ color: #003366; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .section-title {{ font-size: 1.05em; font-weight: 700; color: #003366;
                    border-bottom: 2px solid #003366; padding-bottom: 4px; margin: 24px 0 14px; }}
  .plot-card {{ margin-bottom: 20px; }}
  .iframe-wrap {{ width: 100%; height: 520px; border: 1px solid #ddd; border-radius: 4px; overflow: hidden; }}
  .iframe-wrap iframe {{ width: 100%; height: 100%; border: none; }}
  .meta {{ font-size: 0.8em; color: #888; margin-top: 6px; }}
</style>
</head>
<body>
<div class="header">
  <h1>PADB Analysis Results</h1>
  <p>{description}</p>
</div>
<div class="body">

  <div class="meta-grid">
    <div class="card">
      <h2>Run Info</h2>
      <table>
        <tr><td>POD file</td><td>{pod_name}</td></tr>
        <tr><td>Generated</td><td>{run_time}</td></tr>
        <tr><td>Results dir</td><td>{results_dir.name}</td></tr>
        <tr><td>PADB exe</td><td><code>{cfg.get("padb_exe","")}</code></td></tr>
      </table>
    </div>
    <div class="card">
      <h2>Extraction Overrides (subex)</h2>
      <table><tr><th>Key</th><th>Value</th></tr>{subex_rows}</table>
    </div>
    <div class="card">
      <h2>Analytics</h2>
      <table><tr><th>#</th><th>Type</th><th>Name</th><th>Title</th><th>CSV</th></tr>{analytic_rows}</table>
    </div>
  </div>

  <div class="section-title">Interactive Plots</div>
  {plot_blocks if plot_blocks else '<p style="color:#888">No secondary plots configured.</p>'}

  <div class="section-title">Downloads</div>
  <div class="meta-grid">
    <div class="card">
      <h2>PADB PDF Reports</h2>
      <ul>{pdf_links}</ul>
    </div>
    <div class="card">
      <h2>CSV Data Files</h2>
      <ul>{csv_links}</ul>
    </div>
    <div class="card">
      <h2>Run Artifacts</h2>
      <ul>
        <li><a href="run.log">run.log</a></li>
        <li><a href="padb_switches.txt">padb_switches.txt</a></li>
        <li><a href="_run.pod">_run.pod</a> (pod used for this run)</li>
      </ul>
    </div>
  </div>

</div>
</body>
</html>"""

    idx_path = results_dir / "index.html"
    idx_path.write_text(html, encoding="utf-8")
    return idx_path


# ---------------------------------------------------------------------------
# Mode guidance
# ---------------------------------------------------------------------------

_MODE_GUIDANCE = {
    "simple": """\
PADB SIMPLE MODE -- HOW TO GET THE MOST OUT OF THIS RUN
=========================================================

What you're looking at:
  index.html shows PADB-R.exe's own native plot renders (PNG, linked to a
  matching PDF) -- exactly what PADB-R would produce if you ran the pod
  interactively, no custom plotting or statistics on top. The table next to
  each image is a literal dump of that analytic's own extraction/analysis
  settings (grouping, spec limits, date bounds, etc.) straight from the pod
  -- nothing here is computed by this tool.

What you WON'T find here:
  No filters, no serial/condition exclusion, no tolerance intervals, no
  interactive controls. Simple mode is a direct, static replacement for the
  extract-and-post that PADB::Simple used to do -- if you need to slice the
  data, filter by serial number, or see statistical summaries with
  confidence intervals, this is not the tier for that.

Downloads available per analytic (results/padb/):
  .pdf   -- print-quality version of the same native plot
  .csv   -- raw extracted data, if the analytic produces one
  .sao   -- PADB's saved analysis object for this extraction
  .pod   -- the pod snapshot PADB-R wrote for this run
  .txt   -- PADB-R's own tabular export of the plotted data

Want the richer interactive tier instead?
  Set "mode": "interactive" in this job.json and see PADB_Tools_Guide.md /
  GETTING_STARTED.md for the two-command V2 workflow (filters, tolerance
  intervals, serial exclusion, global-flag exclusion, CSV export, etc.).
""",
    "interactive": """\
PADB INTERACTIVE MODE -- HOW TO GET THE MOST OUT OF THIS RUN
================================================================

This job.json is set up to feed the V2 pipeline -- the richer interactive
plot suite (scatter, stat_summary, boxplot, distribution, env_coverage,
summary) with filters, tolerance intervals, serial/condition exclusion,
and global-flag (GF) exclusion.

This step (padb_run.py) only extracted the CSV(s) -- see "CSVs found" above
in the run log. To build the actual interactive HTML views, run:

  py padb_v2.py <your_v2_job.json> --csv <path/to/data.csv>

using one of the CSVs just extracted into results/padb/. See
GETTING_STARTED.md ("Two pipelines") and PADB_Tools_Guide.md for the full
V2 job.json schema (views, room_values, publish_to, etc.) and what each
interactive control does.

Want the leaner static tier instead?
  Set "mode": "simple" in this job.json for a direct extract-and-post
  gallery of PADB-R's own native plot renders, no interactivity.
""",
}


def write_mode_guidance(cfg: dict, mode: str) -> Path:
    """Write results/HOW_TO_USE.txt -- a short, mode-aware guidance file so
    users know how to get the most out of whichever tier they just ran."""
    results_dir: Path = cfg["_results_dir"]
    out_path = results_dir / "HOW_TO_USE.txt"
    out_path.write_text(_MODE_GUIDANCE[mode], encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def publish_results(cfg: dict, results_dir: Path) -> None:
    dest_raw = cfg.get("publish", {}).get("destination", "")
    if not dest_raw:
        return
    dest = Path(dest_raw)
    try:
        print(f"  Publishing to {dest} ...")
        if dest.exists():
            shutil.copytree(str(results_dir), str(dest), dirs_exist_ok=True)
        else:
            shutil.copytree(str(results_dir), str(dest))
        print(f"  Published OK.")
    except Exception as exc:
        print(f"  WARNING: Publish failed: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="PADB run orchestrator")
    parser.add_argument("job", help="Path to job.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build switch file but skip PADB-R.exe execution")
    parser.add_argument("--no-publish", action="store_true",
                        help="Skip copying results to publish share")
    parser.add_argument("--plots-only", action="store_true",
                        help="Skip PADB; regenerate plots from existing CSVs")
    args = parser.parse_args()

    job_path = Path(args.job).resolve()
    if not job_path.exists():
        print(f"ERROR: job.json not found: {job_path}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  PADB Run: {job_path.name}")
    print(f"{'='*60}\n")

    cfg = load_job(job_path)

    mode = cfg["mode"]
    if mode not in ("legacy", "simple", "interactive"):
        print(f"ERROR: unknown mode '{mode}' -- expected 'legacy', 'simple', or 'interactive'")
        sys.exit(1)

    pod_path = cfg.get("_pod_path")
    if not pod_path or not pod_path.exists():
        print(f"ERROR: pod file not found: {pod_path}")
        sys.exit(1)

    results_dir: Path = cfg["_results_dir"]
    results_padb = results_dir / "padb"
    plots_dir = results_dir / "plots"

    results_padb.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    # Tee stdout to a timestamped log file so scheduled/unattended runs are captured.
    log_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = results_dir / f"padb_run_{log_ts}.log"
    _log_fh = open(log_file_path, "w", encoding="utf-8", buffering=1)
    _orig_stdout = sys.stdout
    sys.stdout = _Tee(_orig_stdout, _log_fh)  # type: ignore[assignment]

    print(f"Description : {cfg.get('description', '')}")
    print(f"POD         : {pod_path}")
    print(f"Results     : {results_dir}\n")

    # Create run pod copy with baked-in subex overrides
    run_pod = results_dir / "_run.pod"
    subex = cfg.get("subex", {})
    unique_output_filenames = cfg.get("unique_output_filenames", False)
    make_run_pod(pod_path, run_pod, subex, force_native_render=(mode == "simple"),
                 unique_output_filenames=unique_output_filenames)
    print(f"Run pod: {run_pod}\n")

    # Parse analytics from the actual run pod, not the original -- reflects
    # any AnalyticName/OutputConfig_OutputFile renaming make_run_pod() applied
    analytics = parse_pod_analytics(run_pod)
    print(f"Analytics found in pod: {len(analytics)}")
    for a in analytics:
        title_hint = f"  ({a['main_title']})" if a.get("main_title") else ""
        print(f"  [{a['index']}] {_type_label(a['type']):14s}  {a.get('name', '')}{title_hint}")
    print()

    # Run PADB
    if not args.plots_only:
        print("Running PADB:")
        rc, _, _ = run_padb(cfg, run_pod, results_padb, analytics=analytics, dry_run=args.dry_run)
        if rc not in (0, -1) and not args.dry_run:
            print(f"\nWARNING: PADB-R.exe returned code {rc}. See run.log.")
        print()

    # Find CSVs
    csv_map = find_csvs(results_padb, analytics)
    print(f"CSVs found: {len(csv_map)}")
    for name, path in csv_map.items():
        print(f"  {path.name}")

    if mode == "simple":
        # Simple mode: literal extract-and-post gallery of PADB-R's own native
        # PNG/PDF renders -- no custom plotting, no secondary_plots.
        import padb_simple
        print("\nGenerating Simple mode gallery ...")
        idx = padb_simple.make_simple_gallery_html(cfg, analytics, results_padb, csv_map)
        print(f"  {idx}")
    else:
        # Secondary plots
        plot_results: list[dict] = []
        if cfg.get("secondary_plots"):
            print(f"\nGenerating {len(cfg['secondary_plots'])} secondary plot(s):")
            plot_results = run_secondary_plots(cfg, csv_map, plots_dir)

        # Index HTML
        print("\nGenerating index.html ...")
        idx = make_index_html(cfg, analytics, csv_map, plot_results, results_padb)
        print(f"  {idx}")

    if mode in ("simple", "interactive"):
        write_mode_guidance(cfg, mode)

    if mode == "interactive":
        first_csv = next(iter(csv_map.values()), "<path/to/data.csv>")
        print(f"\nMode: interactive -- build the V2 view suite with:")
        print(f"  py padb_v2.py <your_v2_job.json> --csv {first_csv}")

    # Publish
    if not args.no_publish:
        print("\nPublishing:")
        publish_results(cfg, results_dir)

    print("\nDone.\n")

    sys.stdout = _orig_stdout
    _log_fh.close()
    print(f"Log: {log_file_path}")


if __name__ == "__main__":
    main()
