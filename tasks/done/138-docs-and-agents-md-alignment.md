---
id: 138
feature: kitaru-replay-runtime
status: done
---

# Docs alignment: AGENTS.md, running_the_code, retire dead future task

Tags: `docs`
Depends on: 131, 133, 137
Blocks: —

This task implements ADR-0019 (docs are authored in the grooming commit; this task aligns the
OPERATIVE docs the agents and humans actually load — AGENTS.md and runbooks — with the shipped
reality).

## Scope

- **AGENTS.md** (surgical, remove-over-add):
  - Header + Tech-Stack "Durability" row: `kitaru[local,pydantic-ai,llm]` / "Durable headless
    flow … checkpoints + replay … ADR-0008/0009" → kitaru 0.22.2 replay-based model:
    `kitaru[cli,mcp,worker]` + `kitaru-pydantic-ai` adapter, recording seam, ADR-0019; note
    the `pydantic-ai-slim >=2.22,<2.23` pin rationale.
  - "Kitaru replay & what-if (operator surface)" section: rewrite onto sessions/replays/
    workers + the installed `kitaru-investigation` / `kitaru-replay-experiment` skills
    (the referenced `kitaru-replay-ops` skill is already deleted on this branch).
  - Invariants: update the "at DECODE_ENV=local decode never imports kitaru" phrasing to the
    tightened "no kitaru import unless recording is configured (or a worker task context)".
- **running_the_code/03_runtime.md**: rewrite — plain headless `decode run`, recording opt-in,
  worker replay + agent version 2 (137's reproducible sequence lives or is referenced here).
- **running_the_code/06_credentials.md**: bucket-on-new-client (132) + Kitaru replay secrets
  (`--secret-id`) distinction. **07_infra.md**: managed workspace + worker replaces the old
  local-server/deploy story (mark the GCP appendix stale rather than rewriting it).
- Delete `tasks/future/hitl-replay-answer-reuse.md` (describes dead wait/replay semantics).
- Sweep stale references: `grep -rn "checkpoint\|durable\|exec_id\|--hitl\|decode replay" AGENTS.md running_the_code/ .claude/` and fix hits that describe the dead model (the
  compaction "checkpoint JSONL line" is a DIFFERENT concept — leave it).

## Acceptance Criteria

- [x] AGENTS.md contains no reference to durable flows, checkpoints, waits, `decode replay`, `--hitl`, or `kitaru[local,pydantic-ai,llm]`; the replay section names sessions/replays/workers and the installed skills.
- [x] `running_the_code/03_runtime.md` walks a new operator from `decode run` → recorded session → worker baseline replay, matching 137's evidence.
- [x] `tasks/future/hitl-replay-answer-reuse.md` deleted.
- [x] The grep sweep returns no stale durable-model hits in AGENTS.md / running_the_code / .claude.
- [x] Glossary terms are used verbatim (Recording Seam, Kitaru Session, Kitaru Worker, Agent Version, Baseline Replay).
- [x] ADR-0019 carries the three dated implementation-reality amendments (input contract + no RunSpec input schema, `secret_ids: []`, Worker-Task gate widening).

## Out of scope

- ADR/glossary authoring (done in the grooming commit).
- README marketing copy; scripts/*.sh cleanup.

## Log

### [SWE] 2026-08-22 05:05 — Implementation

**Files modified**
- `docs/adr/0019-kitaru-replay-runtime.md` — NEW `## Amendments` section (dated, 3 items, Nygard-compatible):
  §4 input contract widened to the two recorded replay shapes + "RunSpec has no input-schema field";
  §4 secrets → version 2 ships `secret_ids: []` (Worker shell env, verified against kitaru
  `worker/process.py::build_process_env`); §3 gate widened so a Worker Task is always recording-configured.
- `AGENTS.md` — header, `runtime/` tree line, Tech-Stack row (`Durability` → **Recording / replay**:
  `kitaru[cli,mcp,worker]` + `kitaru-pydantic-ai`, the `>=2.22,<2.23` pin rationale), the Kitaru CLI bullet,
  Hand-back invariant (dropped the `--hitl` exception), the tightened no-kitaru-import invariant, ONE new
  Recording-Seam invariant, the e2e-skill surface list, and a rewritten "Kitaru replay & what-if" section
  (sessions / replays / workers / agent versions + the installed `kitaru-investigation`,
  `kitaru-replay-experiment`, `kitaru-importer-builder` skills; the deleted `kitaru-replay-ops` is gone).
- `running_the_code/03_runtime.md` — rewritten around the shipped reality: plain `decode run`, opt-in
  recording (the seam's two env vars, both failure modes), 137's worker-replay sequence kept verbatim plus a
  Baseline Replay paragraph, and a new Troubleshooting table (the dead macOS local-server section is gone —
  nothing listens on `127.0.0.1:8383` any more).
- `running_the_code/06_credentials.md` — bucket now described as riding the kitaru 0.22.2 **client** API
  against the managed workspace (132), with a new box separating it from a replay's version-attached
  `--secret-id` secrets; every `kitaru secrets …` CLI invocation replaced (0.22.2 ships no such command) —
  prerequisite is `kitaru status`, the read-back is the sync script's own diff answered `N`, deletion is the
  dashboard; the ADR-0015 §4 migration box, the mirror-semantics bullets and the missing-bucket failure line
  now quote the shipped text.
- `running_the_code/07_infra.md` — new head: "there is no server to deploy any more" + a table of what the
  remote picture actually is (managed workspace / Kitaru Worker / Agent Version / Modal / Opik). Everything
  from the old §1 down is now `## Appendix — the retired self-hosted GCP stack (stale)` behind a warning
  banner; the two references to the deleted `scripts/kitaru_bootstrap_api_key.py` are gone.
- `running_the_code/00_troubleshooting.md`, `01_install_and_usage.md`, `04_sandboxing.md` — three one-line
  cross-reference fixes ("durable runtime" → headless runtime; `gcloud` no longer a prerequisite; "move the
  whole agent to the cloud" → replay on a Kitaru Worker).
- `.agents/skills/manual-e2e-qa/SKILL.md` (the file `.claude/skills/manual-e2e-qa` symlinks to) — frontmatter
  surface list; the Opik headless sentence (`decode_run` only, Thread = session id); `decode run` row; the
  `--hitl` row REPLACED by a **Recording Seam** row (on / off / degrade / worker hard-fail A/Bs); the
  `--model` row; the `decode replay` row REPLACED by a **Worker replay (baseline)** row; and the sandboxed
  `decode run` row (host-side hand-back, no checkpoints).
- `tasks/future/hitl-replay-answer-reuse.md` — deleted (`git rm`); `tasks/future/` is now empty and gone. Its
  only live referrer was the `decode replay` row of the e2e skill, which this task replaced.

**Tests**
- Unit: 2256 passing, 0 failing — unchanged by this task (docs-only diff; no test targets docs).
- Integration: N/A — no source or infra change.

**Acceptance criteria**
- [x] AGENTS.md carries no dead-model reference — `grep -rn -i "checkpoint\|durable\|exec_id\|hitl\|decode replay\|kitaru\[local\|replay-ops" AGENTS.md` returns ONE line: the ADR-0015 invariant's historical "supersedes ADR-0008 §5" pointer (0008 is marked *Superseded by 0019*, so the pointer is honest history, not a live instruction).
- [x] `03_runtime.md` walks `decode run` → recorded session → worker baseline replay — verified live end-to-end (Evidence).
- [x] `tasks/future/hitl-replay-answer-reuse.md` deleted.
- [x] Sweep clean — remaining hits are (a) inside 07_infra's explicitly-stale appendix, (b) 01_install's "✅ Checkpoint" callouts (a different concept, like the compaction checkpoint line), (c) the sentence in 03_runtime that *says* durable HITL is dead.
- [x] Glossary verbatim — Recording Seam, Kitaru Session, Kitaru Worker, Worker Task, Agent Version, Baseline Replay, Cohort, Replay, Environment Bucket, Harness Home, Workspace, Hand-back, Model Override all used as written in `docs/glossary.md`.
- [x] ADR-0019 amendments — appended, dated, three items.

**Evidence**

```
$ make format-fix && make lint-fix && make format-check && make lint-check
307 files left unchanged · All checks passed! · 307 files already formatted · All checks passed!

$ make pre-commit          # includes its own pytest pass
============================ 2256 passed in 39.32s =============================
$ make unit-tests
============================ 2256 passed in 38.79s =============================

# --- e2e: every command the rewritten 03_runtime.md tells an operator to type -----------------
$ uv run kitaru status
https://f5ee9622-kitaru.cloudinfra.zenml.io authenticated live_workers=1

# recording OFF (the default) — the doc's byte-identical claim
$ uv run python -c "import sys, decode.cli; print('kitaru imported:', any(m.split('.')[0]=='kitaru' for m in sys.modules))"
kitaru imported: False

# recording ON — 03_runtime.md "Record runs as Kitaru Sessions"
$ export KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io
$ export KITARU_AGENT_ID=01a02523-1097-77e1-aa74-c64e7593050b
$ LLM_PROVIDER=gemini uv run decode run "say hi in exactly three words"
Hi there, developer.                                   # stdout: the answer alone; stderr EMPTY; exit 0
.decode/logs/decode.log:
  [kitaru] recording this run on https://f5ee9622-kitaru.cloudinfra.zenml.io
  (agent_id=01a02523-…, session_name=9e01a6b9-f5d9-46fc-9819-82b16e7ea27d)
$ uv run kitaru session get 01a0272f-a45d-7b52-94a2-6901a7a5c240
{"origin":"recorded","status":"completed","agent_id":"01a02523-…","name":"9e01a6b9-…",
 "inputs":"say hi in exactly three words","outputs":"Hi there, developer."}
# ← note `inputs` is a BARE STRING: ADR-0019 Amendment §1's shape, live.

# degrade — the doc's "ONE stderr line, still exits 0"
$ KITARU_API_URL=http://127.0.0.1:9 KITARU_AGENT_ID=01a02523-… LLM_PROVIDER=gemini uv run decode run "say hi in exactly three words"
stdout: Hi there, friend!          exit=0
stderr: [kitaru] not recording this run: http://127.0.0.1:9 is unavailable
        (ConnectError: All connection attempts failed); continuing on the bare agent

# the two guard lines quoted in "Run a task"
$ uv run decode run                      → Decode: decode run needs a TASK to run: … KITARU_TASK_INPUTS.   exit=1
$ RUNTIME_ENABLED=false uv run decode run "hi" → Decode: the headless runtime is disabled — …            exit=1

# the registration step, unchanged from 137 and still reproducible
$ uv run python scripts/register_kitaru_agent.py --dry-run
kitaru agent version register decode --command '…/.venv/bin/decode run' --working-dir
/Users/pauliusztin/.decode-kitaru-worker --env SANDBOX_MODE=docker --env SANDBOX_REPO=… --env
DECODE_ENV=local --timeout-seconds 1800 --description '…'
--dry-run: nothing was registered.

# --- link check: every relative markdown link in AGENTS.md / running_the_code / docs resolves --
$ python3 <walker over ./**/*.md, excluding tasks/>
checked; broken: 1   → .agents/skills/manual-e2e-qa/SKILL.md → tests/integration/test_milestone1_capstone.py
                       (PRE-EXISTING: repo-root-relative link, broken since the skill moved to
                        .agents/skills/; untouched by this task — see Notes)
```

**Notes**
- **A live LLM call was made** (two `decode run`s on Gemini, ~cents) because the recording claims in
  03_runtime.md are not provable offline. The second one left a real recorded session on the workspace
  (`01a0272f-a45d-7b52-94a2-6901a7a5c240`, agent `decode`, `origin: recorded`) — harmless, and useful as the
  Tester's read-only proof.
- **`.env`'s `LLM_PROVIDER=modal` endpoint is still 503** (137 hit the same). Every e2e above therefore ran
  with `LLM_PROVIDER=gemini`; nothing in this task depends on the provider.
- **Read-only surfaces I did NOT touch, for PA.** `docs/glossary.md` has two entries the amendments now
  contradict: **Thread (Opik)** still says the headless thread key is "Kitaru exec_id" (it is the run's
  session id — `RUN_SPAN_NAME`/`thread_id` in `runtime/headless.py`), and **Worker Task** states the input
  contract as `{"task","model"}` without the two recorded shapes. Both are PA-owned edits.
- **Out of scope, still stale (needs its own task):** `Makefile` keeps `deploy` and `run-remote` targets
  pointing at the retired stack (`run-remote` would now just launch a plain `DECODE_ENV=prod` headless run),
  and `scripts/deploy.sh:333` still calls the deleted `kitaru_bootstrap_api_key.py`. The task's Out-of-scope
  names `scripts/*.sh`; the Makefile targets are the same cleanup and belong with them.
- **Pre-existing broken link** in the e2e skill (`tests/integration/test_milestone1_capstone.py`, root-relative
  since the skill moved under `.agents/skills/`) left alone deliberately — unrelated to the dead-model sweep.
- 07_infra.md was marked stale, not rewritten, per the task's own instruction; its `kitaru executions` /
  `exec_id` / checkpoint prose is deliberately preserved as history behind the appendix banner.

### [Tester] 2026-08-22 02:03 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` → 307 files already formatted; `ruff check` →
  All checks passed; `make pre-commit` → 2256 passed)
- Unit tests: 2256 passed / 0 failed (`make pre-commit` and `make unit-tests`, both re-run independently)
- Integration tests: N/A — docs-only diff, no integration targets touch these files
- Warnings: 0 (`filterwarnings=["error"]` project-wide; no pytest warning lines emitted)

**E2E adversarial pass**
- Happy path: read-only `uv run kitaru status` → workspace `f5ee9622-…zenml.io`, `authenticated`,
  `live_worker_count:1` — matches 03_runtime.md's claim verbatim (PASS)
- Happy path 2: `uv run kitaru session get 01a0272f-a45d-7b52-94a2-6901a7a5c240` (the session the SWE's
  live recorded run left on the workspace) → `"origin":"recorded"`, `"inputs":"say hi in exactly three
  words"` — a **bare string**, confirming ADR-0019 Amendment §1's shape is live, not aspirational (PASS)
- Break path 1 (missing required input): `uv run decode run` (no TASK arg, no `KITARU_TASK_INPUTS`) →
  `Decode: decode run needs a TASK to run: … KITARU_TASK_INPUTS.` exit=1, no traceback — matches
  03_runtime.md §"Run a task" and the task's own AC evidence exactly (PASS)
- Break path 2 (feature-flag-off state): `RUNTIME_ENABLED=false uv run decode run "hi"` →
  `Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true …` exit=1, no traceback (PASS)
- Break path 3 (link-rot sweep — malformed/stale cross-references): wrote a standalone markdown-link
  walker over every `*.md` outside `tasks/`, resolving relative links against each file's own directory.
  Found only ONE real broken link — `.agents/skills/manual-e2e-qa/SKILL.md` →
  `tests/integration/test_milestone1_capstone.py` (root-relative, pre-existing since the skill moved
  under `.agents/skills/`, explicitly called out by the SWE as untouched/out-of-scope) — no new breakage
  introduced by this diff (PASS). Two other hits (`kitaru-investigation/SKILL.md`,
  `kitaru-guided-tour/SKILL.md` → `RESOLVED_URL`) are template placeholders in unrelated pre-existing
  skills, not real links — false positives, confirmed by inspection.
- Break path 4 (grep-sweep evasion via symlinks): re-ran the mandated sweep
  (`grep -rn -i "checkpoint|durable|exec_id|hitl|decode replay|kitaru\[local"` over `AGENTS.md
  running_the_code/ .claude/ docs/glossary.md`) — zero hits, confirming AC4. Then deliberately
  dereferenced through the `.claude/skills/*` symlinks into `.agents/skills/kitaru-*` to see if the
  sweep was hiding stale content behind a symlink boundary: found many "durable"/"checkpoint" hits, but
  all inside pre-existing, task-138-untouched upstream Kitaru-authoring skills
  (`kitaru-investigation`, `kitaru-replay-experiment`, `kitaru-importer-builder`, `kitaru-guided-tour`,
  `kitaru-adapter-builder`) using "durable"/"checkpoint" in the generic sense of *persisted Kitaru
  server state*, not decode's deleted durable-execution-engine concept — same distinction the task
  itself draws for the compaction "checkpoint JSONL line." Not a regression from this diff (PASS with
  note — worth a glossary/style pass eventually, but not a task-138 defect).

**Acceptance criteria**
- [x] PASS — AGENTS.md contains no reference to durable flows, checkpoints, waits, `decode replay`,
      `--hitl`, or `kitaru[local,pydantic-ai,llm]`; replay section names sessions/replays/workers +
      installed skills — Evidence: `grep -rn -i "checkpoint\|durable\|exec_id\|hitl\|decode replay\|kitaru\[local" AGENTS.md`
      → zero hits; AGENTS.md:135-142 "Kitaru replay & what-if" section names Session/Replay/Worker/Agent
      Version and `kitaru-investigation`/`kitaru-replay-experiment`/`kitaru-importer-builder` (the
      deleted `kitaru-replay-ops` is gone, confirmed absent from `.claude/skills/` listing)
- [x] PASS — `running_the_code/03_runtime.md` walks `decode run` → recorded session → worker baseline
      replay, matching 137's evidence — Evidence: file read end-to-end (§"Run a task" →
      §"Record runs as Kitaru Sessions" → §"Replay a recorded session on a Kitaru Worker"); live
      `uv run kitaru status` + `kitaru session get <id>` reproduce the doc's exact claims;
      `uv run python scripts/register_kitaru_agent.py --dry-run` output byte-matches the doc's step 1
- [x] PASS — `tasks/future/hitl-replay-answer-reuse.md` deleted — Evidence: `git status` shows it staged
      deleted; `ls tasks/future` → no such directory; repo-wide grep for
      `hitl-replay-answer-reuse` finds only the ADR history mention (0010, superseded) and this task's
      own scope text — no live doc referrer left
- [x] PASS — grep sweep returns no stale durable-model hits in AGENTS.md / running_the_code / .claude —
      Evidence: sweep re-run independently, zero hits; remaining doc mentions of "checkpoint"/"durable"
      confirmed to be (a) 07_infra.md's explicitly-stale appendix (banner at line 32), (b) 01_install's
      unrelated `✅ Checkpoint` callout convention, (c) 03_runtime.md's sentence stating durable HITL
      *died* — none are live instructions
- [x] PASS — glossary terms used verbatim (Recording Seam, Kitaru Session, Kitaru Worker, Agent Version,
      Baseline Replay) — Evidence: all five terms appear capitalized/hyphenated identically to
      `docs/glossary.md` in AGENTS.md and 03_runtime.md; spot-checked against glossary.md lines 53-60
- [x] PASS — ADR-0019 carries the three dated implementation-reality amendments (input contract, no
      RunSpec input-schema, `secret_ids: []`, Worker-Task gate widening) — Evidence:
      `docs/adr/0019-kitaru-replay-runtime.md` new `## Amendments` section, dated `2026-08-22`, three
      numbered items matching §4/§4/§3 exactly, placed Nygard-compatibly before `## Consequences`

**Evidence**
```
$ make format-check && make lint-check
uv run ruff format --check
307 files already formatted
uv run ruff check
All checks passed!

$ make pre-commit
============================ 2256 passed in 39.xx s =============================

$ uv run decode run
Decode: decode run needs a TASK to run: pass it as an argument (decode run "<task>"), or launch it as a
Kitaru Worker Task, which supplies it in KITARU_TASK_INPUTS.
exit=1

$ RUNTIME_ENABLED=false uv run decode run "hi"
Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true in your environment or .env to use
`decode run` (see .env.example).
exit=1

$ uv run kitaru session get 01a0272f-a45d-7b52-94a2-6901a7a5c240
{"origin":"recorded","status":"completed",...,"inputs":"say hi in exactly three words",
 "outputs":"Hi there, developer."}
```

**Other issues found**
- `docs/glossary.md` **Thread (Opik)** entry (line 72) still says the headless thread key is "Kitaru
  exec_id"; reality is the run's session id (confirmed against `runtime/headless.py` and 03_runtime.md's
  own rewritten text, which correctly says "Thread = the run's session id"). **Task 138's own Out of
  scope line explicitly excludes "ADR/glossary authoring (done in the grooming commit)"** — so this is
  correctly NOT part of this task's diff, and the SWE correctly flagged it read-only for PA rather than
  reaching outside scope to fix it. Not a task-138 FAIL. Recommend a follow-up task/PA fix before this
  drifts further.
- `docs/glossary.md` **Worker Task** entry (line 56) states the input contract as only
  `{"task", "model"}`, omitting the two additional recorded-replay shapes now codified in ADR-0019
  Amendments §1 (bare-string prompt, `{"input": …}`). Same scope reasoning as above — flag for the same
  follow-up, not a task-138 FAIL.
- New (not previously flagged): `docs/glossary.md` **Observability (Opik tracing)** entry (line 73)
  still says "Distinct from the JSONL session log and **Kitaru Checkpoints**" — Kitaru Checkpoints are
  a dead concept post-ADR-0019 (kitaru 0.22.2 removed `checkpoint` entirely, per the ADR's own Context
  section). Same out-of-scope reasoning; bundle into the same follow-up glossary-fix task.
- Out of scope, confirmed present and worth a follow-up task (per the SWE's own note): `Makefile`
  `deploy` / `run-remote` targets and `scripts/deploy.sh:333` still reference the retired stack /
  deleted `kitaru_bootstrap_api_key.py`. Verified these still exist un-touched in this diff — correctly
  left alone per the task's Out-of-scope line naming `scripts/*.sh`.
- Pre-existing broken link in `.agents/skills/manual-e2e-qa/SKILL.md` (root-relative
  `tests/integration/test_milestone1_capstone.py`, broken since the skill moved under `.agents/skills/`)
  confirmed still broken and correctly left alone — unrelated to this task's dead-model sweep.

**VERDICT: PASS**

### [PA] 2026-08-22 06:00 — Acceptance Review (feature kitaru-replay-runtime, tasks 131-138, PR #65)

**VERDICT: ACCEPT**

Walked the whole feature from the operator's perspective against the Tasks Plan ACs and
ADR-0019 **including its dated Amendments section** (which already records the input-contract
widening, `secret_ids: []`, and the Worker-gate widening — the PR body's "Follow-ups" claim
that the ADR still needs a PA amendment is STALE and should be edited before merge).

**Feature gates, all verified from evidence in the task logs plus direct re-reads:**
- (a) `make ci` green — 133 SWE evidence + Tester's independent true-key-free repro (2261 passed, exit 0).
- (b) headless run recorded — 138's live e2e left session `01a0272f-a45d…` (`origin: recorded`, bare-string inputs = Amendment §1 live), read back by the Tester.
- (c) REPL recording + graceful degrade — 135's live degrade + real post-degrade Gemini turn; wrap/session_name/multi-turn unit-pinned. Residual: the [HUMAN] live REPL-turn-on-workspace checkbox stays open — same seam and adapter surface the live headless/replay proofs exercised, acceptable residual for a human to tick opportunistically.
- (d) worker baseline replay executed — 137: replay `01a0270b-0c96…` completed, result session `origin: replay` on agent version 2, all 29 tool calls served from history, host tree untouched; Tester re-read all ids read-only.

**Operator experience spot-checked directly:** `running_the_code/03_runtime.md` walks
run → record → replay → troubleshoot with glossary-verbatim terms and honest failure lines;
every guard/degrade/hard-fail message reads as one actionable `Decode:`/`[kitaru]` line
(after 134's fix round, which the Tester re-proved live).

**Carried items, dispositioned:**
1. Glossary drift — FIXED by PA in this review (my surface): **Worker Task** (three input shapes per Amendment §1), **Thread (Opik)** (dead `exec_id` → per-run session id), **Observability** (dead "Kitaru Checkpoints" → Kitaru Session recording), plus **Recording Seam** (gate widening per Amendment §3). Fold into the branch before merge.
2. Makefile `deploy`/`run-remote` + `scripts/deploy.sh` (live trap: line 333 calls the deleted bootstrap script) + the last `exec_id` docstring in `src/decode/observability/tracing.py:147` — follow-up task filed: `tasks/140-retire-dead-remote-stack-surface.md` (outside this feature's gate; plan declared `scripts/*.sh` out of scope).
3. Worker-mode lazy session creation escaping the Seam's one-line contract — follow-up task groomed and filed: `tasks/139-worker-lazy-session-failure-one-line.md` (design decision taken: worker-gated catch at the CLI boundary; no new ADR — implements ADR-0019 §3 as amended).
4. `test_live_gemini_fanout_smoke` flake — no action; Tester's ruling stands (live-key-only, never gates CI, strictness is deliberate).

All acceptance criteria verified from user POV. Hand off to the PR Reviewer.

### [PR Reviewer] 2026-08-22 — Review (feature rollup, tasks 131-138 + onboarding commits)

**VERDICT: NO BLOCKERS**

Reviewed the full PR #65 diff vs merge-base `c2218f7`: 122 files, ~12,100 insertions / ~6,200 deletions. Walked all dimensions (performance / clean code / untested / standards / documentation discipline / simplicity).

- Blockers: 0
- Nits: 4 — appended to the PR #65 description and posted as a caveman-review comment:
  1. [Clean code] `scripts/demo-multiple-attempts.sh:194` echoes a `decode replay` hint for the deleted command — widen task 140's sweep to cover the line (140 currently names only the file's comments).
  2. [Clean code] `kitaru_plan.md:3` still reads "not yet implemented" — delete or archive under `tasks/done/`; ADR-0019 + tasks 131-138 are the record.
  3. [Clean code] list-by-name→get-with-values two-step duplicated in `settings.py::_read_bucket` and `sync_secrets.py::find_secret` — cross-reference or share a helper.
  4. [Doc polish] ADR-0019 Context "decode is on 2.33" reads stale post-downgrade — reword to past tense.

Docs discipline verified: ADR-0019 (+ dated Amendments) Accepted; ADR-0008/0010 marked Superseded; glossary carries every new domain noun (Recording Seam, Kitaru Session, Kitaru Worker, Worker Task, Agent Version, Cohort, Baseline Replay). Follow-ups 139/140 pre-filed at PA acceptance were not re-raised. Pipeline may advance to hand-off.
