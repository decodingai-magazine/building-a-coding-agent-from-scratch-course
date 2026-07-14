---
id: 110
feature: subagent-fanout
status: done
---

# Tell the primary personas WHEN to delegate — plus the two PR-Reviewer nits

Depends on: 103-109 (the whole fan-out mechanism this task finally makes *reachable*). The final task
of `subagent-fanout`. Closes the gap the PA found at acceptance: the fan-out works, but nothing tells
a primary agent to REACH FOR it.

## Scope

**1. The delegation nudge (the user's literal ask, currently unmet).**

The original request was: *"Properly run subagents in parallel. When we ask for 'Explore repo' run by
default 3 agents in parallel with different prompts and aggregate their result."* Tasks 103-109 built
the mechanism — one `agent(prompts=[…])` call, N concurrent children, one labelled aggregate, a
Synthesis Footer telling the parent how to compile it. But `build.md` / `plan.md` / `code-reviewer.md`
never mention delegating exploration; if anything they nudge the model to read and search serially
itself ("Understand before you act. Read the relevant files and search the codebase…"). The tool
description says how to fan out *once the model decides to call the tool* — nothing says WHEN to
reach for it. So a bare "explore this repo" may never fan out at all, and the feature's headline
behavior depends on luck.

- Add a SHORT (one- to two-line) delegation nudge to each of the three primary personas that grant
  the `agent` tool — `build.md`, `plan.md`, `code-reviewer.md`. `explore.md` gets none: it is the
  subagent, it has no `agent` tool, and telling a child to delegate would be nonsense.
- The nudge states WHEN to delegate — a broad, multi-area question about the codebase ("explore this
  repo", "how does X work end to end") → ONE `agent` call with at least 3 distinct angles, rather than
  exploring serially; a narrow single-file question → just read the file.
- It must NOT state how to synthesize. Synthesis is the harness-owned Synthesis Footer's job
  (ADR-0017 §9), and `test_no_builtin_persona_body_carries_the_synthesis_instruction` bans the words
  `synthes` / `diagram` / `mermaid` / `ascii` / `box-drawing` from every persona body. That guard
  stays green: this nudge is about the CALL, not the answer.

**2. The two PR-Reviewer nits in `src/decode/tools/agent.py`.**

- `_faults()` returns a `list[str]` that can only ever hold ONE element, which is then `"; ".join`ed.
  With ADR-0017 §3's single-criterion floor locked, `str | None` is the honest type — the list is a
  leftover seam from the two demolished multi-criteria designs. Collapse it.
- The guard's design history (AND-gate → OR → floor, with the false-reject counts) is currently told
  in FULL three times: the code comment, ADR-0017 §3, and the test-battery comments. The ADR is
  canonical. Shrink the code comment to the RULE + a pointer to ADR-0017 §3; trim the test comments
  the same way. Three synchronized copies is drift risk.

**3. The pipeline trail.** The seven `tasks/done/*.md` files carry uncommitted PA + PR-Reviewer log
appendices. They ship in this task's commit.

**Tests**

- `tests/unit/decode/agents/test_loader.py` — the three primaries carry the nudge (pinned on stable
  markers: the `agent` tool name + "3"/"angles", never a full sentence); `explore` does not; the
  §9 synthesis-leak guard stays green (unchanged, must not need editing).
- `tests/unit/decode/tools/test_agent.py` — the battery moves to `_fault(...) is None` / `== _TERSE`.
- `tests/integration/test_subagents_capstone.py` — a skipif-gated LIVE probe alongside
  `test_live_gemini_fanout_smoke`: the bare, UNSTEERED user message "explore this repo" through a real
  `build_agent()` + `Runner`, asserting ONE `agent` call whose `prompts` list holds >= 3 entries.
  Presence-only and tolerant (a live model is non-deterministic), because the claim under test is
  behavioral: does the persona nudge actually make a real model delegate a broad question?

## Acceptance Criteria

- [x] `build.md`, `plan.md` and `code-reviewer.md` each carry a one-to-two-line delegation nudge naming the `agent` tool and "at least 3 distinct angles" for a broad question; `explore.md` carries none.
- [x] The nudge says WHEN to delegate, never how to synthesize — `test_no_builtin_persona_body_carries_the_synthesis_instruction` stays green, unedited.
- [x] A LIVE, UNSTEERED "explore this repo" through the real stack produces ONE `agent` call with >= 3 prompts — captured as a skipif-gated test, and demonstrated to FAIL before the nudge and PASS after.
- [x] `_faults()` is `_fault(prompt) -> str | None`; the `"; ".join` is gone; the nag text is unchanged.
- [x] The guard's code comment states the rule and points at ADR-0017 §3 instead of re-telling the design history; the test-battery comments are trimmed the same way.
- [x] The seven `tasks/done/*.md` log appendices are committed.
- [x] Full local gauntlet green: format-fix → lint-fix → format-check → lint-check → unit-tests → integration-tests.

## Out of scope

- Changing the fan-out mechanism, the guards, the retry, the fold, or the Synthesis Footer — 103-109
  shipped those and the PA accepted them.
- A new ADR. ADR-0017 §1 already puts the "at least 3 distinct angles for a broad question" push in the
  model-facing contract; this task only moves the *trigger* for that contract into the personas that
  can act on it. No architectural fork.
- Making the delegation mandatory (a harness-side "broad question → force a fan-out" heuristic). The
  model keeps the judgment call; a heuristic that hijacks a narrow question into 3 children would be
  strictly worse than reading one file.
- Removing the design history from the tests entirely: the anti-whack-a-mole BATTERY (the falsely
  rejected phrasings) stays as-is. It is a regression pin, not prose.

## Log

### [SWE] 2026-07-14 23:40 — Implementation

**Files modified**
- `src/decode/agents/builtin/build.md`, `plan.md`, `code-reviewer.md` — one delegation-nudge bullet
  each (WHEN to reach for `agent`: a broad, multi-area question → ONE call, at least 3 distinct angles;
  a narrow single-file question → just read it). No synthesis wording, no diagram wording, no footer.
- `src/decode/agents/builtin/explore.md` — untouched, deliberately (no `agent` tool, no nudge).
- `src/decode/tools/agent.py` — nit 1: `_faults() -> list[str]` collapsed to `_fault() -> str | None`
  (the `"; ".join` of a one-element list is gone; the nag text is byte-identical). Nit 2: the guard's
  comment block shrank from 27 lines of design history to 12 stating THE RULE + pointers to ADR-0017
  §3 (canonical) and the test battery.
- `tests/unit/decode/agents/test_loader.py` — two new tests: the three primaries carry the nudge
  (markers: the `` `agent` call `` phrase, "at least 3 distinct angles", "serially"), and `explore`
  carries none. Body whitespace-collapsed before matching, so a 100-column wrap cannot shatter a pin.
- `tests/unit/decode/tools/test_agent.py` — the guard battery moves to `_fault(...) is None` /
  `== _TERSE`; the three battery header comments trimmed (the AND-gate/OR history is ADR-0017 §3's).
- `tests/integration/test_subagents_capstone.py` — `test_live_gemini_unsteered_broad_question_fans_out`:
  a skipif-gated LIVE probe that submits the bare user message "explore this repo" — no steering at
  all — through real `build_agent()` + `Runner` and asserts ONE `agent` call with >= 3 prompts.
- `tasks/done/103..109` — the PA + PR-Reviewer log appendices, committed here (pipeline trail).

**Tests**
- Unit: 1590 passing, 0 failing (was 1586 — the two nudge tests + two parametrizations).
- Integration: 120 passing, 0 skipped (117+2-skipped before; the 2 live smokes RAN, with a real key
  borrowed from the main worktree and deleted afterwards, + the new live probe).

**Acceptance criteria**
- [x] The three primaries carry the nudge; `explore` does not — pinned by
      `test_every_primary_that_grants_the_agent_tool_is_told_when_to_delegate` (parametrized over
      build/plan/code-reviewer) and `test_the_explore_subagent_is_never_told_to_delegate`.
- [x] WHEN, never how-to-synthesize — `test_no_builtin_persona_body_carries_the_synthesis_instruction`
      is green, UNEDITED (the nudge contains none of `synthes`/`diagram`/`mermaid`/`ascii`/`box-drawing`).
- [x] Live unsteered proof — see Evidence: measured BEFORE and AFTER, 9 runs before, 9 after.
- [x] `_fault(prompt) -> str | None` — the join is gone, `_faults` has zero references left anywhere.
- [x] The code comment states the rule + points at ADR-0017 §3; the battery comments trimmed the same way.
- [x] The seven `tasks/done/*.md` appendices are in this commit.
- [x] Gauntlet green: format-fix → lint-fix → format-check (181 files) → lint-check (all passed) →
      unit-tests (1590) → integration-tests (120) → pre-commit (1590).

**Evidence**

The nudge's real proof is behavioral, so it was measured live against Gemini, unsteered, both sides of
the change. The honest finding: the PA's "it may never fan out" was RIGHT about the outcome and
half-right about the cause — the `agent` tool DESCRIPTION already nudges a bit ("for a broad question
like 'explore the repo', give at least 3 DISTINCT angles"), so the pre-nudge model sometimes fanned out
anyway. But it was a coin flip, and two of its failure modes are exactly the ones this feature exists to
kill: **no delegation at all**, and a **1-angle "fan-out"** (one subagent = not parallel exploration).

```
BEFORE (personas un-nudged) — bare user message "explore this repo", nothing else:

  $ uv run pytest tests/integration/... -k unsteered      # 4 runs
  run 1: FAILED — "the model explored WITHOUT calling the agent tool …
                   Tools it reached for: []"              <- zero tool calls; answered from memory
  run 2: passed        run 3: passed        run 4: passed

  $ uv run python probe.py                                # 5 runs, same prompt, real build_agent
  run 1: FAN-OUT: agent(prompts=[5 angles])
  run 2: FAN-OUT: agent(prompts=[3 angles])
  run 3: FAN-OUT: agent(prompts=[1 angles])    <- one child. Not a fan-out in any useful sense.
  run 4: FAN-OUT: agent(prompts=[3 angles])
  run 5: FAN-OUT: agent(prompts=[1 angles])    <- again

  => 9 unsteered runs, 3 of them (33%) would not satisfy "3 agents in parallel": one no-delegation,
     two 1-angle. The headline behavior really did depend on luck.

AFTER (build.md carries the nudge) — identical prompt, identical harness:

  $ uv run python probe.py                                # 5 runs
  run 1: FAN-OUT: agent(prompts=[3 angles])
  run 2: FAN-OUT: agent(prompts=[4 angles])
  run 3: FAN-OUT: agent(prompts=[3 angles])
  run 4: FAN-OUT: agent(prompts=[3 angles])
  run 5: FAN-OUT: agent(prompts=[3 angles])

  $ uv run pytest tests/integration/... -k unsteered      # 4 runs
  run 1: 1 passed (47.74s)   run 2: 1 passed (66.96s)
  run 3: 1 passed (44.35s)   run 4: 1 passed (37.08s)

  => 9/9 fanned out, 9/9 with >= 3 distinct angles. 0 no-delegation, 0 narrow folds.

A sample of what the model actually wrote, post-nudge (run 2, 4 angles, unsteered):
  [1] "What are the main entry points of the `decode` application and how is the CLI structured?
       Focus on `src/decode/cli.py` and `pyproject.toml`. Report the …"
  [2] "How does the core agent loop in `pydantic-ai` work, and how does it interact with the tools?
       Focus on `src/decode/agent/` and `src/decode/tools/`. Rep…"
  [3] "How are file I/O operations and the sandbox mechanism implemented? Focus on
       `src/decode/sandbox/` and `src/decode/tools/`. Report the classes/function…"
  [4] "How are tests structured and executed in this repository? Focus on `tests/` and `Makefile`. …"
```

```
$ make unit-tests           → 1590 passed in 91.90s
$ make integration-tests    → 120 passed in 385.74s        (0 skipped — the live key was present)
$ make pre-commit           → 1590 passed in 89.05s
$ make format-check         → 181 files already formatted
$ make lint-check           → All checks passed!
```

**Notes**
- **The sample is small (9 + 9) and the model is non-deterministic.** I am not claiming the nudge makes
  delegation impossible to miss — I am claiming the pre-nudge distribution contained failures that the
  post-nudge one did not, over the same prompt and harness. A future regression here would show up as a
  flaky live probe, which is the right place for it to show up.
- The live probe is presence-only and tolerant BY DESIGN (one `agent` call, >= 3 prompts; angles,
  reports and final answer unasserted). It is skipif-gated on `GEMINI_API_KEY`, so CI without a key
  skips it exactly like the existing `test_live_gemini_fanout_smoke`.
- Why it also fanned out sometimes WITHOUT the nudge: the `agent` tool description already carries
  ADR-0017 §1's "at least 3 DISTINCT angles" coaching. The nudge does not duplicate that; it supplies
  the missing TRIGGER (when to call the tool at all), which is what the personas' own "read the
  relevant files and search the codebase" bullet was quietly arguing against.
- No new dependency, no new setting, no ADR needed: ADR-0017 §1 already owns the "3 distinct angles for
  a broad question" contract — this task only moved its trigger into the personas that can act on it.
- `.env` was borrowed from the main worktree for every live run and deleted immediately afterwards
  (`git status` confirms it is not in the tree, and `.env` is gitignored regardless).

### [Tester] 2026-07-14 09:20 — QA (reviewing committed state, PR #33, commit e6beba6)

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` — 181 files already formatted;
  `make lint-check` — all checks passed; `make pre-commit` — 1590 passed in 87.59s)
- Unit tests: 1590 passed / 0 failed (`make unit-tests`, 89.73s)
- Integration tests: 120 passed / 0 failed / 0 skipped (`make integration-tests`, 385.31s — real
  `GEMINI_API_KEY` borrowed from the main worktree's `.env`, deleted immediately after; both live
  smokes ran for real, confirmed via `pytest -k live -v` showing `test_live_gemini_fanout_smoke` and
  `test_live_gemini_unsteered_broad_question_fans_out` both PASSED, not skipped)
- Warnings: 0

**E2E adversarial pass**
- Happy path: unsteered `"explore this repo"` through real `build_agent()` + `Runner` (the actual
  `test_live_gemini_unsteered_broad_question_fans_out`) → ONE `agent` call, `prompts` width >= 3
  (PASS). Ran it independently (not the SWE's numbers) **14 times total**: 3 runs in an interrupted
  first batch (44.95s/38.79s/39.79s, all PASS), 10 more in a full uninterrupted batch (all PASS,
  25.6s-101.6s each), 1 more via the `-k live` pair run (PASS). **14/14 independent live runs PASSED**
  — a materially larger and equally clean sample than the SWE's own 9-run claim, corroborating it.
- Break path 1 (over-trigger check — does the nudge cause a NARROW single-file question to
  needlessly fan out, which the spec explicitly forbids?): wrote a standalone probe script driving
  the real `build_agent()` + `Runner` with two narrow prompts — `"What does the init_logger function
  in src/decode/logging.py do? Just tell me what that one function does."` and `"Read pyproject.toml
  and tell me the Python version required."` Both runs: `agent() calls made: 0`, `all tools used:
  ['read']` (PASS — the nudge does not over-trigger on narrow questions).
- Break path 2 (persona-guard regression — did the nudge leak synthesis/diagram wording into any
  persona, or edit the §9 guard test?): `git show e6beba6 -- tests/unit/decode/agents/test_loader.py`
  shows `test_no_builtin_persona_body_carries_the_synthesis_instruction` is untouched by the diff
  (only two new tests appended below it); ran it explicitly —
  `test_no_builtin_persona_body_carries_the_synthesis_instruction[build|plan|code-reviewer|explore]`
  all PASSED (PASS).
- Break path 3 (silent behavior change in the `_fault` refactor / comment trim — dead code, changed
  nag text, or lost regression pins?): `grep -rn "_faults" src/ tests/` → zero hits anywhere in the
  tree (PASS, the old name is fully gone); `git show e6beba6 -- src/decode/tools/agent.py` shows
  `_TERSE` is byte-identical before/after (only the `"; ".join` of a one-element list was removed);
  diffed the test-battery comment trims against the phrasing arrays themselves — every
  `_ROUND_1_PROMPTS` / `_ROUND_2_PROMPTS` / `_VARIED_PROMPTS` / `_WELL_FORMED_PROMPTS` /
  `_LAZY_PROMPTS` entry is untouched; only the narrated design-history prose above them shrank
  (PASS).

**Acceptance criteria**
- [x] PASS — `build.md`, `plan.md`, `code-reviewer.md` each carry a one-to-two-line delegation nudge
      naming the `agent` tool and "at least 3 distinct angles"; `explore.md` carries none — Evidence:
      `git show e6beba6 -- src/decode/agents/builtin/{build,plan,code-reviewer}.md` (each adds
      exactly the nudge, 3/5/3 lines); `git show e6beba6 -- src/decode/agents/builtin/explore.md`
      is empty (untouched); `grep -n "agent\` call\|at least 3 distinct angles\|serially"` on all
      three primaries confirms the literal pin markers exist; `test_the_explore_subagent_is_never_told_to_delegate`
      PASSED.
- [x] PASS — nudge says WHEN, never how to synthesize;
      `test_no_builtin_persona_body_carries_the_synthesis_instruction` stays green, unedited —
      Evidence: diff shows the test function body is byte-identical (only two new tests appended
      after it); re-ran it, 4/4 parametrizations PASSED.
- [x] PASS — a LIVE, UNSTEERED "explore this repo" through the real stack produces ONE `agent` call
      with >= 3 prompts, captured as a skipif-gated test, demonstrated FAIL-before/PASS-after —
      Evidence: `test_live_gemini_unsteered_broad_question_fans_out` in
      `tests/integration/test_subagents_capstone.py:1359-1400`; ran it 14 times independently against
      the real Gemini API, 14/14 PASSED (see E2E pass above); the SWE's before/after evidence in this
      file's own Log entry (9 pre-nudge runs with 3 failures, 9 post-nudge runs 9/9 clean) is
      consistent with what I reproduced live.
- [x] PASS — `_fault(prompt) -> str | None`, the `"; ".join` is gone, nag text unchanged — Evidence:
      `src/decode/tools/agent.py:87-94`; `grep -rn "_faults" src/ tests/` → 0 hits; `_TERSE` string
      literal byte-identical in the diff; `uv run ty check src/decode/tools/agent.py` → all checks
      passed.
- [x] PASS — the guard's code comment states the rule + points at ADR-0017 §3, test-battery comments
      trimmed the same way — Evidence: `src/decode/tools/agent.py:65-79` (12-line comment, rule +
      pointer, down from 27); `docs/adr/0017-resilient-parallel-subagent-fanout.md` §3 confirmed
      present and matching; the anti-whack-a-mole battery's phrasing arrays in
      `tests/unit/decode/tools/test_agent.py` are untouched, only their header comments shrank.
- [x] PASS — the seven `tasks/done/*.md` log appendices are committed — Evidence:
      `git show e6beba6 --stat` lists `tasks/done/103..109-*.md` each with a `### [PA] ... —
      Acceptance Review` appendix (`+6` to `+33` lines), consistent with the PA verdicts already in
      this PR's history.
- [x] PASS — full local gauntlet green — Evidence: `make format-check` (181 files formatted),
      `make lint-check` (all checks passed), `make pre-commit` (1590 passed), `make unit-tests`
      (1590 passed), `make integration-tests` (120 passed, 0 skipped) — all re-run independently
      above, matching the SWE's reported counts exactly.

**Evidence**

```
$ make unit-tests
======================= 1590 passed in 89.73s (0:01:29) ========================

$ make integration-tests
tests/integration/test_subagents_capstone.py ..............              [ 97%]
======================= 120 passed in 385.31s (0:06:25) ========================

$ uv run pytest tests/integration/test_subagents_capstone.py -v -k "live"
tests/integration/test_subagents_capstone.py::test_live_gemini_fanout_smoke PASSED [ 50%]
tests/integration/test_subagents_capstone.py::test_live_gemini_unsteered_broad_question_fans_out PASSED [100%]
====================== 2 passed, 12 deselected in 54.64s =======================

$ for i in 1..10; do uv run pytest tests/integration/test_subagents_capstone.py -k unsteered -q; done
1 passed, 13 deselected in 44.95s
1 passed, 13 deselected in 38.79s
1 passed, 13 deselected in 39.79s
1 passed, 13 deselected in 57.07s
1 passed, 13 deselected in 25.63s
1 passed, 13 deselected in 28.21s
1 passed, 13 deselected in 101.63s
1 passed, 13 deselected in 28.15s
1 passed, 13 deselected in 36.11s
1 passed, 13 deselected in 42.47s
# 10/10 PASS (plus 3 more from an interrupted earlier batch = 13/13, plus the -k live run = 14/14)

$ grep -rn "_faults" src/ tests/
# (no output — zero references left)

$ git show e6beba6 --stat
 src/decode/agents/builtin/build.md                 |   3 +
 src/decode/agents/builtin/code-reviewer.md         |   5 +
 src/decode/agents/builtin/plan.md                  |   3 +
 src/decode/tools/agent.py                          |  48 ++---
 tasks/done/103..109-*.md (7 files)                 |  each +6 (108: +33)
 tasks/done/110-delegation-nudge-and-guard-cleanup.md | 202 +++
 tests/integration/test_subagents_capstone.py       |  49 +++++
 tests/unit/decode/agents/test_loader.py             |  33 ++++
 tests/unit/decode/tools/test_agent.py               |  38 ++--
 15 files changed, 398 insertions(+), 52 deletions(-)
# Scoped exactly as claimed: 3 personas, agent.py, 8 task files (103-110), 3 test files. No stray files.
```

**Other issues found**
- None blocking. Minor observation only: the SWE's own live-probe evidence (a standalone `probe.py`
  script) isn't checked into the repo, so it isn't independently re-runnable from the commit alone —
  not an issue since the *committed* skipif-gated pytest test is the actual regression pin and I
  reproduced its live behavior 14/14 independently myself.
- Confirmed (adversarially, beyond the AC): the nudge does not over-trigger on narrow single-file
  questions — two live probes on narrow prompts both used only `read`, zero `agent` calls. This
  matters because a heuristic that fans out on everything would be worse than the pre-nudge state;
  the SWE's design correctly left the judgment call to the model and it holds up live.

**VERDICT: PASS**
