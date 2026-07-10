"""Unit tests for the hand-rolled LSP Service (``decode.services.lsp``), per ADR-0007.

The service spawns one stdio Language Server per project root through the patchable
``_spawn_process`` seam. Every test patches that seam to inject a :class:`FakeLanguageServer` (or a
deliberately broken / dead / hanging stand-in) feeding canned ``Content-Length``-framed JSON-RPC
responses — **no real ``ty``, no subprocess, no network**. They prove the wire contract (framing
round-trip, handshake order, the four ops mapped to 1-based decode-native value objects,
match-by-id), the lazy per-root cache (spawn once / broken-spawn cached), and the best-effort posture
(timeout / malformed frame / disabled → ``UNAVAILABLE``, never an exception).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from support.lsp_fakes import DeadProcess, FakeLanguageServer, frame

from decode.config.settings import settings
from decode.services.lsp import service as lsp_service
from decode.services.lsp.client import LspClient
from decode.services.lsp.types import UNAVAILABLE, Diagnostic, Location


def _seed_root(root: Path) -> Path:
    """Create the queried ``pkg/mod.py`` on disk (didOpen reads it) and return its relative path."""
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "mod.py").write_text("def foo() -> int:\n    return 1\n", encoding="utf-8")
    return Path("pkg/mod.py")


def _canned(root: Path) -> dict[str, Any]:
    """Canned LSP results (0-based on the wire) for one root — the four ops + the handshake."""
    other = (root / "pkg" / "other.py").resolve().as_uri()
    mod = (root / "pkg" / "mod.py").resolve().as_uri()
    return {
        "initialize": {"capabilities": {}},
        "textDocument/definition": {
            "uri": other,
            "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 3}},
        },
        "textDocument/references": [
            {"uri": other, "range": {"start": {"line": 2, "character": 0}}},
            {"uri": mod, "range": {"start": {"line": 9, "character": 4}}},
        ],
        "textDocument/hover": {"contents": {"kind": "markdown", "value": "def foo() -> int"}},
        "textDocument/diagnostic": {
            "kind": "full",
            "items": [
                {
                    "range": {"start": {"line": 4, "character": 6}},
                    "severity": 1,
                    "message": "undefined name `bar`",
                },
                {
                    "range": {"start": {"line": 7, "character": 0}},
                    "severity": 2,
                    "message": "unused import `os`",
                },
            ],
        },
    }


def _patch_spawn(mocker, value=None, *, side_effect=None):
    """Patch the spawn seam (``_spawn_process`` is async → auto-AsyncMock); return the mock."""
    spawn = mocker.patch.object(lsp_service, "_spawn_process")
    if side_effect is not None:
        spawn.side_effect = side_effect
    else:
        spawn.return_value = value
    return spawn


def _request_for(fake: FakeLanguageServer, method: str) -> dict[str, Any]:
    return next(req for req in fake.requests if req.get("method") == method)


# framing round-trips in both directions


async def test_framing_roundtrips_both_directions(tmp_path: Path):
    fake = FakeLanguageServer()
    client = LspClient(fake, tmp_path)

    # client → server: a written frame parses back to the exact message the fake decoded.
    await client._notify("ping", {"n": 1})
    assert fake.requests == [{"jsonrpc": "2.0", "method": "ping", "params": {"n": 1}}]

    # server → client: a frame is decoded by its exact Content-Length.
    fake.stdout.feed_data(frame({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}))
    assert await client._read() == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}


# handshake order + the four ops mapped to 1-based decode-native value objects


async def test_definition_handshake_order_and_1based_location(tmp_path: Path, mocker):
    fake = FakeLanguageServer(_canned(tmp_path))
    _patch_spawn(mocker, fake)
    path = _seed_root(tmp_path)

    result = await lsp_service.definition(tmp_path, str(path), line=10, column=5)

    # initialize → initialized → didOpen → definition, in that order on the wire.
    assert fake.received == [
        "initialize",
        "initialized",
        "textDocument/didOpen",
        "textDocument/definition",
    ]
    # 0-based wire (line 2, char 0) → 1-based decode value object (line 3, column 1).
    assert result == Location(path="pkg/other.py", line=3, column=1)


async def test_references_returns_all_locations_with_declaration(tmp_path: Path, mocker):
    fake = FakeLanguageServer(_canned(tmp_path))
    _patch_spawn(mocker, fake)
    path = _seed_root(tmp_path)

    result = await lsp_service.references(tmp_path, str(path), line=1, column=1)

    assert result == [
        Location(path="pkg/other.py", line=3, column=1),
        Location(path="pkg/mod.py", line=10, column=5),
    ]
    # the declaration is requested explicitly.
    assert _request_for(fake, "textDocument/references")["params"]["context"] == {
        "includeDeclaration": True
    }


async def test_hover_returns_text(tmp_path: Path, mocker):
    fake = FakeLanguageServer(_canned(tmp_path))
    _patch_spawn(mocker, fake)
    path = _seed_root(tmp_path)

    result = await lsp_service.hover(tmp_path, str(path), line=1, column=5)

    assert result == "def foo() -> int"


async def test_diagnostics_returns_1based_tuples(tmp_path: Path, mocker):
    fake = FakeLanguageServer(_canned(tmp_path))
    _patch_spawn(mocker, fake)
    path = _seed_root(tmp_path)

    result = await lsp_service.diagnostics(tmp_path, str(path))

    # pull diagnostic: the request is response-matched (no publishDiagnostics handling).
    assert "textDocument/diagnostic" in fake.received
    assert result == [
        Diagnostic(severity=1, line=5, column=7, message="undefined name `bar`"),
        Diagnostic(severity=2, line=8, column=1, message="unused import `os`"),
    ]


async def test_definition_converts_1based_position_to_0based_wire(tmp_path: Path, mocker):
    fake = FakeLanguageServer(_canned(tmp_path))
    _patch_spawn(mocker, fake)
    path = _seed_root(tmp_path)

    await lsp_service.definition(tmp_path, str(path), line=10, column=5)

    # caller speaks 1-based (10, 5); the wire carries 0-based (9, 4).
    assert _request_for(fake, "textDocument/definition")["params"]["position"] == {
        "line": 9,
        "character": 4,
    }


async def test_definition_null_result_is_none_not_unavailable(tmp_path: Path, mocker):
    responses = _canned(tmp_path) | {"textDocument/definition": None}
    _patch_spawn(mocker, FakeLanguageServer(responses))
    path = _seed_root(tmp_path)

    result = await lsp_service.definition(tmp_path, str(path), line=10, column=5)

    # "server answered, found nothing" is None — distinct from UNAVAILABLE ("no answer").
    assert result is None


# responses are matched by JSON-RPC id, not by arrival order


async def test_out_of_order_response_resolves_by_id(tmp_path: Path, mocker):
    # The fake emits a stale wrong-id response BEFORE the real definition response.
    fake = FakeLanguageServer(_canned(tmp_path), decoy_methods=("textDocument/definition",))
    _patch_spawn(mocker, fake)
    path = _seed_root(tmp_path)

    result = await lsp_service.definition(tmp_path, str(path), line=10, column=5)

    # the decoy (id=-999) is skipped; the matching id resolves the right call.
    assert result == Location(path="pkg/other.py", line=3, column=1)


# lazy, cached, one server per root


async def test_same_root_spawns_once(tmp_path: Path, mocker):
    spawn = _patch_spawn(mocker, FakeLanguageServer(_canned(tmp_path)))
    path = _seed_root(tmp_path)

    first = await lsp_service.definition(tmp_path, str(path), line=10, column=5)
    second = await lsp_service.definition(tmp_path, str(path), line=10, column=5)

    assert spawn.call_count == 1  # one server per root, reused
    assert first == second == Location(path="pkg/other.py", line=3, column=1)


async def test_different_roots_spawn_separately(tmp_path: Path, mocker):
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    path_a, path_b = _seed_root(root_a), _seed_root(root_b)
    spawn = _patch_spawn(
        mocker,
        side_effect=[FakeLanguageServer(_canned(root_a)), FakeLanguageServer(_canned(root_b))],
    )

    result_a = await lsp_service.definition(root_a, str(path_a), line=10, column=5)
    result_b = await lsp_service.definition(root_b, str(path_b), line=10, column=5)

    assert spawn.call_count == 2
    assert isinstance(result_a, Location) and isinstance(result_b, Location)
    assert len(lsp_service._CLIENTS) == 2


async def test_crashed_client_is_respawned_not_stuck_unavailable(tmp_path: Path, mocker):
    # A server that handshakes fine and serves one op, then its subprocess dies mid-session. Unlike a
    # never-spawned root (cached _BROKEN forever), a crashed-after-handshake client must recover: the
    # next call drops it and respawns ONCE, rather than failing against the dead pipe all session.
    healthy, replacement = (
        FakeLanguageServer(_canned(tmp_path)),
        FakeLanguageServer(_canned(tmp_path)),
    )
    spawn = _patch_spawn(mocker, side_effect=[healthy, replacement])
    path = _seed_root(tmp_path)

    first = await lsp_service.definition(tmp_path, str(path), line=10, column=5)
    assert isinstance(first, Location) and spawn.call_count == 1  # handshook + cached

    healthy._exit()  # ty crashes: the subprocess exits (returncode set, stdout at EOF)

    second = await lsp_service.definition(tmp_path, str(path), line=10, column=5)

    assert isinstance(second, Location)  # recovered against the fresh server, not UNAVAILABLE
    assert spawn.call_count == 2  # the dead client was dropped and respawned exactly once
    cached = lsp_service._CLIENTS[tmp_path.resolve()]
    assert (
        isinstance(cached, LspClient) and cached._process is replacement
    )  # the fresh client is cached


# best-effort: broken spawn, dead process, timeout, malformed frame, disabled


async def test_broken_spawn_is_cached_no_retry_storm(tmp_path: Path, mocker):
    spawn = _patch_spawn(mocker, side_effect=FileNotFoundError("ty: command not found"))
    path = _seed_root(tmp_path)

    first = await lsp_service.definition(tmp_path, str(path), line=10, column=5)
    second = await lsp_service.definition(tmp_path, str(path), line=10, column=5)

    assert first is UNAVAILABLE and second is UNAVAILABLE  # never raised
    assert spawn.call_count == 1  # the failed spawn is cached; no retry storm


async def test_dead_process_is_cached_unavailable(tmp_path: Path, mocker):
    spawn = _patch_spawn(mocker, side_effect=[DeadProcess(), DeadProcess()])
    path = _seed_root(tmp_path)

    first = await lsp_service.definition(tmp_path, str(path), line=10, column=5)
    second = await lsp_service.definition(tmp_path, str(path), line=10, column=5)

    assert first is UNAVAILABLE and second is UNAVAILABLE
    assert spawn.call_count == 1  # handshake-on-dead-pipe failure is cached as broken


async def test_request_timeout_returns_unavailable(tmp_path: Path, mocker):
    mocker.patch.object(settings, "lsp_request_timeout_s", 0.05)
    fake = FakeLanguageServer(_canned(tmp_path), hang_methods=("textDocument/diagnostic",))
    _patch_spawn(mocker, fake)
    path = _seed_root(tmp_path)

    result = await lsp_service.diagnostics(tmp_path, str(path))

    assert result is UNAVAILABLE  # timed out, best-effort, no exception escaped


async def test_malformed_frame_returns_unavailable(tmp_path: Path, mocker):
    fake = FakeLanguageServer(_canned(tmp_path), malformed_methods=("textDocument/hover",))
    _patch_spawn(mocker, fake)
    path = _seed_root(tmp_path)

    result = await lsp_service.hover(tmp_path, str(path), line=1, column=1)

    assert result is UNAVAILABLE  # bad JSON frame, best-effort, no exception escaped


async def test_disabled_short_circuits_without_spawning(tmp_path: Path, mocker):
    mocker.patch.object(settings, "lsp_enabled", False)
    spawn = _patch_spawn(mocker, FakeLanguageServer(_canned(tmp_path)))
    path = _seed_root(tmp_path)

    result = await lsp_service.definition(tmp_path, str(path), line=10, column=5)

    assert result is UNAVAILABLE
    spawn.assert_not_called()  # lsp_enabled == False never spawns a server


# the sync diagnostics-on-edit bridge


def test_diagnostics_on_edit_disabled_does_not_bridge(tmp_path: Path, mocker):
    mocker.patch.object(settings, "lsp_diagnostics_on_edit", False)
    run = mocker.patch.object(lsp_service.anyio.from_thread, "run")

    assert lsp_service.diagnostics_on_edit(tmp_path, "pkg/mod.py") is None
    run.assert_not_called()  # gated off → no sync→async bridge attempted


def test_diagnostics_on_edit_master_gate_off_does_not_bridge(tmp_path: Path, mocker):
    mocker.patch.object(settings, "lsp_enabled", False)
    run = mocker.patch.object(lsp_service.anyio.from_thread, "run")

    assert lsp_service.diagnostics_on_edit(tmp_path, "pkg/mod.py") is None
    run.assert_not_called()


def test_diagnostics_on_edit_returns_diagnostics(tmp_path: Path, mocker):
    diags = [Diagnostic(severity=1, line=5, column=7, message="undefined name `bar`")]
    mocker.patch.object(lsp_service.anyio.from_thread, "run", return_value=diags)

    assert lsp_service.diagnostics_on_edit(tmp_path, "pkg/mod.py") == diags


def test_diagnostics_on_edit_maps_unavailable_to_none(tmp_path: Path, mocker):
    mocker.patch.object(lsp_service.anyio.from_thread, "run", return_value=UNAVAILABLE)

    assert lsp_service.diagnostics_on_edit(tmp_path, "pkg/mod.py") is None


def test_diagnostics_on_edit_swallows_bridge_failure(tmp_path: Path, mocker):
    # No portal running (e.g. not called from a worker thread) → anyio raises; helper must return None.
    mocker.patch.object(
        lsp_service.anyio.from_thread, "run", side_effect=RuntimeError("no running portal")
    )

    assert lsp_service.diagnostics_on_edit(tmp_path, "pkg/mod.py") is None


# shutdown (app-exit path)


async def test_shutdown_all_terminates_and_clears(tmp_path: Path, mocker):
    fake = FakeLanguageServer(_canned(tmp_path))
    _patch_spawn(mocker, fake)
    path = _seed_root(tmp_path)
    await lsp_service.definition(tmp_path, str(path), line=10, column=5)

    await lsp_service.shutdown_all()

    assert "shutdown" in fake.received and "exit" in fake.received  # graceful shutdown handshake
    assert fake.returncode == 0  # subprocess terminated
    assert lsp_service._CLIENTS == {}  # cache cleared
    await lsp_service.shutdown_all()  # idempotent: a second call is a harmless no-op


async def test_shutdown_all_noop_when_nothing_spawned():
    await lsp_service.shutdown_all()  # never spawned → no error
    assert lsp_service._CLIENTS == {}
