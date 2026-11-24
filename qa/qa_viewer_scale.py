r"""qa_viewer_scale.py -- time / memory / degradation limits of the parquet viewer on a LARGE file.

Generates a synthetic ~2M-row padb-style parquet (Group text with Port/Serial/Frequency,
thousands of distinct x, a limit stair), starts the real viewer on it and asserts:

  SCALE-LOAD   the server is ready within LOAD_BUDGET_S and uses < RSS_BUDGET_MB
  SCALE-META   rows == N and the band counts match a numpy oracle
  SCALE-QUERY  full-range / windowed / condition-filtered /api/scatter each answer within
               QUERY_BUDGET_S, report the TRUE total (numpy oracle) and stay within the cap
  SCALE-GUARD  a band view over a huge band returns the "band too large" card quickly --
               NOT a render attempt, a hang or a 500
  SCALE-ENV    the envelope still keeps the global min/max on the big set

No browser needed. The budgets are generous (this runs on busy shared machines); they exist
to catch an order-of-magnitude regression (e.g. a query going back to a full pandas scan),
not to benchmark. The measured numbers are printed so drift is visible.

    py qa/qa_viewer_scale.py [--rows 2000000]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the repository root (this file lives in qa/)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
PASS = FAIL = 0
LOAD_BUDGET_S = 90
QUERY_BUDGET_S = 8
GUARD_BUDGET_S = 20
RSS_BUDGET_MB = 2000


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail)) if detail else "")


def make_big(d: Path, n: int):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(7)
    nx = 4000
    xs = np.round(np.geomspace(0.01, 20000.0, nx), 4)
    xi = rng.integers(0, nx, n)
    x = xs[xi]
    port = rng.integers(0, 2, n)
    ser = rng.integers(0, 12, n)
    y = rng.normal(-80, 6, n).round(2)
    lim = np.where(x <= 1000, -70.0, -65.0)
    ports = np.array(["RF1", "RF2"])
    sers = np.array(["US6508%04d" % i for i in range(12)])
    grp = (pd.Series(ports[port]).radd("Port: ") + "  Serial Number: " + pd.Series(sers[ser]) +
           "  Frequency (MHz): " + pd.Series(x).map("{:g}".format))
    df = pd.DataFrame({"Group": grp.astype("category"), "Frequency (MHz)": x,
                       "SpurPower (dBc)": y, "Upper Limit": lim, "Test Step": "Room"})
    df.to_parquet(d / "big.parquet")
    (d / "padb_viewer_bands.json").write_text(json.dumps({
        "unit": "MHz", "bands": [{"name": "Low", "lo": 0, "hi": 8},
                                 {"name": "Mid", "lo": 8, "hi": 375},
                                 {"name": "High", "lo": 375, "hi": 30000}]}))
    return x, y, port


def rss_mb(pid):
    try:
        import psutil
        return psutil.Process(pid).memory_info().rss / 1048576.0
    except Exception:  # noqa: BLE001
        pass
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True).stdout
        kb = out.strip().split('","')[-1].replace(" K", "").replace(",", "").replace('"', "").replace(".", "")
        return int(kb) / 1024.0
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=2_000_000)
    ap.add_argument("--port", type=int, default=8793)
    args = ap.parse_args()
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
        import pyarrow  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print("qa_viewer_scale: numpy/pandas/pyarrow missing (%s) -- ENV-UNAVAILABLE, skipped" % exc)
        return 3
    tmp = Path(tempfile.mkdtemp(prefix="qa_vscale_"))
    t0 = time.time()
    x, y, port = make_big(tmp, args.rows)
    print("  (generated %s rows in %.0f s)" % (format(args.rows, ","), time.time() - t0))
    base = "http://127.0.0.1:%d" % args.port
    t0 = time.time()
    srv = subprocess.Popen([sys.executable, str(HERE / "padb_viewer.py"), str(tmp / "big.parquet"),
                            "--port", str(args.port), "--no-open"],
                           stdout=open(str(tmp / "server.log"), "w"), stderr=subprocess.STDOUT, cwd=str(HERE))
    try:
        meta = None
        for _ in range(int(LOAD_BUDGET_S * 2)):
            try:
                meta = json.loads(urllib.request.urlopen(base + "/api/meta", timeout=10).read())
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        load_s = time.time() - t0
        if meta is None:
            check("SCALE-LOAD: server ready within %d s" % LOAD_BUDGET_S, False, "not ready after %.0f s" % load_s)
            return 1
        rss = rss_mb(srv.pid)
        print("  (ready in %.1f s, rss %s MB, engine %s)" % (load_s, "%.0f" % rss if rss else "?", meta.get("engine")))
        check("SCALE-LOAD: ready within %d s (%.1f s)" % (LOAD_BUDGET_S, load_s), load_s <= LOAD_BUDGET_S)
        if rss is not None:
            check("SCALE-LOAD: resident memory < %d MB (%.0f MB)" % (RSS_BUDGET_MB, rss), rss < RSS_BUDGET_MB)
        n = args.rows
        check("SCALE-META: rows == generated", meta["rows"] == n, (meta["rows"], n))
        want = [int(((x >= b["lo"]) & (x <= b["hi"])).sum()) for b in meta["bands"]]
        check("SCALE-META: band counts == numpy oracle", meta["band_counts"] == want, (meta["band_counts"], want))

        def timed(path):
            t = time.time()
            r = json.loads(urllib.request.urlopen(base + path, timeout=120).read())
            return r, time.time() - t

        def pts(r):
            return [(float(a), float(b)) for t in r["traces"] for a, b in zip(t["x"], t["y"])]

        r, dt = timed("/api/scatter?flo=0.01&fhi=20000&maxpts=4000&by=site")
        check("SCALE-QUERY: full range in %.2f s (< %d s), true total, within cap" % (dt, QUERY_BUDGET_S),
              dt < QUERY_BUDGET_S and r["n_total"] == n and 0 < r["n_returned"] <= 4000, (dt, r["n_total"], r["n_returned"]))
        p = pts(r)
        check("SCALE-ENV: envelope keeps the global min and max on the big set",
              min(b for _, b in p) == float(y.min()) and max(b for _, b in p) == float(y.max()),
              (min(b for _, b in p), float(y.min()), max(b for _, b in p), float(y.max())))
        r, dt = timed("/api/scatter?flo=1&fhi=100&maxpts=4000&by=site")
        w = int(((x >= 1) & (x <= 100)).sum())
        check("SCALE-QUERY: windowed in %.2f s, total == numpy oracle" % dt,
              dt < QUERY_BUDGET_S and r["n_total"] == w, (dt, r["n_total"], w))
        dim = next((d for d in meta["dims"] if d["name"] == "Port"), None)
        check("SCALE-QUERY: Port condition detected on the big file", dim is not None, [d["name"] for d in meta["dims"]])
        if dim:
            conds = urllib.parse.quote(json.dumps({str(dim["id"]): ["RF1"]}))
            r, dt = timed("/api/scatter?flo=0.01&fhi=20000&maxpts=4000&by=site&conds=" + conds)
            check("SCALE-QUERY: condition-filtered in %.2f s, total == numpy oracle" % dt,
                  dt < QUERY_BUDGET_S and r["n_total"] == int((port == 0).sum()), (dt, r["n_total"], int((port == 0).sum())))
        t = time.time()
        body = urllib.request.urlopen(base + "/view?view=stat_summary&flo=0.01&fhi=20000&full=0", timeout=180).read().decode()
        dt = time.time() - t
        check("SCALE-GUARD: huge band -> 'too large' card in %.1f s (not a render / hang / 500)" % dt,
              dt < GUARD_BUDGET_S and "too large" in body.lower(), (round(dt, 1), body[:100]))
        rss2 = rss_mb(srv.pid)
        if rss2 is not None:
            check("SCALE-LOAD: memory still < %d MB after the queries (%.0f MB)" % (RSS_BUDGET_MB, rss2), rss2 < RSS_BUDGET_MB)
    finally:
        srv.terminate()
    print("\nqa_viewer_scale: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
