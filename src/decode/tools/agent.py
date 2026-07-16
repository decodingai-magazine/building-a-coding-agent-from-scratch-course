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
Quality is enforced on the way OUT too (§7): a BAD report — empty, or backed by ZERO tool calls (the
child answered from model memory) — buys the child EXACTLY ONE re-spawn with a nudge, and a second
bad report folds an explicit note. Two attempts, never three, so a broken child cannot eat the run.
A child that raises still gets its section, carrying a failure note: partial results beat an
exception that discards the siblings. Every child's report is truncated to a SHARED budget
(``subagent_result_max_bytes // len(prompts)``), so the fold's cost is width-independent — and the
harness-owned :data:`SYNTHESIS_FOOTER` (§9) is appended AFTER that truncation, on every aggregate,
telling the parent model to compile the N reports into ONE answer (prose + a text diagram). Three
seams: the set-once main-agent seam (mirrors bash's ``_EXECUTOR``), the per-running-loop fan-out
semaphore, and the read-only child deps. Child transcripts stay ephemeral and child usage is never
threaded into the parent's. See ADR-0017 §1-2,4-7,9 and ADR-0013 §1,5-10.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic_ai import DeferredToolRequests, ModelRetry, RunContext, UsageLimits
from pydantic_ai.messages import FunctionToolCallEvent, ModelResponse, ToolCallPart

from decode.agent.deps import AgentDeps, EventSink
from decode.config.settings import settings
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.types import PermissionMode

if TYPE_CHECKING:
    from collections.abc import AsyncIterable

    from pydantic_ai import Agent
    from pydantic_ai.agent import AgentRunResult, EventStreamHandler
    from pydantic_ai.messages import AgentStreamEvent

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
# IN — deterministically, with ZERO extra LLM calls: pure string inspection over the prompt the
# parent model already wrote.
#
# THE RULE, whole: reject a prompt iff its word count is under MIN_PROMPT_WORDS. A substance FLOOR,
# and nothing else — no keyword set gets a vote. The three-part shape (QUESTION + SCOPE + REPORT) is
# COACHING in the tool description, never a rejection predicate; when in doubt, ACCEPT, because a
# false accept merely costs one weak report while a false reject BREAKS a run (it burns a model turn
# and eats AGENT_TOOL_RETRIES). Two keyword-grader designs were built and demolished on exactly that
# asymmetry — ADR-0017 §3 records why, and the anti-whack-a-mole battery in
# ``tests/unit/decode/tools/test_agent.py`` pins the briefs they falsely rejected. Read the ADR
# before widening this.
MIN_PROMPT_WORDS = 8

# The one rejection reason — the ModelRetry's vocabulary, and the tests'. It is TRUE of every
# prompt it is attached to: we never enumerate individually-absent parts (that produced a nag that
# lied, telling the model to add a scope it had already given).
_TERSE = f"too terse — give more detail (aim for at least {MIN_PROMPT_WORDS} words)"


def _fault(prompt: str) -> str | None:
    """Why the guard rejects ``prompt``, or ``None`` if it is accepted (ADR-0017 §3).

    Deterministic and pure — same string in, same answer out; no LLM, no I/O, no network, no clock.
    """
    if len(prompt.split()) < MIN_PROMPT_WORDS:
        return _TERSE
    return None


def _check_substance(prompts: list[str]) -> None:
    """Raise :class:`ModelRetry` naming every under-specified prompt BY INDEX and what it lacks.

    Whole-call rejection: one lazy angle nags the entire call, so no child spawns and no semaphore
    slot is consumed — the parent model rewrites the offenders and calls the tool again. Indices are
    1-based, matching the ``## Subagent i`` section labels the model already sees.
    """
    problems = [
        f'- Prompt {index} ("{prompt}"): {fault}.'
        for index, prompt in enumerate(prompts, start=1)
        if (fault := _fault(prompt))
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


# --- The output contract: bad-report detection + exactly ONE retry (ADR-0017 §7) ----------------
#
# Quality on the way OUT, the mirror of the substance guard's quality on the way IN. A child report
# is BAD iff (i) it is empty/whitespace-only, or (ii) the child made ZERO tool calls — it answered
# from model memory instead of reading the code (the hallucination tell §8 pairs with). The
# defensive ``DeferredToolRequests`` output is BAD too, so it enters this machinery rather than
# short-circuiting to a note of its own. Deterministic: pure inspection of the child's own result,
# no scoring model, no extra LLM call.
#
# A bad child gets EXACTLY ONE re-spawn (same prompt + the nudge below), then gives up with a note.
# Two attempts, ever — never three: a broken child must not eat the run's budget in a retry loop.

# Appended to a bad child's ONE retry prompt. A MODULE CONSTANT, not a Settings field: least
# mechanism for a string nobody tunes per environment (same reasoning as MAX_FANOUT_PROMPTS). It
# must say WHAT was wrong and WHAT to do instead — a bare "try again" gives the child nothing to
# change, and would just buy the same empty report twice.
_RETRY_NUDGE = (
    "\n\nIMPORTANT: your previous report was unusable — it was empty, or it cited no code you "
    "actually read. Use your tools (read / glob / grep / lsp) to read the code FIRST, then report "
    "the finding with file:line evidence."
)

# The give-up note folded into a twice-bad child's section: honest UX — the parent model (and the
# human reading the transcript) sees WHICH angle produced nothing, and its siblings still fold.
_NO_USABLE_REPORT_NOTE = "The subagent returned no usable report."

# The other failure note (ADR-0017 §5): a child that RAISED. Distinct from a bad report — an
# exception is a transport/limit failure, and retrying it is an explicit non-goal of ADR-0017.
_CHILD_FAILED_NOTE = "This subagent failed before producing a report."


def _read_any_code(result: AgentRunResult[str | DeferredToolRequests]) -> bool:
    """Whether the child called ANY tool — a ``ToolCallPart`` in any ``ModelResponse`` of its transcript.

    ``AgentRunResult.all_messages()`` is the child's full transcript (pydantic-ai 1.95 ``run.py:461``).
    No tool call means the child never opened a file: whatever it reported came from model memory.
    """
    return any(
        isinstance(part, ToolCallPart)
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


def _usable_report(result: AgentRunResult[str | DeferredToolRequests]) -> str | None:
    """The child's usable report text, or ``None`` if the report is BAD (ADR-0017 §7).

    Deterministic and total: the three BAD shapes are the deferred output (a read-only child can
    never legitimately produce one), empty/whitespace-only text, and a report backed by no tool call.
    """
    output = result.output
    if isinstance(output, DeferredToolRequests):
        return None
    text = str(output)
    if not text.strip():
        return None
    if not _read_any_code(result):
        return None
    return text


# --- The Synthesis Footer (ADR-0017 §9) --------------------------------------------------------
#
# The aggregate is N raw reports; the ANSWER is one synthesis of them. This instruction is what
# turns the former into the latter, and the harness — not a persona — owns it, for three reasons:
#   * JUST-IN-TIME: it rides the tool result, so it costs zero tokens on the many turns that fan out
#     no children. In a persona prompt it would be paid for on EVERY turn of EVERY run.
#   * ONE PLACE: it lives beside the fold it describes, so it cannot drift out of sync with the
#     section format above — and a future persona author cannot forget to copy it into a new parent.
#   * LAST WORD: appended after the final section, it is the last thing the model reads before it
#     writes, which is where an instruction about the whole document belongs.
# A MODULE CONSTANT, like MAX_FANOUT_PROMPTS and _RETRY_NUDGE: least mechanism for a string nobody
# tunes per environment (no Settings field, no ``.env.example`` entry). Public (no underscore): it
# is part of the tool's model-facing contract, and the persona guard test asserts against it.
#
# ASCII/box-drawing is the DEFAULT diagram flavour, not a stylistic preference: decode's TUI is Rich
# in a terminal, which renders a Mermaid block as raw source — the model would emit a picture nobody
# can see. Mermaid is allowed ONLY where the structure is a genuine graph, i.e. where a monospaced
# box drawing would misrepresent it and raw source is still the lesser evil.
SYNTHESIS_FOOTER = (
    "\n\n---\n\n"
    "COMPILE the subagent reports above into ONE answer for the user — do not hand back the reports "
    "one by one, and do not answer from a single report alone. Reconcile what the subagents found, "
    "keep their file:line evidence, and say so explicitly if two of them disagree or if a section "
    "reports a failure.\n\n"
    "Your answer must contain BOTH:\n"
    "1. PROSE — what the structure is and how it works.\n"
    "2. A TEXT DIAGRAM of that structure, in ASCII / box-drawing characters. Your output is rendered "
    "in a TERMINAL, so the diagram must read as a diagram in plain monospaced text. Use a Mermaid "
    "block ONLY when what you found is a genuine graph (a real many-to-many dependency, a state "
    "machine) — a terminal renders Mermaid as RAW SOURCE, not as a picture."
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


def _make_child_emit(parent: AgentDeps, index: int) -> EventSink:
    """The child's sink: :func:`_silent_emit` by default, the PARENT's sink in Verbose Mode (§8 amendment).

    ``parent.verbose.enabled`` is read at EMIT time — never captured when the child is spawned —
    because the user presses Ctrl+O *while* a fan-out is running: the very next child event must
    obey the new state. A child's tool call rides the parent's sink tagged with ``child_index``
    (its 1-based prompt index), so decode's own renderer indents and labels it ``[child N]``.
    """

    def child_emit(event: events.Event) -> None:
        if not parent.verbose.enabled:
            _silent_emit(event)
            return
        if isinstance(event, events.ToolCallStarted):
            event = replace(event, child_index=index)
        parent.emit(event)

    return child_emit


def _make_child_stream_handler(emit: EventSink) -> EventStreamHandler[AgentDeps]:
    """Turn the child's run-event stream into decode ``ToolCallStarted`` events on ``emit``.

    A child runs as a nested ``agent.run()``, which does NOT pass through decode's own agent loop —
    so nothing would otherwise surface its tool calls at all. ``event_stream_handler`` is pydantic-ai's
    live seam for exactly that (``agent/abstract.py``): it hands us the same ``FunctionToolCallEvent``
    the parent loop maps, as the child makes each call. Attached on EVERY child (not only when
    verbose is on) — the toggle is decided downstream, at emit time, in :func:`_make_child_emit`;
    with it off these events end in ``_silent_emit`` and the TUI is byte-identical to before.
    Only the CALL is forwarded, never the result: a child's tool outputs are what its report already
    compresses, and panelling them would bury the parent's own output.
    """

    async def handler(_ctx: RunContext[AgentDeps], stream: AsyncIterable[AgentStreamEvent]) -> None:
        async for event in stream:
            if isinstance(event, FunctionToolCallEvent):
                call = event.part
                emit(
                    events.ToolCallStarted(
                        tool_call_id=call.tool_call_id,
                        name=call.tool_name,
                        args=call.args_as_json_str(),
                    )
                )

    return handler


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
        *(
            _spawn_child(ctx, prompt, index=index, max_bytes=child_max_bytes)
            for index, prompt in enumerate(prompts, start=1)
        )
    )
    # Labelled concatenation, NO synthesis LLM call — the parent model is the synthesizer (§5), and
    # the footer (§9) is how it is TOLD to be. Appended AFTER the sections, i.e. after every child's
    # report has already been truncated to its own budget above: the footer is harness overhead on
    # TOP of the ~16 KB of reports, never a bite out of a child's share — the fold's evidence must
    # not shrink because the instruction got longer. Always appended, one-element folds included.
    fold = "\n\n".join(
        f'## Subagent {index} — "{_label(prompt)}"\n\n{section}'
        for index, (prompt, section) in enumerate(zip(prompts, sections, strict=True), start=1)
    )
    return fold + SYNTHESIS_FOOTER


def _label(prompt: str) -> str:
    """``prompt`` as ONE line — the section heading's label (ADR-0017 §5).

    A real parent model writes a MULTI-LINE brief, because the tool description asks it for three
    parts ("QUESTION: …\\nSCOPE: …\\nWHAT THE REPORT MUST CONTAIN: …"). A Markdown heading ends at the
    first newline by spec, so embedding that verbatim spilled the label across lines and left its
    closing quote stranded in the paragraph below — a broken heading for whoever reads the raw
    transcript (session-log JSONL, a pasted ``decode run`` result). Collapsing the whitespace keeps
    every word and costs the reader nothing.

    NOT truncated: a heading is single-line but unbounded in length (renderers wrap it), the full brief
    is what lets a reader attribute a section to its angle, and the fan-out is capped at
    :data:`MAX_FANOUT_PROMPTS` labels anyway. RENDERING only — the CHILD is briefed with the model's
    original text, newlines and all (:func:`_spawn_child`).
    """
    return " ".join(prompt.split())


async def _spawn_child(
    ctx: RunContext[AgentDeps], prompt: str, *, index: int, max_bytes: int
) -> str:
    """Run ONE Explore child on ``prompt``, VALIDATE its report, and return its section body (§5,7).

    At most TWO attempts, ever. The first runs ``prompt`` as the model wrote it; if its report is BAD
    (:func:`_usable_report` — empty, or backed by no tool call), exactly one re-spawn runs
    ``prompt + _RETRY_NUDGE``. A second bad report gives up with :data:`_NO_USABLE_REPORT_NOTE`. The
    nudged prompt is HARNESS-authored, so it never re-enters the input substance guard (that guard
    coaches the MODEL about ITS prompts, pre-fan-out — nagging the model about a string the harness
    wrote would be nonsense).

    This whole cycle is PRIVATE to this child's gather slot: it holds no shared state, so a retry
    here cannot delay, reorder or corrupt a sibling's section — the aggregate stays in prompt order.
    Never raises: a child that blows up (e.g. ``UsageLimitExceeded``) folds :data:`_CHILD_FAILED_NOTE`
    instead of discarding its siblings' reports (an exception is a transport failure, NOT a bad
    report — ADR-0017 explicitly does not retry those). Whichever attempt's report wins is truncated
    to ``max_bytes`` — a retry's report is budget-capped exactly like a first attempt's.
    """
    from decode.tools.truncate import truncate  # lazy: mirrors the child-deps imports below

    try:
        report = await _run_attempt(ctx, prompt, index=index)
        if report is None:
            # BAD report → the ONE retry, with the nudge telling the child what to do differently.
            logger.warning(
                "subagent %d returned an unusable report; retrying once (last try)", index
            )
            report = await _run_attempt(ctx, prompt + _RETRY_NUDGE, index=index)
    except Exception:
        # One broken child must not discard its siblings — fold an honest note into ITS section.
        logger.warning("explore subagent %d failed (prompt=%r)", index, prompt, exc_info=True)
        return _CHILD_FAILED_NOTE

    if report is None:
        # Bad twice: give up. Two attempts is the cap — a broken child never eats the run's budget.
        logger.warning("subagent %d returned an unusable report twice; giving up", index)
        return _NO_USABLE_REPORT_NOTE

    return truncate(report, max_lines=settings.max_output_lines, max_bytes=max_bytes).text


async def _run_attempt(ctx: RunContext[AgentDeps], prompt: str, *, index: int) -> str | None:
    """ONE child attempt: its report text, or ``None`` if the report is BAD (ADR-0013 §5,7,8).

    Builds FRESH, narrowed deps — the parent's ``cwd`` + ``harness_home`` (the child's read scope), its
    own event sink (silent in the TUI unless Verbose Mode is on — :func:`_make_child_emit`, and
    ``index`` is what labels its lines ``[child N]``), a fresh :class:`~decode.permissions.gate.PermissionGate`, a
    fresh empty ``task_store``, the headless deny resolvers, and ``active_agent=explore`` — then, under
    the per-loop concurrency semaphore, re-enters the installed main Agent via a nested ``agent.run()``.
    The child is bounded by ``UsageLimits(request_limit=settings.subagent_max_requests)`` and does
    **not** thread ``usage=ctx.usage`` (so the parent's context gauge stays parent-only, ADR-0013 §7,10).
    A retry is just another attempt: fresh deps, a fresh semaphore acquisition, the same limits.
    """
    # Lazy imports: ``load_agent`` would form a tools -> agents -> tools cycle at module load.
    from decode.agents.loader import load_agent
    from decode.permissions.gate import PermissionGate
    from decode.tools.askuser import deny_user_question_resolver

    explore = load_agent(_SUBAGENT_PERSONA)
    # You may only spawn a *subagent*, never a primary (ADR-0013 §3).
    assert explore.subagent, f"the {_SUBAGENT_PERSONA!r} persona must declare subagent: true"

    child_emit = _make_child_emit(ctx.deps, index)
    child_deps = AgentDeps(
        cwd=ctx.deps.cwd,
        harness_home=ctx.deps.harness_home,
        emit=child_emit,
        # A FRESH gate in BYPASS: no harness loop resolves a child's deferred approval, so its
        # read-only tools must run inline (never raise ApprovalRequired) — ADR-0013 §2,5.
        gate=PermissionGate(mode=PermissionMode.BYPASS),
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=deny_user_question_resolver,
        active_agent=explore,
        # ``task_store`` omitted: the default_factory gives each child a fresh empty list.
    )

    logger.debug("spawning explore subagent %d (prompt=%r)", index, prompt)
    async with _semaphore():  # per child ATTEMPT: the ceiling bounds a fan-out wider than it
        result = await _require_main_agent().run(
            prompt,
            deps=child_deps,
            usage_limits=UsageLimits(request_limit=settings.subagent_max_requests),
            # The child's live tool-call feed (a nested ``run()`` bypasses decode's loop, so this is
            # the only place its calls exist). It lands in ``child_emit``, which decides — at emit
            # time — whether Verbose Mode shows it or ``_silent_emit`` swallows it.
            event_stream_handler=_make_child_stream_handler(child_emit),
        )
    return _usable_report(result)
