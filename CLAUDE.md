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
                       → padb_run_YYYYMMDD_HHMMSS.log (tee of all stdout)
                       → publish to \\srsnas01...

padb_scheduler.py → Windows Task Scheduler → padb_run.py (overnight)
```

**Dispatch:** `padb_run.py` calls plot functions by name via `getattr(padb_plots, plot_type)`. Adding a new plot type to `padb_plots.py` as a public function automatically makes it available in job.json — no changes to `padb_run.py` needed.

**Plot function signature (required):**
```python
def my_plot_type(csv_path: Path, cfg: dict, output_html: Path) -> None:
```

**CLI:**
```
py padb_run.py job.json                 # full run
py padb_run.py job.json --plots-only    # redo HTML only (fast iteration)
py padb_run.py job.json --no-publish
py padb_run.py job.json --dry-run
```

---

## Active job files (as of 2026-07-22)

| Job file | Pod | Status | Publish destination |
|---|---|---|---|
| `amplitude_job.json` | Amplitude_Accuracy_All_temps_062526.pod | ✓ Published | `...\AmplitudeAccuracy` |
| `clockspurs_job.json` / `clock_leakage_env_v2_job.json` | Non-Harmonic_Clock_spurs_all_Spec_DUTS_June10.pod | ✓ Published | `...\ClockSpurs` (explicit `publish_to:""` opt-out on the V2 job — published via the older mechanism) |
| `harmonics_job.json` / `harmonics_env_v2_job.json` | Harmonics_Latest_all_Spec_DUTS_June10.pod | ✓ Published | `...\Harmonics` |
| `linespurs_job.json` / `line_related_env_v2_job.json` | Line_Related_Spurs_all_Spec_DUTS_June10.pod | ✓ Published | `...\LineSpurs` |
| `closein_job.json` / `closein_env_v2_job.json` | Non-Harmonics_Close-In_all_Spec_DUTS_June10.pod | ✓ V2 stable | `...\CloseIn` |
| `absphase_noise_job.json` | Absolute Phase Noise EP6 Spec Setting.pod | ✓ Published | `...\AbsPhaseNoise` |
| `maxpower2_job.json` | (superseded) | ⚠️ Known issues, not fixed — see below | — |
| `maxpower3_run_job.json` + 4 plot jobs | MaxPower3.pod | ✓ Plotted, published (default location) | `...\padb-tools-results\maxpower3_results` |
| `vswr_v2_job.json` | VSWR2.pod (`vswr_scatter.csv`) | ✓ V2, Room-only, published (default location) | `...\padb-tools-results\vswr2_results` |
| `return_loss_v2_job.json` | VSWR2.pod (`return_loss_scatter.csv`) | ✓ V2, Room-only, published (default location) | `...\padb-tools-results\vswr2_results` |
| `phase_noise_de_v2_job.json` | Absolute Phase Noise EP6 Spec Setting DE.pod | ✓ V2, multi-temp, published (default location) | `...\padb-tools-results\phase_noise_de_results` |

All explicit publish destinations are under `\\srsnas01.srs.is.keysight.com\prod\MIDRF3\SG6311A\`. See **Default publish location** below for jobs with no `publish_to` set.

### MaxPower2 → MaxPower3

`maxpower2_job.json` had three known unresolved issues (empty Environmental plot, no spec limits, n=17 below NP-TI threshold — see project memory `project_maxpower2_issues`). MaxPower3 is the redo with a new pod:

- **Fixed:** `Environment_TestStep={All}` is now set in `MaxPower3.pod` (was `'Room'` in MaxPower2), so `distribution`/`env_coverage`/`summary` views now have non-Room data to compute deltas from.
- **Still open:** Every analytic in `MaxPower3.pod` has `Limits_YLimit=None` — no spec limits are configured at the pod level. Worked around per-job via the `spec_direction` key (see below) rather than a pod fix.
- MaxPower is the first non-spur (dBm, one-sided-lower-spec) pod family run through the V2 pipeline — see `spec_direction` below for what that surfaced.

### `spec_direction` job.json key (added for MaxPower3)

`stat_summary` (V2) auto-detects whether to show the lower spec line, upper spec line, both, or neither, based on whether any `freq_stats` entry has `spec_lo`/`spec_up` populated (`padb_plots.py` ~line 4072). MaxPower3's pod has no spec limits at all (`Limits_YLimit=None` everywhere), so auto-detection always resolves to `"none"` — no pass/fail line would ever show, even though MaxPower is conceptually a lower-spec-only (guaranteed minimum power) measurement.

Fix: set `"spec_direction": "lo"` explicitly in the job JSON (`maxpower3_leveled_linear_job.json`, `maxpower3_unleveled_linear_job.json`) to force the lower-spec display regardless of what's in the CSV. Valid values: `"lo"`, `"hi"`, `"both"`, `"none"`, or omit for `"auto"` (data-driven detection, correct for spur-family pods that do have `Lower Limit`/`Upper Limit` columns).

**This was not previously documented** — added to `PADB_Tools_Guide.md` and `PADB_Analytic_Requirements.md` on 2026-07-16.

---

## `x_label` / `x_unit` job.json keys (added for the phase-noise pod, 2026-07-21)

The x-axis title and every unit-suffix string (hover tooltips, stats table headers, CSV export headers, filter-bar labels) in `scatter`/`stat_summary`/`env_coverage`/`summary`/`distribution`/`boxplot` were hardcoded to `"Frequency (MHz)"` / `"MHz"` with no override. This was actively wrong for the phase-noise pod (`Absolute Phase Noise EP6 Spec Setting DE.pod`), whose x-axis is **Frequency Offset in Hz**, not carrier frequency in MHz — a 10,000,000 Hz value labeled "MHz" looks like 10,000 GHz.

- `"x_label"` — full axis title, e.g. `"Frequency Offset (Hz)"`. Default: `"Frequency (MHz)"`.
- `"x_unit"` — short unit suffix used everywhere else, e.g. `"Hz"`. Default: `"MHz"`.

Both default to the exact prior literal text, so no existing pod's output changes unless it explicitly sets these. `env_coverage`'s own `y_label` is still hardcoded (`"ΔEnv (dB)"`) in `render_env_coverage` regardless of job.json — a separate, still-open gap; the documented `env_coverage_y_label` key isn't actually wired up.

---

## Group-string parser padding bug (fixed 2026-07-21)

`_parse_group_kv()` silently dropped grouping keys whenever PADB's own value padding produced 2+ spaces after a colon (e.g. `"Frequency (MHz):  10"` for a 2-digit value vs `"Frequency (MHz): 100"` for a 3-digit value — PADB right-pads to a fixed column width). The 2+-space split treated the padding as a segment boundary, splitting `"Frequency (MHz):"` (empty value) away from its orphaned value `"10"` — both fragments then failed the `key: value` regex and were dropped. Exactly half of the phase-noise pod's carrier-frequency groupings vanished before this fix.

**Fix:** colon-less fragments produced by the 2+-space split are now re-merged into the preceding part before matching (both in `_parse_group_kv()` and the duplicate inline parser in `_build_stat_summary_html`'s `COND_DIMS` builder). This is a no-op when no orphan fragments exist, so it's safe for every existing pod — verified via `qa_padb.py` (unchanged 27/5) plus full regen of clock leakage, close-in, VSWR2.

**Implication:** this bug could be lurking in any already-"stable" pod with a variable-width numeric grouping value that nobody happened to check for — it was never specifically tested for before the phase-noise pod's 10/100 MHz carrier split exposed it.

---

## Spec-mask rendering (`scatter` view, added 2026-07-22)

`accuracy_vs_freq`'s `buildLayout()` used to round every row's `Upper_Limit`/`Lower_Limit` to the nearest integer and draw one **full-width** dashed line per distinct rounded value (`xref:'paper', x0:0, x1:1`) — designed for a constant spec with sub-dBc MU-adjustment noise. For a genuinely frequency-varying spec (PADB `Limits_YLimit=Line`, e.g. a phase-noise mask or a frequency-banded dBc spec), this produced a cluttered stack of full-width lines, none tied to the frequency range they actually applied to.

**Fix:** `getSpecMask(dataArr)` (new helper in `_AV_FREQ_JS`) builds per-frequency (min Upper_Limit / max Lower_Limit) pairs and flags `isMask=true` when more than 3 distinct rounded values exist. `buildTraces()` then draws a proper `line:{shape:'hv'}` step trace following the real (freq, limit) pairs, and `buildLayout()` skips the old full-width shapes entirely when in mask mode.

**This changes the visual appearance of already-published pods**, not just the phase-noise pod: Clock Leakage (6→1 line), Line-Related (6→1 line), and Close-In (5→1 line) all have genuine frequency-banded step specs and now trigger mask mode — confirmed monotonic and correct against the documented spec tables, so this is an improvement, not a regression, but it was a deliberate, explicitly-confirmed decision (not silent) given those datasets are already published. Harmonics/Sub-Harmonics stays on the old flat-line rendering (only 3 tight values, doesn't cross the threshold).

---

## Auto view-selection (added 2026-07-22)

`padb_v2.py`'s per-job-runner omits `"views"` from job.json entirely now to get automatic, data-driven defaults instead of hardcoding a list per pod:

- **Room-only data** (`Temperature` column is a subset of `room_values`, default `{"Room"}`) → `scatter` + `boxplot` only.
- **Multi-temp data detected** → all six views (`scatter`, `stat_summary`, `boxplot`, `distribution`, `env_coverage`, `summary`).
- **Room-only + `"room_only_full_views": true"`** → also adds `summary` + `stat_summary` (never `distribution`/`env_coverage` — those need non-Room data to compute a delta against, so they're never meaningful for Room-only data regardless of the flag).

An explicit `"views"` key in job.json always overrides auto-detection, preserving all pre-existing job configs verbatim. `vswr_v2_job.json` / `return_loss_v2_job.json` (Room-only, want `stat_summary` too) now use `"room_only_full_views": true` instead of a hardcoded `views` list — the direct real-world case this was built for.

---

## Default publish location (added 2026-07-22)

Jobs with **no `publish_to` key at all** now default to publishing to:
```
\\srsnas01.srs.is.keysight.com\prod\MIDRF3\SG6311A\padb-tools-results\<results_dir>
```
(`DEFAULT_PUBLISH_ROOT` in `padb_v2.py`). Set `"publish_to": ""` (or `false` / `null`) explicitly to opt out — this is what the 4 stable spur V2 jobs do, since they're already published via their own established destinations through a different mechanism. Set `"publish_to"` to a real path to publish somewhere specific, exactly as before.

**Gotcha found while wiring this up:** `_publish()`'s success message used a Unicode arrow (`→`), which throws `UnicodeEncodeError` on this Windows console's codepage (`cp1252`/`charmap`) — and since the actual `shutil.copy2()` calls happen *before* that print statement, the copy succeeds but the exception handler reports `"[WARN] Publish failed"`, a false negative. Fixed by using plain ASCII (`->`) instead. Two more instances of the same class of bug (em-dash `—` in warning messages in `padb_run.py`/`padb_v2.py`) fixed at the same time. **Any future `print()` with a non-ASCII character in this codebase should be treated as a latent bug on Windows consoles** — stick to ASCII in printed status/error text.

---

## `updateStatPanel` defensive try-catch (added 2026-07-22)

Mirrors the existing `de_summary` fix below: `updateStatPanel` (the `stat_summary` statistics table) is now wrapped in try/catch, rendering the actual JS error message into the panel on failure instead of silently doing nothing. Added while investigating a report that the harmonics/sub-harmonics table wasn't updating on filter change — turned out to be a stale browser cache, not a real bug, but the defensive wrapping is a safe, permanent improvement (no-op when nothing throws) and is now in place if a real instance of this bug class ever occurs.

---

## Scheduler (padb_scheduler.py)

`py C:\apps\padb\tools\padb_scheduler.py`

tkinter GUI that manages Windows Task Scheduler entries for every `*_job.json` found in a directory. Scans `C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\` by default (directory is user-selectable).

- **Treeview table:** Job File / Scheduled? / Schedule columns. Scheduled rows shown in green; orphan tasks (task exists but job file deleted) shown in grey.
- **Add/Edit Schedule:** opens `ScheduleDialog` — Weekly (with day checkboxes) or Daily, hour/minute spinboxes, "Test Run Now" button (launches job immediately in a new console).
- **Remove Schedule:** deletes the Task Scheduler entry; prompts for confirmation.
- **Task naming:** `PADB_{job_stem}` (e.g. `PADB_amplitude_job`).
- **Backend:** `schtasks` CLI. Runs tasks as the **current user** (not SYSTEM) so network publish paths remain accessible.
- **Orphan detection:** tasks present in Task Scheduler with no matching `.json` file are shown greyed out (can only be removed, not edited).

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

Serial keys are excluded from condition filter dropdowns. Condition keys with exactly 1 distinct value are excluded (constant, no info). **Correction (2026-07-21):** there is no 20-value upper cap in `stat_summary`'s own filter-panel builder (`_build_stat_summary_html`, `len(vals) > 1`, no ceiling) — confirmed by VSWR2's `OA` key (40–56 distinct values) rendering as a full checkbox panel. A separate `1 < len(vals) <= 50` check exists elsewhere (env_coverage/boxplot condition-vs-serial classification) but that's a different cap for a different purpose, not a "filter panel cutoff." A previous version of this doc and `PADB_Analytic_Requirements.md` incorrectly stated a 20-value cap throughout — corrected.

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

**R-Plots collection uses stem-matching (not timestamps):**
`_collect_padb_outputs()` matches files in `padb_output_dir` (R-Plots) by stem against known analytic names. Parallel jobs with different stems do not contaminate each other. Old files from a previous run of the same job (same stems) will be re-collected — this is expected. If R-Plots is stale or missing, copy CSVs to `results/padb/` manually and use `--plots-only`.

**stat_summary Spec↓ is a magnitude:**
The lower spec field in stat_summary is entered as a positive magnitude (e.g., `0.15` for a ±0.15 dB spec). It is internally negated. The field label is `|Spec↓|` with `min=0`.

**Phase noise serial collapse in stat_summary (n=1):**
When the Group string does not embed the serial number (e.g. phase noise pods where `Serial Number` is a separate TData column, not part of Group), the serial fallback uses the entire Group string — every DUT in a group gets the same serial ID, collapsing n to 1. Fixed: `_aggregate_stat_data()` now overrides `_serial_id` from `df["Serial"]` when `serial_keys` is empty and the column contains valid serial patterns. No action needed in job.json; it is automatic.

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
- Never use a non-ASCII character in a `print()`/status message — throws `UnicodeEncodeError` on this Windows console's codepage; see **Default publish location** above for the bug this caused

## Run log files

Every `padb_run.py` run writes a timestamped `padb_run_YYYYMMDD_HHMMSS.log` to `results_dir/`. Output is teed to both the console (when interactive) and the log file simultaneously, line-buffered. This means:
- Partial output is preserved even if the process crashes.
- Task Scheduler overnight runs (no console) still produce a log.
- Multiple runs accumulate separate log files — they do not overwrite each other.

---

### Future work identified

- **Parallel scatter overlay on stat_boxplot:** ✅ Implemented. `vals_detail: [{s, v}]` is embedded in `BOX_DATA` for every freq_stat entry (no second CSV needed). "Show points" checkbox in the filter bar overlays per-DUT scatter points (size 5, opacity 0.55) on the boxes. Respects serial and Y-range filters via the `vals_detail` field on `fs` entries. Outlier traces still use `circle-open` markers; scatter points use filled circles for visual distinction.
- **Remove dead `de_summary` at ~line 825** — the old static version is superseded by the interactive one at ~line 2594.
- **`env_coverage_y_label` job.json key is documented but not wired up** — `render_env_coverage` hardcodes `y_label="ΔEnv (dB)"` in the caller regardless of cfg. Low priority since ΔEnv is always the right label for this view's actual data, but the doc/code mismatch should be resolved one way or the other.
- **`de_summary`/`stat_boxplot`'s non-interactive branch don't have `x_label`/`x_unit`** — only the six V2-pipeline-relevant view builders (`scatter`, `stat_summary`, `boxplot` interactive path, `distribution`, `env_coverage`, `summary`) were updated. Add if a future pod needs a non-MHz axis through the V1 `de_summary` or `stat_boxplot(interactive=False)` paths.
