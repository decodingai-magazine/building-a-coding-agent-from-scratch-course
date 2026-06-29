---
id: 061-runtime-credentials-proxy
feature: kitaru-runtime
status: done
---

# Credentials proxy: resolve the model key via Kitaru secrets at construction (flow mode)

Tags: `runtime`, `infra`, `agent`
Depends on: #058
Blocks: #062

This task implements ADR-0008 §5 and honors the AGENTS.md invariant *"secrets never reach the model
or the sandbox payload."* In **flow mode**, model construction resolves the provider API key through
Kitaru secrets instead of the direct `settings.<provider>_api_key.get_secret_value()` at
`agent/factory.py:115`, so a (later, deployed) flow payload carries **handles, not raw keys**.
Interactive in-process runs keep reading `SecretStr` from settings unchanged.

**Honest caveat (drives the AC below):** the Kitaru secrets proxy for the *model key* is the
**least-exampled** surface — no Agent Harness Platform example wires it; the credential-proxy example
injects HTTP headers for the *sandbox*, and the docs-backed PydanticAI path is **env injection**
(`@flow(image=kitaru.ImageSettings(secret_environment_from=[...]))`) because the adapter needs a
concrete model at construction. So this task **verifies the secrets API against the installed SDK /
context7 first**, and ships the env-injection seam as the documented fallback.

## Scope

Verify against the installed SDK + context7 `/kitaru/guides/secrets.md`,
`/kitaru/guides/secrets-and-model-registration.md`, `/kitaru/adapters/pydantic-ai.md` **before
coding** (pre-1.0). Confirmed API at grooming: `from kitaru import create_secret, get_secret,
delete_secret`; `create_secret(name, {KEY: val}, private=True)`; `get_secret(name).get("KEY")` /
`.values`.

- **Gate:** all of this is behind `settings.runtime_credentials_proxy_enabled` (default `False`, from
  057). When `False` (the default) **or** interactive, `_build_model()` is byte-unchanged (reads
  `SecretStr` from settings). When `True` **and** in flow mode, resolve the key via Kitaru.
- **Primary path (explicit handle):** at the model-construction seam, when enabled+flow-mode, read
  the provider key with `get_secret(settings.runtime_secret_name).get("<PROVIDER>_API_KEY")` (e.g.
  `GEMINI_API_KEY` / `OPENROUTER_API_KEY`) and pass it to the provider exactly where the `SecretStr`
  value is used today. Thread flow-mode awareness into `_build_model` cleanly (a small param or a
  module seam — do not read `os.environ` deep in the factory; AGENTS.md).
- **Documented fallback (env injection):** if the explicit-handle path does not round-trip on the
  local stack, the flow declares the secret on its image —
  `@flow(image=kitaru.ImageSettings(secret_environment_from=[settings.runtime_secret_name]))` — so the
  provider SDK reads the key from the injected env at construction. Pick whichever round-trips on the
  installed SDK; record the choice and why in the task log.
- **Operator setup:** document creating the secret once —
  `kitaru secrets set decode-llm-creds --GEMINI_API_KEY=…` (CLI) or `create_secret("decode-llm-creds",
  {"GEMINI_API_KEY": …}, private=True)` (Python) — and that the raw key then lives only in Kitaru, not
  in the flow payload. Add the `RUNTIME_CREDENTIALS_PROXY_ENABLED` / `RUNTIME_SECRET_NAME` usage to
  the README runtime section.
- **Invariant check:** the resolved-handle path must not log or echo the raw key; the model is
  constructed with the key but the *flow payload* (the serialized `run_agent_task` arguments) carries
  only the task string + the secret *name*.

## Acceptance criteria

- [x] **Verify-first:** the SWE log records the secrets API confirmed against the installed SDK
      (`get_secret(...).get(...)` shape) and which path round-trips on the local stack
      (explicit-handle vs env-injection); the shipped code matches that finding.
- [x] With `runtime_credentials_proxy_enabled=False` (default) **or** interactive mode,
      `_build_model()` behavior is byte-identical to today (reads `settings.<provider>_api_key`); the
      existing factory/provider tests pass unchanged.
- [x] With `runtime_credentials_proxy_enabled=True` in flow mode, the provider key is resolved from
      Kitaru (`get_secret(runtime_secret_name)` **or** the env injected via `secret_environment_from`),
      not from `settings.<provider>_api_key`; a unit test patches `kitaru.get_secret` (or the env seam)
      and asserts the model is constructed with the secret-sourced key and that `settings.gemini_api_key`
      is **not** read on that path.
- [x] The flow payload carries only the task string + the secret **name** — never the raw key; a test
      asserts the raw key value does not appear in the serialized flow arguments / logs.
- [x] Works for at least the default `gemini` provider end-to-end through the seam (offline, patched);
      the openrouter/modal branches are covered or explicitly deferred-with-reason in the log.
- [x] If neither Kitaru secrets path round-trips on the installed SDK, the task ships the
      env-injection fallback and an Open-Question note rather than a broken handle path (no silent
      raw-key leak). — N/A: the explicit-handle path round-trips, so it ships (no fallback needed).
- [x] `make ci` green, 0 warnings.

## User stories

### Story: A deployed flow never carries the raw key
1. An operator runs `kitaru secrets set decode-llm-creds --GEMINI_API_KEY=…` once and sets
   `RUNTIME_CREDENTIALS_PROXY_ENABLED=true`.
2. A `decode run` flow constructs the Gemini model with the key resolved from the Kitaru secret.
3. Inspecting the flow execution's arguments shows only the task and the secret name — the raw key is
   not in the payload.

### Story: The interactive REPL is unaffected
1. A developer runs bare `decode` with `GEMINI_API_KEY` in `.env` and the proxy disabled (default).
2. The model is constructed exactly as before (SecretStr from settings) — no Kitaru secret lookup,
   no behavior change.

### Story: The proxy is opt-in and safe by default
1. A developer enables `RUNTIME_CREDENTIALS_PROXY_ENABLED=true` but has not created the secret.
2. The flow surfaces Kitaru's missing-secret error (or the documented env-injection fallback), not a
   raw traceback or a silent fallback that leaks the settings key into the payload.

## Out of scope
- Sandbox HTTP header injection / the proxy-container pattern (that is the sandbox feature, a later
  step) — this task is only the **model-construction** credential seam.
- Model-alias registration (`kitaru model register`) as the primary path — noted as an option, not
  required for MVP.
- Rotating/deleting secrets tooling beyond documenting `create_secret`/`delete_secret`.

## Log

### [SWE] 2026-06-28 13:10 — Implementation

**Verify-first findings (AC1)**

Confirmed the Kitaru secrets API against the **installed SDK (kitaru 0.18, `.venv/.../kitaru/__init__.py`)**
AND the docs (`/kitaru/guides/secrets.md`):
- `from kitaru import create_secret, get_secret, delete_secret` — present.
- `create_secret(name, values: Mapping, *, private: bool = False) -> SecretSummary` (metadata only, no raw values).
- `get_secret(name_or_id) -> Secret`; `Secret.values: dict[str, str]` + `Secret.get(key, default=None)`.
  So the groomed shape `get_secret(name).get("GEMINI_API_KEY")` is exact.
- Missing secret → `kitaru.errors.KitaruRuntimeError("Secret \`<name>\` was not found.")` — a clear
  message, not a raw traceback. Key-absent-within-secret → `.get(...)` returns `None`.

**Which path round-trips on the local stack:** the **explicit-handle path round-trips fully offline**
(no Kitaru server, no network) on the isolated local stack — I created a secret with `create_secret(...)`
and read it back with `get_secret(...).get("GEMINI_API_KEY")` in a hermetic temp store (mirroring the
runtime conftest isolation) and got the value back verbatim. So decode **ships the explicit-handle path**,
NOT the env-injection (`secret_environment_from`) fallback. AC6 therefore N/A. Docs note the lookup must
run inside the flow/checkpoint body (runtime context) — our seam does exactly that: the runtime calls
`build_agent(flow_mode=True)` from inside the `@flow`, so `get_secret` runs in the flow body.

**Design / seam:** threaded flow-mode awareness as a keyword-only param (`build_agent(*, flow_mode=False)`
→ `_build_model(*, flow_mode=False)`), not `os.environ`. The single-api-key branches (gemini/openrouter)
source their key through one helper `_provider_api_key(provider, *, flow_mode)`: default/interactive →
`settings.<provider>_api_key.get_secret_value()` (byte-identical behaviour); flow-mode + proxy-enabled →
`_resolve_key_via_proxy(provider)` (lazy `from kitaru import get_secret`, so the REPL path never imports
kitaru). `modal` deliberately stays on settings — it authenticates with dual Modal proxy *tokens* (a
header surface, the later sandbox step), not a single api_key; documented in code + below.

**Files modified**
- `src/decode/agent/factory.py` — `flow_mode` param on `build_agent`/`_build_model`; `_PROXY_SECRET_KEY`
  map + `_provider_api_key` + `_resolve_key_via_proxy` (Credentials Proxy seam, raw key never logged).
- `src/decode/runtime/flow.py` — `_build_runtime_agent` / `_build_hitl_runtime_agent` now call
  `build_agent(flow_mode=True)` so the proxy engages inside the flow body.
- `tests/unit/decode/agent/test_factory_credentials_proxy.py` — proxy gate + resolution + raw-key-not-logged
  + missing-secret / missing-key error paths (patched `kitaru.get_secret`, offline construction-only).
- `tests/unit/decode/runtime/test_credentials_proxy.py` — real local-stack round-trip through the seam
  (gemini), `_build_runtime_agent` wiring, and the payload invariant (persisted `run.config.parameters`
  carry only `{"task": ...}` — neither the Kitaru nor the settings raw key appears).
- `README.md` — Credentials-proxy operator subsection (`kitaru secrets set` / `create_secret`,
  `RUNTIME_CREDENTIALS_PROXY_ENABLED` / `RUNTIME_SECRET_NAME`, raw key lives only in Kitaru).

**Tests**
- Unit: 998 passing, 0 failing (`make ci`, includes the 13 new proxy tests). Integration: 12 passing.
- New proxy tests: 13 passing (`test_factory_credentials_proxy.py` ×10, `test_credentials_proxy.py` ×3).

**Acceptance criteria**
- [x] AC1 verify-first — recorded above; shipped code matches (explicit-handle path).
- [x] AC2 proxy off / interactive byte-identical — `test_flow_mode_with_proxy_disabled_reads_the_settings_key`,
      `test_interactive_mode_with_proxy_enabled_still_reads_the_settings_key`; all 24 existing factory tests pass unchanged.
- [x] AC3 proxy on + flow → key from Kitaru, settings not read —
      `test_flow_mode_with_proxy_enabled_resolves_gemini_key_from_kitaru` (+ openrouter, + real-stack round-trip).
- [x] AC4 payload/logs carry no raw key — `test_flow_payload_carries_only_the_task_not_the_raw_key`,
      `test_proxy_does_not_log_the_raw_key`.
- [x] AC5 gemini end-to-end through the seam — `test_real_kitaru_secret_round_trips_through_build_agent_in_flow_mode`,
      `test_build_runtime_agent_resolves_the_key_via_the_proxy`. **openrouter** covered
      (`test_flow_mode_with_proxy_enabled_resolves_openrouter_key_from_kitaru`); **modal deferred** —
      its dual proxy-token auth is the later sandbox HTTP-header surface, not a model-key seam (documented in code + README).
- [x] AC6 — N/A: explicit-handle path round-trips, so it ships (no env-injection fallback, no Open Question).
- [x] AC7 `make ci` green, 0 warnings.

**Evidence**
```
$ uv run pytest tests/unit/decode/agent/test_factory_credentials_proxy.py tests/unit/decode/runtime/test_credentials_proxy.py -q
............. [100%]
13 passed in 6.16s

$ make ci
... 998 passed in 42.03s

$ make integration-tests
... 12 passed in 1.60s

$ uv run kitaru secrets set --help
Usage: kitaru secrets set NAME ASSIGNMENTS [ARGS]   # README CLI command confirmed to exist

$ python e2e_proxy.py   # operator e2e on the real local stack, offline
[1] operator created secret 'decode-llm-creds' with GEMINI_API_KEY
[2] flow-mode build resolved key = 'REAL-KITARU-GEMINI-KEY'  (from Kitaru: True)
[3] interactive build key = 'SETTINGS-KEY-unused'  (settings, proxy not consulted: True)
[4] missing secret -> KitaruRuntimeError: Secret `decode-llm-creds` was not found.
```

**Notes**
- `.env.example` already lists `RUNTIME_CREDENTIALS_PROXY_ENABLED` / `RUNTIME_SECRET_NAME` (task 057) — confirmed, no change needed.
- The "flow payload" = the flow's serialized input arguments, persisted as `run.config.parameters` on the
  local stack. For `run_agent_task(task)` that is exactly `{"task": ...}`; the key is resolved inside the
  flow body at model construction and lives only in the in-memory provider client, never serialized.
- No commit yet — handing off to the Tester per the lifecycle.

### [Tester] 2026-06-28 15:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 139 files; `ruff check` all passed)
- Unit tests: 986 passed / 0 failed
- Integration tests: 12 passed / 0 failed (986 + 12 = 998, matching the SWE `make ci` total)
- `uv lock --check`: clean. Warnings: 0
- Hermeticity: `tests/unit/decode/runtime/test_credentials_proxy.py` run ALONE twice under `-W error` → 3 passed both times, deterministic, 0 `PytestUnraisableExceptionWarning` (conftest's 059 disposal pattern — engine dispose + idle-loop close + gc — is autouse and applies). `test_factory_credentials_proxy.py` alone under `-W error` → 10 passed.

**E2E adversarial pass**
- Happy path (real local stack): `create_secret("decode-llm-creds", {"GEMINI_API_KEY": …}) ; build_agent(flow_mode=True)` → model carries the Kitaru key, not the settings sentinel (PASS).
- Break path 1 (security — payload leak, broad scan): ran the real `run_agent_task` flow with the proxy resolving a real raw key inside the flow body, then scanned `run.config` + `run` + every `step` model_dump_json from the persisted ZenML execution → raw key and settings key appear NOWHERE (PASS — the core invariant holds).
- Break path 2 (security — log leak): `caplog` over `decode.agent.factory` at DEBUG → only the secret + key *names* logged, raw key absent (PASS).
- Break path 3 (boundary — empty key value): `create_secret(..., {"GEMINI_API_KEY": ""})` is rejected by kitaru itself (`KitaruUsageError: Secret value ... cannot be empty`); the code's `if not api_key` guard is defense-in-depth behind that (PASS).
- Break path 4 (state — secret exists but lacks provider key): → guidance `RuntimeError` naming `GEMINI_API_KEY`, never a `None` key to the provider, no silent settings fallback (PASS).
- Break path 5 (interactive isolation): importing `decode.agent.factory` and `build_agent()` (default flow_mode) → `kitaru` NOT in `sys.modules` (PASS — REPL path never imports kitaru).
- Break path 6 (FAILURE MODE — enabled-but-missing-secret via the real `decode run` CLI): **FAIL — see blocker below.**

**Acceptance criteria** (numbered ACs all verified passing)
- [x] PASS — AC1 verify-first: installed kitaru 0.18 exposes `get_secret(name)->Secret`, `Secret.get(key, default)`, `create_secret`, `KitaruRuntimeError` (inspected the installed package); the real explicit-handle round-trip is genuine (live `create_secret`/`get_secret`, no mock). Shipped code matches.
- [x] PASS — AC2 default/interactive byte-unchanged: `_provider_api_key` reads `settings.<provider>_api_key.get_secret_value()` on the off path; interactive build does not import kitaru; existing factory tests pass.
- [x] PASS — AC3 enabled+flow resolves from Kitaru: tests assert positive (key == Kitaru sentinel) AND negative (`get_secret.assert_called_once`, key != settings sentinel).
- [x] PASS — AC4 (SECURITY) no raw key in payload/logs: verified on the real store across config + run + step dumps + logs (above). No leak path found.
- [x] PASS — AC5 gemini e2e + openrouter covered + modal deferred: modal deferral reason is sound (dual Modal-Key/Modal-Secret proxy *tokens*, a header surface, not a single api_key — that's the later sandbox-header step).
- [x] PASS — AC6 fallback N/A: the explicit-handle path genuinely round-trips offline on the live local stack, so env-injection is correctly not shipped.
- [x] PASS — AC7 `make ci` green, 0 warnings.

**BLOCKER — User Story #3 ("opt-in and safe by default") violated: missing secret → raw traceback via `decode run`**

The seam correctly raises `KitaruRuntimeError` with no silent fallback (security intact). BUT the user-facing `decode run` surface dumps a ~30-frame Python traceback instead of one friendly line. The up-front `_provider_config_error()` guard (`cli.py:57`) checks only the *settings* key, never the Kitaru secret, and is not proxy-aware. Two configs:

- Scenario A — proxy ON, no settings `GEMINI_API_KEY`, no secret: guard fires → `Decode: set GEMINI_API_KEY …` (friendly exit 1), but the message **misdirects** (with the proxy on the key comes from Kitaru, not settings — the operator should be told to `kitaru secrets set`).
- Scenario B — proxy ON, settings `GEMINI_API_KEY` present (realistic leftover from prior REPL use), no secret: guard PASSES, then `run_agent_task.run(...).wait()` (`cli.py:238`, no try/except) lets `KitaruRuntimeError` propagate as a **raw traceback**:

```
$ RUNTIME_CREDENTIALS_PROXY_ENABLED=true GEMINI_API_KEY=leftover decode run "list the files"
  ...
  File ".../src/decode/runtime/flow.py", line 206, in run_agent_task
  File ".../src/decode/agent/factory.py", line 202, in _resolve_key_via_proxy
    secret = get_secret(secret_name)
  File ".../kitaru/secrets.py", line 163, in _get_secret_response_exact
    raise KitaruRuntimeError(
kitaru.errors.KitaruRuntimeError: Secret `decode-llm-creds-nonexistent` was not found.
exit 1
```

User Story #3 step 2 explicitly requires "the flow surfaces Kitaru's missing-secret error … **not a raw traceback**." Same gap on the HITL path: `_run_hitl` → `run_hitl_agent_task` → `run_agent_task_hitl.run(...)` (the local orchestrator runs inline and re-raises) has no friendly guard either, and a hard-failed execution would otherwise be mis-reported by the `is_finished and is_successful` check as `paused=True`.

Fix (small, central; mirror the existing friendly-guard pattern):
- Preferred: make the pre-flight proxy-aware — when `runtime_credentials_proxy_enabled`, validate the Kitaru secret resolves before launching the flow and emit one stderr line (and skip the settings-key requirement so Scenario A's message stops misdirecting); OR
- wrap `run_agent_task.run(...).wait()` and `run_hitl_agent_task(...)` in `try/except (KitaruRuntimeError, RuntimeError)` → `click.echo(<friendly line naming the secret + `kitaru secrets set`>, err=True); raise click.exceptions.Exit(1)`.
- Add a regression test driving `decode run` with the proxy on + a missing secret, asserting a friendly stderr line + non-zero exit + no traceback.

**File ownership** — diff is `src/decode/agent/factory.py` + `src/decode/runtime/flow.py` + `tests/unit/decode/agent/test_factory_credentials_proxy.py` + `tests/unit/decode/runtime/test_credentials_proxy.py` + `README.md` + the task file. `agent/loop.py` / `tui/` untouched. `.env.example` already lists `RUNTIME_CREDENTIALS_PROXY_ENABLED` / `RUNTIME_SECRET_NAME` (task 057), `settings.py` already has both fields. Clean.

**Other issues found (for PA in /review — not blockers on their own)**
- The pre-flight guard requiring a settings key while the proxy is on is a design smell: it forces the operator to keep a (now-unused) `GEMINI_API_KEY` in `.env` just to pass the guard, which then routes straight into the Scenario-B traceback. The proxy-aware pre-flight fix above resolves both.

**VERDICT: FAIL** — 1 blocker: missing-secret → raw traceback via `decode run` (User Story #3 / the orchestrator's explicit "NOT a raw traceback" criterion). The security core (no raw key in payload or logs) and all 7 numbered ACs are solid; this is a CLI friendly-error / proxy-aware-guard gap.

### [SWE] 2026-06-28 16:20 — Fixes (QA blocker: proxy-aware pre-flight)

Fixed the blocker — a missing/incomplete Kitaru secret now produces one friendly stderr line + non-zero
exit on **both** `decode run` and `decode run --hitl`, never a raw traceback. Closes both Scenario A
(misdirecting message) and Scenario B (raw `KitaruRuntimeError` traceback). No silent fallback to the
settings key; the security core (no raw key in payload/logs) is untouched.

**What changed (proxy-aware pre-flight, the central fix)**
- `src/decode/cli.py`
  - `_uses_credentials_proxy()` — True only when `runtime_credentials_proxy_enabled` AND the provider is
    proxied (`gemini`/`openrouter`); `modal` is never proxied (settings tokens), so it keeps the old guard.
  - `run()` is now proxy-aware: when the proxy is engaged it **skips** the settings-key requirement
    (`_provider_config_error()`) — so a stale `GEMINI_API_KEY` no longer wrongly satisfies/misdirects the
    guard — and instead, **after** the `RUNTIME_ENABLED` guard (the secret lookup boots Kitaru), runs a
    `_proxy_credential_error()` pre-flight that resolves the Kitaru secret *before any flow is built*. A
    missing secret (`KitaruRuntimeError`) or a secret lacking the provider key (`RuntimeError`) → one
    friendly line via `_proxy_secret_message()` naming `kitaru secrets set <name> --GEMINI_API_KEY=…`.
  - `_launch_durable()` — belt-and-braces safety net wrapping both flow launches (`run_agent_task.run().wait()`
    and `run_hitl_agent_task()`); scoped to an engaged proxy + Kitaru's own `KitaruRuntimeError` so an
    unrelated flow error is never masked and proxy-OFF runs are byte-identical (no wrap).
- `src/decode/agent/factory.py` — promoted `_resolve_key_via_proxy` → public `resolve_provider_key_via_proxy`
  and `_PROXY_SECRET_KEY` → `PROXY_SECRET_KEY` (now a second concrete caller: the cli pre-flight). The
  throwaway pre-flight resolution is deliberate — the flow resolves the key **again** inside its body so the
  raw key never rides in the payload (invariant preserved; verified `get_secret` works outside a `@flow` on
  the local stack — KitaruRuntimeError for missing, `.get()`→None for missing key).
- `README.md` — credentials-proxy section now states the settings key is not required/consulted under the
  proxy and the missing-secret outcome is a friendly pre-flight line (not a traceback, not a silent fallback).

**Invariant / behavior held**
- Bare `decode` REPL and proxy-OFF `decode run`: byte-unchanged (the `_provider_config_error()` path is
  untouched; the wrap is a no-op when proxy off). Existing 3 run-command tests + 13 credential-proxy tests
  stay green.
- NO silent fallback to settings when the secret is missing (the seam still raises; the pre-flight surfaces
  it as a friendly line).

**Tests (regression, in `tests/unit/decode/runtime/test_run_command.py`)**
- `test_run_command_proxy_missing_secret_is_a_friendly_line_not_a_traceback` — Scenario B (leftover settings
  key + no secret): exit≠0, friendly line names the secret + `kitaru secrets set`, NOT "set GEMINI_API_KEY",
  `not isinstance(result.exception, RuntimeError)` (no traceback escaped).
- `test_run_command_proxy_no_settings_key_names_the_secret_not_the_settings_var` — Scenario A: names the
  secret, not the settings var.
- `test_run_command_proxy_secret_missing_provider_key_is_friendly` — incomplete secret (no `GEMINI_API_KEY`).
- `test_run_hitl_proxy_missing_secret_is_a_friendly_line_not_a_traceback` — same contract on `decode run --hitl`.
- `test_run_command_proxy_with_a_valid_secret_runs_the_flow` — happy path: valid secret → pre-flight passes,
  flow runs, output prints, exit 0.
- Updated `test_factory_credentials_proxy.py` import + one call site for the rename (still 10 passing).

**Final gate**
- `make format-check` / `make lint-check`: clean (139 files).
- Unit: **991 passed**, 0 failing, **0 warnings** (`filterwarnings=["error"]`).
- Integration: **12 passed**.
- `uv lock --check`: clean (149 packages resolved).

**Evidence**
```
$ uv run pytest tests/unit/decode/runtime/test_run_command.py -q
........                                                                  [100%]
8 passed

$ make unit-tests
991 passed in 45.46s

$ make integration-tests
12 passed in 1.37s

$ uv lock --check
Resolved 149 packages

# E2E (isolated ZenML config), Scenario B reproduced — now a friendly line, exit 1, no traceback:
$ RUNTIME_CREDENTIALS_PROXY_ENABLED=true GEMINI_API_KEY=leftover decode run "list the python files"
Decode: RUNTIME_CREDENTIALS_PROXY_ENABLED is on but the Kitaru secret 'decode-llm-creds' is missing or
has no GEMINI_API_KEY value — create it with `kitaru secrets set decode-llm-creds --GEMINI_API_KEY=…`
(see .env.example).
EXIT=1
```

**Notes**
- No commit — handing back to the Tester for re-review per the lifecycle.

### [Tester] 2026-06-28 17:55 — QA (round 2: re-verify the round-1 blocker fix)

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 139 files; `ruff check` all passed; `make pre-commit` → 991 passed)
- Unit tests: 991 passed / 0 failed
- Integration tests: 12 passed / 0 failed
- `uv lock --check`: clean (149 packages). Warnings: 0
- Hermeticity (each ALONE under `-W error`): `test_credentials_proxy.py` → 3 passed; `test_run_command.py` → 8 passed; `test_factory_credentials_proxy.py` → 10 passed. Deterministic, zero `PytestUnraisableExceptionWarning`.

**E2E adversarial pass (real `decode run` CLI, isolated ZENML_CONFIG_PATH + HOME, offline)**
- Scenario B (`decode run`): proxy ON + leftover `GEMINI_API_KEY=leftover-from-the-repl` + NO secret → ONE friendly stderr line naming `decode-llm-creds` + `kitaru secrets set`, `EXIT=1`, **0 traceback markers** in stderr (PASS — round-1 blocker closed).
- Scenario B (`decode run --hitl`): same inputs → identical friendly line, `EXIT=1`, 0 traceback markers (PASS — HITL path now guarded too).
- Scenario A (`decode run`): proxy ON + NO settings key + NO secret → friendly line names the Kitaru secret; the misdirecting `set GEMINI_API_KEY in your environment` phrase is **ABSENT** (grep count 0), `kitaru secrets set` present, `EXIT=1`, no traceback (PASS — message no longer misdirects).
- Security re-attack (real local stack, real secret `GEMINI_API_KEY=SCAN-RAW-…`, proxy engaged): ran the real `run_agent_task` flow (real `build_agent(flow_mode=True)` resolves the raw key in the flow body; scripted model keeps it offline), then deep-scanned. Raw key absent from: pre-flight logs, `run.config` payload (`parameters == {'task'}`), full run dump, every step dump, run-phase logs. Settings sentinel absent from payload. `OVERALL leaked = False` (PASS — invariant holds after the pre-flight was added; the throwaway pre-flight resolution leaks nothing).
- No silent fallback: with the proxy on + secret missing, neither path ever falls back to the leftover settings key — both exit non-zero before any flow is built (PASS).

**Acceptance criteria** (all 7 numbered ACs remain verified from round 1; the round-2 fix closes User Story #3 without regressing them)
- [x] PASS — AC1 verify-first (installed kitaru 0.18 API + explicit-handle round-trip) — unchanged, still green.
- [x] PASS — AC2 default/interactive byte-identical — `_launch_durable` early-returns unwrapped when proxy off; proxy-off `test_run_command_prints_the_agents_output` green; `loop.py`/`tui/` untouched.
- [x] PASS — AC3 enabled+flow resolves from Kitaru, settings not read — factory tests + real round-trip green.
- [x] PASS — AC4 (SECURITY) no raw key in payload/logs — re-attacked on the real store incl. the new pre-flight path; leaked nowhere.
- [x] PASS — AC5 gemini e2e + openrouter covered + modal deferred (decision carries over).
- [x] PASS — AC6 fallback N/A (explicit-handle path round-trips).
- [x] PASS — AC7 `make ci` equivalent green (pre-commit + integration + lock), 0 warnings.

**Round-1 blocker (User Story #3: missing-secret → raw traceback) — VERIFIED FIXED**
- Fix is the preferred shape from the round-1 report: a proxy-aware pre-flight `_proxy_credential_error()` in `cli.py` that skips the settings-key guard when `_uses_credentials_proxy()` and validates the Kitaru secret resolves *before any flow is built* (`cli.py:141-160, 303-323`), PLUS a belt-and-braces `_launch_durable()` wrapper scoped to an engaged proxy + `KitaruRuntimeError` only (`cli.py:163-183`) so unrelated flow errors are never masked and proxy-OFF runs are unwrapped/byte-identical.
- 5 new regression tests in `test_run_command.py` genuinely assert (exit≠0, friendly line names the secret + `kitaru secrets set`, `not isinstance(result.exception, RuntimeError)`, Scenario-A message-not-misdirecting, happy-path runs the flow). All 8 run-command tests + 13 credential-proxy tests green.
- Public-symbol promotion (`resolve_provider_key_via_proxy`, `PROXY_SECRET_KEY`) wired into the cli pre-flight as the second concrete caller; no stale `_resolve_key_via_proxy`/`_PROXY_SECRET_KEY` references remain.

**Evidence**
```
$ # Scenario B (decode run), isolated env, proxy ON + leftover key + no secret
$ decode run "list the python files"
Decode: RUNTIME_CREDENTIALS_PROXY_ENABLED is on but the Kitaru secret 'decode-llm-creds' is missing
or has no GEMINI_API_KEY value — create it with `kitaru secrets set decode-llm-creds --GEMINI_API_KEY=…`
(see .env.example).
EXIT=1   # traceback markers in stderr: 0

$ # security re-attack (real store, real secret, proxy engaged)
PROBE1 RAW_KEY absent from pre-flight logs (expect True): True
PROBE2 run.config.parameters keys (expect {'task'}): {'task'}
PROBE2 RAW_KEY absent from run.config / full run / steps / run logs: all True
OVERALL: raw key leaked ANYWHERE (expect False): False

$ make pre-commit   →  991 passed
$ make integration-tests  →  12 passed
$ uv lock --check   →  Resolved 149 packages (clean)
```

**File ownership**
- Diff = `README.md` + `src/decode/agent/factory.py` + `src/decode/cli.py` + `src/decode/runtime/flow.py` + `tasks/061-…md` + `tests/unit/decode/runtime/test_run_command.py` (modified) + `tests/unit/decode/agent/test_factory_credentials_proxy.py` + `tests/unit/decode/runtime/test_credentials_proxy.py` (untracked). `agent/loop.py` / `tui/` NOT in the diff. `.env.example` lists `RUNTIME_CREDENTIALS_PROXY_ENABLED` + `RUNTIME_SECRET_NAME` (task 057). Clean.

**Other issues found**
- None blocking. The round-1 design-smell (settings-key guard forcing an unused key while the proxy is on) is resolved by the proxy-aware pre-flight that skips it. For PA in /review: the modal-deferred decision (dual proxy-token header surface, not a single api_key) carries over unchanged and is documented in code + README — confirm it is acceptable to defer.

**VERDICT: PASS** — round-1 blocker closed on both `decode run` and `decode run --hitl` (friendly line, exit non-zero, no traceback, no misdirection); the security invariant still holds after the pre-flight (raw key absent from payload AND all logs, no silent settings fallback); full suite + lint + lock green, 0 warnings.
