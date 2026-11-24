# PADB Tools v3.0 -- design plan and staged roadmap

v2.5 (tag `v2.5`, branch `develop`) is the last release of the v2 line. v3.0 (branch `v3.0`) is the
"do it over" redesign: **keep what works, replace what made every feature expensive.**

## Why (what v2 taught us)

| Pain in v2 | Root cause | v3.0 answer |
|---|---|---|
| A feature lands in 4 of 6 views; `_COMMON_JS`, `qa_js_segments`, "can't land in one view and miss others" | Filtering, verdicts, aggregation and segment stepping are re-implemented in JavaScript in every view | **One analysis engine** (DuckDB). Viewer and HTML render its output; HTML pages become frozen snapshots of a query |
| Padding-space bug, dropped keys, blank-condition bugs, slightly different parsers | The `Key: value` Group text is re-parsed in several places and engines | **Parse conditions once**, at load, into typed columns (`padb_core.conditions`) |
| Limit overrides trivial in the viewer, hard in HTML; the paused in-page import | Limits are baked into each page and pre-aggregated at build time | **Limits are data**, applied at query time (`padb_core.limits`, a view over a limits table) -- live everywhere, instantly reversible |
| Lock bar, Global Filter, segment bands, per-view checkboxes, persistence: each with its own storage and apply path | Filter state grew one feature at a time | **One declarative FilterSpec** (JSON) shared by every view (`padb_core.filterspec`) |
| compression, binary-encode, decimation, parallel render | The early decision to embed every point as JSON text | **Tiers**: precomputed envelopes/aggregates by default, raw points on demand, columnar binary, error-bounded decimation, automatic routing of giants |
| "limit vs spec vs TLL, which wins" explained in docs and patched in each view | No single "effective limit" | **One effective limit per row**, with the reason recorded (`data` / `override`) |

## What stays

- The **parquet contract** (one wide table per analytic, typed columns) -- padbloader, the viewer and the HTML builds all share it.
- **Self-contained HTML as an export** (opens from the share, no install, archivable, PDF source).
- **QA as the specification**: independent oracles, cross-view invariants, mutation proof, honest-exit tiers.
- The v2.5 code stays working and releasable on `develop`; v3.0 grows beside it and is adopted piece by piece.

## Method (how we avoid a big-bang rewrite)

1. New code lives in `padb_core/` and is **proven equivalent to the existing implementation** by `qa_core.py`
   (the v2 code is the oracle) before anything depends on it.
2. Then each consumer moves onto the core one at a time, with the existing QA suites as the safety net:
   viewer -> `padb_v2` builds -> individual HTML views.
3. Every stage ends with the gate GREEN and a mutation-proven test for the new surface.

## Stages

| Stage | Deliverable | Status |
|---|---|---|
| **1 -- core** | `padb_core`: canonical group parser + typed condition index; `FilterSpec` (the one filter state, canonical SQL); limits-as-data with an effective-limit view and recorded reason; a DuckDB `Dataset` engine (query, envelope, counts, bands, spec stairs). `qa_core.py` proves parity with the v2 viewer engine and parsers | **done on this branch** |
| 2 -- viewer on the core | `padb_viewer.py` drops its private copies (parser, `_where`, column detection, limits UPDATE; ~270 lines gone) and its DuckDB engine IS a `padb_core.Dataset`; `/api/scatter` and `/view` take ONE `spec` (a FilterSpec; legacy params still accepted; junk -> 400); the UI sends one spec. `qa_core` now uses the released **v2.5 tag** as its oracle (loaded via `git show`) and pins the adoption (`CORE-ADOPT`) | **done on this branch** |
| 2b -- separate analysis from extraction | The last analysis->extraction import is gone (`padb_v2 -> padb_run`: the filename rules moved to `padb_names`, the dead PADB-R runner stub removed). New stable entry point **`padb_analyze.py`** (versioned request/result JSON; padbloader parquet / padb-tools parquet / PADB CSV in, jobs + HTML views + index [+ PDF] out) and **`make_analysis_bundle.py`** (the import closure of the analysis layer; REFUSES to build if it reaches extraction). `qa_separation` runs real analyses from a freshly built bundle in another working directory with no extraction code present; `qa_core` `CORE-BOUNDARY` now allows zero exceptions | **done on this branch** |
| 3a -- one filter file across tools | `FilterSpec` <-> the HTML pages' **lock** JSON (`{dims:{label:[values]}, freq:{lo,hi}, passfail}`), implemented once in `padb_core.lockio` (best-effort apply: unknown / absent values are skipped and reported, never empty the view). The viewer gets **Export filter / Import filter**; a filter exported from the viewer applies on a built HTML page and vice versa -- proven in a browser against an oracle (`qa_filter_interchange`) | **done on this branch** |
| 3b -- filter customisation | *Moved up from "later" (David 2026-11-23): it is part of the filter state.* Per-dataset config for which conditions are exposed as filters: show/hide, order, labels, and "expose anyway" for keys the automatic rules drop (single-valued, >40 values, serial-like -> searchable dropdown). Stable per-key ids with a column for every detected key, so changing the config is instant and saved selections stay valid; hiding a filter also clears its selection. Viewer **Edit filters** panel now; the same config as a job key feeds the HTML builds in stage 4 | **done on this branch** (viewer; HTML builds in stage 4) |
| 4 -- views on shared payloads and a shared limits/filter layer | **Honest scope: this is the biggest stage** -- the six HTML views (~22k lines of `padb_plots`) each re-implement spec handling and recompute verdicts in JS from differently shaped embedded data, so it is done in slices, each with its own oracle and a visible per-view notice for views not yet converted (the cross-view rule: a feature must not silently apply in some views and not others). **4a** (first): static HTML pages apply a **limits file in-page** -- a shared JS limits core (a port of the `padb_limits` semantics, conformance-tested in a browser against the Python), a per-test store, an Import / Export / Clear control on the shared bar, the scatter adapter (a pre-pass over its embedded rows), and an amber "imported but not applied in this view" notice on the others; the SAME limits file works in the viewer, in `limits_file` builds and on a page. **4a is done on this branch (scatter view); 4b done (stat_summary, summary, boxplot, reference; histogram N/A) -- oracle = the build-time override, see `qa_limits_views`**. **4b**: the aggregate views (stat_summary, summary, boxplot, reference, histogram) take the same layer through the per-point limit fields they already embed. **4c**: `padb_core` produces the per-view aggregates (boxplot stats, stat_summary TI, summary min/max, scatter envelope) once from the engine, proven equal to the existing pandas aggregators, and the views render those payloads instead of recomputing; the filter config (3b) becomes a job key the builds honour | **4a, 4b done; 4c filters slice done (2026-11-24): `filters_file` / `filter_config` applied by Scatter + Reference, index note for the rest, `qa_filters_pages` oracle. The engine-payload half is intentionally replaced by a parity oracle (decision 2026-11-24: static pages recompute live from per-point data, so baking aggregates would break live filtering / GF / Group-by); the parity oracle is done (`qa_parity`, 2026-11-24); remaining 4c work = the other views' filters** |
| 5 -- size tiers | Envelope-first pages with raw points on demand; Arrow/columnar binary instead of JSON text; giants auto-routed to the server; build-time visibility by default | **Done 2026-11-24 as `padb_tiers` + `qa_tiers`, scoped by measurement:** envelope-first / raw-on-demand and float32 + deflate packing already exist in v2.5 and keep every measured page <= 20 MB, so the Arrow rewrite was dropped; the real gap was PREDICTION -- build time is linear in rows and in x * conditions (fitted, cross-checked against the real EP5 build), so views predicted over a 300 s budget are not built by default and the viewer is recommended (tier.json, index banner, `padb_analyze` result). |
| 6 -- job schema | A job is one declarative, validated object (extract + analyse + publish); the web app edits the schema, not raw JSON | **Done 2026-11-24 as `padb_jobschema` + `qa_jobschema`, scoped honestly:** every key described once (type / kind / group / constraints), `validate()` with did-you-mean, advisory findings printed by `padb_v2` / `padb_run`, `job_issues` in `padb_analyze`, `/api/job-schema` + `/api/job-validate`, findings shown in the editor's Review. Checked against all 147 real jobs and 44 generator outputs. NOT done: restructuring the file layout (breaks every job for no gain), a generated FORM editor (the schema endpoint is what it would use), a strict mode. |
| 7 -- directory watcher | *Last (David 2026-11-23)*: `padb_watch.py` polls a folder for new parquet files (polling -- reliable on shares), waits until a file is complete (size stable + readable footer), keeps a ledger so nothing is processed twice (restart-safe), and runs `padb_analyze` per file into its own folder with retry/give-up; `--once` for Task Scheduler / padbloader. Needs 5 (giants) and 6 (the request/job schema) to behave well, hence last | |


## Separation: analysis is independent of extraction and scheduling (David, 2026-11-23)

**Requirement:** analysis-object generation and plotting must be separate from padb-tools' extraction and
scheduling, so that padbloader can use the analysis capability directly (padbloader owns extraction,
scheduling and credentials; padb-tools owns analysis).

**Three layers, one-way dependencies** (analysis never imports extraction):

| Layer | Contents today | Rule |
|---|---|---|
| `padb_core` (engine) | parser, FilterSpec, limits view, DuckDB `Dataset` | imports nothing from the other layers |
| analysis | `padb_v2`, `padb_plots`, `padb_refstats`, `padb_pdf_report`, `padb_compress`, `padb_bands`, `padb_limits`, `padb_loader_adapter`, `padb_viewer`, `padb_csv_to_parquet`, `padb_make_bands`, `padb_csv_check` | depends on `padb_core` and data files only |
| extraction / scheduling | `padb_run`, `padb_batch` (PADB-R.exe), `padb_scheduler`, `padb_make_job`, `padb_make_v2_job` (pod-driven), `padb_convert_site`, `padb_config`, `webapp/` | may call analysis; analysis never calls it |

**The contract padbloader (a .NET tool) uses:** *data in, artifacts out* -- no shared process, no Python import
required. Input: a parquet (padbloader's own, or padb-tools' table) plus an optional analysis request (the same
JSON the jobs use: views, FilterSpec, limits file, bands file, publish folder). Output: the plot job(s), the
self-contained HTML views + index, the optional PDF report, and (stage 4+) per-view payloads. Entry points, in
order of how soon: (1) a CLI -- `py padb_loader_adapter.py <parquet> --out <dir>` already does this for a
parquet; v3.0 gives it a stable, documented, versioned request/response; (2) the viewer's local HTTP API
(`/api/scatter?spec=`, `/view?spec=`) for interactive use; (3) importing `padb_core` from Python.

**Coupling (stage 2b, 2026-11-23): none.** The one analysis->extraction import (`padb_v2 -> padb_run`) is gone: the
PADB filename-normalisation rules now live in `padb_names.py` (analysis layer; `padb_run` imports them and re-exports
the names, so every caller still works) and the never-implemented PADB-R runner stub in `padb_v2` was deleted (with no
data source `padb_v2` now says extraction is a separate step). `qa_core` `CORE-BOUNDARY` allows zero exceptions;
`make_analysis_bundle.py` re-checks it at packaging time over the real import closure (lazy imports included).

**The entry point and the bundle (stage 2b):** `padb_analyze.py request.json --result result.json` -- schemas
`padb-analysis-request/1` / `padb-analysis-result/1` (documented in its docstring; malformed requests exit 2 with a message,
progress goes to stderr, stdout is one JSON document). `py make_analysis_bundle.py <dir>` writes the closure (18 files today: the
entry point, `padb_core`, the plot builders, the viewer), a requirements list, a manifest with sha256s and a README.
Open idea: a request flag for "generate the jobs only, do not build" (the adapter CLI already does this).

**Still entangled (job generation):** `padb_make_v2_job.py` generates plot jobs FROM A POD (extraction-side) and
imports `padb_run`/`padb_config`; the analysis-side equivalent is `padb_loader_adapter` (jobs from a parquet).
Separation means the plot job schema (stage 6) is owned by analysis and `padb_make_v2_job` becomes just one
producer of it.

## Stage 1 contents (this branch)

- `padb_core/columns.py` -- parquet column detection (x / value / serial / group / temperature).
- `padb_core/conditions.py` -- `parse_group` (the one canonical `Key: value` parser, identical to the HTML
  loader's `_parse_group_kv`, so the viewer and the plots finally agree on edge cases), `site_of`, `serial_of`,
  and `GroupIndex`: distinct Group strings -> site / serial / typed condition dimensions, parsed **once**.
- `padb_core/filterspec.py` -- `FilterSpec` (window, sites, temps, conditions, pass/fail): validate, JSON
  round-trip, canonical cache key, and `to_sql()` -- the single place the WHERE clause is built.
- `padb_core/limits.py` -- limit overrides as a table: `effective` expressions, the `data`/`override` reason,
  and a `LimitTable` that builds the query-time view (no row UPDATEs; `set_limits([])` is free and exact).
- `padb_core/engine.py` -- `Dataset` over DuckDB: build, `meta()`, `query()` (filtered, min/max-envelope
  decimated), `counts()`, `band_counts()`, `spec_segments()`, `set_limits()`.
- `qa_core.py` -- parity against the v2 code as the oracle (parsers, WHERE builder, whole-query results, limit
  verdicts), plus mutation teeth.

Known intentional differences from v2 (all documented in the tests):
- The v2 viewer's own group parser keeps a `Key:` with an empty value (`""`); the HTML loader's parser drops
  it. The core follows the **HTML loader** (that is what the verdicts and filters in the shipped views use) and
  `qa_core` pins both behaviours so stage 2 unifies the viewer deliberately, not by accident.
