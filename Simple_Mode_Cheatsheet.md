# PADB Simple Mode — One-Page Steps

Literal extract-and-post: PADB-R.exe renders its own native PNG/PDF per analytic; the tool just wraps them in a gallery. No custom plotting, no filters — a modern `PADB::Simple` replacement.

## 1. Have a `.pod` file
Use an existing one in `Data\`, or build a new analysis in the PADB-R.NET GUI and save it as `.pod` there. Simple mode runs whatever analytics the pod already defines — it doesn't create them.

## 2. Write a job.json
Name it `*_job.json` (underscore before "job") if you ever want it schedulable — `padb_scheduler.py` only discovers that exact pattern.

```json
{
  "description": "SG6311A <Analysis> — Simple mode",
  "pod": "YourPod.pod",
  "mode": "simple",
  "padb_exe": "C:\\Program Files\\KEYSIGHT\\PADB-R.NET\\PADB-R.exe",
  "results_dir": "your_analysis_simple_results",
  "padb_timeout": 7200,
  "run_analytics": true,
  "padb_output_dir": "C:\\Users\\damurray\\OneDrive - Keysight Technologies\\Documents\\Padb\\R-Plots",
  "padb_logs_dir": "C:\\Users\\damurray\\OneDrive - Keysight Technologies\\Documents\\Padb\\Logs",
  "publish": { "destination": "\\\\srsnas01...\\SG6311A\\YourFolder" }
}
```
- `subex` (optional) — override extraction filters (`Device_MinDate`, `TestRun_RunStatus`, etc.).
- `publish` (optional) — omit entirely to keep results local only.
- `secondary_plots`/`views` are ignored in this mode.
- Every backslash in a Windows path must be doubled (`\\`) — a lone `\"` right before a closing quote is the #1 way to break the JSON.

## 3. Run it
```
py C:\apps\padb\tools\padb_run.py "C:\path\to\your_job.json"
```
Real PADB-R.exe execution — needs your actual desktop session (not headless SSH) and live Oracle DB access. Forces `OutputConfig_OutputGraph=1`/`GraphFormat=png,pdf` on every analytic automatically; you don't need to edit the pod.

Useful flags: `--dry-run` (build the switch file, skip PADB), `--no-publish`, `--plots-only` (rebuild the gallery from already-extracted files, no re-run).

## 4. Review
Open `results_dir\index.html`. One card per rendered image (an analytic can produce several via PADB pagination — normal), a metadata table dumped verbatim from the pod, and download links for `.pdf`/`.csv`/`.sao`/`.pod`/`.txt`. Also check `results_dir\HOW_TO_USE.txt` for a mode-specific explainer.

## 5. Schedule it (optional)
```
schtasks /create /tn PADB_<job_stem> /tr "py \"C:\apps\padb\tools\padb_run.py\" \"C:\path\to\your_job.json\"" /sc daily /st 02:00 /f
```
Runs as your own login (needed for the publish path), so it only fires while you're logged in unless Task Scheduler is configured otherwise. Or use the GUI: `py C:\apps\padb\tools\padb_scheduler.py`.
