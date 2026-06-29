---
status: done
feature: kitaru-runtime
---

# [PR review rollup] Kitaru durable runtime — re-review of the 064/065 delta (PR #19)

Tags: `rollup`, `pr-review`
Refs: PR #19 (branch: `feat/kitaru-runtime`)

## Scope

Re-review of the three commits added since the prior NO-BLOCKERS pass (`c37ee26..cf86260`):
`695816c` (docs), `b54fe7e` (task 064 — secret-store config source), `cf86260` (task 065 — test
isolation). The 057–062 slice was not re-litigated.

The **code** for 064 (`KitaruSecretSettingsSource`, `reload_settings`, `_config_from_secret_store`,
the cli pre-flight + guard reordering) and the 065 test-infra are high quality and clear all code
review dimensions — no code Blockers. **One documentation-discipline Blocker** comes from the docs
commit (`695816c`), which deleted an Accepted ADR that records a still-in-force architectural
decision without superseding or migrating it. This Blocker routes to **PA** (it is a docs cure, not
an SWE code change), hence the `[PA]` prefix.

Pipeline does not advance until the Blocker is resolved. PA re-grooms on this rollup; the small
reference-repointing in `pyproject.toml` / `AGENTS.md` is done in the same coordinated pass once PA
fixes the ADR resolution. Then re-run PR Reviewer.

## Acceptance Criteria

- [x] Blocker 1 `[PA]`: the pydantic-ai 2.0→1.x downgrade + version-cap decision has an ADR on the
      branch again (either ADR-0009 restored/renumbered, or its rationale folded into ADR-0008 with a
      dedicated section), AND every `ADR-0009` reference is repointed to the live ADR: `pyproject.toml`
      lines 26, 29, 32, 37 (incl. the stale `NEEDS ADR-0009 amend` breadcrumb) and `AGENTS.md` line 68.
      No reference points to a non-existent ADR; the decision behind the version caps is discoverable
      from `docs/adr/`.
- [ ] PA re-runs acceptance review and ACCEPTS.
- [ ] PR Reviewer re-runs and reports `NO BLOCKERS`.

(Nits below are non-blocking; SWE/PA may fix at their discretion. They will be appended to the PR
description if the pipeline advances on the next pass.)

## Blockers (detail)

### 1. [PA] [Documentation discipline] — `docs/adr/0009-...` deleted; the downgrade decision now has no ADR; dangling `ADR-0009` references

- **What's wrong:** Commit `695816c` deleted `docs/adr/0009-downgrade-pydantic-ai-for-kitaru.md` — an
  **Accepted** ADR (with its own task-058 amendment) recording the decision to downgrade pydantic-ai
  2.0 → 1.x and cap `pydantic<2.13`, `click<8.3`, `pydantic-ai-slim>=1.95,<1.96`, and drop kitaru's
  `mcp` extra. That decision is **still in force** — those exact pins live in `pyproject.toml` today.
  The ADR's content was **not** migrated into ADR-0008 (verified: ADR-0008 contains no
  downgrade/version-cap rationale) nor anywhere else in `docs/adr/`. Meanwhile the deleted ADR is
  still cited as the authority in production config and the canonical memory file:
  - `pyproject.toml:26` — `# capped <2.13 for kitaru→zenml (≤2.12.5); see ADR-0009`
  - `pyproject.toml:29` — `# capped <8.3 for kitaru→zenml (≤8.2.1); see ADR-0009`
  - `pyproject.toml:32` — `# ... downgraded from 2.0 (ADR-0009). ...`
  - `pyproject.toml:37` — `# ... Capped <1.96 ... NEEDS ADR-0009 amend.` (also the prior Nit #1)
  - `AGENTS.md:68` — `... not the meta package (ADR-0009). ...`
  ADR-0009 never reached `origin/main`, so on merge the branch lands a non-obvious, far-reaching
  dependency-downgrade decision (every future contributor who tries to bump pydantic/click/pydantic-ai
  hits these caps) with **no ADR**, and four+ breadcrumbs pointing at a file that does not exist.
- **Why it's a Blocker:** Dimension E — "architectural decision landed without an ADR (when
  `docs/adr/` exists)" and "ADR removed/contradicted without supersession." `AGENTS.md` itself lists
  dependency/runtime choices as ADR-worthy ("choosing Kitaru ... ships with one"). The cure is PA's
  (write/restore/fold an ADR), so this is `[PA]`-routed.
- **Suggested fix (PA decides specifics):** Either (a) restore ADR-0009 (or renumber it as a fresh
  ADR) carrying the existing Context/Decision/Consequences + the task-058 amendment, or (b) fold the
  downgrade rationale into ADR-0008 as a dedicated "Consequences — dependency downgrade" section.
  Then repoint all the references above to the chosen ADR (and drop the `NEEDS ADR-0009 amend`
  breadcrumb, since the amendment is already captured).
- **Regression test (if applicable):** none (docs). Optionally, a cheap test asserting no source/config
  comment references a `docs/adr/NNNN-*.md` that doesn't exist would prevent recurrence, but it is not
  required.

## Nits (non-blocking; will be appended to PR description if pipeline advances)

### 1. [Documentation discipline] — `AGENTS.md:~113` (Gemini bullet, introduced by `695816c`)
- **Suggestion:** The shortened infra bullet is now truncated — `- **Gemini** — primary LLM API via
  the ``google-genai`` SDK; ` ends on a dangling `;` plus trailing whitespace and (unlike the
  sibling OpenRouter/Modal/Opik/Kitaru bullets) names no CLI. Finish or trim the line (e.g.
  `... SDK; \`gemini\` / \`GEMINI_API_KEY\`.`) and drop the trailing space.

### 2. [Clean code / correctness] — `src/decode/runtime/flow.py:455-462` (`run_hitl_agent_task`) — prior review Nit #3, still present
- **Suggestion:** The function returns `HitlRunResult(paused=True, output=None)` for **any** status
  that is not `is_finished and is_successful`. A run that genuinely **finished but failed** (e.g. an
  unexpected exception → finished+unsuccessful) is then reported to the operator as "paused on a
  durable human-in-the-loop wait," which misdirects (`kitaru executions input ...` will not help).
  Consider distinguishing finished-unsuccessful from suspended-on-wait. In the cleared 057–062 slice
  and untouched by this delta — carried forward as a Nit only.

### 3. [Standards / hardening] — `src/decode/cli.py:330-350` (secret-store ↔ proxy pre-flight composition)
- **Suggestion:** The "never a traceback" guarantee has one narrow gap: `_secret_store_config_error`
  evaluates `_uses_credentials_proxy()` on the **hydrated** singleton (inside the context) and defers
  to the proxy pre-flight when it sees the proxy on, but the proxy pre-flight at line 346 re-reads the
  **un-hydrated** singleton. So if `runtime_credentials_proxy_enabled` is set **only inside the Kitaru
  secret** (not env/.env) AND the provider key is absent from the secret, neither pre-flight catches
  it and the failure surfaces as an in-flow traceback. Unreachable in any documented config (the proxy
  flag is set via env/.env, and secret-store users keep the key in the secret), so non-blocking; worth
  a one-line note that the proxy-enable flag belongs in env/.env, or reading the flag once from the
  hydrated view in both places.

## Verified resolved (prior-review Nits — no action needed)
- Prior Nit #2 (glossary entries lacked deferred/deployed qualifiers): **resolved** — the 064 glossary
  split adds "Secret-Store Config (Kitaru)" (headless-only, both opt-in/default-off) and reserves
  "Credential Proxy" with an explicit "Deferred to the sandbox milestone" qualifier.
- Prior Nit #4 (belt-and-braces `KitaruRuntimeError` in `cli._launch_durable`): **resolved** —
  `_launch_durable` was dropped in `6b5a9fe`; the pre-flight is now the sole guard.

---

Refs: PR #19

## Log

### [PA] 2026-06-29 17:28 — Grooming / cure of Blocker 1 (documentation discipline)

**Summary**
Cured the `[PA]` documentation-discipline Blocker: commit `695816c` deleted Accepted ADR-0009
(the still-in-force pydantic-ai 2.0→1.x downgrade + version caps), leaving five `ADR-0009`
citations dangling. Resolution chosen: **RESTORE**, not fold.

**Key decisions**
- **Restore, do not fold into ADR-0008.** ADR-0008 = "choose Kitaru"; ADR-0009 = "downgrade
  pydantic-ai + cap pydantic/click/pydantic-ai-slim + drop kitaru's `mcp` extra". These are two
  distinct decisions; folding them violates the project's one-decision-per-ADR convention. The
  content is lossless in git, so restoring verbatim is the correct cure (no supersession, no
  renumber — ADR-0009 is still accurate and in force).
- Restored `docs/adr/0009-downgrade-pydantic-ai-for-kitaru.md` verbatim from `695816c^` via
  `git show`. Confirmed **byte-identical** to the pre-delete version (`git diff --no-index` empty),
  including Status: Accepted, Context/Decision/Diagram/Consequences/Non-goals and the
  "Amendment (2026-06-28, task 058)" block.

**Accuracy verification (ADR §1 Decision vs live `pyproject.toml` — pins are source of truth)**
- `pydantic>=2.0,<2.13` — `pyproject.toml:26` ✓
- `click>=8.1,<8.3` — `pyproject.toml:29` ✓
- `pydantic-ai-slim[google,openai]>=1.95,<1.96` — `pyproject.toml:38` ✓
- `kitaru[local,pydantic-ai,llm]>=0.18.0`; `mcp` extra dropped — `pyproject.toml:44` ✓
- No drift; ADR required no reconciliation.

**Two doc nits fixed in the same pass**
- `pyproject.toml:37` — dropped the now-obsolete `NEEDS ADR-0009 amend.` breadcrumb (the task-058
  amendment is captured in the restored ADR's amendment block). Rest of the comment kept. Closes
  prior-review Nit #1.
- `AGENTS.md:113` — finished the truncated Gemini infra bullet (was `... \`google-genai\` SDK; `
  with a dangling `;` + trailing whitespace and no CLI). Now reads
  `... \`google-genai\` SDK; \`GEMINI_API_KEY\` (no dedicated CLI).` — matches sibling
  OpenRouter/Modal/Opik/Kitaru bullet style; trailing whitespace removed. Closes rollup Nit #1.

**Reference resolution**
- The other four `ADR-0009` citations (`pyproject.toml:26,29,32` + `AGENTS.md:68`) were already
  correct — they resolve now that the file exists again; no repointing needed.
- `git grep -n "ADR-0009"` across the repo (pyproject, AGENTS.md, src/, tests/, tasks/) — every hit
  now points at an existing file. Zero dangling references. `NEEDS ADR-0009 amend` no longer present
  anywhere.

**Out of scope (left untouched, by design)**
- Rollup Nit #2 (`run_hitl_agent_task` paused-status conflation) and Nit #3 (cli guard-composition
  gap) — pre-existing in the already-accepted 057–062 slice, not introduced by this delta. Carried
  forward as Nits.

Blocker 1 cured. No commit/push from PA — Tester gates next, then SWE commits this cure.
Ready for re-test.

### [Tester] 2026-06-29 18:05 — QA (docs + config-comment cure)

**Test summary**
- Format / lint / `uv lock --check` / pre-commit: PASS (`make ci` green end-to-end)
- Unit + integration tests: 1032 passed / 0 failed
- Warnings: 0 (suite runs under `filterwarnings=["error"]`)

**Scope** — this cure is docs + a comment-only config edit; no behavior change expected. Working
tree carries only the cure (`docs/adr/0009-...md` restored, `pyproject.toml` comment, `AGENTS.md`
bullet, `tasks/066`) plus pre-existing `tasks/064`/`tasks/065` dirt from prior pipeline steps
(out of scope, ignored per orchestrator note). No `src/` or test file touched.

**E2E adversarial pass** (docs/config integrity — the relevant "user" surface here is the build
+ every ADR breadcrumb resolving)
- Happy path: `make ci` → `1032 passed in 97.38s`, format-check `144 files already formatted`,
  lint-check `All checks passed!`, `uv lock --check` → `Resolved 149 packages`, rc=0 (PASS)
- Break path 1 (dangling-ref hunt — broaden beyond ADR-0009): `for n in $(git grep -hoE
  "ADR-[0-9]{4}" | sort -u); do test file exists; done` → every cited ADR 0001–0009 maps to a
  real `docs/adr/NNNN-*.md`; zero dangling (PASS)
- Break path 2 (stale breadcrumb): `git grep -n "NEEDS ADR-0009 amend"` → no match; the obsolete
  breadcrumb is gone everywhere (PASS)
- Break path 3 (lock drift from a sneaky dep edit): `uv lock --check` clean → the pyproject edit
  changed no dependency spec, so the resolved tree is byte-stable (PASS)
- Break path 4 (whitespace/format regression in the touched docs): `grep -nE ' +$' AGENTS.md` →
  none; the finished Gemini bullet ends `... (no dedicated CLI).` with a clean newline (PASS)

**Acceptance criteria** (only the in-scope Blocker-1 AC; the other two are downstream pipeline)
- [x] PASS — Blocker 1 `[PA]`: downgrade + version-cap decision has an ADR on the branch again,
      all `ADR-0009` refs repointed/resolve, no dangling reference.
      Evidence:
      • `docs/adr/0009-downgrade-pydantic-ai-for-kitaru.md` restored, **byte-identical** to
        `695816c^` — `git show 695816c^:... | git diff --no-index -` empty, rc=0.
      • Status: Accepted (line 3); carries the "Amendment (2026-06-28, task 058)" block
        (Decision §1 lines 44–55 + Consequences amendments lines 98–105).
      • Caps documented in the ADR match `pyproject.toml` exactly: `pydantic>=2.0,<2.13` (:26),
        `click>=8.1,<8.3` (:29), `pydantic-ai-slim[google,openai]>=1.95,<1.96` (:38),
        `kitaru[local,pydantic-ai,llm]>=0.18.0` + `mcp` extra dropped (:44).
      • `git grep -n "ADR-0009"` — every hit (AGENTS.md:68, pyproject:26/29/32, src/decode/agent/
        loop.py, tests, tasks) resolves to the now-existing file; `NEEDS ADR-0009 amend` gone.
      • `pyproject.toml` diff = the breadcrumb-comment edit only (line 37); no dependency spec/
        version changed → `uv lock --check` clean.
      • `AGENTS.md` Gemini bullet finished, sibling-style, no trailing whitespace.
- [ ] PENDING (downstream, not Tester's step) — PA re-runs acceptance review and ACCEPTS.
- [ ] PENDING (downstream, not Tester's step) — PR Reviewer re-runs and reports `NO BLOCKERS`.

**Evidence**
```
$ git show 695816c^:docs/adr/0009-downgrade-pydantic-ai-for-kitaru.md \
    | git diff --no-index - docs/adr/0009-downgrade-pydantic-ai-for-kitaru.md ; echo rc=$?
rc=0                                  # byte-identical

$ git grep -n "NEEDS ADR-0009 amend"  # no output → breadcrumb removed

$ make ci   # tail
======================= 1032 passed in 97.38s (0:01:37) ========================
$ uv lock --check
Resolved 149 packages in 3ms         # rc=0 — lock unaffected
$ make format-check                  # 144 files already formatted
$ make lint-check                    # All checks passed!
```

**Other issues found**
- None from this cure. The two carried-forward Nits (#2 `run_hitl_agent_task` paused-status
  conflation; #3 cli secret-store↔proxy pre-flight gap) live in the already-accepted 057–062 slice,
  are untouched by this delta, and remain non-blocking — out of scope here.

**VERDICT: PASS**

### [PA] 2026-06-29 18:20 — Acceptance Review (re-confirmation, post-cure)

**VERDICT: ACCEPT**

Re-ran the user-/documentation-integrity acceptance review on the kitaru-runtime feature
(PR #19) after the rollup-066 cure (`d94474a`). The single `[PA]` documentation-discipline
Blocker is cured; nothing else regressed. Evidence verified by reading files + git, not by
running code (the Tester owns runtime; 1032 passed / 0 warnings).

**Verified — a future contributor who hits the caps can discover *why*:**
- `docs/adr/0009-downgrade-pydantic-ai-for-kitaru.md` exists (`ls docs/adr/` shows it,
  7600 bytes); **Status: Accepted** (line 3), Date 2026-06-28. Records the still-in-force
  pydantic-ai 2.0→1.x downgrade + the pydantic/click/pydantic-ai caps, including the
  task-058 "meta→slim, floor 1.95, cap <1.96" amendment (Decision §1, lines 44–55).
- **Byte-identical restore confirmed:** `git diff 695816c^:docs/adr/0009-...md HEAD:...` is
  empty, rc=0 (695816c had deleted all 120 lines). RESTORE over fold was the right call —
  one-decision-per-ADR holds (see below).

**Verified — ADR caps match `pyproject.toml` exactly** (ADR Decision §1 ↔ pyproject deps):
- `pydantic>=2.0,<2.13` — pyproject:26 ✓
- `click>=8.1,<8.3` — pyproject:29 ✓
- `pydantic-ai-slim[google,openai]>=1.95,<1.96` — pyproject:38 ✓
- `kitaru[local,pydantic-ai,llm]>=0.18.0`, `mcp` extra dropped — pyproject:44 ✓
- Context-table sub-caps also echoed in the pin comments (`≤2.12.5`, `≤8.2.1`). No drift.

**Verified — no dangling pointer remains:** `git grep -n "ADR-0009"` — every hit
(AGENTS.md:68, pyproject:26/29/32, src/decode/agent/loop.py:151/383, tests, tasks) now
resolves to the existing ADR file. The obsolete `NEEDS ADR-0009 amend` breadcrumb is gone
from `pyproject.toml` (it survives only as historical record inside the task logs, which is
correct).

**Verified — the ADR set is coherent (two distinct decisions, not redundant):**
- ADR-0008 = "Kitaru durable runtime — a headless flow as a second entry path" (the *choice*
  of Kitaru). ADR-0009 = "Downgrade pydantic-ai 2.0→1.x (and cap pydantic/click) to integrate
  the Kitaru runtime" (the *cost/consequence* of that choice). Both Accepted; neither
  duplicates the other. Folding 0009 into 0008 would have violated one-decision-per-ADR — the
  RESTORE was the correct resolution.

**Verified — no behavior/product change:** `git show d94474a --stat` = docs + one config
*comment* + task logs only. `pyproject.toml` diff is the comment-only line-37 breadcrumb
removal (the `pydantic-ai-slim` pin itself is untouched → `uv lock --check` clean). `AGENTS.md`
diff finishes the truncated Gemini bullet (`... GEMINI_API_KEY (no dedicated CLI).`). No `src/`
or `tests/` file touched.

Rollup AC "PA re-runs acceptance review and ACCEPTS" → satisfied. All original-feature
acceptance criteria remain verified from the user/documentation-integrity POV. Hand off to the
PR Reviewer for the final `NO BLOCKERS` confirmation, then human squash-merge.

### [PR Reviewer] 2026-06-29 17:55 — Re-review (post-066 cure)

**VERDICT: NO BLOCKERS**

Re-reviewed the 066 cure commit `d94474a` against the one carried Blocker from the prior pass.

- ADR-0009 restored **byte-identical** to the pre-deletion version (`git diff
  695816c~1:docs/adr/0009-...md d94474a:...` → empty). Status: Accepted; the task-058 amendment
  (slim-package pin, floor 1.95) intact.
- Documented caps match `pyproject.toml`: `pydantic>=2.0,<2.13`, `click>=8.1,<8.3`,
  `pydantic-ai-slim[google,openai]>=1.95,<1.96`, `kitaru[...]>=0.18.0` mcp-extra dropped.
- Zero dangling `ADR-0009` refs — every hit resolves to the existing file. `NEEDS ADR-0009
  amend` breadcrumb gone from `pyproject.toml` (survives only inside task logs as history).
- Cure is comment/doc-only: `git show d94474a --stat` = AGENTS.md + docs/adr/0009 + one
  pyproject *comment* + task logs. No `src/` or `tests/` change → no new Blockers.

Blockers: 0; Nits: 2 (both pre-existing, carried forward, appended to PR #19 description).
064/065 code + 057–062 slice not re-litigated (already cleared). Pipeline may advance to
hand-off / human squash-merge.
