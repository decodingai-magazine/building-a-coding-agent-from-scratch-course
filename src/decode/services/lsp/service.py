"""The LSP Service surface: a lazy, per-root, cached, best-effort client behind a spawn seam (ADR-0007).

This module is the public face the next tasks consume — the ``lsp`` tool (052), the Diagnostics
Enricher (053), and the app-exit path (054) — and it owns the lifecycle the client does not:

* **Lazy, one server per project root, cached** — mirroring ``bash``'s module-level ``_EXECUTOR`` and
  ``web``'s ``_TRANSPORT`` (AGENTS.md): :data:`_CLIENTS` maps a resolved root to its :class:`LspClient`,
  spawned on first use and reused thereafter. A spawn/handshake that **fails caches the
  :data:`_BROKEN` sentinel** so a missing or crashing server is not re-spawned on every edit/tool call
  (no retry storm) — that root stays "unavailable" until the process restarts.
* **A patchable spawn seam** — :func:`_spawn_process` is the one place a real subprocess is created;
  unit tests patch it to inject a fake process with canned framed responses, so the suite never spawns
  real ``ty`` (mirrors ``web``'s ``_TRANSPORT`` test seam).
* **Best-effort everywhere** — every op resolves spawn failure, timeout, closed pipe, or malformed
  frame to :data:`~decode.services.lsp.types.UNAVAILABLE`; **no exception escapes**. ``lsp_enabled ==
  False`` short-circuits to ``UNAVAILABLE`` **without spawning anything**.
* **A sync→async bridge for the enricher** — :func:`diagnostics_on_edit` lets the sync ``write`` /
  ``edit`` tools (run in pydantic-ai's worker thread) reach the async client via
  ``anyio.from_thread.run``, keeping one cache / one client; it is patched out in unit tests.
* **An async shutdown entry** — :func:`shutdown_all` for the ``run_app`` exit path (task 054).

The four ops return a decode-native value object (or ``None`` for "found nothing") on success and the
``UNAVAILABLE`` sentinel when the server could not answer — never a raw LSP dict, never an exception.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import anyio

from decode.config.settings import settings
from decode.services.lsp.client import LspClient
from decode.services.lsp.types import UNAVAILABLE, Diagnostic, Location, Unavailable

logger = logging.getLogger(__name__)


class _Broken:
    """Sentinel cached for a root whose Language Server could not be spawned/initialized."""


# A failed spawn caches this so the server is not re-spawned on every call (no retry storm).
_BROKEN = _Broken()

# Module-level per-root cache — one Language Server per project root, lazily spawned and reused.
# Mirrors bash._EXECUTOR / web._TRANSPORT (AGENTS.md "Sandbox is the one real abstraction" pattern).
_CLIENTS: dict[Path, LspClient | _Broken] = {}


async def _spawn_process(root: Path) -> asyncio.subprocess.Process:
    """The patchable spawn seam: launch the configured stdio Language Server under ``root``.

    This is the single real-subprocess boundary (mirrors ``web``'s ``_TRANSPORT``). Unit tests patch
    **this** to inject a fake process with canned ``Content-Length``-framed responses — no real ``ty``,
    no real subprocess in the suite (AGENTS.md test discipline).
    """
    return await asyncio.create_subprocess_exec(
        settings.lsp_server_command,
        *settings.lsp_server_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=root,
    )


async def _get_client(cwd: Path) -> LspClient | None:
    """Return the cached client for ``cwd``'s root, spawning lazily on first use; ``None`` if unavailable.

    ``lsp_enabled == False`` short-circuits to ``None`` **without spawning**. A previously failed root
    (cached :data:`_BROKEN`) also returns ``None`` without re-spawning (no retry storm).
    """
    if not settings.lsp_enabled:
        return None
    root = Path(cwd).resolve()
    cached = _CLIENTS.get(root)
    if isinstance(cached, LspClient):
        return cached
    if cached is _BROKEN:
        return None
    client = await _start_client(root)
    _CLIENTS[root] = client if client is not None else _BROKEN
    return client


async def _start_client(root: Path) -> LspClient | None:
    """Spawn + handshake one Language Server for ``root``; ``None`` (best-effort) on any failure.

    A spawn error (e.g. ``ty`` not on PATH → ``FileNotFoundError``) or a failed ``initialize``
    handshake (immediate exit, closed pipe, timeout) is logged and resolves to ``None``, which the
    caller caches as :data:`_BROKEN`.
    """
    try:
        process = await _spawn_process(root)
    except Exception as exc:
        logger.warning("lsp: could not spawn %r in %s: %s", settings.lsp_server_command, root, exc)
        return None
    client = LspClient(process, root)
    try:
        await client.initialize()
    except Exception as exc:
        logger.warning("lsp: initialize handshake failed in %s: %s", root, exc)
        await client.shutdown()
        return None
    return client


# --- the four async Code Intelligence ops (best-effort: value object | None | UNAVAILABLE) ------


async def definition(cwd: Path, path: str, line: int, column: int) -> Location | None | Unavailable:
    """Where is the symbol at ``(path, line, column)`` defined? (1-based in and out.)"""
    client = await _get_client(cwd)
    if client is None:
        return UNAVAILABLE
    try:
        return await client.definition(path, line, column)
    except Exception as exc:
        logger.debug("lsp definition failed for %s:%d:%d: %s", path, line, column, exc)
        return UNAVAILABLE


async def references(cwd: Path, path: str, line: int, column: int) -> list[Location] | Unavailable:
    """Who references the symbol at ``(path, line, column)``? (declaration included)."""
    client = await _get_client(cwd)
    if client is None:
        return UNAVAILABLE
    try:
        return await client.references(path, line, column)
    except Exception as exc:
        logger.debug("lsp references failed for %s:%d:%d: %s", path, line, column, exc)
        return UNAVAILABLE


async def hover(cwd: Path, path: str, line: int, column: int) -> str | None | Unavailable:
    """The hover text for the symbol at ``(path, line, column)`` (1-based)."""
    client = await _get_client(cwd)
    if client is None:
        return UNAVAILABLE
    try:
        return await client.hover(path, line, column)
    except Exception as exc:
        logger.debug("lsp hover failed for %s:%d:%d: %s", path, line, column, exc)
        return UNAVAILABLE


async def diagnostics(cwd: Path, path: str) -> list[Diagnostic] | Unavailable:
    """Pull ``path``'s diagnostics (empty list when clean; ``UNAVAILABLE`` if the server can't answer)."""
    client = await _get_client(cwd)
    if client is None:
        return UNAVAILABLE
    try:
        return await client.diagnostics(path)
    except Exception as exc:
        logger.debug("lsp diagnostics failed for %s: %s", path, exc)
        return UNAVAILABLE


# --- sync→async bridge for the passive Diagnostics Enricher (task 053) --------------------------


def diagnostics_on_edit(cwd: Path, path: str) -> list[Diagnostic] | None:
    """**Sync** best-effort diagnostics for the enricher — bridges to the async client. Never raises.

    The ``write`` / ``edit`` tools are sync (pydantic-ai runs them in an anyio worker thread), so this
    helper bridges to the async :func:`diagnostics` via ``anyio.from_thread.run`` (valid from that
    thread), keeping one cache / one client. Returns ``None`` on **any** failure — disabled, no portal,
    timeout, unavailable — so the enricher stays silent (ADR-0007 §5). Unit tests patch this helper.
    """
    if not settings.lsp_enabled or not settings.lsp_diagnostics_on_edit:
        return None
    try:
        result = anyio.from_thread.run(diagnostics, cwd, path)
    except Exception as exc:
        logger.debug("lsp diagnostics-on-edit bridge failed for %s: %s", path, exc)
        return None
    if result is UNAVAILABLE:
        return None
    return result


# --- shutdown (app-exit path, task 054) --------------------------------------------------------


async def shutdown_all() -> None:
    """Shut every spawned Language Server down (``run_app`` exit path). Idempotent; never raises.

    A no-op when nothing was spawned; each client's ``shutdown`` is best-effort, and the cache is
    cleared so a second call (or a fresh session) starts clean.
    """
    for root, client in list(_CLIENTS.items()):
        if isinstance(client, LspClient):
            try:
                await client.shutdown()
            except Exception as exc:
                logger.debug("lsp: shutdown of %s failed: %s", root, exc)
    _CLIENTS.clear()
