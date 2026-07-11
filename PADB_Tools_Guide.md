# PADB Modern Analysis Tools — User Guide

**Tools:** `padb_run.py`, `padb_v2.py`, `padb_plots.py`, `padb_stats.py`, `padb_scheduler.py`  
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
| `secondary_plots` | List of plot configurations (see below). |
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

### `accuracy_vs_freq`

**Source:** Type=80 Scatter CSV  
**What it shows:** Individual measurement values vs frequency, one trace per serial number or condition. Spec limit lines as horizontal dashed red lines.

**Interactive controls:**
- **Group by** — display by serial number, test step, or any condition dimension parsed from the Group field
- **Sort** — traces by name, worst-first, or median value
- **Condition filter dropdowns** — one per dimension (OA State, AlcState, etc.)
- **Frequency sliders** — min/max zoom on the X axis
- **Log X toggle**
- **Reset button**
- **Hover** — shows frequency, group label, and value

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
- **Serial number filter** — uncheck individual DUTs to exclude them; statistics recompute live
- **TI toggle** — show/hide parametric tolerance interval band
- **NP TI toggle** — show/hide non-parametric tolerance interval bounds (requires server-side scipy; displayed when data is sufficient)
- **Show points** — overlay individual per-DUT measurement points on each trace. Points for serials currently excluded by the serial filter are shown in grey rather than hidden, so the full population remains visible while the statistics reflect only the selected DUTs.
- **Show excluded** — display conditions currently excluded by the condition filter as dim grey traces in the background, so you can compare filtered and unfiltered populations without switching the filter off.
- **Frequency sliders** — min/max zoom on the X axis
- **Log X toggle**
- **Statistics Table toggle** — opens a scrollable table below the plot showing per-condition, per-frequency: n, mean, σ, Q1, Q2, Q3, normality (Shapiro-Wilk W), NP TI bounds, outliers with serial numbers
- **CSV export** — downloads a CSV of all visible data

**Best for:** Primary statistical deliverable for a measurement characterisation. Captures both the population spread and the statistical confidence bounds.

**Key parameters:**
- `proportion` — fraction of the population the TI must capture (default 0.90)
- `confidence` — confidence level that the stated proportion is captured (default 0.90)
- Required n: 29 for P90/C90, 59 for P95/C90, 299 for P99/C95

---

### `stat_boxplot`

**Source:** Type=80 Scatter CSV  
**What it shows:** Box-and-whisker plots per condition, grouped by temperature condition. Box = Q1–Q3, whisker = 1.5×IQR, dots = outliers. Normality colour coding (green = Shapiro-Wilk p ≥ 0.05, red = non-normal).

**Interactive controls:**
- **Condition filter dropdowns** — one per non-temperature condition dimension
- **Temperature filter** — show/hide individual temperature conditions
- **Serial number filter** — uncheck individual DUTs; box statistics recompute live
- **Y-range filter** — All data / Passing only / Custom min–max
- **Show points** — overlay individual per-DUT measurement points on each box trace (size 5, semi-transparent). Points for serials excluded by the serial filter are shown in grey; outliers remain as open-circle markers for visual distinction. Hovering a scatter point shows the serial number and value.
- **Log X toggle**
- **Statistics Table toggle** — scrollable table below the plot showing per-condition, per-frequency: n, mean, σ, Q1, Q2, Q3, normality, NP TI bounds, outliers with serial numbers
- **CSV export**
- **Outlier hover** — shows value and serial number of each outlier point

**Best for:** Comparing spread across temperature conditions. Identifying which condition drives the worst-case. Outlier identification with serial traceability.

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
| `y_lim` | `[min, max]` Y-axis range |
| `room_values` | List of Test Step strings treated as room temperature (default `["Room"]`) |
| `proportion` | TI proportion (default `0.90`) |
| `confidence` | TI confidence (default `0.90`) |
| `views` | List of views to generate: `scatter`, `stat_summary`, `boxplot`, `distribution`, `env_coverage`, `summary` |
| `results_dir` | Output folder relative to job file (default `v2_results`) |
| `publish_to` | Optional UNC or local path to copy results to |

### V2 plot: `distribution`

**What it shows:** Kernel density estimate (KDE) curves for delta-from-room temperature distributions per spur type. Supports absolute and delta view modes.

**Interactive controls:**
- **View mode** — Delta (relative to Room) or Absolute values
- **Spur type filter** — individual checkboxes per spur type
- **Temperature filter** — include/exclude individual non-room temperatures
- **Serial / port filter** — exclude individual DUTs or ports
- **Frequency sliders** — restrict to a frequency sub-range
- **Delta summary table** — per-spur-type statistics comparing temperature points
- **State persistence** — filter selections and frequency range are remembered across page loads

---

### V2 plot: `summary`

**What it shows:** All-temperature summary. For each condition group: a min/max shaded band, a NP-TI band, and a mean line across all frequencies, covering every temperature in the dataset.

**Interactive controls:**
- **Condition filter dropdowns** — one per condition dimension found in the data (e.g. HarmonicNumber, Port, AlcState). Serial number columns are intentionally excluded — the summary pre-aggregates all DUTs per condition in Python, so no per-serial data reaches the browser.
- **Global filter (GF)** — cross-plot DUT exclusion set from the boxplot propagates here. Conditions whose pre-aggregated data includes GF-flagged DUTs are visually flagged. In Focus/Inspect mode only GF-flagged conditions are shown; in Exclude mode they are dimmed. GF matching strips both serial and temperature dimensions from the GF key before comparing against summary conditions (which aggregate both away).
- **Show excluded** — dim grey min/max/mean bands for conditions excluded by the filter, rendered behind the active traces.
- **Frequency sliders** — min/max zoom on the X axis.
- **Log X toggle**

**Note on serial filter:** Serial numbers are intentionally absent from the summary filter bar. The summary aggregates all DUT measurements per condition group in Python before generating the HTML — individual serial contributions are not separable in the browser. Use the boxplot or stat_summary for per-serial analysis.

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
- **Serial / port filter** — exclude individual DUTs or ports; TI recomputes live.
- **Global filter (GF)** — cross-plot DUT exclusion reacts automatically.
- **Show excluded** — dim grey UDE/LDE bands for GF-excluded conditions.
- **Frequency sliders** — min/max zoom on the X axis.
- **Log X toggle**
- **Statistics table** — per-condition × per-frequency: UDE, LDE, TTU, TTL, Room μ, Room n, ΔEnv n. Rows where TTU/TTL exceeds the spec are highlighted red.
- **CSV export**

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

**V2 plot job JSON additional keys:**

| Key | Description |
|---|---|
| `csv_path` | Full path to the main scatter CSV |
| `env_coverage_csv` | Full path to an alternate CSV for the env_coverage view (e.g. carrier power data) |
| `env_coverage_y_label` | Y-axis label override for env_coverage |
| `env_coverage_y_lim` | `[min, max]` Y-axis range for env_coverage |
| `env_coverage_freq_scale` | Frequency scale multiplier for env_coverage (e.g. `0.000001` converts Hz to MHz) |

---

## Limitations and Known Issues

- **No serial filter for de_summary.** The Environmental CSV is pre-aggregated across all DUTs; per-DUT data is not available in this file format.

- **No serial filter for summary (V2).** The summary pre-aggregates all DUTs per condition group in Python before generating the HTML. Individual serial contributions are not separable in the browser. Use boxplot or stat_summary for per-serial analysis.

- **stat_boxplot box statistics are from the CSV.** The box stats (Q1, Q2, Q3, whiskers) shown in the plot come from the per-frequency aggregates in the CSV. Serial filter and Y-range filter recompute from the raw per-measurement rows (`vals_detail`) but NP TI cannot be recalculated client-side; NP TI is set to null when a filter is active.

- **Tolerance intervals need adequate n.** With n=15, P90/C90 is the maximum well-supported level. The tool flags an adequacy warning automatically.

- **env_coverage TTU/TTL require spec overrides when CSV has no limits.** If `Upper_Limit` / `Lower_Limit` are null in the source CSV (common for carrier power data), TTU/TTL lines are absent until you enter values in the Spec hi / Spec lo override inputs.

- **env_coverage Y-axis excludes TTU/TTL from ranging.** TTU/TTL may be in absolute spec units (e.g. carrier power dBm) while UDE/LDE are delta values (dB). The Y-axis is always scaled to UDE/LDE/Room data only; TTU/TTL are rendered as reference lines and may extend outside the visible range.

- **PADB INT_MAX sentinel.** PADB uses ±2,147,483,647 for missing computation results (e.g., UDE (Max) when environmental computation fails). These are filtered to `null` automatically.

- **PADB-R.exe requires a desktop session.** It is a WinForms application and will not run in a headless SSH session.

- **Publish destination.** A simple directory copy. Requires write access to the network share. Large result sets (many large CSVs, many PDFs) may be slow.
