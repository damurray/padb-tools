r"""padb_make_bands.py -- set up a custom named-band config for the "Segment by: Named bands" step.

Writes a band JSON (``padb_viewer_bands.json`` by default) next to your results. Both the
parquet viewer and the interactive HTML views auto-pick it up: the viewer's segment stepper
gets a "Named bands" basis, and every swept-x view's "Segment by" dropdown gains a "Named
bands" option that steps the frequency window through your bands (Filter -> Plot -> Tables
stay coupled). A band file just partitions the swept x-axis into named chunks -- it does NOT
have to be RF-frequency specific.

Three ways to build one:

  # 1. Auto-generate an editable STARTER from a dataset (parquet / results folder), then edit:
  py padb_make_bands.py "<folder-or-parquet>"

  # 2. EXPLICIT bands (repeatable Name:lo:hi), in a chosen unit:
  py padb_make_bands.py "<dir>" --unit MHz --band "DAC:0.009:8" --band "Low:8:375" \
                                --band "Mid:375:3200" --band "High:3200:20000"

  # 3. The standard SG6311A PRESET (DAC/Low/Mid/High):
  py padb_make_bands.py "<dir>" --sg6311a

File shape written:
    {"unit": "MHz", "bands": [{"name": "...", "lo": <num>, "hi": <num>}, ...]}
`lo`/`hi` are in `unit`; the loaders convert them to the data's own x-axis unit. Edit the file
any time -- rename bands, change edges, add/remove rows. Won't overwrite without --force.

Exit 0 on success, 1 on error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import padb_bands

# Standard SG6311A carrier bands (Hz). Mirrors the shipped padb_viewer_bands.json template.
_SG6311A_HZ = [
    {"name": "DAC Band (9kHz-8MHz)", "lo": 9e3, "hi": 8e6},
    {"name": "LowBand (8-375MHz)", "lo": 8e6, "hi": 375e6},
    {"name": "MidBand (375-3200MHz)", "lo": 375e6, "hi": 3200e6},
    {"name": "HighBand (3.2-20GHz)", "lo": 3200e6, "hi": 20e9},
]

_EDIT_HINT = ("Edit this file any time: rename bands, adjust 'lo'/'hi', add/remove rows. "
              "'unit' is the unit for lo/hi (Hz/kHz/MHz/GHz); loaders convert to the data's "
              "x unit. Delete the file to stop offering named bands.")


def _resolve_out(target: Path, out: str | None) -> Path:
    """Where to write the band file: --out wins; else <dir>/padb_viewer_bands.json (for a
    .parquet target, its parent dir)."""
    if out:
        return Path(out).resolve()
    t = target.resolve()
    d = t.parent if t.is_file() else t
    return d / "padb_viewer_bands.json"


def _parse_band(spec: str) -> dict:
    """Parse 'Name:lo:hi' -> {name,lo,hi}. Name may contain colons (only the last two
    fields are lo/hi)."""
    parts = spec.rsplit(":", 2)
    if len(parts) != 3:
        raise ValueError(f"bad --band {spec!r}; expected Name:lo:hi")
    name, lo_s, hi_s = parts
    try:
        lo, hi = float(lo_s), float(hi_s)
    except ValueError:
        raise ValueError(f"bad --band {spec!r}; lo/hi must be numbers")
    if not name.strip():
        raise ValueError(f"bad --band {spec!r}; name is empty")
    if hi < lo:
        lo, hi = hi, lo
    return {"name": name.strip(), "lo": lo, "hi": hi}


def _auto_from_dataset(target: Path, count: int):
    """Read the swept x + unit from a parquet (or a folder containing one) and derive a
    starter band set. Reuses padb_viewer's column detection so it matches what the viewer
    sees. Returns (bands, unit)."""
    try:
        import padb_viewer
    except Exception as exc:  # pragma: no cover - env
        raise SystemExit(f"ERROR: auto mode needs padb_viewer importable ({exc})")
    pq = padb_viewer._find_parquet(target.resolve())
    ds = padb_viewer.DataSet(pq)
    bands = padb_bands.auto_bands(ds.df["x"].tolist(), ds.x_unit, target=count)
    if not bands:
        raise SystemExit("ERROR: could not derive bands (need a swept x with >=2 values). "
                         "Use --band or --sg6311a to specify bands manually.")
    return bands, ds.x_unit


def _write(path: Path, bands: list, unit: str, auto: bool, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"ERROR: {path} already exists (use --force to overwrite, or edit it directly)")
    doc = {
        "_comment": (("AUTO-GENERATED starter bands -- " if auto else "Custom bands -- ") + _EDIT_HINT),
        "unit": unit or "",
        "bands": [{"name": b["name"], "lo": b["lo"], "hi": b["hi"]} for b in bands],
    }
    if auto:
        doc["_auto_generated"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="Results folder or a .parquet file (where the band file "
                                   "is written; also the data source for auto mode)")
    ap.add_argument("--out", help="Explicit output path (default: <dir>/padb_viewer_bands.json)")
    ap.add_argument("--unit", default=None, help="Unit for --band lo/hi values (Hz/kHz/MHz/GHz; "
                    "default MHz for --band). Ignored for auto mode (uses the data unit).")
    ap.add_argument("--band", action="append", default=[], metavar="NAME:LO:HI",
                    help="Add an explicit band (repeatable), e.g. --band \"LowBand:8:375\"")
    ap.add_argument("--sg6311a", action="store_true", help="Write the standard SG6311A preset "
                    "(DAC/Low/Mid/High, in Hz)")
    ap.add_argument("--count", type=int, default=4, help="Auto mode: how many bands to derive (default 4)")
    ap.add_argument("--force", action="store_true", help="Overwrite an existing band file")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if not target.exists():
        print(f"ERROR: target not found: {target}", file=sys.stderr)
        return 1
    out = _resolve_out(target, args.out)

    # Pick a source of bands: preset > explicit > auto-from-data.
    auto = False
    if args.sg6311a:
        if args.band:
            print("ERROR: use either --sg6311a or --band, not both", file=sys.stderr)
            return 1
        bands, unit = _SG6311A_HZ, "Hz"
    elif args.band:
        try:
            bands = [_parse_band(b) for b in args.band]
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        unit = args.unit or "MHz"
    else:
        bands, unit = _auto_from_dataset(target, args.count)
        auto = True

    try:
        _write(out, bands, unit, auto, args.force)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    kind = "auto-generated starter" if auto else ("SG6311A preset" if args.sg6311a else "custom")
    print(f"Wrote {len(bands)} {kind} band(s) ({unit}) -> {out}")
    for b in bands:
        print(f"  - {b['name']}: {b['lo']:g} .. {b['hi']:g} {unit}")
    print("Edit the file to rename/re-range. The viewer + HTML views pick it up on next "
          "render/reload ('Segment by: Named bands').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
