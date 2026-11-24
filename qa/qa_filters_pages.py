r"""qa_filters_pages.py -- the job's filter configuration (`filters_file` / `filter_config`) in the HTML pages.

v3.0 stage 4c (filters slice). The configuration decides which condition dimensions a page offers as FILTERS.
It is applied by the Scatter and Reference views; the other views still list every condition and the index page
says so. A condition that is not exposed puts no restriction on the data (no checkbox panel), but the data
itself is untouched: the records still carry the column, so the table / CSV export and the Global Filter are
unchanged.

Oracle: each page's `GROUP_COLS` (what its filter panels and applyFilters use) is compared with an INDEPENDENT
expectation computed from the plain page, and -- for an explicit `show` list -- with `padb_core`'s own
resolution (`GroupIndex.apply_config`) over the same Group text, so the viewer and the pages cannot drift.
Checks (no browser needed):

  FILT-PLAIN     no configuration: every condition dimension is offered; HIDDEN_COLS is empty
  FILT-HIDE      hide [X]: X is gone from GROUP_COLS (order of the rest kept), listed in HIDDEN_COLS, STILL in the
                 embedded data; the serial dimension is untouched; the plain page differs (teeth)
  FILT-SHOW      show [B, A]: the governed conditions are exactly B then A
  FILT-EXPOSE    show [Mode, ...] where Mode is single-valued (the automatic rules drop it): it is offered and embedded
  FILT-CORE      padb_core's resolution of the same `show` list names the same conditions in the same order
  FILT-NOTICE    the index page says the configuration is active (and that it is not applied everywhere)
  FILT-RESOLVE   the job-key resolver: inline object, relative file, missing file, bad file, default config, labels

    py qa/qa_filters_pages.py          -> exit 1 on a failure
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the repository root (this file lives in qa/)
sys.path.insert(0, str(HERE))
PASS = FAIL = 0


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail))[:500] if detail else "")


def const(path, name):
    h = Path(path).read_text(encoding="utf-8")
    m = re.search(r"^var %s\s*=\s*" % name, h, re.M)
    return json.loads(h[m.end():h.index(";\n", m.end())])


def build(src, out, views, filters=None):
    cmd = [sys.executable, str(HERE / "padb_analyze.py"), "--input", str(src), "--out", str(out),
           "--views", ",".join(views)]
    if filters:
        cmd += ["--filters-file", str(filters)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    res = json.loads(r.stdout)
    assert res["ok"], res
    files = [Path(f) for f in res["analyses"][0]["files"]]
    pages = {f.stem.split("fixture_")[-1].split("filtermode_")[-1]: f for f in files if f.suffix == ".html"}
    index = next(f for f in files if f.name == "index.html")
    return pages, index


def names(group_cols):
    """['_grp_Port', ...] -> ['Port', ...] for the condition dimensions only."""
    return [c[5:] for c, _ in group_cols if c.startswith("_grp_")]


def open_page_ref(browser, path):
    """(# checkbox panels for the hidden dim, # group-by options, page errors) of a reference page."""
    pg = browser.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(Path(path).as_uri())
    pg.wait_for_selector("#f_lo", timeout=120000)
    pg.wait_for_timeout(600)
    panels = pg.evaluate("document.querySelectorAll('.fchk[data-col]').length")
    opts = pg.evaluate("document.querySelectorAll('#groupby option').length")
    hid = pg.evaluate("document.querySelectorAll('.fchk[data-col=\"_grp_%s\"]').length" % HIDE_NAME[0])
    return (hid, opts, errs)


HIDE_NAME = [""]


def main() -> int:
    import qa_viewer_ui as fx
    from padb_core.conditions import GroupIndex
    from padb_core.filterconfig import FilterConfig

    tmp = Path(tempfile.mkdtemp(prefix="qa_fp_"))
    df = fx.make_fixture(tmp)
    df["Group"] = df["Group"] + "  Mode: M1"           # a single-valued key: the automatic rules never offer it
    src = tmp / "filtermode.parquet"
    df.to_parquet(src)
    VIEWS = ["scatter", "reference"]

    P0, I0 = build(src, tmp / "plain", VIEWS)
    plain = {v: const(P0[v], "GROUP_COLS") for v in VIEWS}
    base = {v: names(plain[v]) for v in VIEWS}
    print("  plain dimensions:", base)
    serial = [n for n in base["scatter"] if any(k in n.lower() for k in ("serial", "unit id", "dut id", "s/n"))]
    # NOTE (known divergence, not changed here): when the sweep variable is repeated in the Group text
    # ("Frequency (MHz): 10") the pages offer it as a condition filter, while padb_core classifies it as the
    # x-axis key and never offers it. The checks below use conditions both sides agree on.
    xlike = [n for n in base["scatter"] if "frequency" in n.lower()]
    if xlike:
        print("  note: pages offer the x-axis key %s as a condition filter; padb_core does not (known divergence)" % xlike)
    free = [n for n in base["scatter"] if n not in serial and n not in xlike]
    check("FILT-PLAIN: no configuration -> every condition offered, nothing hidden; 'Mode' (single-valued) not offered",
          len(free) >= 2 and "Mode" not in base["scatter"] and const(P0["scatter"], "HIDDEN_COLS") == [],
          (base, const(P0["scatter"], "HIDDEN_COLS")))
    A, B = free[0], free[1]
    HIDE_NAME[0] = A

    # ---- FILT-HIDE
    f_hide = tmp / "hide.json"
    f_hide.write_text(json.dumps({"hide": [A]}), encoding="utf-8")
    P1, I1 = build(src, tmp / "hide", VIEWS, f_hide)
    for v in VIEWS:
        got = names(const(P1[v], "GROUP_COLS"))
        want = [n for n in base[v] if n != A]
        check("FILT-HIDE[%s]: hide [%s] -> exactly the plain dimensions minus it, same order" % (v, A), got == want, (got, want))
        check("FILT-HIDE[%s] teeth: the plain page offered %s" % (v, A), A in base[v], base[v])
    hid = const(P1["scatter"], "HIDDEN_COLS")
    check("FILT-HIDE[scatter]: the hidden condition is listed in HIDDEN_COLS (table / CSV export keep it)",
          [c for c, _ in hid] == ["_grp_" + A], hid)
    rows = const(P1["scatter"], "DATA")
    check("FILT-HIDE[scatter]: ... and is STILL in the embedded data (hiding a filter never edits the data)",
          rows and ("_grp_" + A) in rows[0], list(rows[0])[:12] if rows else None)
    check("FILT-HIDE: the serial dimension is untouched", [n for n in names(const(P1["scatter"], "GROUP_COLS")) if n in serial] == serial)

    # ---- FILT-SHOW (order + exclusivity)
    f_show = tmp / "show.json"
    f_show.write_text(json.dumps({"show": [B, A]}), encoding="utf-8")
    P2, I2 = build(src, tmp / "show", VIEWS, f_show)
    for v in VIEWS:
        got = [n for n in names(const(P2[v], "GROUP_COLS")) if n not in serial]
        check("FILT-SHOW[%s]: show [%s, %s] -> exactly those conditions, in that order" % (v, B, A), got == [B, A], got)

    # ---- FILT-EXPOSE
    f_exp = tmp / "expose.json"
    f_exp.write_text(json.dumps({"show": ["Mode", A]}), encoding="utf-8")
    P3, I3 = build(src, tmp / "expose", VIEWS, f_exp)
    for v in VIEWS:
        got = [n for n in names(const(P3[v], "GROUP_COLS")) if n not in serial]
        check("FILT-EXPOSE[%s]: a single-valued key the rules drop is offered when listed in `show`" % v, got == ["Mode", A], got)
    rows3 = const(P3["scatter"], "DATA")
    check("FILT-EXPOSE[scatter]: ... and its column is embedded so the filter has data", rows3 and "_grp_Mode" in rows3[0],
          list(rows3[0]) if rows3 else None)

    # ---- FILT-CORE : the viewer's resolution of the same list
    idx = GroupIndex.build(sorted(set(df["Group"])), config=FilterConfig.from_dict({"show": ["Mode", A]}))
    core_names = [d["name"] for d in idx.dims]
    check("FILT-CORE: padb_core resolves show [Mode, %s] to the same conditions, same order, as the pages" % A,
          core_names == ["Mode", A], core_names)
    idx2 = GroupIndex.build(sorted(set(df["Group"])), config=FilterConfig.from_dict({"show": [B, A]}))
    check("FILT-CORE: ... and show [%s, %s] likewise" % (B, A), [d["name"] for d in idx2.dims] == [B, A], [d["name"] for d in idx2.dims])

    # ---- FILT-NOTICE
    t0, t1 = I0.read_text(encoding="utf-8"), I1.read_text(encoding="utf-8")
    check("FILT-NOTICE: the index page announces the configuration and that it is not applied in every view",
          "Filter configuration active" in t1 and "Scatter and Reference" in t1 and "Filter configuration active" not in t0)

    # ---- FILT-RESOLVE
    import padb_v2
    d = tmp / "jobdir"
    d.mkdir()
    (d / "f.json").write_text(json.dumps({"hide": ["Port"]}), encoding="utf-8")
    (d / "bad.json").write_text("{not json", encoding="utf-8")
    (d / "dflt.json").write_text("{}", encoding="utf-8")
    c1 = {"filters_file": "f.json", "_job_dir": str(d)}
    c2 = {"filter_config": {"show": ["Port"], "labels": {"Port": "RF port"}}}
    c3 = {"filters_file": "nope.json", "_job_dir": str(d)}
    c4 = {"filters_file": "bad.json", "_job_dir": str(d)}
    c5 = {"filters_file": "dflt.json", "_job_dir": str(d)}
    c6 = {"filter_config": {"surprise": 1}}
    r = [padb_v2._resolve_filter_config(c) for c in (c1, c2, c3, c4, c5, c6)]
    check("FILT-RESOLVE: relative file and inline object resolve; missing / bad / default / unknown-field are ignored, never raise",
          r == [True, True, False, False, False, False] and c1["_filter_cfg"] == {"hide": ["Port"]}
          and c2["_filter_cfg"]["show"] == ["Port"] and "_filter_cfg" not in c3 and "_filter_cfg" not in c4
          and "_filter_cfg" not in c5 and "_filter_cfg" not in c6, (r, c1, c2))
    check("FILT-RESOLVE: labels are reported as viewer-only (pages match dimensions by label for locks / saved filters)",
          "viewer only" in c2.get("_filters_note", ""), c2.get("_filters_note"))
    # ---- FILT-BROWSER (skipped, not failed, without Playwright/Chromium)
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        print("  SKIP  FILT-BROWSER: no Playwright/Chromium (%s)" % exc)
        pw = browser = None
    if browser:
        try:
            def open_page(path):
                pg = browser.new_page()
                pg.errors = []
                pg.on("pageerror", lambda e: pg.errors.append(str(e)))
                pg.goto(Path(path).as_uri())
                pg.wait_for_selector("#plot .main-svg", timeout=120000)
                pg.wait_for_timeout(600)
                return pg
            pp, ph = open_page(P0["scatter"]), open_page(P1["scatter"])
            n_plain = pp.evaluate("document.querySelectorAll('.fchk[data-col=\"_grp_%s\"]').length" % A)
            n_hide = ph.evaluate("document.querySelectorAll('.fchk[data-col=\"_grp_%s\"]').length" % A)
            n_other = ph.evaluate("document.querySelectorAll('.fchk[data-col=\"_grp_%s\"]').length" % B)
            check("FILT-BROWSER: the hidden condition has NO checkbox panel; the others do (plain page had one for it)",
                  n_plain > 0 and n_hide == 0 and n_other > 0, (n_plain, n_hide, n_other))
            rows_plain = pp.evaluate("applyFilters(DATA).length")
            rows_hide = ph.evaluate("applyFilters(DATA).length")
            check("FILT-BROWSER: hiding a filter puts no restriction on the data (same rows pass the filters)",
                  rows_plain == rows_hide and rows_hide > 0, (rows_plain, rows_hide))
            ph.evaluate("toggleScatterTable()")
            ph.wait_for_timeout(500)
            head = ph.inner_text("#scatter_table_panel")
            check("FILT-BROWSER: the data table still shows the hidden condition as a column", A in head, head[:200])
            with ph.expect_download(timeout=30000) as dl:
                ph.evaluate("saveCSV(false)")
            csv_text = Path(dl.value.path()).read_text(encoding="utf-8", errors="replace")
            hdr = next((ln for ln in csv_text.splitlines() if ln and not ln.startswith("#")), "")
            check("FILT-BROWSER: the CSV export still carries the hidden condition", A in hdr, hdr[:200])
            check("FILT-BROWSER: no uncaught page errors", not (pp.errors + ph.errors), (pp.errors + ph.errors)[:3])
            # reference: the panel / group-by follow the configuration too
            rp, rh = open_page_ref(browser, P0["reference"]), open_page_ref(browser, P1["reference"])
            check("FILT-BROWSER[reference]: the hidden condition is gone from the filter panels and the group-by list",
                  rp[0] > 0 and rh[0] == 0 and rp[1] > rh[1] and not rh[2], (rp, rh))
            HIDE_NAME[0] = "Mode"
            rx = open_page_ref(browser, P3["reference"])
            check("FILT-BROWSER[reference]: an exposed single-valued condition gets a filter panel", rx[0] > 0, rx)
            sx = open_page(P3["scatter"])
            check("FILT-BROWSER[scatter]: ... and so does the scatter page",
                  sx.evaluate("document.querySelectorAll('.fchk[data-col=\"_grp_Mode\"]').length") > 0)
        finally:
            browser.close()
            pw.stop()
    print("\nqa_filters_pages: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
