# Interactive Plots — User Guide

You've been sent a link to a results page and want to know what you're looking at and how to use it. This guide is written for that: no job.json, no PADB, no pipeline knowledge assumed. If you're the one *generating* these plots, see `Quick_Start.md` / `PADB_Tools_Guide.md` instead.

---

## Opening a results page

Every page is a single, self-contained HTML file — no server, no login, no install. Just open the link (or double-click the file) in any browser (Chrome, Edge, Firefox). It works the same whether it's on a network share or your own machine.

Most results are organized as a gallery: an `index.html` page with links to every individual view. If you were sent a direct link to one view (e.g. `..._scatter.html`), you can still get to the others — look for a link back to the index near the top of the page, or ask whoever sent it for the gallery link.

There are up to six kinds of view. Not every result has all six — which ones exist depends on the data (see **"Why don't I see a Distribution/Env Coverage/Summary view?"** below):

| View | One-line summary |
|---|---|
| **Scatter** | Every individual measurement, plotted raw |
| **Boxplot** | Box-and-whisker summary per condition and temperature |
| **Stat Summary** | Mean/spread statistics per frequency, with pass/fail bounds |
| **Summary** | All-temperature min/max/mean bands, one view for everything |
| **Env Coverage** | How much the environment (temperature) shifts the measurement |
| **Distribution** | Histogram/density curves of how measurements are spread |

---

## Controls you'll see on most pages

These same controls show up, in slightly different forms, across most of the six views. Once you know what they do here, you'll recognize them everywhere.

- **Condition filter dropdowns** — checkboxes for each thing the data varies by (e.g. Port, Test Step, AlcState). Uncheck a value to hide it from the plot; check it again to bring it back. "Select all" / "select none" buttons are usually nearby.
- **Serial / Port filter** — same idea, specifically for excluding individual test units (DUTs) or output ports.
- **Frequency range controls** — a slider and/or two number boxes (min/max) that zoom the X axis to a sub-range. There's usually a **Log X** toggle next to it, for switching the frequency axis between linear and logarithmic scale.
- **Group by** — collapses the legend down to one chosen dimension (e.g. just "SpurType") when the plot otherwise shows a confusing number of near-duplicate lines that only differ by a small technical detail. Switch it back to "Condition" to see everything broken out again. *(Present on Stat Summary, Summary, Env Coverage, and Boxplot.)* On Boxplot, the default auto-switches from "Condition" to "Serial Number" once a pod has a lot of raw (per-unit-fragmented) conditions — over 150 — since a Condition-grouped legend that large would be bigger than the plot itself; below that it still defaults to Condition.
- **Segment by** *(with Prev / Next buttons)* — for data with a spec that changes at different frequency bands (a "staircase" spec), this jumps the frequency range straight to each band in turn. **The whole control is hidden, not just Prev/Next, when the plot's data has no frequency-varying Spec/Limit/Uncertainty at all** — if you don't see a "Segment by" control, that's expected for a flat/constant spec, not a bug. If the control *is* there but Prev/Next don't do anything, see the troubleshooting section below. *(Present on Scatter, Boxplot, Stat Summary, Summary, Env Coverage, and Distribution, whenever the underlying data has something to segment by.)*
- **Global Filter (GF)** — a DUT exclusion list that's remembered by your browser and shared across every view of this result set. Set it once (usually from the Boxplot page — see below) and every other view automatically reflects it. It only ever *adds* exclusions until you clear it. The GF has two display modes: the normal **Exclude** mode (the excluded points are hidden), and an **Inspect** mode that flips it around to show *only* the excluded points, so you can look at exactly what you've filtered out. Inspect mode always starts off (Exclude) when you open a page — and while it's on, a prominent amber "⚠ INSPECT MODE" banner sits above the plot so a page showing almost nothing is never mistaken for missing data. If a plot looks nearly empty, check for that banner first.
- **Help (ⓘ) button** — every main view has one. Click it for a short in-page explanation of that view's own controls, plus an automatic check that flags anything unusual it finds in the data (like a spec that's backwards). If the pod this data came from was configured to extract only a subset of runs (e.g. Room-only), that's also shown here so you know the data was already pre-scoped before it ever reached this page.
- **Statistics Table** — a button that expands a detailed numbers table below the plot (mean, σ, counts, pass/fail bounds, etc., depending on the view). Click again to collapse it.
- **CSV export** — downloads whatever's currently visible (after your filters are applied) as a CSV file, so you can take the exact numbers into Excel or elsewhere.
- **Log X** — switches the frequency axis between linear and logarithmic. Purely visual, doesn't change the data.
- **Autoscale Y** — a dedicated button (on all six views) that rescales just the Y axis to fit whatever's currently shown, without touching your frequency zoom. Handy after you've filtered or zoomed the X axis and the vertical scale no longer fits the visible data.

None of these controls change the underlying result file — you're only changing what's displayed in your own browser. Reloading the page resets most filters (though a few, like GF and some frequency ranges, are remembered).

---

## Scatter

**What it shows:** every individual measurement point, plotted against frequency. This is the closest thing to "raw data" — one dot (or line) per unit per measurement.

**Good for:** spotting an obviously bad unit, seeing exactly which frequencies are out of spec, sanity-checking that the data looks like what you'd expect before trusting any of the statistics views.

**Controls specific to this view:**
- **Group by** *(a different one from the shared list above — this is a display grouping, e.g. by serial number or by test step, not a statistical pooling)* — changes how traces are colored/split. Defaults to whichever dimension has the fewest distinct values, so the legend starts out as small as possible — switch it if you want a different breakdown.
- **Sort** — reorders traces by name, by "worst" value, or by median.
- A dashed (or, for a frequency-varying spec, stepped) red line marks the spec limit(s), when the data has them.

**Hover** over any point to see its exact frequency, value, and group label.

---

## Boxplot

**What it shows:** one box-and-whisker per condition, split out by temperature. The box covers the middle 50% of measurements (Q1–Q3), the whiskers extend to the typical range, and dots beyond the whiskers are outliers.

**Good for:** comparing spread across temperatures at a glance, and finding exactly which unit produced an outlier (hover over an outlier dot to see its serial number).

**Controls specific to this view:**
- **Temperature filter** — show/hide specific temperature conditions.
- **Group by (Condition / Temperature Step / Port / Serial Number / any condition dimension)** — defaults to Condition for most pods, but auto-switches to Serial Number once a pod has over 150 raw conditions (the legend would otherwise be bigger than the plot). Condition-dimension filters (the checkboxes above) still work correctly no matter which Group by mode is active — unchecking every value always shows nothing, in every mode.
- **TLL display (Both / Upper only / Lower only)** — only appears when the data has no built-in spec limit; lets you pick which side of the tolerance band to display.
- **Data filter (All data / Passing only / Upper limit / Lower limit)** — trims which raw points feed into the box statistics. If "Passing only" has nothing to compare against for this data (no spec at all, and no TLL override typed in), an orange note now appears next to the radio saying so — see the FAQ below.
- **Show points** — overlays every individual measurement as a small dot on top of its box, so you can see the raw data behind the summary.
- **Collapse dup runs** — off by default. If a unit was genuinely measured more than once at the exact same point (same condition, port, and temperature), this averages those repeats into one point before the box statistics are computed, so that unit doesn't quietly count twice. See **"Why does the Statistics Table show a DUT count larger than the number of units I tested?"** below.
- **Global Filter buttons** — this is the page where you actually *set* the Global Filter: "Set filter as GF" / "Set outliers as GF" / "Set delta outliers as GF" (these add to, not replace, whatever's already set), plus "Clear global filter", "Export GF CSV" / "Import GF CSV" (save/reload your exclusion list as a file), and **"Copy PADB Filter"** — a dropdown that builds a real PADB filter expression (matching PADB's own syntax, ready to paste into its filter box) in one of three modes:
  - **Plot view** — the filter that reproduces exactly what this page is currently narrowed to (condition, Serial, Port, Frequency, Temperature).
  - **Global Filter only** — the filter that reproduces the Global Filter's *full captured scope* on its own (the harmonic/condition, frequency range, and serials it was built from — not just a bare serial-number exclusion). This stays fixed to what the GF captured; changing other plot controls afterward doesn't alter it.
  - **Plot + GF** — both combined: the current plot view *and* the Global Filter. Note "Set outliers as GF" checks each *currently-selected Temperature checkbox* independently — with every temperature checked (the default), it isn't Room-only; narrow to just Room first if that's what you want. "Set delta outliers as GF" is the genuinely different, always-non-Room metric (temperature sensitivity relative to each DUT's own Room baseline).
- **Site Population Check** — only appears on a page comparing two sites (see **Cross-Site Comparison** below). Tests each new-site DUT's value at each frequency/temperature against the established site's own range there, with a per-DUT summary and a table flagging any frequency where multiple DUTs are affected at once (the strongest signal that something's off with the station/setup, not any one DUT). Also on the **Stat Summary** and **Summary** pages, not just Boxplot.

---

## Stat Summary

**What it shows:** for each condition, a mean line, a shaded band showing the statistical spread, and (when there's enough data) tolerance-interval bounds — the range that's expected to contain most of the population with high confidence. Pass/fail lines show where the spec sits relative to that.

**Good for:** the main "is this measurement good enough" statistical view — one glance tells you whether the population, not just individual units, is comfortably inside spec.

**Controls specific to this view:**
- **Serial number filter** — uncheck specific units to exclude them; the statistics recompute live.
- **TI / NP-TI toggles** — show or hide the two different styles of statistical bound.
- **Show points** — overlays individual per-unit measurements; excluded units show up in grey rather than disappearing, so you can still see the full picture.
- **Show excluded** — shows conditions you've filtered out as faint grey traces in the background, for comparison.

---

## Summary

**What it shows:** the same idea as Stat Summary, but rolled up across *every* temperature at once — a min/max band, a tolerance band, and a mean line, per condition.

**Good for:** a single view that answers "across every temperature this unit was ever tested at, does it stay in spec?" without needing to flip through per-temperature pages.

**Note:** there's no per-unit (serial number) filter dropdown on this page — that's intentional (this view combines all units together before drawing). To exclude a specific unit here, set it via the Global Filter on the Boxplot page instead — it'll be picked up automatically.

**Controls specific to this view:**
- **TLL display / Data filter** — same idea as Boxplot's version of these controls.
- **Show excluded** — same as above, faint grey bands for filtered-out conditions.

---

## Env Coverage

**What it shows:** how much a measurement shifts purely because of temperature (or another environmental variable), separate from the spec itself. Shows the room-temperature spread alongside the extra spread introduced by temperature, and how much spec margin is left over after accounting for both.

**Good for:** answering "is temperature eating into my margin?" — useful when a unit passes at room temperature but you're worried about hot/cold extremes.

**Controls specific to this view:**
- **P / C sliders** — adjust the statistical confidence level used for the bands (ask whoever generated the data if you're not sure what these should be set to).
- **M.U. input** — a measurement-uncertainty value that gets subtracted from the spec to show the true remaining margin.
- **Spec hi / Spec lo override** — lets you type in spec values by hand if the underlying data doesn't already have them.
- **Temperature filter** — include/exclude specific non-room temperatures.

Zooming in on the plot (drag a box, or scroll) now also narrows the Statistics Table to match — the same behavior Stat Summary and Boxplot already had. Double-click the plot (or use the "Reset axes" button) to go back to the full range.

---

## Distribution

**What it shows:** curves showing how measurements are distributed — either their absolute values, or how much they shift relative to room temperature ("delta").

**Good for:** understanding the *shape* of a spread, not just its range — e.g. spotting whether a "bad" measurement is a genuine outlier or just the normal tail of a wide distribution.

**Controls specific to this view:**
- **Delta vs. Absolute toggle** — switch between "shift from room temperature" and "actual measured value" views.
- **Spur type filter** — for pods with multiple spur types, isolate one or a few at a time.
- **Delta summary table** — a small table below the chart with per-type statistics.

---

## Cross-Site Comparison

Some result pages combine data from two sites — e.g. an established site's data against a newer site's first production units — instead of just one. You'll know because the Boxplot, Stat Summary, and Summary pages will have an extra "Site Population Check" button, and "Site" will show up as its own filter dimension alongside the usual conditions. Want to build one of these yourself? See `Compare_Mode_Cheatsheet.md` for the one-page steps.

**What Site Population Check tells you:** for each new-site DUT, whether its measurements at each frequency/temperature fall inside the range the established site's own units produced there. Anything outside that range is worth a look — the table it shows breaks that down further:
- If one specific DUT is flagged repeatedly across many frequencies, while its own site's other units look fine — that's likely a bad unit.
- If *several different* units from the new site are all flagged at the *same* frequency — that points at something about the site/station/fixture, not any one unit, since it's unlikely several independent units would fail identically by coincidence.
- A DUT flagged as "below" the established range (rather than toward the side that would actually fail spec) usually isn't a compliance problem — it's just a real difference worth being aware of (e.g. a calibration offset), not something urgent.

The table's "Suggested triage" column reflects this reasoning, but it's a suggestion to guide where you look first, not a final verdict — use your own judgment alongside it.

There's also an always-visible amber banner near the top noting anything one site has that the other doesn't (e.g. a temperature or port the new site hasn't tested yet) — worth checking whether that's expected for where that site's test plan currently stands.

**On the Boxplot page specifically**, two extra columns help tell apart "this DUT was measured under more real test conditions than others" from "this DUT's own measurement was genuinely repeated": **Dup runs** / **Genuinely repeated freqs** in the per-DUT table, and **"SR dup pts"** in the per-point detail table (listing which established-site units, if any, have more than one raw measurement at that exact point, e.g. `US65080419×2`). See the FAQ below for why this distinction matters.

**Export CSV (All)** / **Export CSV (Outside only)** buttons (Boxplot, Stat Summary, and Summary) download the per-point detail table exactly as shown, so you can take the flagged points into Excel or elsewhere.

---

## Frequently asked questions

**Why don't I see a Distribution / Env Coverage / Summary view for this result?**
Those three views only make sense when the data covers more than just room temperature. If a measurement was only ever tested at room temperature, only Scatter and Boxplot (and sometimes Stat Summary/Summary) exist — that's expected, not a missing file.

**Why don't I see a "Segment by" control at all, or why did it find nothing to tab through?**
Segment by only produces Prev/Next stops when the spec itself genuinely changes at different frequency bands (a "staircase"). If the spec is flat across the whole frequency range — even if the raw numbers look like they vary slightly per unit — there's only one "segment," so there's nothing to page between, and the control disappears entirely rather than showing an inert selector. This isn't a bug; it means the spec for this particular measurement really is constant.

**The Help (ⓘ) button says something about "inverted" rows — what does that mean?**
It means the data has rows where the upper spec limit is numerically lower than the lower spec limit for some units — backwards from the normal convention. This is a data/entry issue with the specific test run, not something wrong with the plot. The Help panel will usually tell you which serial number, port, or other value the odd rows are concentrated in — that's the fastest way to go investigate.

**Why does the Statistics Table show a DUT count larger than the number of units I tested?**
It's counting raw measurement rows, not distinct units — if a unit was genuinely measured more than once at the exact same point, it counts twice. A "Dup runs" column (Boxplot's Statistics Table, and the Site Population Check's per-DUT table on a comparison page) tells you how much of the count is from real repeats rather than independent units — and importantly, it *sums across every duplicated unit*, so a value like "3" can mean either "one unit measured 3 times" or "three different units each measured twice," which is why the Site Population Check lists them out by name instead of just a number (e.g. `US65080419×2, US66060602×2`). A unit tested on two ports (RF1/RF2) is *not* a duplicate — those are two independent measurements and are never counted as repeats of each other. On Boxplot, check "Collapse dup runs" to average real repeats into one point and remove the extra weight from the box statistics; Stat Summary and Summary already do this automatically, with no toggle needed.

**Why do some pages show an amber "Statistical note" banner about noisy data?**
Env Coverage, Distribution, Stat Summary, and Summary all compute a tolerance-interval-style bound (or, for Distribution, a density curve) that assumes a reasonably well-behaved, low-noise set of measurements. That's always true of the method, not something specific to your data, so the note is shown on every page for these views as a standing reminder — with few units, high measurement noise, or a visibly scattered spread, treat the bound shown as a guide, not a guarantee, and cross-check against the Scatter or Boxplot view for that same data.

**I set Boxplot's Data filter to "Passing only" and nothing changed.**
Check whether an orange note appeared next to the radio button. "Passing only" filters against the spec — if this measurement has no spec limit in the data *and* you haven't typed a value into the TLL override field, there's genuinely nothing to compare against, so it correctly leaves the data unchanged and tells you why. Type a value into the TLL override field (or use "Upper limit"/"Lower limit" instead, which take a typed value directly) if you want it to actually filter.

**Why does Stat Summary's table sometimes only show one spec value ("Spec Spt") instead of both?**
It shows one side (↓ or ↑) when the measurement is genuinely one-sided (e.g. a guaranteed-minimum-power spec), and shows both when the direction is unclear either way — meaning both sides are genuinely relevant, matching what the plot itself already shows. If you were seeing only the upper side even though the plot clearly shows both, that's a bug that's since been fixed — reload the page.

**Why doesn't this view have a "serial number" filter?**
A few views (Summary, and always for Env Coverage's room/delta baseline) pre-combine every unit's data before drawing, so there's no per-unit toggle to expose. If you need to exclude a specific unit from those views, set the Global Filter on the Boxplot page instead — it applies everywhere automatically.

**A control doesn't seem to do anything when I click it.**
Try a hard reload of the page first (Ctrl+Shift+R, or Cmd+Shift+R on Mac) — a stale cached copy of an older version of the page is the most common cause of a control looking broken. If it still doesn't respond after that, it's worth reporting.

**Can I break anything by clicking around?**
No. Every control here only changes what your browser displays — it never modifies the underlying result files. Feel free to explore.

---

## Where to go for more help

- Something looks *wrong* with the data itself (not just confusing) — contact whoever generated the result.
- You want to understand the statistics being shown in more depth — see the **Understanding the Statistics** section of `PADB_Tools_Guide.md`.
- You want to generate or regenerate these plots yourself — see `Quick_Start.md` (or `Interactive_Mode_Cheatsheet.md` for the short version).
