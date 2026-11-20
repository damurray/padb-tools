"""qa_csv_to_parquet.py -- QA for padb_csv_to_parquet.py (CSV -> zstd Parquet).

Browser-free and deterministic (seeded).  The converter must be an *encoding
change only*: same rows, same column order, same values.  Checks:

  FIDELITY   an independent comparer (stdlib `csv` vs the parquet, NOT the
             converter's own reader) over a seeded fuzz CSV: ints, awkward floats,
             unicode / commas / quotes / embedded newlines, blank cells, datetimes,
             CRLF line endings, a UTF-8 BOM.
  STREAMING  column count/order and row count preserved; a type that changes
             after the first chunk of the ragged-row (pandas) path fails LOUDLY
             rather than silently changing a column's type.
  VERIFY     --verify catches a dropped row, reordered columns and float32 loss.
  ATOMIC     a conversion that dies half-way leaves no .parquet/.part and does
             NOT clobber an existing .parquet.
  CLI        exit codes, folder mode (skips '_'-prefixed temp files), --out dir
             / file, skip-without-force, header-only CSV.
  TEETH      the fidelity comparer and --verify are run against deliberately
             BROKEN converters (drop last row / float32 / reversed columns); each
             must be detected -- a QA that cannot fail proves nothing.
  SWEEP      optional: --sweep DIR converts + verifies every real CSV in DIR.

Run:  py qa_csv_to_parquet.py [--sweep DIR]   (exit 1 on any FAIL)
"""
import contextlib
import csv
import datetime as dt
import io
import math
import random
import sys
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

import padb_csv_to_parquet as c2p
import padb_v2

_PASS: list = []
_FAIL: list = []


def check(desc: str, cond: bool, detail: str = "") -> None:
    if cond:
        _PASS.append(desc)
        print("  PASS  " + desc)
    else:
        _FAIL.append(desc)
        print("  FAIL  " + desc + (" -- " + detail if detail else ""))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
_NASTY = ["plain", "with,comma", 'with "quotes"', "two  spaces  inside", " lead", "trail ",
          "uniéü±µ", "line1\nline2", "a;b|c", "Key: v  Other: w", "x" * 400]


def make_fuzz_csv(path: Path, rows=400, seed=7, crlf=False, bom=False) -> None:
    rnd = random.Random(seed)
    base = dt.datetime(2026, 1, 1)
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator="\r\n" if crlf else "\n")
    w.writerow(["id", "val", "txt", "when", "maybe"])
    for i in range(rows):
        val = rnd.choice([0.1, -123456.789012345, 1e-9, 3.0, rnd.random() * 1e3,
                          -rnd.random(), 1.2345678901234567e5])
        maybe = "" if rnd.random() < .3 else repr(rnd.random() * 100)
        w.writerow([i, repr(val), rnd.choice(_NASTY),
                    (base + dt.timedelta(seconds=rnd.randrange(10 ** 7))).strftime("%Y-%m-%d %H:%M:%S"),
                    maybe])
    data = out.getvalue().encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)


def read_csv_rows(path: Path):
    """Independent CSV reader (stdlib).  Returns (header, rows)."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        rows = [r + [""] * (len(header) - len(r)) for r in rd]    # pad ragged rows
    return header, rows


def compare(csv_path: Path, pq_path: Path) -> list:
    """Return a list of problems (empty == the parquet faithfully holds the CSV)."""
    header, rows = read_csv_rows(csv_path)
    tbl = pq.read_table(str(pq_path))
    probs = []
    if tbl.column_names != header:
        probs.append("columns %s != %s" % (tbl.column_names, header))
        return probs
    if tbl.num_rows != len(rows):
        probs.append("rows %d != %d" % (tbl.num_rows, len(rows)))
        return probs
    cols = {n: tbl.column(n).to_pylist() for n in header}
    for ci, name in enumerate(header):
        for ri, row in enumerate(rows):
            s, v = row[ci], cols[name][ri]
            if s == "":
                ok = v is None or v == ""
            elif isinstance(v, bool):
                ok = s.lower() == str(v).lower()
            elif isinstance(v, int):
                ok = int(s) == v
            elif isinstance(v, float):
                ok = (math.isnan(v) and s.lower() == "nan") or float(s) == v
            elif isinstance(v, dt.datetime):
                ok = dt.datetime.fromisoformat(s) == v
            else:
                ok = v == s
            if not ok:
                probs.append("row %d col %s: csv %r != parquet %r" % (ri, name, s, v))
                if len(probs) >= 5:
                    return probs
    return probs


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = c2p.main(argv)
    return rc, buf.getvalue()


# ---------------------------------------------------------------------------
# broken converters (teeth)
# ---------------------------------------------------------------------------
def _mutant(kind):
    orig = padb_v2._csv_to_parquet

    def broken(csv_path, out_path):
        rows, smb, dmb = orig(csv_path, out_path)
        t = pq.read_table(str(out_path))
        if kind == "drop_last_row":
            t = t.slice(0, t.num_rows - 1)
        elif kind == "float32":
            t = pa.table({n: (t.column(n).cast(pa.float32()) if pa.types.is_floating(t.schema.field(n).type)
                              else t.column(n)) for n in t.column_names})
        elif kind == "reverse_columns":
            t = t.select(list(reversed(t.column_names)))
        pq.write_table(t, str(out_path), compression="zstd")
        return rows, smb, dmb
    return orig, broken


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_fidelity(td: Path):
    print("\n[FIDELITY]")
    for label, kw in (("LF", {}), ("CRLF", {"crlf": True}), ("UTF-8 BOM", {"bom": True})):
        src = td / ("fz_%s.csv" % label.replace(" ", "").replace("-", ""))
        make_fuzz_csv(src, **kw)
        with quiet():
            st = c2p.convert(src)[0]
        probs = compare(src, src.with_suffix(".parquet"))
        check("fuzz round-trip (%s): converts, every cell matches an independent CSV read" % label,
              st == "ok" and not probs, "; ".join(probs[:2]))
    src = td / "fz_LF.csv"
    t = pq.read_table(str(src.with_suffix(".parquet")))
    check("fuzz: embedded-newline and unicode cells survive byte-exact",
          "line1\nline2" in set(t.column("txt").to_pylist())
          and "uniéü±µ" in set(t.column("txt").to_pylist()))
    check("fuzz: blank numeric cells are null, not 0 / NaN-as-text",
          any(v is None for v in t.column("maybe").to_pylist()))


def test_streaming(td: Path):
    print("\n[STREAMING]")
    # ragged file (pandas fallback) whose column changes type AFTER the first 500k-row chunk
    rag = td / "rag_type.csv"
    with open(rag, "w", newline="") as fh:
        fh.write("a,b,c\n")
        for i in range(520_000):
            fh.write("%d,x\n" % i)             # 2 fields: ragged -> forces the pandas path
        fh.write("1.5,y,z\n")
    with quiet():
        st, rows, _, _, note = c2p.convert(rag)
    # int column 'a' stays int in chunk 1; the late 1.5 is in chunk 2 -> must not silently
    # become a different type.  Either it fails loudly, or the stored values stay exact.
    if st == "ok":
        v = pq.read_table(str(rag.with_suffix(".parquet"))).column("a").to_pylist()
        loud_or_exact = (len(v) == 520_001 and v[-1] == 1.5)
    else:
        loud_or_exact = (not rag.with_suffix(".parquet").exists()) and "FAILED" == st
    check("ragged path: a type change after the first chunk fails loudly or stays value-exact "
          "(never silently truncates 1.5)", loud_or_exact, "status=%s note=%s" % (st, note))
    # column order / count
    src = td / "order.csv"
    src.write_text("z,a,m\n1,2,3\n4,5,6\n", encoding="utf-8")
    with quiet():
        c2p.convert(src)
    check("column order and count preserved (not alphabetised)",
          pq.read_table(str(src.with_suffix(".parquet"))).column_names == ["z", "a", "m"])
    # >1 Arrow batch: enough rows to cross several internal batches
    big = td / "multi.csv"
    with open(big, "w", newline="") as fh:
        fh.write("i,v\n")
        for i in range(300_000):
            fh.write("%d,%r\n" % (i, i * 0.5))
    with quiet():
        st = c2p.convert(big, verify=True)[0]
    t = pq.read_table(str(big.with_suffix(".parquet")))
    check("300k-row multi-batch file: row count exact, first/last rows correct",
          st == "ok" and t.num_rows == 300_000 and t.column("i")[0].as_py() == 0
          and t.column("i")[299_999].as_py() == 299_999 and t.column("v")[299_999].as_py() == 149999.5)


def test_verify(td: Path):
    print("\n[VERIFY]")
    src = td / "vf.csv"
    make_fuzz_csv(src, rows=200, seed=3)
    with quiet():
        c2p.convert(src)
    good = src.with_suffix(".parquet")
    n = pq.ParquetFile(str(good)).metadata.num_rows
    check("verify accepts a faithful parquet", c2p._verify(src, good, n) == "")
    t = pq.read_table(str(good))
    bad = td / "vf_bad.parquet"
    pq.write_table(t.slice(0, t.num_rows - 1), str(bad))
    check("verify rejects a dropped row", c2p._verify(src, bad, n) != "")
    pq.write_table(t.select(list(reversed(t.column_names))), str(bad))
    check("verify rejects reordered columns", c2p._verify(src, bad, n) != "")
    f32 = pa.table({nm: (t.column(nm).cast(pa.float32()) if nm == "val" else t.column(nm))
                    for nm in t.column_names})
    pq.write_table(f32, str(bad))
    check("verify rejects float32 precision loss in the first rows", c2p._verify(src, bad, n) != "")


def test_atomic(td: Path):
    print("\n[ATOMIC]")
    src = td / "at.csv"
    make_fuzz_csv(src, rows=50, seed=5)
    out = src.with_suffix(".parquet")
    orig = padb_v2._csv_to_parquet

    def dies(csv_path, out_path):
        Path(out_path).write_bytes(b"PAR1 half written")
        raise OSError("disk full (simulated)")
    padb_v2._csv_to_parquet = dies
    try:
        with quiet():
            st = c2p.convert(src)[0]
    finally:
        padb_v2._csv_to_parquet = orig
    check("a conversion that dies half-way is FAILED and leaves no .parquet / .part",
          st == "FAILED" and not out.exists() and not Path(str(out) + ".part").exists())
    with quiet():
        c2p.convert(src)
    before = out.read_bytes()
    padb_v2._csv_to_parquet = dies
    try:
        with quiet():
            st = c2p.convert(src, force=True)[0]
    finally:
        padb_v2._csv_to_parquet = orig
    check("a failed --force overwrite leaves the EXISTING .parquet byte-identical",
          st == "FAILED" and out.read_bytes() == before)


def test_cli(td: Path):
    print("\n[CLI]")
    d = td / "cli"
    d.mkdir()
    (d / "one.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (d / "two.csv").write_text("a,b\n3,4\n", encoding="utf-8")
    (d / "_v2_tmp_scatter.csv").write_text("a,b\n9,9\n", encoding="utf-8")
    rc, txt = cli([str(d)])
    names = sorted(p.name for p in d.glob("*.parquet"))
    check("folder mode converts every CSV and skips '_'-prefixed temp files; exit 0",
          rc == 0 and names == ["one.parquet", "two.parquet"], "%s %s" % (rc, names))
    rc, txt = cli([str(d)])
    check("re-run skips existing parquets; exit 0", rc == 0 and txt.count("skipped") == 2)
    outd = td / "outdir"
    rc, _ = cli([str(d / "one.csv"), "--out", str(outd)])
    check("--out DIR writes <stem>.parquet there", rc == 0 and (outd / "one.parquet").is_file())
    rc, _ = cli([str(d / "one.csv"), "--out", str(td / "named.parquet")])
    check("--out FILE.parquet names a single output", rc == 0 and (td / "named.parquet").is_file())
    rc, _ = cli([str(d / "one.csv"), str(d / "two.csv"), "--out", str(td / "x.parquet")])
    check("--out FILE.parquet with 2 inputs is refused; exit 1", rc == 1)
    (d / "empty.csv").write_text("a,b\n", encoding="utf-8")
    rc, txt = cli([str(d / "empty.csv")])
    check("header-only CSV: FAILED, exit 1, no parquet",
          rc == 1 and "FAILED" in txt and not (d / "empty.parquet").exists())
    rc, _ = cli([str(td / "no_such_dir_or_file.csv")])
    check("missing input: exit 1", rc == 1)
    emptydir = td / "emptydir"
    emptydir.mkdir()
    rc, _ = cli([str(emptydir)])
    check("folder with no CSVs: exit 1", rc == 1)


def test_teeth(td: Path):
    print("\n[TEETH] broken converters must be caught")
    src = td / "teeth.csv"
    make_fuzz_csv(src, rows=200, seed=11)
    for kind in ("drop_last_row", "float32", "reverse_columns"):
        orig, broken = _mutant(kind)
        out = td / ("teeth_%s.parquet" % kind)
        padb_v2._csv_to_parquet = broken
        try:
            with quiet():
                c2p.convert(src, out, force=True)
                st_v = c2p.convert(src, td / ("teeth_v_%s.parquet" % kind), force=True, verify=True)[0]
        finally:
            padb_v2._csv_to_parquet = orig
        check("comparer detects '%s'" % kind, bool(compare(src, out)))
        check("--verify refuses '%s' (and writes no parquet)" % kind,
              st_v == "FAILED" and not (td / ("teeth_v_%s.parquet" % kind)).exists())
    check("comparer is quiet on the real converter (control)", not compare(src, _fresh(src, td)))


def _fresh(src: Path, td: Path) -> Path:
    out = td / "control.parquet"
    with quiet():
        c2p.convert(src, out, force=True)
    return out


def test_sweep(root: Path):
    print("\n[SWEEP] %s" % root)
    files = [p for p in sorted(root.glob("*.csv")) if not p.name.startswith("_")]
    if not files:
        print("  (no CSVs found)")
        return
    with tempfile.TemporaryDirectory() as td:
        for f in files:
            with quiet():
                st, rows, smb, dmb, note = c2p.convert(f, Path(td) / (f.stem + ".parquet"), verify=True)
            check("sweep: %s (%.0f MB)" % (f.name, smb), st == "ok", note)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    sweep = Path(argv[argv.index("--sweep") + 1]) if "--sweep" in argv else None
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        test_fidelity(td)
        test_streaming(td)
        test_verify(td)
        test_atomic(td)
        test_cli(td)
        test_teeth(td)
    if sweep:
        test_sweep(sweep)
    print("\n" + "=" * 60)
    print("  PASS: %d    FAIL: %d" % (len(_PASS), len(_FAIL)))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
