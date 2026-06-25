---
id: 024-skills-skilldef-entity
feature: skills
status: done
---

# Skills: the SkillDef entity

Implements [ADR-0004](../docs/adr/0004-milestone-3-skills.md) (the Skill entity).
Depends on: (none) · Blocks: 025

## Scope

Define the **Skill** entity: the validated, parsed result of one skill Markdown file. Pure entity +
validation — no loading, dispatcher, or catalog yet (later tasks). Mirror
`src/decode/entities/agent_def.py` (frozen + slotted, owns its own validation).

- **`entities/skill_def.py`** — `SkillDef`, a `@dataclass(frozen=True, slots=True)`:
  - `name: str` — the skill's canonical name (the key the dispatcher resolves and the catalog lists);
    it comes from the file's `name:` frontmatter, not the filename (ADR-0004).
  - `description: str` — the one-line summary shown in the Skills Catalog.
  - `body: str` — the full Markdown instructions returned on demand (pure injected guidance, not code).
  - `source: str` — provenance label so a project override is distinguishable in logs (e.g.
    `"builtin"` for a packaged skill, or the absolute project file path for a discovered one).
  - `__post_init__` validates **every field non-empty** (after `.strip()`) and raises a clear
    `ValueError` naming the offending field (and the skill `name` where it is known), exactly like
    `AgentDef.__post_init__`.
  - **No `tools` / `mode` / `allow` / `deny` fields** — skills are pure injected instructions this
    milestone (ADR-0004); the actions a skill *describes* still ride their own tool gates.

## Acceptance criteria

- [x] `SkillDef` is `frozen=True, slots=True`; constructing with `name`/`description`/`body`/`source`
      yields an immutable instance whose attributes read back unchanged. Unit-tested.
- [x] Empty-or-whitespace `name` raises `ValueError` mentioning `name`. Unit-tested.
- [x] Empty-or-whitespace `description` raises `ValueError` mentioning `description`. Unit-tested.
- [x] Empty-or-whitespace `body` raises `ValueError` mentioning `body`. Unit-tested.
- [x] Empty-or-whitespace `source` raises `ValueError` mentioning `source`. Unit-tested.
- [x] `SkillDef` carries **no** `tools`/`mode`/`allow`/`deny` attributes (assert via `hasattr`/fields).
- [x] **Working looks like:** `SkillDef(name="commit", description="…", body="…", source="builtin")`
      constructs; `dataclasses.replace(it, name="")` raises `ValueError`.
- [x] `make ci` green, 0 warnings; `tests/unit/decode/entities/test_skill_def.py` mirrors
      `src/decode/entities/skill_def.py`.

## Out of scope
- The loader, the two built-in skill files, and project discovery — task 025.
- The `skill` dispatcher tool + registry wiring — task 026.
- Catalog injection — task 027.
- The `/<skill-name>` TUI invocation — task 028.
- A body-size cap (deferred — skills are author-trusted and small, ADR-0004).

## Log
### [PA] 2026-06-25 — Grooming
First task of the Skills feature. Models `SkillDef` directly on `entities/agent_def.py` (frozen +
slotted, self-validating) but trims it to the M3 reality: pure instructions, so **no**
tools/mode/rules — just `name`/`description`/`body` plus a `source` provenance field so a project
override is traceable in logs. The `name` is the frontmatter `name:` (filename is cosmetic, ADR-0004
§3). No dependencies; blocks the loader (025).

### [SWE] 2026-06-25 22:30 — Implementation

**Files modified**
- `src/decode/entities/skill_def.py` — new `SkillDef` frozen+slotted dataclass (`name`/`description`/`body`/`source`); `__post_init__` validates each field non-empty after `.strip()` and raises a `ValueError` naming the field (and the skill `name` once known), mirroring `AgentDef`. No tools/mode/allow/deny.
- `tests/unit/decode/entities/test_skill_def.py` — new test module (mirrors src 1:1) covering the full acceptance set.

**Tests**
- Unit: 13 passing, 0 failing in the new module; full suite 575 passing, 0 warnings (`filterwarnings=["error"]`).
- Integration: N/A — pure entity, no infra changes.

**Acceptance criteria**
- [x] frozen+slots immutable round-trip — `test_skill_def_carries_its_fields`, `test_skill_def_is_frozen_and_hashable`, `test_skill_def_uses_slots`
- [x] empty `name` → ValueError mentioning name — `test_skill_def_rejects_an_empty_name`
- [x] empty `description` → ValueError — `test_skill_def_rejects_an_empty_description`
- [x] empty `body` → ValueError — `test_skill_def_rejects_an_empty_body`
- [x] empty `source` → ValueError — `test_skill_def_rejects_an_empty_source`
- [x] no tools/mode/allow/deny attrs — `test_skill_def_has_no_persona_or_rule_fields` (parametrized, via `dataclasses.fields` + `hasattr`)
- [x] working-looks-like: constructs + `dataclasses.replace(it, name="")` raises — `test_skill_def_replace_with_empty_name_raises`
- [x] QA clean, 0 warnings; test mirrors src 1:1

**Evidence**
```
$ make unit-tests
... tests/unit/decode/entities/test_skill_def.py ...
============================= 575 passed in 6.57s ==============================

$ uv run python -c '... SkillDef(...) ; dataclasses.replace(it, name="") ...'
constructed: SkillDef(name='commit', description='stage and commit the working tree', body='# Commit\nRun git add then git commit.', source='builtin')
fields: ['name', 'description', 'body', 'source']
has __dict__: False
replace(name="") raised ValueError: skill name must be a non-empty string
empty description raised: skill 'commit' must have a non-empty description
empty body raised: skill 'commit' must have a non-empty body
empty source raised: skill 'commit' must have a non-empty source
```

**Notes**
- format-fix / lint-fix / format-check / lint-check / pre-commit all clean. Not committed — awaiting Tester review.
- `SkillDef` is not re-exported from `entities/__init__.py`; followed the existing `AgentDef` pattern (direct module import).

### [Tester] 2026-06-25 23:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 97 files formatted; `ruff check` all checks passed)
- Unit tests: 575 passed / 0 failed
- Integration tests: 1 passed / 0 failed
- `make ci` (lock-check + format + lint + full suite): 576 passed / 0 failed
- Warnings: 0 (suite runs `filterwarnings=["error"]`, so any warning would have failed the run)
- New module `tests/unit/decode/entities/test_skill_def.py`: 13 collected, 13 passed (SWE's count is accurate)

**E2E adversarial pass** (`uv run python` driving `SkillDef` directly; AgentDef cross-checked)
- Happy path: `SkillDef(name="commit", description="stage and commit", body="# Commit\nrun git", source="builtin")` → constructs; attribute round-trip clean; `dataclasses.fields` = `['name','description','body','source']` (PASS)
- Break 1 (boundary: empty `""` per field): each of name/description/body/source `""` raises `ValueError` naming that exact field; non-`name` errors also name the skill (`skill 'n' must have a non-empty <field>`) (PASS)
- Break 2 (boundary: whitespace-only — tabs `\t\t`, newline `\n`, spaces, mixed ` \t\n `): every field raises `ValueError` naming the field (strip-then-check works) (PASS)
- Break 3 (validation order: all-four-empty): reports `name` first, matching `__post_init__` order (PASS)
- Break 4 (state edge: `dataclasses.replace`): `replace(it, name="")` → `ValueError`; `replace(it, source=" ")` → `ValueError` naming source+skill; `replace(it, name="amend")` → valid new instance (PASS)
- Break 5 (immutability): `setattr(name)` → `FrozenInstanceError`; `delattr(name)` → `FrozenInstanceError`; adding a new attr `foo` → `TypeError` (blocked by slots) (PASS)
- Break 6 (slots / shape): `__dict__` absent; `__slots__ == ('name','description','body','source')`; `hasattr` False for `tools`/`mode`/`allow`/`deny` **and** `allow_rules`/`deny_rules`/`prompt` (PASS)
- Break 7 (value semantics): two equal SkillDefs compare `==` and hash-equal; usable as dict key (frozen+slotted → hashable) (PASS)
- Break 8 (store-raw behavior): leading/trailing whitespace is validated-stripped but **stored raw** (`name="  commit  "` kept verbatim) — identical to `AgentDef`; a contract note for the task-025 loader, not a defect (PASS with note)
- Break 9 (malformed type, out of contract): `name=None`/`name=123` raise `AttributeError` (`.strip()` on non-str), not `ValueError` — **identical to `AgentDef`**; ACs only mandate empty/whitespace-string handling and the loader feeds parsed-string frontmatter (PASS with note)
- Break 10 (large + unicode input): 2,000,000-char body and unicode name `naïve-skill-✓` / description `résumé` construct fine — no body-size cap is deferred by design (ADR-0004, task Out-of-scope) (PASS)

**Acceptance criteria**
- [x] PASS — frozen+slots, immutable round-trip — `@dataclass(frozen=True, slots=True)` at `src/decode/entities/skill_def.py:27`; `test_skill_def_carries_its_fields`, `test_skill_def_is_frozen_and_hashable`, `test_skill_def_uses_slots`; adversarial Break 5/6
- [x] PASS — empty/whitespace `name` → ValueError mentioning name — `test_skill_def_rejects_an_empty_name`; `skill_def.py:45-46`; adversarial Break 1/2/3
- [x] PASS — empty/whitespace `description` → ValueError — `test_skill_def_rejects_an_empty_description`; `skill_def.py:47-48`; adversarial Break 1/2
- [x] PASS — empty/whitespace `body` → ValueError — `test_skill_def_rejects_an_empty_body`; `skill_def.py:49-50`; adversarial Break 1/2
- [x] PASS — empty/whitespace `source` → ValueError — `test_skill_def_rejects_an_empty_source`; `skill_def.py:51-52`; adversarial Break 1/2
- [x] PASS — no `tools`/`mode`/`allow`/`deny` attrs — `test_skill_def_has_no_persona_or_rule_fields[tools|mode|allow|deny]`; adversarial Break 6 (all `hasattr` False, plus rule/prompt fields absent)
- [x] PASS — working-looks-like: constructs + `dataclasses.replace(it, name="")` raises — `test_skill_def_replace_with_empty_name_raises`; adversarial Break 4
- [x] PASS — `make ci` green, 0 warnings; test mirrors src 1:1 — `make ci` → 576 passed, 0 warnings; `tests/unit/decode/entities/test_skill_def.py` ↔ `src/decode/entities/skill_def.py`

**Evidence**
```
$ make ci
============================= 576 passed in 6.71s ==============================

$ uv run pytest tests/unit/decode/entities/test_skill_def.py -v
... 13 items ... 13 passed in 0.04s

$ make integration-tests
============================== 1 passed in 1.25s ===============================
```

**Other issues found**
- None blocking. Two PASS-with-note observations for the downstream loader (task 025), neither a defect and both identical to the mirrored `AgentDef`:
  1. Whitespace is stripped for validation but the raw value is stored, so a catalog `name:` like `"  commit  "` would persist surrounding spaces — the loader should `.strip()` frontmatter before constructing if the dispatcher key must be exact.
  2. Non-string inputs (`None`, `int`) raise `AttributeError`, not `ValueError`; fine while the loader guarantees parsed strings.
- The working tree also carries untracked PA artifacts (`docs/adr/0004-...md`, `tasks/025-029-*.md`) and a `docs/glossary.md` edit adding the Skill / Skills Catalog / Skill Dispatcher / Progressive Disclosure terms. These are feature-planning/docs for the Skills epic, not task-024 code, and this task has no glossary/ADR AC — noted, not flagged. Nothing is staged.

**VERDICT: PASS**
