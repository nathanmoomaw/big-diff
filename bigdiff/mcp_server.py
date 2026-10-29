"""MCP server: big diff's engines as tools for MCP clients such as Claude Code.

claude mcp add bigdiff -- uv run --project /path/to/big-diff --extra mcp python -m bigdiff.mcp_server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import data, image, packet
from .models import MODELS

mcp = FastMCP("bigdiff")


@mcp.tool()
def review_packet(
    repo: str, base: str = "HEAD", head: str | None = None, budget: int = packet.DEFAULT_BUDGET, format: str = "md"
) -> str:
    """Changes from base to head (None = working tree) by symbol, ranked by risk, each with its uses
    elsewhere in the repo, fitted to `budget` chars. Use it instead of `git diff` before reviewing."""
    return packet.build(repo, base, head, budget).render(format)


@mcp.tool()
def review_changes(repo: str, base: str = "HEAD", head: str | None = None, model: str = "haiku") -> str:
    """One model review (haiku, sonnet or opus; prices in bigdiff/models.py) of the changed files from base to
    head, with the review packet as context. Bench: sonnet caught the most real bugs, haiku is the cheap pass.
    Needs the `review` extra and an Anthropic key."""
    if model not in MODELS:
        raise ValueError(f"model must be one of {', '.join(MODELS)} (got {model!r})")
    try:
        from .review import check
    except ImportError as e:
        raise RuntimeError("review_changes needs big diff's review extra: run the server with --extra review") from e
    r = check(repo, base, head, model)
    return r.report() if r else "no changed source files to review"


@mcp.tool()
def data_diff(a: str, b: str, key: str | None = None, tol: float = 0.0) -> str:
    """Value-level diff of two JSON or CSV files (paths). `key` matches list items / rows by that field."""
    ops = data.diff_paths(a, b, key=key, tol=tol)
    return "\n".join(o.render() for o in ops) or "no differences"


@mcp.tool()
def image_diff(a: str, b: str, threshold: int = image.DEFAULT_THRESHOLD, heatmap: str | None = None) -> str:
    """Pixel diff of two images (paths): changed fraction, bounding box, optional red heatmap PNG."""
    return image.diff(a, b, threshold, heatmap).render()


if __name__ == "__main__":
    mcp.run()
