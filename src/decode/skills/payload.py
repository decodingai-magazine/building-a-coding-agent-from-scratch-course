"""The shared skill payload helper: body, plus a resource manifest when the skill ships files (§1,§5).

A skill is invokable two ways — the model's ``skill(name)`` dispatcher and the user's
``/<skill-name>`` TUI command (ADR-0004 §5). Both resolve through the same ``load_skills(cwd)`` and
then format the result here, so the two paths return an **identical** payload and can never diverge.

The payload is the bridge from **tier 2** (the ``SKILL.md`` body) to **tier 3** (bundled resource
files the body references — ``references/``, ``examples/``, ``scripts/``; ADR-0004 §1,§5):

* a skill with **no** bundled resources (``resource_dir is None`` — every built-in, and any
  resource-less project skill) returns its ``body`` **unchanged**, so the common case stays lean;
* a skill that **does** ship resources gets its ``body`` plus a **resource manifest**, appended
  after a blank line: every bundled file, enumerated recursively with its exact cwd-relative path.

The manifest replaces the original directory-only trailer (*revised after a live failure*): naming
just the directory left the model to discover the files itself, but a ``glob <dir>/*`` does not
cross into ``references/`` / ``scripts/`` subdirectories, so the model guessed body-relative paths
(``references/template.md``) against the cwd and missed. Enumerating the files hands it the exact
paths — which resolve for the host-side ``read`` tool (they pass the containment check under
``cwd``, :func:`decode.tools.files._resolve_in_cwd`) **and**, byte-identically, for ``bash`` inside
a sandbox (both sandboxes mount/seed ``.decode/skills`` at ``/workspace/.decode/skills``, and
``/workspace`` is the shell's cwd — ADR-0011). A resource dir that yields no files (deleted midway,
unreadable) degrades to the old directory-only line rather than failing the dispatch.

Walks only the skill's own (tiny by design) directory, at dispatch time — so files added after
startup are seen, and nothing is scanned for skills never invoked.
"""

from __future__ import annotations

import os
from pathlib import Path

from decode.entities.skill_def import SkillDef

__all__ = ["format_skill_payload"]


def format_skill_payload(skill: SkillDef, *, cwd: Path) -> str:
    """Return ``skill.body``, plus a bundled-file manifest when the skill ships resources (§1,§5).

    With ``skill.resource_dir is None`` (a built-in or a resource-less project skill) the body is
    returned **verbatim** — no trailer. Otherwise the skill's directory is walked (recursively,
    sorted, ``SKILL.md`` itself excluded — its content IS the payload) and every bundled file is
    listed with its **cwd-relative** path (exact because task 032 stored ``resource_dir`` cwd-joined
    and un-``.resolve()``d), after a blank-line separator. Each listed path, joined under ``cwd``,
    passes the ``read`` tool's containment check, and the same relative path resolves under the
    sandbox's ``/workspace`` for ``bash``-run scripts. An empty walk falls back to the one-line
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

    The manifest source. ``SKILL.md`` at the top level is excluded (it is the body the payload
    already carries); nested files keep their subdir prefixes (``references/template.md``,
    ``scripts/fetch.py``). An absent or unreadable directory yields ``[]`` so the caller degrades to
    the directory-only trailer instead of failing the dispatch.
    """
    try:
        return sorted(
            path.relative_to(resource_dir).as_posix()
            for path in resource_dir.rglob("*")
            if path.is_file() and path.relative_to(resource_dir).as_posix() != "SKILL.md"
        )
    except OSError:
        return []
