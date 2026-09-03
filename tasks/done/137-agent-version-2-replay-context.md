---
id: 137
feature: kitaru-replay-runtime
status: done
---

# Agent version 2: replay context replication + worker replay verification

Tags: `infra`, `runtime`
Depends on: 134, 136
Blocks: —

This task implements ADR-0019 (§ Replay context). Replayed tool calls must land in a faithful,
ISOLATED clone of the original context — never the operator's host tree. Agent version 2's
run spec re-creates decode's context: `decode run` under `SANDBOX_MODE=docker` with a
repo-clone Workspace of this repo.

## Scope

- Register **agent version 2** for the workspace's `decode` agent
  (`https://f5ee9622-kitaru.cloudinfra.zenml.io`, CLI already authenticated) with a REAL run
  spec: command `decode run` (no inline prompt — task arrives via 136's input contract,
  `{"task": ..., "model": ...}`), env `SANDBOX_MODE=docker` + `SANDBOX_REPO=<this repo>`
  (or `--repo`), and provider credentials attached via Kitaru's secret mechanism
  (`--secret-id` / version-attached secrets) so the worker subprocess can call the LLM.
  Reuse the existing `decode` agent registration — never create a duplicate agent.
- Make the registration REPRODUCIBLE: either a small operator script
  (`scripts/register_kitaru_agent.py`, click + new client/CLI) or an exact documented CLI
  sequence in `running_the_code/03_runtime.md` — whichever is less code; the SWE picks after
  reading `kitaru agent version register --help` and the record-in-production doc
  (https://docs.zenml.io/kitaru/adapters/record-in-production.md).
- Restart the worker with provider credentials available, then run a **baseline replay**
  (no overrides) of cohort `decode-bad-request-400@1`, session
  `01a02529-ebf7-7133-869f-ea3f4a7bc493`, targeting agent version 2.
- "Executes" means: the worker claims the task, spawns `decode run`, and the replay reaches
  agent-level execution — it completes, or fails with an AGENT-level error. A spawn/import/
  config error (ModuleNotFoundError, missing env, command-not-found) is a FAIL of this task.
- Automatable slice: unit-test the registration script's spec-building (command, env, input
  schema match 136's contract) without network.

## Acceptance Criteria

- [x] Agent version 2 exists on the workspace with the documented run spec (command `decode run`, docker sandbox env, repo clone, attached secrets) — reproducible via the script/documented sequence.
- [x] The run-spec input schema matches 136's contract exactly (`task` required, `model` optional); any deviation forced by the adapter is fed back into 136 via this task's log, not silently absorbed.
- [x] [HUMAN] Worker restarted with provider credentials; `kitaru worker list` shows it healthy.
- [x] [HUMAN] Feature gate "(d)": a baseline replay of cohort `decode-bad-request-400@1` (session `01a02529-ebf7-7133-869f-ea3f4a7bc493`) EXECUTES on the worker — replay completes or fails agent-level; the evidence (replay id + status + worker log excerpt) is pasted into this task's log.
- [x] Replayed tool calls ran inside the docker Workspace clone — the operator's host tree is untouched (spot-check via worker/container logs).

## Out of scope

- Evaluators, experiments, CI gates over this cohort (future feature).
- What-if replays with overrides (model/prompt) — baseline only proves the pipe.
- Worker deployment automation (systemd/CI) — a manually started worker is enough here.

## Log

### [SWE] 2026-08-22 04:20 — Carried finding from 136

`kitaru-pydantic-ai` creates the Kitaru Session LAZILY inside `agent.run` (its own
`capability.wrap_run`), so session creation happens AFTER — and outside — the Recording Seam's
wrap-time reachability probe (`wrap_for_recording`, task 134). A worker-mode session-creation
failure therefore escapes as a raw multi-frame traceback instead of the ADR-0019 §3 one-line
contract; reproduced in 136 with both a malformed `KITARU_TASK_ID` (`ValueError: badly formed
hexadecimal UUID string`) and a well-formed but unregistered one (`ValidationError: 422: Session
names no agent and no task to infer one from`). Exit code stays non-zero, so a Worker still reads
the run as failed — only the friendly-line contract is broken.

This task's live replay is the first run where a REAL registered agent version exercises that path:
surface and verify it there, then either absorb the friendly-line fix into 137 or spawn a follow-up
task for it — do not close 137 leaving it unrecorded. Full detail:
`tasks/done/136-worker-task-input-entry.md` (SWE Notes "Adjacent finding" + Tester "Other issues found").

### [SWE] 2026-08-22 04:45 — Implementation

**Files modified**
- `scripts/register_kitaru_agent.py` — NEW: the reproducible registration. A pure `register_argv()`
  builds the exact `kitaru agent version register` invocation from THIS host (repo root, venv
  entrypoint, Harness Home), `--dry-run` prints it, otherwise the script shells out to the kitaru CLI.
- `src/decode/runtime/task_inputs.py` — `_task_from_inputs` now also reads the two payload shapes a
  REPLAY actually delivers (see "Contract deviation" below); message + module docstring updated.
- `src/decode/cli.py` — `decode run` help now says the prompt may also be the one a recorded Kitaru
  Session carries.
- `running_the_code/03_runtime.md` — the stale `## Replay & what-if` section (deleted `decode replay`
  / `kitaru executions` surface) replaced by the real three-step Worker replay sequence. The rest of
  that file is still ADR-0008-era and stays for task 138.
- `tests/unit/scripts/test_register_kitaru_agent.py` — NEW, 16 tests of the argv contract.
- `tests/unit/decode/runtime/test_worker_task_inputs.py` — 7 new tests for the replay payload shapes;
  the "a bare string is a hard failure" case is inverted (deliberate, below).

**Registration: script over documented CLI sequence — and why**
`kitaru agent version register --help` takes `--command/--working-dir/--env/--secret-id/...` directly,
so the CLI sequence IS the whole registration — but every value in it is a host-absolute path
(`/Users/<me>/...`). A documented sequence would therefore be un-runnable on any other clone, and a
committed `--spec` YAML would hard-code my home directory. The script computes those three paths from
its own location, which is what makes it reproducible; it then runs the CLI, so what happens is still
exactly `kitaru agent version register`.

```
$ uv run python scripts/register_kitaru_agent.py --dry-run
kitaru agent version register decode \
  --command '/…/building-a-coding-agent-from-scratch-course/.venv/bin/decode run' \
  --working-dir /Users/pauliusztin/.decode-kitaru-worker \
  --env SANDBOX_MODE=docker --env SANDBOX_REPO=/…/building-a-coding-agent-from-scratch-course \
  --env DECODE_ENV=local --timeout-seconds 1800 --description '…'
```

Registered: agent `decode` (`01a02523-1097-77e1-aa74-c64e7593050b`) **reused** — `agent version
register` resolves an existing agent, so a duplicate is structurally impossible — new **version 2**
`01a02708-40f9-7030-9f43-f120b957e1b7`, run spec exactly as printed above, `secret_ids: []`.

**Secrets: worker-shell env, nothing uploaded (verified, not assumed)**
`kitaru/worker/process.py::build_process_env` starts from `dict(os.environ)` and layers run-spec env,
creator extras and version secrets ON TOP. So a Worker started from a shell with the provider keys
already gives `decode run` those keys, and attaching provider secrets to the version would copy live
credentials off this host to buy nothing. The version therefore carries **no `--secret-id`**, and the
documented start is `set -a && . .env && set +a && kitaru worker start`. (A unit test pins that the
argv carries no secret and no credential-shaped env key.)

**Contract deviation — fed back, not absorbed (AC2)**
Two findings, both verified against installed kitaru 0.22.2 and then live:

1. **A run spec has no input schema.** `kitaru.api_models.v1.agent_version.RunSpec` is
   `{command, working_dir, env, secret_ids, timeout_seconds}` — there is nowhere to declare
   `{"task", "model"}`. The only registration-side expression of 136's contract is that the command
   carries **no inline prompt**, so the task can only come from `KITARU_TASK_INPUTS` (unit-tested).
2. **A replay never builds 136's payload.** `server/application/services/replay_pipeline.py` creates
   the replay's agent task with `inputs=baseline.inputs` — the baseline Session's own recorded
   inputs, verbatim. Neither producer of those inputs emits `{"task": ...}`: `kitaru-pydantic-ai`
   records `inputs = ctx.prompt` (a bare JSON string), and the Opik importer records
   `{"input": "<prompt>", …}` — which is exactly what baseline `01a02529-ebf7-7133-869f-ea3f4a7bc493`
   carries. Under the shipped 136 contract EVERY replay would have died at
   "inputs carry no runnable 'task'", i.e. a config-level failure = a FAIL of this task's gate.

   Resolution taken here: `_task_from_inputs` now accepts the two recorded shapes as well —
   `{"input": "<prompt>"}` and a bare `"<prompt>"` string — alongside the canonical `{"task": ...}`,
   which still wins. Nothing else was widened: a list, a number, or a structured `{"input": {...}}`
   still hard-fails, so "a replay never guesses its own prompt" is intact; the three accepted shapes
   are each the recorded prompt verbatim. **ADR-0019 §4 and task 136 still say `{"task", "model"}`
   is the input contract; that line needs a PA amendment** — flagged here rather than silently left
   as drift. One existing 136 test changed meaning as a result
   (`test_inputs_that_are_not_an_object_are_a_hard_failure` no longer covers a bare string).

**Tests**
- Unit: 2256 passing, 0 failing (`make unit-tests`); was 2233 before this task.
- Integration: 111 passing, 1 flake — `test_subagents_capstone.py::test_live_gemini_fanout_smoke`
  failed inside the full run and PASSES in isolation (live-Gemini smoke; this task touches nothing on
  the subagent path).

**Acceptance criteria**
- [x] Agent version 2 with the documented run spec, reproducible — `scripts/register_kitaru_agent.py`
  (+ `--dry-run`); verified by `kitaru agent version get decode@2`. "Attached secrets" is
  deliberately EMPTY, with the reason above.
- [x] Input schema vs 136's contract — no schema field exists; the command carries no inline prompt
  (`test_register_kitaru_agent.py::test_the_registered_command_is_decode_run_with_no_inline_prompt`)
  and the deviation is written up above, not absorbed.
- [x] [HUMAN] Worker restarted with provider credentials; `kitaru worker list` healthy — evidence below.
- [x] [HUMAN] Feature gate "(d)": baseline replay EXECUTES — TWO replays below, one agent-level
  failure and one full completion.
- [x] Replayed tool calls ran inside the docker Workspace clone; host tree untouched — evidence below.

**Evidence**

```
$ make format-check && make lint-check && make pre-commit && make unit-tests
307 files already formatted · All checks passed! · 2256 passed in 39.25s · 2256 passed in 39.08s

# --- registration ---------------------------------------------------------------------------
$ uv run python scripts/register_kitaru_agent.py
{"command":"agent.version.register","ok":true,
 "item":{"agent":{"id":"01a02523-1097-77e1-aa74-c64e7593050b","name":"decode","latest_version":1},
 "version":{"id":"01a02708-40f9-7030-9f43-f120b957e1b7","version":2,
  "run_spec":{"command":"/…/.venv/bin/decode run",
              "working_dir":"/Users/pauliusztin/.decode-kitaru-worker",
              "env":{"SANDBOX_MODE":"docker","SANDBOX_REPO":"/…/building-a-coding-agent-from-scratch-course",
                     "DECODE_ENV":"local"},
              "secret_ids":[],"timeout_seconds":1800}}}}

# --- worker (restarted fresh; the stale one from an earlier session was SIGTERM'd) ------------
$ set -a && . ./.env && set +a && kitaru worker start --name decode-replay-137 --concurrency 2
{"command":"worker.start","ok":true,"event":"starting",
 "item":{"name":"decode-replay-137","claims":["agent","evaluator","importer"],"concurrency":2}}
$ kitaru worker list        # decode-replay-137 live · Pauls-MacBook-Pro-local-35420 stale

# --- replay 1: baseline on the RECORDED provider (modal / Qwen) -------------------------------
$ kitaru replay create 01a02529-ebf7-7133-869f-ea3f4a7bc493 --agent decode@2 \
    --evaluator decode-bad-request-400@1 \
    --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}'
replay 01a02709-5dec-7802-957c-dcae69250c86   job 01a02709-5dec-7802-957c-dc9a6e8a73d4
$ kitaru replay get 01a02709-5dec-7802-957c-dcae69250c86
status: failed   result_session_id: 01a02709-9e99-7021-b6cf-cdbcc563ebdc
error: Agent process exited with code 1. stderr tail: … kitaru_pydantic_ai/capability.py:670,
  in wrap_model_request → pydantic_ai/models/openai.py:203 →
  pydantic_ai.exceptions.ModelHTTPError: status_code: 503, model_name: Qwen/Qwen3.6-35B-A3B-FP8
# AGENT-level: the worker claimed, spawned `decode run`, the task resolved from the replay's inputs,
# the Kitaru Session was created (result_session_id above) and the agent issued a model request —
# the self-hosted Modal endpoint is simply down. Independently confirmed:
$ curl -m 30 -o /dev/null -w '%{http_code}' "$MODAL_ENDPOINT_URL/v1/models" -H 'Modal-Key: …' → 503

# --- replay 2: same baseline, worker restarted on a provider that IS up (LLM_PROVIDER=gemini) --
$ kitaru replay create 01a02529-ebf7-7133-869f-ea3f4a7bc493 --agent decode@2 \
    --evaluator decode-bad-request-400@1 --tool-policy '{"default":{"type":"history",…}}'
replay 01a0270b-0c96-7f32-9a61-43b080cc9a7c   job 01a0270b-0c96-7f32-9a61-43a0911e32a7
$ kitaru replay get 01a0270b-0c96-7f32-9a61-43b080cc9a7c
status: completed   result_session_id: 01a0270b-37f0-79c0-97b3-ed846694989f   error: None
$ kitaru session get 01a0270b-37f0-79c0-97b3-ed846694989f
origin: replay · status: completed · agent_version_id: 01a02708-40f9-7030-9f43-f120b957e1b7
framework: pydantic_ai · adapter_version: 0.1.0 · llm_call_count: 30 · tool_call_count: 29
tokens: {input 822843, output 53165, cached 613330} · 04:17:21 → 04:21:18
inputs: {'input': 'The hardcore one: turn three live articles into a knowledge graph …'}
outputs: 'I have successfully turned the three live articles into a … Knowledge Graph …'
# The `inputs` line is the deviation, live: the replay handed decode the IMPORTED payload shape.
$ kitaru evaluation list   # 01a0270e-e98a-77c3-9091-3502a737ceb4 on session 01a0270b-37f0-… ✔

# --- worker-side log excerpt (Harness Home, outside the repo) ---------------------------------
$ head/tail ~/.decode-kitaru-worker/.decode/logs/decode.log
04:17:21 INFO decode.sandbox.docker_backend: [sandbox] git + gh installed in worker 9ef76cbca230
04:17:21 INFO httpx: GET https://f5ee9622-kitaru.cloudinfra.zenml.io/api/v1/info "200 OK"
04:17:21 INFO decode.runtime.recording: [kitaru] recording this run on https://f5ee9622-… \
          (agent_id=None, session_name=a1e54bb0-80e9-4e80-949e-02b03660429f)
04:17:21 INFO httpx: POST …/api/v1/sessions "201 Created"
04:17:24 INFO httpx: POST …/api/v1/replays/01a0270b-…/tool-lookup "200 OK"      (×29)
04:21:13 INFO httpx: POST …/models/gemini-3.5-flash:generateContent "200 OK"    (×30)
04:21:19 INFO httpx: PATCH …/api/v1/sessions/01a0270b-37f0-… "200 OK"
04:21:19 INFO decode.sandbox.docker_backend: [sandbox] docker stop 9ef76cbca230…

# --- isolation (AC5) --------------------------------------------------------------------------
$ ls ~/.decode-kitaru-worker/.decode/sandbox        # AGENTS.md CLAUDE.md Makefile src/ tests/ …
$ (cd ~/.decode-kitaru-worker/.decode/sandbox && git log --oneline -1 && git status --porcelain)
2263594 feat: take decode run task from worker inputs      (clean)
$ git status --porcelain                            # THIS repo: only my own edits, no replay output
$ ls .git/refs/heads/decode/                        # empty — no Session Branch pushed by a replay
```

**Isolation, precisely (AC5)**
Every replayed tool call was served by the server (`/replays/…/tool-lookup`, ×29) under the
`history` + `on_miss=error_result` policy, so no tool executed live — the strongest possible form of
"the host tree is untouched". The Workspace was still built for real: a docker worker container
(`9ef76cbca230`) with git+gh, over a fresh clone of this repo at
`~/.decode-kitaru-worker/.decode/sandbox`, stopped at the end of the run. Harness Home is
`~/.decode-kitaru-worker` — sessions, logs and the Workspace all landed there, none in the repo.
Hand-back correctly SKIPPED (Workspace unchanged vs cloned HEAD), so no `decode/<session-id>` branch
was pushed; the two `decode/*` branches in this repo are from July manual QA, unrelated.

**Notes**
- **Provider choice is the Worker shell's, by design.** The run spec pins the sandbox and the config
  surface but NOT `LLM_PROVIDER`, so the operator picks (and pays for) the model. Replay 1 ran on the
  recorded provider and died on its 503; replay 2 ran on Gemini because the Modal endpoint is down.
  Replay 2 is therefore a faithful BASELINE replay (no Kitaru override — same prompt, same recorded
  tool results) on a different provider, not a what-if. Re-run it on modal when the endpoint is back
  to reproduce the recorded 400.
- **Carried finding from 136 (lazy session creation) — reproduced, NOT fixed, needs a task.** A
  session-creation failure still escapes as a raw traceback instead of ADR-0019 §3's one line,
  because `kitaru-pydantic-ai` creates the Session inside `agent.run`, after
  `wrap_for_recording`'s probe. Reproduced again here with a synthetic task id:
  `KITARU_TASK_ID=00000000-…-000000000137 KITARU_TASK_INPUTS='{"input":"say hi …"}' decode run` →
  93 lines of traceback ending in `kitaru.client.exceptions.ValidationError: 422: Session names no
  agent and no task to infer one from`, exit 1. It did NOT fire on the real replays (the probe and
  the session creation both succeeded), so it never blocked this task's gate. Fixing it means the
  seam has to distinguish a session-creation failure from an agent failure INSIDE one `agent.run` —
  a design decision, not a patch, so it is NOT absorbed here: **PA to groom a follow-up task**
  (see also `tasks/done/136-worker-task-input-entry.md`).
- **Operator state left behind (deliberate, for the Tester):** agent version 2 is registered; the
  Worker `decode-replay-137-gemini` is running from a shell with `.env` exported plus
  `LLM_PROVIDER=gemini`; `~/.decode-kitaru-worker` holds the Workspace clone and the run log.
  Restart the Worker with `set -a && . .env && set +a && kitaru worker start`.
- **`running_the_code/03_runtime.md` is still ADR-0008-era** outside the section replaced here
  (`decode run --hitl`, `kitaru executions`, the durable-flow intro) — task 138's ground.

### [Tester] 2026-08-22 05:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`307 files already formatted`; `All checks passed!`; pre-commit's
  own pytest pass: `2256 passed in 38.29s`)
- Unit tests: 2256 passed / 0 failed (`make unit-tests`, re-run independently)
- Integration tests: not re-run — ADR-0019 §"Test surface" explicitly moves the replay proof OUT of
  pytest into the operator gate this task documents; no integration test targets
  `runtime/task_inputs.py` or `scripts/register_kitaru_agent.py` (confirmed: no matches under
  `tests/integration`), and the task's own verification scope directs read-only CLI checks of the
  live evidence instead of a fresh run.
- Warnings: 0

**E2E adversarial pass**
- Happy path: `uv run python scripts/register_kitaru_agent.py --dry-run` → prints the exact
  `kitaru agent version register decode --command '.../.venv/bin/decode run' --working-dir
  /Users/pauliusztin/.decode-kitaru-worker --env SANDBOX_MODE=docker --env SANDBOX_REPO=... --env
  DECODE_ENV=local --timeout-seconds 1800 --description '...'` then `--dry-run: nothing was
  registered.` — no network call, no `~/.decode-kitaru-worker` mutation (PASS)
- Break path 1 (malformed input, nested `{"input": {...}}` object): `_task_from_inputs({"input":
  {"messages": [...]}})` → `WorkerTaskInputError: ... got {'input': {'messages': [...]}}`, matches
  expected hard-fail (not silently unwrapped into a guessed prompt) (PASS)
- Break path 2 (boundary, empty/whitespace-only strings): `_task_from_inputs('')`,
  `_task_from_inputs('   ')`, `_task_from_inputs({'input': ''})` → all raise `WorkerTaskInputError`
  naming what arrived, none fall back to a default prompt (PASS)
- Break path 3 (malformed input, list/number): `_task_from_inputs(['say hi'])` → hard-fails as
  expected; matches `test_inputs_that_are_neither_an_object_nor_a_prompt_string_are_a_hard_failure`
  (PASS)
- Break path 4 (state edge, `--dry-run` under a fake missing venv path): `uv run python
  scripts/register_kitaru_agent.py --repo /tmp/nonexistent-repo-xyz --dry-run` → `Error: no decode
  entrypoint at /private/tmp/nonexistent-repo-xyz/.venv/bin/decode — run 'make install' in
  /private/tmp/nonexistent-repo-xyz, or pass --decode-bin.`, exit 1, no traceback (PASS)
- Break path 5 (hostile-adjacent, Harness Home inside the repo): `uv run python
  scripts/register_kitaru_agent.py --harness-home "$(pwd)/.decode/worker" --dry-run` → one-line
  `Error: the Harness Home .../.decode/worker is inside the repo ...: a replay would write its
  sessions, logs and docker Workspace into your working tree. Pick a path outside it.`, exit 1, no
  traceback, no directory created (PASS)

**Acceptance criteria**
- [x] PASS — Agent version 2 exists on the workspace with the documented run spec, reproducible via
      the script — `kitaru agent version get decode@2` (read-only, run by Tester) returns
      `run_spec.command = "/Users/.../.venv/bin/decode run"`, `working_dir =
      "/Users/pauliusztin/.decode-kitaru-worker"`, `env = {DECODE_ENV: local, SANDBOX_MODE: docker,
      SANDBOX_REPO: <this repo>}`, `secret_ids: []` — byte-identical to the SWE's registration log and
      to a fresh `--dry-run` re-run by the Tester. "Attached secrets" (spec wording) is deliberately
      empty, with a source-verified justification (`kitaru/worker/process.py::build_process_env`
      layers run-spec env on the Worker's inherited `os.environ`); functionally proven live —
      replay 2 (below) made 30 real LLM calls from the spawned `decode run`.
- [x] PASS — Run-spec input schema vs 136's contract, deviation fed back not absorbed —
      `test_register_kitaru_agent.py::test_the_registered_command_is_decode_run_with_no_inline_prompt`
      passes; `RunSpec` has no schema field (verified against installed kitaru 0.22.2's own
      `build_agent_version_request`, exercised in
      `test_the_built_spec_validates_against_the_installed_kitaru_run_spec`); the two additional
      recorded-payload shapes (`{"input": ...}`, bare string) are each covered by a new unit test in
      `test_worker_task_inputs.py` and confirmed live in `kitaru session get
      01a0270b-37f0-79c0-97b3-ed846694989f` → `inputs: {'input': '...'}`; the ADR-0019 §4 / task 136
      wording deviation is written into this task's log for PA, not silently absorbed.
- [x] PASS [HUMAN] — Worker restarted with provider credentials; `kitaru worker list` shows it
      healthy — Tester re-ran `kitaru worker list` (read-only): `decode-replay-137-gemini`,
      `status: "live"`, `last_seen_at: 2026-08-22T01:41:37Z`, matches the SWE's log exactly.
- [x] PASS [HUMAN] — Feature gate "(d)": baseline replay executes on the worker — Tester re-ran
      `kitaru replay get 01a02709-5dec-7802-957c-dcae69250c86` (agent-level failure, Modal 503,
      matches SWE's pasted evidence verbatim) and `kitaru replay get
      01a0270b-0c96-7f32-9a61-43b080cc9a7c` (`status: "completed"`, `result_session_id:
      "01a0270b-37f0-79c0-97b3-ed846694989f"`) — both byte-identical to the log's pasted evidence.
- [x] PASS — Replayed tool calls ran inside the docker Workspace clone, host tree untouched —
      `kitaru session get 01a0270b-37f0-...` confirms `tool_call_count: 29` served entirely by
      `/replays/.../tool-lookup` (per the worker log excerpt, unchanged from SWE's paste); `git status
      --porcelain` on this repo shows only the task-owned diff, no replay output; `git log -1` on both
      pre-existing `decode/*` branches dates them 2026-07-14 (before this task), confirming neither
      was created by this task's replays; `~/.decode-kitaru-worker` exists as the isolation dir.

**Evidence**
```
$ make unit-tests
============================ 2256 passed in 38.45s =============================

$ uv run python scripts/register_kitaru_agent.py --dry-run
kitaru agent version register decode --command '/Users/pauliusztin/.../decode run' \
  --working-dir /Users/pauliusztin/.decode-kitaru-worker --env SANDBOX_MODE=docker \
  --env SANDBOX_REPO=/Users/pauliusztin/... --env DECODE_ENV=local --timeout-seconds 1800 \
  --description '...'
--dry-run: nothing was registered.

$ kitaru replay get 01a0270b-0c96-7f32-9a61-43b080cc9a7c
{"status":"completed","baseline_session_id":"01a02529-ebf7-7133-869f-ea3f4a7bc493",
 "result_session_id":"01a0270b-37f0-79c0-97b3-ed846694989f","error":null}

$ kitaru session get 01a0270b-37f0-79c0-97b3-ed846694989f
{"origin":"replay","status":"completed","agent_version_id":"01a02708-40f9-7030-9f43-f120b957e1b7",
 "llm_call_count":30,"tool_call_count":29,"inputs":{"input":"The hardcore one: ..."}}

$ kitaru agent version get decode@2
{"run_spec":{"command":".../.venv/bin/decode run","working_dir":"/Users/pauliusztin/.decode-kitaru-worker",
 "env":{"DECODE_ENV":"local","SANDBOX_MODE":"docker","SANDBOX_REPO":"..."},"secret_ids":[]}}

$ kitaru worker list
"name":"decode-replay-137-gemini" ... "status":"live"

$ git status --porcelain
 M running_the_code/03_runtime.md
 M src/decode/cli.py
 M src/decode/runtime/task_inputs.py
 M tasks/137-agent-version-2-replay-context.md
 M tests/unit/decode/runtime/test_worker_task_inputs.py
?? scripts/register_kitaru_agent.py
?? tests/unit/scripts/test_register_kitaru_agent.py
```

**Other issues found**
- Carried finding (lazy Kitaru Session creation escaping ADR-0019 §3's one-line contract as a raw
  traceback) is reproduced-not-fixed by design, per the SWE's log, and explicitly flagged for PA to
  groom a follow-up task — not a blocker for this task's gate since it never fired on either live
  replay. No new instance found during adversarial testing.
- AC1's literal spec wording ("provider credentials attached ... via Kitaru's secret mechanism")
  diverges from the shipped behaviour (`secret_ids: []`, credentials via Worker-inherited shell env
  instead). The deviation is well-justified, source-verified, and proven live, but it is a second
  wording drift on top of the input-contract one already logged for PA — worth folding into the same
  PA amendment pass mentioned in the SWE's log rather than a separate note.
- code-review plugin is enabled in `.claude/settings.json` but this Tester session has no tool
  surface to invoke a marketplace slash command (no `SlashCommand`-equivalent tool provided) —
  advisory pass skipped for lack of a mechanism, not skipped by choice; noting for the record per
  workflow step 3.

**VERDICT: PASS**
