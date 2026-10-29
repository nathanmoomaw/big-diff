"""Event-flow edges: other code that listens for the events a change handles or fires.

Callers can't link two files that never call each other but react to the same browser event:
a knob's pointerup is followed by a `click` that a `document` listener in another file treats
as a shake (PR-gate case #5). This finds those links by event name, widened to related events
(a pointer press also produces mouse and click events), and marks page-level listeners
(document/window), which see every event in the page.
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from pathlib import Path

from .gitdiff import GREP_EXCLUDE, grep, read_at
from .symbols import Symbol, symbols

# Events the browser fires together for one physical action; touching one can trigger the others.
FAMILIES = [
    {"pointerdown", "pointerup", "pointermove", "pointercancel", "mousedown", "mouseup", "mousemove", "click",
     "dblclick", "contextmenu", "touchstart", "touchend", "touchmove", "touchcancel", "auxclick"},
    {"keydown", "keyup", "keypress", "beforeinput", "input"},
    {"focus", "blur", "focusin", "focusout"},
    {"change", "input", "submit", "reset"},
    {"wheel", "scroll"},
    {"dragstart", "drag", "dragend", "dragenter", "dragleave", "dragover", "drop"},
    {"devicemotion", "deviceorientation"},
]  # fmt: skip
POINTER = FAMILIES[0]
# React props and HTML attributes count only when they name a DOM event (onSuccess, onShake… are callbacks).
DOM_EVENTS = set().union(*FAMILIES) | {
    "load", "error", "resize", "select", "copy", "cut", "paste", "message", "storage", "popstate", "hashchange",
    "visibilitychange", "beforeunload", "online", "offline", "animationend", "transitionend", "mouseenter",
    "mouseleave", "mouseover", "mouseout", "pointerenter", "pointerleave", "pointerover", "pointerout",
}  # fmt: skip
REACT_ALIASES = {"doubleclick": "dblclick"}  # React spellings that aren't the DOM name
PAGE_ROOTS = {"document", "window", "self", "globalThis"}

_RECV = r"(?P<recv>\$\(\s*\w+\s*\)|[\w$]+(?:\??\.[\w$]+|\(\))*)"
_LISTEN = re.compile(rf"(?:{_RECV}\s*\??\.\s*)?\b(?:add|remove)EventListener\(\s*['\"`](?P<ev>[\w:-]+)")
_EMIT = re.compile(rf"(?:{_RECV})?\s*\.(?:on|once|off|one|emit)\(\s*['\"`](?P<ev>[\w:-]+)")  # jQuery, emitters
_DISPATCH = re.compile(r"\bdispatchEvent\(\s*new\s+\w*Event\(\s*['\"`](?P<ev>[\w:-]+)")
_PROP = re.compile(r"\bon([A-Z][A-Za-z]+?)(?:Capture)?\s*(?:=\s*\{|:)")  # JSX onPointerDown={…}, handler maps onClick:
_ATTR = re.compile(r"\bon([a-z]{3,})\s*=\s*[\"']")  # HTML onclick="…"
GREP = r"EventListener|dispatchEvent|\.(emit|on|once|off|one)\("  # loose prefilter; parse() decides
MAX_EDGES = 10
SNIPPET_LINES = 40


def page_level(recv: str | None) -> bool:
    """Is a listener on this receiver page-level? A bare call (no receiver) is a global listener."""
    if recv is None:
        return True
    if recv.startswith("$("):
        return recv[2:-1].strip() in ("document", "window")
    parts = re.split(r"\??\.|\(\)", recv)
    return parts[0] in PAGE_ROOTS or "ownerDocument" in parts


def parse(text: str, props: bool = True) -> dict[str, bool]:
    """Event names this code listens for or fires → whether any listener for it is page-level.
    props=False skips JSX props / HTML attributes: they bind one element, so another file's
    `onClick={…}` isn't coupled to this change, though they do name this change's own events."""
    found: dict[str, bool] = {}

    def add(name: str, is_global: bool = False) -> None:
        found[name] = found.get(name, False) or is_global

    for rx in (_LISTEN, _EMIT):
        for m in rx.finditer(text):
            add(
                m.group("ev"),
                page_level(m.group("recv")) if rx is _LISTEN or (m.group("recv") or "").startswith("$(") else False,
            )
    for m in _DISPATCH.finditer(text):
        add(m.group("ev"))
    for m in _PROP.finditer(text) if props else ():
        name = REACT_ALIASES.get(m.group(1).lower(), m.group(1).lower())
        if name in DOM_EVENTS:
            add(name)
    for m in _ATTR.finditer(text) if props else ():
        if m.group(1) in DOM_EVENTS:
            add(m.group(1))
    return found


def events_in(text: str) -> set[str]:
    return set(parse(text))


def widen(names: set[str]) -> set[str]:
    out = set(names)
    for fam in FAMILIES:
        if fam & names:
            out |= fam
    return out


@dataclass(frozen=True)
class Edge:
    path: str
    line: int
    events: tuple[str, ...]
    is_global: bool  # a page-level listener: sees every matching event in the page
    text: str
    owner: str | None = None  # the function or class that holds the line
    snippet: str | None = None  # page-level listeners only: the owner's code, so a reviewer sees what the handler does


@dataclass
class EventLinks:
    """Other code sharing a change's events: how many, the top few, and why they matter."""

    total: int
    edges: list[Edge]
    note: str | None = None


def explain(names: set[str], edges: list[Edge]) -> str | None:
    """One line on why the listed listeners can fire because of this code, or None."""
    caught = {ev for e in edges if e.is_global for ev in e.events}
    if not caught:
        return None
    if names & POINTER and caught <= POINTER and not names & caught:  # only then is it a compatibility event
        return (
            f"This code handles {', '.join(sorted(names & POINTER))}. The browser follows a pointer or touch press with "
            f"compatibility events ({', '.join(sorted(caught))}), which reach the page-level listeners below even though "
            "this code never listens for them."
        )
    return f"Page-level listeners below receive the same {', '.join(sorted(caught))} events this code handles."


@dataclass
class Index:
    """Every line in the repo that listens for or fires an event, from one grep, by event name."""

    lines: list[tuple[str, int, str, dict[str, bool]]]
    repo: str | Path = "."
    ref: str | None = None
    by_event: dict[str, list[int]] = field(default_factory=dict)
    files: dict[str, tuple[list[Symbol], list[str]]] = field(default_factory=dict, repr=False)  # path → symbols, lines
    sources: dict[str, str] = field(default_factory=dict, repr=False)  # texts the caller already read at this ref

    def __post_init__(self) -> None:
        for i, (*_, ev) in enumerate(self.lines):
            for name in ev:
                self.by_event.setdefault(name, []).append(i)

    def owner(self, path: str, line: int) -> tuple[Symbol | None, list[str]]:
        """Innermost symbol containing path:line at this index's ref, and the file's lines."""
        if path not in self.files:  # parsed only when one of its lines is shown
            src = self.sources[path] if path in self.sources else read_at(self.repo, self.ref, path) or ""
            self.files[path] = (symbols(path, src), src.splitlines())
        syms, lines = self.files[path]
        inside = [s for s in syms if s.start <= line <= s.end]
        return (min(inside, key=lambda s: s.end - s.start) if inside else None), lines

    def edges(self, path: str, names: set[str], max_edges: int = MAX_EDGES) -> EventLinks | None:
        """Lines in other files sharing an event (widened) with `names`: page-level first."""
        want = widen(names)
        hits = []
        for i in set().union(*(self.by_event.get(n, ()) for n in want)):
            p, n, t, ev = self.lines[i]
            if p != path:
                matched = {e: g for e, g in ev.items() if e in want}
                hits.append(Edge(p, n, tuple(sorted(matched)), any(matched.values()), t))
        if not hits:
            return None
        top = heapq.nsmallest(
            max_edges, hits, key=lambda e: (not e.is_global, not set(e.events) & names, e.path, e.line)
        )
        shown, seen = [], set()
        for e in top:
            sym, lines = self.owner(e.path, e.line)
            owner = sym.qualname if sym else None
            code = None
            if e.is_global and sym and (e.path, owner) not in seen:  # the handler's code, once per owner
                lo = max(sym.start - 1, min(e.line - 1 - SNIPPET_LINES // 2, sym.end - SNIPPET_LINES))
                code = "\n".join(lines[lo : min(lo + SNIPPET_LINES, sym.end)])
            seen.add((e.path, owner))
            shown.append(Edge(e.path, e.line, e.events, e.is_global, e.text, owner, code))
        return EventLinks(len(hits), shown, explain(names, shown))


def index(repo: str | Path, ref: str | None = None) -> Index:
    lines = [
        (path, num, text.strip()[:200], ev)
        for path, num, text in grep(repo, ref, "-E", "-e", GREP, exclude=(*GREP_EXCLUDE, ":!*.json"))
        if (ev := parse(text, props=False))
    ]
    return Index(lines, repo, ref)
