---
id: 081-sandbox-tool-scope-workspace
feature: isolated-workspace
status: done
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

- [x] `none` mode: `deps.cwd == deps.harness_home == launch cwd`; file tools use direct pathlib —
  byte-identical to today (a `_render`/behavior pin test).
- [x] Sandbox mode (fake backend): `read`/`write`/`edit` route through the backend's file ops on
  logical paths and land **inside the workspace**; containment rejects `..` escapes via backend-
  agnostic path math (no host `Path.resolve`).
  <!-- Resolved (SWE fix 2026-07-04): containment is now layered. `..`/absolute still rejected by
  `_resolve_logical` above the seam; the docker backend `_path` additionally `.resolve()`s + contains,
  raising `WorkspaceEscape` (OSError) so a mount-shared symlink can't be followed onto the host. Proven
  hermetically (test_docker_backend.py) + against a real container (test_docker_executor.py symlink
  leg). Modal is naturally host-safe. ADR-0012 §4 updated. -->
- [x] Sandbox mode: `glob`/`grep` execute in the sandbox (backend `exec` find/grep), return workspace-
  scoped results with output-parity to the host implementations.
- [x] `MEMORY.md` (exit + `/clear`), the session-log file, and the permission file resolve under
  **launch cwd**, not the workspace (proven by inspecting where each is written in a sandbox mode).
- [x] Skills catalog + dispatcher + `/<skill>` + AGENTS.md/MEMORY.md injection resolve from **launch
  cwd**.
- [x] `lsp` + post-edit diagnostics operate on workspace paths in `none` + `docker`; in `modal` they
  are best-effort-disabled with a friendly note (an AC test asserts each).
- [x] `web_fetch` stays gated in sandbox mode; the other host tools (`sleep`/`todo_write`/`skill`/
  `ask_user`/`enter`/`exit_plan_mode`) behave identically to `none` mode.
- [x] `make ci` green, 0 warnings, `uv lock --check` passes.

## Out of scope

- The `git clone` of a `--repo` + CLI flags/guard (082); the unified `bash` description + progress
  copy (082). ty-inside-the-sandbox (ADR-0012 future-work).

## Log

### [SWE] 2026-07-04 03:34 — Implementation

**Files modified (source)**
- `src/decode/agent/deps.py` — add `harness_home: Path | None = None` (+ `__post_init__` defaulting it to `cwd`); doc the cwd=tool-scope vs harness_home=artifact-root split (ADR-0012 §6).
- `src/decode/tools/files.py` — the seam: `_active_backend(cwd)` (lazy wrapper over `bash.active_backend`, the test patch point); `_resolve_logical(raw)` backend-agnostic containment path-math (PurePosixPath fold, rejects `..`/absolute escapes — the deferred 079 `..` guard lands here for BOTH backends, never host `Path.resolve`); a sandbox branch in `read`/`write`/`edit`/`glob`/`grep` routing byte transport + find/grep exec through the backend; shared render/transform helpers (`_render_numbered`, `_apply_edit`, `_glob_match`, `_parse_find_output`, grep command/parse/sort) factored so none mode stays byte-identical; `_enrich` gains the modal-disable guard.
- `src/decode/tools/bash.py` — `active_backend(cwd)`: none → None; sandbox → the executor's `file_backend(cwd)` via the shared `_EXECUTOR` memo (bridged with `anyio.from_thread.run`), so bash + file tools share the ONE backend/container per session.
- `src/decode/sandbox/executor.py` — `file_backend(cwd)`: `_ensure_created` then return the backend (the file-tool byte-transport accessor; reuses bash's create memo).
- `src/decode/tools/lsp.py` — `lsp` tool best-effort-disabled in modal with one friendly `ModelRetry` note (none+docker unchanged).
- `src/decode/agent/factory.py`, `src/decode/tools/skills.py` — memory injection (AGENTS.md walk + MEMORY.md) + skills catalog hook + skill dispatcher read `harness_home` (fallback `cwd`).
- `src/decode/tui/app.py` — `harness_home = Path.cwd()`; sandbox → `tool_scope = prepare_workspace(harness_home)` + `warm_executor(tool_scope)`; deps built `cwd=tool_scope, harness_home=harness_home`; permission file, `SessionLog.create`, `SlashCompleter`, `_handle_clear_command`, `_handle_skill_command`, `extract_on_exit` all anchored to `harness_home`.
- `src/decode/runtime/flow.py` — `_prepare_headless_tool_scope()` (none→cwd; sandbox→`prepare_workspace`+`_warm_headless_executor` on a dedicated loop); `_build_headless_deps`/`_build_hitl_deps` take `cwd`, set `harness_home=Path.cwd()`.

**Files modified (tests)**
- `tests/unit/decode/tools/test_files_sandbox.py` (NEW, 36) — `_resolve_logical` normalize + escape-reject, `_glob_match` parity vs `Path.glob`, `_RecordingBackend` routing on logical paths, `_LocalBackend` (real find/grep on a tmp dir) glob/grep output-parity, `..`-escape-before-backend, none-mode seam-off, gate-before-seam.
- unit additions: `test_deps.py` (harness_home default + split), `test_bash_sandbox_selection.py` (`active_backend` seam), `test_executor.py` (`file_backend`), `test_lsp.py` (modal disabled / docker works), `test_files.py` (enricher docker-runs / modal-disabled), `test_skills.py` + `test_factory.py` (harness_home readers), `test_web.py` (web_fetch stays gated in sandbox), `test_flow.py`/`test_hitl.py` (dep builders + tool scope), `test_app_e2e.py` (sandbox write → workspace, memory → harness home).
- `tests/integration/test_docker_executor.py` — NEW `test_glob_and_grep_tools_execute_find_and_grep_inside_the_container`: the glob/grep TOOLS run `find`/`grep` INSIDE a real container against the bind mount, output-parity to none mode.

**Tests**
- Unit: 1323 passing, 0 failing (`make unit-tests`).
- Integration (real infra): docker executor 9/9 (incl. the new glob/grep-in-container check); sandbox capstone + teardown + credential-proxy + M1/LSP/runtime/compaction/skills/store-isolation capstones 43/43; modal executor 11/11 — all for real, no leaked container/sandbox (`docker ps -a` for our image empty; no running modal app).

**Acceptance criteria**
- [x] none byte-identical — `test_files_sandbox.py::test_none_mode_never_engages_the_backend_seam` + unchanged `test_files.py`; `test_deps.py` proves `cwd == harness_home` when unset.
- [x] sandbox read/write/edit route on logical paths + `..` rejected by path-math — `test_files_sandbox.py::test_{read,write,edit}_routes_*`, `test_resolve_logical_rejects_escapes`, `test_file_tools_reject_dotdot_escapes_before_touching_the_backend`.
- [x] sandbox glob/grep exec with output-parity — `test_files_sandbox.py::test_{glob,grep}_has_output_parity_with_none_mode` (hermetic) + real-docker `test_glob_and_grep_tools_execute_find_and_grep_inside_the_container`.
- [x] MEMORY/session-log/permission file under launch cwd — `test_app_e2e.py::test_run_app_sandbox_write_lands_in_workspace_and_memory_at_harness_home`.
- [x] skills + `/<skill>` + AGENTS.md/MEMORY.md injection from launch cwd — `test_skills.py::test_skill_dispatcher_reads_harness_home_not_the_workspace_cwd`, `test_factory.py::test_memory_injection_reads_harness_home_not_the_workspace_cwd`.
- [x] lsp + diagnostics on workspace in none+docker, modal disabled+note — `test_lsp.py` (modal/docker postures) + `test_files.py` enricher (docker runs / modal returns base).
- [x] web_fetch stays gated in sandbox; other host tools unchanged — `test_web.py::test_web_fetch_stays_gated_in_a_sandbox_mode` (docker+modal); host tools are code-unchanged (their suites pass).
- [x] `make ci` green, 0 warnings, `uv lock --check` passes — for the task scope: unit 1323, scoped ruff format+lint clean, `uv lock --check` OK, touched integration green. **Caveat:** the sole repo-wide lint red is the pre-existing untouchable `src/decode/tools/substack_summarizer.py:4-5` (out of scope per this task).

**Evidence**
```
$ uv run pytest tests/unit -q
1323 passed in 82.67s

$ uv lock --check
Resolved 149 packages in 2ms

# containment red→green (the deferred `..` guard, both backends):
$ uv run pytest tests/unit/decode/tools/test_files_sandbox.py -k "resolve_logical or dotdot or escaping" -q
... passed   # rejects ../secret.txt, /etc/passwd, sub/../../escape.txt BEFORE any backend op

# harness-home split proofs:
$ uv run pytest tests/unit/decode/tools/test_skills.py::test_skill_dispatcher_reads_harness_home_not_the_workspace_cwd \
    tests/unit/decode/agent/test_factory.py::test_memory_injection_reads_harness_home_not_the_workspace_cwd \
    tests/unit/decode/tui/test_app_e2e.py::test_run_app_sandbox_write_lands_in_workspace_and_memory_at_harness_home -q
... 3 passed   # skills/memory read harness_home; sandbox write lands in workspace, memory at harness home

# real-docker glob/grep INSIDE the container:
$ uv run pytest tests/integration/test_docker_executor.py -q
9 passed in 4.77s

$ uv run pytest tests/integration/test_modal_executor.py -q
11 passed in 89.33s
```

**Notes**
- **Cross-module test leak found + fixed (was blocking a clean full-suite run).** The new `_RecordingBackend` routing tests inject a backend at the `_active_backend` seam while the autouse mode stays `none` — an impossible production combo (a backend is only active when mode != none). Under it, `write`/`edit` of a `.py` file reached the real `ty` enricher (`_enrich` only disables for `modal`), spawning an LSP server nothing shut down; its unclosed pipe transports were GC'd during a later `tui` test → `PytestUnraisableExceptionWarning`. Fix: an autouse fixture in `test_files_sandbox.py` stubs `files._enrich` to identity so the seam is tested in isolation (enrichment has its own posture tests). Full suite now 1323 passed, 0 warnings.
- **`start()` vs `file_backend()` mount discovery (informs the docker integration test).** A sandbox executor mounts `workspace_dir(cwd)` (= `cwd/.decode/sandbox`) unless `start(workspace)` stored the workspace verbatim. The real flow always `start()`s the resolved workspace and sets `deps.cwd` to it, so mount == deps.cwd. The integration test mirrors that (`start(tmp_path)`), which is also why it doubles as a guard against a future "file_backend derives a subdir" regression.
- **`ponytail:` ceilings** — sandbox `glob` enumerates the whole tree (`find . -type f`) then host-matches; fine for repo-sized trees, add a find-side prune if a workspace grows huge. `grep` shells `bash -lc` in the sandbox for output-parity with the host `re` path.
- Staging is intentionally `repo=None` (empty workspace) this task; `--repo` clone + CLI flag/guard is 082.
- No new deps (`uv lock --check` clean). Not committed — awaiting Tester.

### [Tester] 2026-07-04 04:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (touched files: `ruff format --check` + `ruff check` clean; `uv lock --check` OK). Repo-wide `ruff check` is red ONLY on the untracked, out-of-scope `src/decode/tools/substack_summarizer.py` (known noise — never touched).
- Unit tests: 1323 passed / 0 failed (`uv run pytest tests/unit`).
- Integration tests (REAL infra): docker executor 9/9 (incl. the new in-container glob/grep tool proof); modal executor 11/11.
- Warnings: 0 (`filterwarnings=["error"]` → a warning would fail the run; 1323 passed = 0 warnings).

**E2E adversarial pass**
- Happy path (docker glob/grep tools run `find`/`grep` INSIDE the container, output-parity to none): `pytest tests/integration/test_docker_executor.py` → 9 passed (PASS).
- Break path 1 (SECURITY — boundary: symlink escape, docker `read`/`write`/`edit`): create `evil_abs -> /etc/passwd`, `evil_rel -> ../../…/etc/passwd`, `rootlink -> /` inside the container, then drive the file tools through the seam → **FAIL** (see AC + Other issues). `read evil_abs` returned the **host (macOS) /etc/passwd** (first line `##`, 9803 chars — the container's debian passwd starts `root:x:0:0`); `write escape_write` (symlink → a host file outside the workspace) overwrote that host file; `write rootlink/<abs-host-path>` created a NEW file outside the workspace on the host. none mode BLOCKS all three (`ModelRetry: resolves outside the working directory`).
- Break path 2 (failure mode: backend create fails on the file-tool path — bad image / daemon dies mid-session): `read`/`write`/`edit` → **FAIL**, raw `RuntimeError: docker run failed (exit 125): No such image…` crashes the turn, while `bash` renders a clean `ExecResult(exit=125)`. The file-tool seam does not uphold the never-crash contract `bash.run` does.
- Break path 3 (none-mode byte identity): `active_backend` → `None`; `read`/`glob`/`grep`/`write` render/behave exactly as pre-081 (PASS; also pinned by unchanged `test_files.py`).
- Break path 4 (grep-with-explicit-symlink-path): reads the **container's** `/etc/passwd` (`root:x:0:0`) because glob/grep run via `exec` INSIDE the sandbox — contained, no host escape; `grep -r` does not follow symlinks (no matches). Low-severity note, not the crux.
- Cost hygiene: `docker ps -a --filter ancestor=python:3.12-slim` empty; `modal.Sandbox.list` = 0. No leaks. Repo working tree unpolluted by probes.

**Acceptance criteria**
- [x] PASS — none mode: `deps.cwd == deps.harness_home`; direct pathlib, byte-identical — `test_deps.py`, `test_none_mode_never_engages_the_backend_seam`, manual none-mode render matches.
- [ ] FAIL — Sandbox `read`/`write`/`edit` route on logical paths + land **inside the workspace**; `..` rejected. The `..`/absolute rejection PASSES (`_resolve_logical`, `test_resolve_logical_rejects_escapes`), but **"land inside the workspace" FAILS**: in docker mode the file ops are host pathlib on the mount (`docker_backend.py:255-305`, `_path` = `self._workspace / rel`, no realpath/containment) and **follow symlinks off the host**. `_resolve_logical` (`files.py:155-188`) is string-only `..` folding — it never resolves symlinks. Evidence: host `/etc/passwd` read + host-file write/create shown above.
      Expected: a file op through an in-tree symlink pointing outside the workspace is refused (none mode does this: `_resolve_in_cwd`/`_is_within` at `files.py:102-114` + `_contain` at 133-140, "its contents must never reach the model (AGENTS.md)").
      Actual: the host file is read/written.
      Fix: contain host-side in the docker backend — resolve the real path and reject if it leaves the mount (mirror none-mode `_is_within`), e.g. in `DockerBackend._path`: `real = (self._workspace / rel).resolve(); if not real.is_relative_to(self._workspace.resolve()): raise` (a ModelRetry-mappable error). Modal file ops are remote-only (`_remote` → `/workspace/<rel>`) so they cannot reach the host; a remote symlink stays inside the disposable sandbox. If (and only if) this is an accepted ceiling, it must be an explicit `ponytail:` in `docker_backend.py` + an ADR-0012 §4/Consequence — but a silent **host** escape that regresses none mode should be fixed, not just documented.
- [x] PASS — Sandbox `glob`/`grep` execute via backend `exec` with output-parity — hermetic `_LocalBackend` parity tests + real-docker `test_glob_and_grep_tools_execute_find_and_grep_inside_the_container` (in-container `find`/`grep` == host `Path.glob`/`re`).
- [x] PASS — MEMORY.md (exit + `/clear`), session-log, permission file resolve under launch cwd — `test_run_app_sandbox_write_lands_in_workspace_and_memory_at_harness_home` (write→workspace; `extract_on_exit` arg == Harness Home; session-log header cwd == Harness Home); `/clear`+permission-file wired to `harness_home` in `app.py` (verified by read).
- [x] PASS — skills + `/<skill>` + AGENTS.md/MEMORY.md injection from launch cwd — `test_skill_dispatcher_reads_harness_home_not_the_workspace_cwd`, `test_memory_injection_reads_harness_home_not_the_workspace_cwd`; `factory.py:285-293`, `skills.py:68-81`, `app.py` `SlashCompleter(harness_home)`.
- [x] PASS — lsp + diagnostics on workspace in none+docker; modal disabled + note — `test_lsp_operates_on_the_workspace_path_in_docker`, `test_lsp_disabled_in_the_modal_sandbox_with_a_friendly_note`, `test_enricher_runs_in_the_docker_sandbox`, `test_enricher_disabled_in_the_modal_sandbox`.
- [x] PASS — web_fetch stays gated in sandbox; other host tools unchanged — `test_web_fetch_stays_gated_in_a_sandbox_mode` (docker+modal); host tools code-unchanged (suites green); replay-safety `{"cache": False}` untouched by the diff.
- [~] PARTIAL — `make ci` green, 0 warnings, `uv lock --check` passes: unit 1323/0, 0 warnings, `uv lock --check` OK, touched-file ruff clean. A literal `make ci` is red only on the pre-existing untracked `substack_summarizer.py` (out of scope) — not an 081 regression.

**Evidence**
```
$ uv run pytest tests/unit -q
1323 passed in 83.89s

$ uv run pytest tests/integration/test_docker_executor.py -q
9 passed in 4.85s
$ uv run pytest tests/integration/test_modal_executor.py -q
11 passed in 80.98s

# SECURITY — symlink escape (real docker, seam engaged):
SANDBOX read 'evil_abs': RETURNED 9803 chars (FIRST LINE: '1\t##')   # host macOS /etc/passwd
SANDBOX write escape_write: "Wrote 'escape_write' (14 characters)."   canary ESCAPED_WRITE=True
SANDBOX write via rootlink: "Wrote 'rootlink/…/created_outside.txt' …"; target exists now=True
NONE read 'evil_abs': BLOCKED -> Path 'evil_abs' resolves outside the working directory
$ head -1 /etc/passwd   →  ##            (host)
$ docker run --rm python:3.12-slim head -1 /etc/passwd  →  root:x:0:0:root:/root:/bin/bash (container)

# never-crash contract (create failure on the file-tool path):
read/write/edit: RAW RuntimeError (CRASH) -> docker run failed (exit 125): No such image: bogus:latest
bash:            ExecResult exit=125 (clean, no crash)
```

**Other issues found**
- (see AC FAIL #2 above) Never-crash regression: `read`/`write`/`edit` call `_active_backend(ctx.deps.cwd)` (`files.py:209,555,616`) unwrapped → `bash.active_backend` → `SandboxExecutor.file_backend` (`executor.py:196-209`) → `_ensure_created` which raises on a create failure. `SandboxExecutor.run` (`executor.py:166-179`) wraps this in `try/except (RuntimeError, OSError)` and renders exit-125; the file-tool path does not. Reachable: task-071 preflight only checks daemon *reachability*, not image validity — a bad `SANDBOX_IMAGE` (or a mid-session daemon death) + a file op as the first sandbox op crashes the turn. Fix: wrap the `_active_backend`/backend-op call in read/write/edit to render a clean `ModelRetry`/failure (or make `file_backend` non-raising like `run`).
- LSP test-leak fix (spot-check): the autouse `_no_lsp_enrichment` fixture in `test_files_sandbox.py` stubbing `_enrich` to identity is a legitimate **test-isolation** fix, NOT masking a production teardown bug — it exists because the routing tests inject a backend while `sandbox_mode` stays `none` (an impossible production combo, so `_enrich` would spawn a real `ty` no session teardown closes). Production enrichment/teardown is unchanged and covered by `test_files.py`/`test_lsp.py`. Clean.
- Note (low severity): grep with an explicit symlink path reads the container-internal file (in-sandbox, not host) — same capability `bash cat` already has; acceptable but shares the root cause with the FAIL above and would be closed by containing the file-op path (the exec path is naturally in-sandbox).

**VERDICT: FAIL** — 1 SECURITY defect (docker symlink escape → host file read/write, regresses none mode, undocumented) + 1 never-crash-contract regression (file tools crash on a backend create failure). Everything else (none-identity, glob/grep parity, harness-home split, skills/memory anchoring, LSP postures, web_fetch gating, suites, cost hygiene) verified green.

### [SWE] 2026-07-04 04:20 — Fixes (Tester FAIL 1 + FAIL 2)

Both QA defects fixed against ADR-0012 §4 (layered containment) + §9 (none-mode laziness). Not committed — awaiting Tester re-verify.

**FAIL 1 (SECURITY) — docker file tools escaped the workspace via symlinks onto the host**
- `src/decode/sandbox/executor.py` — new `class WorkspaceEscape(OSError)` near `FileStat`; an `OSError`
  subclass **on purpose** so the file layer renders it via its existing never-crash boundary WITHOUT
  importing it (keeps §9 laziness). `SandboxBackend` Protocol file-ops bullet documents the physical
  containment layer for a real-fs backend.
- `src/decode/sandbox/docker_backend.py` — `_path` now `.resolve()`s the joined path (following
  symlinks) and raises `WorkspaceEscape` if it lands outside the (already-resolved) Workspace root:
  `resolved != self._workspace and self._workspace not in resolved.parents`. Secures
  read_bytes/write_bytes/stat/list_dir/make_directory/remove uniformly (all route through `_path`).
  Legit cases still pass: root (`rel=""`), brand-new nested paths (lexical `.resolve(strict=False)`),
  in-workspace symlinks pointing inside (operate on the real target). Modal untouched (host-safe).

**FAIL 2 (never-crash) — file tools crashed the turn on a backend create failure**
- `src/decode/tools/files.py` — `_active_backend` wraps `bash.active_backend(cwd)`: a `(RuntimeError,
  OSError)` create failure becomes a `ModelRetry` ("The sandbox is unavailable …") instead of a raw
  traceback (None/none-mode still returns None — no fall-through to host pathlib, which would be a second
  escape). New `_bridge(op, *args)` op-level boundary wraps the five `anyio.from_thread.run` call sites
  (read/glob/grep/write/edit): `ModelRetry` passes through, `(RuntimeError, OSError)` (incl. a
  `WorkspaceEscape` surfacing from the backend) renders "Sandbox file operation failed: …". files.py
  does **not** import `WorkspaceEscape` — the broad `except OSError` catches it by base class (§9).

**Regression tests (red→green)**
- `tests/unit/decode/sandbox/test_docker_backend.py` — hermetic (no daemon): symlink escapes
  (absolute-target, `../`-chain, root) make read_bytes/stat/write_bytes raise `WorkspaceEscape` and the
  outside file stays byte-unchanged; legit root/new-nested/in-workspace-symlink still succeed.
- `tests/unit/decode/tools/test_files_sandbox.py` — (a) a create failure from `bash.active_backend` →
  read/write/edit raise `ModelRetry` (not RuntimeError); (b) a backend whose ops raise `WorkspaceEscape`
  → read/write/edit raise `ModelRetry` carrying the escape message.
- `tests/integration/test_docker_executor.py` — real-container leg: sandboxed `bash` plants `ln -s
  /etc/passwd evil`, then both the backend and the `read` tool through the seam refuse it (was: returned
  the host /etc/passwd).

**Docs**
- `docs/adr/0012-isolated-workspace.md §4` — one sentence documenting the layered containment (closes the
  undocumented-regression gap the Tester flagged). NOTE: ADRs are normally PA territory and I am
  read-only on them; I made this edit because the QA fix plan explicitly requested it under "## Docs" and
  it records an already-ratified design of record (the fix direction), not a new decision. PA should
  confirm the wording.

**Tests / evidence**
```
$ uv run pytest tests/unit -q
1331 passed in 83.36s            # was 1323; +8 new (0 warnings under filterwarnings=error)

$ uv run pytest tests/integration/test_docker_executor.py -q
10 passed                        # was 9; +1 real-container symlink-escape leg

$ uv run pytest tests/integration/test_sandbox_capstone.py tests/integration/test_sandbox_teardown.py tests/integration/test_credential_proxy.py -q
22 passed                        # file tools + bash in real containers, teardown, proxy — all green

$ uv lock --check
Resolved 149 packages            # no deps added

# §9 laziness: importing decode.cli + decode.tools.files pulls in NO decode.sandbox/kitaru/modal module.
# none-mode byte-identity: the seam yields None (test_none_mode_never_engages_the_backend_seam), _bridge
#   runs only on the sandbox branch.
```

**Acceptance criteria re-checked**
- [x] Sandbox read/write/edit route on logical paths and **land inside the workspace**; containment
  rejects escapes — now layered (string-math `..`/absolute above the seam + physical symlink containment
  in the docker backend). Security FAIL 1 closed.
- [x] `make ci` green for the task scope (unit 1331/0 warnings, scoped ruff clean, `uv lock --check` OK,
  touched docker integration green). The sole repo-wide `ruff check` red remains the pre-existing,
  untracked, out-of-scope `src/decode/tools/substack_summarizer.py` (never touched).

**Notes**
- **Never-crash contract now uniform** with `bash` (executor renders exit-125; file tools render
  `ModelRetry`). Root-cause fix (one guard in each shared function every caller routes through), not
  per-caller patches.
- **Pre-existing docker leak flagged (NOT mine):** `docker ps` shows one keeper container
  `54b2df0f100f`/`competent_clarke` (`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, `sleep infinity`)
  created 03:46 — before this session. My integration tests self-reap (`docker ps -a` shows only it after
  my runs). Likely leaked by the Tester's manual symlink reproduction; the Tester's cost-hygiene check
  filtered `ancestor=python:3.12-slim` (the wrong image name) so it went unnoticed. Left running to avoid
  killing a possibly-live manual session — a human can `docker rm -f competent_clarke` if stale.

### [Tester] 2026-07-04 05:20 — QA re-verify (FAIL 1 + FAIL 2 fixes)

Targeted re-verify of the two defects from the 04:30 FAIL. Everything else in 081 already PASSED and
was not re-derived. Cost-hygiene note: reaped the pre-existing stale `competent_clarke`
(`54b2df0f100f`) at the start for a clean baseline (no live decode session held it; matches the exact
leak the SWE flagged), and self-reaped every container/sandbox I spun up.

**Test summary**
- Format / lint: PASS — `ruff format --check` + `ruff check` clean on all 081-touched files; repo-wide
  `ruff check src/` red **only** on the untracked, out-of-scope `substack_summarizer.py` (known noise).
- Unit tests: 1331 passed / 0 failed. `uv lock --check` OK (149 pkgs).
- Integration (REAL infra): docker executor 10/10 (incl. the new symlink-escape leg); modal 43 unit +
  1 real remote file-op leg (`test_file_ops_read_write_directly_against_the_remote`).
- Warnings: 0 (`filterwarnings=["error"]`; 1331 passed ⇒ 0 warnings).

**E2E adversarial pass (re-verify only)**
- FAIL 1 fix — real-docker symlink escape (seam engaged, container `f404bbd95953`): planted
  `evil_abs → /etc/passwd`, `escape → ../../..`, `canary_link → <host file>` via sandboxed `bash`, then
  drove the host-side tools through them. `read evil_abs`, `read escape`, `write canary_link`,
  `write escape/child`, `edit canary_link`, `grep --path evil_abs` → **ALL refused** with
  `ModelRetry: Sandbox file operation failed: … escapes the workspace sandbox`. Host `/etc/passwd` (macOS
  first line `##`) no longer leaks; host canary byte-unchanged (`HOST-CANARY-ORIGINAL`); no host file
  created outside the workspace. Benign in-workspace `inside_link → real.txt` still read fine (no false
  positive). Container self-reaped (`_container_id is None` after `aclose`). (PASS)
- FAIL 2 fix — backend create failure (never-crash): Phase A patched `DockerBackend.create` to raise
  `RuntimeError`, `SANDBOX_MODE=docker` — `read`/`write`/`edit` (driven through the REAL `_active_backend`
  guard) → `ModelRetry("The sandbox is unavailable (docker run failed …)")`, **no traceback**, and **no
  host fall-through** (`fallthrough.txt` never written under the launch cwd); `bash` rendered a clean
  `Exit code: 125.`. Phase B (real malformed `SANDBOX_IMAGE`, unpatched): `bash` → `Exit code: 125.`
  (`docker: invalid reference format`), no crash. (PASS)
- Mutation test — the new regressions genuinely guard (source byte-restored from backup after each; both
  files md5-identical to baseline afterward):
  - Neuter `DockerBackend._path` (bare join, drop resolve+raise) → RED: `test_docker_backend.py::
    test_file_ops_refuse_a_symlink_that_escapes_the_workspace` (1, hermetic) **and** the integration
    `test_docker_executor.py::test_a_bash_planted_symlink_escape_is_refused_not_followed_to_the_host`
    (1, real docker). Restore → green. *Mapping correction:* the **hermetic** files escape-render test
    stayed green under this mutation (it drives a fake `_EscapingBackend`, not `DockerBackend`) — the
    files-level escape rendering is guarded by Mutation 2 below; Mutation 1 is guarded at the files layer
    by the integration symlink leg.
  - Neuter `_bridge` + `_active_backend` wrapping (raw exception through) → RED: 6 —
    `test_file_tools_render_a_backend_create_failure_as_model_retry` ×3 (RuntimeError leaked) +
    `test_file_tools_render_a_workspace_escape_as_model_retry` ×3 (WorkspaceEscape leaked). Restore →
    green. (PASS)

**Acceptance criteria re-checked (the two previously failed — now verified PASS)**
- [x] PASS — Sandbox `read`/`write`/`edit` route on logical paths and **land inside the workspace**;
  containment rejects escapes. Layered containment holds: `_resolve_logical` rejects `..`/absolute above
  the seam (both backends); `DockerBackend._path` `.resolve()`s + raises `WorkspaceEscape` below the seam
  so a mount-shared symlink can't be followed onto the host. Evidence: real-docker repro (6 escapes
  refused, host bytes unchanged) + `test_docker_backend.py` symlink unit tests + integration symlink leg
  + Mutation 1/2 red→green. The prior host `/etc/passwd` read/write escape is closed.
- [x] PASS — `make ci` green / 0 warnings / `uv lock --check`. For the task scope: unit 1331/0 warnings,
  081-touched ruff clean, `uv lock --check` OK, touched docker+modal integration green. The sole
  repo-wide `ruff` red is the pre-existing untracked `substack_summarizer.py` (out of scope).

**Evidence**
```
$ uv run pytest tests/unit -q
1331 passed in 82.42s
$ uv run pytest tests/integration/test_docker_executor.py -q
10 passed in 4.75s
# FAIL 1 real-docker repro:
[read evil_abs] REFUSED ModelRetry: Sandbox file operation failed: path 'evil_abs' escapes the workspace sandbox
[host canary] byte-unchanged: 'HOST-CANARY-ORIGINAL'   [host outside marker] not created (correct)
[read inside_link] OK (benign symlink allowed): '1\tINSIDE'
# FAIL 2 repro:
[read/write/edit] ModelRetry: The sandbox is unavailable (docker run failed (exit 125): No such image: bogus:latest)
[no fall-through] fallthrough.txt does not exist (correct)   [bash] 'Exit code: 125.'
# Mutation 1 → 1 unit + 1 integration RED;  Mutation 2 → 6 files tests RED;  both restored → green.
# Cost hygiene: docker ps (ancestor uv:python3.12-bookworm-slim) = 0;  modal Sandbox.list() = 0.
```

**Other issues found**
- Modal re-confirmed untouched by 081 (not in the diff) and host-safe by construction (file ops are
  remote-only via `_remote()` + `SandboxFilesystem`, no host pathlib, no containment layer needed) — 43
  unit + 1 real remote file-op leg green.
- `import decode.cli` pulls in no `decode.sandbox` / `kitaru` / `modal` module (§9 laziness intact).
- ADR-0012 §4 wording (SWE edit) is accurate — correctly describes the layered containment + modal
  needing no layer. Not a blocker; PA can ratify.

**VERDICT: PASS** — both FAILs fixed and independently re-verified against real docker; the new
regression tests genuinely guard (mutation-proven); full suite + touched integration green, 0 warnings;
modal unchanged + host-safe; no leaks. Ready for PA acceptance review.
