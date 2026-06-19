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

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai import ApprovalRequired, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import TurnContext
from decode.permissions.gate import PermissionGate
from decode.tools import files
from decode.tools.askuser import deny_user_question_resolver


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _ctx(cwd: Path, *, approved: bool = True) -> RunContext[AgentDeps]:
    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=deny_user_question_resolver,
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


# --- write: create / overwrite, gated, contained (task 007) ---------------------------------


def test_write_requires_approval_when_not_approved(tmp_path: Path):
    with pytest.raises(ApprovalRequired):
        files.write(_ctx(tmp_path, approved=False), path="f.txt", content="hi\n")
    # Gated BEFORE any disk touch: the file is never created on a denied/unapproved call.
    assert not (tmp_path / "f.txt").exists()


def test_write_creates_a_new_file(tmp_path: Path):
    out = files.write(_ctx(tmp_path), path="new.txt", content="hello\nworld\n")

    target = tmp_path / "new.txt"
    assert target.read_text(encoding="utf-8") == "hello\nworld\n"
    assert "new.txt" in out  # a model-readable confirmation mentions the path


def test_write_overwrites_an_existing_file(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("old content\n", encoding="utf-8")

    files.write(_ctx(tmp_path), path="f.txt", content="brand new\n")

    assert target.read_text(encoding="utf-8") == "brand new\n"


def test_write_creates_parent_directories(tmp_path: Path):
    files.write(_ctx(tmp_path), path="a/b/c/deep.txt", content="nested\n")

    assert (tmp_path / "a" / "b" / "c" / "deep.txt").read_text(encoding="utf-8") == "nested\n"


def test_write_rejects_dotdot_escape(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(ModelRetry):
        files.write(_ctx(project), path="../escape.txt", content="nope\n")
    # Nothing was written outside the project tree.
    assert not (tmp_path / "escape.txt").exists()


def test_write_rejects_absolute_path_outside_cwd(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"

    with pytest.raises(ModelRetry):
        files.write(_ctx(project), path=str(outside), content="nope\n")
    assert not outside.exists()


def test_write_rejects_symlink_resolving_outside_cwd(tmp_path: Path):
    # An in-tree directory symlink pointing OUTSIDE cwd must not let a write escape.
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ModelRetry):
        files.write(_ctx(project), path="link/escape.txt", content="nope\n")
    assert not (outside / "escape.txt").exists()


def test_denied_write_leaves_an_existing_file_untouched(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"do not change me\n")

    with pytest.raises(ApprovalRequired):
        files.write(_ctx(tmp_path, approved=False), path="f.txt", content="CHANGED\n")

    # Byte-for-byte untouched (gate fires before any disk write).
    assert target.read_bytes() == b"do not change me\n"


# --- edit: exact-then-fuzzy unique match, BOM/EOL preserved (task 007) -----------------------


def test_edit_requires_approval_when_not_approved(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbravo\n", encoding="utf-8")

    with pytest.raises(ApprovalRequired):
        files.edit(
            _ctx(tmp_path, approved=False),
            path="f.txt",
            old_string="alpha",
            new_string="ALPHA",
        )
    # Gated before disk touch: unchanged.
    assert target.read_text(encoding="utf-8") == "alpha\nbravo\n"


def test_edit_replaces_an_exact_unique_match(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")

    files.edit(_ctx(tmp_path), path="f.txt", old_string="return 1", new_string="return 2")

    assert target.read_text(encoding="utf-8") == "def foo():\n    return 2\n"


def test_edit_returns_a_model_readable_confirmation(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("hello\n", encoding="utf-8")

    out = files.edit(_ctx(tmp_path), path="f.txt", old_string="hello", new_string="bye")

    assert "f.txt" in out


def test_edit_whitespace_fuzzy_match_when_no_exact_match(tmp_path: Path):
    # The file uses 4-space indentation; the model sends old_string with different (tab)
    # whitespace. Exact match fails, whitespace-normalized fuzzy match finds the unique span.
    target = tmp_path / "f.txt"
    target.write_text("def foo():\n    x = 1\n    return x\n", encoding="utf-8")

    files.edit(
        _ctx(tmp_path),
        path="f.txt",
        old_string="x = 1\n\treturn x",  # tab + spacing differs from the file's whitespace
        new_string="x = 2\n    return x",
    )

    assert target.read_text(encoding="utf-8") == "def foo():\n    x = 2\n    return x\n"


def test_edit_empty_old_string_returns_model_retry(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"content\n")

    with pytest.raises(ModelRetry, match="empty"):
        files.edit(_ctx(tmp_path), path="f.txt", old_string="", new_string="x")
    # Untouched.
    assert target.read_bytes() == b"content\n"


def test_edit_no_match_returns_model_retry(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"alpha\nbravo\n")

    with pytest.raises(ModelRetry, match="not found"):
        files.edit(_ctx(tmp_path), path="f.txt", old_string="charlie", new_string="x")
    assert target.read_bytes() == b"alpha\nbravo\n"


def test_edit_ambiguous_match_returns_model_retry_with_count(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"x = 1\nx = 1\nx = 1\n")

    with pytest.raises(ModelRetry, match="ambiguous"):
        files.edit(_ctx(tmp_path), path="f.txt", old_string="x = 1", new_string="x = 2")
    # The message names how many matches there were so the model can disambiguate.
    with pytest.raises(ModelRetry, match="3"):
        files.edit(_ctx(tmp_path), path="f.txt", old_string="x = 1", new_string="x = 2")
    assert target.read_bytes() == b"x = 1\nx = 1\nx = 1\n"


def test_edit_preserves_crlf_line_endings(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"alpha\r\nbravo\r\ncharlie\r\n")

    files.edit(_ctx(tmp_path), path="f.txt", old_string="bravo", new_string="BRAVO")

    # The replacement landed AND every line ending stayed CRLF (no LF leaked in).
    assert target.read_bytes() == b"alpha\r\nBRAVO\r\ncharlie\r\n"


def test_edit_preserves_cr_line_endings(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"alpha\rbravo\rcharlie\r")

    files.edit(_ctx(tmp_path), path="f.txt", old_string="bravo", new_string="BRAVO")

    assert target.read_bytes() == b"alpha\rBRAVO\rcharlie\r"


def test_edit_preserves_utf8_bom(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"\xef\xbb\xbfalpha\nbravo\n")

    files.edit(_ctx(tmp_path), path="f.txt", old_string="alpha", new_string="ALPHA")

    # The BOM is restored verbatim and the edit applied.
    assert target.read_bytes() == b"\xef\xbb\xbfALPHA\nbravo\n"


def test_edit_preserves_bom_and_crlf_together(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"\xef\xbb\xbfalpha\r\nbravo\r\n")

    files.edit(_ctx(tmp_path), path="f.txt", old_string="bravo", new_string="BRAVO")

    assert target.read_bytes() == b"\xef\xbb\xbfalpha\r\nBRAVO\r\n"


def test_edit_missing_file_returns_model_retry(tmp_path: Path):
    with pytest.raises(ModelRetry):
        files.edit(_ctx(tmp_path), path="nope.txt", old_string="x", new_string="y")


def test_edit_rejects_dotdot_escape(tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret\n")
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(ModelRetry):
        files.edit(_ctx(project), path="../outside.txt", old_string="secret", new_string="x")
    assert outside.read_bytes() == b"secret\n"


def test_edit_rejects_absolute_path_outside_cwd(tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret\n")
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(ModelRetry):
        files.edit(_ctx(project), path=str(outside), old_string="secret", new_string="x")
    assert outside.read_bytes() == b"secret\n"


def test_edit_rejects_symlink_resolving_outside_cwd(tmp_path: Path):
    # An in-tree symlink pointing to a file OUTSIDE cwd must not be editable.
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / "link.txt").symlink_to(outside)

    with pytest.raises(ModelRetry):
        files.edit(_ctx(project), path="link.txt", old_string="secret", new_string="x")
    assert outside.read_bytes() == b"secret\n"


def test_denied_edit_leaves_the_file_untouched(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"alpha\r\nbravo\r\n")

    with pytest.raises(ApprovalRequired):
        files.edit(
            _ctx(tmp_path, approved=False),
            path="f.txt",
            old_string="bravo",
            new_string="CHANGED",
        )

    # Byte-for-byte untouched (gate fires before reading or writing).
    assert target.read_bytes() == b"alpha\r\nbravo\r\n"


def test_mutating_file_tools_are_tagged_not_read_only():
    assert files.WRITE_TOOL_NAME == "write"
    assert files.EDIT_TOOL_NAME == "edit"
    assert files.FILE_TOOLS_MUTATING == {"write": False, "edit": False}


# --- through a real agent: gated write/edit, then approve (task 007) -------------------------


def _agent(mocker):
    """A real `decode` agent built with a dummy key (the model is overridden per test)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


def _tool_then_text(tool_name: str, json_args: str, final_text: str = "done"):
    """A streaming FunctionModel that calls ``tool_name`` on leg 1, then returns text.

    The first model request streams a single tool call (which raises ``ApprovalRequired``
    until approved, so the leg resolves to ``DeferredToolRequests``); every later (resume) leg
    streams ``final_text`` so the turn terminates.
    """
    state = {"calls": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        state["calls"] += 1
        if state["calls"] == 1:
            yield {0: DeltaToolCall(name=tool_name, json_args=json_args)}
        else:
            yield final_text

    return FunctionModel(stream_function=stream_function)


def _drive_to_completion(handler: AgentTurnHandler, prompt: str, sink: list[events.Event]):
    """Drive a turn handler to completion, approving at every model-request boundary."""

    async def _run() -> None:
        agen = handler(TurnContext(0, prompt, sink.append))
        with contextlib.suppress(StopAsyncIteration):
            await agen.asend(None)
            while True:
                await agen.asend([])
        await agen.aclose()

    return _run


async def test_write_runs_through_the_agent_when_approved(tmp_path: Path, mocker):
    emitted: list[events.Event] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    deps = AgentDeps(
        cwd=tmp_path,
        emit=emitted.append,
        gate=PermissionGate(),
        resolve_permission=approving_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    agent = _agent(mocker)
    handler = AgentTurnHandler(agent, deps=deps)

    model = _tool_then_text(
        files.WRITE_TOOL_NAME,
        '{"path": "out.txt", "content": "written by the agent\\n"}',
        final_text="wrote the file",
    )
    with agent.override(model=model):
        await _drive_to_completion(handler, "create out.txt", emitted)()

    # The gated write was surfaced, approved, and actually hit disk.
    perms = [e for e in emitted if isinstance(e, events.PermissionRequested)]
    assert perms and perms[0].name == "write"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "written by the agent\n"


async def test_edit_runs_through_the_agent_when_approved(tmp_path: Path, mocker):
    target = tmp_path / "f.txt"
    target.write_text("the quick brown fox\n", encoding="utf-8")

    emitted: list[events.Event] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    deps = AgentDeps(
        cwd=tmp_path,
        emit=emitted.append,
        gate=PermissionGate(),
        resolve_permission=approving_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    agent = _agent(mocker)
    handler = AgentTurnHandler(agent, deps=deps)

    model = _tool_then_text(
        files.EDIT_TOOL_NAME,
        '{"path": "f.txt", "old_string": "quick", "new_string": "slow"}',
        final_text="edited the file",
    )
    with agent.override(model=model):
        await _drive_to_completion(handler, "edit f.txt", emitted)()

    perms = [e for e in emitted if isinstance(e, events.PermissionRequested)]
    assert perms and perms[0].name == "edit"
    assert target.read_text(encoding="utf-8") == "the slow brown fox\n"
