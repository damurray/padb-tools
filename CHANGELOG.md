# Changelog — 2026-08-17 to 2026-11-23

Pulled directly from git history. Newest summary first; the full day-by-day log follows.

---

### 2026-11-23

- **FIX: a `limits_file` / viewer limit override was IGNORED by the scatter and boxplot views whenever the data carries its spec in the Group text** (PADB's `Upper Spec` / `Upper Uncertainty` grouping items -- the usual case). Those two views reload their data from a temporary CSV that carries only `Upper Limit` / `Lower Limit`; `Spec_Hi` / `Spec_Lo` / uncertainty come back from the Group text, and the classifier prefers Spec, so the data's own spec silently won (scatter `Spec_Hi` stayed -76 with an override of -90). Fix: `padb_limits.apply_from_cfg` re-applies the override after each reload (scatter, boxplot, interactive boxplot); `padb_v2` now uses the shared `padb_limits.resolve_from_cfg`. Pinned by `qa_limits_groupspec` (4 checks: the override wins in both views; pages built without it keep the data's spec; **fails on the unfixed code**). Ported from v3.0 (`2041e51`). Pages built before this must be rebuilt.

- **Viewer: condition filters as a row of dropdowns.** A **Conditions:** row of buttons (one per dimension, same look and behaviour as the HTML views' filter buttons: a tick panel with *Select all*, an `n/total` badge and a highlight when narrowed, closes on an outside click) sits above the existing checkbox list; the **Conditions** button still opens the full list. Both are views of the SAME `.condchk` state, so the server query, saved state and band views never see two truths; list changes refresh the row (`syncCondRow`) and row changes write back to the list; a reload restores the row. `qa_viewer_ui` 101/0 (INV-DDROW: one button per dimension, dropdown narrows == oracle, row->list and list->row sync, badge, Select all on/off, outside click, reload). Teeth: row change not copied to the list (5 FAIL), list change not refreshing the row (2), reload not mirrored (1).

- **Viewer: live band editor.** **Edit bands** (next to the segment stepper) opens a table of name / min / max with Add / delete / Revert / Apply. Apply POSTs `/api/bands`, which validates (max 64, non-empty unique names, finite numbers, lo < hi; edges inclusive, overlaps allowed), writes the shared band file atomically (`padb_viewer_bands.json` / `bands.json`, the same file the HTML builds read) and updates the live dataset: band buttons, per-band counts, the heavy-band warning, the band-view row cap and the Named-bands stepper follow without a restart. A failed write or invalid input changes nothing. `GET /api/bands` returns the current set. `qa_viewer_ui` 72/0 (INV-BANDEDIT: 5 invalid payloads -> 400 and nothing changed, edit+add via the real UI, file rewritten, counts == oracle for the new edges, buttons rebuilt, stepper walks the new bands, invalid UI edit shows the error, delete + reload). Teeth: no recount (2 FAIL), no file write (1), lo>=hi accepted (3). Not yet: the band VIEW pages (padb_v2 renders) still derive their own named bands, so stepping *inside* an iframe uses those, not the edited set.

- **Viewer: state persistence + oracle + scale QA.** (1) **Persistence:** the overview now saves window, max-points, site/temp/condition filters (as the UNTICKED values, so new values default on), group-by, pass/fail, limits, full-render and segment basis+position to `localStorage` per dataset (`padb_viewer_state:<parquet>:<rows>`), restored on load; new **Reset all** button clears it; corrupt/blocked storage falls back to defaults (try/catch everywhere). (2) `qa_viewer_ui.py` (now 57/0): INV-PERSIST (reload restores window/conds/group-by/limits/max-pts and the same population == oracle, axis follows the window, Next continues from the restored band, Reset all, corrupt storage), INV-ORA-* (row/serial counts, per-band counts incl. a row ON the band edge -- edges inclusive both ends, limit stairs == oracle runs, capped envelope returns only real rows + keeps the global min/max, uncapped returns every row, Room-only env_coverage message). Fixture now has a limit STAIR (-76 then -74) and a band-edge frequency. Note the API clamps `maxpts` to >=100. (3) **`qa_viewer_scale.py`** (new, 11/0, ~10 s): a 2M-row synthetic parquet -- ready 3.3 s / 212 MB RSS, full-range query 0.26 s, windowed/condition-filtered 0.16 s, totals == numpy oracle, huge band -> the 'too large' card in 0.0 s; generous budgets (90 s load, 8 s query, 2 GB) so it only trips on order-of-magnitude regressions. Both are `qa_selfcheck` suites. Teeth proven by mutation: saveState no-op (7 FAIL), loadState ignoring conditions (2), envelope losing the max (1 each harness), strict-lower band count (1), pandas engine on the big file (conditions missing), guard text changed (1).

- **Viewer QA: gate check + filter equivalence + band views built by the viewer.** `qa_viewer_ui.py` (now 37/0) is a real `qa_selfcheck` suite (browser tier; honest-exits 3 with `ENV-UNAVAILABLE`). New invariants: **INV-EQ-ZOOM** (drag-zoom == typed frequency window == pandas oracle; Autoscale -> full), **INV-EQ-COND+WIN**, **INV-EQ-STEP** (Next changes only the window, conditions untouched), **INV-BAND-WIN/COND** (the `/view` pages the viewer builds honour the window and condition filter: scatter n == oracle, boxplot/stat_summary/summary show fewer conditions and none from the unticked value), **INV-BAND-PART** (Passing U Failing == All on a viewer-built stat_summary band), **INV-VIEWARGS** (empty `flo`/`fhi` = full range, junk -> 400, never a bare 500). Teeth proven: band views ignoring filters -> 4 FAIL; zoom not driving the window -> 2 FAIL. A dimension narrowed to one value is dropped from a page's condition text by design, so the checks compare filtered vs unfiltered pages. Fixture limit is now -76 so it has real fails. Not done: viewer state persistence across reloads, oracle checks for envelope/limits, large-file time/memory limits.

### 2026-11-22

- **padb_viewer: condition filters, live checkboxes, hover conditions.** (1) A **Conditions** panel lists every `Key: value` dimension in Group (2-40 values; serial/temp/site keys excluded); unticking narrows the overview plot AND the band views (`/view` now carries sites/temps/pass-fail/conditions; DuckDB engine only -- the pandas fallback has no condition filters). Blank dim = no constraint; all-unticked = nothing. (2) Site/Temp checkboxes now apply on change (they used to need the Update button). (3) Hover shows Serial, Temp and the point's full condition text. (4) `update()` drops stale responses and keeps its relayout guard until Plotly settles, so fast Next/Prev clicks cannot leave an old window drawn. The overview is a min/max envelope per x bucket, so narrowing a condition changes the picture only where the extremes move; the status line shows the true point count. (5) **Group by** (Site / Serial / Temp / any condition, max 60 groups) and a **Show limits** checkbox (dashed upper/lower limit steps from the data). (6) New browser harness `qa_viewer_ui.py` (20/0) drives the overview page: conditions vs a pandas oracle, group-by partition, segment stepping + rapid clicks, hover, limits. (7) Summary view: legend selector **Inside / Right / Below / Hidden** (default inside the plot). Pinned in `qa_viewer` (91/0; oracle = pandas over the parsed Group text; teeth: narrowing must remove rows).

## 2026-11-21 -- filter equivalence + band-step persistence QA (`qa_filter_equiv.py`)

New browser-tier harness driving the real controls on all six views (adversarial sweep with non-round frequencies, three named bands). Asserts: plot zoom == frequency filter (outward, same rows as typing the range), checkbox == plot content, Next/Prev band steps change ONLY the frequency window (bands and spec basis), and a reload restores every setting and the exact frequency window; plus no uncaught page errors. First run: 13 failures across 5 views, all fixed:

- scatter drag-zoom wrote the freq filter with round-to-nearest, so it could be narrower than the visible axis (40 of 120 points hidden). Now outward.
- every view saved the range SLIDER (step-snapped), so reload moved a band [300,400] to [300.16,399.61]. Text boxes now saved/restored verbatim (scatter/stat_summary/summary/env_coverage/distribution).
- group-by, sort, auto-filter basis/level, P/C, boxplot group-by and the Segment-by basis did not persist; they now do (Reset clears them).
- distribution: "Hide spec lines" then any Segment Next/Prev threw (`_extremes`) and the step never completed; toggle now uses `Plotly.react`.

Open, reported as WARN not FAIL: legend-click hides a trace without narrowing filter/table/`n_points`; `summary` has no zoom->filter sync; auto-filter panel open state not restored on reload. Pinned by `test_filter_equiv_and_persistence_fixes` (qa_regressions 759/0). Existing built HTML must be rebuilt.

## 2026-11-20 — v2.0: padbloader parquet integration and build speed

### `summary` render: 3x faster, output byte-identical

Profiled a 38k-row, ~500-condition Closed Loop band (133 s). Cost was per-condition pandas overhead on Arrow-string columns: six `dut_spec_vals` groupby/unstack passes (plus per-temperature twins), a string-keyed per-DUT mean, per-frequency group iteration, a Python modal-limit groupby and a row-wise condition-label apply. Replaced with integer codes computed once per condition and numpy (`_dut_minmax_rows`, `_dut_mean_rows`, `_split_sorted`, numpy `_modal_by_freq`, label per distinct condition). **133 s -> 44 s**; page byte-identical to the old build (Room-only band and a 3-temperature Harmonics set across summary/stat_summary/boxplot). Pinned by `test_summary_vectorised_helpers_match_pandas_reference` (with teeth). Remaining time is spread per-condition pandas calls; next lever would be restructuring around all conditions at once.

### Large-dataset viewer: DuckDB query engine

`padb_viewer.py` now answers every query in DuckDB (default when installed; automatic pandas fallback; `--engine pandas|duckdb`). Real 3.78M-row Closed Loop: load 4.9 s / +2.6 GB -> 1.0 s / +104 MB; scatter queries 20-60 ms. Output identical to the pandas engine (pinned by `qa_viewer` engine-parity checks, including a byte-identical band-slice CSV). Spec-segment tie order at a repeated x is now deterministic (first in file order). `build_viewer.py` bundles duckdb.

### padbloader parquet -> plots (web app drop zone + CLI)
- **padbloader** is a separate tool (a colleague's) that extracts straight from Oracle and exports CSV / Parquet / dbdiff. It owns extraction, credentials and (planned) scheduling; **padb-tools consumes its Parquet**. Its CSV export is not a usable input (one row per attribute; no serial/limits/run attributes).
- **Drop a `.parquet` on the web app's drop zone** (or use one already on disk, in place): it lists the algorithms (rows, units, limits, swept x, temperatures), you tick algorithms and the **plots to generate** (every applicable view ticked; the environmental views are an opt-out and offered only when there is more than one test temperature; on a wide sweep the heavy views are shown ticked with a warning), and **Generate Plot Jobs** writes a `*_v2_job.json` per algorithm. **Run the plots now** (default on) queues them into the **Running Jobs** panel with a live log and Cancel. CLI equivalent: `padb_loader_adapter.py`.
- **Multi-result algorithms.** The unit of selection is *(algorithm, result)*: one padbloader algorithm can emit several results in different units (Harmonics: dBc level + `Carrier Power (dBm)` + `Impurity Power (dBm)`). Default **combined**: one dataset with a leading `Result` filter dimension and the main result pre-selected (`result_default`), so dBc and dBm are never plotted on one axis; untick *Combine* for a separate job per result.
- **padb-tools' own parquet** (the sidecar written for big datasets, or `padb_csv_to_parquet.py` output) is a first-class source too: a job points at it in place with `parquet_path`; the drop zone, `padb_csv_check.py` and `compare_csv` accept it.
- **`padb_csv_to_parquet.py`** — streamed CSV -> zstd parquet converter (80-140x smaller; `--verify` compares row count, columns and the first 1000 cells). Test temperature comes from a `Test Step` column in the loader's native form.
- Known limitation (unchanged, documented): a step such as `-40 Deg C` is read as +40 C by the plot loader (the regex drops the minus sign); non-`Deg C` step text counts as Room.
- QA: `qa_loader_adapter` (78), `qa_csv_to_parquet` (29), new `qa_regressions` pins. Docs: `PADB_Tools_Guide.md` -> **Parquet input and padbloader**, `README.md`, `GETTING_STARTED.md`, `Interactive_Plots_User_Guide.md` (Result filter).

### Build speed (output byte-identical)
Profiled a real 965k-row, 7,662-offset phase-noise build with py-spy; the time was Python loops, not file format.
- **Tolerance interval:** `nonparam_tolerance_interval` did one scalar `scipy.stats.beta.cdf` per (i, j) pair -- O(n^2) per frequency (54% of the summary build). Coverage depends only on `n` and the gap `k = j - i`, so it is now one cached vectorised table per `(n, P, C)`; the narrowest-window tie-break is preserved exactly (n=300: 2.03 s -> 0.0002 s). Pinned against the old double loop on 351 cases incl. ties/NaN, with teeth on the tie-break.
- **Per-frequency DataFrame filters** (`df[df.Frequency == f]` inside `for f in freqs`) replaced by one `groupby` per condition in `render_summary`, `_aggregate_stat_data` and the boxplot aggregation; `iterrows()` / `to_dict("records")` per frequency replaced by column lists.
- **Parallel views** (`parallel_views`, default auto: big data + 2 or more views): each view renders in its own worker process (`padb_v2.py --worker-view`), heaviest first, capped by `parallel_workers` (default min(3, cpus/4) -- memory is the real limit). Pages are identical to a serial build.
- **Compressed embedded data** (`compress_data`, default auto for pages of 20 MB or more): each very large `var NAME=<json>;` becomes `PADB_unz("<deflated base64>",n)` with a small synchronous inflate in `<head>` -- pages shrink **5-16x** (a 313 MB boxplot -> 29 MB; scatter 83 -> 5 MB), so they open from the network share much faster, and the decoded data is byte-identical. In place and synchronous on purpose, so script order and DOMContentLoaded timing are unchanged. New `padb_compress.py`, `qa_compress.py` (26, incl. every deflate block type decoded in a real browser and a teeth check).
- **Result on the real EP5 build** (965k rows, 7,662 offsets, all five views): **31 min 47 s -> 10 min 47 s (2.9x)**. Per view, old -> new: summary 8 min 8 s -> 87 s, statistical summary 8 min 48 s -> 3 min 48 s, box plot 14 min 12 s -> 10 min 19 s (now the single slowest view, so total time is about that); output size on disk 771 MB -> 105 MB. The box plot's remaining time has not been profiled yet (next candidate).
- Proof: old code == new code (serial) == new code (parallel), byte-for-byte, on all five views; compressed pages decode to identical data in a real browser (same constants hashed, same traces, no page errors).

---

## 2026-11-04

### Web app: edit job files, History / undo, and named job lists
- **Edit a job from the jobs table.** Tick a row and an **Edit** button appears in its Name cell. The modal edits the raw job JSON: **Review changes** validates it and shows a colour-coded diff (nothing written), **Confirm save** writes it. Every save first makes a timestamped `.bak` copy (newest 20 kept per job; same-second saves never overwrite each other), swaps the file in atomically, is blocked while the job is queued/running, and preserves the file's own line endings (the browser textarea normalises CRLF, which had made every line look changed). Pinned by `test_edit_job_file`.
- **History / undo.** Lists a job's earlier versions (newest first, lines-differ count); **Load this version** loads one into the editor and shows the diff; the restore goes through the normal save, so it is backed up and itself undoable. Pinned by `test_job_history_and_restore`.
- **Named job lists** (e.g. Production / NPI). A **List** dropdown filters the jobs table; Add checked to list… / Remove checked from list / Rename / Delete manage `job_lists.json` in the Data folder. Lists are a view only — job files, schedules, results and run order are untouched; a job can be in several lists; stale names prune automatically. **Select Filtered** respects the chosen list. Pinned by `test_job_lists`.
- **Fixes found while building it:** the Edit button now alerts (instead of failing silently) when the running server is older than the page — restart via `Start_web.bat` and Ctrl+F5; the Edit button moved into the Name cell so a narrow window can't push it off-screen.
- QA: `qa_webapp.py` 111/0; each new guard (running-job block, backup, same-second stepping, pruning, name validation, list pruning/validation/remove) was individually broken to prove its test fails.

### Run log: false "produced no output" warnings removed
- On the AmplitudeAccuracyClosedLoop AMC2 pod, 5 of 6 "analytic(s) produced no output" warnings were false: the 4 SummaryPlot (Type 90) analytics write `.txt` only by design, and one analytic's `:` in its OutputFile is written by PADB as `_` so the predicted name never matched. That mismatch also made `find_csvs` hand the analytic another analytic's CSV (listed twice). Fixed: a Type 90 `.txt` counts as output, Windows-safe name variants are matched, and the fuzzy fallback can no longer take a CSV another analytic matched exactly. The one real gap (Relative Amplitude Sweep -- no data returned) is still reported. `qa_regressions` 711 -> 721, each guard proven to fail when removed.

### Test-point reduction: gross-failing runs no longer block every recommendation
- On the Harmonics & Sub-harmonics all-runs study (253 unit-runs) the report recommended **dropping 0 of 373 frequencies**: spec fails are always protected, and 3 runs (one failing at 287 of 373 frequencies) put a fail at every frequency. The reducer now excludes unit-runs that fail broadly (>= `--gross-min` 25 distinct frequencies and >= `--gross-frac` 10% of what that run measured), lists them in the report as DUT/fixture/station problems to repair, and keeps protecting genuine frequency-specific fails. If more than `--gross-max-share` (25%) of unit-runs are gross it is treated as systemic: nothing is excluded and the report warns. `--gross-frac 0` restores the old behaviour. Result on that dataset: **80 of 373 (21.4%) droppable**. `qa_testpoint_reduce` 11 -> 20, each new guard proven to fail when broken.
- Note: a `*_run_job.json` only extracts; the reduction report is the separate **Test-point Reduce** action.

### Docs
- `/padb-tools` (Claude Code command) is repo-scoped: start Claude Code with this repo as the working directory, or copy `.claude/commands/padb-tools.md` to `~/.claude/commands/` to use it anywhere (`GETTING_STARTED.md`).

## 2026-09-29

### Conflicting/unstable spec limits → hide the lines, point to the datasheet
- **Scatter now suppresses the spec lines and shows a datasheet note when the limits are inconsistent.** On a two-site compare (or any dataset) where the flat-line branch would draw more than 3 distinct binned limit values — which happens because SR is where specs get *set* (they change over time and only stabilize by the time units ship to MY/AMC), or because two sites carry different limits — the plot used to splatter a stack of full-width flat lines (e.g. 30/23/22.44/21.44/19.3/18.48/17.3) that implied limits applying across all frequencies. It now hides them and shows an amber note: *"Multiple differing spec limits detected … refer to the datasheet for the real limit (flat, stepped by start/stop frequency, or interpolated — whichever the test defines)."* A **"show the raw spec lines anyway"** link reveals them on demand. Pass/fail is unchanged (still per-point against each measurement's own limit). Genuine frequency masks are unaffected — they still draw a proper stepped/interpolated spec trace with no note. Scatter-only for now. Pinned by `test_spec_conflict_note`.

### Fix: "Segment by: Named bands" no longer strips units/conditions
- **Stepping "Segment by: Named bands" now scopes the frequency window only** — it no longer narrows the condition/serial filters. A named band is an arbitrary *frequency* chunk, but the stepper had been reusing the spec/limit path that narrows every condition dim to "values present in the band," which silently dropped units — on the single-site Harmonics scatter/boxplot a carrier-band step cut Serial Number from 11 to 4 (units with no spur at those exact frequencies), reported as "named bands removes data using the filters." Now a named-band step leaves all conditions/serials checked and only scopes frequency (plot and table stay coupled). **Spec / Limit / Uncertainty segmentation is unchanged** — those still narrow conditions, since a spec segment genuinely is a set of conditions. Guarded per view (`if(_segKey!=='bands')`); pinned by `test_named_band_step_is_frequency_only`. **Rebuild plots to pick this up.**

### Fix: "Segment by: Limit" step went empty on single-site line-limit jobs
- **Stepping the segment stepper (Prev/Next) could empty the plot** on a standard (non-compare) interactive job whose spec is a frequency mask (`Limits_YLimit=Line`) — reported on the freshly-run single-site AMC2 MaxPower job. Root cause: the programmatic freq-range setters wrote the freq filter with `toFixed(3)` (round-to-**nearest**), which rounds *inward* at a segment boundary that lands mid-thousandth (e.g. `44.53125 → 44.531`). Segment boundaries **are** data frequencies, so a sparse segment whose only rows sit exactly on its edges had *both* edges clipped → zero points, no error. Fix: round **outward** (floor the low bound, ceil the high bound) in every `setFreqBand` (5 copies) and every `segTab` that writes the freq filter directly (stat_summary / env_coverage / distribution / boxplot) — the same outward-rounding rule Reset/`freqStep` already used. Verified live across scatter / stat_summary / boxplot: all 8 segments now populate (segment 2 went 0 → 22/57/… points). **Rebuild existing plots to pick this up.**
- Only recently-QA'd *compare* jobs were exercised, whose denser segments keep interior points even when an edge clips — so this single-site regression slipped through. New source-contract pin `test_setfreqband_rounds_outward` (with teeth) guards all copies.

## 2026-09-28

### Global Filter & Locked filters — one clearer "cross-view state" bar
- **The Global Filter is now shown and managed on the floating bar, on every view** (not just the Boxplot). Whenever a GF exists, the bar gains a **🌐 Global Filter** section with the point count, a **readable grouped summary** (by unit + port, e.g. "US65080432 (RF1) (all), … +15 more" — never raw keys), an **Inspect** toggle, and **Clear GF**. Setting exclusions still happens on the Boxplot; viewing/inspecting/clearing now works from anywhere.
- **One Lock button.** "Lock these filters" now also pins your exact serial+port population when you've narrowed serial/port (the old "Sync this selection to all views" / "Clear sync" buttons are gone — folded in). **Clearing a lock removes only the population the lock added** to the Global Filter; any other GF entries you set separately survive.
- **"Keep only this population" → "Keep only what's shown."** It now stores the **true inverse of the current visible slice** — everything not on screen (conditions, frequency, serial/port, temperature, pass/fail) is excluded, so every view shows exactly what you're looking at. (Stepped into a named band, it keeps that band's shown slice and excludes the rest — clear the frequency range first if you want a population across all bands.)
- **GF buttons grouped by intent** — **Keep only ▸** (green; saves the inverse so those stay) vs **Exclude ▸** (amber; adds the shown slice to the exclusion list so those disappear). "Add locked filters to GF" is renamed **"Exclude the locked slice."**
- **"Set filter as GF" now respects the pass/fail filter** — under "Failing only" it captures only the failing measurements (it used to sweep in passing points too).
- **"applied N" no longer goes stale.** After Reset / "Show all data" the lock bar shows **"not applied"** (the lock is still saved, just not the current filter) instead of a stale "applied N"; it returns to "applied N" when you re-Apply.
- **Warning when a lock mixes two pass/fail notions.** `Test Event Status` is the PADB *verdict* (P/F); "Passing/Failing only" is the *spec* filter. Locking both together (e.g. Status=P + Failing) scopes the spec filter to only that verdict — so "Exclude the locked slice" would leave the other verdict's fails behind. Saving such a lock now asks you to confirm.
- **GF Inspect: heads-up when your filters hide the flagged data.** In Inspect mode, if a condition you've deselected (e.g. `Test Event Status=F`) hides all the GF-flagged points, the banner now says so and points you at Reset — instead of Inspect looking empty for no reason.

### Stat Summary — failing points get a red outline
- On Stat Summary the mean marker's fill colour is *normality* (green = Normal), which reads as "pass." A frequency actually fails on its tolerance-interval bound, not its mean, so a green dot could sit within spec on a "Failing only" plot and look out of place. Failing frequencies' mean markers now carry a **red outline** (with a "fails spec (TI)" swatch in the legend) so they're unmistakable regardless of the normality colour.

### Correctness
- **Scatter vs Boxplot per-band fail counts now match.** Scatter's frequency axis was float32-encoded (for size) while the boxplot's stays full precision — at a named-band edge that tiny difference put an edge point in a different band, so the two views disagreed by a point or two. Frequency is now kept full-precision (only the Y value is compressed), so both views bin every edge point identically.
- **Cross-site "Coverage gap" banner drops meaningless dimensions.** Test Run Datetime (unique per run — can never match between sites) and Run NN (unique tags) are no longer listed as "gaps"; real controlled dims (Temperature/Mode/Port/…) still are.

### Segment-by
- **No compounding across steps.** Stepping the "Segment by" bands no longer lets one band's auto-narrowing leak into the next (a fail that lived in a later band could vanish while stepping). Each step now narrows from the selection you had when you began stepping.
- **Hint to Named bands.** When the chosen "Segment by" basis has no bands along the current x-axis (so Prev/Next are hidden) but Named bands would step, an inline hint points you there.

## 2026-09-27

### View pages remember your display state
- Each view now remembers its **pass/fail, table view (grouped vs per-point), and table-open** state — not just its filters — so flipping between views and back keeps the same setup. Reset / "Show all data" clears it back to a clean slate.

### Data filter with no spec — informed, not removed
- When a dataset carries no spec/limit at all (and no manual Spec override), the All/Passing/Failing control would be inert ("Failing only" shows nothing). Rather than disable it, an inline note now points you at the **Spec override** (type your own limit to make the filter meaningful); the note clears once a limit exists.

---

## 2026-09-24

### Named-band segment step (all 6 swept-x views + the parquet viewer)
- **"Segment by: Named bands."** A band file partitions the swept x-axis into named chunks (DAC/Low/Mid/High — or anything; not limited to RF frequency). Every swept-x view's "Segment by" dropdown gains a **Named bands** option that steps the frequency window through your bands; the band name shows in the label and stepping narrows the plot **and** the tables to that band (same coupling as the spec stepper). The parquet viewer's segment stepper gets the same, plus it now shows the current **spec/TLL value** while stepping spec stairs, and its band-view load cap **floors at the largest named band** (a single band always opens; heavy bands are flagged "may load slowly").
- **`padb_make_bands.py`** helper to set one up: auto-generate an editable starter from the data (`py padb_make_bands.py <folder>`), type your own (`--band "Name:lo:hi" --unit MHz`), or the standard `--sg6311a` preset. Edit the file any time to rename/re-range.
- **Auto-generation is opt-in** (`auto_bands` job key / `--auto-bands`) — an existing band file is always loaded, but we don't drop one into every results folder by default.

### Fix — locked filters: frequency range now applies on views with plain number inputs
- The locked **frequency range** silently failed to apply on the **boxplot** (its freq inputs have no min/max, so the old `Math.max(parseFloat(min), lo)` clamp produced `NaN` and the range was dropped — dims applied, freq didn't). Now NaN-guarded across all views. `qa_crossview` INV-LOCK now also locks a freq sub-range and asserts it propagates.

---

## 2026-09-23

### Cross-view locked filters (all 8 views)
- **New "🔒 Locked filters" panel on every view.** Set condition dims + frequency range + pass/fail on any view, click **Lock these filters**, and every other view auto-applies the same set on load. Floating panel offers Lock / Apply / Update-from-this-view / Clear / Export / Import (Export/Import JSON is the fallback for pages opened straight off the network share, where browser storage isn't shared). View-agnostic object matched by dimension label; best-effort (a locked filter absent from a view is ignored, never blanks it); only *narrowed* dims are locked. Serial Number/Port are routed to each view's dedicated serial/port controls (serial matched on base form, so a base-serial lock still selects port-qualified `MY123_RF1`). Works from any view to any view. Covers scatter, boxplot, stat_summary, summary, distribution, env_coverage, histogram, and reference.

### Per-point fail correctness + cross-view consistency (found on EP6 phase-noise + Harmonics compares)
- **summary / stat_summary / scatter no longer fabricate a limit** for points that carry none. They scored limit-less, PADB-passed points (Test Event Status: P, null Upper Limit) against a per-frequency aggregate or the page-global spec, inflating summary's "Failing only" count (e.g. 145/234 vs scatter's ~85) and marking passed points FAIL. Each point is now scored against its **own** limit only; a limit-less point is unscored ("—"), matching scatter's table. summary's Passing/Failing is now per-**point** (was a per-frequency tolerance-interval test). Raw-point views (scatter/boxplot/reference) fall back to PADB's recorded Test Event Status for limit-less points.
- With identical filters, **all views now report the same fail count** (verified across every harmonic on a Harmonics compare).

### summary "Group by: Serial Number"
- Added a per-DUT group-by to the Summary view (parity with boxplot).

---

## 2026-09-21

### Boxplot filter fixes (found via a relational filters→plots→tables→stat-tables audit)
- **Compare boxplot no longer zeroes the site missing a grouping key.** On a cross-site compare, one site's rows can carry a grouping key the other's don't (e.g. AMC has `Test Event Status`, SR doesn't). Deselecting *any* filter used to uncheck every row of the site lacking that key (its condition strings didn't match the absent dimension's regex), blanking half the plot with no recovery. An absent dimension now imposes **no constraint** (a no-op for single-site pods, where every condition carries every key).
- **Boxplot "Data filter" cleaned up (Q1).** The confusing `All / Passing only / Upper limit / Lower limit` radio group — which jammed a pass/fail axis together with a raw-sample *trim* and had no "Failing" — is now a clean pass/fail axis **All / Passing only / Failing only** (Failing is the exact complement of Passing), plus a **separate, always-visible "Trim raw samples: above/below"** control that's independent of the pass/fail filter and the Spec override (so it can combine with either). The Spec override is unchanged (it sets the pass/fail *threshold*; the trim removes *data*). Applied consistently to the plot, the grouped + non-grouped Statistics Table, the per-point table, and the outlier collector.
- **Boxplot Workflow button** now uses the shared helper (flipping ▸/▾ caret) like the other four views.

### Boxplot pass/fail + spec lines were wrong for per-condition specs (found on a Harmonics compare)
- **Table #fail is now per-point vs each point's OWN Upper/Lower Limit**, not a single flat `HI_SPEC`/`LO_SPEC`. The flat spec was the first data row's limit, so on a pod where conditions/frequencies carry different specs (Harmonic 2's staircase ≠ 0.5's) the table counted **phantom failures the plot's own spec line never supported** — measured 7,501 flat-spec "fails" vs 974 real (own-limit) for Harmonic 2. Applied to the grouped table, per-point table, and outlier collector (`_boxPtLim`/`_boxFailCountDetail`/`_boxFailCellDetail`); the flat `_boxFailCell`/`_boxFailCount`/`_boxPfLimits` are gone.
- **One spec line per condition.** The plot min-pooled every selected condition's spec into a single (tightest) line, so 2 and 0.5 collapsed to one misleading line. Now draws one line per **distinct** per-condition Limit staircase (`_limitMapsByGroup`), collapsing to a single red line when identical (the common case).
- **Autoscale Y re-fits when the plotted set changes.** An Autoscale-Y pin used to persist across filter changes, clipping a newly-added condition off-screen while the table still listed it (looked like "plot shows only 0.5, table shows both"). It now re-fits Y when the condition/serial/temp/freq selection changes; a manual drag-zoom still persists, and a configured `Y_LIM` still wins.
- **Pass/fail VERDICT falls back to PADB's `Test Event Status` (P/F) when there is no numeric limit.** On a no-limit pod (e.g. phase-noise offset compares) the "Failing only" filter showed **nothing** on the plot and the per-point table had **no Status column** — the recorded P/F verdict was ignored. A single `_boxVerdict(condition, point, filter)` rule now drives the Passing/Failing filter **and** the table Status / #fail: numeric limit wins (each point's own, override respected), else the recorded `Test Event Status` verdict (auto-detected as `BOX_STATUS_FIELD`). So filters → plot → table stay consistent whether pass/fail comes from a limit or PADB's verdict. Verified on the ECF compare: Failing-only isolates the 41 F points on the plot, and both tables show 41 (was 0).

### Parquet sidecar gate is size-based, not compare-blanket
- `_maybe_export_parquet` used to auto-write a parquet sidecar for **every** compare job regardless of size — so a tiny switching-speed compare (e.g. `QA_Switching`, 1.5 MB) got a needless sidecar *and* a misleading "Large-dataset viewer" section on its index. The parquet viewer only helps when the self-contained HTML is too big to open, so it now auto-exports **only for large jobs** (the same size/row thresholds as `binary_encode`) — for compares and non-compares alike. `export_parquet: true` still forces one on a small job. A rebuild that no longer exports also **removes any stale sidecar** (so a plot-job rerun self-cleans an existing small compare's `.parquet` + its index viewer section — no manual delete needed).

### QA
- New `qa_regressions` pins (`test_compare_boxplot_absent_dim_and_caret`, `test_box_data_filter_passfail_and_trim`, `test_box_fail_per_point_limit_and_spec_lines`, parquet size-gate + verdict/cross-sweep guards, 321 → 352) and `qa_filters` behavioral checks — the Q1 data-filter set (`box-failing-isolates-nonempty-subset`, `box-passing-strict-subset`, `box-passing-failing-partition`, `box-trim-combines-with-failing`) plus `box-perpoint-fail-equals-own-limit` / `box-perpoint-fail-not-flat-spec` (would catch a flat-spec regression: on the Harmonics compare the table must show 974, not 7,501). Proven with teeth (a deterministic synthetic shows 6 own-limit fails vs 30 flat-spec, and the table shows 6). Ran the browser-tier relational gate across single-site, spur-staircase, and cross-site-compare galleries.

### Docs
- **Interactive_Plots_User_Guide.md**: added **Reference Statistics** and **Histogram** (switching-speed) view sections, corrected the Room-temperature view rules (Summary + Stat Summary are Room-only defaults now), a **"What's new in comparison mode"** subsection, and updated the Boxplot Data-filter description. Compare cheatsheet "Known gaps" refreshed (Site Population Check is on all six views).

---

## 2026-09-04 → 2026-09-18

### Large-dataset viewer (parquet)
- **Compact `.parquet` sidecar + `padb_viewer.py`** — self-contained HTML can't open a giant analytic (wide phase-noise sweeps, big compares, millions of points). `padb_v2.py` now writes a zstd parquet sidecar (80–140× smaller than the CSV) for compare jobs and large inputs; `padb_viewer.py` is a local Flask server that serves only the decimated/filtered slice being viewed, with a frequency filter, Site/temperature checkboxes, and band-view buttons that render any full view for the current range. `build_viewer.py` freezes it to `PADB_Viewer.exe`. The results `index.html` links it ("Open in viewer" / "Open folder" / `Open_in_viewer.bat`) and flags it **optional** — the HTML plots carry the same analysis; only reach for the viewer if they're too big to open.
- **Viewer main-plot Autoscale / Reset axes / drag-zoom now drive the frequency filter** (and re-query), so zooming refines detail and the band-view buttons stay scoped.

### Interactive views
- **Busy/loading overlay on every view** — a "Loading plot data…" spinner painted before the data parses, cleared once the plot renders, so a large page never looks dead.
- **Busy spinner on table/panel render + Site-check row cap** — the Site Population Check panels paint a "Computing Site Population Check…" spinner and build on the next frame (so a heavy panel shows progress instead of freezing), and their per-point detail tables cap at 5,000 rows (the summary counts and CSV export still cover all rows). This covers the previously-unguarded slow tables (e.g. a 62-dimension switching-speed compare with ~12k+ non-primary rows). **Extended to the main Statistics/Results tables** (stat_summary, boxplot, summary, env_coverage): the **"Refresh table"** button — the explicit large-rebuild above the ~150-condition size-gate — now paints a "Building Statistics Table…" spinner and builds on the next frame too. The fast auto-refresh path (below the gate) stays instant/unwrapped, and the compute functions remain synchronous (so programmatic/QA callers are unaffected — only the user-initiated open/refresh defers). **Histogram:** it has no size-gated "Refresh table" button, so its own **Statistics panel** toggle (`toggleStats`) is spinner-wrapped instead — this is what gives the switching-speed compares (histogram-only) a table-render spinner. Shared helpers `PADB_spinnerHtml` / `PADB_deferRender` live in the common JS prelude. **Net scope:** busy indicators now cover the plot load (all views), the Site Population Check panels (all views that have one), the four Statistics/Results "Refresh table" builds, and the histogram Statistics panel.
- **Scatter "Worst first" sort** now ranks by largest **error relative to spec** (furthest past the limit), falling back to raw value only when the data has no spec.
- **Compare-to selector (fence / Spec-Limit / both)** rolled out to all six views; **Site Population Check** selectable comparison basis; extended to env_coverage/distribution/histogram (with edit-reimport for the histogram, which has no Global Filter).
- **Auto-filter bad DUTs + ⚙ Workflow & Recommendations** matured: per-site risk + reference/onboarding/both **site scope** on compares, **"Remove auto-filter"** (subtract only the auto increment), a **subpopulation / dual-distribution advisory** rolled out across the population views, and a "why no Apply button" explainer.
- **Statistics/Results tables**: Grouped/Per-point toggle + **"# fail / n"** column (boxplot → stat_summary → summary); per-point Pass/Fail vs the effective limit; scatter data-rows table shows Spec/Limit bounds + per-point Pass/Fail. Per-point Limit/Status now honour a **manually-typed Spec** on data with no CSV spec.
- **Scatter**: per-DUT line view + Draw modes + Smooth (phase-noise style); Room temperature is filterable.

### Test-point reduction & sentinel
- **`padb_testpoint_reduce.py`** (report-only test-plan trimmer, adaptive-noise ε + data-driven ceiling) and **`padb_sentinel.py`** (run-to-run audit-diff) added, with a reduction-study extraction primitive (all-runs + run-datetime grouping), a run-aware per-DUT scatter view, and a worked example. Wired into the webapp compare menu (reduce on merged data).

### Comprehensive PDF report
- **`padb_pdf_report.py`** — opt-in multi-view PDF (cover + every view with its table) built at build time via Playwright/Chromium (headless Edge is dead on this box); filter-aware and on-demand variants; site-scope option; index links + publishes the PDF.

### Hardening & QA
- **Shared JS prelude** (`_COMMON_JS`) — single `PADB_isFail` / `PADB_specClass` / `PADB_fence` for every view; Site spec-classifiers and fence helpers collapsed onto the shared rules. Plotly 3.6 axis-title fix across all views. New behavioral gate `qa_jsrules.py`, cross-view `qa_gf_crossview`, and a feature registry that fails until a feature is in all views.
- **Webapp**: control-context clarity pass (optional/destructive tagging); compare panel creates-the-job-only; single-instance guard; `Start_web.bat` frees port 5000.

---

## Week of 2026-08-27 → (in progress)

- **Histogram auto-filter undo wording fixed (2026-09-15)** — the auto-filter's undo button on the **Histogram** now reads **"Clear auto-exclusion"** (with a view-local tooltip), not the misleading **"Clear global filter"** — the histogram has no shared Global Filter; its auto-filter affects only that page. The remaining shared prose (the auto-filter control tooltip, the Workflow & Recommendations steps, and the closing "reversible via …" note) is now ctx-driven, so the histogram consistently says "auto-exclusion" everywhere while Boxplot/Stat Summary/Summary/Env Coverage keep "Global Filter". Documented in the Interactive Plots User Guide; pinned by `qa_regressions.py` (`undo-label:` checks).
- **New `histogram` view** — a CSV-driven interactive value-distribution histogram for tests with no swept x-axis (switching speed and similar "one number per event" measurements). Auto-detected bins (Freedman–Diaconis), overlaid conditions (e.g. per port), a spec-limit line, and a stats panel (n / mean / median / p95 / p99 / max / % out-of-spec). `padb_make_v2_job.py` now routes any Type=80 analytic whose x-axis is text / single-value (`[T]` or `(1 x 1)`) to `views:["histogram"]` automatically; the Type=80 analytic's CSV remains the raw-value source.
- **Scatter: "Table" view** — a toggle that shows the currently-plotted rows (after all filters) as a table below the plot, same columns as the CSV export, capped at 2000 rows with a "use Save CSV for all" note above that.
- **Scrollbar gutter reserved** on every interactive view — stops the vertical scrollbar toggling on/off (and briefly hiding the buttons below the plot) when a filter/panel/re-render changes page height.
- **Fixed:** the scatter's GF "Inspect" toggle wrote the *unscoped* GF-mode key (regression from the per-analytic GF scoping) — it now uses the scoped key.

## Week of 2026-08-27 → 2026-09-03 (53 commits)

### Interactive plots — features
- **Multi-select "Group by"** in every view — pool/split by any *combination* of parameters (boxplot first, then scatter, stat_summary, summary, env_coverage). None selected = Condition; a subset pools across the parameters you didn't pick; legend/tables reflect the combination.
- **Distribution: condition-dimension filters** (AlcState, Mode, …) added for filter-parity with the other views — previously it only filtered by Spur Type / serial / port / temperature / frequency. Filtering recomputes the KDE curves live in both Absolute and ΔTemp modes.
- **"Autoscale Y"** button on all six views (rescales Y to the visible data without disturbing the frequency zoom).
- **Segment-by** tab-through now narrows the condition filters to each band, not just the frequency window.
- **"Copy PADB Filter"** rewritten to real PADB syntax and expanded into a **three-mode dropdown** (Plot view / Global Filter only / Plot + GF), with "Global Filter only" reproducing the GF's full captured scope.
- **GF Inspect-mode** now resets to Exclude on every page load, with a prominent "⚠ INSPECT MODE" banner while active.
- **Boxplot normality on grouped conditions** — grouping by the natural test conditions now shows the real Shapiro / NP-TI (matching "Group by: Condition"), including when Temperature is one of the group dimensions; honest, specific fallback wording otherwise.
- **`binary_encode`** extended to boxplot per-point data; Help-panel guidance on why pooled statistics can look off.

### Interactive plots — fixes
- Segment-by **collapsed the list and hid the Prev/Next bar** mid-tab on single-point bands — fixed in all six views.
- **Global Filter was browser-global** — a GF built on one dataset leaked onto unrelated pages (and kept its checkbox highlighted); now **scoped per-analytic**.
- Stat Summary Segment-by didn't move the frequency axis; boxplot Group-by/segment-tab desyncs; boxplot initial render ignored restored filter state; stats-table default row could disagree with the plot on a duplicate.
- env_coverage Stats Table now syncs to a plot drag-zoom; stat_summary spec-direction display fix.
- Serial/Port panels were missing on `binary_encode` (compare) plots; boxplot GF Exclude semantics + serial/port + multichannel fixes.
- Site Population Check: excluded-all-data with no serial panel, fence-cell column bleed, quality-hint bugs, benign-direction verdicts (+ Site dup-pts column).
- Boxplot "Passing only" now warns when there's nothing to compare against.

### Web app
- **Publishing is opt-in per run** (default off / `--no-publish`); job files never rewritten. **"Module" → "Folder name"**, plus a **"Share path (override)"** field and a **settable default share root** (persists to `padb_config.json`), with how-to tooltips.
- **"View log"** link on job failure; **"Select Filtered"** button; the "Dry run" checkbox removed.
- Auto-resume no longer **loops forever** on a permanently-failing sibling; restart-mid-loop hardening; re-subscribe to active jobs on browser refresh.
- **"Clean up orphaned PADB-R"** now elevates on demand (one UAC prompt) and also finds parentless `R-Host.exe`.
- Compare panel: busy-state feedback + queue position; CSV name filter + Refresh; x-axis unit inheritance/cross-check; `PADB-Compare` default publish root; `padb_csv_check.py` publish pre-flight gate.
- **`Start_web.bat`** convenience launcher.

### Pipeline & tooling
- `padb_v2.py`: clear **"no matching test data"** failure logging (`build_failures.log`) instead of a raw traceback; **oversized-view size guard** (warns when a self-contained HTML is too big for a browser).
- `results_padb` is now the only trusted output source (no stale R-Plots fallback), plus collection false-negative fixes.
- `_build_help_pdfs.py`; documentation brought fully current.

---

## Bug Fixes

- **Aug 17** — PADB-R.exe exclusivity guard was blocking new job launches behind an idle GUI window that wasn't actually running a batch job
- **Aug 18** — `toggleLogX()` discarded an active zoom/pan on every toggle
- **Aug 18** — Webapp's "delete job" could delete a job's `.sao` file along with its results data
- **Aug 18** — `summary`'s Upper/Lower-limit data filter excluded nothing, regardless of the threshold entered
- **Aug 18** — `stat_summary`'s Statistics Table didn't respect active filters the first time it was opened
- **Aug 18** — Default frequency-range boxes silently clipped the true min/max data point (rounding-direction bug)
- **Aug 18** — Boxplot's "Clear everything" was wiping the Global Filter (should only clear local filters); boxplot frequency labels could collide for close-in frequencies
- **Aug 18** — Boxplot Statistics Table ignored "Group by: Serial Number/Port"
- **Aug 18** — `summary`'s Spec Hi/Lo and Margin columns ignored the manual TLL override
- **Aug 19** — `stat_summary`'s Spec Lo override had inverted priority vs. Spec Hi (never actually applied)
- **Aug 19** — Bundled fix set: `summary` temperature-filter blindness in the data filter, frequency-arrow-key stepping precision bug, boxplot's "Copy PADB Filter" bug, webapp log buffering (stdout not flushing live)
- **Aug 20** — Stale leftover files in R-Plots could clobber a fresh `-dir` extraction's real output
- **Aug 20** — Boxplot: deselecting every condition checkbox showed *everything* instead of nothing
- **Aug 21** — Boxplot Group-by condition filter had no effect once grouped by Serial/Port
- **Aug 21** — Boxplot Statistics Table's frequency column header ignored the `x_label` override
- **Aug 21** — Reset left the Global Filter stuck in "Inspect" mode across unrelated pages; boxplot's "Excl outliers" didn't affect "Show points" or the table
- **Aug 21** — Webapp: "Run Selected" could queue the same job twice; Compare panel's CSV dropdown went stale
- **Aug 21** — Webapp restart was killing an in-progress job's own subprocess (stdout pipe closed on parent death — lost a real ~13-minute build)
- **Aug 25** — Boxplot Dup-runs count was missing Port from its duplicate-detection identity (a DUT's RF1/RF2 rows were miscounted as duplicates of each other)
- **Aug 25** — `stat_summary`'s Statistics Table pushed the plot out of view when opened (DOM ordering bug, unique to that view)
- **Aug 31** — Boxplot Group-by/segment-tab filter desyncs; segment-tab left a stale Autoscale-Y pin
- **Aug 31** — Site Population Check excluded all data on a page with no serial filter panel
- **Sep 1** — Cross-site compare boxplot was missing its Serial/Port filter panels entirely (data stored as `vals_detail_bin` under binary_encode, so the panels computed from the wrong source)
- **Sep 1** — "Copy PADB Filter" Global-Filter output was a bare `'Serial Number' NOT IN {...}`; now reproduces the GF's full captured scope (harmonic/condition, frequency range, serials)
- **Sep 1** — Global Filter could get stuck in "Inspect" mode across unrelated result pages (browser-global setting); now resets to Exclude on every page load, with an amber "⚠ INSPECT MODE" banner while active
- **Sep 2** — Stat Summary: stepping "Segment by" updated the spec lines but left the frequency axis where it was — the axis stayed pinned to an earlier segment/zoom range instead of moving to the stepped band
- **Sep 8** — Stat Summary: on first load with a restored/narrow frequency window, the spec lines were drawn for *all* frequencies (both spec bands), only correcting after a Segment-by (or any other) click. The init rendered via a bespoke 2-arg `buildLayout()` that skipped the frequency window (and used the unfiltered condition list), so the spec-line set didn't match the windowed data until the first real `update()`. Init now renders through `update()` like every later frame, so the first paint is consistent (verified: restored freq_hi=400 shows only the in-window band's spec on first render, no click needed).
- **Sep 8** — Distribution: setting Freq min = Freq max (a single frequency) showed no data. `getFreqRange()` read the range *slider* (whose `.value` the browser snaps to its coarse `step`), so a single-frequency window landed between real frequency samples and matched zero points. Now reads the exact text box (same fix as scatter/summary). Also: when a narrow selection leaves too few points for a KDE (< 4), the raw value(s) now render as a rug ("N pts, n<4 for KDE") instead of a silent blank, so a single test point is still visible.
- **Sep 3** — Boxplot & Stat Summary fabricated fake "serial numbers" from the condition string on pods with no serial data (Close-In: Group is AlcState/Mode/SpurType only, no serial anywhere) — the Serial filter and "Group by: Serial Number" listed *conditions*, not serials, and grouping by them did nothing. With no genuine serial, both views now correctly hide the Serial controls (matching the scatter, which already showed no serials) instead of a broken condition-as-serial panel. (The data-side fix is to add Serial Number to the pod's Group By so real per-DUT serials are extracted.)

## Feature Improvements

- **Aug 17** — Warning when R-Plots collection looks stale; "Hide spec lines" checkbox added to scatter, then extended to distribution/stat_summary/boxplot/summary; "Delete job" added to the webapp
- **Aug 18** — Jump-nav TOC for PADB Simple mode's gallery; V2 `index.html` now groups links by analytic instead of one flat list; zoom/pan persistence across filter changes (scatter first, then all 6 interactive views); Statistics/Results Tables auto-refresh below a condition-count threshold with a highlighted manual "Refresh" above it; Reset button added to stat_summary/env_coverage; drag-zoom now syncs to the frequency slider/table in boxplot and stat_summary; Outliers column split into Max +Δ/Max −Δ by sign; lower-side TLL manual override added to boxplot and summary
- **Aug 19** — Spec/TLL overrides now visually flagged (orange highlight) instead of silently substituted; **cross-site comparison feature** added (`compare_csv` job.json key, webapp Compare UI, full docs); boxplot Site Population Check scoped to the current frequency window
- **Aug 20** — Site Population Check added to stat_summary, then extended (with the coverage-gap banner) to summary; `padb_make_v2_job.py` now auto-detects non-frequency x-axis pods
- **Aug 21** — Adaptive Group-by defaults for boxplot (Serial Number above 150 conditions); Segment-by control now hides itself when there's nothing to segment; "Legacy" mode retired from the webapp; orphaned PADB-R.exe cleanup button added
- **Aug 24** — Boxplot "Dup runs"/"Genuinely repeated freqs" columns (Statistics Table + Site Population Check); Site Population Check CSV export (boxplot, then stat_summary/summary); pod `Filter_Expression` now shown in the Help panel; opt-in "Collapse dup runs" toggle for boxplot; "SR dup pts" changed from a summed total to a per-DUT breakdown list
- **Aug 25** — Noise-sensitivity disclaimers added to env_coverage, distribution, stat_summary, and summary
- **Aug 31** — "Autoscale Y" button added to all six interactive views (rescales the Y axis without disturbing the frequency zoom); Segment-by tab-through now narrows the condition filters to match each segment, not just the frequency window; `Start_web.bat` convenience launcher for the web app; `_build_help_pdfs.py` to regenerate the local PDF copies of the help docs; fast-path "Hide spec lines" toggle
- **Sep 1** — "Copy PADB Filter" rewritten to match real PADB filter syntax and expanded into a three-mode dropdown (Plot view / Global Filter only / Plot + GF); Compare webapp panel gained a CSV name filter + Refresh, wider dropdowns/Description, and x-axis unit inheritance; `padb_csv_check.py` gained a `publish_to` pre-flight gate; compare jobs default-publish to a `PADB-Compare` share tree
- **Sep 2** — Webapp: "View log" link on job failure (full console written to `<results_dir>/webapp_console.log`); "Select Filtered" button (checks every job matching the current Mode/Kind/Name filters); the "Dry run" checkbox was removed from the UI (CLI `--dry-run` unchanged)
- **Sep 2** — Webapp: publishing is now opt-in per run — a "Publish to share after run" checkbox (default off, passes `--no-publish`); job files are never rewritten. `padb_v2.py` gained a `--no-publish` flag to match `padb_run.py`
- **Sep 2** — Webapp: "Clean up orphaned PADB-R" now elevates on demand (one UAC prompt) to kill orphaned `R-Host.exe` a non-elevated `taskkill` couldn't touch — no need to run the whole server as administrator
- **Sep 2** — Generate Job: renamed "Module" → "Folder name" (the subfolder under the fixed share root), plus a new "Share path (override)" field for an exact `publish_to` off the standard tree (`--publish-to` on both job generators)
- **Sep 2** — Generate Job: settable **Default share root** (persists to `padb_config.json`), so other products/users point the tool at their own share tree without editing code
- **Sep 3** — Webapp: an auto-resume for an interrupted V2 plot chain no longer loops forever when one sibling can never build — a permanently-failing sibling is retried at most once, then recorded and left alone
- **Sep 3** — `padb_v2.py`: a plot build that has nothing to plot now logs a clear reason (e.g. "no matching test data — PADB placeholder export") to `build_failures.log` in the results dir (and the job console) instead of a raw traceback; compare jobs note any site whose CSV has no matching data, using the other site as the reference
- **Sep 3** — `padb_v2.py`: a generated view whose self-contained HTML is very large (≥80 MB) now logs a size warning with concrete options (`binary_encode`, `scatter_decimate`, narrower extraction, or that a per-frequency boxplot over thousands of offsets isn't a fitting view), so a page too big for a browser to render is diagnosed instead of looking like "no plot data" (real case: an 8.5M-row phase-noise DCFM boxplot → 517 MB HTML)
- **Sep 3** — Boxplot "Group by" is now a **multi-select**: pool the boxes by any *combination* of parameters (Ctrl/Cmd-click), not just one dimension or all. Selecting none = Condition (no pooling); a subset pools across the parameters you didn't pick. Legend and Statistics Table reflect the combination.
- **Sep 3** — Multi-select "Group by" extended to **scatter, stat_summary, summary, and env_coverage** — group/split by any combination of parameters in every view, for consistent cross-view comparison. (scatter splits traces by the combination, none selected = one combined trace.)
- **Sep 3** — Segment-by (all 6 views): tabbing **Next/Prev collapsed the segment list to 1 and hid the whole Prev/Next bar** when a segment narrowed the conditions to a single-point band (e.g. Harmonics with `[7.99, 7.99]` bands). The segment list is no longer rebuilt mid-tab, so you can step through every band; a genuine filter change still rebuilds it.
- **Sep 3** — Global Filter was **browser-global** — a GF built on one dataset showed up (and its checkbox stayed highlighted) on completely unrelated results pages. It's now **scoped per-analytic** (per scatter-CSV output): still shared across that analytic's own views, but never bleeds across different analytics or test types.
- **Sep 3** — Distribution view now has **condition-dimension filters** (AlcState, Mode, …) like the other views, so it can subset to the same data — previously it only filtered by Spur Type / serial / port / temperature / frequency. Each dim's per-point values ride along in the KDE recompute (both Absolute and ΔTemp modes).
- **Sep 3** — Boxplot Statistics Table: grouping by the natural test conditions (e.g. AlcState + HarmonicNumber + Mode) showed "— (no normality test)" even when each group is a single real condition. It now shows the real **Shapiro normality / NP-TI** (matching "Group by: Condition") when a group is one condition on Room data with no distorting filters — and still withholds it, honestly, when the population is genuinely pooled (multiple conditions, non-Room, or a serial/value/GF filter changes it).
- **Sep 3** — **Global Filter now honored in the Distribution view** — excluding a DUT/condition anywhere else in the tool now also drops it from the density curves (Distribution was the last interactive view without GF support). An "apply GF" checkbox + badge appear only when a GF is set; zero change when it's empty.
- **Sep 3** — **Terminology cleanup (TTL/TLL → Spec):** boxplot's and summary's manual override actually overrides the *Spec* limit (not a computed TTL/TLL band), so its label now reads "Spec↑/↓ override", the drawn line "Spec (manual)", and the side selector "Limit display". The genuinely-TLL controls (stat_summary's computed spec-minus-guard-band, its own "TLL override") are unchanged. Behavior identical — labels/tooltips only.
- **Sep 3** — **Stat Summary and Summary each got a scope subtitle** (Room-temperature-only vs. all-temperatures-combined) so the two similar-looking views' complementary purposes are clear at a glance.
- **Sep 3** — **Help panel now lists dimensions held constant** across the whole dataset (e.g. "Held constant: AlcState = TRUE, Mode = 0"), so a single-setting parameter reads as intentional rather than a missing filter/Group-by control.
- **Sep 3** — **`padb_v2.py` auto-enables `binary_encode` for large data** — when a CSV is ≥ 25 MB *or* ≥ 250,000 usable rows, the numeric arrays are float32-packed automatically to cut page size/latency (a plot-transparent optimization — it changes only file encoding, never a displayed value). An explicit `"binary_encode"` in job.json (true or false) always wins; thresholds are per-job overridable. Heads off oversized/unrenderable pages without a manual step.
- **Sep 3** — **Removed three dead code paths** (a duplicate static `de_summary`, the superseded `_isPoolableGroupBy`, and stale env_coverage save/load references to controls it never renders) — no behavior change; cleanup only.
- **Sep 8** — **New QA toolchain + `QA_GUIDE.md`.** Two standalone gates added: **`qa_csv_sweep.py`** runs the pre-flight check across every CSV under given roots (x_col-aware — applies each CSV's job `x_col` so a non-frequency-axis analytic like a `Rate (kHz)` Flatness sweep isn't false-FAILed; skips tool intermediates), and **`qa_view_sweep.py`** rebuilds real job.json files into a temp dir and headless-verifies each view actually renders (plus per-job needle checks), with a portable per-user `qa_view_sweep.json` coverage manifest (`--write-manifest` to start one). Both take `--root`/`--job` so other groups point them at their own data. Documented end-to-end in the new `QA_GUIDE.md` (the tiered approach, each tool, a full-pass recipe, the pod-variety coverage matrix, and how to read WARN vs FAIL).
