"""padb_core -- the v3.0 analysis core (see V3_PLAN.md).

One place for what v2 re-implemented in every view and engine:
  conditions  parse the Group text ONCE into typed condition columns
  filterspec  the single declarative filter state (and the only WHERE builder)
  limits      limits as DATA applied at query time (effective limit + its reason)
  engine      a DuckDB Dataset answering every query

Stage 1 (this package) is proven equivalent to the v2 implementation by qa_core.py before any
consumer depends on it.
"""
__version__ = "3.0.0-dev1"

from .conditions import GroupIndex, parse_group, serial_of, site_of      # noqa: F401
from .filterspec import FilterSpec                                       # noqa: F401
from .limits import LimitTable                                           # noqa: F401
from .engine import Dataset                                              # noqa: F401
