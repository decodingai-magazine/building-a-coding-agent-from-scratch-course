"""The gated ``bash`` tool — run a shell command under the executor seam (ADR-0002 §7,10).

Gated like ``write`` / ``edit`` (ADR-0002 §3): no dangerous-command classifier — the
human-in-the-loop approval is the safety gate. Execution goes through a cached
:class:`~decode.tools.exec.CommandExecutor` selected by ``SANDBOX_MODE`` on first use — ``none``
keeps the host :class:`LocalExecutor` (byte-identical), ``docker`` / ``modal`` lazily swap in the
sandbox executor (ADR-0011 §4); sandbox semantics reach the model via the tool description
(:func:`bash_description`). A model-supplied ``timeout`` is clamped to ``settings.bash_timeout_s``
(never longer); on timeout the whole process group is killed and partial output returned. Each
stream is capped through :mod:`decode.tools.truncate` (2000 lines OR 50 KB, line-snapped) with an
overflow spill file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.tools.approval import needs_approval
from decode.tools.exec import CommandExecutor, ExecResult, LocalExecutor
from decode.tools.truncate import Truncated, truncate

if TYPE_CHECKING:
    # Typing-only: a runtime import would break the ``none`` path's sandbox laziness (ADR-0012 §9).
    from decode.sandbox.executor import SandboxBackend

logger = logging.getLogger(__name__)

BASH_TOOL_NAME = "bash"

# The cached executor behind the CommandExecutor seam. Starts as the ``none``-mode LocalExecutor
# and on FIRST use is replaced via ``select_executor(settings.sandbox_mode)`` for docker/modal —
# a lazy swap so ``none`` never imports the sandbox package; the mode is read once per session
# (ADR-0011 §1,4). ``_executor_selected`` memoizes selection (a container/sandbox is created once,
# not per call); ``_EXECUTOR`` stays a live, patchable object so a test's ``_EXECUTOR.run`` patch
# survives the getter. ``reset_executor`` clears the memo; ``close_executor`` reaps on teardown.
_EXECUTOR: CommandExecutor = LocalExecutor()
_executor_selected = False

# The ONE model-facing paragraph appended to the ``bash`` tool description in a sandbox mode
# (ADR-0012 §2 fresh-exec): docker AND modal share it (one fresh-exec SandboxExecutor shape);
# ``none`` appends nothing (byte-identical). Composed by :func:`bash_description`, installed via
# the registry's ``prepare=``.
_SANDBOX_DESCRIPTION_SUFFIX = (
    "Sandbox: commands run inside an isolated sandbox, not on the host machine. The working directory "
    "/workspace is the isolated Workspace — a git clone of your repo when one was supplied "
    "(--repo/SANDBOX_REPO), otherwise an empty scratch — and it is the SAME tree the "
    "read/write/edit/glob/grep tools operate on, so a file you create with bash is visible to those "
    "tools and vice-versa. The filesystem persists across bash calls (one sandbox per session), but "
    "each command runs as a fresh shell, so `cd` and `export` do NOT carry over between calls — use "
    "absolute paths or chain them in one call (e.g. `cd /workspace/app && <command>`). If a command "
    "times out, only that command is killed; the sandbox and its filesystem survive. stdout and stderr "
    "are captured as separate streams."
)


def _get_executor() -> CommandExecutor:
    """Return the cached executor, selecting it by ``SANDBOX_MODE`` on first use (ADR-0011 §1,4).

    ``none`` keeps the eager :class:`LocalExecutor` untouched (no sandbox import; a test's
    ``_EXECUTOR.run`` patch is preserved); ``docker`` / ``modal`` lazily import and memoize the
    sandbox executor so its container/sandbox is created once, not per command.
    """
    global _EXECUTOR, _executor_selected
    if not _executor_selected:
        _executor_selected = True
        mode = settings.sandbox_mode
        logger.info("[sandbox] mode=%s", mode)
        if mode != "none":
            # Lazy import: the ``none`` path never touches ``decode.sandbox`` (ADR-0011 §4).
            from decode.sandbox import select_executor

            _EXECUTOR = select_executor(mode)
    return _EXECUTOR


async def warm_executor(workspace: Path) -> None:
    """Eagerly start the selected sandbox backend at REPL launch (ADR-0011 §4; ADR-0012 §2).

    A **no-op in ``none`` mode** — it returns before touching the executor memo, so the plain
    REPL stays byte-identical (no selection, no log line, no sandbox import). Otherwise it runs
    the same lazy selection the first ``bash`` call would (sharing the memo) and awaits a
    duck-typed ``start(workspace)`` if present (the :class:`CommandExecutor` Protocol stays
    run-only). Failures propagate with the memo **kept** — the next ``bash`` retries from
    scratch.
    """
    if settings.sandbox_mode == "none":
        return
    executor = _get_executor()
    start = getattr(executor, "start", None)
    if start is not None:
        await start(workspace)


def reset_executor() -> None:
    """Clear the executor selection memo (no teardown) — test hermeticity (ADR-0011 §4).

    Restores the ``none``-mode :class:`LocalExecutor` and re-arms selection. Does **not** close
    a live sandbox executor (use :func:`close_executor`); it only drops the reference.
    """
    global _EXECUTOR, _executor_selected
    _EXECUTOR = LocalExecutor()
    _executor_selected = False


async def close_executor() -> None:
    """Tear down the cached sandbox executor (best-effort) and reset the seam (ADR-0011 §4).

    The memo is reset **first** (the seam is clean even if teardown raises), then a duck-typed
    async ``aclose`` / sync ``close`` is invoked. Safe no-op in ``none`` mode and idempotent;
    ``--rm`` (docker) / the modal ``timeout`` remain the crash backstops.
    """
    global _EXECUTOR, _executor_selected
    executor = _EXECUTOR
    _EXECUTOR = LocalExecutor()
    _executor_selected = False
    aclose = getattr(executor, "aclose", None)
    if aclose is not None:
        await aclose()
        return
    close = getattr(executor, "close", None)
    if close is not None:
        close()


async def export_executor() -> None:
    """Sweep the live sandbox Workspace back to the host **mid-session**, leaving it alive (ADR-0012 §5,8).

    The mid-session ``/ship`` hook: awaits a duck-typed async ``export`` when present (docker =
    bind-mount no-op; modal = tar sweep to the host ``.decode/sandbox``). Unlike
    :func:`close_executor` it neither resets the memo nor destroys the sandbox; safe no-op in
    ``none`` mode and when nothing was ever selected.
    """
    executor = _EXECUTOR
    export = getattr(executor, "export", None)
    if export is not None:
        await export()


def active_backend(cwd: Path) -> SandboxBackend | None:
    """Return the active session's **created** sandbox backend, or ``None`` in ``none`` mode (ADR-0012 §4).

    The file-tool half of the executor seam: ``none`` returns ``None`` **before touching the
    memo** (no selection, no log line, no sandbox import — the file tools stay direct-pathlib,
    byte-identical). In ``docker`` / ``modal`` it shares ``bash``'s ``_EXECUTOR`` memo (the same
    container / remote sandbox per session) and bridges to the executor's async duck-typed
    ``file_backend`` via :func:`anyio.from_thread.run` (the file tools run in a worker thread);
    an executor without one yields ``None``, so the seam stays optional.
    """
    if settings.sandbox_mode == "none":
        return None
    executor = _get_executor()
    file_backend = getattr(executor, "file_backend", None)
    if file_backend is None:
        return None
    return anyio.from_thread.run(file_backend, cwd)


def bash_description(base: str) -> str:
    """Compose the model-facing ``bash`` description for the active ``SANDBOX_MODE`` (ADR-0012 §2).

    ``none`` returns ``base`` **unchanged** (the caller detects the no-op and leaves the
    ``ToolDefinition`` untouched); ``docker`` AND ``modal`` append the same unified
    :data:`_SANDBOX_DESCRIPTION_SUFFIX` — one fresh-exec rule set regardless of backend.
    """
    if settings.sandbox_mode == "none":
        return base
    return f"{base}\n\n{_SANDBOX_DESCRIPTION_SUFFIX}"


async def bash(
    ctx: RunContext[AgentDeps],
    command: str,
    timeout: float | None = None,
) -> str:
    """Run a shell ``command`` in the working directory and report its result (ADR-0002 §7).

    ``command`` runs through a shell (pipes, redirects, ``&&`` all work) under
    ``ctx.deps.cwd``. ``timeout`` is an optional wall-clock limit in seconds; it defaults to
    ``settings.bash_timeout_s`` and is clamped to that maximum (a model cannot ask for longer).
    The reply states the exit code, notes a timeout if one happened, and includes each non-empty
    stream (truncated to 2000 lines / 50 KB with the full content spilled to a temp-file path on
    overflow).

    Gated (ADR-0002 §3): raises :class:`pydantic_ai.ApprovalRequired` until the call is
    approved — and *before* the command runs — so a denied call never executes anything.
    Returns a model-readable :class:`pydantic_ai.ModelRetry` for an empty command or a
    non-positive ``timeout`` so the model can correct itself instead of crashing the REPL.
    """
    if needs_approval(ctx):
        logger.debug("bash requires approval (command=%r)", command)
        raise ApprovalRequired

    if not command.strip():
        raise ModelRetry("command is empty; provide a shell command to run.")
    timeout_s = _resolve_timeout(timeout)

    result = await _get_executor().run(command, cwd=ctx.deps.cwd, timeout_s=timeout_s)
    logger.debug(
        "bash ran (exit=%d, timed_out=%s, command=%r)",
        result.exit_code,
        result.timed_out,
        command,
    )
    return _render(result, timeout_s=timeout_s)


def _resolve_timeout(timeout: float | None) -> float:
    """Resolve the effective timeout: default from settings, clamped to the configured max.

    A model-supplied value is clamped to ``settings.bash_timeout_s`` (the model cannot extend
    its own leash) and must be positive (else a model-correctable :class:`ModelRetry`).
    """
    if timeout is None:
        return settings.bash_timeout_s
    if timeout <= 0:
        raise ModelRetry("timeout must be a positive number of seconds.")
    return min(timeout, settings.bash_timeout_s)


def _render(result: ExecResult, *, timeout_s: float) -> str:
    """Render an :class:`ExecResult` into the model-facing reply (status + truncated streams).

    A header states the exit code (flagging a timeout); each non-empty stream is appended as a
    labelled, truncated section (empty streams omitted). A non-empty ``result.note`` is appended
    last; an empty ``note`` (every ``none``-mode result) leaves the output **byte-identical** to
    before the field existed (ADR-0011 §2).
    """
    if result.timed_out:
        header = (
            f"Command timed out after {timeout_s:g}s and was terminated "
            f"(exit code {result.exit_code}). Partial output below."
        )
    else:
        header = f"Exit code: {result.exit_code}."

    sections = [header]
    stdout_section = _stream_section("stdout", result.stdout)
    if stdout_section:
        sections.append(stdout_section)
    stderr_section = _stream_section("stderr", result.stderr)
    if stderr_section:
        sections.append(stderr_section)
    if result.note:
        sections.append(result.note)
    return "\n\n".join(sections)


def _stream_section(label: str, content: str) -> str | None:
    """Format one captured stream as a labelled, truncated section with a spill notice (``None`` if empty)."""
    if content == "":
        return None
    capped: Truncated = truncate(
        content, max_lines=settings.max_output_lines, max_bytes=settings.max_output_bytes
    )
    body = capped.text.rstrip("\n")
    if capped.truncated:
        body += (
            f"\n\n[{label} truncated to {settings.max_output_lines} lines / "
            f"{settings.max_output_bytes} bytes; full content at {capped.full_path}]"
        )
    return f"{label}:\n{body}"
