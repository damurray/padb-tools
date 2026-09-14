"""Build-time comprehensive multi-view PDF report.

Drives a headless Chromium (via Playwright) over the *same* interactive view
HTML files padb_v2.py already generates, opens each view's Statistics/Results
table, suppresses the interactive-only chrome (control bars, filter bars, action
buttons) with an injected print stylesheet, prints each view to a landscape PDF,
and concatenates them behind a cover page into one comprehensive report per
analytic.

Design notes / why it looks like this
-------------------------------------
* **Reuses the real pages, not a re-render.** The whole point of a headless-print
  report (chosen over a static Plotly image export) is that the PDF matches
  exactly what an engineer sees on screen -- same plot, same stats table, same
  spec lines. So this module never re-plots anything; it loads the already-built
  self-contained HTML and prints it. That also means it can't drift from the
  views' own rendering logic by construction.
* **No edits to padb_plots.py.** Every view already exposes a stats-table toggle
  function and uses the shared `.ctrl-bar` / `.filter-bar` / `.flt-bar` chrome
  classes, so the "open the table, hide the buttons" step is done entirely from
  the outside via `page.evaluate` + an injected `<style>`. If a view later renames
  a toggle or a chrome class, only PRINT_PROFILES / _REPORT_HIDE_CSS here need a
  tweak -- the pages themselves are untouched.
* **Opt-in and fail-safe.** Playwright + its bundled Chromium is a heavyweight
  dependency (~150 MB) that the rest of the tool does not need, so this is only
  invoked when a job asks for it (`build_pdf_report: true`). If Playwright or its
  browser isn't installed, `generate_multiview_pdf` logs a NOTE and returns None
  -- it never raises into, or fails, the surrounding build.

Enable once with:
    py -m pip install playwright pypdf
    py -m playwright install chromium
"""

from __future__ import annotations

import html as _html
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional


# --------------------------------------------------------------------------
# Per-view print profile
# --------------------------------------------------------------------------
# slug -> how to prepare that view's page for printing:
#   plot    : the Plotly div id (wait for it to have traces before printing)
#   panels  : stats/results panel div id(s) that must be visible in the PDF
#   toggles : JS function name(s) to call to build+show the panel(s). Each view's
#             toggle both builds the table and reveals it, so calling it once from
#             a hidden state is exactly "open the table". Views whose table is
#             always visible (distribution) have no toggle.
# `refresh` (optional) is a button id clicked after the table is opened, to force
# a full build past each view's "large dataset -- click Refresh table" size gate
# (Statistics/Results tables auto-refresh only below ~150 conditions). A report
# always wants the real table, so we force it.
# `workflow`/`ctx` (optional): the one-click auto-filter workflow fn and its ctx
# global. In filter-aware mode we call the workflow before printing so the plot +
# stats table reflect the recommended exclusions (the shared Global Filter for the
# GF views -- inherited across them -- or the in-memory _hAutoExcl for histogram).
# scatter/distribution have no engine: they deliberately stay the full collected
# population (you want to *see* every point in a raw scatter / KDE).
PRINT_PROFILES: dict[str, dict[str, Any]] = {
    "scatter":      {"plot": "plot",     "panels": ["scatter_table_panel"], "toggles": ["toggleScatterTable"]},
    "stat_summary": {"plot": "plot",     "panels": ["stat_panel"],          "toggles": ["toggleStatPanel"],  "refresh": "stat_refresh_table_btn", "workflow": "statRunWorkflow", "ctx": "STAT_AF"},
    "boxplot":      {"plot": "plot",     "panels": ["box_stat_panel"],       "toggles": ["toggleStatPanel"],  "refresh": "box_refresh_table_btn", "workflow": "boxRunWorkflow", "ctx": "BOX_AF"},
    "distribution": {"plot": "kde_plot", "panels": ["delta_tbl", "dist_ti_tbl"], "toggles": []},
    "env_coverage": {"plot": "plot",     "panels": ["ec_stat_panel"],        "toggles": ["toggleStatsPanel"], "refresh": "ec_refresh_table_btn", "workflow": "ecRunWorkflow", "ctx": "EC_AF"},
    "summary":      {"plot": "plot",     "panels": ["sum_table_wrap"],       "toggles": ["buildTable"],       "refresh": "sum_refresh_table_btn", "workflow": "sumRunWorkflow", "ctx": "SUM_AF"},
    "histogram":    {"plot": "plot",     "panels": ["h_stats"],              "toggles": ["toggleStats"],      "workflow": "histRunWorkflow", "ctx": "HIST_AF"},
}

# Known view slugs, longest-first, so a filename like
# "..._env_coverage.html" matches "env_coverage" before "summary"/"coverage".
_VIEW_SLUGS = sorted(PRINT_PROFILES.keys(), key=len, reverse=True)

# Injected print stylesheet: hide every interactive-only control so the PDF is
# just title + plot + stats table + any always-on disclaimer notes. The stats
# panels are plain <div>s (not buttons), so they survive. Legends live inside the
# Plotly SVG, so they survive too.
_REPORT_HIDE_CSS = """
.ctrl-bar, .filter-bar, .flt-bar, .env-bar, .dist-filter-bar,
.badge, .csv-wrap, .csv-menu, .seg-bar, .segbar { display: none !important; }
button, input, select, textarea { display: none !important; }
/* interactive-only auto-filter / workflow / GF panels we did not open */
#box_wf_panel, #auto_gf_panel, #stat_wf_panel, #stat_auto_panel,
#ec_wf_panel, #ec_auto_panel, #sum_wf_panel, #sum_auto_panel,
#box_site_panel, #stat_site_panel, #ec_site_panel, #dist_site_panel,
#h_site_panel, #box_outlier_panel, #box_delta_panel { display: none !important; }
body { padding: 0 !important; margin: 0 !important; }
"""

_METHODOLOGY_NOTE = (
    "Each view is the exact interactive page an engineer opens from the results "
    "gallery, printed with its Statistics/Results table expanded and the "
    "interactive controls (filters, Global-Filter buttons, zoom) suppressed. "
    "Statistics shown are computed the same way as in the live pages "
    "(one point per DUT per frequency; tolerance intervals via the same "
    "parametric / non-parametric path). Tolerance-interval and KDE estimates "
    "assume a reasonably well-behaved population -- few DUTs, high measurement "
    "noise, or strongly non-normal data can make the bounds unstable."
)


def _view_of(path: Path) -> Optional[str]:
    """Recover the view slug from a generated view filename."""
    stem = path.stem.lower()
    for slug in _VIEW_SLUGS:
        if stem.endswith("_" + slug) or stem == slug:
            return slug
    return None


# --------------------------------------------------------------------------
# Environment check
# --------------------------------------------------------------------------
def check_environment() -> tuple[bool, str]:
    """Return (ok, reason). ok=True means Playwright + Chromium + pypdf are usable."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception as exc:  # pragma: no cover - env dependent
        return False, (
            f"Playwright not installed ({exc}). Enable the PDF report with: "
            "py -m pip install playwright pypdf && py -m playwright install chromium"
        )
    try:
        import pypdf  # noqa: F401
    except Exception as exc:  # pragma: no cover
        return False, f"pypdf not installed ({exc}). Run: py -m pip install pypdf"
    # Confirm the Chromium browser is actually downloaded (import alone doesn't).
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
        if not exe or not Path(exe).exists():
            return False, (
                "Playwright Chromium browser not downloaded. Run: "
                "py -m playwright install chromium"
            )
    except Exception as exc:  # pragma: no cover
        return False, f"Playwright Chromium not available ({exc}). Run: py -m playwright install chromium"
    return True, "ok"


# --------------------------------------------------------------------------
# Cover page
# --------------------------------------------------------------------------
def _cover_html(meta: dict[str, Any], view_labels: list[str],
                apply_filter: bool = False,
                exclusions: Optional[list] = None) -> str:
    esc = _html.escape
    title = esc(str(meta.get("title", "Multi-View Analysis Report")))
    rows = []

    def _row(k: str, v: Any) -> None:
        if v is None or v == "":
            return
        rows.append(
            f'<tr><td style="padding:3px 14px 3px 0;color:#555;white-space:nowrap">{esc(k)}</td>'
            f'<td style="padding:3px 0;font-weight:600">{esc(str(v))}</td></tr>'
        )

    _row("Source CSV", meta.get("csv_name"))
    _row("Rows (usable)", f"{meta['rows']:,}" if meta.get("rows") is not None else None)
    _row("DUTs", meta.get("n_duts"))
    _row("Conditions", meta.get("n_conds"))
    _row("Temperatures", meta.get("temps"))
    _row("Spec limits", meta.get("spec"))
    _row("Generated", meta.get("generated"))

    contents = "".join(f"<li>{esc(l)}</li>" for l in view_labels)

    # Filtering section: what (if anything) was auto-filtered out of the stats
    # views. The Dataset table above is always the full *collected* population.
    if apply_filter:
        total_duts = sum(e.get("duts", 0) for _l, e in (exclusions or []))
        total_pts = sum(e.get("pts", 0) for _l, e in (exclusions or []))
        if exclusions:
            excl_rows = "".join(
                f'<li>{esc(lbl)}: auto-excluded <b>{e.get("duts",0)}</b> DUT'
                f'{"" if e.get("duts",0)==1 else "s"} '
                f'({e.get("pts",0)} point{"" if e.get("pts",0)==1 else "s"})</li>'
                for lbl, e in exclusions
            )
        else:
            excl_rows = "<li>No engine views in this report.</li>"
        filter_html = (
            '<div class="sect">Filtering applied</div>'
            '<p class="note">The statistical views below reflect the auto-filter '
            "<b>recommended exclusions</b> (the risk-gated &ldquo;auto&rdquo; set, "
            "the same as the in-page &ldquo;Run recommended workflow&rdquo; button) "
            "&mdash; reversible and audited. The raw Scatter / Distribution views "
            "(where present) still show the <b>full collected population</b>, so "
            "this report shows both what was collected (Dataset, above) and what "
            f"the cleaning removed. Total auto-excluded across views: "
            f"<b>{total_duts}</b> DUT-instances / <b>{total_pts}</b> points.</p>"
            f"<ul>{excl_rows}</ul>"
        )
    else:
        filter_html = (
            '<div class="sect">Filtering applied</div>'
            '<p class="note">None &mdash; every view shows the <b>full collected '
            "population</b> as measured. (Generate with the filter-aware option to "
            "apply the auto-filter recommended exclusions to the statistical views.)</p>"
        )

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family: Segoe UI, Arial, sans-serif; color:#222; margin:0; padding:48px 56px; }}
  h1 {{ font-size:26px; margin:0 0 4px; }}
  h2 {{ font-size:15px; color:#555; font-weight:500; margin:0 0 28px; }}
  table {{ border-collapse:collapse; font-size:14px; margin-bottom:26px; }}
  .sect {{ font-size:13px; text-transform:uppercase; letter-spacing:.05em; color:#888;
           border-bottom:1px solid #ddd; padding-bottom:4px; margin:22px 0 10px; }}
  ul {{ font-size:14px; line-height:1.7; margin:0 0 26px; padding-left:22px; }}
  p.note {{ font-size:12.5px; color:#555; line-height:1.6; background:#f7faff;
            border:1px solid #cdd6e6; border-radius:4px; padding:12px 14px; }}
</style></head><body>
  <h1>{title}</h1>
  <h2>Comprehensive multi-view analysis report</h2>
  <div class="sect">Dataset (as collected)</div>
  <table>{''.join(rows)}</table>
  {filter_html}
  <div class="sect">Contents</div>
  <ul>{contents}</ul>
  <div class="sect">Methodology &amp; caveats</div>
  <p class="note">{esc(_METHODOLOGY_NOTE)}</p>
</body></html>"""


# --------------------------------------------------------------------------
# Printing
# --------------------------------------------------------------------------
def _prepare_and_print(page, url: str, out_pdf: Path, profile: Optional[dict],
                       log: Callable[[str], None], apply_filter: bool = False) -> tuple[bool, Optional[dict]]:
    """Load one page, (optionally) apply the auto-filter, open its table, hide
    chrome, print to out_pdf. Returns (ok, exclusion_info) -- exclusion_info is
    {"duts": n, "pts": m} when filtering ran on this view, else None."""
    page.goto(url, wait_until="load", timeout=60000)
    plot_id = (profile or {}).get("plot", "plot")
    # Wait for Plotly to actually have traces (the page renders via JS on load).
    try:
        page.wait_for_function(
            "(id)=>{var g=document.getElementById(id);return g&&g.data&&g.data.length>0;}",
            arg=plot_id, timeout=45000,
        )
    except Exception:
        # A cover page or a placeholder has no plot -- that's fine, keep going.
        pass
    page.wait_for_timeout(600)

    excl = None
    if profile and apply_filter and profile.get("workflow"):
        # Run the one-click auto-filter workflow, then read how much it excluded.
        # This applies the conservative "auto" set only (the risk-gated
        # recommendation) -- the same thing the in-page "Run recommended
        # workflow" button does -- and re-renders the plot + table against it.
        try:
            page.evaluate(
                "(fn)=>{ if(typeof window[fn]==='function'){ try{ window[fn](); }catch(e){} } }",
                profile["workflow"],
            )
            page.wait_for_timeout(1200)
            ctxname = profile.get("ctx")
            if ctxname:
                excl = page.evaluate(
                    "(cn)=>{var c=window[cn]; if(!c) return null;"
                    " var r=window[c.resultVar]; if(!r||!r.auto) return {duts:0,pts:0};"
                    " return {duts:r.auto.length, pts:r.auto.reduce(function(x,d){return x+(d.pts?d.pts.length:0);},0)};}",
                    ctxname,
                )
        except Exception:
            excl = None

    if profile:
        # Open the stats/results table (build + show).
        for fn in profile.get("toggles", []):
            try:
                page.evaluate(
                    "(fn)=>{ if(typeof window[fn]==='function'){ try{ window[fn](); }catch(e){} } }",
                    fn,
                )
            except Exception:
                pass
        page.wait_for_timeout(300)
        # Force a full table build past the "large dataset -- click Refresh" gate.
        refresh = profile.get("refresh")
        if refresh:
            try:
                page.evaluate(
                    "(id)=>{var b=document.getElementById(id); if(b){ try{ b.click(); }catch(e){} }}",
                    refresh,
                )
                page.wait_for_timeout(500)
            except Exception:
                pass
        # Force any still-hidden target panel visible.
        for pid in profile.get("panels", []):
            try:
                page.evaluate(
                    "(id)=>{var el=document.getElementById(id);"
                    "if(el&&getComputedStyle(el).display==='none'){el.style.display='block';}}",
                    pid,
                )
            except Exception:
                pass
        try:
            page.add_style_tag(content=_REPORT_HIDE_CSS)
        except Exception:
            pass
        page.wait_for_timeout(200)

    page.emulate_media(media="print")
    page.pdf(
        path=str(out_pdf),
        print_background=True,
        landscape=True,
        width="11in",
        height="8.5in",
        margin={"top": "0.3in", "bottom": "0.3in", "left": "0.3in", "right": "0.3in"},
    )
    return (out_pdf.exists() and out_pdf.stat().st_size > 0), excl


def generate_multiview_pdf(
    view_pairs: list[tuple[str, Path]],
    out_pdf: Path,
    meta: dict[str, Any],
    log: Optional[Callable[[str], None]] = None,
    apply_filter: bool = False,
) -> Optional[Path]:
    """Build one comprehensive PDF from the given (view_slug, html_path) pairs.

    apply_filter=True runs each engine view's one-click auto-filter workflow
    before printing, so the stats views (boxplot/stat_summary/summary/env_coverage
    /histogram) show the recommended-exclusion (cleaned) population; the raw
    scatter/distribution still show the full collected data. The cover documents
    both what was collected and what was excluded.

    Returns the output path on success, or None if the environment is unavailable
    or nothing could be printed. Never raises into the caller.
    """
    log = log or (lambda s: print(s, flush=True))

    ok, reason = check_environment()
    if not ok:
        log(f"  NOTE: build-time PDF report skipped -- {reason}")
        return None

    pairs = [(v, p) for (v, p) in view_pairs if p and Path(p).exists()]
    if not pairs:
        log("  NOTE: build-time PDF report skipped -- no view files to include.")
        return None

    from playwright.sync_api import sync_playwright
    from pypdf import PdfWriter

    view_labels = [meta.get("view_labels", {}).get(v, v) for (v, _p) in pairs]

    # A fresh page per view (closed after) keeps a 10 MB+ Plotly page from
    # accumulating memory across gotos until the single renderer OOMs -- the
    # failure mode on large, non-decimated analytics. Hardened launch args plus
    # a bigger V8 heap reduce renderer crashes further; if the whole browser
    # still dies on one view, we relaunch it and press on so one bad view can't
    # sink the report.
    launch_args = ["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox",
                   "--js-flags=--max-old-space-size=4096"]

    tmpdir = Path(tempfile.mkdtemp(prefix="padb_pdf_"))
    part_pdfs: list[Path] = []

    def _print_one(browser, url: str, part: Path, profile, do_filter: bool) -> tuple[bool, Any, Optional[dict]]:
        """Print one page on a throwaway page; return (ok, browser, excl)
        relaunching the browser if it crashed so the caller can continue."""
        for attempt in range(2):
            try:
                page = browser.new_page(viewport={"width": 1000, "height": 1400})
                try:
                    ok, excl = _prepare_and_print(page, url, part, profile, log, do_filter)
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
                return ok, browser, excl
            except Exception as exc:
                msg = str(exc)
                crashed = ("crash" in msg.lower() or "closed" in msg.lower()
                           or "target" in msg.lower())
                if crashed and attempt == 0:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser = pw.chromium.launch(args=launch_args)
                    continue
                raise
        return False, browser, None

    view_parts: list[Path] = []
    exclusions: list[tuple[str, dict]] = []  # (view_label, {duts,pts}) when filtered
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=launch_args)

            # Views first (so the cover, printed after, can summarize what each
            # view's auto-filter excluded), then merged cover-first below.
            for i, (slug, html_path) in enumerate(pairs, 1):
                profile = PRINT_PROFILES.get(slug)
                part = tmpdir / f"{i:02d}_{slug}.pdf"
                label = meta.get("view_labels", {}).get(slug, slug)
                try:
                    ok, browser, excl = _print_one(
                        browser, Path(html_path).resolve().as_uri(), part, profile, apply_filter)
                    if ok:
                        view_parts.append(part)
                        if excl is not None:
                            exclusions.append((label, excl))
                            log(f"    + {label}  (auto-filter: -{excl.get('duts',0)} DUT/"
                                f"{excl.get('pts',0)} pts)")
                        else:
                            log(f"    + {label}")
                    else:
                        log(f"    ! {label}: produced no PDF page (skipped)")
                except Exception as exc:
                    log(f"    ! {label}: {str(exc).splitlines()[0]} (skipped)")

            # Cover page last (needs the exclusion tallies), merged first.
            cover_html = tmpdir / "_cover.html"
            cover_html.write_text(
                _cover_html(meta, view_labels, apply_filter=apply_filter, exclusions=exclusions),
                encoding="utf-8")
            cover_pdf = tmpdir / "00_cover.pdf"
            try:
                ok, browser, _ = _print_one(browser, cover_html.resolve().as_uri(), cover_pdf, None, False)
                if ok:
                    part_pdfs.append(cover_pdf)
            except Exception as exc:
                log(f"  NOTE: PDF cover page failed ({exc}); continuing without it.")

            part_pdfs.extend(view_parts)

            try:
                browser.close()
            except Exception:
                pass

        if not part_pdfs:
            log("  NOTE: build-time PDF report produced no pages.")
            return None

        writer = PdfWriter()
        for part in part_pdfs:
            writer.append(str(part))
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        with open(out_pdf, "wb") as fh:
            writer.write(fh)
        writer.close()

        size_mb = out_pdf.stat().st_size / (1024 * 1024)
        log(f"  Comprehensive PDF report: {out_pdf.name} "
            f"({len(part_pdfs)} sections, {size_mb:.1f} MB)")
        return out_pdf
    except Exception as exc:
        log(f"  NOTE: build-time PDF report failed ({exc}).")
        return None
    finally:
        # Best-effort temp cleanup.
        try:
            for f in tmpdir.glob("*"):
                f.unlink(missing_ok=True)
            tmpdir.rmdir()
        except Exception:
            pass


# --------------------------------------------------------------------------
# CLI: build a report from an already-generated results folder
# --------------------------------------------------------------------------
def _cli(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Build a comprehensive multi-view PDF from generated view HTML files."
    )
    ap.add_argument("target", help="A results folder (uses all *_<view>.html) or a filename prefix.")
    ap.add_argument("--out", help="Output PDF path (default <folder>/<prefix>_report.pdf).")
    ap.add_argument("--title", help="Report title (default derived from the files).")
    ap.add_argument("--apply-filter", action="store_true",
                    help="Filter-aware report: run each engine view's auto-filter "
                         "recommended workflow before printing, so the stats views "
                         "show the cleaned population (raw scatter/distribution stay "
                         "full). Best run on demand, after the plots exist.")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if target.is_dir():
        htmls = sorted(target.glob("*.html"))
    else:
        htmls = sorted(target.parent.glob(target.name + "*.html"))
        target = target.parent
    pairs: list[tuple[str, Path]] = []
    seen: set[str] = set()
    # Order views canonically.
    order = list(PRINT_PROFILES.keys())
    tagged = []
    for h in htmls:
        v = _view_of(h)
        if v and v not in seen:
            seen.add(v)
            tagged.append((v, h))
    tagged.sort(key=lambda t: order.index(t[0]) if t[0] in order else 99)
    pairs = tagged
    if not pairs:
        print(f"No view HTML files found under {target}", flush=True)
        return 1

    title = args.title or pairs[0][1].stem
    out = Path(args.out) if args.out else (target / f"{re.sub(r'_[a-z_]+$', '', pairs[0][1].stem)}_report.pdf")
    meta = {
        "title": title,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "view_labels": {
            "scatter": "Scatter", "stat_summary": "Statistical Summary",
            "boxplot": "Box Plots", "distribution": "Distribution (Delta-Env)",
            "env_coverage": "Environmental Coverage", "summary": "Summary",
            "histogram": "Histogram",
        },
    }
    res = generate_multiview_pdf(pairs, out, meta, apply_filter=args.apply_filter)
    return 0 if res else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
