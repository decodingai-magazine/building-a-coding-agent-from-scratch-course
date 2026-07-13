---
id: 101
feature: env-bucket-secrets
status: done
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

- [x] The drift test exists, runs in `make unit-tests`, and passes on the current tree.
- [x] Deleting any documented line for an existing `Settings` field from `.env.example` makes it fail naming that field; adding a bogus `TYPO_VAR=1` line (commented or not) makes it fail naming `TYPO_VAR` — demonstrated in the task's QA notes, not committed.
- [x] No allowlist / exclusion set exists in the test.
- [x] `DECODE_LOG_FILE` / `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` remain documented in `.env.example` as prose (still findable by grep) but no longer parse as keys; `MODAL_PROXY_TOKEN_*` remain normal `KEY=` lines.
- [x] The old per-section `test_env_example_lists_every_*_var` tests are gone (subsumed).
- [x] `make ci` green.

## Out of scope

- Enforcing that a user's real `.env` is well-formed; validating values; policing AGENTS.md's "new env var →
  `.env.example` + `settings.py`" rule beyond what this test mechanically enforces.

## Log

### [SWE] 2026-07-13 — Implementation

**The rule the test enforces (no allowlist, no third list)**

`{KEY for lines matching ^\s*#?\s*([A-Z][A-Z0-9_]*)= in .env.example}` == `{name.upper() for name in
Settings.model_fields}`, asserted in BOTH directions as two named tests, each failing with the fix to make.
Checked `model_config` first: no `env_prefix`, no field aliases, so `NAME.upper()` IS the env-var name. A
future alias would only ever make the test fail (never pass wrongly), so it needs no guard.

**Files modified**
- `tests/unit/decode/config/test_env_example_drift.py` — NEW. The two-direction drift guard.
- `tests/unit/decode/config/test_settings.py` — retired the 5 subsumed one-way
  `test_env_example_lists_every_{lsp,runtime,sandbox,subagent,opik}_var` guards; left a pointer comment.
- `.env.example` — reconciled onto the surface (see below).

**`.env.example` reconciliation.** The test found REAL drift on the current tree, in both directions:
- 7 `Settings` fields were undocumented (they would have silently vanished at every remote env, since
  `sync_secrets.py` only mirrors keys present in the file): `BASH_TIMEOUT_S`, `MAX_OUTPUT_LINES`,
  `MAX_OUTPUT_BYTES`, `WEB_FETCH_TIMEOUT_S` (new "Tool execution / output truncation" block),
  `MEMORY_MAX_LINES`, `MEMORY_MAX_BYTES` (new "Memory" block), `DECODE_DIR` (into "Harness artifacts").
- 3 keys were not fields → converted to PROSE (grep-findable, no `KEY=` shape), which is a correctness fix:
  both are read from `os.environ`, never from `.env`, so the old lines actively lied.
  - `DECODE_LOG_FILE` — `src/decode/logging.py:30` `os.environ.get("DECODE_LOG_FILE")`; not a Settings field.
  - `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` — `src/decode/cli.py:154`
    `os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET")`; not Settings fields
    (`MODAL_PROXY_TOKEN_ID` / `_SECRET` are a DIFFERENT pair, they ARE fields, and stay live `KEY=` lines).

**Mutation evidence — the guard bites (all reverted, none committed)**
```
A) add `drifty_new_knob: str = "x"` to Settings:
E  AssertionError: 1 Settings field(s) are not documented in .env.example: DRIFTY_NEW_KNOB. Add a line
   for each (e.g. `# DRIFTY_NEW_KNOB=<default>`) — an undocumented field is missing from the Environment
   Bucket that `make sync-secrets` mirrors, so the knob silently disappears at every remote environment.
B) append a COMMENTED `# TYPO_VAR=1` to .env.example:
E  AssertionError: 1 key(s) in .env.example are not Settings fields: TYPO_VAR. Remove the line, or add
   the field to config/settings.py. A variable genuinely read from os.environ belongs here as PROSE...
C) delete the `# SESSIONS_DIR=` line:
E  AssertionError: 1 Settings field(s) are not documented in .env.example: SESSIONS_DIR. ...
reverted → 2 passed
```

**`sync_secrets.py` (100) needed no change** — it already derives the surface from `Settings.model_fields`
(`syncable_keys`), so script and test share ONE derivation. Verified they agree: documented keys == Settings
keys == the 52 keys the script would mirror, plus the `DECODE_ENV` gate it deliberately excludes.

**Tests**
- `make ci` GREEN: 1513 unit + integration → **1633 passed** (0 failed). format/lint/pre-commit clean.

**Notes**
- The prose line `#   export DECODE_LOG_FILE=/tmp/decode.log` is not parsed as a key (the regex anchors the
  NAME at line start after an optional `#`; here the line starts with `export`) — that shape is deliberate:
  it shows the user the exact command AND keeps the name out of the surface.
