"""Unit tests for the ungated ``sleep`` control tool (``decode.tools.sleep``).

ADR-0003 §8: ``sleep`` is a one-line ``await asyncio.sleep(...)`` **capped** at
``settings.sleep_max_s`` so a model can never stall a turn forever, and it **rejects** a
non-negative-violating ``seconds`` — a negative value or ``nan`` (which would defeat the cap, since
``min(nan, …)`` is ``nan`` and ``asyncio.sleep(nan)`` never returns) — with a model-readable
:class:`pydantic_ai.ModelRetry`. It is ungated (touches no filesystem, never raises
``ApprovalRequired``).

These tests pin that contract with **no real wait**: ``asyncio.sleep`` is patched to an awaitable
no-op so we can assert the exact (capped) duration the tool would have slept, and ``sleep_max_s`` is
patched to a short value so the cap is exercised deterministically.
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


# ---------------------------------------------------------------------------
# The mode-aware ``_SLEEPER`` seam: durable (flow) mode (ADR-0008 §4, task 060)
#
# In flow mode the Headless Runtime installs the durable sleeper, which pauses on a flow-scope
# ``kitaru.wait`` instead of ``asyncio.sleep``. These tests patch ``kitaru.wait`` (the network/runtime
# boundary) so they assert the contract with no real wait and no flow: the cap and the
# negative/``nan`` rejection still fire (and fire BEFORE the wait), the wait gets the **capped**
# timeout (never the raw request), and installing then resetting the seam restores ``asyncio.sleep``.
# ---------------------------------------------------------------------------


@pytest.fixture
def _durable_seam():
    """Install the durable sleeper, then guarantee the seam is reset (no leak into later tests)."""
    sleep_module.install_durable_sleeper()
    try:
        yield
    finally:
        sleep_module.reset_sleeper()


async def test_durable_sleeper_waits_on_kitaru_with_the_capped_timeout(mocker, _durable_seam):
    # In flow mode the seam is the durable sleeper: a request above the cap is clamped, and the
    # durable ``kitaru.wait`` gets the CAPPED whole-second timeout (named "sleep"), never the raw
    # 10_000s request — so the cap holds in flow mode exactly as it does interactively.
    wait = mocker.patch("kitaru.wait")
    slept = mocker.patch("decode.tools.sleep.asyncio.sleep")
    mocker.patch("decode.tools.sleep.settings.sleep_max_s", 5.0)

    result = await sleep_module.sleep(_ctx(), seconds=10_000)

    wait.assert_called_once_with(name="sleep", timeout=5)
    slept.assert_not_awaited()  # flow mode does NOT use the in-process sleep
    assert result == "Slept 5.0 s."  # the confirmation contract is unchanged in flow mode


async def test_durable_sleeper_passes_a_sub_cap_request_through_to_the_wait(mocker, _durable_seam):
    # A request under the cap is passed through (coerced to whole seconds): kitaru.wait's timeout is
    # ``int``, so 7.0 → 7. The confirmation still reports the float the tool clamped to.
    wait = mocker.patch("kitaru.wait")
    mocker.patch("decode.tools.sleep.settings.sleep_max_s", 60.0)

    result = await sleep_module.sleep(_ctx(), seconds=7.0)

    wait.assert_called_once_with(name="sleep", timeout=7)
    assert result == "Slept 7.0 s."


async def test_durable_sleeper_rejects_negative_before_waiting(mocker, _durable_seam):
    # The negative guard fires BEFORE the seam in flow mode too: a ModelRetry is raised and the
    # durable wait is never created — the cap can never be defeated by a negative in flow mode.
    wait = mocker.patch("kitaru.wait")

    with pytest.raises(ModelRetry):
        await sleep_module.sleep(_ctx(), seconds=-1)

    wait.assert_not_called()


async def test_durable_sleeper_rejects_nan_before_waiting(mocker, _durable_seam):
    # The headline flow-mode safety case: a ``nan`` request raises ModelRetry and NEVER reaches
    # ``kitaru.wait`` (``min(nan, cap)`` is ``nan``, which would create an unbounded durable wait).
    wait = mocker.patch("kitaru.wait")
    mocker.patch("decode.tools.sleep.settings.sleep_max_s", 60.0)

    with pytest.raises(ModelRetry):
        await sleep_module.sleep(_ctx(), seconds=float("nan"))

    wait.assert_not_called()


async def test_reset_sleeper_restores_in_process_asyncio_sleep(mocker):
    # AC: after a durable run installs + resets the seam, a subsequent interactive ``sleep`` uses
    # ``asyncio.sleep`` again (no global leakage). Install, reset, then prove the in-process path runs
    # and ``kitaru.wait`` is NOT touched.
    sleep_module.install_durable_sleeper()
    sleep_module.reset_sleeper()

    wait = mocker.patch("kitaru.wait")
    slept = mocker.patch("decode.tools.sleep.asyncio.sleep")
    mocker.patch("decode.tools.sleep.settings.sleep_max_s", 60.0)

    result = await sleep_module.sleep(_ctx(), seconds=0.01)

    slept.assert_awaited_once_with(0.01)  # back to the in-process sleep
    wait.assert_not_called()  # the durable wait is gone
    assert result == "Slept 0.01 s."


async def test_install_durable_sleeper_swaps_the_active_seam():
    # The seam itself is the patch point (mirrors bash ``_EXECUTOR`` / web ``_TRANSPORT``): installing
    # points it at the durable sleeper; resetting points it back at the interactive default.
    try:
        sleep_module.install_durable_sleeper()
        assert sleep_module._SLEEPER is sleep_module._durable_sleep
    finally:
        sleep_module.reset_sleeper()
    assert sleep_module._SLEEPER is sleep_module._interactive_sleep
