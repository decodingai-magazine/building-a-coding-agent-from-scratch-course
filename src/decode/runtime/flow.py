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

import asyncio
import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from decode.tools.bash import BASH_TOOL_NAME, close_executor
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


def _reap_runtime_executor() -> None:
    """Reap the session's sandbox executor at headless-flow completion — best-effort (ADR-0011 §4).

    Called in a ``finally`` around each flow body (bypass + HITL) so a ``decode run`` tears down its
    Docker container / Modal sandbox even when the flow errors or pauses. The ``@flow`` body is sync and
    :func:`decode.tools.bash.close_executor` is async, so it runs to completion on a **dedicated**
    short-lived event loop created and closed here — deliberately NOT :func:`asyncio.run`, which resets
    the thread's current loop and orphans the one pydantic-ai's ``run_sync`` leaves set (an unclosed-loop
    ``ResourceWarning`` under ``filterwarnings=error``); this loop sets nothing current, so it never
    touches ``run_sync``'s loop. A teardown failure is logged, never raised, so it cannot mask the flow's
    result; ``--rm`` (docker) / the modal ``timeout`` are the crash backstops. A no-op in ``none`` mode
    (``LocalExecutor`` has no teardown) and when no ``bash`` ran this session.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(close_executor())
    except Exception:
        logger.warning("headless sandbox teardown failed; continuing", exc_info=True)
    finally:
        loop.close()


def _start_runtime_executor(executor: Any, workspace: Path) -> None:
    """Eagerly start the installed sandbox ``executor`` against ``workspace`` — best-effort (ADR-0012 §2).

    The headless mirror of :func:`decode.tools.bash.warm_executor`: the docker Credential-Proxy flow
    brings the worker up before the first ``bash`` so its CA is trusted. The ``@flow`` body is sync and
    ``start`` is async, so it runs on a **dedicated** short-lived loop (like :func:`_reap_runtime_executor`
    — never :func:`asyncio.run`, which would reset the thread's current loop and orphan ``run_sync``'s).
    Fresh-exec makes this loop-agnostic: only the container id is captured here, and every later ``exec``
    / ``docker rm -f`` spawns its own subprocess. A warm-up failure is logged, never raised — the first
    ``bash`` retries the create lazily and renders any persistent failure to the model.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(executor.start(workspace))
    except Exception:
        logger.warning(
            "[sandbox] headless sandbox warm-up failed; degrading to lazy start", exc_info=True
        )
    finally:
        loop.close()


def _warm_headless_executor(workspace: Path) -> None:
    """Eagerly warm the headless sandbox executor against ``workspace`` — best-effort (ADR-0012 §2,6).

    The headless mirror of the REPL's warm-up (:func:`decode.tools.bash.warm_executor`): it selects the
    sandbox executor by ``SANDBOX_MODE`` and starts it against the Workspace so ``bash`` *and* the file
    tools share one live container / remote sandbox. Runs ``warm_executor`` on a **dedicated** short-lived
    loop (like :func:`_start_runtime_executor` — never :func:`asyncio.run`, which would reset the thread's
    current loop and orphan ``run_sync``'s). **Idempotent**: on the proxy path the executor
    :func:`_sandbox_proxy` already installed + started is found and its ``start`` is a no-op; on the
    non-proxy sandbox path it does the lazy select + start. A warm-up failure is logged, never raised — the
    first ``bash`` / file op retries the create lazily. ``warm_executor`` is a no-op in ``none`` mode.
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


def _prepare_headless_tool_scope() -> Path:
    """The headless agent tool scope: the prepared+warmed Workspace in a sandbox mode, else cwd (§3,6).

    ``none`` → the launch cwd (``deps.cwd == harness_home``, byte-identical). A sandbox mode → the
    isolated Workspace: :func:`~decode.sandbox.workspace.prepare_workspace` ensures ``.decode/sandbox``
    (empty this task — ``--repo`` lands in 082), then :func:`_warm_headless_executor` starts the executor
    against it (idempotent w.r.t. the :func:`_sandbox_proxy` path). Returned as ``deps.cwd`` so the
    file/search tools + ``bash`` operate inside the Workspace while harness artifacts stay at ``Path.cwd()``.
    The sandbox import stays lazy so ``none`` pulls in no sandbox module (ADR-0012 §9).
    """
    if settings.sandbox_mode == "none":
        return Path.cwd()
    from decode.sandbox.workspace import prepare_workspace

    workspace = prepare_workspace(Path.cwd())  # repo=None this task (082 wires --repo/SANDBOX_REPO)
    _warm_headless_executor(workspace)
    return workspace


@contextmanager
def _sandbox_proxy() -> Iterator[None]:
    """Run the docker Credential Proxy for a headless flow span, tear it down on exit (ADR-0011 §6).

    Engaged **only** when ``settings.sandbox_mode == "docker"`` **and**
    ``settings.sandbox_credential_proxy_enabled`` — otherwise a pure no-op that yields immediately,
    imports nothing, and touches no seam, so every ``none`` / ``modal`` / proxy-disabled headless flow
    stays byte-unchanged and the REPL never imports :mod:`decode.sandbox.proxy` or kitaru. When engaged
    it (1) resolves the credential map host-side from :data:`~decode.sandbox.proxy.DEFAULT_PROXY_RULES`,
    (2) starts a ``mitmproxy`` addon container on a per-run docker network, (3) installs a proxy-wired
    ``SandboxExecutor(DockerBackend(...))`` as ``bash``'s executor for the flow span (the seam via
    :func:`decode.tools.bash.install_executor`) and eagerly starts it against the Workspace
    (``prepare_workspace`` — no repo this task, ADR-0012 §3), and (4) tears it all down.

    Teardown order is load-bearing and loop-independent (ADR-0011 §4; trivial under ADR-0012 fresh-exec):
    reap the **worker** first (:func:`_reap_runtime_executor` — ``docker rm -f`` + reset the bash seam),
    THEN
    :meth:`~decode.sandbox.proxy.DockerCredentialProxy.stop` the proxy container + remove the network
    (``docker network rm`` fails while the worker is still attached). ``proxy.stop()`` runs even if
    ``proxy.start()`` raised partway, so a half-built proxy still cleans up. Mirrors
    :func:`_config_from_secret_store` / :func:`_durable_sleeper` (install a flow-span seam, restore it
    on exit) and nests **inside** ``_config_from_secret_store`` so a proxy rule reads config already
    hydrated from the Kitaru secret.
    """
    if not (settings.sandbox_mode == "docker" and settings.sandbox_credential_proxy_enabled):
        yield
        return
    # Lazy imports: only an enabled docker headless flow pulls in the sandbox proxy (REPL stays clean).
    from decode.sandbox.docker_backend import DockerBackend
    from decode.sandbox.executor import SandboxExecutor
    from decode.sandbox.proxy import (
        DEFAULT_PROXY_RULES,
        DockerCredentialProxy,
        build_credential_map,
    )
    from decode.sandbox.workspace import prepare_workspace
    from decode.tools.bash import install_executor

    proxy = DockerCredentialProxy(build_credential_map(DEFAULT_PROXY_RULES))
    try:
        proxy.start()
        executor = SandboxExecutor(
            DockerBackend(
                network=proxy.network,
                proxy_env=proxy.worker_proxy_env,
                ca_cert_host_path=proxy.ca_cert_host_path,
            )
        )
        install_executor(executor)
        # Eagerly bring the worker up against the Workspace so its CA is trusted before the first bash
        # (no repo this task — 082 wires --repo/SANDBOX_REPO). Run on a fresh loop: the sync flow body
        # cannot ``await``, and fresh-exec means the container id is loop-agnostic (later ``exec`` /
        # ``docker rm -f`` spawn their own subprocesses), so warming here is safe.
        _start_runtime_executor(executor, prepare_workspace(Path.cwd()))
        try:
            yield
        finally:
            # Reap the worker BEFORE the proxy: ``_reap_runtime_executor`` does ``docker rm -f`` on the
            # worker (detaching it from the network) and resets the bash seam. The outer flow ``finally``
            # calls it again — harmless (idempotent: it finds the reset LocalExecutor and no-ops).
            _reap_runtime_executor()
    finally:
        proxy.stop()


def _build_runtime_agent(
    model: str | None = None,
) -> KitaruAgent[AgentDeps, str | DeferredToolRequests]:
    """The patchable runtime seam: wrap ``build_agent()`` in ``KitaruAgent`` (ADR-0008 §2).

    Mirrors the bash ``_EXECUTOR`` / lsp ``_spawn_process`` seams: the one place a real
    ``KitaruAgent`` is constructed, so a test can patch it to inject a scripted-model agent and
    exercise the real ``@flow`` + adapter offline. ``checkpoint_strategy`` comes from settings
    (``"calls"`` — per model/tool call — is the default; ``"turn"`` is one coarse checkpoint per run).

    ``flow_mode=True`` engages the **Credentials Proxy** (ADR-0008 §5): when
    ``settings.runtime_credentials_proxy_enabled`` the provider key is resolved from a Kitaru secret
    here (inside the flow body), so a deployed flow payload carries the secret name, not the raw key.

    ``model`` is the **Model Override** (ADR-0010 §2) threaded from :func:`run_agent_task`: ``None``
    (the default) reads ``settings.<provider>_model``, byte-unchanged; a value overrides only the
    active provider's model id, which is what lets Kitaru swap it on a what-if Replay.

    **Replay-safety for sandbox bash (ADR-0011 §5).** A sandbox ``bash`` has real shell side effects,
    so a cached checkpoint would serve a stale, side-effect-free result on a ``decode replay`` (which
    is bypass-only). When ``settings.sandbox_mode != "none"`` this configures ``bash`` to
    **re-execute on replay** instead — ``checkpoint_strategy="calls"`` (the default) plus
    ``tool_checkpoint_config_by_name={BASH_TOOL_NAME: {"cache": False}}``, which keeps the per-call
    checkpoint but disables its cache. Verified on kitaru 0.18: ``CheckpointConfig`` is a
    ``TypedDict(total=False)``, so ``{"cache": False}`` is a valid per-tool config that KEEPS the
    checkpoint — a bare ``False`` would DROP it (the HITL waiter opt-out) and lose replay-readiness. In
    ``none`` mode no such kwarg is passed, so the ``KitaruAgent`` build is byte-identical to task 070.
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
    """Construct the headless :class:`~decode.agent.deps.AgentDeps` (ADR-0008 §2; ADR-0012 §6).

    ``cwd`` is the agent's **tool scope** — the isolated Workspace in a sandbox mode (passed from
    :func:`_prepare_headless_tool_scope`), else the launch directory; ``harness_home`` is always the
    launch cwd, so harness artifacts (memory injection, skills) stay anchored there while the file/search
    tools + ``bash`` operate in the Workspace. ``cwd=None`` defaults to the launch cwd (the ``none``-mode
    equal-roots case, byte-identical). ``emit`` only logs (no TUI); the gate is in **BYPASS** so every
    gated tool runs inline (no ``ApprovalRequired`` → no Kitaru wait); and both decision resolvers are the
    headless deny defaults so ``ask_user`` / ``exit_plan_mode`` map to a ``ModelRetry`` instead of hanging.
    ``active_agent`` defaults (via the dataclass factory) to the full-tool ``build`` persona.
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
    — **not** ``.wait().output``: under the default ``"calls"`` strategy (ADR-0010 §3) the run ends in
    several terminal per-call checkpoints, so ``.wait()`` cannot auto-extract a single value (it raises
    ``_MultipleTerminalStepsOutputError`` — verified in task 068). Using the sink unconditionally keeps
    the read-back identical under the ``"turn"`` opt-out (one terminal checkpoint) too. This is the same
    output-artifact mechanism the HITL flow uses; see :func:`decode.cli` for the read-back.

    ``model`` is the **Model Override** (ADR-0010 §2), a keyword-defaulted flow input threaded to the
    seam: ``None`` (the default) reads ``settings.<provider>_model`` — so ``run(task=…)`` without a
    model is byte-unchanged — while a value overrides only the active provider's model id. Because it
    is a flow input, Kitaru can swap it on a what-if Replay (``run_agent_task.replay(..., model=…)``).

    When ``settings.runtime_secret_store_config`` is on (ADR-0008 §5) the whole run executes inside
    :func:`_config_from_secret_store`, so ``build_agent`` reads config hydrated from the Kitaru secret;
    off (the default) it is a no-op and behaviour is byte-unchanged. It nests
    :func:`_sandbox_proxy` (ADR-0011 §6): with ``sandbox_mode == "docker"`` and the Credential Proxy
    enabled, the run's sandboxed ``bash`` routes through a mitmproxy container so the token-free worker
    makes authenticated tool calls; off/non-docker it is a no-op and the flow is byte-unchanged.

    The whole body runs under a ``finally`` that reaps the sandbox executor
    (:func:`_reap_runtime_executor`, ADR-0011 §4), so a ``decode run`` tears down its Docker container /
    Modal sandbox on completion **and** on error. A no-op in ``none`` mode.
    """
    try:
        with _config_from_secret_store(), _sandbox_proxy():
            tool_scope = _prepare_headless_tool_scope()
            durable_agent = _build_runtime_agent(model)
            deps = _build_headless_deps(tool_scope)
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
    finally:
        _reap_runtime_executor()


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


def _build_hitl_deps(cwd: Path | None = None) -> AgentDeps:
    """Construct the headless **gating** deps for the HITL flow (ADR-0008 §3; ADR-0012 §6).

    Unlike the bypass deps (:func:`_build_headless_deps`), the gate runs in
    :attr:`~decode.permissions.types.PermissionMode.DEFAULT` (a *gating* mode) and
    ``headless_durable_waits`` is ``True``: with no decode loop to run the gate,
    :func:`decode.tools.approval.needs_approval` applies the read-only-allow floor itself, so
    read-only tools run inline while ``write`` / ``edit`` / ``bash`` raise ``ApprovalRequired`` (the
    adapter turns it into a durable approval wait). ``resolve_user_question`` is the durable
    :func:`flow_resolve_user_question` bridge so ``ask_user`` / ``exit_plan_mode`` pause on a wait.
    ``resolve_permission`` stays the deny safety-net: the adapter resolves approvals natively from
    ``ApprovalRequired`` under ``run_sync``, so this resolver is never reached. ``cwd`` is the tool scope
    (the Workspace in a sandbox mode, else the launch cwd) and ``harness_home`` the artifact root, the
    same Harness-Home split as :func:`_build_headless_deps` (ADR-0012 §6).
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

    Like the bypass flow, the whole body runs under a ``finally`` that reaps the sandbox executor
    (:func:`_reap_runtime_executor`, ADR-0011 §4) on completion, error, or the deny-return path.
    """
    try:
        with _config_from_secret_store(), _sandbox_proxy():
            tool_scope = _prepare_headless_tool_scope()
            durable_agent = _build_hitl_runtime_agent(model)
            deps = _build_hitl_deps(tool_scope)
            # The durable sleeper is installed only for the span of ``run_sync`` and reset on exit, so a
            # ``sleep`` in this run pauses on a flow-scope ``kitaru.wait`` (ADR-0008 §4) while a later
            # in-process interactive ``sleep`` still uses :func:`asyncio.sleep` (no leakage).
            with _durable_sleeper():
                try:
                    result = durable_agent.run_sync(task, deps=deps)
                except _ToolApprovalDenied:
                    # The operator rejected a tool approval. The adapter raises out of ``run_sync`` (it
                    # has no feed-back-to-model path), so the run stops here — the denied tool never ran.
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
    finally:
        _reap_runtime_executor()


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


def run_hitl_agent_task(task: str, model: str | None = None) -> HitlRunResult:
    """Launch the HITL flow and return its result or its paused execution id (ADR-0008 §3).

    On the local Kitaru stack ``flow.run(...)`` runs the execution in-process and returns once it has
    either finished or paused on an unresolved wait, so the handle's status is current here. A
    finished run's text is loaded from the output artifact (``.wait()`` cannot auto-extract it under
    the ``"calls"`` + opt-out shape). A paused run yields ``paused=True`` + the ``exec_id`` to resolve
    out-of-band — the caller surfaces the ``kitaru executions input`` instructions.

    ``model`` is the **Model Override** (ADR-0010 §2), forwarded to the ``@flow`` as a flow input:
    ``None`` (the default) reads ``settings.<provider>_model``, byte-unchanged; a value overrides only
    the active provider's model id. It is exposed as ``decode run --hitl --model`` (ADR-0010 §4).
    """
    handle = run_agent_task_hitl.run(task=task, model=model)
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
# Replay: a what-if re-run of a recorded BYPASS run with a swapped Model Override
# (ADR-0010 §5-6, task 070). decode wraps Kitaru's native flow-object replay 1:1 and adds only the
# enablers; diff / cohort / checkpoint-overrides stay on the Kitaru operator surface (documented in
# AGENTS.md, not wrapped). Kitaru's ``.replay(exec_id, from_=…, model=…)`` re-executes a recorded run
# from the ``from_`` checkpoint: everything upstream serves from cache, the anchor + its downstream
# descendants re-execute for real — so a swapped ``model`` only bites the turns re-executed downstream.
# ---------------------------------------------------------------------------

# The ZenML pipeline (flow) name Kitaru records each ``@flow`` under is the flow function's own name —
# Kitaru's ``build_pipeline_registration_name`` is an identity transform for these already-valid
# identifiers. Verified on kitaru 0.18: ``KitaruClient().executions.get(exec_id).flow_name`` is exactly
# the flow function's name. It is how :func:`is_hitl_execution` tells a replayable BYPASS run from a HITL
# run ``decode replay`` must refuse (bypass-only — a HITL replay re-asks every wait on the local stack,
# ADR-0010 §5,7). Derived from the flow object so the constant can never drift from the flow name.
HITL_RUNTIME_PIPELINE_NAME = run_agent_task_hitl.__name__  # "run_agent_task_hitl" — the HITL flow


def is_hitl_execution(exec_id: str) -> bool:
    """True when ``exec_id`` was recorded by the HITL flow, not the bypass flow (ADR-0010 §5).

    ``decode replay`` is **bypass-only**: a HITL replay re-asks every durable wait on the local stack
    (Kitaru cannot pre-populate wait results — ADR-0010 §7, ``tasks/future/hitl-replay-answer-reuse.md``),
    so the cli refuses a HITL exec_id with guidance instead of silently re-prompting. Detection reads the
    recorded **flow name** from the Kitaru execution record —
    ``KitaruClient().executions.get(exec_id).flow_name`` — and compares it to the HITL flow's name
    (verified on kitaru 0.18). A missing/unloadable exec_id raises ``KitaruBackendError`` from
    ``executions.get``; the caller turns that into one friendly "could not load" line (no traceback).
    ``kitaru`` is imported lazily here so the REPL path (which never calls this) stays kitaru-free.
    """
    from kitaru import KitaruClient

    return KitaruClient().executions.get(exec_id).flow_name == HITL_RUNTIME_PIPELINE_NAME


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """The outcome of a successful bypass Replay: the Fork's id, the source id, and the (re)computed text.

    ``exec_id`` is the **new** (Fork) execution Kitaru created; ``original_exec_id`` is the source run
    the replay anchored on. The flow-object ``FlowHandle`` does not expose the source (only the SDK
    ``client.executions.replay`` return carries ``original_exec_id`` — verified on kitaru 0.18), so decode
    carries the input id forward. ``output`` is the possibly-changed final text, loaded from the terminal
    :data:`RUNTIME_OUTPUT_ARTIFACT` — the same read-back the bypass ``decode run`` uses, because ``.wait()``
    cannot auto-extract a single value under the ``"calls"`` terminal sink (ADR-0010 §3).
    """

    exec_id: str
    original_exec_id: str
    output: str


def replay_agent_task(exec_id: str, *, from_: str, model: str | None) -> ReplayResult:
    """Replay a recorded **bypass** run from ``from_`` with an optional Model Override (ADR-0010 §5).

    A thin 1:1 wrapper over Kitaru's native flow-object replay: ``run_agent_task.replay(exec_id,
    from_=…, model=…)``. ``from_`` maps straight to Kitaru's ``from_`` — decode invents no default anchor
    (Kitaru *requires* it; the cli surfaces that requirement as a friendly line when ``--from`` is
    omitted). ``model=None`` replays as-is (the run's recorded model); a value swaps only the active
    provider's model id on the turns re-executed downstream of ``from_``.

    On the local stack ``.replay(...)`` runs the Fork in-process and returns once finished, so the output
    is read back here from the terminal :func:`_capture_runtime_output` artifact via
    :func:`_load_runtime_output` (bypass never pauses). Kitaru's replay failures propagate to the cli,
    which renders each as one friendly line: an ambiguous/invalid ``from_`` (``KitaruStateError``), a
    swap that diverged the recorded call sequence (``KitaruDivergenceError``), and a missing/unloadable
    ``exec_id`` (``KitaruBackendError``). ``kitaru`` is reached only through the flow object (which imports
    it at this module's load time), so the REPL path — which never imports this module — never loads it.
    """
    handle = run_agent_task.replay(exec_id, from_=from_, model=model)
    return ReplayResult(
        exec_id=handle.exec_id,
        original_exec_id=exec_id,
        output=_load_runtime_output(handle.exec_id),
    )
