---
id: 073-sandbox-modal-executor
feature: sandboxing
status: pending
---

# Modal sandbox executor — one session-persistent remote sandbox, empty scratch

Tags: `sandbox`, `infra`
Depends on: #071
Blocks: #074, #077

This task implements ADR-0011 §3. It adds `ModalExecutor` — a `CommandExecutor` that runs model-chosen
commands inside **one session-persistent remote `modal.Sandbox`**, starting **EMPTY** at `/workspace`
with **no** local-tree sync. Ships as a directly-tested class alongside `DockerExecutor`; not yet wired
into `bash` (task 074). The `modal` SDK is imported directly (already a runtime dependency,
`modal>=1.5.1`) — verified surface: `modal.Sandbox.create(app=…, image=…, timeout=…)`, `sb.exec(...)`,
`Sandbox.terminate`, `App.lookup(name, create_if_missing=True)`, `modal.Image.from_registry(...)`.

## Scope

- **Module:** `src/decode/sandbox/modal_executor.py` with `class ModalExecutor` implementing
  `async run(command, *, cwd, timeout_s) -> ExecResult`. (Named `modal_executor.py`, **not** `modal.py`,
  to avoid shadowing the `modal` SDK import — pytest collection edge cases + human confusion.)
- **Verify-first (pre-1.0 surface, mirror task 061):** confirm the `modal` Sandbox API against the
  installed SDK / context7 before coding; record the confirmed signatures in the log. Grooming
  confirmed the surface above against `modal 1.5.1`.
- **Sandbox lifecycle (lazy, one per session):** on the first `run()`, `App.lookup("decode-sandbox",
  create_if_missing=True)` then `modal.Sandbox.create(app=app,
  image=modal.Image.from_registry(settings.sandbox_image), timeout=int(settings.sandbox_timeout_s))`,
  working dir `/workspace`. Reuse the sandbox for every later command. `aclose()` calls
  `sandbox.terminate()` (idempotent, best-effort); the modal `timeout` is the crash backstop.
- **Per-command exec (empty scratch, no local tree):** each command runs via `sb.exec("bash", "-lc",
  command, workdir="/workspace")` (or the confirmed exec shape); read stdout/stderr and the exit code
  from the returned process handle. **Filesystem changes persist** across calls (same sandbox — git
  clone, pip install stick); **shell cwd/env reset per call** (each `sb.exec` is a fresh process — same
  effective semantics as `none` mode, unlike docker's persistent shell). The **local tree is NOT
  present** — the model is told this by 074's mode-specific description.
- **Timeout contract:** bound each `sb.exec` by `timeout_s` (asyncio-level wrapper; verify whether the
  SDK exposes a per-exec timeout and prefer it). On timeout, **terminate the exec process** (not the
  sandbox — the sandbox and its fs survive), returning `ExecResult(stdout=<partial>, stderr=<partial>,
  exit_code=<sentinel>, timed_out=True)`. Set `note` only if a session-level reset actually happened
  (normally empty — the sandbox persists).
- **Observability (ADR-0011 §7):** logger lines — on create: `[sandbox] modal create <sandbox-id>
  image=<image>` (INFO); per command: `[sandbox] $ <cmd>` → `exit=<code> bytes=<stdout-size>` (DEBUG);
  on terminate: `[sandbox] modal terminate <sandbox-id>` (INFO). Never log output at INFO.
- **Justification (log + ADR):** modal SDK (not a CLI) — modal exposes no stable exec-in-sandbox CLI; the
  SDK is the intended interface and already a dependency.

## Acceptance criteria

- [ ] Verify-first: the log records the `modal.Sandbox` create/exec/terminate + `App.lookup` /
  `Image.from_registry` signatures confirmed against the installed SDK; shipped code matches.
- [ ] `ModalExecutor.run` satisfies the `CommandExecutor` Protocol and returns an `ExecResult` with the
  real exit code + output for a normal command — proven against a **real modal account** in a
  `@pytest.mark.skipif(no modal creds)` integration test (`run("echo hi")` → `hi`, `exit_code == 0`).
- [ ] **Filesystem persists, no local tree:** `run("echo data > /workspace/f.txt")` then
  `run("cat /workspace/f.txt")` returns `data`; and a file that exists in the host cwd is **absent** in
  the sandbox (`run("ls <a-known-host-file>")` → non-zero / not found) — proving empty remote scratch.
  (skipif-modal.)
- [ ] **Timeout kills the exec, not the sandbox:** `run("sleep 100", timeout_s=1)` → `timed_out=True`; a
  subsequent `run("echo alive")` on the same instance still works (sandbox survived). (skipif-modal.)
- [ ] `aclose()` terminates the sandbox (idempotent; safe if never created; double-close does not raise).
- [ ] Observability: create/terminate + each command logged (id + image on create; `$ cmd` → exit/bytes;
  terminate on teardown) — asserted via `caplog` in the skipif-modal test.
- [ ] Tests hermetic under `filterwarnings=["error"]`; no leaked async resources.
- [ ] `make ci` green with 0 warnings **without** modal creds (the modal tests SKIP); `uv lock --check`
  passes (modal already a dep).

## Out of scope

- Wiring `ModalExecutor` into the `bash` selection seam + the mode-specific description (074).
- Local-tree sync to the remote sandbox (deliberately not done — empty scratch is the design).
- Any credential-proxy involvement (proxy is docker-only).

## Log
