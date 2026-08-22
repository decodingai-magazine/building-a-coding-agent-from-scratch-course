---
status: done
feature: ci-infra
---

# CI (GitHub Actions) has stopped triggering on pushes to feat/kitaru-migration (PR #65)

Tags: `infra`, `ci`
Depends on: None
Blocks: PR #65 merge (no green CI signal to verify against)

Filed by On-Call after watching the push of `2ae189e` (PR #65, `Closes #N` chain for tasks
131-146). Not a code/test failure — the `CI` workflow (`.github/workflows/ci.yml`) is not being
triggered at all for this branch's recent pushes. This is not traceable to any single task's code
change; `.github/workflows/ci.yml` itself is unmodified in the affected commit range and
`make ci` is reported green locally (2506 passed) through task 146.

## Scope

Get GitHub Actions triggering again for `feat/kitaru-migration` / PR #65, and confirm a `CI`
workflow run completes (green or red — either is an actionable signal) for the current head
commit.

## Evidence

- Last commit with a `GitHub Actions` check-suite: `ac2ca7763f80deb45ce9f6a423484d2be6736d05`
  ("docs: add kitaru evals and replays walkthrough") — check-suite `completed`/`success` at
  `2026-08-22T08:05:38Z` (run id `32561361366`).
- Every commit pushed after it on this branch has **no** `GitHub Actions` check-suite at all
  (only `GitHub Pages` fires for a couple of them):
  `c25a48e`, `86decd3`, `869157d`, `07b453b`, `0d32b97`, `a968e8c`, `50ca379`, `a6ac2e8`,
  `2ae189e` (current PR head, confirmed via `gh pr view 65 --json headRefOid`).
  ```
  $ gh api repos/decodingai-magazine/building-a-coding-agent-from-scratch-course/commits/2ae189e9f92c905624d1c138e989c449f9edfaa3/check-suites --jq '.check_suites[] | "\(.app.name) \(.status)"'
  GitHub Pages queued
  $ gh api repos/decodingai-magazine/building-a-coding-agent-from-scratch-course/commits/2ae189e9f92c905624d1c138e989c449f9edfaa3/status --jq '.total_count'
  0
  ```
- Ruled out: `.github/workflows/ci.yml` unchanged in `ac2ca77..2ae189e` (no YAML break); Actions
  are enabled repo-wide (`allowed_actions: all`, `enabled: true`); it is not a global outage —
  `main`, `dependabot/*`, and a `decode/<session-id>` branch all triggered normal `GitHub Actions`
  runs throughout the same window (10:43-12:48 UTC today), while `feat/kitaru-migration` got none
  since 08:05.
- All commits in the affected range are authored/committed by a real user identity
  (`iusztinpaul <p.e.iusztin@gmail.com>`), not a bot/App identity — rules out the common
  "`GITHUB_TOKEN`-authored push can't trigger further workflow runs" loop-guard as the *obvious*
  explanation from commit metadata alone, but the credential actually used to run `git push`
  (as opposed to the commit author/committer field) has not been inspected — worth checking
  whether pushes since 08:05 went through a different remote/credential (e.g. a fine-grained PAT
  vs. the previous one, or any GitHub App installation token) than the one used for `ac2ca77`.
  Note ADR-0016 §4 describes a *sandbox* hand-back path (`SANDBOX_GIT_TOKEN` → `GITHUB_TOKEN`) —
  confirm this branch's pushes did **not** go through that path (they shouldn't have; this is a
  human-driven feature branch, not a headless run's `decode/<session-id>` branch).

## Suggested remediation to try, in order (needs `gh` PR-state permission this agent does not
have in auto mode — a human or a differently-permissioned agent should run these)

1. `gh pr close 65` then `gh pr reopen 65` — `reopened` is one of `pull_request`'s default
   trigger types (`opened`/`synchronize`/`reopened`), so this alone may retrigger the `CI`
   workflow without any code change. Cheapest, fully reversible, first thing to try.
2. If (1) doesn't retrigger it: push a trivial empty commit
   (`git commit --allow-empty -m "chore: retrigger CI"`) to force a fresh `synchronize` event,
   and diff the credential/remote used for that push against the one used for the last commit
   that *did* trigger CI (`ac2ca77`).
3. If still nothing: check repo Settings → Actions → General (or `gh api
   repos/.../actions/permissions/workflow`) for a workflow-permissions change, and check whether
   PR #65's `mergeable: CONFLICTING` state (rebase against `main` needed) is somehow implicated —
   try rebasing/updating the branch against `main` as an independent variable.

## Acceptance Criteria

- [ ] A `GitHub Actions` check-suite (workflow `CI`) exists and completes (`success` or
      `failure`) for the current PR #65 head commit — confirmed via
      `gh api repos/.../commits/<sha>/check-suites` or `gh pr checks 65`.
- [ ] If the fix required a code/config change (e.g. `ci.yml` trigger types), it's documented
      here with the diff reference; if it required only a GitHub-side action (close/reopen,
      empty commit), that's logged too — no silent fix.
- [ ] On-Call re-verifies: `gh run list --branch feat/kitaru-migration` shows a run for the
      then-current head SHA, green or red, and hands off accordingly (red → new diagnosis cycle;
      green → close this task).

## Out of scope

- Task 147 (`ensure_harness_home` OSError) and task 148 (README durability copy) — unrelated,
  already filed.
- Re-diagnosing the 131-146 feature code itself — `make ci` is reported green locally through
  task 146; this task is purely about the pipeline not running at all.

---

Refs #65

## Log

### [On-Call] 2026-08-22 16:35 — CI trigger investigation

**Failed step:** none — no `CI` workflow run exists for the current push at all (not a red run;
a *missing* run).

**What I checked:** `gh run list --branch feat/kitaru-migration` (last run: `ac2ca77`, success,
08:05 UTC) / `gh pr checks 65` ("no checks reported") / polled `gh api
commits/<sha>/check-runs` and `.../check-suites` for the PR head (`2ae189e`) for ~5 minutes,
zero `GitHub Actions` check-suite created / cross-checked `main`, `dependabot/*`, and a
`decode/<session-id>` branch, all of which triggered normal Actions runs in the same window,
ruling out a global outage / confirmed `.github/workflows/ci.yml` unmodified in the affected
commit range, ruling out a broken workflow file.

**Root cause:** Unknown at the GitHub-Actions-internals level — the webhook/event that should
create a `GitHub Actions` check-suite for `pull_request: synchronize` on this branch has stopped
firing since commit `ac2ca77` (08:05 UTC), across 9 subsequent pushes over ~8 hours, while other
branches in the same repo continued triggering normally. Not a code defect (workflow file
untouched; `make ci` green locally). Attempted the standard reversible remediation
(`gh pr close 65` / `gh pr reopen 65` to force a `reopened` trigger event) but it was blocked by
this agent's own permission scope (Claude Code auto-mode classifier denied the PR-state-changing
action) — handing off rather than working around the denial.

Fixing now — via the SWE/orchestrator or a human with PR-close/reopen permission, per the
Suggested remediation section above.

### [On-Call/Orchestrator] 2026-08-22 — Resolved

Root cause: the PR had become `mergeable: CONFLICTING` (dependabot commits on main vs the
branch's pyproject/uv.lock changes), and GitHub fires NO `pull_request` workflow runs for a
conflicting PR — the workflow's only branch trigger is `pull_request`, so CI went silent with
no error anywhere. Fix: merged origin/main into the branch (`4cd2f18`, our ADR-0019 pins kept,
main's floor bumps folded in, re-locked, unit suite green) — CI fired immediately and passed
(run 32586007775, 4m46s). Lesson recorded: a silent CI on a PR branch whose workflow only
triggers on pull_request means CHECK MERGEABILITY FIRST.
