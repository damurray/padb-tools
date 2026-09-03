# Changelog — 2026-08-17 to 2026-09-03

Pulled directly from git history. A consolidated **week-of-Aug-27 summary** is first; the full day-by-day log follows.

---

## Week of 2026-08-27 → 2026-09-03 (53 commits)

### Interactive plots — features
- **Multi-select "Group by"** in every view — pool/split by any *combination* of parameters (boxplot first, then scatter, stat_summary, summary, env_coverage). None selected = Condition; a subset pools across the parameters you didn't pick; legend/tables reflect the combination.
- **Distribution: condition-dimension filters** (AlcState, Mode, …) added for filter-parity with the other views — previously it only filtered by Spur Type / serial / port / temperature / frequency. Filtering recomputes the KDE curves live in both Absolute and ΔTemp modes.
- **"Autoscale Y"** button on all six views (rescales Y to the visible data without disturbing the frequency zoom).
- **Segment-by** tab-through now narrows the condition filters to each band, not just the frequency window.
- **"Copy PADB Filter"** rewritten to real PADB syntax and expanded into a **three-mode dropdown** (Plot view / Global Filter only / Plot + GF), with "Global Filter only" reproducing the GF's full captured scope.
- **GF Inspect-mode** now resets to Exclude on every page load, with a prominent "⚠ INSPECT MODE" banner while active.
- **Boxplot normality on grouped conditions** — grouping by the natural test conditions now shows the real Shapiro / NP-TI (matching "Group by: Condition"), including when Temperature is one of the group dimensions; honest, specific fallback wording otherwise.
- **`binary_encode`** extended to boxplot per-point data; Help-panel guidance on why pooled statistics can look off.

### Interactive plots — fixes
- Segment-by **collapsed the list and hid the Prev/Next bar** mid-tab on single-point bands — fixed in all six views.
- **Global Filter was browser-global** — a GF built on one dataset leaked onto unrelated pages (and kept its checkbox highlighted); now **scoped per-analytic**.
- Stat Summary Segment-by didn't move the frequency axis; boxplot Group-by/segment-tab desyncs; boxplot initial render ignored restored filter state; stats-table default row could disagree with the plot on a duplicate.
- env_coverage Stats Table now syncs to a plot drag-zoom; stat_summary spec-direction display fix.
- Serial/Port panels were missing on `binary_encode` (compare) plots; boxplot GF Exclude semantics + serial/port + multichannel fixes.
- Site Population Check: excluded-all-data with no serial panel, fence-cell column bleed, quality-hint bugs, benign-direction verdicts (+ Site dup-pts column).
- Boxplot "Passing only" now warns when there's nothing to compare against.

### Web app
- **Publishing is opt-in per run** (default off / `--no-publish`); job files never rewritten. **"Module" → "Folder name"**, plus a **"Share path (override)"** field and a **settable default share root** (persists to `padb_config.json`), with how-to tooltips.
- **"View log"** link on job failure; **"Select Filtered"** button; the "Dry run" checkbox removed.
- Auto-resume no longer **loops forever** on a permanently-failing sibling; restart-mid-loop hardening; re-subscribe to active jobs on browser refresh.
- **"Clean up orphaned PADB-R"** now elevates on demand (one UAC prompt) and also finds parentless `R-Host.exe`.
- Compare panel: busy-state feedback + queue position; CSV name filter + Refresh; x-axis unit inheritance/cross-check; `PADB-Compare` default publish root; `padb_csv_check.py` publish pre-flight gate.
- **`Start_web.bat`** convenience launcher.

### Pipeline & tooling
- `padb_v2.py`: clear **"no matching test data"** failure logging (`build_failures.log`) instead of a raw traceback; **oversized-view size guard** (warns when a self-contained HTML is too big for a browser).
- `results_padb` is now the only trusted output source (no stale R-Plots fallback), plus collection false-negative fixes.
- `_build_help_pdfs.py`; documentation brought fully current.

---

## Bug Fixes

- **Aug 17** — PADB-R.exe exclusivity guard was blocking new job launches behind an idle GUI window that wasn't actually running a batch job
- **Aug 18** — `toggleLogX()` discarded an active zoom/pan on every toggle
- **Aug 18** — Webapp's "delete job" could delete a job's `.sao` file along with its results data
- **Aug 18** — `summary`'s Upper/Lower-limit data filter excluded nothing, regardless of the threshold entered
- **Aug 18** — `stat_summary`'s Statistics Table didn't respect active filters the first time it was opened
- **Aug 18** — Default frequency-range boxes silently clipped the true min/max data point (rounding-direction bug)
- **Aug 18** — Boxplot's "Clear everything" was wiping the Global Filter (should only clear local filters); boxplot frequency labels could collide for close-in frequencies
- **Aug 18** — Boxplot Statistics Table ignored "Group by: Serial Number/Port"
- **Aug 18** — `summary`'s Spec Hi/Lo and Margin columns ignored the manual TLL override
- **Aug 19** — `stat_summary`'s Spec Lo override had inverted priority vs. Spec Hi (never actually applied)
- **Aug 19** — Bundled fix set: `summary` temperature-filter blindness in the data filter, frequency-arrow-key stepping precision bug, boxplot's "Copy PADB Filter" bug, webapp log buffering (stdout not flushing live)
- **Aug 20** — Stale leftover files in R-Plots could clobber a fresh `-dir` extraction's real output
- **Aug 20** — Boxplot: deselecting every condition checkbox showed *everything* instead of nothing
- **Aug 21** — Boxplot Group-by condition filter had no effect once grouped by Serial/Port
- **Aug 21** — Boxplot Statistics Table's frequency column header ignored the `x_label` override
- **Aug 21** — Reset left the Global Filter stuck in "Inspect" mode across unrelated pages; boxplot's "Excl outliers" didn't affect "Show points" or the table
- **Aug 21** — Webapp: "Run Selected" could queue the same job twice; Compare panel's CSV dropdown went stale
- **Aug 21** — Webapp restart was killing an in-progress job's own subprocess (stdout pipe closed on parent death — lost a real ~13-minute build)
- **Aug 25** — Boxplot Dup-runs count was missing Port from its duplicate-detection identity (a DUT's RF1/RF2 rows were miscounted as duplicates of each other)
- **Aug 25** — `stat_summary`'s Statistics Table pushed the plot out of view when opened (DOM ordering bug, unique to that view)
- **Aug 31** — Boxplot Group-by/segment-tab filter desyncs; segment-tab left a stale Autoscale-Y pin
- **Aug 31** — Site Population Check excluded all data on a page with no serial filter panel
- **Sep 1** — Cross-site compare boxplot was missing its Serial/Port filter panels entirely (data stored as `vals_detail_bin` under binary_encode, so the panels computed from the wrong source)
- **Sep 1** — "Copy PADB Filter" Global-Filter output was a bare `'Serial Number' NOT IN {...}`; now reproduces the GF's full captured scope (harmonic/condition, frequency range, serials)
- **Sep 1** — Global Filter could get stuck in "Inspect" mode across unrelated result pages (browser-global setting); now resets to Exclude on every page load, with an amber "⚠ INSPECT MODE" banner while active
- **Sep 2** — Stat Summary: stepping "Segment by" updated the spec lines but left the frequency axis where it was — the axis stayed pinned to an earlier segment/zoom range instead of moving to the stepped band

## Feature Improvements

- **Aug 17** — Warning when R-Plots collection looks stale; "Hide spec lines" checkbox added to scatter, then extended to distribution/stat_summary/boxplot/summary; "Delete job" added to the webapp
- **Aug 18** — Jump-nav TOC for PADB Simple mode's gallery; V2 `index.html` now groups links by analytic instead of one flat list; zoom/pan persistence across filter changes (scatter first, then all 6 interactive views); Statistics/Results Tables auto-refresh below a condition-count threshold with a highlighted manual "Refresh" above it; Reset button added to stat_summary/env_coverage; drag-zoom now syncs to the frequency slider/table in boxplot and stat_summary; Outliers column split into Max +Δ/Max −Δ by sign; lower-side TLL manual override added to boxplot and summary
- **Aug 19** — Spec/TLL overrides now visually flagged (orange highlight) instead of silently substituted; **cross-site comparison feature** added (`compare_csv` job.json key, webapp Compare UI, full docs); boxplot Site Population Check scoped to the current frequency window
- **Aug 20** — Site Population Check added to stat_summary, then extended (with the coverage-gap banner) to summary; `padb_make_v2_job.py` now auto-detects non-frequency x-axis pods
- **Aug 21** — Adaptive Group-by defaults for boxplot (Serial Number above 150 conditions); Segment-by control now hides itself when there's nothing to segment; "Legacy" mode retired from the webapp; orphaned PADB-R.exe cleanup button added
- **Aug 24** — Boxplot "Dup runs"/"Genuinely repeated freqs" columns (Statistics Table + Site Population Check); Site Population Check CSV export (boxplot, then stat_summary/summary); pod `Filter_Expression` now shown in the Help panel; opt-in "Collapse dup runs" toggle for boxplot; "SR dup pts" changed from a summed total to a per-DUT breakdown list
- **Aug 25** — Noise-sensitivity disclaimers added to env_coverage, distribution, stat_summary, and summary
- **Aug 31** — "Autoscale Y" button added to all six interactive views (rescales the Y axis without disturbing the frequency zoom); Segment-by tab-through now narrows the condition filters to match each segment, not just the frequency window; `Start_web.bat` convenience launcher for the web app; `_build_help_pdfs.py` to regenerate the local PDF copies of the help docs; fast-path "Hide spec lines" toggle
- **Sep 1** — "Copy PADB Filter" rewritten to match real PADB filter syntax and expanded into a three-mode dropdown (Plot view / Global Filter only / Plot + GF); Compare webapp panel gained a CSV name filter + Refresh, wider dropdowns/Description, and x-axis unit inheritance; `padb_csv_check.py` gained a `publish_to` pre-flight gate; compare jobs default-publish to a `PADB-Compare` share tree
- **Sep 2** — Webapp: "View log" link on job failure (full console written to `<results_dir>/webapp_console.log`); "Select Filtered" button (checks every job matching the current Mode/Kind/Name filters); the "Dry run" checkbox was removed from the UI (CLI `--dry-run` unchanged)
- **Sep 2** — Webapp: publishing is now opt-in per run — a "Publish to share after run" checkbox (default off, passes `--no-publish`); job files are never rewritten. `padb_v2.py` gained a `--no-publish` flag to match `padb_run.py`
- **Sep 2** — Webapp: "Clean up orphaned PADB-R" now elevates on demand (one UAC prompt) to kill orphaned `R-Host.exe` a non-elevated `taskkill` couldn't touch — no need to run the whole server as administrator
- **Sep 2** — Generate Job: renamed "Module" → "Folder name" (the subfolder under the fixed share root), plus a new "Share path (override)" field for an exact `publish_to` off the standard tree (`--publish-to` on both job generators)
- **Sep 2** — Generate Job: settable **Default share root** (persists to `padb_config.json`), so other products/users point the tool at their own share tree without editing code
- **Sep 3** — Webapp: an auto-resume for an interrupted V2 plot chain no longer loops forever when one sibling can never build — a permanently-failing sibling is retried at most once, then recorded and left alone
- **Sep 3** — `padb_v2.py`: a plot build that has nothing to plot now logs a clear reason (e.g. "no matching test data — PADB placeholder export") to `build_failures.log` in the results dir (and the job console) instead of a raw traceback; compare jobs note any site whose CSV has no matching data, using the other site as the reference
- **Sep 3** — `padb_v2.py`: a generated view whose self-contained HTML is very large (≥80 MB) now logs a size warning with concrete options (`binary_encode`, `scatter_decimate`, narrower extraction, or that a per-frequency boxplot over thousands of offsets isn't a fitting view), so a page too big for a browser to render is diagnosed instead of looking like "no plot data" (real case: an 8.5M-row phase-noise DCFM boxplot → 517 MB HTML)
- **Sep 3** — Boxplot "Group by" is now a **multi-select**: pool the boxes by any *combination* of parameters (Ctrl/Cmd-click), not just one dimension or all. Selecting none = Condition (no pooling); a subset pools across the parameters you didn't pick. Legend and Statistics Table reflect the combination.
- **Sep 3** — Multi-select "Group by" extended to **scatter, stat_summary, summary, and env_coverage** — group/split by any combination of parameters in every view, for consistent cross-view comparison. (scatter splits traces by the combination, none selected = one combined trace.)
- **Sep 3** — Segment-by (all 6 views): tabbing **Next/Prev collapsed the segment list to 1 and hid the whole Prev/Next bar** when a segment narrowed the conditions to a single-point band (e.g. Harmonics with `[7.99, 7.99]` bands). The segment list is no longer rebuilt mid-tab, so you can step through every band; a genuine filter change still rebuilds it.
- **Sep 3** — Global Filter was **browser-global** — a GF built on one dataset showed up (and its checkbox stayed highlighted) on completely unrelated results pages. It's now **scoped per-analytic** (per scatter-CSV output): still shared across that analytic's own views, but never bleeds across different analytics or test types.
- **Sep 3** — Distribution view now has **condition-dimension filters** (AlcState, Mode, …) like the other views, so it can subset to the same data — previously it only filtered by Spur Type / serial / port / temperature / frequency. Each dim's per-point values ride along in the KDE recompute (both Absolute and ΔTemp modes).
- **Sep 3** — Boxplot Statistics Table: grouping by the natural test conditions (e.g. AlcState + HarmonicNumber + Mode) showed "— (no normality test)" even when each group is a single real condition. It now shows the real **Shapiro normality / NP-TI** (matching "Group by: Condition") when a group is one condition on Room data with no distorting filters — and still withholds it, honestly, when the population is genuinely pooled (multiple conditions, non-Room, or a serial/value/GF filter changes it).
