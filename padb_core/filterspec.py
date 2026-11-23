"""FilterSpec: the ONE declarative filter state (and the only place a WHERE clause is built).

v2 had a lock bar, a Global Filter, segment bands and per-view checkboxes -- each with its own
storage and apply path. v3: a single JSON-able spec that every view reads and writes, that the
server turns into SQL in exactly one function, and whose canonical key is the cache key.

Semantics (identical to the v2 viewer's `_where`, proven by qa_core):
  * window: lo <= x <= hi, both edges inclusive
  * sites / temps: a non-empty list restricts to those values; empty = no restriction
  * pf: 'all' | 'pass' | 'fail'   (fail = a present limit is crossed; pass = everything else)
  * conds: {dim_id: [allowed values]} -- ONLY narrowed dimensions are listed. An empty list means every
    value is unticked -> nothing. A row whose value for the dimension is blank/absent is "not
    applicable" and is never excluded.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field


@dataclass
class FilterSpec:
    lo: float = -math.inf
    hi: float = math.inf
    sites: list = field(default_factory=list)
    temps: list = field(default_factory=list)
    pf: str = "all"
    conds: dict = field(default_factory=dict)

    # ---- construction / validation -------------------------------------------------------
    @classmethod
    def from_dict(cls, d) -> "FilterSpec":
        d = d or {}
        conds = {}
        for k, v in (d.get("conds") or {}).items():
            conds[int(k)] = [str(x) for x in (v or [])]
        spec = cls(
            lo=float(d["lo"]) if d.get("lo") is not None else -math.inf,
            hi=float(d["hi"]) if d.get("hi") is not None else math.inf,
            sites=[str(x) for x in (d.get("sites") or [])],
            temps=[str(x) for x in (d.get("temps") or [])],
            pf=str(d.get("pf") or "all"),
            conds=conds)
        spec.validate()
        return spec

    @classmethod
    def from_json(cls, text: str) -> "FilterSpec":
        return cls.from_dict(json.loads(text))

    def validate(self) -> None:
        if math.isnan(self.lo) or math.isnan(self.hi):
            raise ValueError("window must not be NaN")
        if self.lo > self.hi:
            raise ValueError("window: lo must not be above hi")
        if self.pf not in ("all", "pass", "fail"):
            raise ValueError("pf must be 'all', 'pass' or 'fail'")

    # ---- serialisation -------------------------------------------------------------------
    def to_dict(self) -> dict:
        d: dict = {}
        if math.isfinite(self.lo):
            d["lo"] = self.lo
        if math.isfinite(self.hi):
            d["hi"] = self.hi
        if self.sites:
            d["sites"] = list(self.sites)
        if self.temps:
            d["temps"] = list(self.temps)
        if self.pf != "all":
            d["pf"] = self.pf
        if self.conds:
            d["conds"] = {str(k): list(v) for k, v in sorted(self.conds.items())}
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    def key(self) -> str:
        """Canonical string -- equal specs give equal keys (use as a cache key). Order of the
        lists is irrelevant to the meaning, so it is normalised away."""
        d = self.to_dict()
        for k in ("sites", "temps"):
            if k in d:
                d[k] = sorted(d[k])
        if "conds" in d:
            d["conds"] = {k: sorted(v) for k, v in d["conds"].items()}
        return json.dumps(d, sort_keys=True)

    # ---- the ONE WHERE builder -----------------------------------------------------------
    def to_sql(self, dim_ids, flo=None, fhi=None):
        """-> (where_sql, args). `dim_ids` = the dataset's condition-dimension ids; a cond key that is
        not one of them is ignored. `flo`/`fhi` override the window (v2 callers pass floats)."""
        lo = self.lo if flo is None else flo
        hi = self.hi if fhi is None else fhi
        where, args = ["x >= ?", "x <= ?"], [lo, hi]
        if self.sites:
            where.append("site IN (" + ",".join("?" * len(self.sites)) + ")")
            args += list(self.sites)
        if self.temps:
            where.append("temp IN (" + ",".join("?" * len(self.temps)) + ")")
            args += list(self.temps)
        if self.pf == "fail":
            where.append("fail")
        elif self.pf == "pass":
            where.append("NOT fail")
        ok = set(dim_ids)
        for k, vals in self.conds.items():
            k = int(k)
            if k not in ok:
                continue
            if not vals:
                where.append("false")          # every value of this dim unticked -> nothing
                continue
            # blank/absent value for a dim = not applicable = no constraint (never excluded)
            where.append(f"(d{k} IS NULL OR d{k} IN (" + ",".join("?" * len(vals)) + "))")
            args += list(vals)
        return " AND ".join(where), args
