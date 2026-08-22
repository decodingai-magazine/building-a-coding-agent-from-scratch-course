"""The Recording Seam — presence-based ``KitaruAgent`` wrap, two failure modes (ADR-0019 §3).

Drives the real :func:`decode.runtime.recording.wrap_for_recording` with the whole Kitaru stack
faked at the ``sys.modules`` boundary (``support.kitaru_recording``): no workspace, no credentials,
no network. Four properties carry the design:

* **Presence-based**: ``KITARU_AGENT_ID`` + the adapter's own ``KITARU_API_URL`` → wrapped; either
  missing → the bare agent, with no kitaru module imported at all (the tightened invariant).
* **Degrade (user-launched)**: an unreachable workspace costs ONE warning line — logged AND handed
  back to the caller to show the operator — and the run proceeds on the bare agent; recording is an
  observer, never an availability dependency.
* **Hard fail (Worker Task)**: with ``KITARU_TASK_ID`` in the env the same failure raises, because
  an unrecorded replay is a lying experiment.
* **Transparent**: the wrapper still runs the agent it wraps.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import uuid

import pytest
from support.kitaru_recording import install_fake_recording_stack

import decode.runtime.recording as rec

AGENT_ID = "6f1d6b6a-6f6f-4c0a-9c9a-0f0f0f0f0f0f"
API_URL = "https://f5ee9622-kitaru.example.invalid"

RECORDING_LOGGER = "decode.runtime.recording"


class _StubAgent:
    """The built agent the seam wraps — it only has to be runnable."""

    def __init__(self, answer: str = "the answer") -> None:
        self.answer = answer
        self.prompts: list[str] = []

    async def run(self, task: str, **kwargs: object) -> str:
        self.prompts.append(task)
        return self.answer


@pytest.fixture
def _configured(monkeypatch):
    """A fully configured user-launched recording setup: agent id + the adapter's connection env."""
    monkeypatch.setattr(rec.settings, "kitaru_agent_id", AGENT_ID)
    monkeypatch.setenv("KITARU_API_URL", API_URL)


# --- the presence gate --------------------------------------------------------------------------


def test_recording_is_not_configured_without_an_agent_id(monkeypatch):
    monkeypatch.setenv("KITARU_API_URL", API_URL)

    assert rec.recording_is_configured() is False


def test_recording_is_not_configured_without_the_adapter_connection_env(monkeypatch):
    """The url/key are ADAPTER-owned env; decode adds no settings of its own, so it checks the env."""
    monkeypatch.setattr(rec.settings, "kitaru_agent_id", AGENT_ID)
    monkeypatch.delenv("KITARU_API_URL", raising=False)

    assert rec.recording_is_configured() is False


def test_recording_is_configured_with_an_agent_id_and_the_connection_env(_configured):
    assert rec.recording_is_configured() is True


def test_a_whitespace_only_agent_id_reads_as_unset(monkeypatch):
    monkeypatch.setattr(rec.settings, "kitaru_agent_id", "   ")
    monkeypatch.setenv("KITARU_API_URL", API_URL)

    assert rec.recording_is_configured() is False


def test_a_worker_task_is_recorded_even_without_an_agent_id(monkeypatch):
    """Under a Worker Task the agent id is inferred from the task — recording is mandatory anyway."""
    monkeypatch.setattr(rec.settings, "kitaru_agent_id", "")
    monkeypatch.setenv("KITARU_TASK_ID", str(uuid.uuid4()))

    assert rec.recording_is_configured() is True


# --- unconfigured: the bare agent, and no kitaru at all -----------------------------------------


async def test_an_unconfigured_run_gets_the_bare_agent_back(monkeypatch):
    monkeypatch.setattr(rec.settings, "kitaru_agent_id", "")
    agent = _StubAgent()

    assert await rec.wrap_for_recording(agent, session_name="session-1") == (agent, None)


async def test_an_unconfigured_run_touches_no_kitaru_module(monkeypatch):
    """Belt-and-braces on the import invariant: the fake stack must record nothing at all."""
    stack = install_fake_recording_stack(monkeypatch)
    monkeypatch.setattr(rec.settings, "kitaru_agent_id", "")

    await rec.wrap_for_recording(_StubAgent())

    assert stack.wrapped == []
    assert stack.probe_calls == []
    assert stack.opened == 0


def test_the_unconfigured_seam_imports_no_kitaru_module_in_a_fresh_interpreter(tmp_path):
    """The tightened invariant (ADR-0019 §3): unconfigured, the headless path imports no kitaru.

    A clean subprocess from a ``tmp_path`` cwd (no repo ``.env``) with the kitaru env scrubbed keeps
    this honest regardless of what the rest of the suite already imported: importing the runtime AND
    running the seam must leave ``sys.modules`` kitaru-free.
    """
    code = (
        "import asyncio, sys\n"
        "import decode.runtime\n"  # the headless entry package
        "from decode.runtime.recording import wrap_for_recording\n"
        "class A:\n"
        "    async def run(self, task, **kw):\n"
        "        return 'ok'\n"
        "agent = A()\n"
        "assert asyncio.run(wrap_for_recording(agent, session_name='s')) == (agent, None)\n"
        "leaked = sorted(m for m in sys.modules if m == 'kitaru' or m.startswith('kitaru'))\n"
        "assert not leaked, leaked\n"
        "print('NO_KITARU_OK')\n"
    )
    scrubbed = {"DECODE_ENV", "KITARU_AGENT_ID", "KITARU_API_URL", "KITARU_TASK_ID"}
    child_env = {k: v for k, v in os.environ.items() if k not in scrubbed}

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=child_env,
    )

    assert result.returncode == 0, result.stderr
    assert "NO_KITARU_OK" in result.stdout


# --- configured + reachable: the KitaruAgent wrap -----------------------------------------------


async def test_a_configured_run_is_wrapped_in_kitaru_agent(monkeypatch, _configured):
    stack = install_fake_recording_stack(monkeypatch)
    agent = _StubAgent()

    wrapped, notice = await rec.wrap_for_recording(agent, session_name="session-42")

    assert wrapped is stack.wrapped[0]
    assert wrapped.wrapped is agent
    assert notice is None  # nothing was lost, so the operator hears nothing


async def test_the_wrap_carries_the_configured_agent_id_and_the_session_name(
    monkeypatch, _configured
):
    stack = install_fake_recording_stack(monkeypatch)

    await rec.wrap_for_recording(_StubAgent(), session_name="session-42")

    assert stack.wrapped[0].agent_id == uuid.UUID(AGENT_ID)
    assert stack.wrapped[0].session_name == "session-42"


async def test_the_wrapper_still_runs_the_agent_it_wraps(monkeypatch, _configured):
    install_fake_recording_stack(monkeypatch)
    agent = _StubAgent("recorded answer")

    wrapped, _notice = await rec.wrap_for_recording(agent)

    assert await wrapped.run("do it") == "recorded answer"
    assert agent.prompts == ["do it"]


async def test_the_probe_asks_the_workspace_for_the_configured_agent(monkeypatch, _configured):
    """One authenticated call decides reachability, credentials AND that the agent id is real."""
    stack = install_fake_recording_stack(monkeypatch)

    await rec.wrap_for_recording(_StubAgent())

    assert stack.probe_calls == [("agents.get", uuid.UUID(AGENT_ID))]
    assert stack.closed == 1  # ...and the probe client is always closed


async def test_a_worker_task_probes_reachability_without_an_agent_id(monkeypatch):
    """The Worker infers the agent from the task, so there is nothing to look up — just reach it."""
    stack = install_fake_recording_stack(monkeypatch)
    monkeypatch.setattr(rec.settings, "kitaru_agent_id", "")
    monkeypatch.setenv("KITARU_TASK_ID", str(uuid.uuid4()))

    wrapped, _notice = await rec.wrap_for_recording(_StubAgent())

    assert stack.probe_calls == [("info.get", None)]
    assert wrapped.agent_id is None  # inferred from the task's agent version


# --- configured + unreachable, user-launched: degrade with ONE warning ---------------------------


async def test_an_unreachable_workspace_degrades_to_the_bare_agent(
    monkeypatch, _configured, caplog
):
    install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("connection refused"))
    agent = _StubAgent()

    with caplog.at_level(logging.WARNING, logger=RECORDING_LOGGER):
        returned, notice = await rec.wrap_for_recording(agent, session_name="s")

    assert returned is agent
    assert notice is not None  # ...and the caller gets the line to put in front of the operator


async def test_the_degrade_costs_exactly_one_warning_line_naming_the_workspace(
    monkeypatch, _configured, caplog
):
    """ONE line, naming the server — not a traceback nobody reads (ADR-0019 §3).

    The line the caller gets back is the SAME text that was logged: one message, two sinks (the log
    file for later, the operator's stderr for now), so they cannot drift apart.
    """
    install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("connection refused"))

    with caplog.at_level(logging.WARNING, logger=RECORDING_LOGGER):
        _agent, notice = await rec.wrap_for_recording(_StubAgent())

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert API_URL in warnings[0].getMessage()
    assert warnings[0].exc_info is None  # no traceback dump
    assert "\n" not in warnings[0].getMessage()  # exactly ONE line
    assert notice == warnings[0].getMessage()


async def test_a_multi_line_failure_is_still_one_warning_line(monkeypatch, _configured, caplog):
    """An API error whose body spans lines must not turn the one warning into a wall of text."""
    install_fake_recording_stack(
        monkeypatch, probe_error=RuntimeError("502 Bad Gateway\n<html>\n  <body>down</body>\n")
    )

    with caplog.at_level(logging.WARNING, logger=RECORDING_LOGGER):
        _agent, notice = await rec.wrap_for_recording(_StubAgent())

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "\n" not in warnings[0].getMessage()
    assert notice is not None and "\n" not in notice  # the stderr copy stays one line too


async def test_a_malformed_agent_id_degrades_rather_than_failing_a_user_run(monkeypatch, caplog):
    """Recording is an observer: a typo'd agent id costs the recording, never the run."""
    install_fake_recording_stack(monkeypatch)
    monkeypatch.setattr(rec.settings, "kitaru_agent_id", "not-a-uuid")
    monkeypatch.setenv("KITARU_API_URL", API_URL)
    agent = _StubAgent()

    with caplog.at_level(logging.WARNING, logger=RECORDING_LOGGER):
        returned, notice = await rec.wrap_for_recording(agent)

    assert returned is agent
    assert notice is not None

    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


# --- configured + unreachable, Worker Task: hard fail --------------------------------------------


async def test_a_worker_task_hard_fails_when_the_workspace_is_unreachable(monkeypatch, _configured):
    """A silently unrecorded replay would be a lying experiment — so it must not be silent."""
    install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("connection refused"))
    monkeypatch.setenv("KITARU_TASK_ID", str(uuid.uuid4()))

    with pytest.raises(rec.RecordingUnavailableError, match="recording"):
        await rec.wrap_for_recording(_StubAgent(), session_name="s")


async def test_the_hard_failure_names_the_workspace_and_keeps_the_cause(monkeypatch, _configured):
    install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("connection refused"))
    monkeypatch.setenv("KITARU_TASK_ID", str(uuid.uuid4()))

    with pytest.raises(rec.RecordingUnavailableError) as excinfo:
        await rec.wrap_for_recording(_StubAgent())

    assert API_URL in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ConnectionError)


async def test_a_worker_task_failure_does_not_warn_instead_of_raising(
    monkeypatch, _configured, caplog
):
    """The degrade path must be unreachable under a Worker Task — no warning, no bare agent."""
    install_fake_recording_stack(monkeypatch, probe_error=ConnectionError("nope"))
    monkeypatch.setenv("KITARU_TASK_ID", str(uuid.uuid4()))

    with (
        caplog.at_level(logging.WARNING, logger=RECORDING_LOGGER),
        pytest.raises(rec.RecordingUnavailableError),
    ):
        await rec.wrap_for_recording(_StubAgent())

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
