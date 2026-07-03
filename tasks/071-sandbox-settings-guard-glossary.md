---
id: 071-sandbox-settings-guard-glossary
feature: sandboxing
status: done
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

- [x] `Settings` exposes `sandbox_mode` (`"none"`), `sandbox_image` (`"python:3.12-slim"`),
  `sandbox_timeout_s` (`600.0`), `sandbox_credential_proxy_enabled` (`False`), `sandbox_proxy_image`
  (`"mitmproxy/mitmproxy"`) with the exact names/types/defaults; a unit test asserts every default.
- [x] `sandbox_mode` rejects a value outside `{none,docker,modal}` (Literal validation, fails at load);
  `sandbox_timeout_s` rejects `0`/negative (`Field(gt=0)`); both unit-tested.
- [x] Each var is env-overridable (`SANDBOX_MODE=docker`, etc.); a unit test sets them via
  env/`monkeypatch` and asserts the parsed values.
- [x] `.env.example` lists all five vars under a Sandboxing block; the existing env/settings drift test
  confirms every new setting has a matching `.env.example` line.
- [x] `SANDBOX_MODE=none` (default): `_sandbox_config_error()` returns `None` and the REPL + `decode run`
  start exactly as before — a test asserts no guard fires and no docker/modal probe runs.
- [x] `SANDBOX_MODE=docker` with the daemon unreachable (probe patched to fail): both the REPL start and
  `decode run` exit non-zero with the friendly docker line on stderr — **no traceback**; a test asserts it
  (`docker info` probe patched, not a real daemon).
- [x] `SANDBOX_MODE=modal` with no `MODAL_TOKEN_*` env and no `~/.modal.toml` (patched): both paths exit
  non-zero with the friendly modal line; a test asserts it without importing `modal` or hitting the network.
- [x] The guard checks **presence only** — a *present but wrong* value is NOT rejected here (matches the
  provider-key guards); a test with a reachable-but-fake docker probe passes the guard.
- [x] `docs/glossary.md` carries the updated Sandbox + Credential Proxy rows and the new Sandbox Mode /
  Worker / Proxy Rule rows; ADR-0011 is `Accepted`; a grep confirms no non-canonical synonym. Verified in log.
- [x] `make ci` green, 0 warnings (`filterwarnings=["error"]`); `uv lock --check` passes (no dep change).

## Out of scope

- Any executor implementation (docker 072, modal 073) and the selection seam (074).
- The credential proxy readers of `sandbox_credential_proxy_enabled` / `sandbox_proxy_image` (075).
- Authoring ADR-0011 / glossary content (plan gate). This task verifies only.

## Log

### [SWE] 2026-07-02 — Implementation

**Files modified**
- `src/decode/config/settings.py` — added the five `# --- Sandboxing (ADR-0011) ---` fields
  (`sandbox_mode` Literal, `sandbox_image`, `sandbox_timeout_s` `Field(gt=0)`,
  `sandbox_credential_proxy_enabled`, `sandbox_proxy_image`). No executor readers (they land in 072-075).
- `src/decode/cli.py` — added `_sandbox_config_error() -> str | None` (mirrors `_provider_config_error`)
  plus two patchable probe helpers `_docker_daemon_reachable()` (fast `docker info` via `subprocess`,
  bounded by `_DOCKER_PROBE_TIMEOUT_S=5.0`; missing binary / non-zero / timeout ⇒ unreachable) and
  `_modal_credentials_present()` (env `MODAL_TOKEN_ID`+`MODAL_TOKEN_SECRET` or `~/.modal.toml`; no
  network, no `modal` import). Wired into (a) the REPL `cli()` chain right after the provider guard and
  (b) `_runtime_config_preflight()` as step 3 (after `RUNTIME_ENABLED`, before the kitaru-backed
  pre-flights). Two friendly stderr messages; `none` runs no probe.
- `.env.example` — added a `# --- Sandboxing ---` block mirroring all five vars (commented,
  explain-the-default voice: none=zero-change default, docker=local bind-mounted session container,
  modal=remote empty-scratch, proxy=headless+docker-only opt-in).
- `tests/conftest.py` — new autouse `_default_sandbox_mode` fixture pins `sandbox_mode="none"` on the
  singleton suite-wide (hermeticity: a developer's exported `SANDBOX_MODE` must not trip the new guard
  in unrelated cli tests — same intent as the existing `_no_real_provider_key` guard).
- `tests/unit/decode/config/test_settings.py` — `_SANDBOX_ENV_VARS` + 8 tests (defaults, env & dotenv
  override, each valid Literal, Literal rejection, `gt=0` rejection, `.env.example` drift guard).
- `tests/unit/decode/test_cli.py` — 18 tests: the `_sandbox_config_error` contract for all three modes,
  the docker probe (zero/non-zero/missing-binary/timeout), the modal probe (env pair / toml / absent /
  half-pair), the REPL guard (docker & modal friendly line + non-zero + no traceback + `run_app` not
  awaited), the none/reachable pass-through, and provider-guard-precedes-sandbox ordering.
- `tests/unit/decode/runtime/test_run_command.py` — 3 tests: `decode run` docker-unreachable &
  modal-missing friendly line + no flow built, and none-mode runs no probe + runs the flow.
- `tests/unit/decode/runtime/test_replay_command.py` — 1 test: `decode replay` shares the pre-flight, so
  docker-unreachable exits friendly before any kitaru boundary is touched.

**Tests**
- Unit: `make pre-commit` → 1086 passing, 0 failing. New sandbox-focused subset: 32 passing.
- Full gate `make ci` → 1107 passing (unit + integration), 0 warnings under `filterwarnings=["error"]`;
  `uv lock --check` clean (no dep change).

**Acceptance criteria** — all 10 verified by tests (see checkboxes above); no `[HUMAN]` items. Key mappings:
- defaults/types → `test_settings.py::test_sandbox_defaults`; Literal/`gt=0` rejection →
  `test_sandbox_mode_rejects_unknown_value` / `test_rejects_a_non_positive_sandbox_timeout`.
- guard behaviors (probes PATCHED) → `test_cli.py::test_cli_sandbox_docker_unreachable_*` /
  `test_cli_sandbox_modal_missing_creds_*`; headless →
  `test_run_command.py::test_run_sandbox_docker_unreachable_*` (+ modal, none) and
  `test_replay_command.py::test_replay_sandbox_docker_unreachable_*`.
- presence-only → `test_sandbox_config_error_docker_reachable_returns_none` (+ modal-present).

**Glossary / ADR verification** (this task authors none — plan-gate commit `864c446`):
- `docs/adr/0011-sandboxing-and-credential-proxy.md` → `**Status:** Accepted` (line 3). Verified.
- `docs/glossary.md` carries all five canonical rows — **Sandbox** (updated), **Credential Proxy**
  (updated), **Sandbox Mode** / **Worker** / **Proxy Rule** (new). Verified by
  `grep -cE '^\| \*\*(Sandbox|Credential Proxy|Sandbox Mode|Worker|Proxy Rule)\*\*' docs/glossary.md` → 5.
- No non-canonical synonym: `grep -rniE 'container mode|shell jail' docs/ src/` → none found.

**Evidence**
```
$ uv run pytest tests/unit/decode/config/test_settings.py -k sandbox \
    tests/unit/decode/test_cli.py -k "sandbox or docker_daemon or modal_credentials" -q
28 passed, 85 deselected in 1.44s

$ uv run pytest .../runtime/test_run_command.py .../runtime/test_replay_command.py -k sandbox -q
4 passed, 33 deselected in 4.33s

$ make ci
======================= 1107 passed in 122.63s (0:02:02) =======================
$ uv lock --check
Resolved 149 packages in 2ms

# e2e (real CLI, probes forced via env — no test doubles):
$ GEMINI_API_KEY=dummy SANDBOX_MODE=docker DOCKER_HOST=unix:///nonexistent/docker.sock decode
Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable — start Docker and retry (see .env.example).
  → exit=1  (same line + exit=1 for `decode run "…"`)
$ env -u MODAL_TOKEN_ID -u MODAL_TOKEN_SECRET GEMINI_API_KEY=dummy SANDBOX_MODE=modal HOME=<empty> decode
Decode: SANDBOX_MODE=modal but Modal credentials are missing — run `modal token set …` (see .env.example).
  → exit=1
# SANDBOX_MODE=none (default) and SANDBOX_MODE=modal with a present token pair both pass the guard
# and reach the REPL banner (guard is a no-op / passes).
```

**Notes**
- Guard placement: REPL chain = right after the provider-config guard (before `--agent`/`--mode`);
  headless pre-flight = after `RUNTIME_ENABLED`, before the kitaru-backed pre-flights. Rationale: a
  disabled runtime short-circuits first; the sandbox guard is kitaru-free so it never risks importing
  kitaru on the REPL path (the bare-`decode`-is-kitaru-free invariant holds — subprocess import tests
  still green).
- `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` are read straight from `os.environ` in the guard (not via
  `Settings`) on purpose: they are the modal CLI's own account-token contract, distinct from decode's
  endpoint/proxy-token settings — and reading them at the entrypoint layer is the right place.
- No new deps; `docker`/`modal` are never imported by the guard (CLI shell-out for docker; presence
  check only for modal), matching ADR-0011's CLI-over-SDK choice and the presence-not-correctness rule.
- Out of scope (untouched, per spec): all executors (072/073), the selection seam (074), the proxy
  readers (075). No ADR/glossary content authored (verification only).

### [Tester] 2026-07-03 00:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 145 files ok; `ruff check` all passed)
- Unit tests: 1086 passed / 0 failed
- Integration tests: 21 passed / 0 failed
- Warnings: 0 (`filterwarnings=["error"]` would have errored otherwise)
- `uv lock --check`: clean (149 packages, no dep change) — AC10 ✓

**E2E adversarial pass** (real CLI via `.venv/bin/decode`, no test doubles; docker probe forced via a
fake `docker` on PATH that drops a sentinel + `DOCKER_HOST=unix:///nonexistent/docker.sock`; modal
absence forced via empty `HOME` + stripped `MODAL_TOKEN_*`; provider absence via empty cwd (no `.env`)):
- Happy path (none default): BP6 — `SANDBOX_MODE=none decode run …` passes the sandbox guard, no docker
  probe runs (sentinel absent), builds the Kitaru flow, reaches the model request. PASS.
- BP1 (docker unreachable, REPL): `SANDBOX_MODE=docker DOCKER_HOST=bad decode` → friendly docker line,
  exit 1, 0 tracebacks, no `docker info` spew (capture_output). PASS.
- BP2 (docker unreachable, headless run): `… decode run "list the files"` → same friendly line, exit 1,
  0 tracebacks, no flow built. PASS.
- BP3 (docker unreachable, headless replay): `… decode replay kr-abc123 --from cp` → same friendly line,
  exit 1, 0 tracebacks, before any kitaru boundary. PASS.
- BP4 (guard ordering, hermetic: key TRULY absent + `SANDBOX_MODE=docker`): prints the GEMINI_API_KEY
  provider line, NOT the sandbox line; docker sentinel never written (probe short-circuited). Provider
  guard precedes the sandbox guard in the real CLI. PASS. (First attempt was contaminated by the repo
  `.env` supplying the key — re-run from an empty cwd; behavior confirmed correct.)
- BP5 (modal missing creds, REPL, empty HOME): `SANDBOX_MODE=modal` → "Modal credentials are missing —
  run `modal token set …`", exit 1, 0 tracebacks, instant (no network / no modal import). PASS.
- Boundary (modal HALF-pair: only `MODAL_TOKEN_ID`, no secret) → still "missing", exit 1. PASS.
- Boundary (`SANDBOX_TIMEOUT_S=0`) → fails at load: `sandbox_timeout_s: Input should be greater than 0`
  (`type=greater_than`), exit 1. PASS.
- Boundary (`SANDBOX_MODE=""` empty / `=vm` bogus) → fails at load: `sandbox_mode: Input should be
  'none', 'docker' or 'modal'` (`type=literal_error`), exit 1 — not silently coerced to `none`; identical
  convention to the pre-existing bogus `LLM_PROVIDER`. PASS.
- Hermeticity (conftest leak): with `SANDBOX_MODE=docker` + bad `DOCKER_HOST` exported, the cli + runtime
  + tui subsets (241 tests) all pass — the autouse `_default_sandbox_mode` fixture pins `none`, so a
  developer's exported mode does not trip the guard in unrelated tests. PASS.

**Acceptance criteria**
- [x] PASS — five settings, exact names/types/defaults + defaults test — `settings.py:239-253`;
      `test_settings.py::test_sandbox_defaults`.
- [x] PASS — Literal rejects unknown / `Field(gt=0)` rejects ≤0, at load — `test_sandbox_mode_rejects_unknown_value`,
      `test_rejects_a_non_positive_sandbox_timeout`; real CLI boundary runs (`=vm`, `=""`, timeout `=0`).
- [x] PASS — each var env-overridable — `test_reads_sandbox_vars_from_process_env`,
      `test_loads_sandbox_vars_from_a_dotenv_file`, `test_sandbox_mode_accepts_each_valid_literal`.
- [x] PASS — five vars in `.env.example` + drift test — `.env.example:154-179`;
      `test_env_example_lists_every_sandbox_var`.
- [x] PASS — none returns None + no probe, REPL & run start as before —
      `test_sandbox_config_error_none_returns_none_and_runs_no_probe`,
      `test_cli_sandbox_none_default_starts_the_repl_and_runs_no_probe`,
      `test_run_sandbox_none_default_runs_no_probe_and_runs_the_flow`; real CLI BP6 (sentinel proof).
- [x] PASS — docker unreachable → friendly line, non-zero, no traceback, REPL & run —
      `test_cli_sandbox_docker_unreachable_*`, `test_run_sandbox_docker_unreachable_*`,
      `test_replay_sandbox_docker_unreachable_*`; real CLI BP1/BP2/BP3.
- [x] PASS — modal no creds → friendly line, no network/import, both paths —
      `test_cli_sandbox_modal_missing_creds_*`, `test_run_sandbox_modal_missing_creds_*`; real CLI BP5 +
      half-pair boundary.
- [x] PASS — presence-only (reachable-but-fake passes) —
      `test_sandbox_config_error_docker_reachable_returns_none`,
      `test_sandbox_config_error_modal_present_creds_returns_none`, `test_cli_sandbox_docker_reachable_starts_the_repl`.
- [x] PASS — glossary rows + ADR Accepted + no synonym — `docs/glossary.md:17,53,54,55,56` (Sandbox,
      Credential Proxy, Sandbox Mode, Worker, Proxy Rule); ADR-0011 `Status: Accepted` (line 3);
      `grep -riE 'container mode|shell jail' docs/ src/` → none.
- [x] PASS — `make ci` components green, 0 warnings, `uv lock --check` clean.

**Evidence**
```
$ make unit-tests
======================= 1086 passed in 74.04s (0:01:14) ========================
$ make integration-tests
============================= 21 passed in 49.49s ==============================
$ uv lock --check
Resolved 149 packages in 3ms

# real CLI, no doubles:
$ SANDBOX_MODE=docker DOCKER_HOST=unix:///nonexistent/docker.sock GEMINI_API_KEY=dummy decode run "list the files"
Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable — start Docker and retry (see .env.example).   (exit 1, 0 tracebacks)
$ SANDBOX_MODE=modal HOME=<empty> (no MODAL_TOKEN_*) GEMINI_API_KEY=dummy decode
Decode: SANDBOX_MODE=modal but Modal credentials are missing — run `modal token set …` (see .env.example).        (exit 1, 0 tracebacks)
# none mode, fake docker on PATH + bad DOCKER_HOST: sentinel NEVER written → no probe ran; flow proceeds.
# conftest leak: SANDBOX_MODE=docker + bad DOCKER_HOST exported → 241 cli/runtime/tui tests still pass.
```

**Other issues found** (non-blocking notes for the orchestrator / PR reviewer)
- A malformed value for a `Literal`/`Field` setting (`SANDBOX_MODE=vm`/`""`, `SANDBOX_TIMEOUT_S=0`) surfaces
  as a pydantic `ValidationError` traceback from the module-level `settings = Settings()`, not a one-line
  friendly message. This is **consistent with the pre-existing behavior for `LLM_PROVIDER`** (verified
  side-by-side) and satisfies the AC ("fails at load"); the friendly-guard pattern is deliberately scoped
  to backend *availability* (presence, not correctness). Not a task-071 regression — flagged only if the
  team ever wants a uniform friendly wrapper around settings-load errors (would be a separate task).
- `code-review` plugin is enabled in `.claude/settings.json`; it is a slash-command surface not invocable
  from this QA agent context. The manual checklist above (types, no `print`, no hardcoded secrets, scoped
  diff, security) was applied in its place and found nothing.

**VERDICT: PASS**
