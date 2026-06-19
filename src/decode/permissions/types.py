"""Narrow types for the permission gate (ADR-0002 §3).

:class:`PermissionMode` is the **seam** M3 grows into ``default/plan/edit/bypass``. v1 ships
only ``ASK`` (ask on every tool call), so the gate has exactly one mode to evaluate under;
adding the M3 modes is then a deliberate extension of this enum rather than a rewrite.

Kept in ``permissions/types.py`` (not ``entities/``) because the mode is internal policy
vocabulary, not a value object crossing the loop/TUI boundary — the cross-boundary models
(``PermissionRequest`` / ``PermissionDecision``) live in :mod:`decode.entities.permissions`.
"""

from __future__ import annotations

import enum


class PermissionMode(enum.Enum):
    """How the gate decides on a tool call (ADR-0002 §3).

    v1 is ``ASK`` only: every tool call is routed to the human. M3 adds ``default`` (read-only
    auto-allow), ``plan`` (read-only, no mutations), ``edit`` (file edits auto-allowed), and
    ``bypass`` (no gate). Those are deliberately *not* added yet — one concept per step.
    """

    ASK = "ask"
