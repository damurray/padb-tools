"""qa_compress.py -- QA for padb_compress.py (deflated embedded data) and its padb_v2 hook.

  REWRITE   which constants are rewritten and which are deliberately left alone: only a
            large single-line `var NAME=<json>;` is touched; NaN/Infinity payloads, two
            statements on one line, a missing `;`, tiny constants and already-dense data
            are untouched; CRLF line endings survive; a second pass changes nothing.
  HOOK      padb_v2._maybe_compress_page honours `compress_data` (false / auto / true).
  DECODER   (browser tier; honest SKIP when Playwright/Chromium is unavailable) the
            synchronous inflate decodes every deflate block type (stored, fixed Huffman,
            dynamic, long back-references, unicode) to EXACTLY the original JSON; a page
            rewritten by compress_html behaves like the original -- a later script still
            reads the constant at parse time and its DOMContentLoaded handler still fires.
  TEETH     a deliberately broken decoder and a deliberately broken rewrite (NaN allowed
            through) are caught by the same checks.

Run:  py qa/qa_compress.py        (exit 1 on FAIL; browser tier skipped -> still 0)
"""
import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))   # the repository root: the tools live there
import padb_compress as pc

_PASS, _FAIL, _SKIP = [], [], []


def check(desc, cond, detail=""):
    if cond:
        _PASS.append(desc)
        print("  PASS  " + desc)
    else:
        _FAIL.append(desc)
        print("  FAIL  " + desc + (" -- " + detail if detail else ""))


def _records(n, seed=1):
    r = np.random.default_rng(seed)
    return [{"s": "US%05d" % int(r.integers(0, 40)), "f": round(float(r.random() * 1000), 6),
             "v": round(float(r.normal(-70, 5)), 6), "g": "Harmonic: 2  Mode: CW"} for _ in range(n)]


def _page(lines, crlf=False):
    body = "<!doctype html><html><head><meta charset=\"utf-8\"><title>t</title></head><body>" \
           "<script>\n" + "\n".join(lines) + "\n</script></body></html>"
    return body.replace("\n", "\r\n") if crlf else body


def test_rewrite(td: Path):
    data = json.dumps(_records(4000))
    nan = data.replace('"v": -', '"v": NaN, "w": -', 1)             # NaN: JS accepts, JSON.parse rejects
    assert "NaN" in nan
    p = td / "a.html"
    p.write_text(_page(["var DATA=" + data + ";", "var SMALL=[1,2,3];", "var K='x';"]), encoding="utf-8", newline="")
    r = pc.compress_html(p, min_payload_bytes=1000)
    t = p.read_text(encoding="utf-8")
    check("rewrite: big JSON constant becomes PADB_unz(...), small constant untouched",
          r["changed"] and r["names"] == ["DATA"] and "PADB_unz(" in t and "var SMALL=[1,2,3];" in t)
    check("rewrite: page shrinks and the decoder is in <head> before the data",
          r["after"] < r["before"] and t.find("function PADB_unz") < t.find("var DATA="))
    r2 = pc.compress_html(p, min_payload_bytes=1000)
    check("rewrite: a second pass changes nothing (idempotent)", not r2["changed"])

    p = td / "nan.html"
    p.write_text(_page(["var DATA=" + nan + ";"]), encoding="utf-8", newline="")
    check("rewrite: payload with NaN is left alone (JSON.parse would reject it)",
          not pc.compress_html(p, min_payload_bytes=1000)["changed"])

    p = td / "two.html"
    p.write_text(_page(["var A=" + data + ";var B=[1,2];"]), encoding="utf-8", newline="")
    check("rewrite: two statements on one line are left alone",
          not pc.compress_html(p, min_payload_bytes=1000)["changed"])
    p = td / "nosemi.html"
    p.write_text(_page(["var A=" + data]), encoding="utf-8", newline="")
    check("rewrite: a line without a closing ';' is left alone",
          not pc.compress_html(p, min_payload_bytes=1000)["changed"])
    p = td / "small.html"
    p.write_text(_page(["var A=" + json.dumps(_records(20)) + ";"]), encoding="utf-8", newline="")
    check("rewrite: a constant under the size floor is left alone", not pc.compress_html(p)["changed"])
    rnd = json.dumps([float(x) for x in np.random.default_rng(5).random(60000)])
    p = td / "dense.html"
    p.write_text(_page(["var A=" + rnd + ";"]), encoding="utf-8", newline="")
    r = pc.compress_html(p, min_payload_bytes=1000)
    check("rewrite: already-dense data is only rewritten when it really saves space",
          (not r["changed"]) or r["after"] < r["before"])

    p = td / "crlf.html"
    p.write_text(_page(["var DATA=" + data + ";", "var X=1;"], crlf=True), encoding="utf-8", newline="")
    r = pc.compress_html(p, min_payload_bytes=1000)
    t = p.read_bytes().decode("utf-8")
    check("rewrite: CRLF line endings survive (rest of the page byte-preserved)",
          r["changed"] and "\r\nvar X=1;\r\n" in t)

    # TEETH: if NaN were let through, the NaN page WOULD be rewritten -> the check above fails
    saved = pc._is_json
    pc._is_json = lambda payload: True
    try:
        p = td / "nan2.html"
        p.write_text(_page(["var DATA=" + nan + ";"]), encoding="utf-8", newline="")
        broken = pc.compress_html(p, min_payload_bytes=1000)["changed"]
    finally:
        pc._is_json = saved
    check("teeth: with the JSON guard disabled the NaN page IS rewritten (so the guard is what protects it)", broken)


def test_hook(td: Path):
    import padb_v2 as v2
    data = json.dumps(_records(4000))
    html = _page(["var DATA=" + data + ";"])

    def run(cfg, size_floor_mb=None):
        p = td / "h.html"
        p.write_text(html, encoding="utf-8", newline="")
        with contextlib.redirect_stdout(io.StringIO()):
            v2._maybe_compress_page(p, cfg)
        return "PADB_unz(" in p.read_text(encoding="utf-8")
    check("hook: compress_data=false never touches the page", not run({"compress_data": False}))
    check("hook: auto leaves a page under the size floor alone", not run({}))
    check("hook: compress_data=true compresses regardless of size", run({"compress_data": True}))
    check("hook: auto compresses once the page is over compress_data_auto_mb",
          run({"compress_data_auto_mb": 0.01}))
    p = td / "bad.html"
    p.write_text("not a page", encoding="utf-8")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            v2._maybe_compress_page(p, {"compress_data": True})
        ok = True
    except Exception:
        ok = False
    check("hook: never raises (a failure leaves the page as it was)", ok and p.read_text() == "not a page")


def _deflate(raw: bytes, level=6, strategy=zlib.Z_DEFAULT_STRATEGY):
    co = zlib.compressobj(level, zlib.DEFLATED, -15, 9, strategy)
    return co.compress(raw) + co.flush()


def test_browser(td: Path):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        _SKIP.append("playwright not installed")
        print("  SKIP  playwright not installed (env, not a gap)")
        return
    try:
        pw = sync_playwright().start()
        br = pw.chromium.launch()
    except Exception as e:
        _SKIP.append("chromium unavailable")
        print("  SKIP  chromium unavailable (%s) (env, not a gap)" % str(e)[:80])
        return
    try:
        blank = td / "blank.html"
        blank.write_text("<!doctype html><html><head><meta charset=utf-8><script>" + pc.UNZ_JS +
                         "</script></head><body></body></html>", encoding="utf-8")
        pg = br.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        pg.goto(blank.as_uri())

        big = json.dumps(_records(30000, seed=9)).encode()
        cases = {
            "stored blocks (level 0)": _deflate(big[:200000], 0),
            "fixed Huffman (Z_FIXED)": _deflate(json.dumps({"a": [1, 2, 3], "b": "xyz"}).encode(), 9, zlib.Z_FIXED),
            "dynamic Huffman (level 9)": _deflate(big, 9),
            "dynamic Huffman (level 1)": _deflate(big, 1),
            "unicode strings": _deflate(json.dumps({"k": "déjà vu ☃ 中文 " * 500}, ensure_ascii=False).encode("utf-8"), 6),
            "long back-references": _deflate(json.dumps(["abcdefghij" * 30] * 4000 + [list(range(2000))]).encode(), 9),
            "single byte": _deflate(b"7", 6),
        }
        raws = {
            "stored blocks (level 0)": big[:200000],
            "fixed Huffman (Z_FIXED)": json.dumps({"a": [1, 2, 3], "b": "xyz"}).encode(),
            "dynamic Huffman (level 9)": big, "dynamic Huffman (level 1)": big,
            "unicode strings": json.dumps({"k": "déjà vu ☃ 中文 " * 500}, ensure_ascii=False).encode("utf-8"),
            "long back-references": json.dumps(["abcdefghij" * 30] * 4000 + [list(range(2000))]).encode(),
            "single byte": b"7",
        }
        for name, z in cases.items():
            raw = raws[name]
            if name.startswith("stored"):                    # a truncated JSON is not parseable; use bytes mode
                raw = json.dumps(["x" * 150000]).encode()
                z = _deflate(raw, 0)
            want = json.dumps(json.loads(raw.decode("utf-8")), separators=(",", ":"), ensure_ascii=False)
            got = pg.evaluate("([b,n]) => { try { return JSON.stringify(PADB_unz(b,n)); } catch(e) { return 'ERR ' + e.message; } }",
                              [base64.b64encode(z).decode(), len(raw)])
            check("decoder: %s decodes to exactly the original JSON" % name, got == want, str(got)[:80])
        check("decoder: no page errors while decoding", not errs, str(errs))
        check("decoder: wrong length is rejected, not silently truncated",
              str(pg.evaluate("([b,n]) => { try { PADB_unz(b,n); return 'no error'; } catch(e) { return e.message; } }",
                              [base64.b64encode(cases["single byte"]).decode(), 5])).startswith("padb-unz"))

        # a rewritten page behaves like the original (parse-time read + DOMContentLoaded handler)
        data = json.dumps(_records(5000, seed=2))
        body = ["var DATA=" + data + ";"]
        later = ("window.__len=DATA.length;window.__dcl=0;"
                 "document.addEventListener('DOMContentLoaded',function(){window.__dcl=DATA.length;});")
        for tag, comp in (("orig", False), ("comp", True)):
            f = td / (tag + "_pg.html")
            f.write_text(_page(body).replace("</script></body>", "</script><script>" + later + "</script></body>"),
                         encoding="utf-8", newline="")
            if comp:
                pc.compress_html(f, min_payload_bytes=1000)
            p2 = br.new_page()
            e2 = []
            p2.on("pageerror", lambda e, e2=e2: e2.append(str(e)[:120]))
            p2.goto(f.as_uri())
            p2.wait_for_timeout(200)
            res = p2.evaluate("() => [window.__len, window.__dcl, JSON.stringify(DATA).length]")
            if tag == "orig":
                want_res = res
            else:
                check("page: rewritten page reads DATA at parse time and its DOMContentLoaded handler fires, "
                      "same values as the original", res == want_res and res[0] == 5000 and not e2, "%s vs %s %s" % (res, want_res, e2))
            p2.close()

        # TEETH: a decoder with a wrong longest-match length (258 -> 257) must be caught.  (A
        # mutation that can loop forever would hang the browser, so this one fails fast.)
        broken = td / "broken.html"
        bad_js = pc.UNZ_JS.replace("227,258]", "227,257]")
        assert bad_js != pc.UNZ_JS
        broken.write_text("<!doctype html><html><head><meta charset=utf-8><script>" + bad_js +
                          "</script></head><body></body></html>", encoding="utf-8")
        pb = br.new_page()
        pb.set_default_timeout(30000)
        pb.goto(broken.as_uri())
        # long runs encode as maximum-length (258) back-references
        raw = json.dumps([0] * 3000 + [1] + [0] * 5000).encode()
        z = _deflate(raw, 9)
        got = pb.evaluate("([b,n]) => { try { return JSON.stringify(PADB_unz(b,n)).length; } catch(e) { return 'ERR'; } }",
                          [base64.b64encode(z).decode(), len(raw)])
        good = pg.evaluate("([b,n]) => JSON.stringify(PADB_unz(b,n)).length", [base64.b64encode(z).decode(), len(raw)])
        check("teeth: a decoder with a wrong repeat-code length is detected (good=%s, broken=%s)" % (good, got),
              got != good and good == len(json.dumps(json.loads(raw), separators=(',', ':'))))
    finally:
        br.close()
        pw.stop()


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        test_rewrite(td)
        # Browser tier BEFORE the hook test: importing padb_v2 (hook) pulls in padb_plots/
        # padb_run, and starting Playwright's sync driver afterwards hangs on Windows.
        test_browser(td)
        test_hook(td)
    print("\n" + "=" * 60)
    print("  PASS: %d    FAIL: %d%s" % (len(_PASS), len(_FAIL),
                                         ("    SKIPPED tiers: " + "; ".join(_SKIP)) if _SKIP else ""))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
