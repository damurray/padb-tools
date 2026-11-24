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

from .filterconfig import FilterConfig

MAX_DIM_VALUES = 40          # AUTOMATIC rule: a condition with more distinct values is not offered by default
EXPOSE_MAX_VALUES = 500      # ceiling: a condition with more values than this can never be a filter
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


def classify_key(name: str, values, xb: str = "", max_values: int = MAX_DIM_VALUES):
    """-> (exposable, auto, reason). `exposable` False = the key has its own dedicated filter (site,
    temperature, the x-axis) or too many values to be a filter at all; `auto` = the automatic rules would
    offer it; `reason` says why not (shown in the Edit filters panel)."""
    kl = name.lower()
    if kl == "site" or "temp" in kl:
        return False, False, "has its own dedicated filter"
    if "frequency" in kl or (xb and kl.split("(")[0].strip() == xb):
        return False, False, "is the x-axis"
    n = len(values)
    if n > EXPOSE_MAX_VALUES:
        return False, False, "%d values (more than %d)" % (n, EXPOSE_MAX_VALUES)
    if any(t_ in kl for t_ in ("serial", "unit id", "dut id", "s/n")) \
            or sum(1 for x in values if _SERIAL_PAT.match(x)) * 2 > n:
        return True, False, "serial-like"
    if n < 2:
        return True, False, "single value"
    if n > max_values:
        return True, False, "%d values (more than %d)" % (n, max_values)
    return True, True, ""


@dataclass
class GroupIndex:
    """The DISTINCT Group strings of a dataset, parsed once.

    groups[i]        the i-th distinct Group text (gid == i)
    sites[i]         site name ('(all)' when untagged)
    serials[i]       serial ('?' when none)
    registry         EVERY filterable condition key with a STABLE id: [{'id','name','n_values','auto','reason',
                     'values'}]. Ids are assigned from the automatic rules only (auto keys by name first --
                     exactly the v2.5 ids -- then the others by name), never from a FilterConfig, so changing
                     what is exposed never renumbers anything and saved selections stay valid.
    unavailable      keys that can never be filters here: [{'name','reason'}]
    dims             the EXPOSED conditions per `config`: [{'id','name','values'[, 'label']}] in display order
    values[i][id]    the group's value for registry key `id` (None = absent = not applicable)
    """
    groups: list
    sites: list
    serials: list
    registry: list = field(default_factory=list)
    unavailable: list = field(default_factory=list)
    dims: list = field(default_factory=list)
    values: list = field(default_factory=list)
    config: FilterConfig = field(default_factory=FilterConfig)

    @classmethod
    def build(cls, groups, x_col: str = "", max_values: int = MAX_DIM_VALUES, config=None) -> "GroupIndex":
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
        autos, extras, unavailable = [], [], []
        for k in sorted(seen):
            ok, auto, why = classify_key(k, seen[k], xb, max_values)
            if not ok:
                unavailable.append({"name": k, "reason": why})
            elif auto:
                autos.append((k, why))
            else:
                extras.append((k, why))
        registry = []
        for k, why in autos + extras:
            registry.append({"id": len(registry), "name": k, "n_values": len(seen[k]), "auto": not why,
                             "reason": why, "values": sorted(seen[k])})
        values = [{r["id"]: kv.get(r["name"]) for r in registry} for kv in kvs]
        idx = cls(groups=groups, sites=sites, serials=serials, registry=registry, unavailable=unavailable,
                  values=values)
        idx.apply_config(config or FilterConfig())
        return idx

    def apply_config(self, config: FilterConfig):
        """Choose which registry keys are exposed (display order + labels). Metadata only -- the per-key
        columns exist for every registry key, so this is instant and needs no data reload.
        -> the names the config mentions that this dataset does not have."""
        chosen, unknown = config.resolve(self.registry)
        dims = []
        for r in chosen:
            d = {"id": r["id"], "name": r["name"], "values": list(r["values"])}
            lab = config.labels.get(r["name"])
            if lab:
                d["label"] = lab
            dims.append(d)
        self.dims = dims
        self.config = config
        return unknown

    def exposed_ids(self):
        return [d["id"] for d in self.dims]

    def frame(self):
        """pandas frame to register in DuckDB: g, gid, g_site, g_serial, d<id> for EVERY registry key."""
        import pandas as pd
        cols = {"g": self.groups, "gid": list(range(len(self.groups))),
                "g_site": self.sites, "g_serial": self.serials}
        for r in self.registry:
            cols["d%d" % r["id"]] = [v[r["id"]] for v in self.values]
        return pd.DataFrame(cols)

    def hover(self, gid: int) -> str:
        """The condition text shown on hover / in tables for one group."""
        return re.sub(r"\s{2,}", " | ", str(self.groups[gid]).strip())
