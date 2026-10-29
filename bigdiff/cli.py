"""bigdiff command line.

bigdiff review [BASE] [--head REF] [--format md|json|html] [--budget CHARS] [-o FILE] [PATH…]
bigdiff check  [BASE] [--head REF] [--model haiku|sonnet|opus]   # packet → one model review (`review` extra)
bigdiff data   A B [--key ID] [--tol 0.001]   # JSON / CSV value diff
bigdiff image  A B [--threshold 16] [--heatmap OUT.png]
bigdiff serve  [--port 8780] [--no-open]     # local web UI: text, data, images, git refs
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import data, image, packet
from .gitdiff import git
from .models import MODELS


def _repo() -> str:
    return git(".", "rev-parse", "--show-toplevel").strip()


def cmd_review(a: argparse.Namespace) -> int:
    repo = _repo()
    paths = tuple(os.path.relpath(Path(x).resolve(), repo) for x in a.paths)  # cwd-relative → repo-relative
    p = packet.build(repo, a.base, a.head, a.budget, paths)
    out = p.render(a.format)
    if a.output:
        Path(a.output).write_text(out)
        print(f"wrote {a.output}: {len(p.items)} item(s), {len(p.omitted)} omitted", file=sys.stderr)
    else:
        print(out)
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    try:
        from .review import check
    except ImportError:
        print("bigdiff check needs the review extra: uv run --extra review bigdiff check …", file=sys.stderr)
        return 2
    r = check(_repo(), a.base, a.head, a.model, a.budget, a.effort)
    if r is None:
        print("no changed source files to review")
        return 0
    print(r.report())
    return 1 if r.findings else 0


def cmd_data(a: argparse.Namespace) -> int:
    ops = data.diff_paths(a.a, a.b, a.kind, a.key, a.tol)
    for op in ops:
        print(op.render())
    print(f"{len(ops)} difference(s)", file=sys.stderr)
    return 1 if ops else 0


def cmd_image(a: argparse.Namespace) -> int:
    d = image.diff(a.a, a.b, a.threshold, a.heatmap)
    print(d.render())
    return 1 if d.changed_px or d.size_a != d.size_b else 0


def cmd_serve(a: argparse.Namespace) -> int:
    from .web import serve

    serve(a.port, not a.no_open)
    return 0


COMMANDS = {"review": cmd_review, "check": cmd_check, "data": cmd_data, "image": cmd_image, "serve": cmd_serve}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bigdiff", description="Diff tool for code review.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("review", "check"):
        s = sub.add_parser(name)
        s.add_argument("base", nargs="?", default="HEAD")
        s.add_argument("--head", default=None, help="compare to this ref instead of the working tree")
        s.add_argument("--budget", type=int, default=packet.DEFAULT_BUDGET, help="max packet size in chars")
        if name == "check":
            s.add_argument("--model", choices=tuple(MODELS), default="haiku")
            s.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), default="medium")
        if name == "review":
            s.add_argument("--format", choices=("md", "json", "html"), default="md")
            s.add_argument("-o", "--output")
            s.add_argument("paths", nargs="*")
    s = sub.add_parser("data")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--kind", choices=("json", "csv"))
    s.add_argument("--key", help="match list items / CSV rows by this field")
    s.add_argument("--tol", type=float, default=0.0, help="absolute tolerance for numbers")
    s = sub.add_parser("image")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--threshold", type=int, default=image.DEFAULT_THRESHOLD, help="per-channel difference that counts as change (0–255)")
    s.add_argument("--heatmap", help="write a PNG with changed pixels in red")
    s = sub.add_parser("serve", help="local web UI")
    s.add_argument("--port", type=int, default=8780)
    s.add_argument("--no-open", action="store_true", help="don't open a browser")

    a = ap.parse_args(argv)
    try:
        return COMMANDS[a.cmd](a)
    except (RuntimeError, OSError, ValueError) as e:  # git errors, missing files, bad JSON
        print(f"bigdiff: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
