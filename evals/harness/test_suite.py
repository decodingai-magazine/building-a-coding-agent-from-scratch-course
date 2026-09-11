"""Regression surface (b): Opik Test Suites — natural-language assertions (ADR-0022 §8; §6 kept).

The deliberate CONTRAST to :mod:`evals.harness.regression`. Surface (a) grades behavior with
deterministic ``BaseMetric`` code + a pytest threshold gate; surface (b) grades the SAME cases against
NATURAL-LANGUAGE quality bars — an LLM judge decides whether the agent's answer "invents no file that
does not exist" or "follows the requested template sections". Same cases, a different judgment style;
the contrast IS the teaching point (ADR-0017 §6).

One case declaration feeds both: ``evals/harness/datasets.py`` registers every case's dataset item
(what the metrics gate) AND its Test Suite item carrying the case's own ``assertion`` (what the judge
reads) in one pass. This module only RUNS the suite: it selects the cases (``--case`` /
``--difficulty``, the same filter surface (a) uses), makes sure the suite it is about to run holds
exactly those items, adapts the regression task fn to the Test Suites ``{"input", "output"}``
contract, and gates on ``result.pass_rate``.

``opik.run_tests`` takes no per-item filter — it runs every item of the suite it is handed — so a
FILTERED run registers its own sliced suite (``decode-regression-suite-hard``) rather than billing
every runnable case. ``--difficulty`` is therefore the cost knob on THIS surface too. The full run
uses ``decode-regression-suite``, the one ``python -m evals sync`` writes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from evals.harness.datasets import REGRESSION_SUITE_NAME
from evals.harness.regression import make_regression_task_fn, scoped_name
from evals.regression.loader import load_cases, runnable_cases, select_cases

if TYPE_CHECKING:
    import opik

    from evals.regression.case import RegressionCase

logger = logging.getLogger(__name__)

# The suite-level pass_rate bar: the fraction of items that must pass for the suite to gate green.
# Below it, ``python -m evals suite`` exits non-zero (ADR-0017 §6). A regression gate wants every item
# green, but NL judges are noisy (ADR-0017 consequences), so the bar is not 1.0 — over the whole
# twenty-case set it absorbs four judge misfires, over a filtered tier proportionally fewer. It stays
# 0.8 in v2 (ADR-0022 §8 changed the SCOPE the bar is applied to, not the bar); tune it from the first
# real full runs, not from a guess.
SUITE_PASS_BAR = 0.8


class SuiteSelectionError(Exception):
    """No runnable case matched the suite filter — never a silent empty (but "passing") suite."""


class SuitePassRateError(Exception):
    """The suite ran but its pass rate fell below :data:`SUITE_PASS_BAR` — the gate failed."""


def make_suite_task_fn(
    cases_by_id: dict[str, RegressionCase],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Adapt the regression task fn to the Test Suite ``{"input", "output"}`` contract (ADR-0017 §6).

    Reuses :func:`evals.harness.regression.make_regression_task_fn` — the SAME run-and-grade path
    surface (a) uses — then reshapes its flat payload: ``input`` is JUST the prompt the agent received
    (never the expected answer — the docs warn a leaked expectation lets the judge cheat), and
    ``output`` is the agent's final answer text the NL judge grades. Nothing else from the graded
    payload (``file_state``, the error channels) crosses into the judge's view.
    """
    regression_task_fn = make_regression_task_fn(cases_by_id)

    def suite_task(item: dict[str, Any]) -> dict[str, Any]:
        case = cases_by_id[item["case_id"]]
        payload = regression_task_fn({"case_id": case.id})
        return {"input": {"prompt": case.prompt}, "output": payload["output"]}

    return suite_task


def run_test_suite(
    *,
    case_id: str | None = None,
    difficulty: str | None = None,
    client: opik.Opik | None = None,
) -> Any:
    """Run the selected cases' Test Suite and return its result (ADR-0022 §8).

    Selects the runnable cases the filters name, upserts them into the suite that slice runs under
    (:func:`~evals.harness.regression.scoped_name` over :data:`REGRESSION_SUITE_NAME`), and calls
    ``opik.run_tests``.

    The run is SERIAL by construction (``worker_threads=1``): each item drives the REAL agent
    host-native through the process-global ``bash`` executor seam, so two concurrent items would clash
    — the same reason surface (a) runs ``evaluate(task_threads=1)``. Returns the run result carrying
    ``pass_rate``.
    """
    import opik

    from evals.harness.datasets import sync_regression_cases

    all_cases = load_cases()
    selected = runnable_cases(select_cases(all_cases, case_id=case_id, difficulty=difficulty))
    if not selected:
        raise SuiteSelectionError(
            f"no runnable regression case matched (case={case_id!r}, difficulty={difficulty!r}); "
            f"{len(all_cases)} case(s) available."
        )

    client = client or opik.Opik()
    suite_name = scoped_name(REGRESSION_SUITE_NAME, case_id=case_id, difficulty=difficulty)
    suite = sync_regression_cases(selected, client=client, suite_name=suite_name).suite
    task = make_suite_task_fn({case.id: case for case in all_cases})
    return opik.run_tests(test_suite=suite, task=task, worker_threads=1)


def assert_pass_rate(pass_rate: float, bar: float = SUITE_PASS_BAR) -> None:
    """Raise :class:`SuitePassRateError` when ``pass_rate`` is below ``bar`` — the suite gate (§6).

    Split from the CLI so the gate logic is unit-testable without an Opik run: the CLI turns the raise
    into a non-zero exit.
    """
    if pass_rate < bar:
        raise SuitePassRateError(f"test-suite pass rate {pass_rate:.0%} is below the bar {bar:.0%}")
