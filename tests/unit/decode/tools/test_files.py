"""Unit tests for the read-only file tools (``decode.tools.files``).

ADR-0002 §7: ``read`` (line-paginated, 1-indexed, numbered, truncated), ``glob`` (paths only),
and ``grep`` (regex search) are the read-only file tools. All three:

* **gate** — raise :class:`pydantic_ai.ApprovalRequired` until the call is approved (v1 asks on
  *every* tool, read-only included; the ``read_only=True`` tag is for M3's future auto-allow);
* **resolve paths under ``ctx.deps.cwd``** — never the process cwd;
* return a model-readable :class:`pydantic_ai.ModelRetry` (not a crash) for a missing /
  unreadable path.

These tests drive the tool functions directly with a hand-built :class:`RunContext` (mirroring
``tests/.../test_noop.py``), using ``tmp_path`` for a real filesystem. The
``read_only``-through-the-agent wiring is covered in ``test_registry.py``.
"""

from pathlib import Path

import pytest
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from decode.agent.deps import AgentDeps
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.tools import files


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _ctx(cwd: Path, *, approved: bool = True) -> RunContext[AgentDeps]:
    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=approved)  # type: ignore[arg-type]


# --- gating (every read-only tool still asks in v1) -----------------------------------------


def test_read_requires_approval_when_not_approved(tmp_path: Path):
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    with pytest.raises(ApprovalRequired):
        files.read(_ctx(tmp_path, approved=False), path="f.txt")


def test_glob_requires_approval_when_not_approved(tmp_path: Path):
    with pytest.raises(ApprovalRequired):
        files.glob(_ctx(tmp_path, approved=False), pattern="*.py")


def test_grep_requires_approval_when_not_approved(tmp_path: Path):
    with pytest.raises(ApprovalRequired):
        files.grep(_ctx(tmp_path, approved=False), pattern="x")


# --- read: numbered output, offset/limit windowing ------------------------------------------


def test_read_numbers_lines_one_indexed(tmp_path: Path):
    (tmp_path / "f.txt").write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")

    out = files.read(_ctx(tmp_path), path="f.txt")

    assert out == "1\talpha\n2\tbravo\n3\tcharlie"


def test_read_offset_starts_at_the_requested_line_keeping_line_numbers(tmp_path: Path):
    (tmp_path / "f.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    out = files.read(_ctx(tmp_path), path="f.txt", offset=3)

    # offset is 1-indexed; line numbers stay absolute (so the model can re-page).
    assert out == "3\tthree\n4\tfour"


def test_read_limit_windows_the_number_of_lines(tmp_path: Path):
    (tmp_path / "f.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    out = files.read(_ctx(tmp_path), path="f.txt", offset=2, limit=2)

    assert out == "2\ttwo\n3\tthree"


def test_read_offset_past_end_returns_model_retry(tmp_path: Path):
    (tmp_path / "f.txt").write_text("only-one-line\n", encoding="utf-8")

    with pytest.raises(ModelRetry):
        files.read(_ctx(tmp_path), path="f.txt", offset=99)


def test_read_resolves_relative_to_cwd_not_process_cwd(tmp_path: Path):
    sub = tmp_path / "project"
    sub.mkdir()
    (sub / "f.txt").write_text("inside\n", encoding="utf-8")

    out = files.read(_ctx(sub), path="f.txt")

    assert "inside" in out


def test_read_missing_path_returns_model_retry(tmp_path: Path):
    with pytest.raises(ModelRetry):
        files.read(_ctx(tmp_path), path="nope.txt")


def test_read_directory_returns_model_retry(tmp_path: Path):
    (tmp_path / "adir").mkdir()
    with pytest.raises(ModelRetry):
        files.read(_ctx(tmp_path), path="adir")


def test_read_truncates_long_files_and_reports_the_spill(tmp_path: Path, mocker):
    # Force a tiny line cap so a small fixture overflows; assert the spill path is mentioned.
    mocker.patch("decode.tools.files.settings.max_output_lines", 3, create=False)
    mocker.patch("decode.tools.files.settings.max_output_bytes", 50_000, create=False)
    (tmp_path / "big.txt").write_text("".join(f"L{i}\n" for i in range(100)), encoding="utf-8")

    out = files.read(_ctx(tmp_path), path="big.txt")

    assert "1\tL0" in out
    assert "3\tL2" in out
    assert "4\tL3" not in out  # capped at 3 lines
    assert "truncated" in out.lower()  # the spill notice is appended


# --- glob: paths only, honoring cwd ---------------------------------------------------------


def test_glob_returns_matching_paths_relative_to_cwd(tmp_path: Path):
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")

    out = files.glob(_ctx(tmp_path), pattern="*.py")

    lines = out.splitlines()
    assert lines == ["a.py", "b.py"]  # sorted, only .py, relative to cwd


def test_glob_recursive_pattern_honors_cwd(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "deep.py").write_text("", encoding="utf-8")
    (tmp_path / "top.py").write_text("", encoding="utf-8")

    out = files.glob(_ctx(tmp_path), pattern="**/*.py")

    lines = out.splitlines()
    assert "src/deep.py" in lines
    assert "top.py" in lines


def test_glob_no_matches_returns_model_retry(tmp_path: Path):
    with pytest.raises(ModelRetry):
        files.glob(_ctx(tmp_path), pattern="*.nope")


def test_glob_does_not_escape_cwd(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "own.py").write_text("", encoding="utf-8")

    out = files.glob(_ctx(project), pattern="*.py")

    assert out.splitlines() == ["own.py"]


def test_glob_rejects_dotdot_escaping_pattern(tmp_path: Path):
    # A sibling "secrets" dir with a secret file, OUTSIDE the project cwd.
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "creds.env").write_text("API_KEY=sk-do-not-leak\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "own.py").write_text("", encoding="utf-8")

    # `../secrets/*.env` must NOT enumerate the out-of-tree file — refused, never listed.
    with pytest.raises(ModelRetry):
        files.glob(_ctx(project), pattern="../secrets/*.env")


def test_glob_rejects_absolute_pattern_outside_cwd(tmp_path: Path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    secret_file = secrets / "creds.env"
    secret_file.write_text("API_KEY=sk-do-not-leak\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "own.py").write_text("", encoding="utf-8")

    # An absolute pattern targeting a file outside cwd must be refused.
    with pytest.raises(ModelRetry):
        files.glob(_ctx(project), pattern=str(secret_file))


def test_glob_excludes_symlink_resolving_outside_cwd(tmp_path: Path):
    # A symlink INSIDE cwd that points to a secret file OUTSIDE cwd must not be listed.
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    secret_file = secrets / "creds.env"
    secret_file.write_text("API_KEY=sk-do-not-leak\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "own.env").write_text("", encoding="utf-8")
    (project / "link.env").symlink_to(secret_file)

    out = files.glob(_ctx(project), pattern="*.env")

    # Only the genuinely in-tree file is listed; the escaping symlink is dropped.
    assert out.splitlines() == ["own.env"]


# --- grep: regex search, honoring cwd -------------------------------------------------------


def test_grep_finds_matching_lines_with_file_and_line_prefix(tmp_path: Path):
    (tmp_path / "a.txt").write_text("foo\nbar\nfoobar\n", encoding="utf-8")

    out = files.grep(_ctx(tmp_path), pattern="foo")

    lines = out.splitlines()
    assert "a.txt:1:foo" in lines
    assert "a.txt:3:foobar" in lines
    assert not any(line == "bar\n" for line in lines)


def test_grep_honors_a_regex_pattern(tmp_path: Path):
    (tmp_path / "a.txt").write_text("error 404\nok 200\nerror 500\n", encoding="utf-8")

    out = files.grep(_ctx(tmp_path), pattern=r"error \d+")

    lines = out.splitlines()
    assert "a.txt:1:error 404" in lines
    assert "a.txt:3:error 500" in lines
    assert len(lines) == 2


def test_grep_scopes_to_a_single_path_when_given(tmp_path: Path):
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle\n", encoding="utf-8")

    out = files.grep(_ctx(tmp_path), pattern="needle", path="a.txt")

    lines = out.splitlines()
    assert lines == ["a.txt:1:needle"]


def test_grep_scopes_to_a_glob_when_given(tmp_path: Path):
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle\n", encoding="utf-8")

    out = files.grep(_ctx(tmp_path), pattern="needle", glob="*.py")

    lines = out.splitlines()
    assert lines == ["a.py:1:needle"]


def test_grep_no_matches_returns_model_retry(tmp_path: Path):
    (tmp_path / "a.txt").write_text("nothing here\n", encoding="utf-8")

    with pytest.raises(ModelRetry):
        files.grep(_ctx(tmp_path), pattern="absent-token")


def test_grep_rejects_dotdot_escaping_glob_and_does_not_leak(tmp_path: Path):
    # A sibling "secrets" dir with a secret file, OUTSIDE the project cwd.
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "creds.env").write_text("API_KEY=sk-do-not-leak\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "own.env").write_text("API_KEY=in-tree-ok\n", encoding="utf-8")

    # `grep(glob="../secrets/*.env")` must refuse — never returning the out-of-tree contents.
    with pytest.raises(ModelRetry):
        files.grep(_ctx(project), pattern="API_KEY", glob="../secrets/*.env")


def test_grep_rejects_absolute_glob_outside_cwd_and_does_not_leak(tmp_path: Path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    secret_file = secrets / "creds.env"
    secret_file.write_text("API_KEY=sk-do-not-leak\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "own.env").write_text("API_KEY=in-tree-ok\n", encoding="utf-8")

    # An absolute glob targeting a file outside cwd must be refused.
    with pytest.raises(ModelRetry):
        files.grep(_ctx(project), pattern="API_KEY", glob=str(secret_file))


def test_grep_excludes_symlink_resolving_outside_cwd(tmp_path: Path):
    # A symlink INSIDE cwd pointing to a secret file OUTSIDE cwd must not be searched.
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    secret_file = secrets / "creds.env"
    secret_file.write_text("API_KEY=sk-do-not-leak\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "own.env").write_text("API_KEY=in-tree-ok\n", encoding="utf-8")
    (project / "link.env").symlink_to(secret_file)

    out = files.grep(_ctx(project), pattern="API_KEY", glob="*.env")

    lines = out.splitlines()
    # The in-tree file is searched; the escaping symlink (and its secret) never appears.
    assert lines == ["own.env:1:API_KEY=in-tree-ok"]
    assert not any("sk-do-not-leak" in line for line in lines)


def test_grep_invalid_regex_returns_model_retry(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")

    with pytest.raises(ModelRetry):
        files.grep(_ctx(tmp_path), pattern="(unclosed")


def test_grep_missing_explicit_path_returns_model_retry(tmp_path: Path):
    with pytest.raises(ModelRetry):
        files.grep(_ctx(tmp_path), pattern="x", path="nope.txt")


# --- read-only registration tags ------------------------------------------------------------


def test_file_tools_are_tagged_read_only():
    assert files.READ_TOOL_NAME == "read"
    assert files.GLOB_TOOL_NAME == "glob"
    assert files.GREP_TOOL_NAME == "grep"
    assert files.FILE_TOOLS_READ_ONLY == {"read": True, "glob": True, "grep": True}
