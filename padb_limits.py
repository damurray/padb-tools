r"""padb_limits.py -- editable LIMIT OVERRIDES shared by the parquet viewer and the padb_v2 builds.

A limits file replaces the data's own Upper / Lower Limit for rows whose swept x falls in a
range -- for display (the limit line), for pass/fail, and (via padb_v2) for every view. It never
touches the source CSV/parquet.

File (JSON, e.g. padb_viewer_limits.json next to the data, or job key "limits_file"):

    {"unit": "MHz",
     "limits": [ {"lo": 0.009, "hi": 100, "upper": -76, "lower": null, "note": "dBc spec"},
                 {"lo": 100,   "hi": 350, "upper": -74} ]}

Semantics (ONE definition, used by numpy, DuckDB SQL and the views so they cannot drift):
  * an entry applies to rows with lo <= x <= hi (edges inclusive);
  * `upper` / `lower` are each optional: null/absent = "not overridden" -- the data's own value
    for that SIDE stands (so you can override just the upper limit);
  * entries are sorted by lo (stable); where ranges overlap, or share an edge, the LATER entry
    wins, per side;
  * rows outside every range keep the data's own limits;
  * pass/fail is then the usual one: fail = value above a present upper or below a present lower.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np

MAX_LIMITS = 200
DEFAULT_NAME = "padb_viewer_limits.json"
_SEARCH = (DEFAULT_NAME, "limits.json")


def validate(raw):
    """-> (clean list sorted by lo, None) or (None, human-readable error)."""
    if not isinstance(raw, list):
        return None, "limits must be a list"
    if len(raw) > MAX_LIMITS:
        return None, "at most %d limit ranges" % MAX_LIMITS
    out = []
    for i, e in enumerate(raw, 1):
        if not isinstance(e, dict):
            return None, "range %d: not an object" % i
        if isinstance(e.get("lo"), bool) or isinstance(e.get("hi"), bool):
            return None, "range %d: min and max must be numbers" % i
        try:
            lo, hi = float(e["lo"]), float(e["hi"])
        except (KeyError, TypeError, ValueError):
            return None, "range %d: min and max must be numbers" % i
        if not (math.isfinite(lo) and math.isfinite(hi)):
            return None, "range %d: min and max must be finite" % i
        if lo > hi:
            return None, "range %d: min must not be above max" % i
        vals = {}
        for key in ("upper", "lower"):
            v = e.get(key)
            if v is None or (isinstance(v, str) and not v.strip()):
                vals[key] = None
                continue
            if isinstance(v, bool):
                return None, "range %d: %s must be a number or blank" % (i, key)
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None, "range %d: %s must be a number or blank" % (i, key)
            if not math.isfinite(f):
                return None, "range %d: %s must be finite" % (i, key)
            vals[key] = f
        if vals["upper"] is None and vals["lower"] is None:
            return None, "range %d: give an upper and/or a lower limit" % i
        if vals["upper"] is not None and vals["lower"] is not None and vals["lower"] > vals["upper"]:
            return None, "range %d: lower must not be above upper" % i
        item = {"lo": lo, "hi": hi, "upper": vals["upper"], "lower": vals["lower"]}
        note = str(e.get("note", "") or "").strip()
        if note:
            item["note"] = note[:120]
        out.append(item)
    out.sort(key=lambda d: d["lo"])        # stable: equal lo keeps the user's order
    return out, None


def _scale(file_unit: str, data_unit: str) -> float:
    try:
        import padb_bands
        return padb_bands.unit_scale(file_unit, data_unit)
    except Exception:  # noqa: BLE001
        return 1.0


def load_file(path, data_unit: str = ""):
    """Read a limits file, converting its x-range from the file's `unit` to `data_unit`.
    -> (limits, error).  A missing file is ([], None); a bad one is ([], message). Never raises."""
    p = Path(path)
    if not p.is_file():
        return [], None
    try:
        cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [], "could not read %s: %s" % (p.name, exc)
    raw = cfg.get("limits") if isinstance(cfg, dict) else cfg
    clean, err = validate(raw)
    if err:
        return [], "%s: %s" % (p.name, err)
    sc = _scale(str(cfg.get("unit", "")) if isinstance(cfg, dict) else "", data_unit)
    if sc != 1.0:
        for e in clean:
            e["lo"] *= sc
            e["hi"] *= sc
    return clean, None


def find_file(search_dirs):
    for d in search_dirs:
        for n in _SEARCH:
            p = Path(d) / n
            if p.is_file():
                return p
    return None


def save_file(path, limits, unit: str = "") -> None:
    """Atomic write (temp + replace). Raises OSError on failure."""
    p = Path(path)
    tmp = Path(str(p) + ".part")
    tmp.write_text(json.dumps({"unit": unit or "", "limits": limits}, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ---- the ONE semantic definition, three renderings --------------------------------------
def override_arrays(x, limits):
    """-> (hi_val, hi_mask, lo_val, lo_mask) numpy arrays over x. Later entry wins, per side."""
    x = np.asarray(x, dtype=float)
    hi_val = np.full(x.shape, np.nan)
    lo_val = np.full(x.shape, np.nan)
    hi_mask = np.zeros(x.shape, dtype=bool)
    lo_mask = np.zeros(x.shape, dtype=bool)
    for e in limits or []:
        m = (x >= e["lo"]) & (x <= e["hi"])
        if e.get("upper") is not None:
            hi_val[m] = e["upper"]
            hi_mask |= m
        if e.get("lower") is not None:
            lo_val[m] = e["lower"]
            lo_mask |= m
    return hi_val, hi_mask, lo_val, lo_mask


def effective(x, hi0, lo0, limits):
    """Effective (upper, lower) arrays: overrides where they apply, else the data's own."""
    hv, hm, lv, lm = override_arrays(x, limits)
    hi = np.where(hm, hv, np.asarray(hi0, dtype=float))
    lo = np.where(lm, lv, np.asarray(lo0, dtype=float))
    return hi, lo


def sql_case(limits, xcol: str, key: str, base: str) -> str:
    """SQL mirror of `effective` for one side (key 'upper' | 'lower'); later entry wins."""
    parts = []
    for e in reversed(limits or []):
        v = e.get(key)
        if v is None:
            continue
        parts.append("WHEN %s >= %r AND %s <= %r THEN %r" % (xcol, float(e["lo"]), xcol, float(e["hi"]), float(v)))
    if not parts:
        return base
    return "CASE " + " ".join(parts) + " ELSE " + base + " END"


def resolve_from_cfg(cfg):
    """The override a plot job asks for: inline `limit_overrides` (already in the data's x unit), else
    `limits_file` (relative to the job file via cfg["_job_dir"]; its unit converted to cfg["x_unit"]).
    -> (limits, source label, error). No key -> ([], "", None)."""
    cfg = cfg or {}
    if cfg.get("limit_overrides"):
        lims, err = validate(cfg["limit_overrides"])
        return (lims or []), "inline overrides", err
    if cfg.get("limits_file"):
        lf = Path(cfg["limits_file"])
        if not lf.is_absolute():
            lf = Path(cfg.get("_job_dir") or ".") / lf
        if not lf.is_file():
            return [], lf.name, "limits_file not found: %s" % lf
        lims, err = load_file(lf, cfg.get("x_unit", "MHz"))
        return lims, lf.name, err
    return [], "", None


def apply_from_cfg(df, cfg, xcol: str = "Frequency_MHz") -> int:
    """Apply a job's override to `df` in place (silently; padb_v2 owns the log line / index note).
    Safe to call twice (idempotent). Used by builders that RELOAD the data from a temporary CSV -- that
    round trip carries only Upper/Lower Limit, so the spec / uncertainty columns come back from the Group
    text and must be overridden again. -> rows overridden."""
    lims, _src, err = resolve_from_cfg(cfg)
    if err or not lims:
        return 0
    return apply_df(df, lims, xcol)


def apply_df(df, limits, xcol: str = "Frequency_MHz") -> int:
    """Apply overrides IN PLACE to a padb_v2 scatter frame: Upper_Limit/Spec_Hi (and
    Lower_Limit/Spec_Lo) where they apply, uncertainty zeroed on those rows so the effective
    limit the views classify against IS the override. Creates the columns if the data had none.
    -> number of rows overridden."""
    if not limits or xcol not in df.columns or len(df) == 0:
        return 0
    hv, hm, lv, lm = override_arrays(df[xcol].to_numpy(dtype=float, na_value=np.nan), limits)
    for cols, val, mask, unc in ((("Upper_Limit", "Spec_Hi"), hv, hm, "Unc_Hi"),
                                 (("Lower_Limit", "Spec_Lo"), lv, lm, "Unc_Lo")):
        if not mask.any():
            continue
        for c in cols:
            base = df[c].to_numpy(dtype=float, na_value=np.nan) if c in df.columns \
                else np.full(len(df), np.nan)
            df[c] = np.where(mask, val, base)
        if unc in df.columns:
            ub = df[unc].to_numpy(dtype=float, na_value=np.nan)
            df[unc] = np.where(mask, 0.0, ub)
    return int((hm | lm).sum())
