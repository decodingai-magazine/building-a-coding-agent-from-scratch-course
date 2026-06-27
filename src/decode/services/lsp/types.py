"""decode-native value objects for the LSP Service (ADR-0007).

The client maps every LSP wire result into one of these small, frozen objects so **raw LSP dicts
never leak upward** into the ``lsp`` tool (task 052) or the Diagnostics Enricher (task 053). Line and
column are **1-based** here — the client converts from LSP's 0-based wire basis at its boundary so the
rest of decode (``read``'s ``cat -n``, ``grep``'s ``path:lineno``) stays uniformly 1-based.
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
    """Sentinel signalling the Code Intelligence answer could not be produced (best-effort).

    Returned (never raised) when the Language Server is disabled, could not be spawned, timed out,
    closed its pipe, or sent a malformed frame. It is **distinct from ``None``** — ``None`` means the
    server answered but found nothing (no definition / no hover), whereas :data:`UNAVAILABLE` means
    "no answer at all", which the ``lsp`` tool maps to a ``ModelRetry`` telling the model to fall back
    to ``read`` / ``grep`` (ADR-0007). A single-member enum is the typed-singleton sentinel pattern.
    """

    UNAVAILABLE = "unavailable"


UNAVAILABLE = Unavailable.UNAVAILABLE
"""The single :class:`Unavailable` instance — compare with ``is UNAVAILABLE``."""
