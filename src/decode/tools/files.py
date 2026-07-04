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
function raises :class:`pydantic_ai.ApprovalRequired` until ``ctx.tool_call_approved`` is set.
``write`` / ``edit`` are tagged ``read_only=False`` (see :data:`FILE_TOOLS_MUTATING`) and stay
gated. Because the gate fires *before* any path is resolved or any byte is read or written, a
**denied write/edit leaves the target byte-for-byte untouched** (never created, never truncated).

**Path safety.** Every path is resolved under ``ctx.deps.cwd`` and is never allowed to escape
it (``..`` traversal, absolute paths pointing elsewhere, in-tree symlinks resolving outside).
A missing / unreadable path, an empty result set, a bad regex, or an unmatchable / ambiguous
``edit`` target returns a model-readable :class:`pydantic_ai.ModelRetry` so the model can
correct itself — the REPL never crashes on bad tool input.

**Sandbox routing (ADR-0012 §4).** In a sandbox mode the tools operate on the isolated Workspace
**through the backend seam** instead of host pathlib: :func:`_active_backend` (mirroring ``bash``'s
executor memo) yields the session's :class:`~decode.sandbox.executor.SandboxBackend`, and
``read`` / ``write`` / ``edit`` route their byte transport through its file ops while ``glob`` / ``grep``
run as backend ``exec`` (``find`` / ``grep``) — the same one container / remote sandbox ``bash`` uses.
The **shared logic stays host-side above the seam**: containment is backend-agnostic path math
(:func:`_resolve_logical` — a ``PurePosixPath`` fold that rejects ``..`` escapes on the *logical*
Workspace root, never host ``Path.resolve``, since a modal path is not a host path), and read's
numbering/truncation, edit's search/replace, glob's matching, and grep's rendering are the **same**
code both modes call — so a sandbox result reads identically to a host one. Containment is **layered**:
a real-filesystem backend (docker's shared mount) additionally resolves symlinks *physically* below the
seam and raises :class:`~decode.sandbox.executor.WorkspaceEscape` (an :class:`OSError`, rendered here by
:func:`_bridge`) so a symlink planted in the Workspace can't be followed onto the host — string math
alone can't see a symlink. ``none`` mode is the direct-pathlib path, byte-identical to before (the seam
yields ``None``, so it is never engaged).

**Sync, not async.** Filesystem access here is local and the tool layer runs **sequentially**
in v1 (ADR-0002 §7), so there is no concurrency to win back by going async; Pydantic AI already
runs a sync tool in a worker thread. Keeping these sync keeps the code readable (the
network/DB-only "async-for-IO" rule from AGENTS.md does not bite on a single local file read).
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
    # Typing only: a runtime import would pull the sandbox executor module into the ``none`` path and
    # break its laziness (ADR-0012 §9). ``from __future__ import annotations`` keeps this a string.
    from decode.sandbox.executor import SandboxBackend

logger = logging.getLogger(__name__)

READ_TOOL_NAME = "read"
GLOB_TOOL_NAME = "glob"
GREP_TOOL_NAME = "grep"
WRITE_TOOL_NAME = "write"
EDIT_TOOL_NAME = "edit"

# The two mutating file tools (task 007): NOT read-only, gated, always asked. Tagged here so the
# registry stays a single declaration site and M3's read-only auto-allow never touches them.
FILE_TOOLS_MUTATING: dict[str, bool] = {
    WRITE_TOOL_NAME: False,
    EDIT_TOOL_NAME: False,
}

# A UTF-8 byte-order mark. Some editors prepend it; we strip it before matching and restore it
# verbatim on write so an ``edit`` never silently drops (or adds) a BOM.
_UTF8_BOM = "﻿"

# Passive Diagnostics Enricher (task 053, ADR-0007). The LSP ``DiagnosticSeverity.Error`` value — the
# *only* severity the enricher surfaces inline (warnings/info/hints are dropped; the active ``lsp``
# tool is the full-severity query surface).
_LSP_ERROR_SEVERITY = 1
# Bound the appended block so a flood of errors can't blow up the tool result; a ``(+K more)`` tail
# names the remainder.
_LSP_DIAGNOSTICS_LIMIT = 10


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


def _active_backend(cwd: Path) -> SandboxBackend | None:
    """The active session's sandbox backend for byte transport, or ``None`` in ``none`` mode (§4).

    The file-tool seam: a thin indirection over :func:`decode.tools.bash.active_backend` (imported
    lazily so the ``none`` path never pulls in the sandbox package) — mirroring how the LSP enricher
    reaches its service. ``None`` means "no seam engaged" → the direct-pathlib path. This is the one
    function tests patch to inject a fake backend and exercise the sandbox routing hermetically.

    **Never-crash contract.** Reaching the backend *creates* the sandbox on first touch, which can fail
    (a bad ``SANDBOX_IMAGE``, a daemon that died mid-session — the task-071 preflight only checks daemon
    reachability, not image validity, and a file op can be the first sandbox touch). A create failure is
    caught and rendered as a model-readable :class:`pydantic_ai.ModelRetry` — the same never-crash
    contract ``bash`` upholds (its executor renders an exit-125 ``ExecResult``). It is **not** downgraded
    to ``None``: falling through to host pathlib on the launch cwd would be a second escape.
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

    The op-level never-crash boundary: the file tools are sync (Pydantic AI runs them in a worker
    thread), so they bridge to the async backend via :func:`anyio.from_thread.run`. This wraps that
    bridge so an infra failure below the seam becomes a model-readable :class:`pydantic_ai.ModelRetry`
    instead of a raw traceback — mirroring how ``bash.run`` renders a backend failure as an exit-125
    result. The op's *own* model-facing errors (a missing file, a ``..`` escape rendered upstream) are
    :class:`~pydantic_ai.ModelRetry`\\ s and pass straight through. A :class:`WorkspaceEscape` surfacing
    from a real-fs backend's physical containment (:meth:`DockerBackend._path`) is an :class:`OSError`,
    so it is caught here by base class and rendered — files.py never imports it, keeping the ``none`` path
    free of any sandbox import (ADR-0012 §9).
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

    Backend-agnostic path math — **never** host ``Path.resolve`` (a modal Workspace path is not a host
    path): fold ``.`` / ``..`` in ``raw`` against the logical Workspace root and reject anything that
    escapes it (a ``..`` climbing above the root, or an absolute path). This is the deferred ``..``
    containment (a 079 Tester note) landing here, shared by **both** backends above the seam — the
    docker / modal file ops receive an already-validated logical path. Returns the path relative to the
    Workspace root (e.g. ``"sub/f.txt"``; ``""`` for the root). Raises :class:`pydantic_ai.ModelRetry`
    (not a crash) on an escape, the same refusal :func:`_resolve_in_cwd` gives in ``none`` mode — so a
    model that wanders out of tree is corrected identically in either mode.
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

    The rendering tail both the ``none`` (direct-pathlib) and sandbox (backend ``read_bytes``) paths call,
    so a sandbox read reads **byte-identically** to a host read: 1-indexed ``cat -n`` numbering for the
    ``offset`` / ``limit`` window, then the safety cap through :mod:`decode.tools.truncate` with the
    overflow spill note. An ``offset`` past end-of-file is a model-readable :class:`pydantic_ai.ModelRetry`.
    """
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


async def _sandbox_read_content(backend: SandboxBackend, path: str, rel: str) -> str:
    """Read + decode the logical Workspace path ``rel`` via the backend seam (ADR-0012 §4).

    The sandbox byte transport for :func:`read`: ``stat`` for the same missing / is-a-directory
    :class:`pydantic_ai.ModelRetry`\\ s ``none`` mode raises, then ``read_bytes`` + UTF-8 decode. The
    shared :func:`_render_numbered` does the numbering/truncation above the seam. Runs on the event loop
    (the sync tool bridges here via :func:`anyio.from_thread.run`); a raised ``ModelRetry`` propagates
    back through the bridge, so the model sees the same error whichever backend is active.
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


# The backend ``exec`` glob runs to enumerate the Workspace's files (paths only — never the tree's
# contents, ADR-0012 §4). The pattern is matched host-side by :func:`_glob_match` (shared logic above
# the seam), which reproduces ``pathlib.Path.glob`` exactly (verified on 3.12), so a sandbox glob lists
# the same files a host glob would. ``ponytail:`` ``find -type f`` skips symlinks (``none``-mode
# ``Path.glob`` follows in-tree ones) and enumerates the whole tree — fine for a repo clone; a
# ``find``-side ``-name`` prefilter is the upgrade path if a huge Workspace ever makes this a cost.
_FIND_ALL_FILES = "find . -type f"


async def _sandbox_glob(backend: SandboxBackend, pattern: str) -> list[str]:
    """List Workspace files matching ``pattern`` via a backend ``find`` exec (ADR-0012 §4).

    Execs ``find . -type f`` in ``/workspace`` to enumerate every file (relative paths), then filters
    host-side with :func:`_glob_match` — the shared matcher that mirrors ``Path.glob`` — and sorts, so
    the result is the same sorted relative-path list ``none`` mode's ``base.glob`` returns. Runs on the
    event loop (the sync tool bridges here).
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

    The shared, backend-agnostic matcher (verified against :meth:`pathlib.Path.glob` on 3.12): the
    pattern and path are split on ``/`` and matched segment-by-segment — each non-``**`` segment by
    :func:`fnmatch.fnmatch` against **one** path component (``*`` never crosses ``/``), and ``**`` against
    zero or more components (so ``**/*.py`` matches both top-level and nested ``.py``). This is what gives
    the sandbox ``glob`` output-parity with the host implementation.
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

    The rendering tail both the ``none`` (Python-``re`` per line) and sandbox (backend ``grep`` exec)
    paths call, so a sandbox grep reads **byte-identically** to a host grep: the hits joined and passed
    through the safety cap (:mod:`decode.tools.truncate`) with the overflow spill note.
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

    The search **executes in the sandbox** (never downloading the tree's contents, ADR-0012 §4), with
    the same scope resolution as ``none`` mode: a single ``path`` (``stat``-checked for the same "no such
    file to search" :class:`pydantic_ai.ModelRetry`), else the files matching ``glob`` (resolved via
    :func:`_sandbox_glob` for exact ``Path.glob`` file-scope parity), else the whole tree (a recursive
    ``grep -r``). Hits are stripped of the ``./`` prefix and sorted by ``(path, lineno)`` to reproduce
    ``none`` mode's sorted-candidate, ascending-line order.

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


# grep flags for both scopes: -r recursive (recursive scope only), -H force the filename prefix (so a
# single file still renders ``path:lineno:line``), -n line numbers, -I skip binary files (``none`` mode
# skips undecodable ones), -E extended regex, -e so a pattern starting with ``-`` is not misread. ``--``
# ends the options so a path/pattern starting with ``-`` is safe. Pattern + paths are ``shlex.quote``d.
def _grep_recursive_command(pattern: str) -> str:
    """The recursive ``grep`` command for the whole-Workspace scope (``none`` mode's ``**/*`` default)."""
    return f"grep -rHnI -E -e {shlex.quote(pattern)} -- ."


def _grep_files_command(pattern: str, files: list[str]) -> str:
    """The ``grep`` command scoped to an explicit ``files`` list (a single ``path`` or a ``glob`` scope)."""
    quoted_files = " ".join(shlex.quote(f) for f in files)
    return f"grep -HnI -E -e {shlex.quote(pattern)} -- {quoted_files}"


def _parse_grep_output(raw: str) -> list[str]:
    """Parse ``grep -Hn`` stdout into ``path:lineno:line`` hits: strip ``./`` and sort by ``(path, lineno)``.

    The sort reproduces ``none`` mode's order (sorted candidates, ascending line): the path is sorted by
    its ``PurePosixPath`` parts (matching ``none`` mode's ``Path`` sort) and then the numeric line.
    """
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

    The sandbox byte transport for :func:`write`: ``stat`` for the same is-a-directory
    :class:`pydantic_ai.ModelRetry` ``none`` mode raises, then ``write_bytes`` (the backend creates
    missing parents — the one directory side effect ``write`` has). The LSP enrichment stays on the
    worker thread in :func:`write` (it bridges to the LSP service itself), so it is *not* run here.
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
        # Decode raw bytes (not ``read_text``): universal-newline translation would collapse
        # the file's CR/CRLF on read, so we could never detect and restore the original style.
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

    The search/replace core both the ``none`` (pathlib) and sandbox (backend ``read_bytes`` / ``write_bytes``)
    paths call, so an edit behaves **byte-identically** in either mode: strip a leading UTF-8 BOM, detect
    the CRLF / CR / LF style, LF-normalize for matching, apply :func:`_replace_unique` (exact-then-fuzzy,
    raising the same ambiguous / not-found :class:`pydantic_ai.ModelRetry`), then restore the original BOM
    + line-ending style. Returns the new full text (the caller writes it back atomically / via the seam).
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

    The sandbox byte transport for :func:`edit`: ``stat`` for the same missing-file
    :class:`pydantic_ai.ModelRetry` ``none`` mode raises, ``read_bytes`` (raw bytes, not text — the
    shared :func:`_apply_edit` needs the untranslated newlines to restore the style), the shared
    transform, then ``write_bytes`` of the new full text. The LSP enrichment stays on the worker thread
    in :func:`edit`, so it is *not* run here.
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

    The **passive** channel: the byte write already happened and was approved, so this surfaces the
    just-written file's *errors* (LSP severity ``1``) inline — like opencode's "LSP errors detected,
    please fix" — so the model corrects them without a user nudge. ``base`` (the exact ``Wrote …`` /
    ``Edited …`` string) is returned **unchanged** unless there are errors, in which case the block is
    appended as ``f"{base}\\n\\n{summary}"``.

    Best-effort and silent: returns ``base`` untouched when the feature/setting is off, the file is
    not ``.py``, the server is unavailable, the file is clean, or it has only warnings — and it
    **swallows every exception**, so the enricher can never change or break the write/edit return or
    the file write. No extra permission gate: it rides the write/edit approval already granted.

    **Sandbox posture (ADR-0012 §7).** In ``none`` + ``docker`` the enricher runs: ``ty`` is host-side and
    ``cwd`` is a real host path it can open (``none`` = the repo tree; ``docker`` = the live bind mount, so
    the just-written file is on disk for ``ty``). In ``modal`` it is **best-effort-disabled** — ``ty``
    cannot reach the remote Workspace filesystem — so this returns ``base`` untouched (ADR-0007's
    best-effort posture; ty-inside-the-sandbox is the recorded upgrade path).
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

    Keeps only LSP errors (``severity == 1``) — warnings / info / hints are dropped (the active
    ``lsp`` tool is the full-severity query surface). Returns ``None`` for ``None`` input (server
    unavailable), an empty list (clean file), or a list with no errors. Otherwise a server-named
    header (``LSP diagnostics (<server>) — fix these:``) followed by up to
    :data:`_LSP_DIAGNOSTICS_LIMIT` ``  line:column  message`` lines, with a ``  (+K more)`` tail when
    truncated.
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
