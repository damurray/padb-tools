# Changelog — 2026-08-17 to 2026-08-25

Pulled directly from git history (41 commits). Split into bug fixes and feature improvements.

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

## Feature Improvements

- **Aug 17** — Warning when R-Plots collection looks stale; "Hide spec lines" checkbox added to scatter, then extended to distribution/stat_summary/boxplot/summary; "Delete job" added to the webapp
- **Aug 18** — Jump-nav TOC for PADB Simple mode's gallery; V2 `index.html` now groups links by analytic instead of one flat list; zoom/pan persistence across filter changes (scatter first, then all 6 interactive views); Statistics/Results Tables auto-refresh below a condition-count threshold with a highlighted manual "Refresh" above it; Reset button added to stat_summary/env_coverage; drag-zoom now syncs to the frequency slider/table in boxplot and stat_summary; Outliers column split into Max +Δ/Max −Δ by sign; lower-side TLL manual override added to boxplot and summary
- **Aug 19** — Spec/TLL overrides now visually flagged (orange highlight) instead of silently substituted; **cross-site comparison feature** added (`compare_csv` job.json key, webapp Compare UI, full docs); boxplot Site Population Check scoped to the current frequency window
- **Aug 20** — Site Population Check added to stat_summary, then extended (with the coverage-gap banner) to summary; `padb_make_v2_job.py` now auto-detects non-frequency x-axis pods
- **Aug 21** — Adaptive Group-by defaults for boxplot (Serial Number above 150 conditions); Segment-by control now hides itself when there's nothing to segment; "Legacy" mode retired from the webapp; orphaned PADB-R.exe cleanup button added
- **Aug 24** — Boxplot "Dup runs"/"Genuinely repeated freqs" columns (Statistics Table + Site Population Check); Site Population Check CSV export (boxplot, then stat_summary/summary); pod `Filter_Expression` now shown in the Help panel; opt-in "Collapse dup runs" toggle for boxplot; "SR dup pts" changed from a summed total to a per-DUT breakdown list
- **Aug 25** — Noise-sensitivity disclaimers added to env_coverage, distribution, stat_summary, and summary
