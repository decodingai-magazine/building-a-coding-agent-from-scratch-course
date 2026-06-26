"""Shared YAML-frontmatter splitting for the Markdown catalogs (ADR-0003 §5, ADR-0004 §3).

Both the Agents Catalog loader (:mod:`decode.agents.loader`) and the Skills Catalog loader
(:mod:`decode.skills.loader`) read the same on-disk shape: a ``---``-fenced YAML frontmatter block
atop a Markdown file, then the body. :func:`split_frontmatter` is the one place that split lives —
extracted once the skills loader became a genuine second caller (AGENTS.md: abstract on the second
implementation, not before).

Only the *split* is shared. Each loader keeps its own ``_require_str`` because they differ: the skills
loader strips the returned value (so a dispatcher key is exact) while the agents loader returns it
raw — sharing a stripping helper would silently change how the agents catalog parses ``name`` /
``description``.
"""

from __future__ import annotations

# The YAML frontmatter fence — a line that is exactly ``---``.
FENCE = "---"


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split a ``---``-fenced YAML frontmatter block from the body, returning ``(yaml, body)``.

    Raises :class:`ValueError` when the text does not open with a ``---`` fence or the block is never
    closed with a second ``---`` — the caller turns that into a clear per-format error.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        raise ValueError("file must start with a '---' YAML frontmatter block")
    for index in range(1, len(lines)):
        if lines[index].strip() == FENCE:
            frontmatter = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            return frontmatter, body
    raise ValueError("frontmatter block is not closed with a second '---'")
