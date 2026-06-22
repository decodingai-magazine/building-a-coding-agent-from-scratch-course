"""Unit tests for :class:`decode.harness.decisions.DecisionChannel`.

The channel is the one-input-surface seam (ADR-0002 §3-4): a mid-turn requester awaits a
line via :meth:`request`; the input loop fulfils it via :meth:`resolve`. These tests pin the
pending-decision lifecycle (idle -> pending -> resolved / cancelled) the permission resolver
and the app loop both depend on, without any prompt_toolkit involvement.
"""

import asyncio

import pytest

from decode.harness.decisions import DecisionChannel


async def test_starts_idle_with_nothing_pending():
    channel = DecisionChannel()

    assert channel.pending is False


async def test_request_blocks_until_a_line_is_resolved():
    channel = DecisionChannel()

    task = asyncio.ensure_future(channel.request())
    await asyncio.sleep(0)  # let request() register the pending future

    # While the requester awaits, the channel reports a pending decision.
    assert channel.pending is True
    assert not task.done()

    # The input loop fulfils it with the next submitted line.
    resolved = channel.resolve("y")
    assert resolved is True
    assert await task == "y"
    # Back to idle after the answer lands.
    assert channel.pending is False


async def test_resolve_without_a_pending_request_returns_false():
    channel = DecisionChannel()

    # Nothing is awaiting, so the input loop must handle the line itself (normal mode).
    assert channel.resolve("hello") is False


async def test_cancel_unblocks_the_requester_with_cancelled_error():
    channel = DecisionChannel()

    task = asyncio.ensure_future(channel.request())
    await asyncio.sleep(0)
    assert channel.pending is True

    channel.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert channel.pending is False


async def test_cancel_is_a_noop_when_nothing_is_pending():
    channel = DecisionChannel()

    channel.cancel()  # must not raise

    assert channel.pending is False


async def test_two_concurrent_requests_are_rejected():
    """Single-flight invariant: only one mid-turn decision can be pending at a time."""
    channel = DecisionChannel()

    task = asyncio.ensure_future(channel.request())
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError):
        await channel.request()

    channel.resolve("y")
    await task
