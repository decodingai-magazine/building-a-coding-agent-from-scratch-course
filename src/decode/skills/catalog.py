"""Assemble the Skills Catalog block injected into the agent's instructions (ADR-0004 §1,§9).

:func:`assemble_skills_catalog` is the cheap **menu** half of progressive disclosure: it
advertises each skill's ``name`` + one-line ``description`` every turn, while the
:mod:`decode.tools.skills` dispatcher returns a skill's full ``body`` on demand. Returns ``""``
when there are no skills — the same contract as :func:`assemble_memory`, so the factory hook
then contributes nothing. Sync, like the loader and the memory service (ADR-0004 §11).
"""

from __future__ import annotations

import logging
from pathlib import Path

from decode.skills.loader import load_skills

logger = logging.getLogger(__name__)

# The cue heading the catalog block; the model fills the skill("<name>") template with a name.
_CATALOG_CUE = (
    'Skills you can load on demand — call skill("<name>") to read a skill\'s full '
    "instructions before following it:"
)


def assemble_skills_catalog(cwd: Path) -> str:
    """Return the Skills Catalog prompt block for ``cwd`` (ADR-0004 §1,§9).

    Renders the cue plus one ``- <name> — <description>`` line per skill, sorted by name for a
    stable block; ``""`` when no skills are found.
    """
    skills = load_skills(cwd)
    if not skills:
        return ""

    ordered = sorted(skills.values(), key=lambda skill: skill.name)
    # Collapse internal whitespace per field so one bullet stays one physical line — an embedded
    # newline in a project skill's name/description could otherwise inject a fake catalog entry
    # (a prompt-injection vector).
    lines = [
        f"- {' '.join(skill.name.split())} — {' '.join(skill.description.split())}"
        for skill in ordered
    ]
    logger.debug("assembled skills catalog with %d skills: %s", len(lines), sorted(skills))
    return _CATALOG_CUE + "\n" + "\n".join(lines)
