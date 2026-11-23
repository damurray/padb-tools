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
| 2 -- viewer on the core | `padb_viewer.py` drops its private copies (parser, `_where`, limits UPDATE) and calls `padb_core`; `/api/*` takes a FilterSpec; filter state saved/shared as one JSON | next |
| 3 -- one filter state across views | Lock bar / Global Filter / segment bands / persistence collapse into FilterSpec (+ excluded-unit list); export/import is that file | |
| 4 -- precomputed view payloads | `padb_core` produces per-view aggregates (boxplot stats, stat_summary TI, summary min/max, scatter envelope) once, from the engine; HTML views render payloads and no longer recompute verdicts in JS; static pages apply a limits table in-page for free | |
| 5 -- size tiers | Envelope-first pages with raw points on demand; Arrow/columnar binary instead of JSON text; giants auto-routed to the server; build-time visibility by default | |
| 6 -- job schema | A job is one declarative, validated object (extract + analyse + publish); the web app edits the schema, not raw JSON | |

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
