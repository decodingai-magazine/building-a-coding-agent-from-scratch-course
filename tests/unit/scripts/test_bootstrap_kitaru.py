"""The operator script that registers decode on ONE Kitaru Server (task 165, ADR-0022 §10).

Hermetic: nothing here reaches a Kitaru Server. Two halves:

* the PURE builders moved out of the deleted ``scripts/register_kitaru_agent.py`` — the four
  properties a Worker's spawn depends on (no inline prompt, the docker + repo replay context, no
  credential, the EXISTING agent reused) plus the offline proof that the built spec validates
  against kitaru's OWN request builder;
* the orchestration, driven with a fake ``kitaru`` runner whose recorded argv IS the assertion:
  a second run must register nothing.

The envelopes the fake answers with are copies of what kitaru 0.26.0 actually returned on a local
OSS deployment (``kitaru login --local``), including the 409 ``conflict`` a duplicate register gives
— which is why this script reads before it writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from scripts.bootstrap_kitaru import (
    DEFAULT_AGENT,
    BootstrapError,
    Row,
    VersionSpec,
    agent_register_argv,
    bootstrap,
    build_run_env,
    desired_versions,
    evaluator_name,
    evaluator_register_argv,
    evaluator_scripts,
    importer_register_argv,
    importer_version_argv,
    main,
    matching_script_version,
    matching_version,
    register_argv,
    render_table,
    script_digest,
)

REPO = Path("/home/op/decode-course")
HOME = Path("/home/op/.decode-kitaru-worker")
SERVER = "http://localhost:8000"


def _argv(**overrides: Any) -> list[str]:
    kwargs: dict[str, Any] = {
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


# --- the command: `decode run`, no inline prompt ---------------------------------------------------


def test_the_registered_command_is_decode_run_with_no_inline_prompt():
    """The prompt is NOT part of the registration — it arrives in KITARU_TASK_INPUTS (task 136)."""
    assert _option(_argv(), "--command") == f"{REPO}/.venv/bin/decode run"


def test_the_command_is_an_absolute_binary_so_the_worker_needs_no_path_setup():
    assert _option(_argv(), "--command").startswith("/")


# --- the replay context ----------------------------------------------------------------------------


def test_the_run_env_puts_the_workspace_in_docker_over_a_clone_of_this_repo():
    assert build_run_env(repo=REPO) == {"SANDBOX_MODE": "docker", "SANDBOX_REPO": str(REPO)}


def test_every_run_env_entry_is_passed_as_its_own_env_option():
    argv = _argv()

    assert [argv[i + 1] for i, item in enumerate(argv) if item == "--env"] == [
        "SANDBOX_MODE=docker",
        f"SANDBOX_REPO={REPO}",
    ]


def test_the_working_dir_is_the_harness_home_outside_the_repo():
    assert _option(_argv(), "--working-dir") == str(HOME)


def test_a_harness_home_inside_the_repo_is_refused():
    with pytest.raises(ValueError, match="inside the repo"):
        _argv(harness_home=REPO / ".decode/worker")


def test_the_repo_itself_as_harness_home_is_refused():
    with pytest.raises(ValueError, match="inside the repo"):
        _argv(harness_home=REPO)


@pytest.mark.parametrize("mode", ["docker", "none", "modal"])
def test_a_harness_home_inside_the_repo_is_refused_in_every_mode(mode: str):
    with pytest.raises(ValueError, match="inside the repo"):
        _argv(harness_home=REPO / "sub", sandbox_mode=mode)


def test_none_mode_registers_no_sandbox_repo():
    """decode refuses a repo with no sandbox to clone it into (ADR-0012 §3)."""
    assert build_run_env(repo=REPO, sandbox_mode="none") == {"SANDBOX_MODE": "none"}


def test_none_mode_argv_passes_exactly_one_env_option():
    argv = _argv(sandbox_mode="none", harness_home=Path("/harness"))

    assert [item for item in argv if item == "--env"] == ["--env"]
    assert "SANDBOX_REPO" not in " ".join(argv)


def test_modal_mode_keeps_the_repo_clone():
    assert build_run_env(repo=REPO, sandbox_mode="modal")["SANDBOX_REPO"] == str(REPO)


@pytest.mark.parametrize("mode", ["docker", "none", "modal"])
def test_the_description_names_the_mode_it_registers(mode: str):
    argv = _argv(sandbox_mode=mode, harness_home=Path("/harness"))

    assert f"SANDBOX_MODE={mode}" in _option(argv, "--description")


# --- no credential ever leaves the host ------------------------------------------------------------


def test_no_secret_is_attached_to_the_version():
    """Provider keys ride the Worker's inherited shell env (``build_process_env``), not the spec."""
    assert "--secret-id" not in _argv()


def test_the_run_env_carries_no_credential_shaped_key():
    env = build_run_env(repo=REPO)

    assert not [
        key for key in env if any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET"))
    ]


# --- the agent is reused, never forked -------------------------------------------------------------


def test_the_argv_registers_a_version_of_the_named_agent():
    assert _argv()[:5] == ["kitaru", "agent", "version", "register", DEFAULT_AGENT]


def test_a_custom_agent_reference_is_honoured():
    assert _argv(agent="decode-staging")[4] == "decode-staging"


def test_the_process_timeout_is_declared():
    assert _option(_argv(timeout_seconds=60), "--timeout-seconds") == "60"


def test_the_server_is_named_explicitly_on_every_argv():
    """`--server` beats both KITARU_API_URL and a stale login store (kitaru 0.26 resolution)."""
    assert _option(_argv(server=SERVER), "--server") == SERVER


def test_the_first_registration_creates_the_agent_and_its_first_version():
    argv = agent_register_argv(
        agent=DEFAULT_AGENT,
        spec=VersionSpec(
            sandbox_mode="docker",
            decode_bin=REPO / ".venv/bin/decode",
            harness_home=HOME,
            repo=REPO,
        ),
        server=SERVER,
    )

    assert argv[:4] == ["kitaru", "agent", "register", DEFAULT_AGENT]
    assert _option(argv, "--command") == f"{REPO}/.venv/bin/decode run"
    assert _option(argv, "--working-dir") == str(HOME)


# --- the built spec is one kitaru itself accepts ---------------------------------------------------


@pytest.mark.parametrize("mode", ["docker", "none"])
def test_the_built_spec_validates_against_the_installed_kitaru_run_spec(mode: str):
    """The offline proof: an argv these tests accept is one the server's ``RunSpec`` accepts."""
    from kitaru.cli.registration import build_agent_version_request

    argv = _argv(sandbox_mode=mode, harness_home=Path("/harness"))
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
    assert ("SANDBOX_REPO" in request.run_spec.env) is (mode != "none")


def test_the_modal_worker_version_uses_the_images_own_paths():
    """The registration and the image are one contract: the baked venv and /harness."""
    from decode.remote.image import DECODE_BIN, HARNESS_HOME

    specs = desired_versions(repo=REPO, decode_bin=REPO / ".venv/bin/decode", harness_home=HOME)

    assert [spec.sandbox_mode for spec in specs] == ["docker", "none"]
    assert specs[1].decode_bin == Path(DECODE_BIN)
    assert specs[1].harness_home == Path(HARNESS_HOME)


# --- what "already registered" means ---------------------------------------------------------------


DOCKER_SPEC = VersionSpec(
    sandbox_mode="docker", decode_bin=REPO / ".venv/bin/decode", harness_home=HOME, repo=REPO
)


def registered_version(number: int = 1) -> dict[str, Any]:
    """A version as ``kitaru agent version list`` actually returns it (server defaults included)."""
    return {
        "id": f"ver-{number}",
        "version": number,
        "display_version": None,
        "run_spec": {
            "command": f"{REPO}/.venv/bin/decode run",
            "working_dir": str(HOME),
            "env": {"SANDBOX_MODE": "docker", "SANDBOX_REPO": str(REPO)},
            "secret_ids": [],
            "hooks": [],
            "runtime_capabilities": {"overrides": True, "tool_policies": True},
            "timeout_seconds": 1800,
        },
    }


def test_an_identical_run_spec_is_recognised_through_the_servers_own_defaults():
    assert matching_version([registered_version()], DOCKER_SPEC) == registered_version()


def test_a_moved_venv_is_not_the_same_version():
    moved = VersionSpec(
        sandbox_mode="docker",
        decode_bin=Path("/elsewhere/decode"),
        harness_home=HOME,
        repo=REPO,
    )

    assert matching_version([registered_version()], moved) is None


def test_a_different_sandbox_mode_is_not_the_same_version():
    other = VersionSpec(
        sandbox_mode="none", decode_bin=REPO / ".venv/bin/decode", harness_home=HOME, repo=REPO
    )

    assert matching_version([registered_version()], other) is None


def test_a_script_is_matched_on_the_digest_of_the_bytes_it_would_upload(tmp_path):
    script = tmp_path / "evaluator.py"
    script.write_text("def evaluate(session): ...\n")

    versions = [{"version": 2, "display_version": script_digest(script)}]

    assert matching_script_version(versions, script) == versions[0]


def test_an_edited_script_no_longer_matches_its_registered_version(tmp_path):
    script = tmp_path / "evaluator.py"
    script.write_text("def evaluate(session): ...\n")
    versions = [{"version": 2, "display_version": script_digest(script)}]
    script.write_text("def evaluate(session): return None\n")

    assert matching_script_version(versions, script) is None


def test_the_digest_rides_in_the_registration_argv(tmp_path):
    script = tmp_path / "opik_importer.py"
    script.write_text("parser = None\n")

    argv = importer_register_argv(
        name="opik", script=script, entrypoint="parse", provider="opik", server=SERVER
    )

    assert _option(argv, "--display-version") == script_digest(script)
    assert _option(argv, "--provider") == "opik"
    assert _option(argv, "--entrypoint") == "parse"


def test_a_new_importer_version_carries_the_script_but_not_the_provider(tmp_path):
    """`importer version register` has no --provider: the provider belongs to the parent."""
    script = tmp_path / "opik_importer.py"
    script.write_text("parser = None\n")

    argv = importer_version_argv(name="opik", script=script, entrypoint="parse", server=SERVER)

    assert argv[:5] == ["kitaru", "importer", "version", "register", "opik"]
    assert "--provider" not in argv


def test_every_evaluator_in_the_repo_is_registered_under_its_file_name():
    scripts = evaluator_scripts(Path("evaluators"))

    assert Path("evaluators/decode_bad_request_400.py") in scripts
    assert evaluator_name(Path("evaluators/decode_bad_request_400.py")) == "decode-bad-request-400"


def test_dunder_files_are_not_evaluators(tmp_path):
    (tmp_path / "__init__.py").write_text("")
    (tmp_path / "real_check.py").write_text("def evaluate(): ...")

    assert evaluator_scripts(tmp_path) == [tmp_path / "real_check.py"]


def test_an_evaluator_registers_with_kitarus_evaluate_entrypoint(tmp_path):
    script = tmp_path / "my_check.py"
    script.write_text("def evaluate(session): ...")

    argv = evaluator_register_argv(name="my-check", script=script, server=SERVER)

    assert argv[:4] == ["kitaru", "evaluator", "register", "my-check"]
    assert _option(argv, "--entrypoint") == "evaluate"


# --- the one subprocess helper ---------------------------------------------------------------------


def test_a_failing_kitaru_call_is_read_off_stderr(mocker):
    """Regression (live, kitaru 0.26): a FAILING sub-command writes its envelope to STDERR.

    Parsing stdout alone made every "not found" read as a broken CLI, which a read-then-register
    script cannot tell apart from an unreachable server.
    """
    import subprocess as sp

    from scripts import bootstrap_kitaru

    envelope = '{"ok": false, "error": {"kind": "not_found", "message": "Agent was not found."}}'
    mocker.patch.object(
        sp,
        "run",
        lambda argv, **kwargs: sp.CompletedProcess(argv, 4, stdout="", stderr=envelope),
    )

    with pytest.raises(BootstrapError, match="Agent was not found"):
        bootstrap_kitaru.run_kitaru(["kitaru", "agent", "get", "decode"])


def test_a_kitaru_that_says_nothing_at_all_is_still_one_line(mocker):
    import subprocess as sp

    from scripts import bootstrap_kitaru

    mocker.patch.object(
        sp,
        "run",
        lambda argv, **kwargs: sp.CompletedProcess(argv, 1, stdout="", stderr="connection refused"),
    )

    with pytest.raises(BootstrapError, match="connection refused"):
        bootstrap_kitaru.run_kitaru(["kitaru", "agent", "get", "decode"])


# --- the orchestration -----------------------------------------------------------------------------


class FakeKitaru:
    """A ``kitaru`` runner over an in-memory server: reads answer state, writes mutate it."""

    def __init__(
        self, *, agent: bool = False, versions: list[dict[str, Any]] | None = None
    ) -> None:
        self.agent = agent
        self.versions = list(versions or [])
        self.scripts: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> dict[str, Any]:
        self.calls.append(list(argv))
        route = tuple(argv[1:4])
        if route[:2] == ("agent", "get"):
            if not self.agent:
                raise BootstrapError("Agent 'decode' was not found.")
            return {"ok": True, "item": {"id": "agent-1", "latest_version": len(self.versions)}}
        if route == ("agent", "version", "list"):
            return {"ok": True, "items": self.versions}
        if route[:2] == ("agent", "register"):
            self.agent = True
            version = self._add_version(argv)
            return {"ok": True, "item": {"agent": {"id": "agent-1"}, "version": version}}
        if route == ("agent", "version", "register"):
            return {"ok": True, "item": {"version": self._add_version(argv)}}
        if route[:2] in (("importer", "get"), ("evaluator", "get")):
            name = argv[3]
            if name not in self.scripts:
                raise BootstrapError(f"{route[0]} {name!r} was not found.")
            return {"ok": True, "item": {"id": f"{name}-id"}}
        if route in (("importer", "version", "list"), ("evaluator", "version", "list")):
            return {"ok": True, "items": self.scripts.get(argv[4], [])}
        if route[:2] in (("importer", "register"), ("evaluator", "register")):
            return {"ok": True, "item": {"version": self._add_script(argv[3], argv)}}
        if route in (("importer", "version", "register"), ("evaluator", "version", "register")):
            return {"ok": True, "item": {"version": self._add_script(argv[4], argv)}}
        raise AssertionError(f"unexpected kitaru call: {argv}")

    def _add_version(self, argv: list[str]) -> dict[str, Any]:
        env = dict(
            entry.split("=", 1)
            for entry in (argv[i + 1] for i, x in enumerate(argv) if x == "--env")
        )
        version = {
            "id": f"ver-{len(self.versions) + 1}",
            "version": len(self.versions) + 1,
            "run_spec": {
                "command": _option(argv, "--command"),
                "working_dir": _option(argv, "--working-dir"),
                "env": env,
                "timeout_seconds": int(_option(argv, "--timeout-seconds")),
            },
        }
        self.versions.append(version)
        return version

    def _add_script(self, name: str, argv: list[str]) -> dict[str, Any]:
        versions = self.scripts.setdefault(name, [])
        version = {
            "id": f"{name}-v{len(versions) + 1}",
            "version": len(versions) + 1,
            "display_version": _option(argv, "--display-version"),
        }
        versions.append(version)
        return version

    def writes(self) -> list[list[str]]:
        return [call for call in self.calls if "register" in call[:4] or "create" in call[:4]]


@pytest.fixture
def repo_tree(tmp_path: Path) -> Path:
    """A repo-shaped tree with one evaluator and the importer script."""
    (tmp_path / "importers").mkdir()
    (tmp_path / "importers" / "opik_importer.py").write_text("parser = None\n")
    (tmp_path / "evaluators").mkdir()
    (tmp_path / "evaluators" / "decode_bad_request_400.py").write_text("def evaluate(s): ...\n")
    return tmp_path


def _bootstrap(fake: FakeKitaru, repo_tree: Path, **overrides: Any) -> list[Row]:
    kwargs: dict[str, Any] = {
        "runner": fake,
        "repo": REPO,
        "decode_bin": REPO / ".venv/bin/decode",
        "harness_home": HOME,
        "server": SERVER,
        "evaluators_root": repo_tree / "evaluators",
        "importer_script": repo_tree / "importers" / "opik_importer.py",
    }
    return bootstrap(**{**kwargs, **overrides})


def test_a_fresh_server_gets_the_agent_two_versions_the_importer_and_every_evaluator(repo_tree):
    fake = FakeKitaru()

    rows = _bootstrap(fake, repo_tree)

    kinds = [row.kind for row in rows]
    assert kinds == [
        "agent",
        "agent version (docker)",
        "agent version (none)",
        "importer",
        "evaluator",
    ]
    assert [row.action for row in rows] == [
        "created",
        "registered",
        "registered",
        "registered",
        "registered",
    ]
    assert [row.ref for row in rows][1:3] == ["decode@1", "decode@2"]


def test_rerunning_registers_nothing_and_prints_the_same_table(repo_tree):
    fake = FakeKitaru()
    first = _bootstrap(fake, repo_tree)
    writes_after_first = len(fake.writes())

    second = _bootstrap(fake, repo_tree)

    assert len(fake.writes()) == writes_after_first
    assert [row.action for row in second] == ["exists"] * len(second)
    assert [row.ref for row in second] == [row.ref for row in first]


def test_an_existing_agent_is_reused_never_re_created(repo_tree):
    """`agent register` on a duplicate is a 409 — and would fork the sessions if it were not."""
    fake = FakeKitaru(agent=True)

    _bootstrap(fake, repo_tree)

    assert not [call for call in fake.calls if call[1:3] == ["agent", "register"]]


def test_a_moved_venv_registers_exactly_one_new_agent_version(repo_tree):
    fake = FakeKitaru()
    _bootstrap(fake, repo_tree)
    before = len(fake.writes())

    rows = _bootstrap(fake, repo_tree, decode_bin=Path("/new/venv/bin/decode"))

    assert len(fake.writes()) == before + 1
    assert [row.action for row in rows if row.kind.startswith("agent version")] == [
        "registered",
        "exists",
    ]


def test_an_edited_evaluator_registers_one_new_version(repo_tree):
    fake = FakeKitaru()
    _bootstrap(fake, repo_tree)
    before = len(fake.writes())
    (repo_tree / "evaluators" / "decode_bad_request_400.py").write_text(
        "def evaluate(s): return 1\n"
    )

    rows = _bootstrap(fake, repo_tree)

    assert len(fake.writes()) == before + 1
    assert [row.ref for row in rows if row.kind == "evaluator"] == ["decode-bad-request-400@2"]


def test_dry_run_prints_the_argv_and_registers_nothing(repo_tree, capsys):
    fake = FakeKitaru()
    printed: list[str] = []

    _bootstrap(fake, repo_tree, dry_run=True, echo=printed.append)

    assert fake.writes() == []
    assert any(line.startswith("kitaru agent register decode") for line in printed)
    assert any("--server http://localhost:8000" in line for line in printed)
    assert any("importer register opik" in line for line in printed)


def test_dry_run_against_an_unreachable_server_still_shows_the_full_bootstrap(repo_tree):
    """A read that fails is treated as "nothing registered yet", never as a crash."""

    def unreachable(argv: list[str]) -> dict[str, Any]:
        raise BootstrapError("connection refused")

    printed: list[str] = []

    rows = _bootstrap(unreachable, repo_tree, dry_run=True, echo=printed.append)

    assert len(rows) == 5
    assert any("agent register decode" in line for line in printed)


def test_a_missing_importer_script_is_one_friendly_error(repo_tree):
    fake = FakeKitaru()

    with pytest.raises(BootstrapError, match="no importer script"):
        _bootstrap(fake, repo_tree, importer_script=repo_tree / "importers" / "gone.py")


def test_the_table_carries_kind_reference_and_id(repo_tree):
    fake = FakeKitaru()

    table = render_table(_bootstrap(fake, repo_tree))

    assert "KIND" in table and "NAME@VERSION" in table and "ID" in table
    assert "decode@1" in table
    assert "decode-bad-request-400@1" in table


# --- the CLI surface -------------------------------------------------------------------------------


def test_the_cli_refuses_a_missing_decode_binary(tmp_path):
    result = CliRunner().invoke(main, ["--repo", str(tmp_path), "--dry-run"])

    assert result.exit_code != 0
    assert "no decode entrypoint" in result.output


def test_the_cli_prints_the_agent_id_to_export(mocker, tmp_path):
    binary = tmp_path / "decode"
    binary.write_text("#!/bin/sh\n")
    mocker.patch(
        "scripts.bootstrap_kitaru.bootstrap",
        return_value=[Row(kind="agent", ref="decode", id="agent-uuid", action="exists")],
    )

    result = CliRunner().invoke(
        main,
        [
            "--decode-bin",
            str(binary),
            "--harness-home",
            str(tmp_path / "home"),
            "--server",
            SERVER,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "KITARU_AGENT_ID=agent-uuid" in result.output
    assert SERVER in result.output


def test_the_cli_targets_the_exported_server_when_no_flag_is_passed(mocker, tmp_path, monkeypatch):
    binary = tmp_path / "decode"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("KITARU_API_URL", "https://example.invalid")
    captured: dict[str, Any] = {}

    def fake_bootstrap(**kwargs: Any) -> list[Row]:
        captured.update(kwargs)
        return []

    mocker.patch("scripts.bootstrap_kitaru.bootstrap", fake_bootstrap)

    result = CliRunner().invoke(
        main, ["--decode-bin", str(binary), "--harness-home", str(tmp_path / "home")]
    )

    assert result.exit_code == 0, result.output
    assert captured["server"] == "https://example.invalid"


def test_the_cli_creates_the_harness_home_it_registers(mocker, tmp_path):
    binary = tmp_path / "decode"
    binary.write_text("#!/bin/sh\n")
    home = tmp_path / "worker-home"
    mocker.patch("scripts.bootstrap_kitaru.bootstrap", return_value=[])

    result = CliRunner().invoke(
        main, ["--decode-bin", str(binary), "--harness-home", str(home), "--server", SERVER]
    )

    assert result.exit_code == 0, result.output
    assert home.is_dir()
