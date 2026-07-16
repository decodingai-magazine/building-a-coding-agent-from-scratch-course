"""Unit tests for the shared skill payload helper (``decode.skills.payload``) — ADR-0004 §1,5.

``format_skill_payload`` returns the body plus the standing outputs-default trailer (new skill
work products land under ``.decode/outputs/`` unless the user named a destination), and — when
``resource_dir`` is set — a recursive cwd-relative resource manifest in between (the manifest
replaced a directory-only trailer after a live failure: ``glob <dir>/*`` cannot see into subdirs,
so the model guessed wrong paths). An empty walk degrades to the directory-only line. Both the
``skill`` dispatcher and the ``/<skill-name>`` TUI command ride this one helper.
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


# resource_dir is None → body + the outputs default only (no resource manifest)


def test_resourceless_payload_is_the_body_plus_the_outputs_default(tmp_path):
    # A SKILL.md-only skill (built-in or resource-less project skill) gets NO resource manifest:
    # the payload is its body followed only by the standing outputs-default trailer.
    skill = _skill(resource_dir=None)

    result = payload.format_skill_payload(skill, cwd=tmp_path)

    assert result.startswith(skill.body)
    assert ".decode/outputs" in result
    assert "Bundled files" not in result  # no phantom resource manifest


def test_outputs_default_rides_every_payload_and_yields_to_the_user(tmp_path):
    # The outputs default is a standing convention on BOTH payload shapes (resource-less and
    # resource-shipping): new work products land under `.decode/outputs/` — and the trailer says
    # in words that a user-named destination wins.
    resource_dir = tmp_path / ".decode" / "skills" / "deploy"
    (resource_dir / "references").mkdir(parents=True)
    (resource_dir / "references" / "template.md").write_text("t", encoding="utf-8")

    for skill in (_skill(resource_dir=None), _skill(resource_dir=resource_dir)):
        result = payload.format_skill_payload(skill, cwd=tmp_path)
        assert "`.decode/outputs/`" in result
        assert "unless the user" in result.lower()

    # On the resource-shipping payload the outputs default comes AFTER the bundled manifest.
    with_resources = payload.format_skill_payload(_skill(resource_dir=resource_dir), cwd=tmp_path)
    assert with_resources.index("Bundled files") < with_resources.index(".decode/outputs/")


# resource_dir set → body + trailer


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
    # NO bundled files on disk (the dir does not even exist): the payload degrades to the
    # directory-only fallback line — cwd-relative, explaining ``read`` + ``bash`` — rather than
    # failing the dispatch or listing phantom contents.
    resource_dir = tmp_path / ".decode" / "skills" / "deploy"
    skill = _skill(resource_dir=resource_dir)

    result = payload.format_skill_payload(skill, cwd=tmp_path)
    trailer = result[len(skill.body) :]

    assert ".decode/skills/deploy" in trailer  # cwd-relative, not the absolute path
    assert str(resource_dir) not in trailer  # never the absolute path
    assert "read" in trailer and "bash" in trailer  # how to load files / run scripts


def test_trailer_enumerates_every_bundled_file_recursively(tmp_path):
    # The manifest (the live-failure fix): every bundled file — INCLUDING subdir files a
    # ``glob <dir>/*`` would miss — is listed with its exact cwd-relative path, sorted, with the
    # skill's own SKILL.md excluded (its content IS the payload body).
    resource_dir = tmp_path / ".decode" / "skills" / "deploy"
    (resource_dir / "references").mkdir(parents=True)
    (resource_dir / "scripts").mkdir()
    (resource_dir / "SKILL.md").write_text("body source", encoding="utf-8")
    (resource_dir / "references" / "template.md").write_text("t", encoding="utf-8")
    (resource_dir / "scripts" / "fetch.py").write_text("print()", encoding="utf-8")
    skill = _skill(resource_dir=resource_dir)

    result = payload.format_skill_payload(skill, cwd=tmp_path)
    trailer = result[len(skill.body) :]

    assert "- .decode/skills/deploy/references/template.md" in trailer
    assert "- .decode/skills/deploy/scripts/fetch.py" in trailer
    assert "SKILL.md" not in trailer  # the body's own file is never listed
    # Sorted, deterministic order: references/ before scripts/.
    assert trailer.index("references/template.md") < trailer.index("scripts/fetch.py")


def test_every_listed_path_resolves_via_the_read_containment_check(tmp_path):
    # Each manifest line, typed verbatim by the model, must pass the ``read`` tool's containment
    # check and land on the real on-disk file.
    resource_dir = tmp_path / ".decode" / "skills" / "deploy"
    (resource_dir / "references").mkdir(parents=True)
    bundled = resource_dir / "references" / "template.md"
    bundled.write_text("t", encoding="utf-8")
    skill = _skill(resource_dir=resource_dir)

    result = payload.format_skill_payload(skill, cwd=tmp_path)

    listed = [line[2:] for line in result.splitlines() if line.startswith("- ")]
    assert listed == [".decode/skills/deploy/references/template.md"]
    assert _resolve_in_cwd(tmp_path, listed[0]) == bundled.resolve()


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
