"""Unit tests for the harness runner: phase machine, single-flight, drain, abort.

These exercise the load-bearing semantics from ADR-0002 §4-5 against a *controllable*
stub turn handler (no real agent). The handler is an async generator that yields
:class:`Boundary` markers; the runner drives it, draining the queues and checking the
abort flag at those boundaries. The stub records what steering/follow-up the runner fed
back at each leg, so the tests can assert exactly *when* each queue was drained.
"""

import asyncio

from decode.entities import events
from decode.harness.runner import Boundary, Phase, Runner, TurnContext
from decode.tui.app import InputIntent


class RecordingHandler:
    """A stub multi-step turn whose legs and boundaries the test controls.

    ``steps`` is the number of model-request legs. Each leg emits one assistant delta,
    then a fake tool leg (a started + result event), recording any steering the runner
    injected before the leg. After the last leg it hits the would-stop boundary, recording
    any follow-up the runner injected; a follow-up adds one more leg.
    """

    def __init__(self, steps: int = 2) -> None:
        self._initial_steps = steps
        self.steering_seen: list[list[str]] = []
        self.followups_seen: list[list[str]] = []
        self.legs_run = 0
        # An external gate the test can hold closed to pin the handler mid-turn.
        self.leg_gate: asyncio.Event | None = None

    async def __call__(self, ctx: TurnContext):
        remaining = self._initial_steps
        while remaining > 0:
            # Model-request boundary: runner drains steering and feeds it back here.
            injected = yield Boundary.MODEL_REQUEST
            self.steering_seen.append(list(injected))

            if self.leg_gate is not None:
                await self.leg_gate.wait()

            self.legs_run += 1
            ctx.emit(events.AssistantTextDelta(text=f"leg-{self.legs_run}"))
            # Fake tool leg so a turn is genuinely multi-step.
            ctx.emit(events.ToolCallStarted(tool_call_id=f"t{self.legs_run}", name="noop", args=""))
            ctx.emit(events.ToolResult(tool_call_id=f"t{self.legs_run}", name="noop", output="ok"))
            remaining -= 1

            if remaining == 0:
                # Would-stop boundary: runner drains follow-up and feeds it back here.
                followups = yield Boundary.WOULD_STOP
                self.followups_seen.append(list(followups))
                # A follow-up continues the turn as one more leg.
                remaining += len(followups)


def _collect(sink: list[events.Event]):
    def _on_event(event: events.Event) -> None:
        sink.append(event)

    return _on_event


async def test_phase_starts_idle():
    runner = Runner(RecordingHandler(steps=1), on_event=_collect([]))

    assert runner.phase is Phase.IDLE


async def test_submit_on_idle_runs_a_turn_to_completion():
    emitted: list[events.Event] = []
    handler = RecordingHandler(steps=2)
    runner = Runner(handler, on_event=_collect(emitted))

    await runner.submit("hello", InputIntent.STEER)
    await runner.wait_idle()

    assert handler.legs_run == 2
    assert runner.phase is Phase.IDLE
    kinds = [e.kind for e in emitted]
    assert kinds[0] == "turn_started"
    assert kinds[-1] == "turn_finished"
    assert "assistant_text_delta" in kinds


async def test_phase_is_set_before_submit_yields_and_before_the_turn_runs():
    """ADR-0002 §4: the single-flight phase is set synchronously, before any await.

    The idle path of ``submit`` has no ``await`` before it flips the phase and schedules the
    turn task, so by the time ``await submit(...)`` returns the runner is already busy --
    *before* the freshly-scheduled turn task has had a chance to run (it cannot, because we
    have not yielded to the loop yet). A racing second submit therefore sees "busy" with no
    window to start a parallel turn.
    """
    handler = RecordingHandler(steps=1)
    handler.leg_gate = asyncio.Event()  # keep the turn pinned so it cannot finish
    runner = Runner(handler, on_event=_collect([]))

    await runner.submit("hi", InputIntent.STEER)

    # Busy already, and the turn task has not executed a single leg yet.
    assert runner.phase is not Phase.IDLE
    assert runner.active_turns == 1
    assert handler.legs_run == 0

    try:
        # A concurrent submit in this same window enqueues instead of starting a turn.
        await runner.submit("racer", InputIntent.STEER)
        assert runner.active_turns == 1
    finally:
        handler.leg_gate.set()
        await runner.wait_idle()
    assert runner.phase is Phase.IDLE


async def test_steering_drains_before_each_model_request_leg():
    """ADR-0002 §4: steering is drained before each model-request leg."""
    handler = RecordingHandler(steps=2)
    handler.leg_gate = asyncio.Event()
    runner = Runner(handler, on_event=_collect([]))

    await runner.submit("start", InputIntent.STEER)
    # Queue steering while the turn is pinned before its first leg.
    await runner.submit("steer-A", InputIntent.STEER)
    await runner.submit("steer-B", InputIntent.STEER)

    handler.leg_gate.set()
    await runner.wait_idle()

    # Leg 1 saw both steering messages drained at its boundary; leg 2 saw none queued.
    assert handler.steering_seen[0] == ["steer-A", "steer-B"]
    assert handler.steering_seen[1] == []


async def test_followup_drains_only_at_would_stop_boundary():
    """ADR-0002 §4: follow-up is drained only at the would-stop boundary."""
    handler = RecordingHandler(steps=1)
    runner = Runner(handler, on_event=_collect([]))

    await runner.submit("start", InputIntent.STEER)
    await runner.wait_idle()

    # First turn had no follow-up queued at would-stop.
    assert handler.followups_seen == [[]]

    # A second turn whose follow-up is queued before would-stop continues for one more leg.
    handler2 = RecordingHandler(steps=1)
    handler2.leg_gate = asyncio.Event()
    runner2 = Runner(handler2, on_event=_collect([]))
    await runner2.submit("start", InputIntent.STEER)
    await runner2.submit("more please", InputIntent.FOLLOW_UP)
    handler2.leg_gate.set()
    await runner2.wait_idle()

    assert handler2.followups_seen[0] == ["more please"]
    assert handler2.legs_run == 2  # the follow-up added a leg


async def test_followup_is_not_drained_at_a_model_request_boundary():
    """A follow-up queued before the first leg must NOT steer the current leg."""
    handler = RecordingHandler(steps=2)
    handler.leg_gate = asyncio.Event()
    runner = Runner(handler, on_event=_collect([]))

    await runner.submit("start", InputIntent.STEER)
    await runner.submit("followup", InputIntent.FOLLOW_UP)
    handler.leg_gate.set()
    await runner.wait_idle()

    # No model-request boundary saw the follow-up as steering.
    assert all("followup" not in seen for seen in handler.steering_seen)


async def test_second_concurrent_submit_does_not_start_a_parallel_turn():
    """ADR-0002 §4: the single-flight lock spans the whole turn."""
    handler = RecordingHandler(steps=1)
    handler.leg_gate = asyncio.Event()
    runner = Runner(handler, on_event=_collect([]))

    await runner.submit("first", InputIntent.STEER)
    # A second submit while busy must enqueue (route by intent), never spawn a turn.
    await runner.submit("second", InputIntent.STEER)

    assert runner.active_turns == 1  # exactly one turn in flight
    assert runner.phase is not Phase.IDLE

    handler.leg_gate.set()
    await runner.wait_idle()
    assert runner.active_turns == 0


async def test_abort_stops_at_next_boundary_keeping_completed_history():
    """ADR-0002 §5: Esc stops the turn at the next boundary, keeps completed history."""
    emitted: list[events.Event] = []
    handler = RecordingHandler(steps=3)
    handler.leg_gate = asyncio.Event()
    runner = Runner(handler, on_event=_collect(emitted))

    await runner.submit("go", InputIntent.STEER)
    runner.abort()  # set the cooperative-abort flag while pinned before leg 1
    handler.leg_gate.set()
    await runner.wait_idle()

    # Cooperative: stops at the very next boundary, so no leg's work was emitted.
    assert handler.legs_run == 0
    # Completed history (the events emitted so far) is kept; the turn ends marked aborted.
    assert emitted[0].kind == "turn_started"
    finished = [e for e in emitted if e.kind == "turn_finished"]
    assert finished and finished[-1].aborted is True
    assert runner.phase is Phase.IDLE


async def test_abort_clears_the_queues_and_returns_to_idle():
    """ADR-0002 §5: on abort the queues are cleared and the runner returns to idle."""
    handler = RecordingHandler(steps=2)
    handler.leg_gate = asyncio.Event()
    runner = Runner(handler, on_event=_collect([]))

    await runner.submit("go", InputIntent.STEER)
    await runner.submit("queued-steer", InputIntent.STEER)
    await runner.submit("queued-followup", InputIntent.FOLLOW_UP)
    runner.abort()
    handler.leg_gate.set()
    await runner.wait_idle()

    assert runner.phase is Phase.IDLE
    assert runner.queues.drain_steering() == []
    assert runner.queues.drain_follow_up() == []


async def test_abort_flag_resets_for_the_next_turn():
    """A turn aborted by Esc must not poison the following turn."""
    handler = RecordingHandler(steps=1)
    runner = Runner(handler, on_event=_collect([]))

    await runner.submit("first", InputIntent.STEER)
    runner.abort()
    await runner.wait_idle()
    assert handler.legs_run == 0

    handler2 = RecordingHandler(steps=1)
    runner2 = Runner(handler2, on_event=_collect([]))
    await runner2.submit("second", InputIntent.STEER)
    await runner2.wait_idle()
    assert handler2.legs_run == 1  # ran normally, abort flag was reset


async def test_handler_error_surfaces_as_agent_error_and_returns_to_idle():
    """A crashing turn becomes an AgentError event; the REPL stays alive (idle)."""
    emitted: list[events.Event] = []

    async def boom(ctx: TurnContext):
        raise RuntimeError("kaboom")
        yield Boundary.MODEL_REQUEST  # pragma: no cover - generator marker

    runner = Runner(boom, on_event=_collect(emitted))
    await runner.submit("go", InputIntent.STEER)
    await runner.wait_idle()

    errors = [e for e in emitted if e.kind == "agent_error"]
    assert errors and "kaboom" in errors[-1].message
    assert runner.phase is Phase.IDLE
