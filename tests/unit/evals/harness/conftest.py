"""Offline fixtures for the eval-driver tests (ADR-0017 §4).

The scripted-model builders live in ``tests/support/eval_models.py`` (a regular importable module);
this conftest holds only the ``install_model`` fixture that injects one of them as the agent's base
model, so ``build_agent()`` — which the driver calls — constructs a real decode agent (all tools,
real instructions hook) whose only fake part is the model. No network, no keys.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic_ai.models.function import FunctionModel


@pytest.fixture
def install_model(mocker) -> Callable[[FunctionModel], None]:
    """Return a helper that installs ``model`` as the agent's base model for the whole run.

    Patches the provider seam so ``build_agent()`` builds a real decode agent on the scripted
    model — no ``GEMINI_API_KEY`` is touched because ``_build_model`` never runs.
    """

    def _install(model: FunctionModel) -> None:
        mocker.patch("decode.agent.factory._build_model", return_value=model)

    return _install
