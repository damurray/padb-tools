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

### `spec_direction` job.json key and the live TLL-direction selector (added for MaxPower3, finalized 2026-08-04)

`stat_summary` (V2) auto-detects whether to show the lower spec line, upper spec line, both, or neither, based on whether any `freq_stats` entry has `spec_lo`/`spec_up` populated (`padb_plots.py` ~line 4072). MaxPower3's pod has no spec limits at all (`Limits_YLimit=None` everywhere), so auto-detection always resolves to `"none"` — no pass/fail line would ever show, even though MaxPower is conceptually a lower-spec-only (guaranteed minimum power) measurement. `"spec_direction"` in job.json (`"lo"`/`"hi"`/`"both"`/`"none"`/`"auto"`) exists to override this.

**Final rule, confirmed by the user and applied uniformly in `summary` (`_build_summary_html`) and `stat_boxplot`/boxplot (`_stat_boxplot_interactive`/`_build_box_interactive_html`) — both in `padb_plots.py`:**

1. **If the CSV itself has a real `Upper_Limit`/`Lower_Limit`, that detected value always wins.** No selector is shown, full stop — this is true even if job.json also sets an explicit `"spec_direction"`. Data beats config.
2. **If the CSV has no limit at all, a live "TLL display: Both / Upper only / Lower only" radio selector is *always* shown** — never hidden, regardless of whether `spec_direction` is `"auto"` or explicit. `spec_direction` (or `"both"` if unset) only sets which radio is *pre-checked* by default; it does not remove the viewer's ability to switch. (An earlier same-day version of this rule incorrectly hid the selector whenever `spec_direction` was explicit — corrected after user feedback: "explicit config should only set the default, not remove the option to override live.")
3. **The Data-filter range control is two independent radios, not one relabeled radio**: `"Upper limit"` and `"Lower limit"` are separate options (values `range_hi`/`range_lo`), each shown only when relevant to the *currently selected* TLL direction (both shown when direction is "Both"). A single relabeled radio can't represent two independent bounds simultaneously — this was the actual bug behind an early "why does this still show Upper Limit" report. Filter semantics are literal, not inverted: "Lower limit" cuts off (hides) data *below* it, "Upper limit" cuts off data *above* it — matches whichever side the TLL selector is currently showing.
4. **Boxplot's filter mechanics differ from summary's**: boxplot trims raw sample points before computing Q1/Q2/Q3/whiskers (`d.v>rhi`/`d.v<rlo` in `buildBoxTraces`/`buildPortSerialTraces`/`updateStatsTable`/`_collectOutliers`), where summary's filter hides/shows whole condition traces (`max_data[i]<=limit`/`min_data[i]>=limit`). Both got the identical two-radio, direction-aware treatment, implemented independently since they're separate JS templates (`_SUMPLOT_JS` vs `_STAT_BOXPLOT_INTERACTIVE_JS`).
5. **`summary`'s Results Table and CSV export are also direction-aware** (`buildTable()`/`exportTableCSV()`): only show TTL↑/Spec Hi/Margin↑ columns when Upper/Both is active, TTL↓/Spec Lo/Margin↓ when Lower/Both is active. (Boxplot's own Statistics Table has no equivalent gap to fix — it's purely descriptive statistics (Q1/Median/Q3/whiskers/normality), with no spec-direction-dependent columns at all.)

**Current MaxPower3 job settings** (all pod-specific, not tool-wide defaults — confirmed with the user that other pods keep whatever their own data/config implies): the 4 hand-written V2 jobs (`MaxPower3_Leveled_Log_v2_job.json`, `MaxPower3_Leveled_Linear_v2_job.json`, `MaxPower3_Unleveled_Log_v2_job.json`, `MaxPower3_Unleveled_Linear_v2_job.json`) all have `"spec_direction": "lo"` (selector shown, defaults "Lower only"). The legacy V1 job `maxpower3_leveled_linear_job.json` has `"spec_direction": "auto"` (selector shown, defaults "Both") — a separate, earlier user request to match `Leveled_Log`'s config at the time, left as-is. `maxpower3_unleveled_linear_job.json` still has `"lo"` from the original 2026-07-16 fix and was intentionally left alone. **For a new pod's canonical "no spec limits, one-sided measurement" example, use the 4 `MaxPower3_*_v2_job.json` files, not `maxpower3_leveled_linear_job.json`** — the latter no longer demonstrates the explicit-direction pattern.

Verification pattern for this class of fix: render the HTML headlessly and dump the post-JS DOM rather than asking the user to check a browser or guessing from source —
```
"$EDGE" --headless --disable-gpu --virtual-time-budget=15000 --user-data-dir=$(mktemp -d) --dump-dom "file:///path/to/file.html" > dump.html
```
Plain `--dump-dom` without `--virtual-time-budget` can hang indefinitely on a large embedded-Plotly page; always pair with a virtual-time-budget and an outer shell `timeout`.

**This was not previously documented** — added to `PADB_Tools_Guide.md` and `PADB_Analytic_Requirements.md` on 2026-07-16, revised 2026-08-04.

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

## `mode` job.json key (added 2026-08-03)

Three values, default preserves every pre-existing job.json byte-for-byte:

- `"legacy"` (default when the key is omitted) — today's V1 behavior: `run_secondary_plots()` + `make_index_html()`, unchanged.
- `"simple"` — a direct, static replacement for the old internal Perl `PADB::Simple` tool. No custom plotting or statistics: `make_run_pod()` forces `OutputConfig_OutputGraph=1`/`OutputConfig_GraphFormat=png,pdf` inside every `[PADBAnalyticN]` section (only when this mode is set — no-op otherwise), so PADB-R.exe itself renders each analytic's native PNG/PDF. `padb_simple.py`'s `make_simple_gallery_html()` then wraps those native renders in a bare HTML gallery (one card per PNG, a metadata table dumped verbatim from `_run.pod`'s own `[Extract]`/`[PADBAnalyticN]` settings, download links to `.sao`/`.pod`/`.txt`/`.csv`) — written to the same `results_dir/index.html` path V1/V2 already use, not a nested index-of-indexes.
- `"interactive"` — label only, documents that this job feeds the existing V2 two-command flow (`padb_run.py` extract, then a separate `py padb_v2.py ... --csv ...`). No dispatch change: V2's job.json schema is structurally different (`csv_path`/`views`/`publish_to` vs V1's `pod`/`subex`/`secondary_plots`), and `generate_report()` takes one CSV per call while a V1-style `analytics` list can yield N CSVs, so wiring this in-process was a deliberate scope cut, not a missed requirement. Setting this mode just prints a one-line "run padb_v2.py with this CSV" hint after extraction.

**Bug fixed 2026-08-05 — native renders leaking into non-Simple modes:** `make_run_pod()` only ever forced `OutputConfig_OutputGraph` **on** (for `"simple"`); it never forced it **off** for `"legacy"`/`"interactive"`. Neither of those pipelines reads a native render — but a pod previously tuned for Simple mode (like the `MaxPower3` family — see the "Verified no-op case" note below) keeps `OutputConfig_OutputGraph=1` baked into every analytic regardless of what mode a *later* job.json against that same pod uses, so PADB-R silently re-rendered a full native PNG/PDF gallery on every Interactive-mode extraction too — wasted render time, and confusing PNG/PDF files sitting next to the CSVs that could be mistaken for the actual result (a real case: `MaxPower3_v2_run_job.json`, reported via the web app). Fixed with a new `disable_native_render` flag on `make_run_pod()` (`_DISABLE_RENDER_KEYS = {"OutputConfig_OutputGraph": "0"}`), called as `disable_native_render=(mode != "simple")` alongside the existing `force_native_render=(mode == "simple")` — the two are mutually exclusive by construction. Verified via `--dry-run` on both a real Interactive-mode job (now `OutputConfig_OutputGraph=0` in the `_run.pod` copy) and a real Simple-mode job (still `=1`, unchanged).

Both `"simple"` and `"interactive"` also get a `results_dir/HOW_TO_USE.txt` written by `write_mode_guidance()` — a short, mode-aware text explainer (what the output is, what it can't do, how to switch tiers). Not written for `"legacy"` — no behavior change to existing jobs.

**Known metadata gotcha:** the metadata table's `ExtractionOptions_AllRunResults` field was renamed to `ExtractionOptions_LastRun` in newer PADB pods (confirmed: `MaxPower3.pod`, PADB Version 4.12.2.8, uses the new name; the legacy PADB::Simple output on the share, PADB Version 3.1.2, used the old one). `build_metadata_table_html()` in `padb_simple.py` checks both key names — if another renamed field like this ever turns up, add it to that field's candidate-key tuple in `_METADATA_FIELDS` rather than special-casing it.

**Verified no-op case:** `MaxPower3.pod`'s 6 analytics already have `OutputConfig_OutputGraph=1`/`GraphFormat=pdf,png` set, so forcing them in Simple mode is a true no-op there — confirmed by diffing `make_run_pod()` output before/after the change on real pods (`MaxPower3.pod`, `test1.pod`, `flat.pod`) with `force_native_render=False`, byte-identical in every case. Other pod families haven't been checked — if a pod currently renders natively off, flipping it on in Simple mode adds PADB run time/disk and could surface a previously-suppressed render failure.

---

## `subex` relative-date sentinels (added 2026-08-03)

Any `subex` value can be a placeholder resolved to PADB's `YYYY-MM-DD` format at the moment the job actually *runs*, not whenever job.json was written — this is the capability the old PADB::Simple tool had that was missing here:

```json
"subex": {
    "Device_MinDate": "8 weeks ago",
    "Device_MaxDate": "today"
}
```

Supported forms, matched case-insensitively (`_resolve_date_sentinel()`, `padb_run.py`): `"today"`, and `"N day(s) ago"` / `"N week(s) ago"` / `"N month(s) ago"` / `"N year(s) ago"` for any integer N. Month/year arithmetic is real calendar arithmetic (via `calendar.monthrange`), not a 30/365-day approximation. Resolution happens once, inside `load_job()`, right after the friendly list-field → subex merge — a literal date string (`"2026-07-31"`) or any other subex value (`"{All}"`, a quoted list) that doesn't match one of these patterns is returned unchanged, so this is safe to leave wired in unconditionally. Verified against real pods: `4 weeks ago`, `1 day ago`, `3 months ago`, `1 year ago`, and `today` all resolve correctly, and non-date subex values pass through untouched.

Useful for recurring/scheduled jobs (`schtasks`/`padb_scheduler.py`) that should always pull "the last N weeks" rather than a range that goes stale the day after the job.json is written.

---

## `_collect_padb_outputs()` clears stale files before copying fresh ones (added 2026-08-03)

`results_padb` (`results_dir/padb/`) used to accumulate forever — every real run's `_collect_padb_outputs()` call only ever copied newly-matched files *in*, never removed anything, so repeated runs of the same job piled up duplicate PNGs from past runs on top of the current ones. This bit Simple mode hard: a job run 3 times in one day had **273 PNG files** in `results_padb` for 2 analytics, most of them stale leftovers, producing a gallery with ~270 duplicate-looking cards instead of the correct handful.

**Fix:** before copying, `_collect_padb_outputs()` now clears any existing `results_padb` file whose stem matches a known analytic stem **that also has fresh output this run** (`padb_run.py:246`). Stems with zero fresh matches in `padb_output_dir` this run are left untouched — this is the one case that had to be handled carefully: the clock-spurs job relies on a CSV manually placed in `results_padb` *forever*, specifically because PADB never writes a matching file to R-Plots for that analytic (see the Clock spurs gotcha below). A naive "delete everything matching a known stem" fix would silently wipe that workaround on the next real run; scoping the clear to "stems with fresh output this run" preserves it.

Verified in production: a real re-run of `spectral_history_closein_job.json` logged `Cleared 286 stale file(s) from a previous run` and `Collected 14 file(s) from R-Plots/` — the correct count, matching what a single clean collection pass produces, with none of the historical bloat.

---

## `padb_make_job.py` — job.json generator (added 2026-08-03)

Generates a `<pod_stem>_job.json` next to each given `.pod` file, using the same template every hand-written job.json in this project already follows (`mode`, `results_dir`, `padb_exe`, output/logs dirs, `publish.destination`).

```
py padb_make_job.py pod1.pod pod2.pod --module MiniMoab
py padb_make_job.py pod1.pod --module VSWR --min-date "8 weeks ago" --max-date today
py padb_make_job.py pod1.pod --no-publish          # local results only, no publish key
py padb_make_job.py pod1.pod --module X --force     # overwrite an existing job.json
```

- `--module` names the subfolder under `--publish-root` (default the `PADB-Simple` root). **Required unless `--no-publish` is given** — deliberately not auto-derived from the pod filename, since guessing the wrong subfolder name has already happened twice in practice (`MiniMoab`, `ReferenceNominalSpecs` vs `Reference`) and silently publishing to the wrong place on a shared network location is a worse failure mode than a required flag.
- `--min-date`/`--max-date` are written into `subex` verbatim (including sentinel strings like `"today"`/`"8 weeks ago"` — resolved later by `load_job()`, not by this script). Omit both and no `subex` key is written at all, leaving the pod's own baked-in `[Extract]` date range untouched — this was the specific design ask that prompted the script.
- Skips a target file that already exists unless `--force` is passed — won't clobber a manually-tuned job.json.
- All other defaults (`padb_exe`, `padb_output_dir`, `padb_logs_dir`, `padb_timeout=7200`) match this session's established values and are overridable via their own flags.
- **Fixed 2026-08-10**: `description` was hardcoded `f"SG6311A {stem} — ..."` regardless of the pod's actual instrument. Caught when a user pointed out that `MCS_Spurs_Example_simple_results\index.html` (an MCS pod, `Device_Device='M9484C'`) showed "SG6311A MCS_Spurs_Example — Simple mode" in both its `<title>` and page body — visibly wrong, not just an internal label. Fixed by reading `Device_Device` from the pod's own `[Extract]` section via `parse_pod_sections()` (already used for this in `padb_make_v2_job.py`, see below) and using that instead of a literal; falls back to no device prefix at all if the field is missing or empty, rather than guessing. Same fix applied to `padb_make_v2_job.py`'s `description`/`title_prefix`/`index_title` (see that section) — those are more consequential since `title_prefix` becomes the literal generated HTML filename prefix (e.g. every file this session was named `SG6311A_<analytic>_<view>.html`, correct only by coincidence since this session's pods really were SG6311A). The per-analytic metadata table (`padb_simple.py`'s `build_metadata_table_html`) already showed the correct `Device_Device` value the whole time — only the page title/header text was wrong.

---

## `unique_output_filenames` job.json key (added 2026-08-04)

Some pods have multiple analytics sharing one `OutputConfig_OutputFile` despite having distinct `AnalyticName`s — confirmed in the wild: `Harmonics_and_Subharmonics_Spec_Setting_Data2_review.pod` has 13 of 19 analytics sharing one `OutputFile` and 2 more sharing another, all 19 `AnalyticName`s distinct. `find_csvs()` already falls back to `AnalyticName` as the differentiator in this case (documented there), but that's a downstream workaround — the collision still exists in what PADB actually writes, and anything that predicts a CSV filename *before* extraction (like `padb_make_v2_job.py`) has to guess right.

Set `"unique_output_filenames": true` in job.json to fix it at the source instead. `make_run_pod()` (`padb_run.py`) then forces every analytic's `AnalyticName` **and** `OutputConfig_OutputFile` to the same slug of that analytic's own original `AnalyticName`, patched only into the `_run.pod` copy — the original `.pod` is never touched, same convention as every other `make_run_pod()` flag.

**Guaranteed, not just usually true:** slugifying can itself introduce a *new* collision when two `AnalyticName`s differ only by punctuation style — a real case in the same pod: `"Sub-Harmonics Summary 50MHz-20GHz"` (analytic 11) and `"Sub-Harmonics_Summary_50MHz-20GHz"` (analytic 18) both slugify to `Sub_Harmonics_Summary_50MHz_20GHz`. `make_run_pod()` detects any slug that isn't unique after the first pass and appends that analytic's own index (`_11`, `_18`) — confirmed both via direct unit testing and against a real PADB-R.exe run (all 19 analytics wrote distinct, correctly-named CSVs; analytics 11/18 correctly got the index suffix).

**Ordering consequence:** because this can rename fields the rest of the pipeline depends on for file-collection stem-matching, `main()` now calls `make_run_pod()` *before* `parse_pod_analytics()`, and parses from the `_run.pod` copy instead of the original pod path. Verified this reorder is a no-op for every existing job (byte-identical output when no flags are set, so parsing from either file gives identical results) — it only matters once `unique_output_filenames` or a future similar flag is actually used.

`padb_make_v2_job.py` sets this automatically whenever it detects an `OutputConfig_OutputFile` collision while generating a pod's job files, and predicts every `csv_path` using the identical slug + collision-disambiguation logic, so the generator and the runtime patching can't drift out of sync — see below.

---

## `force_output_csv` job.json key (added 2026-08-04)

Real case: a CW Closed Loop pod's single Type=80 Scatter analytic had `OutputConfig_OutputCSV=0` in the pod itself. PADB-R happily rendered native PNG/PDF pages (proving real data existed) but wrote **zero** CSVs — completely silent, since `run_padb()` returns code 0 either way. V2/Interactive mode is fundamentally built on a Type=80 CSV, so this pod could never feed the V2 pipeline as-authored.

Set `"force_output_csv": true` in job.json to fix it at the source. `make_run_pod()` (`padb_run.py`) forces `OutputConfig_OutputCSV=1` on every Type=80 (Scatter) analytic in the `_run.pod` copy only — scoped to Type=80 deliberately, since other analytic types may have CSV output disabled on purpose. Existing values are replaced in place; a missing key is appended when the section ends — same convention as `force_native_render`/`unique_output_filenames`.

`padb_make_v2_job.py` sets this automatically whenever a Type=80 analytic has `OutputConfig_OutputCSV=0` (parsed via `parse_pod_analytics()`'s existing `output_csv` field), printing a `NOTE:` explaining why.

**This is a genuinely separate problem from date-range issues** — a run that returns 0 CSVs in a few seconds can be either "no data in the requested window" (real, expected) or "this analytic doesn't write CSVs at all" (a pod configuration gap `force_output_csv` fixes). Distinguish by checking `OutputConfig_OutputCSV` in the pod and/or re-running with the pod's own baked-in date range as a sanity check — a run that takes tens of seconds and produces native PNG/PDF but still 0 CSVs points at the CSV-disabled case, not the date-range case.

---

## Spec-limit segment tab-through (added 2026-08-06)

All 6 V2 views have a "Segment by" selector (Spec / Limit / Uncertainty) plus Prev/Next buttons that jump the frequency range to each contiguous band of a frequency-varying spec — e.g. a datasheet spec that steps from -100 dBc to -94 dBc to -88 dBc as frequency increases: `accuracy_vs_freq`/`render_scatter` (scatter), `stat_boxplot` (boxplot), `stat_summary`, `render_summary` (summary, padb_v2.py), `render_env_coverage` (env_coverage, padb_v2.py), and `_build_env_distribution_html`/`render_distribution` (distribution, padb_v2.py).

**Real gap found and fixed a day later (2026-08-07):** the first pass added this to `distribution()` in padb_plots.py — a real, working function, but the *wrong* one. `padb_v2.py`'s `render_distribution()` (the function that actually backs the V2 "Distribution (Delta-Env)" tile in the standard 6-view suite) calls a completely different function, `_build_env_distribution_html()` (multi-temp overlaid KDE curves with ΔEnv analysis), which never got the feature. So "all 6 views" was false for a full day — only 5 of 6 real V2 views had it. Found while recapturing training-deck screenshots (the real distribution tile visibly had no "Segment by" control), fixed by adding the same feature to `_build_env_distribution_html` instead/in addition. `distribution()` itself keeps its own copy — it's still real, reachable via V1-legacy `secondary_plots`, just not what the V2 pipeline's "distribution" view actually renders.

**PADB extraction has three separate limit-key pairs you can select as grouping items: `Upper/Lower Limit` (selected by default), `Upper/Lower Uncertainty`, and `Upper/Lower Spec`.** Confirmed against real data: `Upper Limit ≈ Upper Spec − Upper Uncertainty` (Uncertainty ≈ M.U. + ΔEnv). Limit is the *derived* value PADB shows by default — it shifts per-unit with that DUT's own measurement uncertainty, so it's frequently NOT piecewise-constant across frequency and either fragments into extra noisy segments or hides real band structure. Spec is the raw nominal value and is the one that's actually piecewise-constant by frequency.

**For "Segment by: Spec" or "Segment by: Uncertainty" to find anything, the pod's Type=80 analytic extraction must have `Upper Spec`/`Lower Spec` and/or `Upper Uncertainty`/`Lower Uncertainty` added as grouping items** (open the pod in PADB-R.exe, add them to the analytic's grouping, re-save) — most existing pods only have the default `Upper Limit`/`Lower Limit`. Without this, those two selector options show zero segments (Prev/Next bar stays hidden); "Segment by: Limit" still works off the always-present `Upper_Limit`/`Lower_Limit` CSV columns.

Implementation:
- `_load_scatter_csv`/`_load_scatter_for_stats` (padb_plots.py) parse `Upper Spec (<=): ...`/`Lower Spec (>=): ...` and `Upper Uncertainty (<=): ...`/`Lower Uncertainty (>=): ...` straight out of the raw `Group` text into `Spec_Hi`/`Spec_Lo`/`Unc_Hi`/`Unc_Lo` columns via `_extract_group_field()`, alongside the existing `Upper_Limit`/`Lower_Limit` CSV columns.
- `getSpecMaskByKey(dataArr, key)` (scatter/distribution) and its boxplot equivalent read a specific field pair with **no automatic fallback** — picking a key the pod's extraction never selected legitimately produces zero segments, which is itself informative. This is deliberately separate from `getSpecMask()`'s own automatic Spec-preferred-else-Limit blend used for the actually-plotted dashed reference line, so the selector can't disturb that existing rendering.
- **Boxplot, stat_summary, summary, and env_coverage are all architecturally different** from scatter/distribution — each pre-aggregates server-side into its own distinct per-condition shape, not a flat per-row array, and each needed its own (structurally similar but not identical) segment-detection function as a result:
  - **Boxplot**: `BOX_DATA` → per-`(condition, temp)` `freq_stats` entries. `_aggregate_box_data_by_temp()` carries Spec/Limit/Uncertainty as **per-point fields on each `vals_detail` entry** (not one value per frequency). Segment recompute mirrors `buildBoxTraces()`'s condition/temp/serial/port/GF filtering (`_boxIsInGf()`'s coarse-key matching).
  - **stat_summary**: `STAT_DATA` → per-condition `freq_stats`, each with a `dut_vals` array of per-DUT `{s, p, v}` dicts. `_aggregate_stat_data()` extends each dict with `spec_hi`/`spec_lo`/`unc_hi`/`unc_lo`/`upper_limit`/`lower_limit`. Segment recompute mirrors `recomputeFreqStat()`'s per-DUT GF check (`_isStatGfExcl()`).
  - **summary** (`render_summary`, padb_v2.py): records use a **2D array** shape — `dut_vals[freq_idx][dut_idx]`, parallel to `dut_info[dut_idx] = {s: serial}`. Added `dut_spec_vals[field][freq_idx][dut_idx]` (one per Limit/Spec/Uncertainty side) built via the same groupby+unstack+reindex pattern as `dut_vals` — pandas' version of "pivot a long list of (frequency, DUT, value) rows into a 2D table," roughly analogous to building a `Dictionary<(double freq, string dut), double>` and then reading it out as a rectangular array in a fixed row/column order — but aggregated with **min/max (tightest-wins), not mean** — unlike the measured Value, these should be constant per (freq, DUT); averaging would silently blend a genuine data conflict (e.g. a datapak error recording two different spec values for the same DUT/frequency) into a meaningless number. Segment recompute (`_sumInclDutIdxs()`) mirrors `getSumCondData()`'s per-DUT serial+GF inclusion logic.
  - **env_coverage** (`render_env_coverage`, padb_v2.py): each DUT (`cd.duts[dutKey]`) already carried a `room`/`deltas` array per frequency; added a parallel `spec` dict (`spec.upper_limit[freq_idx]`, etc.) via `_aggregate_env_coverage_data()`'s new `spec_pivots` (same pivot-table pattern as the existing `room_pivot`, aggregated with min/max for the same tightest-wins reason as summary). Segment recompute reuses `getActiveDuts(cd)` exactly, so it's automatically consistent with whatever serial/port/GF filtering the actual plot uses.
  - **distribution** (`_build_env_distribution_html`, padb_plots.py): the easiest of the five — `RAW_ABS[spurIdx][tempIdx]` already carried per-point `hi`/`lo` (Upper_Limit/Lower_Limit) arrays for its own existing freq/serial-filtered live KDE recompute, so this just needed `spec_hi`/`spec_lo`/`unc_hi`/`unc_lo` added as parallel per-point arrays in the same `_abs_cols` construction — no new aggregation shape at all. No GF exists in this view (delta-env/KDE has no per-DUT exclusion mechanism), so segment recompute only respects the SpurType/serial/port filters, the same ones `update()`'s own raw-recompute path already uses.
  - None of these five had pre-existing per-DUT-per-frequency Limit/Spec/Uncertainty tracking before this work — scatter/boxplot's `Spec_Hi`/`Spec_Lo`/`Unc_Hi`/`Unc_Lo` CSV columns (`_load_scatter_csv`/`_load_scatter_for_stats`) already existed from the Spec/Uncertainty parsing described above; the aggregation functions just hadn't threaded them through to their client-side JSON yet.
- A real bug was found and fixed in the same work: `setFreqBand()` (scatter/distribution) wrote its lo/hi to the range slider's `.value` then read that back into the text boxes — browsers silently snap programmatic `.value` assignment to the nearest `step`, which for a wide frequency range (a coarse step) corrupted the actual filter boundary. Also, segment-index recovery after each `segTab()` call was reading the slider's raw `.value` instead of the text box, which combined with the same snapping caused Prev/Next to appear to get stuck after the first click on data with small segments. Both fixed to read/write the exact float via the text box, matching the pattern `freqTxtChange` already used for the identical reason. Boxplot's `box_freq_lo`/`box_freq_hi` are plain `<input type="number">`, not sliders, so it was never exposed to this bug.
- Every one of the 6 views' page-init sequence builds its first plot via a direct `Plotly.newPlot(...)` call, bypassing `update()` entirely — `_recomputeSpecSegments()` has to be called explicitly in each view's init block too, not just inside `update()`, or the segment bar never appears until the first filter change. Found and fixed for boxplot, stat_summary, summary, and env_coverage (scatter's `_recomputeSpecSegments()` was already explicit in its init sequence from the start). The real distribution view (`_build_env_distribution_html`) is the one exception that needed no fix here — its init sequence already calls `update()` directly (`window.addEventListener('DOMContentLoaded',function(){loadState();update();})`), so the hook inside `update()` was sufficient.
- Verified end-to-end against a real clock-leakage pod (`ClockSpurs_PADBToolTest.pod`) with a genuine 5-level spec staircase (−100→−94→−88→−82→−76 dBc, 8 MHz–20 GHz) and a real datapak anomaly (a handful of serials showing a −65 dBc DAC-Band spec under the wrong SpurType) — see `feedback_npi_data_anomalies` memory for how that anomaly was resolved (not a code bug). All 6 views individually re-verified against the same real dataset after this extension.

**Asymmetric two-sided limit gap found and fixed (2026-08-07):** `getSpecSegments()` originally took one array of `{x,y}` points and built segments purely from value changes in that one array — every call site fed it `hiPoints.length ? hiPoints : loPoints`, i.e. Upper preferred whenever it existed at all, Lower used only as a fallback when Upper was totally absent. For the common case (Upper and Lower stepping at the same frequency band edges, which is virtually every real spec — both come from the same guard-banded limit table) this is indistinguishable from correct. But if Upper and Lower were ever configured to transition at genuinely different frequencies, Lower's own unique transition point would never become a tab stop — Prev/Next would only stop at Upper's boundaries, silently absorbing Lower's transition into whichever Upper-defined segment it happened to fall inside.

Fixed by changing `getSpecSegments()`'s signature to take *both* arrays (`getSpecSegments(hiPoints, loPoints)`) and building the union of both sides' breakpoints: it walks the merged, sorted set of frequencies where either side has a point, carries forward each side's last-seen value independently, and starts a new segment whenever *either* value changes. Each segment now carries `hiValue`/`loValue` separately instead of one ambiguous `value` — `_segLabelText()` renders whichever side(s) are present as `upper: X` / `lower: Y` (previously just `value: X`, which didn't say which side it was even in the one-sided case). One-sided data still works exactly as before (the missing side's `*Points` array is simply empty, so its value stays `null` throughout and only the present side drives segment breaks).

This is the same repeated-per-view pattern as everywhere else in this feature — `getSpecSegments()` is defined 7 times (scatter, legacy `distribution()`, real V2 `_build_env_distribution_html`, stat_summary, summary, env_coverage, boxplot), all textually identical, all fixed identically via one `replace_all` edit. The `var points = hi.length ? hi : lo` line at each of the 7 call sites was deleted; each now calls `getSpecSegments(hiPoints, loPoints)` (or `getSpecSegments(mask.hi, mask.lo)` for scatter/legacy-distribution, which get their arrays from `getSpecMaskByKey()` instead of building them inline) directly.

Verified two ways: (1) `qa_js_segments.py` (new, permanent — promoted from a throwaway synthetic harness after the fix was confirmed) extracts all 7 `getSpecSegments()` copies straight from the current `padb_plots.py` source, asserts they're still textually identical (catches future per-view drift immediately instead of leaving 6 views silently unfixed), then runs one extracted copy under headless Edge against symmetric two-sided, genuinely asymmetric two-sided (Upper breaks at one frequency, Lower at another — confirms 3 segments where the old code silently produced 2), one-sided upper, one-sided lower, empty-both, and a late-starting side — all passing; sanity-checked by running the same test against the old buggy single-array function, which correctly fails 5 of 6 cases, confirming the test has real teeth rather than passing vacuously. (2) Real one-sided ClockSpurs data re-generated end-to-end, confirming zero regression (still 5 segments, same boundaries) and the new label format (`upper: -88` instead of `value: -88`). `qa_padb.py` baseline unchanged (27 PASS / 5 FAIL, same pre-existing failures as always).

---

## Boxplot Global Filter (GF): additive semantics, Export/Import CSV (added 2026-08-06)

**"Set filter as GF" / "Set outliers as GF" / "Set delta outliers as GF" all *add* to the current global filter — none of them replace it.** Every one of these funnels through `_mergeGf()`, which unions new keys into whatever's already stored in `localStorage['padb_v2_excluded']`. Use "Clear global filter" first if you want to start over rather than layer on top of an existing selection. Button hovers now say this briefly; this is the fuller explanation.

**Export GF CSV / Import GF CSV** round-trip the current GF through a human-readable CSV (`Serial,Condition,Temperature,Start_Freq_<unit>,Stop_Freq_<unit>,N_Points`). Import re-merges (adds to, doesn't replace) the current filter, same as the "Set ... as GF" buttons. Key thing to know: **the runtime exclusion check (`_boxIsInGf`, via `_loadBoxGlobalFilter`'s key-coarsening) only ever matches on (serial, condition, temperature) — frequency is dropped at match time**, even though the original GF key format includes a frequency. So:
- The exported `Start_Freq`/`Stop_Freq`/`N_Points` columns are display-only context (what frequency range and how many points the original exclusion happened to cover) — they are *not* used to reconstruct an exact frequency-by-frequency exclusion on import.
- Import re-forms a raw `serial||condKey||temp||freq` key per CSV row (any placeholder frequency, since it's discarded at match time anyway) and merges it via the existing `_mergeGf()` — functionally identical to the original exclusion, even though the exact original frequency points aren't individually recoverable from the summarised range.
- No filesystem check gates the Import button (e.g. "only enable if a GF CSV already exists in the results folder") — a static HTML page opened via `file://` has no API to probe the local folder ahead of time, and since the button is just a native file-picker trigger, clicking it costs nothing if there's nothing to import.
- "Copy PADB Filter" (a best-effort `'Serial Number' NOT IN {...}`-style expression for pasting into PADB's own filter box) is flagged **under development** in its hover — the generated expression may not exactly match PADB's own filter syntax in every case.

---

## "Group by" — collapsing fragmented conditions (stat_summary, summary, env_coverage; added 2026-08-06)

Real motivation: when a pod's extraction includes `Upper Spec`/`Upper Uncertainty` as grouping items (see the segment tab-through section above), "condition" in these three views is the *full combination* of every Group key — including the per-unit-noisy `Upper Limit` text. A pod with 5 real SpurTypes but per-unit Limit variation can explode into 150+ near-duplicate legend entries that differ only in Limit/Uncertainty digits. "Group by" lets you collapse on a *single* dimension (e.g. "SpurType" alone) instead of the full combination — confirmed on real data: 151 fragmented conditions → 5 real SpurTypes.

Each view needed its own pooling function since each has its own aggregate shape (same reason the segment tab-through feature needed four different implementations) — but they share one **exactness principle**: because "condition" is the full key combination, a given DUT's data falls under exactly one constituent condition when grouping by any single dimension, so pooling per-DUT contributions across constituents is mathematically the correct group value, not an approximation, for anything that's a plain aggregate over DUTs (mean, min, max, or a from-scratch recompute like `computeStats()`). The exception is anything that needs the *pooled population's own* order statistics or requires data that isn't embedded client-side at all (Shapiro normality, non-parametric TI, DEnv) — those fall back to a **worst-case (max/tightest) approximation across the constituent conditions' own pre-computed values**, following the same tightest-wins convention already used for spec-conflict resolution elsewhere in this codebase (see `getSpecMask()`).

- **stat_summary** (`getGroupedConditions()`/`_poolFreqStats()`): mean/std/quantiles/outliers recomputed exactly from pooled `dut_vals`. DEnv and spec are worst-case across constituent `freq_stats` entries. Shapiro normality (`W`/`p`/`norm`) is **not** recomputed — pooled entries get `norm:'grouped'`, which doesn't match `"Normal"`/`"Marginal"` in `normColor()`, so it renders as the "Non-normal" red dot as a visible (if imperfect) cue that this is a pooled approximation, not a real Shapiro result.
- **summary** (`render_summary`/`_SUMPLOT_JS`): mean/min/max are **exact** (pooled per-DUT `dut_vals`/`dut_info`, and min/max of a union equals min/max of per-subset mins/maxes — no approximation at all). `uttl`/`lttl` (NP TI) and `spec_hi`/`spec_lo` are worst-case across constituent records, since a true NP TI recompute needs the pooled population's raw order statistics, and `by_temp`'s precomputed per-temp breakdown doesn't cleanly re-aggregate once records are merged.
- **env_coverage** (`render_env_coverage`): the **only fully exact one** — `computeStats()` already recomputes UDE/LDE/TTU/TTL from raw per-DUT `room`/`deltas` arrays on every call regardless of grouping, so pooling the underlying `duts` dicts and letting the existing function run unchanged is exactly correct, no separate re-aggregation math needed. Only `spec_hi`/`spec_lo` (already a single `mode()` value per condition pre-Group-by) take the tightest value across constituents.
- All three feed the pooled "virtual conditions" into the exact same downstream code (`buildTraces`, stats tables, CSV export, segment-tab detection) that real conditions use — segment detection in particular automatically respects whatever Group By is active, since it iterates whichever `getGroupedConditions()` currently returns.
- `env_coverage`'s existing "Show excluded" checkbox compares candidate conditions against `ENV_DATA` by object identity — meaningless once Group By produces synthetic pooled objects, so `update()` skips it (shows nothing "excluded") whenever Group By is active, rather than incorrectly flagging everything as excluded.
- Verified against the same real clock-leakage pod: all three views correctly collapse 151 conditions → 5 SpurTypes with matching, correctly-pooled statistics.

**Real bug found and fixed the same day**: `summary`'s pooled virtual record initially set `by_temp: {}` (empty object) on the returned record. `getSumCondData()`'s fallback branch checks `if(!cd.by_temp){ ...use the precomputed mean/min/max/uttl/lttl directly... }` — but `{}` is truthy in JavaScript, so that check never fired. Execution fell through into the by-temperature recompute path instead, which iterates `cd.by_temp[t]` for each temp; since the object was empty, every lookup came back `undefined`, `tot_n` stayed `0` for every frequency, and the function returned `null` for the entire trace — a real, silent blank plot for every "Group by" option except the default "Condition". Fixed by setting `by_temp: null` instead (falsy), which correctly routes into the "use precomputed" branch — exactly the values `_poolSumRecords()` had already computed correctly. `stat_summary`'s equivalent field (`denv_by_temp`) was never at risk of the same bug — its only consumers read it via `fs.denv_by_temp||{}`, a safe fallback pattern rather than a `!x` truthy branch, confirmed by re-checking both call sites. Lesson: an empty object is not a safe stand-in for "no data" wherever the consumer branches on `!field` rather than `Object.keys(field).length`.
  - **For C#-background readers**: this bug only exists because JavaScript lets you write `if(!someObject)` at all. C# has no implicit object→bool conversion — `if (someObject)` is a compile error unless `someObject` is itself a `bool`, so this exact mistake can't be expressed in C#. JS instead has a small fixed list of "falsy" values — `false`, `0`, `""`, `null`, `undefined`, `NaN` — and everything else, including `{}` and `[]` (empty object/array, no `Count`/`Length` involved), is truthy. The C# instinct "an empty collection is falsy-ish, right?" doesn't transfer — the closest C# analogy is `someList != null` (reference check) vs. `someList.Count > 0` (content check); this bug is exactly "used the reference check where a content check was needed."

---

## env_coverage: Room TI and delta TI now share one DUT population (changed 2026-08-08)

**What changed**: `computeStats()` (`_ENV_COVERAGE_JS`, `padb_plots.py`) used to compute Room stats (`room_ns`/`room_means`/`room_lo`/`room_hi`) from `allDuts` — every DUT in the condition, completely unfiltered (ignored the Serial filter, the Port filter, and even the Global Filter). Delta stats (`ude`/`lde`, via `getDeltaDuts()`) were already correctly serial+GF filtered. Room now uses `getDeltaDuts(cd)` too — the exact same population as delta — so Serial/GF selections shrink both together instead of only shrinking delta.

**Premise**: UDE/LDE is fundamentally a *per-DUT delta* (non-Room value − Room value for that same DUT). The Room TI band/n displayed alongside it exists as a reference for "how much does Room itself vary." For that comparison to actually isolate temperature as the only degree of freedom — the entire point of showing the two bands together — both must be computed from the *same* DUTs. If Room's population differs from delta's (e.g. Room includes a DUT that Serial-filtering excluded from delta), the two bands are no longer measuring the same population's behavior at two temperatures; they're comparing two different populations, and any resulting difference could be population variation rather than a temperature effect. Reported by the user after noticing exactly this: deselecting one DUT via the Serial filter dropped ΔEnv `n` from 6 to 4 while Room `n` stayed at 6.

**What's deliberately unchanged**: both Room and delta remain **port-agnostic** — selecting a single port never shrinks either `n`. This preserves the original (and still valid) rationale for `getDeltaDuts()` excluding port: narrowing to one port for viewing purposes isn't the same kind of population change as deselecting a DUT, and letting it shrink `n` would make the k-factor lookup (tolerance-interval multiplier) unnecessarily unstable at small `n` for no statistically meaningful reason. Only the *Serial/GF* mismatch was the actual inconsistency — port was never the problem, and the fix doesn't touch it.

Verified via direct probe on `computeStats()`'s output: with all serials selected, `room_ns`/`delta_ns` both read `[6,6,6]`; after deselecting one serial, both read `[4,4,4]` together (previously Room would have stayed at `[6,6,6]`). `qa_padb.py` baseline unchanged (37 PASS / 4 FAIL).

---

## CSV auto-detection fallback in `padb_v2.py` (added 2026-08-04)

`padb_v2.py` resolves its input CSV in priority order: `--csv` CLI arg, then `cfg["csv_path"]` from job.json, then a fallback search. The `cfg["csv_path"]` case previously failed hard (`sys.exit`) if the path didn't exist — a real risk for `padb_make_v2_job.py`-generated jobs, since `csv_path` there is *predicted* from naming conventions, not verified against a real extraction.

`_resolve_csv_path()` now gives a predicted-but-missing `csv_path` one more chance: it searches the same directory for a CSV matching via `padb_run.filename_stem_variants()` — the identical space/hyphen/dot normalization `find_csvs()` already applies in `padb_run.py` (hoisted from a nested function to a module-level one specifically so `padb_v2.py` could reuse it without duplicating the logic) — then falls back further to a fuzzy 15-char-prefix glob, matching `find_csvs()`'s own fallback order. Prints which match (if any) it used and why. Falls through to the original `sys.exit` only if nothing in the directory matches at all.

---

## `padb_make_v2_job.py` — V2 (Interactive mode) job.json generator (added 2026-08-04)

Generates the full Interactive-mode job set from a `.pod` file alone: one shared extraction job (`<pod_stem>_run_job.json`, `padb_run.py`'s schema) plus one plot job per Type=80 Scatter analytic (`<pod_stem>_<analytic>_v2_job.json`, `padb_v2.py`'s schema) — mirroring the real hand-written `MaxPower3.pod` V2 job set structurally, though not every design choice matches (see below).

```
py padb_make_v2_job.py MyPod.pod --module MyModule
py padb_make_v2_job.py MyPod.pod --module MyModule --spec-direction lo
py padb_make_v2_job.py MyPod.pod --no-publish
py padb_make_v2_job.py MyPod.pod --module MyModule --force
```

Key design decisions, each deliberate:

- **`"views"` is omitted from every generated plot job.** `padb_v2.py` already auto-detects Room-only (`scatter`+`boxplot`) vs. multi-temp (all six views, including `env_coverage`/`distribution` when non-Room data is present) from the actual extracted CSV at run time — the existing "Auto view-selection" mechanism above. No new detection logic was needed; verified against a real 19-analytic pod where 7 analytics correctly got all six views (genuine multi-temp data) and 2 correctly got just `scatter`+`boxplot` (genuinely Room-only), with zero manual `views` tuning.
- **Every Type=80 analytic gets its own plot job, full stop** — no attempt to guess which one deserves the "primary" full treatment vs. a lighter `scatter`-only comparison view, the way the hand-written `MaxPower3.pod` jobs do (3 of 4 near-duplicate analytics trimmed to `scatter`-only by a human). See `feedback_padb_automation_completeness` memory / the "completeness over curation" principle — confirmed by the user as the right default after comparing generator output to the hand-curated original side-by-side.
- **`csv_path` is predicted, not confirmed**, from the analytic's `OutputConfig_OutputFile` (or, when a pod-wide `OutputFile` collision is detected, the same guaranteed-unique slug `unique_output_filenames` will produce) — can't be verified correct until the run job has actually executed once. Verified exact-match against real extraction output on two very different pods: `MaxPower3.pod` (no collisions, `OutputFile`-based prediction) and the Harmonics pod (collisions, `AnalyticName`-slug prediction with index-suffix disambiguation) — both predicted every `csv_path` correctly on the first real run, zero manual correction needed.
- **`spec_direction` defaults to `"auto"`** — a measurement that's one-sided despite having no configured pod-level spec limits (`MaxPower3.pod`'s hand-tuned jobs hardcode `"lo"` for exactly this reason) can't be inferred from the pod alone; override with `--spec-direction` if you know better.
- **All plot jobs for one pod share one `results_dir` and one publish destination** — `padb_v2.py`'s `_write_index()` already merges multiple runs into one combined gallery, so N analytics' worth of views (up to N×6 files) accumulate into a single `index.html`, matching the real `MaxPower3.pod` example. Verified: the Harmonics pod's 9 analytics produced exactly 47 files (42 + 4 view files + `index.html`) in one gallery, published to `PADB-Interactive\<module>\<pod_stem>` — a separate top-level share tree from `PADB-Simple`, per user preference.
- `--module` is required unless `--no-publish`, same reasoning as `padb_make_job.py`.
- **`description`/`title_prefix`/`index_title` are tagged with the pod's real `Device_Device` (fixed 2026-08-10), not hardcoded.** `sections = parse_pod_sections(pod_path)` was already computed here (used for `y_label`) but the "SG6311A" prefix on every title/description/index-title was a separate literal, unconditional of it. Since `title_prefix` becomes the literal generated HTML filename prefix, this wasn't just cosmetic — every plot file for a non-SG6311A pod would be misnamed. Now reads `sections["Extract"]["Device_Device"]` (stripped of the literal quotes PADB wraps it in, e.g. `'M9484C'` → `M9484C`) via a small `_dev_tag()` helper; omits the prefix entirely if the field is missing rather than guessing.

**Path-length warning (added 2026-08-04):** both `padb_make_job.py` and `padb_make_v2_job.py` call `padb_config.warn_if_path_long()` right after writing each file. Real case that motivated this: a CW Closed Loop pod nested in its own `padbResults\<name>.dir\` tree, with a single Type=80 analytic whose name nearly repeated the pod's own already-long stem, produced a 256-character `_v2_job.json` path — one character away from Windows' 260-char `MAX_PATH`. That specific case was fixed at the naming-logic level (the per-analytic suffix is now only appended when a pod has more than one Type=80 analytic — see `_predict_csv_stem`/plot-job-naming above), but nothing stopped a *different* long pod/module/analytic-name combination from hitting the same wall. `warn_if_path_long()` (`padb_config.py`) prints a `WARNING:` with the full path, its length, and concrete next steps (move the pod to a shallower directory, shorten the pod filename, shorten `AnalyticName` if multiple Type=80 analytics force the suffix, and a reminder that `results_dir`/`publish_to` paths built from this job nest even deeper) whenever a generated path reaches 220+ characters — 40 characters of margin before the hard 260 limit. This only covers the two generators' own output paths; it does not (yet) check `results_dir` or `publish_to` paths themselves, since those aren't known to be problematic in practice yet — extend `warn_if_path_long()` calls to those if a real case surfaces.

---

## `padb_convert_site.py` — convert a pod/job.json between database sites (added 2026-08-05)

Malaysia (AMC2) production ramp-up surfaced a new axis of variation: the *same* test, pulling from a *different* PADB Oracle database. Comparing a real Santa Rosa pod against a hand-made AMC2 variant of the same test (`MaxPowerTutorial1.pod` vs `MaxPowerTutorial1-AMC2.pod`) showed the only genuine differences live in `[Extract]`: `Device_Server` (`"PADB ORACLE SR"` vs `"PADB ORACLE AMC2"`) and `Device_Database` (`"V2_GALLEON"` vs `"GALLEON_1"`). Everything else — every `AnalyticName`, every `OutputConfig_OutputFile` — is identical between the two, which means running both pods writes identically-named CSVs. No collision *within* one pod (the existing `unique_output_filenames` case) — a collision *across* two site-variant pods of conceptually the same test, if either is ever run into a shared location.

- **Site registry**: `padb_sites.json`, next to the script — `{"SiteName": {"suffix": "...", "Device_Server": "...", "Device_Database": "..."}}`. Exactly one site must have `"suffix": ""` — that's the *primary* site (Santa Rosa); its analytic names are the canonical, unsuffixed ones everything else disambiguates against. Add a new site here (no code changes) when a third location shows up.
- **`--pod <file> --to <site>`**: detects the source site by matching the pod's live `Device_Server`/`Device_Database` against the registry (raises clearly, never guesses, if it matches none — same defensive-throw convention as the spec functions). Writes a new pod (never touches the source): swaps `Device_Server`/`Device_Database`, and for every analytic, appends the target site's suffix to `AnalyticName` (space-separated, e.g. `"Leveled Linear"` → `"Leveled Linear AMC2"`) and `OutputConfig_OutputFile` (underscore-separated, e.g. `"..._Linear"` → `"..._Linear_AMC2"`) — or strips a known suffix back off when converting *to* the primary site. Verified round-trip byte-identical (Santa Rosa → AMC2 → Santa Rosa reproduces the original pod exactly, apart from `SaoFile`/`LastUpdated`).
- **`.sao` files can't be converted.** They're a binary PADB format (version-tagged, with encoded DUT serial numbers) — a Santa Rosa `.sao` is meaningless against AMC2 hardware. The tool points `SaoFile=` at the expected new filename and prints an explicit `WARNING:` that a real `.sao` extracted at the target site still needs to be supplied before the converted pod can run.
- **`--job <job.json> --to <site>`**: repoints `"pod"`, and substitutes the old pod stem for the new one everywhere it appears in `results_dir`, `publish`/`publish_to`, and `description`. Auto-creates the companion converted pod via the same logic above if it doesn't exist yet (prints when it does this — no silent side effects). For a V2 run job (`"mode": "interactive"`) it also prints a reminder to re-run `padb_make_v2_job.py` against the new pod for the plot-job side, rather than hand-patching a `csv_path` prediction a second time.
- **Never overwrites an existing output file** without `--force` — same convention as every other generator in this repo.

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

## Web app (webapp/padb_web.py) — Phase 1 (added 2026-08-05, refined same day)

`py C:\apps\padb\tools\webapp\padb_web.py`

Local Flask app (opens `http://127.0.0.1:5000` in the default browser). Local use only — the dev server is not meant to be reachable beyond 127.0.0.1. Every route shells out to the existing CLI scripts via `subprocess`, or imports their pure functions directly (`parse_pod_analytics()`, `discover_all_padb_tasks()`, `create_task()`, `delete_task()`, `query_task()`, `format_schedule_summary()`, `load_sites()`, `convert_pod()`, `convert_job()`); nothing in `padb_run.py`/`padb_v2.py`/`padb_make_job.py`/`padb_make_v2_job.py`/`padb_convert_site.py` was changed to build this (one real bug in `padb_scheduler.py` itself was found and fixed along the way — see below).

Phase 1 covers four of the seven originally-requested features:

- **Drop a `.pod` file** (drag-and-drop or click to choose) → saved into `padb_config.load_defaults()["data_dir"]` (same folder every CLI script already uses) → shows the parsed analytic list (`parse_pod_analytics()`) → fill in mode/module/dates → **Generate Job** calls `padb_make_job.py` (legacy/simple) or `padb_make_v2_job.py` (interactive) exactly as the CLI would, and shows the generated job.json content plus any `NOTE:`/`WARNING:` output verbatim. Freely overwrites an existing pod of the same name in `data_dir` — dropping a pod that's already been onboarded (to (re)generate its job.json) is the common case, not a collision to guard against; the thing that actually shouldn't be silently clobbered is the job.json itself, and that's already `--force`-gated in the generator scripts.
- **Execute job(s)** — a jobs table (same `sorted(data_dir.glob("*_job.json"))` discovery pattern as `padb_scheduler.py`) with checkboxes and a **Run Selected** button. A single background worker thread + `queue.Queue` is the actual serialization point (PADB-R.exe must never run two instances concurrently) — Flask's dev server fielding concurrent requests does **not** by itself guarantee this; the queue does. The worker branches on job shape: a job with a `pod` key (`kind: "run"` — legacy/simple/interactive) runs via `padb_run.py`; a job without one (`kind: "plot"` — `csv_path`/`analytic` key instead, no PADB-R.exe involved at all) runs directly via `padb_v2.py` (added 2026-08-05 — previously plot jobs could be selected but always failed, since the worker unconditionally called `padb_run.py`, whose `load_job()` requires a `pod` key that plot jobs don't have). For a `"mode": "interactive"` run job (`*_run_job.json`), once it succeeds the worker auto-globs and runs every sibling `*_v2_job.json` plot job in turn, completing the full V2 flow without a manual second step — this is *in addition to* being able to run one plot job standalone, useful for rebuilding just one analytic's HTML after a `padb_plots.py`/`padb_v2.py` code change without re-extracting anything. A **Dry run** checkbox passes `--dry-run` through to `padb_run.py` for `kind: "run"` jobs — note this still runs the publish step if the job's existing results already have CSVs on disk (dry-run only skips the PADB-R.exe call itself, not publishing); it has no effect on `kind: "plot"` jobs (no PADB-R.exe call to skip in the first place — padb_v2.py has no equivalent flag, so the dry-run checkbox is simply ignored for those, always doing the real (cheap, local) build).
- **Schedule/unschedule job(s)** — a second toolbar row (schedule type Daily/Weekly, day checkboxes, start time, **Schedule Selected** / **Unschedule Selected**) posts to `/api/schedule` / `/api/unschedule`, which call `padb_scheduler.create_task()` / `delete_task()` directly — the exact same functions the desktop Scheduler GUI uses, so a task created from either place is indistinguishable to the other. `POST /api/schedule` body: `{paths, schedule_type, days, start_time}`; both routes return a per-job `{path, task_name, ok, error}` list since some jobs in a batch can fail (e.g. a bad path) while others succeed.
- **Convert pod/job between sites** — a **Convert to** site dropdown + **Convert Selected** button in the jobs toolbar (targets checked jobs), and a matching **Convert Pod** control under the drop-a-pod analytics preview (targets the just-dropped pod). Both call `padb_convert_site.py`'s `convert_pod()`/`convert_job()` directly rather than via subprocess — unlike the job generators, that module already exposes clean, reusable functions instead of inline `main()` logic, so there was no need to shell out and re-parse stdout. `POST /api/convert-pod`/`/api/convert-job` capture that function's own `print()` output via `contextlib.redirect_stdout` into a `log` field returned to the browser (same "show the real tool's own messages verbatim" pattern as Generate Job), and catch `SystemExit` specifically (`padb_convert_site.py`'s functions raise it, not return an error code, for conditions like "already at that site" or a pod matching no known site) since a bare `except Exception` would not catch it. `GET /api/sites` powers both dropdowns from `padb_sites.json`.
- The status panel polls `GET /api/job-status/<id>` every ~2s per active job, showing queued/running/done/failed and a scrolling log tail, plus an **Open results** link once `result_index` is set (see below).

**Jobs table columns** (Name, Mode, Pod, Description, Scheduled, Last Run, Results), each earning its keep:
- **Mode / Kind / Name filters** above the table — client-side filtering of the same fetched job list, no extra round-trip. **Kind** (added 2026-08-05) distinguishes `"run"` (has a `pod` key — runnable via `padb_run.py`: legacy/simple/interactive-run jobs) from `"plot"` (has `csv_path` or `analytic`, no `pod` — a V2 plot job, only runnable via `padb_v2.py`, would just fail if sent through `padb_run.py`) from `"unknown"` (neither — shouldn't normally happen). A **Select All Runnable** button checks every currently-rendered `kind=="run"` row and unchecks everything else — the safe way to bulk-select for **Run Selected** across a large job list without also grabbing non-runnable plot jobs (real motivating case: 124 job files, ~44 of them plot jobs, hand-picking checkboxes wasn't practical for an unattended batch run).
- **Bug fixed 2026-08-05 — sibling-glob stem-prefix collision**: `_find_v2_siblings()` (used by both the Results-link lookup and the worker's auto-chain) globs `"{stem}_*v2_job.json"` for a run job's plot siblings — but one job's stem can be a literal prefix of a completely different, unrelated pod's longer stem. Real case: `maxpower3_run_job.json` (stem `maxpower3`) incorrectly matched `MaxPower3_v2`'s own plot jobs too (`MaxPower3_v2_Leveled_Linear_v2_job.json` etc. all start with `maxpower3_`, case-insensitively). Fixed by validating each glob-matched candidate's own `csv_path` actually points into *this* run job's `results_dir` before accepting it as a real sibling — but only when `csv_path` is set; older plot jobs using the `analytic` key (no `csv_path` at all) are passed through unfiltered rather than incorrectly excluded. Verified with a full sweep comparing naive-glob vs fixed results across every `*_run_job.json` in the real dataset (124 jobs) — exactly one case differed (the known `maxpower3` collision), no other collisions, no regressions.
- **Scheduled** — shows the actual cadence (e.g. "Mon Wed Fri  02:00"), not just a checkmark: `discover_all_padb_tasks()` finds which jobs have a `TASK_PREFIX + job_stem` task (one `schtasks` call for the whole table), then `query_task()` + `format_schedule_summary()` fill in the human-readable schedule for just those. Reuses `padb_scheduler.py`'s own detection/formatting rather than re-implementing it, so this column can never disagree with the Scheduler GUI.
- **Bug fixed in `padb_scheduler.py` while building this**: `query_task()` queried `schtasks /query /tn <name> /fo LIST` *without* `/v` — but `Schedule Type`/`Start Time`/`Days` only appear in schtasks' verbose output. This meant `query_task()` had always returned empty schedule info for any real task, so the desktop Scheduler GUI's own schedule-summary display was almost certainly blank too, silently, this whole time. Fixed by adding `/v` to that one call (`padb_scheduler.py` line ~71). Confirmed via a real create→query→delete round trip on a live Windows Scheduled Task.
- **Last Run** — mtime of that job's `results_dir/index.html`, formatted `YYYY-MM-DD HH:MM`. Reflects the last *successful* run (index.html is only written on success), not the last attempt — a repeatedly-failing job shows a stale date rather than "just failed."
- **Results** — a link to that job's `results_dir/index.html`, if it exists. Served through the app itself at `/results/<token>/<filename>` rather than a `file://` link — browsers silently block navigating an `http://` page to `file://`, which was a real bug here. `<token>` is a short hash of the results directory, registered in an in-memory `_RESULT_DIRS` dict the first time that directory is ever pointed at (resets on server restart, same as job/queue state); `send_from_directory` blocks path traversal outside it. Because the URL path mirrors the real directory structure, the generated gallery's own relative links between sibling plot HTML pages resolve correctly with no rewriting.
- **Bug fixed 2026-08-05 — run job's own Results link pointed at the wrong page**: for a `*_run_job.json` row, `list_jobs()` was using that job's *own* `results_dir` (the extraction step's plain metadata/analytics-table page) instead of the sibling plot jobs' shared `results_dir` (the actual merged interactive gallery) — those are two different folders by design (`padb_make_v2_job.py`: run job gets `{stem}_run_results`, every plot job shares `{stem}_v2_results`). Reported via the web app: clicking "Open" on the run-job row showed "no interactive plots." Fixed with `_job_result_index_path()` — for a `mode=="interactive"` job named `*_run_job.json`, it looks up the first sibling `*_v2_job.json`'s own index instead of its own; every other job shape is unaffected. Used for both the jobs-table Results/Last-Run columns and the running-job status panel's fallback.
- **Layout**: `body` max-width 1300px (bumped up from an arbitrary initial 900px). Name/Pod/Description/Scheduled wrap onto extra lines when long (row grows taller) rather than truncating or scrolling — tried per-cell horizontal scrollbars first, wrapping reads easier. Mode/Last Run/Results stay single-line since they're never long enough to need it. The table itself sits in a `max-height: 320px` scrolling box (sticky header) so a long job list doesn't push "Running Jobs" off-screen.

Deliberately out of scope for Phase 1 (not started): the guided "what do you want to do?" wizard, the tutorial walkthrough dropdown, and the doc viewer.

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
- **Two concurrent instances interfere with each other and both stall** (zero CPU progress) — see the cross-process exclusivity guard below for the real fix, not just "don't do that."

---

## Cross-process PADB-R.exe exclusivity guard (added 2026-08-10)

**Real incident this was built from**: the webapp's single-worker job queue (`queue.Queue` + an in-memory `_jobs` dict in `padb_web.py`) only serializes PADB-R.exe launches within one Flask process's *lifetime*. That Flask process got killed mid-run (a background-task lifecycle issue, unrelated to the job itself — not something either the user or the job caused), while a real extraction was still in progress. `subprocess.Popen` doesn't bind child-process lifetime to its parent on Windows without an explicit job object, which this code never set up — so the already-launched `padb_run.py` → PADB-R.exe chain kept running, orphaned and invisible to any UI, once its parent Flask process died. A *new* Flask process then started with a fresh, empty queue that had no idea the orphan existed, and happily launched a second PADB-R.exe for a different job. Two concurrent PADB-R.exe instances → both stalled at zero CPU progress, confirmed via repeated `Get-Process -Id ... | Select CPU` sampling showing no growth across several seconds on both.

**Fix**: `padb_batch.py`'s `wait_for_exclusive_padb_r(exe_path, max_wait, poll_interval)`, called from `PADBBatch.run()` itself — the one real choke point every invocation path goes through (webapp queue *and* direct CLI use of `padb_run.py`), rather than relying on any one process's in-memory queue state:
- Checks the **live OS process table** via `tasklist /FI "IMAGENAME eq <exe>" /FO CSV` (not psutil — avoids a new dependency for one lookup; `tasklist` is always present on Windows). Checking real OS state instead of a lock file means a stale lock from a crashed/killed process can never cause a false "still busy" deadlock — the moment the real process exits, this sees it gone, no cleanup step required.
- If another instance is found, polls every `poll_interval` seconds (default 5s) until it clears or `max_wait` elapses, printing a one-time notice on the first check so a long wait doesn't look silently stuck.
- If it never clears, raises `RuntimeError` naming the blocking PID(s) and how long it waited, with the exact `taskkill /IM <exe> /F` command to clear a genuinely-stuck instance by hand — refuses to launch a second instance rather than let two interfere silently.
- `max_wait` defaults to the caller's own `timeout` (i.e. `padb_timeout` from job.json) when `PADBBatch.run()` is called with one, so "how long am I willing to wait for my own run" and "how long am I willing to wait for someone else's run to clear first" reuse the same already-configurable value rather than inventing a second timeout setting.
- `--dry-run` never reaches this check at all (`padb_run.py` returns before calling `.run()` when `dry_run=True`), so it adds no delay to switch-file-only runs.

Verified: directly exercised `_running_pids()`/`wait_for_exclusive_padb_r()` against a real live PADB-R.exe instance (correctly detected the real PID and refused to proceed within the test's short `max_wait`) and against a nonexistent exe name (returned in ~1s, the `tasklist` subprocess's own overhead, with no artificial delay).

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

**`summary_plot()` (V1-legacy) serial filter (added 2026-08-05):**
Important scope note: `summary_plot()` is the *V1/legacy* function, reachable only via `secondary_plots` `"type": "summary_plot"` against a real Type=90 SummaryPlot CSV — a data source `PADB_Analytic_Requirements.md` explicitly tells pod authors not to use as V2's primary source ("Do not use Type=60/Type=90 ... those produce pre-aggregated output that cannot be used for per-DUT analysis"). It is **not** what V2's real `summary` view uses — that's `render_summary()` in `padb_v2.py`, a completely separate function (see below). This fix only matters for whoever still has an old job.json using the legacy path (confirmed one real case: `harmonics_job.json`).

`summary_plot()` explicitly excludes serial-like keys from its generic Condition-filter dimensions (`_serial_kws` match on key name, or a `_serial_re` match on values) — correct, since a per-DUT serial isn't a "condition" to compare across. But unlike de_summary's Type=60 data, a Type=90 Summary CSV's rows are still grouped by the *whole* Group string, so per-serial granularity is retained whenever the pod's `Group_Num` was configured high enough to include a Serial Number key — the exclusion was just also hiding it from ever being offered as a filter. Fixed by detecting the excluded serial-like key separately and, if it has 2+ distinct values, appending it back into `cond_keys` (not just `cond_dims`) — everything downstream (`dim_vals` collection, each record's `cond_keys` dict) already iterates `cond_keys` generically, so no other code needed to change. Mirrors `de_summary`'s own existing "add serial back" pattern, adapted to reuse the already-parsed `group_kv` dict instead of re-deriving it from raw condition strings via regex. Verified with synthetic CSVs (checking the actual `COND_DIMS` JS variable, not just text search — a plain string search on `"Serial Number"` is unreliable here since an unrelated JS helper comment always contains that literal text): 2+ distinct serials → dimension appears with correct values; exactly 1 serial → correctly omitted; no serial key at all → unaffected, other dimensions still work.

**`summary` (V2) — per-DUT filtering is GF, not a Condition-filter dropdown:**
`render_summary()` (`padb_v2.py`) is built directly from the same single Type=80 Scatter CSV every other V2 view uses (per pod-authoring guidance: use Scatter with proper Group-By/Order-By, not Type=90/60, precisely so this data survives intact). It excludes serial-like columns from its Condition-filter `cond_dims` the same way `summary_plot()` does — but does **not** need `summary_plot()`'s "add it back" fix, because it already has a working, different mechanism for per-DUT effects: whenever it finds a serial-like column at all (no 2+-distinct-value gate, unlike the Condition-filter path), it unconditionally embeds `dut_vals`/`dut_info` — per-frequency, per-DUT mean values — into each record, specifically so the JS side can recompute the displayed aggregate when GF excludes specific DUTs (the comment at that call site literally says so). So: excluding a DUT via GF elsewhere in the tool already correctly affects `summary`'s numbers, with no dedicated Serial Number dropdown needed on the summary page itself. `de_summary` has no equivalent — it isn't even part of V2's view set (`VIEW_FUNCS` in `padb_v2.py` has no `de_summary` entry; V2's environmental analogue is `env_coverage`, also Scatter-CSV-derived).

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
  - **Correction (2026-08-10):** this claimed the interactive `boxplot` path already had `x_label` — it didn't. `_build_box_interactive_html` had `x_unit` but no `x_label` parameter at all, and `_stat_boxplot_interactive` (its caller) never read `x_label` from `cfg` either. Fixed the same day as the two bugs below — see that section.

---

## Real bugs found on a real complex multi-analytic pod (2026-08-10)

Found while plotting `AmplitudeAccuracyClosedLoop_PADBToolTest` (5 Type=80 analytics, one with a non-frequency x-axis, one with 2,388 distinct Group values, one a 1.26GB CSV).

**1. Segment-tab stepping stuck on segment 0 for real (non-round) sweep data.** `_recomputeSpecSegments()`'s index-recovery loop compared the freq textbox value (rounded to 3 decimals via `.toFixed(3)`, see `setFreqBand`) against each segment's *unrounded* `.lo` boundary. For synthetic/round test data this never mattered; for a real instrument sweep (segment boundary `10.107422`, textbox shows `"10.107"`), `10.107 >= 10.107422` is false, so the lookup always fell back to index 0 — Prev/Next appeared completely broken (`_segIdx` never advanced) even though `setFreqBand()` itself was correctly moving the frequency window every click. Fixed by rounding the segment boundary to the same 3 decimals before comparing (`parseFloat(_specSegments[i].lo.toFixed(3))-1e-9`), across all 7 duplicated copies. Verified end-to-end: 5 consecutive Next clicks now advance `_segIdx` 0→1→2→3→4 with matching real frequency ranges, not just improved unit-test coverage.

**2. "Freq min:"/"Freq max:" control labels never followed `x_label`.** The unit suffix and slider range correctly reflected an `x_label`/`x_unit` override (e.g. a job configured for `"x_label": "Amplitude (dBm)"` correctly showed a `-120` to `25` `dBm` range) — but the label text itself was a hardcoded literal `"Freq"` string in every one of 6 view builders, independent of any override. A pod whose real x-axis is Amplitude showed "Freq min: [-120.000] dBm", which is actively misleading, not just cosmetically inconsistent. Fixed by adding `_short_x_label(x_label)` (derives e.g. `"Amplitude"` from `"Amplitude (dBm)"`, preserving the literal `"Freq"` wording for the default `"Frequency (MHz)"` case so no existing pod's label text changes as a side effect) and using it in place of the literal `"Freq"` in `_build_av_freq_html` (scatter), `_build_env_distribution_html` (distribution), `_build_stat_summary_html`, `_build_env_coverage_html`, `_build_box_interactive_html` (boxplot — needed a new `x_label` parameter threaded from `_stat_boxplot_interactive`, since it never had one), and `_build_summary_html`. Deliberately **not** touched: the legacy `distribution()` function (V1, hardcodes `&nbsp;MHz` with no `x_unit` support at all — same pre-existing exclusion boundary as `de_summary`) and `_build_env_summary_html`/`_ENV_SUMMARY_JS` (the `de_summary`-equivalent legacy path) — both already excluded from the original `x_label`/`x_unit` rollout, not newly excluded here.

**3. A genuine second swept numeric dimension is silently pooled with no way to isolate it.** Neither bug — just a real design gap surfaced by this pod: `Relative_Frequency_Sweep_Vernier_Power_Per_DUT`'s CSV has a real `Amplitude (dBm)` column with 46 distinct values (the sweep was repeated at 46 different amplitudes), and `Relative_Amplitude_Sweep`'s CSV has the mirror-image `Frequency (MHz)` column with 12 distinct values. Neither is the detected x-axis, neither is `Group`-text, so neither is exposed anywhere — the tool has no concept of a second numeric sweep/condition column at all; it silently pools every value of it together. Not fixed (bigger design decision, not a quick patch) — see **Open question: selectable x-axis / secondary numeric dimension** below.

**4. "Extra segments" on `Absolute_Accuracy_PM`/`Absolute_Accuracy_NA` traced to per-DUT/per-Port inverted spec rows, not dimension pooling.** User reports: PM scatter showed 14 segments where ~7 were expected ("Segment 6 of 14 ... why 8 more segments?"), and NA boxplot showed the same pattern (19 segments). First hypothesis — that a sparse `Amplitude (dBm)` calibration condition (value `0`) was pooling with the real sweep (value `15`) via `getSpecMaskByKey`'s tightest-wins and producing isolated single-point segments — was **wrong**: direct row inspection showed `Amplitude (dBm): 0` covers 100% of frequencies (1,145 of 1,145), not a sparse few, so it isn't a pooling artifact at all. The real cause, found by inspecting the actual rows at the "extra" segment frequencies: in `Absolute_Accuracy_PM.csv`, **one single Serial Number (`US65080433`) has Upper Spec/Limit `<` Lower Spec/Limit across its entire dataset** — all frequencies, both Amplitude conditions (2,186 of 37,024 rows, 5.9%) — a per-DUT data-entry/labeling issue in the pod, not a padb-tools bug (same category as prior NPI-era anomalies: describe factually, let the user adjudicate — see `feedback_npi_data_anomalies` precedent). In `Absolute_Accuracy_NA.csv` the same inverted-value pattern exists (2,174 of 21,722 rows, 10.0%) but concentrated differently — by `Port: RF1` and `Lower/Upper Uncertainty: 0.04` (Serial is blank for this analytic, so the per-DUT lens doesn't apply here). Both cases: `getSpecMaskByKey`'s tightest-wins pooling is working correctly on bad input data, faithfully surfacing every distinct (often-conflicting) Spec/Limit combination as its own segment. Led directly to the Help panel feature below, which surfaces this exact check automatically instead of requiring a manual investigation each time.

---

## `padb_csv_check.py` — pre-flight CSV sanity check (added 2026-08-10)

Standalone script, run **before** `padb_v2.py`, that would have caught all three findings above (well, #1 and #2's root causes, and directly warns about #3) before spending time on a slow or wrong build. Deliberately does not re-implement any column-detection logic — it calls `padb_plots._load_scatter_for_stats()` directly (the exact function `padb_v2.py` itself uses via `load_scatter()`) and inspects what it actually picked, so the check can never drift out of sync with real pipeline behavior.

```
py padb_csv_check.py <csv_path> [--x-col "Exact Column Name"]
```

Checks, in order:
1. **Load success** — if `_load_scatter_for_stats` returns 0 rows, reports it as a FAIL with the same guidance the loader's own `[WARN]` gives (set `x_col`).
2. **Orphaned numeric columns** — any numeric CSV column that isn't the detected x-axis, value, or a Limit column, and isn't `Group`/`Test Step`/known metadata. Flags with extra emphasis when the orphaned column has *more* distinct values than the detected x-axis (the exact `Relative_Amplitude_Sweep` scenario — Frequency auto-detected as x-axis with only 12 values, while the *real* x-axis, Amplitude, has hundreds and was sitting right there unused).
3. **Raw-vs-usable row count** — reports the drop rate from `dropna(subset=["Frequency_MHz","Value"])`, since this exact number (807,136 of 4,254,039, 81%) is easy to be alarmed by if you only ever see it later in `padb_v2.py`'s own terminal output with no context for whether it's expected.
4. **Group cardinality** — warns above 100 (crowded legend, "Group by" recommended) and above 500 (real combinatorial slowness — a 2,388-condition analytic took ~19 minutes to build boxplot/stat_summary).
5. **Grouping-item presence** — Serial (checked via *both* a dedicated CSV column and a Group-text key, since most real pods embed Serial in Group text only — see `PADB_Analytic_Requirements.md` §7), Port, Upper/Lower Limit, Upper/Lower Spec/Uncertainty (needed for full 3-way Segment-by).
6. **Temperature coverage** — Room-only vs multi-temp, since that silently determines whether distribution/env_coverage/summary get built at all.

Exit code 1 on any WARN or FAIL (matches `qa_padb.py`'s convention), so it's usable as a pre-flight gate in a script, not just an interactive read.

---

## In-page "Help" panel — surfaces inverted spec/limit rows (added 2026-08-10)

`_build_help_panel_html(df, dims, ...)` in `padb_plots.py` builds a collapsible &#9432; Help button. Wired into all 6 main views as of 2026-08-10: `_build_av_freq_html` (scatter), `_build_box_interactive_html`/`_stat_boxplot_interactive` (boxplot), `_build_stat_summary_html` (stat_summary), `_build_env_distribution_html` (distribution), `_build_env_coverage_html`/`render_env_coverage` (env_coverage, computed in `padb_v2.py` since that builder only receives `cond_dims`, not `df`), `_build_summary_html`/`render_summary` (summary, same reason). Deliberately **not** wired into `_build_env_summary_html` (the `de_summary`-equivalent legacy path, same pre-existing exclusion boundary as the `x_label`/`x_unit` rollout above).

Not every view shares the same filter-widget CSS/JS or controls, so the function is parameterized rather than one hardcoded snippet:
- `has_group_by`/`has_segment_by` drop the matching explanatory bullet when a view has no such control (`distribution` has no "Group by").
- `btn_class`/`panel_class`/`toggle_fn`/`panel_id`/`wrap_class` plug in a view's own filter-widget naming. Five views share `filter-btn`/`filter-panel`/`togglePanel`/`panel_help`/`filter-wrap`; `distribution` uses its own `dist-filter-btn`/`dist-filter-panel`/`toggleDistPanel`/`dist_panel_help`/`dist-filter-wrap` convention and was wired accordingly.
  - **Bug found and fixed same day**: the initial version only parameterized `btn_class`/`panel_class`/`toggle_fn`/`panel_id` — the outer wrapper `<div>` was hardcoded to `class="filter-wrap"` regardless. `distribution`'s page never defines `.filter-wrap` (only `.dist-filter-wrap`, which supplies the `position:relative` the popup panel anchors to), so the button rendered and was clickable but the panel had no positioned ancestor to open relative to — reported by the user as "no active help button" on a real generated page. Fixed by adding `wrap_class` and passing `"dist-filter-wrap"` at distribution's call site; verified by simulating an actual click in headless Chromium (`panel.classList.contains('open')` false→true, wrapper's `getComputedStyle().position === 'relative'`), not just by checking the class name appears in the HTML.
- `env_coverage`/`summary`/`boxplot` don't receive raw `df` in their HTML-builder function (only pre-aggregated data) — `help_panel_html` is computed one level up, in the function that *does* have `df` (`render_env_coverage`/`render_summary` in `padb_v2.py`, `_stat_boxplot_interactive` in `padb_plots.py`), and threaded down as a plain `help_panel_html: str = ""` parameter, matching the existing `tll_selector_html`-style convention already used in this codebase for pre-rendered HTML snippets.

Two parts:
1. **Static explanation** of what Filter dropdowns / Group by / Segment by actually do, and how an unfiltered dimension can pool into segments.
2. **Dynamic inverted-row check** — flags rows where `Upper_Limit < Lower_Limit` or `Spec_Hi < Spec_Lo` (backwards from the usual convention), and names which filter-dimension value(s) the inverted rows are concentrated in (checks every dimension in `dims` plus `Serial` if present, using a >=90%-of-inverted-rows threshold to name the real culprit rather than every dimension the bad rows happen to also have a value for).

**Design history worth keeping**: the first version of this check used row-share percentage ("flag any filter value covering <10% of rows") instead of the inverted-row check. Looked plausible but was wrong on real data — it false-flagged every normal high-cardinality dimension (all 17 Serials in the PM data, each ~5.8-6.1% of rows; all 17 distinct Limit values) while completely missing the real `Amplitude (dBm): 0` hypothesis (which turned out to be wrong anyway, see bug #4 above) since that condition actually covers 100% of frequencies, not a sparse few. Replaced with the targeted inverted-Upper/Lower check once the real anomaly was found by direct row inspection — a lesson in verifying a heuristic against real generated output before trusting it, not just checking that it renders.

---

## Hover-template "Freq" leaked through on non-frequency x-axes (fixed 2026-08-10)

The 2026-08-10 `_short_x_label()` fix (finding #2 above) only touched the human-readable control *labels* ("Freq min:"/"Freq max:"). It missed Plotly `hovertemplate` strings, which are built separately and still hardcoded the literal word `"Freq"` — so hovering over any point on an Amplitude-axis page showed "Freq: -45.2 dBm" instead of "Amplitude: -45.2 dBm". Caught by the user directly reading a hover tooltip on `Relative_Amplitude_Sweep_scatter.html`. Fixed the same way as the control labels: added a JS constant `var X_SHORT_LABEL=...;` (from `_short_x_label(x_label)`, computed server-side) next to each view's existing `X_LABEL`/`X_UNIT` JS constants, then used `+X_SHORT_LABEL+` in place of the literal `'Freq'` in every hovertemplate. Fixed in: `_build_av_freq_html` (scatter, 1 site), `_STAT_BOXPLOT_INTERACTIVE_JS` (boxplot's real embedded JS module — see below for how that's wired — 2 sites), `_SUMPLOT_JS` (summary's embedded JS module, 4 sites, which also hardcoded a literal `" MHz"` unit alongside "Freq" — fixed to use `+X_UNIT+` too). Deliberately **not** touched: `stat_boxplot`'s `interactive=False` branch (confirmed dead code — `padb_v2.py` always calls `interactive=True`) and the legacy `accuracy_vs_freq`/`de_summary`/`de_heatmap` V1 functions (same pre-existing exclusion boundary as everywhere else in this doc).

**Module-level JS string constants, and why line numbers lie about which function "owns" a line**: this file defines several giant JS blocks as *module-level* constants (`_AV_FREQ_JS`, `_STAT_SUMMARY_JS`, `_ENV_SUMMARY_JS`, `_ENV_COVERAGE_JS`, `_STAT_BOXPLOT_INTERACTIVE_JS`, `_SUMPLOT_JS` — all `_SOMETHING_JS = r"""..."""` at column 0), physically positioned *between* two `def` blocks in the file but not inside either one. A "what's the nearest preceding `def`" heuristic (e.g. `awk 'NR<=N && /^def /'`) will confidently misattribute a line inside one of these constants to whatever function happens to be lexically above it — which is exactly what happened while investigating this fix: a hovertemplate at "line 8133" looked like it was inside `stat_boxplot`'s dead `interactive=False` branch (plausible dead-code bug), when it was actually inside `_STAT_BOXPLOT_INTERACTIVE_JS` (7803–9932, module level), the real JS embedded by `_build_box_interactive_html`. Always confirm with `grep -n "^_[A-Z_]*_JS = r"` (or `^def `) and compare *both* sets of line numbers before trusting a "which function is this in" judgment in this file.

## Segment-tab "stuck at the last segment, can't go back" — same-boundary tie in the index-recovery loop (fixed 2026-08-10)

User report: on `Absolute_Accuracy_NA_boxplot.html`, tabbing forward to segment 19 (the last one) and then clicking Prev repeatedly did nothing — stuck on the same segment. User's own diagnosis ("processing order issue") was correct.

Root cause: `segTab(dir)` computes the new `_segIdx` from the *old* `_specSegments` array, writes that segment's `.lo`/`.hi` into the Freq-range textbox(es), then calls `update()` → `_recomputeSpecSegments()`, which **recomputes `_segIdx` from scratch** by scanning for the highest-indexed segment whose `.lo` is `<=` the just-written textbox value (`_recomputeSpecSegments()`'s own index-recovery logic exists so that changing a *filter*, not just tabbing, keeps you near the same frequency). This round-trip is lossy whenever two segments share the same `.lo` — which happens for real on this data: consecutive single-point "spike" segments at the same frequency (e.g. two rows for the same frequency with conflicting Upper/Lower Spec — see finding #4's inverted-row anomaly) produce segments like `20000.000–20000.000` back-to-back. Writing `_segIdx=18`'s `.lo` into the textbox, then re-deriving `_segIdx` from that value, always resolves to the *last* segment with a matching `.lo` — segment 19 again — so Prev from 19 silently snaps right back to 19 every time.

Fixed by adding a `_segIdxPinned` flag (declared alongside `_specSegments`/`_segIdx`): `segTab()` sets it to `true` right after computing the intentional new `_segIdx`; `_recomputeSpecSegments()` checks it first and, if set, trusts the already-correct `_segIdx` (just clamping it back into bounds in case the segment count changed) instead of re-deriving it from the textbox — clearing the flag afterward so a real filter change still gets the normal recovery behavior. Applied to all 7 duplicated copies of this segment-tab machinery (scatter, distribution, stat_summary, env_coverage, boxplot, summary — matching the same 7x duplication already tracked for the precision fix in finding #1). Verified end-to-end on the real NA boxplot page: 25 consecutive Next clicks correctly clamp at segment 18 (0-indexed, 19 total), then 3 consecutive Prev clicks correctly decrement 18→17→16→15.

---

## Open question: selectable x-axis / secondary numeric dimension (raised 2026-08-10, undecided)

Two analytics in the same real pod each have a genuine *second* swept numeric column the tool currently can't expose at all (see finding #3 above). Two options discussed, not yet decided:

- **Full selectable x-axis**: embed both numeric columns per point, add a client-side selector, and rework every x-axis-dependent piece of JS (segment-tab boundary detection, frequency-range filtering, all of boxplot/stat_summary/env_coverage's frequency-keyed aggregation) to work generically instead of assuming `Frequency_MHz`. Real architecture change, not a quick patch.
- **Expose as a filter dimension instead** (smaller, faster): treat the secondary numeric column like `Port`/`Serial` — a checkbox filter, not a swappable axis. Doesn't let you flip the axis, but lets you isolate one value of it (e.g. "just the 14 dBm sweep"). For genuinely discrete repeated-value columns (46 distinct amplitudes here, not a second continuous co-sweep), this may be the better philosophical fit anyway, not just the cheaper one.

No pod has needed this until now — revisit if/when it comes up again, with a real preference for which of the two (or both) actually matters in practice.
