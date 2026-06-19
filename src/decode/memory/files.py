"""Discover the project memory files to inject into the prompt (ADR-0002 §8).

:func:`discover_memory_files` walks **from ``cwd`` up to the filesystem root**, collecting the
memory files that exist at each level. Two file kinds are memory:

* ``AGENTS.md`` — the project-authored memory file (the canonical name for this project);
* ``MEMORY.md`` — the model-maintained scratch memory (write-back lands in task 013).

``CLAUDE.md`` is **skipped**: it is the Claude-Code shim (it just imports ``AGENTS.md``), so
reading it would duplicate ``AGENTS.md`` into the prompt.

**Stop condition.** The walk climbs ``cwd`` → its parents until it reaches the filesystem root
(``path.parent == path``), inclusive. We stop at the filesystem root rather than a "repo root"
(a ``.git`` marker) deliberately: it is the simplest rule that needs no project-layout
heuristic, it matches the surveyed harnesses' "ancestor walk", and a developer running ``decode``
from outside a git repo still gets their ancestor ``AGENTS.md`` files. In practice the only
memory files on the path are the project's own, so the broader walk costs nothing.

**Ordering — cwd-most wins.** Levels are emitted **root-most first, cwd-most last**, and within a
level ``AGENTS.md`` precedes ``MEMORY.md``. The assembler concatenates in this order, so the
**cwd-most** (most specific) file appears **last** in the prompt and therefore has the final
word — the same "nearest config wins" rule editors and linters use.

**Pure and sync.** Filesystem stats are local and fast and the tool layer runs sequentially in
v1 (ADR-0002 §7,10), so there is no concurrency to win by going async. The function only reads
directory metadata (``Path.is_file``); it never opens a file (that is the service's job).
"""

from __future__ import annotations

from pathlib import Path

# The memory file kinds we inject, in the order they appear within a single level. ``CLAUDE.md``
# is deliberately absent — it is the Claude-Code import shim, not a distinct memory source.
MEMORY_FILENAMES: tuple[str, ...] = ("AGENTS.md", "MEMORY.md")


def discover_memory_files(cwd: Path) -> list[Path]:
    """Collect ``AGENTS.md`` / ``MEMORY.md`` from ``cwd`` up to the filesystem root.

    Walks ``cwd`` and every ancestor (inclusive, up to and including the filesystem root) and,
    at each level, appends whichever of :data:`MEMORY_FILENAMES` exist as files. The returned
    list is ordered **root-most first, cwd-most last** (and ``AGENTS.md`` before ``MEMORY.md``
    within a level) so a downstream concatenation lets the cwd-most file win. ``CLAUDE.md`` is
    never collected. Returns an empty list when no memory file exists anywhere on the path.
    """
    # Walk cwd → root (most specific first), collecting each level's files as a group; reverse
    # the *level order* (root-most first / cwd-most last) while keeping the within-level order
    # (AGENTS.md before MEMORY.md), then flatten.
    levels_cwd_first: list[list[Path]] = []
    for level in _ancestors_inclusive(cwd):
        group = [level / name for name in MEMORY_FILENAMES if (level / name).is_file()]
        if group:
            levels_cwd_first.append(group)

    return [path for group in reversed(levels_cwd_first) for path in group]


def _ancestors_inclusive(start: Path) -> list[Path]:
    """``start`` and every ancestor up to the filesystem root, most specific first.

    The root is detected by ``parent == self`` (``Path("/").parent`` is ``Path("/")``), so the
    loop terminates there. ``start`` is resolved so a relative ``cwd`` still walks real parents.
    """
    current = start.resolve()
    chain = [current]
    while current.parent != current:
        current = current.parent
        chain.append(current)
    return chain
