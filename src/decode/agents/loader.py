"""Read + validate the built-in Agents Catalog from bundled Markdown files (ADR-0003 §5).

Each built-in persona is a ``builtin/*.md`` file: a YAML frontmatter block (delimited by ``---``
lines) carrying ``name`` / ``description`` / ``tools`` / ``mode`` (+ optional ``allow`` / ``deny``),
followed by the system-prompt body. :func:`parse_agent_file` splits and validates one such file into
an :class:`~decode.entities.agent_def.AgentDef`; :func:`load_builtin_agents` does it for every bundled
file and keys them by name; :func:`load_agent` returns one by name (or a clear "no such agent" error);
:func:`load_primary_agent` returns one only if it is a **primary** (rejecting subagents — ADR-0013 §3).

The files are **packaged data** (loaded via :mod:`importlib.resources` from the installed
``decode.agents.builtin`` package), never a hard-coded repo path, so the catalog works from an
installed wheel. Validation is loud: a missing frontmatter block, a missing required key, a bad
``mode``, an unknown tool name, or a malformed rule each raises :class:`ValueError` with a message
naming the offending file/value — a catalog file is authored data the developer must fix, not user
input to tolerate. Unknown frontmatter keys (e.g. a future ``model``) are ignored so the format stays
forward-compatible (ADR-0003 §5).
"""

from __future__ import annotations

import importlib.resources
import logging
from importlib.resources.abc import Traversable

import yaml

from decode.entities.agent_def import AgentDef
from decode.frontmatter import split_frontmatter
from decode.permissions.types import PermissionMode

logger = logging.getLogger(__name__)

# The package the bundled catalog files live in (packaged data, loaded via importlib.resources).
_BUILTIN_PACKAGE = "decode.agents.builtin"


def load_builtin_agents() -> dict[str, AgentDef]:
    """Read + validate every bundled built-in agent, keyed by name (ADR-0003 §5).

    Returns a fresh dict each call (no shared mutable state). Raises :class:`ValueError` if any
    bundled file is malformed — the built-ins ship with the package, so a failure here is a
    packaging bug surfaced loudly rather than a silently dropped persona.
    """
    agents: dict[str, AgentDef] = {}
    for entry in _builtin_files():
        text = entry.read_text(encoding="utf-8")
        try:
            agent = parse_agent_file(text)
        except ValueError as exc:
            raise ValueError(f"invalid built-in agent file {entry.name!r}: {exc}") from exc
        agents[agent.name] = agent
    logger.debug("loaded %d built-in agents: %s", len(agents), sorted(agents))
    return agents


def load_agent(name: str) -> AgentDef:
    """Return the built-in agent named ``name`` — primary *or* subagent (ADR-0003 §5).

    Raises :class:`ValueError` with a message listing every available agent name when ``name`` is not
    a built-in. This is the by-name loader; the two *selection* surfaces (the CLI ``--agent`` guard
    and the ``/agent`` slash command) go through :func:`load_primary_agent`, which additionally
    rejects subagents (ADR-0013 §3).
    """
    agents = load_builtin_agents()
    agent = agents.get(name)
    if agent is None:
        available = ", ".join(sorted(agents))
        raise ValueError(f"no such agent {name!r}; available agents: {available}")
    return agent


def load_primary_agent(name: str) -> AgentDef:
    """Return the built-in **primary** agent named ``name``, rejecting subagents (ADR-0013 §3).

    A *primary* is a persona selectable as the one main agent (``subagent is False`` — build / plan /
    code-reviewer); a *subagent* (explore, ``subagent: true``) is spawnable only via the Agent tool.
    This is the shared guard behind both selection surfaces — the CLI ``--agent`` flag and the
    ``/agent`` slash command. It raises :class:`ValueError` listing only the **primary** names when
    ``name`` is unknown *or* names a subagent, so the caller can render one friendly primaries-only
    line (and, because the raise precedes any mutation in ``select_agent``, the session stays intact).
    """
    agents = load_builtin_agents()
    primaries = ", ".join(sorted(n for n, a in agents.items() if not a.subagent))
    agent = agents.get(name)
    if agent is None:
        raise ValueError(f"no such agent {name!r}; available agents: {primaries}")
    if agent.subagent:
        raise ValueError(
            f"agent {name!r} is a subagent and cannot be selected as a main agent; "
            f"available agents: {primaries}"
        )
    return agent


def parse_agent_file(text: str) -> AgentDef:
    """Parse one catalog Markdown file (frontmatter + body) into an :class:`AgentDef`.

    Splits the leading ``---``-fenced YAML frontmatter from the prompt body, validates the required
    keys (``name`` / ``description`` / ``tools`` / ``mode``) and the optional rule lists, and lets
    :class:`AgentDef` enforce the rest (unknown tool name, empty name/prompt, malformed rule). Raises
    :class:`ValueError` on any structural problem.
    """
    frontmatter, body = split_frontmatter(text)
    meta = yaml.safe_load(frontmatter)
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a YAML mapping of agent fields")
    return AgentDef(
        name=_require_str(meta, "name"),
        description=_require_str(meta, "description"),
        tools=_require_str_tuple(meta, "tools"),
        mode=_parse_mode(meta.get("mode")),
        allow=_optional_str_tuple(meta, "allow"),
        deny=_optional_str_tuple(meta, "deny"),
        subagent=_optional_bool(meta, "subagent"),
        prompt=body.strip(),
    )


def _builtin_files() -> list[Traversable]:
    """The bundled ``builtin/*.md`` catalog files, sorted by name (packaged data)."""
    package = importlib.resources.files(_BUILTIN_PACKAGE)
    return sorted(
        (entry for entry in package.iterdir() if entry.name.endswith(".md")),
        key=lambda entry: entry.name,
    )


def _parse_mode(raw: object) -> PermissionMode:
    """Parse the ``mode`` frontmatter value into a :class:`PermissionMode`."""
    if not isinstance(raw, str):
        raise ValueError("'mode' is required and must be a string")
    try:
        return PermissionMode(raw.strip())
    except ValueError:
        valid = ", ".join(mode.value for mode in PermissionMode)
        raise ValueError(f"unknown mode {raw!r}; valid modes: {valid}") from None


def _require_str(meta: dict[str, object], key: str) -> str:
    """Read a required string frontmatter field."""
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' is required and must be a non-empty string")
    return value


def _require_str_tuple(meta: dict[str, object], key: str) -> tuple[str, ...]:
    """Read a required list-of-strings frontmatter field (e.g. ``tools``)."""
    value = meta.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"'{key}' is required and must be a list of strings")
    return tuple(value)


def _optional_str_tuple(meta: dict[str, object], key: str) -> tuple[str, ...]:
    """Read an optional list-of-strings frontmatter field (e.g. ``allow`` / ``deny``)."""
    value = meta.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"'{key}' must be a list of strings when present")
    return tuple(value)


def _optional_bool(meta: dict[str, object], key: str) -> bool:
    """Read an optional boolean frontmatter field (e.g. ``subagent``; ADR-0013 §3).

    Absent → ``False``. Present-but-not-a-bool raises :class:`ValueError` naming the key — mirroring
    :func:`_optional_str_tuple`'s loud validation (a catalog file is authored data the developer must
    fix, not user input to tolerate). ``isinstance(value, bool)`` also rejects an int like ``1``
    (``bool`` is a strict subtype), so only a real YAML boolean (``true`` / ``false``) is accepted.
    """
    value = meta.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"'{key}' must be a boolean when present")
    return value
