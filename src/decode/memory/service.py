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
deliberately sized; ``MEMORY.md`` (the single harness file ``cwd/.decode/MEMORY.md``, Fix 1) is
**model-maintained** (task 013 appends to it every session) and can grow without bound, so it is
the file that needs a budget. Caps are config-driven
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
from typing import Literal

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

    kept = clip_lines_to_budget(lines, max_lines=max_lines, max_bytes=max_bytes, keep="head")
    note = (
        f"\n\n[memory truncated to {max_lines} lines / {max_bytes} bytes; "
        f"the file continues beyond this point]"
    )
    return kept + note


def clip_lines_to_budget(
    lines: list[str], *, max_lines: int, max_bytes: int, keep: Literal["head", "tail"]
) -> str:
    """Clip ``lines`` to a line AND byte budget, keeping whole lines from one end (ADR-0002 §8).

    The shared core of the two memory budgeters — :func:`_cap` here (``keep="head"``: clip a
    file's leading lines so the model reads the start) and
    :func:`decode.memory.extract.append_session_summary` (``keep="tail"``: drop the oldest
    ``MEMORY.md`` lines so the freshest survive). Both cap by line count first, then drop whole
    lines until the UTF-8 byte length is within ``max_bytes``; a line is never split, and at least
    one whole line is always kept (the truncation note, if any, flags that there is more).

    ``keep`` is the *only* axis they differ on:

    * ``"head"`` keeps the first ``max_lines`` lines and drops from the **tail** to hit the byte
      budget — so the *first* line always survives.
    * ``"tail"`` keeps the last ``max_lines`` lines and drops from the **front** — so the *last*
      line always survives.
    """
    if keep == "head":
        kept = lines[:max_lines]
        while len(kept) > 1 and len("\n".join(kept).encode("utf-8")) > max_bytes:
            kept = kept[:-1]  # drop the oldest-readable trailing line
    else:
        kept = lines[-max_lines:] if max_lines > 0 else lines[-1:]
        while len(kept) > 1 and len("\n".join(kept).encode("utf-8")) > max_bytes:
            kept = kept[1:]  # drop the oldest leading line
    return "\n".join(kept)
