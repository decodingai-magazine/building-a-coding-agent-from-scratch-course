---
id: 078-sandbox-workspace-host-helpers
feature: isolated-workspace
status: pending
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

- [ ] `workspace_dir(home)` → `home/.decode/sandbox` (resolved); creation is idempotent.
- [ ] `prepare_workspace(home, repo=<local repo>)` clones the committed HEAD tree into the empty
  workspace with a real `.git`; `repo=None` leaves it empty; `local=True` uses a local clone.
- [ ] A non-empty workspace is reused, not re-cloned (a pre-placed marker file survives).
- [ ] `seed_skills(workspace)` copies `<...>/.decode/skills/**` → `workspace/.decode/skills/**`; a
  missing source is a no-op.
- [ ] `tar_dir`/`extract_tar` round-trip a nested tree byte-for-byte.
- [ ] `SANDBOX_REPO` exists on `Settings` (default `""`) and is mirrored in `.env.example`.
- [ ] Nothing outside its own tests imports `workspace.py`; `make ci` green, 0 warnings,
  `uv lock --check` passes.

## Out of scope

- Any executor/backend wiring (079/080), file-tool routing (081), CLI/clone-at-launch (082).
- **The retired mtime-delta sync** — no marker/delta/size-cap helpers exist (ADR-0012 rejects it for
  deletion-blindness).

## Log
