---
id: 080-sandbox-modal-backend
feature: isolated-workspace
status: done
---

# ModalBackend — fresh-exec + direct SandboxFilesystem file ops + bootstrap upload + export sweep

Tags: `sandbox`, `modal`, `refactor`
Depends on: #079, #078
Blocks: #081

The second adapter behind the unified `SandboxExecutor` (ADR-0012): `ModalBackend`. File ops go
**directly against the remote via the SandboxFilesystem API** (no host mirror, so `read` never lies —
the deletion-blind mtime-sync is rejected). Remaining transfers are only ONE bootstrap upload at
create and ONE end-of-session export sweep. `ModalExecutor` is replaced; both backends now live behind
one seam. `deps.cwd` still stays the launch cwd this task (flipped in 081). Supersedes ADR-0011 §3.

## Scope

- **New `src/decode/sandbox/modal_backend.py`** — `ModalBackend(SandboxBackend)` (moved+extended from
  the deleted `modal_executor.py`; `modal` imported lazily via a `_load_modal` seam):
  - `create(workspace)`: `App.lookup` → `Sandbox.create("sleep","infinity",
    image=from_registry(sandbox_image), timeout=…)` → `mkdir -p /workspace` → **ONE bootstrap upload**
    of the host `workspace` (cloned repo + seeded `.decode/skills`) into `/workspace` — via
    `sandbox.filesystem.copy_from_local` (verified on modal 1.5.1) or tar-over-exec. No `add_local_dir`.
  - `exec`: unchanged fresh `sb.exec("bash","-lc",command, workdir="/workspace", timeout=…,
    text=False)`, separate streams, `errors="replace"`; timeout → kill exec, sandbox survives,
    `timed_out=True`, `note==""`.
  - **file ops = the SandboxFilesystem API against `/workspace/<rel>`** (verified present on modal
    1.5.1, `sandbox.filesystem`): `read_bytes` → `filesystem.read_bytes`; `write_bytes` →
    `filesystem.write_bytes`; `make_directory` → `filesystem.make_directory(create_parents=True)`;
    `list_dir` → `filesystem.list_files`; `stat` → `filesystem.stat`; `remove` →
    `filesystem.remove(recursive=…)`. **Direct against the remote — no mirror, always truthful.**
  - **glob/grep run as REMOTE COMMANDS via `exec`** (`find` / `grep`) — never download the tree to
    search it. (Consumed by the shared glob/grep logic in 081; output-parity defined there.)
  - `export()`: ONE end-of-session sweep `/workspace` → host `.decode/sandbox` via
    `sandbox.filesystem.copy_to_local`, so the final workspace is host-visible for the git hand-back.
    (Called from `SandboxExecutor.aclose` before `destroy`.)
  - **Standalone export** (for `/ship` mid-session, task 083): `ModalBackend.export()` is callable
    independently — `SandboxExecutor.export()` runs `backend.export()` **without** `destroy()`, so a
    mid-session `/ship` can sweep `/workspace` (incl. `.git`) down to host `.decode/sandbox` while the
    sandbox stays alive; `aclose()` = `export()` + `destroy()`. Add an `export_executor()` accessor to
    `tools/bash.py` (duck-typed like `warm_executor`/`close_executor`) so task 083 can trigger it.
  - **Revival** (max-lifetime expiry): a `poll()`-dead sandbox is recreated and **re-bootstrapped from
    the host-side `.decode/sandbox` state**; the result `note` says the workspace was restored from the
    last local state and that **in-sandbox changes since the last export may be lost** (honest; still
    better than total loss).
  - `destroy()`: `sandbox.terminate()` (idempotent, loop-independent for free via synchronicity).
- **`sandbox/__init__.py`**: `select_executor("modal")` → `SandboxExecutor(ModalBackend())`. Both
  backends now unified.
- **Delete `src/decode/sandbox/modal_executor.py`.**
- **bash description**: update the modal suffix — remote sandbox; fresh-exec (`cd`/`export` don't
  persist); fs persists across calls; the workspace is exported host-side at session end. Unified in 082.
- **Test migration (keep green):** rewrite `test_modal_executor.py` → `SandboxExecutor`+`ModalBackend`
  (exec + SandboxFilesystem file ops + bootstrap + export + revival); add offline unit tests for
  `ModalBackend` file ops + bootstrap/export against a **fake sandbox/filesystem**; minimal capstone
  edits to stay green.

## Acceptance criteria

- [x] `SANDBOX_MODE=modal` → `SandboxExecutor(ModalBackend)`; construction inert (no `modal` import).
- [x] **Real modal (skipif):** `create` bootstrap-uploads the host workspace once; `ModalBackend` file
  ops read/write **directly against the remote** — a file `bash` writes in `/workspace` is returned by
  `read_bytes` **without any mirror**, and a `remove` is reflected by a subsequent `stat`/`read_bytes`
  raising not-found (no deletion-blindness).
- [x] **Real modal (skipif):** the filesystem persists across `run()`s; a timeout kills the exec, the
  sandbox + fs survive, `note==""`.
- [x] **Real modal (skipif):** `aclose()` runs the export sweep (a file created in `/workspace` appears
  under host `.decode/sandbox`) then terminates the sandbox (no leak).
- [x] **Real modal (skipif):** a remotely-ended sandbox is recreated + re-bootstrapped from the host
  state, and the result `note` says it was restored from the last local state (and later changes may be
  lost).
- [x] `SandboxExecutor.export()` can run mid-session (docker no-op; modal sweeps `/workspace` → host
  `.decode/sandbox`) leaving the sandbox alive; `aclose()` = export + destroy.
- [x] `grep` proves modal never calls `add_local_dir`, uses no mirror/mtime/marker machinery, and
  `modal_executor.py` is gone.
- [x] Offline unit tests prove `ModalBackend` file ops + bootstrap + export against a fake filesystem.
- [x] `make ci` green, 0 warnings, `uv lock --check` passes; modal tests migrated; docker + none
  untouched.

## Out of scope

- File-tool routing through the seam (081), clone-at-launch / CLI (082).
- **Any per-call sync / mtime deltas / size caps** (retired). **Deletion propagation is a non-issue
  now** — file ops are direct, so there is no stale mirror to go blind.

## Log

### [SWE] 2026-07-04 12:20 — Implementation

**Files modified**
- `src/decode/sandbox/modal_backend.py` (NEW) — `ModalBackend(SandboxBackend)`: `create` (App.lookup →
  `Sandbox.create("sleep","infinity", …)` → `mkdir -p /workspace` → ONE tar bootstrap upload), fresh
  `sb.exec` (`text=False` + replace-decode, `-1`→`-SIGKILL` timeout sentinel), direct SandboxFilesystem
  file ops mapped onto `FileStat` (with the missing-file → `FileNotFoundError`/`None` normalization),
  standalone `export()` sweep, `poll()`-driven **and** exec-error revival, `destroy()`.
- `src/decode/sandbox/__init__.py` — `select_executor("modal")` → `SandboxExecutor(ModalBackend())`;
  `__getattr__`/`__all__`/docstrings retargeted `ModalExecutor` → `ModalBackend`.
- `src/decode/sandbox/modal_executor.py` (DELETED) — replaced by the backend above.
- `src/decode/tools/bash.py` — added `export_executor()` (duck-typed like warm/close, for 083); rewrote
  `_MODAL_DESCRIPTION_SUFFIX` (fresh-exec + workspace-exported-at-session-end).
- `tests/unit/decode/sandbox/test_modal_backend.py` (NEW, replaces `test_modal_executor.py`) — 33 offline
  tests over a fake sandbox/filesystem: exec shape, decode contract, file ops + missing-file mapping,
  bootstrap tar upload, export sweep, poll-revival + exec-error-revival regressions, destroy, laziness.
- `tests/integration/test_modal_executor.py` — rewritten to `SandboxExecutor(ModalBackend())` (real
  modal, skipif): bootstrap, direct file ops, no-deletion-blindness, fs-persist/timeout, export, aclose,
  max-lifetime-expiry revival.
- `tests/unit/decode/sandbox/test_select.py`, `tests/unit/decode/tools/test_bash_sandbox_selection.py`,
  `tests/integration/test_sandbox_capstone.py` — retargeted to the new stack; added `export_executor`
  unit tests; capstone real-modal test rewritten to the isolated-Workspace contract.

**Tests**
- Unit: 1256 passing, 0 failing — `make unit-tests` (full suite). `test_modal_backend.py`: 33 passing.
- Integration (real modal, creds present — RAN, not skipped): `test_modal_executor.py` 10 passing;
  `test_sandbox_capstone.py` 17 passing (incl. real docker + real modal + proxy). Docker-side unchanged:
  `test_docker_executor.py` + `test_sandbox_teardown.py` 9 passing.
- `uv lock --check` passes (no new deps — `modal` already present).

**Chosen transfer mechanism (verified on a REAL modal 1.5.1 run)**
- **tar-over-exec via `write_bytes`/`read_bytes`**, NOT `copy_from_local`/`copy_to_local`. Reason: the
  `copy_*` calls are **single-file** on modal 1.5.1 (a whole git-clone tree = N round-trips), and
  hand-driving `ContainerProcess.stdin` re-implements the drain/`write_eof`/`ConflictError` dance that
  `write_bytes` already does robustly. So: bootstrap = `tar_dir(host)` → `write_bytes(tar, /tmp/…tar)` →
  one remote `tar -xpf … -C /workspace`; export = remote `tar -cf /tmp/…tar -C /workspace .` →
  `read_bytes` → host `extract_tar`. One `write_bytes`+`tar` per direction; whole-tree ceiling noted as a
  `ponytail:` (git-diff transport is the ADR-0012 Future-work upgrade).

**Acceptance criteria** (all verified)
- [x] `SANDBOX_MODE=modal` → `SandboxExecutor(ModalBackend)`, inert (no modal import) — live smoke +
  `test_select.py::test_select_modal_returns_an_inert_sandbox_executor_over_a_modal_backend`.
- [x] Real modal: create bootstrap-uploads once; file ops direct (no mirror); remove → not-found —
  `test_modal_executor.py::{test_create_bootstrap_uploads_the_host_workspace_once,
  test_file_ops_read_write_directly_against_the_remote, test_remove_is_reflected_by_a_later_stat_no_deletion_blindness}`.
- [x] Real modal: fs persists; timeout kills exec, sandbox+fs survive, `note==""` —
  `test_modal_executor.py::{test_filesystem_persists_but_cd_and_export_do_not, test_timeout_kills_the_exec_but_the_sandbox_and_fs_survive}`.
- [x] Real modal: `aclose()` exports (file appears host-side) then terminates (no leak) —
  `test_modal_executor.py::test_aclose_exports_then_terminates_the_sandbox`.
- [x] Real modal: expired sandbox recreated + re-bootstrapped, note says restored —
  `test_modal_executor.py::test_a_max_lifetime_expiry_is_recreated_and_rebootstrapped`.
- [x] `SandboxExecutor.export()` mid-session (docker no-op; modal sweeps) leaves sandbox alive; aclose =
  export+destroy — integration `test_export_sweeps_…`; `test_executor.py::test_export_passthrough_reaches_the_backend`;
  `test_bash_sandbox_selection.py::test_export_executor_*`.
- [x] `grep` proves no `add_local_dir` call, no mirror/mtime/marker machinery, `modal_executor.py` gone.
- [x] Offline unit tests prove file ops + bootstrap + export against a fake filesystem — `test_modal_backend.py`.
- [x] `make ci`-scope green, 0 warnings, `uv lock --check` passes; modal tests migrated; docker + none
  untouched. (Caveat below.)

**Evidence**
```
$ uv run pytest tests/unit -q
1256 passed in 81.72s

$ uv run pytest tests/integration/test_modal_executor.py -v     # real modal (creds present)
10 passed in 72.00s
  ... test_create_bootstrap_uploads_the_host_workspace_once PASSED
  ... test_file_ops_read_write_directly_against_the_remote PASSED
  ... test_remove_is_reflected_by_a_later_stat_no_deletion_blindness PASSED
  ... test_export_sweeps_the_workspace_to_the_host_and_leaves_the_sandbox_alive PASSED
  ... test_aclose_exports_then_terminates_the_sandbox PASSED
  ... test_a_max_lifetime_expiry_is_recreated_and_rebootstrapped PASSED

$ uv run pytest tests/integration/test_sandbox_capstone.py -v   # real docker + real modal
17 passed in 20.75s  (incl. test_real_modal_isolated_workspace_contract, test_real_docker_*)

$ python -c "modal.Sandbox.list()"    # cost hygiene, after every real run
modal LIVE: []   +  no ghcr.io/astral-sh/uv worker containers   → no leaks

$ uv lock --check
Resolved 149 packages   (no change)
```

**Notes**
- **Real-modal design correction (found + fixed during implementation).** First real run crashed the
  revival test: after a sandbox ends, `poll()` does NOT reflect an *external terminate* (stays `None` for
  20s+) and `sb.exec` on a dead sandbox raises `modal.exception.NotFoundError` (MRO `GRPCError`, not
  `RuntimeError`/`OSError`). Fixes: (1) production `exec` now has a **second revival trigger** — it
  catches `NotFoundError` from the command exec, revives + re-bootstraps, and retries once (regression
  test `test_exec_revives_when_the_command_hits_a_shut_down_sandbox`); a non-sandbox error still surfaces
  (`test_exec_lets_a_non_sandbox_error_from_the_command_surface`). (2) The integration revival test now
  forces a **real max-lifetime expiry** (modal's 10s floor; `poll()` reliably returns the exit code
  ~1s past the deadline) instead of an external terminate — deterministic and RAN green.
- **`make ci` caveat (per task WIP guidance).** Repo-wide `make ci`/`make lint-check` is red ONLY in the
  pre-existing WIP `src/decode/tools/substack_summarizer.py` (untracked, out of this task's scope and
  explicitly off-limits). Everything in THIS task's scope is format/lint/type/test clean — verified with
  `ruff format --check` + `ruff check` scoped to the 8 changed files (all pass). Did not touch
  `substack_summarizer.py` or `docs/notes/`.
- **`deps.cwd` unchanged** this task (still launch cwd) per scope — flipped in 081. File-tool routing
  through the seam and clone-at-launch/CLI are 081/082; the description is an honest interim (082 unifies).
- Cost hygiene: every real-modal run ends with `Sandbox.list()` == `[]` and no worker containers.

### [Tester] 2026-07-04 01:19 — QA

**Test summary**
- Format / lint / pre-commit: PASS (task scope) — `ruff format --check` clean repo-wide (168 files);
  `ruff check` clean on the 8 changed files. Repo-wide `ruff check` red ONLY in untracked, off-limits
  `src/decode/tools/substack_summarizer.py` (2× E402) — pre-existing WIP, not in this diff (accepted noise).
- Unit tests: 1256 passed / 0 failed (`test_modal_backend.py`: 33 passed).
- Integration tests: 36 passed / 0 failed — real-modal `test_modal_executor.py` 10, `test_sandbox_capstone.py`
  17 (incl. real docker + real modal + proxy), docker `test_docker_executor.py` 8 + `test_sandbox_teardown.py` 1.
- Warnings: 0 (`filterwarnings=error`). `uv lock --check`: 149 packages, no change.

**E2E adversarial pass** (real modal, creds present; fake for the offline probes)
- Happy path: `SandboxExecutor(ModalBackend).run("./run.sh")` after a real bootstrap → exit 0, `ran-ok` (PASS).
- Break path 1 (state edge — file op on a max-lifetime-expired sandbox): real modal — `backend.read_bytes(...)`
  and `backend.stat(...)` on an expired sandbox → **RAISE raw `modal.exception.NotFoundError`** (a `GRPCError`,
  not `FileNotFoundError`/`OSError`), while `exec` on the same dead sandbox revives gracefully with the restore
  note. Confirmed offline (fake) for all six file ops + on real modal for `read_bytes`/`stat`. (**FAIL** — see below.)
- Break path 2 (revival loop-safety): fake — `exec` with `NotFoundError` forever → exactly 2 sandbox creates
  then a clean exit-125 session-lost, no spin; a `-1` timeout does NOT trigger revival (1 create, same sandbox
  stays live). (PASS)
- Break path 3 (bootstrap/export fidelity): real modal — a workspace with a 0o755 script, a nested dir, a
  relative symlink, and a hidden `.git/` → after bootstrap the script RUNS remotely (exec bit survived `tar -xpf`,
  mode 755), hidden dir + symlink + nested present; export round-trips `made.sh` back host-side at mode 0o755
  (exec bit preserved through the `data` filter), `.git/HEAD` and the symlink survive as-is. (PASS)
- Break path 4 (export/aclose on an already-terminated sandbox): real modal — best-effort, returns cleanly, no
  crash. (PASS)
- Break path 5 (cross-backend missing-file parity, docker vs modal): `stat(missing)`→None, `read_bytes(missing)`
  →FileNotFoundError, `remove(missing)`→no-raise all MATCH; **`list_dir(missing)` MISMATCH** — docker
  `FileNotFoundError`, modal `SandboxFilesystemNotFoundError` (`list_dir` has no missing-path normalization,
  unlike `read_bytes`/`stat`/`remove`). (FAIL — secondary; same 081 shared-layer contract.)
- No-deletion-blindness / fs-persist / timeout-kills-exec-not-sandbox: re-proven by the real-modal suite (PASS).

**Acceptance criteria** (all 8 written ACs verified — evidence below)
- [x] PASS — `SANDBOX_MODE=modal` → `SandboxExecutor(ModalBackend)`, inert (no modal import) — subprocess
      spot-check (SandboxExecutor/ModalBackend, `_created=False`, `_sandbox=None`, no `modal`/`kitaru` in
      `sys.modules`); `test_select.py`, `test_construction_creates_no_sandbox_and_imports_no_modal`.
- [x] PASS — Real modal: bootstrap uploads once; file ops direct (no mirror); remove → not-found —
      `test_modal_executor.py::{test_create_bootstrap_uploads_the_host_workspace_once,
      test_file_ops_read_write_directly_against_the_remote, test_remove_is_reflected_by_a_later_stat_...}` +
      real probe A (bootstrap fidelity).
- [x] PASS — Real modal: fs persists; timeout kills exec, sandbox+fs survive, `note==""` —
      `test_modal_executor.py::{test_filesystem_persists_but_cd_and_export_do_not, test_timeout_kills_the_exec_...}`.
- [x] PASS — Real modal: `aclose()` exports then terminates (no leak) —
      `test_aclose_exports_then_terminates_the_sandbox`; real probe A export fidelity.
- [x] PASS — Real modal: expired sandbox recreated + re-bootstrapped, note says restored —
      `test_a_max_lifetime_expiry_is_recreated_and_rebootstrapped`; real probe B `exec` contrast (restore note).
- [x] PASS — `SandboxExecutor.export()` mid-session leaves sandbox alive; `aclose` = export+destroy —
      `test_export_sweeps_..._leaves_the_sandbox_alive`; `test_export_executor_*`; real probe C.
- [x] PASS — grep proves no `add_local_dir` call (only docstrings), no mirror/mtime/marker machinery,
      `modal_executor.py` + old unit test deleted.
- [x] PASS — Offline unit tests prove file ops + bootstrap + export against a fake fs — `test_modal_backend.py` (33).
- [x] PASS — `make ci` scope green, 0 warnings, `uv lock --check` passes; modal tests migrated; docker + none
      untouched (36 integration green; caveat: off-limits `substack_summarizer.py` E402 is the only repo-wide red).

**Evidence**
```
$ uv run pytest tests/unit -q
1256 passed in 82.05s

$ uv run pytest tests/integration/test_modal_executor.py tests/integration/test_sandbox_capstone.py \
    tests/integration/test_docker_executor.py tests/integration/test_sandbox_teardown.py -v
36 passed in 92.61s   (real modal 10 + capstone 17 + docker 9)

# real modal — file op on an expired sandbox vs exec (the blocking inconsistency)
read_bytes('marker.txt')  -> RAISED modal.exception.NotFoundError (UNNORMALIZED leak)
stat('marker.txt')        -> RAISED modal.exception.NotFoundError (UNNORMALIZED leak)
exec (contrast)           -> exit=0 'bootstrapped' note_has_restore=True
NotFoundError MRO: NotFoundError→Error→_GRPCErrorWrapper→GRPCError→Exception (NOT FileNotFoundError/OSError)

$ uv run python -c "for s in modal.Sandbox.list(): ..."   # cost hygiene, post-QA
total live modal sandboxes: 0   +  0 containers of ghcr.io/astral-sh/uv:python3.12-bookworm-slim
```

**Other issues found**
- **[BLOCKING] File ops do not handle a dead/expired sandbox — `exec` revives, file ops crash.** The two
  `exec` revival triggers (`poll()` + the `NotFoundError` backstop) make a max-lifetime-expired sandbox
  self-heal for `bash`. But `read_bytes`/`write_bytes`/`make_directory`/`stat`/`list_dir`/`remove` call
  `self._fs()` with no liveness check and no `NotFoundError` catch, so on a dead sandbox they leak a raw
  `modal.exception.NotFoundError` (a `GRPCError` — neither `FileNotFoundError` nor `OSError`), and the dead
  `_sandbox` handle isn't even cleared. 081 routes `read`/`write`/`edit` through these, so a file tool called
  right after an expiry crashes the turn where `bash` would gracefully revive — the exact "exec revives but
  file ops crash" contract inconsistency, confirmed on the fake AND real modal. It is neither handled nor
  documented with a task-081-visible note (081's spec assumes clean routing and doesn't mention sandbox-gone).
  Per the grooming bar this must be resolved before 081 builds on the seam blind. Fix (either): (a) catch
  `_is_sandbox_gone` on the file-op path and revive-or-render a clean/normalized error (mirroring `exec`); or
  (b) at minimum add a `ponytail:` in the file-op section of `src/decode/sandbox/modal_backend.py` (~line 281)
  AND a scope/AC line in `tasks/081-...md` stating file ops do not revive and 081's shared layer must handle
  sandbox-gone. Add a regression test either way.
- **[BLOCKING, same root] `list_dir(missing)` is not normalized to `FileNotFoundError`.** `read_bytes`/`stat`/
  `remove` normalize modal's missing-path error to the DockerBackend contract, but `list_dir` (modal_backend.py:325)
  has no try/except, so a missing path raises `SandboxFilesystemNotFoundError` where docker raises
  `FileNotFoundError` — 081's shared layer won't meet ONE contract. Normalize it alongside the fix above.
- [note, non-blocking] A stray `python:3.12-slim` `sleep infinity` container is running, but it is 8h old and
  uses a DIFFERENT image than this project's configured `ghcr.io/astral-sh/uv:...` — a pre-existing manual
  leftover, NOT a leak from 080 (whose docker tests all reap in teardown; 0 configured-image containers remain).
- [note, non-blocking] Bootstrap/export tar carries the whole tree (`ponytail:` already documented — git-diff
  transport is the ADR-0012 Future-work upgrade). Fine for this task.

**VERDICT: FAIL** — all 8 written ACs pass and the rest of the adversarial matrix is green, but the file-op
path crashes (raw `NotFoundError`) on a dead sandbox while `exec` revives — an undocumented contract
inconsistency the seam's consumer (081) will hit blind, plus the related `list_dir(missing)` parity gap.
Both are narrow and cheaply fixed (normalize/handle sandbox-gone on the file-op path + normalize `list_dir`,
or document the limitation task-081-visibly). Nothing else blocks.

### [SWE] 2026-07-04 11:05 — Fixes (Tester FAIL: file ops don't revive + list_dir parity)

Took the FULL fix (the Tester's option 1) — file ops now self-heal a dead sandbox exactly like `exec`,
and `list_dir(missing)` is normalized. No document-and-defer.

**Root cause (one, two facets).** The six file ops called `self._fs()` directly with no sandbox-gone
handling, so on a max-lifetime-expired / terminated sandbox they leaked a raw `modal.exception.NotFoundError`
(MRO `NotFoundError→…→GRPCError` — verified NOT `FileNotFoundError`/`OSError`) and left `_sandbox` a dead
handle, while `exec` revived. Plus `list_dir(missing)` leaked `SandboxFilesystemNotFoundError` where
`DockerBackend.list_dir` raises `FileNotFoundError`.

**Key fact that makes the fix clean (verified on installed modal 1.5.1):**
`issubclass(SandboxFilesystemNotFoundError, NotFoundError) == False` — the missing-FILE error and the
sandbox-gone error are disjoint classes, so catching `NotFoundError` for revival never swallows a
missing-file.

**Files modified**
- `src/decode/sandbox/modal_backend.py` — added `_run_file_op(op)` (the file-op analogue of `exec`'s
  `NotFoundError` backstop): on `_is_sandbox_gone`, drop the dead handle, `_ensure_live()` (recreate +
  re-bootstrap from the host state), retry the op ONCE; a second death → a clean `RuntimeError`
  (`_SANDBOX_LOST_NOTE`), never a raw GRPC type. Routed all six file ops through it. Normalized
  `list_dir(missing)` → `FileNotFoundError` (DockerBackend parity). The one-shot `self._recreated` flag
  (the SAME one `exec`'s revival sets) is set by a file-op revival, so the restore note rides the NEXT
  `exec` result — documented in the `_run_file_op` docstring, the file-ops bullet, and each op's docstring.
  Added `Callable`/`Awaitable` imports + a `_T` TypeVar (type-preserving wrapper).
- `tests/unit/decode/sandbox/test_modal_backend.py` — fake fs gained a `gone` flag (`_guard_live()` raises
  the `_Gone`/`NotFoundError` stand-in on every op) to simulate a shut-down sandbox, and `_list_files`
  now mirrors modal by raising `_FsNotFound` on a missing dir. New tests (offline fakes): file-op revival
  for all six ops (parametrized), the revival note rides the next `exec`, revival-fails-again → clean
  `RuntimeError` + no spin (2 creates) + no raw `_Gone` escape, `list_dir(missing)` parity vs a real
  `DockerBackend`, and a timeout (-1) never triggers revival.
- `tests/integration/test_modal_executor.py` — one real-modal probe: a file op (`read_bytes`) on a
  max-lifetime-expired sandbox revives + re-bootstraps + returns the restored content, and the note rides
  the next `exec` (one-shot).

**Tests — red → green**
- RED (before the source fix, fakes in place): `9 failed, 1 passed` — every new file-op probe raised the
  raw `_Gone` through `_fs()`, and `list_dir(missing)` raised `_FsNotFound` (not `FileNotFoundError`); the
  timeout-never-revives guard already passed.
- GREEN (after the fix): the same 10 → `10 passed`. Full modal backend unit file `43 passed` (was 33).
- Full unit suite: `1266 passed` (was 1256, +10). Sandbox unit subset re-run post-`ruff format`: `78 passed`.
- Real modal (creds present, RAN): `tests/integration/test_modal_executor.py` `11 passed in 90.99s`
  (10 prior + the new file-op-revival probe). Cost hygiene: `modal.Sandbox.list()` → `0` after (no leaks).
- Scoped `ruff format --check` + `ruff check` on the 3 changed files: clean. `ty check` on
  `modal_backend.py`: `All checks passed!`. Repo-wide `format-check`: 168 files clean; repo-wide
  `lint-check` red ONLY in the off-limits WIP `src/decode/tools/substack_summarizer.py` (2× E402,
  pre-existing — untouched).

**Evidence**
```
# RED (source not yet patched; fakes added)
$ uv run pytest tests/unit/decode/sandbox/test_modal_backend.py -q -k "revives_a_dead_sandbox or revival_note_rides or renders_a_clean_error_when_revival or list_dir_on_a_missing_path_matches or timeout_never_triggers_file_op"
9 failed, 1 passed, 33 deselected

# GREEN (after _run_file_op + list_dir normalization)
$ uv run pytest tests/unit/decode/sandbox/test_modal_backend.py -q
43 passed

$ uv run pytest tests/unit -q
1266 passed

$ uv run pytest tests/integration/test_modal_executor.py -q     # real modal
11 passed in 90.99s

$ uv run python -c "import modal; print(len(list(modal.Sandbox.list())))"   # cost hygiene
0
```

**Notes**
- Behaviour parity with `exec` on purpose: `_run_file_op` reuses `_ensure_live` + the `_recreated` flag,
  so a file op and a `bash` both self-heal a dead sandbox and both surface ONE restore note (the file op
  defers it to the next `exec`, having no note channel). This is what 081's shared read/write/edit routing
  needs — one normalized contract, no sandbox-gone leak.
- Scope discipline: only the modal backend + its two test files changed. `deps.cwd`, docker, and none are
  untouched; the off-limits `substack_summarizer.py` / `docs/notes/` were not touched.
- Not committed — handing back to the Tester for re-review.

### [Tester] 2026-07-04 01:57 — QA (round 2 — re-review of the round-1 FAIL fix)

Re-verified the SWE's fix for the two round-1 blockers (file ops don't revive a dead sandbox; `list_dir(missing)`
unnormalized). Verified, did not trust — neutered the fix and confirmed the tests fail without it, checked the
linchpin against real modal, and probed the inverse hazard + note-delivery edges independently.

**Test summary**
- Format / lint: task scope PASS — repo-wide `ruff format --check` clean (168 files); `ruff check` on the 8 changed
  files clean; `ty check src/decode/sandbox/modal_backend.py` → All checks passed. Repo-wide `ruff check` red ONLY in
  the untracked, off-limits WIP `src/decode/tools/substack_summarizer.py` (2× E402) — not in this diff (accepted, unchanged from round 1).
- Unit tests: 1266 passed / 0 failed (`test_modal_backend.py`: 43 passed, was 33 — +10 file-op-revival + list_dir-parity + timeout-never-revives).
- Integration tests: 37 passed / 0 failed — real-modal `test_modal_executor.py` 11 (was 10, +the file-op-revival probe, RAN not skipped),
  capstone 17 (real docker + real modal + proxy), docker `test_docker_executor.py` 8 + `test_sandbox_teardown.py` 1.
- Warnings: 0 (`filterwarnings=error`). `uv lock --check`: 149 packages, no change.

**E2E adversarial pass** (real modal, creds present via ~/.modal.toml; independent hand-rolled fakes for the offline probes)
- Happy path: `uv run pytest tests/integration/test_modal_executor.py -v` → 11/11 PASS incl.
  `test_a_file_op_on_an_expired_sandbox_revives_and_re_bootstraps` (real 10s max-lifetime expiry → `read_bytes` self-heals + re-bootstraps + returns restored content, note rides the next exec) (PASS).
- Break path 1 (anti-theater — neuter the fix): temporarily made `_run_file_op` an early `return await op()` passthrough →
  the 6 parametrized `test_a_file_op_revives_a_dead_sandbox_and_retries_once` + `..._revival_note_rides_the_next_exec` +
  `..._renders_a_clean_error_when_revival_fails_again` = **8 FAILED** with the raw `_Gone` (NotFoundError) escaping; timeout-never-revives still passed. Restored; re-verified exact (format-check clean + 43 pass) (PASS — the tests genuinely bind the fix).
- Break path 2 (anti-theater — remove list_dir normalization): re-raised `SandboxFilesystemNotFoundError` unchanged →
  `test_list_dir_on_a_missing_path_matches_dockerbackend` **FAILED** (leaked `_FsNotFound`, docker raises `FileNotFoundError`). Restored (PASS).
- Break path 3 (linchpin, verified against installed modal 1.5.1): `issubclass(SandboxFilesystemNotFoundError, NotFoundError)==False`
  and `...NotADirectoryError, NotFoundError)==False`; neither missing-file class subclasses `FileNotFoundError`/`OSError`; `NotFoundError`
  MRO ends in `GRPCError` (not OSError, so it reaches the `_is_sandbox_gone` check, not the `(RuntimeError,OSError)` short-circuit). Missing-file vs sandbox-gone are provably disjoint (PASS).
- Break path 4 (inverse hazard, independent fake): a genuine missing-file on a LIVE sandbox → `read_bytes`/`list_dir`→FileNotFoundError,
  `stat`→None, `remove`→no-raise, with **0 revival creates**, `_recreated` stays False, the live handle is not dropped, no revival log. A missing FILE never triggers a spurious sandbox revival (PASS).
- Break path 5 (note-delivery, independent fake): TWO back-to-back file-op revivals (A→B→C) before a run() → 2 creates, boolean flag (not a counter),
  the 1st exec carries exactly one restore note, the 2nd is empty, `_recreated` False after. No double-note, no lost note (PASS).
- Cost hygiene after the full run: `modal.Sandbox.list()` → 0 live; `docker ps` → no configured-image containers (clean, no leaks).

**Acceptance criteria** (all 8 written ACs re-confirmed PASS; both round-1 blockers now RESOLVED)
- [x] PASS — `SANDBOX_MODE=modal` → `SandboxExecutor(ModalBackend)`, inert — `__init__.py` diff + `test_construction_creates_no_sandbox_and_imports_no_modal` + `test_importing_decode_does_not_import_modal`.
- [x] PASS — Real modal: bootstrap once; file ops direct (no mirror); remove → not-found — `test_modal_executor.py` (11/11).
- [x] PASS — Real modal: fs persists; timeout kills exec, sandbox+fs survive, note=="" — same suite.
- [x] PASS — Real modal: aclose exports then terminates (no leak) — `test_aclose_exports_then_terminates_the_sandbox`.
- [x] PASS — Real modal: expired sandbox recreated + re-bootstrapped, note says restored — `test_a_max_lifetime_expiry_is_recreated_and_rebootstrapped`.
- [x] PASS — `export()` mid-session leaves sandbox alive; aclose = export+destroy — `test_export_sweeps_..._leaves_the_sandbox_alive` + `export_executor` unit tests.
- [x] PASS — grep proves no `add_local_dir` call (only prose), no mirror/mtime/marker machinery, `modal_executor.py` + old unit test deleted.
- [x] PASS — Offline unit tests prove file ops + bootstrap + export + revival against a fake fs — `test_modal_backend.py` (43).
- [x] PASS — `make ci` scope green, 0 warnings, `uv lock --check` passes; modal tests migrated; docker + none untouched.

RESOLVED round-1 blockers:
- File ops now self-heal a dead sandbox exactly like `exec` (`_run_file_op`: sandbox-gone → drop handle → `_ensure_live` → retry once → 2nd death = clean `RuntimeError`), all six ops routed through it; restore note rides the next `exec` via the shared `_recreated` flag. Proven by the parametrized offline tests, the real-modal probe, and the anti-theater neuter.
- `list_dir(missing)` normalized to `FileNotFoundError` (DockerBackend parity), proven by `test_list_dir_on_a_missing_path_matches_dockerbackend` + the anti-theater removal.

**Evidence**
```
$ uv run pytest tests/unit/decode/sandbox/test_modal_backend.py -q
43 passed in 1.31s

$ uv run pytest tests/unit -q
1266 passed in 81.27s

$ uv run pytest tests/integration/test_modal_executor.py -v      # real modal, creds present (RAN)
11 passed in 86.37s   (incl. test_a_file_op_on_an_expired_sandbox_revives_and_re_bootstraps)

$ uv run pytest tests/integration/test_sandbox_capstone.py tests/integration/test_docker_executor.py tests/integration/test_sandbox_teardown.py -v
26 passed in 29.87s   (capstone 17 incl. real docker + real modal + proxy; docker 8; teardown 1)

# anti-theater — revival neutered
$ uv run pytest tests/unit/decode/sandbox/test_modal_backend.py -k "revives_a_dead_sandbox or revival_note_rides or renders_a_clean_error_when_revival or timeout_never_triggers_file_op"
8 failed, 1 passed   → restored → 43 passed

# linchpin (installed modal 1.5.1)
issubclass(SandboxFilesystemNotFoundError, NotFoundError) = False
issubclass(SandboxFilesystemNotADirectoryError, NotFoundError) = False
NotFoundError MRO: NotFoundError -> Error -> _GRPCErrorWrapper -> GRPCError -> Exception ...

$ uv run python -c "import modal; print(len(list(modal.Sandbox.list())))"   # cost hygiene
0
```

**Other issues found**
- [note, non-blocking] Repo-wide lint red ONLY on the untracked off-limits WIP `src/decode/tools/substack_summarizer.py` (2× E402) and `docs/notes/` is untracked — neither is in task 080's staged/modified diff; the SWE did not touch them. Same as round 1; not a 080 blocker.
- [note, non-blocking] Bootstrap/export still tars the whole tree (`ponytail:` already documented — git-diff transport is the ADR-0012 Future-work upgrade). Fine for this task.
- No new issues. The `_run_file_op` / `list_dir` code path is clean: type-preserving wrapper, no dead code, revival logged at INFO, and (verified) a missing-file after a re-bootstrap correctly falls through each op's own normalization (→ None / FileNotFoundError), not a crash.

**VERDICT: PASS** — both round-1 blockers are fixed and proven (not theater): all six file ops now revive a dead sandbox
exactly like `exec` and `list_dir(missing)` is docker-parity normalized. Full suite green (1266 unit + 37 integration, 0 warnings),
the real-modal file-op-revival probe RAN and passed, the linchpin holds on real modal, the inverse hazard and double-note edges
are clean, and cost hygiene shows zero leaks. Hand off to PA for acceptance review.
