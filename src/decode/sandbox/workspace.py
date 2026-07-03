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
* :func:`seed_skills` — copy the project's ``.decode/skills`` (the Workspace's sibling under
  ``.decode/``) into ``<workspace>/.decode/skills`` so cwd-relative skill-script paths resolve inside
  the Workspace. Replaces the docker read-only mount and the modal ``add_local_dir`` seeding
  (ADR-0012 §5). A no-op when the project ships no skills.
* :func:`tar_dir` / :func:`extract_tar` — backend-agnostic, in-memory tar helpers the Modal backend's
  ONE-shot bootstrap upload may use (080). The retired per-call mtime-delta sync is **gone** (ADR-0012
  §5 rejects it — an mtime delta cannot propagate a remote ``rm``), so these are the only transport
  helpers here: no marker, delta, or size-cap machinery.

Nothing imports this module yet — it lands as a pure addition (079-082 wire it in), so the existing
suite stays byte-green.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
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

    Returns the Workspace path. A clone failure raises :class:`RuntimeError` (the launch-time
    degrade-to-empty policy is task 082's concern, above this helper).
    """
    workspace = workspace_dir(harness_home)
    if repo is None:
        return workspace
    if any(workspace.iterdir()):
        logger.debug("[sandbox] workspace %s already populated — reusing (no clone)", workspace)
        return workspace
    _git_clone(repo, workspace, local=local)
    return workspace


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


def extract_tar(data: bytes, directory: Path) -> None:
    """Extract a :func:`tar_dir` archive into ``directory`` (created if missing).

    The mirror of :func:`tar_dir` — reconstructs the packed tree under ``directory``. Uses the
    ``data`` extraction filter (Python 3.12+): it sanitizes member paths (no absolute paths, no ``..``
    escapes) *and* silences the unfiltered-``extractall`` ``DeprecationWarning`` that
    ``filterwarnings=["error"]`` would otherwise turn into a failure.
    """
    directory.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        tar.extractall(directory, filter="data")
