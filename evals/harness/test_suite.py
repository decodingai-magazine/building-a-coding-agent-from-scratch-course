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

``opik.run_tests`` takes no per-item filter — it runs every item of the suite it is handed — so the
run first RECONCILES ``decode-regression-suite`` to exactly the selected cases
(:func:`evals.harness.datasets.sync_regression_cases`). A FILTERED run therefore bills only its
selection (``--difficulty`` is the cost knob on THIS surface too), an EDITED case is judged once, and
Opik's native suite versions (``v1``, ``v2``…) keep every earlier item set readable
(ADR-0022 Amendment §15).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from decode.config.settings import settings
from evals.harness.judges import judge_model, judge_provider
from evals.harness.regression import make_regression_task_fn
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

# Where ``run_tests``' JSON report lands: next to the benchmark's Trial Dirs under the Harness Home's
# ``.decode/`` (already git-ignored) — never Opik's default ``./opik_test_suite_reports/`` at the cwd,
# which would litter the repo root with one untracked file per run.
SUITE_REPORTS_DIR_PARTS = ("evals", "suite-reports")


def suite_report_path(suite_version: str | None) -> Path:
    """The report file for one run: ``<decode_dir>/evals/suite-reports/<UTC stamp>-<version>.json``."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{suite_version or 'suite'}.json"
    return Path(settings.decode_dir).joinpath(*SUITE_REPORTS_DIR_PARTS, name)


class SuiteSelectionError(Exception):
    """No runnable case matched the suite filter — never a silent empty (but "passing") suite."""


class SuitePassRateError(Exception):
    """The suite ran but its pass rate fell below :data:`SUITE_PASS_BAR` — the gate failed."""


class SuiteJudgeRouteError(Exception):
    """The judge route cannot ride the Test Suite's assertion judge (a ``modal`` judge, ADR-0022 §7).

    ``opik.run_tests`` hands its ``LLMJudge`` a bare LiteLLM model NAME (``models_factory.get``), so
    the endpoint ``base_url`` + Modal proxy headers a modal judge needs have nowhere to ride — unlike
    the G-Eval judges of surface (a), which take a pre-built :class:`~evals.harness.judges.ModalJudgeModel`.
    Raised before anything is synced or billed, with the two ways out in the message.
    """


@dataclass(frozen=True)
class SuiteRun:
    """One Test Suite run: the suite version it ran and the ``run_tests`` result it returned.

    The version rides out with the result so the CLI PRINTS the item set the run actually billed —
    the run's most useful breadcrumb, read once from the reconcile rather than guessed at the call site.
    """

    suite_version: str | None
    result: Any
    report_path: Path


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
) -> SuiteRun:
    """Run the selected cases' Test Suite and return it with its result (ADR-0022 §8).

    Selects the runnable cases the filters name, upserts them through
    :func:`~evals.harness.datasets.sync_regression_cases` — which reconciles the suite to exactly
    that selection — and calls ``opik.run_tests`` on it.

    The run is SERIAL by construction (``worker_threads=1``): each item drives the REAL agent
    host-native through the process-global ``bash`` executor seam, so two concurrent items would clash
    — the same reason surface (a) runs ``evaluate(task_threads=1)``. Returns the :class:`SuiteRun`
    carrying the suite version and the result (whose ``pass_rate`` the gate reads).

    The assertion judge runs on :func:`~evals.harness.judges.judge_model` — the same
    ``EVAL_JUDGE_PROVIDER`` / ``EVAL_JUDGE_MODEL`` routing surface (a) uses — passed to ``run_tests``
    as its ``model``; left unset, Opik would silently grade on its own default (``gpt-5-nano``, an
    ``OPENAI_API_KEY`` this project never configures). A ``modal`` judge route raises
    :class:`SuiteJudgeRouteError` up front (see its docstring).
    """
    import opik

    from evals.harness.datasets import sync_regression_cases

    if judge_provider() == "modal":
        raise SuiteJudgeRouteError(
            "the Test Suite's assertion judge takes a bare model name and cannot carry a Modal "
            "endpoint's base url + proxy headers. Run it with EVAL_JUDGE_PROVIDER=gemini (or "
            "openrouter), or use `make eval-regression-dataset`, whose G-Eval judges do run on modal."
        )
    all_cases = load_cases()
    selected = runnable_cases(select_cases(all_cases, case_id=case_id, difficulty=difficulty))
    if not selected:
        raise SuiteSelectionError(
            f"no runnable regression case matched (case={case_id!r}, difficulty={difficulty!r}); "
            f"{len(all_cases)} case(s) available."
        )

    client = client or opik.Opik()
    surfaces = sync_regression_cases(selected, client=client)
    task = make_suite_task_fn({case.id: case for case in all_cases})
    report_path = suite_report_path(surfaces.suite_version)
    result = opik.run_tests(
        test_suite=surfaces.suite,
        task=task,
        worker_threads=1,
        model=judge_model(),
        report_output_path=str(report_path),
    )
    return SuiteRun(suite_version=surfaces.suite_version, result=result, report_path=report_path)


def assert_pass_rate(pass_rate: float, bar: float = SUITE_PASS_BAR) -> None:
    """Raise :class:`SuitePassRateError` when ``pass_rate`` is below ``bar`` — the suite gate (§6).

    Split from the CLI so the gate logic is unit-testable without an Opik run: the CLI turns the raise
    into a non-zero exit.
    """
    if pass_rate < bar:
        raise SuitePassRateError(f"test-suite pass rate {pass_rate:.0%} is below the bar {bar:.0%}")
