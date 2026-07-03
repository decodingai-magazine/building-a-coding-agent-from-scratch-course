---
id: 082-sandbox-repo-clone-cli-and-description
feature: isolated-workspace
status: pending
---

# Workspace = git clone — --repo/--local CLI, none-mode guard, unified bash description, progress

Tags: `sandbox`, `cli`, `workspace`
Depends on: #081
Blocks: #083, #084, #085

The user-facing completion of the isolated Workspace (ADR-0012): the Workspace is populated by a host
`git clone` of a user-provided repo via `--repo`/`SANDBOX_REPO`, so decode works on any repo like
codex/opencode. Adds the none-mode guard, the ONE unified sandbox `bash` description, and the
eager-start progress lines. The clone is a real host-visible git repo at `.decode/sandbox` — the git
**hand-back** (branch + push) is built on top in task 083.

## Scope

- **CLI flags** (`cli.py`), on both `decode` and `decode run`:
  - `--repo <url-or-local-path>` — cloned into the Workspace at launch; overrides `SANDBOX_REPO`;
    omitted/unset → empty Workspace.
  - `--local` — fast local clone for a local-path `--repo`.
  - Thread the resolved repo (flag > `SANDBOX_REPO` > none) into `prepare_workspace(harness_home,
    repo=…, local=…)` in both `tui/app.py` and `runtime/flow.py`. **Record the clone's origin +
    HEAD** (so task 083's ship can tell "unchanged vs cloned HEAD" and push to the right origin).
- **none-mode guard** (task-004 style): `--repo`/`SANDBOX_REPO` set while `SANDBOX_MODE=none` → one
  friendly stderr line + non-zero exit (no traceback), in **both** the REPL startup chain and the
  headless `run`/`replay` pre-flight (`_runtime_config_preflight`).
- **Clone at launch:** committed HEAD, ambient host git creds (private repos work); a non-empty
  Workspace is reused; a clone failure surfaces one friendly line and degrades (empty Workspace),
  never crashes.
- **Unified `bash` description** (`bash.py`): replace the two per-mode suffixes with ONE sandbox
  paragraph for docker AND modal — fresh-exec (`cd`/`export` don't persist; chain in one call);
  `/workspace` is the isolated Workspace (a clone of your repo if `--repo`, else empty scratch); fs
  persists across calls. `none` byte-identical.
- **Eager-start progress** (`tui/app.py`): keep `Decode - starting <mode> sandbox …`; add `Decode -
  cloning <repo> into the workspace …` when cloning and (modal) an `uploading the workspace …` line;
  the banner keeps `sandbox:<mode>`.
- **Tests:** `--repo <local repo>` clones and a `read`/`bash` sees its files under `/workspace`; the
  none-mode guard fires (REPL + headless); omitted → empty; unified description captured (docker ==
  modal == base + paragraph; `none` base); `--local` local clone; the cloned Workspace is a real repo
  host-visible at `.decode/sandbox` with the origin recorded (the substrate task 083 ships from).

## Acceptance criteria

- [ ] `decode --repo <local repo>` (docker or modal) clones HEAD into the Workspace; a `read`/`bash`
  sees its files under `/workspace` (== `deps.cwd`). `decode run --repo …` does the same headlessly.
- [ ] `--repo`/`SANDBOX_REPO` with `SANDBOX_MODE=none` → one friendly stderr line + non-zero exit in
  the REPL and the headless pre-flight.
- [ ] No repo given → empty Workspace; sandbox mode still works. `--local` uses a local clone; a clone
  failure degrades to a friendly line + empty Workspace.
- [ ] `bash` description is ONE unified sandbox paragraph (docker == modal == base + paragraph);
  `none` byte-identical (captured in a test).
- [ ] Eager-start prints the starting/cloning (+ modal uploading) progress lines; banner shows
  `sandbox:<mode>`.
- [ ] The cloned Workspace is a real repo host-visible at `.decode/sandbox` with its origin + cloned
  HEAD recorded — the substrate the task-083 hand-back branches, secures, and pushes.
- [ ] `make ci` green, 0 warnings, `uv lock --check` passes.

## Out of scope

- The git hand-back / branch / push / `/ship` (083). Docs prose (084); capstone rewrite (085). Auth
  beyond ambient git creds; a local repo's uncommitted working-tree dirt (HEAD only).

## Log
