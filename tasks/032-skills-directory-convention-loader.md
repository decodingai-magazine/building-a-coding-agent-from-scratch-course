---
id: 032-skills-directory-convention-loader
feature: skills-directory-convention
status: pending
---

# Skills: directory convention (`<name>/SKILL.md`) + `SkillDef.resource_dir` + migrate built-ins

Implements [ADR-0004](../docs/adr/0004-milestone-3-skills.md) §1,§3,§5,§6 (directory convention,
three-tier disclosure, `SkillDef.resource_dir`).
Depends on: 031 · Blocks: 033, 034

## Scope

Hard-switch the Skills loader from the flat `skills/<name>.md` format to the **Agent Skills directory
convention**: a skill is a directory `<name>/SKILL.md`, and a project skill may ship sibling resource
folders/files (`references/`, `examples/`, `scripts/`, or anything) alongside its `SKILL.md`. The
loader recognizes **only** `<name>/SKILL.md` directories — flat `*.md` support is dropped entirely (no
back-compat). Built-ins remain **SKILL.md-only** (ADR-0004 §3); `resource_dir` is set only for a
**project** skill whose directory contains something besides `SKILL.md`. This task is pure
load/entity/migration — the resource **trailer** (033) and the tier-3 **capstone demo** (034) are not
in this task. Every invariant from ADR-0004 is preserved; the only changes are the on-disk shape and
the new optional field.

This is a **hard switch**, so its blast radius includes every test that writes a project skill on disk
as a flat `<name>.md`. Those fixtures (unit + integration) are migrated to `<name>/SKILL.md` **in this
task** so `make ci` stays green at this commit; no skills test is deleted wholesale.

### `src/decode/entities/skill_def.py`
- Add an **optional** field `resource_dir: Path | None = None` (defaulted, so existing construction
  stays compatible; `from __future__ import annotations` + a `from pathlib import Path` import). It
  holds the project skill's bundled-resource directory **iff** the skill ships resources, else `None`
  (built-ins and resource-less project skills → `None`). Update the class + module docstrings to name
  the new tier-3 role and the directory convention (`<name>/SKILL.md`).
- Keep the existing `__post_init__` validation of `name`/`description`/`body`/`source` exactly as is.
  Do **not** validate `resource_dir` against the filesystem (the entity is frozen + slotted and must
  not touch disk; the loader sets it correctly). `resource_dir` accepts `None` or any `Path`.

### `src/decode/skills/loader.py`
- `parse_skill_file(text, source, resource_dir=None)` — thread an **optional** `resource_dir` argument
  through into the constructed `SkillDef`. Everything else unchanged: `split_frontmatter` + `FENCE`,
  `yaml.safe_load`, `_require_str("name")` / `_require_str("description")`, `body.strip()`,
  `source.strip()`, name from the frontmatter `name:`.
- `load_builtin_skills()` — scan `builtin/*/SKILL.md` (a built-in is now a **subdirectory** of
  `decode.skills.builtin` containing a `SKILL.md`), via `importlib.resources` **nested traversal**:
  ```python
  package = importlib.resources.files(_BUILTIN_PACKAGE)          # decode.skills.builtin
  for entry in sorted(package.iterdir(), key=lambda e: e.name):
      if not entry.is_dir():
          continue                                               # skip __init__.py, etc.
      skill_file = entry / "SKILL.md"                            # Traversable.joinpath
      if not skill_file.is_file():
          logger.debug("skipping built-in dir without SKILL.md: %s", entry.name)
          continue                                               # also silences __pycache__
      text = skill_file.read_text(encoding="utf-8")
      ...
  ```
  `Traversable` supports `iterdir` / `is_dir` / `joinpath` (`/`) / `is_file` / `read_text` for both
  filesystem and wheel/zip resources. Built-ins are **always** `resource_dir=None` (ADR-0004 §3 —
  their resources would live in site-packages, unreadable by the model's `read` tool). A built-in
  parse failure still **raises loudly** (packaging bug); replace the old `_builtin_files()` `*.md`
  helper with the directory walk.
- `discover_project_skills(cwd)` — scan `<cwd>/<settings.skills_dir>/*/SKILL.md` (each **subdirectory**
  that contains a `SKILL.md`), sorted by directory name for deterministic merge order:
  - For each project skill directory, set `resource_dir` to that directory **iff** it contains any
    entry other than `SKILL.md` (any sibling file or folder → it has bundled resources); otherwise
    `None`. Store `resource_dir` in a form the `read`/`bash` tools resolve (033 surfaces it): the
    directory **as joined under `cwd`** — i.e. do **not** `.resolve()` it — so 033 can render it
    cwd-relative exactly (`source` keeps using `str(path.resolve())` as today; `resource_dir` does not).
  - `source` is the absolute path of the `SKILL.md` file (`str((sub / "SKILL.md").resolve())`).
  - A subdirectory **lacking** `SKILL.md` is skipped (log at DEBUG/WARNING naming the dir) — never a
    crash. A missing skills dir → `{}` (unchanged).
  - Preserve the **malformed-YAML / unreadable skip** exactly: keep catching
    `(ValueError, OSError, yaml.YAMLError)` and logging a WARNING then `continue` (task 030 invariant) —
    a typo'd project `SKILL.md` is skipped, never propagated to the live session.
  - When a directory's name differs from its frontmatter `name`, the skill **still loads** (keyed by
    the frontmatter `name` — the directory name is cosmetic) but the mismatch is logged at **WARNING**
    to catch copy-paste slips. Never an error.
  - Optional, non-fatal nicety (keep simple, do not over-build): a DEBUG log when a **loose** `*.md`
    sits directly under `skills_dir` (flat skills are no longer supported) to aid migration.
- `load_skills(cwd)` — **unchanged**: built-ins first, `dict.update` the project skills, project
  overrides a built-in of the same frontmatter `name` (intentional, silent, `source`-traceable).

### Migrate the two built-ins on disk (bodies unchanged)
- `src/decode/skills/builtin/commit.md` → `src/decode/skills/builtin/commit/SKILL.md`
- `src/decode/skills/builtin/review-diff.md` → `src/decode/skills/builtin/review-diff/SKILL.md`
- Body + frontmatter **byte-identical** to the current files (commit = active, review-diff = advisory).
  The subdirectories `commit/` and `review-diff/` are resource dirs, **not** Python packages — do
  **not** add `__init__.py` to them (`importlib.resources` traverses them without it). Keep
  `builtin/__init__.py` (the package docstring) as is.

### `src/decode/config/settings.py`
- No change: `skills_dir` stays `Path(".decode/skills")` (it now contains subdirectories). Note it; do
  not re-spec the path.

### Tests (migrate the flat→directory blast radius; keep every test, update fixtures)
- `tests/unit/decode/entities/test_skill_def.py` — add coverage for the optional `resource_dir`:
  defaults to `None`, accepts a `Path`, leaves the existing field validation intact (a `None` or
  `Path` resource_dir never trips `__post_init__`).
- `tests/unit/decode/skills/test_loader.py` — rewrite the `_write_skill` helper to write
  `<skills_dir>/<dir>/SKILL.md`; update every call site (renamed-name keying, sorted order,
  skip-malformed, override-by-name, the directory glob). Add: built-in load via `*/SKILL.md` returns
  `{"commit","review-diff"}` each with `resource_dir is None`; a project skill **with** a sibling
  (e.g. `references/x.md`) gets `resource_dir` set to its directory; a project skill with **only**
  `SKILL.md` gets `resource_dir is None`; a subdir **without** `SKILL.md` is skipped; a dir-name ≠
  frontmatter-name mismatch loads + logs a WARNING; the malformed-YAML project `SKILL.md` skip holds.
- `tests/unit/decode/skills/test_catalog.py` — update its `_write_skill` to the directory layout. The
  catalog **behavior is unchanged** (name + description menu, sorted, `""`-when-empty, whitespace
  collapse) — assertions stay; only the fixture layout changes. Catalog output must **not** contain
  any resource path (tier-1 stays paths-free).
- `tests/unit/decode/tools/test_skills.py` — update its project-skill fixture writer to the directory
  layout. No trailer yet (033), so the dispatcher still returns the plain `body`; assertions on the
  returned body stay (the project-override fixture writes only `SKILL.md` → `resource_dir is None`).
- `tests/unit/decode/tui/test_app.py` — update its `_write_skill` to the directory layout. No trailer
  yet; `_handle_skill_command` still returns the plain body; assertions stay.
- `tests/integration/test_milestone3_skills_capstone.py` — migrate its project-override fixture from
  `<cwd>/.decode/skills/commit.md` to `<cwd>/.decode/skills/commit/SKILL.md` so the capstone stays
  green under the new layout. (The tier-3 demo + no-trailer-builtin assertions are added in 034.)

## Acceptance criteria

- [ ] `SkillDef` gains an optional `resource_dir: Path | None = None`; constructing without it still
      works, existing `name`/`description`/`body`/`source` validation is unchanged, and a `None` or
      `Path` `resource_dir` never raises. Unit-tested in `test_skill_def.py`.
- [ ] `load_builtin_skills()` returns exactly `{"commit", "review-diff"}` by scanning
      `builtin/*/SKILL.md` via `importlib.resources` nested traversal; each built-in has its original
      `name`/`description`, the **byte-identical** body (commit mentions `git add` + `git commit`;
      review-diff mentions `git diff` and **not** `git commit`), `source == "builtin"`, and
      `resource_dir is None`. Unit-tested.
- [ ] A built-in `<name>/` directory that lacks a `SKILL.md` is skipped (logged), and a built-in
      `SKILL.md` parse failure still **raises loudly** (packaging-bug asymmetry preserved). Unit-tested.
- [ ] `discover_project_skills(cwd)` discovers `<cwd>/.decode/skills/*/SKILL.md`, keyed by **frontmatter
      `name`** (a directory `foo/` whose `SKILL.md` has `name: bar` keys as `bar`; dir name is cosmetic),
      with `source` set to the absolute `SKILL.md` path; a missing skills dir → `{}`. Unit-tested.
- [ ] A project skill whose **directory name differs from its frontmatter `name`** still loads (keyed by
      the frontmatter `name`; directory name is cosmetic) and logs the mismatch at **WARNING**.
      Unit-tested.
- [ ] `resource_dir` is set to the skill's directory **iff** that directory contains any entry besides
      `SKILL.md` (a project skill with `references/x.md` → `resource_dir` set; a project skill with only
      `SKILL.md` → `resource_dir is None`), and is stored cwd-relatively-resolvable (un-`.resolve()`d,
      cwd-joined) so 033 can surface it. Built-ins are always `None`. Unit-tested.
- [ ] A subdirectory without a `SKILL.md`, and a malformed/unreadable project `SKILL.md` (incl. a
      YAML-scanner error — `yaml.YAMLError`), are skipped with a WARNING and the other project skills
      still load (the task-030 skip invariant holds for the directory layout). Unit-tested.
- [ ] `load_skills(cwd)` still merges built-ins-then-project with **project-override-by-frontmatter-name**
      (the project `body`/`source`/`resource_dir` win; unoverridden built-ins and project-only skills
      both appear). Unit-tested.
- [ ] The flat format is **gone**: a loose `<cwd>/.decode/skills/commit.md` (no enclosing `<name>/`
      directory) is **not** discovered (hard switch). Unit-tested.
- [ ] The two built-ins live at `src/decode/skills/builtin/commit/SKILL.md` and
      `src/decode/skills/builtin/review-diff/SKILL.md`, bodies byte-identical to the pre-refactor files;
      the old `builtin/commit.md` / `builtin/review-diff.md` are removed; no `__init__.py` is added to
      the skill subdirectories.
- [ ] The built-in `SKILL.md` files ship in the wheel: `uv build` + `unzip -l dist/*.whl | grep
      skills/builtin` shows `decode/skills/builtin/commit/SKILL.md` and `.../review-diff/SKILL.md`. If
      hatchling omits the nested data files, add an explicit wheel `artifacts`/`include` glob
      (e.g. `src/decode/skills/builtin/**/*.md`) — **not** stray `__init__.py` files — and re-verify.
- [ ] Every existing skills test is **updated, not deleted**: `test_loader.py`, `test_catalog.py`,
      `tools/test_skills.py`, `tui/test_app.py`, and `test_milestone3_skills_capstone.py` write project
      skills as `<dir>/SKILL.md`; their unchanged-behavior assertions still pass.
- [ ] The tier-1 catalog (`skills/catalog.py`) is **unchanged** and its output contains **no** resource
      path (paths-free always-on prompt); the whitespace-collapse prompt-injection defense is intact.
- [ ] `make ci` is green with 0 warnings (`filterwarnings=["error"]`); `tests/unit/decode/skills/`
      mirrors `src/decode/skills/` 1:1.

## Out of scope
- The resource **trailer** (body + path surfacing) in the dispatcher and the `/<skill-name>` TUI path —
  task 033.
- The tier-3 capstone proof (a project skill with `references/<file>.md`, the model `read`-ing the
  bundled file, the built-in-no-trailer assertion) — task 034.
- The deferred ADR-0004 §10 items (user-home source, per-agent allowlists, body-size cap, dispatcher
  `args`) — unchanged.

## Log
