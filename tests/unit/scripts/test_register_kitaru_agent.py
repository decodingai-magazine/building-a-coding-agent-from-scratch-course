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


# --- agent version 3: the Modal-hosted Worker's run spec (ADR-0020 §5, task 144) -------------------
#
# The SAME script, one flag apart: `--sandbox-mode none` swaps the laptop's docker Workspace for the
# Worker's own gVisor container. Two properties carry the difference:
#
# * **`none` carries NO `SANDBOX_REPO`.** decode's `--repo`-under-`none` guard (ADR-0012 §3) is not
#   relaxed for the Worker, so a repo in the run spec would fail every spawn at pre-flight.
# * **The paths are in-image paths.** Registration runs on the laptop, where `/harness` and the baked
#   console script do not exist — so the local entrypoint check must be skippable.


def test_none_mode_registers_no_sandbox_repo():
    """AC3: decode refuses a repo under SANDBOX_MODE=none — the container IS the isolation."""
    assert build_run_env(repo=REPO, sandbox_mode="none") == {
        "SANDBOX_MODE": "none",
        "DECODE_ENV": "local",
    }


def test_none_mode_argv_passes_exactly_two_env_options():
    argv = _argv(sandbox_mode="none")

    passed = [argv[i + 1] for i, item in enumerate(argv) if item == "--env"]
    assert passed == ["SANDBOX_MODE=none", "DECODE_ENV=local"]


def test_modal_mode_keeps_the_repo_clone():
    """`modal` is the nested-Sandbox variant of the same shape: decode clones the repo natively."""
    assert build_run_env(repo=REPO, sandbox_mode="modal") == {
        "SANDBOX_MODE": "modal",
        "SANDBOX_REPO": str(REPO),
        "DECODE_ENV": "local",
    }


def test_the_docker_default_is_the_shipped_v2_env():
    """The laptop spec is what it was before the flag existed — v2 replays keep working."""
    assert build_run_env(repo=REPO, sandbox_mode="docker") == build_run_env(repo=REPO)


@pytest.mark.parametrize("mode", ["docker", "none", "modal"])
def test_the_description_names_the_mode_it_registers(mode):
    """An operator reading `kitaru agent get decode` must be able to tell the versions apart."""
    assert f"SANDBOX_MODE={mode}" in _option(_argv(sandbox_mode=mode), "--description")


def test_a_harness_home_inside_the_repo_is_refused_in_every_mode():
    """The guard is about where artifacts land, not about which Workspace runs the tools."""
    for mode in ("docker", "none", "modal"):
        with pytest.raises(ValueError, match="outside"):
            _argv(sandbox_mode=mode, harness_home=REPO / ".decode/worker")


def test_the_v3_spec_uses_the_modal_worker_images_own_paths():
    """The registration and the image are one contract: both name the baked venv and /harness.

    The paths are asserted against ``decode.remote.image``'s constants (both Modal apps share that
    image builder), so moving the venv or the Harness Home in the image breaks this test instead of
    breaking every replay the Worker claims.
    """
    from decode.remote.image import DECODE_BIN, HARNESS_HOME

    argv = _argv(
        sandbox_mode="none",
        decode_bin=Path(DECODE_BIN),
        harness_home=Path(HARNESS_HOME),
    )

    assert _option(argv, "--command") == f"{DECODE_BIN} run"
    assert _option(argv, "--working-dir") == HARNESS_HOME


def test_the_none_mode_spec_validates_against_the_installed_kitaru_run_spec():
    """The same offline proof as v2, for the spec the Modal Worker will actually spawn."""
    from kitaru.cli.registration import build_agent_version_request

    argv = _argv(sandbox_mode="none", harness_home=Path("/harness"))
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
    assert request.run_spec.working_dir == "/harness"
    assert "SANDBOX_REPO" not in request.run_spec.env


# --- the CLI surface for v3 ------------------------------------------------------------------------


def test_the_sandbox_mode_flag_rejects_an_unknown_mode(tmp_path):
    """AC1: a typo is click's usage error, not a registered version nobody can run."""
    result = CliRunner().invoke(main, ["--sandbox-mode", "kubernetes", "--dry-run"])

    assert result.exit_code != 0
    assert "kubernetes" in result.output
    assert "Traceback" not in result.output


def test_none_mode_dry_run_prints_an_argv_with_no_sandbox_repo(tmp_path):
    """The reproducibility contract task 145's docs paste: the exact registration argv."""
    result = CliRunner().invoke(
        main,
        [
            "--sandbox-mode",
            "none",
            "--decode-bin",
            "/.uv/.venv/bin/decode",
            "--harness-home",
            "/harness",
            "--skip-bin-check",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "kitaru agent version register decode" in result.output
    assert "SANDBOX_MODE=none" in result.output
    assert "DECODE_ENV=local" in result.output
    assert "SANDBOX_REPO" not in result.output


def test_container_paths_register_with_no_local_decode_binary(mocker, tmp_path):
    """AC4: the operator registers in-image paths from a laptop where they do not exist."""
    spawn = mocker.patch(
        "scripts.register_kitaru_agent.subprocess.run",
        return_value=mocker.Mock(returncode=0),
    )

    result = CliRunner().invoke(
        main,
        [
            "--sandbox-mode",
            "none",
            "--decode-bin",
            "/.uv/.venv/bin/decode",
            "--harness-home",
            str(tmp_path / "home"),
            "--skip-bin-check",
        ],
    )

    assert result.exit_code == 0, result.output
    argv = spawn.call_args.args[0]
    assert _option(argv, "--command") == "/.uv/.venv/bin/decode run"


def test_an_in_image_harness_home_is_registered_verbatim_and_not_created_locally(mocker, tmp_path):
    """`/harness` belongs to the worker image (its build makes it) — creating it here is litter."""
    spawn = mocker.patch(
        "scripts.register_kitaru_agent.subprocess.run",
        return_value=mocker.Mock(returncode=0),
    )
    home = tmp_path / "harness"

    result = CliRunner().invoke(
        main,
        [
            "--sandbox-mode",
            "none",
            "--decode-bin",
            "/.uv/.venv/bin/decode",
            "--harness-home",
            str(home),
            "--skip-bin-check",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not home.exists()
    assert _option(spawn.call_args.args[0], "--working-dir") == str(home)


def test_the_local_binary_check_still_guards_the_default_laptop_run(tmp_path):
    """Without the flag, a missing entrypoint is still caught before anything is registered."""
    result = CliRunner().invoke(
        main,
        [
            "--sandbox-mode",
            "none",
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
    assert "--skip-bin-check" in result.output


def test_a_relative_harness_home_is_refused_under_skip_bin_check():
    """--skip-bin-check never resolves the path, so a relative one would slip past the inside-repo
    guard (resolved repo vs unresolved harness home never match) AND be meaningless to the Worker,
    which chdirs to it in a container it does not share a cwd with."""
    result = CliRunner().invoke(
        main,
        [
            "--sandbox-mode",
            "none",
            "--decode-bin",
            "/.uv/.venv/bin/decode",
            "--harness-home",
            ".decode/rogue-worker",
            "--skip-bin-check",
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    assert "absolute" in result.output
    assert "--harness-home" in result.output
    assert "Traceback" not in result.output
    assert "kitaru agent version register" not in result.output


def test_a_relative_decode_bin_is_refused_under_skip_bin_check():
    """Same reasoning for the entrypoint: an unresolvable relative binary is a spawn that fails on
    the Worker's first task, long after the operator has walked away."""
    result = CliRunner().invoke(
        main,
        [
            "--sandbox-mode",
            "none",
            "--decode-bin",
            ".venv/bin/decode",
            "--harness-home",
            "/harness",
            "--skip-bin-check",
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    assert "absolute" in result.output
    assert "--decode-bin" in result.output
    assert "Traceback" not in result.output


def test_an_absolute_in_repo_harness_home_is_still_refused_under_skip_bin_check(tmp_path):
    """The inside-repo guard is not weakened by the new absolute check — it still fires."""
    repo = tmp_path / "repo"
    repo.mkdir()

    result = CliRunner().invoke(
        main,
        [
            "--sandbox-mode",
            "none",
            "--repo",
            str(repo),
            "--decode-bin",
            "/.uv/.venv/bin/decode",
            "--harness-home",
            str(repo / ".decode/worker"),
            "--skip-bin-check",
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    assert "inside the repo" in result.output
    assert "Traceback" not in result.output


def test_an_absolute_in_image_path_still_registers_untouched(mocker, tmp_path):
    """The container-path property survives the new check: /harness is registered verbatim, never
    stat-ed, resolved or created on the operator's laptop."""
    spawn = mocker.patch(
        "scripts.register_kitaru_agent.subprocess.run",
        return_value=mocker.Mock(returncode=0),
    )

    result = CliRunner().invoke(
        main,
        [
            "--sandbox-mode",
            "none",
            "--decode-bin",
            "/.uv/.venv/bin/decode",
            "--harness-home",
            "/harness",
            "--skip-bin-check",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _option(spawn.call_args.args[0], "--working-dir") == "/harness"
    assert not Path("/harness").exists()


def test_a_relative_harness_home_is_still_resolved_on_the_laptop_path(mocker, tmp_path):
    """Without --skip-bin-check nothing changes: local paths stay relative-friendly, resolved
    against the operator's cwd exactly as the shipped v2 script did."""
    spawn = mocker.patch(
        "scripts.register_kitaru_agent.subprocess.run",
        return_value=mocker.Mock(returncode=0),
    )
    entrypoint = tmp_path / "bin/decode"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.touch()
    runner = CliRunner()

    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        result = runner.invoke(
            main,
            [
                "--repo",
                str(tmp_path / "repo"),
                "--decode-bin",
                str(entrypoint),
                "--harness-home",
                "worker-home",
            ],
        )

    assert result.exit_code == 0, result.output
    assert _option(spawn.call_args.args[0], "--working-dir") == str(
        Path(cwd).resolve() / "worker-home"
    )


def test_skipping_the_bin_check_is_named_in_the_help_with_its_failure_mode():
    """A typo'd in-image path cannot be caught here, so --help must say where it does surface."""
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "--skip-bin-check" in result.output
    assert "--sandbox-mode" in result.output
