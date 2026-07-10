"""decode-native value objects for the LSP Service (ADR-0007).

The client maps every LSP wire result into these frozen objects so raw LSP dicts never leak
upward. Line and column are **1-based** — the client converts from the 0-based wire basis.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Location:
    """A resolved source position — where a symbol is defined or referenced.

    ``path`` is relative to the project root when the target lives under it (matching ``grep``'s
    ``path:lineno``), absolute otherwise. ``line`` / ``column`` are **1-based**.
    """

    path: str
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One diagnostic (error / warning / info / hint) reported by the Language Server.

    ``severity`` is the raw LSP ``DiagnosticSeverity`` (``1`` = Error, ``2`` = Warning, ``3`` = Info,
    ``4`` = Hint) — the Diagnostics Enricher (task 053) keeps only ``severity == 1``. ``line`` /
    ``column`` are **1-based**; ``message`` is the human-readable text.
    """

    severity: int
    line: int
    column: int
    message: str


class Unavailable(enum.Enum):
    """Sentinel: the server could not answer at all (disabled, spawn/timeout/wire failure).

    Returned, never raised. Distinct from ``None``, which means the server answered but found
    nothing. A single-member enum is the typed-singleton sentinel pattern.
    """

    UNAVAILABLE = "unavailable"


UNAVAILABLE = Unavailable.UNAVAILABLE
"""The single :class:`Unavailable` instance — compare with ``is UNAVAILABLE``."""
