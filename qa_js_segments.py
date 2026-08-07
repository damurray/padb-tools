#!/usr/bin/env python3
"""
qa_js_segments.py — regression test for getSpecSegments() (the spec-limit
segment tab-through's frequency-band detector), covering the 2026-08-07
asymmetric-two-sided-limit fix.

getSpecSegments() merges Upper and Lower limit/spec/uncertainty points into
one list of contiguous frequency bands for the "Segment by" Prev/Next
control. It used to prefer Upper's transitions exclusively whenever Upper
had any data at all, silently dropping any transition unique to Lower (an
asymmetric two-sided spec, where Upper and Lower step at different
frequencies) -- see CLAUDE.md's "Spec-limit segment tab-through" section.

The function is duplicated verbatim 7 times in padb_plots.py, once per view
(scatter, legacy distribution(), the real V2 _build_env_distribution_html,
stat_summary, summary, env_coverage, boxplot) rather than shared, because
each view's JS is a self-contained inline <script> block. This test:

  1. Extracts every one of the 7 copies directly from the CURRENT
     padb_plots.py source (never a hand-copied duplicate embedded in this
     test file -- a hand-copied one could keep passing forever while the
     real, shipped function had regressed).
  2. Asserts all 7 copies are still textually identical. The 2026-08-07 fix
     relied on that being true to apply via one search/replace across every
     view; if a future edit touches only one copy, this catches the drift
     immediately instead of leaving the other 6 views silently unfixed.
  3. Runs ONE extracted copy under headless Edge against synthetic hi/lo
     point arrays covering: symmetric two-sided, genuinely asymmetric
     two-sided (the exact bug this test exists for), one-sided upper,
     one-sided lower, empty-both, and one side starting later than the
     other (a real edge case, not just a log line).

Usage:
    python qa_js_segments.py

Exit codes: 0 = all checks pass, 1 = one or more failures.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PADB_PLOTS = Path(__file__).with_name("padb_plots.py")

_EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

FUNC_NAME = "getSpecSegments"

# ---------------------------------------------------------------------------
# Result tracking (same style as qa_padb.py)
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
# Extraction: pull every getSpecSegments(...) body out of padb_plots.py via
# balanced-brace scanning (safe against the nested braces in forEach
# callbacks -- a naive non-greedy regex would stop at the first inner "}").
# ---------------------------------------------------------------------------

def _extract_function_bodies(src: str, name: str) -> list[str]:
    out = []
    pat = re.compile(r"function " + re.escape(name) + r"\([^)]*\)\{")
    for m in pat.finditer(src):
        start = m.start()
        depth = 0
        i = m.end() - 1  # index of the opening '{'
        while i < len(src):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append(src[start:i + 1])
                    break
            i += 1
    return out


def _find_edge() -> str:
    for cand in _EDGE_CANDIDATES:
        if Path(cand).exists():
            return cand
    print("[ERROR] Microsoft Edge not found in the usual install locations")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Synthetic test cases, run inside headless Edge against one real extracted
# copy of getSpecSegments(). Each case is [hiPoints, loPoints, expectedSegs]
# where expectedSegs is a list of [lo, hi, hiValue, loValue].
# ---------------------------------------------------------------------------

_CASES = {
    "symmetric_two_sided": {
        "hi": [{"x": 0, "y": -90}, {"x": 10, "y": -90}, {"x": 11, "y": -95}, {"x": 20, "y": -95}],
        "lo": [{"x": 0, "y": 80}, {"x": 10, "y": 80}, {"x": 11, "y": 75}, {"x": 20, "y": 75}],
        "expect": [[0, 10, -90, 80], [11, 20, -95, 75]],
    },
    "asymmetric_two_sided": {
        # The bug this test exists for: Upper breaks at x=11, Lower breaks
        # at x=15. The old hi-preferred-exclusively code silently collapsed
        # this to 2 segments, losing Lower's own transition at 15.
        "hi": [{"x": 0, "y": -90}, {"x": 10, "y": -90}, {"x": 11, "y": -95},
               {"x": 14, "y": -95}, {"x": 15, "y": -95}, {"x": 20, "y": -95}],
        "lo": [{"x": 0, "y": 80}, {"x": 10, "y": 80}, {"x": 11, "y": 80},
               {"x": 14, "y": 80}, {"x": 15, "y": 75}, {"x": 20, "y": 75}],
        "expect": [[0, 10, -90, 80], [11, 14, -95, 80], [15, 20, -95, 75]],
    },
    "one_sided_upper": {
        "hi": [{"x": 0, "y": -90}, {"x": 10, "y": -90}, {"x": 11, "y": -95}, {"x": 20, "y": -95}],
        "lo": [],
        "expect": [[0, 10, -90, None], [11, 20, -95, None]],
    },
    "one_sided_lower": {
        "hi": [],
        "lo": [{"x": 0, "y": 80}, {"x": 10, "y": 80}, {"x": 11, "y": 75}, {"x": 20, "y": 75}],
        "expect": [[0, 10, None, 80], [11, 20, None, 75]],
    },
    "empty_both": {
        "hi": [],
        "lo": [],
        "expect": [],
    },
    "lo_starts_late": {
        # Lower has no data until x=10 -- the segment break must land exactly
        # where Lower's own data begins, not get absorbed into Upper's
        # (constant, non-breaking) band.
        "hi": [{"x": 0, "y": -90}, {"x": 10, "y": -90}, {"x": 20, "y": -90}],
        "lo": [{"x": 10, "y": 80}, {"x": 20, "y": 80}],
        "expect": [[0, 0, -90, None], [10, 20, -90, 80]],
    },
}


def _build_test_html(func_src: str) -> str:
    cases_json = json.dumps(_CASES)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<div id="out"></div>
<script>
{func_src}
var CASES = {cases_json};
var results = [];
Object.keys(CASES).forEach(function(name) {{
  var c = CASES[name];
  var got = getSpecSegments(c.hi, c.lo);
  var gotSimplified = got.map(function(s) {{ return [s.lo, s.hi, s.hiValue, s.loValue]; }});
  var pass = JSON.stringify(gotSimplified) === JSON.stringify(c.expect);
  results.push(name + ": " + (pass ? "PASS" : "FAIL") + " got=" + JSON.stringify(gotSimplified) + " expect=" + JSON.stringify(c.expect));
}});
document.getElementById('out').textContent = results.join(" || ");
</script>
</body></html>"""


def main() -> None:
    src = PADB_PLOTS.read_text(encoding="utf-8", errors="replace")
    bodies = _extract_function_bodies(src, FUNC_NAME)

    print(f"[extraction]")
    check(f"{FUNC_NAME}: found in padb_plots.py", len(bodies) > 0)
    check(f"{FUNC_NAME}: exactly 7 copies (one per view)", len(bodies) == 7,
          f"got {len(bodies)}")
    if not bodies:
        print(f"\n{'='*55}\n  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}")
        sys.exit(1 if _FAIL else 0)

    all_identical = all(b == bodies[0] for b in bodies)
    check(f"{FUNC_NAME}: all copies textually identical (no per-view drift)",
          all_identical,
          "one or more views has a different copy -- see CLAUDE.md, the fix "
          "must be re-applied to whichever copy(ies) diverged")

    print(f"\n[behavior — running one extracted copy under headless Edge]")
    edge = _find_edge()
    html = _build_test_html(bodies[0])

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        html_path = tmp / "qa_js_segments_test.html"
        dom_path = tmp / "qa_js_segments_dom.html"
        html_path.write_text(html, encoding="utf-8")

        udd = tmp / "udd"
        # Headless Edge can spawn a crash-reporter child that inherits the
        # stdout pipe and outlives the main process, which makes a
        # capture_output=True (pipe-read) subprocess.run() hang forever
        # waiting for EOF even though msedge.exe itself has already exited.
        # Redirecting stdout to a real file and using Popen.wait() (which
        # only waits on the immediate child's exit status, not the pipe)
        # sidesteps that entirely.
        with open(dom_path, "w", encoding="utf-8") as dom_f:
            proc = subprocess.Popen(
                [edge, "--headless", "--disable-gpu", "--disable-crash-reporter",
                 "--virtual-time-budget=5000", f"--user-data-dir={udd}",
                 "--dump-dom", str(html_path)],
                stdout=dom_f, stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                # Observed in this environment: --dump-dom finishes writing
                # its output well within virtual-time-budget, but msedge.exe
                # itself lingers afterward (background/telemetry activity)
                # rather than exiting promptly. The file is already
                # complete by the time we get here, so kill and move on --
                # this is a known quirk, not evidence the render failed.
                print("  (msedge.exe lingered past 20s -- killing; output file was already complete)")
                proc.kill()
                proc.wait()

        dom_text = dom_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'<div id="out">(.*?)</div>', dom_text, re.S)
        if not m:
            check("browser produced readable output", False,
                  "no #out div found in dumped DOM -- Edge may have failed to load the page")
        else:
            check("browser produced readable output", True)
            for line in m.group(1).split(" || "):
                name = line.split(":", 1)[0]
                check(f"{FUNC_NAME}: case '{name}'", "PASS" in line and "FAIL" not in line,
                      line)

    print(f"\n{'='*55}")
    print(f"  PASS: {len(_PASS)}    FAIL: {len(_FAIL)}")
    if _FAIL:
        print("\nFailed checks:")
        for f in _FAIL:
            print(f"  - {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
