"""The Skill entity for the Skills Catalog (ADR-0004 §6).

A :class:`SkillDef` is the parsed + **validated** result of one skill's ``SKILL.md`` (the Agent
Skills directory convention: a skill is a directory ``<name>/SKILL.md`` —
``src/decode/skills/builtin/<name>/SKILL.md`` for a built-in, ``<cwd>/.decode/skills/<name>/SKILL.md``
for a project skill — YAML frontmatter + a Markdown body). A skill is *pure injected guidance*: the
catalog advertises its ``name`` + ``description`` cheaply on every turn, and the dispatcher returns
its ``body`` on demand. The ``source`` is a provenance label (``"builtin"`` for a packaged skill, or
the absolute ``SKILL.md`` path for a discovered one) so a project override stays traceable in logs
(ADR-0004 §3).

``resource_dir`` is the tier-3 role (ADR-0004 §5): a **project** skill may ship sibling resource
folders/files (``references/``, ``examples/``, ``scripts/``, …) alongside its ``SKILL.md``. When it
does, ``resource_dir`` names the skill's directory so the dispatcher (task 033) can tell the model
where to ``read`` from; a built-in or a resource-less project skill carries ``None``. Built-ins are
always ``None`` — their resources would live unreadable in site-packages (ADR-0004 §3).

Unlike :class:`~decode.entities.agent_def.AgentDef`, a ``SkillDef`` carries **no**
``tools`` / ``mode`` / ``allow`` / ``deny`` fields — a skill is instructions the model follows, not
a persona and not code; the actions it *describes* still ride their own tool gates (ADR-0004 §6-7).
Adding such a field later is forward-compatible: the loader ignores unknown frontmatter keys.

The entity owns its validation (the loader just hands it parsed frontmatter): construction rejects
an empty-or-whitespace ``name`` / ``description`` / ``body`` / ``source`` with a clear
:class:`ValueError` naming the offending field (and the skill ``name`` where it is known), exactly
like :meth:`AgentDef.__post_init__`. ``resource_dir`` is **not** validated against the filesystem —
the entity is frozen + slotted and must not touch disk; the loader sets it correctly. Frozen +
slotted like the other entities so it is cheap and safe to pass across the loop boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SkillDef:
    """One skill: catalog ``name`` + ``description`` + Markdown ``body`` + ``source`` (ADR-0004 §6).

    ``name`` is the skill's canonical key (from the ``SKILL.md`` ``name:`` frontmatter, not the
    directory name) that the dispatcher resolves and the catalog lists. ``description`` is the
    one-line summary shown in the Skills Catalog. ``body`` is the full Markdown instructions returned
    on demand. ``source`` is the provenance label (``"builtin"`` or the project ``SKILL.md`` path).
    ``resource_dir`` is the optional tier-3 bundled-resource directory (set only for a project skill
    that ships sibling resources; ``None`` for built-ins and resource-less project skills). Construction
    validates every string field is non-empty (after :meth:`str.strip`) and raises :class:`ValueError`
    naming the first offending field; ``resource_dir`` accepts ``None`` or any :class:`~pathlib.Path`
    and is never validated against the filesystem.
    """

    name: str
    description: str
    body: str
    source: str
    resource_dir: Path | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("skill name must be a non-empty string")
        if not self.description.strip():
            raise ValueError(f"skill {self.name!r} must have a non-empty description")
        if not self.body.strip():
            raise ValueError(f"skill {self.name!r} must have a non-empty body")
        if not self.source.strip():
            raise ValueError(f"skill {self.name!r} must have a non-empty source")
