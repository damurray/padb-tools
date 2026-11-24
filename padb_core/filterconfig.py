"""FilterConfig: which condition dimensions are exposed as filters (v3.0 stage 3b).

The Group text of a dataset carries many `Key: value` conditions. v2 picked the filterable ones by fixed
rules (2..40 values, not serial-like, ...) and nobody could change that. v3: the rules still give the
DEFAULT set, and a small per-dataset config -- a JSON file beside the data, edited live in the viewer --
decides what is actually exposed:

  show    None  -> the automatic set (everything the rules accept), minus `hide`
          [..]  -> exactly these keys, in this order. Keys the rules would drop (single-valued, serial-like,
                   more than 40 values) can be listed: "expose anyway"
  hide    keys removed from the automatic set (ignored when `show` is given -- it already says what to show)
  labels  {key: display name} -- cosmetic; the KEY stays the identity used by saved filters and files

A condition that is not exposed puts NO restriction on the data ("all"): hiding a narrowed filter clears its
selection (a hidden filter that silently still narrows is a trap). The site, temperature and x-axis keys keep
their own dedicated filters and can never be exposed here.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_NAME = "padb_viewer_filters.json"
_SEARCH = (DEFAULT_NAME, "filters.json")
MAX_LABEL = 60


@dataclass
class FilterConfig:
    show: object = None                      # None | list[str]
    hide: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d) -> "FilterConfig":
        if d is None:
            return cls()
        if not isinstance(d, dict):
            raise ValueError("the filter configuration must be a JSON object")
        unknown = set(d) - {"show", "hide", "labels", "schema"}
        if unknown:
            raise ValueError("unknown field(s): %s" % ", ".join(sorted(unknown)))
        show = d.get("show")
        if show is not None:
            if not isinstance(show, list) or not all(isinstance(k, str) and k for k in show):
                raise ValueError("`show` must be null or a list of condition names")
            if len(set(show)) != len(show):
                raise ValueError("`show` lists a condition twice")
            show = list(show)
        hide = d.get("hide") or []
        if not isinstance(hide, list) or not all(isinstance(k, str) and k for k in hide):
            raise ValueError("`hide` must be a list of condition names")
        labels = d.get("labels") or {}
        if not isinstance(labels, dict):
            raise ValueError("`labels` must be an object {condition: label}")
        clean = {}
        for k, v in labels.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("`labels` must map condition names to text")
            v = v.strip()
            if len(v) > MAX_LABEL:
                raise ValueError("label for %r is longer than %d characters" % (k, MAX_LABEL))
            if v and v != k:
                clean[k] = v
        return cls(show=show, hide=list(dict.fromkeys(hide)), labels=clean)

    def to_dict(self) -> dict:
        d: dict = {}
        if self.show is not None:
            d["show"] = list(self.show)
        if self.hide:
            d["hide"] = list(self.hide)
        if self.labels:
            d["labels"] = dict(self.labels)
        return d

    def is_default(self) -> bool:
        return self.show is None and not self.hide and not self.labels

    def resolve(self, registry):
        """registry = [{'id','name','auto',...}] (every exposable key, stable ids).
        -> (exposed registry entries in display order, [unknown names listed in show/hide/labels])."""
        by_name = {r["name"]: r for r in registry}
        unknown = []
        if self.show is None:
            hidden = set(self.hide)
            chosen = [r for r in registry if r["auto"] and r["name"] not in hidden]
        else:
            chosen = []
            for k in self.show:
                if k in by_name:
                    chosen.append(by_name[k])
                else:
                    unknown.append(k)
        for k in list(self.hide) + list(self.labels):
            if k not in by_name and k not in unknown:
                unknown.append(k)
        return chosen, unknown


def find_file(search_dirs):
    for d in search_dirs:
        for n in _SEARCH:
            p = Path(d) / n
            if p.is_file():
                return p
    return None


def load_file(path):
    """-> (FilterConfig, error). A missing file is (default, None); a bad one is (default, message)."""
    p = Path(path)
    if not p.is_file():
        return FilterConfig(), None
    try:
        return FilterConfig.from_dict(json.loads(p.read_text(encoding="utf-8-sig"))), None
    except (OSError, ValueError) as exc:
        return FilterConfig(), "%s: %s" % (p.name, exc)


def save_file(path, config: FilterConfig) -> None:
    """Atomic write (temp + replace). Raises OSError on failure."""
    p = Path(path)
    tmp = Path(str(p) + ".part")
    tmp.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, p)
