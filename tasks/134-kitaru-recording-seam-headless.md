---
id: 134
feature: kitaru-replay-runtime
status: pending
---

# Recording Seam: presence-based KitaruAgent wrap for headless `decode run`

Tags: `runtime`, `enhancement`
Depends on: 133
Blocks: 135, 136, 137

This task implements ADR-0019 (§ Recording Seam). One seam decides, for any built agent,
whether it runs wrapped in `kitaru_pydantic_ai.KitaruAgent` (recorded) or bare.

## Scope

- New settings field `kitaru_agent_id` (env `KITARU_AGENT_ID`, default empty) + `.env.example`
  entry. Presence-based opt-in: recording is configured iff `kitaru_agent_id` is set AND the
  adapter's own connection env (`KITARU_API_URL`; `KITARU_API_KEY` per the client's
  conventions) is present — decode passes the agent id through and lets the adapter client
  resolve its env itself; decode adds NO url/key settings of its own.
- New module (suggested `src/decode/runtime/recording.py`): one function that takes the built
  `Agent` (+ optional `session_name`) and returns either `KitaruAgent(agent,
  agent_id=settings.kitaru_agent_id, session_name=...)` or the bare agent. Constructor facts
  (verified from the adapter docs): `KitaruAgent(agent, agent_id=None, agent_version_id=None,
  session_name=None, batch_size=20)`; one Kitaru session per `run()` call; async `run` +
  `iter` supported; under a worker task the agent id is inferred.
- **Import invariant (tightened):** `kitaru_pydantic_ai` / `kitaru` are imported ONLY inside
  the seam, ONLY when recording is configured. At `DECODE_ENV=local` with no kitaru config,
  decode imports no kitaru module — extend the existing no-import test.
- **Graceful degrade (user-launched):** the adapter fast-fails at session creation when the
  server is unreachable. The seam catches that failure and falls back to the UNWRAPPED agent
  with exactly ONE stderr/log warning line (naming the server, not dumping a traceback); the
  run itself proceeds and succeeds. Design note for the SWE: "catch at wrap seam" — whether
  that's an eager reachability probe at wrap time or catching the first run()'s
  session-creation error and re-running unwrapped is an implementation choice; the observable
  contract is: ONE warning, run completes, exit code unaffected.
- **Hard fail (worker-spawned):** if `KITARU_TASK_ID` is present in the env, degrade is
  FORBIDDEN — a recording/session failure propagates and the process exits non-zero. A silent
  unrecorded replay would be a lying experiment.
- Wire the seam into the plain headless runner from 131 (`decode run`): wrap when configured;
  `session_name` may carry the run's session id.
- Unit tests fake the adapter/server boundary (no network): wrap-when-configured,
  bare-when-not, degrade-once-with-one-line, hard-fail-under-task-id, no-import invariant.

## Acceptance Criteria

- [ ] With `KITARU_AGENT_ID` unset, `decode run` builds and runs the bare agent and imports no kitaru module (invariant test).
- [ ] With recording configured and a reachable (faked) server, the runner executes through `KitaruAgent` with `agent_id=settings.kitaru_agent_id`.
- [ ] With recording configured and an unreachable server, a user-launched `decode run` prints exactly ONE warning line, completes on the bare agent, and exits 0.
- [ ] With `KITARU_TASK_ID` set and the server unreachable, the process exits non-zero with a clear error naming the recording failure.
- [ ] `.env.example` documents `KITARU_AGENT_ID` (+ pointers to `KITARU_API_URL`/`KITARU_API_KEY` as adapter-owned env); settings drift test green.
- [ ] [HUMAN] Live proof (feature gate "(b)"): with the managed workspace configured, `decode run "<small task>"` records a session visible via `kitaru session list --agent decode --origin recorded`.

## Out of scope

- REPL wiring (135); `KITARU_TASK_INPUTS` handling (136); agent-version registration (137).
- Any Opik/logfire change — the adapter composes with OTel per its docs; tracing stays as-is.

## Log
