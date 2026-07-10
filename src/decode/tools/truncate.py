"""Shared output-truncation helper for tool results (ADR-0002 §7,10).

Model-bound tool output is capped at **2000 lines OR 50 KB, whichever comes first**
(``settings.max_output_lines`` / ``settings.max_output_bytes``), snapping to a **line boundary**
so a line is never cut in half. On overflow the *full* original content is spilled to a temp
file whose path rides back in the result. Deliberately tool-agnostic: text, line/byte counts,
and a spill file only.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Spill-file naming, so a human eyeballing the temp dir can tell what produced these files.
_SPILL_PREFIX = "decode-output-"
_SPILL_SUFFIX = ".txt"


@dataclass(frozen=True, slots=True)
class Truncated:
    """The result of truncating tool output (ADR-0002 §7).

    ``text`` is the model-safe content; ``truncated`` whether anything was dropped;
    ``full_path`` the temp file holding the complete original iff truncation happened.
    """

    text: str
    truncated: bool
    full_path: Path | None = None


def _line_offsets(content: str) -> list[int]:
    """Byte offsets at which each line *ends* — the valid "keep up to here" cut points for the byte cap."""
    offsets: list[int] = []
    cursor = 0
    encoded = content.encode("utf-8")
    while cursor < len(encoded):
        newline = encoded.find(b"\n", cursor)
        if newline == -1:
            offsets.append(len(encoded))
            break
        offsets.append(newline + 1)  # include the newline in the kept slice
        cursor = newline + 1
    return offsets


def truncate(content: str, *, max_lines: int, max_bytes: int) -> Truncated:
    """Cap ``content`` at ``max_lines`` lines OR ``max_bytes`` bytes, whichever comes first.

    The cut always snaps to a line boundary; if even the first line exceeds ``max_bytes`` that
    one whole line is kept regardless. When anything is dropped, the *entire* original is
    written to a temp file named in :class:`Truncated`; no truncation →
    ``Truncated(content, truncated=False, full_path=None)``.
    """
    encoded_len = len(content.encode("utf-8"))
    lines = content.splitlines(keepends=True)
    if len(lines) <= max_lines and encoded_len <= max_bytes:
        return Truncated(text=content, truncated=False, full_path=None)

    kept = _truncate_text(content, lines, max_lines=max_lines, max_bytes=max_bytes)
    spill_path = _spill(content)
    logger.debug(
        "truncated output: %d -> %d bytes (%d lines), full content at %s",
        encoded_len,
        len(kept.encode("utf-8")),
        kept.count("\n"),
        spill_path,
    )
    return Truncated(text=kept, truncated=True, full_path=spill_path)


def _truncate_text(content: str, lines: list[str], *, max_lines: int, max_bytes: int) -> str:
    """Return the head of ``content`` capped by lines and bytes, snapped to a line boundary."""
    # Line cap first: keep at most max_lines whole lines.
    head = "".join(lines[:max_lines])
    if len(head.encode("utf-8")) <= max_bytes:
        return head
    # Byte cap bites harder than the line cap: snap back to the last whole line that fits.
    offsets = _line_offsets(head)
    encoded = head.encode("utf-8")
    cut = 0
    for offset in offsets:
        if offset > max_bytes:
            break
        cut = offset
    if cut == 0:
        # Even the first line overflows the byte cap; keep that one whole line regardless.
        cut = offsets[0]
    return encoded[:cut].decode("utf-8")


def _spill(content: str) -> Path:
    """Write the full ``content`` to a temp file and return its path (kept after close)."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=_SPILL_PREFIX,
        suffix=_SPILL_SUFFIX,
        delete=False,
    ) as handle:
        handle.write(content)
        return Path(handle.name)
