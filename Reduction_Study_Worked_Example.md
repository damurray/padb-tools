<!-- Worked example: a real end-to-end test-point reduction & sentinel study.
     Illustrates the reduction workflow — all-runs reduction_extraction (padb_run.py),
     merge, then padb_sentinel.py + padb_testpoint_reduce.py. Data artifacts live
     outside the repo (OneDrive); only this write-up is versioned here. -->

> **Worked example** — kept in the repo to show how the reduction/sentinel tools are used and read end to end on real SR-vs-AMC data. The referenced CSV/report artifacts are not committed (they live in the study's data folder).

# Test-Point Reduction & Sentinel Study — Harmonics_and_Subharmonics (SR vs AMC2)

**Date:** 2026-09-16  **Pod family:** Harmonics_and_Subharmonics_Spec_Setting_Data2
**Sites:** SR (Santa Rosa, reference) vs AMC2 (Malaysia)  **Analytic:** `Harmonics_and_Subharmonics_Env_Dataset` (Type=80 scatter, dBc, upper-limit-only spec)

---

## 1. Purpose

Determine which swept test frequencies can be trimmed from the harmonics screen, and — more importantly — which points are **earning their keep** by catching real, fixable defects, using **all-runs** data from both sites so run-to-run behaviour is visible. Report only; no data, pod, or plot was modified.

## 2. Method

- **All-runs extraction per site** (`RunStatus={All}`, `AllRunResults=True`, `LastResult=False`), grouped by `Test Run Datetime` so every run of each DUT is a distinguishable, chronologically-orderable point.
- **Per-site date windows at each site's real data start** — SR `2026-05-21 → today`, AMC2 `2026-07-21 → today`. (Starting before a site's data existed pulls un-analysable runs and crashes the analysis — see §7.)
- **Merged** the two site CSVs with `Site:` tags → one compare dataset (**125,455 rows**, 9 serials/site, run index up to 17).
- **Analysed** with `padb_sentinel.py` (run-to-run classification) and `padb_testpoint_reduce.py` (trim recommendation), `--primary-site SR`.

## 3. Headline result

The raw analysis looked alarming — **137 "cross-site failures"** (clean at SR, failing at AMC2) — which would suggest widespread new AMC2 fault modes and "do not trim." **The triage showed otherwise:**

| | Raw | After removing 1 bad unit |
|---|---|---|
| Cross-site re-expansion triggers | **137** | **12** |
| Confirmed sentinels | 32 | 18 |
| Unresolved fails | 154 | 28 |
| Regressions | 166 | 23 |
| Redundancy candidates | 207 | **332** |

**125 of the 137 were one anomalous unit.** The genuine cross-site signal is small (12), and far more of the sweep is legitimately trimmable (332) once the bad unit is removed.

## 4. Finding A — `MY66250001` is bad data (quarantine)

One AMC2 unit drove 113 of the 137 raw triggers. Its readings are **non-physical**:
- Value distribution reaches **+10.69 dBc** (a harmonic *above* the carrier is impossible); **~2 % of its readings are ≥ −5 dBc**, including exact **`0.00` dBc** spikes.
- The "46–62 dB over spec" exceedances are these glitched `0.00`/near-carrier readings vs a normal −37…−60 dBc limit.
- A healthy peer (`MY66250002`) maxes at −33.5 dBc — every reading physical.

**Action:** quarantine / re-test `MY66250001`; a datapak or measurement glitch, not a real harmonic failure. Its data must not drive any screen decision.

## 5. Finding B — 12 genuine cross-site triggers (real, marginal)

With `MY66250001` removed, the real SR-clean/AMC2-fail set is **12 frequencies, all 8.0–17.6 MHz, 2nd harmonic** (3rd only at 8 MHz):

- Driven by **`MY66250002`** (11 of 12), with **`MY66250011`** joining at 8 MHz (the worst point).
- **All values physical** (−20…−37 dBc); zero anomalies.
- **Clean low-frequency roll-off**: exceedance is worst at 8 MHz (**+7.6 dB**) and tapers monotonically to **+0.1 dB by 17.6 MHz** — the signature of a real low-band H2 filtering/match weakness.

**Action:** investigate low-band (8–18 MHz) 2nd-harmonic performance on `MY66250002` (and `MY66250011`). Keep these test points — they catch a real, if marginal, AMC2 difference.

## 6. Finding C — 18 confirmed sentinels (proven keepers — never trim)

Frequencies where a unit **failed on an early run, then passed on a later run after a repair/cal** (27 transitions). These points demonstrably catch fixable defects:

- **Two bands:** low-freq H2/H3 (8–17.6 MHz, mostly `MY66250002`) and high-freq H2/H0.5 (7500, 12000, 14600–14800 MHz, multiple DUTs).
- **Both sites recover** (AMC2 19, SR 8) → a real property of the test *process*, not site-specific.
- **Large recoveries** (a marginal +0.1…+7.6 dB fail drops to 15–52 dB *under* spec next run) → genuine repairs fixed genuine harmonic problems.
- **Standout:** `12000.010 MHz, H0.5` (subharmonic) — four units across both sites failed then recovered. A high-value sentinel.
- `MY66250002` at low-freq H2 is **intermittent** (fails → fixed → drifts back), appearing as confirmed sentinel, cross-site trigger, and unresolved — a marginal unit / marginal spec point.

## 7. What can be trimmed

`padb_testpoint_reduce` (adaptive) on the raw merged data recommended **keep 368 / drop 5 (~1.3 %)** — deliberately conservative because so much of the sweep carries fault/margin signal. On the **cleaned** data, **332 frequencies are redundancy candidates**.

> **Redundancy ≠ dispensable fault coverage.** A frequency that passes on every run may still be a *dormant* sentinel for a fault mode that hasn't occurred yet. Trim only a **production screen** backed by periodic full characterisation + escape monitoring, on merged multi-site data; never trim the characterisation coverage itself, and never on a single site's passing data. Confirmed sentinels (§6) and genuine cross-site triggers (§5) are hard-protected.

## 8. Recommendations

1. **Quarantine / re-test `MY66250001`** — non-physical data.
2. **Investigate low-band (8–18 MHz) H2** on `MY66250002` / `MY66250011` — real marginal fails.
3. **Keep** the 18 confirmed sentinels + 12 genuine triggers.
4. **Trim** only from the 332 redundancy candidates, and only under a monitored/reversible screen (see §7).
5. Base any AMC2 screen decision on the **cleaned** picture (12 real triggers), not the raw 137.

## 9. Process notes (for reproducibility)

- Data pulled as **all-runs**, grouped by **Test Run Datetime**, per-site windows at each site's data start.
- Extractions were done via a **manual PADB pull** (produces the CSV in R-Plots). The web tool's *automated* reduction extraction currently writes nothing under `-dir` + native-render-off for the grouped all-runs config — to be fixed in the guided reduction-study workflow.
- Two setup pitfalls fixed along the way: (a) `LastResult=True` left alongside `AllRunResults=True` crashes PADB (`NullReferenceException`); (b) a date window starting **before** a site's data pulls un-analysable runs and crashes the analysis.

## 10. Artifacts (this folder)

- `Harmonics_reduction_compare_merged.csv` — merged all-runs SR+AMC2
- `..._sentinel_audit.txt/.csv` — full run-to-run classification (raw)
- `..._no_MY66250001_sentinel_audit.txt/.csv` — cleaned classification (the real picture)
- `..._testpoint_reduction.txt/.csv` — trim recommendation + per-condition margin/relVar/ceiling
