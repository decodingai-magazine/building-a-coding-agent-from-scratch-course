"""Unit tests for the Skills Catalog loader (``decode.skills.loader``; ADR-0004 §3).

The loader reads skills from two sources and merges them (ADR-0004 §3):

* **built-in** — the bundled ``builtin/*.md`` files loaded as *packaged data* (``importlib.resources``,
  so they ship in the wheel), validated into :class:`~decode.entities.skill_def.SkillDef`, keyed by
  frontmatter ``name``, ``source == "builtin"``. A built-in parse failure raises loudly.
* **project-local** — ``<cwd>/<settings.skills_dir>/*.md`` (cwd-relative, like memory), ``source`` set
  to the absolute file path. A malformed/unreadable project skill is logged at WARNING and skipped; a
  missing dir yields ``{}``.

:func:`load_skills` merges built-ins first then project skills, so a project skill whose frontmatter
``name`` equals a built-in's intentionally overrides it. These tests pin the two built-ins (the active
``commit`` body, the advisory ``review-diff`` body), the packaged-data path, frontmatter/body parsing +
error messages, project discovery + keying by frontmatter name, skip-with-warning vs raise asymmetry,
and the project-override-by-name merge.
"""

import importlib.resources
import logging
from pathlib import Path

import pytest

from decode.config.settings import settings
from decode.entities.skill_def import SkillDef
from decode.skills import loader

_BUILTIN_NAMES = {"commit", "review-diff"}


def _write_skill(
    skills_dir: Path, filename: str, *, name: str, body: str = "Do the thing."
) -> Path:
    """Write a minimal valid project skill file and return its path."""
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / filename
    text = f"---\nname: {name}\ndescription: a {name} skill\n---\n{body}\n"
    path.write_text(text, encoding="utf-8")
    return path


def _skills_dir(cwd: Path) -> Path:
    """The project skills dir the loader reads (``cwd / settings.skills_dir``)."""
    return cwd / settings.skills_dir


# --- settings -------------------------------------------------------------------------------


def test_skills_dir_default_is_decode_skills():
    assert settings.skills_dir == Path(".decode/skills")


def test_discover_reads_the_dir_via_the_settings_singleton(tmp_path, monkeypatch):
    # The loader must read the location only through ``settings.skills_dir`` (no literal path): point
    # the singleton at a different relative dir and discovery must follow it.
    monkeypatch.setattr(settings, "skills_dir", Path("custom/skills"))
    custom = _write_skill(tmp_path / "custom" / "skills", "c.md", name="custom-skill")

    found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"custom-skill"}
    assert found["custom-skill"].source == str(custom.resolve())


# --- the two built-ins ----------------------------------------------------------------------


def test_load_builtin_skills_returns_the_two_skills():
    skills = loader.load_builtin_skills()

    assert set(skills) == _BUILTIN_NAMES
    assert all(isinstance(s, SkillDef) for s in skills.values())
    assert all(name == skill.name for name, skill in skills.items())


def test_each_builtin_has_description_body_and_builtin_source():
    skills = loader.load_builtin_skills()

    for skill in skills.values():
        assert skill.description.strip()
        assert skill.body.strip()
        assert skill.source == "builtin"


def test_builtin_descriptions_match_the_frontmatter():
    skills = loader.load_builtin_skills()

    assert skills["commit"].description == (
        "Stage the appropriate changes and commit them with a Conventional Commits message."
    )
    assert skills["review-diff"].description == (
        "Review the working-tree diff for bugs and over-engineering."
    )


def test_commit_skill_body_is_active_it_stages_and_commits():
    # The commit skill is ACTIVE: it instructs `git add` + `git commit` on the working tree.
    commit = loader.load_builtin_skills()["commit"]

    assert "git add" in commit.body
    assert "git commit" in commit.body


def test_review_diff_skill_body_is_advisory_read_only():
    # review-diff inspects with `git diff` but never commits.
    review = loader.load_builtin_skills()["review-diff"]

    assert "git diff" in review.body
    assert "git commit" not in review.body


def test_load_builtin_skills_is_independent_per_call():
    first = loader.load_builtin_skills()
    second = loader.load_builtin_skills()

    assert first == second
    assert first is not second


# --- packaged-data loading ------------------------------------------------------------------


def test_builtin_files_are_packaged_data_not_a_repo_path():
    # Load through the installed package's resources, not a hard-coded filesystem path.
    files = importlib.resources.files("decode.skills.builtin")
    names = {entry.name for entry in files.iterdir() if entry.name.endswith(".md")}

    assert names == {"commit.md", "review-diff.md"}
    for md in names:
        assert (files / md).read_text(encoding="utf-8").strip()


# --- parse_skill_file -----------------------------------------------------------------------


def test_parse_skill_file_splits_frontmatter_and_body():
    text = "---\nname: demo\ndescription: a demo skill\n---\nYou are the demo skill.\n"

    skill = loader.parse_skill_file(text, source="builtin")

    assert skill.name == "demo"
    assert skill.description == "a demo skill"
    assert skill.body.strip() == "You are the demo skill."
    assert skill.source == "builtin"


def test_parse_skill_file_derives_name_from_frontmatter_not_a_filename():
    # The skill name is the frontmatter `name:`, independent of any file it came from.
    text = "---\nname: actual-name\ndescription: x\n---\nBody.\n"

    skill = loader.parse_skill_file(text, source="/tmp/cosmetic.md")

    assert skill.name == "actual-name"


def test_parse_skill_file_passes_source_through():
    text = "---\nname: demo\ndescription: x\n---\nBody.\n"

    skill = loader.parse_skill_file(text, source="/abs/path/demo.md")

    assert skill.source == "/abs/path/demo.md"


def test_parse_skill_file_strips_whitespace_from_name_and_description():
    # Forward-note 1: SkillDef stores raw, so the loader must strip so the dispatcher key and the
    # catalog text are exact (no leading/trailing whitespace).
    text = "---\nname: '  demo  '\ndescription: '  spacey  '\n---\nBody.\n"

    skill = loader.parse_skill_file(text, source="builtin")

    assert skill.name == "demo"
    assert skill.description == "spacey"


def test_parse_skill_file_rejects_a_missing_frontmatter_block():
    with pytest.raises(ValueError, match="frontmatter"):
        loader.parse_skill_file("no frontmatter here, just a body\n", source="builtin")


def test_parse_skill_file_rejects_an_unclosed_fence():
    with pytest.raises(ValueError, match="closed"):
        loader.parse_skill_file("---\nname: demo\ndescription: x\nbody but no closing fence\n", "x")


def test_parse_skill_file_rejects_a_missing_name():
    with pytest.raises(ValueError, match="name"):
        loader.parse_skill_file("---\ndescription: x\n---\nBody.\n", source="builtin")


def test_parse_skill_file_rejects_a_missing_description():
    with pytest.raises(ValueError, match="description"):
        loader.parse_skill_file("---\nname: demo\n---\nBody.\n", source="builtin")


def test_parse_skill_file_rejects_an_empty_body():
    with pytest.raises(ValueError, match="body"):
        loader.parse_skill_file("---\nname: demo\ndescription: x\n---\n   \n", source="builtin")


@pytest.mark.parametrize("bad_name", ["[a, b]", "123", "true"])
def test_parse_skill_file_rejects_a_non_string_name(bad_name):
    # Forward-note 2: a YAML value that isn't a string (list/number/bool) must surface as a clear
    # ValueError from the loader, not an AttributeError from SkillDef.
    text = f"---\nname: {bad_name}\ndescription: x\n---\nBody.\n"

    with pytest.raises(ValueError, match="name"):
        loader.parse_skill_file(text, source="builtin")


def test_parse_skill_file_rejects_a_non_string_description():
    text = "---\nname: demo\ndescription: [a, b]\n---\nBody.\n"

    with pytest.raises(ValueError, match="description"):
        loader.parse_skill_file(text, source="builtin")


def test_parse_skill_file_ignores_unknown_frontmatter_keys():
    # A future key (e.g. ``tools``) must not break the loader (forward-compatible, ADR-0004 §6).
    text = "---\nname: demo\ndescription: x\ntools: [read]\n---\nBody.\n"

    skill = loader.parse_skill_file(text, source="builtin")

    assert skill.name == "demo"


# --- discover_project_skills ----------------------------------------------------------------


def test_discover_finds_project_skills_keyed_by_frontmatter_name(tmp_path):
    # A file `renamed.md` whose frontmatter is `name: actual` keys as `actual` (filename cosmetic).
    path = _write_skill(_skills_dir(tmp_path), "renamed.md", name="actual")

    found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"actual"}
    assert found["actual"].source == str(path.resolve())


def test_discover_finds_multiple_project_skills(tmp_path):
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "a.md", name="alpha")
    _write_skill(skills_dir, "b.md", name="beta")

    found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"alpha", "beta"}


def test_discover_missing_dir_returns_empty_dict(tmp_path):
    assert loader.discover_project_skills(tmp_path) == {}


def test_discover_empty_dir_returns_empty_dict(tmp_path):
    _skills_dir(tmp_path).mkdir(parents=True)

    assert loader.discover_project_skills(tmp_path) == {}


def test_discover_skips_a_malformed_project_skill_with_a_warning(tmp_path, caplog):
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "good.md", name="good")
    # A malformed file (no frontmatter) must be skipped, not crash the agent.
    (skills_dir / "bad.md").write_text("not a skill, no frontmatter\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"good"}  # the good one still loads
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert any("bad.md" in record.getMessage() for record in caplog.records)


def test_discover_skips_an_unreadable_project_skill_with_a_warning(tmp_path, caplog):
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "good.md", name="good")
    # A `*.md` that cannot be read as a file (here: a directory) is skipped, not fatal.
    (skills_dir / "broken.md").mkdir()

    with caplog.at_level(logging.WARNING):
        found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"good"}
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# --- load_skills (merge) --------------------------------------------------------------------


def test_load_skills_returns_builtins_when_no_project_skills(tmp_path):
    skills = loader.load_skills(tmp_path)

    assert set(skills) == _BUILTIN_NAMES
    assert all(skill.source == "builtin" for skill in skills.values())


def test_load_skills_includes_project_only_skills_alongside_builtins(tmp_path):
    _write_skill(_skills_dir(tmp_path), "extra.md", name="deploy")

    skills = loader.load_skills(tmp_path)

    assert set(skills) == _BUILTIN_NAMES | {"deploy"}
    assert skills["deploy"].source.endswith("extra.md")
    # The built-ins are untouched.
    assert skills["commit"].source == "builtin"


def test_load_skills_project_skill_overrides_a_builtin_by_name(tmp_path):
    # A project skill named `commit` replaces the built-in `commit` (body + source become the file's).
    path = _write_skill(
        _skills_dir(tmp_path), "mycommit.md", name="commit", body="My team's commit ritual."
    )

    skills = loader.load_skills(tmp_path)

    assert skills["commit"].body.strip() == "My team's commit ritual."
    assert skills["commit"].source == str(path.resolve())
    # The other built-in is unaffected and still present.
    assert skills["review-diff"].source == "builtin"


def test_load_skills_working_looks_like_project_commit_wins(tmp_path):
    # AC "working looks like": a project commit.md with a different body wins by name.
    path = _write_skill(
        _skills_dir(tmp_path), "commit.md", name="commit", body="Project commit body."
    )

    skills = loader.load_skills(tmp_path)

    assert skills["commit"].body.strip() == "Project commit body."
    assert skills["commit"].source == str(path.resolve())
