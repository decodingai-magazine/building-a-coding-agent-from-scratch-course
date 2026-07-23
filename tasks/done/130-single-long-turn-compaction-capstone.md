---
id: 130
feature: fix-compaction
status: done
---

# Capstone regression: the original single-long-turn session shape compacts end to end

The bug was discovered on a real session (1 user prompt + 63 tool messages in ONE turn, no
compaction record ever). Each fix task proved its slice in isolation; this capstone proves
the composed behavior through the real `AgentTurnHandler` at the session shape that exposed
the bug — so the bug class cannot silently return.

Depends on: 125, 126, 127, 128. (129 not required — the capstone uses the Model-instance
seam, no network.)

## Scope

Extend `tests/integration/test_compaction_capstone.py` (or add a sibling test in it) driving
`AgentTurnHandler` with a `FunctionModel`/`TestModel` that reproduces the shape: one turn,
one user prompt, ~15+ tool call/return rounds with fat tool outputs, per-response populated
usage, and a small configured window (monkeypatched settings), summarizer = stub Model.
Three composed assertions:

1. **Auto full compaction fires on one long turn** (root cause 1+2 together): at would-stop
   the cascade triggers off the LAST response's usage, `split_tail` cuts at a
   `ModelResponse` boundary inside the turn, history becomes `[summary, *tail]` with every
   tool pair intact, a `compaction` line is persisted, `ContextCompacted` is emitted.
2. **Gauge lifecycle**: during the turn `last_input_tokens` equals the last response's
   `input_tokens + cache_read_tokens` (not the cumulative sum); immediately post-compaction
   it equals the chars≈/4 estimate of the kept history; a follow-up leg overwrites it with
   the provider number.
3. **Micro tier on the same shape**: with usage tuned between the micro and full levels,
   `ContextMicrocompacted` fires with `elided > 0` and the JSONL log keeps full fidelity
   (no compaction line, cursor unmoved).

## Acceptance criteria

- [x] The capstone test fails when any ONE of the four fixes (125/126/127/128) is reverted
      (spot-verified by the SWE during development — e.g. `git stash` the split_tail hunk and
      watch it go red; note the check in the task log).
- [x] Assertion set 1-3 above implemented as described, through the public handler surface
      (no reaching into pydantic-ai internals beyond message construction).
- [x] Runs offline in `make integration-tests` / `make ci` (Model-instance seam only, no
      keys), consistent with the existing capstone's conventions.
- [x] `make ci` green.

## User stories

### Story: The original failing session, replayed green
1. Developer runs `make integration-tests`.
2. The capstone reconstructs the 2026-07-22 session shape (one turn, dozens of tool
   messages) and asserts a compaction record NOW appears where the real log had none.

### Story: A future refactor cannot silently regress compaction
1. A future task refactors `split_tail` or the usage plumbing subtly wrong.
2. `make ci` fails on this capstone with a named assertion (boundary / gauge / tier),
   pointing at the exact regressed slice.

## Out of scope

- Manual-QA playbook updates (skill `manual-e2e-qa`) — separate docs surface.
- Any new production code: this task ships tests only (plus trivial test helpers).

## Log

### [SWE] 2026-07-23 09:45 — Implementation

**Files modified**
- `tests/integration/test_compaction_capstone.py` — added the single-long-turn capstone: a
  `_LongTurnModel` (FunctionModel forcing per-response usage, one prompt → 16 inline `sleep`
  rounds in ONE leg), a `_synthetic_long_turn_history` builder, and three tests pinning the
  composed ADR-0018 fix on the original bug shape. Trivial test helpers only; no production code.

**Tests** (offline, Model-instance seam only — no keys, no network, no skipif)
- `test_single_long_turn_primitives_on_the_original_shape` — synthetic one-turn history (no run):
  `split_tail` cuts at a `ModelResponse` boundary inside the turn (125); `_leg_input_tokens` is the
  LAST response's `input + cache_read`, not the cumulative sum nor the first response (126);
  `compact()` → `CompactOutcome.COMPACTED` then `NOTHING_TO_COMPACT` (127); post-compaction gauge ==
  `estimate_history_tokens([summary, *tail])` (128).
- `test_single_long_turn_auto_full_compaction` — real `AgentTurnHandler` + `Runner` + FunctionModel
  driving ONE long turn: at would-stop the cascade triggers off the last response's usage (125==95+30,
  `before_tokens < mid*N`), history becomes `[summary, *tail]` opening on a `ModelResponse` with every
  pair intact + no orphan, one `compaction` line persisted, `ContextCompacted` emitted; gauge drops to
  the kept-history estimate then a follow-up leg overwrites it with the provider number (128); resume
  replays the compacted history.
- `test_single_long_turn_microcompaction` — same shape, usage tuned between micro (90) and full (120):
  one `ContextMicrocompacted` with `elided > 0`, `before_tokens == 95` (last-response, not cumulative),
  no full compaction, no `compaction` line on disk, cursor unmoved, placeholder in memory only.
- Integration: 108 passed, 16 skipped (docker-only) — `make integration-tests`.
- Full suite: 2327 passed, 16 skipped — `make ci` (CI_EXIT=0).

**Acceptance criteria**
- [x] Capstone goes RED when any ONE of the four fixes is reverted — spot-verified (see Evidence).
- [x] Assertion sets 1-3 implemented through the public handler surface (message construction only).
- [x] Runs offline in `make integration-tests` / `make ci`.
- [x] `make ci` green.

**Evidence — RED spot-checks** (revert one fix's hunk, run `-k single_long_turn`, restore)
- **125** (`_is_compaction_boundary` → user-turn-only snap): all 3 RED. First failure
  `test_..._primitives ... assert 0 < split < len(history)` (split collapses to 0); micro fails
  `assert len(micro_events) == 1` (elided 0).
- **126** (`_leg_input_tokens` → cumulative sum): all 3 RED. `assert _leg_input_tokens(history) ==
  125` got `1085`; auto `before_tokens == 125` got `1085`; micro `len(micro_events) == 1` got 0
  (cumulative pushes past the full line).
- **127** (`compact()` → bool return): primitives RED — `assert outcome is CompactOutcome.COMPACTED`
  got `True`. (Auto path behaves identically on the happy path, so only the enum assertion catches it.)
- **128** (drop the post-compaction `_last_input_tokens = estimate_history_tokens(...)` line):
  primitives + auto-full RED — `assert handler.last_input_tokens == estimate_history_tokens(compacted)`
  got `125 != 76` (gauge stuck on the pre-compaction provider number).
- After each check the file was restored; final `git diff src/` is empty (tests-only) and all 4
  capstone tests pass.

**Evidence — green**
```
$ uv run pytest tests/integration/test_compaction_capstone.py -v -k single_long_turn
test_single_long_turn_primitives_on_the_original_shape PASSED
test_single_long_turn_auto_full_compaction PASSED
test_single_long_turn_microcompaction PASSED
3 passed, 1 deselected

$ make ci  →  CI_EXIT=0  →  2327 passed, 16 skipped in 463.00s
```

**Notes**
- Distinct value vs the existing `test_compaction_capstone_micro_full_persist_resume` (multi-turn):
  this reproduces the SINGLE-long-turn shape (one prompt, 16 tool rounds in one leg) that has exactly
  one user-turn boundary at index 0 — the shape the original bug silently no-op'd on.
- `NOTHING_TO_COMPACT` is pinned on a trivial fully-fitting history: a second `compact()` on the
  freshly-compacted `[summary, *tail]` still re-compacts here (the tiny keep-recent budget doesn't
  fit summary+tail), so it is not a reliable no-op signal.
- The during-turn "gauge == last-response, not cumulative" assertion is observed in the MICRO real
  run (micro never reseeds the gauge) and in the synthetic `_leg_input_tokens` check; the auto-FULL
  run reseeds to the estimate, so it cannot show the raw last-response number post-turn.

### [Tester] 2026-07-23 00:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 308 files formatted; `ruff check` all
  passed; `make pre-commit` → 2219 unit tests passed)
- Unit tests: 2219 passed / 0 failed
- Integration tests: 108 passed / 16 skipped (docker daemon unreachable, expected) / 0 failed
- Full suite (`uv run pytest`, equivalent to `make ci`'s test step; also ran `uv lock --check`
  separately, resolved clean): 2327 passed / 16 skipped / 0 failed, 478.83s
- Warnings: 0 (pytest configured `filterwarnings=["error"]`; a warning would surface as a failure —
  none did)
- `git diff --stat -- src/` empty throughout (confirmed before and after every revert experiment) —
  tests-only, as scoped.
- code-review plugin: enabled in `.claude/settings.json`, but its command (`commands/code-review.md`)
  operates on a GitHub PR via `gh pr view/diff/comment` — this is a file-mode task with no PR yet
  (uncommitted work), so the plugin has nothing to attach to. Performed the equivalent manual
  diff/comment/history review myself in its place (see below).

**E2E adversarial pass**
- Happy path: `uv run pytest tests/integration/test_compaction_capstone.py -v` → all 4 tests PASS
  (the pre-existing multi-turn capstone + the 3 new single-long-turn tests) (PASS)
- Break path 1 (revert ADR-0018 fix 125 — `_is_compaction_boundary` restored to the OLD
  user-turn-only predicate): edited `src/decode/context/compaction.py` to
  `isinstance(message, ModelRequest) and any(isinstance(part, UserPromptPart) ...)`, ran
  `-k single_long_turn` → all 3 new tests RED. First failure reproduced verbatim:
  `assert 0 < split < len(history)` → `assert 0 < 0` (split collapses to 0), exactly the failure
  line the SWE's log claims. Restored file byte-identical (`git diff --stat -- src/` empty), reran
  → 3 passed. (PASS — regression correctly caught)
- Break path 2 (revert ADR-0018 fix 126 — `_leg_input_tokens` restored to a cumulative-sum walk
  instead of last-populated-response): edited `src/decode/agent/loop.py`, ran `-k single_long_turn`
  → all 3 RED, `assert full_events[0].before_tokens == _FULL_LAST_INPUT + _FULL_LAST_CACHE` →
  `assert 1085 == 125`, matching the SWE's claimed `1085` exactly. Restored byte-identical, reran →
  3 passed. (PASS — regression correctly caught)
- Break path 3 (revert ADR-0018 fix 128 — dropped the post-compaction
  `_last_input_tokens = estimate_history_tokens(...)` line in `compact()`): primitives + auto-full
  went RED (`assert handler.last_input_tokens == estimate_history_tokens(compacted)` →
  `125 == 78`, same failure shape as the SWE's claimed `125 != 76`; exact number differs slightly
  run-to-run due to non-deterministic tool_call_id/timestamp string lengths feeding the chars≈/4
  estimate, immaterial to the assertion), microcompaction test correctly stayed green (128 doesn't
  touch the micro path, matching the SWE's note). Restored byte-identical, reran → 4/4 passed.
  (PASS — regression correctly caught; 3 of the 4 claimed spot-checks independently reproduced,
  exceeding the required minimum of 2)
- Offline / no-keys check: `env -i PATH="$PATH" HOME="$HOME" uv run pytest
  tests/integration/test_compaction_capstone.py -k single_long_turn` (no GEMINI/OPENROUTER/MODAL/
  OPIK keys in env at all) → 3 passed, 1 deselected. (PASS — no hidden key dependence)
- Determinism check: ran the 3 single-long-turn tests twice back to back → `3 passed` both times,
  identical outcome. (PASS)
- No `skipif` markers found in the file (`grep -n "skipif" tests/integration/test_compaction_capstone.py`
  → no matches); module docstring states "Fully offline — no network, no API key, no skipif."
  (PASS)

**Acceptance criteria**
- [x] PASS — The capstone test fails when any ONE of the four fixes (125/126/127/128) is reverted —
      Independently reproduced 3/4 (125, 126, 128) with matching or equivalent failure signatures
      (see Break paths 1-3 above); the 4th (127, `compact()` bool-vs-enum) is a straightforward enum
      identity assertion (`outcome is CompactOutcome.COMPACTED`) that trivially fails against a bare
      `True`/`False` return, read and confirmed by inspection at
      `tests/integration/test_compaction_capstone.py:711`.
- [x] PASS — Assertion sets 1-3 implemented through the public handler surface — `handler.compact()`
      (public method, `src/decode/agent/loop.py:314`) and `handler.last_input_tokens` (public
      property) are the only handler entry points touched; `session_log.load()` (module function,
      reads `path.read_text()` from disk) is used for the resume assertion, not handler internals.
      The one non-message-construction touch is `response._usage = usage` inside
      `_LongTurnModel.request_stream` (`tests/integration/test_compaction_capstone.py:543`) — a
      private `StreamedResponse` attribute set to force per-response usage; this is the SAME
      technique the pre-existing (already-accepted) multi-turn capstone's `_ScriptedModel` uses
      (line 192), so it is an established, not new, seam — noted, not blocking.
- [x] PASS — Runs offline in `make integration-tests` / `make ci` — ran with `env -i` (no keys at
      all) above; `make integration-tests` → 108 passed, 16 skipped (docker-only); full
      `uv run pytest` → 2327 passed, 16 skipped, 0 warnings.
- [x] PASS — `make ci` green — ran `uv lock --check` (resolved clean) + `format-check` + `lint-check`
      + full `uv run pytest` (the three steps `make ci` chains) independently; all green, matching
      the SWE's claimed `2327 passed, 16 skipped`.

**Evidence**
```
$ uv run pytest tests/integration/test_compaction_capstone.py -v -p no:cacheprovider
test_compaction_capstone_micro_full_persist_resume PASSED
test_single_long_turn_primitives_on_the_original_shape PASSED
test_single_long_turn_auto_full_compaction PASSED
test_single_long_turn_microcompaction PASSED
4 passed in 1.01s

$ make integration-tests
108 passed, 16 skipped in 344.78s (0:05:44)

$ uv run pytest -q   (full suite, equivalent to make ci's test step)
2327 passed, 16 skipped in 478.83s (0:07:58)

$ uv lock --check
Resolved 210 packages in 3ms

$ git diff --stat -- src/
(empty — confirmed before and after every revert experiment)
```

**Other issues found**
- `_LongTurnModel.request_stream` / `_ScriptedModel.request_stream` write to the private
  `StreamedResponse._usage` attribute to force per-response usage numbers. Not a new issue (the
  established pattern from the prior task's capstone), and there is no public FunctionModel API to
  set arbitrary usage on a streamed response, so this is a reasonable, precedented test seam — flagged
  for visibility only, not a blocker.
- Minor: the exact RED-spot-check numbers for fix 128 (`125 != 76` in the SWE's log vs `125 == 78`
  reproduced here) differ slightly because the estimate is sensitive to non-deterministic
  tool_call_id / timestamp string lengths baked into the synthetic/real messages; the assertion
  shape and root cause are identical either way. Worth a one-line note in the log if this file is
  revisited, but not a functional problem.

**VERDICT: PASS**

### [PA] 2026-07-23 — Acceptance Review (feature fix-compaction, PR #50)

**VERDICT: ACCEPT**

Walked the whole feature from the user's perspective against the Tasks Plan (tasks 125-130,
ADR-0018): the original single-long-turn session shape now auto-compacts (capstone 4/4 green,
re-run); the Context Gauge reads the last response's usage and drops to the kept-history
estimate the instant compaction lands (footer reads `handler.last_input_tokens`, app.py:587);
`/compact` gives three honest distinct lines (failure copy names `.decode/logs/decode.log`,
no enum jargon leaked); all three providers summarize via `compaction_model=agent.model`
(wiring test gemini/openrouter/modal green, re-run); glossary terms (Compaction Boundary /
Compaction Outcome / Context Gauge) and ADR-0018 land verbatim-consistent with code.
Non-blocking nit noted for a future cleanup: stale "user-turn boundary, ADR-0006 §5" comment
at tests/integration/test_compaction_capstone.py:413. Hand off to the PR Reviewer.

### [PR Reviewer] 2026-07-23 — Review (PR #50, branch feat/fix-compaction)

**VERDICT: NO BLOCKERS**

Reviewed 21 files, ~3092 insertions (commits f348ea2..3284e93) across all dimensions
(performance, clean code, tests, standards, doc discipline, simplicity). Findings:
- Blockers: 0
- Nits: 2 (appended to the PR description; also posted as caveman-review comments)

**Nits**
1. [Clean code] tests/unit/decode/context/test_compaction.py:375 — assert subsumed by the
   next line (`_has_tool_return` is already False for a ModelResponse); drop it.
2. [Documentation discipline] tests/integration/test_compaction_capstone.py:412-413 — stale
   comment "snaps to a user-turn boundary, ADR-0006 §5"; should read Compaction Boundary,
   ADR-0006 §5 as amended by ADR-0018 §1 (matches PA's noted candidate).

QA spot-check: format-check + lint-check clean; touched suites green (237 unit + 64
capstone/e2e/grounding passed). Doc discipline exemplary: ADR-0018 present, ADR-0006 status
amended, glossary carries Compaction Boundary (redefined) + Compaction Outcome. Pipeline may
advance to hand-off.

### [On-Call] 2026-07-23 00:10 — CI Failure

**Failed step:** CI → ci → Lint, format & test (GitHub Actions run `29967544312`, PR #50,
commit `ff8bd2a`)

**Error**
```
tests/integration/test_compaction_capstone.py:703: in test_single_long_turn_primitives_on_the_original_shape
    handler = AgentTurnHandler(
>           build_agent(),
...
src/decode/agent/factory.py:113: in _build_model
    provider=GoogleProvider(
self = <GoogleProvider object>
api_key = None, ...
>                   raise UserError(
                        'Set the `GOOGLE_API_KEY` environment variable or pass it via `GoogleProvider(api_key=...)`'
                        ' to use the Google Generative Language API.'
                    )
E       pydantic_ai.exceptions.UserError: Set the `GOOGLE_API_KEY` environment variable or pass it via `GoogleProvider(api_key=...)` to use the Google Generative Language API.

1 failed, 2323 passed, 19 skipped in 450.24s (0:07:30)
```

**Root cause**
`test_single_long_turn_primitives_on_the_original_shape` (added in commit `3284e93`) calls
`build_agent()` directly (around line 703) without first faking the provider key — unlike
every other `build_agent()` call site in this file (`_long_turn_setup`,
`test_compaction_capstone_micro_full_persist_resume`), which do
`monkeypatch.setattr("decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"),
raising=False)` before constructing the agent. Locally this went unnoticed because a
developer's `.env` sets `LLM_PROVIDER=modal` (or similar), which `dotenv_values()` reads
directly regardless of `env -i`/`env -u` process-env scrubbing — so `_build_model` never
takes the `gemini` branch on a dev machine, including under the Tester's "offline / no-keys"
`env -i` check. CI has no `.env` at all, so `settings.llm_provider` defaults to `"gemini"`
(the field default), `_build_model` builds a real `GoogleProvider`, and with no key faked and
no `GOOGLE_API_KEY`/`GEMINI_API_KEY` in the environment, `pydantic_ai` raises `UserError`. This
is a real test bug (a missing monkeypatch line), not flake or infra — reproduced deterministically
locally with `env -u GEMINI_API_KEY -u GOOGLE_API_KEY LLM_PROVIDER=gemini uv run pytest
tests/integration/test_compaction_capstone.py::test_single_long_turn_primitives_on_the_original_shape`.

Fixing now — handing a fix task to the SWE (Refs #130 in file mode; PR #50).

### [SWE] 2026-07-23 — CI fix (On-Call task, PR #50)

**Files modified**
- `tests/integration/test_compaction_capstone.py` — in
  `test_single_long_turn_primitives_on_the_original_shape`, added the missing
  `monkeypatch.setattr("decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"),
  raising=False)` immediately before the `handler = AgentTurnHandler(build_agent(), ...)` block,
  matching `_long_turn_setup` and `test_compaction_capstone_micro_full_persist_resume`. Tests only —
  no production code (`git diff --stat -- src/` empty).

**Root cause** — This test called `build_agent()` without faking the provider key. With no `.env`
(CI) `settings.llm_provider` defaults to `"gemini"`, so `_build_model` built a real `GoogleProvider`
and raised `UserError` for the absent key. Local `.env` (`LLM_PROVIDER=modal`) is read by
`dotenv_values()` regardless of `env -i`/`env -u`, so it masked the bug for the SWE and Tester.

**Reproduction (CI-matching env)**
```
$ env -u GEMINI_API_KEY -u GOOGLE_API_KEY LLM_PROVIDER=gemini uv run pytest \
    tests/integration/test_compaction_capstone.py::test_single_long_turn_primitives_on_the_original_shape -q
# before: 1 failed (pydantic_ai UserError: Set the GOOGLE_API_KEY ...)
# after:  1 passed in 0.82s

$ env -u GEMINI_API_KEY -u GOOGLE_API_KEY LLM_PROVIDER=gemini uv run pytest \
    tests/integration/test_compaction_capstone.py -q
4 passed in 1.01s
```

**QA**
- `make format-check` — 308 files already formatted
- `make lint-check` — All checks passed
- `make unit-tests` — 2219 passed in 118.61s

**Notes** — Status left `in-progress` for On-Call to re-verify CI and close.

### [On-Call] 2026-07-23 00:16 — CI Resolution

SWE pushed the fix as commit `a73331d` on `feat/fix-compaction` (PR #50): added the missing
`monkeypatch.setattr("decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"),
raising=False)` line to `test_single_long_turn_primitives_on_the_original_shape` before its
`build_agent()` call, matching the pattern already used elsewhere in the file. Tests-only,
verified locally by the SWE with the CI-matching env command (red before, green after; whole
capstone 4 passed; unit 2219 passed).

Re-checked CI: run `29968423979` on commit `a73331d` — `completed success`
(https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course/actions/runs/29968423979).
Pipeline green. Closing task 130 again.
