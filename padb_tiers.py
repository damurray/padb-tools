"""padb_tiers.py -- predict what a dataset will cost BEFORE building it, and route heavy views away by default.

v3.0 stage 5 (size tiers). v2.5 only noticed an unrenderable page AFTER building it (`_warn_if_view_too_large`),
and the one rule that avoided a doomed build (a sweep over 300 distinct x -> scatter + summary only) lived in the
padbloader adapter, so a padb parquet / CSV given to `padb_analyze` or a plain job got every view regardless.
This module is the single predictive rule.

WHAT IS PREDICTED -- build TIME, not page size. Measured (2026-11-24, 10 synthetic datasets from 10k to 640k rows,
4 to 128 conditions, 100 to 2000 x; `qa_tiers.CALIBRATION`): every page stayed <= ~20 MB because v2.5's automatic
levers (scatter decimation at density, float32 packing at >= 250k rows, compression at >= 20 MB) flatten size, so
page size is NOT monotonic in rows (scatter: 14.9 MB at 40k rows, 7.4 MB at 160k) and cannot justify routing.
Build time IS linear in two things per view:

        seconds(view) = t0 + a * rows + b * cells,        cells = distinct x  *  distinct conditions

(`cells` = the number of boxes / per-frequency groups: the boxplot and the statistics views do work per cell, not
per row). Least-squares fit over the 10 measurements; worst relative error 38% (scatter), boxplot 10%. Independent
check against the real EP5 build recorded in CLAUDE.md (965k rows, 7,662 x; boxplot 619 s, stat_summary 228 s,
summary 87 s): solving the condition count from the boxplot time (~59) predicts stat_summary 190 s and summary 64 s,
both within 26% of the real run. The coefficients are for THIS machine's class; they are an order-of-magnitude
guide for a budget, not a stopwatch -- the budget is deliberately coarse.

POLICY (a view is "over budget" when its predicted time exceeds `budget_s`, default 300 s):
  * views chosen AUTOMATICALLY: an over-budget view is NOT built, the reason and prediction are recorded and the
    parquet viewer is recommended;
  * views requested EXPLICITLY (job `views`): never dropped -- the prediction is only reported (a warning);
  * tier: 'standard' = nothing over budget; 'heavy' = some over budget, others still built; 'giant' = every
    candidate is over budget -- the single cheapest view is still built so there is something to open, and the
    viewer is the recommended way in;
  * job key `tiering: false` turns the planner off (v2.5 behaviour); `tier_budget_s` sets the budget.
Pure functions, no I/O: `padb_v2` supplies the dataset statistics and records the outcome.
"""
from __future__ import annotations

DEFAULT_BUDGET_S = 300.0

# view -> (t0 seconds, seconds per row, seconds per cell); fitted, see the module docstring
COEF = {
    "boxplot":      (2.26, 3.159e-05, 1.354e-03),
    "stat_summary": (2.61, 1.490e-05, 3.850e-04),
    "summary":      (2.38, 1.010e-05, 1.156e-04),
    "scatter":      (3.10, 1.702e-05, 1.526e-04),
    "reference":    (1.94, 1.879e-05, 5.059e-06),
}
# views with no fitted model are never judged (never dropped): they are not in the measured family
_UNMODELLED = ("distribution", "env_coverage", "histogram")


def predict_seconds(view: str, rows: int, n_x: int, n_cond: int):
    """Predicted build seconds for `view`, or None for a view with no fitted model."""
    c = COEF.get(view)
    if c is None:
        return None
    t0, a, b = c
    return t0 + a * max(0, int(rows)) + b * max(0, int(n_x)) * max(1, int(n_cond))


def plan(rows: int, n_x: int, n_cond: int, views, explicit: bool, budget_s: float = DEFAULT_BUDGET_S) -> dict:
    """-> {'tier', 'budget_s', 'stats', 'predictions': {view: s|None}, 'build': [views], 'skipped': [{view,
    predicted_s, reason}], 'over_budget': [views], 'viewer_recommended': bool, 'notes': [text]}"""
    views = list(views)
    pred = {v: predict_seconds(v, rows, n_x, n_cond) for v in views}
    over = [v for v in views if pred[v] is not None and pred[v] > budget_s]
    stats = {"rows": int(rows), "n_x": int(n_x), "n_cond": int(n_cond), "cells": int(n_x) * max(1, int(n_cond))}
    out = {"tier": "standard", "budget_s": float(budget_s), "stats": stats,
           "predictions": {v: (None if p is None else round(p, 1)) for v, p in pred.items()},
           "build": list(views), "skipped": [], "over_budget": over, "viewer_recommended": False, "notes": []}
    if not over:
        return out
    out["viewer_recommended"] = True
    if explicit:
        out["tier"] = "heavy" if len(over) < len(views) else "giant"
        out["notes"].append("requested views predicted over the %.0f s budget (built anyway, as requested): %s"
                            % (budget_s, ", ".join("%s ~%.0f s" % (v, pred[v]) for v in over)))
        return out
    keep = [v for v in views if v not in over]
    if keep:
        out["tier"] = "heavy"
    else:
        out["tier"] = "giant"
        keep = [min(views, key=lambda v: pred[v])]             # something to open
    out["build"] = keep
    for v in views:
        if v not in keep:
            out["skipped"].append({"view": v, "predicted_s": round(pred[v], 1),
                                   "reason": "predicted %.0f s to build (budget %.0f s) for %s rows x %s x-values x %s "
                                             "conditions" % (pred[v], budget_s, format(stats["rows"], ","),
                                                             format(stats["n_x"], ","), format(stats["n_cond"], ","))})
    out["notes"].append("%s tier: not built by default -- %s. Open the dataset in the parquet viewer, or request a "
                        "view explicitly (job `views`) / raise `tier_budget_s` to build it anyway."
                        % (out["tier"], ", ".join("%s (~%.0f s)" % (s["view"], s["predicted_s"]) for s in out["skipped"])))
    return out


def count_conditions(group_strings, parse_group, serial_like) -> int:
    """Distinct condition combinations in the DISTINCT Group strings of a dataset, ignoring serial-like keys
    (a condition is what is shared by every DUT). `parse_group` / `serial_like` are injected so this stays pure."""
    seen = set()
    for g in group_strings:
        kv = parse_group(g)
        seen.add(tuple(sorted((k, v) for k, v in kv.items() if not serial_like(k))))
    return max(1, len(seen))
