"""padb_names.py -- PADB output-filename normalisation (pure functions, no dependencies).

Lives in the ANALYSIS layer (v3.0 separation, docs/V3_PLAN.md): the plot builder (`padb_v2`) needs these rules to
find the CSV a PADB extraction actually wrote, and the extraction layer (`padb_run`) needs the very same
rules to collect its outputs. Extraction may import analysis; analysis must never import extraction -- so
the shared rules are defined here and `padb_run` imports them (it still re-exports the names, so every
existing caller keeps working).
"""
from __future__ import annotations

import re

# PADB writes output filenames through a Windows-safe filter: characters a filename
# cannot hold (: * ? " < > | / \\) become "_". Real case (2026-11-05): an analytic whose
# OutputFile is "..._GreaterThan_minus90dBm_:LogScale_AMC2" was written as
# "..._GreaterThan_minus90dBm__LogScale_AMC2.csv"; without this the predicted name
# never matched, the analytic was reported "no output at all" and find_csvs fell
# through to a fuzzy glob that attributed ANOTHER analytic's CSV to it.
_WIN_ILLEGAL_RE = re.compile(r'[:*?"<>|/\\]')


def _win_safe(s: str) -> str:
    return _WIN_ILLEGAL_RE.sub("_", s)


def filename_stem_variants(s: str) -> list[str]:
    """
    Return candidate filename stems for a name string.
    PADB replaces spaces with underscores, hyphens kept, and decimal points
    become underscores (e.g. "1.5" -> "1_5" in filenames).
    We try four variants: (hyphen kept | hyphen->_) x (dot kept | dot->_).

    Module-level (not nested in find_csvs) so padb_v2.py can reuse the exact
    same normalization rules when resolving a predicted csv_path that
    doesn't match what PADB actually wrote.
    """
    if not s:
        return []
    p0 = s.replace(" ", "_")                           # spaces->_, hyphens/dots kept
    p1 = p0.replace(".", "_")                          # spaces->_, dots->_
    p2 = p0.replace("-", "_")                          # spaces->_, hyphens->_
    p3 = p2.replace(".", "_")                          # spaces->_, hyphens->_, dots->_
    seen: list[str] = []
    for stem in (p0, p1, p2, p3):
        if stem not in seen:
            seen.append(stem)
    for stem in (p0, p1, p2, p3):          # + PADB's Windows-safe form (":" -> "_" ...)
        w = _win_safe(stem)
        if w not in seen:
            seen.append(w)
    return seen
