---
id: 081-sandbox-tool-scope-workspace
feature: isolated-workspace
status: pending
---

# Tool scope → isolated workspace: route file tools through the backend seam + harness-home split

Tags: `sandbox`, `workspace`, `wiring`, `tools`
Depends on: #079, #080
Blocks: #082

The crux task (ADR-0012): in a sandbox mode the file/search tools operate on the sandbox filesystem
**through the backend seam** (pi's ExecutionEnv "swap the set"), the agent's tool scope (`deps.cwd`)
becomes the **logical** workspace root, and **every harness artifact stays anchored to the launch cwd**
("harness home"). `none` mode is byte-identical (direct pathlib, no seam). This is the invariant-
critical wiring — `extract_on_exit`, the session log, the permission file, skills, and memory must use
the launch cwd, not `deps.cwd`.

## Scope

- **File/search tool routing** (`src/decode/tools/files.py`: `read`/`write`/`edit`/`glob`/`grep`):
  - `none` mode → today's **direct-pathlib** code, byte-identical (no seam engaged).
  - sandbox mode → route through the active session's backend: `read`/`write`/`edit` call the
    backend's `read_bytes`/`write_bytes`/`make_directory`/`stat` on **logical** paths; `glob`/`grep`
    run as backend `exec` (find/grep) scoped to the workspace, with **output-parity to the host
    implementations** (same flags/format the model already sees).
  - **Shared logic stays host-side above the seam:** containment/normalization is **backend-agnostic
    path math** (PurePosixPath join + `..` resolution against logical root `/`, reject escapes) — NOT
    host `Path.resolve` (modal workspace paths are not host paths); edit's search/replace, truncation,
    and rendering are unchanged shared code.
  - **The seam:** file tools reach the active backend via a module-level accessor (mirroring bash's
    `_EXECUTOR`) set on warm/install and cleared on `close_executor`; in `none` mode it yields nothing
    → the direct-pathlib path. Both `bash` and the file tools share the **one** backend per session
    (same container/sandbox).
- **Harness-home vs tool-scope split:**
  - `AgentDeps` (`agent/deps.py`): add `harness_home: Path` (launch cwd; defaults to `cwd` for
    back-compat). `cwd` = tool scope (logical workspace in sandbox mode); `harness_home` = artifact
    root. Equal in `none` mode.
  - Route roots to the right readers: file/search tools + bash → `deps.cwd`; skills catalog hook +
    `skill` dispatcher + `/<skill>`/`SlashCompleter` + memory injection (AGENTS.md walk + MEMORY.md) →
    `deps.harness_home` (harness artifacts).
  - `tui/app.py`: compute `harness_home = Path.cwd()`; in sandbox mode `workspace =
    prepare_workspace(harness_home, repo=None)` (empty this task) + warm executor with it; set
    `deps = AgentDeps(cwd=(workspace if sandbox else harness_home), harness_home=harness_home, …)`;
    thread `harness_home` into `extract_on_exit(...)`, `_handle_clear_command(cwd=harness_home)`,
    `SessionLog.create(cwd=harness_home)`, and the permission-file load/persist.
  - `runtime/flow.py`: `_build_headless_deps`/`_build_hitl_deps` set `cwd=(workspace if sandbox else
    Path.cwd())` + `harness_home=Path.cwd()`; prepare + start the executor with the workspace (both
    lazy and `_sandbox_proxy`-installed paths).
- **LSP (revised per addendum):** the `lsp` tool + post-edit Diagnostics Enricher operate on the
  **workspace path** — in `none` + `docker` they keep working against `.decode/sandbox` files (docker:
  live mount; ty runs host-side pointed at the mount path); in **modal** they are **best-effort-disabled
  with a friendly note** (ty cannot reach the remote fs), consistent with ADR-0007's best-effort
  posture. (ADR-0012 records ty-inside-the-sandbox as the upgrade path.)
- **Tool placement (host-side, unchanged in code, confirmed by tests):** `enter_plan_mode`/
  `exit_plan_mode`, `sleep`, `todo_write`, `skill`, `ask_user`, `web_fetch` stay host-side; `web_fetch`
  **stays gated** even in sandbox mode (it reaches the host network). Tasks/session/memory artifacts
  anchor to harness home.

## Acceptance criteria

- [ ] `none` mode: `deps.cwd == deps.harness_home == launch cwd`; file tools use direct pathlib —
  byte-identical to today (a `_render`/behavior pin test).
- [ ] Sandbox mode (fake backend): `read`/`write`/`edit` route through the backend's file ops on
  logical paths and land **inside the workspace**; containment rejects `..` escapes via backend-
  agnostic path math (no host `Path.resolve`).
- [ ] Sandbox mode: `glob`/`grep` execute in the sandbox (backend `exec` find/grep), return workspace-
  scoped results with output-parity to the host implementations.
- [ ] `MEMORY.md` (exit + `/clear`), the session-log file, and the permission file resolve under
  **launch cwd**, not the workspace (proven by inspecting where each is written in a sandbox mode).
- [ ] Skills catalog + dispatcher + `/<skill>` + AGENTS.md/MEMORY.md injection resolve from **launch
  cwd**.
- [ ] `lsp` + post-edit diagnostics operate on workspace paths in `none` + `docker`; in `modal` they
  are best-effort-disabled with a friendly note (an AC test asserts each).
- [ ] `web_fetch` stays gated in sandbox mode; the other host tools (`sleep`/`todo_write`/`skill`/
  `ask_user`/`enter`/`exit_plan_mode`) behave identically to `none` mode.
- [ ] `make ci` green, 0 warnings, `uv lock --check` passes.

## Out of scope

- The `git clone` of a `--repo` + CLI flags/guard (082); the unified `bash` description + progress
  copy (082). ty-inside-the-sandbox (ADR-0012 future-work).

## Log
