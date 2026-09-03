"""``decode remote`` — the Modal launch surface, driven through Click (ADR-0020, task 154).

Hermetic: the deployment is a ``MagicMock`` standing in for ``modal.Function.from_name``; no
``modal`` object is created, no container starts, no subprocess runs. The tests assert the
properties an operator's money depends on:

* **validation is client-side** — ``docker``, a fan-out without a repo, zero attempts: ONE friendly
  line, non-zero exit, and the deployment is never even looked up;
* **every subcommand targets the DEPLOYED app** — ``Function.from_name``, never an ephemeral app —
  which is what makes ``--detach`` real (the app outlives the launcher);
* **the REPL pays nothing for the group** — importing ``decode.cli`` imports no ``modal``.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from decode.cli import cli
from decode.remote import cli as remote_cli
from decode.remote import headless as mh

TASK = "explain what this repo does"
REPO = "https://github.com/iusztinpaul/decode-course.git"
SESSION_ID = "3f662b01-3ab6-49d0-b2b6-9ebc58acb14e"
SESSION_BRANCH = "decode/3f662b01"


def _payload(**overrides) -> dict[str, object]:
    payload = {
        "exit_code": 0,
        "sandbox_mode": "modal",
        "answer": "done",
        "answer_truncated": False,
        "session_id": SESSION_ID,
        "session_branch": SESSION_BRANCH,
        "note": "",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def deployment(mocker):
    """The deployed ``run_task`` Function, as every subcommand sees it."""
    function = mocker.MagicMock()
    mocker.patch.object(remote_cli, "deployed_run_task", return_value=function)
    return function


def _invoke(*args: str):
    return CliRunner().invoke(cli, ["remote", *args])


# --- the group on the decode CLI --------------------------------------------------------------------


def test_remote_is_a_subcommand_group_of_decode():
    assert "remote" in cli.commands
    assert set(cli.commands["remote"].commands) == {"deploy", "run", "attempts", "logs"}


def test_importing_the_cli_does_not_import_modal():
    """The launcher's ``modal`` import is lazy: the REPL and ``decode run`` never pay for it."""
    import subprocess
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import decode.cli, sys; "
            "assert 'modal' not in sys.modules, 'modal imported'; "
            "assert 'decode.remote.app' not in sys.modules, 'app imported'",
        ],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr


# --- decode remote run ------------------------------------------------------------------------------


def test_run_rejects_docker_before_any_remote_call(mocker):
    """AC2: the rejection happens on the laptop — no Function is looked up, nothing is billed."""
    resolve = mocker.patch.object(remote_cli, "deployed_run_task")

    result = _invoke("run", TASK, "--sandbox-mode", "docker")

    assert result.exit_code != 0
    resolve.assert_not_called()
    assert mh.DOCKER_MODE_MESSAGE in result.output
    assert "Traceback" not in result.output


def test_run_calls_the_deployment_with_every_knob_and_prints_the_answer(deployment):
    deployment.remote.return_value = _payload(answer="the answer")

    result = _invoke(
        "run",
        TASK,
        "--repo",
        REPO,
        "--sandbox-mode",
        "modal",
        "--model",
        "gemini-2.5-pro",
        "--timeout-seconds",
        "60",
        "--max-requests",
        "7",
    )

    assert result.exit_code == 0, result.output
    deployment.remote.assert_called_once_with(
        task=TASK,
        repo=REPO,
        sandbox_mode="modal",
        model="gemini-2.5-pro",
        timeout_seconds=60,
        max_requests=7,
    )
    assert "the answer" in result.output
    assert SESSION_BRANCH in result.output  # the summary line names the shipped branch


def test_run_defaults_match_the_functions_own(deployment):
    deployment.remote.return_value = _payload()

    _invoke("run", TASK)

    kwargs = deployment.remote.call_args.kwargs
    assert kwargs["sandbox_mode"] == mh.DEFAULT_SANDBOX_MODE
    assert kwargs["timeout_seconds"] == mh.DEFAULT_TIMEOUT_SECONDS
    assert kwargs["repo"] is None and kwargs["model"] is None and kwargs["max_requests"] is None


def test_run_exits_with_the_runs_own_code(deployment):
    deployment.remote.return_value = _payload(exit_code=3)

    result = _invoke("run", TASK)

    assert result.exit_code == 3


def test_a_missing_deployment_is_one_friendly_line_not_a_traceback(mocker):
    """The app must be deployed once before it can be run against (ADR-0020 §1)."""
    modal = pytest.importorskip("modal")
    mocker.patch.object(
        modal.Function, "from_name", side_effect=modal.exception.NotFoundError("no such app")
    )

    result = _invoke("run", TASK)

    assert result.exit_code != 0
    assert "decode remote deploy" in result.output
    assert "Traceback" not in result.output


def test_missing_modal_credentials_are_one_friendly_line(mocker):
    modal = pytest.importorskip("modal")
    mocker.patch.object(
        modal.Function, "from_name", side_effect=modal.exception.AuthError("no token")
    )

    result = _invoke("run", TASK)

    assert result.exit_code != 0
    assert "modal token set" in result.output
    assert "Traceback" not in result.output


def test_the_deployment_is_resolved_by_name_never_an_ephemeral_app(mocker):
    """``--detach`` is only fire-and-forget if the app OUTLIVES the launcher: spawn on the deployment.

    ``modal run`` tears its ephemeral app down when the entrypoint returns, taking the spawned calls
    with it; ``Function.from_name`` targets what ``decode remote deploy`` published (ADR-0020 §1).
    """
    modal = pytest.importorskip("modal")
    from_name = mocker.patch.object(modal.Function, "from_name")

    remote_cli.deployed_run_task()

    from_name.assert_called_once_with(mh.APP_NAME, "run_task")


# --- decode remote attempts: spawning, N independent calls, no stagger -----------------------------


def test_the_fan_out_spawns_one_independent_call_per_attempt(mocker):
    """Each attempt = its own container = its own Workspace = its own branch. No warm-up, no stagger."""
    function = mocker.MagicMock()

    calls = remote_cli.spawn_attempts(
        function,
        task=TASK,
        count=3,
        repo=REPO,
        sandbox_mode="modal",
        model=None,
        timeout_seconds=60,
    )

    assert len(calls) == 3
    assert function.spawn.call_count == 3
    for call in function.spawn.call_args_list:
        assert call.kwargs["task"] == mh.attempt_task(TASK)
        assert call.kwargs["repo"] == REPO
        assert call.kwargs["sandbox_mode"] == "modal"
        assert call.kwargs["timeout_seconds"] == 60


def test_the_fan_out_threads_the_ceiling_into_every_attempt(mocker):
    function = mocker.MagicMock()

    remote_cli.spawn_attempts(
        function,
        task=TASK,
        count=2,
        repo=REPO,
        sandbox_mode="modal",
        model=None,
        timeout_seconds=60,
        max_requests=40,
    )

    for call in function.spawn.call_args_list:
        assert call.kwargs["max_requests"] == 40


def test_an_attempt_that_returns_normally_is_collected_as_its_payload(mocker):
    call = mocker.MagicMock()
    call.get.return_value = _payload()

    assert remote_cli.collect_attempt(call, sandbox_mode="modal") == _payload()


def test_an_attempt_that_raises_becomes_a_failed_row_instead_of_killing_the_fan_out(mocker):
    """N-1 finished attempts are worth real money; one exception must not take the table with it."""
    call = mocker.MagicMock()
    call.get.side_effect = RuntimeError("container died")

    result = remote_cli.collect_attempt(call, sandbox_mode="modal")

    assert result["error"]
    assert "container died" in str(result["note"])
    assert result["session_branch"] is None


# --- decode remote attempts: the subcommand ---------------------------------------------------------


def test_attempts_rejects_a_bad_fan_out_before_spawning_anything(mocker):
    """AC2: the validation is client-side — no deployed Function is even looked up."""
    resolve = mocker.patch.object(remote_cli, "deployed_run_task")

    result = _invoke("attempts", TASK, "--attempts", "3")  # no --repo

    assert result.exit_code != 0
    resolve.assert_not_called()
    assert "--repo" in result.output
    assert "Traceback" not in result.output


def test_attempts_warns_once_when_none_mode_will_ship_nothing(deployment):
    call = deployment.spawn.return_value
    call.get.return_value = _payload(sandbox_mode="none", session_branch=None)

    result = _invoke("attempts", TASK, "--repo", REPO, "--attempts", "1", "--sandbox-mode", "none")

    assert mh.NONE_MODE_ATTEMPTS_WARNING in result.output


def test_detached_attempts_never_wait_on_a_call(deployment):
    """The whole point: spawn, print the ids, exit — no ``.get()``, no blocking."""
    call = deployment.spawn.return_value
    call.object_id = "fc-abc"

    result = _invoke(
        "attempts", TASK, "--repo", REPO, "--attempts", "2", "--sandbox-mode", "modal", "--detach"
    )

    assert result.exit_code == 0, result.output
    call.get.assert_not_called()
    assert deployment.spawn.call_count == 2
    assert "fc-abc" in result.output
    assert "decode remote logs" in result.output


def test_waiting_attempts_print_the_table_and_the_tail(deployment):
    deployment.spawn.return_value.get.return_value = _payload()

    result = _invoke("attempts", TASK, "--repo", REPO, "--attempts", "2", "--sandbox-mode", "modal")

    assert result.exit_code == 0, result.output
    assert SESSION_BRANCH in result.output
    assert "git ls-remote" in result.output


def test_attempts_exit_non_zero_when_every_attempt_failed(deployment):
    deployment.spawn.return_value.get.return_value = _payload(exit_code=1)

    result = _invoke("attempts", TASK, "--repo", REPO, "--attempts", "2", "--sandbox-mode", "modal")

    assert result.exit_code != 0


# --- decode remote deploy / logs: thin wrappers over the modal CLI ---------------------------------


def test_deploy_runs_modal_deploy_on_the_app_module(mocker):
    mocker.patch.object(remote_cli, "repo_root_error", return_value=None)
    run = mocker.patch.object(remote_cli.subprocess, "run", return_value=mocker.Mock(returncode=0))

    result = _invoke("deploy")

    assert result.exit_code == 0, result.output
    run.assert_called_once_with(["modal", "deploy", "-m", "decode.remote.app"], check=False)


def test_deploy_outside_a_checkout_is_one_friendly_line_and_no_modal_call(mocker):
    """The image bakes the repo source; an installed wheel has none to bake."""
    mocker.patch.object(remote_cli, "repo_root_error", return_value="Decode: not a checkout")
    run = mocker.patch.object(remote_cli.subprocess, "run")

    result = _invoke("deploy")

    assert result.exit_code != 0
    run.assert_not_called()
    assert "not a checkout" in result.output


def test_deploy_relays_a_failed_modal_deploy_exit_code(mocker):
    mocker.patch.object(remote_cli, "repo_root_error", return_value=None)
    mocker.patch.object(remote_cli.subprocess, "run", return_value=mocker.Mock(returncode=7))

    assert _invoke("deploy").exit_code == 7


def test_logs_tails_the_deployed_apps_logs(mocker):
    run = mocker.patch.object(remote_cli.subprocess, "run", return_value=mocker.Mock(returncode=0))

    result = _invoke("logs")

    assert result.exit_code == 0
    run.assert_called_once_with(["modal", "app", "logs", mh.APP_NAME], check=False)
