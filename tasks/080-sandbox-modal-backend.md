---
id: 080-sandbox-modal-backend
feature: isolated-workspace
status: pending
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

- [ ] `SANDBOX_MODE=modal` → `SandboxExecutor(ModalBackend)`; construction inert (no `modal` import).
- [ ] **Real modal (skipif):** `create` bootstrap-uploads the host workspace once; `ModalBackend` file
  ops read/write **directly against the remote** — a file `bash` writes in `/workspace` is returned by
  `read_bytes` **without any mirror**, and a `remove` is reflected by a subsequent `stat`/`read_bytes`
  raising not-found (no deletion-blindness).
- [ ] **Real modal (skipif):** the filesystem persists across `run()`s; a timeout kills the exec, the
  sandbox + fs survive, `note==""`.
- [ ] **Real modal (skipif):** `aclose()` runs the export sweep (a file created in `/workspace` appears
  under host `.decode/sandbox`) then terminates the sandbox (no leak).
- [ ] **Real modal (skipif):** a remotely-ended sandbox is recreated + re-bootstrapped from the host
  state, and the result `note` says it was restored from the last local state (and later changes may be
  lost).
- [ ] `SandboxExecutor.export()` can run mid-session (docker no-op; modal sweeps `/workspace` → host
  `.decode/sandbox`) leaving the sandbox alive; `aclose()` = export + destroy.
- [ ] `grep` proves modal never calls `add_local_dir`, uses no mirror/mtime/marker machinery, and
  `modal_executor.py` is gone.
- [ ] Offline unit tests prove `ModalBackend` file ops + bootstrap + export against a fake filesystem.
- [ ] `make ci` green, 0 warnings, `uv lock --check` passes; modal tests migrated; docker + none
  untouched.

## Out of scope

- File-tool routing through the seam (081), clone-at-launch / CLI (082).
- **Any per-call sync / mtime deltas / size caps** (retired). **Deletion propagation is a non-issue
  now** — file ops are direct, so there is no stale mirror to go blind.

## Log
