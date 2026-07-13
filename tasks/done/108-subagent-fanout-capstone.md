---
id: 108
feature: subagent-fanout
status: done
---

# Capstone: resilient parallel fan-out end-to-end + docs verification

Depends on: 103, 104, 105, 106, 107. Proves ADR-0017 composed, through the full real stack.

## Scope

Extend `tests/integration/test_subagents_capstone.py` (same style: real `build_agent` + `Runner`
+ `AgentTurnHandler` + gate + `render_event`, one scripted `FunctionModel` driving parent AND
children; no network except the skipif-gated live smoke).

**New composed scenarios**

- **The resilience matrix in one turn** — one `agent` call, 4 well-formed prompts; children
  scripted as: A good (real read-only tool + report), B empty-first → nudged retry → good,
  C text-only (zero tool calls) → retry → still text-only → failure note, D good. Assert: ONE
  `agent` ToolCallStarted on the sink; the aggregate carries 4 sections in prompt order; B's
  section = its retry report (B spawned exactly twice); C's section = the failure note; per-child
  budget division holds (patched small setting); Synthesis Footer present after the sections;
  zero `PermissionRequested`; children silent-until-done; parent usage gauge excludes children;
  session log + `--resume` replay carry the single spawn + aggregate only.
- **Width-cap round-trip through the loop** — parent first emits 7 prompts → the `ModelRetry` nag
  reaches the model → the scripted model re-emits 3 consolidated prompts → the turn completes
  green (also proves 103's raised per-tool retries budget end-to-end).
- **Substance-guard round-trip** — parent first emits one under-specified prompt → nag names the
  index + missing parts → model rewrites → children spawn.

**Live Gemini smoke** (existing `test_live_gemini_fanout_smoke`, still skipif-gated on
`GEMINI_API_KEY`) — updated: the prompt asks for a broad multi-angle exploration of named files;
assert presence-only: ≥1 `agent` call whose args carry a `prompts` list, an aggregated result
containing `## Subagent` heading(s) and the footer, no permission prompt.

**Docs verification (this feature's drift check — read, verify, fix if drifted)**

- `docs/glossary.md`: **Fan-out**, **Subagent Report**, **Synthesis Footer** rows + the amended
  **Agent tool** / **Subagent** rows match shipped behavior (written in the grooming commit —
  verify, don't duplicate).
- `docs/adr/0013-explore-subagents.md` header carries the dated amendment pointing at ADR-0017;
  `docs/adr/0017-*.md` present and matches what shipped.
- `.claude/skills/manual-e2e-qa/SKILL.md` subagents row (updated in 103) matches final shipped
  behavior — one `agent(prompts=[…])` call, width cap, budget split, retry-once, failure notes,
  footer.

## Acceptance Criteria

- [x] The resilience-matrix test passes: 4 sections in prompt order, retried child folded, failed child noted, budget split, footer present, one tool call on the sink, no prompts, silent children, parent-only usage, resume-clean log.
- [x] The 7-prompt width-cap round-trip completes green through the real loop (nag → consolidate → run), proving the raised tool-retries budget.
- [x] The substance-guard round-trip completes green through the real loop.
- [x] The live Gemini smoke (skipif-gated) asserts the new shape presence-only and stays green when the key is set.
- [x] Glossary rows, ADR-0013 amendment header, ADR-0017, and the manual-e2e-qa row all match shipped behavior (verified by reading; fixed here if drifted).
- [x] `make ci` green.

## Out of scope

- New runtime behavior — this task adds proof and verifies docs, not features.
- A deployed-stack headless subagent replay proof (ADR-0013 open seam, unchanged).

## Log

### [SWE] 2026-07-14 18:40 — Implementation

**Files modified**
- `tests/integration/test_subagents_capstone.py` — the three composed scenarios (the resilience matrix
  in one turn; the width-cap round-trip; the substance-guard round-trip), the live smoke reshaped to
  the broad multi-angle prompt + a **behavioral** footer proof, and the byte assertion tightened from
  `<=` to exact equality. New helpers: `_budgeted`, `_retry_prompts`, `_final_answer`, `_call_args`,
  `_looks_like_a_text_diagram`, `_SECTION_HEADING_RE`.
- `tests/unit/decode/tools/test_agent.py` — every byte assertion (103's two + 107's footer test +
  the retry-budget test) tightened from an upper bound to EXACT equality via `_budgeted()`, plus a new
  `test_each_child_is_truncated_at_exactly_its_share_of_the_budget` that spies `truncate()` and pins the
  `max_bytes` ARGUMENT — the one assertion that catches a sub-line-sized budget theft.
- `docs/adr/0017-resilient-parallel-subagent-fanout.md` — **DRIFT FIXED.** §3 recorded the substance
  guard as "names … what is missing" with the heuristic left open; what shipped is a bare word floor
  (`MIN_PROMPT_WORDS = 8`), single criterion, biased to accept, with the three-part shape as *coaching*
  in the tool description rather than a rejection predicate. §3 now records that, with the reason (the
  three-signal AND-gate false-rejected 6/8 realistic briefs). Dated `Amended:` header line + the
  Mermaid guard node now reads "any prompt under 8 words?". Shipped `AGENT_TOOL_RETRIES = 3` pinned.
- `.claude/skills/manual-e2e-qa/SKILL.md` — **DRIFT FIXED.** The subagents row described the fan-out,
  width cap, budget split and the *raised* failure note, but predated 104/106/107: no substance guard,
  no bad-report retry-once/give-up note, no Synthesis Footer. All three added, with what the human
  should actually SEE (a nag naming `Prompt i ("…")` as too terse; a `The subagent returned no usable
  report.` section; a final answer carrying a text diagram).
- `docs/glossary.md` — verified, NOT changed: the Fan-out / Subagent Report / Synthesis Footer rows and
  the amended Agent tool / Subagent rows all match shipped behavior.
- `docs/adr/0013-explore-subagents.md` — verified, NOT changed: the dated `Amended: 2026-07-13` header
  pointing at ADR-0017 is present and correct.

**Tests**
- Unit: 1584 passing, 0 failing (`test_agent.py` = 112).
- Integration: 117 passing, 2 skipped (the two live-key-gated smokes, with no `.env` present).
- `make ci`: **1701 passed, 2 skipped** in 6:29.
- Live Gemini (real key, real network, run twice): `test_live_gemini_fanout_smoke` PASSED both times.

**Acceptance criteria**
- [x] Resilience matrix — `test_the_resilience_matrix_folds_four_children_in_one_turn`: ONE `agent`
      ToolCallStarted on the sink; 4 sections in prompt order; B spawned exactly twice (its section is
      the RETRY's report, the empty first attempt leaves no trace); C's section is
      `_NO_USABLE_REPORT_NOTE` and its memory-only text never reaches the parent; each healthy body
      `== _budgeted(report, per_child=400 // 4)`; footer after the last section; zero
      `PermissionRequested` and both resolvers untouched; `tool_call_names() == {"agent"}`; no spawn
      threaded `usage=`; `session_log.load()` replays byte-for-byte with ONE `agent` call and zero
      `read` calls. Exactly 6 spawns — 4 children + B's retry + C's retry, never a 3rd attempt.
- [x] Width-cap round-trip — `test_the_width_cap_nag_round_trips_through_the_loop_and_the_turn_completes`.
      Scripted with the model STUBBORN ONCE (7 → nag → 7 → nag → 3 → green) **on purpose**: pydantic-ai
      carries a failing tool's retry count across run steps (`tool_manager.py:120-130,177-181`) and its
      default budget is 1, so a SINGLE nag would pass even at the default and would prove nothing about
      103's raised budget. Two consecutive nags do. Verified by mutation: with `AGENT_TOOL_RETRIES = 1`
      the test goes RED with `UnexpectedModelBehavior: Tool 'agent' exceeded max retries count of 1`.
- [x] Substance-guard round-trip — `test_the_substance_nag_round_trips_and_the_rewritten_prompts_spawn_children`:
      `["explore the repo"]` → nothing spawns → the nag reaches the model quoting `Prompt 1 ("explore
      the repo")` + `_TERSE` → the rewritten prompts fan out → green.
- [x] Live Gemini smoke — reshaped to a broad 3-angle exploration of named files; presence-only:
      ≥1 `agent` call whose `ToolCallPart.args` carries a `prompts` LIST, an aggregate with `## Subagent`
      heading(s) ending in the footer, zero permission prompts. **Plus the Tester's addition (1): a
      behavioral proof the footer WORKS** — the parent's FINAL ANSWER must carry a text diagram
      (`_looks_like_a_text_diagram`: mermaid fence / ≥4 box-drawing glyphs / ≥3 ASCII connector lines).
      Ran live twice: PASSED. Gemini obeyed the footer — prose + an ASCII diagram (transcript in the
      SWE report).
- [x] Docs — glossary + ADR-0013 header verified clean; ADR-0017 §3 and the manual-e2e-qa row were
      DRIFTED and are fixed here (see Files modified).
- [x] `make ci` green — 1701 passed, 2 skipped.

**Evidence**
```
$ make ci
================= 1701 passed, 2 skipped in 389.82s (0:06:29) ==================

$ uv run pytest tests/integration/test_subagents_capstone.py -q -k live   # real GEMINI_API_KEY
1 passed, 12 deselected in 26.43s      (and again: 1 passed in 24.43s)

# Tester addition (2) — the tightened byte guard now has teeth. Mutation:
#   child_max_bytes = (settings.subagent_result_max_bytes - 30) // len(prompts)
$ uv run pytest tests/unit/decode/tools/test_agent.py -q
2 failed, 110 passed        # was: 111 passed (the theft slipped through the old `<=` assertions)

# The raised retry budget is genuinely proven. Mutation: AGENT_TOOL_RETRIES = 1
$ uv run pytest tests/integration/test_subagents_capstone.py -k width_cap
FAILED ... pydantic_ai.exceptions.UnexpectedModelBehavior: Tool 'agent' exceeded max retries count of 1
```

**Notes**
- **The footer WORKS against a real model** (the thing no hermetic test can prove). Live Gemini made ONE
  `agent(prompts=[3 angles])` call, the children read the named files, and the final answer was a single
  synthesis with file:line evidence **plus an ASCII box diagram** — not three reports handed back one by
  one. Both live runs produced a diagram.
- **FINDING (cosmetic, out of scope — needs a follow-up task): a multi-line spawn prompt breaks the
  section heading.** Gemini writes prompts like `"QUESTION: …\nSCOPE: …\nWHAT THE REPORT MUST CONTAIN: …"`,
  and the fold embeds the prompt VERBATIM in `## Subagent {i} — "{prompt}"`, so the markdown heading
  spills across lines and its closing quote lands on a later line. Harmless to the model (the structure
  is still legible) and to the budget, but it is malformed markdown in the transcript the human reads,
  and it means a strict single-line heading regex does not match a real fold. The hermetic tests all use
  single-line prompts, which is why nothing caught it. NOT fixed here — collapsing the prompt's
  whitespace inside the heading is new runtime behavior, explicitly out of scope for 108. The live smoke
  uses a heading probe (`_SECTION_HEADING_RE`) tolerant of it; the hermetic tests keep the strict regex.
- **Why the byte guard needed TWO assertions, not one.** Equality on the section body catches a theft big
  enough to drop a line, but truncation is LINE-aligned, so a small theft can land inside a line and
  still yield byte-identical text (the Tester's 30-byte mutation does exactly that at `per_child = 100`
  with ~17-byte lines — the 3-child equality tests stayed green). The `truncate()` spy pinning the
  `max_bytes` ARGUMENT is what makes a one-byte theft impossible to hide; it is now asserted both in its
  own test and inside `test_the_footer_never_eats_a_childs_byte_budget` itself, which is the test the
  Tester named.
- No runtime code changed: `git diff --stat src/` is empty. Both mutations above were reverted
  immediately (`git checkout src/decode/tools/agent.py`), and `.env` was borrowed from the main
  worktree for the live run and deleted afterwards.

### [Tester] 2026-07-14 03:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 181 files already formatted; `ruff check`: all
  checks passed; `make pre-commit` unit run: 1584 passed, 0 failed)
- Unit tests: 1584 passed / 0 failed
- Integration tests: 117 passed / 2 skipped (both live-key-gated; ran the live one myself separately —
  see below)
- Warnings: 0 (`-W error` re-run of the capstone file confirms; project `filterwarnings=["error"]`)
- `git diff --stat src/` confirmed genuinely EMPTY — no runtime change smuggled into this proof task.

**E2E adversarial pass** (this task is proof+docs, so the adversarial pass = attacking the PROOF
itself via mutation, plus running the live smoke with my own eyes — the two things that actually
carry this task's value)
- Happy path: `uv run pytest tests/integration/test_subagents_capstone.py -k "resilience_matrix or
  width_cap or substance_nag"` → `3 passed, 10 deselected` (PASS)
- Break path 1 (mutation: retry-budget regression) — set `AGENT_TOOL_RETRIES = 1` in
  `src/decode/tools/agent.py`, ran `uv run pytest tests/integration/test_subagents_capstone.py -k
  width_cap` → `1 failed`, `pydantic_ai.exceptions.UnexpectedModelBehavior: Tool 'agent' exceeded max
  retries count of 1`, exactly as claimed. Reverted (`git checkout -- src/decode/tools/agent.py`),
  re-ran → `1 passed`. (PASS — the test genuinely proves the raised retry budget, not merely
  "passes at any budget.")
- Break path 2 (mutation: byte-budget theft) — set `child_max_bytes = (settings.subagent_result_max_bytes
  - 30) // len(prompts)`, ran `uv run pytest tests/unit/decode/tools/test_agent.py -q` → **3 failed, 109
  passed** (`test_each_child_is_truncated_at_exactly_its_share_of_the_budget`,
  `test_a_single_child_still_gets_the_whole_byte_budget`, `test_the_footer_never_eats_a_childs_byte_budget`).
  Reverted, re-ran → `112 passed`. (PASS on substance — the `truncate()`-spy assertions genuinely catch a
  sub-line byte theft, which is the whole point. **Numbers discrepancy flagged below** — SWE reported "2
  failed, 110 passed"; I independently and reproducibly get 3/109, twice in a row. The theft is shared
  code (`child_max_bytes` is computed once, before the per-prompt split), so it also shaves the
  single-child budget test — a third, *correct* catch the SWE's own report undercounted. This does not
  change the verdict: the assertions have MORE teeth than claimed, not fewer.)
- Break path 3 (live-model reality check, not a mutation) — ran `test_live_gemini_fanout_smoke` myself
  twice: once via `uv run pytest tests/integration/test_subagents_capstone.py -k live -q -s` (`1 passed,
  12 deselected in 25.33s`), once via a standalone probe script driving the real `build_agent()` +
  `Runner` end to end and printing the actual tool call args + final answer + one folded section verbatim
  (see Evidence). Confirmed with my own eyes: ONE `agent` call carrying 3 distinct multi-line prompts, a
  synthesized final answer with prose **plus an ASCII box diagram**, and — reproducing the SWE's
  reported finding — a `## Subagent 1 — "QUESTION: ...\nSCOPE: ...\nWHAT THE REPORT MUST CONTAIN: ..."`
  heading that spans 3 lines with the closing quote landing on the third. (PASS on the feature; see
  adjudication below on the cosmetic finding.)

**Acceptance criteria**
- [x] PASS — Resilience matrix — `test_the_resilience_matrix_folds_four_children_in_one_turn` passes in
      isolation and in the full suite; read the assertions directly
      (`tests/integration/test_subagents_capstone.py:961-1114`): `len(sink.tool_calls()) == 1` +
      `.name == AGENT_TOOL_NAME` (one call on the sink); `_sections(aggregate)` == 4 headings in prompt
      order A/B/C/D; B's body == `_budgeted(report_b_retry, ...)` (the retry's report, empty first attempt
      leaves no trace) and `b_attempts` == exactly 2 (original + `+ _RETRY_NUDGE`); C's body ==
      `_NO_USABLE_REPORT_NOTE` and `hallucinated not in aggregate`; `bodies[0]`/`bodies[3]` ==
      `_budgeted(report, per_child=100)` (exact budget split, patched `subagent_result_max_bytes=400`);
      `aggregate.endswith(SYNTHESIS_FOOTER)` after the last section; `sink.permission_events() == []` and
      `resolvers.permission_requests == []` (zero `PermissionRequested`); `sink.tool_call_names() ==
      {AGENT_TOOL_NAME}` (silent children); `all("usage" not in kwargs ...)` + `handler.last_input_tokens >
      0` (parent-only usage); `session_log.load(log.path) == handler.message_history` and
      `_tool_calls_in_history(replayed, "read") == []` (resume-clean log, no child transcript). `len(spawns)
      == 6` (4 children + B's + C's one retry each, never a 3rd). Evidence:
      `uv run pytest tests/integration/test_subagents_capstone.py -k resilience_matrix -v` → PASSED.
- [x] PASS — Width-cap round-trip — `test_the_width_cap_nag_round_trips_through_the_loop_and_the_turn_completes`
      passes; mutation-verified myself (Break path 1 above) that it genuinely requires
      `AGENT_TOOL_RETRIES >= 2`, not merely "passes."
- [x] PASS — Substance-guard round-trip —
      `test_the_substance_nag_round_trips_and_the_rewritten_prompts_spawn_children` passes; asserts the nag
      names `Prompt 1 ("explore the repo")` + `_TERSE`, zero children spawn on the lazy call, rewritten
      prompts fan out green.
- [x] PASS — Live Gemini smoke — ran it myself twice (pytest + standalone probe, see Break path 3); the
      parent's real final answer contains prose + an ASCII box diagram; zero permission prompts; `prompts`
      list confirmed in the recorded `ToolCallPart.args`.
- [x] PASS — Docs — `docs/glossary.md` rows (Subagent/Fan-out/Subagent Report/Synthesis Footer/Agent tool,
      `docs/glossary.md:22-25,41`) read against `src/decode/tools/agent.py`: MIN_PROMPT_WORDS=8 floor,
      `AGENT_TOOL_RETRIES=3`, `MAX_FANOUT_PROMPTS=6`, budget-split truncation, retry-once-then-note, footer
      — all match. `docs/adr/0013-explore-subagents.md:1-10` carries the dated `Amended: 2026-07-13`
      header pointing at ADR-0017. `docs/adr/0017-*.md` §3's `Amended: 2026-07-14` block matches
      `agent.py`'s actual guard verbatim (bare word-floor, not a three-signal AND-gate) — read side by
      side, no drift found. `.claude/skills/manual-e2e-qa/SKILL.md` subagents row (diff read in full)
      covers the substance guard, retry-once/give-up note, and Synthesis Footer that 104/106/107 shipped —
      matches.
- [x] PASS — `make ci` green — reproduced independently as two separate runs (not `make ci` itself, to
      isolate the Docker-flake-prone integration suite from the unit suite): `make pre-commit` → `1584
      passed`; `make integration-tests` → `117 passed, 2 skipped`. `1584 + 117 = 1701` passed / 2 skipped —
      matches the SWE's claimed `make ci` total exactly.

**Evidence**
```
$ uv run ruff format --check && uv run ruff check
181 files already formatted
All checks passed!

$ make pre-commit   (unit suite)
======================= 1584 passed in 88.34s (0:01:28) ========================

$ make integration-tests
tests/integration/test_docker_executor.py ..........   [not flaky this run]
tests/integration/test_modal_executor.py ...........
tests/integration/test_subagents_capstone.py ............s
================== 117 passed, 2 skipped in 312.87s (0:05:12) ==================

# Mutation 1 — retry budget (AGENT_TOOL_RETRIES = 1):
$ uv run pytest tests/integration/test_subagents_capstone.py -k width_cap -q
FAILED ... pydantic_ai.exceptions.UnexpectedModelBehavior: Tool 'agent' exceeded max retries count of 1
1 failed, 12 deselected in 1.76s
$ git checkout -- src/decode/tools/agent.py && uv run pytest ... -k width_cap -q
1 passed, 12 deselected in 1.71s

# Mutation 2 — byte theft (child_max_bytes = (subagent_result_max_bytes - 30) // len(prompts)):
$ uv run pytest tests/unit/decode/tools/test_agent.py -q
3 failed, 109 passed in 4.74s   # SWE reported 2/110 — my rerun (twice) is reproducibly 3/109
$ git checkout -- src/decode/tools/agent.py && uv run pytest tests/unit/decode/tools/test_agent.py -q
112 passed in 4.58s

# Live Gemini, run myself (independent of the SWE's runs):
$ uv run pytest tests/integration/test_subagents_capstone.py -k live -q -s
1 passed, 12 deselected in 25.33s
$ uv run python <standalone probe driving build_agent()+Runner>
=== TOOL CALLS ===
agent prompts= ['QUESTION: How is the agent built and its tools registered?\nSCOPE: src/decode/agent/factory.py\n...', ...]  # 3 distinct angles, one call
=== FINAL ANSWER === (excerpt)
...integrates agent construction, tool declaration, and permission management...
### Architecture Diagram
+---------------------------+
| src/decode/agent/factory.py |
...                                    <- real ASCII box diagram, not prose-only
=== ONE FOLDED SECTION SAMPLE ===
## Subagent 1 — "QUESTION: How is the agent built and its tools registered?
SCOPE: src/decode/agent/factory.py
WHAT THE REPORT MUST CONTAIN: Explain the `build_agent` function, ...evidence."   <- multi-line heading, closing quote on line 3
```

**Other issues found**
- **Evidence-accuracy nit (not a code defect):** the SWE's report claims the byte-theft mutation yields
  "2 failed, 110 passed"; I get a reproducible "3 failed, 109 passed" (see Break path 2). Root cause: the
  mutation shaves `child_max_bytes` before the `// len(prompts)` split, so it also breaks
  `test_a_single_child_still_gets_the_whole_byte_budget` (a `len(prompts)==1` case, `per_child` still
  shrinks by 30). This makes the SWE's own proof stronger than reported, not weaker — not a blocker, but
  the report's numbers should be corrected for the record.
- **code-review plugin note:** `code-review@claude-plugins-official` is enabled in
  `.claude/settings.json`, but as a subagent I have no tool surface to invoke its slash command (only
  Read/Edit/Write/Bash). I could not fold its output into this review. Flagging so the orchestrator can
  invoke it separately if that signal is wanted before merge; it would only add advisory findings, not
  change this verdict, per my role's own rubric.

**ADJUDICATION — the multi-line spawn-prompt heading finding**
Reproduced independently (Break path 3 / Evidence above) against a REAL Gemini fan-out, not the SWE's
transcript: Gemini does write multi-line prompts (`"QUESTION: …\nSCOPE: …\nWHAT THE REPORT MUST
CONTAIN: …"`), and the fold's `## Subagent {i} — "{prompt}"` heading embeds the prompt verbatim, so the
heading's closing quote lands two lines down. I also checked how decode's own TUI renders this
(`src/decode/tui/render.py:108-113`, `_render_tool_result`): the tool result is wrapped in `rich.text.Text`,
**not** passed through a Markdown parser — so inside decode's own REPL this never renders as a broken
Markdown heading; it prints as literal text with an embedded newline and a stray `"` a couple of lines
down, inside a bordered Panel. The place it would actually look "malformed" is a human reading the raw
string outside decode: `session_log`'s JSONL, or someone piping a `decode run` result / copy-pasted
transcript through an external Markdown renderer (GitHub, a Rich-Markdown viewer, etc.), where `##` starts
a heading strictly at the first newline (Markdown headings are single-line by spec) and the following two
lines land as a new paragraph carrying a stray closing quote.

**My recommendation: (a) genuine defect worth a low-priority follow-up task — not (b) fully cosmetic-ignore,
and clearly not (c) severe enough to block this ship.** Reasoning:
- It is real and reproducible against a live model (not a hermetic-test blind spot only) — every parent
  model with any verbosity training will write multi-line briefs once told to give a QUESTION/SCOPE/REPORT
  shape, so this is closer to "the common case in practice" than an edge case.
- But it is presence-only cosmetic: nothing crashes, no data is lost, the model still parses and
  synthesizes correctly (my own live run shows a coherent final answer built from these very sections),
  and decode's own TUI never renders it as broken Markdown (Text, not Markdown).
- It is genuinely out of scope for THIS task (108 is proof+docs; the SWE is right that fixing it is new
  runtime behavior — e.g. `" ".join(prompt.split())` before embedding in the heading — and belongs in its
  own task with its own tests).
- It is a trivial, contained, one-line fix wherever it lands (`fold = "\n\n".join(f'## Subagent {index} —
  "{" ".join(prompt.split())}"\n\n{section}' ...)` in `src/decode/tools/agent.py:349-352`), so a follow-up
  task is cheap and low-risk — worth opening, not worth blocking on.

**VERDICT: PASS**
