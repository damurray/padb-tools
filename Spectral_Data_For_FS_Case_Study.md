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

---

## Why this is a good training example

- **A clean log is not the same as a verified claim.** "Return code 0" and a plausible elapsed time both looked like evidence of success — neither actually proved that fresh work happened. The first answer given was wrong, and it was backed by a real log.
- **Domain experience caught what the log missed.** The correction didn't come from re-reading the code more carefully — it came from someone who actually knows how long this pod takes in the GUI, noticing the numbers didn't add up.
- **The fix for "I'm not sure, but I doubt it" is a concrete check, not a stronger opinion.** Comparing the run's own timestamp against the collected files' modification times turned a disagreement into a provable fact in one step.
- **Not every confirmed finding needs an immediate fix.** The real open question — can a run silently serve stale data as success — was worth writing down precisely and deferring, not rushing to patch under time pressure before training.
