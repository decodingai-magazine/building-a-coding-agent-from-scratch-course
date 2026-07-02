---
id: 071-sandbox-settings-guard-glossary
feature: sandboxing
status: pending
---

# Sandbox settings, startup guard, .env.example mirror, glossary + ADR-0011 verification

Tags: `data`, `infra`, `docs`
Depends on: None
Blocks: #072, #073, #074, #075, #076, #077

This task implements ADR-0011 (Sandboxing + Credential Proxy). It lands the **configuration surface**
and the **backend-unavailable startup guard** ahead of any executor, exactly like task 057 (runtime),
050 (LSP), and 041 (compaction) added settings before their consumers. It stays independently
shippable: `SANDBOX_MODE` defaults to `none`, so the REPL, every existing `.env`, and every test are
byte-unchanged. ADR-0011 and the glossary rows (updated **Sandbox** + **Credential Proxy**; new
**Sandbox Mode** / **Worker** / **Proxy Rule**) are applied to `docs/` at the plan gate; this task
**verifies** they are present and used.

## Scope

- **Settings** (add to `Settings` in `config/settings.py`, defaults safe for tests, all
  type-annotated, `Literal`/`Field` as the file requires) under a new
  `# --- Sandboxing (ADR-0011) ---` block. No executor readers yet (they land in 072-075):
  - `sandbox_mode: Literal["none", "docker", "modal"] = "none"` — selects the `CommandExecutor` the
    `bash` seam uses. `none` = today's `LocalExecutor` (byte-unchanged). Read by 074's selection seam
    and the guard below.
  - `sandbox_image: str = "python:3.12-slim"` — the worker image: docker pulls it directly; modal maps
    it via `modal.Image.from_registry(...)`. Must include `bash` (default does). Read by 072/073.
  - `sandbox_timeout_s: float = Field(600.0, gt=0)` — max lifetime of a **remote** (Modal) sandbox
    before Modal reaps it; docker's session container has no lifetime cap (uses `sleep infinity`). Read
    by 073.
  - `sandbox_credential_proxy_enabled: bool = False` — enable the **headless + docker-only** Credential
    Proxy (ADR-0011 §6). Default `False`. Read by 075.
  - `sandbox_proxy_image: str = "mitmproxy/mitmproxy"` — the mitmproxy addon container image. Read by
    075.
- **Startup guard** in `cli.py` (mirror the task-004 `_provider_config_error` pattern): a
  `_sandbox_config_error() -> str | None` that returns one friendly line when the chosen backend is
  unavailable, else `None`. Checks **presence/reachability only, never correctness**:
  - `none` → always `None` (no-op; the default path is untouched).
  - `docker` → a **fast** daemon-reachability probe (`docker info` / `docker version`, bounded by a
    short timeout; missing binary / non-zero exit / timeout ⇒ unavailable) → e.g. `"Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable — start Docker and retry (see .env.example)."`
  - `modal` → **credential presence** without a network call or a heavy import: `MODAL_TOKEN_ID` +
    `MODAL_TOKEN_SECRET` in env, or `~/.modal.toml` present → else e.g. `"Decode: SANDBOX_MODE=modal but Modal credentials are missing — run \`modal token set …\` (see .env.example)."`
  - Wire it into (a) the REPL startup chain in `cli()` (alongside the provider-config guard) and (b)
    `_runtime_config_preflight()` (so `decode run`/`decode replay` refuse the same way before building a
    flow). Echo to stderr, `raise click.exceptions.Exit(1)`.
- **`.env.example`:** add a `# --- Sandboxing ---` block mirroring all five vars (commented,
  explain-the-default voice matching the LSP/runtime blocks), noting: `none` is the default (zero
  behavior change); docker = a local session container over the bind-mounted repo; modal = a remote
  empty-scratch sandbox with **no** local tree; the credential proxy is headless + docker only and
  opt-in.
- **Glossary/ADR verification** (`docs/glossary.md`, `docs/adr/0011-...md`): verify the updated
  **Sandbox** + **Credential Proxy** rows and the new **Sandbox Mode** / **Worker** / **Proxy Rule**
  rows are present in the table format and cross-reference correctly; verify ADR-0011 is `Accepted`.
  This task authors **no** ADR/glossary content (done at the plan gate) — it only asserts presence and
  that no non-canonical synonym ("container mode", "shell jail") is introduced.

## Acceptance criteria

- [ ] `Settings` exposes `sandbox_mode` (`"none"`), `sandbox_image` (`"python:3.12-slim"`),
  `sandbox_timeout_s` (`600.0`), `sandbox_credential_proxy_enabled` (`False`), `sandbox_proxy_image`
  (`"mitmproxy/mitmproxy"`) with the exact names/types/defaults; a unit test asserts every default.
- [ ] `sandbox_mode` rejects a value outside `{none,docker,modal}` (Literal validation, fails at load);
  `sandbox_timeout_s` rejects `0`/negative (`Field(gt=0)`); both unit-tested.
- [ ] Each var is env-overridable (`SANDBOX_MODE=docker`, etc.); a unit test sets them via
  env/`monkeypatch` and asserts the parsed values.
- [ ] `.env.example` lists all five vars under a Sandboxing block; the existing env/settings drift test
  confirms every new setting has a matching `.env.example` line.
- [ ] `SANDBOX_MODE=none` (default): `_sandbox_config_error()` returns `None` and the REPL + `decode run`
  start exactly as before — a test asserts no guard fires and no docker/modal probe runs.
- [ ] `SANDBOX_MODE=docker` with the daemon unreachable (probe patched to fail): both the REPL start and
  `decode run` exit non-zero with the friendly docker line on stderr — **no traceback**; a test asserts it
  (`docker info` probe patched, not a real daemon).
- [ ] `SANDBOX_MODE=modal` with no `MODAL_TOKEN_*` env and no `~/.modal.toml` (patched): both paths exit
  non-zero with the friendly modal line; a test asserts it without importing `modal` or hitting the network.
- [ ] The guard checks **presence only** — a *present but wrong* value is NOT rejected here (matches the
  provider-key guards); a test with a reachable-but-fake docker probe passes the guard.
- [ ] `docs/glossary.md` carries the updated Sandbox + Credential Proxy rows and the new Sandbox Mode /
  Worker / Proxy Rule rows; ADR-0011 is `Accepted`; a grep confirms no non-canonical synonym. Verified in log.
- [ ] `make ci` green, 0 warnings (`filterwarnings=["error"]`); `uv lock --check` passes (no dep change).

## Out of scope

- Any executor implementation (docker 072, modal 073) and the selection seam (074).
- The credential proxy readers of `sandbox_credential_proxy_enabled` / `sandbox_proxy_image` (075).
- Authoring ADR-0011 / glossary content (plan gate). This task verifies only.

## Log
