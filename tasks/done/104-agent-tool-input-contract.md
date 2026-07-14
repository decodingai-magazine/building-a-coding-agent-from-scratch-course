---
id: 104
feature: subagent-fanout
status: done
---

# Agent tool input contract: hardened description + deterministic substance guard

Depends on: 103. Implements ADR-0017 §3.

## Scope

Enforce prompt quality on the way IN, with zero extra LLM calls: a hardened model-facing tool
description states the required shape, and a deterministic guard in the tool body raises
`ModelRetry` on an under-specified prompt so the parent model rewrites it before any child spawns.
Each prompt stays a **free-form string** — no rigid task/context/expected_output slots.

**`src/decode/tools/agent.py`**

- **Hardened tool description** (the function docstring — pydantic-ai lifts it into the tool
  schema). It must state, model-facing:
  - each prompt must carry: the QUESTION to answer, the SCOPE to search (directories, files, or
    patterns to start from), and WHAT THE REPORT MUST CONTAIN;
  - "for a broad question like 'explore the repo', give at least 3 DISTINCT angles";
  - a single focused question = a one-element list;
  - at most 6 prompts per call (matches 103's width cap).
- **Deterministic substance guard**, applied per prompt element, before any spawn, alongside
  103's structural guards. The exact heuristic is the SWE's call (e.g. a minimum-substance
  check — length/word floor plus presence of scope-ish content), but it MUST be: deterministic,
  cheap (no LLM, no I/O, no network), and its `ModelRetry` message must name WHICH prompt
  (by index) is under-specified and WHAT is missing (question / scope / expected report content).
- Guard failures never spawn a child and never consume semaphore slots.

**Tests**

- `tests/unit/decode/tools/test_agent.py` — NEW: an under-specified prompt (e.g. `"explore"`)
  raises `ModelRetry` naming the offending index and the missing part(s); a well-specified prompt
  passes; a mixed list (one good, one bad) is rejected as a whole with the bad index named;
  the guard is deterministic (same input, same outcome, twice); guard fires before spawn
  (spy: `agent.run` not called); the tool schema description carries the "3 DISTINCT angles"
  push and the per-prompt shape (assert on the registered tool's description / docstring).
- **Update every existing test whose scripted spawn prompts are now too terse to survive the
  guard** — the loop-driven prompts in `tests/unit/decode/tools/test_agent.py`, and the scripted
  parents in `tests/integration/test_subagents_capstone.py` /
  `test_observability_capstone.py` (e.g. `"explore area 0"`): rewrite them as well-formed
  prompts (question + scope + expected report). Terse prompts remain ONLY inside the
  guard-specific tests.

## Acceptance Criteria

- [x] The registered `agent` tool's model-facing description states the per-prompt shape (question + scope + report content), the "at least 3 DISTINCT angles for broad questions" push, the one-element-list case, and the width cap of 6.
- [x] An under-specified prompt raises `ModelRetry` whose message names the offending prompt index AND what is missing; no child is spawned (spy on `agent.run` proves it).
- [x] A well-specified list of prompts passes the guard and spawns normally.
- [x] The guard is deterministic and makes no LLM/network/file call (unit-testable in isolation, same result on repeat).
- [x] All previously-green tests pass with their scripted prompts upgraded to guard-passing form; `make ci` green.

## Out of scope

- Prompt-injection hardening (feature non-goal, ADR-0017).
- Deduping duplicate prompts (allowed by design — decision locked).
- Output-side validation (106); persona wording (105); footer (107).

## Log

### [SWE] 2026-07-13 — Implementation

**Files modified**
- `src/decode/tools/agent.py` — hardened model-facing tool docstring (per-prompt shape, "at least
  3 DISTINCT angles", one-element list, cap of 6) + the deterministic substance guard
  (`_missing_parts` / `_check_substance`), called after 103's structural guards and before the fan-out.
- `tests/unit/decode/tools/test_agent.py` — 11 new tests: the registered tool's schema description,
  the guard predicate (lazy vs well-formed, which parts are missing), determinism, index-naming
  `ModelRetry`, whole-call rejection of a mixed list, and guard-fires-before-`agent.run` (spy).

**The heuristic** (reasoning lives in the code comment above `MIN_PROMPT_WORDS`)
Three cheap signals over the prompt string — QUESTION (a `?` or an interrogative/investigative word),
SCOPE (a path-ish token like `src/decode/` / `gate.py` / `**/*.py`, or a scoping word), REPORT (a
word asking for something back) — plus a **word floor of 8**, because keyword presence alone is
gameable (`"how? src/ report"` ticks all three boxes and says nothing). The floor is what actually
rejects `"explore the repo"`. Deliberately biased to **false-accept**: a false nag burns a model turn
on a prompt that was already fine; a false accept only leaves us where we were before the guard. No
stemming, no embeddings — pure string inspection, no LLM/IO/network/clock.

**Tests**
- Unit: 1512 passing, 0 failing (`make unit-tests`, `make pre-commit`).
- Integration: 112 passing, 2 skipped (no live API keys) — `make integration-tests`.
- No existing scripted prompt needed rewriting: task 103 had already written the loop-driven unit
  prompts and both capstones' (`test_subagents_capstone.py`, `test_observability_capstone.py`)
  scripted prompts in well-formed shape. Verified by running both capstones green against the live
  guard. Terse prompts exist ONLY inside the guard-specific tests, as the spec requires.

**Acceptance criteria**
- [x] Description states shape + 3-angles push + one-element list + cap 6 — `test_the_registered_tool_description_states_the_input_contract` (asserts on the REGISTERED tool's `.description`, incl. that the literal `6` appears and `MAX_FANOUT_PROMPTS` does not).
- [x] Under-specified prompt → `ModelRetry` naming index + missing parts, no spawn — `test_an_under_specified_prompt_raises_model_retry_naming_its_index_and_what_is_missing`, `test_the_substance_guard_fires_before_agent_run_is_ever_called` (spies `agent.run` itself).
- [x] Well-specified list passes and spawns — `test_a_well_specified_list_passes_the_substance_guard_and_spawns`, plus `test_a_well_formed_prompt_is_never_under_specified` over every prompt the suite scripts.
- [x] Deterministic, no LLM/network/file call — `test_the_guard_is_pure_and_deterministic_on_repeat` (pure fn in isolation), `test_the_substance_guard_is_deterministic_through_the_tool` (identical message twice).
- [x] Previously-green tests all pass — 1512 unit + 112 integration.
- Mixed list rejected as a whole with only the bad index named — `test_a_mixed_list_is_rejected_as_a_whole_with_only_the_bad_index_named`.

**Evidence**

E2E through a REAL `build_agent()` (FunctionModel parent, no network): the parent makes a lazy call,
the guard nags, the model rewrites, two children spawn, the labelled aggregate folds back.

```
>>> parent leg 1: lazy call  agent(prompts=['explore the repo'])
>>> the model received this ModelRetry:

No subagent was spawned: some prompts are under-specified. Every prompt must carry the QUESTION to
answer, the SCOPE to search (a directory, file, or glob pattern to start from), and WHAT THE REPORT
MUST CONTAIN.
- Prompt 1 ("explore the repo") is missing: the QUESTION to answer, the SCOPE to search (a
  directory, file, or glob pattern to start from), WHAT THE REPORT MUST CONTAIN.
Rewrite the prompts above and call the agent tool again.

>>> parent leg 2: rewritten, well-formed prompts
>>> tool result folded back to the parent:

## Subagent 1 — "How does the permission gate decide allow/ask/deny? Search src/decode/permissions/ and report the decision path with file:line evidence."
child report: gate.py:42 decides.

## Subagent 2 — "How is bash dispatched to a sandbox? Search src/decode/sandbox/ and report the backends with file:line evidence."
child report: gate.py:42 decides.

>>> parent final output: FINAL ANSWER
```

```
$ make unit-tests
1512 passed in 93.00s
$ make integration-tests
112 passed, 2 skipped in 311.79s
```

**Notes**
- FLAKE (pre-existing, unrelated): the FIRST full `make integration-tests` run failed
  `test_sandbox_teardown.py::test_headless_bypass_flow_reaps_the_real_container_on_exit` (real Docker
  container reap under full-suite load). It passes in isolation both WITH and WITHOUT this change,
  and the second full-suite run was green (112 passed). Not caused by task 104 — flagging for the
  Tester.
- The 1-based prompt index in the nag matches the `## Subagent i` section labels the model already
  sees, so the two vocabularies agree.
- Untouched by design (out of scope): output-side validation (106), the explore persona (105), the
  Synthesis Footer (107). No new Settings field, no `.env.example` entry.

### [Tester] 2026-07-13 22:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit`: 1512 passed, 0 warnings)
- Unit tests: 1512 passed / 0 failed (`make pre-commit` runs the full unit suite as its push hook)
- Integration tests: 112 passed / 2 skipped (`make integration-tests`) — matches SWE's claim exactly
- Warnings: 0 (project `filterwarnings=["error"]`; any warning would show as a failure)

**E2E adversarial pass**
- Happy path: real `build_agent()` + `AgentTurnHandler` + `FunctionModel`, parent calls
  `agent(prompts=[well-formed A, well-formed B])` → two `## Subagent i` sections fold back, parent
  emits final text. PASS.
- Break path 1 (false-accept via keyword stuffing): `_missing_parts("how? src/ report")` → all
  three parts flagged missing (word floor of 8 catches it, 3 words). Padding to ≥8 meaningless
  keyword-stuffed words DOES get through, e.g. `_missing_parts("how why what src file report list
  summary evidence detail")` → `[]` (accepted). Confirms the SWE's stated false-accept bias is
  real and IS bounded by the 8-word floor for short gaming attempts, but NOT bounded against
  padded keyword salad ≥8 words. Documented, intentional per the SWE's own reasoning (a false
  accept only restores pre-guard status quo) — PASS WITH NOTE, not blocking.
- Break path 2 (false reject — imperative-phrased, realistic, well-formed prompts, NO literal `?`
  and NO word from `_QUESTION_WORDS`): tested 5 realistic, verbose (15-20 word), clearly
  well-formed exploration prompts starting with `Summarize` / `Document` / `List` / `Enumerate` /
  `Map` (each carrying an explicit scope path and an explicit report ask with file:line evidence).
  **All 5 were falsely rejected**, every one flagged `missing: ['the QUESTION to answer']` even
  though scope and report content were correctly recognized. Example:
  `_missing_parts("Summarize the retry logic in src/decode/tools/agent.py including the
  ModelRetry budget and report the file:line for each check.")` → `['the QUESTION to answer']`.
  Expected: none of these rejected (they satisfy the tool description's own three-part contract —
  a clear investigative ask, an explicit scope, an explicit report requirement). FAIL.
- Break path 3 (false reject — terse-but-substantive, under the 8-word floor): e.g.
  `_missing_parts("Trace gate.py's ASK path and report evidence.")` (7 words) →
  all three parts flagged missing, despite containing a question word (`Trace`), a path-ish scope
  token (`gate.py`) and a report word (`report`). The word floor overrides genuinely present
  signals and reports a misleading "all three missing" instead of "too short". Same class of
  defect as break path 2, smaller blast radius — noted, folded into the same FAIL.
- Break path 4 (CRITICAL — retry-budget interaction, driven through a REAL loop, not a stub): built
  a `FunctionModel` that on the parent's first `agent` call sends 7 prompts (width-cap nag), on the
  second call sends `["explore the repo"]` (substance nag), on the third call sends well-formed
  prompts. Drove it through real `build_agent()` + `AgentTurnHandler` (script:
  `/private/tmp/.../scratchpad/test_retry_budget_e2e.py`). Result: **no
  `UnexpectedModelBehavior`**, the run completed normally, and leg 3 spawned 2 real children whose
  labelled sections folded back (`## Subagent 1 — "..."` / `## Subagent 2 — "..."`). Confirms
  `AGENT_TOOL_RETRIES = 3` genuinely absorbs a width-cap nag + a substance nag before a successful
  call — exactly the failure mode the raised retry budget exists to prevent. PASS.
- Break path 5 (guard fires before spawn / no semaphore slot): code-read confirms `_check_substance`
  (a pure, synchronous call) runs before `asyncio.gather(..._spawn_child...)`, and `_spawn_child` is
  the only caller of `_semaphore()` — architecturally the guard cannot reach the semaphore. Backed
  by the existing spy test `test_the_substance_guard_fires_before_agent_run_is_ever_called`
  (patches `agent.run` itself, not `_require_main_agent`) — `run.assert_not_called()` passes. PASS.
- Break path 6 (determinism): `_missing_parts` called twice on the same well-formed / lazy / gaming
  inputs (12 prompts total) produced identical results every time; ran the tool-level guard twice
  via the existing `test_the_substance_guard_is_deterministic_through_the_tool`. PASS.
- Break path 7 (SWE's "no rewrite needed" claim): greped every scripted prompt in
  `tests/unit/decode/tools/test_agent.py`, `tests/integration/test_subagents_capstone.py`, and
  `tests/integration/test_observability_capstone.py` for terse literals (`"explore area 0"` style)
  — none found outside the guard-specific tests; all scripted spawn prompts (`_PROMPT_A/B/C`,
  `_prompts(n)`, `_child_prompt(i)`) are phrased as questions with explicit scope + report asks, so
  they trivially clear the guard's narrow QUESTION-word/`?` requirement. Claim VERIFIED true. PASS.

**Flake adjudication — `test_sandbox_teardown.py::test_headless_bypass_flow_reaps_the_real_container_on_exit`**
Pre-existing, NOT a regression from this task. Evidence: (1) `git log --oneline -- tests/integration/test_sandbox_teardown.py` shows the file was last touched in PR #25 (ADR-0012), long before 103/104; (2) task 104's entire diff is 3 files (`src/decode/tools/agent.py`, its unit test, the task file) — zero overlap with sandbox/docker/runtime code; (3) ran the test 3× in isolation, all passed (~11-13s each); (4) re-ran the full `make integration-tests` myself independently — 112 passed, 0 failed, no recurrence. Consistent with a real-Docker-daemon timing flake under full-suite sequential load (the suite runs `test_docker_executor.py`, `test_sandbox_capstone.py` and `test_sandbox_teardown.py` back to back, all spinning real containers). Not blocking.

**Acceptance criteria**
- [x] PASS — Description states shape + 3-angles push + one-element list + cap 6 — `test_the_registered_tool_description_states_the_input_contract` passes; confirmed by reading the registered docstring at `src/decode/tools/agent.py:272-292`.
- [x] PASS — Under-specified prompt → `ModelRetry` naming index + missing parts, no spawn — `test_an_under_specified_prompt_raises_model_retry_naming_its_index_and_what_is_missing` + `test_the_substance_guard_fires_before_agent_run_is_ever_called` (spy on `agent.run`) both pass; reproduced manually.
- [ ] FAIL — A well-specified list of prompts passes the guard and spawns normally.
      Expected: any prompt that genuinely carries a question/ask + scope + report content (per the
      tool description's own three-part contract) passes the guard.
      Actual: realistic, verbose, well-formed prompts phrased as imperatives (`Summarize …`,
      `Document …`, `List …`, `Enumerate …`, `Map …` — no `?`, no word in `_QUESTION_WORDS`) are
      rejected 5/5 in testing, always mislabelled as missing ALL of question/scope/report even when
      scope and report are correctly detected. Terse-but-substantive prompts under
      `MIN_PROMPT_WORDS` (8) are also rejected wholesale, discarding correctly-detected signals.
      Fix: broaden the QUESTION signal in `src/decode/tools/agent.py::_missing_parts` (e.g. widen
      `_QUESTION_WORDS` with common investigative imperatives — list/summarize/document/enumerate/
      map/outline/audit/review/inventory/catalog/verify/confirm/check — or treat detected SCOPE +
      REPORT + a directive verb as jointly satisfying QUESTION), and reconsider the sub-floor
      all-or-nothing report (a 6-7 word prompt with 2/3 signals present should say what's actually
      missing, not lie that all three are absent). Add regression tests pinning both fixes.
- [x] PASS — Deterministic, no LLM/network/file call — `test_the_guard_is_pure_and_deterministic_on_repeat`, `test_the_substance_guard_is_deterministic_through_the_tool`, and my own repeat-call script all pass; code reads pure string/regex ops only.
- [x] PASS — Previously-green tests all pass; `make ci` green — 1512 unit passed, 112 integration passed / 2 skipped, reproduced independently (see Test summary).

**Evidence**

```
$ make pre-commit
... (full suite) ...
1512 passed in 94.54s (0:01:34)

$ make integration-tests
... (full suite) ...
112 passed, 2 skipped in 314.15s (0:05:14)
```

```
>>> _missing_parts("Summarize the retry logic in src/decode/tools/agent.py including the
    ModelRetry budget and report the file:line for each check.")
['the QUESTION to answer']          # FALSE REJECT — 17 words, clear scope + report ask
>>> _missing_parts("List all Pydantic models defined under src/decode/entities/ and report
    their field names with file:line citations.")
['the QUESTION to answer']          # FALSE REJECT
>>> _missing_parts("Trace gate.py's ASK path and report evidence.")   # 7 words
['the QUESTION to answer', 'the SCOPE to search (...)', 'WHAT THE REPORT MUST CONTAIN']
                                     # FALSE REJECT — has a question word, a path token, AND a
                                     # report word, all discarded by the sub-8-word floor
```

```
>>> real-loop retry-budget stress (width-cap nag -> substance nag -> success), no stubs:
RUN COMPLETED WITHOUT ABORT
TOOLRESULT ok=False name=agent output="You asked for 7 subagents; the limit is 6 per call. ..."
TOOLRESULT ok=False name=agent output="No subagent was spawned: some prompts are under-specified. ..."
TOOLRESULT ok=True  name=agent output="## Subagent 1 — \"...\"\n\nCHILD REPORT... \n\n## Subagent 2 — ..."
```

**Other issues found**
- Minor: SWE's hand-off claims "11 new tests"; `git diff` shows 10 new `def test_...` functions
  (2 of which are parametrized, yielding more collected test IDs). Not blocking — cosmetic
  miscount in the report, not a code defect.
- The false-accept bias (keyword-stuffed prompts ≥8 words passing) is real but intentional and
  documented in-code; not blocking per the SWE's own stated risk tradeoff, but worth a one-line
  callout in the docstring/comment that the floor does not stop *padded* gaming, only *short*
  gaming, in case a future reader assumes the floor is a general anti-gaming measure.

**VERDICT: FAIL**

Root cause: the deterministic substance guard's QUESTION signal is narrower than the tool
description's own contract, producing reproducible false rejects against realistic, well-formed,
imperative-phrased exploration prompts (a phrasing style at least as common as interrogative
phrasing for tasks like "list", "document", "summarize", "enumerate", "map"), and against
terse-but-substantive prompts under the 8-word floor whose genuinely-present signals get discarded
wholesale. Per ADR-0017 §3 and this task's own design rationale, a false reject is the dangerous
failure mode (burns a model turn, eats into the raised retry budget) — this is not a cosmetic nit.
Retry-budget resilience (the critical 103×104 interaction) holds correctly under real-loop testing;
the flake in `test_sandbox_teardown.py` is adjudicated pre-existing and not blocking. SWE: widen
the QUESTION signal (and fix the sub-floor all-or-nothing reporting) per the Fix note above, add
regression tests for the imperative-phrasing and terse-but-substantive prompt classes, and
resubmit.

### [SWE] 2026-07-13 23:40 — Fixes (QA round 1)

Both defects fixed in `src/decode/tools/agent.py`; the guard's shape, cost and bias are unchanged.

**Defect 1 — false rejects on imperative-phrased prompts (the dangerous class)**
`_QUESTION_WORDS` widened from 18 to 33 words: the 8 interrogatives are kept, and the investigative
imperatives a parent model actually writes are added — `summarize`/`summarise`, `list`, `document`,
`enumerate`, `map`, `find`, `show`, `outline`, `review`, `audit`, `inventory`, `catalog`/`catalogue`,
`survey`, `inspect`, `examine`, `check`, `verify`, `assess`, `evaluate`, `diagnose` (alongside the
existing `trace`, `locate`, `identify`, `describe`, `explain`, `compare`…). Still one frozenset
lookup over lowercased words: deterministic, no stemming, no NLP pipeline. All five of the Tester's
rejected prompts now pass, and they are pinned as a table (`_IMPERATIVE_PROMPTS`).

**Defect 2 — the ModelRetry message lied when the word floor was what failed**
The floor is no longer a stand-in for the three parts. `_missing_parts` is now signal-only and
TRUE by construction (length is not judged there), and a new `_faults(prompt)` composes the
rejection reasons: the parts genuinely absent, plus `_TERSE` when the prompt is under
`MIN_PROMPT_WORDS`. So `"Trace gate.py's ASK path and report evidence."` (7 words, all three parts
present) is now told exactly one true thing — it is too terse — instead of three false things.

Nag output, before → after:
```
- Prompt 1 ("Trace gate.py's ASK path and report evidence.") is missing: the QUESTION to answer, the SCOPE to search (…), WHAT THE REPORT MUST CONTAIN.     # LIE (all three are present)
- Prompt 1 ("Trace gate.py's ASK path and report evidence.") is too terse — give more detail (aim for at least 8 words).                                    # TRUE
```
A prompt failing on both counts still reports both, each clause true:
```
- Prompt 1 ("explore the repo") is missing the QUESTION to answer; missing WHAT THE REPORT MUST CONTAIN; too terse — give more detail (aim for at least 8 words).
```
(Note it no longer claims SCOPE is missing — "repo" is a scope word. The old code said it was.)

**Properties the Tester verified and that still hold**
- Guard fires BEFORE any spawn, consumes no semaphore slot — `_check_substance` is still the same
  pure synchronous call ahead of the `asyncio.gather`; spy test on `agent.run` still green.
- Deterministic / no LLM / no I/O — pure string + frozenset + regex work only.
- 103×104 retry-budget interaction — re-driven through a REAL loop (below): width-cap nag then
  substance nag then success, no `UnexpectedModelBehavior`.
- False-accept bias preserved, NOT over-corrected: short keyword-stuffing (`"how? src/ report"`) is
  still caught by the floor; padded keyword salad ≥8 words still gets through, and the code comment
  now says so explicitly ("the floor stops SHORT gaming, not PADDED gaming — that is the accepted
  trade, not an oversight"), per the Tester's callout.

**Files modified**
- `src/decode/tools/agent.py` — widened `_QUESTION_WORDS`; `_missing_parts` made signal-only;
  new `_faults` + `_TERSE`; `_check_substance` emits one clause per TRUE fault; comment block
  rewritten to state the imperative-phrasing rationale and the padded-gaming trade.
- `tests/unit/decode/tools/test_agent.py` — regression tests for both defects.

**Tests** (new — both defects pinned)
- `_IMPERATIVE_PROMPTS`: the Tester's five realistic 15–20-word imperative prompts (`Summarize` /
  `List` / `Document` / `Enumerate` / `Map`), asserted twice — as the guard predicate
  (`test_a_well_formed_prompt_is_never_under_specified`, now parametrized over them) and THROUGH the
  tool (`test_an_imperative_prompt_is_not_falsely_rejected_by_the_tool`: no `ModelRetry`, the child
  actually spawns, one labelled section folds back).
- `test_a_sub_floor_prompt_is_told_it_is_terse_not_that_present_parts_are_missing`: the 7-word
  terse-but-substantive prompt — asserts `_missing_parts(...) == []` (all three parts present), the
  `ModelRetry` names the floor and the number 8, and does NOT name any of the three parts in the
  problem line; no spawn.
- `test_a_lazy_prompt_is_under_specified` now asserts on `_faults` (the guard's real predicate).

**Evidence**

```
$ make format-check && make lint-check
181 files already formatted
All checks passed!

$ make pre-commit
1523 passed in 85.84s (0:01:25)          # was 1512 — +11 collected, 0 failing

$ uv run pytest tests/integration/test_subagents_capstone.py \
      tests/integration/test_observability_capstone.py \
      tests/integration/test_milestone1_capstone.py -q
12 passed, 2 skipped in 2.20s            # skips = live GEMINI/OPIK keys unset
```

E2E through a REAL `build_agent()` + `AgentTurnHandler` loop (FunctionModel parent, no network) —
the exact 103×104 stress the Tester ran, with leg 3 switched to the previously-rejected imperatives:

```
RUN COMPLETED WITHOUT ABORT (no UnexpectedModelBehavior)
legs: ['leg1: 7 prompts', 'leg2: lazy prompt', 'leg3: imperative prompts']
--- MODEL RETRY : You asked for 8 subagents; the limit is 6 per call. Consolidate your angles …
--- MODEL RETRY : No subagent was spawned: some prompts are under-specified. … - Prompt 1
                  ("explore the repo") is missing the QUESTION to answer; missing WHAT THE REPORT
                  MUST CONTAIN; too terse — give more detail (aim for at least 8 words). …
--- TOOL RETURN: ## Subagent 1 — "Summarize the retry logic in src/decode/tools/agent.py including
                  the ModelRetry budget and report the file:line for each check." | child report… |
                  ## Subagent 2 — "Map the sandbox executor seam across src/decode/sandbox/ …"
--- FINAL       : ['FINAL ANSWER']
```

**Notes**
- Guard-predicate vocabulary changed: `_missing_parts` no longer folds the floor in, so the "does the
  guard reject this?" predicate is `_faults`. Any future check should call `_faults`, not
  `_missing_parts`.
- `summarize` / `list` / `document` now sit in BOTH `_QUESTION_WORDS` and `_REPORT_WORDS` — deliberate:
  such a word IS both an investigative ask and a report ask ("List X and report Y"). Given the
  false-accept bias this is the safe direction, and the floor still stops one-word gaming.
- `test_sandbox_teardown.py` container-reap flake: ignored per the Tester's adjudication (zero overlap
  with this diff) — not touched.
- NOT COMMITTED — awaiting Tester re-review.

### [Tester] 2026-07-13 23:55 — Re-QA (round 2)

**Test summary**
- Format / lint: PASS — `make format-check` (181 files already formatted), `make lint-check` (all checks passed).
- Unit tests: 1523 passed / 0 failed (`make pre-commit`, 86.11s) — matches SWE's claim of "was 1512, +11 collected."
- Integration tests: 112 passed / 2 skipped (`make integration-tests`, 312.23s) — matches SWE's claim exactly; the two skips are the live-key-gated smokes (`test_observability_capstone.py`, `test_subagents_capstone.py`). No `test_sandbox_teardown.py` flake this run.
- Warnings: 0.

**E2E adversarial pass**
- Happy path: real `_faults`/`_missing_parts` + tool-level call on `_PROMPT_A/B/C` — no faults, spawns normally. PASS.
- Break path 1 (round-1 regression check — the exact five prompts I previously rejected): all five now pass `_faults()` and spawn through the real tool (`test_an_imperative_prompt_is_not_falsely_rejected_by_the_tool`, reproduced manually). PASS — defect 1 (the five pinned prompts) is fixed.
- Break path 2 (round-1 regression check — the terse-but-substantive prompt): `_missing_parts("Trace gate.py's ASK path and report evidence.")` → `[]` (all three parts detected present); `_faults(...)` → only the TERSE clause; the raised `ModelRetry` names the floor and does NOT claim any of the three present parts are missing (verified via `message.split("- Prompt 1")[1]` containing none of `_QUESTION`/`_SCOPE`/`_REPORT`). PASS — defect 2 (the lying nag) is fixed for this exact case, and the fix is structurally sound (`_missing_parts` is now signal-only; `_faults` composes genuine absences + `_TERSE`).
- Break path 3 (**CRITICAL — fresh, adversarial battery of 8 realistic well-formed prompts the SWE has NOT seen**, both via `_faults()` directly AND through the real `agent()` tool call with a spawn spy): 6 of 8 (75%) were **falsely rejected**, reproduced at both the predicate level and the full tool-call level (`ModelRetry` raised, `spawn.assert_not_called()` passes — i.e. the false reject is "real," not a test artifact). Examples, all ≥15 words with an explicit scope path and an explicit report ask:
  - `"Outline the tool registration flow in src/decode/tools/registry.py and note which module each tool lives in with file:line references."` → `missing WHAT THE REPORT MUST CONTAIN` (report ask phrased as "note ... with file:line references" — no word in `_REPORT_WORDS` matches "note"/"references").
  - `"Where does the settings singleton get constructed? Search src/decode/config/settings.py and describe the lazy-init pattern used."` → `missing WHAT THE REPORT MUST CONTAIN` (report ask phrased as "describe the pattern used" — "describe" is in `_QUESTION_WORDS` but NOT in `_REPORT_WORDS`).
  - `"Break down the retry budget for the bash tool across src/decode/tools/bash.py and produce a short summary of each guard clause."` → `missing the QUESTION to answer` ("Break down" not in `_QUESTION_WORDS`).
  - `"Walk through the sandbox handback flow under src/decode/sandbox/handback.py and cite the git commands issued, in order."` → `missing the QUESTION to answer` ("Walk through" not in `_QUESTION_WORDS`).
  - `"Chart every Pydantic-AI tool registered by the harness, searching src/decode/tools/, and give me a table of tool name to file."` → `missing the QUESTION to answer; missing WHAT THE REPORT MUST CONTAIN` ("Chart" not covered, "give me a table" not covered).
  - `"Dig into the compaction trigger inside src/decode/context/ and tell me the token threshold, citing the exact line."` → `missing the QUESTION to answer` ("Dig into" not covered).
  Only 2/8 fresh prompts passed. This is not a cherry-picked edge case — it is a 75% false-reject rate on a small, realistic sample of phrasings a parent model plausibly uses ("outline and note", "search and describe", "break down and produce a summary", "walk through and cite", "chart and give me a table", "dig into and tell me"), none of them exotic. FAIL — confirms the round-1 fix is **overfitted**: it patches the exact five prompts it was handed (all now correctly pinned as regression tests) without closing the underlying gap, which is structural — a closed frozenset over investigative verbs and report-verbs will always have holes a real model's phrasing variety will find. Evidence reproduced via a temporary pytest file exercising the real `agent_module.agent()` call path (deleted after use, not part of the diff).
- Break path 4 (guard NOT hollowed out — lazy prompts still rejected): re-ran the original 4 lazy prompts (`"explore"`, `"explore the repo"`, `"look around"`, `"the codebase"`) plus 3 fresh ones (`"go look at the code"`, `"tell me about this project"`, `"dig in"`) — all 7 correctly rejected with faults. The widened `_QUESTION_WORDS` did NOT create a false-accept regression on genuinely lazy input. PASS.
- Break path 5 (**103×104 retry-budget interaction, re-proven through a REAL loop, not a stub**): built a fresh `FunctionModel` + real `build_agent()` + real `Runner`/`AgentTurnHandler` (not reusing the SWE's script) driving: leg 1 = 7 prompts (width-cap `ModelRetry`), leg 2 = `["explore the repo"]` (substance `ModelRetry`), leg 3 = 2 well-formed prompts (spawns 2 real children through the same FunctionModel in child context), leg 4 = final text. Result: `legs == ["width-cap-leg", "lazy-leg", "success-leg", "final"]`, no `UnexpectedModelBehavior`, tool ultimately returned successfully (`sink.tool_result_names() == {"agent"}`). Confirms `AGENT_TOOL_RETRIES = 3` still correctly absorbs two consecutive nags before success. PASS.
- Break path 6 (determinism): `_missing_parts`/`_faults` called twice on 12+ prompts (my fresh battery + lazy set) — identical output every time. PASS.
- Break path 7 (guard fires before spawn / no semaphore slot): unchanged code path — `_check_substance` is still a pure synchronous call ahead of the fan-out; spy test `test_the_substance_guard_fires_before_agent_run_is_ever_called` passes; break path 3's tool-level false-reject reproduction independently confirms `spawn.assert_not_called()` on every rejection. PASS.
- Break path 8 (out-of-scope check): `git diff --stat` limited to exactly the 3 expected files (`src/decode/tools/agent.py`, its unit test, the task file) — no `src/decode/config/settings.py`, no `.env.example`, no `src/decode/agents/` (105 persona), no retry-machinery module (106), no footer module (107) touched. PASS.

**Acceptance criteria**
- [x] PASS — Description states shape + 3-angles push + one-element list + cap 6 — `test_the_registered_tool_description_states_the_input_contract` passes; docstring re-read at `src/decode/tools/agent.py:316-333`, literal `6` present, `MAX_FANOUT_PROMPTS` absent.
- [x] PASS — Under-specified prompt → `ModelRetry` naming index + missing parts, no spawn — `test_an_under_specified_prompt_raises_model_retry_naming_its_index_and_what_is_missing`, `test_the_substance_guard_fires_before_agent_run_is_ever_called` pass; reproduced manually.
- [ ] FAIL — A well-specified list of prompts passes the guard and spawns normally.
      Expected: any prompt that genuinely carries a question/ask + scope + report content (per the
      tool description's own three-part contract) passes the guard, regardless of which of the many
      legitimate ways a model phrases an investigative ask / report request it uses.
      Actual: 6 of 8 fresh, realistic, well-formed prompts (not seen by the SWE, verified through
      BOTH `_faults()` directly and the full `agent()` tool call with a spawn spy) are still falsely
      rejected. The round-1 fix widened the word lists to cover the five specific prompts from the
      first QA round but the underlying design — closed frozenset membership over investigative verbs
      (`_QUESTION_WORDS`) and report verbs (`_REPORT_WORDS`) — remains structurally narrow. Concrete
      gaps found this round: "describe" is in `_QUESTION_WORDS` but missing from `_REPORT_WORDS` (so
      "describe the pattern used" as a report ask isn't recognized); "note"/"references"/"cite the...
      in order"/"give me a table"/"tell me" aren't in `_REPORT_WORDS`; "break down"/"walk through"/
      "chart"/"dig into" (two-word investigative phrasal verbs) aren't in `_QUESTION_WORDS` (which is
      single-word only).
      Fix: this is the second round the fix has been overfitted to the exact prompts handed to it.
      Recommend a structural change rather than another word-list patch — e.g. treat a genuine SCOPE
      signal (a path-ish token) + a genuine intent-to-report signal (an imperative mood detector, or
      simply: any sentence starting with a capitalized verb) as jointly satisfying QUESTION, since the
      tool description's own example ("Good: 'How does the permission gate decide...'") shows question
      and imperative phrasings are meant to be treated as equivalent; and/or broaden `_REPORT_WORDS`
      to include common report-content phrasing ("note", "describe", "tell me", "give me", "with...
      evidence/citations/references", "in order"). Add regression tests pinning the fresh prompts in
      this log entry (not just the five from round 1) so future word-list edits can't re-narrow the
      guard back to overfitting the last failing example handed to it.
- [x] PASS — Deterministic, no LLM/network/file call — `test_the_guard_is_pure_and_deterministic_on_repeat`, `test_the_substance_guard_is_deterministic_through_the_tool`, and my own repeat-call script all pass; code reads pure string/frozenset/regex ops only.
- [x] PASS — Previously-green tests all pass; `make ci` green — 1523 unit passed, 112 integration passed / 2 skipped, both reproduced independently (see Test summary).

**Evidence**

```
$ make pre-commit
1523 passed in 86.11s (0:01:26)

$ make integration-tests
112 passed, 2 skipped in 312.23s (0:05:12)
```

```
>>> fresh adversarial battery (8 prompts the SWE never saw), through _faults() AND the real tool:
[FALSE REJECT] "Outline the tool registration flow in src/decode/tools/registry.py and note which
  module each tool lives in with file:line references." -> faults=['missing WHAT THE REPORT MUST CONTAIN']
[FALSE REJECT] "Where does the settings singleton get constructed? Search
  src/decode/config/settings.py and describe the lazy-init pattern used." ->
  faults=['missing WHAT THE REPORT MUST CONTAIN']
[FALSE REJECT] "Break down the retry budget for the bash tool across src/decode/tools/bash.py and
  produce a short summary of each guard clause." -> faults=['missing the QUESTION to answer']
[FALSE REJECT] "Walk through the sandbox handback flow under src/decode/sandbox/handback.py and cite
  the git commands issued, in order." -> faults=['missing the QUESTION to answer']
[FALSE REJECT] "Chart every Pydantic-AI tool registered by the harness, searching
  src/decode/tools/, and give me a table of tool name to file." ->
  faults=['missing the QUESTION to answer', 'missing WHAT THE REPORT MUST CONTAIN']
[FALSE REJECT] "Dig into the compaction trigger inside src/decode/context/ and tell me the token
  threshold, citing the exact line." -> faults=['missing the QUESTION to answer']
[PASS]  "Inspect how the TUI renders streaming tokens under src/decode/tui/ and report the render
  loop's entry point with file:line evidence." -> faults=[]
[PASS]  "Confirm whether the memory loader in src/decode/memory/ reads AGENTS.md before MEMORY.md,
  and back up your finding with file:line detail." -> faults=[]

>>> confirmed through the real tool call (pytest, mocker.patch.object(agent_module,
    "_require_main_agent")): all 6 FALSE REJECT prompts above raise ModelRetry and
    spawn.assert_not_called() passes -- the false reject is real, not a predicate-only artifact.
```

```
>>> real-loop retry-budget re-stress (fresh script, real build_agent()+Runner+AgentTurnHandler):
LEGS: ['width-cap-leg', 'lazy-leg', 'success-leg', 'final']
TOOL RESULT NAMES: {'agent'}
1 passed in 1.55s -- no UnexpectedModelBehavior
```

**Other issues found**
- `code-review` plugin (enabled in `.claude/settings.json`) is PR-oriented (`gh pr view`/`gh pr diff`
  against a GitHub pull request) and this task is in file-mode tracker with no open PR yet — not
  invokable in its current form against uncommitted local work. Not blocking; noted for when this
  branch is pushed.
- The round-1 "false-accept bias preserved" claim re-checked and still holds: short keyword-stuffing
  (`"how? src/ report"`, 3 words) is still caught by the floor; the code comment now states the
  padded-vs-short-gaming trade explicitly. Not blocking.

**VERDICT: FAIL**

Round-1 defect 2 (the lying nag) is genuinely fixed — `_faults` composing signal-absence with a
separate `_TERSE` clause is a sound, structural fix, not a patch, and holds under my own re-testing.
Round-1 defect 1 (false rejects on imperative prompts) is only **partially** fixed: the SWE correctly
widened the word lists to pass the exact five prompts I handed them in round 1 (now pinned as
regression tests, which is good practice), but a fresh, realistic 8-prompt battery this round shows a
75% false-reject rate persists — the fix treated the symptom (five specific prompts) rather than the
cause (a closed word-list heuristic can't keep pace with a model's phrasing variety, and specific gaps
remain even within the SWE's own added categories — e.g. "describe" recognized as investigative but
not as a report verb). Per ADR-0017 §3 and the task's own design rationale, a false reject is the
dangerous failure mode (burns a model turn, eats the retry budget) — this is not a nitpick. Retry-
budget resilience holds under real-loop re-testing; determinism holds; the guard is not hollowed out
against lazy prompts; out-of-scope items remain untouched. SWE: address the structural root cause (see
Fix note above) rather than adding more individual words, and pin regression tests against the fresh
prompts logged here (not just the round-1 five) so the next iteration is verifiably broader, not just
re-targeted.

### [SWE] 2026-07-13 — Fixes (QA round 2): the guard is restructured, not re-worded

The word lists were not the bug. **The AND-gate was.** Round 1 widened lists; round 2 found six new
holes, because a rejection predicate built as `QUESTION and SCOPE and REPORT` over three fuzzy
keyword sets COMPOUNDS its false-reject probability across three independent fuzzy tests — every
widening invites the next miss and it never converges. It also inverted the bias my own code comment
demanded (*when in doubt, ACCEPT*: a false accept restores the pre-guard status quo; a false reject
actively breaks a run by burning the retry budget). So the predicate is gone.

**The guard is now a SUBSTANCE FLOOR and nothing else** (`MIN_PROMPT_WORDS = 8`, unchanged value).
`_faults(prompt)` returns `[_TERSE]` below the floor, `[]` otherwise. That is the whole predicate.
It still catches exactly what the guard exists to catch — `"explore"`, `"explore the repo"`,
`"look around"`, `"the codebase"`, `"go look at the code"`, `"tell me about this project"`,
`"dig in"` — and it cannot false-reject on phrasing, because it never looks at phrasing.

**The three-part shape (QUESTION + SCOPE + REPORT) stays in the tool description**, untouched and
Tester-approved. That is where coaching belongs: the model reads it BEFORE it writes. It is simply
no longer a rejection predicate. `_QUESTION_WORDS` / `_SCOPE_WORDS` / `_REPORT_WORDS` / `_PATHISH_RE`
/ `_missing_parts` are DELETED (dead code), along with the now-unused `re` import.

**On the optional total-absence check (permissive OR): implemented, tested, DROPPED.** I built it
(reject only when NO signal at all — no `?`, no path token, no keyword from any of the three sets)
and ran it against the battery below. It false-rejected a genuinely well-formed 17-word brief:

```
"Tell me every place the harness shells out to git during hand back and quote the exact commands"
  -> ['no question, scope, or requested output could be identified']   # FALSE REJECT
```

No `?`, no path token, no word from ~80 signal words — and yet a perfectly good brief. Per the
orchestrator's instruction (any false reject → drop it entirely and ship the floor alone), it is
gone. The floor alone satisfies every acceptance criterion. The accepted cost is one class of false
ACCEPT: a rambling ≥8-word prompt with no investigative signal now passes (`"Go and have a proper
look at everything we have here and let me know how it went"`). That is the correct side of the
trade — a false accept only restores the pre-guard status quo.

**The nag stays truthful.** It names the floor, quotes the offending prompt, and accuses it of
nothing else; the header still states the three-part shape as the coaching the model needs to rewrite
with. No enumeration of individually-absent parts — that is what produced the lying nag in round 1.

```
No subagent was spawned: some prompts are under-specified. Every prompt must carry the QUESTION to
answer, the SCOPE to search (a directory, file, or glob pattern to start from), and WHAT THE REPORT
MUST CONTAIN.
- Prompt 1 ("explore the repo"): too terse — give more detail (aim for at least 8 words).
Rewrite the prompts above and call the agent tool again.
```

**Files modified**
- `src/decode/tools/agent.py` — guard restructured to the floor alone; word lists + path regex +
  `_missing_parts` + `import re` deleted; comment block rewritten to record WHY the AND-gate (and
  then the OR) were dropped, so nobody re-derives them; nag separator `—` → `:` (it read
  "— too terse —").
- `tests/unit/decode/tools/test_agent.py` — the regression battery (below) replaces the
  per-part-detection tests, which encoded the AND-gate semantics that no longer exist.

**Tests — the anti-whack-a-mole battery** (this is the part that stops the cycle)
- `_WELL_FORMED_PROMPTS` — 24 prompts that MUST ALL PASS: the Tester's five from round 1
  (`_ROUND_1_PROMPTS`), the Tester's eight fresh ones from round 2 (`_ROUND_2_PROMPTS`, read out of
  the log), six more of my own in varied phrasings (`_VARIED_PROMPTS`: interrogative-no-path,
  imperative-no-punctuation-no-path-no-keyword, phrasal-verb, terse-but-substantive, declarative
  statement-of-need, colloquial imperative), plus the suite's own `_PROMPT_A/B/C` + `_prompts(3)`.
  Asserted TWICE — as the predicate (`test_a_well_formed_prompt_is_never_rejected`) and THROUGH the
  real tool call with a spawn spy (`test_a_well_formed_prompt_reaches_the_fan_out_through_the_tool`),
  because QA proved the false rejects were real at the tool level, not a predicate-only artefact.
- `_LAZY_PROMPTS` — 7 prompts that MUST ALL STILL BE REJECTED (`test_a_lazy_prompt_is_rejected`),
  including the three fresh lazy ones the Tester added in round 2.
- `test_the_substance_floor_is_the_only_rejection_criterion` — floor−1 words rejected, floor words
  accepted, with no `?` / path / keyword anywhere. This is the tripwire: any future edit that gives a
  keyword set a vote again fails here.
- `test_the_nag_names_the_floor_and_never_invents_a_missing_part` — the 7-word terse-but-complete
  prompt is told only that it is terse; the problem line contains no "missing" claim.

**Acceptance criteria**
- [x] Description states shape + 3-angles push + one-element list + cap 6 — unchanged, still
      `test_the_registered_tool_description_states_the_input_contract`.
- [x] Under-specified prompt → `ModelRetry` naming index + what is wrong, no spawn —
      `test_an_under_specified_prompt_raises_model_retry_naming_its_index_and_what_is_missing`,
      `test_the_substance_guard_fires_before_agent_run_is_ever_called` (spies `agent.run` itself).
- [x] A well-specified list passes and spawns — the 24-prompt battery, at predicate AND tool level;
      `test_a_well_specified_list_passes_the_substance_guard_and_spawns`.
- [x] Deterministic, no LLM/network/file call — `test_the_guard_is_pure_and_deterministic_on_repeat`,
      `test_the_substance_guard_is_deterministic_through_the_tool`. The guard is now one `len(split())`.
- [x] All previously-green tests pass — 1560 unit, 112 integration.

**Evidence**

```
$ make format-check && make lint-check
181 files already formatted
All checks passed!

$ make pre-commit
1560 passed in 89.88s (0:01:29)         # was 1523 — +37 collected (the battery), 0 failing

$ make unit-tests
1560 passed in 90.62s (0:01:30)

$ make integration-tests
112 passed, 2 skipped in 318.47s (0:05:18)   # skips = live GEMINI/OPIK keys unset
```

Battery probe against the shipped guard (24 well-formed / 8 lazy):

```
=== WELL-FORMED (must ALL pass) ===   false rejects: 0/19    # incl. all 6 the OR still rejected
=== LAZY (must ALL be rejected) ===   false accepts: 1/8     # the rambling 17-word one — accepted
                                                             # trade, see above
```

E2E through a REAL `build_agent()` + `Runner` + `AgentTurnHandler` (FunctionModel parent, no
network) — the 103×104 retry-budget stress, with leg 3 switched to the prompts the OLD guard falsely
rejected:

```
>>> RUN COMPLETED WITHOUT ABORT (no UnexpectedModelBehavior)
>>> LEGS: leg1: 7 prompts (width cap) | leg2: lazy prompt (substance floor)
          leg3: the round-2 prompts the old guard falsely rejected | leg4: final
>>> nags received: "You asked for 7 subagents; the limit is 6 per call. …"
                   "No subagent was spawned … - Prompt 1 ("explore the repo"): too terse — give more
                    detail (aim for at least 8 words)."
>>> FOLDED AGGREGATE (leg 3): 3 labelled sections
    ## Subagent 1 — "Outline the tool registration flow in src/decode/tools/registry.py and note …"
    ## Subagent 2 — "Break down the retry budget for the bash tool across src/decode/tools/bash.py …"
    ## Subagent 3 — "Walk through the sandbox handback flow under src/decode/sandbox/handback.py …"
```

**Notes**
- Properties the Tester verified and that all still hold: the guard fires before any spawn and takes
  no semaphore slot (`_check_substance` is still the same pure synchronous call ahead of the
  `asyncio.gather`; `_spawn_child` is the only caller of `_semaphore()`); deterministic on repeat;
  the 103×104 retry-budget interaction survives two consecutive nags without
  `UnexpectedModelBehavior`; genuinely lazy prompts are still rejected.
- Guard vocabulary: `_faults` remains the predicate. `_missing_parts` no longer exists.
- The temporary e2e file used for the run above was deleted; the diff is exactly 3 files.
- `test_sandbox_teardown.py` container-reap flake: adjudicated pre-existing, ignored — and it did not
  recur in this run (112 passed).
- NOT COMMITTED — awaiting Tester re-review.

### [Tester] 2026-07-14 00:01 — Re-QA (round 3)

**Test summary**
- Format / lint: PASS — `make format-check` (181 files already formatted), `make lint-check` (all checks passed), reproduced independently.
- Unit tests: 1560 passed / 0 failed (`make pre-commit`, 91.77s) — matches SWE's claim exactly.
- Integration tests: 112 passed / 2 skipped (`make integration-tests`, 300.22s) — matches SWE's claim exactly; `test_sandbox_teardown.py` (the adjudicated pre-existing flake) passed cleanly this run, no recurrence.
- `uv lock --check`: PASS (part of `make ci`, run independently).
- Warnings: 0.

**Restructure verified clean (no collateral damage from the deletion)**
- `grep -rn "_missing_parts\|_QUESTION_WORDS\|_SCOPE_WORDS\|_REPORT_WORDS\|_PATHISH_RE"` across the whole repo → zero hits. All dead per-part machinery is gone, no orphaned references anywhere (tests, docs, or code).
- `import re` removed from `src/decode/tools/agent.py` (confirmed absent); the two `import re` hits elsewhere are in test files (`test_agent.py`'s own section-parsing regex, `test_subagents_capstone.py`) — legitimate, unrelated uses, not leftovers of the deleted guard.
- `_faults(prompt)` is now exactly: word-floor check → `[_TERSE]` or `[]`. Read at `src/decode/tools/agent.py:92-101`. No AND/OR compound logic remains.
- The three-part shape (QUESTION/SCOPE/REPORT) remains ONLY in the tool docstring (coaching) and in the fixed preamble of the `ModelRetry` message (`_check_substance`, lines 104-124) — never evaluated as a predicate. Confirmed by reading the code path: `_check_substance` calls `_faults` per prompt and only composes `_TERSE` clauses; nothing in the call chain inspects question/scope/report keywords.

**E2E adversarial pass**
- Happy path: real `build_agent()` docstring inspection + direct `agent_module.agent()` calls with well-formed prompts (`_PROMPT_A/B/C` and the SWE's 24-prompt battery) → no `ModelRetry`, labelled sections fold back correctly. PASS.
- Break path 1 (fresh THIRD battery — 10 well-formed prompts the SWE has never seen, varied phrasing: run-on declarative, "please dig up", "I'm curious whether", comparative, recursive-imperative, path-first declarative, colon-clause, "explain with citations", non-ASCII/mixed-script Cyrillic+English, "nail down whether"): all 10 passed `_faults()` with `[]` — **0/10 false rejects**. Reproduced through the real `agent()` tool call with a spawn-tracker (2 of the 10 spawned for real, `tracker.spawns == 2`, matching prompt count, sections folded correctly). PASS.
- Break path 2 (fresh lazy battery — 6 genuinely sub-floor prompts the SWE has never seen: `"explore"`, `"explore the repo"`, `"check it out"`, `"look at the code please"`, `"investigate"`, `"what's going on here"`): all 6 correctly rejected with `[_TERSE]` — **0/6 false accepts**. The floor is NOT hollowed out; `MIN_PROMPT_WORDS = 8` unchanged from round 1/2. PASS.
- Break path 3 (nag truthfulness — the round-1 lying-nag regression case, re-verified fresh): `_check_substance(["Trace gate.py's ASK path and report evidence."])` (7 words, all 3 parts genuinely present) raised: `- Prompt 1 ("Trace gate.py's ASK path and report evidence."): too terse — give more detail (aim for at least 8 words).` — names the index (1), states the true fault (terse, floor of 8), and invents no false claim about a missing question/scope/report. PASS.
- Break path 4 (CRITICAL — 103×104 retry-budget interaction, driven through a REAL `build_agent()` + `Runner` + `AgentTurnHandler` loop, fresh script not reusing any prior round's file, leg 3 using round-3's own fresh prompts): legs = `['leg1: 8 prompts (width cap)', 'leg2: lazy prompt (substance floor)', 'leg3: fresh well-formed prompts (round 3, never seen)', 'final']`. Run completed without `UnexpectedModelBehavior`; the tool ultimately returned one labelled section. Confirms `AGENT_TOOL_RETRIES = 3` still correctly absorbs a width-cap nag + a substance nag before success — this holds identically under the restructured floor-only guard. PASS.
- Break path 5 (guard fires before spawn / no semaphore slot): architecturally unchanged — `_check_substance` is still the same pure synchronous call ahead of `asyncio.gather`, and `_spawn_child` remains the only caller of `_semaphore()`. Backed by `test_the_substance_guard_fires_before_agent_run_is_ever_called` (spies `agent.run` itself, `run.assert_not_called()` passes) and independently reproduced manually with `mocker.patch.object(agent_module, "_require_main_agent")` on a lazy prompt → `m.assert_not_called()` passes. PASS.
- Break path 6 (determinism): `_faults()` called on the same 16 prompts (10 fresh well-formed + 6 fresh lazy) — identical results confirmed by inspection of the pure `len(prompt.split())` implementation (no state, no I/O); existing `test_the_guard_is_pure_and_deterministic_on_repeat` and `test_the_substance_guard_is_deterministic_through_the_tool` both pass. PASS.

**Acceptance criteria** — judged against the restructured design (floor-only guard), per the orchestrator's ruling
- [x] PASS — Description states shape + 3-angles push + one-element list + cap 6 — read the REGISTERED tool's `.description` directly off a real `build_agent()`: contains "question"/"scope"/"report" (lowered), the literal `"3 DISTINCT angles"`, `"one-element list"`, and the literal `6`; `MAX_FANOUT_PROMPTS` does NOT appear in the description. `test_the_registered_tool_description_states_the_input_contract` passes. Confirms this AC survived the deletion untouched, as claimed.
- [x] PASS — Under-specified prompt raises `ModelRetry` naming the offending prompt index AND what is wrong; no child spawns — verified manually (`- Prompt 1 ("explore"): too terse — give more detail...`) and via `test_an_under_specified_prompt_raises_model_retry_naming_its_index_and_what_is_missing` + `test_the_substance_guard_fires_before_agent_run_is_ever_called`. Note: under the restructured design "what is missing" is now truthfully "too terse" (the one real fault) rather than an enumerated list of absent parts — this is the correct, intentional outcome of dropping the AND-gate (per the orchestrator's explicit direction to judge the guard against what it now claims to be).
- [x] PASS — A well-specified list of prompts passes the guard and spawns normally — verified via the SWE's 24-prompt battery (both at predicate and real-tool level, in the suite) AND my own fresh third battery of 10 (0/10 false rejects, reproduced through the real tool call with a spawn tracker). This is the criterion that failed twice before; it now holds against three independent adversarial batteries (SWE's own, mine round 1, mine round 2) plus a fourth I built fresh this round.
- [x] PASS — Deterministic, no LLM/network/file call — `_faults` is one `len(prompt.split())` comparison; `test_the_guard_is_pure_and_deterministic_on_repeat`, `test_the_substance_floor_is_the_only_rejection_criterion`, `test_the_substance_guard_is_deterministic_through_the_tool` all pass; code reads pure string ops only, confirmed by reading `src/decode/tools/agent.py:92-101`.
- [x] PASS — All previously-green tests pass; `make ci` green — 1560 unit passed (0 failed), 112 integration passed / 2 skipped (0 failed), `uv lock --check` clean, `make format-check` + `make lint-check` clean, all reproduced independently in this round.

**Evidence**

```
$ make format-check && make lint-check
181 files already formatted
All checks passed!

$ make pre-commit
1560 passed in 91.77s (0:01:31)

$ make integration-tests
112 passed, 2 skipped in 300.22s (0:05:00)

$ uv lock --check
Resolved 155 packages in 2ms
```

```
>>> fresh THIRD battery (10 well-formed prompts, never seen by the SWE), through _faults():
[PASS] "Investigate why the retry counter resets between turns in src/decode/agent/loop.py..." -> []
[PASS] "Please dig up how the priority gate orders queued messages..." -> []
[PASS] "I'm curious whether the LSP client reconnects after a crash..." -> []
[PASS] "Compare how docker and modal backends implement teardown..." -> []
[PASS] "Recursively hunt for every place SecretStr is unwrapped..." -> []
[PASS] "src/decode/context/ — how does compaction pick which messages to drop..." -> []
[PASS] "Track down the origin of the harness_home concept..." -> []
[PASS] "Explain, with citations, how the TUI decides when to show a spinner..." -> []
[PASS] "У меня вопрос: где обрабатывается ask_user tool? ..." -> []          # mixed-script
[PASS] "Nail down whether skill dispatch is case-sensitive..." -> []
false rejects: 0/10

>>> fresh lazy battery (6 sub-floor prompts, never seen by the SWE):
"explore" -> ['too terse...'], "explore the repo" -> ['too terse...'],
"check it out" -> ['too terse...'], "look at the code please" -> ['too terse...'],
"investigate" -> ['too terse...'], "what's going on here" -> ['too terse...']
false accepts: 0/6

>>> through the real tool call (spawn tracker, not just the predicate):
SPAWNS: 2   SECTIONS OK: 2   (2 of the fresh well-formed prompts, both spawned for real)

>>> nag truthfulness re-check (round-1 regression case):
"- Prompt 1 (\"Trace gate.py's ASK path and report evidence.\"): too terse — give more detail
  (aim for at least 8 words)." — names the floor, invents nothing about question/scope/report.
```

```
>>> real-loop retry-budget re-stress (fresh script, real build_agent()+Runner+AgentTurnHandler,
    round-3's own prompts, not reused from prior rounds):
LEGS: ['leg1: 8 prompts (width cap)', 'leg2: lazy prompt (substance floor)',
       'leg3: fresh well-formed prompts (round 3, never seen)', 'final']
TOOL RETURNS COUNT: 1
 -> ## Subagent 1 — "Investigate why the retry counter resets between turns..."
RUN COMPLETED WITHOUT ABORT (no UnexpectedModelBehavior)
```

**Other issues found**
- None new. The round-2 note about `code-review` plugin not being invokable against uncommitted, no-PR local work still applies (unchanged environment); not blocking.
- The accepted trade (a rambling ≥8-word prompt with zero investigative signal now passes) is real, intentional, and explicitly documented in the code comment and the SWE's log — consistent with the orchestrator's ruling that a false accept restores the pre-guard status quo and is not a defect. Not blocking.

**VERDICT: PASS**

The restructure is sound: the SWE correctly diagnosed that round 1 and round 2 both failed for the same underlying reason — a compound AND-gate (and then a permissive OR) over fuzzy keyword sets cannot converge against real phrasing variety — and replaced it with a single deterministic word-floor, exactly matching the orchestrator's directive. Verified independently across four adversarial batteries total (the SWE's own 24-prompt battery, both prior QA rounds' prompts now pinned as regressions, and a fresh third battery of 10 well-formed + 6 lazy prompts this round): zero false rejects, zero false accepts on genuinely lazy input. The guard still does its job — "explore", "explore the repo", and every fresh sub-floor variant I tried are rejected; it has not been hollowed out. The nag is truthful (names the floor, invents nothing). The 103×104 retry-budget interaction holds under a fresh real-loop stress test with leg 3 exercising round-3's own never-before-seen prompts — no `UnexpectedModelBehavior`. The tool description's three-part shape + "3 DISTINCT angles" + cap-of-6 survived the deletion untouched (AC 1 intact). The deletion is clean: no dead code, no orphaned constants (`_QUESTION_WORDS`/`_SCOPE_WORDS`/`_REPORT_WORDS`/`_PATHISH_RE`/`_missing_parts` and the `re` import are fully gone, confirmed by a whole-repo grep), no unused imports. Full suite green (1560 unit, 112 integration / 2 skipped, `uv lock --check`, format, lint), zero warnings. Out-of-scope items (105/106/107, new Settings field, `.env.example`) remain untouched — `git diff --stat` limited to exactly the two expected files (`src/decode/tools/agent.py`, its unit test) plus the task file. Hand off to PA for acceptance review.

### [PA] 2026-07-14 — Acceptance Review

**VERDICT: ACCEPT**

Reviewed as part of the subagent-fanout feature acceptance (PR #33). The AC drift from the original spec ("names what is missing") to the shipped floor-only guard ("too terse") is properly recorded — ADR-0017's dated 2026-07-14 amendment (§3 + the diagram node) matches `src/decode/tools/agent.py:91-131` verbatim, and the false-accept bias is the right product call: a lying nag and a 75% false-reject rate (both empirically demonstrated in this log) are worse user experiences than one weak child report that the 106 output validator then catches anyway. Tool docstring (`agent.py:304-316`) carries the three-part coaching + "3 DISTINCT angles" push + cap of 6, verified against the registered description. Not a quiet drop — an adjudicated, documented redesign.
