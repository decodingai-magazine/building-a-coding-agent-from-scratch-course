---
id: 119
feature: evals
status: done
---

# Demo skills 5–7: review-swarm, sandbox-feature-pr, todoist-app

Depends on: none. Implements ADR-0017 §2 (Track A).

## Scope

- **demo-5-review-swarm** — prompt-only: fan out THREE parallel subagents via the `agent` tool
  (explore subagents doing read-only review work, ADR-0013), one per decode module (suggest
  `src/decode/permissions/`, `src/decode/sandbox/`, `src/decode/context/`); merge into ONE
  severity-ranked verdict (Critical/Major/Minor) INCLUDING text-based diagrams (Mermaid/ASCII) of
  each module's structure.
- **demo-6-sandbox-feature-pr** — the meta "decode improves decode" demo. Body documents the full
  flow: launch `SANDBOX_MODE=docker decode --repo <course repo URL>` (note: the grilled spec wrote
  `--sandbox docker`; the CLI exposes sandbox mode via `SANDBOX_MODE` — document the invocation
  that works, and mention the modal variant `SANDBOX_MODE=modal`); implement a small
  self-contained feature inside the Workspace; on exit the Hand-back pushes the
  `decode/<session-id>` Session Branch (ADR-0012 §8); then `gh pr create --draft` against the
  course repo from that branch.
- **demo-7-todoist-app** — prompt-only: write a single-file `index.html` todo app — vanilla JS +
  `localStorage`, zero deps: add / complete / filter (all|active|done) — then `open index.html`.

**Tests**: same loader-parse unit coverage as 118 for all three skills.

## Acceptance Criteria

- [x] Three skills in the catalog, loadable by name; frontmatter/body conventions hold. — verified
      by `test_demo_skills.py` parse/catalog/merge tests + live `loader.load_skills(cwd)`.
- [x] demo-6's documented invocation is verified against the real CLI flags (no `--sandbox` flag
      invented) and names both sandbox rungs + the draft-PR step. — verified by
      `test_cli_exposes_repo_but_no_sandbox_flag`, `test_demo_6_body_uses_the_real_invocation_shape`,
      `test_demo_6_targets_the_real_course_repo`.
- [ ] [HUMAN] Manual spot-run of demo-5 or demo-7 end-to-end, logged. — needs a live agent; no
      `GEMINI_API_KEY` present in this environment. Offline loader-dispatch verified instead.
      Awaiting human verification with a keyed live run.
- [x] `make ci` green. — verified by Tester: `uv lock --check` + format-check + lint-check + full
      `pytest` (unit+integration) → 2054 passed, 2 skipped (both explicit
      GEMINI_API_KEY/OPIK_API_KEY-gated live-model skips), 0 failed. Two transient docker
      integration-test failures seen on an earlier run were confirmed pre-existing/unrelated to
      this diff (baseline `git stash` reproduces the same two tests passing; isolated reruns of
      the two files pass cleanly; a repeat full `make ci` run passed clean) — resource contention
      across the ~200-container docker suite, not a regression from this task.

## Out of scope

- Adding a `--sandbox` CLI flag (file separately if wanted). Credential-proxy anything (non-goal).

## Log

### [SWE] 2026-07-14 11:05 — Implementation

**Files modified**
- `.decode/skills/demo-5-review-swarm/SKILL.md` — prompt-only skill: fan out three parallel
  read-only Explore subagents (ADR-0013), one per decode module (permissions/sandbox/context),
  merged into one Critical/Major/Minor verdict with a Mermaid/ASCII diagram per module.
- `.decode/skills/demo-6-sandbox-feature-pr/SKILL.md` — prompt-only skill documenting the
  "decode improves decode" round-trip: `SANDBOX_MODE=docker decode --repo <course repo>` (+ the
  `SANDBOX_MODE=modal` variant), build a small feature in the Workspace, Hand-back pushes the
  `decode/<session-id>` Session Branch (ADR-0012 §8), then `gh pr create --draft`. No `--sandbox`
  flag invented; sandbox mode documented as an env var.
- `.decode/skills/demo-7-todoist-app/SKILL.md` — prompt-only skill: single-file `index.html` todo
  app, vanilla JS + `localStorage`, zero deps (add/complete/filter all|active|done), then
  `open index.html`.
- `tests/unit/decode/skills/test_demo_skills.py` — extended: all three demos added to the
  loader-parse/catalog/merge/resource-dir coverage; new demo-5 fan-out/verdict/diagram assertions;
  new demo-6 CLI-consistency tests (real Click `--repo`, no `--sandbox`; body uses SANDBOX_MODE
  docker+modal, targets the real course repo, ends in a draft PR); a no-credential-proxy guard
  across all authored demos; demo-7 single-file/localStorage/filter-state contract.

**Tests**
- Unit: 22 passing in `test_demo_skills.py` (was 11); full suite 1941 passing, 0 failing via
  `make pre-commit`. Integration: N/A — no infra changes (docs + tests only).
- TDD red→green confirmed: the 11 new/extended assertions failed on missing files/content before the
  skills were authored, pass after.

**Acceptance criteria**
- [x] Three skills loadable by name; conventions hold — `test_demo_skills.py` + live loader dispatch.
- [x] demo-6 invocation verified against real CLI (no `--sandbox`), names both rungs + draft PR.
- [ ] [HUMAN] Manual spot-run of demo-5/7 — no `GEMINI_API_KEY` present; offline loader-dispatch
      verified instead. Needs a keyed live agent run.
- [ ] [HUMAN] `make ci` green — local unit/format/lint clean; full CI is the Tester's gate.

**Evidence**
```
$ uv run pytest tests/unit/decode/skills/test_demo_skills.py -q
......................                                                   [100%]
22 passed in 3.50s

$ make pre-commit
======================= 1941 passed in 123.96s (0:02:03) =======================

$ uv run python -c "loader.load_skills(cwd) -> demo-5/6/7"
demo-5-review-swarm: desc[186] body[2248] resource_dir=None
demo-6-sandbox-feature-pr: desc[239] body[2750] resource_dir=None
demo-7-todoist-app: desc[166] body[1277] resource_dir=None

$ uv run decode --help | grep -iE 'repo|sandbox'
  --repo URL-OR-PATH  Clone this repo ... Requires a sandbox mode (SANDBOX_MODE=docker|modal).
  (no --sandbox flag exists)
```

**Notes**
- All three demos are prompt-only (SKILL.md only) → `resource_dir=None`; no fixtures to keep honest,
  unlike demo-2/demo-4.
- No credential-proxy references anywhere (non-goal); no `--sandbox` CLI flag added (out of scope).
- `GEMINI_API_KEY` absent in this environment, so the live spot-run of demo-5/demo-7 is deferred to a
  `[HUMAN]` keyed run per the task's spot-run note.

### [Tester] 2026-07-14 14:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 295 files clean; `ruff check`: all
  checks passed; `make pre-commit`: 1941 passed)
- Unit tests: 1941 passed / 0 failed (`make unit-tests` equivalent via `make pre-commit`)
- Integration tests (`make ci` full run): 2054 passed / 2 skipped (both explicit
  GEMINI_API_KEY/OPIK_API_KEY-gated) / 0 failed on the clean run. An earlier full-suite run hit 2
  transient docker-integration failures (`test_docker_executor.py::test_timeout_kills_...`,
  `test_sandbox_teardown.py::test_headless_bypass_flow_reaps_...`); both traced to pre-existing
  docker resource contention, NOT this diff — confirmed via `git stash` to the pre-119 baseline
  (both tests pass there too), isolated reruns of both files (11 passed), and a repeat `make ci`
  (2054 passed, 0 failed). Neither failing test touches skills/loader/CLI code this diff changed.
- Warnings: 0 (pytest `filterwarnings=["error"]` — a stray `RuntimeWarning` observed came from
  `litellm` teardown outside pytest's collection, not a test warning)

**E2E adversarial pass**
- Happy path: real end-to-end dispatch of all three demos through the actual `skill()` tool
  (`decode.tools.skills.skill`, the exact code path a live agent invokes) →
  `demo-5-review-swarm` (2248 chars), `demo-6-sandbox-feature-pr` (2750 chars),
  `demo-7-todoist-app` (1277 chars) all returned their full body payload. PASS.
- Break path 1 (malformed input — unknown skill name): `await skill(ctx, "demo-999-nonexistent")`
  → `ModelRetry: No skill named 'demo-999-nonexistent'. Available skills: adr, commit, ...` — no
  crash, model-readable retry with the full catalog. PASS.
- Break path 2 (claim-vs-code adversarial audit — demo-6, every sentence checked against the real
  source, not taken on faith): `--repo` exists on both `cli` and `cli.commands["run"]`
  (`src/decode/cli.py:282,396`); `SANDBOX_MODE` is the real `Literal["none","docker","modal"]`
  field (`src/decode/config/settings.py:277`); no `--sandbox` flag anywhere in `cli.py`; Hand-back
  branch pattern `decode/<session-id>` matches `_BRANCH_PREFIX = "decode/"` +
  `_branch_name()` in `src/decode/sandbox/handback.py:24-25,110-112` exactly; the URL in the body
  (`git@github.com:decodingai-magazine/building-a-coding-agent-from-scratch-course.git`) matches
  `git remote get-url origin` exactly; `gh pr create --draft --repo --head --title --body` — all
  five flags valid per `gh pr create --help`; the Explore subagent toolset claimed in demo-5
  (`read`/`glob`/`grep`/`lsp`, no write/edit/bash) matches `src/decode/agents/builtin/explore.md`
  frontmatter exactly; the "N `agent(...)` calls in one response run concurrently" claim matches
  ADR-0013 verbatim. PASS — zero drift found between any claim and the real code.
- Break path 3 (hostile/state edge — resource contention under full-suite docker load): ran
  `make ci` three times total; one run failed 2 different docker tests (flaky, not the same test
  twice), the other two ran fully clean. Root-caused to pre-existing environmental flakiness (not
  a code defect in this diff, not deterministic, doesn't reproduce in isolation or on the
  pre-119 baseline). PASS with note — recommend a separate flakiness ticket for the docker
  integration suite under full-load contention (outside this task's scope; not caused by it).

**Acceptance criteria**
- [x] PASS — Three skills in the catalog, loadable by name; frontmatter/body conventions hold —
      `test_demo_skills.py::test_demo_skill_md_parses_through_the_real_loader[demo-5/6/7]`,
      `test_authored_demos_appear_in_the_project_catalog`,
      `test_authored_demos_load_alongside_the_builtins` all pass; live
      `loader.load_skills(REPO_ROOT)` shows 13 skills total (10 built-in/prior + 3 new), no name
      collisions; live `skill()` dispatcher round-trip confirmed above.
- [x] PASS — demo-6's documented invocation verified against the real CLI flags (no `--sandbox`
      invented), names both sandbox rungs + the draft-PR step —
      `test_cli_exposes_repo_but_no_sandbox_flag`, `test_demo_6_body_uses_the_real_invocation_shape`,
      `test_demo_6_targets_the_real_course_repo` all pass; manually re-verified every claim
      against `cli.py`/`settings.py`/`handback.py`/`git remote`/`gh pr create --help` (see Break
      path 2 above) — no drift.
- [ ] [HUMAN] Manual spot-run of demo-5 or demo-7 end-to-end, logged.
      Awaiting human verification — `GEMINI_API_KEY` confirmed unset in this environment (checked
      presence-only, no value printed); SWE's claim is honest. Offline dispatch substitute
      verified above (Break path 1/happy path) is real signal but not a substitute for a live run.
- [x] PASS — `make ci` green — verified directly (see Test summary + evidence below); the two
      transient failures on one run were root-caused to pre-existing docker flakiness unrelated to
      this diff, not this task's regression.

**Evidence**
```
$ make pre-commit
======================= 1941 passed in 116.60s (0:01:56) =======================

$ uv run pytest tests/unit/decode/skills/test_demo_skills.py -v
22 passed in 3.48s

$ make ci   (final clean run)
=========================== short test summary info ============================
SKIPPED [1] tests/integration/test_observability_capstone.py:572: OPIK_API_KEY and GEMINI_API_KEY must both be set for the live Opik export smoke
SKIPPED [1] tests/integration/test_subagents_capstone.py:657: GEMINI_API_KEY is unset — the live Gemini fan-out smoke is skipped
================= 2054 passed, 2 skipped in 495.99s (0:08:15) ==================

$ git stash -u && uv run pytest tests/integration/test_docker_executor.py::test_timeout_kills_the_command_but_the_container_and_fs_survive tests/integration/test_sandbox_teardown.py::test_headless_bypass_flow_reaps_the_real_container_on_exit -v
2 passed in 23.12s   # confirms pre-existing on baseline, unrelated to this diff
$ git stash pop

$ gh pr create --help | grep -E "draft|repo|head|title|body"
  -b, --body string  -d, --draft  -H, --head branch  -t, --title string  -R, --repo [HOST/]OWNER/REPO
```

**Other issues found**
- The docker/sandbox integration suite is flaky under full-load contention (2 different failures
  across 3 full `make ci` runs, neither reproducing in isolation or on baseline) — not caused by
  this task, but worth a follow-up flakiness ticket for the integration suite since the task file
  explicitly names "full `make ci`" as a gate for future tasks too.
- No other issues. No `print()` calls added; no secrets; no unrelated files in the diff
  (`git status` shows exactly the 2 modified files + 3 new prompt-only `SKILL.md` dirs the task
  scoped).

**VERDICT: PASS**
