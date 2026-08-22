---
id: 146
feature: modal-remote-headless
status: done
---

# Docs: the new remote story — 07_infra rewrite, 08_evals_replays Modal-worker option, cross-refs

Tags: `docs`
Depends on: 141, 142, 143, 144, 145
Blocks: —

This task implements ADR-0020 §7. Docs describe only what shipped; every command shown must
have been executed by the tasks above.

## Scope

- **`running_the_code/07_infra.md` — rewrite around the Modal story:**
  - "The shape today" table gains two rows: **Modal Headless App** (`decode-headless`;
    `modal run` sync / `modal deploy` + `attempts` spawn; sandbox `none`/`modal` only, docker
    rejected) and **Modal-hosted Kitaru Worker** (`decode-kitaru-worker`; replays remote via
    agent v3). Modal's row updated: it hosts the harness again — but launch-vs-execute split,
    not a server (ADR-0020 §1).
  - New sections: **secrets setup** — the exact `modal secret create decode-headless …` and
    `modal secret create decode-kitaru-worker …` commands with key NAMES only (values never
    committed; note the deliberate `KITARU_AGENT_ID` asymmetry and why, ref 08 §7.3); **run &
    verify** — the sync run, the N-attempts run, the branch check, the worker replay check
    (reuse the executed AC commands from 142/143/145); **costs** — usage-based Modal only, the
    ~$16/month VM stays dead.
  - **Trim the stale GCP appendix** to a short retirement note (~15 lines): what the stack was,
    why it died (ADR-0019), where the full text lives (git history), and that its deleted
    surfaces (`deploy.sh`, `run-remote`, `demo-multiple-attempts.sh`, `flow.Dockerfile`,
    `remote` dep group) were removed by this feature. The traps-as-lessons prose goes; the
    pointer preserves it.
- **`running_the_code/08_evals_replays.md`:** §5 gains the Modal worker as the second way to
  run a worker (one paragraph + the two commands, agent `decode@3`, `none` sandbox note);
  §7.3's pitfall gains one sentence: the `decode-kitaru-worker` secret deliberately omits
  `KITARU_AGENT_ID`, and the Modal worker scrubs it defensively.
- **`running_the_code/02_modal_endpoints.md`:** one cross-ref line near the top — Modal also
  hosts decode's headless harness and Kitaru Worker, see 07_infra.md.
- **`AGENTS.md`:** update the Modal rows minimally — tech-stack table ("Sandbox / serving" row
  gains the headless harness + hosted worker mention) and the infra-access CLI list; nothing
  else.
- **Verify** `.env.example` needs no new keys (the feature added no `Settings` fields) and that
  glossary/ADR references (committed at grooming) match what shipped.

## Acceptance Criteria

- [x] 07_infra.md head table names both new Modal apps with their launch commands; every command block in the new sections is copy-paste runnable and was executed by a prior task's AC.
- [x] The GCP appendix is ≤ ~20 lines: retirement note + git-history pointer; `grep -n "deploy.sh\|run-remote\|KITARU_STACK" running_the_code/` returns hits only inside that note (historical prose), nowhere as live instructions.
- [x] Secret-creation commands show key names for both secrets; `KITARU_AGENT_ID` appears in `decode-headless` and explicitly NOT in `decode-kitaru-worker`, with the one-sentence why.
- [x] 08_evals_replays.md §5 offers laptop and Modal worker paths; §7.3 carries the secret-composition sentence.
- [x] 02_modal_endpoints.md carries the cross-ref; AGENTS.md Modal rows updated; `.env.example` unchanged (verified).
- [x] All intra-doc links resolve; `make ci` green.

## User Stories

### Story: New reader sets up the full remote story from 07_infra alone
1. Reader opens 07_infra.md, reads the shape-today table
2. Follows the secrets section: creates both Modal secrets from their `.env` values
3. Runs the sync headless command, then the worker deploy + one replay — every command works verbatim
4. Nothing in the doc tells them to provision GCP, run deploy.sh, or toggle Docker Desktop settings

### Story: Operator hits the 403 and the docs already name it
1. A replay hard-fails with `403: Task credentials are not accepted on this route`
2. Operator finds 08_evals_replays.md §7.3: the worker env must not carry `KITARU_AGENT_ID`, and the `decode-kitaru-worker` secret omits it by design
3. Operator fixes the secret; the replay claims and runs

## Out of scope

- The `manual-e2e-qa` skill and squid-plugin docs (separate surfaces, separate round).
- Re-teaching the retired GCP traps — git history is the archive.

---

Refs: ADR-0020 §7, tasks 141–145

## Log

### [Tester] 2026-08-22 15:00 — Follow-up noted during task 141 QA

While QA-ing task 141 (retire dead remote surface), confirmed two adjacent "Kitaru `exec_id`"
mentions the SWE flagged as out of that task's `src/`-scoped ACs: `evals/README.md:74` and
`evals/harness/online.py:5` both still describe the Opik thread key as "Kitaru `exec_id`" (the
concept `src/decode/observability/tracing.py`'s docstring was already fixed to call "the decode
session id" per ADR-0019 §1). Same one-line wording fix in both places when this task touches
docs/evals prose — not blocking task 141's PASS, just don't let it fall through the cracks.

### [SWE] 2026-08-22 21:30 — Implementation

**Files modified**
- `running_the_code/07_infra.md` — rewritten around the Modal story: head table (both new apps +
  their launch commands, the laptop Worker, Agent Versions v2/v3, Modal's updated row), §1 secrets
  (both `modal secret create` commands, key-name matrix, the `KITARU_AGENT_ID` asymmetry + the
  `ZENPROKEY_…` mint options A/B/C), §2 run & verify (sync run, mode table, docker rejection,
  `attempts` fan-out + branch check), §3 the Modal-hosted Kitaru Worker (v3 registration, deploy,
  detached start, the pending replay gate), §4 costs. GCP appendix trimmed 296 → 18 lines.
- `running_the_code/08_evals_replays.md` — §5 gains the Modal worker as the second way to run a
  Worker (two commands, `decode@3` pinned, `none` sandbox, claim scoping); §7.3 gains the
  secret-composition sentence; §5's opener no longer says a Worker is only "on your machine".
- `running_the_code/02_modal_endpoints.md` — one cross-ref callout under the snapshot banner.
- `AGENTS.md` — two Modal rows only: tech-stack "Sandbox / serving" + the infra-access CLI bullet.
- `scripts/modal_headless.py` — docstring bug task 145 flagged: the sync command needs `::main`
  (two local entrypoints). Docstring only, no behavior.
- `evals/README.md`, `evals/harness/online.py` — the 141-QA follow-up in this task's Log: the Opik
  thread key is "the decode session id", not "Kitaru `exec_id`" (that concept died with the flow).

**Tests**
- Unit: 2394 passing, 0 failing (`make unit-tests`, re-run after the last edits)
- Integration / full CI: `make ci` → 2506 passing, 0 failing, exit 0 (8:16)
- No test file changed: this is a docs task plus two prose/docstring one-liners. The `::main` claim
  is proven by execution instead (evidence 1 below), which is the only assertable thing in it.

**Acceptance criteria**
- [x] Head table names both apps with launch commands; every command block executed by a prior task
      — sourced from 142 (secret create, sync run, docker rejection, `git ls-remote`), 143 (deploy,
      `::attempts`, `--detach`), 144 (v3 registration, `agent version list`), 145 (worker deploy,
      `modal run --detach`, pre-flight line, `::main`). The only NOT-yet-executed commands are the
      `ZENPROKEY_…` mint and the three worker gates, each explicitly marked **⏳ Pending**.
- [x] Appendix = 18 lines (`awk '/^## Appendix/{f=1} f' … | wc -l`); the grep sweep returns exactly
      one hit, line 322, inside the note (evidence 3).
- [x] Both secret-creation commands show key NAMES with `"$VAR"` values; the asymmetry table marks
      `KITARU_AGENT_ID` ✅/❌ **never** with the 403 explanation and the 08 §7.3 link.
- [x] 08 §5 offers both Workers; §7.3 carries the sentence naming the secret's composition as the
      rule and the Function's scrub as the backstop.
- [x] 02_modal_endpoints cross-ref present; AGENTS.md two rows updated; `.env.example` unchanged
      (`git status --short .env.example` → empty; the feature added no `Settings` field).
- [x] Link/anchor sweep across all five changed docs resolves 100% (evidence 4); `make ci` green.

**Evidence**

1. The `::main` docstring bug is real, and the fixed form works verbatim (no Function container, no
   spend — the client-side docker guard fires first):

```
$ uv run modal run scripts/modal_headless.py --task "print uname" --sandbox-mode docker
Error: Specify a Modal Function or local entrypoint to run. E.g.
> modal run scripts/modal_headless.py::my_function [..args]
'scripts/modal_headless.py' has the following functions and local entrypoints:
main / app.main
attempts / app.attempts
run_task / app.run_task

$ uv run modal run scripts/modal_headless.py::main --task "print uname" --sandbox-mode docker
✓ Created objects. └── 🔨 Created function run_task.
Decode: sandbox mode 'docker' cannot run on Modal — a Modal container has no Docker daemon. Use
--sandbox-mode none (…) or --sandbox-mode modal (…).
Stopping app - uncaught exception raised locally: SystemExit(1).                        EXIT=1
```

2. The live state the doc describes (read-only checks):

```
$ uv run modal app list | grep -i "decode-head\|decode-kita"
ap-9N95tjRLSMo6xIYYlvBTTe  decode-head…  deployed
ap-nLFXHyKPdi8nnT6venIFtM  decode-kita…  deployed
$ uv run modal secret list | grep decode
decode-kitaru-work…  2026-08-22 17:53   |   decode-headless  2026-08-22 15:03
$ uv run kitaru agent version list decode
v4 id 01a029cb-…  /.uv/.venv/bin/decode run  /harness  {SANDBOX_MODE: none, DECODE_ENV: local}
v3 id 01a029bf-0ae3-7de1-b594-4bc71a7ba91a   (same spec — v4 is the QA-accident duplicate)
v2 id 01a02708-40f9-7030-9f43-f120b957e1b7   SANDBOX_MODE=docker + SANDBOX_REPO
```
   → the `--agent-version-id 01a029bf-…` printed in 07_infra §3 / 08 §5 IS v3, and the "pin
   `decode@3`, `latest_version` reads 4" sentence is factually true.

3. Sweeps:

```
$ grep -rn "deploy.sh\|run-remote\|KITARU_STACK" running_the_code/
running_the_code/07_infra.md:322:then deleted every surface it named: `make deploy` / `make run-remote`, …
$ awk '/^## Appendix/{f=1} f' running_the_code/07_infra.md | wc -l
      18
$ git status --short .env.example        # (empty)
```

4. Link + anchor sweep (every relative link in the five changed docs, target file AND `#anchor`
   against the target's real headings):

```
$ python3 <link-checker>   # ad-hoc, in the scratchpad
checked 5 files
all links + anchors resolve
```

5. Suites:

```
$ make format-fix && make lint-fix && make format-check && make lint-check
312 files left unchanged / All checks passed! / 312 files already formatted / All checks passed!
$ make pre-commit && make unit-tests
2394 passed in 43.13s / 2394 passed in 42.20s
$ make ci
======================= 2506 passed in 496.11s (0:08:16) =======================   exit 0
```

**Notes**
- **Two pending gates are documented AS pending, never as instructions.** 142's recording gate and
  145's three worker gates all close with the SAME control plane `ZENPROKEY_…` key, so 07_infra
  carries one ⏳ block in §1 (with the mint options: A scoped service-account key, recommended; B
  `/users/me/api_keys`; C the personal device credential, not recommended) and one in §3 (the three
  ready-to-run checks). Every other command on the page was executed by 141-145.
- **The `attempts` table numbers are 143's measured ones** (59 s for 3 attempts vs 72 s for 1, the
  three real session ids and branches) rather than invented ones — a reader comparing their own run
  against the doc is comparing against something that happened.
- **What I deliberately did NOT re-run:** the paid live proofs (a real `--sandbox-mode none|modal`
  run, the 3-attempt fan-out, the branch pushes). They are 142/143's `[HUMAN]` evidence, already
  Tester-verified; re-running them costs tokens, container minutes and leaves `decode/*` branches on
  origin for no new signal. The one live thing I did run is the free client-side guard (evidence 1),
  because it is what proves *this* task's `::main` fix.
- **Modal state I changed:** none. `modal run` left one stopped ephemeral app
  (`ap-sj0qkXggqK7mvJK99xtcSl`), the same zero-cost artifact every client-side-rejected invocation
  leaves; no secret, no deployment, no Kitaru object touched.
- **Adjacent, NOT touched (needs a rollup task):** `README.md:259` still lists "GCP — deploy the
  agent to run remotely — ~$16/month … see 07_infra.md" and `README.md:311` describes 07_infra as
  "Deploying the remote runtime to GCP and Modal". Both are stale since ADR-0019 (pre-existing, not
  a regression from this diff) and now contradict 07_infra's costs table. Out of this task's named
  scope; one-line fixes when someone owns the README.
- **Also out of scope, flagged by 145's Tester:** `scripts/modal_kitaru_worker.py::ensure_harness_home`
  lets an `OSError` escape as a raw traceback instead of a friendly `Decode:` line. I touched only
  the headless script's docstring, so I left it alone.
- Working tree also carries the long-standing unrelated edit to
  `tasks/done/138-docs-and-agents-md-alignment.md` (a stray PR-Reviewer log entry from an earlier
  pipeline, flagged by the Testers of 142/143/144/145). Not mine — do not sweep it into this commit.
- Nothing committed — awaiting Tester.

### [Tester] 2026-08-22 19:01 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 312 files unchanged, `ruff check` all
  passed, `make pre-commit` green)
- Unit tests: 2394 passed / 0 failed (`make unit-tests`, re-run independently)
- Integration tests: 112 passed / 0 failed (`make integration-tests`, re-run independently,
  440.70s) — combined 2506, matches SWE's `make ci` count exactly
- Warnings: 0 (`filterwarnings=["error"]` — a suite pass with warnings would show as failures)

**E2E adversarial pass** (docs-QA framing: "use the doc the way a new reader will, then try to
break it" — no live Modal/kitaru writes per orchestrator instruction)
- Happy path (new-reader read-through of 07_infra.md top to bottom): every command block traces
  to a prior task's executed AC evidence (142/143/144/145) or is explicitly marked `⏳ Pending`
  with a friendly-degrade explanation — no command presented as "just run this" that wasn't
  actually run. PASS.
- Break path 1 (stale cross-doc contradiction — a new reader's actual journey: README.md →
  07_infra.md): `README.md:259` still lists "GCP — deploy the agent to run remotely — ~$16/month
  … see 07_infra.md", and `README.md:311` describes 07_infra.md as "Deploying the remote runtime
  to GCP and Modal". Opening 07_infra.md from there, the reader finds no GCP story at all — its
  own costs table (§4) explicitly reads "the ~$16/month GCE VM + static IP — $0 — gone, and it
  stays gone." Expected: README's front door is consistent with the doc it points to. Actual:
  contradiction a reader hits on the very first click. FAIL (see Acceptance criteria below —
  this is not a named 146 AC, but the task's own framing, "docs describe only what shipped,"
  plus the orchestrator's explicit steer to decide rather than defer, makes this a required fix:
  the two lines are trivial and directly about this task's subject matter).
- Break path 2 (grep-sweep boundary — could a reader accidentally find "instructions" outside the
  retirement note): `grep -rn "deploy.sh\|run-remote\|KITARU_STACK" running_the_code/` → exactly
  one hit, `07_infra.md:322`, inside the appendix's retirement-note prose, never presented as a
  runnable command. PASS.
- Break path 3 (hostile input / secret-leak check across all 7 changed files): grepped for
  `ZENPROKEY_`, `KITKEY_`, and generic API-key shapes in every changed doc/script — zero matches;
  every credential in the diff is a `$VAR` reference or an explicit `<placeholder>`
  (`<ZENPROKEY_…>`, `<your zenml pro organization uuid>`). PASS.
- Break path 4 (link/anchor integrity — a reader clicking every relative link): wrote an
  independent link+anchor checker (github-slugger-style heading slugification, not simple
  whitespace-collapse) over the five changed docs (`07_infra.md`, `08_evals_replays.md`,
  `02_modal_endpoints.md`, `AGENTS.md`, `evals/README.md`) — all links and `#anchor`s resolve
  (my first slugifier implementation produced 5 false positives from a too-aggressive
  whitespace-collapse; fixed to match GitHub's actual non-collapsing algorithm, then 0 problems).
  PASS.

**Acceptance criteria**
- [x] PASS — 07_infra.md head table names both new Modal apps with launch commands; every command
      traceable to a prior task's AC — cross-checked every command block against
      `tasks/done/142-modal-headless-app-sync-run.md`, `143-modal-headless-spawn-attempts.md`,
      `144-register-agent-v3-sandbox-mode.md`, `145-modal-kitaru-worker-app.md` (secret create,
      sync run + gVisor uname output, docker rejection, `::attempts` fan-out + session
      ids/branches/timings, `register_kitaru_agent.py --sandbox-mode none`, worker deploy +
      `--agent-version-id 01a029bf-…`) — all match verbatim. The only not-yet-executed commands
      (the `ZENPROKEY_…` mint + the three worker gates) are explicitly `⏳ Pending`, never
      presented as done.
- [x] PASS — GCP appendix ≤ ~20 lines: `awk '/^## Appendix/{f=1} f' running_the_code/07_infra.md
      | wc -l` → 18. `grep -rn "deploy.sh\|run-remote\|KITARU_STACK" running_the_code/` → one hit,
      `07_infra.md:322`, inside the note.
- [x] PASS — both secret-creation commands show key NAMES only (`"$VAR"` values); asymmetry table
      marks `KITARU_AGENT_ID` ✅ `decode-headless` / ❌ **never** `decode-kitaru-worker`, with the
      one-sentence why (403 trap) and a link into 08 §7.3.
- [x] PASS — 08_evals_replays.md §5 offers both laptop and Modal worker paths (`git diff` shows
      the added paragraph + two commands, `decode@3` pinned, `none` sandbox noted); §7.3 gains
      the secret-composition sentence naming the Secret's omission as the rule and the Function's
      scrub as the backstop.
- [x] PASS — 02_modal_endpoints.md carries the cross-ref (verified in diff, callout under the
      snapshot banner); AGENTS.md carries exactly the two described row edits ("Sandbox / serving"
      + the infra-access CLI bullet, `git diff AGENTS.md` matches the SWE's description);
      `.env.example` unchanged (`git diff .env.example` empty, `git status --short .env.example`
      empty).
- [x] PASS — all intra-doc links resolve (independent checker, see break path 4); `make ci`
      equivalent green (`make pre-commit` + `make unit-tests` + `make integration-tests` run
      independently: 2394 + 112 = 2506 passed, 0 failed, 0 warnings).

**Evidence**

```
$ make format-check && make lint-check
312 files already formatted / All checks passed!
$ make pre-commit
============================ 2394 passed in 43.89s =============================
$ make unit-tests
============================ 2394 passed in 44.82s =============================
$ make integration-tests
======================= 112 passed in 440.70s (0:07:20) ========================
$ awk '/^## Appendix/{f=1} f' running_the_code/07_infra.md | wc -l
18
$ grep -rn "deploy.sh\|run-remote\|KITARU_STACK" running_the_code/
running_the_code/07_infra.md:322:then deleted every surface it named: `make deploy` / `make run-remote`, ...
$ git status --short .env.example
(empty)
$ ls scripts/deploy.sh scripts/demo-multiple-attempts.sh docker/flow.Dockerfile scripts/kitaru_bootstrap_api_key.py
ls: <all four> No such file or directory   # confirmed deleted, matches the appendix's claim
```

**Other issues found**
- `README.md:259` and `README.md:311` — stale GCP references, pre-existing since ADR-0019 but now
  actively contradicting 07_infra.md's shipped costs table; see break path 1. Two-line fix
  recommended (drop or reword the GCP cost row; reword the 07_infra.md table description to
  "Deploying decode's remote pieces on Modal").
- (Not blocking, already flagged by 145's Tester and correctly left alone by the SWE per scope)
  `scripts/modal_kitaru_worker.py::ensure_harness_home` lets an `OSError` escape as a raw
  traceback instead of a friendly `Decode:` line — still open, needs its own task.
- The working tree carries an unrelated diff to `tasks/done/138-docs-and-agents-md-alignment.md`
  (a stray PR-Reviewer log entry from an earlier pipeline round). Confirmed via `git log` that
  138 is already merged (`git log --oneline -- tasks/done/138…` shows it landed in commit
  `ce827f4`) — this local edit is uncommitted drift, not part of 146. SWE must stage files by
  name (not `git add -A`) when committing 146 to avoid sweeping it in.

**VERDICT: FAIL**

All 6 named acceptance criteria verified PASS with evidence, full suite green (2506/2506, 0
warnings), and 3 of 4 adversarial break paths clean. One break path fails: the README front door
now contradicts 07_infra.md's shipped costs table (break path 1). Given the task's own governing
principle ("docs describe only what shipped") and the orchestrator's explicit instruction to
decide rather than defer, this is a required fix, not a rollup note — it is a two-line edit.
SWE: fix `README.md:259` and `README.md:311`, then resubmit for re-review (I expect this to be a
fast re-pass; nothing else needs to change).

### [SWE] 2026-08-22 21:55 — Fixes (README front door vs shipped 07_infra)

**Files modified**
- `README.md` — three one-line edits, no other file touched:
  - `:159` (tech stack) — `**Deploying:** GCP & Modal` → `**Deploying:** Modal — headless agents +
    the Kitaru Worker run there, no servers to keep up`. Not in the Tester's two-line list, but the
    same stale-GCP contradiction one screen above it; leaving it would have failed the identical
    break path on the next read-through. Same class, same file, same subject — fixed, not deferred.
  - `:259` (cost table) — the `GCP — deploy the agent to run remotely … ~$16/month … $300 credits`
    row → `Modal — run headless agents + the Kitaru Worker remotely _(optional)_` /
    `usage-based, ~zero idle — see 07_infra.md`, matching 07_infra §4's costs table verbatim in
    substance ("usage-based only — you pay per run-second, nothing idle").
  - `:311` (guides table) — `Deploying the remote runtime to GCP and Modal` →
    `Remote headless agents + Kitaru Worker on Modal`, matching 07_infra's head table (the two apps
    it actually documents).

**Tests**
- Unit: 2394 passing, 0 failing (`make pre-commit`, which runs format-check + lint-check + the unit
  suite). No new/changed test: docs-only prose, nothing assertable in `src/`. Per the orchestrator's
  docs-only steer the full `make integration-tests` / `make ci` was not re-run — the previous round
  already recorded 2506/2506 green and this diff touches no importable code (`git diff --stat` →
  `README.md | 6 +++---`, nothing else staged for 146).

**Acceptance criteria** — unchanged, all still `[x]`; this round closes the Tester's break path 1
(README front door vs the doc it points to), which was not a named AC.

**Evidence**

1. The blocker is gone — no GCP claim survives anywhere in the README:

```
$ grep -rn "GCP" README.md
(no output)
```

2. Table formatting intact (the Tester's explicit constraint) — every row in both tables still pads
   to identical cell widths, so the pipes stay aligned in raw view:

```
$ python3 -c "…[len(p) for p in line.split('|')]…"
cost table   rows 252-259 → [0, 158, 114, 0] on every row   (row 259 is the new Modal row)
guides table rows 302-312 → [0,  69,  54, 0] on every row   (row 311 is the reworded 07_infra entry)
```

3. Read-the-doc-as-a-reader pass (the docs equivalent of the e2e step) — README §Cost Structure →
   click `07_infra.md` → land on §4 Costs. The two now agree: "usage-based, ~zero idle" vs
   "usage-based only — you pay per run-second, nothing idle" + "the ~$16/month GCE VM + static IP —
   $0 — gone, and it stays gone". Same for §Running the Code → 07_infra's head table (Modal Headless
   App + Modal-hosted Kitaru Worker). Every relative link in README still resolves:

```
$ python3 <relative-link-checker over README.md>
broken relative links: none
```

4. Suites:

```
$ make format-fix && make lint-fix && make format-check && make lint-check
312 files left unchanged / All checks passed! / 312 files already formatted / All checks passed!
$ make pre-commit
============================ 2394 passed in 42.73s =============================
```

**Notes**
- Scope discipline: I fixed the extra `:159` line because it is the *same* defect the Tester failed
  the task on (README asserting a retired GCP deploy story), not adjacent cleanup. Nothing else in
  the README was touched — the `$0 free tiers` framing still holds, since Modal's $30 credits row
  two lines up covers the optional remote runs.
- Still not mine, still uncommitted drift in the working tree: `tasks/done/138-…md`, plus the other
  files from this task's first round. Stage 146 by name (`README.md running_the_code/07_infra.md
  running_the_code/08_evals_replays.md running_the_code/02_modal_endpoints.md AGENTS.md
  scripts/modal_headless.py evals/README.md evals/harness/online.py tasks/146-…md`) — never
  `git add -A`.
- Still open, still needs its own task (unchanged from the last round):
  `scripts/modal_kitaru_worker.py::ensure_harness_home` raw-`OSError` traceback.
- Nothing committed — back to the Tester for re-review.

### [Tester] 2026-08-22 22:15 — Re-QA (round 2)

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 312 files unchanged, `ruff check` all
  passed, `make pre-commit` green)
- Unit tests: 2394 passed / 0 failed (`make pre-commit`, includes the unit suite)
- Integration tests: not re-run this round — no importable code changed (`git diff --stat` shows
  only `README.md` moved since round 1; every other file's diff size matches round 1 exactly, see
  evidence). Round 1 already recorded 2506/2506 green on the same non-README diff; re-running a
  7-minute integration suite for a 3-line markdown edit adds no new signal.
- Warnings: 0

**E2E adversarial pass — re-run of round-1 break path 1**
- Break path 1 (README → 07_infra.md reader walk, the exact failing path from round 1): opened
  README.md at the three edited lines (`:159` tech-stack, `:259` cost table, `:311` guides table),
  then followed the link into `running_the_code/07_infra.md` §4 Costs. README now reads "Modal —
  run headless agents + the Kitaru Worker remotely (optional) / usage-based, ~zero idle"; 07_infra
  §4 reads "usage-based only — you pay per run-second, nothing idle" and explicitly confirms "the
  ~$16/month GCE VM + static IP — $0 — gone, and it stays gone." The guides-table entry now reads
  "Remote headless agents + Kitaru Worker on Modal", matching 07_infra's actual head table (the two
  Modal apps it documents). No GCP claim survives anywhere in the front door. PASS (was FAIL in
  round 1).
- Break path 2 (grep sweep, confirms blocker fully gone): `grep -rn "GCP" README.md` → no output
  (exit 1). PASS.
- Break path 3 (table-integrity regression check — did the 3-line edit break markdown table
  rendering): computed per-cell character widths for every row of both edited tables
  (`README.md:252-259` cost table, `README.md:302-312` guides table) — every row in each table has
  identical column widths (`[0, 158, 114, 0]` and `[0, 69, 54, 0]` respectively), so the pipes stay
  aligned in raw view exactly as the SWE claimed. PASS.

**Acceptance criteria** — all 6 named ACs remain PASS (unchanged from round 1; spot-checked, not
re-derived from scratch since the only file that moved this round is README.md, which is not named
in any AC):
- [x] PASS — 07_infra.md head table + command provenance — `git diff --stat` shows
      `running_the_code/07_infra.md` diff size identical to round 1 (646 changed lines); file
      content unmodified this round.
- [x] PASS — GCP appendix ≤ ~20 lines — `awk '/^## Appendix/{f=1} f' running_the_code/07_infra.md
      | wc -l` → 18 (unchanged); `grep -rn "deploy.sh\|run-remote\|KITARU_STACK" running_the_code/`
      → exactly one hit, `07_infra.md:322`, inside the retirement note (unchanged).
- [x] PASS — secret-creation commands + `KITARU_AGENT_ID` asymmetry — file unchanged this round,
      previously verified in diff.
- [x] PASS — 08_evals_replays.md §5/§7.3 — file unchanged this round (`08_evals_replays.md` diff
      size identical to round 1), previously verified in diff.
- [x] PASS — 02_modal_endpoints.md cross-ref + AGENTS.md two rows + `.env.example` unchanged —
      `git status --short .env.example` empty; `AGENTS.md` and `02_modal_endpoints.md` diff sizes
      identical to round 1.
- [x] PASS — links resolve + suite green — `make pre-commit` → 2394 passed, 0 failed, 0 warnings;
      README's three edited links (`07_infra.md` appearing twice, self-consistent anchors) resolve
      to real files/headings.

**Evidence**

```
$ git diff --stat
 AGENTS.md                                      |   4 +-
 README.md                                      |   6 +-
 evals/README.md                                |   4 +-
 evals/harness/online.py                        |   2 +-
 running_the_code/02_modal_endpoints.md         |   4 +
 running_the_code/07_infra.md                   | 646 +++++++++----------------
 running_the_code/08_evals_replays.md           |  27 +-
 scripts/modal_headless.py                      |   5 +-
 ...
$ grep -rn "GCP" README.md
(no output, exit 1)
$ python3 -c "widths per row"
Cost table   rows 252-259 -> [0, 158, 114, 0] on every row
Guides table rows 302-312 -> [0,  69,  54, 0] on every row
$ make format-check && make lint-check
312 files already formatted / All checks passed!
$ make pre-commit
============================ 2394 passed in 43.48s =============================
```

**Other issues found**
- (Unchanged from round 1, not blocking) `scripts/modal_kitaru_worker.py::ensure_harness_home`
  lets an `OSError` escape as a raw traceback — needs its own follow-up task.
- (Unchanged from round 1, not blocking) `tasks/done/138-docs-and-agents-md-alignment.md` still
  carries the stray uncommitted PR-Reviewer log entry — confirmed pre-existing drift, not part of
  146. SWE must stage 146 by file name at commit time, not `git add -A`.

**VERDICT: PASS**

Round-1 blocker (README front door contradicting 07_infra's shipped Modal-only costs table) is
fixed by exactly the three claimed one-line edits (`README.md:159/259/311`); `git diff` confirms no
other README content drifted. `grep -rn "GCP" README.md` returns nothing. Both edited tables remain
pipe-aligned. All 6 named acceptance criteria still verified PASS. Full pre-commit suite green:
2394/2394, 0 warnings. Hand off to PA for acceptance review.
