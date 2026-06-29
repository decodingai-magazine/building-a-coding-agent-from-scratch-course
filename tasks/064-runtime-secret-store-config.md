---
id: 064-runtime-secret-store-config
feature: kitaru-runtime
status: done
---

# Kitaru secret-store config source: hydrate the whole `.env.example` surface in `decode run`

Tags: `runtime`, `config`, `infra`
Depends on: #058, #061
Blocks: —

Generalizes the task-061 single-key secret lookup into a **generic Kitaru secret-store config
source**: in headless `decode run`, `Settings` can be hydrated from a Kitaru secret holding any of the
`.env.example` variables (`LLM_PROVIDER`, `GEMINI_MODEL`, `OPENROUTER_*`, `MODAL_*`, `OPIK_API_KEY`,
compaction/LSP tuning, …). Because `config/settings.py` already maps every `.env.example` var to a
field, this covers the whole surface with **no per-variable code**.

**This is NOT env-injection and NOT the credential proxy.** Values are read into the **`Settings`
object only — never `os.environ`, never a worker env** — so a model-chosen `bash` command never
inherits them (the rule: Kitaru secret → `Settings`, yes; → process/worker env, no). The real
credential proxy (mitmproxy header injection via `SandboxProxyRule`/`DockerProxy`, for the
sandboxed worker's *tool* credentials) is deferred to the **sandbox milestone** — out of scope here.

## Verify-first (record findings in the SWE log; pre-1.0 surface — ADR-0008 §5)

Before coding, confirm against the INSTALLED SDK + pydantic-settings:
- The pydantic-settings **custom source** API in the pinned version: `PydanticBaseSettingsSource`
  subclassing + the `settings_customise_sources(cls, init_settings, env_settings, dotenv_settings,
  file_secret_settings)` signature and the precedence semantics (earlier in the returned tuple =
  higher priority).
- `get_secret(name).values` returns the full `dict[str, str]` (confirmed in 061) and is callable at
  the hydration point on the local stack.
- An **in-place** rebuild of the global `settings` singleton under pydantic v2 with ZERO warning
  under `filterwarnings=["error"]` (reuse/define a `reload_settings()`-style helper; do NOT switch
  call sites to a getter).
- Confirm the source returns `{}` and does **not import kitaru** when inactive (the REPL-safety
  invariant).

## Scope

- **Setting:** add `runtime_secret_store_config: bool = False` to `config/settings.py` (+ `.env.example`
  mirror). Reuses `runtime_secret_name`. Default off → no secret read, no kitaru import.
- **Custom source:** add `KitaruSecretSettingsSource(PydanticBaseSettingsSource)` in
  `config/settings.py`. Its `__call__` returns `{}` immediately (no kitaru import) unless a
  module-level hydration flag/contextvar is active. When active: lazy-import kitaru, fetch
  `get_secret(runtime_secret_name).values`, and return `{field: value}` for env-var-named keys that
  map to a known field (`KEY.lower()` ∈ `model_fields`; ignore extras, matching `extra="ignore"`).
  Wire it via `settings_customise_sources` at precedence **below `env_settings`, above
  `dotenv_settings`**: `(init, env, kitaru, dotenv, file_secret)` → real env overrides Kitaru;
  Kitaru overrides `.env`/defaults.
- **Headless-only activation:** the source is inert for the interactive REPL — the global `settings`
  singleton built at import has the hydration flag OFF, so bare `decode` never imports kitaru. A
  context manager in `runtime/flow.py` (mirroring `_durable_sleeper`) turns the flag ON, rebuilds the
  `settings` singleton in place (so `build_agent` reads the hydrated config), yields, and on
  **exit/`finally` restores the original `settings` + clears the flag** — load-bearing so a later
  in-process interactive `Settings`/test is unaffected. Wrap both `run_agent_task` and
  `run_agent_task_hitl`'s `run_sync` when `runtime_secret_store_config` is on.
- **No `os.environ` write:** the hydrated values live only in the `Settings` object. A test asserts
  `os.environ` is unchanged (so `LocalExecutor` `bash` never inherits a Kitaru-sourced secret).
- **Relationship to 061 (keep as-is):** the model-key path (`resolve_provider_key_via_proxy`) stays
  unchanged and independent; this source is additive (it can also supply the key via the secret).
  Document precedence when both flags are on; add one "both on" coherence test.
- **Docs:** README headless section — store whole config in `decode-llm-creds`, set
  `RUNTIME_SECRET_STORE_CONFIG=true`, headless-only, env-overrides-Kitaru precedence, values stay in
  `Settings` (not `os.environ`). Amend ADR-0008 §5: distinguish the **secret-store config source**
  (this task) from the deferred **credential proxy** (sandbox step); reserve "Credential Proxy" for
  the header-injection feature.

## Acceptance criteria

- [x] **Verify-first** logged: the custom-source API, `get_secret().values`, the in-place reload, and
      the inactive-source no-import behavior — shipped code matches.
- [x] Default (`runtime_secret_store_config=False`) **or** interactive → byte-identical to today; the
      bare `decode` REPL path does **not** import kitaru (asserted); existing settings/runtime tests
      pass unchanged.
- [x] Headless on: a `decode run` flow hydrates `Settings` from a patched `get_secret` — a test
      asserts `build_agent` sees the injected values (e.g. `GEMINI_MODEL`, `LLM_PROVIDER`, a key)
      that were NOT in the real env.
- [x] **Precedence:** a var present in the real process env is NOT overridden by the Kitaru secret; a
      var present only in the secret IS applied; a var only in `.env` is overridden by the secret —
      unit-tested.
- [x] **No `os.environ` write:** a test asserts `os.environ` is unchanged after hydration (Kitaru
      secrets never reach the env `bash` inherits).
- [x] **Restore on exit:** after the flow returns (and on error) the `settings` singleton is back to
      its original values and the hydration flag is cleared; a test asserts no leak into a subsequent
      in-process `Settings` read.
- [x] **Payload invariant:** the serialized flow payload carries only `{"task"}`; an injected
      sentinel secret value appears in neither the payload nor logs.
- [x] **Both flags on** (secret-store config + 061 credentials proxy) produce a coherent run, no
      raw-key leak — tested.
- [x] `make ci` green, 0 warnings, `uv lock --check` clean.

## User stories

### Story: Centralize all `decode run` config in one Kitaru secret
1. An operator stores provider, model, keys, and tuning in `decode-llm-creds` and sets
   `RUNTIME_SECRET_STORE_CONFIG=true`.
2. `decode run "<task>"` hydrates `Settings` from that secret (anything set in the real env still
   wins), with no per-variable wiring.
3. The values stay in the harness `Settings` object — not `os.environ`, not the flow payload.

### Story: The REPL and defaults are untouched
1. A developer runs bare `decode`, or `decode run` with the flag off (default).
2. No Kitaru secret is read, kitaru is not imported on the REPL path, and config comes from
   `.env`/settings exactly as before.

### Story: A headless run never poisons the process
1. A flow with the flag on hydrates the `settings` singleton, runs, and exits.
2. A subsequent in-process `Settings` read (or the next test) sees the original config — the
   hydration was restored on flow exit.

## Out of scope
- The credential proxy (mitmproxy header injection, `SandboxProxyRule`/`DockerProxy`) — deferred to
  the sandbox milestone, built the canonical way on the Docker sandbox.
- Writing Kitaru-sourced values into `os.environ` or any worker/container env (the anti-pattern).
- Changing the 061 model-key path or the `runtime_secret_name` default.
- Making the source active for the interactive REPL (headless-only by decision).

## Log

### [SWE] 2026-06-29 14:12 — Implementation

**Verify-first findings (pre-1.0 surface; matched against the installed SDK)**
- **Custom-source API (pydantic-settings 2.14.2, pydantic 2.12.5):** `PydanticBaseSettingsSource`
  has **two** abstract methods — `__call__` *and* `get_field_value(field, field_name)` — so the
  source must provide a (here unused) `get_field_value` body even though `__call__` is fully
  overridden. `settings_customise_sources` real signature is
  `(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings)` (note the
  explicit `settings_cls`, not just `cls`). Precedence is left-to-right (earlier = higher); inserting
  the Kitaru source at `(init, env, kitaru, dotenv, file_secret)` gives **env > Kitaru > .env >
  defaults** — verified with a scratch model.
- **`get_secret(name).values`:** `kitaru.secrets.Secret` is a frozen pydantic model with a
  `values: dict[str, str]` field (and a `.get()` helper, which 061 used). So `get_secret(name).values`
  is the full dict — confirmed against the source.
- **In-place singleton rebuild:** `settings.__dict__.update(fresh.__dict__)` +
  `__pydantic_fields_set__` sync emits **zero warnings under `-W error`** on pydantic v2, and a name
  bound via `from … import settings` (the pattern factory/flow use) sees the update because the object
  identity is preserved. Re-assigning the module attribute would *not* update those bound names, so
  in-place is required (not optional).
- **Inactive source / REPL-safety:** with the flag off, `KitaruSecretSettingsSource.__call__` returns
  `{}` and never executes the lazy `from kitaru import get_secret`. A clean-subprocess test imports
  `decode.cli` + builds `Settings()` and asserts no `kitaru*` module is in `sys.modules`.

**The restore mechanism (load-bearing)**
- `config/settings.py`: a module-level `_secret_hydration_active` flag (+ `set_secret_hydration_active`
  / `is_secret_hydration_active`) the source consults; `reload_settings()` rebuilds the singleton **in
  place** from its sources.
- `runtime/flow.py::_config_from_secret_store()` (mirrors `_durable_sleeper`): when
  `settings.runtime_secret_store_config` is on it **snapshots** `settings.__dict__` +
  `__pydantic_fields_set__`, sets the flag, `reload_settings()` (which now pulls the Kitaru secret
  through the active source), yields, and in `finally` clears the flag and restores the exact snapshot
  — so the restore is byte-identical even on error. When the setting is off it yields immediately,
  imports no kitaru, touches no settings (pure no-op → bypass/HITL byte-unchanged). Both
  `run_agent_task` and `run_agent_task_hitl` wrap their `build_agent` + `run_sync` span in it; the HITL
  flow nests `_durable_sleeper` inside it so the two seams compose independently.

**Files modified**
- `src/decode/config/settings.py` — `runtime_secret_store_config` field; `KitaruSecretSettingsSource`
  (lazy kitaru import, inert unless active, env-var-key→field mapping ignoring extras, logs field
  NAMES only); `settings_customise_sources` wiring (env > Kitaru > .env); hydration flag + helpers;
  `reload_settings()`.
- `src/decode/runtime/flow.py` — `_config_from_secret_store()` context manager; wrapped both flow
  bodies.
- `.env.example` — `RUNTIME_SECRET_STORE_CONFIG=false` mirror (+ note it reuses `RUNTIME_SECRET_NAME`).
- `README.md` — new "Secret-store config source" subsection under the headless runtime (whole-surface
  hydration, `env > Kitaru > .env`, values stay in `Settings` not `os.environ`, headless-only,
  distinct from the deferred sandbox credential proxy, composes with 061).
- `tests/unit/decode/config/test_settings.py` — extended the runtime-var default/env/.env/drift tests
  for the new setting; added source-mechanism, precedence (env-wins, secret-over-dotenv), in-place
  `reload_settings`, and clean-subprocess no-kitaru-import tests (fake `kitaru` injected for speed).
- `tests/unit/decode/runtime/test_secret_store_config.py` — new file: real local-stack round-trips
  (hydration seen by `build_agent`, env-wins precedence, no `os.environ` write, restore on
  success/error + no leak into a later `Settings`, no-op when off, log carries names-not-values,
  payload carries only `{"task"}`, both-flags-on coherence).

**Tests**
- Unit: 1006 passing, 0 failing (`make pre-commit`). New: 11 in `test_settings.py` additions +
  9 in `test_secret_store_config.py`.
- Integration: 19 passing (`make integration-tests`).
- `make ci` green (includes `uv lock --check`, format-check, lint-check): 1025 passed, 0 warnings.
- Hermeticity: ran `tests/unit/decode/runtime/test_secret_store_config.py` ALONE under `-W error`
  **twice** → 9 passed each, zero `PytestUnraisableExceptionWarning`; ran it alongside
  `config/` + `runtime/` + `test_factory_credentials_proxy.py` (91 passed) to confirm no settings /
  `os.environ` leak into other tests (autouse snapshot/restore fixture + the context manager's own
  `finally`).

**Evidence**
```
$ make ci
... 1025 passed in 70.48s (0:01:10)

$ # faithful offline e2e: real Kitaru @flow + adapter + hydration, scripted model boundary
$ uv run python e2e.py   # secret holds LLM_PROVIDER/GEMINI_MODEL/GEMINI_API_KEY; none in env
FLOW OUTPUT: done: ran headlessly
build_agent saw provider : gemini
build_agent saw model    : gemini-from-the-kitaru-secret
build_agent saw a key    : True
os.environ unchanged     : True
GEMINI_API_KEY in env    : False
settings restored after  : gemini-2.5-flash
```

**Notes**
- **ADR-0008 §5 amendment NOT made by me.** The Docs scope asked to amend ADR-0008 §5, but mid-task
  the orchestrator instructed that the ADR amendments are being authored directly elsewhere — so I
  made **no** edits to `docs/adr/0008-kitaru-durable-runtime.md`. Everything else in the Docs scope
  (README + `.env.example`) shipped.
- **Terminology vs the glossary (for PA):** `docs/glossary.md` currently binds **"Credentials Proxy"**
  to the task-061 *model-key* path. The task framing wanted to "reserve 'Credential Proxy' for the
  header-injection feature." I did **not** touch the glossary (read-only for SWE) and did **not** assert
  a contradicting redefinition in code/docs — I named this feature the **"secret-store config source"**
  and the deferred sandbox feature the **"credential proxy (mitmproxy header injection)"** throughout.
  If the PA wants to formally re-scope the glossary term, that is a glossary edit (PA territory).
- **CLI guard interaction (documented, not changed):** `decode run`'s startup presence guard
  (`_provider_config_error`, the 061-era guard) runs **before** the flow hydrates, so a provider key
  that lives *only* in the Kitaru secret (credentials proxy OFF) would still trip the no-key line. The
  common case — key already in `.env` (REPL users have it) — passes and the secret then overrides
  model/tuning inside the flow; the key-only-in-secret case is exactly what the 061 credentials proxy
  is for (its proxy-aware pre-flight validates from the secret). The README states this. I did not
  touch the cli guard: it is outside this task's scope ("Out of scope: changing the 061 model-key
  path") and gating it differently is an architectural choice the spec did not make.
- **Complex-typed fields:** the source maps raw secret strings into fields; pydantic coerces scalars /
  `SecretStr` / `Literal` from strings, but a complex field like `lsp_server_args` (`list[str]`) stored
  in the secret would need a JSON string just as it does via env — an edge well outside the realistic
  provider/model/key/tuning surface, left as-is.

### [Tester] 2026-06-29 15:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 141 files clean; `ruff check` all passed)
- Unit tests: 1006 passed / 0 failed
- Integration tests: 19 passed / 0 failed
- `uv lock --check`: clean (exit 0)
- Warnings: 0 (whole suite runs under `filterwarnings=["error"]`; every targeted run repeated with `-W error`)

**E2E adversarial pass** (the secret-store source is config-surface code, so I drove the real
`_config_from_secret_store` context manager + `reload_settings` with a fake `kitaru` module, plus a
real `decode run` subprocess for the guard path)
- Happy path: valid secret `{GEMINI_MODEL, GEMINI_API_KEY}` → inside ctx `model='m-from-secret'`,
  on exit restored to default `gemini-2.5-flash`, `os.environ` unchanged, sentinel value NOT in env. PASS
- Break path 1 (boundary: empty secret `{}`): no hydration, ctx exits clean, settings unchanged. PASS
- Break path 2 (malformed: `LLM_PROVIDER=totally-bogus`): pydantic `ValidationError` (literal_error)
  raised — but settings/flag/`os.environ` fully restored by the `finally`. PASS for restore-safety;
  see Other issues for the deep-traceback UX note.
- Break path 3 (malformed: `RUNTIME_WAIT_TIMEOUT_S=not-a-number`): `ValidationError` (float_parse),
  restore held. PASS (restore). Break path 3b (`LSP_SERVER_ARGS=server`, complex list field): `ValidationError`
  (list_type), restore held. PASS (restore).
- Break path 4 (failure mode: secret missing — `get_secret` raises `RuntimeError`): exception propagates,
  settings + flag + `os.environ` restored. PASS (restore).
- Break path 5 (mixed-case key `Gemini_Model`): `.lower()` maps it to `gemini_model` → `mixed-case-model`. PASS
- Break path 6 (security: `os.environ` write): byte-unchanged across ALL probes incl. valid hydration;
  no Kitaru value reachable to a `LocalExecutor` `bash`. PASS
- Break path 7 (REPL safety, clean subprocess): `import decode.cli` + `Settings()` → zero `kitaru*`
  modules in `sys.modules`. PASS

**Acceptance criteria** (all verified independently; all `[x]` are accurate)
- [x] PASS — Verify-first matches the installed SDK — confirmed: pydantic 2.12.5 / pydantic-settings
      2.14.2; `PydanticBaseSettingsSource.__abstractmethods__ == {__call__, get_field_value}`;
      `settings_customise_sources(settings_cls, init, env, dotenv, file_secret)`; `get_secret().values`
      exercised by the real-stack tests.
- [x] PASS — Default off / interactive byte-identical, REPL imports no kitaru — my clean subprocess
      shows `leaked == []`, default `runtime_secret_store_config=False`; full settings/runtime suites green.
- [x] PASS — Headless on hydrates `Settings`, `build_agent` sees secret-only values —
      `tests/unit/decode/runtime/test_secret_store_config.py::test_headless_flow_hydrates_settings_seen_by_build_agent`
      + my probe (`model='m-from-secret'` with the var cleared from env).
- [x] PASS — Precedence env > secret > .env, all three directions —
      `test_settings.py::test_real_env_overrides_kitaru_secret` / `test_kitaru_secret_overrides_dotenv` /
      `test_secret_store_source_hydrates_known_fields_when_active`; real-stack `test_real_env_overrides_kitaru_secret_in_flow`.
- [x] PASS — No `os.environ` write — `test_hydration_never_writes_os_environ` + my probes (unchanged in all 7 break paths).
- [x] PASS — Restore on exit AND on error, no leak — `test_context_restores_settings_on_success/on_error`;
      my probes confirm restore even when `reload_settings` itself raises (ValidationError / RuntimeError);
      contamination run (hydrating file FIRST, then `test_settings.py` + `test_factory_credentials_proxy.py`,
      forced order, `-W error`) → 60 passed.
- [x] PASS — Payload invariant only `{task}`, sentinel absent from payload + logs —
      `test_flow_payload_carries_only_the_task_not_the_secret_value` + `test_hydration_logs_field_names_not_secret_values`
      (source logs `sorted(hydrated)` = field NAMES only).
- [x] PASS — Both flags on coherent, no raw-key leak —
      `test_both_flags_on_produce_a_coherent_run_with_no_raw_key_leak` (model from secret-store source,
      key via the 061 proxy, raw key absent from the serialized payload).
- [x] PASS — `make ci` green, 0 warnings, lock clean — reproduced (format+lint+1006 unit+19 integration; lock exit 0).

**Adjudication — SWE-flagged startup-guard gap: ACCEPTABLE-WITH-DOC for this slice (not a blocking defect); recommend a follow-up for 061-consistency.**
Reproduced in a real subprocess: `LLM_PROVIDER=gemini RUNTIME_SECRET_STORE_CONFIG=true`,
proxy off, no `GEMINI_API_KEY` in env/.env, from a clean cwd:
`decode run "list files"` → `Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).`,
exit **1**, **no traceback**, kitaru never imported (it exits at `_provider_config_error()` before the flow hydrates).
- Why not a blocking FAIL: (a) no AC requires the guard to be `runtime_secret_store_config`-aware — every
  064 AC is about the flow/settings mechanism, which works; (b) the outcome is friendly + non-zero + no
  traceback (not a crash/corruption/hang); (c) a coherent supported path for key-in-secret already exists
  and is an AC ("both flags on" — enable the 061 proxy, whose pre-flight validates the key straight from the
  secret), and the README documents exactly this workaround; (d) `cli.py` is outside the declared 064 diff.
- Why I still flag it: it IS the same misleading-guard class 061 round-2 repaired. The message names
  `GEMINI_API_KEY`/`.env` with no mention of the secret store or the flag, so an operator following the 064
  headline story ("store keys + config in one secret, set the flag", User Story 1 names *keys*) hits a
  misleading line for the key half. **Recommended fix if PA rules defect:** mirror the 061 pre-flight —
  in cli `run`, when `settings.runtime_secret_store_config` is on, skip/defer `_provider_config_error()`
  for the proxied providers and run a secret-store pre-flight that resolves config from the secret before
  the flow (after the `RUNTIME_ENABLED` guard, since it boots kitaru). PA decides in /review.

**Doc + ownership checks**
- File ownership matches the brief exactly: diff = `.env.example`, `README.md`, `docs/adr/0008`,
  `docs/glossary.md`, `config/settings.py`, `runtime/flow.py`, `tests/.../config/test_settings.py`;
  untracked `tasks/064` + `tests/.../runtime/test_secret_store_config.py`. `agent/loop.py` and `tui/` NOT touched. ✓
- ADR-0008 §5 amendment + "Future work" + glossary rows (orchestrator-authored): coherent with shipped
  code — precedence, `Settings`-not-`os.environ` rule, the secret-store-config vs deferred-Credential-Proxy
  split, both settings names, the `get_secret().values` mechanism all line up. Minor staleness (PA, non-blocking):
  the ADR **Consequences** bullet still lists the old term "Credentials Proxy" among the six glossary terms
  (the glossary now splits it into "Secret-Store Config (Kitaru)" + "Credential Proxy"); and the ADR §5 point-1
  says 061's label is "renamed model-key secret resolution" while the code identifiers
  (`runtime_credentials_proxy_enabled`, `_uses_credentials_proxy`, docstrings) still say "credentials proxy" —
  renaming them is explicitly out of 064 scope, so this is a documented conceptual-vs-identifier divergence, not a defect.

**Evidence**
```
$ make unit-tests   → 1006 passed in 57.60s
$ make integration-tests → 19 passed in 17.87s
$ uv lock --check   → Resolved 149 packages; exit 0
$ pytest tests/unit/decode/runtime/test_secret_store_config.py -W error  (x2) → 9 passed each
$ pytest tests/unit/decode/config/test_settings.py -W error  (x2) → 41 passed each
$ pytest (secret_store FIRST, then test_settings + test_factory_credentials_proxy) -W error → 60 passed
$ decode run "list files"  (flag on, proxy off, no key)  → friendly line, exit 1, no traceback
```

**Other issues found** (non-blocking; for PA / possible follow-up)
- **Malformed/missing secret value surfaces as a deep traceback inside the flow.** A typo'd secret value
  (e.g. `LLM_PROVIDER=Gemini`, a non-numeric float, a list field as a bare string) or a missing secret raises
  `ValidationError`/`RuntimeError` from inside `reload_settings` → the flow body in a real `decode run`, since
  cli `run` has no friendly handler around the flow call. State is always restored cleanly (no corruption,
  `os.environ` untouched), and this matches the existing project behavior that a malformed `.env` value also
  raises at construction — so it is consistent, not a regression, and no AC covers it. The same secret-store
  pre-flight proposed in the adjudication would also convert this into a friendly line.
- **Hydration flag is a plain module global (by SWE design), not re-entrant.** Fine for the single-process,
  single-flow headless slice (each flow wraps the context once); worth a note if a future surface nests flows.

**VERDICT: PASS**

### [SWE] 2026-06-29 17:05 — Follow-up fix (cli `run` secret-store awareness)

Addresses the Tester-flagged gap (adjudication + "Other issues"): the `decode run` provider-config
guard ran BEFORE the flow hydrated `Settings` from the secret store, so with
`runtime_secret_store_config=true` (1) a key living ONLY in the Kitaru secret (proxy OFF) tripped the
misleading `set GEMINI_API_KEY` line + exit 1, and (2) a missing/malformed secret surfaced as a deep
traceback from inside the flow. Same class as 061's round-2 proxy-aware fix; this mirrors it.

**The fix.** A new `cli._secret_store_config_error()` (the secret-store counterpart to the 061
`_proxy_credential_error`): when `runtime_secret_store_config` is on, BEFORE building the flow it
reuses the flow's own `_config_from_secret_store()` context to hydrate `Settings` from the Kitaru
secret up front, runs `_provider_config_error()` against the *hydrated* config (so a secret-only key
satisfies the guard — no false `set GEMINI_API_KEY`), and converts a missing/malformed secret
(`KitaruRuntimeError` ⊂ `RuntimeError`, or a pydantic `ValidationError` from a bad stored value) into
ONE friendly stderr line naming the real fix (`kitaru secrets set <name> …`) + `Exit(1)` — never a
traceback. The context restores the singleton on exit; the flow re-hydrates idempotently in its own
body (sequential, not nested — no re-entrancy).

**Ordering / coherence.** In `run()`: the settings-key guard is now skipped up front when EITHER
kitaru-backed source is on (proxy or secret-store); after the `RUNTIME_ENABLED` guard (both boot
kitaru) the secret-store pre-flight runs FIRST, then the 061 proxy pre-flight — so with both flags on
the proxy resolves its key from the now-hydrated secret config and only one line is ever emitted
(documented in code comments). Secret-store OFF + proxy OFF is byte-unchanged (the original guard
order), and no kitaru import happens on that path or the bare-`decode` REPL path (the lazy
`from decode.runtime.flow import _config_from_secret_store` lives inside the new helper, reached only
when the flag is on).

**Files modified**
- `src/decode/cli.py` — added `_secret_store_config_error()`; made `run()`'s guard block
  secret-store-aware (skip the settings-key guard when the secret-store source is on; new pre-flight
  before the proxy pre-flight); updated the `run` docstring; imported `pydantic.ValidationError`.
- `tests/unit/decode/runtime/test_run_command.py` — `_secret_store_on` fixture + 4 tests: secret-only
  key satisfies the guard (run proceeds, scripted seam); missing secret → friendly line/no flow/no
  traceback on both `decode run` and `decode run --hitl`; malformed secret value → friendly line/no
  traceback.
- `README.md` — replaced the now-obsolete "key-in-secret needs the proxy flag too" caveat in the
  secret-store subsection with the pre-flight behavior (secret-only key now satisfies the guard;
  missing/malformed → friendly line).

**Tests**
- Unit: 1010 passing, 0 failing (`make pre-commit`; +4 over the 1006 baseline).
- Integration: 19 passing (`make integration-tests`).
- Lint/format: `ruff format --check` 141 files clean; `ruff check` all passed. `uv lock --check` clean.
- Hermeticity: ran the new file ALONE under `-W error` twice (12 passed each); ran it alongside
  `test_secret_store_config.py` + `config/test_settings.py` + `test_cli.py` under `-W error`
  (106 passed) — no settings/`os.environ` leak.

**Acceptance criteria** — all 064 ACs were already `[x]` (PASS-verified); this follow-up adds no new
checkbox. It closes the Tester's recommended follow-up (cli guard `runtime_secret_store_config`-aware).

**Evidence**
```
$ make pre-commit
... 1010 passed in 56.44s
$ make integration-tests
... 19 passed in 16.90s
$ uv lock --check
Resolved 149 packages

$ # real `decode run`, isolated Kitaru store, flag on, NO secret:
$ RUNTIME_SECRET_STORE_CONFIG=true RUNTIME_ENABLED=true decode run "list the python files"
Decode: RUNTIME_SECRET_STORE_CONFIG is on but the Kitaru secret 'decode-llm-creds' could not be
loaded (it is missing, or a stored value is invalid) — create or repair it with `kitaru secrets set
decode-llm-creds --LLM_PROVIDER=… --GEMINI_API_KEY=…` (see .env.example).
EXIT CODE: 1                       # friendly line, non-zero, no traceback

$ # same store, secret holds a bogus LLM_PROVIDER (fails a pydantic field):
$ RUNTIME_SECRET_STORE_CONFIG=true RUNTIME_ENABLED=true decode run "list files"
Decode: RUNTIME_SECRET_STORE_CONFIG is on but the Kitaru secret 'decode-llm-creds' could not be
loaded ...
EXIT CODE: 1                       # ValidationError → same friendly line, no traceback
```

**Notes**
- `docs/adr/0008-kitaru-durable-runtime.md` and `docs/glossary.md` NOT touched (orchestrator-owned).
- DO NOT commit — handing back to the Tester for re-review.

### [Tester] 2026-06-29 18:45 — QA (round 2 — startup-guard follow-up)

Focused re-review of the round-2 `cli._secret_store_config_error()` fix; full regression sweep.

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 141 files clean; `ruff check` all passed)
- Unit tests: 1010 passed / 0 failed (`make unit-tests`)
- Integration tests: 19 passed / 0 failed (`make integration-tests`)
- `uv lock --check`: clean (Resolved 149 packages)
- Warnings: 0 (suite under `filterwarnings=["error"]`; targeted runs repeated with `-W error`)

**E2E adversarial pass** (independent harness: real `decode run` via `CliRunner`, real
`_secret_store_config_error` + `_config_from_secret_store` + `KitaruSecretSettingsSource` + real
Kitaru `get_secret`/`create_secret` on an isolated offline local store; only the model seam scripted)
- Happy path (S1, the guard-gap fix): secret-store ON, key ONLY in the Kitaru secret, proxy OFF →
  `decode run` proceeds (scripted seam prints "the secret-store answer"), exit 0, and the misleading
  `set GEMINI_API_KEY` line is GONE. PASS — closes PA-flagged item #1.
- Break path 1 (failure mode: missing secret) — `run`: ONE friendly line naming `decode-llm-creds`
  + `kitaru secrets set` + `RUNTIME_SECRET_STORE_CONFIG`, exit 1, no flow built, `exc_type=SystemExit`
  (no RuntimeError/ValidationError escaped). PASS.
- Break path 2 (failure mode: missing secret) — `--hitl`: same friendly line, exit 1, no flow,
  SystemExit only. PASS.
- Break path 3 (malformed: bogus `LLM_PROVIDER=totally-bogus`, a pydantic `Literal` failure): same
  friendly line, exit 1, SystemExit only — the round-1 deep-traceback symptom is GONE. PASS — closes
  PA-flagged item #2.
- Break path 4 (both flags on: secret-store + 061 proxy, missing secret): EXACTLY ONE non-blank
  stderr line (the secret-store line); the proxy line does NOT also appear (`lines=1`,
  `secret_store_line=True`, `proxy_line=False`). PASS — no two conflicting lines.
- Break path 5 (malformed numeric field `RUNTIME_WAIT_TIMEOUT_S=not-a-number`): friendly line, exit 1,
  SystemExit only. PASS.
- Break path 6 (boundary: secret OMITS the key, sets only provider/model): hydration succeeds but
  the hydrated config has no key → the normal `set GEMINI_API_KEY` friendly line fires (not a
  traceback, not a silent run). PASS — correct fall-through to `_provider_config_error()`.
- Break path 7 (security/state: restore-on-exit + no `os.environ` write): after a successful pre-flight
  the hydration flag is cleared (`is_secret_hydration_active() is False`) and a hydrated `GEMINI_MODEL`
  did NOT leak into `os.environ` (env byte-identical). PASS.
- Note: a secret storing an empty value (e.g. `GEMINI_API_KEY=""`) is unreachable — Kitaru's
  `create_secret` itself rejects empty values (`KitaruUsageError`), so that probe cannot be set up.

**Exception-handling structural check** (why "no traceback" holds): `get_secret` raises
`KitaruUsageError` (⊂ `ValueError`, only on an empty NAME — never with the non-empty default),
`KitaruRuntimeError` (⊂ `RuntimeError`, not-found / unreadable), or `KitaruBackendError`
(⊂ `KitaruRuntimeError` ⊂ `RuntimeError`); malformed stored values raise pydantic `ValidationError`.
`except (RuntimeError, ValidationError)` therefore catches every realistic failure mode — verified
both empirically (S2-S5) and against the installed SDK's class hierarchy.

**Regression / invariants**
- Default-off + interactive byte-identical: bare `decode` and `decode run` with no key both emit the
  verbatim `Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).`,
  exit 1, no traceback — verified in a clean subprocess. PASS.
- REPL-safety: clean subprocess `import decode.cli` + `Settings()` → ZERO `kitaru*` modules in
  `sys.modules`; the lazy `from decode.runtime.flow import _config_from_secret_store` is reached only
  when the flag is on (the default-off guard paths import no kitaru either). PASS.
- 064 invariants intact (re-confirmed via the round-1 suite + S7): no `os.environ` write; restore on
  success and on error; payload `{"task"}` only; precedence env > Kitaru > .env.
- All prior 064 + 061 + `test_run_command.py` tests green: `test_run_command.py` now 12 (8 + 4 new
  secret-store), `test_secret_store_config.py` 9, `config/test_settings.py` 41.

**Hermeticity** (brief's exact check): the three touched files
(`test_run_command.py` + `test_secret_store_config.py` + `config/test_settings.py`) run ALONE under
`-W error` twice → 62 passed each, deterministic, zero `PytestUnraisableExceptionWarning`, no
settings/`os.environ` leak. `test_run_command.py` alone twice → 12 passed each.

**File ownership**
- Round-2 diff is surgical: `src/decode/cli.py` (new `_secret_store_config_error()` + guard reorder),
  `tests/unit/decode/runtime/test_run_command.py` (`_secret_store_on` fixture + 4 tests), `README.md`
  (pre-flight behavior replaces the obsolete caveat). The wider uncommitted diff (`config/settings.py`,
  `runtime/flow.py`, `config/test_settings.py`, `.env.example`, docs) is the still-uncommitted round-1
  work, expected.
- `docs/adr/0008-kitaru-durable-runtime.md` and `docs/glossary.md` still carry the orchestrator's
  amendments (the 2026-06-29 task-064 §5 amendment + "Future work — the Credential Proxy" section;
  the glossary split into "Secret-Store Config (Kitaru)" + reserved "Credential Proxy") — present and
  coherent. (Cannot byte-diff round-1 vs round-2 without a commit, but content is intact and the SWE
  log states they were untouched.) `agent/loop.py` and `tui/` are NOT in the diff. No stray scratch
  files (round-1 `e2e.py` cleaned up).

**Evidence**
```
$ make unit-tests          → 1010 passed in 56.28s
$ make integration-tests   → 19 passed in 17.03s
$ uv lock --check          → Resolved 149 packages (clean)
$ pytest <3 touched files> -W error  (x2) → 62 passed each (deterministic)
$ pytest test_run_command.py -W error (x2) → 12 passed each
$ python repro064.py       → 8/8 e2e scenarios PASS (S1 guard-gap, S2-S5 no-traceback, S6 fall-through, S7 restore)
$ # clean subprocess: import decode.cli + Settings() → kitaru modules == []
```

**Other issues found** (non-blocking; NOT triggered by the real suite — for PA / a possible follow-up)
- **Latent cross-test Kitaru-store isolation gap (pre-existing infra, not the round-2 change).** When
  a NON-isolated test that touches/initializes ZenML (here `tests/unit/decode/test_cli.py`) is forced
  to run BETWEEN a secret-creating runtime test and another runtime test, the runtime conftest's
  `isolated_kitaru_store` isolation breaks down: a later test's `create_secret`/`get_secret` resolves
  against the developer's REAL ZenML store, which both (a) pollutes real user state with a
  `decode-llm-creds` secret and (b) makes `test_run_command.py`'s secret-store/proxy tests fail (a
  leaked secret defeats their missing-secret assumption). Reproduced with a forced cross-package order
  (`pytest test_secret_store_config.py test_cli.py test_run_command.py -p no:randomly` → 6 failed +
  real store polluted). It affects a PRE-EXISTING 061 proxy test
  (`test_run_command_proxy_with_a_valid_secret_runs_the_flow`) as well as the new 064 tests, so the
  mechanism predates this task. **It does NOT affect the as-run suite:** pytest-randomly is not
  installed and `addopts` sets no random order, so `make unit-tests` runs in a deterministic order
  (runtime/ before `test_cli.py`) — verified 1010 green AND the real store clean afterward (I deleted
  the secret my diagnostics left and re-ran). Recommended follow-up (test-infra, not product): in the
  runtime conftest delete the secret in teardown / use a per-test unique secret name, or harden the
  isolation so it survives an interleaved non-isolated test — so the suite is order-independent and can
  never write to a developer's real ZenML store. PA decides whether to spin a task; the round-2 cli fix
  itself is correct and complete.

**Resolution of the two earlier PA-flagged items**
- Guard gap (secret-only key tripped `set GEMINI_API_KEY`): RESOLVED — S1 proceeds cleanly, message gone.
- Missing/malformed-secret deep traceback: RESOLVED — S2/S3/S5 all emit one friendly line, SystemExit
  only, no traceback, on both `run` and `--hitl`.

**VERDICT: PASS**
