---
id: 085-sandbox-isolated-workspace-capstone
feature: isolated-workspace
status: done
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

- [x] The offline slice passes with no infra/key and proves: the one-seam fresh-exec contract, file
  tools through the seam (+ `none` byte-identical), selection swap, harness-home split, unified
  description, offline clone + none-mode guard, LSP posture, gated web_fetch, the git hand-back
  (branch-local-on-push-fail / dirty captured / unchanged ships nothing / none-mode no-op + `/ship`
  line / creds-not-in-sandbox), credential map, and replay-safety config. — 39 offline tests pass.
- [x] The real-infra smokes SKIP cleanly when absent and PASS when present, including the real
  docker/modal ship round-trips to a local origin. — docker round-trip + ship PASS; modal round-trip
  (bootstrap + direct ops + export) + revival PASS; the modal ship-over-clone round-trip now PASSES
  (`test_modal_export_over_a_clone_round_trips_git`, un-`xfail`ed). The flagged product gap
  (`extract_tar` could not overwrite a clone's read-only `.git` objects) was **fixed** — see the
  2026-07-04 SWE-log FIX note (real-modal `--repo` export + git hand-back verified end to end).
- [x] Hermetic under `filterwarnings=["error"]` run alone (deterministic disposal; no leaked
  subprocess/async/container/sandbox/network). — file alone: 42 passed, 1 xfailed, 0 warnings; 0 leaked
  docker containers/networks + 0 modal sandboxes after.
- [x] `make ci` green, 0 warnings, infra-less (real smokes skipped); `uv lock --check` passes. —
  `make ci` NOT run (the untouchable WIP `substack_summarizer.py` is red under ruff — standing
  constraint); instead `ruff format`/`ruff check` on the capstone pass, `uv lock --check` passes, and
  the capstone is green infra-less (real smokes skip; offline slice + hermetic xfail only).
- [x] The module docstring documents the feature end to end (doubles as documentation), naming real vs
  faked boundaries. — the module docstring maps the REAL vs FAKED boundaries + the one pinned gap.

## Out of scope

- New product code (all in 078–083). A deployed-stack proxy test / a real *remote* push (local-origin
  push only, offline-provable).

## Log

### [SWE] 2026-07-04 09:20 — Implementation

**Files modified**
- `tests/integration/test_sandbox_capstone.py` — full rewrite: replaced the ADR-0011-era capstone with
  the ADR-0012 living proof (always-run offline slice + skipif-guarded real-infra smokes). No product
  code touched.
- `tasks/085-sandbox-isolated-workspace-capstone.md` — status → in-progress; acceptance boxes; this log.

**Tests** (run with `uv run pytest` directly — NOT `make ci`/`make pre-commit`, red on the untouchable
WIP `substack_summarizer.py`)
- Capstone alone: `42 passed, 1 xfailed, 0 warnings` in 38.7s (hermetic; 0 leaked containers/networks
  /modal sandboxes after).
- Offline slice only (no docker/modal/key path): `38 passed, 4 deselected, 1 xfailed`.
- Real docker smokes: `2 passed` (isolated-workspace round-trip + ship; credential-proxy boundary); 0 leaks.
- Real modal smokes: `2 passed` (isolated-workspace round-trip + lifecycle; max-lifetime revival); 0 leaks.
- Full unit suite: `1394 passed, 0 warnings`.
- Full integration suite (real docker+modal live): `92 passed, 1 xfailed, 0 warnings` — no regressions.
- `uv lock --check` passes; `import decode.cli` is kitaru-free.

**Acceptance criteria**
- [x] Offline slice proves the whole feature (one-seam fresh-exec, file tools through the seam + `none`
  byte-identical, selection swap, harness-home split, unified description, offline clone + none-mode
  guard, LSP posture, gated web_fetch, git hand-back, credential map, replay-safety).
- [ ] Real smokes SKIP-when-absent / PASS-when-present incl ship round-trips — docker ship PASSES; **the
  modal ship-over-clone round-trip is BLOCKED by a product gap** (see BLOCKER).
- [x] Hermetic under `filterwarnings=["error"]` run alone; deterministic disposal, no leaks.
- [x] Green infra-less + `uv lock --check` (`make ci` not runnable — WIP red; equivalents pass).
- [x] Module docstring documents the feature end to end, naming real vs faked boundaries.

**Evidence**
```
$ uv run pytest tests/integration/test_sandbox_capstone.py -p no:randomly
collected 43 items
............................. ...........x..                            [100%]
XFAIL ...test_modal_export_over_a_clone_round_trips_git - PRODUCT GAP (flagged, not fixed)
42 passed, 1 xfailed in 38.72s
$ docker ps -aq --filter ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim | wc -l  → 0
$ python -c "import modal; print(len(list(modal.Sandbox.list())))"                        → 0
$ uv run pytest tests/unit -q       → 1394 passed in 88.45s
$ uv run pytest tests/integration   → 92 passed, 1 xfailed in 191.32s
```

**BLOCKER — product gap surfaced by the capstone (NO product code changed, per the task's HARD RULE)**
The modal **ship-over-clone hand-back** cannot be proven passing: `decode.sandbox.workspace.extract_tar`
(the sweep `ModalBackend.export` uses) raises `PermissionError` overwriting a `git clone`'s **read-only
`.git` loose objects** (mode 0444) and `extractall` aborts — so a modal session's export-over-a-clone
sweeps *nothing*, and the modal git hand-back to a `--repo` origin captures no work. Docker is unaffected
(its bind mount is live — no extract). The exhaustive modal matrix missed this (it exports a non-git
`marker.txt` workspace); the capstone is the first to export over a real clone. Reproduced hermetically
(`extract_tar(tar_dir(clone), clone)` → `PermissionError` on `.git/objects/**`). I did **not** fix it
(test-authoring task, no product code). It is pinned by `test_modal_export_over_a_clone_round_trips_git`
with `xfail(strict=True)` (flips to a failure the moment `extract_tar` is fixed, forcing the pin's
removal). Suggested product fix (a follow-up task): make `extract_tar` unlink/chmod a read-only target
before writing, or skip identical content-addressed objects. Needs PA to file the fix task; the AC-2
modal-ship half stays open until then.

**Notes**
- Doubles kept in sync with the real seams: recording/local-exec `SandboxBackend` doubles mirror
  `tests/unit/decode/tools/test_files_sandbox.py`; hermetic-git helpers mirror `test_handback.py`; the
  scripted-`FunctionModel` gated-loop driver mirrors the M1 capstone.
- Docstring boundary map: REAL = the run seam + `PermissionGate` + `build_agent` registry, the real
  `SandboxExecutor` over a fake backend, mode→executor selection, the per-mode `bash` description, file
  containment path-math + `glob`/`grep` parity, host-side git hand-back, `build_credential_map`, and the
  replay-safety wiring; FAKED = the model (`FunctionModel`), the `SandboxBackend` doubles, `kitaru.get_secret`,
  the LSP call (Modal-off posture), and the spied `KitaruAgent` build. Part 2 real smokes named.
- `make ci` deliberately not run (the untouchable WIP `src/decode/tools/substack_summarizer.py` is red
  under ruff); `ruff format` + `ruff check` on the capstone pass, and `uv lock --check` passes.

### [SWE] 2026-07-04 09:03 — FIX: extract_tar overwrites a clone's read-only .git (unblocks modal ship-over-clone)

Fixes the real product bug the capstone surfaced (the strict-`xfail` above). Product code change,
authorized as a follow-up because the gap broke a core feature promise: **modal + `--repo` + git
hand-back captured no work.**

**Root cause**
`decode.sandbox.workspace.extract_tar` (the sweep `ModalBackend.export` uses) called
`tarfile.extractall(..., filter="data")` straight onto the host `.decode/sandbox`. For a `--repo`
session that destination is a real `git clone` whose `.git` loose objects git writes **read-only**
(mode 0444). `extractall` opens each target in `"wb"`, so the first read-only object raised
`PermissionError` and aborted the whole extract — a modal `--repo` session swept **nothing** and the
hand-back had no work to ship. Docker is unaffected (live bind mount, no extract). The 080 modal matrix
missed it (it only exported a non-git `marker.txt`); the capstone is the first export over a real clone.

**Fix (minimal; traversal guard preserved)**
`src/decode/sandbox/workspace.py` — before extracting, `extract_tar` now calls a new
`_make_tree_writable(directory)` that walks the existing destination top-down and OR-s owner-write into
every path (owner-`rwx` on dirs so the walk can descend/rewrite; owner-`w` on files; best-effort per
path via `contextlib.suppress(OSError)`). The existing `tarfile.extractall(directory, filter="data")`
call is **unchanged**, so the 078 `data`-filter path-traversal containment (no absolute paths, no `..`,
no absolute/escaping links) is byte-for-byte intact — I did NOT switch to per-member extraction.
Overwriting a content-addressed git object with identical bytes is safe, the `data` filter re-normalizes
each written member's mode (0444→0644), and git re-derives object modes, so the swept `.git` stays a
valid repo. Diff is one helper + one call + `import stat`/`contextlib`; no new dependency.

**Files modified**
- `src/decode/sandbox/workspace.py` — `_make_tree_writable` + `_add_owner_write`; `extract_tar` chmods
  the dest writable before `extractall(filter="data")`; docstrings.
- `tests/unit/decode/sandbox/test_workspace.py` — 2 regression tests (below).
- `tests/integration/test_sandbox_capstone.py` — flipped `test_modal_export_over_a_clone_round_trips_git`
  from `xfail(strict=True)` to a real test (agent work **and** a valid `.git` — `status`/`log`/`rev-parse`
  — survive the sweep); refreshed the module + fixture/test docstrings that referenced the (now-fixed) gap.

**Tests** (run with `uv run pytest` directly — `make ci` red on the untouchable `substack_summarizer.py`)
- New unit regressions: `test_extract_tar_overwrites_a_read_only_clone_tree` (red→green: was
  `PermissionError` on the 0444 object, now overwrites + bytes intact) and
  `test_extract_tar_keeps_the_data_filter_traversal_guard` (proves the `data`-filter containment survived:
  `..` rejected + no parent write, absolute neutralized-not-leaked, absolute symlink rejected).
- `tests/unit/decode/sandbox/test_workspace.py`: 21 passed (alone, `filterwarnings=["error"]`, 0 warnings).
- Full unit suite: 1396 passed, 0 warnings. Full integration suite (real docker+modal live): 93 passed,
  0 warnings (was 92 passed + 1 xfailed — the flip removed the xfail).
- Capstone alone: 43 passed, 0 xfailed — incl. real docker round-trip+ship, real modal round-trip, real
  modal revival, and the un-xfailed modal-export-over-clone; 0 leaked docker containers / modal sandboxes.
- `uv lock --check` passes; `import decode.cli` kitaru-free.

**Evidence — real-modal `--repo` end-to-end (the promise the bug broke)**
```
1. cloned workspace ... — 3 read-only .git object(s) present
2. real modal sandbox started + clone bootstrap-uploaded
3. agent work in remote /workspace: 'shipped by the agent'
4. export swept work back to host: exists=True content='shipped by the agent'   # the former bug site
5. hand-back: branch=decode/6412dd68 pushed=True
6. origin has branch decode/6412dd68: files=['README.md', 'agent_report.txt'] agent_report.txt='shipped by the agent'
E2E OK: modal --repo export-over-clone + git hand-back captured the work and shipped it.
   sandbox reaped: _sandbox is None -> True
```
Cost hygiene after every run: `docker ps --filter ancestor=…uv:python3.12-bookworm-slim` → 0;
`modal.Sandbox.list()` → 0. Foreign containers left alone.

**Notes**
- Not committed — Tester goes next (per the run's instruction).
- Untouchable WIP (`substack_summarizer.py`, `docs/notes/`) not touched; ruff run scoped to the 3 changed
  files (format + check clean).

### [Tester] 2026-07-04 09:40 — QA

**Test summary** (run with `uv run pytest` directly + scoped ruff — `make ci`/`make pre-commit` red only
on the untouchable WIP `substack_summarizer.py`, per the standing constraint)
- Format / lint (scoped to the 3 changed code files): PASS — `ruff format --check` "3 files already
  formatted"; `ruff check` "All checks passed!".
- Unit tests: 1396 passed / 0 failed (`uv run pytest tests/unit`, 88.6s).
- Integration tests: 93 passed / 0 failed (`uv run pytest tests/integration`, real docker+modal, 190.4s).
- Warnings: 0 (suite runs under `filterwarnings=["error"]`).
- `uv lock --check` PASS; `import decode.cli` kitaru-free PASS.

**E2E adversarial pass** (docker + modal BOTH available on this host — every real smoke ran for real)
- Mutation-check (traversal guard is real, not vacuous): neutered `filter="data"` → `filter="fully_trusted"`
  in `extract_tar` → `test_extract_tar_keeps_the_data_filter_traversal_guard` went RED ("DID NOT RAISE
  FilterError"); restored (blob `132aa4b`, byte-identical). Fix keeps `extractall(..., filter="data")` —
  NO switch to per-member extraction. PASS.
- Break path 1 (containment: symlink escaping the workspace) — **FAIL** (see AC / issue below). Hermetic
  repro: a destination clone containing `evil_file -> ../outside/canary.txt` (0o444) and
  `evil_dir -> ../outside/canary_dir` (0o500); `extract_tar(tar_dir(benign), workspace)` →
  canary FILE 0o444→**0o644**, canary DIR 0o500→**0o700**. `_make_tree_writable`'s chmod followed the
  symlinks OUT of the workspace and mutated host targets' modes.
- Break path 2 (real modal `--repo` export-over-clone) — PASS. `test_modal_export_over_a_clone_round_trips_git`
  (un-xfailed) PASS; the swept `.git` is a valid repo (rev-parse/log/status intact) and the agent's
  uncommitted work survives. The read-only-`.git` bug the capstone surfaced is genuinely fixed.
- Break path 3 (real docker roundtrip + hand-back + credential proxy) — PASS.
  `test_real_docker_isolated_workspace_roundtrip_and_handback` (bind mount, fresh-exec, timeout survives,
  ship pushes `decode/<id>`) + `test_real_docker_credential_proxy_boundary` (injected header ARRIVED at the
  upstream; worker `env` scan holds NO secret, only `http_proxy=`).
- Break path 4 (real modal roundtrip + max-lifetime revival) — PASS
  (`test_real_modal_isolated_workspace_roundtrip` + `test_real_modal_revival_re_bootstraps_from_host_state`).
- Break path 5 (infra-absent → skipif gates, offline slice green with no key) — PASS. Re-ran the capstone
  under a failing `docker` shim + empty `HOME` + empty `MODAL_TOKEN_*`: **39 passed, 4 skipped** (docker×2,
  modal×2 SKIP with correct reasons — never fail); Gemini key faked by the autouse fixture.
- Hermetic / no-leak — PASS. Capstone alone: 43 passed, 0 warnings, 39.9s. Full suite green. After both
  runs: 0 leaked `uv:python3.12-bookworm-slim` containers, 0 `decode` networks, 0 `decode-cap-upstream`
  containers, 0 modal sandboxes (correct filter). Baseline was 0/0/0 — self-reaped only what the tests
  created; foreign containers untouched.

**Acceptance criteria** (all 5 capstone-deliverable ACs verified true; the verdict FAIL is a defect in the
folded-in product fix, which this task explicitly scoped into QA)
- [x] PASS — Offline slice proves the whole feature — 39 offline tests pass (one-seam fresh-exec, file
  tools through the seam + `none` byte-identical, selection swap, harness-home split, unified description,
  offline clone + none-mode guard, LSP posture, gated web_fetch, git hand-back, credential map, replay-safety).
- [x] PASS — Real smokes SKIP-when-absent / PASS-when-present incl ship round-trips + modal-over-clone —
  verified for real (docker+modal live) AND SKIP-clean under simulated absence.
- [x] PASS — Hermetic under `filterwarnings=["error"]` run alone; 0 leaked containers/networks/sandboxes.
- [x] PASS — Green infra-less + `uv lock --check`; scoped ruff clean (make ci constraint noted).
- [x] PASS — Module docstring documents the feature end to end; REAL vs FAKED boundary map spot-checked
  accurate against the tests; bug note accurate.
- [ ] FAIL — **Product fix `extract_tar`/`_make_tree_writable` containment (folded-in scope).**
      Expected: `_make_tree_writable` chmods only entries WITHIN the workspace tree; a symlink in the
      existing destination (a `--repo` clone's working-tree symlink) that escapes the workspace is NOT
      followed, so no out-of-tree host file/dir mode is changed.
      Actual: `os.walk` (followlinks=False) still yields the symlink NAME at its level; `_add_owner_write`
      calls `Path.chmod(path.stat().st_mode | bits)`, and both `stat`/`chmod` follow symlinks on
      macOS/Linux (no `lchmod`). Repro (above): outside FILE 0o444→0o644, outside DIR 0o500→0o700.
      Reachable in production: `modal` mode, `ModalBackend.export()` → `extract_tar(data, self._workspace)`
      over the host `.decode/sandbox` clone; fires automatically on `aclose`/exit and on `/ship`. The
      sandbox threat model is explicitly "clone + run untrusted code," so an untrusted `--repo` whose
      working tree holds `evil -> /abs/host/path` is in scope. Impact is a permission-integrity breach
      (OR-s owner-write into an arbitrary host path a symlink names) — it does not read/delete/overwrite
      target CONTENTS (the `data` filter still guards the incoming tar; `_make_tree_writable` only chmods)
      — so "minor," but a real NEW containment gap the fix introduced (pre-fix `extract_tar` never walked
      the destination), and the fix's own regression test only covers the incoming-tar path, not this new
      destination-walk surface.
      Fix: skip symlinks in the walk, e.g. in `_add_owner_write`:
      `if path.is_symlink(): return` before the `chmod` (or filter symlink names in `_make_tree_writable`).
      Safe: git loose objects are regular files (the read-only-`.git` fix still works); `os.walk` already
      never recurses INTO symlinked dirs. Add a regression test: an escaping symlink in the destination
      tree → the outside target's mode is UNCHANGED after `extract_tar`.

**Evidence**
```
# mutation-check (guard is real)
$ (neuter filter="data" → "fully_trusted") uv run pytest ...test_extract_tar_keeps_the_data_filter_traversal_guard
E   Failed: DID NOT RAISE FilterError    → 1 failed   (restored after; blob 132aa4b)

# symlink containment probe (the FAIL)
canary FILE mode: before=0o444 after=0o644
canary DIR  mode: before=0o500  after=0o700
RESULT: CONTAINMENT GAP — _make_tree_writable followed a symlink out of the workspace

$ uv run pytest tests/integration/test_sandbox_capstone.py -p no:randomly   → 43 passed in 39.92s (0 warnings)
$ uv run pytest tests/unit                                                  → 1396 passed in 88.61s
$ uv run pytest tests/integration                                          → 93 passed in 190.40s
# infra-absent simulation (failing docker shim + empty HOME + empty MODAL_TOKEN_*)
$ .venv/bin/python3 -m pytest tests/integration/test_sandbox_capstone.py -rs → 39 passed, 4 skipped
# leaks after (correct filter): docker=0  decode-net=0  decode-cap-upstream=0  modal=0
$ uv lock --check → OK ;  import decode.cli → NO kitaru
```

**Other issues found**
- (nit, non-blocking) AC-1 body text still says "38 offline tests" — after the xfail→real flip the offline
  count is 39. Stale wording only; the 2026-07-04 FIX log correctly states 43 passed / 0 xfailed.

**VERDICT: FAIL** — 1 issue: the folded-in product fix (`_make_tree_writable`) follows symlinks out of the
workspace during the modal export sweep, a NEW containment gap. All else (capstone offline slice, real
docker+modal smokes incl. the modal export-over-clone, hermeticity/no-leak, suites, docstring) is green.
Skip symlinks in the walk + add the regression test, then re-QA.

### [SWE] 2026-07-04 10:15 — FIX: skip symlinks in the writable sweep (close the destination-walk gap)

Addresses the single blocking QA FAIL: `_make_tree_writable`'s pre-extract chmod followed a symlink OUT
of the Workspace and mutated the out-of-tree host target's mode.

**Root cause**
`_add_owner_write` did `path.chmod(path.stat().st_mode | bits)`. `Path.chmod`/`Path.stat` FOLLOW
symlinks (no `lchmod` on macOS/Linux), and `os.walk(followlinks=False)` still yields the symlink NAME at
its level (it only blocks *recursion*). So an escaping symlink in the existing destination clone
(`evil_file -> ../outside/canary.txt` 0o444, `evil_dir -> ../outside/canary_dir` 0o500) got its
out-of-tree target chmod'd (→0o644 / →0o700). Reachable in prod: `modal` `ModalBackend.export()` →
`extract_tar(data, self._workspace)` over the host `.decode/sandbox` clone (fires on `aclose`/exit and
`/ship`); threat model is "clone + run untrusted code" and git stores arbitrary symlink targets.

**Fix (one chokepoint, minimal)**
`src/decode/sandbox/workspace.py` — `_add_owner_write` now skips symlinks before the chmod:
```python
with contextlib.suppress(OSError):
    if path.is_symlink():
        return
    path.chmod(path.stat().st_mode | bits)
```
Both `dirnames` and `filenames` route through this one helper, so the guard covers the whole walk. The
check rides *inside* the existing `OSError` suppression so a rare `lstat` failure stays best-effort (left
for `extractall` to surface), not a hard abort. `tar.extractall(..., filter="data")` is byte-for-byte
UNCHANGED — the incoming-tar containment is untouched. Rationale it stays correct: git loose objects are
REGULAR files → still made writable → the read-only-`.git` overwrite fix still works; `os.walk` already
never recurses INTO a symlinked dir, so skipping the symlink ENTRY closes the surface fully; a symlink
never needs owner-write for the extract (the `data` filter neutralizes incoming symlink members).
Docstrings on `_add_owner_write` + `_make_tree_writable` updated to state the symlink-skip.

**Files modified**
- `src/decode/sandbox/workspace.py` — `_add_owner_write` skips symlinks; docstrings.
- `tests/unit/decode/sandbox/test_workspace.py` — new regression test + `import stat`.
- `tasks/085-…md` — AC-1 wording "38 offline tests" → "39" (the non-blocking nit); this log.

**Regression test** (the gap the existing test missed — it only covered the INCOMING-tar path)
`test_extract_tar_does_not_chmod_a_symlink_target_escaping_the_destination` — a destination clone with an
in-tree read-only regular file (0o444) + `evil_file`/`evil_dir` symlinks ESCAPING it (outside file 0o444,
outside dir 0o500); `extract_tar` a benign tar over it; asserts the OUTSIDE targets' modes are UNCHANGED,
the in-tree read-only file WAS made owner-writable, and the extract still landed. Confirmed red→green:
before the fix the outside file went 0o444→0o644 (`assert 420 == 292` failed); after, unchanged.

**Tests** (run with `uv run pytest` directly — `make ci`/`make pre-commit` red only on the untouchable WIP
`substack_summarizer.py`, per the standing constraint; scoped `ruff` on the 2 changed code files)
- New test + the two it must not regress: `test_extract_tar_does_not_chmod_a_symlink_target_escaping_the_destination`,
  `test_extract_tar_overwrites_a_read_only_clone_tree`, `test_extract_tar_keeps_the_data_filter_traversal_guard` — 3 passed.
- `tests/unit/decode/sandbox/test_workspace.py`: 22 passed (was 21 + the new one).
- Full unit suite: 1397 passed, 0 warnings. Full integration (real docker+modal live): 93 passed, 0 warnings.
- Capstone alone under `filterwarnings=["error"]`: 43 passed, 0 skipped, 0 xfailed, 0 warnings — every real
  leg ran for REAL (none skipped), incl. `test_modal_export_over_a_clone_round_trips_git` (the read-only-`.git`
  fix survives the symlink-skip), `test_real_modal_isolated_workspace_roundtrip`, the modal revival, the docker
  roundtrip+handback, and the docker credential-proxy boundary.
- `uv lock --check` passes; `import decode.cli` kitaru-free; scoped `ruff format`/`ruff check` clean.
- Cost hygiene after: docker containers (that ancestor) = 0; decode networks = 0; `modal.Sandbox.list()` = 0.
  Foreign containers left alone.

**Evidence**
```
# red before the fix (right reason — outside file mode changed, not an import/typo error):
$ uv run pytest ...::test_extract_tar_does_not_chmod_a_symlink_target_escaping_the_destination
E   AssertionError: assert 420 == 292   (0o644 != 0o444 — symlink followed out of tree)   → 1 failed

# green after the fix:
$ uv run pytest ...::{new} ...::overwrites_a_read_only_clone_tree ...::keeps_the_data_filter_traversal_guard
3 passed
$ uv run pytest tests/unit -q                                    → 1397 passed, 0 warnings
$ uv run pytest tests/integration -q                             → 93 passed, 0 warnings
$ uv run pytest tests/integration/test_sandbox_capstone.py -rs   → 43 passed (0 skipped/xfailed), 0 warnings
$ uv lock --check → OK ;  import decode.cli → kitaru imported: False
$ docker ps --filter ancestor=…uv:python3.12-bookworm-slim | wc -l → 0 ;  modal.Sandbox.list() → 0
```

**Notes**
- Not committed — handing back to the Tester for re-verification per the run's instruction.
- Untouchable WIP (`src/decode/tools/substack_summarizer.py`, `docs/notes/`) not touched; no `git stash`.

### [Tester] 2026-07-04 10:45 — QA re-verify (targeted: symlink-skip containment fix)

Targeted re-verify of the single delta since the last QA FAIL: the symlink-skip in `_add_owner_write`
(`if path.is_symlink(): return` inside `contextlib.suppress(OSError)`) + its regression test + the
AC-1 "38"→"39" nit. The prior PASS scope (all ACs, real docker+modal smokes, offline slice,
hermeticity, read-only-`.git` fix) was NOT re-derived — I confirmed the gap is closed, the `.git` fix
survived, and nothing regressed.

**Test summary** (`uv run pytest` directly + scoped ruff — `make ci` red only on the untouchable WIP)
- Scoped ruff (workspace.py + the 2 test files): PASS — format "3 files already formatted"; check "All checks passed!".
- Unit + integration together: 1490 passed / 0 failed (real docker+modal live, 285.24s).
- Capstone alone: 43 passed, 0 skipped, 0 xfailed (37.71s).
- Warnings: 0 (`filterwarnings=["error"]`, pyproject:108).
- `uv lock --check` PASS (149 pkgs, no drift); `import decode.cli` kitaru-free PASS.

**E2E adversarial pass** (docker daemon UP + modal creds PRESENT — every real leg ran for REAL)
- Break path 1 from last round (symlink escaping the destination) — now CLOSED. Independent escape
  probe against the real `extract_tar`/`tar_dir`: a destination clone with `evil_file ->
  ../outside/canary.txt` (0o444) and `evil_dir -> ../outside/canary_dir` (0o500) → after `extract_tar`
  the outside FILE stays 0o444 and the outside DIR stays 0o500 (UNCHANGED), while the in-tree read-only
  regular file WAS made owner-writable and the benign tar landed. PASS.
- Mutation-check (the new test genuinely guards, not vacuous): removed `if path.is_symlink(): return`
  → `test_extract_tar_does_not_chmod_a_symlink_target_escaping_the_destination` went RED for the right
  reason (`AssertionError: assert 420 == 292` — outside canary 0o444→0o644 as the chmod followed the
  symlink out of tree); the other two extract_tar tests stayed green (property isolated). Restored the
  guard byte-exact → 3 passed. PASS.
- Read-only-`.git` fix SURVIVED the symlink-skip — `test_extract_tar_overwrites_a_read_only_clone_tree`
  (unit) PASS + `test_modal_export_over_a_clone_round_trips_git` PASS: the sweep over a real clone's
  0444 loose objects lands and the swept `.git` is valid (rev-parse == cloned HEAD, log "initial
  commit", `status --porcelain` shows the agent's untracked work). Git loose objects are regular files,
  so the symlink-skip never touches them. PASS.
- Real docker legs — `test_real_docker_isolated_workspace_roundtrip_and_handback` +
  `test_real_docker_credential_proxy_boundary` ran for real, PASS.
- Real modal legs — `test_real_modal_isolated_workspace_roundtrip` +
  `test_real_modal_revival_re_bootstraps_from_host_state` ran for real, PASS.
- Hermetic / no-leak — capstone alone 43 passed 0 warnings; after the capstone AND after the full
  suite: docker (correct filter)=0, decode-*=0, decode networks=0, decode-cap-upstream=0, modal
  `Sandbox.list()`=0. Baseline was 0 across the board — self-reaped only what the tests created;
  foreign containers untouched. PASS.

**Acceptance criteria** (all 5 verified `[x]`; the prior blocking FAIL — the folded-in
`_make_tree_writable` symlink containment — is now fixed and covered by a regression test)
- [x] PASS — Offline slice proves the whole feature — 39 offline tests (capstone alone: 43 = 39 offline + 4 real legs).
- [x] PASS — Real smokes SKIP-when-absent / PASS-when-present incl the modal export-over-clone — all ran green (infra live).
- [x] PASS — Hermetic under `filterwarnings=["error"]` run alone; 0 leaked containers/networks/sandboxes.
- [x] PASS — Green infra-less + `uv lock --check`; scoped ruff clean (make ci constraint noted).
- [x] PASS — Module docstring documents the feature end to end; the `_add_owner_write`/`_make_tree_writable`
  docstrings now correctly state the symlink-skip.

**Delta scope** — the ONLY tracked `src/` change in the whole uncommitted set is
`src/decode/sandbox/workspace.py` (the `extract_tar` fix); the capstone added no product code.
`tarfile.extractall(..., filter="data")` is byte-for-byte unchanged (incoming-tar containment intact).

**Evidence**
```
# mutation-check — guard removed → the new test RED for the right reason:
$ uv run pytest ...::test_extract_tar_does_not_chmod_a_symlink_target_escaping_the_destination
E   AssertionError: assert 420 == 292   (0o644 != 0o444 — symlink followed out of tree)  → 1 failed, 2 passed
# guard restored → green:
$ uv run pytest ...::{new} ...::overwrites_a_read_only_clone_tree ...::keeps_the_data_filter_traversal_guard → 3 passed
# independent escape probe (real helpers):
outside FILE mode: before=0o444 after=0o444  UNCHANGED
outside DIR  mode: before=0o500 after=0o500  UNCHANGED
RESULT: CONTAINED (gap CLOSED)
$ uv run pytest tests/integration/test_sandbox_capstone.py -rs → 43 passed (0 skipped/xfailed), 0 warnings
$ uv run pytest tests/unit tests/integration                  → 1490 passed, 0 warnings (285.24s)
$ uv lock --check → OK ;  import decode.cli → kitaru imported: False
# hygiene after everything (correct filter): docker=0 decode-*=0 networks=0 cap-upstream=0 modal=0
```

**Other issues found**
- None. The AC-1 "38 offline tests"→"39" wording nit is fixed (task line 59).

**VERDICT: PASS** — the blocking containment gap is closed (escape probe contained; the regression
test proven non-vacuous by mutation), the read-only-`.git` fix survived the symlink-skip (unit + the
export-over-clone git round-trip green), the full suite is green with 0 warnings and 0 leaks, the real
docker+modal legs ran for real, the only tracked src change is `workspace.py`, and the "39" nit is
fixed. Hand off to PA for acceptance review.

### [PA] 2026-07-04 11:40 — Acceptance Review (feature `isolated-workspace`, PR #25, tasks 078-085)

**VERDICT: ACCEPT**

Walked the whole feature from the user's perspective against the Tasks Plan ACs and the user's three
stated intents. Reviewed the shipped code (not just the Tester logs) at each user-facing surface. All
three promises are delivered end-to-end, proven against real docker + real modal by the capstone.

- **Fully isolated + EXACT SAME LOGIC across backends** — ONE `SandboxExecutor` (`sandbox/executor.py`)
  over a `SandboxBackend` seam; `select_executor` returns `SandboxExecutor(DockerBackend())` /
  `SandboxExecutor(ModalBackend())`; the old `docker_executor.py` (711 L) + `modal_executor.py` (289 L)
  are deleted. Both `exec` paths are fresh-exec (`run → backend.exec("bash","-lc",…)`, no persistent
  shell) and render the identical `ExecResult`/timeout contract. File tools route through the seam
  (`tools/files.py`): docker pathlib-on-mount, modal `SandboxFilesystem` + remote `find`/`grep`; `none`
  stays direct-pathlib byte-identical. The only per-backend difference is byte transport (mount vs
  bootstrap-upload/export) — the intentional same-logic-as-local transport the user accepted in the Q5
  grilling, documented in ADR-0012 §5 + README + AGENTS.md.
- **Clone any user-provided repo** — `--repo`/`--local` on `decode` and `decode run` (+ `SANDBOX_REPO`),
  `_resolve_sandbox_repo` precedence, `workspace.prepare_workspace` clones the USER's repo at committed
  HEAD into `/workspace` (verified in CLI `--help` + the real docker/modal clone round-trips: `read`/
  `bash` see the cloned files). `SANDBOX_MODE=none` + `--repo` → one friendly stderr line + exit 1 in
  BOTH the REPL and the headless pre-flight (`_sandbox_repo_config_error`), never a crash; a bad repo
  degrades to an empty Workspace + one friendly line.
- **Ship results back as a NEW BRANCH** — `sandbox/handback.py::ship_workspace` secures a deterministic
  `decode/<session-id>` branch (auto-commits dirty work, preserves the model's own commits) and
  `git push origin`s it (URL → remote, local path → local source), **host-side only** (boundary test +
  the real-docker "NO GIT IN SANDBOX" proof — no credential ever enters the sandbox). Never-lose-results
  is real: secure-before-push, so a failed/disabled push still leaves a named local branch with a
  recovery line. Fires on REPL exit (after `close_executor`'s modal export sweep), the idle-only `/ship`
  (discoverable in footer + completer; exports first mid-session), and headless `decode run --repo`
  completion; unchanged/non-git/no-repo → friendly skip (no spurious branch for a do-nothing session).

**User-facing copy** is clear, action-oriented, and consistent (deliberate `Decode - ` status vs
`Decode: ` error convention). The three AGENTS.md invariants (Sandbox / Harness-home / Hand-back) match
shipped behavior; README, `.env.example`, and `settings.py` comments are reconciled and accurate.
**Documentation discipline** honored: one feature ADR (0012), glossary terms added (Workspace, Harness
Home, Sandbox Backend, Hand-back), and ADR-0011's Status correctly records the partial supersession.

**Known non-blockers, judged — none breaks the user promise:** (1) `decode run --hitl --repo` auto-ship
is intentionally unwired, but its results still survive locally at `.decode/sandbox` (modal exported via
`close_executor`) and the 3 primary paths ship — an edge-of-edge operator path, documented honestly;
(2) `_branch_name` exotic-ref hardening is unreachable (hex session ids); (3) two `/ship` doc-placeholder
cosmetics. The capstone-surfaced read-only-`.git` modal-export bug was found AND fixed (c55c571) — a net
positive that makes the real-modal `--repo` hand-back work end to end.

All ACs across 078-085 verified from the user POV. User satisfaction guaranteed. Hand off to the PR Reviewer.

### [PR Reviewer] 2026-07-04 12:30 — Review

**VERDICT: NO BLOCKERS**

Reviewed the full diff for PR #25 (`feat/isolated-workspace`, tasks 078-085 + ADR-0012 + the
`extract_tar` fix c55c571) — every changed file (~11.8k insertions across src + tests + docs).

- Blockers: 0
- Nits: 3 (appended to the PR description)

Walked all six review dimensions (A performance, B clean-code, C tests, D standards, E doc-discipline,
F simplicity) plus the requested cross-cutting seams. Highlights verified:

- **Security — host-side-only git.** `handback._run_git` is the single choke point: a host `git -C`
  subprocess with ambient creds + `GIT_TERMINAL_PROMPT=0`, never `executor.run`/`backend.exec`; push
  stderr kept out of the user message (URL-cred leak) and only logged. Boundary test + real-docker
  "no git in sandbox" proof cover it.
- **Security — layered containment.** `files._resolve_logical` (string math, both backends, rejects
  `..`/absolute) + `docker_backend._path` (physical symlink resolution → `WorkspaceEscape`, an OSError
  rendered by `_bridge` without files.py importing it). Modal is remote-disposable (no host to escape).
  `extract_tar` symlink-skip (`_add_owner_write`) + `filter="data"` close the read-only-`.git` overwrite
  safely and fail-closed on escaping symlinks.
- **Correctness — harness-home vs deps.cwd split** threaded consistently (factory/skills read
  `harness_home`; file tools + bash read `deps.cwd`; session log / permission file / MEMORY.md / skills
  / SlashCompleter all anchored to Harness Home; lsp uses the Workspace path for docker, disabled for
  modal). No artifact leaks into the Workspace; no tool scope leaks to launch cwd.
- **Correctness — loop-bridge + teardown** loop-independent by construction (fresh-exec; dedicated
  short-lived loops in the headless flow that avoid orphaning `run_sync`'s loop); never-crash contract
  on the file tools (`_active_backend` + `_bridge`) renders ModelRetry on infra failure.
- **Replay-safety** `{"cache": False}` present on the bash checkpoint when `sandbox_mode != none`.
- **Doc discipline** complete: ADR-0012 Accepted, ADR-0011 supersession recorded, glossary carries
  every new concept (Workspace, Harness Home, Hand-back, Session Branch, Sandbox Backend), `.env.example`
  + settings reconciled.

The three previously-triaged non-blockers (HITL `--repo` auto-ship unwired; `_branch_name` exotic-ref;
`/ship` doc cosmetics) confirmed non-blocking. Pipeline may advance to hand-off.
