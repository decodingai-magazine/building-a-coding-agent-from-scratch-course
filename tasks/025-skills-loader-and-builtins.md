---
id: 025-skills-loader-and-builtins
feature: skills
status: pending
---

# Skills: loader, project discovery, project-override merge + the two built-in skills

Implements [ADR-0004](../docs/adr/0004-milestone-3-skills.md) (skill sources + precedence + built-ins).
Depends on: 024 · Blocks: 026, 028

## Scope

Load + validate skills from two sources and merge them (project-local overrides built-in by name).
Mirror `src/decode/agents/loader.py` (packaged-data via `importlib.resources`) and
`src/decode/memory/files.py` (cwd-relative discovery, skip-unreadable). Pure load/merge — no
dispatcher (026), catalog injection (027), or TUI invocation (028) yet.

- **`config/settings.py`** — add `skills_dir: Path = Path(".decode/skills")` (mirror `sessions_dir`
  / `permissions_file`; the single config reader for the project-skills location). Mirror it in
  **`.env.example`** with a commented `# SKILLS_DIR=.decode/skills` line + one-line explanation.
- **`skills/__init__.py`** — package docstring + re-exports (`load_skills`, `load_builtin_skills`,
  `discover_project_skills`, `parse_skill_file`), mirroring `agents/__init__.py`.
- **`skills/builtin/__init__.py`** — package docstring only (makes `builtin` an importable package so
  `importlib.resources` loads it and hatchling ships the `.md`), mirroring
  `agents/builtin/__init__.py`. **Verify** the `.md` files land in the wheel by default (hatchling
  includes every file under the package dir — confirmed for `agents/builtin`; no `pyproject` change
  expected — confirm with `uv build` + `unzip -l`).
- **`skills/builtin/commit.md`** (ACTIVE — stages + commits) and **`skills/builtin/review-diff.md`**
  (advisory / read-only) — frontmatter (`name` + `description`) + a Markdown body of instructions
  (NOT executable code). Drafts below.
- **`skills/loader.py`**:
  - `parse_skill_file(text, source) -> SkillDef` — split the leading `---`-fenced YAML frontmatter
    from the body (reuse the agent loader's `_split_frontmatter` shape), require `name` +
    `description`, and let `SkillDef` validate the rest. The skill's name is the frontmatter `name:`
    (ADR-0004 §3); the filename is cosmetic. `source` is passed in by the caller (the one deliberate
    deviation from the `parse_agent_file(text)` shape — `SkillDef` carries provenance).
  - `load_builtin_skills() -> dict[str, SkillDef]` — read + validate every `builtin/*.md` via
    `importlib.resources.files("decode.skills.builtin")`, keyed by frontmatter name, `source="builtin"`.
    A built-in **parse failure raises loudly** (our packaging bug), like `load_builtin_agents`.
  - `discover_project_skills(cwd) -> dict[str, SkillDef]` — scan `cwd / settings.skills_dir` for
    `*.md`, parse each with `source=<absolute file path>`, keyed by frontmatter name. A **malformed or
    unreadable** project skill is logged at WARNING and skipped (never crashes the agent — mirror
    memory's skip-unreadable). A missing skills dir → empty dict.
  - `load_skills(cwd) -> dict[str, SkillDef]` — built-ins first, then `dict.update` the project skills
    so a **project skill whose frontmatter name equals a built-in's intentionally overrides** it
    (most-specific wins; the silent override is acceptable — `source` keeps it traceable, ADR-0004 §3).

### Built-in skill drafts

`skills/builtin/commit.md` (ACTIVE — stages and commits autonomously):

```
---
name: commit
description: Stage the appropriate changes and commit them with a Conventional Commits message.
---
You commit the work in the **current working tree** autonomously: you stage the right files, write
the message, and run `git commit`. You commit exactly what you stage.

1. Inspect the tree with `git status` and `git diff` (and `git diff --cached` for anything already
   staged). If there is nothing to commit, say so and stop.
2. Stage the changes that belong in this commit with `git add`. Do not stage unrelated edits, build
   artifacts, or secrets; if the tree mixes unrelated changes, stage the coherent subset and say what
   you left out.
3. Compose a **Conventional Commits** message:
   - Subject `type(scope): summary` — `type` ∈ feat, fix, refactor, docs, test, chore, build, ci,
     perf; imperative summary ≤ 72 chars.
   - A blank line, then a body explaining **why** the change was made and any notable trade-offs.
4. Run `git commit` with that message, committing exactly what you staged. Describe only changes you
   actually saw in the diff; never invent them. Report the resulting commit subject.
```

`skills/builtin/review-diff.md` (advisory — read-only, unchanged):

```
---
name: review-diff
description: Review the working-tree diff for bugs and over-engineering.
---
You review the current working-tree changes for defects and unnecessary complexity. You do not edit
the code and you do not commit — you report.

1. Read the change with `git diff` (and `git diff --cached` for staged work). Ground every comment in
   a specific `file:line` you actually read.
2. Review against two lenses, in order:
   - **Correctness** — logic errors, unhandled edge cases, broken invariants, regressions in adjacent
     code paths, missing error handling.
   - **Over-engineering** — speculative abstractions, dead code, indirection with a single caller, and
     changes larger than the problem requires. Prefer the smallest change that works.
3. Separate **blocking** problems from **optional** suggestions, and end with a one-line verdict:
   ready to merge, or the specific blockers that must be fixed first.
```

## Acceptance criteria

- [ ] `settings.skills_dir` defaults to `Path(".decode/skills")` and is mirrored (commented) in
      `.env.example`; the loader reads the path only via the `settings` singleton (no literal path).
- [ ] `load_builtin_skills()` returns exactly `{"commit", "review-diff"}`, each with the right
      `name`/`description`, a non-empty `body`, and `source == "builtin"`. Unit-tested.
- [ ] The **commit** skill body is ACTIVE: it instructs staging (`git add`) and running `git commit`
      on the current working tree (asserted by substring on the loaded body — e.g. it mentions
      `git add` and `git commit`). The **review-diff** body stays advisory/read-only (mentions
      `git diff`, not `git commit`). Unit-tested. (Do **not** assert skills "never mutate".)
- [ ] `parse_skill_file(text, source)` splits frontmatter/body, requires `name` + `description`,
      derives the skill name from the `name:` frontmatter, and raises a clear `ValueError` on missing
      frontmatter / missing key / unclosed fence. Unit-tested.
- [ ] `discover_project_skills(cwd)` finds `<cwd>/.decode/skills/*.md`, keys them by **frontmatter
      name** (a file `foo.md` whose frontmatter is `name: bar` keys as `bar`), with `source` set to
      the absolute file path; a missing dir returns `{}`. Unit-tested.
- [ ] A **malformed or unreadable** project skill is skipped with a WARNING log and the other project
      skills still load (no crash). Unit-tested. A **built-in** parse failure instead raises (tested
      via `parse_skill_file` on bad text — the shipped built-ins are valid).
- [ ] `load_skills(cwd)` merges with **project-override-by-name**: a project skill whose frontmatter
      `name` equals `commit` replaces the built-in `commit` (its `body` and `source` become the
      project file's), while unoverridden built-ins and project-only skills both appear. Unit-tested.
- [ ] The `builtin/*.md` load via the **installed package** (packaged data, `importlib.resources`),
      not a repo path — a test loads them through the package; `uv build` + `unzip -l` shows both
      `.md` in the wheel.
- [ ] **Working looks like:** write `<tmp>/.decode/skills/commit.md` with a different body →
      `load_skills(tmp)["commit"].body` is the project body and `.source` is that file's path.
- [ ] `make ci` green, 0 warnings; `tests/unit/decode/skills/` mirrors `src/decode/skills/`.

## Out of scope
- The `skill` dispatcher tool + registry wiring + adding `skill` to the agents — task 026.
- Catalog assembly / prompt injection — task 027.
- The `/<skill-name>` TUI invocation — task 028.
- A `~/.decode/skills` user-home source (deferred, ADR-0004).
- A body-size cap (deferred, ADR-0004).

## Log
### [PA] 2026-06-25 — Grooming
Mirrors `agents/loader.py` (packaged-data) + `memory/files.py` (cwd-relative, skip-unreadable). The
override rule (project wins by **frontmatter name**) is the same "most-specific wins" memory uses;
the silent same-name override is intentional (ADR-0004 §3), with `source` keeping provenance in logs.
`skills_dir` lands here — not the later catalog task — because the loader is its first reader
(single-config-reader rule). `parse_skill_file` takes a `source` arg (the one deviation from
`parse_agent_file`) so provenance is set at parse time. **Round-2 delta:** the built-in `commit`
skill is now ACTIVE — it stages and runs `git commit` (the git ops ride the gated `bash` tool, so the
gate still governs them — see ADR-0004 §7); `review-diff` stays advisory. ACs assert the active body
content rather than any "never mutates" claim. Built-in failure raises; project failure is skipped —
same asymmetry as the agents loader vs the user settings.json.
