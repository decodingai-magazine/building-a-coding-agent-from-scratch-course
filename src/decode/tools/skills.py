"""The ungated ``skill`` dispatcher — returns a skill's full body on demand (ADR-0004 §2,§7).

``skill`` is the model-facing, on-demand half of progressive disclosure: the Skills Catalog
advertises each skill's ``name`` + ``description`` cheaply on every turn, and this dispatcher returns
the named skill's full Markdown ``body`` as the tool result so the model can follow it. It is the
catalog's read side — it loads instructions, it does not act.

It mirrors the ungated control tools (:mod:`decode.tools.sleep`, :mod:`decode.tools.orchestration`)
to the letter:

* the signature is ``skill(name)`` only — **no structured** ``args`` (ADR-0004 §2: the lazy v1
  catalog has no built-in that needs them; adding ``args`` later is forward-compatible);
* an **unknown** ``name`` is a model mistake, not a crash: it raises a model-readable
  :class:`pydantic_ai.ModelRetry` listing the available skill names so the model corrects the call,
  exactly like ``sleep``'s ``ModelRetry`` on a bad ``seconds``.

**Ungated (ADR-0004 §7).** Loading instructions is harmless, so — like ``ask_user`` and the
orchestration controls — ``skill`` never raises :class:`pydantic_ai.ApprovalRequired` and so never
reaches the permission gate; it stays callable in any mode, including plan mode. Crucially, the
*actions a skill describes* still ride **their own** tool gates: the ``commit`` skill's body tells
the model to run ``git add`` / ``git commit`` via the gated ``bash`` tool, so default mode asks
before that commit and plan mode denies it. Returning the skill body grants no new authority — it is
the gated ``bash`` / ``write`` / ``edit`` calls the body induces that the gate still governs.
"""

from __future__ import annotations

import logging

from pydantic_ai import ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.skills.loader import load_skills

logger = logging.getLogger(__name__)

SKILL_TOOL_NAME = "skill"

__all__ = ["SKILL_TOOL_NAME", "skill"]


async def skill(ctx: RunContext[AgentDeps], name: str) -> str:
    """Return the ``body`` of the skill called ``name`` from the merged catalog (ADR-0004 §2,§7).

    Loads the merged Skills Catalog for the run's ``cwd`` (built-ins overlaid by any project skills
    under ``<cwd>/<settings.skills_dir>``) via :func:`decode.skills.loader.load_skills`, looks ``name``
    up as a **dict key only** (it is never interpolated into a filesystem path or a shell command),
    and returns the matched skill's full Markdown ``body``. An **unknown** ``name`` raises a
    model-readable :class:`pydantic_ai.ModelRetry` listing the available skill names so the model
    retries with a real one instead of crashing the turn.

    Ungated: ``skill`` never raises :class:`pydantic_ai.ApprovalRequired`, so it never reaches the
    permission gate and stays callable in any mode (loading instructions is harmless). The actions
    the returned body describes still pass their own gates — e.g. the ``commit`` skill's ``git add`` /
    ``git commit`` run through the gated ``bash`` tool, which default mode asks for and plan mode
    denies (ADR-0004 §7).
    """
    catalog = load_skills(ctx.deps.cwd)
    found = catalog.get(name)
    if found is None:
        available = ", ".join(sorted(catalog))
        logger.debug("skill dispatcher: unknown skill %r (available: %s)", name, available)
        raise ModelRetry(f"No skill named {name!r}. Available skills: {available}.")
    logger.debug("skill dispatcher returning %r body (source=%s)", name, found.source)
    return found.body
