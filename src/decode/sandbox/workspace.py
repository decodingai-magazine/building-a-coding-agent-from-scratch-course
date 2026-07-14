"""Host-side Workspace helpers — resolve, clone, seed skills, bootstrap-tar (ADR-0012 §3,5,6).

Pure, synchronous building blocks for the isolated Workspace, kept free of any docker/modal import
so they compose with either backend and unit-test against plain local git repos. Cloning runs
host-side with the user's ambient git credentials — no credential enters the sandbox. The retired
per-call mtime-delta sync is gone (ADR-0012 §5 rejects it); :func:`tar_dir` / :func:`extract_tar`
are the only transport helpers.
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

    The single place the Workspace path is computed: ``harness_home /
    settings.sandbox_workspace_dir``. Only the *tool scope* — Harness Home still anchors every other
    ``.decode`` artifact (ADR-0012 §3,6).
    """
    workspace = harness_home / settings.sandbox_workspace_dir
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace.resolve()


# Path segments that mean "this is a page ABOUT the repo", not the repo: the copy-paste a human makes
# from a browser. GitHub's `/tree/`, `/blob/`, `/commit/`, `/pull/`, `/releases`, `/issues`, and
# GitLab's `/-/` prefix all sit AFTER `owner/repo`, so truncating at the first one recovers the clone
# URL — without assuming a path depth (GitLab subgroups nest arbitrarily).
_WEB_URL_MARKERS = (
    "/tree/",
    "/blob/",
    "/commit/",
    "/commits/",
    "/pull/",
    "/releases",
    "/issues",
    "/-/",
)


def normalize_repo(repo: str) -> str:
    """Turn what a human pastes into something ``git clone`` accepts (ADR-0012 §3).

    ``https://github.com/o/r/tree/main`` is a *web page*, not a repo, and git says so only at clone
    time — deep inside a flow container, where the failure used to cost a whole (paid) run. Fixing it
    at the CLI would leave every other caller brittle, so it happens HERE, at the single choke point
    every mode clones through.

    Strips the browser's trailing junk (``?tab=…``, ``#readme``, the ``/tree/<ref>``-style suffixes)
    and a trailing slash. **Local paths and scp-form remotes pass through untouched** — a directory is
    free to be called ``blob`` and ``git@host:o/r.git`` is already exactly what git wants.
    """
    if "://" not in repo:  # a local path (or scp form) — never a browser URL
        return repo
    cleaned = repo.strip().split("#", 1)[0].split("?", 1)[0]
    # The EARLIEST marker in the string wins, not the first in the list: GitLab's `/-/tree/main`
    # contains both `/-/` and `/tree/`, and cutting at `/tree/` would leave a trailing `/-`.
    cuts = [index for marker in _WEB_URL_MARKERS if (index := cleaned.find(marker)) != -1]
    if cuts:
        cleaned = cleaned[: min(cuts)]
    return cleaned.rstrip("/")


def prepare_workspace(harness_home: Path, *, repo: str | None = None, local: bool = False) -> Path:
    """Ensure the Workspace exists and, if empty + ``repo`` given, clone it at HEAD (ADR-0012 §3).

    A populated Workspace is **reused, never re-cloned** (it persists across sessions; re-cloning
    would discard in-progress work). A clone failure raises :class:`RuntimeError`; launch-time
    callers wanting the degrade-to-empty policy use :func:`prepare_workspace_or_empty`. Because this
    is a real ``git clone``, the Workspace's own git recovers everything the hand-back needs — no
    sidecar file: ``origin`` is the push target and ``origin/HEAD`` stays pinned at the cloned
    commit for the unchanged-vs-worked check (ADR-0012 §8).

    ``repo`` is passed through :func:`normalize_repo` first, so a browser URL clones the repo it
    obviously means instead of failing.
    """
    workspace = workspace_dir(harness_home)
    if repo is None:
        return workspace
    if any(workspace.iterdir()):
        logger.debug("[sandbox] workspace %s already populated — reusing (no clone)", workspace)
        return workspace
    _git_clone(normalize_repo(repo), workspace, local=local)
    return workspace


def prepare_workspace_or_empty(
    harness_home: Path, *, repo: str | None = None, local: bool = False
) -> tuple[Path, str | None]:
    """Prepare the Workspace, degrading to an **empty** one if the clone fails (ADR-0012 §3).

    Returns ``(workspace_path, error)`` — ``error`` is ``None`` on success, the git failure text on
    a degrade. The one launch-time policy the REPL and the headless flow share: a bad ``--repo``
    never crashes the launch.
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

    Read from the ``SANDBOX_GIT_USER_*`` settings (empty values skipped) so a model ``git commit``
    has an author out of the box; each backend applies them its own way.
    """
    pairs: list[tuple[str, str]] = []
    if settings.sandbox_git_user_name:
        pairs.append(("user.name", settings.sandbox_git_user_name))
    if settings.sandbox_git_user_email:
        pairs.append(("user.email", settings.sandbox_git_user_email))
    return pairs


# The env var name the Worker's ``GITHUB_TOKEN`` is injected under, in BOTH backends (ADR-0016 §2).
# ``gh`` reads it natively; the credential helper below feeds it to git's HTTPS transport.
GIT_TOKEN_ENV = "GITHUB_TOKEN"

# The credential-helper VALUE: echoes the RUNTIME ``$GITHUB_TOKEN`` as the HTTPS password so a
# ``git push`` over HTTPS authenticates. Reads the env var at push time, so the token is never
# expanded into a command line (nor, on modal, frozen into a cached image layer). Used by both the
# Worker (via the config command below) and the host-side Hand-back (``handback._push``).
GIT_CREDENTIAL_HELPER_VALUE = (
    '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'
)

# Configured in a Worker whose ``SANDBOX_GIT_TOKEN`` is set — one mechanism, both backends
# (ADR-0016 §2).
GIT_CREDENTIAL_HELPER = f"git config --global credential.helper '{GIT_CREDENTIAL_HELPER_VALUE}'"


def sandbox_git_token() -> str:
    """The resolved ``SANDBOX_GIT_TOKEN``, or ``""`` when unset/empty — the gate both backends read.

    Gating on the resolved VALUE (not ``is not None``) is load-bearing: an explicit
    ``SANDBOX_GIT_TOKEN=`` parses to ``SecretStr("")``, which must inject nothing at all — no env
    var, no credential helper (ADR-0016 §2).
    """
    if settings.sandbox_git_token is None:
        return ""
    return settings.sandbox_git_token.get_secret_value()


def _git_clone(repo: str, workspace: Path, *, local: bool) -> None:
    """``git clone`` ``repo`` into the empty ``workspace`` via the host ``git`` CLI.

    Ambient credentials only (inherited env) — decode never handles a token; ``GIT_TERMINAL_PROMPT=0``
    makes a missing credential fail fast instead of hanging. A non-zero exit raises
    :class:`RuntimeError` carrying git's stderr. ``ponytail:`` no wall-clock cap — bound it here if
    clone-at-launch ever needs a hard deadline.
    """
    args = ["clone"]
    if local:
        # --local: fast hardlink clone of a local-path source; HEAD only, uncommitted dirt not carried.
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
    """Copy ``<harness_home>/.decode/skills`` into ``<workspace>/.decode/skills`` (ADR-0012 §5).

    One host-side copy replacing the docker read-only mount and the modal ``add_local_dir`` seeding,
    so cwd-relative skill-script paths resolve inside the Workspace. A no-op when the project ships
    no skills; ``dirs_exist_ok=True`` lets a re-seed merge rather than crash.
    """
    source = workspace.parent / "skills"
    if not source.is_dir():
        logger.debug("[sandbox] no skills to seed at %s", source)
        return
    destination = workspace / ".decode" / "skills"
    shutil.copytree(source, destination, dirs_exist_ok=True)
    logger.debug("[sandbox] seeded skills %s → %s", source, destination)


def tar_dir(directory: Path) -> bytes:
    """Pack ``directory``'s contents into an in-memory uncompressed tar under ``arcname="."``.

    Backend-agnostic bootstrap-transfer helper; :func:`extract_tar` auto-detects compression on read.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        tar.add(directory, arcname=".")
    return buffer.getvalue()


def _make_tree_writable(directory: Path) -> None:
    """Add owner-write across ``directory`` so a re-:func:`extract_tar` can overwrite read-only files.

    ``tar.extractall`` raises ``PermissionError`` on existing read-only targets — exactly a clone's
    0444 ``.git`` loose objects. Owner-``rwx`` on dirs so the walk can still descend; symlinks are
    skipped in :func:`_add_owner_write`. Best-effort per path — a chmod failure is left for
    ``extractall`` to surface as the real error.
    """
    for root, dirnames, filenames in os.walk(directory):
        base = Path(root)
        for name in dirnames:
            _add_owner_write(base / name, stat.S_IRWXU)
        for name in filenames:
            _add_owner_write(base / name, stat.S_IWUSR)


def _add_owner_write(path: Path, bits: int) -> None:
    """OR ``bits`` into ``path``'s mode, best-effort (``OSError`` suppressed), **skipping symlinks**.

    ``Path.chmod`` FOLLOWS symlinks, so chmod'ing a link escaping the Workspace would touch an
    out-of-tree host target — a containment breach. A symlink never needs owner-write for the
    extract (the ``data`` filter neutralizes symlink members) and ``os.walk`` never recurses into
    symlinked dirs, so skipping the entry fully closes the surface.
    """
    with contextlib.suppress(OSError):
        if path.is_symlink():
            return
        path.chmod(path.stat().st_mode | bits)


def extract_tar(data: bytes, directory: Path) -> None:
    """Extract a :func:`tar_dir` archive into ``directory`` (created if missing), overlaying it.

    The ``data`` filter sanitizes member paths and silences the ``DeprecationWarning`` that
    ``filterwarnings=["error"]`` would fail on. The destination tree is made owner-writable first so
    the Modal export sweep over a clone's read-only ``.git`` objects overwrites instead of aborting
    (ADR-0012 §5,8).
    """
    directory.mkdir(parents=True, exist_ok=True)
    _make_tree_writable(directory)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        tar.extractall(directory, filter="data")
