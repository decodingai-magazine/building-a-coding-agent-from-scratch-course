---
id: 079-sandbox-executor-docker-backend
feature: isolated-workspace
status: done
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

- [x] `SANDBOX_MODE=docker` → `SandboxExecutor(DockerBackend)`; `modal` → unchanged `ModalExecutor`;
  `none` → `LocalExecutor`. Construction inert.
- [x] **Real docker (skipif):** two `run()`s share the container fs but `cd`/`export` do NOT persist
  across calls (the deleted-persistent-shell proof).
- [x] **Real docker (skipif):** `run("sleep 100", timeout_s=1)` → `timed_out=True`, partial output,
  `note==""`, container + a previously-written file survive.
- [x] **Real docker (skipif):** `DockerBackend` file ops (`read_bytes`/`write_bytes`/`list_dir`/
  `make_directory`/`remove`/`stat`) round-trip against the bind-mounted workspace and a file written
  by `bash` is visible via `read_bytes` (mount = one truthful tree).
- [x] **Real docker (skipif):** `aclose()` removes the container; `[sandbox]` start/stop logs emitted;
  the Credential-Proxy boundary still holds via `SandboxExecutor(DockerBackend(proxy…))`.
- [x] `grep` proves the persistent-shell machinery + `docker_executor.py` are gone from `src/`.
- [x] Invariants intact: `import decode.cli` imports no kitaru; `none` imports no sandbox module.
- [x] `make ci` green, 0 warnings, `uv lock --check` passes; docker tests migrated; modal untouched.
  (repo-wide `lint-check` still reports the pre-existing 2 `E402`s in the untracked WIP
  `src/decode/tools/substack_summarizer.py` — out of scope; every file this task touches is clean.)

## Out of scope

- Modal backend (080); file-tool routing through the seam (081); clone/CLI (082); capstone rewrite
  (085). Replay-safety `{"cache": False}` — unchanged, do not regress.

## Log

### [SWE] 2026-07-04 — Implementation

**Files modified**
- `src/decode/sandbox/executor.py` (NEW) — `SandboxExecutor` (a `CommandExecutor`) + the `SandboxBackend`
  Protocol (create/exec/file-ops/export/destroy) + the `FileStat` dataclass. Fresh-exec `run`, `start`
  (seeds skills + creates), `aclose` (export→destroy), workspace-resolution fallback, and a crash-free
  render of backend-create failures (exit-125).
- `src/decode/sandbox/docker_backend.py` (NEW) — `DockerBackend`: `docker run` keeper + kept
  Credential-Proxy wiring (network/proxy_env/CA mount + synchronous `update-ca-certificates`); fresh
  `docker exec -w /workspace <id>` per call with separate streams + process-group-kill timeout; pathlib
  file ops on the bind mount; `export` no-op; `destroy` = loop-free `docker rm -f`.
- `src/decode/sandbox/docker_executor.py` (DELETED) — the whole persistent-shell/marker/`$?`/shell-reset
  machinery is gone.
- `src/decode/sandbox/__init__.py` — `select_executor("docker")` → `SandboxExecutor(DockerBackend())`;
  `modal` unchanged; PEP-562 `__getattr__` resolves `SandboxExecutor`/`DockerBackend`/`ModalExecutor`.
- `src/decode/runtime/flow.py` — `_sandbox_proxy` builds `SandboxExecutor(DockerBackend(proxy…))`,
  installs it, and eagerly `start()`s it (new `_start_runtime_executor` helper, dedicated loop) against
  `prepare_workspace(Path.cwd())`.
- `src/decode/tui/app.py` — warm-up passes `workspace_dir(deps.cwd)` (lazy import keeps `none` clean).
- `src/decode/tools/bash.py` — `_DOCKER_DESCRIPTION_SUFFIX` rewritten to fresh-exec semantics; docstrings.
- `src/decode/sandbox/modal_executor.py`, `src/decode/sandbox/proxy.py` — stale `DockerExecutor` doc refs
  retargeted (doc-only; modal logic untouched).
- Tests: NEW `tests/unit/decode/sandbox/test_executor.py` + `test_docker_backend.py`; DELETED
  `tests/unit/decode/sandbox/test_docker_executor.py`; rewrote `tests/integration/test_docker_executor.py`
  to the fresh-exec contract; updated `test_select.py`, `test_bash_sandbox_selection.py`,
  `test_executor_teardown.py`, `test_bash.py`, `test_modal_executor.py`, `test_credential_proxy.py`,
  `test_sandbox_teardown.py`, and `test_sandbox_capstone.py` (imports + docker smoke + description pins).

**Tests**
- Unit: 1245 passing, 0 failing (`make unit-tests` — includes the two new sandbox test modules).
- Integration: 56 passing, 0 failing against a REAL docker daemon (docker executor, credential proxy,
  sandbox teardown, sandbox capstone, runtime) — no leaked containers/networks after (`docker ps -a` clean).

**Acceptance criteria** — all met; verified by:
- selection/inert → `tests/unit/decode/sandbox/test_select.py`, `test_executor.py::test_construction_is_inert`
- fs-persists-but-cd/export-don't → `tests/integration/test_docker_executor.py::test_filesystem_persists_but_cd_and_export_do_not`
- timeout (note=="", container+file survive) → `..._docker_executor.py::test_timeout_kills_the_command_but_the_container_and_fs_survive`
- file ops truthful tree → `..._docker_executor.py::test_file_ops_and_bash_share_one_truthful_tree`
- aclose+logs+proxy boundary → `..._docker_executor.py` (aclose/logs) + `tests/integration/test_credential_proxy.py`
- machinery gone → `grep` clean (only prose docstrings remain); `docker_executor.py` deleted
- invariants → verified `import decode.cli` (no kitaru) + none path (no `decode.sandbox.*`)

**Evidence — red→green story for the deleted-shell semantics change**

The behavioral flip is fresh-exec: the retired `DockerExecutor` drove ONE persistent bash shell so
`cd`/`export` PERSISTED across `bash` calls; the new `DockerBackend` runs a fresh `docker exec` per call
so they do NOT. Old integration assertions encoded the old contract, e.g.
`test_shell_state_persists_across_run_calls` asserted `"42" in stdout and "/tmp" in stdout` after a
prior `export DECODE_X=42 && cd /tmp` — that test is DELETED with its shell. The replacement
`test_filesystem_persists_but_cd_and_export_do_not` asserts the inverse (`"[]"` and `"/workspace"`, with
the file still readable). Proven live against the real daemon in the Step-7 end-to-end run:

```
$ echo hello > work.txt && export TOKEN=secret && cd /tmp   → exit=0
$ cat work.txt; echo [$TOKEN]; pwd
  stdout='hello\n[]\n/workspace'      # file persists; TOKEN empty; cwd back at /workspace (fresh-exec)
$ echo out; echo err >&2   → stdout='out' stderr='err'      # separate streams (no merge)
$ sleep 100 (timeout_s=1)  → exit=-9 timed_out=True note='' # only the command dies
$ cat work.txt             → stdout='hello'                 # container + fs survive the timeout
[sandbox] docker start 8a841e29…  →  [sandbox] docker stop 8a841e29…   # reaped, no leak
```

Likewise the timeout assertion flipped from `"reset" in note.lower()` (shell killed+restarted) to
`note == ""` (only the exec dies, container survives) — mirrored in the unit
`test_docker_backend.py::test_exec_timeout_kills_only_the_client_process_group` (a real `sleep` child
stands in, no daemon).

**Notes**
- `_sandbox_proxy` now EAGERLY starts the worker (per the task) so the CA is trusted before the first
  bash; `test_credential_proxy.py`'s two context tests were updated (chdir to tmp for hermeticity; assert
  the worker container is reaped too — it previously asserted "worker never started").
- Modal path: only 2 doc comments retargeted; `ModalExecutor` logic and its tests are untouched. app.py
  now passes `workspace_dir(cwd)` to `warm_executor`; ModalExecutor's own workspace/skills rewiring is
  080 (its skipif smokes don't run here without creds), a deliberate deferral per the task's staging.
- `FileStat` is minimal (`path`/`is_dir`/`size`) — the least the 081 file-tool routing needs; extend
  there if a concrete consumer wants mtime/symlink.
- DO NOT COMMIT — handing to the Tester first. The untracked WIP `substack_summarizer.py` + `docs/notes/`
  are the user's; I did not touch them (they account for the only repo-wide `lint-check` failures).

### [Tester] 2026-07-04 00:20 — QA

**Test summary**
- Format-check (touched files + repo-wide): PASS (`ruff format --check` — 168 files formatted)
- Lint-check (every file this task touches): PASS. Repo-wide lint-check FAILs on exactly 2 pre-existing
  `E402`s in the untracked WIP `src/decode/tools/substack_summarizer.py` — out of this task's scope,
  documented in AC8 + the QA brief, and in a file the task never touches. Not a 079 defect.
- Unit tests: 1245 passed / 0 failed (82.26s)
- Integration tests (REAL docker daemon): 56 passed / 0 failed (98.35s) — incl. `test_docker_executor`(8),
  `test_credential_proxy`(4), `test_sandbox_capstone`(17), `test_sandbox_teardown`(1),
  `test_runtime_capstone`(8), `test_modal_executor`(5, untouched)
- Warnings: 0 (`filterwarnings=["error"]` is in effect; a warning would fail the run)
- `uv lock --check`: PASS (149 packages, exit 0, no dependency drift)

**E2E adversarial pass** (all against the real Docker 29.4.3 daemon, exercising the live
`SandboxExecutor(DockerBackend())`)
- Happy path: `run("echo out; echo err >&2")` + file round-trips → separate streams, truthful mount
  (bash↔read_bytes, binary + nested parents, unicode filename/content) → PASS
- Break path 1 (timeout truthfulness — the sharp edge): `run("echo bg-started; sleep 100", timeout_s=1)`
  → dt=1.02s, `timed_out=True`, exit=-9, `note==""`, partial stdout `'bg-started'`, container + prior
  file survive. Verified what actually dies via `docker exec <id> ps`: the in-container `sleep` is
  **killed (0 survivors)** — even a `&`-backgrounded `sleep 300 &` leaves 0 survivors, 0 zombies;
  repeated timeouts don't accumulate and the container stays functional. Reality is *cleaner* than the
  code's hedged `ponytail:` note ("the in-container command may outlive it"), so the honesty note is
  conservative and correct — no documentation-honesty defect. → PASS
- Break path 2 (fresh-exec flip): fs persists across `run`s but `cd`/`export` do NOT (`f.txt` kept,
  `$DECODE_X` empty, cwd back to `/workspace`); stdout/stderr genuinely separate (not merged — the old
  merged-stream doc/tests are gone). → PASS
- Break path 3 (file ops through the mount): write/read round-trip, `stat` (missing → `None`, no crash),
  `list_dir`, `make_directory`, `remove` (file + tree + missing_ok) all truthful. `read_bytes` of a
  missing file raises a clean `FileNotFoundError` (a normal primitive exception 081 handles above the
  seam — not a tool crash). NON-BLOCKING NOTE: a backend-level `rel="../../x"` DOES escape the workspace
  — this is explicitly documented (`_path` docstring + ADR-0012 §4) as deferred to 081's above-the-seam
  containment path-math; in-scope-deferred, not a 079 defect. → PASS
- Break path 4 (teardown + loop-independence + mid-session loss): `aclose` from a fresh loop reaps the
  container, double-`aclose` is safe; the M8 leak-guard re-proven on the new stack — externally
  `docker rm -f`ing the container mid-session then `run()`ing again renders a failure (exit=1 + clear
  daemon stderr `No such container`), never crashes, and `aclose` leaves no leak. Real headless
  `decode run` reap proven green by `test_sandbox_teardown`. → PASS
- Break path 5 (credential proxy on the new stack): `test_credential_proxy`(4) +
  `test_real_docker_credential_proxy_boundary` green — header arrives at the upstream, worker env is
  secret-free; `test_worker_trusts_the_proxy_ca_on_its_very_first_command` green (eager-start CA trust,
  no first-command race). → PASS
- Break path 6 (invariants): `import decode.cli` is kitaru-free; `none`-mode `_get_executor()` returns
  `LocalExecutor` and imports no `decode.sandbox.{executor,docker_backend,modal_executor}`; `tools/exec.py`
  (LocalExecutor) untouched → none-mode byte-identical; replay-safety `{"cache": False}` block untouched
  (0 `cache` hits in the flow.py diff; `test_build_runtime_agent_disables_bash_cache_in_{docker,modal}_mode`
  green). → PASS
- Cost hygiene: containers 6→6, networks 13→13 after every probe + the full integration run; zero
  `decode-*`, `uv:python3.12`, or `mitmproxy` leftovers. → PASS

**Acceptance criteria** (all verified — checkboxes confirmed `[x]`)
- [x] PASS — `docker`→`SandboxExecutor(DockerBackend)`, `modal`→`ModalExecutor`, `none`→`LocalExecutor`,
      inert — `test_select.py`, `test_executor.py::test_construction_is_inert`, INV2 probe
- [x] PASS — two `run()`s share fs but `cd`/`export` don't persist —
      `test_docker_executor.py::test_filesystem_persists_but_cd_and_export_do_not` + probe_timeout
- [x] PASS — `sleep 100`@1s → `timed_out=True`, partial output, `note==""`, container + file survive —
      `test_timeout_kills_the_command_but_the_container_and_fs_survive` + probe_timeout/probe_bg
- [x] PASS — file ops round-trip on the mount + bash-written file visible via `read_bytes` —
      `test_file_ops_and_bash_share_one_truthful_tree` + probe_fileops
- [x] PASS — `aclose()` removes the container; `[sandbox]` start/stop logs; proxy boundary holds —
      `test_aclose_removes_the_container...`, `test_observability_logs...`, `test_credential_proxy`
- [x] PASS — persistent-shell machinery + `docker_executor.py` gone from `src/` — grep clean (only a
      prose docstring mentions the retired `DockerExecutor`)
- [x] PASS — `import decode.cli` kitaru-free; `none` imports no sandbox module — INV1/INV2 probes +
      `test_none_mode_agent_imports_no_sandbox_executor_module`
- [x] PASS — full suite green, 0 warnings, `uv lock --check` passes, docker tests migrated, modal
      untouched (doc-only 2-comment diff). CAVEAT: repo-wide `make ci`/`lint-check` is red SOLELY on the
      out-of-scope untracked WIP `substack_summarizer.py` (2 `E402`), exactly as AC8 documents — every
      file 079 touches is clean. The PR-reviewer/human should note this file blocks a real CI run.

**Evidence**
```
$ make unit-tests
======================= 1245 passed in 82.26s (0:01:22) ========================
$ make integration-tests   # real docker daemon
tests/integration/test_docker_executor.py ........                       [ 23%]
tests/integration/test_credential_proxy.py ....                          [  8%]
tests/integration/test_sandbox_capstone.py .................             [ 98%]
tests/integration/test_sandbox_teardown.py .                             [100%]
======================== 56 passed in 98.35s (0:01:38) =========================
$ uv lock --check
Resolved 149 packages in 2ms          # exit 0

# timeout truthfulness (real daemon)
timeout run: dt=1.02s timed_out=True exit=-9 note='' stdout='bg-started\n'
in-container sleep processes right after timeout: 0        # even `&`-backgrounded: 0 survivors
fs survived: 'survivor' (exit=0)
still functional after repeated timeouts: 'still-alive'    # no zombie accumulation

# mid-session container loss (leak-guard)
post-loss run: exit=1 note='' stderr='Error response from daemon: No such container: ...' (rendered, no crash)
aclose clean; container listing (empty=gone): ''
```

**Other issues found**
- None blocking. Notes for the record (orchestrator/PR-reviewer to weigh, not 079 fixes):
  1. Backend file ops do NOT contain `..` escapes — by design, deferred to 081 (documented in `_path`
     + ADR-0012 §4). Confirm 081 lands the above-the-seam path-math before file tools route through here.
  2. Repo-wide `make ci` cannot pass until the untracked WIP `src/decode/tools/substack_summarizer.py`
     (2 `E402`) is cleaned or removed — out of scope for 079, but a real-CI blocker to track.
  3. `SandboxExecutor`/`DockerBackend` are documented single-call (not concurrency-safe); decode's
     harness serializes `bash`, so this is fine — just flagging the assumption.

**VERDICT: PASS**
