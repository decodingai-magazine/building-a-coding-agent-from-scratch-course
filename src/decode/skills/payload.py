"""The shared skill payload helper: body, plus a resource manifest when the skill ships files.

Both invocation paths — the model's ``skill(name)`` dispatcher and the user's ``/<skill-name>``
TUI command — format their result here, so the two payloads can never diverge (ADR-0004 §1,§5).
A skill with no bundled resources returns its ``body`` unchanged; one that ships resources gets
a manifest enumerating every bundled file with its exact cwd-relative path — paths that resolve
for the host-side ``read`` tool and, byte-identically, for ``bash`` inside a sandbox. The walk
happens at dispatch time, only over the skill's own directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from decode.entities.skill_def import SkillDef

__all__ = ["format_skill_payload"]


def format_skill_payload(skill: SkillDef, *, cwd: Path) -> str:
    """Return ``skill.body``, plus a bundled-file manifest when the skill ships resources (§1,§5).

    With ``skill.resource_dir is None`` the body is returned verbatim — no trailer. Otherwise
    every bundled file (recursive, sorted, ``SKILL.md`` itself excluded) is listed with its
    cwd-relative path after a blank-line separator; an empty walk falls back to the one-line
    directory-only trailer.
    """
    if skill.resource_dir is None:
        return skill.body
    rel_dir = os.path.relpath(skill.resource_dir, cwd)
    files = _bundled_files(skill.resource_dir)
    if files:
        listing = "\n".join(f"- {rel_dir}/{name}" for name in files)
        trailer = (
            f"Bundled files for this skill (all under `{rel_dir}/` — use these EXACT paths):\n"
            f"{listing}\n"
            "Read them with the `read` tool; run `scripts/` files with `bash`."
        )
    else:
        trailer = (
            f"Bundled files for this skill are under `{rel_dir}/` — "
            "read them with the `read` tool, run `scripts/` with `bash`."
        )
    return f"{skill.body}\n\n{trailer}"


def _bundled_files(resource_dir: Path) -> list[str]:
    """Every file under the skill's directory — recursive, sorted, POSIX-relative — minus ``SKILL.md``.

    An absent or unreadable directory yields ``[]`` so the caller degrades instead of failing.
    """
    try:
        return sorted(
            path.relative_to(resource_dir).as_posix()
            for path in resource_dir.rglob("*")
            if path.is_file() and path.relative_to(resource_dir).as_posix() != "SKILL.md"
        )
    except OSError:
        return []
