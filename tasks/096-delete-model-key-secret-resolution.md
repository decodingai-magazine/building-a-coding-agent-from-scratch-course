---
id: 096
feature: env-bucket-secrets
status: pending
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

- [ ] `grep -rn "runtime_secret_store_model_key\|RUNTIME_SECRET_STORE_MODEL_KEY\|SECRET_STORE_KEY\|resolve_provider_key_from_secret_store\|_uses_secret_store_model_key\|_model_key_secret_error" src/ tests/ .env.example README.md CREDENTIALS.md docs/glossary.md` returns nothing (`docs/adr/` history exempt).
- [ ] `build_agent(flow_mode=True, …)` still hands Gemini the keep-alive-free httpx client (the existing flow-mode client test stays green) while the provider key is read from `Settings` in both flow and interactive mode.
- [ ] `decode run` with `RUNTIME_SECRET_STORE_CONFIG=true` and a valid Kitaru secret still passes its pre-flight (the surviving mechanism is untouched) — the `test_secret_store_config.py` suite is green after interplay updates.
- [ ] An env still carrying `RUNTIME_SECRET_STORE_MODEL_KEY=true` changes nothing and prints nothing (`extra="ignore"` — the silent clean break).
- [ ] `make ci` green.

## Out of scope

- Deleting `runtime_secret_name` / `runtime_secret_store_config` (task 097).
- The cohesive `CREDENTIALS.md` rewrite (task 102).
- Any `DECODE_ENV` work.

## Log
