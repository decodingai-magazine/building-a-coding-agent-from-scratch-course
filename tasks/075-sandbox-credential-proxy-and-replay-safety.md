---
id: 075-sandbox-credential-proxy-and-replay-safety
feature: sandboxing
status: done
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

- [x] Verify-first: the log records the confirmed kitaru-0.18 `tool_checkpoint_config_by_name` shape that
  keeps `bash` checkpointed but **re-executes on replay** (`{"cache": False}` vs bare `False`).
- [x] With `sandbox_mode != "none"`, the bypass `_build_runtime_agent` configures `bash` to re-execute on
  replay; a unit test asserts the `KitaruAgent` is built with that config (patched seam, no real infra).
- [x] With `sandbox_mode == "none"`, `_build_runtime_agent` is **byte-identical** to today (existing
  runtime tests pass unchanged).

### Credential Proxy

- [x] Verify-first: `kitaru.get_secret(...).values` + the mitmproxy addon hook confirmed; recorded in log.
- [x] `build_credential_map` resolves `{{ name.key }}` templates via a patched `kitaru.get_secret` into
  `{host:{header:value}}` (unit, hermetic); an **empty** `DEFAULT_PROXY_RULES` yields an empty map.
- [x] **Credential boundary (SECURITY):** the resolved values appear **only** in the proxy container's
  env, **never** the worker container's env — a `skipif(no docker)` integration test scans the worker's
  env and asserts the secret is absent; logs carry only rule/host/header names, never values (`caplog`).
- [x] **Authenticated call without a token in the worker:** with a rule for a test host and a patched
  secret, the worker makes an outbound HTTP(S) request (python/urllib) that arrives **with** the injected
  header though the worker holds no token — asserted via a stub upstream on the shared network
  (skipif-docker). (HTTP per the task's own Tests-section stub `http.server` guidance; see log note.)
- [x] The proxy is **headless + docker only**: it is never built in the REPL and never in modal mode;
  `import decode.cli` still imports **no** kitaru (a test asserts kitaru absent from `sys.modules` on the
  REPL path). The `_sandbox_proxy()` context restores the `bash` seam + tears down the proxy container +
  network on exit (asserted, incl. on error).
- [x] Permission gate unchanged: a gated `bash` still gates identically (sandbox is defense-in-depth
  beneath it) — existing gate tests pass.
- [x] Tests hermetic under `filterwarnings=["error"]`; `make ci` green with 0 warnings **without** docker
  (proxy integration tests SKIP); `uv lock --check` passes (mitmproxy is NOT a decode dep — runs in its
  container).

## Out of scope

- Modal-mode credential proxy (docker only — modal's dual proxy tokens are a separate header surface,
  ADR-0008 §5).
- Auto-allow sandboxed bash; hard/default-deny egress (both future work — cooperative proxy is the ceiling).
- REPL-mode proxy.

## Log

### [SWE] 2026-07-03 08:20 — Implementation

**Verify-first records (installed toolchain: kitaru 0.18.0, docker 29.4.3, python:3.12-slim, mitmproxy/mitmproxy:latest)**

- **Replay-safety kwarg shape (kitaru 0.18):** `KitaruAgent.__init__`'s
  `tool_checkpoint_config_by_name: Mapping[str, CheckpointConfig | Literal[False]]`.
  `CheckpointConfig` is a `TypedDict(total=False)` with keys `{cache, runtime, retries, type}` — so
  **`{"cache": False}` is a valid per-tool config that KEEPS the per-call checkpoint but disables its
  cache** (→ replay re-executes bash). A **bare `False`** is the `Literal[False]` that DROPS the
  checkpoint entirely (what HITL uses for its waiters via `dict.fromkeys(_HITL_WAIT_TOOL_NAMES, False)`)
  — which would lose replay-readiness. Confirmed shape shipped: `{BASH_TOOL_NAME: {"cache": False}}`,
  passed only when `sandbox_mode != "none"`. Stored on `KitaruAgent._tool_checkpoint_config_by_name`.
- **`kitaru.get_secret` (kitaru 0.18):** `get_secret(name_or_id: str) -> Secret`; `Secret.values` is a
  `dict[str, str]`; a missing secret raises `KitaruRuntimeError` (propagated, not caught → the task AC's
  "not a silent skip"). `build_credential_map` resolves `{{ name.key }}` via `get_secret(name).values[key]`.
- **mitmproxy addon + CA wiring (real-docker probe):** the **stock** `mitmproxy/mitmproxy` image accepts
  `mitmdump --quiet --listen-host 0.0.0.0 --listen-port 8080 --set confdir=/certs -s /opt/proxy_addon.py`
  with the addon **mounted read-only** (`-v addon:/opt/proxy_addon.py:ro`) and the map in the container's
  own env (`DECODE_CREDENTIAL_MAP`). mitmdump writes its CA to the bind-mounted confdir as
  `/certs/mitmproxy-ca-cert.pem` (a valid `-----BEGIN CERTIFICATE-----` PEM, host-readable). `python3` is
  on PATH in the image (used for the port-readiness probe).
- **Worker CA trust (real-docker probe):** `python:3.12-slim` ships `/usr/sbin/update-ca-certificates`,
  has `/usr/local/share/ca-certificates`, runs as **root**, and has `bash` — so the ADR's "stock image +
  CA-mount + `update-ca-certificates`" holds. Worker entry becomes `bash -c "update-ca-certificates &&
  exec sleep infinity"` on the proxy path (byte-identical `sleep infinity` off it).

**Files modified**
- `src/decode/runtime/flow.py` — `_build_runtime_agent` now passes the replay-safety
  `tool_checkpoint_config_by_name={BASH_TOOL_NAME: {"cache": False}}` iff `sandbox_mode != "none"`
  (byte-identical `KitaruAgent` build in `none`); new `_sandbox_proxy()` context manager (mirrors
  `_config_from_secret_store`/`_durable_sleeper`) engaged only for docker + proxy-enabled headless flows;
  both flow bodies (`run_agent_task`, `run_agent_task_hitl`) now nest `_sandbox_proxy()`.
- `src/decode/sandbox/docker_executor.py` — `DockerExecutor` gains optional keyword proxy params
  (`network`/`proxy_env`/`ca_cert_host_path`, plain types — no proxy type leaks); new `_docker_run_args`
  (byte-identical `docker run` off the proxy path) + `_entry_command` (CA-trust-then-sleep on it);
  `_WORKER_CA_PATH` const.
- `src/decode/tools/bash.py` — new `install_executor()` seam (mirrors `install_durable_sleeper`) the flow
  uses to install the proxy-wired worker for the flow span; paired with the existing `close_executor()`.
- `tests/unit/decode/sandbox/test_docker_executor.py` — proxy-arg construction tests (byte-identical off,
  `--network`/`-e`/CA-mount/entry-command on).
- `tests/unit/decode/test_cli.py` — REPL invariant: `import decode.cli` imports no `decode.sandbox.proxy`.

**Files added**
- `src/decode/sandbox/proxy.py` — `SandboxProxyRule` (frozen), `DEFAULT_PROXY_RULES` (ships empty; github
  example in a comment), `build_credential_map` (host-side `{{ name.key }}` resolution via lazy
  `kitaru.get_secret`, names-only logging), `DockerCredentialProxy` (the mitmproxy topology: per-run
  network + container, CA via shared confdir, `start`/`stop`, sync CLI → loop-independent teardown).
- `src/decode/sandbox/proxy_addon.py` — standalone mitmproxy addon (stdlib + mitmproxy only; imports no
  decode/kitaru); reads `DECODE_CREDENTIAL_MAP`, injects matching-host headers, logs names only.
- `tests/unit/decode/sandbox/test_proxy.py` — hermetic: rule shape, template resolution, empty→{},
  missing-secret/missing-key propagate, secret-caching, names-not-values `caplog`, proxy properties.
- `tests/unit/decode/runtime/test_sandbox_proxy.py` — replay-safety kwarg present iff sandbox active
  (spy on `KitaruAgent`) + byte-identical `none`; `_sandbox_proxy` no-op for none/modal/disabled.
- `tests/integration/test_credential_proxy.py` — skipif-docker: header ARRIVES at a stub upstream while
  the worker env holds no secret + CA mounted; the real `_sandbox_proxy()` installs+tears-down (incl. on
  a raising body).

**Tests**
- Unit: 1192 passing, 0 failing (`make pre-commit` = format-check + lint-check + unit-tests, all green).
- Integration: 39 passing with real docker (incl. 3 new credential-proxy + the task-062 runtime capstone
  unchanged); 12 skipped cleanly when the docker daemon is unreachable (`make ci` green without docker).
- `uv lock --check`: clean — mitmproxy is NOT a decode dep (runs in its container).

**Acceptance criteria** — all met (see checkboxes above). No `[HUMAN]` criteria.

**Evidence**

```
$ make pre-commit
... ======================= 1192 passed in 82.03s (0:01:22) ========================

$ uv run pytest tests/integration/ -q
... 39 passed in 78.62s (0:01:18)

$ uv lock --check
Resolved 149 packages in 2ms

# docker-unavailable → clean SKIP (fake `docker` exiting 1 on PATH):
$ PATH=<fake>:$PATH uv run pytest tests/integration/test_credential_proxy.py tests/integration/test_docker_executor.py -q
... 12 skipped in 1.76s

# Real e2e: decode run, live Gemini, SANDBOX_MODE=docker SANDBOX_CREDENTIAL_PROXY_ENABLED=true
$ SANDBOX_MODE=docker SANDBOX_CREDENTIAL_PROXY_ENABLED=true decode run "... run: echo hello-from-sandbox ..."
[sandbox] proxy start 0dde7422e0e7 (image=mitmproxy/mitmproxy, hosts=[])
[sandbox] docker start a772925...b428b9 image=python:3.12-slim (proxy-wired)   # worker is proxy-wired
[sandbox] docker stop a772925...b428b9                                          # worker reaped FIRST
[sandbox] proxy stop 0dde7422e0e7                                               # proxy stopped after
Pipeline `run_agent_task` completed successfully.  (exit 0)
The exact output is: hello-from-sandbox
# no leftover decode-proxy / decode-sandbox containers or networks

# Real-docker topology probe (decode classes): injected header ARRIVED at upstream, worker env clean
INJECTED HEADER ARRIVED: True
SECRET ABSENT FROM WORKER ENV: True
http_proxy set in worker env: True
CA mounted in worker: True
proxy/worker/upstream containers gone: True; network gone: True
```

**Notes**
- **HTTP(S) reconciliation:** the integration test drives the worker's outbound call over **HTTP** to a
  stub `http.server` on the shared network — exactly the "run a tiny http.server container on the shared
  network" path the task's Tests section prescribes. This proves both security claims (the injected
  header ARRIVES at the upstream; the worker env holds no token). A hermetic **HTTPS**-through-the-proxy
  test would require either weakening the production proxy with `--ssl-insecure` (an anti-goal) or a real
  internet endpoint (non-hermetic); instead the CA-trust path is asserted directly (the
  `mitmproxy-ca-cert.crt` is mounted into the worker + `update-ca-certificates` runs at container start),
  and mitmproxy's HTTPS MITM uses that same trusted CA. Flagged for the Tester as a deliberate,
  scoped trade-off.
- **Teardown order is load-bearing:** `_sandbox_proxy` reaps the **worker first** (`docker rm -f` detaches
  its network endpoint) then removes the network — `docker network rm` fails while a container is still
  attached, so `DockerCredentialProxy.stop()` also retries the network rm briefly to absorb the daemon's
  endpoint-cleanup lag. Proven by the real e2e log order and the leftover-container/network assertions.
- **`_reap_runtime_executor` runs twice** on the engaged path (once in `_sandbox_proxy`'s finally to reap
  the worker before the network rm, once in the flow's outer finally) — safe/idempotent (the second finds
  the reset `LocalExecutor` and no-ops); documented inline.
- Both headless flows (bypass + HITL) nest `_sandbox_proxy`; it is a no-op for none/modal/proxy-disabled,
  so every existing runtime test + the capstone pass unchanged.

### [Tester] 2026-07-03 11:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS
- Unit tests: 1192 passed / 0 failed
- Integration tests: 39 passed / 0 failed (real docker); 13 SKIP cleanly with docker down (`make ci` green without docker)
- Warnings: 0 (`filterwarnings=["error"]`)
- `uv lock --check`: clean (mitmproxy is not a decode dep)

**E2E adversarial pass** (real docker daemon 29.4.3; REAL isolated kitaru secret store via `create_secret`, store-isolation tripwire enforced)
- Happy path (live Gemini, `SANDBOX_MODE=docker SANDBOX_CREDENTIAL_PROXY_ENABLED=true decode run "echo ..."`) → exit 0, answer on stdout, teardown order `proxy start → docker start (proxy-wired) → docker stop → proxy stop`, no litter (PASS)
- Break 1 (SECURITY, credential boundary, REAL secret): rule host request arrives WITH injected header carrying the real secret; worker `env` holds NO secret value and NO `DECODE_CREDENTIAL_MAP`; value present ONLY in proxy container env; worker/proxy separate containers; resolution logs names-only (PASS)
- Break 2 (SECURITY, over-injection): a NON-rule host (`other.local`) request gets NO injected header — the boundary does not leak one host's cred to another (PASS)
- Break 3 (missing secret): a rule referencing an absent secret fails LOUDLY against a real store (`KitaruRuntimeError: Secret ... not found`), not a silent skip (PASS)
- Break 4 (replay-safety, real docker + real kitaru replay): replay from the `bash_tool` anchor RE-EXECUTES bash with the shipped `{"cache": False}` (`Checkpoint bash_tool started`; side-effect file 1→2), vs a forced-`cache:True` control that serves it stale (`Checkpoint bash_tool cached`; file 1→1) — the ADR-0011 §5 fix proven end to end (PASS)
- Break 5 (teardown on error + loop-independence): `_sandbox_proxy` reaps proxy+network on a raising body (integration test) and no `Event loop is closed` traceback / `ResourceWarning` on stderr in a live docker run (PASS)
- Break 6 (REPL invariant): `import decode.cli` pulls in NO kitaru, NO `decode.sandbox.proxy`, NO `proxy_addon` (PASS)
- **Break 7 (HTTPS CA-trust — FAIL): the worker's FIRST command runs BEFORE `update-ca-certificates` folds the mitmproxy CA into the trust store → the first HTTPS-through-proxy tool call fails `CERTIFICATE_VERIFY_FAILED`.** 3/3 trials: first cmd @ ~160-180ms fails "unable to get local issuer certificate"; CA trusted only @ ~430-480ms. Root cause: `_entry_command` runs `bash -c "update-ca-certificates && exec sleep infinity"` as PID 1, but `_ensure_container` returns as soon as `docker run -d` yields the id and `docker exec` commands run concurrently. Because the worker is created lazily on the first `bash` call, the first HTTPS call reliably lands in the untrusted window. Proven decisively: a leaf signed by the real mitmproxy CA verifies against the worker's default store only AFTER the window closes (`openssl verify` → `unable to get local issuer` early, `OK` late). The shipped example rule (GitHub token → `api.github.com`, HTTPS) would fail on its first call.

**HTTPS-gap adequacy ruling:** HTTP-only proof + "update-ca-certificates runs" is **inadequate**. `update-ca-certificates` does run, but not *before use* — so a real HTTPS call does NOT reliably trust the proxy (the minimum bar). Credential injection over HTTP is proven; over HTTPS (the feature's headline use) is broken for the first call.

**Acceptance criteria** — all explicit ACs met per their letter (the authenticated-call AC is scoped to HTTP by its own parenthetical, and HTTP passes). Verify-first shapes independently confirmed: `KitaruAgent.tool_checkpoint_config_by_name: Mapping[str, CheckpointConfig | Literal[False]]`; `CheckpointConfig` is `TypedDict(total=False)` with a `cache` key → `{"cache": False}` keeps the checkpoint / disables cache. none-mode `_build_runtime_agent` is byte-identical to the 070 baseline (`{name, checkpoint_strategy}` only).

**FAIL (blocker): the CA-trust HTTPS path (ADR-0011 §6) has a start-up race that breaks the first HTTPS tool call through the proxy.**
- Expected: after the worker starts on the proxy path, the mitmproxy CA is in the system trust store BEFORE the first command, so a first HTTPS call trusts the proxy.
- Actual: `update-ca-certificates` runs unawaited as PID 1; the first command (the lazily-created container's first `docker exec`) runs ~250-300ms before the CA is folded in → first HTTPS call fails `CERTIFICATE_VERIFY_FAILED`.
- Fix: on the proxy path (`ca_cert_host_path` set), make the worker await CA readiness before the first command — e.g. run `update-ca-certificates` synchronously via `docker exec` inside `_ensure_container` after `docker run -d`, or poll `docker exec <id> test -f /etc/ssl/certs/<hash>.0` (mirroring the proxy's own `_wait_until_ready`). Add a regression test: the worker's FIRST command verifies a mitmproxy-CA-signed leaf against its default trust store (`openssl verify` → OK).

**Other issues found (non-blocking, for the orchestrator)**
- The credential map JSON (with the resolved secret) rides on the proxy container's `docker run -e DECODE_CREDENTIAL_MAP=...` argv, briefly visible in host `ps` during proxy start. Host-side only (the host process legitimately holds the secret); the worker boundary is clean. Consider passing it via `--env-file` / stdin if hardening host exposure later.
- Pre-existing (not 075): kitaru's local runner routes all flow logs to **stdout** with a `Kitaru:` prefix, and docker-mode runs emit an INFO `Loop ... that handles pid ... is closed` line (present in docker mode without the proxy too — a 072/074/068 artifact, benign, not the 074 traceback-noise).

**VERDICT: FAIL** — 1 blocker (CA-trust HTTPS race). The credential SECURITY boundary itself is solid and thoroughly proven (secret never reaches the worker, no over-injection, loud on missing secret); replay-safety is proven end to end. The single fix is small and localized to `DockerExecutor` on the proxy path.

### [SWE] 2026-07-03 09:35 — Fixes (CA-trust race blocker)

Fixed the one blocker: the worker's first HTTPS call raced a still-booting `update-ca-certificates`.

**Root cause (confirmed):** the CA was trusted by a **PID-1 entry step** (`bash -c "update-ca-certificates
&& exec sleep infinity"`), but `docker run -d` returns before it finishes — and the worker is
lazily-created on the first `bash`, so the first `docker exec` always landed in the untrusted window.

**Fix (`src/decode/sandbox/docker_executor.py`, proxy path only):**
- `_ensure_container` now folds the CA in **synchronously, before returning the container as ready**: a
  new `_trust_proxy_ca(id)` runs `docker exec <id> update-ca-certificates` and **awaits it** (bounded by
  `_CA_TRUST_TIMEOUT_S=60s`; no `sandbox_startup_timeout_s` setting exists, so a fixed internal bound).
  On failure/timeout it reaps the just-created worker (no leak) and raises → `run()`'s existing
  infra-failure handler renders it for the model.
- `_docker_run_args` entry is now a bare `sleep infinity` in **both** cases (dropped the PID-1
  CA-trust step; removed `_entry_command`). **Non-proxy path is byte-identical** — no CA step runs
  (`test_ensure_container_runs_no_ca_step_off_the_proxy_path`; the existing byte-identical-argv test
  still passes). Runs on `run_sync`'s loop (setup), so no cross-loop teardown regression.

**Non-blocking hardening also applied** (the Tester's `ps`-visibility note): the resolved credential
map now rides a private `--env-file` (a `0600` temp file `unlink`ed the instant `docker run` consumes
it) instead of `-e DECODE_CREDENTIAL_MAP=<json>`, so **the secret never appears on the `docker run`
argv (host `ps`)** nor lingers on disk. It still lands only in the proxy container's env. Verified
`docker --env-file` round-trips compact JSON (incl. values with spaces). New hermetic test asserts the
secret is absent from the argv and present only in the (then-deleted) env-file.
(Left as-is per the Tester: the benign `Loop ... is closed` INFO line and kitaru's `Kitaru:` stdout
prefix — both pre-existing, not 075.)

**What changed since round 1 (files)**
- `src/decode/sandbox/docker_executor.py` — new `_trust_proxy_ca` (synchronous CA trust) + `_CA_TRUST_TIMEOUT_S`; `_ensure_container` calls it on the proxy path; `_docker_run_args` entry → bare `sleep infinity`; removed `_entry_command`; class docstring updated.
- `src/decode/sandbox/proxy.py` — `DockerCredentialProxy._run_proxy_container` uses `--env-file` (was `-e`); added `contextlib`/`os` imports.
- `tests/integration/test_credential_proxy.py` — **new** `test_worker_trusts_the_proxy_ca_on_its_very_first_command` (the race regression, openssl-verify of the CA against the worker's default store on the FIRST command).
- `tests/unit/decode/sandbox/test_docker_executor.py` — replaced the 2 `_entry_command` tests with 3 CA-trust tests (proxy path runs `docker exec update-ca-certificates`; non-proxy runs none; failure reaps + raises); fixed the proxy-args entry assertion to `sleep infinity`.
- `tests/unit/decode/sandbox/test_proxy.py` — **new** `test_run_proxy_container_keeps_the_secret_off_the_docker_argv` (hardening); added `Path` import.

**Red→green evidence (the CA fix)**

```
# BEFORE the fix — the new regression fails 3/3 (the race), matching the Tester's evidence:
$ pytest tests/integration/test_credential_proxy.py::test_worker_trusts_the_proxy_ca_on_its_very_first_command   # x3
  AssertionError: proxy CA not trusted on the FIRST command:
    error 18 at 0 depth lookup: self-signed certificate
    error /usr/local/share/ca-certificates/mitmproxy-ca-cert.crt: verification failed   (x3, all FAIL)

# AFTER the fix — green 3/3:
  1 passed  /  1 passed  /  1 passed
```

**Re-run gate (all green)**
- `make pre-commit` (format-check + lint-check + unit): **1194 passed** (was 1192; +4 new, −2 removed).
- `uv run pytest tests/integration/ -q -W error`: **40 passed** (incl. the new CA regression), 0 warnings.
- Docker-unavailable → **12 skipped** cleanly (fake `docker` on PATH) → `make ci` green without docker.
- `uv lock --check`: clean (mitmproxy still not a dep).
- Re-confirmed still green: credential-boundary (`..._worker_holds_no_secret`, `..._logs_names_never_values`) + all of `test_sandbox_proxy.py` (replay-safety + no-op).
- Docker hygiene after the full integration run: `docker ps -a` / `docker network ls` show **no** leftover `decode-proxy*` / `decode-sandbox*` / `decode-it-upstream*` containers or networks.

**Notes**
- Not committed (Tester re-review first).
- The regression is race-decoupled: green after the fix means the CA was trusted *before the first
  command returned* (the exact contract), and it reproduced RED 3/3 pre-fix on this machine.

### [Tester] 2026-07-03 13:05 — QA re-review (round 2)

**Test summary**
- Format / lint / pre-commit: PASS — **1194 unit passed, 0 warnings**
- Integration (real docker): **40 passed, 0 warnings** (incl. the new CA regression); **13 SKIP** cleanly with docker down (`make ci` green without docker)
- `uv lock --check`: clean

**Re-verify of the round-1 blocker — CA-trust HTTPS race — FIXED (verified, not trusted)**
- **My exact round-1 repro, re-run:** a leaf signed by the REAL mitmproxy CA (CA key stays host-side), verified against the worker's DEFAULT trust store as the worker's **FIRST** command → **3/3 PASS** (`leaf.pem: OK`). First command now @ ~542-579ms (was ~160ms pre-fix); the ~380ms increase is the synchronous `docker exec update-ca-certificates`. **No untrusted window** — the CA is trusted before the first command returns.
- The new integration regression `test_worker_trusts_the_proxy_ca_on_its_very_first_command` passes; I confirmed it reproduces the SWE's red 3/3 signature pre-fix.
- **HTTPS-adequacy ruling — now ADEQUATE.** The `openssl verify` of a real-mitmproxy-CA-signed leaf against the worker's default store is **equivalent** to the client-side TLS trust decision: same cert chain, same `/etc/ssl/certs` trust store, same OpenSSL verification a real HTTPS handshake runs; the leaf is signed by the actual mitmproxy CA exactly as MITM does on the fly. A full real HTTPS GET stays hermetically inconclusive with the shipped (eager, upstream-verifying) proxy — a 502-at-CONNECT masks client trust — so leaf-verify is the correct decisive proof, and the round-1 negative control (untrusted → error 20/18) shows it discriminates.

**Break paths re-run (round 2)**
- Bounded CA-trust FAILURE: forced `update-ca-certificates` non-zero against a real container → `run()` returns a rendered infra error (exit 125), worker **reaped (no leak)**, no crash/hang (PASS)
- `--env-file` hardening: with a real secret, the value is **absent from host `ps` argv**, no `DECODE_CREDENTIAL_MAP=` in argv, no `decode-proxy-cred-*.env` left on disk, and the value still reaches **only** the proxy container's env (PASS)
- Non-proxy byte-identity: `_docker_run_args` off the proxy path is bare `sleep infinity` (no CA step); `_ensure_container` non-proxy = 1 spawn (unit tests + argv confirmed) (PASS)
- Credential boundary re-run over the new `--env-file` delivery path: 14/14 — header arrives at rule host, NO over-injection to a non-rule host, worker env clean (no secret, no cred-map var), secret only in proxy env, names-only logs, clean teardown (PASS)
- Replay-safety re-run (code byte-identical to round 1): replay from `bash_tool` with `{"cache": False}` → `bash_tool started` → bash RE-EXECUTES → side-effect 1→2 (PASS)
- -W error hermeticity across unit + integration (0 warnings), incl. the CA-trust exec on `run_sync`'s loop — no cross-loop regression (PASS)

**Cost hygiene:** no leftover decode/proxy/upstream containers or networks; 0 active modal containers.

**All acceptance criteria met (verified with evidence).** The round-1 blocker is resolved and the non-blocking `ps`-visibility note was hardened. No regressions.

**VERDICT: PASS** — the security-critical credential-proxy + replay-safety task is CLEARED. The credential boundary is proven (secret never reaches the worker, only the proxy; no over-injection; loud on missing secret), the CA-trust HTTPS path now trusts the proxy on the very first command with no race, replay-safety re-executes sandbox bash, and teardown/hygiene are clean.
