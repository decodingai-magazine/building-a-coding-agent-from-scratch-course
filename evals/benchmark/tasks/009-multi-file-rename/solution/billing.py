"""Billing math."""

from __future__ import annotations


def calculate_total(prices: list[float], tax_rate: float = 0.0) -> float:
    """Return the sum of ``prices`` with ``tax_rate`` applied, rounded to 2 decimals."""
    subtotal = sum(prices)
    return round(subtotal * (1 + tax_rate), 2)
