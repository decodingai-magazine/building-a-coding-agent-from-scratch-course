"""Small project-local adapters around Langfuse dataset experiments."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExperimentScore:
    name: str
    value: float
    comment: str | None = None
    scoring_failed: bool = False


@dataclass(frozen=True)
class ExperimentCase:
    dataset_item_content: dict[str, Any]
    task_output: dict[str, Any]


@dataclass(frozen=True)
class ExperimentItem:
    test_case: ExperimentCase
    score_results: list[ExperimentScore]


@dataclass(frozen=True)
class AggregatedScore:
    mean: float


@dataclass(frozen=True)
class AggregatedEvaluation:
    aggregated_scores: dict[str, AggregatedScore]


@dataclass(frozen=True)
class ExperimentResult:
    name: str
    experiment_id: str
    test_results: list[ExperimentItem]
    experiment_config: dict[str, Any]
    pass_rate: float | None = None

    def aggregate_evaluation_scores(self) -> AggregatedEvaluation:
        """Return the mean of each successful item-level score."""
        values: dict[str, list[float]] = defaultdict(list)
        for result in self.test_results:
            for score in result.score_results:
                if not score.scoring_failed:
                    values[score.name].append(score.value)
        return AggregatedEvaluation(
            {
                name: AggregatedScore(sum(scores) / len(scores))
                for name, scores in values.items()
                if scores
            }
        )


def mapping(value: Any) -> dict[str, Any]:
    """Convert SDK model objects and mappings into plain dictionaries."""
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        result = value.model_dump()
        return result if isinstance(result, dict) else {}
    if hasattr(value, "dict"):
        result = value.dict()
        return result if isinstance(result, dict) else {}
    return {}


def item_input(item: Any) -> dict[str, Any]:
    """Read an experiment input from either Langfuse's local or dataset item shape."""
    if isinstance(item, dict):
        return mapping(item.get("input"))
    return mapping(getattr(item, "input", None))


def item_metadata(item: Any) -> dict[str, Any]:
    """Read experiment item metadata."""
    if isinstance(item, dict):
        return mapping(item.get("metadata"))
    return mapping(getattr(item, "metadata", None))


def result_items(result: Any) -> list[ExperimentItem]:
    """Adapt Langfuse SDK item results to the evaluation harness's shared result shape."""
    adapted: list[ExperimentItem] = []
    for entry in getattr(result, "item_results", []) or []:
        item = getattr(entry, "item", None)
        source = {**item_input(item), **item_metadata(item)}
        output = mapping(getattr(entry, "output", None))
        scores = [
            ExperimentScore(
                name=str(getattr(score, "name", "")),
                value=float(getattr(score, "value", 0.0)),
                comment=getattr(score, "comment", None),
                scoring_failed=bool(
                    mapping(getattr(score, "metadata", None)).get("scoring_failed")
                ),
            )
            for score in (getattr(entry, "evaluations", []) or [])
            if isinstance(getattr(score, "value", None), (int, float))
        ]
        adapted.append(
            ExperimentItem(
                test_case=ExperimentCase(dataset_item_content=source, task_output=output),
                score_results=scores,
            )
        )
    return adapted


def experiment_result(
    result: Any,
    *,
    name: str,
    metadata: dict[str, Any],
) -> ExperimentResult:
    """Normalize one Langfuse SDK experiment result."""
    items = result_items(result)
    passing = [item for item in items if item.test_case.task_output.get("status") != "infra_error"]
    passed = sum(
        1
        for item in passing
        if item.test_case.task_output.get("pass") is True
        or item.test_case.task_output.get("reward") == 1
    )
    return ExperimentResult(
        name=getattr(result, "name", None) or name,
        experiment_id=str(getattr(result, "experiment_id", "")),
        test_results=items,
        experiment_config=metadata,
        pass_rate=(passed / len(passing)) if passing else 0.0,
    )
