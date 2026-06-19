"""Assemble the project memory block injected into the agent's instructions (ADR-0002 §8).

:func:`assemble_memory` is the bridge between :func:`decode.memory.files.discover_memory_files`
and the dynamic ``@agent.instructions`` hook in :mod:`decode.agent.factory`. It:

#. reads each discovered file (root-most → cwd-most, so the cwd-most has the final word);
#. prefixes each with a **provenance header** — ``# From <absolute path>`` — so the model can
   tell where a rule came from (and a human reading the prompt can too);
#. **caps ``MEMORY.md`` only** at ``settings.memory_max_lines`` lines AND
   ``settings.memory_max_bytes`` bytes, whichever bites first, appending a **visible truncation
   note** so the model knows there is more it cannot see.

Why cap ``MEMORY.md`` but not ``AGENTS.md``: ``AGENTS.md`` is project-authored and trusted to be
deliberately sized; ``MEMORY.md`` is **model-maintained** (task 013 appends to it every session)
and can grow without bound, so it is the file that needs a budget. Caps are config-driven
(``settings.memory_max_*``) — the same single-config-reader rule the rest of the package follows.

**Missing / unreadable files are skipped, not errors.** A file can vanish or become unreadable
between discovery and read (or be a broken symlink); memory is best-effort context, never a hard
dependency, so a bad file is dropped and assembly continues. An empty discovery (or every file
unreadable) returns ``""`` — the factory then injects nothing beyond the static base prompt.

Sync, like discovery: local file reads, sequential tool layer (ADR-0002 §7,10).
"""

from __future__ import annotations

import logging
from pathlib import Path

from decode.config.settings import settings
from decode.memory.files import MEMORY_FILENAMES, discover_memory_files

logger = logging.getLogger(__name__)

# The model-maintained memory file (task 013 writes to it). It is the only discovered file we
# cap, because it is the only one that grows on its own. ``AGENTS.md`` is project-authored.
_CAPPED_FILENAME = "MEMORY.md"

# Sanity check: the file we cap is one we actually discover. Guards against a rename drift
# between this module and ``files.MEMORY_FILENAMES`` silently disabling the cap.
assert _CAPPED_FILENAME in MEMORY_FILENAMES


def assemble_memory(cwd: Path) -> str:
    """Read the discovered memory files and return the prompt block to inject (ADR-0002 §8).

    Files are read root-most → cwd-most (discovery order) and joined with a blank line between
    them, each under a ``# From <abs path>`` provenance header. ``MEMORY.md`` is clipped to the
    configured line/byte budget with a visible truncation note; ``AGENTS.md`` is passed through
    whole. Missing / unreadable files are skipped. Returns ``""`` when nothing readable is found.
    """
    blocks: list[str] = []
    for path in discover_memory_files(cwd):
        content = _read_text(path)
        if content is None:
            continue
        if path.name == _CAPPED_FILENAME:
            content = _cap(content)
        blocks.append(f"# From {path}\n{content}")

    return "\n\n".join(blocks)


def _read_text(path: Path) -> str | None:
    """Read ``path`` as UTF-8, returning ``None`` (not raising) if it cannot be read.

    A file that vanished, turned into a directory, or holds undecodable bytes between discovery
    and read is dropped — memory is best-effort context, never a hard dependency.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("skipping unreadable memory file %s: %s", path, exc)
        return None


def _cap(content: str) -> str:
    """Clip ``content`` to the configured line AND byte budget, with a visible truncation note.

    The cut applies ``settings.memory_max_lines`` first (keep at most that many whole lines),
    then ``settings.memory_max_bytes`` (snap back to the last whole line that fits) — whichever
    bites first. A line is never split. When anything is dropped, a ``[memory truncated …]`` note
    is appended so the model knows the file continues beyond what it can see. Content that fits
    both budgets is returned unchanged (no note).
    """
    max_lines = settings.memory_max_lines
    max_bytes = settings.memory_max_bytes

    lines = content.splitlines()
    fits_lines = len(lines) <= max_lines
    fits_bytes = len(content.encode("utf-8")) <= max_bytes
    if fits_lines and fits_bytes:
        return content

    kept = _clip_to_budget(lines, max_lines=max_lines, max_bytes=max_bytes)
    note = (
        f"\n\n[memory truncated to {max_lines} lines / {max_bytes} bytes; "
        f"the file continues beyond this point]"
    )
    return kept + note


def _clip_to_budget(lines: list[str], *, max_lines: int, max_bytes: int) -> str:
    """Return the head of ``lines`` capped by line count then by byte count (line-aligned).

    Keeps at most ``max_lines`` whole lines, then drops trailing whole lines until the UTF-8
    byte length is within ``max_bytes``. If even the first line alone exceeds ``max_bytes`` we
    still keep that one whole line — the model needs *something* readable and the truncation note
    flags that there is more.
    """
    head = lines[:max_lines]
    while len(head) > 1 and len("\n".join(head).encode("utf-8")) > max_bytes:
        head = head[:-1]
    return "\n".join(head)
