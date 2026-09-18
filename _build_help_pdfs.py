"""Regenerate the local PDF copies of the *.md help docs (not tracked in git --
see .gitignore -- these are personal, local-only build artifacts). Converts
each Markdown source to styled HTML, then renders that HTML to PDF with
**Playwright's bundled Chromium** (headless Edge's --print-to-pdf is dead on
this workstation -- exits 0 and writes nothing -- which is also why
padb_pdf_report.py moved to Playwright). Run again any time the underlying .md
files change; safe to delete the output PDFs and re-run, they're not referenced
by anything else in the pipeline.

One-time engine setup (heavyweight Chromium download):
    py -m pip install playwright markdown
    py -m playwright install chromium
"""
import sys
from pathlib import Path

import markdown

TOOLS_DIR = Path(__file__).resolve().parent
DOCS = [
    "GETTING_STARTED.md",
    "Interactive_Plots_User_Guide.md",
    "PADB_Tools_Guide.md",
    "CHANGELOG.md",
]

CSS = """
<style>
body{font-family:"Segoe UI",Arial,sans-serif;max-width:900px;margin:0 auto;
     padding:20px 30px;color:#222;line-height:1.5;font-size:14px;}
h1{font-size:26px;border-bottom:2px solid #0066cc;padding-bottom:8px;color:#1b2a4a;}
h2{font-size:20px;margin-top:1.6em;border-bottom:1px solid #ccc;padding-bottom:4px;color:#1b2a4a;}
h3{font-size:16px;margin-top:1.3em;color:#1b2a4a;}
h4{font-size:14px;margin-top:1.1em;color:#333;}
code{background:#f2f4f8;padding:1px 5px;border-radius:3px;font-family:Consolas,monospace;font-size:12.5px;}
pre{background:#282c34;color:#e6e6e6;padding:10px 14px;border-radius:5px;overflow-x:auto;
    font-family:Consolas,monospace;font-size:12px;}
pre code{background:none;color:inherit;padding:0;}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:12.5px;}
th,td{border:1px solid #ccc;padding:5px 9px;text-align:left;}
th{background:#f2f4f8;}
blockquote{border-left:3px solid #0066cc;margin:0.8em 0;padding:2px 14px;color:#555;background:#f8f9fb;}
hr{border:none;border-top:1px solid #ccc;margin:1.6em 0;}
a{color:#0066cc;}
</style>
"""

MD_EXTENSIONS = ["extra", "tables", "fenced_code", "toc", "sane_lists"]


def _md_to_html(md_name: str) -> tuple[str, Path]:
    md_path = TOOLS_DIR / md_name
    body = markdown.markdown(md_path.read_text(encoding="utf-8"), extensions=MD_EXTENSIONS)
    title = md_name.replace(".md", "").replace("_", " ")
    html = (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title>"
            f"{CSS}</head><body>{body}</body></html>")
    return html, md_path


def build_one(md_name: str, browser, out_dir: Path) -> Path:
    """Render one .md to a styled PDF via Playwright's Chromium (page.pdf)."""
    html, md_path = _md_to_html(md_name)
    html_path = out_dir / (md_path.stem + ".html")
    html_path.write_text(html, encoding="utf-8")
    pdf_path = TOOLS_DIR / (md_path.stem + ".pdf")
    page = browser.new_page()
    try:
        page.goto(html_path.as_uri(), wait_until="load")
        page.emulate_media(media="print")
        page.pdf(path=str(pdf_path), print_background=True, format="A4",
                 margin={"top": "12mm", "bottom": "14mm", "left": "10mm", "right": "10mm"})
    finally:
        page.close()
    return pdf_path


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright not installed. Run: py -m pip install playwright markdown  "
                 "&&  py -m playwright install chromium")
    import tempfile
    with tempfile.TemporaryDirectory(prefix="padb_pdf_build_") as tmp:
        out_dir = Path(tmp)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
                try:
                    for md_name in DOCS:
                        pdf_path = build_one(md_name, browser, out_dir)
                        print(f"Wrote {pdf_path.name} ({pdf_path.stat().st_size:,} bytes)")
                finally:
                    browser.close()
        except Exception as exc:  # most likely: Chromium not downloaded
            sys.exit(f"PDF build failed ({exc}).\n"
                     f"If Chromium is missing, run: py -m playwright install chromium")


if __name__ == "__main__":
    main()
