"""The Agent (persona) entity for the Agents Catalog (ADR-0003 §5).

An :class:`AgentDef` is the parsed + **validated** result of one Agents Catalog Markdown file
(``src/decode/agents/builtin/*.md`` — YAML frontmatter + a system-prompt body). It scopes a
persona three ways: the system ``prompt``, the ``tools`` allowlist, and the default ``mode`` the
gate resets to when this agent is selected. It also carries the agent's *own* optional ``allow`` /
``deny`` Permission Rules so a built-in's defaults (e.g. code-reviewer's ``bash(git *)``) live in
the catalog, not seeded into the user's ``.decode/settings.json`` (ADR-0003 §4).

There is **no** ``model`` field — agents run on the one configured Gemini model until the providers
milestone (step 3); the loader ignores unknown frontmatter keys, so adding ``model`` later is
forward-compatible.

The entity owns its validation (the loader just hands it parsed frontmatter): construction rejects
an unknown tool name (clear error naming the tool), an empty ``name`` or ``prompt``, and a malformed
``allow`` / ``deny`` rule string (reusing task 018's :func:`decode.permissions.rules.parse_rule`).
Frozen + slotted like the other entities so it is cheap and safe to pass across the loop boundary
(it rides ``AgentDeps.active_agent`` in task 020).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from decode.permissions.rules import Rule, parse_rule
from decode.permissions.types import PermissionMode
from decode.tools import KNOWN_TOOL_NAMES


@dataclass(frozen=True, slots=True)
class AgentDef:
    """One Agents Catalog persona: prompt + tool allowlist + default mode + agent rules (ADR-0003 §5).

    ``tools`` is the allowlist of tool names this agent may call (validated against
    :data:`decode.tools.KNOWN_TOOL_NAMES` — the registered tools plus the orchestration tools).
    ``mode``
    is the :class:`~decode.permissions.types.PermissionMode` the gate resets to on selection.
    ``allow`` / ``deny`` are optional agent-scoped Permission Rule *strings* (``Tool(pattern)`` or
    bare ``Tool``), parsed once at construction into :attr:`allow_rules` / :attr:`deny_rules`.
    ``prompt`` is the system-prompt body. Construction validates every field and raises
    :class:`ValueError` with a clear message on the first problem.
    """

    name: str
    description: str
    tools: tuple[str, ...]
    mode: PermissionMode
    prompt: str
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    allow_rules: tuple[Rule, ...] = field(default=(), init=False)
    deny_rules: tuple[Rule, ...] = field(default=(), init=False)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("agent name must be a non-empty string")
        if not self.prompt.strip():
            raise ValueError(f"agent {self.name!r} must have a non-empty prompt")
        unknown = [tool for tool in self.tools if tool not in KNOWN_TOOL_NAMES]
        if unknown:
            known = ", ".join(sorted(KNOWN_TOOL_NAMES))
            raise ValueError(
                f"agent {self.name!r} lists unknown tool(s) {unknown}; known tools: {known}"
            )
        # field(init=False) on a frozen dataclass: set via object.__setattr__ in __post_init__.
        object.__setattr__(self, "allow_rules", self._parse_rules(self.allow))
        object.__setattr__(self, "deny_rules", self._parse_rules(self.deny))

    def _parse_rules(self, raw: tuple[str, ...]) -> tuple[Rule, ...]:
        """Parse a tuple of rule strings into :class:`Rule`, surfacing a malformed one loudly.

        Unlike the user's ``settings.json`` (where one bad rule is skipped so a typo never breaks a
        session), a malformed rule in a *catalog* file is a packaging error the author must fix, so
        it raises with the agent name and the offending string.
        """
        parsed: list[Rule] = []
        for text in raw:
            try:
                parsed.append(parse_rule(text))
            except ValueError as exc:
                raise ValueError(
                    f"agent {self.name!r} has a malformed permission rule {text!r}: {exc}"
                ) from exc
        return tuple(parsed)
