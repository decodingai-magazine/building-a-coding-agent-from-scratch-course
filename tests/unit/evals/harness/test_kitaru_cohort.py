"""``evals kitaru cohort from-experiment`` — failing trials → a frozen Cohort (task 165).

The Opik experiment is a fake whose items carry the real ``trial_payload`` shape, and the ``kitaru``
CLI is faked at the ONE seam (:func:`evals.harness.kitaru_cli.run_kitaru`), so every assertion is
either a pure rule over plain dicts or the exact argv an operator could re-type.
"""

from __future__ import annotations

from typing import Any

import pytest

from evals.harness import kitaru_cli, kitaru_cohort
from evals.harness.kitaru_cohort import (
    DEFAULT_COHORT,
    CohortError,
    FailedTrial,
    build_cohort,
    cohort_version_args,
    experiment_agent_id,
    failed_trials,
    item_reward,
    resolve_sessions,
)

AGENT_ID = "01a08f8d-7587-73a0-b782-80f943d09c70"
CONFIG = {"kitaru_agent_id": AGENT_ID, "sandbox": "docker"}
SERVER = "http://localhost:8000"


class Item:
    """An ``opik`` ``ExperimentItemContent`` as far as this module cares."""

    def __init__(
        self,
        *,
        task_id: str = "001-find-and-replace",
        reward: float | None = 0.0,
        status: str = "agent_fail",
        session_id: str | None = "sess-1",
        kitaru_session_id: str | None = None,
        scores: list[dict[str, Any]] | None = None,
    ) -> None:
        self.dataset_item_data = {"task_id": task_id}
        self.evaluation_task_output = {
            "status": status,
            "reward": reward,
            "session_id": session_id,
            "kitaru_session_id": kitaru_session_id,
            "trial_dir": "/tmp/job/001__abcd",
        }
        self.feedback_scores = scores if scores is not None else self._default_scores(reward)

    @staticmethod
    def _default_scores(reward: float | None) -> list[dict[str, Any]]:
        # Opik drops a `scoring_failed` score entirely — a `None` reward leaves NO score behind.
        return [] if reward is None else [{"name": "reward", "value": reward}]


def session(session_id: str, name: str | None = None) -> dict[str, Any]:
    return {"id": session_id, "name": name}


def install_kitaru(mocker, envelopes: list[Any]) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> dict[str, Any]:
        calls.append(list(args))
        answer = envelopes.pop(0) if envelopes else {"ok": True, "items": [], "page": {}}
        if isinstance(answer, Exception):
            raise answer
        return answer

    mocker.patch.object(kitaru_cli, "run_kitaru", fake_run)
    return calls


SESSION_PAGE = {
    "ok": True,
    "items": [{"id": "kit-1", "name": "sess-1"}, {"id": "kit-2", "name": "sess-2"}],
    "page": {},
}
EMPTY_PAGE: dict[str, Any] = {"ok": True, "items": [], "page": {}}
COHORT_MISSING = kitaru_cli.KitaruCommandError("Cohort not found.", kind="not_found")
VERSION_CREATED = {"ok": True, "item": {"version": 1, "session_count": 1}}


# --- which trials count as failures ----------------------------------------------------------------


def test_a_zero_reward_trial_is_a_failure():
    assert len(failed_trials([Item(reward=0.0)])) == 1


def test_a_passing_trial_is_not():
    assert failed_trials([Item(reward=1.0)]) == []


def test_a_trial_whose_score_failed_is_neither_pass_nor_fail():
    """`scoring_failed` leaves no score at all — a missing reward must never read as a zero."""
    assert failed_trials([Item(reward=None, status="agent_fail")]) == []


def test_an_infra_error_trial_is_excluded_even_with_a_zero_payload():
    assert failed_trials([Item(reward=0.0, status="infra_error")]) == []


def test_the_feedback_score_wins_over_a_stale_payload_reward():
    item = Item(reward=1.0, scores=[{"name": "reward", "value": 0.0}])

    assert item_reward(item) == 0.0
    assert len(failed_trials([item])) == 1


def test_a_failure_carries_its_task_and_session_ids():
    trial = failed_trials([Item(session_id="sess-9", kitaru_session_id="kit-9")])[0]

    assert trial.task_id == "001-find-and-replace"
    assert trial.session_id == "sess-9"
    assert trial.kitaru_session_id == "kit-9"


def test_a_plain_dict_item_reads_the_same_way():
    item = {
        "dataset_item_data": {"task_id": "018-git-bisect-revert"},
        "evaluation_task_output": {"reward": 0.0, "status": "agent_fail", "session_id": "s"},
        "feedback_scores": [{"name": "reward", "value": 0.0}],
    }

    assert failed_trials([item])[0].task_id == "018-git-bisect-revert"


# --- the refusals ----------------------------------------------------------------------------------


def test_an_experiment_that_recorded_nothing_is_refused_by_name():
    with pytest.raises(CohortError) as error:
        experiment_agent_id({"kitaru_agent_id": None})

    assert "kitaru_agent_id" in str(error.value)


def test_a_missing_experiment_config_is_refused_too():
    with pytest.raises(CohortError):
        experiment_agent_id(None)


def test_a_recorded_experiment_hands_back_its_agent():
    assert experiment_agent_id(CONFIG) == AGENT_ID


def test_no_failing_trial_is_a_refusal_not_an_empty_cohort(mocker):
    install_kitaru(mocker, [])

    with pytest.raises(CohortError) as error:
        build_cohort([Item(reward=1.0)], CONFIG, server=SERVER)

    assert "nothing to freeze" in str(error.value)


def test_failures_that_resolve_to_no_session_are_refused_with_the_server_named(mocker):
    install_kitaru(mocker, [EMPTY_PAGE])

    with pytest.raises(CohortError) as error:
        build_cohort([Item()], CONFIG, server=SERVER)

    assert "KITARU_API_URL" in str(error.value)


# --- the join --------------------------------------------------------------------------------------


def test_a_trial_resolves_by_the_session_name_the_adapter_recorded():
    trials = [FailedTrial(task_id="001", session_id="sess-1", kitaru_session_id=None)]

    resolved, unresolved = resolve_sessions(trials, [session("kit-1", "sess-1")])

    assert resolved == ["kit-1"]
    assert unresolved == []


def test_a_known_kitaru_session_id_short_circuits_the_name_lookup():
    trials = [FailedTrial(task_id="001", session_id="sess-1", kitaru_session_id="kit-7")]

    resolved, _ = resolve_sessions(trials, [session("kit-7", "other-name")])

    assert resolved == ["kit-7"]


def test_a_kitaru_id_this_server_does_not_have_falls_back_to_the_name():
    trials = [FailedTrial(task_id="001", session_id="sess-1", kitaru_session_id="kit-elsewhere")]

    resolved, unresolved = resolve_sessions(trials, [session("kit-1", "sess-1")])

    assert resolved == ["kit-1"] and unresolved == []


def test_an_unrecorded_trial_is_reported_never_silently_dropped():
    trials = [FailedTrial(task_id="001", session_id=None, kitaru_session_id=None)]

    resolved, unresolved = resolve_sessions(trials, [session("kit-1", "sess-1")])

    assert resolved == [] and unresolved == trials


def test_two_trials_of_the_same_session_are_frozen_once():
    trials = [
        FailedTrial(task_id="001", session_id="sess-1", kitaru_session_id=None),
        FailedTrial(task_id="001", session_id="sess-1", kitaru_session_id=None),
    ]

    resolved, _ = resolve_sessions(trials, [session("kit-1", "sess-1")])

    assert resolved == ["kit-1"]


# --- the kitaru argv -------------------------------------------------------------------------------


def test_each_session_rides_its_own_add_session_flag():
    """kitaru 0.26 binds ONE token per `--add-session`; two ids after one flag is a parse error."""
    args = cohort_version_args("decode-benchmark-failures", ["a", "b"])

    assert args[:4] == ["cohort", "version", "create", "decode-benchmark-failures"]
    assert args[4:] == ["--add-session", "a", "--add-session", "b"]


# --- the orchestration -----------------------------------------------------------------------------


def test_an_absent_cohort_is_created_then_versioned(mocker):
    calls = install_kitaru(
        mocker,
        [SESSION_PAGE, COHORT_MISSING, {"ok": True, "item": {}}, VERSION_CREATED],
    )

    outcome = build_cohort([Item(session_id="sess-1")], CONFIG, server=SERVER)

    assert outcome.created_cohort is True
    assert outcome.version_ref == f"{DEFAULT_COHORT}@1"
    assert outcome.added == 1
    assert [c[:3] for c in calls] == [
        ["session", "list", "--size"],
        ["cohort", "get", DEFAULT_COHORT],
        ["cohort", "create", DEFAULT_COHORT],
        ["cohort", "version", "create"],
    ]


def test_an_existing_cohort_only_gains_the_sessions_it_lacks(mocker):
    calls = install_kitaru(
        mocker,
        [
            SESSION_PAGE,
            {"ok": True, "item": {"latest_version": 1}},
            {"ok": True, "items": [{"id": "kit-1"}], "page": {}},  # already a member
            {"ok": True, "item": {"version": 2, "session_count": 2}},
        ],
    )
    items = [Item(session_id="sess-1"), Item(session_id="sess-2")]

    outcome = build_cohort(items, CONFIG, server=SERVER)

    assert outcome.added == 1
    assert outcome.version_ref == f"{DEFAULT_COHORT}@2"
    assert calls[-1][-1] == "kit-2"


def test_a_rerun_that_adds_nothing_reports_the_current_version_instead_of_a_422(mocker):
    """A cohort version is immutable and kitaru 422s a delta that re-adds a member."""
    calls = install_kitaru(
        mocker,
        [
            SESSION_PAGE,
            {"ok": True, "item": {"latest_version": 3}},
            {"ok": True, "items": [{"id": "kit-1"}], "page": {}},
        ],
    )

    outcome = build_cohort([Item(session_id="sess-1")], CONFIG, server=SERVER)

    assert outcome.added == 0
    assert outcome.version_ref == f"{DEFAULT_COHORT}@3"
    assert ["cohort", "version", "create"] not in [c[:3] for c in calls]


def test_the_cohort_name_is_overridable(mocker):
    calls = install_kitaru(mocker, [SESSION_PAGE, COHORT_MISSING, {"ok": True}, VERSION_CREATED])

    outcome = build_cohort([Item()], CONFIG, cohort="my-cohort", server=SERVER)

    assert outcome.cohort == "my-cohort"
    assert calls[1][:3] == ["cohort", "get", "my-cohort"]


def test_a_server_error_that_is_not_not_found_is_never_swallowed(mocker):
    install_kitaru(
        mocker,
        [SESSION_PAGE, kitaru_cli.KitaruCommandError("server is down", kind="unavailable")],
    )

    with pytest.raises(kitaru_cli.KitaruCommandError):
        build_cohort([Item()], CONFIG, server=SERVER)


def test_the_sessions_are_read_for_the_agent_the_cohort_belongs_to(mocker):
    calls = install_kitaru(mocker, [SESSION_PAGE, COHORT_MISSING, {"ok": True}, VERSION_CREATED])

    build_cohort([Item()], CONFIG, agent="decode", server=SERVER)

    assert calls[0][calls[0].index("--agent") + 1] == "decode"


def test_unresolved_trials_ride_back_on_the_outcome(mocker):
    install_kitaru(mocker, [SESSION_PAGE, COHORT_MISSING, {"ok": True}, VERSION_CREATED])
    items = [Item(session_id="sess-1"), Item(session_id="never-recorded")]

    outcome = build_cohort(items, CONFIG, server=SERVER)

    assert [t.session_id for t in outcome.unresolved] == ["never-recorded"]


def test_the_module_reads_no_opik_at_import_time():
    """`--help` must stay opik-free (ADR-0017 §1): the SDK is imported inside `open_experiment`."""
    import inspect

    source = inspect.getsource(kitaru_cohort)
    assert "\nimport opik" not in source


# --- the Opik lookup -------------------------------------------------------------------------------


class FakeExperiment:
    def __init__(
        self, name: str, *, metadata: dict[str, Any] | None = None, created: str = "1"
    ) -> None:
        self.name = name
        self._metadata = metadata
        self._created = created

    def get_experiment_data(self) -> Any:
        return type("Data", (), {"metadata": self._metadata, "created_at": self._created})()

    def get_items(self) -> list[Any]:
        return [Item()]


def install_opik(mocker, experiments: list[Any] | Exception) -> None:
    import opik

    client = mocker.Mock()
    if isinstance(experiments, Exception):
        client.get_experiments_by_name.side_effect = experiments
    else:
        client.get_experiments_by_name.return_value = experiments
    mocker.patch.object(opik, "Opik", return_value=client)


def test_an_unknown_job_name_is_one_refusal_not_an_opik_traceback(mocker):
    """Regression (live): `get_experiments_by_name` RAISES `ExperimentNotFound`, never returns []."""
    from opik.exceptions import ExperimentNotFound

    install_opik(mocker, ExperimentNotFound("No experiment(s) found with the name 'nope'."))

    with pytest.raises(CohortError) as error:
        kitaru_cohort.open_experiment("nope")

    assert "nope" in str(error.value)


def test_a_substring_match_is_not_the_experiment_asked_for(mocker):
    """`get_experiments_by_name` is a case-insensitive SUBSTRING search (opik SDK docstring)."""
    install_opik(mocker, [FakeExperiment("bench-20260911-101500")])

    with pytest.raises(CohortError):
        kitaru_cohort.open_experiment("bench-2026")


def test_the_newest_experiment_of_that_exact_name_wins(mocker):
    install_opik(
        mocker,
        [
            FakeExperiment("bench-x", metadata={"kitaru_agent_id": "old"}, created="2026-09-01"),
            FakeExperiment("bench-x", metadata={"kitaru_agent_id": "new"}, created="2026-09-11"),
        ],
    )

    items, config = kitaru_cohort.open_experiment("bench-x")

    assert config == {"kitaru_agent_id": "new"}
    assert len(items) == 1
