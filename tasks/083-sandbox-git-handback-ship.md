---
id: 083-sandbox-git-handback-ship
feature: isolated-workspace
status: pending
---

# Git hand-back — ship the Workspace as a decode/<session-id> branch (auto on exit + /ship)

Tags: `sandbox`, `workspace`, `git`, `cli`
Depends on: #082, #080
Blocks: #084, #085

The built hand-back (ADR-0012 §8): the harness **guarantees the Workspace's results survive as a new
branch pushed back to the provided repo** — all git runs **host-side against the local Workspace git
state, so no credential ever enters the sandbox** (the same secrets-never-in-the-sandbox invariant as
the Credential Proxy). Layered durability: the local `decode/<session-id>` branch always exists even
when the push fails. Triggered automatically on session exit AND explicitly via a `/ship` TUI command.

## Scope

- **New `src/decode/sandbox/handback.py`** — a pure host-side helper
  `ship_workspace(harness_home, *, repo, origin, cloned_head, session_id) -> ShipResult` (git via
  host `subprocess`, never `executor.run`/`backend.exec`):
  1. **Collect** — the local Workspace git state at `harness_home/.decode/sandbox` (docker: the live
     mount; modal: already swept down by the executor `export()` — the callers ensure the export ran
     first).
  2. **Secure** — the model is NOT trusted to have committed. Ensure a `decode/<session-id-short>`
     ref points at a commit capturing the **final** Workspace state: create/point the branch at the
     current HEAD (wherever the model is), and if the worktree is dirty `git add -A && git commit -m
     "decode session <id>"`. Do **not** rewrite the model's own branches/commits — the branch is
     created at their HEAD so their history is preserved; a re-ship (a later `/ship` or exit after a
     `/ship`) fast-forwards the same ref.
  3. **Ship** — `git push origin decode/<session-id>` with the user's ambient host creds. `--repo
     <URL>` → the branch lands on the remote; `--repo <local path>` → origin is the local source repo,
     so the push lands the branch there credential-free. No force-push (a diverged push fails
     gracefully).
  4. **Skip** — when no `--repo` was given / the Workspace is not a git repo / the Workspace is
     **unchanged vs the cloned HEAD** (clean worktree AND no commits beyond `cloned_head`): ship
     nothing, return a `skipped` `ShipResult`.
  - `ShipResult(branch: str | None, pushed: bool, message: str)` — `branch=None` on skip; a
    push failure returns `pushed=False` with `branch` set and a message naming the branch + that the
    results live in `.decode/sandbox/` (never-lose-results).
- **Auto-trigger — REPL exit** (`tui/app.py`): after `close_executor()` (which, for modal, already
  ran the export sweep via `aclose`), run `ship_workspace(...)` in the shutdown sequence alongside the
  memory write-back / LSP / executor teardown. **Best-effort non-fatal** like its siblings — it never
  blocks exit — but on failure its one line **names the branch**. A no-op in `none` mode / no-repo /
  unchanged Workspace.
- **Auto-trigger — headless** (`cli.py` `decode run --repo` completion): after the flow completes and
  the output is printed, run the same host-side ship (best-effort; one line naming the branch +
  outcome on stderr). Uses the run's exec id as the session id.
- **`/ship` TUI command** (`tui/app.py`): reserved slash command, matched **before the skill branch**
  (like `/compact` / `/clear`), **idle-only** (the `Phase.IDLE` guard; a busy line otherwise). It (a)
  triggers an executor `export()` first (modal mid-session sweep; docker no-op) via
  `export_executor()`, then (b) runs `ship_workspace(...)` and prints the branch name + push outcome.
  In `none` mode / no-repo it prints a friendly "no sandbox workspace to ship" line. Add `/ship` to
  the `SlashCompleter` menu and the `footer_hint` list.
- **Session id source:** the REPL uses the `SessionLog` session id; headless uses the run's exec_id.
  The branch name is deterministic: `decode/<short-id>`.
- **Tests:** offline git (a local repo + a local `--repo` origin, no network): dirty Workspace → the
  branch exists locally and carries the uncommitted work even with the push disabled/failing; a
  push-to-local-origin lands `decode/<id>`; an unchanged Workspace ships nothing; `none`/no-repo
  auto-ship is a no-op and `/ship` prints the friendly line; a cheap boundary assertion that no git
  cred/token appears in the sandbox env and no git command routes through the executor/backend seam
  (reusing the 075 boundary-test style).

## Acceptance criteria

- [ ] After a sandbox session with changes, a `decode/<session-id>` branch exists **locally** in
  `.decode/sandbox` capturing the final Workspace state — even when the push is disabled or fails
  (layered never-lose-results).
- [ ] An uncommitted (dirty) Workspace is captured: the branch carries the model's uncommitted work
  (auto `git add -A && git commit`); the model's own commits/branches are preserved (not rewritten).
- [ ] `git push origin decode/<id>` lands the branch on the remote for `--repo <URL>` and in the local
  source repo for `--repo <local path>` (credential-free); a push failure → ONE friendly line naming
  the branch + its `.decode/sandbox/` location, exit not blocked.
- [ ] An **unchanged** Workspace (clean + no commits beyond the cloned HEAD) ships nothing.
- [ ] Auto-ship fires on **both** REPL exit (best-effort, non-fatal, never blocks exit, failure line
  names the branch) and headless `decode run --repo` completion.
- [ ] `/ship` is idle-only, reserved before the skill branch, prints the branch + push outcome, appears
  in the slash-completer + footer, triggers a modal export first, and in `none`/no-repo prints a
  friendly "no sandbox workspace to ship" line.
- [ ] `none` mode and no-repo sessions are byte-identical (auto-ship is a no-op; the only new surface
  is the `/ship` friendly line).
- [ ] **No git credential ever enters the sandbox env** — all git runs host-side (a test asserts the
  hand-back uses host `git`/`subprocess`, never `executor.run`/`backend.exec`; 075-style boundary
  assertion where cheap).
- [ ] `make ci` green, 0 warnings, `uv lock --check` passes.

## Out of scope

- **Auto-PR creation (`gh pr create`)** — deferred to M14 (ADR-0012 future-work).
- Rewriting/squashing the model's commit history; conflict resolution on push (a rejected push is a
  friendly line, not a merge).
- Docs prose (084) and the capstone hand-back proof (085).

## Log
