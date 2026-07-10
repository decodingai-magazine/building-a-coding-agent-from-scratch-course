"""The file tools: ``read`` / ``glob`` / ``grep`` (read-only) and ``write`` / ``edit`` (ADR-0002 §7).

Load-bearing invariants:

* The permission gate fires *before* any path is resolved or byte is read/written, so a denied
  write/edit leaves the target byte-for-byte untouched (ADR-0002 §3).
* Every path stays contained under ``ctx.deps.cwd`` — ``..`` traversal, absolute paths, and
  in-tree symlinks resolving outside are rejected; bad tool input returns a model-readable
  :class:`pydantic_ai.ModelRetry`, never a crash.
* In a sandbox mode the byte transport routes through the backend seam (:func:`_active_backend`)
  while the shared logic (containment path math, numbering/truncation, search/replace, matching,
  rendering) stays host-side, so a sandbox result reads identically to a host one; containment is
  layered (a real-fs backend also resolves symlinks physically). ``none`` mode is direct pathlib,
  byte-identical (ADR-0012 §4).
* Sync on purpose: local filesystem, sequential tool layer, worker-thread execution (ADR-0002 §7).
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import shlex
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import anyio
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.services import lsp as lsp_service
from decode.services.lsp import Diagnostic
from decode.tools.approval import needs_approval
from decode.tools.truncate import truncate

if TYPE_CHECKING:
    # Typing-only: a runtime import would break the ``none`` path's sandbox laziness (ADR-0012 §9).
    from decode.sandbox.executor import SandboxBackend

logger = logging.getLogger(__name__)

READ_TOOL_NAME = "read"
GLOB_TOOL_NAME = "glob"
GREP_TOOL_NAME = "grep"
WRITE_TOOL_NAME = "write"
EDIT_TOOL_NAME = "edit"

# The two mutating file tools: NOT read-only, so the read-only auto-allow never touches them.
FILE_TOOLS_MUTATING: dict[str, bool] = {
    WRITE_TOOL_NAME: False,
    EDIT_TOOL_NAME: False,
}

# UTF-8 BOM: stripped before matching, restored verbatim on write so an edit never drops/adds it.
_UTF8_BOM = "﻿"

# LSP ``DiagnosticSeverity.Error`` — the only severity the passive enricher surfaces inline (ADR-0007).
_LSP_ERROR_SEVERITY = 1
# Cap on the appended diagnostics block; a ``(+K more)`` tail names the remainder.
_LSP_DIAGNOSTICS_LIMIT = 10


def _is_within(base: Path, candidate: Path) -> bool:
    """True iff ``candidate`` is ``base`` itself or lives under it (both already resolved)."""
    return candidate == base or base in candidate.parents


def _resolve_in_cwd(cwd: Path, raw: str) -> Path:
    """Resolve ``raw`` under ``cwd``; raise :class:`pydantic_ai.ModelRetry` when it escapes."""
    base = cwd.resolve()
    target = (base / raw).resolve()
    if not _is_within(base, target):
        raise ModelRetry(
            f"Path {raw!r} resolves outside the working directory; stay within the project tree."
        )
    return target


def _reject_escaping_pattern(pattern: str) -> None:
    """Reject a glob ``pattern`` that escapes ``cwd`` (absolute or ``..``) before globbing.

    Also avoids ``Path.glob``'s ``NotImplementedError`` on absolute patterns; :func:`_contain`
    is the post-glob second line of defence (in-tree symlinks resolving outside ``cwd``).
    """
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        raise ModelRetry(
            f"Glob pattern {pattern!r} points outside the working directory; "
            "use a pattern relative to the project tree (no '..' or absolute paths)."
        )


def _contain(base: Path, matches: list[Path]) -> list[Path]:
    """Keep only ``matches`` whose *resolved* real path stays under ``base`` (files only).

    Resolving first drops an in-tree symlink pointing outside ``cwd`` — its contents must
    never reach the model.
    """
    return sorted(m for m in matches if m.is_file() and _is_within(base, m.resolve()))


def _active_backend(cwd: Path) -> SandboxBackend | None:
    """The active session's sandbox backend for byte transport, or ``None`` in ``none`` mode (§4).

    Thin, lazily-importing indirection over :func:`decode.tools.bash.active_backend` (the ``none``
    path never pulls in the sandbox package); the one function tests patch to inject a fake
    backend. Reaching the backend *creates* the sandbox on first touch, which can fail — the
    failure is rendered as a model-readable :class:`pydantic_ai.ModelRetry`, **not** downgraded to
    ``None``: falling through to host pathlib would be a second escape.
    """
    from decode.tools.bash import active_backend

    try:
        return active_backend(cwd)
    except (RuntimeError, OSError) as exc:
        logger.debug("sandbox backend unavailable for a file op: %s", exc)
        raise ModelRetry(
            f"The sandbox is unavailable ({exc}); it could not be started for this operation."
        ) from exc


def _bridge[T](op: Callable[..., Awaitable[T]], *args: object) -> T:
    """Run an async backend file op from a sync tool, rendering an infra failure as a retry (§4).

    Bridges via :func:`anyio.from_thread.run`; an infra failure below the seam becomes a
    model-readable :class:`pydantic_ai.ModelRetry` while the op's own ``ModelRetry``\\ s pass
    straight through. A :class:`WorkspaceEscape` from a real-fs backend's physical containment is
    an :class:`OSError`, so it is caught by base class — files.py never imports it, keeping the
    ``none`` path free of any sandbox import (ADR-0012 §9).
    """
    try:
        return anyio.from_thread.run(op, *args)
    except ModelRetry:
        raise
    except (RuntimeError, OSError) as exc:
        logger.debug("sandbox file op failed: %s", exc)
        raise ModelRetry(f"Sandbox file operation failed: {exc}") from exc


def _resolve_logical(raw: str) -> str:
    """Resolve ``raw`` to a Workspace-relative POSIX path, rejecting escapes (ADR-0012 §4).

    Backend-agnostic path math — **never** host ``Path.resolve`` (a modal Workspace path is not a
    host path): fold ``.`` / ``..`` against the logical Workspace root and reject anything that
    escapes it (a ``..`` climbing above the root, or an absolute path) with the same
    :class:`pydantic_ai.ModelRetry` refusal :func:`_resolve_in_cwd` gives in ``none`` mode.
    Shared by both backends; returns the root-relative path (``""`` for the root).
    """
    pure = PurePosixPath(raw)
    escape = ModelRetry(
        f"Path {raw!r} resolves outside the working directory; stay within the project tree."
    )
    if pure.is_absolute():
        raise escape
    parts: list[str] = []
    for part in pure.parts:
        if part == ".":
            continue
        if part == "..":
            if not parts:
                raise escape
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


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
    if needs_approval(ctx):
        logger.debug("read requires approval (path=%r)", path)
        raise ApprovalRequired

    backend = _active_backend(ctx.deps.cwd)
    if backend is not None:
        rel = _resolve_logical(path)
        content = _bridge(_sandbox_read_content, backend, path, rel)
        return _render_numbered(content, path, offset=offset, limit=limit)

    target = _resolve_in_cwd(ctx.deps.cwd, path)
    if not target.exists():
        raise ModelRetry(f"No such file: {path!r}.")
    if target.is_dir():
        raise ModelRetry(f"{path!r} is a directory; use glob to list its contents.")
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ModelRetry(f"Could not read {path!r}: {exc}.") from exc

    return _render_numbered(content, path, offset=offset, limit=limit)


def _render_numbered(content: str, path: str, *, offset: int | None, limit: int | None) -> str:
    """Number ``content``'s lines into the model-facing read result — shared by both modes (§4).

    The rendering tail both the ``none`` and sandbox paths call, so a sandbox read reads
    byte-identically to a host read: 1-indexed ``cat -n`` numbering for the ``offset`` / ``limit``
    window, then the safety cap through :mod:`decode.tools.truncate` with the overflow spill note.
    An ``offset`` past end-of-file is a model-readable :class:`pydantic_ai.ModelRetry`.
    """
    start = 1 if offset is None else max(offset, 1)
    # `limit` is the caller's window; the truncate safety cap below bounds runaway output.
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


async def _sandbox_read_content(backend: SandboxBackend, path: str, rel: str) -> str:
    """Read + decode the logical Workspace path ``rel`` via the backend seam (ADR-0012 §4).

    ``stat`` first for the same missing / is-a-directory :class:`pydantic_ai.ModelRetry`\\ s
    ``none`` mode raises, then ``read_bytes`` + UTF-8 decode; the shared
    :func:`_render_numbered` renders above the seam.
    """
    st = await backend.stat(rel)
    if st is None:
        raise ModelRetry(f"No such file: {path!r}.")
    if st.is_dir:
        raise ModelRetry(f"{path!r} is a directory; use glob to list its contents.")
    try:
        raw = await backend.read_bytes(rel)
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ModelRetry(f"Could not read {path!r}: {exc}.") from exc


def _number_lines(content: str, *, start: int, limit: int | None) -> str | None:
    """Render ``content`` as ``<lineno>\\t<line>`` for ``[start, start+limit)`` (``None`` = to end).

    Line numbers are absolute (1-indexed) so a windowed read re-pages correctly. Returns
    ``None`` when ``start`` is past the last line (the caller turns that into a ``ModelRetry``).
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
    if needs_approval(ctx):
        logger.debug("glob requires approval (pattern=%r)", pattern)
        raise ApprovalRequired

    _reject_escaping_pattern(pattern)
    backend = _active_backend(ctx.deps.cwd)
    if backend is not None:
        matches = _bridge(_sandbox_glob, backend, pattern)
        if not matches:
            raise ModelRetry(f"No files match {pattern!r} under the working directory.")
        return "\n".join(matches)

    base = ctx.deps.cwd.resolve()
    matches = _contain(base, list(base.glob(pattern)))
    if not matches:
        raise ModelRetry(f"No files match {pattern!r} under the working directory.")
    return "\n".join(str(p.relative_to(base)) for p in matches)


# Backend glob: ``find`` enumerates Workspace file paths only (never contents, ADR-0012 §4);
# the pattern is matched host-side by :func:`_glob_match` for exact ``Path.glob`` parity.
# ``ponytail:`` ``find -type f`` skips symlinks (``none``-mode ``Path.glob`` follows in-tree ones)
# and enumerates the whole tree — fine for a repo clone; a ``find``-side ``-name`` prefilter is
# the upgrade path if a huge Workspace ever makes this a cost.
_FIND_ALL_FILES = "find . -type f"


async def _sandbox_glob(backend: SandboxBackend, pattern: str) -> list[str]:
    """List Workspace files matching ``pattern`` via a backend ``find`` exec (ADR-0012 §4).

    Enumerates every file, filters host-side with :func:`_glob_match`, and sorts — the same
    sorted relative-path list ``none`` mode's ``base.glob`` returns.
    """
    result = await backend.exec("bash", "-lc", _FIND_ALL_FILES, timeout_s=settings.bash_timeout_s)
    files = _parse_find_output(result.stdout)
    return sorted(f for f in files if _glob_match(f, pattern))


def _parse_find_output(raw: str) -> list[str]:
    """Parse ``find . -type f`` stdout into Workspace-relative POSIX paths (strip the ``./`` prefix)."""
    files: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped == ".":
            continue
        files.append(stripped[2:] if stripped.startswith("./") else stripped)
    return files


def _glob_match(path: str, pattern: str) -> bool:
    """Whether the relative POSIX ``path`` matches shell-glob ``pattern`` with ``Path.glob`` semantics.

    Segment-by-segment: each non-``**`` segment via :func:`fnmatch.fnmatch` against **one**
    component (``*`` never crosses ``/``); ``**`` matches zero or more components. Verified
    against :meth:`pathlib.Path.glob` on 3.12 — the sandbox/host output parity.
    """
    return _match_segments(path.split("/"), pattern.split("/"))


def _match_segments(parts: list[str], pats: list[str]) -> bool:
    """Recursive segment match for :func:`_glob_match` (``**`` = zero-or-more path components)."""
    if not pats:
        return not parts
    head, *rest = pats
    if head == "**":
        return any(_match_segments(parts[i:], rest) for i in range(len(parts) + 1))
    if not parts:
        return False
    if fnmatch.fnmatch(parts[0], head):
        return _match_segments(parts[1:], rest)
    return False


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
    if needs_approval(ctx):
        logger.debug("grep requires approval (pattern=%r)", pattern)
        raise ApprovalRequired

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ModelRetry(f"Invalid regular expression {pattern!r}: {exc}.") from exc

    backend = _active_backend(ctx.deps.cwd)
    if backend is not None:
        hits = _bridge(_sandbox_grep, backend, pattern, path, glob)
        if not hits:
            raise ModelRetry(f"No matches for {pattern!r} in the searched files.")
        return _render_grep_hits(hits)

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
    return _render_grep_hits(hits)


def _render_grep_hits(hits: list[str]) -> str:
    """Render ``path:lineno:line`` hits into the model-facing grep result — shared by both modes (§4).

    Hits joined and passed through the safety cap (:mod:`decode.tools.truncate`) with the
    overflow spill note, so sandbox and host greps read identically.
    """
    result = truncate(
        "\n".join(hits) + "\n",
        max_lines=settings.max_output_lines,
        max_bytes=settings.max_output_bytes,
    )
    text = result.text.rstrip("\n")
    if result.truncated:
        text += f"\n\n[matches truncated; full results at {result.full_path}]"
    return text


async def _sandbox_grep(
    backend: SandboxBackend, pattern: str, path: str | None, glob: str | None
) -> list[str]:
    """Search the Workspace via a backend ``grep`` exec, returning sorted ``path:lineno:line`` hits (§4).

    Executes **in the sandbox** (never downloading the tree's contents, ADR-0012 §4) with
    ``none``-mode scope resolution: a single ``path`` (``stat``-checked), else ``glob`` (via
    :func:`_sandbox_glob` for file-scope parity), else recursive ``grep -r``. Hits are
    ``./``-stripped and sorted by ``(path, lineno)`` to reproduce ``none`` mode's order.

    ``ponytail:`` grep's regex is its ERE dialect, not Python ``re`` (the pattern was ``re``-validated
    above for a shared "invalid regex" error, but a pattern whose dialects differ can match differently);
    a specific-``glob`` scope passes its file list as ``grep`` args (bounded by the OS arg limit for a
    huge match). Both are acceptable for the tool layer; the recursive default avoids the arg-limit path.
    """
    if path is not None:
        rel = _resolve_logical(path)
        st = await backend.stat(rel)
        if st is None or st.is_dir:
            raise ModelRetry(f"No such file to search: {path!r}.")
        command = _grep_files_command(pattern, [rel])
    elif glob is not None:
        _reject_escaping_pattern(glob)
        files = await _sandbox_glob(backend, glob)
        if not files:
            return []
        command = _grep_files_command(pattern, files)
    else:
        command = _grep_recursive_command(pattern)
    result = await backend.exec("bash", "-lc", command, timeout_s=settings.bash_timeout_s)
    return _parse_grep_output(result.stdout)


# grep flags: -r recursive, -H force the filename prefix, -n line numbers, -I skip binaries,
# -E extended regex, -e / ``--`` guard leading-``-`` patterns and paths. All ``shlex.quote``d.
def _grep_recursive_command(pattern: str) -> str:
    """The recursive ``grep`` command for the whole-Workspace scope (``none`` mode's ``**/*`` default)."""
    return f"grep -rHnI -E -e {shlex.quote(pattern)} -- ."


def _grep_files_command(pattern: str, files: list[str]) -> str:
    """The ``grep`` command scoped to an explicit ``files`` list (a single ``path`` or a ``glob`` scope)."""
    quoted_files = " ".join(shlex.quote(f) for f in files)
    return f"grep -HnI -E -e {shlex.quote(pattern)} -- {quoted_files}"


def _parse_grep_output(raw: str) -> list[str]:
    """Parse ``grep -Hn`` stdout into ``path:lineno:line`` hits: strip ``./``, sort by ``(path, lineno)``."""
    hits: list[str] = []
    for line in raw.splitlines():
        if not line:
            continue
        hits.append(line[2:] if line.startswith("./") else line)
    return sorted(hits, key=_grep_hit_sort_key)


def _grep_hit_sort_key(hit: str) -> tuple[tuple[str, ...], int]:
    """Sort key for a ``path:lineno:line`` hit — ``(path parts, lineno)`` (``none``-mode order)."""
    path, _, rest = hit.partition(":")
    lineno_text, _, _ = rest.partition(":")
    lineno = int(lineno_text) if lineno_text.isdigit() else 0
    return (PurePosixPath(path).parts, lineno)


def _grep_candidates(base: Path, *, path: str | None, glob: str | None) -> list[Path]:
    """The list of files grep will search, resolved under ``base`` (sorted, files only).

    ``path`` wins over ``glob``; with neither, the whole tree (``**/*``). Both routes stay
    contained under ``base`` (escaping patterns rejected up front, out-of-tree symlinks dropped),
    so grep never reads an out-of-tree file. Raises :class:`pydantic_ai.ModelRetry` for a
    missing explicit ``path`` or an escaping ``glob`` pattern.
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
    if needs_approval(ctx):
        logger.debug("write requires approval (path=%r)", path)
        raise ApprovalRequired

    backend = _active_backend(ctx.deps.cwd)
    if backend is not None:
        rel = _resolve_logical(path)
        _bridge(_sandbox_write_bytes, backend, path, rel, content.encode("utf-8"))
        logger.debug("wrote %d bytes to %r (sandbox)", len(content), path)
        return _enrich(f"Wrote {path!r} ({len(content)} characters).", ctx.deps.cwd, path)

    target = _resolve_in_cwd(ctx.deps.cwd, path)
    if target.is_dir():
        raise ModelRetry(f"{path!r} is a directory; choose a file path to write.")
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(target, content.encode("utf-8"))
    logger.debug("wrote %d bytes to %r", len(content), path)
    base = f"Wrote {path!r} ({len(content)} characters)."
    return _enrich(base, ctx.deps.cwd, path)


async def _sandbox_write_bytes(backend: SandboxBackend, path: str, rel: str, data: bytes) -> None:
    """Write ``data`` to the logical Workspace path ``rel`` via the backend seam (ADR-0012 §4).

    ``stat`` first for the same is-a-directory :class:`pydantic_ai.ModelRetry` ``none`` mode
    raises, then ``write_bytes`` (the backend creates missing parents). The LSP enrichment stays
    on the worker thread in :func:`write`.
    """
    st = await backend.stat(rel)
    if st is not None and st.is_dir:
        raise ModelRetry(f"{path!r} is a directory; choose a file path to write.")
    await backend.write_bytes(rel, data)


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
    if needs_approval(ctx):
        logger.debug("edit requires approval (path=%r)", path)
        raise ApprovalRequired

    if old_string == "":
        raise ModelRetry("old_string is empty; provide the exact text to replace.")

    backend = _active_backend(ctx.deps.cwd)
    if backend is not None:
        rel = _resolve_logical(path)
        _bridge(_sandbox_edit_bytes, backend, path, rel, old_string, new_string)
        logger.debug("edited %r (sandbox)", path)
        return _enrich(f"Edited {path!r} (replaced 1 occurrence).", ctx.deps.cwd, path)

    target = _resolve_in_cwd(ctx.deps.cwd, path)
    if not target.is_file():
        raise ModelRetry(f"No such file to edit: {path!r}.")
    try:
        # Raw bytes (not read_text): universal newlines would destroy the CR/CRLF style to restore.
        raw = target.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ModelRetry(f"Could not read {path!r} to edit: {exc}.") from exc

    final = _apply_edit(raw, old_string, new_string)
    _atomic_write_bytes(target, final.encode("utf-8"))
    logger.debug("edited %r", path)
    base = f"Edited {path!r} (replaced 1 occurrence)."
    return _enrich(base, ctx.deps.cwd, path)


def _apply_edit(raw_text: str, old_string: str, new_string: str) -> str:
    """Apply edit's unique-match replacement to ``raw_text`` — shared by both modes (ADR-0012 §4).

    Strip a leading UTF-8 BOM, detect the CRLF / CR / LF style, LF-normalize for matching, apply
    :func:`_replace_unique` (exact-then-fuzzy), then restore the original BOM + line-ending
    style — so an edit behaves byte-identically in either mode. Returns the new full text.
    """
    had_bom = raw_text.startswith(_UTF8_BOM)
    body = raw_text[len(_UTF8_BOM) :] if had_bom else raw_text
    eol = _detect_eol(body)
    normalized = _to_lf(body)
    needle = _to_lf(old_string)
    new_normalized = _replace_unique(normalized, needle, _to_lf(new_string))
    restored = new_normalized.replace("\n", eol) if eol != "\n" else new_normalized
    return (_UTF8_BOM + restored) if had_bom else restored


async def _sandbox_edit_bytes(
    backend: SandboxBackend, path: str, rel: str, old_string: str, new_string: str
) -> None:
    """Read → replace → write the logical Workspace path ``rel`` via the backend seam (ADR-0012 §4).

    ``stat`` for the same missing-file :class:`pydantic_ai.ModelRetry`, raw-bytes read
    (:func:`_apply_edit` needs untranslated newlines to restore the style), the shared transform,
    then ``write_bytes``. The LSP enrichment stays on the worker thread in :func:`edit`.
    """
    st = await backend.stat(rel)
    if st is None or st.is_dir:
        raise ModelRetry(f"No such file to edit: {path!r}.")
    try:
        raw = (await backend.read_bytes(rel)).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ModelRetry(f"Could not read {path!r} to edit: {exc}.") from exc
    final = _apply_edit(raw, old_string, new_string)
    await backend.write_bytes(rel, final.encode("utf-8"))


def _enrich(base: str, cwd: Path, path: str) -> str:
    """Append an errors-only LSP diagnostics block to a successful ``.py`` write/edit (ADR-0007).

    ``base`` (the exact ``Wrote …`` / ``Edited …`` string) is returned **unchanged** unless the
    just-written file has LSP *errors* (severity ``1``), in which case the block is appended as
    ``f"{base}\\n\\n{summary}"``. Best-effort and silent — it **swallows every exception**, so it
    can never change or break the write/edit return; no extra permission gate. In ``modal`` it is
    best-effort-disabled: host ``ty`` cannot reach the remote Workspace filesystem (ADR-0012 §7);
    ``none`` + ``docker`` run it against a real host path.
    """
    if not (settings.lsp_enabled and settings.lsp_diagnostics_on_edit):
        return base
    if settings.sandbox_mode == "modal":
        return base
    if not path.lower().endswith(".py"):
        return base
    try:
        summary = _format_lsp_errors(lsp_service.diagnostics_on_edit(cwd, path))
    except Exception as exc:  # best-effort: an enricher failure never touches the edit's return
        logger.debug("lsp enricher skipped for %r (unavailable): %s", path, exc)
        return base
    if summary is None:
        return base
    logger.debug("lsp enricher appended diagnostics for %r", path)
    return f"{base}\n\n{summary}"


def _format_lsp_errors(diagnostics: list[Diagnostic] | None) -> str | None:
    """Render an errors-only, bounded diagnostics block; ``None`` when there is nothing to report.

    Keeps only LSP errors (``severity == 1``); ``None`` for no input, a clean file, or a list
    with no errors. Otherwise a server-named header plus up to :data:`_LSP_DIAGNOSTICS_LIMIT`
    ``  line:column  message`` lines, with a ``  (+K more)`` tail when truncated.
    """
    if not diagnostics:
        return None
    errors = [d for d in diagnostics if d.severity == _LSP_ERROR_SEVERITY]
    if not errors:
        return None
    shown = errors[:_LSP_DIAGNOSTICS_LIMIT]
    lines = [f"LSP diagnostics ({settings.lsp_server_command}) — fix these:"]
    lines += [f"  {d.line}:{d.column}  {d.message}" for d in shown]
    hidden = len(errors) - len(shown)
    if hidden > 0:
        lines.append(f"  (+{hidden} more)")
    return "\n".join(lines)


def _detect_eol(text: str) -> str:
    """Return the dominant line-ending style of ``text``: CRLF checked first, then lone CR, else LF."""
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

    Exact match first (``0`` → not found, ``>1`` → ambiguous); only on no exact match fall back
    to the whitespace-normalized fuzzy search (:func:`_fuzzy_unique_span`), which must still
    resolve to a single span. Raises a model-readable :class:`pydantic_ai.ModelRetry` on 0 / >1.
    """
    exact = haystack.count(needle)
    if exact == 1:
        return haystack.replace(needle, replacement, 1)
    if exact > 1:
        raise ModelRetry(
            f"old_string is ambiguous, {exact} matches found; add surrounding context to "
            "old_string so it identifies exactly one location."
        )

    span = _fuzzy_unique_span(haystack, needle)
    if span is None:
        raise ModelRetry(
            "old_string not found in the file; it must match the file's current text "
            "(check for typos, indentation, or stale content)."
        )
    start, end = span
    return haystack[:start] + replacement + haystack[end:]


def _normalize_ws(text: str) -> str:
    """Collapse whitespace runs to single spaces and strip the ends — the fuzzy-match key."""
    return " ".join(text.split())


def _fuzzy_unique_span(haystack: str, needle: str) -> tuple[int, int] | None:
    """Find the unique ``[start, end)`` span in ``haystack`` matching ``needle`` after whitespace collapse.

    Scans spans that begin/end on non-whitespace and keeps those whose :func:`_normalize_ws`
    form equals the normalized ``needle``. Returns the single match, ``None`` for zero, and
    raises :class:`pydantic_ai.ModelRetry` (``ambiguous``) for more than one *distinct* span.
    Cold path (exact match missed, bounded files, human-gated), so the all-pairs scan is fine.
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

    # Keep the shortest end per start: longer ends differ only by whitespace already absorbed.
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
    """Reduce ``spans`` to the distinct minimal-length match per start (distinct starts → ambiguous upstream)."""
    shortest_by_start: dict[int, tuple[int, int]] = {}
    for start, end in spans:
        current = shortest_by_start.get(start)
        if current is None or end < current[1]:
            shortest_by_start[start] = (start, end)
    return sorted(shortest_by_start.values())


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically-ish (sibling temp file + ``os.replace``).

    A reader never sees a half-written file and a crash mid-write leaves the original intact;
    the temp file shares ``target``'s directory so the rename stays on one filesystem
    (``os.replace`` is atomic only within a filesystem).
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
