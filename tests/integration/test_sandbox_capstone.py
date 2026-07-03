"""The sandboxing capstone: the whole ``SANDBOX_MODE`` feature end to end (ADR-0011, tasks 071-076).

This is the living proof for the sandboxing feature — and it doubles as documentation, in the style of
:mod:`tests.integration.test_milestone1_capstone` (swap only the boundary),
:mod:`tests.integration.test_runtime_capstone` (patch the seam, ``skipif`` the real stack), and
:mod:`tests.integration.test_lsp_capstone` (a real-wire smoke guarded on a binary probe). It has two
parts: an **always-run offline slice** and three **``skipif``-guarded real-infra smokes**, so ``make ci``
stays green on a machine with no Docker / no Modal.

**The feature in one paragraph.** decode runs model-chosen shell commands through the ADR-0002
``CommandExecutor`` **run seam** (:mod:`decode.tools.exec`); ``SANDBOX_MODE`` — read once at startup and
fixed for the session — selects which executor ``bash`` runs through, behind that **one** seam with zero
change to ``bash``'s own logic:

* ``none`` — the host :class:`~decode.tools.exec.LocalExecutor` (a subprocess), the default,
  **byte-identical** to M1;
* ``docker`` — one session-persistent local container driving a persistent bash shell over the
  bind-mounted cwd (:class:`~decode.sandbox.docker_executor.DockerExecutor`, ``cd`` / ``export`` persist);
* ``modal`` — one session-persistent **remote** empty-scratch ``modal.Sandbox``
  (:class:`~decode.sandbox.modal_executor.ModalExecutor`, no local tree).

The model is told the active mode's live semantics through the mode-specific ``bash`` **description**
(``none`` byte-identical; ``docker`` / ``modal`` append their sandbox-semantics paragraph). A headless
``decode run`` in a sandbox additionally (a) re-executes ``bash`` on a what-if Replay instead of serving a
stale, side-effect-free cached turn — the ``{"cache": False}`` bash checkpoint config (ADR-0011 §5) — and
(b) can route the sandboxed worker's outbound tool calls through a docker-only **Credential Proxy**: a
``mitmproxy`` addon container that injects a per-host header **after** the request leaves the token-free
worker, so the worker holds **no** secret (ADR-0011 §6). The whole design keeps the interactive REPL
kitaru-free and the ``none`` path free of every sandbox executor module.

**Part 1 — the always-run offline slice (no docker, no modal, no network, no ``GEMINI_API_KEY``).**
Like the M1 capstone it swaps only the boundaries; everything structural is real:

* **REAL** — the ADR-0002 run seam via the ``none``-mode :class:`~decode.tools.exec.LocalExecutor` (a real
  ``echo`` round-trips through the real :func:`~decode.agent.factory.build_agent` registry + the real
  :class:`~decode.permissions.gate.PermissionGate` to an :class:`~decode.tools.exec.ExecResult` and
  renders); the real ``SANDBOX_MODE`` → executor-class selection; the real per-mode ``bash`` description
  the model receives; the real host-side :func:`~decode.sandbox.proxy.build_credential_map`; and the real
  replay-safety wiring in :func:`decode.runtime.flow._build_runtime_agent`.
* **FAKED** — the model is a scripted :class:`~pydantic_ai.models.function.FunctionModel`
  (``GEMINI_API_KEY`` is faked only so ``build_agent`` constructs); the ``docker`` / ``modal`` executors
  are stubbed at the :func:`decode.sandbox.select_executor` seam (so no daemon / no account is touched);
  ``kitaru.get_secret`` is patched for the credential-map resolution; and the bypass ``KitaruAgent`` build
  is spied so the replay-safety kwarg is asserted without booting a flow.

**Part 2 — the skipif-guarded real-infra smokes.** Each SKIPS (never fails) when its infra is absent,
using the **same** predicates as the executors' own integration tests (a ``docker info`` probe; the
``modal`` credential presence check): a real :class:`~decode.sandbox.docker_executor.DockerExecutor`
persistent-shell round-trip, a real :class:`~decode.sandbox.modal_executor.ModalExecutor` remote-scratch
round-trip, and the real docker Credential-Proxy boundary (an authenticated outbound call arrives with the
injected header while the worker env holds no secret). Each tears its container / sandbox / network down
in a ``finally`` so the suite is hermetic under ``filterwarnings=["error"]`` and leaves no infra litter.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import TurnContext
from decode.permissions.gate import PermissionGate
from decode.sandbox.docker_executor import DockerExecutor
from decode.sandbox.modal_executor import ModalExecutor
from decode.sandbox.proxy import (
    DEFAULT_PROXY_RULES,
    DockerCredentialProxy,
    SandboxProxyRule,
    build_credential_map,
)
from decode.tools import bash as bash_mod
from decode.tools.askuser import deny_user_question_resolver
from decode.tools.exec import ExecResult, LocalExecutor

_BASH = bash_mod.BASH_TOOL_NAME


# ================================================================================================
# Hermeticity fixtures — a faked key so ``build_agent`` constructs offline, and a reset of the module
# -level ``bash`` executor seam around every test (the selection swap mutates a global memo).
# ================================================================================================


@pytest.fixture(autouse=True)
def _fake_gemini_key(mocker) -> None:
    """Let ``build_agent`` construct the Gemini provider offline (the model is always overridden)."""
    from pydantic import SecretStr

    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )


@pytest.fixture(autouse=True)
def _reset_bash_executor_seam() -> AsyncIterator[None]:
    """Reset ``bash``'s cached executor memo before and after each test (it is a module global).

    The selection-swap tests patch ``settings.sandbox_mode`` + the ``select_executor`` seam and let
    ``bash`` memoize a stubbed sandbox executor; resetting on both sides restores the ``none``-mode
    :class:`~decode.tools.exec.LocalExecutor` default so a stub never leaks into a later test.
    """
    bash_mod.reset_executor()
    yield
    bash_mod.reset_executor()


# ================================================================================================
# Scripted drivers — a REAL decode agent (full ``build_agent`` registry) on a scripted FunctionModel,
# driven through the REAL interactive gated loop so the run seam + permission gate are exercised.
# ================================================================================================


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    """A permission resolver that denies — used where no gated tool is expected to be approved."""
    return PermissionDecision.deny()


def _last_request_has_tool_return(messages: list[Any]) -> bool:
    """True when the most recent request carries a tool result (i.e. this is a resume leg)."""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            return any(isinstance(part, ToolReturnPart) for part in message.parts)
    return False


def _bash_stream_model(command: str) -> FunctionModel:
    """A streaming model that calls ``bash(command)`` on the fresh leg, then ends the turn with text.

    The interactive loop streams every model node, so the model must *stream* (yield), not return
    (matching the M1 capstone): it yields the ``bash`` tool call on the first leg and plain text once
    the tool result has come back, so the turn fires exactly one gated ``bash`` and finishes.
    """

    async def stream_function(messages: list[Any], info: AgentInfo) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield "the command ran"
            return
        yield {0: DeltaToolCall(name=_BASH, json_args=json.dumps({"command": command}))}

    return FunctionModel(stream_function=stream_function)


async def _drive_one_gated_bash_turn(
    command: str, *, cwd: Path
) -> tuple[list[str], list[events.Event]]:
    """Drive ONE gated ``bash(command)`` through the real agent + gate + registry; return its returns+events.

    Builds the real :func:`~decode.agent.factory.build_agent` agent (so the real flat tool registry and
    the real deferred-approval seam are exercised), overrides its model with :func:`_bash_stream_model`,
    and runs it through the real :class:`~decode.agent.loop.AgentTurnHandler` with a real
    :class:`~decode.permissions.gate.PermissionGate` + an approving resolver. Which executor actually
    runs the command is whatever the active ``SANDBOX_MODE`` selects (``none`` → the real
    :class:`~decode.tools.exec.LocalExecutor`; ``docker`` / ``modal`` → the stubbed
    :func:`decode.sandbox.select_executor` seam) — so the same driver proves both the executor contract
    and the mode→executor selection swap. Returns every ``bash`` tool-return string and the emitted events.
    """
    emitted: list[events.Event] = []

    async def _approve(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    deps = AgentDeps(
        cwd=cwd,
        emit=emitted.append,
        gate=PermissionGate(),
        resolve_permission=_approve,
        resolve_user_question=deny_user_question_resolver,
    )
    agent = build_agent()
    handler = AgentTurnHandler(agent, deps=deps)

    async def _run() -> None:
        agen = handler(TurnContext(0, "run a command", emitted.append))
        with contextlib.suppress(StopAsyncIteration):
            await agen.asend(None)
            while True:
                await agen.asend([])
        await agen.aclose()

    with agent.override(model=_bash_stream_model(command)):
        await _run()

    returns = [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == _BASH
    ]
    return returns, emitted


class _RecordingExecutor:
    """A fake :class:`~decode.tools.exec.CommandExecutor` that records ``run`` and returns a canned result.

    Stands in for a real sandbox executor at the :func:`decode.sandbox.select_executor` seam, so the
    selection swap + the ``ExecResult`` render path are proven with **no** Docker / Modal touched.
    ``start_calls`` records the eager REPL warm-up (``warm_executor`` → ``start(cwd)``).
    """

    def __init__(self, result: ExecResult) -> None:
        self._result = result
        self.calls: list[tuple[str, Path, float]] = []
        self.start_calls: list[Path] = []

    async def start(self, cwd: Path) -> None:
        self.start_calls.append(cwd)

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ExecResult:
        self.calls.append((command, cwd, timeout_s))
        return self._result


# ================================================================================================
# 1. Executor contract — a command round-trips the run seam to an ExecResult and renders; the note
#    surfaces on a (simulated) timeout; ``none``-mode rendering is byte-identical.
# ================================================================================================


async def test_none_mode_command_round_trips_the_run_seam_and_renders(tmp_path: Path) -> None:
    """``none`` mode: a real ``echo`` runs through the real gate + the host LocalExecutor and renders.

    The executor-contract anchor. A gated ``bash`` is surfaced to the permission gate and approved,
    the real :class:`~decode.tools.exec.LocalExecutor` runs the real command under ``cwd``, and its
    :class:`~decode.tools.exec.ExecResult` renders the exit code + captured stdout back to the model —
    the whole ADR-0002 run seam, proven with no infra and no faked executor.
    """
    returns, emitted = await _drive_one_gated_bash_turn("echo capstone-sandbox-ok", cwd=tmp_path)

    # The gate asked for bash (it is gated defense-in-depth beneath the sandbox — ADR-0011 §4) ...
    assert any(isinstance(e, events.PermissionRequested) and e.name == _BASH for e in emitted), (
        "the gated bash call must be surfaced to the permission gate"
    )
    # ... and the real LocalExecutor round-tripped the command to a rendered ExecResult.
    assert returns, "the bash result must reach the model as a tool return"
    assert any("Exit code: 0" in r and "capstone-sandbox-ok" in r for r in returns)


async def test_execresult_note_surfaces_through_bash_on_a_simulated_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    """A sandbox executor's out-of-band ``note`` (the docker timeout shell-reset) reaches the model.

    ``docker`` mode with a stubbed executor that returns ``timed_out=True`` + a shell-reset ``note`` (the
    real :class:`~decode.sandbox.docker_executor.DockerExecutor` timeout contract, ADR-0011 §2, without a
    daemon): the tool must flag the timeout AND append the note, so the model learns its shell state was
    reset — state the command's own output would never reveal.
    """
    reset_note = (
        "Note: the command exceeded its timeout, so the sandbox shell was killed and restarted. Its "
        "working directory and environment were reset."
    )
    stub = _RecordingExecutor(
        ExecResult(stdout="partial", stderr="", exit_code=-9, timed_out=True, note=reset_note)
    )
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: stub)
    bash_mod.reset_executor()

    returns, _ = await _drive_one_gated_bash_turn("sleep 100", cwd=tmp_path)

    assert returns
    assert any("timed out" in r.lower() and "reset" in r.lower() for r in returns), (
        "the timeout header and the shell-reset note must both reach the model"
    )


def test_none_mode_rendering_is_byte_identical_with_an_empty_note() -> None:
    """An empty ``note`` (every ``none``-mode LocalExecutor result) renders exactly as before the field.

    Pins the byte-for-byte model-facing output so the sandbox ``note`` plumbing (ADR-0011 §2) can never
    regress the default path: the shipped ``none``-mode shape is unchanged.
    """
    result = ExecResult(stdout="hi\n", stderr="", exit_code=0, timed_out=False)

    assert bash_mod._render(result, timeout_s=120.0) == "Exit code: 0.\n\nstdout:\nhi"


# ================================================================================================
# 2. Selection swap + per-mode description — ``SANDBOX_MODE`` picks the executor class, ``bash`` routes
#    a command through the selected one, and the model-facing description adapts per mode.
# ================================================================================================


def test_sandbox_mode_selects_the_matching_executor_class(monkeypatch) -> None:
    """``SANDBOX_MODE`` → the right :class:`~decode.tools.exec.CommandExecutor` class, inertly (no infra).

    Through the REAL :func:`decode.sandbox.select_executor` (not faked): ``none`` keeps the eager host
    :class:`~decode.tools.exec.LocalExecutor` (no sandbox module imported), ``docker`` yields a
    :class:`~decode.sandbox.docker_executor.DockerExecutor`, and ``modal`` a
    :class:`~decode.sandbox.modal_executor.ModalExecutor` — construction is inert for all three (no
    container started, no remote sandbox created, no ``modal`` SDK imported), so no daemon / account is
    needed to prove the mapping.
    """
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "none")
    bash_mod.reset_executor()
    assert isinstance(bash_mod._get_executor(), LocalExecutor)

    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    bash_mod.reset_executor()
    assert isinstance(bash_mod._get_executor(), DockerExecutor)

    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "modal")
    bash_mod.reset_executor()
    assert isinstance(bash_mod._get_executor(), ModalExecutor)


async def test_bash_routes_a_command_through_the_selected_executor(
    tmp_path: Path, monkeypatch
) -> None:
    """``docker`` mode: ``bash`` runs the command through the executor ``select_executor`` returns.

    A recording stub stands in for :class:`~decode.sandbox.docker_executor.DockerExecutor` at the
    selection seam (no daemon). The gated ``bash`` call — driven through the real agent + gate — must
    route the command through that stub (proving the seam swap end to end) and render its canned
    :class:`~decode.tools.exec.ExecResult` back to the model.
    """
    stub = _RecordingExecutor(
        ExecResult(stdout="sandboxed-output", stderr="", exit_code=0, timed_out=False)
    )
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: stub)
    bash_mod.reset_executor()

    returns, _ = await _drive_one_gated_bash_turn("echo hi", cwd=tmp_path)

    # The command was routed through the SELECTED (stubbed docker) executor, with bash's cwd threaded ...
    assert len(stub.calls) == 1
    assert stub.calls[0][0] == "echo hi"
    assert stub.calls[0][1] == tmp_path
    # ... and its ExecResult was rendered back to the model.
    assert any("sandboxed-output" in r and "Exit code: 0" in r for r in returns)


async def test_warm_executor_starts_the_selected_sandbox_and_the_turn_reuses_it(
    tmp_path: Path, monkeypatch
) -> None:
    """The REPL warm-up chain: ``warm_executor`` starts the SAME executor the turn then runs through.

    The eager-start slice of the interactive flow (ADR-0011 §4): ``warm_executor(cwd)`` must select
    the executor once, await its ``start(cwd)``, and the later gated ``bash`` turn — driven through
    the real agent + gate — must route through that SAME warmed instance. One selection, one start:
    warm-then-reuse continuity, with no infra touched.
    """
    stub = _RecordingExecutor(
        ExecResult(stdout="warmed-output", stderr="", exit_code=0, timed_out=False)
    )
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", "docker")
    monkeypatch.setattr("decode.sandbox.select_executor", lambda mode: stub)
    bash_mod.reset_executor()

    await bash_mod.warm_executor(tmp_path)
    returns, _ = await _drive_one_gated_bash_turn("echo hi", cwd=tmp_path)

    assert stub.start_calls == [tmp_path]  # warmed exactly once, at launch time
    assert len(stub.calls) == 1  # the turn ran through the SAME warmed instance
    assert any("warmed-output" in r for r in returns)


async def _bash_description_the_model_sees(mode: str, monkeypatch, cwd: Path) -> str:
    """Build a real agent in ``mode`` and return the ``bash`` description the model is actually handed."""
    monkeypatch.setattr(bash_mod.settings, "sandbox_mode", mode)
    agent = build_agent()
    captured: dict[str, str | None] = {}

    def model_fn(messages: list[Any], info: AgentInfo) -> ModelResponse:
        captured["bash"] = next(
            (t.description for t in info.function_tools if t.name == _BASH), None
        )
        return ModelResponse(parts=[TextPart(content="ok")])

    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    with agent.override(model=FunctionModel(model_fn)):
        await agent.run("hi", deps=deps)
    description = captured["bash"]
    assert description is not None  # the build persona exposes bash
    return description


async def test_bash_description_adapts_per_mode(monkeypatch, tmp_path: Path) -> None:
    """The model-facing ``bash`` description: ``none`` is the base; ``docker`` / ``modal`` append a paragraph.

    Capturing the description the model actually receives proves the end-to-end wiring (the registry
    ``prepare`` → the model schema, ADR-0011 §4). ``docker``/``modal`` == ``none`` + their suffix
    transitively proves the ``none``-mode description is byte-identical to the untouched base (no sandbox
    paragraph leaks into ``none``), and each sandbox paragraph states that mode's live semantics.
    """
    none_desc = await _bash_description_the_model_sees("none", monkeypatch, tmp_path)
    docker_desc = await _bash_description_the_model_sees("docker", monkeypatch, tmp_path)
    modal_desc = await _bash_description_the_model_sees("modal", monkeypatch, tmp_path)

    # none carries NO sandbox paragraph (byte-identical to the base description) ...
    assert "/workspace" not in none_desc
    assert "SANDBOX_MODE" not in none_desc
    # ... docker / modal are exactly none + their sandbox-semantics paragraph ...
    assert docker_desc == f"{none_desc}\n\n{bash_mod._DOCKER_DESCRIPTION_SUFFIX}"
    assert modal_desc == f"{none_desc}\n\n{bash_mod._MODAL_DESCRIPTION_SUFFIX}"
    # ... and each paragraph tells the model that mode's reality.
    assert "persistent bash shell" in docker_desc  # docker's cd/export-persist shell
    assert "remote Modal sandbox" in modal_desc and "NOT present" in modal_desc  # empty scratch
    assert ".decode/skills" in modal_desc  # ...except the seeded skills dir (the model is told)


# ================================================================================================
# 3. REPL kitaru/sandbox-free — the ``none`` path imports no sandbox executor module, and importing
#    ``decode.cli`` (the REPL entrypoint) imports no kitaru. Fresh interpreters keep it honest.
# ================================================================================================


def _run_isolated(code: str, *, mode: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter with a pinned ``SANDBOX_MODE`` (a clean ``sys.modules``)."""
    env = {
        **os.environ,
        "SANDBOX_MODE": mode,
        "GEMINI_API_KEY": "test-key",
        "LLM_PROVIDER": "gemini",
        "DECODE_LOG_FILE": "",
    }
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)


def test_none_mode_agent_imports_no_sandbox_executor_module() -> None:
    """Building + selecting on the ``none`` path pulls in NEITHER the docker nor the modal executor module.

    The ADR-0011 §4 laziness guarantee: ``none`` mode keeps the eager host executor and never imports
    :mod:`decode.sandbox` at all, so the default REPL pays for no sandbox backend. A fresh interpreter
    proves it regardless of what the rest of the suite already imported.
    """
    code = (
        "import sys; "
        "import decode.tools.bash as b; "
        "from decode.agent.factory import build_agent; "
        "build_agent(); "
        "b._get_executor(); "  # selects the none-mode executor (must not import the sandbox pkg)
        "leaked = [m for m in "
        "('decode.sandbox.docker_executor', 'decode.sandbox.modal_executor') if m in sys.modules]; "
        "assert not leaked, leaked; "
        "print('OK')"
    )
    result = _run_isolated(code, mode="none")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_the_cli_imports_no_kitaru() -> None:
    """The REPL entrypoint stays kitaru-free: the Credential Proxy is headless + docker only (ADR-0011 §6).

    Importing :mod:`decode.cli` must not pull in ``kitaru`` (or its heavy zenml stack) — only ``decode
    run`` does, lazily, inside the subcommand body; the sandbox Credential Proxy is likewise imported
    only inside the headless flow. A subprocess keeps the check honest.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import decode.cli, sys; assert 'kitaru' not in sys.modules"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ================================================================================================
# 4. Credential map — host-side ``{{ name.key }}`` resolution via a patched ``kitaru.get_secret``; the
#    shipped rules are empty (a passthrough map); no resolved value is ever logged. All hermetic.
# ================================================================================================

# The resolved secret value the tests inject — the string that must NEVER appear in a log line.
_CRED_SECRET = "ghp_capstone_secret_token_value"


def _patch_get_secret(mocker, values_by_name: dict[str, dict[str, str]]):
    """Patch ``kitaru.get_secret`` to return a fake ``Secret`` (``.values``) per name (no real store)."""

    def _fake(name: str):
        return SimpleNamespace(values=values_by_name[name])

    return mocker.patch("kitaru.get_secret", side_effect=_fake)


def test_build_credential_map_resolves_templates_hermetically(mocker) -> None:
    """``build_credential_map`` resolves a ``{{ name.key }}`` header template into the ``{host:{header:value}}`` map.

    The host-side half of the Credential Proxy (ADR-0011 §6): a rule's template is resolved via a
    **patched** :func:`kitaru.get_secret`, so no real secret store is touched — the map the proxy
    container will consume is built entirely offline.
    """
    _patch_get_secret(mocker, {"github-token": {"value": _CRED_SECRET}})
    rules = [
        SandboxProxyRule(
            name="github-auth",
            hosts=["api.github.com"],
            headers={"Authorization": "Bearer {{ github-token.value }}"},
        )
    ]

    assert build_credential_map(rules) == {
        "api.github.com": {"Authorization": f"Bearer {_CRED_SECRET}"}
    }


def test_default_proxy_rules_ship_empty_and_yield_a_passthrough_map(mocker) -> None:
    """The shipped rule set is empty (opt-in) → an empty credential map (a passthrough proxy, no secrets)."""
    spy = _patch_get_secret(mocker, {})

    assert DEFAULT_PROXY_RULES == []
    assert build_credential_map(DEFAULT_PROXY_RULES) == {}
    spy.assert_not_called()  # an empty rule set fetches no secret


def test_build_credential_map_logs_names_never_values(mocker, caplog) -> None:
    """The resolved secret value never reaches a log line — only rule / host / header NAMES (task-061 discipline).

    So an operator can correlate an injection from the ``[sandbox]`` logs without the secret ever leaking
    into them — the names-not-values guarantee the credential claim rests on (ADR-0011 §6, Consequences).
    """
    _patch_get_secret(mocker, {"github-token": {"value": _CRED_SECRET}})
    rules = [
        SandboxProxyRule(
            name="github-auth",
            hosts=["api.github.com"],
            headers={"Authorization": "Bearer {{ github-token.value }}"},
        )
    ]

    with caplog.at_level(logging.DEBUG, logger="decode.sandbox.proxy"):
        build_credential_map(rules)

    assert _CRED_SECRET not in caplog.text  # the resolved value never appears
    assert "github-auth" in caplog.text  # the rule name does (for correlation)
    assert "Authorization" in caplog.text  # the header name does


# ================================================================================================
# 5. Replay-safety config — with ``SANDBOX_MODE != none`` the bypass ``_build_runtime_agent`` builds the
#    ``KitaruAgent`` with the verified ``{"cache": False}`` bash checkpoint config (re-execute on replay,
#    ADR-0011 §5); ``none`` mode is byte-identical (no such kwarg). Spied — no flow is booted.
# ================================================================================================


def _spy_runtime_agent_kwargs(monkeypatch, *, mode: str) -> dict[str, Any]:
    """Call ``flow._build_runtime_agent`` with a spied ``KitaruAgent`` and return its kwargs (no stack).

    Patches the flow module's ``build_agent`` (→ a sentinel, so no real agent / key) and ``KitaruAgent``
    (→ a spy that records its kwargs), then invokes the real seam under the given ``SANDBOX_MODE``. Proves
    the replay-safety wiring precisely without constructing a real durable agent or booting a flow.
    """
    import decode.runtime.flow as flow_mod

    captured: dict[str, Any] = {}

    def _fake_kitaru_agent(agent: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(agent=agent, kwargs=kwargs)

    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", mode)
    monkeypatch.setattr(
        flow_mod, "build_agent", lambda flow_mode=True, model=None: SimpleNamespace()
    )
    monkeypatch.setattr(flow_mod, "KitaruAgent", _fake_kitaru_agent)

    flow_mod._build_runtime_agent()
    return captured


def test_sandbox_bypass_agent_gets_the_cache_false_bash_checkpoint(monkeypatch) -> None:
    """``docker`` mode: the bypass durable agent re-executes ``bash`` on replay (``{"cache": False}``).

    ADR-0011 §5: a sandbox ``bash`` has real shell side effects, so a ``decode replay`` must **re-run** it
    rather than serve a stale, side-effect-free cached turn. :func:`decode.runtime.flow._build_runtime_agent`
    therefore builds the ``KitaruAgent`` with ``tool_checkpoint_config_by_name={bash: {"cache": False}}``
    (the verified shape that KEEPS the per-call checkpoint but disables its cache) whenever
    ``SANDBOX_MODE != none``.
    """
    import decode.runtime.flow as flow_mod

    captured = _spy_runtime_agent_kwargs(monkeypatch, mode="docker")

    assert captured["tool_checkpoint_config_by_name"] == {_BASH: {"cache": False}}
    assert captured["name"] == flow_mod.RUNTIME_AGENT_NAME
    assert captured["checkpoint_strategy"] == "calls"  # the replay-ready default (settings)


def test_none_mode_bypass_agent_is_byte_identical_without_the_replay_safety_kwarg(
    monkeypatch,
) -> None:
    """``none`` mode: the bypass durable agent build carries NO replay-safety kwarg (byte-identical to 070).

    The default path is untouched by the sandbox replay-safety wiring — no ``tool_checkpoint_config_by_name``
    is passed — so a non-sandbox ``decode run`` / ``replay`` behaves exactly as before ADR-0011.
    """
    captured = _spy_runtime_agent_kwargs(monkeypatch, mode="none")

    assert "tool_checkpoint_config_by_name" not in captured


# ================================================================================================
# PART 2 — skipif-guarded real-infra smokes. Each SKIPS (never fails) when its infra is absent, using
# the SAME predicates as the executors' own integration tests (a ``docker info`` probe; the ``modal``
# credential-presence check). Every test reaps its container / sandbox / network in a ``finally``.
# ================================================================================================


def _docker_available() -> bool:
    """True if a local docker daemon answers a fast ``docker info`` probe (else the real-docker tests SKIP)."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5.0, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _modal_credentials_present() -> bool:
    """True if modal account credentials are present (the task-071 predicate; else the real-modal test SKIPS).

    Presence only, no network call and no ``modal`` import: the ``MODAL_TOKEN_ID`` + ``MODAL_TOKEN_SECRET``
    pair in the environment, or a ``~/.modal.toml`` written by ``modal token set``.
    """
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return (Path.home() / ".modal.toml").exists()


_DOCKER_AVAILABLE = _docker_available()
_MODAL_AVAILABLE = _modal_credentials_present()

# A real remote-sandbox cold start (image pull + spawn) can take a while; give each modal command room.
_MODAL_TIMEOUT_S = 120.0


def _container_exists(name_or_id: str) -> bool:
    """True while the daemon still lists a container matching ``name_or_id`` by id OR by name.

    Two separate ``docker ps`` probes on purpose: docker **ANDs** distinct filter types, so the old
    single call (``--filter name=X --filter id=X``) could never match a full container id (an
    auto-generated *name* never contains the hex id) — which made the id-keyed teardown asserts
    vacuously true. Probing each filter type on its own restores the intended OR.
    """
    for key in ("id", "name"):
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"{key}={name_or_id}"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
        if result.stdout.strip():
            return True
    return False


def _network_exists(name: str) -> bool:
    """True while the daemon still lists a network named ``name`` (used to prove proxy-network teardown)."""
    result = subprocess.run(
        ["docker", "network", "ls", "-q", "--filter", f"name={name}"],
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    return bool(result.stdout.strip())


def _wait_until_gone(name_or_id: str, timeout_s: float = 5.0) -> bool:
    """Poll (bounded) until the container is no longer listed; return whether it is gone."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _container_exists(name_or_id):
            return True
        time.sleep(0.2)
    return not _container_exists(name_or_id)


@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="the docker daemon is not reachable")
async def test_real_docker_persistent_shell_contract(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The real Docker sandbox contract against a live daemon — else SKIP (ADR-0011 §2).

    One session-persistent container + one persistent bash shell: (1) ``cd`` / ``export`` persist across
    two ``run`` calls; (2) a timeout kills+resets the shell and SAYS so (the ``note``), clearing env and
    resetting cwd to ``/workspace``; (3) ``aclose`` removes the container (no leak); (4) the ``[sandbox]``
    observability lines are emitted. Reaps the container in a ``finally`` so the suite stays hermetic even
    on failure.
    """
    # A minimal project cwd: its .decode/sandbox scratch backs /workspace and its .decode/skills
    # must appear read-only in the container (the project tree itself must NOT be mounted).
    (tmp_path / ".decode" / "skills" / "demo").mkdir(parents=True)
    (tmp_path / ".decode" / "skills" / "demo" / "SKILL.md").write_text(
        "# demo skill\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    executor = DockerExecutor()
    container_id: str | None = None
    try:
        with caplog.at_level(logging.DEBUG, logger="decode.sandbox.docker_executor"):
            # 0. Eager warm-up (the REPL launch path): start() brings the container up BEFORE any
            #    command — visible in ``docker ps`` from launch — and the first run() reuses it.
            await executor.start(tmp_path)
            warmed_id = executor._container_id
            assert warmed_id is not None
            assert _container_exists(warmed_id)

            # 0b. Mount semantics (ADR-0011 §2 amended): /workspace IS the host scratch — a file
            #     written in the container lands in <cwd>/.decode/sandbox on the host; the skills
            #     mount is visible but read-only; the project tree itself is NOT in the container.
            await executor.run("echo scratched > probe.txt", cwd=tmp_path, timeout_s=30.0)
            host_probe = tmp_path / ".decode" / "sandbox" / "probe.txt"
            assert host_probe.read_text(encoding="utf-8").strip() == "scratched"
            seeded = await executor.run(
                "cat .decode/skills/demo/SKILL.md", cwd=tmp_path, timeout_s=30.0
            )
            assert seeded.exit_code == 0
            assert "# demo skill" in seeded.stdout
            read_only = await executor.run(
                "touch .decode/skills/blocked 2>&1", cwd=tmp_path, timeout_s=30.0
            )
            assert read_only.exit_code != 0  # the skills mount is read-only
            no_tree = await executor.run("ls pyproject.toml", cwd=tmp_path, timeout_s=30.0)
            assert no_tree.exit_code != 0  # the project tree is out of bash's reach

            # 1. Persistent shell: state written in one run() survives into the next.
            await executor.run("export CAP=persisted && cd /tmp", cwd=tmp_path, timeout_s=30.0)
            persisted = await executor.run("echo $CAP && pwd", cwd=tmp_path, timeout_s=30.0)
            assert "persisted" in persisted.stdout
            assert "/tmp" in persisted.stdout
            assert persisted.exit_code == 0
            assert persisted.note == ""  # a normal command carries no out-of-band note
            container_id = executor._container_id
            assert container_id is not None
            assert container_id == warmed_id  # the warmed container, reused — not a second one

            # 2. Timeout kills + resets the shell and tells the model; env cleared, cwd back to /workspace.
            timed = await executor.run("sleep 100", cwd=tmp_path, timeout_s=1.0)
            assert timed.timed_out is True
            assert "reset" in timed.note.lower()
            after = await executor.run("echo [$CAP] && pwd", cwd=tmp_path, timeout_s=30.0)
            assert "[]" in after.stdout  # CAP is gone — the respawned shell cleared the env
            assert "/workspace" in after.stdout  # cwd reset to the container workdir
            assert after.timed_out is False

            # 3. aclose stops + removes the session container (captured, so the stop line is asserted).
            await executor.aclose()

        # 4. Observability: container start (id + image) and each command's exit + byte count.
        text = caplog.text
        assert f"[sandbox] docker start {container_id}" in text
        assert "image=python:3.12-slim" in text
        assert "exit=0" in text
        assert "bytes=" in text
        assert f"[sandbox] docker stop {container_id}" in text
        assert _wait_until_gone(container_id), "aclose() must stop and remove the session container"
    finally:
        await executor.aclose()  # idempotent safety net (a no-op if already closed) — no leak


@pytest.mark.skipif(not _MODAL_AVAILABLE, reason="modal account credentials are not present")
async def test_real_modal_remote_scratch_contract(tmp_path: Path) -> None:
    """The real Modal sandbox contract against a live account — else SKIP (ADR-0011 §3).

    One session-persistent remote ``modal.Sandbox``: (1) a file written in one ``run`` is readable
    in the next (fs persists across execs); (2) the project's ``.decode/skills/`` is **seeded** at
    ``/workspace/.decode/skills`` (the live acceptance gate for ``add_local_dir`` on
    ``Sandbox.create``) while everything else stays **absent** (no local-tree sync); (3) a timeout
    kills the exec but NOT the sandbox (the fs survives, no ``note``); (4) ``aclose`` terminates
    the sandbox (no leaked remote sandbox). Lean — a few cents of Modal compute — and reaps the
    sandbox in a ``finally``.
    """
    # A minimal project cwd whose .decode/skills/ must be seeded into the remote /workspace —
    # and whose OTHER content (pyproject.toml) must NOT be.
    project = tmp_path / "project"
    (project / ".decode" / "skills" / "demo").mkdir(parents=True)
    (project / ".decode" / "skills" / "demo" / "SKILL.md").write_text(
        "# demo skill\n", encoding="utf-8"
    )
    (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    executor = ModalExecutor()
    try:
        # 1. Filesystem persists across run()s (one sandbox).
        await executor.run("echo data > /workspace/f.txt", cwd=project, timeout_s=_MODAL_TIMEOUT_S)
        readback = await executor.run(
            "cat /workspace/f.txt", cwd=project, timeout_s=_MODAL_TIMEOUT_S
        )
        assert readback.stdout.strip() == "data"
        assert readback.exit_code == 0

        # 2a. The skills seed made it: the cwd-relative skill path resolves inside /workspace.
        seeded = await executor.run(
            "cat .decode/skills/demo/SKILL.md", cwd=project, timeout_s=_MODAL_TIMEOUT_S
        )
        assert seeded.exit_code == 0
        assert "# demo skill" in seeded.stdout

        # 2b. Everything else stays absent: a host file (pyproject.toml) is NOT present.
        host_file = await executor.run("ls pyproject.toml", cwd=project, timeout_s=_MODAL_TIMEOUT_S)
        assert host_file.exit_code != 0
        assert host_file.timed_out is False

        # 3. A per-exec timeout kills the command but leaves the sandbox (and its fs) alive.
        timed = await executor.run("sleep 100", cwd=project, timeout_s=1.0)
        assert timed.timed_out is True
        assert timed.note == ""  # unlike docker, no session reset — the sandbox persists
        alive = await executor.run("echo alive", cwd=project, timeout_s=_MODAL_TIMEOUT_S)
        assert alive.stdout.strip() == "alive"
        assert alive.exit_code == 0
    finally:
        await executor.aclose()  # terminate the remote sandbox (no leak)

    # 4. aclose terminated + cleared the sandbox (a later run would create a fresh one).
    assert executor._sandbox is None


# --- The real docker Credential-Proxy boundary (a lean slice of the 075 topology) --------------

_PROXY_SECRET = "capstone-proxy-secret-4b2a91"
_PROXY_HEADER = "X-Decode-Proxy-Auth"
_PROXY_UPSTREAM_ALIAS = "upstream.local"

# A stub upstream that echoes every request header back in the body, so the worker can prove which
# headers actually ARRIVED. Stdlib only (runs in ``python:3.12-slim``); binds port 80.
_UPSTREAM_SERVER = (
    "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
    "class H(BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        body=''.join(f'{k}: {v}\\n' for k,v in self.headers.items()).encode()\n"
    "        self.send_response(200); self.send_header('Content-Length', str(len(body)))\n"
    "        self.end_headers(); self.wfile.write(body)\n"
    "    def log_message(self,*a): pass\n"
    "HTTPServer(('0.0.0.0',80),H).serve_forever()\n"
)
# The worker's outbound probe — python/urllib (slim has no curl); reads its ``http_proxy`` env.
_REQUEST_SCRIPT = (
    "import urllib.request\n"
    f"print(urllib.request.urlopen('http://{_PROXY_UPSTREAM_ALIAS}/', timeout=10).read().decode())\n"
)


def _wait_tcp_ready(container: str, port: int, timeout_s: float = 15.0) -> None:
    """Poll (bounded) until a server inside ``container`` accepts a TCP connection on ``port``."""
    probe = f"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=1).close()"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, "python3", "-c", probe],
            capture_output=True,
            timeout=10.0,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.2)
    raise AssertionError(f"{container} port {port} never became ready")


def _start_upstream(network: str) -> str:
    """Start the header-echoing stub upstream on ``network`` (alias ``upstream.local``); return its name."""
    name = f"decode-cap-upstream-{uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            _PROXY_UPSTREAM_ALIAS,
            "python:3.12-slim",
            "python3",
            "-c",
            _UPSTREAM_SERVER,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60.0,
    )
    _wait_tcp_ready(name, 80)
    return name


def _stop_container(name: str) -> None:
    subprocess.run(["docker", "stop", "--time", "2", name], capture_output=True, timeout=30.0)


@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="the docker daemon is not reachable")
async def test_real_docker_credential_proxy_boundary(monkeypatch, tmp_path: Path) -> None:
    """The credential boundary, proven end to end against a real daemon — else SKIP (ADR-0011 §6).

    A lean slice of the 075 topology: a rule injects ``X-Decode-Proxy-Auth: <secret>`` on requests to the
    stub upstream, resolved host-side from a **patched** Kitaru secret. A token-free proxy-wired
    :class:`~decode.sandbox.docker_executor.DockerExecutor` worker makes a urllib request through the
    ``mitmproxy`` addon container; the upstream echoes the headers it received — proving the header
    **ARRIVED** — while a scan of the worker container's own env proves the secret is **absent** there (it
    lives only in the proxy container). Everything is torn down in a ``finally`` and asserted gone, so the
    suite leaves no docker litter even on failure.
    """
    monkeypatch.setattr(
        "kitaru.get_secret", lambda name: SimpleNamespace(values={"token": _PROXY_SECRET})
    )
    credential_map = build_credential_map(
        [
            SandboxProxyRule(
                name="upstream-auth",
                hosts=[_PROXY_UPSTREAM_ALIAS],
                headers={_PROXY_HEADER: "{{ test-secret.token }}"},
            )
        ]
    )
    proxy = DockerCredentialProxy(credential_map)
    executor: DockerExecutor | None = None
    upstream: str | None = None
    worker_id: str | None = None
    try:
        proxy.start()
        upstream = _start_upstream(proxy.network)
        # The worker's /workspace is the project's .decode/sandbox scratch (ADR-0011 §2 amended),
        # NOT the cwd itself — drop the probe script where the container will actually see it.
        scratch = tmp_path / ".decode" / "sandbox"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "req.py").write_text(_REQUEST_SCRIPT, encoding="utf-8")
        executor = DockerExecutor(
            network=proxy.network,
            proxy_env=proxy.worker_proxy_env,
            ca_cert_host_path=proxy.ca_cert_host_path,
        )

        result = await executor.run("python3 /workspace/req.py", cwd=tmp_path, timeout_s=30.0)
        worker_id = executor._container_id

        # The upstream echoed the header the proxy injected — it ARRIVED, though the worker never held it.
        assert result.exit_code == 0, result.stdout
        assert f"{_PROXY_HEADER}: {_PROXY_SECRET}" in result.stdout

        # SECURITY: the worker container's own env carries the proxy URL but NOT the secret value.
        env = subprocess.run(
            ["docker", "exec", worker_id, "env"], capture_output=True, text=True, timeout=15.0
        ).stdout
        assert _PROXY_SECRET not in env  # the worker holds no token ...
        assert "http_proxy=" in env  # ... it is merely routed through the proxy
    finally:
        if executor is not None:
            await executor.aclose()
        if upstream is not None:
            _stop_container(upstream)
        proxy.stop()

    # Everything is torn down — no docker litter left behind.
    assert not _container_exists(proxy._container_name)
    assert worker_id is None or not _container_exists(worker_id)
    assert upstream is None or not _container_exists(upstream)
    assert not _network_exists(proxy.network)
