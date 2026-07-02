---
id: 075-sandbox-credential-proxy-and-replay-safety
feature: sandboxing
status: pending
---

# Credential Proxy (headless + docker) + headless bash replay-safety

Tags: `sandbox`, `runtime`, `infra`, `agent`
Depends on: #074
Blocks: #076, #077

This task implements ADR-0011 §5-6 — the two ways the **headless durable flow** must meet the sandbox:
(1) a **replay-safety** fix so a replayed `decode run` re-executes sandbox `bash` instead of serving a
stale, side-effect-free cache (ADR-0010 reconciliation); and (2) the canonical **Credential Proxy**
(headless + docker only) so a sandboxed **Worker** makes authenticated tool calls while holding **no**
secret. Built the `agent_harness_platform` way (these classes are NOT in the `kitaru` package — decode
adapts them). Honors AGENTS.md *"secrets never reach the model or the sandbox payload."* The REPL never
builds the proxy, so **bare `decode` never imports kitaru** stands.

## Scope

### A. Headless bash replay-safety (ADR-0011 §5)

- **Check today's behavior first:** `runtime/flow.py::_build_runtime_agent` (bypass) passes **no**
  `tool_checkpoint_config_by_name`, so under the `"calls"` default `bash` gets a **cached** per-call
  checkpoint → a replay would serve stale results (shell side effects not re-run). The HITL agent already
  opts `bash` out of its checkpoint (a waiter), and `decode replay` is bypass-only, so this targets the
  **bypass** flow.
- **Fix:** when `settings.sandbox_mode != "none"`, configure the bypass agent's `bash` checkpoint to
  **re-execute on replay** (kitaru's guidance: `checkpoint_strategy="calls"` +
  `tool_checkpoint_config_by_name={BASH_TOOL_NAME: {"cache": False}}`). **Verify-first (kitaru 0.18):**
  confirm the value shape that keeps the checkpoint but disables caching — a bare `False` drops the
  checkpoint entirely (losing replay-readiness); the `{"cache": False}` dict is the per-checkpoint config.
  Record the confirmed shape. When `sandbox_mode == "none"` behavior is **byte-unchanged**.

### B. Credential Proxy (ADR-0011 §6) — `src/decode/sandbox/proxy.py`

- **Verify-first (mirror task 061):** confirm `kitaru.get_secret(name).values` and the mitmproxy addon
  request-hook API against the installed SDK / context7 before coding.
- `SandboxProxyRule` — a frozen dataclass: `name`, `hosts: list[str]`, `headers: dict[str,str]` (values
  may embed `{{ secret-name.key }}` templates). `DEFAULT_PROXY_RULES: list[SandboxProxyRule] = []`
  (ships **empty** — opt-in; an empty map is a passthrough proxy).
- `build_credential_map(rules) -> dict[str, dict[str, str]]` — host-side, at flow start: resolve each
  `{{ name.key }}` via `kitaru.get_secret(name).values` (lazy `from kitaru import get_secret`), producing
  `{host: {header: resolved-value}}`. Never logs resolved **values** — only rule/host/header **names**
  (task-061 discipline).
- **`DockerProxy` topology:** a `_sandbox_proxy()` context manager in `runtime/flow.py` (mirroring
  `_durable_sleeper` / `_config_from_secret_store`), engaged only when `sandbox_mode == "docker"` **and**
  `sandbox_credential_proxy_enabled`:
  1. `credential_map = build_credential_map(DEFAULT_PROXY_RULES)` (host-side).
  2. create a docker network; start the proxy container from `settings.sandbox_proxy_image`
     (`mitmproxy/mitmproxy`) running a **mounted addon** (`src/decode/sandbox/proxy_addon.py`, a
     standalone module executed by mitmproxy inside the container — imports **no** decode) that reads the
     credential map from **its own env** (JSON) and injects the matching host's headers per request; the
     proxy writes its CA to a shared volume.
  3. construct the `DockerExecutor` with **proxy wiring** — `--network <net>`, `http_proxy`/`https_proxy`
     → the proxy container, the mounted mitmproxy CA, and `update-ca-certificates` as the container's
     first shell step (worker is root; slim has ca-certificates) — and install it as `bash`'s `_EXECUTOR`
     for the flow span (this is `DockerExecutor`'s **second caller**, earning its optional proxy params).
  4. teardown on exit: `close_executor()` (worker), stop the proxy container, remove the network; restore
     the seam.
- **CA-trust choice (ADR §6):** stock `SANDBOX_IMAGE` + CA-mount + `update-ca-certificates` (not an
  in-repo `sandbox.Dockerfile`). Outbound probe uses **python/urllib** (slim has no curl).
- **Observability:** `[sandbox] proxy start/stop <container-id>` (INFO); never log resolved credential
  values.

## Acceptance criteria

### Replay-safety

- [ ] Verify-first: the log records the confirmed kitaru-0.18 `tool_checkpoint_config_by_name` shape that
  keeps `bash` checkpointed but **re-executes on replay** (`{"cache": False}` vs bare `False`).
- [ ] With `sandbox_mode != "none"`, the bypass `_build_runtime_agent` configures `bash` to re-execute on
  replay; a unit test asserts the `KitaruAgent` is built with that config (patched seam, no real infra).
- [ ] With `sandbox_mode == "none"`, `_build_runtime_agent` is **byte-identical** to today (existing
  runtime tests pass unchanged).

### Credential Proxy

- [ ] Verify-first: `kitaru.get_secret(...).values` + the mitmproxy addon hook confirmed; recorded in log.
- [ ] `build_credential_map` resolves `{{ name.key }}` templates via a patched `kitaru.get_secret` into
  `{host:{header:value}}` (unit, hermetic); an **empty** `DEFAULT_PROXY_RULES` yields an empty map.
- [ ] **Credential boundary (SECURITY):** the resolved values appear **only** in the proxy container's
  env, **never** the worker container's env — a `skipif(no docker)` integration test scans the worker's
  env and asserts the secret is absent; logs carry only rule/host/header names, never values (`caplog`).
- [ ] **Authenticated call without a token in the worker:** with a rule for a test host and a patched
  secret, the worker makes an outbound HTTPS request (python/urllib) that arrives **with** the injected
  header though the worker holds no token — asserted via the mitmproxy addon (skipif-docker).
- [ ] The proxy is **headless + docker only**: it is never built in the REPL and never in modal mode;
  `import decode.cli` still imports **no** kitaru (a test asserts kitaru absent from `sys.modules` on the
  REPL path). The `_sandbox_proxy()` context restores the `bash` seam + tears down the proxy container +
  network on exit (asserted, incl. on error).
- [ ] Permission gate unchanged: a gated `bash` still gates identically (sandbox is defense-in-depth
  beneath it) — existing gate tests pass.
- [ ] Tests hermetic under `filterwarnings=["error"]`; `make ci` green with 0 warnings **without** docker
  (proxy integration tests SKIP); `uv lock --check` passes (mitmproxy is NOT a decode dep — runs in its
  container).

## Out of scope

- Modal-mode credential proxy (docker only — modal's dual proxy tokens are a separate header surface,
  ADR-0008 §5).
- Auto-allow sandboxed bash; hard/default-deny egress (both future work — cooperative proxy is the ceiling).
- REPL-mode proxy.

## Log
