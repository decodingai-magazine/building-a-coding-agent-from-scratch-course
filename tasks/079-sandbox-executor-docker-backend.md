---
id: 079-sandbox-executor-docker-backend
feature: isolated-workspace
status: pending
---

# Unified SandboxExecutor + SandboxBackend Protocol (exec + file ops) + DockerBackend (fresh-exec)

Tags: `sandbox`, `docker`, `refactor`
Depends on: #078
Blocks: #080, #081

The core architectural collapse (ADR-0012): ONE `SandboxExecutor` over a thin `SandboxBackend`
Protocol that carries **both** command exec **and** file ops (the "swap the set" seam — file tools
route through this in 081), plus the first adapter `DockerBackend` in **fresh-exec** form — deleting
docker's persistent-shell + marker/`$?` protocol + shell-reset machinery. `DockerExecutor` is
replaced; the docker Credential Proxy is rewired. Modal keeps its old executor this task (rewired in
080) so the suite stays green. `none` mode untouched. Supersedes ADR-0011 §2.

## Scope

- **New `src/decode/sandbox/executor.py`** — `SandboxExecutor` (a `CommandExecutor`) + the
  `SandboxBackend` Protocol:
  - `SandboxExecutor.run(command, *, cwd, timeout_s)`: ensure created → `backend.exec("bash","-lc",
    command, timeout_s=timeout_s)` → return `ExecResult`. **Fresh-exec** — one exec per call, no
    persistent shell. One container/sandbox per session.
  - `SandboxExecutor.start(workspace: Path)` — eager warm-up: set `self._workspace`, `backend.create()`
    (bootstrap incl. `workspace.seed_skills(workspace)` so every entry point gets skills — replaces
    the docker ro-mount), idempotent.
  - `SandboxExecutor.aclose()` — `backend.export()` (session-end sandbox→host sweep; docker no-op) then
    `backend.destroy()`. Idempotent, best-effort, loop-independent.
  - **Workspace resolution contract (stable from here):** the executor uses the `start(workspace)`
    path; `run(cwd=…)` is ignored for the workdir by sandbox executors (they run in `/workspace`); an
    un-started executor derives `workspace.workspace_dir(cwd)` as a test/lazy fallback only.
  - `SandboxBackend` Protocol — **exec + file ops + lifecycle**:
    `create(workspace) -> None` (incl. one-shot bootstrap); `exec(*args, timeout_s) -> ExecResult`;
    **file ops on LOGICAL paths (relative to the workspace root):** `read_bytes(rel) -> bytes`,
    `write_bytes(rel, data) -> None`, `make_directory(rel) -> None`, `stat(rel) -> FileStat | None`,
    `list_dir(rel) -> list[FileStat]`, `remove(rel) -> None`; `export() -> None` (session-end sweep;
    docker no-op); `destroy() -> None`. Byte transport is per-backend; the shared file-tool logic
    (containment path-math, edit search/replace, truncation, render) lives above the seam (wired in
    081). glob/grep run via `exec` (find/grep), not Protocol file ops.
- **New `src/decode/sandbox/docker_backend.py`** — `DockerBackend` (fresh-exec; moved+simplified from
  the deleted `docker_executor.py`):
  - `create`: `docker run -d --rm -v <workspace>:/workspace -w /workspace <image> sleep infinity` +
    the Credential-Proxy wiring kept intact (`--network`, proxy env, CA mount + synchronous
    `update-ca-certificates`). No skills ro-mount.
  - `exec`: `docker exec -w /workspace <id> bash -lc <command>` — fresh per call, separate
    stdout/stderr, bounded by `timeout_s`; on timeout kill only the one `docker exec` client (its
    process group) — **container + filesystem survive**, `timed_out=True`, empty `note` (mirror
    modal). Daemon-lost→rendered-failure retained.
  - **file ops = plain pathlib on the bind-mounted workspace** (`self._workspace / rel`) — the mount
    makes the host dir BE the sandbox fs, so `read_bytes`/`write_bytes`/etc. are always truthful, zero
    remote plumbing.
  - `export`: no-op (mount is live). `destroy`: `docker rm -f <id>` (loop-independent, best-effort).
  - **DELETE** the persistent-shell machinery (`_ensure_shell`/`_stop_shell`/`_teardown_shell_clean`/
    `_kill_shell_loop_free`/`_neutralize_shell_transports`/`_underlying_popen`/`_read_until_marker`/
    `_build_payload`/`_recover_stdout`/`_parse_exit_code`/`_make_marker`/`_shell`/`_shell_loop` and the
    `_SHELL_RESET_NOTE`/`_SHELL_ENDED_*` constants).
- **`sandbox/__init__.py`**: `select_executor("docker")` → `SandboxExecutor(DockerBackend())`;
  `"modal"` → the existing `ModalExecutor()` (unchanged); update `__getattr__`/`__all__`.
  `select_executor` keeps its `CommandExecutor` signature.
- **`runtime/flow.py::_sandbox_proxy`**: build `SandboxExecutor(DockerBackend(network=…, proxy_env=…,
  ca_cert_host_path=…))`; `install_executor` unchanged; the installed executor is `start()`ed with the
  workspace.
- **`tui/app.py`**: warm-up passes the workspace — `warm_executor(workspace_dir(Path.cwd()))`.
  `deps.cwd` stays the launch cwd this task. `none` skips the block (byte-identical).
- **Delete `src/decode/sandbox/docker_executor.py`.**
- **bash description**: rewrite the docker suffix to fresh-exec semantics (`cd`/`export` do NOT persist
  — chain in one command; `/workspace` bind-mounted; fs persists; a timeout kills the one command,
  container + fs survive). Unified in 082.
- **Test migration (keep green):** rewrite `test_docker_executor.py` → `SandboxExecutor`+`DockerBackend`
  contract (exec + the new file ops); update `test_sandbox_teardown.py` docker parts +
  `test_credential_proxy.py`; minimal `test_sandbox_capstone.py` edits (imports + docker smoke) to
  stay green. Modal tests untouched.

## Acceptance criteria

- [ ] `SANDBOX_MODE=docker` → `SandboxExecutor(DockerBackend)`; `modal` → unchanged `ModalExecutor`;
  `none` → `LocalExecutor`. Construction inert.
- [ ] **Real docker (skipif):** two `run()`s share the container fs but `cd`/`export` do NOT persist
  across calls (the deleted-persistent-shell proof).
- [ ] **Real docker (skipif):** `run("sleep 100", timeout_s=1)` → `timed_out=True`, partial output,
  `note==""`, container + a previously-written file survive.
- [ ] **Real docker (skipif):** `DockerBackend` file ops (`read_bytes`/`write_bytes`/`list_dir`/
  `make_directory`/`remove`/`stat`) round-trip against the bind-mounted workspace and a file written
  by `bash` is visible via `read_bytes` (mount = one truthful tree).
- [ ] **Real docker (skipif):** `aclose()` removes the container; `[sandbox]` start/stop logs emitted;
  the Credential-Proxy boundary still holds via `SandboxExecutor(DockerBackend(proxy…))`.
- [ ] `grep` proves the persistent-shell machinery + `docker_executor.py` are gone from `src/`.
- [ ] Invariants intact: `import decode.cli` imports no kitaru; `none` imports no sandbox module.
- [ ] `make ci` green, 0 warnings, `uv lock --check` passes; docker tests migrated; modal untouched.

## Out of scope

- Modal backend (080); file-tool routing through the seam (081); clone/CLI (082); capstone rewrite
  (085). Replay-safety `{"cache": False}` — unchanged, do not regress.

## Log
