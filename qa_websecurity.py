r"""qa_websecurity.py -- the web app must refuse requests that do not come from its own page on loopback.

The app is a local, single-user tool with NO login and ~20 state-changing POST routes (run / delete / schedule a job,
open Explorer or the viewer, kill processes, change the default publish share). It binds to 127.0.0.1, but that does
not stop a web page you have open in the same browser from sending requests to http://127.0.0.1:5000:

  CSRF           a cross-site <form method=post enctype=text/plain> or fetch(mode:'no-cors', Content-Type:text/plain)
                 is a "simple request" -- no preflight -- and the app parses its body with get_json(force=True), so the
                 action runs even though the attacker cannot read the reply
  DNS rebinding  a page on evil.example whose name is re-pointed at 127.0.0.1 makes the app answer a request whose Host
                 header is evil.example -- same-origin to the browser, so the attacker CAN read every response

The guard (`padb_web._loopback_guard`): the Host header must be loopback; a state-changing request must carry an Origin
equal to its own origin (or, with no Origin, a Sec-Fetch-Site of same-origin / none) -- non-browser clients (curl, the
test client, scripts) send neither and are unaffected.

  WS-REAL-CSRF     a REAL browser (Chromium) on another origin (attacker.localhost) posts to the app by fetch(no-cors)
                   and by a text/plain form: BOTH must change nothing; the SAME post from the app's own page works
  WS-REBIND        a real socket request with Host: evil.example is refused (GET and POST) and changes nothing
  WS-MATRIX        Host / Origin / Sec-Fetch-Site combinations: loopback names and [::1] accepted; suffix attacks,
                   `Origin: null`, a different loopback PORT, https, cross-site / same-site Sec-Fetch-Site refused;
                   GET (reads) and header-less clients unaffected; the effect of a refused POST never happened
  WS-APP-UI        the app's own page still works in a real browser (loads, its own POST succeeds) via 127.0.0.1 and localhost

    py qa_websecurity.py     -> exit 1 on a failure; the browser tier honest-exits (skips) without Chromium
"""
import html
import http.client
import json
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "webapp"))
PASS = FAIL = 0
BODY = {"op": "add", "list": "PWNED", "names": ["x_job.json"]}


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail))[:400] if detail else "")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def lists_file(data_dir):
    return Path(data_dir) / "job_lists.json"


def raw(port, method, path, host=None, headers=None, body=None):
    """A real socket request with chosen headers -> (status, text)."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    h = {"Host": host or "127.0.0.1:%d" % port}
    h.update(headers or {})
    if body is not None:
        h.setdefault("Content-Type", "text/plain")
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in h.items():
        c.putheader(k, v)
    data = body.encode() if isinstance(body, str) else body
    if data is not None:
        c.putheader("Content-Length", str(len(data)))
    c.endheaders(data)
    r = c.getresponse()
    out = (r.status, r.read().decode("utf-8", "replace"))
    c.close()
    return out


class _Attacker(BaseHTTPRequestHandler):
    page = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(type(self).page)

    def log_message(self, *a):
        pass


def main() -> int:
    import padb_web
    tmp = Path(tempfile.mkdtemp(prefix="qa_ws_"))
    data = tmp / "data"
    data.mkdir()
    (data / "x_job.json").write_text(json.dumps({"csv_path": "a.csv"}), encoding="utf-8")
    padb_web.DATA_DIR = data
    from werkzeug.serving import make_server
    port = free_port()
    srv = make_server("127.0.0.1", port, padb_web.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    lf = lists_file(data)

    def reset():
        if lf.exists():
            lf.unlink()

    def changed():
        return lf.exists() and "PWNED" in lf.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ WS-REAL-CSRF / WS-APP-UI (real browser)
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        print("  SKIP  WS-REAL-CSRF / WS-APP-UI: no Playwright/Chromium (%s)" % exc)
        pw = browser = None
    if browser:
        try:
            aport = free_port()
            target = "http://127.0.0.1:%d/api/job-lists" % port
            # text/plain form: the body is NAME=VALUE, so NAME = '{...json prefix..., "z": "' and VALUE = '"}'
            # makes `{"op": ..., "z": "="}` -- valid JSON that the app reads with get_json(force=True)
            form_name = json.dumps(BODY)[:-1] + ', "z": "'
            _Attacker.page = ("""<!doctype html><title>attacker</title>
<script>
fetch(%s, {method:'POST', mode:'no-cors', headers:{'Content-Type':'text/plain'}, body:%s});
</script>
<iframe name=hidden style=display:none></iframe>
<form id=f method=post action="%s" enctype="text/plain" target=hidden>
  <input type=hidden name="%s" value="%s">
</form>
<script>setTimeout(function(){document.getElementById('f').submit();}, 400);</script>
""" % (json.dumps(target), json.dumps(json.dumps(BODY)), html.escape(target, quote=True),
       html.escape(form_name, quote=True), html.escape('"}', quote=True))).encode()
            asrv = HTTPServer(("127.0.0.1", aport), _Attacker)
            threading.Thread(target=asrv.serve_forever, daemon=True).start()
            reset()
            pg = browser.new_page()
            pg.set_default_timeout(30000)
            pg.goto("http://attacker.localhost:%d/" % aport)
            pg.wait_for_timeout(3000)
            check("WS-REAL-CSRF: a page on ANOTHER origin (attacker.localhost) posting by fetch(no-cors) AND by a text/plain "
                  "form changed nothing in the app", not changed(), lf.read_text() if lf.exists() else None)
            pg.close()
            # the same post from the app's own page
            for host in ("127.0.0.1", "localhost"):
                reset()
                pg2 = browser.new_page()
                pg2.set_default_timeout(30000)
                pg2.goto("http://%s:%d/" % (host, port))
                pg2.wait_for_selector("body")
                res = pg2.evaluate("""async (b) => { try { const r = await fetch('/api/job-lists', {method:'POST',
                    signal: AbortSignal.timeout(15000), headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)});
                    return r.status; } catch (e) { return 'ERROR ' + e.name; } }""", BODY)
                check("WS-APP-UI: the app's own page (via %s) can still POST -- status %s, effect applied" % (host, res),
                      res == 200 and changed(), res)
                jobs = pg2.evaluate("""async () => { try { const r = await fetch('/api/jobs', {signal: AbortSignal.timeout(20000)});
                    return r.status; } catch (e) { return 'ERROR ' + e.name; } }""")
                check("WS-APP-UI: ... and still reads (GET /api/jobs -> %s) via %s" % (jobs, host), jobs == 200)
                pg2.close()
            reset()
            asrv.shutdown()
        finally:
            browser.close()
            pw.stop()

    # ------------------------------------------------------------------ WS-REBIND (real socket, hostile Host)
    reset()
    s1, t1 = raw(port, "GET", "/api/jobs", host="evil.example:%d" % port)
    s2, t2 = raw(port, "POST", "/api/job-lists", host="evil.example:%d" % port, body=json.dumps(BODY))
    check("WS-REBIND: Host: evil.example is refused for GET (%s) and POST (%s) and the POST changed nothing"
          % (s1, s2), s1 == 403 and s2 == 403 and not changed(), (s1, t1[:80], s2))
    s3, _ = raw(port, "GET", "/api/jobs")
    check("WS-REBIND: the same GET with the real Host still works (%s)" % s3, s3 == 200)

    # ------------------------------------------------------------------ WS-MATRIX (real socket)
    def post(headers, host=None):
        reset()
        st, _ = raw(port, "POST", "/api/job-lists", host=host, headers=headers, body=json.dumps(BODY))
        return st, changed()
    own = "http://127.0.0.1:%d" % port
    cases = [
        ("no Origin, no Sec-Fetch (curl / scripts / the test client)", {}, None, True),
        ("Origin equal to the app's own origin", {"Origin": own}, None, True),
        ("localhost origin + localhost host", {"Origin": "http://localhost:%d" % port}, "localhost:%d" % port, True),
        ("[::1] origin + [::1] host", {"Origin": "http://[::1]:%d" % port}, "[::1]:%d" % port, True),
        ("Sec-Fetch-Site: same-origin (browser, no Origin)", {"Sec-Fetch-Site": "same-origin"}, None, True),
        ("Sec-Fetch-Site: none (user typed it)", {"Sec-Fetch-Site": "none"}, None, True),
        ("cross-site Origin", {"Origin": "http://evil.example"}, None, False),
        ("suffix attack on the Origin (127.0.0.1.evil.example)", {"Origin": "http://127.0.0.1.evil.example:%d" % port}, None, False),
        ("suffix attack on the Origin (localhost.evil.example)", {"Origin": "http://localhost.evil.example"}, None, False),
        ("Origin: null (sandboxed iframe / data: URL)", {"Origin": "null"}, None, False),
        ("loopback Origin on a DIFFERENT port (another local app)", {"Origin": "http://127.0.0.1:%d" % (port + 1)}, None, False),
        ("https Origin", {"Origin": "https://127.0.0.1:%d" % port}, None, False),
        ("Sec-Fetch-Site: cross-site (no Origin)", {"Sec-Fetch-Site": "cross-site"}, None, False),
        ("Sec-Fetch-Site: same-site (no Origin)", {"Sec-Fetch-Site": "same-site"}, None, False),
        ("hostile Host with a loopback Origin", {"Origin": own}, "evil.example:%d" % port, False),
        ("Host that merely starts with localhost (localhost.evil.example)", {}, "localhost.evil.example:%d" % port, False),
        ("Host with userinfo trick (127.0.0.1@evil.example)", {}, "127.0.0.1@evil.example", False),
    ]
    for desc, hdr, host, allowed in cases:
        st, did = post(hdr, host)
        ok = (st == 200 and did) if allowed else (st == 403 and not did)
        check("WS-MATRIX: POST %s -> %s (%s) and %s" % (desc, "accepted" if allowed else "refused", st,
                                                        "took effect" if did else "had no effect"), ok, (st, did))
    st, _ = raw(port, "GET", "/api/jobs", headers={"Origin": "http://evil.example"})
    check("WS-MATRIX: a GET with a foreign Origin is still served (a browser cannot read it without CORS; "
          "the reads are not state changes)", st == 200, st)
    st, txt = raw(port, "POST", "/api/job-lists", host="evil.example", body=json.dumps(BODY))
    check("WS-MATRIX: the refusal is a clear JSON 403, not a stack trace",
          st == 403 and "error" in json.loads(txt), txt[:120])
    reset()
    srv.shutdown()

    # the existing in-process client (qa_webapp's) must be unaffected: no Origin, Host 'localhost'
    cl = padb_web.app.test_client()
    check("WS-MATRIX: the Flask test client (Host localhost, no Origin) is unaffected",
          cl.get("/api/jobs").status_code == 200
          and cl.post("/api/job-lists", json={"op": "delete", "list": "no-such-list"}).status_code == 404)
    print("\nqa_websecurity: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
