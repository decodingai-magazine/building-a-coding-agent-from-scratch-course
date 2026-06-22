"""Unit tests for :func:`decode.memory.files.discover_memory_files` (ADR-0002 §8, Fix 1).

Discovery has two kinds of memory, found differently:

* ``AGENTS.md`` — human/project memory, **walked** from ``cwd`` up to the filesystem root,
  ordered **root-most first, cwd-most last** so the cwd-most file "wins" (appears last). The
  Claude-Code shim ``CLAUDE.md`` is skipped.
* ``MEMORY.md`` — the harness-extracted scratch memory. **Not walked**: it is the single file
  ``cwd/.decode/MEMORY.md``, appended **last** of all so it has the final word.

These tests build a fake ``cwd → root`` tree under ``tmp_path`` so the walk is hermetic (no real
filesystem layout).
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


def _harness_memory(cwd: Path) -> Path:
    """The single harness MEMORY.md path under ``cwd`` (Fix 1: ``cwd/.decode/MEMORY.md``)."""
    return cwd / ".decode" / "MEMORY.md"


def test_discovers_agents_and_harness_memory_at_cwd(root):
    agents = _write(root / "AGENTS.md")
    memory = _write(_harness_memory(root))

    found = discover_memory_files(root)

    assert set(found) == {agents, memory}


def test_walks_cwd_up_to_root_collecting_each_agents_md(root):
    # A three-level tree: tmp_path (root-most) → mid → leaf (cwd). AGENTS.md is walked.
    root_agents = _write(root / "AGENTS.md")
    mid = root / "mid"
    mid_agents = _write(mid / "AGENTS.md")
    leaf = mid / "leaf"
    leaf_agents = _write(leaf / "AGENTS.md")

    found = discover_memory_files(leaf)

    # Every level contributes its AGENTS.md.
    assert set(found) == {root_agents, mid_agents, leaf_agents}


def test_orders_agents_root_most_first_cwd_most_last(root):
    # cwd-most AGENTS.md must appear LAST so it wins when concatenated.
    root_agents = _write(root / "AGENTS.md")
    leaf = root / "a" / "b"
    leaf_agents = _write(leaf / "AGENTS.md")

    found = discover_memory_files(leaf)

    assert found.index(root_agents) < found.index(leaf_agents)
    assert found[-1] == leaf_agents


def test_harness_memory_is_not_walked_only_cwd_decode_dir_counts(root):
    # An ancestor MEMORY.md (or a project-root MEMORY.md) is NOT memory anymore: only the single
    # cwd/.decode/MEMORY.md is the harness file (Fix 1).
    leaf = root / "a" / "b"
    leaf.mkdir(parents=True)
    _write(root / "MEMORY.md")  # an ancestor MEMORY.md — must be ignored
    _write(leaf / "MEMORY.md")  # a project-root MEMORY.md — must be ignored
    harness = _write(_harness_memory(leaf))  # the real harness file

    found = discover_memory_files(leaf)

    assert found == [harness]


def test_harness_memory_appears_last_after_agents(root):
    # The harness MEMORY.md has the final word: it is appended after every AGENTS.md.
    root_agents = _write(root / "AGENTS.md")
    leaf_agents = _write(root / "sub" / "AGENTS.md")
    harness = _write(_harness_memory(root / "sub"))

    found = discover_memory_files(root / "sub")

    assert found[-1] == harness
    assert found.index(root_agents) < found.index(harness)
    assert found.index(leaf_agents) < found.index(harness)


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


def test_only_lists_files_that_exist(root):
    # cwd has the harness MEMORY.md but no AGENTS.md anywhere — only the existing file is collected.
    memory = _write(_harness_memory(root))

    found = discover_memory_files(root)

    assert found == [memory]
