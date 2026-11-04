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
    shape: str = ""  # hash of its code with layout, comments and quote style left out ("" = unknown)
    renamed_shape: str = ""  # the same with its own name left out: a pure rename keeps it
    decorated: bool = False  # @decorator: used through it (a route, a hook)
    exported: bool = False  # export / export default / export { name }: part of a module's API

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]


@dataclass
class SymbolChange:
    path: str
    status: str  # added, removed, modified, reformatted (same code, new layout), renamed (same code, new name)
    old: Symbol | None
    new: Symbol | None

    @property
    def sym(self) -> Symbol:
        return self.new or self.old  # type: ignore[return-value]

    @property
    def signature_changed(self) -> bool:
        return self.status == "modified" and self.old.signature != self.new.signature  # type: ignore[union-attr]

    @property
    def cosmetic(self) -> bool:
        """Nothing a reviewer needs to read: same code, only layout or the name differs."""
        return self.status in ("reformatted", "renamed")

    @property
    def lookup_name(self) -> str:
        """The name whose uses matter: a rename's old name (anything still calling it is broken)."""
        return self.old.name if self.status == "renamed" else self.sym.name  # type: ignore[union-attr]


def language(path: str) -> str | None:
    return LANGS.get(PurePosixPath(path).suffix.lower())


def _hash(lines: list[str]) -> str:
    return hashlib.sha1("\n".join(x.rstrip() for x in lines).encode()).hexdigest()[:12]


def _make(qualname: str, kind: str, start: int, end: int, lines: list[str], sig: str, **flags) -> Symbol:
    """sig: declaration text from its start up to its body (one rule for every language)."""
    sig = " ".join(sig.split()).rstrip(" :{")  # whitespace-collapsed, so re-wrapping params isn't a change
    return Symbol(qualname, kind, start, end, sig, _hash(lines[start - 1 : end]), **flags)


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


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
    except (SyntaxError, ValueError, RecursionError):  # ValueError: NUL bytes; RecursionError: deep nesting
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
                shape = ast.dump(child)  # no positions: layout, comments, quotes and parens drop out
                name, child.name = child.name, ""
                renamed = ast.dump(child)
                child.name = name
                out.append(
                    _make(
                        q, kind, start, child.end_lineno or child.lineno, lines, sig,
                        shape=_digest(shape), renamed_shape=_digest(renamed), decorated=bool(child.decorator_list),
                    )
                )  # fmt: skip
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
_CLOSERS = ([")"], ["]"], ["}"])
_HOLES = {",", "[", "(", "{"}  # a comma after these is an array hole (`[,]`, `[a,,]`), not a trailing comma


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

    def shapes(n, name, keyword: str = "") -> tuple[str, str]:
        """(shape, renamed shape) from n's tree: its node types and tokens, with comments, `;`, trailing
        commas and quote style left out. The node types keep ASI honest: `return\\nx` isn't `return x`."""
        toks: list[str] = [keyword]  # const / let / var for `const f = …`: reassigning and hoisting differ
        named_at = -1

        def leaves(x) -> None:
            nonlocal named_at
            if x.type == "comment":
                return
            if x.child_count == 0 or x.type in ("string_fragment", "template_string"):
                if x.start_byte == name.start_byte:
                    named_at = len(toks)
                toks.append('"' if x.type in ("'", '"') else text(x))
                return
            toks.append("\x01" + x.type)  # \x01/\x02 can't collide with a token's text
            for y in x.children:
                leaves(y)
            toks.append("\x02")

        leaves(n)
        keep = [
            i
            for i, t in enumerate(toks)
            if t != ";" and not (t == "," and toks[i + 1 : i + 2] in _CLOSERS and toks[i - 1] not in _HOLES)
        ]
        shape = "\x00".join(toks[i] for i in keep)
        return _digest(shape), _digest("\x00".join("" if i == named_at else toks[i] for i in keep))

    def flags(decl, name: str) -> dict:
        """decorated / exported, from the declaration's place in the tree. name: "" for nested
        symbols, which `export { … }` can't name."""
        export = decl.parent if decl.parent is not None and decl.parent.type == "export_statement" else None
        prev = decl.prev_named_sibling  # a method's decorators are its siblings in the class body
        decorated = (prev is not None and prev.type == "decorator") or any(
            c.type == "decorator" for n in (decl, export) if n is not None for c in n.children
        )
        return {"decorated": decorated, "exported": export is not None or name in exports}

    exports = {  # export { name, other as alias }; export default name
        text(spec.child_by_field_name("name"))
        for clause in _all(tree.root_node, "export_clause")
        if clause.parent is None or clause.parent.child_by_field_name("source") is None  # not `… from './x'`
        for spec in clause.named_children
        if spec.type == "export_specifier" and spec.child_by_field_name("name") is not None
    } | {
        text(c)
        for st in tree.root_node.children
        if st.type == "export_statement"
        for c in st.named_children
        if c.type == "identifier"
    }

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
                shape, renamed = shapes(child, name_node)
                out.append(
                    _make(
                        q, kind, child.start_point[0] + 1, child.end_point[0] + 1, lines, header(child),
                        shape=shape, renamed_shape=renamed, **flags(child, "" if prefix else text(name_node)),
                    )
                )  # fmt: skip
                walk(child, q + ".")
                continue
            if child.type == "variable_declarator":  # const f = () => …
                raw, name_node = child.child_by_field_name("value"), child.child_by_field_name("name")
                value = _unwrap(raw, src)  # memo(function X…), useCallback((e) => …), forwardRef(…)
                if value is not None and name_node is not None and value.type in _TS_FN_VALUES:
                    decl = child.parent if child.parent is not None else child  # include `const`
                    q = f"{prefix}{text(name_node)}"
                    sig = f"{text(name_node)} = {header(value)}"
                    shape, renamed = shapes(child, name_node, text(decl.children[0]))
                    out.append(
                        _make(
                            q, "function", decl.start_point[0] + 1, child.end_point[0] + 1, lines, sig,
                            shape=shape, renamed_shape=renamed, **flags(decl, "" if prefix else text(name_node)),
                        )
                    )  # fmt: skip
                    walk(raw, q + ".")  # the whole wrapped call: other arguments can hold symbols too
                    continue
            walk(child, prefix)

    walk(tree.root_node, "")
    return out


def _all(node, type_: str):
    """Every descendant of one node type."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == type_:
            yield n
        stack.extend(n.children)


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
            same = o.shape and (o.shape, o.exported) == (s.shape, s.exported)  # newly exported is new API
            changes.append(SymbolChange(path, "reformatted" if same else "modified", o, s))
    changes += [SymbolChange(path, "removed", o, None) for k, o in old.items() if k not in new]
    return _pair_renames(changes)


def _pair_renames(changes: list[SymbolChange]) -> list[SymbolChange]:
    """A removed and an added symbol with the same code under one parent, each the only one of its
    kind, are one rename. A rename that also edits the body stays removed + added. What's nested in
    a renamed symbol is unchanged too (its shape covers them), so it isn't reported."""

    def key(s: Symbol) -> tuple:
        return (s.qualname.rpartition(".")[0], s.kind, s.exported, s.renamed_shape)

    gone: dict[tuple, list[SymbolChange]] = {}
    born: dict[tuple, list[SymbolChange]] = {}
    for c in changes:
        if c.sym.renamed_shape and c.status in ("removed", "added"):
            (gone if c.status == "removed" else born).setdefault(key(c.sym), []).append(c)
    pairs = {id(g[0]): b[0] for k, g in gone.items() if len(g) == 1 and len(b := born.get(k, [])) == 1}
    paired = {id(b) for b in pairs.values()}
    inside = tuple(
        f"{x.sym.qualname}." for c in changes if id(c) in pairs for x in (c, pairs[id(c)])
    )  # Old.method removed and New.method added under a renamed class
    out = []
    for c in changes:
        if id(c) in pairs:
            out.append(SymbolChange(c.path, "renamed", c.old, pairs[id(c)].new))
        elif id(c) not in paired and not (inside and c.sym.qualname.startswith(inside)):
            out.append(c)
    return out
