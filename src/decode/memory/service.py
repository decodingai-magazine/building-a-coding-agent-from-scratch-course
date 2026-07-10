"""Assemble the project memory block injected into the agent's instructions (ADR-0002 §8).

:func:`assemble_memory` reads the discovered files (root-most → cwd-most, so the most
specific wins), prefixes each with a ``# From <absolute path>`` provenance header, and caps
``MEMORY.md`` only — it is model-maintained and can grow without bound, while ``AGENTS.md``
is project-authored and trusted. Missing / unreadable files are skipped, never errors —
memory is best-effort context; an empty assembly returns ``""``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from decode.config.settings import settings
from decode.memory.files import MEMORY_FILENAMES, discover_memory_files

logger = logging.getLogger(__name__)

# The model-maintained file — the only one that grows on its own, so the only one capped.
_CAPPED_FILENAME = "MEMORY.md"

# Guards against a rename drift vs ``files.MEMORY_FILENAMES`` silently disabling the cap.
assert _CAPPED_FILENAME in MEMORY_FILENAMES


def assemble_memory(cwd: Path) -> str:
    """Read the discovered memory files and return the prompt block to inject (ADR-0002 §8).

    Each file appears under a ``# From <abs path>`` provenance header; ``MEMORY.md`` is
    clipped to the configured budget with a visible truncation note. Returns ``""`` when
    nothing readable is found.
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
    """Read ``path`` as UTF-8, returning ``None`` (never raising) when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("skipping unreadable memory file %s: %s", path, exc)
        return None


def _cap(content: str) -> str:
    """Clip ``content`` to the configured line AND byte budgets (whichever bites first).

    A line is never split; a visible ``[memory truncated …]`` note is appended when anything
    is dropped. Content that fits both budgets is returned unchanged.
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

    The shared core of the two memory budgeters: ``keep="head"`` keeps the first ``max_lines``
    and drops from the tail to fit ``max_bytes``; ``keep="tail"`` keeps the last ``max_lines``
    and drops from the front. A line is never split; at least one line is always kept.
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
