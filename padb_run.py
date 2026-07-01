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
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# Locate padb_batch.py relative to this file's location
sys.path.insert(0, str(Path(__file__).parent.parent / "PADBPython"))
sys.path.insert(0, str(Path(__file__).parent))

from padb_batch import PADBBatch


# ---------------------------------------------------------------------------
# Job config
# ---------------------------------------------------------------------------

def load_job(job_path: Path) -> dict:
    """Load job.json, resolving relative paths against the job file location."""
    with open(job_path, encoding="utf-8") as f:
        cfg = json.load(f)

    base = job_path.parent
    cfg["_base_dir"] = base
    cfg["_job_filename"] = job_path.name

    pod_raw = cfg.get("pod", "")
    cfg["_pod_path"] = (base / pod_raw).resolve() if pod_raw else None

    results_raw = cfg.get("results_dir", "results")
    cfg["_results_dir"] = (base / results_raw).resolve()

    cfg.setdefault("padb_exe", r"C:\Program Files\KEYSIGHT\PADB-R.NET\PADB-R.exe")
    cfg.setdefault("run_analytics", True)
    cfg.setdefault("padb_timeout", 600)

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


def make_run_pod(src_pod: Path, dest_pod: Path, subex: dict) -> None:
    """
    Write a copy of the pod to dest_pod with [Extract] key=value lines
    patched from subex overrides. The original pod's relative paths (including
    SaoFile=) are left unchanged so PADB resolves them from its own working
    directory during extraction.
    """
    with open(src_pod, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    in_extract = False
    out_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_extract = (stripped.lower() == "[extract]")

        if "=" in stripped and in_extract:
            key = stripped.split("=", 1)[0].strip()
            if key in subex:
                line = f"{key}={subex[key]}\n"

        out_lines.append(line)

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
        print(f"  WARNING: no analytic stems found — skipping R-Plots collection")
        return

    results_padb.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for f in output_dir.iterdir():
        if not f.is_file():
            continue
        stem = f.stem  # e.g. "EP6_Closed_Loop_Phase_Noise_EFC_1"
        if any(stem == s or stem.startswith(s + "_") for s in known_stems):
            dest = results_padb / f.name
            shutil.copy2(str(f), str(dest))
            copied.append(f.name)

    if copied:
        print(f"  Collected {len(copied)} file(s) from {output_dir.name}/")
        for name in sorted(copied):
            print(f"    {name}")
    else:
        print(f"  No matching files in {output_dir.name}/ — check PADB log")


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
        PADB replaces spaces with underscores and preserves hyphens.
        We try the hyphen-as-underscore variant as a fallback.
        """
        if not s:
            return []
        primary = s.replace(" ", "_")          # spaces → underscores, hyphens kept
        secondary = primary.replace("-", "_")  # hyphens → underscores (fallback)
        return [primary, secondary] if primary != secondary else [primary]

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
            primary = name.replace(" ", "_")
            for stem in [primary, primary.replace("-", "_")]:
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

    pod_path = cfg.get("_pod_path")
    if not pod_path or not pod_path.exists():
        print(f"ERROR: pod file not found: {pod_path}")
        sys.exit(1)

    results_dir: Path = cfg["_results_dir"]
    results_padb = results_dir / "padb"
    plots_dir = results_dir / "plots"

    results_padb.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    print(f"Description : {cfg.get('description', '')}")
    print(f"POD         : {pod_path}")
    print(f"Results     : {results_dir}\n")

    # Parse analytics from pod
    analytics = parse_pod_analytics(pod_path)
    print(f"Analytics found in pod: {len(analytics)}")
    for a in analytics:
        title_hint = f"  ({a['main_title']})" if a.get("main_title") else ""
        print(f"  [{a['index']}] {_type_label(a['type']):14s}  {a.get('name', '')}{title_hint}")

    # Create run pod copy with baked-in subex overrides
    run_pod = results_dir / "_run.pod"
    subex = cfg.get("subex", {})
    make_run_pod(pod_path, run_pod, subex)
    print(f"\nRun pod: {run_pod}\n")

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

    # Secondary plots
    plot_results: list[dict] = []
    if cfg.get("secondary_plots"):
        print(f"\nGenerating {len(cfg['secondary_plots'])} secondary plot(s):")
        plot_results = run_secondary_plots(cfg, csv_map, plots_dir)

    # Index HTML
    print("\nGenerating index.html ...")
    idx = make_index_html(cfg, analytics, csv_map, plot_results, results_padb)
    print(f"  {idx}")

    # Publish
    if not args.no_publish:
        print("\nPublishing:")
        publish_results(cfg, results_dir)

    print("\nDone.\n")


if __name__ == "__main__":
    main()
