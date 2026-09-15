"""Pure threshold-gate + baseline-compare logic for the regression ritual (ADR-0017 §6; task 115).

The pre-merge ritual (``evals/regression/test_thresholds.py``) runs the case suite once and then asks
two questions this module answers with plain dicts — no Opik, no keys, no agent run — so both are
unit-tested offline in ``make ci``:

* **Absolute gate (the hard contract).** :func:`evaluate_thresholds` checks each aggregated per-metric
  mean against the :data:`THRESHOLDS` table. Judges are LLM-scored and nondeterministic, so they sit at
  a lower floor (:data:`JUDGE_METRIC_THRESHOLD`) than the mechanical tool-discipline metrics
  (:data:`TOOL_DISCIPLINE_THRESHOLD`) — thresholds are NOT exact-match on purpose (ADR-0017 §6,7).
* **Per-tier report (a readable signal).** :func:`scores_by_tier` folds the run's ``test_results``
  into one mean per metric PER Difficulty Tier so the ritual can print an easy / medium / hard table
  (ADR-0022 §8). It is a report, not a gate: the floors below stay GLOBAL.
* **Baseline compare (a soft signal).** :func:`compare_to_baseline` diffs this run's per-metric means
  against the previous experiment's and flags regressions; the ritual WARNs on those while the absolute
  thresholds stay the only hard gate, so the suite is usable on day one with no baseline at all.

Absent-metric policy: a metric missing from the run's scores is never gated. A behavior that did not run
cannot regress — this is how the skip-guarded case 12 (MCP, ADR-0017 §10) is handled: its
``tool_called_echo`` metric is simply absent from the aggregation, so the gate neither fails nor
vacuously passes it. The ritual asserts the report is non-empty (some case DID run) so an all-absent
suite can never pass silently.

Everything here is duck-typed on the shapes opik hands back (``.mean`` on a statistics object,
``.name`` / ``.value`` on a feedback-score average) so the module imports no Opik and the extractors are
testable with plain stand-ins.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

# The pre-merge contract, honest first numbers (ADR-0017 §6 — tune from the first real runs).
# Tool-discipline metrics are deterministic code checks, so they must be met almost every run.
TOOL_DISCIPLINE_THRESHOLD = 0.8
# Judges (GEval) are nondeterministic LLM scores, so they sit at a lower, noise-tolerant floor.
JUDGE_METRIC_THRESHOLD = 0.7

# Every GEval judge in the suite is built with the same metric name (evals/harness/judges.py), so all
# judge scores across the judged cases aggregate under this one key — the single metric held to the
# judge floor. Every OTHER aggregated metric is a mechanical code metric held to the discipline floor.
JUDGE_METRIC_NAME = "g_eval_metric"

# Per-metric overrides; any aggregated metric NOT named here falls back to :data:`DEFAULT_THRESHOLD`.
THRESHOLDS: dict[str, float] = {
    JUDGE_METRIC_NAME: JUDGE_METRIC_THRESHOLD,
}
DEFAULT_THRESHOLD = TOOL_DISCIPLINE_THRESHOLD


class _HasMean(Protocol):
    mean: float


class _HasNameValue(Protocol):
    name: str
    value: float


@dataclass(frozen=True)
class MetricGate:
    """One metric's verdict against its floor: its aggregated ``value`` vs ``threshold``."""

    name: str
    value: float
    threshold: float
    passed: bool


@dataclass(frozen=True)
class ThresholdReport:
    """The absolute-gate outcome across every graded metric (ADR-0017 §6)."""

    gates: tuple[MetricGate, ...]

    @property
    def failures(self) -> tuple[MetricGate, ...]:
        """The gates whose metric fell below its floor — empty when the suite is green."""
        return tuple(gate for gate in self.gates if not gate.passed)

    @property
    def passed(self) -> bool:
        """True when every graded metric cleared its floor (vacuously true for an empty report)."""
        return not self.failures

    @property
    def empty(self) -> bool:
        """True when nothing was graded — the ritual treats this as "the suite did not run"."""
        return not self.gates


@dataclass(frozen=True)
class MetricDelta:
    """One metric's change vs the baseline experiment (a soft, WARN-only signal)."""

    name: str
    current: float
    baseline: float
    tolerance: float = 0.0

    @property
    def delta(self) -> float:
        """Signed change from baseline to this run (negative = the metric dropped)."""
        return self.current - self.baseline

    @property
    def regressed(self) -> bool:
        """True when the metric dropped by more than ``tolerance`` (noise-absorbing slack)."""
        return self.current < self.baseline - self.tolerance


@dataclass(frozen=True)
class BaselineCandidate:
    """A prior experiment considered as the baseline: its id, creation time, and per-metric means.

    ``created_at`` is ``datetime | None`` because opik's ``ExperimentPublic.created_at`` is
    ``Optional[datetime]`` — an undated candidate sorts oldest in :func:`latest_baseline` rather than
    crashing the newest-by-time pick.
    """

    experiment_id: str
    created_at: datetime | None
    scores: Mapping[str, float]


def threshold_for(metric_name: str, *, thresholds: Mapping[str, float] | None = None) -> float:
    """The floor a metric must clear — its :data:`THRESHOLDS` override, else the discipline default."""
    table = THRESHOLDS if thresholds is None else thresholds
    return table.get(metric_name, DEFAULT_THRESHOLD)


def evaluate_thresholds(
    scores: Mapping[str, float],
    *,
    thresholds: Mapping[str, float] | None = None,
    default: float = DEFAULT_THRESHOLD,
) -> ThresholdReport:
    """Grade each metric mean against its floor (``>=``), returning the per-metric report (ADR-0017 §6).

    Only the metrics present in ``scores`` are gated — a metric that never ran (a skip-guarded case)
    is absent and therefore not asserted. The gate is inclusive (``value >= threshold``) so a score
    exactly at the floor passes. An empty ``scores`` yields an empty report that vacuously passes; the
    ritual asserts non-emptiness so an all-absent suite cannot slip through.
    """
    table = THRESHOLDS if thresholds is None else thresholds
    gates = tuple(
        MetricGate(
            name=name,
            value=scores[name],
            threshold=(threshold := table.get(name, default)),
            passed=scores[name] >= threshold,
        )
        for name in sorted(scores)
    )
    return ThresholdReport(gates=gates)


def scores_by_tier(
    test_results: Iterable[Any], tiers: Mapping[str, str]
) -> dict[str, dict[str, float]]:
    """Fold a run's ``test_results`` into ``{tier: {metric: mean}}`` (ADR-0022 §8) — pure, offline.

    ``tiers`` maps each case id to its Difficulty Tier (the registry is the authority, so a case that
    changed tier reports under its CURRENT one). Each result's case id is read off
    ``test_case.dataset_item_content`` — the same place the benchmark's aggregates read their slice
    labels — falling back to the task payload; a result with no KNOWN case id is skipped rather than
    filed under a guessed tier. A ``scoring_failed`` score is excluded: a metric that crashed scored
    nothing, and averaging its 0.0 in would read as a behavior regression.

    Everything is duck-typed on the shapes opik hands back, so the report is testable with plain
    stand-ins and this module still imports no Opik.
    """
    sums: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], int] = {}
    for result in test_results:
        tier = tiers.get(_case_id_of(result) or "")
        if tier is None:
            continue
        for score in getattr(result, "score_results", None) or []:
            if getattr(score, "scoring_failed", False):
                continue
            name = getattr(score, "name", None)
            value = getattr(score, "value", None)
            if not isinstance(name, str) or not isinstance(value, (int, float)):
                continue
            key = (tier, name)
            sums[key] = sums.get(key, 0.0) + float(value)
            counts[key] = counts.get(key, 0) + 1
    report: dict[str, dict[str, float]] = {}
    for (tier, name), total in sums.items():
        report.setdefault(tier, {})[name] = total / counts[(tier, name)]
    return report


def _case_id_of(result: Any) -> str | None:
    """The case id behind one ``TestResult``: its dataset item content, else its task payload."""
    test_case = getattr(result, "test_case", None)
    for source in (
        getattr(test_case, "dataset_item_content", None),
        getattr(test_case, "task_output", None),
    ):
        case_id = source.get("case_id") if isinstance(source, dict) else None
        if isinstance(case_id, str):
            return case_id
    return None


def format_tier_scores(by_tier: Mapping[str, Mapping[str, float]]) -> str:
    """One readable table per tier for the ritual's WARN — empty tiers say so (ADR-0022 §8)."""
    if not by_tier:
        return "no per-tier scores (no case reported a known tier)"
    lines: list[str] = []
    for tier in sorted(by_tier):
        lines.append(f"  [{tier}]")
        lines.extend(f"    {name}: {by_tier[tier][name]:.3f}" for name in sorted(by_tier[tier]))
    return "per-tier scores (report only — the thresholds gate globally):\n" + "\n".join(lines)


def compare_to_baseline(
    current: Mapping[str, float],
    baseline: Mapping[str, float],
    *,
    tolerance: float = 0.0,
) -> list[MetricDelta]:
    """Diff this run's per-metric means against the baseline's, over the metrics they share.

    A metric present this run but absent from the baseline (a brand-new case) has nothing to compare
    against and is skipped. ``tolerance`` is slack that absorbs small judge noise before a dip counts
    as a regression. The result is a delta per shared metric — the caller WARNs on the regressed ones.
    """
    return [
        MetricDelta(
            name=name,
            current=current[name],
            baseline=baseline[name],
            tolerance=tolerance,
        )
        for name in sorted(current)
        if name in baseline
    ]


def latest_baseline(candidates: Iterable[BaselineCandidate]) -> BaselineCandidate | None:
    """The most recent prior experiment that actually carries scores, else ``None``.

    An experiment with no feedback scores (a crashed / empty run) is not a usable baseline, so it is
    dropped first. Among the scored ones the newest by ``created_at`` wins; a ``created_at=None``
    candidate (opik's ``Optional[datetime]``) sorts oldest — it is only chosen when NO scored candidate
    carries a timestamp, and even then a candidate is returned rather than crashing on a ``None``/
    ``datetime`` comparison. This keeps the pure helper total, upholding the ritual's "any Opik hiccup
    degrades to a ``None`` baseline, never a gate crash" invariant.
    """
    scored = [candidate for candidate in candidates if candidate.scores]
    if not scored:
        return None
    dated = [(candidate.created_at, candidate) for candidate in scored if candidate.created_at]
    if dated:
        return max(dated, key=lambda pair: pair[0])[1]
    return scored[-1]  # no timestamps to order by — best-effort: the last discovered


def scores_from_aggregation(aggregated: Mapping[str, _HasMean]) -> dict[str, float]:
    """Flatten opik's ``{name: ScoreStatistics}`` aggregation to a plain ``{name: mean}`` dict."""
    return {name: stat.mean for name, stat in aggregated.items()}


def baseline_scores_from_feedback(feedback_scores: Iterable[_HasNameValue]) -> dict[str, float]:
    """Flatten an experiment's feedback-score averages (``.name`` / ``.value``) to ``{name: value}``."""
    return {score.name: score.value for score in feedback_scores}


def format_failures(report: ThresholdReport) -> str:
    """A one-line-per-failure message for the gate assertion — empty when the report is green."""
    if not report.failures:
        return "all metrics cleared their thresholds"
    lines = [
        f"  - {gate.name}: {gate.value:.3f} < {gate.threshold} (floor)" for gate in report.failures
    ]
    return "regression thresholds NOT met:\n" + "\n".join(lines)


def format_deltas(deltas: Iterable[MetricDelta]) -> str:
    """A readable per-metric baseline-delta table, marking regressions — for the WARN signal."""
    rows = list(deltas)
    if not rows:
        return "no metrics shared with the baseline experiment"
    lines = [
        f"  {'REGRESSED' if delta.regressed else 'ok       '} {delta.name}: "
        f"{delta.current:.3f} vs baseline {delta.baseline:.3f} ({delta.delta:+.3f})"
        for delta in rows
    ]
    return "baseline compare (per-metric delta):\n" + "\n".join(lines)
