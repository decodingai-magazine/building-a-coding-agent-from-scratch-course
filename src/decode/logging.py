"""Logging bootstrap.

``init_logger()`` is called at module level in every entrypoint (the ``decode``
CLI, scripts) **before any other project import**, so the first log line — including
anything third-party libraries emit on import — goes through the project's formatter.

It deliberately reads the level from the ``LOG_LEVEL`` environment variable rather than
from ``decode.config.settings``: importing settings here would run project code before
the logger is configured, defeating the whole point of bootstrapping logging first.
"""

from __future__ import annotations

import logging
import os

from rich.logging import RichHandler

_INITIALIZED = False


def init_logger(level: str | None = None) -> None:
    """Configure root logging once, with a Rich handler.

    Idempotent: subsequent calls are no-ops, so importing several entrypoints in one
    process (e.g. during tests) does not stack handlers. Pass ``level`` to override the
    ``LOG_LEVEL`` env var (default ``INFO``).
    """
    global _INITIALIZED
    if _INITIALIZED:
        return
    resolved = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=resolved,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
        force=True,
    )
    _INITIALIZED = True
