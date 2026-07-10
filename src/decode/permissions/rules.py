"""The Permission Rule engine: parse, load, and match allow/deny rules (ADR-0003 §4).

A Permission Rule is ``Tool(pattern)`` or a bare ``Tool``, globbed (:mod:`fnmatch`) against a
per-kind **subject**: ``bash`` → the command, file tools → the path, ``web_fetch`` → the url,
everything else → the tool name. A bare rule matches any call of that tool. Rules load from the
optional ``.decode/settings.json`` (missing/malformed files are non-fatal); the agent catalog
reuses the same :class:`RuleSet`. This module is policy data only — the gate owns precedence.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decode.entities.permissions import PermissionRequest

logger = logging.getLogger(__name__)

# The JSON args field carrying each tool's subject; unlisted tools fall back to the tool name.
_SUBJECT_FIELD: dict[str, str] = {
    "bash": "command",
    "read": "path",
    "write": "path",
    "edit": "path",
    "glob": "path",
    "grep": "path",
    "web_fetch": "url",
}


@dataclass(frozen=True, slots=True)
class Rule:
    """A parsed Permission Rule: ``tool_name`` + optional glob ``pattern`` (``None`` = bare rule).

    Construct via :func:`parse_rule` rather than the raw fields.
    """

    tool_name: str
    pattern: str | None = None


@dataclass(slots=True)
class RuleSet:
    """An allow list + a deny list of :class:`Rule`; the gate holds one per rule source (ADR-0003 §4).

    Mutable (not frozen) because the always-allow flow reloads the user set in place.
    """

    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def matching_allow(self, request: PermissionRequest) -> Rule | None:
        """The first allow rule that matches ``request`` (or ``None``)."""
        return _first_match(self.allow, request)

    def matching_deny(self, request: PermissionRequest) -> Rule | None:
        """The first deny rule that matches ``request`` (or ``None``)."""
        return _first_match(self.deny, request)


def parse_rule(text: str) -> Rule:
    """Parse ``Tool(pattern)`` or bare ``Tool`` into a :class:`Rule`; ValueError on an empty name."""
    stripped = text.strip()
    open_paren = stripped.find("(")
    if open_paren == -1:
        tool_name = stripped
        pattern: str | None = None
    else:
        if not stripped.endswith(")"):
            raise ValueError(f"malformed rule (unbalanced parens): {text!r}")
        tool_name = stripped[:open_paren].strip()
        pattern = stripped[open_paren + 1 : -1].strip()
    if not tool_name:
        raise ValueError(f"rule is missing a tool name: {text!r}")
    return Rule(tool_name=tool_name, pattern=pattern)


def subject_for(tool_name: str, args_json: str) -> str:
    """Extract the per-kind subject a rule globs against; falls back to the tool name. Never raises."""
    field_name = _SUBJECT_FIELD.get(tool_name)
    if field_name is None:
        return tool_name
    try:
        args = json.loads(args_json)
    except (json.JSONDecodeError, TypeError):
        return tool_name
    value = args.get(field_name) if isinstance(args, dict) else None
    if isinstance(value, str) and value:
        return value
    return tool_name


def matches(rule: Rule, request: PermissionRequest) -> bool:
    """Whether ``rule`` matches ``request``: equal tool name, then bare-or-glob on the subject."""
    if rule.tool_name != request.tool_name:
        return False
    if rule.pattern is None:
        return True
    return fnmatch.fnmatch(request.subject, rule.pattern)


def allow_rule_string(request: PermissionRequest) -> str:
    """The persistable always-allow rule: ``Tool(subject)``, or bare ``Tool`` without a subject."""
    if request.subject and request.subject != request.tool_name:
        return f"{request.tool_name}({request.subject})"
    return request.tool_name


def persist_allow_rule(path: Path, request: PermissionRequest) -> None:
    """Append an allow rule for ``request`` to ``permissions.allow`` in the settings file.

    Idempotent; preserves unrelated keys. May raise :class:`OSError` — the caller treats a write
    failure as non-fatal (allow-once).
    """
    rule = allow_rule_string(request)
    data = _read_settings(path)
    permissions = data.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {}
        data["permissions"] = permissions
    allow = permissions.setdefault("allow", [])
    if not isinstance(allow, list):
        allow = []
        permissions["allow"] = allow
    if rule not in allow:
        allow.append(rule)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    logger.debug("persisted allow rule %r to %s", rule, path)


def _read_settings(path: Path) -> dict[str, object]:
    """Read the settings JSON into a dict, treating missing/malformed as the empty shape."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("overwriting malformed permissions settings.json at %s", path)
        return {}
    return data if isinstance(data, dict) else {}


def load_rule_set(path: Path) -> RuleSet:
    """Load ``{"permissions": {"allow": [...], "deny": [...]}}`` at ``path`` into a :class:`RuleSet`.

    A missing file yields an empty set silently; a malformed file is logged and yields an empty
    set; one unparseable rule string is skipped while the rest still loads.
    """
    if not path.exists():
        return RuleSet()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("ignoring malformed permissions settings.json at %s", path, exc_info=True)
        return RuleSet()
    permissions = data.get("permissions") if isinstance(data, dict) else None
    if not isinstance(permissions, dict):
        return RuleSet()
    return RuleSet(
        allow=_parse_rule_list(permissions.get("allow")),
        deny=_parse_rule_list(permissions.get("deny")),
    )


def _parse_rule_list(raw: object) -> list[Rule]:
    """Parse a JSON list of rule strings into :class:`Rule`, skipping (logging) bad entries."""
    if not isinstance(raw, list):
        return []
    parsed: list[Rule] = []
    for entry in raw:
        if not isinstance(entry, str):
            logger.warning("ignoring non-string permission rule: %r", entry)
            continue
        try:
            parsed.append(parse_rule(entry))
        except ValueError:
            logger.warning("ignoring unparseable permission rule: %r", entry)
    return parsed


def _first_match(candidates: list[Rule], request: PermissionRequest) -> Rule | None:
    """The first rule in ``candidates`` that matches ``request`` (or ``None``)."""
    for rule in candidates:
        if matches(rule, request):
            return rule
    return None
