---
id: 097
feature: env-bucket-secrets
status: pending
---

# DECODE_ENV + Environment-Bucket hydration (the config-surface cutover)

Depends on: 096. Implements ADR-0015 §1–§3, §5, and the second half of §4.

## Scope

Replace the headless-only, flag-gated Kitaru config source with the `DECODE_ENV` environment gate: one
config surface (`Settings`), two injection mechanisms (`.env` at `local`, the derived Kitaru bucket
`decode-<env>` everywhere else), active in **both** surfaces (TUI and headless).

**`src/decode/config/settings.py`**

- New field: `decode_env: Literal["local", "dev", "staging", "prod"] = "local"` (env var `DECODE_ENV`).
  It selects the injection mechanism, nothing else.
- **Bootstrap resolution (out-of-band).** `DECODE_ENV` decides whether to read the bucket, so it can never
  come *from* the bucket. Add a module helper (e.g. `_resolve_decode_env()`) used by
  `settings_customise_sources` and the source: parse the dotenv file for `DECODE_ENV` (`dotenv_values`;
  honour the `dotenv_settings` source's `env_file` so `Settings(_env_file=…)` test builds behave), then
  overlay `os.environ["DECODE_ENV"]` — **process env wins**, consistent with every other setting; default
  `"local"`. Carry a clear comment explaining why this one variable is read out-of-band. The source feeds
  the resolved value back into the field (include `decode_env` in its returned mapping) so
  `settings.decode_env` can never diverge from the gate that was actually applied.
- **Source chain (`settings_customise_sources`).** `local` → `(init, env, dotenv, file_secret)` — today's
  behaviour, zero kitaru. Non-local → `(init, env, <bucket source>, file_secret)`: **dotenv is dropped from
  the chain entirely**, so a key missing from the bucket fails loudly instead of being silently backfilled
  from a developer's `.env`. Precedence at remote: process env > bucket > defaults.
- **The source.** Rename `KitaruSecretSettingsSource` → `EnvironmentBucketSettingsSource` (match the
  glossary). Active iff the resolved `decode_env != "local"`; bucket name is **derived**:
  `f"decode-{decode_env}"` — no override knob. Keeps the two safety invariants: lazy kitaru import (never
  imported at `local`), values land in this `Settings` object only — never `os.environ`; logs hydrated
  field **names** only.
- **Failure capture, not import-time crash.** The singleton is built at import, so the source must not
  raise: catch the fetch failure (missing bucket, Kitaru local daemon down), record it in a module-level
  slot exposed via an accessor (e.g. `bucket_load_error() -> str | None`), and return only
  `{"decode_env": …}`. The startup chains below turn it into the one friendly line.
- **Delete:** `set_secret_hydration_active` / `is_secret_hydration_active` (the headless-only gate this
  feature replaces), `runtime_secret_name`, `runtime_secret_store_config`, and `reload_settings()` (its
  only caller dies below).

**`src/decode/runtime/flow.py`**

- Delete `_config_from_secret_store()`; the two `with _config_from_secret_store(), _sandbox_proxy(repo, local):`
  sites (bypass + HITL flows) keep only `_sandbox_proxy`. Hydration is process-scoped now — the singleton
  was hydrated at import; no per-flow snapshot/restore is needed or wanted. Update the tracing-init comments
  ("AFTER `_config_from_secret_store`" is obsolete: a bucket-hydrated `OPIK_API_KEY` is simply already in
  settings).

**`src/decode/cli.py`**

- Replace `_secret_store_config_error()` with `_env_bucket_error() -> str | None`: `None` at `local`; at
  remote, if `bucket_load_error()` is set, return ONE friendly line naming the fix — e.g.
  `Decode: DECODE_ENV=staging but the environment bucket 'decode-staging' could not be loaded (missing, or the Kitaru local server is down) — run 'make sync-secrets ENV=staging' (see CREDENTIALS.md).`
  — exit non-zero, no traceback (house pattern).
- Wire it into **both** surfaces, **first** in each chain (before the provider guard — at a remote env the
  provider key is expected to come from the bucket, so a bucket failure must name `make sync-secrets`, not
  `GEMINI_API_KEY`):
  - the bare-`decode` REPL startup chain (new guard ahead of `_provider_config_error()`);
  - `_runtime_config_preflight()`, whose ordering simplifies now that hydration is process-scoped:
    (1) bucket guard, (2) provider guard — unconditional again, it runs against hydrated settings,
    (3) `RUNTIME_ENABLED`, (4) sandbox backend, (5) sandbox repo. Update the docstring and the `run()` help text.

**Docs in this task**

- `.env.example`: add a `DECODE_ENV` block at the top (what it selects, the derived bucket name,
  `make sync-secrets ENV=<env>`); delete `RUNTIME_SECRET_NAME` / `RUNTIME_SECRET_STORE_CONFIG`; add a **loud**
  clean-break comment — the `RUNTIME_SECRET_*` knobs are gone and are silently ignored; `DECODE_ENV` replaces
  them (see ADR-0015 / CREDENTIALS.md).
- `AGENTS.md`: restate the invariant as **"at `DECODE_ENV=local` (the default), decode never imports kitaru"**
  (wherever the old headless-only phrasing lives).
- `docs/glossary.md`: already carries **Environment Bucket** + **DECODE_ENV** (written in the plan commit) —
  verify the wording matches what shipped; do not duplicate.
- `docs/adr/0008-kitaru-durable-runtime.md` §5: append the dated amendment pointer — §5's secret-store knobs
  are deleted/replaced by the Environment Bucket; see ADR-0015. **Append-only**; never rewrite the historical text.
- `README.md`: replace the "Keeping keys out of the flow payload" section body with a short
  "Environments & secrets" pointer (`DECODE_ENV`, bucket, `make sync-secrets`) — the full narrative pass is 102's.

**Tests** (TDD-first; unit, kitaru stubbed via monkeypatched `get_secret` except where the isolated store is exercised)

- Rewrite `tests/unit/decode/runtime/test_secret_store_config.py` → `tests/unit/decode/config/test_env_bucket.py`
  (it is a settings feature now):
  - at `DECODE_ENV=staging`, bucket values hydrate known fields; unknown keys ignored;
  - process env overrides a bucket value; bucket values never touch `os.environ`;
  - **dotenv dropped at remote**: a key present only in `.env` does NOT reach `Settings`;
  - a key missing from the bucket is NOT backfilled from `.env` (the loud-failure property);
  - the bucket name is derived (`decode-dev` for `DECODE_ENV=dev`);
  - bootstrap: `DECODE_ENV` in the dotenv file activates the bucket; process env beats dotenv; an invalid
    value fails validation with a clear error;
  - fetch failure → `bucket_load_error()` set, `Settings()` still constructs (no import crash);
  - at `local`: byte-identical to today — dotenv works, no kitaru import.
- Restate `test_bare_decode_path_does_not_import_kitaru` as the `DECODE_ENV=local` invariant (fresh-subprocess
  import check, as today).
- cli tests: REPL + `decode run` + `decode replay` each exit 1 with the friendly bucket line when remote +
  `bucket_load_error()`; pre-flight order updates in `test_run_command.py` / `test_replay_command.py`.
- `tests/unit/decode/runtime/test_flow_tracing.py`: drop the `_config_from_secret_store` slices (tracing now
  simply reads hydrated settings).
- `tests/support/runtime_fixtures.py`: retire the `runtime_secret_name` fixture (derived names make
  unique-per-test buckets impossible); replace with a fixture that pins `DECODE_ENV` and best-effort-deletes
  `decode-dev` inside the isolated store. Isolation now rests on `isolated_kitaru_store` alone — say so in the
  fixture docstring. Update `test_store_isolation.py` accordingly.
- `tests/conftest.py`: add an autouse hermeticity fixture mirroring `_default_sandbox_mode` —
  `monkeypatch.delenv("DECODE_ENV")` + pin `settings.decode_env = "local"` — so a developer's exported
  `DECODE_ENV` can never flip the suite remote.

## Acceptance Criteria

- [ ] `DECODE_ENV` defaults to `local`; `Settings().decode_env == "local"` with no env/dotenv input; the `Literal` rejects anything else with a clear error.
- [ ] At `DECODE_ENV=local`, a fresh `python -c "import decode.cli"` subprocess has no `kitaru` module loaded (restated invariant test green), and dotenv behaviour is byte-identical to today's suite.
- [ ] At a remote env, `Settings` hydrates from a (stubbed) `get_secret("decode-<env>")`; process env wins over the bucket; `.env` is dropped from the chain — both the "dotenv-only key absent" and "no silent backfill" tests prove it.
- [ ] The resolved `decode_env` fed to the gate and the value on the built `Settings` object are always identical (bootstrap feedback test).
- [ ] Remote + unreachable/missing bucket: `decode`, `decode run`, and `decode replay` each exit non-zero with ONE friendly line naming `make sync-secrets ENV=<env>` — no traceback, in the REPL startup chain and the headless pre-flight alike.
- [ ] `grep -rn "runtime_secret_name\|runtime_secret_store_config\|RUNTIME_SECRET_NAME\|RUNTIME_SECRET_STORE_CONFIG\|set_secret_hydration_active\|is_secret_hydration_active\|reload_settings\|_config_from_secret_store" src/ tests/` returns nothing.
- [ ] `.env.example` documents `DECODE_ENV` and carries the loud clean-break note; `AGENTS.md` carries the restated invariant; ADR-0008 §5 carries the amendment pointer to ADR-0015.
- [ ] `make ci` green.

## Out of scope

- Proxy-rule template resolution (098), Opik project derivation (099), the sync script (100), the drift test (101), the `CREDENTIALS.md` rewrite (102).
- Any `DECODE_ENV` effect beyond source selection (session dirs, log paths, `MEMORY.md` — ADR-0015 non-goals).

## Log
