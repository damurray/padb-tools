"""padb_bands.py -- named frequency-band config for the segment stepper.

A "band file" just partitions the swept x-axis into named chunks -- it does NOT
have to be RF-frequency specific; any swept x (Vgg, rate, offset, ...) works. It
is shared by:
  * padb_viewer.py  -- the overview's "Segment step -> Named bands" mode
  * padb_v2/padb_plots -- the HTML views' "Segment by: Named bands" mode

File shape (JSON):
    {"unit": "Hz",
     "bands": [{"name": "DAC Band", "lo": 9000, "hi": 8e6}, ...]}
`lo`/`hi` are expressed in `unit`; loaders convert them to the data's own x-axis
unit. `unit` may be omitted, in which case the values are assumed to already be
in the data's unit.

Discovery + auto-generation are centralised here so the viewer and the HTML
views behave identically (David 2026-09-24): drop a file next to the results and
every surface picks it up; if none exists we auto-generate an editable starter
from the actual swept data and invite the user to rename/re-range the bands.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

# Config-unit -> Hz scale (for converting a band file's `unit` to the data unit).
_UNIT_HZ = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}

# Standard filenames looked up next to the data (parquet / results dir).
BAND_FILENAMES = ("bands.json", "padb_viewer_bands.json")


def unit_scale(cfg_unit: str, data_unit: str) -> float:
    """Multiplier converting a value in `cfg_unit` into `data_unit`. Falls back to
    1.0 (no conversion) when either unit is unknown -- the values are then assumed
    to already be in the data's unit, which is the documented default."""
    c = (cfg_unit or "").strip().lower()
    d = (data_unit or "").strip().lower()
    if c in _UNIT_HZ and d in _UNIT_HZ:
        return _UNIT_HZ[c] / _UNIT_HZ[d]
    return 1.0


def _clean_bands(raw, scale: float) -> list[dict]:
    out = []
    for b in raw or []:
        try:
            lo = float(b["lo"]) * scale
            hi = float(b["hi"]) * scale
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(lo) and math.isfinite(hi)):
            continue
        if hi < lo:
            lo, hi = hi, lo
        name = str(b.get("name", "")).strip() or f"{lo:g}-{hi:g}"
        out.append({"name": name, "lo": lo, "hi": hi})
    out.sort(key=lambda d: d["lo"])
    return out


def load_bands_file(path: Path, data_unit: str) -> list[dict]:
    """Read a named-band JSON at `path`, converting each band's lo/hi from the
    file's `unit` into `data_unit`. Returns [] on any problem (never raises)."""
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(cfg, list):            # bare list of bands, already data-unit
        return _clean_bands(cfg, 1.0)
    if not isinstance(cfg, dict):
        return []
    scale = unit_scale(cfg.get("unit", data_unit), data_unit)
    return _clean_bands(cfg.get("bands"), scale)


def _round_sig(x: float, sig: int = 3) -> float:
    if x == 0 or not math.isfinite(x):
        return 0.0
    d = sig - int(math.floor(math.log10(abs(x)))) - 1
    return round(x, d)


def _fmt(x: float) -> str:
    """Compact human label for a band edge (no trailing zeros)."""
    x = _round_sig(x, 4)
    if x == int(x):
        return str(int(x))
    return ("%g" % x)


def auto_bands(freqs, x_unit: str, target: int = 4) -> list[dict]:
    """Derive an editable starter set of ~`target` bands from the actual swept x
    values. Log-spaced edges when the data spans >=2 decades (common for RF
    sweeps), else linear; interior edges rounded to nice values, the true data
    min/max kept at the ends. Names are generic ("Band k (lo-hi unit)") on the
    assumption the user will rename them."""
    xs = sorted({float(f) for f in freqs
                 if f is not None and isinstance(f, (int, float)) and math.isfinite(float(f))})
    if len(xs) < 2:
        return []
    lo, hi = xs[0], xs[-1]
    if hi <= lo:
        return []
    n = max(1, min(target, len(xs) - 1))
    if lo > 0 and hi / lo >= 100:        # spans >=2 decades -> log-spaced edges
        lg0, lg1 = math.log10(lo), math.log10(hi)
        edges = [10 ** (lg0 + (lg1 - lg0) * i / n) for i in range(n + 1)]
    else:                                # linear edges
        edges = [lo + (hi - lo) * i / n for i in range(n + 1)]
    # Round interior edges to nice values; keep true min/max at the ends.
    edges = [lo] + [_round_sig(e, 2) for e in edges[1:-1]] + [hi]
    # Enforce strictly increasing (rounding can collide); drop dupes.
    clean = [edges[0]]
    for e in edges[1:]:
        if e > clean[-1]:
            clean.append(e)
    if len(clean) < 2:
        clean = [lo, hi]
    bands = []
    for i in range(len(clean) - 1):
        a, b = clean[i], clean[i + 1]
        u = (" " + x_unit) if x_unit else ""
        bands.append({"name": f"Band {i + 1} ({_fmt(a)}-{_fmt(b)}{u})", "lo": a, "hi": b})
    return bands


def write_auto_bands(path: Path, bands: list[dict], x_unit: str) -> None:
    """Write an auto-generated band file (values already in the data unit) with a
    comment inviting the user to edit names/ranges. Never overwrites silently --
    callers only call this when no file exists."""
    doc = {
        "_comment": ("AUTO-GENERATED starter bands from the swept x-axis -- EDIT ME. "
                     "Rename each band and adjust 'lo'/'hi' to your real band edges. "
                     "'unit' is the value unit for lo/hi (here the data's own x unit); "
                     "set it to Hz/kHz/MHz/GHz to enter edges in that unit instead. "
                     "Delete this file to regenerate, or replace it with your own."),
        "_auto_generated": True,
        "unit": x_unit or "",
        "bands": [{"name": b["name"], "lo": b["lo"], "hi": b["hi"]} for b in bands],
    }
    Path(path).write_text(json.dumps(doc, indent=2), encoding="utf-8")


def find_or_create_bands(freqs, x_unit, search_dirs, allow_create=True, target=4):
    """Resolve the named bands for a dataset.

    1. Look for an existing band file (BAND_FILENAMES) in each of `search_dirs`.
    2. If none and `allow_create`, auto-generate a starter from `freqs` and write
       it into the first search dir (padb_viewer_bands.json).

    Returns (bands_in_data_units, path_or_None, created_bool). `bands` is [] when
    the data has no usable swept x and nothing could be created.
    """
    dirs = [Path(d) for d in search_dirs if d]
    for d in dirs:
        for fn in BAND_FILENAMES:
            p = d / fn
            if p.exists():
                return load_bands_file(p, x_unit), p, False
    if not allow_create or not dirs:
        return [], None, False
    bands = auto_bands(freqs, x_unit, target=target)
    if not bands:
        return [], None, False
    out = dirs[0] / "padb_viewer_bands.json"
    try:
        write_auto_bands(out, bands, x_unit)
    except OSError:
        return bands, None, True   # still usable in-memory even if the write failed
    return bands, out, True
