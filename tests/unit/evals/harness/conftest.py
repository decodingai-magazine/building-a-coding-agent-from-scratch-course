"""Offline fixtures for the eval harness tests — scripted model + fake sandbox seam (ADR-0017 §3,4,5).

The scripted-model builders live in ``tests/support/eval_models.py`` and the in-memory sandbox fake in
``tests/support/fake_sandbox.py`` (both regular importable modules); this conftest wires them into the
seams: ``install_model`` injects a scripted model as the agent's base model (so ``build_agent()`` builds
a real decode agent whose only fake part is the model) and ``install_fake`` patches
``decode.sandbox.select_executor`` to hand back a :class:`~support.fake_sandbox.FakeExecutor`. The
autouse ``_reset_seam`` leaves the process-global ``bash`` executor memo clean between tests. No
network, no keys.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic_ai.models.function import FunctionModel
from support.fake_sandbox import FakeExecutor

from decode.tools.bash import reset_executor


@pytest.fixture(autouse=True)
def _reset_seam():
    """Leave the ``decode.tools.bash`` executor seam clean after each test (the memo is process-global)."""
    yield
    reset_executor()


@pytest.fixture
def install_model(mocker) -> Callable[[FunctionModel], None]:
    """Return a helper that installs ``model`` as the agent's base model for the whole run.

    Patches the provider seam so ``build_agent()`` builds a real decode agent on the scripted
    model — no ``GEMINI_API_KEY`` is touched because ``_build_model`` never runs.
    """

    def _install(model: FunctionModel) -> None:
        mocker.patch("decode.agent.factory._build_model", return_value=model)

    return _install


@pytest.fixture
def install_fake(monkeypatch) -> Callable[[FakeExecutor], dict]:
    """Return a helper that patches the sandbox seam to build a given :class:`FakeExecutor`.

    The helper returns a dict the test can read ``["mode"]`` from — the ``--sandbox`` mode
    ``select_executor`` was asked for — proving the docker / modal rung selection.
    """

    def _install(fake: FakeExecutor) -> dict:
        captured: dict = {}

        def fake_select(mode: str) -> FakeExecutor:
            captured["mode"] = mode
            return fake

        monkeypatch.setattr("decode.sandbox.select_executor", fake_select)
        return captured

    return _install
