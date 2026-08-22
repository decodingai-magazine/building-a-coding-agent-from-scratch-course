"""The Modal Headless App's pure helpers — the launch surface's decisions (ADR-0020 §1-4, task 142).

Hermetic: no Modal object is created, no container starts, no ``git`` / ``decode`` process runs. The
script's whole job is to turn one operator invocation into the exact ``decode run`` subprocess the
container executes, so the tests drive the builders and assert the properties an operator's money
depends on:

* **``docker`` never reaches Modal.** There is no Docker daemon in a gVisor container, so the mode is
  rejected client-side with ONE friendly line — before ``.remote()``, before a container starts.
* **``none`` never passes ``--repo`` to decode.** decode's ``--repo``-under-``none`` guard
  (ADR-0012 §3) is not relaxed: the HARNESS clones the repo and launches decode with that cwd.
* **``modal`` passes ``--repo`` through**, so decode clones natively into its nested Modal Sandbox
  Workspace and the Hand-back ships ``decode/<session-id>``.
* **No credential is ever an argument.** ``SANDBOX_GIT_TOKEN`` reaches git as an env var read at push
  time by a credential helper — never an argv element, never a formatted log line.
"""

from __future__ import annotations

import pytest

from scripts import modal_headless as mh

TASK = "explain what this repo does"
REPO = "https://github.com/iusztinpaul/decode-course.git"
TOKEN = "ghp_notarealtoken_0123456789"


# --- sandbox-mode validation: docker is rejected client-side ---------------------------------------


def test_docker_is_rejected_with_one_friendly_decode_line():
    """AC2: a typo'd docker mode costs one line of text, not one container."""
    message = mh.sandbox_mode_error("docker")

    assert message is not None
    assert message.startswith("Decode: ")
    assert "\n" not in message
    assert "docker" in message and "none" in message and "modal" in message


@pytest.mark.parametrize("mode", ["none", "modal"])
def test_the_two_supported_modes_are_accepted(mode):
    assert mh.sandbox_mode_error(mode) is None


def test_an_unknown_mode_is_rejected_with_one_friendly_line():
    message = mh.sandbox_mode_error("kubernetes")

    assert message is not None
    assert message.startswith("Decode: ")
    assert "\n" not in message


def test_the_local_entrypoint_rejects_docker_before_any_remote_call(mocker, capsys):
    """The rejection happens on the laptop: no Function object is invoked, so nothing is billed."""
    function = mocker.patch.object(mh, "run_task")

    with pytest.raises(SystemExit) as exit_info:
        mh.main(task=TASK, sandbox_mode="docker")

    assert exit_info.value.code != 0
    function.remote.assert_not_called()
    assert capsys.readouterr().err.strip() == mh.DOCKER_MODE_MESSAGE


def test_the_function_defends_the_mode_in_container_without_a_traceback(mocker):
    """A direct ``.remote()`` / ``.spawn()`` caller (task 143) gets the same line, non-zero."""
    popen = mocker.patch("scripts.modal_headless.subprocess.Popen")

    result = mh.run_task.local(task=TASK, sandbox_mode="docker")

    popen.assert_not_called()
    assert result["exit_code"] != 0
    assert result["answer"] == mh.DOCKER_MODE_MESSAGE


# --- the decode argv: one console script, per-mode repo handling -----------------------------------


def test_none_mode_never_passes_repo_to_decode():
    """ADR-0012 §3 stands: decode refuses --repo under SANDBOX_MODE=none, so the harness clones."""
    argv = mh.decode_argv(task=TASK, sandbox_mode="none", repo=REPO)

    assert argv == [mh.DECODE_BIN, "run", TASK]


def test_none_mode_launches_decode_in_the_harness_clone():
    assert mh.decode_cwd(sandbox_mode="none", repo=REPO) == mh.REPO_CLONE_DIR


def test_none_mode_without_a_repo_launches_decode_in_the_harness_home():
    assert mh.decode_cwd(sandbox_mode="none", repo=None) == mh.HARNESS_HOME


def test_modal_mode_passes_the_repo_through_to_decode():
    """decode clones natively into the nested Modal Sandbox Workspace and hands the branch back."""
    argv = mh.decode_argv(task=TASK, sandbox_mode="modal", repo=REPO)

    assert argv == [mh.DECODE_BIN, "run", TASK, "--repo", REPO]


def test_modal_mode_keeps_the_harness_home_outside_the_repo_checkout():
    """ADR-0012 §6: sessions / logs / .decode/sandbox anchor here, never in the model's clone."""
    assert mh.decode_cwd(sandbox_mode="modal", repo=REPO) == mh.HARNESS_HOME


def test_the_model_override_is_passed_through():
    argv = mh.decode_argv(task=TASK, sandbox_mode="none", model="gemini-2.5-pro")

    assert argv[-2:] == ["--model", "gemini-2.5-pro"]


def test_no_model_flag_without_an_override():
    assert "--model" not in mh.decode_argv(task=TASK, sandbox_mode="none")


def test_the_decode_entrypoint_is_an_absolute_in_image_path():
    """The console script baked at build: no PATH set-up, no `uv run`, no shell."""
    assert mh.DECODE_BIN.startswith("/")
    assert mh.decode_argv(task=TASK, sandbox_mode="none")[0] == mh.DECODE_BIN


def test_the_harness_clone_is_a_plain_git_clone():
    assert mh.clone_argv(REPO, mh.REPO_CLONE_DIR) == ["git", "clone", REPO, mh.REPO_CLONE_DIR]


# --- the child env: one config surface, no bucket, no stray repo -----------------------------------


def test_the_child_env_pins_the_mode_the_config_surface_and_the_log_file():
    env = mh.decode_run_env({}, sandbox_mode="modal", log_file="/harness/decode-run.log")

    assert env["SANDBOX_MODE"] == "modal"
    assert env["DECODE_ENV"] == "local"  # ADR-0020 §4: secret env, never an Environment Bucket
    assert env["DECODE_LOG_FILE"] == "/harness/decode-run.log"


def test_the_child_env_keeps_the_secrets_the_container_was_given():
    env = mh.decode_run_env({"GEMINI_API_KEY": "k", "KITARU_AGENT_ID": "a"}, sandbox_mode="none")

    assert env["GEMINI_API_KEY"] == "k"
    assert env["KITARU_AGENT_ID"] == "a"


def test_none_mode_drops_a_stray_sandbox_repo_from_the_child_env():
    """A SANDBOX_REPO in the secret would trip decode's own --repo-under-none guard mid-run."""
    env = mh.decode_run_env({"SANDBOX_REPO": REPO}, sandbox_mode="none")

    assert "SANDBOX_REPO" not in env


def test_modal_mode_leaves_the_repo_to_the_flag_not_the_env():
    env = mh.decode_run_env({"SANDBOX_REPO": REPO}, sandbox_mode="modal")

    assert "SANDBOX_REPO" not in env


def test_the_child_env_asks_for_the_debug_line_that_names_the_session():
    """The session id is a DEBUG log line; the payload's session_id is parsed back out of it."""
    assert mh.decode_run_env({}, sandbox_mode="none")["LOG_LEVEL"] == "DEBUG"


def test_an_operator_chosen_log_level_wins():
    assert mh.decode_run_env({"LOG_LEVEL": "INFO"}, sandbox_mode="none")["LOG_LEVEL"] == "INFO"


# --- the git token: an env var read at push time, never an argument --------------------------------


def test_the_git_token_is_mirrored_into_the_env_var_git_reads():
    env = mh.decode_run_env({"SANDBOX_GIT_TOKEN": TOKEN}, sandbox_mode="modal")

    assert env[mh.GIT_TOKEN_ENV] == TOKEN


def test_no_token_no_github_token_variable():
    """ADR-0016: absent SANDBOX_GIT_TOKEN → the container holds no credential at all."""
    assert mh.GIT_TOKEN_ENV not in mh.decode_run_env({}, sandbox_mode="modal")


def test_an_empty_token_injects_nothing():
    assert mh.GIT_TOKEN_ENV not in mh.decode_run_env(
        {"SANDBOX_GIT_TOKEN": ""}, sandbox_mode="modal"
    )


def test_the_credential_helper_is_configured_only_when_a_token_is_present():
    assert mh.git_credential_argv({"SANDBOX_GIT_TOKEN": TOKEN}) is not None
    assert mh.git_credential_argv({}) is None
    assert mh.git_credential_argv({"SANDBOX_GIT_TOKEN": ""}) is None


def test_no_token_value_appears_in_any_argv_or_log_format_string():
    """AC4: the token reaches git through $GITHUB_TOKEN at push time, never through a command line."""
    argvs = [
        mh.decode_argv(task=TASK, sandbox_mode="modal", repo=REPO),
        mh.clone_argv(REPO, mh.REPO_CLONE_DIR),
        mh.git_credential_argv({"SANDBOX_GIT_TOKEN": TOKEN}),
    ]

    for argv in argvs:
        assert argv is not None
        assert not any(TOKEN in item for item in argv), argv
    assert "$" + mh.GIT_TOKEN_ENV in " ".join(mh.git_credential_argv({"SANDBOX_GIT_TOKEN": TOKEN}))
    assert TOKEN not in mh.RUN_SUMMARY_FORMAT


def test_the_credential_helper_matches_the_one_decode_itself_uses():
    """Drift guard: scripts/ is outside decode's import graph, so the string is copied — not forked."""
    from decode.sandbox.workspace import GIT_CREDENTIAL_HELPER_VALUE, GIT_TOKEN_ENV

    assert mh.GIT_CREDENTIAL_HELPER_VALUE == GIT_CREDENTIAL_HELPER_VALUE
    assert mh.GIT_TOKEN_ENV == GIT_TOKEN_ENV


# --- reading the run back out of the child's log ---------------------------------------------------

SESSION_ID = "9c2d0f1a-7b3e-4c55-9a2b-1d6f0e5c4b31"
# The Hand-back names the branch after the session id's first 8 chars (handback._branch_name).
SESSION_BRANCH = "decode/9c2d0f1a"
LOG = (
    "2026-08-22 10:00:00 DEBUG decode.cli: decode run starting (task='x', model=None)\n"
    f"2026-08-22 10:00:00 DEBUG decode.runtime.headless: headless run starting (session_id={SESSION_ID}, "
    "model=None, repo='https://example.com/r.git', local=False)\n"
)


def test_the_session_id_is_read_back_from_the_child_log():
    assert mh.session_id_from_log(LOG) == SESSION_ID


def test_a_log_without_a_session_line_yields_no_session_id():
    assert mh.session_id_from_log("nothing here") is None


def test_the_shipped_session_branch_is_read_back_from_the_hand_back_line():
    log = LOG + (
        "2026-08-22 10:05:00 INFO decode.runtime.headless: [handback] handed the workspace back on "
        f"branch {SESSION_BRANCH} (pushed to origin).\n"
    )

    assert mh.session_branch_from_log(log) == SESSION_BRANCH


def test_prose_punctuation_is_not_swallowed_into_the_branch_name():
    log = LOG + f"INFO decode.runtime.headless: [handback] handed it back on {SESSION_BRANCH}.\n"

    assert mh.session_branch_from_log(log) == SESSION_BRANCH


def test_a_skipped_hand_back_yields_no_branch():
    log = LOG + (
        "2026-08-22 10:05:00 INFO decode.sandbox.handback: [handback] the workspace is unchanged "
        "from the cloned HEAD, so there is nothing to hand back.\n"
    )

    assert mh.session_branch_from_log(log) is None


# --- the result payload ----------------------------------------------------------------------------


def test_the_payload_carries_the_answer_the_ids_and_the_exit_code():
    payload = mh.build_result(
        sandbox_mode="modal",
        repo=REPO,
        exit_code=0,
        stdout="the final answer\n",
        log_text=LOG
        + f"INFO decode.runtime.headless: [handback] handed it back on branch {SESSION_BRANCH}.\n",
    )

    assert payload["exit_code"] == 0
    assert payload["answer"] == "the final answer"
    assert payload["session_id"] == SESSION_ID
    assert payload["session_branch"] == SESSION_BRANCH
    assert payload["sandbox_mode"] == "modal"


def test_a_long_answer_is_returned_as_its_tail():
    payload = mh.build_result(
        sandbox_mode="none", repo=None, exit_code=0, stdout="x" * 20_000, log_text=""
    )

    assert payload["answer_truncated"] is True
    assert len(payload["answer"]) <= mh.ANSWER_TAIL_CHARS
    assert payload["answer"].endswith("x")


def test_none_mode_with_a_repo_says_in_the_payload_that_nothing_ships_back():
    """ADR-0020 §3: the harness clone lives and dies with the container — answer-only."""
    payload = mh.build_result(
        sandbox_mode="none", repo=REPO, exit_code=0, stdout="done", log_text=LOG
    )

    assert payload["session_branch"] is None
    assert "hand" in payload["note"].lower()


def test_a_branch_that_could_not_be_pushed_is_named_as_such_in_the_payload():
    """Live-run regression: without SANDBOX_GIT_TOKEN the Hand-back still NAMES a branch, but it only
    exists inside a container that is already gone — reporting it bare reads as "shipped"."""
    log = LOG + (
        f"WARNING decode.sandbox.handback: [handback] could not push {SESSION_BRANCH}: fatal: "
        "could not read Username for 'https://github.com'\n"
    )

    payload = mh.build_result(
        sandbox_mode="modal", repo=REPO, exit_code=0, stdout="done", log_text=log
    )

    assert payload["session_branch"] == SESSION_BRANCH
    assert "NOT pushed" in payload["note"]
    assert "SANDBOX_GIT_TOKEN" in payload["note"]


def test_a_pushed_branch_carries_no_note():
    log = LOG + f"INFO decode.sandbox.handback: [handback] pushed {SESSION_BRANCH} to origin\n"

    payload = mh.build_result(
        sandbox_mode="modal", repo=REPO, exit_code=0, stdout="done", log_text=log
    )

    assert payload["session_branch"] == SESSION_BRANCH
    assert payload["note"] == ""


def test_a_failed_run_keeps_its_exit_code_and_its_output():
    payload = mh.build_result(
        sandbox_mode="none",
        repo=None,
        exit_code=1,
        stdout="Decode: GEMINI_API_KEY is not set",
        log_text="",
    )

    assert payload["exit_code"] == 1
    assert "GEMINI_API_KEY" in payload["answer"]


# --- streaming the child (subprocess mocked) --------------------------------------------------------


def _popen(mocker, *, lines: list[str], returncode: int = 0):
    process = mocker.MagicMock()
    process.stdout = iter(lines)
    process.wait.return_value = returncode
    process.returncode = returncode
    return mocker.patch("scripts.modal_headless.subprocess.Popen", return_value=process)


def test_the_child_output_is_streamed_to_the_function_log_and_returned(mocker, capsys):
    _popen(mocker, lines=["thinking...\n", "the answer\n"])

    stdout, exit_code = mh.stream_subprocess(
        ["/bin/decode", "run", TASK], cwd="/harness", env={"A": "1"}, timeout_seconds=60
    )

    assert exit_code == 0
    assert stdout == "thinking...\nthe answer\n"
    assert "the answer" in capsys.readouterr().out


def test_the_child_runs_in_the_given_cwd_with_the_given_env(mocker):
    popen = _popen(mocker, lines=[])

    mh.stream_subprocess(["/bin/decode"], cwd="/harness", env={"A": "1"}, timeout_seconds=60)

    assert popen.call_args.kwargs["cwd"] == "/harness"
    assert popen.call_args.kwargs["env"] == {"A": "1"}


def test_the_harness_clone_replaces_a_leftover_from_a_re_used_container(mocker, tmp_path):
    """Modal re-uses warm containers, so the clone dir can already hold the PREVIOUS run's repo."""
    destination = tmp_path / "repo"
    destination.mkdir()
    (destination / "stale.txt").write_text("from the previous input")
    run = mocker.patch(
        "scripts.modal_headless.subprocess.run", return_value=mocker.Mock(returncode=0, stderr="")
    )

    mh.clone_for_none_mode(REPO, {}, dest=str(destination))

    assert not (destination / "stale.txt").exists()
    assert run.call_args.args[0] == ["git", "clone", REPO, str(destination)]


def test_a_failed_harness_clone_is_fatal(mocker, tmp_path):
    """ADR-0012 §3: nobody is watching a remote run, so an empty workspace would burn the whole run."""
    mocker.patch(
        "scripts.modal_headless.subprocess.run",
        return_value=mocker.Mock(returncode=128, stderr="fatal: repository not found"),
    )

    with pytest.raises(RuntimeError, match="nothing to work on"):
        mh.clone_for_none_mode(REPO, {}, dest=str(tmp_path / "repo"))


def test_a_non_zero_child_exit_is_reported_not_raised(mocker):
    _popen(mocker, lines=["boom\n"], returncode=3)

    _, exit_code = mh.stream_subprocess(["/bin/decode"], cwd="/harness", env={}, timeout_seconds=60)

    assert exit_code == 3
