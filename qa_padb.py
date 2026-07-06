#!/usr/bin/env python3
"""
qa_padb.py — PADB Tools automated QA

Generates a small synthetic scatter CSV with deterministic values, runs
padb_v2.py against it, then validates the generated HTML output by checking
file existence, HTML structure (control element IDs/classes), embedded JS data
(parsed from the HTML), and one computed statistical value.

Usage:
    python qa_padb.py [--keep]

    --keep   Copy the generated HTML output to qa_output/ in the current
             directory so you can open the files in a browser for manual
             inspection.

Exit codes: 0 = all checks pass, 1 = one or more failures.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PYTHON = r"C:\Users\damurray\AppData\Local\Python\bin\python.exe"
PADB_V2 = Path(__file__).with_name("padb_v2.py")

# ---------------------------------------------------------------------------
# Synthetic dataset design
# ---------------------------------------------------------------------------

SERIALS = ["QA001", "QA002", "QA003"]
HARMONICS = [2, 3]
PORTS = ["RF1", "RF2"]
TEMPS = {"Room": 0.0, "0.0 Deg C": -1.5, "55.0 Deg C": +2.0}
FREQS = [100.0, 200.0, 300.0, 400.0, 500.0]
SPEC_HI = -50.0
SPEC_LO = -110.0


def _synth_value(harmonic: int, port: str, serial_idx: int, temp_offset: float, freq: float) -> float:
    """Deterministic measurement value with a predictable mean across DUTs."""
    base = -60.0 - harmonic * 4.0 - (0.0 if port == "RF1" else 2.0)
    return base + temp_offset + serial_idx * 0.5 + (freq - 300.0) * 0.001


def _expected_mean(harmonic: int, port: str, temp_offset: float, freq: float) -> float:
    """Mean across all 3 DUTs — the value stat_summary should embed."""
    return float(np.mean([_synth_value(harmonic, port, i, temp_offset, freq)
                          for i in range(len(SERIALS))]))


def make_synthetic_csv(path: Path) -> None:
    rows = []
    for temp_label, temp_offset in TEMPS.items():
        for harmonic in HARMONICS:
            for port in PORTS:
                for serial_idx, serial in enumerate(SERIALS):
                    group = (
                        f"HarmonicNumber: {harmonic}  Port: {port}"
                        f"  Serial Number: {serial}"
                    )
                    for freq in FREQS:
                        val = _synth_value(harmonic, port, serial_idx, temp_offset, freq)
                        rows.append({
                            "Test Step": temp_label,
                            "Frequency (MHz)": freq,
                            "Power (dBc)": round(val, 6),
                            "Group": group,
                            "Upper Limit": SPEC_HI,
                            "Lower Limit": SPEC_LO,
                        })
    pd.DataFrame(rows).to_csv(path, index=False)


def make_job_json(path: Path) -> None:
    job = {
        "description": "QA synthetic test",
        "title_prefix": "QA_Test",
        "y_label": "Power (dBc)",
        "y_lim": [-120, 0],
        "room_values": ["Room"],
        "proportion": 0.90,
        "confidence": 0.90,
        "views": ["scatter", "stat_summary", "boxplot", "distribution",
                  "env_coverage", "summary"],
    }
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

def _extract_js_var(html: str, var_name: str):
    """
    Extract a JavaScript variable embedded as  var NAME = <json>;  in HTML.
    Uses bracket-matching to handle arbitrarily large JSON blobs.
    Returns the parsed Python object, or None if not found/parseable.
    """
    m = re.search(rf'\bvar\s+{re.escape(var_name)}\s*=\s*', html)
    if not m:
        return None
    start = m.end()
    if start >= len(html) or html[start] not in ('[', '{'):
        return None
    open_ch, close_ch = html[start], (']' if html[start] == '[' else '}')
    depth = 0
    in_str = False
    esc = False
    end = start
    for i, ch in enumerate(html[start:], start):
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    end = i
                    break
    try:
        return json.loads(html[start:end + 1])
    except json.JSONDecodeError:
        return None


def _has_id(html: str, id_: str) -> bool:
    return f'id="{id_}"' in html or f"id='{id_}'" in html


def _has_class(html: str, cls: str) -> bool:
    return cls in html


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

_PASS: list[str] = []
_FAIL: list[str] = []


def check(desc: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASS.append(desc)
        print(f"  PASS  {desc}")
    else:
        _FAIL.append(desc)
        msg = f"  FAIL  {desc}"
        if detail:
            msg += f" — {detail}"
        print(msg)


# ---------------------------------------------------------------------------
# Per-plot checks
# ---------------------------------------------------------------------------

def test_stat_summary(path: Path) -> None:
    print(f"\n[stat_summary]")
    html = path.read_text(encoding="utf-8", errors="replace")
    check("stat_summary: file non-empty", len(html) > 5_000)
    check("stat_summary: show_pts_chk control", _has_id(html, "show_pts_chk"))
    check("stat_summary: serial filter checkboxes", 'class="ser_chk"' in html)

    stat_data = _extract_js_var(html, "STAT_DATA")
    check("stat_summary: STAT_DATA parseable", stat_data is not None)
    if not stat_data:
        return

    conditions = [cd.get("condition", "") for cd in stat_data]
    check("stat_summary: 4 conditions (2H × 2P)",
          len(conditions) == 4,
          f"got {len(conditions)}: {conditions}")

    # Locate condition HarmonicNumber:2 Port:RF1
    cond = next((cd for cd in stat_data
                 if "HarmonicNumber: 2" in cd.get("condition", "")
                 and "RF1" in cd.get("condition", "")), None)
    check("stat_summary: HarmonicNumber:2 RF1 condition present", cond is not None)
    if not cond:
        return

    fs = next((f for f in cond.get("freq_stats", [])
               if abs(f.get("freq", -1) - 100.0) < 0.5), None)
    check("stat_summary: freq_stats entry at 100 MHz", fs is not None)
    if not fs:
        return

    expected = _expected_mean(2, "RF1", 0.0, 100.0)
    got = fs.get("mean")
    check("stat_summary: mean at 100 MHz matches expected",
          got is not None and abs(got - expected) < 0.01,
          f"expected {expected:.4f}, got {got}")

    dut_vals = fs.get("dut_vals", [])
    check("stat_summary: dut_vals has 3 entries",
          len(dut_vals) == len(SERIALS),
          f"got {len(dut_vals)}")


def test_boxplot(path: Path) -> None:
    print(f"\n[boxplot]")
    html = path.read_text(encoding="utf-8", errors="replace")
    check("boxplot: file non-empty", len(html) > 5_000)
    check("boxplot: show_pts_chk control", _has_id(html, "box_show_pts_chk"))
    check("boxplot: serial filter toggle", _has_id(html, "all_box_ser"))
    check("boxplot: temperature filter checkboxes", "box_env_chk" in html)

    box_data = _extract_js_var(html, "BOX_DATA")
    check("boxplot: BOX_DATA parseable", box_data is not None)
    if not box_data:
        return

    conditions = list({cd.get("condition", "") for cd in box_data})
    check("boxplot: >= 4 distinct conditions",
          len(conditions) >= 4,
          f"got {len(conditions)}")

    # Each condition entry should have freq_stats
    has_freq_stats = all(len(cd.get("freq_stats", [])) > 0 for cd in box_data)
    check("boxplot: all BOX_DATA entries have freq_stats", has_freq_stats)

    # vals_detail (for show points) should be present
    has_vals = any(
        any(len(fs.get("vals_detail", [])) > 0 for fs in cd.get("freq_stats", []))
        for cd in box_data
    )
    check("boxplot: vals_detail present (show points data)", has_vals)


def test_distribution(path: Path) -> None:
    print(f"\n[distribution]")
    html = path.read_text(encoding="utf-8", errors="replace")
    check("distribution: file non-empty", len(html) > 5_000)
    check("distribution: show_excl_chk control", _has_id(html, "show_excl_chk"))
    check("distribution: CONDS data present", "var CONDS" in html or "CONDS=" in html)


def test_summary(path: Path) -> None:
    print(f"\n[summary]")
    html = path.read_text(encoding="utf-8", errors="replace")
    check("summary: file non-empty", len(html) > 5_000)
    check("summary: show_excl_chk control", _has_id(html, "sum_show_excl_chk"))

    data = _extract_js_var(html, "DATA")
    check("summary: DATA parseable", data is not None)
    if data:
        check("summary: >= 4 condition groups in DATA",
              len(data) >= 4,
              f"got {len(data)}")

    cond_dims = _extract_js_var(html, "COND_DIMS")
    check("summary: COND_DIMS parseable", cond_dims is not None)
    if cond_dims:
        labels = [d.get("label", "") for d in cond_dims]
        check("summary: HarmonicNumber in COND_DIMS",
              any("harmonic" in l.lower() for l in labels),
              f"dims: {labels}")
        check("summary: >= 2 filter dimensions",
              len(cond_dims) >= 2,
              f"dims: {labels}")


def test_env_coverage(path: Path) -> None:
    print(f"\n[env_coverage]")
    html = path.read_text(encoding="utf-8", errors="replace")
    check("env_coverage: file non-empty", len(html) > 1_000)


def test_scatter(path: Path) -> None:
    print(f"\n[scatter]")
    html = path.read_text(encoding="utf-8", errors="replace")
    check("scatter: file non-empty", len(html) > 5_000)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    keep = "--keep" in sys.argv

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        csv_path = tmp / "qa_synthetic.csv"
        job_path = tmp / "qa_job.json"
        out_dir = tmp / "qa_html"

        print("Generating synthetic CSV ...")
        make_synthetic_csv(csv_path)
        print(f"  {len(SERIALS)} DUTs × {len(HARMONICS)} harmonics × "
              f"{len(PORTS)} ports × {len(TEMPS)} temps × {len(FREQS)} freqs "
              f"= {len(SERIALS)*len(HARMONICS)*len(PORTS)*len(TEMPS)*len(FREQS)} rows")

        print("Writing job JSON ...")
        make_job_json(job_path)

        print("Running padb_v2.py ...")
        result = subprocess.run(
            [PYTHON, str(PADB_V2), str(job_path),
             "--csv", str(csv_path),
             "--out", str(out_dir)],
            capture_output=True, text=True,
        )
        if result.stdout:
            print(result.stdout.rstrip())
        if result.returncode != 0:
            print(f"\n[ERROR] padb_v2.py exited {result.returncode}")
            if result.stderr:
                print(result.stderr[:2000])
            sys.exit(1)

        html_files = {p.stem.lower(): p for p in out_dir.glob("*.html")}

        print("\n--- File existence ---")
        for tag in ("stat_summary", "boxplot", "distribution", "summary", "scatter", "env_coverage"):
            matched = next((p for stem, p in html_files.items() if tag in stem), None)
            check(f"output: {tag} HTML exists", matched is not None)

        print("\n--- Content checks ---")
        for stem, path in sorted(html_files.items()):
            if "stat_summary" in stem:
                test_stat_summary(path)
            elif "boxplot" in stem:
                test_boxplot(path)
            elif "distribution" in stem:
                test_distribution(path)
            elif "env_coverage" in stem:
                test_env_coverage(path)
            elif "scatter" in stem:
                test_scatter(path)
            elif "summary" in stem:
                test_summary(path)

        if keep:
            dest = Path("qa_output")
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(out_dir, dest)
            print(f"\nHTML output kept at: {dest.resolve()}")

    print(f"\n{'='*55}")
    print(f"  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}")
    if _FAIL:
        print("\nFailed checks:")
        for f in _FAIL:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("  All checks passed.")


if __name__ == "__main__":
    main()
