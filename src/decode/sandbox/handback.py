"""Git hand-back — ship the Workspace as a ``decode/<session-id>`` Session Branch (ADR-0012 §8).

Every git command runs host-side against the local Workspace — no credential ever enters the
sandbox. The Session Branch is secured locally BEFORE the non-forced push (a dirty worktree is
auto-committed; the model's own commits are preserved, never rewritten), so results survive a
disabled/failed push. Skipped when there is nothing to hand back: no ``--repo``, not a git repo, or
unchanged vs the cloned HEAD. Triggered on REPL exit, the idle-only ``/ship`` command, and headless
``decode run --repo`` completion. Pure, synchronous, free of docker/modal/executor imports (see the
boundary test).
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from decode.sandbox.workspace import workspace_dir

logger = logging.getLogger(__name__)

# Deterministic ``decode/<short-id>`` so a re-ship fast-forwards the SAME ref (ADR-0012 §8).
_BRANCH_PREFIX = "decode/"
_SHORT_ID_LEN = 8

# The remote a clone names its source — the Workspace is always a clone, so this is the push target.
_ORIGIN_REMOTE = "origin"

# decode's seeded scaffolding inside the Workspace — ignored via the local ``.git/info/exclude`` so it
# is never shipped and never makes an unchanged Workspace look "changed" (untracked paths only, AC4).
_DECODE_NAMESPACE = ".decode/"

# Identity for the dirty-worktree capture commit (``git -c`` overrides) so it succeeds regardless of
# host git config; ``commit.gpgsign=false`` avoids a hang on machines with signing forced on.
_CAPTURE_IDENTITY = (
    "-c",
    "user.name=decode",
    "-c",
    "user.email=decode@localhost",
    "-c",
    "commit.gpgsign=false",
)


@dataclass(frozen=True, slots=True)
class ShipResult:
    """The outcome of a hand-back: the Session Branch (if any), whether the push landed, and a message.

    ``branch is None`` means a skip (nothing shipped). ``pushed=False`` with ``branch`` set is the
    never-lose-results state — the local branch exists and ``message`` names it and its location.
    ``message`` is a friendly, credential-free sentence the callers prefix themselves.
    """

    branch: str | None
    pushed: bool
    message: str


def ship_workspace(harness_home: Path, *, repo: str | None, session_id: str) -> ShipResult:
    """Secure the Workspace onto a ``decode/<session-id>`` branch and push it host-side (ADR-0012 §8).

    Skips (``branch=None``) when there is nothing to ship: no ``repo``, no git repo with an origin,
    or unchanged vs the cloned HEAD. Otherwise the branch is secured locally **before** the
    non-forced push (never-lose-results, AC1). Every git command is a host subprocess — no credential
    ever enters the sandbox. A rare git failure on the secure step raises :class:`RuntimeError`
    (callers wrap best-effort so a hand-back failure never blocks exit or a flow).
    """
    workspace = workspace_dir(harness_home)

    if repo is None:
        return ShipResult(
            None, False, "no --repo/SANDBOX_REPO was given, so there is nothing to hand back."
        )
    if not _has_origin(workspace):
        return ShipResult(
            None,
            False,
            "the workspace is not a git repo with an origin remote, so there is nothing to hand back.",
        )
    # Ignore decode's seeded ``.decode/`` scaffolding before change-detection + commit (AC4; §5,8).
    _exclude_decode_namespace(workspace)
    if _is_unchanged(workspace):
        return ShipResult(
            None,
            False,
            "the workspace is unchanged from the cloned HEAD, so there is nothing to hand back.",
        )

    branch = _branch_name(session_id)
    # SECURE before push (AC1): a disabled/failed push still leaves the results on the local branch.
    _secure_session_branch(workspace, branch=branch, session_id=session_id)

    pushed, error = _push(workspace, branch)
    if pushed:
        logger.info("[handback] pushed %s to origin", branch)
        return ShipResult(
            branch, True, f"handed the workspace back on branch {branch} (pushed to origin)."
        )
    logger.warning("[handback] could not push %s: %s", branch, error)
    return ShipResult(
        branch,
        False,
        f"could not push {branch} to origin; the results are safe on the local branch {branch} "
        "in .decode/sandbox — push it yourself when ready.",
    )


def _branch_name(session_id: str) -> str:
    """Deterministic ``decode/<short-id>`` for ``session_id`` — branch-safe chars only, fixed fallback."""
    safe = "".join(c for c in session_id if c.isalnum() or c in "-._")
    short = safe[:_SHORT_ID_LEN] or "session"
    return f"{_BRANCH_PREFIX}{short}"


def _secure_session_branch(workspace: Path, *, branch: str, session_id: str) -> None:
    """Point ``branch`` at HEAD, committing a dirty worktree first (ADR-0012 §8).

    The capture commit is an *addition* under the decode identity — the model's own branches/commits
    are preserved, never rewritten; ``git branch -f`` re-points the ref without checking it out.
    Raises :class:`RuntimeError` on the rare git failure.
    """
    if _is_dirty(workspace):
        _run_git_checked(workspace, "add", "-A")
        _run_git_checked(
            workspace, *_CAPTURE_IDENTITY, "commit", "-m", f"decode session {session_id}"
        )
    _run_git_checked(workspace, "branch", "-f", branch, "HEAD")


def _push(workspace: Path, branch: str) -> tuple[bool, str]:
    """``git push origin <branch>`` host-side, non-forced; return ``(pushed, error)``. Never raises.

    The stderr is for a **log** line only — kept out of the user-facing message, which could
    otherwise leak a credential embedded in a remote URL.
    """
    completed = _run_git(workspace, "push", _ORIGIN_REMOTE, branch)
    if completed.returncode == 0:
        return True, ""
    return False, completed.stderr.strip() or "git push failed"


def _has_origin(workspace: Path) -> bool:
    """True if the Workspace is a git repo with an ``origin`` remote to push to.

    Never returns or logs the URL — a remote URL may embed a credential.
    """
    return _run_git(workspace, "remote", "get-url", _ORIGIN_REMOTE).returncode == 0


def _exclude_decode_namespace(workspace: Path) -> None:
    """Append ``.decode/`` to the Workspace's local ``.git/info/exclude`` (never committed/pushed).

    Keeps decode's seeded scaffolding out of ``git status`` / ``git add -A``; only **untracked**
    paths are ignored, so a ``.decode`` the user genuinely tracks is untouched. Idempotent
    (ADR-0012 §5,8).
    """
    exclude_file = workspace / ".git" / "info" / "exclude"
    existing = exclude_file.read_text(encoding="utf-8") if exclude_file.exists() else ""
    if any(line.strip() == _DECODE_NAMESPACE for line in existing.splitlines()):
        return
    exclude_file.parent.mkdir(parents=True, exist_ok=True)
    with exclude_file.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(f"{_DECODE_NAMESPACE}\n")


def _is_unchanged(workspace: Path) -> bool:
    """True if there is no work to hand back: clean worktree AND ``HEAD == origin/HEAD`` (§8).

    An unresolvable ``origin/HEAD`` is treated as *changed* — ship rather than silently drop results.
    """
    if _is_dirty(workspace):
        return False
    head = _rev_parse(workspace, "HEAD")
    cloned_head = _rev_parse(workspace, f"{_ORIGIN_REMOTE}/HEAD")
    return head is not None and head == cloned_head


def _is_dirty(workspace: Path) -> bool:
    """True if the Workspace worktree has uncommitted changes (``git status --porcelain`` non-empty)."""
    completed = _run_git(workspace, "status", "--porcelain")
    return completed.returncode == 0 and bool(completed.stdout.strip())


def _rev_parse(workspace: Path, ref: str) -> str | None:
    """Resolve ``ref`` to a commit sha in the Workspace, or ``None`` if it does not resolve."""
    completed = _run_git(workspace, "rev-parse", ref)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _run_git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git -C <workspace> <args>`` as a **host** subprocess — never through a sandbox seam (§8).

    The single choke point for the hand-back's git commands: ambient env for the user's credentials
    plus only ``GIT_TERMINAL_PROMPT=0`` (fail fast, never hang). ``check=False`` — callers decide.
    """
    return subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        check=False,
    )


def _run_git_checked(workspace: Path, *args: str) -> str:
    """Run a git command that must succeed; return stripped stdout, raise :class:`RuntimeError` on failure."""
    completed = _run_git(workspace, *args)
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout.strip()
