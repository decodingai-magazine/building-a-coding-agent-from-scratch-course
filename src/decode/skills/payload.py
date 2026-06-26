"""The shared skill payload helper: body, plus a resource trailer when the skill ships files (§1,§5).

A skill is invokable two ways — the model's ``skill(name)`` dispatcher and the user's
``/<skill-name>`` TUI command (ADR-0004 §5). Both resolve through the same ``load_skills(cwd)`` and
then format the result here, so the two paths return an **identical** payload and can never diverge.

The payload is the bridge from **tier 2** (the ``SKILL.md`` body) to **tier 3** (bundled resource
files the body references — ``references/``, ``examples/``, ``scripts/``; ADR-0004 §1,§5):

* a skill with **no** bundled resources (``resource_dir is None`` — every built-in, and any
  resource-less project skill) returns its ``body`` **unchanged**, so the common case stays lean;
* a skill that **does** ship resources gets its ``body`` plus a short **resource trailer**, appended
  after a blank line, that names the skill's directory and tells the model how to load the files.

The trailer names the directory as a **cwd-relative** path (e.g. ``.decode/skills/pdf-export``) — the
directory **only**, never a listing of its contents (the grilled decision: a directory name the model
can ``read``/``glob`` from, not a manifest that re-bloats the payload). Because a project skill's
directory lives under ``cwd`` and ``resource_dir`` is stored cwd-joined / un-``.resolve()``d (task
032), this relative path, joined back under ``cwd``, satisfies the ``read`` tool's containment check
(:func:`decode.tools.files._resolve_in_cwd`) — so the model can ``read("<dir>/references/foo.md")``.

Tiny and synchronous: pure string formatting, no filesystem access (the entity never touches disk and
neither does this helper).
"""

from __future__ import annotations

import os
from pathlib import Path

from decode.entities.skill_def import SkillDef

__all__ = ["format_skill_payload"]


def format_skill_payload(skill: SkillDef, *, cwd: Path) -> str:
    """Return ``skill.body``, plus a resource trailer when the skill ships bundled files (§1,§5).

    With ``skill.resource_dir is None`` (a built-in or a resource-less project skill) the body is
    returned **verbatim** — no trailer. Otherwise the directory is rendered **relative to ``cwd``**
    (``os.path.relpath`` — exact because task 032 stored ``resource_dir`` cwd-joined and
    un-``.resolve()``d) and a one-line trailer naming **only** that directory is appended after a
    blank-line separator. The surfaced path, joined under ``cwd``, passes the ``read`` tool's
    containment check, so the model can ``read("<dir>/references/<file>")`` and run ``scripts/`` via
    ``bash``.
    """
    if skill.resource_dir is None:
        return skill.body
    rel_dir = os.path.relpath(skill.resource_dir, cwd)
    trailer = (
        f"Bundled files for this skill are under `{rel_dir}/` — "
        "read them with the `read` tool, run `scripts/` with `bash`."
    )
    return f"{skill.body}\n\n{trailer}"
