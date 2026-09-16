#!/usr/bin/env python3
"""padb_testpoint_reduce.py -- test-plan trim RECOMMENDATION (added 2026-09-16).

Given one analytic's Type=80 scatter CSV, flag which SWEPT test frequencies are
redundant -- i.e. removing them from the pod's sweep would not lose analytically
meaningful coverage -- so you can shorten the test plan (and its run time). This
is a *report only*: it never edits the CSV, the pod, or any plot. The raw data
stays the source of truth; the report tells you exactly what could be dropped and
why, for you to decide.

Design (scoped with David 2026-09-14/16):
  * PROTECTED (never dropped), for ANY condition:
      - sweep endpoints (min/max frequency),
      - spec/limit/uncertainty TRANSITION edges (a bound changes vs the neighbour),
      - spec FAILS (a value outside its own Upper/Lower limit),
      - flagged OUTLIERS (IQR fence within a (condition, frequency) population).
    (Hardware/calibration band edges slot in here later, once the band-edge
     config exists -- not required for this first version.)
  * REDUNDANT (candidate to drop): among non-protected interior points, drop the
    ones whose per-condition MEAN-vs-frequency curve is reconstructed from its
    retained neighbours within a tolerance eps -- greedy Douglas-Peucker-style,
    worst-condition governs, and never below a per-condition floor of retained
    points. A real peak/step is unreconstructable from neighbours -> high error
    -> kept, so structure is preserved without a separate "extreme" rule.

Redundancy tolerance eps -- self-calibrating by default (David's point: a
noise-floor-limited spurs measurement has high per-point variance and is very
redundant, whereas an absolute Max-Power measurement is low-variance and must be
trimmed conservatively):
  * adaptive (default): eps_c = k * (that condition's own measured point
    dispersion -- the robust within-(condition, frequency) spread across
    DUTs/repeats/temps). Auto-loosens for spurs, tightens for Max Power. If a MU
    budget is supplied it is used as a FLOOR (eps never tighter than the
    uncertainty you already can't resolve).
  * mu:       eps = mu_frac * (MU + dEnv)      (needs --mu)
  * absolute: eps = --eps  (Y units, same for every condition)
  * target:   ignore eps; drop least-important first until --target-pct reached
              (still respecting protections + floor).

Usage:
  py padb_testpoint_reduce.py <scatter.csv> [--mode adaptive|mu|absolute|target]
     [--k 1.0] [--eps 0.2] [--mu-frac 0.5] [--target-pct 30] [--floor 5]
     [--mu 0.1] [--denv 0.0] [--x-col "Exact Column"] [--out DIR] [--quiet]

Exit code 0 always on a successful analysis (advisory tool); 1 on a load/usage
error or if the internal safety assertion trips (a protected point was dropped or
a floor was violated -- a bug, reported loudly rather than shipped silently).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import padb_plots

# --- condition labelling (strip serial/port like the views do) ---------------
_SERIAL_KEY_KWS = ("serial", "unit id", "dut id", "s/n")
_SERIAL_VAL_PAT = __import__("re").compile(r"^[A-Z]{2,3}\d{5,}$")
_PORT_KEY_KWS = ("port",)


def _condition_labels(df: pd.DataFrame) -> pd.Series:
    """One label per row = the measurement condition (every Group key EXCEPT
    serial-like and port keys), so DUTs/ports pool into one condition -- the same
    notion of "condition" the interactive views use. Falls back to 'All' when the
    CSV has no usable Group text."""
    if "Group" not in df.columns or df["Group"].isna().all() or (df["Group"] == "").all():
        return pd.Series(["All"] * len(df), index=df.index)
    uniq = [g for g in df["Group"].dropna().unique() if g != ""]
    kv = {g: padb_plots._parse_group_kv(g) for g in uniq}
    all_keys: set[str] = set()
    for d in kv.values():
        all_keys.update(d.keys())
    cond_keys: list[str] = []
    for key in sorted(all_keys):
        if any(k in key.lower() for k in _SERIAL_KEY_KWS):
            continue
        vals = {d.get(key, "") for d in kv.values() if key in d}
        if vals and sum(_SERIAL_VAL_PAT.match(v) is not None for v in vals) / len(vals) > 0.5:
            continue  # serial by value shape
        if any(k in key.lower() for k in _PORT_KEY_KWS):
            continue  # port pools into the same population
        if 1 < len(vals) <= 50:
            cond_keys.append(key)

    def _label(g: str) -> str:
        d = kv.get(g, {})
        parts = [f"{k}: {d[k]}" for k in cond_keys if k in d]
        return "  ".join(parts) if parts else "All"

    return df["Group"].map(_label).fillna("All")


# --- helpers -----------------------------------------------------------------
def _mad(a: np.ndarray) -> float:
    """Median absolute deviation, scaled to a std-equivalent (x1.4826)."""
    a = a[~np.isnan(a)]
    if len(a) < 2:
        return 0.0
    return float(np.median(np.abs(a - np.median(a))) * 1.4826)


def _row_margins(vals: np.ndarray, hi: float, lo: float) -> np.ndarray:
    """Signed headroom to the nearest present limit, per measurement (positive =
    inside with headroom, negative = outside/fail). NaN when no limit at all."""
    if np.isnan(hi) and np.isnan(lo):
        return np.full(len(vals), np.nan)
    m = np.full(len(vals), np.inf)
    if not np.isnan(hi):
        m = np.minimum(m, hi - vals)
    if not np.isnan(lo):
        m = np.minimum(m, vals - lo)
    return m


class Cond:
    """One condition's sweep: mean curve over the shared frequency grid, its own
    measured point dispersion (the noise scale that sets eps), and per-frequency
    margin-to-limit / fail context."""

    def __init__(self, label, freqs, mean, dispersion, n_at, margin, fails):
        self.label = label
        self.freqs = freqs          # frequencies this condition actually has data at
        self.mean = mean            # mean value per freq (aligned to self.freqs)
        self.dispersion = dispersion
        self.n_at = n_at            # #measurements per freq
        self.margin = margin        # worst-case (min) headroom per freq (NaN if no limit)
        self.fails = fails          # #measurements failing a limit, per freq
        self.margin_by_f = {float(f): m for f, m in zip(freqs, margin)}
        self.fails_by_f = {float(f): int(x) for f, x in zip(freqs, fails)}

    @property
    def n_total(self) -> float:
        return float(np.nansum(self.n_at))

    @property
    def fail_rate(self) -> float:
        tot = self.n_total
        return float(np.nansum(self.fails) / tot) if tot else 0.0

    @property
    def min_margin(self) -> float:
        m = self.margin[~np.isnan(self.margin)]
        return float(np.min(m)) if len(m) else float("nan")

    @property
    def mean_margin(self) -> float:
        m = self.margin[~np.isnan(self.margin)]
        return float(np.mean(m)) if len(m) else float("nan")

    @property
    def rel_variance(self) -> float:
        """Noise relative to headroom: dispersion / |median positive margin|. High
        => the measurement noise is large compared to how much room to the limit
        there is (a marginal, risk-carrying measurement); low => comfortably clear."""
        m = self.margin[~np.isnan(self.margin)]
        m = m[m > 0]
        med = float(np.median(m)) if len(m) else float("nan")
        if not med or np.isnan(med) or med <= 0:
            return float("nan")
        return self.dispersion / med

    def risk_ceiling(self, margin_k: float) -> tuple[str, float]:
        """Data-driven recommended MAX reduction for this condition (fraction of
        its points), with the reason. Heuristic guidance bands, not a guarantee:
        the riskier the measurement (fails / thin headroom / noise comparable to
        headroom), the less of its sweep should be trimmed. relVar (noise/headroom)
        is orthogonal to redundancy -- a far-below-limit noisy spur is both very
        reducible AND low risk."""
        thin = (not np.isnan(self.min_margin) and self.dispersion > 0
                and self.min_margin <= margin_k * self.dispersion)
        rv = self.rel_variance
        if self.fail_rate > 0:
            return "fails present -> keep dense coverage", 0.20
        if thin:
            return "thin margin to limit", 0.30
        if not np.isnan(rv) and rv >= 0.5:
            return "noise ~ headroom (marginal)", 0.45
        if not np.isnan(rv) and rv >= 0.2:
            return "moderate headroom", 0.60
        return "comfortably clear, low noise", 0.80


def _build_conditions(df: pd.DataFrame) -> tuple[list[Cond], np.ndarray]:
    df = df.dropna(subset=["Frequency_MHz", "Value"]).copy()
    df["_cond"] = _condition_labels(df)
    has_hi = "Upper_Limit" in df.columns
    has_lo = "Lower_Limit" in df.columns
    grid = np.array(sorted(df["Frequency_MHz"].unique()), dtype=float)
    conds: list[Cond] = []
    for label, sub in df.groupby("_cond", sort=True):
        fq_sorted = sorted(sub["Frequency_MHz"].unique())
        means, ns, margins, fails, spreads = [], [], [], [], []
        for f in fq_sorted:
            r = sub[sub["Frequency_MHz"] == f]
            vals = r["Value"].to_numpy(dtype=float)
            vals = vals[~np.isnan(vals)]
            means.append(float(np.mean(vals)) if len(vals) else np.nan)
            ns.append(len(vals))
            spreads.append(_mad(vals))
            hi = float(r["Upper_Limit"].dropna().iloc[0]) if has_hi and r["Upper_Limit"].notna().any() else np.nan
            lo = float(r["Lower_Limit"].dropna().iloc[0]) if has_lo and r["Lower_Limit"].notna().any() else np.nan
            mg = _row_margins(vals, hi, lo)
            margins.append(float(np.min(mg)) if len(mg) and not np.all(np.isnan(mg)) else np.nan)
            fails.append(int(np.nansum(mg < 0)))
        spreads_arr = np.array(spreads)
        disp = float(np.median(spreads_arr[spreads_arr > 0])) if (spreads_arr > 0).any() else 0.0
        conds.append(Cond(label, np.array(fq_sorted, dtype=float),
                          np.array(means, dtype=float), disp,
                          np.array(ns, dtype=float), np.array(margins, dtype=float),
                          np.array(fails, dtype=float)))
    return conds, grid


def _protected_frequencies(df: pd.DataFrame, grid: np.ndarray,
                           conds: list[Cond], margin_k: float) -> dict[float, str]:
    """Frequencies that must never be dropped, mapped to the reason. Endpoints,
    spec/limit/uncertainty transitions, spec fails, thin margin-to-limit
    (near the pass/fail edge), and IQR outliers."""
    prot: dict[float, str] = {}
    if len(grid):
        prot[float(grid[0])] = "endpoint"
        prot[float(grid[-1])] = "endpoint"

    df = df.dropna(subset=["Frequency_MHz"]).copy()
    df["_cond"] = _condition_labels(df)

    # Thin margin-to-limit: a point whose worst-case headroom is within margin_k
    # of the condition's own noise is where pass/fail is decided -- keep it even
    # if the curve is flat there (importance beats redundancy). margin_k<=0 off.
    if margin_k > 0:
        for c in conds:
            thr = margin_k * c.dispersion
            for f, m in c.margin_by_f.items():
                if not np.isnan(m) and m >= 0 and m <= thr:
                    prot.setdefault(float(f), "thin margin (near limit)")

    # Transition edges: a bound value changes between consecutive frequencies.
    bound_cols = [c for c in ("Upper_Limit", "Lower_Limit", "Spec_Hi", "Spec_Lo",
                              "Unc_Hi", "Unc_Lo") if c in df.columns]
    for _, sub in df.groupby("_cond", sort=False):
        # one representative bound value per frequency (bounds are per-(cond,freq))
        bf = (sub.groupby("Frequency_MHz")[bound_cols].agg(
                  lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan)
              if bound_cols else None)
        if bf is not None and len(bf) > 1:
            fq = np.array(bf.index, dtype=float)
            for col in bound_cols:
                v = bf[col].to_numpy(dtype=float)
                for i in range(1, len(v)):
                    a, b = v[i - 1], v[i]
                    changed = (np.isnan(a) != np.isnan(b)) or (
                        not np.isnan(a) and not np.isnan(b) and abs(a - b) > 1e-12)
                    if changed:
                        prot.setdefault(float(fq[i - 1]), "spec/limit transition")
                        prot.setdefault(float(fq[i]), "spec/limit transition")

    # Spec fails: a value outside its own per-point limit.
    if {"Upper_Limit", "Lower_Limit"} & set(df.columns):
        hi = df.get("Upper_Limit")
        lo = df.get("Lower_Limit")
        val = df["Value"]
        fail = pd.Series(False, index=df.index)
        if hi is not None:
            fail |= val > hi
        if lo is not None:
            fail |= val < lo
        for f in df.loc[fail.fillna(False), "Frequency_MHz"].unique():
            prot[float(f)] = "spec fail"  # override weaker reasons

    # NOTE: deliberately NO per-(cond,freq) IQR-outlier protection. On a
    # noise-floor-limited measurement (spurs), the 1.5*IQR fence flags a value at
    # nearly every frequency -- pure noise, not a sweep anomaly -- which would
    # over-protect exactly the measurements that should reduce most. A genuine
    # sweep-level anomaly (a real spike in the mean curve) already produces a
    # large neighbour-reconstruction error and is kept by the redundancy test; a
    # single DUT being off at one frequency is a DUT issue, not a reason to keep
    # that frequency in the SWEEP. Fails + thin margin carry the pass/fail risk.
    return prot


def _recon_err(cond: Cond, retained: np.ndarray, f: float) -> float:
    """|mean(f) - piecewise-linear interp of the mean curve over `retained` at f|,
    for this condition. inf if the condition can't be reconstructed (f not in its
    own grid, or no retained neighbour on a side)."""
    idx = np.searchsorted(cond.freqs, f)
    if idx >= len(cond.freqs) or cond.freqs[idx] != f:
        return 0.0  # this condition has no data at f -> dropping f costs it nothing
    ret = retained[(retained != f)]
    ret = ret[np.isin(ret, cond.freqs)]
    left = ret[ret < f]
    right = ret[ret > f]
    if not len(left) or not len(right):
        return float("inf")
    fl, fr = left[-1], right[0]
    ml = cond.mean[np.searchsorted(cond.freqs, fl)]
    mr = cond.mean[np.searchsorted(cond.freqs, fr)]
    interp = ml + (mr - ml) * (f - fl) / (fr - fl)
    return abs(cond.mean[idx] - interp)


def _eps_for(cond: Cond, args, mu_budget: float) -> float:
    if args.mode == "absolute":
        return float(args.eps)
    if args.mode == "mu":
        return float(args.mu_frac) * mu_budget
    # adaptive (and target uses adaptive costs too): k * own dispersion, MU floor
    eps = float(args.k) * cond.dispersion
    if mu_budget > 0:
        eps = max(eps, float(args.mu_frac) * mu_budget)
    if eps <= 0:
        eps = 1e-9  # degenerate zero-noise condition: only exact-reconstruct drops
    return eps


def reduce_testpoints(conds: list[Cond], grid: np.ndarray,
                      protected: dict[float, str], args, mu_budget: float):
    eps = {c.label: _eps_for(c, args, mu_budget) for c in conds}
    floor = int(args.floor)
    retained = set(float(f) for f in grid)
    prot = set(protected)

    # retained count per condition (only counts frequencies the condition has)
    cond_freqs = {c.label: set(float(f) for f in c.freqs) for c in conds}
    ret_count = {c.label: len(cond_freqs[c.label]) for c in conds}

    def cost(f: float) -> tuple[float, str, float]:
        """(worst normalised error, governing condition, worst raw error)."""
        ret_arr = np.array(sorted(retained), dtype=float)
        worst, who, raw = 0.0, "", 0.0
        for c in conds:
            if f not in cond_freqs[c.label]:
                continue
            e = _recon_err(c, ret_arr, f)
            norm = e / eps[c.label] if eps[c.label] > 0 else (0.0 if e == 0 else float("inf"))
            if norm > worst:
                worst, who, raw = norm, c.label, e
        return worst, who, raw

    dropped: list[tuple[float, str, float]] = []
    removable = sorted(retained - prot)
    n_total = len(grid)
    target_drop = int(round(n_total * float(args.target_pct) / 100.0)) if args.mode == "target" else None

    while True:
        best = None  # (cost, f, who, raw)
        for f in removable:
            if f not in retained:
                continue
            # floor guard: dropping f must not push any condition below the floor
            if any(f in cond_freqs[c.label] and ret_count[c.label] - 1 < floor for c in conds):
                continue
            cst, who, raw = cost(f)
            if best is None or cst < best[0]:
                best = (cst, f, who, raw)
        if best is None:
            break
        cst, f, who, raw = best
        if args.mode == "target":
            if len(dropped) >= target_drop:
                break
            # target mode drops least-important first regardless of eps<1,
            # but still never drops something that fails reconstruction (inf).
            if cst == float("inf"):
                break
        else:
            if cst >= 1.0:
                break  # next-cheapest drop already exceeds tolerance -> stop
        retained.discard(f)
        for c in conds:
            if f in cond_freqs[c.label]:
                ret_count[c.label] -= 1
        dropped.append((f, who, raw))

    return retained, dropped, eps, ret_count, floor


# --- reporting ---------------------------------------------------------------
def _fmt(v: float, d: int = 4) -> str:
    return f"{v:.{d}f}"


def _parse_fragile(items: list[str]) -> list[tuple[str, float]]:
    """--fragile 'SUBSTR' or 'SUBSTR:0.15' -> [(substr_lower, ceiling)]. A
    condition whose label contains SUBSTR is treated as fragile; its reduction is
    capped at the given ceiling (default 0.20). This is the lightweight, per-run
    form of the domain knowledge that some test conditions are inherently more
    fragile than their statistics suggest -- the durable form is a loadable
    per-instrument-family config (same pattern as padb_sites.json / the future
    band-edge config), not built here."""
    out = []
    for it in items:
        if ":" in it:
            s, c = it.rsplit(":", 1)
            try:
                out.append((s.strip().lower(), float(c)))
                continue
            except ValueError:
                pass
        out.append((it.strip().lower(), 0.20))
    return out


def _fragile_ceiling(label: str, frag: list[tuple[str, float]]):
    ll = label.lower()
    hits = [c for s, c in frag if s and s in ll]
    return min(hits) if hits else None


def build_report(csv_path: Path, conds, grid, protected, retained, dropped,
                 eps, ret_count, floor, args, mu_budget) -> tuple[str, str]:
    n = len(grid)
    kept = sorted(retained)
    n_drop = len(dropped)
    pct = (n_drop / n * 100.0) if n else 0.0
    drop_by_reason: dict[str, int] = {}
    prot_kept = [f for f in kept if f in protected]
    reason_of = {f: r for f, r in protected.items()}

    lines: list[str] = []
    lines.append(f"Test-point trim recommendation -- {csv_path.name}")
    lines.append("=" * 68)
    lines.append(f"Swept frequencies:        {n}")
    lines.append(f"Recommend KEEP:           {len(kept)}")
    lines.append(f"Recommend DROP (redundant): {n_drop}   (~{pct:.1f}% fewer sweep points)")
    lines.append(f"Conditions analysed:      {len(conds)}")
    lines.append(f"Criterion mode:           {args.mode}"
                 + (f" (k={args.k})" if args.mode in ("adaptive", "target") else "")
                 + (f" (eps={args.eps})" if args.mode == "absolute" else "")
                 + (f" (mu_frac={args.mu_frac}, MU+dEnv={mu_budget})" if args.mode == "mu" else ""))
    lines.append(f"Per-condition floor:      >= {floor} retained points")
    lines.append("")
    lines.append("Per-condition context (eps = point-to-point change treated as noise):")
    lines.append(f"  {'condition':<40} {'eps':>8} {'disp':>8} {'fail%':>7} "
                 f"{'minMrg':>8} {'meanMrg':>8} {'relVar':>7}")
    for c in conds:
        rv = c.rel_variance
        lines.append(f"  {c.label[:40]:<40} {_fmt(eps[c.label]):>8} {_fmt(c.dispersion):>8} "
                     f"{c.fail_rate * 100:>6.1f}% {_fmt(c.min_margin, 3):>8} "
                     f"{_fmt(c.mean_margin, 3):>8} "
                     + (f"{rv:>7.2f}" if not np.isnan(rv) else f"{'n/a':>7}"))
    lines.append("  (relVar = noise / median headroom: high => marginal/risk-carrying; "
                 "low => comfortably clear)")
    lines.append("")

    # --- Data-driven reduction ceiling (David: derive the % from the parameters;
    #     the riskiest / most fragile condition governs the shared sweep) ---
    frag = _parse_fragile(getattr(args, "fragile", []) or [])
    per_cond_ceiling = []
    for c in conds:
        reason, ceil = c.risk_ceiling(float(args.margin_k))
        fc = _fragile_ceiling(c.label, frag)
        if fc is not None and fc < ceil:
            ceil, reason = fc, "flagged fragile (domain override)"
        per_cond_ceiling.append((c, ceil, reason))
    gov = min(per_cond_ceiling, key=lambda t: t[1])
    ceiling_pct = gov[1] * 100.0
    lines.append("Data-driven reduction recommendation:")
    lines.append(f"  Adaptive analysis safely removes ~{pct:.1f}% now "
                 f"(everything reconstructable within each condition's own noise).")
    lines.append(f"  Recommended reduction CEILING: ~{ceiling_pct:.0f}% "
                 f"-- governed by '{gov[0].label[:40]}' ({gov[2]}).")
    if pct > ceiling_pct + 1e-6:
        lines.append(f"  NOTE: the adaptive result ({pct:.1f}%) exceeds the risk ceiling "
                     f"({ceiling_pct:.0f}%); treat the extra drops with care.")
    else:
        lines.append("  The adaptive result is within the recommended ceiling.")
    lines.append("  Per-condition ceiling (riskiest governs):")
    for c, ceil, reason in sorted(per_cond_ceiling, key=lambda t: t[1]):
        lines.append(f"    {c.label[:44]:<44} <= {ceil * 100:>3.0f}%  ({reason})")
    lines.append("")

    # protection breakdown
    prot_reasons: dict[str, int] = {}
    for f in prot_kept:
        prot_reasons[reason_of[f]] = prot_reasons.get(reason_of[f], 0) + 1
    lines.append("Protected (always kept):")
    for r, cnt in sorted(prot_reasons.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {r:<26} {cnt}")
    lines.append("")

    # dropped detail (cap the printed list)
    lines.append("Dropped frequencies (redundant -- reconstructed from neighbours within eps):")
    lines.append(f"  {'Frequency':>14}  {'max recon err':>13}  governing condition")
    cap = 60
    for f, who, raw in sorted(dropped)[:cap]:
        lines.append(f"  {_fmt(f):>14}  {_fmt(raw):>13}  {who[:44]}")
    if n_drop > cap:
        lines.append(f"  ... and {n_drop - cap} more (see the CSV for the full list)")
    lines.append("")
    lines.append("Retained frequency list (ready to set as the pod's sweep):")
    lines.append("  " + ", ".join(_fmt(f) for f in kept))
    lines.append("")
    lines.append("NOTE: advisory only -- the CSV/pod are never modified. Re-run the pod with")
    lines.append("      the retained list to realise the test-time saving.")

    # per-frequency margin/fail context aggregated across conditions
    def _agg_at(f: float) -> tuple[float, int]:
        mins, fsum = [], 0
        for c in conds:
            if f in c.margin_by_f and not np.isnan(c.margin_by_f[f]):
                mins.append(c.margin_by_f[f])
            fsum += c.fails_by_f.get(f, 0)
        return (min(mins) if mins else float("nan")), fsum

    # CSV: one row per frequency, full traceability + margin/fail context
    csv_rows = ["Frequency,Decision,Reason,Max_Recon_Err,Governing_Condition,Min_Margin,Fail_Count"]
    drop_map = {f: (who, raw) for f, who, raw in dropped}
    for f in sorted(grid):
        f = float(f)
        mn, fc = _agg_at(f)
        mn_s = _fmt(mn) if not np.isnan(mn) else ""
        if f in retained:
            reason = reason_of.get(f, "kept (within floor / not evaluated redundant)")
            csv_rows.append(f"{_fmt(f)},KEEP,{reason},,,{mn_s},{fc}")
        else:
            who, raw = drop_map[f]
            csv_rows.append(f"{_fmt(f)},DROP,redundant,{_fmt(raw)},{who},{mn_s},{fc}")
    return "\n".join(lines) + "\n", "\n".join(csv_rows) + "\n"


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Recommend redundant swept test points to trim (report only).")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--mode", choices=("adaptive", "mu", "absolute", "target"), default="adaptive")
    ap.add_argument("--k", type=float, default=1.0, help="adaptive/target: eps = k * condition dispersion (default 1.0)")
    ap.add_argument("--eps", type=float, default=0.2, help="absolute mode: tolerance in Y units (default 0.2)")
    ap.add_argument("--mu-frac", type=float, default=0.5, help="fraction of the MU+dEnv budget used as the tolerance / floor (default 0.5)")
    ap.add_argument("--mu", type=float, default=0.0, help="measurement uncertainty budget (Y units)")
    ap.add_argument("--denv", type=float, default=0.0, help="environmental drift budget (Y units)")
    ap.add_argument("--target-pct", type=float, default=30.0, help="target mode: aim to drop this %% of points (default 30)")
    ap.add_argument("--floor", type=int, default=5, help="minimum retained points per condition (default 5)")
    ap.add_argument("--margin-k", type=float, default=3.0, help="protect points whose headroom to the limit is <= margin_k * dispersion (near the pass/fail edge); 0 disables (default 3.0)")
    ap.add_argument("--fragile", action="append", default=[], metavar="SUBSTR[:CEIL]", help="mark conditions whose label contains SUBSTR as fragile, capping their reduction at CEIL (default 0.20); repeatable. Durable form is a loadable per-family config (future).")
    ap.add_argument("--x-col", default=None, help="exact swept-x column name if not Frequency/X value")
    ap.add_argument("--out", type=Path, default=None, help="output dir (default: alongside the CSV)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not args.csv.exists():
        print(f"[FAIL] CSV not found: {args.csv}")
        sys.exit(1)
    try:
        df = padb_plots._load_scatter_for_stats(args.csv, x_col=args.x_col)
    except Exception as exc:
        print(f"[FAIL] could not load CSV: {exc}")
        sys.exit(1)
    df = df.dropna(subset=["Frequency_MHz", "Value"])
    if not len(df):
        print("[FAIL] no usable (frequency, value) rows -- see padb_csv_check.py.")
        sys.exit(1)

    conds, grid = _build_conditions(df)
    if len(grid) < 3:
        print(f"[NOTE] only {len(grid)} swept frequency(ies) -- nothing to reduce.")
        sys.exit(0)
    protected = _protected_frequencies(df, grid, conds, float(args.margin_k))
    mu_budget = float(args.mu) + float(args.denv)
    if args.mode == "mu" and mu_budget <= 0:
        print("[FAIL] --mode mu needs --mu (and optionally --denv) > 0.")
        sys.exit(1)

    retained, dropped, eps, ret_count, floor = reduce_testpoints(conds, grid, protected, args, mu_budget)

    # Safety assertions -- a protected point dropped, or a floor violated, is a
    # bug; fail loudly rather than ship a recommendation that loses coverage.
    bad_prot = [f for f in protected if f not in retained]
    bad_floor = [c.label for c in conds if ret_count[c.label] < floor
                 and len(c.freqs) >= floor]
    if bad_prot or bad_floor:
        print(f"[FAIL] internal safety check: dropped protected={bad_prot[:5]} "
              f"floor_violations={bad_floor[:5]}")
        sys.exit(1)

    txt, csv_txt = build_report(args.csv, conds, grid, protected, retained, dropped,
                                eps, ret_count, floor, args, mu_budget)
    out_dir = args.out or args.csv.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.csv.stem
    (out_dir / f"{stem}_testpoint_reduction.txt").write_text(txt, encoding="utf-8")
    (out_dir / f"{stem}_testpoint_reduction.csv").write_text(csv_txt, encoding="utf-8")
    if not args.quiet:
        print(txt)
    print(f"[OK] wrote {stem}_testpoint_reduction.txt / .csv to {out_dir}")
    sys.exit(0)


if __name__ == "__main__":
    main()
