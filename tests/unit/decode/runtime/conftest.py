"""Fixtures for the Headless Runtime tests (ADR-0008).

The round-trip tests run the **real** Kitaru ``@flow`` + ``KitaruAgent`` on the **local** stack —
no Kitaru server, no network — by swapping only the model boundary (a ``FunctionModel`` agent
injected through the ``_build_runtime_agent`` seam) and isolating Kitaru/ZenML's on-disk store under
a per-test ``tmp_path``. This mirrors how the LSP feature patches its service seam (ADR-0007) and
keeps the suite hermetic under ``filterwarnings=["error"]``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

from decode.agent.deps import AgentDeps
from decode.tools.registry import register_tools


@pytest.fixture(autouse=True)
def isolated_kitaru_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect Kitaru/ZenML's store + config under ``tmp_path`` so flows run offline, hermetically.

    Kitaru's local stack persists checkpoints/metadata through ZenML, which by default writes under
    the user's home. We redirect ``Path.home`` / ``click.get_app_dir`` / ``ZENML_CONFIG_PATH`` to
    ``tmp_path``, disable analytics, and reset the ZenML global-config + client singletons before
    and after so no test ever touches real user state or makes a network call. ``cwd`` is moved into
    ``tmp_path`` too, so any tool that writes a file stays inside the sandbox.
    """
    from zenml.client import Client
    from zenml.config.global_config import GlobalConfiguration

    config_dir = tmp_path / "kitaru-config"
    config_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("click.get_app_dir", lambda app_name: str(config_dir))
    monkeypatch.setenv("ZENML_CONFIG_PATH", str(config_dir))
    monkeypatch.setenv("ZENML_ANALYTICS_OPT_IN", "false")
    monkeypatch.chdir(tmp_path)

    GlobalConfiguration._reset_instance()
    Client._reset_instance()
    try:
        yield tmp_path
    finally:
        Client._reset_instance()
        GlobalConfiguration._reset_instance()


# A model leg: a callable producing the next ``ModelResponse`` from the message history.
ModelLeg = Callable[[list[ModelResponse], AgentInfo], ModelResponse]


def make_scripted_agent(
    responses: Sequence[ModelResponse],
    *,
    name: str = "decode-runtime",
) -> tuple[Agent[AgentDeps, str | DeferredToolRequests], dict[str, int]]:
    """Build a real decode agent (all tools registered) on a scripted ``FunctionModel``.

    ``responses`` is replayed one per model leg (the last one repeats if the agent asks for more),
    so a test scripts e.g. ``[<call write>, <final text>]``. Returns the agent plus a mutable
    ``{"legs": n}`` counter the caller can assert against (e.g. to prove a replay served the turn
    from cache without a fresh model leg). The agent carries ``deps_type=AgentDeps`` and
    ``output_type=[str, DeferredToolRequests]`` exactly like ``build_agent()``.
    """
    counter = {"legs": 0}

    def model_fn(messages: list[ModelResponse], info: AgentInfo) -> ModelResponse:
        index = min(counter["legs"], len(responses) - 1)
        counter["legs"] += 1
        return responses[index]

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(model_fn),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        name=name,
    )
    register_tools(agent)
    return agent, counter
