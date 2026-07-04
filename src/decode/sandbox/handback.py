"""Git hand-back — ship the Workspace as a ``decode/<session-id>`` Session Branch, host-side (ADR-0012 §8).

The built promise of the isolated Workspace: **the results of a sandbox session are guaranteed to
survive** as a new branch pushed back to the repo the user supplied. This module owns that hand-back,
and it does so with **every git command running host-side against the local Workspace git** — so *no
credential ever enters the sandbox*, the identical secrets-never-in-the-sandbox invariant the
Credential Proxy (ADR-0012 §9) upholds. It is a pure, synchronous, host-side helper kept deliberately
free of any docker/modal/executor import (see the boundary test), so it composes with either backend
and unit-tests hermetically against plain local git repos — no daemon, no remote, no network.

:func:`ship_workspace` runs the three-step hand-back over the local Workspace at
``<harness_home>/.decode/sandbox`` (task 082 clones a real repo there, so its own git recovers
everything needed — no sidecar file):

1. **Collect** — the local Workspace git state. Docker's bind mount is already live there; a modal
   session's ``/workspace`` is swept down to the host by the executor ``export()`` first, which the
   callers run before this (REPL exit / headless ``finally`` via ``close_executor``; ``/ship`` via
   ``export_executor``). The push origin (the ``origin`` remote) and the cloned HEAD
   (``origin/HEAD`` — a remote-tracking ref that stays pinned at the clone commit even after the agent
   commits, task 082's regression proves it) are recovered straight from that git.
2. **Secure** — the model is **not** trusted to have committed, so a deterministic
   ``decode/<session-id-short>`` Session Branch is pointed at the *final* Workspace state: the branch
   is force-created at the current HEAD (wherever the model is), and a dirty worktree is captured with
   ``git add -A && git commit``. The model's own branches/commits are **preserved, never rewritten**
   (the branch is created *at* their HEAD, so their history rides along); a re-ship (a later ``/ship``,
   or exit after a ``/ship``) fast-forwards the same ref. This runs **before** any push, so the
   never-lose-results guarantee holds even when the push is disabled or fails (AC1).
3. **Ship** — ``git push origin decode/<session-id>`` with the user's **ambient** host credentials
   (SSH agent / credential helper / cached creds inherited via the env; only ``GIT_TERMINAL_PROMPT=0``
   is added so a missing credential fails fast instead of hanging). ``--repo <URL>`` lands the branch
   on the remote; ``--repo <local path>`` lands it in the local source repo, credential-free. **No
   force-push**: a diverged/unreachable push fails gracefully (``pushed=False``, the branch named).

It is **skipped** (a ``skipped`` :class:`ShipResult` with ``branch=None``) when there is nothing to
hand back: no ``--repo`` was given, the Workspace is not a git repo, or the Workspace is **unchanged**
vs the cloned HEAD (a clean worktree *and* ``HEAD == origin/HEAD``). Triggered both automatically (REPL
exit, headless ``decode run --repo`` completion) and explicitly (the idle-only ``/ship`` TUI command).
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from decode.sandbox.workspace import workspace_dir

logger = logging.getLogger(__name__)

# The branch namespace + how much of the session id names the Session Branch. ``decode/<short-id>`` is
# deterministic so a re-ship fast-forwards the SAME ref (ADR-0012 §8). Eight chars of a uuid/exec-id is
# ample to avoid collision within a machine's local branch namespace while keeping the branch readable.
_BRANCH_PREFIX = "decode/"
_SHORT_ID_LEN = 8

# The remote a ``git clone`` names its source — where the hand-back pushes. Constant: the Workspace is
# always a clone (task 082), so its push target is always ``origin`` (a URL or a local path).
_ORIGIN_REMOTE = "origin"

# decode's own reserved namespace INSIDE the Workspace. The sandbox executor seeds ``.decode/skills``
# there (ADR-0012 §5) so skill scripts resolve at the agent's cwd — but that is decode's scaffolding,
# **not** the user's work: it must neither be shipped into the user's Session Branch nor make an
# otherwise-unchanged Workspace look "changed" (which would defeat the unchanged-skip, AC4). The
# hand-back ignores it via the Workspace's LOCAL ``.git/info/exclude`` (never committed/pushed), which
# also leaves any ``.decode`` the user genuinely *tracks* untouched — ``info/exclude`` only ignores
# untracked paths, so a tracked file is still captured.
_DECODE_NAMESPACE = ".decode/"

# The identity used for the dirty-worktree capture commit, passed as ``git -c`` overrides so the commit
# ALWAYS succeeds regardless of the host's git config (a clone does not inherit the source's *local*
# identity, and never-lose-results must not hinge on a configured global one). It clearly marks the
# leftover-capture commit as decode-generated; the model's own commits keep whatever identity they were
# made with. ``commit.gpgsign=false`` avoids a hang/failure on a machine with signing forced on.
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

    ``branch`` is ``None`` **only** on a skip (no repo / not a git repo / unchanged Workspace) — a
    caller reads ``branch is None`` as "nothing was shipped". When the hand-back runs, ``branch`` is the
    deterministic ``decode/<short-id>`` ref that was secured locally: ``pushed=True`` means it also
    reached ``origin``; ``pushed=False`` (with ``branch`` set) means the local branch exists but the
    push was disabled/failed — the never-lose-results state, and ``message`` then names the branch and
    its ``.decode/sandbox`` location. ``message`` is a friendly, credential-free sentence the callers
    surface with their own ``Decode - `` / ``Decode: `` prefix.
    """

    branch: str | None
    pushed: bool
    message: str


def ship_workspace(harness_home: Path, *, repo: str | None, session_id: str) -> ShipResult:
    """Secure the Workspace onto a ``decode/<session-id>`` branch and push it host-side (ADR-0012 §8).

    Runs the collect → secure → ship hand-back over the local Workspace at
    ``<harness_home>/.decode/sandbox``. ``repo`` is the resolved source (``--repo`` / ``SANDBOX_REPO``);
    ``None`` means no repo was requested, so there is nothing to hand back. ``session_id`` names the
    deterministic Session Branch (the REPL passes the ``SessionLog`` id; the headless run passes its
    ``exec_id``).

    Returns a :class:`ShipResult`. **Skips** (``branch=None``) when there is nothing to ship: no repo,
    the Workspace is not a git repo (with an origin to push to), or it is unchanged vs the cloned HEAD
    (clean worktree AND ``HEAD == origin/HEAD``). Otherwise it force-creates ``decode/<short-id>`` at
    HEAD — committing a dirty worktree first (``git add -A && git commit``) so the model's uncommitted
    work is captured, without rewriting the model's own branches/commits — **before** pushing, so the
    local branch survives even when the push is disabled or fails (never-lose-results, AC1). The push is
    non-forced: a diverged/unreachable origin yields ``pushed=False`` with the branch named.

    Every git command runs as a **host** ``git`` subprocess against the local Workspace (never
    ``executor.run`` / ``backend.exec``), so no credential ever enters the sandbox. The rare git failure
    on the secure step raises :class:`RuntimeError`; the callers wrap this best-effort so a hand-back
    failure never blocks exit or a flow.
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
    # Ignore decode's own seeded ``.decode/`` scaffolding before the change-detection + commit, so it is
    # neither counted as "work" (AC4) nor shipped into the user's branch (ADR-0012 §5,8).
    _exclude_decode_namespace(workspace)
    if _is_unchanged(workspace):
        return ShipResult(
            None,
            False,
            "the workspace is unchanged from the cloned HEAD, so there is nothing to hand back.",
        )

    branch = _branch_name(session_id)
    # SECURE first (never-lose-results, AC1): the local branch + its capture commit must exist before
    # the push, so a disabled/failed push still leaves the results on a local branch.
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
    """The deterministic Session Branch name for ``session_id`` — ``decode/<short-id>`` (ADR-0012 §8).

    Keeps only branch-safe characters and takes the first :data:`_SHORT_ID_LEN` of them, so the same
    session always maps to the same ref (a re-ship fast-forwards it) and any exotic exec-id character is
    dropped. Falls back to a fixed suffix if the id is empty/all-unsafe (defensive; ids are uuids /
    exec-ids in practice).
    """
    safe = "".join(c for c in session_id if c.isalnum() or c in "-._")
    short = safe[:_SHORT_ID_LEN] or "session"
    return f"{_BRANCH_PREFIX}{short}"


def _secure_session_branch(workspace: Path, *, branch: str, session_id: str) -> None:
    """Capture the final Workspace state onto ``branch`` at HEAD, committing a dirty worktree first (§8).

    The model is not trusted to have committed: if the worktree is dirty, ``git add -A && git commit``
    captures the leftover work (under a decode identity via ``-c`` so it always succeeds), advancing
    HEAD on the model's current branch — an *addition*, never a rewrite of the model's own commits. Then
    ``git branch -f`` points ``branch`` at HEAD **without checking it out**, so the model's current
    branch is left exactly where it is (its history preserved) and a re-ship simply re-points/advances
    the same ref. Raises :class:`RuntimeError` on the rare git failure (callers wrap best-effort).
    """
    if _is_dirty(workspace):
        _run_git_checked(workspace, "add", "-A")
        _run_git_checked(
            workspace, *_CAPTURE_IDENTITY, "commit", "-m", f"decode session {session_id}"
        )
    _run_git_checked(workspace, "branch", "-f", branch, "HEAD")


def _push(workspace: Path, branch: str) -> tuple[bool, str]:
    """``git push origin <branch>`` host-side (no force); return ``(pushed, error)`` (ADR-0012 §8).

    Never raises: a non-zero exit (a non-fast-forward rejection, an unreachable/authless origin) returns
    ``(False, <git stderr>)`` so the caller degrades to the never-lose-results local branch. The stderr
    is returned to the caller for a **log** line only (kept out of the user-facing message, which could
    otherwise leak a credential embedded in a remote URL). Ambient host creds are inherited via the env;
    ``GIT_TERMINAL_PROMPT=0`` makes a missing credential fail fast instead of hanging on an invisible
    prompt.
    """
    completed = _run_git(workspace, "push", _ORIGIN_REMOTE, branch)
    if completed.returncode == 0:
        return True, ""
    return False, completed.stderr.strip() or "git push failed"


def _has_origin(workspace: Path) -> bool:
    """True if the Workspace is a git repo with an ``origin`` remote to push to (ADR-0012 §8).

    Recovers the push target's *existence* via ``git remote get-url origin`` **without** returning or
    logging the URL — a remote URL may embed a credential (task-061 discipline: never surface a secret
    value). A non-git directory (an empty/degraded clone scratch) or a repo with no origin both fail the
    command → ``False`` → the hand-back skips.
    """
    return _run_git(workspace, "remote", "get-url", _ORIGIN_REMOTE).returncode == 0


def _exclude_decode_namespace(workspace: Path) -> None:
    """Locally git-ignore decode's seeded ``.decode/`` scaffolding in the Workspace (ADR-0012 §5,8).

    Appends ``.decode/`` to the Workspace's ``.git/info/exclude`` — a **local** ignore file that is
    never committed or pushed — so ``git status`` / ``git add -A`` skip decode's own seeded
    ``.decode/skills`` (injected by the executor for skill-script resolution). Without this, that
    scaffolding would make every session's worktree perpetually dirty (defeating the unchanged-skip)
    and would be committed into the user's Session Branch. ``info/exclude`` only ignores **untracked**
    paths, so a ``.decode`` the user genuinely tracks is untouched. Idempotent: a no-op when the line is
    already present, so a re-ship never duplicates it. Called only after :func:`_has_origin` confirmed a
    git repo, so ``.git/`` exists (``info/`` is created if absent).
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
    """True if the Workspace has no work to hand back: clean worktree AND ``HEAD == origin/HEAD`` (§8).

    Both conditions are required. ``origin/HEAD`` is the cloned HEAD (a remote-tracking ref pinned at
    the clone commit, task 082), so ``HEAD == origin/HEAD`` means no commit landed beyond the clone; a
    clean worktree means no uncommitted work either. If ``origin/HEAD`` cannot be resolved (a clone
    without the ref — unusual), it is treated as *changed* (ship rather than silently drop results).
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
    """Run ``git -C <workspace> <args>`` as a **host** subprocess (never through a sandbox seam) (§8).

    The single choke point for every git command the hand-back runs, so the security crux is trivially
    auditable (the boundary test records these calls): a plain host ``git`` against the local Workspace,
    inheriting the ambient env for the user's credentials plus only ``GIT_TERMINAL_PROMPT=0`` (fail fast,
    never hang) — nothing sandbox-specific, no injected token. ``check=False``: callers decide how to
    treat a non-zero exit.
    """
    return subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        check=False,
    )


def _run_git_checked(workspace: Path, *args: str) -> str:
    """Run a git command that must succeed (the secure-step writes); raise on a non-zero exit.

    Returns the stripped stdout on success. A failure raises :class:`RuntimeError` carrying git's stderr
    so the caller's best-effort wrapper can log it — used for ``add`` / ``commit`` / ``branch``, which
    are robust host-side operations (the capture commit forces an identity via ``-c``).
    """
    completed = _run_git(workspace, *args)
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout.strip()
