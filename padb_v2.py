"""
padb_v2.py — PADB Analytics V2.1

Single-scatter approach: one PADB Scatter (Type=80) CSV with Test Step encoding
temperature supplies all data needed for every plot view.

Views generated from one CSV:
  scatter       — accuracy-vs-frequency (room, per DUT)
  stat_summary  — parametric TI + TLL + Spec_supportable (room)
  boxplot       — box plots per condition / frequency / temperature
  distribution  — histogram / violin per condition / frequency
  env_coverage  — scatter all temperatures (environmental coverage)
  summary       — group-by-frequency summary with TLL band (all temps)

Usage (CLI):
    python padb_v2.py job_v2.json
    python padb_v2.py job_v2.json --csv path/to/Scatter.csv  # skip PADB run

Job JSON schema:
{
    "description": "...",
    "pod": "relative/or/absolute.pod",       # optional if --csv supplied
    "analytic": "Harmonics_Env_Dataset",     # scatter analytic name in pod
    "padb_exe": "C:\\...\\PADB-R.exe",       # optional if --csv supplied
    "results_dir": "v2_results",
    "padb_timeout": 1800,
    "title_prefix": "SG6311A Harmonics",
    "y_label": "Power (dBc)",
    "y_lim": [-120, 0],
    "room_values": ["Room"],                 # Test Step values treated as room temp
    "proportion": 0.90,
    "confidence": 0.90,
    "freq_scale": 1.0,                       # optional: multiply Frequency_MHz by this (e.g. 1e-6 if CSV stores Hz)
    "views": ["scatter", "stat_summary", "boxplot", "distribution",
              "env_coverage", "summary"],   # omit to auto-select based on data:
                                             #   Room-only  -> scatter, boxplot, reference,
                                             #                 summary, stat_summary
                                             #   multi-temp -> all six
    "room_only_full_views": False,          # DEPRECATED / no-op: summary + stat_summary are
                                             # Room-only DEFAULTS now (2026-09-15). Kept accepted
                                             # for back-compat. distribution/env_coverage still
                                             # never auto-added Room-only (need non-Room deltas)
    "env_coverage_csv": "",                  # optional: alternate CSV for env_coverage view (e.g. carrier power dBm)
    "env_coverage_y_label": "",             # y-axis label override for env_coverage when env_coverage_csv is set
    "env_coverage_y_lim": null,             # y-axis limits override [lo, hi] for env_coverage when env_coverage_csv is set
    "env_coverage_freq_scale": 1.0,         # freq_scale override for env_coverage when env_coverage_csv is set
    "compare_csv": {"SR": "path/to/sr.csv", "AMC2": "path/to/amc2.csv"},
                                             # optional: merge 2+ sites' own scatter CSVs into
                                             # one, tagging each row "Site: <name>" so Site
                                             # becomes a normal condition dimension everywhere.
                                             # Overrides csv_path when present.
    "primary_site": "SR",                   # which compare_csv site's population defines the
                                             # boxplot "Site Population Check" fence. Defaults
                                             # to the first key in compare_csv when omitted.
    "publish_to": ""                         # network path; omit key entirely to use the
                                              # default share (padb-tools-results, or
                                              # PADB-Compare when "compare_csv" is set), or
                                              # set to "" / false / null to opt out of publishing
}
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

# Default publish destination for jobs that don't set their own "publish_to".
# Each job gets its own subfolder (named after its results_dir) so unrelated
# jobs don't collide. Set "publish_to": "" / false / null explicitly to opt out.
DEFAULT_PUBLISH_ROOT = r"\\srsnas01.srs.is.keysight.com\prod\MIDRF3\SG6311A\padb-tools-results"

# Same idea, but for cross-site compare_csv jobs -- a third top-level share
# tree alongside PADB-Simple (padb_make_job.py) and PADB-Interactive
# (padb_make_v2_job.py), added 2026-08-28 at the user's request so compare
# output doesn't keep landing in the generic padb-tools-results catch-all.
# Only takes effect when a job has no explicit "publish_to" of its own --
# every hand-authored compare job in this codebase's history so far sets
# "publish_to": "" to opt out entirely (see the accidental-publish incident
# in CLAUDE.md), so this only matters for a *new* compare job that omits it.
COMPARE_PUBLISH_ROOT = r"\\srsnas01.srs.is.keysight.com\prod\MIDRF3\SG6311A\PADB-Compare"

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import padb_run

# ---------------------------------------------------------------------------
# Reuse V1.0 internals — same directory
# ---------------------------------------------------------------------------
try:
    import padb_plots as _pp
    _HAS_V1 = True
except ImportError:
    _HAS_V1 = False
    print("[WARN] padb_plots not found -- HTML rendering unavailable", file=sys.stderr)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    "load_scatter",
    "generate_report",
]


# ===========================================================================
# 1.  Load & normalise
# ===========================================================================

class NoPlottableData(Exception):
    """A CSV has no usable measurement data to plot -- e.g. the analytic
    returned no matching test results for this database/site, so PADB wrote a
    default placeholder export (Model Number / PROCEDURE TIME columns, no
    numeric x-axis) instead of the real scatter. Carries a human-readable
    reason so the caller can log 'no matching test data' rather than a raw
    traceback. Real case: an SR-cloned Pulse pod whose Overshoot analytic had
    no result rows in the SR DB (2026-09-03)."""


def _diagnose_no_data(csv_path: Path, cfg: dict | None, detail: str = "") -> str:
    """Best-effort human reason for why a CSV yielded nothing to plot, from
    its own column names -- the 'good reference' being what a real scatter
    CSV looks like (a Frequency / X-value column plus a numeric measurement)."""
    try:
        cols = list(pd.read_csv(csv_path, nrows=0).columns)
    except Exception:
        cols = []
    has_x = any(("frequency" in c.lower() or "x value" in c.lower()) for c in cols)
    is_placeholder = any("procedure time" in c.lower() for c in cols) or bool(cols and not has_x)
    x_col = cfg.get("x_col") if cfg else None
    parts = []
    if x_col and cols and x_col not in cols:
        parts.append(f"the configured x_col {x_col!r} is not one of the CSV's columns")
    if not has_x:
        parts.append("there is no Frequency / X-value column to plot against")
    if is_placeholder:
        parts.append("the columns look like PADB's default placeholder export "
                     "(e.g. Model Number / PROCEDURE TIME) -- what PADB writes when the "
                     "analytic returns NO MATCHING TEST results for this database/site")
    reason = "no plottable measurement data"
    if parts:
        reason += " -- " + "; ".join(parts)
    reason += f". CSV columns: {cols}."
    if detail:
        reason += f" (loader detail: {detail})"
    return reason


def _log_build_failure(output_dir: Path, cfg: dict, csv_path: Path, reason: str) -> None:
    """Print a clear failure reason AND append it to build_failures.log in the
    results dir, so a plot build that can't proceed says *why* (e.g. 'no
    matching test data') instead of just a stack trace. Printed too, so the web
    app's per-job console log (webapp_console.log / 'View log') captures it."""
    prefix = (cfg.get("title_prefix") or cfg.get("description") or csv_path.stem) if cfg else csv_path.stem
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = (f"[{stamp}] BUILD FAILED: {prefix}\n"
             f"  CSV   : {csv_path}\n"
             f"  Reason: {reason}\n")
    print("\n" + block, flush=True)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_dir / "build_failures.log", "a", encoding="utf-8") as fh:
            fh.write(block + "\n")
    except OSError:
        pass


# A self-contained view HTML embeds all its data plus Plotly inline. Past
# roughly this size a browser gets sluggish, and well past it (a few hundred
# MB) the page may not open at all -- it looks like "the plot produced no
# data" even though the data is all there. Warn with margin so it's diagnosed,
# not silently unrenderable. Real case (2026-09-03): an 8.5M-row / 7,662-
# offset phase-noise DCFM boxplot produced a 517MB HTML that wouldn't render.
VIEW_SIZE_WARN_MB = 80

# Auto-enable binary_encode above either threshold. binary_encode is plot-transparent
# -- it float32-packs the numeric arrays (Frequency/Value, boxplot vals_detail), changing
# only the file encoding/size, never a displayed value/statistic/plot -- so turning it on
# for large data is a pure size/latency optimization with no accuracy risk. Whichever
# trigger fires first wins. An explicit "binary_encode" in job.json (true OR false) always
# overrides this. Thresholds overridable per-job via binary_encode_auto_mb /
# binary_encode_auto_rows (set either to a huge value to effectively disable that trigger).
AUTO_BINARY_ENCODE_MB = 25
AUTO_BINARY_ENCODE_ROWS = 250_000


def _maybe_auto_binary_encode(cfg: dict, csv_path: Path, df) -> None:
    """Set cfg['binary_encode']=True when the CSV is large by size OR usable row count,
    unless job.json set it explicitly. Logs a NOTE with the reason."""
    if "binary_encode" in cfg:
        return  # explicit job.json setting always wins (either direction)
    try:
        mb = csv_path.stat().st_size / (1024 * 1024)
    except OSError:
        mb = 0.0
    rows = len(df)
    mb_thr = float(cfg.get("binary_encode_auto_mb", AUTO_BINARY_ENCODE_MB))
    rows_thr = int(cfg.get("binary_encode_auto_rows", AUTO_BINARY_ENCODE_ROWS))
    hit_mb = mb >= mb_thr
    hit_rows = rows >= rows_thr
    if hit_mb or hit_rows:
        cfg["binary_encode"] = True
        why = []
        if hit_mb:
            why.append(f"CSV {mb:.0f} MB >= {mb_thr:.0f} MB")
        if hit_rows:
            why.append(f"{rows:,} usable rows >= {rows_thr:,}")
        print(f"  NOTE: auto-enabled binary_encode ({' and '.join(why)}) -- float32-packs "
              f"numeric arrays to cut page size/latency (plot data unchanged); set "
              f'"binary_encode": false in job.json to override.', flush=True)


def _csv_to_parquet(csv_path: Path, out_path: Path) -> tuple[int, float, float]:
    """Stream a CSV to a zstd-compressed parquet (bounded memory). Returns
    (rows, csv_MB, parquet_MB). Raises on failure (caller decides what to do)."""
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq
    # newlines_in_values: some PADB Group/label cells contain embedded newlines
    # (e.g. Phase_Nose merged CSVs) -- without this the chunker desyncs and the
    # whole export fails ("CSV parser got out of sync with chunker").
    reader = pacsv.open_csv(
        str(csv_path),
        read_options=pacsv.ReadOptions(block_size=64 << 20),
        parse_options=pacsv.ParseOptions(newlines_in_values=True))
    writer = None
    rows = 0
    try:
        for batch in reader:
            if writer is None:
                writer = pq.ParquetWriter(str(out_path), batch.schema,
                                          compression="zstd", compression_level=9)
            writer.write_batch(batch)
            rows += batch.num_rows
    finally:
        if writer is not None:
            writer.close()
    smb = csv_path.stat().st_size / 1e6
    dmb = out_path.stat().st_size / 1e6 if out_path.exists() else 0.0
    return rows, smb, dmb


def _maybe_export_parquet(cfg: dict, csv_path: Path, output_dir: Path, df=None) -> None:
    """Write a compact parquet sidecar of the SOURCE csv next to the report, so
    padb_viewer.py can serve datasets too big to open as self-contained HTML.
    The raw CSV (not the loaded/filtered df) is exported, so the viewer sees the
    same columns the loader detects (x/value/Serial/Group incl. 'Site: ...').

    Gate: explicit cfg['export_parquet'] wins (true/false). Otherwise auto-export
    only when the CSV is LARGE by the same size/row thresholds as binary_encode --
    for compares AND non-compares alike. (Until 2026-09-21 this fired for *every*
    compare regardless of size, so a tiny switching-speed compare got a needless
    sidecar + a misleading "Large-dataset viewer" section on its index; the parquet
    viewer only helps when the self-contained HTML is too big to open. Set
    export_parquet:true to force one on a small job.) Never fails the build."""
    explicit = cfg.get("export_parquet")
    if explicit is False:
        return
    if explicit is not True:
        try:
            mb = csv_path.stat().st_size / (1024 * 1024)
        except OSError:
            mb = 0.0
        large = (mb >= float(cfg.get("export_parquet_auto_mb", AUTO_BINARY_ENCODE_MB))
                 or (df is not None
                     and len(df) >= int(cfg.get("export_parquet_auto_rows",
                                                AUTO_BINARY_ENCODE_ROWS))))
        if not large:
            return
    out_path = output_dir / (csv_path.stem + ".parquet")
    try:
        rows, smb, dmb = _csv_to_parquet(csv_path, out_path)
    except Exception as exc:  # pragma: no cover - defensive; never break a build
        _log_note(output_dir, f"parquet export skipped for {csv_path.name}: {exc}")
        return
    ratio = (smb / dmb) if dmb else 0.0
    print(f"  NOTE: wrote parquet sidecar {out_path.name} ({rows:,} rows, "
          f"{smb:.0f} MB CSV -> {dmb:.1f} MB parquet, {ratio:.0f}x) -- open with "
          f"padb_viewer.py for large datasets a browser can't load as HTML.", flush=True)


def _warn_if_view_too_large(out_html: Path, view: str, cfg: dict, output_dir: Path) -> None:
    """Flag a generated view whose file is large enough that a browser may
    struggle to (or can't) render it -- logged to build_failures.log and the
    console, with concrete size-reduction options, so an unrenderable page is
    diagnosed rather than mistaken for 'no plot data'."""
    try:
        mb = out_html.stat().st_size / (1024 * 1024)
    except OSError:
        return
    if mb < VIEW_SIZE_WARN_MB:
        return
    # Tips must be VIEW-AWARE -- suggesting an option that does nothing for this
    # view is worse than saying nothing. binary_encode is only wired into the two
    # views that embed the big raw numeric arrays (scatter: Frequency/Value;
    # boxplot: per-point vals_detail); scatter_decimate only thins the scatter
    # view's dense series. Both are no-ops on stat_summary/summary/env_coverage/
    # distribution, so don't recommend them there.
    binenc_view = view in ("scatter", "boxplot")
    decimate_view = view == "scatter"
    tips = []
    if binenc_view and not cfg.get("binary_encode"):
        tips.append('"binary_encode": true (float32-packs the embedded numeric arrays)')
    if decimate_view and cfg.get("scatter_decimate") in (None, False):
        tips.append('"scatter_decimate": "auto" (thins dense scatter series, keeps min/max/spikes)')
    tips.append("narrow the extraction (fewer frequency points / conditions / DUTs)")
    if view in ("boxplot", "stat_summary", "summary"):
        tips.append(f"the {view} x-axis is per-frequency -- over thousands of distinct "
                    f"frequencies (e.g. a wide phase-noise offset sweep) it isn't a meaningful "
                    f"view; use the scatter instead")
    # "no size optimizations" only makes sense for a view that HAS an encoding
    # lever (scatter/boxplot) and isn't already using it. On a boxplot that is
    # already binary-encoded and still too big, the size is inherent -> say so.
    opt_on = (bool(cfg.get("binary_encode")) if binenc_view else False) or \
             (cfg.get("scatter_decimate") not in (None, False) if decimate_view else False)
    if not binenc_view and not decimate_view:
        note = ""  # no encoding lever exists for this view
    elif opt_on:
        note = (f" (This view already uses {'binary_encode' if cfg.get('binary_encode') else 'scatter_decimate'}; "
                f"the remaining size is inherent to the data.)")
    else:
        note = " This job used no size optimizations."
    _log_note(output_dir,
              f"{out_html.name} is {mb:.0f} MB -- a self-contained page this large can be very "
              f"slow to open, and past ~a few hundred MB a browser may not render it at all "
              f"(it can look like 'no plot data' even though the data is present)."
              + note
              + " Options: " + "; ".join(tips) + ".")


def _log_note(output_dir: Path, text: str) -> None:
    """Append a non-fatal NOTE to build_failures.log (and print it). Used for
    'a build still succeeded, but here's something worth flagging' -- e.g. one
    site in a compare contributed no plottable rows."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] NOTE: {text}\n"
    print(line, flush=True)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_dir / "build_failures.log", "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def load_scatter(csv_path: Path, cfg: dict | None = None) -> pd.DataFrame:
    """
    Load a PADB Scatter (Type=80) CSV that encodes all conditions and
    temperatures in a single file.

    Returns a normalised DataFrame with columns:
        Frequency_MHz, Value, Serial, Station, Group, Temperature,
        Upper_Limit, Lower_Limit, _val_col_name
    Plus _grp_<Key> columns for each key-value pair in the Group string.

    Delegates to V1.0's _load_scatter_for_stats → _parse_group_fields.
    Those functions already handle:
      - Column detection (frequency, value, serial, station, group, limits)
      - Test Step → Temperature extraction ("20.0 Deg C" → "20°C", "Room" → "Room")
      - _grp_<Key> expansion
    """
    if not _HAS_V1:
        raise RuntimeError("padb_plots is required for load_scatter")

    try:
        df = _pp._load_scatter_for_stats(csv_path, x_col=(cfg.get("x_col") if cfg else None))
    except ValueError as exc:
        # e.g. "x_col=... not found in CSV columns" -- turn the raw error into a
        # diagnosed 'no matching test data'-style reason for the failure log.
        raise NoPlottableData(_diagnose_no_data(csv_path, cfg, str(exc))) from exc
    if df is None or len(df) == 0:
        # Loaded, but every row dropped (no numeric x-axis, all-NA values, a
        # placeholder export, etc.) -- nothing to plot.
        raise NoPlottableData(_diagnose_no_data(csv_path, cfg, "0 usable rows after load"))
    df = _pp._parse_group_fields(df)

    # If Serial column came back empty (serial lives in Group string, not a standalone CSV column),
    # populate it from the first _grp_* column whose name contains "serial".
    if df["Serial"].replace("", pd.NA).isna().all():
        _ser_grp = next(
            (c for c in df.columns if c.startswith("_grp_") and "serial" in c.lower()),
            None,
        )
        if _ser_grp:
            df["Serial"] = df[_ser_grp].fillna("").str.strip()

    # Apply frequency scaling (e.g. PADB exports Hz but column header says MHz: set freq_scale=1e-6)
    freq_scale = cfg.get("freq_scale", 1.0) if cfg else 1.0
    if freq_scale != 1.0:
        df["Frequency_MHz"] = df["Frequency_MHz"] * freq_scale

    # Override Temperature 'Room' normalisation with cfg.room_values if provided
    if cfg:
        room_values = cfg.get("room_values", ["Room"])
        if room_values and room_values != ["Room"]:
            step_col = next(
                (c for c in df.columns if c.lower() == "test_step"), None
            )
            if step_col:
                is_room = df[step_col].str.strip().isin(room_values)
                df["Temperature"] = df["Temperature"].where(~is_room, "Room")

    return df


# ===========================================================================
# 2.  Renderer helpers
# ===========================================================================

def _cfg_for_view(base_cfg: dict, overrides: dict | None = None) -> dict:
    """Merge base cfg with per-view overrides."""
    merged = dict(base_cfg)
    if overrides:
        merged.update(overrides)
    return merged


def _write_placeholder(output_html: Path, title: str, message: str) -> None:
    """Write a minimal placeholder HTML for unimplemented/skipped views."""
    output_html.parent.mkdir(parents=True, exist_ok=True)
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>"
        f"<h2>{title}</h2><p>{message}</p></body></html>"
    )
    output_html.write_text(html, encoding="utf-8")


# ===========================================================================
# 3.  Renderers
# ===========================================================================

def render_scatter(
    df: pd.DataFrame,
    cfg: dict,
    output_html: Path,
) -> None:
    """
    Accuracy-vs-frequency scatter (all temperatures, with env_bar filter).
    Wraps V1.0 accuracy_vs_freq by writing a temporary CSV.
    """
    if df.empty:
        _write_placeholder(output_html, cfg.get("title", output_html.stem),
                           "No data rows found.")
        return

    _tmp = output_html.parent / "_v2_tmp_scatter.csv"
    try:
        _df_to_scatter_csv(df, _tmp)
        _pp.accuracy_vs_freq(_tmp, cfg, output_html)
    finally:
        _tmp.unlink(missing_ok=True)


def render_stat_summary(
    df: pd.DataFrame,
    cfg: dict,
    output_html: Path,
) -> None:
    """
    Parametric TI + TLL + Spec_supportable interactive chart (room temperature).
    Calls V1.0 _aggregate_stat_data + _build_stat_summary_html directly —
    no temp-CSV round-trip needed.
    """
    k_table = _pp._build_k_table()
    stat_data = _pp._aggregate_stat_data(df, cfg)
    all_serials = sorted({
        d["s"]
        for cd in stat_data
        for fs in cd.get("freq_stats", [])
        for d in fs.get("dut_vals", [])
    })
    title = cfg.get("title", output_html.stem)
    html = _pp._build_stat_summary_html(stat_data, k_table, df, cfg, title, all_serials)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


def render_boxplot(
    df: pd.DataFrame,
    cfg: dict,
    output_html: Path,
) -> None:
    """
    Interactive box plots per frequency × condition × temperature.
    Wraps V1.0 stat_boxplot (interactive mode) via temp-CSV.

    TODO V2.0: Replace temp-CSV round-trip with a direct DataFrame renderer.
    """
    _tmp = output_html.parent / "_v2_tmp_boxplot.csv"
    try:
        _df_to_scatter_csv(df, _tmp)
        _pp.stat_boxplot(_tmp, cfg, output_html, interactive=True)
    finally:
        _tmp.unlink(missing_ok=True)


def render_distribution(
    df: pd.DataFrame,
    cfg: dict,
    output_html: Path,
) -> None:
    """
    Multi-temperature overlaid KDE distribution with ΔEnv analysis.
    Detects suspected uncompensated DUTs; supports derived correction and exclusion.
    """
    if df.empty:
        _write_placeholder(output_html, cfg.get("title", output_html.stem),
                           "No data rows found.")
        return
    title = cfg.get("title", output_html.stem)
    html = _pp._build_env_distribution_html(df, cfg, title)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


def render_env_coverage(
    df: pd.DataFrame,
    cfg: dict,
    output_html: Path,
) -> None:
    """
    Interactive environmental delta TI plot with P/C sliders for Room and ΔEnv.

    Aggregates Room stats (mean, std, n per frequency) and paired delta stats
    for each non-Room temperature from the raw scatter DataFrame.  The k-factor
    table is embedded so JS can recompute UDE/LDE/TTU interactively.
    """
    if df.empty:
        _write_placeholder(output_html, cfg.get("title", output_html.stem), "No data rows found.")
        return

    k_table = _pp._build_k_table()
    env_data, cond_dims, non_room_temps, all_serials, all_ports = _pp._aggregate_env_coverage_data(df, cfg)
    help_panel_html = _pp._build_help_panel_html(
        df, [(f"_grp_{d['col']}", d["label"]) for d in cond_dims],
        pod_filter_expression=cfg.get("pod_filter_expression", ""),
    )
    has_segments = _pp._has_segmentable_spec(df)

    all_freqs = sorted(set(f for cd in env_data for f in cd["freqs"] if f is not None))
    freq_min = float(min(all_freqs)) if all_freqs else 0.0
    freq_max = float(max(all_freqs)) if all_freqs else 1.0

    log_x_cfg = cfg.get("log_x")
    log_x = bool(log_x_cfg) if log_x_cfg is not None else (freq_min > 0 and freq_max / max(freq_min, 1e-9) >= 100)

    title = cfg.get("title", output_html.stem)

    # Cross-site compare: detect a "Site" tag in the Group text; when >=2 sites and
    # a primary_site, enable the Site Population Check (SR-fence membership over
    # Room baseline OR ΔEnv drift, selectable in the panel).
    _ec_site_vals = set()
    if "Group" in df.columns:
        for _g in df["Group"].dropna().astype(str):
            _m = re.search(r"Site:\s*([^|]+?)(?:\s{2,}|$)", _g)
            if _m:
                _ec_site_vals.add(_m.group(1).strip())
    _ec_site_vals = sorted(_ec_site_vals)
    ec_primary_site = cfg.get("primary_site") or (_ec_site_vals[0] if len(_ec_site_vals) > 1 else None)
    ec_site_enabled = len(_ec_site_vals) > 1 and ec_primary_site in _ec_site_vals
    ec_site_btn_html = ""
    if ec_site_enabled:
        _ps = str(ec_primary_site).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ec_site_btn_html = (
            '<div style="margin:6px 2px">\n'
            '  <button class="reset-btn" id="ec_site_btn" onclick="toggleEcSitePanel()">&#9654; Site Population Check</button>\n'
            '  <label style="font-size:11px;color:#555">&nbsp;Fence basis:'
            ' <label><input type="radio" name="ec_site_basis" value="room" checked onchange="updateEcSitePanel()">&nbsp;Room baseline</label>'
            ' <label><input type="radio" name="ec_site_basis" value="delta" onchange="updateEcSitePanel()">&nbsp;&Delta;Env drift</label></label>\n'
            '  <label style="font-size:11px;color:#555" title="Tukey fence multiplier: fence = Q1 - k*IQR .. Q3 + k*IQR. Lower k = stricter.">'
            '&nbsp;k&times;IQR: <input type="number" id="ec_site_k" value="1.5" min="0" step="0.1" style="width:52px" onchange="updateEcSitePanel()"></label>\n'
            '  <label style="font-size:11px;color:#555" title="How to judge each non-primary DUT:'
            ' against the primary site fence (site-population shifts), the datasheet Spec/Limit (real'
            ' pass/fail; Room baseline only), or both.">&nbsp;Site check vs:'
            ' <select id="ec_site_cmp" onchange="updateEcSitePanel()">'
            f'<option value="fence">{_ps} fence</option>'
            '<option value="spec">Spec/Limit</option><option value="both">Both</option></select></label>\n'
            f'  <span style="color:#888;font-size:11px">(each non-{_ps} DUT vs the {_ps} k&times;IQR fence)</span>\n'
            '</div>\n'
        )

    palette = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
    ]

    html = _pp._build_env_coverage_html(
        env_data=env_data,
        cond_dims=cond_dims,
        title=title,
        # ΔEnv is always the right label for this view's actual data, so the
        # default is unchanged for every existing pod; the env_coverage_y_label
        # job.json key exists only for a pod whose delta axis needs a different
        # unit/wording (documented but previously unwired -- CLAUDE.md).
        y_label=cfg.get("env_coverage_y_label", "ΔEnv (dB)"),
        y_lim=None,          # auto-scale; delta values are much smaller than absolute power range
        log_x=log_x,
        freq_min=freq_min,
        freq_max=freq_max,
        palette=palette,
        freq_vals=all_freqs,
        k_table=k_table,
        non_room_temps=non_room_temps,
        all_serials=all_serials,
        all_ports=all_ports,
        default_P=cfg.get("proportion", 0.90),
        default_C=cfg.get("confidence", 0.90),
        results_dir=cfg.get("results_dir", ""),
        x_label=cfg.get("x_label", "Frequency (MHz)"),
        x_unit=cfg.get("x_unit", "MHz"),
        help_panel_html=help_panel_html,
        has_segments=has_segments,
        primary_site=ec_primary_site if ec_site_enabled else None,
        site_btn_html=ec_site_btn_html,
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


def render_summary(
    df: pd.DataFrame,
    cfg: dict,
    output_html: Path,
) -> None:
    """
    Group-by-frequency summary with TLL band — all temperatures combined.
    Computes mean/min/max/NP-TI per (condition × frequency) from raw scatter data.
    """
    import padb_stats as pst
    import padb_plots as _pp

    proportion = cfg.get("proportion", 0.90)
    confidence = cfg.get("confidence", 0.90)
    # Categorical box-identity label per frequency (same _freq_label_map the
    # boxplot uses, from the WHOLE df) so a GF key the boxplot stores matches
    # point-precisely in this view's per-freq GF recompute.
    _sum_flmap = _pp._freq_label_map(sorted(df["Frequency_MHz"].dropna().unique()), cfg.get("x_unit", "MHz"))

    _path_pat   = re.compile(r'^(rf|path|port|ch|channel)\s*\d*$', re.IGNORECASE)
    _serial_pat = re.compile(r'^[A-Z]{2,4}\d{4,}$')
    _serial_kws = ("serial", "unit id", "dut id", "s/n")
    _temp_kws   = ("temp", "temperature", "deg c", "deg f")

    all_grp = [c for c in df.columns if c.startswith("_grp_") and df[c].nunique(dropna=True) >= 2]

    def _is_exclude(col: str) -> bool:
        name = col.removeprefix("_grp_").lower()
        if any(kw in name for kw in _serial_kws + _temp_kws):
            return True
        vals = df[col].dropna().unique()
        if not len(vals):
            return False
        if all(_path_pat.match(str(v)) for v in vals):
            return True
        if all(_serial_pat.match(str(v)) for v in vals):
            return True
        return False

    cond_cols = [c for c in all_grp if not _is_exclude(c)]

    # Include port/path-labelled columns as conditions (RF1 vs RF2 etc. are meaningful)
    for col in all_grp:
        if col in cond_cols:
            continue
        vals = df[col].dropna().unique()
        if 1 < len(vals) <= 20 and all(_path_pat.match(str(v)) for v in vals):
            cond_cols.append(col)

    def _cond_label(row: pd.Series) -> str:
        parts = [f"{c.removeprefix('_grp_')}: {row[c]}" for c in cond_cols if pd.notna(row[c])]
        return "  ".join(parts) if parts else "All"

    df = df.copy()
    df["_cond"] = df.apply(_cond_label, axis=1)

    # Build cond_dims for filter panels
    cond_dims = []
    for col in cond_cols:
        label  = col.removeprefix("_grp_")
        col_id = re.sub(r"\W+", "_", label)
        vals   = sorted(str(v) for v in df[col].dropna().unique() if str(v).strip())
        if len(vals) > 1:
            cond_dims.append({"col": label, "col_id": col_id, "label": label, "vals": vals})

    help_panel_html = _pp._build_help_panel_html(
        df, [(f"_grp_{d['col']}", d["label"]) for d in cond_dims],
        pod_filter_expression=cfg.get("pod_filter_expression", ""),
    )

    # Identify serial column so per-DUT means can be embedded for GF support
    _ser_col = next(
        (c for c in all_grp
         if any(kw in c.removeprefix("_grp_").lower() for kw in _serial_kws)),
        None
    )
    # Fall back to the already-standardized plain "Serial" column when the serial
    # isn't embedded in Group text at all (e.g. a real UHP amplifier pod where
    # Serial Number is a separate CSV column, not a Group dimension).
    if _ser_col is None and "Serial" in df.columns and df["Serial"].nunique(dropna=True) >= 1:
        _ser_col = "Serial"

    hi_spec_global = float("nan")
    lo_spec_global = float("nan")
    records: list[dict] = []

    for cond, cdf in df.groupby("_cond", sort=True):
        all_freqs = sorted(cdf["Frequency_MHz"].dropna().unique())

        # Spec per freq (modal Upper/Lower_Limit)
        spec_hi_list, spec_lo_list = [], []
        for freq in all_freqs:
            fd = cdf[cdf["Frequency_MHz"] == freq]
            hi_v = fd["Upper_Limit"].dropna()
            lo_v = fd["Lower_Limit"].dropna()
            spec_hi_list.append(float(hi_v.mode().iloc[0]) if len(hi_v) else None)
            spec_lo_list.append(float(lo_v.mode().iloc[0]) if len(lo_v) else None)

        g_hi = next((v for v in spec_hi_list if v is not None), None)
        g_lo = next((v for v in spec_lo_list if v is not None), None)

        means, mins, maxs, uttl_list, lttl_list = [], [], [], [], []
        for freq in all_freqs:
            vals = cdf[cdf["Frequency_MHz"] == freq]["Value"].dropna().values
            if len(vals) == 0:
                for lst in (means, mins, maxs, uttl_list, lttl_list):
                    lst.append(None)
                continue
            means.append(round(float(np.mean(vals)), 6))
            mins.append(round(float(np.min(vals)), 6))
            maxs.append(round(float(np.max(vals)), 6))
            if len(vals) >= 3:
                tl, th, _ = pst.nonparam_tolerance_interval(vals, proportion, confidence)
                uttl_list.append(round(float(th), 6) if th is not None else maxs[-1])
                lttl_list.append(round(float(tl), 6) if tl is not None else mins[-1])
            else:
                uttl_list.append(maxs[-1])
                lttl_list.append(mins[-1])

        # Per-temperature breakdown for JS temperature filter and stat controls
        temps_in_cond = sorted(str(t) for t in cdf["Temperature"].dropna().unique())
        by_temp: dict = {}
        for temp in temps_in_cond:
            tdf = cdf[cdf["Temperature"] == temp]
            t_ns, t_means_t, t_stds, t_mins_t, t_maxs_t = [], [], [], [], []
            for freq in all_freqs:
                vals_t = tdf[tdf["Frequency_MHz"] == freq]["Value"].dropna().values
                if len(vals_t) == 0:
                    t_ns.append(0); t_means_t.append(None); t_stds.append(None)
                    t_mins_t.append(None); t_maxs_t.append(None)
                    continue
                t_ns.append(int(len(vals_t)))
                t_means_t.append(round(float(np.mean(vals_t)), 6))
                t_stds.append(round(float(np.std(vals_t, ddof=1)) if len(vals_t) > 1 else 0.0, 6))
                t_mins_t.append(round(float(np.min(vals_t)), 6))
                t_maxs_t.append(round(float(np.max(vals_t)), 6))
            by_temp[temp] = {
                "n": t_ns, "mean": t_means_t, "std": t_stds,
                "min_data": t_mins_t, "max_data": t_maxs_t,
            }

        # Per-DUT means per frequency — embedded so JS can recompute aggregates
        # when the global filter is active (GF excludes specific DUTs).
        dut_info: list[dict] = []
        dut_vals: list[list] = []  # [freq_idx][dut_idx] = mean across all temps
        if _ser_col:
            dut_serials = sorted(str(s) for s in cdf[_ser_col].dropna().unique())
            dut_info = [{"s": s} for s in dut_serials]
            # Single groupby instead of nested per-freq per-DUT filter loops
            _sfm = (
                cdf[["Frequency_MHz", _ser_col, "Value"]]
                .dropna(subset=["Value"])
                .groupby(["Frequency_MHz", _ser_col], sort=False)["Value"]
                .mean()
                .unstack(level=_ser_col)
                .reindex(index=all_freqs, columns=dut_serials)
            )
            for freq in all_freqs:
                row_s = _sfm.loc[freq]
                dut_vals.append([
                    round(float(v), 4) if pd.notna(v) else None
                    for v in row_s
                ])

        # Per-DUT Limit/Spec/Uncertainty per frequency, same [freq_idx][dut_idx]
        # shape as dut_vals, so client-side segment detection can respect GF's
        # per-DUT exclusion -- excluding a DUT must also drop its own
        # spec/limit/uncertainty contribution at that frequency, not just its
        # measured value. Aggregated with min/max (tightest-wins), not mean --
        # unlike the measured Value, these should be constant per (freq, DUT);
        # mean would silently average together a genuine data conflict (e.g. a
        # datapak error recording two different spec values for the same
        # DUT/frequency) into a meaningless number.
        dut_spec_vals: dict[str, list] = {}
        if _ser_col:
            for _col in ("Upper_Limit", "Lower_Limit", "Spec_Hi", "Spec_Lo", "Unc_Hi", "Unc_Lo"):
                if _col not in cdf.columns:
                    continue
                _agg = "min" if _col in ("Upper_Limit", "Spec_Hi", "Unc_Hi") else "max"
                _sfm_spec = (
                    cdf[["Frequency_MHz", _ser_col, _col]]
                    .dropna(subset=[_col])
                    .groupby(["Frequency_MHz", _ser_col], sort=False)[_col]
                    .agg(_agg)
                    .unstack(level=_ser_col)
                    .reindex(index=all_freqs, columns=dut_serials)
                )
                dut_spec_vals[_col.lower()] = [
                    [round(float(v), 6) if pd.notna(v) else None for v in _sfm_spec.loc[freq]]
                    for freq in all_freqs
                ]

        # cond_keys: label -> value for each condition dimension
        cond_keys_dict = {}
        for col in cond_cols:
            label = col.removeprefix("_grp_")
            unique_in_cond = cdf[col].dropna().unique()
            cond_keys_dict[label] = str(unique_in_cond[0]) if len(unique_in_cond) == 1 else ""

        records.append({
            "condition":        cond,
            "cond_keys":        cond_keys_dict,
            "freqs":            [round(float(f), 6) for f in all_freqs],
            "freq_labels":      [_sum_flmap.get(float(f), str(f)) for f in all_freqs],
            "mean":             means,
            "min_data":         mins,
            "max_data":         maxs,
            "uttl":             uttl_list,
            "lttl":             lttl_list,
            "uttl_is_estimate": False,
            "spec_hi":          g_hi,
            "spec_lo":          g_lo,
            "spec_hi_list":     spec_hi_list,
            "spec_lo_list":     spec_lo_list,
            "by_temp":          by_temp,
            "temps":            temps_in_cond,
            "dut_info":         dut_info,
            "dut_vals":         dut_vals,
            "dut_spec_vals":    dut_spec_vals,
        })

        if np.isnan(hi_spec_global) and g_hi is not None:
            hi_spec_global = g_hi
        if np.isnan(lo_spec_global) and g_lo is not None:
            lo_spec_global = g_lo

    temps_all = sorted(set(t for r in records for t in r.get("temps", [])))
    freq_vals = sorted(float(f) for f in df["Frequency_MHz"].dropna().unique())
    freq_min  = freq_vals[0] if freq_vals else 0.0
    freq_max  = freq_vals[-1] if freq_vals else 1.0
    has_segments = _pp._has_segmentable_spec(df)

    # Cross-site comparison (compare_csv tags each row's Group text "Site: <name>"
    # before this function ever sees it -- see _build_compare_csv). "Site" already
    # flows through as a normal cond_cols/cond_keys dimension (same free-ride as
    # every other view), so this only needs to compute the coverage-gap banner --
    # the Site Population Check panel itself is built entirely client-side from
    # DATA/cond_keys, same as boxplot/stat_summary.
    all_sites = sorted({r["cond_keys"].get("Site") for r in records if r["cond_keys"].get("Site")})
    primary_site = cfg.get("primary_site")
    site_compare_enabled = bool(primary_site) and primary_site in all_sites and len(all_sites) > 1
    coverage_gap_html = ""
    if site_compare_enabled:
        def _norm_val(v):
            # Same rationale as boxplot's/stat_summary's identical helper:
            # different sites' own PADB extractions can format the same
            # numeric value with different trailing precision -- compare
            # numerically when possible so that never shows up as a false gap.
            try:
                return round(float(v), 6)
            except (TypeError, ValueError):
                return v

        def _site_coverage_gaps(dim_label: str, series) -> list:
            tmp = pd.DataFrame({"_site": df["_grp_Site"], "_v": series})
            tmp["_norm"] = tmp["_v"].map(lambda v: _norm_val(v) if pd.notna(v) else None)
            by_site = tmp.groupby("_site")["_norm"].apply(lambda s: set(s.dropna().unique()))
            all_vals = set().union(*by_site.tolist()) if len(by_site) else set()
            display = {}
            for norm, raw in zip(tmp["_norm"], tmp["_v"]):
                if norm is not None and norm not in display:
                    display[norm] = raw
            def _sort_key(v):
                return (0, v) if isinstance(v, (int, float)) else (1, str(v))
            lines = []
            for site in all_sites:
                missing = sorted((all_vals - by_site.get(site, set())), key=_sort_key)
                if missing:
                    shown = ", ".join(str(display.get(v, v)) for v in missing)
                    lines.append(f"<b>{site}</b> has no {dim_label} data for: {shown}")
            return lines

        _gap_lines = []
        _gap_lines += _site_coverage_gaps("Temperature", df["Temperature"])
        for _col in cond_cols:
            _label = _col.removeprefix("_grp_")
            if _label == "Site":
                continue
            _gap_lines += _site_coverage_gaps(_label, df[_col])
        if _gap_lines:
            coverage_gap_html = (
                '<div style="background:#fff8e1;border:1px solid #e0c05a;border-radius:4px;'
                'padding:6px 12px;margin:4px 0;font-size:12px;color:#6b5a00">'
                '<b>Coverage gap across sites:</b> ' + ' &nbsp;|&nbsp; '.join(_gap_lines) + '</div>'
            )

    _pp._build_summary_html(
        records, cond_dims, cfg, output_html,
        hi_spec=hi_spec_global, lo_spec=lo_spec_global,
        freq_min=freq_min, freq_max=freq_max, freq_vals=freq_vals,
        temps_all=temps_all,
        help_panel_html=help_panel_html,
        primary_site=primary_site if site_compare_enabled else None,
        coverage_gap_html=coverage_gap_html,
        has_segments=has_segments,
    )


# ===========================================================================
# 4.  Temp-CSV helpers (bridge V1.0 functions that take a csv_path)
# ===========================================================================

def _df_to_scatter_csv(df: pd.DataFrame, out_path: Path) -> None:
    """
    Write a normalised DataFrame back to a PADB-compatible scatter CSV so
    V1.0 functions that still take a csv_path can consume it.

    Column mapping matches what _load_scatter_for_stats expects.
    """
    out = pd.DataFrame()
    out["Frequency (MHz)"] = df["Frequency_MHz"]
    out["Value"]           = df["Value"]
    out["Serial"]          = df.get("Serial", "")
    out["Station"]         = df.get("Station", "")
    out["Group"]           = df.get("Group", "")
    out["Test Step"]       = df.get("Temperature", "Room")
    if "Upper_Limit" in df.columns:
        out["Upper Limit (<=)"] = df["Upper_Limit"]
    if "Lower_Limit" in df.columns:
        out["Lower Limit (>=)"] = df["Lower_Limit"]
    if "_val_col_name" in df.columns:
        name = df["_val_col_name"].iloc[0]
        out.rename(columns={"Value": name}, inplace=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)


# ===========================================================================
# 5.  Report orchestrator
# ===========================================================================

def render_reference_stats(
    df: pd.DataFrame,
    cfg: dict,
    output_html: Path,
) -> None:
    """Filter-coupled descriptive-statistics "1000 ft view": overall + pass/fail,
    Pareto by group, per-group descriptive stats. Aggregates over the FULL data
    (never decimated), so counts are accurate on large datasets."""
    if df.empty:
        _write_placeholder(output_html, cfg.get("title", output_html.stem), "No data rows found.")
        return
    import padb_refstats
    html = padb_refstats._build_reference_stats_html(df, cfg, cfg.get("title", output_html.stem))
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


_VIEW_FN = {
    "scatter":      render_scatter,
    "stat_summary": render_stat_summary,
    "boxplot":      render_boxplot,
    "distribution": render_distribution,
    "env_coverage": render_env_coverage,
    "summary":      render_summary,
    "reference":    render_reference_stats,
}

_VIEW_LABELS = {
    "scatter":      "Scatter (All Temps)",
    "stat_summary": "Statistical Summary (Room)",
    "boxplot":      "Box Plots",
    "distribution": "Distribution (Delta-Env)",
    "env_coverage": "Environmental Coverage",
    "summary":      "Summary (All Temps)",
    "reference":    "Reference Statistics",
    "histogram":    "Histogram",
}


def _interp_mask_fill(df: pd.DataFrame, col: str, use_log: bool,
                      method: str = "linear") -> int:
    """Fill remaining NaN `col` by interpolating the frequency-varying spec MASK
    between its defined breakpoints -- a "complex limit line" (phase-noise /
    broadband-noise masks are specified at a handful of offset breakpoints and
    interpolated between them, in log-frequency).

    method="linear" (default): piecewise-LINEAR between breakpoints (np.interp).
    method="pchip": monotone piecewise-cubic Hermite (shape-preserving) -- a
    SMOOTH mask that, unlike a plain cubic spline, provably never overshoots
    between breakpoints (no false limit dips that would misclassify passing
    points). Falls back to linear if SciPy is unavailable or there are <2
    breakpoints. Chosen over cubic spline deliberately: on the real 2.4G
    broadband-noise mask, a plain cubic spline overshot below ~893 passing points
    while PCHIP did not; PCHIP fit the recorded pass/fail status as well as linear
    with no overshoot risk.

    Opt-in only (spec_interp="linear"/"pchip"): most pods have either a constant
    spec or a STEP/staircase mask (spurs), where interpolation would be WRONG --
    those keep the default modal fill. Interpolates from the union of known
    breakpoints (one median value per breakpoint frequency); clamps at the mask
    ends (a held-constant limit past the first/last breakpoint, for both methods)."""
    import numpy as _np
    f = "Frequency_MHz"
    if col not in df.columns or f not in df.columns:
        return 0
    known = df.dropna(subset=[col])
    if known.empty:
        return 0
    bp = known.groupby(f)[col].median().sort_index()   # one value per breakpoint freq
    xf = bp.index.to_numpy(dtype=float)
    yv = bp.to_numpy(dtype=float)
    nullmask = df[col].isna()
    if not nullmask.any() or len(xf) < 1:
        return 0
    tf = df.loc[nullmask, f].to_numpy(dtype=float)
    if len(xf) < 2:
        vals = _np.full(len(tf), float(yv[0]))          # single breakpoint -> constant
    else:
        _log = use_log and bool((xf > 0).all()) and bool((tf > 0).all())
        xi = _np.log10(xf) if _log else xf
        ti = _np.log10(tf) if _log else tf
        if method == "pchip":
            try:
                from scipy.interpolate import PchipInterpolator as _Pchip
                pc = _Pchip(xi, yv)
                # Clamp targets to the breakpoint range so the ends hold constant
                # (match np.interp's clamping); PCHIP itself would extrapolate.
                vals = pc(_np.clip(ti, xi[0], xi[-1]))
            except Exception:
                vals = _np.interp(ti, xi, yv)            # SciPy missing -> linear
        else:
            vals = _np.interp(ti, xi, yv)                # linear; np.interp clamps at ends
    df.loc[df.index[nullmask.to_numpy()], col] = vals
    return int(nullmask.sum())


def _fill_spec_nulls(df: pd.DataFrame, spec_interp: str = "none") -> pd.DataFrame:
    """
    Fill NaN Upper_Limit / Lower_Limit using modal spec for matching condition × frequency.

    spec_interp="linear" (or "pchip" for a smooth, non-overshooting monotone mask)
    adds a final pass that interpolates a frequency-varying mask between its
    breakpoints in log-frequency -- see _interp_mask_fill. Default "none" preserves
    the modal-only behavior (safe for step/constant specs).

    Uses two passes to handle cases where some sub-groups (e.g. Port RF2) are entirely
    null and cannot self-fill:
      Pass 1 — fine grouping: all _grp_* columns + Frequency_MHz
      Pass 2 — coarser grouping: _grp_* columns minus path-selector columns + Frequency_MHz

    Path-selector columns are those whose values all match patterns like RF1, RF2, Port1, etc.
    """
    if "Upper_Limit" not in df.columns and "Lower_Limit" not in df.columns:
        return df

    _path_pat   = re.compile(r'^(rf|path|port|ch|channel)\s*\d*$', re.IGNORECASE)
    _serial_pat = re.compile(r'^[A-Z]{2,4}\d{4,}$')
    _serial_kws = ("serial", "unit id", "dut id", "s/n")

    all_grp = [c for c in df.columns if c.startswith("_grp_") and df[c].nunique(dropna=True) >= 1]

    def _is_exclude_col(col: str) -> bool:
        name_lower = col.removeprefix("_grp_").lower()
        if any(kw in name_lower for kw in _serial_kws):
            return True
        vals = df[col].dropna().unique()
        if not len(vals):
            return False
        if all(_path_pat.match(str(v)) for v in vals):
            return True
        if all(_serial_pat.match(str(v)) for v in vals):
            return True
        return False

    exclude_cols = {c for c in all_grp if _is_exclude_col(c)}
    coarse_grp = [c for c in all_grp if c not in exclude_cols] + ["Frequency_MHz"]
    fine_grp   = all_grp + ["Frequency_MHz"]

    def _mode_fill(series: "pd.Series") -> "pd.Series":
        known = series.dropna()
        return series.fillna(known.mode().iloc[0]) if not known.empty else series

    df = df.copy()
    for col in ("Upper_Limit", "Lower_Limit"):
        if col not in df.columns or not df[col].isna().any():
            continue
        total_null = int(df[col].isna().sum())

        # If no non-null values exist at all, there is nothing to fill from — skip silently.
        if df[col].notna().sum() == 0:
            continue

        # Pass 1: fine grouping
        df[col] = df.groupby(fine_grp, group_keys=False)[col].transform(_mode_fill)
        after_p1 = int(df[col].isna().sum())

        # Pass 2: coarser grouping (drop path-selector dims) for still-null rows
        if df[col].isna().any() and coarse_grp != fine_grp:
            df[col] = df.groupby(coarse_grp, group_keys=False)[col].transform(_mode_fill)
        after_p2 = int(df[col].isna().sum())

        n_filled = total_null - after_p2
        if n_filled > 0:
            print(f"    Filled {n_filled:,} null {col} values from per-condition modal spec"
                  + (f" ({exclude_cols} excluded from pass-2 grouping)" if exclude_cols else ""),
                  flush=True)

        # Pass 3 (opt-in): interpolate a frequency-varying mask between breakpoints.
        # Modal fill can only fill a frequency that HAS a limit somewhere; a mask
        # defined at sparse breakpoints leaves every gap-frequency null, which then
        # renders as a flat fallback line. Linear (log-freq) interpolation draws the
        # true complex limit line and drives pass/fail correctly.
        if spec_interp in ("linear", "pchip") and df[col].isna().any() and df[col].notna().sum() >= 1:
            n_interp = _interp_mask_fill(df, col, use_log=True, method=spec_interp)
            if n_interp > 0:
                _how = "monotone PCHIP" if spec_interp == "pchip" else "linear"
                print(f"    Interpolated {n_interp:,} null {col} values across the mask "
                      f"breakpoints ({_how} in log-frequency)", flush=True)
            after_p2 = int(df[col].isna().sum())

        if after_p2 > 0:
            print(f"    WARNING: {after_p2:,} null {col} values remain unfilled "
                  f"(these rows will be hidden in pass-only mode)", flush=True)
    return df


def _finish_report(output_dir, prefix, generated, cfg, is_room_only=False):
    """Write the gallery index and publish (or opt out per cfg), then return the
    generated-file list. Shared by the normal multi-view path and the histogram
    branch so both handle index + publish identically."""
    _write_index(output_dir, prefix, generated, cfg, is_room_only=is_room_only)
    if "publish_to" in cfg:
        if cfg["publish_to"]:
            _publish(output_dir, Path(cfg["publish_to"]))
    else:
        publish_root = COMPARE_PUBLISH_ROOT if cfg.get("compare_csv") else DEFAULT_PUBLISH_ROOT
        _publish(output_dir, Path(publish_root) / output_dir.name)
    return generated


def generate_report(
    csv_path: Path,
    cfg: dict,
    output_dir: Path,
) -> list[Path]:
    """
    Load the scatter CSV once, then render each requested view.

    Parameters
    ----------
    csv_path:   Path to the PADB Scatter (Type=80) CSV.
    cfg:        Job configuration dict (see module docstring for keys).
    output_dir: Directory where HTML files are written.

    Returns
    -------
    List of paths to generated HTML files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = cfg.get("title_prefix", csv_path.stem)

    # No-swept-x value-distribution view (e.g. switching speed: one number per
    # event, spec'd against a limit -- the pod's original analytics are Type=70
    # histograms). Routed here BEFORE load_scatter, which requires a numeric
    # Frequency/X axis this data doesn't have; padb_plots.histogram() reads the
    # CSV's measurement column directly.
    if cfg.get("views") == ["histogram"]:
        out_html = output_dir / (re.sub(r"[^\w]+", "_", prefix) + "_histogram.html")
        title = prefix + " - Histogram"
        print(f"  Rendering Histogram -> {out_html.name}", flush=True)
        try:
            _pp.histogram(csv_path, {**cfg, "title": title}, out_html)
            generated = [out_html]
        except Exception as exc:
            print(f"    [ERROR] {exc}", flush=True)
            _write_placeholder(out_html, title, f"Error: {exc}")
            generated = []
        _maybe_export_parquet(cfg, csv_path, output_dir)
        if cfg.get("build_pdf_report", False) and generated:
            _maybe_build_pdf_report(None, cfg, prefix, csv_path, output_dir,
                                    [("histogram", out_html)], lambda v: "Histogram")
        return _finish_report(output_dir, prefix, generated, cfg)

    print(f"  Loading scatter CSV: {csv_path.name}", flush=True)
    df = load_scatter(csv_path, cfg)
    if df.empty:
        msg = (
            f"No usable rows loaded from {csv_path.name} -- the x-axis/value column "
            f"auto-detection likely picked the wrong columns (see the [WARN] above). "
            f'Set "x_col" in job.json to the exact x-axis column name and re-run.'
        )
        print(f"  [ERROR] {msg}", flush=True)
        _write_placeholder(output_dir / "index.html", cfg.get("title_prefix", csv_path.stem), msg)
        return []
    df = _fill_spec_nulls(df, cfg.get("spec_interp", "none"))
    print(f"    Rows: {len(df):,}  |  Temps: {sorted(df['Temperature'].unique())}",
          flush=True)
    _maybe_auto_binary_encode(cfg, csv_path, df)
    _maybe_export_parquet(cfg, csv_path, output_dir, df)

    # Load alternate env_coverage CSV if specified
    ec_csv_raw = cfg.get("env_coverage_csv", "")
    df_ec: pd.DataFrame | None = None
    cfg_ec: dict | None = None
    if ec_csv_raw:
        ec_csv_path = Path(ec_csv_raw)
        if not ec_csv_path.is_absolute():
            ec_csv_path = csv_path.parent / ec_csv_raw
        ec_cfg_overrides: dict[str, Any] = {}
        if cfg.get("env_coverage_y_label"):
            ec_cfg_overrides["y_label"] = cfg["env_coverage_y_label"]
        if cfg.get("env_coverage_y_lim"):
            ec_cfg_overrides["y_lim"] = cfg["env_coverage_y_lim"]
        if cfg.get("env_coverage_freq_scale"):
            ec_cfg_overrides["freq_scale"] = cfg["env_coverage_freq_scale"]
        cfg_ec = _cfg_for_view(cfg, ec_cfg_overrides)
        print(f"  Loading env_coverage CSV: {ec_csv_path.name}", flush=True)
        df_ec = load_scatter(ec_csv_path, cfg_ec)
        df_ec = _fill_spec_nulls(df_ec, cfg.get("spec_interp", "none"))
        print(f"    Rows: {len(df_ec):,}  |  Temps: {sorted(df_ec['Temperature'].unique())}",
              flush=True)

    room_values = set(cfg.get("room_values", ["Room"]))
    is_room_only = set(df["Temperature"].dropna().unique()) <= room_values

    if "views" in cfg:
        views = cfg["views"]
    else:
        if is_room_only:
            # summary + stat_summary are useful for Room-only data too (per-condition
            # stats/TI vs spec -- no temperature deltas needed), so they're DEFAULTS now
            # (2026-09-15, David: "when there is no env data it is still useful to have a
            # summary plot for the room data"). env_coverage/distribution stay OFF -- they
            # compute deltas against Room and need non-Room data. room_only_full_views is
            # retained as an accepted no-op (these views are default now, not opt-in).
            views = ["scatter", "boxplot", "reference", "summary", "stat_summary"]
            print(f"    Room-only data detected -> default views: {views}", flush=True)
        else:
            views = list(_VIEW_FN.keys())
    generated: list[Path] = []
    gen_pairs: list[tuple[str, Path]] = []

    def _view_label(view: str) -> str:
        # "Scatter"/"Summary" default to "(All Temps)" -- correct for the common
        # multi-temp case, but actively wrong when the data is genuinely Room-only
        # (e.g. via room_only_full_views). stat_summary is always "(Room)" by
        # design regardless of what other temps exist, so it's not affected.
        if is_room_only and view in ("scatter", "summary"):
            return _VIEW_LABELS[view].replace("(All Temps)", "(Room)")
        return _VIEW_LABELS[view]

    for view in views:
        fn = _VIEW_FN.get(view)
        if fn is None:
            print(f"  [SKIP] Unknown view '{view}'", flush=True)
            continue

        slug = re.sub(r"[^\w]+", "_", view)
        html_name = re.sub(r"[^\w]+", "_", prefix) + "_" + slug + ".html"
        out_html = output_dir / html_name

        if view == "env_coverage" and df_ec is not None:
            view_cfg = _cfg_for_view(cfg_ec, {"title": f"{prefix} — {_view_label(view)}"})
            use_df = df_ec
        else:
            view_cfg = _cfg_for_view(cfg, {"title": f"{prefix} — {_view_label(view)}"})
            use_df = df

        print(f"  Rendering {_view_label(view)} -> {html_name}", flush=True)
        try:
            fn(use_df, view_cfg, out_html)
            generated.append(out_html)
            gen_pairs.append((view, out_html))
            _warn_if_view_too_large(out_html, view, cfg, output_dir)
        except Exception as exc:
            print(f"    [ERROR] {exc}", flush=True)
            _write_placeholder(out_html, view_cfg["title"], f"Error: {exc}")

    if cfg.get("build_pdf_report", False) and gen_pairs:
        _maybe_build_pdf_report(df, cfg, prefix, csv_path, output_dir, gen_pairs, _view_label)

    return _finish_report(output_dir, prefix, generated, cfg, is_room_only=is_room_only)


def _maybe_build_pdf_report(df, cfg, prefix, csv_path, output_dir, gen_pairs, view_label_fn):
    """Build the opt-in comprehensive multi-view PDF (headless-Chromium print).

    Fully guarded: any failure (module missing, browser not installed, a bad view)
    logs a NOTE and returns without disturbing the surrounding build.
    """
    try:
        import padb_pdf_report
    except Exception as exc:
        _log_note(output_dir, f"build-time PDF report skipped -- padb_pdf_report unavailable ({exc}).")
        return
    try:
        # Dataset summary for the cover page (all best-effort). df is None for a
        # histogram-only job (that branch never loads the scatter df).
        rows = n_duts = n_conds = temps = None
        spec = None
        if df is not None:
            rows = int(len(df))
            try:
                n_duts = int(df["Serial"].replace("", pd.NA).nunique(dropna=True)) if "Serial" in df.columns else None
            except Exception:
                n_duts = None
            try:
                grp_cols = [c for c in df.columns if c.startswith("_grp_") and df[c].nunique(dropna=True) >= 2]
                n_conds = int(df.groupby(grp_cols).ngroups) if grp_cols else 1
            except Exception:
                n_conds = None
            try:
                temps = ", ".join(sorted(str(t) for t in df["Temperature"].dropna().unique()))
            except Exception:
                temps = None
            hi = df["Upper_Limit"].notna().any() if "Upper_Limit" in df.columns else False
            lo = df["Lower_Limit"].notna().any() if "Lower_Limit" in df.columns else False
            spec = {(True, True): "Upper + Lower", (True, False): "Upper only",
                    (False, True): "Lower only", (False, False): "none in CSV"}[(bool(hi), bool(lo))]

        meta = {
            "title": cfg.get("title", prefix),
            "csv_name": Path(csv_path).name,
            "rows": rows,
            "n_duts": n_duts,
            "n_conds": n_conds,
            "temps": temps,
            "spec": spec,
            "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "view_labels": {v: view_label_fn(v) for (v, _p) in gen_pairs},
        }
        out_pdf = output_dir / (re.sub(r"[^\w]+", "_", prefix) + "_report.pdf")
        apply_filter = bool(cfg.get("pdf_report_apply_filter", False))
        filter_site = cfg.get("pdf_report_filter_site", "primary")
        print(f"  Building comprehensive PDF report -> {out_pdf.name}"
              f"{' (filter-aware)' if apply_filter else ''}"
              f"{' [site: ' + filter_site + ']' if (apply_filter and filter_site != 'primary') else ''}",
              flush=True)
        padb_pdf_report.generate_multiview_pdf(gen_pairs, out_pdf, meta,
                                               apply_filter=apply_filter, filter_site=filter_site)
    except Exception as exc:
        _log_note(output_dir, f"build-time PDF report failed ({exc}).")


# ===========================================================================
# 6.  Index page
# ===========================================================================

_VIEW_ORDER = list(_VIEW_FN.keys())
# View suffixes recognized when grouping the index. "histogram" isn't in
# _VIEW_FN (it's routed via a separate branch, not the 6-view dispatch table),
# but its files are still named "<prefix>_histogram.html" -- so it must be
# stripped here too, or a histogram analytic's group key keeps the "_histogram"
# suffix and the report link (named "<prefix>_report.pdf") never matches it.
_INDEX_VIEW_SUFFIXES = _VIEW_ORDER + ["histogram"]


def _index_group_key(stem: str) -> tuple[str, str | None]:
    """Split a generated HTML filename's stem into (analytic_prefix, view) by
    stripping a trailing "_<view>" suffix -- the exact naming convention
    generate_report() uses (f"{prefix}_{slug}.html"). Returns (stem, None)
    for a file that doesn't end in any known view suffix (not written by
    generate_report(), e.g. a stray file someone dropped in the output dir),
    so it falls back to being its own single-item group in _write_index()."""
    for view in _INDEX_VIEW_SUFFIXES:
        suffix = "_" + view
        if stem.endswith(suffix) and len(stem) > len(suffix):
            return stem[: -len(suffix)], view
    return stem, None


def _write_index(output_dir: Path, prefix: str, html_files: list[Path], cfg: dict,
                  is_room_only: bool = False) -> None:
    # Merge newly generated files with any pre-existing HTML files in the directory
    # so multiple job runs into the same output dir all appear in the index.
    existing = sorted(
        p for p in output_dir.glob("*.html")
        if p.name != "index.html" and p not in html_files
    )
    all_files = sorted(set(existing) | set(html_files), key=lambda p: p.stem.lower())

    # Group by analytic (the prefix each view's own filename shares) rather
    # than one flat alphabetical list -- a multi-analytic pod's views for
    # different analytics otherwise interleave with nothing showing which
    # analytic a link belongs to. Only worth the extra structure once there's
    # more than one analytic in this output_dir; a single-analytic dir keeps
    # the plain flat list it always had.
    groups: dict[str, list[tuple[Path, str | None]]] = {}
    for p in all_files:
        group_key, view = _index_group_key(p.stem)
        groups.setdefault(group_key, []).append((p, view))

    # Comprehensive build-time PDF report(s), if any -- named
    # "<sanitized-analytic-prefix>_report.pdf" (see _maybe_build_pdf_report),
    # so a report's stem-without-"_report" equals that analytic's group_key.
    pdf_reports = {
        p.name[: -len("_report.pdf")]: p
        for p in sorted(output_dir.glob("*_report.pdf"))
    }

    def _pdf_link_for(group_key: str) -> str:
        p = pdf_reports.get(group_key)
        if not p:
            return ""
        return (f'<li><a class="pdf" href="{p.name}">&#128196; '
                f'Comprehensive PDF report</a></li>')

    if len(groups) > 1:
        def _view_rank(view: str | None) -> int:
            return _VIEW_ORDER.index(view) if view in _VIEW_ORDER else len(_VIEW_ORDER)

        def _index_label(p: Path, view: str | None) -> str:
            # Only correct the label for files from *this* run (html_files) --
            # for pre-existing entries from an earlier run against a different
            # analytic, we don't know that analytic's temp coverage without
            # reloading its CSV, so they keep whatever label they were written
            # with (same as before this fix).
            if view is None:
                return p.stem.replace("_", " ")
            if is_room_only and view in ("scatter", "summary") and p in html_files:
                return _VIEW_LABELS[view].replace("(All Temps)", "(Room)")
            return _VIEW_LABELS.get(view, p.stem.replace("_", " "))

        sections = []
        for group_key in sorted(groups, key=str.lower):
            entries = sorted(groups[group_key], key=lambda pv: _view_rank(pv[1]))
            items = "".join(
                f'<li><a href="{p.name}">{_index_label(p, view)}</a></li>'
                for p, view in entries
            )
            items += _pdf_link_for(group_key)
            sections.append(f'<h3>{group_key.replace("_", " ")}</h3>\n<ul>{items}</ul>')
        items_html = "\n".join(sections)
    else:
        items = "".join(
            f'<li><a href="{p.name}">{p.stem.replace("_", " ")}</a></li>'
            for p in all_files
        )
        # A single-analytic dir: append its report link(s) (usually one).
        items += "".join(_pdf_link_for(k) for k in sorted(pdf_reports))
        items_html = f"<ul>{items}</ul>"

    # Large-dataset parquet sidecar(s), if any -- not browser-openable HTML, so
    # link them with viewer guidance rather than as a plain page. Also drop a
    # one-click Open_in_viewer.bat in the folder so a user doesn't have to type the
    # padb_viewer.py command (David's request 2026-09-17): it prefers a co-located
    # PADB_Viewer.exe, else runs padb_viewer.py from the tool dir or the share.
    parquets = sorted(output_dir.glob("*.parquet"))
    parquet_html = ""
    if parquets:
        _tools_dir = str(Path(__file__).resolve().parent)
        _share_viewer = (r"\\srsnas01.srs.is.keysight.com\prod\MIDRF3\SG6311A"
                         r"\padb-tools\tools\padb_viewer.py")
        _bat = (
            "@echo off\r\n"
            "REM Open the PADB large-dataset viewer on the parquet(s) in this folder.\r\n"
            "REM Generated by padb_v2.py -- double-click to launch the viewer in your browser.\r\n"
            'cd /d "%~dp0"\r\n'
            'if exist "PADB_Viewer.exe" ( start "" "PADB_Viewer.exe" & goto :eof )\r\n'
            f'set "V1={_tools_dir}\\padb_viewer.py"\r\n'
            f'set "V2={_share_viewer}"\r\n'
            'if exist "%V1%" ( py "%V1%" "%CD%" & goto :eof )\r\n'
            'if exist "%V2%" ( py "%V2%" "%CD%" & goto :eof )\r\n'
            'echo Could not find PADB_Viewer.exe or padb_viewer.py -- see index.html '
            'for manual steps.\r\n'
            "pause\r\n"
        )
        try:
            (output_dir / "Open_in_viewer.bat").write_text(_bat, encoding="utf-8")
        except OSError:
            pass
        plis = "".join(
            f'<li><a href="{p.name}">&#128202; {p.name}</a> '
            f'({p.stat().st_size / 1e6:.1f} MB)</li>' for p in parquets)
        # Primary path: an "Open in viewer" button that asks the (local) web app to
        # launch the viewer server-side -- a browser can't run a .bat from a link (it
        # just shows/downloads the text), and a downloaded .bat would run against the
        # wrong folder anyway. Falls back to the folder's Open_in_viewer.bat when this
        # page is opened off the share (file://) rather than through the web app.
        _viewer_script = (
            "<script>\n"
            "function _pnqOpenViewer(){\n"
            "  var m=document.getElementById('pnq_viewer_msg');\n"
            "  var parts=location.pathname.split('/').filter(Boolean);\n"
            "  var token=(parts[0]==='results')?parts[1]:null;\n"
            "  if(location.protocol==='file:'||!token){ m.textContent="
            "'Open this folder and double-click Open_in_viewer.bat to launch the viewer.'; return; }\n"
            "  m.textContent='Launching viewer...';\n"
            "  fetch('/api/open-viewer',{method:'POST',headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({token:token})})\n"
            "    .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})\n"
            "    .then(function(x){ m.textContent = x.ok ? (x.d.msg||'Viewer launching in a new window.')"
            " : ('Could not launch: '+((x.d&&x.d.error)||'error')); })\n"
            "    .catch(function(){ m.textContent="
            "'Open this folder and double-click Open_in_viewer.bat to launch the viewer.'; });\n"
            "}\n"
            "function _pnqOpenFolder(){\n"
            "  var m=document.getElementById('pnq_viewer_msg');\n"
            "  var parts=location.pathname.split('/').filter(Boolean);\n"
            "  var token=(parts[0]==='results')?parts[1]:null;\n"
            "  if(location.protocol==='file:'||!token){ m.textContent="
            "'This folder is on disk beside the parquet file.'; return; }\n"
            "  fetch('/api/open-folder',{method:'POST',headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({token:token})})\n"
            "    .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})\n"
            "    .then(function(x){ m.textContent = x.ok ? (x.d.msg||'Opened the folder.')"
            " : ('Could not open folder: '+((x.d&&x.d.error)||'error')); })\n"
            "    .catch(function(){ m.textContent='Open the results folder on disk to find the files.'; });\n"
            "}\n</script>\n")
        # Collapsed by default (the viewer is optional). Auto-expand ONLY when a
        # published view page is genuinely huge (>= VIEW_SIZE_WARN_MB) -- i.e. the
        # self-contained HTML is actually hard to open, so the viewer is worth
        # surfacing up front. Otherwise it stays a one-line collapsible entry.
        _view_htmls = [p for p in output_dir.glob("*.html") if p.name != "index.html"]
        _max_html_mb = max((p.stat().st_size / 1e6 for p in _view_htmls), default=0.0)
        _auto_open = _max_html_mb >= VIEW_SIZE_WARN_MB
        _open_attr = " open" if _auto_open else ""
        _big_note = (f' &mdash; <span style="color:#b02a37;font-weight:600">large pages '
                     f'detected (~{_max_html_mb:.0f} MB); use the viewer</span>'
                     if _auto_open else
                     ' <span style="font-weight:400;color:#888">&mdash; only needed if a '
                     'page is too big to open</span>')
        parquet_html = (
            f'<details{_open_attr} style="margin:18px 0 4px">'
            f'<summary style="font-size:1.05em;color:#444;font-weight:bold;cursor:pointer">'
            f'Large-dataset viewer (optional){_big_note}</summary>'
            '<p style="font-size:.9em;color:#555"><b>You only need this if the interactive '
            'HTML plots above are too slow or too large to open comfortably.</b> The HTML '
            'plots have all the same analysis &mdash; the viewer just serves the data from a '
            'compact <b>parquet</b> sidecar and draws only the slice you’re looking at, so '
            'it stays fast on very large datasets. If the HTML plots open fine for you, you '
            'can ignore this.</p>'
            '<p><button type="button" onclick="_pnqOpenViewer()" style="font-size:14px;'
            'padding:4px 12px;cursor:pointer">&#9654; Open in viewer</button> '
            '<button type="button" onclick="_pnqOpenFolder()" style="font-size:14px;'
            'padding:4px 12px;cursor:pointer;margin-left:6px">&#128193; Open folder</button> '
            '<span id="pnq_viewer_msg" style="font-size:.85em;color:#555"></span></p>'
            '<p style="font-size:.85em;color:#555">The button works when this page is open '
            'through the web app (127.0.0.1:5000). Otherwise open <b>this folder</b> and '
            '<b>double-click Open_in_viewer.bat</b> (a browser can&rsquo;t run a .bat from a '
            'link), or run <code>py padb_viewer.py "&lt;this folder&gt;"</code>, or drop '
            '<code>PADB_Viewer.exe</code> here and double-click it.</p>'
            f'<ul>{plis}</ul>'
            + _viewer_script + '</details>')

    title = cfg.get("index_title", prefix)
    # Suppress per-job description when multiple jobs share the same output dir
    desc = cfg.get("description", "") if not existing else ""
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset='utf-8'><title>{title}</title>
<style>
  body{{font-family:sans-serif;max-width:800px;margin:40px auto;}}
  h1{{font-size:1.4em;}} h3{{font-size:1.05em;margin:18px 0 4px;color:#444;}}
  li{{margin:6px 0;}}
  a{{color:#1f77b4;text-decoration:none;}} a:hover{{text-decoration:underline;}}
  a.pdf{{color:#b02a37;font-weight:600;}}
</style>
</head>
<body>
<h1>{title}</h1>
{"<p>"+desc+"</p>" if desc else ""}
{items_html}
{parquet_html}
</body></html>"""
    (output_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"  Index: {output_dir / 'index.html'}", flush=True)


# ===========================================================================
# 7.  Publish
# ===========================================================================

def _publish(source_dir: Path, dest_dir: Path) -> None:
    """Copy all HTML files to publish destination."""
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for f in source_dir.glob("*.html"):
            shutil.copy2(f, dest_dir / f.name)
            copied += 1
        # Also publish any comprehensive PDF report(s) so the index link works
        # on the share, not just locally.
        for f in source_dir.glob("*_report.pdf"):
            shutil.copy2(f, dest_dir / f.name)
            copied += 1
        # Publish parquet sidecar(s) (tiny -- a giant CSV crushes to ~1 MB) so the
        # large-dataset viewer works off the share, and the viewer exe if the user
        # placed one here. Without the parquet, the index's viewer link is dead on
        # the share even though the HTML views published fine.
        for pat in ("*.parquet", "PADB_Viewer.exe", "*.bat"):
            for f in source_dir.glob(pat):
                shutil.copy2(f, dest_dir / f.name)
                copied += 1
        # Test-point reduction report(s), if any (advisory .txt/.csv beside the
        # data) -- so the published index's reduction link resolves too.
        for f in source_dir.glob("*_testpoint_reduction.*"):
            shutil.copy2(f, dest_dir / f.name)
            copied += 1
        print(f"  Published {copied} file(s) -> {dest_dir}", flush=True)
    except Exception as exc:
        print(f"  [WARN] Publish failed: {exc}", flush=True)


# ===========================================================================
# 8.  PADB runner (optional — skip with --csv)
# ===========================================================================

def _resolve_csv_path(csv_path: Path) -> Path:
    """
    If the configured/predicted csv_path doesn't exist, look for the real
    CSV PADB actually wrote in the same directory, using the identical
    filename-normalization rules find_csvs() applies in padb_run.py (PADB
    replaces spaces with underscores and can normalize hyphens/dots too).
    A predicted csv_path (e.g. from padb_make_v2_job.py) can drift from
    reality -- this gives it one more chance before failing outright.

    Real incident this stricter matching was added for (2026-08-26): the
    original fallback used a fixed 15-character prefix of the predicted
    stem to fuzzy-match, e.g. "EP6_Closed_Loop" for
    "EP6_Closed_Loop_Phase_Noise_DCFM_at_Defined_Offsets_AMC2" -- but every
    scatter analytic in a multi-analytic pod commonly shares that same short
    prefix. When PADB genuinely never wrote the predicted CSV at all (here:
    OutputConfig_OutputCSV was off for this one AMC2 analytic, confirmed by
    only .txt/.pdf siblings existing, no .csv), this silently substituted a
    completely different, unrelated analytic's CSV -- specifically the
    pod's full continuous-sweep "DCFM" trace (288MB, sorts alphabetically
    first) in place of the small "DCFM at Defined Offsets" one -- producing
    a 213MB HTML mislabeled with the wrong analytic's title, no warning
    beyond an easy-to-miss log line. Replaced the length-based slug with a
    token-based check: every "word" in the predicted stem (split on
    non-alphanumeric boundaries) must appear in the candidate's own stem, so
    a candidate missing "Defined"/"Offsets" (or carrying the wrong
    "EFC"/"DCFM") can never match. No safe candidate -> return csv_path
    unchanged, so the caller's own "CSV not found" error path fires instead
    of a silent wrong-data substitution.
    """
    if csv_path.exists():
        return csv_path
    parent = csv_path.parent
    if not parent.exists():
        return csv_path

    all_stems = {p.stem: p for p in parent.glob("*.csv")}
    for stem in padb_run.filename_stem_variants(csv_path.stem):
        if stem in all_stems:
            found = all_stems[stem]
            print(f"  CSV  : predicted '{csv_path.name}' not found -- using "
                  f"'{found.name}' (matched via PADB filename normalization)")
            return found

    predicted_tokens = set(re.findall(r"[A-Za-z0-9]+", csv_path.stem.lower()))
    candidates = []
    for p in sorted(parent.glob("*.csv")):
        candidate_tokens = set(re.findall(r"[A-Za-z0-9]+", p.stem.lower()))
        if predicted_tokens <= candidate_tokens:
            candidates.append(p)
    if candidates:
        # Shortest stem among token-superset matches is the tightest fit
        # (closest to the predicted name, least extra content).
        best = min(candidates, key=lambda p: len(p.stem))
        print(f"  CSV  : predicted '{csv_path.name}' not found -- using "
              f"'{best.name}' (fuzzy token match)")
        return best

    return csv_path


def _site_has_swept_x(df, x_col=None) -> bool:
    """True only if a site's CSV has a *genuine* swept x-axis: an x-like column
    AND a separate numeric value column to plot against it.

    The x-like column is found by, in priority order:
      1. an explicitly configured ``x_col`` (job.json), when it names a real
         numeric column -- this is how a non-'Frequency'-named swept axis
         (e.g. 'Rate (kHz)' for AM flatness, 'Vgg (V)' for an IV sweep) is
         recognised. ``padb_make_v2_job.py`` already sets ``x_col`` from the
         pod's own ``Data_ScatterPlot_XData_Label`` for exactly these pods, and
         the scatter loader already honours it -- only this routing check was
         still name-only, so such a compare wrongly fell through to histogram.
      2. otherwise, a column whose name contains 'frequency' or 'x value'.

    A no-swept-x test (switching speed) whose only numeric column is the
    measurement itself -- even when that measurement is named e.g. 'Frequency
    Switching Speed (us)', which merely contains the word 'frequency' -- returns
    False, so a cross-site compare of it is correctly routed to the histogram
    view instead of a scatter. (The plain substring check this replaced was
    fooled by 'Frequency' appearing in the measurement name.)"""
    cols = [str(c).strip() for c in df.columns]
    lcs = [c.lower() for c in cols]
    x_idx = set()
    if x_col:
        xl = str(x_col).strip().lower()
        cand = {i for i, l in enumerate(lcs) if l == xl}
        # Only trust the configured x_col if it's actually a present, numeric
        # column; otherwise fall through to name-based detection (a predicted
        # x_col that doesn't exist in this site's CSV must not force True).
        if cand and any(pd.to_numeric(df[cols[i]], errors="coerce").notna().any() for i in cand):
            x_idx = cand
    if not x_idx:
        x_idx = {i for i, l in enumerate(lcs) if ("frequency" in l or "x value" in l)}
    if not x_idx:
        return False
    meta = {"analysis type", "model(s)", "algorithm -> result", "units",
            "group", "device family", "serial number", "station"}
    for i, c in enumerate(cols):
        if i in x_idx or "limit" in lcs[i] or lcs[i] in meta:
            continue
        # Any numeric value column (even sparse -- real measurements can have
        # many blanks) counts, matching the scatter loader's own "first numeric
        # column after the x-axis" selection. A no-swept-x test has no such
        # separate column (its only numeric col is the x-named measurement).
        if bool(pd.to_numeric(df[c], errors="coerce").notna().any()):
            return True
    return False


def _build_compare_csv(compare_csv: dict, job_dir: Path, output_dir: Path, x_col=None) -> tuple[Path, bool]:
    """
    Merge two or more sites' own scatter CSVs into one, tagging each row's
    Group text with "  Site: <name>" before any downstream Group parsing
    happens. Every filter/Group-by/spec-detection function in padb_plots.py
    already treats whatever's in the Group column as a condition dimension,
    so "Site" becomes a real, filterable dimension for free -- no changes
    needed anywhere downstream of this merge.

    Deliberately tolerant of "less than perfect" cross-site data: sites are
    allowed to have different columns (pd.concat unions them, missing ones
    become NaN), different Group dimensions (a site missing a key just gets
    no _grp_ value for it), and one site having no spec limits at all.

    Returns (merged_csv_path, no_swept_x). ``no_swept_x`` is True when *every*
    site's CSV lacks a Frequency/X-value column -- i.e. this is a no-swept-x
    value-distribution test (e.g. switching speed) that should render as a
    histogram, not a scatter. The caller uses it to auto-select the histogram
    view when the job didn't set one explicitly.
    """
    if not isinstance(compare_csv, dict) or len(compare_csv) < 2:
        sys.exit('"compare_csv" must be an object mapping 2+ site names to CSV paths')
    dfs = []
    # sites whose own CSV has no Frequency/X-value column -- (site, csv_name, cols)
    sites_without_freq: list[tuple[str, str, list]] = []
    for site_name, rel_path in compare_csv.items():
        p = Path(rel_path)
        if not p.is_absolute():
            p = (job_dir / rel_path).resolve()
        p = _resolve_csv_path(p)
        if not p.exists():
            sys.exit(f"compare_csv: site {site_name!r} CSV not found: {p}")
        df = pd.read_csv(p, dtype=str)
        df.columns = df.columns.str.strip()
        group_col = next((c for c in df.columns if c.strip().lower() == "group"), None)
        if group_col is None:
            df["Group"] = f"Site: {site_name}"
        else:
            df[group_col] = df[group_col].fillna("").astype(str).str.rstrip() + f"  Site: {site_name}"
        print(f"  compare_csv: site {site_name!r} -- {len(df):,} rows from {p.name}", flush=True)
        if not _site_has_swept_x(df, x_col):
            sites_without_freq.append((site_name, p.name, list(df.columns)))
        dfs.append(df)
    merged = pd.concat(dfs, ignore_index=True, sort=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "_compare_merged.csv"
    merged.to_csv(out_path, index=False)
    print(f"  compare_csv: merged {len(merged):,} total rows from {len(compare_csv)} site(s) -> {out_path.name}", flush=True)

    # A missing Frequency/X column means two very different things depending on
    # whether it's *every* site or just some:
    #   - EVERY site lacks it  -> a genuine no-swept-x value-distribution test
    #     (e.g. switching speed). The histogram view reads the measurement
    #     column, not Frequency, so the rows DO render -- warning about it here
    #     would be a false alarm. Signal the caller to auto-route to histogram.
    #   - SOME sites lack it   -> those are placeholder exports (no matching
    #     test data for this analytic at that site) while the others have real
    #     swept data; keep the per-site NOTE for the genuine gaps.
    no_swept_x = len(sites_without_freq) == len(compare_csv)
    if no_swept_x:
        _log_note(output_dir,
                  "compare_csv: no site has a swept x-axis (a numeric x column plus a separate "
                  "value column) -- this looks like a no-swept-x value-distribution test (e.g. "
                  "switching speed). It will render as an overlaid-by-site histogram, with "
                  "'Site' as a filterable condition dimension; every site's rows are included.")
    else:
        for site_name, csv_name, cols in sites_without_freq:
            _log_note(output_dir,
                      f"compare_csv: site {site_name!r} has no swept x-axis in "
                      f"{csv_name} -- a placeholder export (no matching test data for this "
                      f"analytic at this site) or a different test shape than the other site(s); "
                      f"its rows may not appear in a scatter/swept view. Columns: {cols}")
    return out_path, no_swept_x


def _run_padb_for_csv(cfg: dict, job_dir: Path) -> Path:
    """
    Run PADB to produce the scatter CSV if not already available.

    TODO V2.0: Replace with a direct call to padb_run.py run_padb() once
               the runner is refactored to accept a single-analytic job config.
               For now, raises NotImplementedError so callers fall back to --csv.
    """
    raise NotImplementedError(
        "Automatic PADB execution not yet implemented in V2.0.  "
        "Supply the scatter CSV directly with --csv."
    )


# ===========================================================================
# 9.  CLI entry point
# ===========================================================================

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="PADB Analytics V2.1 — generate all views from one scatter CSV"
    )
    parser.add_argument("job", help="Path to V2 job JSON file")
    parser.add_argument(
        "--csv",
        metavar="PATH",
        help="Pre-generated scatter CSV (skips PADB run)",
    )
    parser.add_argument(
        "--out",
        metavar="DIR",
        help="Output directory (overrides job results_dir)",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Build locally only; do not publish, regardless of the job's "
             "publish_to (leaves the job file itself untouched)",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Force publishing to the standard share location even when the "
             "job's publish_to is empty/absent (a real publish_to still "
             "wins). Publishes to COMPARE_PUBLISH_ROOT\\<dir> for compare "
             "jobs, DEFAULT_PUBLISH_ROOT\\<dir> otherwise. Leaves the job "
             "file itself untouched. --no-publish wins if both are given.",
    )
    parser.add_argument(
        "--pdf-report",
        action="store_true",
        help="Build a comprehensive multi-view PDF report (cover + every view "
             "with its stats table, headless-Chromium print) after the HTML "
             "views. Runtime override for the job's build_pdf_report key; leaves "
             "the job file untouched. Requires: py -m pip install playwright "
             "pypdf && py -m playwright install chromium.",
    )
    parser.add_argument(
        "--pdf-apply-filter",
        action="store_true",
        help="Build the PDF report filter-aware: run each engine view's "
             "auto-filter recommended workflow before printing, so the stats "
             "views show the cleaned population (raw scatter/distribution stay "
             "full). Implies --pdf-report.",
    )
    parser.add_argument(
        "--pdf-filter-site", choices=["primary", "onboarding", "both"], default=None,
        help="Compare only: which site the filter-aware PDF cleans "
             "(primary=reference/SR default, onboarding, both). Implies "
             "--pdf-apply-filter.",
    )
    args = parser.parse_args(argv)

    job_path = Path(args.job).resolve()
    if not job_path.exists():
        sys.exit(f"Job file not found: {job_path}")

    with job_path.open(encoding="utf-8") as f:
        cfg = json.load(f)

    # --no-publish / --publish are runtime overrides only -- they steer
    # _finish_report()'s publish decision without rewriting the job file on
    # disk. --no-publish forces the opt-out path; --publish forces the
    # default-share path when the job opted out ("") or never set a
    # destination, while leaving a real publish_to untouched (it wins). This
    # is what the webapp's "copy to share" checkbox uses so a local-only
    # compare job can reach PADB-Compare without hand-editing its JSON.
    if args.no_publish:
        cfg["publish_to"] = ""
    elif args.publish and not cfg.get("publish_to"):
        cfg.pop("publish_to", None)

    if args.pdf_report:
        cfg["build_pdf_report"] = True
    if args.pdf_apply_filter:
        cfg["build_pdf_report"] = True
        cfg["pdf_report_apply_filter"] = True
    if args.pdf_filter_site:
        cfg["build_pdf_report"] = True
        cfg["pdf_report_apply_filter"] = True
        cfg["pdf_report_filter_site"] = args.pdf_filter_site

    job_dir = job_path.parent

    # Resolve output directory
    results_rel = cfg.get("results_dir", "v2_results")
    output_dir = Path(args.out).resolve() if args.out else (job_dir / results_rel).resolve()

    print()
    print("=" * 60)
    print("  PADB Analytics V2.1")
    print("=" * 60)
    print(f"  Job  : {job_path}")
    print(f"  Out  : {output_dir}")

    # Locate scatter CSV
    if args.csv:
        csv_path = Path(args.csv).resolve()
    elif cfg.get("compare_csv"):
        if not cfg.get("primary_site"):
            cfg["primary_site"] = next(iter(cfg["compare_csv"]))
        csv_path, no_swept_x = _build_compare_csv(cfg["compare_csv"], job_dir, output_dir, cfg.get("x_col"))
        # No-swept-x compare (every site lacks a numeric x-axis, e.g. switching
        # speed) -> auto-select the histogram view, unless the job set views
        # explicitly. Without this, auto view-selection would try scatter, which
        # this data has no x-axis for.
        if no_swept_x and "views" not in cfg:
            cfg["views"] = ["histogram"]
            print("  compare_csv: no numeric x-axis at any site -> auto-selecting histogram view", flush=True)
        print(f"  CSV  : {csv_path.name} (merged compare_csv, primary_site={cfg['primary_site']!r})")
    elif cfg.get("csv_path"):
        csv_path = _resolve_csv_path(Path(cfg["csv_path"]).resolve())
        print(f"  CSV  : {csv_path.name} (from job json)")
    else:
        # Check if a CSV already exists in the results dir
        analytic = cfg.get("analytic", "")
        candidate_name = re.sub(r"[^\w]+", "_", analytic) + ".csv" if analytic else ""
        candidate = output_dir / candidate_name if candidate_name else None
        if candidate and candidate.exists():
            csv_path = candidate
            print(f"  CSV  : {csv_path.name} (existing)")
        else:
            try:
                csv_path = _run_padb_for_csv(cfg, job_dir)
            except NotImplementedError as e:
                print(f"\n  [ERROR] {e}")
                sys.exit(1)

    if not csv_path.exists():
        _log_build_failure(output_dir, cfg, csv_path,
                           "the extraction produced no CSV at this path -- the analytic wrote "
                           "no output at all (no matching test results in the database, or CSV "
                           "output is disabled for it in the pod)")
        sys.exit(f"Scatter CSV not found: {csv_path}")

    print(f"  CSV  : {csv_path}")
    print()

    try:
        generated = generate_report(csv_path, cfg, output_dir)
    except NoPlottableData as exc:
        _log_build_failure(output_dir, cfg, csv_path, str(exc))
        sys.exit(f"Build failed -- {exc}")

    print()
    print(f"Done. {len(generated)} plot(s) in {output_dir}")
    print()


if __name__ == "__main__":
    main()
