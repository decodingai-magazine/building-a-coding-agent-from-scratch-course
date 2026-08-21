---
id: 136
feature: kitaru-replay-runtime
status: pending
---

# Worker task entry: `decode run` takes its task from KITARU_TASK_INPUTS when the CLI arg is absent

Tags: `runtime`, `cli`, `enhancement`
Depends on: 134
Blocks: 137

This task implements ADR-0019 (§ Replay context). When a Kitaru Worker replays a session it
spawns the agent version's command with `KITARU_TASK_ID` (+ `KITARU_TASK_INPUTS` or a
fetchable task spec) in the env — the command line carries no prompt. `decode run` must
accept the task from that channel. Verified against installed kitaru 0.22.2:
`kitaru.task.get_task_inputs()` returns `json.loads(KITARU_TASK_INPUTS)` when set, else
fetches `/api/v1/tasks/{id}/spec` synchronously; returns `None` outside task mode.

## Scope

- `decode run`'s `TASK` argument becomes optional. Resolution: explicit CLI arg wins; else,
  if `KITARU_TASK_ID` is present, read the task from `kitaru.task.get_task_inputs()`; else
  exit with ONE friendly line ("decode run needs a TASK argument (or a Kitaru task context)").
- **Input contract** (shared with 137's registration): task inputs are
  `{"task": "<prompt>", "model": "<id>" | null}` — `task` required, `model` optional and
  mapped to the existing Model Override. Malformed/missing `task` in a worker context →
  hard fail non-zero (a worker replay must never guess).
- The `kitaru.task` import stays inside the worker branch (`KITARU_TASK_ID` present), so the
  no-import invariant holds for every user-launched path.
- Replay ids: the adapter handles `KITARU_TASK_ID`/`KITARU_REPLAY_ID` natively — decode reads
  ONLY the task inputs; it never touches replay plumbing.
- Unit tests (env-faked, no network): CLI-arg precedence, env-supplied task via
  `KITARU_TASK_INPUTS`, friendly no-task error, malformed-inputs hard fail, no-import
  invariant outside task mode.

## Acceptance Criteria

- [ ] `decode run` with no arg and no `KITARU_TASK_ID` exits non-zero with the one friendly line.
- [ ] `KITARU_TASK_ID=x KITARU_TASK_INPUTS='{"task":"say hi"}' decode run` executes "say hi" (mocked model) with no CLI arg.
- [ ] An explicit CLI TASK wins over env inputs.
- [ ] `{"model": ...}` in task inputs threads into the Model Override exactly like `--model`.
- [ ] Malformed `KITARU_TASK_INPUTS` under `KITARU_TASK_ID` exits non-zero naming the parse failure.
- [ ] No kitaru import on any path where `KITARU_TASK_ID` is absent.

## Out of scope

- Registering the agent version that emits these inputs (137).
- Any `KITARU_REPLAY_ID` handling in decode (adapter-native).

## Log
