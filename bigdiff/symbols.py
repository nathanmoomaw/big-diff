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
    shape: str = ""  # hash of its code minus layout, comments, quote style and its own name ("" = unknown)
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
    def name_gone(self) -> bool:
        """Its name no longer exists at head: anything still using it is broken."""
        return self.status in ("removed", "renamed")

    @property
    def lookup_name(self) -> str:
        """The name whose uses matter: the old one once it's gone."""
        return (self.old if self.name_gone else self.sym).name  # type: ignore[union-attr]


def language(path: str) -> str | None:
    return LANGS.get(PurePosixPath(path).suffix.lower())


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _hash(lines: list[str]) -> str:
    return _digest("\n".join(x.rstrip() for x in lines))


def _make(qualname: str, kind: str, start: int, end: int, lines: list[str], sig: str, **flags) -> Symbol:
    """sig: declaration text from its start up to its body (one rule for every language)."""
    sig = " ".join(sig.split()).rstrip(" :{")  # whitespace-collapsed, so re-wrapping params isn't a change
    return Symbol(qualname, kind, start, end, sig, _hash(lines[start - 1 : end]), **flags)


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
                name, child.name = child.name, ""  # a rename keeps the shape
                try:
                    shape = _digest(ast.dump(child))  # no positions: layout, comments, quotes and parens drop out
                finally:
                    child.name = name
                end = child.end_lineno or child.lineno
                out.append(_make(q, kind, start, end, lines, sig, shape=shape, decorated=bool(child.decorator_list)))
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
_CLOSERS = {")", "]", "}"}
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

    def shape(n, name, keyword: str = "") -> str:
        """Hash of n's tree: node types and tokens, without comments, `;`, trailing commas, quote
        style or its own name. The node types keep ASI honest: `return\\nx` isn't `return x`."""
        toks = [keyword]  # const / let / var for `const f = …`: reassigning and hoisting differ

        def leaves(x) -> None:
            t = x.type
            if t in ("comment", ";") or t == "," and _trailing(x):
                return
            if x == name:
                toks.append("")
            elif x.child_count == 0 or t in ("string_fragment", "template_string"):
                toks.append('"' if t in ("'", '"') else text(x))
            else:
                toks.append("\x01" + t)  # \x01/\x02 can't collide with a token's text
                for y in x.children:
                    leaves(y)
                toks.append("\x02")

        leaves(n)
        return _digest("\x00".join(toks))

    def flags(decl, name: str) -> dict:
        """decorated / exported, from the declaration's place in the tree. name: "" for nested
        symbols, which `export { … }` can't name."""
        export = decl.parent if decl.parent is not None and decl.parent.type == "export_statement" else None
        prev = decl.prev_named_sibling  # a method's decorators are its siblings in the class body
        decorated = (prev is not None and prev.type == "decorator") or any(
            c.type == "decorator" for n in (decl, export) if n is not None for c in n.children
        )
        return {"decorated": decorated, "exported": export is not None or name in exports}

    exports = set()  # export { name, other as alias }; export default name. Not `export { x } from './y'`
    for st in tree.root_node.children:
        if st.type == "export_statement" and st.child_by_field_name("source") is None:
            for c in st.named_children:
                if c.type == "identifier":
                    exports.add(text(c))
                elif c.type == "export_clause":
                    exports |= {
                        text(spec.child_by_field_name("name"))
                        for spec in c.named_children
                        if spec.type == "export_specifier" and spec.child_by_field_name("name") is not None
                    }

    def emit(prefix: str, name_node, kind: str, decl, node, sig: str, keyword: str = "") -> str:
        """Add the symbol `node` declares (decl: its whole declaration, `const` included); its qualname."""
        q = f"{prefix}{text(name_node)}"
        start, end = decl.start_point[0] + 1, node.end_point[0] + 1
        fl = flags(decl, "" if prefix else text(name_node))
        out.append(_make(q, kind, start, end, lines, sig, shape=shape(node, name_node, keyword), **fl))
        return q

    def header(n) -> str:
        """Declaration text up to its body: name and full parameter list, however wrapped."""
        body = n.child_by_field_name("body")
        return src[n.start_byte : body.start_byte if body is not None else n.end_byte].decode("utf-8", "replace")

    def walk(node, prefix: str) -> None:
        for child in node.children:
            kind = _TS_DECL.get(child.type)
            name_node = child.child_by_field_name("name") if kind else None
            if kind and name_node is not None:
                q = emit(prefix, name_node, kind, child, child, header(child))
                walk(child, q + ".")
                continue
            if child.type == "variable_declarator":  # const f = () => …
                raw, name_node = child.child_by_field_name("value"), child.child_by_field_name("name")
                value = _unwrap(raw, src)  # memo(function X…), useCallback((e) => …), forwardRef(…)
                if value is not None and name_node is not None and value.type in _TS_FN_VALUES:
                    decl = child.parent if child.parent is not None else child  # include `const`
                    sig = f"{text(name_node)} = {header(value)}"
                    q = emit(prefix, name_node, "function", decl, child, sig, text(decl.children[0]))
                    walk(raw, q + ".")  # the whole wrapped call: other arguments can hold symbols too
                    continue
            walk(child, prefix)

    walk(tree.root_node, "")
    return out


def _trailing(comma) -> bool:
    """A comma right before a closing bracket, after an element (not an array hole)."""
    nxt, prev = comma.next_sibling, comma.prev_sibling
    return nxt is not None and nxt.type in _CLOSERS and prev is not None and prev.type not in _HOLES


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
            # same name here, so the same shape is the same code; newly exported is new API
            same = o.shape and (o.shape, o.exported) == (s.shape, s.exported)
            changes.append(SymbolChange(path, "reformatted" if same else "modified", o, s))
    changes += [SymbolChange(path, "removed", o, None) for k, o in old.items() if k not in new]
    return _pair_renames(changes)


def _pair_renames(changes: list[SymbolChange]) -> list[SymbolChange]:
    """A removed and an added symbol with the same code under one parent, each the only one of its
    kind, are one rename. A rename that also edits the body stays removed + added. What's nested in
    a renamed symbol is unchanged too (its shape covers them), so it isn't reported."""

    def key(s: Symbol) -> tuple:
        return (s.qualname.rpartition(".")[0], s.kind, s.exported, s.shape)

    gone: dict[tuple, list[int]] = {}
    born: dict[tuple, list[int]] = {}
    for i, c in enumerate(changes):
        if c.sym.shape and c.status in ("removed", "added"):
            (gone if c.status == "removed" else born).setdefault(key(c.sym), []).append(i)
    pairs = {g[0]: b[0] for k, g in gone.items() if len(g) == 1 and len(b := born.get(k, [])) == 1}  # old → new
    # Old.method removed and New.method added under a renamed class
    inside = tuple(f"{changes[i].sym.qualname}." for pair in pairs.items() for i in pair)
    out = []
    for i, c in enumerate(changes):
        if i in pairs:
            out.append(SymbolChange(c.path, "renamed", c.old, changes[pairs[i]].new))
        elif i not in pairs.values() and not (inside and c.sym.qualname.startswith(inside)):
            out.append(c)
    return out
