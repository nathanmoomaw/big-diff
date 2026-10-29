"""Symbols (functions, classes, methods) per file, and which ones a change touched.

Python uses the stdlib `ast` (exact). JS/TS/TSX use tree-sitter. Other files have no symbols:
their changes travel as plain hunks.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

LANGS = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}


@dataclass(frozen=True)
class Symbol:
    qualname: str  # Class.method for nested symbols
    kind: str  # function, class, method
    start: int  # 1-based, inclusive (decorators included)
    end: int
    signature: str  # first line, whitespace-collapsed
    body_hash: str

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]


@dataclass
class SymbolChange:
    path: str
    status: str  # added, removed, modified
    old: Symbol | None
    new: Symbol | None

    @property
    def sym(self) -> Symbol:
        return self.new or self.old  # type: ignore[return-value]

    @property
    def signature_changed(self) -> bool:
        return bool(self.old and self.new and self.old.signature != self.new.signature)


def language(path: str) -> str | None:
    return LANGS.get(PurePosixPath(path).suffix.lower())


def _hash(lines: list[str]) -> str:
    return hashlib.sha1("\n".join(x.rstrip() for x in lines).encode()).hexdigest()[:12]


def _make(qualname: str, kind: str, start: int, end: int, lines: list[str], sig: str) -> Symbol:
    """sig: declaration text from its start up to its body (one rule for every language)."""
    sig = " ".join(sig.split()).rstrip(" :{")  # whitespace-collapsed, so re-wrapping params isn't a change
    return Symbol(qualname, kind, start, end, sig, _hash(lines[start - 1 : end]))


def _span(lines: list[str], start: tuple[int, int], stop: tuple[int, int]) -> str:
    """Source text between two (1-based line, 0-based column) positions."""
    (l1, c1), (l2, c2) = start, stop
    if l1 == l2:
        return lines[l1 - 1][c1:c2]
    return "\n".join([lines[l1 - 1][c1:], *lines[l1 : l2 - 1], lines[l2 - 1][:c2]])


def symbols(path: str, source: str) -> list[Symbol]:
    lang = language(path)
    if lang is None:
        return []
    lines = source.splitlines()
    try:
        return _py_symbols(source, lines) if lang == "python" else _ts_symbols(lang, source, lines)
    except (SyntaxError, ValueError):  # ValueError: NUL bytes
        return []  # half-written file: fall back to plain hunks


def _py_symbols(source: str, lines: list[str]) -> list[Symbol]:
    out: list[Symbol] = []

    def walk(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = f"{prefix}{child.name}"
                start = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                kind = "class" if isinstance(child, ast.ClassDef) else ("method" if in_class else "function")
                # signature: from the def/class keyword (decorators excluded) up to the body
                sig = _span(lines, (child.lineno, child.col_offset), (child.body[0].lineno, child.body[0].col_offset))
                out.append(_make(q, kind, start, child.end_lineno or child.lineno, lines, sig))
                walk(child, q + ".", isinstance(child, ast.ClassDef))
            else:  # defs under if TYPE_CHECKING / try-except fallbacks / with blocks
                walk(child, prefix, in_class)

    walk(ast.parse(source), "", False)
    return out


_TS_DECL = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "method_definition": "method",
    "interface_declaration": "class",
}
_TS_FN_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}


# Calls that wrap a function and return it (or a component built from it). Not map/useState/useMemo:
# those return values, so `const doubled = items.map((x) => …)` is not a function symbol.
WRAPPERS = {"memo", "forwardRef", "useCallback", "debounce", "throttle", "observer", "once", "lazy"}


def _unwrap(node, src: bytes):
    """The function inside wrapper calls like memo(…), forwardRef(…), useCallback(…): first argument only."""
    while node is not None and node.type == "call_expression":
        fn, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
        callee = (
            src[fn.start_byte : fn.end_byte].decode("utf-8", "replace").rsplit(".", 1)[-1] if fn is not None else ""
        )
        first = args.named_children[0] if args is not None and args.named_children else None
        if (
            callee not in WRAPPERS
            or first is None
            or not (first.type in _TS_FN_VALUES or first.type == "call_expression")
        ):
            return None
        node = first
    return node


def _ts_symbols(lang: str, source: str, lines: list[str]) -> list[Symbol]:
    from tree_sitter_language_pack import get_parser

    src = source.encode()
    tree = get_parser(lang).parse(src)
    out: list[Symbol] = []

    def text(n) -> str:
        return src[n.start_byte : n.end_byte].decode("utf-8", "replace")

    def header(n) -> str:
        """Declaration text up to its body: name and full parameter list, however wrapped."""
        body = n.child_by_field_name("body")
        return src[n.start_byte : body.start_byte if body is not None else n.end_byte].decode("utf-8", "replace")

    def walk(node, prefix: str) -> None:
        for child in node.children:
            kind = _TS_DECL.get(child.type)
            name_node = child.child_by_field_name("name") if kind else None
            if kind and name_node is not None:
                q = f"{prefix}{text(name_node)}"
                out.append(_make(q, kind, child.start_point[0] + 1, child.end_point[0] + 1, lines, header(child)))
                walk(child, q + ".")
                continue
            if child.type == "variable_declarator":  # const f = () => …
                raw, name_node = child.child_by_field_name("value"), child.child_by_field_name("name")
                value = _unwrap(raw, src)  # memo(function X…), useCallback((e) => …), forwardRef(…)
                if value is not None and name_node is not None and value.type in _TS_FN_VALUES:
                    decl = child.parent if child.parent is not None else child  # include `const`
                    q = f"{prefix}{text(name_node)}"
                    sig = f"{text(name_node)} = {header(value)}"
                    out.append(_make(q, "function", decl.start_point[0] + 1, child.end_point[0] + 1, lines, sig))
                    walk(raw, q + ".")  # the whole wrapped call: other arguments can hold symbols too
                    continue
            walk(child, prefix)

    walk(tree.root_node, "")
    return out


def _keyed(syms: list[Symbol]) -> dict[tuple[str, int], Symbol]:
    """Key by (qualname, nth occurrence) so same-named overloads stay distinct."""
    seen: dict[str, int] = {}
    out = {}
    for s in syms:
        i = seen.get(s.qualname, 0)
        seen[s.qualname] = i + 1
        out[(s.qualname, i)] = s
    return out


def _own_hash(sym: Symbol, syms: list[Symbol], lines: list[str]) -> str:
    """Hash of a symbol's lines minus its nested symbols' lines (its own code only)."""
    inner = {
        n for s in syms if s is not sym and sym.start <= s.start and s.end <= sym.end for n in range(s.start, s.end + 1)
    }
    own = [lines[n - 1].rstrip() for n in range(sym.start, sym.end + 1) if n not in inner and n - 1 < len(lines)]
    return _hash([x for x in own if x])  # blank lines between children aren't the parent's code


def compare(path: str, old_src: str | None, new_src: str | None) -> list[SymbolChange]:
    """Symbols added, removed or whose body changed between two versions of one file.

    A parent (class, or a function holding inner functions) counts as modified only if its own
    lines changed, so editing one method reports the method, not the whole class as well.
    """
    old_list = symbols(path, old_src) if old_src is not None else []
    new_list = symbols(path, new_src) if new_src is not None else []
    old_lines = old_src.splitlines() if old_src else []
    new_lines = new_src.splitlines() if new_src else []
    old, new = _keyed(old_list), _keyed(new_list)
    changes = []
    for k, s in new.items():
        o = old.get(k)
        if o is None:
            changes.append(SymbolChange(path, "added", None, s))
        elif o.body_hash != s.body_hash and _own_hash(o, old_list, old_lines) != _own_hash(s, new_list, new_lines):
            changes.append(SymbolChange(path, "modified", o, s))
    changes += [SymbolChange(path, "removed", o, None) for k, o in old.items() if k not in new]
    return changes
