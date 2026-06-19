---
id: 004-agent-chat-only
feature: m1-vanilla-agent
status: done
---

# Agent loop: chat-only (Pydantic AI + Gemini)

## Scope
The Pydantic AI agent on Gemini with streaming, no tools — the first real round-trip. Replaces the stub turn handler. See [ADR-0002 §1–2](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md).

## Acceptance criteria
- [x] `agent/factory.py` builds `Agent(output_type=[str, DeferredToolRequests], deps_type=AgentDeps)` on Gemini via the `google-gla:` API-key path; model id from `settings.gemini_model`.
- [x] `agent/loop.py` (`AgentTurnHandler`) drives `agent.iter()`, streams `TextPartDelta` → `AssistantTextDelta` events, drains steering before each model-request leg; `message_history` carries across turns.
- [x] Unit tests use `pydantic_ai.models.test.TestModel`/`FunctionModel` (no network). `uv run decode` wiring verified offline against `TestModel`/`FunctionModel`; — [ ] [HUMAN] hold a real Gemini chat (needs `GEMINI_API_KEY`, not available in this env).
- [x] Confirmed against the installed SDK: `GoogleProvider(api_key=...)` (google-gla, not Vertex), model id `gemini-2.5-flash`, `agent.iter()` + node `stream()` + `PartDeltaEvent`/`TextPartDelta`/`ThinkingPartDelta`, `message_history` carry-over. Steering append before each model request is confirmed; a steering user-message at a *real* `DeferredToolRequests` resume is deferred to task 005 (chat-only has no tools to defer) — see log.

## Out of scope
- Tools, the permission gate (task 005+).

## Dependencies
- Adds `pydantic-ai` (pulls `google-genai`).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan + ADR-0002. Resolve the flagged Pydantic AI unknowns here.

### [SWE] 2026-06-19 21:55 — Implementation

**Dependency**
- `uv add pydantic-ai` → `pydantic-ai==1.107.0` (pulls `google-genai==2.9.0`). Moved out of the
  deferred-deps comment in `pyproject.toml`; `uv lock --check` passes (163 packages).

**Files modified**
- `src/decode/agent/__init__.py` — new package docstring (Pydantic AI ReAct loop).
- `src/decode/agent/deps.py` — `AgentDeps` dataclass: `cwd: Path` + `emit` event sink (callable
  emitting `entities.events`). Room left for gate/session_log/task_store (005/014/009); not built.
- `src/decode/agent/factory.py` — `build_agent()` → `Agent(GoogleModel, deps_type=AgentDeps,
  output_type=[str, DeferredToolRequests], instructions=<static decode base prompt>)`.
- `src/decode/agent/loop.py` — `AgentTurnHandler`, the real `TurnHandler` seam: drives
  `agent.iter()`, streams model-request nodes (`PartStartEvent`/`PartDeltaEvent`) →
  `AssistantTextDelta`/`ThinkingDelta`, yields `MODEL_REQUEST`/`WOULD_STOP`, drains steering into
  the prompt before each model request, carries `message_history` across turns.
- `src/decode/tui/app.py` — `run_app()` now builds the agent + `AgentTurnHandler(deps=...)` and
  passes it to `Runner` (replaces `stub_turn_handler`).
- `src/decode/harness/runner.py` — removed the now-dead `stub_turn_handler` (task-003 scaffolding;
  the runner stays handler-agnostic, exercised by `RecordingHandler` in its own tests). Updated docstring.
- `tests/unit/decode/agent/{test_deps,test_factory,test_loop}.py` — new (TestModel/FunctionModel, no network).
- `tests/unit/decode/test_cli.py` — inject a dummy Gemini key (agent is now built at startup; still offline).

**Tests**
- Unit: 73 passing, 0 failing — `make pre-commit` output below. New: 14 (deps 2, factory 5, loop 7).
- Integration: N/A — no infra changes.
- No-network honored: every model interaction runs against `TestModel`/`FunctionModel`. No new
  `filterwarnings` ignores were needed — `filterwarnings=["error"]` stays clean (key: never pass
  any Vertex/Cloud arg to `GoogleProvider`, incl. `vertexai=False`, which would raise a
  `PydanticAIDeprecationWarning` → test error).

**Confirmed SDK facts (pydantic-ai 1.107.0 / google-genai 2.9.0)**
- **Google model + provider (google-gla, NOT Vertex):** `GoogleModel(model_id, provider=GoogleProvider(api_key=...))`
  from `pydantic_ai.models.google` / `pydantic_ai.providers.google`. `GoogleProvider(api_key=...)`
  builds `google.genai.Client(vertexai=False, ...)` — the Generative-Language endpoint; `model.system == "google"`.
  The explicit kwarg **is** `api_key=` (we pass `settings.gemini_api_key.get_secret_value()` so config,
  not env, is the source). Env fallback exists (`GOOGLE_API_KEY` then `GEMINI_API_KEY`) but the error
  message it raises names `GOOGLE_API_KEY`, slightly misleading vs our `GEMINI_API_KEY` (note below).
  The `'google-gla:<model>'` string form also works (reads env) but we chose the explicit instance so
  the key comes from settings and `agent.model` is introspectable.
- **Model id:** `gemini-2.5-flash` is valid — present in pydantic-ai's `LatestGoogleModelNames`
  (`models/google.py`) and used throughout google-genai. Default in `config/settings.py` is correct;
  no change needed.
- **Streaming + iteration:** `async with agent.iter(prompt, deps=..., message_history=...) as run:` +
  `async for node in run:`; gate model nodes with `Agent.is_model_request_node(node)`, then
  `async with node.stream(run.ctx) as request_stream: async for event in request_stream:`. This is the
  canonical pattern (matches pydantic-ai's own `_cli/__init__.py` and `agent/abstract.py` docstrings).
  Text streams as `PartStartEvent(part=TextPart)` then `PartDeltaEvent(delta=TextPartDelta(content_delta=...))`;
  thinking as `ThinkingPart`/`ThinkingPartDelta(content_delta=...)`. We emit on both the start part
  (initial content) and each delta.
- **History carry-over:** `run.all_messages()` after the leg returns prior history + this leg's messages;
  re-passed as `message_history` next turn (verified: 2 turns → history length 4). Serialization adapter
  for task 014: `pydantic_ai.messages.ModelMessagesTypeAdapter` (+ `run.all_messages_json()` /
  `run.new_messages_json()`).

**Steering-at-deferred-resume (note for task 005)**
Chat-only has no tools, so a leg never resolves to a real `DeferredToolRequests` — there is no deferred
pause to resume mid-turn. What IS implemented + tested here: steering drained at the `MODEL_REQUEST`
boundary is folded into the prompt **before** the model request, so the model sees it on that leg
(`test_steering_is_appended_before_the_model_request`). **Task 005 must validate** appending a steering
*user-message* to `message_history` (or via `agent.iter(... deferred_tool_results=..., message_history=...)`)
at an actual deferred-tool resume, i.e. between the gate decision and the resume leg. The
`output_type=[str, DeferredToolRequests]` seam is already in place so no factory change is needed.

**Evidence — offline end-to-end (no key available; `GEMINI_API_KEY` not set in env)**
Drove the real `AgentTurnHandler` through the real `Runner` + renderer (same wiring as `run_app`)
against a `FunctionModel` streaming token deltas:
```
[smoke] built agent on model=gemini-2.5-flash system=google
you what is the capital of France?
decode here: the capital of France is Paris.    # streamed as 5 AssistantTextDelta chunks
[done]
you and of Germany?
decode here: the capital of France is Paris.
[done]
[smoke] message_history length after 2 turns = 4
[smoke] OK
```
`uv run decode --help` renders; `uv run decode` startup builds the agent (offline). A real Gemini
round-trip via `uv run decode` is left for [HUMAN] verification once `GEMINI_API_KEY` is set.

**Evidence — `make pre-commit`**
```
uv run ruff format --check
32 files already formatted
uv run ruff check
All checks passed!
uv run pytest tests/unit
collected 73 items
tests/unit/decode/agent/test_deps.py ..
tests/unit/decode/agent/test_factory.py .....
tests/unit/decode/agent/test_loop.py .......
... (all suites)
73 passed
```

**Notes**
- Removed `stub_turn_handler` (task-003 scaffolding) since `app.py` no longer uses it and the runner's
  semantics are covered by `RecordingHandler` in `test_runner.py`. Flag if the team would rather keep it.
- Minor UX paper-cut surfaced (out of scope, candidate follow-up task): with no key configured,
  `uv run decode` fails at startup with the SDK's `UserError` naming `GOOGLE_API_KEY` (not our
  `GEMINI_API_KEY`). Construction is eager (mirrors pydantic-ai's own CLI). A friendlier
  "set GEMINI_API_KEY" guard could be added when the session/persistence work lands.

### [Tester] 2026-06-19 23:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 32 files; `ruff check` all passed)
- `uv lock --check`: PASS (163 packages resolved, lock current)
- Unit tests: 73 passed / 0 failed
- Integration tests: N/A (`tests/integration` empty — no infra touched)
- Warnings: 0 under `filterwarnings=["error"]` — no pydantic-ai / google-genai deprecation leaked
  (grep for `warning|deprecat` in pytest output found nothing; `-W error` rerun clean)
- `code-review` plugin enabled in `.claude/settings.json`; ran the manual-checklist equivalent
  (it is not tool-invocable from the Tester subagent) — no defects beyond the nit below.

**SDK facts — verified against the installed packages, not just asserted** (pydantic-ai 1.107.0 / google-genai 2.9.0)
- `GoogleProvider(api_key=...)` → builds `google.genai.client.Client` with `client.vertexai == False`
  (google-gla / Generative-Language endpoint, NOT Vertex); `provider.name == "google"`. Param list
  includes `api_key` as an explicit kwarg.
- `gemini-2.5-flash` AND `gemini-2.5-pro` both present in `LatestGoogleModelNames` — model id valid.
- `Agent.is_model_request_node`, `Agent.iter`, `Agent.override` all exist; a model node exposes
  `.stream`, the run exposes `.ctx` + `.all_messages()` + `.all_messages_json()` + `.new_messages_json()`.
  All part/event imports in `loop.py` (`PartStartEvent`/`PartDeltaEvent`/`TextPart`/`TextPartDelta`/
  `ThinkingPart`/`ThinkingPartDelta`) and `ModelMessagesTypeAdapter` resolve.

**E2E adversarial pass** — drove the REAL `AgentTurnHandler` + `Runner` + `render.render_event`
(same wiring as `run_app`, events rendered through a captured Rich `Console`) against
`FunctionModel`/`TestModel` (no network; `GEMINI_API_KEY`/`GOOGLE_API_KEY` not set in env).
- Happy path: prompt "what is the capital of France?" with a 5-token FunctionModel stream →
  5 `AssistantTextDelta` chunks `['The capital ','of France ','is ','Paris','.']`, reassemble to
  `"The capital of France is Paris."`, rendered incrementally (`you …` / chunks / `[done]`);
  runner returns to `idle`, `active_turns == 0`. (PASS)
- Incremental render: confirmed >=5 separate deltas reach the renderer (not one buffered blob). (PASS)
- Multi-turn history: turn 1 history len 2 → turn 2 history len 4; model saw 1 message on leg 1 and
  3 on leg 2 → prior history carried across turns. (PASS)
- Steering at the model-request boundary: injected `"URGENT: also consider B"` at `MODEL_REQUEST`
  via the handler; both it and the base prompt reached the model on that leg. (PASS)
- Break path 1 (boundary: empty prompt straight into the runner): returns to `idle`, no stuck lock,
  no crash (chat-only handler does not guard `""`; the TUI guards it via `if not text.strip()`). (PASS)
- Break path 2 (thinking + text interleaved): `ThinkingDelta` = `"let me reason about this"`,
  `AssistantTextDelta` = `"Final answer here."` — kept on separate event channels, rendered dim vs
  plain. (PASS)
- Break path 3 (failure mode: model raises mid-stream, simulating a 503): surfaced as a single
  `events.AgentError("simulated upstream 503 from Gemini")` via the runner's `logger.exception` +
  emit; `TurnFinished(aborted=False)` still fired; runner back to `idle`, `active_turns == 0`; a
  subsequent turn ran fine ("recovered fine") — no stuck lock, REPL survives. (PASS)
- Cooperative abort: `runner.abort()` before idle → `TurnFinished`, phase `idle`, `active_turns 0`,
  no stuck lock. (PASS)
- Note (NOT a defect): mid-turn steering submitted *after* a chat leg's `MODEL_REQUEST` boundary is
  queued but not consumed, because chat-only is a single-leg turn with no tool-resume leg to drain it
  into. This is exactly ADR-0002 §4 + the SWE's deferred-to-005 note; the seam is in place.

**Acceptance criteria**
- [x] PASS — `factory.build_agent()` builds `Agent(output_type=[str, DeferredToolRequests],
      deps_type=AgentDeps)` on Gemini via the google-gla API-key path; model id from
      `settings.gemini_model`. Evidence: `test_factory.py` (5 tests pass) asserts `GoogleModel` +
      `model.system == "google"` + `GoogleProvider`; live SDK check shows `client.vertexai == False`;
      `factory.py:53-61`.
- [x] PASS — `loop.AgentTurnHandler` drives `agent.iter()`, streams text deltas →
      `AssistantTextDelta`, drains steering before each model-request leg, carries `message_history`.
      Evidence: `test_loop.py` (7 tests pass) + my e2e harness (5 deltas, history 2→4, steering lands
      on the leg); `loop.py:109-144`.
- [x] PASS — Unit tests use `TestModel`/`FunctionModel` (no network); `uv run decode` wiring verified
      offline. Evidence: every model interaction is `TestModel`/`FunctionModel`; `uv run decode --help`
      renders; `GEMINI_API_KEY=dummy … /quit` starts the full agent+runner+renderer wiring and exits 0.
      [HUMAN] real Gemini chat remains `[ ]` — no key in env; offline equivalent (full streaming
      round-trip through the real handler/runner/renderer) is solid.
- [x] PASS — SDK facts confirmed against the installed SDK (see "SDK facts" above); steering-at-real-
      DeferredToolRequests-resume correctly deferred to task 005 (chat-only has no tools to defer).

**Evidence — `make pre-commit` equivalent**
```
$ uv run ruff format --check    → 32 files already formatted        (exit 0)
$ uv run ruff check             → All checks passed!                (exit 0)
$ uv lock --check               → Resolved 163 packages … (current) (exit 0)
$ uv run pytest tests/unit      → 73 passed in 1.47s                (0 warnings)
$ uv run pytest tests/integration → no tests ran (empty dir)
```

**Other issues found**
- (Non-blocking nit, NOT in AC) No-key startup UX: with no `GEMINI_API_KEY`/`GOOGLE_API_KEY`,
  `uv run decode` (no `--help`) crashes with a raw, unhandled Python traceback ending in
  `ValueError: No API key was provided. Please pass a valid API key. …` (exit 1). Two corrections to
  the SWE's note: (1) the surfaced exception is google-genai's generic `ValueError` "No API key was
  provided", not a `UserError` naming `GOOGLE_API_KEY` — that env-fallback message lives in a path we
  don't hit because we pass `api_key=` explicitly; (2) the user-visible problem is the *unhandled
  traceback dump*, not the env-var name. **Judgment: non-blocking for task 004.** It is not an
  acceptance criterion (AC#3 explicitly `[HUMAN]`-gates the real-key path and notes no key in env),
  the eager construction mirrors pydantic-ai's own CLI, and the scope is "the first real round-trip".
  Strongly recommend a follow-up task: wrap `build_agent()` (or guard `settings.gemini_api_key`) so a
  missing key prints a one-line `click.echo` "Set GEMINI_API_KEY to run decode (see .env.example)"
  and exits non-zero, instead of a stack trace. The team should also decide whether to keep the now-
  removed `stub_turn_handler` (SWE flagged; runner semantics are covered by `RecordingHandler`).

**VERDICT: PASS**
