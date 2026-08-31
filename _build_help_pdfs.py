"""Regenerate the local PDF copies of the *.md help docs (not tracked in git --
see .gitignore -- these are personal, local-only build artifacts). Converts
each Markdown source to styled HTML, then uses headless Edge's own
--print-to-pdf (the same Edge binary already used throughout this repo's own
headless verification workflow) to render that HTML to PDF. Run again any
time the underlying .md files change; safe to delete the output PDFs and
re-run, they're not referenced by anything else in the pipeline.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

TOOLS_DIR = Path(__file__).resolve().parent
DOCS = [
    "GETTING_STARTED.md",
    "Interactive_Plots_User_Guide.md",
    "PADB_Tools_Guide.md",
]

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
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


def find_edge() -> str:
    for c in EDGE_CANDIDATES:
        if Path(c).exists():
            return c
    sys.exit("Edge not found in any known location")


def build_one(md_name: str, edge: str, out_dir: Path) -> Path:
    md_path = TOOLS_DIR / md_name
    text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=MD_EXTENSIONS)
    title = md_name.replace(".md", "").replace("_", " ")
    html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title>{CSS}</head><body>{body}</body></html>"
    html_path = out_dir / (md_path.stem + ".html")
    html_path.write_text(html, encoding="utf-8")

    pdf_path = TOOLS_DIR / (md_path.stem + ".pdf")
    profile_dir = out_dir / (md_path.stem + "_profile")
    cmd = [
        edge, "--headless", "--disable-gpu", "--disable-crash-reporter",
        f"--user-data-dir={profile_dir}",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={pdf_path}",
        f"file:///{html_path.as_posix()}",
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return pdf_path


def main() -> None:
    edge = find_edge()
    with tempfile.TemporaryDirectory(prefix="padb_pdf_build_") as tmp:
        out_dir = Path(tmp)
        for md_name in DOCS:
            pdf_path = build_one(md_name, edge, out_dir)
            size = pdf_path.stat().st_size
            print(f"Wrote {pdf_path.name} ({size:,} bytes)")


if __name__ == "__main__":
    main()
