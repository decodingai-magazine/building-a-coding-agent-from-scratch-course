---
id: 085-sandbox-isolated-workspace-capstone
feature: isolated-workspace
status: pending
---

# Capstone rewrite — the isolated-workspace feature end to end (offline + skipif real infra)

Tags: `sandbox`, `test`
Depends on: #082, #083
Blocks: —

Rewrite `tests/integration/test_sandbox_capstone.py` as the living proof for the isolated Workspace
(ADR-0012): an always-run offline slice + skipif-guarded real-infra smokes, so `make ci` stays green
without docker/modal. Replaces the ADR-0011-era capstone.

## Scope

- **Always-run offline slice (no infra/key):**
  - **One-seam contract:** a command round-trips `SandboxExecutor` (fake `SandboxBackend` double)
    through the real `bash` registry + `PermissionGate` to a rendered `ExecResult`; fresh-exec (one
    exec per call); `none`-mode rendering byte-identical.
  - **File tools through the seam:** with a fake backend, `read`/`write`/`edit` route through the
    backend's file ops on logical paths into the Workspace; `glob`/`grep` route through backend `exec`;
    `none` mode uses direct pathlib (byte-identical); containment rejects `..` via backend-agnostic
    path math.
  - **Selection swap** (`SANDBOX_MODE` → `SandboxExecutor(DockerBackend)` /
    `SandboxExecutor(ModalBackend)` / `LocalExecutor`); **harness-home vs tool-scope** split (file tool
    writes to Workspace; MEMORY/session/permission/skills/memory under launch cwd); **unified `bash`
    description**; **workspace prep** (offline clone; `--repo`+`none` → guard line); **LSP posture**
    (none+docker on; modal best-effort-off); **`web_fetch` stays gated**.
  - **Git hand-back (offline):** with a local repo + a local `--repo` origin (no network) — a dirty
    Workspace → `ship_workspace` creates `decode/<id>` locally carrying the uncommitted work even with
    push off/failing; a push to the local origin lands the branch; an unchanged Workspace ships
    nothing; `none`/no-repo auto-ship is a no-op and `/ship` prints the friendly line; the push is
    host-side (a test asserts no git command routes through the executor/backend seam — creds can't
    reach the Worker).
  - **Credential map** (retained) + **replay-safety** (`{"cache": False}` when `sandbox_mode != none`;
    `none` byte-identical); **REPL free** (`none` imports no sandbox module; `import decode.cli`
    imports no kitaru).
- **skipif-guarded real-infra smokes** (SKIP, never fail, when infra absent):
  - **real docker:** a `--repo` clone-Workspace round-trip — clone into the Workspace, `bash` + file
    tools see its files, a `bash`-written file is host-visible in `.decode/sandbox` (mount),
    fresh-exec, timeout kills the command but the container + fs survive; **ship** creates +
    (local-origin) pushes `decode/<id>`; `aclose` removes the container.
  - **real modal:** direct file ops + lifecycle — bootstrap upload; `read_bytes`/`write_bytes` direct
    against the remote (no mirror; a `remove` is reflected); timeout kills the exec not the sandbox;
    `export()` sweeps `/workspace` (incl. `.git`) host-side; **ship** creates + pushes `decode/<id>`
    to a local origin; `aclose` exports + terminates; revival re-bootstraps from host state.
  - **real docker + proxy:** the credential boundary (header ARRIVES; worker env holds no secret).
  - Each reaps its container/sandbox/network in a `finally`.

## Acceptance criteria

- [ ] The offline slice passes with no infra/key and proves: the one-seam fresh-exec contract, file
  tools through the seam (+ `none` byte-identical), selection swap, harness-home split, unified
  description, offline clone + none-mode guard, LSP posture, gated web_fetch, the git hand-back
  (branch-local-on-push-fail / dirty captured / unchanged ships nothing / none-mode no-op + `/ship`
  line / creds-not-in-sandbox), credential map, and replay-safety config.
- [ ] The real-infra smokes SKIP cleanly when absent and PASS when present, including the real
  docker/modal ship round-trips to a local origin.
- [ ] Hermetic under `filterwarnings=["error"]` run alone (deterministic disposal; no leaked
  subprocess/async/container/sandbox/network).
- [ ] `make ci` green, 0 warnings, infra-less (real smokes skipped); `uv lock --check` passes.
- [ ] The module docstring documents the feature end to end (doubles as documentation), naming real vs
  faked boundaries.

## Out of scope

- New product code (all in 078–083). A deployed-stack proxy test / a real *remote* push (local-origin
  push only, offline-provable).

## Log
