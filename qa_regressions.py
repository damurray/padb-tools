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
    """Scatter gained Draw modes + Smooth for phase-noise-style plots (2026-09-15,
    user request): Markers / Lines / Lines+markers / Vertical(per-freq sticks), a
    Smooth (spline) toggle, and a default group-by of Serial (one curve per DUT) for
    a real swept measurement with a modest DUT count. 'lines' collapses repeat
    measurements to one median point per x (clean curve); 'sticks' draws a vertical
    min..max segment per frequency (right for discrete spurs); Smooth is off by
    default (honest linear) and can be defaulted on via cfg 'scatter_smooth'."""
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
        check("scatter: Smooth (spline) toggle present, spline shape wired",
              'id="smooth_chk"' in h and "?'spline':'linear'" in h)
        check("scatter: lines mode collapses repeats to one median point per x",
              "median(byXl[x])" in h and "(median of repeats)" in h)
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
    """Boxplot Site Population Check gained a selectable comparison basis (2026-09-15,
    user request): 'fence' (primary k*IQR fence, existing), 'spec' (judge each
    non-primary point against its own datasheet Spec/Limit -- reuses the fence path's
    downstream triage by reclassifying verdict against the limit), and 'both' (fence
    drives triage + a Spec P/F column). Only on a compare page (PRIMARY_SITE set)."""
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
        check("site check: comparison-basis selector rendered (fence/spec/both)",
              'id="box_site_basis"' in h and 'value="fence"' in h and 'value="spec"' in h and 'value="both"' in h)
        check("site check: spec classifier present (_siteSpecClass, limit-or-page-spec)",
              "function _siteSpecClass(p)" in h and "typeof HI_SPEC!=='undefined'" in h
              and "p.limHi!=null" in h)
        check("site check: spec basis reclassifies verdict + skips <4-primary gate",
              "if(siteBasis==='spec')" in h and "the <4-primary-points 'n/a' gate does not apply" in h)
        check("site check: both-mode adds Spec P/F column + null-safe fence bound render",
              "var showSpecCol=(siteBasis==='both')" in h
              and "r.lo!==undefined&&r.lo!==null" in h)
        check("site check: CSV export carries basis + Spec_PF",
              "'Spec_PF'" in h and "# Comparison basis: " in h)
        # A non-compare boxplot has no PRIMARY_SITE -> no selector at all.
        pn = Path(td) / "nc.csv"
        with pn.open("w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Test Step", "Frequency (MHz)", "Power (dBc)", "Group", "Upper Limit", "Lower Limit"])
            for freq in (100.0, 200.0):
                for i in range(8):
                    w.writerow(["Room", freq, 10.0 + 0.05 * i, f"Serial Number: D{i:02d}", 20, -20])
        on = Path(td) / "nc.html"
        pp.stat_boxplot(pn, {"y_label": "P", "title_prefix": "T"}, on)
        check("site check: no comparison-basis selector on a non-compare page",
              'id="box_site_basis"' not in on.read_text(encoding="utf-8"))


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
    # 3) Pure pass/fail sites route through the single rule (no divergent inline copy).
    check("scatter/boxplot pass-fail route through PADB_isFail",
          "PADB_isFail(r.Value,r.Upper_Limit,r.Lower_Limit)" in src
          and "var fail=PADB_isFail(v,lim.hi,lim.lo);" in src
          and "if(PADB_isFail(v,lim.hi,lim.lo)===true) n++;" in src)
    # 4) Cross-view feature registry: (label, marker, min occurrences). A feature
    #    dropped from a view drops the count and trips the check. Grounded in the
    #    dedicated pins (axis titles / compare-basis / segment-by) but consolidated
    #    here as the institutional "must be in all views" guard.
    registry = [
        ("axis titles use object form (all 6 views + marginals)", "title:{text:", 6),
        ("Site compare-to selectors present (box/stat/sum/hist/dist/ec)", "site_basis", 3),
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
        check("box table: grouped #fail/n helper present",
              "function _boxFailCount(" in h and "function _boxFailCell(" in h)
        check("box table: per-point mode short-circuits updateStatsTable",
              "if(_boxTableMode()==='perpoint'){ el.innerHTML=_boxPerPointTable(" in h)


def test_site_compare_basis_rollout() -> None:
    """The Site Population Check "Compare to" selector (fence/spec/both) was rolled
    out from boxplot to ALL views (2026-09-15): stat_summary, summary, histogram
    (bespoke panels) and env_coverage + distribution (shared _SITE_PANEL_SHARED_JS).
    Source-contract drift guard: each view's selector id + spec-classify + basis
    wiring must be present, and every view must judge spec pass/fail by the same
    RULE (vs Limit/Spec, side-aware, verdict 'OUTSIDE'==fails)."""
    ppsrc = (HERE / "padb_plots.py").read_text(encoding="utf-8")
    v2src = (HERE / "padb_v2.py").read_text(encoding="utf-8")
    # Per-view selector ids (boxplot's box_site_basis is pinned separately).
    for sid in ('id="stat_site_basis"', 'id="sum_site_basis"', "id='h_site_basis'", 'id="dist_site_cmp"'):
        check(f"site compare-to selector present: {sid}", sid in ppsrc)
    check("site compare-to selector present: ec_site_cmp (padb_v2)", 'id="ec_site_cmp"' in v2src)
    # Bespoke spec classifiers (side-aware, verdict OUTSIDE==fails spec).
    check("bespoke _siteSpecClass present in >=3 views (box/stat/summary)",
          ppsrc.count("function _siteSpecClass(p)") >= 3)
    check("histogram _hSiteSpecClass present", "function _hSiteSpecClass(p)" in ppsrc)
    # Shared panel: spec classify + per-basis row + basis-aware render/CSV.
    check("shared _spSpecClass + _spRowForBasis present",
          "function _spSpecClass(p)" in ppsrc and "function _spRowForBasis(" in ppsrc)
    check("shared render/CSV honor meta.compareBasis",
          ppsrc.count("meta.compareBasis||'fence'") >= 2)
    check("dist + ec supply compareBasis to shared panel",
          ppsrc.count("compareBasis:cmp") >= 2)
    # Every classifier delegates to the shared PADB_specClass (the single rule),
    # so all views' spec verdicts are side-aware and identical by construction.
    check("all Site spec-classifiers delegate to shared PADB_specClass",
          ppsrc.count("PADB_specClass(p.value") >= 5)


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
        check("scatter table: Pass/Fail judged vs Limit via shared PADB_isFail rule",
              "function _scatterStatus(r)" in h
              and "PADB_isFail(r.Value,r.Upper_Limit,r.Lower_Limit)" in h)
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
               test_scatter_draw_modes, test_site_compare_basis_rollout,
               test_box_table_perpoint_mode, test_common_prelude_and_feature_registry,
               test_plotly_api_lint_and_render_guards):
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
