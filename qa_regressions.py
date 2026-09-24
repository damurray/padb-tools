#!/usr/bin/env python3
r"""qa_regressions.py -- per-fix regression pins for the pure Python helpers.

Many one-off fixes documented in CLAUDE.md were verified once by hand and then
had no permanent guard. The browser-tier JS fixes are now covered generically by
qa_filters; this file pins the *Python-side* helper fixes -- each test encodes the
EXACT reported failure shape, so a regression that reintroduces the old behaviour
fails here immediately. Browser-free and deterministic, so it belongs in
qa_selfcheck's core.

Each check names the fix it guards (date in CLAUDE.md). Add a pin here whenever a
new pure-helper fix lands.

Usage:  python qa_regressions.py
Exit codes: 0 = all checks pass, 1 = one or more failures.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import padb_plots as pp
import padb_run as pr
import padb_make_v2_job as mv

_PASS: list[str] = []
_FAIL: list[str] = []


def check(desc: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASS.append(desc)
        print(f"  PASS  {desc}")
    else:
        _FAIL.append(desc)
        print(f"  FAIL  {desc}" + (f" -- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Freq-range default clipping -- _floor_dec/_ceil_dec (2026-08-18)
# Plain round()/':.3f' can round a true min UP / true max DOWN, silently
# excluding the very first/last data point on page load. Floor lo / ceil hi.
# ---------------------------------------------------------------------------
def test_freq_floor_ceil():
    lo = pp._floor_dec(1000.1236, 3)
    hi = pp._ceil_dec(1999.8764, 3)
    check("_floor_dec never rounds a lower bound above the true value",
          lo <= 1000.1236 and lo == 1000.123, f"lo={lo}")
    check("_ceil_dec never rounds an upper bound below the true value",
          hi >= 1999.8764 and hi == 1999.877, f"hi={hi}")
    # a value already at the precision is unchanged
    check("_floor_dec/_ceil_dec are exact on an at-precision value",
          pp._floor_dec(750.0, 3) == 750.0 and pp._ceil_dec(750.0, 3) == 750.0)


# ---------------------------------------------------------------------------
# Boxplot freq-label collision -- _freq_label_map (2026-08-18)
# Two genuinely distinct close frequencies must NOT collapse to one categorical
# label (they'd render as overlapping boxes). Well-separated freqs keep short labels.
# ---------------------------------------------------------------------------
def test_freq_label_map():
    m = pp._freq_label_map([8199.95, 8200.05], "MHz")
    check("_freq_label_map disambiguates a 2-way GHz collision",
          m[8199.95] != m[8200.05], f"map={m}")
    m3 = pp._freq_label_map([8199.95, 8200.0, 8200.05], "MHz")
    check("_freq_label_map disambiguates a 3-way collision (3 distinct labels)",
          len({m3[8199.95], m3[8200.0], m3[8200.05]}) == 3, f"map={m3}")
    ms = pp._freq_label_map([100.0, 200.0, 8500.0], "MHz")
    check("_freq_label_map keeps short labels for well-separated freqs",
          ms[100.0] == "100 MHz" and ms[200.0] == "200 MHz" and ms[8500.0] == "8.5 GHz", f"map={ms}")


# ---------------------------------------------------------------------------
# P/C <select> snap -- _snap_pc_opt (2026-08-18)
# Must return the option's exact value STRING ("0.90"), not the float 0.9 --
# assigning a bare float to a fixed-string-option <select> silently fails.
# ---------------------------------------------------------------------------
def test_snap_pc_opt():
    p = pp._snap_pc_opt(0.90, pp._PC_P_OPTS)
    c = pp._snap_pc_opt(0.90, pp._PC_C_OPTS)
    check("_snap_pc_opt returns the exact option string for P (not a float)",
          p == "0.90" and isinstance(p, str), f"p={p!r}")
    check("_snap_pc_opt returns the exact option string for C",
          c == "0.90" and isinstance(c, str), f"c={c!r}")
    check("_snap_pc_opt snaps a between-value to the nearest option",
          pp._snap_pc_opt(0.94, pp._PC_P_OPTS) == "0.95", f"got={pp._snap_pc_opt(0.94, pp._PC_P_OPTS)!r}")


# ---------------------------------------------------------------------------
# Control-label short form -- _short_x_label (2026-08-10)
# Non-frequency x-axes must relabel "Freq" controls; the default is preserved.
# ---------------------------------------------------------------------------
def test_short_x_label():
    check("_short_x_label derives a non-freq short label",
          pp._short_x_label("Amplitude (dBm)") == "Amplitude", pp._short_x_label("Amplitude (dBm)"))
    check("_short_x_label preserves the literal 'Freq' for the default axis",
          pp._short_x_label("Frequency (MHz)") == "Freq", pp._short_x_label("Frequency (MHz)"))


# ---------------------------------------------------------------------------
# Group-string padding bug -- _parse_group_kv (2026-07-21)
# PADB right-pads a short value, producing 2+ spaces after the colon; the old
# split treated that as a segment boundary and dropped the key. Must still parse.
# ---------------------------------------------------------------------------
def test_parse_group_kv():
    kv = pp._parse_group_kv("Frequency (MHz):  10  Serial Number: US65080401")
    check("_parse_group_kv keeps a key whose value is space-padded (2+ spaces)",
          kv.get("Frequency (MHz)") == "10", f"kv={kv}")
    check("_parse_group_kv preserves multi-word keys alongside padding",
          kv.get("Serial Number") == "US65080401", f"kv={kv}")
    kv2 = pp._parse_group_kv("HarmonicNumber: 2  Port: RF1  Serial Number: QA001")
    check("_parse_group_kv parses a normal double-space-separated group",
          kv2 == {"HarmonicNumber": "2", "Port": "RF1", "Serial Number": "QA001"}, f"kv2={kv2}")


# ---------------------------------------------------------------------------
# Spec/Uncertainty group parsing -- _extract_group_field (2026-08-06)
# ---------------------------------------------------------------------------
def test_extract_group_field():
    s = pd.Series(["Upper Spec (<=): -43.0  Lower Spec (>=): -110.0",
                   "Upper Spec (<=): -48.0  Lower Spec (>=): -105.0"])
    hi, lo = pp._extract_group_field(s, "Spec")
    check("_extract_group_field pulls Upper/Lower Spec values from Group text",
          list(hi) == [-43.0, -48.0] and list(lo) == [-110.0, -105.0], f"hi={list(hi)} lo={list(lo)}")


# ---------------------------------------------------------------------------
# Segment-by control gating -- _has_segmentable_spec (2026-08-21)
# ---------------------------------------------------------------------------
def test_has_segmentable_spec():
    flat = pd.DataFrame({"Upper_Limit": [-50.0, -50.0], "Lower_Limit": [-110.0, -110.0]})
    check("_has_segmentable_spec is False for a flat (constant) spec",
          pp._has_segmentable_spec(flat) is False)
    varying = pd.DataFrame({"Upper_Limit": [-50.0, -50.0], "Lower_Limit": [-110.0, -110.0],
                            "Spec_Hi": [-48.0, -43.0]})
    check("_has_segmentable_spec is True when a spec key varies",
          pp._has_segmentable_spec(varying) is True)


# ---------------------------------------------------------------------------
# subex relative-date sentinels -- _resolve_date_sentinel (2026-08-03)
# ---------------------------------------------------------------------------
def test_resolve_date_sentinel():
    today = date.today()
    check("_resolve_date_sentinel('today') is today's date",
          pr._resolve_date_sentinel("today") == today.isoformat())
    check("_resolve_date_sentinel('8 weeks ago') is today-56d",
          pr._resolve_date_sentinel("8 weeks ago") == (today - timedelta(weeks=8)).isoformat())
    check("_resolve_date_sentinel('1 day ago' is today-1d",
          pr._resolve_date_sentinel("1 day ago") == (today - timedelta(days=1)).isoformat())
    check("_resolve_date_sentinel returns None for a literal date (caller keeps it)",
          pr._resolve_date_sentinel("2026-07-31") is None)
    check("_resolve_date_sentinel returns None for a non-date subex ('{All}')",
          pr._resolve_date_sentinel("{All}") is None)


# ---------------------------------------------------------------------------
# CSV filename stem variants -- filename_stem_variants (2026-08-04)
# space->_, hyphen kept|->_, dot kept|->_
# ---------------------------------------------------------------------------
def test_filename_stem_variants():
    v = pr.filename_stem_variants("Sub-Harmonics 1.5")
    check("filename_stem_variants offers the space->_ hyphen-kept dot-kept variant",
          "Sub-Harmonics_1.5" in v, f"v={v}")
    check("filename_stem_variants offers the hyphen->_ and dot->_ variant",
          "Sub_Harmonics_1_5" in v, f"v={v}")
    check("filename_stem_variants de-dups a name with nothing to normalise",
          pr.filename_stem_variants("Plain") == ["Plain"], f"v={pr.filename_stem_variants('Plain')}")


# ---------------------------------------------------------------------------
# Non-frequency x-axis auto-detect -- _clean_x_axis_label / _x_col_override /
# _is_non_sweep_x (2026-08-21, phase-noise offset fix 2026-08-26, histogram)
# ---------------------------------------------------------------------------
def test_x_axis_detection():
    check("_clean_x_axis_label strips the ~ prefix and (rows x cols) suffix",
          mv._clean_x_axis_label("~Vgg (V) (1 x 303)") == "Vgg (V)",
          mv._clean_x_axis_label("~Vgg (V) (1 x 303)"))
    check("_x_col_override overrides a non-freq swept axis (Vgg/V)",
          mv._x_col_override("~Vgg (V) (1 x 303)") == ("Vgg (V)", "Vgg (V)", "V"),
          f"{mv._x_col_override('~Vgg (V) (1 x 303)')}")
    check("_x_col_override returns None for a plain Frequency-in-MHz axis",
          mv._x_col_override("~Frequency (MHz) (1 x 500)") is None,
          f"{mv._x_col_override('~Frequency (MHz) (1 x 500)')}")
    check("_x_col_override overrides a Frequency-OFFSET-in-Hz axis (phase noise)",
          mv._x_col_override("~Frequency Offset (Hz) (1 x 200)") == ("Frequency Offset (Hz)", "Frequency Offset (Hz)", "Hz"),
          f"{mv._x_col_override('~Frequency Offset (Hz) (1 x 200)')}")
    check("_is_non_sweep_x True for a text-typed [T] axis",
          mv._is_non_sweep_x("~Device Family [T] (1 x 1)") is True)
    check("_is_non_sweep_x True for a single-value (1 x 1) axis",
          mv._is_non_sweep_x("~Something (1 x 1)") is True)
    check("_is_non_sweep_x False for a real numeric sweep",
          mv._is_non_sweep_x("~Vgg (V) (1 x 303)") is False)
    check("_is_non_sweep_x True for an empty label",
          mv._is_non_sweep_x("") is True)


# ---------------------------------------------------------------------------
# CSV->parquet embedded-newline fix -- _csv_to_parquet (2026-09-11)
# A quoted Group/label cell can contain a newline; without
# ParseOptions(newlines_in_values=True) pyarrow desyncs and the export fails.
# ---------------------------------------------------------------------------
def test_csv_to_parquet_newlines():
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        check("_csv_to_parquet embedded-newline (SKIPPED: no pyarrow)", True)
        return
    import padb_v2 as v2
    with tempfile.TemporaryDirectory() as td:
        csvp = Path(td) / "nl.csv"
        # 3 data rows; the middle row's Group cell contains an embedded newline.
        csvp.write_text(
            'Frequency (MHz),Power (dBc),Group\n'
            '100,-80,"SpurType: A"\n'
            '200,-81,"SpurType: B\nsecond line"\n'
            '300,-82,"SpurType: C"\n',
            encoding="utf-8",
        )
        outp = Path(td) / "nl.parquet"
        try:
            rows, _csv_mb, _pq_mb = v2._csv_to_parquet(csvp, outp)
            ok = rows == 3 and outp.exists()
            detail = f"rows={rows} exists={outp.exists()}"
        except Exception as exc:
            ok = False
            detail = f"raised: {exc}"
        check("_csv_to_parquet round-trips a CSV with an embedded newline in a cell",
              ok, detail)


# ---------------------------------------------------------------------------
# Live "Show all points" toggle -- scatter_decimate_toggle (2026-09-14)
# When set, the FULL point set must be embedded (no server decimation) plus the
# toggle constant + checkbox; the default build stays server-decimated with no
# checkbox. (The client-side _decimateClient envelope itself is browser-tier;
# verified via the in-app browser -- this pins the server contract.)
# ---------------------------------------------------------------------------
def test_scatter_decimate_toggle():
    import csv as _csv
    import math as _m
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "dense.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for i in range(6000):   # one dense series > the 2000 threshold, > 3000 target
                w.writerow(["Room", round(100.0 + i * 0.1, 3), round(-80 + _m.sin(i / 300.0), 4),
                            "Serial Number: D1", -50, -110])
        df = pp._load_scatter_csv(p)
        base = {"y_label": "P", "views": ["scatter"]}
        html_tog = pp._build_av_freq_html(df.copy(), {**base, "scatter_decimate_toggle": True}, "T")
        html_def = pp._build_av_freq_html(df.copy(), dict(base), "D")   # default (auto server-decimate)

        def nrows(h):
            return h.count('"Frequency_MHz":')

        # The JS always *references* show_all_pts_chk (getElementById), so test for
        # the rendered <input id="show_all_pts_chk"> ELEMENT, not the bare string.
        check("scatter toggle emits SCATTER_DECIMATE_TOGGLE=true + renders the Show-all-points checkbox",
              "SCATTER_DECIMATE_TOGGLE=true" in html_tog and 'id="show_all_pts_chk"' in html_tog)
        check("scatter default build has no toggle (SCATTER_DECIMATE_TOGGLE=false, checkbox not rendered)",
              "SCATTER_DECIMATE_TOGGLE=false" in html_def and 'id="show_all_pts_chk"' not in html_def)
        check("scatter toggle embeds the FULL series while default server-decimates it",
              nrows(html_tog) == 6000 and nrows(html_def) < 6000,
              f"toggle_rows={nrows(html_tog)} default_rows={nrows(html_def)}")


def test_spec_mask_interpolation():
    """spec_interp='linear' interpolates a frequency-varying spec MASK between its
    sparse breakpoints (linear in log-frequency), so gap-frequencies get the true
    complex limit line instead of a flat fallback. Default 'none' leaves gaps null
    (safe for step/constant specs). Regression for the 2.4G broadband-noise mask
    that rendered as a flat -72 line with dropouts."""
    import padb_v2 as _v2
    import numpy as _np
    # Sparse mask: breakpoints at 1/10/100/1000 MHz; gaps at 50 and 500 are null.
    rows = []
    bp = {1.0: -72.0, 10.0: -95.0, 100.0: -105.0, 1000.0: -135.0}
    for f, v in bp.items():
        for s in ("D00", "D01"):
            rows.append({"_grp_Serial Number": s, "Frequency_MHz": f, "Upper_Limit": v})
    for f in (50.0, 500.0):          # gap frequencies -- no limit at all
        for s in ("D00", "D01"):
            rows.append({"_grp_Serial Number": s, "Frequency_MHz": f, "Upper_Limit": float("nan")})
    df = pd.DataFrame(rows)
    # Default: gaps stay null.
    d_none = _v2._fill_spec_nulls(df.copy(), "none")
    n_null_none = int(d_none["Upper_Limit"].isna().sum())
    check("spec mask: default 'none' leaves gap-frequency limits null",
          n_null_none == 4, f"nulls={n_null_none} (expected 4)")
    # linear: no nulls, breakpoints preserved, gaps log-interpolated.
    d_lin = _v2._fill_spec_nulls(df.copy(), "linear")
    check("spec mask: 'linear' fills every gap (no nulls remain)",
          int(d_lin["Upper_Limit"].isna().sum()) == 0)
    at = lambda f: float(d_lin[d_lin["Frequency_MHz"] == f]["Upper_Limit"].iloc[0])
    check("spec mask: breakpoints preserved exactly",
          at(1.0) == -72.0 and at(100.0) == -105.0 and at(1000.0) == -135.0)
    exp50 = _np.interp(_np.log10(50.0), _np.log10([10.0, 100.0]), [-95.0, -105.0])
    check("spec mask: gap at 50 MHz interpolated linearly in log-frequency",
          abs(at(50.0) - float(exp50)) < 1e-6, f"got={at(50.0)} expected={exp50}")
    # descending: 50 is between 10 and 100's values, not a flat fallback.
    check("spec mask: interpolated gap lies between its neighbors (not flat)",
          -105.0 < at(50.0) < -95.0, f"at(50)={at(50.0)}")
    # pchip: smooth monotone mask -- fills every gap, preserves breakpoints, and
    # (unlike a plain cubic spline) never overshoots below the neighbors' range.
    d_pch = _v2._fill_spec_nulls(df.copy(), "pchip")
    check("spec mask: 'pchip' fills every gap (no nulls remain)",
          int(d_pch["Upper_Limit"].isna().sum()) == 0)
    atp = lambda f: float(d_pch[d_pch["Frequency_MHz"] == f]["Upper_Limit"].iloc[0])
    check("spec mask: 'pchip' preserves breakpoints exactly",
          atp(1.0) == -72.0 and atp(100.0) == -105.0 and atp(1000.0) == -135.0)
    check("spec mask: 'pchip' gap stays within neighbor range (no overshoot)",
          -105.0 <= atp(50.0) <= -95.0 and -135.0 <= atp(500.0) <= -105.0,
          f"at50={atp(50.0)} at500={atp(500.0)}")


def test_scatter_mask_is_dataset_level():
    """The scatter must decide 'is this a frequency-varying mask?' from the whole
    DATASET (memoized _isMaskDataset), NOT from the currently-filtered subset --
    zooming into a slowly-varying part of a mask leaves <=3 distinct rounded limit
    values in view and the old per-filter heuristic flipped to 'not a mask',
    drawing flat full-width lines instead of the sloped mask segment (reported:
    zoomed-in shows a pair of horizontal limits)."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mask.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            # descending mask across freq (many distinct limit values -> a real mask)
            for i in range(40):
                fq = round(1.0 + i * 5.0, 3)
                lim = round(-70 - i * 2.5, 3)
                w.writerow(["Room", fq, round(lim - 5, 3), "Serial Number: D0", lim, -300])
        df = pp._load_scatter_csv(p)
        h = pp._build_av_freq_html(df, {"y_label": "P", "views": ["scatter"]}, "T")
        check("scatter: dataset-level mask helper present (_isMaskDataset + memo)",
              "function _isMaskDataset()" in h and "_dsMaskCache" in h)
        check("scatter: buildTraces gates the mask trace on _isMaskDataset() (not the filtered subset)",
              "if(_isMaskDataset()){" in h)
        check("scatter: buildLayout gates flat shapes on !_isMaskDataset() (not getSpecMask(filtered).isMask)",
              "if(!_isMaskDataset()){" in h and "if(!getSpecMask(filtered||DATA).isMask)" not in h)


def test_scatter_worst_first_spec_relative():
    """'Worst first' sort ranks legend groups by largest ERROR RELATIVE TO SPEC
    (max exceedance past the effective Spec_Hi/Lo, else Upper/Lower_Limit), NOT
    just highest raw value -- with a graceful fallback to raw max Value when the
    dataset carries no spec at all (David 2026-09-18). Verified live (controlled
    flip + real ClockSpurs staircase); this pins the wiring so it can't silently
    revert to the old raw-value sort."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "w.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Frequency (MHz)", "Value (dBc)", "Group", "Upper Limit"])
            for i in range(20):
                fq = 100 + i * 100
                w.writerow([fq, -80, "SpurType: A", -76])   # passes (margin)
                w.writerow([fq, -90, "SpurType: B", -95])   # exceeds spec
        df = pp._load_scatter_csv(p)
        h = pp._build_av_freq_html(df, {"y_label": "P", "views": ["scatter"]}, "T")
        check("scatter worst-first: spec-exceedance helpers present",
              "function _rowSpecExceed(r)" in h and "function _groupWorstSpec(rows)" in h
              and "function _scatHasSpec()" in h)
        check("scatter worst-first: _rowSpecExceed prefers Spec_Hi/Lo then Upper/Lower_Limit",
              "r.Spec_Hi" in h and "r.Upper_Limit" in h
              and "r.Spec_Lo" in h and "r.Lower_Limit" in h)
        check("scatter worst-first: sort ranks by spec exceedance with raw-value fallback",
              "sortBy==='worst_desc'" in h and "_scatHasSpec()" in h
              and "_groupWorstSpec(" in h and "_rowsMaxVal(" in h)
        check("scatter worst-first: no-spec groups sort last (null-guarded)",
              "if(wa===null) return 1;" in h and "if(wb===null) return -1;" in h)


def test_scatter_room_temp_filterable():
    """Scatter temperature checkboxes are ALL toggleable, including Room. Room is just a
    plotted Test Step in a scatter, NOT a baseline (that role is delta/env-only). Room was
    previously rendered `disabled` (force-on) so it couldn't be excluded while every other
    temp toggled -- reported as filter/table/plot not matching on a multi-temp compare."""
    import csv as _csv, re as _re
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mt.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for temp in ("Room", "20.0 Deg C", "30.0 Deg C"):
                for i in range(4):
                    w.writerow([temp, 100.0, round(10.0 + i * 0.1, 4), f"Serial Number: D{i}", 20, -20])
        df = pp._load_scatter_csv(p)
        h = pp._build_av_freq_html(df, {"y_label": "P", "views": ["scatter"]}, "T")
        checks = _re.findall(r'<input type="checkbox" class="env_chk" value="([^"]*)"([^>]*)>', h)
        room = [a for v, a in checks if v == "Room"]
        check("scatter: multi-temp env_chk boxes rendered incl Room", len(checks) >= 2 and len(room) == 1,
              f"vals={[v for v, _ in checks]}")
        check("scatter: Room temp checkbox is NOT disabled (filterable like other temps)",
              bool(room) and "disabled" not in room[0], f"room_attrs={room}")
        check("scatter: every temp checkbox has onchange=update() (incl Room)",
              all("onchange" in a for _, a in checks) and not any("disabled" in a for _, a in checks))


def test_filter_state_scoping():
    """Filter-panel localStorage state (STATE_KEY) must be scoped PER PAGE, not per
    results-dir. All plot jobs of one pod share a results_dir (by design), so a
    results-dir-only key made every analytic -- and two different datasets built
    into the same dir -- collide in localStorage: one analytic's serial selection
    leaked into another's and 'did not persist'. STATE_KEY now includes the page
    title, so two pages sharing a results_dir get distinct namespaces."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "s.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for i in range(4):
                for fq in (100.0, 200.0):
                    w.writerow(["Room", fq, round(-80 + 0.2 * i, 3),
                                f"Serial Number: D{i:02d}", -50, -110])
        df = pp._load_scatter_csv(p)
        import re as _re
        def state_key(h):
            m = _re.search(r"var STATE_KEY=(\"[^\"]*\");", h)
            return m.group(1) if m else None
        # Two DIFFERENT analytics/pages, SAME results_dir.
        rd = {"y_label": "P", "views": ["scatter"], "results_dir": "shared_v2_results"}
        hA = pp._build_av_freq_html(df.copy(), {**rd, "title": "PodX AnalyticA — Scatter (Room)"}, "A")
        hB = pp._build_av_freq_html(df.copy(), {**rd, "title": "PodX AnalyticB — Scatter (Room)"}, "B")
        kA, kB = state_key(hA), state_key(hB)
        check("filter STATE_KEY is present and page-scoped (includes the title)",
              kA is not None and "AnalyticA" in kA, f"kA={kA}")
        check("filter STATE_KEY differs between two pages sharing a results_dir",
              kA is not None and kB is not None and kA != kB, f"kA={kA} kB={kB}")
        # Same page (same title+results_dir) is stable across rebuilds.
        hA2 = pp._build_av_freq_html(df.copy(), {**rd, "title": "PodX AnalyticA — Scatter (Room)"}, "A")
        check("filter STATE_KEY is stable for the same page across rebuilds",
              state_key(hA2) == kA, f"{state_key(hA2)} vs {kA}")


def test_auto_filter_boxplot():
    """Server contract for the boxplot 'Auto-filter bad DUTs' feature: the controls
    render, and the JS carries the MAD-robust magnitude (not sigma-from-mean) and
    the per-direction systemic guard -- the two correctness fixes this feature
    depends on. Pins the shape so a refactor can't silently drop either."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "box.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for freq in (100.0, 200.0):
                for i in range(12):
                    w.writerow(["Room", freq, round(10.0 + 0.05 * (i % 5), 4),
                                f"HarmonicNumber: 2  Serial Number: D{i:02d}", 20, -20])
        out = Path(td) / "box.html"
        pp.stat_boxplot(p, {"y_label": "P", "title_prefix": "T"}, out)
        h = out.read_text(encoding="utf-8")
        check("auto-filter renders basis + level selects",
              'id="auto_gf_basis"' in h and 'id="auto_gf_level"' in h)
        check("auto-filter basis options dist/iqr/dmad/spec/tll present",
              all(f'value="{v}"' in h for v in ("dist", "iqr", "dmad", "spec", "tll")))
        check("auto-filter shared scorer present (_afScorer/_afPeerBasis, all 5 bases)",
              "_afScorer" in h and "_afPeerBasis" in h and "Double-MAD" in h and "IQR fence" in h)
        check("auto-filter level options off/conservative/moderate/aggressive present",
              all(f'value="{v}"' in h for v in ("off", "conservative", "moderate", "aggressive")))
        check("auto-filter panel div rendered", 'id="auto_gf_panel"' in h)
        check("auto-filter JS present (_autoBadPoints/_autoFilterCompute/autoFilterApply/_AUTO_LEVELS)",
              all(s in h for s in ("_autoBadPoints", "_autoFilterCompute", "autoFilterApply", "_AUTO_LEVELS")))
        # Magnitude is MAD-robust (median/MAD, 1.4826 scale, Iglewicz 3.5 cutoff) --
        # NOT sigma-from-mean, which saturates and masks the bad DUT.
        check("auto-filter magnitude is MAD-robust (1.4826 + 3.5 cutoff)",
              "1.4826" in h and "3.5" in h and "_autoMedian" in h)
        # Systemic 'shared' guard keys on direction too, so a high outlier and a low
        # outlier at one frequency aren't mistaken for one systemic event.
        check("auto-filter systemic guard is per-direction (keys include o.dir)",
              "o.freqLabel+'|'+o.dir" in h)
        # Risk gate + compare scoping present.
        check("auto-filter risk gate + compare scoping present",
              "_autoRisk" in h and "d.risk<0.05" in h and "PRIMARY_SITE" in h)
        check("boxplot Workflow & Recommendations present (button/panel/ctx/engine)",
              all(s in h for s in ('id="box_wf_btn"', 'id="box_wf_panel"', "BOX_AF", "boxRunWorkflow",
                                   "_afAnalyze", "_afRecommend", "_afRunWorkflow")))
        check("boxplot print-to-PDF report present (_afGenerateReport/boxGenReport/_afCapture multi-shot)",
              all(s in h for s in ("_afGenerateReport", "boxGenReport", "Generate PDF report", "Plotly.toImage", "_afCapture")))
        # Subpopulation / dual-distribution advisory: the JS port of padb_subpop.py
        # (_spDetect/_spBucketSplit) + its Workflow-panel renderer (_spAdvisoryHtml),
        # gated behind ctx.subpopSlices, wired into boxplot via BOX_AF.subpopSlices.
        check("subpop advisory: JS detector port present (_spDetect/_spBucketSplit/_spMedian)",
              all(s in h for s in ("_spDetect", "_spBucketSplit", "_spMedian", "_spMad")))
        check("subpop advisory: renderer + workflow hook present",
              "_spAdvisoryHtml" in h and "typeof ctx.subpopSlices==='function'" in h)
        check("subpop advisory: boxplot supplies subpopSlices to BOX_AF",
              "subpopSlices:function()" in h and "budget_by_freq" in h and "station_by_dut" in h)
        # "Remove auto-filter": subtract ONLY the auto increment from the GF (manual items
        # survive), distinct from Clear-global-filter (wipes all). Tracks auto-new keys.
        check("remove-auto: engine + boxplot wiring present (_afMarkApplied/_afRemoveApplied/boxRemoveAuto)",
              all(s in h for s in ("_afMarkApplied", "_afRemoveApplied", "boxRemoveAuto",
                                   "removeFn:'boxRemoveAuto'", "Remove auto-filter (")))
        # Workflow panel expand/contract: caret + shared toggle + explicit display:block
        # (so the "shown" state is detectable and the panel can actually contract).
        check("workflow panel expand/contract wired (wfcaret + _afToggleWorkflow + display block)",
              "wfcaret" in h and "_afToggleWorkflow" in h and "panel.style.display='block'" in h)
        # "No Apply button" is explained (not a blank result) when candidates exist but
        # none is auto-filterable (auto=0 && marginal=0 && review>0) -- e.g. a systemic /
        # site-wide spread. Reported on the Return_Loss compare boxplot.
        check("auto-filter no-apply banner present + wired in both preview paths",
              "_afNoApplyBanner" in h and h.count("!r.auto.length && !r.marginal.length && r.review.length") >= 1
              and "Nothing to auto-filter here" in h)
        # Recommended-workflow re-check step names the now-always-present Summary/Stat
        # Summary views (+ Env Coverage/Distribution only when multiTemp) and the
        # Distribution health subpop check -- adapts to the dataset's available views.
        check("workflow re-check step names Summary/Stat Summary + Distribution health",
              "Re-check the cleaned data in the" in h and "Stat&nbsp;Summary" in h
              and "Distribution&nbsp;health" in h and "a.multiTemp?" in h)
        # Longform per-condition selections (box_cond_lf_chk) are the authoritative
        # getSelectedConds() source and must round-trip through save/loadState --
        # they can express arbitrary condition subsets the per-dim panels cannot, so
        # relying on _syncLfFromAllDims to re-derive them silently loses a single
        # unchecked condition on reload (found by qa_filters on the FM1 compare).
        check("boxplot persists longform condition selections (saveState writes lfcond)",
              "_stSet('lfcond_'" in h)
        check("boxplot restores longform condition selections (loadState reads lfcond)",
              "_stGet('lfcond_'" in h)
        # Statistics Table couples to a normal-mode GF exclusion (the auto-filter's own
        # apply path), not only GF Focus/Inspect mode. Bug found 2026-09-15 on the
        # MaxPowerTutorial2 Leveled_Linear boxplot ("auto filter tables are not
        # coupled"): the ungrouped table branch was entered for gfFocusActive but not
        # _gfActiveSt, and its inner point-drop ran only 'if(gfFocusActive)', so an
        # auto-filter/GF exclusion (exclude mode -- the default) changed the plot's box
        # stats but never the Statistics Table's n/mean/Q1..Q3. The plot has always
        # applied GF in both modes (buildBoxTraces: gfActive + boxGfFocus?!_ig:_ig);
        # the table must match. (Grouped Group-by path already did, via
        # _computeBoxGroupedByColId.)
        check("stats table enters filtered branch on normal-mode GF (_gfActiveSt in else-if)",
              "||gfFocusActive||_gfActiveSt||isExclRoom()" in h)
        check("stats table applies GF point-drop in BOTH modes (not focus-only)",
              "if(_gfActiveSt){var _ck=_boxBaseSerial(d.s)+'||'+_boxFullCondKey(cd.condition,d.p)"
              "+'|Temp='+cd.temp+'|Freq='+_gfFreqKey(f);var _ig=_boxIsInGf(_ck);"
              "if(_gfFocusSt?!_ig:_ig) return false;}" in h)
        check("stats table shows GF-filtered label + skips BOX_STATS normality reuse under GF",
              "_gfActiveSt?'GF-filtered'" in h
              and "!gfFocusActive&&!_gfActiveSt&&!exclRoomSt" in h  # tempOnlyFilter
              and "&&!gfFocusActive&&!_gfActiveSt&&!isExclRoom()" in h)  # _stReuseBase


def test_auto_filter_site_scope():
    """Server contract for the compare auto-filter SITE SCOPE (boxplot prototype):
    a compare boxplot (Site in Group + primary_site) renders the site-scope selector
    (Reference/Onboarding/Both) and the scope-gate + per-site risk-summary wiring; a
    NON-compare boxplot has no such selector (default reference-only, unchanged)."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        # Compare CSV: two sites, a lone per-site outlier at different freqs.
        pc = Path(td) / "cmp.csv"
        with pc.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for site in ("SR", "AMC"):
                for freq in (100.0, 200.0):
                    for i in range(12):
                        v = round(10.0 + 0.05 * (i % 5), 4)
                        if (site == "SR" and freq == 100.0 and i == 0) or (site == "AMC" and freq == 200.0 and i == 6):
                            v = 40.0
                        w.writerow(["Room", freq, v,
                                    f"HarmonicNumber: 2  Serial Number: {site}D{i:02d}  Site: {site}", 20, -20])
        oc = Path(td) / "cmp.html"
        pp.stat_boxplot(pc, {"y_label": "P", "title_prefix": "T", "primary_site": "SR"}, oc)
        h = oc.read_text(encoding="utf-8")
        check("site-scope: compare boxplot renders the auto-filter site selector",
              'id="auto_gf_site"' in h)
        check("site-scope: selector has reference/onboarding/both options",
              all(f'value="{v}"' in h for v in ("primary", "onboarding", "both")))
        check("site-scope: scope-gate + scope-independent per-site summary wired",
              "siteScope" in h and "siteSummary" in h and "_autoScopeLabel" in h and "autoEligible" in h)
        # Non-compare boxplot: no site selector at all (default reference-only).
        pn = Path(td) / "single.csv"
        with pn.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for freq in (100.0, 200.0):
                for i in range(12):
                    w.writerow(["Room", freq, round(10.0 + 0.05 * (i % 5), 4),
                                f"HarmonicNumber: 2  Serial Number: D{i:02d}", 20, -20])
        on = Path(td) / "single.html"
        pp.stat_boxplot(pn, {"y_label": "P", "title_prefix": "T"}, on)
        hn = on.read_text(encoding="utf-8")
        check("site-scope: non-compare boxplot has NO site selector",
              'id="auto_gf_site"' not in hn)
        # Shared control helper: renders the scope selector only when has_site_scope.
        cy = pp._af_control_html("x", "pv", "cl", has_site_scope=True, primary_site="SR")
        cn = pp._af_control_html("x", "pv", "cl")
        check("site-scope: _af_control_html renders selector + options when compare",
              'id="x_auto_site"' in cy and all(f'value="{v}"' in cy for v in ("primary", "onboarding", "both")))
        check("site-scope: _af_control_html omits selector when not compare",
              "auto_site" not in cn)
        # Undo-button label: default is the shared-GF text; histogram overrides it
        # (its clear affects only this view, not a shared Global Filter).
        c_hist = pp._af_control_html("h", "pv", "clearHistAuto",
                                     clear_label="Clear auto-exclusion",
                                     clear_title="Remove every auto-filter exclusion on this histogram")
        check("undo-label: default _af_control_html button says 'Clear global filter'",
              ">Clear global filter</button>" in cn)
        check("undo-label: histogram override button says 'Clear auto-exclusion', not global-filter",
              ">Clear auto-exclusion</button>" in c_hist
              and ">Clear global filter</button>" not in c_hist)
        # Shared-engine view (stat_summary) carries the selector + scope engine.
        oss = Path(td) / "ss.html"
        pp.stat_summary(pc, {"y_label": "P", "title_prefix": "T", "primary_site": "SR"}, oss)
        hss = oss.read_text(encoding="utf-8")
        check("site-scope: compare stat_summary renders its site selector + scope engine",
              'id="stat_auto_site"' in hss and "_afScopeLabel" in hss and "siteScope" in hss and "siteSummary" in hss)
        oss1 = Path(td) / "ss1.html"
        pp.stat_summary(pn, {"y_label": "P", "title_prefix": "T"}, oss1)
        check("site-scope: non-compare stat_summary has NO site selector",
              'id="stat_auto_site"' not in oss1.read_text(encoding="utf-8"))


def test_auto_filter_stat_summary():
    """Server contract for the stat_summary 'Auto-filter bad DUTs' feature (the
    first population view on the shared engine). Pins the controls, the shared
    _AUTO_FILTER_SHARED_JS engine, the per-view gathering + GF write, and the
    Clear-global-filter undo."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ss.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for freq in (100.0, 200.0):
                for i in range(12):
                    w.writerow(["Room", freq, round(10.0 + 0.05 * (i % 5), 4),
                                f"HarmonicNumber: 2  Serial Number: D{i:02d}", 20, -20])
        out = Path(td) / "ss.html"
        pp.stat_summary(p, {"y_label": "P", "title_prefix": "T"}, out)
        h = out.read_text(encoding="utf-8")
        check("stat auto-filter renders basis + level selects + panel",
              all(s in h for s in ('id="stat_auto_basis"', 'id="stat_auto_level"', 'id="stat_auto_panel"')))
        check("stat auto-filter basis/level options present (incl iqr/dmad)",
              all(f'value="{v}"' in h for v in ("dist", "iqr", "dmad", "spec", "tll", "off", "conservative", "moderate", "aggressive")))
        check("shared auto-filter engine present (_AF_LEVELS/_afCompute/_afRisk/_afMedian/_afPreview/_afScorer)",
              all(s in h for s in ("_AF_LEVELS", "_afCompute", "_afRisk", "_afMedian", "_afPreview", "_afScorer", "_afPeerBasis")))
        check("stat per-view gathering + GF write + clear present",
              all(s in h for s in ("_statAutoBadPoints", "_statMergeGf", "clearStatGlobalFilter", "STAT_AF")))
        check("stat auto-filter magnitude is MAD-robust (1.4826 + 3.5 cutoff)",
              "1.4826" in h and "3.5" in h)
        check("shared systemic guard is per-direction (keys include o.dir)",
              "o.freqLabel+'|'+o.dir" in h)
        check("stat auto-filter writes point-precise GF keys (base serial + condKey + Room + freq_label)",
              "_statBaseSerial(d.s)+'||'+_condKeyForStat" in h)
        check("stat Clear-global-filter button rendered",
              "clearStatGlobalFilter()" in h and "Clear global filter" in h)
        check("stat_summary Workflow & Recommendations present (button/panel/ctx/adapters)",
              all(s in h for s in ('id="stat_wf_btn"', 'id="stat_wf_panel"', "statRunWorkflow",
                                   "toggleStatWorkflow", "buckets:function", "_afRenderWorkflow")))
        check("stat_summary print-to-PDF report present (statGenReport)",
              "statGenReport" in h and "_afGenerateReport" in h and "Generate PDF report" in h)
        # Subpopulation advisory rolled out to stat_summary: STAT_AF supplies
        # subpopSlices (budget from unc_hi, station null), rendered by the shared
        # _spAdvisoryHtml via _afRenderWorkflow's ctx.subpopSlices hook.
        check("stat_summary supplies subpopSlices to STAT_AF (subpop advisory wired)",
              "subpopSlices:function()" in h and "_spDetect" in h and "_spAdvisoryHtml" in h)
        check("stat_summary remove-auto wired (statRemoveAuto + removeFn + reloadGf)",
              "statRemoveAuto" in h and "removeFn:'statRemoveAuto'" in h and "_afRemoveApplied" in h)


def test_room_only_default_views():
    """Room-only data defaults to scatter+boxplot+reference+summary+stat_summary
    (2026-09-15: summary/stat_summary are useful Room-only too, no longer opt-in),
    and NEVER auto-adds env_coverage/distribution (they need non-Room deltas).
    room_only_full_views is an accepted no-op now."""
    import csv as _csv
    import padb_v2 as _v2
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "room.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for freq in (100.0, 200.0):
                for i in range(8):
                    w.writerow(["Room", freq, round(10.0 + 0.05 * (i % 4), 4),
                                f"HarmonicNumber: 2  Serial Number: D{i:02d}", 20, -20])
        outdir = Path(td) / "out"
        outdir.mkdir()
        gen = _v2.generate_report(p, {"title_prefix": "T", "results_dir": "out", "publish_to": ""}, outdir)
        stems = {g.stem for g in gen}
        for v in ("scatter", "boxplot", "reference", "summary", "stat_summary"):
            check(f"room-only default includes {v}", f"T_{v}" in stems, f"stems={sorted(stems)}")
        check("room-only default EXCLUDES env_coverage/distribution",
              "T_env_coverage" not in stems and "T_distribution" not in stems, f"stems={sorted(stems)}")


def test_auto_filter_rollout_summary_envcov():
    """Server contract: the auto-filter engine + Workflow + PDF report rolled out to
    summary and env_coverage (shared _AUTO_FILTER_SHARED_JS via SUM_AF / EC_AF)."""
    import csv as _csv
    import padb_v2 as _v2
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mt.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group"])
            for temp, off in (("Room", 0.0), ("0.0 Deg C", -1.5), ("55.0 Deg C", 2.0)):
                for freq in (100.0, 200.0):
                    for i in range(12):
                        w.writerow([temp, freq, round(10.0 + 0.05 * (i % 5) + off, 4),
                                    f"HarmonicNumber: 2  Serial Number: D{i:02d}"])
        df = _v2.load_scatter(p, {})
        for label, render, prefix, ctx in (
            ("summary", _v2.render_summary, "sum", "SUM_AF"),
            ("env_coverage", _v2.render_env_coverage, "ec", "EC_AF"),
        ):
            out = Path(td) / (label + ".html")
            render(df.copy(), {"y_label": "P", "title_prefix": "T", "results_dir": label}, out)
            h = out.read_text(encoding="utf-8")
            check(f"{label} auto-filter control + options present",
                  all(s in h for s in (f'id="{prefix}_auto_basis"', f'id="{prefix}_auto_level"',
                                       'value="iqr"', 'value="dmad"')))
            check(f"{label} shared engine + ctx + wrappers present",
                  all(s in h for s in ("_AF_LEVELS", "_afScorer", "_afRunWorkflow", ctx,
                                       f"{prefix}RunWorkflow", f"{prefix}GenReport")))
            check(f"{label} workflow button + panels present",
                  all(s in h for s in (f'id="{prefix}_wf_btn"', f'id="{prefix}_wf_panel"', f'id="{prefix}_auto_panel"')))
            check(f"{label} print-to-PDF report present",
                  "_afGenerateReport" in h and "Generate PDF report" in h)
            check(f"{label} clear-global-filter present",
                  f"clear{prefix.capitalize()}GlobalFilter" in h or ("clearSumGlobalFilter" if prefix == "sum" else "clearEcGlobalFilter") in h)
            # Subpopulation advisory rollout: summary + env_coverage wired
            # (SUM_AF/EC_AF.subpopSlices); histogram still pending.
            check(f"{label} supplies subpopSlices to {ctx} (subpop advisory wired)",
                  "subpopSlices:function()" in h and "_spDetect" in h and "_spAdvisoryHtml" in h)
            if label == "env_coverage":
                # env_coverage adds the DEnv-drift/Room basis picker (default drift).
                check("env_coverage subpop basis picker present (drift default + room)",
                      "subpopBasisControlHtml:function()" in h and "_ecSubpopBasis" in h
                      and "value=\"drift\"" in h and "value=\"room\"" in h)


def test_auto_filter_histogram():
    """Server contract: auto-filter engine on histogram, which has NO Global Filter
    -- it uses a per-measurement-index exclusion (_hAutoExcl) consulted by
    _hFilteredIdx, with ctx text overridden away from 'Global Filter'."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "h.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Group", "Switching Speed (us)", "Upper Limit"])
            for _ in range(3):
                for i in range(12):
                    w.writerow([f"Port: RF1  Serial Number: D{i:02d}", round(10.0 + 0.05 * (i % 5), 4), 20.0])
        out = Path(td) / "h.html"
        pp.histogram(p, {"title_prefix": "T", "results_dir": "h"}, out)
        h = out.read_text(encoding="utf-8")
        check("histogram auto-filter control + options present",
              all(s in h for s in ('id="h_auto_basis"', 'id="h_auto_level"', 'value="iqr"', 'value="dmad"')))
        check("histogram uses measurement-index exclusion (_hAutoExcl in _hFilteredIdx)",
              "_hAutoExcl" in h and "_hAutoExcl.has(i)" in h)
        check("histogram engine + ctx + wrappers present",
              all(s in h for s in ("_afScorer", "_afRunWorkflow", "HIST_AF", "histRunWorkflow", "histGenReport", "clearHistAuto")))
        check("histogram workflow button + panels + report present",
              all(s in h for s in ('id="h_wf_btn"', 'id="h_wf_panel"', 'id="h_auto_panel"', "_afGenerateReport")))
        check("histogram ctx text overridden away from Global Filter (no-GF view)",
              "applyNoun:'the auto-exclusion'" in h and "undoHint:'Clear auto-exclusion'" in h)
        # Subpopulation advisory: histogram is single-bucket (no freq axis) -- one slice
        # per dim-combo, per-DUT means, opts:{min_buckets:1}. Completes the 5-view rollout.
        check("histogram supplies subpopSlices to HIST_AF (single-bucket, min_buckets:1)",
              "subpopSlices:function()" in h and "_spDetect" in h and "min_buckets:1" in h)


# ---------------------------------------------------------------------------
# Build-time multi-view PDF report -- padb_pdf_report (2026-09-14)
# Browser-free pins: every 6-view analytic view has a print profile, filenames
# map back to the right view, the cover page builds, and the env check reports a
# reason string rather than raising. The actual headless-print path is exercised
# separately (needs Playwright + Chromium), not in this pure-Python gate.
# ---------------------------------------------------------------------------
def test_pdf_report_contract():
    import padb_pdf_report as R
    import padb_v2 as v2
    # Every view padb_v2 renders in the 6-view suite must have a print profile.
    for view in v2._VIEW_FN:
        check(f"pdf report: print profile exists for '{view}'",
              view in R.PRINT_PROFILES, f"missing {view}")
    # Each profile names a plot div and (for table views) a panel to reveal.
    for slug, prof in R.PRINT_PROFILES.items():
        check(f"pdf report: profile '{slug}' has a plot id",
              bool(prof.get("plot")))
    # Filename -> view recovery, incl. longest-match (env_coverage vs summary).
    check("pdf report: _view_of recovers env_coverage",
          R._view_of(Path("SG6311A_Foo_env_coverage.html")) == "env_coverage")
    check("pdf report: _view_of recovers stat_summary (not summary)",
          R._view_of(Path("SG6311A_Foo_stat_summary.html")) == "stat_summary")
    check("pdf report: _view_of returns None for a non-view file",
          R._view_of(Path("index.html")) is None)
    # Cover page builds non-trivial HTML from meta.
    cov = R._cover_html({"title": "T", "rows": 100, "generated": "now"}, ["Scatter", "Box Plots"])
    check("pdf report: cover HTML includes title + contents",
          "<h1>T</h1>" in cov and "Box Plots" in cov and "Methodology" in cov)
    # Environment check returns a (bool, str) tuple and never raises.
    ok, reason = R.check_environment()
    check("pdf report: check_environment returns (bool, reason)",
          isinstance(ok, bool) and isinstance(reason, str) and reason != "")
    # Print CSS must keep wide tables inside the page (2026-09-17): let cells wrap
    # (override the live .stbl nowrap), cap table width, and un-clip scroll boxes --
    # otherwise a many-column compare table runs past the right margin in the PDF.
    css = R._REPORT_HIDE_CSS
    check("pdf report: print CSS wraps table cells (overrides nowrap)",
          "white-space: normal !important" in css
          and ("overflow-wrap" in css or "word-break" in css))
    check("pdf report: print CSS caps table width + un-clips scroll boxes",
          "max-width: 100% !important" in css and "overflow: visible !important" in css)

    # _write_index links a "<prefix>_report.pdf" when present, and omits it when not.
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "Foo_scatter.html").write_text("x", encoding="utf-8")
        (d / "Foo_boxplot.html").write_text("x", encoding="utf-8")
        v2._write_index(d, "Foo", [d / "Foo_scatter.html", d / "Foo_boxplot.html"], {"index_title": "Foo"})
        idx_no = (d / "index.html").read_text(encoding="utf-8")
        check("pdf report: index has no PDF link when no report file exists",
              "_report.pdf" not in idx_no)
        (d / "Foo_report.pdf").write_text("%PDF-1.4", encoding="utf-8")
        v2._write_index(d, "Foo", [d / "Foo_scatter.html", d / "Foo_boxplot.html"], {"index_title": "Foo"})
        idx_yes = (d / "index.html").read_text(encoding="utf-8")
        check("pdf report: index links <prefix>_report.pdf when present",
              'href="Foo_report.pdf"' in idx_yes and 'class="pdf"' in idx_yes)
    # Histogram-only dir: the "_histogram" suffix must be stripped for the group
    # key so the "<prefix>_report.pdf" link matches (regression -- histogram
    # isn't in _VIEW_FN, so it was missed by _index_group_key).
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "Bar_A_histogram.html").write_text("x", encoding="utf-8")
        (d / "Bar_B_histogram.html").write_text("x", encoding="utf-8")
        (d / "Bar_A_report.pdf").write_text("%PDF-1.4", encoding="utf-8")
        v2._write_index(d, "Bar", [d / "Bar_A_histogram.html", d / "Bar_B_histogram.html"],
                        {"index_title": "Bar"})
        idx = (d / "index.html").read_text(encoding="utf-8")
        check("pdf report: histogram group key strips _histogram (no suffix in header)",
              "<h3>Bar A</h3>" in idx and "Bar A histogram" not in idx)
        check("pdf report: histogram job links its report.pdf",
              'href="Bar_A_report.pdf"' in idx)
        check("pdf report: histogram analytic without a report has no PDF link",
              'href="Bar_B_report.pdf"' not in idx)


def test_reference_stats():
    """Server contract for the Reference Statistics view (padb_refstats): a dataset
    with a pod status field ('Test Run Status') detects STATUS_COL and renders the
    overall/pareto/group panels + column-oriented embed; a dataset without a status
    field still builds (STATUS_COL null, pass/fail falls back to limits)."""
    import csv as _csv
    import padb_refstats as _rs
    with tempfile.TemporaryDirectory() as td:
        # (1) with a pod status field
        ps = Path(td) / "st.csv"
        with ps.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for st in ("P", "F"):
                for freq in (100.0, 200.0):
                    for i in range(10):
                        w.writerow(["Room", freq, round(10.0 + 0.1 * i, 3),
                                    f"Test Run Status: {st}  Serial Number: D{i:02d}", 20, -20])
        df = pp._parse_group_fields(pp._load_scatter_for_stats(ps))
        h = _rs._build_reference_stats_html(df, {"title": "T", "y_label": "P"}, "T")
        check("reference: status field detected as STATUS_COL",
              '"_grp_Test Run Status"' in h and "STATUS_COL" in h)
        check("reference: overall/pareto/group panels + column embed + JS present",
              all(s in h for s in ('id="overall"', 'id="pareto"', 'id="grouptbl"', "var COLS=", "GROUP_COLS", "_pfMode")))
        check("reference: group-by + freq filter + override-limit controls present",
              all(s in h for s in ('id="groupby"', 'id="f_lo"', 'id="ovr_hi"', 'id="ovr_lo"')))
        # Increment 2: value-distribution histogram + outlier-points table (+ export).
        check("reference: distribution + outlier panels present (increment 2)",
              all(s in h for s in ('id="distplot"', 'id="outliers"')))
        check("reference: outlier table wiring present (fence bounds + export + real-outlier list)",
              all(s in h for s in ("oflo:loF", "ofhi:hiF", "_refOutliers", "exportOutliers")))
        check("reference: distribution is pass/fail-coloured overlay when a mode exists",
              "barmode:'overlay'" in h and "'Pass'" in h and "'Fail'" in h)
        # Increment 2.1: honour the shared cross-view Global Filter (same key format
        # every other view uses) so a DUT cleaned elsewhere drops from this view too.
        check("reference: honours the shared Global Filter (key + apply toggle + matcher)",
              all(s in h for s in ('var GF_KEY="padb_v2_excluded_', "var GF_DIMS=",
                                   'id="ref_gf_chk"', "_refGfExcl", "_loadRefGlobalFilter")))
        # Serial embedded from the Group-text serial col (the common case) so GF has
        # a base serial to match on -- df["Serial"] is empty here.
        check("reference: effective serial embedded for GF (from Group-text)",
              '"Serial":' in h)
        # Increment 3: auto-filter before/after impact preview, reusing the shared
        # engine (identical methodology to the plot views), preview-only (no GF write).
        check("reference: auto-filter impact panel + selectors present (increment 3)",
              all(s in h for s in ('id="ref_af_basis"', 'id="ref_af_level"', 'id="ref_af_impact"')))
        check("reference: impact preview reuses the shared auto-filter engine",
              all(s in h for s in ("_afScorer", "_afCompute", "_afAnalyze", "_afRecommend", "var _AF_LEVELS=")))
        check("reference: impact ctx + refresh wiring present",
              all(s in h for s in ("var REF_AF=", "_refImpactRefresh", "_refBadPoints", "_refUseRec")))
        # (2) no status field, limits present -> builds, STATUS_COL null
        pl = Path(td) / "nolim.csv"
        with pl.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for freq in (100.0, 200.0):
                for i in range(10):
                    w.writerow(["Room", freq, round(10.0 + 0.1 * i, 3),
                                f"HarmonicNumber: 2  Serial Number: D{i:02d}", 20, -20])
        df2 = pp._parse_group_fields(pp._load_scatter_for_stats(pl))
        h2 = _rs._build_reference_stats_html(df2, {"title": "T", "y_label": "P"}, "T")
        check("reference: no-status dataset still builds with STATUS_COL null",
              "var STATUS_COL=null;" in h2 and 'id="pareto"' in h2)


def test_scatter_draw_modes() -> None:
    """Scatter Draw modes for phase-noise-style plots (2026-09-15, user request):
    Markers / Lines / Lines+markers / Vertical(per-freq sticks), and a default group-by
    of Serial (one curve per DUT) for a real swept measurement with a modest DUT count.
    'lines' collapses repeat measurements to one mean point per x (unified to mean
    2026-09-16); 'sticks' draws a vertical min..max segment per frequency (right for
    discrete spurs). The Smooth (spline) toggle was REMOVED 2026-09-22 (David) -- a control
    with no obvious effect; smoothing is now simply always-on (spline by default, no toggle)."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pn.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Frequency (MHz)", "Value (dBc/Hz)", "Group"])
            # 6 DUTs, 8 offsets each, 2 repeats/offset -> a real sweep with repeats
            for s in range(6):
                for fo in (1.0, 10.0, 100.0, 1e3, 1e4, 1e5, 1e6, 1e7):
                    for rep in (0, 1):
                        w.writerow([fo, -80 - 5 * (fo > 1e3) + rep * 0.2,
                                    f"Serial Number: D{s:02d}"])
        out = Path(td) / "pn.html"
        pp.accuracy_vs_freq(p, {"y_label": "Value (dBc/Hz)", "title_prefix": "T"}, out)
        h = out.read_text(encoding="utf-8")
        check("scatter: Draw selector has markers/lines/lines+markers/sticks",
              'id="drawmode"' in h and 'value="markers"' in h and 'value="lines"' in h
              and 'value="lines+markers"' in h and 'value="sticks"' in h)
        check("scatter: Smooth toggle REMOVED; spline is now always-on (no toggle)",
              'id="smooth_chk"' not in h and "?'spline':'linear'" not in h
              and "var _lineShape='spline';" in h)
        check("scatter: lines mode collapses repeats to one mean point per x",
              "meanOf(byXl[x])" in h and "(mean of repeats)" in h
              and "median(byXl[x])" not in h)
        check("scatter: sticks mode draws vertical min..max segment per freq",
              "drawMode==='sticks'" in h and "sx.push(x,x,null)" in h)
        # This 6-DUT, 8-offset sweep should default the selected groupby <option> to
        # the serial-like dimension (one curve per DUT), not a lower-cardinality dim.
        import re as _re
        m = _re.search(r'<option value="[^"]*"\s+selected>([^<]*)</option>', h)
        check("scatter: default group-by option is serial-like (per DUT) for a swept dataset",
              bool(m) and any(kw in m.group(1).lower() for kw in ("serial", "unit id", "dut id", "s/n")),
              f"selected default option = {m.group(1) if m else '(none)'}")


def test_site_check_compare_basis() -> None:
    """Boxplot Site Population Check is FENCE-ONLY (2026-09-22, David-approved). The
    earlier selectable comparison basis (fence/spec/both) was removed: the spec/both
    modes overlapped with the datasheet pass/fail already shown by the Data filter +
    Statistics Table and were being misread as governing the main table. The check now
    only ever answers the population-shift question (primary k*IQR fence) -- the JS
    reads no selector and defaults siteBasis to 'fence'. Teeth: re-adding the selector
    trips the 'no basis selector' pin. Button shown only on a compare page
    (PRIMARY_SITE set)."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        pc = Path(td) / "cmp.csv"
        with pc.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for site in ("SR", "AMC"):
                for freq in (100.0, 200.0):
                    for i in range(8):
                        v = round(10.0 + 0.05 * (i % 4), 4)
                        if site == "AMC" and i == 0:
                            v = 25.0  # over the +20 upper limit -> fails spec, may sit in SR fence
                        w.writerow(["Room", freq, v,
                                    f"Serial Number: {site}D{i:02d}  Site: {site}", 20, -20])
        oc = Path(td) / "cmp.html"
        pp.stat_boxplot(pc, {"y_label": "P", "title_prefix": "T", "primary_site": "SR"}, oc)
        h = oc.read_text(encoding="utf-8")
        check("site check: fence-only -- NO comparison-basis selector on a compare page",
              'id="box_site_basis"' not in h and 'Site&nbsp;check&nbsp;vs' not in h)
        check("site check: Site Population Check button still present on a compare page",
              'id="box_site_toggle_btn"' in h and "Site Population Check" in h)
        check("site check: JS defaults siteBasis to fence with no selector element",
              "(document.getElementById('box_site_basis')||{}).value||'fence'" in h)
        # A non-compare boxplot has no PRIMARY_SITE -> no button and no selector.
        pn = Path(td) / "nc.csv"
        with pn.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for freq in (100.0, 200.0):
                for i in range(8):
                    w.writerow(["Room", freq, 10.0 + 0.05 * i, f"Serial Number: D{i:02d}", 20, -20])
        on = Path(td) / "nc.html"
        pp.stat_boxplot(pn, {"y_label": "P", "title_prefix": "T"}, on)
        ontext = on.read_text(encoding="utf-8")
        check("site check: no basis selector or Site button on a non-compare page",
              'id="box_site_basis"' not in ontext and 'id="box_site_toggle_btn"' not in ontext)


def test_box_control_groups() -> None:
    """Below-plot boxplot controls are organized into three labeled groups
    (2026-09-22, David-approved): Analysis (Statistics Table / Outlier / Delta /
    Site Population Check / Workflow), Global Filter (all GF set/clear/export/import
    / Copy PADB Filter / GF Mode), and View (Autoscale Y / Clear everything). Teeth:
    the three uppercase group labels must render in order, Site Population Check must
    sit inside Analysis (before the Global Filter label), GF controls inside Global
    Filter, and Autoscale Y + Clear everything inside View."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for site in ("SR", "AMC"):
                for freq in (100.0, 200.0):
                    for i in range(6):
                        w.writerow(["Room", freq, 10.0 + 0.05 * i,
                                    f"Serial Number: {site}D{i:02d}  Site: {site}", 20, -20])
        oc = Path(td) / "b.html"
        pp.stat_boxplot(p, {"y_label": "P", "title_prefix": "T", "primary_site": "SR"}, oc)
        h = oc.read_text(encoding="utf-8")
        iA = h.find(">Analysis</span>")
        iG = h.find(">Global Filter</span>")
        iV = h.find(">View</span>")
        check("box control groups: Analysis/Global Filter/View labels render in order",
              -1 < iA < iG < iV)
        iSite = h.find("Site Population Check")
        check("box control groups: Site Population Check sits inside Analysis (before Global Filter)",
              iA < iSite < iG)
        iSetGf = h.find("Set filter as GF")
        iGfMode = h.find("GF Mode:")
        check("box control groups: GF set/mode controls sit inside Global Filter",
              iG < iSetGf < iV and iG < iGfMode < iV)
        iAuto = h.find('onclick="autoscaleY()"')
        iClrEvery = h.find('onclick="clearEverything()"')
        check("box control groups: Autoscale Y + Clear everything sit inside View",
              iV < iAuto and iV < iClrEvery)


def test_distribution_compare_room_only_site() -> None:
    """Distribution Absolute-mode Room must retain a Room-only NON-PRIMARY site in a
    cross-site compare (2026-09-22, David-reported). The env_serials nicety (thin Room
    to DUTs that also have non-Room data, so Room-abs matches env-abs populations) was
    unconditional -- it silently erased an onboarding site measured only at Room (real
    case: SR multi-temp vs AMC/MY-serial Room-only Close-In compare -- AMC never in
    env_serials -> dropped from every Room curve AND the Site Population Check). Gate:
    skip the restriction whenever a Site dimension is present. Single-site pods still
    thin a Room-only DUT (population-matching preserved). Teeth: the compare Room-abs
    must contain the Room-only site; the single-site case must still thin its
    Room-only DUT."""
    import csv as _csv, json as _json, re as _re
    import padb_v2 as _v2  # load_scatter promotes serial-in-Group -> Serial (env_serials teeth)

    def _room_abs(html):
        temps = _json.loads(_re.search(r"var TEMPS=(\[[^;]*?\]);", html).group(1))
        raw = _json.loads(_re.search(r"var RAW_ABS=(\[.*?\]);", html, _re.S).group(1))
        ri = temps.index("Room")
        sites, sers = set(), set()
        for sp in raw:
            cell = sp[ri]
            sers.update(cell.get("s") or [])
            for g in (cell.get("g") or []):
                m = _re.search(r"Site:\s*([^|]+?)(?:\s{2,}|$)", g)
                if m:
                    sites.add(m.group(1).strip())
        return sites, sers

    with tempfile.TemporaryDirectory() as td:
        # Compare: SR multi-temp (Room + 55C) + AMC (MY-serial) Room-only.
        pc = Path(td) / "cmp.csv"
        with pc.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for i in range(8):
                sn = f"US6508{i:04d}"
                for ts in ("Room", "55.0 Deg C"):
                    for fr in (100.0, 200.0):
                        w.writerow([ts, fr, -60.0 + 0.1 * i + (2 if ts != "Room" else 0),
                                    f"SpurType: CloseIn  Serial Number: {sn}  Site: SR", -50, -70])
            for i in range(6):
                sn = f"MY6625{i:04d}"
                for fr in (100.0, 200.0):
                    w.writerow(["Room", fr, -59.0 + 0.1 * i,
                                f"SpurType: CloseIn  Serial Number: {sn}  Site: AMC", -50, -70])
        d = _v2.load_scatter(pc, {})
        hc = pp._build_env_distribution_html(d, {"y_label": "P", "title": "cmp", "primary_site": "SR"}, "cmp")
        sites, _ = _room_abs(hc)
        check("distribution compare: Room-only non-primary site (AMC) retained in Absolute Room",
              "AMC" in sites and "SR" in sites)
        # Per-site split (2026-09-22, David-approved): a compare page offers a
        # "Split by site" checkbox (default on) so each site draws its own KDE curve
        # in Absolute mode (primary solid / others dashed), instead of pooling all
        # sites into one temperature curve. Behaviour verified live via Playwright
        # (Room -> Room.SR + Room.AMC traces); source-pinned here so it can't rot.
        check("distribution compare: 'Split by site' checkbox present + default on",
              'id="dist_split_site_chk" checked' in hc)
        check("distribution compare: site list embedded + split helper + per-site bucketing",
              '"AMC"' in _re.search(r"var DIST_SITE_VALS=(\[[^;]*\]);", hc).group(1)
              and "function _distSplitSite()" in hc
              and "raw.c['Site']" in hc)

        # Single-site: multi-temp + one Room-only DUT -> still thinned (no Site dim).
        ps = Path(td) / "single.csv"
        with ps.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for i in range(8):
                sn = f"US6508{i:04d}"
                for ts in ("Room", "55.0 Deg C"):
                    for fr in (100.0, 200.0):
                        w.writerow([ts, fr, -60.0 + 0.1 * i,
                                    f"SpurType: CloseIn  Serial Number: {sn}", -50, -70])
            for fr in (100.0, 200.0):  # Room-only DUT
                w.writerow(["Room", fr, -59.0, "SpurType: CloseIn  Serial Number: US65089999", -50, -70])
        d2 = _v2.load_scatter(ps, {})
        hs = pp._build_env_distribution_html(d2, {"y_label": "P", "title": "single"}, "single")
        _, sers = _room_abs(hs)
        check("distribution single-site: Room-only DUT still thinned (env_serials nicety preserved)",
              "US65089999" not in sers and any(s.startswith("US6508") for s in sers))
        check("distribution single-site: no 'Split by site' control (not a compare)",
              'id="dist_split_site_chk"' not in hs)


def test_scatter_blank_dim_not_dropped() -> None:
    """Cross-site compare scatter (+ every applyFilters copy) must NOT drop a row
    whose value for a condition dimension is blank (David 2026-09-22: a Harmonics
    SR-vs-AMC compare scatter showed only MY/AMC serials -- SR's rows carry a null
    'Test Event Status' that AMC records as P/F, and the per-column filter excluded
    every value not in the checkbox options; blank is never an option, so all SR
    rows were filtered out of the plot AND the table). Fix: blank value = dimension
    not applicable = no constraint (matching the boxplot's absent-dimension rule),
    applied to all applyFilters/group-filter copies. Behaviour verified live via
    Playwright (SR + AMC both survive); source-pinned across every copy here."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    guarded = src.count("if(v!==''&&allowed.indexOf(v)<0) return false;")
    unguarded = src.count("      if(allowed.indexOf(v)<0) return false;")
    check(f"scatter/filters: blank-dim guard in every applyFilters copy (>=4; found {guarded})",
          guarded >= 4)
    check("scatter/filters: no unguarded per-value exclusion remains (blank would drop a site)",
          unguarded == 0)


def test_summary_data_filter_rollout() -> None:
    """Q1 data-filter cleanup rolled out from the boxplot to summary + stat_summary
    (David 2026-09-22): the pass/fail axis is All / Passing only / Failing only, and
    the confusing 'Upper limit'/'Lower limit' radios are gone. Summary keeps the manual
    threshold as a SEPARATE always-on 'Hide conditions beyond' trim (independent of the
    pass/fail radio); stat_summary's manual limit becomes the single separate Spec
    override (stat_spec_hi/lo) feeding BOTH plot and table. Failing = exact complement
    of Passing. Verified live via Playwright (partition, complement, trim, override);
    source-pinned across both views here. Teeth: re-introducing a range_hi/range_lo
    radio-mode, or losing the Failing radio, trips this."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    # Both views: Failing-only radio present, old range radios gone, no range-mode logic.
    check("summary: Failing-only radio present", 'name="sum_flt" value="failing"' in src)
    check("stat_summary: Failing-only radio present", 'name="data_flt" value="failing"' in src)
    check("both views: old Upper/Lower-limit range radios removed",
          'name="sum_flt" value="range_hi"' not in src
          and 'name="sum_flt" value="range_lo"' not in src
          and 'name="data_flt" value="range_hi"' not in src
          and 'name="data_flt" value="range_lo"' not in src)
    check("both views: no residual range_hi/range_lo filter-mode logic",
          "flt.mode==='range_hi'" not in src and "flt.mode==='range_lo'" not in src)
    # Summary: always-on trim + failing complement.
    check("summary: trim is always-on (isFinite), independent of pass/fail radio",
          "var trimHi=isFinite(flt.yhi), trimLo=isFinite(flt.ylo);" in src
          and "if(flt.mode==='all'&&!trimHi&&!trimLo) return active;" in src)
    check("summary: Passing/Failing are POINT-granular via shared _sumFreqMatch",
          "function _sumFreqMatch(" in src
          and "function _sumModeKeepIdx(" in src
          and "return _sumModeKeepIdx(cd,stats,vis,flt.mode,sumPar).length>0;" in src)
    check("summary: plot + table share the per-point match rule (table==plot)",
          "idxs=_sumModeKeepIdx(cd,_stats,idxs,_sumMode,_sumParams);" in src
          and "if(_bcrMode==='failing'&&!_sumFreqMatch(cd,fi,'failing',params)) return;" in src)
    check("summary: point mode draws markers + skips the min-max fill band",
          "var _pointMode=(_sumMode==='passing'||_sumMode==='failing');" in src
          and "if(!_pointMode) traces.push({" in src)
    # stat_summary: failing complement + single Spec override feeds the plot.
    check("stat_summary: Failing = complement of Passing (TI within TLL)",
          "if(flt.mode==='failing') return !(r.pass_up&&r.pass_lo);" in src)
    check("stat_summary: manual Spec override routed from separate stat_spec inputs (plot+table)",
          "_statSpecEntry():{hi:null,lo:null}" in src
          and "params.spec_hi_override=_statMan.hi" in src
          and "params.spec_lo_override=_statMan.lo" in src)


def test_reference_busy_overlay() -> None:
    """Every interactive view shows a busy overlay while its embedded data parses/first
    renders. The reference view (padb_refstats.py) was the ONLY one without it (David
    2026-09-22). It now inserts _BUSY_OVERLAY_HTML before the data <script> and removes
    #padb_busy after the first update() via rAF (this view has no #plot for the shared
    PADB_busyHide poll to watch). Pinned so the one-view gap can't silently return."""
    ref = (HERE / "padb_refstats.py").read_text(encoding="utf-8")
    check("reference: imports + inserts _BUSY_OVERLAY_HTML",
          ref.count("_BUSY_OVERLAY_HTML") >= 2)
    check("reference: removes #padb_busy after first render (rAF, no #plot to poll)",
          "getElementById('padb_busy')" in ref
          and "removeChild(_b)" in ref and "requestAnimationFrame" in ref)


def test_af_apply_line_applied_aware() -> None:
    """The auto-filter 'Will auto-filter ... Apply' line is APPLIED-AWARE (David
    2026-09-22): auto-filter analyzes the raw population and ignores the GF, so r.auto
    stays the same size after Apply -- a persistent 'Apply -> add N DUTs' button sitting
    next to a 'Remove auto-filter' button told a contradictory 'already added, yet asking
    to add' story. _afApplyLineHtml compares the auto set's point-keys to the LIVE GF and
    renders one of three honest states: already-applied (green check, NO Apply button) /
    partially-applied ('Apply remaining') / nothing-applied. Shared by the generic
    _afPreview AND boxplot's own autoFilterPreview so the two can't diverge. Behaviour
    verified via Playwright (Apply -> line flips to the green check, button gone). Teeth:
    reverting either preview to the raw inline Apply line trips this."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    check("af: _afApplyLineHtml helper present with all three states",
          "function _afApplyLineHtml(" in src
          and "Auto-filter applied: " in src
          and "Apply remaining " in src)
    check("af: definition + both preview builders use _afApplyLineHtml (>=3)",
          src.count("_afApplyLineHtml(") >= 3)
    check("af: no raw inline 'Apply -> add' line left in a preview builder",
          'onclick="autoFilterApply()">Apply' not in src
          and "()\">Apply → add to '+_an+'" not in src)


def test_blank_dim_no_site_drop() -> None:
    """Blank/absent condition-dimension must never silently drop a row/condition
    (David 2026-09-22, surfaced by qa_crossview): a cross-site compare where one
    site records a dimension (e.g. Test Event Status) the other leaves null was
    erasing the whole other site in scatter, reference, summary, and stat_summary,
    because a blank value is never a checkbox option. Rule: blank/absent dimension
    = not applicable = no constraint. Pinned across EVERY per-dimension filter copy
    (source-contract teeth for the umbrella; the behavioural proof is qa_crossview).
    Teeth: reverting any copy to its unguarded form trips this."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    ref = (HERE / "padb_refstats.py").read_text(encoding="utf-8")
    # Guarded forms present:
    check("scatter/reference-family applyFilters: blank guard x4 (found >=4)",
          src.count("if(v!==''&&allowed.indexOf(v)<0) return false;") >= 4)
    check("distribution _distCondKeep: blank/null guard",
          "if(cv!==''&&cv!=null&&!cf.sel.has(cv)) return false;" in src)
    check("stat_summary getActiveConditions: absent-dim passes",
          "return m?(allowed.indexOf(m[1].trim())>=0):true;" in src)
    check("summary getActive: blank-dim passes",
          "return allowed.length>0&&(v===''||allowed.indexOf(v)>=0);" in src)
    check("summary matchesCurSel: blank/null-dim passes",
          "if(v!==null&&v!==''&&curDimSel[colId].indexOf(v)<0) return false;" in src)
    check("reference (padb_refstats) applyFilters: blank-dim passes",
          "if(v!==''&&!sel[c][String(v)])return false;" in ref)
    # Unguarded forms gone (the exact shapes that dropped a site):
    check("no unguarded stat_summary form (m&&allowed.indexOf)",
          "return m&&allowed.indexOf(m[1].trim())>=0;" not in src)
    check("no unguarded summary form (allowed.indexOf(v)>=0 w/o blank pass)",
          "return allowed.length>0&&allowed.indexOf(v)>=0;" not in src)
    check("no unguarded reference form (sel[c][String(v)] w/o blank pass)",
          "if(v==null)v=''; if(!sel[c][String(v)])return false;" not in ref)
    # The proactive behavioural proof lives in qa_crossview.py (browser tier): it
    # generates an adversarial compare (Room-only site + asymmetric dim), renders
    # every view, and asserts INV-SITE (no site dropped) + INV-PART. Pinned here so
    # the harness can't be silently deleted (mirrors the qa_jsrules gate pin).
    xv = HERE / "qa_crossview.py"
    check("qa_crossview cross-view invariant harness exists", xv.exists())
    if xv.exists():
        xvs = xv.read_text(encoding="utf-8")
        check("qa_crossview asserts INV-SITE/INV-PART + honest-exits 3 without a browser",
              "INV-SITE" in xvs and "INV-PART" in xvs and "sys.exit(3)" in xvs)


def test_stat_perpoint_passfail_pointwise() -> None:
    """stat_summary PER-POINT table: 'Passing/Failing only' filters by each point's OWN
    status, built over the pre-pass/fail conds (condsAll) rather than the per-frequency
    TI-vs-spec filter that governs the plot + grouped table. Otherwise 'Failing only'
    showed every point at a failing FREQUENCY -- mostly PASS/no-limit -- and Passing+Failing
    didn't partition All (a passing point at a failing frequency fell through both). David
    2026-09-23. Verified via Playwright (ALL=PASS+FAIL; PASSING=only pass; FAILING=only FAIL;
    partition holds). Source-pinned."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    check("stat per-point: rows filtered by each point's own status (failing/passing)",
          "if(_ppMode==='failing') pts=pts.filter(function(pt){return pt.st.t==='FAIL';});" in src
          and "else if(_ppMode==='passing') pts=pts.filter(function(pt){return pt.st.t!=='FAIL';});" in src)
    check("stat per-point: getFilteredCondsAndParams exposes pre-pass/fail condsAll",
          "var condsAll=conds;" in src and "condsAll:condsAll" in src)
    check("stat per-point: the per-point table is built from condsAll (partition holds)",
          "_statPerPointTable(condsAll||conds,params)" in src)


def test_summary_stat_perpoint_own_limit_only() -> None:
    """summary + stat_summary score each DUT-point against its OWN limit only -- never a
    fabricated fallback (David 2026-09-23). Two fabrications were removed: (a) the
    per-frequency aggregate spec_hi_list[fi] (borrowed from OTHER points at the same
    offset), and (b) the page-global HI_SPEC/LO_SPEC (one value misapplied to every
    offset of a swept measurement). Both marked genuinely limit-less, PADB-passed points
    (Test Event Status: P, null Upper Limit) as FAIL -- inflating summary's fail count to
    145/234 vs scatter's ~85. A point with no real per-point limit (and no manual Spec
    override/entry) is unscored ('-'), matching scatter's table (raw Upper/Lower_Limit
    only). Passing/Failing is per-POINT (was per-frequency TTL-vs-spec), exact-complement:
    Failing = true fails; Passing = pass + no-limit. Verified via Playwright on the EP6
    phase-noise compare (85 fails; failing-only all FAIL; partition holds). Source-pinned."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    import re
    def _fn(name):
        m = re.search(r"function " + re.escape(name) + r"\(", src)
        if not m: return ""
        i = m.start(); depth = 0; started = False
        for j in range(i, min(len(src), i + 4000)):
            c = src[j]
            if c == "{": depth += 1; started = True
            elif c == "}":
                depth -= 1
                if started and depth == 0: return src[i:j+1]
        return src[i:i+4000]
    sdl = _fn("_sumDutLimit"); stl = _fn("_statDutLimits")
    check("summary _sumDutLimit does not fabricate from HI_SPEC/LO_SPEC or spec_hi_list",
          bool(sdl) and "HI_SPEC" not in sdl and "LO_SPEC" not in sdl and "spec_hi_list" not in sdl)
    check("summary _sumDutLimit uses own upper_limit/spec_hi + manual override only",
          "pick('upper_limit')" in sdl and "pick('spec_hi')" in sdl and "ovHi" in sdl)
    check("stat_summary _statDutLimits does not fabricate from HI_SPEC/LO_SPEC",
          bool(stl) and "HI_SPEC" not in stl and "LO_SPEC" not in stl)
    check("stat_summary _statDutLimits keeps own limit/spec + manual entry",
          "d.upper_limit" in stl and "man.hi" in stl)
    # scatter: table + filter share ONE per-point rule (_scatRowFail), own limit/spec
    # only -- no page-global HI_SPEC/LO_SPEC. Before this, the filter fabricated fails
    # from HI_SPEC (156) while the table (own-limit) showed 85 (David 2026-09-23).
    srf = _fn("_scatRowFail")
    check("scatter _scatRowFail scores own Upper/Lower_Limit -> own Spec, no HI_SPEC",
          bool(srf) and "r.Upper_Limit" in srf and "r.Spec_Hi" in srf
          and "HI_SPEC" not in srf and "LO_SPEC" not in srf)
    check("scatter table status shares the filter rule (_scatterStatus calls _scatRowFail)",
          "var fail=_scatRowFail(r);" in src)
    # Raw-point views (scatter, boxplot, reference) honor PADB's recorded Test Event
    # Status for a point with NO numeric limit -- so limit-less points get PADB's P/F
    # verdict instead of '-' (David 2026-09-23). scatter gained this to match boxplot
    # (_condStatusFail) + reference. Aggregate views (summary/stat_summary) stay '-'
    # because a per-DUT mean has no single PADB verdict.
    check("scatter falls back to PADB status (_scatStatusFail) when no numeric limit",
          "function _scatStatusFail(r)" in src
          and "if(hi==null&&lo==null) return _scatStatusFail(r);" in src
          and "SCAT_STATUS_FIELD" in src)
    check("scatter emits the detected SCAT_STATUS_FIELD constant",
          'f"var SCAT_STATUS_FIELD=' in src and "scat_status_field = " in src)
    # per-point (not per-frequency TTL) pass/fail, exact-complement in both tables
    check("summary Passing/Failing is per-point via _sumFreqMatch (old _sumFreqPasses gone)",
          "function _sumFreqMatch(" in src and "function _sumFreqPasses(" not in src)
    check("summary per-point table filters rows by point status (exact complement)",
          "if(_ppMode==='failing') pts=pts.filter(function(pt){return pt.st.t==='FAIL';});" in src
          and "else if(_ppMode==='passing') pts=pts.filter(function(pt){return pt.st.t!=='FAIL';});" in src)


def test_locked_filters_crossview() -> None:
    """Cross-view LOCKED FILTERS (David 2026-09-23): save the common filters (condition
    dims, frequency range, pass/fail) on any view; other views auto-apply them on load.
    Same pattern as the Global Filter: localStorage, applied at load, loud banner +
    Clear + Export/Import (file:// fallback). The lock is a VIEW-AGNOSTIC object matched
    by dimension LABEL; apply is BEST-EFFORT (only values present here; a missing dim/value
    is ignored, never emptying the view). Covers ALL views: scatter/stat_summary/summary
    (the .fchk trio) + boxplot (per-condition longform) + distribution/env_coverage (delta
    views: dims+freq, no pass/fail) + histogram (dims+pass/fail, no freq; all/pass/fail
    vocabulary translated) + reference (dims+freq; embeds the standalone _LOCK_JS since it
    has no _COMMON_JS). Behaviourally verified by qa_crossview INV-LOCK (a scatter lock
    auto-applies on summary/stat_summary/boxplot/reference/distribution, best-effort on the
    delta-only env_coverage); histogram is non-swept so it's source-pinned + shares the
    proven adapter pattern. Source-pinned so the core + per-view adapters can't regress."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    # Shared core (in _COMMON_JS -> every view)
    for fn in ("function PADB_lockRegister(", "function PADB_lockGet(", "function PADB_lockSet(",
               "function PADB_lockApply(", "function PADB_lockSave(", "function PADB_lockClear(",
               "function PADB_lockInit(", "function PADB_lockRenderBar(",
               "function PADB_lockExport(", "function PADB_lockImport(",
               "function PADB_lockSetChecks(", "function PADB_lockReadChecks("):
        check(f"lock core present: {fn}", fn in src)
    check("lock stored in localStorage under a stable key",
          "PADB_LOCK_KEY='padb_v2_locked_filters'" in src)
    check("lock apply is BEST-EFFORT (no matching value here -> leave as-is, don't empty)",
          "if(!anyHere) return false;" in src)
    check("lock read locks only NARROWED dims (strict subset), not fully-open ones",
          "checked.length<boxes.length" in src)
    # Per-view adapters registered + auto-applied on load. All 8 views now:
    # scatter/stat_summary/summary/boxplot + distribution/env_coverage/histogram (padb_plots)
    # and reference (padb_refstats). Each registers a read/apply pair.
    for rd, ap in (("_avLockRead", "_avLockApply"), ("_ssLockRead", "_ssLockApply"),
                   ("_sumLockRead", "_sumLockApply"), ("_bxLockRead", "_bxLockApply"),
                   ("_distLockRead", "_distLockApply"), ("_ecLockRead", "_ecLockApply"),
                   ("_hLockRead", "_hLockApply")):
        check(f"view adapter registered: {rd}/{ap}",
              f"function {rd}(" in src and f"function {ap}(" in src
              and f"PADB_lockRegister({{read:{rd},apply:{ap}}})" in src)
    check("boxplot lock apply won't empty the view on a non-existent locked value",
          "if(m&&r.want[m[1].trim()]) r.applicable=true;" in src and "if(active.length){" in src)
    check("histogram translates its all/pass/fail select to the lock's all/passing/failing",
          "(v==='fail')?'failing':(v==='pass'?'passing':'all')" in src
          and "(o.passfail==='failing')?'fail':(o.passfail==='passing'?'pass':'all')" in src)
    check("7 padb_plots views auto-apply the lock on load (>=7 PADB_lockInit references)",
          src.count("PADB_lockInit") >= 7)
    # Lock core is a standalone _LOCK_JS constant so the reference view can embed it too.
    check("lock core extracted to _LOCK_JS and appended to _COMMON_JS",
          '_LOCK_JS = r"""' in src and '_COMMON_JS = _COMMON_JS + "\\n" + _LOCK_JS' in src)
    ref = (HERE / "padb_refstats.py").read_text(encoding="utf-8")
    check("reference embeds _LOCK_JS + registers its adapter",
          "_LOCK_JS" in ref and "function _refLockRead(" in ref and "function _refLockApply(" in ref
          and "PADB_lockRegister({read:_refLockRead,apply:_refLockApply})" in ref)
    # Serial Number / Port are condition dims on scatter/reference but DEDICATED filters
    # on the aggregate views; the lock routes them there so nothing is silently skipped
    # (David 2026-09-23). Serial matches on BASE form (port-qualified vs base serial).
    check("lock has serial/port routing helpers (split + apply + read + base serial match)",
          "function PADB_lockSplitSP(" in src and "function PADB_lockApplySP(" in src
          and "function PADB_lockReadSP(" in src and "function PADB_lockSetSerials(" in src)
    check("serial matches on base form (strips a trailing port-like suffix)",
          "_lockSerBase" in src and "replace(/_[A-Za-z]+\\d*$/,'')" in src)
    for ser, port, temp in (("box_ser_chk", "'box_port_chk'", "'box_env_chk'"),
                            ("ser_chk", "'ss_port_chk'", "'env_chk'"),
                            ("sum_ser_chk", "null", "'sum_temp_chk'"),
                            ("dist_ser_chk", "'dist_port_chk'", "'env_chk'"),
                            ("ec_ser_chk", "'ec_port_chk'", "'ec_temp_chk'")):
        check(f"an aggregate view routes serial/port/temp via PADB_lockApplySP({ser}/{port}/{temp})",
              f"PADB_lockApplySP(sp,'{ser}',{port},applied,skipped,{temp})" in src)
    # Temperature lives in a dedicated env-checkbox bar (not GROUP_COLS/COND_DIMS), so the
    # lock routes it like serial/port; scatter/reference handle it inline (David 2026-09-24:
    # "set temperature to room only ... apply did not work" -- temp wasn't captured/applied).
    check("lock split + read + apply cover Temperature",
          "temp=o.dims[k]" in src and "dims['Temperature']=ct" in src
          and "sp.temp!=null" in src)
    check("scatter reads temp from env_chk and routes it on apply",
          "PADB_lockReadSP(dims,null,null,'env_chk')" in src
          and "document.querySelectorAll('.env_chk'),o.dims[label]" in src)
    check("histogram keeps Port as a dim (only serial is dedicated) + routes serial",
          "sp.rest['Port']=sp.port" in src and "PADB_lockApplySP({serial:sp.serial,port:null},'hf_serial'" in src)
    check("reference routes serial-like to its dedicated Serial column (base match)",
          "PADB_lockSetSerials(document.querySelectorAll('.fchk[data-col=\"Serial\"]')" in ref)
    check("reference routes temperature to its dedicated Temperature column",
          "PADB_lockSetChecks(document.querySelectorAll('.fchk[data-col=\"Temperature\"]'),sp.temp)" in ref)
    # Boxplot "Add locked filters to GF" -- applies the saved lock here then captures that
    # slice into the Global Filter (David 2026-09-23).
    check("boxplot has an 'Add locked filters to GF' button + handler",
          "Add locked filters to GF</button>" in src and "onclick=\"_boxAddLockToGf()\"" in src
          and "function _boxAddLockToGf(" in src
          and "_bxLockApply(o)" in src and "setFilterAsGf();" in src)
    check("the shared Help (i) panel documents locked filters",
          "Locked filters</b>" in src and "Lock these filters" in src
          and "separate from the Global Filter" in src)
    # Naming clarity (David 2026-09-24): keep "Locked filters" but spell out that it means
    # ALL data-narrowing controls (conditions/frequency/temperature/serial-port/pass-fail),
    # so temperature/frequency reading as non-"filters" doesn't confuse newcomers.
    check("lock help + button clarify 'filters' = every data-narrowing control",
          "every control that narrows the data" in src
          and "Save ALL of this view" in src and "data filters" in src)
    # Draggable lock bar (David 2026-09-24: "sometimes blocks filter features"). Grip handle,
    # drag listener on the persistent bar element (survives innerHTML rewrites), position
    # persisted in localStorage across views. Lives in the shared _LOCK_JS -> every view + ref.
    check("lock bar is draggable via a grip handle with a persisted position",
          "class=\"padb_lock_drag\"" in src and "cursor:move" in src
          and "padb_v2_lock_pos" in src
          and "bar.addEventListener('mousedown'" in src)
    # Lock-apply freq: the plot AXIS must follow the locked range, not just the filter
    # (David 2026-09-24: "plot axis stayed full after a locked freq"). The 4 freq-x plot views
    # (scatter/stat_summary/summary/env_coverage) route the locked freq through their
    # setFreqBand, which sets sliders+text AND relayouts xaxis.range (log-aware). update()'s
    # _liveAxisRange otherwise preserves the old pinned (full) range.
    check("4 freq-x lock-applies route freq through setFreqBand (moves the axis, not just the filter)",
          src.count("setFreqBand(o.freq.lo,o.freq.hi);   // sets sliders+text AND relayouts the x-axis (coupled)") == 4,
          f"count={src.count('setFreqBand(o.freq.lo,o.freq.hi);')}")
    check("stat_summary + env_coverage setFreqBand gained a log-aware x-axis relayout",
          src.count("Plotly.relayout('plot',{'xaxis.range':_lx?[Math.log10(Math.max(loV,1e-9)),Math.log10(Math.max(hiV,1e-9))]:[loV,hiV]});") == 2)
    # distribution (value-KDE x) + boxplot (categorical x) have no continuous freq x-axis to
    # relayout, so they keep the direct NaN-safe freq set (empty-min/max safe: isFinite guard,
    # never Math.max(NaN,lo)). This is the residual home of the NaN-safe clamp.
    check("distribution + boxplot keep the direct NaN-safe freq clamp (no freq x-axis to relayout)",
          src.count("var _mn=parseFloat(s1.min);var lo=o.freq.lo!=null?(isFinite(_mn)?Math.max(_mn,o.freq.lo):o.freq.lo):_mn;") == 2
          and "parseFloat(s1.min),o.freq.lo)" not in src)  # old bare-clamp form fully removed


def test_named_band_segments_crossview() -> None:
    """Named-band segment step across the swept-x HTML views (David 2026-09-24): a band
    file (padb_bands.py) partitions the swept x into named chunks; every swept-x view's
    "Segment by" control gains a "Named bands" mode that reuses the existing
    segTab()/_segFilterCondDims()/setFreqBand() machinery -- so Filter->Plot->Tables stay
    coupled. Bands come from an auto-generated (or user-edited) sidecar next to the results,
    injected as `var NAMED_BANDS`. Shared helpers live in _COMMON_JS and every reference is
    typeof-guarded so the EXCLUDED legacy distribution() (no _COMMON_JS) is unaffected when
    the shared replace_all touches its duplicated segment functions. Behaviourally verified
    live on all 6 views (boxplot Filter->Plot->Table proven to the band's freqs); source- and
    module-pinned here so it can't silently regress. TEETH: strings unique to the feature."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    v2 = (HERE / "padb_v2.py").read_text(encoding="utf-8")
    # Shared band-segment core in _COMMON_JS.
    check("band-seg core defined + appended to _COMMON_JS",
          '_BANDSEG_JS = r"""' in src
          and '_COMMON_JS = _COMMON_JS + "\\n" + _LOCK_JS + "\\n" + _BANDSEG_JS' in src)
    for fn in ("function PADB_hasNamedBands(", "function PADB_bandSegs(",
               "function PADB_ensureBandOption("):
        check(f"band-seg helper present: {fn}", fn in src)
    check("PADB_bandSegs clamps bands to the data x-range (drops non-overlapping)",
          "Math.max(Number(b.lo),xmin)" in src and "Math.min(Number(b.hi),xmax)" in src)
    # NAMED_BANDS injected into exactly the 6 active swept-x views (via the shared GF_KEY line).
    check("NAMED_BANDS injected into the 6 active swept-x view constants",
          src.count("var NAMED_BANDS={json.dumps((cfg or {}).get('_named_bands') or [])}") == 6,
          f"count={src.count(chr(39)+'var NAMED_BANDS=')}")
    # Every _recomputeSpecSegments adds the option (typeof-guarded) + branches to bands.
    check("recompute adds the Named-bands option (typeof-guarded so legacy is safe)",
          "if(typeof PADB_ensureBandOption==='function') PADB_ensureBandOption();" in src)
    check("segment build branches to PADB_bandSegs when the key is 'bands' (guarded)",
          src.count("(_segKey==='bands'&&typeof PADB_bandSegs==='function')?PADB_bandSegs():") >= 6)
    check("default seg key falls back to 'bands' when no spec but bands exist (guarded)",
          "return (typeof PADB_hasNamedBands==='function'&&PADB_hasNamedBands())?'bands':'spec';" in src)
    check("segment label shows the band name when present",
          "(seg.name?seg.name+': ':'')" in src)
    # Hover help on the "Named bands" option noting the band file is user-customizable AND
    # where it lives (David 2026-09-24). Set on the option + reflected on the select while
    # selected; the location comes from NAMED_BANDS_PATH injected per report into all views.
    check("Named-bands option has a customizable-file hover tooltip with the file location",
          "function PADB_bandTip(" in src and "customizable band file" in src
          and "'\\nFile: '+NAMED_BANDS_PATH" in src
          and "o.title=tip;" in src
          and "sel.title=(sel.value==='bands')?PADB_bandTip():'';" in src)
    check("NAMED_BANDS_PATH injected into all 6 views + resolved in padb_v2",
          src.count("var NAMED_BANDS_PATH={json.dumps((cfg or {}).get('_named_bands_path') or '')}") == 6
          and 'cfg["_named_bands_path"] = str(_bpath) if _bpath else ""' in v2)
    # env_coverage/boxplot builders received a cfg param so they can inject NAMED_BANDS.
    check("env_coverage + boxplot builders take cfg (to inject NAMED_BANDS)",
          src.count("cfg: dict | None = None,") >= 2)
    # padb_v2 resolves bands (sidecar-or-auto) and ORs them into has_segments.
    check("padb_v2 resolves named bands via padb_bands.find_or_create_bands",
          "import padb_bands" in v2 and "padb_bands.find_or_create_bands(" in v2
          and 'cfg["_named_bands"] = _bands' in v2)
    # Auto-generation is OPT-IN (David 2026-09-24: don't drop a file into every results
    # folder). An existing sidecar always loads; creation is gated on the auto_bands key /
    # --auto-bands flag. TEETH: allow_create must be the flag, never a hardcoded True.
    check("padb_v2 gates auto-generation on the auto_bands opt-in (not hardcoded True)",
          '_allow_auto = bool(cfg.get("auto_bands", False))' in v2
          and "allow_create=_allow_auto)" in v2
          and 'cfg["auto_bands"] = True' in v2)  # --auto-bands CLI override
    vw = (HERE / "padb_viewer.py").read_text(encoding="utf-8")
    check("viewer gates auto-generation on --auto-bands (existing sidecar still loads)",
          "allow_create=args.auto_bands)" in vw and '"--auto-bands"' in vw)
    check("has_segments ORs named bands so the control shows even with no spec",
          'or bool(cfg.get("_named_bands"))' in src
          and 'or bool(cfg.get("_named_bands"))' in v2)
    # The guarded option-adder call reaches ALL 7 _recomputeSpecSegments copies (6 active
    # + the EXCLUDED legacy distribution(), which has no _COMMON_JS): the typeof guard is
    # what makes that shared replace_all safe there. If a future edit dropped the guard,
    # the legacy view would throw ReferenceError -- so pin the guarded form on every copy.
    check("all 7 _recomputeSpecSegments copies got the typeof-guarded option-adder",
          src.count("if(typeof PADB_ensureBandOption==='function') PADB_ensureBandOption();")
          == src.count("function _recomputeSpecSegments(){"))
    check("there are the expected 7 _recomputeSpecSegments copies (6 active + 1 legacy)",
          src.count("function _recomputeSpecSegments(){") == 7,
          f"count={src.count('function _recomputeSpecSegments(){')}")


def test_summary_group_by_serial() -> None:
    """summary view offers a 'Group by: Serial Number' option (David 2026-09-23) --
    a special per-DUT pooling entry (like boxplot's __serial__), NOT a parsed
    condition dim, injected only when >1 serial. getGroupedConditions branches on
    '__serial__' into _poolSumBySerial, which emits one virtual record per unit
    (dut_info length 1, labelled 'Serial Number: <s>' so the shared GF matcher can
    hide/focus it), pooling that DUT's per-frequency value across the selected
    conditions. Verified via Playwright on a real compare page (20 serials -> 20
    single-DUT groups, band = each unit's cross-condition spread, no console
    errors). Source-pinned so the option + per-DUT path can't silently regress."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    check("summary injects a __serial__ Group-by option when >1 serial",
          '<option value="__serial__">Serial Number</option>' in src
          and "if len(all_sum_serials) > 1:" in src)
    check("summary getGroupedConditions branches on __serial__ into _poolSumBySerial",
          "cols.indexOf('__serial__')>=0" in src and "return _poolSumBySerial(active,dims);" in src)
    check("summary _poolSumBySerial emits one single-DUT record per serial, GF-labelled",
          "function _poolSumBySerial(active,dims)" in src
          and "dut_info:[{s:g.serial}]" in src
          and "lblParts.push('Serial Number: '+g.serial);" in src)
    check("summary serial pool respects the live serial filter (deselected = not emitted)",
          "if(_serFlt&&!_selSet[serial]) return;" in src)


def test_systemic_label_covers_batch() -> None:
    """The auto-filter 'systemic' classification (a DUT whose outliers are shared with other
    DUTs at the same frequency/direction -> never auto-removed) must NOT editorialize toward a
    station/fixture artifact: a shared multi-DUT pattern can equally be a REAL population defect
    (a bad component lot / common circuit-build issue), and the values alone can't distinguish
    them (David 2026-09-23, from the US65080433 example). The reason string (shared + boxplot
    copies), the triage label (3 copies), the workflow summary, and the control tooltip now name
    BOTH interpretations. Teeth: the old station-only wording must be gone."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    check("systemic: old 'likely station/systemic, not one bad DUT' reason removed",
          "likely station/systemic, not one bad DUT" not in src)
    check("systemic: old 'Likely station/systemic' triage label removed",
          "Likely station/systemic" not in src)
    check("systemic: reason names a common cause across DUTs + a population defect (bad lot)",
          "a COMMON CAUSE across DUTs" in src and "bad component lot" in src)
    check("systemic: triage label is neutral (station or batch)",
          "Common cause (station or batch)" in src)
    check("systemic: workflow summary + tooltip name a population defect / bad batch",
          "bad batch" in src and "population defect" in src)


def test_jsrules_behavioral_gate_present() -> None:
    """The behavioral cross-view gate qa_jsrules.py executes the SHIPPED shared JS
    (_COMMON_JS + shared panel) under Playwright and asserts the pass/fail + fence
    truth table AND that _spSpecClass behaviorally agrees with PADB_specClass -- so,
    since every view delegates to those (source-pinned above), all views agree by
    construction. Browser-tier (honest-exits 3 when no browser is reachable, as in
    this sandbox); pinned here so the behavioral battery can't silently rot.
    Verified live 14/14 via the in-app browser (2026-09-15)."""
    p = HERE / "qa_jsrules.py"
    check("qa_jsrules.py behavioral gate exists", p.exists())
    if not p.exists():
        return
    s = p.read_text(encoding="utf-8")
    check("qa_jsrules executes the shipped _COMMON_JS + shared panel",
          "padb_plots._COMMON_JS" in s and "padb_plots._SITE_PANEL_SHARED_JS" in s)
    check("qa_jsrules battery covers spec-class + fence + cross-view agreement",
          "PADB_specClass(" in s and "PADB_fence(" in s
          and "_spSpecClass agrees with PADB_specClass" in s)
    check("qa_jsrules is an honest browser-tier gate (exit 3 when unavailable)",
          "sys.exit(3)" in s)


def test_plotly_api_lint_and_render_guards() -> None:
    """RESILIENCE (2026-09-15): (a) a Plotly-API lint so a future version bump can't
    silently break rendering again -- the bare-string axis title dropped by Plotly
    3.6 was exactly that class; also flags deprecated 'titlefont' and bare-string
    colorbar titles. (b) A render-guard audit: every panel renderer that builds HTML
    into a panel must wrap its body in try/catch so a runtime error surfaces inline
    instead of leaving a stale/blank panel (the failure mode the null.toFixed crash
    would have caused if it weren't in a guarded render)."""
    import re
    for fn in ("padb_plots.py", "padb_v2.py"):
        s = (HERE / fn).read_text(encoding="utf-8")
        bad_axis = re.findall(r"(?:xaxis|yaxis)\d*:(?:Object\.assign\()?\{title:(?!\{)", s)
        check(f"plotly-lint {fn}: no bare-string axis titles", not bad_axis, str(bad_axis[:4]))
        check(f"plotly-lint {fn}: no deprecated 'titlefont' (use title.font)", "titlefont" not in s)
        bad_cb = re.findall(r"colorbar:\{[^}]*title:(?!\{)[^,}]", s)
        check(f"plotly-lint {fn}: no bare-string colorbar titles", not bad_cb, str(bad_cb[:4]))
    ps = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    # Every HTML-building panel renderer exists and has an error-surfacing catch.
    for fnname in ("updateStatsTable", "updateStatPanel", "updateSitePanel",
                   "updateEcSitePanel", "updateDistSitePanel"):
        check(f"render-guard: {fnname} present", ("function " + fnname + "(") in ps)
    check("render-guard: Statistics Table renderers catch+surface errors",
          "Error building Statistics Table" in ps and "Error building statistics table" in ps)
    check("render-guard: every Site Population Check renderer catches+surfaces (>=4)",
          ps.count("Error building Site Population Check") >= 4)


def test_common_prelude_and_feature_registry() -> None:
    """HARDENING TRACKER (2026-09-15): the shared JS prelude (_COMMON_JS) that gives
    every view a SINGLE source of truth for cross-view rules (PADB_num / PADB_isFail)
    must be defined once and injected into EVERY view -- and pure pass/fail sites must
    route through it. Plus a cross-view feature REGISTRY: each cross-view marker must
    appear across the builders, so dropping a feature from one view (the 'one view
    first' drift David flagged) FAILS QA until propagated."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    # 1) Shared prelude defines the single classification (the ONLY >hi/<lo site),
    #    and PADB_isFail is derived from it (no second copy of the rule).
    check("_COMMON_JS defines PADB_specClass (single side-aware >hi/<lo rule) + derived PADB_isFail",
          '_COMMON_JS = r"""' in src and "function PADB_num(v)" in src
          and "function PADB_specClass(v,hi,lo)" in src
          and "if(hi!==null&&v>hi){dir='high'" in src
          and "function PADB_isFail(v,hi,lo){ var c=PADB_specClass(v,hi,lo);" in src)
    # 1b) Every Site spec-classifier delegates to PADB_specClass (no inline copy of
    #     the direction/dist/verdict logic left in any view).
    check(f"all 5 Site spec-classifiers delegate to PADB_specClass (found {src.count('PADB_specClass(p.value')})",
          src.count("PADB_specClass(p.value") >= 5)
    # 1c) Single Tukey fence: PADB_fence defined once; every per-view fence helper
    #     (_spFence/_hSiteFence/_siteFence x2) delegates to it -- no copy of the
    #     Q1-k*IQR math left in a view.
    check("_COMMON_JS defines the single PADB_fence (Q1-k*IQR) + PADB_pct",
          "function PADB_fence(vals,k)" in src and "function PADB_pct(sorted,p)" in src
          and "q3=PADB_pct(s,75),iqr=q3-q1; return {lo:q1-k*iqr,hi:q3+k*iqr" in src)
    # _spFence + _hSiteFence delegate (return PADB_fence(vals,k)); the two _siteFence
    # copies delegate via PADB_fence(vals,1.5). (Box-statistics quartile code that
    # also uses pct() for the median/Q1/Q3 table columns is a separate concern and
    # legitimately stays -- it needs quartiles, not a fence.)
    check("per-view Site fence helpers all delegate to PADB_fence",
          src.count("return PADB_fence(vals,k); }") >= 2 and src.count("PADB_fence(vals,1.5)") >= 2)
    check("the spec >hi/<lo direction rule exists in exactly one place (PADB_specClass)",
          src.count("if(hi!==null&&v>hi){dir='high'") == 1)
    # 2) Injected into every V2 view's <script> assembly (uses = total minus the def).
    common_uses = src.count("_COMMON_JS") - 1
    check(f"_COMMON_JS injected into every V2 view (>=7 uses; found {common_uses})",
          common_uses >= 7)
    # 2b) Busy/loading overlay (2026-09-18): a static #padb_busy div painted BEFORE the
    #     big embedded-data <script>, hidden by PADB_busyHide (single def in _COMMON_JS)
    #     once any plot div has rendered -- added to every interactive view so a large
    #     page never just looks dead while the data parses/first render runs.
    check("busy overlay: _BUSY_OVERLAY_HTML defined with #padb_busy spinner",
          "_BUSY_OVERLAY_HTML = (" in src and 'id="padb_busy"' in src and "padbspin" in src)
    check("busy overlay: single PADB_busyHide in _COMMON_JS + poll covers #plot and #kde_plot",
          "function PADB_busyHide()" in src and "ids=['plot','kde_plot']" in src)
    # Minimum on-screen time (2026-09-24): on a fast/cached load the plot renders in the
    # same frame the poll fires, so the overlay was removed before it ever painted ("I
    # don't see the busy icon"). PADB_busyHide now defers removal until >=PADB_BUSY_MIN_MS
    # from page start; reference (no _COMMON_JS) applies the same floor to its own removal.
    check("busy overlay: enforced minimum visible time so a fast load still shows it",
          "var PADB_BUSY_MIN_MS=" in src and "var _padbBusyT0=" in src
          and "PADB_BUSY_MIN_MS-(_padbNow()-_padbBusyT0)" in src)
    _refsrc = (HERE / "padb_refstats.py").read_text(encoding="utf-8")
    check("reference busy overlay honors the same minimum visible time",
          "_refBusyMin=450" in _refsrc and "setTimeout(_refBusyGo" in _refsrc)
    busy_inserts = src.count("+ _BUSY_OVERLAY_HTML")
    check(f"busy overlay: inserted into all 7 interactive views (>=7; found {busy_inserts})",
          busy_inserts >= 7)
    # 3) Pure pass/fail sites route through the single rule (no divergent inline copy).
    check("scatter/boxplot pass-fail route through PADB_isFail",
          "return PADB_isFail(r.Value,hi,lo);" in src
          and "var fail=PADB_isFail(v,lim.hi,lim.lo);" in src
          and "if(lim.hi!=null||lim.lo!=null) return PADB_isFail(d.v,lim.hi,lim.lo);" in src)
    # 4) Cross-view feature registry: (label, marker, min occurrences). A feature
    #    dropped from a view drops the count and trips the check. Grounded in the
    #    dedicated pins (axis titles / compare-basis / segment-by) but consolidated
    #    here as the institutional "must be in all views" guard.
    registry = [
        ("axis titles use object form (all 6 views + marginals)", "title:{text:", 6),
        ("Site basis readers default to fence in JS (selectors removed; fence-only)", "site_basis", 3),
        ("Site spec classifiers (bespoke views)", "function _siteSpecClass(p)", 3),
        ("shared spec classify present", "function _spSpecClass(p)", 1),
    ]
    for label, marker, need in registry:
        got = src.count(marker)
        check(f"feature-registry: {label} (>= {need}; found {got})", got >= need)


def test_box_table_perpoint_mode() -> None:
    """Boxplot Statistics Table gained a Grouped/Per-point mode toggle (2026-09-15,
    David-approved): per-point mode = one row per measurement + PASS/FAIL vs the
    effective limit (side-aware, same rule as the scatter table / Site spec-mode),
    Group by sorts/sections; grouped mode keeps pooling (+ a #fail/n column, added
    next). Pins the toggle + per-point renderer + side-aware status."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for freq in (100.0, 200.0):
                for i in range(8):
                    v = 25.0 if i == 0 else round(10.0 + 0.05 * (i % 4), 4)  # DUT0 fails +20 upper
                    w.writerow(["Room", freq, v, f"Serial Number: D{i:02d}", 20, -20])
        out = Path(td) / "b.html"
        pp.stat_boxplot(p, {"y_label": "P", "title_prefix": "T"}, out)
        h = out.read_text(encoding="utf-8")
        check("box table: Grouped/Per-point mode selector present",
              'id="box_table_mode"' in h and 'value="perpoint"' in h and 'value="grouped"' in h)
        check("box table: per-point renderer + status via shared PADB_isFail rule",
              "function _boxPerPointTable(" in h and "function _boxPointStatus(v,lim)" in h
              and "var fail=PADB_isFail(v,lim.hi,lim.lo);" in h)
        check("box table: grouped #fail/n helper present (per-point-limit form)",
              "function _boxFailCountDetail(" in h and "function _boxFailCellDetail(" in h)
        check("box table: per-point mode short-circuits updateStatsTable",
              "if(_boxTableMode()==='perpoint'){ el.innerHTML=_boxPerPointTable(" in h)
        # Per-point table must apply the SAME Port filter the plot does, or narrowing
        # Port (directly or via a locked filter) leaves the table showing dropped ports
        # (David 2026-09-23).
        check("box per-point table filters by Port (matches the plot)",
              "var allPorts=getAllBoxPorts(), selPorts=getSelectedBoxPorts(), portActive=" in h
              and "if(portActive&&selPorts.indexOf(d.p||'')<0) return;" in h)
        # Grouped #fail/n column must be wired into EVERY row-push branch (grouped,
        # else-if filtered, default-Room, default-nonRoom) + the header, using the
        # per-point-limit form (see test_box_fail_per_point_limit). The old flat
        # _boxFailCell/_boxPfLimits must be gone (they counted phantom failures against
        # a single global spec when conditions/frequencies had different specs).
        check("box table: #fail/n column wired into all 4 grouped push sites (detail form)",
              h.count("+_boxFailCellDetail(") >= 4)
        check("box table: old flat _boxFailCell/_boxPfLimits form removed",
              "function _boxFailCell(" not in h and "_boxPfLimits(" not in h)
        check("box table: #fail/n header column present",
              "#&nbsp;fail&nbsp;/&nbsp;n</th>" in h)


def test_compare_boxplot_absent_dim_and_caret() -> None:
    """Two boxplot fixes surfaced by qa_filters on a cross-site compare (David
    2026-09-21):

    1. ABSENT-DIM = NO CONSTRAINT. On a compare, one site's rows can carry a
       grouping key the other's don't (e.g. AMC has 'Test Event Status', SR
       doesn't). _syncLfFromAllDims / the fallback getSelectedConds matched each
       COND_DIM against a condition string with a regex; a missing key gave m===null,
       and the old `m && ...` returned FALSE -> deselecting ANY filter unchecked every
       row of the site lacking that key, zeroing half the plot (and nothing restored).
       An absent dimension must impose NO constraint (m===null -> true). No-op for
       single-site pods (every condition carries every key, so m is never null).

    2. Boxplot's Workflow button must use the shared helper (flipping .wfcaret) like
       every other view -- it used caret-less inline markup, caught by qa_filters'
       workflow-toggle check."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    check("box: _syncLfFromAllDims treats an absent dim key as no-constraint (m?...:true)",
          "return m ? (sel.indexOf(m[1].trim())>=0) : true;" in src)
    check("box: fallback getSelectedConds treats an absent dim key as no-constraint",
          "return m ? (allowed.indexOf(m[1].trim())>=0) : true;" in src)
    # The pre-fix bug was the bare `m && sel.indexOf(...)>=0` form -- ensure it's gone
    # from the boxplot sync (its presence would mean the zeroing bug is back).
    check("box: pre-fix `m&&sel.indexOf` absent-dim-fails-row form removed",
          "return m&&sel.indexOf(m[1].trim())>=0;" not in src)
    check("box: workflow button uses the shared helper (flipping .wfcaret)",
          "_af_workflow_button_html('box', 'toggleBoxWorkflow')" in src
          and 'id="box_wf_btn" onclick="toggleBoxWorkflow()"' not in src)


def test_box_data_filter_passfail_and_trim() -> None:
    """Boxplot Data-filter Q1 cleanup (David 2026-09-21): the confusing
    All / Passing only / Upper limit / Lower limit radio group -- which conflated a
    pass/fail axis with a raw-sample TRIM and was missing the Failing complement --
    became a clean pass/fail axis (All / Passing only / Failing only) plus a SEPARATE,
    always-visible "Trim raw samples: above/below" control (so trim can combine with
    Passing/Failing instead of being a mutually-exclusive radio). Spec override is
    untouched (it still sets the pass/fail threshold). Pins the new structure + the
    failing = exact-complement-of-passing skip in every place points are filtered."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    check("box: Data-filter has a Failing-only radio",
          'name="box_flt" value="failing"' in src)
    check("box: old Upper/Lower-limit radios (range_hi/range_lo) removed from box_flt",
          'name="box_flt" value="range_hi"' not in src
          and 'name="box_flt" value="range_lo"' not in src)
    check("box: trim inputs are a separate always-on control (labeled 'Trim' group)",
          '_fgl("Trim"' in src
          and "Data-cleaning trim: drop raw samples" in src
          and 'id="box_flt_yhi"' in src and 'id="box_flt_ylo"' in src)
    # Data-filter bar reorganized into labeled clusters (David 2026-09-22:
    # "could the data filter section look less cluttered and better organized?").
    check("box: Data-filter bar has labeled groups (Data filter/Spec/Display/Outliers/Frequency)",
          all(('_fgl("' + g + '"') in src for g in
              ("Data filter", "Spec", "Display", "Outliers", "Frequency")))
    # Failing must be the EXACT complement of Passing, wired in every point-filter
    # site: the main trace builder + grouped pooler (verdict skip), the outlier collector
    # and non-grouped stats table (verdict-keep clause), and the per-point table. All now
    # route through _boxVerdict so Passing/Failing = pass/fail verdict (limit OR status),
    # never the old flat passLo/passHi comparison.
    check("box: failing skip via _boxVerdict in main trace builder + grouped pooler",
          src.count("if(passActive&&_vd===true) return false; if(failActive&&_vd!==true) return false;") >= 1
          and "if(passActive&&_vd===true) return; if(failActive&&_vd!==true) return;" in src)
    check("box: outlier collector + non-grouped stats-table honour failing via _boxVerdict",
          src.count("!failActive||_boxVerdict(cd.condition,d,yFlt)===true") >= 2
          and src.count("!passActive||_boxVerdict(cd.condition,d,yFlt)!==true") >= 2)
    check("box: per-point table filters via _boxVerdict (pass/fail)",
          "if(_pass&&_vd===true) return;" in src and "if(_fail&&_vd!==true) return;" in src)
    check("box: old flat passLo/passHi pass/fail skip fully removed",
          "failActive&&!((passLo!==null&&d.v<passLo)" not in src
          and "!failActive||((olPassLo!==null" not in src
          and "!failActive||((stPassLo!==null" not in src)
    # Trim is now independent of the pass/fail radio (active whenever a value is typed),
    # not gated on mode==='range_hi'/'range_lo' as before.
    check("box: trim is always-on (isFinite), not gated on a range radio mode",
          "var rhi=isFinite(yFlt.yhi)?yFlt.yhi:Infinity;" in src
          and "yFlt.mode==='range_hi'" not in src and "yFlt.mode==='range_lo'" not in src)


def test_box_fail_per_point_limit_and_spec_lines() -> None:
    """Boxplot pass/fail + spec-line correctness (David 2026-09-21), found on a
    Harmonics compare:

    1. The table counted #fail against a single flat HI_SPEC/LO_SPEC. That's WRONG
       when conditions/frequencies have different specs (Harmonic 2's spec staircase
       != 0.5's) -- it showed ~4.5x phantom failures the plot's own spec line never
       supported. Pass/fail is now per-point vs each point's OWN Upper/Lower Limit
       (override wins) via _boxPtLim / _boxFailCountDetail / _boxFailCellDetail.
    2. The plot min-pooled every condition's spec into ONE line, so 2 and 0.5
       collapsed to a single (wrong) line. Now one line per DISTINCT per-condition
       Limit staircase (_limitMapsByGroup), collapsing to one when identical.
    3. Autoscale Y pinned a Y window that then clipped newly-added conditions
       off-screen while the table still listed them. An Autoscale-Y pin now re-fits
       when the plotted set changes (_yPinnedByAutoscale / _yAutoRefit / _condSigY);
       a manual drag-zoom still persists."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    # (1) per-point-own-limit pass/fail
    check("box: per-point pass/fail helpers present (_boxPtLim/_boxFailCountDetail/_boxFailCellDetail)",
          "function _boxPtLim(d,yFlt){" in src and "function _boxFailCountDetail(" in src
          and "function _boxFailCellDetail(" in src)
    check("box: _boxPtLim uses each point's OWN upper_limit/lower_limit (override wins)",
          "yFlt.tll_hi:(d?d.upper_limit:null)" in src and "yFlt.tll_lo:(d?d.lower_limit:null)" in src)
    check("box: flat-spec fail helpers removed (_boxFailCell/_boxFailCount/_boxPfLimits gone)",
          "function _boxFailCell(" not in src and "function _boxFailCount(" not in src
          and "function _boxPfLimits(" not in src)
    # (1b) VERDICT fallback -- no numeric limit but a recorded P/F field (Test Event Status).
    # Drives Passing/Failing-only + table Status/#fail so filter->plot->table agree.
    check("box: verdict helpers present (_condStatusFail/_boxVerdict) + BOX_STATUS_FIELD emitted",
          "function _condStatusFail(condStr){" in src and "function _boxVerdict(condStr,d,yFlt){" in src
          and "var BOX_STATUS_FIELD=" in src)
    check("box: server detects a P/F status cond-dim (box_status_field)",
          "box_status_field = \"\"" in src and "_PF_TOKENS" in src and "status_field=box_status_field" in src)
    check("box: Passing/Failing filter routes through _boxVerdict (not flat passLo/passHi)",
          "if(passActive&&_vd===true) return false; if(failActive&&_vd!==true) return false;" in src
          and "passActive&&((passLo!==null&&d.v<passLo)" not in src)
    check("box: per-point table Status shows when a verdict exists (limit OR status field)",
          "var hasStatus=pts.some(function(pt){return pt.vd!==null;})" in src)
    # (2) per-condition spec lines
    check("box: per-group Limit staircase helper present (_limitMapsByGroup)",
          "function _limitMapsByGroup(selConds,fr,f2l){" in src)
    check("box: draws one line per DISTINCT staircase, collapses identical",
          "var _multiSpec=_sigOrder.length>1;" in src and "_limMaps=_limitMapsByGroup(" in src)
    check("box: hide-spec toggle matches Spec Hi/Lo traces by PREFIX (suffixed names)",
          "nm.indexOf('Spec Lo')===0||nm.indexOf('Spec Hi')===0" in src)
    # (3) Autoscale-Y re-fit on condition change
    check("box: Autoscale-Y re-fit machinery present",
          "var _yPinnedByAutoscale=false" in src and "function _condSigY()" in src
          and "if(_yPinnedByAutoscale&&_csY!==_lastCondSigY){ _yAutoRefit=true;" in src)
    check("box: buildLayout honours _yAutoRefit (re-fit Y this rebuild; Y_LIM still wins)",
          "var curY=Y_LIM||(_yAutoRefit?null:_liveAxisRange('yaxis'));" in src)
    check("box: a manual Y drag-zoom clears the autoscale pin (persists)",
          "_yPinnedByAutoscale=false" in src and "ed['yaxis.range[0]']!==undefined) _yPinnedByAutoscale=false" in src)
    # Browser-tier teeth must exist in qa_filters (run explicitly on affected pages, not in
    # the umbrella): the verdict end-to-end check + the singly/crossed filter sweep. Pinned
    # here so they can't silently rot.
    qf = HERE / "qa_filters.py"
    check("qa_filters.py exists", qf.exists())
    if qf.exists():
        qs = qf.read_text(encoding="utf-8")
        check("qa_filters: verdict end-to-end check (plot Failing == table == independent verdict)",
              "box-verdict-failing-plot-isolates-fails" in qs
              and "box-perpoint-fail-equals-independent-verdict" in qs)
        check("qa_filters: filters verified singly AND crossed (filter-cross sweep)",
              "filter-cross[" in qs and "scenario('serial+cond+temp'" in qs
              and "failing=all-shown-fail" in qs)


def test_stat_sum_table_perpoint_rollout() -> None:
    """The per-point/grouped table toggle was rolled out from boxplot to
    stat_summary and summary (2026-09-15, completing the tracked rollout).
    Source-contract drift guard: each view must have the mode selector, a
    per-point renderer that short-circuits its table builder, a grouped #fail/n
    column, and -- critically, the part that breaks most often -- the per-point
    rows + #fail count must be tightly COUPLED to the live serial/port/GF filter
    (not read raw fs.dut_vals, which recomputeFreqStat deliberately leaves
    unfiltered). Pins the coupling helper each view uses."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    # stat_summary
    check("stat table: Grouped/Per-point selector present",
          'id="stat_table_mode"' in src and "function _statTableMode(" in src)
    check("stat table: per-point mode short-circuits updateStatPanel",
          "if(_statTableMode()==='perpoint'){ el.innerHTML=_statPerPointTable(" in src)
    check("stat table: #fail/n via shared PADB_isFail rule + coupled dut_vals",
          "function _statFailCell(" in src and "function _statActiveDutVals(" in src
          and "_statFailCell(cd.condition,fs)" in src)
    check("stat table: per-point + #fail re-apply the live serial/port/GF filter",
          "_statActiveDutVals(cd.condition,fs)" in src
          and "if(useSerFlt&&selS.indexOf(d.s)<0) return false;" in src)
    check("stat table: #fail/n header column present",
          "How many DUTs fail the effective go/no-go Limit" in src)
    # Per-point Limit hi/lo + Status (and the #fail cell) must fall back to the manual
    # Spec entry (stat_spec_hi/lo) when the data carries no spec -- otherwise a compare
    # with null CSV limits (e.g. Absolute_Accuracy_NA) shows "—" forever even after a
    # spec is typed (David 2026-09-18). Pin the fallback in _statDutLimits.
    check("stat table: per-DUT limit falls back to the manual Spec entry (_statSpecEntry)",
          "function _statSpecEntry(" in src
          and "getElementById('stat_spec_hi')" in src and "getElementById('stat_spec_lo')" in src)
    check("stat table: _statDutLimits consults the manual entry (man.hi/man.lo) before page spec",
          "var man=_statSpecEntry();" in src
          and "man.hi!=null?man.hi:" in src and "man.lo!=null?man.lo:" in src)
    # stat_summary previously hardcoded a caret-less workflow button (every other
    # view uses the shared helper's flipping .wfcaret) -- surfaced by qa_filters'
    # workflow-toggle check. Pin it to the shared helper so it can't drift back.
    check("stat: workflow button uses the shared helper (flipping .wfcaret)",
          "_af_workflow_button_html('stat', 'toggleStatWorkflow')" in src
          and "id=\"stat_wf_btn\" onclick=\"toggleStatWorkflow()\"" not in src)
    # summary
    check("sum table: Grouped/Per-point selector present",
          'id="sum_table_mode"' in src and "function _sumTableMode(" in src)
    check("sum table: per-point mode short-circuits buildTable",
          "if(_sumTableMode()==='perpoint'){ _sumPerPointTable(" in src)
    check("sum table: #fail/n via shared PADB_isFail rule + per-freq inclusion",
          "function _sumFailAt(" in src and "function _sumInclAtFi(" in src
          and "PADB_isFail(v,lim.hi,lim.lo)" in src)
    check("sum table: per-point + #fail re-apply the live serial/GF filter",
          "_sumInclAtFi(cd,fi)" in src and "_isSumGfExcl(cd.dut_info[idx].s" in src)
    check("sum table: #fail/n column added to grouped Results Table",
          "cols=cols.concat(['M.U.','ΔEnv','# fail / n']);" in src
          and "_sumFailTd({fail:r.n_fail,scored:r.n_scored})" in src)


def test_dataset_summary_lines() -> None:
    """Shared dataset-summary header (2026-09-16) for the reduce + sentinel reports:
    totals, per-condition-dimension cardinality, DUTs, all-runs depth, per-site.
    Must EXCLUDE run-metadata dims (serial/run/datetime/site/event-status) from the
    condition count, and split per-site when >1 site present."""
    import pandas as _pd
    rows = []
    def row(site, ser, dt, alc, hn, f, v):
        return {"Frequency_MHz": f, "Value": v,
                "Group": (f"AlcState: {alc}  HarmonicNumber: {hn}  Serial Number: {ser}  "
                          f"Test Event Status: P  Site: {site}  Test Run Datetime: {dt}")}
    # 2 sites, 2 AlcState x 2 Harmonic = 4 real conditions; serial/site/status/run excluded
    for site, sers in (("SR", ["US001", "US002"]), ("AMC2", ["MY001"])):
        for ser in sers:
            for run, dt in enumerate(["2026/07/01 09:00:00", "2026/07/05 09:00:00"], 1):
                for alc in ("TRUE", "FALSE"):
                    for hn in ("2", "3"):
                        for f in (10.0, 20.0, 30.0):
                            rows.append(row(site, ser, dt, alc, hn, f, -70.0))
    lines = pp.dataset_summary_lines(_pd.DataFrame(rows))
    txt = "\n".join(lines)
    check("summary: has header + core metrics",
          "Dataset summary:" in txt and "measurements (rows)" in txt
          and "test frequencies" in txt and "conditions" in txt)
    check("summary: 3 distinct frequencies", "test frequencies    : 3 " in txt)
    check("summary: conditions = 4 (AlcState x Harmonic; excludes serial/site/status/run)",
          "conditions          : 4 " in txt and "AlcState=2" in txt and "HarmonicNumber=2" in txt)
    check("summary: run-metadata dims are NOT counted as conditions",
          "Serial" not in txt.split("conditions")[1].split("\n")[0]
          and "Test Event Status" not in txt.split("conditions")[1].split("\n")[0]
          and "Datetime" not in txt.split("conditions")[1].split("\n")[0])
    check("summary: DUTs=3 and all-runs depth reported",
          "DUTs                : 3" in txt and "runs/DUT (all-runs) : up to 2" in txt)
    check("summary: per-site breakdown present (2 sites)",
          "per site:" in txt and "SR " in txt and "AMC2 " in txt)
    # wiring: both report builders must actually emit the summary header
    red = (HERE / "padb_testpoint_reduce.py").read_text(encoding="utf-8")
    sen = (HERE / "padb_sentinel.py").read_text(encoding="utf-8")
    check("summary: reduce report wires in dataset_summary_lines",
          "dataset_summary_lines(df)" in red and "summary_lines" in red
          and "lines.extend(summary_lines)" in red)
    check("summary: sentinel report wires in dataset_summary_lines",
          "dataset_summary_lines(df)" in sen and "df.attrs.get(\"summary_lines\")" in sen
          and "L.extend(summary_lines)" in sen)


def test_run_index_derivation() -> None:
    """Reduction view (b): a reduction-study CSV carries 'Test Run Datetime' in the
    Group; _parse_group_fields must derive a per-(site, DUT) CHRONOLOGICAL run index
    -> an ordered, low-cardinality '_grp_Run' group-by dimension, so the scatter can
    step through a unit's run-to-run timeline. No-op on normal data."""
    import pandas as _pd
    def _mk(rows):
        df = _pd.DataFrame([{"Frequency_MHz": f, "Value": v, "Serial": s,
                             "Group": g} for (g, s, f, v) in rows])
        return pp._parse_group_fields(df)
    # US001's 3 runs appear file-shuffled (06/03, 06/01, 06/02); must map to
    # Run 01=06/01, Run 02=06/02, Run 03=06/03. US002 ranked independently.
    def row(site, ser, dt, f, v):
        return (f"Site: {site}  Serial Number: {ser}  Test Run Datetime: {dt}", ser, f, v)
    df = _mk([
        row("SR", "US001", "06/03/2026 09:00:00 AM", 1.0, -70),
        row("SR", "US001", "06/01/2026 09:00:00 AM", 1.0, -40),
        row("SR", "US001", "06/02/2026 09:00:00 AM", 1.0, -60),
        row("SR", "US002", "06/05/2026 09:00:00 AM", 1.0, -71),
        row("SR", "US002", "06/04/2026 09:00:00 AM", 1.0, -72),
    ])
    def run_of(ser, dt):
        m = df[(df["Serial"] == ser) & (df["_grp_Test Run Datetime"] == dt)]
        return m["_grp_Run"].iloc[0] if len(m) else None
    check("run-index: per-DUT chronological (shuffled dates -> ordered runs)",
          run_of("US001", "06/01/2026 09:00:00 AM") == "Run 01"
          and run_of("US001", "06/03/2026 09:00:00 AM") == "Run 03")
    check("run-index: ranked independently per DUT",
          run_of("US002", "06/04/2026 09:00:00 AM") == "Run 01"
          and run_of("US002", "06/05/2026 09:00:00 AM") == "Run 02")
    check("run-index: labels zero-padded so they sort chronologically",
          sorted(df["_grp_Run"].dropna().unique()) == ["Run 01", "Run 02", "Run 03"])
    check("run-index: '_grp_Run' offered as a group-by dimension",
          any(lbl == "Run" for _, lbl in pp._detect_group_cols(df)))
    # TEETH: no run-datetime grouping -> no _grp_Run at all (pure no-op).
    df2 = _mk([("Serial Number: US001  SpurType: 2.4GHz", "US001", 1.0, -70),
               ("Serial Number: US002  SpurType: 2.4GHz", "US002", 1.0, -71)])
    check("run-index: no-op when the data has no Test Run Datetime grouping",
          "_grp_Run" not in df2.columns)


def test_reduction_extraction() -> None:
    """Test-point-reduction extraction primitive (2026-09-16): a run pod flagged
    for a reduction study must force all-runs (TestRun_RunStatus={All},
    ExtractionOptions_AllRunResults=True) and add '<prefix>:Test Run Datetime' as a
    grouping item on every Type=80 analytic (prefix DERIVED from an existing
    analytic-prefixed grouping item, never guessed), so each run of a DUT is a
    distinguishable, orderable point. Idempotent; safe-skips when no prefix can be
    derived; flags a pinned datetime filter."""
    import padb_run as pr
    def _run(pod_text):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "_run.pod"
            p.write_text(pod_text, encoding="utf-8")
            c1 = pr.apply_reduction_extraction(p)
            c2 = pr.apply_reduction_extraction(p)  # idempotency
            return c1, c2, p.read_text(encoding="utf-8")
    # 1) Extract keys MISSING -> appended; Type=80 -> run-datetime grouping added.
    pod = ("[Extract]\nDevice_Device='X'\n\n"
           "[PADBAnalytic1]\nType=80\n"
           "Grouping_Item1=Foo-->Foo (dBc):AlcState\n"
           "Grouping_Item2=Serial Number\n"
           "Group_Num=2\n")
    c1, c2, out = _run(pod)
    check("reduction: forces all-runs extract keys when missing",
          "TestRun_RunStatus={All}" in out and "ExtractionOptions_AllRunResults=True" in out
          and "ExtractionOptions_LastResult=False" in out and c1["extract_forced"] == 3)
    check("reduction: adds UNPREFIXED 'Test Run Datetime' grouping on Type=80 "
          "(prefixed form crashes PADB DoAnalysis)",
          "Grouping_Item3=Test Run Datetime" in out
          and "Foo-->Foo (dBc):Test Run Datetime" not in out
          and "Group_Num=3" in out and c1["analytics_grouped"] == 1)
    check("reduction: forces Data_TData=Datapack on Type=80 (analytic-prefixed "
          "Data_TData crashes PADB DoAnalysis in all-runs mode)",
          "Data_TData=Datapack" in out and c1["tdata_forced"] == 1)
    check("reduction: is idempotent (second pass changes nothing)",
          c2["extract_forced"] == 0 and c2["analytics_grouped"] == 0
          and c2["tdata_forced"] == 0 and out.count("Test Run Datetime") == 1
          and out.count("Data_TData=Datapack") == 1)
    # 1b) TEETH: an existing analytic-PREFIXED Data_TData (the real crash trigger) is
    #     REPLACED with Datapack, not left in place.
    pod1b = ("[Extract]\nDevice_Device='X'\n\n[PADBAnalytic1]\nType=80\n"
             "Data_TData=Foo-->Foo (dBc):Test Step\n"
             "Grouping_Item1=Foo-->Foo (dBc):AlcState\nGroup_Num=1\n")
    c1x, _c2x, out1b = _run(pod1b)
    check("reduction: replaces a prefixed Data_TData with Datapack",
          "Data_TData=Datapack" in out1b
          and "Data_TData=Foo-->Foo (dBc):Test Step" not in out1b
          and c1x["tdata_forced"] == 1)
    # 2) Non-Type=80 analytic is left alone.
    pod2 = ("[Extract]\nDevice_Device='X'\n\n[PADBAnalytic1]\nType=70\n"
            "Grouping_Item1=Foo-->Foo (dBc):AlcState\nGroup_Num=1\n")
    c1b, _c2b, out2 = _run(pod2)
    check("reduction: non-Type=80 analytic is NOT grouped or TData-forced",
          c1b["analytics_grouped"] == 0 and "Test Run Datetime" not in out2
          and c1b["tdata_forced"] == 0 and "Data_TData=Datapack" not in out2)
    # 3) TEETH: a Type=80 whose grouping items are all UNPREFIXED still gets the
    #    (unprefixed) run grouping added -- the old code required a derivable prefix
    #    and wrongly SKIPPED here, so such a pod never got run identity. Corrected
    #    2026-09-17: no prefix needed; the bare field is what PADB accepts.
    pod3 = ("[Extract]\nDevice_Device='X'\n\n[PADBAnalytic1]\nType=80\n"
            "Grouping_Item1=Serial Number\nGroup_Num=1\n")
    c1c, _c2c, out3 = _run(pod3)
    check("reduction: adds unprefixed run grouping even with no prefixed source item",
          c1c["analytics_grouped"] == 1
          and "Grouping_Item2=Test Run Datetime" in out3 and "Group_Num=2" in out3)
    # 4) Pinned datetime filter is flagged (would defeat all-runs).
    pod4 = ("[Extract]\nTestRun_RunDateTime='06/01/2026 01:00:00 AM'\n\n"
            "[PADBAnalytic1]\nType=80\nGrouping_Item1=Foo-->Foo (dBc):AlcState\nGroup_Num=1\n")
    c1d, _c2d, _out4 = _run(pod4)
    check("reduction: flags a pinned TestRun_RunDateTime filter", c1d["pinned_datetime_filter"] is True)
    # 5) TEETH: ExtractionOptions_LastResult=True (last-run-only) is MUTUALLY
    #    EXCLUSIVE with all-runs -- must be flipped to False, not left alongside
    #    AllRunResults=True. Leaving both True crashed PADB's DoAnalysis with a
    #    NullReferenceException on the first live run (rc 0, no CSV). Regression pin.
    pod5 = ("[Extract]\nExtractionOptions_LastResult=True\n\n"
            "[PADBAnalytic1]\nType=80\nGrouping_Item1=Foo-->Foo (dBc):AlcState\nGroup_Num=1\n")
    _c1e, _c2e, out5 = _run(pod5)
    check("reduction: turns OFF LastResult when enabling all-runs (no contradiction)",
          "ExtractionOptions_LastResult=False" in out5
          and "ExtractionOptions_LastResult=True" not in out5
          and "ExtractionOptions_AllRunResults=True" in out5)


def test_reduction_native_render_and_rplots() -> None:
    """Webtool reduction-extraction CSV fix (2026-09-16): a reduction run must keep
    native render ON (OutputConfig_OutputGraph=1, no GraphFormat forced) so PADB
    writes the all-runs/grouped Type=80 output at all -- OutputGraph=0 (the normal
    Interactive disable) suppressed it, giving rc 0 with ZERO CSV via the webtool
    while a manual GUI pull (native render on) worked. And, ONLY for reduction runs,
    a tightly-scoped R-Plots sweep pulls a FRESH (mtime>=run_start) matching CSV into
    results_padb for an analytic that got no -dir output, without reintroducing the
    cross-site stale-leftover hazard the 2026-08-27 general removal fixed."""
    import os, time
    # A) make_run_pod: enable_native_render forces OutputGraph=1, NOT GraphFormat.
    pod = ("[Extract]\nDevice_Device='X'\n\n[PADBAnalytic1]\nType=80\n"
           "AnalyticName=My Analytic\nGrouping_Item1=Foo-->Foo (dBc):AlcState\n")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.pod"; src.write_text(pod, encoding="utf-8")
        d_en = Path(td) / "en.pod"; pr.make_run_pod(src, d_en, {}, enable_native_render=True)
        d_dis = Path(td) / "dis.pod"; pr.make_run_pod(src, d_dis, {}, disable_native_render=True)
        en = d_en.read_text(encoding="utf-8"); dis = d_dis.read_text(encoding="utf-8")
    check("reduction: enable_native_render forces OutputConfig_OutputGraph=1",
          "OutputConfig_OutputGraph=1" in en and "OutputConfig_OutputGraph=0" not in en)
    check("reduction: enable_native_render does NOT force GraphFormat (no extra render fmt)",
          "OutputConfig_GraphFormat" not in en)
    check("reduction: disable_native_render still sets OutputGraph=0 (distinct path)",
          "OutputConfig_OutputGraph=0" in dis and "OutputConfig_OutputGraph=1" not in dis)

    # B) _collect_padb_outputs reduction R-Plots fallback.
    def _collect(reduction: bool, fresh: bool) -> bool:
        with tempfile.TemporaryDirectory() as td:
            rplots = Path(td) / "R-Plots"; rplots.mkdir()
            results = Path(td) / "results_padb"; results.mkdir()
            run_start = time.time() - 5.0
            csv = rplots / "My_Analytic.csv"; csv.write_text("x,y\n1,2\n", encoding="utf-8")
            if not fresh:
                old = run_start - 3600
                os.utime(csv, (old, old))
            cfg = {"padb_output_dir": str(rplots)}
            if reduction:
                cfg["reduction_extraction"] = True
            analytics = [{"index": 1, "type": 80, "name": "My Analytic",
                          "output_file": "My_Analytic"}]
            pr._collect_padb_outputs(cfg, analytics, results, run_start=run_start)
            return (results / "My_Analytic.csv").exists()
    check("reduction: fresh R-Plots CSV is pulled into results_padb (no -dir output)",
          _collect(reduction=True, fresh=True) is True)
    check("reduction TEETH: a STALE R-Plots CSV (predates run) is NOT pulled",
          _collect(reduction=True, fresh=False) is False)
    check("reduction TEETH: a non-reduction job never pulls from R-Plots (2026-08-27 policy)",
          _collect(reduction=False, fresh=True) is False)


def test_publish_and_parquet_index_link() -> None:
    """Large-dataset access (2026-09-17): _write_index links a parquet sidecar with
    viewer guidance when one exists, and _publish copies the parquet (+ viewer exe +
    reduction report) to the share, not just the HTML/PDF -- otherwise the index's
    viewer/reduction links are dead on the published copy."""
    import importlib
    pv = importlib.import_module("padb_v2")
    # _write_index emits a 'Large-dataset viewer' section iff a .parquet is present.
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "MyAnalytic_scatter.html").write_text("<html></html>", encoding="utf-8")
        pv._write_index(d, "MyAnalytic", [d / "MyAnalytic_scatter.html"], {})
        no_pq = (d / "index.html").read_text(encoding="utf-8")
        check("no parquet -> no viewer section", "Large-dataset viewer" not in no_pq)
        (d / "MyAnalytic.parquet").write_bytes(b"PAR1data")
        pv._write_index(d, "MyAnalytic", [d / "MyAnalytic_scatter.html"], {})
        with_pq = (d / "index.html").read_text(encoding="utf-8")
        check("parquet present -> 'Large-dataset viewer' section + link + padb_viewer guidance",
              "Large-dataset viewer" in with_pq
              and 'href="MyAnalytic.parquet"' in with_pq
              and "padb_viewer.py" in with_pq)
        # The viewer is a performance fallback, not a required step (David 2026-09-18):
        # the HTML plots have the same analysis; only reach for the viewer if they're
        # too slow/large to open.
        check("viewer section framed as optional / performance fallback",
              "(optional)" in with_pq and "only need this if" in with_pq)
        # Collapsible (David 2026-09-18): the viewer section is a <details>/<summary>,
        # collapsed by default (small pages here), auto-expanded only when a view page
        # is very large (>= VIEW_SIZE_WARN_MB).
        import re as _re
        _m = _re.search(r"<details([^>]*)>", with_pq)
        check("viewer section is a collapsible <details> with a <summary>",
              _m is not None and "<summary" in with_pq)
        check("viewer section collapsed by default for small pages (no auto-open)",
              _m is not None and " open" not in _m.group(1))
        _orig_warn = pv.VIEW_SIZE_WARN_MB
        try:
            pv.VIEW_SIZE_WARN_MB = 1e-9   # any nonzero view page now counts as "very large"
            pv._write_index(d, "MyAnalytic", [d / "MyAnalytic_scatter.html"], {})
            big_pq = (d / "index.html").read_text(encoding="utf-8")
        finally:
            pv.VIEW_SIZE_WARN_MB = _orig_warn
        _mb = _re.search(r"<details([^>]*)>", big_pq)
        check("viewer section auto-expands when a view page is very large",
              _mb is not None and " open" in _mb.group(1) and "large pages detected" in big_pq)
        # One-click launcher: an "Open in viewer" button (fetches /api/open-viewer so
        # the local web app launches the viewer -- a browser can't run a .bat from a
        # link), plus the Open_in_viewer.bat written to the folder for the file:// case.
        bat = d / "Open_in_viewer.bat"
        check("parquet present -> 'Open in viewer' button wired to /api/open-viewer",
              "Open in viewer" in with_pq and "_pnqOpenViewer" in with_pq
              and "/api/open-viewer" in with_pq)
        check("parquet present -> 'Open folder' button wired to /api/open-folder",
              "Open folder" in with_pq and "_pnqOpenFolder" in with_pq
              and "/api/open-folder" in with_pq)
        check("parquet present -> Open_in_viewer.bat written (file:// fallback), not a browser link",
              bat.exists() and 'href="Open_in_viewer.bat"' not in with_pq)
        bat_txt = bat.read_text(encoding="utf-8")
        check("launcher bat prefers PADB_Viewer.exe then falls back to padb_viewer.py",
              "PADB_Viewer.exe" in bat_txt and "padb_viewer.py" in bat_txt
              and bat_txt.index("PADB_Viewer.exe") < bat_txt.index("padb_viewer.py"))
    # Parquet gate is SIZE-based, not compare-blanket (David 2026-09-21): a small compare
    # must NOT auto-export a sidecar (which also added a misleading "Large-dataset viewer"
    # section); only large jobs, or an explicit export_parquet:true, do.
    try:
        import pyarrow  # noqa: F401
        _has_pa = True
    except Exception:
        _has_pa = False
    if _has_pa:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "s.csv").write_text("Frequency (MHz),Value,Group\n100,1,Site: SR\n100,2,Site: AMC\n", encoding="utf-8")
            (d / "s.parquet").write_bytes(b"PAR1stale")   # a prior sidecar (old compare-blanket rule)
            pv._maybe_export_parquet({"compare_csv": {"SR": "x", "AMC": "y"}}, d / "s.csv", d, None)
            check("small compare does NOT auto-export a parquet (size gate, not compare-blanket)",
                  not list(d.glob("*.parquet")))
            check("small-compare rebuild REMOVES a stale sidecar (plot-job run self-cleans)",
                  not (d / "s.parquet").exists())
            d2 = d / "forced"; d2.mkdir()
            (d2 / "s.csv").write_text("Frequency (MHz),Value,Group\n100,1,Site: SR\n100,2,Site: AMC\n", encoding="utf-8")
            pv._maybe_export_parquet({"export_parquet": True}, d2 / "s.csv", d2, None)
            check("explicit export_parquet:true still forces a parquet on a small job",
                  bool(list(d2.glob("*.parquet"))))
    # _publish copies html, *_report.pdf, *.parquet, PADB_Viewer.exe, .bat, reduction report.
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"; dst = Path(td) / "dst"; src.mkdir()
        for name in ("a_scatter.html", "a_report.pdf", "a.parquet",
                     "PADB_Viewer.exe", "Open_in_viewer.bat",
                     "a_testpoint_reduction.txt", "a_testpoint_reduction.csv"):
            (src / name).write_bytes(b"x")
        (src / "notes.log").write_bytes(b"x")  # must NOT be published
        pv._publish(src, dst)
        published = {p.name for p in dst.iterdir()}
        check("publish copies html/pdf/parquet/exe/bat/reduction-report",
              {"a_scatter.html", "a_report.pdf", "a.parquet", "PADB_Viewer.exe",
               "Open_in_viewer.bat", "a_testpoint_reduction.txt",
               "a_testpoint_reduction.csv"} <= published)
        check("publish does NOT copy unrelated files (e.g. .log)",
              "notes.log" not in published)


def test_repeat_collapse_is_mean() -> None:
    """Repeat-collapse (multiple values at one point) uses the MEAN everywhere,
    reconciled 2026-09-16: scatter's lines mode previously used median alone while
    boxplot's _collapseDupRuns / stat_summary+summary DUT-averaging all use mean
    (and the TI/TTL/MU framework is mean+/-sigma based). Drift guard: scatter
    lines mode must collapse with meanOf (not median), and _collapseDupRuns must
    stay a mean (sum/length)."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    check("scatter lines mode collapses repeats by mean (meanOf), not median",
          "y:xkl.map(function(x){return meanOf(byXl[x]);})" in src
          and "function meanOf(" in src
          and "median(byXl[x])" not in src)
    check("scatter lines hover says 'mean of repeats' (not median)",
          "(mean of repeats)" in src and "(median of repeats)" not in src)
    check("_collapseDupRuns stays a mean (sum/length)",
          "function _collapseDupRuns(" in src and "out.v=sum/g.length;" in src)


def test_site_check_table_cap_and_spinner() -> None:
    """Site Population Check per-point detail tables (boxplot/stat_summary/summary/
    histogram) render a <tr> per non-primary measurement -- on a large, high-
    dimensional compare (e.g. 62-dim YIG switching, ~12k+ non-primary rows) that's
    a huge uncapped DOM build. Cap the rendered rows at 5000 (summary counts + CSV
    still cover ALL rows), and show a spinner while the panel computes/renders
    (David 2026-09-18)."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    # All 4 Site-check detail tables iterate a capped slice, not raw rows.
    check("all 4 Site-check detail tables cap at 5000 (_siteShown)",
          src.count("var _siteCapN=5000") >= 4 and src.count("_siteShown.forEach") >= 4)
    check("Site-check cap shows a 'Showing first N of M' note; counts stay over all rows",
          "Showing first '+_siteCapN.toLocaleString()+' of '+rows.length.toLocaleString()" in src
          and "summary counts above are over all rows" in src)
    # Busy spinner on slow panel render, deferred one frame so it paints first.
    check("shared spinner + deferred-render helpers present",
          "function PADB_spinnerHtml(" in src and "function PADB_deferRender(" in src
          and "requestAnimationFrame(function(){ requestAnimationFrame(buildFn); })" in src)
    check("all 4 Site-panel toggles render via PADB_deferRender (spinner then build)",
          src.count("PADB_deferRender(") >= 5)   # 1 def + 4 toggle call sites
    # Extended 2026-09-18: the 4 Statistics/Results table "Refresh" buttons (the
    # explicit large-rebuild above the size-gate) also spinner-wrap via PADB_deferRender.
    check("Statistics/Results Refresh buttons wrap the build in PADB_deferRender (spinner)",
          src.count("PADB_deferRender(document.getElementById(") >= 4)
    # Histogram has no size-gated Refresh table -- its own Statistics panel toggle
    # (toggleStats) spinner-wraps its build instead (2026-09-18), so switching-speed
    # compares (histogram-only) also get a table-render spinner.
    check("histogram Statistics toggle spinner-wraps its build",
          "if(show) PADB_deferRender(el, update," in src)


def test_site_check_fence_only_all_views() -> None:
    """Site Population Check is FENCE-ONLY in EVERY view (2026-09-22, David: cross-view
    consistency -- "similar menus should perform the same"). The boxplot was made
    fence-only earlier; the fence/spec/both basis selector is now removed from
    stat_summary, summary, histogram, distribution (padb_plots) and env_coverage
    (padb_v2) too. Distribution keeps its Absolute/ΔTemp basis and env_coverage keeps
    Room/ΔEnv (those are view-specific axes, not the datasheet-vs-fence choice). The JS
    defaults every reader to 'fence' when the selector is absent. Teeth: re-adding any
    fence/spec/both basis selector trips this."""
    ppsrc = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    v2src = (HERE / "padb_v2.py").read_text(encoding="utf-8")
    for sid in ('id="box_site_basis"', 'id="stat_site_basis"', 'id="sum_site_basis"',
                "id='h_site_basis'", 'id="dist_site_cmp"'):
        check(f"fence-only: basis selector removed from padb_plots ({sid})", sid not in ppsrc)
    check("fence-only: basis selector removed from env_coverage (ec_site_cmp)",
          'id="ec_site_cmp"' not in v2src)
    # No "Site check vs:" basis label should remain anywhere (that label was the selector's).
    check("fence-only: no 'Site check vs' basis label remains",
          "Site check vs" not in ppsrc and "Site&nbsp;check&nbsp;vs" not in ppsrc
          and "Site check vs" not in v2src)
    # Every reader still resolves to 'fence' when the (now-absent) selector isn't found.
    check("fence-only: readers default to 'fence' when no selector present",
          ppsrc.count("||{}).value||'fence'") >= 2
          and "el?el.value:'fence'" in ppsrc)
    # The Site Population Check button itself must still exist in each aggregate view.
    for btn in ('id="stat_site_toggle_btn"', 'id="sum_site_toggle_btn"',
                'id="dist_site_btn"', "id='h_site_btn'"):
        check(f"fence-only: Site Population Check button still present ({btn})", btn in ppsrc)
    check("fence-only: env_coverage Site button still present (padb_v2)",
          'id="ec_site_btn"' in v2src)


def test_scatter_passfail_and_crossfilter() -> None:
    """Scatter gained two consistency features (David 2026-09-22):
    (a) an All/Passing/Failing data filter, per-point vs each point's OWN effective limit
        (per-point Upper/Lower Limit -> raw Spec -> page spec) via the shared PADB_isFail,
        gated on the dataset having a spec, applied inside applyFilters so the plot AND the
        data-rows table (both fed by applyFilters) stay consistent -- Passing keeps pass +
        no-limit, Failing keeps only true fails (boxplot convention);
    (b) cross-filter greying -- opening a dimension panel greys options not present under
        the OTHER dimensions' current selections (visual only, never unchecks).
    Behaviourally verified via Playwright (pass/fail partitions + table row-count matches;
    RUN_B greyed when AlcState=FALSE). Source-pinned here."""
    src = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    check("scatter: All/Passing/Failing data-filter radios (name=scat_flt)",
          'name="scat_flt" value="all"' in src
          and 'name="scat_flt" value="passing"' in src
          and 'name="scat_flt" value="failing"' in src)
    check("scatter: per-point fail via shared PADB_isFail + effective-limit precedence",
          "function _scatRowFail(r)" in src and "PADB_isFail(r.Value,hi,lo)" in src)
    check("scatter: pass/fail applied in applyFilters (plot+table consistent)",
          "if(pfMode==='passing'&&_fl===true) return false;" in src
          and "if(pfMode==='failing'&&_fl!==true) return false;" in src)
    check("scatter: pass/fail control gated on the dataset having a spec",
          "_scat_has_spec" in src)
    check("scatter: cross-filter availability helpers present",
          "function _crossFilterAvail(" in src and "function _applyCrossFilterGrey(" in src)
    check("scatter: cross-filter greying invoked on panel open",
          "panel.classList.add('open'); _applyCrossFilterGrey();" in src)


def test_gf_clear_in_apply_views() -> None:
    """F3 cross-view consistency (2026-09-22, David): the shared Global Filter can now be
    CLEARED from every view that APPLIES it. scatter/distribution/reference previously
    applied the GF but offered no clear control; each now has a 'Clear global filter'
    button (shown when a GF exists) + a clear function that removes GF_KEY, reloads the
    view's GF state and re-renders."""
    ppsrc = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    ref = (HERE / "padb_refstats.py").read_text(encoding="utf-8")
    check("scatter: Clear-global-filter button + function",
          'id="gf_clear_btn"' in ppsrc and "function clearScatterGlobalFilter()" in ppsrc)
    check("distribution: Clear-global-filter button + function",
          'id="dist_gf_clear_btn"' in ppsrc and "function clearDistGlobalFilter()" in ppsrc)
    check("reference: Clear-global-filter button + function",
          'id="ref_gf_clear_btn"' in ref and "function clearRefGlobalFilter()" in ref)


def test_control_context_clarity() -> None:
    """Control-context clarity pass (2026-09-17, David: 'context for button, tables and
    plots as obvious as possible'). Drift guard for the P1+P2 label/tooltip fixes:
    P/C expanded + explained, Reset scope-tooltip'd, Group-by says what it regroups,
    auto-filter level explained, Site-panel CSV disambiguated, webapp Save-default
    tooltip'd."""
    pp = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    idx = (HERE / "webapp" / "templates" / "index.html").read_text(encoding="utf-8")
    # P1.1 P/C -- expanded label + coverage/confidence explanation (stat/sum/dist labels
    # + env_coverage tooltips).
    check("P/C explained as coverage/confidence",
          "P (coverage)" in pp and "C (confidence)" in pp
          and pp.count("P&nbsp;(coverage)") >= 3)
    # P1.2 Reset -- scope tooltip on every reset/clear control (resetFilters x5,
    # resetView, clearEverything).
    check("Reset controls state they do NOT clear the Global Filter",
          pp.count("Does NOT clear the Global Filter") >= 6)
    # P2.3 Group-by -- says it regroups both plot and table.
    check("Group-by tooltip states it regroups plot + table",
          "Regroups BOTH the plot traces and the Statistics/Results table" in pp)
    # P2.4 auto-filter level -- Off=inactive + aggressiveness explained (shared helper +
    # stat + boxplot).
    check("auto-filter level explains Off/aggressiveness",
          pp.count("How aggressively to auto-exclude bad DUTs") >= 3)
    # P2.5 Site-panel CSV disambiguated from the main CSV export.
    check("Site-panel CSV export relabeled 'Export site-check CSV'",
          "Export site-check CSV (All)" in pp
          and "Export site-check CSV (Outside only)" in pp
          and "Export CSV (All)" not in pp)
    # Webapp: Save-default (share root) button now has a tooltip.
    check("webapp Save-default button has a scope tooltip",
          'id="saveRootBtn" title=' in idx)


def test_compare_create_only() -> None:
    """Compare panel CREATES the job only; running is done from the standard jobs table
    (the single run path with Publish/PDF/dry-run options). David 2026-09-17: the old
    'Create & Run' auto-ran and silently bypassed those options. Guard: button relabeled
    'Create job', tooltip says it does not run, and the compare submit handler no longer
    POSTs execute-job."""
    idx = (HERE / "webapp" / "templates" / "index.html").read_text(encoding="utf-8")
    appjs = (HERE / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    check("compare button relabeled 'Create job' (not 'Create & Run')",
          ">Create job<" in idx and "Create &amp; Run" not in idx)
    check("compare create-button tooltip states it does NOT run the job",
          "does NOT run it" in idx)
    # Isolate the compare submit handler (from its registration up to the next
    # top-level call) and assert it no longer auto-executes the created job.
    seg = ""
    if 'getElementById("compareForm")' in appjs and "loadCompareCsvs()" in appjs:
        seg = appjs.split('getElementById("compareForm")', 1)[1].split("loadCompareCsvs()", 1)[0]
    check("compare submit handler no longer auto-runs (no /api/execute-job in it)",
          bool(seg) and "/api/execute-job" not in seg and "created = true" in seg)


def test_webapp_optional_toolbars() -> None:
    """Optional secondary jobs-toolbar actions (Generate PDF report, Test-point Reduce,
    Schedule, Convert) are tagged with an 'optional' chip so they're visually distinct
    from the required drop->generate->run flow (David 2026-09-17). Convert dropdowns also
    get a neutral placeholder (nothing pre-selected -- an auto-selected site made an
    optional step look required) + an empty-selection guard."""
    idx = (HERE / "webapp" / "templates" / "index.html").read_text(encoding="utf-8")
    css = (HERE / "webapp" / "static" / "style.css").read_text(encoding="utf-8")
    appjs = (HERE / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    check(".opt-chip class defined in style.css", ".opt-chip{" in css or ".opt-chip {" in css)
    check("optional chip on >=4 secondary toolbars", idx.count('class="opt-chip"') >= 4)
    for anchor in ('id="generatePdfBtn"', 'id="reduceBtn"', 'id="scheduleType"', 'id="convertJobSite"'):
        last_toolbar = idx.split(anchor, 1)[0].rsplit('<div class="toolbar">', 1)[-1]
        check(f"optional chip precedes {anchor}", 'class="opt-chip"' in last_toolbar)
    check("convert dropdowns get a neutral placeholder (nothing pre-selected)",
          'ph.value = ""' in appjs and "select site" in appjs)
    check("convert handlers guard an empty target site",
          appjs.count("Pick a target site to convert") >= 2)
    # The optional toolbars are wrapped in a single collapsible <details> to cut
    # clutter (David 2026-09-23). All four optional actions live inside it; the
    # required Run/Publish controls and the destructive Delete row stay outside.
    check("optional actions wrapped in a collapsible <details id=optionalActions>",
          'id="optionalActions"' in idx and 'class="optional-actions"' in idx)
    check(".optional-actions styling defined in style.css",
          ".optional-actions" in css)
    _optpos = idx.find('id="optionalActions"')
    _optend = idx.find("</details>", idx.rfind('id="convertSelectedBtn"'))
    for anchor in ('id="generatePdfBtn"', 'id="reduceBtn"', 'id="scheduleSelectedBtn"', 'id="convertSelectedBtn"'):
        _p = idx.find(anchor)
        check(f"{anchor} is inside the collapsible optional block",
              _optpos >= 0 and _optend > _optpos and _optpos < _p < _optend)
    # The per-run "Build PDF report" checkbox was dropped -- PDF is on-demand only
    # (Generate PDF report button) so a slow PDF isn't forced onto every run.
    check("per-run Build PDF report checkbox removed (PDF is on-demand only)",
          'id="pdfReportCheckbox"' not in idx and "Build PDF report" not in idx)
    check("Run Selected no longer sends pdf_report from a per-run checkbox",
          "pdfReportCheckbox" not in appjs)
    # The Delete row is tagged 'destructive' (red chip), not 'optional' -- it's an
    # irreversible action, so it gets a distinct warning marker, and it stays
    # visible (outside the collapsible optional block).
    check(".danger-chip class defined in style.css", ".danger-chip{" in css or ".danger-chip {" in css)
    del_toolbar = idx.split('id="deleteSelectedBtn"', 1)[0].rsplit('<div class="toolbar">', 1)[-1]
    check("destructive chip precedes Delete Selected (and it's not mislabeled optional)",
          'class="danger-chip"' in del_toolbar and 'class="opt-chip"' not in del_toolbar)
    check("Delete row stays outside the collapsible optional block",
          idx.find('id="deleteSelectedBtn"') > _optend)
    # Tooltip help is always on (David 2026-09-23): the show/hide checkbox was
    # removed -- help is a default, not an option. Inline title= tooltips remain,
    # and the JS no longer wires a toggle or persists a padb_web_tooltips pref.
    check("tooltip show/hide checkbox removed (help is always on)",
          'id="tooltipToggle"' not in idx and "Show tooltip help" not in idx)
    check("inline title tooltips still present on the page",
          idx.count('title="') >= 30)
    check("app.js no longer wires a tooltip toggle or persists the pref",
          "tooltipToggle" not in appjs and "padb_web_tooltips" not in appjs)
    # The compare panel's inline "Run test-point reduction on merged data" control
    # was removed as redundant (David 2026-09-23): reduction on a compare job's
    # merged CSV is run from the standard job-menu Test-point Reduce action, which
    # resolves _compare_merged.csv. The compare panel creates the job only; all
    # post-run analytics go through the one standard path. Backend reduce_on_merged
    # is kept for API/job.json back-compat, so only the UI wiring is pinned gone.
    check("compare panel's inline reduce control removed (redundant with job-menu)",
          'id="compareReduceChk"' not in idx and 'id="compareReduceMode"' not in idx
          and 'id="compareReducePct"' not in idx)
    check("compare-create body no longer sends reduce_on_merged from the panel",
          "compareReduceChk" not in appjs)
    check("standalone job-menu Test-point Reduce control still present",
          'id="reduceBtn"' in idx)


def test_scatter_spec_line_caveat() -> None:
    """Scatter spec-line caveat (2026-09-17): when the scatter pools points with
    heterogeneous per-point limits (multiple limits at the same offset) or mostly-null
    limits UNDER a single drawn spec line, warn that a 'pass' point can legitimately
    sit above the line (it's judged against its own limit) and hint which Measurement
    dim to filter. Must NOT fire on clean one-limit-per-offset data or when no spec
    line is drawn. (Root cause of a real 'pass line above spec line' report on a
    phase-noise pod pooling AM Noise + Phase Noise, 98.7% null limits.)"""
    # (a) heterogeneous limits at the same offset + mostly-null -> banner + Measurement hint.
    rows = []
    for off in (10.0, 100.0, 1000.0):
        rows.append({"Frequency_MHz": off, "Value": -130.0, "Upper_Limit": -120.0, "Lower_Limit": None, "_grp_Measurement": "AM Noise", "Serial": "S1"})
        rows.append({"Frequency_MHz": off, "Value": -150.0, "Upper_Limit": -145.0, "Lower_Limit": None, "_grp_Measurement": "Phase Noise", "Serial": "S1"})
        for k in range(8):
            rows.append({"Frequency_MHz": off, "Value": -140.0, "Upper_Limit": None, "Lower_Limit": None, "_grp_Measurement": "Phase Noise", "Serial": f"S{k}"})
    df = pd.DataFrame(rows); df["_val_col_name"] = "Value (dBc/Hz)"
    for _c in ("Upper_Limit", "Lower_Limit"):  # real loader gives numeric NaN, not None
        df[_c] = pd.to_numeric(df[_c], errors="coerce")
    html_bad = pp._build_av_freq_html(df, {}, "T")
    check("scatter caveat fires on heterogeneous/mostly-null limits under a spec line",
          "Spec-line caveat" in html_bad and "its <b>own</b> limit" in html_bad)
    check("scatter caveat points to the Data filter (per-point pass/fail), not just the line",
          "Data&nbsp;filter" in html_bad
          and ("Passing&nbsp;only" in html_bad or "Passing only" in html_bad))
    check("scatter caveat names the Measurement dim + values to filter",
          "Measurement" in html_bad and "AM Noise" in html_bad and "filter" in html_bad)
    # (b) clean: one limit per offset, all limited -> NO banner.
    rows2 = []
    for off, lim in ((10.0, -120.0), (100.0, -130.0), (1000.0, -140.0)):
        for s in range(4):
            rows2.append({"Frequency_MHz": off, "Value": lim - 5, "Upper_Limit": lim, "Lower_Limit": None, "_grp_Measurement": "Phase Noise", "Serial": f"S{s}"})
    df2 = pd.DataFrame(rows2); df2["_val_col_name"] = "Value (dBc/Hz)"
    for _c in ("Upper_Limit", "Lower_Limit"):
        df2[_c] = pd.to_numeric(df2[_c], errors="coerce")
    check("scatter caveat does NOT fire on clean one-limit-per-offset data",
          "Spec-line caveat" not in pp._build_av_freq_html(df2, {}, "T"))
    # (c) no spec line drawn (all-null limits) -> NO banner even though all null.
    df3 = df2.copy(); df3["Upper_Limit"] = float("nan")
    check("scatter caveat does NOT fire when no spec line is drawn",
          "Spec-line caveat" not in pp._build_av_freq_html(df3, {}, "T"))


def test_axis_titles_object_form() -> None:
    """Plotly 3.x silently DROPS a bare-string axis title (xaxis:{title:'x'} or
    xaxis:{title:VAR}) -- only title:{text:...} renders. The bundled Plotly bump
    to 3.6.0 stripped EVERY interactive view's axis names (reported 2026-09-15 on
    the DCFM boxplot: both axes unlabeled). Pin: no hand-written JS layout in
    padb_plots.py / padb_viewer.py sets an xaxis/yaxis title as a bare string or
    variable -- every one must be title:{text:...}. (Python-side plotly, e.g. the
    reference view's fig.update_yaxes(title_text=...), is unaffected and not
    scanned.)"""
    import re
    # `(xaxis|yaxis)[N]:` optionally wrapped in `Object.assign(`, then `{title:`
    # whose next char is NOT `{` (i.e. a bare string/var, the broken form).
    pat = re.compile(r"(?:xaxis|yaxis)\d*:(?:Object\.assign\()?\{title:(?!\{)")
    for fn in ("padb_plots.py", "padb_viewer.py"):
        src = (HERE / fn).read_text(encoding="utf-8")
        bad = pat.findall(src)
        check(f"{fn}: all JS axis titles use title:{{text:...}} (Plotly-3 form)",
              not bad, f"{len(bad)} bare-string axis title(s): {bad[:6]}")
    # And confirm a freshly-built boxplot actually renders both axis titles in the
    # object form (the categorical x-axis uses X_SHORT_LABEL+' ('+X_UNIT+')').
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "box.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for freq in (100.0, 200.0):
                for i in range(8):
                    w.writerow(["Room", freq, round(10.0 + 0.05 * (i % 4), 4),
                                f"HarmonicNumber: 2  Serial Number: D{i:02d}", 20, -20])
        out = Path(td) / "box.html"
        pp.stat_boxplot(p, {"y_label": "Pwr (dBc)", "title_prefix": "T"}, out)
        h = out.read_text(encoding="utf-8")
        check("boxplot x-axis title is object form built from X_SHORT_LABEL+X_UNIT",
              "title:{text:X_SHORT_LABEL+' ('+X_UNIT+')'}" in h)
        check("boxplot y-axis title is object form",
              "({title:{text:Y_LABEL}},curY?" in h)


def test_scatter_table_spec_status() -> None:
    """Scatter data-rows table + CSV export gained Spec Hi/Lo + Limit Hi/Lo columns
    and a per-point Pass/Fail Status (added 2026-09-15, user request). Pass/Fail is
    judged against the LIMIT (Upper/Lower Limit -- PADB's derived go/no-go) and only
    applies a bound that's present, so a one-sided (upper-only) spec fails only on
    the side that exists. Spec and Limit column-pairs are gated independently
    (_scatterBounds) so an all-empty pair isn't shown as dead columns."""
    import csv as _csv
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sc.csv"
        with p.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Frequency (MHz)", "Value (dBc)", "Group", "Upper Limit", "Lower Limit"])
            # 6 pass (<=-80) + 2 fail (>-80) against an upper-only limit
            for i, v in enumerate([-90, -85, -82, -95, -88, -100, -70, -60]):
                w.writerow([100.0 + i, v, f"Serial Number: D{i:02d}", -80, ""])
        out = Path(td) / "sc.html"
        pp.accuracy_vs_freq(p, {"y_label": "Value (dBc)", "title_prefix": "T"}, out)
        h = out.read_text(encoding="utf-8")
        check("scatter table: bounds gating present (_scatterBounds spec/limit)",
              "function _scatterBounds()" in h and "spec:spec,limit:limit" in h)
        check("scatter table: Pass/Fail judged via the shared _scatRowFail rule",
              "function _scatterStatus(r)" in h
              and "var fail=_scatRowFail(r);" in h)
        check("scatter table: Spec/Limit/Status column headers emitted",
              "'Spec Hi','Spec Lo'" in h and "'Limit Hi','Limit Lo'" in h and "extraH.push('Status')" in h)
        check("scatter CSV export carries the same Spec/Limit/Status columns",
              "hdrs.push('Limit Hi','Limit Lo')" in h and "_scatterStatus(r).t" in h)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("qa_regressions -- per-fix regression pins (pure Python helpers)")
    for fn in (test_freq_floor_ceil, test_freq_label_map, test_snap_pc_opt,
               test_short_x_label, test_parse_group_kv, test_extract_group_field,
               test_has_segmentable_spec, test_resolve_date_sentinel,
               test_filename_stem_variants, test_x_axis_detection,
               test_csv_to_parquet_newlines, test_scatter_decimate_toggle,
               test_filter_state_scoping, test_spec_mask_interpolation,
               test_scatter_mask_is_dataset_level,
               test_auto_filter_boxplot, test_auto_filter_stat_summary,
               test_auto_filter_rollout_summary_envcov, test_auto_filter_histogram,
               test_auto_filter_site_scope, test_pdf_report_contract,
               test_room_only_default_views, test_scatter_room_temp_filterable,
               test_reference_stats, test_axis_titles_object_form,
               test_scatter_table_spec_status, test_site_check_compare_basis,
               test_box_control_groups, test_distribution_compare_room_only_site,
               test_scatter_blank_dim_not_dropped, test_summary_data_filter_rollout,
               test_blank_dim_no_site_drop, test_af_apply_line_applied_aware,
               test_reference_busy_overlay,
               test_scatter_draw_modes, test_scatter_worst_first_spec_relative,
               test_site_check_table_cap_and_spinner,
               test_site_check_fence_only_all_views, test_gf_clear_in_apply_views,
               test_scatter_passfail_and_crossfilter, test_systemic_label_covers_batch,
               test_stat_perpoint_passfail_pointwise, test_summary_group_by_serial,
               test_summary_stat_perpoint_own_limit_only, test_locked_filters_crossview,
               test_control_context_clarity, test_scatter_spec_line_caveat,
               test_compare_create_only, test_webapp_optional_toolbars,
               test_box_table_perpoint_mode, test_compare_boxplot_absent_dim_and_caret,
               test_box_data_filter_passfail_and_trim,
               test_box_fail_per_point_limit_and_spec_lines,
               test_stat_sum_table_perpoint_rollout,
               test_repeat_collapse_is_mean, test_reduction_extraction,
               test_reduction_native_render_and_rplots,
               test_publish_and_parquet_index_link,
               test_run_index_derivation, test_dataset_summary_lines,
               test_common_prelude_and_feature_registry,
               test_named_band_segments_crossview,
               test_plotly_api_lint_and_render_guards, test_jsrules_behavioral_gate_present):
        try:
            fn()
        except Exception as exc:
            check(f"{fn.__name__} raised", False, repr(exc))
    print(f"\n{'=' * 60}\n  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}")
    if _FAIL:
        print("\nFailed checks:")
        for f in _FAIL:
            print(f"  - {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
