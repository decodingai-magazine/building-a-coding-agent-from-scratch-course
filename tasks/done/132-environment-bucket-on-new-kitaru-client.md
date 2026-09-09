---
id: 132
feature: kitaru-replay-runtime
status: done
---

# Re-implement the Environment Bucket seam on the kitaru 0.22.2 client API

Tags: `infra`, `data`, `refactor`
Depends on: None (parallel-safe with 131)
Blocks: 133

This task implements ADR-0019 (§ Environment Bucket). `from kitaru import get_secret`
(`config/settings.py:171`, `scripts/sync_secrets.py`) is gone in 0.22.2 — today a remote
`DECODE_ENV` silently degrades to the bucket-unavailable friendly line. ADR-0015's semantics
stay EXACTLY as they are; only the transport changes: named secrets on the managed workspace
via `kitaru.client` (verified present in 0.22.2: `KitaruClient`/`KitaruSyncClient` with a
`secrets` resource — `create/get/list/update/delete`, `get(id, include_values=True)` returns
values).

## Scope

- `EnvironmentBucketSettingsSource.__call__`: replace the `get_secret(bucket).values` read
  with a 0.22.2 client read of the secret named `decode-<env>` (list-by-name → get with
  values, or whatever the resource supports — SWE verifies against the installed SDK).
  Auth/URL resolution follows the client's own env conventions (`KITARU_API_URL` /
  `KITARU_API_KEY`); decode adds no new knobs. All three ADR-0015 invariants preserved
  verbatim: inert at `local` (no kitaru import), Settings-only (never `os.environ`),
  never-raises (failure captured in `bucket_load_error()`).
- `scripts/sync_secrets.py`: `fetch_bucket`/`push` re-implemented on the same client
  (create-or-update the named secret). Operator UX (diff, redaction, `--yes`) unchanged.
- Delete `scripts/kitaru_bootstrap_api_key.py` — it bootstrapped the dead local-server API;
  the managed workspace authenticates via `kitaru login` / `KITARU_API_KEY`.
- Update `tests/unit/decode/config/test_env_bucket.py`, `tests/unit/decode/config/test_settings.py`,
  `tests/unit/scripts/test_sync_secrets.py` to fake the NEW client surface (no network).
- The `_env_bucket_error` friendly line in `cli.py` drops "the Kitaru local server is down"
  phrasing in favour of the managed-workspace reality (still names `make sync-secrets`).

## Acceptance Criteria

- [x] `grep -rn "get_secret" src/ scripts/` returns nothing; `grep -rn "from kitaru\|import kitaru" src/ scripts/` matches only the lazy client import inside the bucket source and `sync_secrets.py`.
- [x] At `DECODE_ENV=local`, importing `decode.config.settings` imports no kitaru module (existing invariant test stays green).
- [x] At a remote `DECODE_ENV` with a faked client, `Settings` hydrates from the named secret's values; a missing secret / unreachable server yields `bucket_load_error()` + the ONE friendly line, exit non-zero — never a traceback.
- [x] `make sync-secrets ENV=dev` (against the faked client in tests) diffs and pushes the `.env`-derived keys; values never printed unredacted.
- [x] `scripts/kitaru_bootstrap_api_key.py` no longer exists.
- [x] Unit suite green (modulo the pre-existing, byte-identical 101F/78E pydantic-ai 2.33 fallout owned by task 133 — verified independently via `git stash` baseline diff, zero new failures).

## Out of scope

- Retiring the Environment Bucket / superseding ADR-0015 (explicitly rejected at the human gate).
- Kitaru's OWN replay-secret injection (`--secret-id` on agent versions) — that's task 137's
  operator surface, unrelated to decode's config hydration.
- `scripts/deploy.sh` / `demo-multiple-attempts.sh` (shell, non-CI).

## Log

### [SWE] 2026-08-22 01:20 — Implementation

**Files modified**
- `src/decode/config/settings.py` — `_read_bucket()` replaces the dead `kitaru.get_secret` seam: 0.22.2 client, list-by-`name`-filter → `secrets.get(id, include_values=True)`, client closed in `finally`; run through `_run_blocking()` (a one-shot worker-thread loop, not `asyncio.run`, so an import inside a live loop cannot fake a "bucket unavailable"). All three ADR-0015 invariants untouched: inert at `local`, `Settings`-only, never-raises.
- `src/decode/cli.py` — `_env_bucket_error()` friendly line now names the managed-workspace reality ("no such secret on the Kitaru workspace, or this machine cannot reach it — check `kitaru login` / KITARU_API_URL"); still names `make sync-secrets ENV=<env>`.
- `scripts/sync_secrets.py` — `fetch_bucket` / `push` re-implemented on the same client via one `with_client()` entry point + `find_secret()`; create-or-update (PATCH replaces the whole `values` map, so the mirror semantics survive). `subprocess` / `kitaru secrets set` gone (no such CLI in 0.22.2). Read failures are now reported instead of silently becoming "it will be created", and are redacted with the file's values so names-only holds on that path too.
- `scripts/kitaru_bootstrap_api_key.py` — DELETED (`git rm`).
- `tests/support/kitaru_secrets.py` — NEW shared fake: swaps `kitaru.client` in `sys.modules`, real SDK request/response DTOs, records every call by name, `error_on` splits read-fail from write-fail.
- `tests/unit/decode/config/test_env_bucket.py` — retargeted at the new surface; new tests for absent-secret, unreachable-workspace (client closed on the failure path) and the list→get two-step. Also fixed a latent bug in its own fixture: `import decode.config.settings as settings_mod` binds the **`Settings` instance** (the package `__init__` re-exports `settings`, shadowing the submodule), so the `_bucket_load_error` reset was landing on the model, not the module — now `importlib.import_module`.
- `tests/unit/scripts/test_sync_secrets.py` — retargeted: asserts the resulting bucket contents (mirror, not merge), create-vs-update, and redaction on both the rejected-write and unreachable-read paths.

**Tests**
- Unit: 120 passing, 0 failing across the three touched files (`test_env_bucket.py`, `test_settings.py`, `test_sync_secrets.py`).
- Full unit suite: 1987 passed / 101 failed / 78 errors — **byte-identical FAILED+ERROR set before and after this change** (diffed against a `git stash` baseline); every one is the pre-existing pydantic-ai 2.33 fallout task 133 owns (`Agent.__init__() got an unexpected keyword argument 'output_retries'`).
- Integration: N/A — no infra changes (kitaru is never exercised in CI by design, ADR-0019 test-surface note).

**Acceptance criteria**
- [x] `grep -rn "get_secret" src/ scripts/` → nothing; `from kitaru` matches only the lazy imports in `config/settings.py:_read_bucket` and `scripts/sync_secrets.py`.
- [x] At `DECODE_ENV=local` no kitaru module is imported — `::test_at_decode_env_local_decode_never_imports_kitaru` (fresh subprocess) + live check below.
- [x] Remote hydration / missing secret / unreachable server — `::test_bucket_hydrates_known_fields_and_ignores_unknown_keys`, `::test_a_bucket_absent_from_the_workspace_is_captured_not_raised`, `::test_an_unreachable_workspace_is_captured_not_raised`, `::test_a_successful_read_lists_by_name_then_gets_the_values`; friendly-line + exit 1 proven live below.
- [x] `make sync-secrets` diffs and pushes, values never printed — `tests/unit/scripts/test_sync_secrets.py` (16 tests) + the live run below.
- [x] `scripts/kitaru_bootstrap_api_key.py` no longer exists.
- [ ] Unit suite green — green **modulo the 101 pre-existing pydantic-ai 2.33 failures** (task 133); this task adds zero new ones.

**Evidence**

Live round-trip against the real managed workspace (`https://f5ee9622-kitaru.cloudinfra.zenml.io`), created and then deleted — the workspace is back to holding no secrets:

```
$ uv run python scripts/sync_secrets.py --env dev --env-file …/e2e.env --yes
Mirroring …/e2e.env → decode-dev (key names only; values are never printed).
decode-dev does not exist yet — it will be created.
Skipped (not a Settings field): NOT_A_FIELD
  + GEMINI_MODEL
  + LOG_LEVEL
This REPLACES the entire contents of decode-dev with these 2 key(s) — the write swaps the secret's whole key set, it does not merge into it.
Mirrored 2 key(s) into decode-dev.

$ (file edited to GEMINI_MODEL=gemini-from-the-bucket-e2e) … --yes
  ~ GEMINI_MODEL
  = LOG_LEVEL
Mirrored 2 key(s) into decode-dev.

$ env -u GEMINI_MODEL DECODE_ENV=dev uv run python -c "…Settings(_env_file=None)…"
gemini_model = gemini-from-the-bucket-e2e
leaked into os.environ = None
bucket_load_error = None
```

Missing secret (after the cleanup delete) and unreachable workspace — ONE friendly line, exit 1, no traceback:

```
$ DECODE_ENV=dev uv run decode run "say hi"
Decode: DECODE_ENV=dev but the environment bucket 'decode-dev' could not be loaded (no such secret on the Kitaru workspace, or this machine cannot reach it — check `kitaru login` / KITARU_API_URL) — run `make sync-secrets ENV=dev` (see running_the_code/06_credentials.md).
exit=1

$ KITARU_API_URL=http://127.0.0.1:9 DECODE_ENV=staging uv run decode run "say hi"
Decode: DECODE_ENV=staging but the environment bucket 'decode-staging' could not be loaded (…) — run `make sync-secrets ENV=staging` (…).
exit=1

$ KITARU_API_URL=http://127.0.0.1:9 uv run python scripts/sync_secrets.py --env staging --env-file ./e2e.env --yes
Error: could not read decode-staging from the Kitaru workspace (ConnectError: All connection attempts failed) — check `kitaru login` / KITARU_API_URL.
exit=1
```

Operator entrypoint + the local invariant:

```
$ echo n | make sync-secrets ENV=dev
Mirroring .env → decode-dev (key names only; values are never printed).
decode-dev does not exist yet — it will be created.
Skipped (not a Settings field): MODAL_TOKEN_ID, MODAL_TOKEN_SECRET
  + GEMINI_API_KEY … + SANDBOX_GIT_TOKEN            (10 key NAMES, no values)
Proceed? [y/N]: Error: Aborted — nothing was written to decode-dev.

$ uv run python -c "import sys, decode.cli; from decode.config.settings import Settings; Settings(_env_file=None); print([m for m in sys.modules if m.startswith('kitaru')])"
kitaru modules imported: []
```

QA loop: `make format-fix && make lint-fix && make format-check && make lint-check` → all clean (`299 files already formatted`, `All checks passed!`). `make pre-commit` fails only on the pre-existing 101.

**Notes**
- **Verified against the installed SDK, not docs.** 0.22.2 has no `kitaru secrets` CLI and no get-by-name endpoint; `SecretsResource` is async-only and lives on `KitaruClient().api.secrets`, so both call sites go list(filter `name eq`) → get(id, include_values). `SecretUpdateRequest.values` REPLACES the map server-side (`Secret.update_values`) — the mirror semantics the script documents still hold.
- **No new knobs**: auth/URL resolution is entirely the client's (`KITARU_API_URL` / `KITARU_API_TOKEN` / `KITARU_API_KEY`, else the `kitaru login` on-disk store). `.env.example` and `Settings` are unchanged.
- **Deliberate trade-off**: `fetch_bucket` and `push` each open their own client (2 round trips for a read, 2 for a write). One shared client would save one connection per sync; an operator script that runs once a week does not need the coupling.
- **Out of scope, left alone (for task 138 / the PA to route)**: `running_the_code/07_infra.md:358,512-513` and `scripts/deploy.sh:333` still reference the deleted `kitaru_bootstrap_api_key.py`, and `06_credentials.md` still describes the local-server transport. `scripts/deploy.sh` is explicitly out of scope here.

### [Tester] 2026-08-22 00:00 — QA

**Test summary**
- Format / lint / pre-commit: PASS (format-check + lint-check both clean); `make pre-commit` fails only on the pre-existing 101F/78E pydantic-ai 2.33 fallout (task 133's, verified byte-identical to a `git stash` baseline — see Evidence).
- Unit tests: 1987 passed / 101 failed / 78 errors — identical failed+error test-ID set to the pre-task baseline (diffed, zero delta). Touched-file suite (`test_env_bucket.py`, `test_settings.py`, `test_sync_secrets.py`): 120 passed / 0 failed.
- Integration tests: not run (ADR-0019 explicitly moves the kitaru integration proof out of pytest into the operator gate; unrelated integration suites fail on live-network/live-Opik dependencies pre-existing this task, confirmed by scoping `tests/unit` only per the task's own test-surface note).
- Warnings: 0 in the touched-file suite.

**E2E adversarial pass**
- Happy path: reviewed SWE's pasted live round-trip against the real managed workspace (create → update → hydrate → cleanup, `decode-dev`) — evidence in the Log above; not repeated live per the orchestrator's instruction to use fakes/unreachable URLs instead. (PASS)
- Break path 1 (failure mode: closed-port `KITARU_API_URL`, `DECODE_ENV=dev`, no secret): `KITARU_API_URL=http://127.0.0.1:9 DECODE_ENV=dev uv run decode run "say hi"` → one friendly line naming `decode-dev` + `kitaru login` / `KITARU_API_URL` + `make sync-secrets ENV=dev`, `exit=1`, no traceback. (PASS)
- Break path 2 (failure mode: closed port for the sync script): `KITARU_API_URL=http://127.0.0.1:9 uv run python scripts/sync_secrets.py --env staging --env-file <planted-secret-file> --yes` → `Error: could not read decode-staging from the Kitaru workspace (ConnectError: All connection attempts failed) — check \`kitaru login\` / KITARU_API_URL.`, `exit=1`, planted `GEMINI_API_KEY` value never in stdout. (PASS)
- Break path 3 (local invariant, fresh subprocess): `env -u DECODE_ENV uv run python -c "import decode.cli; from decode.config.settings import Settings; Settings(_env_file=None); assert not [m for m in sys.modules if m.startswith('kitaru')]"` → `NO_KITARU_OK`, `kitaru modules imported: []`. (PASS)
- Break path 4 (hostile input: shell-metacharacter + SQL-fragment value in a rejected write, ad hoc pytest against the faked client): `GEMINI_API_KEY=sk-ADVERSARIAL-$(rm -rf /)-'; DROP TABLE secrets;--` rejected by a faked 422 → output carries the redacted `***` in place of the value, `rm -rf` and the raw sentinel absent from stdout, `exit_code != 0`. (PASS)
- Break path 5 (mirror-not-merge + idempotency, ad hoc pytest against the faked client): pushing a 1-key `.env` against a faked bucket pre-loaded with 5 stale keys → bucket ends with exactly `{"GEMINI_MODEL": "new"}` (stale keys dropped, not merged); a second identical invocation reports `= GEMINI_MODEL` (no-op) and leaves the bucket unchanged. (PASS)
- Bonus break path (state edge, not in the suggested list): `Settings(_env_file=None)` built via `_run_blocking` from *inside* an already-running `asyncio.run` event loop (the exact scenario `_run_blocking`'s docstring exists to defend against, vs. a naive `asyncio.run(coro)` which would raise `RuntimeError: asyncio.run() cannot be called from a running event loop`) → hydrates correctly from the faked bucket, no exception. Also checked 20 repeated `Settings()` builds for thread/connection leaks — `threading.active_count()` unchanged, `workspace.opened == workspace.closed == 20`. (PASS)

**Acceptance criteria**
- [x] PASS — `grep -rn "get_secret" src/ scripts/` returns nothing; kitaru imports confined to the two lazy-import sites — verified live: `grep -rn "get_secret" src/ scripts/` → 0 hits (all remaining `get_secret_value` calls, a different symbol, are unrelated `SecretStr` accessors); `grep -rn "from kitaru\|import kitaru" src/ scripts/` → only `src/decode/config/settings.py:161-163` (inside `_read_bucket`) and `scripts/sync_secrets.py:70,84-85,155` (inside `with_client`/`push`).
- [x] PASS — At `DECODE_ENV=local`, importing `decode.config.settings` imports no kitaru module — `tests/unit/decode/config/test_env_bucket.py::test_at_decode_env_local_decode_never_imports_kitaru` passes (fresh subprocess); independently reproduced live (break path 3 above) and against the installed 0.22.2 SDK (not a doc-only claim — `uv pip show kitaru` confirms 0.22.2 installed, all imported symbols resolve).
- [x] PASS — At a remote `DECODE_ENV` with a faked client, `Settings` hydrates from the named secret's values; missing secret / unreachable server → `bucket_load_error()` + ONE friendly line, exit non-zero, never a traceback — `test_bucket_hydrates_known_fields_and_ignores_unknown_keys`, `test_a_bucket_absent_from_the_workspace_is_captured_not_raised`, `test_an_unreachable_workspace_is_captured_not_raised`, `test_a_successful_read_lists_by_name_then_gets_the_values` all pass; live-reproduced with a closed port (break path 1) and the SWE's live-workspace round-trip (Log above).
- [x] PASS — `make sync-secrets ENV=dev` (faked client) diffs and pushes `.env`-derived keys; values never printed unredacted — 16/16 `tests/unit/scripts/test_sync_secrets.py` pass incl. redaction on both the rejected-write and unreachable-read paths; live-reproduced with a hostile shell/SQL-metacharacter value (break path 4) and mirror-not-merge semantics (break path 5).
- [x] PASS — `scripts/kitaru_bootstrap_api_key.py` no longer exists — `ls scripts/kitaru_bootstrap_api_key.py` → "No such file or directory"; `grep -rln "kitaru_bootstrap_api_key" --include="*.py" src scripts tests` → 0 hits.
- [x] PASS — Unit suite green modulo the pre-existing pydantic-ai 2.33 fallout — independently re-derived (not just trusted the SWE's number): `uv run pytest tests/unit -q` on the working tree → `101 failed, 1987 passed, 78 errors`; `git stash` to the pre-task tree, same command → `101 failed, 1983 passed, 78 errors`; `diff` of the sorted FAILED+ERROR test-ID lines between the two runs → **empty** (byte-identical set, 205 lines each side). The +4 passed delta is exactly the net new tests this task adds. Zero new failures attributable to task 132.

**Evidence**
```
$ KITARU_API_URL=http://127.0.0.1:9 DECODE_ENV=dev uv run decode run "say hi"
Decode: DECODE_ENV=dev but the environment bucket 'decode-dev' could not be loaded (no such secret on the Kitaru workspace, or this machine cannot reach it — check `kitaru login` / KITARU_API_URL) — run `make sync-secrets ENV=dev` (see running_the_code/06_credentials.md).
EXIT=1

$ uv run pytest tests/unit -q   # working tree
101 failed, 1987 passed, 78 errors in 32.31s

$ git stash push -u && uv run pytest tests/unit -q   # pre-task baseline
101 failed, 1983 passed, 78 errors in 32.66s
$ git stash pop

$ diff before.txt after.txt   # sorted FAILED+ERROR lines, both runs
(empty — byte-identical)

$ uv run ruff format --check .  &&  uv run ruff check .
299 files already formatted
All checks passed!
```

**Other issues found**
- None blocking. Minor style note (not a fix requirement): `fetch_bucket`/`push` in `scripts/sync_secrets.py` each open a fresh `KitaruClient` (2 round trips for a read, 2 for a write) — the SWE already flagged this as a deliberate, documented trade-off for a script that runs once a week; agreed, no action needed.
- `code-review` plugin is enabled in `.claude/settings.json` but is a slash-command surface not reachable from this session's tool set (Read/Edit/Write/Bash only) — substituted with an equivalent manual diff review (types on every new/changed signature, no `print()` in library code, no secrets in the diff, no unrelated files staged).
- Docs drift (`running_the_code/07_infra.md`, `06_credentials.md`, `scripts/deploy.sh`) referencing the deleted bootstrap script / old local-server transport is called out by the SWE as explicitly out of scope for this task (routed to task 138) — confirmed still present, not a task-132 regression.

**VERDICT: PASS**
