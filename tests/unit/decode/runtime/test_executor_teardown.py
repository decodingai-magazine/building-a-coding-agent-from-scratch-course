"""The headless flows reap the sandbox executor on exit — bypass + HITL, incl. the error path (ADR-0011 §4).

These drive the **real** Kitaru ``@flow`` on the local stack (the same harness as ``test_flow.py`` /
``test_hitl.py``), inject a fake executor with an ``aclose`` spy at the ``bash`` seam, and assert the
flow's ``finally`` reaps it — on normal completion **and** when the flow body raises. The reap runs
:func:`decode.tools.bash.close_executor` on a dedicated short-lived loop, so a real ``decode run`` tears
down its Docker container / Modal sandbox even though the sync ``@flow`` cannot ``await`` it. Hermetic:
the executor is a fake (no Docker daemon, no Modal), and in-suite the default ``none`` executor makes
the reap a no-op — so this proves the wiring, not the executors.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kitaru.adapters.pydantic_ai import KitaruAgent
from pydantic_ai.messages import ModelResponse, TextPart
from support.runtime_agents import make_scripted_agent

import decode.runtime.flow as flow_mod
import decode.tools.bash as bash_mod
from decode.runtime import run_agent_task, run_hitl_agent_task
from decode.sandbox.docker_executor import DockerExecutor

# The real flow boots the Kitaru/ZenML stack; scope its two third-party deprecation warnings (see
# test_flow.py) so the strict ``filterwarnings=["error"]`` gate stays green here too.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]


def _inject_fake_executor(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Put a fake executor with an ``aclose`` spy in the bash seam; return the spy."""
    aclose = AsyncMock()
    monkeypatch.setattr(bash_mod, "_EXECUTOR", SimpleNamespace(aclose=aclose))
    monkeypatch.setattr(bash_mod, "_executor_selected", True)
    return aclose


def test_bypass_flow_reaps_the_executor_on_completion(monkeypatch):
    aclose = _inject_fake_executor(monkeypatch)
    agent, _ = make_scripted_agent([ModelResponse(parts=[TextPart(content="done")])])
    durable = KitaruAgent(agent, name="decode-runtime", checkpoint_strategy="calls")
    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: durable)

    handle = run_agent_task.run(task="x")

    assert flow_mod._load_runtime_output(handle.exec_id) == "done"
    aclose.assert_awaited_once()  # the finally reaped the sandbox executor
    assert bash_mod._executor_selected is False  # and reset the seam memo


def test_bypass_flow_reaps_the_executor_even_when_the_flow_errors(monkeypatch):
    """A flow whose ``run_sync`` raises still reaps the executor (the ``finally`` around the body)."""
    aclose = _inject_fake_executor(monkeypatch)

    class _BoomAgent:
        name = "decode-runtime"
        checkpoint_strategy = "calls"

        def run_sync(self, task, deps):
            raise RuntimeError("boom from run_sync")

    monkeypatch.setattr(flow_mod, "_build_runtime_agent", lambda model=None: _BoomAgent())

    # Kitaru may re-raise or return a failed handle; either way the flow's ``finally`` must have run.
    with contextlib.suppress(Exception):
        run_agent_task.run(task="x")

    aclose.assert_awaited_once()


def test_hitl_flow_reaps_the_executor_on_completion(monkeypatch):
    aclose = _inject_fake_executor(monkeypatch)
    agent, _ = make_scripted_agent(
        [ModelResponse(parts=[TextPart(content="done")])],
        name=flow_mod.HITL_RUNTIME_AGENT_NAME,
    )
    durable = flow_mod._to_hitl_durable_agent(agent)
    monkeypatch.setattr(flow_mod, "_build_hitl_runtime_agent", lambda model=None: durable)

    result = run_hitl_agent_task("x")

    assert result.output == "done"
    aclose.assert_awaited_once()  # the HITL flow's finally reaped the sandbox executor too


def _pid_alive(pid: int) -> bool:
    """True while ``pid`` names a live process; False once it is gone (fully reaped, no zombie)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_reap_runtime_executor_reaps_a_loop_bound_executor_cross_loop(monkeypatch, caplog):
    # THE regression that closes the Tester's test-quality gap. The wiring specs above inject a
    # loop-AGNOSTIC ``AsyncMock``, so they pass even on the buggy cross-loop teardown. This drives the
    # REAL ``_reap_runtime_executor`` against a REAL loop-bound ``DockerExecutor`` whose shell was created
    # on a now-CLOSED loop — the exact headless condition (kitaru's per-call loop is gone by teardown).
    # The old teardown raised "Event loop is closed" inside the reap, which ``_reap_runtime_executor``
    # then LOGGED ("headless sandbox teardown failed") and swallowed while the container LEAKED (exit
    # stayed 0, masking the leak). So we assert the reap is CLEAN (no such warning) AND the loop-bound
    # child is actually killed. Hermetic: a real ``sleep`` child stands in for the docker-exec shell and
    # ``_container_id`` is None, so no daemon is touched.
    loop1 = asyncio.new_event_loop()

    async def _spawn() -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            "sleep",
            "30",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )

    shell = loop1.run_until_complete(_spawn())
    pid = shell.pid
    executor = DockerExecutor()
    executor._shell = shell
    executor._shell_loop = loop1
    executor._container_id = None  # only the loop-free shell teardown runs — no ``docker rm``
    loop1.close()  # the per-call loop is gone: the buggy reap raised "Event loop is closed" here

    monkeypatch.setattr(bash_mod, "_EXECUTOR", executor)
    monkeypatch.setattr(bash_mod, "_executor_selected", True)

    with caplog.at_level(logging.WARNING, logger="decode.runtime.flow"):
        flow_mod._reap_runtime_executor()  # sync; the finally's reap — must stay clean

    assert (
        "headless sandbox teardown failed" not in caplog.text
    )  # the buggy path logged this + leaked
    assert not _pid_alive(pid)  # the loop-bound child was actually reaped, not leaked
    assert bash_mod._executor_selected is False  # close_executor reset the seam memo
