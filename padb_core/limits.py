"""Limits as DATA, applied at query time.

v2 baked limits into each page (and, in the viewer, UPDATEd the derived table in place), so an override
was easy in one place and hard in the others. v3: a limits table (the same ranges padb_limits already
defines -- one semantic definition) becomes a SQL **view** over the base table. Changing or clearing the
overrides is `CREATE OR REPLACE VIEW`: no row rewrites, instantly and exactly reversible, and every
consumer sees the same *effective* limit.

Each row of the view carries:
  yhi / ylo     the EFFECTIVE upper / lower limit (override where one applies, else the data's own)
  yhi0 / ylo0   the data's own limits (never modified)
  limit_src     WHY: 'override' when any override applies at this x, else 'data'
  scored/fail   has a limit / crosses a present limit
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:                       # padb_limits lives at the repo root
    sys.path.insert(0, _HERE)
import padb_limits                              # noqa: E402


class LimitTable:
    """A validated, sorted list of override ranges ({lo,hi,upper,lower,note})."""

    def __init__(self, entries=None):
        clean, err = padb_limits.validate(list(entries or []))
        if err:
            raise ValueError(err)
        self.entries = clean

    def __bool__(self):
        return bool(self.entries)

    def __len__(self):
        return len(self.entries)

    def to_list(self):
        return [dict(e) for e in self.entries]

    # ---- SQL renderings ------------------------------------------------------------------
    def expr(self, side: str, x: str = "x", base: str = "yhi0") -> str:
        """Effective limit for one side ('upper' | 'lower') as a SQL expression."""
        return padb_limits.sql_case(self.entries, x, side, base)

    def reason_expr(self, x: str = "x") -> str:
        """'override' where any override range (with an upper or lower) covers x, else 'data'."""
        parts = ["(%s >= %r AND %s <= %r)" % (x, float(e["lo"]), x, float(e["hi"]))
                 for e in self.entries if e.get("upper") is not None or e.get("lower") is not None]
        if not parts:
            return "'data'"
        return "CASE WHEN " + " OR ".join(parts) + " THEN 'override' ELSE 'data' END"

    def view_sql(self, base: str = "pts0", name: str = "pts", extra_cols: str = "") -> str:
        """CREATE OR REPLACE VIEW `name` over `base` (which must have x, y, yhi0, ylo0)."""
        hi, lo = self.expr("upper", "x", "yhi0"), self.expr("lower", "x", "ylo0")
        return (
            f"CREATE OR REPLACE VIEW {name} AS SELECT b.*{extra_cols}, "
            f"{hi} AS yhi, {lo} AS ylo, {self.reason_expr('b.x')} AS limit_src, "
            f"(({hi}) IS NOT NULL OR ({lo}) IS NOT NULL) AS scored, "
            f"COALESCE((({hi}) IS NOT NULL AND y > ({hi})) OR (({lo}) IS NOT NULL AND y < ({lo})), false) AS fail "
            f"FROM {base} b")
