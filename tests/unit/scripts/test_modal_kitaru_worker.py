"""The Modal-hosted Kitaru Worker's decisions — claims, the agent-id scrub, the layout (task 145).

Hermetic: no Modal object is created, no container starts, no ``kitaru`` process runs. The script's
whole job is to turn one operator invocation into the exact ``kitaru worker start`` subprocess a
gVisor container runs for a day, so the tests drive the builders and assert the properties an
operator's replays depend on:

* **``KITARU_AGENT_ID`` never survives into the worker's env.** With it set, the Recording Seam of
  every spawned replay probes an agents route the task-scoped token cannot use → ``403`` and a
  hard-fail (ADR-0019 §3, tasks/139, 08_evals_replays.md §7.3). The secret is not supposed to carry
  it; this is the backstop, and it says so in one line.
* **Claims are scoped.** The Modal worker serves replay (``agent``) and ``evaluator`` work only —
  never ``importer``, whose jobs read export files that exist on the operator's laptop and nowhere
  in the container.
* **The in-image layout is ONE definition.** Agent version 3's registered run spec names
  :data:`scripts.modal_image.DECODE_BIN` and :data:`scripts.modal_image.HARNESS_HOME` verbatim; a
  drifted path here is not a red test, it is every replay failing to spawn on a machine nobody is
  watching.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import modal_headless as mh
from scripts import modal_image as mi
from scripts import modal_kitaru_worker as mkw

AGENT_VERSION_ID = "01a02523-1097-77e1-aa74-c64e7593050b"
WORKSPACE_URL = "https://f5ee9622-kitaru.cloudinfra.zenml.io"
API_KEY = "ZENPROKEY_notarealkey_0123456789"


def _configured_env(**overrides: str) -> dict[str, str]:
    """A container env shaped like the ``decode-kitaru-worker`` Secret's (no agent id)."""
    env = {
        "KITARU_API_URL": WORKSPACE_URL,
        "KITARU_API_KEY": API_KEY,
        "GEMINI_API_KEY": "gem-notreal",
        "DECODE_ENV": "local",
    }
    env.update(overrides)
    return env


# --- the image and its layout: one build, shared with the headless app -----------------------------


def test_both_modal_apps_run_the_same_in_image_decode_entrypoint():
    """AC4: the path agent v3 is registered with is ONE constant, not two that agree today."""
    assert mkw.DECODE_BIN is mi.DECODE_BIN
    assert mh.DECODE_BIN is mi.DECODE_BIN


def test_both_modal_apps_use_the_same_harness_home():
    assert mkw.HARNESS_HOME is mi.HARNESS_HOME
    assert mh.HARNESS_HOME is mi.HARNESS_HOME


def test_the_registered_v3_paths_are_pinned_to_their_exact_values():
    """Agent versions are immutable: moving either path silently orphans the registered v3 spec."""
    assert mi.DECODE_BIN == "/.uv/.venv/bin/decode"
    assert mi.HARNESS_HOME == "/harness"


def test_the_kitaru_console_script_lives_beside_decode_in_the_same_venv():
    assert f"{mi.VENV_DIR}/bin/kitaru" == mkw.KITARU_BIN
    assert mkw.KITARU_BIN.startswith("/")


def test_only_the_shared_helper_builds_an_image():
    """AC1: no copy-pasted build block — the recipe exists in exactly one file.

    Asserted on ``modal.Image`` and ``add_local_dir``, the two the build cannot be written without;
    both scripts still DISCUSS the image in prose, and should.
    """
    for module in (mh, mkw):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "modal.Image" not in source
        assert "add_local_dir" not in source
    helper = Path(mi.__file__).read_text(encoding="utf-8")
    assert "modal.Image" in helper


@pytest.fixture
def image(mocker):
    """A chainable stand-in for :class:`modal.Image`, recording every build step."""
    stub = mocker.MagicMock()
    for step in (
        "apt_install",
        "uv_sync",
        "add_local_dir",
        "add_local_python_source",
        "run_commands",
        "env",
    ):
        getattr(stub, step).return_value = stub
    mocker.patch.object(mi.modal.Image, "debian_slim", return_value=stub)
    return stub


def test_the_shared_image_creates_the_harness_home_and_any_extra_dir(image):
    mi.build_image(extra_dirs=("/scratch/repo",))

    commands = [call.args[0] for call in image.run_commands.call_args_list]
    mkdir = [command for command in commands if command.startswith("mkdir")]
    assert mkdir == [f"mkdir -p {mi.HARNESS_HOME} /scratch/repo"]


def test_the_scripts_package_is_importable_inside_the_container(image):
    """Regression: both apps import this shared helper, and a Modal container's sys.path is not ours.

    Found live — the first ``modal run`` of the worker died at import with
    ``ModuleNotFoundError: No module named 'scripts'`` before a single line of the Function ran.
    """
    mi.build_image()

    image.add_local_python_source.assert_called_once_with("scripts")
    # And it must be the LAST step: Modal refuses a build step after an ``add_local_*`` (the deploy
    # fails outright), so an added-in-the-middle source step breaks both apps at deploy time.
    assert image.mock_calls[-1][0] == "add_local_python_source"


# --- claim scoping: the Modal worker serves replays and evaluators, never imports -------------------


def test_the_worker_claims_agent_and_evaluator_work():
    assert mkw.worker_claims() == ["agent", "evaluator"]


def test_the_worker_never_claims_importer_work():
    """Importer jobs read local export files, which exist on the laptop and nowhere in a container."""
    assert "importer" not in mkw.worker_claims()
    assert "importer" not in mkw.worker_argv(concurrency=4)


def test_the_agent_claim_can_be_narrowed_to_one_agent_version():
    """Both workers poll the same queue: unscoped, the Modal one would claim a docker v2 replay."""
    claims = mkw.worker_claims(agent_version_id=AGENT_VERSION_ID)

    assert claims == [f"agent={AGENT_VERSION_ID}", "evaluator"]


def test_the_argv_passes_every_claim_as_its_own_flag():
    argv = mkw.worker_argv(concurrency=4, agent_version_id=AGENT_VERSION_ID)

    assert argv.count("--claim") == 2
    assert f"agent={AGENT_VERSION_ID}" in argv
    assert "evaluator" in argv


# --- the worker argv: the laptop's command, in a container -----------------------------------------


def test_the_argv_starts_the_worker_from_the_in_image_console_script():
    argv = mkw.worker_argv(concurrency=4)

    assert argv[:3] == [mkw.KITARU_BIN, "worker", "start"]


def test_the_concurrency_reaches_the_worker():
    argv = mkw.worker_argv(concurrency=7)

    assert argv[argv.index("--concurrency") + 1] == "7"


def test_the_worker_is_named_so_an_operator_can_spot_it_in_worker_list():
    argv = mkw.worker_argv(concurrency=4)

    assert argv[argv.index("--name") + 1] == mkw.DEFAULT_WORKER_NAME
    assert "modal" in mkw.DEFAULT_WORKER_NAME


def test_an_operator_can_name_the_worker():
    argv = mkw.worker_argv(concurrency=4, name="decode-modal-experiment")

    assert argv[argv.index("--name") + 1] == "decode-modal-experiment"


def test_the_argv_carries_no_credential():
    """Credentials reach the worker through the Secret's env, never an argv a log could echo."""
    argv = mkw.worker_argv(concurrency=4, agent_version_id=AGENT_VERSION_ID)

    assert not [item for item in argv if API_KEY in item or WORKSPACE_URL in item]


# --- the KITARU_AGENT_ID scrub: the 403 trap cannot fire --------------------------------------------


def test_the_agent_id_is_dropped_from_the_workers_env():
    """AC2: a configured agent id turns every spawned replay into a 403 hard-fail."""
    env = mkw.worker_env(_configured_env(KITARU_AGENT_ID=AGENT_VERSION_ID))

    assert "KITARU_AGENT_ID" not in env


def test_the_scrub_keeps_every_other_variable_the_container_was_given():
    env = mkw.worker_env(_configured_env(KITARU_AGENT_ID=AGENT_VERSION_ID))

    assert env["KITARU_API_URL"] == WORKSPACE_URL
    assert env["KITARU_API_KEY"] == API_KEY
    assert env["GEMINI_API_KEY"] == "gem-notreal"


def test_an_env_without_an_agent_id_is_passed_through_untouched():
    base = _configured_env()

    assert mkw.worker_env(base) == base


def test_the_scrub_is_announced_in_one_line_that_names_the_reason():
    line = mkw.agent_id_scrub_line(_configured_env(KITARU_AGENT_ID=AGENT_VERSION_ID))

    assert line is not None
    assert "\n" not in line
    assert "KITARU_AGENT_ID" in line
    assert "403" in line


def test_the_scrub_line_never_echoes_the_agent_id_it_dropped():
    line = mkw.agent_id_scrub_line(_configured_env(KITARU_AGENT_ID=AGENT_VERSION_ID))

    assert AGENT_VERSION_ID not in str(line)


def test_nothing_is_announced_when_the_secret_is_composed_correctly():
    assert mkw.agent_id_scrub_line(_configured_env()) is None


def test_an_empty_agent_id_is_dropped_too():
    """``KITARU_AGENT_ID=`` is still a set variable to the Recording Seam's ``os.environ`` check."""
    env = _configured_env(KITARU_AGENT_ID="")

    assert "KITARU_AGENT_ID" not in mkw.worker_env(env)


# --- pre-flight: a worker that cannot authenticate should say so, not poll 401s for a day -----------


def test_a_container_without_a_workspace_url_is_refused_with_one_friendly_line():
    message = mkw.credential_error({"KITARU_API_KEY": API_KEY})

    assert message is not None
    assert "KITARU_API_URL" in message
    assert message.startswith("Decode: ")


def test_a_container_without_a_credential_is_refused_with_one_friendly_line():
    """A container has no ``kitaru login`` store, so an absent key is a day of 401 polling."""
    message = mkw.credential_error({"KITARU_API_URL": WORKSPACE_URL})

    assert message is not None
    assert "KITARU_API_KEY" in message
    assert mkw.SECRET_NAME in message


def test_a_static_task_token_also_counts_as_a_credential():
    env = {"KITARU_API_URL": WORKSPACE_URL, "KITARU_API_TOKEN": "eyJhbGciOi.notreal"}

    assert mkw.credential_error(env) is None


def test_a_fully_configured_container_passes_pre_flight():
    assert mkw.credential_error(_configured_env()) is None


def test_the_pre_flight_message_never_echoes_the_credential_it_checked():
    message = mkw.credential_error({"KITARU_API_URL": WORKSPACE_URL})

    assert API_KEY not in str(message)


# --- the Function: harness home, scrub, subprocess (all mocked) -------------------------------------


@pytest.fixture
def container(mocker, tmp_path):
    """A container whose Harness Home is a temp dir and whose ``kitaru`` never runs."""
    mocker.patch.object(mkw, "HARNESS_HOME", str(tmp_path / "harness"))
    mocker.patch.dict(mkw.os.environ, _configured_env(), clear=True)
    return mocker.patch(
        "scripts.modal_kitaru_worker.subprocess.run", return_value=mocker.Mock(returncode=0)
    )


def test_the_harness_home_exists_before_the_worker_claims_anything(container, tmp_path):
    """AC: v3's ``--working-dir`` is the cwd of every spawned replay — a missing dir is a spawn error."""
    mkw.run_worker.local()

    assert (tmp_path / "harness").is_dir()


def test_creating_an_existing_harness_home_is_not_an_error(container, tmp_path):
    (tmp_path / "harness").mkdir()

    assert mkw.run_worker.local() == 0


def test_the_worker_runs_in_the_harness_home_with_the_scrubbed_env(mocker, tmp_path):
    mocker.patch.object(mkw, "HARNESS_HOME", str(tmp_path / "harness"))
    mocker.patch.dict(mkw.os.environ, _configured_env(KITARU_AGENT_ID=AGENT_VERSION_ID), clear=True)
    run = mocker.patch(
        "scripts.modal_kitaru_worker.subprocess.run", return_value=mocker.Mock(returncode=0)
    )

    mkw.run_worker.local()

    assert run.call_args.kwargs["cwd"] == str(tmp_path / "harness")
    assert "KITARU_AGENT_ID" not in run.call_args.kwargs["env"]


def test_the_function_scrubs_the_agent_id_with_one_logged_line(mocker, tmp_path, capsys):
    """Story 2: the operator learns the variable was dropped, and why, from the Function log."""
    mocker.patch.object(mkw, "HARNESS_HOME", str(tmp_path / "harness"))
    mocker.patch.dict(mkw.os.environ, _configured_env(KITARU_AGENT_ID=AGENT_VERSION_ID), clear=True)
    mocker.patch(
        "scripts.modal_kitaru_worker.subprocess.run", return_value=mocker.Mock(returncode=0)
    )

    mkw.run_worker.local()

    logged = [line for line in capsys.readouterr().err.splitlines() if "KITARU_AGENT_ID" in line]
    assert len(logged) == 1


def test_the_concurrency_and_scope_reach_the_subprocess(container):
    mkw.run_worker.local(concurrency=9, agent_version_id=AGENT_VERSION_ID)

    argv = container.call_args.args[0]
    assert argv == mkw.worker_argv(concurrency=9, agent_version_id=AGENT_VERSION_ID)


def test_a_container_that_cannot_authenticate_exits_without_starting_the_worker(mocker, tmp_path):
    mocker.patch.object(mkw, "HARNESS_HOME", str(tmp_path / "harness"))
    mocker.patch.dict(mkw.os.environ, {"GEMINI_API_KEY": "gem-notreal"}, clear=True)
    run = mocker.patch("scripts.modal_kitaru_worker.subprocess.run")

    exit_code = mkw.run_worker.local()

    assert exit_code != 0
    run.assert_not_called()


def test_a_dead_worker_reports_its_exit_code_instead_of_raising(mocker, tmp_path):
    mocker.patch.object(mkw, "HARNESS_HOME", str(tmp_path / "harness"))
    mocker.patch.dict(mkw.os.environ, _configured_env(), clear=True)
    mocker.patch(
        "scripts.modal_kitaru_worker.subprocess.run", return_value=mocker.Mock(returncode=137)
    )

    assert mkw.run_worker.local() == 137


def test_the_function_may_run_for_a_whole_day():
    """A Worker is long-running by definition; 24h is Modal's ceiling (ADR-0020 §5)."""
    assert mkw.FUNCTION_TIMEOUT_SECONDS == 24 * 60 * 60


def test_the_worker_runs_on_the_purpose_split_secret():
    """ADR-0020 §4: its own Secret, deliberately without an agent id."""
    assert mkw.SECRET_NAME == "decode-kitaru-worker"
    assert mkw.APP_NAME == "decode-kitaru-worker"


# --- the launch surface -----------------------------------------------------------------------------


def test_the_local_entrypoint_fires_the_function_with_the_operators_options(mocker):
    function = mocker.patch.object(mkw, "run_worker")
    function.remote.return_value = 0

    mkw.main(concurrency=6, agent_version_id=AGENT_VERSION_ID, name="decode-modal-experiment")

    assert function.remote.call_args.kwargs == {
        "concurrency": 6,
        "agent_version_id": AGENT_VERSION_ID,
        "name": "decode-modal-experiment",
    }


def test_the_launcher_exits_non_zero_when_the_worker_died_unhappy(mocker):
    function = mocker.patch.object(mkw, "run_worker")
    function.remote.return_value = 3

    with pytest.raises(SystemExit) as exit_info:
        mkw.main()

    assert exit_info.value.code == 3
