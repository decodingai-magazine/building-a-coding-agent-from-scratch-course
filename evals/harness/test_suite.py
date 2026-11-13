"""Natural-language regression assertions executed as a Langfuse experiment."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from decode.config.settings import settings
from evals.harness.datasets import (
    GLOBAL_ASSERTIONS,
    REGRESSION_SUITE_NAME,
    langfuse_client,
    sync_regression_cases,
)
from evals.harness.judges import make_judge
from evals.harness.langfuse_experiments import experiment_result, item_input, item_metadata
from evals.harness.regression import make_regression_task_fn
from evals.regression.case import RegressionCase
from evals.regression.loader import load_cases, runnable_cases, select_cases

logger = logging.getLogger(__name__)

SUITE_PASS_BAR = 0.8
SUITE_REPORTS_DIR_PARTS = ("evals", "suite-reports")


def suite_report_path(suite_version: str | None) -> Path:
    """Create a unique report path under Harness Home."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return Path(settings.decode_dir).joinpath(
        *SUITE_REPORTS_DIR_PARTS, f"{stamp}-{suite_version or 'suite'}.json"
    )


class SuiteSelectionError(Exception):
    """No runnable case matched the requested selection."""


class SuitePassRateError(Exception):
    """The assertion pass rate fell below the gate."""


class SuiteJudgeRouteError(Exception):
    """Compatibility exception retained for the existing CLI contract."""


@dataclass(frozen=True)
class SuiteRun:
    """One natural-language assertion run."""

    suite_version: str | None
    result: Any
    report_path: Path
    session_ids: dict[str, str]


def make_suite_task_fn(
    cases_by_id: dict[str, RegressionCase],
    *,
    session_ids: dict[str, str] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Adapt the regression task to the judge-visible prompt/answer pair."""
    regression_task_fn = make_regression_task_fn(cases_by_id)

    def suite_task(item: dict[str, Any]) -> dict[str, Any]:
        data = item_input(item) if "input" in item else item
        metadata = item_metadata(item)
        case_id = str(data.get("case_id") or metadata.get("case_id"))
        case = cases_by_id[case_id]
        payload = regression_task_fn({"case_id": case.id})
        session_id = payload.get("session_id")
        if session_ids is not None and isinstance(session_id, str) and session_id:
            session_ids[case.id] = session_id
        return {"input": {"prompt": case.prompt}, "output": payload.get("output", "")}

    return suite_task


def _suite_judge(
    *,
    input: Any,
    output: Any,
    expected_output: Any,
    metadata: dict[str, Any] | None = None,
    **_: Any,
) -> Any:
    """Judge one answer against the case assertion and suite-wide criteria."""
    from langfuse.experiment import Evaluation

    assertions = expected_output.get("assertions", []) if isinstance(expected_output, dict) else []
    rubric = "\n".join(f"- {assertion}" for assertion in assertions)
    score = make_judge(
        "Judge whether the assistant response satisfies the requested quality criteria.",
        rubric,
    ).score(input=input, output=output.get("output") if isinstance(output, dict) else output)
    passed = not score.scoring_failed and score.value >= 0.5
    return Evaluation(
        name="assertion_pass",
        value=1.0 if passed else 0.0,
        comment=score.reason,
        metadata={"case_id": (metadata or {}).get("case_id")},
    )


def run_test_suite(
    *,
    case_id: str | None = None,
    difficulty: str | None = None,
    client: Any = None,
) -> SuiteRun:
    """Run selected cases through Langfuse's experiment API and local judges."""
    all_cases = load_cases()
    selected = runnable_cases(select_cases(all_cases, case_id=case_id, difficulty=difficulty))
    if not selected:
        raise SuiteSelectionError(
            f"no runnable regression case matched (case={case_id!r}, difficulty={difficulty!r}); "
            f"{len(all_cases)} case(s) available."
        )

    client = client or langfuse_client()
    surfaces = sync_regression_cases(selected, client=client)
    session_ids: dict[str, str] = {}
    task = make_suite_task_fn({case.id: case for case in all_cases}, session_ids=session_ids)
    data = [
        {
            "input": {"case_id": case.id, "prompt": case.prompt},
            "expected_output": {
                "assertions": [case.assertion, *GLOBAL_ASSERTIONS],
            },
            "metadata": {
                "case_id": case.id,
                "difficulty": case.difficulty,
                "description": case.description,
            },
        }
        for case in selected
    ]
    version = surfaces.suite_version
    raw_result = client.run_experiment(
        name=REGRESSION_SUITE_NAME,
        run_name=version or "suite",
        description="Natural-language decode regression assertions",
        data=data,
        task=lambda *, item, **_: task(item),
        evaluators=[_suite_judge],
        max_concurrency=1,
        metadata={
            "suite_version": version or "",
            "case_count": len(selected),
            "judge_model": settings.eval_judge_model,
        },
    )
    result = experiment_result(
        raw_result,
        name=REGRESSION_SUITE_NAME,
        metadata={"suite_version": version or ""},
    )
    graded = result.test_results
    passed = sum(
        1
        for item in graded
        if any(
            score.name == "assertion_pass" and score.value >= 1.0 for score in item.score_results
        )
    )
    result = replace(result, pass_rate=passed / len(graded) if graded else 0.0)

    report_path = suite_report_path(version)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "suite_version": version,
                "pass_rate": result.pass_rate,
                "results": [
                    {
                        "case_id": item.test_case.dataset_item_content.get("case_id"),
                        "output": item.test_case.task_output,
                        "scores": [score.__dict__ for score in item.score_results],
                    }
                    for item in result.test_results
                ],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return SuiteRun(version, result, report_path, session_ids)


def assert_pass_rate(pass_rate: float, bar: float = SUITE_PASS_BAR) -> None:
    """Raise when the natural-language assertion gate falls below its pass bar."""
    if pass_rate < bar:
        raise SuitePassRateError(f"test-suite pass rate {pass_rate:.0%} is below the bar {bar:.0%}")
