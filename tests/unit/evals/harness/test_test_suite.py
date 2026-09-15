"""Offline tests for the Opik Test Suites regression surface (ADR-0022 §8; ADR-0017 §6).

No infra, no keys: ``opik.run_tests`` and the client's ``get_or_create_test_suite`` are mocked. The
tests cover the case selection (``--case`` / ``--difficulty``, skip-guarded cases excluded), the
content-versioned suite name a run registers (the same one ``sync --regression`` resolves), the
``{"input", "output"}`` adapter (and that it never
leaks an expected answer into ``input``), the serial run wiring, and the pass-rate gate.
"""

from __future__ import annotations

import pytest

from evals.harness.datasets import regression_suite_name
from evals.harness.test_suite import (
    SUITE_PASS_BAR,
    SuitePassRateError,
    SuiteSelectionError,
    assert_pass_rate,
    make_suite_task_fn,
    run_test_suite,
)
from evals.regression.case import RegressionCase


def _case(case_id: str, prompt: str = "do the thing", **overrides: object) -> RegressionCase:
    """A minimal case standing in for a real one — the suite surface reads id / prompt / tier."""
    fields: dict[str, object] = {
        "id": case_id,
        "prompt": prompt,
        "fixture": lambda _w: None,
        # the suite surface never scores with these; one is enough to construct
        "metrics": [object()],
        "difficulty": "easy",
        "description": "Tests that it behaves.",
        "symptom": "harness invariant: it behaves.",
        "assertion": "The response behaves.",
    }
    fields.update(overrides)
    return RegressionCase(**fields)  # type: ignore[arg-type]


# --- the {"input", "output"} adapter ----------------------------------------------------------------


def test_suite_task_fn_shapes_input_and_output_from_the_regression_payload(mocker):
    """The adapter reuses the regression task fn and returns the ``{input, output}`` Test Suite contract."""
    case = _case("17-grounded-answer", prompt="what is the Quibbler responsible for?")
    fake_regression = mocker.Mock(return_value={"output": "The Quibbler deduplicates webhooks."})
    mocker.patch("evals.harness.test_suite.make_regression_task_fn", return_value=fake_regression)

    task = make_suite_task_fn({case.id: case})
    result = task({"case_id": case.id})

    fake_regression.assert_called_once_with({"case_id": case.id})
    assert result["output"] == "The Quibbler deduplicates webhooks."
    assert result["input"] == {"prompt": case.prompt}


def test_suite_task_fn_input_never_leaks_an_expected_answer(mocker):
    """Even if the regression payload carries file_state / errors, ``input`` stays the prompt alone (§6).

    The docs warn a leaked expectation in ``input`` lets the NL judge cheat, so the adapter must forward
    ONLY the prompt the agent actually received — never the graded payload's internals.
    """
    case = _case("18-no-hallucinated-files", prompt="what does does_not_exist.py do?")
    fake_regression = mocker.Mock(
        return_value={
            "output": "That file does not exist in the project.",
            "file_state": {"README.md": "# Sample"},
            "agent_error": None,
        }
    )
    mocker.patch("evals.harness.test_suite.make_regression_task_fn", return_value=fake_regression)

    task = make_suite_task_fn({case.id: case})
    result = task({"case_id": case.id})

    assert result["input"] == {"prompt": case.prompt}
    assert "file_state" not in result["input"]
    assert set(result.keys()) == {"input", "output"}


# --- selection + run wiring -------------------------------------------------------------------------


def test_run_test_suite_registers_the_cases_and_runs_the_suite_serially(mocker):
    """The happy path: sync both surfaces → ``opik.run_tests`` with the adapter, one item at a time."""
    run_tests = mocker.patch("opik.run_tests", create=True)
    sync = mocker.patch("evals.harness.datasets.sync_regression_cases")
    mocker.patch("evals.harness.test_suite.load_cases", return_value=[_case("a"), _case("b")])
    client = mocker.Mock()

    run = run_test_suite(client=client)

    assert run.result is run_tests.return_value
    assert run.suite_name is sync.return_value.suite_name
    assert [case.id for case in sync.call_args.args[0]] == ["a", "b"]
    kwargs = run_tests.call_args.kwargs
    assert kwargs["test_suite"] is sync.return_value.suite
    assert callable(kwargs["task"])
    # The agent runs host-native through the process-global bash seam — two items at once would clash.
    assert kwargs["worker_threads"] == 1


def test_a_filtered_run_registers_its_own_sliced_suite(mocker):
    """``run_tests`` has no item filter, so a tier run gets its own suite instead of billing them all.

    The slice needs no suffix: the suite name hashes the SELECTED cases, so handing ``sync`` the one
    hard case is what gives the run its own suite.
    """
    mocker.patch("opik.run_tests", create=True)
    sync = mocker.patch("evals.harness.datasets.sync_regression_cases")
    mocker.patch(
        "evals.harness.test_suite.load_cases",
        return_value=[_case("a"), _case("b", difficulty="hard")],
    )

    run_test_suite(difficulty="hard", client=mocker.Mock())

    assert [case.id for case in sync.call_args.args[0]] == ["b"]
    assert "suite_name" not in sync.call_args.kwargs


def test_the_run_registers_the_versioned_suite_with_one_item_per_case(mocker):
    """End to end through the REAL sync: ONE function names the suite, and it holds one item per case.

    Only ``opik.run_tests`` is mocked; the fake client records what ``sync_regression_cases`` asked
    Opik for — the same call ``python -m evals sync --regression`` makes, so both commands provably
    agree on the name.
    """
    mocker.patch("opik.run_tests", create=True)
    cases = [_case("a"), _case("b"), _case("skipped", skip_reason="MCP has not shipped")]
    mocker.patch("evals.harness.test_suite.load_cases", return_value=cases)
    client = mocker.Mock()
    suite = client.get_or_create_test_suite.return_value

    run = run_test_suite(client=client)

    runnable = [case for case in cases if case.skip_reason is None]
    assert run.suite_name == regression_suite_name(runnable)
    assert client.get_or_create_test_suite.call_args.kwargs["name"] == regression_suite_name(
        runnable
    )
    inserted = suite.insert.call_args.args[0]
    assert [item["data"]["case_id"] for item in inserted] == ["a", "b"]


def test_a_skip_guarded_case_is_never_registered_or_run(mocker):
    """A declared-but-blocked case (MCP) stays in the registry and out of the judged suite."""
    mocker.patch("opik.run_tests", create=True)
    sync = mocker.patch("evals.harness.datasets.sync_regression_cases")
    mocker.patch(
        "evals.harness.test_suite.load_cases",
        return_value=[_case("a"), _case("skipped", skip_reason="MCP has not shipped")],
    )

    run_test_suite(client=mocker.Mock())

    assert [case.id for case in sync.call_args.args[0]] == ["a"]


def test_run_test_suite_raises_when_the_filter_matches_nothing(mocker):
    """An empty selection is a friendly stop, never a silent zero-item (vacuously green) suite run."""
    mocker.patch("opik.run_tests", create=True)
    mocker.patch("evals.harness.test_suite.load_cases", return_value=[_case("a")])

    with pytest.raises(SuiteSelectionError):
        run_test_suite(case_id="nope", client=mocker.Mock())


# --- the pass-rate gate -----------------------------------------------------------------------------


def test_assert_pass_rate_passes_at_and_above_the_bar():
    """A pass rate at or above the bar clears the gate (no raise)."""
    assert_pass_rate(SUITE_PASS_BAR)
    assert_pass_rate(1.0)


def test_assert_pass_rate_raises_below_the_bar():
    """A pass rate below the bar fails the gate loudly — the CLI turns this into a non-zero exit."""
    with pytest.raises(SuitePassRateError) as excinfo:
        assert_pass_rate(SUITE_PASS_BAR - 0.01)

    assert "below" in str(excinfo.value)
