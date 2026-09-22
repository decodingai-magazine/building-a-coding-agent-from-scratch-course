"""The operator script that records a seed batch of Sessions for an investigation to judge.

Hermetic: no ``decode run`` is spawned. The scenario table is the contract — thirty runs, the three
shapes decode's Opik history shows, over ten prompts — and the orchestration is driven with a fake
runner whose outcomes are what ``--summary-json`` would have said.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from scripts.seed_kitaru_sessions import (
    BOGUS_MODEL,
    CUTOFF_PROMPTS,
    GOOD_PROMPTS,
    Outcome,
    Scenario,
    crash_env,
    main,
    render_command,
    render_row,
    render_tally,
    run_argv,
    scenarios,
)

# --- the scenario table ------------------------------------------------------------------------


def test_thirty_scenarios_in_three_shapes_over_ten_prompts() -> None:
    rows = scenarios("modal")

    assert len(rows) == 30
    assert [r.number for r in rows] == list(range(1, 31))
    assert {r.kind for r in rows} == {"good", "cutoff", "crash"}
    assert sum(r.kind == "good" for r in rows) == 14
    assert sum(r.kind == "cutoff" for r in rows) == 8
    assert sum(r.kind == "crash" for r in rows) == 8
    assert len({r.prompt for r in rows}) == len(GOOD_PROMPTS) + len(CUTOFF_PROMPTS) == 10


def test_a_cutoff_run_is_a_multi_step_task_capped_at_one_request() -> None:
    cutoffs = [r for r in scenarios("modal") if r.kind == "cutoff"]

    assert all(r.extra_argv == ("--max-requests", "1") for r in cutoffs)
    assert all(r.prompt in CUTOFF_PROMPTS for r in cutoffs)


def test_crash_runs_split_between_a_bogus_model_and_a_broken_provider() -> None:
    crashes = [r for r in scenarios("modal") if r.kind == "crash"]

    bogus = [r for r in crashes if r.extra_argv == ("--model", BOGUS_MODEL)]
    broken = [r for r in crashes if r.env]
    assert len(bogus) == 4 and len(broken) == 4
    assert all(r.env == {"MODAL_ENDPOINT_URL": "http://127.0.0.1:9"} for r in broken)
    assert all(not r.extra_argv for r in broken)


@pytest.mark.parametrize(
    ("provider", "key"),
    [
        ("modal", "MODAL_ENDPOINT_URL"),
        ("gemini", "GEMINI_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
    ],
)
def test_the_crash_override_targets_the_active_provider_only(provider: str, key: str) -> None:
    assert list(crash_env(provider)) == [key]


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        crash_env("bedrock")


# --- the command ---------------------------------------------------------------------------------


def test_the_argv_is_a_headless_run_with_a_summary_on_this_interpreter(tmp_path: Path) -> None:
    scenario = Scenario(3, "cutoff", "list every python file", extra_argv=("--max-requests", "1"))

    argv = run_argv(scenario, tmp_path / "s.json")

    assert argv[1:4] == ["-m", "decode", "run"]
    assert argv[4:6] == ["--max-requests", "1"]
    assert argv[6:8] == ["--summary-json", str(tmp_path / "s.json")]
    assert argv[-1] == "list every python file"


def test_the_printed_command_carries_the_env_override_and_quotes_the_prompt() -> None:
    scenario = Scenario(29, "crash", "say hi in exactly three words", env={"GEMINI_API_KEY": "x y"})

    assert render_command(scenario) == (
        "GEMINI_API_KEY='x y' uv run decode run 'say hi in exactly three words'"
    )


# --- the report ----------------------------------------------------------------------------------


def _outcome(number: int, kind: str, **overrides: object) -> Outcome:
    values: dict[str, object] = {
        "returncode": 0,
        "session_id": f"s-{number}",
        "recorded": True,
        "exit_reason": "completed",
        "error": None,
    }
    values.update(overrides)
    return Outcome(scenario=Scenario(number, kind, "p"), **values)  # type: ignore[arg-type]


def test_a_row_names_the_decode_session_and_flags_one_the_seam_could_not_record() -> None:
    assert render_row(_outcome(1, "good")) == " 1  good    completed      s-1"
    assert render_row(_outcome(2, "crash", recorded=False, exit_reason="error", error="E")) == (
        " 2  crash   error          s-2  NOT RECORDED  E"
    )


def test_the_tally_counts_kinds_and_recorded_sessions() -> None:
    outcomes = [
        _outcome(1, "good"),
        _outcome(2, "cutoff"),
        _outcome(3, "crash", recorded=False),
    ]

    assert render_tally(outcomes) == (
        "3 run(s) — good: 1, cutoff: 1, crash: 1 — 2 recorded as Kitaru Sessions"
    )


# --- the CLI -------------------------------------------------------------------------------------


def test_dry_run_prints_every_selected_command_and_spawns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.seed_kitaru_sessions._provider", lambda: "gemini")
    monkeypatch.setattr(
        "scripts.seed_kitaru_sessions.run_scenario",
        lambda s: pytest.fail("dry-run must not run anything"),
    )

    result = CliRunner().invoke(main, ["--dry-run", "--only", "crash", "--limit", "5"])

    assert result.exit_code == 0, result.output
    commands = [line for line in result.output.splitlines() if not line.startswith("#")]
    assert len(commands) == 5
    assert commands[0] == f"uv run decode run --model {BOGUS_MODEL} 'say hi in exactly three words'"
    assert commands[4].startswith("GEMINI_API_KEY=invalid-seed-key uv run decode run ")


def test_it_refuses_to_burn_model_calls_when_recording_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.seed_kitaru_sessions._provider", lambda: "gemini")
    monkeypatch.setattr("scripts.seed_kitaru_sessions._recording_is_configured", lambda: False)
    monkeypatch.setattr(
        "scripts.seed_kitaru_sessions.run_scenario",
        lambda s: pytest.fail("must not run when recording is off"),
    )

    result = CliRunner().invoke(main, ["--limit", "1"])

    assert result.exit_code == 1
    assert "KITARU_API_URL + KITARU_AGENT_ID" in result.output


def test_it_runs_the_selection_and_reports_one_row_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.seed_kitaru_sessions._provider", lambda: "modal")
    monkeypatch.setattr("scripts.seed_kitaru_sessions._recording_is_configured", lambda: True)
    monkeypatch.setattr(
        "scripts.seed_kitaru_sessions.run_scenario",
        lambda s: _outcome(s.number, s.kind),
    )

    result = CliRunner().invoke(main, ["--only", "good", "--limit", "2", "--jobs", "1"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].startswith("seed: 2 run(s), 1 at a time;")
    assert lines[1:3] == [" 1  good    completed      s-1", " 2  good    completed      s-2"]
    assert lines[3] == "2 run(s) — good: 2, cutoff: 0, crash: 0 — 2 recorded as Kitaru Sessions"


def test_a_batch_that_recorded_nothing_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.seed_kitaru_sessions._provider", lambda: "modal")
    monkeypatch.setattr("scripts.seed_kitaru_sessions._recording_is_configured", lambda: True)
    monkeypatch.setattr(
        "scripts.seed_kitaru_sessions.run_scenario",
        lambda s: _outcome(s.number, s.kind, recorded=False),
    )

    result = CliRunner().invoke(main, ["--limit", "1"])

    assert result.exit_code == 1
    assert "nothing was recorded" in result.output
