"""LSP capstone (ADR-0007): both LSP channels through the full real stack.

Proves the active ``lsp`` tool AND the passive write/edit Diagnostics Enricher end to end:
real build_agent (flat tool registry + permission seam), real Runner + AgentTurnHandler,
real LSP service cache + hand-rolled JSON-RPC/stdio client (incl. the enricher's sync→async
bridge), real render_event, real SessionLog persist + replay. Swapped/faked: only the
``service._spawn_process`` subprocess boundary, patched to a FakeLanguageServer feeding
canned Content-Length-framed JSON-RPC (GEMINI_API_KEY faked so build_agent constructs) —
no network, no API key, no real subprocess in the hermetic tests.

Capstone 1 drives definition + enricher (errors / clean write / clean edit / non-.py) with an
available fake; capstone 2 fails the spawn seam so both channels degrade without crashing.
The real-``ty`` test is the live half — skipif-guarded on the ``ty`` binary (skipped, never
failed, when absent) — proving the wire against an actual ``ty server``.
"""

from __future__ import annotations

import io
import json
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    RetryPromptPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from rich.console import Console
from support.lsp_fakes import FakeLanguageServer

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.context import session_log
from decode.context.session_log import SessionLog
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Runner
from decode.permissions.gate import PermissionGate
from decode.services.lsp import service as lsp_service
from decode.services.lsp.types import Diagnostic, Location
from decode.tui import render

# --- markers the scripted model streams, so assertions read as a transcript -----------------

# ACTIVE: the model reads a symbol position out of a read/grep and asks where it is defined. The fake
# answers with a canned location under the root; the client maps 0-based wire (2, 0) → 1-based (3, 1).
_DEF_QUERY_PATH = "pkg/mod.py"
_DEF_TARGET_PATH = "pkg/helpers.py"
_DEF_RESULT = f"{_DEF_TARGET_PATH}:3:1"  # what the tool returns to the model

# PASSIVE (errors): a .py write the fake flags with one severity-1 error at 1-based 5:7.
_BUGGY_PATH = "buggy.py"
_BUGGY_BODY = "import os\n\n\ndef broken() -> int:\n    return bar\n"
_BUGGY_BASE = f"Wrote {_BUGGY_PATH!r} ({len(_BUGGY_BODY)} characters)."
_ERROR_MESSAGE = "undefined name `bar`"
# The enricher header is server-named (settings.lsp_server_command == "ty") and errors-only; each
# error line is "  line:column  message" (the Diagnostic is already 1-based).
_DIAGNOSTICS_BLOCK = f"LSP diagnostics (ty) — fix these:\n  5:7  {_ERROR_MESSAGE}"
_BUGGY_RESULT = f"{_BUGGY_BASE}\n\n{_DIAGNOSTICS_BLOCK}"

# PASSIVE (clean): a .py write + a .py edit the fake reports clean for — base strings stay verbatim.
_CLEAN_PATH = "clean.py"
_CLEAN_BODY = "VALUE = 1\n"
_CLEAN_WRITE_BASE = f"Wrote {_CLEAN_PATH!r} ({len(_CLEAN_BODY)} characters)."
_CLEAN_EDIT_OLD = "VALUE = 1"
_CLEAN_EDIT_NEW = "VALUE = 2"
_CLEAN_EDIT_BASE = f"Edited {_CLEAN_PATH!r} (replaced 1 occurrence)."

# NON-.py: a Markdown write — the enricher is .py-only, so it never queries the server.
_DOC_PATH = "notes.md"
_DOC_BODY = "# notes\n"
_DOC_BASE = f"Wrote {_DOC_PATH!r} ({len(_DOC_BODY)} characters)."

_FINAL_TEXT = "lsp capstone step done"

# The real-`ty` fixture: a helper whose call resolves (definition) and an undefined name (error).
_REAL_TY_FIXTURE = "sample.py"
_REAL_TY_SOURCE = (
    "def helper() -> int:\n    return 1\n\n\nresult = helper()\nprint(not_defined_name)\n"
)
_TY_AVAILABLE = shutil.which("ty") is not None


@pytest.fixture(autouse=True)
def _fake_gemini_key(mocker):
    """Let ``build_agent`` construct the Gemini provider offline (the model is overridden)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )


@pytest.fixture(autouse=True)
def _isolate_lsp_cache():
    """Isolate the module-level per-root LSP cache between tests (it persists across calls by design)."""
    lsp_service._CLIENTS.clear()
    yield
    lsp_service._CLIENTS.clear()


# --- the scripted model: one tool call per fresh leg, plain text once the tool returns/retries ----


def _scripted_model(calls: list[DeltaToolCall]) -> FunctionModel:
    """A streaming FunctionModel that fires ``calls[step]`` on each fresh leg, then ends the turn.

    Mirrors the M1 capstone's model: a leg that follows a tool **return** or a **retry** (the
    unavailable ``ModelRetry``) is a resume leg — it streams plain text so the runner reaches its
    would-stop boundary; a fresh leg streams the next scripted tool call and advances the step.
    Streaming (not returning) so the loop's node streamer runs.
    """
    state = {"step": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_is_resume(messages):
            yield _FINAL_TEXT
            return
        step = state["step"]
        state["step"] += 1
        yield {0: calls[step]}

    return FunctionModel(stream_function=stream_function)


def _last_request_is_resume(messages: list[ModelMessage]) -> bool:
    """True when the most recent request carries a tool result OR a retry (i.e. a resume leg)."""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            return any(isinstance(part, ToolReturnPart | RetryPromptPart) for part in message.parts)
    return False


class _ScriptedResolvers:
    """The scripted human: an approve/deny verdict per *asked* call; ``ask_user`` is never used here."""

    def __init__(self, *, permission_verdicts: list[PermissionDecision]):
        self._verdicts = list(permission_verdicts)
        self.permission_requests: list[PermissionRequest] = []

    async def resolve_permission(self, request: PermissionRequest) -> PermissionDecision:
        self.permission_requests.append(request)
        return self._verdicts.pop(0)

    async def resolve_user_question(self, question: str) -> str:  # pragma: no cover - never called
        raise AssertionError("ask_user is not exercised by the lsp capstone")


# --- the URI-aware fake `ty server` -------------------------------------------------------------


def _available_server(root: Path) -> FakeLanguageServer:
    """A fake ``ty server`` that resolves the canned definition and flags only ``buggy.py``.

    ``textDocument/definition`` answers with a fixed location under ``root`` (0-based wire → the tool
    surfaces 1-based ``pkg/helpers.py:3:1``); ``textDocument/diagnostic`` is a per-request responder
    keyed on the requested file URI — one severity-1 error for ``buggy.py``, an empty (clean) report
    for anything else. This is the single boundary the capstone swaps; everything above it is real.
    """
    helpers_uri = (root / _DEF_TARGET_PATH).resolve().as_uri()

    def diagnostic_for(message: dict[str, Any]) -> dict[str, Any]:
        uri = message["params"]["textDocument"]["uri"]
        if uri.endswith(_BUGGY_PATH):
            return {
                "kind": "full",
                "items": [
                    {
                        "range": {"start": {"line": 4, "character": 6}},  # 0-based → 1-based 5:7
                        "severity": 1,
                        "message": _ERROR_MESSAGE,
                    }
                ],
            }
        return {"kind": "full", "items": []}  # clean: no diagnostics

    return FakeLanguageServer(
        {
            "initialize": {"capabilities": {}},
            "textDocument/definition": {
                "uri": helpers_uri,
                "range": {"start": {"line": 2, "character": 0}},
            },
            "textDocument/diagnostic": diagnostic_for,
        }
    )


# --- small history / turn helpers ---------------------------------------------------------------


def _tool_returns(history: list[ModelMessage]) -> list[str]:
    """Every tool-return content string in the conversation (what tools fed back to the model)."""
    return [
        str(part.content)
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _retry_prompts(history: list[ModelMessage]) -> list[str]:
    """Every retry-prompt content string (a ``ModelRetry`` the model was handed back)."""
    return [
        str(part.content)
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    ]


async def _run_turn(runner: Runner, prompt: str) -> None:
    """Submit one prompt and drive the runner to idle (one whole turn)."""
    from decode.tui.app import InputIntent

    await runner.submit(prompt, InputIntent.STEER)
    await runner.wait_idle()


def _build_runner(
    working_dir: Path, sessions_dir: Path, on_event, resolvers
) -> tuple[Runner, AgentTurnHandler, Any]:
    """Wire the real agent + deps + session log + runner around ``working_dir`` (runner, handler, agent)."""
    agent = build_agent()
    deps = AgentDeps(
        cwd=working_dir,
        emit=on_event,
        gate=PermissionGate(),
        resolve_permission=resolvers.resolve_permission,
        resolve_user_question=resolvers.resolve_user_question,
    )
    log = SessionLog.create(
        sessions_dir,
        cwd=working_dir,
        now=datetime(2026, 6, 27, 9, 0, tzinfo=UTC),
        session_id=UUID("00000000-0000-0000-0000-0000000005b0"),
    )
    handler = AgentTurnHandler(agent, deps=deps, session_log=log)
    return Runner(handler, on_event=on_event), handler, agent


# Hermetic capstone 1 — both channels through the real stack with an AVAILABLE fake server.


async def test_lsp_capstone_both_channels_available(tmp_path, mocker):
    """Drive active definition + passive enricher (errors / clean / non-.py) through the real stack."""
    # --- arrange: a real working tree under tmp; the queried .py seeded so didOpen can read it ---
    working_dir = tmp_path / "workspace"
    (working_dir / "pkg").mkdir(parents=True)
    (working_dir / _DEF_QUERY_PATH).write_text("def caller():\n    return helpers()\n", "utf-8")
    sessions_dir = tmp_path / "sessions"

    # Swap ONLY the LSP subprocess boundary: _spawn_process returns the fake (no real ty/subprocess).
    fake = _available_server(working_dir)
    mocker.patch.object(lsp_service, "_spawn_process", return_value=fake)

    # Every event flows through the REAL renderer into a Rich buffer: an unhandled event kind would
    # raise here and fail the turn, so this proves the whole render path end to end.
    render_buffer = io.StringIO()
    console = Console(file=render_buffer, force_terminal=False, width=100)

    def on_event(event: events.Event) -> None:
        console.print(render.render_event(event))

    # Under default mode the read-only ``lsp`` auto-allows (no verdict); the four mutating
    # write/edit/write/write calls are each approved — so four verdicts only, no ``lsp`` among them.
    resolvers = _ScriptedResolvers(permission_verdicts=[PermissionDecision.allow()] * 4)
    calls = [
        DeltaToolCall(
            name="lsp",
            json_args=json.dumps(
                {"op": "definition", "path": _DEF_QUERY_PATH, "line": 2, "column": 12}
            ),
        ),
        DeltaToolCall(
            name="write", json_args=json.dumps({"path": _BUGGY_PATH, "content": _BUGGY_BODY})
        ),
        DeltaToolCall(
            name="write", json_args=json.dumps({"path": _CLEAN_PATH, "content": _CLEAN_BODY})
        ),
        DeltaToolCall(
            name="edit",
            json_args=json.dumps(
                {"path": _CLEAN_PATH, "old_string": _CLEAN_EDIT_OLD, "new_string": _CLEAN_EDIT_NEW}
            ),
        ),
        DeltaToolCall(
            name="write", json_args=json.dumps({"path": _DOC_PATH, "content": _DOC_BODY})
        ),
    ]
    runner, handler, agent = _build_runner(working_dir, sessions_dir, on_event, resolvers)

    # --- act: walk the scripted conversation, one turn per step, through the real model override ---
    with agent.override(model=_scripted_model(calls)):
        await _run_turn(runner, "where is helpers defined?")
        await _run_turn(runner, "write the buggy module")
        await _run_turn(runner, "write the clean module")
        await _run_turn(runner, "tweak the clean module")
        await _run_turn(runner, "write the notes doc")

    returns = _tool_returns(handler.message_history)

    # 1. ACTIVE: the canned definition came back to the model as path:line:column (1-based), and the
    #    read-only ``lsp`` tool auto-allowed — it never reached the human resolver (no prompt).
    assert _DEF_RESULT in returns, "the definition location must reach the model"
    assert "lsp" not in [r.tool_name for r in resolvers.permission_requests], (
        "the read-only lsp tool must auto-allow (no permission prompt)"
    )

    # 2. PASSIVE (errors): the buggy .py write result == the EXACT base string + the errors-only block.
    assert _BUGGY_RESULT in returns, "the buggy write must append the errors-only diagnostics block"
    buggy_return = next(r for r in returns if r.startswith(_BUGGY_BASE))
    assert buggy_return == _BUGGY_RESULT
    assert buggy_return.startswith(_BUGGY_BASE)  # the base substring is byte-for-byte unchanged
    assert (working_dir / _BUGGY_PATH).read_text("utf-8") == _BUGGY_BODY  # the gated write ran

    # 3. PASSIVE (clean, write): a clean .py write result == the base string, unchanged (silent).
    assert _CLEAN_WRITE_BASE in returns, "the clean write result must be the bare base string"

    # 4. PASSIVE (clean, edit): a clean .py edit result == the base string, unchanged (silent).
    assert _CLEAN_EDIT_BASE in returns, "the clean edit result must be the bare base string"
    assert (working_dir / _CLEAN_PATH).read_text("utf-8") == "VALUE = 2\n"  # the edit applied

    # 5. NON-.py: the Markdown write result is the bare base string AND the enricher never queried
    #    the server for it (a .py-only gate — no diagnostic request carries the .md URI).
    assert _DOC_BASE in returns, "the non-.py write result must be the bare base string"
    diagnostic_uris = [
        req["params"]["textDocument"]["uri"]
        for req in fake.requests
        if req.get("method") == "textDocument/diagnostic"
    ]
    assert not any(uri.endswith(_DOC_PATH) for uri in diagnostic_uris), (
        "the enricher is .py-only — a non-.py write must never reach the server"
    )
    # The server WAS queried for the two .py edits (buggy + clean write/edit), proving the real
    # sync→async bridge ran through pydantic-ai's worker thread.
    assert any(uri.endswith(_BUGGY_PATH) for uri in diagnostic_uris)
    assert any(uri.endswith(_CLEAN_PATH) for uri in diagnostic_uris)

    # Only the four mutating calls reached the human resolver, in order; ``lsp`` auto-allowed.
    assert [r.tool_name for r in resolvers.permission_requests] == [
        "write",
        "write",
        "edit",
        "write",
    ]

    # The real renderer ran on every emitted event without raising; the transcript carries the work.
    rendered = render_buffer.getvalue()
    assert _DEF_RESULT in rendered, "the definition result must render in the TUI transcript"
    assert _ERROR_MESSAGE in rendered, "the appended diagnostics must render in the TUI transcript"

    # The session log was written and replays the whole conversation (ADR-0002 §9): header + 5 turns.
    replayed = session_log.load(log_path := handler._session_log.path)
    assert replayed == handler.message_history, "the JSONL log must replay the whole conversation"
    lines = [ln for ln in log_path.read_text("utf-8").splitlines() if ln]
    assert len(lines) == 1 + 5, "header + one append per turn"
    # The replayed history still carries both channels' results (the enricher block survived the round trip).
    assert _BUGGY_RESULT in _tool_returns(replayed)
    assert _DEF_RESULT in _tool_returns(replayed)


# Hermetic capstone 2 — UNAVAILABLE: a failed spawn degrades both channels, the turn never crashes.


async def test_lsp_capstone_unavailable_degrades_gracefully(tmp_path, mocker):
    """With the spawn seam failing (``ty`` missing), the ``lsp`` tool retries and the enricher is silent."""
    working_dir = tmp_path / "workspace"
    working_dir.mkdir()
    (working_dir / _DEF_QUERY_PATH.replace("pkg/", "")).write_text("x = 1\n", "utf-8")
    sessions_dir = tmp_path / "sessions"

    # The spawn fails like ``ty`` not on PATH → best-effort UNAVAILABLE (broken spawn cached per root).
    mocker.patch.object(
        lsp_service, "_spawn_process", side_effect=FileNotFoundError("ty: command not found")
    )

    events_seen: list[events.Event] = []
    console = Console(file=io.StringIO(), force_terminal=False, width=100)

    def on_event(event: events.Event) -> None:
        events_seen.append(event)
        console.print(render.render_event(event))  # the renderer still runs on every event

    resolvers = _ScriptedResolvers(permission_verdicts=[PermissionDecision.allow()])
    calls = [
        DeltaToolCall(
            name="lsp",
            json_args=json.dumps({"op": "definition", "path": "mod.py", "line": 1, "column": 1}),
        ),
        DeltaToolCall(
            name="write", json_args=json.dumps({"path": _BUGGY_PATH, "content": _BUGGY_BODY})
        ),
    ]
    runner, handler, agent = _build_runner(working_dir, sessions_dir, on_event, resolvers)

    with agent.override(model=_scripted_model(calls)):
        await _run_turn(runner, "where is x defined?")  # lsp → unavailable → ModelRetry → turn ends
        await _run_turn(runner, "write the buggy module")  # write → enricher silent → base only

    # The ``lsp`` call came back as a model-readable ``ModelRetry`` (not a crash): the turn completed
    # and the model was told to fall back to read/grep.
    retries = _retry_prompts(handler.message_history)
    assert any("code intelligence is unavailable" in r for r in retries), (
        "the unavailable lsp call must surface a model-readable ModelRetry"
    )

    # The buggy .py write returned JUST the base string — the enricher stayed silent (server unavailable).
    returns = _tool_returns(handler.message_history)
    assert _BUGGY_BASE in returns, "the unavailable enricher must leave the write base string bare"
    assert _BUGGY_RESULT not in returns, "no diagnostics block when the server is unavailable"
    assert (working_dir / _BUGGY_PATH).read_text("utf-8") == _BUGGY_BODY  # the write still ran

    # The turn never crashed — the conversation persisted and replays.
    assert session_log.load(handler._session_log.path) == handler.message_history


# Optional real-`ty` test — proves the hand-rolled wire works against an ACTUAL ``ty server``.


@pytest.mark.skipif(not _TY_AVAILABLE, reason="the `ty` language server binary is not on PATH")
async def test_lsp_capstone_real_ty_wire(tmp_path):
    """Spawn a REAL ``ty server`` against a fixture and assert a real definition + a real error diagnostic."""
    root = tmp_path / "project"
    root.mkdir()
    (root / _REAL_TY_FIXTURE).write_text(_REAL_TY_SOURCE, encoding="utf-8")

    try:
        # `result = helper()` is line 5; the call `helper` starts at 1-based column 10.
        definition = await lsp_service.definition(root, _REAL_TY_FIXTURE, line=5, column=10)
        diagnostics = await lsp_service.diagnostics(root, _REAL_TY_FIXTURE)
    finally:
        await lsp_service.shutdown_all()  # terminate the real subprocess (and clear the cache)

    # A REAL definition: ty resolved the call back to ``def helper`` on line 1 (1-based).
    assert isinstance(definition, Location), f"expected a real Location, got {definition!r}"
    assert definition.path.endswith(_REAL_TY_FIXTURE)
    assert definition.line == 1, "the definition must resolve to the `def helper` line"

    # A REAL error diagnostic: ty flagged the undefined name as a severity-1 error.
    assert isinstance(diagnostics, list), f"expected real diagnostics, got {diagnostics!r}"
    errors = [d for d in diagnostics if isinstance(d, Diagnostic) and d.severity == 1]
    assert errors, "ty must report at least one real error diagnostic for the fixture"
    assert any("not_defined_name" in d.message for d in errors), (
        "the real diagnostic must name the undefined symbol"
    )
