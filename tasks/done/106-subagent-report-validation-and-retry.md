---
id: 106
feature: subagent-fanout
status: done
---

# Subagent Report validation: bad-report detection + exactly one child retry

Depends on: 103 (per-child runner + sections), 105 (the report contract the nudge points back to).
Implements ADR-0017 §7.

## Scope

Validate each child's report on the way OUT; retry a bad child exactly ONCE with a nudge; fold an
explicit failure note if the retry is also bad. Never infinite retry — a broken child must not eat
the run's budget.

**`src/decode/tools/agent.py`**

- **Bad-report predicate** (deterministic, framework-verified): a child report is BAD iff
  - (i) its output text is empty/whitespace-only, OR
  - (ii) the child made ZERO tool calls — no `ToolCallPart` in any `ModelResponse` of
    `result.all_messages()` (verified: `AgentRunResult.all_messages()`, pydantic-ai 1.95
    `run.py:461`) — it answered from model memory instead of reading the code.
  - The defensive `DeferredToolRequests` output also classifies as BAD (it enters this machinery
    instead of short-circuiting to a note, as it did after 103).
- **One retry**: re-spawn that child once — same prompt + an appended nudge (a module constant;
  it must say what was wrong, e.g. "Your previous report was unusable: it was empty or cited no
  code you actually read. Use your tools to read the code, then report the finding with file:line
  evidence."). Fresh child deps, fresh semaphore acquisition, same `UsageLimits` — exactly like
  any spawn.
- **Second bad → failure note**: fold an explicit note for that child (e.g. "The subagent
  returned no usable report.") into its section. Exactly 2 attempts max, ever.
- A bad child + retry never delays or corrupts sibling sections (each child's
  attempt/retry/give-up cycle is private to its own gather slot); section order stays prompt
  order. The retry's report is budget-truncated exactly like a first-attempt report.
- Log each retry and each give-up at `logger.warning` (names/index only — never report bodies at
  warning level).

**Tests** (`tests/unit/decode/tools/test_agent.py`, scripted `FunctionModel` children)

- Empty-first / good-second child → exactly 2 spawns for that prompt, the good report folds.
- Empty twice → exactly 2 spawns, its section carries the failure note verbatim.
- Non-empty but ZERO-tool-call child (pure text answer) → detected as bad and retried, even
  though text is present.
- A child that calls a real read-only tool then reports → NOT bad, never retried (spawn count 1).
- The retry prompt = original prompt + the nudge (assert both present on the second spawn).
- Sibling isolation: in a 3-wide Fan-out with one twice-bad child, the other two sections carry
  their intact reports.
- The retry's output respects the per-child byte budget.

## Acceptance Criteria

- [x] An empty/whitespace-only child report triggers exactly one retry with the nudge appended; a good retry report folds into that child's section.
- [x] A non-empty report from a child that made zero tool calls (checked via `result.all_messages()`) is classified BAD and retried — pinned by a test whose child answers text-only.
- [x] A second bad attempt folds the explicit failure note; total spawns for that prompt == 2, never 3 (spy-counted).
- [x] A good child is never retried (spawn count == 1).
- [x] Sibling reports are unaffected by another child's retry/failure (order + content pinned).
- [x] A `DeferredToolRequests` child output routes through the same bad-report machinery.
- [x] `make ci` green.

## Out of scope

- Model-flake retry/backoff (transport-level) — feature non-goal, ADR-0017.
- Validating file:line presence in a syntactically fine report (persona-quality lever, 105).
- Any change to the input guards (104).

## Log

### [SWE] 2026-07-14 10:20 — Implementation

**Files modified**
- `src/decode/tools/agent.py` — the ADR-0017 §7 output contract: `_usable_report()` (the bad-report
  predicate: deferred output / empty text / zero tool calls via `result.all_messages()`),
  `_RETRY_NUDGE` + `_NO_USABLE_REPORT_NOTE` + `_CHILD_FAILED_NOTE` module constants, and
  `_spawn_child` split into a validate-and-retry-once shell over a single-attempt `_run_attempt`.
- `tests/unit/decode/tools/test_agent.py` — the §7 block (12 new tests: retry-once, nudge content,
  give-up note, zero-tool-call detection, good-child-never-retried, deferred routing, sibling
  isolation, retry byte budget, guard-not-re-run, no report body at WARNING) + a `_ScriptedAgent`
  stub (per-attempt scripts, spawn counting) + `_report()` now carries an `all_messages()` transcript.
- `tests/integration/test_subagents_capstone.py` — new §7 slice
  (`test_a_hallucinating_child_is_retried_once_then_noted_while_its_sibling_folds`) through the real
  Runner/handler; three existing capstone children now actually `glob` before reporting (they were
  text-only, which the new predicate correctly calls BAD).

**Tests**
- Unit: 1575 passing, 0 failing (`make unit-tests`); `tests/unit/decode/tools/test_agent.py` = 106 passing.
- Integration: `make ci` → 1688 passed, 2 skipped (both live-key-gated smokes: GEMINI/OPIK unset).

**Acceptance criteria**
- [x] Empty report → exactly one nudged retry, good retry folds — `test_an_empty_report_is_retried_once_with_the_nudge_and_the_good_retry_folds`, `test_the_retry_prompt_is_the_original_prompt_plus_the_nudge`.
- [x] Zero-tool-call (text-present) child is BAD — `test_a_zero_tool_call_child_is_bad_even_though_it_returned_text` (stub) + `test_a_real_text_only_child_is_retried_then_gives_up_with_the_note` (real `AgentRunResult.all_messages()`).
- [x] Second bad → failure note, spawns == 2 never 3 — `test_a_twice_bad_child_folds_the_failure_note_and_never_spawns_a_third_time` (the `_ScriptedAgent` asserts on a 3rd attempt).
- [x] A good child is never retried — `test_a_child_that_called_a_tool_and_reported_is_never_retried`, `test_a_real_child_that_reads_code_then_reports_is_never_retried`.
- [x] Sibling isolation + order — `test_a_twice_bad_child_leaves_its_siblings_intact_and_in_order`, capstone `test_a_hallucinating_child_is_retried_once_then_noted_while_its_sibling_folds`.
- [x] `DeferredToolRequests` routes through the machinery — `test_a_deferred_tool_requests_output_routes_through_the_same_retry_machinery` (retry-then-good AND bad-twice).
- [x] `make ci` green.

**Evidence**
```
$ make ci
================= 1688 passed, 2 skipped in 389.63s (0:06:29) ==================

$ uv run python scratchpad/e2e_106.py     # real build_agent + real agent tool, network boundary only faked
WARNING decode.tools.agent: subagent 2 returned an unusable report; retrying once (last try)
WARNING decode.tools.agent: subagent 2 returned an unusable report twice; giving up
## Subagent 1 — "How does the truncate helper cap tool output? …"

truncate() caps at 2000 lines / 50KB — truncate.py:1

## Subagent 2 — "How does the permission gate decide allow/ask/deny? …"

The subagent returned no usable report.
---
total child spawns (attempts): 3
  attempt 1: first try      — How does the truncate helper cap tool output? …   (honest child: 1 spawn)
  attempt 2: first try      — How does the permission gate decide …            (hallucinating child)
  attempt 3: RETRY (nudged) — How does the permission gate decide …            (its ONE retry)
```

**Notes**
- **104 interaction (the substance guard).** The nudged prompt never re-enters `_check_substance`:
  the guard runs ONCE inside `agent()`, over the MODEL's `prompts` list, strictly pre-fan-out; the
  retry prompt is built inside `_spawn_child` (`prompt + _RETRY_NUDGE`) and goes straight to
  `agent.run()`. So a harness-authored prompt can never be nagged back at the model (which never
  wrote it). Pinned by `test_a_retry_never_re_runs_the_substance_guard_over_the_nudged_prompt`
  (spies `_check_substance`: called once, with the model's list) — plus belt-and-braces, the nudged
  prompt would pass the floor anyway, being strictly longer than an already-accepted prompt.
- **An exception is NOT a bad report.** A child that raises still folds `_CHILD_FAILED_NOTE` with no
  retry — transport/model-flake retry is an explicit ADR-0017 non-goal. Only the three BAD shapes
  (deferred / empty / zero-tool-call) buy a retry.
- **Existing tests changed on purpose.** Several unit + capstone children were text-only, i.e.
  reports backed by zero tool calls — exactly what §7 now classifies BAD. They now `glob` before
  reporting (the honest shape). The unit helper branches on the tool CALL, not its return, because
  `glob` over an empty `tmp_path` raises `ModelRetry`.
- **A real model cannot return an empty final text part** — pydantic-ai's own output retry fires
  first (`Exceeded maximum output retries`). The empty-report branch is therefore kept as a
  defensive predicate arm, pinned by stub-scripted tests; the real-run tests use the zero-tool-call
  arm, which is the one that actually fires in production.
- Logging: retry + give-up are `logger.warning` with the 1-based subagent INDEX only; a bad report's
  body never reaches a WARNING line (pinned by `test_a_bad_report_body_never_reaches_a_warning_log_line`).
- No new Settings field, no `.env.example` entry (the nudge is a module constant).

### [Tester] 2026-07-14 01:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 181 files unchanged; `ruff check` all
  clean; `make pre-commit` → 1575 passed, 0 failed)
- Unit tests: 1575 passed / 0 failed (`tests/unit/decode/tools/test_agent.py` = 106 passed, matches
  SWE claim)
- Integration tests: first `make integration-tests` run showed 1 failure
  (`test_docker_executor.py::test_timeout_kills_the_command_but_the_container_and_fs_survive` —
  "No such container", a Docker daemon/network flake), 112 passed, 2 skipped. Isolated re-run of
  that single test passed (7.53s). Full `uv run pytest tests/integration` re-run clean: **113
  passed, 2 skipped**, 0 failures. Same category as the adjudicated `test_sandbox_teardown.py`
  container-reap flake (Docker infra flake, unrelated to `src/decode/tools/agent.py` — the diff
  touches nothing under `sandbox/`); not counted against this task.
- `uv lock --check`: clean.
- Warnings: 0 (pytest `filterwarnings=["error"]`, no warning surfaced in any run).
- code-review plugin: enabled in `.claude/settings.json`, but its `/code-review` command is
  GitHub-PR-shaped (`gh pr view`/`gh pr diff`, Task-launched subagents) and this project runs
  `TRACKER_MODE: file` with the work still uncommitted on `feat/subagent-fanout` — no PR exists to
  hand it. Not invokable in this session; the manual checklist below stands in for it.

**E2E adversarial pass**
- Happy path: real `build_agent()` + `AgentTurnHandler` + a scripted `FunctionModel` driving parent
  AND children (no network) → parent emits ONE `agent(prompts=[...])` call, 2 healthy children each
  `glob` then report, aggregate folds with both sections in prompt order, no retry, no WARNING.
  PASS.
- Break path 1 (predicate correctness, independent of the SWE's own test helpers): called
  `agent_module._usable_report()` directly against 4 hand-built `AgentRunResult` stand-ins — (i)
  whitespace-only text with a tool call → `None`, (ii) non-empty text with ZERO tool calls → `None`,
  (iii) non-empty text WITH a tool call → the text, (iv) `DeferredToolRequests` output → `None`.
  All 4 matched the ADR-0017 §7 contract exactly. PASS.
- Break path 2 (force a 3rd attempt under real concurrency contention): wrote an independent script
  (real `build_agent()`, `subagent_max_parallel=1` — worst-case single semaphore slot, 3 prompts
  A/B/C, B bad on attempt 1, good on the nudged retry) and spied on the real `agent.run()` seam.
  Observed spawn order: `A-start/end, B-start/end(BAD), C-start/end, B(retry)-start/end`. B was
  spawned exactly twice (never a third time), A and C exactly once each. This also independently
  proves the semaphore is released BETWEEN a bad child's two attempts — C's single spawn ran to
  completion strictly between B's attempt 1 and B's retry, so a bad child's retry does NOT hold a
  semaphore slot across its whole cycle and cannot starve a sibling beyond ordinary FIFO queuing.
  PASS.
- Break path 3 (sibling isolation + order in a real 3-wide gather with one twice-bad child): ran the
  SWE's `test_a_twice_bad_child_leaves_its_siblings_intact_and_in_order` (unit) and
  `test_a_hallucinating_child_is_retried_once_then_noted_while_its_sibling_folds` (capstone,
  real `Runner`) — both green; independently re-derived via my own script above that section order
  and spawn counts survive a bad child in the middle of the fan-out. PASS.
- Break path 4 (log-body leak): grepped every `logger.warning` call site in `agent.py` — the retry
  and give-up warnings interpolate only the 1-based index (`"subagent %d ..."`); the exception-path
  warning interpolates the INPUT prompt (`%r`), never the report/output body. No report content can
  reach a WARNING line. PASS.

**Acceptance criteria**
- [x] PASS — Empty/whitespace-only report → exactly one nudged retry, good retry folds —
      `test_an_empty_report_is_retried_once_with_the_nudge_and_the_good_retry_folds` +
      `test_the_retry_prompt_is_the_original_prompt_plus_the_nudge` (both green); re-verified
      directly against `_usable_report()`.
- [x] PASS — Non-empty, zero-tool-call report classified BAD and retried —
      `test_a_zero_tool_call_child_is_bad_even_though_it_returned_text` (stub) +
      `test_a_real_text_only_child_is_retried_then_gives_up_with_the_note` (real
      `AgentRunResult.all_messages()`); independently re-derived via `_usable_report()` direct call.
- [x] PASS — Second bad attempt folds the note, spawns == 2 never 3 —
      `test_a_twice_bad_child_folds_the_failure_note_and_never_spawns_a_third_time` (the
      `_ScriptedAgent` asserts on any 3rd attempt); independently forced a 3rd-attempt attempt under
      real semaphore contention (cap=1) and confirmed exactly 2 spawns for the bad child.
- [x] PASS — A good child is never retried (spawn count == 1) —
      `test_a_child_that_called_a_tool_and_reported_is_never_retried` +
      `test_a_real_child_that_reads_code_then_reports_is_never_retried`; independently confirmed A
      and C spawned exactly once each in the adversarial contention script.
- [x] PASS — Sibling reports unaffected (order + content) —
      `test_a_twice_bad_child_leaves_its_siblings_intact_and_in_order` (unit) + capstone
      `test_a_hallucinating_child_is_retried_once_then_noted_while_its_sibling_folds` (real Runner);
      both green, both re-read line by line.
- [x] PASS — `DeferredToolRequests` routes through the same machinery —
      `test_a_deferred_tool_requests_output_routes_through_the_same_retry_machinery` (retry-then-good
      AND bad-twice arms); confirmed with a direct `_usable_report()` call too.
- [x] PASS — `make ci` green — `format-check` + `lint-check` + `uv lock --check` clean; unit 1575/0;
      integration 113 passed / 2 skipped on a clean re-run (first run's 1 failure was an
      unrelated pre-existing Docker container/network flake, confirmed by isolated re-run passing).

**Test-edit audit (the headline scrutiny item)**
Went through `git diff` on both changed test files line by line.
- `tests/unit/decode/tools/test_agent.py`: `_child_returns_text` (text-only, zero tool calls) was
  swapped for a new `_child_globs_then_reports` helper in exactly the tests that need a GOOD child
  (`test_spawn_through_the_loop_folds_the_child_report_and_never_prompts`,
  `test_spawn_builds_fresh_narrowed_read_only_child_deps`,
  `test_child_toolset_is_exactly_read_glob_grep_lsp`) — genuinely required: under the unmodified
  helper these children are now classified BAD by the new predicate and would retry/give-up,
  changing the folded text the assertions key on. `_child_returns_text` itself was NOT deleted — it
  is still used, deliberately, in `test_a_real_text_only_child_is_retried_then_gives_up_with_the_note`
  where a BAD child is exactly what the test wants. `_child_model()` (the standalone `FunctionModel`
  helper) was updated the same way and is used by
  `test_child_run_does_not_thread_parent_usage`, which needed the change for the same reason. The
  `_report()` stand-in gained a `tool_call: bool = True` parameter — defaulting to a GOOD transcript
  — so every PRE-EXISTING call site that didn't need to change (`_EchoAgent`,
  `test_each_child_report_is_truncated_to_the_shared_byte_budget`,
  `test_a_single_child_still_gets_the_whole_byte_budget`,
  `test_a_child_that_raises_gets_a_failure_note_and_its_siblings_still_fold`) kept its exact prior
  assertions unmodified and still passes — no assertion in these was weakened, loosened, or made
  vacuous. `test_deferred_tool_requests_output_returns_a_fallback_note` was replaced by
  `test_a_deferred_tool_requests_output_routes_through_the_same_retry_machinery`, which is STRICTER,
  not weaker: it keeps the "never the raw object" assertion and adds coverage for both the
  retry-then-good and bad-twice arms. No test edit found in this file that papers over a real
  regression; every edit is required by the new (correct) BAD-report classification and none removes
  or dilutes an existing check.
- `tests/integration/test_subagents_capstone.py`: four pre-existing hermetic tests
  (`test_parallel_fanout_overlaps_and_is_bounded_by_subagent_max_parallel`,
  `test_child_report_is_truncated_to_the_byte_cap_through_the_fold`,
  `test_child_toolset_excludes_agent_recursion_default_deny`, plus the untouched
  `test_parent_usage_gauge_excludes_child_counts` which was ALREADY glob-based before 106, confirmed
  via `git show HEAD:...`) had their scripted children changed from bare `TextPart` to
  glob-then-report — each addition is a `glob` tool call inserted BEFORE the existing report/rendezvous
  logic, with every original assertion (peak concurrency == cap, byte-cap-per-child, toolset ==
  {read,glob,grep,lsp}, no-agent-recursion, usage isolation) left completely untouched. Re-ran
  `test_parallel_fanout_overlaps_and_is_bounded_by_subagent_max_parallel` specifically: the
  concurrency rendezvous still happens on the child's FIRST model turn (before the glob tool
  actually runs), so the genuine-overlap proof is unaffected by the extra turn. One new test
  (`test_a_hallucinating_child_is_retried_once_then_noted_while_its_sibling_folds`) was added, not
  substituted for anything. No deletion, no loosened count, no assertion made vacuous in this file
  either.
Verdict on the test-edit audit: every edit is defensible — required by the new correct behavior,
never a self-serving weakening.

**Other checks (per the scrutiny brief)**
- Retry nudge appended to the SAME prompt on the second spawn: confirmed both in
  `test_the_retry_prompt_is_the_original_prompt_plus_the_nudge` and in my own contention script
  (`B(retry)` prompt == `PROMPT_B + agent_module._RETRY_NUDGE`).
- 104 interaction: `_check_substance` spy shows `call_count == 1` even when a retry happens
  (`test_a_retry_never_re_runs_the_substance_guard_over_the_nudged_prompt`); independently confirmed
  `agent_module._faults(prompt + agent_module._RETRY_NUDGE) == []` in a standalone REPL check (the
  nudge is 40 words, well over the 8-word floor, so even a re-run would pass).
- "A real model cannot emit empty final text" claim: read pydantic-ai's `_agent_graph.py` directly
  (`.venv/lib/python3.12/site-packages/pydantic_ai/_agent_graph.py:1230-1242`) — `if text:` gates
  `_handle_text_response`; an empty/whitespace `TextPart` with no tool calls falls through to
  `ToolRetryError` → `consume_output_retry`, i.e. the framework's OWN output-retry loop, before
  `AgentRunResult.output` could ever be empty. The claim is accurate; the empty-text arm of
  `_usable_report` is honestly documented as defensive/stub-only, matching reality.
- Out of scope, verified absent: `git diff --stat` touches only `src/decode/tools/agent.py` (+ the
  two test files + the task file) — no `.env.example`, no `src/decode/config/settings.py`, no
  transport backoff, no file:line validator, no footer/107 work, no change to any 104 guard code
  (`_check_substance`/`_faults` bodies are byte-identical in the diff — only new tests reference
  them).
- `GEMINI_API_KEY` was not set in this environment (`settings.gemini_api_key` empty) — the live smoke
  test was correctly skipped (`test_live_gemini_fanout_smoke`), and I could not run a real hallucinating
  Gemini child. Substituted with the independent real-`build_agent()` + scripted-`FunctionModel`
  probes above, which exercise the exact same code path (`_run_attempt` → `_usable_report` →
  `_spawn_child`) with zero mocking of the retry/validation logic itself.

**Evidence**
```
$ make pre-commit
... 1575 passed in 94.53s ...

$ uv run pytest tests/unit/decode/tools/test_agent.py -q
106 passed in 4.49s

$ uv run pytest tests/integration -q      # clean re-run after the isolated docker flake
113 passed, 2 skipped in 331.53s (0:05:31)

$ uv run pytest tests/integration/test_docker_executor.py::test_timeout_kills_the_command_but_the_container_and_fs_survive -q
1 passed in 7.53s   # confirms the earlier failure was a transient Docker daemon flake

$ uv run python -c "... direct _usable_report() checks ..."
empty -> None
zero-tool-call -> None
good -> a real finding
deferred -> None
ALL INDEPENDENT PREDICATE CHECKS PASSED

# adversarial contention script (cap=1, 3 prompts, B bad-then-good):
SPAWN LOG (label, event, t):
   0.000  A          start
   0.008  A          end
   0.008  B          start
   0.012  B          end
   0.017  C          start
   0.026  C          end
   0.026  B(retry)   start
   0.031  B(retry)   end
```

**Other issues found**
- None blocking. The `test_docker_executor.py` container-reap-adjacent flake
  (`test_timeout_kills_the_command_but_the_container_and_fs_survive` — "No such container") is a
  second instance of Docker daemon flakiness in this environment, same family as the adjudicated
  `test_sandbox_teardown.py` flake; unrelated to this task's diff (nothing under `sandbox/` changed)
  and passes in isolation. Worth a follow-up ticket on Docker test-infra stability if it keeps
  recurring, but out of scope here.

**VERDICT: PASS**

### [PA] 2026-07-14 — Acceptance Review

**VERDICT: ACCEPT**

Reviewed as part of the subagent-fanout feature acceptance (PR #33). This is the substantive half of "more resilient to prompts": `_usable_report` (`src/decode/tools/agent.py:179-193`) catches the answered-from-memory case via `_read_any_code`'s transcript scan (`:165-176`), the one nudged retry + honest give-up note in `_spawn_child` (`:396-411`) bounds a broken child at two attempts, and the exception path (`:404-406`) keeps siblings intact. From the user's POV: a hallucinated child report can no longer masquerade as evidence, and the transcript says plainly which angle produced nothing.
