# PADB Interactive Mode (V2) — One-Page Steps

The full interactive suite — filters, tolerance intervals, serial/condition exclusion, global-flag exclusion, CSV export. Two files, two commands: extract, then plot.

**Fastest path:** `padb_make_v2_job.py` generates *both* job files below (steps 1 and 3) in one shot — one run job plus one plot job per Type=80 analytic in the pod, all sharing one results folder/gallery:
```
py C:\apps\padb\tools\padb_make_v2_job.py YourPod.pod --module YourFolder
```
Read on if you want to understand what it writes, customize by hand, or write it yourself.

## 1. Write the extract job (`*_run_job.json`)
Same schema as any `padb_run.py` job — no `mode` key needed (or set `"mode": "interactive"` just as a label; it doesn't change what this step does):

```json
{
  "description": "SG6311A <Analysis> — extract step",
  "pod": "YourPod.pod",
  "padb_exe": "C:\\Program Files\\KEYSIGHT\\PADB-R.NET\\PADB-R.exe",
  "results_dir": "your_analysis_run_results",
  "padb_timeout": 7200,
  "run_analytics": true,
  "padb_output_dir": "C:\\Users\\damurray\\OneDrive - Keysight Technologies\\Documents\\Padb\\R-Plots",
  "padb_logs_dir": "C:\\Users\\damurray\\OneDrive - Keysight Technologies\\Documents\\Padb\\Logs"
}
```

**Shortcut:** `padb_make_job.py` can write this one too — pass `--mode interactive` explicitly (its default is `simple`, which would trigger the wrong dispatch for this workflow):
```
py C:\apps\padb\tools\padb_make_job.py YourPod.pod --module YourFolder --mode interactive
```
Add `--min-date "8 weeks ago" --max-date today` for a rolling extraction window instead of the pod's own baked-in dates (see `subex` note below).

By hand, that's a `"subex"` block: `{"Device_MinDate": "8 weeks ago", "Device_MaxDate": "today"}` — relative-date values are resolved to the real date every time the job runs, not whenever the file was written.

## 2. Run the extraction
```
py C:\apps\padb\tools\padb_run.py "C:\path\to\your_analysis_run_job.json"
```
Real PADB-R.exe execution (desktop session + Oracle DB required). Writes CSV(s) into `results_dir\padb\`. Note the printed path — you'll need it in step 4.

**Recommended before step 4:** sanity-check the CSV first —
```
py C:\apps\padb\tools\padb_csv_check.py "C:\path\to\your_analysis_run_results\padb\Extracted.csv"
```
Catches orphaned numeric columns (a second swept dimension sitting unused), inverted spec rows, high Group cardinality, and missing grouping items (Serial/Port/Spec/Uncertainty) before you spend time on a build that turns out wrong.

## 3. Write the plot job (`*_v2_job.json`)
Different schema — this one drives `padb_v2.py`, not `padb_run.py`:

```json
{
  "description": "SG6311A <Analysis>",
  "title_prefix": "SG6311A <Analysis>",
  "y_label": "Measured Value (units)",
  "results_dir": "your_analysis_v2_results",
  "index_title": "SG6311A <Analysis>",
  "spec_direction": "auto",
  "views": ["scatter", "stat_summary", "boxplot", "distribution", "env_coverage", "summary"]
}
```
- Omit `"views"` for automatic, data-driven selection (Room-only data → `scatter`+`boxplot`; multi-temp → all six; add `"room_only_full_views": true` to also get `stat_summary`/`summary` on Room-only data).
- `spec_direction`: `"lo"`/`"hi"`/`"both"`/`"none"`/`"auto"` — set explicitly if the pod has no configured spec limits but you know the measurement is one-sided. Only sets the *default*: a real CSV limit always overrides it, and `summary`/`stat_boxplot` show a live Both/Upper/Lower selector on top of it whenever the CSV has no limit at all.
- `x_label`/`x_unit` — override if the x-axis isn't carrier frequency in MHz (e.g. phase-noise offset in Hz).
- `publish_to` — omit for the default `\\srsnas01...\SG6311A\padb-tools-results\<results_dir>`; set `""` to opt out, or a path to publish elsewhere.
- Comparing two sites instead of a single extraction? Use `"compare_csv": {"SiteA": "path...", "SiteB": "path..."}` + `"primary_site"` instead of a single CSV — see `PADB_Tools_Guide.md` → **Cross-Site Comparison**. The webapp's collapsed-by-default "Compare two datasets" panel can build and run this job for you without hand-writing it.
- CSV huge / a dense continuous sweep (thousands of raw points per DUT/condition)? Add `"scatter_decimate": "auto"` (default anyway — reduces each series above 2000 points while always keeping the true min/max/first/last so a real spike is never dropped) and/or `"binary_encode": true` (float32-encodes the numeric columns instead of JSON text; byte-for-byte identical output when omitted) — see `PADB_Tools_Guide.md` → **Performance: Decimation and Binary Encoding**.

## 4. Build the interactive views
```
py C:\apps\padb\tools\padb_v2.py "C:\path\to\your_analysis_v2_job.json" --csv "C:\path\to\your_analysis_run_results\padb\Extracted.csv"
```

## 5. Review
Open the `results_dir\index.html` this step wrote. Filters, TI/NP-TI toggle, serial exclusion, GF (set/clear/export/import a global flag), Group by, Segment by (Spec/Limit/Uncertainty), Y-range filter, CSV export — per view, see `PADB_Tools_Guide.md`. Each view also has a collapsible ⓘ **Help** panel that explains these controls and flags any inverted Upper/Lower Limit or Spec rows it finds.

## 6. Iterate without re-extracting
CSV unchanged? Just re-run step 4 alone — no need to touch PADB-R.exe again.

## 7. Schedule it (optional)
Only the extraction step (step 2) is worth scheduling automatically — the plot-build step is fast and usually run on demand after reviewing new data:
```
schtasks /create /tn PADB_<run_job_stem> /tr "py \"C:\apps\padb\tools\padb_run.py\" \"C:\path\to\your_analysis_run_job.json\"" /sc daily /st 02:00 /f
```
