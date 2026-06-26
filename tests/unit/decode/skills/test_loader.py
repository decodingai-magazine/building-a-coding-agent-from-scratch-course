"""Unit tests for the Skills Catalog loader (``decode.skills.loader``; ADR-0004 §3,§5).

A skill follows the **Agent Skills directory convention**: a directory ``<name>/SKILL.md``. The loader
reads skills from two sources and merges them (ADR-0004 §3):

* **built-in** — the bundled ``builtin/<name>/SKILL.md`` directories loaded as *packaged data*
  (``importlib.resources`` nested traversal, so they ship in the wheel), validated into
  :class:`~decode.entities.skill_def.SkillDef`, keyed by frontmatter ``name``, ``source == "builtin"``,
  ``resource_dir is None`` always. A built-in parse failure raises loudly.
* **project-local** — ``<cwd>/<settings.skills_dir>/<name>/SKILL.md`` (cwd-relative, like memory),
  ``source`` set to the absolute ``SKILL.md`` path; ``resource_dir`` set to the skill's directory iff it
  ships sibling resources. A malformed/unreadable ``SKILL.md`` (or a subdir without one) is logged at
  WARNING and skipped; a missing dir yields ``{}``.

:func:`load_skills` merges built-ins first then project skills, so a project skill whose frontmatter
``name`` equals a built-in's intentionally overrides it. These tests pin the two built-ins (the active
``commit`` body, the advisory ``review-diff`` body), the packaged-data nested traversal, frontmatter/body
parsing + error messages, project discovery + keying by frontmatter name, ``resource_dir`` set/unset, the
directory-name-mismatch warning, skip-with-warning vs raise asymmetry, the dropped flat format, and the
project-override-by-name merge.
"""

import importlib.resources
import logging
from pathlib import Path

import pytest
import yaml

from decode.config.settings import settings
from decode.entities.skill_def import SkillDef
from decode.skills import loader

_BUILTIN_NAMES = {"commit", "review-diff"}


def _write_skill(
    skills_dir: Path, dir_name: str, *, name: str | None = None, body: str = "Do the thing."
) -> Path:
    """Write a minimal valid project skill ``<skills_dir>/<dir_name>/SKILL.md`` and return its path.

    The frontmatter ``name`` defaults to ``dir_name`` (the common, mismatch-free case); pass ``name``
    explicitly to exercise the cosmetic-directory-name path.
    """
    name = dir_name if name is None else name
    skill_dir = skills_dir / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    text = f"---\nname: {name}\ndescription: a {name} skill\n---\n{body}\n"
    path.write_text(text, encoding="utf-8")
    return path


def _write_resource(skill_dir: Path, relpath: str, content: str = "resource") -> Path:
    """Write a sibling resource file under a skill's directory (makes it a tier-3 skill)."""
    path = skill_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
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
    custom = _write_skill(tmp_path / "custom" / "skills", "custom-skill")

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


def test_each_builtin_has_no_resource_dir():
    # ADR-0004 §3: built-ins are SKILL.md-only — their resources would live unreadable in
    # site-packages, so ``resource_dir`` is always ``None`` for a built-in.
    skills = loader.load_builtin_skills()

    assert all(skill.resource_dir is None for skill in skills.values())


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


def test_builtin_skills_are_packaged_directories_each_with_a_skill_md():
    # The directory convention as packaged data: ``builtin/<name>/SKILL.md`` walked via
    # importlib.resources (not a hard-coded filesystem path), no flat ``*.md`` left.
    package = importlib.resources.files("decode.skills.builtin")
    skill_dirs = {entry.name for entry in package.iterdir() if entry.is_dir()}

    assert skill_dirs >= _BUILTIN_NAMES  # commit/ and review-diff/ are subdirectories
    for name in _BUILTIN_NAMES:
        skill_file = package / name / "SKILL.md"
        assert skill_file.is_file()
        assert skill_file.read_text(encoding="utf-8").strip()
    # The flat format is gone — no top-level ``*.md`` ships in the built-in package.
    assert not [entry for entry in package.iterdir() if entry.name.endswith(".md")]


def test_load_builtin_skills_skips_a_dir_without_a_skill_md(mocker, tmp_path, caplog):
    # A built-in subdir lacking a SKILL.md is skipped (logged), not a crash — point the loader at a
    # fake built-in package: a valid skill dir + an empty dir + a stray top-level file.
    fake_pkg = tmp_path / "fake_builtin"
    _write_skill(fake_pkg, "good")
    (fake_pkg / "empty").mkdir()  # a directory with no SKILL.md
    (fake_pkg / "README.txt").write_text("not a skill", encoding="utf-8")  # a non-dir entry
    mocker.patch.object(loader.importlib.resources, "files", return_value=fake_pkg)

    with caplog.at_level(logging.DEBUG):
        skills = loader.load_builtin_skills()

    assert set(skills) == {"good"}
    assert any("empty" in record.getMessage() for record in caplog.records)


def test_load_builtin_skills_raises_loudly_on_a_malformed_skill_md(mocker, tmp_path):
    # The built-in/project asymmetry (ADR-0004 §3): a malformed *built-in* SKILL.md is a packaging
    # bug, so it raises loudly (unlike a project skill, which is skipped with a warning).
    fake_pkg = tmp_path / "fake_builtin"
    bad_dir = fake_pkg / "broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("no frontmatter, just a body\n", encoding="utf-8")
    mocker.patch.object(loader.importlib.resources, "files", return_value=fake_pkg)

    with pytest.raises(ValueError, match="broken"):
        loader.load_builtin_skills()


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


def test_parse_skill_file_defaults_resource_dir_to_none():
    text = "---\nname: demo\ndescription: x\n---\nBody.\n"

    skill = loader.parse_skill_file(text, source="builtin")

    assert skill.resource_dir is None


def test_parse_skill_file_threads_resource_dir_through():
    # The loader supplies ``resource_dir`` for a project skill that ships resources; parse threads it
    # into the constructed SkillDef unchanged.
    text = "---\nname: demo\ndescription: x\n---\nBody.\n"
    resource_dir = Path(".decode/skills/demo")

    skill = loader.parse_skill_file(text, source="/abs/demo/SKILL.md", resource_dir=resource_dir)

    assert skill.resource_dir == resource_dir


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
    # A directory `renamed/` whose SKILL.md frontmatter is `name: actual` keys as `actual` (dir name
    # cosmetic); ``source`` is the absolute SKILL.md path.
    path = _write_skill(_skills_dir(tmp_path), "renamed", name="actual")

    found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"actual"}
    assert found["actual"].source == str(path.resolve())


def test_discover_finds_multiple_project_skills(tmp_path):
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "alpha")
    _write_skill(skills_dir, "beta")

    found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"alpha", "beta"}


def test_discover_missing_dir_returns_empty_dict(tmp_path):
    assert loader.discover_project_skills(tmp_path) == {}


def test_discover_empty_dir_returns_empty_dict(tmp_path):
    _skills_dir(tmp_path).mkdir(parents=True)

    assert loader.discover_project_skills(tmp_path) == {}


def test_discover_skips_a_subdirectory_without_a_skill_md_with_a_warning(tmp_path, caplog):
    # A subdirectory lacking a SKILL.md is skipped (logged), never a crash; siblings still load.
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "good")
    (skills_dir / "incomplete").mkdir()  # a skill dir with no SKILL.md

    with caplog.at_level(logging.WARNING):
        found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"good"}
    assert any(
        record.levelno == logging.WARNING and "incomplete" in record.getMessage()
        for record in caplog.records
    )


def test_discover_does_not_discover_a_loose_flat_md(tmp_path):
    # The flat format is gone (hard switch): a loose ``<skills_dir>/legacy.md`` (no enclosing
    # ``<name>/`` directory) is NOT discovered — only ``<name>/SKILL.md`` directories are skills.
    skills_dir = _skills_dir(tmp_path)
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "legacy.md").write_text(
        "---\nname: legacy\ndescription: flat\n---\nFlat body.\n", encoding="utf-8"
    )

    found = loader.discover_project_skills(tmp_path)

    assert found == {}


def test_discover_skips_a_malformed_project_skill_with_a_warning(tmp_path, caplog):
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "good")
    # A malformed SKILL.md (no frontmatter) must be skipped, not crash the agent.
    bad_dir = skills_dir / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("not a skill, no frontmatter\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"good"}  # the good one still loads
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert any("bad" in record.getMessage() for record in caplog.records)


def test_discover_skips_an_unreadable_project_skill_with_a_warning(tmp_path, caplog, mocker):
    # A SKILL.md present but unreadable (OSError on read) is skipped, not fatal — the OSError branch
    # of the (ValueError, OSError, yaml.YAMLError) catch. Simulated deterministically (no chmod, which
    # root bypasses in CI) by forcing read_text to raise only for the broken skill's SKILL.md.
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "good")
    broken = _write_skill(skills_dir, "broken")

    real_read_text = Path.read_text

    def _read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == broken:
            raise OSError("simulated unreadable SKILL.md")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    mocker.patch.object(Path, "read_text", _read_text)

    with caplog.at_level(logging.WARNING):
        found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"good"}
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# --- resource_dir (tier-3 bundled resources; ADR-0004 §5) -----------------------------------


def test_discover_sets_resource_dir_when_the_skill_ships_a_sibling(tmp_path):
    # A project skill whose directory holds anything besides SKILL.md (here a ``references/x.md``)
    # gets ``resource_dir`` set to its directory.
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "deploy")
    _write_resource(skills_dir / "deploy", "references/x.md")

    found = loader.discover_project_skills(tmp_path)

    assert found["deploy"].resource_dir == skills_dir / "deploy"


def test_discover_resource_dir_is_none_when_only_skill_md(tmp_path):
    # A project skill whose directory contains only SKILL.md ships no resources → ``None``.
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "deploy")

    found = loader.discover_project_skills(tmp_path)

    assert found["deploy"].resource_dir is None


def test_discover_resource_dir_is_cwd_joined_not_resolved(tmp_path):
    # 033 renders ``resource_dir`` cwd-relative, so it must stay cwd-joined and un-``.resolve()``d
    # (``source`` keeps the resolved absolute path; ``resource_dir`` does not).
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "deploy")
    _write_resource(skills_dir / "deploy", "scripts/run.sh")

    found = loader.discover_project_skills(tmp_path)

    expected = tmp_path / settings.skills_dir / "deploy"
    assert found["deploy"].resource_dir == expected
    # ``source`` keeps the resolved absolute SKILL.md path; ``resource_dir`` stays the cwd-joined dir.
    assert str(found["deploy"].source).endswith("deploy/SKILL.md")


def test_discover_resource_dir_set_for_a_sibling_directory_too(tmp_path):
    # "anything besides SKILL.md" includes a sibling *folder*, not just a file.
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "deploy")
    (skills_dir / "deploy" / "examples").mkdir()

    found = loader.discover_project_skills(tmp_path)

    assert found["deploy"].resource_dir == skills_dir / "deploy"


# --- directory-name vs frontmatter-name mismatch (ADR-0004 §3) ------------------------------


def test_discover_dir_name_mismatch_loads_and_warns(tmp_path, caplog):
    # A directory ``foo/`` whose SKILL.md is ``name: bar`` still loads (keyed by ``bar``), but the
    # mismatch is logged at WARNING to catch copy-paste slips.
    _write_skill(_skills_dir(tmp_path), "foo", name="bar")

    with caplog.at_level(logging.WARNING):
        found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"bar"}  # keyed by frontmatter name; dir name cosmetic
    assert any(
        record.levelno == logging.WARNING
        and "foo" in record.getMessage()
        and "bar" in record.getMessage()
        for record in caplog.records
    )


def test_discover_matching_dir_name_does_not_warn(tmp_path, caplog):
    # The common case (dir name == frontmatter name) loads without a mismatch warning.
    _write_skill(_skills_dir(tmp_path), "deploy")

    with caplog.at_level(logging.WARNING):
        found = loader.discover_project_skills(tmp_path)

    assert set(found) == {"deploy"}
    assert not [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "cosmetic" in record.getMessage()
    ]


# Frontmatter whose YAML is *syntactically* malformed — each raises ``yaml.YAMLError``
# (``ScannerError``), which is NOT a ``ValueError``. The wrapping ``---`` fences are added by the
# test; only the inner lines below are the (broken) frontmatter. (task-030 / ADR-0004 §3.)
_MALFORMED_YAML_FRONTMATTER = {
    "unquoted_colon": "name: ok\ndescription: a value: with an unquoted colon",
    "unterminated_quote": 'name: ok\ndescription: "unterminated',
    "tab_indent": "name: ok\n\tdescription: tab-indented",
}


@pytest.mark.parametrize(
    "frontmatter",
    list(_MALFORMED_YAML_FRONTMATTER.values()),
    ids=list(_MALFORMED_YAML_FRONTMATTER),
)
def test_discover_skips_a_malformed_yaml_project_skill_with_a_warning(
    tmp_path, caplog, frontmatter
):
    # A project skill whose frontmatter is broken YAML (``yaml.YAMLError``, not ``ValueError``) must be
    # skipped with a WARNING, not crash the session — load_skills runs every turn (ADR-0004 §3).
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "good")
    bad_dir = skills_dir / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nBody.\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        found = loader.discover_project_skills(tmp_path)

    # The sibling valid skill still loads; the malformed one is skipped (no raise).
    assert set(found) == {"good"}
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_discover_malformed_yaml_warning_names_the_offending_file(tmp_path, caplog):
    # The WARNING must name the offending file (its ``source`` SKILL.md path) so the typo is debuggable.
    skills_dir = _skills_dir(tmp_path)
    bad_dir = skills_dir / "typo"
    bad_dir.mkdir(parents=True, exist_ok=True)
    bad_path = bad_dir / "SKILL.md"
    bad_path.write_text('---\nname: ok\ndescription: "unterminated\n---\nBody.\n', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        loader.discover_project_skills(tmp_path)

    source = str(bad_path.resolve())
    assert any(
        record.levelno == logging.WARNING and source in record.getMessage()
        for record in caplog.records
    )


# --- load_skills (merge) --------------------------------------------------------------------


def test_load_skills_returns_builtins_when_no_project_skills(tmp_path):
    skills = loader.load_skills(tmp_path)

    assert set(skills) == _BUILTIN_NAMES
    assert all(skill.source == "builtin" for skill in skills.values())


def test_load_skills_includes_project_only_skills_alongside_builtins(tmp_path):
    _write_skill(_skills_dir(tmp_path), "deploy")

    skills = loader.load_skills(tmp_path)

    assert set(skills) == _BUILTIN_NAMES | {"deploy"}
    assert skills["deploy"].source.endswith("SKILL.md")
    # The built-ins are untouched.
    assert skills["commit"].source == "builtin"


def test_load_skills_project_skill_overrides_a_builtin_by_name(tmp_path):
    # A project skill named `commit` replaces the built-in `commit` (body + source become the file's).
    path = _write_skill(_skills_dir(tmp_path), "commit", body="My team's commit ritual.")

    skills = loader.load_skills(tmp_path)

    assert skills["commit"].body.strip() == "My team's commit ritual."
    assert skills["commit"].source == str(path.resolve())
    # The other built-in is unaffected and still present.
    assert skills["review-diff"].source == "builtin"


def test_load_skills_working_looks_like_project_commit_wins(tmp_path):
    # AC "working looks like": a project ``commit/SKILL.md`` with a different body wins by name.
    path = _write_skill(_skills_dir(tmp_path), "commit", body="Project commit body.")

    skills = loader.load_skills(tmp_path)

    assert skills["commit"].body.strip() == "Project commit body."
    assert skills["commit"].source == str(path.resolve())


def test_load_skills_project_override_carries_its_resource_dir(tmp_path):
    # An overriding project skill that ships resources wins with its ``resource_dir`` (built-in had
    # ``None``); the project body/source/resource_dir all win.
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "commit", body="Project commit body.")
    _write_resource(skills_dir / "commit", "references/style.md")

    skills = loader.load_skills(tmp_path)

    assert skills["commit"].resource_dir == skills_dir / "commit"


def test_load_skills_does_not_raise_on_a_malformed_yaml_project_skill(tmp_path, caplog):
    # The live-session crash path: load_skills runs every turn via the catalog hook, so a single
    # typo'd `.decode/skills/<name>/SKILL.md` with broken YAML must NOT propagate — built-ins + valid
    # project skills come back, the bad one is skipped (ADR-0004 §3).
    skills_dir = _skills_dir(tmp_path)
    _write_skill(skills_dir, "deploy")
    bad_dir = skills_dir / "broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text(
        '---\nname: ok\ndescription: "unterminated\n---\nBody.\n', encoding="utf-8"
    )

    with caplog.at_level(logging.WARNING):
        skills = loader.load_skills(tmp_path)

    assert set(skills) == _BUILTIN_NAMES | {"deploy"}
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# --- built-in / project asymmetry (ADR-0004 §3) ---------------------------------------------


def test_parse_skill_file_propagates_malformed_yaml_frontmatter():
    # The built-in path (`load_builtin_skills`) catches only ValueError, so a malformed-YAML built-in
    # still raises loudly — the built-in/project asymmetry (ADR-0004 §3): a packaging bug surfaces, a
    # user's project typo is skipped. parse_skill_file must NOT swallow yaml errors.
    text = '---\nname: demo\ndescription: "unterminated\n---\nBody.\n'

    with pytest.raises(yaml.YAMLError):
        loader.parse_skill_file(text, source="builtin")
