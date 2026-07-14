"""Hidden test module for 016-implement-from-spec, injected only at grade time.

Named with a leading underscore so it never clobbers a file the agent may have created, and run by
verify.sh via import-and-call (no pytest binary in the slim sandbox image). Each ``test_*`` asserts
one clause of the docstring contract for :func:`intervals.merge_intervals`.
"""

from __future__ import annotations

from intervals import merge_intervals


def test_basic_overlap() -> None:
    assert merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]


def test_touching_intervals_merge() -> None:
    assert merge_intervals([[1, 4], [4, 5]]) == [[1, 5]]


def test_unsorted_input_is_sorted() -> None:
    assert merge_intervals([[5, 6], [1, 3], [2, 4]]) == [[1, 4], [5, 6]]


def test_fully_nested_interval() -> None:
    assert merge_intervals([[1, 10], [2, 3], [4, 5]]) == [[1, 10]]


def test_disjoint_intervals_unchanged() -> None:
    assert merge_intervals([[1, 2], [5, 6]]) == [[1, 2], [5, 6]]


def test_empty_input() -> None:
    assert merge_intervals([]) == []


def test_input_not_mutated() -> None:
    data = [[3, 4], [1, 2]]
    snapshot = [list(pair) for pair in data]
    merge_intervals(data)
    assert data == snapshot


def test_returns_lists_not_tuples() -> None:
    result = merge_intervals([[1, 2]])
    assert result == [[1, 2]]
    assert all(isinstance(pair, list) for pair in result)
