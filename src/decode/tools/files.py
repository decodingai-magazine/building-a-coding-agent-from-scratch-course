"""The file tools: ``read`` / ``glob`` / ``grep`` (read-only) and ``write`` / ``edit`` (ADR-0002 §7).

How the agent touches the working tree:

* :func:`read` — print a file with 1-indexed, ``cat -n``-style numbered lines, optionally
  windowed by ``offset`` / ``limit``, and truncated through :mod:`decode.tools.truncate`;
* :func:`glob` — list paths matching a shell glob, relative to ``cwd``, paths only;
* :func:`grep` — regex-search file contents, returning ``path:lineno:line`` hits;
* :func:`write` — create or overwrite a file (creating parent dirs), written atomically;
* :func:`edit` — replace a **unique** occurrence of ``old_string`` (BOM-stripped, EOL-normalized
  for matching; exact-then-whitespace-fuzzy), restoring the file's original BOM / line endings.

**Gating (ADR-0002 §3).** v1 asks on *every* tool call — read-only tools included — so each
function raises :class:`pydantic_ai.ApprovalRequired` until ``ctx.tool_call_approved`` is set,
exactly like :mod:`decode.tools.noop`. The read-only trio is *tagged* ``read_only=True`` (see
:data:`FILE_TOOLS_READ_ONLY`) so M3 can auto-allow them later without touching this code;
``write`` / ``edit`` are tagged ``read_only=False`` (see :data:`FILE_TOOLS_MUTATING`) and stay
gated. Because the gate fires *before* any path is resolved or any byte is read or written, a
**denied write/edit leaves the target byte-for-byte untouched** (never created, never truncated).

**Path safety.** Every path is resolved under ``ctx.deps.cwd`` and is never allowed to escape
it (``..`` traversal, absolute paths pointing elsewhere, in-tree symlinks resolving outside).
A missing / unreadable path, an empty result set, a bad regex, or an unmatchable / ambiguous
``edit`` target returns a model-readable :class:`pydantic_ai.ModelRetry` so the model can
correct itself — the REPL never crashes on bad tool input.

**Sync, not async.** Filesystem access here is local and the tool layer runs **sequentially**
in v1 (ADR-0002 §7), so there is no concurrency to win back by going async; Pydantic AI already
runs a sync tool in a worker thread. Keeping these sync matches :mod:`decode.tools.noop` and
keeps the code readable (the network/DB-only "async-for-IO" rule from AGENTS.md does not bite
on a single local file read).
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.tools.truncate import truncate

logger = logging.getLogger(__name__)

READ_TOOL_NAME = "read"
GLOB_TOOL_NAME = "glob"
GREP_TOOL_NAME = "grep"
WRITE_TOOL_NAME = "write"
EDIT_TOOL_NAME = "edit"

# All three file tools are read-only (they never mutate the tree). Tagged here for M3's
# read-only auto-allow; v1 still asks for each (see the module docstring).
FILE_TOOLS_READ_ONLY: dict[str, bool] = {
    READ_TOOL_NAME: True,
    GLOB_TOOL_NAME: True,
    GREP_TOOL_NAME: True,
}

# The two mutating file tools (task 007): NOT read-only, gated, always asked. Tagged here so the
# registry stays a single declaration site and M3's read-only auto-allow never touches them.
FILE_TOOLS_MUTATING: dict[str, bool] = {
    WRITE_TOOL_NAME: False,
    EDIT_TOOL_NAME: False,
}

# A UTF-8 byte-order mark. Some editors prepend it; we strip it before matching and restore it
# verbatim on write so an ``edit`` never silently drops (or adds) a BOM.
_UTF8_BOM = "﻿"


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


def write(ctx: RunContext[AgentDeps], path: str, content: str) -> str:
    """Create or overwrite a file with ``content`` (ADR-0002 §7).

    Resolves ``path`` under ``ctx.deps.cwd`` (an escape — ``..`` / absolute / an in-tree
    symlink pointing outside — is rejected with :class:`pydantic_ai.ModelRetry`). **Missing
    parent directories are created** so the model can scaffold a tree in one call; this is the
    only directory side effect ``write`` has. The file is written as UTF-8.

    Gated (ADR-0002 §3): raises :class:`pydantic_ai.ApprovalRequired` until the call is
    approved — and crucially *before* the path is resolved or any byte is written, so a denied
    write leaves the target byte-for-byte untouched (it is never created, never truncated).
    """
    if not ctx.tool_call_approved:
        logger.debug("write requires approval (path=%r)", path)
        raise ApprovalRequired

    target = _resolve_in_cwd(ctx.deps.cwd, path)
    if target.is_dir():
        raise ModelRetry(f"{path!r} is a directory; choose a file path to write.")
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(target, content.encode("utf-8"))
    logger.debug("wrote %d bytes to %r", len(content), path)
    return f"Wrote {path!r} ({len(content)} characters)."


def edit(ctx: RunContext[AgentDeps], path: str, old_string: str, new_string: str) -> str:
    """Replace a **unique** occurrence of ``old_string`` with ``new_string`` (ADR-0002 §7).

    The matching pipeline, in order:

    #. Read the file, **strip a leading UTF-8 BOM** and **detect the line-ending style**
       (CRLF / CR / LF), normalizing everything to ``\\n`` before matching so the model never
       has to reason about the file's physical newlines or BOM.
    #. Match ``old_string`` (also LF-normalized) **exactly first** (``str.count`` / ``find``).
       If there is no exact match, fall back to a **whitespace-normalized fuzzy** match — runs
       of whitespace are collapsed on both sides — that must still map back to a *single* span
       in the original text.
    #. Require a **unique** match: ``0`` matches → ``not found``; ``>1`` → ``ambiguous, N
       matches``; an empty ``old_string`` → ``empty``. Each is a model-readable
       :class:`pydantic_ai.ModelRetry` so the model can widen/narrow ``old_string`` and retry.

    On success the replacement is applied, the **original BOM and line-ending style are
    restored**, and the file is written atomically (temp file + ``os.replace``). Same cwd
    containment as :func:`write`.

    Gated (ADR-0002 §3): raises :class:`pydantic_ai.ApprovalRequired` *before* the file is read
    or written, so a denied edit leaves it byte-for-byte untouched.
    """
    if not ctx.tool_call_approved:
        logger.debug("edit requires approval (path=%r)", path)
        raise ApprovalRequired

    if old_string == "":
        raise ModelRetry("old_string is empty; provide the exact text to replace.")

    target = _resolve_in_cwd(ctx.deps.cwd, path)
    if not target.is_file():
        raise ModelRetry(f"No such file to edit: {path!r}.")
    try:
        # Decode raw bytes (not ``read_text``): universal-newline translation would collapse
        # the file's CR/CRLF on read, so we could never detect and restore the original style.
        raw = target.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ModelRetry(f"Could not read {path!r} to edit: {exc}.") from exc

    had_bom = raw.startswith(_UTF8_BOM)
    body = raw[len(_UTF8_BOM) :] if had_bom else raw
    eol = _detect_eol(body)
    normalized = _to_lf(body)
    needle = _to_lf(old_string)

    new_normalized = _replace_unique(normalized, needle, _to_lf(new_string))

    restored = new_normalized.replace("\n", eol) if eol != "\n" else new_normalized
    final = (_UTF8_BOM + restored) if had_bom else restored
    _atomic_write_bytes(target, final.encode("utf-8"))
    logger.debug("edited %r (eol=%r, bom=%s)", path, eol, had_bom)
    return f"Edited {path!r} (replaced 1 occurrence)."


def _detect_eol(text: str) -> str:
    """Return the dominant line-ending style of ``text``: ``"\\r\\n"``, ``"\\r"``, or ``"\\n"``.

    CRLF is checked first (a ``\\r\\n`` file also contains ``\\r`` and ``\\n``); a lone ``\\r``
    (classic Mac) is checked next; everything else (including a file with no newline at all)
    is treated as LF, which is the safe default for a freshly created or single-line file.
    """
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _to_lf(text: str) -> str:
    """Normalize CRLF and lone-CR line endings to LF (so matching is newline-agnostic)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _replace_unique(haystack: str, needle: str, replacement: str) -> str:
    """Replace the single occurrence of ``needle`` in ``haystack`` (exact, then fuzzy).

    Exact (``str.count``/``find``) is tried first: ``0`` → ``not found``, ``>1`` → ``ambiguous``.
    Only when there is no exact match do we fall back to a whitespace-normalized fuzzy search
    (:func:`_fuzzy_unique_span`) that must still resolve to a single span in ``haystack``.
    Raises a model-readable :class:`pydantic_ai.ModelRetry` on 0 / >1 matches.
    """
    exact = haystack.count(needle)
    if exact == 1:
        return haystack.replace(needle, replacement, 1)
    if exact > 1:
        raise ModelRetry(
            f"old_string is ambiguous, {exact} matches found; add surrounding context to "
            "old_string so it identifies exactly one location."
        )

    # No exact match: try a whitespace-normalized fuzzy match that maps to a unique span.
    span = _fuzzy_unique_span(haystack, needle)
    if span is None:
        raise ModelRetry(
            "old_string not found in the file; it must match the file's current text "
            "(check for typos, indentation, or stale content)."
        )
    start, end = span
    return haystack[:start] + replacement + haystack[end:]


def _normalize_ws(text: str) -> str:
    """Collapse every run of whitespace to a single space and strip the ends.

    This is the fuzzy-match key: two strings that differ only in indentation, trailing
    whitespace, or LF-vs-spacing compare equal under it.
    """
    return " ".join(text.split())


def _fuzzy_unique_span(haystack: str, needle: str) -> tuple[int, int] | None:
    """Find the unique ``[start, end)`` span in ``haystack`` matching ``needle`` fuzzily.

    "Fuzzily" means after collapsing whitespace runs (:func:`_normalize_ws`). We scan every
    substring of ``haystack`` that begins and ends on a non-whitespace character (whitespace at
    a span's edges is never load-bearing) and keep those whose normalized form equals the
    normalized ``needle``. Returns the single matching span, ``None`` if there are zero, and
    raises :class:`pydantic_ai.ModelRetry` (``ambiguous``) if more than one *distinct* span
    matches — mirroring the exact-match uniqueness rule.

    Cold path: reached only when the exact match misses, on bounded files, and every ``edit``
    is human-gated — so the all-pairs span scan here is acceptable for M1 (a rolling-window
    matcher is a fine later optimization if ``edit`` ever sees large files).
    """
    target = _normalize_ws(needle)
    if target == "":
        return None

    starts = [i for i, ch in enumerate(haystack) if not ch.isspace()]
    ends = [i + 1 for i, ch in enumerate(haystack) if not ch.isspace()]
    matches: list[tuple[int, int]] = []
    for start in starts:
        for end in ends:
            if end <= start:
                continue
            if _normalize_ws(haystack[start:end]) == target:
                matches.append((start, end))

    # Keep only minimal (non-overlapping-superset) spans: for a given start, the shortest end
    # that matches is the intended span; a longer end with the same normalized form only differs
    # by trailing whitespace already absorbed by normalization.
    minimal = _minimal_spans(matches)
    if not minimal:
        return None
    if len(minimal) > 1:
        raise ModelRetry(
            f"old_string is ambiguous, {len(minimal)} matches found (after normalizing "
            "whitespace); add surrounding context so it identifies exactly one location."
        )
    return minimal[0]


def _minimal_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Reduce ``spans`` to the distinct minimal-length match per start position.

    For a fixed start, several ends can share the same normalized form (they differ only by
    trailing whitespace); the shortest is the real span. Distinct starts that yield distinct
    minimal spans count as distinct matches (→ ambiguous upstream).
    """
    shortest_by_start: dict[int, tuple[int, int]] = {}
    for start, end in spans:
        current = shortest_by_start.get(start)
        if current is None or end < current[1]:
            shortest_by_start[start] = (start, end)
    return sorted(shortest_by_start.values())


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically-ish (temp file in the same dir + ``os.replace``).

    Writing to a sibling temp file and renaming means a reader never sees a half-written file,
    and a crash mid-write leaves the original intact (the rename is the commit point). The temp
    file shares ``target``'s directory so the rename stays on one filesystem (``os.replace`` is
    atomic only within a filesystem).
    """
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".decode-write-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
