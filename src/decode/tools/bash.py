"""The gated ``bash`` tool — run a shell command under the executor seam (ADR-0002 §7,10).

``bash`` lets the model run a shell command in the working directory and reports the command's
``stdout`` / ``stderr``, exit code, and whether it timed out. It is the mutating workhorse, so
it is **gated** exactly like ``write`` / ``edit`` (ADR-0002 §3): it raises
:class:`pydantic_ai.ApprovalRequired` until ``ctx.tool_call_approved`` is set, so the first leg
defers to the permission gate and a human approves *every* call. There is **no
dangerous-command classifier in v1** — the human-in-the-loop approval *is* the safety gate (an
OS sandbox + classifier are M8).

**How it runs.** Execution goes through a :class:`~decode.tools.exec.CommandExecutor` (default
:class:`~decode.tools.exec.LocalExecutor`) under ``ctx.deps.cwd`` — the same working-directory
contract as the file tools. The seam is what M8 swaps for a Docker / Modal sandbox; ``bash``
itself stays infra-agnostic.

**Timeout.** The wall-clock limit defaults to ``settings.bash_timeout_s`` and the model may
request a shorter one via the optional ``timeout`` argument; a model-supplied value is
**clamped to ``settings.bash_timeout_s``** (never longer) and must be positive. On timeout the
executor kills the command's whole process group (no orphaned children) and ``bash`` tells the
model the command timed out, returning whatever partial output was captured.

**Truncation.** Each stream is capped through :mod:`decode.tools.truncate` (2000 lines OR
50 KB, snapped to a line boundary); on overflow the full stream spills to a temp file whose
path rides back in the result so the model can read more without us shipping a wall of text
into the context window.
"""

from __future__ import annotations

import logging

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.tools.exec import CommandExecutor, ExecResult, LocalExecutor
from decode.tools.truncate import Truncated, truncate

logger = logging.getLogger(__name__)

BASH_TOOL_NAME = "bash"

# The default executor: a local asyncio subprocess. M8 swaps a sandboxed executor in here
# (Docker / Modal) behind the same CommandExecutor seam without touching this tool.
_EXECUTOR: CommandExecutor = LocalExecutor()


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
    if not ctx.tool_call_approved:
        logger.debug("bash requires approval (command=%r)", command)
        raise ApprovalRequired

    if not command.strip():
        raise ModelRetry("command is empty; provide a shell command to run.")
    timeout_s = _resolve_timeout(timeout)

    result = await _EXECUTOR.run(command, cwd=ctx.deps.cwd, timeout_s=timeout_s)
    logger.debug(
        "bash ran (exit=%d, timed_out=%s, command=%r)",
        result.exit_code,
        result.timed_out,
        command,
    )
    return _render(result, timeout_s=timeout_s)


def _resolve_timeout(timeout: float | None) -> float:
    """Resolve the effective timeout: default from settings, clamped to the configured max.

    ``None`` → ``settings.bash_timeout_s``. A model-supplied value is clamped to that maximum
    (the human-approved default is the ceiling — the model cannot extend its own leash) and
    must be positive (a non-positive timeout is a model-correctable :class:`ModelRetry`).
    """
    if timeout is None:
        return settings.bash_timeout_s
    if timeout <= 0:
        raise ModelRetry("timeout must be a positive number of seconds.")
    return min(timeout, settings.bash_timeout_s)


def _render(result: ExecResult, *, timeout_s: float) -> str:
    """Render an :class:`ExecResult` into the model-facing reply (status + truncated streams).

    A header line states the exit code (and flags a timeout); each non-empty stream is appended
    under a labelled section, truncated through :mod:`decode.tools.truncate` with an overflow
    notice pointing at the spill file. Empty streams are omitted so the model is not handed
    blank sections.
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
    return "\n\n".join(sections)


def _stream_section(label: str, content: str) -> str | None:
    """Format one captured stream as a labelled, truncated section (``None`` if empty).

    The stream is capped through :mod:`decode.tools.truncate` (2000 lines / 50 KB); on overflow
    a notice naming the spill-file path is appended so the model can read the full stream.
    """
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
