# PADB Analytic Requirements for Each Plot Type

This document describes exactly which PADB analytics must be configured, what CSV columns they must produce, and how the Group string must be structured to support each plot type in padb-tools.

---

## Quick Reference

| Plot type | Analytic type | CSV required | Group must contain | Key columns needed |
|---|---|---|---|---|
| `accuracy_vs_freq` | 80 Scatter | Yes | Serial (recommended) | Frequency, Value, Group |
| `distribution` | 80 Scatter | Yes | — | Frequency, Value |
| `population_envelope` | 80 Scatter | Yes | — | Frequency, Value |
| `empirical_cdf` | 80 Scatter | Yes | Serial | Frequency, Value, Serial |
| `spec_derivation` | 80 Scatter | Yes | — | Frequency, Value |
| `stat_summary` | 80 Scatter | Yes | **Serial + conditions** | Frequency, Value, Group, Serial (or Serial col) |
| `stat_boxplot` | 80 Scatter | Yes | **Serial + conditions + Temp** | Frequency, Value, Group, Serial |
| `de_summary` | **60 Environmental** | Yes | Condition dimensions | X value, Group, UDE, LDE, TTL cols |

---

## 1. Scatter Analytic (Type=80)

Used by: `accuracy_vs_freq`, `distribution`, `population_envelope`, `empirical_cdf`, `spec_derivation`, `stat_summary`, `stat_boxplot`

### Required pod settings

```
AnalyticType=80
OutputConfig_OutputCSV=True
OutputConfig_OutputFile=<your chosen filename>
```

### CSV column detection

The loader searches by keyword match (case-insensitive). Columns do not need to be named exactly as shown — the detection rules are:

| Internal field | Detection rule | How used |
|---|---|---|
| **Frequency** | Column name contains `"frequency"` or `"x value"` | X axis (MHz) |
| **Value** | First numeric column *after* the frequency column, skipping known metadata columns | Y axis measurement |
| **Group** | Column named exactly `"Group"` | Condition and serial parsing |
| **Serial** | Column whose name contains `"serial num"`, `"serial no"`, `"sn"`, `"unit id"`, or `"dut id"` (and does not contain `"station"`); or a column named exactly `"serial"` | DUT identification |
| **Station** | Column name contains `"station"` | Test station grouping |
| **Lower Limit** | Column name contains `"lower limit"` | Spec line (lower) |
| **Upper Limit** | Column name contains `"upper limit"` | Spec line (upper) |

**Value column fallback:** The value column is auto-selected as the first numeric column after the frequency column, skipping Group, Serial, Station, Lower Limit, Upper Limit, and the metadata columns `Analysis Type`, `Model(s)`, `Algorithm -> Result`, `Units`. If your CSV has unusual column ordering — non-numeric columns interspersed after frequency — verify the correct column is being picked up.

**Spec limits:** Taken from `Lower Limit` / `Upper Limit` columns in the CSV. Can be overridden in `job.json` with `"spec_limits": [lower, upper]`.

---

## 2. Environmental Analytic (Type=60)

Used by: `de_summary`

### Required pod settings

```
AnalyticType=60
OutputConfig_OutputCSV=True
OutputConfig_OutputFile=<your chosen filename>
```

### CSV column detection and requirements

The Environmental loader reads columns by **exact name** (after stripping whitespace). These are standard PADB output column names for Type=60:

| Column name | Required | Description |
|---|---|---|
| `Group` | **Yes** | Condition string (see Group string format below) |
| `X value` | **Yes** | Frequency in MHz |
| `UDE` | **Yes** | Upper delta environmental contribution (positive values expected) |
| `LDE` | **Yes** | Lower delta environmental contribution (positive values expected) |
| `Min (Env.)` | Recommended | Minimum observed environmental deviation |
| `Max (Env.)` | Recommended | Maximum observed environmental deviation |
| `mean (Env.)` | Recommended | Mean environmental deviation |
| `Upper TTL (est)` | Recommended | Estimated total tolerance limit (upper) |
| `Lower TTL (est)` | Recommended | Estimated total tolerance limit (lower) |
| `UDE (Max)` | Optional | Scalar: max UDE across all frequencies for this group |
| `LDE (Max)` | Optional | Scalar: max LDE across all frequencies for this group |
| `Lower Limit` | Optional | Spec lower limit (same for all rows of a group) |
| `Upper Limit` | Optional | Spec upper limit (same for all rows of a group) |
| `Units` | Optional | Y-axis unit string |

**INT_MAX sentinel:** PADB writes `2,147,483,647` (or close to it) in `UDE`, `LDE`, `UDE (Max)`, and `LDE (Max)` when the environmental computation fails (e.g., insufficient data, degenerate case). These are automatically clamped to `null` and excluded from the plot and statistics table.

**Mean fallback:** If `mean (Env.)` is absent but `Min (Env.)` and `Max (Env.)` are present, mean is estimated as `(Min + Max) / 2`.

---

## 3. The Group String

The `Group` column is the primary mechanism by which PADB communicates test conditions. All interactive filter panels in padb-tools are derived by parsing this string.

### Format

PADB writes Group as a space-delimited sequence of `Key: Value` pairs. **Multi-word keys are separated from adjacent pairs by two or more spaces:**

```
AlcState: TRUE  OA State: 0  Mode: 0  Serial Number: US65080401
```

The parser splits on 2+ spaces first (preserving multi-word keys like `"OA State"` and `"Serial Number"`), then extracts `Key: Value` from each segment. If no 2+ space separator is found, it falls back to single-word key parsing.

### How the Group string drives the UI

Each distinct key in the Group string becomes a potential filter dimension. The tool classifies each key as either a **condition** (appears in filter dropdowns) or a **serial identifier** (used for serial number filtering):

**Serial detection — a key is classified as serial if either:**
- The key name contains `"serial"`, `"unit id"`, `"dut id"`, or `"s/n"` (case-insensitive), **or**
- More than 50% of the observed values for that key match the pattern `^[A-Z]{2,3}\d{5,}$` (e.g., `US65080401`, `MY12345678`)

**Condition detection — a key is used as a filter dropdown if:**
- It is not classified as serial, and
- It has more than 1 distinct value across all groups

There is **no fixed upper cap in `stat_summary`'s own filter-panel builder** (`_build_stat_summary_html` — any key with 2+ distinct values gets a full checkbox panel, however large). A separate cardinality check used elsewhere (env_coverage/boxplot condition-vs-serial classification, `_build_box_interactive_html`) caps at **50**, not 20 — a key with 51+ distinct values is treated as serial-like there instead of becoming a condition filter. Neither path uses 20 as a threshold; earlier versions of this doc were wrong on this point (confirmed against `padb_plots.py` 2026-07-21 — see the VSWR2 pod's `OA` key, 40–56 distinct values, which does render as a full filter panel in `stat_summary`).

A key with high cardinality (dozens of distinct values) still functions, but produces an unwieldy filter panel (one checkbox per value) rather than being silently dropped — if your Group string design produces a key like this unintentionally, expect a large but working checkbox list, not an absent filter.

Keys with only one value (a constant across all groups) are silently ignored — they add no information to the filter.

### Group string design for full tool capability

| Desired capability | What the Group string must contain |
|---|---|
| Serial number filter in `stat_summary` / `stat_boxplot` | A key whose name contains `"serial"` (e.g., `"Serial Number: US65080401"`) **or** values matching `^[A-Z]{2,3}\d{5,}$` |
| Condition filter dropdowns | At least one key with 2+ distinct values, ideally under ~20 for a usable UI (e.g., `"OA State: 3"`, `"AlcState: TRUE"`) |
| Temperature grouping in `stat_boxplot` | A key whose name contains `"temp"` (e.g., `"Temp: 25"`, `"Temperature: -40"`) |
| Scatter trace grouping in `accuracy_vs_freq` | Serial key **or** any condition key with multiple values |
| No filter panels (simple plots) | Group can be blank or omit condition keys |

**Ideal Group string for full capability (stat_summary, stat_boxplot):**
```
Serial Number: US65080401  Mode: 0  AlcState: TRUE  OA State: 3  Temp: 25
```

This produces: a serial filter with individual DUT checkboxes, three condition filter dropdowns (Mode, AlcState, OA State), and temperature-based box grouping.

---

## 4. Per-Plot-Type Requirements

### `accuracy_vs_freq`

**Minimum:** Frequency column + Value column.

**Recommended:** Group string with serial number. Without a serial key the tool falls back to grouping by the raw Group string (one trace per unique Group value). Spec lines require `Lower Limit` / `Upper Limit` columns or `spec_limits` in job.json.

**Interactive filter panels** appear for any Group string condition key with 2+ distinct values — there is no upper cap in `stat_summary`'s builder (see §3 above); keep cardinality low (under ~20) for a usable UI, not because higher counts are dropped.

**Spec-limit segment tab-through** (added 2026-08-06): a "Segment by" selector (Spec / Limit / Uncertainty) plus Prev/Next buttons let you jump the frequency range directly to each contiguous band of a frequency-varying spec. **This only works fully if the pod's extraction was configured to include the `Upper Spec`/`Lower Spec` and/or `Upper Uncertainty`/`Lower Uncertainty` grouping items** — most existing pods only have the default `Upper Limit`/`Lower Limit`, which is spec adjusted per-unit by measurement uncertainty and is *not* reliably frequency-piecewise-constant (it fragments into more, noisier segments, or fails to reveal band structure at all). To add these: open the pod in PADB-R.exe, add `Upper Spec`/`Lower Spec` (and optionally `Upper Uncertainty`/`Lower Uncertainty`) as extraction grouping items on the Type=80 analytic, and re-save. Without them, "Segment by: Spec/Uncertainty" will show zero segments and the Prev/Next bar stays hidden — falls back to "Limit" automatically if that's the only one with data.

---

### `distribution`

**Minimum:** Frequency column + Value column.

No Group parsing. All measurements pooled into a single histogram. Spec lines from `Lower Limit` / `Upper Limit`.

Has the same spec-limit segment tab-through control as `accuracy_vs_freq` (identical "Segment by" requirement above).

---

### `population_envelope`

**Minimum:** Frequency column + Value column.

Groups all measurements by frequency, computes population statistics (min, max, P5, P50, P95, non-parametric TI). No Group parsing. Spec lines from `Lower Limit` / `Upper Limit`.

---

### `empirical_cdf`

**Minimum:** Frequency column + Value column + **Serial column**.

One CDF trace per serial number. Without a Serial column all measurements are plotted as a single trace. Spec limits from `Lower Limit` / `Upper Limit`.

---

### `spec_derivation`

**Minimum:** Frequency column + Value column.

Requires `"freq_bands"` in job.json. Spec limits from `Lower Limit` / `Upper Limit` or `spec_limits`.

---

### `stat_summary`

**Full capability requires:**
1. Frequency column + Value column
2. **Serial identification** — either a serial key in the Group string (e.g. `"Serial Number: US65080401"`) **or** a dedicated `Serial` column in the CSV (as produced by some phase noise analytics). When the Group string has no serial key, the tool automatically falls back to the CSV `Serial` column if it contains valid serial patterns (`^[A-Z]{2,3}\d{5,}$`). Without any serial source, all DUTs collapse to n=1.
3. **Group string with condition keys** — without these all measurements are treated as one condition with no filter dropdowns
4. Sufficient n per condition × frequency (n ≥ 29 for P90/C90 TI; tool warns when below minimum)
5. `Lower Limit` / `Upper Limit` columns for spec lines and pass/fail markers

**How statistics are computed:**
- For each (condition × frequency): all measurements from that DUT are first averaged, then population statistics are computed across DUTs.
- This means each DUT contributes **one data point** per (condition × frequency), regardless of how many repeat measurements PADB recorded.

**NP TI** is computed server-side by scipy and embedded in the HTML at generation time. It is set to null when the serial filter is active (client-side recomputation is not feasible for NP TI).

**Show points** embeds per-DUT values as `dut_vals: [{s: serial, v: value}]` in each `freq_stats` entry. This requires the serial identification step above — without a serial source, all DUTs are merged and individual point overlay is not meaningful.

**Spec-limit segment tab-through** (added 2026-08-06): same "Segment by" (Spec/Limit/Uncertainty) + Prev/Next control as `accuracy_vs_freq`/`distribution`/`stat_boxplot` — same requirement that the pod's extraction include `Upper Spec`/`Lower Spec` and/or `Upper Uncertainty`/`Lower Uncertainty` grouping items. Respects GF and the serial filter, same reasoning as `stat_boxplot` below.

**"Group by" (added 2026-08-06):** when a Group string has many condition keys (e.g. a per-unit-varying `Upper Limit`), "condition" is the full combination of all of them, which can fragment into a large number of near-duplicate traces. A "Group by" dropdown lets you collapse on one dimension alone (e.g. "SpurType") instead — mean/std/quantiles recompute exactly from the pooled per-DUT values; DEnv and spec take the worst case (max/tightest) across the collapsed conditions rather than a true recompute; Shapiro normality isn't recomputed at all for pooled groups (shows as "Non-normal" red, a visible cue it's approximate). See "Group by" in `CLAUDE.md` for the full reasoning.

---

### `stat_boxplot`

**Full capability requires everything `stat_summary` requires, plus:**

1. **Temperature key in the Group string** — the tool looks for a key whose name contains `"temp"`. If found, box plots are separated by temperature condition. Without a Temp key, all measurements are treated as a single temperature group.
2. A **room condition** for reference — the tool identifies the room-temperature group as the one with a Temp value closest to `25`. If no Temp key exists, all data is treated as room temperature.

**Group string example for full stat_boxplot capability:**
```
Serial Number: US65080401  OA State: 3  AlcState: TRUE  Temp: 25
```

This produces: serial filter, OA State and AlcState condition filter dropdowns, and separate box traces for each temperature.

**Spec-limit segment tab-through** (added 2026-08-06): same "Segment by" (Spec/Limit/Uncertainty) + Prev/Next control as `accuracy_vs_freq`/`distribution` — same requirement that the pod's extraction include `Upper Spec`/`Lower Spec` and/or `Upper Uncertainty`/`Lower Uncertainty` grouping items for "Spec"/"Uncertainty" to find anything. Segment detection here also respects the Global Filter (GF) and the serial/port filters — a DUT excluded via GF has its spec/limit/uncertainty contribution dropped too, not just its measured value, so isolating a clean SpurType/cohort (e.g. to work around a datapak error affecting a few units) cleans up the segment boundaries live.

---

### `summary` and `env_coverage` (V2 only, `padb_v2.py`)

Same "Segment by" (Spec/Limit/Uncertainty) + Prev/Next control and same `Upper Spec`/`Lower Spec`/`Upper Uncertainty`/`Lower Uncertainty` grouping-item requirement as the views above (added 2026-08-06). Both respect GF and the serial filter per-DUT, same reasoning as `stat_boxplot`. See "Spec-limit segment tab-through" in `CLAUDE.md` for exactly how each view's own data shape (2D `dut_vals` arrays for `summary`, per-DUT `room`/`spec` arrays for `env_coverage`) threads these fields through.

Both also have the same "Group by" dropdown as `stat_summary` (added 2026-08-06), for the same reason (collapsing fragmented per-unit conditions to one real dimension). `env_coverage`'s recompute is fully exact — its UDE/LDE/TTU/TTL are already computed client-side from raw per-DUT data on every call, so pooling the underlying DUTs needs no separate re-aggregation math. `summary`'s mean/min/max are exact; NP TI (`uttl`/`lttl`) and spec take the worst case across the collapsed conditions. See "Group by" in `CLAUDE.md` for the full reasoning.

---

### `de_summary`

**Minimum:** `Group` + `X value` + `UDE` + `LDE` columns.

**For TTL lines:** `Upper TTL (est)` and `Lower TTL (est)` columns must be present and non-null.

**For spec lines and red-highlighted rows in the statistics table:** `Lower Limit` and `Upper Limit` columns must be present.

**For the statistics table Min/Max/Mean columns:** `Min (Env.)`, `Max (Env.)`, `mean (Env.)` columns must be present.

**For the peak UDE footnote:** `UDE (Max)` must be present and not INT_MAX.

**Group string for condition filter dropdowns:** Same rules as Scatter analytics — condition keys with 2+ distinct values become filter dropdowns (no fixed upper cap; see §3). Serial keys are excluded. Unlike Scatter analytics, there is **no serial filter** because the Environmental CSV is pre-aggregated across all DUTs.

**Group string example for de_summary condition filter:**
```
Mode: 0  AlcState: TRUE  OA State: 3
```

With 11 OA State values (0–10), this produces one condition filter dropdown with 11 checkboxes.

---

## 5. Pod Configuration Checklist

For each analytic you want to plot, verify:

- [ ] `OutputConfig_OutputCSV=True` is set — if you can't fix the pod (or it's not yours to fix), `"force_output_csv": true` in job.json will force this in the `_run.pod` copy for every Type=80 analytic at run time. A real case: a CW Closed Loop pod had this at `False`, so PADB rendered native PNG/PDF but wrote zero CSVs, silently starving the V2 pipeline. `padb_make_v2_job.py` detects and sets this automatically. See `CLAUDE.md` → **`force_output_csv` job.json key**.
- [ ] `OutputConfig_OutputFile=` is set to a name that **matches the analytic name** (see note below)
- [ ] The Group string includes a serial key (for `stat_summary`, `stat_boxplot`, `accuracy_vs_freq`)
- [ ] The Group string includes a `Temp` key (for `stat_boxplot` multi-temperature capability)
- [ ] The Group string includes condition keys with 2+ distinct values (for filter dropdowns) — keep cardinality low (under ~20) for a usable UI; higher counts still render, just as a large checkbox panel
- [ ] `Lower Limit` / `Upper Limit` are configured if spec lines and pass/fail are needed
- [ ] `TestRun_RunStatus` is set to `{All}` in subex if the pod defaults to passing runs only
- [ ] For Environmental analytics: `UDE`, `LDE`, `Upper TTL (est)`, `Lower TTL (est)` are enabled in the output

### CSV filename must match the analytic name

`padb_run.py` locates each output CSV by matching the filename stem against the analytic name. If the names don't correspond, the CSV is not found and the plot is silently skipped.

**Rule:** Set `OutputConfig_OutputFile` to a filename whose stem is a close match (spaces → underscores, same words in the same order) to the analytic name in the pod.

Example — analytic named `Harmonics_Env_Dataset2`, output file should be:
```
OutputConfig_OutputFile=Harmonics_Env_Dataset2.csv      ← correct
OutputConfig_OutputFile=Harmonics_Dataset.csv           ← will not match, plot skipped
```

If an exact name match is not possible (e.g. the analytic name contains words that don't appear in the output filename), use `csv_file` in the job JSON to specify the filename explicitly instead of relying on auto-matching:
```json
{ "type": "accuracy_vs_freq", "csv_file": "Scatter_My_Data.csv", ... }
```

### Minimum n for statistical plots

| TI level | Minimum n per (condition × frequency) |
|---|---|
| P90/C90 | 29 |
| P95/C90 | 59 |
| P99/C95 | 299 |

With fewer DUTs (e.g., n=6–15 in early production), P90/C90 is the maximum supportable level and the tool will flag an adequacy warning. The TI is still computed and displayed but should be treated as indicative.

---

## 6. Overlay and Comparison Controls

### "Show points" — `stat_boxplot` and `stat_summary`

Individual per-DUT measurement points can be overlaid on the active traces using the **"Show points"** checkbox in the filter bar. No additional CSV is required — per-DUT values are embedded at HTML generation time.

**`stat_boxplot`** (`vals_detail: [{s, v}]` in `BOX_DATA`):
- Respects the serial filter and Y-range filter
- Filled circle markers (size 5, opacity 0.55) in the same colour as the box trace
- Outlier markers remain `circle-open` (larger, size 7) for visual distinction
- Hovering shows the serial number and value

**`stat_summary`** (`dut_vals: [{s, v}]` in each `freq_stats` entry of `STAT_DATA`):
- Respects the serial filter — points for excluded serials are shown in grey (rgba(160,160,160,0.4)) rather than hidden, so the full population remains visible while statistics reflect only selected DUTs
- Markers size 5, opacity 0.7, white border

### "Show excluded" — `stat_summary`, `de_summary`, `distribution` (V2), `summary` (V2)

A checkbox that renders conditions currently hidden by the condition filter as dim grey background traces. The selected conditions remain in full colour in front. Useful for comparing a filtered subset against the full population without toggling the filter off.

| Plot | Excluded rendering |
|---|---|
| `stat_summary` | Dim grey mean ± σ band |
| `de_summary` | Dim grey UDE/LDE band |
| `distribution` (V2 delta-env) | Dim dotted grey KDE curve |
| `summary` (V2) | Dim grey min/max fill band + mean line |

### Condition filter in `summary` (V2)

The V2 `summary` plot includes condition filter dropdowns for all dimensions found in the data. For datasets with per-DUT conditions (e.g. harmonics with HarmonicNumber, Port, and Serial Number all in the Group string), the filter includes:
- **HarmonicNumber** — filter to specific harmonics
- **Port** (RF1/RF2 or similar path-labelled values) — filter to a specific port
- **Serial** — filter to specific DUTs (individual DUT min/max/mean bands)

---

## 7. Pod Requirements for padb_v2.py (Contributing a New Pod)

This section is for engineers providing a pod file for analysis via `padb_v2.py`. It describes the minimum requirements and the one rule that can cause silent errors.

### Required: Type=80 (Scatter) analytics

All views generated by `padb_v2.py` (scatter, stat_summary, boxplot, distribution, env_coverage, summary) are driven from a single Type=80 Scatter CSV. Do not use Type=60 (Environmental) or Type=90 (SummaryPlot) analytics as the primary data source — those produce pre-aggregated output that cannot be used for per-DUT analysis.

### Critical rule: one measurement value per analytic

The tool auto-selects the value column as the **first numeric column after the frequency column**. If your scatter analytic outputs two measurement columns (e.g. "Measured Power" and "Set Power" side by side), the tool picks the first one silently — no warning is shown. 

**If you need two measurements, use two separate analytics with two separate CSVs and two separate job entries.**

### Required: include all temperatures in the extract

```
Environment_TestStep={All}
```

or list specific steps:

```
Environment_TestStep='Room','0 Deg C','55 Deg C'
```

Room-only extracts (`Environment_TestStep='Room'`) disable the distribution, environmental coverage, and delta-env views — those plots require non-Room temperature data to compute any delta. The scatter and stat_summary views still work, but show Room data only.

### Group column — what becomes a filter

The Group column is parsed automatically into filter dropdowns. PADB populates it from the grouping dimensions you configure in the analytic. Each distinct key in the Group string becomes a filter if it has 2+ distinct values — there is no fixed upper cap (see §3), so a high-cardinality grouping dimension (e.g. a compound calibration-state key) still becomes a filter panel, just an unwieldy one.

Design your grouping dimensions with the filters you want in mind:

| Desired filter | Include in analytic grouping |
|---|---|
| Port (RF1 / RF2) | Port dimension |
| Mode | Mode dimension |
| ALC state (on/off) | AlcState dimension |
| Serial number | Serial Number dimension |

Keys with only one value across all data (e.g. Mode always = 0) are silently ignored — they appear in the Group string but produce no filter dropdown.

**One analytic per ALC state is fine** — it is not possible to have both Leveled and Unleveled data in the same scatter analytic, so those naturally become separate job entries and separate HTML files.

### Spec limits (optional)

If the analytic has spec limits configured, they appear automatically as `Lower Limit (>=)` and `Upper Limit (<=)` columns in the CSV and are shown as spec lines in the plots. If not configured, the stat_summary still works — users can type spec limits manually via the Spec↑/↓ controls in the HTML.

**One-sided measurements with no pod spec limits:** `stat_summary`/`summary`/`stat_boxplot` all auto-detect which spec line(s) to draw based on whether `Lower Limit`/`Upper Limit` are populated in the CSV. If the pod has `Limits_YLimit=None` on every analytic (no limits configured at all) but the measurement is conceptually one-sided — e.g. a guaranteed-minimum max-power spec — auto-detection has nothing to go on. Set `"spec_direction": "lo"` (or `"hi"`) explicitly in the job JSON to set the default direction. In `stat_summary` this is the only mechanism (plus manual Spec↑/↓ entry) — there's no live selector. In `summary` and `stat_boxplot`, `spec_direction` only sets the *default*; because the CSV genuinely has no limit, a live "TLL display: Both / Upper only / Lower only" selector is also shown, letting a viewer switch without regenerating (a real CSV limit, when present, always overrides `spec_direction` and removes the selector — data beats config). See the 4 `MaxPower3_*_v2_job.json` jobs (`Leveled_Log`/`Leveled_Linear`/`Unleveled_Log`/`Unleveled_Linear`) for working examples — not `maxpower3_leveled_linear_job.json`, which is set to `"auto"` for an unrelated, earlier reason and no longer demonstrates this pattern.

### Serial Number column

PADB always outputs a `Serial Number` column in scatter CSVs. Do not suppress it. Without it all DUTs collapse to n=1 and statistical plots are not meaningful.

### Checklist for a new pod

- [ ] Extract includes all temperatures: `Environment_TestStep={All}` (or explicit list)
- [ ] Each analytic has one measurement value column only
- [ ] Group string includes Serial Number dimension (for per-DUT filtering)
- [ ] Group string includes the condition dimensions you want as filter dropdowns (Port, Mode, AlcState, etc.)
- [ ] `TestRun_RunStatus={All}` in subex (most pods default to passing runs only, which can silently exclude data)
- [ ] Spec limits configured in the analytic if available (optional — can be entered manually in the HTML)
- [ ] `OutputConfig_OutputCSV=True` on each analytic
- [ ] `OutputConfig_OutputFile=` filename stem matches the analytic name (words in the same order, spaces → underscores); or use `csv_file` in the job JSON to specify the path explicitly
