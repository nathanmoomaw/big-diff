"""Review models: one record per name, no dependencies (the CLI and MCP server read it without the
`review` extra). Prices are Anthropic first-party API rates, USD per 1M tokens."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    id: str
    price_in: float
    price_out: float
    max_input: int  # refuse bigger prompts (Haiku: stay under its price cliff)
    server_fallback: bool  # supports `fallbacks: "default"` on a safety refusal
    cliff_tokens: int | None = None  # input size above which both prices multiply…
    cliff_mult: float = 1.0  # …by this


MODELS = {
    "haiku": Model("claude-haiku-5-5", 0.10, 0.50, 95_000, False, cliff_tokens=100_000, cliff_mult=5),
    "sonnet": Model("claude-sonnet-5-5", 2.00, 10.00, 400_000, True),
    "opus": Model("claude-opus-5-5", 4.00, 20.00, 400_000, True),
}
BY_ID = {m.id: m for m in MODELS.values()}


def cost(model_id: str, tokens_in: int, tokens_out: int) -> float | None:
    """USD for one call, or None for a model not in the table (e.g. one a fallback answered with)."""
    m = BY_ID.get(model_id)
    if m is None:
        return None
    mult = m.cliff_mult if m.cliff_tokens and tokens_in > m.cliff_tokens else 1.0
    return (tokens_in * m.price_in + tokens_out * m.price_out) * mult / 1e6
