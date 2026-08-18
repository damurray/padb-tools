"""
padb_simple.py — PADB Simple mode

A leaner, static alternative to V1/V2: no custom plotting or statistics.
Wraps PADB-R.exe's own native PNG/PDF renders (turned on per analytic by
padb_run.py's make_run_pod(..., force_native_render=True)) in a bare HTML
gallery, one card per rendered image, with a metadata table dumped verbatim
from the run pod's own [Extract]/[PADBAnalyticN] settings. This is a direct
replacement for what the old PADB::Simple Perl tool did.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from padb_run import _analytic_stems, _type_label

# ---------------------------------------------------------------------------
# POD section parsing
# ---------------------------------------------------------------------------

def parse_pod_sections(pod_path: Path) -> dict[str, dict[str, str]]:
    """Generic pass over a .pod file: {section_name: {key: value}} for every
    section (e.g. "Extract", "PADBAnalytic1", ...). Unlike padb_run.py's
    parse_pod_analytics (which only extracts a fixed set of named fields),
    this captures every key so the metadata table can pull whichever fields
    it needs without a matching parser change."""
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None

    with open(pod_path, encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            m = re.match(r"^\[(.+)\]$", line)
            if m:
                current = {}
                sections[m.group(1)] = current
                continue
            if current is None or "=" not in line:
                continue
            key, val = line.split("=", 1)
            current[key.strip()] = val.strip()

    return sections


# ---------------------------------------------------------------------------
# Metadata table (literal dump, no computation)
# ---------------------------------------------------------------------------

# (display label, candidate keys in priority order -- a candidate list handles
# a field renamed across PADB versions, e.g. ExtractionOptions_AllRunResults
# became ExtractionOptions_LastRun in newer pods).
_METADATA_FIELDS: list[tuple[str, tuple[str, ...]]] = [
    ("ExtractionOptions_AllRunResults", ("ExtractionOptions_AllRunResults", "ExtractionOptions_LastRun")),
    ("Algorithm_AlgorithmLabel", ("Algorithm_AlgorithmLabel",)),
    ("Device_Device", ("Device_Device",)),
    ("Device_Family", ("Device_Family",)),
    ("Environment_TestStep", ("Environment_TestStep",)),
    ("Environment_TestSuite", ("Environment_TestSuite",)),
    ("ExtractionOptions_TestStationLabel", ("ExtractionOptions_TestStationLabel",)),
    ("TestRun_Max", ("TestRun_Max",)),
    ("TestRun_Min", ("TestRun_Min",)),
    ("Limits_YLimit", ("Limits_YLimit",)),
]


def build_metadata_table_html(sections: dict[str, dict[str, str]], analytic_index: int) -> str:
    """Format the fixed metadata table PADB::Simple used to show per plot --
    a literal dump of the pod's own Extract + PADBAnalyticN settings, no
    computation. Missing fields render as an empty cell, never raise."""
    extract = sections.get("Extract", {})
    analytic = sections.get(f"PADBAnalytic{analytic_index}", {})
    merged = {**extract, **analytic}  # analytic-specific keys (e.g. Limits_YLimit) win

    rows: list[tuple[str, str]] = []

    min_date = merged.get("Device_MinDate", "")
    max_date = merged.get("Device_MaxDate", "")
    date_bounds = f"{min_date} to {max_date}" if (min_date or max_date) else ""
    rows.append(("Extraction Data Date Bounds", date_bounds))

    for label, candidates in _METADATA_FIELDS:
        val = next((merged[k] for k in candidates if k in merged), "")
        rows.append((label, val))

    grouping_items = sorted(
        (int(m.group(1)), key, val)
        for key, val in analytic.items()
        if (m := re.match(r"^Grouping_Item(\d+)$", key))
    )
    for _, key, val in grouping_items:
        rows.append((key, val))

    row_html = "\n".join(
        f'<tr><td class="label">{label}:</td><td>{val}</td></tr>'
        for label, val in rows
    )
    table = f'<table class="meta-table">{row_html}</table>'
    # <details> is collapsed by default with zero JS -- keeps the page compact
    # on narrow/mobile viewports, tap-to-expand per card on any device.
    return f'<details class="meta-details"><summary>Selected Extraction/Analysis Options</summary>{table}</details>'


# ---------------------------------------------------------------------------
# Gallery HTML
# ---------------------------------------------------------------------------

def _analytic_files(a: dict, results_padb: Path, suffix: str) -> list[Path]:
    """Every file in results_padb matching this analytic's known stems and
    file extension (reuses padb_run's stem-matching, no new logic)."""
    stems = _analytic_stems([a])
    found: dict[str, Path] = {}
    for stem in stems:
        for p in results_padb.glob(f"{stem}*{suffix}"):
            found[p.name] = p
    return sorted(found.values())


def _downloads_html(a: dict, results_padb: Path, csv_map: dict[str, Path]) -> str:
    links: list[str] = []
    for suffix in (".sao", ".pod", ".txt"):
        for p in _analytic_files(a, results_padb, suffix):
            links.append(f'<a href="padb/{p.name}">{p.name}</a>')
    key = a.get("name") or a.get("output_file") or ""
    csv_path = csv_map.get(key)
    if csv_path is not None:
        links.append(f'<a href="padb/{csv_path.name}">{csv_path.name}</a>')
    return " &nbsp; ".join(links) if links else ""


def make_simple_gallery_html(
    cfg: dict,
    analytics: list[dict],
    results_padb: Path,
    csv_map: dict[str, Path],
) -> Path:
    """Write results/index.html for Simple mode: a PADB::Simple-style gallery
    of PADB-R's own native renders, one card per rendered PNG, plus the
    metadata table for that analytic. Same output path/convention as V1/V2
    (results_dir/index.html) -- no nested index-of-indexes structure."""
    results_dir: Path = cfg["_results_dir"]
    run_pod = results_dir / "_run.pod"
    sections = parse_pod_sections(run_pod)

    description = cfg.get("description", "")
    pod_name = cfg["_pod_path"].name if cfg.get("_pod_path") else "N/A"
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    cards: list[str] = []
    toc_items: list[str] = []
    for a in analytics:
        idx = a["index"]
        title = a.get("main_title") or a.get("name") or f"Analytic {idx}"
        anchor_id = f"analytic-{idx}"
        nav_label = f"{_type_label(a['type'])}: {title}"
        toc_items.append(f'<a href="#{anchor_id}">{nav_label}</a>')
        table_html = build_metadata_table_html(sections, idx)
        downloads_html = _downloads_html(a, results_padb, csv_map)
        downloads_row = f'<p class="meta">Downloads: {downloads_html}</p>' if downloads_html else ""

        pngs = _analytic_files(a, results_padb, ".png")
        pdfs_by_stem = {p.stem: p for p in _analytic_files(a, results_padb, ".pdf")}

        if not pngs:
            cards.append(f"""
    <div class="card plot-card" id="{anchor_id}">
      <h3>{nav_label}</h3>
      <p style="color:#cc4400">Native graph not found for this analytic -- PADB may not have
      rendered a PNG (check OutputConfig_OutputGraph/GraphFormat in the pod).</p>
      {table_html}
      {downloads_row}
    </div>""")
            continue

        for i, png in enumerate(pngs):
            pdf = pdfs_by_stem.get(png.stem)
            image_html = f'<img src="padb/{png.name}" style="max-width:100%">'
            image_block = f'<a href="padb/{pdf.name}">{image_html}</a>' if pdf else image_html
            # Anchor only the first card for this analytic -- an analytic that
            # paginates into several PNGs still gets one TOC entry, not one
            # per page, since the nav is meant to jump between analytics.
            id_attr = f' id="{anchor_id}"' if i == 0 else ""
            cards.append(f"""
    <div class="card plot-card"{id_attr}>
      <h3>{nav_label}</h3>
      <table border="0" cellpadding="10" cellspacing="0">
        <tr>
          <td>{image_block}</td>
          <td valign="top">{table_html}</td>
        </tr>
      </table>
      {downloads_row}
    </div>""")

    cards_html = "\n".join(cards) if cards else '<p style="color:#888">No analytics found in pod.</p>'
    # Only worth a jump-nav once there are enough analytics that scrolling to
    # find one is a real problem -- for 1-3 analytics it's just clutter above
    # the content.
    toc_html = (
        f'<div class="toc"><b>Jump to:</b> {" &nbsp;|&nbsp; ".join(toc_items)}</div>'
        if len(toc_items) > 3 else ""
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PADB Simple — {description or pod_name}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: Arial, Helvetica, sans-serif; background: #f0f2f5; color: #222; font-size: 14px; }}
  .header {{ background: #003366; color: #fff; padding: 18px 32px; }}
  .header h1 {{ font-size: 1.35em; font-weight: 700; }}
  .header p  {{ margin-top: 4px; opacity: 0.75; font-size: 0.9em; }}
  .body  {{ padding: 24px 32px; }}
  .card {{ background: #fff; border-radius: 6px; padding: 16px 18px; box-shadow: 0 1px 4px rgba(0,0,0,.1); margin-bottom: 20px; }}
  .card h3 {{ font-size: 1em; color: #003366; margin-bottom: 10px; }}
  table {{ border-collapse: collapse; font-size: 0.85em; }}
  th {{ background: #f5f5f5; font-weight: 600; color: #555; text-align: left; padding: 5px 8px; }}
  td {{ padding: 4px 8px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }}
  td.label {{ font-weight: 600; color: #555; white-space: nowrap; }}
  .meta-table {{ border: 1px solid #e0e0e0; margin-top: 6px; }}
  .meta-details summary {{ cursor: pointer; font-weight: 600; color: #555; font-size: 0.85em;
                            padding: 4px 0; }}
  .meta-details summary:hover {{ color: #003366; }}
  a {{ color: #003366; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .meta {{ font-size: 0.8em; color: #888; margin-top: 10px; }}
  .toc {{ background: #fff; border-radius: 6px; padding: 10px 16px; margin-bottom: 20px;
          box-shadow: 0 1px 4px rgba(0,0,0,.1); font-size: 0.85em; }}
  .toc a {{ white-space: nowrap; }}
</style>
</head>
<body>
<div class="header">
  <h1>PADB Simple Results</h1>
  <p>{description}</p>
</div>
<div class="body">
  <p class="meta">POD: {pod_name} &nbsp;|&nbsp; Generated: {run_time} &nbsp;|&nbsp;
     <a href="HOW_TO_USE.txt">How to use this output</a></p>
  {toc_html}
  {cards_html}
</div>
</body>
</html>"""

    idx_path = results_dir / "index.html"
    idx_path.write_text(html, encoding="utf-8")
    return idx_path
