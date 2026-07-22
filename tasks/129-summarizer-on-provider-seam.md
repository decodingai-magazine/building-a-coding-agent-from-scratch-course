---
id: 129
feature: fix-compaction
status: pending
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

- [ ] Regression test (written first, fails on current code): with
      `settings.llm_provider = "openrouter"` (and no usable `GEMINI_API_KEY`), the summarizer
      model used for compaction is NOT a `GoogleModel` — no Google model/provider is
      constructed anywhere on the compaction path.
- [ ] Same assertion for `llm_provider = "modal"` (OpenAI-compatible model over the modal
      client) and `"gemini"` (GoogleModel — unchanged behavior for the default provider).
- [ ] TUI wiring test: the handler's compaction model is the ACTIVE provider's built model
      (same class/config as the Provider Seam produces for `settings.llm_provider`).
- [ ] The Model-instance test seam still works: passing a `TestModel`/`FunctionModel`
      summarizes with no network, exactly as before.
- [ ] `compaction.py` no longer imports `GoogleModel`/`GoogleProvider`/`Settings`-for-model;
      no import cycle introduced (`make lint-check` + import of every touched module in tests).
- [ ] `memory/extract.py` untouched (its twin `_resolve_model` is explicitly out of scope).
- [ ] `make format-check lint-check unit-tests` green.

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
