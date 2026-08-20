# PADB Cross-Site Comparison — One-Page Steps

Compares two sites' data (e.g. an established site vs. a newly-stood-up site's first production units) without hand-merging CSVs. A corner case — mainly useful once, the first time a new site comes online — built on top of the normal Interactive (V2) pipeline, not a separate tool.

## 1. Have two already-extracted CSVs

Each site needs its own scatter CSV already produced by the normal extract step — same pod (or that pod's site-converted counterpart, see `padb_convert_site.py` in `PADB_Tools_Guide.md`) run at each site. If you haven't extracted one or both yet, do that first — see `Interactive_Mode_Cheatsheet.md` steps 1–2.

## 2. Fastest path — the webapp

```
py C:\apps\padb\tools\webapp\padb_web.py
```
- Open **"3. Compare two datasets"** — collapsed by default, click the header to expand.
- Pick a CSV for **Site A** and **Site B** (auto-discovered from every already-extracted CSV on disk), give each a short name (e.g. `SR` / `AMC2`), and pick the **Primary site** — the reference population for the boxplot check.
- Click **Check compatibility** first. It always warns (never blocks) on soft gaps — missing temperatures/ports, non-overlapping frequency ranges. It **blocks** only on a genuine measurement-unit mismatch (e.g. dBc vs. dBm) — check **Override and proceed anyway** if you're sure that's what you actually want.
- Click **Create & Run**. Builds the job.json and queues it — no hand-written file needed. New jobs default to **local-only** (no publish) on purpose.

## 3. Or write the job.json by hand

```json
{
  "description": "SG6311A <Analysis> — SR vs AMC2",
  "title_prefix": "SG6311A <Analysis> Compare",
  "compare_csv": {
    "SR": "C:\\path\\to\\sr_run_results\\padb\\Scatter.csv",
    "AMC2": "C:\\path\\to\\amc2_run_results\\padb\\Scatter.csv"
  },
  "primary_site": "SR",
  "results_dir": "your_analysis_compare_results",
  "publish_to": ""
}
```
- `compare_csv` replaces `csv_path` entirely — 2+ site names required. Every backslash doubled, same as any other job.json.
- `primary_site` defaults to the first key if omitted. It only matters for the boxplot check (below) — no effect on any other view.
- Omit `"views"` for the normal auto-detection (Room-only → `scatter`+`boxplot`; multi-temp → all six) — same rule as any other V2 job.
- Set `publish_to` explicitly (even to a real path) — an ad-hoc comparison shouldn't silently inherit the default publish location.

## 4. Run it

```
py C:\apps\padb\tools\padb_v2.py "C:\path\to\your_analysis_compare_job.json"
```
No separate extraction step — both CSVs already exist. Re-run this alone any time you tweak the job.json; nothing here touches PADB-R.exe.

## 5. Review — what's different from a normal V2 page

Open `results_dir\index.html` → the boxplot view. Two things only appear here, not on a single-site page:
- **Coverage-gap banner** (always visible, above the plot) — lists what one site has that the other doesn't (e.g. missing temperatures/ports). Numeric formatting differences between sites (e.g. `-100.00` vs `-100`) never falsely show up as a gap.
- **"Site Population Check" button** — tests each non-primary-site DUT's value at each frequency/temperature against the k×IQR fence built from the primary site's own population there (same fence/k×IQR control the page's own outlier detection already uses). Three tables, in order:
  1. **Per-DUT summary** — checked/outside counts, high/low split, and a **suggested triage tag**: station/systemic issue (shared with other DUTs) beats bad DUT (isolated, toward-failing) beats isolated-worth-a-look beats below-population/benign beats ambiguous. A suggestion, not a verdict.
  2. **Frequency clusters** — every point where 2+ distinct DUTs are simultaneously outside. Several independent DUTs failing at the identical spot points at the station/fixture, not any one DUT.
  3. **Per-point detail** — every checked point, for drill-down.
- Narrowing the frequency range (drag-zoom or the number inputs) scopes all three tables to that window — a note in the summary line says so.

See `Interactive_Plots_User_Guide.md` → **Cross-Site Comparison** for the plain-language version, and `PADB_Tools_Guide.md` → **Cross-Site Comparison (`compare_csv`)** for the full mechanism.

## 6. Known gaps

- Only `boxplot` has the Site Population Check. The other five views render the merged data fine (Site is just a filterable condition dimension to them too) but have no dedicated comparison feature yet.
- The check compares only exact-matching frequencies between the two sites' sweeps — a frequency present in only one site reports `n/a`, not a near-match guess.
