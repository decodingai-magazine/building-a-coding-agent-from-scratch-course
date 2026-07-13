---
id: 099
feature: env-bucket-secrets
status: done
---

# Opik project name defaults to decode-<env>

Depends on: 097. Implements ADR-0015 §8.

## Scope

`opik_project_name` **defaults** to `f"decode-{decode_env}"` (local → `decode-local`, staging →
`decode-staging`); an explicitly-set `OPIK_PROJECT_NAME` — from process env, `.env`, or the bucket — always
wins.

**`src/decode/config/settings.py`**

- Detect "was it explicitly set" via pydantic's `model_fields_set`, **not** a sentinel comparison. (Verified
  against the installed pydantic-settings 2.14.2: source-supplied values land in `model_fields_set`;
  default-applied values do not.) Suggested shape: an `@model_validator(mode="after")` that derives the value
  when `"opik_project_name" not in self.model_fields_set` (use `object.__setattr__` if needed to avoid
  polluting `fields_set`); the SWE picks the exact mechanics. The declared default becomes cosmetic — update
  the field comment to say "derived: `decode-<DECODE_ENV>` unless explicitly set".
- `observability/tracing.py` needs no change (it already reads `settings.opik_project_name`).

**Docs**

- `.env.example` Opik block: `# OPIK_PROJECT_NAME=decode-local` with a "defaults to `decode-<DECODE_ENV>`;
  set to override" comment.

**Tests** (`tests/unit/decode/config/test_settings.py` + a tracing spot-check)

- default at `local` == `decode-local`; with `DECODE_ENV=staging` (stubbed bucket) == `decode-staging`;
- an explicit `OPIK_PROJECT_NAME` wins: via process env (at both local and remote), via `.env` (at local),
  via the bucket (at remote) — one test per source, each asserting the mechanism is `model_fields_set`, not a
  value comparison;
- `init_tracing()` puts the derived name on the OTLP `projectName` header (existing header-assertion pattern,
  in-memory exporter).

## Acceptance Criteria

- [x] `Settings().opik_project_name == "decode-local"` with no explicit input; `decode-staging` under `DECODE_ENV=staging`.
- [x] An explicit `OPIK_PROJECT_NAME` from env / dotenv (local) / bucket (remote) always wins — one test per source.
- [x] The Opik OTLP `projectName` header carries the resolved name.
- [x] `.env.example` documents the derived default.
- [x] `make ci` green.

## Out of scope

- Any other per-env behaviour (session dirs, log paths, `MEMORY.md` — ADR-0015 non-goals). Opik workspace/key
  handling is unchanged.

## Log

### [SWE] 2026-07-13 — Implementation

**Files modified**
- `src/decode/config/settings.py` — `opik_project_name` gains a DERIVED default `decode-<DECODE_ENV>` via an
  `@model_validator(mode="after")` gated on `model_fields_set`; declared default is now the cosmetic
  `"decode-local"`.
- `.env.example` — Opik block documents the derived default (`OPIK_PROJECT_NAME` KEY= line kept for the task-101
  drift test).
- `.claude/skills/manual-e2e-qa/SKILL.md` — the Opik startup line it quotes said `project 'decode'` (stale).
- `tests/unit/decode/config/test_settings.py` — local chain: derived default, explicit-wins via process env and
  via dotenv (each asserting `model_fields_set`), anti-sentinel.
- `tests/unit/decode/config/test_env_bucket.py` — remote chain (stubbed bucket): derived per env, bucket- and
  process-env-supplied names win, discriminating anti-sentinel (explicit `decode-local` at `DECODE_ENV=dev`).
- `tests/unit/decode/observability/test_tracing.py` — spot-check: the derived name rides the OTLP `projectName`.
- `tests/unit/decode/tui/test_app_tracing.py` — startup line built from the singleton, not the literal `decode`.

**Tests**
- Unit: 1502 passing, 0 failing. Integration: 120 passing. `make ci`: **1622 passed in 551.96s**.

**Acceptance criteria**
- [x] Derived default — `test_opik_project_name_is_derived_from_decode_env_when_unset`,
  `test_opik_project_name_is_derived_from_a_remote_decode_env[dev|staging|prod]`.
- [x] Explicit wins, one test per source — `test_reads_opik_vars_from_process_env`,
  `test_loads_opik_vars_from_a_dotenv_file`, `test_a_bucket_supplied_opik_project_name_wins_over_the_derived_default`,
  `test_a_process_env_opik_project_name_wins_at_a_remote_env`.
- [x] OTLP header — `test_init_tracing_header_carries_the_derived_per_env_project_name`.
- [x] `.env.example` documents the derived default.
- [x] `make ci` green.

**Evidence**
```
$ DECODE_ENV=staging uv run python -c "from decode.config.settings import settings, bucket_load_error; ..."
env         : staging
project     : decode-staging
bucket err  : decode-staging: Secret `decode-staging` was not found.

$ uv run python -c "<real singleton + explicit override + init_tracing header>"
singleton   : local -> decode-local
fields_set  : False
explicit    : my-project          # OPIK_PROJECT_NAME=my-project
otlp header : True decode-local https://www.comet.com/opik/api/v1/private/otel/v1/traces

$ make ci
======================= 1622 passed in 551.96s (0:09:11) =======================
```

**Notes**
- Mechanism is `model_fields_set`, never a sentinel: the discriminating test sets `OPIK_PROJECT_NAME=decode-local`
  at `DECODE_ENV=dev` — a value comparison against the declared default would silently rewrite it to `decode-dev`.
- `object.__setattr__` writes the derived value: no re-validation, and it does not forge an "explicit" mark in
  `model_fields_set` (asserted).
- The suffix applies ALWAYS (ADR-0015 §8): there is no bare `decode` project any more, `local` → `decode-local`.
- `observability/` untouched — this is a naming default, not a tracing-behaviour change.
