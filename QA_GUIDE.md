# PADB Tools — QA Guide

How to QA a padb-tools change (or another group's own extractions) with minimal
PADB runs and mostly-automated verification. Every tool here is standalone and
portable — point it at your own data with `--root` / `--job` / a manifest.

---

## The idea: test each layer at its cheapest point

| Layer | What it covers | Cost | How to test |
|---|---|---|---|
| **Extraction** (`padb_run.py`, `make_run_pod`, output collection, PADB-R exclusivity guard) | pod → CSV | expensive (PADB-R.exe, minutes, one-at-a-time) | 1–2 real run jobs — no need to re-extract for plot/JS testing |
| **Plot / JS** (`padb_v2.py` → the 6 views + all interactive JS) | CSV → HTML | cheap (no PADB) | rebuild from **existing CSVs** |
| **Generators / webapp / convert** | job.json, publish, schedule | cheap | dry-run / function-level |

The leverage: plot builds read existing CSVs, so the whole interactive/statistical
surface is testable with **zero new extractions**.

---

## The tools

All exit non-zero on failure, so they double as CI-style gates. Run with the
project Python (`py` / `python3.14.exe`).

### `qa_padb.py` — regression baseline
Synthetic-data unit/integration checks across the pipeline. Self-contained (no
paths, no PADB).
```bash
py qa_padb.py
```
Baseline: **37 PASS / 4 FAIL** (the 4 FAILs are long-standing/pre-existing — a
change is clean if it doesn't move that count).

### `qa_js_segments.py` — per-view JS drift guard
Asserts the segment-tab machinery (`getSpecSegments`) still has exactly **7
textually-identical copies** (one per view) — catches a fix applied to one view
but not the others. (Its headless-Edge sub-check can fail on an Edge/environment
issue; the static invariant is the important part.)
```bash
py qa_js_segments.py
```

### `padb_csv_check.py` — pre-flight one CSV (or one job)
Inspects a single extracted CSV before you build plots: load success, x-axis/value
auto-detection, serial/temperature/spec detection, Group cardinality, drop rate.
```bash
py padb_csv_check.py path\to\Scatter.csv
py padb_csv_check.py path\to\Scatter.csv --x-col "Rate (kHz)"   # non-frequency x-axis
py padb_csv_check.py --job path\to\job_v2.json                  # also checks publish target
```
Exit 1 on any WARN or FAIL. **WARNs are usually benign** (placeholder-row drop,
crowded legend, no Spec/Uncertainty grouping items) — read them, don't fear them.

### `qa_csv_sweep.py` — pre-flight the whole dataset (Gate 2)
Runs `padb_csv_check.py` across every CSV under the given roots and prints a
pass/warn/fail table. No render, no PADB.
```bash
py qa_csv_sweep.py                         # standard data roots
py qa_csv_sweep.py --root C:\your\data      # your own data (repeatable)
py qa_csv_sweep.py --fails-only            # only FAIL/error rows
```
- **WARN alone does not fail the sweep**; only a real FAIL/error does.
- **x_col aware:** for each CSV it finds a matching `*_v2_job.json` and applies that
  job's `x_col`, so a non-frequency-x-axis analytic (e.g. a `Rate (kHz)` sweep)
  isn't false-FAILed. `--no-xcol` tests raw auto-detection only.
- Skips tool-generated intermediates (`_compare_merged.csv`, `_v2_tmp_*.csv`,
  `global_filter_*.csv`) and `backup/` / `Job_Archive/` dirs.
- Skips **non-scatter CSVs** the scatter loader can't read — Type=60 Environmental
  (detected by their `UDE`/`LDE` columns) and DateTime/list metadata (by name) —
  and lists them separately at the end. `--include-non-scatter` checks them anyway.

### `qa_view_sweep.py` — rebuild + headless-verify views (Gate 3)
Drives real `*_v2_job.json` files into a throwaway `--out` dir via
`padb_v2.py <job> --no-publish`, then for each job:
1. captures stdout signals (auto-`binary_encode` NOTE, oversized-view warning,
   "no matching test data", any ERROR/FAIL);
2. lists the views built (confirms auto view-selection);
3. headless-renders each view in Edge and asserts the Plotly plot actually drew
   (no fatal JS error), plus optional per-job needles (e.g. boxplot shows real
   serials not `["unknown"]`; a compare page has the Site Population Check panel).
```bash
py qa_view_sweep.py                        # run the coverage set
py qa_view_sweep.py --list                 # show which jobs resolve, run nothing
py qa_view_sweep.py --job my_v2_job.json --no-defaults   # just this job
py qa_view_sweep.py --root C:\your\data     # search your data for the coverage jobs
py qa_view_sweep.py --keep                 # keep the temp output to inspect
py qa_view_sweep.py --max-headless-mb 200  # DOM-dump larger views too (slow)
```

#### The coverage manifest (`qa_view_sweep.json`)
The coverage set (one representative job per pod-variety axis) is **not** hardwired
to any one workstation. It's loaded from a `qa_view_sweep.json` manifest, resolved
in priority order: `--manifest PATH` → the per-user Padb dir (next to
`padb_config.json`) → beside the script → the built-in default. Each entry is
`{label, glob (str or list), needles?}`.

Another group sets up their own coverage set once:
```bash
py qa_view_sweep.py --write-manifest       # writes a starter qa_view_sweep.json
# edit the "glob" patterns to your own pod/job names, then just run:
py qa_view_sweep.py
```

---

## Running a full QA pass

```bash
py qa_padb.py            # Gate 1a: regression baseline (expect 37/4)
py qa_js_segments.py     # Gate 1b: per-view JS drift guard
py qa_csv_sweep.py       # Gate 2 : every CSV loads / detects correctly
py qa_view_sweep.py      # Gate 3 : every representative view builds + renders
```
Then a short manual eyeball of ~6 representative pages (one multi-temp statistical
page, a compare page, a no-spec page, a non-frequency-axis page, a large
phase-noise scatter, one legacy summary) — confirm the plot *renders* and one
drag-zoom + one Group-by change behave. Automation can't judge "does it look right".

**Sign-off:** Gates 1–3 green (baseline unchanged; no FAILs in the sweep beyond
known WARNs; every representative view renders with no JS error) + the eyeball
pages OK + one real extraction completes clean.

---

## Pod-variety coverage (what to make sure is represented)

| Variety / code path | Example dataset |
|---|---|
| Multi-temp + serial + frequency-varying (staircase) spec | Clock spurs / SR Close-In |
| Room-only + serial (auto view-selection → scatter+boxplot only) | AMC2 Close-In |
| Serial-less pod (serial controls must hide, not fabricate) | a pod with no Serial in Group |
| No spec limits (`spec_direction`, Limit-display selector) | MaxPower |
| **Non-frequency x-axis** (`x_col`/`x_label`) | Analog-Mod Flatness (`Rate (kHz)`), Vgg, Amplitude |
| High-cardinality / fragmented conditions (Group-by pooling) | ClockSpurs (151), Absolute Accuracy (~2388) |
| Cross-site compare (Site Population Check, coverage-gap) | `compare_csv` SR-vs-AMC2 |
| Large / dense sweep (auto-`binary_encode`, decimation, 80 MB size guard) | phase-noise DCFM/EFC offset sweeps |
| Multi-analytic pod (index grouping) | Harmonics + Sub-harmonics |

---

## Interpreting results

- **WARN vs FAIL:** WARNs are informational and usually expected; FAILs mean a CSV
  couldn't load usefully (0 rows, wrong x-axis) — investigate those.
- **"0 usable rows" on a non-frequency-x-axis analytic:** the analytic sweeps
  something other than Frequency (e.g. `Rate (kHz)`). Set `x_col` (+ `x_label`/
  `x_unit`) in its plot job.json, or regenerate with `padb_make_v2_job.py`, which
  auto-detects it from the pod's `Data_ScatterPlot_XData_Label`. **Caveat:** if the
  pod's own metadata mislabels the axis (some do), the hand-set `x_col` is the
  authority — don't let a regeneration clobber a working one.
- **Oversized view (≥ 80 MB):** `padb_v2.py` logs a size warning with options.
  Large CSVs (≥ 25 MB or ≥ 250k rows) auto-enable `binary_encode` (plot-transparent
  — file encoding only, never a displayed value). A per-frequency boxplot over
  thousands of distinct frequencies is inherently huge — use the scatter for that.
- **Publishing is opt-in** (webapp "Publish to share after run", or a job's own
  `publish_to`); QA runs stay local by default.

---

## Portability summary

| Tool | Portable via |
|---|---|
| `qa_padb.py`, `qa_js_segments.py` | already path-agnostic |
| `padb_csv_check.py` | explicit CSV/job path |
| `qa_csv_sweep.py` | `--root` |
| `qa_view_sweep.py` | `--root` + `qa_view_sweep.json` manifest / `--job` |
| core run-job / plot pipeline | job.json + per-user `padb_config.json` |

---

## `qa_filters.py` — filter / Global-Filter self-consistency gate (added 2026-09-10)

Where `qa_view_sweep.py` proves a page *renders*, `qa_filters.py` proves its
**filters are self-consistent** — the recurring bug class (a filter, especially
the Global Filter, that doesn't do exactly what it says, or a plot/table that
drift apart on a filter change). Boxplot-focused, **compare-aware** (the priority
for cross-site production-ramp comparisons).

**Mechanism:** injects a self-test harness into each boxplot HTML that drives the
page's OWN controls headlessly — it turns on "Show Points" so every plotted point
is a real marker it can read back (serial from the point's hover text, frequency
from the box category on x), applies a filter/GF matrix, and after each op asserts
invariants by diffing the plotted-point set. Rendered under headless Edge
(`--dump-dom`, same house mechanism as `qa_js_segments.py`; each run is a fresh
temp file + `--user-data-dir`, so browser caching can't stale a result — a real
gotcha when driving these pages interactively). Results parsed from a
`#__qa_results` JSON sentinel. Exit 1 on any FAIL.

**Invariants:** baseline-not-blank; **outliers-GF-precise** (Set outliers as GF
removes ONLY points sharing an outlier's `(serial, condition, freq-label)`
identity — never a whole DUT across other frequencies — and never blanks);
clear-GF-restores; deselect-site (compare) / deselect-serial remove exactly their
target and restore; filter-GF-whole-dut (Set filter as GF on one narrowed serial
removes that serial across ALL frequencies); reset-restores.

```
py qa_filters.py                          # all compare boxplots under C:\temp\data
py qa_filters.py --root <dir> --include-single-site
py qa_filters.py --page <one_boxplot.html>
```

**Validated:** passes 15/0 on a freshly-built compare boxplot; correctly FAILS
`outliers-GF-precise` on an old-code page (the pre-2026-09-10 whole-DUT
over-exclusion — `removed=63, 23 outside the outlier identity`). Tests pages AS
BUILT, so rebuild a page (padb_v2.py) before testing it if it predates a fix.

**Roadmap (compare-first, then generalize):** v1 covers the boxplot (single-site
+ compare). Next: view-appropriate readers for scatter / stat_summary / summary /
env_coverage / distribution / histogram (each exposes plotted points differently),
and a plot↔table (`#box_stat_panel`) cross-check, so the same invariant set runs
on every view.
