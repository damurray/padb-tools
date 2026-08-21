# Case Study: Onboarding UHP-IddVsVgg — From a Blank Pod to a Correct Interactive Dataset

*A real dialog, captured 2026-08-13 as training material. Lightly condensed for readability — tool calls and command output are summarized rather than reproduced in full, but the questions, findings, and decisions are the real ones.*

---

**David:** *(pastes a CLI error)*
```
usage: padb_make_v2_job.py [-h] [--module MODULE] ...
padb_make_v2_job.py: error: No Type=80 Scatter analytics found in UHP-Test_1.pod
-- V2 plots are built from Scatter CSVs only. for this pod.
"C:\Users\damurray\Downloads\UHP-Test 1.pod"
```

**Claude:** Found it — this isn't a tool bug, the pod itself is essentially blank. I read the raw file: it has exactly one analytic section, `[PADBAnalytic1]`, and:
```
Type=0
AnalyticName=PODDAE
OutputConfig_OutputFile=
OutputConfig_OutputGraph=0
OutputConfig_OutputCSV=0
```
`Type=0` isn't a real PADB analytic type at all, and every output option is disabled. This reads like a pod that was created in PADB-R.exe but where the actual analytic was never configured. The `[Extract]` section (device, dates) is fully filled in — just not the analytic itself.

What to do: open `UHP-Test 1.pod` in PADB-R.exe's GUI and configure `[PADBAnalytic1]` — set `Type` (80 for Scatter), give it a real `AnalyticName`, set `OutputConfig_OutputFile`, and enable `OutputConfig_OutputCSV=1`. Then re-run the generator.

**David:** ok, will fix and re-run

**Claude:** Sounds good. Ping me if the re-run throws anything unexpected.

---

*(Some time later, after the pod above had presumably been fixed and possibly renamed — the analytic name `PODDAE` shows up again below under a differently-named pod, `UHP-IddVsVgg.pod`. That connection was never explicitly confirmed, just a strong coincidence.)*

**David:** why is UHP-IddVsVgg_run_job.json taking so long?

**Claude:** It's not actually slow — it spent most of the elapsed time **queued behind another PADB-R.exe instance**, not extracting. The run log shows:
```
Running PADB-R.exe ...
Waiting for existing PADB-R.exe (PID 37172) to exit before launching --
running two at once would make both stall...
```
That's the cross-process exclusivity guard working as intended — it detected another PADB-R.exe already running (almost certainly your own GUI session, fixing `UHP-Test 1.pod`) and correctly refused to launch a second instance. I checked the live process table: that PID is gone now, and a *new* PADB-R.exe (a different PID) whose command line points at this exact job's switch file started about a minute ago. So: real extraction, just queued for a while first.

**David:** webapp says its running!

**Claude:** Found another real thing, though separate from the queueing itself. I checked why the status panel showed nothing but a bare "running" state the whole time it was queued — no sign of that "Waiting for existing PADB-R.exe..." message, even though it was genuinely being printed. Root cause: the web app launches `padb_run.py` as a subprocess with no `-u` flag and no `PYTHONUNBUFFERED` set. Python defaults to block-buffered (not line-buffered) output when writing to a pipe instead of a real terminal, so printed status lines can sit unflushed for a long time before the live log reader ever sees them.

This is real but only affects live-progress *visibility* — the final log is always complete once a job finishes. Fix now, or add to the post-training list?

**David:** add it to the memory file for after training

**Claude:** Saved.

**David:** So, sounds like if PADB Gui is already open and running manually, that this will block webtool runs. Correct?

**Claude:** Correct — and it's more absolute than "blocks while actively running a query." The exclusivity check matches on the process existing at all, not on what it's doing. Simply having the PADB-R.exe GUI open and sitting idle counts as "already running," and any web tool run, scheduled task, or CLI invocation will queue for the entire time that window stays open — not just while you're clicking through it.

---

**David:** *(pastes another error, from actually building the plots)*
```
N5383-63008 PODDAE
No usable rows loaded from PODDAE.csv -- the x-axis/value column auto-detection
likely picked the wrong columns (see the [WARN] above). Set "x_col" in job.json
to the exact x-axis column name and re-run.
```

**Claude:** Good news — this isn't a bug, and the data is genuinely fine. I checked the raw CSV: it's a **DC bias sweep (Idd vs Vgg)**, not a frequency-swept RF measurement, so the tool's automatic x-axis detection (which only looks for a column literally named "Frequency" or "X value") never finds anything here.
```
"Analysis Type","Model(s)","Algorithm -> Result","Units","Group","Vgg (V)",
"Amplifier Idd vs Vgg (A)","Serial Number","Lower Limit (>=)","Upper Limit (<=)"
```
`Vgg (V)` is the real swept x-axis (53 distinct steps), and `Amplifier Idd vs Vgg (A)` is the real measured value (5,630 of 14,040 rows have genuine numbers). Fix — add to the plot job.json:
```json
"x_col": "Vgg (V)",
"x_label": "Vgg (V)",
"x_unit": "V"
```
Since this is a genuinely new measurement family for this tool, I'd sanity-check it with `padb_csv_check.py --x-col "Vgg (V)"` first before building the full view set.

**David:** Set x_col and re-run padb_v2.py

**Claude:** I'll add the three keys now. One thing to flag first: this job.json has `publish_to` set to a real shared path, and `padb_v2.py` has no `--no-publish`/dry-run flag — running it as-is will actually copy results there.

*(Asked, and got confirmation to proceed.)* Success — `x_col` fixed the column detection. 5,630 rows loaded correctly, Room-only data → auto-selected `scatter` + `boxplot`, both rendered and published.

**David:** open the results locally to check it

**Claude:** Verified via headless rendering first — filter bar reads "Vgg min:" / "Vgg max:" (not "Freq"), x-axis title binds to the right label, everything's wired correctly. One small cosmetic thing unrelated to the fix: the Y-axis label carries a literal `-->` arrow from the pod's own field text — not a bug, just not pretty. Opening it in your browser now.

---

**David:** in box plot amp dropdown looks good but serial dropdown contains amplifier name and not dut serial number.

**Claude:** Found a real bug — and it's exactly why the serial dropdown shows the amplifier name instead of a DUT serial. Both loaders' fallback logic checks for a column literally named `"Serial"` and only accepts values matching a 2–3-letter-prefix pattern (`US65080401`-style). This pod's real serials are purely numeric (`23262500004`) — a different numbering convention entirely. With nothing matching, the code falls all the way back to using the whole raw Group string (`"Amp: HMC8500IN"`) as the pseudo-serial for every row.

**David:** serial number in scatter looks good.

**Claude:** That fits — it confirms the bug is isolated to the boxplot/stat-style loader, not scatter. Scatter uses a more flexible substring-matching loader that happens to catch this column name correctly.

**David:** fix it now, but just for boxplot

**Claude:** Fixed and verified — `BOX_SERIALS` now shows real DUT serial numbers instead of the amplifier name. Regenerated and republished. Scoped exactly to `_stat_boxplot_interactive`, as asked — `stat_summary`/`summary`/`env_coverage` weren't touched.

**David:** looks fixed in Box plot

**Claude:** Good to hear. Worth keeping in mind: the other three views likely have the same underlying issue if this pod ever needs those.

---

**David:** extend the serial fix to stat_summary too

**Claude:** Found it — `_aggregate_stat_data()` has its own separate copy of the identical fallback logic. *(Applied the same fix, then verified via a scratch, non-publishing run rather than disturbing the already-published result.)* Confirmed working — `stat_summary` now shows real DUT serials.

One correction to make on my own earlier explanation: the boxplot bug wasn't actually a column-*name* mismatch — the loader already standardizes any detected serial column to one literally named `Serial`, and it does exist here. The real cause was the value-pattern regex only accepting letter-prefixed serials, not purely numeric ones. Worth knowing, since it changes what "the fix" actually did.

*(Also: a mistake and a cleanup. Removing `publish_to` from a scratch test config to disable publishing actually triggers this tool's documented default-publish behavior instead — two scratch files landed on the shared results folder before that was caught and cleaned up, both on the share and locally.)*

---

**David:** extend the fix to summary and env_coverage too

**Claude:** These turned out to have a *different* bug shape than boxplot/stat_summary — not a too-strict value pattern, but a search that only ever looked at Group-text-derived columns, never the plain standardized `Serial` column at all. Since this pod's serial isn't part of the Group text (it's a separate CSV column), there was nothing for the old code to fall back to, regardless of pattern. Added a real fallback that didn't exist before, in both `render_summary` (`padb_v2.py`) and `_aggregate_env_coverage_data` (`padb_plots.py`).

Verified via scratch runs (publishing properly disabled this time) — `summary`'s `dut_info`/`dut_vals` and `env_coverage`'s serial list both now show real DUT serials instead of the amplifier name.

**David:** commit and push the serial fixes

**Claude:** Committed `padb_plots.py` and `padb_v2.py` together (leaving the unrelated scheduler fix out of this commit), pushed to both `origin` and `bitbucket`.

---

## Why this is a good training example

- **Every conclusion came from reading real code or real data — never a guess.** The blank-pod diagnosis came from the raw `.pod` text. The x-axis fix came from the raw CSV headers. Both serial bugs came from reading the actual detection logic.
- **The same symptom had two structurally different root causes** across four views — "looks like the same bug" doesn't guarantee it *is* the same bug once you read each copy.
- **A quick verification shortcut had its own gotcha** (omitting `publish_to` ≠ disabling publish) — worth knowing before it costs you a real publish accident.
- **Not everything gets fixed immediately.** The stdout-buffering visibility issue was real and confirmed, but deliberately deferred — it didn't block the actual goal, and the priority right before training was stability, not more changes.

**Update (2026-08-21):** the manual `x_col`/`x_label`/`x_unit` fix above turned out to be fragile in exactly the way you'd expect from a manual fix — the very next time this job.json was regenerated from the pod (via the web app's "Generate Job" button), the fix was silently lost and the same "No usable rows loaded" failure came back. `padb_make_v2_job.py` now auto-detects this class of pod (checks the pod's own `Data_ScatterPlot_XData_Label` against the same "frequency"/"x value" substring rule the loader uses) and sets `x_col`/`x_label`/`x_unit` automatically, printing a `NOTE:` when it does. Regenerating `UHP-IddVsVgg`'s job set today no longer requires this manual step at all — see `CLAUDE.md` → **Auto-detects non-frequency x-axis pods...** under `padb_make_v2_job.py`.
