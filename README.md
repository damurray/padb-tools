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
| `maxpower2_job.json` | Max power (superseded — known issues, see `CLAUDE.md`) |
| `maxpower3_run_job.json` | Max power V3 — extract step (V2 pipeline) |
| `maxpower3_leveled_log_job.json` | Max power V3 — leveled, log X, scatter only |
| `maxpower3_unleveled_log_job.json` | Max power V3 — unleveled, log X, scatter only |
| `maxpower3_leveled_linear_job.json` | Max power V3 — leveled, linear X, full view set |
| `maxpower3_unleveled_linear_job.json` | Max power V3 — unleveled, linear X |
| `vswr_v2_job.json` | VSWR (Output Attenuator Cal NA) — Room-only, V2 |
| `return_loss_v2_job.json` | Return Loss (Output Attenuator Cal NA) — Room-only, V2 |
| `phase_noise_de_v2_job.json` | Absolute Phase Noise EP6 EFC (DE) — multi-temp, V2, Frequency Offset (Hz) x-axis |
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
| `stat_summary` | Type=80 Scatter | Condition filter, freq sliders, serial filter, TI/NP-TI toggle, show points, stats table, log X, CSV export |
| `stat_boxplot` | Type=80 Scatter | Condition/temp filter, serial filter, Y-range filter, show points, stats table, log X, CSV export |
| `de_summary` | Type=60 Environmental | Condition filter, show excluded, freq sliders, stats table, log X, CSV export |

**V2 pipeline** (`padb_v2.py`) generates all views from a single scatter CSV using a two-step workflow: `padb_run.py` extracts from the database → `padb_v2.py` builds the HTML.

Omit `"views"` from job.json to get automatic, data-driven view selection: Room-only data defaults to `scatter` + `boxplot`; multi-temp data defaults to all six. Add `"room_only_full_views": true` to also get `summary` + `stat_summary` on Room-only data (never `distribution`/`env_coverage` — those need non-Room data to be meaningful). See `CLAUDE.md` → **Auto view-selection**.

| Type | Interactive controls |
|---|---|
| `scatter` (V2) | Condition/serial/port filter, temp filter, freq sliders, log X, GF |
| `stat_summary` (V2) | Condition/serial filter, TI/NP-TI toggle, show points/excluded, freq sliders, log X, stats table, CSV, GF |
| `boxplot` (V2) | Condition/temp/serial/port filter, Y-range, show points, outlier panel, GF, set-as-GF |
| `distribution` (V2) | Spur type/temp/serial/port filter, delta vs absolute mode, freq sliders, delta summary table, state persistence |
| `env_coverage` (V2) | P/C/MU/spec-override inputs, serial/port/temp filter, freq sliders, log X, stats table, CSV, GF |
| `summary` (V2) | Condition filter (no serial — pre-aggregated), show excluded, freq sliders, log X, GF |

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

**Default publish location:** V2 jobs (`padb_v2.py`) with no `publish_to` key at all publish to `\\srsnas01...\SG6311A\padb-tools-results\<results_dir>` automatically. Set `"publish_to": ""` (or `false`/`null`) to opt out, or to a real path to publish somewhere specific.
