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
| `stat_summary` | 80 Scatter | Yes | **Serial + conditions** | Frequency, Value, Group, Serial |
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
- It has more than 1 and no more than 20 distinct values across all groups

Keys with only one value (a constant across all groups) are silently ignored — they add no information to the filter.

### Group string design for full tool capability

| Desired capability | What the Group string must contain |
|---|---|
| Serial number filter in `stat_summary` / `stat_boxplot` | A key whose name contains `"serial"` (e.g., `"Serial Number: US65080401"`) **or** values matching `^[A-Z]{2,3}\d{5,}$` |
| Condition filter dropdowns | At least one key with 2–20 distinct values (e.g., `"OA State: 3"`, `"AlcState: TRUE"`) |
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

**Interactive filter panels** appear only if the Group string contains condition keys with 2–20 distinct values.

---

### `distribution`

**Minimum:** Frequency column + Value column.

No Group parsing. All measurements pooled into a single histogram. Spec lines from `Lower Limit` / `Upper Limit`.

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
2. **Group string with a serial key** — without this there is one "unknown" DUT and no serial filter
3. **Group string with condition keys** — without these all measurements are treated as one condition with no filter dropdowns
4. Sufficient n per condition × frequency (n ≥ 29 for P90/C90 TI; tool warns when below minimum)
5. `Lower Limit` / `Upper Limit` columns for spec lines and pass/fail markers

**How statistics are computed:**
- For each (condition × frequency): all measurements from that DUT are first averaged, then population statistics are computed across DUTs.
- This means each DUT contributes **one data point** per (condition × frequency), regardless of how many repeat measurements PADB recorded.

**NP TI** is computed server-side by scipy and embedded in the HTML at generation time. It is set to null when the serial filter is active (client-side recomputation is not feasible for NP TI).

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

---

### `de_summary`

**Minimum:** `Group` + `X value` + `UDE` + `LDE` columns.

**For TTL lines:** `Upper TTL (est)` and `Lower TTL (est)` columns must be present and non-null.

**For spec lines and red-highlighted rows in the statistics table:** `Lower Limit` and `Upper Limit` columns must be present.

**For the statistics table Min/Max/Mean columns:** `Min (Env.)`, `Max (Env.)`, `mean (Env.)` columns must be present.

**For the peak UDE footnote:** `UDE (Max)` must be present and not INT_MAX.

**Group string for condition filter dropdowns:** Same rules as Scatter analytics — condition keys with 2–20 distinct values become filter dropdowns. Serial keys are excluded. Unlike Scatter analytics, there is **no serial filter** because the Environmental CSV is pre-aggregated across all DUTs.

**Group string example for de_summary condition filter:**
```
Mode: 0  AlcState: TRUE  OA State: 3
```

With 11 OA State values (0–10), this produces one condition filter dropdown with 11 checkboxes.

---

## 5. Pod Configuration Checklist

For each analytic you want to plot, verify:

- [ ] `OutputConfig_OutputCSV=True` is set
- [ ] `OutputConfig_OutputFile=` is set to a unique, recognisable name
- [ ] The Group string includes a serial key (for `stat_summary`, `stat_boxplot`, `accuracy_vs_freq`)
- [ ] The Group string includes a `Temp` key (for `stat_boxplot` multi-temperature capability)
- [ ] The Group string includes condition keys with 2–20 distinct values (for filter dropdowns)
- [ ] `Lower Limit` / `Upper Limit` are configured if spec lines and pass/fail are needed
- [ ] `TestRun_RunStatus` is set to `{All}` in subex if the pod defaults to passing runs only
- [ ] For Environmental analytics: `UDE`, `LDE`, `Upper TTL (est)`, `Lower TTL (est)` are enabled in the output

### Minimum n for statistical plots

| TI level | Minimum n per (condition × frequency) |
|---|---|
| P90/C90 | 29 |
| P95/C90 | 59 |
| P99/C95 | 299 |

With fewer DUTs (e.g., n=6–15 in early production), P90/C90 is the maximum supportable level and the tool will flag an adequacy warning. The TI is still computed and displayed but should be treated as indicative.

---

## 6. Future Plot Types (Not Yet Implemented)

### Parallel scatter overlay for `stat_boxplot`

The box plot's source CSV is pre-aggregated to quartile statistics by PADB (Type=90 SummaryPlot or derived from Type=80). To overlay individual measurement points on the box plot — accessing the original datum per DUT per frequency — a second Type=80 Scatter analytic would need to be configured alongside the box plot's source analytic, sharing the same frequency grid. The scatter analytic's CSV would be loaded in parallel and used to draw individual measurement points on each box.

This would require:
- A Type=80 Scatter analytic with the same measurement, frequency grid, and Group string as the box plot source
- A new `scatter_overlay` key in the secondary_plot config pointing to the scatter CSV
- Client-side rendering of scatter points alongside the existing Plotly box traces
