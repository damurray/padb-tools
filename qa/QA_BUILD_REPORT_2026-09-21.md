# QA Build Report — Boxplot pass/fail, spec lines, Autoscale-Y, verdict (2026-09-21)

Scope: the boxplot correctness fixes made this session, plus the Q1 Data-filter
cleanup. All work is in `padb_plots.py` (+ `qa_filters.py` / `qa_regressions.py`
teeth). Verified via the deterministic gates below **and** browser-tier behavioural
checks driven through a real headless Chromium (Playwright `chrome-headless-shell`;
system Edge headless is dead on this box).

---

## 1. What changed (under test)

| # | Fix | Root cause |
|---|---|---|
| A | Table #fail / per-point Status judged per-point vs each point's **own** Upper/Lower Limit (override wins) | Table used a single flat `HI_SPEC`/`LO_SPEC` (= first data row's limit) → phantom fails when conditions/frequencies carry different specs |
| B | One spec line **per condition** (distinct staircase; collapses when identical) | Plot min-pooled every condition's spec into one misleading line (2 & 0.5 collapsed) |
| C | Autoscale-Y **re-fits** when the plotted set changes; manual drag-zoom still persists | An Autoscale-Y pin clipped a newly-added condition off-screen while the table still listed it |
| D | Pass/fail **verdict** falls back to PADB's `Test Event Status` (P/F) when there is no numeric limit — drives the Passing/**Failing-only** filter **and** the table Status/#fail | No-limit pods: Failing-only showed nothing; table had no Status, ignoring the real P/F verdict |
| E | Q1 Data-filter cleanup: `All / Passing only / Failing only` + separate always-on "Trim raw samples"; compare boxplot no longer zeroes the site missing a grouping key; Workflow caret unified | (earlier this session) |

Design invariant enforced throughout: **filters → plot → table are one chain**, driven
by a single `_boxVerdict(condition, point, filter)` rule (limit → else recorded verdict),
so the table follows the plot follows the filters, by construction.

---

## 2. Deterministic gates (no browser)

| Gate | Result |
|---|---|
| `qa_selfcheck.py` (umbrella, vs `qa_baseline.json`) | **GREEN** — no known check regressed |
| `compile` | 20/0 |
| `qa_padb.py` (synthetic baseline) | 37/4 (unchanged pre-existing) |
| `qa_regressions.py` (source-contract pins) | **347/0** (321 → 347 this session) |
| `qa_stats_recompute` / `qa_webapp` / `qa_subpop` / `qa_js_segments` / … | unchanged, ok |

New/updated regression pins (source contracts, run every build in the umbrella):
`test_box_fail_per_point_limit_and_spec_lines` (per-point-own-limit helpers, per-condition
`_limitMapsByGroup`, prefix hide-spec, Autoscale-Y re-fit, **verdict helpers + `BOX_STATUS_FIELD`
+ P/F status-field detection + Passing/Failing routes through `_boxVerdict`**),
`test_box_data_filter_passfail_and_trim`, `test_compare_boxplot_absent_dim_and_caret`.

---

## 3. Browser behavioural checks (the ones that would catch these bugs)

### 3a. Verdict pod — phase-noise ECF compare (`Test Event Status` = P/F, **no numeric limit**)
Independent recompute: 41 failures. All three surfaces agree, and differ from the flat spec:

| Check | Measured |
|---|---|
| `box-verdict-failing-plot-isolates-fails` | plot Failing-only = **41** = verdict (was **0** before) |
| `box-perpoint-fail-equals-independent-verdict` | per-point table #fail = **41** = verdict |
| `box-verdict-grouped-fail-total-matches` | grouped table #fail total = **41** = verdict |
| `box-perpoint-status-column-present-when-verdict` | Status column now present |
| `box-verdict-not-flat-spec` (teeth) | table = 41 ≠ flatSpec = 0 |
| Passing + Failing partition | 5003 + 41 = 5044 (All) |
| **Page total** | **84 PASS / 0 FAIL / 6 skip** |

### 3b. Numeric-limit divergence — deterministic synthetic (2 conditions, different specs)
Proves the flat-spec phantom-fail bug and its fix with unmistakable teeth:

| Metric | Value |
|---|---|
| own-limit failures (correct) | **6** |
| flat-spec failures (old, wrong) | **30** |
| grouped table #fail | **6** ✓ |
| per-point table #fail | **6** ✓ |
| spec lines drawn | **2** (`Spec Hi [SpurType: A]`, `[SpurType: B]`) |

### 3c. Spur staircase — clock_leakage (per-SpurType specs)
`45 PASS / 0 FAIL`; **8 distinct per-condition spec lines** drawn (was 1 min-pooled);
verdict teeth honestly **skip** (this pod has 0 failures, so own==flat==0 — a flat-spec
regression would be invisible here, and the check says so rather than passing vacuously).

### 3d. Q1 Data-filter + compare absent-dim (earlier)
`box-failing-isolates-nonempty-subset`, `box-passing-strict-subset`,
`box-passing-failing-partition`, `box-trim-combines-with-failing`, and the compare
absent-dim/caret checks — all pass on single-site, spur, and cross-site-compare galleries.

---

## 4. Filter coverage — singly and crossed

**Singly** (each filter changes the plot AND the table stays consistent — all covered by
`qa_filters.py`, verified on real pages this build):

| Filter | Check(s) |
|---|---|
| Condition dimensions | `table-updates-on-filter`, `table-n-matches-plotted-points`, `table-conds-are-plotted`, `filter-had-no-effect` |
| Serial | `deselect-serial-removes-it` / `-keeps-others` / `reselect-serial-restores` |
| Site (compare) | `deselect-site-removes-it` / `-keeps-others` / `reselect-site-restores` |
| Frequency range | `freq-range-narrows-plot` / `-table` / `-restores` |
| Drag-zoom (X) | `drag-zoom-narrows-plot` / `-table` / `-syncs-freq-inputs` |
| Data filter (Pass/Fail) | `box-failing-isolates-nonempty-subset`, `box-passing-strict-subset`, `box-passing-failing-partition`, `box-verdict-*` |
| Trim raw samples | `box-trim-combines-with-failing` |
| Group by (Condition/Serial/Port/multi) | `group-by-all-modes-nonblank`, `group-by-reversible`, `plotted-groups-in-table` |
| Global Filter | `clear-GF-restores`, `filter-GF-keeps-others`, `outliers-GF-*`, cross-view GF precision |
| Reset | `reset-restores` (exact baseline point set) |
| CSV export | `csv-export-matches-plotted-points`, `csv-export-matches-results-table` |

**Crossed** (combinations exercised this build): trim × Failing-only
(`box-trim-combines-with-failing`); Spec-override × Passing/Failing; Show-points ×
table-mode × Group-by (verdict checks run with all three engaged); drag-zoom × table.

**Honest gap:** there is **not yet** a systematic full cross-product sweep of every
filter pair/triple. Recommended next QA step (proposed, not yet built): a property-based
sweep that applies N random filter combinations and asserts, for each, that the per-point
**table population == the plotted population == an independent verdict recompute** over the
same filtered set — the single invariant that, if held under arbitrary combinations, proves
"filters → plot → table" for the crossed case too. This is the cleanest way to realize
"every filter, singly and crossed" without enumerating an intractable combinatorial matrix.

---

## 5. Environment note
Browser-tier checks ran through Playwright's `chrome-headless-shell`; when no working
headless browser is present they honest-exit 3 (environment, not a product pass/fail) —
they are **not** part of the umbrella `qa_selfcheck` GREEN, so they were run explicitly
against the affected pages (above) and must be for any boxplot change.
