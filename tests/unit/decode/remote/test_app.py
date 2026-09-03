"""The Modal Headless App's three Functions — thin callers of ``decode.remote.headless`` (ADR-0020).

Hermetic: the Functions are driven with ``.local()`` (no container), the run itself is mocked at
``execute_run``, and FastAPI is only needed for the one test that asserts the 400 (skipped where
the image-only package is absent).
"""

from __future__ import annotations

import pytest

from decode.remote import app as mh
from decode.remote import headless

TASK = "explain what this repo does"
REPO = "https://github.com/iusztinpaul/decode-course.git"


def test_the_deploy_target_is_the_named_app_the_launcher_resolves():
    """``decode remote run`` resolves ``Function.from_name(APP_NAME, "run_task")`` — both ends agree."""
    assert mh.app.name == headless.APP_NAME
    assert mh.run_task.local is not None


def test_run_task_is_execute_run_on_the_containers_env(mocker):
    execute = mocker.patch.object(mh, "execute_run", return_value={"exit_code": 0})

    assert mh.run_task.local(task=TASK, sandbox_mode="modal", repo=REPO, max_requests=5) == {
        "exit_code": 0
    }
    execute.assert_called_once_with(
        task=TASK,
        repo=REPO,
        sandbox_mode="modal",
        model=None,
        timeout_seconds=headless.DEFAULT_TIMEOUT_SECONDS,
        max_requests=5,
    )


def test_the_function_defends_the_mode_in_container_without_a_traceback(mocker):
    """A direct ``.remote()`` / ``.spawn()`` caller (task 143) gets the same line, non-zero."""
    popen = mocker.patch("decode.remote.headless.subprocess.Popen")

    result = mh.run_task.local(task=TASK, sandbox_mode="docker")

    popen.assert_not_called()
    assert result["exit_code"] != 0
    assert result["answer"] == headless.DOCKER_MODE_MESSAGE


# --- the nightly cron: deploy-time configuration from DECODE_NIGHTLY_* ---------------------------


def test_no_cron_env_registers_no_schedule():
    assert mh.nightly_schedule({}) is None


def test_a_cron_env_becomes_a_modal_cron():
    schedule = mh.nightly_schedule({headless.NIGHTLY_CRON_ENV: "0 2 * * *"})

    assert isinstance(schedule, mh.modal.Cron)
    assert schedule.proto_message.cron.cron_string == "0 2 * * *"


def test_the_nightly_function_runs_the_job_in_its_own_container(mocker, monkeypatch):
    monkeypatch.setenv(headless.NIGHTLY_TASK_ENV, TASK)
    monkeypatch.setenv(headless.NIGHTLY_REPO_ENV, REPO)
    monkeypatch.setenv(headless.NIGHTLY_SANDBOX_MODE_ENV, "modal")
    run_task = mocker.patch.object(mh, "run_task")
    run_task.local.return_value = {"exit_code": 0}

    assert mh.nightly.local() == {"exit_code": 0}
    run_task.local.assert_called_once_with(
        task=TASK,
        repo=REPO,
        sandbox_mode="modal",
        model=None,
        max_requests=None,
        timeout_seconds=headless.DEFAULT_TIMEOUT_SECONDS,
    )


def test_an_unconfigured_nightly_function_says_so_and_runs_nothing(mocker, monkeypatch, capsys):
    monkeypatch.delenv(headless.NIGHTLY_TASK_ENV, raising=False)
    run_task = mocker.patch.object(mh, "run_task")

    result = mh.nightly.local()

    run_task.local.assert_not_called()
    assert result["exit_code"] != 0
    assert capsys.readouterr().err.strip() == headless.NIGHTLY_UNCONFIGURED_MESSAGE


# --- the webhook: one POST, one spawned run ---------------------------------------------------------


def test_the_webhook_spawns_on_run_task_and_returns_at_once(mocker):
    run_task = mocker.patch.object(mh, "run_task")
    run_task.spawn.return_value = mocker.Mock(object_id="fc-456")

    response = mh.webhook.local(headless.WebhookRequest(task=TASK, max_requests=30))

    run_task.spawn.assert_called_once()
    assert run_task.spawn.call_args.kwargs["max_requests"] == 30
    assert response["call_id"] == "fc-456"


def test_the_webhook_rejects_a_bad_run_before_spawning_anything(mocker):
    run_task = mocker.patch.object(mh, "run_task")
    http_error = pytest.importorskip("fastapi").HTTPException

    with pytest.raises(http_error) as error:
        mh.webhook.local(headless.WebhookRequest(task=TASK, sandbox_mode="docker"))

    run_task.spawn.assert_not_called()
    assert error.value.status_code == 400
    assert error.value.detail == headless.DOCKER_MODE_MESSAGE


def test_the_webhook_image_carries_fastapi_the_worker_image_does_not():
    from decode.remote.image import extra_packages_command

    assert any(package.startswith("fastapi") for package in headless.WEB_PACKAGES)
    assert "/.uv/.venv/bin/python" in extra_packages_command(headless.WEB_PACKAGES)
