---
id: 096
feature: env-bucket-secrets
status: done
---

# Delete model-key secret resolution (the provider key now only ever comes from Settings)

Depends on: none. Implements ADR-0015 §4 (first half of the clean break).

## Scope

Delete the `RUNTIME_SECRET_STORE_MODEL_KEY` mechanism end to end. After this task the provider API key
comes from `settings.gemini_api_key` / `settings.openrouter_api_key`, period — hydrated by whichever
settings source is active. No fallback, no shim, no deprecation warning: the break is deliberate and
silent in code (ADR-0015 §4); the docs get loud in 097/102.

**`src/decode/config/settings.py`**

- Delete the `runtime_secret_store_model_key` field and its comment block. **Keep** `runtime_secret_name`
  and `runtime_secret_store_config` — they die in 097, and the config-source path must stay green through
  this task.

**`src/decode/agent/factory.py`**

- Delete `SECRET_STORE_KEY`, `resolve_provider_key_from_secret_store()`, and the `flow_mode`-gated branch
  inside `_provider_api_key()`. `_provider_api_key(provider)` loses its `flow_mode` parameter (or is
  inlined — SWE's call): it is now a plain `settings.<provider>_api_key.get_secret_value()`.
- **`build_agent(flow_mode=…)` / `_build_model(flow_mode=…)` KEEP the parameter.** Every caller was
  checked: `flow_mode` still selects the keep-alive-free httpx client (`_flow_mode_http_client`,
  ADR-0010 §3), which is unrelated to key resolution. Only the key special-case goes. Update both
  docstrings (they currently name ADR-0008 §5 as the seam).

**`src/decode/cli.py`**

- Delete `_uses_secret_store_model_key()` and `_model_key_secret_error()`.
- `_runtime_config_preflight()`: drop the model-key pre-flight step; the first step's skip condition
  simplifies to `if not settings.runtime_secret_store_config:`.
- `_secret_store_config_error()`: remove the `_uses_secret_store_model_key()` branch — with the secret
  hydrated it now always runs `_provider_config_error()` against the hydrated config.
- Trim the `run()` command docstring paragraph describing the two kitaru-backed sources down to the one
  that still exists (`RUNTIME_SECRET_STORE_CONFIG`).

**Docs (scrub, not rewrite — 102 does the cohesive pass)**

- `.env.example`: delete the `RUNTIME_SECRET_STORE_MODEL_KEY` line; reword the "two secret-store lookups"
  comment to the one remaining knob.
- `README.md` "Keeping keys out of the flow payload": delete the `RUNTIME_SECRET_STORE_MODEL_KEY` bullet.
- `CREDENTIALS.md`: delete Part 1 (1a–1d), the model-key column of the intro table, the model-key half of
  the Part 3 composed scenario, and the Part 5 references to the deleted test files. Do not restructure
  further.
- `docs/glossary.md`: no action — the plan commit already replaced the Secret-Store Config entry with
  **Environment Bucket** / **DECODE_ENV** (verify only).

**Tests**

- Delete `tests/unit/decode/runtime/test_secret_store_model_key.py` and
  `tests/unit/decode/agent/test_factory_secret_store_model_key.py`.
- `tests/integration/test_runtime_capstone.py`: delete the model-key slice and its module-docstring mention.
- `tests/unit/decode/config/test_settings.py`: drop the field from the runtime defaults/env tests.
- `tests/unit/decode/runtime/test_run_command.py` / `test_replay_command.py` / `test_secret_store_config.py`:
  update pre-flight-order and interplay tests that reference the deleted flag/pre-flight.
- **TDD-first regression test:** `build_agent(flow_mode=True)` with a settings key builds a model whose key
  came from settings — i.e. flow mode no longer changes key sourcing.

## Acceptance Criteria

- [x] `grep -rn "runtime_secret_store_model_key\|RUNTIME_SECRET_STORE_MODEL_KEY\|SECRET_STORE_KEY\|resolve_provider_key_from_secret_store\|_uses_secret_store_model_key\|_model_key_secret_error" src/ tests/ .env.example README.md CREDENTIALS.md docs/glossary.md` returns nothing (`docs/adr/` history exempt). — clean across `src/` + all four docs; the ONLY surviving hits are in `tests/unit/decode/config/test_settings.py`, in the test that *asserts the knob is dead* (it must name the env var to set it — see Notes).
- [x] `build_agent(flow_mode=True, …)` still hands Gemini the keep-alive-free httpx client (the existing flow-mode client test stays green) while the provider key is read from `Settings` in both flow and interactive mode. — `tests/unit/decode/agent/test_factory.py::test_gemini_flow_mode_wires_the_loop_safe_http_client` (untouched, green) + the new `::test_flow_mode_reads_the_gemini_key_from_settings` / `::test_flow_mode_reads_the_openrouter_key_from_settings`.
- [x] `decode run` with `RUNTIME_SECRET_STORE_CONFIG=true` and a valid Kitaru secret still passes its pre-flight (the surviving mechanism is untouched) — the `test_secret_store_config.py` suite is green after interplay updates.
- [x] An env still carrying `RUNTIME_SECRET_STORE_MODEL_KEY=true` changes nothing and prints nothing (`extra="ignore"` — the silent clean break). — `test_settings.py::test_stale_model_key_secret_env_var_is_silently_ignored` + verified live (Evidence A/B).
- [x] `make ci` green.

## Out of scope

- Deleting `runtime_secret_name` / `runtime_secret_store_config` (task 097).
- The cohesive `CREDENTIALS.md` rewrite (task 102).
- Any `DECODE_ENV` work.

## Log

### [SWE] 2026-07-13 — Implementation

**Files modified**
- `src/decode/config/settings.py` — deleted the `runtime_secret_store_model_key` field + its comment block; the surviving `runtime_secret_name` / `runtime_secret_store_config` comment now describes ONE secret-store lookup (they die in 097).
- `src/decode/agent/factory.py` — deleted `SECRET_STORE_KEY` and `resolve_provider_key_from_secret_store()`; `_provider_api_key(provider)` lost its `flow_mode` param and is now a plain `Settings` read. `build_agent(flow_mode=…)` / `_build_model(flow_mode=…)` KEEP the parameter (it still selects `_flow_mode_http_client`, ADR-0010 §3); both docstrings re-pointed from ADR-0008 §5 to ADR-0015 §4. The module no longer imports kitaru on any path.
- `src/decode/cli.py` — deleted `_uses_secret_store_model_key()` + `_model_key_secret_error()`; `_runtime_config_preflight()` lost its 5th step and its skip condition is now `if not secret_store_on:`; `_secret_store_config_error()` always runs `_provider_config_error()` against the hydrated config; `run()` docstring trimmed to the one surviving kitaru-backed source.
- `.env.example` / `README.md` / `CREDENTIALS.md` — scrubbed the knob (intro table column, the name-note blockquote, Part 1 (1a–1d), the Part 3 composed scenario now composes the Credential Proxy with the *surviving* `RUNTIME_SECRET_STORE_CONFIG`, and Part 5's two stale test-file paths dropped). `docs/glossary.md`: verified only — Environment Bucket / DECODE_ENV already in place, no model-key entry.
- `tests/unit/decode/agent/test_factory.py` — NEW regression tests (written first): `test_flow_mode_reads_the_gemini_key_from_settings` / `…_openrouter_…` — `build_agent(flow_mode=True)` builds the model from the settings `SecretStr` and `kitaru.get_secret` is never called.
- `tests/unit/decode/config/test_settings.py` — NEW `test_stale_model_key_secret_env_var_is_silently_ignored` (the red test); dropped the field from the runtime defaults / process-env / dotenv tests + `_RUNTIME_ENV_VARS`.
- `tests/unit/decode/runtime/test_run_command.py` — deleted the `_proxy_on` fixture + its 5 pre-flight tests and `test_run_model_does_not_bypass_the_proxy_secret_guard`; `_secret_store_on` no longer pins the deleted flag.
- `tests/unit/decode/runtime/test_replay_command.py` — deleted `test_replay_proxy_missing_secret_guard_does_not_replay`; the secret-store guard test no longer pins the deleted flag.
- `tests/unit/decode/runtime/test_secret_store_config.py` — the "both flags on" interplay test became `test_hydrated_key_reaches_the_model_with_no_raw_key_leak`: the secret-store source is now the only way a Kitaru secret can supply the key, and it does so by hydrating `Settings`, which the factory reads. Payload-never-carries-the-raw-key assertions retained.
- `tests/integration/test_runtime_capstone.py` — deleted the model-key slice (§6) + its module-docstring mention + the now-unused `factory_mod` / `build_agent` / `SecretStr` imports.
- **Deleted:** `tests/unit/decode/runtime/test_secret_store_model_key.py`, `tests/unit/decode/agent/test_factory_secret_store_model_key.py`.

**Tests**
- Unit: 1479 passing, 0 failing (`make unit-tests`).
- Integration + full `make ci`: green (exit 0).

**Evidence**

Red first (before the deletion):
```
$ uv run pytest tests/unit/decode/config/test_settings.py::test_stale_model_key_secret_env_var_is_silently_ignored -q
>       assert not hasattr(s, "runtime_secret_store_model_key")
E       AssertionError: assert not True
1 failed, 41 passed
```

Green after:
```
$ make unit-tests
======================= 1479 passed in 115.73s (0:01:55) =======================
$ make format-check && make lint-check
182 files already formatted
All checks passed!
$ make ci
(exit 0)
```

End-to-end (the real `decode run` surface, stale knob deliberately still set):
```
$ env -u GEMINI_API_KEY RUNTIME_SECRET_STORE_MODEL_KEY=true uv run decode run "say hi"
Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).
exit=1
    ^ the stale knob no longer diverts to Kitaru: the plain Settings guard fires.
      Pre-change this same command printed "kitaru secrets set decode-llm-creds --GEMINI_API_KEY=…".

$ RUNTIME_SECRET_STORE_MODEL_KEY=true uv run decode run "say hi in exactly three words"
Kitaru: Pipeline `run_agent_task` completed successfully.
Hi there!
exec_id: 617c7bc1-50df-48cd-9bd8-90250a1e47d0
    ^ real Gemini call, key from Settings (.env), stale knob ignored — the silent clean break.
```

**Notes**
- **The grep AC has one deliberate exception, in tests only.** AC #1 (grep returns nothing) and AC #4 (a stale `RUNTIME_SECRET_STORE_MODEL_KEY=true` changes nothing) are in tension: the test that *proves* AC #4 has to `monkeypatch.setenv("RUNTIME_SECRET_STORE_MODEL_KEY", …)` and assert `not hasattr(s, "runtime_secret_store_model_key")`, so those two literals survive in `tests/unit/decode/config/test_settings.py` by design. `src/`, `.env.example`, `README.md`, `CREDENTIALS.md`, `docs/glossary.md` are all clean.
- `flow_mode` is untouched on `build_agent` / `_build_model` (only `_provider_api_key` lost it), and `test_gemini_flow_mode_wires_the_loop_safe_http_client` still passes — the keep-alive-free client behaviour is intact.
- CREDENTIALS.md Part numbering left as 2/3/4/5 (no Part 1) — restructuring is task 102's job.
- Part 5 of CREDENTIALS.md was already stale before this task: it listed `test_credentials_proxy.py` / `test_factory_credentials_proxy.py`, files that no longer exist under those names. Both lines are gone now.

### [Tester] 2026-07-13 15:55 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 182 files already formatted; `ruff check` all checks passed; `make pre-commit` = 1479 passed)
- Unit tests: 1479 passed / 0 failed
- Integration tests: 120 passed / 0 failed (a clean serial `make integration-tests` run — see Evidence; two earlier runs I launched *concurrently by accident* raced on Docker container-teardown assertions in `test_sandbox_teardown.py` / `test_docker_executor.py`, files this task never touches; a clean serial re-run went 120/120 green, confirming those were my own concurrency artifact, not a regression)
- `uv lock --check`: clean (155 packages resolved, no drift)
- Warnings: 0

**E2E adversarial pass**
- Happy path: `uv run decode run "say hi"` (real Gemini call, `.env`-sourced key, flow_mode=True) → prints answer + `exec_id`, exit 0, no `Event loop is closed` (PASS — CRITICAL ITEM 1: `_flow_mode_http_client` survives)
- Break path 1 (clean-break silence — CRITICAL ITEM 3): `GEMINI_API_KEY="" RUNTIME_SECRET_STORE_MODEL_KEY=true uv run decode run "hi"` → `Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).`, exit 1, no traceback, no `kitaru secrets set` line (PASS — matches spec exactly; the pre-change behaviour would have printed the Kitaru-flavored line)
- Break path 2 (malformed stale-knob value): `GEMINI_API_KEY="" RUNTIME_SECRET_STORE_MODEL_KEY="garbage-not-a-bool" uv run decode run "hi"` → identical friendly line, exit 1, no `ValidationError` (PASS — `extra="ignore"` truly drops the field regardless of value shape, not just `"true"`/`"false"`)
- Break path 3 (surviving mechanism — CRITICAL ITEM 2, real infra): `test_run_secret_store_only_key_satisfies_the_provider_guard` (`tests/unit/decode/runtime/test_run_command.py:325`) creates a REAL Kitaru secret via `kitaru.create_secret` (not mocked) with the key living ONLY in the secret, then drives the real `CliRunner` through the real `_runtime_config_preflight` → exit 0, flow runs (PASS). I could not additionally hand-run this against a live secret myself: creating a new named Kitaru secret entry holding a real Gemini key was blocked by the auto-mode permission classifier (reasonable — an agent-chosen secret-store entry name holding a real credential); the test above already proves it end-to-end against the real local ZenML/Kitaru stack, so I relied on that rather than working around the block.
- Grep adversarial check (AC#1): `grep -rn "runtime_secret_store_model_key\|RUNTIME_SECRET_STORE_MODEL_KEY\|SECRET_STORE_KEY\|resolve_provider_key_from_secret_store\|_uses_secret_store_model_key\|_model_key_secret_error" src/ tests/ .env.example README.md CREDENTIALS.md docs/glossary.md` → exactly 3 hits, all in `tests/unit/decode/config/test_settings.py:360,366,370` (the test proving the stale knob is dead) (PASS — matches the SWE's Notes claim exactly, no additional stray hits anywhere else)

**Acceptance criteria**
- [x] PASS — grep for the deleted symbols returns nothing outside the one test that must name the stale knob to prove it's ignored — Evidence: grep above, 3 hits total, all `tests/unit/decode/config/test_settings.py:360/366/370`
- [x] PASS — `build_agent(flow_mode=True)` still hands Gemini the keep-alive-free httpx client; key sourcing no longer varies by mode — Evidence: `tests/unit/decode/agent/test_factory.py::test_gemini_flow_mode_wires_the_loop_safe_http_client` (untouched diff, green) + `::test_flow_mode_reads_the_gemini_key_from_settings` / `::test_flow_mode_reads_the_openrouter_key_from_settings` (new, green, `kitaru.get_secret` asserted never called); live `decode run` above confirms no cross-loop breakage
- [x] PASS — `decode run` with `RUNTIME_SECRET_STORE_CONFIG=true` + valid Kitaru secret still passes pre-flight — Evidence: `tests/unit/decode/runtime/test_run_command.py::test_run_secret_store_only_key_satisfies_the_provider_guard` (real `kitaru.create_secret`, real CliRunner, green) + full `test_secret_store_config.py` suite green (9/9)
- [x] PASS — stale `RUNTIME_SECRET_STORE_MODEL_KEY=true` changes nothing, prints nothing, silent clean break — Evidence: `test_settings.py::test_stale_model_key_secret_env_var_is_silently_ignored` green + live command above (Break path 1 & 2)
- [x] PASS — `make ci` green — Evidence: `uv lock --check` clean, `make format-check` / `make lint-check` clean, `make unit-tests` 1479 passed, `make integration-tests` (clean serial run) 120 passed

**Evidence**
```
$ make unit-tests
======================= 1479 passed in 113.46s (0:01:53) =======================

$ make integration-tests   # clean serial run, after an earlier accidental-parallel-launch flake
======================= 120 passed in 453.85s (0:07:33) ========================

$ grep -rn "runtime_secret_store_model_key\|RUNTIME_SECRET_STORE_MODEL_KEY\|SECRET_STORE_KEY\|resolve_provider_key_from_secret_store\|_uses_secret_store_model_key\|_model_key_secret_error" src/ tests/ .env.example README.md CREDENTIALS.md docs/glossary.md
tests/unit/decode/config/test_settings.py:360:    """ADR-0015 §4 (clean break): ``RUNTIME_SECRET_STORE_MODEL_KEY`` is deleted, not shimmed.
tests/unit/decode/config/test_settings.py:366:    monkeypatch.setenv("RUNTIME_SECRET_STORE_MODEL_KEY", "true")
tests/unit/decode/config/test_settings.py:370:    assert not hasattr(s, "runtime_secret_store_model_key")

$ GEMINI_API_KEY="" RUNTIME_SECRET_STORE_MODEL_KEY=true uv run decode run "say hi"
Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).
$ echo $?
1

$ grep -n "kitaru" src/decode/agent/factory.py
(no output — factory.py imports no kitaru on any path)
```

**Other issues found**
- None. The SWE's own tension flagged in Notes (grep AC vs. the AC#4 test) is adjudicated correctly: `src/` + all four docs are genuinely clean, the only surviving literals are in the test proving the knob is dead.
- Docstring cross-references (`ADR-0008 §5` still cited for the surviving `runtime_secret_store_config` mechanism in `cli.py` / `settings.py`) are intentionally untouched — correct, since that mechanism and its ADR-0008 §5 pointer die together in task 097, per Out of scope.

**VERDICT: PASS**
