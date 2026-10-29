"""Model review of changed files, with a big diff packet as context. Needs the `review` extra and an
Anthropic key (ANTHROPIC_API_KEY or `ant auth login`).

One call per review: the files whole (line-numbered), the packet as context, structured findings
back. Haiku is the cheap default; Sonnet is the stronger final pass (bench round 3: Sonnet + packet
12/18 real bugs incl. the cross-file one 3/3, research/pr-gate-replay.md). Models and prices: models.py.
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

from .models import MODELS, cost

COUNT_ABOVE_CHARS = 250_000  # under this a prompt is < ~83K tokens even at 3 chars/token: no need to count
MAX_REVIEW_BYTES = 150_000  # bigger files are almost always generated
CONTEXT_HEAD = (
    "Review packet (changed symbols ranked by risk, their uses elsewhere, other code sharing their events):\n\n"
)
SYSTEM = """You review code changes. Report only real defects you are confident about: wrong results, crashes,
unhandled edge cases, security problems, broken or vacuous tests, spec violations. Use the review packet: it lists
what changed, where each changed symbol is used, and other code that reacts to the same events — bugs often live
in those interactions. No style, naming or docs nits. At most 10 findings, most severe first. Empty list if none."""


class Finding(BaseModel):
    file: str
    line: int
    severity: Literal["high", "medium", "low"]
    summary: str


class Findings(BaseModel):
    findings: list[Finding]


@dataclass
class Result:
    findings: list[Finding]
    model: str  # the model that answered (a fallback can differ from the one asked)
    tokens_in: int
    tokens_out: int
    cost: float | None  # None: the answering model isn't priced in models.py
    seconds: float

    def report(self) -> str:
        lines = [f"{f.file}:{f.line} [{f.severity}] {f.summary}" for f in self.findings]
        price = f"${self.cost:.4f}" if self.cost is not None else "cost unknown"
        lines.append(
            f"review: {self.model}, {len(self.findings)} finding(s), {self.tokens_in:,} in / {self.tokens_out:,} out, "
            f"{price}, {self.seconds:.1f} s"
        )
        return "\n".join(lines)


@functools.cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def prompt(files: dict[str, str], context: str) -> str:
    """files: display path → text."""
    parts = [f"Context:\n{context}\n"] if context else []
    for path, text in files.items():
        numbered = "\n".join(f"{i:4d}  {line}" for i, line in enumerate(text.splitlines(), 1))
        parts.append(f"=== {path} ===\n{numbered}")
    return "\n\n".join(parts)


def review(files: dict[str, str], context: str = "", model: str = "haiku", effort: str = "medium") -> Result:
    """Review `files` (display path → text) with `context`; raise RuntimeError on any failure."""
    m = MODELS[model]
    text = prompt(files, context)
    messages = [{"role": "user", "content": text}]
    kwargs = {
        "model": m.id,
        "max_tokens": 16000,
        "system": SYSTEM,
        "messages": messages,
        "output_config": {"effort": effort},
    }
    t0 = time.perf_counter()
    try:
        client = _client()
        if len(text) > COUNT_ABOVE_CHARS:  # below this it can't reach the cap: skip the round trip
            n = client.messages.count_tokens(model=m.id, system=SYSTEM, messages=messages).input_tokens
            if n > m.max_input:
                raise RuntimeError(f"{n:,} input tokens is over {m.max_input:,} for {model}: review fewer files")
        if m.server_fallback:  # route around a safety-classifier refusal instead of failing the review
            r = client.beta.messages.parse(
                **kwargs, output_format=Findings, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        else:
            r = client.messages.parse(**kwargs, output_format=Findings)
    except anthropic.APIError as e:
        raise RuntimeError(f"API error: {e}") from e
    except TypeError as e:
        if "auth" not in str(e).lower():  # the SDK reports missing credentials as a TypeError at request time
            raise
        raise RuntimeError(f"no Anthropic credentials: set ANTHROPIC_API_KEY or run `ant auth login` ({e})") from e
    if r.stop_reason != "end_turn" or r.parsed_output is None:
        raise RuntimeError(f"review stopped early: {r.stop_reason}")
    u = r.usage
    answered = getattr(r, "model", None) or m.id
    seconds = time.perf_counter() - t0
    return Result(
        r.parsed_output.findings,
        answered,
        u.input_tokens,
        u.output_tokens,
        cost(answered, u.input_tokens, u.output_tokens),
        seconds,
    )


def check(
    repo: str | Path,
    base: str = "HEAD",
    head: str | None = None,
    model: str = "haiku",
    budget: int | None = None,
    effort: str = "medium",
) -> Result | None:
    """Review the changed source/config files (as they are at head, or on disk) with their big diff packet.
    None when nothing reviewable changed."""
    from . import packet

    p = packet.build(repo, base, head, budget or packet.DEFAULT_BUDGET)
    files = {path: text for path, text in p.reviewable().items() if len(text) < MAX_REVIEW_BYTES}
    return review(files, CONTEXT_HEAD + p.markdown(), model, effort) if files else None
