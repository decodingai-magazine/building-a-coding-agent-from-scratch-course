"""The Skill entity for the Skills Catalog (ADR-0004 §6).

A :class:`SkillDef` is the parsed + validated result of one skill's ``SKILL.md`` (frontmatter +
Markdown body). A skill is pure injected guidance — no ``tools``/``mode``/``allow``/``deny``
fields; the actions it describes still ride their own tool gates. ``source`` is a provenance
label; ``resource_dir`` is the optional tier-3 bundled-resource directory (project skills only).
The entity owns its validation and never touches disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SkillDef:
    """One skill: catalog ``name`` + ``description`` + Markdown ``body`` + ``source`` (ADR-0004 §6).

    ``name`` is the canonical key from the ``name:`` frontmatter (not the directory name);
    ``source`` is ``"builtin"`` or the project ``SKILL.md`` path; ``resource_dir`` is ``None``
    for built-ins and resource-less project skills, and never validated against the filesystem.
    Construction rejects any empty string field with a :class:`ValueError` naming it.
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
