# PADB Tools — New Pod Quick Start

Everything needed to go from a `.pod` file to published interactive HTML results.

---

## 1  One-time setup

```
py -m pip install pandas numpy matplotlib scipy plotly
```

PADB-R.NET must be installed at `C:\Program Files\KEYSIGHT\PADB-R.NET\PADB-R.exe`.

---

## 2  Inspect the pod — find analytic names and types

Open the `.pod` file in a text editor. Each analytic block looks like:

```
[Analytic_1]
AnalyticName=Amplitude Accuracy Ref Scatter Order by Test Step
AnalyticType=80
OutputConfig_OutputFile=Amplitude_Accuracy_Ref_Scatter_Order_by_Test_Step
...
```

Note for each analytic:
- **AnalyticName** — used to match the `csv` key in job.json
- **AnalyticType** — determines which secondary_plot `type` to use (see table below)
- **OutputConfig_OutputFile** — the base filename PADB writes (used for `csv_file` key)

### Analytic type → plot type mapping

| AnalyticType | Description | Recommended plot types |
|---|---|---|
| **80** | Scatter (raw per-measurement) | `accuracy_vs_freq`, `distribution`, `stat_summary`, `stat_boxplot` |
| **60** | Environmental / DeltaEnv (pre-aggregated) | `de_summary` |
| **90** | SummaryPlot (may or may not write CSV) | `accuracy_vs_freq` if CSV exists |
| **20** | BoxPlot | *(no CSV loader — use scatter analytic instead)* |

### Does the analytic write a CSV?

Run with `--dry-run` first, then a full run, then check `results\padb\` for CSV files. If a CSV is missing it means:
- The analytic type doesn't produce CSV output (Type=20, some Type=90)
- Or the pod needs `OutputConfig_OutputCSV=True` set

If a CSV lands in `results\padb\` with an unexpected filename, use `csv_file` (exact filename) instead of `csv` (substring match).

---

## 3  Create job.json

```json
{
    "description": "What this run is",
    "pod": "MyMeasurement.pod",
    "padb_exe": "C:\\Program Files\\KEYSIGHT\\PADB-R.NET\\PADB-R.exe",
    "results_dir": "results",
    "padb_timeout": 7200,

    "subex": {
        "TestRun_SerialNum": "'US65080401','US65080415','US65080427'",
        "Device_MinDate": "2026-06-01",
        "Device_MaxDate": "2026-06-30"
    },

    "run_analytics": true,

    "secondary_plots": [
        {
            "type": "accuracy_vs_freq",
            "csv":  "Amplitude Accuracy Ref Scatter Order by Test Step",
            "title": "Amplitude Accuracy vs Frequency",
            "y_label": "Accuracy (dB)",
            "y_lim": [-0.5, 0.5]
        },
        {
            "type": "stat_summary",
            "csv_file": "Lev_Acc_Closed_Loop_YIG_8MHz_20000MHz.csv",
            "title": "Level Accuracy — Statistical Summary",
            "y_label": "Level Error (dB)",
            "y_lim": [-0.25, 0.25],
            "proportion": 0.90,
            "confidence": 0.90
        },
        {
            "type": "de_summary",
            "csv": "Lev Acc Rel Closed Loop YIG 8-20000MHz",
            "title": "Level Accuracy — Environmental Delta",
            "y_label": "Delta (dB)",
            "y_lim": [-0.35, 0.35]
        }
    ],

    "publish": {
        "destination": "\\\\srsnas01.srs.is.keysight.com\\prod\\MIDRF3\\SG6311A\\MyAnalysis"
    }
}
```

### Key subex notes

| Situation | Fix |
|---|---|
| PADB returns no data | Add `"TestRun_RunStatus": "{All}"` to subex — some pods default to passing runs only |
| Need all dates | Set `Device_MinDate` far in the past, `Device_MaxDate` far in the future |

### csv vs csv_file

| Key | Match rule | Use when |
|---|---|---|
| `csv` | Substring of analytic name (case-sensitive) | Analytic name uniquely identifies the CSV |
| `csv_file` | Exact filename in `results\padb\` | Two analytics share the same output file, or the name contains characters that make substring matching ambiguous |

---

## 4  Run

```
py "C:\apps\padb\tools\padb_run.py" job.json            # full run
py "C:\apps\padb\tools\padb_run.py" job.json --dry-run  # build switch file only
py "C:\apps\padb\tools\padb_run.py" job.json --no-publish
py "C:\apps\padb\tools\padb_run.py" job.json --plots-only  # redo plots, no PADB
```

`--plots-only` is the fastest iteration loop — edit `secondary_plots` in job.json and rerun in seconds.

---

## 5  Open results

```
results\index.html
```

Gallery page with embedded interactive plots, PDF links, CSV downloads, run log. Works directly from a network share — no server required.

Each run also writes a `padb_run_YYYYMMDD_HHMMSS.log` to the results folder — the full console output. Check this first if something looks wrong.

---

## 6  Schedule overnight runs

```
py "C:\apps\padb\tools\padb_scheduler.py"
```

Opens a GUI that reads `*_job.json` files from a directory and manages Windows Task Scheduler entries for each. Select a job and click **Add / Edit Schedule** to configure days and time. Tasks run as the current Windows user and can reach the NAS publish path.

Each scheduled run produces a `padb_run_YYYYMMDD_HHMMSS.log` in the results directory for post-run diagnostics.

---

## Available plot types

| type | Source analytic | Interactive controls | What it shows |
|---|---|---|---|
| `accuracy_vs_freq` | Type=80 Scatter | Group-by selector, condition filter, freq sliders, log X | Raw measurement vs frequency, one trace per serial or condition |
| `distribution` | Type=80 Scatter | — | Histogram + KDE + best-fit PDF + spec lines |
| `population_envelope` | Type=80 Scatter | — | Min/max/P5-P95/median bands + TI bounds across all serials |
| `empirical_cdf` | Type=80 Scatter | — | eCDF per serial — read pass yield directly |
| `spec_derivation` | Type=80 Scatter | — | Per-band TI + margin to spec (for datasheet derivation) |
| `stat_summary` | Type=80 Scatter | Condition filter, freq sliders, serial filter, TI/NP-TI toggle, stats table, log X, CSV export | Per-frequency statistics: mean, TI bounds, NP-TI bounds, pass/fail markers |
| `stat_boxplot` | Type=80 Scatter | Condition/temp filter, serial filter, Y-range filter, stats table, log X, CSV export | Box-and-whisker per condition × temperature with outlier hover |
| `de_summary` | Type=60 Environmental | Condition filter, freq sliders, stats table, log X, CSV export | UDE/LDE environmental contribution band + estimated TTL per condition |

---

## stat_summary parameters

| Key | Default | Description |
|---|---|---|
| `proportion` | `0.90` | Tolerance interval proportion (fraction of population) |
| `confidence` | `0.90` | Confidence that the TI captures the stated proportion |
| `y_lim` | auto | `[min, max]` for Y axis |

Sample size needed: n ≥ 29 for P90/C90. With n < 29 the tool flags an adequacy warning.

---

## de_summary notes

The Environmental CSV is pre-aggregated by PADB (one row per condition × frequency). The plot shows the **environmental contribution band** [−LDE, +UDE] centred at zero — not absolute measurement levels.

The Statistics table (toggle button below the plot) shows UDE, LDE, Min/Max/Mean(Env.), TTL bounds, and spec limits per row. Rows where estimated TTL exceeds the spec limit are highlighted red.

Serial filtering is **not available** for `de_summary` — the CSV contains no per-DUT rows.

---

## Full documentation

`C:\apps\padb\tools\PADB_Tools_Guide.md`
