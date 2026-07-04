"""Host-side Workspace helpers — resolve, clone, seed skills, bootstrap-tar (ADR-0012 §3,5,6).

Pure, **synchronous**, host-side building blocks for the isolated Workspace (ADR-0012). A sandbox
mode gives the agent an isolated ``/workspace`` that is a ``git clone`` of a user-supplied repo; this
module owns the *host* side of setting that up, kept deliberately free of any docker/modal import so
it composes with either backend (079 docker, 080 modal) and stays trivially unit-testable with plain
local git repos — no daemon, no remote.

* :func:`workspace_dir` — the **single** place the Workspace path is computed: ``harness_home /
  settings.sandbox_workspace_dir`` (``.decode/sandbox``), created idempotently and resolved. Every
  caller (the CLI warm-up, both executors) derives the path here so the host directory and the
  sandbox's ``/workspace`` never drift.
* :func:`prepare_workspace` — ensure that directory exists and, when a ``repo`` is given and the
  Workspace is still **empty**, host-side ``git clone`` it at its committed HEAD (``local=True`` → the
  fast ``git clone --local`` for a local-path source). A Workspace that already holds content is
  **reused, never re-cloned** — it is the docker mount source / the modal bootstrap source across
  sessions, so re-cloning would blow away in-progress work. Cloning uses the user's **ambient** git
  credentials (SSH agent / credential helper / cached creds); the interactive terminal prompt is
  disabled so a missing credential fails fast instead of hanging on an invisible prompt.
* :func:`prepare_workspace_or_empty` — the launch-time wrapper (task 082) that degrades a clone
  failure to an empty Workspace + a message instead of raising, so a bad ``--repo`` never crashes the
  REPL / headless launch. The one degrade policy both the TUI and the headless flow share.
* :func:`seed_skills` — copy the project's ``.decode/skills`` (the Workspace's sibling under
  ``.decode/``) into ``<workspace>/.decode/skills`` so cwd-relative skill-script paths resolve inside
  the Workspace. Replaces the docker read-only mount and the modal ``add_local_dir`` seeding
  (ADR-0012 §5). A no-op when the project ships no skills.
* :func:`tar_dir` / :func:`extract_tar` — backend-agnostic, in-memory tar helpers the Modal backend's
  ONE-shot bootstrap upload may use (080). The retired per-call mtime-delta sync is **gone** (ADR-0012
  §5 rejects it — an mtime delta cannot propagate a remote ``rm``), so these are the only transport
  helpers here: no marker, delta, or size-cap machinery.

Wired in incrementally (079 docker, 080 modal, 081 the file-tool seam, 082 the CLI ``--repo`` /
``--local`` clone-at-launch): the helpers stay backend-free so they unit-test with plain local git.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

from decode.config.settings import settings

logger = logging.getLogger(__name__)


def workspace_dir(harness_home: Path) -> Path:
    """Resolve (and idempotently create) the host Workspace directory for ``harness_home``.

    The **single** place the Workspace path is computed: ``harness_home /
    settings.sandbox_workspace_dir`` (``.decode/sandbox`` by default). Created with ``parents=True,
    exist_ok=True`` so repeated calls are idempotent, then ``resolve()``d so the host path and the
    sandbox's ``/workspace`` (bind mount / bootstrap target) never drift. Note this is only the
    *tool scope* — Harness Home still anchors every other ``.decode`` artifact (sessions, MEMORY.md,
    skills, the permission file); ADR-0012 §3,6.
    """
    workspace = harness_home / settings.sandbox_workspace_dir
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace.resolve()


def prepare_workspace(harness_home: Path, *, repo: str | None = None, local: bool = False) -> Path:
    """Ensure the Workspace exists and, if empty + ``repo`` given, clone it at HEAD (ADR-0012 §3).

    Resolves the Workspace via :func:`workspace_dir`, then:

    * ``repo is None`` → leave it empty (an empty Workspace is a valid scratch) and return it.
    * ``repo`` given **and the Workspace is empty** → host-side ``git clone`` the source (a URL or a
      local path) at its committed HEAD into it. ``local=True`` uses ``git clone --local`` (a fast
      hardlink clone for a local-path source).
    * ``repo`` given **but the Workspace already holds content** → **reuse it, never re-clone**. The
      Workspace is the docker mount source / the modal bootstrap source and persists across sessions;
      re-cloning would discard in-progress work.

    Returns the Workspace path. A clone failure raises :class:`RuntimeError`; callers that want the
    launch-time degrade-to-empty policy use :func:`prepare_workspace_or_empty`.

    **Substrate for the task-083 git hand-back (ADR-0012 §8).** Because this is a *real* ``git clone``,
    the Workspace's own git recovers everything the hand-back needs — **no sidecar file** (``ponytail:``
    prefer git-native recovery over a marker):

    * the **origin** (where to push) — ``git -C <workspace> remote get-url origin`` returns the source
      the clone was made from (``git clone`` sets ``origin`` automatically, URL or local path);
    * the **cloned HEAD** (to tell "unchanged vs worked") — the remote-tracking ref
      ``git -C <workspace> rev-parse origin/HEAD`` (or the current branch's ``@{upstream}``) stays
      pinned at the commit that was cloned even after the agent commits, so ``HEAD == origin/HEAD``
      means the Workspace is unchanged-vs-cloned and the hand-back can be skipped.
    """
    workspace = workspace_dir(harness_home)
    if repo is None:
        return workspace
    if any(workspace.iterdir()):
        logger.debug("[sandbox] workspace %s already populated — reusing (no clone)", workspace)
        return workspace
    _git_clone(repo, workspace, local=local)
    return workspace


def prepare_workspace_or_empty(
    harness_home: Path, *, repo: str | None = None, local: bool = False
) -> tuple[Path, str | None]:
    """Prepare the Workspace, degrading to an **empty** one if the clone fails (ADR-0012 §3).

    The launch-time wrapper around :func:`prepare_workspace`: a ``git clone`` failure (a bad URL, a
    missing credential, a network stall) must never crash the launch — an empty Workspace is a valid
    scratch, so decode starts anyway. Returns ``(workspace_path, error)`` where ``error`` is ``None`` on
    success and the git failure text on a degrade; the path is the same ``.decode/sandbox`` either way
    (only its *contents* differ). The failure is logged here; the caller surfaces ``error`` the way its
    surface allows — the REPL renders one friendly TUI line, the headless flow just carries the log line
    — so both entry paths share one degrade policy without duplicating the ``try``/``except``.
    """
    try:
        return prepare_workspace(harness_home, repo=repo, local=local), None
    except RuntimeError as exc:
        logger.warning(
            "[sandbox] workspace clone of %r failed; degrading to an empty workspace", repo
        )
        return workspace_dir(harness_home), str(exc)


def git_config_pairs() -> list[tuple[str, str]]:
    """The ``(key, value)`` git-config pairs to preconfigure in the sandbox — ``user.name`` / ``user.email``.

    Read from the ``SANDBOX_GIT_USER_*`` settings (default ``decode`` / ``decode@localhost`` — the same
    identity the hand-back stamps its capture commit with), an empty value skipped, so a model ``git
    commit`` in the Workspace has an author out of the box (or none if both are cleared). Host-side and
    import-light (no docker/modal), so each backend applies it its own way: docker ``git config`` in the
    session container, modal a baked image layer.
    """
    pairs: list[tuple[str, str]] = []
    if settings.sandbox_git_user_name:
        pairs.append(("user.name", settings.sandbox_git_user_name))
    if settings.sandbox_git_user_email:
        pairs.append(("user.email", settings.sandbox_git_user_email))
    return pairs


def _git_clone(repo: str, workspace: Path, *, local: bool) -> None:
    """``git clone`` ``repo`` into the empty ``workspace`` at its committed HEAD (sync subprocess).

    Shells out to the ``git`` CLI (dependency-free, mirroring the sandbox executors' CLI-over-SDK
    choice) with the user's **ambient** credentials — the inherited env carries the SSH agent /
    credential helper / cached creds, so a private repo clones without decode ever handling a token
    (ADR-0012 §3, the same "no credential in the sandbox" invariant as the Credential Proxy). Only
    ``GIT_TERMINAL_PROMPT=0`` is added, so a *missing* credential fails fast instead of hanging on an
    invisible interactive prompt (a warm-up / headless launch has no terminal to answer it). A
    non-zero exit raises :class:`RuntimeError` carrying git's stderr. ``ponytail:`` no wall-clock cap
    — a large repo may clone for a while and a network stall relies on git's own timeouts; bound it
    here if clone-at-launch ever needs a hard deadline.
    """
    args = ["clone"]
    if local:
        # --local: a fast hardlink/copy clone of a local-path source's object store. HEAD only — the
        # source's uncommitted working-tree dirt is never carried (ADR-0012 consequence).
        args.append("--local")
    args += [repo, str(workspace)]
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git clone of {repo!r} failed (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    logger.info("[sandbox] cloned %s into %s", repo, workspace)


def seed_skills(workspace: Path) -> None:
    """Copy the project's skills into ``<workspace>/.decode/skills`` (ADR-0012 §5).

    The project's skills live at the Workspace's sibling under ``.decode/`` — ``workspace.parent /
    "skills"`` (i.e. ``<harness_home>/.decode/skills``). They are copied into
    ``<workspace>/.decode/skills`` so a skill payload's cwd-relative script path
    (``.decode/skills/<name>/scripts/…``) resolves once the agent's cwd is the Workspace. This one
    host-side copy replaces the docker read-only ``.decode/skills`` mount and the modal
    ``add_local_dir`` seeding, running the same way for both backends' bootstrap. A **no-op** when the
    project ships no skills; ``dirs_exist_ok=True`` makes a re-seed (a second warm-up) merge rather
    than crash.
    """
    source = workspace.parent / "skills"
    if not source.is_dir():
        logger.debug("[sandbox] no skills to seed at %s", source)
        return
    destination = workspace / ".decode" / "skills"
    shutil.copytree(source, destination, dirs_exist_ok=True)
    logger.debug("[sandbox] seeded skills %s → %s", source, destination)


def tar_dir(directory: Path) -> bytes:
    """Pack ``directory``'s contents into an in-memory (uncompressed) tar and return the bytes.

    A backend-agnostic bootstrap-transfer helper the Modal backend's ONE-shot upload may use (080):
    the whole tree is added under ``arcname="."`` so :func:`extract_tar` reconstructs it faithfully at
    any target root. In-memory (no temp file) and uncompressed (``mode="w"``) since the round-trip is
    local — :func:`extract_tar` auto-detects compression on read, so a future gzip switch stays
    compatible.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        tar.add(directory, arcname=".")
    return buffer.getvalue()


def _make_tree_writable(directory: Path) -> None:
    """Add owner-write across ``directory`` so a re-:func:`extract_tar` can overwrite read-only files.

    ``tar.extractall`` opens each target in ``"wb"``, which raises ``PermissionError`` when the
    destination already holds a **read-only** file — exactly a ``git clone``'s ``.git`` loose objects,
    which git writes mode 0444 (content-addressed and immutable). Walking top-down and OR-ing owner-write
    into every existing **non-symlink** path — owner-``rwx`` on directories, so the walk can still descend
    into and rewrite the entries beneath a read-only dir; a symlink is skipped in :func:`_add_owner_write`
    so a link escaping the tree never has its out-of-tree target chmod'd — lets the overwrite land; the
    ``filter="data"`` pass then re-normalizes each written member's mode and git re-derives object
    modes, so the swept ``.git`` stays a valid repo. Best-effort per path: a chmod failure is left for
    ``extractall`` to surface as the real error rather than masked here.
    """
    for root, dirnames, filenames in os.walk(directory):
        base = Path(root)
        for name in dirnames:
            _add_owner_write(base / name, stat.S_IRWXU)
        for name in filenames:
            _add_owner_write(base / name, stat.S_IWUSR)


def _add_owner_write(path: Path, bits: int) -> None:
    """OR ``bits`` into ``path``'s mode; swallow ``OSError`` (best-effort — let ``extractall`` surface it).

    **Skips symlinks.** ``Path.chmod``/``Path.stat`` FOLLOW symlinks (there is no ``lchmod`` on
    macOS/Linux) and ``os.walk(followlinks=False)`` still yields a symlink's NAME at its level, so
    chmod'ing a symlink that escapes the Workspace would OR owner-write into an OUT-OF-TREE host target —
    a containment breach under the "clone + run untrusted code" threat model (git stores arbitrary link
    targets). A symlink never needs owner-write for the extract anyway (the ``data`` filter neutralizes
    incoming symlink members) and ``os.walk`` never recurses INTO a symlinked dir, so skipping the
    symlink ENTRY at each level fully closes the surface — while the read-only ``.git`` fix still lands,
    since git's loose objects are REGULAR files. The check rides inside the ``OSError`` suppression so a
    rare ``lstat`` failure stays best-effort (left for ``extractall`` to surface), not a hard abort.
    """
    with contextlib.suppress(OSError):
        if path.is_symlink():
            return
        path.chmod(path.stat().st_mode | bits)


def extract_tar(data: bytes, directory: Path) -> None:
    """Extract a :func:`tar_dir` archive into ``directory`` (created if missing), overlaying it.

    The mirror of :func:`tar_dir` — reconstructs the packed tree under ``directory``. Uses the
    ``data`` extraction filter (Python 3.12+): it sanitizes member paths (no absolute paths, no ``..``
    escapes) *and* silences the unfiltered-``extractall`` ``DeprecationWarning`` that
    ``filterwarnings=["error"]`` would otherwise turn into a failure.

    Before extracting it makes any existing destination tree owner-writable
    (:func:`_make_tree_writable`), so the Modal end-of-session export sweep OVER a ``git clone`` — whose
    ``.git`` loose objects are read-only (0444) — overwrites them instead of aborting with
    ``PermissionError`` and sweeping nothing (ADR-0012 §5,8). Overwriting a content-addressed object with
    identical bytes is safe.
    """
    directory.mkdir(parents=True, exist_ok=True)
    _make_tree_writable(directory)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        tar.extractall(directory, filter="data")
