"""The Modal Headless App's pure helpers — the launch surface's decisions (ADR-0020 §1-4, task 142).

Hermetic: no Modal object is created, no container starts, no ``git`` / ``decode`` process runs. The
module's whole job is to turn one operator invocation into the exact ``decode run`` subprocess the
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

from decode.remote import headless as mh

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


def test_the_function_defends_the_mode_in_container_without_a_traceback(mocker):
    """A direct ``.remote()`` / ``.spawn()`` caller (task 143) gets the same line, non-zero."""
    popen = mocker.patch("decode.remote.headless.subprocess.Popen")

    result = mh.execute_run(task=TASK, sandbox_mode="docker")

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


def test_the_leftover_log_of_a_re_used_container_is_cleared_before_the_run(tmp_path):
    """Live-run regression (task 143): decode APPENDS to DECODE_LOG_FILE and Modal re-uses warm
    containers, so attempt 1 of a fan-out reported the session id of the PREVIOUS run — a table row
    whose session and branch belonged to two different agents."""
    log = tmp_path / "decode-run.log"
    log.write_text(f"DEBUG headless run starting (session_id={SESSION_ID})\n")

    mh.reset_child_log(str(log))

    assert not log.exists()


def test_clearing_a_log_that_was_never_written_is_not_an_error(tmp_path):
    mh.reset_child_log(str(tmp_path / "never-written.log"))


def test_the_session_id_of_a_concatenated_log_is_the_LATEST_run_not_the_first():
    """Second defence for the same regression: whatever is in the file, the run that just finished is
    the last one logged — the same rule ``session_branch_from_log`` already follows."""
    stale = "DEBUG headless run starting (session_id=11111111-1111-4111-8111-111111111111)\n"

    assert mh.session_id_from_log(stale + LOG) == SESSION_ID


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
    return mocker.patch("decode.remote.headless.subprocess.Popen", return_value=process)


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
        "decode.remote.headless.subprocess.run", return_value=mocker.Mock(returncode=0, stderr="")
    )

    mh.clone_for_none_mode(REPO, {}, dest=str(destination))

    assert not (destination / "stale.txt").exists()
    assert run.call_args.args[0] == ["git", "clone", REPO, str(destination)]


def test_a_failed_harness_clone_is_fatal(mocker, tmp_path):
    """ADR-0012 §3: nobody is watching a remote run, so an empty workspace would burn the whole run."""
    mocker.patch(
        "decode.remote.headless.subprocess.run",
        return_value=mocker.Mock(returncode=128, stderr="fatal: repository not found"),
    )

    with pytest.raises(RuntimeError, match="nothing to work on"):
        mh.clone_for_none_mode(REPO, {}, dest=str(tmp_path / "repo"))


def test_a_non_zero_child_exit_is_reported_not_raised(mocker):
    _popen(mocker, lines=["boom\n"], returncode=3)

    _, exit_code = mh.stream_subprocess(["/bin/decode"], cwd="/harness", env={}, timeout_seconds=60)

    assert exit_code == 3


class _ImmediateTimer:
    """A ``threading.Timer`` that fires the moment it is started — the kill path, without the wait."""

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function

    def start(self):
        self.function()

    def cancel(self):
        """Cancelling an already-fired timer is a no-op, exactly as it is for the real one."""


def test_a_child_past_its_timeout_is_killed(mocker):
    """The hang becomes a normal non-zero exit with partial output, not a Function-ceiling death."""
    popen = _popen(mocker, lines=["thinking...\n"], returncode=-9)
    mocker.patch("decode.remote.headless.threading.Timer", _ImmediateTimer)

    stdout, exit_code = mh.stream_subprocess(
        ["/bin/decode"], cwd="/harness", env={}, timeout_seconds=1800
    )

    popen.return_value.kill.assert_called_once_with()
    assert exit_code == -9
    assert stdout == "thinking...\n"


def test_a_timed_out_child_says_so_in_one_line(mocker, capsys):
    """Otherwise the operator reads an unexplained exit=-9 and blames the agent for the timer."""
    _popen(mocker, lines=[], returncode=-9)
    mocker.patch("decode.remote.headless.threading.Timer", _ImmediateTimer)

    mh.stream_subprocess(["/bin/decode"], cwd="/harness", env={}, timeout_seconds=1800)

    logged = [line for line in capsys.readouterr().err.splitlines() if line.startswith("Decode: ")]
    assert len(logged) == 1
    assert "1800" in logged[0]


def test_a_child_that_finishes_in_time_is_never_reported_as_timed_out(mocker, capsys):
    _popen(mocker, lines=["the answer\n"], returncode=0)

    mh.stream_subprocess(["/bin/decode"], cwd="/harness", env={}, timeout_seconds=60)

    assert capsys.readouterr().err == ""


# ===================================================================================================
# N parallel attempts at one task (task 143, ADR-0020 §1) — the successor of demo-multiple-attempts.sh
# ===================================================================================================
#
# One deployed image, N spawned containers, N ``decode/<session-id>`` branches to read side by side.
# The helpers below are everything the fan-out decides BEFORE and AFTER the money is spent: what the
# operator is allowed to ask for, what text every attempt is given, and how N payloads read as a table.


# --- validation: a typo costs one line, never N paid runs -------------------------------------------


def test_zero_attempts_is_rejected_with_one_friendly_line():
    """AC2: ``--attempts 0`` is a typo, and a typo must not reach Modal."""
    message = mh.attempts_input_error(attempts=0, repo=REPO, sandbox_mode="modal")

    assert message is not None
    assert message.startswith("Decode: ")
    assert "\n" not in message


def test_several_attempts_without_a_repo_are_rejected_with_one_friendly_line():
    """AC2: attempts are compared as branches, and a run without a repo ships no branch."""
    message = mh.attempts_input_error(attempts=3, repo=None, sandbox_mode="modal")

    assert message is not None
    assert message.startswith("Decode: ")
    assert "\n" not in message
    assert "--repo" in message


def test_one_attempt_without_a_repo_is_legal():
    """A single fire-and-forget run has nothing to compare, so it needs nothing to compare against."""
    assert mh.attempts_input_error(attempts=1, repo=None, sandbox_mode="none") is None


def test_a_valid_fan_out_passes_validation():
    assert mh.attempts_input_error(attempts=3, repo=REPO, sandbox_mode="modal") is None


def test_the_fan_out_rejects_docker_with_the_same_line_as_the_single_run():
    """One mode guard, not two: docker is impossible on Modal however the run is launched."""
    assert mh.attempts_input_error(attempts=3, repo=REPO, sandbox_mode="docker") == (
        mh.DOCKER_MODE_MESSAGE
    )


def test_a_repo_under_none_mode_warns_that_nothing_will_ship():
    """ADR-0020 §3: none has no Hand-back — N answers, zero branches. Warn BEFORE spending on N runs."""
    warning = mh.attempts_input_warning(repo=REPO, sandbox_mode="none")

    assert warning is not None
    assert warning.startswith("Decode: ")
    assert "modal" in warning


def test_a_repo_under_modal_mode_warns_about_nothing():
    assert mh.attempts_input_warning(repo=REPO, sandbox_mode="modal") is None


def test_no_repo_warns_about_nothing():
    assert mh.attempts_input_warning(repo=None, sandbox_mode="none") is None


# --- the task text every attempt is given ----------------------------------------------------------


def test_every_attempt_carries_the_push_ban_paragraph_verbatim():
    """AC3: the Hand-back is the ONLY ship path, so every attempt lands as decode/<session-id>.

    A model that pushes its own branch names it itself (and sometimes forgets), and the attempts stop
    being comparable — the exact lesson the retired demo script encoded.
    """
    text = mh.attempt_task("refactor the parser")

    assert text.startswith("refactor the parser")
    assert text.endswith(
        "Commit your work when you are done. Do NOT push and do NOT open a pull request."
    )
    assert "\n\n" in text  # its own paragraph, not glued to the operator's last sentence


def test_the_operators_own_trailing_whitespace_does_not_double_the_blank_line():
    assert "\n\n\n" not in mh.attempt_task("refactor the parser\n\n")


# --- spawning: N independent calls, no stagger ------------------------------------------------------


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


def test_a_shipped_attempt_renders_its_session_its_branch_and_a_zero_exit():
    row = mh.attempt_row(1, _payload())

    assert SESSION_ID in row
    assert SESSION_BRANCH in row
    assert mh.SHIPPED_STATUS in row
    assert row.startswith("1")


def test_an_attempt_whose_branch_never_reached_origin_is_not_shipped():
    """A branch that exists only in a container that is gone is NOT a shipped attempt (ADR-0016 §4)."""
    row = mh.attempt_row(2, _payload(note=mh.UNPUSHED_BRANCH_NOTE))

    assert mh.NOT_SHIPPED_STATUS in row
    assert mh.SHIPPED_STATUS not in row.replace(mh.NOT_SHIPPED_STATUS, "")


def test_an_attempt_that_shipped_nothing_at_all_is_not_shipped():
    row = mh.attempt_row(2, _payload(session_branch=None, note=""))

    assert mh.NOT_SHIPPED_STATUS in row


def test_a_failed_attempt_renders_as_failed_with_dashes_for_the_ids():
    row = mh.attempt_row(3, mh.failed_attempt_result(RuntimeError("boom"), sandbox_mode="modal"))

    assert mh.FAILED_STATUS in row
    assert SESSION_ID not in row


def test_a_non_zero_exit_keeps_its_code_in_the_row():
    row = mh.attempt_row(4, _payload(exit_code=1, session_branch=None))

    assert "1" in row.split()[-1]


def test_the_table_carries_a_header_and_one_row_per_attempt():
    table = mh.attempts_table([_payload(), _payload(session_branch=None)])

    lines = table.splitlines()
    assert len(lines) == 4  # header + rule + 2 rows
    assert "session" in lines[0]
    assert lines[2].startswith("1")
    assert lines[3].startswith("2")


def test_the_notes_are_listed_under_the_table_not_squeezed_into_it():
    notes = mh.attempts_notes([_payload(), _payload(note=mh.UNPUSHED_BRANCH_NOTE)])

    assert len(notes) == 1
    assert "attempt 2" in notes[0]
    assert mh.UNPUSHED_BRANCH_NOTE in notes[0]


# --- the copy-paste tail ----------------------------------------------------------------------------


def test_the_tail_hands_over_the_ls_remote_and_the_branch_diffs():
    commands = mh.compare_commands(
        [_payload(), _payload(session_branch="decode/aaaaaaaa")], repo=REPO
    )

    joined = "\n".join(commands)
    assert f"git ls-remote {REPO} 'refs/heads/decode/*'" in joined
    assert f"git diff origin/HEAD..origin/{SESSION_BRANCH}" in joined
    assert f"git diff origin/{SESSION_BRANCH}..origin/decode/aaaaaaaa" in joined


def test_the_tail_offers_no_branch_diffs_when_nothing_shipped():
    commands = mh.compare_commands([_payload(session_branch=None)], repo=REPO)

    assert not any("git diff" in line for line in commands)


def test_the_tail_is_empty_without_a_repo():
    assert mh.compare_commands([_payload(session_branch=None)], repo=None) == []


# --- detach: the ids, the log line, and out --------------------------------------------------------


def test_detach_prints_one_function_call_id_per_attempt_and_the_log_line():
    """AC5: fire-and-forget — the operator closes the laptop with the ids to come back to."""
    lines = mh.detach_lines(["fc-001", "fc-002"], repo=REPO)

    joined = "\n".join(lines)
    assert "fc-001" in joined
    assert "fc-002" in joined
    assert "decode remote logs" in joined
    assert f"git ls-remote {REPO} 'refs/heads/decode/*'" in joined


def test_one_surviving_attempt_is_a_successful_fan_out():
    assert mh.attempts_exit_code([_payload(exit_code=1), _payload()]) == 0


def test_max_requests_becomes_decodes_own_flag():
    argv = mh.decode_argv(task=TASK, sandbox_mode="none", max_requests=40)

    assert argv[-2:] == ["--max-requests", "40"]


def test_no_ceiling_means_no_flag():
    assert "--max-requests" not in mh.decode_argv(task=TASK, sandbox_mode="none")


def test_no_cron_env_registers_no_schedule():
    assert mh.nightly_cron({}) is None
    assert mh.nightly_cron({mh.NIGHTLY_CRON_ENV: "   "}) is None


def test_a_cron_env_is_read_back_as_its_crontab_string():
    assert mh.nightly_cron({mh.NIGHTLY_CRON_ENV: " 0 2 * * * "}) == "0 2 * * *"


def test_the_job_env_ships_only_the_nightly_keys_that_are_set():
    env = {
        mh.NIGHTLY_CRON_ENV: "0 2 * * *",
        mh.NIGHTLY_TASK_ENV: "review the TODOs",
        mh.NIGHTLY_REPO_ENV: REPO,
        mh.NIGHTLY_MODEL_ENV: "",
        "GEMINI_API_KEY": "must-not-travel",
    }

    job = mh.nightly_job_env(env)

    assert job == {mh.NIGHTLY_TASK_ENV: "review the TODOs", mh.NIGHTLY_REPO_ENV: REPO}
    assert mh.NIGHTLY_CRON_ENV not in job  # the schedule lives on the Function, not in the env


def test_without_a_cron_the_job_env_is_never_validated():
    assert mh.nightly_config_error({mh.NIGHTLY_SANDBOX_MODE_ENV: "docker"}) is None


def test_a_cron_without_a_task_is_rejected_on_the_laptop():
    error = mh.nightly_config_error({mh.NIGHTLY_CRON_ENV: "0 2 * * *"})

    assert error is not None
    assert error.startswith("Decode:")
    assert mh.NIGHTLY_TASK_ENV in error


def test_a_nightly_docker_mode_is_rejected_with_the_same_line_as_every_surface():
    env = {
        mh.NIGHTLY_CRON_ENV: "0 2 * * *",
        mh.NIGHTLY_TASK_ENV: TASK,
        mh.NIGHTLY_SANDBOX_MODE_ENV: "docker",
    }

    assert mh.nightly_config_error(env) == mh.DOCKER_MODE_MESSAGE


@pytest.mark.parametrize("value", ["0", "-3", "many"])
def test_a_nightly_ceiling_must_be_a_positive_integer(value):
    env = {
        mh.NIGHTLY_CRON_ENV: "0 2 * * *",
        mh.NIGHTLY_TASK_ENV: TASK,
        mh.NIGHTLY_MAX_REQUESTS_ENV: value,
    }

    error = mh.nightly_config_error(env)

    assert error is not None
    assert mh.NIGHTLY_MAX_REQUESTS_ENV in error


def test_a_complete_nightly_config_passes():
    env = {
        mh.NIGHTLY_CRON_ENV: "0 2 * * *",
        mh.NIGHTLY_TASK_ENV: TASK,
        mh.NIGHTLY_REPO_ENV: REPO,
        mh.NIGHTLY_SANDBOX_MODE_ENV: "modal",
        mh.NIGHTLY_MAX_REQUESTS_ENV: "80",
        mh.NIGHTLY_TIMEOUT_ENV: "900",
    }

    assert mh.nightly_config_error(env) is None
    assert mh.nightly_run_kwargs(env) == {
        "task": TASK,
        "repo": REPO,
        "sandbox_mode": "modal",
        "model": None,
        "max_requests": 80,
        "timeout_seconds": 900,
    }


def test_a_deployment_without_a_task_runs_nothing():
    assert mh.nightly_run_kwargs({}) is None


def test_the_nightly_defaults_match_a_bare_main_invocation():
    kwargs = mh.nightly_run_kwargs({mh.NIGHTLY_TASK_ENV: TASK})

    assert kwargs == {
        "task": TASK,
        "repo": None,
        "sandbox_mode": mh.DEFAULT_SANDBOX_MODE,
        "model": None,
        "max_requests": None,
        "timeout_seconds": mh.DEFAULT_TIMEOUT_SECONDS,
    }


def test_a_webhook_body_needs_only_a_task():
    request = mh.WebhookRequest(task=TASK)

    assert mh.webhook_request_error(request) is None
    assert mh.webhook_spawn_kwargs(request) == {
        "task": TASK,
        "repo": None,
        "sandbox_mode": mh.DEFAULT_SANDBOX_MODE,
        "model": None,
        "max_requests": None,
        "timeout_seconds": mh.DEFAULT_TIMEOUT_SECONDS,
    }


def test_an_empty_webhook_task_is_one_friendly_line():
    assert mh.webhook_request_error(mh.WebhookRequest(task="   ")) == mh.WEBHOOK_EMPTY_TASK_MESSAGE


def test_a_webhook_docker_mode_is_rejected_with_the_same_line_as_every_surface():
    request = mh.WebhookRequest(task=TASK, sandbox_mode="docker")

    assert mh.webhook_request_error(request) == mh.DOCKER_MODE_MESSAGE


def test_a_webhook_ceiling_below_one_is_rejected_by_the_schema():
    with pytest.raises(ValueError):
        mh.WebhookRequest(task=TASK, max_requests=0)


def test_the_webhook_answers_with_the_call_id_and_where_to_watch():
    request = mh.WebhookRequest(task=TASK, repo=REPO, sandbox_mode="modal")

    response = mh.webhook_response("fc-123", request)

    assert response["call_id"] == "fc-123"
    assert response["status"] == "spawned"
    assert any(mh.APP_NAME in line for line in response["watch"])
    assert any(REPO in line for line in response["watch"])


def test_a_repo_less_webhook_run_lists_no_branch_to_watch():
    response = mh.webhook_response("fc-123", mh.WebhookRequest(task=TASK))

    assert not any("ls-remote" in line for line in response["watch"])
