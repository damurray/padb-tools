# PADB Tools — Developer Context for Claude Code

This file is read automatically by Claude Code at session start. It captures the non-obvious implementation decisions, file layout, active work, and gotchas that are not apparent from reading the code alone.

---

## What this repo is

`padb-tools` automates PADB-R.exe — Keysight's RF characterisation database tool — to run headlessly, collect CSV outputs, and generate self-contained interactive HTML plots for SG6311A signal generator data. The goal is to replace PADB::Simple (an internal Keysight tool) with a modern, reproducible, publishable analysis pipeline.

**Key constraint:** Every HTML plot must be fully self-contained (no server, no CDN). Plotly.js is embedded inline. Engineers open results directly from a Windows network share (`\\srsnas01...`).

---

## File locations

| What | Where |
|---|---|
| This repo | `C:\apps\padb\tools\` |
| Job configs | `C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\*.json` |
| PADB results | `C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\*_results\` |
| Raw PADB output | `C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\R-Plots\` |
| PADB logs | `C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Logs\Padb_Err_*.err` |
| Python executable | `C:\Users\damurray\AppData\Local\Python\bin\python3.14.exe` |
| PADB-R.exe | `C:\Program Files\KEYSIGHT\PADB-R.NET\PADB-R.exe` |
| GitHub | `https://github.com/damurray/padb-tools.git` |

**The job configs and results are NOT in the repo** — they live in OneDrive. The repo contains only the tool source.

---

## Architecture

```
job.json → padb_run.py → PADB-R.exe → results/padb/*.csv
                       → padb_plots.py → results/plots/*.html
                       → index.html (gallery)
                       → publish to \\srsnas01...
```

**Dispatch:** `padb_run.py` calls plot functions by name via `getattr(padb_plots, plot_type)`. Adding a new plot type to `padb_plots.py` as a public function automatically makes it available in job.json — no changes to `padb_run.py` needed.

**Plot function signature (required):**
```python
def my_plot_type(csv_path: Path, cfg: dict, output_html: Path) -> None:
```

**CLI:**
```
python padb_run.py job.json                 # full run
python padb_run.py job.json --plots-only    # redo HTML only (fast iteration)
python padb_run.py job.json --no-publish
python padb_run.py job.json --dry-run
```

---

## Active job files (as of 2026-06-30)

| Job file | Pod | Status | Publish destination |
|---|---|---|---|
| `amplitude_job.json` | Amplitude_Accuracy_All_temps_062526.pod | ✓ Published | `...\AmplitudeAccuracy` |
| `clockspurs_job.json` | Non-Harmonic_Clock_spurs_all_Spec_DUTS_June10.pod | ✓ Published | `...\ClockSpurs` |
| `harmonics_job.json` | Harmonics_Latest_all_Spec_DUTS_June10.pod | ✓ Published | `...\Harmonics` |
| `linespurs_job.json` | Line_Related_Spurs_all_Spec_DUTS_June10.pod | ✓ Published | `...\LineSpurs` |
| `closein_job.json` | Non-Harmonics_Close-In_all_Spec_DUTS_June10.pod | ❌ Not yet run | `...\CloseIn` |

All publish destinations are under `\\srsnas01.srs.is.keysight.com\prod\MIDRF3\SG6311A\`.

---

## Implemented plot types

| Function | Source | Interactive? |
|---|---|---|
| `accuracy_vs_freq` | Type=80 Scatter | Yes — full control bar |
| `distribution` | Type=80 Scatter | Plotly native only |
| `population_envelope` | Type=80 Scatter | Plotly native only |
| `empirical_cdf` | Type=80 Scatter | Plotly native only |
| `spec_derivation` | Type=80 Scatter | Plotly native only |
| `stat_summary` | Type=80 Scatter | Yes — full control bar |
| `stat_boxplot` | Type=80 Scatter | Yes — full control bar |
| `de_summary` | Type=60 Environmental | Yes — full control bar |

`de_summary` is defined **twice** in `padb_plots.py` — the first definition (around line 825) is an older static version. The second (around line 2594) is the active interactive version. Python uses the second definition; the first is dead code and should eventually be removed.

---

## PADB-R.exe quirks

- **WinForms app (PE subsystem=2).** Always call with `capture_output=False`. Using `capture_output=True` hangs indefinitely — the process waits for a GUI message loop that never starts.
- **Requires a desktop session.** Will not run headless (SSH without virtual desktop).
- **Always use `-ext r` flag** for Oracle extraction.
- **`-dir` flag** redirects PDF/PNG/CSV output to a folder. When used, CSVs land directly in `results/padb/` and do NOT appear in R-Plots — the `_collect_padb_outputs()` function monitors R-Plots, so its "no new files" message is expected and harmless.
- **Timeout:** Large pods (Environmental analytics, all temps) need `padb_timeout: 7200` or more.

---

## CSV loading

### Scatter (Type=80) — `_load_scatter_csv`

Column detection by keyword match (case-insensitive, stripped):
- **Frequency:** column containing `"frequency"` or `"x value"`
- **Value:** first numeric column after frequency, skipping Group/Serial/Station/Lower Limit/Upper Limit/metadata columns
- **Serial:** column containing `"serial num"`, `"serial no"`, `"sn"`, `"unit id"`, `"dut id"` (excluding `"station"`); or exactly `"serial"`
- **Station:** contains `"station"`
- **Lower/Upper Limit:** contains `"lower limit"` / `"upper limit"`
- **Group:** exactly `"group"`

### Environmental (Type=60) — `_load_env_csv` (the one at ~line 2392)

Reads by **exact column name** (PADB standard output names). Key columns:
`X value`, `Group`, `UDE`, `LDE`, `Min (Env.)`, `Max (Env.)`, `mean (Env.)`, `Upper TTL (est)`, `Lower TTL (est)`, `UDE (Max)`, `LDE (Max)`, `Lower Limit`, `Upper Limit`, `Units`

Values > 2,000,000,000 in `UDE`, `LDE`, `UDE (Max)`, `LDE (Max)` are clamped to `NaN` — PADB writes `2,147,483,647` (INT_MAX) when environmental computation fails.

---

## Group string format and parsing

PADB writes the `Group` column as key:value pairs separated by **two or more spaces**:
```
AlcState: TRUE  OA State: 0  Mode: 0  Serial Number: US65080401
```

`_parse_group_kv()` splits on `2+` spaces first (preserving multi-word keys like `"OA State"` and `"Serial Number"`), then extracts `Key: Value` per segment. Falls back to single-word key regex if no double-space separators are found.

**Serial key detection** (used by `stat_summary`, `stat_boxplot`, `de_summary`):
1. Key name contains `"serial"`, `"unit id"`, `"dut id"`, or `"s/n"` (case-insensitive), **or**
2. More than 50% of observed values for that key match `^[A-Z]{2,3}\d{5,}$` (e.g., `US65080401`)

Serial keys are excluded from condition filter dropdowns. Condition keys with 1 or >20 distinct values are also excluded — only 2–20 value keys become filter panels.

**Temperature detection** (`stat_boxplot` only): a key whose name contains `"temp"`. Room condition = the temperature value numerically closest to 25.

---

## Embedding JavaScript in Python

**Always use raw strings for large JS blocks:**
```python
_MY_JS = r"""
function foo(x){ return x*2; }
"""
```

This avoids `{{`/`}}` escaping hell in f-strings. Python variables are injected as `var X=...;` declarations before the raw string:
```python
constants = f"var TITLE={json.dumps(title)};\nvar DATA={json.dumps(data)};"
html = f"<script>\n{constants}\n{_MY_JS}</script>"
```

**Never use f-strings for the JS body itself.**

---

## Interactive HTML patterns

### Toggle panels (stat_summary, stat_boxplot, de_summary)

Always use **`style.display` toggling**, not CSS class toggling:
```javascript
// CORRECT
if(el.style.display==='none'){ el.style.display='block'; }
else { el.style.display='none'; }

// WRONG — class toggling silently fails in Plotly-embedded pages
el.classList.toggle('open');
```

Stats panel divs start with `style="display:none"` inline (not a CSS rule). Button IDs follow the pattern `*_toggle_btn` or `*_btn`.

### Plotly.js placement

Always load Plotly.js in `<head>`, never at the end of `<body>`:
```html
<head>
  <script>{_get_plotlyjs()}</script>
</head>
```

If loaded after the plot div, `Plotly.newPlot()` inline scripts inside the div run before Plotly is defined and silently fail.

### Trace count consistency

`Plotly.react()` matches traces by index. Always emit a **fixed number of traces per condition** on every `update()` call — use empty `x:[], y:[]` arrays rather than conditionally omitting traces. Mismatched trace counts cause fill bands to attach to the wrong reference trace.

### TI bands require `type:'scatter'` not `type:'scattergl'`

`fill:'tonexty'` is silently ignored by WebGL (`scattergl`) traces. All TI band and fill traces must use `type:'scatter'`.

---

## job.json: csv vs csv_file

| Key | Match rule |
|---|---|
| `csv` | **Substring** match against analytic names in `csv_map`. Case-sensitive. |
| `csv_file` | **Exact filename** (with `.csv` extension) in `results/padb/`. |

Use `csv_file` when: (a) the analytic name doesn't substring-match cleanly, (b) two analytics share the same output filename, or (c) the CSV was copied manually from R-Plots.

`csv_file` takes precedence over `csv` when both are present.

---

## Common gotchas

**PADB returns code 0 but writes no CSV / no data:**
Add `"TestRun_RunStatus": "{All}"` to `subex`. Many pods default to `TestRun_RunStatus='P'` (passing runs only). This silently filters out all data if no runs are marked passing.

**CSV not found after a successful run:**
Check if the file is in `results/padb/` with a slightly different name than expected. PADB sometimes adds suffixes or uses different capitalisation. Switch from `csv` to `csv_file` with the exact filename.

**Clock spurs Environmental CSV:**
The clock spurs SummaryPlot doesn't write a CSV (only PNGs/PDF). The `Env_Clock_spurs_All_Spec_Duts.csv` was copied manually from R-Plots to `clockspurs_results/padb/` and referenced via `csv_file`. This is expected — it's a pod configuration limitation.

**Amplitude CSVs predate the run:**
If a previous partial run left CSVs in `results/padb/`, `_collect_padb_outputs()` may miss them (it only picks up files newer than run start). Copy from R-Plots manually and use `--plots-only`.

**stat_summary Spec↓ is a magnitude:**
The lower spec field in stat_summary is entered as a positive magnitude (e.g., `0.15` for a ±0.15 dB spec). It is internally negated. The field label is `|Spec↓|` with `min=0`.

**de_summary serial filter:**
Not possible. The Environmental CSV (Type=60) is pre-aggregated across all DUTs by PADB — there are no per-DUT rows. Serial filtering would require re-computing environmental deltas from a raw Scatter CSV.

**de_summary stat table showed no data:**
Root cause: the table panel was rendering at `display:block` (via class toggle) but with zero height because `updateEnvStatsTable` silently errored. Fixed by switching to `style.display` toggling (matching the pattern in stat_summary and stat_boxplot) and wrapping the table-build in a try-catch that renders the error message in the panel on failure.

---

## Statistics implementation notes

**NP TI (non-parametric tolerance interval):**
Computed server-side in `_nonparametric_ti()` using `scipy.stats.beta.cdf`. Finds tightest symmetric order-statistic bounds [x_(d+1), x_(n-d)] satisfying `beta.cdf(1-P, 2(d+1), n-2(d+1)+1) >= C`. Requires n ≥ ~39 for P=0.90, C=0.90. Stored as `np_ti_lo`/`np_ti_up` per frequency stat. Set to `null` when serial filter is active (client-side recomputation of NP TI is not feasible).

**stat_summary DUT averaging:**
Each DUT contributes **one data point** per (condition × frequency) — all repeat measurements for that DUT at that frequency are averaged first. Population statistics (mean, σ, TI) are then computed across DUT averages. This means n in the statistics table = number of DUTs, not number of measurements.

**Whisker convention in stat_boxplot:**
- Unfiltered: whiskers use **max-inlier** convention (Python `_box_stats`, Tukey IQR fence)
- When Y-range filter or serial filter is active: whiskers recomputed client-side using **fence** convention (Q1 − 1.5×IQR, Q3 + 1.5×IQR), which can extend beyond the filter boundary

---

## Extending the tool

### Adding a new plot type

1. Add a public function to `padb_plots.py` with signature `def my_type(csv_path, cfg, output_html)`.
2. Reference it by function name in `secondary_plots` in job.json: `"type": "my_type"`.
3. No changes to `padb_run.py` needed.

### Adding a new interactive control

Follow the established pattern:
- Condition filter: collapsible div with `filter-wrap`/`filter-panel`/`filter-btn` CSS classes
- Toggle panel (stats table, etc.): `style="display:none"` on div, `style.display` toggling in JS, button with unique ID
- Frequency sliders: `<input type="range">` with `oninput="syncFreq()"`
- All controls call `update()` which calls `Plotly.react()`
- Embed JS as a module-level raw string (`r"""..."""`); inject Python data as `var X=...;` constants before the raw string block

### Future work identified

- **Parallel scatter overlay on stat_boxplot:** Load a second Type=80 Scatter CSV alongside the box plot to overlay individual DUT measurement points on the boxes (accessing the original datum). Needs a `scatter_overlay` key in job.json config.
- **Remove dead `de_summary` at ~line 825** — the old static version is superseded by the interactive one at ~line 2594.
- **`closein_job.json`** has not been run yet.
