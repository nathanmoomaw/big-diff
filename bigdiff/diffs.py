"""Two-input diffs for the web pages: text side by side, JSON/CSV values, images.

Only the standard library, data.py and image.py (Pillow), so the hosted site can run this module
in the browser under Pyodide while `bigdiff serve` runs it in a local server. Both hosts pass the
page's raw request to `handle`; only turning images into bytes is theirs.
"""

from __future__ import annotations

import base64
import difflib
import io

from . import data, image


def text_rows(a: str, b: str, context: int = 3) -> dict:
    """Side-by-side rows {t: eq|del|add|chg|gap, an, a, bn, b} plus counts; unchanged runs beyond `context` become gaps."""
    al, bl = a.splitlines(), b.splitlines()
    rows: list[dict] = []
    added = removed = 0
    if al == bl:
        return {"rows": rows, "added": 0, "removed": 0}
    shown = 0  # lines of `a` accounted for so far
    for group in difflib.SequenceMatcher(None, al, bl, autojunk=False).get_grouped_opcodes(context):
        if group[0][1] > shown:
            rows.append({"t": "gap", "n": group[0][1] - shown})
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows += [{"t": "eq", "an": i + 1, "a": al[i], "bn": j + 1, "b": bl[j]} for i, j in zip(range(i1, i2), range(j1, j2))]
                continue
            removed += i2 - i1
            added += j2 - j1
            for k in range(max(i2 - i1, j2 - j1)):
                ia, jb = i1 + k, j1 + k
                ra = {"an": ia + 1, "a": al[ia]} if ia < i2 else {}
                rb = {"bn": jb + 1, "b": bl[jb]} if jb < j2 else {}
                rows.append({"t": "chg" if ra and rb else "del" if ra else "add", **ra, **rb})
        shown = group[-1][2]
    if shown < len(al):
        rows.append({"t": "gap", "n": len(al) - shown})
    return {"rows": rows, "added": added, "removed": removed}


def diff_texts(a: str, b: str, kind: str = "auto", key: str | None = None, tol: float = 0.0, names: tuple[str, ...] = ()) -> dict:
    """Value diff for JSON/CSV (asked for, named so, or both sides parse as JSON); line diff otherwise."""
    if kind != "text":
        k = None if kind == "auto" else kind
        k = k or next(filter(None, map(data.kind_of, names)), None)
        try:
            ops = data.diff_text(a, b, k, key or None, tol)
            k = k or "json"
            return {"kind": k, "summary": f"{len(ops)} difference(s) as {k.upper()} values",
                    "ops": [{"op": o.op, "text": o.render()} for o in ops]}
        except data.UnknownKind:
            pass
    r = text_rows(a, b)
    changed = f"{r['added']} line(s) added or changed, {r['removed']} removed or changed"
    return {"kind": "text", "summary": changed if r["rows"] else "No differences.", **r}


def diff_images(a: bytes, b: bytes, threshold: int = image.DEFAULT_THRESHOLD) -> dict:
    heat = io.BytesIO()
    r = image.diff(io.BytesIO(a), io.BytesIO(b), threshold, heat)
    return {"summary": r.render(), "fraction": r.fraction, "heatmap": "data:image/png;base64," + base64.b64encode(heat.getvalue()).decode()}


def handle(op: str, req: dict) -> dict:
    """One page request, values as the form sent them (strings, maybe empty). Images are bytes-like."""
    if op == "text":
        return diff_texts(req.get("a", ""), req.get("b", ""), req.get("kind") or "auto", req.get("key"),
                          float(req.get("tol") or 0), tuple(filter(None, req.get("names") or ())))
    if op == "image":
        t = req.get("threshold")
        return diff_images(bytes(req["a"]), bytes(req["b"]), image.DEFAULT_THRESHOLD if t in (None, "") else int(float(t)))
    raise KeyError(op)
