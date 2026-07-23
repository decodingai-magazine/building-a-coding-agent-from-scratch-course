---
id: 129
feature: fix-compaction
status: done
---

# Compaction summarizer rides the Provider Seam — works on gemini, openrouter, AND modal

`context/compaction.py::_resolve_model` (lines 204-209) hardcodes `GoogleModel`: a Modal or
OpenRouter user without `GEMINI_API_KEY` gets a silently failing summarizer — full compaction
and `/compact` never succeed (surfaced as SUMMARIZER_FAILED since task 127). Human-approved
decision: the ACTIVE provider's configured model does the summarizing, routed through the
existing Provider Seam (`src/decode/agent/factory.py::_build_model`). No cross-provider
override knob.

This task implements ADR-0018 §5. Depends on: none hard (127 makes the failure visible;
this makes it not happen). Research fact for the SWE: `runtime/flow.py` wires NO
`AgentTurnHandler` and therefore no compaction — the ONLY wiring site is `tui/app.py:850-858`.
Record that in this task's log; do not add headless compaction here.

## Scope

- **Shape (watch the import direction — `agent.loop` imports `context.compaction`; a
  `context.compaction → agent.factory` import must be verified cycle-free before choosing
  it):** preferred, least-mechanism shape — `compaction` accepts a built
  `pydantic_ai.models.Model` and the call site provides it:
  - `summarize_for_compaction(..., model_or_settings=...)` narrows to a built `Model`
    (suggested param rename: `model=`; keep the Model-instance path — it is the tests'
    no-network seam). Delete the `Settings → GoogleModel` branch and the
    `GoogleModel`/`GoogleProvider` imports from `compaction.py`.
  - `AgentTurnHandler(compaction_model_or_settings=...)` narrows accordingly (suggested
    rename: `compaction_model=`); `None` still disables the cascade.
  - Wiring at `tui/app.py:857`: pass a built Model instead of `settings` — either the
    already-built agent's own model (zero extra construction, guaranteed same provider) or
    the output of a factory call exposed for this purpose. SWE picks; both satisfy the AC.
- The `Settings` union member disappears from compaction's public surface; update docstrings
  (`compaction.py` module header + `summarize_for_compaction`; `loop.py` handler docstring;
  the `tui/app.py:848` wiring comment) to reference ADR-0018 §5.
- Update every test constructing the handler/summarizer with `Settings`.

**Regression-test-first:** the failing tests below before the change.

## Acceptance criteria

- [x] Regression test (written first, fails on current code): with
      `settings.llm_provider = "openrouter"` (and no usable `GEMINI_API_KEY`), the summarizer
      model used for compaction is NOT a `GoogleModel` — no Google model/provider is
      constructed anywhere on the compaction path. — verified by
      `tests/unit/decode/tui/test_app_e2e.py::test_run_app_wires_the_active_providers_model_as_the_compaction_summarizer[openrouter-OpenAIChatModel-False]`
- [x] Same assertion for `llm_provider = "modal"` (OpenAI-compatible model over the modal
      client) and `"gemini"` (GoogleModel — unchanged behavior for the default provider). —
      the `modal-...-False` + `gemini-...-True` parametrizations of the same wiring test.
- [x] TUI wiring test: the handler's compaction model is the ACTIVE provider's built model
      (same class/config as the Provider Seam produces for `settings.llm_provider`). — the same
      parametrized test asserts `compaction_model is agent.model` for all three providers.
- [x] The Model-instance test seam still works: passing a `TestModel`/`FunctionModel`
      summarizes with no network, exactly as before. — `test_compaction.py` summarize tests
      (renamed `model=`) + `test_summarize_for_compaction_builds_no_provider_model`.
- [x] `compaction.py` no longer imports `GoogleModel`/`GoogleProvider`/`Settings`-for-model;
      no import cycle introduced (`make lint-check` + import of every touched module in tests). —
      verified by `test_compaction_module_imports_no_google_or_settings_for_model_construction`.
- [x] `memory/extract.py` untouched (its twin `_resolve_model` is explicitly out of scope).
- [x] `make format-check lint-check unit-tests` green.

## User stories

### Story: A Modal-only user compacts successfully with zero Google config
1. User runs decode with `LLM_PROVIDER=modal`, a Modal endpoint configured, and NO
   `GEMINI_API_KEY` in `.env`.
2. After a long session, user types `/compact`.
3. The summary call goes to the Modal endpoint (the model already serving the session);
   the `ContextCompacted` line renders. No log warning about a failed Gemini call.

### Story: An OpenRouter user's AUTO compaction works end to end
1. `LLM_PROVIDER=openrouter`; a session crosses the full threshold at would-stop.
2. The cascade summarizes via OpenRouter and rewrites history to `[summary, *tail]`; the
   JSONL log gains a `compaction` line; `--resume` replays the compacted state.

## Out of scope

- A summarizer model OVERRIDE knob (human-approved non-goal — active provider only).
- `memory/extract.py`'s Gemini hardcode (MEMORY.md write-back) — follow-up task if wanted.
- Headless compaction wiring in `runtime/flow.py` (none exists; out of this bug's scope).

## Log

### [SWE] 2026-07-23 — Implementation

**Files modified**
- `src/decode/context/compaction.py` — dropped `GoogleModel`/`GoogleProvider`/`Settings` imports and the `_resolve_model` helper; `summarize_for_compaction` now takes a built `model: Model` (param renamed `model_or_settings` → `model`); module + function docstrings reference ADR-0018 §5.
- `src/decode/agent/loop.py` — `AgentTurnHandler(compaction_model=...)` narrows `Model | Settings | None` → `Model | None` (param renamed `compaction_model_or_settings` → `compaction_model`); dropped the now-unused `Settings` import; `compact()` calls `summarize_for_compaction(..., model=self._compaction_model)`; docstring/comments updated (ADR-0018 §5).
- `src/decode/tui/app.py` — the ONE wiring site now passes `compaction_model=agent.model` (the ACTIVE provider's own built model) instead of `compaction_model_or_settings=settings`; wiring comment references ADR-0018 §5.
- `evals/harness/driver.py` — renamed kwarg at its handler wiring (already passed `agent.model`).
- `tests/unit/decode/tui/test_app_e2e.py` — added parametrized wiring regression `test_run_app_wires_the_active_providers_model_as_the_compaction_summarizer` (gemini/openrouter/modal); renamed the `fake_summarize` seam param.
- `tests/unit/decode/context/test_compaction.py` — renamed summarize calls to `model=`; added `test_summarize_for_compaction_builds_no_provider_model` (spies GoogleModel/GoogleProvider — never called) and `test_compaction_module_imports_no_google_or_settings_for_model_construction`.
- `tests/unit/decode/agent/test_loop.py`, `tests/unit/evals/regression/test_cases_grounding.py`, `tests/integration/test_opik_repl_trace.py`, `tests/integration/test_observability_capstone.py`, `tests/integration/test_compaction_capstone.py` — mechanical kwarg rename `compaction_model_or_settings=` → `compaction_model=`.

**Tests**
- Unit: 2219 passing, 0 failing (`make pre-commit` runs the full unit suite).
- Integration: 105 passing, 16 skipped (docker daemon not reachable in this env — pre-existing/environmental, unrelated to this change).

**Acceptance criteria**
- [x] openrouter → no GoogleModel on the compaction path — `test_app_e2e.py::test_run_app_wires_the_active_providers_model_as_the_compaction_summarizer[openrouter-OpenAIChatModel-False]`
- [x] modal + gemini variants — same test's `modal-...-False` / `gemini-...-True` cases
- [x] TUI wiring: handler's compaction model IS the active provider's built model (`compaction_model is agent.model`, all three providers)
- [x] Model-instance test seam intact — renamed `test_compaction.py` summarize tests + the no-provider-model spy test
- [x] `compaction.py` drops Google/Settings-for-model imports, no import cycle — `test_compaction_module_imports_no_google_or_settings_for_model_construction` + `make lint-check` clean
- [x] `memory/extract.py` untouched (verified: its `model_or_settings` / `_resolve_model` twin left as-is — out of scope)
- [x] `make format-check lint-check unit-tests` green

**Regression-test-first evidence**
Wiring test written first, confirmed RED on current code before the fix (kwarg `compaction_model` absent → captured `None`, `None is not agent.model`):
```
FAILED ...test_run_app_wires_the_active_providers_model_as_the_compaction_summarizer[gemini-GoogleModel-True]
FAILED ...[openrouter-OpenAIChatModel-False]
FAILED ...[modal-OpenAIChatModel-False]
E       AssertionError: assert None is OpenAIChatModel()
```
Green after the fix:
```
$ uv run pytest tests/unit/decode/context/test_compaction.py tests/unit/decode/agent/test_loop.py \
    "tests/unit/decode/tui/test_app_e2e.py::test_run_app_wires_the_active_providers_model_as_the_compaction_summarizer" -q
105 passed in 2.05s
```

**Evidence (e2e — the bug scenario: Modal-only user, zero Google config)**
```
$ uv run python - (build_agent with LLM_PROVIDER=modal, GEMINI_API_KEY="")
active provider model: OpenAIChatModel
handler compaction model: OpenAIChatModel
summary produced offline via a Model instance: True
OK: Modal-only user compacts with zero Google config
```

**Design notes**
- Chose the `agent.model` wiring (not a fresh `factory._build_model()` call): zero extra construction, guaranteed the same provider/model already serving the session, and keeps the import direction `tui → {agent, context}` — no `context.compaction → agent.factory` edge, so no cycle risk.
- Research fact confirmed & recorded: `runtime/flow.py` wires NO `AgentTurnHandler`, so there is no headless compaction cascade — `tui/app.py` is the ONLY wiring site. No headless compaction added (out of scope, per ADR-0018 §5 non-goals).
- `memory/extract.py` keeps its own Gemini-hardcoded `_resolve_model` and `model_or_settings` API deliberately — explicitly out of scope (ADR-0018 non-goals; the milestone1 capstone monkeypatch of `summarize_session(model_or_settings=...)` is that separate memory function, left untouched).

DO NOT commit — handing off to Tester.

### [Tester] 2026-07-23 01:58 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 308 files already formatted; `ruff check`: all checks passed; `make pre-commit` full unit suite: 2219 passed, 0 failed, 117.84s)
- Unit tests: 2219 passed / 0 failed
- Integration tests: 105 passed / 16 skipped (all 16 skips are pre-existing "docker daemon not reachable" — confirmed `docker info` fails in this env; unrelated to this diff) / 0 failed
- Warnings: 0 (repo-wide `filterwarnings=["error"]`; also reran the touched test files with `-W error::DeprecationWarning` — 135 passed, 0 warnings)

**E2E adversarial pass**
- Happy path (Modal-only user, empty `GEMINI_API_KEY`): built a real agent via `build_agent()` with `llm_provider="modal"` + modal creds + `gemini_api_key=""`, confirmed `agent.model` is `OpenAIChatModel`, then called `summarize_for_compaction` with a spy on `pydantic_ai.models.google.GoogleModel.__init__` around the call → summary produced offline, `GoogleModel` never constructed. PASS.
- Break path 1 (`agent.model` attribute reality check on pydantic-ai 1.95.1): `Agent(TestModel()).model` → `TestModel` instance (a real `Model`, not a string/proxy); confirmed the installed `pydantic-ai-slim==1.95.1` matches `uv.lock`. PASS.
- Break path 2 (state edge: `compaction_model=None` under a would-fire window): ran `tests/unit/decode/agent/test_loop.py::test_none_seam_disables_cascade_even_with_a_tiny_window` — patches `compaction_context_window_tokens=1` (would fire if wired) yet the unwired handler (`compaction_model` omitted → `None`) emits no `ContextCompacted`/`ContextMicrocompacted` event and writes no `compaction` line to the session log; also read `_maybe_auto_compact` (`loop.py:280-281`) — `if self._compaction_model is None: return` is the very first line, before any usage computation or INFO log. PASS — clean, no crash, no log spam.
- Break path 3 (failure mode: ACTIVE model raises mid-summarize): called `summarize_for_compaction(messages, model=FunctionModel(boom))` where `boom` raises `RuntimeError` (stand-in for e.g. a downed Modal endpoint) → returned `None` (warning logged with `exc_info=True`), no exception propagated to the caller — degrades to `CompactOutcome.SUMMARIZER_FAILED` at the `compact()` call site (`loop.py:333-334`), never raised mid-turn. Confirmed the `except Exception` in `compaction.py:128-132` is unconditional (not provider-specific) so this holds for whichever provider is active. PASS.
- Break path 4 (regression-test-first independent verification): `git stash push -- src/decode/context/compaction.py src/decode/agent/loop.py src/decode/tui/app.py evals/harness/driver.py` (old src, new tests), reran the wiring test → all 3 parametrizations RED with the exact claimed reason (`AssertionError: assert None is OpenAIChatModel()` — kwarg `compaction_model` absent, captured `None`); `git stash pop` restored the diff exactly (verified via `git status --short`), reran → 105 passed. PASS — SWE's red-then-green claim independently reproduced, not taken on faith.

**Acceptance criteria**
- [x] PASS — Regression test fails on current code (openrouter, no GoogleModel on compaction path) — independently reproduced RED via `git stash` of the src hunks (see break path 4); GREEN at `tests/unit/decode/tui/test_app_e2e.py::test_run_app_wires_the_active_providers_model_as_the_compaction_summarizer[openrouter-OpenAIChatModel-False]`
- [x] PASS — modal + gemini variants — same parametrized test, `modal-OpenAIChatModel-False` and `gemini-GoogleModel-True` cases, both pass
- [x] PASS — TUI wiring: `compaction_model is agent.model` for all three providers — asserted directly in the parametrized test; also confirmed `src/decode/tui/app.py:865` passes `compaction_model=agent.model` literally
- [x] PASS — Model-instance test seam intact — `test_compaction.py` summarize tests (renamed `model=`) all pass; `test_summarize_for_compaction_builds_no_provider_model` spies `GoogleModel`/`GoogleProvider` and asserts not-called
- [x] PASS — `compaction.py` drops Google/Settings-for-model imports, no import cycle — `git diff` shows the `GoogleModel`/`GoogleProvider`/`Settings` imports and `_resolve_model` deleted; `test_compaction_module_imports_no_google_or_settings_for_model_construction` passes; independently imported `decode.context.compaction`, `decode.agent.loop`, `decode.tui.app`, `evals.harness.driver` together in one process with no `ImportError`/cycle; `grep -n "google\|Google"` on `compaction.py` returns only the docstring prose mention of the deleted branch, zero live imports/references
- [x] PASS — `memory/extract.py` untouched — `git diff --stat` lists no entry for it; read the file, its own `_resolve_model`/`model_or_settings`/`GoogleModel`/`GoogleProvider` hardcode is fully intact, unchanged
- [x] PASS — `make format-check lint-check unit-tests` green — see Test summary above

**Evidence**
```
$ uv run ruff format --check
308 files already formatted
$ uv run ruff check
All checks passed!
$ make pre-commit   (full unit suite)
======================= 2219 passed in 117.84s (0:01:57) =======================
$ make integration-tests
================= 105 passed, 16 skipped in 380.34s (0:06:20) ==================
(16 skips: "the docker daemon is not reachable" — docker info confirmed down in this env)
$ git stash push -- src/decode/context/compaction.py src/decode/agent/loop.py src/decode/tui/app.py evals/harness/driver.py
$ uv run pytest "tests/unit/decode/tui/test_app_e2e.py::test_run_app_wires_the_active_providers_model_as_the_compaction_summarizer" -q
FAILED ...[gemini-GoogleModel-True]
FAILED ...[openrouter-OpenAIChatModel-False]
FAILED ...[modal-OpenAIChatModel-False]
E       AssertionError: assert None is OpenAIChatModel()
3 failed in 1.07s
$ git stash pop
$ uv run pytest tests/unit/decode/context/test_compaction.py tests/unit/decode/agent/test_loop.py "tests/unit/decode/tui/test_app_e2e.py::test_run_app_wires_the_active_providers_model_as_the_compaction_summarizer" -q
105 passed in 2.27s
```

**Other issues found**
- None. `git diff --stat` scope is clean (only the files the SWE's log names, plus the task file itself); no unrelated files swept in. No `print()` calls introduced. No secrets. Docstrings correctly point to ADR-0018 §5 (verified against `docs/adr/0018-compaction-cut-points-token-source-and-provider-seam-summarizer.md`).

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
