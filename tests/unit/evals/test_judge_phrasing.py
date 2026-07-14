"""Guard: no G-Eval judge may phrase its rubric as a numeric verdict (ADR-0017 §6,7; task 121).

Task 114 proved with live Opik experiments that a criterion written as "Score 1.0 when … Score 0.0
when …" collides with the judge's internal 0-10 scale and produces incoherent scores (a *perfect*
answer scored 0.1). ``evals/README.md`` §online step 6 forbids the pattern in bold; probes 17/18/19
carry the "Phrased qualitatively — NOT as 'Score 1.0/0.0'" comment. This test makes the ban
STRUCTURAL: it scans every loaded regression probe's G-Eval criteria AND every benchmark
``task.yaml`` judge spec, and fails on any numeric-verdict anchor — so probe/task 21+ can never
silently reintroduce the anti-pattern the repo's own docs forbid.

The regex ``Score <0|1>[.decimals]`` matches the exact shapes the seven violating judges shipped
("Score 1.0 when", "Score 0.0 for", "Score 1 if", "Score 0 if") while leaving "high-scoring",
"scored 0.1", and the 0-10 scale text untouched.
"""

from __future__ import annotations

import re

from opik.evaluation.metrics import GEval

from evals.harness.task_loader import load_benchmark_tasks
from evals.regression.loader import load_probes

# A numeric verdict anchor: the word "Score" immediately followed by a 0 or 1 (optionally decimal).
# ``\b`` bounds keep it off "scored"/"scoring" and off the "0-10" scale prose. Case-insensitive so a
# lowercase "score 1" is caught too.
_NUMERIC_ANCHOR = re.compile(r"\bscore\s+[01](?:\.\d+)?\b", re.IGNORECASE)


def _offenders(text: str) -> list[str]:
    """The numeric-anchor substrings in ``text`` — empty when the phrasing is qualitative."""
    return _NUMERIC_ANCHOR.findall(text)


def test_no_regression_probe_judge_uses_a_numeric_anchor():
    """Every regression probe's G-Eval criteria state qualities, never a "Score 1.0/0.0" verdict."""
    violations: list[str] = []
    for probe in load_probes():
        for metric in probe.metrics:
            if not isinstance(metric, GEval):
                continue
            for field in (metric.task_introduction, metric.evaluation_criteria):
                if _NUMERIC_ANCHOR.search(field):
                    violations.append(f"{probe.id}: {_offenders(field)}")

    assert not violations, (
        "G-Eval judges must phrase criteria qualitatively, never as a numeric verdict "
        "(task-114 lesson; see evals/README.md step 6). Offenders: " + "; ".join(violations)
    )


def test_no_benchmark_task_judge_uses_a_numeric_anchor():
    """Every benchmark ``task.yaml`` judge spec states qualities, never a "Score 1 if" verdict."""
    violations: list[str] = []
    for task in load_benchmark_tasks():
        for judge in task.judges:
            for field in (judge.task_introduction, judge.evaluation_criteria):
                if _NUMERIC_ANCHOR.search(field):
                    violations.append(f"{task.id}/{judge.name}: {_offenders(field)}")

    assert not violations, (
        "Benchmark judges must phrase criteria qualitatively, never as a numeric verdict "
        "(task-114 lesson; see evals/README.md step 6). Offenders: " + "; ".join(violations)
    )


def test_the_guard_regex_matches_the_known_anti_patterns_but_not_qualitative_prose():
    """The regex catches the shapes the seven judges shipped and spares legitimate phrasing.

    Pins the guard itself so a future loosening of the pattern (which would let the anti-pattern
    back in) fails here rather than silently passing the scans above.
    """
    for anchored in ("Score 1.0 when the answer", "Score 0.0 for any other", "Score 1 if clean"):
        assert _NUMERIC_ANCHOR.search(anchored), anchored
    for qualitative in (
        "The answer is fully correct when it states the value",
        "A high-scoring answer resolves the request",
        "a perfect answer scored 0.1 in QA",
        "a response_quality (0-10) feedback score",
    ):
        assert not _NUMERIC_ANCHOR.search(qualitative), qualitative
