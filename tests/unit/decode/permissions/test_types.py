"""Unit tests for :mod:`decode.permissions.types`.

ADR-0002 §3: ``PermissionMode`` is the seam for M3's ``default/plan/edit/bypass`` modes.
v1 only ships ``ASK`` (ask on every tool call); the test pins exactly that so adding the
M3 modes is a deliberate, visible change rather than an accident.
"""

from decode.permissions.types import PermissionMode


def test_v1_ships_only_ask_mode():
    # M3 adds default/plan/edit/bypass; v1 is intentionally ASK-only (ADR-0002 §3).
    assert {m.value for m in PermissionMode} == {"ask"}


def test_ask_mode_value():
    assert PermissionMode.ASK.value == "ask"
