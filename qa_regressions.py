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
               test_csv_to_parquet_newlines):
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
