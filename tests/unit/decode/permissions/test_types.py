"""Unit tests for :mod:`decode.permissions.types`.

ADR-0003 §1-2: M1's single ``ASK`` mode is replaced by the four real
:class:`~decode.permissions.types.PermissionMode` values (``default/plan/edit/bypass``), and a
:class:`~decode.permissions.types.ToolKind` classification (``read_only/file_edit/other``)
replaces the single ``read_only`` bool. These tests pin exactly those vocabularies so a future
rename is a deliberate, visible change rather than an accident.
"""

from decode.permissions.types import PermissionMode, ToolKind


def test_permission_mode_is_the_four_milestone2_modes():
    # ADR-0003 §1: the single M1 ``ask`` mode is gone; the four real modes replace it.
    assert {m.value for m in PermissionMode} == {"default", "plan", "edit", "bypass"}


def test_permission_mode_values():
    assert PermissionMode.DEFAULT.value == "default"
    assert PermissionMode.PLAN.value == "plan"
    assert PermissionMode.EDIT.value == "edit"
    assert PermissionMode.BYPASS.value == "bypass"


def test_permission_mode_has_no_ask_value():
    # The old ASK *mode* value is removed (the ASK *outcome* lives on PermissionOutcome).
    assert not hasattr(PermissionMode, "ASK")


def test_tool_kind_is_the_three_classifications():
    # ADR-0003 §2: a three-way classification replaces the single read_only bool.
    assert {k.value for k in ToolKind} == {"read_only", "file_edit", "other"}


def test_tool_kind_values():
    assert ToolKind.READ_ONLY.value == "read_only"
    assert ToolKind.FILE_EDIT.value == "file_edit"
    assert ToolKind.OTHER.value == "other"
