r"""make_analysis_bundle.py -- package padb-tools' ANALYSIS layer as a self-contained folder (v3.0).

The bundle is what another tool (padbloader) ships to get padb-tools' analysis without any of its
extraction or scheduling: `padb_analyze.py` (the stable entry point), the interactive viewer, `padb_core`
and every module they import -- computed by walking the imports from the entry points -- and nothing else.
It REFUSES to build if that closure reaches an extraction/scheduling module (padb_run, padb_batch,
padb_scheduler, padb_make_job, padb_make_v2_job, padb_convert_site, padb_config, the webapp): the
separation is enforced at packaging time, not just by convention.

    py make_analysis_bundle.py <dest-folder> [--no-viewer] [--zip]

Writes the files, `requirements-analysis.txt`, `BUNDLE_MANIFEST.json` (version + sha256 of every file) and
`README_BUNDLE.txt` (how to call it). qa/qa_separation.py runs real analyses FROM a freshly built bundle in a
different working directory, so a missing file or a hidden extraction dependency fails the gate.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

EXTRACTION = {"padb_run", "padb_batch", "padb_scheduler", "padb_make_job", "padb_make_v2_job",
              "padb_convert_site", "padb_config", "padb_sites", "webapp"}
ENTRIES = ["padb_analyze.py"]
VIEWER_ENTRIES = ["padb_viewer.py"]
REQUIREMENTS = """# Python packages the padb-tools analysis layer needs
numpy
pandas
pyarrow
scipy
plotly
duckdb            # the engine behind padb_core and the viewer
flask             # only for the interactive viewer (padb_viewer.py)
# optional: playwright + pypdf   (the multi-view PDF report; run `playwright install chromium`)
"""
README = """PADB analysis bundle ({version})

Data in, artifacts out -- no extraction, no scheduling, no PADB-R, no Oracle.

  py padb_analyze.py request.json --result result.json

request.json   {{"input": "extract.parquet", "out": "results", "views": ["scatter","summary"]}}
               input = padbloader parquet | padb-tools parquet | PADB scatter .csv
               optional: algorithms, x_col, limits_file, publish_to, pdf_report
result.json    {{"schema": "padb-analysis-result/1", "ok": true, "analyses": [{{... "index": ".../index.html"}}]}}
exit code      0 ok, 1 an analysis failed, 2 bad request.   Progress text goes to stderr.

Interactive viewer for datasets too big for a single HTML page:   py padb_viewer.py <folder-or-parquet>

Install the packages in requirements-analysis.txt first. Full schema: see the docstring of padb_analyze.py.
"""


class BoundaryError(RuntimeError):
    """The analysis closure reaches an extraction/scheduling module."""


def _local_imports(path: Path, root: Path) -> set:
    """Top-level names imported anywhere in `path` (incl. lazy imports inside functions) that are repo
    modules/packages under `root`."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {n for n in names if (root / (n + ".py")).is_file() or (root / n / "__init__.py").is_file()}


def closure(entries, root: Path = HERE) -> list:
    """Repo files (relative, sorted) reachable by import from `entries`. Raises BoundaryError if any
    extraction/scheduling module is reachable."""
    files, todo, seen = set(), list(entries), set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        p = root / rel
        files.add(rel)
        for n in _local_imports(p, root):
            if n in EXTRACTION:
                raise BoundaryError("%s imports the extraction/scheduling module %r -- the analysis layer must not "
                                    "depend on it (docs/V3_PLAN.md, Separation)" % (rel, n))
            if (root / n / "__init__.py").is_file():
                for q in sorted((root / n).glob("*.py")):
                    todo.append(str(q.relative_to(root)).replace("\\", "/"))
            else:
                todo.append(n + ".py")
    return sorted(files)


def build_bundle(dest, viewer: bool = True, root: Path = HERE) -> dict:
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError("destination is not empty: %s" % dest)
    files = closure(ENTRIES + (VIEWER_ENTRIES if viewer else []), root)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for rel in files:
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, out)
        manifest[rel] = hashlib.sha256(out.read_bytes()).hexdigest()
    try:
        sys.path.insert(0, str(root))
        import padb_core
        version = padb_core.__version__
    except Exception:  # noqa: BLE001
        version = "unknown"
    finally:
        if sys.path and sys.path[0] == str(root):
            sys.path.pop(0)
    (dest / "requirements-analysis.txt").write_text(REQUIREMENTS, encoding="utf-8")
    (dest / "README_BUNDLE.txt").write_text(README.format(version=version), encoding="utf-8")
    info = {"bundle": "padb-analysis", "version": version, "entry_points": ENTRIES + (VIEWER_ENTRIES if viewer else []),
            "files": manifest}
    (dest / "BUNDLE_MANIFEST.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dest")
    ap.add_argument("--no-viewer", action="store_true")
    ap.add_argument("--zip", action="store_true", help="also write <dest>.zip")
    a = ap.parse_args(argv)
    try:
        info = build_bundle(a.dest, viewer=not a.no_viewer)
    except (BoundaryError, FileExistsError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    print("bundle %s: %d files -> %s" % (info["version"], len(info["files"]), a.dest))
    if a.zip:
        zp = Path(str(a.dest).rstrip("\\/") + ".zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(Path(a.dest).rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(a.dest))
        print("zip -> %s" % zp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
