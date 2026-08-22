"""The Recording Seam: the ONE place decode decides whether a run is recorded (ADR-0019 §3).

:func:`wrap_for_recording` takes a built agent and returns either ``kitaru_pydantic_ai.KitaruAgent``
wrapped around it (recorded — every LLM and tool call lands on the Kitaru workspace as a Kitaru
Session) or the very same agent, bare — plus the ONE operator-visible notice line the caller must
surface when recording was silently dropped. Nothing else in decode imports the adapter, so this
module is the whole recording story:

* **Presence-based opt-in.** Recording is configured when ``KITARU_AGENT_ID`` is set AND the adapter
  client's own connection env (``KITARU_API_URL``) is present. decode adds no url/key settings of
  its own — the adapter's client resolves those itself, so there is exactly one place to configure
  the workspace. Empty agent id → the bare agent, and **no kitaru module is ever imported**: both
  imports below sit inside the configured branch (the tightened import invariant, ADR-0019 §3).
* **Degrade, user-launched.** The adapter fast-fails at session creation when the workspace is
  unreachable, which would take a paid run down with it. So the seam probes the workspace ONCE
  before wrapping and, on any failure, returns the bare agent with exactly ONE warning line naming
  the workspace (no traceback). The run proceeds and exits 0 — recording is an observer, never an
  availability dependency. That one line goes to the log AND back to the caller, because a log file
  nobody is tailing is not a warning: the seam itself never writes to a terminal (the REPL wiring
  would fight prompt_toolkit for it), so the caller owns the surface — ``decode run`` echoes it on
  stderr, exactly like :func:`decode.cli._context_window_notice`.
* **Hard fail, Worker-spawned.** With ``KITARU_TASK_ID`` in the env the process is executing a Kitaru
  Worker Task, where an unrecorded run would be a lying experiment. The same failure raises
  :class:`RecordingUnavailableError` instead, and the process exits non-zero. Recording is then
  mandatory even without ``KITARU_AGENT_ID``, because the adapter infers the agent from the task.

Probing rather than catching the first ``run()`` is deliberate: the alternative — running wrapped,
catching the session-creation error and re-running bare — cannot tell a recording failure from an
agent failure, and a re-run repeats whatever tool side effects the first attempt already made.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
from uuid import UUID

from decode.config.settings import settings

if TYPE_CHECKING:
    from pydantic_ai.agent import AbstractAgent

logger = logging.getLogger(__name__)

# ADAPTER-owned connection env, read (never written) here: its presence is the second half of the
# opt-in, and its value names the workspace in the degrade warning. The matching key/token vars are
# deliberately NOT read — the adapter's client resolves credentials itself (env, else the on-disk
# ``kitaru login`` store), and duplicating that resolution is how the two drift apart.
API_URL_ENV = "KITARU_API_URL"

# Set by a Kitaru Worker in the env of every process it spawns for a Worker Task: the switch between
# the two failure modes.
TASK_ID_ENV = "KITARU_TASK_ID"

# How much of a failure's text rides on the one warning line before it is cut.
_REASON_MAX_CHARS = 200


class RecordingUnavailableError(RuntimeError):
    """A run that MUST be recorded (a Worker Task) could not be — so it fails instead of lying."""


def is_worker_task() -> bool:
    """True when a Kitaru Worker spawned this process for a Worker Task (ADR-0019 §4)."""
    return bool(os.environ.get(TASK_ID_ENV))


def recording_is_configured() -> bool:
    """True when this process should record its runs as Kitaru Sessions (ADR-0019 §3).

    A Worker Task is ALWAYS recorded — that is the entire point of the run, and the adapter infers
    the agent id from the task, so ``KITARU_AGENT_ID`` is not required there. Otherwise both halves
    of the opt-in must be present: decode's agent id and the adapter's connection env.
    """
    if is_worker_task():
        return True
    return bool(settings.kitaru_agent_id.strip()) and bool(os.environ.get(API_URL_ENV))


def _configured_agent_id() -> UUID | None:
    """The configured Kitaru agent as a ``UUID``, or ``None`` to let a Worker Task infer it.

    Raises ``ValueError`` on a malformed id — a recording setup failure like any other, handled by
    the caller's degrade / hard-fail split rather than by a second error path here.
    """
    raw = settings.kitaru_agent_id.strip()
    return UUID(raw) if raw else None


def _workspace_label() -> str:
    """The workspace the adapter will talk to, for the one warning line."""
    return os.environ.get(API_URL_ENV) or "the Kitaru workspace configured by your kitaru login"


def _one_line(error: Exception) -> str:
    """``error`` as a single short line: the cause, never a multi-line HTML body or a traceback."""
    text = " ".join(f"{type(error).__name__}: {error}".split())
    return text if len(text) <= _REASON_MAX_CHARS else f"{text[:_REASON_MAX_CHARS]}…"


async def _probe_workspace(agent_id: UUID | None) -> None:
    """Reach the Kitaru workspace once, so an unreachable one is known BEFORE the agent runs.

    With an agent id this is an authenticated lookup of that very agent, so one call settles all
    three ways recording can be misconfigured: the url, the credentials, and the id itself. A Worker
    Task has no id to look up (the task carries it), so it checks plain reachability instead.

    Raises whatever the client raises; the caller owns the degrade / hard-fail split.
    """
    from kitaru.client import KitaruAPIClient

    client = KitaruAPIClient()
    try:
        if agent_id is not None:
            await client.agents.get(agent_id)
        else:
            await client.info.get()
    finally:
        await client.close()


async def wrap_for_recording[DepsT, OutputT](
    agent: AbstractAgent[DepsT, OutputT], *, session_name: str | None = None
) -> tuple[AbstractAgent[DepsT, OutputT], str | None]:
    """Return ``(agent-to-run, degrade notice)``: wrapped for Kitaru when configured, else as given.

    ``session_name`` names the resulting Kitaru Session: the run's session id for headless
    ``decode run``, so one recorded session maps to one decode session (and, in the REPL, so a
    conversation's turns group under one name).

    The second element is ``None`` on every path except the degrade — where it is the SAME one line
    that was logged, handed back so the caller can put it in front of the operator (stderr for
    ``decode run``). Library code here never writes to a terminal itself: the caller owns that
    surface, mirroring ``prepare_workspace_or_empty``'s ``(workspace, error)`` shape.

    The wrapper is transparent — ``KitaruAgent`` is a pydantic-ai ``WrapperAgent`` whose ``run`` /
    ``iter`` behave exactly like the wrapped agent's — so callers need not know which one they got.
    """
    if not recording_is_configured():
        return agent, None

    worker = is_worker_task()
    try:
        agent_id = _configured_agent_id()
        await _probe_workspace(agent_id)
        from kitaru_pydantic_ai import KitaruAgent

        wrapped = KitaruAgent(agent, agent_id=agent_id, session_name=session_name)
    except Exception as error:  # broad on purpose: ANY setup failure takes one of the two exits
        if worker:
            raise RecordingUnavailableError(
                f"[kitaru] recording is unavailable for this Kitaru Worker Task: "
                f"{_workspace_label()} could not be reached ({_one_line(error)}). Failing the run "
                f"rather than producing an unrecorded — and therefore untrustworthy — replay."
            ) from error
        # The ONE line: what was lost, where, and why — no traceback (ADR-0019 §3). Logged for the
        # post-hoc reader AND returned for the operator watching the run right now.
        notice = (
            f"[kitaru] not recording this run: {_workspace_label()} is unavailable "
            f"({_one_line(error)}); continuing on the bare agent"
        )
        logger.warning("%s", notice)
        return agent, notice

    logger.info(
        "[kitaru] recording this run on %s (agent_id=%s, session_name=%s)",
        _workspace_label(),
        agent_id,
        session_name,
    )
    return wrapped, None
