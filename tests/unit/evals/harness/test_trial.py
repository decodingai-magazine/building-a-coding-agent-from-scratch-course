"""``run_trial``: the five-phase Benchmark Trial, against a fake ``decode`` (ADR-0022 §1,§3,§4,§7).

One Trial = seed → ``decode run`` in a subprocess → collect its summary → grade a PRISTINE clone of
the handed-back branch host-side → write the Trial Dir. Every test here drives the real
:func:`~evals.harness.trial.run_trial` with the only faked part being the ``decode_command``
(``tests/support/fake_decode.py``): no model, no key, no docker daemon, no network.

What the file pins:

* the taxonomy — ``agent_ok`` iff reward 1, ``agent_fail`` for a graded-but-losing run (nop, request
  cap, timeout), ``infra_error`` for a missing summary / a broken clone / an unreadable reward;
* the isolation — the agent never sees ``tests/`` or ``solution/``, and the Verifier runs host-side
  (``trial.py`` imports nothing from ``decode.sandbox`` / ``decode.tools``);
* the evidence — a complete Trial Dir with ``result.json``, written in a ``finally`` even when the
  trial fails as infra;
* the child env — the parent's ``Settings`` exported for the subprocess, with the trial's overrides
  and removals, and no secret anywhere in ``result.json``.
"""

from __future__ import annotations

import ast
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from support.benchmark_tasks import CANARY_LINE, write_task_dir
from support.fake_decode import FAKE_BRANCH, write_fake_decode

from decode.config.settings import Settings
from evals.harness import trial as trial_module
from evals.harness.task_loader import BenchmarkTask, load_benchmark_task
from evals.harness.trial import TrialResult, child_env, run_trial

TRIAL_ID = "0a1b2c3d"


def _task(tmp_path: Path, **overrides: Any) -> BenchmarkTask:
    """A synthetic task: "write ``42`` into ``answer.txt``", graded by a three-line Verifier."""
    return load_benchmark_task(write_task_dir(tmp_path / "001-synthetic", **overrides))


def _script(body: str) -> str:
    return f"#!/usr/bin/env bash\n# {CANARY_LINE}\nset -uo pipefail\n{body}\n"


def _fast(**agent: Any) -> dict[str, dict[str, Any]]:
    """Task tables with a SHORT agent budget, so a timeout test costs a second, not ten minutes."""
    tables: dict[str, dict[str, Any]] = {
        "task": {
            "name": "001-synthetic",
            "version": 1,
            "description": "A synthetic task folder authored by the test suite.",
        },
        "metadata": {"difficulty": "easy", "category": "Software", "tags": ["fixture"]},
        "agent": {"timeout_sec": 600.0, "max_steps": 15, **agent},
        "verifier": {"timeout_sec": 120.0},
    }
    return tables


def _run(
    task: BenchmarkTask, tmp_path: Path, mode: str, *, model: str | None = None
) -> TrialResult:
    """Run one trial of ``task`` against the fake ``decode`` in ``mode``."""
    return run_trial(
        task,
        sandbox="docker",
        job_dir=tmp_path / "runs" / "job",
        trial_id=TRIAL_ID,
        model=model,
        decode_command=write_fake_decode(tmp_path, mode),
    )


def _result_json(result: TrialResult) -> dict[str, Any]:
    return json.loads((result.trial_dir / "result.json").read_text(encoding="utf-8"))


def _agent_stdout(result: TrialResult, *, line: int = 0) -> dict[str, Any]:
    """A json line the fake ``decode`` printed: line 0 is its argv, cwd and Seed Repo listing."""
    text = (result.trial_dir / "agent" / "stdout.txt").read_text(encoding="utf-8")
    return json.loads(text.splitlines()[line])


# --------------------------------------------------------------------------- the winning trial


def test_an_oracle_agent_earns_reward_one_and_a_complete_trial_dir(tmp_path: Path) -> None:
    """The full happy path: the pushed Session Branch is cloned pristine and grades 1.0."""
    task = _task(tmp_path)

    result = _run(task, tmp_path, "oracle")

    assert (result.status, result.reward, result.timed_out) == ("agent_ok", 1.0, False)
    assert result.reason is None
    assert result.branch == FAKE_BRANCH
    assert result.base_sha and len(result.base_sha) == 40
    trial_dir = result.trial_dir
    assert trial_dir == tmp_path / "runs" / "job" / f"{task.id}__{TRIAL_ID}"
    for relative in (
        "seed/.git",
        "home",
        "pristine/answer.txt",
        "pristine/tests/test.sh",
        "agent/stdout.txt",
        "agent/stderr.txt",
        "agent/summary.json",
        "verifier/test-stdout.txt",
        "verifier/test-stderr.txt",
        "verifier/reward.txt",
        "result.json",
    ):
        assert (trial_dir / relative).exists(), relative


def test_result_json_records_the_trial(tmp_path: Path) -> None:
    """``result.json`` carries the taxonomy, the provenance, the summary and the phase timings."""
    task = _task(tmp_path)

    result = _run(task, tmp_path, "oracle", model="gemini-3.5-pro")

    payload = _result_json(result)
    assert payload["task_id"] == task.id
    assert payload["trial_id"] == TRIAL_ID
    assert payload["status"] == "agent_ok"
    assert payload["reward"] == 1.0
    assert payload["timed_out"] is False
    assert payload["branch"] == FAKE_BRANCH
    assert payload["base_sha"] == result.base_sha
    assert payload["agent"]["model"] == "gemini-3.5-pro"
    assert payload["agent"]["sandbox"] == "docker"
    assert payload["agent"]["provider"]
    assert payload["agent"]["git_sha"]
    assert payload["agent"]["decode_version"]
    assert payload["summary"]["handback"] == {"branch": FAKE_BRANCH, "pushed": True}
    assert set(payload["timings"]) == {"seed", "run", "verify"}
    assert all(payload["timings"][phase] >= 0.0 for phase in payload["timings"])


def test_the_run_command_carries_the_trial_flags(tmp_path: Path) -> None:
    """``decode run <instruction> --repo <seed> --local --max-requests --summary-json``."""
    task = _task(tmp_path, tables=_fast(max_steps=7))

    result = _run(task, tmp_path, "nop", model="gemini-3.5-pro")

    argv = _agent_stdout(result)["argv"]
    assert argv[0] == "run"
    assert argv[1] == task.instruction
    assert "--local" in argv
    assert argv[argv.index("--repo") + 1] == str(result.trial_dir / "seed")
    assert argv[argv.index("--max-requests") + 1] == "7"
    assert argv[argv.index("--summary-json") + 1] == str(
        result.trial_dir / "agent" / "summary.json"
    )
    assert argv[argv.index("--model") + 1] == "gemini-3.5-pro"
    assert _agent_stdout(result)["cwd"] == str((result.trial_dir / "home").resolve())


def test_no_model_flag_without_an_override(tmp_path: Path) -> None:
    """``--model`` is passed only when the caller overrode it — else the provider's own model runs."""
    task = _task(tmp_path)

    result = _run(task, tmp_path, "nop")

    assert "--model" not in _agent_stdout(result)["argv"]


def test_a_relative_job_dir_is_resolved_before_the_subprocess_starts(
    tmp_path: Path, monkeypatch
) -> None:
    """The child runs from ``home/``, so a relative ``--repo`` would name a Seed Repo that is not there.

    Regression: driven from the repo root with the default ``.decode/evals/runs/<job>`` job dir, the
    subprocess resolved ``--repo`` against its own cwd and ``decode run`` died with "repository does
    not exist" — an Infra Error on every trial.
    """
    task = _task(tmp_path)
    command = write_fake_decode(tmp_path, "oracle")
    monkeypatch.chdir(tmp_path)

    result = run_trial(
        task,
        sandbox="docker",
        job_dir=Path("runs") / "job",
        trial_id=TRIAL_ID,
        decode_command=command,
    )

    argv = _agent_stdout(result)["argv"]
    assert result.trial_dir.is_absolute()
    assert Path(argv[argv.index("--repo") + 1]).is_absolute()
    assert (result.status, result.reward) == ("agent_ok", 1.0)


# --------------------------------------------------------------------------- agent failures


def test_a_nop_agent_is_graded_on_the_base_commit(tmp_path: Path) -> None:
    """A skipped Hand-back (``handback: null``) grades the base commit and fails — never infra."""
    task = _task(tmp_path)

    result = _run(task, tmp_path, "nop")

    assert result.status == "agent_fail"
    assert result.reward == 0.0
    assert result.branch is None
    assert result.reason is not None
    assert "Hand-back" in result.reason
    assert (result.trial_dir / "result.json").is_file()


def test_a_hung_agent_is_interrupted_and_still_graded(tmp_path: Path) -> None:
    """The timeout path: SIGINT to the group, decode's ``finally`` writes the summary, still graded."""
    task = _task(tmp_path, tables=_fast(timeout_sec=1.0))

    started = time.monotonic()
    result = _run(task, tmp_path, "hang-handback")
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert result.status == "agent_fail"
    assert result.reward == 0.0
    assert result.summary is not None  # the SIGINT let the run hand back and report
    assert result.reason is not None
    assert "timed out" in result.reason
    assert elapsed < trial_module.SIGINT_GRACE_S  # the grace never ran: the run answered the SIGINT


def test_a_timeout_without_a_summary_is_still_an_agent_failure(tmp_path: Path) -> None:
    """A wedged agent is the worst agent behaviour, never an infra error (ADR-0022 §4)."""
    task = _task(tmp_path, tables=_fast(timeout_sec=1.0))

    result = _run(task, tmp_path, "hang")

    assert result.timed_out is True
    assert result.status == "agent_fail"
    assert result.reward == 0.0
    assert result.summary is None
    assert result.reason is not None
    assert "timed out" in result.reason


def test_an_agent_that_ignores_the_interrupt_is_killed_with_its_whole_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escalation: a run that swallows the SIGINT gets a SIGKILL, and its children go with it.

    The grace is patched to a second so the test costs seconds instead of the production minute.
    """
    monkeypatch.setattr(trial_module, "SIGINT_GRACE_S", 1.0)
    task = _task(tmp_path, tables=_fast(timeout_sec=1.0))

    result = _run(task, tmp_path, "hang-ignore-sigint")

    assert result.timed_out is True
    assert result.status == "agent_fail"  # a wedged agent is still graded, never an infra error
    assert (result.trial_dir / "result.json").is_file()  # the evidence survives the kill
    grandchild = _agent_stdout(result, line=1)["child_pid"]
    assert _reaped(grandchild), f"pid {grandchild} outlived the SIGKILL to its process group"


def _reaped(pid: int, *, deadline_s: float = 5.0) -> bool:
    """``True`` once ``pid`` is gone — polled, because the kernel reaps a group asynchronously."""
    until = time.monotonic() + deadline_s
    while time.monotonic() < until:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        time.sleep(0.05)
    return False


def test_a_request_limit_exit_is_an_agent_failure(tmp_path: Path) -> None:
    """``exit_reason: request_limit`` is the agent spending its budget — graded, in the denominator."""
    task = _task(tmp_path)

    result = _run(task, tmp_path, "capped")

    assert result.status == "agent_fail"
    assert result.reward == 0.0  # the ceiling still handed back; the wrong answer graded 0
    assert result.branch == FAKE_BRANCH
    assert result.reason is not None
    assert "request" in result.reason


# --------------------------------------------------------------------------- infra errors


def test_a_run_without_a_summary_is_an_infra_error(tmp_path: Path) -> None:
    """A ``decode run`` that died before reporting cannot be graded — excluded, with evidence."""
    task = _task(tmp_path)

    result = _run(task, tmp_path, "no-summary")

    assert result.status == "infra_error"
    assert result.reward is None
    assert result.summary is None
    assert result.reason is not None
    assert "summary" in result.reason
    assert (result.trial_dir / "result.json").is_file()  # the ``finally`` still left evidence


def test_a_garbage_reward_is_an_infra_error(tmp_path: Path) -> None:
    """A Verifier that wrote something unreadable never grades as a zero (ADR-0022 §3)."""
    task = _task(tmp_path, test_script=_script('printf "banana\\n" > "$VERIFIER_DIR/reward.txt"'))

    result = _run(task, tmp_path, "oracle")

    assert result.status == "infra_error"
    assert result.reward is None
    assert result.reason is not None
    assert "reward" in result.reason


def test_a_reward_outside_the_unit_range_is_an_infra_error(tmp_path: Path) -> None:
    """A reward must be a fraction of 1; ``7`` means the Verifier is broken, not that the agent won."""
    task = _task(tmp_path, test_script=_script('printf "7\\n" > "$VERIFIER_DIR/reward.txt"'))

    result = _run(task, tmp_path, "oracle")

    assert result.status == "infra_error"
    assert result.reason is not None
    assert "reward" in result.reason


def test_a_verifier_timeout_is_an_infra_error(tmp_path: Path) -> None:
    """A Verifier that hangs graded nothing — the trial is excluded, not scored zero."""
    tables = _fast()
    tables["verifier"] = {"timeout_sec": 1.0}
    task = _task(tmp_path, tables=tables, test_script=_script("sleep 30"))

    result = _run(task, tmp_path, "nop")

    assert result.status == "infra_error"
    assert result.reason is not None
    assert "Verifier" in result.reason


def test_a_failing_seed_is_an_infra_error(tmp_path: Path) -> None:
    """``setup.sh`` exiting non-zero is a broken task folder, not a failed agent."""
    task = _task(tmp_path, setup_script=_script("exit 3"))

    result = _run(task, tmp_path, "oracle")

    assert result.status == "infra_error"
    assert result.reason is not None
    assert "seed" in result.reason
    assert (result.trial_dir / "result.json").is_file()


def test_run_trial_never_raises(tmp_path: Path, mocker) -> None:
    """Opik's ``evaluate`` has no per-item isolation: one raised trial would abort the experiment."""
    task = _task(tmp_path)
    mocker.patch.object(trial_module, "_clone_pristine", side_effect=RuntimeError("disk on fire"))

    result = _run(task, tmp_path, "oracle")

    assert result.status == "infra_error"
    assert result.reason is not None
    assert "disk on fire" in result.reason


def test_a_missing_branch_clone_is_an_infra_error(tmp_path: Path) -> None:
    """A summary naming a branch the Seed Repo does not carry cannot be graded honestly."""
    task = _task(tmp_path)

    result = _run(task, tmp_path, "lying-branch")

    assert result.status == "infra_error"
    assert result.reason is not None
    assert "clone" in result.reason


# --------------------------------------------------------------------------- isolation


def test_the_agent_never_sees_the_hidden_tests_or_the_oracle(tmp_path: Path) -> None:
    """The Verifier and the gold answer are injected AFTER the run, into ``pristine/`` only."""
    task = _task(tmp_path)

    result = _run(task, tmp_path, "oracle")

    seen = _agent_stdout(result)["seed"]  # listed by the fake agent DURING its run
    assert "tests" not in seen
    assert "solution" not in seen
    for directory in (result.trial_dir / "seed", result.trial_dir / "home"):
        assert not (directory / "tests").exists()
        assert not (directory / "solution").exists()
    assert (result.trial_dir / "pristine" / "tests" / "test.sh").is_file()


def test_trial_imports_nothing_from_the_sandbox_or_tool_packages() -> None:
    """The Verifier is host-side: a trial drives ``decode run``, it never touches the seam itself."""
    tree = ast.parse(Path(trial_module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not [name for name in imported if name.startswith(("decode.sandbox", "decode.tools"))]


# --------------------------------------------------------------------------- the child env


def test_child_env_applies_the_trial_overrides_and_removals() -> None:
    """``SANDBOX_MODE`` / ``OPIK_PROJECT_NAME`` / ``RUNTIME_ENABLED`` set; three vars stripped."""
    parent = {
        "PATH": "/usr/bin",
        "KITARU_TASK_ID": "task-1",
        "KITARU_REPLAY_ID": "replay-1",
        "KITARU_AGENT_ID": "agent-1",
        "SANDBOX_REPO": "git@example.com:someone/else.git",
        "SANDBOX_MODE": "none",
    }

    config = Settings(_env_file=None)
    env = child_env(sandbox="modal", env=parent, config=config)

    assert env["SANDBOX_MODE"] == "modal"
    assert env["OPIK_PROJECT_NAME"] == config.eval_project_name
    assert env["RUNTIME_ENABLED"] == "true"
    assert env["KITARU_AGENT_ID"] == "agent-1"  # recording rides the Recording Seam, untouched
    assert env["PATH"] == "/usr/bin"
    for stripped in ("KITARU_TASK_ID", "KITARU_REPLAY_ID", "SANDBOX_REPO"):
        assert stripped not in env


def test_child_env_exports_the_parents_resolved_settings() -> None:
    """A key that lives only in the repo ``.env`` must reach a child launched from another cwd."""
    config = Settings(
        _env_file=None,
        gemini_api_key="key-from-dotenv",
        lsp_server_args=["server", "--verbose"],
        decode_dir=Path("/tmp/harness/.decode"),
        compaction_enabled=False,
        runtime_max_requests=11,
    )

    env = child_env(sandbox="docker", env={}, config=config)

    assert env["GEMINI_API_KEY"] == "key-from-dotenv"  # the secret is UNWRAPPED for the child
    assert env["COMPACTION_ENABLED"] == "false"
    assert env["RUNTIME_MAX_REQUESTS"] == "11"


def test_child_env_round_trips_through_settings(monkeypatch) -> None:
    """The exported values must PARSE back: a list env var is json, a Path a plain string."""
    config = Settings(
        _env_file=None,
        gemini_api_key="key-from-dotenv",
        lsp_server_args=["server", "--verbose"],
        decode_dir=Path("/tmp/harness/.decode"),
        compaction_enabled=False,
        llm_cost_input_usd_per_mtok=0.5,
    )

    env = child_env(sandbox="docker", env={}, config=config)

    monkeypatch.setattr(os, "environ", dict(env))
    child = Settings(_env_file=None)
    assert child.lsp_server_args == ["server", "--verbose"]
    assert child.decode_dir == Path("/tmp/harness/.decode")
    assert child.compaction_enabled is False
    assert child.llm_cost_input_usd_per_mtok == 0.5
    assert child.gemini_api_key.get_secret_value() == "key-from-dotenv"
    assert child.sandbox_mode == "docker"


def test_child_env_skips_an_unset_or_placeholder_secret() -> None:
    """An empty secret is exported as nothing at all, never as ``GEMINI_API_KEY=``."""
    config = Settings(_env_file=None, gemini_api_key="changeme", sandbox_git_token=None)

    env = child_env(sandbox="docker", env={}, config=config)

    assert "GEMINI_API_KEY" not in env
    assert "SANDBOX_GIT_TOKEN" not in env


def test_no_secret_reaches_the_result_json(tmp_path: Path, mocker) -> None:
    """``result.json`` is evidence a human reads; a configured key must never land in it."""
    sentinel = "sk-sentinel-must-not-appear-0123456789"
    mocker.patch.object(
        trial_module,
        "settings",
        Settings(_env_file=None, gemini_api_key=sentinel, sandbox_git_token=sentinel),
    )
    task = _task(tmp_path)

    result = _run(task, tmp_path, "nop")

    assert sentinel not in (result.trial_dir / "result.json").read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "posix", reason="process groups are a posix concept")
def test_the_subprocess_runs_in_its_own_process_group(tmp_path: Path) -> None:
    """``start_new_session=True`` is what lets the timeout SIGINT reach decode's whole tree."""
    task = _task(tmp_path)

    result = _run(task, tmp_path, "nop")

    assert _agent_stdout(result)["pgid"] != os.getpgrp()
