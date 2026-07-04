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
from pydantic import SecretStr

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


def _assert_seam_untouched(
    monkeypatch, *, mode: str, enabled: bool, git_token: SecretStr | None = None
) -> None:
    """Enter/exit ``_sandbox_proxy`` for the given config and assert the bash executor seam is inert."""
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", mode)
    monkeypatch.setattr(flow_mod.settings, "sandbox_credential_proxy_enabled", enabled)
    monkeypatch.setattr(flow_mod.settings, "sandbox_git_token", git_token)
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
    # Flag off AND no token → the proxy stays down (both are opt-in engagement signals).
    _assert_seam_untouched(monkeypatch, mode="docker", enabled=False, git_token=None)


def test_sandbox_proxy_is_a_noop_in_docker_mode_with_an_empty_git_token(monkeypatch):
    # Nit 4 regression: an explicit ``SANDBOX_GIT_TOKEN=`` parses to ``SecretStr("")`` (not None). It must
    # NOT engage the docker proxy nor inject empty garbage headers — the gate is on the resolved VALUE
    # (``bool(token)``), mirroring modal's ``if token:``. Only a NON-EMPTY token auto-engages the proxy.
    _assert_seam_untouched(monkeypatch, mode="docker", enabled=False, git_token=SecretStr(""))


def test_sandbox_proxy_is_a_noop_in_modal_mode_even_when_enabled(monkeypatch):
    # Docker-only: modal mode never builds the proxy even with the flag on (modal's dual proxy tokens
    # are a separate header surface — out of scope, ADR-0011).
    _assert_seam_untouched(monkeypatch, mode="modal", enabled=True)


def test_sandbox_proxy_stays_down_in_modal_mode_even_with_a_git_token(monkeypatch):
    # A SANDBOX_GIT_TOKEN engages the *docker* proxy only; modal direct-injects the token itself
    # (modal_backend), so the docker proxy body must never run in modal mode.
    _assert_seam_untouched(monkeypatch, mode="modal", enabled=False, git_token=SecretStr("ghp_x"))


def test_sandbox_proxy_engages_on_a_git_token_even_when_the_flag_is_off(monkeypatch):
    # ADR-0012 §10 (the one-knob GitHub path): a set SANDBOX_GIT_TOKEN auto-engages the docker proxy
    # with the flag OFF, and the github rules built from the token are prepended (api.github.com first)
    # ahead of DEFAULT_PROXY_RULES. Hermetic: build_credential_map records the rules then bails before
    # any container/workspace work runs.
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(flow_mod.settings, "sandbox_credential_proxy_enabled", False)
    monkeypatch.setattr(flow_mod.settings, "sandbox_git_token", SecretStr("ghp_from_setting"))

    recorded: dict[str, object] = {}

    class _Bail(Exception):
        pass

    def _record_then_bail(rules):
        recorded["rules"] = rules
        raise _Bail

    monkeypatch.setattr("decode.sandbox.proxy.build_credential_map", _record_then_bail)

    with pytest.raises(_Bail), flow_mod._sandbox_proxy():
        pass

    rules = recorded["rules"]
    assert [r.name for r in rules] == ["github-api", "github-git"]  # prepended, api host first
    assert rules[0].headers["Authorization"] == "Bearer ghp_from_setting"  # literal setting token
