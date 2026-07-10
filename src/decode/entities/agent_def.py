"""The Agent (persona) entity for the Agents Catalog (ADR-0003 §5).

An :class:`AgentDef` is the parsed + validated result of one catalog Markdown file (YAML
frontmatter + system-prompt body). It scopes a persona by ``prompt``, ``tools`` allowlist, and
default ``mode``, and carries the agent's own optional ``allow``/``deny`` Permission Rules.
There is no ``model`` field — agents ride the configured model. The entity owns its validation;
frozen + slotted like the other entities.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from decode.permissions.rules import Rule, parse_rule
from decode.permissions.types import PermissionMode
from decode.tools import KNOWN_TOOL_NAMES


@dataclass(frozen=True, slots=True)
class AgentDef:
    """One Agents Catalog persona: prompt + tool allowlist + default mode + agent rules (ADR-0003 §5).

    ``tools`` is validated against :data:`decode.tools.KNOWN_TOOL_NAMES`; ``allow`` / ``deny``
    rule strings are parsed once at construction into :attr:`allow_rules` / :attr:`deny_rules`.
    ``subagent`` places the persona on the primary/subagent axis (ADR-0013 §3): ``False`` is
    selectable as the main agent, ``True`` is spawnable only via the Agent tool. Construction
    raises :class:`ValueError` on the first problem.
    """

    name: str
    description: str
    tools: tuple[str, ...]
    mode: PermissionMode
    prompt: str
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    subagent: bool = False
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
        """Parse rule strings, raising loudly — a malformed catalog rule is a packaging error."""
        parsed: list[Rule] = []
        for text in raw:
            try:
                parsed.append(parse_rule(text))
            except ValueError as exc:
                raise ValueError(
                    f"agent {self.name!r} has a malformed permission rule {text!r}: {exc}"
                ) from exc
        return tuple(parsed)
