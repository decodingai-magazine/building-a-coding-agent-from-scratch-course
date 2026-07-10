"""The ungated ``skill`` dispatcher — returns a skill's payload on demand.

The on-demand half of progressive disclosure: the catalog advertises name + description cheaply;
this dispatcher returns the named skill's full body (via the shared ``format_skill_payload``
helper the ``/<skill-name>`` TUI command also uses). An unknown name raises a model-readable
:class:`pydantic_ai.ModelRetry` listing the available skills. Ungated — loading instructions is
harmless and grants no new authority: the gated ``bash``/``write``/``edit`` calls a body induces
are what the gate still governs. See ADR-0004 §2,§5,§7.
"""

from __future__ import annotations

import logging

from pydantic_ai import ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.skills.loader import load_skills
from decode.skills.payload import format_skill_payload

logger = logging.getLogger(__name__)

SKILL_TOOL_NAME = "skill"

__all__ = ["SKILL_TOOL_NAME", "skill"]


async def skill(ctx: RunContext[AgentDeps], name: str) -> str:
    """Return the payload of the skill called ``name`` from the merged catalog (ADR-0004 §2,§5,§7).

    Loads the merged Skills Catalog for the run's ``cwd`` (built-ins overlaid by any project skills
    under ``<cwd>/<settings.skills_dir>``) via :func:`decode.skills.loader.load_skills`, looks ``name``
    up as a **dict key only** (it is never interpolated into a filesystem path or a shell command),
    and returns the matched skill's payload from the shared
    :func:`decode.skills.payload.format_skill_payload` helper — the Markdown ``body``, plus a resource
    trailer naming the skill's cwd-relative directory **iff** the skill ships bundled resources
    (``resource_dir`` set); a built-in or a resource-less project skill returns the body unchanged. An
    **unknown** ``name`` raises a model-readable :class:`pydantic_ai.ModelRetry` listing the available
    skill names so the model retries with a real one instead of crashing the turn.

    Ungated: ``skill`` never raises :class:`pydantic_ai.ApprovalRequired`, so it never reaches the
    permission gate and stays callable in any mode (loading instructions is harmless). The actions
    the returned body describes still pass their own gates — e.g. the ``commit`` skill's ``git add`` /
    ``git commit`` run through the gated ``bash`` tool, which default mode asks for and plan mode
    denies (ADR-0004 §7).
    """
    # Skills are HARNESS artifacts: load from ``harness_home`` (the launch cwd), not ``cwd`` — in a
    # sandbox mode the catalog stays anchored at Harness Home, and because skills are seeded into
    # the workspace too, the same relative path resolves for workspace-scoped tools (ADR-0012 §5,6).
    home = ctx.deps.harness_home or ctx.deps.cwd
    catalog = load_skills(home)
    found = catalog.get(name)
    if found is None:
        available = ", ".join(sorted(catalog))
        logger.debug("skill dispatcher: unknown skill %r (available: %s)", name, available)
        raise ModelRetry(f"No skill named {name!r}. Available skills: {available}.")
    logger.debug("skill dispatcher returning %r payload (source=%s)", name, found.source)
    return format_skill_payload(found, cwd=home)
