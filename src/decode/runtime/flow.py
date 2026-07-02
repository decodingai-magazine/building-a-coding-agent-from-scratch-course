"""The Headless Runtime: a Kitaru Durable Flow that runs ``build_agent()`` autonomously (ADR-0008).

This is decode's **second entry path** (ADR-0008 §1). The interactive TUI drives ``agent.iter()``
through the harness and streams to the terminal; this module instead runs the **same**
:func:`decode.agent.factory.build_agent` headlessly inside a Kitaru ``@flow`` so an unattended run
is durable — each turn is checkpointed and a crash replays finished turns from cache instead of
re-paying for them. Launched by ``decode run "<task>"`` (see :mod:`decode.cli`).

**Durability via the PydanticAI adapter (ADR-0008 §2).** :func:`_build_runtime_agent` wraps the
factory's :class:`~pydantic_ai.Agent` in :class:`kitaru.adapters.pydantic_ai.KitaruAgent`. The flow
calls ``KitaruAgent.run_sync(task)`` — Kitaru's loop, **not** decode's interactive loop. The flow
body is **sync**; the adapter bridges the async pydantic-ai agent internally, so there is no manual
asyncio here.

**Two flows, one ``build_agent()`` (ADR-0008 §2-3).** ``run_sync`` does not use decode's loop, so
the loop's deferred-approval round-trip (which resolves every ``ApprovalRequired`` through the
permission gate) is not in play, and the Kitaru adapter converts *any* ``ApprovalRequired`` into a
flow-scope ``kitaru.wait()`` (a human-in-the-loop pause). The two flows differ only in how they
handle that:

* :func:`run_agent_task` — the **bypass** run (task 058): the gate is in **BYPASS**, so every gated
  tool runs **inline** (no ``ApprovalRequired``, no wait) and ``run_sync`` returns a clean text
  result. No human; ``ask_user`` / ``exit_plan_mode`` deny-resolve to a ``ModelRetry``.
* :func:`run_agent_task_hitl` — the **HITL** run (task 059): the gate is in a **gating** mode
  (``DEFAULT``) with ``headless_durable_waits=True``, so read-only tools still run inline but
  ``write`` / ``edit`` / ``bash`` raise ``ApprovalRequired`` (→ durable approval wait) and
  ``ask_user`` / ``exit_plan_mode`` call :func:`flow_resolve_user_question` → ``wait_for_input`` (→
  durable question wait). Each wait is resolved out-of-band (``kitaru executions input``).

**Headless safety (bypass).** ``ask_user`` / ``exit_plan_mode`` route through
:func:`decode.tools.askuser.deny_user_question_resolver`, which raises so the tool maps it to a
``ModelRetry`` ("no human attached") and the agent proceeds without an answer rather than hanging.
``resolve_permission`` is a deny safety-net that is never reached under BYPASS (the gate never asks).

**Kitaru imports stay inside this package** so importing :mod:`decode.cli` (the REPL path) never
imports kitaru — the ``run`` subcommand imports :mod:`decode.runtime` lazily.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from kitaru import checkpoint, flow, save
from kitaru.adapters.pydantic_ai import KitaruAgent, wait_for_input
from kitaru.adapters.pydantic_ai._toolset import _ToolApprovalDenied
from pydantic_ai import DeferredToolRequests

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.config.settings import reload_settings, set_secret_hydration_active, settings
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.tools.askuser import ASK_USER_TOOL_NAME, deny_user_question_resolver
from decode.tools.bash import BASH_TOOL_NAME
from decode.tools.files import EDIT_TOOL_NAME, WRITE_TOOL_NAME
from decode.tools.orchestration import EXIT_PLAN_MODE_TOOL_NAME
from decode.tools.sleep import (
    SLEEP_TOOL_NAME,
    install_durable_sleeper,
    reset_sleeper,
)

logger = logging.getLogger(__name__)

# The stable Agent name Kitaru needs for checkpoint identity (the factory's Agent has none).
# It names the per-turn checkpoint, so it must be stable across runs for replay to hit cache.
RUNTIME_AGENT_NAME = "decode-runtime"
# A distinct name for the HITL durable agent so its checkpoints never collide with the bypass run's.
HITL_RUNTIME_AGENT_NAME = "decode-runtime-hitl"

# The named artifact the HITL flow stores its final text under. ``checkpoint_strategy="calls"`` +
# the wait opt-outs leave the flow with several terminal model-request checkpoints, so Kitaru cannot
# auto-extract a single return value via ``.wait()`` (ADR-0008 §3). The flow instead saves its
# output under this stable name in a final checkpoint and the reader loads it back by name.
RUNTIME_OUTPUT_ARTIFACT = "decode_runtime_output"

# What the HITL flow records as its output when an operator **denies** a tool approval. The Kitaru
# adapter resolves a denied approval by raising ``_ToolApprovalDenied`` out of ``run_sync`` (it has no
# feed-the-denial-back-to-the-model path the way decode's interactive gate does — ADR-0008 §3), so a
# deny STOPS the run before the tool acts. The flow catches it and finishes cleanly with this text.
_HITL_DENIED_MESSAGE = (
    "The operator denied a required tool approval, so the task was stopped before that step ran."
)

# The tools that PAUSE on a flow-scope wait in the durable runtime and so must be opted out of their
# per-call checkpoints (the Kitaru adapter rule: a wait must live at flow scope, not inside a
# ``*_tool`` checkpoint — ADR-0008 §3). Three reasons a tool waits: ``write`` / ``edit`` / ``bash``
# raise ``ApprovalRequired`` (the adapter turns it into a durable approval wait); ``ask_user`` /
# ``exit_plan_mode`` call ``wait_for_input`` from their body; and ``sleep``, once the durable sleeper
# is installed (ADR-0008 §4), calls ``kitaru.wait`` directly as a durable timer. ``sleep`` is the one
# *ungated* member — it never raises ``ApprovalRequired`` — but it still creates a flow-scope wait, so
# it needs the same checkpoint opt-out as the gated waiters. Read-only tools never wait (they run
# inline under ``needs_approval``), so they are deliberately absent — opting them out is unnecessary
# and keeping them in keeps the per-call checkpoint that records their work.
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
def _config_from_secret_store() -> Iterator[None]:
    """Hydrate ``settings`` from a Kitaru secret for the span of a flow, restore on exit (ADR-0008 §5).

    When ``settings.runtime_secret_store_config`` is on, this turns the
    :class:`~decode.config.settings.KitaruSecretSettingsSource` on, rebuilds the ``settings`` singleton
    **in place** (so the ``build_agent`` call inside the flow reads the hydrated config — provider,
    model, keys, tuning — sourced from the secret, with the real env still winning), yields, and in
    ``finally`` **restores the original singleton and clears the flag**. The restore is load-bearing
    and mirrors :func:`_durable_sleeper`: the singleton is a module-level global shared by every
    ``from decode.config.settings import settings`` reader, so without it a later in-process
    interactive ``Settings`` read (the REPL shares the process in tests) — or the next flow — would
    inherit the hydrated config and the active source. The snapshot is the exact pre-flow field state,
    so an error inside the flow still leaves the singleton byte-identical to before. When the setting
    is off this is a pure no-op: it yields immediately, imports no kitaru, and touches no settings —
    so the bypass/HITL flows stay byte-unchanged for the default and interactive paths.

    Composes cleanly with :func:`_durable_sleeper`: the HITL flow nests the sleeper inside this
    context (config first, sleeper innermost) so both seams install and tear down independently.
    """
    if not settings.runtime_secret_store_config:
        yield
        return
    # Snapshot the exact pre-flow field state so the restore is byte-identical even on error.
    snapshot = dict(settings.__dict__)
    snapshot_fields_set = set(settings.__pydantic_fields_set__)
    set_secret_hydration_active(True)
    try:
        reload_settings()  # rebuilds the singleton in place, pulling the secret through the source
        yield
    finally:
        set_secret_hydration_active(False)
        settings.__dict__.clear()
        settings.__dict__.update(snapshot)
        settings.__pydantic_fields_set__.clear()
        settings.__pydantic_fields_set__.update(snapshot_fields_set)


@contextmanager
def _durable_sleeper() -> Iterator[None]:
    """Install the durable ``sleep`` seam for a durable run, reset on exit (ADR-0008 §4, task 060).

    The durable HITL flow runs inside this context so a ``sleep`` call pauses on a flow-scope
    ``kitaru.wait`` (a resumable timer) instead of an in-process :func:`asyncio.sleep`. The reset in
    ``finally`` is load-bearing: the seam is a module-level global, so without it a later in-process
    interactive ``sleep`` (the REPL shares the process in tests) would inherit the durable sleeper and
    try to create a Kitaru wait outside any flow. The durable sleeper only works under the HITL agent
    config — ``checkpoint_strategy="calls"`` + ``sleep`` opted out of its checkpoint
    (:data:`_HITL_WAIT_TOOL_NAMES`) + ``allow_sync_tool_body_waits=True`` — because a ``"turn"``
    checkpoint cannot host a flow-scope wait at all (the same constraint task 059 hit for approvals).
    """
    install_durable_sleeper()
    try:
        yield
    finally:
        reset_sleeper()


def _headless_emit(event: events.Event) -> None:
    """The headless event sink: there is no TUI, so events are only logged (ADR-0008 §1).

    The interactive ``emit`` streams events to the terminal; a headless run has no surface to
    render them, so we drop them at debug level. Kitaru's own checkpoint metadata is the durable
    record of what happened.
    """
    logger.debug("runtime event: %s", type(event).__name__)


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """Deny safety-net for ``resolve_permission`` — never reached under BYPASS (ADR-0008 §2).

    Under the headless BYPASS gate no tool call ever resolves to an ``ASK`` (BYPASS auto-allows,
    and gated tools run inline), so this resolver is never invoked. It exists only so the
    :class:`~decode.agent.deps.AgentDeps` contract is satisfied; denying is the safe default for an
    unattended run if the posture ever changes.
    """
    logger.debug("headless permission resolver denying tool=%s", request.tool_name)
    return PermissionDecision.deny(reason="No interactive approver in the headless runtime.")


def _build_runtime_agent(
    model: str | None = None,
) -> KitaruAgent[AgentDeps, str | DeferredToolRequests]:
    """The patchable runtime seam: wrap ``build_agent()`` in ``KitaruAgent`` (ADR-0008 §2).

    Mirrors the bash ``_EXECUTOR`` / lsp ``_spawn_process`` seams: the one place a real
    ``KitaruAgent`` is constructed, so a test can patch it to inject a scripted-model agent and
    exercise the real ``@flow`` + adapter offline. ``checkpoint_strategy`` comes from settings
    (``"turn"`` — one checkpoint per turn — is the MVP default; ``"calls"`` is per model/tool call).

    ``flow_mode=True`` engages the **Credentials Proxy** (ADR-0008 §5): when
    ``settings.runtime_credentials_proxy_enabled`` the provider key is resolved from a Kitaru secret
    here (inside the flow body), so a deployed flow payload carries the secret name, not the raw key.

    ``model`` is the **Model Override** (ADR-0010 §2) threaded from :func:`run_agent_task`: ``None``
    (the default) reads ``settings.<provider>_model``, byte-unchanged; a value overrides only the
    active provider's model id, which is what lets Kitaru swap it on a what-if Replay.
    """
    agent = build_agent(flow_mode=True, model=model)
    return KitaruAgent(
        agent,
        name=RUNTIME_AGENT_NAME,
        checkpoint_strategy=settings.runtime_checkpoint_strategy,
    )


def _build_headless_deps() -> AgentDeps:
    """Construct the headless :class:`~decode.agent.deps.AgentDeps` (ADR-0008 §2).

    ``cwd`` is the launch directory; ``emit`` only logs (no TUI); the gate is in **BYPASS** so
    every gated tool runs inline (no ``ApprovalRequired`` → no Kitaru wait); and both decision
    resolvers are the headless deny defaults so ``ask_user`` / ``exit_plan_mode`` map to a
    ``ModelRetry`` instead of hanging. ``active_agent`` defaults (via the dataclass factory) to the
    full-tool ``build`` persona — the same persona the interactive default uses.
    """
    return AgentDeps(
        cwd=Path.cwd(),
        emit=_headless_emit,
        gate=PermissionGate(mode=PermissionMode.BYPASS),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=deny_user_question_resolver,
    )


@checkpoint
def _capture_runtime_output(output: str) -> str:
    """Persist a flow's final text as the :data:`RUNTIME_OUTPUT_ARTIFACT`, the single-sink terminal step.

    Shared by **both** durable flows (:func:`run_agent_task` and :func:`run_agent_task_hitl`). Under
    ``checkpoint_strategy="calls"`` a run ends in several terminal per-model/tool-call checkpoints, so
    Kitaru cannot auto-extract a single return value via ``.wait()`` — it raises
    ``_MultipleTerminalStepsOutputError`` (ADR-0008 §3 amendment 5; verified for the bypass path in task
    068). This final checkpoint saves the agent's output under a stable artifact name so
    :func:`_load_runtime_output` can load it back by name instead of relying on ``.wait()``.
    """
    save(RUNTIME_OUTPUT_ARTIFACT, output, type="output")
    return output


@flow
def run_agent_task(task: str, model: str | None = None) -> str:
    """Run ``task`` to completion through the durable agent and return its final text (ADR-0008 §1-2).

    Sync ``@flow``: build the durable agent (the patchable seam), construct the headless BYPASS
    deps, and call ``run_sync(task)`` — one or more checkpointed model/tool calls, every tool inline,
    no human wait. A crash mid-run replays the finished checkpoints from the Kitaru cache on a re-run
    rather than re-executing them.

    Launched via ``run_agent_task.run(task=…)`` → a ``FlowHandle``. The final text is stored via the
    terminal :func:`_capture_runtime_output` checkpoint and read back with :func:`_load_runtime_output`
    — **not** ``.wait().output``: under the ``"calls"`` default (ADR-0010 §3) the run ends in several
    terminal per-call checkpoints, so ``.wait()`` cannot auto-extract a single value (it raises
    ``_MultipleTerminalStepsOutputError`` — verified in task 068). This is the same output-artifact
    mechanism the HITL flow uses; see :func:`decode.cli` for the read-back.

    ``model`` is the **Model Override** (ADR-0010 §2), a keyword-defaulted flow input threaded to the
    seam: ``None`` (the default) reads ``settings.<provider>_model`` — so ``run(task=…)`` without a
    model is byte-unchanged — while a value overrides only the active provider's model id. Because it
    is a flow input, Kitaru can swap it on a what-if Replay (``run_agent_task.replay(..., model=…)``).

    When ``settings.runtime_secret_store_config`` is on (ADR-0008 §5) the whole run executes inside
    :func:`_config_from_secret_store`, so ``build_agent`` reads config hydrated from the Kitaru secret;
    off (the default) it is a no-op and behaviour is byte-unchanged.
    """
    with _config_from_secret_store():
        durable_agent = _build_runtime_agent(model)
        deps = _build_headless_deps()
        result = durable_agent.run_sync(task, deps=deps)
    output = result.output
    if not isinstance(output, str):
        # Defensive: under BYPASS every tool runs inline, so a run never resolves to a deferred
        # request. Reaching here means a gated tool ignored bypass — a bug, not a user-facing path.
        raise RuntimeError(
            "headless runtime expected text output but the agent deferred a tool call; "
            "BYPASS mode must run every tool inline (ADR-0008 §2)."
        )
    return _capture_runtime_output(output)


# ---------------------------------------------------------------------------
# Headless HITL: durable approvals + ``ask_user`` as flow-scope Kitaru waits (ADR-0008 §3, task 059)
# ---------------------------------------------------------------------------


def _hitl_wait_name(question: str) -> str:
    """A stable wait name for an ``ask_user`` / ``exit_plan_mode`` question (ADR-0008 §3).

    The name must be **deterministic** so a Replay of the execution reuses the saved answer instead
    of re-prompting: Kitaru keys a resolved wait by its name. We derive it from a short SHA-1 of the
    question text, so the same question always maps to the same wait. (The resolver only receives the
    question — not the pydantic-ai ``tool_call_id`` the adapter uses for ``ApprovalRequired`` waits —
    so two *identical* questions in one run share a name and the second reuses the first answer; a
    rare edge accepted for the headless slice.)
    """
    digest = hashlib.sha1(question.encode("utf-8")).hexdigest()[:8]
    return f"{ASK_USER_TOOL_NAME}:{digest}"


async def flow_resolve_user_question(question: str) -> str:
    """Bridge ``resolve_user_question`` to a durable flow-scope ``wait_for_input`` (ADR-0008 §3).

    The headless complement of the interactive console resolver: ``ask_user`` (and
    ``exit_plan_mode``) ``await`` this for the human's answer, and here that becomes a durable Kitaru
    wait an operator resolves out-of-band (``kitaru executions input``). It is ``async`` (the
    resolver contract) but calls the **sync** :func:`kitaru.adapters.pydantic_ai.wait_for_input`
    directly: under ``KitaruAgent.run_sync`` the agent's event loop runs on Kitaru's workflow thread
    (``allow_sync_tool_body_waits=True``), which is exactly where a flow-scope wait must be created,
    so the blocking call is correct — offloading it to a worker thread (``anyio.to_thread``) would
    move it *off* that thread and trip Kitaru's "waits must be at flow scope" guard. The answer is
    coerced to ``str`` (the tool result contract). Verified against the installed adapter (task 059).
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

    HITL **forces** ``checkpoint_strategy="calls"`` regardless of ``settings.runtime_checkpoint_strategy``
    (which still governs the bypass run): the per-tool checkpoint opt-out that hoists a tool's wait to
    flow scope is only accepted under ``"calls"`` — under ``"turn"`` the single turn checkpoint wraps
    the tool and the wait raises "must be at flow scope". So the wait-capable tools are opted out
    (:data:`_HITL_WAIT_TOOL_NAMES`) and ``allow_sync_tool_body_waits=True`` keeps Pydantic AI's sync
    tool bodies on the workflow thread so ``wait_for_input`` is created there.
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
    """The patchable HITL runtime seam: wrap ``build_agent()`` in the HITL ``KitaruAgent``.

    Mirrors :func:`_build_runtime_agent` (the bypass seam) so a test patches it to inject a
    scripted-model agent and drive the real ``@flow`` + adapter waits offline. ``flow_mode=True``
    engages the Credentials Proxy on the same terms as the bypass seam (ADR-0008 §5), and ``model``
    threads the **Model Override** (ADR-0010 §2) through on the same terms too (``None`` → the
    settings default).
    """
    return _to_hitl_durable_agent(build_agent(flow_mode=True, model=model))


def _build_hitl_deps() -> AgentDeps:
    """Construct the headless **gating** deps for the HITL flow (ADR-0008 §3).

    Unlike the bypass deps (:func:`_build_headless_deps`), the gate runs in
    :attr:`~decode.permissions.types.PermissionMode.DEFAULT` (a *gating* mode) and
    ``headless_durable_waits`` is ``True``: with no decode loop to run the gate,
    :func:`decode.tools.approval.needs_approval` applies the read-only-allow floor itself, so
    read-only tools run inline while ``write`` / ``edit`` / ``bash`` raise ``ApprovalRequired`` (the
    adapter turns it into a durable approval wait). ``resolve_user_question`` is the durable
    :func:`flow_resolve_user_question` bridge so ``ask_user`` / ``exit_plan_mode`` pause on a wait.
    ``resolve_permission`` stays the deny safety-net: the adapter resolves approvals natively from
    ``ApprovalRequired`` under ``run_sync``, so this resolver is never reached.
    """
    return AgentDeps(
        cwd=Path.cwd(),
        emit=_headless_emit,
        gate=PermissionGate(mode=PermissionMode.DEFAULT),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=flow_resolve_user_question,
        headless_durable_waits=True,
    )


@flow
def run_agent_task_hitl(task: str, model: str | None = None) -> str:
    """Run ``task`` headlessly with **durable HITL** approvals + ``ask_user`` waits (ADR-0008 §3).

    The gating complement of :func:`run_agent_task`: same ``build_agent()``, but under a gating gate
    so a mutating tool pauses the whole execution on a durable Kitaru wait an operator resolves
    out-of-band (``kitaru executions input``), and ``ask_user`` / ``exit_plan_mode`` likewise pause
    on a ``wait_for_input``. Read-only tools still run inline. A ``sleep`` becomes a **durable timer**
    here (ADR-0008 §4): the :func:`_durable_sleeper` context swaps its seam so it pauses on a
    flow-scope ``kitaru.wait`` — the execution can suspend and the process exit, then resume — and the
    seam is reset on exit so an in-process ``sleep`` is unaffected. A **denied** approval is caught and
    the run finishes with :data:`_HITL_DENIED_MESSAGE` (the adapter raises rather than feeding the
    denial back to the model — ADR-0008 §3). The final text is stored via
    :func:`_capture_runtime_output`; use :func:`run_hitl_agent_task` to launch and read it back.

    ``model`` is the **Model Override** (ADR-0010 §2), threaded to the HITL seam on the same terms as
    :func:`run_agent_task` (``None`` → ``settings.<provider>_model``, byte-unchanged).

    When ``settings.runtime_secret_store_config`` is on (ADR-0008 §5) the whole run executes inside
    :func:`_config_from_secret_store` (the sleeper nests inside it), so ``build_agent`` reads config
    hydrated from the Kitaru secret; off (the default) it is a no-op and behaviour is byte-unchanged.
    """
    with _config_from_secret_store():
        durable_agent = _build_hitl_runtime_agent(model)
        deps = _build_hitl_deps()
        # The durable sleeper is installed only for the span of ``run_sync`` and reset on exit, so a
        # ``sleep`` in this run pauses on a flow-scope ``kitaru.wait`` (ADR-0008 §4) while a later
        # in-process interactive ``sleep`` still uses :func:`asyncio.sleep` (no leakage).
        with _durable_sleeper():
            try:
                result = durable_agent.run_sync(task, deps=deps)
            except _ToolApprovalDenied:
                # The operator rejected a tool approval. The adapter raises out of ``run_sync`` (it
                # has no feed-back-to-model path), so the run stops here — the denied tool never acted.
                logger.debug("HITL run stopped: an operator denied a tool approval")
                return _capture_runtime_output(_HITL_DENIED_MESSAGE)
    output = result.output
    if not isinstance(output, str):
        # A deferred request escaping ``run_sync`` means a wait-capable tool was not opted out (so
        # the adapter could not hoist its wait) — a wiring bug, not a user-facing path.
        raise RuntimeError(
            "headless HITL runtime expected text output but the agent deferred a tool call; "
            "every wait-capable tool must be opted out of its checkpoint (ADR-0008 §3)."
        )
    return _capture_runtime_output(output)


@dataclass(frozen=True, slots=True)
class HitlRunResult:
    """The outcome of a HITL run: the final text, or a pause awaiting out-of-band resolution.

    ``paused`` is ``True`` when the execution suspended on an unresolved durable wait no operator
    answered before it timed out — an ``ask_user`` / ``exit_plan_mode`` question wait (bounded by
    ``runtime_wait_timeout_s``) or a ``write`` / ``edit`` / ``bash`` approval wait (bounded by the
    adapter's fixed ``600s`` default, which ignores ``runtime_wait_timeout_s`` — a known limitation,
    ADR-0008 §3). ``output`` is then ``None`` and ``exec_id`` is the execution to resolve + resume
    out-of-band. On a completed run ``output`` is the agent's final text loaded from the
    :data:`RUNTIME_OUTPUT_ARTIFACT`.
    """

    exec_id: str
    output: str | None
    paused: bool


def _load_runtime_output(exec_id: str) -> str:
    """Load a finished run's final text from its :data:`RUNTIME_OUTPUT_ARTIFACT` (ADR-0008 §3).

    Shared by both flows: the bypass ``decode run`` (task 068) and the HITL run read the terminal
    :func:`_capture_runtime_output` artifact back by name here, instead of ``.wait().output`` (which
    the ``"calls"`` per-call checkpoints break — ``_MultipleTerminalStepsOutputError``).
    """
    from kitaru import KitaruClient

    client = KitaruClient()
    for artifact in client.artifacts.list(exec_id):
        if artifact.name == RUNTIME_OUTPUT_ARTIFACT:
            return str(artifact.load())
    raise RuntimeError(
        f"HITL execution {exec_id} finished without a {RUNTIME_OUTPUT_ARTIFACT!r} artifact; "
        "the flow did not reach _capture_runtime_output (ADR-0008 §3)."
    )


def run_hitl_agent_task(task: str) -> HitlRunResult:
    """Launch the HITL flow and return its result or its paused execution id (ADR-0008 §3).

    On the local Kitaru stack ``flow.run(...)`` runs the execution in-process and returns once it has
    either finished or paused on an unresolved wait, so the handle's status is current here. A
    finished run's text is loaded from the output artifact (``.wait()`` cannot auto-extract it under
    the ``"calls"`` + opt-out shape). A paused run yields ``paused=True`` + the ``exec_id`` to resolve
    out-of-band — the caller surfaces the ``kitaru executions input`` instructions.
    """
    handle = run_agent_task_hitl.run(task=task)
    status = handle.status
    if status.is_finished and status.is_successful:
        return HitlRunResult(
            exec_id=handle.exec_id, output=_load_runtime_output(handle.exec_id), paused=False
        )
    logger.debug(
        "HITL execution %s did not finish (status=%s) — paused on a wait", handle.exec_id, status
    )
    return HitlRunResult(exec_id=handle.exec_id, output=None, paused=True)
