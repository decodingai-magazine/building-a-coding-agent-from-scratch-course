"""Trace metadata — the fields an eval joins and filters traces on (ADR-0022 §10).

:func:`trace_metadata` is just a small mapping of plain strings; landing it in the trace is
:func:`decode.observability.root_span`'s job, which sets each key as an ``opik.metadata.<key>``
attribute — the only spelling Opik ingests into a span's ``metadata`` (see
:data:`decode.observability.tracing.OPIK_METADATA_PREFIX`). Four fields, the ones Trace Mining slices
by: which build ran (``git_sha``), which model, which sandbox mode, which environment.
``kitaru_session_id`` rides along when a caller knows it — the join itself never depends on it,
because the decode session id (= Opik ``thread_id`` = Kitaru ``session_name`` = the Session Branch's
short id) is the join.

The sha is one ``git rev-parse`` per launch directory, cached: a run that is not launched from a
checkout (a benchmark Trial's throwaway Harness Home) gets ``"unknown"`` rather than a missing key,
so a mining query never has to special-case an absent field.
"""

from __future__ import annotations

import logging
import subprocess
from functools import cache
from pathlib import Path

from decode.config.settings import settings

logger = logging.getLogger(__name__)

# What a run outside a git checkout — or one where git is unavailable — reports as its build.
UNKNOWN_GIT_SHA = "unknown"

# A local ``rev-parse`` answers in milliseconds; the bound is only there so a wedged git (an NFS
# mount, a stale index lock) can never hold up the first span of a run.
_GIT_TIMEOUT_S = 5.0


@cache
def git_sha(cwd: str) -> str:
    """The HEAD sha of the repo ``cwd`` sits in, or :data:`UNKNOWN_GIT_SHA` (ADR-0022 §10).

    Cached per directory — the sha of a running process cannot change under it, and every root span
    of a REPL session would otherwise fork a subprocess. Keyed on the directory (a ``str``, so the
    cache key is hashable and a test's ``chdir`` cannot read a neighbour's answer) rather than on
    "the cwd", so the value is never resolved for one directory and reported for another.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("could not resolve the git sha of %s", cwd, exc_info=True)
        return UNKNOWN_GIT_SHA
    sha = completed.stdout.strip()
    return sha if completed.returncode == 0 and sha else UNKNOWN_GIT_SHA


def trace_metadata(
    model: str | None = None, *, kitaru_session_id: str | None = None
) -> dict[str, str]:
    """The join fields for one run's root span: build, model, sandbox mode, environment.

    ``model`` is THIS run's model (``decode run --model <id>``); ``None`` reads the active
    provider's configured id, which is what the REPL uses. ``kitaru_session_id`` is added only when
    a caller actually knows it — an absent key reads as "not recorded here", while a ``"None"``
    string in a metadata filter reads as a value.
    """
    metadata = {
        "git_sha": git_sha(str(Path.cwd())),
        "model": model or settings.active_model,
        "sandbox_mode": settings.sandbox_mode,
        "decode_env": settings.decode_env,
    }
    if kitaru_session_id:
        metadata["kitaru_session_id"] = kitaru_session_id
    return metadata
