# PADB Tools — QA Guide

How to QA a padb-tools change (or another group's own extractions) with minimal
PADB runs and mostly-automated verification. Every tool here is standalone and
portable — point it at your own data with `--root` / `--job` / a manifest.

---

## Getting started with no data yet (new user, few or no run jobs)

You do **not** need to collect a backlog of run jobs before you can self-QA.
Most of the QA is synthetic and needs zero real data.

**1. Run the deterministic core immediately — it generates its own data.**
```bash
py qa_selfcheck.py
```
This umbrella runs compile + `qa_padb` + `qa_viewer` + `qa_js_segments` +
`qa_stats_recompute`, all on internally-generated synthetic datasets, and prints a
GREEN/RED/AMBER verdict against a saved baseline. A brand-new user with an empty
results folder still gets a real verdict covering the pipeline, every statistical
aggregation, and per-view JS drift. This is ~80% of self-QA and needs nothing
collected.

**2. For the render/behavior tiers, collect *variety*, not *volume*.** Only
`qa_csv_sweep` / `qa_view_sweep` / `qa_filters` need real CSVs — and plot builds
reuse existing CSVs with no re-extraction, so one good extraction is reusable
forever. Cheapest ways to get enough, in order:

- **Maximize samples per extraction, not number of jobs.** Widen the date range and
  pull all runs so one run job yields a large multi-DUT, multi-temp sample:
  ```json
  "subex": { "Device_MinDate": "8 weeks ago", "Device_MaxDate": "today",
             "TestRun_RunStatus": "{All}" }
  ```
- **Cover the variety matrix with ~5 pods, then stop.** One multi-temp statistical
  pod, one Room-only, one cross-site `compare_csv`, one no-spec/one-sided pod, and
  one non-frequency-x-axis pod (e.g. a `Rate (kHz)` sweep or a switching-speed
  histogram). Five extractions exercise nearly every rendering branch; more of the
  same *shape* adds little.
- **Point the sweeps at data that already exists** rather than generating your own:
  ```bash
  py qa_csv_sweep.py --root C:\path\to\any\results
  py qa_view_sweep.py --root C:\path\to\any\results
  ```
- **Register your own coverage set once** so re-runs are one command:
  ```bash
  py qa_view_sweep.py --write-manifest   # edit qa_view_sweep.json to your pod/job names
  py qa_view_sweep.py
  ```

Summary: run `qa_selfcheck` on day one (needs nothing); do **one broad extraction**
(wide date range + `{All}` runs) to seed the data-dependent sweeps; add
representative pods only to fill the variety matrix — reusing existing CSVs
throughout.

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

### `qa_regressions.py` — per-fix regression pins (pure Python helpers)
Pins the *Python-side* helper fixes documented in CLAUDE.md to their exact reported
failure shapes, so a regression reintroducing the old behaviour fails here
immediately. Covers: `_floor_dec`/`_ceil_dec` (freq-range clip), `_freq_label_map`
(adaptive-precision collision), `_snap_pc_opt` (P/C select string), `_short_x_label`,
`_parse_group_kv` (space-padding), `_extract_group_field`, `_has_segmentable_spec`,
`_resolve_date_sentinel` (subex dates), `filename_stem_variants`, `_clean_x_axis_label`/
`_x_col_override`/`_is_non_sweep_x` (non-freq/offset/histogram x-axis), and
`_csv_to_parquet` (embedded-newline). Browser-free. (The JS-side fixes are covered
generically by `qa_filters`.)
```bash
py qa_regressions.py
```
Baseline: **34 PASS / 0 FAIL**. Part of `qa_selfcheck.py`'s core suite.

### `qa_webapp.py` — hermetic Flask route coverage
Exercises the web app's routes (`/`, `/api/jobs`, `/api/execute-job`, `/api/schedule`,
`/api/delete-job`, `/api/generate-job`, `/api/config`, `/api/convert-*`,
`/api/orphaned-padb`, results-token serving, …) with the Flask **test client** — no
running dev server. Every real side effect is neutralized: `DATA_DIR` is redirected
to a temp dir (patched *before* importing `padb_web`), and the worker's subprocess
launcher (`_stream`), schtasks (`create_task`/`delete_task`), `taskkill`,
`padb_config.save_config`, and the process-table probes are all stubbed. So it tests
the **route logic** — where the real bugs were (sibling-glob collisions, wrong
Results link, deleting a shared `results_dir`, publish-flag precedence, run/plot
dispatch) — without PADB-R, a Scheduled Task, a killed process, or a written config.
```bash
py qa_webapp.py
```
Baseline: **45 PASS / 0 FAIL**. Part of `qa_selfcheck.py`'s core suite.

### `qa_stats_recompute.py` — independent statistics recompute (the "uniformly-wrong" blind spot)
Self-consistency checks (table↔plot, cross-view GF) all agree even when a value is
*uniformly wrong everywhere* (wrong population fed into a statistic, raw rows instead
of per-DUT means, an off-by-one tolerance-interval order statistic). This gate is the
only one that can catch that: it builds a deterministic synthetic dataset whose true
values are known **analytically** (from `qa_padb._synth_value`), calls the real
aggregators (`padb_plots._aggregate_stat_data` / `_aggregate_box_data_by_temp`), and
compares mean / std / Q1–Q3 / whiskers / outliers / Shapiro W,p / NP-TI / delta-env
field-by-field to an independent recompute. Browser-free and deterministic.
```bash
py qa_stats_recompute.py
```
Baseline: **43 PASS / 0 FAIL**. Part of `qa_selfcheck.py`'s core suite.

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
py qa_selfcheck.py       # Gate 1 : umbrella — compile + qa_padb + qa_viewer +
                         #          qa_js_segments + qa_stats_recompute vs baseline
py qa_padb.py            # Gate 1a: regression baseline (expect 37/4)
py qa_js_segments.py     # Gate 1b: per-view JS drift guard
py qa_stats_recompute.py # Gate 1c: independent stats recompute (expect 43/0)
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

**env_coverage / distribution Site Population Check (added 2026-09-13).**
`runSiteFencePanel` gates the SR-fence panel on those two compare views (selectable
basis — env_coverage: Room baseline vs ΔEnv drift; distribution: Absolute vs ΔTemp):
panel produces rows; every OUTSIDE value is truly outside its stated fence and every
inside truly inside; each fence is a valid Tukey fence (n≥4, lo≤hi); both bases
render non-blank; the live k is monotonic; and the panel CSV matches on screen.
Self-skips off a compare env_coverage/distribution page.

**Histogram Site Population Check — SR-fence membership (added 2026-09-13).**
`runHistogramSite` gates the histogram's cross-site fence panel: it independently
recomputes the PRIMARY_SITE (SR) 1.5×IQR fence per non-Site dimension combination
and the inside/outside classification from the raw data, compares to the panel's
rows (catches wrong bucketing / a uniformly-wrong fence), checks every OUTSIDE
value is truly outside its stated fence, the panel CSV matches on screen, and
drives the full **edit-reimport workflow** the histogram needs *because it has no
Global Filter* — export → delete a bad SR DUT's rows → reimport → assert the fence
re-wires and recomputes from the cleaned SR population. Self-skips off a compare
histogram.

**CSV-export-matches-screen + import round-trip (added 2026-09-13).** For every
view with an export, `runCsvExport` proves the exported CSV is *what you see*, not
the whole dataset. It captures the download by intercepting the `Blob` constructor
(exports download rather than return text), then checks: **nonblank**; **tracks a
filter** — narrowing a condition/serial/temp checkbox (or, when that doesn't move a
length-based signature, the frequency range) must shrink the export and undo must
restore it exactly; **matches the on-screen count** — histogram CSV rows ==
plotted measurements, summary `exportTableCSV` rows == Results-Table rows; and
**import round-trip** — re-importing a just-exported histogram CSV (`_hApplyImport`)
reproduces the identical plot + table. This found and fixed a real bug: summary's
`exportTableCSV` was emitting condition/temp-filtered rows too, mislabeled "GF
Excluded" (it diffed against the whole dataset instead of the GF-bypassed
selection) — a superset of the on-screen table. Verified across all views
(histogram/summary/stat_summary/env_coverage/scatter pass; boxplot has no table
export → clean skip).

```
py qa_filters.py                          # all compare boxplots under C:\temp\data
py qa_filters.py --root <dir> --include-single-site
py qa_filters.py --page <one_boxplot.html>
```

**Validated:** passes 15/0 on a freshly-built compare boxplot; correctly FAILS
`outliers-GF-precise` on an old-code page (the pre-2026-09-10 whole-DUT
over-exclusion — `removed=63, 23 outside the outlier identity`). Tests pages AS
BUILT, so rebuild a page (padb_v2.py) before testing it if it predates a fix.

**All 7 view types are covered (2026-09-10).** Rather than a bespoke per-point
reader for each (the views are too idiosyncratic -- per-serial `scattergl` traces,
aggregated per-condition traces, `type:'histogram'` x-only traces, distribution's
own plot-div id, and five different serial/temp checkbox class families), the
generic layer uses one oracle-free invariant that works everywhere:
**filter reversibility** -- toggle a filter checkbox off then back on (via a real
`change` event) and the plot's *signature* (every data trace as `name:len`,
sorted; `len` = y-array or, for histograms, x-array) must return to the exact
baseline. A filter that does nothing changes nothing (reported soft); a filter
that corrupts state fails to restore (hard FAIL). Plus baseline-not-blank and
reset-restores (tries `clearEverything`/`resetFilters`/`resetView`/`hResetFilters`).
The **boxplot** additionally gets the deep point-precise GF set-difference checks
(it's the view where GF is *set*). The Plotly graph div is found generically
(`#plot` or `.js-plotly-plot`), and Show Points is NOT forced in the generic path
(reset toggles it, which would false-positive reset-restores). Validated with no
false positives on current scatter/stat_summary/summary/env_coverage/distribution/
histogram/boxplot pages, and it still catches the pre-fix boxplot GF over-exclusion.

**Table cross-check (added 2026-09-10).** Beyond plot self-consistency, every view
with a Statistics/Results table (`#box_stat_panel`, `#stat_panel`, `#sum_table_wrap`,
`#h_stats`, `#ec_stat_panel`) is now cross-checked against the plot and for internal
sanity — the "are you also verifying tables, statistical and otherwise?" ask. It is
deliberately **oracle-free**: it never recomputes Shapiro/NP-TI/k-factor from
scratch, only checks relationships the table must satisfy however it computed. The
harness force-opens the collapsible panel *once* (idempotent — only toggles if the
panel is hidden — since a double-toggle would leave it closed and `update()` skips a
closed panel, reading stale DOM) and refreshes without re-toggling. Checks:
- **table-not-blank** — an open table has rows whenever the plot has data.
- **table-conds-are-plotted** — every table Condition row is an actually-plotted
  trace (`All` allowed as an aggregate label); catches phantom/stale rows.
- **plotted-groups-in-table** — every `type:'box'`/`type:'histogram'` primary trace
  has a table row (the clean reverse direction, by trace type).
- **table-n-matches-plotted-points** — where per-point data is embedded (histogram),
  each condition row's `n` equals that trace's value count, and the `All` row equals
  the total. The one *exact* oracle.
- **table-stats-sane** — per row: `Q1≤Median≤Q3`, `Min≤Mean≤Max`, `Std≥0`, `n≥1`,
  `%out-of-spec∈[0,100]`, `p95≤p99≤Max`, TI `[lo,hi]` with `lo≤hi`, and margin signs
  agree with the pass/fail token (`PASS`⇒no margin<0; a `✔` cell ⇒ value≥0). Runs
  whatever columns the view exposes (1340 checks over 335 rows on a real
  stat_summary; 42 over 14 on the compare boxplot).
- **table-updates-on-filter / table-restores-on-filter** — toggle one filter off:
  the plot changes *and* the table digest changes together, then both restore. A
  view whose table only refreshes on an explicit Refresh (the large-dataset design)
  is honored (it force-refreshes and passes with a note), not failed.

Views without such a table (scatter, distribution) skip `table-present`; env_coverage
gets consistency + update checks (its UDE/LDE columns aren't in the sanity set, so
`table-stats-sane` skips there). Validated with no false positives across all view
types. `--verbose` prints every check (pass/skip too), not just failures; stdout is
UTF-8-reconfigured so table arrows/checkmarks (`↑↓✔✘`) in details don't crash the
cp1252 console.

**Heavy-page hardening (added 2026-09-10).** The big compare boxplots (5-10 MB,
hundreds of thousands of embedded points) used to intermittently produce *no
`#__qa_results` sentinel* — reads happened mid-render, so any result off them was
untrustworthy. Four fixes made them deterministic:
- **Readiness gate** — the harness no longer fires at a fixed delay; it waits until
  the Plotly graph div actually has data traces before starting. Crucially the
  "ready" predicate treats a `type:'box'`/`type:'histogram'` trace as rendered even
  with **no `x`/`y` point arrays** — under `binary_encode` the box is drawn from
  precomputed q1/median/q3, so `t.y` is empty; the old "has points" predicate never
  fired and the poll spun out the whole virtual-time budget (the actual root cause
  of every "no sentinel"). If the plot genuinely never renders, a clean
  `render-not-ready` sentinel is emitted (reported as `[HEAVY]`, not a logic FAIL).
- **Size-scaled budget/timeout** — `--virtual-time-budget` and the kill timeout auto-
  scale with HTML size (a 9 MB page gets ~200k ms), since the budget is consumed
  during async Plotly render gaps and a too-small budget lets Edge dump the DOM
  before `run()` finishes. `--no-scale` and explicit `--budget`/`--timeout` override.
- **Reduced deterministic suite on ≥ 8 MB pages** (`_HEAVY_MB`, `--no-heavy-mode` to
  force full) — the generic filter-reversibility loop and the table cross-check do
  many `update()`s / rebuild a huge stats table, whose async work races the dump.
  On a genuinely heavy page those are skipped and only the **deep GF block** (the
  point-precise outlier/GF invariants — what a compare boxplot is actually gated
  for) runs, which is fast (~0.2 s) and deterministic. Light/medium pages (≤ 7 MB)
  still run the full suite.
- **Batched checkbox sets** — the filter-GF serial loop sets all checkboxes without
  firing per-serial `change` events, then `update()`s once (was 2×N expensive
  updates on a many-serial page), and `digest()` is an O(1) fingerprint instead of
  serializing thousands of table rows.

Result: PM1 (5 MB) full suite 27/0 in ~5 s; AM1_Flatness (7 MB) full suite
deterministic in ~12 s; AbsoluteAccuracy_PM/NA (9-10 MB, previously silent timeouts)
reduced suite deterministic in ~20 s. These heavy pages now reproduce a **real** GF
over-exclusion (`outliers-GF-precise` FAIL: "Set outliers as GF" drops whole DUTs
across all x-axis frequencies on the AmplitudeAccuracyClosedLoop compare boxplots,
whose coarse GF keys carry the carrier "Frequency (MHz)" condition but not the swept
modulation-frequency x-axis — the secondary-numeric-dimension case; the e3b53d8
`|Freq=` fix's path isn't hit here). That is a genuine `padb_plots.py` bug to fix,
now that the gate can measure it deterministically.

**Remaining depth (optional):** GF-*consumption* checks on the aggregated views
(inject a GF serial, assert its contribution drops) -- the reversibility + table
layers already cover the filter/table-consistency bug class those would target.

### Track-1 sweep result (2026-09-10)

First full non-boxplot sweep: 35 pages across every non-boxplot view (compare +
single-site: ClockSpurs, Harmonics, MaxPower, phase-noise, switching-speed).
**132 invariant checks PASS, 0 genuine filter failures** — the shipped
scatter/stat_summary/summary/env_coverage/distribution/histogram filters are
self-consistent (reversible, non-blanking, reset-clean) on real data.

The only non-passes were headless render **timeouts** (no `#__qa_results`
sentinel), not product bugs: the two **ClockSpurs stat_summary** pages (151
fragmented conditions × Shapiro/NP-TI, which the reversibility loop recomputes in
full several times) don't complete even at `--budget 90000` — a harness-scaling
limit on the single heaviest page type, not a page defect (the same stat_summary
code path passes on Harmonics/MaxPower, and the page renders fine for a user).
ClockSpurs distribution passed once `--budget` was raised to 45000. Practical
guidance: run heavy datasets with a larger `--budget`; treat the very heaviest
stat_summary as spot-check-manually.

Two harness-robustness bugs were fixed in this pass: temp-dir cleanup raced with
a lingering msedge holding `dom.html` (`ignore_cleanup_errors=True` +
wait-after-kill), and one page's harness crash could abort the whole sweep
(per-page try/except in the runner).
