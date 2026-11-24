# Case Study: The Run That Was Too Fast — `Spectral_Data_For_FS_run_job.json`

*A real dialog, captured 2026-08-13 as training material. Lightly condensed — tool calls and output summarized, but the questions, findings, and correction are the real ones.*

---

**David:** I just noticed when I used the webtool to run Spectral_Data_For_FS_run_job.json that it didn't take very long. I'm guessing that you saw that the csv file was fresh so didn't do the extraction. Correct?

**Claude:** No — I checked the actual run log, and there's no "skip extraction if the CSV looks fresh" behavior anywhere in this tool (confirmed by grepping the code — no such logic exists). The log shows a real extraction happened:
```
Running PADB-R.exe ...
PADB completed in 4.5s, return code: 0
Collected 9 file(s) from R-Plots/
```
PADB-R.exe itself just returned in 4.5 seconds — genuinely fast, not skipped. That's plausible given the job's date window is only 12 weeks for 3 Scatter analytics. So: real extraction, just a fast one — not a caching shortcut.

**David:** No, it takes me much longer using the PADB GUI. Some of the csv files are very large.

**Claude:** *(Tries to dig into it with a couple of tool calls — checking file sizes and timestamps directly. Both get declined at first.)*

**David:** I found the issue. will try again with the web tool. Could be good for you to see if there is a pre-existing csv, read its size and estimate extraction / analysis time

**Claude:** *(Checks the actual collected CSVs' sizes and modification times, and compares them against the source copies in R-Plots.)*

Good catch — and the evidence backs it up exactly. The run log's own filename says it started at **11:22:00**. The files it "collected" are dated **10:22, 10:29, and 10:43** — 39 to 60 minutes *before* the run even started. `_collect_padb_outputs()` matches files by filename stem, not timestamp, so it silently re-grabbed leftover files from an earlier session instead of anything this run actually produced. PADB-R.exe genuinely returned in 4.5s, but that's consistent with it not writing fresh output at all, not with a real multi-hundred-MB DB pull.

Sizes and timing from that earlier (real) session, for reference:

| File | Size | Finish time |
|---|---|---|
| `Non_Harmonics_Clock_leakage_room.csv` | 175 MB | 10:22 |
| `non_harmonics_close_in_spurs_room.csv` | 1.24 GB | 10:29 (+7 min) |
| `Harmonics_and_Subharmonics_room.csv` | 204 MB | 10:43 (+14 min) |

Worth noting: extraction time doesn't track CSV byte size — the 1.24 GB file finished faster than the 204 MB one. Oracle query/join complexity drives the wall-clock time here, not the final file size. Rough order of magnitude: ~5–15 minutes per large spur analytic for this pod's date window. Separately, plot-*build* time (not extraction) is its own concern for a file this large — worth running `padb_csv_check.py` on it before committing to a full V2 build.

**David:** OK, can you track how long the last run took?

**Claude:** *(Starts checking for a new run log, and whether PADB-R.exe is currently running or R-Plots has fresh activity.)*

**David:** remind me to look into this again after the training is done. code is stable right now. Dont want to make any changes beofe the training session unless I find a big bug.

**Claude:** Understood — no changes, no more digging right now. Saved a memory note to revisit after your training session: whether `_collect_padb_outputs()`'s stem-based matching in R-Plots ever silently serves stale data as if it were a fresh extraction, versus just harmlessly re-collecting genuinely-current files.

**Update (2026-08-20):** revisited, and the answer is yes — it genuinely could, and did. A stale R-Plots leftover (typically from a manual GUI session) sharing a stem with an analytic that has real fresh `-dir` output could silently clobber that fresh output with the old file. Confirmed via a real incident on `UHP-IddVsVgg` (a stale leftover overwrote every subsequent automated re-extraction's genuinely fresh, larger CSV, run after run) and found in 11 of 16 recent run logs across several jobs when audited afterward. Fixed the same day — see `CLAUDE.md` → **`_collect_padb_outputs()` clears stale files before copying fresh ones** for the fix and its verification.

---

## Second incident: "why are there two box plots at one frequency?"

*This is where the pod's name comes from — `Spectral_Data_For_FS` was generated for a requestor referred to as FS. Reviewing the boxplot results with him is what first surfaced this.*

**David:** for the box plot views, each frequency looks like it has two samples per serial number. The real issue is that we are testing at very small offsets either side of a frequency band break or YTO band break. You've chosen to stack to boxes on waht looks like the same x-axis frequency. Any chance you could offset those boxes very slightly so its obvious they are close in frequencies. Hover data is useful here too. For now just an analysis and review.

**Claude:** Traced the actual root cause in `padb_plots.py` — it's not really "two samples stacked at one frequency," it's a **label-precision collision** on a categorical axis.

The boxplot's x-axis is a Plotly **category** axis, and each box's position is keyed by `_freq_label(f)` — a *rounded display string*, not the real number:
```python
def _freq_label(f: float) -> str:
    if x_unit == "MHz" and f >= 1000:
        return f"{f / 1000:.3g} GHz"   # only ~1 decimal place once in GHz
    return f"{f:.4g} {x_unit}"
```
Above 1000 MHz — exactly where YTO band edges live — `.3g` collapses e.g. both `8199.95` and `8200.05` MHz to the identical string `"8.2 GHz"`. Since the axis is categorical and keyed by that string, both boxes land on the same slot and render fully overlapping. Underneath, `_aggregate_box_data_by_temp()` groups by the exact raw frequency value with no merging at all — the two box statistics are computed correctly and independently. The stacking is purely a rendering collision from the label, not a data problem.

What that means for a fix: tightening `_freq_label()`'s precision so two genuinely distinct frequencies never format to the same string should resolve the visual stacking on its own — once the labels are distinct, Plotly's categorical axis naturally gives each its own slot. No manual box-offset hack should be needed; the stacking is a label bug, not an intentional design choice. Richer hover data (spelling out the exact frequency per box, not just the rounded tick) is still worth doing either way. No changes made — this is analysis only, per your ask.

**David:** save this to memory to explore as future embellishment.

**Claude:** Saved — alongside the other deferred, pre-training items.

---

## Why this is a good training example

- **A clean log is not the same as a verified claim.** "Return code 0" and a plausible elapsed time both looked like evidence of success — neither actually proved that fresh work happened. The first answer given was wrong, and it was backed by a real log.
- **Domain experience caught what the log missed.** The correction didn't come from re-reading the code more carefully — it came from someone who actually knows how long this pod takes in the GUI, noticing the numbers didn't add up.
- **The fix for "I'm not sure, but I doubt it" is a concrete check, not a stronger opinion.** Comparing the run's own timestamp against the collected files' modification times turned a disagreement into a provable fact in one step.
- **Not every confirmed finding needs an immediate fix.** The real open question — can a run silently serve stale data as success — was worth writing down precisely and deferring, not rushing to patch under time pressure before training. (It was revisited after training and confirmed real — see the update above.)
- **A display bug and a data bug can look identical from the outside.** "Two box plots at one frequency" sounded like a data problem (duplicate measurements, or a real aggregation collapsing two points together). It was neither — the underlying statistics were computed correctly and independently; only the on-screen label rounding made two distinct frequencies collide onto the same spot. Worth checking which layer a symptom actually lives in before assuming where the fix belongs.
