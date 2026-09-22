# Changelog — 2026-08-17 to 2026-09-21

Pulled directly from git history. Newest summary first; the full day-by-day log follows.

---

## 2026-09-21

### Boxplot filter fixes (found via a relational filters→plots→tables→stat-tables audit)
- **Compare boxplot no longer zeroes the site missing a grouping key.** On a cross-site compare, one site's rows can carry a grouping key the other's don't (e.g. AMC has `Test Event Status`, SR doesn't). Deselecting *any* filter used to uncheck every row of the site lacking that key (its condition strings didn't match the absent dimension's regex), blanking half the plot with no recovery. An absent dimension now imposes **no constraint** (a no-op for single-site pods, where every condition carries every key).
- **Boxplot "Data filter" cleaned up (Q1).** The confusing `All / Passing only / Upper limit / Lower limit` radio group — which jammed a pass/fail axis together with a raw-sample *trim* and had no "Failing" — is now a clean pass/fail axis **All / Passing only / Failing only** (Failing is the exact complement of Passing), plus a **separate, always-visible "Trim raw samples: above/below"** control that's independent of the pass/fail filter and the Spec override (so it can combine with either). The Spec override is unchanged (it sets the pass/fail *threshold*; the trim removes *data*). Applied consistently to the plot, the grouped + non-grouped Statistics Table, the per-point table, and the outlier collector.
- **Boxplot Workflow button** now uses the shared helper (flipping ▸/▾ caret) like the other four views.

### Boxplot pass/fail + spec lines were wrong for per-condition specs (found on a Harmonics compare)
- **Table #fail is now per-point vs each point's OWN Upper/Lower Limit**, not a single flat `HI_SPEC`/`LO_SPEC`. The flat spec was the first data row's limit, so on a pod where conditions/frequencies carry different specs (Harmonic 2's staircase ≠ 0.5's) the table counted **phantom failures the plot's own spec line never supported** — measured 7,501 flat-spec "fails" vs 974 real (own-limit) for Harmonic 2. Applied to the grouped table, per-point table, and outlier collector (`_boxPtLim`/`_boxFailCountDetail`/`_boxFailCellDetail`); the flat `_boxFailCell`/`_boxFailCount`/`_boxPfLimits` are gone.
- **One spec line per condition.** The plot min-pooled every selected condition's spec into a single (tightest) line, so 2 and 0.5 collapsed to one misleading line. Now draws one line per **distinct** per-condition Limit staircase (`_limitMapsByGroup`), collapsing to a single red line when identical (the common case).
- **Autoscale Y re-fits when the plotted set changes.** An Autoscale-Y pin used to persist across filter changes, clipping a newly-added condition off-screen while the table still listed it (looked like "plot shows only 0.5, table shows both"). It now re-fits Y when the condition/serial/temp/freq selection changes; a manual drag-zoom still persists, and a configured `Y_LIM` still wins.
- **Pass/fail VERDICT falls back to PADB's `Test Event Status` (P/F) when there is no numeric limit.** On a no-limit pod (e.g. phase-noise offset compares) the "Failing only" filter showed **nothing** on the plot and the per-point table had **no Status column** — the recorded P/F verdict was ignored. A single `_boxVerdict(condition, point, filter)` rule now drives the Passing/Failing filter **and** the table Status / #fail: numeric limit wins (each point's own, override respected), else the recorded `Test Event Status` verdict (auto-detected as `BOX_STATUS_FIELD`). So filters → plot → table stay consistent whether pass/fail comes from a limit or PADB's verdict. Verified on the ECF compare: Failing-only isolates the 41 F points on the plot, and both tables show 41 (was 0).

### Parquet sidecar gate is size-based, not compare-blanket
- `_maybe_export_parquet` used to auto-write a parquet sidecar for **every** compare job regardless of size — so a tiny switching-speed compare (e.g. `QA_Switching`, 1.5 MB) got a needless sidecar *and* a misleading "Large-dataset viewer" section on its index. The parquet viewer only helps when the self-contained HTML is too big to open, so it now auto-exports **only for large jobs** (the same size/row thresholds as `binary_encode`) — for compares and non-compares alike. `export_parquet: true` still forces one on a small job. (Existing small compares keep their already-written sidecar until rebuilt with the stale `.parquet` removed.)

### QA
- New `qa_regressions` pins (`test_compare_boxplot_absent_dim_and_caret`, `test_box_data_filter_passfail_and_trim`, `test_box_fail_per_point_limit_and_spec_lines`, parquet size-gate + verdict/cross-sweep guards, 321 → 352) and `qa_filters` behavioral checks — the Q1 data-filter set (`box-failing-isolates-nonempty-subset`, `box-passing-strict-subset`, `box-passing-failing-partition`, `box-trim-combines-with-failing`) plus `box-perpoint-fail-equals-own-limit` / `box-perpoint-fail-not-flat-spec` (would catch a flat-spec regression: on the Harmonics compare the table must show 974, not 7,501). Proven with teeth (a deterministic synthetic shows 6 own-limit fails vs 30 flat-spec, and the table shows 6). Ran the browser-tier relational gate across single-site, spur-staircase, and cross-site-compare galleries.

### Docs
- **Interactive_Plots_User_Guide.md**: added **Reference Statistics** and **Histogram** (switching-speed) view sections, corrected the Room-temperature view rules (Summary + Stat Summary are Room-only defaults now), a **"What's new in comparison mode"** subsection, and updated the Boxplot Data-filter description. Compare cheatsheet "Known gaps" refreshed (Site Population Check is on all six views).

---

## 2026-09-04 → 2026-09-18

### Large-dataset viewer (parquet)
- **Compact `.parquet` sidecar + `padb_viewer.py`** — self-contained HTML can't open a giant analytic (wide phase-noise sweeps, big compares, millions of points). `padb_v2.py` now writes a zstd parquet sidecar (80–140× smaller than the CSV) for compare jobs and large inputs; `padb_viewer.py` is a local Flask server that serves only the decimated/filtered slice being viewed, with a frequency filter, Site/temperature checkboxes, and band-view buttons that render any full view for the current range. `build_viewer.py` freezes it to `PADB_Viewer.exe`. The results `index.html` links it ("Open in viewer" / "Open folder" / `Open_in_viewer.bat`) and flags it **optional** — the HTML plots carry the same analysis; only reach for the viewer if they're too big to open.
- **Viewer main-plot Autoscale / Reset axes / drag-zoom now drive the frequency filter** (and re-query), so zooming refines detail and the band-view buttons stay scoped.

### Interactive views
- **Busy/loading overlay on every view** — a "Loading plot data…" spinner painted before the data parses, cleared once the plot renders, so a large page never looks dead.
- **Busy spinner on table/panel render + Site-check row cap** — the Site Population Check panels paint a "Computing Site Population Check…" spinner and build on the next frame (so a heavy panel shows progress instead of freezing), and their per-point detail tables cap at 5,000 rows (the summary counts and CSV export still cover all rows). This covers the previously-unguarded slow tables (e.g. a 62-dimension switching-speed compare with ~12k+ non-primary rows). **Extended to the main Statistics/Results tables** (stat_summary, boxplot, summary, env_coverage): the **"Refresh table"** button — the explicit large-rebuild above the ~150-condition size-gate — now paints a "Building Statistics Table…" spinner and builds on the next frame too. The fast auto-refresh path (below the gate) stays instant/unwrapped, and the compute functions remain synchronous (so programmatic/QA callers are unaffected — only the user-initiated open/refresh defers). **Histogram:** it has no size-gated "Refresh table" button, so its own **Statistics panel** toggle (`toggleStats`) is spinner-wrapped instead — this is what gives the switching-speed compares (histogram-only) a table-render spinner. Shared helpers `PADB_spinnerHtml` / `PADB_deferRender` live in the common JS prelude. **Net scope:** busy indicators now cover the plot load (all views), the Site Population Check panels (all views that have one), the four Statistics/Results "Refresh table" builds, and the histogram Statistics panel.
- **Scatter "Worst first" sort** now ranks by largest **error relative to spec** (furthest past the limit), falling back to raw value only when the data has no spec.
- **Compare-to selector (fence / Spec-Limit / both)** rolled out to all six views; **Site Population Check** selectable comparison basis; extended to env_coverage/distribution/histogram (with edit-reimport for the histogram, which has no Global Filter).
- **Auto-filter bad DUTs + ⚙ Workflow & Recommendations** matured: per-site risk + reference/onboarding/both **site scope** on compares, **"Remove auto-filter"** (subtract only the auto increment), a **subpopulation / dual-distribution advisory** rolled out across the population views, and a "why no Apply button" explainer.
- **Statistics/Results tables**: Grouped/Per-point toggle + **"# fail / n"** column (boxplot → stat_summary → summary); per-point Pass/Fail vs the effective limit; scatter data-rows table shows Spec/Limit bounds + per-point Pass/Fail. Per-point Limit/Status now honour a **manually-typed Spec** on data with no CSV spec.
- **Scatter**: per-DUT line view + Draw modes + Smooth (phase-noise style); Room temperature is filterable.

### Test-point reduction & sentinel
- **`padb_testpoint_reduce.py`** (report-only test-plan trimmer, adaptive-noise ε + data-driven ceiling) and **`padb_sentinel.py`** (run-to-run audit-diff) added, with a reduction-study extraction primitive (all-runs + run-datetime grouping), a run-aware per-DUT scatter view, and a worked example. Wired into the webapp compare menu (reduce on merged data).

### Comprehensive PDF report
- **`padb_pdf_report.py`** — opt-in multi-view PDF (cover + every view with its table) built at build time via Playwright/Chromium (headless Edge is dead on this box); filter-aware and on-demand variants; site-scope option; index links + publishes the PDF.

### Hardening & QA
- **Shared JS prelude** (`_COMMON_JS`) — single `PADB_isFail` / `PADB_specClass` / `PADB_fence` for every view; Site spec-classifiers and fence helpers collapsed onto the shared rules. Plotly 3.6 axis-title fix across all views. New behavioral gate `qa_jsrules.py`, cross-view `qa_gf_crossview`, and a feature registry that fails until a feature is in all views.
- **Webapp**: control-context clarity pass (optional/destructive tagging); compare panel creates-the-job-only; single-instance guard; `Start_web.bat` frees port 5000.

---

## Week of 2026-08-27 → (in progress)

- **Histogram auto-filter undo wording fixed (2026-09-15)** — the auto-filter's undo button on the **Histogram** now reads **"Clear auto-exclusion"** (with a view-local tooltip), not the misleading **"Clear global filter"** — the histogram has no shared Global Filter; its auto-filter affects only that page. The remaining shared prose (the auto-filter control tooltip, the Workflow & Recommendations steps, and the closing "reversible via …" note) is now ctx-driven, so the histogram consistently says "auto-exclusion" everywhere while Boxplot/Stat Summary/Summary/Env Coverage keep "Global Filter". Documented in the Interactive Plots User Guide; pinned by `qa_regressions.py` (`undo-label:` checks).
- **New `histogram` view** — a CSV-driven interactive value-distribution histogram for tests with no swept x-axis (switching speed and similar "one number per event" measurements). Auto-detected bins (Freedman–Diaconis), overlaid conditions (e.g. per port), a spec-limit line, and a stats panel (n / mean / median / p95 / p99 / max / % out-of-spec). `padb_make_v2_job.py` now routes any Type=80 analytic whose x-axis is text / single-value (`[T]` or `(1 x 1)`) to `views:["histogram"]` automatically; the Type=80 analytic's CSV remains the raw-value source.
- **Scatter: "Table" view** — a toggle that shows the currently-plotted rows (after all filters) as a table below the plot, same columns as the CSV export, capped at 2000 rows with a "use Save CSV for all" note above that.
- **Scrollbar gutter reserved** on every interactive view — stops the vertical scrollbar toggling on/off (and briefly hiding the buttons below the plot) when a filter/panel/re-render changes page height.
- **Fixed:** the scatter's GF "Inspect" toggle wrote the *unscoped* GF-mode key (regression from the per-analytic GF scoping) — it now uses the scoped key.

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
- **Sep 8** — Stat Summary: on first load with a restored/narrow frequency window, the spec lines were drawn for *all* frequencies (both spec bands), only correcting after a Segment-by (or any other) click. The init rendered via a bespoke 2-arg `buildLayout()` that skipped the frequency window (and used the unfiltered condition list), so the spec-line set didn't match the windowed data until the first real `update()`. Init now renders through `update()` like every later frame, so the first paint is consistent (verified: restored freq_hi=400 shows only the in-window band's spec on first render, no click needed).
- **Sep 8** — Distribution: setting Freq min = Freq max (a single frequency) showed no data. `getFreqRange()` read the range *slider* (whose `.value` the browser snaps to its coarse `step`), so a single-frequency window landed between real frequency samples and matched zero points. Now reads the exact text box (same fix as scatter/summary). Also: when a narrow selection leaves too few points for a KDE (< 4), the raw value(s) now render as a rug ("N pts, n<4 for KDE") instead of a silent blank, so a single test point is still visible.
- **Sep 3** — Boxplot & Stat Summary fabricated fake "serial numbers" from the condition string on pods with no serial data (Close-In: Group is AlcState/Mode/SpurType only, no serial anywhere) — the Serial filter and "Group by: Serial Number" listed *conditions*, not serials, and grouping by them did nothing. With no genuine serial, both views now correctly hide the Serial controls (matching the scatter, which already showed no serials) instead of a broken condition-as-serial panel. (The data-side fix is to add Serial Number to the pod's Group By so real per-DUT serials are extracted.)

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
- **Sep 3** — **Global Filter now honored in the Distribution view** — excluding a DUT/condition anywhere else in the tool now also drops it from the density curves (Distribution was the last interactive view without GF support). An "apply GF" checkbox + badge appear only when a GF is set; zero change when it's empty.
- **Sep 3** — **Terminology cleanup (TTL/TLL → Spec):** boxplot's and summary's manual override actually overrides the *Spec* limit (not a computed TTL/TLL band), so its label now reads "Spec↑/↓ override", the drawn line "Spec (manual)", and the side selector "Limit display". The genuinely-TLL controls (stat_summary's computed spec-minus-guard-band, its own "TLL override") are unchanged. Behavior identical — labels/tooltips only.
- **Sep 3** — **Stat Summary and Summary each got a scope subtitle** (Room-temperature-only vs. all-temperatures-combined) so the two similar-looking views' complementary purposes are clear at a glance.
- **Sep 3** — **Help panel now lists dimensions held constant** across the whole dataset (e.g. "Held constant: AlcState = TRUE, Mode = 0"), so a single-setting parameter reads as intentional rather than a missing filter/Group-by control.
- **Sep 3** — **`padb_v2.py` auto-enables `binary_encode` for large data** — when a CSV is ≥ 25 MB *or* ≥ 250,000 usable rows, the numeric arrays are float32-packed automatically to cut page size/latency (a plot-transparent optimization — it changes only file encoding, never a displayed value). An explicit `"binary_encode"` in job.json (true or false) always wins; thresholds are per-job overridable. Heads off oversized/unrenderable pages without a manual step.
- **Sep 3** — **Removed three dead code paths** (a duplicate static `de_summary`, the superseded `_isPoolableGroupBy`, and stale env_coverage save/load references to controls it never renders) — no behavior change; cleanup only.
- **Sep 8** — **New QA toolchain + `QA_GUIDE.md`.** Two standalone gates added: **`qa_csv_sweep.py`** runs the pre-flight check across every CSV under given roots (x_col-aware — applies each CSV's job `x_col` so a non-frequency-axis analytic like a `Rate (kHz)` Flatness sweep isn't false-FAILed; skips tool intermediates), and **`qa_view_sweep.py`** rebuilds real job.json files into a temp dir and headless-verifies each view actually renders (plus per-job needle checks), with a portable per-user `qa_view_sweep.json` coverage manifest (`--write-manifest` to start one). Both take `--root`/`--job` so other groups point them at their own data. Documented end-to-end in the new `QA_GUIDE.md` (the tiered approach, each tool, a full-pass recipe, the pod-variety coverage matrix, and how to read WARN vs FAIL).
