"""Unit tests for :func:`decode.memory.service.assemble_memory` (ADR-0002 §8).

``assemble_memory`` reads the files :func:`~decode.memory.files.discover_memory_files` found,
concatenates them with a **provenance header** (``# From <abs path>``) per file, and caps
**``MEMORY.md`` specifically** at ``settings.memory_max_lines`` lines AND
``settings.memory_max_bytes`` bytes — whichever bites first — appending a **visible truncation
note** so the model knows there is more. Missing / unreadable files are skipped (never raise),
and an empty discovery yields ``""``. The line cap and the byte cap are exercised independently.
"""

from pathlib import Path

from decode.config.settings import settings
from decode.memory.service import assemble_memory, clip_lines_to_budget


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _harness_memory(cwd: Path) -> Path:
    """The single harness MEMORY.md path under ``cwd`` (Fix 1: ``cwd/.decode/MEMORY.md``)."""
    return cwd / ".decode" / "MEMORY.md"


def test_returns_empty_string_when_no_files_found(tmp_path):
    leaf = tmp_path / "empty"
    leaf.mkdir()

    assert assemble_memory(leaf) == ""


def test_adds_a_provenance_header_naming_the_absolute_path(tmp_path):
    agents = _write(tmp_path / "AGENTS.md", "use tabs not spaces")

    assembled = assemble_memory(tmp_path)

    assert f"# From {agents}" in assembled
    assert "use tabs not spaces" in assembled


def test_concatenates_root_then_cwd_so_cwd_most_wins(tmp_path):
    root_agents = _write(tmp_path / "AGENTS.md", "ROOT RULE")
    leaf = tmp_path / "sub"
    leaf_agents = _write(leaf / "AGENTS.md", "LEAF RULE")

    assembled = assemble_memory(leaf)

    # Both present; the cwd-most (leaf) file appears AFTER the root file.
    assert assembled.index(str(root_agents)) < assembled.index(str(leaf_agents))
    assert assembled.index("ROOT RULE") < assembled.index("LEAF RULE")


def test_missing_files_are_skipped_not_errors(tmp_path):
    # Nothing on disk at all: no exception, empty result.
    leaf = tmp_path / "nope"
    leaf.mkdir()

    assert assemble_memory(leaf) == ""


def test_unreadable_file_is_skipped_without_raising(tmp_path, mocker):
    # A discovered file that errors on read must be silently skipped, not crash assembly.
    # Compare resolved paths: discovery resolves cwd, so the file the service reads is the
    # resolved path (macOS /var → /private/var), not the raw tmp_path-relative one.
    bad = _write(_harness_memory(tmp_path), "secret").resolve()
    _write(tmp_path / "AGENTS.md", "VISIBLE")

    real_read_text = Path.read_text

    def flaky_read_text(self, *args, **kwargs):
        if self.resolve() == bad:
            raise OSError("boom")
        return real_read_text(self, *args, **kwargs)

    mocker.patch.object(Path, "read_text", flaky_read_text)

    assembled = assemble_memory(tmp_path)

    assert "VISIBLE" in assembled
    assert "secret" not in assembled


def test_memory_md_is_capped_by_line_count_with_a_visible_note(tmp_path):
    # Far more than the line cap, but each line is tiny so the BYTE cap never bites: only the
    # line cap can clip this. Lines are well under memory_max_bytes in total.
    line_count = settings.memory_max_lines + 50
    body = "\n".join(f"line{i}" for i in range(line_count))
    _write(_harness_memory(tmp_path), body)

    assembled = assemble_memory(tmp_path)

    # The last line is dropped by the line cap; the truncation note is visible.
    assert "truncated" in assembled.lower()
    assert f"line{line_count - 1}" not in assembled
    # First lines survive.
    assert "line0" in assembled


def test_memory_md_is_capped_by_byte_count_with_a_visible_note(tmp_path):
    # Few lines (well under the line cap) but each line is huge, so the BYTE cap bites first.
    big_line = "z" * (settings.memory_max_bytes // 2)
    body = "\n".join([big_line, big_line, big_line])  # 3 lines, ~1.5x the byte cap
    _write(_harness_memory(tmp_path), body)

    assembled = assemble_memory(tmp_path)

    assert "truncated" in assembled.lower()
    # The assembled memory must be well under twice the cap (proves clipping happened).
    assert len(assembled.encode("utf-8")) < settings.memory_max_bytes * 2


def test_agents_md_is_not_capped(tmp_path):
    # AGENTS.md is authored by the project and trusted; only MEMORY.md (model-written) is capped.
    line_count = settings.memory_max_lines + 100
    body = "\n".join(f"a{i}" for i in range(line_count))
    _write(tmp_path / "AGENTS.md", body)

    assembled = assemble_memory(tmp_path)

    assert f"a{line_count - 1}" in assembled
    assert "truncated" not in assembled.lower()


def test_short_memory_md_is_not_marked_truncated(tmp_path):
    _write(_harness_memory(tmp_path), "one short note")

    assembled = assemble_memory(tmp_path)

    assert "one short note" in assembled
    assert "truncated" not in assembled.lower()


def test_includes_agents_md(tmp_path):
    _write(tmp_path / "AGENTS.md", "content of AGENTS.md")

    assembled = assemble_memory(tmp_path)

    assert "content of AGENTS.md" in assembled


def test_includes_harness_memory_md(tmp_path):
    # The harness MEMORY.md is the single cwd/.decode/MEMORY.md (Fix 1).
    _write(_harness_memory(tmp_path), "content of MEMORY.md")

    assembled = assemble_memory(tmp_path)

    assert "content of MEMORY.md" in assembled


# clip_lines_to_budget — the one shared core of both memory budgeters (task: structural tidy).
# It replaced the head-keeping ``service._clip_to_budget`` and the tail-keeping
# ``extract._trim_keeping_recent``; these pin the two ends differ ONLY in which end survives.

_FIVE = ["aaaa", "bbbb", "cccc", "dddd", "eeee"]


def test_clip_keep_head_keeps_the_first_lines():
    # Line cap of 2 keeps the first two whole lines; bytes are not the binding constraint here.
    assert clip_lines_to_budget(_FIVE, max_lines=2, max_bytes=1000, keep="head") == "aaaa\nbbbb"


def test_clip_keep_tail_keeps_the_last_lines():
    # Same inputs, tail-keeping: the last two whole lines survive instead.
    assert clip_lines_to_budget(_FIVE, max_lines=2, max_bytes=1000, keep="tail") == "dddd\neeee"


def test_clip_keep_head_drops_trailing_lines_to_hit_the_byte_budget():
    # Line cap admits all 3, but 14 bytes only fits two 4-byte lines + 1 newline (9 bytes).
    assert (
        clip_lines_to_budget(["aaaa", "bbbb", "cccc"], max_lines=10, max_bytes=9, keep="head")
        == "aaaa\nbbbb"
    )


def test_clip_keep_tail_drops_leading_lines_to_hit_the_byte_budget():
    # Same byte budget, tail-keeping: the two FRESHEST lines survive, the oldest is dropped.
    assert (
        clip_lines_to_budget(["aaaa", "bbbb", "cccc"], max_lines=10, max_bytes=9, keep="tail")
        == "bbbb\ncccc"
    )


def test_clip_keeps_at_least_one_whole_line_even_when_it_overflows():
    # A single line longer than the byte budget is never split away to nothing — both ends agree.
    long = "x" * 100
    assert clip_lines_to_budget([long, "y"], max_lines=10, max_bytes=5, keep="head") == long
    assert clip_lines_to_budget(["y", long], max_lines=10, max_bytes=5, keep="tail") == long
