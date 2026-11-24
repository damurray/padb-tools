"""One filter file across tools: FilterSpec <-> the HTML pages' "lock" JSON.

The self-contained HTML views already let you save the analysis filters as a *lock* (and export / import
it as a file): `{v, ts, title, dims:{'<label>':[values]}, freq:{lo,hi}, passfail:'all'|'passing'|'failing'}`,
matched by dimension LABEL, best-effort. v3 makes that the interchange format between the HTML pages and the
viewer, implemented ONCE here (the viewer's endpoints call it; nothing re-implements the mapping in JS):

  to_lock(spec, ctx)    -> the lock dict an HTML page can import (only genuinely NARROWED dims are exported,
                           exactly like the pages' own `_avLockRead`: a fully-open dim never constrains
                           another view)
  from_lock(lock, ctx)  -> (FilterSpec, applied, skipped): best-effort -- a label or value that does not exist
                           in this dataset is SKIPPED AND REPORTED, never empties the view

Mapping: condition dimension name <-> dims[name]; sites <-> dims['Site']; temperatures <-> dims['Temperature']
(aliases 'Temperature Step' / 'Test Step' / 'Temp' accepted on import); window <-> freq {lo,hi}; pass/fail <->
passfail ('pass'<->'passing', 'fail'<->'failing'). Serial-like labels cannot be applied by the viewer (serial
is a Group-by key there, not a filter) and are reported as skipped.

`ctx` = {'dims': [{'id','name','values'}], 'sites': [...], 'temps': [...], 'x_min': float, 'x_max': float}
"""
from __future__ import annotations

import math
import re
import time

from .filterspec import FilterSpec

LOCK_VERSION = 1
_TEMP_LABELS = {"temperature", "temperature step", "test step", "temp"}
_SERIAL_RE = re.compile(r"serial|unit id|dut id|s/n")
_PF_OUT = {"pass": "passing", "fail": "failing", "all": "all"}
_PF_IN = {"passing": "pass", "failing": "fail", "all": "all"}


def _narrowed(vals, universe) -> bool:
    return bool(vals) and len(set(map(str, vals)) & set(map(str, universe))) < len(set(map(str, universe)))


def to_lock(spec: FilterSpec, ctx: dict, title: str = "") -> dict:
    dims = {}
    by_id = {d["id"]: d for d in ctx.get("dims", [])}
    for k, vals in spec.conds.items():
        d = by_id.get(int(k))
        if d and _narrowed(vals, d["values"]):
            dims[d["name"]] = [str(v) for v in vals]
    if _narrowed(spec.sites, ctx.get("sites", [])):
        dims["Site"] = list(spec.sites)
    if _narrowed(spec.temps, ctx.get("temps", [])):
        dims["Temperature"] = list(spec.temps)
    lock = {"v": LOCK_VERSION, "ts": int(time.time() * 1000), "title": title, "dims": dims}
    x0, x1 = ctx.get("x_min"), ctx.get("x_max")
    lo = spec.lo if math.isfinite(spec.lo) else None
    hi = spec.hi if math.isfinite(spec.hi) else None
    if (lo is not None and x0 is not None and lo > x0) or (hi is not None and x1 is not None and hi < x1):
        lock["freq"] = {"lo": lo if lo is not None else x0, "hi": hi if hi is not None else x1}
    lock["passfail"] = _PF_OUT.get(spec.pf, "all")
    return lock


def from_lock(lock, ctx: dict):
    """-> (FilterSpec, applied: [str], skipped: [(label, reason)])."""
    if not isinstance(lock, dict):
        raise ValueError("a locked-filters file is a JSON object")
    raw_dims = lock.get("dims") or {}
    if not isinstance(raw_dims, dict):
        raise ValueError("`dims` must be an object {label: [values]}")
    applied, skipped = [], []
    dims = ctx.get("dims", [])
    by_name = {d["name"]: d for d in dims}
    by_lower = {d["name"].lower(): d for d in dims}
    spec = FilterSpec()

    def pick(vals, universe):
        want = {str(v) for v in (vals or [])}
        return [u for u in universe if str(u) in want]

    for label, vals in raw_dims.items():
        low = str(label).lower()
        if low == "site":
            got = pick(vals, ctx.get("sites", []))
            if got:
                spec.sites = got
                applied.append(label)
            else:
                skipped.append((label, "none of its values exist in this dataset"))
        elif low in _TEMP_LABELS:
            got = pick(vals, ctx.get("temps", []))
            if got:
                spec.temps = got
                applied.append(label)
            else:
                skipped.append((label, "none of its values exist in this dataset"))
        elif _SERIAL_RE.search(low):
            skipped.append((label, "serial filters are not condition filters in the viewer"))
        else:
            d = by_name.get(label) or by_lower.get(low)
            if not d:
                skipped.append((label, "hidden by this dataset's filter configuration"
                                if label in set(ctx.get("hidden", [])) or low in {h.lower() for h in ctx.get("hidden", [])}
                                else "no such condition in this dataset"))
                continue
            got = pick(vals, d["values"])
            if got:
                spec.conds[d["id"]] = got
                applied.append(label)
            else:
                skipped.append((label, "none of its values exist in this dataset"))
    fr = lock.get("freq")
    if isinstance(fr, dict):
        try:
            lo, hi = float(fr.get("lo")), float(fr.get("hi"))
            x0, x1 = ctx.get("x_min"), ctx.get("x_max")
            if x0 is not None:
                lo = max(lo, x0)
            if x1 is not None:
                hi = min(hi, x1)
            if math.isfinite(lo) and math.isfinite(hi) and lo <= hi:
                spec.lo, spec.hi = lo, hi
                applied.append("freq")
            else:
                skipped.append(("freq", "the locked range lies outside this dataset"))
        except (TypeError, ValueError):
            skipped.append(("freq", "not a numeric range"))
    pf = lock.get("passfail")
    if pf is not None:
        if pf in _PF_IN:
            spec.pf = _PF_IN[pf]
            if pf != "all":
                applied.append("passfail")
        else:
            skipped.append(("passfail", "unknown value %r" % (pf,)))
    spec.validate()
    return spec, applied, skipped
