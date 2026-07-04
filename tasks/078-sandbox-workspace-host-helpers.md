---
id: 078-sandbox-workspace-host-helpers
feature: isolated-workspace
status: done
---

# Sandbox workspace host-helpers + SANDBOX_REPO setting (resolve / clone / bootstrap / skills-copy)

Tags: `sandbox`, `workspace`, `config`
Depends on: None
Blocks: #079, #080

The host-side foundation for the isolated workspace (ADR-0012). A new pure/sync module
`src/decode/sandbox/workspace.py` that resolves the workspace directory, prepares it (empty or a
`git clone` of a user-provided repo at its committed HEAD), copies the project's skills into it, and
provides a bootstrap-tar helper. Plus the `SANDBOX_REPO` setting. **Pure additions — nothing consumes
this module yet, so the whole suite stays green.**

## Scope

- **New `src/decode/sandbox/workspace.py`** (host-side, sync, no docker/modal imports):
  - `workspace_dir(harness_home: Path) -> Path` — the single resolver: `harness_home /
    settings.sandbox_workspace_dir` (resolved). The one place the workspace path is computed.
  - `prepare_workspace(harness_home, *, repo: str | None = None, local: bool = False) -> Path` —
    ensure the workspace dir exists; if it is **empty** and `repo` is given, host-side `git clone` the
    source (URL or local path) at its **committed HEAD** into it (`local=True` → `git clone --local`);
    `repo=None` → leave empty; return the path. A workspace that already holds content is **reused,
    never re-cloned** (docker's mount source / modal's bootstrap source across sessions). Uses the
    user's ambient git creds.
  - `seed_skills(workspace: Path) -> None` — copy the project's `.decode/skills` (the sibling of the
    workspace under `.decode/`, i.e. `workspace.parent / "skills"`) into `workspace/.decode/skills`,
    so cwd-relative skill-script paths resolve inside the workspace. No-op when absent. Replaces the
    docker ro-mount and the modal `add_local_dir` seeding.
  - `tar_dir(dir) -> bytes` / `extract_tar(data, dir)` — the backend-agnostic bootstrap-transfer
    helpers Modal's ONE-shot upload may use (080). (No mtime/delta/marker helpers — the per-call sync
    is retired per ADR-0012.)
- **New `sandbox_repo: str = ""` setting** in `config/settings.py`, mirrored as `SANDBOX_REPO=` in
  `.env.example` (that line only; full section prose is 084).
- **New unit tests** `tests/unit/decode/sandbox/test_workspace.py` (host-side, no infra): clone a tiny
  local git repo into the workspace (HEAD tree present, real `.git`); `repo=None` → empty; non-empty
  workspace reused (a marker file survives a second call); `seed_skills` copies / no-ops; `tar_dir` →
  `extract_tar` round-trips a nested tree faithfully; `--local` fast clone works.

## Acceptance criteria

- [x] `workspace_dir(home)` → `home/.decode/sandbox` (resolved); creation is idempotent.
- [x] `prepare_workspace(home, repo=<local repo>)` clones the committed HEAD tree into the empty
  workspace with a real `.git`; `repo=None` leaves it empty; `local=True` uses a local clone.
- [x] A non-empty workspace is reused, not re-cloned (a pre-placed marker file survives).
- [x] `seed_skills(workspace)` copies `<...>/.decode/skills/**` → `workspace/.decode/skills/**`; a
  missing source is a no-op.
- [x] `tar_dir`/`extract_tar` round-trip a nested tree byte-for-byte.
- [x] `SANDBOX_REPO` exists on `Settings` (default `""`) and is mirrored in `.env.example`.
- [x] Nothing outside its own tests imports `workspace.py`; `make ci` green, 0 warnings,
  `uv lock --check` passes.

## Out of scope

- Any executor/backend wiring (079/080), file-tool routing (081), CLI/clone-at-launch (082).
- **The retired mtime-delta sync** — no marker/delta/size-cap helpers exist (ADR-0012 rejects it for
  deletion-blindness).

## Log

### [SWE] 2026-07-03 20:10 — Implementation

**Files modified**
- `src/decode/sandbox/workspace.py` (NEW) — pure/sync host-side Workspace helpers: `workspace_dir`,
  `prepare_workspace` (`git clone` at committed HEAD into an empty workspace; reuse non-empty;
  ambient creds + `GIT_TERMINAL_PROMPT=0`; `--local`), `seed_skills`, `tar_dir`/`extract_tar`. No
  docker/modal import; reads `settings.sandbox_workspace_dir` only.
- `src/decode/config/settings.py` — added `sandbox_repo: str = ""` (in the Sandboxing block, after
  `sandbox_workspace_dir`); no reader yet (task 082 wires the CLI).
- `.env.example` — mirrored `SANDBOX_REPO=` (one commented line after `SANDBOX_WORKSPACE_DIR`; full
  section prose is 084).
- `tests/unit/decode/sandbox/test_workspace.py` (NEW) — 13 hermetic tests (real `git` against local
  repos under `tmp_path`; no daemon/remote/network).

**Tests**
- Unit: 13 new passing (full suite 1244 passing, 0 failing). Evidence below.
- Integration: 57 passing (byte-green; my change is a pure addition, imported by nothing yet).

**Acceptance criteria**
- [x] `workspace_dir(home)` → `home/.decode/sandbox` (resolved), idempotent creation —
  `test_workspace_dir_resolves_under_harness_home`, `test_workspace_dir_creation_is_idempotent`.
- [x] `prepare_workspace` clones committed HEAD (real `.git`) / `repo=None` empty / `local=True` —
  `test_prepare_workspace_clones_committed_head_into_empty_workspace`,
  `..._repo_none_leaves_the_workspace_empty`, `..._local_clone_works`,
  `..._passes_local_flag_and_disables_prompt`, `..._omits_local_flag_by_default`.
- [x] Non-empty workspace reused, not re-cloned — `test_prepare_workspace_reuses_a_non_empty_workspace`
  (pre-placed marker survives, no `.git`/clone). Plus `..._raises_on_clone_failure`.
- [x] `seed_skills` copies `.decode/skills/**` / no-op when absent —
  `test_seed_skills_copies_project_skills_into_the_workspace`, `..._is_a_noop_when_no_skills_dir`,
  `..._is_idempotent`.
- [x] `tar_dir`/`extract_tar` round-trip a nested tree byte-for-byte (incl. non-UTF-8 bytes) —
  `test_tar_dir_and_extract_tar_round_trip_a_nested_tree`.
- [x] `SANDBOX_REPO` on `Settings` (default `""`) + mirrored in `.env.example`.
- [x] Nothing outside its tests imports `workspace.py`; `uv lock --check` passes; 0 warnings.

**Evidence**
```
$ uv run pytest tests/unit/decode/sandbox/test_workspace.py -q
............. [100%]  13 passed in 0.60s

$ make unit-tests        # full suite
======================= 1244 passed in 83.71s =======================

$ make integration-tests
======================== 57 passed in 97.00s ========================

$ uv lock --check
Resolved 149 packages in 3ms      # no dep change

$ make format-check      # repo-wide
166 files already formatted

# end-to-end (real git): clone → reuse-preserves-model-work → seed_skills → --local → tar round-trip
prepare (clone)    -> cloned app.py present: True | real .git: True
prepare (reuse)    -> model work survived  : True
seed_skills        -> workspace/.decode/skills/linter/{SKILL.md,scripts/run.py} present: True
prepare (--local)  -> local clone app.py   : True
tar round-trip     -> app.py bytes match   : True
prepare (repo=None)-> empty workspace      : True
```

**Notes**
- **Known pre-existing failure, NOT mine:** repo-wide `make lint-check` (hence `make ci`) fails on the
  user's UNTRACKED WIP `src/decode/tools/substack_summarizer.py` (E402 ×2). It is untracked and not
  part of this task, so it is absent from CI's committed tree — I did not touch/commit/fix it, and I
  scoped my ruff runs to my own files. Everything else (my files + all 166 tracked files' format,
  1301 tests) is clean.
- Deliberate ceiling (`ponytail:` in `_git_clone`): no wall-clock cap on `git clone` — a large repo
  legitimately takes a while; a network stall relies on git's own timeouts. `GIT_TERMINAL_PROMPT=0`
  prevents an invisible interactive-prompt hang so a missing credential fails fast (task 082 owns the
  degrade-to-empty-workspace policy above this helper).
- Pure addition: `workspace.py` is imported by nothing yet (079 docker backend, 080 modal, 081 tool
  scope, 082 CLI wire it in). `sandbox_repo` has no reader yet (082).
- NOT COMMITTED — handing off to the Tester first.

### [Tester] 2026-07-03 23:21 — QA

**Test summary**
- Format / lint / pre-commit: PASS (scoped) — repo-wide `format-check` green (166 files); scoped
  `ruff format --check` + `ruff check` on the 3 tracked `.py` touched → clean. Repo-wide `lint-check`
  fails **only** on the untracked WIP `src/decode/tools/substack_summarizer.py` (E402 ×2) — a
  pre-existing, out-of-scope, not-in-committed-tree file the Tester was told to disregard; not touched.
- Unit tests: 1244 passed / 0 failed (incl. the 13 new `test_workspace.py`).
- Integration tests: 57 passed / 0 failed.
- Warnings: 0 (`filterwarnings=["error"]` in effect — any warning would have errored).
- `uv lock --check`: clean (Resolved 149 packages, no dep change).

**E2E adversarial pass** (real `git`, all under `tmp_path`; no daemon/remote/infra)
- Happy path: clone local repo → HEAD tree + real `.git`; reuse preserves a later new file **and**
  edits to `app.py`; `seed_skills` copies `SKILL.md`+`scripts/run.py`; tar round-trip (structure +
  bytes); `repo=None` → empty. All PASS.
- Break 1 (clone-failure hygiene / no reuse-poisoning): bogus local path → `RuntimeError` carrying
  git's stderr (`fatal: repository … does not exist`, exit 128). **Debris after a failed clone: `[]`**
  — git cleans up, the workspace is left empty; a subsequent `prepare_workspace(repo=<good>)` then
  **clones successfully** (no wrongful reuse). Credential-prompt URL
  (`…/definitely-not-a-real-private-repo-xyz.git`) → **RAISED in 0.71 s, no hang** (GitHub returns
  "Repository not found"; `GIT_TERMINAL_PROMPT=0` wiring unit-asserted). PASS.
- Break 2 (definition of "empty"): a stale **empty subdir** and a stray **`.DS_Store`** each make the
  reuse check (`any(workspace.iterdir())`) treat the workspace as populated → clone SKIPPED. Behaviour
  confirmed and **defensible** — errs toward preserving data, and the create→check window is
  microseconds so a first-launch stray is near-impossible. Noted, not a blocker (082 owns launch policy).
- Break 3 (tar fidelity + safety): exec bit `0o755 → 0o755` preserved; in-tree relative **symlink**
  preserved and resolves; extract into a **non-empty** dir merges (unrelated file survives) and
  overwrites collisions; **path traversal blocked** — a crafted `../ESCAPED_REL.txt` + an absolute-path
  member both raise `OutsideDestinationError` and write nothing outside the target (`filter="data"`).
  Non-UTF-8 filename: rejected by APFS (Errno 92) before the helper is reached — fs limitation, N/A. PASS.
- Break 4 (`seed_skills` overwrite semantics): a conflicting dest `SKILL.md` is **overwritten** by the
  source; a dest-only file is **preserved** (merge, not wipe); two extra seeds are idempotent
  (`dirs_exist_ok=True`). PASS.

**Acceptance criteria**
- [x] PASS — `workspace_dir(home)` → `home/.decode/sandbox` (resolved), idempotent — evidence:
  `test_workspace_dir_resolves_under_harness_home`, `..._creation_is_idempotent`; `workspace.py:48-60`;
  e2e `happy.workspace path` PASS.
- [x] PASS — `prepare_workspace` clones committed HEAD (real `.git`) / `repo=None` empty / `local=True`
  — evidence: `test_prepare_workspace_clones_committed_head_into_empty_workspace`,
  `..._repo_none_leaves_the_workspace_empty`, `..._local_clone_works`,
  `..._passes_local_flag_and_disables_prompt`, `..._omits_local_flag_by_default`; `workspace.py:63-119`.
- [x] PASS — non-empty workspace reused, not re-cloned — evidence:
  `test_prepare_workspace_reuses_a_non_empty_workspace` (marker survives, no `.git`/README);
  `workspace.py:82-84`; e2e `happy.reuse` + `b1.retry` PASS.
- [x] PASS — `seed_skills` copies `.decode/skills/**` / no-op when absent — evidence:
  `test_seed_skills_copies_project_skills_into_the_workspace`, `..._is_a_noop_when_no_skills_dir`,
  `..._is_idempotent`; `workspace.py:122-140`; e2e Break 4 PASS.
- [x] PASS — `tar_dir`/`extract_tar` round-trip a nested tree byte-for-byte — evidence:
  `test_tar_dir_and_extract_tar_round_trip_a_nested_tree` (incl. `\x00\x01\x02\xff`); `workspace.py:143-168`;
  e2e Break 3 (exec bit / symlink / merge / traversal) PASS.
- [x] PASS — `SANDBOX_REPO` on `Settings` (default `""`) + mirrored in `.env.example` — evidence:
  live `Settings()` → `sandbox_repo == ''`, env override `SANDBOX_REPO=git@github.com:acme/widgets.git`
  round-trips; `settings.py:258`; `.env.example:178`.
- [x] PASS — nothing outside its tests imports `workspace.py`; 0 warnings; `uv lock --check` passes —
  evidence: `grep` shows only `test_workspace.py` imports `decode.sandbox.workspace` (the
  `docker_executor.py` hits are the unrelated `settings.sandbox_workspace_dir` symbol). `make ci`:
  the sole red is the untracked, out-of-scope `substack_summarizer.py` lint noise — every **tracked**
  file (format + lint), 1244 unit + 57 integration tests, and the lock are green.

**Evidence**
```
$ make unit-tests
======================= 1244 passed in 82.08s (0:01:22) ========================
$ make integration-tests
======================== 57 passed in 98.64s (0:01:38) =========================
$ uv run pytest tests/unit/decode/sandbox/test_workspace.py -q
13 passed in 0.74s
$ uv lock --check
Resolved 149 packages in 3ms
$ uv run ruff format --check <3 tracked .py>  ->  3 files already formatted
$ uv run ruff check      <3 tracked .py>  ->  All checks passed!
# adversarial e2e: HAPPY (11) + Break 1-4 (13) probes → all PASS; traversal blocked by
#   OutsideDestinationError; failed clone leaves [] debris; retry clones cleanly.
```

**Other issues found** (none blocking)
- Note (Break 2): "empty" == zero directory entries, so a stray `.DS_Store` / empty subdir counts as
  "populated" and skips the clone. Safe direction (never destroys data); flag only so 082's launch
  policy is aware of the coarseness.
- Note: the live credential-URL probe 404'd (GitHub hides private vs absent) rather than hitting the
  interactive-prompt path, so the no-hang guarantee is shown by the fast fail (0.71 s) + the
  unit-asserted `GIT_TERMINAL_PROMPT=0`, not by a live prompt.
- Note: two unrelated untracked files exist in the tree (`docs/notes/sandboxing_seatbelt_bwrap.md`,
  `src/decode/tools/substack_summarizer.py`); the SWE correctly kept the 078 diff to its 4 files and
  did not sweep them in.

**VERDICT: PASS**
