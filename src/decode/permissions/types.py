"""Narrow types for the permission gate (ADR-0003 §1-2).

Two enums drive every gate decision:

* :class:`PermissionMode` — the gate's evaluation mode, one of ``default/plan/edit/bypass``
  (Milestone 2 replaced M1's single ``ask`` mode). The mode is mutable on the gate.
* :class:`ToolKind` — a tool's permission classification (``read_only/file_edit/other``),
  declared once on its registry spec. Edit mode must tell a file edit from a shell exec, which
  the single M1 ``read_only`` bool could not express; ``read_only`` is now *derived* from the
  kind (``kind is READ_ONLY``).

Kept in ``permissions/types.py`` (not ``entities/``) because mode + kind are internal policy
vocabulary, not value objects crossing the loop/TUI boundary — the cross-boundary models
(``PermissionRequest`` / ``PermissionDecision``) live in :mod:`decode.entities.permissions`.
"""

from __future__ import annotations

import enum


class PermissionMode(enum.Enum):
    """How the gate decides on a tool call (ADR-0003 §1).

    Semantics by tool kind: ``DEFAULT`` auto-allows read-only tools and asks for mutating ones;
    ``PLAN`` auto-allows read-only and denies any mutation (with a reason pointing at
    ``exit_plan_mode``); ``EDIT`` additionally auto-allows file edits but still asks for other
    mutations (``bash``); ``BYPASS`` allows everything with no prompt. The gate default is
    ``DEFAULT``.
    """

    DEFAULT = "default"
    PLAN = "plan"
    EDIT = "edit"
    BYPASS = "bypass"


class ToolKind(enum.Enum):
    """A tool's permission classification (ADR-0003 §2).

    ``READ_ONLY`` tools have no disk/exec side effect (the file readers, ``web_fetch``, and the
    in-memory ``todo_write`` checklist); ``FILE_EDIT`` tools mutate the file tree (``write`` /
    ``edit``); ``OTHER`` is everything else (shell exec via ``bash``, and the default for an
    unclassified tool). The gate reads this so edit mode can auto-allow a file edit while still
    asking for a shell exec.
    """

    READ_ONLY = "read_only"
    FILE_EDIT = "file_edit"
    OTHER = "other"
