"""Unit tests for the active ``lsp`` Code Intelligence tool (``decode.tools.lsp``; ADR-0007).

ADR-0007 (the active channel): ``lsp`` is a single READ_ONLY tool with a four-op surface
(``definition`` / ``references`` / ``hover`` / ``diagnostics``) over the task-051 LSP Service. It
auto-allows like ``read`` / ``web_fetch`` (raises :class:`pydantic_ai.ApprovalRequired` until
approved; the gate then auto-allows it by READ_ONLY kind), returns model-readable ``path:line:column``
/ hover / diagnostics strings (1-based), and maps every recoverable problem — unknown op, missing
``line``/``column`` for a position op, an out-of-tree/missing path, and the service reporting
**unavailable** — to a :class:`pydantic_ai.ModelRetry` so the loop never crashes. Crucially, the
service's ``UNAVAILABLE`` ("no answer at all") becomes a retry, while ``None`` / an empty list
("answered, found nothing") becomes the plain ``"no X"`` string — **not** a retry.

**No real ``ty``.** Every test fakes the task-051 service seam by patching the four async ops on the
``decode.services.lsp`` package the tool calls through — no subprocess, no language server — and drives
the tool directly with a hand-built :class:`RunContext` (mirroring ``test_web.py``), plus one run
**through a real agent** proving the auto-allow path end to end.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponseStreamEvent,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import TurnContext
from decode.permissions.gate import PermissionGate
from decode.permissions.types import ToolKind
from decode.services import lsp as lsp_service
from decode.services.lsp import UNAVAILABLE, Diagnostic, Location
from decode.tools import lsp as lsp_module
from decode.tools.askuser import deny_user_question_resolver
from decode.tools.registry import TOOL_KIND, TOOL_SPECS

_POSITION_OPS = ("definition", "references", "hover")


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _ctx(
    cwd: Path,
    *,
    approved: bool = True,
    resolve: Callable[[PermissionRequest], Awaitable[PermissionDecision]] = _deny_resolver,
) -> RunContext[AgentDeps]:
    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=resolve,
        resolve_user_question=deny_user_question_resolver,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=approved)  # type: ignore[arg-type]


def _source_file(cwd: Path, name: str = "mod.py") -> str:
    """Create a real Python file under ``cwd`` (the tool checks the path exists) and return its name."""
    (cwd / name).write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    return name


def _patch_op(mocker, op_name: str, result: object) -> None:
    """Fake the task-051 service seam: ``decode.services.lsp.<op>`` returns ``result`` (no real ``ty``)."""

    async def _fake(*_args: object, **_kwargs: object) -> object:
        return result

    mocker.patch.object(lsp_service, op_name, _fake)


# --- registry classification ----------------------------------------------------------------


def test_lsp_is_registered_read_only():
    # ADR-0007: lsp only reads code intelligence → READ_ONLY, so the gate auto-allows it everywhere.
    assert TOOL_KIND["lsp"] is ToolKind.READ_ONLY
    assert "lsp" in {spec.name for spec in TOOL_SPECS}


# --- gating: lsp auto-allows (defers like read/web_fetch; READ_ONLY → no prompt) -------------


async def test_lsp_requires_approval_when_not_approved(tmp_path: Path, mocker):
    # Like read/web_fetch, lsp raises ApprovalRequired until approved (the gate then auto-allows the
    # READ_ONLY call with no prompt). It must defer BEFORE reaching the service.
    sentinel = mocker.Mock(side_effect=AssertionError("must not reach the service unapproved"))
    mocker.patch.object(lsp_service, "definition", sentinel)
    path = _source_file(tmp_path)

    with pytest.raises(ApprovalRequired):
        await lsp_module.lsp(
            _ctx(tmp_path, approved=False), op="definition", path=path, line=1, column=1
        )
    sentinel.assert_not_called()


# --- definition -----------------------------------------------------------------------------


async def test_lsp_definition_returns_location_one_based(tmp_path: Path, mocker):
    path = _source_file(tmp_path)
    _patch_op(mocker, "definition", Location(path="src/decode/agent/factory.py", line=42, column=1))

    out = await lsp_module.lsp(_ctx(tmp_path), op="definition", path=path, line=2, column=12)

    # The target location is rendered as model-readable 1-based path:line:column.
    assert out == "src/decode/agent/factory.py:42:1"


async def test_lsp_definition_none_returns_no_definition_found(tmp_path: Path, mocker):
    path = _source_file(tmp_path)
    # None means the server ANSWERED but found nothing — a plain string, NOT a ModelRetry.
    _patch_op(mocker, "definition", None)

    out = await lsp_module.lsp(_ctx(tmp_path), op="definition", path=path, line=2, column=12)

    assert out == "no definition found"


# --- references -----------------------------------------------------------------------------


async def test_lsp_references_returns_counted_list_one_based(tmp_path: Path, mocker):
    path = _source_file(tmp_path)
    _patch_op(
        mocker,
        "references",
        [
            Location(path="src/decode/tui/app.py", line=10, column=5),
            Location(path="src/decode/cli.py", line=3, column=9),
        ],
    )

    out = await lsp_module.lsp(_ctx(tmp_path), op="references", path=path, line=2, column=12)

    lines = out.splitlines()
    assert lines[0] == "2 references:"
    assert "src/decode/tui/app.py:10:5" in lines
    assert "src/decode/cli.py:3:9" in lines


async def test_lsp_references_single_match_is_singular(tmp_path: Path, mocker):
    path = _source_file(tmp_path)
    _patch_op(mocker, "references", [Location(path="src/decode/cli.py", line=3, column=9)])

    out = await lsp_module.lsp(_ctx(tmp_path), op="references", path=path, line=2, column=12)

    assert out.splitlines()[0] == "1 reference:"


async def test_lsp_references_empty_returns_no_references_found(tmp_path: Path, mocker):
    path = _source_file(tmp_path)
    _patch_op(mocker, "references", [])

    out = await lsp_module.lsp(_ctx(tmp_path), op="references", path=path, line=2, column=12)

    # An empty list is "found nothing", not "unavailable": a plain string, not a ModelRetry.
    assert out == "no references found"


# --- hover ----------------------------------------------------------------------------------


async def test_lsp_hover_returns_text(tmp_path: Path, mocker):
    path = _source_file(tmp_path)
    _patch_op(mocker, "hover", "def f() -> int")

    out = await lsp_module.lsp(_ctx(tmp_path), op="hover", path=path, line=1, column=5)

    assert out == "def f() -> int"


@pytest.mark.parametrize("empty", [None, ""])
async def test_lsp_hover_empty_returns_no_hover_info(tmp_path: Path, mocker, empty):
    path = _source_file(tmp_path)
    _patch_op(mocker, "hover", empty)

    out = await lsp_module.lsp(_ctx(tmp_path), op="hover", path=path, line=1, column=5)

    assert out == "no hover info"


# --- diagnostics ----------------------------------------------------------------------------


async def test_lsp_diagnostics_returns_compact_list_all_severities(tmp_path: Path, mocker):
    path = _source_file(tmp_path)
    _patch_op(
        mocker,
        "diagnostics",
        [
            Diagnostic(severity=1, line=2, column=4, message="undefined name"),
            Diagnostic(severity=2, line=5, column=1, message="unused import"),
        ],
    )

    out = await lsp_module.lsp(_ctx(tmp_path), op="diagnostics", path=path)

    lines = out.splitlines()
    assert lines[0] == "2 diagnostics:"
    # ALL severities are surfaced here (the tool is the explicit query surface), with readable labels
    # and the queried file as the path of each entry (1-based line:column).
    assert f"error {path}:2:4 undefined name" in lines
    assert f"warning {path}:5:1 unused import" in lines


async def test_lsp_diagnostics_clean_returns_no_diagnostics(tmp_path: Path, mocker):
    path = _source_file(tmp_path)
    _patch_op(mocker, "diagnostics", [])

    out = await lsp_module.lsp(_ctx(tmp_path), op="diagnostics", path=path)

    assert out == "no diagnostics"


async def test_lsp_diagnostics_ignores_line_and_column(tmp_path: Path, mocker):
    # diagnostics needs only a path; a stray line/column is accepted and ignored (not required).
    path = _source_file(tmp_path)
    _patch_op(mocker, "diagnostics", [])

    out = await lsp_module.lsp(_ctx(tmp_path), op="diagnostics", path=path, line=99, column=99)

    assert out == "no diagnostics"


# --- bad arguments → ModelRetry (never a crash) ---------------------------------------------


async def test_lsp_unknown_op_returns_model_retry_listing_ops(tmp_path: Path, mocker):
    path = _source_file(tmp_path)

    with pytest.raises(ModelRetry) as excinfo:
        await lsp_module.lsp(_ctx(tmp_path), op="rename", path=path, line=1, column=1)
    message = str(excinfo.value)
    assert "rename" in message
    for op in ("definition", "references", "hover", "diagnostics"):
        assert op in message


@pytest.mark.parametrize("op", _POSITION_OPS)
@pytest.mark.parametrize(("line", "column"), [(None, 1), (1, None), (None, None)])
async def test_lsp_missing_line_or_column_returns_model_retry(tmp_path: Path, op, line, column):
    # definition/references/hover need BOTH line and column; a missing one is a model-correctable
    # mistake → ModelRetry, never a crash. (No file needed: position is checked before the path.)
    with pytest.raises(ModelRetry) as excinfo:
        await lsp_module.lsp(_ctx(tmp_path), op=op, path="mod.py", line=line, column=column)
    message = str(excinfo.value).lower()
    assert "line" in message and "column" in message


async def test_lsp_out_of_tree_path_returns_model_retry(tmp_path: Path):
    # A path escaping the working directory is refused exactly as the file tools refuse it.
    with pytest.raises(ModelRetry) as excinfo:
        await lsp_module.lsp(_ctx(tmp_path), op="definition", path="../escape.py", line=1, column=1)
    assert "outside" in str(excinfo.value).lower()


async def test_lsp_missing_path_returns_model_retry(tmp_path: Path):
    # A missing in-tree path is "No such file" (a model mistake), NOT "code intelligence unavailable".
    with pytest.raises(ModelRetry) as excinfo:
        await lsp_module.lsp(_ctx(tmp_path), op="diagnostics", path="nope.py")
    assert "no such file" in str(excinfo.value).lower()


# --- the service is unavailable → ModelRetry (distinct from "found nothing") -----------------


@pytest.mark.parametrize(
    ("op", "kwargs"),
    [
        ("definition", {"line": 1, "column": 1}),
        ("references", {"line": 1, "column": 1}),
        ("hover", {"line": 1, "column": 1}),
        ("diagnostics", {}),
    ],
)
async def test_lsp_unavailable_returns_model_retry(tmp_path: Path, mocker, op, kwargs):
    # UNAVAILABLE means "no answer at all" (no server / timeout / broken spawn / lsp_enabled=False):
    # the tool tells the model to fall back to read/grep — a ModelRetry, never an exception.
    path = _source_file(tmp_path)
    _patch_op(mocker, op, UNAVAILABLE)

    with pytest.raises(ModelRetry) as excinfo:
        await lsp_module.lsp(_ctx(tmp_path), op=op, path=path, **kwargs)
    message = str(excinfo.value).lower()
    assert "unavailable" in message
    assert "read" in message and "grep" in message


# --- through a real agent: forced lsp call auto-allows (no prompt) ---------------------------


def _agent(mocker):
    """A real ``decode`` agent built with a dummy key (the model is overridden per test)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


def _already_called(messages: list[ModelMessage]) -> bool:
    """Whether an ``lsp`` tool return is already in the history (the resume leg)."""
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == "lsp"
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def _lsp_then_text(path: str) -> FunctionModel:
    """A FunctionModel that calls ``lsp`` (definition) once with a real position, then settles."""

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[ModelResponseStreamEvent]:
        if _already_called(messages):
            yield "done"
            return
        yield {
            0: DeltaToolCall(
                name="lsp",
                json_args=json.dumps({"op": "definition", "path": path, "line": 1, "column": 5}),
                tool_call_id="c1",
            )
        }

    return FunctionModel(stream_function=stream)


async def test_lsp_auto_allows_and_runs_through_the_agent(tmp_path: Path, mocker):
    """A forced ``lsp`` call is auto-allowed by the gate (READ_ONLY), then actually runs.

    Mirrors the ``web_fetch`` auto-allow test: under DEFAULT mode the gate auto-allows the READ_ONLY
    ``lsp`` call, so it surfaces **no** ``PermissionRequested`` event and never calls the human
    resolver. The whole path runs: the model calls ``lsp`` → it raises ``ApprovalRequired`` → the leg
    resolves to ``DeferredToolRequests`` → the gate auto-allows by mode x kind → ``lsp`` runs on the
    resume leg (against the faked service, no ``ty``) and its result reaches the model as a tool return.
    """
    path = _source_file(tmp_path)
    _patch_op(mocker, "definition", Location(path=path, line=1, column=1))

    emitted: list[events.Event] = []
    resolver_calls: list[PermissionRequest] = []

    async def guard_resolver(request: PermissionRequest) -> PermissionDecision:
        resolver_calls.append(request)  # pragma: no cover - must never run on an auto-allow
        return PermissionDecision.allow()

    deps = AgentDeps(
        cwd=tmp_path,
        emit=emitted.append,
        gate=PermissionGate(),
        resolve_permission=guard_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    agent = _agent(mocker)
    handler = AgentTurnHandler(agent, deps=deps)
    model = _lsp_then_text(path)

    async def _run() -> None:
        import contextlib

        agen = handler(TurnContext(0, "where is f defined?", emitted.append))
        with contextlib.suppress(StopAsyncIteration):
            await agen.asend(None)
            while True:
                await agen.asend([])
        await agen.aclose()

    with agent.override(model=model):
        await _run()

    # The read-only lsp call auto-allowed: no prompt surfaced, the resolver never ran.
    perms = [e for e in emitted if isinstance(e, events.PermissionRequested)]
    assert not perms, "a read-only tool must auto-allow with no PermissionRequested"
    assert resolver_calls == [], "an auto-allowed call must not reach the human resolver"

    # lsp actually executed on the resume leg: its result reached the model as a tool return.
    returns = [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "lsp"
    ]
    assert returns, "the lsp result must reach the model as a tool return"
    assert any(f"{path}:1:1" in r for r in returns)
