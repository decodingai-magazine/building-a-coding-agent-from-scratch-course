"""Narrow types for the permission gate (ADR-0003 §1-2).

:class:`PermissionMode` (``default/plan/edit/bypass``) and :class:`ToolKind`
(``read_only/file_edit/other``) drive every gate decision. Kept here (not ``entities/``)
because mode + kind are internal policy vocabulary; the cross-boundary models live in
:mod:`decode.entities.permissions`.
"""

from __future__ import annotations

import enum


class PermissionMode(enum.Enum):
    """How the gate decides on a tool call (ADR-0003 §1).

    ``DEFAULT`` auto-allows read-only, asks for mutations; ``PLAN`` denies any mutation;
    ``EDIT`` additionally auto-allows file edits; ``BYPASS`` allows everything.
    """

    DEFAULT = "default"
    PLAN = "plan"
    EDIT = "edit"
    BYPASS = "bypass"


class ToolKind(enum.Enum):
    """A tool's permission classification (ADR-0003 §2).

    ``READ_ONLY`` = no disk/exec side effect; ``FILE_EDIT`` = mutates the file tree (``write`` /
    ``edit``); ``OTHER`` = everything else (``bash``, and the default for an unclassified tool).
    """

    READ_ONLY = "read_only"
    FILE_EDIT = "file_edit"
    OTHER = "other"
