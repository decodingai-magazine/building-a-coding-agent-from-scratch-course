---
id: 072-sandbox-docker-executor
feature: sandboxing
status: pending
---

# Docker sandbox executor — one session-persistent container + a persistent bash shell

Tags: `sandbox`, `infra`
Depends on: #071
Blocks: #074, #075, #077

This task implements ADR-0011 §2. It adds `DockerExecutor` — a `CommandExecutor` (the `tools/exec.py`
Protocol) that runs model-chosen commands inside **one session-persistent Docker container** over the
**bind-mounted** cwd, driving a **single long-lived bash shell** so `cd`/`export`/installs persist
across `bash` calls (the canonical DockerSandbox shape). It ships as a directly-tested class in the new
`src/decode/sandbox/` package; it is **not yet wired** into `bash` (that is task 074) — mirroring how
task 057 shipped settings ahead of readers. Access is via the **standard docker CLI**, shelled out with
`asyncio` subprocess (dependency-free; the docker Python SDK is only transitively present via zenml and
must not be relied on — ADR-0011 Alternatives).

## Scope

- **Module:** `src/decode/sandbox/__init__.py` + `src/decode/sandbox/docker_executor.py` with
  `class DockerExecutor` implementing `async run(command, *, cwd, timeout_s) -> ExecResult`.
- **Container lifecycle (lazy, one per session):** on the first `run()`, start the keeper container:
  `docker run -d --rm -v <abs cwd>:/workspace -w /workspace <settings.sandbox_image> sleep infinity`;
  capture the container id. Reuse it for every later command. `--rm` is the crash backstop; explicit
  teardown lands in 074's exit-path wiring. Expose an `async aclose()` that stops/removes the container
  (idempotent, best-effort) — 074 calls it.
- **Persistent-shell command protocol (the teaching heart — spec it exactly):**
  - Start ONE long-lived shell: `docker exec -i <id> bash --noprofile --norc` as an `asyncio`
    subprocess; hold its stdin/stdout. No TTY (`-i`, not `-it`) → clean pipes. Lazily (re)spawned.
  - Per command: generate a unique end marker (`__DECODE_END_<uuid4hex>__`); write to the shell stdin:
    the command, a newline, then `printf '%s %s\n' "<MARKER>" "$?"` (so the marker line carries the
    command's exit code); flush. Read the shell's stdout line-by-line **until** a line beginning with
    `<MARKER> ` — everything before it is the command's output; parse the trailing int as `exit_code`.
  - **Stream handling (verify-first, pick the simplest that passes the contract):** default to merging
    stderr into stdout at shell start (`exec 2>&1` as the shell's first line), so `ExecResult.stderr`
    stays `""` and `bash._render` shows one combined section; `ponytail:` a two-marker / separate-fd
    scheme would split the streams — merged is the honest simple capture for the tutorial. Record the
    chosen approach in the log.
- **Timeout contract (ADR-0011 §2):** bound the read-until-marker by `timeout_s`. On timeout: **kill the
  persistent shell** (terminate the `docker exec` subprocess) and mark it for lazy respawn on the next
  `run()` — which **resets shell state** (cwd back to `/workspace`, env cleared). Return
  `ExecResult(stdout=<partial output read so far>, stderr="", exit_code=<negative/kill sentinel>,
  timed_out=True, note=<shell-reset message>)`. `ponytail:` decode cannot surgically kill one hung
  in-container command while keeping the session — restart is the simple honest rule; a per-command
  PID/cgroup + `docker exec … kill` is the upgrade path (comment it).
- **`ExecResult.note` plumbing:** add an optional `note: str = ""` field to `ExecResult` in
  `tools/exec.py` (backward-compatible default), and have `bash._render` append the note when non-empty.
  `LocalExecutor` (and later `ModalExecutor`) leave it `""` → `none`-mode rendering is **byte-identical**.
  `DockerExecutor` sets it to the shell-reset notice on timeout so the model is told the state was reset.
- **Observability (ADR-0011 §7):** logger lines (never `print`) — on container start: `[sandbox] docker
  start <container-id> image=<image>` (INFO); per command: `[sandbox] $ <cmd>` → `exit=<code>
  bytes=<stdout-size> cwd=<container cwd>` (DEBUG); on teardown: `[sandbox] docker stop <container-id>`
  (INFO). Never log command *output* at INFO.
- **Justification (record in log + it's in ADR):** docker **CLI** over the SDK — dependency-free, mirrors
  `LocalExecutor`'s asyncio-subprocess style, teaches the real commands, and makes gVisor/Kata zero-code
  daemon-config upgrades.
- **Deviation note (in module docstring + ADR §2):** canonical Stage 2 mounts an empty named volume;
  decode **bind-mounts the cwd** so host-side file tools + the real repo are one tree.

## Acceptance criteria

- [ ] `DockerExecutor.run` satisfies the `CommandExecutor` Protocol and returns an `ExecResult` with the
  real exit code, output, and `timed_out=False` for a normal command — proven against a **real docker
  daemon** in a `@pytest.mark.skipif(no docker)` integration test (e.g. `run("echo hi")` → `stdout`
  contains `hi`, `exit_code == 0`).
- [ ] **Shell state persists across `run()` calls:** in one `DockerExecutor` instance,
  `run("export DECODE_X=42 && cd /tmp")` then `run("echo $DECODE_X && pwd")` returns `42` and `/tmp`
  (skipif-docker) — the marker/persistent-shell mechanism, not per-call `docker exec`.
- [ ] The **marker protocol** is honored: a command whose output itself contains a marker-like string
  does not truncate early (unique per-call marker); a non-zero exit is reported (`run("false")` →
  `exit_code != 0`). (skipif-docker.)
- [ ] **Timeout resets the shell:** a `run("sleep 100", timeout_s=1)` returns `timed_out=True` with the
  reset `note`; a subsequent `run("pwd")` shows `/workspace` (state reset) and works — proving the shell
  respawned. No orphaned host process leaks. (skipif-docker.)
- [ ] `ExecResult` gains `note: str = ""`; `bash._render` appends a non-empty note; a **hermetic** unit
  test (no docker) constructs `ExecResult(..., note="…")` and asserts `_render` includes it, and that an
  empty note leaves output **byte-identical** to today.
- [ ] `aclose()` stops/removes the container (skipif-docker: `docker ps` shows the id gone after) and is a
  safe no-op if never started; a double-`aclose()` does not raise.
- [ ] Observability: container start/stop + each command are logged (id + image on start; `$ cmd` →
  exit/bytes on exec; stop on teardown) — asserted via `caplog` in the skipif-docker test.
- [ ] Tests are **hermetic under `filterwarnings=["error"]`**: no unclosed-subprocess/transport
  `ResourceWarning`; the executor closes its shell + container deterministically (run the docker test
  alone under `-W error`).
- [ ] `make ci` green with 0 warnings **without** a docker daemon (the docker tests SKIP, not fail);
  `uv lock --check` passes (no new dep — docker CLI is shelled out).

## Out of scope

- Wiring `DockerExecutor` into `bash`'s selection seam + the mode-specific description (074).
- The Credential Proxy wiring (network / proxy env / CA mount) — 075 extends this executor.
- The headless-flow replay-safety checkpoint config (075).
- Modal (073).

## Log
