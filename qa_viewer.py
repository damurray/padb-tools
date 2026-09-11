"""qa_viewer.py -- automated QA for padb_viewer.py (the local-server viewer).

Builds a synthetic 2-site compare parquet (reusing qa_padb.make_synthetic_csv,
tagging `Site:` into Group), points padb_viewer at it, and exercises the whole
thing through Flask's IN-PROCESS test client -- no ports, no headless browser,
so it can't hit the Edge/port flakiness the other harnesses sometimes do.

Covered:
  * CSV->parquet export round-trip (_csv_to_parquet)
  * DataSet column/site detection
  * named-band config with unit conversion (Hz config -> MHz data)
  * /api/meta, /api/scatter (decimation cap + per-site traces)
  * /view band-windowed render of every view (boxplot/stat_summary/summary/
    env_coverage/distribution) -- 200, no traceback, real plot
  * boxplot lite vs full: lite is smaller and drops the per-point overlay,
    full embeds it (Show-points / Site Population Check path)

Run:  py qa_viewer.py         (exit 1 on any FAIL, like qa_padb.py)
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

import qa_padb
import padb_v2
import padb_viewer as V

_PASS = 0
_FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    print(("  PASS  " if ok else "  FAIL  ") + desc + ("" if ok else "  -- " + detail))
    if ok:
        _PASS += 1
    else:
        _FAIL += 1


def _build_compare_parquet(tmp: Path):
    base = tmp / "synth.csv"
    qa_padb.make_synthetic_csv(base)
    df = pd.read_csv(base, dtype=str)
    parts = []
    for site in ("SR", "AMC"):
        d = df.copy()
        d["Group"] = d["Group"] + f"  Site: {site}"
        parts.append(d)
    merged = pd.concat(parts, ignore_index=True)
    csv = tmp / "_compare_merged.csv"
    merged.to_csv(csv, index=False)
    pq = tmp / "_compare_merged.parquet"
    with redirect_stdout(io.StringIO()):
        rows, _, _ = padb_v2._csv_to_parquet(csv, pq)
    return csv, pq, len(merged)


def _get(client, url):
    """GET a /view render, silencing padb_v2's own build chatter."""
    with redirect_stdout(io.StringIO()):
        return client.get(url)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="qa_viewer_"))
    csv, pq, n_rows = _build_compare_parquet(tmp)

    # --- parquet export round-trip -----------------------------------------
    import pyarrow.parquet as pqm
    t = pqm.read_table(str(pq))
    check("parquet export: row count matches CSV", t.num_rows == n_rows,
          f"{t.num_rows} vs {n_rows}")
    check("parquet export: Group column preserved", "Group" in t.schema.names)
    check("parquet export: value column preserved", "Power (dBc)" in t.schema.names)

    # --- DataSet + named bands (Hz config against MHz data) ----------------
    V.DS = V.DataSet(pq)
    xmin, xmax = V.DS.x_min, V.DS.x_max
    mid = (xmin + xmax) / 2
    bands_cfg = {"unit": "Hz", "bands": [
        {"name": "Low", "lo": xmin * 1e6, "hi": mid * 1e6},
        {"name": "High", "lo": mid * 1e6, "hi": xmax * 1e6},
    ]}
    (tmp / "bands.json").write_text(json.dumps(bands_cfg), encoding="utf-8")
    V.DS.bands = V._load_bands(tmp / "bands.json", V.DS.x_unit)

    check("DataSet: both sites detected", set(V.DS.sites) >= {"SR", "AMC"}, str(V.DS.sites))
    check("DataSet: x_unit is MHz", V.DS.x_unit == "MHz", V.DS.x_unit)
    check("bands: 2 loaded", len(V.DS.bands) == 2, str(V.DS.bands))
    check("bands: Hz->MHz converted (Low.hi ~= mid MHz)",
          V.DS.bands and abs(V.DS.bands[0]["hi"] - mid) < 1e-6,
          str(V.DS.bands[:1]))

    client = V.app.test_client()

    # --- meta ---------------------------------------------------------------
    m = client.get("/api/meta").get_json()
    check("meta: rows>0", m["rows"] > 0, str(m.get("rows")))
    check("meta: bands present", len(m.get("bands", [])) == 2)
    check("meta: x_unit present", bool(m.get("x_unit")))
    check("meta: both sites", set(m.get("sites", [])) >= {"SR", "AMC"})

    # --- scatter decimation + per-site traces ------------------------------
    s = client.get(f"/api/scatter?flo={xmin}&fhi={xmax}&maxpts=50&sites=SR,AMC").get_json()
    check("scatter: 2 per-site traces", len(s["traces"]) == 2, str(len(s["traces"])))
    check("scatter: decimation cap respected", s["n_returned"] <= 50 * 2 + 4,
          f"n_returned={s['n_returned']}")
    check("scatter: n_total is full band", s["n_total"] == n_rows, str(s["n_total"]))

    # --- band-windowed render of every view (lite) -------------------------
    blo, bhi = xmin, mid
    for view in ("boxplot", "stat_summary", "summary", "env_coverage", "distribution"):
        r = _get(client, f"/view?view={view}&flo={blo}&fhi={bhi}&full=0")
        html = r.get_data(as_text=True)
        check(f"{view}: lite render 200", r.status_code == 200, f"status={r.status_code}")
        check(f"{view}: no python traceback", "Traceback (most recent call last)" not in html,
              html[-300:])
        check(f"{view}: real page (has Plotly + substantial)",
              "Plotly" in html and len(html) > 20000, f"len={len(html)}")

    # --- boxplot lite vs full ----------------------------------------------
    lite = _get(client, f"/view?view=boxplot&flo={blo}&fhi={bhi}&full=0").get_data(as_text=True)
    full = _get(client, f"/view?view=boxplot&flo={blo}&fhi={bhi}&full=1").get_data(as_text=True)
    check("boxplot: lite smaller than full", len(lite) < len(full),
          f"lite={len(lite)} full={len(full)}")
    check("boxplot: full embeds per-point overlay", '"vals_detail"' in full)
    check("boxplot: lite drops per-point overlay data",
          full.count('"vals_detail"') > lite.count('"vals_detail"'),
          f"lite={lite.count(chr(34)+'vals_detail'+chr(34))} full={full.count(chr(34)+'vals_detail'+chr(34))}")
    check("boxplot: lite keeps exact server box stats",
          '"q1"' in lite and '"outlier_detail"' in lite)

    print(f"\nqa_viewer: PASS={_PASS}  FAIL={_FAIL}")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
