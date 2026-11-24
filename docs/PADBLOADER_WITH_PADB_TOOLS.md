# Using PadbLoader with padb-tools

PadbLoader pulls test data **out of the PADB Oracle databases**. padb-tools turns that data into
**analysis objects and interactive plots**. This guide shows how to use the two together, from
extraction to a plot someone else can open.

> **Which version?** Everything marked **(v2.5)** works in the released tools today (the
> `v2.5` tag, `develop` branch, the copy on the share). Everything marked **(v3.0)** is on the
> `v3.0` development branch only and is not on the share yet.

---

## 1. Who does what

| | PadbLoader | padb-tools |
|---|---|---|
| **Job** | Extraction and scheduling | Analysis objects and plotting |
| **Talks to Oracle?** | Yes - you sign in with your own Oracle account | **No** - never, in any mode described here |
| **Input** | PADB servers (date range, family, device, suite, serial, algorithm, ...) | A **Parquet** file from PadbLoader (or a PADB scatter CSV) |
| **Output** | DBDIF / CSV / **Parquet** files | Plot jobs (`*_v2_job.json`), interactive HTML views, an index page, optional PDF report, the large-data viewer |

```
 PADB Oracle  --->  PadbLoader  --->  data.parquet  --->  padb-tools  --->  plots + index.html
 (servers)         (extract,          (the contract:     (analysis        (open from a folder
                    schedule)          one row per         objects +        or a share)
                                       test point)         plots)
```

The Parquet file is the **only** thing passed between them. Neither tool needs the other's code
to run, and padb-tools' analysis never calls PADB-R.exe or Oracle.

---

## 2. What you need

- **PadbLoader**: the zip (`PadbLoader-<version>-win-x64.zip`) - unzip anywhere, run
  `PadbLoader.Web.exe`, browser opens at `http://localhost:5177`. Windows only; needs network
  access to the PADB servers and your own Oracle account. (Details: PadbLoader's `README.md`.)
- **padb-tools**: Python 3 (3.14 on the development PC) with `numpy pandas pyarrow scipy plotly duckdb flask`
  (the PDF report also needs `playwright pypdf`). The tools folder is on the share:
  `\\srsnas01...\SG6311A\padb-tools\tools`.

---

## 3. The five-step path

1. **In PadbLoader**, pick the server, date range and filters, choose the **Parquet** format and
   run the extraction (Section 4).
2. **Find the file** - by default under `%LOCALAPPDATA%\PadbLoader\Exports\PadbLoader Data\`
   (the Data Source Editor shows the export folder; scheduled runs go to a `Scheduled\` subfolder,
   Section 6).
3. **Give it to padb-tools** - drop it on the web app, or run one command (Section 5).
4. **Open the result** - the results folder's `index.html` links every view; for very large
   data use the viewer (Section 5.4).
5. **Share it** - copy or publish the results folder (Section 5.5).

---

## 4. In PadbLoader: producing a good Parquet file

1. Start `PadbLoader.Web.exe`, choose a PADB server and **sign in**.
2. In the **Data Source Editor** set the **date range** and narrow the runs (family, device,
   suite, serial number, algorithm, ...). Smaller is faster to extract and to plot - pull what the
   analysis needs.
3. Choose the export format **Parquet** (the default; "DBDIF + Parquet" also writes the
   Parquet). padb-tools reads the Parquet; DBDIF and CSV are not used by it.
4. Run the extraction. The file lands in the export folder (shown in the editor; change it with
   **Export folder**).

**What is in the Parquet file** (so you know what padb-tools will see): **one row per test
point.** Fixed columns include `value` (the measurement), `algorithm`, `result`, `units`,
`limit_upper` / `limit_nominal` / `limit_lower`, `serial`, `device`, `step` (test step, e.g.
`Room`), `run_id`, `run_datetime`, `run_status`; plus **one column per condition** - `c_*` for a
test point's own conditions, `r_*` for run-level ones. Labels and units are kept in the file's
metadata.

> Do **not** use PadbLoader's CSV export as padb-tools input: it is in a different (row-per-field)
> layout without serials, limits or run information. Use Parquet.

---

## 5. In padb-tools: from the Parquet to plots

padb-tools makes **one analysis per algorithm** in the file. If an algorithm has several results
in different units (for example a spur level in dBc plus the carrier and impurity power in dBm),
they are combined into one analysis with a **Result** filter, with the main result pre-selected,
so units are never mixed on one axis until you tick another result.

### 5.1 Web app (v2.5) - easiest

1. Double-click `webapp\Start_web.bat` (opens `http://127.0.0.1:5000`).
2. **Drop the `.parquet` on the drop zone** (or "use a parquet already on disk" to avoid copying
   a large file).
3. You get a table of the algorithms in the file (rows, units, limits, swept x). **Tick** the ones
   you want and the **plots to generate** (every applicable view starts ticked; on a wide sweep
   the heavy views are ticked with a warning).
4. Choose where to publish (a share path or folder name - or leave both empty to keep results
   local) and press **Generate Plot Jobs**. With "Run the plots now" ticked, the jobs are queued
   and shown in *Running Jobs* with a live log.

### 5.2 Command line (v2.5)

```powershell
# what is in the file?
py padb_loader_adapter.py data.parquet --list

# make the analysis objects (jobs) + data, then build the plots
py padb_loader_adapter.py data.parquet --out C:\data\results --no-publish
py padb_v2.py C:\data\<name>_<algorithm>_v2_job.json --no-publish
```

Useful options: `--algorithm "text"` (only algorithms containing the text), `--views
scatter,summary` (choose views), `--split-results` (one analysis per result instead of a
Result filter), `--x-col c_<name>` (override the swept x), `--force` (overwrite existing job files;
without it an existing, hand-edited job is kept).

### 5.3 One call, designed for another tool to use (v3.0)

`padb_analyze.py` does the above in one step and is the interface PadbLoader (or any program in
any language) can call. It reads a request JSON and writes a result JSON; progress goes to
stderr, the result to stdout.

```powershell
py padb_analyze.py request.json --result result.json
# or without a file:
py padb_analyze.py --input data.parquet --out C:\data\results --views scatter,summary
```

```json
{ "schema": "padb-analysis-request/1",
  "input": "C:/data/extract.parquet",
  "out":   "C:/data/results",
  "views": ["scatter", "stat_summary"],
  "limits_file": "C:/data/limits.json",
  "publish_to": "" }
```

Only `input` and `out` are required. The result lists, per analysis, the job file (the analysis
object), the results folder, `index.html`, the views built and `ok` / `failed`. Exit code 0 = all ok,
1 = an analysis failed, 2 = a malformed request.

`py make_analysis_bundle.py <folder>` packages exactly the analysis code that call needs
(no extraction or scheduling code) so it can be shipped alongside PadbLoader.

### 5.4 Very large data (v2.5)

A giant analysis (millions of points, or thousands of x values) may not open as a single HTML
page. The results folder then also has a compact Parquet and the **large-dataset viewer**:
`py padb_viewer.py <results folder>` (or `PADB_Viewer.exe` dropped into that folder). It filters on
the server, so it stays fast, and can render the full interactive views for just the window you
are looking at. Wide sweeps (more than 300 distinct x values) are given a safe default of
`scatter` + `summary` for exactly this reason.

### 5.5 Sharing

The results folder is self-contained - no server needed to open `index.html`. With the web app,
publishing copies the folder to the share path you give it. With `padb_analyze.py`, nothing is
published unless the request sets `publish_to`.

---

## 6. Scheduled extractions

PadbLoader can run an extraction on chosen days and times (a side panel on the Data Source
Editor). Things that matter for padb-tools:

- **Where files go:** `<export folder>\PadbLoader Data\Scheduled\<schedule name> - <owner>\`,
  one new file per run named `<schedule name>_<yyyyMMdd-HHmmss>.parquet` (the stamp includes
  seconds). PadbLoader can keep only the newest N runs per schedule.
- **A run needs an open PadbLoader page signed in as the schedule's owner** - credentials are
  never written to disk, so PadbLoader (and its browser page) must be running when the run is due.
- **Partial files:** a failed run deletes its partial output, and a run in progress works in a
  `.staging-*` folder. Only treat a `*.parquet` directly inside the schedule folder as a finished
  file, and wait until its size has stopped changing before reading it.

**Plotting each new file automatically**

- *Today:* a Windows scheduled task that runs a small script - take the newest `*.parquet` in the
  schedule folder and run `padb_analyze.py` (v3.0) or the adapter + `padb_v2.py` (v2.5) on it.
- *Planned (v3.0, not built yet):* `padb_watch.py`, a directory watcher that does this by itself:
  polls a folder, waits for a file to be complete, keeps a ledger so nothing is processed twice,
  builds each file into its own results folder, and can run once per call (for Task Scheduler or
  PadbLoader) or continuously.

---

## 7. Settings files

PadbLoader can export its settings (a JSON file with the format id `padbloader-settings`: the
extraction selections and the analysis window's chart settings). **padb-tools does not read these
yet**; keep the settings file next to the data for reproducibility. Reading it as analysis hints
(which results, which x column, default grouping) is a possible later addition.

---

## 8. What padb-tools does with the data (so the plots make sense)

- **Conditions** (`c_*` / `r_*`) become filters. A condition with more than 100 distinct values (for
  example a per-row ID) is kept out of the grouping - it would make tens of thousands of
  conditions.
- **Limits** (`limit_upper` / `limit_lower`) drive the limit line and the Passing / Failing filter.
- **Test step** becomes the Temperature. A step such as `Room` or `85.0 Deg C` is understood;
  the temperature-comparison views (Distribution, Env Coverage) are offered only when the data
  has more than one test temperature. A step name that is not a temperature (for example "Hot")
  counts as Room and the importer warns.
- **Wide sweeps** get a safe default view set (see 5.4). You can tick the heavy views back on, at
  the cost of build time and page size.
- **Your own limits:** a `limits_file` (see `PADB_Tools_Guide.md`, *Editable limit overrides*)
  replaces the data's limits over x-ranges for the plots and pass/fail, without touching the
  data.

---

## 9. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| "Not a recognised parquet" | The file is neither a PadbLoader extract (needs `algorithm` and `value` columns) nor a padb-tools table. Re-export from PadbLoader as **Parquet**; do not use the CSV export. |
| "No analyses were generated" | The filters left nothing usable (no numeric measurements, or the chosen algorithms are not in the file). Run `--list` to see what is there. |
| Boxplot / summary build takes very long | A wide sweep (hundreds of x values). Keep `scatter` + `summary`, or narrow the x range; the heavy views are hours long on thousands of x. |
| Two units on one axis | You ticked several results of one algorithm. Use the **Result** filter to pick one, or `--split-results` for separate analyses. |
| A scheduled file is half-written | You read it too early. Wait for the file size to stop changing and ignore `.staging-*` folders. |
| Negative test temperatures look positive | **Known limitation:** the loader reads a step such as `-40.0 Deg C` as +40 C (the minus sign is dropped). Check the temperature labels of any negative step before relying on them; the measured values themselves are not changed. |
| The web app shows a button that does nothing | The page is newer than the running server - restart `Start_web.bat` and press Ctrl+F5. |

**Not verified:** a combined multi-source PadbLoader file (several servers/schemas in one extraction,
where only `(server, schema, run_id)` identifies a run) has not been tested with padb-tools.
Plot each source's file separately until that is checked.

---

## 10. Command and file cheat sheet

| I want to... | Do this |
|---|---|
| Extract data | PadbLoader -> Data Source Editor -> Parquet |
| See what is in a Parquet | `py padb_loader_adapter.py data.parquet --list` |
| Plot it with clicks | `webapp\Start_web.bat` -> drop the file |
| Plot it from the command line (v2.5) | adapter `--out ...`, then `padb_v2.py <job>` |
| Plot it in one call (v3.0) | `py padb_analyze.py request.json --result result.json` |
| Explore a giant dataset | `py padb_viewer.py <results folder>` |
| Use my own limits | `limits_file` in the job, or the viewer's **Edit limits** |
| Package the analysis for another tool (v3.0) | `py make_analysis_bundle.py <folder>` |

| Item | Where |
|---|---|
| PadbLoader exports | `%LOCALAPPDATA%\PadbLoader\Exports\PadbLoader Data\` (or the folder set in the editor) |
| Scheduled output | `...\PadbLoader Data\Scheduled\<name> - <owner>\<name>_<stamp>.parquet` |
| PadbLoader settings files | `...\PadbLoader Settings\` |
| padb-tools jobs / results | next to the job file / the job's `results_dir` (or `<out>\jobs`, `<out>\results\<name>` for `padb_analyze`) |

More: `PADB_Tools_Guide.md` (full reference), `Interactive_Plots_User_Guide.md` (using the plots),
`V3_PLAN.md` (the v3.0 design, including the analysis/extraction separation).
