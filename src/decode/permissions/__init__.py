"""Permissions: the ask/allow/deny policy around every tool call (ADR-0002 §3).

The gate (:mod:`decode.permissions.gate`) turns a ``PermissionRequest`` into a
``PermissionDecision``; it does not own the terminal UI — resolving an ``ask`` is the TUI
resolver's job. Shared value objects live in :mod:`decode.entities.permissions`.
"""
