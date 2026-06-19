---
id: 013-memory-extraction-on-exit
feature: m1-vanilla-agent
status: done
---

# Memory: extraction on exit

## Scope
A deliberately minimal memory-write loop ([ADR-0002 §8](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)): summarize the session into one sentence on quit, to be deepened in M4.

## Acceptance criteria
- [x] `memory/extract.py` runs on session end: one cheap Gemini call summarizes the conversation into a single sentence.
- [x] The sentence is appended (dated) to project-root `MEMORY.md` (created if absent), trimmed to the 200-line / 25 KB caps.
- [x] A summary failure is logged and non-fatal (never blocks exit).
- [x] The line is picked up by `memory.service` on the next session.

## Out of scope
- Forked-agent extractor, topic files, recall selector, compaction (M4).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. KAIROS-style append; the cheap-summary helper is reused by M4 compaction.

### [SWE] 2026-06-20 10:05 — Implementation

**Files modified**
- `src/decode/memory/extract.py` — new: `summarize_session` (cheap one-shot LLM call), `append_session_summary` (dated line + tail-keeping cap trim), `extract_on_exit` (non-fatal orchestrator), `_utc_now` (the single, patchable UTC clock).
- `src/decode/tui/app.py` — hold the `AgentTurnHandler` directly and call `extract_on_exit(handler.message_history, deps.cwd)` in the shutdown path (after `runner.wait_idle()`, before "bye").
- `tests/unit/decode/memory/test_extract.py` — new: 20 tests (summarize/append/orchestrate + round-trip).
- `tests/unit/decode/tui/test_app_e2e.py` — new e2e test: `run_app` fires the write-back on exit with the accumulated history + cwd (no network; `extract_on_exit` captured).

**Tests**
- Unit: 316 passing, 0 failing (`make unit-tests` / `make pre-commit`).
- Integration: N/A — no infra changes (no network; summary driven by `TestModel`/`FunctionModel`, append is pure FS on `tmp_path`).

**Acceptance criteria**
- [x] one cheap call summarizes the session into one sentence — `tests/.../test_extract.py::test_summarize_session_returns_the_model_sentence`, `::test_summarize_session_feeds_the_conversation_to_the_model`; wired on exit by `test_app_e2e.py::test_run_app_runs_memory_write_back_on_exit_with_the_session_history`.
- [x] dated line appended to project-root `MEMORY.md` (created if absent), trimmed to caps — `::test_append_creates_memory_md_when_absent`, `::test_append_writes_a_dated_line`, `::test_append_trims_to_the_line_cap_keeping_most_recent`, `::test_append_trims_to_the_byte_cap_keeping_most_recent`.
- [x] summary failure logged + non-fatal (never blocks exit) — `::test_summarize_session_returns_none_when_the_call_fails`, `::test_extract_on_exit_never_raises_when_summarize_blows_up`, `::test_extract_on_exit_logs_a_warning_when_summarize_blows_up`, `::test_extract_on_exit_never_raises_when_append_blows_up`.
- [x] line picked up by `memory.service` next session — `::test_written_summary_is_picked_up_by_assemble_memory_next_session`.

**Evidence**
```
$ make pre-commit
... 316 passed in 5.99s ...

$ uv run python -c "...end-to-end driver..."
SUMMARY: Added retry-with-backoff to the HTTP client.
MEMORY.md after append:
- 2026-06-20: Added retry-with-backoff to the HTTP client.
ASSEMBLED contains summary: True
ASSEMBLED contains date: True
extract_on_exit swallowed the failure, no MEMORY.md written: True

$ uv lock --check
Resolved 166 packages in 3ms
```

**Notes**
- `summarize_session(messages, *, model_or_settings)` is provider-agnostic: tests inject a `Model` (`TestModel`/`FunctionModel`, no network); production passes `settings`, from which the same `google-gla` Gemini model the factory uses is built. This keeps the cheap-summary helper the reusable seam M4 compaction grows from.
- `extract_on_exit` no-ops on empty conversation AND on missing `GEMINI_API_KEY` (the M1 default is an empty key), so a headless/unconfigured run leaves no trace and never attempts a call.
- Trim keeps the **most-recent** lines (drops from the front) — the mirror of `memory.service._clip_to_budget` (which keeps the head). "Project root" = the launch cwd, documented in the module + `append_session_summary` docstrings.
- No new dependencies; `uv lock --check` clean. Did not commit (awaiting Tester).

### [Tester] 2026-06-20 11:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 73 files formatted; `ruff check`: all checks passed)
- Unit tests: 316 passed / 0 failed
- Integration tests: 0 collected (N/A — no infra changes; no network in tests)
- `uv lock --check`: PASS (166 packages resolved, lockfile clean)
- Warnings: 0 (`filterwarnings=["error"]`)

**E2E adversarial pass**
- Happy path (a): `summarize_session(conv, model_or_settings=FunctionModel(capture))` → returns the model's sentence; the rendered transcript carries BOTH the user prompt and the assistant text (so the summary is about *this* session). PASS
- Break path 1 (boundary: empty / trivial conversation): `summarize_session([], ...)` and `summarize_session([whitespace-only UserPromptPart], ...)` → both `None`, **0 model calls** made (no write). PASS
- Break path 2 (failure mode: raising model + raising append): failing `FunctionModel` → `summarize_session` returns `None` (warning logged via `exc_info`); `extract_on_exit` with a raising `summarize_session` AND separately a raising `append_session_summary` → **swallowed, never raised, no MEMORY.md written**. PASS
- Break path 3 (boundary: caps + naive `now`): append creates `MEMORY.md`, writes `- {YYYY-MM-DD}: …`; tz-aware non-UTC `now` (`2026-06-19 23:30 -04:00`) correctly normalizes to **`2026-06-20`** (UTC date, proving `.astimezone(UTC)`); line cap → 200/200 keeping most-recent (note0 dropped, note249 kept); byte cap → ≤ 25 000 bytes keeping the fresh line; **naive `now` rejected with `ValueError("timezone-aware …")` and NO file created**. PASS
- Break path 4 (round-trip e2e): a written summary + its date + the `# From …MEMORY.md` provenance header are all surfaced by `assemble_memory(cwd)` next session. PASS
- Break path 5 (reader/writer cap-direction interaction): 300 sequential appends → writer holds the file at exactly 200 lines = the **200 most-recent** (`session-100`..`session-299`); `assemble_memory` keeps the head of that already-capped file, so the next session sees the **recent** block (incl. `session-299`), no truncation note, no stale-vs-fresh mismatch. A hand-grown 250-line file (writer not yet run) shows its head + a visible `[memory truncated …]` note until one append self-heals it to the recent 200; non-corrupting, flagged via the note. PASS (no confusing mismatch on the normal append path)
- Break path 6 (state/config: missing GEMINI_API_KEY): `extract_on_exit(real-conv, cwd)` with empty key → **silent no-op**: summarizer never called, no file, no crash. PASS
- Break path 7 (on-exit wiring): real `run_app` driven through `/quit` (piped prompt_toolkit input, FunctionModel, no network) fires `extract_on_exit(handler.message_history, deps.cwd)` after `runner.wait_idle()`, with the accumulated history (carries this session's user prompt) and `Path.cwd()`. PASS (`test_app_e2e.py::test_run_app_runs_memory_write_back_on_exit_with_the_session_history`)
- Break path 8 (hostile inputs): summaries containing embedded newlines, `- ` markdown bullets, `## heading`, leading-dash, 10 KB of text, and Unicode (`résumé — café 🚀 中文`) → dated-line prefix on the appended record stays intact, file round-trips through `assemble_memory` without crash, caps still hold. A single summary larger than the byte cap is kept whole (by design — the freshest note is never split to nothing). PASS

**Acceptance criteria**
- [x] PASS — `memory/extract.py` runs on session end; one cheap Gemini call summarizes the conversation into a single sentence — `test_extract.py::test_summarize_session_returns_the_model_sentence` + `::test_summarize_session_feeds_the_conversation_to_the_model`; one-shot `Agent(model, instructions=_SUMMARIZE_INSTRUCTIONS)` at `extract.py:98-100`; production model built from settings at `_resolve_model` `extract.py:172-174`.
- [x] PASS — the sentence is appended (dated) to project-root `MEMORY.md` (created if absent), trimmed to the 200-line / 25 KB caps — `::test_append_creates_memory_md_when_absent`, `::test_append_writes_a_dated_line`, `::test_append_trims_to_the_line_cap_keeping_most_recent`, `::test_append_trims_to_the_byte_cap_keeping_most_recent`; adversarial probe confirmed both caps + UTC-normalized date at `extract.py:128-134`.
- [x] PASS — a summary failure is logged and non-fatal (never blocks exit) — `::test_summarize_session_returns_none_when_the_call_fails`, `::test_extract_on_exit_never_raises_when_summarize_blows_up`, `::test_extract_on_exit_logs_a_warning_when_summarize_blows_up`, `::test_extract_on_exit_never_raises_when_append_blows_up`; adversarial probe drove a raising summarize AND a raising append — both swallowed (`extract.py:149-162`).
- [x] PASS — the line is picked up by `memory.service` on the next session — `::test_written_summary_is_picked_up_by_assemble_memory_next_session`; round-trip + cap-direction probes confirm the next session surfaces the recent summaries.

**Evidence**
```
$ make pre-commit
... 316 passed in 5.90s ...
$ uv lock --check
Resolved 166 packages in 2ms
$ uv run pytest tests/unit/decode/memory/test_extract.py tests/unit/decode/tui/test_app_e2e.py
... 24 passed in 1.06s ...
adversarial: dated line for now=2026-06-19 23:30 -04:00  ->  "- 2026-06-20: first note"  (UTC normalized)
adversarial: 300 appends -> on-disk = 200 lines (session-100..session-299); assemble surfaces session-299
adversarial: missing key -> summarize NOT called, MEMORY.md absent
```

**Other issues found (non-blocking — PASS with note, orchestrator's call)**
- A summary string with literal `\n` newlines is appended verbatim and spills onto extra physical lines (only the first carries the dated prefix). Non-corrupting and round-trips fine; the byte/line caps still hold. The one production caller (`summarize_session`) instructs the model to return ONE plain sentence with no markdown, so this is an off-nominal model output, not user input. Possible M4 hardening: collapse newlines in the summary before append. Not an AC; no fix required now.
- `_resolve_model` builds a fresh `GoogleProvider`/`GoogleModel` per exit call rather than reusing the agent factory's model. Correct and config-driven; just a tiny duplication of the §1 construction path that M2's gateway will consolidate.

**VERDICT: PASS**
