r"""build_viewer.py -- build PADB_Viewer.exe (PyInstaller onefile).

Produces one self-contained Windows .exe of padb_viewer.py so users WITHOUT a
Python install can view giant datasets: drop PADB_Viewer.exe into a results
folder that has a `.parquet` sidecar (padb_v2.py writes one for compare/large
jobs) and double-click it -- it serves that folder's parquet at
http://localhost and opens the browser. See padb_viewer.py for why a local
server (not a static WASM page) is the workable approach off a \\share.

    py build_viewer.py            # -> dist/PADB_Viewer.exe
    py build_viewer.py --out D:\somewhere

Requires PyInstaller (`pip install pyinstaller`). The result is ~130 MB
(bundles pandas/pyarrow/plotly/flask). Console is kept on purpose so a
double-click shows the serving URL and any error. Build artifacts (dist/,
_pyi_build/, *.exe) are git-ignored -- rebuild rather than commit the binary.
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(HERE / "dist"),
                    help="Output directory for PADB_Viewer.exe (default: ./dist)")
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    work = out.parent / "_pyi_build"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--name", "PADB_Viewer",
        # plotly.offline.get_plotlyjs() reads plotly/package_data/plotly.min.js
        "--collect-data", "plotly",
        "--hidden-import", "plotly.offline",
        # pyarrow ships compiled arrow DLLs the parquet reader needs
        "--collect-all", "pyarrow",
        "--noconfirm", "--clean",
        "--distpath", str(out),
        "--workpath", str(work),
        "--specpath", str(work),
        str(HERE / "padb_viewer.py"),
    ]
    print("Running:", " ".join(cmd), flush=True)
    rc = subprocess.call(cmd)
    exe = out / "PADB_Viewer.exe"
    if rc == 0 and exe.exists():
        print(f"\nOK -> {exe}  ({exe.stat().st_size / 1e6:.0f} MB)")
        print("Drop it into a results folder with a .parquet and double-click.")
    else:
        print(f"\nBUILD FAILED (rc={rc}); PADB_Viewer.exe not found.", file=sys.stderr)
        rc = rc or 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
