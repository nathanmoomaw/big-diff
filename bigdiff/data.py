"""Structural diffs for data: JSON (any nesting) and CSV (rows matched by a key column).

Text diffs of data files report reformatting and reordering as change. These report only
what differs in value, each with a path like `$.items[id=7].price`.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Op:
    path: str
    op: str  # added, removed, changed, type
    old: Any = None
    new: Any = None

    def render(self) -> str:
        if self.op == "added":
            return f"+ {self.path} = {json.dumps(self.new, default=str)}"
        if self.op == "removed":
            return f"- {self.path} (was {json.dumps(self.old, default=str)})"
        return f"~ {self.path}: {json.dumps(self.old, default=str)} → {json.dumps(self.new, default=str)}"


Same = Callable[[Any, Any], bool]


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)  # bool is not a number here


def same_value(a: Any, b: Any, tol: float = 0.0) -> bool:
    """JSON leaf equality: numbers within tol, NaN equals NaN."""
    if _is_num(a) and _is_num(b):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return True
        return a == b if tol == 0 else math.isclose(a, b, rel_tol=0, abs_tol=tol)
    return a == b


def same_cell(a: Any, b: Any, tol: float = 0.0) -> bool:
    """CSV leaf equality: cells compare as text ('00501' ≠ '501'); with a tolerance, two numeric
    cells compare as numbers."""
    if a == b:
        return True
    if tol and isinstance(a, str) and isinstance(b, str):
        try:
            return math.isclose(float(a), float(b), rel_tol=0, abs_tol=tol)
        except ValueError:
            return False
    return False


def diff(
    a: Any, b: Any, key: str | None = None, tol: float = 0.0, path: str = "$", same: Same | None = None
) -> list[Op]:
    """Value differences from a to b. Lists of objects that all carry `key` match by it, not by index."""
    same = same or partial(same_value, tol=tol)
    if isinstance(a, dict) and isinstance(b, dict):
        return _diff_maps(
            a, b, lambda k: f"{path}.{k}" if str(k).isidentifier() else f"{path}[{json.dumps(k)}]", key, same
        )
    if isinstance(a, list) and isinstance(b, list):
        if key and all(isinstance(x, dict) and key in x for x in a + b):
            try:
                am, bm = {x[key]: x for x in a}, {x[key]: x for x in b}
            except TypeError:  # unhashable key values: match by index instead
                am = bm = None
            if am is not None and len(am) == len(a) and len(bm) == len(b):  # keys unique, else by index
                return _diff_maps(am, bm, lambda k: f"{path}[{key}={k}]", key, same)
        return _diff_maps(dict(enumerate(a)), dict(enumerate(b)), lambda i: f"{path}[{i}]", key, same)
    if type(a) is not type(b) and not (_is_num(a) and _is_num(b)):
        return [Op(path, "type", a, b)]
    return [] if same(a, b) else [Op(path, "changed", a, b)]


def _order(k: Any) -> tuple:
    return (0, k, "") if _is_num(k) else (1, 0, str(k))


def _diff_maps(am: dict, bm: dict, path_of: Callable[[Any], str], key: str | None, same: Same) -> list[Op]:
    """Removed / added / recursed entries of two mappings, in key order (no re-sorting later)."""
    ops: list[Op] = []
    for k in sorted(am.keys() | bm.keys(), key=_order):
        if k not in bm:
            ops.append(Op(path_of(k), "removed", old=am[k]))
        elif k not in am:
            ops.append(Op(path_of(k), "added", new=bm[k]))
        else:
            ops += diff(am[k], bm[k], key, path=path_of(k), same=same)
    return ops


def read_csv(text: str) -> tuple[list[str], list[dict]]:
    """Header and rows, cells kept as text."""
    r = csv.DictReader(io.StringIO(text))
    return list(r.fieldnames or []), list(r)


class UnknownKind(ValueError):
    """Neither the names nor the content say JSON or CSV."""


def diff_text(a_text: str, b_text: str, kind: str | None, key: str | None = None, tol: float = 0.0) -> list[Op]:
    """Diff two documents of kind 'json' or 'csv'; None tries JSON and raises UnknownKind if it doesn't parse."""
    if kind in (None, "json"):
        try:
            a, b = json.loads(a_text), json.loads(b_text)
        except ValueError:
            if kind:
                raise
            raise UnknownKind("can't tell the format: pass kind json or csv") from None
        return diff(a, b, key, tol)
    if kind == "csv":
        (cols_a, a), (cols_b, b) = read_csv(a_text), read_csv(b_text)
        ops = [Op(f"$columns.{c}", "removed", old=c) for c in cols_a if c not in cols_b]
        ops += [Op(f"$columns.{c}", "added", new=c) for c in cols_b if c not in cols_a]  # a pure reorder is no change
        return ops + diff(a, b, key, same=partial(same_cell, tol=tol))
    raise ValueError(f"unknown kind {kind!r}")


def kind_of(path: str) -> str | None:
    p = path.lower()
    return "json" if p.endswith((".json", ".geojson")) else "csv" if p.endswith(".csv") else None


def diff_paths(
    a: str | Path, b: str | Path, kind: str | None = None, key: str | None = None, tol: float = 0.0
) -> list[Op]:
    """Diff two files; the kind comes from the extension unless given, else the content (JSON)."""
    kind = kind or kind_of(str(a)) or kind_of(str(b))
    return diff_text(Path(a).read_text(), Path(b).read_text(), kind, key, tol)
