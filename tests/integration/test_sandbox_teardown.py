"""Real-docker integration: the headless runner reaps the sandbox container on exit (ADR-0012 §2; ADR-0011 §4).

Drives the **real** :func:`decode.runtime.headless.run_headless_task` with a **real**
``SandboxExecutor(DockerBackend())`` (one bash call), then asserts the session container is gone and
the reap did not raise. The reap runs on a *fresh* loop, distinct from the run's own; under
ADR-0012 fresh-exec this is trivially clean — there is no loop-bound persistent shell to reap
(``docker rm -f`` is a fresh subprocess), which retires the ADR-0011 §4 leaked-container regression
the retired persistent-shell executor hit. Skips without a docker daemon.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import decode.runtime.headless as hl
from decode.agent.deps import AgentDeps
from decode.tools.registry import register_tools


def _docker_available() -> bool:
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5.0, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_available(), reason="the docker daemon is not reachable"
)


@pytest.fixture(autouse=True)
def _in_tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from a throwaway cwd so the Workspace (``.decode/sandbox``) lands under ``tmp_path``."""
    monkeypatch.chdir(tmp_path)


def _sandbox_container_ids() -> set[str]:
    """The ids of every ``ghcr.io/astral-sh/uv:python3.12-bookworm-slim`` container the daemon lists (decode's worker image)."""
    result = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim",
        ],
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    return {line for line in result.stdout.split() if line}


def _bash_echo_agent() -> Agent[AgentDeps, str | DeferredToolRequests]:
    """A real decode agent that calls bash('pwd') then echoes the tool result as its final text."""

    def model_fn(messages: list[Any], info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        if isinstance(last, ModelRequest):
            for part in last.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == "bash":
                    return ModelResponse(parts=[TextPart(content=f"bash said: {part.content}")])
        return ModelResponse(parts=[ToolCallPart(tool_name="bash", args={"command": "pwd"})])

    agent: Agent[AgentDeps, str | DeferredToolRequests] = Agent(
        FunctionModel(model_fn),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        name="decode-headless",
    )
    register_tools(agent)
    return agent


def test_the_headless_runner_reaps_the_real_container_on_exit(monkeypatch, caplog):
    """A real ``decode run`` (docker mode, one bash call) leaves NO container and does not raise on reap.

    The proof against real infra: after the run executes a bash command in a real container, the
    runner's ``finally`` reap (``close_executor`` → ``SandboxExecutor.aclose``) removes it — even
    though it runs on a *different* loop than the container was created on — and never logs the
    teardown-failed warning.
    """
    monkeypatch.setattr(hl.settings, "sandbox_mode", "docker")
    monkeypatch.setattr(hl, "_build_headless_agent", lambda model=None: _bash_echo_agent())

    before = _sandbox_container_ids()
    output = hl.run_headless_task("print the working directory")
    after = _sandbox_container_ids()

    leaked = after - before
    # Clean up defensively before asserting, so a failure never leaves a container behind.
    for cid in leaked:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)

    assert "/workspace" in output  # the command really ran inside the bind-mounted container
    assert not leaked, f"the headless runner leaked a sandbox container: {leaked}"
    # The reap did not raise: _reap_executor logs this warning only when close_executor throws.
    assert not any("headless sandbox teardown failed" in r.getMessage() for r in caplog.records), (
        "close_executor raised during the headless reap (the cross-loop bug)"
    )
