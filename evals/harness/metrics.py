"""Custom Opik code metrics for the eval harness (ADR-0017 §4,7; task 104).

Every metric here subclasses :class:`opik.evaluation.metrics.base_metric.BaseMetric` and returns a
:class:`~opik.evaluation.metrics.score_result.ScoreResult` — a ``value`` in ``[0, 1]`` plus a
human-readable ``reason``. They grade the mechanical, code-decidable facts of a run (which tool was
used, whether the hidden oracle passed, how many steps, how big the diff); anything a machine cannot
score — quality, groundedness, minimal-diff judgement — is a G-Eval judge instead
(``evals/harness/judges.py``).

The inputs come from the task-fn output dict Opik hands each metric (built from an
:class:`~evals.harness.driver.EvalRunRecord` plus the recorded verify result and diff, wired in task
106) merged with the dataset item. Opik matches a metric's ``score`` parameter names against that
dict; every parameter carries a default and every metric absorbs the rest through
``**ignored_kwargs`` — so a missing or malformed field yields a graceful ``0.0`` with a reason,
never a raise. The Opik built-ins (``Equals``, ``Contains``, ``IsJson``, ``RegexMatch``) are used
directly where they fit and are deliberately NOT wrapped here.

Every metric is constructed with ``track=False``: the ``BaseMetric`` default (``track=True``) wraps
each ``score()`` in ``opik.track(...)``, which opens a real outbound HTTPS round-trip to comet.com —
so the offline unit suite (and ``make ci``, ADR-0017 §9) would phone home. The eval-time span
nesting comes from the enclosing ``evaluate()`` call, not from tracking each metric individually.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from opik.evaluation.metrics.base_metric import BaseMetric
from opik.evaluation.metrics.score_result import ScoreResult


def _tool_call_names(tool_calls: Any) -> list[str] | None:
    """Best-effort tool-name extraction from a task-fn ``tool_calls`` list, else ``None``.

    Accepts the shapes the output can take: dicts (``{"name": ...}``),
    :class:`~evals.harness.driver.ToolCallRecord`-like objects (a ``.name`` attribute), or plain
    strings. Anything else (a non-iterable, a string blob) returns ``None`` so the caller can score
    a graceful ``0.0`` instead of raising.
    """
    if tool_calls is None or isinstance(tool_calls, str) or not isinstance(tool_calls, Iterable):
        return None
    names: list[str] = []
    for call in tool_calls:
        if isinstance(call, str):
            names.append(call)
        elif isinstance(call, dict) and "name" in call:
            names.append(str(call["name"]))
        elif hasattr(call, "name"):
            names.append(str(call.name))
    return names


class ToolCalledMetric(BaseMetric):
    """Score ``1.0`` when ``tool_name`` appears in the run's ``tool_calls``, else ``0.0``."""

    def __init__(self, tool_name: str, name: str | None = None) -> None:
        super().__init__(name=name or f"tool_called_{tool_name}", track=False)
        self.tool_name = tool_name

    def score(self, tool_calls: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        names = _tool_call_names(tool_calls)
        if names is None:
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No usable tool_calls recorded; cannot confirm {self.tool_name!r} was called.",
            )
        called = self.tool_name in names
        return ScoreResult(
            name=self.name,
            value=1.0 if called else 0.0,
            reason=f"{self.tool_name!r} {'was' if called else 'was NOT'} called; tools used: {names}.",
        )


class ToolNotCalledMetric(BaseMetric):
    """Score ``1.0`` when ``tool_name`` is ABSENT from the run's ``tool_calls``, else ``0.0``.

    A missing / malformed ``tool_calls`` field means no tool was recorded, so the forbidden tool was
    — trivially — not called: that scores ``1.0``.
    """

    def __init__(self, tool_name: str, name: str | None = None) -> None:
        super().__init__(name=name or f"tool_not_called_{tool_name}", track=False)
        self.tool_name = tool_name

    def score(self, tool_calls: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        names = _tool_call_names(tool_calls) or []
        called = self.tool_name in names
        return ScoreResult(
            name=self.name,
            value=0.0 if called else 1.0,
            reason=f"{self.tool_name!r} {'was' if called else 'was NOT'} called; tools used: {names}.",
        )


class VerifyOracleMetric(BaseMetric):
    """Map the runner's recorded verify result to ``1.0`` (exit 0 = PASS) or ``0.0``.

    The metric never RUNS anything — the task fn already ran ``verify.sh`` in the sandbox after the
    agent finished (ADR-0017 §5) and recorded ``{"exit_code": ..., "stdout": ...}``. Here we only
    map that recorded result. A missing / malformed ``verify`` mapping (no integer ``exit_code``)
    scores a graceful ``0.0``.
    """

    def __init__(self, name: str | None = None) -> None:
        super().__init__(name=name or "verify_oracle", track=False)

    def score(self, verify: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        exit_code = verify.get("exit_code") if isinstance(verify, dict) else None
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason="No verify result recorded (missing or non-integer exit_code).",
            )
        stdout = str(verify.get("stdout", "")) if isinstance(verify, dict) else ""
        passed = exit_code == 0
        snippet = stdout.strip()[:200]
        return ScoreResult(
            name=self.name,
            value=1.0 if passed else 0.0,
            reason=f"verify.sh exit_code={exit_code} ({'PASS' if passed else 'FAIL'}). stdout: {snippet!r}",
        )


class MaxStepsMetric(BaseMetric):
    """Score ``1.0`` when the run's ``steps`` is within the item's ``max_steps`` budget, else ``0.0``.

    ``steps`` is the model-request count from the record; ``max_steps`` comes from the dataset item.
    The reason always carries the observed step count. A missing ``steps`` or ``max_steps`` scores a
    graceful ``0.0``.
    """

    def __init__(self, name: str | None = None) -> None:
        super().__init__(name=name or "max_steps", track=False)

    def score(self, steps: Any = None, max_steps: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        if not _is_real_int(steps) or not _is_real_int(max_steps):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Cannot score step budget: steps={steps!r}, max_steps={max_steps!r}.",
            )
        within = steps <= max_steps
        return ScoreResult(
            name=self.name,
            value=1.0 if within else 0.0,
            reason=f"steps={steps} {'<=' if within else '>'} max_steps={max_steps}.",
        )


class DiffLinesMetric(BaseMetric):
    """Score ``1.0`` when the recorded diff's changed-line count is ``<= max_lines``, else ``0.0``.

    "Changed lines" are unified-diff body lines starting with ``+`` or ``-``, excluding the
    ``+++`` / ``---`` file headers. An empty diff (``""``) is a valid record of "no changes" and
    scores ``1.0``; a missing diff (``None`` / absent) scores a graceful ``0.0``.
    """

    def __init__(self, max_lines: int, name: str | None = None) -> None:
        super().__init__(name=name or f"diff_lines_le_{max_lines}", track=False)
        self.max_lines = max_lines

    def score(self, diff: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        if not isinstance(diff, str):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No diff recorded (got {type(diff).__name__}); cannot count changed lines.",
            )
        changed = _changed_line_count(diff)
        within = changed <= self.max_lines
        return ScoreResult(
            name=self.name,
            value=1.0 if within else 0.0,
            reason=f"{changed} changed line(s) {'<=' if within else '>'} max_lines={self.max_lines}.",
        )


def _is_real_int(value: Any) -> bool:
    """True only for a genuine ``int`` — ``bool`` is an ``int`` subclass we must reject here."""
    return isinstance(value, int) and not isinstance(value, bool)


def _changed_line_count(diff: str) -> int:
    """Count added / removed body lines in a unified diff, excluding the ``+++`` / ``---`` headers."""
    count = 0
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            count += 1
    return count
