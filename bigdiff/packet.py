"""The review packet: what changed, by symbol, with who depends on it, ranked and fitted to a budget.

Why: a reviewer given whole files reads old code as much as new (NOW, Oct 21), and one given a
raw diff can't see callers in other files (PR-gate test, Oct 21: 3 of 6 real bugs needed context
outside the changed file). The packet gives both, in priority order, under a size cap.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass, field, replace
from enum import IntEnum
from pathlib import Path

from . import blast, events
from .gitdiff import FileDiff, Hunk, diff_files, read_at, resolve
from .symbols import SymbolChange, compare, language

DEFAULT_BUDGET = 60_000  # chars, ~17K tokens: well under Haiku's 100K price cliff
MIN_ITEM = 600  # an item trimmed below this many chars isn't worth showing: omit it instead
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Cargo.lock", "Gemfile.lock"}
CONFIG_SUFFIXES = {".json", ".toml", ".yaml", ".yml", ".sh", ".css", ".html", ".sql"}  # unparsed but behaviour-bearing


class Prio(IntEnum):
    """Packet order, most important first."""

    STILL_REFERENCED = 0  # removed, but something still uses it
    SIGNATURE_WITH_CALLERS = 1
    USED_ELSEWHERE = 2  # body changed, used in other files
    MODIFIED = 3  # body changed; new modules nothing imports; edits in source/config files without symbols
    MINOR = 4  # removed and unreferenced; edits outside symbols in parsed files
    ADDED = 5  # new symbols; docs
    BINARY = 6
    LOCKFILE = 7


REVIEWABLE = ("source", "config")


def file_kind(f: FileDiff) -> str:
    """lockfile, binary, source (has a symbol parser), config (unparsed, behaviour-bearing) or doc."""
    if Path(f.path).name in LOCKFILES:
        return "lockfile"
    if f.binary:
        return "binary"
    if language(f.path):
        return "source"
    return "config" if Path(f.path).suffix in CONFIG_SUFFIXES else "doc"


@dataclass
class Item:
    """One unit of the packet: a changed symbol, or a plain hunk set for an unparsed file."""

    priority: Prio
    path: str
    title: str
    body: str  # unified diff text; empty for a finding with no diff (an orphan module)
    uses: blast.Uses | None = None
    tags: list[str] = field(default_factory=list)
    events: events.EventLinks | None = None  # other code sharing its events

    def render(self) -> str:
        head = f"### {self.title}" + (f"  [{', '.join(self.tags)}]" if self.tags else "")
        parts = [head, f"`{self.path}`"] + (["```diff\n" + self.body.rstrip() + "\n```"] if self.body else [])
        if self.uses and self.uses.sites:
            more = (
                f" (showing {len(self.uses.sites)} of {self.uses.total})"
                if self.uses.total > len(self.uses.sites)
                else ""
            )
            parts.append(f"Used in {self.uses.total} place(s){more}:")
            parts += [f"- `{s.path}:{s.line}` {s.text}" for s in self.uses.sites]
        elif self.uses is not None:
            parts.append("No uses found outside its definition.")
        if self.events:
            ev = self.events
            more = f" (showing {len(ev.edges)})" if ev.total > len(ev.edges) else ""
            if ev.note:
                parts.append(ev.note)
            parts.append(f"Shares events with {ev.total} place(s) in other files{more}:")
            parts += [
                f"- `{e.path}:{e.line}`{f' in `{e.owner}`' if e.owner else ''}"
                f"{' [document/window]' if e.is_global else ''} ({', '.join(e.events)}) {e.text}"
                for e in ev.edges
            ]
            parts += [f"`{e.owner}` ({e.path}):\n```\n{e.snippet}\n```" for e in ev.edges if e.snippet]
        return "\n".join(parts)

    def trimmed(self, room: int) -> Item | None:
        """This item cut to whole lines so it renders within `room` chars, or None if too little fits."""
        overhead = len(self.render()) - len(self.body)
        if room - overhead < MIN_ITEM:
            return None
        kept, size = [], 0
        for line in self.body.splitlines():
            if size + len(line) + 1 > room - overhead - 80:  # 80: the truncation note
                break
            kept.append(line)
            size += len(line) + 1
        note = f"… truncated {len(self.body) - size:,} chars to fit the budget"
        return replace(self, body="\n".join([*kept, note]))


@dataclass
class Packet:
    base: str
    head: str
    files: list[dict]  # path, status, added, removed, kind
    items: list[Item]
    omitted: list[str]
    sources: dict[str, str] = field(default_factory=dict, repr=False)  # new text of each changed text file

    def reviewable(self) -> dict[str, str]:
        """Path → new text of the changed files a reviewer should read whole (source and config)."""
        return {
            f["path"]: self.sources[f["path"]]
            for f in self.files
            if f["kind"] in REVIEWABLE and f["path"] in self.sources
        }

    def markdown(self) -> str:
        out = [f"# Review packet: {self.base} → {self.head}", "", "| file | status | +/- |", "| --- | --- | --- |"]
        out += [f"| `{f['path']}` | {f['status']} | +{f['added']} −{f['removed']} |" for f in self.files]
        out.append("")
        out += [i.render() + "\n" for i in self.items]
        if self.omitted:
            out.append(f"Omitted over budget ({len(self.omitted)}): " + ", ".join(self.omitted))
        return "\n".join(out)

    def json(self) -> str:
        return json.dumps(
            {
                "base": self.base,
                "head": self.head,
                "files": self.files,
                "items": [asdict(i) for i in self.items],
                "omitted": self.omitted,
            },
            indent=2,
        )

    def render(self, fmt: str = "md") -> str:
        if fmt == "json":
            return self.json()
        if fmt == "html":
            from .html import render  # html imports this module

            return render(self)
        return self.markdown()


def _symbol_hunks(old_lines: list[str], new_lines: list[str], c: SymbolChange) -> list[Hunk]:
    """Diff of one symbol's span, as hunks numbered in real file lines."""
    a = old_lines[c.old.start - 1 : c.old.end] if c.old else []
    b = new_lines[c.new.start - 1 : c.new.end] if c.new else []
    off_a, off_b = (c.old.start - 1 if c.old else 0), (c.new.start - 1 if c.new else 0)
    hunks = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(2):
        (_, i1, _, j1, _), (_, _, i2, _, j2) = group[0], group[-1]
        h = Hunk(i1 + off_a + 1 if a else 0, i2 - i1, j1 + off_b + 1 if b else 0, j2 - j1)
        for tag, x1, x2, y1, y2 in group:
            if tag == "equal":
                h.lines += [" " + x for x in a[x1:x2]]
            else:
                h.lines += ["-" + x for x in a[x1:x2]] + ["+" + y for y in b[y1:y2]]
        hunks.append(h)
    return hunks


def _own_text(lines: list[str], sym, others: list) -> str:
    """A symbol's lines minus those of other changed symbols nested in it: each line counts for its
    innermost changed symbol only, so a component and its handlers don't claim the same events."""
    if sym is None:
        return ""
    inner = {
        n
        for o in others
        if o is not sym and sym.start <= o.start and o.end <= sym.end
        for n in range(o.start, o.end + 1)
    }
    return "\n".join(lines[n - 1] for n in range(sym.start, min(sym.end, len(lines)) + 1) if n not in inner)


def _rank(
    c: SymbolChange, u: blast.Uses | None, links: events.EventLinks | None, orphan: bool = False
) -> tuple[Prio, list[str]]:
    callers = bool(u and u.total)
    other_files = bool(u and any(s.path != c.path for s in u.sites))
    coupled = bool(links and any(e.is_global for e in links.edges))  # a page-level listener elsewhere sees its events
    tags = [
        t
        for t, on in (
            ("removed", c.status == "removed"),
            ("signature changed", c.signature_changed),
            ("used in other files", other_files),
            ("event-coupled", coupled),
            ("orphan", orphan),
        )
        if on
    ]
    if c.status == "removed" and callers:
        return Prio.STILL_REFERENCED, tags + ["still referenced"]
    if c.signature_changed and callers:
        return Prio.SIGNATURE_WITH_CALLERS, tags
    if other_files and c.status == "modified" or coupled:
        return Prio.USED_ELSEWHERE, tags
    if c.status == "modified" or orphan:  # new code nothing but tests reaches is worth a look
        return Prio.MODIFIED, tags
    return (Prio.MINOR if c.status == "removed" else Prio.ADDED), tags


def _fit(items: list[Item], budget: int) -> tuple[list[Item], list[str]]:
    """Keep items in order while they fit; trim the one that doesn't (to whole lines) or omit it."""
    kept, omitted, used = [], [], 0
    for i in items:
        size = len(i.render())
        if used + size > budget:
            t = i.trimmed(budget - used)
            if t is None:
                omitted.append(f"{i.path}: {i.title[:80]}")
                continue
            i, size = t, len(t.render())
        kept.append(i)
        used += size
    return kept, omitted


def build(
    repo: str | Path,
    base: str = "HEAD",
    head: str | None = None,
    budget: int = DEFAULT_BUDGET,
    paths: tuple[str, ...] = (),
) -> Packet:
    base_rev = resolve(repo, base)  # empty tree for a repo with no commits yet
    files = diff_files(repo, base_rev, head, paths)
    kinds = {f.path: file_kind(f) for f in files}
    items: list[Item] = []
    sources: dict[str, str] = {}
    parsed = []  # (file, old lines, new lines, changes) for text files
    for f in files:
        kind = kinds[f.path]
        if kind == "lockfile":
            items.append(
                Item(Prio.LOCKFILE, f.path, f"{f.path} (lockfile)", f"lockfile changed: +{f.added} −{f.removed}")
            )
        elif kind == "binary":
            items.append(Item(Prio.BINARY, f.path, f"{f.path} (binary)", "binary file changed"))
        else:
            old_src = read_at(repo, base_rev, f.old_path or f.path) if f.status != "A" else None
            new_src = read_at(repo, head, f.path) if f.status != "D" else None
            changes = compare(f.path, old_src, new_src) if kind == "source" else []
            parsed.append((f, (old_src or "").splitlines(), (new_src or "").splitlines(), changes))
            if new_src is not None:
                sources[f.path] = new_src
    all_changes = [c for *_, changes in parsed for c in changes]
    mods = blast.modules([f.path for f in files if f.status == "A" and kinds[f.path] == "source"], sources)
    hits = blast.word_hits(repo, head, blast.symbol_names(all_changes) | set(mods.values()))  # one grep for all
    found = iter(blast.uses_many(repo, all_changes, head, hits=hits))
    orphan_mods = blast.orphans(mods, hits)  # a whole file: its own item, not one per symbol
    for path, tests in orphan_mods.items():
        why = f"only tests mention it ({', '.join(tests)})" if tests else "nothing else in the repo mentions it"
        items.append(Item(Prio.MODIFIED, path, f"new module nothing imports: {why}", "", tags=["orphan"]))
    ev_index: events.Index | None = None  # built on first need: one grep for every event line
    for f, old_lines, new_lines, changes in parsed:
        covered_new: set[int] = set()
        covered_old: set[int] = set()
        olds, news = [c.old for c in changes if c.old], [c.new for c in changes if c.new]
        check_unused = f.path not in orphan_mods and not blast.is_entry(f.path, sources.get(f.path, ""))
        for c in changes:
            u = next(found)
            links = None
            names = events.events_in(_own_text(old_lines, c.old, olds) + "\n" + _own_text(new_lines, c.new, news))
            if names:
                if ev_index is None:
                    ev_index = events.index(repo, head)
                    ev_index.sources.update(sources)
                links = ev_index.edges(f.path, names)
            unused = None
            if c.new and check_unused:
                unused = blast.orphan_symbol(c, hits, new_lines[c.new.start - 1])
            prio, tags = _rank(c, u, links, bool(unused))
            title = f"{c.sym.kind} `{c.sym.qualname}` {c.status}" + (f": {unused}" if unused else "")
            if c.signature_changed:
                title += f": `{c.old.signature}` → `{c.new.signature}`"  # type: ignore[union-attr]
            body = "\n".join(h.text() for h in _symbol_hunks(old_lines, new_lines, c))
            items.append(Item(prio, f.path, title, body, u, tags, links))
            if c.new:
                covered_new |= set(range(c.new.start, c.new.end + 1))
            if c.old:
                covered_old |= set(range(c.old.start, c.old.end + 1))
        # changes outside any symbol (imports, top-level code, unparsed languages) go as hunks
        rest = [h for h in f.hunks if not (h.new_changed() <= covered_new and h.old_changed() <= covered_old)]
        if rest:
            if changes:
                prio, title = Prio.MINOR, f"{f.path}: changes outside symbols"
            else:
                prio, title = (Prio.ADDED if kinds[f.path] == "doc" else Prio.MODIFIED), f.path
            items.append(Item(prio, f.path, title, "\n".join(h.text() for h in rest)))
    items.sort(key=lambda i: (i.priority, i.path))
    kept, omitted = _fit(items, budget)
    summary = [
        {"path": f.path, "status": f.status, "added": f.added, "removed": f.removed, "kind": kinds[f.path]}
        for f in files
    ]
    return Packet(base, head or "working tree", summary, kept, omitted, sources)
