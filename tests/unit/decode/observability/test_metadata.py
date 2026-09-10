"""Unit tests for the trace metadata every root span carries (ADR-0022 §10).

`git_sha`, `model`, `sandbox_mode`, `decode_env` are the fields Trace Mining filters and joins on.
Pure and hermetic: the only side effect is one `git rev-parse` subprocess, driven here against a
real throwaway repo and against a plain directory (the `"unknown"` branch).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from decode.config.settings import settings
from decode.observability.metadata import UNKNOWN_GIT_SHA, git_sha, trace_metadata

METADATA_KEYS = {"git_sha", "model", "sandbox_mode", "decode_env"}


@pytest.fixture(autouse=True)
def _clear_git_sha_cache():
    """The sha is resolved once per directory and cached — clear it so tests never share a value."""
    git_sha.cache_clear()
    yield
    git_sha.cache_clear()


def _init_repo(path: Path) -> str:
    """A throwaway git repo with one commit; returns its HEAD sha."""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "file.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "seed",
        ],
        cwd=path,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_git_sha_is_the_head_of_the_directory_it_is_asked_about(tmp_path):
    head = _init_repo(tmp_path)

    assert git_sha(str(tmp_path)) == head


def test_git_sha_of_a_non_repo_is_unknown(tmp_path):
    """A run launched outside a checkout (a Trial's throwaway Harness Home) still gets metadata."""
    assert git_sha(str(tmp_path)) == UNKNOWN_GIT_SHA


def test_trace_metadata_carries_the_four_join_fields(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "sandbox_mode", "docker", raising=False)
    monkeypatch.setattr(settings, "decode_env", "prod", raising=False)

    metadata = trace_metadata()

    assert set(metadata) == METADATA_KEYS
    assert metadata["sandbox_mode"] == "docker"
    assert metadata["decode_env"] == "prod"
    assert metadata["model"] == settings.active_model
    assert metadata["git_sha"] == UNKNOWN_GIT_SHA


def test_trace_metadata_reports_the_model_this_run_actually_uses(monkeypatch, tmp_path):
    """``decode run --model <id>`` must show up as that id, not the configured default."""
    monkeypatch.chdir(tmp_path)

    assert trace_metadata(model="gemini-2.5-pro")["model"] == "gemini-2.5-pro"


def test_trace_metadata_adds_the_kitaru_session_id_only_when_it_is_known(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    assert "kitaru_session_id" not in trace_metadata(kitaru_session_id=None)
    assert trace_metadata(kitaru_session_id="k-1")["kitaru_session_id"] == "k-1"


def test_trace_metadata_values_are_all_strings(monkeypatch, tmp_path):
    """OTLP attributes: every value rides as a plain string, so nothing is dropped on export."""
    monkeypatch.chdir(tmp_path)

    assert all(isinstance(value, str) for value in trace_metadata().values())
