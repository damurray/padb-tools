"""Conditions: parse the Group text ONCE into typed columns.

v2 re-parsed the `Key: value  Key: value` Group text in several places (padb_plots._parse_group_kv /
_parse_group_fields, the viewer's _parse_group_dims, the stat_summary inline JS parser) with slightly
different rules -- the source of the padding-space bug, dropped keys and blank-condition surprises.

v3: `parse_group` is the single canonical parser (identical to the HTML loader's `_parse_group_kv`,
because the filters and verdicts in the shipped views use that one) and `GroupIndex` turns the DISTINCT
Group strings of a dataset into site / serial / condition dimensions in one pass. Everything downstream
(filters, group-by, hover text, limits keyed by condition) reads those columns; nothing re-parses text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_DIM_VALUES = 40          # a condition with more distinct values is not a usable filter
_SITE_RE = re.compile(r"Site:\s*([^\s]+)")
_SERIAL_LABELLED = re.compile(r"Serial\s*(?:Number|No|Num)?\s*:\s*([^\s|]+)")
_SERIAL_BARE = re.compile(r"\b([A-Za-z]{2,3}\d{5,})\b")
_SERIAL_PAT = re.compile(r"^[A-Z]{2,3}\d{5,}$")


def parse_group(group_str) -> dict:
    """Group text -> {key: value}. Fragments split on 2+ spaces. PADB right-pads some values to a
    fixed column width ("Frequency (MHz):  10"), which splits a key from its value; a colon-less
    fragment is never a standalone key or value, so it is merged back into the previous part. A
    fragment is `key: value` with a NON-EMPTY value (an empty value is dropped). If nothing parses,
    falls back to single-word `key: value` pairs. Identical to padb_plots._parse_group_kv."""
    s = str(group_str).strip()
    raw_parts = re.split(r"  +", s)
    parts: list = []
    for part in raw_parts:
        if ":" not in part and parts:
            parts[-1] = parts[-1] + " " + part
        else:
            parts.append(part)
    result: dict = {}
    for part in parts:
        m = re.match(r"(.+?):\s*(.+?)\s*$", part.strip())
        if m:
            result[m.group(1).strip()] = m.group(2).strip()
    if not result:
        for k, v in re.findall(r"(\w+):\s*(\S+)", s):
            result[k] = v
    return result


def site_of(group_str) -> str:
    """The `Site: <name>` tag a cross-site compare writes into Group, else '(all)'."""
    m = _SITE_RE.search(str(group_str))
    return m.group(1) if m else "(all)"


def serial_of(group_str, bare_fallback: bool = False):
    """Serial from a labelled `Serial Number: X` fragment (None if absent). With bare_fallback,
    a bare serial-like token (e.g. US65080001) is accepted -- the viewer only turns that on when NO
    group in the dataset carries a labelled serial."""
    m = _SERIAL_LABELLED.search(str(group_str))
    if m:
        return m.group(1)
    if bare_fallback:
        m = _SERIAL_BARE.search(str(group_str))
        return m.group(1) if m else None
    return None


@dataclass
class GroupIndex:
    """The DISTINCT Group strings of a dataset, parsed once.

    groups[i]        the i-th distinct Group text (gid == i)
    sites[i]         site name ('(all)' when untagged)
    serials[i]       serial ('?' when none)
    dims             [{'id','name','values'}] -- the usable condition dimensions
    values[i][d.id]  the group's value for dimension d (None = absent = not applicable)
    """
    groups: list
    sites: list
    serials: list
    dims: list = field(default_factory=list)
    values: list = field(default_factory=list)

    @classmethod
    def build(cls, groups, x_col: str = "", max_values: int = MAX_DIM_VALUES) -> "GroupIndex":
        groups = list(groups)
        sites = [site_of(g) for g in groups]
        ser1 = [serial_of(g) for g in groups]
        if all(s is None for s in ser1):
            ser1 = [serial_of(g, bare_fallback=True) for g in groups]
        serials = [s if s is not None else "?" for s in ser1]
        kvs = [parse_group(g) for g in groups]
        seen: dict = {}
        for kv in kvs:
            for k, v in kv.items():
                seen.setdefault(k, set()).add(v)
        xb = (x_col or "").lower().split("(")[0].strip()
        dims = []
        for k in sorted(seen):
            kl = k.lower()
            vs = seen[k]
            if (kl == "site" or "temp" in kl or "frequency" in kl
                    or (xb and kl.split("(")[0].strip() == xb)
                    or any(t in kl for t in ("serial", "unit id", "dut id", "s/n"))
                    or len(vs) < 2 or len(vs) > max_values
                    or sum(1 for x in vs if _SERIAL_PAT.match(x)) * 2 > len(vs)):
                continue
            dims.append({"id": len(dims), "name": k, "values": sorted(vs)})
        values = [{d["id"]: kv.get(d["name"]) for d in dims} for kv in kvs]
        return cls(groups=groups, sites=sites, serials=serials, dims=dims, values=values)

    def frame(self):
        """pandas frame to register in DuckDB: g, gid, g_site, g_serial, d0..dN."""
        import pandas as pd
        cols = {"g": self.groups, "gid": list(range(len(self.groups))),
                "g_site": self.sites, "g_serial": self.serials}
        for d in self.dims:
            cols["d%d" % d["id"]] = [v[d["id"]] for v in self.values]
        return pd.DataFrame(cols)

    def hover(self, gid: int) -> str:
        """The condition text shown on hover / in tables for one group."""
        return re.sub(r"\s{2,}", " | ", str(self.groups[gid]).strip())
