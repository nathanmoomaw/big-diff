"""Web UI: `bigdiff serve` opens a local page for diffing two texts, data files, images or git refs (diffs.py does the work).

Standard library only (http.server); listens on 127.0.0.1 and refuses other Host headers, so a web
page elsewhere can't reach it through DNS rebinding. Each request is one diff; nothing is stored.
"""

from __future__ import annotations

import base64
import html
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import diffs, packet
from .gitdiff import git
from .html import TOKENS

MAX_BODY = 40 * 1024 * 1024  # two screenshots as base64 fit easily
_here = Path(__file__).parent
PAGE = (_here / "web.html").read_text().replace("/*CSS*/", TOKENS + (_here / "web.css").read_text())
ROUTES = {"/api/text": "text", "/api/image": "image"}


def _decode(url: str) -> bytes:
    return base64.b64decode(url.split(",", 1)[-1])


def review_page(repo: str, base: str | None, head: str | None) -> str:
    root = git(os.path.expanduser(repo or "."), "rev-parse", "--show-toplevel").strip()  # same root as the CLI
    return packet.build(root, base or "HEAD", head or None).render("html")


class Handler(BaseHTTPRequestHandler):
    server_version = "bigdiff"

    def log_message(self, fmt: str, *args) -> None:  # quiet; errors still go back to the page
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost", "[::1]")

    def _send(self, code: int, body: str | bytes, ctype: str = "application/json") -> None:
        raw = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _err(self, code: int, msg: str) -> None:
        self._send(code, json.dumps({"error": msg}))

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._err(403, "bad host")
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(200, PAGE, "text/html")
        if u.path == "/review":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                return self._send(200, review_page(q.get("repo"), q.get("base"), q.get("head")), "text/html")
            except (RuntimeError, OSError, ValueError) as e:
                return self._send(400, f"<pre>{html.escape(str(e))}</pre>", "text/html")
        self._err(404, "not found")

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._err(403, "bad host")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._err(400, "bad Content-Length")
        if not 0 <= n <= MAX_BODY:
            return self._err(400 if n < 0 else 413, "bad Content-Length" if n < 0 else "too large")
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path not in ROUTES:
                return self._err(404, "not found")
            if self.path == "/api/image":
                req["a"], req["b"] = _decode(req["a"]), _decode(req["b"])
            out = diffs.handle(ROUTES[self.path], req)
        except Exception as e:  # bad JSON, unreadable image, CSV key missing: report it on the page
            return self._err(400, f"{type(e).__name__}: {e}")
        self._send(200, json.dumps(out))


def serve(port: int, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_port}/"
    print(f"big diff at {url} (ctrl-c to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
