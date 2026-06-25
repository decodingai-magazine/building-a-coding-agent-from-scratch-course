"""The Agents Catalog: built-in personas as bundled Markdown + a loader (ADR-0003 §5).

Each built-in agent (Build / Plan / Explore / Code-Reviewer) is a Markdown file under
:mod:`decode.agents.builtin` — YAML frontmatter (``name`` / ``description`` / ``tools`` allowlist /
``mode`` / optional ``allow`` + ``deny`` rules) followed by a system-prompt body. The catalog is a
loader + validator, **not** a hardcoded dict (ADR-0003 §5): :mod:`decode.agents.loader` reads the
files as *packaged data* (``importlib.resources``, so they ship in the wheel), validates each into an
:class:`~decode.entities.agent_def.AgentDef`, and returns them keyed by name.

This task (019) is **pure load + validate** — wiring the active agent into the running agent (its
prompt, tool restriction, rules, and mode) is task 020. Subagent spawning is out of scope this
milestone (the catalog is main-agent only).
"""

from __future__ import annotations

from decode.agents.loader import load_agent, load_builtin_agents

__all__ = ["load_agent", "load_builtin_agents"]
