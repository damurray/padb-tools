# padb-tools

Automates PADB-R.exe — Keysight's RF characterisation database tool — to run headlessly, collect CSV outputs, and generate self-contained interactive HTML plots for SG6311A signal generator data.

All HTML output is fully self-contained (Plotly.js embedded inline). Engineers open results directly from a Windows network share with no server required.

---

## Files

| File | Description |
|---|---|
| `padb_run.py` | V1 job runner — reads a job.json, runs PADB-R.exe, generates plots |
| `padb_v2.py` | V2 job runner — lighter driver for the new interactive plot set |
| `padb_plots.py` | Plot library — all interactive HTML plot types |
| `padb_scheduler.py` | tkinter GUI for managing Windows Task Scheduler entries |
| `padb_stats.py` | Statistical helpers (tolerance intervals, k-factors) |
| `v1.0/` | Archive of the original V1.0 scripts |

### Job files

| File | Description |
|---|---|
| `amplitude_job.json` | Amplitude accuracy — all temps |
| `harmonics_job.json` | Harmonics — all temps |
| `harmonics_v2_job.json` | Harmonics — V2 pipeline |
| `clockspurs_job.json` | Non-harmonic clock spurs |
| `linespurs_job.json` | Line-related spurs |
| `closein_job.json` | Close-in non-harmonics |
| `absphase_noise_job.json` | Absolute phase noise |
| `maxpower2_job.json` | Max power |
| `v2_probe_job.json` | V2 probe run |
| `job.json` | Scratch / template |

---

## Quick start

### Prerequisites

```
py -m pip install pandas numpy matplotlib scipy plotly
```

PADB-R.NET must be installed at `C:\Program Files\KEYSIGHT\PADB-R.NET\PADB-R.exe`.

### Run (V1)

```
py padb_run.py job.json                 # full run
py padb_run.py job.json --plots-only    # redo HTML only (fast iteration)
py padb_run.py job.json --no-publish
py padb_run.py job.json --dry-run
```

### Run (V2)

```
py padb_v2.py job.json --csv path\to\data.csv
```

### Schedule overnight runs

```
py padb_scheduler.py
```

Opens a GUI that reads `*_job.json` files from a directory and manages Windows Task Scheduler entries for each job.

---

## Plot types

| Type | Source | Interactive controls |
|---|---|---|
| `accuracy_vs_freq` | Type=80 Scatter | Group-by selector, condition filter, freq sliders, log X |
| `distribution` | Type=80 Scatter | — |
| `population_envelope` | Type=80 Scatter | — |
| `empirical_cdf` | Type=80 Scatter | — |
| `spec_derivation` | Type=80 Scatter | — |
| `stat_summary` | Type=80 Scatter | Condition filter, freq sliders (arrow-key stepping), serial filter, TI/NP-TI toggle, stats table, log X, CSV export |
| `stat_boxplot` | Type=80 Scatter | Condition/temp filter, serial filter, Y-range filter, stats table, log X, CSV export |
| `de_summary` | Type=60 Environmental | Condition filter, freq sliders (arrow-key stepping), stats table, log X, CSV export |

V2 adds a `summary` plot (all-temperature summary with Min/Max/Mean bands and TTL estimates) and an `env_coverage` plot.

---

## job.json structure

```json
{
    "description": "What this run is",
    "pod": "MyMeasurement.pod",
    "padb_exe": "C:\\Program Files\\KEYSIGHT\\PADB-R.NET\\PADB-R.exe",
    "results_dir": "results",
    "padb_timeout": 7200,
    "run_datetimes": ["06/04/2026 01:06:18 PM"],
    "serial_nums": ["US65080401", "US65080415"],
    "subex": {
        "Device_MinDate": "2026-06-01",
        "Device_MaxDate": "2026-06-30"
    },
    "run_analytics": true,
    "secondary_plots": [
        {
            "type": "stat_summary",
            "csv_file": "MyAnalytic.csv",
            "title": "Level Accuracy — Statistical Summary",
            "y_label": "Level Error (dB)",
            "y_lim": [-0.25, 0.25],
            "proportion": 0.90,
            "confidence": 0.90
        }
    ],
    "publish": {
        "destination": "\\\\server\\share\\MyAnalysis"
    }
}
```

See `Quick_Start.md` for a full walkthrough and `PADB_Tools_Guide.md` for complete documentation.

---

## Output

Results land in `results_dir/` and are published to the network share:

- `index.html` — gallery page linking all plots
- `*.html` — self-contained interactive plots
- `padb_run_YYYYMMDD_HHMMSS.log` — full console output for diagnostics
