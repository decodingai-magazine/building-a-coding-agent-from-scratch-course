"""Read + validate + merge the Skills Catalog from two sources (ADR-0004 §3).

A skill is one Markdown file: a YAML frontmatter block (delimited by ``---`` lines) carrying ``name``
+ ``description``, followed by the instruction body. :func:`parse_skill_file` splits and validates one
such file into a :class:`~decode.entities.skill_def.SkillDef`; the ``source`` provenance label is
supplied by the caller (the one deliberate deviation from :func:`decode.agents.loader.parse_agent_file`
— a skill carries where it came from). The skill's **name is the frontmatter ``name:``** (ADR-0004 §3);
the filename is cosmetic.

Skills come from two sources, with the same asymmetry the agents catalog uses for built-ins vs the
user's ``settings.json`` (ADR-0004 §3):

* :func:`load_builtin_skills` reads the bundled ``builtin/*.md`` files as **packaged data** (via
  :mod:`importlib.resources`, so they ship in the installed wheel), ``source="builtin"``. A built-in
  parse failure **raises loudly** — the built-ins ship with the package, so a failure here is a
  packaging bug, not user input to tolerate.
* :func:`discover_project_skills` scans ``<cwd>/<settings.skills_dir>/*.md`` (cwd-relative, like
  memory), ``source`` set to the absolute file path. A malformed/unreadable project skill is logged at
  WARNING and **skipped** (a user's typo never breaks a session); a missing dir yields ``{}``.

:func:`load_skills` merges built-ins first, then project skills via ``dict.update``, so a project skill
whose frontmatter ``name`` equals a built-in's **intentionally overrides** it (most-specific wins; the
silent override is acceptable — ``source`` keeps it traceable, ADR-0004 §3).
"""

from __future__ import annotations

import importlib.resources
import logging
from importlib.resources.abc import Traversable
from pathlib import Path

import yaml

from decode.config.settings import settings
from decode.entities.skill_def import SkillDef
from decode.frontmatter import split_frontmatter

logger = logging.getLogger(__name__)

# The package the bundled catalog files live in (packaged data, loaded via importlib.resources).
_BUILTIN_PACKAGE = "decode.skills.builtin"

# The provenance label for a bundled built-in skill.
_BUILTIN_SOURCE = "builtin"


def parse_skill_file(text: str, source: str) -> SkillDef:
    """Parse one skill Markdown file (frontmatter + body) into a :class:`SkillDef`.

    Splits the leading ``---``-fenced YAML frontmatter from the body, requires a non-empty string
    ``name`` + ``description`` (the skill's name is the frontmatter ``name:``, not any filename), and
    lets :class:`SkillDef` validate the rest. ``source`` is the provenance label the caller supplies
    (``"builtin"`` or the project file path). Raises :class:`ValueError` on any structural problem —
    missing frontmatter, an unclosed fence, a missing/non-string ``name`` or ``description``, or an
    empty body — so a non-string YAML value (e.g. a list for ``name``) surfaces as a clear error here
    rather than an :class:`AttributeError` from :class:`SkillDef`.
    """
    frontmatter, body = split_frontmatter(text)
    meta = yaml.safe_load(frontmatter)
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a YAML mapping of skill fields")
    return SkillDef(
        name=_require_str(meta, "name"),
        description=_require_str(meta, "description"),
        body=body.strip(),
        source=source.strip(),
    )


def load_builtin_skills() -> dict[str, SkillDef]:
    """Read + validate every bundled built-in skill, keyed by frontmatter name (ADR-0004 §3).

    Returns a fresh dict each call (no shared mutable state), ``source="builtin"``. Raises
    :class:`ValueError` if any bundled file is malformed — the built-ins ship with the package, so a
    failure here is a packaging bug surfaced loudly rather than a silently dropped skill.
    """
    skills: dict[str, SkillDef] = {}
    for entry in _builtin_files():
        text = entry.read_text(encoding="utf-8")
        try:
            skill = parse_skill_file(text, source=_BUILTIN_SOURCE)
        except ValueError as exc:
            raise ValueError(f"invalid built-in skill file {entry.name!r}: {exc}") from exc
        skills[skill.name] = skill
    logger.debug("loaded %d built-in skills: %s", len(skills), sorted(skills))
    return skills


def discover_project_skills(cwd: Path) -> dict[str, SkillDef]:
    """Discover project-local skills under ``<cwd>/<settings.skills_dir>``, keyed by frontmatter name.

    Scans the directory for ``*.md`` files (sorted by name for a deterministic merge order) and parses
    each with ``source`` set to its absolute path. A malformed or unreadable file is logged at WARNING
    and **skipped** so a user's typo never crashes the agent (mirrors memory's skip-unreadable and the
    user ``settings.json`` tolerance). A missing skills directory returns an empty dict.
    """
    skills_dir = cwd / settings.skills_dir
    if not skills_dir.is_dir():
        return {}
    skills: dict[str, SkillDef] = {}
    for path in sorted(skills_dir.glob("*.md")):
        source = str(path.resolve())
        try:
            text = path.read_text(encoding="utf-8")
            skill = parse_skill_file(text, source=source)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            # ``yaml.YAMLError`` (e.g. ``ScannerError`` on a typo'd frontmatter) is NOT a ``ValueError``,
            # so it must be caught explicitly — else a single broken project skill crashes the live
            # session (the loader runs every turn via the catalog hook). The built-in path keeps catching
            # only ``ValueError``, so a malformed built-in still raises loudly (ADR-0004 §3 asymmetry).
            logger.warning("skipping malformed/unreadable project skill %s: %s", source, exc)
            continue
        skills[skill.name] = skill
    logger.debug(
        "discovered %d project skills under %s: %s", len(skills), skills_dir, sorted(skills)
    )
    return skills


def load_skills(cwd: Path) -> dict[str, SkillDef]:
    """Merge built-in + project-local skills, keyed by frontmatter name (ADR-0004 §3).

    Built-ins are loaded first, then the project skills ``dict.update`` over them — so a project skill
    whose frontmatter ``name`` equals a built-in's **overrides** it (its ``body`` and ``source`` become
    the project file's), while unoverridden built-ins and project-only skills both appear. The override
    is intentional and silent; ``source`` keeps the provenance traceable in logs.
    """
    skills = load_builtin_skills()
    project = discover_project_skills(cwd)
    overridden = sorted(set(skills) & set(project))
    if overridden:
        logger.info("project skills override built-ins by name: %s", overridden)
    skills.update(project)
    return skills


def _builtin_files() -> list[Traversable]:
    """The bundled ``builtin/*.md`` catalog files, sorted by name (packaged data)."""
    package = importlib.resources.files(_BUILTIN_PACKAGE)
    return sorted(
        (entry for entry in package.iterdir() if entry.name.endswith(".md")),
        key=lambda entry: entry.name,
    )


def _require_str(meta: dict[str, object], key: str) -> str:
    """Read a required, non-empty string frontmatter field.

    Raises :class:`ValueError` (never :class:`AttributeError`) when the key is missing or its YAML
    value is not a string — e.g. a list/number for ``name`` — so the loader, not :class:`SkillDef`,
    owns the clear error. The returned value is stripped so the dispatcher key and catalog text are
    exact (no leading/trailing whitespace), since :class:`SkillDef` validates stripped but stores raw.
    """
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' is required and must be a non-empty string")
    return value.strip()
