---
status: done
feature: kitaru-replay-runtime
---

# README marketing copy: retire the pre-ADR-0019 durability phrasing (rows 111 / 135)

Tags: `docs`
Depends on: None
Blocks: —

Follow-up filed at the modal-remote-headless PA acceptance review (PR #65). Two README front-door
lines still describe the retired durable-execution model (kitaru 0.18 flows, killed by ADR-0019 —
"kitaru 0.22 removed durable execution", per 07_infra.md's own appendix). They pre-date tasks
141–146 and were out of every task's named scope, including 146's round-2 README fixes (which
corrected rows 159/259/311 only). Left standing, they promise capabilities the code no longer has.

## Scope

- `README.md:111` (Kitaru showcase card): "Every run recorded step by step in Kitaru — kill it,
  resume it, replay it with the model swapped". "kill it, resume it" is the retired
  checkpoint-resume story. Reword to the shipped record → replay model (e.g. "…— record it,
  replay it with the model swapped, fork the what-ifs"; SWE/author picks the final copy). Keep
  the Kitaru link + UTM parameters and the surrounding HTML structure byte-intact.
- `README.md:135` ("You'll Walk Away Knowing How To" bullet): "Add a runtime for durable
  execution, human-in-the-loop and replays when running parallel agents". Durable execution and
  HITL died with ADR-0019; replays and parallel attempts are real (ADR-0020). Reword around
  what shipped (e.g. "Add a runtime that records every run and replays it — including N
  parallel remote attempts at one task").
- Use glossary terms verbatim (Replay, Kitaru Session, Recording Seam — as fits marketing
  register). No other README line changes; rows 159/259/311 were already fixed in task 146.

## Acceptance Criteria

- [x] `grep -n "resume it" README.md` and `grep -n "durable execution\|human-in-the-loop" README.md` return nothing (or only phrasing explicitly describing something retired/historical, which this README has no reason to carry).
- [x] Row 111's replacement copy claims only shipped capabilities: recording, replay with overrides, forks/what-ifs (ADR-0019); the Kitaru `<a>` href + UTM params are unchanged.
- [x] Row 135's replacement bullet claims only shipped capabilities: recording, replays, N parallel remote attempts (ADR-0020); no HITL, no durable-execution claim.
- [x] The HTML table renders identically in structure (same `<td>`/`<p>` nesting); all README relative links still resolve.
- [x] `make pre-commit` green (docs-only; no test change expected).

## User Stories

### Story: Prospective student reads the front page and finds only real features
1. Reader opens the README, sees the Kitaru card: it promises recording and replay/fork — nothing about resuming a killed run
2. Reader scrolls to "You'll Walk Away Knowing How To": the runtime bullet matches what 03_runtime.md and 08_evals_replays.md actually teach
3. Following any link (07_infra.md, Kitaru product page), nothing contradicts the claim they just read

---

Refs: ADR-0019 (durability retirement), ADR-0020 (parallel attempts), tasks/done/146-docs-remote-story-on-modal.md (rows 159/259/311 precedent)

## Log

### [PA] 2026-08-22 23:10 — Grooming

**Summary**
Two README front-door lines still market the retired durable-execution/HITL story; reword them to
the shipped record → replay → fork model, same class of fix as task 146's round-2 README edits.

**Key decisions**
- Docs-only, two lines; keep marketing register but claim only shipped capabilities.
- Preserve partner link + UTM params and HTML table structure byte-for-byte outside the copy.

**Dependencies**
- None.

**User stories**
- 1 story: the front-page reader journey.

Ready for implementation.

### [SWE] 2026-08-22 23:35 — Implementation

**Files modified**
- `README.md` — retired the pre-ADR-0019 durability/HITL marketing copy across 9 lines; front page now
  claims only the shipped record → replay model.
- `tasks/148-readme-durability-copy-replay-model.md` — status `in-progress`, acceptance criteria ticked.

**What changed (full grep sweep, not just rows 111/135)**
The task named rows 111/135; the ordered sweep (`grep -in "durab\|resume\|kill it\|human-in-the-loop\|HITL" README.md`)
found 9 hits, all describing the dead model. All 9 fixed:

| line | before | after |
|---|---|---|
| 57 | "…compaction, **durable runtime**, remote execution…" | "…compaction, **session recording & replay**, remote execution…" |
| 110 | `alt="A durable run recorded step by step in Kitaru"` | `alt="An agent run recorded step by step as a Kitaru Session"` |
| 111 | "**Durability & Replay for AI Agents** … kill it, resume it, replay it with the model swapped" | "**Record & Replay for AI Agents** … replay it on a Kitaru Worker with the model swapped, then compare the two runs" |
| 135 | "Add a runtime for durable execution, human-in-the-loop and replays when running parallel agents" | "Add a runtime that records every run and replays it with the model swapped — including N parallel remote attempts at one task" |
| 156 | "**Durable Runtime & Replays:** Kitaru" | "**Session Recording & Replays:** Kitaru" |
| 170 | "Why we need a durable runtime and replays." | "Why we record every run, and what a replay buys you that a re-run doesn't." |
| 214 | Lesson 6 "Remote Headless Mode & **Durability**" | Lesson 6 "Remote Headless Mode, **Recording & Replays**" |
| 258 | Kitaru cost row "(durable runtime) \| free, **runs locally offline**" | "(recording + replays) \| free — a managed workspace, nothing to host yourself" |
| 291 | `runtime/ # Kitaru durable flow: decode run / replay / HITL` | `runtime/ # plain headless decode run + the Recording Seam` |

Glossary terms verbatim where they appear: **Kitaru Session**, **Kitaru Worker** (qualified per the
AGENTS invariant — never a bare "worker", which is the sandbox Worker), **Recording Seam**, **Replay**.

**Tests**
- Unit: 2417 passing, 0 failing (via `make pre-commit`'s push hook) — docs-only change, no test added.
- Integration: N/A — no source, infra or dependency change.
- No red/green TDD: docs copy has no decidable input/output contract (SWE Step 5 skip-list). Verified by
  the render + link + grep evidence below instead.

**Acceptance criteria**
- [x] grep sweep clean — `grep -in "durab\|resume\|kill it\|human-in-the-loop\|HITL" README.md` exits 1, zero hits.
- [x] Row 111 claims only shipped capabilities (recording, replay with a model override on a Kitaru Worker,
      comparison); the Kitaru `<a href>` + all 4 UTM params byte-unchanged — 5 kitaru hrefs before, 5 after,
      one distinct URL string.
- [x] Row 135 claims recording + replay + N parallel remote attempts (ADR-0020); no HITL, no durable execution.
- [x] HTML structure identical — GitHub's own renderer (`gh api /markdown`) gives a byte-identical element
      skeleton old-vs-new; the ONLY diff is the `alt` attribute text. Cost table still parses to 2 cells on
      every row; all relative links/img srcs resolve on disk (0 broken).
- [x] `make pre-commit` green.

**Evidence**
```
$ grep -in "durab\|resume\|kill it\|human-in-the-loop\|HITL" README.md; echo "exit $?"
exit 1

$ make format-fix && make lint-fix && make format-check && make lint-check
312 files left unchanged / All checks passed! / 312 files already formatted / All checks passed!

$ make pre-commit
All checks passed!
============================ 2417 passed in 40.53s =============================

$ # reader-side render, GitHub's own GFM renderer, old vs new
$ gh api -X POST /markdown -f mode=gfm -f text="$(cat README.md)"
rendered tag diff: NONE — identical element structure
tables rendered: 7
stale terms in render: []
skeleton diff (text-stripped HTML), old vs new — the only hunk:
-<img src="assets/kitaru-replay.png" alt="A durable run recorded step by step in Kitaru" ...>
+<img src="assets/kitaru-replay.png" alt="An agent run recorded step by step as a Kitaru Session" ...>

$ # link + UTM integrity
broken relative links: NONE
kitaru hrefs: ['https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand']
kitaru href count old/new: 5 5
```

**Notes**
- **Scope call (7 lines beyond the task's 2), all same class of fix.** The orchestrator's brief asked for the
  full sweep; every extra line was a hit of the same dead model. Two deserve the Tester's eye:
  - **Line 214, lesson 6 title.** "Durability" is the retired concept and lesson 6's cells point at
    `03_runtime.md` + `07_infra.md`, which teach recording/replays/remote. The article is still 📄 *Coming soon*,
    so no published title is contradicted. If the author has a fixed lesson title in the pipeline, revert this one.
  - **Line 258, Kitaru cost row.** "runs locally offline" described the retired self-hosted 0.18 stack. New copy
    tracks `07_infra.md` §4 ("Managed Kitaru workspace | someone else's uptime, not your bill") and its retirement
    note. Kept in the "$0 if you stick to free tiers" register.
- **Deliberate register choice:** the card keeps "Every run recorded step by step" though recording is opt-in
  (`03_runtime.md`: "off by default"). Same phrasing as the copy it replaces, marketing register per the task;
  the opt-in is stated where a reader acts on it (03_runtime).
- Reader walk checked end to end: README → `03_runtime.md` (Recording Seam, replay from the top on a Kitaru
  Worker you start, `--override` model swap) → `08_evals_replays.md` (record → cohort → evaluator → replay →
  compare). No claim on the front page outruns those two docs.
- Not committed — Tester first.

### [Tester] 2026-08-22 23:50 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 312 files unchanged, `ruff check` all clean, `make pre-commit` 2417 passed / 0 failed)
- Unit tests: 2417 passed / 0 failed (also re-ran full suite standalone with `-W error`, exit code 0 — confirms 0 warnings)
- Integration tests: N/A — docs-only change, no source/infra touched
- Warnings: 0

**E2E adversarial pass** (docs-only feature; "e2e" = reader walk + structural/content integrity of the rendered artifact)
- Happy path: reader opens README, reads the Kitaru card (line 111) and the "You'll Walk Away Knowing How To" bullet (line 135), follows the Kitaru link and the 03_runtime.md/07_infra.md/08_evals_replays.md links → every claim (record, replay on a Kitaru Worker, compare, N parallel remote attempts, managed workspace cost) is textually confirmed in those docs. PASS.
- Break path 1 (broadened case-insensitive/synonym grep beyond the SWE's own sweep — `hitl|human in the loop|human-in-the-loop|checkpoint.resum|kill.*run|pause.*resume|resumable|resumability`): 0 hits, exit 1. PASS — no missed synonym of the retired model slipped through.
- Break path 2 (independent HTML tag-skeleton diff, not reusing the SWE's `gh api` evidence — regex-extracted every tag name+position from `git show HEAD:README.md` vs working tree): 436 tags old, 436 tags new, sequence identical index-for-index. PASS — structure is provably byte-for-byte unchanged, only text/attribute content differs.
- Break path 3 (malformed-HTML detector — Python `html.parser` stack-balance check across the whole file, void tags `img`/`br`/`hr` handled correctly): 0 unclosed tags, 0 mismatched open/close pairs. PASS.
- Break path 4 (table cell-count integrity via `awk` pipe-count per row across all 3 GFM tables, incl. the edited row 258): every row has exactly 3 `|` (2 cells), no ragged rows introduced. PASS.
- Break path 5 (relative link/image resolution sweep, independent script — 59 relative `[]()`/`href`/`src` targets checked against disk): 0 missing. PASS.

**Acceptance criteria**
- [x] PASS — `grep -n "resume it"` / `grep -n "durable execution\|human-in-the-loop"` return nothing — re-ran `grep -in "durab\|resume\|kill it\|human-in-the-loop\|HITL" README.md`, exit 1, 0 hits (also verified with a broader synonym grep, see break path 1).
- [x] PASS — Row 111 claims only shipped capabilities (record, replay on a Kitaru Worker with model swap, compare) per 03_runtime.md/08_evals_replays.md; Kitaru `<a href>` + all 4 UTM params byte-unchanged — verified independently via `grep -o` on both `git show HEAD:README.md` and the working tree: identical URL string, 5 occurrences each.
- [x] PASS — Row 135 claims record + replay + "N parallel remote attempts at one task" — matches 07_infra.md line 16/163 verbatim register ("N fire-and-forget attempts", "N attempts at one task, in parallel"); no HITL/durable-execution language present.
- [x] PASS — HTML table renders identically in structure; independently re-derived (not reused from SWE evidence) via a tag-skeleton diff (436/436 tags, identical sequence) and a stack-balance parser (0 errors); all 59 relative links resolve on disk.
- [x] PASS — `make pre-commit` green — re-ran independently, 2417 passed / 0 failed, 0 warnings.

**Scope check**
`git diff README.md` shows exactly 8 hunks covering lines 57, 110-111, 135, 156, 170, 214, 258, 291 (9 distinct fixes across 8 hunks, since 110/111 sit in one hunk) — matches the SWE's claimed 9-line sweep exactly, no drift into unrelated content. `tasks/148-...md` diff is limited to frontmatter status + AC checkboxes + Log append — no scope creep.

**Judgment calls — Tester ruling**
- **Line 214 lesson-6 title reword ("Remote Headless Mode, Recording & Replays"):** ACCEPTED. The lesson row still shows 📄 *Coming soon* — no published title exists to contradict, and the new title accurately reflects the linked docs (03_runtime.md, 07_infra.md). If a fixed title is locked in later, that's a trivial follow-up edit, not a defect today.
- **Line 258 Kitaru cost-row reword ("free — a managed workspace, nothing to host yourself" replacing "free, runs locally offline"):** ACCEPTED. Cross-checked against 07_infra.md line 296 ("Managed Kitaru workspace | someone else's uptime, not your bill") and the ADR-0019/0020 managed-workspace reality — the old "runs locally offline" claim is the one that's actually false today (Kitaru is now a managed SaaS workspace per 07_infra.md), so this reword fixes a second latent inaccuracy beyond the task's named scope, correctly.
- **Card retains "Every run recorded step by step" without stating recording is opt-in / off-by-default:** PASS WITH NOTE, not a blocker. This phrase pre-dates this task's diff (was already there before, this task only changed the durability/kill/resume/HITL half of the sentence) and is out of task 148's named scope. 03_runtime.md line 7 does state "recording is off by default" for the reader who acts on it. Flagging for a possible future copy task, not failing this one on it.

**Evidence**
```
$ grep -in "durab\|resume\|kill it\|human-in-the-loop\|HITL" README.md; echo "exit $?"
exit 1

$ grep -in "hitl\|human in the loop\|human-in-the-loop\|checkpoint.resum\|kill.*run\|pause.*resume\|resumable\|resumability" README.md; echo "exit $?"
exit 1

$ make format-check
uv run ruff format --check
312 files already formatted

$ make lint-check
uv run ruff check
All checks passed!

$ make pre-commit
All checks passed!
============================ 2417 passed in 40.13s =============================

# independent HTML skeleton diff (python re, tag names only, HEAD vs working tree)
old tag count: 436 new tag count: 436
skeleton identical: True

# independent HTML balance check (html.parser, void tags handled)
unclosed tags at EOF: []
errors: []

# independent relative-link sweep
total relative links checked: 59
missing: []

# Kitaru href byte-diff, HEAD vs working tree
https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand   (both, 5 occurrences each)
```

**Other issues found**
- None blocking. See "Card retains opt-in-unstated phrasing" note above for a possible small follow-up, not required for this task's scope.

**VERDICT: PASS**
