"""The model-callable ``agent`` fan-out tool + the in-process Explore-subagent runner.

``agent(prompts)`` spawns ONE read-only **Explore Subagent** per prompt, concurrently, and folds ONE
labelled aggregate back (ADR-0017 §1). Each child is the *same* Pydantic-AI Agent re-entered via a
nested ``agent.run()`` with fresh, narrowed deps (``active_agent=explore``, gate in BYPASS, no-op
event sink, deny resolvers), so ``prepare=`` collapses the child's toolset to ``read/glob/grep/lsp``
and recursion is structurally impossible. Deterministic guards run BEFORE any child spawns (empty
list, width cap, and the per-prompt SUBSTANCE guard of §3 — a lazy prompt is nagged back to the
parent model via ``ModelRetry``, costing one retry leg and no child); the harness then
``asyncio.gather``s the children, each attempt taking the per-loop semaphore
(``subagent_max_parallel`` — a CONCURRENCY ceiling, distinct from the width cap).
A child that raises still gets its section, carrying a failure note: partial results beat an
exception that discards the siblings. Every child's report is truncated to a SHARED budget
(``subagent_result_max_bytes // len(prompts)``), so the fold's cost is width-independent. Three
seams: the set-once main-agent seam (mirrors bash's ``_EXECUTOR``), the per-running-loop fan-out
semaphore, and the read-only child deps. Child transcripts stay ephemeral and child usage is never
threaded into the parent's. See ADR-0017 §1-2,4-6 and ADR-0013 §1,5-10.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import TYPE_CHECKING

from pydantic_ai import DeferredToolRequests, ModelRetry, RunContext, UsageLimits

from decode.agent.deps import AgentDeps
from decode.config.settings import settings
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.types import PermissionMode

if TYPE_CHECKING:
    # Typing only — neither is needed at runtime.
    from pydantic_ai import Agent

    from decode.entities import events

logger = logging.getLogger(__name__)

AGENT_TOOL_NAME = "agent"

# The ONE subagent persona that ships (ADR-0013 §3): a read-only Explore child.
_SUBAGENT_PERSONA = "explore"

# Widest fan-out one ``agent`` call may request (ADR-0017 §2). A module constant, NOT a setting:
# least mechanism for a number nobody tunes. Distinct from ``settings.subagent_max_parallel`` (the
# CONCURRENCY ceiling) — a 6-wide fan-out under a cap of 4 runs 4 children, then the last 2.
# Stated as a literal "6" in the tool docstring too (a docstring cannot interpolate, and the model
# cannot resolve a Python constant's name); a unit test pins the two together.
MAX_FANOUT_PROMPTS = 6

# The per-tool retry budget the registry gives ``agent``: pydantic-ai's default is 1, so two
# consecutive ``ModelRetry`` nags would abort the run with ``UnexpectedModelBehavior`` (ADR-0017 §3).
AGENT_TOOL_RETRIES = 3

# --- The substance guard (ADR-0017 §3) --------------------------------------------------------
#
# A one-word spawn prompt ("explore") produces a useless child, so quality is enforced on the way
# IN — deterministically, with ZERO extra LLM calls (no scoring model, no I/O, no network): pure
# string inspection over the prompt the parent model already wrote.
#
# The guard has exactly ONE rejection criterion: a SUBSTANCE FLOOR, a word count below
# MIN_PROMPT_WORDS. That is what catches "explore" / "explore the repo" / "look around" — the
# failure the guard exists to prevent. Nobody briefs a colleague on a codebase in six words.
#
# It is deliberately NOT a grader of prompt quality. The three-part shape (QUESTION + SCOPE +
# REPORT) is COACHING and lives in the tool description, where the model reads it BEFORE writing;
# it is not a rejection predicate. An earlier revision made it one — an AND-gate over three fuzzy
# keyword sets — and it false-rejected 6 of 8 realistic briefs, because the false-reject
# probability COMPOUNDS across three independent fuzzy tests: "note", "describe", "walk through",
# "break down", "give me a table" are all perfectly good phrasings that no closed word list has,
# and every list you widen invites the next miss. It never converges. A permissive OR over the same
# three signals (reject only if NONE is present) was tried next and still false-rejected a good
# 17-word brief ("Tell me every place the harness shells out to git ... and quote the exact
# commands" — no "?", no path token, no keyword), so it was dropped too. The floor stands alone.
#
# This is the bias the guard MUST have, because the two errors are NOT symmetric:
#   * a false ACCEPT merely restores the pre-guard status quo (one weak child report);
#   * a false REJECT actively breaks a run — it burns a model turn and eats AGENT_TOOL_RETRIES.
# So: when in doubt, ACCEPT. The floor stops SHORT gaming, not PADDED gaming — keyword salad or
# rambling stretched past the floor gets through, and that is the accepted trade, not an oversight.
MIN_PROMPT_WORDS = 8

# The one rejection reason — the ModelRetry's vocabulary, and the tests'. It is TRUE of every
# prompt it is attached to: we never enumerate individually-absent parts (that produced a nag that
# lied, telling the model to add a scope it had already given).
_TERSE = f"too terse — give more detail (aim for at least {MIN_PROMPT_WORDS} words)"


def _faults(prompt: str) -> list[str]:
    """Every reason the guard rejects ``prompt`` — today, exactly one: the substance floor.

    Empty list == the prompt is accepted. Deterministic and pure — same string in, same list out;
    no LLM, no I/O, no network, no clock. A list (not a bool) because it is also the nag's clause
    list, and because the ModelRetry must say WHAT is wrong, not merely that something is.
    """
    if len(prompt.split()) < MIN_PROMPT_WORDS:
        return [_TERSE]
    return []


def _check_substance(prompts: list[str]) -> None:
    """Raise :class:`ModelRetry` naming every under-specified prompt BY INDEX and what it lacks.

    Whole-call rejection: one lazy angle nags the entire call, so no child spawns and no semaphore
    slot is consumed — the parent model rewrites the offenders and calls the tool again. Indices are
    1-based, matching the ``## Subagent i`` section labels the model already sees.
    """
    problems = [
        f'- Prompt {index} ("{prompt}"): {"; ".join(faults)}.'
        for index, prompt in enumerate(prompts, start=1)
        if (faults := _faults(prompt))
    ]
    if not problems:
        return
    raise ModelRetry(
        "No subagent was spawned: some prompts are under-specified. Every prompt must carry the "
        "QUESTION to answer, the SCOPE to search (a directory, file, or glob pattern to start "
        "from), and WHAT THE REPORT MUST CONTAIN.\n"
        + "\n".join(problems)
        + "\nRewrite the prompts above and call the agent tool again."
    )


# Set-once module seam holding the running Agent (mirrors bash's ``_EXECUTOR``); installed by
# ``build_agent`` via :func:`set_main_agent`, so children reuse the parent's model + HTTP client.
_MAIN_AGENT: Agent[AgentDeps, str | DeferredToolRequests] | None = None

# One semaphore per running event loop, sized to ``subagent_max_parallel``. Keyed by the loop
# (weakly) because an asyncio primitive binds to the loop it is first awaited on — a single global
# semaphore would bind to the wrong loop under Kitaru's per-call loops.
_SEMAPHORES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def set_main_agent(agent: Agent[AgentDeps, str | DeferredToolRequests]) -> None:
    """Install the running Agent as the subagent-spawn seam (ADR-0013 §6).

    Called once by :func:`decode.agent.factory.build_agent` after ``register_tools`` +
    ``_register_instructions``, so the ``agent`` tool re-enters *this* Agent for every child. It
    simply overwrites the module reference — a later ``build_agent`` (a fresh REPL / a headless
    flow) replaces it with its own Agent.
    """
    global _MAIN_AGENT
    _MAIN_AGENT = agent


def _require_main_agent() -> Agent[AgentDeps, str | DeferredToolRequests]:
    """Return the installed main Agent, or raise a clear misconfiguration error (ADR-0013 §6)."""
    if _MAIN_AGENT is None:
        raise RuntimeError(
            "the agent tool has no main Agent to spawn a subagent from — build_agent() must call "
            "set_main_agent(agent) before a run (mirrors bash's executor seam)."
        )
    return _MAIN_AGENT


def reset_main_agent() -> None:
    """Clear the main-agent seam — test hermeticity, mirroring ``bash.reset_executor`` (ADR-0013 §6)."""
    global _MAIN_AGENT
    _MAIN_AGENT = None


def _semaphore() -> asyncio.Semaphore:
    """Return this running loop's child-fan-out semaphore, building it once per loop (ADR-0013 §7)."""
    loop = asyncio.get_running_loop()
    existing = _SEMAPHORES.get(loop)
    if existing is not None:
        return existing
    created = asyncio.Semaphore(settings.subagent_max_parallel)
    _SEMAPHORES[loop] = created
    return created


def _reset_semaphores() -> None:
    """Drop every cached per-loop semaphore — test hermeticity (so a re-sized cap takes effect)."""
    _SEMAPHORES.clear()


def _silent_emit(event: events.Event) -> None:
    """The child's event sink: children are silent in the TUI, so their events only log (ADR-0013 §8)."""
    logger.debug("subagent event (silent): %s", type(event).__name__)


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """Deny safety-net for a child's ``resolve_permission`` — never reached, a read-only child
    never resolves to an ASK; denying is the safe default for an unattended child (ADR-0013 §5)."""
    logger.debug("subagent permission resolver denying tool=%s", request.tool_name)
    return PermissionDecision.deny(reason="A subagent runs read-only with no interactive approver.")


async def agent(ctx: RunContext[AgentDeps], prompts: list[str]) -> str:
    """Spawn one read-only Explore subagent per prompt, in parallel, and return their reports.

    The subagents run concurrently and can only read code (read/glob/grep/lsp). Their reports come
    back as ONE labelled document — one `## Subagent i` section per prompt, in the order you listed
    them — for you to synthesize.

    Write each prompt as a self-contained briefing for a colleague who cannot see this conversation.
    Every prompt MUST carry three things:
      1. the QUESTION to answer;
      2. the SCOPE to search — the directories, files, or glob patterns to start from;
      3. WHAT THE REPORT MUST CONTAIN — the findings you need back, with file:line evidence.

    Give each prompt a DISTINCT angle: for a broad question like "explore the repo", give at least
    3 DISTINCT angles (e.g. entry points, data flow, tests) rather than one vague prompt. A single
    focused question is a one-element list. At most 6 prompts per call.

    Good: "How does the permission gate decide allow/ask/deny? Search src/decode/permissions/ and
    report the decision path with file:line evidence."
    Bad: "explore the repo" — no question, no scope, nothing said about the report.
    """
    if not prompts:
        raise ModelRetry(
            "The agent tool needs at least one exploration prompt. Call it again with "
            "prompts=[<one prompt per angle you want investigated>]."
        )
    if len(prompts) > MAX_FANOUT_PROMPTS:
        raise ModelRetry(
            f"You asked for {len(prompts)} subagents; the limit is {MAX_FANOUT_PROMPTS} per call. "
            f"Consolidate your angles into at most {MAX_FANOUT_PROMPTS} prompts and call the agent "
            "tool again."
        )
    # Quality on the way IN (ADR-0017 §3): a lazy prompt is nagged BEFORE any child spawns, so an
    # under-specified angle costs one retry leg — never a child run, never a semaphore slot.
    _check_substance(prompts)

    # The SHARED context budget (ADR-0017 §6): the fold costs ~subagent_result_max_bytes at ANY
    # width, so a wide fan-out is a free default rather than a tax on the parent's context.
    child_max_bytes = settings.subagent_result_max_bytes // len(prompts)
    logger.debug("fanning out %d explore subagents (%d bytes each)", len(prompts), child_max_bytes)

    sections = await asyncio.gather(
        *(_spawn_child(ctx, prompt, max_bytes=child_max_bytes) for prompt in prompts)
    )
    # Labelled concatenation, NO synthesis LLM call — the parent model is the synthesizer (§5).
    return "\n\n".join(
        f'## Subagent {index} — "{prompt}"\n\n{section}'
        for index, (prompt, section) in enumerate(zip(prompts, sections, strict=True), start=1)
    )


async def _spawn_child(ctx: RunContext[AgentDeps], prompt: str, *, max_bytes: int) -> str:
    """Run ONE Explore child on ``prompt`` and return its section body (ADR-0013 §5,7,8).

    Builds FRESH, narrowed deps — the parent's ``cwd`` + ``harness_home`` (the child's read scope), a
    no-op event sink (silent in the TUI), a fresh :class:`~decode.permissions.gate.PermissionGate`, a
    fresh empty ``task_store``, the headless deny resolvers, and ``active_agent=explore`` — then, under
    the per-loop concurrency semaphore, re-enters the installed main Agent via a nested ``agent.run()``.
    The child is bounded by ``UsageLimits(request_limit=settings.subagent_max_requests)`` and does
    **not** thread ``usage=ctx.usage`` (so the parent's context gauge stays parent-only, ADR-0013 §7,10).

    Returns the child's final text truncated to ``max_bytes``. Never raises: a child that blows up
    (e.g. ``UsageLimitExceeded``) folds an explicit failure note into its own section instead of
    discarding its siblings' reports (ADR-0017 §5). Defensive: a
    :class:`pydantic_ai.DeferredToolRequests` output is theoretically impossible for a read-only child
    (its tools never raise :class:`pydantic_ai.ApprovalRequired`), so that case returns a short note
    rather than the raw object.
    """
    # Lazy imports: ``load_agent`` would form a tools -> agents -> tools cycle at module load.
    from decode.agents.loader import load_agent
    from decode.permissions.gate import PermissionGate
    from decode.tools.askuser import deny_user_question_resolver
    from decode.tools.truncate import truncate

    explore = load_agent(_SUBAGENT_PERSONA)
    # You may only spawn a *subagent*, never a primary (ADR-0013 §3).
    assert explore.subagent, f"the {_SUBAGENT_PERSONA!r} persona must declare subagent: true"

    child_deps = AgentDeps(
        cwd=ctx.deps.cwd,
        harness_home=ctx.deps.harness_home,
        emit=_silent_emit,
        # A FRESH gate in BYPASS: no harness loop resolves a child's deferred approval, so its
        # read-only tools must run inline (never raise ApprovalRequired) — ADR-0013 §2,5.
        gate=PermissionGate(mode=PermissionMode.BYPASS),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=deny_user_question_resolver,
        active_agent=explore,
        # ``task_store`` omitted: the default_factory gives each child a fresh empty list.
    )

    logger.debug("spawning explore subagent (prompt=%r)", prompt)
    try:
        async with _semaphore():  # per child ATTEMPT: the ceiling bounds a fan-out wider than it
            result = await _require_main_agent().run(
                prompt,
                deps=child_deps,
                usage_limits=UsageLimits(request_limit=settings.subagent_max_requests),
            )
    except Exception:
        # One broken child must not discard its siblings — fold an honest note into ITS section.
        logger.warning("explore subagent failed (prompt=%r)", prompt, exc_info=True)
        return "This subagent failed before producing a report."

    output = result.output
    if isinstance(output, DeferredToolRequests):
        # Impossible in practice (a read-only child never defers a tool), but never leak the object.
        logger.warning("subagent run resolved to DeferredToolRequests; returning a fallback note")
        return "The subagent could not complete its investigation."

    return truncate(
        str(output),
        max_lines=settings.max_output_lines,
        max_bytes=max_bytes,
    ).text
