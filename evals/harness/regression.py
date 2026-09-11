"""The Opik glue that runs the behavior cases host-native and scores them (ADR-0017 §3,4,6).

Two pieces sit on top of the eval driver (:mod:`evals.harness.driver`) and the case registry
(:mod:`evals.regression.loader`):

* :func:`make_regression_task_fn` builds the sync Opik task fn. For one dataset item it builds the
  case's fixture in a fresh ``tempfile`` dir, runs the REAL agent HOST-NATIVE (``sandbox_mode`` forced
  to ``none`` — fast, no docker) under the case's gate policy / resolvers / pre-filled history / request
  cap, snapshots the resulting file tree, and returns the flat payload the code metrics + judges read.
  The task fn NEVER raises: a crashed agent run grades as fail-with-reason (``agent_error``) and a
  fixture / setup failure grades as fail-with-reason (``infra_error``), because Opik's ``evaluate`` gives
  task fns no per-item isolation — one raise would abort the whole experiment (task 106 lesson). The
  temp dir is always removed.
* :func:`run_regression` loads + filters the cases (``--case`` / ``--difficulty``), upserts the
  selection into ``decode-regression-v2``, and calls ``opik.evaluation.evaluate`` scoped to those
  items with the case-scoped metrics, ``experiment_scoring_functions=[mean_per_metric]`` folding the
  run into one mean per metric ON the Experiment row, and ``experiment_config`` carrying the agent
  model + provider + git sha + the run's shape. The dataset names ``settings.eval_project_name``, so
  eval runs never pollute live REPL tracing.

Per-case metric binding: ``evaluate`` applies ONE metric list to EVERY item, but each case declares
its OWN metrics. :class:`CaseScopedMetric` wraps every case's metric so it scores only the item whose
``case_id`` matches and returns ``[]`` (zero scores) for every other item — so a single ``evaluate``
call returns one :class:`~opik.evaluation.evaluation_result.EvaluationResult` (what the task-115
threshold gate aggregates) with clean per-case scores, no cross-case noise.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from opik.evaluation.metrics.base_metric import BaseMetric
from opik.evaluation.metrics.score_result import ScoreResult

from decode.config.settings import settings
from decode.tools.bash import reset_executor
from evals.harness.driver import run_agent_once_sync
from evals.regression.case import RegressionCase
from evals.regression.loader import load_cases, runnable_cases, select_cases

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    import opik
    from opik.evaluation.evaluation_result import EvaluationResult
    from opik.evaluation.test_result import TestResult

    from evals.harness.driver import EvalRunRecord

logger = logging.getLogger(__name__)

# The Opik task fn Opik hands one dataset item and expects one flat output dict back.
RegressionTaskFn = Callable[[dict[str, Any]], dict[str, Any]]

# Cap the per-file snapshot so a case that writes a huge file can't bloat the Opik payload; a file
# over this size is recorded as an elision marker instead of its bytes.
_MAX_SNAPSHOT_BYTES = 64 * 1024


# The stable experiment name every FULL regression run shares, so ``get_experiments_by_name`` finds
# the previous run to compare against. A filtered run appends its slice (see :func:`scoped_name`).
GATE_EXPERIMENT_NAME = "decode-regression-gate"


class RegressionSelectionError(Exception):
    """No regression case matched the ``--case`` filter — a loud, friendly stop."""


class CaseScopedMetric(BaseMetric):
    """Bind ``inner`` to one case: score only that case's item, contribute nothing to others.

    ``evaluate`` runs every metric against every item; a case's metric is only meaningful on its own
    item (a read-discipline metric grading another case's output is noise — the same reasoning that
    keeps a benchmark's per-task judge off other tasks, ADR-0017 §7). This wrapper reads the item's
    ``case_id`` and delegates to ``inner`` only on a match, else returns ``[]`` — Opik treats an empty
    list as zero score results, so the metric simply does not apply to that item. ``track=False`` for
    the same offline reason the code metrics use (ADR-0017 §9).
    """

    def __init__(self, case_id: str, inner: BaseMetric) -> None:
        super().__init__(name=inner.name, track=False)
        self._case_id = case_id
        self._inner = inner

    def score(self, case_id: Any = None, **kwargs: Any) -> ScoreResult | list[ScoreResult]:
        if case_id != self._case_id:
            return []
        # Forward the full scoring dict (case_id included) exactly as Opik would to a bare metric;
        # ``inner`` absorbs what it does not name through its own ``**ignored_kwargs``.
        return self._inner.score(case_id=case_id, **kwargs)


def make_regression_task_fn(cases_by_id: dict[str, RegressionCase]) -> RegressionTaskFn:
    """Build the sync Opik task fn that runs + grades one case item (ADR-0017 §4,6).

    The returned closure looks the case up by ``item["case_id"]`` and runs it host-native. Sync
    because Opik ``evaluate()`` task fns cannot be async.
    """

    def regression_task_fn(item: dict[str, Any]) -> dict[str, Any]:
        case = cases_by_id[item["case_id"]]
        return run_case(case)

    return regression_task_fn


def run_case(case: RegressionCase) -> dict[str, Any]:
    """Run one case host-native in a fresh temp Workspace, returning a graded payload — never a raise.

    Forces ``sandbox_mode`` to ``none`` (host-native — no docker) and resets the ``bash`` executor seam
    so the run is byte-identical to a plain host session, restoring both in the ``finally`` along with
    removing the temp Workspace. The case's ``settings_overrides`` are applied over the same window and
    rolled back in the same ``finally`` (the compaction case shrinks the context window so its
    near-limit history actually triggers). A fixture / history-builder failure is caught into
    ``infra_error``; a crashed agent run into ``agent_error`` (both inside :func:`_build_and_run`) —
    either way a payload the metrics grade comes back, so one broken case never aborts the experiment
    (task 106 lesson).
    """
    workspace = Path(tempfile.mkdtemp(prefix="decode-regression-")).resolve()
    previous_mode = settings.sandbox_mode
    settings.sandbox_mode = "none"
    saved_settings: dict[str, Any] = {}
    reset_executor()
    try:
        saved_settings = _apply_settings_overrides(case.settings_overrides)
        return _build_and_run(case, workspace)
    except (
        Exception
    ) as exc:  # a fixture / setup / override failure must not abort the whole experiment
        logger.exception("[eval] regression case setup failed for %s", case.id)
        return _payload(None, file_state={}, case=case, agent_error=None, infra_error=str(exc))
    finally:
        settings.sandbox_mode = previous_mode
        _restore_settings(saved_settings)
        reset_executor()
        shutil.rmtree(workspace, ignore_errors=True)


def _apply_settings_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Apply ``overrides`` to the global ``settings`` singleton, returning the prior values.

    Same pattern the ``sandbox_mode`` swap uses above: a case forces a handful of settings for the
    duration of ONE run (the compaction case shrinks the context window / keep-recent tail so its
    near-limit history crosses the trigger), and :func:`_restore_settings` puts them back in the
    ``finally``. Every key is validated BEFORE any is applied, so an unknown attribute fails loudly
    (surfaced as ``infra_error``) without leaving a half-applied settings state behind.
    """
    for key in overrides:
        if not hasattr(settings, key):
            raise AttributeError(f"unknown settings override {key!r} on case")
    saved: dict[str, Any] = {}
    for key, value in overrides.items():
        saved[key] = getattr(settings, key)
        setattr(settings, key, value)
    return saved


def _restore_settings(saved: Mapping[str, Any]) -> None:
    for key, value in saved.items():
        setattr(settings, key, value)


def _build_and_run(case: RegressionCase, workspace: Path) -> dict[str, Any]:
    """Seed the fixture, drive the agent, snapshot the tree — the happy body of :func:`run_case`.

    The fixture and optional ``message_history`` builder run first (a raise here propagates to
    ``run_case``'s ``infra_error`` handler). The agent run itself is caught into ``agent_error`` so a
    crashed run still yields a graded payload (the file snapshot is taken regardless). An optional
    ``case.context`` (e.g. a live ``http.server``) is entered around the run only.
    """
    from contextlib import nullcontext

    case.fixture(workspace)
    history = case.message_history() if case.message_history else None

    record: EvalRunRecord | None = None
    agent_error: str | None = None
    context: AbstractContextManager[Any] = (
        case.context(workspace) if case.context else nullcontext()
    )
    try:
        with context:
            record = run_agent_once_sync(
                case.prompt,
                cwd=workspace,
                gate_mode=case.gate_mode,
                permission_rules=case.permission_rules,
                resolve_permission=case.resolve_permission,
                resolve_user_question=case.resolve_user_question,
                message_history=history,
                max_requests=case.max_requests,
                enable_compaction=case.enable_compaction,
            )
        agent_error = record.agent_error
    except Exception as exc:  # a crashed agent still grades — fail-with-reason, not skipped
        logger.exception("[eval] regression agent run raised for %s", case.id)
        agent_error = f"agent run raised: {exc}"

    return _payload(
        record,
        file_state=_snapshot(workspace),
        case=case,
        agent_error=agent_error,
        infra_error=None,
    )


def _payload(
    record: EvalRunRecord | None,
    *,
    file_state: dict[str, str],
    case: RegressionCase,
    agent_error: str | None,
    infra_error: str | None,
) -> dict[str, Any]:
    """The flat output dict the regression metrics + judges read (ADR-0017 §4,6).

    ``tool_calls`` is de-dataclassed to plain ``{"name", "args"}`` dicts so the payload is
    JSON-serializable for Opik storage; ``file_state`` maps each Workspace-relative path to its text so
    a metric can assert a file was created / edited. ``max_steps`` mirrors the case's request cap for
    :class:`~evals.harness.metrics.MaxStepsMetric` (``None`` when the case sets no cap).
    ``compaction_events`` reports how many times the auto-compaction cascade fired (the
    compaction-survival case reads it to prove firing). Two failure
    channels, both absorbed by the metrics' ``**ignored_kwargs``: ``agent_error`` names a crashed run
    (the tree was still snapshotted); ``infra_error`` names a fixture / setup failure. A ``None`` record
    degrades every run field to its empty default.
    """
    tool_calls = (
        [{"name": call.name, "args": call.args} for call in record.tool_calls] if record else []
    )
    return {
        "output": record.output if record else "",
        "tool_calls": tool_calls,
        "steps": record.steps if record else 0,
        "input_tokens": record.input_tokens if record else 0,
        "output_tokens": record.output_tokens if record else 0,
        "denied_tools": list(record.denied_tools) if record else [],
        "compaction_events": record.compaction_events if record else 0,
        "file_state": file_state,
        "max_steps": case.max_requests,
        "agent_error": agent_error,
        "infra_error": infra_error,
    }


def _snapshot(workspace: Path) -> dict[str, str]:
    """Snapshot the Workspace's text files as ``{relative_posix_path: content}`` (ADR-0017 §6).

    Walks ``workspace`` for regular files, skipping the ``.git`` tree (a case may seed git history —
    its objects are binary noise a metric never grades). A file over :data:`_MAX_SNAPSHOT_BYTES` or one
    that is not valid UTF-8 (a binary artifact) is recorded as a short elision marker instead of its
    bytes, so the payload stays a small, JSON-serializable map of what the agent left behind.
    """
    snapshot: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(workspace).parts:
            continue
        relative = path.relative_to(workspace).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_SNAPSHOT_BYTES:
            snapshot[relative] = f"[elided: {size} bytes > {_MAX_SNAPSHOT_BYTES} cap]"
            continue
        try:
            snapshot[relative] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            snapshot[relative] = "[elided: not UTF-8 text]"
    return snapshot


def run_regression(
    *,
    case_id: str | None = None,
    difficulty: str | None = None,
    experiment_name: str | None = None,
    client: opik.Opik | None = None,
) -> EvaluationResult:
    """Run the selected cases as one Opik experiment and return its result (ADR-0022 §8).

    Loads every case, applies the optional ``--case`` / ``--difficulty`` filters, upserts the
    selection into ``decode-regression-v2`` (the dataset ALONE — a billed gate run never depends on
    the Test Suite API), and calls ``evaluate`` scoped (via ``dataset_item_ids``) to just those items
    with the case-scoped metrics. ``experiment_scoring_functions=[mean_per_metric]`` writes one mean
    per metric onto the Experiment row, so the run's aggregate is readable without re-deriving it.
    Runs single-threaded — the ``bash`` executor seam is process-global. Raises
    :class:`RegressionSelectionError` when nothing matches.

    ``experiment_name`` defaults to :func:`scoped_name` over the filters — one STABLE name per SLICE
    (``decode-regression-gate`` for the whole suite, ``decode-regression-gate-hard`` for a tier), so
    the threshold ritual's baseline compare finds prior runs by name and compares like-for-like
    instead of diffing a tier against the full suite.
    """
    import opik
    from opik.evaluation import evaluate

    from evals.harness.benchmark import evaluate_project_name
    from evals.harness.datasets import sync_regression_dataset

    all_cases = load_cases()
    selected = runnable_cases(select_cases(all_cases, case_id=case_id, difficulty=difficulty))
    if not selected:
        raise RegressionSelectionError(
            f"no runnable regression case matched (case={case_id!r}, difficulty={difficulty!r}); "
            f"{len(all_cases)} case(s) available."
        )

    client = client or opik.Opik()
    dataset = sync_regression_dataset(selected, client=client)
    item_ids = _selected_item_ids(dataset, {case.id for case in selected})

    cases_by_id = {case.id: case for case in all_cases}
    task_fn = make_regression_task_fn(cases_by_id)
    return evaluate(
        dataset=dataset,
        task=task_fn,
        scoring_metrics=_scoring_metrics(selected),
        experiment_scoring_functions=[mean_per_metric],
        experiment_config=experiment_config(case_count=len(selected), difficulty=difficulty),
        experiment_name=experiment_name
        or scoped_name(GATE_EXPERIMENT_NAME, case_id=case_id, difficulty=difficulty),
        project_name=evaluate_project_name(dataset),
        dataset_item_ids=item_ids or None,
        task_threads=1,
    )


def scoped_name(base: str, *, case_id: str | None = None, difficulty: str | None = None) -> str:
    """``base`` for a full run, ``base-<slice>`` for a filtered one (ADR-0022 §8).

    ONE naming rule for both regression surfaces — the experiment a gate run logs
    (``decode-regression-gate-hard``) and the Test Suite a filtered suite run registers
    (``decode-regression-suite-hard``). A slice keeps its own name so its baseline, and the items it
    bills, stay its own; ``--case`` wins over ``--difficulty`` because it is the narrower filter.
    """
    slice_name = case_id or difficulty
    return f"{base}-{slice_name}" if slice_name else base


def _selected_item_ids(dataset: Any, selected_ids: set[str]) -> list[str]:
    """The dataset item ids whose ``case_id`` is in ``selected_ids`` — scopes ``evaluate`` to the run.

    Reads the just-synced dataset's items and keeps those the filter selected, so a run over the shared
    ``decode-regression-v2`` dataset executes only the chosen cases. An item missing an ``id`` (an
    unexpected Opik shape) is skipped rather than crashing the run.
    """
    items = dataset.get_items()
    return [
        item["id"]
        for item in items
        if item.get("case_id") in selected_ids and item.get("id") is not None
    ]


def _scoring_metrics(cases: list[RegressionCase]) -> list[BaseMetric]:
    """Wrap every selected case's metrics in :class:`CaseScopedMetric` (ADR-0017 §6).

    One flat list for the single ``evaluate`` call; each wrapper scores only its own case's item, so a
    multi-case run stays one experiment with clean per-case scores.
    """
    return [CaseScopedMetric(case.id, metric) for case in cases for metric in case.metrics]


def experiment_config(*, case_count: int, difficulty: str | None) -> dict[str, Any]:
    """The Opik ``experiment_config`` for a regression run — what two rows differ by (ADR-0022 §8).

    The model + provider driving the agent, the code the run was on (``git rev-parse HEAD``), and the
    SHAPE of the run: how many cases it graded and which tier it was sliced to (``None`` = the whole
    suite). Without the last two, a green ``decode-regression-gate-easy`` row is indistinguishable
    from a full run that silently graded five cases. Reuses the benchmark harness's model/sha
    resolvers (the same evals harness) — one resolver, both experiment tracks. ``harness`` labels the
    row as a regression run (always host-native ``none`` mode).
    """
    from evals.harness.benchmark import agent_model, git_sha

    return {
        "agent_model": agent_model(),
        "provider": settings.llm_provider,
        "git_sha": git_sha(),
        "harness": "regression",
        "case_count": case_count,
        "difficulty": difficulty,
    }


def mean_per_metric(test_results: Sequence[TestResult]) -> list[ScoreResult]:
    """Fold the run into one ``mean_<metric>`` score per metric, ON the Experiment row (ADR-0022 §8).

    The ``experiment_scoring_functions`` entry every regression run carries: opik 2.2.36 calls it with
    the run's ``List[TestResult]`` and logs what comes back onto the Experiment, so the per-metric
    means are readable straight off the row instead of being re-derived post-hoc from the result.
    Scores are macro-averaged over the items that actually produced them — a case-scoped metric
    contributes nothing to the cases it does not grade (:class:`CaseScopedMetric`).

    Names are prefixed ``mean_`` because Opik keeps experiment scores in their own bag beside the
    item-metric averages: an unprefixed name would put two different numbers under one label on the
    same row. A ``scoring_failed`` score is EXCLUDED — a metric that crashed scored nothing, and
    folding its 0.0 in would read as a behavior regression. Total by construction: opik swallows a
    raising scoring function with a warning, so this one never raises.
    """
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for test_result in test_results:
        for score in getattr(test_result, "score_results", []) or []:
            if getattr(score, "scoring_failed", False):
                continue
            name = getattr(score, "name", None)
            value = getattr(score, "value", None)
            if not isinstance(name, str) or not isinstance(value, (int, float)):
                continue
            sums[name] = sums.get(name, 0.0) + float(value)
            counts[name] = counts.get(name, 0) + 1
    return [
        ScoreResult(
            name=f"mean_{name}",
            value=sums[name] / counts[name],
            reason=f"mean over {counts[name]} scored item(s).",
        )
        for name in sorted(sums)
    ]
