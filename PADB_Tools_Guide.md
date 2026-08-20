# PADB Modern Analysis Tools — User Guide

**Tools:** `padb_run.py`, `padb_v2.py`, `padb_plots.py`, `padb_simple.py`, `padb_stats.py`, `padb_batch.py`, `padb_scheduler.py`, `padb_make_job.py`, `padb_make_v2_job.py`, `padb_convert_site.py`, `padb_csv_check.py`, `webapp/padb_web.py`  
**Location:** `C:\apps\padb\tools\`  
**Purpose:** Automate PADB extraction from a `.pod` file, generate interactive self-contained HTML plots, and publish results to a shared drive.

---

## Prerequisites

### Python

Python 3.10 or later. Install packages once:

```
py -m pip install pandas numpy matplotlib scipy plotly
```

### PADB-R.exe

Must be installed at:
```
C:\Program Files\KEYSIGHT\PADB-R.NET\PADB-R.exe
```

Override the path in `job.json` with `"padb_exe"` if yours differs.

---

## How It Works

```
job.json  →  padb_run.py  →  PADB-R.exe  →  results\padb\  (CSVs, PDFs)
                          →  padb_plots.py →  results\plots\ (interactive HTML)
                          →  index.html   (gallery page)
                          →  copy to publish destination (network share)
```

1. `padb_run.py` reads `job.json` and the `.pod` file.
2. It creates `_run.pod` — a copy of your pod with `subex` overrides applied. This is what PADB actually processes.
3. PADB-R.exe runs all analytics, writing CSVs and PDFs to `results\padb\`.
4. For each entry in `secondary_plots`, the matching function in `padb_plots.py` generates a self-contained interactive HTML in `results\plots\`.
5. `results\index.html` is written — a gallery page with embedded plots, PDF links, and CSV downloads.
6. `results\` is copied to the publish destination.

**PADB-R.exe is a WinForms application** (GUI subsystem). It runs headlessly but requires a Windows desktop session — do not run from a service or SSH session without a virtual desktop.

**Three tiers.** Everything above describes the default (**legacy**) pipeline. A `"mode"` key in job.json selects one of three tiers — see **PADB Simple Mode** below for the third:

| `mode` | What runs |
|---|---|
| *(omitted)* or `"legacy"` | This pipeline — `padb_plots.py` custom plots via `secondary_plots`, as described in this section. Default; every job.json without a `mode` key is unaffected by anything below. |
| `"simple"` | No custom plotting at all — PADB-R.exe's own native PNG/PDF renders, wrapped in a bare gallery. See **PADB Simple Mode**. |
| `"interactive"` | Label only, documenting that this job feeds the V2 two-command flow (`padb_run.py` extract, then `padb_v2.py` plot — see **V2 Pipeline** below). Does not change what this step does. |

---

## Folder Structure

Each analysis is independent. Everything is contained in the analysis folder.

```
MyAnalysis\
  MyMeasurement.pod          ← PADB pod file
  job.json                   ← run configuration (the only file you edit)
  results\
    index.html               ← main results gallery (open this)
    _run.pod                 ← pod submitted to PADB (auditable)
    padb_switches.txt        ← PADB-R switch file
    run.log                  ← PADB-R stdout/stderr
    padb_run_YYYYMMDD_HHMMSS.log ← full stdout log (auto-generated each run)
    padb\
      Scatter_CSV_name.csv   ← PADB CSV outputs
      Environmental_*.csv
      *.pdf                  ← PADB PDF reports
    plots\
      My_Plot_Title.html     ← interactive Plotly plots
      ...
```

---

## Running the Tool

### CLI

```
py "C:\apps\padb\tools\padb_run.py" path\to\job.json [options]
```

### Options

| Option | Effect |
|---|---|
| *(none)* | Full run: PADB + plots + publish |
| `--dry-run` | Build switch file only; do not call PADB-R.exe |
| `--no-publish` | PADB + plots; skip copy to share |
| `--plots-only` | Skip PADB entirely; regenerate plots from existing CSVs |

`--plots-only` is fast (seconds). Use it whenever you tweak `secondary_plots` entries without needing to re-extract data.

---

## Inspecting a Pod File

Open the `.pod` file in any text editor. Each analytic block looks like:

```
[Analytic_1]
AnalyticName=Amplitude Accuracy Ref Scatter Order by Test Step
AnalyticType=80
OutputConfig_OutputFile=Amplitude_Accuracy_Ref_Scatter_Order_by_Test_Step
OutputConfig_OutputCSV=True
...
```

Key fields:

| Field | Used for |
|---|---|
| `AnalyticName` | Value of `csv` key in job.json (substring match) |
| `AnalyticType` | Determines which secondary_plot type to use |
| `OutputConfig_OutputFile` | Base filename in `results\padb\` (used for `csv_file` key) |
| `OutputConfig_OutputCSV` | Must be `True` for a CSV to be written |

### Analytic type → plot type

| AnalyticType | Data shape | Plot types available |
|---|---|---|
| **80** Scatter | One row per measurement (per DUT per frequency) | `accuracy_vs_freq`, `distribution`, `population_envelope`, `empirical_cdf`, `spec_derivation`, `stat_summary`, `stat_boxplot` |
| **60** Environmental | Pre-aggregated: one row per condition × frequency | `de_summary` |
| **90** SummaryPlot | Summary stats per frequency (if CSV written) | `accuracy_vs_freq` only — SummaryPlot CSVs are pre-aggregated and cannot be used with `stat_summary` (which needs raw per-DUT rows). Use the corresponding Scatter analytic's CSV for `stat_summary`. |
| **20** BoxPlot | No CSV output | *(none — use scatter analytic instead)* |

---

## The job.json File

All configuration for a run lives in `job.json`.

```json
{
    "description": "Human-readable label for this run",

    "pod": "MyMeasurement.pod",

    "padb_exe": "C:\\Program Files\\KEYSIGHT\\PADB-R.NET\\PADB-R.exe",

    "results_dir": "results",

    "padb_timeout": 7200,

    "run_datetimes": [
        "06/22/2026 04:38:01 PM",
        "06/23/2026 02:33:43 AM"
    ],

    "serial_nums": ["US65080415", "US65080423", "US65080431"],

    "subex": {
        "Device_MinDate": "2026-06-01",
        "Device_MaxDate": "2026-06-30"
    },

    "run_analytics": true,

    "secondary_plots": [ ... ],

    "publish": {
        "destination": "\\\\srsnas01.srs.is.keysight.com\\prod\\MIDRF3\\SG6311A\\MyAnalysis"
    }
}
```

### Top-level keys

| Key | Description |
|---|---|
| `description` | Free text, shown in index.html run info card. |
| `pod` | Path to the `.pod` file, relative to `job.json`. |
| `padb_exe` | Full path to PADB-R.exe. |
| `results_dir` | Output folder, relative to `job.json`. Default: `results`. |
| `padb_timeout` | Seconds before PADB-R.exe is killed. Default: `600`. Large datasets need 7200+. |
| `run_datetimes` | List of specific test run timestamps to extract. Overrides `TestRun_RunDateTime` in the pod. |
| `serial_nums` | List of DUT serial numbers. Overrides `TestRun_SerialNum` in the pod. |
| `run_labels` | List of run label strings. Overrides `TestRun_RunLabel` in the pod. |
| `subex` | Raw key=value overrides for any `[Extract]` field. Use for fields not covered by the list keys above. |
| `run_analytics` | `true` to run PADB analytics. Default: `true`. |
| `mode` | `"legacy"` (default), `"simple"`, or `"interactive"` — see **Three tiers** above and **PADB Simple Mode** below. |
| `secondary_plots` | List of plot configurations (see below). Ignored when `mode` is `"simple"`. |
| `publish.destination` | UNC or local path to copy `results\` to. Omit to skip publish. |

### Selecting specific test runs

Use `run_datetimes` to restrict extraction to specific test runs identified by their Oracle timestamp. Copy the timestamps exactly as PADB records them (MM/DD/YYYY HH:MM:SS AM/PM):

```json
"run_datetimes": [
    "06/22/2026 04:38:01 PM",
    "06/23/2026 02:33:43 AM",
    "06/23/2026 12:33:33 PM"
]
```

Use `serial_nums` and `run_labels` the same way — plain JSON lists, no manual quoting required:

```json
"serial_nums": ["US65080415", "US65080423", "US65080431"],
"run_labels":  ["DDS Harmonics", "Spectral YTO Mode 0 ALC ON"]
```

Omit any of these keys (or set to `[]`) to use whatever is baked into the pod.

### subex — raw Extract overrides

Use `subex` for `[Extract]` fields not covered by the list keys above. Values must match PADB's expected format exactly:

```json
"subex": {
    "Device_MinDate":    "2026-06-13",
    "Device_MaxDate":    "2026-06-30",
    "TestRun_RunStatus": "{All}"
}
```

`TestRun_RunStatus: "{All}"` is required when a pod is configured to filter to passing runs only (the PADB default). Without it, PADB may return no data for pods that have a RunStatus filter.

If a `subex` key duplicates a list field (`run_datetimes`, `serial_nums`, `run_labels`), the explicit `subex` entry wins.

The original `.pod` is never modified. A `_run.pod` copy is written to `results\` with all substitutions applied.

#### Relative-date values

Any `subex` value can be a placeholder instead of a literal date, resolved to PADB's `YYYY-MM-DD` format at the moment the job actually runs — this was a capability the old PADB::Simple tool had that this replacement was missing:

```json
"subex": {
    "Device_MinDate": "8 weeks ago",
    "Device_MaxDate": "today"
}
```

Supported forms (case-insensitive): `"today"`, and `"N day(s) ago"` / `"N week(s) ago"` / `"N month(s) ago"` / `"N year(s) ago"` for any whole number N. Month/year math uses real calendar arithmetic, not a 30/365-day approximation. Anything that doesn't match one of these forms (a literal date, `"{All}"`, a quoted list) is left exactly as written — this is safe to use in any `subex` block unconditionally.

This is most useful for recurring or scheduled jobs (see **Scheduling Overnight Runs** below) that should always cover "the last N weeks" rather than a fixed range that goes stale the day after you write the job.json.

---

## Secondary Plots

Each entry in `secondary_plots` produces one self-contained HTML file.

### Common keys

| Key | Description |
|---|---|
| `type` | Plot function name (see below). |
| `csv` | Analytic name substring match against the value of `AnalyticName=` in the pod. Case-sensitive. |
| `csv_file` | Exact filename (with extension) in `results\padb\`. Use this when `csv` is ambiguous or two analytics share the same output filename. `csv_file` takes precedence over `csv`. |
| `title` | Plot title shown in the HTML and the index gallery. |
| `y_label` | Y-axis label string. |
| `y_lim` | `[min, max]` to pin the Y axis. Omit for auto-scale. |
| `log_x` | `true` to default to log X. Auto-detected when freq_max / freq_min ≥ 100. A toggle is always shown in the plot. |
| `proportion` | Tolerance interval proportion. Default: `0.90`. |
| `confidence` | Tolerance interval confidence. Default: `0.90`. |

---

## Plot Types

### `accuracy_vs_freq` (V1) / `scatter` (V2)

**Source:** Type=80 Scatter CSV. The V2 `scatter` view is this same function called via `render_scatter`.
**What it shows:** Individual measurement values vs frequency, one trace per serial number or condition. Spec limit line rendering **auto-detects constant vs. frequency-varying specs**: a genuinely constant spec (≤3 distinct rounded values, e.g. small MU-adjustment noise) draws full-width horizontal dashed lines as before; a frequency-varying spec (PADB `Limits_YLimit=Line` — a phase-noise mask, a frequency-banded dBc spec) draws a proper per-frequency step line (`line:{shape:'hv'}`) following the real (frequency, limit) pairs instead. No job.json key controls this — it's re-detected from the currently-filtered data on every render.

**Interactive controls:**
- **Group by** — display by serial number, test step, or any condition dimension parsed from the Group field
- **Sort** — traces by name, worst-first, or median value
- **Condition filter dropdowns** — one per dimension (OA State, AlcState, etc.)
- **Segment by: Spec / Limit / Uncertainty + Prev/Next** — jumps the frequency-range filter to each contiguous band of a frequency-varying spec, sourced from whichever limit-key pair (`Upper/Lower Limit`, `Upper/Lower Uncertainty`, or `Upper/Lower Spec`) the pod's extraction actually selected as a grouping item. Respects the current condition/serial/port filters. See **Segment-by Tab-Through** below.
- **Frequency sliders** — min/max zoom on the X axis
- **Log X toggle**
- **Reset button**
- **Help (ⓘ) panel** — explains the filter/segment controls and flags rows where Upper/Lower Limit or Spec is inverted (backwards) — see **In-Page Help Panel** below.
- **Hover** — shows frequency (labelled with `x_unit`, default `"MHz"`), group label, and value

**Axis labeling:** x-axis title comes from `x_label` (default `"Frequency (MHz)"`); hover/CSV-export text uses the shorter `x_unit` (default `"MHz"`). Set both explicitly for a non-MHz x-axis (e.g. a phase-noise pod's `"Frequency Offset (Hz)"` / `"Hz"`).

**Best for:** Initial sanity check. Spotting outlier DUTs. Confirming which units are out of spec and at which frequencies.

---

### `distribution`

**Source:** Type=80 Scatter CSV  
**What it shows:** Histogram of all measurement values + kernel density estimate + best-fit parametric PDF (normal, lognormal, Weibull, or gamma — selected by AIC). Spec limits as vertical lines.

**Best for:** Confirming or ruling out normality. Understanding distribution shape before choosing whether Gaussian TI or non-parametric TI is more appropriate.

---

### `population_envelope`

**Source:** Type=80 Scatter CSV  
**What it shows:** Per-frequency population statistics across all units: min/max, P5–P95 band, median, and non-parametric tolerance interval bounds. No normality assumption.

**Best for:** Population-level summary with statistically defensible bounds when individual traces are too noisy to read.

---

### `empirical_cdf`

**Source:** Type=80 Scatter CSV  
**What it shows:** Empirical CDF — sorted measurement fraction vs value — one trace per serial. Spec limits as vertical lines. Read off the fraction of measurements within spec directly from the Y axis.

**Best for:** Yield estimation. Does not require any distributional assumption.

---

### `spec_derivation`

**Source:** Type=80 Scatter CSV  
**What it shows:** Per frequency-band analysis: n, median, P5/P95, non-parametric tolerance interval, and margin to the spec limit. Includes a sample-size adequacy warning.

**Extra key required:**
```json
"freq_bands": [
    ["8-100 MHz",     8,    100],
    ["100 MHz-1 GHz", 100,  1000],
    ["1-6 GHz",       1000, 6000],
    ["6-20 GHz",      6000, 20000]
]
```

**Best for:** Deriving or validating a proposed datasheet spec from measured production data. The margin bar shows how far the TI is from the spec — green = comfortable margin, red = spec is violated or tight.

---

### `stat_summary`

**Source:** Type=80 Scatter CSV  
**What it shows:** Per-frequency statistical summary: mean ± 1σ, parametric TI band, non-parametric TI bounds, pass/fail markers vs spec. One panel per condition group.

**Interactive controls:**
- **Condition filter dropdowns** — one per condition dimension (OA State, AlcState, mode, etc.)
- **Group by** — collapse the full condition combination down to a single dimension (e.g. just SpurType) when a pod's grouping items (Upper Spec/Uncertainty, etc.) fragment "condition" into many near-duplicate entries. Mean/std/quantiles/outliers recompute exactly from pooled per-DUT data; Shapiro normality is not recomputed for a pooled group and renders as "Non-normal" (a visible cue it's approximate) rather than a real result. See **Group By** below.
- **Serial number filter** — uncheck individual DUTs to exclude them; statistics recompute live
- **Segment by: Spec / Limit / Uncertainty + Prev/Next** — jump the frequency range to each contiguous spec band; respects the current condition/serial/Group-by selection. See **Segment-by Tab-Through** below.
- **TI toggle** — show/hide parametric tolerance interval band
- **NP TI toggle** — show/hide non-parametric tolerance interval bounds (requires server-side scipy; displayed when data is sufficient)
- **Show points** — overlay individual per-DUT measurement points on each trace. Points for serials currently excluded by the serial filter are shown in grey rather than hidden, so the full population remains visible while the statistics reflect only the selected DUTs.
- **Show excluded** — display conditions currently excluded by the condition filter as dim grey traces in the background, so you can compare filtered and unfiltered populations without switching the filter off.
- **Frequency sliders** — min/max zoom on the X axis
- **Log X toggle**
- **Help (ⓘ) panel** — see **In-Page Help Panel** below.
- **Statistics Table toggle** — opens a scrollable table below the plot showing per-condition, per-frequency: n, mean, σ, Q1, Q2, Q3, normality (Shapiro-Wilk W), NP TI bounds, outliers with serial numbers. Wrapped in try/catch — if it ever fails to build, the panel shows the actual JS error message instead of staying empty. **If the table looks stale/unresponsive to filter changes, hard-reload the browser tab (Ctrl+Shift+R) before assuming it's a bug** — a stale cached copy of an older version of this same file is the most common cause, confirmed twice in practice.
- **CSV export** — downloads a CSV of all visible data

**Best for:** Primary statistical deliverable for a measurement characterisation. Captures both the population spread and the statistical confidence bounds.

**Key parameters:**
- `proportion` — fraction of the population the TI must capture (default 0.90)
- `confidence` — confidence level that the stated proportion is captured (default 0.90)
- `spec_direction` — which spec line(s) to show: `"lo"`, `"hi"`, `"both"`, `"none"`, or `"auto"` (default). Auto-detects from whether the CSV's `Lower Limit`/`Upper Limit` columns are populated — and a real detected limit always wins over this key regardless of what it's set to. Only sets the *default* selection when the CSV has no limit at all; e.g. the 4 `MaxPower3_*_v2_job.json` jobs use `"spec_direction": "lo"` because max output power is a guaranteed-minimum (lower-spec-only) measurement with no limits in the pod itself. `stat_summary` still only exposes this via the manual spec_lo/spec_hi number-entry fallback (no live radio selector) — see `summary` and `stat_boxplot` below for the selector-based version of this same rule.
- `x_label` / `x_unit` — axis title / short unit suffix for a non-MHz x-axis (default `"Frequency (MHz)"` / `"MHz"`); see the `accuracy_vs_freq` section above.
- Required n: 29 for P90/C90, 59 for P95/C90, 299 for P99/C95

---

### `stat_boxplot`

**Source:** Type=80 Scatter CSV  
**What it shows:** Box-and-whisker plots per condition, grouped by temperature condition. Box = Q1–Q3, whisker = 1.5×IQR, dots = outliers. Normality colour coding (green = Shapiro-Wilk p ≥ 0.05, red = non-normal).

**Interactive controls:**
- **Condition filter dropdowns** — one per non-temperature condition dimension
- **Temperature filter** — show/hide individual temperature conditions
- **Serial number filter** — uncheck individual DUTs; box statistics recompute live
- **TLL display: Both / Upper only / Lower only** — same rule as the `summary` plot (see above): shown as a live selector only when the CSV has no `Upper_Limit`/`Lower_Limit` at all, defaulting from job.json's `spec_direction`. A detected CSV limit always wins with no selector.
- **Data filter: All data / Passing only / Upper limit / Lower limit** — "Upper limit"/"Lower limit" are two independent radios (added 2026-08-04, replacing a single relabeled "range" radio that couldn't represent both bounds when direction is "Both"), each shown only when relevant to the current TLL direction. Unlike `summary`'s filter (which hides whole condition traces), this one trims individual raw sample points *before* Q1/Q2/Q3/whiskers are computed — "Upper limit" removes samples above the typed-in value, "Lower limit" removes samples below it.
- **Segment by: Spec / Limit / Uncertainty + Prev/Next** — jump the frequency range to each contiguous spec band. See **Segment-by Tab-Through** below.
- **Show points** — overlay individual per-DUT measurement points on each box trace (size 5, semi-transparent). Points for serials excluded by the serial filter are shown in grey; outliers remain as open-circle markers for visual distinction. Hovering a scatter point shows the serial number and value.
- **Global Filter (GF) buttons** — "Set filter as GF" / "Set outliers as GF" / "Set delta outliers as GF" each *add* to the current GF (they don't replace it — use "Clear global filter" first to start over). **Export GF CSV** / **Import GF CSV** round-trip the current GF through a human-readable CSV; import re-merges (adds to) the current filter, same as the "Set ... as GF" buttons. **Copy PADB Filter** builds a real PADB filter expression (`'Field' = "value"` / `!=` / `IN {...}`, joined with `AND`) reflecting the *current view's own active filters* — condition dims, Serial, Port, Frequency range, Temperature — not the Global Filter's exclusion list. Verified to match PADB's real filter syntax against a hand-provided reference expression. See **Global Filter (GF)** below for what GF actually does across views.
- **Log X toggle**
- **Help (ⓘ) panel** — see **In-Page Help Panel** below.
- **Statistics Table toggle** — scrollable table below the plot showing per-condition, per-frequency: n, mean, σ, Q1, Q2, Q3, normality, NP TI bounds, outliers with serial numbers, plus `Max +Δ`/`Max -Δ` columns splitting outliers by sign relative to the median. Purely descriptive (no spec/TLL columns), so it has no direction-dependent columns to toggle.
- **Site Population Check** (only rendered on a `compare_csv` page — see **Cross-Site Comparison** below) — tests each non-primary-site DUT's value at each frequency/temperature against the k×IQR fence built from the primary site's own population at that same point. Includes a per-DUT rollup and a per-frequency "multiple DUTs affected" cluster table to help distinguish a bad DUT from a station/calibration issue — see that section for the full triage logic.
- **CSV export**
- **Outlier hover** — shows value and serial number of each outlier point

**Best for:** Comparing spread across temperature conditions. Identifying which condition drives the worst-case. Outlier identification with serial traceability.

**Axis labeling:** the frequency filter label, stats table header, and CSV export headers follow `x_unit` (default `"MHz"`) — set alongside `x_label` for a non-MHz x-axis. Note the plot's own x-axis title stays a generic `"Frequency"` (no unit suffix), since it's categorical (one box per discretized frequency label), not a continuous numeric axis.

---

## Global Filter (GF)

A cross-view DUT exclusion set, created in `stat_boxplot`/boxplot and automatically respected by `summary` and `env_coverage` (both V2). Stored in the browser's `localStorage` (`padb_v2_excluded`), so it persists across page reloads for the same results folder but is not shared between different machines/browsers.

- **Additive, not replacing.** "Set filter as GF" / "Set outliers as GF" / "Set delta outliers as GF" all *add* to whatever's already in the GF — none of them start from empty. Use **Clear global filter** first if you want to start over.
- **Export GF CSV / Import GF CSV** round-trip the GF through a human-readable CSV (`Serial,Condition,Temperature,Start_Freq_<unit>,Stop_Freq_<unit>,N_Points`). The frequency columns are **display-only context** — the actual runtime exclusion check matches only on (serial, condition, temperature), not frequency, so importing doesn't reconstruct an exact frequency-by-frequency exclusion, just the same (serial, condition, temperature) match the original exclusion produced. Import merges into (adds to) the current GF, same as the "Set ... as GF" buttons.
- **Copy PADB Filter** generates a best-effort `'Serial Number' NOT IN {...}`-style expression for pasting into PADB's own filter box. Flagged as under development — the generated expression may not exactly match PADB's own filter syntax in every case.
- **Where it applies:** `summary` visually flags (Focus/Inspect mode: shows only; Exclude mode: dims) conditions whose pre-aggregated data includes a GF-flagged DUT. `env_coverage` recomputes Room and ΔEnv tolerance-interval stats directly from the GF-filtered DUT population (see **Real bugs found on a real complex multi-analytic pod** in `CLAUDE.md` — Room and delta now always share the same DUT population when Serial/GF filtering is active; port selection alone never shrinks either).

---

## Segment-by Tab-Through

Present in all 6 V2-relevant views (`scatter`, `boxplot`/`stat_boxplot`, `stat_summary`, `summary`, `env_coverage`, and the real `distribution` view). A **"Segment by: Spec / Limit / Uncertainty"** selector plus **Prev / Next** buttons jump the frequency-range filter to each contiguous band of a frequency-varying spec — e.g. a datasheet spec that steps -100 → -94 → -88 dBc as frequency increases.

- PADB extraction has three separate limit-key pairs a pod's Type=80 analytic can select as grouping items: **Upper/Lower Limit** (selected by default), **Upper/Lower Uncertainty**, and **Upper/Lower Spec**. `Upper Limit ≈ Upper Spec − Upper Uncertainty` — Limit is the *derived*, per-unit value PADB shows by default, so it often isn't piecewise-constant across frequency; **Spec is the raw nominal value and is the one that's actually piecewise-constant**.
- **For "Segment by: Spec" or "Segment by: Uncertainty" to find anything, the pod's extraction needs `Upper Spec`/`Lower Spec` and/or `Upper Uncertainty`/`Lower Uncertainty` added as grouping items** (open the pod in PADB-R.exe, add them to the analytic's grouping, re-save). Most existing pods only have the default `Upper Limit`/`Lower Limit` — without the extra grouping items, those two selector options simply find zero segments and the Prev/Next bar stays hidden. "Segment by: Limit" always works off the always-present `Upper_Limit`/`Lower_Limit` columns.
- Segments respect whatever condition/serial/port/Global Filter/Group-by selection is currently active in that view.
- Asymmetric two-sided specs (Upper and Lower stepping at genuinely different frequencies) are handled correctly — segments break wherever *either* side changes, and each segment reports both sides' values separately (`upper: X` / `lower: Y`) rather than one ambiguous value.

---

## Group By

Present in `stat_summary`, `summary` (V2), and `env_coverage` (V2). "Condition" in these views is the *full combination* of every Group key — including per-unit-noisy keys like Limit/Uncertainty when a pod's extraction includes them as grouping items (for Segment-by, above). A pod with 5 real SpurTypes but per-unit Limit variation can fragment into 150+ near-duplicate legend entries that differ only in Limit/Uncertainty digits.

**Group by** collapses on a single chosen dimension (e.g. "SpurType" alone) instead of the full combination — confirmed on real data: 151 fragmented conditions → 5 real SpurTypes, with matching, correctly-pooled statistics.

- **Exact aggregates** (mean, min, max) are always exact when grouped, since a given DUT's data falls under exactly one constituent condition per single dimension. `env_coverage` is fully exact even for UDE/LDE/TTU/TTL, since it recomputes those directly from pooled raw per-DUT data every time.
- **Approximate aggregates** (NP-TI/spec/Shapiro normality) use a worst-case (tightest) value across the constituent conditions' own pre-computed values rather than a true recompute — `stat_summary`'s Shapiro result renders as "Non-normal" for a pooled group as a visible cue that it's not a real Shapiro test.
- `env_coverage`'s "Show excluded" checkbox has no effect while Group By is active (it compares by object identity, which doesn't apply to synthetic pooled conditions).

---

## In-Page Help Panel

A collapsible ⓘ **Help** button is available on all 6 main views (`scatter`, `boxplot`, `stat_summary`, the real `distribution` view, `env_coverage`, `summary`). It has two parts:

1. A static explanation of what the Filter dropdowns / Group by / Segment by controls actually do, and how an unfiltered dimension can pool into extra segments/legend entries.
2. A **dynamic check** that flags rows where `Upper_Limit < Lower_Limit` or `Spec_Hi < Spec_Lo` (backwards from the usual convention) and names which filter-dimension value(s) the inverted rows are concentrated in. This is the fastest way to explain an unexpectedly high segment/condition count — a real case found this way: a "14 segments where ~7 were expected" report traced to a single Serial Number with inverted spec rows across its entire dataset, a pod data-entry issue, not a tool bug.

Run `padb_csv_check.py` (below) *before* building plots to catch the same class of issue earlier, from the command line, without opening any HTML yet.

---

### `de_summary`

**Source:** Type=60 Environmental CSV (pre-aggregated by PADB)  
**What it shows:** The environmental contribution band [−LDE, +UDE] centred at zero vs frequency. Dotted lines show the estimated Total Tolerance Limit (TTL). One shaded band per PADB condition group. Spec limits shown as horizontal dashed red lines.

**Important:** The Y axis represents the **environmental contribution to measurement uncertainty**, not absolute measurement levels. UDE and LDE are the upper and lower delta-environmental values that characterise how much the environment shifts the measurement. TTL represents the total estimated tolerance including both standard and environmental contributions.

**Interactive controls:**
- **Condition filter dropdowns** — one per varying condition dimension
- **Show excluded** — display conditions excluded by the condition filter as dim grey UDE/LDE bands in the background.
- **Frequency sliders** — min/max zoom on the X axis
- **Log X toggle**
- **Statistics Table toggle** — scrollable table showing per-condition × per-frequency: UDE, LDE, Min(Env.), Max(Env.), Mean(Env.), TTL↑, TTL↓, Spec Lo, Spec Hi. Rows where TTL exceeds spec are highlighted red.
- **CSV export**

**Note:** Serial number filtering is not available for `de_summary`. The Environmental CSV is pre-aggregated across all DUTs by PADB before being written; no per-DUT rows are present.

**Best for:** Assessing whether the measurement environment (temperature, humidity, etc.) introduces uncertainty that is comparable to or exceeds the spec margin.

---

## Understanding the Statistics

### Tolerance Interval (TI)

A (P, C) tolerance interval is a calculated interval that, with confidence C, captures at least fraction P of the population. For example, a (90%, 90%) TI says: "We are 90% confident this interval contains at least 90% of the population."

- Wider than a confidence interval on the mean.
- Requires a distributional assumption (Gaussian TI) or order statistics (non-parametric TI).
- More conservative — and more meaningful for product compliance — than showing ±3σ or ±2σ bounds.

### Non-Parametric TI (NP TI)

Derived from order statistics (sorted ranks) rather than assuming a Gaussian distribution. More conservative than parametric TI for small n, but makes no distributional assumption. Displayed when n is sufficient for the requested (P, C) level.

### UDE / LDE (Environmental Delta)

From a PADB Type=60 Environmental analytic:
- **UDE** (Upper Delta Environmental): the maximum upward shift in measurement value attributable to the environment.
- **LDE** (Lower Delta Environmental): the maximum downward shift.
- Together they define the environmental contribution band: `[−LDE, +UDE]` centred at zero.
- A negative UDE or LDE in the data indicates the environmental shift is in the opposite direction from the convention; `de_summary` plots the signed values directly.

### TTL (Total Tolerance Limit)

PADB's estimated bound on the total measurement uncertainty, combining standard uncertainty (from repeat measurements) and environmental uncertainty (from environmental delta). If TTL exceeds the spec limit, the measurement cannot be guaranteed to pass in all environmental conditions.

### UDE (Max)

A scalar value reported by PADB representing the maximum UDE across all frequencies for a condition group. PADB sets this to 2,147,483,647 (INT_MAX) when the computation fails (e.g., insufficient data or a degenerate case). The tool clamps these to `null` and excludes them from statistics.

---

## index.html — Results Gallery

Open `results\index.html` in any browser. Works from a network share — no server required.

- **Run Info card** — pod file name, run timestamp, results path
- **Extraction Overrides card** — the `subex` values used, documenting what was extracted
- **Analytics card** — all analytics found in the pod, with a checkmark for each CSV collected
- **Interactive Plots** — each secondary plot embedded as an iframe; click **Open full-screen** for full interactivity
- **Downloads** — links to PDF reports, raw CSVs, `run.log`, and `_run.pod`

---

## Common Workflows

### First run on a new pod

1. Inspect the pod — identify analytics, their types, and output filenames.
2. Write `job.json` with `subex` for the desired serials and dates.
3. Run `--dry-run` to verify the switch file is built correctly.
4. Full run. Check `results\padb\` for the CSVs that were produced.
5. Add `secondary_plots` entries to `job.json` referencing the actual CSV names found.
6. Run `--plots-only` to generate HTML.
7. Review `results\index.html`.
8. Publish.

### Iterate on plots without re-extracting data

```
py "C:\apps\padb\tools\padb_run.py" job.json --plots-only
```

Edit `secondary_plots`, re-run. Takes seconds.

### Add a new serial to an existing run

Add the serial to the `serial_nums` list and re-run (full run, not `--plots-only`).
If using raw `subex` instead, update `TestRun_SerialNum` with the full quoted list.

### Compare two lots or date ranges

Create two analysis folders with separate `job.json` files. Publish to different destination subfolders. Both results are accessible independently.

### Pod returns no data

Add `"TestRun_RunStatus": "{All}"` to `subex`. The PADB default filters to passing test runs only; this override includes all.

### CSV not found after a PADB run

1. Check `results\padb\` — is the file there with a slightly different name?
2. Check `padb_run_*.log` in the results directory for the full run output and any errors.
3. Check `run.log` for PADB-R.exe return code.
4. Open the pod and confirm `OutputConfig_OutputCSV=True` for that analytic.
5. If found but name doesn't match `csv` substring, switch to `csv_file` with the exact filename.

---

## Generating job.json Files (padb_make_job.py)

For the common case — one job.json per `.pod`, following this project's standard template — `padb_make_job.py` writes it for you instead of hand-copying an existing job.json:

```
py padb_make_job.py MyMeasurement.pod --module MyModule
py padb_make_job.py pod1.pod pod2.pod pod3.pod --module MyModule --min-date "8 weeks ago" --max-date today
py padb_make_job.py MyMeasurement.pod --no-publish
py padb_make_job.py MyMeasurement.pod --module MyModule --force
```

Accepts one or more `.pod` paths and writes a `<pod_stem>_job.json` next to each.

| Flag | Effect |
|---|---|
| `--module NAME` | Subfolder under `--publish-root` for `publish.destination`. **Required unless `--no-publish` is given** — not auto-derived from the pod filename, so a new pod family never silently lands in the wrong network folder. |
| `--mode` | `legacy` / `simple` / `interactive`. Default `simple`. |
| `--min-date`, `--max-date` | Written into `subex` verbatim, including relative-date sentinels (`"today"`, `"8 weeks ago"`) — see **subex → Relative-date values** above. Omit both and no `subex` key is written at all, leaving the pod's own baked-in `[Extract]` date range exactly as-is. |
| `--no-publish` | Omit the `publish` key entirely — local results only. |
| `--force` | Overwrite an existing job.json (default: skip if the target already exists, so a manually-tuned job.json is never clobbered). |
| `--padb-exe`, `--output-dir`, `--logs-dir`, `--publish-root` | Override the defaults for `padb_exe` / `padb_output_dir` / `padb_logs_dir` / the publish-root prefix, if yours differ from this machine's. |

---

## Scheduling Overnight Runs

Use `padb_scheduler.py` to manage Windows Task Scheduler entries for job files.

```
py "C:\apps\padb\tools\padb_scheduler.py"
```

The tool scans a directory for `*_job.json` files and shows a table with scheduled/unscheduled status. Double-click a row (or click **Add / Edit Schedule**) to configure a Weekly or Daily schedule with day-of-week selection and a 24-hour start time.

Tasks run as the **current Windows user** so they can reach the NAS publish destination (`\\srsnas01...`).

Each task is named `PADB_{job_stem}` in Task Scheduler (e.g. `PADB_amplitude_job`).

**Run log files** — every run writes a timestamped `padb_run_YYYYMMDD_HHMMSS.log` to the results directory. This is the primary diagnostic for overnight failures. Check it first if a run produces unexpected output or fails silently.

**Test Run Now** — the schedule dialog includes a button to launch the job immediately in a new console window without creating a Task Scheduler entry.

---

## Web App (webapp/padb_web.py)

A local Flask app — local use only, not meant to be reachable beyond `127.0.0.1` — that wraps the CLI scripts above in a browser UI:

```
py "C:\apps\padb\tools\webapp\padb_web.py"
```

Opens `http://127.0.0.1:5000` in the default browser. Every route shells out to the same CLI scripts documented in this guide, or imports their functions directly — nothing about the underlying tools changed to build this.

- **Drop a `.pod` file** (drag-and-drop or click to choose) → parses and previews its analytics → fill in mode/module/dates → **Generate Job** calls `padb_make_job.py` or `padb_make_v2_job.py` exactly as the CLI would, showing the generated job.json and any `NOTE:`/`WARNING:` output verbatim.
- **Execute job(s)** — a jobs table with checkboxes and **Run Selected**. A single background worker + queue serializes every PADB-R.exe launch, since two concurrent instances interfere with and stall each other (see **Cross-Process PADB-R.exe Exclusivity**, below). Run jobs execute before plot jobs in a mixed selection, and a queued or running job can be **aborted**. For a V2 `*_run_job.json`, once extraction succeeds the worker automatically runs every sibling `*_v2_job.json` plot job in turn — the full V2 flow in one click.
- **Schedule/unschedule job(s)** — calls the same `padb_scheduler.py` functions the desktop Scheduler GUI uses, so a task created from either place is indistinguishable to the other.
- **Convert pod/job between sites** — calls `padb_convert_site.py`'s functions directly (see **Converting Between Database Sites** below).
- **Compare two datasets** — collapsed by default (a corner case, mainly useful the first time a new site comes online). Pick two already-extracted CSVs (auto-discovered from every `*_results/padb/*.csv` on disk), name each site, pick the primary, and **Create & Run** in one step — builds a `compare_csv` job.json (local-only by default) and queues it immediately. Validates first: a measurement-unit mismatch (e.g. dBc vs. dBm) blocks with an explanation and an explicit override to proceed anyway; softer gaps (missing temperatures/ports, non-overlapping frequency ranges) only warn. See **Cross-Site Comparison** below for what the resulting job actually does.
- The jobs table's **Kind** column distinguishes `run` jobs (have a `pod` key, runnable via `padb_run.py`) from `plot` jobs (have `csv_path`/`analytic`/`compare_csv`, only runnable via `padb_v2.py`) — use **Select All Runnable** to bulk-select only `run` jobs for an unattended batch, rather than hand-picking across a long list. **Scheduled**/**Last Run**/**Results** columns mirror the desktop Scheduler and the actual `results_dir/index.html` on disk.
- **Show tooltip help** checkbox (top of the page) — toggles hover tooltips across every section on/off; the preference is remembered across reloads (`localStorage`).

---

## Cross-Process PADB-R.exe Exclusivity

Two concurrent PADB-R.exe instances interfere with each other and both stall at zero CPU progress — this is true even across **separate process launches** (e.g. the web app restarting while a run is still in flight, or a scheduled task firing while someone is running a job from the CLI), not just within one Python process.

`padb_batch.py`'s `PADBBatch.run()` — the one choke point every invocation path goes through (webapp queue and direct CLI use of `padb_run.py` alike) — calls `wait_for_exclusive_padb_r()` before launching PADB-R.exe:
- Checks the **live OS process table** (`tasklist`), not a lock file, so a stale lock from a crashed process can never cause a false "still busy" deadlock.
- If another instance is running, polls until it clears or a timeout is reached (defaults to the job's own `padb_timeout`), printing a one-time notice so a long wait doesn't look stuck.
- If it never clears, raises an error naming the blocking PID(s) with the exact `taskkill /IM PADB-R.exe /F` command to clear a genuinely-stuck instance by hand, rather than launching a second instance and letting them silently interfere.
- `--dry-run` never reaches this check — switch-file-only runs add no delay.

---

## Adding a Custom Plot Type

Write a function in `padb_plots.py` (or your own module imported there):

```python
def my_custom_plot(csv_path: Path, cfg: dict, output_html: Path) -> None:
    import pandas as pd
    df = pd.read_csv(csv_path)
    # ... build HTML ...
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")
```

Reference it by function name in `job.json`:

```json
{"type": "my_custom_plot", "csv": "...", "title": "..."}
```

`padb_run.py` dispatches by function name via `getattr(padb_plots, plot_type)`, so no changes to `padb_run.py` are needed.

---

## The Statistics Library (padb_stats.py)

Available for use in custom scripts. All functions handle NaN and the PADB INT_MAX sentinel (±2,147,483,647) automatically.

| Function | Returns |
|---|---|
| `nonparam_tolerance_interval(data, proportion, confidence)` | `(lower, upper, warning_bool)` — two-sided NP TI |
| `onesided_tolerance_bound(data, proportion, confidence, side)` | `(bound, warning_bool)` — one-sided, `side='upper'` or `'lower'` |
| `kde(data, x_points, bandwidth)` | `(x, density)` — Gaussian KDE |
| `fit_distributions(data, distributions)` | `[{name, params, aic, bic, dist}]` — sorted by AIC |
| `best_fit_pdf(data, x_points)` | `(x, pdf, info_dict)` — best-fit PDF |
| `bootstrap_ci(data, statistic, n_boot, confidence)` | `(lower, upper, point_estimate)` — bootstrap CI |
| `band_summary(data, proportion, confidence)` | `dict` — full summary: descriptive + TI + bootstrap CI |
| `sample_size_adequacy(n, proportion, confidence)` | `(adequate, n_required, message)` |

---

## PADB Simple Mode

A direct, static replacement for the old internal Perl `PADB::Simple` tool: **no custom plotting or statistics at all.** Set `"mode": "simple"` in job.json and `padb_run.py` produces a bare gallery of PADB-R.exe's own native PNG/PDF renders — literally what the pod's analytics request, extracted and posted, nothing computed on top.

### What changes when `mode` is `"simple"`

- **Native rendering is forced on.** `make_run_pod()` sets `OutputConfig_OutputGraph=1` and `OutputConfig_GraphFormat=png,pdf` inside every `[PADBAnalyticN]` section of the run pod, regardless of what the source `.pod` currently has configured. You don't edit the pod yourself — this happens automatically, only for this mode. If those keys were already set (the common case), this is a no-op.
- **`secondary_plots`/`views` are ignored.** There is no Python-side plotting step in this mode.
- **`padb_simple.py` builds the gallery** instead of `padb_plots.py` + `make_index_html()`. One card per rendered PNG — an analytic can produce several via PADB's own pagination (a Scatter analytic with many groupings can emit a dozen or more numbered PNGs; that's normal PADB behavior, not a bug), each linked to its matching PDF.
- **The metadata table is a literal dump**, not a summary: it pulls a fixed set of fields straight from the run pod's own `[Extract]` and matching `[PADBAnalyticN]` sections — `Algorithm_AlgorithmLabel`, `Device_Device`, `Device_Family`, `Environment_TestStep`, `Environment_TestSuite`, `ExtractionOptions_TestStationLabel`, `TestRun_Max`, `TestRun_Min`, `Limits_YLimit`, every `Grouping_ItemN` present, and a synthesized "Extraction Data Date Bounds" from `Device_MinDate`/`Device_MaxDate`. Nothing here is computed — if a field is blank, the pod itself has it blank.
- **Download links** per analytic: `.pdf` (print-quality native render), `.csv` (if the analytic writes one), `.sao`, `.pod` (PADB's own run snapshot), `.txt` (PADB's tabular export).
- **`HOW_TO_USE.txt`** is written to `results_dir\` alongside `index.html` — a short, mode-specific explainer of what the output is and how to switch tiers if you need filtering/statistics instead.
- **Missing native output is visible, not silent.** If PADB didn't render a PNG for a given analytic (e.g. because a stem-matching mismatch prevented collection, or PADB itself failed to render), that analytic's card shows a plain "native graph not found" notice instead of a broken image or a card that silently disappears.

### job.json for Simple mode

Same job.json shape as any other `padb_run.py` job — just add `"mode": "simple"`:

```json
{
    "description": "SG6311A MaxPower3 — Simple mode",
    "pod": "MaxPower3.pod",
    "mode": "simple",
    "padb_exe": "C:\\Program Files\\KEYSIGHT\\PADB-R.NET\\PADB-R.exe",
    "results_dir": "maxpower3_simple_results",
    "padb_timeout": 7200,
    "run_analytics": true,
    "padb_output_dir": "C:\\Users\\damurray\\OneDrive - Keysight Technologies\\Documents\\Padb\\R-Plots",
    "padb_logs_dir": "C:\\Users\\damurray\\OneDrive - Keysight Technologies\\Documents\\Padb\\Logs"
}
```

Run it exactly like any other job:
```
py padb_run.py path\to\job.json
```
`--dry-run`, `--no-publish`, and `--plots-only` all work the same as in legacy mode — `--plots-only` rebuilds the gallery from whatever's already in `results\padb\` without re-running PADB-R.exe.

### What you won't find in Simple mode

No filters, no serial/condition exclusion, no tolerance intervals, no interactivity of any kind. If you need any of that, use `"mode": "interactive"` (V2, below) instead — the gallery's `HOW_TO_USE.txt` says the same thing.

### Known metadata gotcha across PADB versions

`ExtractionOptions_AllRunResults` was renamed to `ExtractionOptions_LastRun` in newer PADB pods (confirmed against a PADB Version 4.12.2.8 pod vs. older 3.1.2-era output). The metadata table checks both field names automatically — if you ever spot another renamed field, it's handled the same way (a small list of candidate key names per row), not a special case.

---

## V2 Pipeline

`padb_v2.py` is a lighter driver that generates all plot views from a single PADB Scatter (Type=80) CSV — no full `padb_run.py` orchestration required.

```
py padb_v2.py job_v2.json --csv path\to\Scatter.csv
```

V2 job JSON schema (all keys optional unless marked):

| Key | Description |
|---|---|
| `title_prefix` | Stem used for all output filenames and plot titles |
| `y_label` | Y-axis label for all plots |
| `x_label` | X-axis title for `scatter`/`stat_summary`/`env_coverage`/`summary`/`distribution`/`boxplot`. Default: `"Frequency (MHz)"`. Set for non-MHz x-axes (e.g. `"Frequency Offset (Hz)"` for phase noise). |
| `x_unit` | Short unit suffix used in hover text, stats table headers, CSV export headers, and filter-bar labels. Default: `"MHz"`. Set alongside `x_label` (e.g. `"Hz"`). |
| `y_lim` | `[min, max]` Y-axis range |
| `room_values` | List of Test Step strings treated as room temperature (default `["Room"]`) |
| `proportion` | TI proportion (default `0.90`) |
| `confidence` | TI confidence (default `0.90`) |
| `views` | List of views to generate: `scatter`, `stat_summary`, `boxplot`, `distribution`, `env_coverage`, `summary`. **Omit this key** to get automatic selection instead: Room-only data → `scatter` + `boxplot`; multi-temp data → all six. |
| `room_only_full_views` | `true` to also generate `summary` + `stat_summary` for Room-only data when `views` is omitted (never adds `distribution`/`env_coverage` — meaningless without non-Room data). Default `false`. |
| `results_dir` | Output folder relative to job file (default `v2_results`) |
| `publish_to` | UNC or local path to copy results to. **Omit this key entirely** to publish to the default location `\\srsnas01...\SG6311A\padb-tools-results\<results_dir>` instead. Set explicitly to `""` / `false` / `null` to opt out of publishing altogether. |

### V2 plot: `distribution`

**What it shows:** Kernel density estimate (KDE) curves for delta-from-room temperature distributions per spur type. Supports absolute and delta view modes.

**Interactive controls:**
- **View mode** — Delta (relative to Room) or Absolute values
- **Spur type filter** — individual checkboxes per spur type
- **Temperature filter** — include/exclude individual non-room temperatures
- **Serial / port filter** — exclude individual DUTs or ports
- **Segment by: Spec / Limit / Uncertainty + Prev/Next** — same mechanism as `scatter` (above); no "Group by" equivalent exists for this view.
- **Frequency sliders** — restrict to a frequency sub-range
- **Delta summary table** — per-spur-type statistics comparing temperature points
- **Help (ⓘ) panel** — see **In-Page Help Panel** below.
- **State persistence** — filter selections and frequency range are remembered across page loads

**Axis labeling:** the frequency filter's `(MHz)` label follows `x_unit` (default `"MHz"`) — set alongside `x_label` for a non-MHz x-axis.

---

### V2 plot: `summary`

**What it shows:** All-temperature summary. For each condition group: a min/max shaded band, a NP-TI band (both upper and lower — see TLL display below), and a mean line across all frequencies, covering every temperature in the dataset.

**Interactive controls:**
- **Condition filter dropdowns** — one per condition dimension found in the data (e.g. HarmonicNumber, Port, AlcState). Serial number columns are intentionally excluded — the summary pre-aggregates all DUTs per condition in Python, so no per-serial data reaches the browser.
- **Group by** — collapse the full condition combination to a single dimension when per-unit grouping items fragment it into near-duplicates. Mean/min/max are exact when pooled; NP TI (`uttl`/`lttl`) and spec are worst-case across constituent conditions. See **Group By** above.
- **Segment by: Spec / Limit / Uncertainty + Prev/Next** — jump the frequency range to each contiguous spec band; respects the current Group-by selection. See **Segment-by Tab-Through** above.
- **TLL display: Both / Upper only / Lower only** — which side(s) of the NP-TI (TTL) band to draw, and which columns the Results Table/CSV export show (see "TLL display and Data filter direction" below). Shown as a live radio selector *only* when the CSV has no `Upper_Limit`/`Lower_Limit` at all; if the CSV has a real limit, that decides the direction automatically and no selector appears.
- **Data filter: All data / Passing only / Upper limit / Lower limit** — "Upper limit"/"Lower limit" are two independent radios (not one relabeled control), each shown only when relevant to the current TLL direction. "Upper limit" hides conditions whose max data exceeds a typed-in value; "Lower limit" hides conditions whose min data falls below it.
- **Global filter (GF)** — cross-plot DUT exclusion set from the boxplot propagates here. Conditions whose pre-aggregated data includes GF-flagged DUTs are visually flagged. In Focus/Inspect mode only GF-flagged conditions are shown; in Exclude mode they are dimmed. GF matching strips both serial and temperature dimensions from the GF key before comparing against summary conditions (which aggregate both away). See **Global Filter (GF)** above.
- **Show excluded** — dim grey min/max/mean bands for conditions excluded by the filter, rendered behind the active traces. Has no effect while Group by is active.
- **Frequency sliders** — min/max zoom on the X axis.
- **Log X toggle**
- **Help (ⓘ) panel** — see **In-Page Help Panel** above.
- **Results Table** — per-condition × per-frequency stats table below the plot; columns follow the current TLL direction (TTL↑/Spec Hi/Margin↑ shown for Upper/Both, TTL↓/Spec Lo/Margin↓ for Lower/Both). CSV export mirrors the same column set.

**TLL display and Data filter direction (added 2026-08-04):** a real spec limit in the CSV always decides which side of the tolerance band matters — no ambiguity, no selector. Only when the CSV has **no** limit at all (e.g. a guaranteed-minimum-power pod with `Limits_YLimit=None`) does the direction become a genuine choice; in that case job.json's `spec_direction` sets the *default* selection (or "Both" if unset/`"auto"`), and the live selector lets you override it without regenerating. See `spec_direction` under `stat_summary` above and the CLAUDE.md write-up for the full rule (identical logic is applied to `stat_boxplot` too — see below).

**Note on serial filter:** Serial numbers are intentionally absent from the summary filter bar. The summary aggregates all DUT measurements per condition group in Python before generating the HTML — individual serial contributions are not separable in the browser. Use the boxplot or stat_summary for per-serial analysis.

**Axis labeling:** x-axis title / hover text follow `x_label` / `x_unit` (defaults `"Frequency (MHz)"` / `"MHz"`), same as `accuracy_vs_freq`.

---

### V2 plot: `env_coverage`

**What it shows:** Environmental coverage analysis. Per-frequency tolerance interval bands for the delta-from-room environmental contribution (UDE/LDE), Room measurement TI band, and Test Tolerance Upper/Lower (TTU/TTL) lines showing remaining spec margin after subtracting uncertainty. Y-axis scales to the UDE/LDE data range — TTU/TTL reference lines are rendered but do not drive the axis scale.

**Interactive controls:**
- **P / C sliders (Room and ΔEnv)** — tolerance interval probability and confidence for both the Room and delta-environmental components. k-factor table is embedded; all TI recompute live in the browser.
- **M.U. input** — measurement uncertainty (dB) subtracted from spec limits to give TTU/TTL. `TTU = Spec_hi − UDE − MU`, `TTL = Spec_lo + LDE + MU`.
- **Spec hi / Spec lo overrides** — enter explicit spec limits when the source CSV has no `Upper_Limit` / `Lower_Limit` values. Enables TTU/TTL lines that would otherwise be absent.
- **n override inputs** — override the DUT count used for k-factor lookup (useful for extrapolating to a larger population).
- **Temperature filter** — include/exclude individual non-room temperature conditions.
- **Condition filter dropdowns** — one per varying condition dimension.
- **Group by** — collapse the full condition combination to a single dimension. The only fully exact one of the three Group-by views: `computeStats()` recomputes UDE/LDE/TTU/TTL directly from pooled raw per-DUT data, so pooling introduces no approximation at all. See **Group By** above.
- **Segment by: Spec / Limit / Uncertainty + Prev/Next** — jump the frequency range to each contiguous spec band. See **Segment-by Tab-Through** above.
- **Serial / port filter** — exclude individual DUTs or ports; TI recomputes live. Room and ΔEnv TI bands are computed from the *same* serial/GF-filtered DUT population (port selection alone never shrinks either).
- **Global filter (GF)** — cross-plot DUT exclusion reacts automatically, shrinking both the Room and ΔEnv bands together. See **Global Filter (GF)** above.
- **Show excluded** — dim grey UDE/LDE bands for GF-excluded conditions. Has no effect while Group by is active.
- **Frequency sliders** — min/max zoom on the X axis.
- **Log X toggle**
- **Help (ⓘ) panel** — see **In-Page Help Panel** above.
- **Statistics table** — per-condition × per-frequency: UDE, LDE, TTU, TTL, Room μ, Room n, ΔEnv n. Rows where TTU/TTL exceeds the spec are highlighted red.
- **CSV export**

**Axis labeling:** x-axis title / hover / table / CSV header text follow `x_label` / `x_unit` (defaults `"Frequency (MHz)"` / `"MHz"`). Note `y_label` here is **not** configurable — `render_env_coverage` hardcodes `"ΔEnv (dB)"` regardless of job.json; the documented `env_coverage_y_label` key is not actually wired up.

---

## V2 Two-Step Workflow

For V2 analyses, data extraction and plot generation are separate steps:

**Step 1 — Extract from Oracle DB** (writes fresh CSVs to `padb_output_dir`):
```
py padb_run.py path\to\closein_env_v2_run_job.json
```

**Step 2 — Generate HTML plots** (reads existing CSVs, seconds to run):
```
py padb_v2.py path\to\closein_env_v2_job.json
```

The run job JSON (`*_run_job.json`) references the pod file and sets `padb_output_dir`. The plot job JSON (`*_v2_job.json`) references the CSV paths directly. The two are independent — re-run Step 2 alone whenever you tweak plot settings without needing fresh data.

**V2 run job JSON keys:**

| Key | Description |
|---|---|
| `pod` | Path to `.pod` file, relative to job file |
| `padb_exe` | Full path to PADB-R.exe |
| `results_dir` | Output folder for run logs and index |
| `padb_timeout` | Seconds before PADB-R.exe is killed |
| `subex` | `[Extract]` overrides — use `Device_MaxDate` to extend the date range |
| `run_analytics` | `true` to run PADB analytics |
| `padb_output_dir` | Directory where PADB writes CSV output files |
| `padb_logs_dir` | Directory for run log files |
| `unique_output_filenames` | `true` to force every analytic's `AnalyticName`/`OutputConfig_OutputFile` to a guaranteed-unique slug in the `_run.pod` copy — fixes pods where several analytics share one `OutputConfig_OutputFile` (a real case: 13 of 19 analytics in one pod). Not needed for well-behaved pods; `padb_make_v2_job.py` sets it automatically when it detects a collision. See `CLAUDE.md` → **`unique_output_filenames` job.json key**. |

**V2 plot job JSON additional keys:**

| Key | Description |
|---|---|
| `csv_path` | Full path to the main scatter CSV |
| `env_coverage_csv` | Full path to an alternate CSV for the env_coverage view (e.g. carrier power data) |
| `env_coverage_y_label` | Y-axis label override for env_coverage |
| `env_coverage_y_lim` | `[min, max]` Y-axis range for env_coverage |
| `env_coverage_freq_scale` | Frequency scale multiplier for env_coverage (e.g. `0.000001` converts Hz to MHz) |
| `spec_direction` | `"lo"` / `"hi"` / `"both"` / `"none"` / `"auto"` (default). A real CSV limit always overrides this. Only sets the default TLL-display selection in `summary`/`stat_boxplot` (live selector, shown only when the CSV has no limit) and the display in `stat_summary` (manual entry fallback, no selector) — needed when the pod has no `Lower Limit`/`Upper Limit` data but the measurement is one-sided. See the 4 `MaxPower3_*_v2_job.json` jobs above. |
| `force_output_csv` | `true` to force `OutputConfig_OutputCSV=1` on every Type=80 analytic in the `_run.pod` copy — fixes pods where a Scatter analytic has CSV output disabled at the pod level (a real case: a CW Closed Loop pod rendered native PNG/PDF but wrote zero CSVs). `padb_make_v2_job.py` sets it automatically when it detects this. See `CLAUDE.md` → **`force_output_csv` job.json key**. |

---

## Pre-Flight CSV Check (padb_csv_check.py)

Run this **between Step 1 and Step 2** of the V2 workflow — after extraction, before `padb_v2.py` — to catch data issues that would otherwise surface as a confusing plot rather than a clear message:

```
py padb_csv_check.py path\to\Scatter.csv
py padb_csv_check.py path\to\Scatter.csv --x-col "Amplitude (dBm)"
```

It calls the exact same CSV-loading function `padb_v2.py` itself uses (`padb_plots._load_scatter_for_stats()`), so its findings can never drift out of sync with what actually gets plotted. Checks, in order:

1. **Load success** — 0 usable rows is reported as a FAIL with the same guidance the loader's own warning gives (set `--x-col`).
2. **Orphaned numeric columns** — a numeric CSV column that isn't the detected x-axis/value/Limit column and isn't known metadata. Flagged with extra emphasis if it has *more* distinct values than the detected x-axis — the real case this catches: a pod with a genuine second swept dimension (e.g. Amplitude) sitting unused while Frequency got auto-detected as the x-axis.
3. **Raw-vs-usable row count** — the drop rate from discarding rows with no frequency/value, so a large-looking drop isn't a surprise later.
4. **Group cardinality** — warns above 100 distinct conditions (crowded legend — use Group by), and above 500 (real slowness — a 2,388-condition analytic took ~19 minutes to build boxplot/stat_summary).
5. **Grouping-item presence** — Serial, Port, Upper/Lower Limit, Upper/Lower Spec/Uncertainty (needed for the Spec/Uncertainty Segment-by options — see **Segment-by Tab-Through** above).
6. **Temperature coverage** — Room-only vs multi-temp, since that determines whether `distribution`/`env_coverage`/`summary` get built at all.

Exit code is `1` on any WARN or FAIL, so it's usable as a pre-flight gate in a script, not just an interactive read.

---

## Generating V2 Job Files (padb_make_v2_job.py)

For a new pod, `padb_make_v2_job.py` writes the entire Interactive-mode job set above automatically — the shared run job plus one plot job per Type=80 Scatter analytic:

```
py padb_make_v2_job.py MyPod.pod --module MyModule
py padb_make_v2_job.py MyPod.pod --module MyModule --spec-direction lo
py padb_make_v2_job.py MyPod.pod --module MyModule --min-date "2 months ago" --max-date today
py padb_make_v2_job.py MyPod.pod --no-publish
py padb_make_v2_job.py MyPod.pod --module MyModule --force
```

| Flag | Effect |
|---|---|
| `--module NAME` | Subfolder under `--publish-root` (default: this user's `PADB-Interactive` root, kept separate from the Simple-mode `PADB-Simple` tree). **Required unless `--no-publish`.** |
| `--spec-direction` | Applied to every generated plot job. Default `"auto"` — override if you know a measurement is one-sided despite the pod having no configured spec limits (see `spec_direction` above). Remember a real CSV limit overrides this regardless of what's set here. |
| `--min-date`, `--max-date` | Written into the run job's `subex` verbatim, including relative-date sentinels (`"today"`, `"2 months ago"`) — same mechanism as `padb_make_job.py`. Omit both to use the pod's own baked-in `[Extract]` date range. |
| `--no-publish` | Omit `publish_to` from every generated plot job. |
| `--force` | Overwrite existing job files (default: skip anything that already exists). |
| `--padb-exe`, `--output-dir`, `--logs-dir`, `--publish-root` | Override this user's `padb_config` defaults for this generation only. |

What it does and doesn't decide for you:

- **`"views"` is always omitted** — every generated plot job relies on `padb_v2.py`'s own auto-view-selection (above) to pick the right set from the actual extracted data. You never need to tell it a pod has Environmental data or is Room-only; it finds out from the CSV.
- **Every Type=80 analytic gets its own full plot job** — no attempt to pick one "primary" analytic and trim the rest to a lighter view set, unlike some hand-written job sets in this repo. Trim by hand afterward if a pod has several near-duplicate analytics and you don't want the full view set generated for all of them.
- **`csv_path` is a prediction**, not a guarantee, until the run job has actually executed once — though on every pod tested so far (including one with severe `OutputConfig_OutputFile` collisions across 19 analytics) the prediction matched the real extracted filename exactly, with `unique_output_filenames` set automatically when needed (see above). If a predicted `csv_path` turns out wrong anyway, `padb_v2.py` now has a fallback (`_resolve_csv_path()`) that searches the same directory using the same name-normalization rules `find_csvs()` uses, before failing — see `CLAUDE.md` → **CSV auto-detection fallback**.
- **`OutputConfig_OutputCSV=0` on a Type=80 analytic is auto-detected and fixed** — `force_output_csv: true` is set automatically on the run job when a Scatter analytic has CSV output disabled at the pod level, so PADB writes a CSV regardless (see `force_output_csv` above).
- **`spec_direction` can't be inferred** for a one-sided measurement with no configured pod-level spec limits — set `--spec-direction` explicitly if you know better than `"auto"`, though even then it's just the default: a live selector still lets a viewer switch when the CSV has no limit.
- **Generated file paths are checked against Windows' 260-character `MAX_PATH`** — both `padb_make_job.py` and `padb_make_v2_job.py` print a `WARNING:` with concrete next steps (shorten the module/pod name, move the pod to a shallower directory) if a generated job.json path reaches 220+ characters. See `CLAUDE.md` → **Path-length warning**.

---

## Converting Between Database Sites (padb_convert_site.py)

Some tests run against more than one PADB database — e.g. the same test pulling production data from Santa Rosa vs. a remote site's own database (AMC2/Malaysia). Comparing a real pod from each site shows the only genuine differences live in `[Extract]`: `Device_Server` and `Device_Database`. Everything else — including every `AnalyticName` and `OutputConfig_OutputFile` — is identical, which means running both site variants writes identically-named CSVs. If either is ever pointed at a shared results/publish location, one silently overwrites the other.

**Site registry** — `padb_sites.json`, next to the script:

```json
{
  "SantaRosa": { "suffix": "", "Device_Server": "PADB ORACLE SR", "Device_Database": "V2_GALLEON" },
  "AMC2":      { "suffix": "-AMC2", "Device_Server": "PADB ORACLE AMC2", "Device_Database": "GALLEON_1" }
}
```

Exactly one site must have `"suffix": ""` — that's the *primary* site (Santa Rosa), whose analytic names are the canonical, unsuffixed ones every other site's names disambiguate against. Add a new site here — no code changes — when a third location comes online.

**Converting a pod:**
```
py padb_convert_site.py --pod MyPod.pod --to AMC2
```
Detects the source site from the pod's live `Device_Server`/`Device_Database` (errors clearly, never guesses, if it matches no registered site). Writes a new pod — the source is never touched — swapping `Device_Server`/`Device_Database` to the target site's values, and appending that site's suffix to every `AnalyticName` (space-separated: `"Leveled Linear"` → `"Leveled Linear AMC2"`) and `OutputConfig_OutputFile` (underscore-separated: `"..._Linear"` → `"..._Linear_AMC2"`). Converting *to* the primary site strips a recognized suffix back off instead. Verified round-trip: Santa Rosa → AMC2 → Santa Rosa reproduces the original pod byte-for-byte (aside from `SaoFile`/`LastUpdated`).

**`.sao` files cannot be converted.** They're a binary PADB format — version-tagged, with encoded DUT serial numbers specific to the DUTs run at that site. A Santa Rosa `.sao` is meaningless against AMC2 hardware. The tool points the converted pod's `SaoFile=` at the expected new filename and prints an explicit `WARNING:` that you still need to supply a real `.sao` extracted at the target site before the pod can actually run.

**Converting a job.json:**
```
py padb_convert_site.py --job my_run_job.json --to AMC2
```
Repoints `"pod"`, and substitutes the old pod stem for the new one everywhere it appears — `results_dir`, `publish`/`publish_to`, `description`. Auto-generates the companion converted pod first if it doesn't already exist (prints when it does this). For a V2 run job (`"mode": "interactive"`), also prints a reminder to re-run `padb_make_v2_job.py` against the new pod for the plot-job side, rather than re-deriving a `csv_path` prediction a second time.

`--force` overwrites an existing output file; without it, an existing converted pod/job is left alone and skipped — same convention as every other generator in this repo. `--list-sites` prints the registry. See `CLAUDE.md` → **`padb_convert_site.py`** for the full design rationale.

---

## Cross-Site Comparison (`compare_csv`)

Compares data from two sites (e.g. Santa Rosa vs. a newly-stood-up site's early production units) without hand-merging CSVs — mainly useful once, when first standing up a new site, not an everyday tool.

**job.json keys** (V2 plot job):
```json
"compare_csv": {"SR": "path/to/sr.csv", "AMC2": "path/to/amc2.csv"},
"primary_site": "SR"
```
`compare_csv` overrides `csv_path`/`--csv` entirely (2+ site names required). `primary_site` defaults to the first key when omitted — it's the reference population for boxplot's Site Population Check (below); it has no effect on any other view.

**How it works:** each site's own scatter CSV is read as-is and tagged with `"  Site: <name>"` appended to its raw `Group` text, then concatenated into one merged CSV before the normal single-CSV pipeline runs completely unchanged. "Site" becomes a real, filterable/groupable condition dimension for free — every existing filter, Group-by, and spec-detection function already treats whatever's in `Group` text as a condition dimension. Deliberately tolerant of imperfect cross-site data: mismatched columns, one site missing spec limits entirely, or narrower temperature/port coverage than the other site are all expected, not errors.

**Coverage-gap banner** — an always-visible note above the boxplot (not a togglable panel) listing what one site has that the other doesn't, e.g. `"AMC2 has no Temperature data for: 20°C, 30°C | AMC2 has no Port data for: RF2"`, so you can decide whether a gap means the newer site's test plan needs widening. Numeric values (specs/limits/uncertainties) are compared after normalizing for formatting differences between sites (e.g. `"-100.00"` vs `"-100"` for the same value never falsely shows as a gap).

**Boxplot "Site Population Check"** — for the current filter selection, buckets every primary-site point by `(temperature, frequency)`, builds a Tukey k×IQR fence from each bucket (the same fence and live k×IQR control already used for this view's own outlier detection), then tests every non-primary-site point at that same point against it. Points with fewer than 4 primary-site samples at that exact (temp, freq) are reported `n/a`, not silently dropped — a fence isn't meaningful below that.

Three tables, in order:
1. **Per-DUT summary** — checked/outside counts, high/low direction split, max deviation distance, and how many of that DUT's outside points are *shared* with another DUT at the same (temp, freq). Includes a **suggested triage tag** (not a verdict): "Likely station/systemic" (majority shared with other DUTs — the most consequential misread if missed, since it taints every DUT from that site) beats "Likely bad DUT" (toward-failing, not shared) beats "Isolated — worth a look" beats "Below population (benign)" (away from the side that would fail spec) beats "Ambiguous" (spec is two-sided or unconfigured, direction can't be inferred).
2. **Frequency clusters** — every `(site, temp, freq)` where 2+ distinct DUTs are simultaneously outside, sorted by DUT count descending. Multiple independent DUTs failing at the identical spot is the strongest available signal for a station/fixture/calibration issue, not a bad DUT.
3. **Per-point detail** — every checked point with its verdict, direction (high/low), and distance from the fence, for drill-down.

Direction is reported relative to spec when determinable (reuses the live TLL-direction selector): for a one-sided spec, the side that moves toward failing is flagged differently from the side that can't fail spec but still indicates a real population difference (e.g. a site calibration offset).

All three tables respect the plot's current frequency window (`box_freq_lo`/`box_freq_hi`, the same range a drag-zoom already syncs to) — narrowing the view to investigate a specific cluster narrows the check to match, with a note in the summary line when the window is less than the full range.

**Webapp UI**: a collapsed-by-default "Compare two datasets" panel (see **Web App** above) lets you pick two already-extracted CSVs, name each site, pick the primary, and Create & Run in one step — no hand-written job.json needed. It validates before running: a genuine measurement-unit mismatch (e.g. dBc vs. dBm) blocks by default with an explanation, with an explicit override to proceed anyway; softer gaps (missing temps/ports, non-overlapping frequency ranges) only warn, never block.

**Scope**: only `boxplot` has the Site Population Check today. The other five V2 views render a `compare_csv`-merged dataset fine (Site is just a condition dimension to them too) but have no dedicated comparison feature yet.

---

## Limitations and Known Issues

- **No serial filter for de_summary.** The Environmental CSV is pre-aggregated across all DUTs; per-DUT data is not available in this file format.

- **No serial filter for summary (V2).** The summary pre-aggregates all DUTs per condition group in Python before generating the HTML. Individual serial contributions are not separable in the browser. Use boxplot or stat_summary for per-serial analysis.

- **stat_boxplot box statistics are from the CSV.** The box stats (Q1, Q2, Q3, whiskers) shown in the plot come from the per-frequency aggregates in the CSV. Serial filter and Y-range filter recompute from the raw per-measurement rows (`vals_detail`) but NP TI cannot be recalculated client-side; NP TI is set to null when a filter is active.

- **Tolerance intervals need adequate n.** With n=15, P90/C90 is the maximum well-supported level. The tool flags an adequacy warning automatically.

- **env_coverage TTU/TTL require spec overrides when CSV has no limits.** If `Upper_Limit` / `Lower_Limit` are null in the source CSV (common for carrier power data), TTU/TTL lines are absent until you enter values in the Spec hi / Spec lo override inputs.

- **env_coverage Y-axis excludes TTU/TTL from ranging.** TTU/TTL may be in absolute spec units (e.g. carrier power dBm) while UDE/LDE are delta values (dB). The Y-axis is always scaled to UDE/LDE/Room data only; TTU/TTL are rendered as reference lines and may extend outside the visible range.

- **PADB INT_MAX sentinel.** PADB uses ±2,147,483,647 for missing computation results (e.g., UDE (Max) when environmental computation fails). These are filtered to `null` automatically.

- **`scatter`'s spec-line rendering auto-switches between a flat line and a per-frequency mask.** When more than 3 distinct rounded `Upper_Limit`/`Lower_Limit` values exist in the data, `accuracy_vs_freq` draws a proper step-line trace following the real (frequency, limit) pairs instead of a full-width dashed line per value — correct for a PADB `Limits_YLimit=Line` (frequency-varying) spec such as a phase-noise mask or a frequency-banded dBc spec. No job.json key controls this; it's auto-detected from the data every render. If your spec is genuinely constant but happens to produce >3 distinct rounded values (e.g. many small MU-adjustment differences), it will incorrectly render as a mask — check the actual spec structure if the scatter plot's spec line looks unexpectedly stepped.

- **`env_coverage_y_label` is documented but not wired up.** `render_env_coverage` hardcodes `y_label="ΔEnv (dB)"` regardless of job.json. Fine for the intended use (ΔEnv is always the correct label for this view), but don't expect an override to take effect.

- **PADB-R.exe requires a desktop session.** It is a WinForms application and will not run in a headless SSH session.

- **Publish destination.** A simple directory copy. Requires write access to the network share. Large result sets (many large CSVs, many PDFs) may be slow.

- **Site Population Check compares only exact-matching frequencies between sites.** It does not attempt to match frequencies that are close-but-not-identical between two sites' sweeps (a real, confirmed case exists: one site's sweep had 335 distinct frequency points vs. the other's 334, one of which didn't line up). A point at a site-unique frequency simply reports `n/a`, not a false match.

- **`"mode": "interactive"` does not invoke V2 automatically.** It's a label plus a printed hint after extraction — you still run `padb_v2.py` yourself as a second command (see **V2 Two-Step Workflow**). V2's job.json schema is structurally different from `padb_run.py`'s (different `results_dir`/CSV semantics), and `generate_report()` takes one CSV per call while a `padb_run.py` job's analytics list can produce several — wiring this into one command was a deliberate scope cut, not a missed feature.
