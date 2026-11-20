"""padb_csv_to_parquet.py -- convert PADB CSV exports to compact zstd Parquet.

Streams each CSV in 64 MB blocks (bounded memory, so multi-GB exports work) and
writes <name>.parquet next to it (or into --out).  Same writer the interactive
build uses for its parquet sidecar, so the result opens in padb_viewer.py.
The CSV is converted as-is (columns and values unchanged); this is an encoding
change only -- typically 50-140x smaller.

Usage:
  py padb_csv_to_parquet.py <file.csv|folder> [more ...] [--out DIR_OR_FILE]
                            [--force] [--verify]

  folder     every *.csv directly in it (not recursive); names starting with '_'
             (the tool's own temp files) are skipped
  --out      output directory (or a .parquet file name when converting one CSV);
             default: next to the source
  --force    overwrite an existing .parquet (default: skip it)
  --verify   read the parquet back: row count + column names must match the
             streamed CSV, and the first 1000 rows must equal the CSV's

Exit 0 = every file converted/skipped OK, 1 = any failure.  ASCII-only output.
"""
import argparse
import sys
from pathlib import Path


def _targets(paths):
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            for f in sorted(pp.glob("*.csv")):
                if not f.name.startswith("_"):
                    yield f
        else:
            yield pp


def _verify(csv_path: Path, out_path: Path, rows: int) -> str:
    """Return '' when the parquet matches the CSV, else a reason."""
    import csv
    import pyarrow.parquet as pq
    meta = pq.ParquetFile(str(out_path))
    if meta.metadata.num_rows != rows:
        return "row count %d != %d" % (meta.metadata.num_rows, rows)
    # Compare the first rows cell-by-cell against the CSV's own text (stdlib csv,
    # not the converter's reader).  Typed columns are compared by value, so an
    # inferred timestamp / int / float is not a false mismatch.
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        if list(meta.schema_arrow.names) != header:
            return "column names differ"
        head_csv = []
        for r in rd:
            head_csv.append(r + [""] * (len(header) - len(r)))
            if len(head_csv) >= 1000:
                break
    head_pq = next(meta.iter_batches(batch_size=1000)).to_pylist()
    if len(head_pq) != len(head_csv):
        return "first-rows count differs"
    for ri, (crow, prow) in enumerate(zip(head_csv, head_pq)):
        for name, s in zip(header, crow):
            if not _cell_equal(s, prow[name]):
                return "row %d column %r: csv %r != parquet %r" % (ri, name, s, prow[name])
    return ""


def _cell_equal(s: str, v) -> bool:
    import datetime as dt
    import math
    if s == "":
        return v is None or v == ""
    if isinstance(v, bool):
        return s.lower() == str(v).lower()
    try:
        if isinstance(v, int):
            return int(s) == v
        if isinstance(v, float):
            return (math.isnan(v) and s.lower() == "nan") or float(s) == v
        if isinstance(v, dt.datetime):
            return dt.datetime.fromisoformat(s) == v
    except ValueError:
        return False
    return v == s


def _pandas_stream(csv_path: Path, out_path: Path):
    """Fallback for ragged CSVs (some tools drop a trailing empty field, e.g.
    padbloader's CSV: 15 fields on rows whose last column is blank), which the
    strict Arrow reader rejects but pandas pads.  Chunked; the first chunk fixes
    the schema, so a later chunk that can't be cast raises (rather than silently
    changing a column's type).  Returns (rows, csv_MB, parquet_MB)."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    writer, schema, rows = None, None, 0
    try:
        for chunk in pd.read_csv(csv_path, chunksize=500_000, low_memory=False):
            tbl = pa.Table.from_pandas(chunk, schema=schema, preserve_index=False)
            if writer is None:
                schema = tbl.schema
                writer = pq.ParquetWriter(str(out_path), schema,
                                          compression="zstd", compression_level=9)
            writer.write_table(tbl)
            rows += len(chunk)
    finally:
        if writer is not None:
            writer.close()
    smb = csv_path.stat().st_size / 1e6
    dmb = out_path.stat().st_size / 1e6 if out_path.exists() else 0.0
    return rows, smb, dmb


def convert(csv_path, out_path=None, force=False, verify=False):
    """Convert one CSV.  Returns (status, rows, csv_MB, parquet_MB, note) with
    status in {'ok','skipped','FAILED'}."""
    csv_path = Path(csv_path)
    out_path = Path(out_path) if out_path else csv_path.with_suffix(".parquet")
    if not csv_path.is_file():
        return "FAILED", 0, 0.0, 0.0, "not a file"
    if out_path.exists() and not force:
        return "skipped", 0, 0.0, 0.0, "exists (use --force)"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import padb_v2
    tmp = out_path.with_name(out_path.name + ".part")
    try:
        path_note = ""
        try:
            rows, smb, dmb = padb_v2._csv_to_parquet(csv_path, tmp)
        except Exception as first:      # noqa: BLE001
            import pyarrow as pa
            if not isinstance(first, pa.ArrowInvalid):
                raise
            tmp.unlink(missing_ok=True)
            rows, smb, dmb = _pandas_stream(csv_path, tmp)
            path_note = "(ragged rows -> pandas path) "
        if rows == 0:
            tmp.unlink(missing_ok=True)
            return "FAILED", 0, smb, 0.0, "CSV has no data rows"
        why = _verify(csv_path, tmp, rows) if verify else ""
        if why:
            tmp.unlink(missing_ok=True)
            return "FAILED", rows, smb, dmb, "verify: " + why
        tmp.replace(out_path)          # atomic: never leave a half-written .parquet
        return "ok", rows, smb, dmb, path_note + ("verified" if verify else "")
    except Exception as e:             # noqa: BLE001 -- report, keep going
        tmp.unlink(missing_ok=True)
        return "FAILED", 0, 0.0, 0.0, "%s: %s" % (type(e).__name__, e)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args(argv)
    files = list(_targets(a.inputs))
    if not files:
        print("no CSV files found")
        return 1
    out = Path(a.out) if a.out else None
    single_file_out = out is not None and out.suffix.lower() == ".parquet"
    if single_file_out and len(files) != 1:
        print("--out names a .parquet file but %d CSVs were given" % len(files))
        return 1
    bad = 0
    for f in files:
        if single_file_out:
            dest = out
        elif out is not None:
            dest = out / (f.stem + ".parquet")
        else:
            dest = None
        st, rows, smb, dmb, note = convert(f, dest, a.force, a.verify)
        if st == "ok":
            print("ok       %s: %s rows, %.1f MB -> %.1f MB (%.0fx) %s" % (
                f.name, format(rows, ","), smb, dmb, smb / dmb if dmb else 0, note))
        else:
            print("%-8s %s: %s" % (st, f.name, note))
            bad += st == "FAILED"
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
