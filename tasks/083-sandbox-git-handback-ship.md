---
id: 083-sandbox-git-handback-ship
feature: isolated-workspace
status: done
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

- [x] After a sandbox session with changes, a `decode/<session-id>` branch exists **locally** in
  `.decode/sandbox` capturing the final Workspace state — even when the push is disabled or fails
  (layered never-lose-results).
- [x] An uncommitted (dirty) Workspace is captured: the branch carries the model's uncommitted work
  (auto `git add -A && git commit`); the model's own commits/branches are preserved (not rewritten).
- [x] `git push origin decode/<id>` lands the branch on the remote for `--repo <URL>` and in the local
  source repo for `--repo <local path>` (credential-free); a push failure → ONE friendly line naming
  the branch + its `.decode/sandbox/` location, exit not blocked.
- [x] An **unchanged** Workspace (clean + no commits beyond the cloned HEAD) ships nothing.
- [x] Auto-ship fires on **both** REPL exit (best-effort, non-fatal, never blocks exit, failure line
  names the branch) and headless `decode run --repo` completion.
- [x] `/ship` is idle-only, reserved before the skill branch, prints the branch + push outcome, appears
  in the slash-completer + footer, triggers a modal export first, and in `none`/no-repo prints a
  friendly "no sandbox workspace to ship" line.
- [x] `none` mode and no-repo sessions are byte-identical (auto-ship is a no-op; the only new surface
  is the `/ship` friendly line).
- [x] **No git credential ever enters the sandbox env** — all git runs host-side (a test asserts the
  hand-back uses host `git`/`subprocess`, never `executor.run`/`backend.exec`; 075-style boundary
  assertion where cheap).
- [x] `make ci` green, 0 warnings, `uv lock --check` passes. (Ran the scoped equivalent — the
  standing constraint forbids `make ci`/`make pre-commit`, which lint the untracked WIP red:
  `ruff format` + `ruff check` clean on all touched files, full `uv run pytest` unit **and**
  integration suites green with 0 warnings under `filterwarnings=["error"]`, `uv lock --check` passes.)

## Out of scope

- **Auto-PR creation (`gh pr create`)** — deferred to M14 (ADR-0012 future-work).
- Rewriting/squashing the model's commit history; conflict resolution on push (a rejected push is a
  friendly line, not a merge).
- Docs prose (084) and the capstone hand-back proof (085).

## Log

### [SWE] 2026-07-04 05:40 — Implementation

**Files modified**
- `src/decode/sandbox/handback.py` (new) — pure host-side git hand-back: `ship_workspace(harness_home, *, repo, session_id) -> ShipResult` (collect → secure → ship), all git via host `subprocess`, never the executor/backend seam.
- `src/decode/context/session_log.py` — added a `session_id` property (recovered from the log filename) so the REPL can name the `decode/<session-id>` branch.
- `src/decode/tui/app.py` — the `/ship` command (`is_ship_command`, `_handle_ship_command`, `_ship_outcome_line`) reserved before the skill branch + idle-only; the on-exit auto-ship (`_ship_on_exit`); `/ship` added to `footer_hint` + `SlashCompleter`; imported `export_executor`.
- `src/decode/cli.py` — `_auto_ship_headless(repo, exec_id)` wired after the bypass `decode run` completes (best-effort, stderr-only, no-op in none/no-repo).
- `tests/unit/decode/sandbox/test_handback.py` (new) — 12 hermetic tests (real local git) incl. the security boundary test.
- `tests/unit/decode/context/test_session_log.py` — `session_id` property test.
- `tests/unit/decode/tui/test_app.py` — `/ship` predicate/footer/completer/reserved/handler + `_ship_on_exit` + `_ship_outcome_line` tests.
- `tests/unit/decode/runtime/test_run_command.py` — headless auto-ship wiring + `_auto_ship_headless` tests.

**Tests**
- Unit: 1394 passing, 0 failing (`uv run pytest tests/unit`) — +27 new for this task.
- Integration: 67 passing (`uv run pytest tests/integration`, docker daemon present; 0 leaked containers, foreign containers untouched).
- Scoped `ruff format` + `ruff check` clean on all touched files; `uv lock --check` passes. (`make ci`/`make pre-commit` deliberately NOT run — they lint the untracked WIP `substack_summarizer.py` + `docs/notes/` red per the standing constraint.)

**Acceptance criteria**
- [x] never-lose-results local branch even on push fail — `test_dirty_workspace_captured_on_local_branch_even_when_push_fails`
- [x] dirty capture + model history preserved — `test_dirty_workspace_auto_commit_message_names_the_session`, `test_model_commits_and_branches_are_preserved`
- [x] push lands local/URL origin credential-free; push-failure friendly line — `test_push_to_local_origin_lands_the_branch`, push-fail asserts in the never-lose test
- [x] unchanged Workspace ships nothing — `test_unchanged_workspace_ships_nothing` (+ seeded-scaffolding variant)
- [x] auto-ship on REPL exit + headless completion — `test_ship_on_exit_*`, `test_run_invokes_the_auto_ship_with_the_run_exec_id`
- [x] `/ship` idle-only/reserved/completer/footer/export-first/friendly-line — the `/ship` tests
- [x] none/no-repo byte-identical — laziness verified (fresh-interpreter import check), silent no-ops
- [x] no cred in the sandbox / all git host-side — `test_git_runs_host_side_never_through_the_sandbox_seam`
- [x] scoped-equivalent CI green (see note above)

**Evidence**
```
$ uv run pytest tests/unit -q
... 1394 passed in 88.70s

$ uv run pytest tests/integration -q
... 67 passed in 179.84s   (docker present; 0 containers leaked, foreign untouched)

# Real docker e2e (container writes /workspace -> host .decode/sandbox -> host git ships it):
[e2e] container bash exit=0; /workspace listing: README.md agent_made_this.txt
[e2e] ship result: branch=decode/e2edocke pushed=True
[e2e] branch landed in the origin with files: ['README.md', 'agent_made_this.txt']
[e2e] decode's seeded .decode/skills was NOT shipped (correct)
[e2e] container 655341e13f81 reaped (0 leaked)  →  PASS
```

**Notes**
- **Signature simplification (flag for review).** The task sketched `ship_workspace(harness_home, *, repo, origin, cloned_head, session_id)`. I collapsed the redundant `origin`/`cloned_head` params: both are recovered *inside* `ship_workspace` from the Workspace git (`git remote get-url origin`, `git rev-parse origin/HEAD`) exactly as the task's own "consume 082 — recover with git" guidance says, and both are derivable from `harness_home`'s Workspace, so passing them would be pure redundancy. This keeps ALL git in one module (the security crux), makes the 3 call sites one trivial call, and the caller specs use `ship_workspace(...)` (latitude). Net signature: `ship_workspace(harness_home, *, repo, session_id) -> ShipResult`.
- **Root-cause fix found during implementation (regression-tested first).** A real docker/modal session seeds `.decode/skills` into the Workspace (executor `_ensure_created` → `seed_skills`, ADR-0012 §5). That made `git status` perpetually dirty, so (a) a do-nothing session would defeat the unchanged-skip (AC4) and (b) the hand-back would ship decode's own scaffolding into the user's branch. Reproduced it (`?? .decode/` → shipped `.decode/skills/...`), then fixed: `ship_workspace` adds `.decode/` to the Workspace's **local** `.git/info/exclude` (never committed/pushed) before change-detection + commit — ignoring only the untracked seeded scaffolding while still capturing any `.decode` the user genuinely tracks. Covered by `test_seeded_decode_scaffolding_alone_is_unchanged` + `test_seeded_decode_scaffolding_is_not_shipped_with_user_work` and the enhanced docker e2e.
- Capture commit uses `git -c user.name=decode -c user.email=decode@localhost -c commit.gpgsign=false` so never-lose-results doesn't hinge on a configured host identity; the model's own commits keep their identity.
- HITL headless (`decode run --hitl --repo`) auto-ship is not wired (only the bypass `decode run` path is, per the task's "headless `decode run --repo` completion"); `/ship` + exit cover the interactive side. Flag if HITL auto-ship is wanted.
- DID NOT COMMIT — awaiting Tester. Did NOT touch `substack_summarizer.py` / `docs/notes/` (untracked WIP).

### [Tester] 2026-07-04 07:14 — QA

**Test summary**
- Format / lint (scoped on all 8 touched files): PASS — `ruff format --check` "8 files already formatted", `ruff check` "All checks passed!" (repo-wide `make ci`/`pre-commit` intentionally NOT run — the untracked WIP `substack_summarizer.py` + `docs/notes/` lint red per the standing constraint).
- Unit tests: 1394 passed / 0 failed (`uv run pytest tests/unit`).
- Integration tests: 67 passed / 0 failed (`uv run pytest tests/integration`, real docker daemon).
- Warnings: 0 (suites run under `filterwarnings=["error"]`; all-green ⇒ zero warnings).
- `uv lock --check`: PASS. Import laziness: `import decode.cli` and `import decode.tui.app` pull in **no** `kitaru`/`modal`/`decode.sandbox` (fresh-interpreter check).

**E2E adversarial pass** (hermetic local git + REAL docker; no LLM, no network)
- Happy path (real docker full stack): a REAL container writes `agent_made_this.txt` + `src/app.py` into `/workspace` → host `.decode/sandbox` bind mount → host-side `ship_workspace` → `decode/e2edocke` lands in the origin with both files, `.decode/skills` excluded, container reaped (0 leak). The container has **NO git binary** ("NO GIT IN SANDBOX") — direct proof the ship must run host-side. PASS.
- Break path 1 (state edge — diverged remote, no force-push): pre-created a divergent `decode/<id>` on origin, then a conflicting local change → push **refused** (`pushed=False`), remote tip byte-unchanged (before==after), remote work survives, local branch still carries the local work, message names branch + `.decode/sandbox`. PASS.
- Break path 2 (root-cause `.decode` exclude — untracked vs tracked): a USER-**tracked** `.decode/config.toml` edit IS captured (with the agent's content) while decode's UNtracked seeded `.decode/skills` is NOT — `info/exclude` masks untracked only. PASS.
- Break path 3 (boundary — session_id): empty/all-unsafe → `decode/session` (valid); slashes stripped; 5000-char truncated to 8; e2e empty-id still ships a valid pushable branch. All git-valid. PASS (see note 1 for an unreachable edge).
- Break path 4 (idempotency): 4 re-ships (pre-existing no-newline exclude file) → exactly one `.decode/` line, pre-existing `*.log` preserved. PASS.
- Break path 5 (git-native shapes): push to a **bare** origin lands the branch; a clone with unresolvable `origin/HEAD` is treated as **changed** (ships, never silently drops). PASS.

**Mutation checks (proving the tests are not vacuous)** — each applied to `handback.py`, run, then restored (final file byte-identical to pre-QA):
- Security boundary (AC8), all 4 guards bite: inject a cred env → RED (`GIT_INJECTED_TOKEN` caught); route via a functional `env git` wrapper → RED (`cmd[0]=='env'!='git'`); define a module-level `active_backend` seam symbol → RED (`hasattr` structural guard). Non-vacuous.
- Never-lose (AC1): skip the dirty auto-commit → RED (work not on branch); skip `branch -f` → RED (branch absent). Ordering: secure (L151) is unconditionally before push (L153).
- `.decode` exclude (AC4): remove `_exclude_decode_namespace()` → `test_seeded_..._alone_is_unchanged` RED (seeded skills counted as work) AND `test_seeded_..._not_shipped` RED (`.decode/skills` shipped). The fix is genuinely load-bearing.

**Acceptance criteria** — all 8 verified:
- [x] PASS — never-lose local branch even on push-fail — `test_dirty_workspace_captured_on_local_branch_even_when_push_fails` (mutation-verified) + break path 1 (real non-ff rejection).
- [x] PASS — dirty capture + model history preserved — `test_dirty_workspace_auto_commit_message_names_the_session`, `test_model_commits_and_branches_are_preserved` + break path 2.
- [x] PASS — push lands local/URL/bare origin credential-free; push-fail friendly line, exit not blocked; no force-push — `test_push_to_local_origin_lands_the_branch`, break paths 1+5, docker e2e E3/E4, best-effort wrappers unit-tested.
- [x] PASS — unchanged Workspace ships nothing + `.decode` root-cause fix — `test_unchanged_workspace_ships_nothing` + the two seeded-scaffolding tests (mutation-verified) + docker e2e E5 + break paths 2/4.
- [x] PASS — auto-ship on REPL exit + headless completion — `test_ship_on_exit_*`, `test_run_invokes_the_auto_ship_with_the_run_exec_id`, `test_auto_ship_headless_*`; wiring read in `run_app` (app.py:1236, after `close_executor`) + `run` (cli.py:633).
- [x] PASS — `/ship` idle-only/reserved-before-skill/completer/footer/export-first/friendly-line — the `/ship` unit tests (busy, none/no-repo, idle exports-then-ships-then-prints in order) + reserved-branch wiring (app.py:1172).
- [x] PASS — none/no-repo byte-identical — import-laziness (fresh interpreter), none-mode `--repo` CLI guard (exit 1, friendly line), all three ship entrypoints return before any sandbox import in none/no-repo.
- [x] PASS — no git cred in the sandbox / all git host-side — `test_git_runs_host_side_never_through_the_sandbox_seam` (4 guards, all mutation-verified) + structural (only `workspace_dir` imported from `decode.sandbox`) + docker e2e "NO GIT IN SANDBOX".
- [x] PASS — scoped-CI green (see Test summary): ruff clean, 1394 unit + 67 integration green / 0 warnings, `uv lock --check` OK.

**Evidence**
```
$ uv run pytest tests/unit -q         → 1394 passed in 89.63s
$ uv run pytest tests/integration -q  → 67 passed in 182.64s
$ uv lock --check                     → Resolved 149 packages (OK)
docker e2e: DOCKER E2E PASSED (E1-E6); container 86c16e4c… reaped, 0 uv containers leaked, foreign tree-* untouched.
```

**Other issues found** (non-blocking; PASS-with-note for the orchestrator/PA)
1. `_branch_name` sanitizes to `[alnum -._]` + truncates to 8 but does NOT guard git's leading-dot / trailing-`.lock` / leading-dash ref rules (e.g. a `.`-leading id → invalid ref). **Unreachable today** — both session_id sources are UUIDs (REPL `uuid4()`, headless Kitaru exec_id), so the first 8 chars are always hex. Also `str.isalnum()` is Unicode-aware so accented ids would be kept (still git-valid). Purely a defensive-hardening follow-up; not a defect.
2. No positive-ship automated e2e drives `_ship_on_exit` through the full `run_app` shutdown (the existing `run_app` e2e tests run in none-mode = no-op). Wiring is a single unconditional call verified by reading + the function is unit-tested + my docker e2e exercises `ship_workspace` end-to-end. Consider a follow-up e2e if desired.
3. HITL headless (`decode run --hitl --repo`) auto-ship is intentionally NOT wired (SWE flagged this) — only the bypass `decode run` path. Consistent with the written AC5 ("headless `decode run --repo` completion"); flagging for PA if HITL auto-ship is wanted.

**VERDICT: PASS**
