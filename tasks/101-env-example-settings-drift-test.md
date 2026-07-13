---
id: 101
feature: env-bucket-secrets
status: pending
---

# Drift test: .env.example ≡ Settings, both directions, no allowlist

Depends on: 096, 097, 099. Implements ADR-0015 §9 (the "one config surface" enforcement).

## Scope

A unit test (no network) that makes config drift a CI failure in **both** directions:

- every `Settings` field is documented in `.env.example` (its uppercased field name appears as a `KEY=` line,
  commented or not);
- every `KEY=` line in `.env.example` is a real `Settings` field (a typo fails CI).

**No allowlist.**

**`tests/unit/decode/config/test_env_example_drift.py`**

- Parse `.env.example` for lines matching `^\s*#?\s*([A-Z][A-Z0-9_]*)=` (a commented example line documents the
  var — it counts); compare the resulting set against `{name.upper() for name in Settings.model_fields}`. Assert
  set equality, with readable missing/extra diffs in the failure message.
- Retire the now-subsumed one-way `test_env_example_lists_every_*_var` checks in `test_settings.py` (this global
  two-direction test replaces the scattered per-section ones).

**`.env.example` reconciliation.** Three current entries are **not** `Settings` fields, by design, and "no
allowlist" is locked — so they must stop parsing as keys while staying documented. **All three are genuinely
read from `os.environ`, never from `.env`** (decode never exports `.env` into the process env), so the current
`KEY=` lines actively mislead: filling them into `.env` does nothing. Rewording is therefore a correctness fix,
not just test appeasement.

- `DECODE_LOG_FILE` → prose comment: "Optional log-path override — **export** it; `logging.py` reads it from the
  process env before settings exist. An empty string disables file logging." No `KEY=` shape.
- `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` → prose comment pointing at `modal token set` (or exporting them in
  your shell); `cli.py` checks them via `os.environ` exactly as the modal CLI resolves them. No `KEY=` shape.

Note: `MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET` are a **different pair** — they *are* `Settings`
fields (the served-endpoint auth headers) and stay as normal `KEY=` lines.

## Acceptance Criteria

- [ ] The drift test exists, runs in `make unit-tests`, and passes on the current tree.
- [ ] Deleting any documented line for an existing `Settings` field from `.env.example` makes it fail naming that field; adding a bogus `TYPO_VAR=1` line (commented or not) makes it fail naming `TYPO_VAR` — demonstrated in the task's QA notes, not committed.
- [ ] No allowlist / exclusion set exists in the test.
- [ ] `DECODE_LOG_FILE` / `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` remain documented in `.env.example` as prose (still findable by grep) but no longer parse as keys; `MODAL_PROXY_TOKEN_*` remain normal `KEY=` lines.
- [ ] The old per-section `test_env_example_lists_every_*_var` tests are gone (subsumed).
- [ ] `make ci` green.

## Out of scope

- Enforcing that a user's real `.env` is well-formed; validating values; policing AGENTS.md's "new env var →
  `.env.example` + `settings.py`" rule beyond what this test mechanically enforces.

## Log
