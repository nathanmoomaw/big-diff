"""Viewer v0: a review packet as one self-contained HTML page (no JS, light + dark)."""

from __future__ import annotations

import html
from pathlib import Path

from .packet import Packet

TOKENS = Path(__file__).with_name("tokens.css").read_text()  # shared with web.css pages and site/

CSS = TOKENS + """
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:24px 16px}
h1{font-size:20px;margin:0 0 12px} h2{font-size:16px;margin:28px 0 8px}
table{border-collapse:collapse;width:100%;font-size:14px} td,th{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left}
.item{border:1px solid var(--line);border-radius:8px;margin:14px 0;overflow:hidden}
.item header{padding:8px 12px;border-bottom:1px solid var(--line)} .path{color:var(--mute);font-size:13px}
.tag{background:var(--tag);border-radius:4px;font-size:12px;margin-left:6px;padding:1px 6px}
pre{margin:0;overflow-x:auto;font:13px/1.45 ui-monospace,Menlo,monospace} pre span{display:block;padding:0 12px;white-space:pre}
.a{background:var(--add)} .d{background:var(--del)} .h{background:var(--hunk);color:var(--mute)}
.uses{padding:8px 12px;font-size:13px;border-top:1px solid var(--line)} .uses code{font-size:12px}
"""


def _line(line: str) -> str:
    cls = "a" if line.startswith("+") else "d" if line.startswith("-") else "h" if line.startswith("@@") else ""
    return f'<span class="{cls}">{html.escape(line) or " "}</span>'


def render(p: Packet) -> str:
    rows = "".join(
        f"<tr><td><code>{html.escape(f['path'])}</code></td><td>{f['status']}</td><td>+{f['added']} −{f['removed']}</td></tr>"
        for f in p.files
    )
    items = []
    for i in p.items:
        tags = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in i.tags)
        body = "".join(_line(x) for x in i.body.splitlines())
        uses = ""
        if i.uses and i.uses.sites:
            sites = "".join(
                f"<li><code>{html.escape(s.path)}:{s.line}</code> {html.escape(s.text)}</li>" for s in i.uses.sites
            )
            uses = f'<div class="uses">Used in {i.uses.total} place(s):<ul>{sites}</ul></div>'
        items.append(
            f'<section class="item"><header><strong>{html.escape(i.title)}</strong>{tags}'
            f'<div class="path">{html.escape(i.path)}</div></header><pre>{body}</pre>{uses}</section>'
        )
    omitted = f"<p>Omitted over budget: {html.escape(', '.join(p.omitted))}</p>" if p.omitted else ""
    title = f"big diff: {html.escape(p.base)} → {html.escape(p.head)}"
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title><style>{CSS}</style></head><body><h1>{title}</h1>"
        f"<table><tr><th>file</th><th>status</th><th>+/−</th></tr>{rows}</table>"
        f"<h2>Changes, most important first</h2>{''.join(items)}{omitted}</body></html>"
    )
