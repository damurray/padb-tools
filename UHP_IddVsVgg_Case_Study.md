# Case Study: Onboarding UHP-IddVsVgg — From a Blank Pod to a Correct Interactive Dataset

*A narrative log of the real debugging session that turned a never-configured pod into a working, correctly-attributed V2 dataset — captured 2026-08-13 as training material.*

This is the story of one pod, `UHP-IddVsVgg.pod` (device `N5383-63008`), going from completely broken to fully working through the web tool. It's a good teaching example because every problem hit along the way was a *different kind* of bug — a blank pod, a process-scheduling race, a UI observability gap, a wrong-axis assumption, and a data-attribution bug that turned out to affect four separate views in four subtly different ways. None of it was solved by guessing — every step traced the actual code or the actual data before concluding anything.

---

## 1. The pod that wasn't configured yet

It started with a different-looking error entirely: generating a V2 job set for a pod called `UHP-Test 1.pod` failed with:

```
No Type=80 Scatter analytics found in UHP-Test_1.pod -- V2 plots are built from Scatter CSVs only.
```

Rather than guess, the pod file itself was read directly. It had exactly one analytic section, and it told the whole story:

```
[PADBAnalytic1]
Type=0
AnalyticName=PODDAE
OutputConfig_OutputFile=
OutputConfig_OutputGraph=0
OutputConfig_OutputCSV=0
```

`Type=0` isn't a real PADB analytic type at all. `AnalyticName=PODDAE` looked like a leftover placeholder, and every output option was disabled. This wasn't a tool bug — the pod's `[Extract]` section was fully filled in (device, dates, everything), but the actual analytic had never been configured: no type picked, no name given, no CSV output turned on. The fix wasn't code — it was going into PADB-R.exe's GUI and actually setting up the analytic (Type=80 Scatter, a real name, `OutputConfig_OutputCSV=1`).

*(This pod's only analytic was already named `PODDAE` — the same name that shows up throughout the rest of this story under `UHP-IddVsVgg.pod`. It's a reasonable guess that this is the same pod, fixed and renamed to something more descriptive once configured — though that connection was never explicitly confirmed, just inferred from the matching name.)*

## 2. "Why is this taking so long?" — a race, not a hang

Later, running `UHP-IddVsVgg_run_job.json` through the web tool, the question came up: it wasn't triggering visibly, and seemed slow. The run log held the answer immediately:

```
Running PADB-R.exe ...
Waiting for existing PADB-R.exe (PID 37172) to exit before launching -- running two at once would make both stall...
```

This is the cross-process exclusivity guard working exactly as designed (`padb_batch.py`'s `wait_for_exclusive_padb_r()`) — it had detected another PADB-R.exe process already running (very likely the same PADB-R.exe GUI session being used, at that very moment, to fix `UHP-Test 1.pod` from Part 1) and correctly refused to launch a second instance alongside it. A quick check of the live process table confirmed a *new* PADB-R.exe (a different PID) had started moments earlier, running for real — the job wasn't stuck, it had just been queued behind a real, ongoing GUI session the whole time.

**A side discovery from watching this queue happen:** the web app's live status panel showed nothing but a bare "running" state the entire time it was queued — no sign of the "Waiting for existing PADB-R.exe..." message, even though that line genuinely was being printed the whole time. Tracing it down: the web app launches `padb_run.py` as a subprocess with no `-u` flag and no `PYTHONUNBUFFERED` set, and Python defaults to block-buffered (not line-buffered) output when writing to a pipe instead of a real terminal — so informative lines can sit unflushed for a long time before the live log reader ever sees them. Confirmed as real, but scoped as a live-progress *visibility* issue only (final log content is always complete once a job finishes) — deferred to a post-training fix list rather than patched immediately, since training was imminent and this doesn't affect plot correctness.

## 3. "No usable rows loaded" — the pod isn't a frequency sweep at all

Once extraction succeeded, building the V2 plots hit a second, unrelated error:

```
No usable rows loaded from PODDAE.csv -- the x-axis/value column auto-detection
likely picked the wrong columns. Set "x_col" in job.json to the exact x-axis
column name and re-run.
```

Reading the raw CSV columns explained it immediately:

```
"Analysis Type","Model(s)","Algorithm -> Result","Units","Group",
"Vgg (V)","Amplifier Idd vs Vgg (A)","Serial Number","Lower Limit (>=)","Upper Limit (<=)"
```

This is a **DC bias sweep** — drain current (Idd) vs. gate voltage (Vgg) — not an RF frequency sweep. The tool's automatic x-axis detection only ever looks for a column named "Frequency" or "X value"; neither exists here, so it had nothing to find. The data itself was completely fine — `Vgg (V)` was fully populated across 53 real sweep steps, and 5,630 of 14,040 rows had genuine measured current values.

The fix was the same escape hatch already built for the phase-noise pod's non-MHz x-axis, just applied to a case with no frequency concept at all:

```json
"x_col": "Vgg (V)",
"x_label": "Vgg (V)",
"x_unit": "V"
```

`x_col` bypasses auto-detection entirely; `x_label`/`x_unit` control what every hover tooltip, axis title, and filter label actually says (without them, everything would have defaulted to "Frequency (MHz)"/"MHz" — actively wrong for a voltage sweep). Before running the real (publishing) build, this got sanity-checked with `padb_csv_check.py --x-col "Vgg (V)"` first — a genuinely new-to-the-tool measurement family is exactly the case where that pre-flight check earns its keep.

With the fix in place, `padb_v2.py` loaded all 5,630 real rows, correctly detected Room-only data, and built `scatter` + `boxplot`. Verified directly (not assumed) via a headless render of the actual output: the axis title, filter-bar labels ("Vgg min:"/"Vgg max:", not "Freq min/max"), and hover text all correctly said "Vgg (V)"/"V".

## 4. The serial dropdown that showed an amplifier name

Reviewing the boxplot page turned up something subtly wrong: the Amp condition dropdown looked right, but the *serial number* filter listed `Amp: HMC8500IN` — an amplifier model name — instead of real DUT serials.

Tracing `_stat_boxplot_interactive`'s serial-detection code found the actual mechanism: when a pod's `Group` text has no key that looks like a serial number (no "serial"/"unit id"/"dut id" in any key name, and no value matching a serial-like pattern), the code falls back to a **fallback-of-a-fallback**: it checks for a standalone CSV column already standardized to the name `"Serial"` by the loader, but only accepts its values if they match `^[A-Z]{2,3}\d{5,}$` — a 2–3 letter prefix followed by digits (matching every SG6311A serial format seen so far, e.g. `US65080401`). This pod's real serials are **purely numeric** (`23262500002`, no letter prefix at all) — a different product line's numbering convention. The value check silently failed, so the code fell all the way back to using the entire raw `Group` string (`"Amp: HMC8500IN"`) as the pseudo-serial for every row. Confirmed separately: the *scatter* view's serial filter was unaffected, because it uses a different, more flexible loader (`_load_scatter_csv`) that matches column names by substring rather than requiring an exact match.

Fixed by broadening the accepted pattern to `^([A-Z]{2,3}\d{5,}|\d{5,})$` (either format), scoped deliberately to just the boxplot code path as asked. Verified by regenerating and reading the actual embedded serial list straight out of the rendered HTML: `["23262500002", "23262500003", "23262500004", "23262500005", "23262500006"]` — real DUT serials, confirmed.

## 5. One bug, three more places to check

The natural next question: does `stat_summary` have the same problem? It does — `_aggregate_stat_data()` turned out to have its own **separate, independent copy** of the identical fallback logic (not shared code), with the identical letter-prefix-only pattern. Same fix, same verification approach — this time via a scratch (non-publishing) run rather than disturbing the already-published result, specifically to avoid touching the shared network folder while just confirming a fix.

That scratch-run process itself surfaced a real operational lesson: setting `cfg.pop("publish_to", None)` to "disable publishing" for a quick test actually did the opposite — omitting the key entirely triggers the tool's documented *default* publish location, not "don't publish." Two scratch files landed in the shared results folder before this was caught and cleaned up (both the share copy and the local scratch copy). The correct way to disable publishing for a one-off test is setting `"publish_to": ""` explicitly — an easy trap, worth remembering.

`summary` and `env_coverage` were checked next, and turned out to have a **different shape of the same underlying problem**: rather than a too-strict value pattern, their serial-column search only ever looked at `_grp_*`-prefixed columns — the ones derived from parsing the `Group` text. Since this pod's serial isn't part of the `Group` text at all (it's a wholly separate CSV column), the search space itself structurally couldn't find it, regardless of any pattern. The fix here was a genuine fallback that didn't exist before: check the loader's already-standardized plain `Serial` column when nothing turns up among the `_grp_*` columns. Verified the same way — scratch runs (properly disabled publishing this time) confirmed `summary`'s embedded `dut_info`/`dut_vals` and `env_coverage`'s serial list both now show the real DUT serials instead of the amplifier name.

## 6. Landing it

All four fixes (boxplot, stat_summary, summary, env_coverage) were committed together once verified, and pushed to both remotes (`origin` on GitHub, `bitbucket` on the internal Bitbucket) — kept as one focused commit covering just the serial-detection fix, separate from the unrelated scheduler fix found the same day.

---

## Why this is a good training example

- **Every conclusion came from reading real code or real data — never a guess.** The blank-pod diagnosis came from the raw `.pod` text. The x-axis fix came from the raw CSV headers. Both serial bugs came from reading the actual detection logic, not from assuming "it's probably a naming issue" (the boxplot one, if guessed, would have looked like a naming issue — it wasn't; it was a value-pattern issue with a coincidentally-correct-sounding wrong explanation the first time around, corrected once the loader's own standardization behavior was checked directly).
- **The same symptom had two structurally different root causes** across four views — a reminder that "looks like the same bug" across views built from shared-looking code doesn't guarantee it *is* the same bug once you actually read each copy.
- **A quick verification shortcut (skip publishing for a test) had its own gotcha** — worth knowing before it costs you a real publish accident.
- **Not everything gets fixed immediately** — the stdout-buffering visibility issue was real, confirmed, and deliberately deferred rather than rushed in, because it didn't block the actual goal (a correct dataset) and the priority right before training was stability, not more changes.
