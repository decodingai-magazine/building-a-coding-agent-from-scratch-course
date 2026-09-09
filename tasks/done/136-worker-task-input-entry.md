---
id: 136
feature: kitaru-replay-runtime
status: done
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

- [x] `decode run` with no arg and no `KITARU_TASK_ID` exits non-zero with the one friendly line.
- [x] `KITARU_TASK_ID=x KITARU_TASK_INPUTS='{"task":"say hi"}' decode run` executes "say hi" (mocked model) with no CLI arg.
- [x] An explicit CLI TASK wins over env inputs.
- [x] `{"model": ...}` in task inputs threads into the Model Override exactly like `--model`.
- [x] Malformed `KITARU_TASK_INPUTS` under `KITARU_TASK_ID` exits non-zero naming the parse failure.
- [x] No kitaru import on any path where `KITARU_TASK_ID` is absent.

## Out of scope

- Registering the agent version that emits these inputs (137).
- Any `KITARU_REPLAY_ID` handling in decode (adapter-native).

## Log

### [SWE] 2026-08-22 03:51 — Implementation

**Files modified**
- `src/decode/runtime/task_inputs.py` — NEW: `resolve_task_and_model()` (CLI arg > Worker Task
  inputs > one friendly line) + `WorkerTaskInputError`; the `kitaru.task` import sits inside the
  Worker branch.
- `src/decode/cli.py` — `decode run`'s `TASK` argument is now `required=False`; resolution runs
  first in the command body (it finishes the argument parsing Click could not), then the unchanged
  guard chain; the resolved model feeds the context-window notice AND the runner. Help text
  documents the `KITARU_TASK_INPUTS` source.
- `src/decode/runtime/recording.py` — `_one_line` → `one_line` (promoted, same body): the Worker
  Task failures owe the operator the same one-line-no-traceback contract, so the helper is shared
  rather than duplicated.
- `tests/conftest.py` — hermeticity: `KITARU_TASK_INPUTS` added to the `_no_kitaru_recording` scrub
  (it is now a behavioural input — an operator's Worker shell must not hand the suite a task).
- `tests/unit/decode/runtime/test_worker_task_inputs.py` — NEW: 23 tests of the resolver
  (precedence, contract, hard-fail modes, import invariant).
- `tests/unit/decode/runtime/test_run_command.py` — 6 CLI-level tests, one per acceptance criterion.

**Verification of the upstream contract (done FIRST, against installed kitaru 0.22.2)**
`kitaru/task/__init__.py:35-66` — `get_task_inputs()` returns `None` when `KITARU_TASK_ID` is unset,
`json.loads(KITARU_TASK_INPUTS)` when that var is set, else a synchronous
`GET {KITARU_API_URL}/api/v1/tasks/{id}/spec` (raising `RuntimeError: KITARU_API_URL is not set`
without one). Because the env-set path is pure `json.loads`, the resolver tests drive the REAL
accessor with only the env faked — no fake module, no network — and cover both raising paths.

**Tests**
- Unit: 2233 passing, 0 failing (`make unit-tests`); runtime subtree 102 passing (was 73).
- Integration: N/A — no infra changes (no sandbox/LSP/network surface touched).

**Acceptance criteria**
- [x] No arg + no `KITARU_TASK_ID` → non-zero + one friendly line —
  `test_run_command.py::test_run_without_a_task_or_a_worker_context_is_a_friendly_line`,
  `test_worker_task_inputs.py::test_no_task_and_no_worker_context_raises_one_friendly_line`.
- [x] `KITARU_TASK_ID` + `KITARU_TASK_INPUTS` runs the task with no CLI arg —
  `test_run_command.py::test_run_takes_its_task_from_the_worker_task_inputs` (runner boundary faked,
  per the criterion's "mocked model"), `test_worker_task_inputs.py::test_a_worker_task_supplies_the_task_from_its_inputs`.
- [x] Explicit CLI TASK wins — `test_run_cli_task_wins_over_the_worker_task_inputs`,
  `test_an_explicit_task_wins_over_worker_task_inputs` (which also tripwires `kitaru.task` to prove
  it is never consulted).
- [x] `model` in inputs = the Model Override — `test_run_worker_task_inputs_model_threads_into_the_model_override`,
  `test_a_worker_tasks_model_becomes_the_model_override`; `--model` still wins
  (`test_an_explicit_model_flag_wins_over_the_inputs_model`).
- [x] Malformed inputs → non-zero naming the parse failure —
  `test_run_malformed_worker_task_inputs_exits_non_zero_naming_the_parse_failure` (asserts
  `JSONDecodeError` reaches stderr), plus off-contract shapes (non-object, missing/blank/non-string
  `task`, non-string `model`) and an unreadable channel.
- [x] No kitaru import where `KITARU_TASK_ID` is absent —
  `test_resolving_without_a_worker_context_imports_no_kitaru_module` (fresh interpreter, scrubbed
  env, asserts `sys.modules` stays kitaru-free through BOTH the CLI-arg and no-task paths).

**Evidence**
```
$ make unit-tests
============================ 2233 passed in 39.02s =============================

$ make format-check && make lint-check && make pre-commit
305 files already formatted · All checks passed! · 2233 passed

# e2e 1 — no task anywhere (real CLI)
$ env -u KITARU_TASK_ID -u KITARU_TASK_INPUTS uv run decode run
Decode: decode run needs a TASK to run: pass it as an argument (decode run "<task>"), or launch it
as a Kitaru Worker Task, which supplies it in KITARU_TASK_INPUTS.
exit=1

# e2e 2 — malformed inputs under a Worker Task
$ KITARU_TASK_ID=abc123 KITARU_TASK_INPUTS='{not json' uv run decode run
Decode: this Kitaru Worker Task's inputs could not be read: JSONDecodeError: Expecting property name
enclosed in double quotes: line 1 column 2 (char 1). decode run reads its task from
KITARU_TASK_INPUTS (or the task spec) when no TASK argument is given, and a Worker replay must never
guess its own prompt.
exit=1

# e2e 3 — off-contract inputs (no 'task' key)
$ KITARU_TASK_ID=abc123 KITARU_TASK_INPUTS='{"model":"gemini-2.5-pro"}' uv run decode run
Decode: this Kitaru Worker Task's inputs carry no runnable 'task': expected {"task": "<prompt>",
"model": "<id>"|null} in KITARU_TASK_INPUTS, got {'model': 'gemini-2.5-pro'}
exit=1

# e2e 4 — unreadable channel (no KITARU_TASK_INPUTS, no KITARU_API_URL) — kitaru's own cause kept
$ KITARU_TASK_ID=abc123 uv run decode run
Decode: this Kitaru Worker Task's inputs could not be read: RuntimeError: KITARU_API_URL is not set.
… exit=1

# e2e 5 — the CLI-arg path still runs a REAL model end to end (no regression)
$ LLM_PROVIDER=gemini uv run decode run "say hi in exactly three words"
Hello there, friend.
exit=0   (stderr empty — stdout is still exactly the answer)

# e2e 6 — a real process taking task + model from the env, logged at the runner boundary
$ LOG_LEVEL=DEBUG KITARU_TASK_ID=…0001 \
  KITARU_TASK_INPUTS='{"task":"say hi in exactly three words","model":"gemini-2.5-flash"}' \
  uv run decode run
DEBUG decode.runtime.task_inputs: task taken from the Kitaru Worker Task inputs (model='gemini-2.5-flash', cli model=None)
DEBUG decode.cli: decode run starting (task='say hi in exactly three words', model='gemini-2.5-flash', repo=None, local=False)

# e2e 7 — precedence in a real process
$ … KITARU_TASK_INPUTS='{"task":"from the worker","model":"gemini-2.5-flash"}' \
  uv run decode run --model gemini-2.5-pro "from the cli"
DEBUG decode.cli: decode run starting (task='from the cli', model='gemini-2.5-pro', …)
```

**Notes**
- **Ordering choice.** Task resolution runs BEFORE the guard chain: with `TASK` optional, "which
  task?" is the argument parsing Click cannot finish, and Click resolves a required argument before
  the callback too. No existing guard test changed.
- **Precedence choice (not in the spec).** `--model` wins over the inputs' `model`, mirroring the
  task precedence, so an operator debugging a Worker replay by hand can override either. A blank /
  `null` `model` normalises to `None` (= the configured model), never a model id of `""`.
- **Strictness beyond the letter of the spec.** A non-string `model` is a hard failure too — the
  alternative is silently running a model the inputs did not name, which is the same class of lie
  the spec forbids for `task`.
- **`one_line` promotion.** The only edit to `recording.py`; call sites updated, no behaviour change
  (pinned by the existing task-134 tests, all still green).
- **NOT RUN — the full worker-replay e2e** (a real `decode run` driven by a live Kitaru Worker with
  a real model). It needs a registered Agent Version + a real Worker Task, which is task 137. With a
  synthetic `KITARU_TASK_ID` the decode side is proven up to `agent.run` (e2e 6-7 above, and the
  traceback frames go `cli.py:508 → headless.py:183 → kitaru_pydantic_ai/agent.py:85`), then the
  workspace rejects the unknown task: `ValidationError: 422: Session names no agent and no task to
  infer one from`.
- **Adjacent finding, NOT fixed here (belongs to 134/137, not this task):** that 422 escapes as a
  raw traceback rather than the Seam's ONE friendly line + non-zero exit. The Seam's probe (task 134)
  checks reachability at wrap time, but the adapter creates the Kitaru Session lazily inside
  `agent.run`, so a session-creation failure bypasses `RecordingUnavailableError` entirely. Exit code
  is still non-zero, so a Kitaru Worker reads the run as failed; only the friendly-line contract is
  broken. Suggest a follow-up task.
- `running_the_code/03_runtime.md` still documents the deleted durable-flow surfaces and says nothing
  about the optional TASK — left alone deliberately; task 138 owns docs alignment.

### [Tester] 2026-08-22 04:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` → 305 files already formatted; `make lint-check`
  → All checks passed; `make pre-commit` → 2233 passed)
- Unit tests: 2233 passed / 0 failed (`make unit-tests`)
- Integration tests: N/A — no infra surface touched (matches SWE's claim; confirmed no
  `tests/integration/` files changed in `git diff --stat`)
- Warnings: 0 (`pytest.ini` `filterwarnings=["error"]`; re-ran the two new test files with `-W error`
  explicitly: `52 passed`)

**E2E adversarial pass**
- Happy path: `env -u KITARU_TASK_ID -u KITARU_TASK_INPUTS LLM_PROVIDER=gemini uv run decode run
  "reply with exactly the single word: HAPPYPATH"` → `HAPPYPATH` on stdout, exit 0 (PASS). Note: the
  same command under the repo's default `LLM_PROVIDER=modal` hit a transient `503` from the Modal-served
  Qwen model — a provider outage unrelated to this task's code; re-run against Gemini succeeded
  immediately, confirming the CLI-arg path is unregressed.
- Break path 1 (no arg, no worker context): `env -u KITARU_TASK_ID -u KITARU_TASK_INPUTS uv run decode
  run` → `Decode: decode run needs a TASK to run: ... (or a Kitaru Worker Task ...)`, exit 1 (PASS)
- Break path 2 (malformed JSON): `KITARU_TASK_ID=abc123 KITARU_TASK_INPUTS='{not json' uv run decode
  run` → one line naming `JSONDecodeError`, exit 1, no traceback (PASS)
- Break path 3 (off-contract shapes): `{"task": 42}`, `{"task": ""}`, and non-object payloads
  (`"just a string"`, `["say hi"]`, `42`) all under `KITARU_TASK_ID=abc123` → each produced the same
  one-line "carry no runnable 'task'" message with the received payload echoed, exit 1, no traceback
  (PASS — 5/5 shapes tried)
- Break path 4 (precedence): `resolve_task_and_model('from the cli', 'gemini-2.5-pro')` under
  `KITARU_TASK_INPUTS='{"task":"from the worker","model":"gemini-2.5-flash"}'` → `('from the cli',
  'gemini-2.5-pro')` (PASS); confirmed in a real process too — `KITARU_TASK_ID=<uuid>
  KITARU_TASK_INPUTS='{"task":"from the worker (should NOT run)","model":"gemini-2.5-pro"}' uv run
  decode run --model gemini-2.5-flash "reply with exactly the single word: PRECEDENCE"` resolved to
  task="reply with...PRECEDENCE", model="gemini-2.5-flash" before failing later at Kitaru session
  creation (see Other issues found) — resolution itself is correct.
- Break path 5 (fresh-interpreter no-kitaru-import, CLI-arg path): independent subprocess with a
  scrubbed `KITARU_*` env calling `resolve_task_and_model("do the thing", None)` → `('do the thing',
  None)`, `sys.modules` has no `kitaru`/`kitaru.*` entries (PASS)
- Break path 6 (hermeticity): `KITARU_TASK_ID=<uuid> KITARU_TASK_INPUTS='{"task":"leaked from operator
  shell"}' KITARU_AGENT_ID=leaked-agent make unit-tests` → `2233 passed` (PASS — the operator-shell env
  does not leak into the suite)
- Extra: Unicode task (`"héllo wörld 日本語の..."`) round-trips exactly; a 500,000-char task string
  resolves without truncation or crash; a malformed-inputs failure message with an embedded `\n` in the
  payload stays single-line (`" ".join(repr(value).split())` before truncation); `decode run ""` (blank
  CLI arg) correctly falls through to the worker inputs; `--model ""` (blank flag) correctly falls
  through to the inputs' model rather than pinning `model=""`. All PASS.

**Acceptance criteria**
- [x] PASS — `decode run` with no arg and no `KITARU_TASK_ID` exits non-zero with the one friendly line
      — `test_run_command.py::test_run_without_a_task_or_a_worker_context_is_a_friendly_line`,
      `test_worker_task_inputs.py::test_no_task_and_no_worker_context_raises_one_friendly_line`; manual:
      `env -u KITARU_TASK_ID -u KITARU_TASK_INPUTS uv run decode run` → exit 1, one line, no traceback.
- [x] PASS — `KITARU_TASK_ID=x KITARU_TASK_INPUTS='{"task":"say hi"}' decode run` executes "say hi" with
      no CLI arg — `test_run_command.py::test_run_takes_its_task_from_the_worker_task_inputs` (runner
      boundary mocked, per the criterion's wording); manual (env-faked, model mocked at the resolver):
      `resolve_task_and_model(None, None)` under those env vars returns `('say hi', None)`.
- [x] PASS — An explicit CLI TASK wins over env inputs —
      `test_run_command.py::test_run_cli_task_wins_over_the_worker_task_inputs`,
      `test_worker_task_inputs.py::test_an_explicit_task_wins_over_worker_task_inputs` (tripwires
      `kitaru.task` to prove it's never touched); manual precedence check above (break path 4).
- [x] PASS — `{"model": ...}` in task inputs threads into the Model Override exactly like `--model` —
      `test_run_command.py::test_run_worker_task_inputs_model_threads_into_the_model_override`,
      `test_worker_task_inputs.py::test_a_worker_tasks_model_becomes_the_model_override`; `--model`
      still wins (`test_an_explicit_model_flag_wins_over_the_inputs_model`), verified manually above.
- [x] PASS — Malformed `KITARU_TASK_INPUTS` under `KITARU_TASK_ID` exits non-zero naming the parse
      failure — `test_run_command.py::test_run_malformed_worker_task_inputs_exits_non_zero_naming_the_parse_failure`;
      manual: `KITARU_TASK_ID=abc123 KITARU_TASK_INPUTS='{not json' uv run decode run` → names
      `JSONDecodeError`, exit 1, `Traceback` absent from stderr.
- [x] PASS — No kitaru import on any path where `KITARU_TASK_ID` is absent —
      `test_worker_task_inputs.py::test_resolving_without_a_worker_context_imports_no_kitaru_module`
      (fresh interpreter); independently reproduced (break path 5 above) with a differently-scrubbed
      env and a different assertion script, same result.

**Evidence**
```
$ make unit-tests
============================ 2233 passed in 39.07s =============================

$ make format-check && make lint-check && make pre-commit
305 files already formatted · All checks passed! · 2233 passed

$ KITARU_TASK_ID=00000000-0000-4000-8000-000000000099 \
  KITARU_TASK_INPUTS='{"task":"from the worker (should NOT run)","model":"gemini-2.5-pro"}' \
  uv run decode run --model gemini-2.5-flash "reply with exactly the single word: PRECEDENCE"
... (traceback ends in) kitaru.client.exceptions.ValidationError: 422: Session names no agent and
no task to infer one from
# confirms the resolver picked ('reply with...PRECEDENCE', 'gemini-2.5-flash') correctly — the CLI
# task/model won — before failing later, deep in kitaru_pydantic_ai's own session creation. Same
# failure the SWE's log documents ("Adjacent finding"), reproduced independently here with a
# well-formed synthetic KITARU_TASK_ID (a malformed one, e.g. "abc123", fails even earlier inside
# kitaru_pydantic_ai/capability.py at `uuid.UUID(task_value)` — same class of raw traceback, same
# root cause: session creation is lazy and outside `wrap_for_recording`'s probe).

$ KITARU_TASK_ID=00000000-0000-4000-8000-000000000099 \
  KITARU_TASK_INPUTS='{"task":"leaked from operator shell"}' KITARU_AGENT_ID=leaked-agent \
  make unit-tests
============================ 2233 passed in 38.99s =============================
```

**Other issues found**
- Confirmed (not a new bug, and not this task's to fix): the "adjacent finding" the SWE recorded in
  this file's Notes — Kitaru session creation happens lazily inside `agent.run` (inside
  `kitaru_pydantic_ai`'s own `capability.wrap_run`), bypassing `wrap_for_recording`'s reachability
  probe entirely, so a session-creation-time failure escapes as a raw multi-frame traceback instead of
  the ADR-0019 §3 one-friendly-line contract. Reproduced independently with both a malformed
  (`KITARU_TASK_ID=abc123` → `ValueError: badly formed hexadecimal UUID string`) and a well-formed but
  unregistered (`ValidationError: 422: Session names no agent and no task to infer one from`) task id.
  Exit code is still non-zero either way, so a Kitaru Worker still reads the run as failed — only the
  friendly-line contract is broken, and task 136's own scope (resolving task/model text) is unaffected;
  the crash happens strictly after resolution succeeds.
  **Ruling: acceptable to defer to 137.** Task 137's own acceptance criteria already treat this class
  of failure as passing ("the replay reaches agent-level execution — it completes, or fails with an
  AGENT-level error"), so this finding does not block 137's baseline-replay gate. However, as of this
  review it is recorded ONLY in this task's own Log (Notes, above) — `tasks/137-agent-version-2-replay-context.md`
  has no mention of it in Scope, Acceptance Criteria, or Log. Recommend the SWE (or whoever picks up
  137) add a one-line pointer back to this finding in 137's Log before closing that task, or spin it
  into its own follow-up task, so it isn't lost between task files.
- Minor (not blocking): `is_worker_task()` / `recording_is_configured()` treat any non-empty
  `KITARU_TASK_ID` — including a whitespace-only string like `" "` — as a worker context, which then
  makes `resolve_task_and_model` hard-fail on a "no runnable task" rather than degrade gracefully. This
  matches the existing `KITARU_TASK_ID=""` test's spirit (falsy-string check) but a whitespace-only id
  is a step further from anything a real Kitaru Worker would export; not worth a fix, noting only.

**VERDICT: PASS**
