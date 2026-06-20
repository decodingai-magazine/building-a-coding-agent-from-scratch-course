"""Discover the project memory files to inject into the prompt (ADR-0002 §8, Fix 1).

:func:`discover_memory_files` returns the memory files that exist, in the order a downstream
concatenation should read them. Two file kinds are memory, discovered **differently**:

* ``AGENTS.md`` — human/project memory. Walked **from ``cwd`` up to the filesystem root**,
  collecting every ancestor's ``AGENTS.md`` (a developer running ``decode`` from a subdirectory
  still picks up the project-root rules).
* ``MEMORY.md`` — the **harness-extracted** scratch memory. NOT walked: it is the single file
  ``cwd/.decode/MEMORY.md`` (the on-exit write-back's output, ADR-0002 §8 + Fix 1), consolidated
  with the rest of the harness artifacts (``sessions/``, ``logs/``) under ``<cwd>/.decode``.

``CLAUDE.md`` is **skipped**: it is the Claude-Code shim (it just imports ``AGENTS.md``), so
reading it would duplicate ``AGENTS.md`` into the prompt.

**AGENTS.md stop condition.** The walk climbs ``cwd`` → its parents until it reaches the
filesystem root (``path.parent == path``), inclusive. We stop at the filesystem root rather than
a "repo root" (a ``.git`` marker) deliberately: it is the simplest rule that needs no
project-layout heuristic, it matches the surveyed harnesses' "ancestor walk", and a developer
running ``decode`` from outside a git repo still gets their ancestor ``AGENTS.md`` files.

**Ordering — cwd-most wins.** AGENTS.md levels are emitted **root-most first, cwd-most last**;
the harness ``MEMORY.md`` is appended **last** of all. The assembler concatenates in this order,
so the **cwd-most** (most specific) file appears **last** in the prompt and therefore has the
final word — the same "nearest config wins" rule editors and linters use.

**Pure and sync.** Filesystem stats are local and fast and the tool layer runs sequentially in
v1 (ADR-0002 §7,10), so there is no concurrency to win by going async. The function only reads
directory metadata (``Path.is_file``); it never opens a file (that is the service's job).
"""

from __future__ import annotations

from pathlib import Path

from decode.config.settings import settings

# The human/project memory file walked from cwd up to the filesystem root.
_AGENTS_FILENAME = "AGENTS.md"
# The harness-extracted memory file. A single file under ``<cwd>/.decode`` (not walked).
_MEMORY_FILENAME = "MEMORY.md"

# The memory file kinds we may inject. ``CLAUDE.md`` is deliberately absent — it is the
# Claude-Code import shim, not a distinct memory source. Kept as the canonical name set the
# assembler's cap-sanity-check (``service._CAPPED_FILENAME``) validates against.
MEMORY_FILENAMES: tuple[str, ...] = (_AGENTS_FILENAME, _MEMORY_FILENAME)


def harness_memory_path(cwd: Path) -> Path:
    """The single harness ``MEMORY.md`` path: ``cwd/.decode/MEMORY.md`` (Fix 1).

    Config-driven via ``settings.decode_dir`` (the single config reader), so it always matches
    where :func:`decode.memory.extract.append_session_summary` writes the write-back to.
    """
    return cwd / settings.decode_dir / _MEMORY_FILENAME


def discover_memory_files(cwd: Path) -> list[Path]:
    """Collect the memory files to inject: ancestor ``AGENTS.md`` + ``cwd/.decode/MEMORY.md``.

    ``AGENTS.md`` is collected from ``cwd`` and every ancestor (inclusive, up to and including
    the filesystem root), ordered **root-most first, cwd-most last**. The harness ``MEMORY.md``
    (``cwd/.decode/MEMORY.md``) is **not** walked — it is appended once, **last**, when it
    exists. A downstream concatenation therefore lets the cwd-most file win. ``CLAUDE.md`` is
    never collected. Returns an empty list when no memory file exists.
    """
    # AGENTS.md: walk cwd → root (most specific first), then reverse so root-most is first and
    # the cwd-most AGENTS.md lands last (it wins among AGENTS.md files).
    agents_cwd_first = [
        level / _AGENTS_FILENAME
        for level in _ancestors_inclusive(cwd)
        if (level / _AGENTS_FILENAME).is_file()
    ]
    found: list[Path] = list(reversed(agents_cwd_first))

    # MEMORY.md: the single harness file under <cwd>/.decode, appended last so it has the final
    # word (most specific). Resolved to match the resolved AGENTS.md paths.
    memory = harness_memory_path(cwd).resolve()
    if memory.is_file():
        found.append(memory)

    return found


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
