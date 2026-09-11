"""The ``RegressionCase`` contract — one declarative behavior case (ADR-0022 §8; ADR-0017 §2,6).

A case is data, not code-flow: it names an ``id``, the ``prompt`` the agent is given, a ``fixture``
that seeds a fresh temp Workspace (files / ``AGENTS.md`` / ``.decode/settings.json``), the gate policy
the run drives under, and the ``metrics`` that grade the resulting behavior. On top of what ADR-0017's
retired "probe" contract carried, it adds what ADR-0022 §8 asks for: a ``difficulty`` tier (the
``--difficulty`` slice), a one-line ``symptom`` (what a reader sees first), a natural-language
``assertion`` (the Test Suite's quality bar — the English form of the metrics), and the provenance a
MINED case brings with it (``source_trace_id``, ``thread_id``, ``fixed_in``). ONE declaration
therefore registers in BOTH Opik surfaces: a dataset item + deterministic metrics (the gate) and a
Test Suite item + its assertion (the contrast).

Everything the eval driver (:mod:`evals.harness.driver`) can vary per run is reachable straight from
the declaration:

* ``gate_mode`` + ``permission_rules`` + ``resolve_permission`` / ``resolve_user_question`` — the
  full gate surface (default = headless ``BYPASS`` with the deny resolvers, mirroring ``run_agent_once``);
* ``message_history`` — a builder for a pre-filled conversation (the compaction case needs a
  near-limit history);
* ``max_requests`` — the model-request cap so a runaway case stops gracefully;
* ``context`` — an optional context manager entered AROUND the run for a live resource the case
  needs alive during the run but not seeded as a file (the ``http.server`` web-fetch fixture);
* ``settings_overrides`` — settings attributes forced for the duration of the run (the
  compaction-survival case shrinks ``compaction_context_window_tokens`` / ``compaction_keep_recent_tokens``
  so its near-limit history actually crosses the trigger — the real 1M-token window never would);
* ``enable_compaction`` — wire the auto-compaction cascade for this run (the driver leaves it off by
  default, so only a case that grades compaction pays for a summarizer call).

This module imports no Opik: ``metrics`` are duck-typed Opik metric instances (built in task 104 /
Opik built-ins / G-Eval judges), kept as ``Any`` so the case contract stays light and the CLI can
import it without pulling the Opik harness (ADR-0017 §1).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from decode.permissions.types import PermissionMode

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

    from decode.entities.permissions import PermissionDecision, PermissionRequest
    from decode.permissions.rules import RuleSet

# The tiers a case may declare — the SAME three a Benchmark Task uses (ONE Difficulty Tier vocabulary
# across both eval tracks; ``tests/unit/evals/regression/test_case.py`` pins the two to each other).
# Spelled out here rather than imported from ``evals.harness.task_loader`` because importing a
# submodule runs ``evals/harness/__init__.py``, which pulls the whole Opik harness — and this contract
# must stay importable without it (ADR-0017 §1). ``DIFFICULTIES`` is the runtime tuple the validation
# below and the CLI's choices read.
Difficulty = Literal["easy", "medium", "hard"]
DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "medium", "hard")

# Seeds a fresh temp Workspace for one case run: files, ``AGENTS.md``, ``.decode/settings.json``.
FixtureBuilder = Callable[[Path], None]

# Builds a pre-filled pydantic-ai message history (the compaction case's near-limit conversation).
MessageHistoryBuilder = Callable[[], "list[ModelMessage]"]

# Opens a live resource around the run (e.g. a local ``http.server``), torn down when the run ends.
FixtureContext = Callable[[Path], AbstractContextManager[Any]]

# The gate resolver seams, mirroring ``evals.harness.driver`` (default = headless auto-deny).
PermissionResolver = Callable[["PermissionRequest"], Awaitable["PermissionDecision"]]
UserQuestionResolver = Callable[[str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class RegressionCase:
    """One declarative behavior case: what to run, how to gate it, how to grade it (ADR-0022 §8).

    ``id`` is the case's stable key (the Opik dataset item id); ``prompt`` is what the agent is
    asked; ``fixture`` seeds the fresh temp Workspace; ``metrics`` are the Opik metric instances that
    grade the run's behavior. ``difficulty`` places the case in a tier (easy = single-tool discipline,
    medium = planning / delegation / skills / memory / web / lsp, hard = compaction, the gate,
    destructive caution, judged answers, the json contract) so one tier can be run and reported alone.
    ``symptom`` is the one-line regression this case exists to catch — an INVENTED case phrases it as
    ``"harness invariant: <one line>"``, a MINED one names the bad behavior the trace showed.
    ``assertion`` is that same bar in English: it becomes the Test Suite item's assertion, the
    natural-language contrast to the deterministic metrics (ADR-0017 §6).

    ``source_trace_id`` / ``thread_id`` / ``fixed_in`` are the MINED-case provenance (``python -m evals
    mine``): the Opik trace and thread the case came from and the commit that fixed it. An invented
    case leaves all three ``None``.

    The remaining fields map one-to-one onto the eval driver's knobs and default to the headless
    ``BYPASS`` posture.

    ``skip_reason`` marks a case that is DECLARED but not yet runnable — it stays discoverable in the
    registry (so it activates the moment its blocker clears) while
    :func:`evals.harness.regression.run_regression` excludes it from live runs, logging the reason.
    The MCP case uses it: decode has no MCP tool factory yet (ADR-0017 §10 defers it), so it ships
    behind a skip until MCP lands.
    """

    id: str
    prompt: str
    fixture: FixtureBuilder
    metrics: Sequence[Any]
    difficulty: Difficulty
    symptom: str
    assertion: str
    gate_mode: PermissionMode = PermissionMode.BYPASS
    permission_rules: RuleSet | None = None
    resolve_permission: PermissionResolver | None = None
    resolve_user_question: UserQuestionResolver | None = None
    message_history: MessageHistoryBuilder | None = None
    context: FixtureContext | None = None
    max_requests: int | None = None
    settings_overrides: Mapping[str, Any] = field(default_factory=dict)
    enable_compaction: bool = False
    tags: list[str] = field(default_factory=list)
    skip_reason: str | None = None
    source_trace_id: str | None = None
    thread_id: str | None = None
    fixed_in: str | None = None

    def __post_init__(self) -> None:
        """Reject a case that could never grade, never slice, or never read.

        A case with no metrics would run the agent and score nothing — a silent no-op in the suite; a
        blank id/prompt is as unusable here as it is for a benchmark task; an unknown ``difficulty``
        would drop the case out of every ``--difficulty`` slice; a blank ``symptom`` leaves a reader
        with no idea what regression the case catches; and a blank ``assertion`` would register an
        ungraded Test Suite item. All fail loudly at construction rather than at run time.
        """
        if not self.id.strip():
            raise ValueError("RegressionCase.id must not be blank")
        if not self.prompt.strip():
            raise ValueError(f"RegressionCase {self.id!r}: prompt must not be blank")
        if not self.metrics:
            raise ValueError(f"RegressionCase {self.id!r}: at least one metric is required")
        if self.difficulty not in DIFFICULTIES:
            raise ValueError(
                f"RegressionCase {self.id!r}: difficulty must be one of "
                f"{', '.join(DIFFICULTIES)}, got {self.difficulty!r}"
            )
        if not self.symptom.strip():
            raise ValueError(f"RegressionCase {self.id!r}: symptom must not be blank")
        if not self.assertion.strip():
            raise ValueError(f"RegressionCase {self.id!r}: assertion must not be blank")
