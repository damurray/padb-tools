# PADB Tools — Manual Browser QA Checklist

Use this checklist after running `qa_padb.py` to verify the generated HTML plots
visually and interactively. Open each file directly in a browser (no server needed).

Test data: `SG6311A_Harmonics_*` in the harmonics V2 results folder.

---

## Automated QA

Run first:
```
python "C:\apps\padb\tools\qa_padb.py" --keep
```
- [ ] All checks PASS
- [ ] `qa_output/` folder contains 6+ HTML files

---

## Pre-flight CSV check (before building V2 plots)

Run `padb_csv_check.py` against the extracted Scatter CSV **before** trusting any of the V2 HTML below — it catches orphaned numeric columns, inverted spec rows, and high-cardinality Group data as a fast command-line check rather than a confusing plot:
```
python "C:\apps\padb\tools\padb_csv_check.py" path\to\Scatter.csv
```
- [ ] Exit code 0 (no WARN/FAIL) — or any WARN/FAIL is understood and expected for this pod
- [ ] Grouping-item presence check confirms Serial/Port/Limit are found; if Segment-by Spec/Uncertainty will be used, confirms those are found too

---

## `SG6311A_Harmonics_stat_summary.html`

### On load
- [ ] Plot renders without console errors (F12 → Console)
- [ ] 4 coloured traces visible (2 harmonics × 2 ports)
- [ ] Mean line and TI band visible for each trace
- [ ] Spec limit lines (horizontal dashed) present if limits exist in data

### Condition filter
- [ ] Uncheck one harmonic → those traces disappear
- [ ] Re-check → traces return
- [ ] All/none buttons work

### Serial filter
- [ ] Serial filter panel visible with 9 DUT checkboxes
- [ ] Uncheck 1 DUT → statistics table n count drops by 1
- [ ] Mean shifts slightly (other DUT values)
- [ ] Re-check → stats return to original

### Show points
- [ ] "Show points" checkbox appears in filter bar
- [ ] Check it → small dots appear overlaid on each trace
- [ ] Dots match the trace colour
- [ ] Uncheck one serial → those dots turn grey (not hidden)
- [ ] Hover over a dot → shows serial number and value

### Frequency sliders
- [ ] Drag min slider right → X axis zooms in from left
- [ ] Arrow keys step the slider one frequency point at a time

### Log X
- [ ] Toggle log X → X axis switches to log scale
- [ ] Toggle back → returns to linear

### Statistics table
- [ ] Click "Statistics Table" → table expands below plot
- [ ] Per-condition, per-frequency: n, mean, σ, NP TI bounds, outliers visible
- [ ] Click "Statistics Table" again → table collapses

### CSV export
- [ ] Click CSV button → file downloads
- [ ] Open CSV — rows have condition, frequency, mean, TI bounds

### Group by
- [ ] "Group by" dropdown present — selecting a single dimension (e.g. SpurType) collapses the condition list to that dimension's distinct values
- [ ] Mean/std/quantiles/outliers update to the pooled values; Shapiro normality dot renders as "Non-normal" (red) for pooled groups, not a real Shapiro result
- [ ] Switching back to "Condition" (the default) restores the original per-condition traces

### Segment by
- [ ] "Segment by: Spec / Limit / Uncertainty" selector present **only if the dataset has a frequency-varying Spec/Limit/Uncertainty** (whole control is omitted for a flat-spec dataset, as of 2026-08-21); when present, Prev/Next buttons jump the frequency range to each contiguous spec band
- [ ] Selecting "Spec" or "Uncertainty" shows zero segments (Prev/Next hidden) if the pod's extraction didn't include those as grouping items — expected, not a bug
- [ ] Segment boundaries respect the current condition/serial/Group-by selection

### Help panel
- [ ] ⓘ Help button opens a panel explaining Filter/Group by/Segment by
- [ ] If the data has inverted Upper/Lower Limit or Spec rows, the panel names which filter-dimension value they're concentrated in

---

## `SG6311A_Harmonics_boxplot.html`

### On load
- [ ] Box plots render for each harmonic × port condition
- [ ] Each temperature shown in a different colour/group
- [ ] Outlier points visible where applicable

### Temperature filter
- [ ] Uncheck a temperature → those boxes disappear
- [ ] Re-check → boxes return

### Condition filter (outer)
- [ ] Longform condition list visible (all 14 conditions as checkboxes)
- [ ] Uncheck one condition → that column of boxes disappears
- [ ] Uncheck **every** condition → plot shows nothing (not everything) — regression check for a real bug fixed 2026-08-20 where deselecting all conditions silently fell back to showing all of them

### Group by
- [ ] "Group by" selector present (Condition / Temperature Step / Port / Serial Number / any condition dimension) — defaults to Condition unless the pod has 150+ raw conditions, in which case it defaults to Serial Number
- [ ] With Group by set to **Serial Number** or **Port**, the condition-dimension checkboxes above still work — unchecking a condition narrows the plot and Statistics Table in this mode too, and unchecking every condition shows nothing here as well (regression check for a real bug fixed 2026-08-21 where Group-by-Serial/Port ignored the condition filter entirely)

### Harmonic / Port inner filter
- [ ] Harmonic dropdown selects all or subset of harmonics by checking/unchecking longform rows
- [ ] Port inner filter works similarly

### Serial filter
- [ ] Serial filter panel present
- [ ] Uncheck a DUT → box stats recompute (whiskers may shift)

### Show points
- [ ] "Show points" checkbox in filter bar
- [ ] Check it → individual measurement dots appear on each box
- [ ] Outlier dots remain as open circles, filled dots for inliers
- [ ] Uncheck a serial → those DUT's dots change colour

### Y-range filter
- [ ] "Passing only" radio → boxes outside spec disappear
- [ ] "Upper limit" radio (shown only when TLL display is Upper/Both) → number input appears; entering a value removes raw samples above it before recomputing Q1/Q2/Q3/whiskers
- [ ] "Lower limit" radio (shown only when TLL display is Lower/Both) → same, removes samples below the entered value
- [ ] "All data" → returns to full range
- [ ] TLL display selector (Both/Upper only/Lower only) present only when the CSV has no `Upper_Limit`/`Lower_Limit` at all — switching it hides/shows the Upper limit / Lower limit radios accordingly, and falls back to "All data" if the currently-selected radio becomes hidden

### Statistics table and CSV
- [ ] Statistics table toggles (same as stat_summary)
- [ ] CSV download works

### Segment by
- [ ] "Segment by: Spec / Limit / Uncertainty" selector present **only if the dataset actually has a frequency-varying Spec/Limit/Uncertainty** (as of 2026-08-21, the whole control is omitted, not just Prev/Next, for a flat/constant-spec dataset — check both a staircase-spec pod and a flat-spec one if available); when present, Prev/Next jumps the frequency range to each contiguous spec band

### Global Filter (GF) buttons
- [ ] "Set filter as GF" / "Set outliers as GF" / "Set delta outliers as GF" each **add** to the existing GF rather than replacing it (set GF twice from different selections, confirm both sets of exclusions remain)
- [ ] "Set outliers as GF" checks outliers at **each currently-selected Temperature checkbox independently**, not Room-only — with all temps checked, confirm the resulting GF includes outliers from non-Room temps too; narrow to just Room first to get Room-only outliers
- [ ] "Clear global filter" empties it
- [ ] "Export GF CSV" downloads a CSV with `Serial,Condition,Temperature,Start_Freq,Stop_Freq,N_Points` columns
- [ ] "Import GF CSV" on a previously-exported file re-merges (adds to) the current GF without erroring
- [ ] "Copy PADB Filter" copies a `NOT IN {...}`-style expression to the clipboard (hover text notes this is under development)

### Help panel
- [ ] ⓘ Help button opens a panel explaining Filter/Segment by/GF
- [ ] Inverted Upper/Lower Limit or Spec rows (if present) are named by filter-dimension value

---

## `SG6311A_Harmonics_distribution.html`

### On load
- [ ] Delta (relative) mode: plot renders immediately with KDE curves — **no blank page on first load**
- [ ] KDE curves visible for non-Room temperatures in default delta-from-Room mode
- [ ] Spur type badge shows correct count (e.g. "24/24" if all selected, blank if all selected)
- [ ] Chart area has fixed height — delta summary table below is always visible without scrolling

### Mode toggle
- [ ] Toggle from Delta to Absolute → KDE curves update to absolute values
- [ ] Toggle back → returns to delta

### Spur type filter
- [ ] Spur type filter button shows badge when subset selected (e.g. "5/24")
- [ ] Uncheck spurs → only selected spur KDE curves shown in chart
- [ ] "Select all" / "Clear" buttons work; badge updates immediately

### Serial / port filter
- [ ] Uncheck serials → curves recompute with fewer DUTs
- [ ] Badge on serial filter button updates to show count

### Delta summary table
- [ ] Table renders below the chart with per-spur-type delta statistics
- [ ] Table remains visible when chart is present (does not toggle in/out)

### State persistence
- [ ] Adjust filters and frequency range, reload page → filters and range are restored
- [ ] Temperature selections persist across page loads (shared with other plots via `temp_*` key)
- [ ] If all spur types were previously saved as deselected, they are all restored to checked on load

### Segment by
- [ ] "Segment by: Spec / Limit / Uncertainty" selector present **only if the dataset has a frequency-varying Spec/Limit/Uncertainty** (whole control is omitted for a flat-spec dataset, as of 2026-08-21); when present, Prev/Next jumps the frequency range to each contiguous spec band (no "Group by" equivalent exists for this view)

### Help panel
- [ ] ⓘ Help button opens a panel explaining Filter/Segment by

---

## `SG6311A_Harmonics_env_coverage.html` (V2)

### On load
- [ ] UDE/LDE shaded bands render for each condition (coloured, filled, symmetric about zero)
- [ ] Room TI dashed bands visible where room data exists
- [ ] Y-axis scales to the UDE/LDE data range — NOT to TTU/TTL (which may be at large negative dBm values)
- [ ] No console errors (F12)

### P / C / MU controls
- [ ] Adjust P_env slider → UDE/LDE bands widen/narrow
- [ ] Adjust P_room slider → Room TI band changes
- [ ] Enter MU value → when Spec override is set, TTU/TTL lines move by the MU amount
- [ ] Statistics table (if open) updates on every control change

### Spec override inputs
- [ ] Enter a Spec hi value → TTU dotted line appears on the plot
- [ ] Enter a Spec lo value → TTL dotted line appears on the plot
- [ ] TTU/TTL lines do NOT cause Y-axis to rescale (they may extend off the visible area)
- [ ] Clear overrides → TTU/TTL disappear

### Serial / port / temperature filter
- [ ] Serial filter (if >1 DUT): uncheck a serial → UDE/LDE bands recompute
- [ ] Temperature filter: uncheck a temp → that condition's contribution removed from ΔEnv stats
- [ ] Frequency sliders narrow the X range

### Statistics table
- [ ] Click Statistics button → table appears below plot with UDE, LDE, TTU, TTL, Room μ, n columns
- [ ] Rows with TTL below spec or TTU above spec highlighted red
- [ ] CSV export downloads correctly

### Group by
- [ ] "Group by" dropdown collapses conditions to a single dimension; UDE/LDE/TTU/TTL recompute exactly (fully exact, not approximated, since `computeStats()` reruns from pooled raw per-DUT data)
- [ ] "Show excluded" has no visible effect while Group by is active (expected — it compares by object identity, meaningless for pooled conditions)

### Segment by
- [ ] "Segment by: Spec / Limit / Uncertainty" selector present **only if the dataset has a frequency-varying Spec/Limit/Uncertainty** (whole control is omitted for a flat-spec dataset, as of 2026-08-21); when present, Prev/Next jumps the frequency range to each contiguous spec band, respecting the current Group-by selection

### Room/ΔEnv shared population (2026-08-08)
- [ ] Deselect one DUT via the Serial filter → Room `n` and ΔEnv `n` drop together (both, not just ΔEnv)
- [ ] Selecting a single Port does NOT shrink either Room `n` or ΔEnv `n` (port stays excluded from the population on purpose)

### Help panel
- [ ] ⓘ Help button opens a panel explaining Filter/Group by/Segment by

---

## `SG6311A_Harmonics_summary.html` (V2)

### On load
- [ ] Shaded min/max bands and mean lines render for multiple conditions
- [ ] Legend shows condition names
- [ ] No serial number filter present (by design — data is pre-aggregated per condition)

### Condition filter (HarmonicNumber, Port)
- [ ] HarmonicNumber dropdown present — deselecting a harmonic hides those bands
- [ ] Port dropdown present — deselecting RF1 hides RF1 bands

### Show excluded
- [ ] "Show excluded" checkbox present
- [ ] Deselect a harmonic, check "Show excluded" → excluded bands appear dim grey behind selected

### TLL display and Data filter (added 2026-08-04)
- [ ] TLL display selector (Both/Upper only/Lower only) present only when the CSV has no `Upper_Limit`/`Lower_Limit` at all — if the pod has a real spec limit, this selector should be **absent** and the correct side should already be drawn
- [ ] Switching TLL display to "Lower only" → the upper TTL band disappears, lower TTL band appears; Results Table columns switch to TTL↓/Spec Lo/Margin↓; Data filter's "Upper limit" radio disappears and "Lower limit" appears
- [ ] Switching to "Both" → both TTL bands and both sets of table columns appear; both Upper limit and Lower limit radios are available
- [ ] "Passing only" label text matches direction (`TTL ≤ Spec` for Upper, `TTL ≥ Spec` for Lower, `TTL vs Spec` for Both)
- [ ] "Upper limit" filter hides conditions whose max data exceeds the entered value; "Lower limit" hides conditions whose min data falls below it
- [ ] CSV export column set matches the Results Table's current column set

### Global filter (GF) integration
- [ ] If GF is set in boxplot, GF badge appears in summary filter bar
- [ ] In Exclude mode: conditions with GF-flagged DUTs are dimmed/removed
- [ ] In Focus/Inspect mode: only conditions with GF-flagged DUTs are shown
- [ ] Disabling the GF checkbox restores all conditions

### Reset
- [ ] Reset / reset-filters button returns all filters to default

### Group by
- [ ] "Group by" dropdown collapses conditions to a single dimension; mean/min/max are exact (pooled), NP TI (`uttl`/`lttl`) and spec are worst-case across constituent conditions
- [ ] "Show excluded" has no visible effect while Group by is active (expected)

### Segment by
- [ ] "Segment by: Spec / Limit / Uncertainty" selector present **only if the dataset has a frequency-varying Spec/Limit/Uncertainty** (whole control is omitted for a flat-spec dataset, as of 2026-08-21); when present, Prev/Next jumps the frequency range to each contiguous spec band, respecting the current Group-by selection

### Help panel
- [ ] ⓘ Help button opens a panel explaining Filter/Group by/Segment by

---

## Global exclusion filter — cross-plot integration

The boxplot writes excluded DUT+condition keys to `localStorage['padb_v2_excluded']`. Stat_summary, distribution, and summary must all react automatically (via the `storage` event).

**Setup:** Open the boxplot and exclude one or two DUTs by right-clicking outlier points or using the serial filter.

### Stat_summary reaction
- [ ] Orange badge "N globally excl." appears in the stat_summary filter bar (after opening it in the same browser)
- [ ] Mean and TI bounds visibly shift (fewer DUTs included)
- [ ] If "Show points" is on, excluded DUT dots appear in orange-red (not grey or hidden)
- [ ] Badge disappears if exclusions are cleared in the boxplot

### Distribution reaction
- [ ] Orange badge "N globally excl." appears in the distribution filter bar
- [ ] KDE curves recompute — excluded DUTs no longer contribute to the histograms
- [ ] Badge disappears when exclusions are cleared

### Summary reaction
- [ ] Orange badge "N globally excl." appears in the summary filter bar
- [ ] Min/max shaded bands recompute — excluded DUTs' extreme values may vanish
- [ ] Badge disappears when exclusions are cleared

### Persistence
- [ ] Close and reopen stat_summary in a new tab — the badge should still appear (localStorage is persistent)
- [ ] Clear localStorage (`localStorage.removeItem('padb_v2_excluded')` in console) → all badges disappear and all plots restore full data

---

## Cross-plot regression check

After generating a new build, compare the following values against a known-good run:

| Plot | Check |
|---|---|
| stat_summary | Mean at the lowest frequency for each condition |
| stat_summary | TI bounds at the highest frequency |
| boxplot | Q1, Q2, Q3 for Room temp, HarmonicNumber:2, RF1 |
| distribution | KDE peak location for Room condition |
| summary | Max value across all DUTs at mid-frequency |

Record these in a spreadsheet or use `qa_padb.py` which asserts the stat_summary mean automatically.

---

## Cross-Site Comparison (`compare_csv` boxplot/stat_summary/summary pages only)

Applies to a boxplot, `stat_summary`, or `summary` page built from a `compare_csv` job (`"Site"` appears as a real `COND_DIMS` entry) — the Site Population Check reached `stat_summary` and `summary` on 2026-08-19/21, not just boxplot. Skip this section entirely for `env_coverage`/`distribution` pages or a normal single-site page.

### Coverage-gap banner
- [ ] An amber banner appears near the top, above the plot, without needing to click anything
- [ ] It lists real gaps only — a spec/limit value the two sites format with different trailing precision (e.g. `-100.00` vs `-100`) must **not** show up as a false gap on both sides at once

### Site Population Check panel
- [ ] "Site Population Check" button appears (only when `primary_site` is set and 2+ sites are present)
- [ ] On `summary`, the panel's own text notes it's comparing each DUT's mean blended *across all temperatures in its condition*, not a single temperature's raw points (this view has no per-temperature breakdown) — don't expect it to line up exactly with boxplot's per-temperature version on the same data
- [ ] Opening it shows a one-line summary count, then three tables: **Per-DUT summary**, **Frequency clusters**, **Per-point detail**
- [ ] Per-DUT summary's "Shared w/ other DUTs" and "Suggested triage" columns are populated for every DUT with at least one outside point
- [ ] Frequency clusters table only lists `(site, temp, freq)` combinations with 2+ distinct DUTs affected — sorted by DUT count descending
- [ ] Changing the live k×IQR control (same input boxplot's own outlier detection uses) and reopening the panel changes the fence bounds and verdicts accordingly
- [ ] Toggling condition/serial/port/temperature filters and reopening the panel reflects the new selection
- [ ] Narrowing the frequency range (drag-zoom or the min/max number inputs) and reopening the panel scopes all three tables to that window, with a note in the summary line; resetting to the full range removes the note and restores the full-dataset counts

---

## Known limitations (not regressions)

- NP TI is set to null when the serial filter is active — this is by design.
- de_summary has no serial filter — the Environmental (Type=60) CSV is pre-aggregated across all DUTs by PADB itself before extraction, so there are no per-DUT rows to filter in the first place (not a code limitation — see CLAUDE.md).
- summary (V2) has no serial *dropdown* filter, by design — GF already handles per-DUT effects for this view via embedded per-DUT-per-frequency means (`dut_vals`/`dut_info` in `render_summary()`, `padb_v2.py`), recomputed live in JS whenever GF excludes a DUT. A dedicated Serial Number Condition-filter option isn't needed on top of that. (The separate *V1-legacy* `summary_plot()` function — invoked via `secondary_plots` `"type": "summary_plot"` against a real Type=90 CSV, a discouraged V2 data source — does get a Condition-filter Serial Number option automatically when the CSV's Group column has 2+ distinct values, added 2026-08-05. Different function, different code path, not what V2's real `summary` view uses.)
- env_coverage TTU/TTL require Spec override inputs when the source CSV has null Upper_Limit/Lower_Limit columns.
- env_coverage TTU/TTL lines may extend outside the visible Y range — the axis is scaled to UDE/LDE data only, not to TTU/TTL values.
- GF Export/Import CSV's `Start_Freq`/`Stop_Freq`/`N_Points` columns are display-only context — the runtime exclusion match is on (serial, condition, temperature) only, so import does not reconstruct an exact frequency-by-frequency exclusion (by design, not a bug).
- Site Population Check only compares exact-matching frequencies between the two sites — a frequency present in only one site's sweep reports `n/a` rather than attempting a near-match.
- "Segment by: Spec"/"Segment by: Uncertainty" show zero segments if the pod's extraction didn't add `Upper/Lower Spec` or `Upper/Lower Uncertainty` as grouping items — this is a pod-authoring gap, not a code bug. "Segment by: Limit" always works.
