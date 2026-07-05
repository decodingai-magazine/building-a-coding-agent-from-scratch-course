---
id: 091-opik-settings-tracing-module
feature: opik-observability
status: pending
---

# Opik observability — settings, tracing module, init seam, dependency

Tags: `observability`, `opik`, `config`, `deps`
Depends on: None
Blocks: #092, #093, #094, #095

## Scope

The foundation for Opik monitoring (ADR-0014): a settings block, ONE small observability
module, the presence-based `init_tracing()` seam, the `logfire` dependency, and the test-isolation
guard. No call sites are wired yet (like the settings-only precedents 041/050/057/071) — the agent
runs byte-unchanged; 092/093 call the seam. This task is independently shippable and leaves the
codebase working.

- **Settings** — add a `# --- Observability: Opik (ADR-0014) ---` block to
  `src/decode/config/settings.py` (after the Logging block, before the tuning blocks):
  - `opik_api_key: SecretStr = SecretStr("")` — the presence trigger (set → tracing on).
  - `opik_workspace: str = "default"` — the `Comet-Workspace` OTLP header.
  - `opik_project_name: str = "decode"` — the `projectName` OTLP header.
  - `opik_url_override: str | None = None` — the OTLP **base** URL override; `None` → Comet cloud
    base `https://www.comet.com/opik/api/v1/private/otel`; set to a self-host base, e.g.
    `http://localhost:5173/api/v1/private/otel`. The exporter appends `/v1/traces`.
- **`.env.example` fix** — replace the current stale block (`.env.example:57-59`, which has an
  UNCOMMENTED `OPIK_API_KEY=changeme` + `# OPIK_WORKSPACE=default`). Now that a real
  `opik_api_key` field exists, an uncommented `changeme` in a copied `.env` would make settings load
  a truthy key and try to activate tracing against Comet with a bogus token. Comment the whole block
  out (presence-based enablement), document all four vars, and explain the silent-no-op default.
- **Module** — new `src/decode/observability/__init__.py` and `src/decode/observability/tracing.py`
  (ONE small file — ponytail). Public surface:
  - `init_tracing() -> bool` — presence-based + **idempotent** (guarded by a module `_active` flag,
    since `logfire.configure` sets a process-global `TracerProvider`). When
    `settings.opik_api_key` is empty: no-op, returns `False`, imports/configures nothing observable.
    When set: build the OTLP base (`opik_url_override` or the cloud default), construct
    `opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter(endpoint=f"{base}/v1/traces",
    headers={"Authorization": key, "Comet-Workspace": ws, "projectName": project})`, call
    `logfire.configure(send_to_logfire=False,
    additional_span_processors=[BatchSpanProcessor(exporter)])`, then
    `logfire.instrument_pydantic_ai()` (GLOBAL — covers the main loop, memory write-back, compaction,
    and subagents in one call), log **one** INFO line naming the project + target kind (cloud /
    self-hosted), return `True`. Do NOT pass `InstrumentationSettings` — the pydantic-ai defaults are
    exactly what we want (`include_content=True`, `include_binary_content=True`, `version=5`,
    `use_aggregated_usage_attribute_names=True`); passing `version` 2–4 would emit a
    `PydanticAIDeprecationWarning` that `filterwarnings=["error"]` turns into a failure.
  - `is_tracing_active() -> bool` — cheap read of the module flag.
  - `root_span(name: str, *, thread_id: str | None = None) -> AbstractContextManager` — a thin
    wrapper: `logfire.span(name, thread_id=thread_id)` when active (thread_id as a span attribute —
    the docs' `logfire.span("chat_turn", thread_id=…)` pattern Opik maps to a trace thread), else
    `contextlib.nullcontext()`. This is what 092/093 open; keeping it here keeps the module cohesive.
  - `reset_tracing() -> None` — clears the module flags for test hermeticity (mirrors
    `bash.reset_executor` / `agent.reset_main_agent`); span-asserting tests own provider isolation via
    `logfire.testing`.
- **Dependency** — `uv add logfire` (runtime group). It transitively brings
  `opentelemetry-exporter-otlp-proto-http` + `opentelemetry-sdk` + `protobuf`, so this is the ONLY new
  top-level dep. Do NOT add `logfire[httpx]` or `opik`. Update `uv.lock`; `make ci` (`uv lock --check`)
  must stay green. If the resolver cannot satisfy logfire's OTel/protobuf pins against kitaru→zenml /
  modal, STOP and surface it — that is a real conflict, not a nit.
- **Test isolation** — add an autouse `_no_opik_tracing` fixture to `tests/conftest.py` (mirroring
  `_no_real_provider_key` / `_default_sandbox_mode`) that blanks `settings.opik_api_key` so
  `init_tracing()` no-ops in every ordinary test once it is wired (092+). Span-asserting tests opt in
  with a fake key + `logfire.testing`.

## Acceptance Criteria

- [ ] The four settings fields exist with the defaults above; `Settings()` builds clean and
  `reload_settings()` still emits zero warnings under `filterwarnings=["error"]`.
- [ ] `.env.example` Opik block is fully commented out, documents all four vars, and states the
  presence-based silent-no-op default; copying `.env.example` to `.env` no longer sets a truthy
  `OPIK_API_KEY`.
- [ ] **Hermetic (no key, no network):** with `opik_api_key == ""`, `init_tracing()` returns `False`,
  calls no `logfire.configure`, emits no span, and mutates no `os.environ` `OTEL_*` var (assert the
  environ is unchanged). `is_tracing_active()` is `False`. `root_span(...)` is a `nullcontext`.
- [ ] **Hermetic (fake key, in-memory):** with a fake `opik_api_key` set and `logfire.testing`'s
  in-memory exporter installed, `init_tracing()` returns `True`, calls `logfire.configure(...)` +
  `logfire.instrument_pydantic_ai()` exactly once, builds the OTLP exporter with
  endpoint `<base>/v1/traces` and the three headers from settings, logs exactly one INFO line, and is
  idempotent (a second `init_tracing()` reconfigures nothing). No real network call is made.
- [ ] `opik_url_override` unset → cloud base; set → the override base is used verbatim (self-host).
- [ ] `reset_tracing()` clears the flag so a subsequent `init_tracing()` re-drives; the autouse
  `_no_opik_tracing` fixture keeps the whole suite from configuring real export.
- [ ] `uv.lock` updated; `make ci` (lock-check + format + lint + tests) green with no key/network.
- [ ] `tests/unit/decode/observability/test_tracing.py` mirrors the new module 1:1.

## Out of scope

- Wiring `init_tracing()` into `run_app` or the flows, and any root span (092/093).
- The capstone span-tree assertions and the live Opik smoke (095).
- README / AGENTS.md prose (094).
- The `opik` SDK / `OpikSpanProcessor` / `@opik.track` (documented escape hatch only — ADR-0014).

## Log
