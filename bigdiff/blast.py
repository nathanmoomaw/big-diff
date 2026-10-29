"""Blast radius: where else the changed symbols are used.

One word-boundary `git grep` over every tracked text file (templates, HTML and shell included),
for all changed names at once. It over-reports (same name, other meaning) but doesn't miss a
textual use, which is the failure that cost us in the PR-gate test: a bug in one file whose
effect showed up in another. A jCodeMunch-backed resolver is on the roadmap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .gitdiff import grep
from .symbols import SymbolChange

# Names too generic to search: every file has them, so callers would be noise.
COMMON = {
    "__init__", "__call__", "__repr__", "__str__", "__eq__", "__hash__", "__enter__", "__exit__",
    "constructor", "render", "main", "init", "setup", "run", "get", "set", "update", "toString",
    "handle", "default", "test", "call", "apply", "value", "data", "name",
}  # fmt: skip
MAX_SITES = 12  # per symbol; the total is still reported


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    text: str


@dataclass
class Uses:
    name: str
    total: int
    sites: list[Site]  # the first MAX_SITES, other files first
    only_tests: list[str] | None = None  # every use is in these test files ([] = no uses); None otherwise


def searchable(name: str) -> bool:
    return len(name) >= 3 and name not in COMMON and not name.startswith("test")


def word_hits(repo: str | Path, ref: str | None, names: set[str]) -> dict[str, list[Site]]:
    """Every whole-word mention of each name at ref (None = working tree, untracked included). One grep."""
    hits: dict[str, list[Site]] = {n: [] for n in names}
    if not names:
        return hits
    longest_first = sorted(names, key=len, reverse=True)
    word = re.compile(r"(?<![\w$])(" + "|".join(map(re.escape, longest_first)) + r")(?![\w$])")
    for path, num, text in grep(repo, ref, "-w", "-F", *[arg for n in longest_first for arg in ("-e", n)]):
        for n in {m.group(1) for m in word.finditer(text)}:
            hits[n].append(Site(path, num, text.strip()[:200]))
    return hits


def symbol_names(changes: list[SymbolChange]) -> set[str]:
    return {c.sym.name for c in changes if searchable(c.sym.name)}


def uses_many(
    repo: str | Path,
    changes: list[SymbolChange],
    ref: str | None = None,
    max_sites: int = MAX_SITES,
    hits: dict[str, list[Site]] | None = None,
) -> list[Uses | None]:
    """Uses of each change (aligned with `changes`; None for generic names) at ref, outside each
    symbol's own definition. `hits` from a shared `word_hits` call, or one grep here."""
    if hits is None:
        hits = word_hits(repo, ref, symbol_names(changes))
    result: list[Uses | None] = []
    for c in changes:
        if not searchable(c.sym.name):
            result.append(None)
            continue
        own = c.new  # a removed symbol has no definition left at head to exclude
        sites = [s for s in hits[c.sym.name] if not (own and s.path == c.path and own.start <= s.line <= own.end)]
        # callers in other files first: those are the ones a file-local review can't see
        sites.sort(key=lambda s: (s.path == c.path, s.path, s.line))
        result.append(Uses(c.sym.name, len(sites), sites[:max_sites], only_tests({s.path for s in sites})))
    return result


MAIN_GUARD = re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]""", re.M)
# Run by a platform, not imported: serverless handlers, file-based routes, scripts, migrations.
ENTRY_DIRS = {"lambda", "lambdas", "functions", "netlify", "api", "pages", "routes", "bin", "scripts", "migrations"}
HANDLER = re.compile(r"^(export\s+(async\s+)?(const|function)\s+handler\b|exports\.handler\b|def\s+(lambda_)?handler\()", re.M)


def is_test(path: str) -> bool:
    p = Path(path)
    return (
        any(part in ("test", "tests", "__tests__") for part in p.parts[:-1])
        or p.name.startswith("test_")
        or p.stem.endswith(("_test", ".test", ".spec"))
    )


def only_tests(paths: set[str]) -> list[str] | None:
    """The paths, sorted, if all of them are tests (none at all counts); else None."""
    return sorted(paths) if all(map(is_test, paths)) else None


def unused(tests: list[str], verb: str) -> str:
    """How an orphan is described: who still uses it, if anyone."""
    return f"only tests {verb} it ({', '.join(tests)})" if tests else f"nothing else {verb}s it"


def module_name(path: str) -> str:
    """What other code calls a file when it imports it: its name up to the first dot
    (engine.worker.js → engine), or its folder for index/__init__."""
    p = Path(path)
    stem = p.name.split(".")[0]
    return p.parent.name if stem in ("index", "__init__") and p.parent.name else stem


def is_entry(path: str, src: str) -> bool:
    """Code something outside the repo runs: a script, a serverless handler, a file-based route."""
    return (
        any(part in ENTRY_DIRS for part in Path(path).parts[:-1])
        or src.startswith("#!")
        or MAIN_GUARD.search(src) is not None
        or HANDLER.search(src) is not None
    )


def modules(paths: list[str], sources: dict[str, str]) -> dict[str, str]:
    """New source files worth an orphan check → their module names. Entry points, tests and
    generic names (which can't be searched) are left out."""
    out = {}
    for p in paths:
        name = module_name(p)
        if not (is_test(p) or is_entry(p, sources.get(p, "")) or not searchable(name)):
            out[p] = name
    return out


def orphans(mods: dict[str, str], hits: dict[str, list[Site]]) -> dict[str, list[str]]:
    """Modules nothing outside tests mentions → the test files that do (maybe none). Any word-match
    elsewhere counts as a use, so a name meaning something else hides an orphan rather than inventing
    one: this check should only speak up when it's sure."""
    found = {}
    for p, name in mods.items():
        tests = only_tests({s.path for s in hits[name]} - {p})
        if tests is not None:
            found[p] = tests
    return found


def orphan_symbol(c: SymbolChange, u: Uses | None, first_line: str) -> str | None:
    """Why a new symbol looks unused, or None. Only tests using it counts unless it's decorated (a
    route or hook is used through its decorator). Nothing using it counts only for a plain
    function or class: methods can be overrides, exported names can be API. The caller has
    already left out test files and entry points."""
    head = first_line.lstrip()
    if c.status != "added" or u is None or u.only_tests is None or head.startswith("@"):
        return None
    if not u.only_tests and (c.sym.kind == "method" or head.startswith("export")):
        return None
    return unused(u.only_tests, "use")
