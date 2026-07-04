"""Host-side unit tests for the Workspace helpers (``decode.sandbox.workspace``).

Hermetic — **no docker daemon, no remote, no network**. The clone paths drive a real ``git`` against
tiny **local** repos created under ``tmp_path`` (git is a dev/CI dependency already), and the
``--local`` flag wiring plus the failure path are asserted without touching the daemon. The tar
helpers round-trip a nested tree in memory. Mirrors ``src/decode/sandbox/workspace.py`` 1:1.
"""

from __future__ import annotations

import io
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

from decode.config.settings import settings
from decode.sandbox.workspace import (
    extract_tar,
    git_config_pairs,
    prepare_workspace,
    prepare_workspace_or_empty,
    seed_skills,
    tar_dir,
    workspace_dir,
)


def _git(cwd: Path, *args: str) -> None:
    """Run ``git <args>`` in ``cwd``, raising on a non-zero exit (test setup helper)."""
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _git_out(cwd: Path, *args: str) -> str:
    """Run ``git <args>`` in ``cwd`` and return its stripped stdout (test assertion helper)."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


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


def _commit_change(repo: Path, *, filename: str, content: str) -> str:
    """Commit a new file in ``repo`` (models the agent working in the Workspace); return the new HEAD.

    A clone does not inherit the source's *local* git config, so identity + signing are configured
    locally here (never the developer's global config) to keep the commit hermetic.
    """
    _git(repo, "config", "user.email", "test@decode.local")
    _git(repo, "config", "user.name", "decode test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", "workspace change")
    return _git_out(repo, "rev-parse", "HEAD")


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


# --- prepare_workspace: the cloned Workspace is a real repo — the task-083 hand-back substrate --------


def test_cloned_workspace_is_a_real_repo_at_decode_sandbox_with_origin_recoverable(tmp_path):
    """The clone lands at ``.decode/sandbox`` as a REAL repo whose origin + cloned HEAD 083 can recover.

    ADR-0012 §8 substrate: because :func:`prepare_workspace` does a real ``git clone``, the Workspace's
    own git recovers everything the hand-back needs — no sidecar file. The origin remote points at the
    source (where to push), and the cloned HEAD is recoverable so 083 can tell "unchanged vs cloned".
    """
    source = _make_git_repo(tmp_path / "source")
    source_head = _git_out(source, "rev-parse", "HEAD")

    workspace = prepare_workspace(tmp_path / "home", repo=str(source))

    # Host-visible, at the canonical .decode/sandbox path, and a real clone (a working .git).
    assert workspace == workspace_dir(tmp_path / "home")
    assert workspace == (tmp_path / "home" / ".decode" / "sandbox").resolve()
    assert (workspace / ".git").is_dir()
    # (a) origin recoverable → 083 knows where to push.
    assert _git_out(workspace, "remote", "get-url", "origin") == str(source)
    # (b) cloned HEAD recoverable → 083 can tell "unchanged vs cloned HEAD". The remote-tracking ref
    #     stays pinned at the cloned commit even after the agent commits, so it is the durable anchor.
    assert _git_out(workspace, "rev-parse", "HEAD") == source_head
    assert _git_out(workspace, "rev-parse", "origin/HEAD") == source_head


@pytest.mark.parametrize("local", [False, True], ids=["plain-clone", "local-clone"])
def test_origin_head_pins_the_cloned_commit_so_083_detects_workspace_changes(local, tmp_path):
    """``origin/HEAD`` stays pinned at the cloned commit after an in-Workspace commit — 083's signal.

    ADR-0012 §8 substrate the task-083 hand-back keys off: on a FRESH clone ``HEAD == origin/HEAD``
    (the Workspace is unchanged-vs-cloned → nothing to ship, skip the hand-back), and after the agent
    commits inside the Workspace ``HEAD`` moves while ``origin/HEAD`` stays PINNED at the cloned commit
    — so ``HEAD != origin/HEAD`` is the durable "the Workspace changed" signal (a remote-tracking ref
    only moves on fetch/pull, never on a local commit). Both a plain and a ``--local`` clone must set
    the ``refs/remotes/origin/HEAD`` anchor for that comparison to exist; ``--local`` is the specific
    risk (a hardlink clone could omit it), so it is asserted for both.
    """
    source = _make_git_repo(tmp_path / "source")
    cloned_head = _git_out(source, "rev-parse", "HEAD")

    workspace = prepare_workspace(tmp_path / "home", repo=str(source), local=local)

    # The anchor ref exists (the --local risk), and on the fresh clone HEAD == origin/HEAD ⇒ unchanged.
    assert _git_out(workspace, "symbolic-ref", "refs/remotes/origin/HEAD").startswith(
        "refs/remotes/origin/"
    )
    assert _git_out(workspace, "rev-parse", "HEAD") == cloned_head
    assert _git_out(workspace, "rev-parse", "origin/HEAD") == cloned_head

    # The agent commits inside the Workspace → HEAD advances, origin/HEAD stays pinned ⇒ changed.
    worked_head = _commit_change(workspace, filename="CHANGE.md", content="agent work\n")

    assert worked_head != cloned_head
    assert _git_out(workspace, "rev-parse", "HEAD") == worked_head
    assert _git_out(workspace, "rev-parse", "origin/HEAD") == cloned_head  # pinned, not advanced
    assert _git_out(workspace, "rev-parse", "HEAD") != _git_out(
        workspace, "rev-parse", "origin/HEAD"
    )


# --- prepare_workspace_or_empty: the launch-time degrade policy (task 082) -------------------------


def test_prepare_workspace_or_empty_returns_the_clone_and_no_error_on_success(tmp_path):
    source = _make_git_repo(tmp_path / "source")

    workspace, error = prepare_workspace_or_empty(tmp_path / "home", repo=str(source))

    assert error is None
    assert (workspace / "README.md").read_text(encoding="utf-8") == "hello\n"


def test_prepare_workspace_or_empty_degrades_to_empty_on_a_clone_failure(tmp_path):
    """A clone failure never raises — it returns the empty Workspace + the git error text (ADR-0012 §3)."""
    workspace, error = prepare_workspace_or_empty(
        tmp_path / "home", repo=str(tmp_path / "does-not-exist")
    )

    # Degraded, not raised: the caller (REPL / headless) surfaces ``error`` and starts empty.
    assert error is not None
    assert "git clone" in error
    assert workspace == workspace_dir(tmp_path / "home")
    assert list(workspace.iterdir()) == []  # a valid empty scratch


def test_prepare_workspace_or_empty_repo_none_is_the_empty_workspace_no_error(tmp_path):
    workspace, error = prepare_workspace_or_empty(tmp_path, repo=None)

    assert error is None
    assert workspace == workspace_dir(tmp_path)
    assert list(workspace.iterdir()) == []


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


def _one_member_tar(name: str, *, linkname: str | None = None) -> bytes:
    """Forge an in-memory tar holding ONE member — a regular file, or a symlink when ``linkname`` is set.

    A low-level builder for the traversal-guard assertions: it plants a *malicious* member name (a
    ``..`` escape / an absolute path / an absolute symlink) that :func:`tar_dir` — which only packs a
    real on-disk tree — cannot produce, so the ``filter="data"`` containment can be exercised directly.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        if linkname is not None:
            info.type = tarfile.SYMTYPE
            info.linkname = linkname
            tar.addfile(info)
        else:
            payload = b"pwned"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_extract_tar_overwrites_a_read_only_clone_tree(tmp_path):
    """A re-extract OVER a git clone's read-only ``.git`` objects (mode 0444) succeeds (ADR-0012 §5,8).

    The Modal export sweep is ``extract_tar`` over the host ``.decode/sandbox`` — which for a ``--repo``
    session is a real ``git clone`` whose ``.git`` loose objects git writes **read-only** (0444). The
    regression it pins: ``extractall`` aborted with ``PermissionError`` opening such an existing object
    for write, so a Modal ``--repo`` session swept *nothing* and the git hand-back captured no work. The
    swept bytes must land and the object stay readable (git re-derives object modes) so ``.git`` is valid.
    """
    tree = tmp_path / "clone"
    objects = tree / ".git" / "objects" / "ab"
    objects.mkdir(parents=True)
    loose_object = objects / "cdef0123456789"
    loose_object.write_bytes(b"content-addressed-object")
    (tree / "work.txt").write_text("agent work\n", encoding="utf-8")

    data = tar_dir(tree)
    loose_object.chmod(
        0o444
    )  # git writes loose objects read-only — the overwrite must still succeed

    extract_tar(data, tree)  # the sweep OVER the existing read-only clone — must NOT raise

    assert (tree / "work.txt").read_text(encoding="utf-8") == "agent work\n"
    # The content-addressed bytes are intact and the object is readable (a valid .git after the sweep).
    assert loose_object.read_bytes() == b"content-addressed-object"


def test_extract_tar_does_not_chmod_a_symlink_target_escaping_the_destination(tmp_path):
    """The pre-extract writable sweep must NOT follow a symlink OUT of the destination (ADR-0012 §4).

    The read-only-``.git`` fix walks the EXISTING destination (:func:`_make_tree_writable`) so a
    re-extract can overwrite a clone's 0444 loose objects. But ``Path.chmod``/``Path.stat`` FOLLOW
    symlinks (no ``lchmod`` on macOS/Linux) and ``os.walk(followlinks=False)`` still yields a symlink's
    NAME at its level, so a ``--repo`` clone whose working tree holds a symlink escaping the Workspace
    (git stores arbitrary link targets — the "clone + run untrusted code" threat model) could get its
    OUT-OF-TREE host target chmod'd on the host. This pins the containment: the destination walk skips
    symlinks, so the outside file/dir modes are UNCHANGED — while the in-tree read-only regular file is
    still made writable and the extract still succeeds (the read-only-``.git`` fix survives). The
    existing traversal-guard test covers only the INCOMING tar; this covers the destination-walk surface.
    """
    # An "outside" area the destination walk must never reach through a symlink.
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "canary.txt"
    outside_file.write_text("do not touch\n", encoding="utf-8")
    outside_file.chmod(0o444)
    outside_dir = outside / "canary_dir"
    outside_dir.mkdir()
    outside_dir.chmod(0o500)
    before_file_mode = stat.S_IMODE(outside_file.stat().st_mode)  # 0o444
    before_dir_mode = stat.S_IMODE(outside_dir.stat().st_mode)  # 0o500

    # The destination clone: an in-tree read-only regular file (the .git-object case the fix targets)
    # plus symlinks whose targets ESCAPE the destination.
    dest = tmp_path / "clone"
    dest.mkdir()
    in_tree = dest / "readonly.txt"
    in_tree.write_text("agent work\n", encoding="utf-8")
    in_tree.chmod(0o444)
    (dest / "evil_file").symlink_to(outside_file)
    (dest / "evil_dir").symlink_to(outside_dir, target_is_directory=True)

    # A benign incoming tar (models the swept-back /workspace) — it does NOT carry the escaping symlinks,
    # so the surface under test is purely the DESTINATION walk, not the incoming-tar ``data`` filter.
    source = tmp_path / "src"
    source.mkdir()
    (source / "swept.txt").write_text("from the sandbox\n", encoding="utf-8")

    extract_tar(
        tar_dir(source), dest
    )  # the sweep OVER the destination — must not chmod outside targets

    # Containment held: the out-of-tree targets a symlink named were NOT chmod'd.
    assert stat.S_IMODE(outside_file.stat().st_mode) == before_file_mode
    assert stat.S_IMODE(outside_dir.stat().st_mode) == before_dir_mode
    # The read-only-.git fix still works: the in-tree read-only regular file was made owner-writable ...
    assert in_tree.stat().st_mode & stat.S_IWUSR
    # ... and the extract still landed the swept content.
    assert (dest / "swept.txt").read_text(encoding="utf-8") == "from the sandbox\n"


def test_extract_tar_keeps_the_data_filter_traversal_guard(tmp_path):
    """The read-only-overwrite fix must NOT regress the ``filter="data"`` containment (ADR-0012 §4).

    ``extract_tar`` sanitizes member paths so a malicious archive cannot escape the destination: a
    ``..`` member is rejected and writes nothing into the parent, an absolute path is neutralized to a
    contained relative path (never the real absolute location), and an absolute symlink is rejected.
    Proves the path-traversal security property survived the overwrite change.
    """
    dest = tmp_path / "dest"

    # A ``..`` escape is rejected AND nothing lands in the parent directory.
    with pytest.raises(tarfile.FilterError):
        extract_tar(_one_member_tar("../escaped.txt"), dest)
    assert not (tmp_path / "escaped.txt").exists()

    # An absolute path is neutralized (leading slash stripped → contained), never the real location.
    absolute_target = tmp_path / "outside" / "abs.txt"
    extract_tar(_one_member_tar(str(absolute_target)), dest)
    assert not absolute_target.exists()

    # An absolute symlink is rejected outright.
    with pytest.raises(tarfile.FilterError):
        extract_tar(_one_member_tar("link", linkname="/etc/passwd"), dest)


# --- git_config_pairs: the sandbox git identity (user.name / user.email) ------------------------


def test_git_config_pairs_returns_the_default_decode_identity():
    # Defaults match the hand-back's own capture-commit identity, so git commit works out of the box.
    assert git_config_pairs() == [
        ("user.name", "decode"),
        ("user.email", "decode@localhost"),
    ]


def test_git_config_pairs_reflects_an_override(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_git_user_name", "Ada Lovelace")
    monkeypatch.setattr(settings, "sandbox_git_user_email", "ada@example.com")

    assert git_config_pairs() == [
        ("user.name", "Ada Lovelace"),
        ("user.email", "ada@example.com"),
    ]


def test_git_config_pairs_skips_a_cleared_field(monkeypatch):
    # An empty value is dropped (not configured), so clearing both yields no pairs at all.
    monkeypatch.setattr(settings, "sandbox_git_user_name", "bot")
    monkeypatch.setattr(settings, "sandbox_git_user_email", "")

    assert git_config_pairs() == [("user.name", "bot")]
