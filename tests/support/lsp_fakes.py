"""An in-memory fake Language Server for the LSP Service unit tests (no real ``ty``, no subprocess).

The service spawns a Language Server through the patchable ``_spawn_process`` seam (mirroring ``web``'s
``_TRANSPORT``). Tests patch that seam to inject :class:`FakeLanguageServer` — a stand-in that parses
the client's ``Content-Length``-framed requests off its stdin and feeds **canned, id-matched** framed
responses back on its stdout. This exercises the *real* framing / handshake / match-by-id wire in both
directions while spawning **no subprocess and touching no network** (AGENTS.md test discipline).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


def frame(message: dict[str, Any]) -> bytes:
    """Encode a JSON message as a ``Content-Length``-framed LSP wire frame."""
    body = json.dumps(message).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _content_length(header: bytes) -> int | None:
    for line in header.split(b"\r\n"):
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


class _FakeStdin:
    """The fake's stdin: collects the client's writes and dispatches each complete frame parsed out."""

    def __init__(self, dispatch) -> None:
        self._dispatch = dispatch
        self._buffer = b""

    def write(self, data: bytes) -> None:
        self._buffer += data
        self._drain_frames()

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    def _drain_frames(self) -> None:
        sep = b"\r\n\r\n"
        while (idx := self._buffer.find(sep)) != -1:
            length = _content_length(self._buffer[:idx])
            start = idx + len(sep)
            if length is None:  # malformed header — drop it so we never loop forever
                self._buffer = self._buffer[start:]
                continue
            if len(self._buffer) < start + length:
                return  # body not fully arrived yet
            body = self._buffer[start : start + length]
            self._buffer = self._buffer[start + length :]
            self._dispatch(json.loads(body))


class FakeLanguageServer:
    """A canned, in-memory stand-in for one ``ty server`` stdio Language Server.

    ``responses`` maps an LSP method name → the JSON-RPC ``result`` to return for that request; a
    method absent from the map answers with ``null``. A mapped value may also be a **callable**
    ``(request_message) -> result`` — a per-request responder the dispatch invokes with the decoded
    request, so a single fake can answer differently per file (e.g. URI-aware diagnostics: errors for
    one ``.py`` URI, clean for another). ``hang_methods`` are never answered (so the client times out),
    ``decoy_methods`` get a wrong-``id`` decoy response emitted *before* the real one (proving
    match-by-id), and ``malformed_methods`` get a deliberately broken frame. The order of
    received method names is recorded in :attr:`received` (and full messages in :attr:`requests`) so a
    test can assert the handshake sequence and inspect a request's params.
    """

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        *,
        hang_methods: tuple[str, ...] = (),
        decoy_methods: tuple[str, ...] = (),
        malformed_methods: tuple[str, ...] = (),
    ) -> None:
        self.responses = responses or {}
        self.hang_methods = set(hang_methods)
        self.decoy_methods = set(decoy_methods)
        self.malformed_methods = set(malformed_methods)
        self.received: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.stdin = _FakeStdin(self._dispatch)
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.returncode: int | None = None

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is not None:
            self.received.append(method)
        self.requests.append(message)
        msg_id = message.get("id")
        if msg_id is None:
            return  # notification: record only, no response
        if method in self.hang_methods:
            return  # never answer → the client's per-request timeout fires
        if method in self.malformed_methods:
            self.stdout.feed_data(b"Content-Length: 8\r\n\r\n{bad json")
            return
        if method in self.decoy_methods:
            # A stale response from a different id, fed FIRST — the client must skip it and match ours.
            self.stdout.feed_data(frame({"jsonrpc": "2.0", "id": -999, "result": {"decoy": True}}))
        result = self.responses.get(method)
        if callable(result):
            result = result(message)  # a per-request responder (e.g. URI-aware diagnostics)
        self.stdout.feed_data(frame({"jsonrpc": "2.0", "id": msg_id, "result": result}))

    # subprocess surface the client's shutdown path drives
    def terminate(self) -> None:
        self._exit()

    def kill(self) -> None:
        self._exit()

    async def wait(self) -> int:
        self._exit()
        return self.returncode  # type: ignore[return-value]

    def _exit(self) -> None:
        if self.returncode is None:
            self.returncode = 0
            self.stdout.feed_eof()


class DeadProcess:
    """A spawned-but-already-dead process: stdout is at EOF, so the first read fails immediately."""

    def __init__(self) -> None:
        self.stdin = _FakeStdin(lambda _message: None)
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.returncode = 1

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    async def wait(self) -> int:
        return self.returncode
