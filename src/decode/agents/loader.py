"""Read + validate the built-in Agents Catalog from bundled Markdown files (ADR-0003 §5).

Each ``builtin/*.md`` persona is YAML frontmatter (``name`` / ``description`` / ``tools`` /
``mode`` + optional ``allow`` / ``deny`` / ``subagent``) followed by the system-prompt body. The
files are packaged data (:mod:`importlib.resources`), so the catalog works from an installed
wheel. Validation is loud — any structural problem raises :class:`ValueError` naming the
offending file/value; unknown frontmatter keys are ignored for forward compatibility.
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

# The package the bundled catalog files live in.
_BUILTIN_PACKAGE = "decode.agents.builtin"


def load_builtin_agents() -> dict[str, AgentDef]:
    """Read + validate every bundled built-in agent, keyed by name (ADR-0003 §5).

    Returns a fresh dict each call. A malformed bundled file raises :class:`ValueError` — a
    packaging bug surfaced loudly, not a silently dropped persona.
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

    Raises :class:`ValueError` listing every available agent name when ``name`` is unknown. The
    selection surfaces (CLI ``--agent``, ``/agent``) go through :func:`load_primary_agent` instead.
    """
    agents = load_builtin_agents()
    agent = agents.get(name)
    if agent is None:
        available = ", ".join(sorted(agents))
        raise ValueError(f"no such agent {name!r}; available agents: {available}")
    return agent


def load_primary_agent(name: str) -> AgentDef:
    """Return the built-in **primary** agent named ``name``, rejecting subagents (ADR-0013 §3).

    The shared guard behind both selection surfaces (CLI ``--agent``, ``/agent`` slash command).
    Raises :class:`ValueError` listing only the **primary** names when ``name`` is unknown *or*
    names a subagent, so the caller can render one friendly primaries-only line.
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

    Raises :class:`ValueError` on any structural problem; :class:`AgentDef` enforces the rest.
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
    package = importlib.resources.files(_BUILTIN_PACKAGE)
    return sorted(
        (entry for entry in package.iterdir() if entry.name.endswith(".md")),
        key=lambda entry: entry.name,
    )


def _parse_mode(raw: object) -> PermissionMode:
    if not isinstance(raw, str):
        raise ValueError("'mode' is required and must be a string")
    try:
        return PermissionMode(raw.strip())
    except ValueError:
        valid = ", ".join(mode.value for mode in PermissionMode)
        raise ValueError(f"unknown mode {raw!r}; valid modes: {valid}") from None


def _require_str(meta: dict[str, object], key: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' is required and must be a non-empty string")
    return value


def _require_str_tuple(meta: dict[str, object], key: str) -> tuple[str, ...]:
    value = meta.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"'{key}' is required and must be a list of strings")
    return tuple(value)


def _optional_str_tuple(meta: dict[str, object], key: str) -> tuple[str, ...]:
    value = meta.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"'{key}' must be a list of strings when present")
    return tuple(value)


def _optional_bool(meta: dict[str, object], key: str) -> bool:
    """Absent → ``False``; the ``isinstance`` check also rejects ints — only a real YAML boolean."""
    value = meta.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"'{key}' must be a boolean when present")
    return value
