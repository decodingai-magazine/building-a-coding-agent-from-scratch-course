"""The shared skill payload helper: body, resource manifest, and the outputs default.

Both invocation paths — the model's ``skill(name)`` dispatcher and the user's ``/<skill-name>``
TUI command — format their result here, so the two payloads can never diverge (ADR-0004 §1,§5).
A skill that ships resources gets a manifest enumerating every bundled file with its exact
cwd-relative path — paths that resolve for the host-side ``read`` tool and, byte-identically,
for ``bash`` inside a sandbox; the walk happens at dispatch time, only over the skill's own
directory. Every payload ends with the standing **outputs default**: new work-product files land
under ``.decode/outputs/`` (gitignored — ``.decode/*`` minus ``skills/``) unless the user named
a destination, so a skill run never litters the project tree.
"""

from __future__ import annotations

import os
from pathlib import Path

from decode.entities.skill_def import SkillDef

__all__ = ["OUTPUTS_DIR", "format_skill_payload"]

# The default home for files a skill produces, relative to the working directory. Inside
# ``.decode/`` so it is gitignored alongside the other runtime state (only ``skills/`` is tracked).
OUTPUTS_DIR = ".decode/outputs"

# The standing convention appended to EVERY payload. "New files" on purpose: edits a skill makes
# to existing project files stay in place — only fresh artifacts default into the outputs dir.
_OUTPUTS_TRAILER = (
    f"Output default: write NEW files this skill produces under `{OUTPUTS_DIR}/` (create the "
    "directory if missing) — unless the user named a destination path, which always wins. "
    "Edits to existing project files happen in place."
)


def format_skill_payload(skill: SkillDef, *, cwd: Path) -> str:
    """Return ``skill.body`` + optional bundled-file manifest + the outputs default (§1,§5).

    When the skill ships resources, every bundled file (recursive, sorted, ``SKILL.md`` itself
    excluded) is listed with its cwd-relative path after a blank-line separator; an empty walk
    falls back to the one-line directory-only trailer. The outputs-default line closes every
    payload, resource-shipping or not.
    """
    if skill.resource_dir is None:
        return f"{skill.body}\n\n{_OUTPUTS_TRAILER}"
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
    return f"{skill.body}\n\n{trailer}\n\n{_OUTPUTS_TRAILER}"


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
