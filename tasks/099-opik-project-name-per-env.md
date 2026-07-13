---
id: 099
feature: env-bucket-secrets
status: pending
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

- [ ] `Settings().opik_project_name == "decode-local"` with no explicit input; `decode-staging` under `DECODE_ENV=staging`.
- [ ] An explicit `OPIK_PROJECT_NAME` from env / dotenv (local) / bucket (remote) always wins — one test per source.
- [ ] The Opik OTLP `projectName` header carries the resolved name.
- [ ] `.env.example` documents the derived default.
- [ ] `make ci` green.

## Out of scope

- Any other per-env behaviour (session dirs, log paths, `MEMORY.md` — ADR-0015 non-goals). Opik workspace/key
  handling is unchanged.

## Log
