import base64
import io
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from PIL import Image

from bigdiff import diffs, web


def test_text_rows_marks_changes_and_collapses_unchanged_runs():
    a = "\n".join(f"line {i}" for i in range(20))
    b = a.replace("line 10", "line ten") + "\nnew"
    r = diffs.text_rows(a, b)
    rows = r["rows"]
    assert (r["added"], r["removed"]) == (2, 1)
    kinds = [r["t"] for r in rows]
    assert kinds.count("chg") == 1 and kinds.count("add") == 1
    assert "gap" in kinds  # 20 lines with one change don't all show
    chg = next(r for r in rows if r["t"] == "chg")
    assert (chg["an"], chg["a"], chg["bn"], chg["b"]) == (11, "line 10", 11, "line ten")


def test_diff_texts_auto_picks_json_values_else_lines():
    r = diffs.diff_texts('{"a": 1, "b": 2}', '{"a": 1, "b": 3}')
    assert r["kind"] == "json" and [o["op"] for o in r["ops"]] == ["changed"]
    r = diffs.diff_texts("x\ny", "x\nz")
    assert r["kind"] == "text" and r["added"] == 1 and r["removed"] == 1
    assert diffs.diff_texts("same", "same")["added"] == 0


def test_diff_texts_kind_from_file_name():
    r = diffs.diff_texts("id,v\n1,a\n", "id,v\n1,b\n", names=("old.csv",), key="id")
    assert r["kind"] == "csv" and len(r["ops"]) == 1


def test_diff_texts_csv_by_key():
    r = diffs.diff_texts("id,v\n1,a\n2,b\n", "id,v\n2,b\n1,A\n", "csv", "id")
    assert [o["op"] for o in r["ops"]] == ["changed"]


def _png(color) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_diff_images_returns_summary_and_heatmap():
    r = diffs.diff_images(*(web._decode(_png(c)) for c in ("white", "black")))
    assert r["fraction"] == 1.0 and r["heatmap"].startswith("data:image/png;base64,")


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def _req(url, body=None, host=None):
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None)
    if host:
        r.add_header("Host", host)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_server_page_api_and_host_check(server):
    code, page = _req(server + "/")
    assert code == 200 and "<title>big diff</title>" in page
    code, out = _req(server + "/api/text", {"a": "1", "b": "2", "kind": "text"})
    assert code == 200 and json.loads(out)["added"] == 1
    code, out = _req(server + "/api/text", {"a": "{", "b": "{}", "kind": "json"})
    assert code == 400 and "error" in json.loads(out)
    code, _ = _req(server + "/", host="evil.example:80")  # DNS rebinding
    assert code == 403


def test_handle_coerces_form_values_and_keeps_threshold_zero():
    white, black = (web._decode(_png(c)) for c in ("white", "black"))
    assert diffs.handle("image", {"a": white, "b": black, "threshold": 0})["fraction"] == 1.0
    assert diffs.handle("image", {"a": white, "b": black, "threshold": "255"})["fraction"] == 0.0
    assert diffs.handle("image", {"a": white, "b": black, "threshold": ""})["fraction"] == 1.0
    r = diffs.handle("text", {"a": "id,v\n1,2\n", "b": "id,v\n1,2.0004\n", "tol": "0.001", "names": [None, "x.csv"]})
    assert r["kind"] == "csv" and r["summary"] == "0 difference(s) as CSV values"
