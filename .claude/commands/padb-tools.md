# PADB Tools Coding Agent

You are assisting with `padb-tools` — a Python toolset that drives PADB-R.exe (Keysight's RF characterisation database tool) headlessly, collects CSV output, and generates self-contained interactive HTML plots for RF signal generator characterization data (currently SG6311A).

If the user has a specific question or task, address it directly using the knowledge below. If not, ask whether they're: (a) running an existing job, (b) onboarding a new pod, (c) adding/debugging a plot feature, or (d) reviewing generated HTML before publishing.

This file is a condensed index. Full detail lives in the repo's own docs — read them, don't guess:

| Doc | Read for |
|---|---|
| `GETTING_STARTED.md` | New-user orientation, doc map, V1 vs V2 pipeline choice |
| `Quick_Start.md` | Step-by-step new-pod walkthrough |
| `PADB_Tools_Guide.md` | Full job.json / plot-type reference |
| `PADB_Analytic_Requirements.md` | Pod-contributor requirements (Group string format, CSV columns, per-plot-type minimums) |
| `QA_Checklist.md` | Manual browser verification before publishing |
| `CLAUDE.md` | Architecture, file locations, active job status, dev-facing gotchas |

**Path caveat:** examples in this repo's docs use one user's actual OneDrive/Python paths. Adjust to your own environment — don't assume `C:\Users\damurray\...` is universal.

---

## Architecture, one line

```
job.json → padb_run.py → PADB-R.exe → results/padb/*.csv → padb_plots.py (or padb_v2.py) → results/plots/*.html → index.html → publish
```

Two pipelines: **V1** (`padb_run.py` + `padb_plots.py`, one job.json) and **V2** (`padb_run.py` extract-only + `padb_v2.py` plot-only, two job files: `*_run_job.json` + `*_v2_job.json`). Use V2 for any new pod — richer interactive feature set, and Step 2 (plotting) re-runs in seconds without re-extracting.

---

## New-pod checklist — work through this before assuming something's broken

1. **Inspect the pod.** Open the `.pod` file as text. For each analytic note `AnalyticName`, `AnalyticType` (80=Scatter, 60=Environmental, 90=SummaryPlot, 20=BoxPlot — only 80 and 60 have usable CSV loaders), `OutputConfig_OutputFile`, and whether `Limits_YLimit` is set.
2. **`TestRun_RunStatus`.** Many pods default to `'P'` (passing runs only). If PADB returns exit 0 but writes no CSV, add `"TestRun_RunStatus": "{All}"` to the job's `subex` block.
3. **`Environment_TestStep`.** Must be `{All}` (or an explicit list including non-Room steps) to get anything out of `distribution`, `env_coverage`, or `summary` — a `'Room'`-only extract silently disables all three (this is exactly what went wrong in MaxPower2, fixed in MaxPower3).
4. **One measurement value per analytic.** The tool auto-picks the first numeric column after frequency, silently, with no warning if there are two. If your analytic has two measurement columns side by side, split it into two analytics with two CSVs.
5. **Group string design.** Filter dropdowns and serial identification come entirely from parsing the `Group` column (`Key: Value` pairs, 2+ space separated). Include a `Serial Number` key for per-DUT filtering, a `Temp` key for temperature-grouped box plots, and 2–20-valued condition keys for filter dropdowns. See `PADB_Analytic_Requirements.md` §3 for the exact detection rules.
6. **Spec limits.** If the pod has no `Lower Limit`/`Upper Limit` columns (`Limits_YLimit=None`) but the measurement is conceptually one-sided, set `"spec_direction": "lo"` or `"hi"` explicitly in the plot job JSON — auto-detection resolves to `"none"` with no limits data and no spec line will ever show otherwise. (Added for MaxPower3; not needed for the dBc spur pods, which do have limits in the CSV.)
7. **CSV filename matching.** `OutputConfig_OutputFile` stem should closely match `AnalyticName` (spaces→underscores, same word order) for `csv` substring matching to work. When it doesn't, use `csv_file` with the exact filename instead — `csv_file` always takes precedence.
8. **Run `--dry-run` first**, then a full run, then check `results\padb\` for the CSVs actually produced before writing `secondary_plots` / view entries.

---

## Common gotchas (condensed — full list in `CLAUDE.md`)

- **PADB-R.exe is a WinForms app.** Never `capture_output=True` — hangs waiting for a GUI message loop. Needs a real desktop session, not headless SSH.
- **`_collect_padb_outputs()` "no new files" message is expected**, not an error, whenever `-dir` is set — CSVs land directly in `results/padb/`, not R-Plots.
- **Trace count must be fixed per condition** in any interactive plot's `update()` — `Plotly.react()` matches traces by index; a variable trace count misattaches fill bands.
- **`type:'scattergl'` silently drops `fill:'tonexty'`** — TI bands must use `type:'scatter'`.
- **Plotly.js must load in `<head>`**, never after the plot div, or inline `Plotly.newPlot()` calls fail silently.
- **Toggle panels use `style.display`, not CSS class toggling** — class toggling silently fails in these embedded pages.
- **Never embed literal CRLF/LF in a JS string literal in Python source** — always the 4-char escape sequence, verified byte-by-byte if unsure.

---

## Two proven measurement families — expect a third to surface new gaps

- **dBc spurs** (Close-In, Clock Leakage, Line-Related, Harmonics/Sub-Harmonics): spec limits present in CSV, all six V2 views work with no overrides needed.
- **dBm power** (MaxPower2 → MaxPower3): one-sided spec, no limits in the pod, needed `Environment_TestStep={All}` fix and the new `spec_direction` override.

If onboarding a pod that's neither, don't assume full generality — check spec-limit presence, temperature-step extraction, and one-measurement-per-analytic before trusting the output.

---

## When reviewing generated HTML with a user

They will describe or screenshot what they see; you edit `padb_plots.py` (or the relevant job JSON) directly — this is normal iterative work, not a special mode. Use `--plots-only` to regenerate fast (seconds) without re-extracting from PADB. Check `QA_Checklist.md`'s known-limitations section before treating unusual-looking behavior as a bug (e.g. no serial filter on `de_summary`/`summary` is by design, not missing).
