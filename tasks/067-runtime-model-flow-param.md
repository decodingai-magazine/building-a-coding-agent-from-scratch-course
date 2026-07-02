---
id: 067-runtime-model-flow-param
feature: runtime-replay
status: pending
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

- [ ] `_build_model(*, flow_mode=False, model=None)` and `build_agent(*, flow_mode=False, model=None)`
      exist; with `model=None` the constructed model id equals `settings.<active-provider>_model`
      exactly as before (unit test per provider: `gemini`/`openrouter`/`modal`, offline — model
      construction issues no request).
- [ ] With `model="gemini-2.5-pro"` and `LLM_PROVIDER=gemini`, the constructed `GoogleModel`'s model
      id is `"gemini-2.5-pro"` (not the settings default); the provider/auth path is unchanged
      (still `GoogleProvider`, still the settings/`flow_mode` key resolution). Analogous unit
      assertions for `openrouter` (`OpenRouterProvider`) and `modal` (custom `AsyncOpenAI`).
- [ ] The override changes **only** the model id, never the provider: with `LLM_PROVIDER=openrouter`
      and `model="some-id"`, the model is still an `OpenAIChatModel` on `OpenRouterProvider` (a test
      asserts provider class unchanged).
- [ ] `run_agent_task(task, model=None)` and `run_agent_task_hitl(task, model=None)` accept the
      keyword; `run_agent_task.run(task="x")` (no model) still round-trips through the real `@flow`
      offline and returns the scripted text (existing `test_flow.py` tests pass with updated seam
      patches).
- [ ] A real (seed-key, offline) `_build_runtime_agent(model="gemini-2.5-pro")` builds a
      `KitaruAgent` whose inner agent's model id is `"gemini-2.5-pro"` (extends
      `test_build_runtime_agent_wraps_build_agent_in_a_named_kitaru_agent`, `test_flow.py:96`).
- [ ] **Interactive TUI byte-unchanged:** `agent/loop.py`, `tui/`, and the interactive `build_agent()`
      call sites are not in the diff; `test_run_app_*` stay green.
- [ ] **Capstone parameter-serialization ripple handled:** re-run
      `tests/integration/test_runtime_capstone.py`; if adding `model=None` changes the recorded flow
      inputs, update `test_credentials_proxy_sources_the_key_and_keeps_it_off_the_payload`
      (`:627` `assert set(run.config.parameters) == {"task"}`) to the observed set (e.g.
      `{"task", "model"}`), and confirm neither raw key leaks into `run.config.model_dump_json()`
      (`:629-630` still pass — a model id is not a secret).
- [ ] `make ci` green, **0 warnings**; `uv lock --check` passes.

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
