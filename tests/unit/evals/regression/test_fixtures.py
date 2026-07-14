"""Offline tests for the shared regression fixture builders (ADR-0017 §6).

Each builder is exercised on a temp dir / localhost: the type-error module is seeded, the skills
layout is written, the ``http.server`` serves its page and shuts clean (``filterwarnings=error`` would
flag a leaked socket), and the near-limit history reaches its token target with alternating turns.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse

from evals.regression.fixtures import (
    mcp_stdio_server_stub,
    near_limit_history,
    seed_mcp_note,
    seed_skills_dir,
    seed_type_error,
    serve_page,
)
from evals.regression.fixtures.conversation import CHARS_PER_TOKEN, _estimate_tokens


def test_seed_type_error_writes_a_module_with_a_type_error(tmp_path: Path) -> None:
    path = seed_type_error(tmp_path)

    assert path == tmp_path / "buggy.py"
    source = path.read_text(encoding="utf-8")
    assert "def add(left: int, right: int) -> int" in source
    assert 'add("not", "numbers")' in source  # the deliberate type error


def test_seed_type_error_honours_a_custom_filename(tmp_path: Path) -> None:
    path = seed_type_error(tmp_path, filename="mod.py")

    assert path == tmp_path / "mod.py"
    assert path.is_file()


def test_seed_skills_dir_writes_a_skill_md(tmp_path: Path) -> None:
    skill_dir = seed_skills_dir(tmp_path, name="greet", description="Greet someone.")

    assert skill_dir == tmp_path / ".decode" / "skills" / "greet"
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "name: greet" in skill_md
    assert "Greet someone." in skill_md


def test_serve_page_serves_the_body_then_shuts_down() -> None:
    body = "<html><body>known page</body></html>"

    with serve_page(body) as url:
        assert url.startswith("http://127.0.0.1:")
        fetched = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")

    assert fetched == body
    # After the context exits the listener is gone — a fresh connection must fail.
    with pytest.raises(OSError):
        urllib.request.urlopen(url, timeout=1)


def test_serve_page_honours_a_fixed_port() -> None:
    # A probe whose prompt cites the URL verbatim needs a deterministic port, not an ephemeral one.
    with serve_page("<html>fixed</html>", port=8479) as url:
        assert url == "http://127.0.0.1:8479"
        fetched = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")

    assert fetched == "<html>fixed</html>"


def test_near_limit_history_reaches_the_token_target() -> None:
    history = near_limit_history(target_tokens=1000)

    # The coarse estimate reaches (and may slightly exceed) the target — a whole round is never split.
    assert _estimate_tokens(history) >= 1000
    # Alternating user request / assistant response rounds, reusing decode's pydantic-ai message shapes.
    assert all(
        isinstance(history[i], ModelRequest) and isinstance(history[i + 1], ModelResponse)
        for i in range(0, len(history), 2)
    )


def test_near_limit_history_rejects_a_non_positive_target() -> None:
    with pytest.raises(ValueError, match="target_tokens must be positive"):
        near_limit_history(target_tokens=0)


def test_estimate_tokens_uses_the_chars_per_token_divisor() -> None:
    from pydantic_ai.messages import UserPromptPart

    request = ModelRequest(parts=[UserPromptPart(content="x" * (CHARS_PER_TOKEN * 10))])

    assert _estimate_tokens([request]) == 10


def test_seed_mcp_note_writes_the_documentation_note(tmp_path: Path) -> None:
    path = seed_mcp_note(tmp_path)

    assert path == tmp_path / "MCP_FIXTURE.md"
    assert "MCP tool-usage probe" in path.read_text(encoding="utf-8")


def test_mcp_stdio_server_stub_raises_until_mcp_ships(tmp_path: Path) -> None:
    # The stub guards against wiring the MCP probe in before fastmcp + decode's MCP factory land.
    with (
        pytest.raises(NotImplementedError, match="no MCP tool factory yet"),
        mcp_stdio_server_stub(tmp_path),
    ):
        pass
