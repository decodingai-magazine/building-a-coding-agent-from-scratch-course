"""Host-side unit tests for the Workspace helpers (``decode.sandbox.workspace``).

Hermetic — **no docker daemon, no remote, no network**. The clone paths drive a real ``git`` against
tiny **local** repos created under ``tmp_path`` (git is a dev/CI dependency already), and the
``--local`` flag wiring plus the failure path are asserted without touching the daemon. The tar
helpers round-trip a nested tree in memory. Mirrors ``src/decode/sandbox/workspace.py`` 1:1.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from decode.sandbox.workspace import (
    extract_tar,
    prepare_workspace,
    seed_skills,
    tar_dir,
    workspace_dir,
)


def _git(cwd: Path, *args: str) -> None:
    """Run ``git <args>`` in ``cwd``, raising on a non-zero exit (test setup helper)."""
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _make_git_repo(path: Path, *, filename: str = "README.md", content: str = "hello\n") -> Path:
    """Create a local git repo at ``path`` with one committed file; return ``path``.

    Identity + gpg signing are configured **locally** (never touching the developer's global config)
    so ``git commit`` succeeds hermetically regardless of the host's git setup.
    """
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@decode.local")
    _git(path, "config", "user.name", "decode test")
    _git(path, "config", "commit.gpgsign", "false")
    (path / filename).write_text(content, encoding="utf-8")
    _git(path, "add", filename)
    _git(path, "commit", "-q", "-m", "initial commit")
    return path


# --- workspace_dir --------------------------------------------------------------------------------


def test_workspace_dir_resolves_under_harness_home(tmp_path):
    result = workspace_dir(tmp_path)

    # The single resolver: <harness_home>/.decode/sandbox, resolved and created.
    assert result == (tmp_path / ".decode" / "sandbox").resolve()
    assert result.is_dir()


def test_workspace_dir_creation_is_idempotent(tmp_path):
    first = workspace_dir(tmp_path)
    marker = first / "keep.txt"
    marker.write_text("stay", encoding="utf-8")

    second = workspace_dir(tmp_path)  # a second call must not error nor wipe the directory

    assert second == first
    assert marker.read_text(encoding="utf-8") == "stay"


# --- prepare_workspace: clone / empty / reuse -----------------------------------------------------


def test_prepare_workspace_clones_committed_head_into_empty_workspace(tmp_path):
    source = _make_git_repo(tmp_path / "source")

    workspace = prepare_workspace(tmp_path / "home", repo=str(source))

    # The committed HEAD tree lands in the Workspace, with a real .git.
    assert (workspace / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert (workspace / ".git").is_dir()


def test_prepare_workspace_repo_none_leaves_the_workspace_empty(tmp_path):
    workspace = prepare_workspace(tmp_path, repo=None)

    assert workspace == workspace_dir(tmp_path)
    assert list(workspace.iterdir()) == []


def test_prepare_workspace_reuses_a_non_empty_workspace(tmp_path):
    source = _make_git_repo(tmp_path / "source")
    workspace = workspace_dir(tmp_path / "home")
    marker = workspace / "marker.txt"
    marker.write_text("pre-existing", encoding="utf-8")

    result = prepare_workspace(tmp_path / "home", repo=str(source))

    # Reused, never re-cloned: the marker survives and no .git was cloned over it.
    assert result == workspace
    assert marker.read_text(encoding="utf-8") == "pre-existing"
    assert not (workspace / ".git").exists()
    assert not (workspace / "README.md").exists()


def test_prepare_workspace_local_clone_works(tmp_path):
    source = _make_git_repo(tmp_path / "source")

    workspace = prepare_workspace(tmp_path / "home", repo=str(source), local=True)

    # A real, functional local clone: the committed file + a real .git are present.
    assert (workspace / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert (workspace / ".git").is_dir()


def test_prepare_workspace_raises_on_clone_failure(tmp_path):
    # A non-existent local source fails fast (GIT_TERMINAL_PROMPT=0 → no hang) → RuntimeError.
    with pytest.raises(RuntimeError, match="git clone"):
        prepare_workspace(tmp_path / "home", repo=str(tmp_path / "does-not-exist"))


# --- prepare_workspace: git argv wiring (hermetic — subprocess.run patched) ------------------------


def _capture_git(mocker) -> dict[str, object]:
    """Patch ``workspace.subprocess.run`` to record the argv/env and report success; return the store."""
    captured: dict[str, object] = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    mocker.patch("decode.sandbox.workspace.subprocess.run", side_effect=_fake_run)
    return captured


def test_prepare_workspace_passes_local_flag_and_disables_prompt(mocker, tmp_path):
    captured = _capture_git(mocker)

    prepare_workspace(tmp_path, repo="/some/local/repo", local=True)

    cmd = captured["cmd"]
    assert cmd[0] == "git" and "clone" in cmd
    assert "--local" in cmd
    # The source and the resolved Workspace are the final two positional args.
    assert cmd[-2] == "/some/local/repo"
    assert cmd[-1] == str(workspace_dir(tmp_path))
    # Ambient creds preserved, but the interactive terminal prompt is disabled (no-hang guarantee).
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_prepare_workspace_omits_local_flag_by_default(mocker, tmp_path):
    captured = _capture_git(mocker)

    prepare_workspace(tmp_path, repo="https://example.test/repo.git")

    assert "--local" not in captured["cmd"]


# --- seed_skills ----------------------------------------------------------------------------------


def test_seed_skills_copies_project_skills_into_the_workspace(tmp_path):
    home = tmp_path
    skill = home / ".decode" / "skills" / "greet"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("name: greet\n", encoding="utf-8")
    (skill / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")
    workspace = workspace_dir(home)

    seed_skills(workspace)

    dest = workspace / ".decode" / "skills" / "greet"
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "name: greet\n"
    assert (dest / "scripts" / "run.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_seed_skills_is_a_noop_when_no_skills_dir(tmp_path):
    workspace = workspace_dir(tmp_path)  # no <home>/.decode/skills exists

    seed_skills(workspace)

    assert not (workspace / ".decode" / "skills").exists()


def test_seed_skills_is_idempotent(tmp_path):
    home = tmp_path
    skill = home / ".decode" / "skills" / "greet"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("body", encoding="utf-8")
    workspace = workspace_dir(home)

    seed_skills(workspace)
    seed_skills(workspace)  # a second seed must merge, not crash (dirs_exist_ok=True)

    assert (workspace / ".decode" / "skills" / "greet" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "body"


# --- tar_dir / extract_tar ------------------------------------------------------------------------


def _file_tree(root: Path) -> list[str]:
    """Sorted POSIX-relative paths of every file under ``root`` (structure fingerprint)."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_tar_dir_and_extract_tar_round_trip_a_nested_tree(tmp_path):
    source = tmp_path / "src"
    (source / "sub" / "deep").mkdir(parents=True)
    (source / "top.txt").write_text("top\n", encoding="utf-8")
    (source / "sub" / "mid.txt").write_text("mid\n", encoding="utf-8")
    (source / "sub" / "deep" / "leaf.bin").write_bytes(b"\x00\x01\x02\xff")

    data = tar_dir(source)
    dest = tmp_path / "dest"  # does not exist yet — extract_tar must create it
    extract_tar(data, dest)

    assert isinstance(data, bytes)
    # Same structure ...
    assert _file_tree(dest) == _file_tree(source)
    # ... and byte-for-byte contents, including non-UTF-8 bytes.
    assert (dest / "top.txt").read_text(encoding="utf-8") == "top\n"
    assert (dest / "sub" / "mid.txt").read_text(encoding="utf-8") == "mid\n"
    assert (dest / "sub" / "deep" / "leaf.bin").read_bytes() == b"\x00\x01\x02\xff"
