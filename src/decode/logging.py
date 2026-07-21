"""Logging bootstrap.

``init_logger()`` is called at module level in every entrypoint **before any other project
import**, so even third-party import-time log lines use the project formatter. INFO+ goes to a
file under the nearest ``.decode/`` at or above the cwd (``<harness home>/.decode/logs/decode.log``),
never the terminal — the REPL is the user surface.
Config comes from env vars, not ``decode.config.settings`` (importing settings would run project
code before logging is configured): ``LOG_LEVEL`` sets the root level; ``DECODE_LOG_FILE``
overrides the path, and the **empty string** disables file logging entirely (used by the test
suite so it never writes a log file into the repo).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_INITIALIZED = False

# The Harness Home directory name, and the log file inside it; overridable via ``DECODE_LOG_FILE``.
_HARNESS_HOME_DIR = ".decode"
_LOG_FILE_RELATIVE = Path("logs") / "decode.log"


def _default_log_file() -> Path:
    """``<harness home>/.decode/logs/decode.log`` — the NEAREST existing ``.decode``, walking up.

    Not simply ``./.decode/logs/…``: a relative path is resolved against whatever cwd the process
    happens to have, and decode processes do start below the project root — the skill outputs
    default sends work-product to ``.decode/outputs/``, so a script run from there minted a nested
    ``.decode/outputs/.decode/logs/decode.log`` instead of appending to the project's one log.
    Walking up anchors the log to the same Harness Home the rest of the harness artifacts use.

    Falls back to ``./.decode`` when no ancestor has one — the first run in a fresh project, which
    is exactly where creating it IS correct.
    """
    cwd = Path.cwd()
    for directory in (cwd, *cwd.parents):
        if (directory / _HARNESS_HOME_DIR).is_dir():
            return directory / _HARNESS_HOME_DIR / _LOG_FILE_RELATIVE
    return Path(_HARNESS_HOME_DIR) / _LOG_FILE_RELATIVE


def _resolve_log_handler() -> logging.Handler:
    """Build the single root handler: a file handler, or a NullHandler when ``DECODE_LOG_FILE=""``.

    The parent directory is created up-front so the lazy (``delay=True``) open never fails on
    first write.
    """
    configured = os.environ.get("DECODE_LOG_FILE")
    if configured == "":
        return logging.NullHandler()

    path = Path(configured) if configured else _default_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    return handler


def init_logger(level: str | None = None) -> None:
    """Configure root logging once, writing INFO+ to a file (never the terminal).

    Idempotent: subsequent calls are no-ops, so importing several entrypoints in one process does
    not stack handlers. Pass ``level`` to override the ``LOG_LEVEL`` env var (default ``INFO``).
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
