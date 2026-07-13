"""The sandbox seam the headless flow adds (ADR-0011 §5, retained by ADR-0016), proven hermetically.

Replay-safety: the bypass ``_build_runtime_agent`` disables ``bash``'s checkpoint cache when a
sandbox is active — spied on the ``KitaruAgent`` constructor — so a ``decode replay`` re-executes
side-effectful shell commands; ``none`` mode stays byte-identical. (The ``_sandbox_proxy`` context
this file also used to cover is gone with the Credential Proxy itself — ADR-0016 §1.)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import decode.runtime.flow as flow_mod
from decode.tools.bash import BASH_TOOL_NAME


def _spy_kitaru_agent(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``build_agent`` with a stub + ``KitaruAgent`` with a spy; return the spy (no infra)."""
    monkeypatch.setattr(flow_mod, "build_agent", lambda flow_mode=True, model=None: object())
    spy = MagicMock()
    monkeypatch.setattr(flow_mod, "KitaruAgent", spy)
    return spy


# Replay-safety: bash cache disabled iff a sandbox is active


def test_build_runtime_agent_disables_bash_cache_in_docker_mode(monkeypatch):
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "docker")
    spy = _spy_kitaru_agent(monkeypatch)

    flow_mod._build_runtime_agent()

    # The verified kitaru-0.18 shape: keep bash's per-call checkpoint, disable its cache → replay
    # RE-EXECUTES bash. ``{"cache": False}`` is a CheckpointConfig (kept); a bare False would DROP it.
    assert spy.call_args.kwargs["tool_checkpoint_config_by_name"] == {
        BASH_TOOL_NAME: {"cache": False}
    }


def test_build_runtime_agent_disables_bash_cache_in_modal_mode(monkeypatch):
    # Any active sandbox (``!= none``) gets the re-execute-on-replay config, not just docker.
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "modal")
    spy = _spy_kitaru_agent(monkeypatch)

    flow_mod._build_runtime_agent()

    assert spy.call_args.kwargs["tool_checkpoint_config_by_name"] == {
        BASH_TOOL_NAME: {"cache": False}
    }


def test_build_runtime_agent_is_byte_identical_in_none_mode(monkeypatch):
    # AC: with sandbox_mode == none the KitaruAgent build is byte-identical to task 070 — no
    # tool_checkpoint_config_by_name kwarg at all (only name + checkpoint_strategy).
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "none")
    spy = _spy_kitaru_agent(monkeypatch)

    flow_mod._build_runtime_agent()

    assert "tool_checkpoint_config_by_name" not in spy.call_args.kwargs
    assert set(spy.call_args.kwargs) == {"name", "checkpoint_strategy"}
