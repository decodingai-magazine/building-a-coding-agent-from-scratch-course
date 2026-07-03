"""The sandbox seams the headless flow adds (ADR-0011 §5-6), proven hermetically — no docker.

Two things this task wires into ``runtime/flow.py``:

* **Replay-safety (ADR-0011 §5):** the bypass ``_build_runtime_agent`` disables ``bash``'s checkpoint
  cache when a sandbox is active, so a ``decode replay`` re-executes side-effectful shell commands
  instead of serving a stale cached turn. Proven by spying on the ``KitaruAgent`` constructor (no real
  infra), and asserting ``none`` mode is byte-identical to task 070.
* **The Credential Proxy context (ADR-0011 §6):** ``_sandbox_proxy()`` is a **no-op** unless
  ``sandbox_mode == "docker"`` and ``sandbox_credential_proxy_enabled`` — proven by asserting it leaves
  the ``bash`` executor seam untouched for ``none`` / ``modal`` / proxy-disabled configs, so those
  flows stay byte-unchanged. The engaged path (a live mitmproxy container) is the ``@skipif``-guarded
  integration test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import decode.runtime.flow as flow_mod
import decode.tools.bash as bash_mod
from decode.tools.bash import BASH_TOOL_NAME
from decode.tools.exec import LocalExecutor


def _spy_kitaru_agent(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``build_agent`` with a stub + ``KitaruAgent`` with a spy; return the spy (no infra)."""
    monkeypatch.setattr(flow_mod, "build_agent", lambda flow_mode=True, model=None: object())
    spy = MagicMock()
    monkeypatch.setattr(flow_mod, "KitaruAgent", spy)
    return spy


# --- Replay-safety: bash cache disabled iff a sandbox is active -------------------------------


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


# --- _sandbox_proxy(): a no-op unless docker + proxy enabled ----------------------------------


def _assert_seam_untouched(monkeypatch, *, mode: str, enabled: bool) -> None:
    """Enter/exit ``_sandbox_proxy`` for the given config and assert the bash executor seam is inert."""
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", mode)
    monkeypatch.setattr(flow_mod.settings, "sandbox_credential_proxy_enabled", enabled)
    # A sentinel executor + un-selected memo; a no-op must leave both exactly as they were.
    sentinel = LocalExecutor()
    monkeypatch.setattr(bash_mod, "_EXECUTOR", sentinel)
    monkeypatch.setattr(bash_mod, "_executor_selected", False)

    with flow_mod._sandbox_proxy():
        # Inside the context the seam is still the sentinel — no proxy-wired executor was installed.
        assert bash_mod._EXECUTOR is sentinel
        assert bash_mod._executor_selected is False

    assert bash_mod._EXECUTOR is sentinel  # and it stays untouched after exit
    assert bash_mod._executor_selected is False


def test_sandbox_proxy_is_a_noop_in_none_mode(monkeypatch):
    _assert_seam_untouched(monkeypatch, mode="none", enabled=True)


def test_sandbox_proxy_is_a_noop_in_docker_mode_when_proxy_disabled(monkeypatch):
    _assert_seam_untouched(monkeypatch, mode="docker", enabled=False)


def test_sandbox_proxy_is_a_noop_in_modal_mode_even_when_enabled(monkeypatch):
    # Docker-only: modal mode never builds the proxy even with the flag on (modal's dual proxy tokens
    # are a separate header surface — out of scope, ADR-0011).
    _assert_seam_untouched(monkeypatch, mode="modal", enabled=True)
