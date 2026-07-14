"""Receipt formatting built on the billing math."""

from __future__ import annotations

from billing import calculate_total


def format_receipt(prices: list[float], tax_rate: float = 0.0) -> str:
    """Render a one-line receipt for ``prices`` at ``tax_rate``."""
    total = calculate_total(prices, tax_rate)
    return f"Total: {total}"
