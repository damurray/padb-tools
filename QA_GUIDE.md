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
This umbrella runs compile + `qa_padb` + `qa_viewer` + `qa_viewer_ui` + `qa_limits` +
`qa_viewer_scale` + `qa_js_segments` + `qa_stats_recompute` + the other suites listed below, all on
internally-generated synthetic datasets (the browser-tier ones honest-exit when Chromium is unavailable), and prints a
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

### `qa_subpop.py` — subpopulation / dual-distribution detector
Deterministic tests for `padb_subpop.detect_subpopulations` — the "N serials are
behaving differently — action required" advisory (Workflow & Recommendations). Proves
the statistics before any view UI: on hand-built synthetic slices it must FLAG a
planted minority subpopulation (teeth) and must NOT flag the false-positive shapes
(unimodal, continuous spread, one-off, within-budget separation, small-n) — the guards
are shown non-vacuous by a permissive parameterization that DOES flag them. Also covers
budget-anchoring (a separation within the M.U./env-drift budget is clean, beyond it is
flagged), the recurrence gate, cross-field (station) correlation, and the advisory-only
caveats. Browser-free and deterministic.
```bash
py qa_subpop.py
```
Baseline: **18 PASS / 0 FAIL**. Part of `qa_selfcheck.py`'s core suite.

### `qa_csv_to_parquet.py` — CSV → Parquet converter
QA for `padb_csv_to_parquet.py` (an encoding change only: same rows, columns, values).
Fidelity is judged by an **independent** comparer (stdlib `csv` vs the parquet, never the
converter's own reader) over a seeded fuzz CSV — unicode, commas, quotes, embedded newlines,
blank cells, datetimes, CRLF, a UTF-8 BOM. Also: streaming/multi-batch row counts, a type
change after the first chunk on the ragged-row path (must fail loudly, never truncate),
`--verify` catching a dropped row / reordered columns / float32 loss, atomicity (a dying
conversion leaves no `.parquet`/`.part` and never clobbers an existing one), and CLI exit
codes / folder mode / `--out`. **Teeth:** the comparer and `--verify` are run against
deliberately broken converters (drop last row, float32, reversed columns) and must catch each.
```bash
py qa_csv_to_parquet.py                    # synthetic, deterministic
py qa_csv_to_parquet.py --sweep <folder>   # also convert + verify every real CSV in a folder
```
Baseline: **29 PASS / 0 FAIL**. Part of `qa_selfcheck.py`'s core suite. (It caught a real bug in
the converter's `--verify`: a datetime column was a false mismatch.)

### `qa_loader_adapter.py` — padbloader adapter + webapp drop zone
Synthetic seeded padbloader parquet (two algorithms: narrow + wide sweep). **Adapter:**
`list_algorithms`; parquet output loads identically to the CSV output through the real loader;
the high-cardinality per-row id stays out of Group; wide sweeps default to `scatter`+`summary`
(narrow ones carry no `views`); an existing job file is kept unless `force`; the `{algo}` publish
placeholder; a real `padb_v2` build from the generated `parquet_path` job. **Routes** (Flask
test client): `/api/upload-parquet` (multipart, in-place path, junk/missing/wrong-extension
rejected) and `/api/generate-parquet-jobs` (jobs written, Folder-name / Share-path publish
rules, skip-existing, validation errors). **UI** (browser tier, Playwright; SKIPs honestly when
unavailable): a real drop onto the drop zone lists the algorithms, Generate writes only the
ticked ones, the jobs table shows them, no page errors. **Teeth:** the wide-sweep rule and the
`force` guard are shown to change behaviour when broken.
```bash
py qa_loader_adapter.py
```
**Multi-result algorithms:** an algorithm with several results in different units (harmonics: dBc + two dBm) must never be plotted as one stream -- tested in both modes: *combined* (one job per algorithm, `Result` Group key, per-row Units, `result_default`, a single-result algorithm gets no Result key) and *split* (a job per result); selecting only the main datum must not drag in the dBm ones; and a **browser test** that the combined page opens with ONLY the main result ticked (y range all dBc), then ticking the dBm result brings it into the plot (teeth: without `result_default` all results open ticked -- shown).
**Plots-to-generate / test steps / Running Jobs:** `n_steps` and the `Test Step` column (native `Room`/`NN.N Deg C`; the real loader then sees 3 temperatures), env views kept for multi-temperature data and dropped (with a note) for single-temperature data, an unrecognised step name warned and counted as Room, an unknown/empty `views` list rejected (adapter ValueError, route 400), all 7 views really building from a multi-step job, and browser checks: Room data offers 5 views all ticked and no environmental ones, a wide sweep shows heavy views ticked with a warning that disappears when only a narrow result is ticked, the job's `views` equals exactly the ticked plots, "Run the plots now" puts the job in the Running Jobs panel, and multi-temperature data offers all 7 ticked.
**padb-tools' own parquet:** a synthetic sidecar (incl. an all-null `Upper Limit (<=)` column, parquet type null, like the real EP5 file) is sniffed (`padb` / `padbloader` / junk -> None), described (usable rows exclude a value-less row), turned into a job that points at it in place (never copied; wide sweep -> scatter+summary; force/exists; env views dropped), loaded and built by `padb_v2`, listed by the CLI, accepted by the upload/generate routes (junk rejected with a clear message) and shown in the drop zone with one entry, no combine option and the wide-sweep warning.
Baseline: **78 PASS / 0 FAIL**. Part of `qa_selfcheck.py`'s core suite.

### `qa_viewer.py` — parquet viewer data/API parity
**What it pins:** the DuckDB engine and the pandas engine return the same envelope, counts, spec stairs and band slices (byte-identical band CSV); condition filters and group-by are compared to an independent oracle (pandas over the parsed Group text); `padb_bands` / `padb_make_bands`. **Teeth:** narrowing must remove rows; the engines are cross-checked, not assumed. `py qa_viewer.py` -> **91 PASS / 0 FAIL**. Part of `qa_selfcheck.py`.

### `qa_viewer_ui.py` — a real browser drives the viewer (added 2026-11-22/23)
Starts the real `padb_viewer.py` on a generated fixture parquet (a limit **stair**, a row exactly **on a band edge**, real fails) and drives its overview page and the band-view pages it builds with Playwright, checking every number against an independent pandas oracle. Invariants: **INV-COND / INV-SITE / INV-GROUP** (filters, live checkboxes, group-by partition), **INV-SEG / INV-RAPID** (each Next lands on the band; three rapid clicks end on the last window), **INV-HOVER**, **INV-LIMITS**, **INV-EQ-\*** (drag-zoom == typed window == oracle; conditions + window; Next changes only the window), **INV-PERSIST** (a reload restores window/filters/group-by/segment position; Next continues from it; Reset all; corrupt storage), **INV-ORA-\*** (row/serial counts, per-band counts with the edge row counted in both bands, limit stairs, envelope keeps real points and the global min/max, uncapped returns every row), **INV-BAND-\*** (the pages the viewer builds honour window + conditions; Passing U Failing == All), **INV-VIEWARGS** (empty window args never give a bare 500), **INV-FILTCFG** (which conditions are exposed: bad config -> 400 and nothing changes; hiding Port clears its narrowing while AlcState stays narrowed; exposing the serial-like Serial Number filters == oracle; rename + reorder; file saved; reload keeps config and selections; Reset to automatic; an imported filter naming a hidden condition is skipped with the reason; a 45-value condition gets a search box and narrows == oracle), **INV-DDROW** (the condition filters as a row of dropdowns: one button per dimension; narrowing == oracle; the row and the checkbox list stay in sync **both ways**; badge, Select all on/off, outside click closes, reload restores), **INV-BANDEDIT** (invalid bands rejected with nothing changed; edit/add/delete through the real UI; file rewritten; counts == oracle; stepper walks the new bands) and **INV-LIMEDIT** (invalid limits rejected; an override changes Failing totals == oracle, the drawn line and the band views with no stale cache; reload; Clear all). **Teeth proven by mutation** (each break fails at least one check): saveState no-op (7), loadState ignoring conditions (2), envelope losing the max, band count off at the edge, band views ignoring filters (4), zoom not driving the window (2), no band recount / no file write / invalid band accepted, fail flag not recomputed, band cache ignoring limits, limits not passed to the band build. Honest-exits **3** (`ENV-UNAVAILABLE`) without Playwright/Chromium. `py qa_viewer_ui.py` -> **128 PASS / 0 FAIL**. A `qa_selfcheck.py` suite.

### `qa_viewer_scale.py` — time / memory limits on a large file (added 2026-11-23)
Generates a ~2M-row synthetic parquet (thousands of distinct x, ~100k distinct Group strings, a limit stair), starts the real viewer and asserts: ready within 90 s and under 2 GB (measured **3.3 s / ~210 MB**), band counts and totals == a numpy oracle, full-range / windowed / condition-filtered queries within 8 s (measured **0.16-0.26 s**), the envelope still keeps the global min/max, and a huge band returns the "Band too large" card quickly instead of a render attempt. The budgets are deliberately generous (shared machines); they catch order-of-magnitude regressions such as a query reverting to a full pandas scan. **Teeth:** the pandas engine on the big file fails the condition check; a changed guard text fails; an envelope that loses the max fails. `py qa_viewer_scale.py [--rows N]` -> **11 PASS / 0 FAIL** (~10 s). A `qa_selfcheck.py` suite.

### `qa_limits.py` — editable limit overrides (added 2026-11-23)
No browser. **LIM-VALID** (bad ranges, non-numbers, lower > upper, empty rows, >200 ranges rejected with a message); **LIM-ORACLE** (`padb_limits.effective` == an independent scalar re-implementation on edge cases and 40 random sets: edges inclusive, **later wins at a shared edge/overlap, per side**, a blank side keeps the data's value, rows outside every range untouched); **LIM-SQL** (the DuckDB `CASE` == numpy on 25 random sets); **LIM-ENGINES** (pandas and DuckDB DataSets give Failing/Passing totals == an oracle recomputed from the raw data on 4 windows x 3 limit sets, identical spec stairs, and limit-less data gains/loses a pass/fail basis); **LIM-REVERSIBLE** (`set_limits([])` restores the data's own verdicts exactly); **LIM-FILE** (round trip, unit conversion, corrupt/invalid/missing file never raises); **LIM-V2** (`padb_v2._apply_limit_overrides`: Upper_Limit **and** Spec_Hi follow the override because the classifier prefers Spec, uncertainty zeroed, columns created, relative `limits_file`, a bad file never fails a build). **Teeth proven:** lower>upper accepted (1 FAIL), earlier-wins (3), exclusive edges (11), Spec_Hi not set (1). `py qa_limits.py` -> **51 PASS / 0 FAIL**. A `qa_selfcheck.py` suite.

### `qa_limits_views.py` — limit overrides in the aggregate views: in-page import == build-time override (added 2026-11-23)
The strongest oracle for 'does this view apply an imported limits file correctly' is the build itself. For each converted view it builds the same data twice -- with `limits_file` at BUILD time (the authoritative Python path) and plainly -- imports the same file into the plain page, and compares a snapshot of every verdict the view computes (`stat_summary`: per-frequency `computeFreqResult` results, the DUTs' limit records, page constants -- ~11,800 fields) plus a teeth check (the plain page must differ from the build-time one) and the bar's 'applied' state. It also holds **BUILD-GROUPSPEC**: with the spec carried IN THE GROUP TEXT (the usual case) a build-time override must win in scatter, boxplot, stat_summary and summary, and a page built without it keeps the data's own spec -- the regression for a real defect (scatter and boxplot reload through a temp CSV and used to lose the override on Spec_Hi / uncertainty). **Teeth by mutation:** the scatter / boxplot reload not re-applying the override, the stat pre-pass patching only `spec_up`, the pre-pass not called. Covers scatter, stat_summary, summary, boxplot and reference (histogram has no swept x). Teeth also: summary / boxplot / reference pre-pass not called, page-spec remake disabled. Honest-exits 3 without Chromium. `py qa_limits_views.py` -> **12 PASS / 0 FAIL**. A `qa_selfcheck.py` suite.

### `qa_limits_inpage.py` — limit overrides applied in the page (added 2026-11-23)
The semantics exist as numpy / DuckDB SQL (`padb_limits.py`) and as JavaScript in every page, so the main job is **conformance**: **LIMP-CONF** (`PADB_limovValidate` == `padb_limits.validate` -- accept/reject, the message, the normalised list -- on a 24-case corpus + 300 random mutations; `PADB_limovFor` == `override_arrays` at random x including every range edge, 60 range sets; `PADB_limovUnitScale` == `padb_bands.unit_scale`), **LIMP-E2E** (on a REAL built scatter page: Import via the bar -> reload -> Failing count == a pandas oracle; rows carry Upper_Limit AND Spec_Hi, uncertainty 0; Export writes the viewer's file format that Python validates back; Clear restores the data's own verdicts; a kHz file is converted; a bare list imports; a bad / non-JSON file is refused with Python's message and stores nothing), **LIMP-NOTICE** (the boxplot says 'NOT applied in this view' instead of silently using the data's limits; Clear works from there). **Teeth by mutation:** first-wins instead of later-wins, exclusive edges, only Upper_Limit set, lower-above-upper accepted, unit not converted, pre-pass removed, booleans accepted by Python. Honest-exits 3 without Chromium. `py qa_limits_inpage.py` -> **43 PASS / 0 FAIL**. A `qa_selfcheck.py` suite.

### `qa_filter_interchange.py` — one filter file across the viewer and the HTML pages (added 2026-11-23)
Builds a REAL scatter page (via `padb_analyze`, in a subprocess), starts the viewer, and drives both in a browser against an independent pandas oracle. **X-V2H** (a filter exported by the viewer applies on the page: conditions + window + Failing == oracle), **X-H2V** (a filter set on the page, read with the page's own lock reader, imports into the viewer: viewer count == page count == oracle), **X-UI** (the Export button downloads the lock; the Import button applies a file; a partial file skips and LISTS the unknown conditions; a junk file is refused), **X-HTTP** (junk -> 400). Each comparison is against the oracle recomputed from the raw data and the narrowed filter must be a strict subset. The mapping itself is unit-tested in `qa_core` (**CORE-LOCK**: 300-spec round trip, fully-open dims omitted, best-effort skips). **Teeth by mutation:** pass/fail mapped wrongly, conditions ignored on import, fully-open dims exported, skipped items not reported, the UI forgetting pass/fail. Honest-exits 3 without Chromium. `py qa_filter_interchange.py` -> **17 PASS / 0 FAIL**. A `qa_selfcheck.py` suite.

### `qa_separation.py` — the analysis layer works with NO extraction code (added 2026-11-23)
Builds the analysis bundle (`make_analysis_bundle.py`), copies it to a fresh folder and runs `padb_analyze.py` FROM THAT FOLDER in a different working directory with `PYTHONPATH` cleared -- so a missing file or a hidden dependency on `padb_run` / `padb_batch` / the scheduler / `padb_make_v2_job` / `padb_config` / the webapp fails. **SEP-BUNDLE** (no extraction module in the bundle; the builder REFUSES a closure that reaches `padb_run`, even via a lazy import -- tested on a synthetic tree), **SEP-ISOLATED** (those modules are not importable from the bundle), **SEP-RUN** (a padbloader parquet, a padb-tools parquet and a PADB CSV each -> ok, an `index.html` and the requested views; a limits file is applied; stdout is one JSON document), **SEP-REQUEST** (8 malformed requests -> exit 2 with a message, no traceback). **Teeth by mutation:** a lazy `import padb_run` in `padb_v2` (qa_core + the builder refuse), progress text leaking to stdout, `padb_plots` importing `padb_config`, `limits_file` ignored. `qa_core` `CORE-BOUNDARY` is the fast static twin (zero exceptions). `py qa_separation.py` -> **29 PASS / 0 FAIL**. A `qa_selfcheck.py` suite.

### `qa_compress.py` — deflated embedded page data (`padb_compress.py`)
**Rewrite rules:** only a large single-line `var NAME=<json>;` is rewritten; a payload containing `NaN`/`Infinity` (JS accepts, `JSON.parse` rejects), two statements on one line, a missing `;`, a constant under the size floor, and already-dense data are all left alone; CRLF line endings survive; a second pass changes nothing. **Hook:** `compress_data` false / auto (size floor) / true / `compress_data_auto_mb`; never raises. **Decoder (browser tier, honest SKIP):** the synchronous inflate decodes stored blocks, fixed Huffman, dynamic Huffman (levels 1 and 9), long back-references, unicode and a single byte to *exactly* the original JSON; a wrong declared length is rejected; and a rewritten page behaves like the original -- a later script still reads the constant at parse time and its `DOMContentLoaded` handler still fires. **Teeth:** a decoder with a wrong longest-match length (258 -> 257) is detected, and with the JSON guard disabled the NaN page *is* rewritten. (Start Playwright before importing `padb_v2` in one process -- the import makes the sync driver hang on Windows; the suite orders its tiers accordingly.)
```bash
py qa_compress.py
```
Beyond this suite, the speed work was verified by **byte-comparison of real pages**: old code == new code serial == new code parallel on four views of a 680k-row build; five views (incl. boxplot) identical on a 60-offset slice; and the five real 965k-row EP5 pages, after inflating the compressed constants, identical to the pages the old code built. Baseline: **26 PASS / 0 FAIL**. Part of `qa_selfcheck.py`'s core suite.

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
py qa_selfcheck.py       # Gate 1 : umbrella — compile + every suite above vs baseline
                         #          (qa_viewer / _ui / _scale, qa_limits, qa_js_segments, ...)
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
| Room-only + serial (auto view-selection → scatter+boxplot+reference+summary+stat_summary; no distribution/env_coverage) | AMC2 Close-In |
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

## `qa_jsrules.py` — behavioral cross-view rule gate (added 2026-09-15)

`py qa_jsrules.py` — executes the **shipped shared JS rules** (`_COMMON_JS`'s
`PADB_num`/`PADB_specClass`/`PADB_isFail`/`PADB_fence`, plus `_spSpecClass` from
the shared Site panel) under Playwright's Chromium and asserts a truth table
(side-aware pass/fail, strict `>/<`, string-bound coercion, `n/a` cases; Tukey
fence values, `<4`→null, k-scaling) **and** that `_spSpecClass` behaviorally
agrees with `PADB_specClass`. Since the hardening passes made every view *delegate*
to those single definitions (source-pinned in `qa_regressions`), this proves the
one rule is correct → all views agree by construction. Browser-tier: **honest
`exit 3` (ENV-UNAVAILABLE)** when no Playwright browser is reachable (e.g. this
sandbox, where Python can't see the browser) — not a failure; verify via the
in-app browser instead (done 2026-09-15: 14/14). `qa_regressions.test_jsrules_
behavioral_gate_present` pins that the gate exists and its battery isn't gutted.

## `qa_filters.py` — filter / Global-Filter self-consistency gate (added 2026-09-10)

Where `qa_view_sweep.py` proves a page *renders*, `qa_filters.py` proves its
**filters are self-consistent** — the recurring bug class (a filter, especially
the Global Filter, that doesn't do exactly what it says, or a plot/table that
drift apart on a filter change). **Covers every interactive view** (2026-09-14) —
scatter, stat_summary, boxplot, distribution, env_coverage, summary, histogram —
since the coupling checks are view-agnostic; the default sweep discovers all of
them (compare pages by default; `--include-single-site` for the rest). **The
filter/plot/table/statistics couplings verified per plot type:** filter→plot
(reversible), filter→table (updates, not stale), plot↔table (every row plotted &
vice-versa), statistics sanity (Q1≤med≤Q3, min≤mean≤max, %oos∈[0,100], TI lo≤hi,
margin sign ↔ pass/fail), frequency-range + drag-zoom coordination (both plot AND
table move together and restore), group-by robustness, and CSV-export-matches-
screen. Compare-aware (cross-site Site-fence panels on top). Verified in-app-
browser: 0 coupling failures across all 7 view types.

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
