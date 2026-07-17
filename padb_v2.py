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
              "env_coverage", "summary"],
    "env_coverage_csv": "",                  # optional: alternate CSV for env_coverage view (e.g. carrier power dBm)
    "env_coverage_y_label": "",             # y-axis label override for env_coverage when env_coverage_csv is set
    "env_coverage_y_lim": null,             # y-axis limits override [lo, hi] for env_coverage when env_coverage_csv is set
    "env_coverage_freq_scale": 1.0,         # freq_scale override for env_coverage when env_coverage_csv is set
    "publish_to": ""                         # optional network path
}
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Reuse V1.0 internals — same directory
# ---------------------------------------------------------------------------
try:
    import padb_plots as _pp
    _HAS_V1 = True
except ImportError:
    _HAS_V1 = False
    print("[WARN] padb_plots not found — HTML rendering unavailable", file=sys.stderr)

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

    df = _pp._load_scatter_for_stats(csv_path)
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

    all_freqs = sorted(set(f for cd in env_data for f in cd["freqs"] if f is not None))
    freq_min = float(min(all_freqs)) if all_freqs else 0.0
    freq_max = float(max(all_freqs)) if all_freqs else 1.0

    log_x_cfg = cfg.get("log_x")
    log_x = bool(log_x_cfg) if log_x_cfg is not None else (freq_min > 0 and freq_max / max(freq_min, 1e-9) >= 100)

    title = cfg.get("title", output_html.stem)

    palette = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
    ]

    html = _pp._build_env_coverage_html(
        env_data=env_data,
        cond_dims=cond_dims,
        title=title,
        y_label="ΔEnv (dB)",
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

    proportion = cfg.get("proportion", 0.90)
    confidence = cfg.get("confidence", 0.90)

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

    # Identify serial column so per-DUT means can be embedded for GF support
    _ser_col = next(
        (c for c in all_grp
         if any(kw in c.removeprefix("_grp_").lower() for kw in _serial_kws)),
        None
    )

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
        })

        if np.isnan(hi_spec_global) and g_hi is not None:
            hi_spec_global = g_hi
        if np.isnan(lo_spec_global) and g_lo is not None:
            lo_spec_global = g_lo

    temps_all = sorted(set(t for r in records for t in r.get("temps", [])))
    freq_vals = sorted(float(f) for f in df["Frequency_MHz"].dropna().unique())
    freq_min  = freq_vals[0] if freq_vals else 0.0
    freq_max  = freq_vals[-1] if freq_vals else 1.0

    _pp._build_summary_html(
        records, cond_dims, cfg, output_html,
        hi_spec=hi_spec_global, lo_spec=lo_spec_global,
        freq_min=freq_min, freq_max=freq_max, freq_vals=freq_vals,
        temps_all=temps_all,
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

_VIEW_FN = {
    "scatter":      render_scatter,
    "stat_summary": render_stat_summary,
    "boxplot":      render_boxplot,
    "distribution": render_distribution,
    "env_coverage": render_env_coverage,
    "summary":      render_summary,
}

_VIEW_LABELS = {
    "scatter":      "Scatter (All Temps)",
    "stat_summary": "Statistical Summary (Room)",
    "boxplot":      "Box Plots",
    "distribution": "Distribution (Delta-Env)",
    "env_coverage": "Environmental Coverage",
    "summary":      "Summary (All Temps)",
}


def _fill_spec_nulls(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill NaN Upper_Limit / Lower_Limit using modal spec for matching condition × frequency.

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
        if after_p2 > 0:
            print(f"    WARNING: {after_p2:,} null {col} values remain unfilled "
                  f"(these rows will be hidden in pass-only mode)", flush=True)
    return df


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

    print(f"  Loading scatter CSV: {csv_path.name}", flush=True)
    df = load_scatter(csv_path, cfg)
    df = _fill_spec_nulls(df)
    print(f"    Rows: {len(df):,}  |  Temps: {sorted(df['Temperature'].unique())}",
          flush=True)

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
        df_ec = _fill_spec_nulls(df_ec)
        print(f"    Rows: {len(df_ec):,}  |  Temps: {sorted(df_ec['Temperature'].unique())}",
              flush=True)

    views = cfg.get("views", list(_VIEW_FN.keys()))
    generated: list[Path] = []

    for view in views:
        fn = _VIEW_FN.get(view)
        if fn is None:
            print(f"  [SKIP] Unknown view '{view}'", flush=True)
            continue

        slug = re.sub(r"[^\w]+", "_", view)
        html_name = re.sub(r"[^\w]+", "_", prefix) + "_" + slug + ".html"
        out_html = output_dir / html_name

        if view == "env_coverage" and df_ec is not None:
            view_cfg = _cfg_for_view(cfg_ec, {"title": f"{prefix} — {_VIEW_LABELS[view]}"})
            use_df = df_ec
        else:
            view_cfg = _cfg_for_view(cfg, {"title": f"{prefix} — {_VIEW_LABELS[view]}"})
            use_df = df

        print(f"  Rendering {_VIEW_LABELS[view]} -> {html_name}", flush=True)
        try:
            fn(use_df, view_cfg, out_html)
            generated.append(out_html)
        except Exception as exc:
            print(f"    [ERROR] {exc}", flush=True)
            _write_placeholder(out_html, view_cfg["title"], f"Error: {exc}")

    _write_index(output_dir, prefix, generated, cfg)

    if cfg.get("publish_to"):
        _publish(output_dir, Path(cfg["publish_to"]))

    return generated


# ===========================================================================
# 6.  Index page
# ===========================================================================

def _write_index(output_dir: Path, prefix: str, html_files: list[Path], cfg: dict) -> None:
    # Merge newly generated files with any pre-existing HTML files in the directory
    # so multiple job runs into the same output dir all appear in the index.
    existing = sorted(
        p for p in output_dir.glob("*.html")
        if p.name != "index.html" and p not in html_files
    )
    all_files = sorted(set(existing) | set(html_files), key=lambda p: p.stem.lower())
    items = "".join(
        f'<li><a href="{p.name}">{p.stem.replace("_", " ")}</a></li>'
        for p in all_files
    )
    title = cfg.get("index_title", prefix)
    # Suppress per-job description when multiple jobs share the same output dir
    desc = cfg.get("description", "") if not existing else ""
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset='utf-8'><title>{title}</title>
<style>
  body{{font-family:sans-serif;max-width:800px;margin:40px auto;}}
  h1{{font-size:1.4em;}} li{{margin:6px 0;}}
  a{{color:#1f77b4;text-decoration:none;}} a:hover{{text-decoration:underline;}}
</style>
</head>
<body>
<h1>{title}</h1>
{"<p>"+desc+"</p>" if desc else ""}
<ul>{items}</ul>
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
        print(f"  Published {copied} file(s) → {dest_dir}", flush=True)
    except Exception as exc:
        print(f"  [WARN] Publish failed: {exc}", flush=True)


# ===========================================================================
# 8.  PADB runner (optional — skip with --csv)
# ===========================================================================

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
    args = parser.parse_args(argv)

    job_path = Path(args.job).resolve()
    if not job_path.exists():
        sys.exit(f"Job file not found: {job_path}")

    with job_path.open(encoding="utf-8") as f:
        cfg = json.load(f)

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
    elif cfg.get("csv_path"):
        csv_path = Path(cfg["csv_path"]).resolve()
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
        sys.exit(f"Scatter CSV not found: {csv_path}")

    print(f"  CSV  : {csv_path}")
    print()

    generated = generate_report(csv_path, cfg, output_dir)

    print()
    print(f"Done. {len(generated)} plot(s) in {output_dir}")
    print()


if __name__ == "__main__":
    main()
