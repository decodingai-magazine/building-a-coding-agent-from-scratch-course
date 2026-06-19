"""Unit tests for :func:`decode.memory.files.discover_memory_files` (ADR-0002 §8).

Discovery walks from ``cwd`` **up to the filesystem root**, collecting ``AGENTS.md`` and
``MEMORY.md`` at every level and **skipping** ``CLAUDE.md`` (the Claude-Code shim). The order
is **root-most first, cwd-most last** so the cwd-most file "wins" (appears last) when the
assembler concatenates — the most specific memory has the final word. These tests build a fake
``cwd → root`` tree under ``tmp_path`` so the walk is hermetic (no real filesystem layout).
"""

from pathlib import Path

import pytest

from decode.memory.files import discover_memory_files


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A resolved tmp root.

    ``discover_memory_files`` resolves ``cwd`` (it must, to walk real parents), so on macOS
    where ``tmp_path`` lives under a ``/var → /private/var`` symlink the discovered paths are
    the *resolved* ones. Resolving here means the expected paths the tests build match.
    """
    return tmp_path.resolve()


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_discovers_agents_and_memory_at_a_single_level(root):
    agents = _write(root / "AGENTS.md")
    memory = _write(root / "MEMORY.md")

    found = discover_memory_files(root)

    assert set(found) == {agents, memory}


def test_walks_cwd_up_to_root_collecting_each_level(root):
    # A three-level tree: tmp_path (root-most) → mid → leaf (cwd).
    root_agents = _write(root / "AGENTS.md")
    mid = root / "mid"
    mid_agents = _write(mid / "AGENTS.md")
    leaf = mid / "leaf"
    leaf_agents = _write(leaf / "AGENTS.md")

    found = discover_memory_files(leaf)

    # Every level contributes its AGENTS.md.
    assert set(found) == {root_agents, mid_agents, leaf_agents}


def test_orders_root_most_first_cwd_most_last(root):
    # cwd-most must appear LAST so it wins when concatenated.
    root_agents = _write(root / "AGENTS.md")
    leaf = root / "a" / "b"
    leaf_agents = _write(leaf / "AGENTS.md")

    found = discover_memory_files(leaf)

    assert found.index(root_agents) < found.index(leaf_agents)
    assert found[-1] == leaf_agents


def test_agents_md_precedes_memory_md_within_a_level(root):
    agents = _write(root / "AGENTS.md")
    memory = _write(root / "MEMORY.md")

    found = discover_memory_files(root)

    assert found.index(agents) < found.index(memory)


def test_skips_claude_md(root):
    _write(root / "CLAUDE.md")
    agents = _write(root / "AGENTS.md")

    found = discover_memory_files(root)

    assert found == [agents]
    assert all(p.name != "CLAUDE.md" for p in found)


def test_returns_empty_list_when_no_memory_files_exist(root):
    leaf = root / "deep" / "nested"
    leaf.mkdir(parents=True)

    assert discover_memory_files(leaf) == []


def test_only_lists_files_that_exist_at_a_level(root):
    # Level has MEMORY.md but no AGENTS.md — only the existing file is collected.
    memory = _write(root / "MEMORY.md")

    found = discover_memory_files(root)

    assert found == [memory]
