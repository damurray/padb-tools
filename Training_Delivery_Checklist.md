# padb-tools Training — Presenter's Delivery Checklist (Today's Actual Plan)

Not a slide-by-slide script — you're narrating from experience, not reading verbatim (students can read the slides/docs themselves). This is the timeboxed run-of-show for the 1-hour session, with rough minute marks so you can tell at a glance if you're running long.

**Before you start:** close any already-running `padb_web.py` process and start a fresh one (`py C:\temp\tools\webapp\padb_web.py`) — a stale process keeps running whatever code was loaded when it started, so today's fixes won't take effect until it's restarted (this actually happened once already) — [ ] &nbsp; browser tab open on it — [ ] &nbsp; a second terminal open at `C:\temp\tools` for the Claude Code demo — [ ] &nbsp; **start the recording** — [ ]

---

## 0:00–0:05 — Setup check
- [ ] Quick show of hands: who has Python + PADB-R.NET installed and working
- [ ] Note who isn't set up — don't fix anyone live, keep moving
- [ ] Remind them: this session is recorded, they can follow along later once set up

## 0:05–0:15 — Orientation (light touch, not slide-by-slide)
- [ ] The two tiers that matter: Simple vs Interactive (skip Legacy entirely)
- [ ] Both are CLI-driven under the hood — power users should know this exists
- [ ] The web tool is what most people will actually use day to day — that's where the rest of the session lives

## 0:15–0:25 — Tutorial 1 (Simple mode) — **LIVE DEMO**
- [ ] Drop `MaxPowerTutorial1.pod`, Mode: simple, Generate Job, Run Selected
- [ ] Open the result gallery
- [ ] Headline message: **this replaces PADB::Simple scripting**, and setup/automation is dramatically easier — that's the point, not the mechanics

## 0:25–0:45 — Tutorial 2 (Interactive mode) — **LIVE DEMO, main block**
- [ ] Drop `MaxPowerTutorial2.pod`, Mode: interactive, Generate → check only the run job row → Run Selected (extraction + both plot jobs auto-chain)
- [ ] Walk the interactive plots live — filters, Segment by, Group by, whatever lands best in the room
- [ ] **Callout: ~90% complete, real errors still exist.** This is the moment to show the memory list (9 deferred items, each with a confirmed root cause, found and traced over the last two days) — evidence of active, honest tracking, not hand-waving
- [ ] **DEMO** Segment-by tab-through on `ClockSpurs_PADBToolTest.pod` — the one dataset with a real multi-band spec staircase to actually tab through
- [ ] **Reveal** what else got built the last two days — `UHP-IddVsVgg` (a genuinely new measurement type, DC bias sweep, onboarded from a blank/unconfigured pod to a working 4-view dataset) is the strongest single "look what's possible" example if you only show one

## 0:45–0:55 — Tutorial 3 + live Q&A with Claude — **LIVE DEMO**
- [ ] Open Claude Code in `C:\temp\tools`, type `/padb-tools`, run the scripted worked example ("Is MaxPowerTutorial3.pod ready to run in Interactive mode?")
- [ ] Then open the floor — let people ask Claude anything about padb-tools live, not just the script

## 0:55–1:00 — Wrap
- [ ] Summary of what was covered
- [ ] Next steps
- [ ] Open Q&A

---

**If something breaks mid-demo:** fall back to a pre-generated results link rather than debugging live — every tutorial's expected output already exists once you've run it during rehearsal. `GETTING_STARTED.md` → "When something breaks" has the real troubleshooting steps if you want them after the session instead.

**If Q&A goes deeper than expected:** `UHP_IddVsVgg_Case_Study.md` and `Spectral_Data_For_FS_Case_Study.md` are real worked debugging dialogs (not scripted) — good backup material if someone asks "how do you actually track down a bug in this thing."

**The full 45-slide deck (`PADB_Simple_and_Interactive.pptx`) and the original slide-by-slide checklist content are still there if the room wants more detail than this pass covers** — this version is deliberately the leaner, narrated-from-experience path, not a replacement reference.
