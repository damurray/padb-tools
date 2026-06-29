# PADB Modern Analysis Tools — User Guide

**Tools:** `padb_run.py`, `padb_plots.py`, `padb_stats.py`  
**Location:** `C:\apps\padb\tools\`  
**Purpose:** Automate PADB extraction and analysis from a `.pod` file, generate interactive Plotly plots, publish results to a shared drive as a self-contained HTML report.

---

## Prerequisites

### Python

Python 3.10 or later. Check with:

```
py --version
```

### Packages

Install once (if not already present):

```
py -m pip install pandas numpy matplotlib scipy plotly
```

### PADB-R.exe

Must be installed at:

```
C:\Program Files\KEYSIGHT\PADB-R.NET\PADB-R.exe
```

Override the path in `job.json` if yours differs.

---

## How It Works

```
job.json  →  padb_run.py  →  PADB-R.exe  →  results\padb\  (CSVs, PDFs)
                         →  padb_plots.py →  results\plots\ (interactive HTML)
                         →  index.html   (gallery linking everything)
                         →  publish to share
```

1. `padb_run.py` reads `job.json` and the `.pod` file.
2. It creates `_run.pod` — a copy of your pod with extraction overrides (serials, dates) baked in. This is the pod actually submitted to PADB.
3. It calls PADB-R.exe via `padb_batch.py`, which extracts and runs all analytics, writing CSVs and PDFs to `results\padb\`.
4. For each entry in `secondary_plots`, it calls the matching function in `padb_plots.py` to generate a self-contained interactive HTML file in `results\plots\`.
5. It writes `results\index.html` — a single-page gallery with iframes embedding the interactive plots, plus links to all PDFs and CSVs.
6. It copies `results\` to the publish destination (network share).

---

## Project Folder Structure

Each analysis lives in its own folder. Drop a pod file there, create `job.json`, run the tool.

```
MyAnalysis\
  Amplitude_Accuracy_All_temps_062526.pod   ← PADB pod file
  job.json                                  ← run configuration
  run.bat                                   ← one-click launcher
  results\                                  ← created by the tool
    index.html                              ← main results page
    _run.pod                                ← pod used for this run (auditable)
    padb_switches.txt                       ← PADB-R switch file used
    run.log                                 ← stdout/stderr from PADB-R.exe
    padb\
      Amplitude_Accuracy_Ref_Scatter_*.csv  ← PADB CSV outputs
      Lev_Acc_*.csv
      *.pdf                                 ← PADB plot PDFs
    plots\
      Absolute_Accuracy_vs_Frequency.html   ← interactive Plotly plots
      Population_Envelope_*.html
      ...
```

The tool never needs to be copied between runs. Only `job.json` (and the `.pod` file) define a run.

---

## Running the Tool

### From the analysis folder (recommended)

```
run.bat
```

### From anywhere

```
py "C:\apps\padb\tools\padb_run.py" "C:\path\to\job.json"
```

### CLI options

| Option | Effect |
|---|---|
| *(none)* | Full run: PADB extraction + analytics + plots + publish |
| `--dry-run` | Build switch file only; do not call PADB-R.exe |
| `--no-publish` | Run PADB and plots; skip copy to share |
| `--plots-only` | Skip PADB entirely; regenerate plots from existing CSVs |

`--plots-only` is fast and useful when you change `secondary_plots` entries in `job.json` without re-extracting data.

---

## The job.json File

All configuration for a run lives in `job.json`. Paths are relative to the `job.json` file itself.

```json
{
    "description": "Human-readable label for this run",

    "pod": "MyAnalysis.pod",

    "padb_exe": "C:\\Program Files\\KEYSIGHT\\PADB-R.NET\\PADB-R.exe",

    "results_dir": "results",

    "padb_timeout": 600,

    "subex": {
        "TestRun_SerialNum": "'US65080401','US65080415'",
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

### Key descriptions

| Key | Description |
|---|---|
| `pod` | Path to the `.pod` file, relative to `job.json`. |
| `padb_exe` | Full path to PADB-R.exe. |
| `results_dir` | Output folder, relative to `job.json`. Default: `results`. |
| `padb_timeout` | Seconds before PADB-R.exe is killed. Default: `600`. |
| `subex` | Key=value overrides for `[Extract]` section of the pod. Keys must match exactly. Common overrides: `TestRun_SerialNum`, `Device_MinDate`, `Device_MaxDate`. |
| `run_analytics` | `true` to run PADB analytics (and generate CSVs/PDFs). Default: `true`. |
| `secondary_plots` | List of interactive plot configurations (see below). |
| `publish.destination` | UNC or local path to copy `results\` to after the run. Omit to skip. |

### subex key format

Values must match PADB's expected format exactly, including quotes for string lists:

```json
"subex": {
    "TestRun_SerialNum": "'US65080401','US65080415','US65080427'",
    "Device_MinDate": "2026-06-01",
    "Device_MaxDate": "2026-06-30"
}
```

The original `.pod` file is never modified. A `_run.pod` copy is created in `results\` with these values substituted before submission.

---

## Secondary Plots

Each entry in `secondary_plots` generates one self-contained interactive HTML file.

### Common keys for all plot types

| Key | Description |
|---|---|
| `type` | Plot function name (see below). |
| `csv` | Analytic name from the pod (value of `AnalyticName=` or `OutputConfig_OutputFile=`). |
| `title` | Plot title shown in the figure and index. |
| `y_label` | Y-axis label. |
| `x_label` | X-axis label. Default: `Frequency (MHz)`. |
| `y_lim` | `[min, max]` to pin the Y axis. Omit to auto-scale. |
| `log_x` | `true` to default to log X axis. A toggle button is always shown. |
| `spec_limits` | `[lower, upper]` override; used instead of limits from the CSV. |
| `proportion` | Tolerance interval proportion. Default: `0.90`. |
| `confidence` | Tolerance interval confidence. Default: `0.90`. |

### Plot types

#### `accuracy_vs_freq`

Scatter plot of the measurement value vs frequency. One trace per DUT serial number. Spec limit lines shown as horizontal dashed red lines. Hover shows frequency, serial, and value. Linear/log X toggle.

**Best for:** Raw accuracy vs frequency per unit. Spot outlier DUTs.

#### `population_envelope`

Population statistics vs frequency. Shows min-max fill, P5–P95 fill, median line, and non-parametric tolerance interval bounds. No assumption of Gaussian distribution.

**Best for:** Population-level summary with statistically defensible bounds.

#### `distribution`

Histogram + KDE (kernel density estimate) + best-fit parametric PDF (normal, lognormal, Weibull, gamma; selected by AIC). Spec limits as vertical lines.

**Best for:** Understanding the distribution shape; confirming or ruling out normality.

#### `empirical_cdf`

Empirical CDF (sorted data fraction vs value), one trace per serial. Spec limits as vertical lines.

**Best for:** Yield estimation — read off what fraction of units fall within spec.

#### `spec_derivation`

Per-frequency-band analysis: median, P5/P95, non-parametric tolerance interval (upper and lower), and margin to spec. Requires `freq_bands` in cfg. Includes sample-size adequacy warning.

**Best for:** Deriving or validating datasheet specs from a production lot. The margin bar shows how far the TI is from the spec limit — green = passing margin, red = spec is tight or violated.

Extra key required:
```json
"freq_bands": [
    ["8-100 MHz",      8,      100],
    ["100 MHz-1 GHz",  100,   1000],
    ["1-6 GHz",        1000,  6000],
    ["6-14 GHz",       6000, 14000],
    ["14-20 GHz",     14000, 20000]
]
```

#### `de_summary`

Mean environmental deviation vs frequency, one line per PADB Group. Spec limits as horizontal lines. Log/linear X toggle. Works with Environmental (Type=60) analytics.

**Best for:** Temperature compensation quality overview across groups.

#### `de_heatmap`

Heat map: Group × frequency, coloured by mean environmental deviation. Red/blue = elevated positive/negative DE. Annotated per cell.

**Best for:** Instantly spotting which group+frequency combination has the worst DE.

---

## The Statistics Library (padb_stats.py)

Available for use in custom scripts. All functions handle NaN and the PADB integer sentinel (±2,147,483,647) automatically.

| Function | Returns | Notes |
|---|---|---|
| `nonparam_tolerance_interval(data, proportion, confidence)` | `(lower, upper, warning_bool)` | Two-sided TI; no normality assumed |
| `onesided_tolerance_bound(data, proportion, confidence, side)` | `(bound, warning_bool)` | One-sided; `side='upper'` or `'lower'` |
| `kde(data, x_points, bandwidth)` | `(x, density)` | Gaussian KDE |
| `fit_distributions(data, distributions)` | `[{name, params, aic, bic, dist}]` | Fits normal, lognormal, Weibull, gamma; sorted by AIC |
| `best_fit_pdf(data, x_points)` | `(x, pdf, info_dict)` | Best-fit PDF at x_points |
| `bootstrap_ci(data, statistic, n_boot, confidence)` | `(lower, upper, point_estimate)` | Bootstrap CI on any statistic |
| `band_summary(data, proportion, confidence)` | `dict` | Full summary: descriptive + TI + bootstrap CI |
| `sample_size_adequacy(n, proportion, confidence)` | `(adequate, n_required, message)` | n=15 adequate for P90/C90 (need 29); inadequate for P99/C95 (need 299) |

### Sample size guidance

For P90/C90 tolerance interval: need **n ≥ 29**.  
For P95/C90: need **n ≥ 59**.  
For P99/C95: need **n ≥ 299**.

With n=15 (a common early-production lot size), use P90/C90 and treat the interval as indicative. The tool will flag an adequacy warning automatically.

---

## Interpreting the Results

### index.html

Open `results\index.html` in a browser (works from a network share — no server needed).

- **Run Info card**: pod file, timestamp, results directory.
- **Extraction Overrides card**: the subex values used — documents exactly what was extracted.
- **Analytics card**: all analytics from the pod, with a checkmark for each CSV that was found.
- **Interactive Plots section**: each secondary plot embedded as an iframe. Click "Open full-screen" to open the plot in its own tab for full interactivity.
- **Downloads section**: links to PADB PDF reports, CSV data files, `run.log`, and `_run.pod`.

### Interactive plot features

All plots support:
- **Zoom**: scroll wheel or drag to select a region.
- **Pan**: click and drag (after zooming).
- **Hover**: mouse over data points for exact values.
- **Legend toggle**: click a legend entry to show/hide that trace.
- **Linear/log X toggle**: button in the top-left (frequency-based plots).

### run.log

Contains the full stdout and stderr from PADB-R.exe. Check this first if PADB fails or produces unexpected results.

### _run.pod

The exact pod file submitted to PADB for this run. Useful for auditing: you can re-open it in PADB to see what settings were active, or re-run it directly.

---

## Starting a New Analysis

1. Create a folder for the analysis, e.g.:
   ```
   C:\Users\yourname\Padb\MyMeasurement\
   ```

2. Copy your `.pod` file into the folder.

3. Copy `job.json` and `run.bat` from an existing analysis (or the examples in `C:\apps\padb\tools\`).

4. Edit `job.json`:
   - Update `description`, `pod`.
   - Update `subex` with the serials and date range you want.
   - Update `secondary_plots` to match the analytic names in your pod and the plot types you need.
   - Update `publish.destination` or remove it to skip publishing.

5. Run:
   ```
   run.bat
   ```

6. Open `results\index.html` to review.

---

## Common Workflows

### Run with a subset of serial numbers

Edit `subex.TestRun_SerialNum` in `job.json`:

```json
"TestRun_SerialNum": "'US65080401','US65080415'"
```

Run `run.bat`. A new `_run.pod` is created; PADB uses it.

### Change the date range

Edit `subex.Device_MinDate` and `Device_MaxDate`. Re-run.

### Iterate on plots without re-extracting data

Edit `secondary_plots` in `job.json`, then:

```
run.bat --plots-only
```

Takes seconds — PADB is not re-run.

### Comparing two date ranges or serial sets

Create two analysis folders with separate `job.json` files. Run each independently. Publish to different destination subfolders.

### Add a custom plot type

Write a Python function in your own script:

```python
def my_custom_plot(csv_path, cfg, output_html):
    import pandas as pd
    import plotly.graph_objects as go
    df = pd.read_csv(csv_path)
    fig = go.Figure(...)
    fig.write_html(str(output_html), include_plotlyjs=True)
```

Add it to `padb_plots.py`, then reference it by function name in `job.json`:

```json
{"type": "my_custom_plot", "csv": "...", "title": "..."}
```

### Re-run a past analysis

Navigate to the analysis folder and run `run.bat`. The `job.json` is the complete record. Results are overwritten.

---

## Limitations and Caveats

- **PADB-R.exe required.** The tool calls PADB-R.exe directly. Every engineer's PC needs PADB-R.NET installed.

- **Tolerance intervals need adequate n.** With n=15, P90/C90 is the maximum well-supported TI level. Larger proportions or confidences require more units.

- **Scatter CSV value column detection.** The tool identifies the value column by position (first numeric column after Frequency). If your pod has an unusual column order, verify plots against the raw CSV.

- **Environmental CSV group labels.** Long group labels are truncated to 40 characters in heatmap axes. Full labels appear in hover tooltips.

- **Publish destination.** The tool does a simple directory copy. If the destination is on a network share, the copy requires write access and may be slow for large result sets.

- **INT_SENTINEL values.** PADB uses ±2,147,483,647 to represent missing data. These are filtered out automatically before any calculation or plot.
