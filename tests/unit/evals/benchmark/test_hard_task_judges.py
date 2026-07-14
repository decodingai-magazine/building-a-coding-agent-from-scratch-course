"""The hard benchmark tasks' G-Eval ``judges:`` declarations parse and attach (task 110).

Tasks 015 / 019 / 020 declare an optional G-Eval judge in their ``task.yaml`` (ADR-0017 §7); the
loader must parse each into a :class:`~evals.harness.task_loader.JudgeSpec`, and the benchmark runner
must attach it on a single-task run (and drop per-task judges on a multi-task run, where one task's
rubric can't grade another's output). The other three hard tasks declare no judge. No live judge call
is made — ``make_judge`` construction is offline (proven in ``tests/unit/evals/harness/test_judges``).
"""

from __future__ import annotations

import pytest
from opik.evaluation.metrics import GEval

from evals.harness.benchmark import _scoring_metrics
from evals.harness.task_loader import BENCHMARK_TASKS_DIR, load_benchmark_task

# The hard task -> the single G-Eval judge it declares.
_JUDGED = {
    "015-secret-scrub": "minimal_diff",
    "019-patch-conflict-resolve": "resolution_quality",
    "020-build-small-tool": "code_quality",
}

# The hard tasks that (correctly) declare no judge — code oracles alone grade them.
_UNJUDGED = ("016-implement-from-spec", "017-flaky-test-hunt", "018-git-bisect-revert")


@pytest.mark.parametrize(("slug", "judge_name"), sorted(_JUDGED.items()))
def test_judged_hard_task_parses_its_geval_spec(slug: str, judge_name: str) -> None:
    task = load_benchmark_task(BENCHMARK_TASKS_DIR / slug)

    assert [judge.name for judge in task.judges] == [judge_name]
    assert task.judges[0].task_introduction.strip()
    assert task.judges[0].evaluation_criteria.strip()


@pytest.mark.parametrize("slug", _UNJUDGED)
def test_unjudged_hard_task_declares_no_judge(slug: str) -> None:
    task = load_benchmark_task(BENCHMARK_TASKS_DIR / slug)

    assert task.judges == []


@pytest.mark.parametrize(("slug", "judge_name"), sorted(_JUDGED.items()))
def test_runner_attaches_the_judge_on_a_single_task_run(slug: str, judge_name: str) -> None:
    task = load_benchmark_task(BENCHMARK_TASKS_DIR / slug)

    metrics = _scoring_metrics([task])

    geval = [metric for metric in metrics if isinstance(metric, GEval)]
    assert len(geval) == 1
    # make_judge feeds the declared spec straight into the GEval rubric (ADR-0017 §7).
    assert geval[0].task_introduction == task.judges[0].task_introduction
    assert geval[0].evaluation_criteria == task.judges[0].evaluation_criteria


def test_runner_omits_per_task_judges_on_a_multi_task_run() -> None:
    tasks = [load_benchmark_task(BENCHMARK_TASKS_DIR / slug) for slug in sorted(_JUDGED)]

    metrics = _scoring_metrics(tasks)

    assert not [metric for metric in metrics if isinstance(metric, GEval)]
