---
id: 139
feature: kitaru-replay-runtime
status: pending
---

# Worker-mode lazy Kitaru Session creation must honor the one-friendly-line contract

Tags: `runtime`, `cli`, `bug`
Depends on: None (follow-up to 134/136/137 — all done)
Blocks: —

Post-acceptance follow-up to the kitaru-replay-runtime feature (PA-groomed at acceptance
review; NOT part of that feature's accepted gate). This task implements ADR-0019 §3's
existing contract for a failure window the Recording Seam's probe cannot cover — no new ADR.

**The gap (reproduced twice, deliberately unfixed in 136/137):** `kitaru-pydantic-ai`
creates the Kitaru Session LAZILY inside `agent.run` (its own `capability.wrap_run`) — AFTER
`wrap_for_recording`'s wrap-time reachability probe. In worker mode (`KITARU_TASK_ID`
present), a session-creation failure therefore escapes as a 90+-line raw traceback on stderr
instead of the ONE `Decode:`-prefixed friendly line every other recording failure gets.
Reproduced with a malformed task id (`ValueError: badly formed hexadecimal UUID string`) and
a well-formed but unregistered one (`ValidationError: 422: Session names no agent and no
task to infer one from`). Exit code is already non-zero either way, so a Kitaru Worker still
reads the run as failed — only the friendly-line contract is broken. It never fired on the
live replays in 137. Full history: `tasks/done/136-worker-task-input-entry.md` (SWE Notes
"Adjacent finding") and `tasks/done/137-agent-version-2-replay-context.md` (carried finding).

## Scope

- Surface a worker-mode Kitaru session-creation failure that escapes `agent.run` as the same
  one-line contract the seam's probe failures already get: ONE `Decode:`-prefixed stderr line
  naming the cause, full traceback in `.decode/logs/decode.log` only, exit non-zero.
- **Suggested approach (SWE decides specifics):** extend `src/decode/cli.py::run()`'s
  existing `except RecordingUnavailableError` guard to also catch the kitaru client's
  exception family when `is_worker_task()` is true — same
  `logger.warning(..., exc_info=True)` + `click.echo(f"Decode: ...", err=True)` +
  `Exit(1)` idiom the 134 fix round established. The catch must be worker-gated: on a
  user-launched recorded run, an exception escaping `agent.run` is an agent failure and must
  keep propagating untouched.
- **Never mask a genuine agent failure.** A model/provider error inside a worker replay
  (e.g. the `ModelHTTPError` 503 in 137's replay 1) must still surface as an agent-level
  failure — do NOT rewrite it into a recording line. Only kitaru-client/session-creation
  exception types qualify.
- **Import invariant holds.** `kitaru` exception types may only be imported inside the
  worker branch (or matched lazily, e.g. by exception module name) — the no-kitaru-import
  invariant for user-launched paths must stay green.
- **Escalation clause:** if distinguishing a session-creation failure from an agent failure
  inside one `agent.run` turns out to require adapter changes or a recording-architecture
  change, STOP and escalate to the PA (architectural fork) — do not fork the adapter.
- Unit tests at the CLI boundary (faked adapter raising the kitaru exception shapes):
  worker-mode one-liner, user-launched propagation, agent-failure passthrough.

## Acceptance Criteria

- [ ] `KITARU_TASK_ID=<well-formed synthetic uuid> KITARU_TASK_INPUTS='{"task":"say hi"}' decode run` against the real workspace exits non-zero with stderr = exactly ONE `Decode:`-prefixed line naming the session-creation failure (no `Traceback` on stderr); the full traceback is in `.decode/logs/decode.log`.
- [ ] A malformed `KITARU_TASK_ID` (uuid parse failure inside the adapter) produces the same one-line contract.
- [ ] A faked agent-level failure (e.g. a model HTTP 503) under `KITARU_TASK_ID` is NOT rewritten into a recording line — it propagates as an agent failure exactly as today.
- [ ] User-launched recorded runs are byte-identical to today on every path (degrade line, happy path, agent failure) — pinned by existing 134/135 tests staying green untouched.
- [ ] No kitaru import on any path where recording is unconfigured (existing fresh-interpreter invariant tests stay green).
- [ ] Full unit suite green; `make ci` green.

## User Stories

### Story: Operator debugs a Worker locally with a synthetic task id and gets one line
1. Operator exports `KITARU_TASK_ID=00000000-0000-4000-8000-000000000139` and `KITARU_TASK_INPUTS='{"task":"say hi"}'` in a shell authenticated to the workspace
2. Operator runs `decode run`
3. The workspace rejects session creation (422 — no such task)
4. Operator sees exit 1 and exactly one stderr line: `Decode: [kitaru] recording is unavailable for this Kitaru Worker Task: ...` naming the 422 cause
5. `tail .decode/logs/decode.log` shows the full traceback for post-hoc debugging

### Story: A real replay fails at the model and the Worker log stays honest
1. A Kitaru Worker claims a replay and spawns `decode run`; the Kitaru Session is created fine
2. The provider returns 503 on the first model request
3. `kitaru replay get <id>` shows `status: failed` with the agent-level `ModelHTTPError` — no recording-failure wording anywhere (matches 137's replay 1 evidence shape)

---

Refs: `tasks/done/136-worker-task-input-entry.md`, `tasks/done/137-agent-version-2-replay-context.md`, ADR-0019 §3

## Log

### [PA] 2026-08-22 — Grooming

**Summary**
Close the lazy-session-creation window in the Recording Seam's one-friendly-line contract for
worker-spawned runs. Design decision taken at grooming: catch the kitaru client exception
family at the CLI boundary, worker-gated — no adapter fork, no new ADR (this implements
ADR-0019 §3 as amended, it does not change it).

**Key decisions**
- Bias-to-least: reuse the existing `except RecordingUnavailableError` guard idiom in
  `cli.py::run()` rather than building failure-classification machinery inside the seam.
- Worker-gated catch only; user-launched paths untouched by construction.
- Explicit escalation clause if the distinction can't be made at the CLI boundary.

**Dependencies**
- None — 134/136/137 are done; this hardens their shipped surface.

**User stories**
- 2 stories covering: the misconfigured-worker debug path (the reproduced gap) and the
  agent-failure passthrough (the must-not-regress case).

Ready for implementation.
