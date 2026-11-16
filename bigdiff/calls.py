"""Call sites a signature change breaks: calls that fit the old parameter list but not the new one.

Both lists come from the symbol's signature text (declaration up to the body), so no extra file
read is needed for them. Call sites are parsed from the caller's file at head. A call with a
spread (`*args`, `**kw`, `...xs`) can't be counted, so it's never flagged; neither is anything
that didn't fit the old signature either: that call was already off, or isn't this function.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .symbols import language


@dataclass(frozen=True)
class Params:
    positional: tuple[tuple[str, bool], ...]  # (name, has default), in order
    keyword_only: tuple[tuple[str, bool], ...] = ()
    varargs: bool = False  # *args / ...rest
    varkw: bool = False  # **kwargs
    by_name: bool = True  # Python: positional params can be passed by name (JS: no)


@dataclass(frozen=True)
class Call:
    line: int
    args: int  # positional arguments
    keywords: frozenset[str] = field(default_factory=frozenset)


def params(path: str, signature: str, kind: str) -> Params | None:
    """Parameters from a signature like `def f(a, b=1)`, `f = (a, b) =>` or `m(x: number)`."""
    lang = language(path)
    if kind == "class" or lang is None:
        return None
    try:
        return _py_params(signature, kind) if lang == "python" else _ts_params(lang, signature, kind)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _py_params(signature: str, kind: str) -> Params | None:
    tree = ast.parse(signature.strip() + ":\n    pass")
    fn = tree.body[0] if tree.body else None
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    a = fn.args
    pos = a.posonlyargs + a.args
    defaults = len(a.defaults)
    positional = [(p.arg, i >= len(pos) - defaults) for i, p in enumerate(pos)]
    if kind == "method" and positional and positional[0][0] in ("self", "cls"):
        positional = positional[1:]  # bound when called as obj.m(…)
    keyword_only = tuple((p.arg, d is not None) for p, d in zip(a.kwonlyargs, a.kw_defaults))
    return Params(tuple(positional), keyword_only, a.vararg is not None, a.kwarg is not None)


_TS_OPTIONAL = {"assignment_pattern", "optional_parameter"}


def _ts_params(lang: str, signature: str, kind: str) -> Params | None:
    from tree_sitter_language_pack import get_parser

    sig = signature.strip()
    code = f"class X {{ {sig} {{}} }}" if kind == "method" else f"{sig} {{}}"
    src = code.encode()
    tree = get_parser(lang).parse(src)
    formal = next(_find(tree.root_node, "formal_parameters"), None)
    if formal is None:  # x => …: one parameter, no parentheses
        arrow = next(_find(tree.root_node, "arrow_function"), None)
        if arrow is None or arrow.child_by_field_name("parameter") is None:
            return None
        return Params((("x", False),), by_name=False)
    positional, rest = [], False
    for p in formal.named_children:
        if p.type == "comment":
            continue
        target = p.child_by_field_name("pattern") if p.type in ("required_parameter", "optional_parameter") else p
        if p.type == "rest_pattern" or (target is not None and target.type == "rest_pattern"):
            rest = True
            continue
        if target is not None and target.type == "this":
            continue  # TS `this: Type` isn't passed
        optional = p.type in _TS_OPTIONAL or p.child_by_field_name("value") is not None
        positional.append((src[p.start_byte : p.end_byte].decode("utf-8", "replace"), optional))
    return Params(tuple(positional), varargs=rest, by_name=False)


def _find(node, type_: str):
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == type_:
            yield n
        stack.extend(reversed(n.children))


def calls(path: str, source: str, name: str, lines: set[int]) -> dict[int, Call]:
    """Countable calls of `name` (as `name(…)` or `obj.name(…)`) starting on the given lines."""
    lang = language(path)
    if lang is None or not lines:
        return {}
    try:
        found = _py_calls(source, name, lines) if lang == "python" else _ts_calls(lang, source, name, lines)
    except (SyntaxError, ValueError, RecursionError):
        return {}
    return {c.line: c for c in found}


def _py_calls(source: str, name: str, lines: set[int]):
    for n in ast.walk(ast.parse(source)):
        if not isinstance(n, ast.Call) or n.lineno not in lines:
            continue
        f = n.func
        if not ((isinstance(f, ast.Name) and f.id == name) or (isinstance(f, ast.Attribute) and f.attr == name)):
            continue
        if any(isinstance(a, ast.Starred) for a in n.args) or any(k.arg is None for k in n.keywords):
            continue  # *args / **kw: can't count
        yield Call(n.lineno, len(n.args), frozenset(k.arg for k in n.keywords if k.arg))


def _ts_calls(lang: str, source: str, name: str, lines: set[int]):
    from tree_sitter_language_pack import get_parser

    src = source.encode()
    for n in _find(get_parser(lang).parse(src).root_node, "call_expression"):
        line = n.start_point[0] + 1
        fn, args = n.child_by_field_name("function"), n.child_by_field_name("arguments")
        if line not in lines or fn is None or args is None or args.type != "arguments":
            continue  # tagged templates have no argument list
        callee = fn.child_by_field_name("property") if fn.type == "member_expression" else fn
        if callee is None or src[callee.start_byte : callee.end_byte].decode("utf-8", "replace") != name:
            continue
        given = [a for a in args.named_children if a.type != "comment"]
        if any(a.type == "spread_element" for a in given):
            continue
        yield Call(line, len(given))


def fits(p: Params, c: Call) -> bool:
    """Does every argument land on a parameter, with no required one left out? Python raises a
    TypeError otherwise; JS runs on, with an argument dropped or a parameter undefined."""
    names = [n for n, _ in p.positional]
    if c.args > len(names) and not p.varargs:
        return False
    bound = set(names[: c.args])
    allowed = set(names) | {n for n, _ in p.keyword_only} if p.by_name else set()
    for k in c.keywords:
        if k in bound or (k not in allowed and not p.varkw):
            return False
    missing = [n for n, d in p.positional[c.args :] if not d and n not in c.keywords]
    return not missing and all(d or n in c.keywords for n, d in p.keyword_only)


def broken(path: str, kind: str, name: str, old_sig: str, new_sig: str, sites: dict[str, set[int]], read):
    """(path, line) of the calls among `sites` (path → lines where `name` appears) that fit the old
    signature and not the new one. `read(path)` gives a caller's text at head."""
    old, new = params(path, old_sig, kind), params(path, new_sig, kind)
    if old is None or new is None or old == new:
        return []
    out = []
    for p, lines in sites.items():
        text = read(p)
        if text is None:
            continue
        for line, c in sorted(calls(p, text, name, lines).items()):
            if fits(old, c) and not fits(new, c):
                out.append((p, line))
    return out

