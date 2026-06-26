---
id: 035-skills-doc-drift-flat-format
feature: skills-directory-convention
status: done
---

# [PA rejection] Skills directory convention — config docs still describe the dropped flat `*.md` format

Tags: `rollup`, `pa-rejection`, `docs`
Refs: `tasks/032-skills-directory-convention-loader.md` (also 033, 034) · [ADR-0004](../docs/adr/0004-milestone-3-skills.md) §3

## Scope

The skills directory-convention refactor (tasks 032–034) PASSED automated QA and is correct in code
and in the **canonical** docs (ADR-0004 + `docs/glossary.md` both accurately describe
`<name>/SKILL.md`). But two **in-repo config-doc surfaces** still describe project skills as the
**dropped flat `*.md` format** — the exact thing this refactor hard-switched away from. A real user
who reads `.env.example` (per CLAUDE.md, *the* config surface for authoring) to learn how to add a
project skill is told to create a flat `*.md` **file**; under the shipped loader that file is
**silently skipped** (it only logs a DEBUG migration hint the user never sees — `skills/loader.py`
`discover_project_skills`, lines 132–141), so the skill never appears in the catalog and never loads.

This is a documentation-fidelity fix only — bring the two stale comments in line with the
already-correct ADR-0004 §3 + glossary (`SKILL.md`, "the directory name is cosmetic"). **No product
decision changes, no production logic changes, no entity/loader/payload changes.** The canonical
PA-owned docs are correct and are the source of truth to mirror.

## Acceptance Criteria

- [x] Issue 1: `.env.example` (the `# --- Skills ---` block, lines ~47–50) describes project skills
      as **directories** `<name>/SKILL.md` (not flat `*.md` files), and the override unit as a
      same-**name skill/directory** (not a "same-name file"). The `SKILLS_DIR=.decode/skills` line and
      value stay unchanged.
- [x] Issue 2: `src/decode/config/settings.py` `skills_dir` comment (lines ~54–55) describes the
      directory convention `<name>/SKILL.md` (not flat `*.md` files); the field, default
      (`Path(".decode/skills")`), and "read only via this singleton" note stay unchanged.
- [x] No flat-format authoring instruction remains in any user-facing or developer-facing surface: a
      repo-wide check for project-skill authoring described as flat `*.md` "files" returns nothing
      except deliberate **migration/historical** notes (e.g. ADR-0004's "corrected from an initial
      flat-file draft", the loader's loose-`*.md` DEBUG hint). Verify with a grep over
      `src/ docs/ .env.example AGENTS.md` and confirm only those intentional references survive.
      (Also corrected the `decode.skills` package `__init__.py` docstring, lines 6/11, which still
      described skills as flat `*.md` files as *current behavior* — a third drifted developer-facing
      surface; docstring-only, no logic change.)
- [x] The wording matches the canonical vocabulary verbatim: `SKILL.md`, `<name>/SKILL.md`, and the
      "directory name is cosmetic / keyed by frontmatter `name`" framing from `docs/glossary.md` and
      ADR-0004 §3. No new term is introduced; the glossary needs no change (it is already correct).
- [x] `make ci` is green with 0 warnings (doc-only edits; no test or production logic touched).
- [x] Tester re-runs full QA suite and PASSES.
- [ ] PA re-runs acceptance review on the 032–034 refactor and ACCEPTS.

## Issues (detail)

### 1. `.env.example:48-49` — describes the dropped flat format
- **What the user experiences (wrong):** The `# --- Skills ---` block reads
  "Project-local skill `*.md` files (frontmatter + body); a same-name file overrides a built-in
  skill." A user follows it, drops `.decode/skills/deploy.md`, launches `decode`, and the skill
  **never loads** — no error, no catalog entry, just silent absence (the loader skips a loose `*.md`
  with a DEBUG-level migration hint the user does not see).
- **What the spec / shipped behavior implies (right):** A project skill is a **directory**
  `<cwd>/.decode/skills/<name>/SKILL.md`; a same-name **skill** (keyed by frontmatter `name`, dir name
  cosmetic) overrides a built-in. This is exactly what ADR-0004 §3 and the glossary already say.
- **Suggested fix:** Reword the two comment lines to name the `<name>/SKILL.md` directory convention
  and the same-name-skill override; optionally note a directory may also ship bundled
  `references/`/`examples/`/`scripts/` resources (tier 3). Keep `SKILLS_DIR=.decode/skills` as is.

### 2. `src/decode/config/settings.py:54-55` — same flat-format drift
- **What the developer experiences (wrong):** The `skills_dir` field comment reads "Project-authored
  skill ``*.md`` files live here (relative to cwd); they override a built-in skill of the same
  frontmatter name." It contradicts the shipped loader (which scans `<name>/SKILL.md` subdirectories)
  and the ADR/glossary.
- **What the spec / shipped behavior implies (right):** Project-authored skills live here as
  `<name>/SKILL.md` directories; each overrides a built-in of the same frontmatter `name`.
- **Suggested fix:** Reword the comment to the directory convention. Field, type, default, and the
  "read only via this singleton" note are unchanged.

## User Stories

(Inherit from the original 032 task — no new behavior. Re-verify after the fix.)

### Story: A developer authors their first project skill from the docs
1. User opens `.env.example` to learn how to add a project skill.
2. The `# --- Skills ---` block tells them to create `<.decode/skills>/<name>/SKILL.md`.
3. User creates `.decode/skills/deploy/SKILL.md` with `name: deploy` frontmatter + a body.
4. User launches `decode` and types `what can you do?`.
5. The injected Skills Catalog lists `- deploy — <description>` (the skill loaded as documented).
6. User types `/deploy` (or the model calls `skill("deploy")`) and the body is returned — the skill
   the docs told them to author actually works.

## Out of scope
- Any change to the loader, `SkillDef`, the payload helper, the catalog, or the dispatcher (032/033
  shipped and QA-passed them).
- Any change to ADR-0004 or `docs/glossary.md` — both are already correct and are the source of truth
  this task mirrors.
- The deferred ADR-0004 §10 items (user-home source, per-agent allowlists, body-size cap, dispatcher
  `args`).

## Log

### [PA] 2026-06-26 — Acceptance Review rollup filed
Filed on REJECT of the 032–034 acceptance review. Two config-doc surfaces still describe the dropped
flat `*.md` skill format; bring both in line with the already-correct ADR-0004 §3 + glossary. Doc-only
fix — route to SWE, then re-run QA and PA acceptance on the refactor.

### [SWE] 2026-06-26 14:20 — Implementation

Doc/comment-only fidelity fix. No production logic, entity, loader, payload, catalog, or dispatcher
change. No new tests (no decidable code contract changed); proved the docs match shipped behavior
end-to-end against the real loader.

**Files modified**
- `.env.example` — `# --- Skills ---` block rewritten: project skills are directories `<name>/SKILL.md`
  (not flat `*.md`), keyed by frontmatter `name` (dir name cosmetic), same-`name` override, optional
  bundled `references/`/`examples/`/`scripts/` siblings, missing dir → built-ins only.
- `src/decode/config/settings.py` — `skills_dir` comment rewritten to the `<name>/SKILL.md` directory
  convention + same-`name` override + missing-dir note. Field value, default, type, and the
  "read only via this singleton" note unchanged (comment-only).
- `src/decode/skills/__init__.py` — package docstring (lines 6/11) corrected from the dropped flat
  `*.md` description to the `<name>/SKILL.md` directory convention, mirroring `loader.py`. Third
  drifted developer-facing surface found via the criterion-3 grep; docstring-only, no code change.
  (Surfaced beyond the two explicitly-named surfaces solely to satisfy criterion 3's clean-grep gate —
  flag for PA/Tester.)

**Tests**
- Unit: 690 passing, 0 failing. Integration: 8 passing. `make ci`: 698 passing, 0 warnings
  (`filterwarnings=["error"]` would fail on any warning).
- No new tests added — doc/comment-only change, no behavior contract changed.

**Acceptance criteria**
- [x] Issue 1 `.env.example` — rewritten to directory convention; `SKILLS_DIR=.decode/skills` unchanged.
- [x] Issue 2 `settings.py` `skills_dir` comment — rewritten; field/default/"singleton" note unchanged.
- [x] Criterion 3 grep clean — only deliberate corrective/migration/historical/unrelated survivors
      remain (see Evidence); also fixed `skills/__init__.py` drift.
- [x] Criterion 4 — canonical vocab verbatim (`SKILL.md`, `<name>/SKILL.md`, "directory name cosmetic /
      keyed by frontmatter `name`"); no new term; glossary untouched.
- [x] Criterion 5 — `make ci` green, 0 warnings.
- [ ] Tester re-runs full QA suite and PASSES.
- [ ] PA re-runs acceptance review on the 032–034 refactor and ACCEPTS.

**Evidence**
```
$ grep -rniE "skill" src/ docs/ .env.example AGENTS.md | grep -iE "\*\.md|\.md file|flat"
src/decode/config/settings.py:54:    # ... as ``<name>/SKILL.md`` directories, not flat   # corrective (new)
src/decode/skills/loader.py:6:  a flat ``*.md`` is no longer a skill (hard switch ...)     # historical
src/decode/skills/loader.py:125: the dropped flat format ... logged at DEBUG to aid migration # migration
docs/adr/0004-...md:61:  hard switch — no flat format ... A skill is a directory             # corrective
docs/adr/0004-...md:129: the flat registry ...                                               # unrelated (tool registry)
docs/adr/0004-...md:245,247: flat *.md shipped by default / **/*.md glob                      # packaging note
.env.example:49:  not flat `*.md` files. A skill is keyed by its frontmatter `name` ...       # corrective (new)
```
```
$ make ci   # tail
============================= 698 passed in 7.21s ==============================
```
End-to-end against the real loader in a temp project cwd:
```
loaded names: ['commit', 'deploy', 'review-diff']
deploy loaded as documented: True                          # .decode/skills/deploy/SKILL.md loads
flat legacy.md silently skipped (as docs now warn): True   # .decode/skills/legacy.md ignored
```

**Notes**
- The `__init__.py` docstring fix is the one place I went past the two explicitly-named surfaces. I
  did so only because criterion 3 requires the `src/` grep to be clean of flat-format current-behavior
  descriptions, and those two docstring lines described the dropped format as if current (not a
  migration note). It is docstring-only, mirrors the already-correct `loader.py` + ADR-0004/glossary,
  and changes no logic. If PA considers it out of scope, it is a one-line revert — flagging explicitly.
- NOT COMMITTED — handing off to Tester per role.

### [Tester] 2026-06-26 15:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` → 111 files already formatted; `ruff check` → all checks passed)
- Unit tests: 690 passed / 0 failed
- Integration tests: 8 passed / 0 failed (`test_milestone1_capstone` + `test_milestone3_skills_capstone`)
- `make ci`: 698 passed, 0 warnings (`uv lock --check` clean; `filterwarnings=["error"]` in effect)
- Warnings: 0

**E2E adversarial pass** (doc-fidelity: does the shipped loader behave exactly as the corrected docs now claim?)
- Happy path: real `load_skills(tmp)` with `.decode/skills/deploy/SKILL.md` → `deploy` in catalog, body returned intact (`'Deploy steps here.'`); built-ins `commit`/`review-diff` also present → PASS
- Break path 1 (dropped flat format — what the OLD docs told users to author): loose `.decode/skills/legacy.md` → silently skipped, only a DEBUG migration hint emitted, `legacy` absent from catalog → matches corrected docs exactly → PASS
- Break path 2 (boundary: missing skills dir): `load_skills` with no `.decode/skills` → `['commit','review-diff']` (built-ins only) → matches "Missing dir → built-ins only" → PASS
- Break path 3 (dir-name cosmetic claim): dir `whatever-dir/SKILL.md` with `name: realname` → loaded keyed by `realname` → matches "directory name is cosmetic / keyed by frontmatter name" → PASS
- Break path 4 (bundled-resources claim): `deploy/SKILL.md` + sibling `references/note.md` → `resource_dir` set non-None → matches ".env.example may also ship bundled references/examples/scripts siblings" → PASS
- Break path 5 (same-name override claim): project `commit/SKILL.md` → overrides built-in (`body == "MY OVERRIDE"`) → matches "same-`name` overrides a built-in" → PASS

**Acceptance criteria**
- [x] PASS — Issue 1 `.env.example` rewritten to directory convention `<name>/SKILL.md`, frontmatter-`name` override, optional bundled siblings, missing-dir note; `SKILLS_DIR=.decode/skills` line unchanged — Evidence: `git diff .env.example` (only the comment block changed, lines 47–53; `# SKILLS_DIR=.decode/skills` untouched)
- [x] PASS — Issue 2 `settings.py` `skills_dir` comment rewritten to directory convention + same-`name` override + "read only via this singleton"; COMMENT-ONLY — Evidence: `git diff` shows only the two comment lines changed; field line `skills_dir: Path = Path(".decode/skills")` value/type/default untouched
- [x] PASS — No flat-format authoring instruction remains; grep over `src/ docs/ .env.example AGENTS.md` returns only deliberate survivors — Evidence: task-grep hit is ADR-0004:63 ("there is no flat `<name>.md` support", corrective); broader grep survivors are the two new corrective comments, `loader.py:6` (historical hard-switch note), `loader.py:125` (loose-`*.md` DEBUG migration hint), ADR-0004:61 (corrective), :129 (tool registry — unrelated), :245/:247 (wheel packaging note). None describes flat files as the current authoring format. Third surface `skills/__init__.py` is DOCSTRING-ONLY (file is lines 1–18, pure docstring; no imports/exports/code) — in-scope (same defect class: a developer-facing doc describing the dropped format as current; no overreach)
- [x] PASS — Wording matches canonical vocabulary verbatim (`SKILL.md`, `<name>/SKILL.md`, "directory name is cosmetic", "keyed by frontmatter `name`", bundled resources, same-`name` override); no new term; glossary not in diff — Evidence: `docs/glossary.md:21-26` + `docs/adr/0004…:20,60-72` carry the identical phrasing
- [x] PASS — `make ci` green, 0 warnings — Evidence: `698 passed in 8.70s`
- [x] PASS — Tester re-ran full QA suite and PASSES (this entry)
- [ ] PA re-runs acceptance review on the 032–034 refactor — out of Tester scope; left for PA

**Evidence**
```
$ make ci   # tail
tests/integration/test_milestone3_skills_capstone.py .......             [100%]
============================= 698 passed in 8.70s ==============================

$ uv run python (real loader, temp cwd)
project skill names: ['deploy']
all loaded names (built-ins + project): ['commit', 'deploy', 'review-diff']
deploy (<name>/SKILL.md) loaded: True
flat legacy.md silently skipped: True            # DEBUG hint only, user never sees it
CLAIM A missing-dir -> built-ins only: True
CLAIM B dir-name cosmetic (keyed by frontmatter): True
CLAIM C bundled-resource dir captured: True
CLAIM D same-name override wins: True
```

**Other issues found**
- None. The fourth changed file in `git status` (`tasks/032-…md`) is an append-only PA rejection log entry (tracker bookkeeping that filed this rollup), not an unrelated code change — benign. No production logic, entity, loader, payload, catalog, or dispatcher line changed (verified: the only `src/` edits are comments in `settings.py` and the `__init__.py` docstring).

**VERDICT: PASS**
