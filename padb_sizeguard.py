"""padb_sizeguard.py -- know BEFORE loading a parquet whether it will fit in memory, and say so plainly.

v3.0 big-data hardening. The conversion (`padb_loader_adapter`) and the HTML builds (`padb_v2`) load a table whole into
pandas. On a very large pull that does not fail politely: the machine thrashes or the process is killed, taking whatever
else was running with it. The footer of a parquet file says how many rows and how many (uncompressed) bytes it holds
without reading any data, which is enough to predict that, so this module refuses early with a message that says what to
do instead.

MEASURED (2026-11-24, psutil peak resident memory of the whole process tree, on a real 302,530-row / 62-column
padbloader file):
    convert  (padb_loader_adapter, whole file in pandas)       1,105 MB  =  6.2 x the footer's uncompressed bytes
    analyze  (padb_v2, SCATTER view only, one process)         2,918 MB  =  9.6 KB per row
    analyze  (padb_v2, all seven views, parallel workers)      6,151 MB  = 20.3 KB per row
The constants below are those numbers rounded UP; they are an estimate to refuse on, not a promise.

A refusal never touches data. `PADB_SIZE_GUARD=off` disables the guard; `PADB_MAX_LOAD_GB=<n>` replaces "what is free"
with a fixed budget (useful on a shared machine or to test).
"""
from __future__ import annotations

import os
from pathlib import Path

CONVERT_FACTOR = 6.6          # x the file's uncompressed bytes (measured 6.2)
ANALYZE_BYTES_PER_ROW_LIGHT = 10_500  # the scatter view alone (measured 9.6 KB per row)
ANALYZE_BYTES_PER_ROW_FULL = 28_000   # anything else, with parallel workers. Measured: 20.3 KB/row (7 views, 302k rows, 14 x);
                                      # 27.3 KB/row (scatter+boxplot, 283,866 rows over 1,510 x: 7.7 GB peak, 876 s) -- the
                                      # aggregate views grow with x-values x conditions, so the wide-sweep figure is the safe one
BASE_BYTES = 350_000_000      # the interpreter and its libraries
WARN_FRACTION = 0.50          # estimate above this share of the memory available -> warn
REFUSE_FRACTION_AVAIL = 0.85  # ... above this share of what is available -> refuse
REFUSE_FRACTION_TOTAL = 0.60  # ... or above this share of ALL the RAM -> refuse (even if it is free right now)


class TooBig(Exception):
    """Raised when a load would not fit. `.assessment` is the dict `assess` returned; str() is the user message."""

    def __init__(self, assessment: dict):
        self.assessment = assessment
        super().__init__(assessment["message"])


def memory_status():
    """(total_bytes, available_bytes) of this machine's RAM, or (None, None) if it cannot be read."""
    try:
        import psutil
        v = psutil.virtual_memory()
        return int(v.total), int(v.available)
    except ImportError:
        pass
    if os.name == "nt":
        try:
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = _MS()
            ms.dwLength = ctypes.sizeof(ms)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return int(ms.ullTotalPhys), int(ms.ullAvailPhys)
        except Exception:  # noqa: BLE001
            return None, None
    try:
        info = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)
        kb = lambda k: int(info[k].split()[0]) * 1024
        return kb("MemTotal"), kb("MemAvailable")
    except (OSError, KeyError, ValueError):
        return None, None


def footprint(path) -> dict:
    """Rows, row groups, file bytes and the footer's UNCOMPRESSED byte count -- from the footer only."""
    import pyarrow.parquet as pq
    md = pq.ParquetFile(str(path)).metadata
    return {"rows": int(md.num_rows), "row_groups": int(md.num_row_groups), "file_bytes": os.path.getsize(path),
            "uncompressed_bytes": int(sum(md.row_group(i).total_byte_size for i in range(md.num_row_groups)))}


def _light(views) -> bool:
    """Only the scatter view is cheap per row; every aggregate view (boxplot, summary, ...) costs far more on a wide sweep."""
    return bool(views) and all(str(v) == "scatter" for v in views)


def estimate_bytes(op: str, fp: dict, views=None) -> int:
    """Peak memory the operation would need. op: 'convert' (a padbloader parquet -> PADB tables, whole file in pandas)
    or 'analyze' (padb_v2 building the HTML views from a PADB-style parquet; `views` = the views asked for -- one or
    two is the light estimate, anything else the full default set with parallel workers)."""
    if op == "convert":
        return int(BASE_BYTES + CONVERT_FACTOR * fp["uncompressed_bytes"])
    if op == "analyze":
        light = _light(views)
        return int(BASE_BYTES + (ANALYZE_BYTES_PER_ROW_LIGHT if light else ANALYZE_BYTES_PER_ROW_FULL) * fp["rows"])
    raise ValueError("op must be 'convert' or 'analyze'")


def _gb(b) -> float:
    return b / 1e9


def advice(op: str, path, a: dict) -> str:
    name = os.path.basename(str(path))
    if op == "convert":
        return ("The conversion normally keeps the whole file in memory. For a file this size it streams instead "
                "(automatic: `stream=auto`), reading it a slice at a time, so nothing needs to be done unless you "
                "forced the in-memory path (`--stream off`).")
    return ("This is too many rows to build self-contained HTML pages from at once. Options: (1) open it in the parquet "
            "viewer, which reads it out of core: `py padb_viewer.py \"%s\"`; (2) analyse a SLICE -- one site, a few "
            "serials, one temperature -- with `--server`, `--serials` and `--steps` on `padb_analyze` / "
            "`padb_loader_adapter` (about %s rows would fit)." % (path, format(a["max_rows"], ",")))


def max_rows(op: str, views=None, mem=None, env=None) -> int:
    """How many rows `op` can handle on this machine right now (the safe budget divided by the per-row cost) -- used to
    size slices of a file that is too big for one build."""
    env = os.environ if env is None else env
    total, avail = mem if mem is not None else memory_status()
    cap = env.get("PADB_MAX_LOAD_GB")
    if cap:
        try:
            avail = float(cap) * 1e9
            total = max(total or 0, avail)
        except ValueError:
            pass
    if total is None or avail is None:
        return 500_000
    budget = min(REFUSE_FRACTION_AVAIL * avail, REFUSE_FRACTION_TOTAL * total)
    if op == "analyze":
        per = ANALYZE_BYTES_PER_ROW_LIGHT if _light(views) else ANALYZE_BYTES_PER_ROW_FULL
    else:
        per = CONVERT_FACTOR * 620          # ~620 uncompressed bytes per padbloader row (measured 609-616)
    return int(max(50_000, (budget - BASE_BYTES) / per))


def assess(path, op: str, mem=None, env=None, views=None) -> dict:
    """Judge whether `op` on `path` will fit. -> {ok, level ('ok'|'warn'|'refuse'), op, rows, file_mb, est_gb, avail_gb,
    total_gb, budget_gb, max_rows, message, advice}. `mem` = (total, available) to override the machine (tests)."""
    env = os.environ if env is None else env
    fp = footprint(path)
    est = estimate_bytes(op, fp, views)
    total, avail = mem if mem is not None else memory_status()
    cap = env.get("PADB_MAX_LOAD_GB")
    if cap:
        try:
            avail = float(cap) * 1e9
            total = max(total or 0, avail)
        except ValueError:
            pass
    off = str(env.get("PADB_SIZE_GUARD", "")).lower() in ("off", "0", "false", "no")
    per_row = (est - BASE_BYTES) / max(fp["rows"], 1)
    if total is None or avail is None or off:
        level = "ok"
        budget = None
    else:
        budget = min(REFUSE_FRACTION_AVAIL * avail, REFUSE_FRACTION_TOTAL * total)
        level = "refuse" if est > budget else ("warn" if est > WARN_FRACTION * avail else "ok")
    max_rows = int(max(0, (budget or 0) - BASE_BYTES) / per_row) if (budget is not None and per_row > 0) else fp["rows"]
    a = {"ok": level != "refuse", "level": level, "op": op, "rows": fp["rows"], "file_mb": round(fp["file_bytes"] / 1e6, 1),
         "est_gb": round(_gb(est), 2), "avail_gb": None if avail is None else round(_gb(avail), 2),
         "total_gb": None if total is None else round(_gb(total), 2),
         "budget_gb": None if budget is None else round(_gb(budget), 2), "max_rows": max_rows}
    what = {"convert": "converting", "analyze": "building the HTML views from"}[op]
    if level == "ok":
        a["message"] = "%s: %s rows, estimated %.1f GB for %s it (%.1f GB available) -- fine." % (
            os.path.basename(str(path)), format(fp["rows"], ","), _gb(est), what, _gb(avail or 0))
    else:
        a["message"] = ("%s: %s rows. %s it would need about %.1f GB of memory; "
                        "this machine has %.1f GB available (%.1f GB in total)%s."
                        % (os.path.basename(str(path)), format(fp["rows"], ","),
                           what.capitalize(), _gb(est), _gb(avail or 0), _gb(total or 0),
                           " -- over the safe limit, so it was NOT attempted" if level == "refuse" else " -- close to the limit"))
    a["advice"] = advice(op, path, a) + " (To try anyway, set the environment variable PADB_SIZE_GUARD=off.)"
    return a


def require(path, op: str, mem=None, env=None, views=None) -> dict:
    """`assess`, but raises TooBig (with the advice in the message) when the load would not fit. Returns the assessment
    otherwise (callers may print it when level == 'warn')."""
    a = assess(path, op, mem=mem, env=env, views=views)
    if not a["ok"]:
        a = dict(a)
        a["message"] = a["message"] + " " + a["advice"]
        raise TooBig(a)
    return a
