"""File-seeding fixture builders: a type-error module and a skills directory (ADR-0017 §6)."""

from __future__ import annotations

from pathlib import Path

# A tiny module whose ``add`` annotates ``int`` params but is called with a ``str`` — an unambiguous
# type error a language-server / fix-the-bug probe can be asked to find and fix.
_TYPE_ERROR_SOURCE = '''\
"""A tiny module with one deliberate type error for the regression probes."""


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left + right


result: int = add("not", "numbers")
'''

# The minimal skill a dispatch probe needs: a front-matter'd SKILL.md under ``.decode/skills/<name>/``.
_SKILL_TEMPLATE = """\
---
name: {name}
description: {description}
---

# {name}

{body}
"""


def seed_type_error(workspace: Path, *, filename: str = "buggy.py") -> Path:
    """Write a tiny Python module carrying one deliberate type error into ``workspace``.

    Returns the path of the seeded file (relative name ``filename``). The module type-checks as broken
    (``add("not", "numbers")`` against ``int`` params) so an LSP / fix-the-bug probe has something
    concrete to diagnose.
    """
    path = workspace / filename
    path.write_text(_TYPE_ERROR_SOURCE, encoding="utf-8")
    return path


def seed_skills_dir(
    workspace: Path,
    *,
    name: str = "greet",
    description: str = "Greet a person by name.",
    body: str = "Say hello to the person the user names.",
) -> Path:
    """Seed ``<workspace>/.decode/skills/<name>/SKILL.md`` and return the skill's directory.

    The layout mirrors decode's on-disk skills catalog (ADR-0004): one folder per skill under
    ``.decode/skills`` with a front-matter'd ``SKILL.md``. A skill-dispatch probe seeds this, then
    asks the agent to use the skill.
    """
    skill_dir = workspace / ".decode" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _SKILL_TEMPLATE.format(name=name, description=description, body=body),
        encoding="utf-8",
    )
    return skill_dir
