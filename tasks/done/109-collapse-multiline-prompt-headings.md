---
id: 109
feature: subagent-fanout
status: done
---

# Collapse a multi-line spawn prompt into a single-line section heading

Depends on: 103 (the labelled aggregation it fixes), 108 (which found the defect). Implements
ADR-0017 §5 as written — the fold's heading is a *label*, and a Markdown heading is single-line.

## Scope

**The defect** (found by 108's live Gemini smoke, reproduced independently by the Tester against a
real fan-out). A real parent model writes MULTI-LINE spawn prompts, because the tool description asks
it for three parts:

```
QUESTION: How is the agent built and its tools registered?
SCOPE: src/decode/agent/factory.py
WHAT THE REPORT MUST CONTAIN: …
```

The fold embeds that prompt VERBATIM in its section heading (`src/decode/tools/agent.py`, the
`## Subagent {index} — "{prompt}"` f-string), so the heading spills across three lines and its
closing quote lands two lines below the `##`. A Markdown heading ends at the first newline by spec:
what a human reads outside decode (the session-log JSONL, a copy-pasted `decode run` transcript, a
GitHub-rendered paste) is a truncated heading followed by a stray paragraph carrying an orphan `"`.

Confirmed NOT harmful to: decode's own TUI (`src/decode/tui/render.py` wraps a tool result in
`rich.text.Text`, not a Markdown parser), the parent model (it still parses and synthesizes the
sections correctly — 108's live runs prove it), or the byte budget (headings are harness overhead on
top of the per-child share either way). This is a human-readability fix, nothing more.

Every hermetic test used single-line prompts, which is why nothing caught it.

**`src/decode/tools/agent.py`**

- Collapse the prompt's whitespace when RENDERING the heading — `" ".join(prompt.split())` — so a
  section heading is always exactly one line, closing quote included.
- The prompt handed to the CHILD stays byte-for-byte what the model wrote (newlines and all): the
  child is briefed with the model's own three-part brief; only the *label* is collapsed.
- No other change to the aggregation format.

**Tests** (`tests/unit/decode/tools/test_agent.py`) — the regression tests 108 was missing:

- A multi-line prompt folds a SINGLE-LINE heading: the strict `^## Subagent (\d+) — "(.*)"$` parse
  matches on one line, closing quote included, and yields the collapsed prompt.
- The child still receives the ORIGINAL uncollapsed prompt (asserted on what reaches `agent.run()`).
- Existing single-line-prompt folds are byte-identical (collapse is a no-op on them).

## Acceptance Criteria

- [x] A multi-line spawn prompt produces exactly one heading LINE, with its closing quote on that same line (strict single-line regex matches).
- [x] The child's spawn prompt is unchanged — `agent.run()` receives the model's original multi-line text.
- [x] A single-line prompt's fold is unchanged (no regression in the existing aggregation tests).
- [x] The live Gemini smoke asserts the STRICT single-line heading (its tolerant probe existed only to work around this defect).
- [x] `make ci` green.

## Out of scope

- Any other change to the aggregation format (section order, numbering, bodies, separators).
- The per-child byte budget and the Synthesis Footer.
- Eliding an over-long heading. Judgment call, taken deliberately: a Markdown heading is
  single-line but UNBOUNDED in length — renderers wrap it — so length was never the defect, and the
  full prompt in the label is what lets the reader (and the model) attribute a section to its angle.
  Truncating it would add a knob nobody asked for and delete information to fix a problem nobody has;
  the fan-out is capped at 6 prompts, so the headings cost is bounded anyway.

## Log

### [SWE] 2026-07-14 20:15 — Implementation

**Files modified**
- `src/decode/tools/agent.py` — new `_label(prompt)` (`" ".join(prompt.split())`), used ONLY in the
  fold's heading f-string. The prompt handed to `agent.run()` in `_run_attempt` is untouched — the
  child still reads the model's three-part brief on its own lines. Six-line change, no new constant.
- `tests/unit/decode/tools/test_agent.py` — the two regression tests 108 was missing:
  `test_a_multi_line_prompt_still_folds_a_single_line_heading` (a QUESTION/SCOPE/REPORT brief parsed by
  the STRICT `^## Subagent (\d+) — "(.*)"$` — it can only match if the whole label, closing quote
  included, is on one line) and `test_the_child_is_briefed_with_the_models_original_uncollapsed_prompt`
  (asserts on what reaches `agent.run()` via the existing `_ScriptedAgent.prompts` recorder).
- `tests/integration/test_subagents_capstone.py` — the live smoke's tolerant `_SECTION_HEADING_RE`
  DELETED; the live fold is now asserted with the strict single-line `_SECTION_RE`. That probe existed
  only to work around this defect, so the live smoke becomes the end-to-end guard for it.

**Tests**
- Unit: 1586 passing, 0 failing (was 1584 — the two new regressions).
- Integration: 117 passing, 2 skipped (the live-key-gated smokes).
- Live Gemini (real key borrowed from the main worktree, deleted after): `test_live_gemini_fanout_smoke`
  PASSED with the strict heading regex.

**Acceptance criteria**
- [x] Multi-line prompt → single-line heading — RED first: the new test failed with `IndexError` on
      `_sections(out)[0]`, i.e. the strict parse matched NOTHING (the heading really was broken).
      Green after `_label`.
- [x] Child gets the ORIGINAL prompt — `scripted.prompts == [multi_line]`, byte-for-byte. Written
      before the fix and kept green through it: it is what stops the fix over-reaching into the spawn.
- [x] Single-line folds unchanged — collapse is a no-op on them; every pre-existing aggregation/byte
      test (112 in `test_agent.py`) stayed green untouched.
- [x] Live smoke asserts the strict heading — swapped and run live (see Evidence).
- [x] `make ci` green — run as its two halves: `make pre-commit` (1586) + `make integration-tests`
      (117 passed / 2 skipped).

**Evidence**
```
# RED (before the fix) — the strict parse finds no section at all:
$ uv run pytest tests/unit/decode/tools/test_agent.py -k "multi_line or uncollapsed" -q
>       heading, body = _sections(out)[0]
E       IndexError: list index out of range
1 failed, 1 passed

# GREEN:
$ make unit-tests           → 1586 passed in 95.07s
$ make integration-tests    → 117 passed, 2 skipped in 324.16s
$ uv run ruff format --check && uv run ruff check → 181 files already formatted / All checks passed!

# E2E (Step 7) — a REAL Gemini fan-out, steered to the three-part brief shape (the shape 108 saw in
# the wild), driven through build_agent + Runner. The model wrote genuinely MULTI-LINE prompts:
=== CHILD PROMPTS (what actually reached agent.run) ===
--- multiline=True  ---
QUESTION: How does `factory.py` contribute to creating and configuring agents?
SCOPE: src/decode/agent/factory.py
WHAT THE REPORT MUST CONTAIN: A description of the functions and classes involved…   <- newlines INTACT
=== FOLDED HEADINGS ===
'## Subagent 1 — "QUESTION: How does `factory.py` contribute to creating and configuring agents? SCOPE: src/decode/agent/factory.py WHAT THE REPORT MUST CONTAIN: A description of…evidence."'
strict single-line matches: 3        <- 3/3 headings, each ONE line, closing quote included

$ uv run pytest tests/integration/test_subagents_capstone.py -q -k live   # real GEMINI_API_KEY
1 passed, 12 deselected in 23.43s
```

**Notes**
- **The judgment call — no elision, deliberately.** A very long single-line prompt now makes a very long
  heading. I did NOT truncate it. A Markdown heading is single-line by spec but UNBOUNDED in length —
  every renderer wraps it — so length was never the defect; the newline was. Eliding would add a
  magic-number knob nobody asked for AND delete the very text that lets a reader (and the parent model)
  attribute a section to its angle. The fan-out is capped at 6 prompts, so the total heading cost is
  bounded by construction, and headings have always been harness overhead ON TOP of the per-child byte
  budget — a long label cannot steal a child's evidence. If a real model ever writes a 500-word brief,
  the fix is to coach brevity in the tool description, not to censor the transcript.
- No new dependency, no new setting, no new module constant — one private helper beside the fold it
  serves.
- The first live probe run happened to produce single-line prompts (Gemini varies), which would have
  proven nothing; the run above steers the three-part shape explicitly so the e2e evidence actually
  exercises the defect. `.env` was borrowed from the main worktree for both runs and deleted after each.

### [Tester] 2026-07-14 03:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS
- Unit tests: 1586 passed / 0 failed (114 in `test_agent.py`: 112 pre-existing + 2 new)
- Integration tests: 117 passed / 2 skipped (both live-key-gated, expected without `.env`)
- Warnings: 0

**E2E adversarial pass**
- Happy path: borrowed `.env` (real `GEMINI_API_KEY`) into this worktree, ran
  `uv run pytest tests/integration/test_subagents_capstone.py -k live -q -s` → 1 passed. Instrumented
  the live test with print statements (temporarily, reverted after) to capture what the model actually
  wrote and what folded back — see Evidence. (PASS)
- Break path 1 (revert-and-confirm-red): reverted `_label(prompt)` → `prompt` in a working copy of
  `src/decode/tools/agent.py`, ran `uv run pytest tests/unit/decode/tools/test_agent.py -k "multi_line or
  uncollapsed" -q` → `test_a_multi_line_prompt_still_folds_a_single_line_heading` failed with
  `IndexError: list index out of range` (strict parse finds nothing), exactly as the SWE claimed. File
  restored; `git diff --stat` back to the original 20/1 for `agent.py`. (PASS)
- Break path 2 (spy on the child's actual prompt): confirmed via code read that `_run_attempt` calls
  `_require_main_agent().run(prompt, …)` with the untouched `prompt` argument — `_label` is referenced
  nowhere near `_spawn_child`/`_run_attempt`, only in the `fold = "\n\n".join(...)` f-string. Confirmed
  live: the instrumented smoke's `child_prompts` list held the model's raw multi-line text (`\n` present)
  while the folded headings were single-line. (PASS)
- Break path 3 (adversarial `_label` inputs — embedded literal `"`, tabs/CRLF, a 3000+ char single-line
  prompt, Unicode, multi-blank-line prompt): ran `_label` directly on each via `uv run python -c ...`
  — every output was newline/CR/tab-free, exactly `" ".join(prompt.split())`. Then ran a temporary unit
  test (added, run, and cleanly removed — `git diff --stat` confirmed byte-identical to the original 46
  insertions afterward) driving the embedded-quote, tab/CRLF, and long-prompt prompts through the real
  `agent()` fold: `_sections()` (the strict single-line parser) parsed all 3 into distinct headings with
  no leakage between them. A whitespace-only prompt never reaches `_label` at all — `_check_substance`
  rejects it first (`len(prompt.split()) < MIN_PROMPT_WORDS=8` — `_faults('   \n\t  ')` → the terse-nag),
  so there is no empty-heading path in practice. (PASS)
- Break path 4 (tolerant-regex-deletion sanity check): built both the deleted tolerant
  `^## Subagent \d+ — ` and the new strict `^## Subagent \d+ — ".*"$` regexes and ran them against a
  synthetic BROKEN (pre-fix, multi-line) heading and a FIXED (post-fix, single-line) heading. The
  tolerant regex matched BOTH — i.e. it was blind to the defect, would have passed even a genuinely
  broken heading. The strict regex matched only the FIXED one. Deletion is a strict tightening of the
  live assertion, not a loosening. (PASS)

**Acceptance criteria**
- [x] PASS — A multi-line spawn prompt produces exactly one heading LINE, with its closing quote on
      that same line (strict single-line regex matches) — Evidence: revert-and-confirm-red above (SWE's
      claimed RED reproduced exactly); `test_a_multi_line_prompt_still_folds_a_single_line_heading`
      green with the fix; live Gemini instrumented run folded 3/3 genuinely multi-line child prompts into
      3/3 single-line headings.
- [x] PASS — The child's spawn prompt is unchanged — `agent.run()` receives the model's original
      multi-line text — Evidence: code read (`src/decode/tools/agent.py:449-455`, `_run_attempt` passes
      `prompt` untouched to `.run()`); `test_the_child_is_briefed_with_the_models_original_uncollapsed_prompt`
      green; live instrumented run shows `child_prompts` holding `\n`-intact text identical to what the
      model wrote.
- [x] PASS — A single-line prompt's fold is unchanged (no regression) — Evidence: all 112 pre-existing
      `test_agent.py` aggregation/byte tests stayed green (114 collected total = 112 + 2 new); `_label`
      is a documented no-op on already-single-line text (`" ".join(s.split())` for whitespace-normal `s`).
- [x] PASS — The live Gemini smoke asserts the STRICT single-line heading — Evidence: `git diff` on
      `tests/integration/test_subagents_capstone.py` shows `_SECTION_HEADING_RE` (tolerant) deleted,
      the assertion now uses `_SECTION_RE` (strict, same one the hermetic tests use); ran the live smoke
      myself with a real borrowed key — 1 passed, confirmed via instrumentation that the underlying
      model calls really produced multi-line prompts (not a vacuous pass on single-line output); tolerant
      vs strict regex sanity check above confirms the deletion tightens rather than weakens the guard.
- [x] PASS — `make ci` green — Evidence: `make format-check` (181 files formatted), `make lint-check`
      (all checks passed), `make pre-commit` (1586 passed), `make integration-tests` (117 passed / 2
      skipped) — all re-run independently, matching the SWE's claimed counts exactly.

**Evidence**
```
$ make pre-commit
======================= 1586 passed in 94.40s (0:01:34) ========================

$ make integration-tests
================== 117 passed, 2 skipped in 311.13s (0:05:11) ==================

# Revert-and-confirm-red (scratch edit of _label(prompt) -> prompt, then restored):
$ uv run pytest tests/unit/decode/tools/test_agent.py -k "multi_line or uncollapsed" -q
>       heading, body = _sections(out)[0]
E       IndexError: list index out of range
1 failed, 1 passed, 112 deselected

# Live Gemini smoke, run independently with a real borrowed GEMINI_API_KEY, instrumented to print
# what the model actually wrote and what folded back:
=== QA: CHILD PROMPTS RECEIVED ===
--- prompt 1 (has newline: True) ---
'QUESTION: How is the agent built and how are its tools registered within factory.py?\nSCOPE:
src/decode/agent/factory.py\nWHAT THE REPORT MUST CONTAIN: ...'
--- prompt 2 (has newline: True) ---
'QUESTION: How are tools declared and registered in registry.py?\nSCOPE: src/decode/tools/registry.py
\n...'
--- prompt 3 (has newline: True) ---
'QUESTION: How does gate.py determine if a tool call is allowed, asked, or denied?\nSCOPE:
src/decode/permissions/gate.py\n...'
=== QA: FOLDED AGGREGATE HEADINGS ===
'## Subagent 1 — "QUESTION: How is the agent built and how are its tools registered within
factory.py? SCOPE: src/decode/agent/factory.py WHAT THE REPORT MUST CONTAIN: ..."'
'## Subagent 2 — "QUESTION: How are tools declared and registered in registry.py? SCOPE:
src/decode/tools/registry.py WHAT THE REPORT MUST CONTAIN: ..."'
'## Subagent 3 — "QUESTION: How does gate.py determine if a tool call is allowed, asked, or denied?
SCOPE: src/decode/permissions/gate.py WHAT THE REPORT MUST CONTAIN: ..."'
1 passed, 12 deselected in 31.63s
```

**Other issues found**
- None. The scope discipline is tight: `git diff --stat` is exactly `src/decode/tools/agent.py` (20
  lines), `tests/integration/test_subagents_capstone.py` (17 lines), `tests/unit/decode/tools/test_agent.py`
  (46 lines) + the untracked task file — nothing else touched.
- The judgment call to not elide over-long headings is sound: verified directly that a 3000+ char
  single-line prompt collapses cleanly with no truncation, no exception, no pathological output — just a
  long (but valid) single-line heading, exactly as documented in Out of scope.
- Tester process note (not a code defect): during adversarial verification I mistakenly ran
  `git checkout -- tests/integration/test_subagents_capstone.py` while it had an uncommitted
  instrumentation edit layered on top of the SWE's uncommitted change, which reverted the file to the
  committed HEAD and briefly destroyed the SWE's uncommitted diff to that file. Recovered fully from a
  pre-edit backup taken moments earlier — `git diff` was confirmed byte-identical to the original SWE
  diff before proceeding. No SWE work was lost in the final state; flagging for the record.

**VERDICT: PASS**
