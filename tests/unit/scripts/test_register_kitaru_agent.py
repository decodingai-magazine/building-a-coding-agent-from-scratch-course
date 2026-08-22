"""The operator script that registers decode's replay Agent Version (ADR-0019 §4, task 137).

Hermetic: nothing here reaches the Kitaru workspace. The script's whole job is to turn one host into
the exact ``kitaru agent version register`` invocation that re-creates decode's run context, so the
tests drive :func:`~scripts.register_kitaru_agent.register_argv` — the pure builder — and assert the
four properties a Kitaru Worker's spawn depends on:

* **No inline prompt.** The registered command is ``decode run`` and nothing else, so the task can
  only arrive through ``KITARU_TASK_INPUTS`` — task 136's input contract.
* **The replay context is docker + a repo clone**, carried by the run spec's own env, so a replayed
  tool call lands in the isolated Workspace instead of the operator's tree.
* **No credential leaves the host.** Provider keys ride the Kitaru Worker's inherited shell env
  (``build_process_env`` layers the run spec ON TOP of ``os.environ``), so the argv carries no
  ``--secret-id`` and no key value.
* **The existing agent is reused** — ``agent version register <agent>``, never ``agent register``,
  which would create a second ``decode`` agent and orphan the recorded sessions.

The last test feeds the built values through kitaru 0.22.2's OWN request builder, so an argv these
tests accept is an argv the server's ``RunSpec`` model accepts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from scripts.register_kitaru_agent import DEFAULT_AGENT, build_run_env, main, register_argv

REPO = Path("/home/op/decode-course")
HOME = Path("/home/op/.decode-kitaru-worker")


def _argv(**overrides) -> list[str]:
    kwargs = {
        "agent": DEFAULT_AGENT,
        "decode_bin": REPO / ".venv/bin/decode",
        "harness_home": HOME,
        "repo": REPO,
        "timeout_seconds": 1800,
    }
    return register_argv(**{**kwargs, **overrides})


def _option(argv: list[str], name: str) -> str:
    """The single value of ``name`` in ``argv``."""
    values = [argv[i + 1] for i, item in enumerate(argv) if item == name]
    assert len(values) == 1, f"expected exactly one {name}, got {values}"
    return values[0]


# --- the command: `decode run`, no inline prompt --------------------------------------------------


def test_the_registered_command_is_decode_run_with_no_inline_prompt():
    """AC2: the prompt is NOT part of the registration — it arrives in KITARU_TASK_INPUTS."""
    assert _option(_argv(), "--command") == f"{REPO}/.venv/bin/decode run"


def test_the_command_is_an_absolute_binary_so_the_worker_needs_no_path_setup():
    assert _option(_argv(), "--command").startswith("/")


# --- the replay context: docker + a repo clone, outside the operator's tree ------------------------


def test_the_run_env_puts_the_workspace_in_docker_over_a_clone_of_this_repo():
    assert build_run_env(repo=REPO) == {
        "SANDBOX_MODE": "docker",
        "SANDBOX_REPO": str(REPO),
        "DECODE_ENV": "local",
    }


def test_every_run_env_entry_is_passed_as_its_own_env_option():
    argv = _argv()

    passed = [argv[i + 1] for i, item in enumerate(argv) if item == "--env"]
    assert passed == ["SANDBOX_MODE=docker", f"SANDBOX_REPO={REPO}", "DECODE_ENV=local"]


def test_the_working_dir_is_the_harness_home_outside_the_repo():
    """Harness Home anchors .decode/sessions AND .decode/sandbox — inside the repo they would land
    in the operator's working tree (ADR-0012 §6)."""
    assert _option(_argv(), "--working-dir") == str(HOME)


def test_a_harness_home_inside_the_repo_is_refused():
    with pytest.raises(ValueError, match="outside"):
        _argv(harness_home=REPO / ".decode/worker")


def test_the_repo_itself_as_harness_home_is_refused():
    with pytest.raises(ValueError, match="outside"):
        _argv(harness_home=REPO)


# --- no credential leaves the host ----------------------------------------------------------------


def test_no_secret_is_attached_to_the_version():
    """Provider keys come from the Kitaru Worker's own shell env, never from a workspace secret."""
    assert "--secret-id" not in _argv()


def test_the_run_env_carries_no_credential_shaped_key():
    for key in build_run_env(repo=REPO):
        assert not key.endswith(("_KEY", "_TOKEN", "_SECRET")), key


# --- the existing agent is reused -----------------------------------------------------------------


def test_the_argv_registers_a_version_of_the_named_agent():
    assert _argv()[:5] == ["kitaru", "agent", "version", "register", "decode"]


def test_a_custom_agent_reference_is_honoured():
    assert _argv(agent="01a02523-1097-77e1-aa74-c64e7593050b")[4] == (
        "01a02523-1097-77e1-aa74-c64e7593050b"
    )


def test_the_process_timeout_is_declared():
    assert _option(_argv(), "--timeout-seconds") == "1800"


# --- the CLI surface ------------------------------------------------------------------------------


def test_dry_run_prints_the_command_and_registers_nothing(mocker, tmp_path):
    """The operator can read (and paste) the exact CLI call before anything is created."""
    spawn = mocker.patch("scripts.register_kitaru_agent.subprocess.run")
    decode_bin = tmp_path / "bin/decode"
    decode_bin.parent.mkdir(parents=True)
    decode_bin.touch()

    result = CliRunner().invoke(
        main,
        [
            "--repo",
            str(tmp_path / "repo"),
            "--harness-home",
            str(tmp_path / "home"),
            "--decode-bin",
            str(decode_bin),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    spawn.assert_not_called()
    assert "kitaru agent version register decode" in result.output
    assert "SANDBOX_MODE=docker" in result.output


def test_a_missing_decode_binary_is_one_friendly_line(tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "--repo",
            str(tmp_path / "repo"),
            "--harness-home",
            str(tmp_path / "home"),
            "--decode-bin",
            str(tmp_path / "nope/decode"),
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    assert "decode" in result.output
    assert "Traceback" not in result.output


def test_the_run_creates_the_harness_home_it_registers(mocker, tmp_path):
    """A working dir the Worker cannot chdir into fails every spawn — so the script makes it."""
    mocker.patch(
        "scripts.register_kitaru_agent.subprocess.run",
        return_value=mocker.Mock(returncode=0),
    )
    decode_bin = tmp_path / "bin/decode"
    decode_bin.parent.mkdir(parents=True)
    decode_bin.touch()
    home = tmp_path / "home"

    result = CliRunner().invoke(
        main,
        [
            "--repo",
            str(tmp_path / "repo"),
            "--harness-home",
            str(home),
            "--decode-bin",
            str(decode_bin),
        ],
    )

    assert result.exit_code == 0, result.output
    assert home.is_dir()


# --- the SDK would accept this ---------------------------------------------------------------------


def test_the_built_spec_validates_against_the_installed_kitaru_run_spec():
    """The offline proof that registration cannot fail validation: kitaru's OWN builder, real DTOs."""
    from kitaru.cli.registration import build_agent_version_request

    argv = _argv()
    request = build_agent_version_request(
        command=_option(argv, "--command"),
        entrypoint=None,
        description=_option(argv, "--description"),
        display_version=None,
        working_dir=_option(argv, "--working-dir"),
        env=[argv[i + 1] for i, item in enumerate(argv) if item == "--env"],
        secret_ids=None,
        timeout_seconds=int(_option(argv, "--timeout-seconds")),
        tools=None,
        mcp_servers=None,
        skills=None,
    )

    assert request.run_spec is not None
    assert request.run_spec.command == f"{REPO}/.venv/bin/decode run"
    assert request.run_spec.working_dir == str(HOME)
    assert request.run_spec.env == build_run_env(repo=REPO)
    assert request.run_spec.secret_ids == []
