"""Regression surface (b): Opik 2.0 Test Suites — natural-language assertions (ADR-0017 §6; task 116).

The deliberate CONTRAST to :mod:`evals.harness.regression`. Surface (a) grades behavior with
deterministic ``BaseMetric`` code + a pytest threshold gate; surface (b) grades a small subset of the
most judge-flavored probes against NATURAL-LANGUAGE quality bars — an LLM judge decides whether the
agent's answer "invents no file that does not exist" or "follows the requested template sections". Same
probes, a different judgment style; the contrast IS the teaching point (ADR-0017 §6).

**Version gate (task 116, path b).** The Test Suites API — ``Opik.get_or_create_test_suite`` /
``opik.run_tests`` / ``result.pass_rate`` — is Opik 2.0. This repo is pinned to opik 1.9.8 because opik
2.x pulls litellm 1.92, whose Rust bridge needs rustc>=1.86 to build from sdist and ships NO prebuilt
macOS wheel (this host has rustc 1.85.1, so ``uv`` would build from sdist and fail). The code here is
written verbatim against the DOCUMENTED 2.0 API so it activates the moment the pin is lifted; until
then :func:`run_test_suite` detects the missing surface up front and raises
:class:`SuiteUnavailableError` with the reason. Unit tests mock the 2.0 surface. See
``tasks/116-opik-test-suites-surface.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from decode.config.settings import settings
from evals.harness.regression import make_regression_task_fn
from evals.regression.loader import load_probes

if TYPE_CHECKING:
    import opik

    from evals.regression.probe import RegressionProbe

logger = logging.getLogger(__name__)

# The single Test Suite name (the surface-(b) analogue of REGRESSION_DATASET_NAME). One suite version.
TEST_SUITE_NAME = "decode-regression-suite"

# The suite-level pass_rate bar: the fraction of assertion checks that must pass for the suite to gate
# green. Below it, ``python -m evals suite`` exits non-zero (ADR-0017 §6). A regression gate wants every
# item green, but NL judges are noisy (ADR-0017 consequences), so the bar leaves one flaky check of
# headroom over the 5-probe subset rather than demanding a perfect 1.0.
SUITE_PASS_BAR = 0.8

# The ~5 judge-flavored probes this NL-assertion surface covers (ADR-0017 §6 — "a small probe subset",
# NOT all 20). 17/18/19 are the ADR-named judge probes; 05 (grounded web-fetch, judge-flavored) and 20
# (JSON output contract) are the two picks. Every one grades a quality visible in the agent's ANSWER
# TEXT — which is exactly what an NL judge reads (``input`` + ``output``). Behavioral probes whose signal
# is a tool call or a denied mutation stay on surface (a), where code metrics read them directly.
SUITE_PROBE_IDS: tuple[str, ...] = (
    "17-grounded-answer",
    "18-no-hallucinated-files",
    "19-template-compliance",
    "20-json-output-contract",
    "05-web-fetch-discipline",
)

# Cross-cutting NL quality bars every item is graded against (the suite's ``global_assertions``).
GLOBAL_ASSERTIONS: tuple[str, ...] = (
    "The response directly addresses what the user asked in the prompt.",
    "The response does not invent files, functions, or facts that are not grounded in the "
    "workspace the agent was given or in the user's own prompt.",
)

# Per-probe NL assertions checked IN ADDITION to the global ones (the docs' item-level ``assertions``).
# Each is a rubric, not judge-visible input — it states the quality bar WITHOUT leaking an expected
# value (05 says "the rate limit stated on the fetched page", never the number), so a judge reading
# ``input``/``output`` cannot cheat off it.
ITEM_ASSERTIONS: dict[str, tuple[str, ...]] = {
    "17-grounded-answer": (
        "The response answers the question using the content of the source document the user pointed "
        "to, rather than a guessed or generic answer.",
    ),
    "18-no-hallucinated-files": (
        "The response states that the requested file was not found or does not exist, and does not "
        "fabricate its contents, purpose, or functions.",
    ),
    "19-template-compliance": (
        "The response is organized under the exact section headings the requested template names, in "
        "that order, with each section filled in rather than left empty or replied to in free-form "
        "prose.",
    ),
    "20-json-output-contract": (
        "The response is a single raw JSON object and nothing else — no surrounding prose, "
        "explanation, or Markdown code fence.",
    ),
    "05-web-fetch-discipline": (
        "The response reports the rate limit stated on the page the agent fetched, rather than "
        "guessing a value or refusing to answer.",
    ),
}


class SuiteUnavailableError(Exception):
    """The installed Opik lacks the 2.0 Test Suites API — a loud, friendly stop (task 116, path b)."""


class SuiteSelectionError(Exception):
    """None of :data:`SUITE_PROBE_IDS` resolved against the registry — never a silent empty suite."""


class SuitePassRateError(Exception):
    """The suite ran but its pass rate fell below :data:`SUITE_PASS_BAR` — the gate failed."""


def suite_api_available(client: Any) -> bool:
    """Whether the installed Opik exposes the 2.0 Test Suites surface (task 116, path b).

    Both halves are required: the suite is built off ``client.get_or_create_test_suite`` and run through
    the module-level ``opik.run_tests``. opik 1.9.8 has neither; opik>=2.0 has both.
    """
    import opik

    return hasattr(client, "get_or_create_test_suite") and hasattr(opik, "run_tests")


def _unavailable_message() -> str:
    """The version-gate stop message — reads the INSTALLED opik version live (task 116, path b)."""
    import opik

    version = getattr(opik, "__version__", "unknown")
    return (
        f"Opik Test Suites need opik>=2.0, but opik {version} is installed. The repo pins litellm<1.78 "
        "because opik 2.x pulls litellm 1.92, whose Rust bridge needs rustc>=1.86 to build from sdist "
        "and ships no prebuilt macOS wheel (this host has rustc 1.85.1). Lift the opik/litellm pins in "
        "pyproject.toml once the build host has rustc>=1.86, and this surface activates unchanged. See "
        "tasks/116-opik-test-suites-surface.md."
    )


def select_suite_probes(probes: list[RegressionProbe]) -> list[RegressionProbe]:
    """The subset of ``probes`` this surface covers, in :data:`SUITE_PROBE_IDS` order (task 116).

    A subset id missing from the registry is skipped with a warning rather than crashing the run — a
    probe rename should degrade the suite, not silently take the whole thing down.
    """
    by_id = {probe.id: probe for probe in probes}
    selected: list[RegressionProbe] = []
    for probe_id in SUITE_PROBE_IDS:
        probe = by_id.get(probe_id)
        if probe is None:
            logger.warning("[eval] test-suite probe %s not in the registry; skipping", probe_id)
            continue
        selected.append(probe)
    return selected


def suite_items(probes: list[RegressionProbe]) -> list[dict[str, Any]]:
    """The Test Suite items for ``probes``: ``data`` keyed by probe id + per-probe ``assertions`` (§6).

    ``data`` carries ONLY the probe id — the task fn resolves the probe (and its prompt) from it, so the
    judge-visible ``input``/``output`` (shaped by :func:`make_suite_task_fn`) never carry an expected
    answer. Item-level assertions come from :data:`ITEM_ASSERTIONS`; the global bars ride on the suite.
    """
    items: list[dict[str, Any]] = []
    for probe in probes:
        item: dict[str, Any] = {"data": {"probe_id": probe.id}}
        assertions = ITEM_ASSERTIONS.get(probe.id)
        if assertions:
            item["assertions"] = list(assertions)
        items.append(item)
    return items


def make_suite_task_fn(
    probes_by_id: dict[str, RegressionProbe],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Adapt the regression task fn to the Test Suite ``{"input", "output"}`` contract (task 116; §6).

    Reuses :func:`evals.harness.regression.make_regression_task_fn` — the SAME run-and-grade path
    surface (a) uses — then reshapes its flat payload: ``input`` is JUST the prompt the agent received
    (never the expected answer — the docs warn a leaked expectation lets the judge cheat), and ``output``
    is the agent's final answer text the NL judge grades. Nothing else from the graded payload
    (``file_state``, error channels) crosses into the judge's view.
    """
    regression_task_fn = make_regression_task_fn(probes_by_id)

    def suite_task(item: dict[str, Any]) -> dict[str, Any]:
        probe = probes_by_id[item["probe_id"]]
        payload = regression_task_fn({"probe_id": probe.id})
        return {"input": {"prompt": probe.prompt}, "output": payload["output"]}

    return suite_task


def build_suite(client: opik.Opik, probes: list[RegressionProbe]) -> Any:
    """Create/reuse ``decode-regression-suite`` and (re)insert its probe items (task 116; ADR-0017 §6).

    Sets the cross-cutting :data:`GLOBAL_ASSERTIONS` and a one-run-per-item execution policy at the
    suite level; item-level assertions ride on each inserted item. ``project_name`` is
    ``settings.eval_project_name`` so a suite run logs under ``decode-evals`` and never pollutes live
    REPL tracing (ADR-0017 §9). Idempotent: ``get_or_create`` never duplicates the suite and re-insert
    refreshes the items.
    """
    suite = client.get_or_create_test_suite(
        name=TEST_SUITE_NAME,
        project_name=settings.eval_project_name,
        global_assertions=list(GLOBAL_ASSERTIONS),
        global_execution_policy={"runs_per_item": 1, "pass_threshold": 1},
    )
    items = suite_items(probes)
    if items:
        suite.insert(items)
    return suite


def run_test_suite(*, client: opik.Opik | None = None) -> Any:
    """Build + run the Test Suite over the judge-flavored subset, returning the run result (task 116).

    Guards on the 2.0 surface first (:class:`SuiteUnavailableError` with the version reason when
    absent), then builds the suite, adapts the regression task fn, and calls ``opik.run_tests``.

    The run is SERIAL by construction: each item drives the REAL agent host-native through the
    process-global ``bash`` executor seam, so two concurrent items would clash (the same reason surface
    (a) runs ``evaluate(task_threads=1)``). Should the 2.0 ``run_tests`` default to parallel items, its
    concurrency knob must be pinned to 1 here — flagged for the upgrade in the task log. Returns the run
    result carrying ``pass_rate``.
    """
    import opik

    client = client or opik.Opik()
    if not suite_api_available(client):
        raise SuiteUnavailableError(_unavailable_message())

    all_probes = load_probes()
    selected = select_suite_probes(all_probes)
    if not selected:
        raise SuiteSelectionError(
            f"no test-suite probe found among {list(SUITE_PROBE_IDS)}; the registry has "
            f"{len(all_probes)} probe(s)."
        )

    probes_by_id = {probe.id: probe for probe in all_probes}
    suite = build_suite(client, selected)
    task = make_suite_task_fn(probes_by_id)
    return opik.run_tests(test_suite=suite, task=task)


def assert_pass_rate(pass_rate: float, bar: float = SUITE_PASS_BAR) -> None:
    """Raise :class:`SuitePassRateError` when ``pass_rate`` is below ``bar`` — the suite gate (task 116).

    Split from the CLI so the gate logic is unit-testable without an Opik run: the CLI turns the raise
    into a non-zero exit.
    """
    if pass_rate < bar:
        raise SuitePassRateError(f"test-suite pass rate {pass_rate:.0%} is below the bar {bar:.0%}")
