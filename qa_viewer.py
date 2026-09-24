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
    V.DS.compute_band_counts()

    check("DataSet: both sites detected", set(V.DS.sites) >= {"SR", "AMC"}, str(V.DS.sites))
    check("DataSet: x_unit is MHz", V.DS.x_unit == "MHz", V.DS.x_unit)
    check("bands: 2 loaded", len(V.DS.bands) == 2, str(V.DS.bands))
    check("bands: Hz->MHz converted (Low.hi ~= mid MHz)",
          V.DS.bands and abs(V.DS.bands[0]["hi"] - mid) < 1e-6,
          str(V.DS.bands[:1]))
    # Band-derived load cap (David 2026-09-24): counts per band + largest drives the cap.
    check("bands: per-band row counts computed", len(V.DS.band_counts) == 2, str(V.DS.band_counts))
    check("bands: max_band_rows = largest band count",
          V.DS.max_band_rows == max(V.DS.band_counts), str((V.DS.max_band_rows, V.DS.band_counts)))
    check("bands: effective cap floors at the largest band (single band always opens)",
          V._effective_band_cap(V.DS) >= V.DS.max_band_rows, str(V._effective_band_cap(V.DS)))

    # --- main-plot zoom/reset drives the frequency filter (JS in _PAGE) --------
    # The scatter is server-side decimated per [flo,fhi], so a drag-zoom / Autoscale /
    # Reset-axes must drive flo/fhi + re-query -- not just re-viewport the decimated
    # points. Without this, band-view buttons inherit the full (giant) range and the
    # "other plots" appear to show no data. Can't drive Plotly's modebar through the
    # in-process test client, so pin the wiring in the page source.
    page = V._PAGE
    check("main plot: plotly_relayout listener wired", "plotly_relayout" in page)
    check("main plot: _onRelayout handler present",
          "_onRelayout" in page and "function _onRelayout" in page)
    check("main plot: Autoscale/Reset -> full range (handles xaxis.autorange)",
          "xaxis.autorange" in page)
    check("main plot: drag-zoom reads xaxis.range", "xaxis.range" in page)
    check("main plot: update() pins x-range to the queried band (range:[flo,fhi])",
          "range:[flo,fhi]" in page)
    check("main plot: react re-query guarded against relayout loop (_syncing)",
          "_syncing" in page)

    client = V.app.test_client()

    # --- meta ---------------------------------------------------------------
    m = client.get("/api/meta").get_json()
    check("meta: rows>0", m["rows"] > 0, str(m.get("rows")))
    check("meta: bands present", len(m.get("bands", [])) == 2)
    check("meta: band_counts + max_band_rows + band_cap exposed",
          len(m.get("band_counts", [])) == 2 and isinstance(m.get("max_band_rows"), int)
          and m.get("band_cap", 0) >= m.get("max_band_rows", 0),
          str((m.get("band_counts"), m.get("max_band_rows"), m.get("band_cap"))))
    check("meta: slow_band_warn is a bool", isinstance(m.get("slow_band_warn"), bool))
    check("meta: x_unit present", bool(m.get("x_unit")))
    check("meta: both sites", set(m.get("sites", [])) >= {"SR", "AMC"})
    # Temperature read from the real Test Step column (not Group-parsed) -> reliable.
    check("meta: multi-temp temps present", len(m.get("temps", [])) >= 2, str(m.get("temps")))
    check("meta: is_room_only False for multi-temp data", m.get("is_room_only") is False)

    # --- scatter decimation + per-site traces ------------------------------
    s = client.get(f"/api/scatter?flo={xmin}&fhi={xmax}&maxpts=50&sites=SR,AMC").get_json()
    check("scatter: 2 per-site traces", len(s["traces"]) == 2, str(len(s["traces"])))
    check("scatter: decimation cap respected", s["n_returned"] <= 50 * 2 + 4,
          f"n_returned={s['n_returned']}")
    check("scatter: n_total is full band", s["n_total"] == n_rows, str(s["n_total"]))
    # Temperature filter actually narrows the data (one temp < all temps).
    one_t = m["temps"][0]
    st = client.get(f"/api/scatter?flo={xmin}&fhi={xmax}&maxpts=50&temps={one_t}").get_json()
    check("scatter: temp filter narrows n_total", 0 < st["n_total"] < s["n_total"],
          f"one_temp={st['n_total']} all={s['n_total']}")

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

    # --- large-band guard (David 2026-09-24) --------------------------------
    # Over the row cap, ANY band view returns a "narrow the band" page instead of
    # grinding out a huge render (the full-range case that looked blank/broken).
    _orig_cap = V.VIEW_BAND_MAX_ROWS
    try:
        V.VIEW_BAND_MAX_ROWS = 1
        V._band_cache.clear()
        # Full range spans BOTH bands -> exceeds the largest-band floor -> guarded.
        gb = _get(client, f"/view?view=boxplot&flo={xmin}&fhi={xmax}&full=0").get_data(as_text=True)
        check("viewer: large-band guard serves a 'Band too large' page", "Band too large" in gb)
        gs = _get(client, f"/view?view=scatter&flo={xmin}&fhi={xmax}&full=0").get_data(as_text=True)
        check("viewer: guard applies to the scatter band too (not just boxplot)", "Band too large" in gs)
        # ...but a SINGLE named band still opens even with VIEW_BAND_MAX_ROWS=1, because the
        # effective cap floors at the largest band (David 2026-09-24). Teeth: without the
        # floor this would also be guarded.
        V._band_cache.clear()
        sb = _get(client, f"/view?view=scatter&flo={xmin}&fhi={mid}&full=0").get_data(as_text=True)
        check("viewer: a single named band still renders under the largest-band floor",
              "Band too large" not in sb and "Plotly" in sb, sb[-200:])
    finally:
        V.VIEW_BAND_MAX_ROWS = _orig_cap
        V._band_cache.clear()
    # UI wiring (auto-refresh on zoom + spinner) can't be driven by the test client, so pin it.
    vsrc = Path(V.__file__).read_text(encoding="utf-8")
    check("viewer: open band view auto-refreshes on band change (debounced)",
          "_curView=v;" in vsrc and "openView(_curView)" in vsrc and "_vRefreshTimer" in vsrc)
    check("viewer: band-view spinner overlay present",
          'id="vbusy"' in vsrc and "vspin" in vsrc)
    check("viewer: band buttons show point counts + heavy-band flag",
          "band_counts" in vsrc and "band views may load slowly" in vsrc)
    check("viewer: slow-band warning banner wired to slow_band_warn",
          "slow_band_warn" in vsrc and "largest band ~" in vsrc)

    # Efficiency: band HTML references the shared /plotly.js route instead of re-embedding
    # ~4.5 MB of Plotly on every request (David 2026-09-24: "band render longer than the
    # html"). The library is then browser-cached across band views.
    sc_html = _get(client, f"/view?view=scatter&flo={xmin}&fhi={xmax}&full=0").get_data(as_text=True)
    check("viewer: band HTML uses shared /plotly.js (not ~4.5MB inline)",
          'src="/plotly.js"' in sc_html and len(sc_html) < 2_000_000, f"len={len(sc_html)}")
    check("viewer: /plotly.js route serves the library",
          "Plotly" in client.get("/plotly.js").get_data(as_text=True))

    # Overview pass/fail filter (David 2026-09-24). Partition: All == Passing + Failing
    # (Passing = everything that isn't a true fail, so it holds with or without limits).
    def _sn(pf):
        return client.get(f"/api/scatter?flo={xmin}&fhi={xmax}&maxpts=50000&sites=SR,AMC&pf={pf}").get_json()["n_total"]
    na, npass, nfail = _sn("all"), _sn("pass"), _sn("fail")
    check("viewer: overview pass/fail partition (All == Passing + Failing)",
          na == npass + nfail, f"all={na} pass={npass} fail={nfail}")
    check("viewer: meta exposes has_limits", isinstance(m.get("has_limits"), bool))
    # Segment stepper endpoint: spec stairs + named bands.
    seg = client.get("/api/segments").get_json()
    check("viewer: /api/segments returns spec + bands lists",
          isinstance(seg.get("spec"), list) and isinstance(seg.get("bands"), list))
    check("viewer: named-band segments available (bands.json loaded earlier)",
          len(seg["bands"]) == 2, f"bands={seg['bands']}")
    # Spec-segment stepper shows the current spec/TLL value (David 2026-09-24): each spec
    # stair carries hi_val/lo_val, and the page formats them next to the step.
    if m.get("has_limits") and seg["spec"]:
        check("viewer: spec segments carry the limit value (hi_val/lo_val)",
              any(s.get("hi_val") is not None or s.get("lo_val") is not None for s in seg["spec"]),
              str(seg["spec"][:1]))
    check("viewer: page formats the spec/TLL value while stepping (_segSpecStr)",
          "_segSpecStr" in page and "spec/TLL:" in page)

    print(f"\nqa_viewer: PASS={_PASS}  FAIL={_FAIL}")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
