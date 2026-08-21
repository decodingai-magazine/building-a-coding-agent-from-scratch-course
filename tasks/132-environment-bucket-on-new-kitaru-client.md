---
id: 132
feature: kitaru-replay-runtime
status: pending
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

- [ ] `grep -rn "get_secret" src/ scripts/` returns nothing; `grep -rn "from kitaru\|import kitaru" src/ scripts/` matches only the lazy client import inside the bucket source and `sync_secrets.py`.
- [ ] At `DECODE_ENV=local`, importing `decode.config.settings` imports no kitaru module (existing invariant test stays green).
- [ ] At a remote `DECODE_ENV` with a faked client, `Settings` hydrates from the named secret's values; a missing secret / unreachable server yields `bucket_load_error()` + the ONE friendly line, exit non-zero — never a traceback.
- [ ] `make sync-secrets ENV=dev` (against the faked client in tests) diffs and pushes the `.env`-derived keys; values never printed unredacted.
- [ ] `scripts/kitaru_bootstrap_api_key.py` no longer exists.
- [ ] Unit suite green.

## Out of scope

- Retiring the Environment Bucket / superseding ADR-0015 (explicitly rejected at the human gate).
- Kitaru's OWN replay-secret injection (`--secret-id` on agent versions) — that's task 137's
  operator surface, unrelated to decode's config hydration.
- `scripts/deploy.sh` / `demo-multiple-attempts.sh` (shell, non-CI).

## Log
