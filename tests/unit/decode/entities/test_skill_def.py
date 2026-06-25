"""Unit tests for the :class:`decode.entities.skill_def.SkillDef` entity (ADR-0004 §6).

``SkillDef`` is the parsed + validated result of one skill Markdown file: a skill's ``name`` /
``description`` / Markdown ``body`` / ``source`` provenance label. Mirroring ``AgentDef`` it owns
its *validation* (the loader just feeds it parsed frontmatter): every field must be non-empty after
``.strip()``, and the ``ValueError`` must name the offending field. Unlike ``AgentDef`` it carries
**no** ``tools`` / ``mode`` / ``allow`` / ``deny`` — a skill is pure injected guidance this milestone
(ADR-0004 §6). These tests pin that contract without going through the file loader (later task).
"""

import dataclasses

import pytest

from decode.entities.skill_def import SkillDef


def test_skill_def_carries_its_fields():
    skill = SkillDef(
        name="commit",
        description="stage and commit the working tree",
        body="# Commit\n\nRun git add then git commit.",
        source="builtin",
    )

    assert skill.name == "commit"
    assert skill.description == "stage and commit the working tree"
    assert skill.body == "# Commit\n\nRun git add then git commit."
    assert skill.source == "builtin"


def test_skill_def_is_frozen_and_hashable():
    skill = SkillDef(
        name="commit",
        description="x",
        body="do the thing",
        source="builtin",
    )

    hash(skill)  # frozen + slotted -> hashable
    with pytest.raises(dataclasses.FrozenInstanceError):
        skill.name = "mutated"  # type: ignore[misc]


def test_skill_def_uses_slots():
    skill = SkillDef(name="commit", description="x", body="y", source="builtin")

    assert not hasattr(skill, "__dict__")  # slots=True -> no per-instance __dict__


# --- validation -----------------------------------------------------------------------------


def test_skill_def_rejects_an_empty_name():
    with pytest.raises(ValueError, match="name"):
        SkillDef(name="   ", description="x", body="y", source="builtin")


def test_skill_def_rejects_an_empty_description():
    with pytest.raises(ValueError, match="description"):
        SkillDef(name="commit", description="   ", body="y", source="builtin")


def test_skill_def_rejects_an_empty_body():
    with pytest.raises(ValueError, match="body"):
        SkillDef(name="commit", description="x", body="   ", source="builtin")


def test_skill_def_rejects_an_empty_source():
    with pytest.raises(ValueError, match="source"):
        SkillDef(name="commit", description="x", body="y", source="   ")


def test_skill_def_names_the_skill_in_the_field_error():
    # When the name is known, the message also names the skill (like AgentDef.__post_init__).
    with pytest.raises(ValueError, match="commit"):
        SkillDef(name="commit", description="x", body="   ", source="builtin")


# --- shape: no persona / rule fields (ADR-0004 §6) ------------------------------------------


@pytest.mark.parametrize("absent", ["tools", "mode", "allow", "deny"])
def test_skill_def_has_no_persona_or_rule_fields(absent: str):
    skill = SkillDef(name="commit", description="x", body="y", source="builtin")

    field_names = {f.name for f in dataclasses.fields(skill)}
    assert absent not in field_names
    assert not hasattr(skill, absent)


# --- working-looks-like (acceptance) --------------------------------------------------------


def test_skill_def_replace_with_empty_name_raises():
    skill = SkillDef(
        name="commit",
        description="stage and commit the working tree",
        body="instructions",
        source="builtin",
    )

    with pytest.raises(ValueError, match="name"):
        dataclasses.replace(skill, name="")
