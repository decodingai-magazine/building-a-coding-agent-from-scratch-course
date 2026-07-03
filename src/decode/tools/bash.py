"""The gated ``bash`` tool — run a shell command under the executor seam (ADR-0002 §7,10).

``bash`` lets the model run a shell command in the working directory and reports the command's
``stdout`` / ``stderr``, exit code, and whether it timed out. It is the mutating workhorse, so
it is **gated** exactly like ``write`` / ``edit`` (ADR-0002 §3): it raises
:class:`pydantic_ai.ApprovalRequired` until ``ctx.tool_call_approved`` is set, so the first leg
defers to the permission gate and a human approves *every* call. There is **no
dangerous-command classifier in v1** — the human-in-the-loop approval *is* the safety gate (an
OS sandbox + classifier are M8).

**How it runs.** Execution goes through a :class:`~decode.tools.exec.CommandExecutor` under
``ctx.deps.cwd`` — the same working-directory contract as the file tools. Which executor is chosen
is now **live** (ADR-0011 §4): the cached ``_get_executor()`` seam selects by ``SANDBOX_MODE`` on
first use — ``none`` keeps the host :class:`~decode.tools.exec.LocalExecutor` (byte-identical to
before), ``docker`` / ``modal`` lazily swap in the sandbox executor from :mod:`decode.sandbox`.
``bash`` itself stays infra-agnostic; the model is told the active mode's semantics through the
mode-specific tool **description** (:func:`bash_description`, wired via the registry ``prepare=``).

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
from decode.tools.approval import needs_approval
from decode.tools.exec import CommandExecutor, ExecResult, LocalExecutor
from decode.tools.truncate import Truncated, truncate

logger = logging.getLogger(__name__)

BASH_TOOL_NAME = "bash"

# The cached executor ``bash`` runs commands through, behind the ADR-0002 ``CommandExecutor`` seam.
# It starts as the ``none``-mode :class:`LocalExecutor` (a host subprocess — byte-identical to M1) and,
# on the FIRST ``bash`` call, is *replaced* by ``select_executor(settings.sandbox_mode)`` when the mode
# is ``docker`` / ``modal`` (ADR-0011 §4) — a lazy swap so ``none`` never imports the sandbox package.
# The mode is read once and fixed for the session (ADR-0011 §1). ``_executor_selected`` is the memo
# guard: it makes the mode-selection run at most once (so a docker/modal executor is not rebuilt — and
# its container/sandbox not re-created — on every call). ``_EXECUTOR`` stays a live, patchable object at
# all times so a test can ``mocker.patch("decode.tools.bash._EXECUTOR.run", ...)`` (and ``none`` mode
# keeps the eager instance, never re-selecting it, so that patch survives the getter). ``reset_executor``
# clears the memo; ``close_executor`` reaps a sandbox executor on teardown.
_EXECUTOR: CommandExecutor = LocalExecutor()
_executor_selected = False

# The model-facing description paragraphs appended to the ``bash`` tool docstring per SANDBOX_MODE
# (ADR-0011 §4). ``none`` appends nothing (byte-identical). ``docker`` / ``modal`` tell the model the
# sandbox's live semantics so it is never surprised (docker's persistent shell + shared /workspace; the
# empty remote scratch with no local tree). Composed onto the base description by :func:`bash_description`
# and installed on the tool via the registry's ``prepare=`` callback.
_DOCKER_DESCRIPTION_SUFFIX = (
    "Sandbox (SANDBOX_MODE=docker): commands run in a persistent bash shell inside a local Docker "
    "container, with your project directory bind-mounted at /workspace (the shell's working "
    "directory). Because the shell persists across calls, `cd`, `export`, and installations "
    "(e.g. `pip install`) carry over from one bash call to the next. If a command times out it is "
    "killed and the shell is restarted, so its working directory resets to /workspace and its "
    "environment is cleared — but files already written to /workspace survive (they live on the "
    "mounted project directory). stdout and stderr are merged into a single stream in the command's "
    "own order."
)
_MODAL_DESCRIPTION_SUFFIX = (
    "Sandbox (SANDBOX_MODE=modal): commands run in a remote Modal sandbox, not on the local machine. "
    "The local project tree is NOT present in the sandbox, so to work with code you must fetch it "
    "yourself (e.g. `git clone` / `git fetch`) or generate it. Filesystem changes and installations "
    "(e.g. `git clone`, `pip install`) persist across bash calls, but each command runs as a fresh "
    "process, so shell `cd` and `export` do NOT carry over between calls — use absolute paths or chain "
    "them in one call (e.g. `cd /workspace/app && <command>`). The sandbox starts empty at /workspace."
)


def _get_executor() -> CommandExecutor:
    """Return the cached executor, selecting it by ``SANDBOX_MODE`` on first use (ADR-0011 §1,4).

    On the first call it reads ``settings.sandbox_mode`` once, logs it, and — for ``docker`` / ``modal``
    — lazily imports :func:`decode.sandbox.select_executor` and swaps the selected sandbox executor into
    the ``_EXECUTOR`` memo. For ``none`` it keeps the eager :class:`LocalExecutor` untouched, so the
    ``none`` path imports **no** sandbox module and a test's ``_EXECUTOR.run`` patch is preserved. Every
    later call returns the memoized executor (so a docker/modal container/sandbox is created once, not
    per command). Reset the memo with :func:`reset_executor`; reap it with :func:`close_executor`.
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


def reset_executor() -> None:
    """Clear the executor selection memo (no teardown) — test hermeticity (ADR-0011 §4).

    Restores the ``none``-mode :class:`LocalExecutor` default and re-arms selection so the next
    :func:`_get_executor` re-reads ``settings.sandbox_mode``. Does **not** close a live sandbox
    executor (use :func:`close_executor` for that); it only drops decode's reference to it.
    """
    global _EXECUTOR, _executor_selected
    _EXECUTOR = LocalExecutor()
    _executor_selected = False


async def close_executor() -> None:
    """Tear down the cached sandbox executor (best-effort) and reset the seam (ADR-0011 §4).

    Called on the interactive exit path (``tui/app.py``) and at headless-flow completion
    (``runtime/flow.py``) so a session's Docker container / Modal sandbox is reaped. The memo is reset
    **first** (so the seam is clean even if teardown raises), then — if the cached executor exposes an
    async :meth:`aclose` (the sandbox executors) or a sync ``close`` — it is awaited / called. A safe
    **no-op** in ``none`` mode (:class:`LocalExecutor` has neither method) and when nothing was ever
    selected. Idempotent: a second call finds the reset :class:`LocalExecutor` and no-ops. ``--rm``
    (docker) / the modal ``timeout`` remain the crash backstops if this is ever skipped.
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


def bash_description(base: str) -> str:
    """Compose the model-facing ``bash`` description for the active ``SANDBOX_MODE`` (ADR-0011 §4).

    ``none`` returns ``base`` **unchanged** (byte-identical to before this task — the caller detects the
    no-op and leaves the ``ToolDefinition`` untouched). ``docker`` / ``modal`` append the matching
    sandbox-semantics paragraph so the model is told the live rules (docker's persistent shell + shared
    ``/workspace``; the empty remote scratch with no local tree) instead of being surprised at runtime.
    """
    mode = settings.sandbox_mode
    if mode == "docker":
        return f"{base}\n\n{_DOCKER_DESCRIPTION_SUFFIX}"
    if mode == "modal":
        return f"{base}\n\n{_MODAL_DESCRIPTION_SUFFIX}"
    return base


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
    blank sections. A non-empty ``result.note`` (an out-of-band execution notice — e.g. the Docker
    sandbox reset its shell on timeout, ADR-0011 §2) is appended last; an empty ``note`` (every
    ``none``-mode :class:`LocalExecutor` result) leaves the output **byte-identical** to before the
    field existed.
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
