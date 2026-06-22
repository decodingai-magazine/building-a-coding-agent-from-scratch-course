"""Logging bootstrap.

``init_logger()`` is called at module level in every entrypoint (the ``decode``
CLI, scripts) **before any other project import**, so the first log line — including
anything third-party libraries emit on import — goes through the project's formatter.

Logs go to a **file, off the terminal** (Fix 3): the REPL is the user surface, so
httpx / google-genai / pydantic_ai INFO noise must not clutter it. INFO+ is written to
``<cwd>/.decode/logs/decode.log`` via a plain :class:`logging.FileHandler` (``delay=True``,
so the file is opened lazily on the first emit) and there is **no console handler**.

It deliberately reads its config from environment variables rather than from
``decode.config.settings``: importing settings here would run project code before the logger
is configured, defeating the whole point of bootstrapping logging first.

* ``LOG_LEVEL`` — the root level (default ``INFO``); the ``level=`` argument overrides it.
* ``DECODE_LOG_FILE`` — overrides the log-file path. Set it to the **empty string** to disable
  file logging entirely (a :class:`logging.NullHandler` is installed and no ``.decode/logs/`` dir
  is created) — used by the test suite so it never writes a log file into the repo.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_INITIALIZED = False

# The default log file, relative to the launch cwd, consolidated with the other harness artifacts
# under ``<cwd>/.decode`` (Fix 1/3). Overridable via ``DECODE_LOG_FILE``.
_DEFAULT_LOG_FILE = Path(".decode") / "logs" / "decode.log"


def _resolve_log_handler() -> logging.Handler:
    """Build the single root handler: a file handler, or a NullHandler when disabled.

    ``DECODE_LOG_FILE`` set to the empty string disables file logging (a ``NullHandler``); unset
    falls back to ``<cwd>/.decode/logs/decode.log``; any other value is used verbatim. The parent
    directory is created up-front so the lazy (``delay=True``) open never fails on first write.
    """
    configured = os.environ.get("DECODE_LOG_FILE")
    if configured == "":
        return logging.NullHandler()

    path = Path(configured) if configured else _DEFAULT_LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    return handler


def init_logger(level: str | None = None) -> None:
    """Configure root logging once, writing INFO+ to a file (never the terminal).

    Idempotent: subsequent calls are no-ops, so importing several entrypoints in one process
    (e.g. during tests) does not stack handlers. Pass ``level`` to override the ``LOG_LEVEL`` env
    var (default ``INFO``). The destination is a file under ``<cwd>/.decode/logs/`` (or
    ``DECODE_LOG_FILE``), or nothing when ``DECODE_LOG_FILE`` is the empty string.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return
    resolved = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=resolved,
        handlers=[_resolve_log_handler()],
        force=True,
    )
    _INITIALIZED = True
