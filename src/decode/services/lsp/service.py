"""The LSP Service surface: a lazy, per-root, cached, best-effort client behind a spawn seam.

One Language Server per project root, spawned on first use via the patchable :func:`_spawn_process`
seam and cached in :data:`_CLIENTS`; a failed spawn caches :data:`_BROKEN` (no retry storm), while
a mid-session crash respawns once. Every op is best-effort: it returns a decode-native value object,
``None`` for "found nothing", or ``UNAVAILABLE`` — never a raw LSP dict, never an exception.
:func:`diagnostics_on_edit` is the sync bridge for the Diagnostics Enricher; :func:`shutdown_all`
the app-exit entry. See ADR-0007.
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
_CLIENTS: dict[Path, LspClient | _Broken] = {}


async def _spawn_process(root: Path) -> asyncio.subprocess.Process:
    """The patchable spawn seam: launch the configured stdio Language Server under ``root``.

    The single real-subprocess boundary — unit tests patch this to inject a fake process.
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
    """Return the cached client for ``cwd``'s root, spawning lazily; ``None`` if unavailable.

    Disabled or ``_BROKEN`` roots return ``None`` without spawning; a dead cached client is
    dropped and respawned once.
    """
    if not settings.lsp_enabled:
        return None
    root = Path(cwd).resolve()
    cached = _CLIENTS.get(root)
    if isinstance(cached, LspClient):
        if cached.is_alive:
            return cached
        del _CLIENTS[root]  # crashed mid-session → drop and respawn below (not _BROKEN forever)
    elif cached is _BROKEN:
        return None
    client = await _start_client(root)
    _CLIENTS[root] = client if client is not None else _BROKEN
    return client


async def _start_client(root: Path) -> LspClient | None:
    """Spawn + handshake one Language Server for ``root``; ``None`` (best-effort) on any failure."""
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

    The sync ``write``/``edit`` tools (anyio worker thread) reach :func:`diagnostics` via
    ``anyio.from_thread.run``. ``None`` on any failure, so the enricher stays silent (ADR-0007 §5).
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
    """Shut every spawned Language Server down (``run_app`` exit path). Idempotent; never raises."""
    for root, client in list(_CLIENTS.items()):
        if isinstance(client, LspClient):
            try:
                await client.shutdown()
            except Exception as exc:
                logger.debug("lsp: shutdown of %s failed: %s", root, exc)
    _CLIENTS.clear()
