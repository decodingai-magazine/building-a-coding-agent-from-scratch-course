"""The Opik glue that runs the behavior probes host-native and scores them (ADR-0017 §3,4,6).

Two pieces sit on top of the eval driver (:mod:`evals.harness.driver`) and the probe registry
(:mod:`evals.regression.loader`):

* :func:`make_regression_task_fn` builds the sync Opik task fn. For one dataset item it builds the
  probe's fixture in a fresh ``tempfile`` dir, runs the REAL agent HOST-NATIVE (``sandbox_mode`` forced
  to ``none`` — fast, no docker) under the probe's gate policy / resolvers / pre-filled history / request
  cap, snapshots the resulting file tree, and returns the flat payload the code metrics + judges read.
  The task fn NEVER raises: a crashed agent run grades as fail-with-reason (``agent_error``) and a
  fixture / setup failure grades as fail-with-reason (``infra_error``), because Opik's ``evaluate`` gives
  task fns no per-item isolation — one raise would abort the whole experiment (task 106 lesson). The
  temp dir is always removed.
* :func:`run_regression` loads + filters the probes, upserts them into ``decode-regression-v1``, and
  calls ``opik.evaluation.evaluate`` scoped to the selected items with the probe-scoped metrics,
  ``experiment_config`` carrying the agent model + provider + git sha, and
  ``project_name=settings.eval_project_name`` so eval runs never pollute live REPL tracing.

Per-probe metric binding: ``evaluate`` applies ONE metric list to EVERY item, but each probe declares
its OWN metrics. :class:`ProbeScopedMetric` wraps every probe's metric so it scores only the item whose
``probe_id`` matches and returns ``[]`` (zero scores) for every other item — so a single ``evaluate``
call returns one :class:`~opik.evaluation.evaluation_result.EvaluationResult` (what the task-115
threshold gate aggregates) with clean per-probe scores, no cross-probe noise.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from opik.evaluation.metrics.base_metric import BaseMetric

from decode.config.settings import settings
from decode.tools.bash import reset_executor
from evals.harness.driver import run_agent_once_sync
from evals.regression.loader import load_probes
from evals.regression.probe import RegressionProbe

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    import opik
    from opik.evaluation.evaluation_result import EvaluationResult
    from opik.evaluation.metrics.score_result import ScoreResult

    from evals.harness.driver import EvalRunRecord

logger = logging.getLogger(__name__)

# The Opik task fn Opik hands one dataset item and expects one flat output dict back.
RegressionTaskFn = Callable[[dict[str, Any]], dict[str, Any]]

# Cap the per-file snapshot so a probe that writes a huge file can't bloat the Opik payload; a file
# over this size is recorded as an elision marker instead of its bytes.
_MAX_SNAPSHOT_BYTES = 64 * 1024


class RegressionSelectionError(Exception):
    """No regression probe matched the ``--probe`` filter — a loud, friendly stop."""


class ProbeScopedMetric(BaseMetric):
    """Bind ``inner`` to one probe: score only that probe's item, contribute nothing to others.

    ``evaluate`` runs every metric against every item; a probe's metric is only meaningful on its own
    item (a read-discipline metric grading another probe's output is noise — the same reasoning that
    keeps a benchmark's per-task judge off other tasks, ADR-0017 §7). This wrapper reads the item's
    ``probe_id`` and delegates to ``inner`` only on a match, else returns ``[]`` — Opik treats an empty
    list as zero score results, so the metric simply does not apply to that item. ``track=False`` for
    the same offline reason the code metrics use (ADR-0017 §9).
    """

    def __init__(self, probe_id: str, inner: BaseMetric) -> None:
        super().__init__(name=inner.name, track=False)
        self._probe_id = probe_id
        self._inner = inner

    def score(self, probe_id: Any = None, **kwargs: Any) -> ScoreResult | list[ScoreResult]:
        if probe_id != self._probe_id:
            return []
        # Forward the full scoring dict (probe_id included) exactly as Opik would to a bare metric;
        # ``inner`` absorbs what it does not name through its own ``**ignored_kwargs``.
        return self._inner.score(probe_id=probe_id, **kwargs)


def make_regression_task_fn(probes_by_id: dict[str, RegressionProbe]) -> RegressionTaskFn:
    """Build the sync Opik task fn that runs + grades one probe item (ADR-0017 §4,6).

    The returned closure looks the probe up by ``item["probe_id"]`` and runs it host-native. Sync
    because Opik ``evaluate()`` task fns cannot be async.
    """

    def regression_task_fn(item: dict[str, Any]) -> dict[str, Any]:
        probe = probes_by_id[item["probe_id"]]
        return run_probe(probe)

    return regression_task_fn


def run_probe(probe: RegressionProbe) -> dict[str, Any]:
    """Run one probe host-native in a fresh temp Workspace, returning a graded payload — never a raise.

    Forces ``sandbox_mode`` to ``none`` (host-native — no docker) and resets the ``bash`` executor seam
    so the run is byte-identical to a plain host session, restoring both in the ``finally`` along with
    removing the temp Workspace. The probe's ``settings_overrides`` are applied over the same window and
    rolled back in the same ``finally`` (the compaction probe shrinks the context window so its
    near-limit history actually triggers). A fixture / history-builder failure is caught into
    ``infra_error``; a crashed agent run into ``agent_error`` (both inside :func:`_build_and_run`) —
    either way a payload the metrics grade comes back, so one broken probe never aborts the experiment
    (task 106 lesson).
    """
    workspace = Path(tempfile.mkdtemp(prefix="decode-regression-")).resolve()
    previous_mode = settings.sandbox_mode
    settings.sandbox_mode = "none"
    saved_settings: dict[str, Any] = {}
    reset_executor()
    try:
        saved_settings = _apply_settings_overrides(probe.settings_overrides)
        return _build_and_run(probe, workspace)
    except (
        Exception
    ) as exc:  # a fixture / setup / override failure must not abort the whole experiment
        logger.exception("[eval] regression probe setup failed for %s", probe.id)
        return _payload(None, file_state={}, probe=probe, agent_error=None, infra_error=str(exc))
    finally:
        settings.sandbox_mode = previous_mode
        _restore_settings(saved_settings)
        reset_executor()
        shutil.rmtree(workspace, ignore_errors=True)


def _apply_settings_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Apply ``overrides`` to the global ``settings`` singleton, returning the prior values.

    Same pattern the ``sandbox_mode`` swap uses above: a probe forces a handful of settings for the
    duration of ONE run (the compaction probe shrinks the context window / keep-recent tail so its
    near-limit history crosses the trigger), and :func:`_restore_settings` puts them back in the
    ``finally``. Every key is validated BEFORE any is applied, so an unknown attribute fails loudly
    (surfaced as ``infra_error``) without leaving a half-applied settings state behind.
    """
    for key in overrides:
        if not hasattr(settings, key):
            raise AttributeError(f"unknown settings override {key!r} on probe")
    saved: dict[str, Any] = {}
    for key, value in overrides.items():
        saved[key] = getattr(settings, key)
        setattr(settings, key, value)
    return saved


def _restore_settings(saved: Mapping[str, Any]) -> None:
    for key, value in saved.items():
        setattr(settings, key, value)


def _build_and_run(probe: RegressionProbe, workspace: Path) -> dict[str, Any]:
    """Seed the fixture, drive the agent, snapshot the tree — the happy body of :func:`run_probe`.

    The fixture and optional ``message_history`` builder run first (a raise here propagates to
    ``run_probe``'s ``infra_error`` handler). The agent run itself is caught into ``agent_error`` so a
    crashed run still yields a graded payload (the file snapshot is taken regardless). An optional
    ``probe.context`` (e.g. a live ``http.server``) is entered around the run only.
    """
    from contextlib import nullcontext

    probe.fixture(workspace)
    history = probe.message_history() if probe.message_history else None

    record: EvalRunRecord | None = None
    agent_error: str | None = None
    context: AbstractContextManager[Any] = (
        probe.context(workspace) if probe.context else nullcontext()
    )
    try:
        with context:
            record = run_agent_once_sync(
                probe.prompt,
                cwd=workspace,
                gate_mode=probe.gate_mode,
                permission_rules=probe.permission_rules,
                resolve_permission=probe.resolve_permission,
                resolve_user_question=probe.resolve_user_question,
                message_history=history,
                max_requests=probe.max_requests,
                enable_compaction=probe.enable_compaction,
            )
        agent_error = record.agent_error
    except Exception as exc:  # a crashed agent still grades — fail-with-reason, not skipped
        logger.exception("[eval] regression agent run raised for %s", probe.id)
        agent_error = f"agent run raised: {exc}"

    return _payload(
        record,
        file_state=_snapshot(workspace),
        probe=probe,
        agent_error=agent_error,
        infra_error=None,
    )


def _payload(
    record: EvalRunRecord | None,
    *,
    file_state: dict[str, str],
    probe: RegressionProbe,
    agent_error: str | None,
    infra_error: str | None,
) -> dict[str, Any]:
    """The flat output dict the regression metrics + judges read (ADR-0017 §4,6).

    ``tool_calls`` is de-dataclassed to plain ``{"name", "args"}`` dicts so the payload is
    JSON-serializable for Opik storage; ``file_state`` maps each Workspace-relative path to its text so
    a metric can assert a file was created / edited. ``max_steps`` mirrors the probe's request cap for
    :class:`~evals.harness.metrics.MaxStepsMetric` (``None`` when the probe sets no cap).
    ``compaction_events`` reports how many times the auto-compaction cascade fired (the
    compaction-survival probe reads it to prove firing). Two failure
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
        "max_steps": probe.max_requests,
        "agent_error": agent_error,
        "infra_error": infra_error,
    }


def _snapshot(workspace: Path) -> dict[str, str]:
    """Snapshot the Workspace's text files as ``{relative_posix_path: content}`` (ADR-0017 §6).

    Walks ``workspace`` for regular files, skipping the ``.git`` tree (a probe may seed git history —
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
    probe_id: str | None = None,
    nb_samples: int | None = None,
    experiment_name: str | None = None,
    client: opik.Opik | None = None,
) -> EvaluationResult:
    """Run the selected probes as one Opik experiment and return its result (ADR-0017 §3,4,6).

    Loads every probe, applies the optional ``--probe`` id filter, upserts the selection into
    ``decode-regression-v1``, and calls ``evaluate`` scoped (via ``dataset_item_ids``) to just those
    items with the probe-scoped metrics. ``experiment_config`` records the agent model, provider and git
    sha; ``project_name`` is ``settings.eval_project_name`` so live tracing stays clean. Runs
    single-threaded — the ``bash`` executor seam is process-global. Raises
    :class:`RegressionSelectionError` when nothing matches.

    ``experiment_name`` (``None`` = opik auto-names) gives every regression run a STABLE experiment name
    so the task-115 threshold ritual can find prior runs by name (``get_experiments_by_name``) and warn
    on per-metric regressions vs the last one.
    """
    import opik
    from opik.evaluation import evaluate

    from evals.harness.datasets import sync_regression_dataset

    all_probes = load_probes()
    selected = _runnable(_select_probes(all_probes, probe_id=probe_id))
    if not selected:
        raise RegressionSelectionError(
            f"no runnable regression probe matched (probe={probe_id!r}); {len(all_probes)} probe(s) available."
        )

    client = client or opik.Opik()
    dataset = sync_regression_dataset(selected, client=client)
    item_ids = _selected_item_ids(dataset, {probe.id for probe in selected})

    probes_by_id = {probe.id: probe for probe in all_probes}
    task_fn = make_regression_task_fn(probes_by_id)
    return evaluate(
        dataset=dataset,
        task=task_fn,
        scoring_metrics=_scoring_metrics(selected),
        experiment_config=experiment_config(),
        experiment_name=experiment_name,
        project_name=settings.eval_project_name,
        nb_samples=nb_samples,
        dataset_item_ids=item_ids or None,
        task_threads=1,
    )


def _select_probes(probes: list[RegressionProbe], *, probe_id: str | None) -> list[RegressionProbe]:
    """Filter loaded probes by exact ``probe_id`` (``None`` selects the whole suite)."""
    if probe_id is None:
        return probes
    return [probe for probe in probes if probe.id == probe_id]


def _runnable(probes: list[RegressionProbe]) -> list[RegressionProbe]:
    """Drop the skip-guarded probes from a run, logging each reason (ADR-0017 §10; the MCP probe).

    A ``skip_reason`` probe is DECLARED (discoverable via :func:`~evals.regression.loader.load_probes`)
    but not yet runnable — decode's MCP tool factory has not shipped, so probe 12 would grade a
    behavior the agent cannot perform. It is excluded here rather than at load time so the registry
    still lists it and it activates the moment its ``skip_reason`` is removed.
    """
    runnable: list[RegressionProbe] = []
    for probe in probes:
        if probe.skip_reason is not None:
            logger.info("[eval] skipping regression probe %s: %s", probe.id, probe.skip_reason)
            continue
        runnable.append(probe)
    return runnable


def _selected_item_ids(dataset: Any, selected_ids: set[str]) -> list[str]:
    """The dataset item ids whose ``probe_id`` is in ``selected_ids`` — scopes ``evaluate`` to the run.

    Reads the just-synced dataset's items and keeps those the filter selected, so a run over the shared
    ``decode-regression-v1`` dataset executes only the chosen probes. An item missing an ``id`` (an
    unexpected Opik shape) is skipped rather than crashing the run.
    """
    items = dataset.get_items()
    return [
        item["id"]
        for item in items
        if item.get("probe_id") in selected_ids and item.get("id") is not None
    ]


def _scoring_metrics(probes: list[RegressionProbe]) -> list[BaseMetric]:
    """Wrap every selected probe's metrics in :class:`ProbeScopedMetric` (ADR-0017 §6).

    One flat list for the single ``evaluate`` call; each wrapper scores only its own probe's item, so a
    multi-probe run stays one experiment with clean per-probe scores.
    """
    return [ProbeScopedMetric(probe.id, metric) for probe in probes for metric in probe.metrics]


def experiment_config() -> dict[str, Any]:
    """The Opik ``experiment_config`` for a regression run: agent model, provider, git sha (ADR-0017 §8).

    Reuses the benchmark harness's model/sha resolvers (the same evals harness) so two experiment rows
    are told apart by what actually changed. ``harness`` labels the row as a regression run (always
    host-native ``none`` mode).
    """
    from evals.harness.benchmark import agent_model, git_sha

    return {
        "agent_model": agent_model(),
        "provider": settings.llm_provider,
        "git_sha": git_sha(),
        "harness": "regression",
    }
