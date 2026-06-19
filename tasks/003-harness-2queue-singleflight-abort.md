---
id: 003-harness-2queue-singleflight-abort
feature: m1-vanilla-agent
status: pending
---

# Harness: two-queue, single-flight, cooperative abort

## Scope
The interaction loop per [ADR-0002 §4–5](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md): steering + follow-up queues, a phase machine with a single-flight lock spanning a whole turn, and a cooperative abort flag. Wired to a stub multi-step turn handler so semantics are testable before the real agent.

## Acceptance criteria
- [ ] `harness/queue.py` holds steering + follow-up `asyncio.Queue`s; `harness/runner.py` owns the phase machine (`idle|dispatching|running`) + single-flight lock (phase set before first `await`).
- [ ] `entities/events.py` defines the event union the loop emits / TUI renders.
- [ ] Steering drains between the stub's model-steps; follow-up drains only at the would-stop boundary; `Esc` stops at the next boundary keeping completed history; a second concurrent submit does not start a parallel turn.
- [ ] Async tests cover all four behaviours.

## Out of scope
- Real model calls (task 004); mid-stream/mid-tool interruption (never — boundary only).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan + ADR-0003.
