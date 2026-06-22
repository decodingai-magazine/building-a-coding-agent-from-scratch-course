"""Unit tests for the steering + follow-up interaction queues (ADR-0002 §4)."""

from decode.harness.queue import InteractionQueues


async def test_drain_steering_returns_all_queued_oldest_first():
    queues = InteractionQueues()
    await queues.steering.put("first")
    await queues.steering.put("second")

    assert queues.drain_steering() == ["first", "second"]


async def test_drain_steering_is_empty_when_nothing_queued():
    queues = InteractionQueues()

    assert queues.drain_steering() == []


async def test_drain_follow_up_only_drains_the_follow_up_queue():
    queues = InteractionQueues()
    await queues.steering.put("steer")
    await queues.follow_up.put("later")

    # Draining follow-up must not consume steering.
    assert queues.drain_follow_up() == ["later"]
    assert queues.drain_steering() == ["steer"]


async def test_drain_empties_the_queue():
    queues = InteractionQueues()
    await queues.steering.put("once")

    assert queues.drain_steering() == ["once"]
    assert queues.drain_steering() == []


async def test_clear_discards_both_queues():
    queues = InteractionQueues()
    await queues.steering.put("a")
    await queues.follow_up.put("b")

    queues.clear()

    assert queues.drain_steering() == []
    assert queues.drain_follow_up() == []
