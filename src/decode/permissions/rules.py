"""The Permission Rule engine: parse, load, and match allow/deny rules (ADR-0003 §4).

A **Permission Rule** is the string ``Tool(pattern)`` or a bare ``Tool``, parsed into a
:class:`Rule` (``tool_name`` + optional ``pattern``). It is matched (glob via :mod:`fnmatch`)
against a per-kind **subject** — the string the human cares about for that call:

* ``bash`` → the command;
* file tools (``read`` / ``write`` / ``edit`` / ``glob`` / ``grep``) → the path;
* ``web_fetch`` → the url;
* everything else → the tool name (so a bare ``Tool`` rule still matches by name).

A bare ``Tool`` rule (no pattern) matches *any* call of that tool.

Rules come from the user's optional ``.decode/settings.json``
(``{"permissions": {"allow": [...], "deny": [...]}}``) — the only rule source this task wires;
the agent catalog (task 020) reuses the same :class:`RuleSet` engine. A missing file yields an
empty :class:`RuleSet` silently (the file is optional); a malformed file is non-fatal too — it is
logged and treated as no rules so a typo never breaks a session.

The engine is **policy data**, not the decision: :class:`~decode.permissions.gate.PermissionGate`
owns precedence (deny → allow → mode). This module only parses, loads, and answers "does this rule
match this request?".
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

# The JSON args field that carries each tool kind's subject (the thing rules glob against). A tool
# whose name is not here (or whose args lack the field) falls back to matching on the tool name,
# so a bare ``Tool`` rule still matches.
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
    """A parsed Permission Rule: a ``tool_name`` and an optional glob ``pattern`` (ADR-0003 §4).

    ``pattern is None`` is a **bare** rule (``Tool``) that matches any call of ``tool_name``; a
    non-``None`` pattern is globbed (``fnmatch``) against the request's subject. Construct via
    :func:`parse_rule` rather than the raw fields.
    """

    tool_name: str
    pattern: str | None = None


@dataclass(slots=True)
class RuleSet:
    """An allow list + a deny list of :class:`Rule` (ADR-0003 §4).

    The :class:`~decode.permissions.gate.PermissionGate` holds one of these per rule source (the
    user ``.decode/settings.json`` here; the active agent's catalog rules from task 020) and
    evaluates them with deny-beats-allow precedence. :meth:`matching_deny` / :meth:`matching_allow`
    return the first rule that matches a request (or ``None``), so the gate can cite which rule
    fired. Mutable (not frozen) because the always-allow flow reloads the user set in place.
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
    """Parse a rule string ``Tool(pattern)`` or bare ``Tool`` into a :class:`Rule`.

    Surrounding whitespace (and whitespace inside the parens) is stripped. Raises
    :class:`ValueError` when the tool name is empty (e.g. ``""``, ``"()"``, ``"(pattern)"``) — the
    loader catches it so one bad rule string never sinks a whole settings file.
    """
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
    """Extract the per-kind **subject** a rule globs against (ADR-0003 §4).

    ``bash`` → the command, file tools → the path, ``web_fetch`` → the url; anything else (or a
    missing field, or unparseable ``args_json``) falls back to the tool name — which still lets a
    bare ``Tool`` rule match. Never raises: a malformed args blob yields the tool name.
    """
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
    """Whether ``rule`` matches ``request`` (ADR-0003 §4).

    The tool names must be equal. A bare rule (``pattern is None``) then matches any such call; a
    pattern rule globs (``fnmatch``) against ``request.subject``.
    """
    if rule.tool_name != request.tool_name:
        return False
    if rule.pattern is None:
        return True
    return fnmatch.fnmatch(request.subject, rule.pattern)


def allow_rule_string(request: PermissionRequest) -> str:
    """The persistable allow-rule string for ``request`` (ADR-0003 §4 always-allow).

    ``Tool(subject)`` when the request carries a meaningful subject; a **bare** ``Tool`` when the
    subject is empty or is just the tool name (no per-kind subject to scope by). This is what the
    interactive ``a``/``always`` answer writes so the next identical call auto-allows.
    """
    if request.subject and request.subject != request.tool_name:
        return f"{request.tool_name}({request.subject})"
    return request.tool_name


def persist_allow_rule(path: Path, request: PermissionRequest) -> None:
    """Append a matching allow rule for ``request`` to the user ``.decode/settings.json``.

    Reads the existing file (treating a missing/malformed file as the empty shape), appends
    :func:`allow_rule_string` to ``permissions.allow`` if not already present (idempotent), and
    writes the file back, **preserving** any unrelated top-level keys and the existing ``deny``
    list. Creates the parent directory if needed. May raise :class:`OSError` (a write failure) —
    the caller (the TUI resolver) treats that as non-fatal and falls back to allow-once.
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
    """Load the user ``.decode/settings.json`` at ``path`` into a :class:`RuleSet` (ADR-0003 §4).

    Reads the shape ``{"permissions": {"allow": [...], "deny": [...]}}``. A missing file is the
    common case (the file is optional) and yields an empty set **silently**. A malformed file
    (bad JSON, or an unexpected shape) is non-fatal: it is logged at WARNING and yields an empty
    set, so a typo never breaks a session. One unparseable rule *string* is skipped (logged) while
    the rest of the file still loads.
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
