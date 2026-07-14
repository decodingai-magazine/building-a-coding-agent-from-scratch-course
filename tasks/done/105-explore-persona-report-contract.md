---
id: 105
feature: subagent-fanout
status: done
---

# Explore persona rewrite: the tight structured Subagent Report contract

Depends on: 103 (references the shared fold budget). Implements ADR-0017 §8.

## Scope

Rewrite the BODY of `src/decode/agents/builtin/explore.md` so a child returns a **tight
structured summary** — deliberately compressed so N of them fit the parent's ~16 KB fold budget.

**`src/decode/agents/builtin/explore.md`**

- Frontmatter UNCHANGED: `name: explore`, `tools: read/glob/grep/lsp`, `subagent: true`,
  `mode: default` — every loader/narrowing test must stay green untouched.
- Body rewrite (final message IS the report — retained), the report must carry:
  - **the finding** — the direct answer to the question asked;
  - **the file:line evidence** — the specific files/functions/line ranges backing every claim;
    a summary with no file:line evidence is a hallucination tell (pairs with 106's
    zero-tool-call detection);
  - **the trace it followed** — the call/config chain across files, not one snippet in isolation.
- State the compression contract explicitly: the report is one of up to N sibling reports sharing
  the caller's budget — keep it tight (findings and evidence, no preamble, no methodology essay);
  a child's report may be byte-truncated, so lead with the finding.
- Do NOT put any parent-synthesis instruction here — that rides the Synthesis Footer (107),
  by decision.

**Tests**

- `tests/unit/decode/agents/` (wherever the persona/loader tests live) — existing frontmatter
  tests stay green byte-for-byte on the frontmatter. Add one stable content pin: the explore body
  names the three report parts (finding / file:line evidence / trace) — assert on stable
  structural markers, not full sentences (keep it un-brittle; SWE's call on the exact anchors).

## Acceptance Criteria

- [x] `explore.md` frontmatter is byte-identical to before (tools exactly `read/glob/grep/lsp`, `subagent: true`); all existing loader/narrowing/persona tests pass unmodified.
- [x] The body instructs the child to return the finding + file:line evidence + the trace followed, compressed, leading with the finding — pinned by one stable content test.
- [x] The body contains NO parent-synthesis / diagram instruction (that is the footer's job — a grep-style test or reviewer check).
- [x] `make ci` green.

## Out of scope

- Enforcing the contract at runtime (106 enforces the two BAD conditions; file:line presence is a persona-quality lever, not a hard validator — decision locked).
- Any change to build/plan/code-reviewer persona bodies.

## Log

### [SWE] 2026-07-14 — Implementation

**Files modified**

- `src/decode/agents/builtin/explore.md` — body rewritten to the three-part Subagent Report contract
  (Finding / Evidence with `file:line` / Trace) + the explicit compression contract (one of N sibling
  reports sharing one caller budget; truncated from the end → lead with the finding). Frontmatter
  untouched: the diff's first hunk starts at line 16, i.e. below the `---`.
- `tests/unit/decode/agents/test_loader.py` — two content pins on the loaded persona (`prompt`), next
  to the existing explore frontmatter tests.

**Tests**

- Unit: 1562 passing, 0 failing (`make unit-tests`). The existing loader/narrowing/persona tests were
  NOT touched — `git diff` on the test file is +28 lines, no deletions.
- Integration: 112 passing, 2 skipped (both skip on unset live API keys — pre-existing).
- New: `test_explore_body_states_the_three_part_report_contract` (red before the rewrite, on the
  `file:line` marker) and `test_explore_body_carries_no_parent_synthesis_instruction`.

**Acceptance criteria**

- [x] Frontmatter byte-identical; existing tests unmodified — `tests/unit/decode/agents/test_loader.py::test_explore_agent_is_a_read_only_default_mode_subagent` (+ `test_only_explore_is_a_subagent`, `test_all_builtin_personas_expose_the_lsp_tool`) green untouched.
- [x] Body carries finding + `file:line` evidence + trace, compressed, finding first — `test_explore_body_states_the_three_part_report_contract`.
- [x] No parent-synthesis / diagram instruction — `test_explore_body_carries_no_parent_synthesis_instruction` (bans `synthes` / `diagram` / `mermaid` / `ascii` / `box-drawing`).
- [x] `make ci` green (format-check, lint-check, unit, integration all clean).

**Evidence**

```
$ uv run pytest tests/unit/decode/agents/test_loader.py -q     # BEFORE the body rewrite (red)
E  AssertionError: the explore report contract must name 'file:line'
1 failed, 22 passed in 0.32s

$ make unit-tests
1562 passed in 87.51s (0:01:27)

$ make integration-tests
112 passed, 2 skipped in 302.15s (0:05:02)

$ uv run python -c "from decode.agents.loader import load_agent; ..."   # e2e: the real child prompt
name= explore | tools= ('read', 'glob', 'grep', 'lsp') | subagent= True | mode= PermissionMode.DEFAULT
prompt bytes: 1761
---- system prompt the child receives ----
Your final message IS your report. Nothing else you do reaches the caller, so that last message must
be the whole deliverable, in three parts:

- **Finding** — the direct answer to the question you were asked. First, in the first line or two.
- **Evidence** — the `file:line` references backing every claim: ...
- **Trace** — the call/config chain you followed across files ...
Keep it tight. You are one of up to N sibling subagents ... all of your reports share one caller
budget ... A long report gets truncated from the end, so lead with the finding ...
```

**Notes**

- Test anchors are deliberately structural, not sentences: the three section labels (`finding`,
  `trace`), the literal `file:line` term, plus `sibling` + `truncat` for the compression contract.
  A wording tweak inside a bullet cannot shatter them; deleting a report part will.
- The no-synthesis pin is a substring ban list. It is a persona-side guard only; the Synthesis Footer
  (107) still needs its own positive test that the harness appends the diagram instruction.
- Retained from the old body: final message IS the report, read-only tools, go to the source, trace
  the real path across files. Dropped: "Cite what you found" as a separate bullet — it is now the
  Evidence part of the report contract, stated harder (a claim with no `file:line` reads as a
  hallucination), so the instruction is not duplicated.
- No runtime enforcement added (106 owns the two BAD conditions), no other persona touched.

### [Tester] 2026-07-14 12:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 181 files already formatted; `ruff check`: all checks passed)
- Unit tests: 1562 passed / 0 failed (`make pre-commit`)
- Integration tests: 112 passed / 2 skipped (both live-key-gated: Opik export smoke, live Gemini fan-out smoke — unset key in the default `make integration-tests` invocation, expected)
- Live-key run: re-ran `test_live_gemini_fanout_smoke` alone with a real `GEMINI_API_KEY` exported → 1 passed in 19.78s. `test_sandbox_teardown.py` ran clean (1 passed) — the adjudicated flake did not reproduce.
- Warnings: 0 (`filterwarnings=["error"]`; any warning would have failed a test)

**E2E adversarial pass**
- Happy path — genuinely live: built the real agent via `build_agent(flow_mode=True)` on live Gemini and called `decode.tools.agent._spawn_child` directly (not a scripted `FunctionModel`) with the prompt "Read `src/decode/tools/truncate.py`... report the function name and exact line numbers." → real report, 1239 bytes:
  ```
  **Finding**: The `truncate` function in `src/decode/tools/truncate.py` truncates text. It uses an
  algorithm that first prioritizes the maximum number of lines, and then, if the content is still too
  large, it truncates by byte limit, snapping the cut point to the last whole line that fits...

  **Evidence**:
  - **Truncation function**: `src/decode/tools/truncate.py:52-74` (`truncate` function).
  - **Core truncation logic**: `src/decode/tools/truncate.py:77-94` (`_truncate_text` function).
  - **Line cap application**: `src/decode/tools/truncate.py:80` (`head = "".join(lines[:max_lines])`).
  ...
  **Trace**:
  `src/decode/tools/truncate.py:52` (`truncate`) → `src/decode/tools/truncate.py:65` (`_truncate_text`)
  → `src/decode/tools/truncate.py:84` (`_line_offsets`)
  ```
  Leads with the finding, carries real `file:line` evidence (verified the cited line numbers point to
  the actual functions), tight three-part shape, zero preamble/methodology essay. PASS.
- Break path 1 (adversarial tool failure — real model, real grep tool): a first live probe asked the
  child to search two files with `grep`; the live model's `grep` call tripped the tool's own retry
  budget (`UnexpectedModelBehavior: Tool 'grep' exceeded max retries count of 1`, unrelated to 105 —
  a pre-existing `grep` tool-arg friction). `_spawn_child`'s catch-all fired exactly as documented:
  the child's section came back as `"This subagent failed before producing a report."` (47 bytes) —
  no crash propagated to the caller, no stack trace leaked, graceful failure note per ADR-0017 §5.
  PASS (this validates existing `_spawn_child` resilience, not persona-specific, but confirms the
  new body doesn't destabilize the child in a way that turns a tool hiccup into a hard crash).
- Break path 2 (content-pin meaningfulness — revert-and-check): extracted the pre-rewrite body via
  `git show HEAD:src/decode/agents/builtin/explore.md` and ran the marker check against it —
  `finding`/`trace` present (old body happened to use both words), but `file:line` / `sibling` /
  `truncat` all absent → the new test would genuinely FAIL on the old body. Confirms the pin is not
  a tautology. PASS.
- Break path 3 (content-pin brittleness — reasonable wording tweak): took the new body and swapped
  several phrases (`the finding`→`a finding`, `caller budget`→`shared budget`, `a long report`→`an
  overlong report`) while preserving structure; all five markers and both no-leak checks still held.
  Confirms the pin survives a copy-edit and won't nag the SWE on wording churn. PASS.
- Frontmatter byte-identity: `git diff` on `explore.md` shows its first (and only) hunk starting at
  `@@ -16,17 +16,25 @@`, strictly below the closing `---` at line 11 of the current file — no hunk
  touches `name`/`tools`/`subagent`/`mode`. Read the full current file to confirm lines 1-11 match
  the pre-existing frontmatter verbatim (`tools: read/glob/grep/lsp`, `subagent: true`, `mode:
  default`). PASS.

**Acceptance criteria**
- [x] PASS — `explore.md` frontmatter byte-identical; all existing loader/narrowing/persona tests pass
      unmodified — `git diff` confirms zero hunks above line 11 (the closing `---`); `git diff` on
      `tests/unit/decode/agents/test_loader.py` is +28/-0 (additions only, no existing test edited);
      `make pre-commit` shows `test_explore_agent_is_a_read_only_default_mode_subagent`,
      `test_only_explore_is_a_subagent`, `test_all_builtin_personas_expose_the_lsp_tool` all green.
- [x] PASS — body instructs finding + `file:line` evidence + trace, compressed, finding-first, pinned
      by a content test — `tests/unit/decode/agents/test_loader.py::test_explore_body_states_the_three_part_report_contract`
      passes; behaviorally verified live (see Happy path above): the real child's report literally led
      with `**Finding**`, then `**Evidence**` with accurate `file:line`, then `**Trace**`.
- [x] PASS — body contains no parent-synthesis/diagram instruction —
      `test_explore_body_carries_no_parent_synthesis_instruction` passes (bans `synthes`/`diagram`/
      `mermaid`/`ascii`/`box-drawing`); manually read the full 40-line body top to bottom, confirms no
      instruction to compile N sibling reports into a diagram — that stays task 107's job (107 is
      still `status: pending`, confirming it was not accidentally built here).
- [x] PASS — `make ci` green — `format-check` (181 files formatted), `lint-check` (all checks passed),
      `make pre-commit` (1562 passed, 0 failed), `make integration-tests` (112 passed, 2 skipped — both
      live-key-gated, and separately re-run green with a real key: 1 passed).

**Evidence**
```
$ make pre-commit
======================= 1562 passed in 95.09s (0:01:35) ========================

$ make integration-tests
================== 112 passed, 2 skipped in 331.63s (0:05:31) ==================
SKIPPED [1] test_observability_capstone.py:580: OPIK/GEMINI keys unset
SKIPPED [1] test_subagents_capstone.py:710: GEMINI_API_KEY is unset

$ GEMINI_API_KEY=<redacted> uv run pytest tests/integration/test_subagents_capstone.py::test_live_gemini_fanout_smoke -v
tests/integration/test_subagents_capstone.py::test_live_gemini_fanout_smoke PASSED [100%]
1 passed in 19.78s
```

**Adversarial: base-vs-persona contradiction check**
Read `src/decode/agent/factory.py::_register_instructions` — the child's system prompt is
`_BASE_INSTRUCTIONS + explore.prompt + assemble_memory(...) + assemble_skills_catalog(...)` joined by
blank lines, ONE instructions block. `_BASE_INSTRUCTIONS` says "concise and precise: answer directly,
prefer running the work over describing it, never invent... say so plainly." The rewritten explore
body ("Keep it tight... no preamble, no methodology essay... findings and evidence only") sharpens
the same conciseness demand rather than contradicting it — no tension found. Neither part tells the
child to be conversational or verbose, so there is no fight between base and persona here.

**Other issues found**
- None blocking. The `grep`-tool retry-budget friction hit during my adversarial probe
  (`UnexpectedModelBehavior: Tool 'grep' exceeded max retries count of 1` on a live model) is a
  pre-existing `grep`/tool-arg robustness question, out of scope for 105 (persona body only) and
  already covered by 106's "zero tool calls / bad report" validation lane at the harness level — not
  a regression introduced by this task. Noting it here for visibility only.
- `code-review` plugin is enabled in `.claude/settings.json` but is a slash command
  (`/code-review`), not invocable as a tool from this Tester session — substituted with an exhaustive
  manual diff read + line-level frontmatter verification + live-model behavioral check instead.

**VERDICT: PASS**

### [PA] 2026-07-14 — Acceptance Review

**VERDICT: ACCEPT**

Reviewed as part of the subagent-fanout feature acceptance (PR #33). `src/decode/agents/builtin/explore.md` re-read in full: frontmatter intact (`read/glob/grep/lsp`, `subagent: true`), body states Finding / `file:line` Evidence / Trace, the sibling-shared-budget compression contract, and lead-with-the-finding; zero synthesis/diagram wording (107's guard test pins all four personas). The Tester's live child report (Finding→Evidence→Trace with accurate citations) proves the contract lands with a real model.
