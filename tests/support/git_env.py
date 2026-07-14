"""The git environment variables the suite scrubs before every test (task 111).

Git exports its *repository location* into every hook it runs. Our pre-push hook runs ``pytest``,
so every test that shells out to ``git`` inherits those variables and silently operates against the
**wrong repository** — the one being pushed, not the tiny throwaway repo the test built under
``tmp_path``. Observed twice: 7 failures in ``test_workspace.py`` under a leaked ``GIT_DIR``, and a
worktree index left with 252 files staged as deleted while the identical files sat on disk.

The list below is exactly the variables that **redirect git at a different repo / index / worktree /
object store**, i.e. the ones that make ``git -C <tmp_path>`` stop meaning ``<tmp_path>``:

- ``GIT_DIR`` — the repository. The headline leak (git sets it for hooks; absolute in a worktree).
- ``GIT_COMMON_DIR`` — the shared repository behind a worktree; same redirect, worktree-only.
- ``GIT_WORK_TREE`` — the working-tree root a command reads and writes.
- ``GIT_INDEX_FILE`` — the index a command stages into. The index-corruption vector.
- ``GIT_PREFIX`` — the sub-directory prefix git hands hooks; re-anchors relative pathspecs.
- ``GIT_OBJECT_DIRECTORY`` — where objects are written/read.
- ``GIT_ALTERNATE_OBJECT_DIRECTORIES`` — extra object stores merged into the lookup.
- ``GIT_NAMESPACE`` — the ref namespace, i.e. which refs a command can even see.

Deliberately **not** scrubbed — a blanket ``GIT_*`` purge would break legitimate, unrelated setup:
identity (``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*``, which CI images set so ``git commit`` works at all),
config sources (``GIT_CONFIG_*``), transport/auth (``GIT_SSH_COMMAND``, ``GIT_ASKPASS``,
``GIT_TERMINAL_PROMPT``), git's own binaries (``GIT_EXEC_PATH``), and diagnostics (``GIT_TRACE*``).
None of those points git at another repository, so none of them can cause this bug.
"""

from __future__ import annotations

from typing import Final

GIT_HOOK_ENV_VARS: Final[tuple[str, ...]] = (
    "GIT_DIR",
    "GIT_COMMON_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)
