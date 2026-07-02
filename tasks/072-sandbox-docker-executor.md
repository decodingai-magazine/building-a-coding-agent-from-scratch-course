---
id: 072-sandbox-docker-executor
feature: sandboxing
status: done
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

- [x] `DockerExecutor.run` satisfies the `CommandExecutor` Protocol and returns an `ExecResult` with the
  real exit code, output, and `timed_out=False` for a normal command — proven against a **real docker
  daemon** in a `@pytest.mark.skipif(no docker)` integration test (e.g. `run("echo hi")` → `stdout`
  contains `hi`, `exit_code == 0`).
  <!-- Tester 2026-07-03: unchecked (no-trailing-newline hang). SWE 2026-07-03: FIXED — the marker
  printf now leads with a newline (so the marker always lands on its own line) and run() strips that
  one trailing newline back off, so no-trailing-newline output is recovered exactly. Verified by
  tests/integration/test_docker_executor.py::test_run_handles_output_without_a_trailing_newline
  (echo -n hi → "hi", exit 0, timed_out False, fast) + hermetic
  test_no_newline_output_recovers_faithfully_via_the_marker_and_strip. Re-ticked. -->
- [x] **Shell state persists across `run()` calls:** in one `DockerExecutor` instance,
  `run("export DECODE_X=42 && cd /tmp")` then `run("echo $DECODE_X && pwd")` returns `42` and `/tmp`
  (skipif-docker) — the marker/persistent-shell mechanism, not per-call `docker exec`.
- [x] The **marker protocol** is honored: a command whose output itself contains a marker-like string
  does not truncate early (unique per-call marker); a non-zero exit is reported (`run("false")` →
  `exit_code != 0`). (skipif-docker.)
- [x] **Timeout resets the shell:** a `run("sleep 100", timeout_s=1)` returns `timed_out=True` with the
  reset `note`; a subsequent `run("pwd")` shows `/workspace` (state reset) and works — proving the shell
  respawned. No orphaned host process leaks. (skipif-docker.)
- [x] `ExecResult` gains `note: str = ""`; `bash._render` appends a non-empty note; a **hermetic** unit
  test (no docker) constructs `ExecResult(..., note="…")` and asserts `_render` includes it, and that an
  empty note leaves output **byte-identical** to today.
- [x] `aclose()` stops/removes the container (skipif-docker: `docker ps` shows the id gone after) and is a
  safe no-op if never started; a double-`aclose()` does not raise.
- [x] Observability: container start/stop + each command are logged (id + image on start; `$ cmd` →
  exit/bytes on exec; stop on teardown) — asserted via `caplog` in the skipif-docker test.
- [x] Tests are **hermetic under `filterwarnings=["error"]`**: no unclosed-subprocess/transport
  `ResourceWarning`; the executor closes its shell + container deterministically (run the docker test
  alone under `-W error`).
- [x] `make ci` green with 0 warnings **without** a docker daemon (the docker tests SKIP, not fail);
  `uv lock --check` passes (no new dep — docker CLI is shelled out).

## Out of scope

- Wiring `DockerExecutor` into `bash`'s selection seam + the mode-specific description (074).
- The Credential Proxy wiring (network / proxy env / CA mount) — 075 extends this executor.
- The headless-flow replay-safety checkpoint config (075).
- Modal (073).

## Log

### [SWE] 2026-07-03 — Implementation

**Verify-first (real docker 29.4.3, daemon brought up locally)**
Confirmed every docker CLI command shape before coding, against a live container:
- Keeper container: `docker run -d --rm -v <abs cwd>:/workspace -w /workspace python:3.12-slim sleep infinity` → returns a container id.
- Persistent shell: `docker exec -i <id> bash --noprofile --norc` (no TTY). Writing `export DECODE_X=42 && cd /tmp\nprintf '%s %s\n' "<marker>" "$?"\n` then a second `echo $DECODE_X && pwd\nprintf …` on the SAME shell returned `42` / `/tmp` — proving `cd`/`export` persist across logical commands in one shell, and the marker line carries `$?`.
- Non-zero exit: `false` → marker line `<marker> 1`.
- Teardown: `docker rm -f <id>` → `docker ps -aq --filter id=<id>` empty.

**Stream-handling choice (as specced):** merged. The `docker exec` subprocess is created with `stderr=asyncio.subprocess.STDOUT` (one pipe → nothing extra to drain/close under `filterwarnings=error`) AND `exec 2>&1` is written as the shell's first line (so a command's own stdout/stderr interleave in the command's order before docker demuxes). `ExecResult.stderr` therefore stays `""` and `bash._render` shows one combined section. `ponytail:` a two-marker/separate-fd split-stream scheme is the noted alternative.

**Files modified**
- `src/decode/sandbox/docker_executor.py` (new) — `DockerExecutor` (the `CommandExecutor` seam): lazy one-container-per-session (`docker run … sleep infinity`), one long-lived `docker exec -i bash` shell driven by the unique-marker (`__DECODE_END_<uuid4hex>__`) + `$?` protocol, timeout = kill-the-shell-host-process-group (`start_new_session=True` + `os.killpg`, like `LocalExecutor`) + lazy respawn (state reset) + reset `note`, `aclose()` = `docker rm -f` (idempotent, best-effort). Docker types never leak (callers see only `ExecResult`).
- `src/decode/sandbox/__init__.py` (new) — package init re-exporting `DockerExecutor`; `select_executor` deferred to 074.
- `src/decode/tools/exec.py` — `ExecResult` gains `note: str = ""` (trailing default → positional back-compat; `LocalExecutor` never sets it).
- `src/decode/tools/bash.py` — `_render` appends `result.note` as a trailing section only when non-empty (empty note → byte-identical to before).
- `tests/unit/decode/sandbox/test_docker_executor.py` (new) + `tests/unit/decode/sandbox/__init__.py` — hermetic tests: marker/payload/parse helpers, the read-until-marker loop driven by a fake stdout (spoof-resistance + EOF + oversized-line, all offline), construction/aclose laziness (no subprocess spawned without a `run`).
- `tests/integration/test_docker_executor.py` (new) — `@pytest.mark.skipif(no docker daemon)` real-daemon tests: echo round-trip, cross-`run()` state persistence, marker-spoof resistance, non-zero exit, timeout→reset+note+respawn (env cleared, cwd back to `/workspace`), `aclose` removes the container (+ double-aclose safe), and `caplog` observability.
- `tests/unit/decode/tools/test_exec.py` — `note` default `""`, 4-positional-arg back-compat, `LocalExecutor` never sets `note`.
- `tests/unit/decode/tools/test_bash.py` — `_render` byte-identical when `note==""`, appends a non-empty note last, includes the note even with empty streams.

**Tests**
- Unit: hermetic — `sandbox/test_docker_executor.py` (15) + affected `test_exec.py` / `test_bash.py`. Full unit suite: **1107 passed**.
- Integration (real docker, daemon up): `tests/integration/test_docker_executor.py` **7 passed under `-W error`** (no ResourceWarning → deterministic shell/container teardown). Same file **7 skipped cleanly** (never failed) with the daemon unreachable (`DOCKER_HOST=unix:///nonexistent/docker.sock`).
- `make ci` with the daemon **unreachable**: **1128 passed, 7 skipped, 0 warnings, exit 0** (`uv lock --check` — CI's first step — passed: no new dependency, docker CLI is shelled out).

**Acceptance criteria** — all 9 met (none `[HUMAN]`), verified by:
- Protocol/echo/exit → `test_docker_executor.py::test_run_echo_round_trips_through_a_real_container`.
- State persistence → `::test_shell_state_persists_across_run_calls`.
- Marker protocol (spoof + non-zero) → `::test_marker_protocol_resists_a_spoofed_marker_in_output`, `::test_run_reports_a_non_zero_exit_code`, and hermetic `test_read_until_marker_*`.
- Timeout resets shell → `::test_timeout_kills_and_resets_the_persistent_shell`.
- `note` plumbing → `test_bash.py::test_render_is_byte_identical_when_note_is_empty` / `::test_render_appends_a_non_empty_note_after_the_streams`; `test_exec.py::test_exec_result_note_defaults_to_empty`.
- `aclose` → `::test_aclose_removes_the_container_and_is_idempotent`, hermetic `::test_aclose_is_a_safe_noop_when_never_started`.
- Observability → `::test_observability_logs_container_lifecycle_and_each_command`.
- Hermetic under `-W error` + `make ci` skip-clean → the runs above.

**Evidence (direct e2e invocation — DockerExecutor is not yet wired into `bash`; that is 074)**
```
[sandbox] docker start 436e27878432… image=python:3.12-slim
[sandbox] $ echo hello from the sandbox && python --version → exit=0 bytes=38 cwd=/workspace
[sandbox] $ export FOO=bar && cd /tmp → exit=0 bytes=0 cwd=/workspace
[sandbox] $ echo FOO=$FOO at $(pwd) → exit=0 bytes=16 cwd=/workspace          # R2: FOO=bar at /tmp  (persists)
[sandbox] $ sleep 100 timed out after 1s → shell reset (bytes=0)             # R3: timed_out=True exit=-9 + reset note
[sandbox] $ echo after-reset FOO=[$FOO] at $(pwd) → exit=0 bytes=33 cwd=/workspace  # R4: FOO=[] at /workspace (reset)
[sandbox] docker stop 436e27878432…
```
R1 stdout: `hello from the sandbox\nPython 3.12.13\n` (runs in the container). No containers leaked after any run (`docker ps -a --filter ancestor=python:3.12-slim` empty).

**Notes / deliberate scope**
- **Real-docker evidence is present** (daemon was up; integration + e2e ran for real). A Tester on a daemon-less machine will see the 7 integration tests SKIP — that is the designed behavior, and the hermetic unit tests + `make ci` still fully gate.
- **Not wired into `bash`** (selection seam + mode-specific description) — that is task 074, explicitly out of scope; `bash` was touched only for the `_render` note plumbing.
- **Deviations/ceilings recorded in the module docstring + `ponytail:` comments:** bind-mount the cwd vs the canonical empty named volume; timeout cannot surgically kill one hung in-container command (kill+restart the shell is the rule; per-command PID/cgroup + `docker exec … kill` is the upgrade path — the in-container process is reaped when the container is); a single output line longer than the 8 MiB stream buffer resets the shell (chunked reads are the upgrade path); partial-output-on-timeout is the complete lines read before the deadline (a buffered final partial line may be dropped).
- **Defensive (beyond the ACs, kept minimal):** a shell that ends before its marker (e.g. the command runs `exit`) or a broken stdin resets the shell and returns a distinct `note` rather than crashing — covered by the hermetic EOF/oversized-line read tests.
- **NOT committed** — handing to the Tester first per the lifecycle.

### [Tester] 2026-07-03 00:45 — QA

**VERDICT: FAIL** — the full suite is green and 8/9 ACs verify, but the e2e adversarial pass found a
blocking break path: **any command whose stdout lacks a trailing newline hangs for the full timeout,
falsely reports `timed_out=True`, and destroys the persistent shell's session state.** This hits
extremely common commands (`echo -n`, `printf 'x'`, `python -c "print(end='')"`, anything piped
through `tr -d '\n'`, many JSON/data emitters). Per the rubric a failing adversarial break path is a
FAIL. Fix + regression test required.

**Test summary** (real docker daemon 29.4.3, Docker Desktop up)
- Format / lint / pre-commit: PASS (`ruff format --check` 150 files clean; `ruff check` clean).
- Unit tests: 1107 passed / 0 failed (`make unit-tests`, 72.5s).
- Integration tests: 28 passed / 0 failed with daemon up (`make integration-tests`) — includes the 7
  `test_docker_executor.py` real-daemon tests.
- `make ci` with daemon UNREACHABLE (`DOCKER_HOST=unix:///nonexistent/docker.sock`): **1128 passed,
  7 skipped, 0 warnings, exit 0** (docker tests SKIP cleanly; `uv lock --check` passes — no new dep).
- Hermetic `-W error`: `pytest tests/integration/test_docker_executor.py -W error` → 7 passed, no
  ResourceWarning. Same file with bogus `DOCKER_HOST` → 7 skipped, exit 0.
- Warnings: 0.

**E2E adversarial pass** (drove `DockerExecutor` directly — not yet wired into `bash`, that is 074)
- Happy path: `run("echo hi")` → `stdout='hi\n' exit=0 timed_out=False` (PASS).
- Break path 1 (marker spoof HARD mode — exact `__DECODE_END_<hex> 42` shape, wrong uuid, printed
  mid-output before `false`): `stdout` captured the spoof line, `exit_code=1` NOT 42 (PASS — spoof
  resistant).
- **Break path 2 (boundary: output with NO trailing newline)** — `run("printf 'tail-no-newline'")`,
  `run("echo -n bare")`, `run("python3 -c \"print('x', end='')\"")`: all three → **`elapsed≈3.0s`
  (full timeout), `exit=-9`, `timed_out=True`**, and `stdout` shows the smoking gun
  `bare__DECODE_END_67c4...__ 0\n` — the marker printf concatenated onto the command's last line, so
  `line.startswith(marker_prefix)` never matches. **FAIL.** (details + fix below).
- Break path 3 (interleaving: `echo out; echo err 1>&2; false`): `stdout='out\nerr\n' stderr=''
  exit_code=1` (PASS — merged, ordered, `false` exit surfaced).
- Break path 4 (10 rapid sequential runs on one executor): 1 stable container id, host `docker exec`
  procs steady at 1 throughout, container gone + 0 host procs after `aclose` (PASS — no leaks).
- Break path 5 (timeout after an in-container `sleep 500 &`): `timed_out=True` + reset note; post-reset
  `echo [$SESSION_VAR] && pwd` → `[]\n/workspace\n` (env cleared, cwd reset); host `docker exec` procs
  1→1 (no host zombie); final sweep 0 stray procs, 0 leaked containers (PASS — in-container process
  outliving the kill is the documented ceiling).
- Break path 6 (unicode/binary: `printf '\xff\xfe caf\xc3\xa9 \xf0\x9f\x98\x80'`): →
  `'�� café 😀END\n' exit=0` — undecodable bytes replaced, valid UTF-8 preserved, no crash (PASS).
- Break path 7 (large output `seq 1 50000`): 50000 lines in 0.22s, exit=0, not truncated at executor
  level (PASS).
- Break path 8 (empty/whitespace command straight to the executor): `run("")` and `run("   ")` →
  exit=0, empty stdout, fast; shell still alive after (PASS — executor is robust even though `bash`
  guards empties upstream).
- Break path 9 (multi-line / heredoc through the newline-delimited protocol): `cat <<EOF...` →
  `line1\nline2\n` exit=0; `echo a\necho b\nfalse` → `a\nb\n` exit=1 (PASS).
- Break path 10 (cwd with a SPACE in the path): bind mount works both ways — `ls && cat 'marker
  file.txt' && echo OK` → `marker file.txt\nhello-spaced\nOK\n` exit=0, and a container-side
  `echo ... > written.txt` appears host-side as `from-container\n` (PASS — read + write-back).
- Break path 11 (secondary: command that reads stdin — `run("cat")`): `cat` consumes the marker
  printf from the shared pipe and echoes it (`stdout='printf \'%s %s\\n\' "__DECODE_END_..."...'`),
  never emits the marker → full-timeout hang + reset; recovers on the next run (PASS-with-caveat —
  degrades to timeout+reset, an inherent single-pipe-shell ceiling; see Other issues).

**Acceptance criteria**
- [ ] FAIL — AC1 "real exit code, output, `timed_out=False` for a normal command".
      Expected: a normal command returns `timed_out=False` with its real output/exit.
      Actual: the `echo hi` case passes, but `echo -n bare` / `printf 'x'` (no trailing newline) →
      `timed_out=True exit=-9` after a full-timeout hang, and the persistent shell is reset.
      Fix: emit the marker on its own line — `printf '\n%s %s\n' "<marker>" "$?"` in `_build_payload`
      (`docker_executor.py:301`) — then strip exactly ONE trailing `\n` from the joined output on the
      successful-marker path in `run` (`docker_executor.py:163`). The collected bytes are then always
      `S + "\n"`, so one strip recovers `S` faithfully whether or not `S` ended in a newline. Add a
      real-daemon regression test (`echo -n hi` → `stdout=="hi"`, `exit_code==0`, `timed_out is False`)
      and a hermetic `_read_until_marker` case where the last output chunk has the marker on its own
      line after a no-newline payload line.
- [x] PASS — AC2 shell state persists across `run()` — `::test_shell_state_persists_across_run_calls`;
      adversarial break path 5 (pre-reset persistence).
- [x] PASS — AC3 marker protocol (spoof-resistant + non-zero exit) —
      `::test_marker_protocol_resists_a_spoofed_marker_in_output`, `::test_run_reports_a_non_zero_exit_code`;
      adversarial break path 1 (hard-mode spoof → exit 1 not 42). NOTE: the marker *detection* is what
      breaks in the AC1 no-newline case; this AC's specific clauses (printed-marker + non-zero) hold.
- [x] PASS — AC4 timeout resets the shell, no orphaned host process leaks —
      `::test_timeout_kills_and_resets_the_persistent_shell`; adversarial break path 5.
- [x] PASS — AC5 `ExecResult.note` + `bash._render` (byte-identical empty; appended non-empty) —
      `test_bash.py::test_render_is_byte_identical_when_note_is_empty`,
      `::test_render_appends_a_non_empty_note_after_the_streams`,
      `::test_render_includes_the_note_even_when_streams_are_empty`,
      `test_exec.py::test_exec_result_note_defaults_to_empty`,
      `::test_exec_result_accepts_four_positional_args_for_backward_compat`.
- [x] PASS — AC6 `aclose` removes container + safe no-op + double-aclose —
      `::test_aclose_removes_the_container_and_is_idempotent`,
      `test_docker_executor.py (unit)::test_aclose_is_a_safe_noop_when_never_started`; adversarial
      break path 4 (container gone after aclose).
- [x] PASS — AC7 observability logs (id+image start; `$ cmd`→exit/bytes; stop) —
      `::test_observability_logs_container_lifecycle_and_each_command`.
- [x] PASS — AC8 hermetic under `filterwarnings=["error"]` — docker test alone `-W error` → 7 passed,
      no ResourceWarning.
- [x] PASS — AC9 `make ci` green 0 warnings without a daemon (tests skip) + `uv lock --check` —
      `DOCKER_HOST=…nonexistent… make ci` → 1128 passed / 7 skipped / 0 warnings / exit 0.

**Evidence**
```
$ DOCKER_HOST=unix:///nonexistent/docker.sock make ci
================= 1128 passed, 7 skipped in 119.60s (0:01:59) ==================
=== CI (daemon down) EXIT: 0 ===

$ pytest tests/integration/test_docker_executor.py -W error   # daemon up
============================== 7 passed in 3.89s ===============================

# adversarial break path 2 (the blocker) — driven directly against a real container:
[printf-no-nl] elapsed=3.16s exit=-9 timed_out=True stdout='tail-no-newline__DECODE_END_eb4e...__ 0\n'
[echo -n]      elapsed=3.03s exit=-9 timed_out=True stdout='bare__DECODE_END_67c4...__ 0\n'
[python end=''] elapsed=3.02s exit=-9 timed_out=True stdout='x__DECODE_END_3c20...__ 0\n'
```

**Other issues found**
- **(Blocking, above) No-trailing-newline output → hang + false timeout + state reset.** Root cause:
  `_build_payload` puts `printf '%s %s\n'` (no leading newline) right after the command, so a command
  whose stdout has no final newline shares its last line with the marker. This is NOT in the module's
  named ceilings and is a very common input class. Fix in AC1 above.
- **(Secondary, documentation/robustness) A command that reads stdin (`cat`, a REPL, `read`) consumes
  the marker printf from the shell's shared stdin pipe** → the marker is eaten, the read hangs to the
  timeout, the shell resets (recoverable). This is an inherent single-persistent-pipe limitation. At
  minimum add it to the module docstring's ceilings alongside the 8 MiB line; the sturdier fix is to
  feed each command its own stdin (e.g. run commands with `< /dev/null` unless a heredoc supplies
  stdin), which is a larger design change — orchestrator's call whether to fix now or document + defer.
- (Nit, non-blocking) The `_SHELL_ENDED_NOTE` text enumerates "the command exited the shell, or
  produced an extremely long unbroken line" but the no-newline and stdin-eating cases surface via the
  *timeout* path (`_SHELL_RESET_NOTE`), so the model is told "exceeded its timeout" for a command that
  finished instantly. Once break path 2 is fixed this mostly disappears; worth a glance.

**Re-review checklist for the SWE**
1. Fix `_build_payload` + the one-newline strip in `run`; keep `_render` byte-identical.
2. Add the real-daemon regression (`echo -n hi`) + a hermetic no-newline `_read_until_marker` case.
3. Decide (with orchestrator) fix-vs-document for the stdin-reading ceiling; update the docstring
   either way.
4. Re-run `make unit-tests`, `make integration-tests` (daemon up), `pytest …test_docker_executor.py
   -W error`, and `DOCKER_HOST=bogus make ci` — all green, 0 warnings — then hand back.

### [SWE] 2026-07-03 01:08 — Fixes (round 2)

**VERDICT sought: re-review.** Both the BLOCKING no-trailing-newline hang and the SECONDARY
stdin-reader hang are fixed with a **single payload change**, regression-tested first (watched red on
the pre-fix payload, green after), with **zero regression** on the 7 original real-daemon tests.

**Root fix — one payload shape carries both fixes.** `_build_payload` now wraps the command in a
**brace group with stdin from `/dev/null`, marker `printf` inside the group**:

```
{ <command>
printf '\n%s %s\n' "<marker>" "$?"
} </dev/null
```

- **BLOCKING (no trailing newline):** the marker `printf` now **leads with `\n`**, so the marker
  always lands on its own line even when the command's output has no final newline (`echo -n hi`,
  `printf 'x'`). The bytes collected before the marker are then always `S + "\n"`; the successful-marker
  path in `run()` strips exactly one trailing `\n` (new `_recover_stdout`, `removesuffix(b"\n")` — a
  no-op if somehow absent, so never over-strips) to recover `S` faithfully. `echo hi` still renders
  `"hi\n"` (single strip, no double-strip regression).
- **SECONDARY (stdin-reader):** the group's `</dev/null` starves a stdin-reading command (`cat`,
  `read`, a REPL) — it sees EOF instead of stealing the marker `printf` off the shared stdin pipe. A
  **brace group (not a subshell)** runs in the current shell, so `cd`/`export` persistence (AC2) is
  preserved; a heredoc supplies its own stdin and overrides the redirect. I took the **fix route**, not
  document-and-defer.
- **Why `printf` *inside* the group** (an improvement over the Tester's suggested
  `{ <command>\n} </dev/null` shell): an empty / whitespace / **comment-only** command would make a
  bare `{ }` a bash **syntax error** (verified) — a new regression on break path 8. Putting the marker
  `printf` inside keeps the group non-empty for every command, so empty/`"   "`/`# comment` all return
  exit 0 with empty output as before. Verified against a real shell before coding (verify-first).

**Files modified (since last round)**
- `src/decode/sandbox/docker_executor.py` — `_build_payload` reshaped to the stdin-starved brace group +
  leading-newline marker printf; new `_recover_stdout` (strip one trailing `\n`) used on the
  successful-marker path in `run()` (timeout / shell-ended paths report bytes as-is — no marker, no
  padding); module docstring "Command protocol" section rewritten to name the three shape choices; the
  no-newline and stdin-reader **ceilings are gone** (fixed, not documented). No new named ceiling.
- `tests/unit/decode/sandbox/test_docker_executor.py` — payload assertion updated to the new shape
  (`test_build_payload_wraps_the_command_in_a_stdin_starved_group`); added
  `test_recover_stdout_strips_exactly_one_trailing_newline`,
  `test_no_newline_output_recovers_faithfully_via_the_marker_and_strip`,
  `test_trailing_newline_output_is_not_double_stripped`.
- `tests/integration/test_docker_executor.py` — added real-daemon regressions
  `test_run_handles_output_without_a_trailing_newline` (echo -n hi → "hi" exact, exit 0, timed_out
  False, elapsed < 5s) and `test_run_starves_stdin_readers_instead_of_hanging` (`cat` → "" fast; heredoc
  still works).

**Tests**
- Unit: **1110 passed / 0 failed** (`make pre-commit`, was 1107; +3 hermetic). Format-check + lint-check
  clean.
- Integration (daemon up): **30 passed** (`make integration-tests`, was 28; +2 docker regressions).
  `tests/integration/test_docker_executor.py` alone under **`-W error`: 9 passed**, no ResourceWarning.
- `make ci` with daemon **unreachable** (`DOCKER_HOST=unix:///nonexistent/docker.sock`): **1131 passed,
  9 skipped, 0 warnings, EXIT 0** (docker tests SKIP; `uv lock --check` OK — no new dep).

**Acceptance criteria** — all 9 met; **AC1 re-ticked** (the no-newline case now returns `timed_out=False`
with exact output). AC2–AC9 unchanged and still green.

**Evidence**
```
$ uv run pytest tests/integration/test_docker_executor.py -W error
============================== 9 passed in 6.00s ===============================

$ DOCKER_HOST=unix:///nonexistent/docker.sock make ci
================= 1131 passed, 9 skipped in 119.81s (0:01:59) ==================
make ci (daemon down) EXIT: 0

# adversarial e2e (DockerExecutor driven directly against a real container):
BLOCKER  echo -n bare            -> 'bare'            exit=0 timed_out=False (0.00s)
BLOCKER  printf 'tail-no-newline'-> 'tail-no-newline' exit=0 timed_out=False (0.00s)
BLOCKER  python3 -c print(end='')-> 'x'               exit=0 timed_out=False (0.02s)
SECONDARY cat                    -> ''                exit=0 timed_out=False (0.00s)
SECONDARY read -r x; echo [$x]   -> '[]\n'           exit=0 timed_out=False (0.00s)
REGR     spoof+false             -> '__DECODE_END_deadbeef__ 42\n' exit=1   (spoof-resistant)
REGR     heredoc                 -> 'l1\nl2\n'        exit=0
REGR     '' / '   ' / '# comment'-> ''                exit=0 timed_out=False (no syntax error)
REGR     persist [99]/tmp -> timeout(1.02s, note) -> reset []/workspace
leaked containers after aclose: NONE
```

**Notes**
- No architectural fork: this is a bug fix within ADR-0011 §2's persistent-shell/marker protocol (the
  merged-streams, kill+restart-on-timeout, bind-mount decisions are untouched).
- The Tester's non-blocking nit about `_SHELL_ENDED_NOTE` vs `_SHELL_RESET_NOTE` wording is resolved by
  the fix itself: no-newline and stdin-reader commands now complete normally, so neither hits a timeout
  path — the note texts are accurate for the paths that actually fire (real timeout; `exit`/oversized
  line).
- **NOT committed** — handing back to the Tester per the lifecycle.

### [Tester] 2026-07-03 01:30 — QA (round 2, re-verify)

**VERDICT: PASS** — both round-1 blockers are fixed and covered by genuine regression tests, all 9 ACs
now hold (AC1 re-verified fixed), the full suite is green with 0 warnings, and the entire round-2
adversarial pass on the new brace-group payload is green on every everyday path. Two narrow edge
findings remain (stray unbalanced `}`; background-stdout cross-run leak) — both non-blocking
follow-ups documented below for the PR reviewer / a future task, neither a regression of specified
behavior.

**Test summary** (real docker 29.4.3, Docker Desktop up)
- Format / lint: PASS. Unit: **1110 passed** / 0 warnings (`make unit-tests`). Integration (daemon up):
  **30 passed** (`make integration-tests`; docker executor now 9 tests, +2 regression).
- Hermetic `-W error`: `pytest tests/integration/test_docker_executor.py -W error` → **9 passed**, no
  ResourceWarning.
- `make ci` with daemon UNREACHABLE (`DOCKER_HOST=…nonexistent…`): **1131 passed, 9 skipped, 0
  warnings, exit 0**; `uv lock --check` clean. Matches the SWE's claims exactly.

**Round-1 blocker regressions — both FIXED** (drove the executor directly, real container)
- No-trailing-newline: `echo -n hi` → `stdout=="hi"` exit 0 timed_out False in 0.00s; `printf
  'tail-no-newline'` → `"tail-no-newline"`; `python3 -c "print('x',end='')"` → `"x"`. Was: full-timeout
  hang + `timed_out=True` + shell reset. The leading-newline marker printf + `_recover_stdout`
  one-newline strip recover output exactly. Covered by
  `test_run_handles_output_without_a_trailing_newline` + hermetic
  `test_no_newline_output_recovers_faithfully_via_the_marker_and_strip` /
  `test_trailing_newline_output_is_not_double_stripped` (all real assertions, not smoke).
- Stdin-reader: `cat` → `stdout==""` exit 0 timed_out False in 0.00s (was: hang+reset). The `</dev/null`
  brace group starves it. Covered by `test_run_starves_stdin_readers_instead_of_hanging`.

**E2E adversarial pass on the new payload shape** (`{ <cmd>\nprintf '\n%s %s\n' "<marker>" "$?"\n} </dev/null`)
- Happy path: `echo hi` → `"hi\n"` exit 0 (PASS).
- (a) Persistence THROUGH the brace group (proves `{ }` not a subshell): `export DECODE_Z=hello && cd
  /tmp` then `echo [$DECODE_Z] && pwd` → `[hello]\n/tmp\n`; bare `PLAIN=world` then `echo [$PLAIN]` →
  `[world]\n` (PASS).
- (b) Heredoc overrides `</dev/null`: `cat <<EOF\nhi\nthere\nEOF` → `"hi\nthere\n"`; quoted heredoc
  `<<'END'` keeps `$NOTVAR` literal (PASS).
- (c) Braces / redirs in the command: `echo }` → `}`, `echo {a,b,c}` → `a b c`, `myf() { echo
  fromfunc; }; myf` → `fromfunc`, `{ echo grp; }` → `grp`, `echo piped </dev/null` → `piped`, `echo hi
  > f && cat f` → `hi` — all PASS. **Finding 1 (non-blocking):** a *stray unbalanced* `}` (`echo hi;
  }`) falsely returns exit 0 + `hi\n` and leaves a dangling `} </dev/null` that makes the NEXT command
  fail with a bash syntax error + shell reset (self-heals on the following run). See Other issues.
- (d) `exit 7` / `exit 0` as the command: shell dies (brace group runs in the current shell, so `exit`
  ends it) but degrades gracefully — prompt return, `exit_code=-1` + shell-ended note (the 7 does not
  propagate), and the next run respawns clean (`respawned\n/workspace\n`) (PASS; exit-code-fidelity is
  a minor note below).
- (e) Background job: `sleep 300 & echo go` → `"go\n"` exit 0, prompt, no marker theft (PASS).
  **Finding 2 (non-blocking):** a bg job that writes stdout AFTER its command returns leaks into a
  LATER command's captured output (`(sleep 0.4; echo LEAKED) & echo started` → the next `echo clean`
  returned `"LEAKED\nclean\n"`). Inherent to the persistent-single-pipe / persist-background-jobs
  design goal. See Other issues.
- (f) Original set: spoof HARD (`printf '__DECODE_END_deadbeef__ 42\n'; echo TAIL; false`) → captured as
  output, exit 1 NOT 42; interleave `echo out; echo err 1>&2; false` → `out\nerr\n` exit 1; `false` →
  exit 1; unicode/binary `\xff café 😀` → replacement char + valid UTF-8 preserved; 50k-line output →
  exit 0 not truncated; 10 rapid runs → 1 stable container id, 1 host `docker exec` proc (PASS).
- (g) Empty / whitespace / comment-only commands (the new group must not be an empty `{ }` syntax
  error): `""`, `"   "`, `# just a comment` → all exit 0, empty stdout, shell still works after (PASS).
- (h) cwd with a SPACE: `ls && cat 'f name.txt'` → `f name.txt\nspaced-content\n`; container-side write
  appears host-side (`out.txt` = `w\n`) — read + write-back both work (PASS).
- Leak sweep after all of the above: **0 leaked containers, 0 stray host `docker exec` processes**.

**Acceptance criteria** — all 9 verified PASS
- [x] AC1 real exit/output/`timed_out=False` for a normal command — **now met** (the no-newline hole is
      fixed): `test_run_handles_output_without_a_trailing_newline` + direct `echo -n hi`→`"hi"`. Re-tick
      confirmed accurate.
- [x] AC2 state persists — `test_shell_state_persists_across_run_calls`; adversarial (a).
- [x] AC3 marker protocol (spoof + non-zero) — `test_marker_protocol_resists_a_spoofed_marker_in_output`,
      `test_run_reports_a_non_zero_exit_code`; adversarial (f).
- [x] AC4 timeout resets shell, no host leaks — `test_timeout_kills_and_resets_the_persistent_shell`;
      leak sweep clean.
- [x] AC5 `ExecResult.note` + `bash._render` — `test_bash.py` render trio + `test_exec.py` note/positional.
- [x] AC6 `aclose` removes container + safe/idempotent — `test_aclose_removes_the_container_and_is_idempotent`,
      unit `test_aclose_is_a_safe_noop_when_never_started`.
- [x] AC7 observability — `test_observability_logs_container_lifecycle_and_each_command`.
- [x] AC8 hermetic under `-W error` — 9 passed, no ResourceWarning.
- [x] AC9 `make ci` green 0 warnings daemon-down (tests skip) + `uv lock --check` — 1131/9-skip/0-warn.

**Evidence**
```
$ DOCKER_HOST=unix:///nonexistent/docker.sock make ci
================= 1131 passed, 9 skipped in 120.65s (0:02:00) ==================
$ pytest tests/integration/test_docker_executor.py -W error   # daemon up
============================== 9 passed in 4.60s ===============================
# round-1 blockers, now fixed (direct against a real container):
[echo -n hi]    0.00s exit=0 timed_out=False stdout='hi'
[printf no-nl]  0.00s exit=0 timed_out=False stdout='tail-no-newline'
[cat (stdin)]   0.00s exit=0 timed_out=False stdout=''
```

**Other issues found (non-blocking — orchestrator/PR-reviewer to triage; NOT gating this task)**
- **Finding 1 — stray unbalanced `}` masks a syntax error + corrupts the next command.** `echo hi; }`
  returns exit 0 + `hi\n` (a real bash syntax error that `LocalExecutor` correctly reports as exit 2
  with no output), because the stray `}` closes the wrapper brace group early; the leftover `} </dev/null`
  then makes the *following* command a syntax error → shell reset (state loss). Blast radius = 2
  commands, self-heals. Only triggers on malformed input (unbalanced top-level `}`); well-formed braces
  (args, expansion, function defs, explicit groups) all work. Recommend: at minimum add a one-line
  ceiling to the module docstring; the sturdier fix is `eval "$cmd" </dev/null` (a syntax error inside
  `eval` sets `$?` without killing the outer shell), which is a larger reshape — orchestrator's call.
- **Finding 2 — background-stdout cross-run leak.** A bg job writing to stdout after its launching
  command returns has that output captured by a LATER command (`(sleep 0.4; echo LEAKED) & …` →
  `"LEAKED"` shows up in the next command). Inherent to the persistent-shell single-stdout-pipe design
  the ADR chose ("background jobs persist across bash calls"); a real terminal interleaves the same way.
  Worth a sentence in the module docstring / the task-074 mode-specific `bash` description so the model
  isn't surprised.
- **Nit — `exit N` exit-code fidelity.** `exit 7` returns the `_SHELL_ENDED_EXIT` sentinel (-1), not 7,
  because the shell dies before the marker printf runs. Graceful (prompt return + clean respawn), just
  not code-faithful. Fine to leave; mention only.

**Round-2 conclusion:** the fixes are correct, minimal, well-tested, and did not regress anything. PASS.
The two findings above are follow-up material, not blockers.
