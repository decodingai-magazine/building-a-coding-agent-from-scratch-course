"""The Headless Runner: one task, one ``asyncio.run`` around the same ``build_agent()`` the REPL uses.

The autonomous counterpart to the TUI, and nothing more (ADR-0019 §1). The durable runtime it
replaces (Kitaru ``@flow`` + checkpoints + HITL waits) is deleted — upstream removed the
primitives — so a headless run is now a plain async agent run:

* **Bypass**: the gate runs in :data:`~decode.permissions.types.PermissionMode.BYPASS`, so every
  gated tool runs inline with no prompt; ``ask_user`` is the headless no-op resolver. No wait, no
  pause, ever.
* **Workspace**: in a sandbox mode the tool scope is the prepared + warmed Workspace, while
  harness artifacts stay anchored to the launch cwd (ADR-0012 §3,6).
* **Hand-back**: a completed ``decode run --repo`` ships the Workspace as a ``decode/<session-id>``
  Session Branch from THIS host-side process (ADR-0012 §8) — the "runs inside the flow" constraint
  died with the flow.
* **Tracing**: :func:`decode.observability.init_tracing` runs before the agent is built, exactly
  like the TUI, and the run opens one root span keyed on the run's session id (ADR-0014 §4-5).
* **Recording**: the Recording Seam (:mod:`decode.runtime.recording`) decides wrapped-vs-bare; when
  it degrades, this runner echoes its ONE notice line on stderr — stdout stays the answer alone
  (ADR-0019 §3).
* **Request ceiling**: ``max_requests`` (``--max-requests``, else ``RUNTIME_MAX_REQUESTS``) bounds
  the run's model requests through pydantic-ai's ``UsageLimits``; past it the run raises
  :class:`~pydantic_ai.exceptions.UsageLimitExceeded`, which ``decode run`` turns into one friendly
  line and a non-zero exit. The ``finally`` still reaps and hands back, so a capped run ships what it
  did. Unset = unbounded, exactly like the REPL — nobody watches a background run's token bill, so the
  cap is the operator's to set.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import click
from pydantic_ai import Agent, DeferredToolRequests, UsageLimits, capture_run_messages
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage

from decode import observability
from decode.agent.context_window import resolve_context_window
from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.config.settings import settings
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.runtime.recording import one_line, recorded_session_id, wrap_for_recording
from decode.runtime.summary import ExitReason, build_summary, summarize_usage, write_summary
from decode.tools.askuser import deny_user_question_resolver
from decode.tools.bash import close_executor, warm_executor

if TYPE_CHECKING:
    from decode.sandbox.handback import ShipResult

logger = logging.getLogger(__name__)

# The root span a headless run opens; its ``thread_id`` is the run's session id (ADR-0014 §4).
RUN_SPAN_NAME = "decode_run"


def _headless_emit(event: events.Event) -> None:
    """The headless event sink: there is no TUI, so events are only logged at debug level."""
    logger.debug("headless event: %s", type(event).__name__)


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """Deny safety-net for ``resolve_permission`` — never reached under BYPASS (ADR-0019 §1).

    Exists only to satisfy the :class:`~decode.agent.deps.AgentDeps` contract; denying is the safe
    default for an unattended run if the posture ever changes.
    """
    logger.debug("headless permission resolver denying tool=%s", request.tool_name)
    return PermissionDecision.deny(reason="No interactive approver in the headless runner.")


def _build_headless_agent(model: str | None = None) -> Agent[AgentDeps, str | DeferredToolRequests]:
    """The patchable runner seam: the SAME ``build_agent()`` the REPL uses (ADR-0019 §1).

    The one place the headless agent is constructed, so a test can swap in a scripted-model agent.
    ``model`` is the Model Override (``--model``): ``None`` reads the settings default.
    """
    return build_agent(model=model)


def _build_headless_deps(cwd: Path, model: str | None = None) -> AgentDeps:
    """Construct the headless BYPASS :class:`~decode.agent.deps.AgentDeps` (ADR-0012 §6; ADR-0019 §1).

    ``cwd`` is the tool scope (the Workspace in a sandbox mode, else the launch cwd);
    ``harness_home`` always stays the launch cwd. The gate is BYPASS so every gated tool runs
    inline, and both decision resolvers are the headless defaults so ``ask_user`` /
    ``exit_plan_mode`` map to a ``ModelRetry`` instead of hanging. ``model`` is threaded in only to
    resolve THIS run's compaction window: ``decode run --model <smaller-window-id>`` must compact
    against that id.
    """
    home = Path.cwd()
    return AgentDeps(
        cwd=cwd,
        harness_home=home,
        emit=_headless_emit,
        gate=PermissionGate(mode=PermissionMode.BYPASS),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=deny_user_question_resolver,
        context_window_tokens=resolve_context_window(model),
    )


async def _prepare_headless_tool_scope(repo: str | None, local: bool) -> Path:
    """The headless tool scope: the prepared + warmed Workspace in a sandbox mode, else cwd (ADR-0012 §3,6).

    Clones ``repo`` at HEAD when given (``local`` → a fast local clone). A clone failure here is
    **fatal**, unlike in the REPL: ADR-0012 §3's degrade-to-empty policy assumes a human who sees
    the warning and reacts, and nobody is watching a headless run — degrading burns the whole (paid)
    run on an empty directory the Hand-back then skips as "not a git repo". The warm-up stays
    best-effort: the first ``bash`` / file op retries lazily. Both are no-ops in ``none`` mode, so
    the sandbox import stays lazy.
    """
    if settings.sandbox_mode == "none":
        return Path.cwd()
    from decode.sandbox.workspace import prepare_workspace_or_empty

    workspace, clone_error = prepare_workspace_or_empty(Path.cwd(), repo=repo, local=local)
    if clone_error is not None:
        raise RuntimeError(
            f"could not clone {repo!r} into the Workspace, so this run has nothing to work on "
            f"(a headless run has no one to warn): {clone_error}"
        )
    try:
        await warm_executor(workspace)
    except Exception:
        logger.warning(
            "[sandbox] headless sandbox warm-up failed; degrading to lazy start", exc_info=True
        )
    return workspace


def _reap_executor() -> None:
    """Reap the run's sandbox executor at completion — best-effort (ADR-0011 §4).

    Runs the async teardown on a dedicated short-lived loop, after the run's own loop is closed:
    deliberately NOT :func:`asyncio.run`, which would reset the thread's current loop. A failure is
    logged, never raised. A no-op in ``none`` mode.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(close_executor())
    except Exception:
        logger.warning("headless sandbox teardown failed; continuing", exc_info=True)
    finally:
        loop.close()


def _ship_headless_workspace(repo: str | None, session_id: str) -> ShipResult | None:
    """Hand the Workspace back as a ``decode/<session-id>`` Session Branch, host-side (ADR-0012 §8).

    Runs AFTER the executor reap, which is what sweeps a modal sandbox's filesystem back into the
    Workspace this ships. Every git command is a host subprocess against ``.decode/sandbox``, so no
    credential ever enters the sandbox. Best-effort: a hand-back failure never fails a completed
    run; the outcome is logged. A no-op with no repo / in ``none`` mode.

    Returns the :class:`~decode.sandbox.handback.ShipResult` so the run summary can name the branch
    it produced (ADR-0022 §1) — ``None`` when there was no hand-back to do, or when it failed.
    """
    if repo is None or settings.sandbox_mode == "none":
        return None
    from decode.sandbox.handback import ship_workspace

    try:
        result = ship_workspace(Path.cwd(), repo=repo, session_id=session_id)
    except Exception:
        logger.warning("[handback] headless hand-back failed; continuing", exc_info=True)
        return None
    if result.branch is not None:
        logger.info("[handback] %s", result.message)
    return result


def resolve_max_requests(flag: int | None) -> int | None:
    """The run's request ceiling: the ``--max-requests`` flag, else ``RUNTIME_MAX_REQUESTS``, else none.

    The flag is per run; the setting is the operator's default for every run (a cron job's, a
    webhook's). Either is a positive count — a ``None`` at both levels leaves the run unbounded.
    """
    return flag if flag is not None else settings.runtime_max_requests


def _usage_limits(max_requests: int | None) -> UsageLimits | None:
    """The pydantic-ai limits for ``agent.run`` — ``None`` (no limits object at all) when unbounded."""
    return UsageLimits(request_limit=max_requests) if max_requests is not None else None


@dataclass(slots=True)
class _RunState:
    """What the run's ``finally`` needs to write a summary, filled in as the run progresses.

    Mutable and shared across the ``asyncio.run`` boundary on purpose: ``run_headless_task``'s
    ``finally`` is outside the loop, and on the ``request_limit`` / ``error`` paths there is no
    result object to read. ``messages`` is the very list ``capture_run_messages`` mutates in place,
    so it holds whatever the run got through before it stopped. ``exit_reason`` starts at ``error``
    so an escape nobody classified still reports honestly.
    """

    messages: list[ModelMessage] = field(default_factory=list)
    kitaru_session_id: str | None = None
    output: str = ""
    exit_reason: ExitReason = "error"
    error: str | None = None


async def _run_task(
    task: str,
    *,
    model: str | None,
    repo: str | None,
    local: bool,
    session_id: str,
    max_requests: int | None = None,
    state: _RunState | None = None,
) -> str:
    """Run ONE task to completion through the bypass agent and return its final text (ADR-0019 §1).

    ``state`` is the caller's summary scratchpad (ADR-0022 §1): the captured message history, the
    Kitaru Session id when the adapter exposes one. It is filled in as the run goes, so the
    ``finally`` upstairs can report a run that never returned.
    """
    state = state if state is not None else _RunState()
    tool_scope = await _prepare_headless_tool_scope(repo, local)
    # The Recording Seam (ADR-0019 §3): the SAME agent back unless recording is configured, in which
    # case it comes back wrapped for Kitaru, with this run's session id naming the Kitaru Session —
    # so a recorded session, the Opik trace thread and the Session Branch all carry one id.
    agent, recording_notice = await wrap_for_recording(
        _build_headless_agent(model), session_name=session_id
    )
    # A dropped recording is not a guard: the run proceeds and exits 0. But the operator who asked
    # for recording must SEE that they did not get it, before the run burns its tokens — so the one
    # line the seam logged is echoed on stderr too, like the cli's context-window notice. stdout
    # stays exactly the agent's answer (ADR-0019 §1).
    if recording_notice is not None:
        click.echo(recording_notice, err=True)
    state.kitaru_session_id = recorded_session_id(agent)
    deps = _build_headless_deps(tool_scope, model)
    # One root span per run, keyed on the run's session id (a nullcontext when tracing is off), plus
    # the metadata an eval filters and joins traces on (ADR-0022 §10).
    with observability.root_span(
        RUN_SPAN_NAME,
        thread_id=session_id,
        input=task,
        metadata=observability.trace_metadata(model, kitaru_session_id=state.kitaru_session_id),
    ) as span:
        # ``capture_run_messages`` is the only source that survives a raising run: the result object
        # does not exist when the request ceiling fires or the provider errors, but the messages the
        # run DID exchange are already in this list, so the summary reports what was really spent
        # (ADR-0022 §1). Bound immediately — the list is mutated in place, and a copy taken after
        # the ``with`` would only ever run on the completed path.
        with capture_run_messages() as messages:
            state.messages = messages
            result = await agent.run(task, deps=deps, usage_limits=_usage_limits(max_requests))
        observability.record_output(span, result.output)
    output = result.output
    if not isinstance(output, str):
        # Defensive: under BYPASS every tool runs inline, so a deferred request here is a bug.
        raise RuntimeError(
            "the headless runner expected text output but the agent deferred a tool call; "
            "BYPASS mode must run every tool inline (ADR-0019 §1)."
        )
    return output


def run_headless_task(
    task: str,
    *,
    model: str | None = None,
    repo: str | None = None,
    local: bool = False,
    max_requests: int | None = None,
    summary_json: Path | None = None,
) -> str:
    """Run ``task`` headlessly and return the agent's final text — the whole runtime (ADR-0019 §1).

    Sync by design: ``decode run`` is a Click command, and one ``asyncio.run`` is the entire
    concurrency story. Tracing is initialized first (idempotent, a silent no-op without a key), the
    run gets a fresh session id that names BOTH its trace thread and its Hand-back Session Branch,
    and the ``finally`` reaps the sandbox executor and then hands the Workspace back — in that
    order, because the reap is what sweeps the sandbox filesystem into the Workspace the hand-back
    ships. Both run on error too: a crashed run still ships its work — and so does a run that hit
    its ``max_requests`` ceiling (``--max-requests``, else ``RUNTIME_MAX_REQUESTS``), which raises
    :class:`~pydantic_ai.exceptions.UsageLimitExceeded` out of here for ``decode run`` to report.

    ``summary_json`` (``--summary-json``) writes ONE JSON object at the very end of that ``finally``
    — after the Hand-back, so it can name the Session Branch — giving a Benchmark Trial ground truth
    without parsing a trace (ADR-0022 §1). Unset, nothing at all is written and the run is
    byte-identical; a write failure is a logged warning and never touches the exit code.
    """
    session_id = str(uuid4())
    max_requests = resolve_max_requests(max_requests)
    # A silent no-op without a key (ADR-0014 §4-5). An Environment-Bucket-hydrated OPIK_API_KEY is
    # already in ``settings`` — hydration is process-scoped, at singleton construction (ADR-0015 §5).
    observability.init_tracing()
    logger.debug(
        "headless run starting (session_id=%s, model=%r, repo=%r, local=%s, max_requests=%r)",
        session_id,
        model,
        repo,
        local,
        max_requests,
    )
    state = _RunState()
    try:
        output = asyncio.run(
            _run_task(
                task,
                model=model,
                repo=repo,
                local=local,
                session_id=session_id,
                max_requests=max_requests,
                state=state,
            )
        )
    except UsageLimitExceeded:
        # The ceiling is not a failure: the run did exactly what it was told to do and stopped, so
        # the summary reports the spend and leaves ``error`` null (ADR-0022 §1).
        state.exit_reason = "request_limit"
        raise
    except BaseException as error:
        # Includes the KeyboardInterrupt a benchmark's agent-timeout sends: a run killed at its
        # deadline still owes the harness a summary of what it spent (ADR-0022 §4).
        state.error = one_line(error)
        raise
    else:
        state.exit_reason = "completed"
        state.output = output
        return output
    finally:
        _reap_executor()
        handback = _ship_headless_workspace(repo, session_id)
        if summary_json is not None:
            # LAST, after the Hand-back, so the summary can name the Session Branch it produced.
            write_summary(
                summary_json,
                build_summary(
                    session_id=session_id,
                    kitaru_session_id=state.kitaru_session_id,
                    exit_reason=state.exit_reason,
                    error=state.error,
                    usage=summarize_usage(state.messages),
                    handback=handback,
                    output=state.output,
                ),
            )
