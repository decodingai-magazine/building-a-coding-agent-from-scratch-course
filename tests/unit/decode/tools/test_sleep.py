"""Unit tests for the ungated ``sleep`` control tool (``decode.tools.sleep``) — ADR-0003 §8.

Covers the ``sleep_max_s`` cap and the negative/``nan`` rejection (``min(nan, cap)`` is ``nan``,
which would defeat the cap). The durable ``_SLEEPER`` seam is gone — ``sleep`` awaits
:func:`asyncio.sleep` in every mode (ADR-0019 §1). No real wait: ``asyncio.sleep`` is patched so
the exact capped duration is asserted deterministically.
"""

import math
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.tools import sleep as sleep_module


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


async def _no_user_resolver(question: str) -> str:  # pragma: no cover - sleep never asks
    raise AssertionError("sleep must not consult the user")


def _ctx() -> RunContext[AgentDeps]:
    """A minimal RunContext; ``sleep`` ignores ``ctx`` but the signature is context-aware."""
    deps = AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,  # type: ignore[arg-type]
        gate=PermissionGate(),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=_no_user_resolver,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=False)  # type: ignore[arg-type]


async def test_sleep_returns_a_confirmation_for_a_short_wait(mocker):
    slept = mocker.patch("decode.tools.sleep.asyncio.sleep")
    mocker.patch("decode.tools.sleep.settings.sleep_max_s", 60.0)

    result = await sleep_module.sleep(_ctx(), seconds=0.01)

    slept.assert_awaited_once_with(0.01)
    assert "0.01" in result
    assert result.lower().startswith("slept")


async def test_sleep_caps_at_settings_sleep_max_s(mocker):
    # A request far above the cap is clamped to the cap (not rejected); patch a short cap so the
    # assertion is fast and deterministic.
    slept = mocker.patch("decode.tools.sleep.asyncio.sleep")
    mocker.patch("decode.tools.sleep.settings.sleep_max_s", 0.05)

    result = await sleep_module.sleep(_ctx(), seconds=10_000)

    slept.assert_awaited_once_with(0.05)
    assert "0.05" in result, "the confirmation reports the duration actually slept (the cap)"


async def test_sleep_rejects_negative_seconds_with_model_retry(mocker):
    slept = mocker.patch("decode.tools.sleep.asyncio.sleep")

    with pytest.raises(ModelRetry):
        await sleep_module.sleep(_ctx(), seconds=-1)

    # A rejected sleep must not wait at all.
    slept.assert_not_awaited()


async def test_sleep_rejects_nan_seconds_without_hanging(mocker):
    # pydantic_core accepts the JSON ``NaN`` token and validates it as a float, so a model tool call
    # ``{"seconds": NaN}`` reaches ``sleep``. ``min(nan, cap)`` is ``nan`` and ``asyncio.sleep(nan)``
    # never returns — that would stall the turn past any abort boundary, defeating the cap. So nan is
    # rejected like a negative (the guard is ``not (seconds >= 0)``, and ``nan >= 0`` is False).
    slept = mocker.patch("decode.tools.sleep.asyncio.sleep")
    mocker.patch("decode.tools.sleep.settings.sleep_max_s", 60.0)

    with pytest.raises(ModelRetry):
        await sleep_module.sleep(_ctx(), seconds=float("nan"))

    # The cap is never reached and nothing sleeps — no hang.
    slept.assert_not_awaited()


async def test_sleep_caps_infinity_at_settings_sleep_max_s(mocker):
    # ``inf`` is harmless: unlike nan it is ``>= 0``, so it falls through the guard and is clamped by
    # ``min(inf, cap) == cap``. Lock that in so the nan fix never accidentally rejects inf too.
    slept = mocker.patch("decode.tools.sleep.asyncio.sleep")
    mocker.patch("decode.tools.sleep.settings.sleep_max_s", 60.0)

    result = await sleep_module.sleep(_ctx(), seconds=math.inf)

    slept.assert_awaited_once_with(60.0)
    assert "60.0" in result


async def test_sleep_at_exactly_zero_is_allowed(mocker):
    # 0 is the boundary: not negative, so it is allowed (and clamped to 0 under any cap).
    slept = mocker.patch("decode.tools.sleep.asyncio.sleep")
    mocker.patch("decode.tools.sleep.settings.sleep_max_s", 60.0)

    result = await sleep_module.sleep(_ctx(), seconds=0)

    slept.assert_awaited_once_with(0)
    assert "Slept" in result


async def test_sleep_name_constant_is_sleep():
    assert sleep_module.SLEEP_TOOL_NAME == "sleep"


def test_the_durable_sleeper_seam_is_gone():
    """ADR-0019 §1 (clean break): the flow-scope ``kitaru.wait`` seam is deleted, not shimmed.

    ``sleep`` awaits :func:`asyncio.sleep` in every mode now, so nothing may install a second
    sleeper — the tests above patch ``asyncio.sleep`` and that is the whole story.
    """
    for retired in ("_SLEEPER", "_durable_sleep", "install_durable_sleeper", "reset_sleeper"):
        assert not hasattr(sleep_module, retired), f"{retired} must not come back"
