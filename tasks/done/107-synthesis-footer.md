---
id: 107
feature: subagent-fanout
status: done
---

# Synthesis Footer on the aggregated agent result

Depends on: 103. Implements ADR-0017 §9.

## Scope

Append a harness-owned instruction line to the aggregated `agent` tool result telling the parent
model how to synthesize — just-in-time (costs nothing on turns with no Fan-out), lives in ONE
place, cannot be forgotten by a future persona author.

**`src/decode/tools/agent.py`**

- A module-constant footer, appended after the last section on EVERY `agent` tool result
  (one-element lists included). It instructs the parent to compile the N Subagent Reports into
  ONE answer: prose PLUS a text-based diagram of the structure it found — ASCII/box-drawing by
  default, Mermaid only when the structure is a genuine graph (the TUI is Rich in a terminal —
  Mermaid renders as raw source there).
- The footer is appended AFTER per-child truncation — it never eats any child's byte budget
  (total result ≈ 16 KB of reports + headings + footer).
- The parent personas (`build.md` / `plan.md` / `code-reviewer.md`) get NO synthesis wording —
  decision-locked; a guard test keeps it that way.

**Tests** (`tests/unit/decode/tools/test_agent.py`)

- Footer present on a 1-wide and an N-wide fold; positioned after the last section.
- Footer present even when a section carries a failure note.
- Children's per-child budget unchanged by the footer (byte assertions from 103 still hold on
  section bodies).
- Persona guard: no `agents/builtin/*.md` body contains the footer's synthesis instruction
  (stable-marker grep test).

## Acceptance Criteria

- [x] Every `agent` tool result ends with the Synthesis Footer: compile into one answer, prose + text diagram, ASCII default / Mermaid only for genuine graphs.
- [x] The footer never reduces any child's byte budget (appended post-truncation, pinned by test).
- [x] No persona file carries synthesis/diagram instructions (guard test).
- [x] `make ci` green.

## Out of scope

- Rendering diagrams in the TUI (the footer instructs the MODEL; Rich renders its text as-is).
- Making the footer conditional on width (always appended — decision locked).

## Log

### [SWE] 2026-07-14 14:05 — Implementation

**Files modified**
- `src/decode/tools/agent.py` — the ADR-0017 §9 Synthesis Footer: a public module constant
  `SYNTHESIS_FOOTER` (no Settings field, no `.env.example` entry) appended in `agent()` AFTER the
  labelled fold — i.e. after every child's report has already been truncated to its own share of the
  shared budget — on EVERY result, one-element lists included. Module docstring extended to §9.
- `tests/unit/decode/tools/test_agent.py` — the §9 block (5 tests: the constant's content contract,
  the 1-wide fold, the N-wide fold, a degraded fold carrying both §7 failure notes, and the
  byte-budget non-interference test). `_sections()` now strips the footer before parsing (asserting
  its presence), so every pre-existing fold/budget assertion stays about a CHILD's report alone —
  and every one of them is now also a footer test.
- `tests/unit/decode/agents/test_loader.py` — the persona guard, generalized from explore-only (105)
  to all four builtins, parametrized: no `agents/builtin/*.md` body may carry `synthes` / `diagram` /
  `mermaid` / `ascii` / `box-drawing`, nor the footer verbatim.
- `tests/integration/test_subagents_capstone.py` — new §9 slice
  (`test_the_synthesis_footer_reaches_the_parent_model_after_the_last_section`): asserts on the
  `ToolReturnPart` content handed BACK to the parent model through the real `Runner`, not merely on
  the tool's return value. `_section_bodies()` strips the footer for the same reason as above.

**Tests**
- Unit: 1583 passing, 0 failing (`make pre-commit`); `test_agent.py` = 111, `test_loader.py` = 26.
- Integration: `make ci` → 1697 passed, 2 skipped (both live-key-gated smokes: GEMINI/OPIK unset).

**Acceptance criteria**
- [x] Every result ends with the footer (compile→one answer, prose + text diagram, ASCII default /
      Mermaid only for genuine graphs) — `test_the_synthesis_footer_is_a_module_constant_stating_the_whole_contract`,
      `test_a_one_wide_fold_still_carries_the_footer_after_its_only_section`,
      `test_an_n_wide_fold_carries_exactly_one_footer_after_the_last_section`,
      `test_the_footer_is_appended_even_when_a_section_carries_a_failure_note`, capstone
      `test_the_synthesis_footer_reaches_the_parent_model_after_the_last_section`.
- [x] The footer never reduces any child's budget — `test_the_footer_never_eats_a_childs_byte_budget`
      (all three bodies still `<= max_bytes // 3`, all three IDENTICAL full-budget heads, aggregate
      strictly LARGER than `subagent_result_max_bytes`); the 103 byte assertions
      (`test_each_child_report_is_truncated_to_the_shared_byte_budget`,
      `test_a_single_child_still_gets_the_whole_byte_budget`, capstone
      `test_child_report_is_truncated_to_the_byte_cap_through_the_fold`) still hold unchanged.
- [x] No persona carries synthesis/diagram wording —
      `test_no_builtin_persona_body_carries_the_synthesis_instruction[build|plan|explore|code-reviewer]`.
- [x] `make ci` green.

**Evidence**
```
$ make ci
================= 1697 passed, 2 skipped in 386.38s (0:06:26) ==================

$ uv run python scratchpad/e2e_107.py   # real build_agent + real agent tool, network boundary only faked
  4: ## Subagent 1 — "How does the truncate helper cap tool output? …"
112: ## Subagent 2 — "How does the permission gate decide allow/ask/deny? …"
114: The subagent returned no usable report.        # a twice-bad child (§7) — still gets its footer
116: ## Subagent 3 — "How does the agent tool fan out children? …"
---
COMPILE the subagent reports above into ONE answer for the user — do not hand back the reports one
by one, and do not answer from a single report alone. …
1. PROSE — what the structure is and how it works.
2. A TEXT DIAGRAM of that structure, in ASCII / box-drawing characters. Your output is rendered in a
   TERMINAL, so the diagram must read as a diagram in plain monospaced text. Use a Mermaid block
   ONLY when what you found is a genuine graph … — a terminal renders Mermaid as RAW SOURCE …
==============================================================================
per-child budget      : 5333 bytes     # unchanged by the footer
aggregate total       : 11823 bytes
footer                : 747 bytes, appended on TOP
ends with the footer  : True
footer occurrences    : 1
```

**Notes**
- **Why the two test-side section parsers now strip the footer.** The footer trails the last section
  but belongs to no section, so a naive parser would fold it into the LAST child's body and quietly
  break the §6 byte assertions (that body would measure `report + footer`). Stripping it first —
  with an `assert aggregate.endswith(SYNTHESIS_FOOTER)` — keeps those assertions about the child's
  report alone AND turns every existing fold test into an implicit "the footer is there, after
  everything" test. No assertion was loosened; the byte checks from 103 are byte-identical.
- **Public constant, not `_`-prefixed** (unlike `_RETRY_NUDGE`): it is part of the tool's model-facing
  output contract and two test modules assert against it, including the persona guard, which compares
  persona bodies to the real constant rather than to a copy that could drift.
- **The persona guard is now parametrized over all four builtins**, subsuming 105's explore-only ban.
  Explore was the only persona that could be tempted to do its parent's job; the three PARENTS are the
  ones that would pay the footer's ~750 bytes on every turn of every run if the wording leaked into
  them. Both failure modes are now pinned.
- Trade-off named: the footer is a flat ~747 bytes on every `agent` result, at any width — it is NOT
  conditional on width (decision-locked, §9). At the 16 KB fold that is ~4.5% overhead, paid only on
  turns that actually fan out.
- Out of scope, verified absent: no TUI/render change (Rich renders the model's diagram text as-is),
  no Settings field, no `.env.example` entry, no persona file touched.

### [Tester] 2026-07-14 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit` = 1583
  passed, 0 failed)
- Unit tests: 1583 passed / 0 failed (`test_agent.py` = 111, `test_loader.py` matches claim)
- Integration tests: 114 passed / 2 skipped (both live-key-gated: `GEMINI_API_KEY`/`OPIK_API_KEY`
  unset in this environment) — combined with unit = 1697 passed / 2 skipped, matching the SWE's
  `make ci` claim exactly. `uv lock --check` also clean.
- Warnings: 0 (`filterwarnings = ["error"]` in `pyproject.toml` — any warning would already be a
  failure, and the suite is fully green).

**E2E adversarial pass**
- Happy path: called the real `agent()` tool (mocking only the network-facing `_require_main_agent`
  seam, exactly like the SWE's own approach) with a 1-wide and a 3-wide fan-out → aggregate ends
  with `SYNTHESIS_FOOTER` exactly once, positioned strictly after the last `## Subagent N` section.
  PASS.
- Break path 1 (mutation test — does a footer-eats-budget bug get caught?): temporarily patched
  `child_max_bytes = (settings.subagent_result_max_bytes - 30) // len(prompts)` in
  `src/decode/tools/agent.py` (simulating the footer silently stealing 10 bytes/child in a 3-wide
  fold) and re-ran `tests/unit/decode/tools/test_agent.py` → **all 111 tests still passed**. The
  `test_the_footer_never_eats_a_childs_byte_budget` test only asserts `<= per_child` (an upper
  bound), never exact equality, so a modest silent budget shrink would slip through undetected.
  Reverted the mutation immediately. Verdict on the ACTUAL code: PASS regardless — I traced the real
  call graph (`src/decode/tools/agent.py:335,392`) and confirmed `child_max_bytes` is computed and
  consumed by `truncate()` inside `_spawn_child` with **zero reference** to `SYNTHESIS_FOOTER`
  anywhere in that path; the footer is concatenated only once, entirely outside the per-child
  truncation call, so this class of bug is structurally impossible in this implementation, not just
  untested. Flagging the test-rigor gap below as a note for future regression-proofing, not a FAIL.
- Break path 2 (persona-guard mutation, in a scratch copy, NOT the repo): rsync'd the repo (minus
  `.venv`, which I symlinked back in) to scratch, appended synthesis/diagram wording to
  `src/decode/agents/builtin/build.md`, and ran
  `test_no_builtin_persona_body_carries_the_synthesis_instruction[build]` → **failed as expected**
  (`AssertionError: 'diagram' belongs to the Synthesis Footer, not the build persona`). Confirms the
  guard is a real tripwire, not a no-op. Scratch copy deleted afterward; repo untouched. PASS.
- Break path 3 (structural/boundary inputs): empty `prompts=[]` → clean `ModelRetry` ("needs at
  least one exploration prompt"), no footer leaked, no crash. `prompts` list of 7 (over
  `MAX_FANOUT_PROMPTS=6`) → clean `ModelRetry` naming the limit, no spawn, no footer leaked. All
  three children twice-bad (every child gives up) → footer still present, well-formed, at the very
  end after three `_NO_USABLE_REPORT_NOTE` sections — full graceful degradation, no crash even under
  100% child failure. PASS.
- Break path 4 (hostile/adversarial content — a child's OWN report echoes the footer text verbatim,
  a fully plausible real scenario since an Explore child can `read` `agent.py`'s own source and quote
  `SYNTHESIS_FOOTER` as file:line evidence): aggregate still `endswith(SYNTHESIS_FOOTER)` and still
  parses correctly (`_sections()`'s exact-suffix strip is unaffected by an earlier occurrence of the
  same substring inside a child's body); `count()` is 2 in that pathological case (harness only
  guarantees "ends with", not "occurs exactly once" — the tests' `count==1` assertions hold only
  because the *scripted* test reports happen not to contain the footer text). Not a functional bug —
  parsing and budget accounting are both robust to it. PASS with note.
- Break path 5 (concurrency): two concurrent `agent()` calls on the same event loop
  (`asyncio.gather`), one 1-wide and one 2-wide, with distinct prompts → no cross-talk (each result
  contains exactly its own prompts, each ends with exactly one footer). PASS.
- Prompt-quality judgement (footer text itself, read as a prompt, `src/decode/tools/agent.py:214`):
  explicitly asks for BOTH prose and a text diagram, states ASCII/box-drawing as the DEFAULT, states
  Mermaid ONLY for a genuine graph and explains WHY (terminal renders Mermaid as raw source, not a
  picture) — a concrete, falsifiable instruction a competent model should follow. Well-formed.
- Live-model e2e: **not available** — no `GEMINI_API_KEY`, no `OPENROUTER` key, no `.env` in this
  environment (`env | grep -iE "GEMINI|OPENROUTER|MODAL"` empty, `.env` absent). Could not
  independently confirm a real model actually produces a synthesized prose+diagram answer. Relied
  instead on (a) the capstone integration test asserting on the actual `ToolReturnPart` the real
  `Runner` hands back to the parent model (`tests/integration/test_subagents_capstone.py::test_the_synthesis_footer_reaches_the_parent_model_after_the_last_section`,
  which passed), and (b) direct reading of the footer's instruction quality above. This is a real
  gap in proof-of-concept (the harness delivers the instruction correctly; whether a live model
  *obeys* it is unverified here) — noted for awareness, not a FAIL, since it is outside what this
  environment can test and the SWE's own claimed evidence (pasted `scratchpad/e2e_107.py` transcript
  in the Log above) is consistent with correct delivery.

**Acceptance criteria**
- [x] PASS — Every `agent` tool result ends with the Synthesis Footer (compile→one answer, prose +
      text diagram, ASCII default / Mermaid only for genuine graphs) — read
      `src/decode/tools/agent.py:214-231` (the constant's text) and `:340-354` (`fold + SYNTHESIS_FOOTER`,
      unconditional); `tests/unit/decode/tools/test_agent.py::test_the_synthesis_footer_is_a_module_constant_stating_the_whole_contract`,
      `::test_a_one_wide_fold_still_carries_the_footer_after_its_only_section`,
      `::test_an_n_wide_fold_carries_exactly_one_footer_after_the_last_section` all pass; independently
      reproduced via my own scratch probes on 1-wide/3-wide/all-fail folds (above).
- [x] PASS — The footer never reduces any child's byte budget (appended post-truncation, pinned by
      test) — confirmed by reading the call graph: `child_max_bytes` (agent.py:335) is threaded into
      `truncate()` inside `_spawn_child` (agent.py:392) with no reference to `SYNTHESIS_FOOTER`
      anywhere in that path, and the footer is concatenated exactly once, after the fold is fully
      built (agent.py:354). `test_the_footer_never_eats_a_childs_byte_budget` passes; 103's byte
      tests (`test_each_child_report_is_truncated_to_the_shared_byte_budget`,
      `test_a_single_child_still_gets_the_whole_byte_budget`) unchanged and still pass. Note: these
      assertions are upper-bound (`<=`), not exact-equality — see Break path 1 above for the
      resulting (harmless, given the structural proof) test-rigor gap.
- [x] PASS — No persona file carries synthesis/diagram instructions (guard test) —
      `test_no_builtin_persona_body_carries_the_synthesis_instruction[build|plan|explore|code-reviewer]`
      all pass against the real 4 builtins; guard proven to be a real tripwire via the scratch-copy
      mutation in Break path 2 above; `git diff --stat -- src/decode/agents/builtin/` is empty
      (no persona file touched by this task).
- [x] PASS — `make ci` green — reproduced as `make format-check` + `make lint-check` +
      `make pre-commit` (1583 unit) + `make integration-tests` (114 passed, 2 skipped) = 1697
      passed / 2 skipped, matching the SWE's claim exactly; `uv lock --check` also clean.

**Evidence**
```
$ make pre-commit
======================= 1583 passed in 91.33s (0:01:31) ========================

$ make integration-tests
================== 114 passed, 2 skipped in 305.23s (0:05:05) ==================
  (1583 + 114 = 1697 passed, 2 skipped — matches `make ci` claim)

$ uv lock --check
Resolved 155 packages in 2ms
```

Mutation-test excerpt (Break path 1, reverted immediately after):
```
# src/decode/tools/agent.py:335, temporarily changed to:
child_max_bytes = (settings.subagent_result_max_bytes - 30) // len(prompts)
$ uv run pytest tests/unit/decode/tools/test_agent.py -q
111 passed in 5.01s   # a 10-byte-per-child theft is NOT caught by the current assertions
```

Persona-guard mutation excerpt (Break path 2, scratch copy only):
```
$ echo 'Compile subagent reports into one answer with prose and a diagram, ...' >> build.md
$ uv run pytest tests/unit/decode/agents/test_loader.py -k synthesis -q
FAILED [...] AssertionError: 'diagram' belongs to the Synthesis Footer, not the build persona
1 failed, 3 passed, 22 deselected
```

**Other issues found**
- `test_the_footer_never_eats_a_childs_byte_budget` and the pre-existing 103 byte tests it reuses
  assert an upper bound (`len(body) <= per_child`) rather than exact equality against a
  known-correct `truncate()` call. This means a *small* silent budget theft would not turn any test
  red (see Break path 1). Not a functional defect — the current implementation has no code path that
  could cause such theft — but worth a follow-up: assert `body == truncate(big, max_lines=..., max_bytes=per_child).text`
  for a bullet-proof regression guard, rather than relying on the structural argument holding forever.
- Not a defect, just an observation: if a child's own report happens to contain the exact footer
  text verbatim (plausible since Explore children can `read` `agent.py`'s source), `SYNTHESIS_FOOTER`
  occurs more than once in the aggregate. Harmless today (`endswith` + fixed-length strip both still
  work), but any future code relying on `count(SYNTHESIS_FOOTER) == 1` as an invariant (rather than
  `endswith`) would be wrong to do so.
- `code-review` plugin (enabled in `.claude/settings.json`) is PR-diff-oriented
  (`gh pr diff`/`gh pr view`) and has no target on this uncommitted branch (no open PR yet) — not
  invoked; will apply naturally once the PR Reviewer stage opens a PR.
- `test_docker_executor.py` / `test_sandbox_teardown.py`: ran clean in this session (no flakes
  observed), consistent with the hand-off note that any prior failures there were pre-existing
  Docker-daemon flakes unrelated to this task.

**VERDICT: PASS**

### [PA] 2026-07-14 — Acceptance Review

**VERDICT: ACCEPT**

Reviewed as part of the subagent-fanout feature acceptance (PR #33). `SYNTHESIS_FOOTER` (`src/decode/tools/agent.py:214-226`) appended post-truncation on every fold (`:353`) — this is what turns the user's "aggregate their result" from a wall of concatenated reports into one compiled answer, and 108's live runs prove a real model obeys it (prose + ASCII diagram, behaviorally asserted by `_looks_like_a_text_diagram` in the live smoke). ASCII-default/Mermaid-only-for-graphs is the right call for a Rich terminal. Persona guard covers all four builtins.
