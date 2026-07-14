"""The ``RegressionProbe`` contract — one declarative behavior probe (ADR-0017 §2,6).

A probe is data, not code-flow: it names an ``id``, the ``prompt`` the agent is given, a ``fixture``
that seeds a fresh temp Workspace (files / ``AGENTS.md`` / ``.decode/settings.json``), the gate policy
the run drives under, and the ``metrics`` that grade the resulting behavior. Everything the eval
driver (:mod:`evals.harness.driver`) can vary per run is reachable straight from the declaration:

* ``gate_mode`` + ``permission_rules`` + ``resolve_permission`` / ``resolve_user_question`` — the
  full gate surface (default = headless ``BYPASS`` with the deny resolvers, mirroring ``run_agent_once``);
* ``message_history`` — a builder for a pre-filled conversation (the compaction probe needs a
  near-limit history);
* ``max_requests`` — the model-request cap so a runaway probe stops gracefully;
* ``context`` — an optional context manager entered AROUND the run for a live resource the probe
  needs alive during the run but not seeded as a file (the ``http.server`` web-fetch fixture);
* ``settings_overrides`` — settings attributes forced for the duration of the run (the
  compaction-survival probe shrinks ``compaction_context_window_tokens`` / ``compaction_keep_recent_tokens``
  so its near-limit history actually crosses the trigger — the real 1M-token window never would);
* ``enable_compaction`` — wire the auto-compaction cascade for this run (the driver leaves it off by
  default, so only a probe that grades compaction pays for a summarizer call).

This module imports no Opik: ``metrics`` are duck-typed Opik metric instances (built in task 104 /
Opik built-ins / G-Eval judges), kept as ``Any`` so the probe contract stays light and the CLI can
import it without pulling the Opik harness (ADR-0017 §1).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from decode.permissions.types import PermissionMode

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

    from decode.entities.permissions import PermissionDecision, PermissionRequest
    from decode.permissions.rules import RuleSet

# Seeds a fresh temp Workspace for one probe run: files, ``AGENTS.md``, ``.decode/settings.json``.
FixtureBuilder = Callable[[Path], None]

# Builds a pre-filled pydantic-ai message history (the compaction probe's near-limit conversation).
MessageHistoryBuilder = Callable[[], "list[ModelMessage]"]

# Opens a live resource around the run (e.g. a local ``http.server``), torn down when the run ends.
FixtureContext = Callable[[Path], AbstractContextManager[Any]]

# The gate resolver seams, mirroring ``evals.harness.driver`` (default = headless auto-deny).
PermissionResolver = Callable[["PermissionRequest"], Awaitable["PermissionDecision"]]
UserQuestionResolver = Callable[[str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class RegressionProbe:
    """One declarative behavior probe: what to run, how to gate it, how to grade it (ADR-0017 §6).

    ``id`` is the probe's stable key (the Opik dataset item id); ``prompt`` is what the agent is
    asked; ``fixture`` seeds the fresh temp Workspace; ``metrics`` are the Opik metric instances that
    grade the run's behavior. The remaining fields map one-to-one onto the eval driver's knobs and
    default to the headless ``BYPASS`` posture so the simplest probe is a three-field declaration.

    ``skip_reason`` marks a probe that is DECLARED but not yet runnable — it stays discoverable in the
    registry (so it activates the moment its blocker clears) while
    :func:`evals.harness.regression.run_regression` excludes it from live runs, logging the reason.
    The MCP probe uses it: decode has no MCP tool factory yet (ADR-0017 §10 defers probe 12), so it
    ships behind a skip until MCP lands.
    """

    id: str
    prompt: str
    fixture: FixtureBuilder
    metrics: Sequence[Any]
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

    def __post_init__(self) -> None:
        """Reject a probe that could never grade: a blank ``id`` / ``prompt`` or no metrics.

        A probe with no metrics would run the agent and score nothing — a silent no-op in the suite;
        a blank id/prompt is as unusable here as it is for a benchmark task, so both fail loudly at
        construction rather than at run time.
        """
        if not self.id.strip():
            raise ValueError("RegressionProbe.id must not be blank")
        if not self.prompt.strip():
            raise ValueError(f"RegressionProbe {self.id!r}: prompt must not be blank")
        if not self.metrics:
            raise ValueError(f"RegressionProbe {self.id!r}: at least one metric is required")
