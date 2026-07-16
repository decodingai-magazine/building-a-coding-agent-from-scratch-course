# decode article series — v4

Billing: Modal 4 · Opik 2 · ZenML 2. Every lesson: ONE ambient Opik trace, concept-matched, ≤2 sentences else cut + cross-link. Every secondary sponsor mention links to that sponsor's primary lesson.

## Series threads (run through every lesson)

1. **Ambient Opik trace** — one screenshot per lesson, concept-matched (tiers: ambient all 8 → instrument #4 → judge #7).
2. **Proof section** — every lesson ends with ~250 words: what that lesson's milestone capstone asserts, driven through the real harness with only the model/network swapped. Deterministic-testing content lives ONLY in these sections (+ #7's intro recap) — it never gets its own lesson because it has zero sponsor surface.
3. **IOU ledger** — every forward promise is planted and cashed explicitly:

| Planted | Cashed | Promise |
|---|---|---|
| #1 | #8 | Hero trace — "by lesson 8 you read this fluently" |
| #1 | #7 | "Two answers" thesis (harness correct vs agent good) |
| #2 | #5 | Naive HITL gate → gate proper |
| #2 | #4 | Session log paragraph → persistence story |
| #2 | #6, #8 | Warm Modal endpoint reused for fan-out sweep + deploy |
| #3 | #8 | Replay answer-reuse (bypass-only locally) → deployed stack |
| #3 | #8 | Model-Override-as-flow-input → cohort forks |
| #6 | #8 | Code-Reviewer agent → cloud PR reviewer |
| #7 | #8 | The judge → cohort scoring, imported verbatim |

---

## 1. What we're building + system design

**Primary: Modal · secondary Opik, ZenML**

- Anatomy: harness vs loop vs model
- Three seams the course pays off — inference (Modal) / observability (Opik) / durability (ZenML)
- Milestone map
- Course method: ADR per decision, glossary, `tasks/` tracker, `make ci`
- **Full ADR index** — navigation hub for the series
- MCP cut from map (no code exists)
- **"Two answers" thesis planted:** is the harness correct (deterministic, per-lesson Proof sections) vs is the agent good (probabilistic, #7)

Proof: pattern declared — capstone-per-milestone, `FunctionModel` = scripted model, network-boundary-only swap, `make ci` runs it all.

Ambient trace: hero shot — full session tree. "By lesson 8 you read this fluently" — the promise #8 cashes.

*ADRs: 0001 + index*

---

## 2. The agent loop & the human in it

**Primary: Modal · Opik wired**

- **Spine: one user turn end-to-end** — prompt → `agent.iter()` → tool call → approval → result → stream → render → log append
- Full-size: core tools (`read`/`edit`/`bash`; one-liners `web`, `tasks`), Decision Channel, Priority Gate (steer/follow-up/abort demo), modal_models.md thesis (tool-call reliability #1 criterion; vLLM parser disqualifier), presence-based `init_tracing()`
- Demoted: naive HITL gate ≤150 words → pointer #5 · session log one paragraph → pointer #4 · TUI internals → sidebar
- **Endpoint deployed here stays warm through #6 and #8**

Proof: **M1 capstone, full treatment** (founding member of the thread) — six-step conversation through real `build_agent()` + `Runner` + renderer + session log, `FunctionModel` for the model, `httpx.MockTransport` for the web tool, no API key, no network. The "how do you regression-test a nondeterministic system" essay lives here.

Ambient trace: first trace — single ReAct turn.

*ADRs: 0002, 0005, 0014*

---

## 3. Durable execution, HITL & replay

**Primary: ZenML**

- Sleep-tool opener (one tool, two semantics)
- `@flow`, checkpoint strategy, `decode run`
- `kill -9` → resume demo
- `--hitl` durable waits, resolved from another terminal
- Replay three-runs, anchors, **Model-Override-as-flow-input** (planted here, harvested #8), checkpoint overrides
- Downgrade sidebar (pydantic-ai 2.0 → 1.x)
- Execution-id = Opik thread id free win
- **IOU stated explicitly: replay is bypass-only on local stack; answer-reuse lands in #8**

Proof: kill/resume + checkpoint tests — durability asserted deterministically.

Ambient trace: crash/resume — cached checkpoints vs fresh spans.

*ADRs: 0008, 0009, 0010*

---

## 4. Context engineering: the window is a budget

**Primary: Opik (instrument) · ZenML secondary**

Thesis: **context engineering without measurement is folklore.** Five moves, each a before/after experiment measured in Opik:

| Move | Measurement |
|---|---|
| Memory | Cold vs warm start |
| Compaction | Cost curve, ~60%/~80% triggers marked |
| Skills | Tokens per disclosure tier |
| LSP | 3 speculative reads vs 1 call |
| Truncation | Spill vs full read |

- Session JSONL + `--resume` live here (persist move)
- Driver: headless `decode run` on fixed script — repeatable, ZenML cross-link

Proof: Compaction Boundary tests (a compaction never orphans a tool-call/result pair), truncation snap-to-line tests.

*ADRs: 0004, 0006, 0007, 0014*

---

## 5. Containing the agent: permissions → sandbox

**Primary: Modal · ZenML touch**

Trust ladder, two rungs:

- Gate proper (Tool Kind, modes, deny→allow→mode precedence, Plan Mode)
- Containment (`SandboxExecutor` seam, `none`/`docker`/`modal`, fresh-exec)
- Workspace vs Harness Home
- Hand-back Session Branch, host-side creds
- Worker holds exactly `SANDBOX_GIT_TOKEN` (scoped, revocable)
- **Credential Proxy postmortem**

Proof: permission precedence table tests, seam tests, hand-back tests (branch survives failed push).

Ambient trace: same `bash` call under `none`/`docker`/`modal` — seam visible in spans.

*ADRs: 0003, 0011 → 0012, 0016*

---

## 6. Agents catalog, subagents & parallel fan-out

**Primary: Modal · Opik secondary**

- Catalog (Build/Plan/Code-Reviewer/Explore — **Code-Reviewer introduced as #8's future PR reviewer**)
- `agent` tool auto-allow
- Fan-out semaphore, width 6
- Subagent Report contract, budget split, Synthesis Footer
- Resilience (retry + failure note, never silent hole)
- `Ctrl+O` verbose mode
- Workspace inheritance payoff
- Modal reportage: sweep `subagent_max_parallel` 1→6 against #2's warm endpoint; fallback: Opik cost traces vs hosted API, labeled

Proof: fan-out resilience tests — empty report / zero-tool-call child → one retried spawn → explicit failure note, never a silent hole.

Ambient trace: fan-out tree, per-child token spend — ADR-0014's origin.

*ADRs: 0003, 0013, 0017-fanout, 0014*

---

## 7. Is the agent good? The eval stack

**Primary: Opik (judge)**

Opens by cashing the Proof thread: suite green all series — 117 test files, 39 driving the model via `FunctionModel`/`TestModel`, 16 milestone capstones, `filterwarnings=["error"]` (re-count at write time) — harness correct. Now the question pytest structurally cannot answer.

- **One-change-two-verdicts demo:** one PR (prompt tweak), deterministic suite green, regression probe red — the "two answers" thesis from #1 made concrete
- Spine = **eval lifecycle**, offline → gate → production → optimize:
  1. **Benchmark** — outcome scoring (`make eval-benchmark`)
  2. **Regression probes** — behavior gate (`make eval-regression`), never-in-CI rule
  3. **G-Eval** — qualitative judging, demo skills as fixtures
  4. **Online evals** — scoring production runs (bridges into #8's operator world)
  5. **Auto-optimization** — closing the loop
  6. **Build the judge** — #8 imports it verbatim, zero new eval code

*ADRs: 0017-eval-suite*

---

## 8. Ship it to your team: two use cases, end to end

**Primary: ZenML · Modal secondary · Opik as judge**

**Perspective flip: builder → operator.** You now run this agent for a team. **Nothing new is taught — only composed** (stated in intro; self-test: any section needing fresh explanation belongs in an earlier lesson).

Mission-first structure — open with use case 1's goal, infrastructure enters only when mission blocks on it:

- **Use case 1 — feature → cloud GitHub PR review.** Teammate labels an issue, receives a reviewed PR. Blocks resolved in order: config crashes in cloud → **Environment Bucket** (`DECODE_ENV`, fail-loud, never backfilled from `.env`) · run blocks on wait → **deployed stack + answer-reuse** (lesson 3's IOU cashed) · run completes → hand-back → glue opens PR → **Code-Reviewer agent** (#6) reviews on PR-open.
- **Shipping ladder** (= deploy section):
  - Rung 0 — operator laptop (`DECODE_ENV=staging decode run --repo …`)
  - Rung 1 — deployed Kitaru stack (trigger via CLI/`KitaruClient`/MCP; waits answered by anyone; bucket secrets)
  - Rung 2 — GitHub-is-the-UI (issue label → flow → PR → review flow → human squash-merge). UC1 ships at rung 2.
- **Use case 2 — same feature ×5-10, compared.** Stays operator-level (experiment, not service): `make cohort ANCHOR=<id> N=5 OVERRIDES="model=…"` — forks from one anchor (Model Override's whole reason to exist), each in own Modal Workspace, unique session branches by construction, **#7's judge** scores each, Opik groups as one experiment. Deliverable: one comparison table — score / cost / diff-vs-Baseline-Rerun / branch link — posted to team. Merge winner, delete rest.
- **Glue inventory, published honestly** (~150 lines: issue-label Action ~40 · `gh pr create` hook ~10 · PR-open review trigger ~30 · cohort loop + table ~70). "Copy the last mile" — most-bookmarked section.
- **Governance paragraph:** GitHub App / scoped PAT per repo · branch protection on `decode/*`, human merges · bypass mode only inside sandbox · cohort cost printed before firing.
- **Bookend:** walk #1's hero trace span by span — it's UC1's cloud run. Second screenshot: cohort view scored by judge.

Fallback: UC2 degrades to 3 forks without shame; UC1 alone still lands the article.

Proof: CI runs the whole suite one last time; glue stays thin enough not to need tests — say so explicitly.

*ADRs: 0015, 0016, 0010, 0012*

---

## Standing rules

- Scoping self-tests: #7 — every paragraph answers "is the agent good?"; #8 — nothing new taught, only composed
- #8 watch: deploy section drifts Modal-heavy — cap at one paragraph + cross-link; rungs/waits/forks/bucket keep ZenML-primary honest

## Pre-write chores

1. Renumber ADR-0017 collision (eval-suite vs fanout) — both cited in print
2. MCP: build or cut from #1 map
3. Re-verify test counts at #7 write time
4. Keep #2's Modal endpoint deployed through #6 + #8
5. Write the ~150 lines of #8 glue + `make cohort` before drafting #8

## Coverage

| Package | Lesson |
|---|---|
| cli / tui / harness / agent | 2 |
| entities / config / logging | 2, 8 |
| tools | 2 |
| runtime | 3, 8 |
| context + memory | 4 |
| skills + frontmatter | 4, 6 |
| services/lsp | 4 |
| permissions | 5 |
| sandbox | 5, 8 |
| agents | 6, 8 |
| observability | 2, 4, 7 |
| evals | 7, 8 |
| tests (unit + capstones) | Proof sections 1–8, recap in 7 |

ADRs 0001–0017×2 all placed.
