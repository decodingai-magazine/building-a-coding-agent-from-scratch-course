"""The Headless Runtime: Kitaru Durable Flows that run ``build_agent()`` autonomously.

Two flows, one ``build_agent()``: :func:`run_agent_task` (BYPASS — every gated tool inline, no
human wait) and :func:`run_agent_task_hitl` (gating — ``write``/``edit``/``bash`` and ``ask_user``
pause on durable Kitaru waits resolved out-of-band). Each turn is checkpointed, so a crash replays
finished turns from cache. Kitaru imports stay inside this package so a ``DECODE_ENV=local`` REPL
never imports kitaru. Config (including any Environment-Bucket-hydrated key) is already in
``settings`` when a flow starts — hydration is process-scoped, at singleton construction (ADR-0015
§5). Opik tracing is one Trace per run, initialized inside the flow; run-level nesting is best-effort
under a real provider (worker-thread loops drop OTel context, so some model spans may export as
siblings — tokens are still captured).
See ADR-0008, ADR-0010 (replay), ADR-0012 (sandbox), ADR-0014 (tracing), ADR-0015 (config).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kitaru import ImageSettings, checkpoint, current_execution_id, flow, save
from kitaru.adapters.pydantic_ai import KitaruAgent, wait_for_input
from kitaru.adapters.pydantic_ai._toolset import _ToolApprovalDenied
from pydantic_ai import DeferredToolRequests

from decode import observability
from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.config.settings import settings
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.runtime.modal_app import pin_orchestrator_app
from decode.tools.askuser import ASK_USER_TOOL_NAME, deny_user_question_resolver
from decode.tools.bash import BASH_TOOL_NAME, close_executor
from decode.tools.files import EDIT_TOOL_NAME, WRITE_TOOL_NAME
from decode.tools.orchestration import EXIT_PLAN_MODE_TOOL_NAME
from decode.tools.sleep import (
    SLEEP_TOOL_NAME,
    install_durable_sleeper,
    reset_sleeper,
)

logger = logging.getLogger(__name__)

# Import-time, because a submit happens the moment a flow's ``.run()`` is called and the App name is
# read inside it. Importing this module IS the decision to run durably, so there is no earlier hook —
# and it is a no-op on the local stack (ADR-0008 §1; see ``runtime/modal_app.py``).
pin_orchestrator_app()

# Stable Agent names for Kitaru checkpoint identity — must not change across runs or replay misses
# cache. The HITL name is distinct so its checkpoints never collide with the bypass run's.
RUNTIME_AGENT_NAME = "decode-runtime"
HITL_RUNTIME_AGENT_NAME = "decode-runtime-hitl"

# The named artifact both flows store their final text under: under ``checkpoint_strategy="calls"``
# a run ends in several terminal checkpoints, so ``.wait()`` cannot auto-extract one output
# (ADR-0008 §3); the reader loads it back by name instead.
RUNTIME_OUTPUT_ARTIFACT = "decode_runtime_output"

# Recorded as the HITL flow's output on an operator deny: the adapter raises ``_ToolApprovalDenied``
# out of ``run_sync`` (no feed-the-denial-back-to-the-model path), so a deny stops the run before
# the tool acts (ADR-0008 §3).
_HITL_DENIED_MESSAGE = (
    "The operator denied a required tool approval, so the task was stopped before that step ran."
)

# The Kitaru secret carrying MODAL_TOKEN_ID / MODAL_TOKEN_SECRET into the flow container. They are
# the modal CLI's tokens, not ``Settings`` fields, so the Environment Bucket never carries them
# (ADR-0015); ``secret_environment_from`` is their only route into the container's process env.
MODAL_TOKEN_SECRET_NAME = "decode-modal"


def _runtime_image() -> ImageSettings:
    """The flow container for a remote stack (INFRA.md §4) — ignored by the local stack, which never builds.

    ``DECODE_ENV`` and ``SANDBOX_MODE`` are propagated from the submitting process so a remote run
    keeps the config surface the operator chose: ``DECODE_ENV=prod`` makes the container's
    ``Settings`` read the ``decode-prod`` Environment Bucket off the same Kitaru server (ADR-0015),
    and ``SANDBOX_MODE`` decides where its ``bash`` lands. Only ``modal`` needs the Modal tokens, so
    only ``modal`` demands the secret exist.
    """
    return ImageSettings(
        dockerfile="docker/flow.Dockerfile",
        build_context_root=".",
        platform="linux/amd64",  # Modal runs x86-64; the build host may not
        environment={
            "DECODE_ENV": settings.decode_env,
            "SANDBOX_MODE": settings.sandbox_mode,
        },
        secret_environment_from=(
            [MODAL_TOKEN_SECRET_NAME] if settings.sandbox_mode == "modal" else None
        ),
    )


# Tools that pause on a flow-scope wait in the durable runtime and so must be opted out of their
# per-call checkpoints — a Kitaru wait must live at flow scope, not inside a tool checkpoint
# (ADR-0008 §3). ``sleep`` is ungated but still waits (the durable timer, ADR-0008 §4). Read-only
# tools never wait and keep their per-call checkpoints.
_HITL_WAIT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        WRITE_TOOL_NAME,
        EDIT_TOOL_NAME,
        BASH_TOOL_NAME,
        ASK_USER_TOOL_NAME,
        EXIT_PLAN_MODE_TOOL_NAME,
        SLEEP_TOOL_NAME,
    }
)


@contextmanager
def _durable_sleeper() -> Iterator[None]:
    """Install the durable ``sleep`` seam for a durable run, reset on exit (ADR-0008 §4).

    The reset is load-bearing: the seam is a module-level global, so without it a later in-process
    interactive ``sleep`` would try to create a Kitaru wait outside any flow. Only works under the
    HITL agent config (``"calls"`` + the ``sleep`` checkpoint opt-out + sync tool-body waits).
    """
    install_durable_sleeper()
    try:
        yield
    finally:
        reset_sleeper()


def _headless_emit(event: events.Event) -> None:
    """The headless event sink: there is no TUI, so events are only logged at debug level."""
    logger.debug("runtime event: %s", type(event).__name__)


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """Deny safety-net for ``resolve_permission`` — never reached under BYPASS (ADR-0008 §2).

    Exists only to satisfy the :class:`~decode.agent.deps.AgentDeps` contract; denying is the safe
    default for an unattended run if the posture ever changes.
    """
    logger.debug("headless permission resolver denying tool=%s", request.tool_name)
    return PermissionDecision.deny(reason="No interactive approver in the headless runtime.")


def _reap_runtime_executor() -> None:
    """Reap the session's sandbox executor at headless-flow completion — best-effort (ADR-0011 §4).

    Runs the async teardown on a dedicated short-lived loop — deliberately NOT :func:`asyncio.run`,
    which resets the thread's current loop and orphans the one ``run_sync`` leaves set (an
    unclosed-loop ``ResourceWarning`` under ``filterwarnings=error``). A failure is logged, never
    raised. A no-op in ``none`` mode.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(close_executor())
    except Exception:
        logger.warning("headless sandbox teardown failed; continuing", exc_info=True)
    finally:
        loop.close()


def _warm_headless_executor(workspace: Path) -> None:
    """Eagerly warm the headless sandbox executor against ``workspace`` — best-effort (ADR-0012 §2,6).

    Runs on a dedicated short-lived loop (never :func:`asyncio.run` — see
    :func:`_reap_runtime_executor`). A failure is logged, never raised — the first ``bash`` / file op
    retries lazily. A no-op in ``none`` mode.
    """
    from decode.tools.bash import warm_executor

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(warm_executor(workspace))
    except Exception:
        logger.warning(
            "[sandbox] headless sandbox warm-up failed; degrading to lazy start", exc_info=True
        )
    finally:
        loop.close()


def _ship_headless_workspace(repo: str | None) -> None:
    """Hand the Workspace back **from inside the flow**, after the executor is reaped (ADR-0012 §8).

    The flow's own process is the one that owns the Workspace: it clones ``repo`` there, and the
    executor reap above is what sweeps a modal sandbox's filesystem back into it. On a remote stack
    that process is the Modal flow container — NOT the laptop that submitted the run, whose
    ``.decode/sandbox`` the run never touched — so the hand-back has to live here rather than in the
    cli (a headless run's work is otherwise lost with the container). The push authenticates with
    ``SANDBOX_GIT_TOKEN``, the only git credential a flow container has (ADR-0016 §2,4).

    Best-effort: a hand-back failure never fails a completed run; the outcome is logged (Kitaru
    streams a remote flow's logs back to the submitter).
    """
    if repo is None or settings.sandbox_mode == "none":
        return
    from decode.sandbox.handback import ship_workspace

    try:
        result = ship_workspace(Path.cwd(), repo=repo, session_id=current_execution_id())
    except Exception:
        logger.warning("[handback] headless hand-back failed; continuing", exc_info=True)
        return
    if result.branch is not None:
        logger.info("[handback] %s", result.message)


def _prepare_headless_tool_scope(repo: str | None = None, local: bool = False) -> Path:
    """The headless agent tool scope: the prepared+warmed Workspace in a sandbox mode, else cwd (ADR-0012 §3,6).

    Clones ``repo`` at HEAD when given (``local`` → a fast local clone), degrading to an empty
    Workspace on a clone failure rather than crashing. The sandbox import stays lazy so ``none``
    mode pulls in no sandbox module.
    """
    if settings.sandbox_mode == "none":
        return Path.cwd()
    from decode.sandbox.workspace import prepare_workspace_or_empty

    workspace, _clone_error = prepare_workspace_or_empty(Path.cwd(), repo=repo, local=local)
    _warm_headless_executor(workspace)
    return workspace


def _build_runtime_agent(
    model: str | None = None,
) -> KitaruAgent[AgentDeps, str | DeferredToolRequests]:
    """The patchable runtime seam: wrap ``build_agent()`` in ``KitaruAgent`` (ADR-0008 §2).

    The one place a real ``KitaruAgent`` is constructed, so tests can patch in a scripted-model
    agent. ``model`` is the Model Override (ADR-0010 §2): ``None`` reads the settings default; a
    value is what Kitaru swaps on a what-if Replay. Replay-safety (ADR-0011 §5): a sandbox ``bash``
    has real side effects, so when ``sandbox_mode != "none"`` its checkpoint gets
    ``{"cache": False}`` — this KEEPS the per-call checkpoint but disables its cache so replay
    re-executes it (a bare ``False`` would DROP the checkpoint, the HITL waiter opt-out).
    """
    agent = build_agent(flow_mode=True, model=model)
    replay_safety: dict[str, Any] = {}
    if settings.sandbox_mode != "none":
        replay_safety["tool_checkpoint_config_by_name"] = {BASH_TOOL_NAME: {"cache": False}}
    return KitaruAgent(
        agent,
        name=RUNTIME_AGENT_NAME,
        checkpoint_strategy=settings.runtime_checkpoint_strategy,
        **replay_safety,
    )


def _build_headless_deps(cwd: Path | None = None) -> AgentDeps:
    """Construct the headless BYPASS :class:`~decode.agent.deps.AgentDeps` (ADR-0008 §2; ADR-0012 §6).

    ``cwd`` is the tool scope (the Workspace in a sandbox mode, else the launch cwd);
    ``harness_home`` always stays the launch cwd. The gate is BYPASS so every gated tool runs inline
    (no ``ApprovalRequired`` → no Kitaru wait), and both decision resolvers are the headless deny
    defaults so ``ask_user`` / ``exit_plan_mode`` map to a ``ModelRetry`` instead of hanging.
    """
    home = Path.cwd()
    return AgentDeps(
        cwd=cwd or home,
        harness_home=home,
        emit=_headless_emit,
        gate=PermissionGate(mode=PermissionMode.BYPASS),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=deny_user_question_resolver,
    )


@checkpoint
def _capture_runtime_output(output: str) -> str:
    """Persist a flow's final text as the :data:`RUNTIME_OUTPUT_ARTIFACT` terminal checkpoint.

    Shared by both durable flows: under ``checkpoint_strategy="calls"`` a run ends in several
    terminal checkpoints, so ``.wait()`` cannot auto-extract a single return value
    (``_MultipleTerminalStepsOutputError`` — ADR-0008 §3); :func:`_load_runtime_output` loads the
    artifact back by name instead.
    """
    save(RUNTIME_OUTPUT_ARTIFACT, output, type="output")
    return output


@flow(image=_runtime_image())
def run_agent_task(
    task: str, model: str | None = None, repo: str | None = None, local: bool = False
) -> str:
    """Run ``task`` to completion through the durable BYPASS agent and return its final text (ADR-0008 §1-2).

    Sync ``@flow``: every gated tool runs inline, no human wait; a crash replays finished
    checkpoints from cache on a re-run. The final text is stored via :func:`_capture_runtime_output`
    and read back by name — not ``.wait().output``, which the ``"calls"`` terminal shape breaks.
    ``model`` is the Model Override (ADR-0010 §2) and ``repo`` / ``local`` the Workspace clone
    inputs (ADR-0012 §3); all are flow inputs so a what-if Replay can swap or reuse them. The body
    runs under a ``finally`` that reaps the sandbox executor and then hands the Workspace back
    (ADR-0012 §8) — in that order, because the reap is what sweeps the sandbox's filesystem into the
    Workspace the hand-back ships. Both run on error too: a crashed run still ships its work.
    """
    try:
        # Init tracing INSIDE the flow: idempotent + a silent no-op without a key (ADR-0014 §4-5).
        # An Environment-Bucket-hydrated OPIK_API_KEY is simply already in ``settings`` — hydration
        # is process-scoped now, at singleton construction (ADR-0015 §5).
        observability.init_tracing()
        tool_scope = _prepare_headless_tool_scope(repo, local)
        durable_agent = _build_runtime_agent(model)
        deps = _build_headless_deps(tool_scope)
        # One root span per run, keyed on the Kitaru exec_id (a nullcontext when tracing is off).
        with observability.root_span(
            "decode_run", thread_id=current_execution_id(), input=task
        ) as span:
            result = durable_agent.run_sync(task, deps=deps)
            observability.record_output(span, result.output)
        output = result.output
        if not isinstance(output, str):
            # Defensive: under BYPASS every tool runs inline, so a deferred request here is a bug.
            raise RuntimeError(
                "headless runtime expected text output but the agent deferred a tool call; "
                "BYPASS mode must run every tool inline (ADR-0008 §2)."
            )
        return _capture_runtime_output(output)
    finally:
        _reap_runtime_executor()
        _ship_headless_workspace(repo)


# ---------------------------------------------------------------------------
# Headless HITL: durable approvals + ``ask_user`` as flow-scope Kitaru waits (ADR-0008 §3)
# ---------------------------------------------------------------------------


def _hitl_wait_name(question: str) -> str:
    """A stable wait name for an ``ask_user`` / ``exit_plan_mode`` question (ADR-0008 §3).

    Deterministic (a short SHA-1 of the question) so a Replay reuses the saved answer — Kitaru keys
    a resolved wait by name. Two identical questions in one run share a name and the second reuses
    the first answer; a rare edge, accepted.
    """
    digest = hashlib.sha1(question.encode("utf-8")).hexdigest()[:8]
    return f"{ASK_USER_TOOL_NAME}:{digest}"


async def flow_resolve_user_question(question: str) -> str:
    """Bridge ``resolve_user_question`` to a durable flow-scope ``wait_for_input`` (ADR-0008 §3).

    Async by contract but deliberately calls the sync ``wait_for_input`` directly: under
    ``run_sync`` the agent loop runs on Kitaru's workflow thread, exactly where a flow-scope wait
    must be created — offloading to a worker thread would trip Kitaru's "waits must be at flow
    scope" guard. The operator resolves the wait out-of-band (``kitaru executions input``).
    """
    answer = wait_for_input(
        question=question,
        name=_hitl_wait_name(question),
        schema=str,
        timeout=int(settings.runtime_wait_timeout_s),
    )
    return str(answer)


def _to_hitl_durable_agent(agent: object) -> KitaruAgent[AgentDeps, str | DeferredToolRequests]:
    """Wrap ``agent`` in the HITL ``KitaruAgent`` config (ADR-0008 §3).

    HITL forces ``checkpoint_strategy="calls"``: the per-tool checkpoint opt-out that hoists a
    tool's wait to flow scope is only accepted under ``"calls"`` (a ``"turn"`` checkpoint would wrap
    the tool and the wait would raise). ``allow_sync_tool_body_waits=True`` keeps sync tool bodies
    on the workflow thread so ``wait_for_input`` is created there.
    """
    return KitaruAgent(
        agent,  # type: ignore[arg-type]
        name=HITL_RUNTIME_AGENT_NAME,
        checkpoint_strategy="calls",
        tool_checkpoint_config_by_name=dict.fromkeys(_HITL_WAIT_TOOL_NAMES, False),
        allow_sync_tool_body_waits=True,
    )


def _build_hitl_runtime_agent(
    model: str | None = None,
) -> KitaruAgent[AgentDeps, str | DeferredToolRequests]:
    """The patchable HITL runtime seam — mirrors :func:`_build_runtime_agent` (ADR-0008 §3)."""
    return _to_hitl_durable_agent(build_agent(flow_mode=True, model=model))


def _build_hitl_deps(cwd: Path | None = None) -> AgentDeps:
    """Construct the headless **gating** deps for the HITL flow (ADR-0008 §3; ADR-0012 §6).

    The gate runs in ``DEFAULT`` with ``headless_durable_waits=True``: read-only tools run inline
    while ``write`` / ``edit`` / ``bash`` raise ``ApprovalRequired`` (→ a durable approval wait) and
    ``ask_user`` / ``exit_plan_mode`` pause via :func:`flow_resolve_user_question`.
    ``resolve_permission`` stays the deny safety-net (the adapter resolves approvals natively).
    Same ``cwd`` / ``harness_home`` split as :func:`_build_headless_deps`.
    """
    home = Path.cwd()
    return AgentDeps(
        cwd=cwd or home,
        harness_home=home,
        emit=_headless_emit,
        gate=PermissionGate(mode=PermissionMode.DEFAULT),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=flow_resolve_user_question,
        headless_durable_waits=True,
    )


@flow(image=_runtime_image())
def run_agent_task_hitl(
    task: str, model: str | None = None, repo: str | None = None, local: bool = False
) -> str:
    """Run ``task`` headlessly with **durable HITL** approvals + ``ask_user`` waits (ADR-0008 §3).

    The gating complement of :func:`run_agent_task`: mutating tools and ``ask_user`` /
    ``exit_plan_mode`` pause the execution on durable Kitaru waits resolved out-of-band; read-only
    tools run inline; ``sleep`` becomes a durable timer (ADR-0008 §4). A denied approval stops the
    run, which finishes with :data:`_HITL_DENIED_MESSAGE`. ``model`` / ``repo`` / ``local`` are flow
    inputs on the same terms as the bypass flow, and the body runs under the same executor-reaping
    ``finally``. Launch + read-back via :func:`run_hitl_agent_task`.
    """
    try:
        # Init tracing INSIDE the flow: idempotent + a silent no-op without a key (ADR-0014 §4-5).
        # An Environment-Bucket-hydrated OPIK_API_KEY is simply already in ``settings`` — hydration
        # is process-scoped now, at singleton construction (ADR-0015 §5).
        observability.init_tracing()
        tool_scope = _prepare_headless_tool_scope(repo, local)
        durable_agent = _build_hitl_runtime_agent(model)
        deps = _build_hitl_deps(tool_scope)
        # The durable sleeper spans only ``run_sync`` and resets on exit, so a later in-process
        # interactive ``sleep`` still uses :func:`asyncio.sleep` (no leakage).
        with _durable_sleeper():
            try:
                # One root span per run, keyed on the Kitaru exec_id (a nullcontext when tracing
                # is off). A denied approval unwinds it exactly once before the except below.
                with observability.root_span(
                    "decode_run_hitl", thread_id=current_execution_id(), input=task
                ) as span:
                    result = durable_agent.run_sync(task, deps=deps)
                    observability.record_output(span, result.output)
            except _ToolApprovalDenied:
                # The adapter raises on a deny (no feed-back-to-model path), so the run stops
                # here — the denied tool never ran.
                logger.debug("HITL run stopped: an operator denied a tool approval")
                return _capture_runtime_output(_HITL_DENIED_MESSAGE)
        output = result.output
        if not isinstance(output, str):
            # A deferred request escaping ``run_sync`` means a wait-capable tool was not opted out
            # of its checkpoint — a wiring bug, not a user-facing path.
            raise RuntimeError(
                "headless HITL runtime expected text output but the agent deferred a tool call; "
                "every wait-capable tool must be opted out of its checkpoint (ADR-0008 §3)."
            )
        return _capture_runtime_output(output)
    finally:
        _reap_runtime_executor()


@dataclass(frozen=True, slots=True)
class HitlRunResult:
    """The outcome of a HITL run: the final text, or a pause awaiting out-of-band resolution.

    ``paused=True`` when the execution suspended on an unresolved durable wait — question waits
    honor ``runtime_wait_timeout_s``; native approval waits use the adapter's fixed ``600s`` default
    and ignore it (a known limitation, ADR-0008 §3). ``output`` is then ``None`` and ``exec_id`` is
    the execution to resolve out-of-band.
    """

    exec_id: str
    output: str | None
    paused: bool


def _load_runtime_output(exec_id: str) -> str:
    """Load a finished run's final text from its :data:`RUNTIME_OUTPUT_ARTIFACT` (ADR-0008 §3)."""
    from kitaru import KitaruClient

    client = KitaruClient()
    for artifact in client.artifacts.list(exec_id):
        if artifact.name == RUNTIME_OUTPUT_ARTIFACT:
            return str(artifact.load())
    raise RuntimeError(
        f"HITL execution {exec_id} finished without a {RUNTIME_OUTPUT_ARTIFACT!r} artifact; "
        "the flow did not reach _capture_runtime_output (ADR-0008 §3)."
    )


def run_hitl_agent_task(
    task: str, model: str | None = None, repo: str | None = None, local: bool = False
) -> HitlRunResult:
    """Launch the HITL flow and return its result or its paused execution id (ADR-0008 §3).

    On the local stack ``flow.run(...)`` returns once the execution has finished or paused on an
    unresolved wait, so the handle's status is current here. A paused run yields ``paused=True`` +
    the ``exec_id`` to resolve out-of-band. ``model`` / ``repo`` / ``local`` are forwarded as flow
    inputs (ADR-0010 §2; ADR-0012 §3).
    """
    handle = run_agent_task_hitl.run(task=task, model=model, repo=repo, local=local)
    status = handle.status
    if status.is_finished and status.is_successful:
        return HitlRunResult(
            exec_id=handle.exec_id, output=_load_runtime_output(handle.exec_id), paused=False
        )
    logger.debug(
        "HITL execution %s did not finish (status=%s) — paused on a wait", handle.exec_id, status
    )
    return HitlRunResult(exec_id=handle.exec_id, output=None, paused=True)


# ---------------------------------------------------------------------------
# Replay: a what-if re-run of a recorded BYPASS run with a swapped Model Override (ADR-0010 §5-6).
# Everything upstream of ``from_`` serves from cache; the anchor + downstream re-execute for real,
# so a swapped model only bites the re-executed turns. Diff / cohort / checkpoint-overrides stay on
# the Kitaru operator surface (documented in AGENTS.md, not wrapped).
# ---------------------------------------------------------------------------

# Kitaru records each ``@flow`` under the flow function's own name (verified on kitaru 0.18).
# Derived from the flow object so the constant can never drift from the flow name.
HITL_RUNTIME_PIPELINE_NAME = run_agent_task_hitl.__name__


def is_hitl_execution(exec_id: str) -> bool:
    """True when ``exec_id`` was recorded by the HITL flow, not the bypass flow (ADR-0010 §5).

    ``decode replay`` is bypass-only (a HITL replay re-asks every durable wait — ADR-0010 §7), so
    the cli refuses a HITL exec_id. A missing exec_id raises ``KitaruBackendError``; the caller
    renders it as one friendly line. ``kitaru`` is imported lazily so the REPL path stays
    kitaru-free.
    """
    from kitaru import KitaruClient

    return KitaruClient().executions.get(exec_id).flow_name == HITL_RUNTIME_PIPELINE_NAME


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """The outcome of a successful bypass Replay: the Fork's id, the source id, and the (re)computed text.

    ``exec_id`` is the new Fork execution; ``original_exec_id`` the source run (the flow-object
    handle does not expose it, so decode carries the input id forward). ``output`` is loaded from
    the terminal :data:`RUNTIME_OUTPUT_ARTIFACT`, the same read-back the bypass ``decode run`` uses.
    """

    exec_id: str
    original_exec_id: str
    output: str


def replay_agent_task(exec_id: str, *, from_: str, model: str | None) -> ReplayResult:
    """Replay a recorded **bypass** run from ``from_`` with an optional Model Override (ADR-0010 §5).

    A thin 1:1 wrapper over ``run_agent_task.replay(exec_id, from_=…, model=…)`` — decode invents no
    default anchor (Kitaru requires ``from_``; the cli surfaces its omission as a friendly line).
    ``model=None`` replays as-is; a value swaps only the turns re-executed downstream. Kitaru's
    replay failures (invalid anchor, divergence, missing exec_id) propagate to the cli, which
    renders each as one friendly line.
    """
    handle = run_agent_task.replay(exec_id, from_=from_, model=model)
    return ReplayResult(
        exec_id=handle.exec_id,
        original_exec_id=exec_id,
        output=_load_runtime_output(handle.exec_id),
    )
