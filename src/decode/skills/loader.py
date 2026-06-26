"""Read + validate + merge the Skills Catalog from two sources (ADR-0004 §3,§5).

A skill follows the **Agent Skills directory convention**: a directory ``<name>/SKILL.md`` whose
``SKILL.md`` is a YAML frontmatter block (delimited by ``---`` lines) carrying ``name`` +
``description``, followed by the instruction body. The loader recognizes **only** ``<name>/SKILL.md``
directories — a flat ``*.md`` is no longer a skill (hard switch, no back-compat).
:func:`parse_skill_file` splits and validates one ``SKILL.md`` into a
:class:`~decode.entities.skill_def.SkillDef`; the ``source`` provenance label is supplied by the caller
(the one deliberate deviation from :func:`decode.agents.loader.parse_agent_file` — a skill carries
where it came from). The skill's **name is the frontmatter ``name:``** (ADR-0004 §3); the directory
name is cosmetic.

Skills come from two sources, with the same asymmetry the agents catalog uses for built-ins vs the
user's ``settings.json`` (ADR-0004 §3):

* :func:`load_builtin_skills` walks the bundled ``builtin/<name>/SKILL.md`` directories as **packaged
  data** (via :mod:`importlib.resources` nested traversal, so they ship in the installed wheel),
  ``source="builtin"`` and ``resource_dir=None`` always (a built-in's resources would live unreadable
  in site-packages — ADR-0004 §3). A built-in parse failure **raises loudly** — the built-ins ship
  with the package, so a failure here is a packaging bug, not user input to tolerate.
* :func:`discover_project_skills` scans ``<cwd>/<settings.skills_dir>/<name>/SKILL.md`` (cwd-relative,
  like memory), ``source`` set to the absolute ``SKILL.md`` path. A project skill that ships sibling
  resources (anything besides ``SKILL.md`` in its directory) gets ``resource_dir`` set to its
  directory (kept cwd-joined, un-``.resolve()``d, so task 033 can render it cwd-relative). A
  malformed/unreadable ``SKILL.md`` is logged at WARNING and **skipped** (a user's typo never breaks a
  session); a subdirectory with no ``SKILL.md`` is skipped; a missing dir yields ``{}``.

:func:`load_skills` merges built-ins first, then project skills via ``dict.update``, so a project skill
whose frontmatter ``name`` equals a built-in's **intentionally overrides** it (most-specific wins; the
silent override is acceptable — ``source`` keeps it traceable, ADR-0004 §3).
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

# The package the bundled catalog directories live in (packaged data, loaded via importlib.resources).
_BUILTIN_PACKAGE = "decode.skills.builtin"

# The provenance label for a bundled built-in skill.
_BUILTIN_SOURCE = "builtin"

# The canonical per-skill manifest filename (the Agent Skills directory convention; ADR-0004 §3).
_SKILL_FILE = "SKILL.md"


def parse_skill_file(text: str, source: str, resource_dir: Path | None = None) -> SkillDef:
    """Parse one ``SKILL.md`` (frontmatter + body) into a :class:`SkillDef`.

    Splits the leading ``---``-fenced YAML frontmatter from the body, requires a non-empty string
    ``name`` + ``description`` (the skill's name is the frontmatter ``name:``, not the directory name),
    and lets :class:`SkillDef` validate the rest. ``source`` is the provenance label the caller supplies
    (``"builtin"`` or the project ``SKILL.md`` path); ``resource_dir`` is the optional tier-3
    bundled-resource directory the caller threads through (``None`` for built-ins and resource-less
    project skills). Raises :class:`ValueError` on any structural problem — missing frontmatter, an
    unclosed fence, a missing/non-string ``name`` or ``description``, or an empty body — so a non-string
    YAML value (e.g. a list for ``name``) surfaces as a clear error here rather than an
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

    Walks ``builtin/<name>/SKILL.md`` directories via :mod:`importlib.resources` nested traversal
    (so the skills ship in the installed wheel). Returns a fresh dict each call (no shared mutable
    state), ``source="builtin"`` and ``resource_dir=None`` for every built-in (a built-in's resources
    would live unreadable in site-packages — ADR-0004 §3). A directory without a ``SKILL.md`` (and
    non-directory entries like ``__init__.py`` / ``__pycache__``) is skipped at DEBUG. Raises
    :class:`ValueError` if any bundled ``SKILL.md`` is malformed — the built-ins ship with the package,
    so a failure here is a packaging bug surfaced loudly rather than a silently dropped skill.
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

    Scans each ``<name>/SKILL.md`` subdirectory (sorted by directory name for a deterministic merge
    order) and parses its ``SKILL.md`` with ``source`` set to the manifest's absolute path. A skill
    that ships sibling resources (any entry in its directory besides ``SKILL.md``) gets
    ``resource_dir`` set to that directory **un-``.resolve()``d / cwd-joined**, so task 033 can render
    it cwd-relative; a resource-less skill gets ``None``. A subdirectory lacking ``SKILL.md`` is logged
    at WARNING and skipped; a malformed or unreadable ``SKILL.md`` is logged at WARNING and **skipped**
    so a user's typo never crashes the agent (mirrors memory's skip-unreadable and the user
    ``settings.json`` tolerance); a directory-name ≠ frontmatter-name mismatch still loads (keyed by
    frontmatter ``name``) but is logged at WARNING to catch copy-paste slips. A loose ``*.md`` directly
    under the skills dir (the dropped flat format) is logged at DEBUG to aid migration. A missing skills
    directory returns an empty dict.
    """
    skills_dir = cwd / settings.skills_dir
    if not skills_dir.is_dir():
        return {}
    skills: dict[str, SkillDef] = {}
    for sub in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if not sub.is_dir():
            if sub.suffix == ".md":
                logger.debug(
                    "ignoring loose '%s' under %s — skills are now <name>/%s directories",
                    sub.name,
                    skills_dir,
                    _SKILL_FILE,
                )
            continue
        skill_file = sub / _SKILL_FILE
        if not skill_file.is_file():
            logger.warning("skipping skill directory without a %s: %s", _SKILL_FILE, sub)
            continue
        source = str(skill_file.resolve())
        try:
            text = skill_file.read_text(encoding="utf-8")
            resource_dir = sub if _has_bundled_resources(sub) else None
            skill = parse_skill_file(text, source=source, resource_dir=resource_dir)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            # ``yaml.YAMLError`` (e.g. ``ScannerError`` on a typo'd frontmatter) is NOT a ``ValueError``,
            # so it must be caught explicitly — else a single broken project skill crashes the live
            # session (the loader runs every turn via the catalog hook). The built-in path keeps catching
            # only ``ValueError``, so a malformed built-in still raises loudly (ADR-0004 §3 asymmetry).
            logger.warning("skipping malformed/unreadable project skill %s: %s", source, exc)
            continue
        if skill.name != sub.name:
            # The directory name is cosmetic (keyed by frontmatter ``name``), but a mismatch is usually
            # a copy-paste slip worth surfacing — load it, warn loudly (ADR-0004 §3).
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


def _has_bundled_resources(skill_dir: Path) -> bool:
    """True iff ``skill_dir`` holds any entry besides its ``SKILL.md`` (tier-3 resources; ADR-0004 §5).

    Any sibling file or folder (``references/``, ``examples/``, ``scripts/``, a stray note) means the
    skill ships resources the model may ``read``, so its directory becomes the :class:`SkillDef`'s
    ``resource_dir``. A directory containing only ``SKILL.md`` has no resources → ``None``.
    """
    return any(entry.name != _SKILL_FILE for entry in skill_dir.iterdir())


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
