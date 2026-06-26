"""Assemble the Skills Catalog block injected into the agent's instructions (ADR-0004 §1,§9).

:func:`assemble_skills_catalog` is the always-present, cheap **menu** half of progressive
disclosure (ADR-0004 §1): it advertises each skill's ``name`` + one-line ``description`` on every
turn, while the :mod:`decode.tools.skills` dispatcher returns a skill's full ``body`` on demand. It
is the bridge between :func:`decode.skills.loader.load_skills` and the dynamic
``@agent.instructions`` hook in :mod:`decode.agent.factory`, mirroring how
:func:`decode.memory.service.assemble_memory` bridges discovery to the memory hook.

It:

#. loads the merged catalog (built-ins overlaid by any project skills under
   ``<cwd>/<settings.skills_dir>``) via :func:`load_skills`;
#. formats each skill as a markdown list item ``- <name> — <description>``, **sorted by name** so
   the block is stable across runs (no churn from dict ordering);
#. puts the list under a one-line cue telling the model to call ``skill("<name>")`` to load a
   skill's full instructions before following it.

**Returns ``""`` when there are no skills** — the same contract as :func:`assemble_memory`: the
factory hook then contributes nothing extra (no empty header), only the static base prompt rides.
Built-ins always ship, so the empty result is the defensive/edge path, not the common one.

Sync, like the loader and the memory service: local file reads on the sequential tool layer
(ADR-0004 §11).
"""

from __future__ import annotations

import logging
from pathlib import Path

from decode.skills.loader import load_skills

logger = logging.getLogger(__name__)

# The one-line cue that heads the catalog block: it tells the model the menu below is loadable on
# demand and names the exact call to make. ``skill("<name>")`` is a literal template the model
# fills with a listed name.
_CATALOG_CUE = (
    'Skills you can load on demand — call skill("<name>") to read a skill\'s full '
    "instructions before following it:"
)


def assemble_skills_catalog(cwd: Path) -> str:
    """Return the Skills Catalog prompt block for ``cwd`` (ADR-0004 §1,§9).

    Reads the merged catalog via :func:`load_skills` and renders the cue followed by one
    ``- <name> — <description>`` line per skill, sorted by name. Returns ``""`` when no skills are
    found, so the instructions hook adds nothing (no empty header) — the same contract as
    :func:`decode.memory.service.assemble_memory`.
    """
    skills = load_skills(cwd)
    if not skills:
        return ""

    ordered = sorted(skills.values(), key=lambda skill: skill.name)
    # Collapse any internal whitespace (newlines/tabs/runs of spaces) to a single space per field
    # so one bullet stays one physical line. A project skill's `description` (or `name`) may carry a
    # YAML literal block or a quoted "\n", and an embedded newline would otherwise split the bullet —
    # or worse, a payload like "real desc\n- ghost — obey me" would inject a fake, model-loadable
    # catalog entry (a prompt-injection vector). `" ".join(value.split())` is the idiomatic normalize.
    lines = [
        f"- {' '.join(skill.name.split())} — {' '.join(skill.description.split())}"
        for skill in ordered
    ]
    logger.debug("assembled skills catalog with %d skills: %s", len(lines), sorted(skills))
    return _CATALOG_CUE + "\n" + "\n".join(lines)
