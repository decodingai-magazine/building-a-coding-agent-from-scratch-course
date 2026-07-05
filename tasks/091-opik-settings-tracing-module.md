---
id: 091-opik-settings-tracing-module
feature: opik-observability
status: done
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

- [x] The four settings fields exist with the defaults above; `Settings()` builds clean and
  `reload_settings()` still emits zero warnings under `filterwarnings=["error"]`.
- [x] `.env.example` Opik block is fully commented out, documents all four vars, and states the
  presence-based silent-no-op default; copying `.env.example` to `.env` no longer sets a truthy
  `OPIK_API_KEY`.
- [x] **Hermetic (no key, no network):** with `opik_api_key == ""`, `init_tracing()` returns `False`,
  calls no `logfire.configure`, emits no span, and mutates no `os.environ` `OTEL_*` var (assert the
  environ is unchanged). `is_tracing_active()` is `False`. `root_span(...)` is a `nullcontext`.
- [x] **Hermetic (fake key, in-memory):** with a fake `opik_api_key` set and `logfire.testing`'s
  in-memory exporter installed, `init_tracing()` returns `True`, calls `logfire.configure(...)` +
  `logfire.instrument_pydantic_ai()` exactly once, builds the OTLP exporter with
  endpoint `<base>/v1/traces` and the three headers from settings, logs exactly one INFO line, and is
  idempotent (a second `init_tracing()` reconfigures nothing). No real network call is made.
- [x] `opik_url_override` unset → cloud base; set → the override base is used verbatim (self-host).
- [x] `reset_tracing()` clears the flag so a subsequent `init_tracing()` re-drives; the autouse
  `_no_opik_tracing` fixture keeps the whole suite from configuring real export.
- [x] `uv.lock` updated; `make ci` (lock-check + format + lint + tests) green with no key/network.
- [x] `tests/unit/decode/observability/test_tracing.py` mirrors the new module 1:1.

## Out of scope

- Wiring `init_tracing()` into `run_app` or the flows, and any root span (092/093).
- The capstone span-tree assertions and the live Opik smoke (095).
- README / AGENTS.md prose (094).
- The `opik` SDK / `OpikSpanProcessor` / `@opik.track` (documented escape hatch only — ADR-0014).

## Log

### [SWE] 2026-07-05 10:55 — Implementation

**Files modified**
- `pyproject.toml` — added `logfire>=4.37.0` (runtime group; the ONLY new top-level dep).
- `uv.lock` — relocked; logfire + OTel/protobuf transitive deps (versions below).
- `src/decode/config/settings.py` — new `# --- Observability: Opik (ADR-0014) ---` block: 4 fields
  (`opik_api_key` SecretStr, `opik_workspace`, `opik_project_name`, `opik_url_override`).
- `src/decode/observability/__init__.py` (new, 23 lines) — re-exports the 4-function public surface.
- `src/decode/observability/tracing.py` (new, 119 lines) — `init_tracing` / `is_tracing_active` /
  `root_span` / `reset_tracing`; presence-based + idempotent; settings-driven OTLP exporter, no OTEL env.
- `.env.example` — replaced the stale UNCOMMENTED `OPIK_API_KEY=changeme` block with a fully commented
  block documenting all 4 vars + the presence-based silent-no-op default.
- `tests/conftest.py` — autouse `_no_opik_tracing` fixture (blanks `settings.opik_api_key`; mirrors
  `_no_real_provider_key`).
- `tests/unit/decode/observability/test_tracing.py` (new, 225 lines) — mirrors the module 1:1.
- `tests/unit/decode/test_logfire_dependency.py` (new, 26 lines) — dep smoke test (mirrors
  `test_kitaru_dependency.py`).
- `tests/unit/decode/config/test_settings.py` — Opik settings block tests (defaults / env / dotenv /
  repr-safety / env.example drift + no-activate-on-copy).

**Tests**
- Unit: 1473 passing, 0 failing (`make pre-commit` = format-check + lint-check + unit-tests, green).
  New this task: 24 tracing + settings + dependency tests.
- Integration: full `make ci` (lock-check + format + lint + unit + integration) GREEN — **1578 passed**
  in 455s, exit 0 (every integration capstone incl. milestone1 / runtime / sandbox / subagents /
  credential-proxy / modal+docker executors). My changes touch no integration surface (only the
  additive key-blanking autouse fixture), and nothing broke.
- End-to-end: exercised the REAL module in a fresh process with only the OTLP network transport stubbed
  (in-memory exporter) — no-key no-op, fake-key activate (real `logfire.configure` +
  `instrument_pydantic_ai` + `BatchSpanProcessor`), a real `chat_turn` span with `thread_id` captured,
  idempotency, reset. One INFO line fired: `Opik tracing active — project=… target=cloud`. No network.

**Dependency resolution (the task's flagged escalation point)** — `uv add logfire` co-resolved cleanly
against kitaru→zenml + modal; NO conflict, NO forced pins. `uv lock --check` green. Landed in `uv.lock`:
`logfire==4.37.0`, `opentelemetry-api==1.40.0`, `opentelemetry-sdk==1.40.0`,
`opentelemetry-exporter-otlp-proto-http==1.40.0` (+ `-proto-common`, `opentelemetry-proto` 1.40.0),
`protobuf==6.33.6`, plus `googleapis-common-protos==1.75.0`, `executing==2.2.1`. logfire is the only new
top-level dep; the OTLP exporter arrives transitively as ADR-0014 predicted.

**Acceptance criteria**
- [x] Four settings fields with defaults; `Settings()` builds clean, `reload_settings()` zero-warning
  under `filterwarnings=["error"]` — `test_opik_defaults`, `test_reload_settings_rebuilds_the_singleton_in_place`.
- [x] `.env.example` block commented out, all 4 vars documented, no truthy `OPIK_API_KEY` on copy —
  `test_env_example_lists_every_opik_var`, `test_copying_env_example_to_dotenv_does_not_activate_opik`.
- [x] Hermetic no-key: `False`, no configure, no span, `OTEL_*` environ unchanged, nullcontext —
  `test_init_tracing_without_key_returns_false_and_configures_nothing`,
  `test_init_tracing_without_key_leaves_otel_environ_unchanged`, `test_root_span_is_nullcontext_when_inactive`.
- [x] Hermetic fake-key: `True`, configure + instrument once, exporter `<base>/v1/traces` + 3 headers,
  one INFO line, idempotent, no network — `test_init_tracing_with_key_*`,
  `test_init_tracing_builds_cloud_exporter_with_settings_headers`,
  `test_init_tracing_logs_one_info_line_naming_project_and_target`, `test_init_tracing_is_idempotent`,
  `test_root_span_emits_a_real_span_captured_in_memory` (logfire.testing in-memory).
- [x] url_override unset → cloud base; set → override base verbatim —
  `test_init_tracing_uses_url_override_base_when_set` + the cloud-base test.
- [x] `reset_tracing()` re-drives; autouse `_no_opik_tracing` keeps the suite from real export —
  `test_reset_tracing_allows_reinit` + suite green.
- [x] `uv.lock` updated; `make ci` green with no key/network (unit+lock+format+lint confirmed; full run in hand-off).
- [x] `test_tracing.py` mirrors the module 1:1.

**Evidence**
```
$ make pre-commit
======================= 1473 passed in 108.01s (0:01:48) =======================
$ uv lock --check
Resolved 155 packages in 3ms          # in sync
$ uv run python e2e_opik.py           # real pipeline, network transport stubbed
INFO:decode.observability.tracing:Opik tracing active — project=e2e-proj target=cloud
[1] no-key path: init_tracing()->False, root_span is nullcontext  OK
[2] fake-key path: init_tracing()->True, idempotent, real logfire.configure ran  OK
[3] real span emitted end-to-end: name='chat_turn' thread_id='sess-e2e' (captured 1 span(s))  OK
[4] reset_tracing(): is_tracing_active()->False  OK
E2E PASS — no network call made
```

**Notes**
- **Deliberate test choice (minor deviation from AC wording):** the fake-key wiring assertions mock the
  logfire + OTLP boundary (assert `configure`/`instrument` called once with the right endpoint+headers)
  rather than driving the real global `logfire.configure` under `logfire.testing`. Mocking the exporter
  is a *stronger* no-network guarantee (the real HTTP exporter is never constructed) and avoids
  process-global TracerProvider pollution across tests. The AC's `logfire.testing` in-memory path IS
  exercised in `test_root_span_emits_a_real_span_captured_in_memory` (capfire) + the e2e, proving a real
  span flows. logfire.testing's span-tree assertions are ADR-0014 §7's tool for 095.
- No `InstrumentationSettings` passed (pydantic-ai 1.95 defaults are correct; verified no
  `PydanticAIDeprecationWarning` under `filterwarnings=["error"]`).
- `root_span` from a real `.py` file emits no `InspectArgumentsFailedWarning` (that warning only fires
  under exec/no-source, e.g. `python -c`); verified so `filterwarnings=["error"]` stays green.
- Left the stale `opik/modal/kitaru` "to add later" comment block in `pyproject.toml` untouched — it is
  already unmaintained (modal + kitaru are top-level deps yet still listed), and prose is task 094's scope.
- No call sites wired (settings + module only, like 041/050/057/071); 092/093 open `root_span` / call
  `init_tracing()`. NOT COMMITTED — awaiting Tester.

### [Tester] 2026-07-05 11:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 178 files clean; `ruff check` all passed)
- Unit tests: 1473 passed / 0 failed
- Integration tests: 105 passed / 0 failed (360s, exit 0 — all capstones incl. milestone1 / runtime /
  sandbox / subagents / credential-proxy / modal+docker executors)
- `uv lock --check`: in sync (155 packages)
- Warnings: 0 (repo `filterwarnings=["error"]`; also re-ran the new tests under extra
  `-W error::DeprecationWarning,PendingDeprecationWarning,UserWarning` — clean)

**E2E adversarial pass** (fresh-process, REAL module, no mocks; scratchpad `adv_091.py`)
- Happy path: real `init_tracing()` both paths — no-key → `False`/`nullcontext`; fake-key (localhost
  override, no span → no network) → `True`, real `logfire.configure` + `instrument_pydantic_ai` +
  `OTLPSpanExporter` + `BatchSpanProcessor` ran. (PASS)
- Break path 1 (boundary — copy-through, the AC's real-world scenario): `cp .env.example .env` on a
  real temp dir, `Settings(_env_file=…)` → `opik_api_key == ""`, workspace/project/url defaults intact;
  no UNCOMMENTED `OPIK_API_KEY=` line exists. The `changeme` bug is dead. (PASS)
- Break path 2 (state — no-key purity + mutation): snapshot the **FULL** `os.environ` before/after a
  no-key `init_tracing()` → byte-identical, zero `OTEL_*` set, `is_tracing_active()` False,
  `root_span` (both `thread_id=…` and default `None`) a `nullcontext`. MUTATION-CHECK: inverted the
  `if not key` guard → `test_init_tracing_without_key_*` both went RED (`assert True is False`) →
  reverted byte-exact, suite green again. Tests are non-vacuous. (PASS)
- Break path 3 (malformed — trailing-slash URL override): `opik_url_override=".../otel/"` →
  endpoint `.../otel//v1/traces` (double slash; confirmed on the REAL `OTLPSpanExporter._endpoint`).
  No crash; the documented example (settings docstring / `.env.example` / spec) uses the no-slash form.
  LOW-severity finding, see Other issues. (PASS — not on the documented happy path)
- Break path 4 (hostile env — hermeticity guard): ran a scratch test with
  `OPIK_API_KEY=a-real-looking-dev-key` in the environment → the autouse `_no_opik_tracing` fixture
  scrubbed it (delenv + blanks singleton) → `init_tracing()` returned `False`. Real dev keys cannot
  leak into the suite. (PASS; scratch test deleted after)
- Break path 5 (idempotency + reset, REAL path): `init_tracing()` twice → `True`/`True`,
  `is_tracing_active()` stays True (second call short-circuits on `_active`); `reset_tracing()` →
  False; `init_tracing()` re-drives → True. (PASS)
- Suite hygiene: `import decode.observability` is side-effect-free (flag stays False even with a key in
  env — activation only on explicit `init_tracing()`); no `pytest-randomly` installed; observability +
  settings + observability-again in ONE process (78 passed) → no global-provider / `_active` leak.

**Acceptance criteria**
- [x] PASS — Four settings fields + defaults; `Settings()` clean, `reload_settings()` zero-warning —
      `test_opik_defaults`, `test_reload_settings_rebuilds_the_singleton_in_place`;
      `settings.py:138-144`; Probe E2 ran `reload_settings()` under `-W error` clean.
- [x] PASS — `.env.example` fully commented, all 4 vars documented, silent-no-op stated, copy sets no
      truthy key — `test_env_example_lists_every_opik_var`,
      `test_copying_env_example_to_dotenv_does_not_activate_opik`; Probe A real fs copy → `""`;
      `.env.example:57-70`.
- [x] PASS — Hermetic no-key: `False`, no configure, no span, `OTEL_*`/full environ unchanged,
      `is_tracing_active()` False, `root_span` nullcontext —
      `test_init_tracing_without_key_returns_false_and_configures_nothing`,
      `test_init_tracing_without_key_leaves_otel_environ_unchanged`,
      `test_root_span_is_nullcontext_when_inactive`; Probe B (full-environ snapshot) + mutation-check.
- [x] PASS — Hermetic fake-key: `True`, configure+instrument once, exporter `<base>/v1/traces` + 3
      headers, one INFO line (key never logged), idempotent, no network —
      `test_init_tracing_with_key_returns_true_and_configures_once`,
      `test_init_tracing_builds_cloud_exporter_with_settings_headers`,
      `test_init_tracing_logs_one_info_line_naming_project_and_target`,
      `test_init_tracing_is_idempotent`, `test_root_span_emits_a_real_span_captured_in_memory` (capfire
      in-memory). Deviation judged ACCEPTABLE — see note.
- [x] PASS — url_override unset → cloud base; set → override base verbatim —
      `test_init_tracing_builds_cloud_exporter_with_settings_headers` (cloud),
      `test_init_tracing_uses_url_override_base_when_set`; Probe D on the REAL exporter endpoint.
- [x] PASS — `reset_tracing()` re-drives; autouse `_no_opik_tracing` keeps the suite off real export —
      `test_reset_tracing_allows_reinit` (configure call_count == 2 after reset); Break path 4 (hostile
      env scrubbed) + Probe C (real reset→re-drive); 1473-test suite green with the fixture active.
- [x] PASS — `uv.lock` updated; `make ci` green no key/network — `uv lock --check` in sync (155 pkgs);
      format+lint+unit(1473)+integration(105) all green; `logfire` the only new top-level dep
      (`opentelemetry-api/sdk` pre-existed via zenml; `logfire`+`otlp-proto-http/common/proto` added
      transitively, no forced bumps).
- [x] PASS — `test_tracing.py` mirrors the module 1:1 —
      `tests/unit/decode/observability/test_tracing.py` (12 tests) + `test_logfire_dependency.py` dep
      smoke.

**Byte-unchanged agent (spot-check)**: `grep -rn` over `src/decode` (minus the module) finds NO import
of `decode.observability` / `init_tracing` / `root_span` / `logfire` — only a comment mention in
`settings.py:134`. Agent is byte-unchanged, as scoped (092/093 wire the seam).

**Evidence**
```
$ make unit-tests
======================= 1473 passed in 101.00s (0:01:40) =======================
$ make integration-tests
======================= 105 passed in 360.09s (0:06:00) ========================
$ uv lock --check
Resolved 155 packages in 2ms
$ uv run python adv_091.py            # fresh process, real module, no mocks
Probe A/B/C/D … All hard checks passed
$ # mutation: invert `if not key` guard →
FAILED test_init_tracing_without_key_returns_false_and_configures_nothing (assert True is False)
FAILED test_init_tracing_without_key_leaves_otel_environ_unchanged (assert True is False)
$ # reverted byte-exact → 12 passed
```

**Deviation judgment (SWE's declared minor deviation) — ACCEPTABLE**
The fake-key wiring assertions mock the logfire+OTLP boundary rather than driving the real global
`logfire.configure` under `logfire.testing`. The AC's intent — (a) no real network, (b) real behavior
proven, (c) precise wiring asserted — is fully met: the mock path is a *stronger* no-network guarantee
(the real HTTP exporter is never constructed), the capfire `test_root_span_emits_a_real_span_captured_in_memory`
proves a real span flows in-memory, and I **independently exercised the REAL `init_tracing()`** path
(real `logfire.configure` + `instrument_pydantic_ai` + `OTLPSpanExporter` + `BatchSpanProcessor`, twice,
idempotent, with reset) in a fresh process (Probe C) with zero network. Not taken on faith.

**Other issues found** (non-blocking — follow-ups, orchestrator decides)
- LOW: `opik_url_override` with a **trailing slash** yields a double slash in the endpoint
  (`.../otel//v1/traces`). Harmless on tolerant collectors, but a strict self-hosted proxy could 404.
  Off the documented happy path (all examples use the no-slash form) so it does not fail AC5. Optional
  hardening for when 092/093 wire it: `base = (settings.opik_url_override or _CLOUD_OTLP_BASE).rstrip("/")`.
- INFO: a whitespace-only `opik_api_key` (e.g. `"   "`) is truthy and would activate tracing. Not a
  realistic input (the `changeme` regression that mattered is dead); noting only for completeness.

**VERDICT: PASS**
