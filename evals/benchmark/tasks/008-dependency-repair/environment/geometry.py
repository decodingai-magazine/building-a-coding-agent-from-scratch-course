"""Geometry helpers."""

from __future__ import annotations

import math


def circle_area(radius: float) -> float:
    """Return the area of a circle with the given radius."""
    return math.pi * radius * radius
