"""Unit tests for the shared YAML-frontmatter splitter (``decode.frontmatter``).

Both catalog loaders (:mod:`decode.agents.loader`, :mod:`decode.skills.loader`) read the same
on-disk shape — a ``---``-fenced YAML frontmatter block atop a Markdown body — so the split lives
once here. These tests pin the happy split, the body preservation, the ``FENCE`` constant, and the
two structural errors (no opening fence, an unclosed block) whose messages both loaders' tests rely
on (``"frontmatter"`` / ``"closed"``).
"""

import pytest

from decode import frontmatter


def test_fence_is_a_triple_dash():
    assert frontmatter.FENCE == "---"


def test_split_returns_frontmatter_and_body():
    # ``splitlines`` drops line terminators, so the body comes back without its trailing newline
    # (the loaders ``.strip()`` it anyway — what matters is the split point, not the terminator).
    text = "---\nname: demo\ndescription: a demo\n---\nThe body.\n"

    yaml_block, body = frontmatter.split_frontmatter(text)

    assert yaml_block == "name: demo\ndescription: a demo"
    assert body == "The body."


def test_split_preserves_a_multi_line_body_with_internal_newlines():
    # Internal blank lines / newlines in the body are kept (only the trailing terminator is lost).
    text = "---\nname: demo\n---\nline one\n\nline three\n"

    _yaml, body = frontmatter.split_frontmatter(text)

    assert body == "line one\n\nline three"


def test_split_tolerates_whitespace_around_the_fence_lines():
    # A fence line is recognised after stripping surrounding whitespace.
    text = "  ---  \nname: demo\n  ---  \nBody.\n"

    yaml_block, body = frontmatter.split_frontmatter(text)

    assert yaml_block == "name: demo"
    assert body == "Body."


def test_split_empty_frontmatter_block_returns_an_empty_yaml_string():
    text = "---\n---\nBody.\n"

    yaml_block, body = frontmatter.split_frontmatter(text)

    assert yaml_block == ""
    assert body == "Body."


def test_split_rejects_a_missing_frontmatter_block():
    with pytest.raises(ValueError, match="frontmatter"):
        frontmatter.split_frontmatter("no frontmatter here, just a body\n")


def test_split_rejects_empty_text():
    with pytest.raises(ValueError, match="frontmatter"):
        frontmatter.split_frontmatter("")


def test_split_rejects_an_unclosed_fence():
    with pytest.raises(ValueError, match="closed"):
        frontmatter.split_frontmatter("---\nname: demo\nbody but no closing fence\n")
