"""Freeze a benchmark job's FAILING trials into a Kitaru Cohort (ADR-0022 §11).

The other half of the Opik→Kitaru join: ``evals kitaru import`` backfills sessions decode never
recorded; this module takes an Opik Experiment decode DID record (``make eval-benchmark`` with
``KITARU_AGENT_ID`` set) and turns "which trials failed?" into "replay exactly those Sessions".

The join is the **Session Name**. The recording adapter names a recorded Session after the decode
session id (``KitaruAgent(session_name=…)`` → ``Session.name``), and every Benchmark Trial reports
its own ``session_id`` in the ``--summary-json`` the runner reads, which rides onto the experiment
item's output. So: item → ``session_id`` → the Session whose ``name`` is that id.
``kitaru_session_id`` (the Kitaru Session UUID, in the same summary) short-circuits the lookup when
the run recorded one.

Two refusals, both one line and non-zero, because a silently empty or wrong cohort is worse than no
cohort (ADR-0022 §11):

* the experiment's ``experiment_config.kitaru_agent_id`` is ``None`` — those trials were never
  recorded, so there is nothing to freeze;
* no failing trial resolves to a Session on this server — usually the wrong server, or an experiment
  recorded against another workspace.

A cohort VERSION is immutable, so re-running is not "idempotent" in the register sense: this module
reads the latest version's membership and adds only what is new, and says so when there is nothing
to add (kitaru refuses a delta that re-adds a member with a 422).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from evals.harness import kitaru_cli
from evals.harness.kitaru_cli import KitaruCommandError

logger = logging.getLogger(__name__)

# The cohort a benchmark job's failures land in by default — one name, versioned per job.
DEFAULT_COHORT = "decode-benchmark-failures"

# The agent recorded sessions (and therefore the cohort) belong to.
DEFAULT_AGENT = "decode"

# The experiment_config key the benchmark writes the recording agent into (task 161).
AGENT_ID_KEY = "kitaru_agent_id"

# The score of record on a benchmark experiment item (``evals.harness.aggregates``).
REWARD_SCORE = "reward"

# A trial the harness never got to run: excluded from the numerator AND the denominator (ADR-0022 §4).
INFRA_ERROR_STATUS = "infra_error"


class CohortError(Exception):
    """The cohort cannot be built — surfaced as ONE refusal line."""


@dataclass(frozen=True, slots=True)
class FailedTrial:
    """One benchmark trial that scored zero, reduced to what the join needs."""

    task_id: str | None
    session_id: str | None
    kitaru_session_id: str | None
    trial_dir: str | None = None


@dataclass(frozen=True, slots=True)
class CohortOutcome:
    """What the command printed: the frozen version, its size, and what did not resolve."""

    cohort: str
    version_ref: str | None
    session_count: int
    added: int
    unresolved: list[FailedTrial]
    created_cohort: bool = False


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def item_input(item: Any) -> dict[str, Any]:
    """The dataset item behind one trial — where the benchmark task id lives."""
    if isinstance(item, dict):
        return _mapping(item.get("dataset_item_data") or item.get("input"))
    return _mapping(getattr(item, "dataset_item_data", None))


def item_output(item: Any) -> dict[str, Any]:
    """The trial payload on an experiment item, whatever shape the SDK handed back."""
    if isinstance(item, dict):
        return _mapping(item.get("evaluation_task_output") or item.get("output"))
    return _mapping(getattr(item, "evaluation_task_output", None))


def item_scores(item: Any) -> list[dict[str, Any]]:
    """The item's feedback scores as plain dicts."""
    scores = item.get("feedback_scores") if isinstance(item, dict) else None
    if scores is None:
        scores = getattr(item, "feedback_scores", None)
    return [_mapping(score) for score in scores or []]


def item_reward(item: Any) -> float | None:
    """The trial's reward, or ``None`` when it was never scored.

    The ``reward`` FEEDBACK SCORE is the grade of record; a trial whose score was ``scoring_failed``
    carries none at all (Opik drops it), which is exactly the "neither pass nor fail" the benchmark
    intends — so a missing score means skip, never zero. The payload's own ``reward`` is the fallback
    for an experiment read back before the score landed.
    """
    for score in item_scores(item):
        if score.get("name") == REWARD_SCORE and isinstance(score.get("value"), (int, float)):
            return float(score["value"])
    value = item_output(item).get("reward")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def failed_trials(items: list[Any]) -> list[FailedTrial]:
    """Every trial that scored a hard zero — infra errors and unscored trials excluded."""
    failures: list[FailedTrial] = []
    for item in items:
        output = item_output(item)
        if output.get("status") == INFRA_ERROR_STATUS:
            continue
        reward = item_reward(item)
        if reward is None or reward != 0:
            continue
        failures.append(
            FailedTrial(
                task_id=_str_or_none(item_input(item).get("task_id")),
                session_id=_str_or_none(output.get("session_id")),
                kitaru_session_id=_str_or_none(output.get("kitaru_session_id")),
                trial_dir=_str_or_none(output.get("trial_dir")),
            )
        )
    return failures


def _str_or_none(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def experiment_agent_id(config: dict[str, Any] | None) -> str:
    """The experiment's recording agent, or the refusal that says why there is nothing to freeze."""
    agent_id = _mapping(config).get(AGENT_ID_KEY)
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise CohortError(
            "this experiment recorded no Kitaru Sessions (experiment_config.kitaru_agent_id is "
            "None) — export KITARU_AGENT_ID (and KITARU_API_URL) and re-run the benchmark."
        )
    return agent_id.strip()


def resolve_sessions(
    trials: list[FailedTrial], sessions: list[dict[str, Any]]
) -> tuple[list[str], list[FailedTrial]]:
    """(session ids, trials that resolved to nothing) — by Kitaru id first, then Session Name."""
    by_name = {
        str(session.get("name")): str(session.get("id"))
        for session in sessions
        if session.get("name")
    }
    known_ids = {str(session.get("id")) for session in sessions}

    resolved: list[str] = []
    unresolved: list[FailedTrial] = []
    for trial in trials:
        if trial.kitaru_session_id and trial.kitaru_session_id in known_ids:
            session_id: str | None = trial.kitaru_session_id
        else:
            session_id = by_name.get(trial.session_id or "")
        if session_id is None:
            unresolved.append(trial)
        elif session_id not in resolved:
            resolved.append(session_id)
    return resolved, unresolved


def cohort_version_args(cohort: str, session_ids: list[str]) -> list[str]:
    """The ``kitaru cohort version create`` sub-command argv — one ``--add-session`` per id."""
    args = ["cohort", "version", "create", cohort]
    for session_id in session_ids:
        args += ["--add-session", session_id]
    return args


def cohort_members(cohort: str, version: int, *, server: str | None = None) -> set[str]:
    """The session ids already in ``cohort@version`` — the delta a new version must not re-add."""
    if version < 1:
        return set()
    sessions = kitaru_cli.list_sessions(extra=["--cohort", f"{cohort}@{version}"], server=server)
    return {str(session.get("id")) for session in sessions}


def ensure_cohort(cohort: str, *, agent: str, server: str | None = None) -> tuple[int, bool]:
    """(latest version, whether it was created now) — creates the cohort namespace when absent."""
    try:
        item = _mapping(kitaru_cli.run_kitaru(["cohort", "get", cohort], server=server).get("item"))
    except KitaruCommandError as exc:
        if exc.kind != "not_found":
            raise
        kitaru_cli.run_kitaru(["cohort", "create", cohort, "--agent", agent], server=server)
        return 0, True
    latest = item.get("latest_version")
    return (latest if isinstance(latest, int) else 0), False


def build_cohort(
    items: list[Any],
    config: dict[str, Any] | None,
    *,
    cohort: str = DEFAULT_COHORT,
    agent: str = DEFAULT_AGENT,
    server: str | None = None,
) -> CohortOutcome:
    """Freeze this experiment's failing trials into the next version of ``cohort``."""
    # Called for the REFUSAL only, and its id deliberately discarded: the session lookup goes by
    # agent NAME, so an experiment recorded against another server (a different agent UUID for the
    # same `decode`) still resolves — which is the whole point of "a server is just a URL".
    experiment_agent_id(config)

    trials = failed_trials(items)
    if not trials:
        raise CohortError("no trial in this experiment scored 0 — there is nothing to freeze.")

    sessions = kitaru_cli.list_sessions(agent=agent, server=server)
    resolved, unresolved = resolve_sessions(trials, sessions)
    if not resolved:
        raise CohortError(
            f"none of the {len(trials)} failing trial(s) matched a Session on this server — check "
            "that KITARU_API_URL names the server the benchmark recorded to."
        )

    latest, created = ensure_cohort(cohort, agent=agent, server=server)
    members = cohort_members(cohort, latest, server=server)
    additions = [session_id for session_id in resolved if session_id not in members]
    if not additions:
        return CohortOutcome(
            cohort=cohort,
            version_ref=f"{cohort}@{latest}",
            session_count=len(members),
            added=0,
            unresolved=unresolved,
            created_cohort=created,
        )

    item = _mapping(
        kitaru_cli.run_kitaru(cohort_version_args(cohort, additions), server=server).get("item")
    )
    version = item.get("version")
    count = item.get("session_count")
    return CohortOutcome(
        cohort=cohort,
        version_ref=f"{cohort}@{version}" if version else None,
        session_count=count if isinstance(count, int) else len(additions),
        added=len(additions),
        unresolved=unresolved,
        created_cohort=created,
    )


def open_experiment(name: str) -> tuple[list[Any], dict[str, Any] | None]:
    """(items, experiment_config) for the NEWEST experiment of this name — ``opik`` imported lazily.

    Opik keeps every experiment that ever ran under a name; the newest one is the job just run,
    which is what an operator means by ``from-experiment <job>``.
    """
    import opik

    from decode.config.settings import settings

    client = opik.Opik(project_name=settings.eval_project_name)
    # `get_experiments_by_name` is a case-insensitive SUBSTRING search that RAISES on no hit (it
    # never returns an empty list — verified live), so both shapes collapse into the same refusal;
    # and an exact match is picked here, or `bench-2026` would freeze the failures of
    # `bench-20260911-101500`.
    try:
        found = client.get_experiments_by_name(name)
    except opik.exceptions.ExperimentNotFound as exc:
        raise CohortError(
            f"no Opik experiment named {name!r} in {settings.eval_project_name} — check the job "
            "name `evals benchmark` printed."
        ) from exc
    matches = [experiment for experiment in found if experiment.name.lower() == name.lower()]
    if not matches:
        raise CohortError(
            f"no Opik experiment named {name!r} in {settings.eval_project_name} — check the job "
            "name `evals benchmark` printed."
        )
    experiment = max(matches, key=lambda item: str(item.get_experiment_data().created_at or ""))
    config = experiment.get_experiment_data().metadata
    return list(experiment.get_items()), config if isinstance(config, dict) else None
