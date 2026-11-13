"""The Langfuse experiment runner for host-native regression cases.

Two pieces sit on top of the eval driver (:mod:`evals.harness.driver`) and the case registry
(:mod:`evals.regression.loader`):

* :func:`make_regression_task_fn` builds the dataset task fn. For one dataset item it builds the
  case's fixture in a fresh ``tempfile`` dir, runs the REAL agent HOST-NATIVE (``sandbox_mode`` forced
  to ``none`` — fast, no docker) under the case's gate policy / resolvers / pre-filled history / request
  cap, snapshots the resulting file tree, and returns the flat payload the code metrics + judges read.
  The task fn NEVER raises: a crashed agent run grades as fail-with-reason (``agent_error``) and a
  fixture / setup failure grades as fail-with-reason (``infra_error``). The
  temp dir is always removed.
* :func:`run_regression` loads + filters the cases (``--case`` / ``--difficulty``), upserts the
  selection into ``decode-regression``, and runs only those items with case-scoped metrics,
  ``mean_per_metric`` folding the run into one mean per metric, and metadata carrying the agent
  model + provider + git sha + the run's shape. The dataset names ``settings.eval_project_name``, so
  eval runs never pollute live REPL tracing.

Each metric is bound to the case id in dataset metadata so it scores only its own item.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from decode.config.settings import settings
from decode.tools.bash import reset_executor
from evals.harness.datasets import dataset_items, langfuse_client, regression_items_for_dataset
from evals.harness.driver import run_agent_once_sync
from evals.harness.langfuse_experiments import (
    experiment_result,
    item_input,
    item_metadata,
    result_items,
)
from evals.harness.metrics import BaseMetric, ScoreResult
from evals.regression.case import RegressionCase
from evals.regression.loader import load_cases, runnable_cases, select_cases

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from evals.harness.driver import EvalRunRecord

logger = logging.getLogger(__name__)

# A dataset task returns one flat case result.
RegressionTaskFn = Callable[[dict[str, Any]], dict[str, Any]]

# Cap the per-file snapshot so a case that writes a huge file can't bloat the Langfuse payload; a file
# over this size is recorded as an elision marker instead of its bytes.
_MAX_SNAPSHOT_BYTES = 64 * 1024


# The stable experiment name every FULL regression run shares, so ``get_experiments_by_name`` finds
# the previous run to compare against. A filtered run appends its slice (see :func:`scoped_name`).
GATE_EXPERIMENT_NAME = "decode-regression-gate"


class RegressionSelectionError(Exception):
    """Nothing safe to run: no case matched the filters, or a matched case has no fresh item.

    Both halves are the same loud, friendly stop as the benchmark's
    :class:`~evals.harness.benchmark.BenchmarkSelectionError` — an empty selection would otherwise
    become ``dataset_item_ids=None``, i.e. a silent paid run over every item in the dataset.
    """


class CaseScopedMetric(BaseMetric):
    """Bind ``inner`` to one case: score only that case's item, contribute nothing to others.

    ``evaluate`` runs every metric against every item; a case's metric is only meaningful on its own
    item (a read-discipline metric grading another case's output is noise — the same reasoning that
    keeps a benchmark's per-task judge off other tasks, ADR-0017 §7). This wrapper reads the item's
    ``case_id`` and delegates to ``inner`` only on a match, else returns ``[]`` — the Langfuse
    adapter treats an empty list as no scores, so the metric does not apply to that item.
    """

    def __init__(self, case_id: str, inner: BaseMetric) -> None:
        super().__init__(name=inner.name, track=False)
        self._case_id = case_id
        self._inner = inner

    def score(self, case_id: Any = None, **kwargs: Any) -> ScoreResult | list[ScoreResult]:
        if case_id != self._case_id:
            return []
        # Forward the case id and payload; metrics absorb fields they do not use.
        return self._inner.score(case_id=case_id, **kwargs)


def make_regression_task_fn(cases_by_id: dict[str, RegressionCase]) -> RegressionTaskFn:
    """Build the task fn that runs + grades one case item (ADR-0017 §4,6).

    The returned closure looks the case up by its input or metadata case id and runs it host-native.
    """

    def regression_task_fn(item: dict[str, Any]) -> dict[str, Any]:
        item_data = item_input(item) if "input" in item else item
        metadata = item_metadata(item)
        case_id = item_data.get("case_id") or metadata.get("case_id")
        case = cases_by_id[str(case_id)]
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
    JSON-serializable for experiment storage; ``file_state`` maps each Workspace-relative path to its text so
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
        "case_id": case.id,
        "session_id": record.session_id if record else None,
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
    client: Any = None,
) -> Any:
    """Run selected cases through one Langfuse experiment (ADR-0022 §8).

    Loads every case, applies the optional ``--case`` / ``--difficulty`` filters, upserts the
    selection into ``decode-regression`` and selects current checksums only. Runs single-threaded
    because the ``bash`` executor seam is process-global.

    ``experiment_name`` defaults to :func:`scoped_name` over the filters — one STABLE name per SLICE
    (``decode-regression-gate`` for the whole suite, ``decode-regression-gate-hard`` for a tier), so
    the threshold ritual's baseline compare finds prior runs by name and compares like-for-like
    instead of diffing a tier against the full suite.
    """
    from evals.harness.datasets import case_checksum, sync_regression_dataset

    all_cases = load_cases()
    selected = runnable_cases(select_cases(all_cases, case_id=case_id, difficulty=difficulty))
    if not selected:
        raise RegressionSelectionError(
            f"no runnable regression case matched (case={case_id!r}, difficulty={difficulty!r}); "
            f"{len(all_cases)} case(s) available."
        )

    client = client or langfuse_client()
    dataset = sync_regression_dataset(selected, client=client)
    item_ids = _selected_item_ids(dataset, {case.id: case_checksum(case) for case in selected})
    missing = {case.id for case in selected} - set(item_ids)
    if missing:
        raise RegressionSelectionError(
            "the sync did not produce a matching dataset item for "
            f"{sorted(missing)} in {getattr(dataset, 'name', 'decode-regression')!r} — "
            "re-run `python -m evals sync --regression`."
        )

    cases_by_id = {case.id: case for case in all_cases}
    task_fn = make_regression_task_fn(cases_by_id)
    metadata = experiment_config(case_count=len(selected), difficulty=difficulty)
    run_name = experiment_name or scoped_name(
        GATE_EXPERIMENT_NAME, case_id=case_id, difficulty=difficulty
    )
    by_id = {str(getattr(item, "id", "")): item for item in dataset_items(dataset)}
    data = []
    for case in selected:
        source = by_id.get(item_ids[case.id])
        source_input = getattr(source, "input", None) if source is not None else None
        source_metadata = getattr(source, "metadata", None) if source is not None else None
        data.append(
            {
                "input": source_input
                if isinstance(source_input, dict)
                else {"case_id": case.id, "prompt": case.prompt},
                "metadata": {
                    **(source_metadata if isinstance(source_metadata, dict) else {}),
                    "case_id": case.id,
                    "difficulty": case.difficulty,
                    "source_dataset_item_id": item_ids[case.id],
                },
            }
        )

    evaluators = [_metric_evaluator(metric) for metric in _scoring_metrics(selected)]
    raw_result = client.run_experiment(
        name=run_name,
        run_name=run_name,
        description="Deterministic decode regression gate",
        data=data,
        task=lambda *, item, **_: task_fn(
            {
                **(item.get("input", {}) if isinstance(item, dict) else {}),
                **(item.get("metadata", {}) if isinstance(item, dict) else {}),
            }
        ),
        evaluators=evaluators,
        run_evaluators=[_mean_evaluator],
        max_concurrency=1,
        metadata=metadata,
    )
    return experiment_result(raw_result, name=run_name, metadata=metadata)


def scoped_name(base: str, *, case_id: str | None = None, difficulty: str | None = None) -> str:
    """``base`` for a full run, ``base-<slice>`` for a filtered one (ADR-0022 §8).

    The EXPERIMENT naming rule: a gate run over one tier logs as ``decode-regression-gate-hard`` so
    its baseline compares against that tier and never against the whole set; ``--case`` wins over
    ``--difficulty`` because it is the narrower filter. Surface (b) does NOT share it — its one
    ``decode-regression-suite`` is reconciled to the selection instead
    (:func:`evals.harness.datasets.sync_regression_cases`).
    """
    slice_name = case_id or difficulty
    return f"{base}-{slice_name}" if slice_name else base


def _selected_item_ids(dataset: Any, checksums: dict[str, str]) -> dict[str, str]:
    """Return current dataset item ids by case checksum."""
    return regression_items_for_dataset(dataset, checksums)


def _scoring_metrics(cases: list[RegressionCase]) -> list[BaseMetric]:
    """Wrap every selected case's metrics in :class:`CaseScopedMetric` (ADR-0017 §6).

    One flat list for the single ``evaluate`` call; each wrapper scores only its own case's item, so a
    multi-case run stays one experiment with clean per-case scores.
    """
    return [CaseScopedMetric(case.id, metric) for case in cases for metric in case.metrics]


def _metric_evaluator(metric: CaseScopedMetric) -> Callable[..., Any]:
    """Adapt one local metric to Langfuse's item evaluator."""
    from langfuse.experiment import Evaluation

    def evaluate(*, output: Any, metadata: dict[str, Any] | None = None, **_: Any) -> Any:
        payload = output if isinstance(output, dict) else {}
        case_id = (metadata or {}).get("case_id")
        result = metric.score(case_id=case_id, **payload)
        if not result:
            return []
        results = result if isinstance(result, list) else [result]
        return [
            Evaluation(
                name=score.name,
                value=score.value,
                comment=score.reason,
                metadata={"scoring_failed": score.scoring_failed},
            )
            for score in results
        ]

    return evaluate


def _mean_evaluator(*, item_results: list[Any], **_: Any) -> Any:
    """Persist per-metric means on the Langfuse experiment run."""
    from types import SimpleNamespace

    from langfuse.experiment import Evaluation

    adapted = result_items(SimpleNamespace(item_results=item_results))
    return [
        Evaluation(
            name=score.name,
            value=score.value,
            comment=score.reason,
            metadata={"scoring_failed": score.scoring_failed},
        )
        for score in mean_per_metric(adapted)
    ]


def experiment_config(*, case_count: int, difficulty: str | None) -> dict[str, Any]:
    """The Langfuse experiment metadata for a regression run — what two rows differ by (ADR-0022 §8).

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


def mean_per_metric(test_results: Sequence[Any]) -> list[ScoreResult]:
    """Fold the run into one ``mean_<metric>`` score per metric, ON the Experiment row (ADR-0022 §8).

    The run evaluator every regression run carries receives item results and logs per-metric means
    onto the Langfuse experiment row instead of deriving them post-hoc from the result.
    Scores are macro-averaged over the items that actually produced them — a case-scoped metric
    contributes nothing to the cases it does not grade (:class:`CaseScopedMetric`).

    Names are prefixed ``mean_`` because experiment-level scores sit beside the
    item-metric averages: an unprefixed name would put two different numbers under one label on the
    same row. A ``scoring_failed`` score is EXCLUDED — a metric that crashed scored nothing, and
    folding its 0.0 in would read as a behavior regression. This adapter is total and never raises.
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
