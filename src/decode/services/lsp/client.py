"""Hand-rolled JSON-RPC-2.0-over-stdio Language Server client — no protocol library.

Owns one spawned stdio server: Content-Length framing, initialize handshake (pull diagnostics),
per-file didOpen/didChange sync (on-disk file is the source of truth), and request/response
matched by JSON-RPC ``id`` (interleaved notifications skipped; no async publishDiagnostics).
Public methods are 1-based line/column; the 0-based wire conversion happens here only. Every
wire failure raises :class:`LspError`, which the service layer maps to ``UNAVAILABLE``.
See ADR-0007 §3.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from decode.config.settings import settings
from decode.services.lsp.types import Diagnostic, Location

logger = logging.getLogger(__name__)

# Grace period after ``shutdown``/``exit`` before the subprocess is force-killed on app exit.
_SHUTDOWN_GRACE_S = 2.0


class LspError(Exception):
    """Any wire-level failure; the service layer maps it to ``UNAVAILABLE`` (ADR-0007 §6)."""


class LspClient:
    """A thin JSON-RPC/stdio client owning one already-spawned Language Server subprocess.

    Not safe for concurrent requests on one instance — the service drives it one at a time.
    """

    def __init__(self, process: Any, root: Path) -> None:
        self._process = process
        self._root = root.resolve()
        self._ids = itertools.count(1)
        # Per-URI document version: first touch is a didOpen (v1), later touches are didChange (v2+).
        self._versions: dict[str, int] = {}

    @property
    def is_alive(self) -> bool:
        """True while the spawned subprocess is still running (its ``returncode`` is unset)."""
        return self._process.returncode is None

    # --- handshake -----------------------------------------------------------------------------

    async def initialize(self) -> None:
        """Run the ``initialize`` → ``initialized`` handshake (raises :class:`LspError` on failure)."""
        await self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self._root.as_uri(),
                "clientInfo": {"name": "decode"},
                "capabilities": {
                    "textDocument": {
                        "definition": {},
                        "references": {},
                        "hover": {},
                        "diagnostic": {"dynamicRegistration": False},
                    }
                },
            },
        )
        await self._notify("initialized", {})

    # --- the four Code Intelligence ops (1-based in and out) -----------------------------------

    async def definition(self, path: str, line: int, column: int) -> Location | None:
        """``textDocument/definition`` — the first target location, or ``None`` if undefined."""
        uri = await self._sync(path)
        result = await self._request("textDocument/definition", _position_params(uri, line, column))
        locations = _parse_locations(result, self._root)
        return locations[0] if locations else None

    async def references(self, path: str, line: int, column: int) -> list[Location]:
        """``textDocument/references`` (declaration included) — every referencing location."""
        uri = await self._sync(path)
        params = _position_params(uri, line, column)
        params["context"] = {"includeDeclaration": True}
        result = await self._request("textDocument/references", params)
        return _parse_locations(result, self._root)

    async def hover(self, path: str, line: int, column: int) -> str | None:
        """``textDocument/hover`` — the hover text, or ``None`` when the server offers none."""
        uri = await self._sync(path)
        result = await self._request("textDocument/hover", _position_params(uri, line, column))
        return _parse_hover(result)

    async def diagnostics(self, path: str) -> list[Diagnostic]:
        """Pull ``textDocument/diagnostic`` — the file's diagnostics (empty when clean)."""
        uri = await self._sync(path)
        result = await self._request("textDocument/diagnostic", {"textDocument": {"uri": uri}})
        return _parse_diagnostics(result)

    # --- per-file sync -------------------------------------------------------------------------

    async def _sync(self, path: str) -> str:
        """Push ``path``'s current on-disk content (didOpen, then didChange) and return its URI."""
        file_path = (self._root / path).resolve()
        uri = file_path.as_uri()
        text = file_path.read_text(encoding="utf-8")
        version = self._versions.get(uri, 0) + 1
        self._versions[uri] = version
        if version == 1:
            await self._notify(
                "textDocument/didOpen",
                {"textDocument": {"uri": uri, "languageId": "python", "version": 1, "text": text}},
            )
        else:
            await self._notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                },
            )
        return uri

    # --- JSON-RPC request/response matched by id -----------------------------------------------

    async def _request(self, method: str, params: Any) -> Any:
        """Send a request and return its ``result``, bounded by ``settings.lsp_request_timeout_s``."""
        req_id = next(self._ids)
        await self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(
                self._read_result(req_id), timeout=settings.lsp_request_timeout_s
            )
        except TimeoutError as exc:
            raise LspError(f"{method} timed out after {settings.lsp_request_timeout_s:g}s") from exc

    async def _read_result(self, req_id: int) -> Any:
        """Read frames until the response matching ``req_id`` arrives, skipping everything else."""
        while True:
            message = await self._read()
            if message.get("id") == req_id and ("result" in message or "error" in message):
                if "error" in message:
                    raise LspError(f"server error: {message['error']}")
                return message["result"]

    async def _notify(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (no ``id``, no response awaited)."""
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    # --- Content-Length framing ----------------------------------------------------------------

    async def _write(self, message: dict[str, Any]) -> None:
        """Write one ``Content-Length``-framed JSON message to the server's stdin."""
        body = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        await self._process.stdin.drain()

    async def _read(self) -> dict[str, Any]:
        """Read one ``Content-Length``-framed JSON message from the server's stdout."""
        length = await self._read_header()
        try:
            body = await self._process.stdout.readexactly(length)
        except asyncio.IncompleteReadError as exc:
            raise LspError("pipe closed mid-message") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise LspError("malformed JSON frame") from exc

    async def _read_header(self) -> int:
        """Read header lines up to the blank separator; return the ``Content-Length`` value."""
        length: int | None = None
        while True:
            line = await self._process.stdout.readline()
            if not line:
                raise LspError("pipe closed")
            stripped = line.strip()
            if not stripped:  # blank line ends the header block
                break
            name, _, value = stripped.partition(b":")
            if name.strip().lower() == b"content-length":
                try:
                    length = int(value.strip())
                except ValueError as exc:
                    raise LspError("invalid Content-Length header") from exc
        if length is None:
            raise LspError("missing Content-Length header")
        return length

    # --- shutdown ------------------------------------------------------------------------------

    async def shutdown(self) -> None:
        """``shutdown`` request → ``exit`` notification → terminate. Idempotent; never raises."""
        try:
            await self._request("shutdown", None)
            await self._notify("exit", None)
        except Exception as exc:
            logger.debug("lsp shutdown handshake failed (terminating anyway): %s", exc)
        await self._terminate()

    async def _terminate(self) -> None:
        """Terminate the subprocess, escalating to a kill if it does not exit within the grace window."""
        process = self._process
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=_SHUTDOWN_GRACE_S)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()


# --- wire → decode-native value object mapping (no raw LSP dicts escape) ------------------------


def _position_params(uri: str, line: int, column: int) -> dict[str, Any]:
    """Build a ``{textDocument, position}`` param, converting 1-based line/column → 0-based wire."""
    return {
        "textDocument": {"uri": uri},
        "position": {"line": max(line - 1, 0), "character": max(column - 1, 0)},
    }


def _parse_locations(result: Any, root: Path) -> list[Location]:
    """Map an LSP ``Location`` / ``LocationLink`` (or array, or ``null``) into 1-based locations."""
    if result is None:
        return []
    items = result if isinstance(result, list) else [result]
    locations: list[Location] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri")
        rng = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange")
        if not uri or not isinstance(rng, dict):
            continue
        start = rng.get("start", {})
        locations.append(
            Location(
                path=_uri_to_path(uri, root),
                line=int(start.get("line", 0)) + 1,
                column=int(start.get("character", 0)) + 1,
            )
        )
    return locations


def _parse_hover(result: Any) -> str | None:
    """Extract hover text from an LSP ``Hover`` (``MarkupContent`` / string / array); ``None`` if empty."""
    if not isinstance(result, dict):
        return None
    return _hover_text(result.get("contents")) or None


def _hover_text(contents: Any) -> str | None:
    """Flatten the several legal ``Hover.contents`` shapes into a single string."""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):  # MarkupContent / MarkedString {language, value}
        value = contents.get("value")
        return value if isinstance(value, str) else None
    if isinstance(contents, list):
        parts = [text for c in contents if (text := _hover_text(c))]
        return "\n".join(parts) if parts else None
    return None


def _parse_diagnostics(result: Any) -> list[Diagnostic]:
    """Map a pull ``DocumentDiagnosticReport`` into 1-based :class:`Diagnostic` value objects."""
    if not isinstance(result, dict):
        return []
    diagnostics: list[Diagnostic] = []
    for item in result.get("items", []):
        if not isinstance(item, dict):
            continue
        rng = item.get("range")
        start = rng.get("start", {}) if isinstance(rng, dict) else {}
        diagnostics.append(
            Diagnostic(
                severity=int(item.get("severity", 1)),
                line=int(start.get("line", 0)) + 1,
                column=int(start.get("character", 0)) + 1,
                message=str(item.get("message", "")),
            )
        )
    return diagnostics


def _uri_to_path(uri: str, root: Path) -> str:
    """Convert a ``file://`` URI to a path — relative to ``root`` when under it, absolute otherwise."""
    path = Path(unquote(urlparse(uri).path))
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
