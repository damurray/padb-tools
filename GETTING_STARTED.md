# Getting Started with padb-tools

You're looking at this because you've been handed a `.pod` file and need interactive HTML plots out the other end — or because you're picking up maintenance on the tool itself. This doc is the map; it doesn't replace the other docs, it tells you which one to open next.

---

## What this repo actually does, in one paragraph

`padb-tools` drives PADB-R.exe (Keysight's RF characterisation database tool) headlessly: it runs your `.pod` file's analytics, collects the CSV output, and turns it into self-contained interactive HTML plots (Plotly embedded inline — no server, works straight off a network share). You configure a run with a `job.json` file; the tool does the rest.

---

## Doc map — which file answers which question

| Question | Read |
|---|---|
| "What is this repo, at a glance?" | `README.md` |
| "I have a new pod, walk me through it start to finish" | `Quick_Start.md` |
| "Full reference — every job.json key, every plot type, every parameter" | `PADB_Tools_Guide.md` |
| "I'm writing/configuring the *pod itself* — what does PADB need to produce for the tool to work?" | `PADB_Analytic_Requirements.md` |
| "I generated new plots — how do I manually verify they're correct before publishing?" | `QA_Checklist.md` |
| "I'm using Claude Code and want the tool's architecture/gotchas loaded automatically" | `CLAUDE.md` (auto-loaded) or type `/padb-tools` |
| "I'm new, where do I even start" | You're reading it |

Read in this order for a first pod: **this doc → `Quick_Start.md` → `PADB_Analytic_Requirements.md`** (if you're also configuring the pod, not just consuming an existing one) **→ run it → `QA_Checklist.md`** before publishing.

---

## Prerequisites

```
py -m pip install pandas numpy matplotlib scipy plotly
```

PADB-R.NET installed at `C:\Program Files\KEYSIGHT\PADB-R.NET\PADB-R.exe` (override with `padb_exe` in job.json if yours differs). **PADB-R.exe is a WinForms app** — it needs an actual Windows desktop session; it will not run over SSH or as a service.

Job configs and results live outside this repo, in a `Data\` folder next to your pod files (job configs are per-user/per-environment, not version-controlled — see the path caveat below).

---

## Two pipelines — which one do you want?

There are two ways to run this tool, and picking the wrong one is the most common first-timer confusion:

| | V1 (`padb_run.py` + `padb_plots.py`) | V2 (`padb_run.py` extract + `padb_v2.py` plot) |
|---|---|---|
| **Use when** | You want the older per-analytic plot types (`accuracy_vs_freq`, `distribution`, `de_summary`, etc.), one job.json drives everything | You want the full modern interactive suite (`scatter`, `stat_summary`, `boxplot`, `distribution`, `env_coverage`, `summary`) from one Type=80 Scatter CSV |
| **Job files** | One `job.json` per analysis | Two files: `*_run_job.json` (extract) + `*_v2_job.json` (plot) |
| **Re-plot without re-extracting** | `padb_run.py job.json --plots-only` | `py padb_v2.py the_v2_job.json` alone (Step 2 only) |
| **Examples in this repo** | `amplitude_job.json`, `harmonics_job.json` | `closein_env_v2_job.json`, `maxpower3_leveled_linear_job.json` |

**If you're starting a new pod today, use V2.** It's the actively-developed pipeline with the richer interactive feature set (serial filters, global exclusion, NP-TI, etc.) — see the plot type tables in `PADB_Tools_Guide.md`.

---

## Worked example: pod "families" we've actually done

The tool has now been proven on four genuinely different measurement types. If you're onboarding a new one, expect friction points similar to whichever family yours resembles:

### Family 1 — dBc spur measurements (Close-In, Clock Leakage, Line-Related, Harmonics/Sub-Harmonics)
- Two-sided or single-sided spec expressed as dBc, spec limits **are** present in the pod's CSV output
- All six V2 views work out of the box; `spec_direction` never needed (auto-detection from `Lower Limit`/`Upper Limit` columns works fine)
- Main friction: `TestRun_RunStatus` filter, `csv` vs `csv_file` matching, pods where SummaryPlot doesn't write a CSV — see **Common gotchas** in `CLAUDE.md`

### Family 2 — dBm power measurements (MaxPower2 → MaxPower3)
- One-sided (guaranteed-minimum) spec, expressed in absolute dBm — a structurally different measurement than dBc spurs
- MaxPower2 (first attempt) had unresolved issues: empty Environmental plot (pod extracted `Room` only), no spec limits, n below the NP-TI threshold
- MaxPower3 (redo) fixed the Environmental plot by setting `Environment_TestStep={All}` in the pod, but **still has no spec limits configured** (`Limits_YLimit=None` on every analytic) — worked around per-job with the `"spec_direction": "lo"` key rather than a pod fix, since the measurement is known to be lower-spec-only regardless of what the pod says
- **New pod checklist item this surfaced:** if your new pod has no spec limits and you know the measurement is one-sided, don't wait for a pod fix — set `spec_direction` explicitly in the plot job JSON. See `maxpower3_leveled_linear_job.json` for the pattern, and `PADB_Analytic_Requirements.md` → "One-sided measurements with no pod spec limits" for the write-up.

### Family 3 — VSWR / Return Loss ratio measurements (VSWR2.pod)
- Room-only data (this specific test suite is never run across temperature) — `distribution`/`env_coverage`/`summary` are structurally meaningless here; use `padb_v2.py`'s auto view-selection (omit `"views"`) rather than hand-picking
- No spec limits configured at all, same `spec_direction` workaround as MaxPower3
- A compound grouping key (`OA: Calset: N, State M: X dB`) with 40–56 distinct values still renders as a full (if unwieldy) filter panel — there is **no 20-value cap**, contrary to older versions of this doc; see `PADB_Analytic_Requirements.md` §3
- Genuinely frequency-varying spec (PADB `Limits_YLimit=Line`) for VSWR needs the `scatter` view's per-frequency mask rendering, not a flat line — auto-detected, no job.json key needed

### Family 4 — Phase noise vs. frequency offset (Absolute Phase Noise EP6 Spec Setting DE.pod)
- **X-axis is Frequency Offset in Hz, not carrier frequency in MHz** — carrier frequency becomes a *condition* (a Group-string dimension with 2 discrete values), not the swept axis. Set `"x_label"`/`"x_unit"` in job.json (see `PADB_Tools_Guide.md`) or every axis title, hover tooltip, and table header will wrongly claim "MHz."
- Real spec limits present in the CSV, but genuinely varies by offset (a phase-noise mask, tight near the carrier... no, *loose* near the carrier, tightening at larger offsets) — same auto-detected mask rendering as Family 3's VSWR.
- Surfaced a real parsing bug: PADB pads short grouping values to a fixed column width, producing 2+ spaces after the key's colon for some values but not others (`"Frequency (MHz):  10"` vs `"...: 100"`) — this silently dropped the padded key from the Group-string parse entirely. Fixed in `_parse_group_kv()`; if a grouping dimension you expect to see just isn't there, this class of bug is worth re-checking for.

If your new pod is a fifth measurement family, read all of the above before assuming the tool generalizes cleanly — check whether your pod has spec limits (and whether they vary by frequency), whether it extracts all temperatures, whether the x-axis is really carrier frequency in MHz, and whether one measurement value per analytic holds (see the Section 7 checklist in `PADB_Analytic_Requirements.md`).

---

## The one path-portability caveat

`CLAUDE.md` and the guides reference `C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\` for job configs and `C:\Users\damurray\AppData\Local\Python\bin\python3.14.exe` for the Python executable. **These are one user's actual paths, not universal ones.** If you're a different user cloning this repo: your job configs, PADB output, and Python install will be under your own profile — adjust every absolute path you see in examples to match your own environment before running anything.

---

## When something breaks

1. Check `padb_run_YYYYMMDD_HHMMSS.log` in the results directory first — full stdout/stderr, written on every run.
2. No CSV produced but exit code 0? → `TestRun_RunStatus` filter (see `CLAUDE.md` → Common gotchas).
3. CSV produced but plot won't pick it up? → switch from `csv` (substring match) to `csv_file` (exact filename).
4. Plot renders but a control does nothing? → check `QA_Checklist.md` for the specific plot type; several known-by-design limitations are listed there (no serial filter on `de_summary`/`summary`, NP-TI nulled when a filter is active, etc.) — not everything odd-looking is a bug.
5. An interactive control (e.g. a filter, a stats table) doesn't seem to update? → **hard-reload the browser tab first** (Ctrl+Shift+R) before assuming it's a code bug — stale cached JS from a previous version of the same file is the most common cause. If it still doesn't work after a reload, `updateStatPanel` (and the analogous `de_summary` table function) are now wrapped in try/catch and will show the actual JS error in the panel instead of doing nothing, which is the fastest way to find a real bug if one exists.
6. `[WARN] Publish failed` printed, but you're not sure whether the copy actually happened? → check the destination directly. A `print()` with any non-ASCII character (arrows, em-dashes, etc.) throws `UnicodeEncodeError` on this Windows console's codepage — and since that would happen *after* the actual file copy, you'd see a false "failed" message. Fixed instances of this are ASCII-only now, but if you add a new status message with a fancy character, this is what breaks.

---

## If you're using Claude Code

Type `/padb-tools` for an on-demand summary of the architecture, gotchas, and new-pod checklist without having to read all six docs — it's scoped to this repo (`.claude/commands/padb-tools.md`), so it travels with a clone. `CLAUDE.md` loads automatically at session start when your working directory is inside this repo; `/padb-tools` is for invoking the same knowledge explicitly, from anywhere.
