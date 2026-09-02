# padb-tools

Automates PADB-R.exe — Keysight's RF characterisation database tool — to run headlessly, collect CSV outputs, and generate self-contained interactive HTML plots for SG6311A signal generator data.

All HTML output is fully self-contained (Plotly.js embedded inline). Engineers open results directly from a Windows network share with no server required.

---

## Files

| File | Description |
|---|---|
| `padb_run.py` | Job runner — reads a job.json, runs PADB-R.exe, generates plots. Dispatches on the `mode` key: `legacy` (V1, default), `simple`, or `interactive` (label for the V2 flow) |
| `padb_v2.py` | V2 job runner — lighter driver for the new interactive plot set |
| `padb_plots.py` | Plot library — all interactive HTML plot types (legacy/V1) |
| `padb_simple.py` | Simple mode — literal extract-and-post gallery of PADB-R's own native PNG/PDF renders, no custom plotting. See `"mode": "simple"` below |
| `padb_make_job.py` | Generates a job.json from a `.pod` file using this project's standard template — `py padb_make_job.py pod1.pod --module MiniMoab` |
| `padb_make_v2_job.py` | Generates the full Interactive/V2 job set (run job + one plot job per Type=80 analytic) from a `.pod` file — `py padb_make_v2_job.py pod1.pod --module MiniMoab` |
| `padb_config.py` | Shared per-user defaults (padb_exe, R-Plots/Logs/Data paths, publish root), optionally overridden via `padb_config.json` |
| `padb_convert_site.py` | Converts a `.pod`/job.json between PADB database sites (e.g. Santa Rosa ↔ AMC2/Malaysia) — site registry in `padb_sites.json` — `py padb_convert_site.py --pod MyPod.pod --to AMC2` |
| `padb_csv_check.py` | Pre-flight CSV sanity check — run between extraction and `padb_v2.py` to catch orphaned columns, inverted spec rows, and high Group cardinality before building plots — `py padb_csv_check.py path\to\Scatter.csv` |
| `padb_scheduler.py` | tkinter GUI for managing Windows Task Scheduler entries |
| `padb_stats.py` | Statistical helpers (tolerance intervals, k-factors) |
| `padb_batch.py` | Shared PADB-R.exe launcher used by every entry point (CLI and web app) — enforces cross-process exclusivity so two PADB-R.exe instances never run concurrently and stall each other |
| `webapp/` | Local Flask web UI (`padb_web.py` + `static/`/`templates/`) — see **Web app** below |
| `v1.0/` | Archive of the original V1.0 scripts |

### Job files

**These are reference templates, not directly runnable from a fresh clone.** The `.pod` file each one's `"pod"` key points at is not checked into this repo (job configs/pods live in the OneDrive `Data\` folder — see `CLAUDE.md` → **File locations**), and neither are the CSVs a `--plots-only` run would need. Use them as real, working examples of the job.json schema per measurement family — copy from them when onboarding a new pod — but to actually execute one, first supply the matching `.pod` alongside it.

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
py -m pip install pandas numpy matplotlib scipy plotly flask
```

`flask` is only needed for the web app (`webapp/padb_web.py`) — every other script works without it.

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

### Web app

```
py webapp\padb_web.py
```

(Or double-click **`Start_web.bat`** in the tools root — same thing, no terminal needed.)

Opens a local web UI (`http://127.0.0.1:5000`, local use only) for five workflows: drop a `.pod` file to auto-generate its job.json, select one or more job files to execute, schedule/unschedule jobs in Windows Task Scheduler, convert a pod or job between database sites, and pair two already-extracted CSVs into a cross-site comparison job (collapsed by default — a corner case, mainly useful the first time a new site comes online). PADB-R.exe runs are strictly serialized through a background queue — this also holds across separate process launches (e.g. a webapp restart) via `padb_batch.py`'s cross-process exclusivity guard, not just within one queue's lifetime. Run jobs execute before plot jobs in a mixed selection, a queued or running job can be aborted, and `"mode": "interactive"` run jobs auto-chain their sibling V2 plot jobs after extraction succeeds. The jobs table filters by mode/kind/name (with **Select All Runnable** / **Select Filtered** for bulk selection) and shows each job's actual schedule cadence, when it last ran, and a link to its results gallery; a failed job shows a **View log** link. A "Show tooltip help" checkbox toggles hover tooltips across the whole page. See `CLAUDE.md` → **Web app**, **Cross-process PADB-R.exe exclusivity guard**, and **Cross-site comparison** for details.

---

## Plot types

| Type | Source | Interactive controls |
|---|---|---|
| `accuracy_vs_freq` | Type=80 Scatter | Group-by selector, condition filter, freq sliders, log X |
| `distribution` | Type=80 Scatter | — |
| `population_envelope` | Type=80 Scatter | — |
| `empirical_cdf` | Type=80 Scatter | — |
| `spec_derivation` | Type=80 Scatter | — |
| `stat_summary` | Type=80 Scatter | Condition/Group-by filter, freq sliders, serial filter, Segment-by, TI/NP-TI toggle, show points, stats table, log X, CSV export |
| `stat_boxplot` | Type=80 Scatter | Condition/temp filter, serial filter, Y-range filter, Segment-by, GF (set/export/import/copy), show points, stats table, log X, CSV export |
| `de_summary` | Type=60 Environmental | Condition filter, show excluded, freq sliders, stats table, log X, CSV export |

**V2 pipeline** (`padb_v2.py`) generates all views from a single scatter CSV using a two-step workflow: `padb_run.py` extracts from the database → `padb_v2.py` builds the HTML.

Omit `"views"` from job.json to get automatic, data-driven view selection: Room-only data defaults to `scatter` + `boxplot`; multi-temp data defaults to all six. Add `"room_only_full_views": true` to also get `summary` + `stat_summary` on Room-only data (never `distribution`/`env_coverage` — those need non-Room data to be meaningful). See `CLAUDE.md` → **Auto view-selection**.

| Type | Interactive controls |
|---|---|
| `scatter` (V2) | Condition/serial/port filter, temp filter, Segment-by, freq sliders, log X, GF |
| `stat_summary` (V2) | Condition/Group-by/serial filter, TI/NP-TI toggle, show points/excluded, Segment-by, freq sliders, log X, stats table, CSV, GF |
| `boxplot` (V2) | Condition/temp/serial/port filter, Y-range, Segment-by, show points, outlier panel, GF (set/export/import/copy) |
| `distribution` (V2) | Spur type/temp/serial/port filter, delta vs absolute mode, Segment-by, freq sliders, delta summary table, state persistence |
| `env_coverage` (V2) | P/C/MU/spec-override inputs, serial/port/temp/Group-by filter, Segment-by, freq sliders, log X, stats table, CSV, GF |
| `summary` (V2) | Condition/Group-by filter (no serial dropdown — GF already recomputes per-DUT via embedded per-DUT means), show excluded, Segment-by, freq sliders, log X, GF |

All V2 views (plus the real `distribution`) also have an in-page ⓘ Help panel and can be sanity-checked pre-build with `padb_csv_check.py`. See `CLAUDE.md` for the "Segment by"/"Group by"/Help-panel/GF write-ups.

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

**`"mode"` key** (optional, default `"legacy"`): `"legacy"` runs the V1 plots above unchanged; `"simple"` produces a literal extract-and-post gallery of PADB-R's own native PNG/PDF renders instead (a modern `PADB::Simple` replacement — no custom plotting); `"interactive"` is a label documenting that this job feeds the V2 flow (`padb_v2.py`, run separately). See `CLAUDE.md` → **`mode` job.json key** for full details. Both `simple` and `interactive` also write `results_dir/HOW_TO_USE.txt`, a short guide to that tier's output.

**`subex` date values can be relative**, resolved to the real current date every time the job runs rather than a fixed string baked in when you wrote the job.json: `"today"`, or `"8 weeks ago"` / `"3 months ago"` / `"1 year ago"` (any integer N). See `CLAUDE.md` → **`subex` relative-date sentinels**.

**Generating job.json files**: `py padb_make_job.py MyMeasurement.pod --module MyModule` writes a job.json using this same template automatically — see `padb_make_job.py`'s own `--help` or `CLAUDE.md` → **`padb_make_job.py` — job.json generator**.

---

## Output

Results land in `results_dir/` and are published to the network share:

- `index.html` — gallery page linking all plots
- `*.html` — self-contained interactive plots
- `padb_run_YYYYMMDD_HHMMSS.log` — full console output for diagnostics

**Default publish location:** V2 jobs (`padb_v2.py`) with no `publish_to` key at all publish to `\\srsnas01...\SG6311A\padb-tools-results\<results_dir>` automatically. Set `"publish_to": ""` (or `false`/`null`) to opt out, or to a real path to publish somewhere specific.
