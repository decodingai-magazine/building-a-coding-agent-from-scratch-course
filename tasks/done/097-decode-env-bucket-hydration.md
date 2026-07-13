---
id: 097
feature: env-bucket-secrets
status: done
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

- [x] `DECODE_ENV` defaults to `local`; `Settings().decode_env == "local"` with no env/dotenv input; the `Literal` rejects anything else with a clear error. — `tests/unit/decode/config/test_env_bucket.py::test_decode_env_defaults_to_local`, `::test_decode_env_rejects_unknown_value`
- [x] At `DECODE_ENV=local`, a fresh `python -c "import decode.cli"` subprocess has no `kitaru` module loaded (restated invariant test green), and dotenv behaviour is byte-identical to today's suite. — `::test_at_decode_env_local_decode_never_imports_kitaru`, `::test_local_still_loads_from_the_dotenv_file`, `::test_the_bucket_source_is_never_built_at_local` (+ the untouched dotenv block in `test_settings.py`)
- [x] At a remote env, `Settings` hydrates from a (stubbed) `get_secret("decode-<env>")`; process env wins over the bucket; `.env` is dropped from the chain — both the "dotenv-only key absent" and "no silent backfill" tests prove it. — `::test_bucket_hydrates_known_fields_and_ignores_unknown_keys`, `::test_process_env_overrides_a_bucket_value`, `::test_dotenv_is_dropped_at_a_remote_env`, `::test_a_key_missing_from_the_bucket_is_not_backfilled_from_dotenv`
- [x] The resolved `decode_env` fed to the gate and the value on the built `Settings` object are always identical (bootstrap feedback test). — `::test_decode_env_in_the_dotenv_file_activates_the_bucket`, `::test_process_env_decode_env_beats_the_dotenv_file`, `::test_a_bucket_supplied_decode_env_cannot_override_the_resolved_gate`, `::test_bucket_fetch_failure_is_captured_not_raised`
- [x] Remote + unreachable/missing bucket: `decode`, `decode run`, and `decode replay` each exit non-zero with ONE friendly line naming `make sync-secrets ENV=<env>` — no traceback, in the REPL startup chain and the headless pre-flight alike. — `tests/unit/decode/test_cli.py::test_repl_unloadable_bucket_exits_nonzero_with_a_friendly_line` (+ `::test_repl_bucket_guard_precedes_the_provider_key_guard`), `tests/unit/decode/runtime/test_run_command.py::test_run_unloadable_bucket_is_a_friendly_line_not_a_traceback`, `tests/unit/decode/runtime/test_replay_command.py::test_replay_unloadable_bucket_guard_does_not_replay`; e2e evidence below
- [x] `grep -rn "runtime_secret_name\|runtime_secret_store_config\|RUNTIME_SECRET_NAME\|RUNTIME_SECRET_STORE_CONFIG\|set_secret_hydration_active\|is_secret_hydration_active\|reload_settings\|_config_from_secret_store" src/ tests/` returns nothing. — verified (exit 1, no output)
- [x] `.env.example` documents `DECODE_ENV` and carries the loud clean-break note; `AGENTS.md` carries the restated invariant; ADR-0008 §5 carries the amendment pointer to ADR-0015.
- [x] `make ci` green. — 1607 passed in 9m17s

## Out of scope

- Proxy-rule template resolution (098), Opik project derivation (099), the sync script (100), the drift test (101), the `CREDENTIALS.md` rewrite (102).
- Any `DECODE_ENV` effect beyond source selection (session dirs, log paths, `MEMORY.md` — ADR-0015 non-goals).

## Log

### [SWE] 2026-07-13 — Implementation

**Files modified**

- `src/decode/config/settings.py` — `decode_env` field (closed `Literal`, default `local`); `_resolve_decode_env()` (out-of-band bootstrap: dotenv file, overlaid by `os.environ`); `EnvironmentBucketSettingsSource` (renamed, derived `decode-<env>` name, lazy kitaru import, never raises, feeds the resolved gate back into the field); `bucket_load_error()` accessor + module slot; `environment_bucket_name()`; `settings_customise_sources` now picks the chain (`local` → `(init, env, dotenv, file_secret)`, remote → `(init, env, bucket, file_secret)` — **dotenv gone**). Deleted: `set_secret_hydration_active` / `is_secret_hydration_active` / `runtime_secret_name` / `runtime_secret_store_config` / `reload_settings`.
- `src/decode/runtime/flow.py` — deleted `_config_from_secret_store()`; both flows keep only `_sandbox_proxy(repo, local)`; tracing-init comments restated (hydration is process-scoped).
- `src/decode/cli.py` — `_secret_store_config_error()` → `_env_bucket_error()`; wired FIRST into both chains (REPL startup + `_runtime_config_preflight`, whose provider guard is unconditional again); `run()` / `replay()` help text updated.
- `.env.example` — `DECODE_ENV` block at the top + the loud clean-break note; `RUNTIME_SECRET_*` lines deleted.
- `AGENTS.md` — new invariant bullet (one surface, two mechanisms) incl. the restated **"at `DECODE_ENV=local` (the default), decode never imports kitaru"**; the Credential-Proxy bullet now contrasts with the Environment Bucket.
- `docs/adr/0008-kitaru-durable-runtime.md` — §5 dated amendment pointer to ADR-0015 (append-only; historical text untouched).
- `README.md` — "Keeping keys out of the flow payload" → "Environments & secrets" pointer.
- `tests/unit/decode/config/test_env_bucket.py` — NEW (23 tests): gate, hydration, precedence, derived name, dotenv-dropped, no-backfill, bootstrap feedback, failure capture, `local` byte-identical + the fresh-subprocess no-kitaru invariant. Kitaru stubbed via `sys.modules`.
- `tests/unit/decode/runtime/test_secret_store_config.py` — DELETED (superseded by the above).
- `tests/conftest.py` — autouse `_default_decode_env` hermeticity fixture (delenv + pin the singleton), mirroring `_default_sandbox_mode` / `_no_sandbox_git_token`.
- `tests/support/runtime_fixtures.py` — `runtime_secret_name` → `env_bucket_name` (pins `DECODE_ENV=dev`, yields the derived `decode-dev`, best-effort deletes it; isolation now rests on `isolated_kitaru_store` alone).
- `tests/unit/decode/test_cli.py`, `.../runtime/test_run_command.py`, `.../runtime/test_replay_command.py` — secret-store guard tests → bucket guard tests (incl. "the bucket guard precedes the provider guard" in all three surfaces).
- `tests/unit/decode/config/test_settings.py`, `.../runtime/test_flow_tracing.py`, `.../runtime/test_store_isolation.py`, `tests/integration/test_runtime_store_isolation.py` — retired-knob slices dropped; the adverse-order trio now anchors on `test_store_isolation.py` (the deleted file was its first entry).

**Tests**

- Unit: 1487 passing, 0 failing.
- Integration: `make ci` → 1607 passing, 0 failing (9m17s).

**Evidence**

```
$ grep -rn "runtime_secret_name\|runtime_secret_store_config\|RUNTIME_SECRET_NAME\|RUNTIME_SECRET_STORE_CONFIG\|set_secret_hydration_active\|is_secret_hydration_active\|reload_settings\|_config_from_secret_store" src/ tests/
grep exit: 1 (nothing found)

$ make ci
======================= 1607 passed in 556.51s (0:09:16) =======================

# e2e — remote env, bucket missing (real kitaru, developer's real store): all three surfaces
$ DECODE_ENV=staging uv run decode
Decode: DECODE_ENV=staging but the environment bucket 'decode-staging' could not be loaded (it is missing, or the Kitaru local server is down) — run `make sync-secrets ENV=staging` (see CREDENTIALS.md).
exit=1
$ DECODE_ENV=staging uv run decode run "say hi"      → same line, exit=1
$ DECODE_ENV=dev uv run decode replay kr-abc --from cp → same line (decode-dev / ENV=dev), exit=1

# e2e — remote env, REAL kitaru bucket in an isolated store (no stubs): hydration + no env leak
$ HOME=<tmp> ZENML_CONFIG_PATH=<tmp>/cfg uv run python e2e_bucket.py
bucket decode-dev created in <tmp>/cfg
decode_env      : dev
gemini_model    : gemini-from-the-bucket
gemini_api_key  : sk-only-in-the-bucket
bucket_load_error: None
leaked to os.environ?: False
E2E_BUCKET_OK

# e2e — local (default): the REPL starts and exits clean, unchanged
$ echo "" | uv run decode
Decode - bye.      exit=0
```

**Notes**

- The `decode_env` **feedback** is deliberately applied on the failure path too (the source returns `{"decode_env": <resolved>}` when the fetch blows up), so `settings.decode_env` names the gate that was actually applied even when the bucket is unreachable — that is what lets the cli guard say `DECODE_ENV=staging … make sync-secrets ENV=staging` instead of falling back to `local` and starting a broken REPL.
- An **invalid** `DECODE_ENV` (e.g. `qa`) is not a remote env, so it takes the `local` chain and the closed `Literal` rejects it with a plain `ValidationError` — no bucket read on a typo, no kitaru import. Worth knowing: that means a typo'd env name fails as a validation error at import, not as the friendly bucket line.
- `bucket_load_error()` is a module-level slot written during `Settings()` construction. Only remote builds touch it; the `local` singleton never writes it. Tests reset it via the autouse fixture in `test_env_bucket.py`.
- Glossary **verified, not rewritten**: `docs/glossary.md` rows for **Environment Bucket** (l.51), **DECODE_ENV** (l.52) and the amended **Credential Proxy** / **Proxy Rule** rows match what shipped. Note l.51 already claims the bucket is "Kitaru's ONLY `get_secret` seam" — that becomes true in **098** (the proxy still has its own `get_secret` today); left as-is, it is PA-owned wording describing the finished feature.
- `CREDENTIALS.md` still describes the retired knobs — that rewrite is **102** by design (the friendly line points at it already).
- The `make sync-secrets` target does not exist yet (**100**); the guard names it as the fix, as the task specifies.
- Ran `make ci` twice — once before the final comment/test polish, once after; green both times.

### [Tester] 2026-07-13 16:59 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 182 files; `ruff check` all clean; `make pre-commit` green)
- Unit tests: 1487 passed / 0 failed (`make unit-tests`, 90-94s, re-run twice for stability)
- Integration tests: 120 passed / 0 failed (`make integration-tests`, 440.72s) — 1487+120 = 1607, matches the SWE's `make ci` count
- Warnings: 0

**E2E adversarial pass** (real kitaru daemon on this machine, real `.env`, real Gemini key — no stubs)
- Happy path (local): `echo "" | uv run decode` → `Decode - bye.` exit=0 (PASS); `uv run decode run "reply with exactly the word: pong"` → real Gemini HTTP 200, stdout `pong`, exit=0 (PASS); `uv run decode replay <exec_id> --from decode_runtime_model_request` → forked execution, stdout `pong`, exit=0 (PASS)
- Break path 1 (**no-backfill — the whole point of the feature**): real `GEMINI_API_KEY` left in `.env`, `kitaru secrets list` confirmed no `decode-staging` bucket exists, then `DECODE_ENV=staging uv run decode` → `Decode: DECODE_ENV=staging but the environment bucket 'decode-staging' could not be loaded ... run `make sync-secrets ENV=staging`` exit=1 — the `.env` key was NOT used to start the REPL (PASS)
- Break path 2 (**gate/field divergence + real Kitaru-daemon-down**): killed the actual local Kitaru daemon (`kill -TERM <pid>`, confirmed via `kitaru status` → "daemon: service daemon is not running"), then `DECODE_ENV=staging uv run decode` → after kitaru's own ~30s connection-retry storm, still resolved to the correct one-line friendly error naming `make sync-secrets ENV=staging`, exit=1, no unhandled traceback from decode's own code (PASS); restarted the daemon afterward (`kitaru login`) and confirmed `kitaru status` shows it running again before continuing
- Break path 3 (**bootstrap precedence, both directions**): `.env` with `DECODE_ENV=staging` only → `Settings(_env_file=...)` resolves `decode_env="staging"` and reads the bucket (dotenv activates the gate); `.env` with `DECODE_ENV=staging` + process `DECODE_ENV=dev` → resolves `"dev"` (process env beats dotenv) (PASS)
- Break path 4 (**restated local-only invariant, genuinely clean env**): `env -i PATH=... HOME=... uv run python -c "import decode.cli; Settings(_env_file=None); assert no kitaru in sys.modules"` → `NO_KITARU_OK`, no leaked `kitaru*` modules (PASS)
- Break path 5 (**all three surfaces, real missing bucket**): `DECODE_ENV=staging uv run decode`, `DECODE_ENV=staging uv run decode run "say hi"`, `DECODE_ENV=dev uv run decode replay kr-abc --from cp` — all three exit=1 with the one friendly line naming `make sync-secrets ENV=<env>`, none crash or hang (PASS)
- Break path 6 (**malformed value inside a successfully-fetched bucket** — not in the ACs, flagged below): a fake bucket returning `LLM_PROVIDER=totally-bogus` crashes `Settings()` at import with a raw pydantic `ValidationError` traceback, bypassing the friendly-line guard entirely — same pre-existing behavior as a bad `.env`/process-env `LLM_PROVIDER` today (reproduced independently), not a regression this task introduced, and outside AC5's literal "unreachable/missing bucket" wording (NOTE, not a FAIL)

**Acceptance criteria**
- [x] PASS — `DECODE_ENV` defaults to `local`; Literal rejects anything else — `tests/unit/decode/config/test_env_bucket.py::test_decode_env_defaults_to_local`, `::test_decode_env_rejects_unknown_value`; manually confirmed `Settings(_env_file=None).decode_env == "local"`
- [x] PASS — At `local`, no kitaru import in a fresh subprocess, dotenv unchanged — `::test_at_decode_env_local_decode_never_imports_kitaru` is a genuine fresh-subprocess check (verified by reading it); independently reproduced with `env -i` above
- [x] PASS — Remote hydration, process-env-over-bucket, `.env` dropped — `::test_bucket_hydrates_known_fields_and_ignores_unknown_keys`, `::test_process_env_overrides_a_bucket_value`, `::test_dotenv_is_dropped_at_a_remote_env`, `::test_a_key_missing_from_the_bucket_is_not_backfilled_from_dotenv`; independently reproduced end-to-end in break path 1 with a real `.env` key and a real missing bucket
- [x] PASS — Gate/field never diverge, incl. on fetch failure — `::test_a_bucket_supplied_decode_env_cannot_override_the_resolved_gate`, `::test_bucket_fetch_failure_is_captured_not_raised`; independently reproduced against a real downed Kitaru daemon (break path 2) — `settings.decode_env` stayed `"staging"` and the cli guard fired correctly
- [x] PASS — All three surfaces exit non-zero with the one friendly line, guard FIRST — `tests/unit/decode/test_cli.py::test_repl_unloadable_bucket_exits_nonzero_with_a_friendly_line` + `::test_repl_bucket_guard_precedes_the_provider_key_guard`, `test_run_command.py::test_run_unloadable_bucket_is_a_friendly_line_not_a_traceback`, `test_replay_command.py::test_replay_unloadable_bucket_guard_does_not_replay`; independently reproduced live for all three commands (break path 5)
- [x] PASS — grep for the 8 deleted names returns nothing — reran verbatim: exit 1, no output
- [x] PASS — `.env.example` / `AGENTS.md` / ADR-0008 §5 updated — read all three diffs; `.env.example` carries the `DECODE_ENV` block + loud clean-break note, `AGENTS.md` restates "at `DECODE_ENV=local` (the default), decode never imports kitaru", ADR-0008 §5 carries a dated, append-only amendment pointer to ADR-0015. `docs/glossary.md` rows for **Environment Bucket** / **DECODE_ENV** present and topically match (l.51-52) — presence/topical check only, per Tester scope
- [x] PASS — `make ci` green — reproduced as `make unit-tests` (1487) + `make integration-tests` (120) = 1607, matching the SWE's count exactly

**Evidence**
```
$ DECODE_ENV=staging uv run decode <<< ""
Decode: DECODE_ENV=staging but the environment bucket 'decode-staging' could not be loaded (it is missing, or the Kitaru local server is down) — run `make sync-secrets ENV=staging` (see CREDENTIALS.md).
EXIT=1   # real GEMINI_API_KEY sat in .env the whole time — never used

$ kill -TERM <kitaru-daemon-pid>; kitaru status   # confirmed: "daemon: service daemon is not running"
$ DECODE_ENV=staging uv run decode <<< ""
... (kitaru's own ~30s HTTP retry storm, this decode process itself never raises) ...
Decode: DECODE_ENV=staging but the environment bucket 'decode-staging' could not be loaded ... make sync-secrets ENV=staging ...
EXIT=1

$ uv run decode run "reply with exactly the word: pong"   # DECODE_ENV=local, real Gemini
... HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent "HTTP/1.1 200 OK" ...
pong
EXIT=0

$ make unit-tests
======================= 1487 passed in 90.24s (0:01:30) =======================
$ make integration-tests
======================= 120 passed in 440.72s (0:07:20) =======================
```

**Other issues found**
- Killing the real local Kitaru daemon makes the bucket-failure path take ~30s (kitaru/zenml's own HTTP retry policy) and leaks several `Kitaru: Retrying ...` lines to **stdout** before the one friendly line correctly lands on stderr. The friendly-line contract itself is honored (right message, right stream, exit 1, no traceback from decode's own code) — this is kitaru/zenml SDK retry behavior, not code this task added or can cheaply silence; worth a follow-up ticket to set a shorter connect-timeout on the bucket lookup if this class of daemon-down UX matters.
- A bucket that fetches successfully but carries a value that fails a Settings field's validator (e.g. a bogus `LLM_PROVIDER`) still crashes `Settings()` at import with a raw traceback, bypassing the friendly-line guard — same as today's behavior for a bad `.env`/process-env value (reproduced independently, not a regression), and outside AC5's literal "unreachable/missing bucket" wording. Consistent with the SWE's own noted `DECODE_ENV=qa` typo caveat. Worth a follow-up hardening task, not blocking this one.
- Restarting the Kitaru daemon after killing it for break path 2 surfaced a pre-existing macOS-only daemon crash (`objc[...]: +[NSCharacterSet initialize] ... Crashing instead`) unrelated to this task's code — an environment quirk on this machine, noted for completeness, not attributable to the SWE's diff.
- The `code-review` plugin's `/code-review` command targets an existing GitHub PR (fetches via `gh pr view` and posts `gh pr comment`); this branch is uncommitted with no PR yet, so the command has no PR object to review and could not be meaningfully invoked at this stage — noted as N/A rather than skipped silently.

**VERDICT: PASS**
