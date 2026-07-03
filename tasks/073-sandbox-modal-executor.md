---
id: 073-sandbox-modal-executor
feature: sandboxing
status: done
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

- [x] Verify-first: the log records the `modal.Sandbox` create/exec/terminate + `App.lookup` /
  `Image.from_registry` signatures confirmed against the installed SDK; shipped code matches.
- [x] `ModalExecutor.run` satisfies the `CommandExecutor` Protocol and returns an `ExecResult` with the
  real exit code + output for a normal command — proven against a **real modal account** in a
  `@pytest.mark.skipif(no modal creds)` integration test (`run("echo hi")` → `hi`, `exit_code == 0`).
- [x] **Filesystem persists, no local tree:** `run("echo data > /workspace/f.txt")` then
  `run("cat /workspace/f.txt")` returns `data`; and a file that exists in the host cwd is **absent** in
  the sandbox (`run("ls <a-known-host-file>")` → non-zero / not found) — proving empty remote scratch.
  (skipif-modal.)
- [x] **Timeout kills the exec, not the sandbox:** `run("sleep 100", timeout_s=1)` → `timed_out=True`; a
  subsequent `run("echo alive")` on the same instance still works (sandbox survived). (skipif-modal.)
- [x] `aclose()` terminates the sandbox (idempotent; safe if never created; double-close does not raise).
- [x] Observability: create/terminate + each command logged (id + image on create; `$ cmd` → exit/bytes;
  terminate on teardown) — asserted via `caplog` in the skipif-modal test.
- [x] Tests hermetic under `filterwarnings=["error"]`; no leaked async resources.
- [x] `make ci` green with 0 warnings **without** modal creds (the modal tests SKIP); `uv lock --check`
  passes (modal already a dep).

## Out of scope

- Wiring `ModalExecutor` into the `bash` selection seam + the mode-specific description (074).
- Local-tree sync to the remote sandbox (deliberately not done — empty scratch is the design).
- Any credential-proxy involvement (proxy is docker-only).

## Log

### [SWE] 2026-07-03 01:40 — Implementation

**Verify-first (modal 1.5.1, confirmed against the installed SDK + a real-account probe)**

Signatures confirmed via `inspect.signature` on the installed `modal 1.5.1`, then the *runtime*
semantics confirmed by a one-sandbox real-account probe (a few cents of compute):

- `modal.App.lookup(name: str, *, create_if_missing: bool = False, ...) -> App` — async via `.aio`.
- `modal.Image.from_registry(tag: str, ...) -> Image` — **sync** factory (builds a lazy spec, no
  network; **no** `.aio`).
- `modal.Sandbox.create(*args, app=None, image=None, timeout: int = 300, workdir: str | None = None,
  ...) -> Sandbox` — async via `.aio`. Has both `workdir=` and `timeout=` (sandbox max lifetime).
- `sandbox.exec(*args, stdout=PIPE, stderr=PIPE, timeout: int | None = None, workdir: str | None =
  None, text: bool = True, ...) -> ContainerProcess` — async via `.aio`. **Accepts a per-exec
  `timeout=` and `workdir=`.**
- `ContainerProcess`: `.stdout` / `.stderr` are `StreamReader`s (`.read()` → `str` with `text=True`,
  async via `.aio`); `.wait()` → `int` exit code (async via `.aio`); `.poll()`; `.returncode`.
  **No `terminate`/`kill` on the handle** — so the task's "`p.terminate()`" fallback does not exist;
  modal's native per-exec `timeout=` is the *only* way to kill a hung command while keeping the
  sandbox, and is what I used.
- `sandbox.terminate(*, wait: bool = False) -> int | None` — async via `.aio`.
- `sandbox.object_id` — the sandbox id string, available immediately after `create`.

Runtime facts the probe pinned (drove the design):

- **Bootstrap `mkdir -p /workspace` is required** — stock `python:3.12-slim` has no `/workspace`, so
  `exec(workdir="/workspace")` needs it created once first (probe: `pwd` in `/workspace` → `/workspace`).
- **Per-exec `timeout=1` on `sleep 100` → `wait()` returns `-1`** (an internal `ExecTimeoutError`
  mapped to `-1`), completes in ~1.1s, and **the sandbox survives** (`echo alive` → `rc=0` after). The
  code detects the `-1` sentinel and normalizes it to `-signal.SIGKILL` (the sibling executors' convention).
- fs persists across execs (`echo data > f` then `cat f` → `data`); local tree absent
  (`ls pyproject.toml` → `rc=2`); `terminate()` returns `None` and is idempotent (double-call, no raise).

Shipped code matches this surface exactly.

**Files modified**
- `src/decode/sandbox/modal_executor.py` — NEW `ModalExecutor` (`CommandExecutor`): lazy one-per-session
  remote `modal.Sandbox`, empty `/workspace` scratch, `bash -lc` per-exec with `workdir` + native
  per-exec `timeout`, streams drained concurrently, `aclose`→`terminate`. `modal` imported lazily via
  `_load_modal()` so none/docker/REPL paths never pay for it.
- `src/decode/sandbox/__init__.py` — export `ModalExecutor` alongside `DockerExecutor` (no
  `select_executor` — that is 074).
- `tests/unit/decode/sandbox/test_modal_executor.py` — NEW hermetic unit tests (fake modal double via
  the `_load_modal` seam).
- `tests/integration/test_modal_executor.py` — NEW real-modal tests, `@skipif` no creds (task-071 predicate).

**Tests**
- Unit: 16 passing, 0 failing (`tests/unit/decode/sandbox/test_modal_executor.py`); full unit suite
  1126 passing via `make pre-commit`.
- Integration: 4 real-modal tests passing in 13.78s (creds present on this machine); full integration
  suite 34 passing. Verified they SKIP cleanly (4 skipped, 0 errors) with creds absent.

**Acceptance criteria**
- [x] Verify-first recorded (above); shipped code matches modal 1.5.1.
- [x] `run` returns real exit+output — `tests/integration/test_modal_executor.py::test_run_echo_round_trips_and_logs_create_and_command`.
- [x] fs persists / no local tree — `::test_filesystem_persists_and_the_local_tree_is_absent`.
- [x] timeout kills exec not sandbox — `::test_timeout_kills_the_exec_not_the_sandbox`.
- [x] `aclose` terminates, idempotent, safe-if-never-started — unit `test_aclose_*` + integration `::test_aclose_terminates_the_sandbox_and_is_idempotent`.
- [x] observability (caplog) — integration `::test_run_echo_...` (create+command) + `::test_aclose_...` (terminate); also unit `test_logs_create_command_and_terminate`.
- [x] hermetic under `filterwarnings=["error"]`; no leaked async resources — full integration suite green under the error filter.
- [x] `make ci` green without creds (modal SKIPs) + `uv lock --check` passes — verified components (skip path clean, lock clean, format/lint/unit green).

**Evidence**
```
$ uv run pytest tests/unit/decode/sandbox/test_modal_executor.py -q
................                                                          [100%]
16 passed in 1.27s

$ uv run pytest tests/integration/test_modal_executor.py -v   # creds present
test_run_echo_round_trips_and_logs_create_and_command PASSED
test_filesystem_persists_and_the_local_tree_is_absent PASSED
test_timeout_kills_the_exec_not_the_sandbox PASSED
test_aclose_terminates_the_sandbox_and_is_idempotent PASSED
4 passed in 13.78s

$ HOME=<empty> (MODAL_TOKEN_* unset) uv run pytest tests/integration/test_modal_executor.py
4 skipped in 0.21s   # clean skip without creds

$ uv lock --check
Resolved 149 packages   # no lock change (modal already a dep)

# End-to-end (real modal, ModalExecutor invoked as bash would):
INFO  [sandbox] modal create sb-6Sl0c4xypoxULaU1g4LZNJ image=python:3.12-slim
DEBUG [sandbox] $ echo hello from modal && python --version → exit=0 bytes=32
DEBUG [sandbox] $ sleep 100 timed out after 1s → exec killed (sandbox survives)
DEBUG [sandbox] $ echo still-alive → exit=0 bytes=12
INFO  [sandbox] modal terminate sb-6Sl0c4xypoxULaU1g4LZNJ
  normal:  exit 0  timed_out False  note ''  stdout 'hello from modal\nPython 3.12.13\n'
  timeout: exit -9 timed_out True   note ''
  after:   exit 0  stdout 'still-alive\n'   # sandbox survived the exec timeout
```

**Notes**
- Timeout mechanism = modal's **native per-exec `timeout`** (the handle has no terminate/kill), detected
  via `wait() == -1` and normalized to `-signal.SIGKILL`. `note` stays `""` for modal (only the exec
  dies; the sandbox + fs persist) — unlike docker's shell-reset note.
- `ponytail:` a sub-second `timeout_s` is floored to 1s (modal's granularity is integer seconds); a hung
  modal API call during create/lookup is bounded only by the sandbox lifetime, not a client deadline
  (matches `DockerExecutor`, which does not bound `docker run`).
- `cwd` arg to `run()` is deliberately **ignored** (host paths meaningless remotely) — documented in the
  docstring; the mode-specific `bash` description telling the model this is task 074 (out of scope).
- Real-modal evidence: **present** (this machine has `~/.modal.toml`). No `bash`-tool changes,
  `select_executor`, or credential-proxy work — all out of scope (074/075).

### [Tester] 2026-07-03 01:54 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`format-check` exit 0 · `lint-check` "All checks passed!" · `make pre-commit` green)
- Unit tests: 1126 passed / 0 failed (16 in `test_modal_executor.py`)
- Integration tests: 34 passed / 0 failed with creds present (4 real-modal in ~13.9s); creds hidden → 4 modal tests SKIP cleanly, pytest exit 0
- `uv lock --check`: clean (149 packages, no change) · `-W error` on the modal integration file: 4 passed, no ResourceWarning / unclosed-transport noise
- Warnings: 0

**E2E adversarial pass** (drove `ModalExecutor` directly against the real modal account; one sandbox `sb-rEM7sb9abI9nzNckwQv9Z1` reused for all command break-paths)
- Happy path: `run("echo hi")` → `stdout='hi\n' exit=0 timed_out=False note=''` (PASS)
- fs persistence: `echo persisted > /workspace/f.txt` then `cat` → `'persisted'` (PASS)
- Boundary (local tree absent): `ls pyproject.toml` → `exit=2`, stderr "No such file or directory", `timed_out=False` (PASS — empty remote scratch proven)
- Stream split: `echo out; echo err 1>&2; false` → `stdout='out\n' stderr='err\n' exit=1` (PASS — modal keeps streams separate, unlike docker's merge)
- Boundary (unicode): `printf` of multibyte text → exact round-trip, exit 0 (PASS)
- **Malformed/binary output: `head -c 32 /dev/urandom` → RAISED `UnicodeDecodeError` (NOT an ExecResult)** (FAIL — see AC/Other)
- Boundary (empty command `""`): `run("")` → `exit=0 timed_out=False` (PASS)
- Multi-line heredoc: `cat <<'EOF' … EOF` → `'line1\nline2\nline3\n' exit=0` (PASS)
- Sub-second timeout: `run("sleep 100", timeout_s=0.3)` → floored to 1s, `timed_out=True exit=-9 note=''`, wall-clock 1.14s (PASS — no crash, honest)
- Timeout wall-clock: `run("sleep 100", timeout_s=1)` → `timed_out=True` in 1.13s (not ~100s), then `echo alive` → `'alive'` exit 0 (PASS — exec killed, sandbox survived)
- State (rapid sequential): 5 back-to-back runs → all correct; `[sandbox] modal create` appears exactly once; sandbox id stable across all 12 runs (PASS)
- aclose then aclose: second call returned cleanly, no raise; `modal terminate` logged once (PASS)
- Cost hygiene: post-run `modal container list` = None; SDK `Sandbox.list(app_id=...)` = 0 running; **no sandbox left running** (verified)

**Acceptance criteria**
- [x] PASS — Verify-first recorded + shipped code matches modal 1.5.1 — log §"Verify-first" matches `modal_executor.py` (`App.lookup.aio`/`Image.from_registry`/`Sandbox.create.aio(app,image,timeout)`/`exec.aio(...,workdir,timeout)`/`terminate.aio`, `wait()==-1` sentinel).
- [x] PASS — `run` satisfies `CommandExecutor` + real exit/output for a normal command — `tests/integration/test_modal_executor.py::test_run_echo_round_trips_and_logs_create_and_command` + adversarial happy-path (`echo hi`→`hi`, exit 0).
- [x] PASS — fs persists / local tree absent — `::test_filesystem_persists_and_the_local_tree_is_absent` + adversarial (`cat` readback `persisted`; `ls pyproject.toml` exit 2).
- [x] PASS — timeout kills exec not sandbox — `::test_timeout_kills_the_exec_not_the_sandbox` + adversarial wall-clock 1.13s then `echo alive` works.
- [x] PASS — aclose terminates, idempotent, safe-if-never-started, double-close no raise — unit `test_aclose_*` + integration `::test_aclose_...` + adversarial double-aclose.
- [x] PASS — observability create/terminate/command via caplog — unit `test_logs_create_command_and_terminate` + integration; captured logs show `modal create <id> image=…` (INFO), `$ <cmd> → exit=N bytes=N` (DEBUG), `modal terminate <id>` (INFO).
- [x] PASS — hermetic under `filterwarnings=["error"]`, no leaked async resources — full suite green + `-W error` on the modal integration file (4 passed, no ResourceWarning).
- [x] PASS — `make ci` green with 0 warnings without modal creds + `uv lock --check` — verified by components: lock clean, format/lint green, 1126 unit pass, modal integration file skips cleanly (pytest exit 0) with `HOME` redirected + `MODAL_TOKEN_*` unset.

  Every enumerated AC verifies. **But** the adversarial pass surfaced a contract-violating crash (below), which the QA rubric makes an Always-FAIL independent of the enumerated ACs.

**Other issues found**
- **[FAIL — blocking] Non-UTF-8 command output crashes `run()` with an unhandled `UnicodeDecodeError` instead of returning an `ExecResult`.**
  - Repro (real modal): `await ModalExecutor().run("head -c 16 /dev/urandom", cwd=…, timeout_s=120)` → raises `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd0 …`.
  - Location: `src/decode/sandbox/modal_executor.py:183` (`_exec` → `proc.stdout.read.aio()`); root cause is modal's `text=True` default, whose stream reader (`modal/io_streams.py::_decode_bytes_stream_to_str`) uses a **strict** incremental UTF-8 decoder (`errors="strict"`).
  - Why it's a defect: the shared `ExecResult` contract (`src/decode/tools/exec.py:39-40`) states "undecodable bytes are replaced, never crash", and both sibling executors honor it — `LocalExecutor` runs the same command fine (`exit=0`, replacement chars), `DockerExecutor` decodes via `errors="replace"`. `ModalExecutor` diverges and breaks the contract of the very type it returns.
  - Blast radius: `bash.py:76` calls `_EXECUTOR.run(...)` with no try/except, so under `SANDBOX_MODE=modal` any command emitting non-UTF-8 bytes on stdout OR stderr (cat a binary/image/gzip, run a compiled program, latin-1 output, truncated multibyte) crashes the tool call / turn. Realistic, user-hit.
  - Why the suites missed it: the hermetic unit fake (`_FakeStream`) returns clean `str` (faithful to `text=True`) and the real-modal tests only exercise text output — neither exercises the strict-decode path. This is exactly the gap the adversarial pass exists to catch.
  - **Verified fix (tested live):** pass `text=False` to `sandbox.exec.aio(...)` (yields `bytes` streams) and decode both with a `_decode(raw) = raw.decode("utf-8", errors="replace")` helper mirroring the siblings. Live probe confirmed: binary output → no crash (15 chars, replacement applied); UTF-8 multibyte `hi 世界` still round-trips exactly; stderr stays split. Then update the unit fake to return `bytes` and add a regression test (undecodable bytes → replacement, no raise) plus ideally a real-modal integration case (`head -c 32 /dev/urandom` → exit 0, no raise).
- [nit, non-blocking] After the fix, the `bytes=%d` DEBUG log measures decoded `len(str)` (char count), not raw byte count — consistent with `DockerExecutor` today, so acceptable; the SWE may log `len(raw)` if a true byte count is wanted.

**Evidence**
```
$ uv run pytest tests/unit/decode/sandbox/test_modal_executor.py -q
16 passed

$ make integration-tests            # creds present
tests/integration/test_modal_executor.py ....            [ 73%]
34 passed in 65.12s

$ (HOME redirected, MODAL_TOKEN_* unset) uv run pytest tests/integration/test_modal_executor.py -q
4 skipped ; pytest exit 0

$ uv run pytest tests/integration/test_modal_executor.py -W error   # creds present
4 passed in 13.85s

# adversarial binary-output crash (real modal), traceback tail:
  File ".../src/decode/sandbox/modal_executor.py", line 183, in _exec
    stdout, stderr = await asyncio.gather(proc.stdout.read.aio(), proc.stderr.read.aio())
  File ".../modal/io_streams.py", line 193, in _decode_bytes_stream_to_str
    text = decoder.decode(item, final=False)
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd0 in position 0: invalid continuation byte
# same command on LocalExecutor: exit=0, no crash (errors='replace')

$ uv run modal container list       # after all real-modal work
Active Containers in environment: None   # no leak — cost hygiene verified
```

**VERDICT: FAIL** — every enumerated AC passes and the full suite is green, but the e2e adversarial pass found a contract-violating crash on non-UTF-8 command output (a realistic user input) that must be fixed + regression-tested. One blocking issue; verified fix and test guidance provided above.

### [SWE] 2026-07-03 02:20 — Fixes (round 2)

Fixed the one blocking issue: **non-UTF-8 command output no longer crashes `run()`.** Root cause was
relying on modal's `text=True` default, whose reader decodes *strict* UTF-8 — a binary/latin-1 command
raised `UnicodeDecodeError`, violating the `ExecResult` "undecodable bytes replaced, never crash"
contract the sibling executors honor.

**What changed since round 1**
- `src/decode/sandbox/modal_executor.py`:
  - `_exec` now passes `text=False` to `sandbox.exec.aio(...)` (streams yield **bytes**) and decodes
    both via a new module-level `_decode(raw: bytes) -> str` helper using
    `raw.decode("utf-8", errors="replace")` — mirroring `tools/exec.py::_decode` and the docker
    executor. Both the normal and timeout branches route through `_exec`, so partial timeout output is
    decoded-with-replace too. stderr stays **split** from stdout (no merge, unlike docker).
  - Docstrings updated to state the `text=False` + `errors="replace"` contract (removed the stale
    `text=True → str` line).
- `tests/unit/decode/sandbox/test_modal_executor.py`:
  - **Fake now returns `bytes`** (`_FakeStream`/`_FakeProc`), faithful to `text=False`, so it can no
    longer mask this decode-contract bug class. Existing str-payload call sites updated to bytes.
  - +3 regression tests: `test_run_replaces_undecodable_bytes_on_both_streams` (undecodable bytes on
    stdout AND stderr → U+FFFD, no raise, correct exit), `test_run_round_trips_utf8_multibyte_output`
    (café/✓ decode exactly — guards a naive latin-1 "fix"), `test_run_timeout_decodes_partial_bytes_with_replace`
    (timeout branch decodes partial bytes with replace). Added `assert kwargs["text"] is False` to the
    exec-shape test to lock the fix in.
- `tests/integration/test_modal_executor.py`: +`test_binary_output_does_not_crash` — real-modal
  `head -c 16 /dev/urandom` on stdout AND stderr → `str`, exit 0, `timed_out=False`, no crash.

**Re-run gate (round 2)**
- `format-check` exit 0 · `lint-check` "All checks passed!" · `make pre-commit` green — **1129** unit
  passed (was 1126; +3 regressions), 0 failing.
- Regression proof: with the bytes fake in place the decode-dependent tests went **red** (6 failing,
  returning raw `b'...'`), then **green** after the `text=False`+`_decode` fix (19 passing in the file).
- Real modal (creds present): `pytest tests/integration/test_modal_executor.py -v -W error` → **5
  passed in 15.28s**, no ResourceWarning. `test_binary_output_does_not_crash` passes against real modal.
- Creds hidden (`HOME` redirected + `MODAL_TOKEN_*` unset): **5 skipped**, pytest exit 0.
- **Cost hygiene:** post-run `modal container list` → "Active Containers: None"; SDK
  `Sandbox.list(app_id=…)` for `decode-sandbox` → NONE. **No sandbox left running.**

**Not changed (deliberate):** the `bytes=%d` DEBUG log still measures `len(decoded_str)` — the Tester
flagged this as a non-blocking nit and it is consistent with `DockerExecutor`, so kept for sibling
parity. Still uncommitted — awaiting Tester re-review.

### [Tester] 2026-07-03 02:10 — QA (round 2, re-verify fix)

Re-verified the round-2 fix for the round-1 blocking finding (non-UTF-8 output crashed `run()`).
Read the reshaped `_exec` (`text=False` + module-level `_decode(errors="replace")`, both branches
share `_exec`, stderr stays split), the bytes-fake unit tests, and the new integration case.

**Test summary**
- Format / lint / pre-commit: PASS (`format-check` exit 0 · `lint-check` "All checks passed!")
- Unit tests: 1129 passed / 0 failed (was 1126; +3 decode regressions; `test_modal_executor.py` now 19)
- Integration tests: 35 passed / 0 failed with creds present (was 34; +1 real-modal binary test)
- `-W error` on the modal integration file (creds present): 5 passed, no ResourceWarning
- Creds hidden (`HOME` redirected + `MODAL_TOKEN_*` unset): 5 skipped, pytest exit 0
- `uv lock --check`: clean · Warnings: 0

**E2E adversarial pass (round 2 — real modal, one sandbox `sb-b5fpehuVhxV05SfnuaBkLv` reused)**
- Original crashing repro `head -c 16 /dev/urandom`: → `exit=0`, `stdout` is `str` (16 chars), `timed_out=False` — **no longer raises** (FIXED; PASS)
- Malformed (binary on stderr): `head -c 16 /dev/urandom >&2` → `exit=0`, `stderr` is `str` (15 chars), no crash (PASS)
- Malformed (interleaved binary+text, both streams): `printf 'TEXT-OUT '; head -c8 …; printf ' TEXT-ERR ' 1>&2; head -c8 … 1>&2` → `TEXT-OUT` only on stdout, `TEXT-ERR` only on stderr, binary interleaved, exit 0, no cross-contamination, no crash (PASS)
- Large input (1 MiB binary): `head -c 1048576 /dev/urandom` → `exit=0`, decoded to 993455-char `str` in 1.10s — no crash, no pathological slowdown (PASS)
- Regression (stderr split on text): `echo out; echo err 1>&2; false` → `stdout='out\n' stderr='err\n' exit=1` (PASS)
- Regression (UTF-8 multibyte round-trip): `printf '%s' 'cafe 世界'` → exact, exit 0 (PASS — replace decoder never mangles valid bytes)
- Regression (timeout wall-clock + survival): `sleep 100 @1s` → `timed_out=True exit=-9 note=''` in 1.14s, then `echo alive` → `alive` exit 0 (PASS)
- Regression (rapid create-once): 5 back-to-back runs → one `modal create` in logs, stable sandbox id across all runs (PASS)
- Regression (double-aclose): second `aclose()` returned cleanly, no raise (PASS)
- Result: 11/11 checks green.

**Acceptance criteria** — all remain [x] (each verified in round 1) and the round-1 blocking adversarial
finding is now resolved: the decode contract (undecodable bytes replaced, never crash) holds on real
modal for stdout, stderr, interleaved, and 1 MiB inputs; unit + integration regressions lock it in.

**Evidence**
```
$ make pre-commit                       # 1129 passed
$ make integration-tests                # 35 passed (creds present)
$ uv run pytest tests/integration/test_modal_executor.py -W error -q   # 5 passed, no ResourceWarning
$ (HOME redirected, MODAL_TOKEN_* unset) pytest .../test_modal_executor.py   # 5 skipped, exit 0
$ uv lock --check                       # Resolved 149 packages, clean

# round-2 adversarial (real modal), all green:
[PASS] original_repro_no_crash: exit=0 type(stdout)=str decoded_len=16 timed_out=False
[PASS] binary_on_stderr: exit=0 type(stderr)=str stderr_len=15
[PASS] interleaved_binary_text_both_streams: exit=0 stdout_has_TEXT-OUT=True stderr_has_TEXT-ERR=True
[PASS] large_binary_blob_no_crash: exit=0 decoded_len=993455 wall=1.10s
[PASS] timeout_wallclock: timed_out=True wall=1.14s exit=-9 note=''
[PASS] create_once_in_logs: 'modal create' count = 1
SUMMARY: 11 checks, 0 failed

$ uv run modal container list           # after all round-2 real-modal work
Active Containers in environment: None  # no sandbox left running — cost hygiene verified
```

**Other issues found**
- None blocking. The `bytes=%d` DEBUG log measuring decoded `len(str)` remains a non-blocking nit
  (consistent with `DockerExecutor`); no action required.

**VERDICT: PASS** — the round-1 blocking defect is fixed and verified against real modal across stdout,
stderr, interleaved, and large (1 MiB) binary inputs; full suite green (1129 unit / 35 integration),
0 warnings, hermetic skips clean, adversarial pass 11/11, and no remote sandbox left running.
