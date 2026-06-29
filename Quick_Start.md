# PADB Tools — Quick Start

## One-time setup

```
py -m pip install pandas numpy matplotlib scipy plotly
```

Requires PADB-R.NET installed at `C:\Program Files\KEYSIGHT\PADB-R.NET\PADB-R.exe`.

---

## For each new analysis

### 1 — Create an analysis folder

```
MyAnalysis\
  MyMeasurement.pod    ← export from PADB
  job.json             ← copy and edit from an existing analysis
  run.bat              ← copy from C:\apps\padb\tools\
```

### 2 — Edit job.json (minimum changes per run)

```json
{
    "description": "What this run is",
    "pod": "MyMeasurement.pod",
    "subex": {
        "TestRun_SerialNum": "'US65080401','US65080415'",
        "Device_MinDate": "2026-06-01",
        "Device_MaxDate": "2026-06-30"
    },
    "secondary_plots": [
        {
            "type": "accuracy_vs_freq",
            "csv":  "Analytic name from pod (value of AnalyticName=)",
            "title": "My Plot Title",
            "y_label": "Accuracy (dB)",
            "y_lim": [-0.5, 0.5]
        }
    ],
    "publish": {
        "destination": "\\\\srsnas01.srs.is.keysight.com\\prod\\MIDRF3\\SG6311A\\MyAnalysis"
    }
}
```

### 3 — Run

```
run.bat                  full run (PADB + plots + publish)
run.bat --dry-run        build switch file only, don't call PADB
run.bat --no-publish     run PADB + plots, skip copy to share
run.bat --plots-only     redo plots only (PADB already run)
```

Or from anywhere:

```
py "C:\apps\padb\tools\padb_run.py" "C:\path\to\job.json"
```

### 4 — Open results

```
results\index.html
```

Interactive gallery: iframe plots, PDF links, CSV links, run log.

---

## Available plot types

| type | Use with | What it shows |
|---|---|---|
| `accuracy_vs_freq` | Scatter CSV | Accuracy vs frequency, one trace per serial |
| `population_envelope` | Scatter CSV | Min/max/median/P5-P95/TI band across all serials |
| `distribution` | Scatter CSV | Histogram + KDE + best-fit PDF + spec lines |
| `empirical_cdf` | Scatter CSV | eCDF per serial — read yield directly |
| `spec_derivation` | Scatter CSV | Per-band TI + margin to spec — for datasheet specs |
| `de_summary` | Environmental CSV | Mean Env. deviation vs freq per group |
| `de_heatmap` | Environmental CSV | Group × frequency heatmap |

**Scatter CSV** = PADB analytic Type=80  
**Environmental CSV** = PADB analytic Type=60

To find the right `csv` name, open the `.pod` file and look for `AnalyticName=` lines.

---

## Statistics notes (spec_derivation)

| n | Max supported TI level |
|---|---|
| 15 | P90/C90 (indicative) |
| 29 | P90/C90 (valid) |
| 59 | P95/C90 |
| 299 | P99/C95 |

Tool warns automatically when n is below the required minimum.

---

## Full documentation

`C:\apps\padb\tools\PADB_Tools_Guide.md`
