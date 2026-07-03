"""Real-docker integration: the headless flow reaps the sandbox container on exit (ADR-0011 §4, task 074).

The regression the round-1 unit spy missed: it used a loop-agnostic ``AsyncMock``, so it passed while
the REAL headless teardown leaked the container (``DockerExecutor``'s shell subprocess is bound to
``run_sync``'s now-closed per-call loop, so ``aclose`` awaiting it from the flow's fresh reap loop raised
``Event loop is closed`` before ``docker rm -f`` could run). This drives the **real** bypass ``@flow`` +
``KitaruAgent`` on the local Kitaru stack with a **real** ``DockerExecutor`` (one bash call), then
asserts the session container is gone and the reap did not raise. Skips without a docker daemon.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import decode.runtime.flow as flow_mod
from decode.agent.deps import AgentDeps
from decode.runtime import run_agent_task
from decode.tools.registry import register_tools


def _docker_available() -> bool:
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5.0, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


pytestmark = [
    pytest.mark.skipif(not _docker_available(), reason="the docker daemon is not reachable"),
    # The real flow boots the Kitaru/ZenML stack; scope its two known third-party deprecations (see
    # test_flow.py) so the strict ``filterwarnings=["error"]`` gate stays green here too.
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]


@pytest.fixture(autouse=True)
def isolated_kitaru_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect Kitaru/ZenML's store + config under ``tmp_path`` so the flow runs offline, hermetically."""
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
        store = GlobalConfiguration()._zen_store
        engine = getattr(store, "_engine", None)
        if engine is not None:
            engine.dispose()
        Client._reset_instance()
        GlobalConfiguration._reset_instance()
        _close_lingering_event_loops()
        gc.collect()


def _close_lingering_event_loops() -> None:
    """Close any event loop the durable flow left open, so its leak never trips a *later* test.

    A docker-mode headless run spawns the ``docker exec`` shell on a per-call event loop; the
    ThreadedChildWatcher's ``call_soon_threadsafe`` leaves a pending ``Handle`` on that loop, which keeps
    it alive **unclosed** past the flow (the shell is reaped loop-free, so the loop's own subprocess
    cleanup never runs). Harmless in production — a real ``decode run`` exits right after the flow — but
    pytest keeps the process alive, so that unclosed loop is later GC'd inside an unrelated test and
    raises a ``PytestUnraisableExceptionWarning`` (``unclosed event loop`` + its self-pipe sockets) that
    the strict ``filterwarnings=error`` gate turns into a failure. Closing it here (guarded on
    ``not is_running`` so no live loop is touched) keeps this test self-contained and order-independent.
    """
    for obj in gc.get_objects():
        if (
            isinstance(obj, asyncio.AbstractEventLoop)
            and not obj.is_closed()
            and not obj.is_running()
        ):
            with contextlib.suppress(Exception):
                obj.close()


def _sandbox_container_ids() -> set[str]:
    """The ids of every ``python:3.12-slim`` container the daemon lists (decode's worker image)."""
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "ancestor=python:3.12-slim"],
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
        name="decode-runtime",
    )
    register_tools(agent)
    return agent


def test_headless_bypass_flow_reaps_the_real_container_on_exit(monkeypatch, caplog):
    """A real ``decode run`` (docker mode, one bash call) leaves NO container and does not raise on reap.

    The proof for AC5 against real infra: after the bypass flow runs a bash command in a real container,
    the flow's ``finally`` reap (``close_executor`` → ``DockerExecutor.aclose``) removes the container —
    even though it runs on a *different* loop than the shell was created on — and never logs the
    teardown-failed warning (``Event loop is closed`` no longer escapes).
    """
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "docker")
    # Patch the factory so the REAL _build_runtime_agent wraps our agent under the real "calls" strategy
    # (the per-call event-loop lifecycle that exposed the cross-loop teardown bug).
    monkeypatch.setattr(
        flow_mod, "build_agent", lambda flow_mode=True, model=None: _bash_echo_agent()
    )

    before = _sandbox_container_ids()
    handle = run_agent_task.run(task="print the working directory")
    after = _sandbox_container_ids()

    leaked = after - before
    # Clean up defensively before asserting, so a failure never leaves a container behind.
    for cid in leaked:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)

    output = flow_mod._load_runtime_output(handle.exec_id)
    assert "/workspace" in output  # the command really ran inside the bind-mounted container
    assert not leaked, f"the headless flow leaked a sandbox container: {leaked}"
    # The reap did not raise: _reap_runtime_executor logs this warning only when close_executor throws.
    assert not any("headless sandbox teardown failed" in r.getMessage() for r in caplog.records), (
        "close_executor raised during the headless reap (the cross-loop bug)"
    )
