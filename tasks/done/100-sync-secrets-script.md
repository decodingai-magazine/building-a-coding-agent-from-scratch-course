---
id: 100
feature: env-bucket-secrets
status: done
---

# scripts/sync_secrets.py + make sync-secrets — mirror .env into the Environment Bucket

Depends on: 097. Implements ADR-0015 §7.

## Scope

One-way operator sync: `.env` → the derived bucket `decode-<env>`.

**The verified fact the whole design hangs on:** `kitaru secrets set` **REPLACES the entire key set** —
setting one key destroys the others (proven live: `set X --AAA=1 --BBB=2` then `set X --CCC=3` leaves only
`CCC`). So the only safe shape is **one full-surface call**, which also makes the bucket an exact **mirror**
of the file.

**`scripts/sync_secrets.py`** (an operator script — `scripts/` is outside the decode import graph, so
importing kitaru here can never touch the `DECODE_ENV=local` no-kitaru invariant)

- CLI: `--env {dev,staging,prod}` (required; `local` is rejected with a friendly line — local reads `.env`
  directly, there is nothing to sync), `--env-file` (default `.env`), `--yes` (skip the confirmation — CI).
- Read **every** key/value from the env file (`dotenv_values`; empty values are legal and mirrored). A
  missing/empty file → friendly error, non-zero.
- **Key-name-only diff before overwriting an existing bucket**: fetch the current bucket (lazy `kitaru` import;
  a missing bucket means "create", shown as all-added), print added / removed / changed **key names** — never
  values (changed-detection may compare values in memory; nothing but names reaches stdout/stderr/logs) — then
  prompt `y/N`; `--yes` skips.
- Push in ONE call: `kitaru secrets set decode-<env> --private --KEY=value …` — a single subprocess invocation,
  list argv (no shell). A non-zero exit propagates with a friendly line.
- Values never echoed, never logged, at any verbosity. One-way only — no bucket → `.env` path exists.

**`Makefile`**

- `sync-secrets:  ## Mirror .env into the Kitaru environment bucket decode-$(ENV)` → guard: fail with a usage
  line when `ENV` is unset; else `uv run python scripts/sync_secrets.py --env $(ENV)`. Add to `.PHONY` and keep
  it discoverable in `make help`.

**Docs**

- The `.env.example` `DECODE_ENV` block and the README get the one-liner (`make sync-secrets ENV=staging`); the
  tutorial treatment is 102's.

**Tests** — `tests/unit/scripts/test_sync_secrets.py` (unit, no network: monkeypatch `subprocess.run` + the
bucket fetch)

- exactly **one** `kitaru secrets set` invocation, argv ==
  `["kitaru","secrets","set","decode-staging","--private","--KEY=value",…]` covering every file key;
- diff output: names only for added/removed/changed; a value string planted in the file never appears in
  captured stdout/stderr/log records;
- confirmation: aborts (no subprocess call) on decline; `--yes` skips the prompt;
- fresh-bucket path (fetch raises "not found") proceeds as a create;
- `--env local` and a missing env file each exit non-zero with a friendly line.

## Acceptance Criteria

- [x] `make sync-secrets ENV=staging` invokes the script; bare `make sync-secrets` fails with a usage line.
- [x] The push is a **single** `kitaru secrets set decode-<env> --private …` call carrying **every** key of the env file — verified by the argv-shape test (the replace-semantics safety property).
- [x] Overwriting an existing bucket first prints an added/removed/changed **key-name** diff and requires confirmation; `--yes` bypasses it; no secret value ever appears in any output or log (asserted).
- [x] `--env local` is rejected with a friendly line; the sync is one-way (no read-back path exists).
- [x] `make ci` green (the script is ruff-clean; the tests are hermetic).

## Out of scope

- Any bucket → `.env` pull, partial-key updates, non-`kitaru` backends, or CI wiring that calls the target.
- Validating that file keys are Settings fields — the bucket mirrors the **file**; the drift test (101) polices
  the file.

## Log

### [SWE] 2026-07-13 — Implementation

**Files modified**
- `scripts/sync_secrets.py` — the one-way mirror: `.env` → the Environment Bucket `decode-<env>`, in ONE
  `kitaru secrets set` call (replace semantics), key-names-only output.
- `Makefile` — `sync-secrets` target (`ENV` guard + usage line); `.PHONY`.
- `pyproject.toml` — pytest `pythonpath` gains `"."` so the unit tests can import the unpackaged `scripts/`.
- `README.md` — one paragraph on what the mirror pushes (Settings fields; never `DECODE_ENV`) and why one call.
- `tests/unit/scripts/test_sync_secrets.py` — 14 hermetic tests (fake `kitaru` module + monkeypatched
  `subprocess.run`).

**Tests**
- Unit: 1516 passing, 0 failing. `make ci`: 1636 passing (lock check + format + lint + unit + integration).

**Deviation from the groomed spec (deliberate, per the human's hard constraints)**
- The spec said "mirror **every** key of the env file"; the implementation mirrors **the keys that are
  `Settings` fields**, minus `DECODE_ENV`. Rationale: the bucket source hydrates only known fields
  (`config/settings.py`), so a non-field key in the bucket is unreadable by anything; and `DECODE_ENV` inside a
  bucket *named* by the env is a footgun. This is also where ADR-0015 §9 lands (`DECODE_LOG_FILE`,
  `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` are process-env-only operator vars, not part of the surface). Skipped
  keys are reported by name.

**Acceptance criteria** — all five verified; see the boxes above.

**Evidence**
```
$ make sync-secrets
Usage: make sync-secrets ENV=dev|staging|prod   (one-way: .env -> the decode-<ENV> bucket)
make: *** [sync-secrets] Error 1

$ uv run python scripts/sync_secrets.py --env dev --env-file <scratch>.env --yes   # stub kitaru on PATH
Mirroring …/e2e.env → decode-dev (key names only; values are never printed).
Skipped (not a Settings field): MODAL_TOKEN_ID
  + GEMINI_API_KEY
  + GEMINI_MODEL
This REPLACES the entire contents of decode-dev with these 2 key(s) — `kitaru secrets set` overwrites the whole key set.
Mirrored 2 key(s) into decode-dev.
# argv the stub received: secrets set decode-dev --private --GEMINI_API_KEY=… --GEMINI_MODEL=…  (ONE call)

$ … --env dev … --yes            # stub exits 2 with the argv echoed in stderr
Error: `kitaru secrets set decode-dev` failed (exit 2):
usage error near --GEMINI_API_KEY=***          # redacted

$ make ci
1636 passed in 538.82s
```

**Notes**
- Mutation-checked the names-only guard: making `format_diff` append the value fails 3 tests, so the property is
  actually defended, not incidentally green.
- A stray `decode-dev` secret exists in the local Kitaru store from an e2e run (fake values); the follow-up
  `kitaru secrets delete decode-dev` was permission-denied — the human should remove it.
