"""The ONE ``kitaru`` CLI seam the eval bridge commands shell out through (ADR-0022 §10, §11).

Both bridge commands (``evals kitaru import``, ``evals kitaru cohort from-experiment``) join Opik to
Kitaru by shelling out to the ``kitaru`` CLI rather than by importing its client: what the command
does is exactly what an operator could type, so a failure is reproducible by copy-pasting the argv,
and ``evals`` keeps the CLI-only rule the project holds every other piece of infrastructure to. It
also keeps ``kitaru`` out of the eval import graph — only the binary is needed.

**One server, one URL** (ADR-0022 §10). ``KITARU_API_URL`` is read from the PROCESS env here, not
from ``Settings``: decode deliberately owns no kitaru connection setting — the adapter's own client
resolves url + key (ADR-0019 §3, and the Recording Seam block in ``.env.example``). It is passed to
every invocation as an explicit ``--server`` so the argv is self-describing and a stale
``kitaru login`` store can never silently retarget a command at another workspace. (Verified on
kitaru 0.26.0: resolution is ``--server`` > ``KITARU_API_URL`` > the login store.)

Every invocation adds ``--output json`` + ``--non-interactive``: the CLI answers with one envelope
(``{"ok": true, "item"/"items": …}``), which is the whole contract this module parses. A non-zero
exit, an ``ok: false`` envelope, unparseable output or a missing binary all collapse into one
:class:`KitaruCommandError` naming the argv.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from typing import Any

from evals.harness.keys import eval_keys_missing

logger = logging.getLogger(__name__)

# The adapter-owned connection variable (ADR-0019 §3): decode has no setting for it, so the operator
# surfaces read it from the process env — it must be EXPORTED, not merely present in `.env`.
KITARU_API_URL_ENV = "KITARU_API_URL"

# Sessions come back paged; 200 keeps a demo-sized workspace to one request while staying inside the
# CLI's 1-1000 bound. Every reader below pages to exhaustion anyway — a truncated read would mean a
# silently short cohort or a duplicate import.
SESSION_PAGE_SIZE = 200

# A sub-command that waits on a Worker (an import) can take minutes; a read is instant. One generous
# ceiling keeps a hung CLI from hanging the eval command forever.
DEFAULT_TIMEOUT_S = 900.0

# The `kitaru <kind> get` routes that carry a `latest_version` — what a bare name resolves against.
_VERSIONED_KINDS = ("agent", "importer", "evaluator")


class KitaruCommandError(Exception):
    """One ``kitaru`` invocation failed — carries the argv so the operator can re-run it.

    ``kind`` is the server's own error kind (``not_found`` / ``conflict`` / …) when there was an
    envelope to read it from: callers that must tell "this cohort does not exist yet" apart from
    "the server is down" branch on it rather than on message text.
    """

    def __init__(self, message: str, *, kind: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind


def kitaru_server() -> str | None:
    """The Kitaru Server URL from the process env, or ``None`` when it is not exported."""
    value = os.environ.get(KITARU_API_URL_ENV, "").strip()
    return value or None


def kitaru_keys_missing() -> list[str]:
    """The env-var names the bridge commands need but do not have — empty means good to run.

    Both halves of the join: ``OPIK_API_KEY`` to READ the traces (via the shared, settings-backed
    preflight — no inference happens here, so no provider key is demanded) and an exported
    ``KITARU_API_URL`` to WRITE the sessions.
    """
    missing = eval_keys_missing(require_provider=False)
    if kitaru_server() is None:
        missing.append(KITARU_API_URL_ENV)
    return missing


def kitaru_argv(args: list[str], *, server: str | None = None) -> list[str]:
    """The exact ``kitaru …`` argv for one sub-command: json output, no prompts, one server."""
    argv = ["kitaru", *args, "--output", "json", "--non-interactive"]
    if server:
        argv += ["--server", server]
    return argv


def run_kitaru(
    args: list[str], *, server: str | None = None, timeout: float = DEFAULT_TIMEOUT_S
) -> dict[str, Any]:
    """Run one ``kitaru`` sub-command and return its parsed envelope, or raise.

    Deliberately not ``check=True``: the CLI answers a 409/404 with an ``ok: false`` envelope AND a
    non-zero exit, and the envelope carries the message worth printing.
    """
    argv = kitaru_argv(args, server=server)
    logger.info("[eval] %s", format_argv(argv))
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise KitaruCommandError(
            f"`{format_argv(argv)}` could not start: no `kitaru` on PATH ({exc}) — `make install`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise KitaruCommandError(f"`{format_argv(argv)}` timed out after {timeout:.0f}s.") from exc
    return _envelope(completed, argv)


def resolve_ref(kind: str, name: str, *, server: str | None = None) -> str:
    """``name@<latest version>`` for an agent / importer / evaluator; an explicit ref passes through.

    ``kitaru session import`` demands EXACT ``NAME@VERSION`` references, which an operator should not
    have to look up by hand every time the bootstrap script registers a new version.
    """
    if "@" in name:
        return name
    if kind not in _VERSIONED_KINDS:
        raise ValueError(f"unknown kitaru resource kind {kind!r}")
    item = run_kitaru([kind, "get", name], server=server).get("item") or {}
    latest = item.get("latest_version")
    if not isinstance(latest, int) or latest < 1:
        raise KitaruCommandError(
            f"{kind} {name!r} has no registered version yet — run "
            "`uv run python scripts/bootstrap_kitaru.py` against this server first."
        )
    return f"{name}@{latest}"


def list_sessions(
    *,
    agent: str | None = None,
    extra: list[str] | None = None,
    server: str | None = None,
) -> list[dict[str, Any]]:
    """Every session matching the filters, paged to exhaustion.

    Since kitaru 0.24 a listed session carries no payloads — names, ids, tags and metadata only,
    which is exactly what both joins here need (Session Name for a recorded run, ``external_id`` for
    an imported one).
    """
    args = ["session", "list", "--size", str(SESSION_PAGE_SIZE)]
    if agent:
        args += ["--agent", agent]
    args += list(extra or [])

    sessions: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page = run_kitaru([*args, *(["--cursor", cursor] if cursor else [])], server=server)
        sessions.extend(item for item in page.get("items") or [] if isinstance(item, dict))
        cursor = ((page.get("page") or {}) if isinstance(page.get("page"), dict) else {}).get(
            "next_cursor"
        )
        if not cursor:
            return sessions


def format_argv(argv: list[str]) -> str:
    """One paste-able line for an argv — what ``--dry-run`` and every error message print."""
    return shlex.join(argv)


def _envelope(completed: subprocess.CompletedProcess[str], argv: list[str]) -> dict[str, Any]:
    """Parse one CLI envelope, turning every failure shape into one error naming the argv.

    stdout OR stderr: verified live on kitaru 0.26.0, a SUCCEEDING sub-command prints its envelope
    to stdout and a FAILING one prints it to stderr. Reading stdout alone turned every ``not_found``
    into "answered with no json envelope" and threw away the ``kind`` callers branch on.
    """
    payload = _parse(completed.stdout) or _parse(completed.stderr)
    if payload is None:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise KitaruCommandError(
            f"`{format_argv(argv)}` (exit {completed.returncode}) answered with no json envelope"
            + (f": {detail[-1]}" if detail else ".")
        ) from None
    if payload.get("ok") is False or completed.returncode != 0:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        message = error.get("message") or f"exit {completed.returncode}"
        raise KitaruCommandError(f"`{format_argv(argv)}` failed: {message}", kind=error.get("kind"))
    return payload


def _parse(stream: str | None) -> dict[str, Any] | None:
    """One json object out of a CLI stream, or ``None`` when there is none to read."""
    try:
        payload = json.loads(stream or "")
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
