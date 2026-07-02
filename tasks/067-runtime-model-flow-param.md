---
id: 067-runtime-model-flow-param
feature: runtime-replay
status: done
---

# Model as a replayable flow parameter

Tags: `runtime`, `agent`, `replay`
Depends on: None (extends the shipped Headless Runtime — ADR-0008, tasks 057-066)
Blocks: #068, #069, #070

This task implements **ADR-0010 §2** — the single enabler that makes model-swap replay possible.
Kitaru swaps a *flow input* on replay (`flow.replay(exec_id, from_=…, model="…")`), so decode must
expose the model id as a threaded flow parameter rather than reading it only from settings inside
model construction. Thread `model: str | None = None` from the two flow signatures all the way down
to `_build_model`. When `None`, behaviour is byte-identical to today (read `settings.<provider>_model`);
when set, it overrides **only the active provider's model id** — the provider itself stays selected by
`LLM_PROVIDER`. No CLI flag yet (that is #069); no checkpoint-strategy change yet (that is #068).

## Scope

- **`agent/factory.py`** — thread the override through the Provider Seam without touching provider selection:
  - `_build_model(*, flow_mode: bool = False, model: str | None = None) -> Model` (currently
    `factory.py:98`). In each branch use the override or fall back to settings:
    `GoogleModel(model or settings.gemini_model, …)` (`:129`),
    `OpenAIChatModel(model or settings.openrouter_model, …)` (`:135`),
    `OpenAIChatModel(model or settings.modal_endpoint_model, …)` (`:155`). Provider branching still
    keys off `settings.llm_provider` — the override never changes the provider or the auth path.
  - `build_agent(*, flow_mode: bool = False, model: str | None = None)` (`factory.py:69`) passes
    `model` straight to `_build_model`. The interactive TUI keeps calling `build_agent()` with no
    `model` → `None` → byte-unchanged.
- **`runtime/flow.py`** — thread the override through both patchable seams and both flow signatures:
  - `_build_runtime_agent(model: str | None = None)` (`flow.py:194`) → `build_agent(flow_mode=True, model=model)`.
  - `_build_hitl_runtime_agent(model: str | None = None)` (`flow.py:323`) →
    `_to_hitl_durable_agent(build_agent(flow_mode=True, model=model))`.
  - `@flow run_agent_task(task: str, model: str | None = None)` (`flow.py:232`) → `_build_runtime_agent(model)`.
  - `@flow run_agent_task_hitl(task: str, model: str | None = None)` (`flow.py:368`) → `_build_hitl_runtime_agent(model)`.
  - Keep `model` as a keyword-defaulted parameter (not positional-only) so Kitaru can forward it by
    name on `.replay(..., model=…)` and so `run(task=…)` without a model stays valid.
- **Existing test seam-patches must accept the new parameter** (the flow now calls the seams with an
  argument). Update every `_build_runtime_agent` / `_build_hitl_runtime_agent` monkeypatch to accept
  `model` (e.g. `lambda model=None: durable`):
  - `tests/unit/decode/runtime/test_flow.py` (`:40`, `:62`, `:80`).
  - `tests/unit/decode/runtime/test_run_command.py` (`_patch_seam` `:44`; the `*args`-tolerant
    tripwires at `:121` are already safe; make the bare `_tripwire()` at `:63`/`:83` `*args`-tolerant too).
  - `tests/integration/test_runtime_capstone.py` bypass seams (`:335`, `:614` `def seam():`, `:661`)
    and HITL seams (`:387`, `:446`, `:489`, `:559`).

## Acceptance criteria

- [x] `_build_model(*, flow_mode=False, model=None)` and `build_agent(*, flow_mode=False, model=None)`
      exist; with `model=None` the constructed model id equals `settings.<active-provider>_model`
      exactly as before (unit test per provider: `gemini`/`openrouter`/`modal`, offline — model
      construction issues no request).
- [x] With `model="gemini-2.5-pro"` and `LLM_PROVIDER=gemini`, the constructed `GoogleModel`'s model
      id is `"gemini-2.5-pro"` (not the settings default); the provider/auth path is unchanged
      (still `GoogleProvider`, still the settings/`flow_mode` key resolution). Analogous unit
      assertions for `openrouter` (`OpenRouterProvider`) and `modal` (custom `AsyncOpenAI`).
- [x] The override changes **only** the model id, never the provider: with `LLM_PROVIDER=openrouter`
      and `model="some-id"`, the model is still an `OpenAIChatModel` on `OpenRouterProvider` (a test
      asserts provider class unchanged).
- [x] `run_agent_task(task, model=None)` and `run_agent_task_hitl(task, model=None)` accept the
      keyword; `run_agent_task.run(task="x")` (no model) still round-trips through the real `@flow`
      offline and returns the scripted text (existing `test_flow.py` tests pass with updated seam
      patches).
- [x] A real (seed-key, offline) `_build_runtime_agent(model="gemini-2.5-pro")` builds a
      `KitaruAgent` whose inner agent's model id is `"gemini-2.5-pro"` (extends
      `test_build_runtime_agent_wraps_build_agent_in_a_named_kitaru_agent`, `test_flow.py:96`).
- [x] **Interactive TUI byte-unchanged:** `agent/loop.py`, `tui/`, and the interactive `build_agent()`
      call sites are not in the diff; `test_run_app_*` stay green.
- [x] **Capstone parameter-serialization ripple handled:** re-run
      `tests/integration/test_runtime_capstone.py`; if adding `model=None` changes the recorded flow
      inputs, update `test_credentials_proxy_sources_the_key_and_keeps_it_off_the_payload`
      (`:627` `assert set(run.config.parameters) == {"task"}`) to the observed set (e.g.
      `{"task", "model"}`), and confirm neither raw key leaks into `run.config.model_dump_json()`
      (`:629-630` still pass — a model id is not a secret).
- [x] `make ci` green, **0 warnings**; `uv lock --check` passes.

## User stories

### Story: A downstream turn can be re-run under a different model (plumbing proof)
1. A test builds the runtime agent with `_build_runtime_agent(model="model-b")`.
2. The wrapped pydantic-ai agent reports model id `"model-b"`, while the same call with no argument
   reports `settings.gemini_model` — proving the seam forwards the override end to end.

### Story: The interactive REPL is untouched
1. A developer launches bare `decode` exactly as before.
2. `build_agent()` runs with `model=None`; the model id is the settings default; no behaviour changes.

## Out of scope
- The `decode run --model` CLI flag and `exec_id` surfacing (#069).
- Flipping the checkpoint strategy to `"calls"` (#068) and any `.wait()` extraction repair.
- The `decode replay` command (#070) and any replay-time override wiring.
- Cross-provider swap (provider stays `LLM_PROVIDER`-selected) — permanent non-goal (ADR-0010 §NG).

## Log

### [SWE] 2026-07-02 14:16 — Implementation

Threaded `model: str | None = None` through the model-construction path so the model id becomes a
replayable Kitaru flow input (ADR-0010 §2). `None` is byte-identical to today (reads
`settings.<provider>_model`); a value overrides **only** the active provider's model id — never the
provider branch (still keyed off `settings.llm_provider`) and never the auth/key path.

**Files modified (src)**
- `src/decode/agent/factory.py` — `_build_model(*, flow_mode=False, model: str | None = None)` uses
  `model or settings.<provider>_model` in all three branches (gemini/openrouter/modal);
  `build_agent(*, flow_mode=False, model=None)` passes `model` straight through (local var renamed
  `model` → `built_model` to free the new param name). Docstrings note the Model Override.
- `src/decode/runtime/flow.py` — `_build_runtime_agent(model=None)` →
  `build_agent(flow_mode=True, model=model)`; `_build_hitl_runtime_agent(model=None)` →
  `_to_hitl_durable_agent(build_agent(flow_mode=True, model=model))`; both `@flow`s
  (`run_agent_task(task, model=None)`, `run_agent_task_hitl(task, model=None)`) forward `model` to
  their seam. `model` kept keyword-defaulted so `run(task=…)` and `.replay(..., model=…)` both work.

**Files modified (tests)**
- `tests/unit/decode/agent/test_factory.py` — new Model Override section: per-provider
  `model=None`-matches-settings + override-sets-only-the-id (parametrized gemini/openrouter/modal),
  per-provider auth-path-unchanged (GoogleProvider key / OpenRouterProvider key / modal custom-client
  headers), and `build_agent(model=…)` threading + no-arg-default. Added `OpenAIProvider` /
  `OpenRouterProvider` imports.
- `tests/unit/decode/runtime/test_flow.py` — extended the seam test: new
  `test_build_runtime_agent_threads_the_model_override_to_the_inner_agent` (User Story 1) + the HITL
  parallel; updated the 3 `_build_runtime_agent` seam lambdas to `lambda model=None: …`.
- Seam-patch updates so the flow's new `_build_..._agent(model)` call doesn't break existing tests
  (the task's enumeration was **incomplete** — I updated every seam patch in the suite):
  `test_run_command.py` (`_patch_seam` lambda + both bare `_tripwire()` → `*args`-tolerant),
  `test_hitl.py` (`_patch_hitl_seam` lambda), `test_credentials_proxy.py` (`_seam` signature),
  `test_secret_store_config.py` (both `_seam` signatures + 3 `_scripted_durable` direct seams wrapped
  as `lambda model=None: _scripted_durable()`), capstone bypass `seam`/`lambda`s + 4 HITL `lambda`s.
- **Parameter-serialization ripple:** adding the flow param makes Kitaru record `model` as a flow
  input, so the recorded set is `{"task", "model"}` (observed, not assumed). Updated **all four**
  `assert set(run.config.parameters) == {"task"}` sites — the task named only the capstone `:627`,
  but the same assertion lived in `test_credentials_proxy.py` and twice in `test_secret_store_config.py`.
  Confirmed neither raw key leaks into `run.config.model_dump_json()` (a model id is not a secret).

**Tests**
- Unit: 1027 passing, 0 failing (`make unit-tests`).
- Integration: 20 passing incl. all 7 runtime-capstone (`make integration-tests`).
- Full gate: `make ci` → **1047 passed, 0 warnings** (`filterwarnings=["error"]`); `uv lock --check`,
  format-check, lint-check all clean. No new dependency; `.env.example` unchanged (no new setting).

**Acceptance criteria**
- [x] `_build_model` / `build_agent` take `model=None`; `model=None` matches `settings.<provider>_model`
      per provider — `test_build_model_with_none_matches_the_settings_default[gemini|openrouter|modal-*]`.
- [x] Override sets the model id; provider/auth unchanged — `test_build_model_override_sets_only_the_model_id`,
      `test_gemini_override_keeps_the_google_provider_and_settings_key`,
      `test_openrouter_override_keeps_the_openrouter_provider_and_key`,
      `test_modal_override_keeps_the_custom_client_and_proxy_headers`.
- [x] Override never changes the provider (openrouter stays `OpenRouterProvider`) — same two tests above.
- [x] Flows accept the keyword; `run(task="x")` round-trips — existing `test_flow.py` round-trip tests
      pass with the updated seam patches.
- [x] Real `_build_runtime_agent(model="gemini-2.5-pro")` inner model id is the override —
      `test_build_runtime_agent_threads_the_model_override_to_the_inner_agent` (+ HITL parallel).
- [x] Interactive TUI byte-unchanged — `agent/loop.py` / `tui/` / interactive `build_agent()` call site
      (`tui/app.py:785`) not in the diff; `test_app_e2e` (`test_run_app_*`) green.
- [x] Capstone ripple handled — recorded set is `{"task","model"}`; no key leak; capstone green.
- [x] `make ci` green, 0 warnings; `uv lock --check` passes.

**Evidence**
```
$ make ci
... uv lock --check + ruff format --check + ruff check all clean ...
======================= 1047 passed in 95.30s (0:01:35) ========================
```
End-to-end (real `@flow`, offline, hermetic store — the enabler exercised as a downstream task would):
```
A. _build_runtime_agent(model='gemini-2.5-pro').model.model_name = gemini-2.5-pro
A. _build_runtime_agent()                       .model.model_name = gemini-2.5-flash
B. run_agent_task.run(task=..., model='gemini-2.5-pro').wait().output = headless answer (scripted)
B. recorded flow parameters = {'task': 'list the python files', 'model': 'gemini-2.5-pro'}
B. no raw key in serialized config: True
```

**Notes**
- The `model or settings.<provider>_model` fallback also treats `""` as "use the settings default"
  (an empty override is meaningless) — matches the task's exact spec.
- No CLI flag / `decode replay` here — those are #069/#070; this task ships only the plumbing.
- The task's seam-patch enumeration and its single parameter-serialization site (`:627`) were both
  under-counted; flagging so the Tester knows the extra edits (`test_hitl.py`, `test_credentials_proxy.py`,
  `test_secret_store_config.py`) are deliberate, not scope creep. `docs/adr` / `docs/glossary.md` were
  read-only (glossary already carries the `Model Override` row from grooming) — no edits.
- DO NOT COMMIT yet — awaiting Tester review.

### [Tester] 2026-07-02 15:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 144 files clean; `ruff check` all passed; `make pre-commit` 1027 passed)
- Unit tests: 1027 passed / 0 failed
- Integration tests: 20 passed / 0 failed (incl. all 7 `test_runtime_capstone`)
- Warnings: 0 (`filterwarnings = ["error"]` at `pyproject.toml:108`; a green suite proves it)

**E2E adversarial pass** (this is plumbing — the "user" is a downstream flow/Kitaru replay; probed the seam directly, offline)
- Happy path: `_build_runtime_agent(model="gemini-2.5-pro")` → inner `model.model_name == "gemini-2.5-pro"`; no-arg → `settings.gemini_model`. Real `@flow`: `run_agent_task.run(task="list the python files", model="gemini-2.5-pro").wait()` → `"ok"`. (PASS)
- Break path 1 (boundary: empty string `""`): `_build_model(model="")` → `"gemini-2.5-flash"` (settings default) vs expected default. (PASS — matches the spec Note; `"" or settings.x` → settings.x)
- Break path 2 (security: leak into provider/auth path + serialized payload): gemini/openrouter override keeps `GoogleProvider`/`OpenRouterProvider` + settings auth key intact; independent real-`@flow` probe → `set(run.config.parameters) == {"task","model"}`, `parameters["model"] == "gemini-2.5-pro"` (recorded as a non-secret flow input), planted sentinel key absent from `run.config.model_dump_json()`. (PASS — override never leaks into provider/auth/id)
- Break path 3 (state: REPL byte-identical): `_build_model()` == `_build_model(model=None)` == `_build_model(model="")` == settings default; `agent/loop.py` + `tui/` absent from `git diff --stat`; `test_app_e2e` (20) green. (PASS)
- Break path 4 (boundary: whitespace-only `"   "`): truthy → passes through as the model id (NOT defaulted). Correct plumbing behavior at this seam — validation belongs to the future `decode run --model` flag (#069); noted, not blocking.

**Acceptance criteria**
- [x] PASS — `_build_model`/`build_agent` take `model=None`; per-provider `model=None` == `settings.<provider>_model` — `test_build_model_with_none_matches_the_settings_default[gemini|openrouter|modal-authenticated|modal-unauthenticated]`; reproduced offline (all == default).
- [x] PASS — `model="gemini-2.5-pro"` sets the `GoogleModel` id, provider/auth unchanged — `test_gemini_override_keeps_the_google_provider_and_settings_key`; probe: id moved, `GoogleProvider` + key `"settings-secret-key…"` intact.
- [x] PASS — override changes only the id never the provider (openrouter stays `OpenRouterProvider`) — `test_openrouter_override_keeps_the_openrouter_provider_and_key` + `test_build_model_override_sets_only_the_model_id[*]`; probe confirmed.
- [x] PASS — both flows accept the kwarg; no-model `run(task="x")` round-trips offline — `test_flow_round_trips_a_task_and_returns_the_agents_text` (+ 2) green with `lambda model=None` seams; probe no-model run recorded `model=None`.
- [x] PASS — real `_build_runtime_agent(model="gemini-2.5-pro")` inner id is the override — `test_build_runtime_agent_threads_the_model_override_to_the_inner_agent` (+ HITL parallel `test_flow.py`).
- [x] PASS — interactive TUI byte-unchanged — `git diff --stat` touches only `agent/factory.py` + `runtime/flow.py` in `src/`; `tui/app.py:785` is `build_agent()` (no model); `test_run_app_*` green.
- [x] PASS — capstone parameter-serialization ripple handled — independent probe: recorded set is genuinely `{"task","model"}` (not rubber-stamped); all 4 updated `== {"task","model"}` sites (`test_runtime_capstone.py:627`, `test_credentials_proxy.py`, `test_secret_store_config.py` ×2) pass; no raw key in `model_dump_json()`.
- [x] PASS — `make ci`-equivalent green (format/lint/unit/integration), 0 warnings; `uv lock --check` clean (`make pre-commit` + `make integration-tests` reproduced locally).

**Evidence**
```
$ make pre-commit
============================ 1027 passed in 56.74s =============================
$ make integration-tests
tests/integration/test_runtime_capstone.py .......                       [ 95%]
============================= 20 passed in 39.92s ==============================
$ uv run pytest tests/unit/decode/agent/test_factory.py tests/unit/decode/runtime/test_flow.py
============================== 41 passed in 7.63s ==============================
# independent offline flow probe (temp test, since removed):
PROBE recorded parameter set : {'model', 'task'}   model=gemini-2.5-pro   sentinel-key-in-payload=False
PROBE no-model param set     : {'model', 'task'}   model=None
```

**Other issues found**
- None blocking. Non-blocking: a whitespace-only override (`"   "`) is truthy and passes through as the model id — acceptable at this pure-plumbing seam; input validation is the future `--model` flag's job (#069). The SWE's extra edits beyond the task's enumerated list (`test_hitl.py`, `test_credentials_proxy.py`, `test_secret_store_config.py`, and the 3 extra param-serialization sites) are correct ripple — the flow now calls the seams with an argument and records `model` as a flow input — not masking a regression; verified by the green suite + independent payload probe.

**VERDICT: PASS**
