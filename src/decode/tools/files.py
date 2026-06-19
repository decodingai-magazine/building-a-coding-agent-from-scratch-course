"""The read-only file tools: ``read``, ``glob``, ``grep`` (ADR-0002 §7).

These are the three ways the agent inspects the working tree without changing it:

* :func:`read` — print a file with 1-indexed, ``cat -n``-style numbered lines, optionally
  windowed by ``offset`` / ``limit``, and truncated through :mod:`decode.tools.truncate`;
* :func:`glob` — list paths matching a shell glob, relative to ``cwd``, paths only;
* :func:`grep` — regex-search file contents, returning ``path:lineno:line`` hits.

**Gating (ADR-0002 §3).** v1 asks on *every* tool call — read-only tools included — so each
function raises :class:`pydantic_ai.ApprovalRequired` until ``ctx.tool_call_approved`` is set,
exactly like :mod:`decode.tools.noop`. They are nonetheless *tagged* ``read_only=True`` (see
:data:`FILE_TOOLS_READ_ONLY`) so M3 can auto-allow them later without touching this code.

**Path safety.** Every path is resolved under ``ctx.deps.cwd`` and is never allowed to escape
it (``..`` traversal, absolute paths pointing elsewhere). A missing / unreadable path, an empty
result set, or a bad regex returns a model-readable :class:`pydantic_ai.ModelRetry` so the model
can correct itself — the REPL never crashes on bad tool input.

**Sync, not async.** Filesystem access here is local and the tool layer runs **sequentially**
in v1 (ADR-0002 §7), so there is no concurrency to win back by going async; Pydantic AI already
runs a sync tool in a worker thread. Keeping these sync matches :mod:`decode.tools.noop` and
keeps the code readable (the network/DB-only "async-for-IO" rule from AGENTS.md does not bite
on a single local file read).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.tools.truncate import truncate

logger = logging.getLogger(__name__)

READ_TOOL_NAME = "read"
GLOB_TOOL_NAME = "glob"
GREP_TOOL_NAME = "grep"

# All three file tools are read-only (they never mutate the tree). Tagged here for M3's
# read-only auto-allow; v1 still asks for each (see the module docstring).
FILE_TOOLS_READ_ONLY: dict[str, bool] = {
    READ_TOOL_NAME: True,
    GLOB_TOOL_NAME: True,
    GREP_TOOL_NAME: True,
}


def _is_within(base: Path, candidate: Path) -> bool:
    """True iff ``candidate`` is ``base`` itself or lives under it (both already resolved)."""
    return candidate == base or base in candidate.parents


def _resolve_in_cwd(cwd: Path, raw: str) -> Path:
    """Resolve ``raw`` under ``cwd`` and reject anything that escapes it.

    Returns the resolved absolute path. Raises :class:`pydantic_ai.ModelRetry` (not an error)
    when the target lands outside ``cwd`` — a model-correctable mistake, not a crash.
    """
    base = cwd.resolve()
    target = (base / raw).resolve()
    if not _is_within(base, target):
        raise ModelRetry(
            f"Path {raw!r} resolves outside the working directory; stay within the project tree."
        )
    return target


def _reject_escaping_pattern(pattern: str) -> None:
    """Reject a glob ``pattern`` that points outside ``cwd`` up front (ADR-0002 §7).

    A leading ``/`` (absolute) or any ``..`` segment can only be aiming outside the project
    tree, so refuse it with a model-readable :class:`pydantic_ai.ModelRetry` before globbing.
    This also avoids :class:`Path.glob`'s ``NotImplementedError`` on absolute patterns. The
    post-glob containment check (:func:`_contain`) is the second line of defence — it also
    catches in-tree symlinks that resolve outside ``cwd``.
    """
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        raise ModelRetry(
            f"Glob pattern {pattern!r} points outside the working directory; "
            "use a pattern relative to the project tree (no '..' or absolute paths)."
        )


def _contain(base: Path, matches: list[Path]) -> list[Path]:
    """Keep only ``matches`` whose *resolved* real path stays under ``base`` (files only).

    Resolving before the containment check means an in-tree symlink that points outside
    ``cwd`` is dropped — its contents must never reach the model (AGENTS.md). ``base`` is
    already resolved by the caller.
    """
    return sorted(m for m in matches if m.is_file() and _is_within(base, m.resolve()))


def read(
    ctx: RunContext[AgentDeps],
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    """Read a text file with 1-indexed, numbered lines (ADR-0002 §7).

    ``offset`` is the 1-indexed first line to print (default ``1``); ``limit`` is the maximum
    number of lines the *caller* asked for (default: all lines from ``offset``). Output is
    ``cat -n``-style (``<lineno>\\t<line>``) so the model can re-page precisely. The caller's
    window is then passed through :mod:`decode.tools.truncate`, which enforces the *safety* cap
    (2000 lines / 50 KB, spill on overflow) independently of the caller's ``limit``.

    Gated: raises :class:`pydantic_ai.ApprovalRequired` until approved. A missing path, a
    directory, an unreadable/undecodable file, or an ``offset`` past end-of-file returns a
    :class:`pydantic_ai.ModelRetry`.
    """
    if not ctx.tool_call_approved:
        logger.debug("read requires approval (path=%r)", path)
        raise ApprovalRequired

    target = _resolve_in_cwd(ctx.deps.cwd, path)
    if not target.exists():
        raise ModelRetry(f"No such file: {path!r}.")
    if target.is_dir():
        raise ModelRetry(f"{path!r} is a directory; use glob to list its contents.")
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ModelRetry(f"Could not read {path!r}: {exc}.") from exc

    start = 1 if offset is None else max(offset, 1)
    # `limit` is the caller's requested window (None → all lines from `offset`); the safety
    # cap below (truncate) is what bounds runaway output, not this.
    line_limit = None if limit is None else max(limit, 0)
    numbered = _number_lines(content, start=start, limit=line_limit)
    if numbered is None:
        raise ModelRetry(f"{path!r} has no line {start}: offset is past the end of the file.")

    result = truncate(
        numbered, max_lines=settings.max_output_lines, max_bytes=settings.max_output_bytes
    )
    text = result.text.rstrip("\n")
    if result.truncated:
        text += (
            f"\n\n[output truncated to {settings.max_output_lines} lines / "
            f"{settings.max_output_bytes} bytes; full content at {result.full_path}]"
        )
    return text


def _number_lines(content: str, *, start: int, limit: int | None) -> str | None:
    """Render ``content`` as ``<lineno>\\t<line>`` for the window ``[start, start+limit)``.

    ``limit`` ``None`` means "all lines from ``start``". Line numbers are absolute (1-indexed)
    so a windowed read still re-pages correctly. Returns ``None`` when ``start`` is past the
    last line (the caller turns that into a ``ModelRetry``).
    """
    lines = content.splitlines()
    if start > len(lines):
        return None
    window = lines[start - 1 :] if limit is None else lines[start - 1 : start - 1 + limit]
    return "\n".join(f"{start + i}\t{line}" for i, line in enumerate(window))


def glob(ctx: RunContext[AgentDeps], pattern: str) -> str:
    """List paths matching shell glob ``pattern`` under ``cwd``, paths only (ADR-0002 §7).

    Matches are returned **relative to ``cwd``**, one per line, sorted. Supports ``**`` for
    recursive matching. Gated (raises :class:`pydantic_ai.ApprovalRequired` until approved).

    **Containment.** The pattern is rejected up front if it escapes ``cwd`` (``..`` / absolute),
    and every match is re-checked against ``cwd`` after globbing — including symlinks that
    resolve outside it — so no out-of-tree path is ever listed. Returns a
    :class:`pydantic_ai.ModelRetry` when the pattern escapes or nothing matches.
    """
    if not ctx.tool_call_approved:
        logger.debug("glob requires approval (pattern=%r)", pattern)
        raise ApprovalRequired

    _reject_escaping_pattern(pattern)
    base = ctx.deps.cwd.resolve()
    matches = _contain(base, list(base.glob(pattern)))
    if not matches:
        raise ModelRetry(f"No files match {pattern!r} under the working directory.")
    return "\n".join(str(p.relative_to(base)) for p in matches)


def grep(
    ctx: RunContext[AgentDeps],
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
) -> str:
    """Regex-search file contents under ``cwd``, returning ``path:lineno:line`` hits (§7).

    Search scope: a single ``path`` if given, else files matching ``glob`` (default ``**/*``,
    recursive). Line numbers are 1-indexed; results are sorted by ``(path, lineno)``. Gated
    (raises :class:`pydantic_ai.ApprovalRequired` until approved). Returns a
    :class:`pydantic_ai.ModelRetry` for an invalid regex, a missing ``path``, or no matches.
    """
    if not ctx.tool_call_approved:
        logger.debug("grep requires approval (pattern=%r)", pattern)
        raise ApprovalRequired

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ModelRetry(f"Invalid regular expression {pattern!r}: {exc}.") from exc

    base = ctx.deps.cwd.resolve()
    candidates = _grep_candidates(base, path=path, glob=glob)

    hits: list[str] = []
    for file in candidates:
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Skip binary / unreadable files silently — a search should not crash on one.
            continue
        rel = file.relative_to(base)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{rel}:{lineno}:{line}")

    if not hits:
        raise ModelRetry(f"No matches for {pattern!r} in the searched files.")
    result = truncate(
        "\n".join(hits) + "\n",
        max_lines=settings.max_output_lines,
        max_bytes=settings.max_output_bytes,
    )
    text = result.text.rstrip("\n")
    if result.truncated:
        text += f"\n\n[matches truncated; full results at {result.full_path}]"
    return text


def _grep_candidates(base: Path, *, path: str | None, glob: str | None) -> list[Path]:
    """The list of files grep will search, resolved under ``base`` (sorted, files only).

    ``path`` (a single file) wins over ``glob`` (a pattern); with neither, search the whole
    tree (``**/*``). Both routes are contained under ``base``: an explicit ``path`` goes through
    :func:`_resolve_in_cwd`, and a ``glob`` pattern is rejected up front if it escapes and then
    re-checked per match (symlinks resolving outside ``cwd`` are dropped) — so grep never reads,
    let alone returns, an out-of-tree file's contents. Raises :class:`pydantic_ai.ModelRetry`
    for a missing explicit ``path`` or an escaping ``glob`` pattern.
    """
    if path is not None:
        target = _resolve_in_cwd(base, path)
        if not target.is_file():
            raise ModelRetry(f"No such file to search: {path!r}.")
        return [target]
    pattern = glob or "**/*"
    _reject_escaping_pattern(pattern)
    return _contain(base, list(base.glob(pattern)))
