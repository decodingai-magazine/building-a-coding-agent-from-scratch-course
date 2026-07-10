"""Read + validate + merge the Skills Catalog from two sources (ADR-0004 §3,§5).

A skill is a directory ``<name>/SKILL.md``: YAML frontmatter (``name`` + ``description``)
followed by the instruction body. The skill's name is the frontmatter ``name:``; the directory
name is cosmetic. Built-ins load as packaged data and a malformed one raises loudly; project
skills under ``<cwd>/<settings.skills_dir>`` are skipped with a WARNING when malformed (a
user's typo never breaks a session). :func:`load_skills` merges built-ins first, then project
skills — a same-name project skill intentionally overrides the built-in (``source`` keeps the
provenance traceable).
"""

from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path

import yaml

from decode.config.settings import settings
from decode.entities.skill_def import SkillDef
from decode.frontmatter import split_frontmatter

logger = logging.getLogger(__name__)

# The package the bundled catalog directories live in.
_BUILTIN_PACKAGE = "decode.skills.builtin"

# The provenance label for a bundled built-in skill.
_BUILTIN_SOURCE = "builtin"

# The canonical per-skill manifest filename (ADR-0004 §3).
_SKILL_FILE = "SKILL.md"


def parse_skill_file(text: str, source: str, resource_dir: Path | None = None) -> SkillDef:
    """Parse one ``SKILL.md`` (frontmatter + body) into a :class:`SkillDef`.

    ``source`` is the caller-supplied provenance label (``"builtin"`` or the project ``SKILL.md``
    path); ``resource_dir`` is the optional bundled-resource directory (``None`` for built-ins
    and resource-less project skills). Raises :class:`ValueError` on any structural problem,
    including a non-string YAML value, so the error surfaces here rather than as an
    :class:`AttributeError` from :class:`SkillDef`.
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
        resource_dir=resource_dir,
    )


def load_builtin_skills() -> dict[str, SkillDef]:
    """Read + validate every bundled built-in skill, keyed by frontmatter name (ADR-0004 §3).

    Walks ``builtin/<name>/SKILL.md`` via :mod:`importlib.resources`; returns a fresh dict with
    ``source="builtin"`` and ``resource_dir=None`` for every built-in (a built-in's resources
    would live unreadable in site-packages). A malformed bundled ``SKILL.md`` raises
    :class:`ValueError` — a packaging bug surfaced loudly, not a silently dropped skill.
    """
    package = importlib.resources.files(_BUILTIN_PACKAGE)
    skills: dict[str, SkillDef] = {}
    for entry in sorted(package.iterdir(), key=lambda e: e.name):
        if not entry.is_dir():
            continue  # skip __init__.py and any stray top-level files
        skill_file = entry / _SKILL_FILE
        if not skill_file.is_file():
            logger.debug("skipping built-in dir without %s: %s", _SKILL_FILE, entry.name)
            continue  # also silences __pycache__
        text = skill_file.read_text(encoding="utf-8")
        try:
            skill = parse_skill_file(text, source=_BUILTIN_SOURCE)
        except ValueError as exc:
            raise ValueError(f"invalid built-in skill {entry.name!r}/{_SKILL_FILE}: {exc}") from exc
        skills[skill.name] = skill
    logger.debug("loaded %d built-in skills: %s", len(skills), sorted(skills))
    return skills


def discover_project_skills(cwd: Path) -> dict[str, SkillDef]:
    """Discover project-local skills under ``<cwd>/<settings.skills_dir>``, keyed by frontmatter name.

    A skill shipping sibling resources gets ``resource_dir`` set to its directory, kept
    cwd-joined and un-``.resolve()``d so callers can render it cwd-relative. A subdirectory
    lacking ``SKILL.md`` or a malformed/unreadable ``SKILL.md`` is logged at WARNING and skipped
    (a user's typo never crashes the agent); a directory-name ≠ frontmatter-name mismatch still
    loads but warns. A missing skills directory returns an empty dict.
    """
    skills_dir = cwd / settings.skills_dir
    if not skills_dir.is_dir():
        return {}
    skills: dict[str, SkillDef] = {}
    for sub in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if not sub.is_dir():
            continue
        skill_file = sub / _SKILL_FILE
        if not skill_file.is_file():
            logger.warning("skipping skill directory without a %s: %s", _SKILL_FILE, sub)
            continue
        source = str(skill_file.resolve())
        try:
            text = skill_file.read_text(encoding="utf-8")
            resource_dir = sub if any(e.name != _SKILL_FILE for e in sub.iterdir()) else None
            skill = parse_skill_file(text, source=source, resource_dir=resource_dir)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            # ``yaml.YAMLError`` is NOT a ``ValueError`` — catch it explicitly so one broken
            # project skill never crashes the live session (built-ins still raise loudly).
            logger.warning("skipping malformed/unreadable project skill %s: %s", source, exc)
            continue
        if skill.name != sub.name:
            # A mismatch is usually a copy-paste slip — load it, warn loudly (ADR-0004 §3).
            logger.warning(
                "project skill directory %r holds a skill named %r (directory name is cosmetic)",
                sub.name,
                skill.name,
            )
        skills[skill.name] = skill
    logger.debug(
        "discovered %d project skills under %s: %s", len(skills), skills_dir, sorted(skills)
    )
    return skills


def load_skills(cwd: Path) -> dict[str, SkillDef]:
    """Merge built-in + project-local skills, keyed by frontmatter name (ADR-0004 §3).

    Built-ins first, then project skills ``dict.update`` over them — a same-name project skill
    intentionally overrides the built-in; ``source`` keeps the provenance traceable.
    """
    skills = load_builtin_skills()
    project = discover_project_skills(cwd)
    overridden = sorted(set(skills) & set(project))
    if overridden:
        logger.info("project skills override built-ins by name: %s", overridden)
    skills.update(project)
    return skills


def _require_str(meta: dict[str, object], key: str) -> str:
    """Read a required non-empty string field, stripped; missing/non-string raises ValueError."""
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' is required and must be a non-empty string")
    return value.strip()
