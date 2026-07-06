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
- [ ] "Custom range" → min/max inputs appear; entering values clips Y axis
- [ ] "All data" → returns to full range

### Statistics table and CSV
- [ ] Statistics table toggles (same as stat_summary)
- [ ] CSV download works

---

## `SG6311A_Harmonics_distribution.html`

### On load
- [ ] Delta (relative) mode: plot renders immediately with KDE curves — **no blank page on first load**
- [ ] KDE curves are visible for non-Room temperatures (0°C and 55°C in default delta-from-Room mode)

### Mode toggle
- [ ] Toggle from Delta to Absolute → KDE curves update to absolute values
- [ ] Toggle back → returns to delta

### Condition filter
- [ ] Uncheck conditions → those KDE curves disappear
- [ ] Selected conditions remain coloured

### Show excluded
- [ ] "Show excluded" checkbox present
- [ ] Check it → excluded conditions appear as dim dotted grey KDE curves
- [ ] Excluded curves have no legend entry

### Serial filter (if present)
- [ ] Uncheck serials → curves recompute with fewer DUTs

---

## `SG6311A_Harmonics_env_coverage.html` (V2)

### On load
- [ ] Scatter plot renders with multiple coloured traces (one per temperature)
- [ ] All temperature conditions visible in legend
- [ ] Hover shows temperature, frequency, and value

---

## `SG6311A_Harmonics_summary.html` (V2)

### On load
- [ ] Shaded min/max bands and mean lines render for multiple conditions
- [ ] Legend shows condition names

### Condition filter (HarmonicNumber, Port, Serial)
- [ ] HarmonicNumber dropdown present — deselecting a harmonic hides those bands
- [ ] Port dropdown present — deselecting RF1 hides RF1 bands
- [ ] Serial filter present — deselecting DUTs removes their contribution from bands

### Show excluded
- [ ] "Show excluded" checkbox present
- [ ] Deselect a harmonic, check "Show excluded" → excluded bands appear dim grey behind selected

### Reset
- [ ] Reset / reset-filters button returns all filters to default

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

## Known limitations (not regressions)

- NP TI is set to null when the serial filter is active — this is by design.
- de_summary has no serial filter — pre-aggregated data, not per-DUT.
- env_coverage has no interactive filter controls — display only.
