"""Discover the project memory files to inject into the prompt (ADR-0002 §8).

``AGENTS.md`` is walked from ``cwd`` up to the filesystem root (inclusive); ``MEMORY.md`` is
the single harness file ``cwd/.decode/MEMORY.md``, not walked. ``CLAUDE.md`` is skipped — it
is the Claude-Code shim that just imports ``AGENTS.md``. Ordering: root-most first, cwd-most
last, harness ``MEMORY.md`` last of all, so the most specific file has the final word
("nearest config wins"). Pure and sync — only file metadata is checked; reading is the
service's job.
"""

from __future__ import annotations

from pathlib import Path

from decode.config.settings import settings

_AGENTS_FILENAME = "AGENTS.md"
_MEMORY_FILENAME = "MEMORY.md"

# Canonical memory-file name set (``CLAUDE.md`` deliberately absent — the Claude-Code shim);
# the assembler's cap sanity check validates against it.
MEMORY_FILENAMES: tuple[str, ...] = (_AGENTS_FILENAME, _MEMORY_FILENAME)


def harness_memory_path(cwd: Path) -> Path:
    """The single harness ``MEMORY.md`` path: ``cwd/.decode/MEMORY.md`` (via ``settings.decode_dir``)."""
    return cwd / settings.decode_dir / _MEMORY_FILENAME


def discover_memory_files(cwd: Path) -> list[Path]:
    """Collect ancestor ``AGENTS.md`` files (root-most first) + ``cwd/.decode/MEMORY.md`` last.

    Only files that exist are returned; empty list when none do.
    """
    # Walk cwd → root, then reverse so the cwd-most AGENTS.md lands last (it wins).
    agents_cwd_first = [
        level / _AGENTS_FILENAME
        for level in _ancestors_inclusive(cwd)
        if (level / _AGENTS_FILENAME).is_file()
    ]
    found: list[Path] = list(reversed(agents_cwd_first))

    # MEMORY.md appended last (most specific); resolved to match the resolved AGENTS.md paths.
    memory = harness_memory_path(cwd).resolve()
    if memory.is_file():
        found.append(memory)

    return found


def _ancestors_inclusive(start: Path) -> list[Path]:
    """``start`` (resolved) and every ancestor up to the filesystem root (``parent == self``)."""
    current = start.resolve()
    chain = [current]
    while current.parent != current:
        current = current.parent
        chain.append(current)
    return chain
