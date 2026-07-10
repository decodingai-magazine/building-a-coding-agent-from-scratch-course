"""Host-side unit tests for the git hand-back (``decode.sandbox.handback``, ADR-0012 §8).

Hermetic — **no docker daemon, no remote, no network**: every path drives a real ``git`` against
tiny local repos under ``tmp_path`` (a source repo, a real clone at ``<home>/.decode/sandbox`` — the
Workspace; for the push paths the local source *is* the origin, so pushes land credential-free). The
boundary test is the security crux: every git command runs host-side, never through the
executor/backend seam, and no credential enters a sandbox env.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from decode.sandbox.handback import ShipResult, ship_workspace
from decode.sandbox.workspace import prepare_workspace, seed_skills, workspace_dir


def _git(cwd: Path, *args: str) -> None:
    """Run ``git <args>`` in ``cwd``, raising on a non-zero exit (test setup helper)."""
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _git_out(cwd: Path, *args: str) -> str:
    """Run ``git <args>`` in ``cwd`` and return its stripped stdout (test assertion helper)."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _configure_identity(repo: Path) -> None:
    """Configure a local git identity (never the developer's global config) so commits are hermetic."""
    _git(repo, "config", "user.email", "test@decode.local")
    _git(repo, "config", "user.name", "decode test")
    _git(repo, "config", "commit.gpgsign", "false")


def _make_git_repo(path: Path, *, filename: str = "README.md", content: str = "hello\n") -> Path:
    """Create a local git repo at ``path`` with one committed file; return ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _configure_identity(path)
    (path / filename).write_text(content, encoding="utf-8")
    _git(path, "add", filename)
    _git(path, "commit", "-q", "-m", "initial commit")
    return path


def _seed_decode_skills(home: Path, workspace: Path) -> None:
    """Seed a skill into ``<workspace>/.decode/skills`` exactly as a real sandbox session does.

    Mirrors the executor's ``_ensure_created`` → :func:`seed_skills` step (ADR-0012 §5): the harness's
    ``<home>/.decode/skills`` is copied into the Workspace so skill scripts resolve. That injects
    decode's own ``.decode/`` scaffolding into the user's clone — which the hand-back must NOT ship.
    """
    (home / ".decode" / "skills" / "greet").mkdir(parents=True, exist_ok=True)
    (home / ".decode" / "skills" / "greet" / "SKILL.md").write_text(
        "name: greet\n", encoding="utf-8"
    )
    seed_skills(workspace)


def _clone_workspace(source: Path, home: Path) -> Path:
    """Clone ``source`` into ``<home>/.decode/sandbox`` (the Workspace) and return the Workspace path.

    Uses the real task-082 :func:`prepare_workspace`, so the Workspace has the exact origin +
    ``origin/HEAD`` substrate the hand-back recovers (mirrors production).
    """
    return prepare_workspace(home, repo=str(source))


def _commit_change(repo: Path, *, filename: str, content: str) -> str:
    """Commit a new file in ``repo`` (models the agent committing in the Workspace); return the HEAD."""
    _configure_identity(repo)
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", "workspace change")
    return _git_out(repo, "rev-parse", "HEAD")


def _branch_exists(repo: Path, branch: str) -> bool:
    """True if ``branch`` (a local ref) exists in ``repo``."""
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


# never-lose-results: the local Session Branch exists even when the push fails (AC1)


def test_dirty_workspace_captured_on_local_branch_even_when_push_fails(tmp_path):
    """A dirty Workspace lands on ``decode/<id>`` locally with the uncommitted work — push failing.

    The never-lose-results core (AC1): the local branch + its capture commit are created BEFORE the
    push, so an unreachable/failed push still leaves the results on a local branch. Push failure is
    forced by deleting the origin source (recovery reads only local refs, so it still works).
    """
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    # The model leaves uncommitted work in the Workspace (never committed).
    (workspace / "agent_work.txt").write_text("uncommitted results\n", encoding="utf-8")
    # Make the push impossible (origin source gone) while the local git stays fully intact.
    shutil.rmtree(source)

    result = ship_workspace(home, repo=str(source), session_id="abcd1234-5678-9012")

    assert isinstance(result, ShipResult)
    assert result.pushed is False
    assert result.branch == "decode/abcd1234"
    # The failure message names the branch AND its .decode/sandbox location (never-lose-results).
    assert "decode/abcd1234" in result.message
    assert ".decode/sandbox" in result.message
    # The local branch exists and its committed tree carries the uncommitted work.
    assert _branch_exists(workspace, "decode/abcd1234")
    assert "agent_work.txt" in _git_out(workspace, "ls-tree", "--name-only", "decode/abcd1234")


def test_dirty_workspace_auto_commit_message_names_the_session(tmp_path):
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    (workspace / "left_over.txt").write_text("dirty\n", encoding="utf-8")

    result = ship_workspace(home, repo=str(source), session_id="feedface-0000")

    assert result.branch == "decode/feedface"
    subject = _git_out(workspace, "log", "-1", "--format=%s", "decode/feedface")
    assert subject == "decode session feedface-0000"  # the full id rides in the commit message


# push to a local-path origin lands the branch credential-free (AC3)


def test_push_to_local_origin_lands_the_branch(tmp_path):
    """``git push origin decode/<id>`` lands the branch in the local source repo, credential-free.

    ``--repo <local path>`` → origin is the local source, so the push is a plain filesystem write with
    no credential (the same secrets-never-in-the-sandbox invariant, host-side).
    """
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    _commit_change(workspace, filename="CHANGE.md", content="agent work\n")  # model committed

    result = ship_workspace(home, repo=str(source), session_id="deadbeef-1111")

    assert result.pushed is True
    assert result.branch == "decode/deadbeef"
    # The branch landed in the local source origin (a new ref, so no denyCurrentBranch issue).
    assert _branch_exists(source, "decode/deadbeef")
    assert "CHANGE.md" in _git_out(source, "ls-tree", "--name-only", "decode/deadbeef")


# the model's own commits/branches are preserved, not rewritten (AC2)


def test_model_commits_and_branches_are_preserved(tmp_path):
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    # The model works on its OWN branch and commits there.
    _configure_identity(workspace)
    _git(workspace, "checkout", "-q", "-b", "feature")
    model_head = _commit_change(workspace, filename="FEAT.md", content="feature work\n")

    result = ship_workspace(home, repo=str(source), session_id="cafe0000-2222")

    assert result.branch == "decode/cafe0000"
    # The model's branch still points at its own commit (history preserved, never rewritten) ...
    assert _git_out(workspace, "rev-parse", "feature") == model_head
    # ... and the Session Branch was created AT that HEAD (not a rewrite, not a new orphan commit).
    assert _git_out(workspace, "rev-parse", "decode/cafe0000") == model_head


# an unchanged Workspace ships nothing (AC4)


def test_unchanged_workspace_ships_nothing(tmp_path):
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    _clone_workspace(source, home)  # fresh clone: clean AND HEAD == origin/HEAD

    result = ship_workspace(home, repo=str(source), session_id="11112222-3333")

    assert result.branch is None
    assert result.pushed is False
    assert "unchanged" in result.message.lower()
    # Nothing was pushed to the origin.
    assert not _branch_exists(source, "decode/11112222")


def test_committed_but_only_dirty_matters_for_change_detection(tmp_path):
    """A committed change (HEAD advanced past origin/HEAD) is shipped even with a clean worktree."""
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    _commit_change(
        workspace, filename="ONLY.md", content="committed\n"
    )  # clean worktree, HEAD moved

    result = ship_workspace(home, repo=str(source), session_id="99998888-4444")

    assert result.branch == "decode/99998888"
    assert result.pushed is True


# decode's own seeded .decode/ scaffolding is ignored, never shipped (ADR-0012 §5,8)


def test_seeded_decode_scaffolding_alone_is_unchanged(tmp_path):
    """A session that only seeded ``.decode/skills`` (the model did nothing) counts as UNCHANGED.

    In a real docker/modal session the executor seeds ``.decode/skills`` into the Workspace, which
    would otherwise make ``git status`` perpetually dirty and defeat the unchanged-skip (AC4). The
    hand-back ignores decode's own ``.decode/`` namespace, so a do-nothing session ships nothing.
    """
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    _seed_decode_skills(home, workspace)  # the ONLY change is decode's own seeded scaffolding

    result = ship_workspace(home, repo=str(source), session_id="5eeded00-0000")

    assert result.branch is None  # unchanged — decode's scaffolding is not "work"
    assert result.pushed is False


def test_seeded_decode_scaffolding_is_not_shipped_with_user_work(tmp_path):
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    _seed_decode_skills(home, workspace)  # decode's scaffolding ...
    (workspace / "user_change.py").write_text(
        "print('work')\n", encoding="utf-8"
    )  # ... + real work

    result = ship_workspace(home, repo=str(source), session_id="5eeded00-1111")

    assert result.branch == "decode/5eeded00"
    assert result.pushed is True
    shipped = _git_out(source, "ls-tree", "-r", "--name-only", "decode/5eeded00")
    assert "user_change.py" in shipped  # the user's work is captured ...
    assert ".decode/skills/greet/SKILL.md" not in shipped  # ... decode's scaffolding is NOT shipped


# skip: no repo / not a git repo (AC4, byte-identical none/no-repo)


def test_no_repo_ships_nothing(tmp_path):
    home = tmp_path / "home"
    workspace_dir(home)  # a bare, repo-less Workspace scratch

    result = ship_workspace(home, repo=None, session_id="whatever-5555")

    assert result.branch is None
    assert result.pushed is False


def test_non_git_workspace_ships_nothing(tmp_path):
    home = tmp_path / "home"
    workspace = workspace_dir(home)
    (workspace / "notes.txt").write_text("scratch, no .git\n", encoding="utf-8")

    result = ship_workspace(home, repo="/some/source/that/failed/to/clone", session_id="x-6666")

    assert result.branch is None
    assert result.pushed is False


# re-ship fast-forwards the same deterministic ref


def test_reship_fast_forwards_the_same_branch(tmp_path):
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    (workspace / "step1.txt").write_text("first\n", encoding="utf-8")

    first = ship_workspace(home, repo=str(source), session_id="0badf00d-7777")
    assert first.pushed is True
    first_tip = _git_out(source, "rev-parse", "decode/0badf00d")

    # More work, then a re-ship (a later /ship or exit after a /ship).
    (workspace / "step2.txt").write_text("second\n", encoding="utf-8")
    second = ship_workspace(home, repo=str(source), session_id="0badf00d-7777")

    assert second.branch == "decode/0badf00d"
    assert second.pushed is True
    second_tip = _git_out(source, "rev-parse", "decode/0badf00d")
    # The same ref advanced (fast-forward), carrying both steps' work.
    assert second_tip != first_tip
    tree = _git_out(source, "ls-tree", "--name-only", "-r", "decode/0badf00d")
    assert "step1.txt" in tree and "step2.txt" in tree


# the security crux: all git host-side, never through the executor/backend seam (AC8)


def test_git_runs_host_side_never_through_the_sandbox_seam(mocker, tmp_path):
    """Every git op is a host ``git`` subprocess against the local Workspace — no cred in a sandbox env.

    The 075-style boundary assertion (ADR-0012 §8, the Credential-Proxy invariant): record every
    subprocess call the hand-back makes and assert each is a host ``git`` against ``.decode/sandbox``
    (never ``executor.run`` / ``backend.exec``), that no credential/token is injected into any env, and
    — structurally — that the hand-back module pulls in **no** sandbox executor/backend seam at all.
    """
    source = _make_git_repo(tmp_path / "source")
    home = tmp_path / "home"
    workspace = _clone_workspace(source, home)
    (workspace / "work.txt").write_text("results\n", encoding="utf-8")  # force a full secure+push

    calls: list[tuple[list[str], dict]] = []
    real_run = subprocess.run

    def _record(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return real_run(cmd, **kwargs)

    mocker.patch("decode.sandbox.handback.subprocess.run", side_effect=_record)

    result = ship_workspace(home, repo=str(source), session_id="beefcafe-8888")

    assert result.branch == "decode/beefcafe"
    assert calls, "the hand-back must run git commands"
    for cmd, kwargs in calls:
        # (1) every command is the HOST git binary — not routed through executor.run/backend.exec.
        assert cmd[0] == "git"
        # (2) every command targets the local Workspace via -C (host-side, on .decode/sandbox).
        assert "-C" in cmd and str(workspace) in cmd
        # (3) no credential/token is injected into a sandbox env — git inherits only the ambient env
        #     plus GIT_TERMINAL_PROMPT (the no-hang flag); nothing sandbox-specific is added.
        env = kwargs.get("env")
        if env is not None:
            assert set(env) - set(os.environ) <= {"GIT_TERMINAL_PROMPT"}

    # (4) structural: the hand-back module never imports the sandbox executor/backend seam, so a git
    #     command CANNOT route through it (no cred could ever reach a worker via this path).
    import decode.sandbox.handback as handback_module

    for seam in (
        "SandboxExecutor",
        "SandboxBackend",
        "DockerBackend",
        "ModalBackend",
        "select_executor",
        "active_backend",
    ):
        assert not hasattr(handback_module, seam)
