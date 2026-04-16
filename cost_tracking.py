"""
Cost tracking for Claude CLI calls.

Estimates per-call cost based on prompt + response token counts and the
model used. Writes to the SQLite `budget` table via the storage layer.

Pricing as of 2026-04 (per million tokens, USD):
  haiku:  $0.25 input  / $1.25 output
  sonnet: $3.00 input  / $15.00 output
  opus:   $15.00 input / $75.00 output

Token estimation: ~4 chars per token (rough heuristic — accurate enough
for budget tracking, not for billing). Real tokenization would require
the tokenizer library; we deliberately keep this simple and
slightly conservative.

Use:
    from anchovies.cost_tracking import record_call, is_budget_exceeded

    record_call(prompt="...", response="...", model="haiku")

    if is_budget_exceeded():
        # reject new sessions until midnight
        ...
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Daily cost cap in USD. Casey's choice in the makeover Q&A: $25/day.
DAILY_BUDGET_USD = float(os.getenv("DAILY_BUDGET", "25.0"))

# Warn at this fraction of the daily budget (Casey will see a Slack alert).
BUDGET_WARN_FRACTION = float(os.getenv("BUDGET_WARN_FRACTION", "0.8"))

# Approximate chars per token (rough — varies by model and content).
CHARS_PER_TOKEN = 4

# Per-million-token prices in USD (input, output).
# Keys are normalised to lowercase substring matches.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "haiku":  (0.25, 1.25),
    "sonnet": (3.00, 15.00),
    "opus":   (15.00, 75.00),
}

# Default fallback if model name doesn't match anything (assume sonnet).
DEFAULT_PRICES = MODEL_PRICES["sonnet"]


def _estimate_tokens(text: str) -> int:
    """Rough token count from character length."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def _resolve_prices(model: Optional[str]) -> tuple[float, float]:
    """Look up (input_per_mtok, output_per_mtok) for a model name."""
    if not model:
        return DEFAULT_PRICES
    key = model.lower()
    for marker, prices in MODEL_PRICES.items():
        if marker in key:
            return prices
    return DEFAULT_PRICES


def estimate_cost(prompt: str, response: str, model: Optional[str] = None) -> float:
    """
    Estimate the USD cost of one Claude CLI call.

    Returns:
        Cost in USD as a float (e.g., 0.0123 for 1.23 cents).
    """
    in_tokens = _estimate_tokens(prompt)
    out_tokens = _estimate_tokens(response)
    in_price, out_price = _resolve_prices(model)
    return (in_tokens * in_price + out_tokens * out_price) / 1_000_000


def record_call(
    prompt: str,
    response: str,
    model: Optional[str] = None,
    member: Optional[str] = None,
) -> float:
    """
    Estimate and persist the cost of a Claude CLI call.

    Returns:
        The estimated cost in USD.
    """
    cost = estimate_cost(prompt, response, model)
    try:
        from .storage import get_storage
        storage = get_storage()
        storage.add_cost(cost, call_count=1)
        # Audit log only if the call was non-trivial (avoid log spam)
        if cost >= 0.01:
            storage.log_event(
                "claude_call",
                member=member,
                details={
                    "model": model,
                    "cost_usd": round(cost, 6),
                    "in_tokens_est": _estimate_tokens(prompt),
                    "out_tokens_est": _estimate_tokens(response),
                },
            )
    except Exception as e:
        logger.error(f"Failed to record cost: {e}")
    return cost


def get_today_spend() -> tuple[float, int]:
    """Return (total_cost_usd, call_count) for today."""
    try:
        from .storage import get_storage
        return get_storage().get_budget()
    except Exception as e:
        logger.error(f"Failed to read budget: {e}")
        return (0.0, 0)


def is_budget_exceeded() -> bool:
    """True if today's spend is at or above the daily cap."""
    spend, _ = get_today_spend()
    return spend >= DAILY_BUDGET_USD


def is_budget_warning() -> bool:
    """True if today's spend is at or above the warn threshold but not yet at cap."""
    spend, _ = get_today_spend()
    threshold = DAILY_BUDGET_USD * BUDGET_WARN_FRACTION
    return threshold <= spend < DAILY_BUDGET_USD


def remaining_budget() -> float:
    """USD remaining in today's budget (>= 0)."""
    spend, _ = get_today_spend()
    return max(0.0, DAILY_BUDGET_USD - spend)
