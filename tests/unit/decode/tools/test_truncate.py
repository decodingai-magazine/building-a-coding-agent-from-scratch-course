"""Unit tests for the shared output-truncation helper (``decode.tools.truncate``).

ADR-0002 §7,10: tool output is capped at **2000 lines OR 50 KB, whichever comes first**,
snapping to a line boundary; on overflow the *full* content spills to a temp file whose path
rides back in the result so the model (and the user) can still reach everything. The helper is
deliberately tool-agnostic: ``read`` (task 006) and ``bash`` (task 008) both reuse it.

These tests pin the dual-cap behaviour and the overflow spill directly, with no model and no
real tool — the contract is fully decidable from input alone.
"""

from pathlib import Path

from decode.tools.truncate import Truncated, truncate


def test_short_content_is_returned_verbatim_and_not_truncated():
    content = "line one\nline two\nline three\n"

    result = truncate(content, max_lines=2000, max_bytes=50_000)

    assert isinstance(result, Truncated)
    assert result.text == content
    assert result.truncated is False
    assert result.full_path is None


def test_empty_content_is_not_truncated():
    result = truncate("", max_lines=2000, max_bytes=50_000)

    assert result.text == ""
    assert result.truncated is False
    assert result.full_path is None


def test_line_cap_truncates_at_the_line_boundary():
    content = "".join(f"line {i}\n" for i in range(10))

    result = truncate(content, max_lines=3, max_bytes=50_000)

    assert result.truncated is True
    # Only the first three lines survive, snapped to a line boundary (no partial line).
    assert result.text == "line 0\nline 1\nline 2\n"
    assert result.full_path is not None


def test_byte_cap_truncates_snapping_to_a_line_boundary():
    # Five 10-byte lines ("aaaaaaaaa\n"); a 25-byte cap lands mid-line-3, so it must snap
    # back to the end of line 2 (20 bytes) rather than cut a line in half.
    line = "a" * 9 + "\n"
    content = line * 5

    result = truncate(content, max_lines=2000, max_bytes=25)

    assert result.truncated is True
    assert result.text == line * 2  # exactly two whole lines, no partial line
    assert len(result.text.encode("utf-8")) <= 25
    assert result.full_path is not None


def test_whichever_cap_hits_first_wins_line_before_bytes():
    # Lines are tiny so the byte cap is generous, but the line cap (2) is hit first.
    content = "".join(f"{i}\n" for i in range(100))

    result = truncate(content, max_lines=2, max_bytes=50_000)

    assert result.truncated is True
    assert result.text == "0\n1\n"


def test_overflow_spills_full_content_to_a_temp_file():
    content = "".join(f"line {i}\n" for i in range(10))

    result = truncate(content, max_lines=3, max_bytes=50_000)

    assert result.full_path is not None
    spilled = result.full_path.read_text(encoding="utf-8")
    # The spill holds the *entire* original content, not just the truncated head.
    assert spilled == content


def test_content_without_trailing_newline_still_snaps_to_a_line_boundary():
    content = "alpha\nbravo\ncharlie"  # no trailing newline on the last line

    result = truncate(content, max_lines=2, max_bytes=50_000)

    assert result.truncated is True
    assert result.text == "alpha\nbravo\n"


def test_byte_cap_keeps_at_least_the_first_line_when_it_alone_overflows():
    # A single line that already blows the byte cap: we still emit that one line rather than
    # returning empty text (the model needs *something* readable; the full spill has the rest).
    content = "x" * 100 + "\n" + "y" * 100 + "\n"

    result = truncate(content, max_lines=2000, max_bytes=10)

    assert result.truncated is True
    assert result.text == "x" * 100 + "\n"
    assert result.full_path is not None


def test_spill_path_is_a_real_readable_file(tmp_path: Path):
    content = "".join(f"line {i}\n" for i in range(5000))

    result = truncate(content, max_lines=2000, max_bytes=50_000)

    assert result.full_path is not None
    assert result.full_path.exists()
    assert result.full_path.read_text(encoding="utf-8") == content
