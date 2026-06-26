"""Unit tests for the shared skill payload helper (``decode.skills.payload``; ADR-0004 §1,§5).

``format_skill_payload(skill, cwd=…)`` is the single bridge from tier 2 (the body) to tier 3 (the
bundled-resource files): it returns the skill's ``body`` **unchanged** when the skill ships no
resources (``resource_dir is None`` — built-ins and resource-less project skills), and ``body`` +
a short **resource trailer** when it does. The trailer names the resource directory as a
``read``/``bash``-resolvable, **cwd-relative** path (directory only — it never enumerates contents)
and tells the model how to load bundled files. Both invocation paths (the model's ``skill``
dispatcher and the user's ``/<skill-name>`` TUI command) go through this one helper, so the payload
can never diverge.
"""

from __future__ import annotations

from pathlib import Path

from decode.entities.skill_def import SkillDef
from decode.skills import payload
from decode.tools.files import _resolve_in_cwd


def _skill(*, resource_dir: Path | None) -> SkillDef:
    """A minimal valid :class:`SkillDef` with a controllable ``resource_dir``."""
    return SkillDef(
        name="deploy",
        description="a deploy skill",
        body="Deploy the app.\nThen smoke-test it.",
        source="/abs/.decode/skills/deploy/SKILL.md",
        resource_dir=resource_dir,
    )


# --- resource_dir is None → body verbatim, no trailer ---------------------------------------


def test_payload_is_the_body_unchanged_when_no_resource_dir(tmp_path):
    # A SKILL.md-only skill (built-in or resource-less project skill) gets NO trailer: the payload
    # is byte-for-byte its body.
    skill = _skill(resource_dir=None)

    result = payload.format_skill_payload(skill, cwd=tmp_path)

    assert result == skill.body


# --- resource_dir set → body + trailer naming the cwd-relative dir ---------------------------


def test_payload_appends_a_trailer_when_resource_dir_is_set(tmp_path):
    # A skill that ships resources gets its body + a trailer, separated by a blank line. The body
    # itself is left intact at the front of the payload.
    resource_dir = tmp_path / ".decode" / "skills" / "deploy"
    skill = _skill(resource_dir=resource_dir)

    result = payload.format_skill_payload(skill, cwd=tmp_path)

    assert result != skill.body
    assert result.startswith(skill.body)
    assert result[len(skill.body) :].startswith("\n\n")  # blank-line separator


def test_trailer_names_the_cwd_relative_directory_only(tmp_path):
    # The trailer names the directory cwd-relatively (``.decode/skills/deploy``), explains loading
    # bundled files via ``read`` and running ``scripts/`` via ``bash``, and lists NO directory
    # contents (directory only — the grilled decision).
    resource_dir = tmp_path / ".decode" / "skills" / "deploy"
    skill = _skill(resource_dir=resource_dir)

    result = payload.format_skill_payload(skill, cwd=tmp_path)
    trailer = result[len(skill.body) :]

    assert ".decode/skills/deploy" in trailer  # cwd-relative, not the absolute path
    assert str(resource_dir) not in trailer  # never the absolute path
    assert "read" in trailer and "bash" in trailer  # how to load files / run scripts


def test_surfaced_path_resolves_under_cwd_via_the_read_containment_check(tmp_path):
    # The cwd-relative dir the trailer names, joined under cwd, must satisfy the ``read`` tool's
    # containment check so a model can ``read("<dir>/references/<file>")``. Assert the exact path the
    # trailer surfaces passes ``_resolve_in_cwd`` (and resolves to the real on-disk bundled file).
    resource_dir = tmp_path / ".decode" / "skills" / "deploy"
    (resource_dir / "references").mkdir(parents=True)
    bundled = resource_dir / "references" / "guide.md"
    bundled.write_text("read me", encoding="utf-8")
    skill = _skill(resource_dir=resource_dir)

    result = payload.format_skill_payload(skill, cwd=tmp_path)

    # Pull the cwd-relative dir back out of the trailer and rebuild the path a model would type.
    rel_dir = ".decode/skills/deploy"
    assert rel_dir in result
    resolved = _resolve_in_cwd(tmp_path, f"{rel_dir}/references/guide.md")
    assert resolved == bundled.resolve()  # the surfaced path is the real, readable bundled file
