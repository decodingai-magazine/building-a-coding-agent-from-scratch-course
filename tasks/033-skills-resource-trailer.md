---
id: 033-skills-resource-trailer
feature: skills-directory-convention
status: done
---

# Skills: resource trailer — shared body(+trailer) helper for the dispatcher AND the `/<skill>` TUI path

Implements [ADR-0004](../docs/adr/0004-milestone-3-skills.md) §1,§5 (tier-3 path surfacing via the
dispatcher trailer; the two entry points share one `format_skill_payload`).
Depends on: 032 · Blocks: 034

## Scope

Surface a skill's bundled-resource directory to the model so it can load tier-3 files on demand. When a
skill is invoked, the returned payload is its `SKILL.md` body **plus a short trailer** that names the
skill's directory and how to load bundled files — but the trailer is appended **only when the skill has
bundled resources** (`resource_dir is not None`). A built-in (SKILL.md-only) and a resource-less project
skill get **no trailer**. The **same** trailer rides both invocation paths — the model's `skill(name)`
dispatcher and the user's `/<skill-name>` TUI command — through one shared helper, so the two paths can
never diverge. The tier-1 catalog is untouched (no paths in the always-on prompt).

### Shared helper — `src/decode/skills/payload.py` (new module)
- `format_skill_payload(skill: SkillDef, *, cwd: Path) -> str` — returns `skill.body` unchanged when
  `skill.resource_dir is None`; otherwise returns `body` + the resource trailer. The trailer:
  - is appended after the body with a blank-line separator;
  - names the directory as a **`read`/`bash`-resolvable path** — render `resource_dir` **relative to
    `cwd`** (e.g. `os.path.relpath(skill.resource_dir, cwd)` or `resource_dir.relative_to(cwd)`, which
    is exact because 032 stored `resource_dir` un-`.resolve()`d and cwd-joined), yielding e.g.
    `.decode/skills/pdf-export`;
  - tells the model exactly how to load bundled files. Recommended wording (SWE may refine; ACs pin
    behavior, not exact prose):
    ```
    Bundled files for this skill are under `<dir>/` — read them with the `read` tool, run `scripts/` with `bash`.
    ```
    The trailer names the **directory only** — it does not enumerate the directory's contents.
  - The surfaced `<dir>` path, joined under `cwd`, must satisfy the `read` tool's containment check
    (`decode.tools.files._resolve_in_cwd`) so the model can `read("<dir>/references/foo.md")` (project
    skill dirs live under `cwd`, so a cwd-relative path resolves cleanly — confirm in a test).
- Keep it tiny and synchronous (string formatting only). One concept, two callers (the dispatcher and
  the TUI) — exactly the "abstract on the second concrete caller" rule.

### `src/decode/tools/skills.py`
- `skill(ctx, name)` — after resolving `found = load_skills(ctx.deps.cwd).get(name)`, return
  `format_skill_payload(found, cwd=ctx.deps.cwd)` instead of `found.body`. Unknown-name `ModelRetry`
  behavior, ungated contract (never raises `ApprovalRequired`), and the name-as-dict-key-only safety
  are all unchanged. Update the module docstring to mention the resource trailer.

### `src/decode/tui/app.py`
- `_handle_skill_command(name, trailing, *, cwd, emit)` — on a match, build the turn input from
  `format_skill_payload(found, cwd=cwd)` (body + optional trailer) instead of `found.body`; a non-empty
  `trailing` is still appended after a blank line (`f"{payload}\n\n{trailing}"`). The unknown-`/<x>`
  discovery line and reserved-command precedence (`/quit` / `/agent` / `/mode` win) are unchanged.
  `parse_skill_command` is unchanged.

### Tests
- `tests/unit/decode/skills/test_payload.py` (new, mirrors `src/decode/skills/payload.py`) — pin:
  `resource_dir is None` → payload **is** the body, no trailer; `resource_dir` set → payload is
  `body` + trailer, the trailer names the **cwd-relative** dir, and the surfaced path resolves under
  `cwd` (assert via `decode.tools.files._resolve_in_cwd(cwd, "<dir>/references/<f>")` succeeding, or by
  a real `read` of a written bundled file).
- `tests/unit/decode/tools/test_skills.py` — add: a project skill **with** a bundled resource → the
  dispatcher returns body **+ trailer**; a built-in (`commit`) and a resource-less project skill →
  body **only, no trailer**. Existing assertions updated to use the helper's output where relevant.
- `tests/unit/decode/tui/test_app.py` — add: `/<skill>` for a resource-bearing project skill injects
  body **+ trailer** (plus any trailing text after a blank line); `/commit` (built-in) injects the body
  **without** a trailer.

## Acceptance criteria

- [x] `format_skill_payload(skill, cwd=…)` returns the body **unchanged** when `skill.resource_dir is
      None`, and body **+ a trailer** when `resource_dir` is set. Unit-tested.
- [x] The trailer names the resource directory as a **cwd-relative** path (directory only — no contents
      listing) and explains loading bundled files via `read` (and running `scripts/` via `bash`). The
      named path, joined under `cwd`, passes the `read` tool's containment check — a model can
      `read("<dir>/references/<file>")`. Unit-tested (assert `_resolve_in_cwd` accepts it, or perform a
      real gated `read`).
- [x] The `skill(name)` dispatcher returns `format_skill_payload(...)`: a **built-in** (`commit`,
      `review-diff`) returns its body with **no** trailer; a project skill with bundled resources returns
      body **+ trailer**. The dispatcher stays **ungated** (no `ApprovalRequired`, no `PermissionRequested`)
      and unknown names still raise `ModelRetry` listing available skills. Unit-tested.
- [x] The `/<skill-name>` TUI handler injects the **same** payload via the shared helper: built-in →
      body only; resource-bearing project skill → body + trailer; trailing user text is still appended
      after a blank line. Reserved-command precedence and the unknown-slash discovery line are unchanged.
      Unit-tested.
- [x] The dispatcher and the TUI path produce an **identical** payload for the same skill + cwd (one
      helper, no divergence). Unit-tested (both call sites compared, or both asserted against the helper).
- [x] The tier-1 catalog still contains **no** resource paths (paths stay out of the always-on prompt);
      `skills/catalog.py` is unchanged.
- [x] `make ci` is green, 0 warnings; `tests/unit/decode/skills/test_payload.py` mirrors
      `src/decode/skills/payload.py`.

## Out of scope
- The integration capstone update + the tier-3 end-to-end proof (model `read`s a real bundled file) —
  task 034.
- Surfacing resources for **built-in** skills (ADR-0004 §3: built-ins are SKILL.md-only → always no
  trailer).
- Any change to the catalog, the loader, or `SkillDef` (landed in 032).

## Log

### [SWE] 2026-06-26 — Implementation

**Files modified**
- `src/decode/skills/payload.py` (new) — `format_skill_payload(skill, *, cwd)`: body verbatim when
  `resource_dir is None`, else body + a blank-line-separated resource trailer naming the cwd-relative
  directory (`os.path.relpath`), directory only.
- `src/decode/tools/skills.py` — `skill()` returns `format_skill_payload(found, cwd=ctx.deps.cwd)`
  instead of `found.body`; module + function docstrings updated to mention the trailer. Ungated /
  ModelRetry / name-as-dict-key contract unchanged.
- `src/decode/tui/app.py` — `_handle_skill_command` builds the turn input from the shared helper
  (`payload = format_skill_payload(found, cwd=cwd)`), then appends non-empty `trailing` as
  `f"{payload}\n\n{trailing}"`. Discovery line + reserved precedence + parser unchanged.
- `tests/unit/decode/skills/test_payload.py` (new) — mirrors the new module: None → body verbatim;
  set → body + trailer; trailer names the cwd-relative dir only; surfaced path passes
  `files._resolve_in_cwd` and resolves to a real on-disk bundled file.
- `tests/unit/decode/tools/test_skills.py` — dispatcher: resource-bearing project skill → body +
  trailer (== helper output); built-in `commit` → body only; resource-less project skill → body only.
- `tests/unit/decode/tui/test_app.py` — `/<skill>` resource-bearing → body + trailer (+ trailing after
  a blank line); `/commit` built-in → body only; dispatcher and TUI payloads asserted identical for the
  same skill + cwd.
- `tests/unit/decode/skills/test_catalog.py` — pins that the tier-1 catalog of a resource-bearing skill
  carries no resource path (`catalog.py` unchanged).

**How the read-resolvable path is surfaced**
The trailer renders `resource_dir` via `os.path.relpath(skill.resource_dir, cwd)`. Task 032 stores
`resource_dir` cwd-joined and un-`.resolve()`d (e.g. `cwd/.decode/skills/<name>`), so the relative form
is exact (`.decode/skills/<name>`). Joined back under cwd by `read`'s `_resolve_in_cwd`
(`(cwd.resolve() / raw).resolve()` + containment), it stays inside the tree — verified both by a unit
test asserting `_resolve_in_cwd(cwd, "<dir>/references/guide.md")` and by a real gated `read` of a
written bundled file.

**Tests**
- Unit: 690 passing, 0 failing (`make pre-commit` / `make ci`). New: 4 payload + 3 dispatcher + 4 TUI +
  1 catalog.
- Integration: 6 passing (`make integration-tests`) — M1 + M3 capstones unaffected.

**Acceptance criteria** — all non-`[HUMAN]` criteria checked above; verified by:
- payload contract → `tests/unit/decode/skills/test_payload.py`
- read-resolvability → `test_payload.py::test_surfaced_path_resolves_under_cwd_via_the_read_containment_check`
- dispatcher → `tests/unit/decode/tools/test_skills.py::test_skill_with_a_bundled_resource_returns_body_plus_trailer` (+ built-in / resourceless / ungated)
- TUI → `tests/unit/decode/tui/test_app.py::test_handle_skill_command_resource_bearing_skill_injects_body_plus_trailer` (+ trailing / built-in)
- identical payloads → `test_app.py::test_dispatcher_and_tui_produce_identical_payloads_for_the_same_skill`
- tier-1 clean → `tests/unit/decode/skills/test_catalog.py::test_tier1_catalog_carries_no_resource_path_for_a_resource_bearing_skill`

**Evidence**
```
$ make ci
... uv lock --check + ruff format --check + ruff check + pytest ...
============================= 696 passed in 7.80s ==============================
```
End-to-end (real dispatcher + real gated `read`, no network):
```
=== dispatcher payload ===
Use the reference guide.

Bundled files for this skill are under `.decode/skills/pdf-export/` — read them with the `read` tool, run `scripts/` with `bash`.

=== read(<dir>/references/guide.md) ===
1	# Guide
2	Step 1.
dispatcher == helper: True
```

**Notes**
- Used `os.path.relpath` for the cwd-relative dir (the task listed it or `relative_to` as
  interchangeable; relpath returns a string directly and yields forward-slash on posix).
- Built-ins and resource-less project skills get the body verbatim — no trailing whitespace or marker
  added — so 032/earlier body-equality assertions still hold.
- Out of scope per the task: the integration capstone update + the tier-3 e2e proof (task 034). DO NOT
  commit — handing off to the Tester.

### [Tester] 2026-06-26 12:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 111 files clean; `ruff check` clean)
- Unit tests: 690 passed / 0 failed
- Integration tests: 6 passed / 0 failed
- `make ci` (uv lock --check + format + lint + full suite): 696 passed
- Warnings: 0 (`filterwarnings=["error"]` in effect — any warning would have errored the run)

**E2E adversarial pass** (`scratchpad/adv033.py` — real helper + dispatcher + TUI handler + gated
`read`, no network; 47/47 probes green)
- Happy path: `format_skill_payload(resource-bearing skill, cwd)` →
  ``…body…\n\nBundled files for this skill are under `.decode/skills/deploy/` — read them with the
  `read` tool, run `scripts/` with `bash`.`` (PASS)
- Break path 1 (boundary: `resource_dir is None`): payload IS the body byte-for-byte — no trailer, no
  trailing whitespace added, verbatim even when the body ends in `\n` (PASS)
- Break path 2 (security: path leak / listing): trailer is cwd-relative `.decode/skills/deploy` only —
  no absolute path, no `/private`, no `$HOME`, and NO directory-contents listing (`guide.md` /
  `references` / `run.sh` all absent from the trailer) (PASS)
- Break path 3 (read-resolvability): real gated `read(".decode/skills/deploy/references/guide.md")` →
  returned `# Guide` / `Step 1.`; nested `…/deep/more/n.md` → `nested!`; spaced-dir skill
  `…/notes.md` → `spaced-resource`; `_resolve_in_cwd` containment accepts each (PASS)
- Break path 4 (state/precedence): dispatcher built-ins `commit` + `review-diff` → body only; unknown
  name → `ModelRetry` listing skills; TUI `/commit` → body only, `/deploy to prod` → trailer THEN
  blank line THEN `to prod`, whitespace-only trailing → no append, unknown `/zzz` → discovery line +
  no turn; `/mode plan` still parses as the reserved command (PASS)
- Break path 5 (no divergence): dispatcher payload == TUI payload asserted for `deploy`, `commit`,
  `review-diff`, `mode` (PASS)
- Break path 6 (edges): `resource_dir == cwd` → ``under `./` `` (no crash); dotfile-only sibling and
  empty-subdir-only sibling → `resource_dir` set (trailer); mismatched-cwd (not reachable via the two
  real call sites) yields a `..` relpath that `read`'s containment then REJECTS — defence-in-depth
  holds (PASS)

**Acceptance criteria**
- [x] PASS — body unchanged when `resource_dir is None`, body + trailer when set —
      `test_payload.py::{test_payload_is_the_body_unchanged_when_no_resource_dir,
      test_payload_appends_a_trailer_when_resource_dir_is_set}`; adversarial break paths 1-2
- [x] PASS — trailer names the cwd-relative dir (directory only, no listing), explains `read`/`bash`,
      and the named path passes the `read` containment check (real gated `read` returns the bundled
      file) — `test_payload.py::{test_trailer_names_the_cwd_relative_directory_only,
      test_surfaced_path_resolves_under_cwd_via_the_read_containment_check}`; adversarial break paths 2-3
- [x] PASS — dispatcher returns `format_skill_payload(...)`: built-in (`commit`/`review-diff`) no
      trailer, resource-bearing project skill body + trailer, ungated (no `ApprovalRequired`/
      `PermissionRequested`, callable in plan mode), unknown → `ModelRetry` —
      `test_skills.py::{test_skill_with_a_bundled_resource_returns_body_plus_trailer,
      test_skill_builtin_returns_body_only_no_trailer, test_skill_resourceless_project_skill_returns_body_only,
      test_skill_through_the_loop_returns_the_body_and_is_ungated, test_skill_is_callable_in_plan_mode}`;
      adversarial break path 4
- [x] PASS — TUI `/<skill>` injects the same payload (built-in body only; resource-bearing body +
      trailer; trailing appended after a blank line after the trailer; reserved precedence + unknown
      discovery unchanged) — `test_app.py::{test_handle_skill_command_resource_bearing_skill_injects_body_plus_trailer,
      test_handle_skill_command_resource_bearing_skill_appends_trailing_after_trailer,
      test_handle_skill_command_builtin_injects_body_without_a_trailer}`; adversarial break path 4
- [x] PASS — dispatcher and TUI produce an identical payload for the same skill + cwd —
      `test_app.py::test_dispatcher_and_tui_produce_identical_payloads_for_the_same_skill`; adversarial
      break path 5 (deploy/commit/review-diff/mode)
- [x] PASS — tier-1 catalog carries no resource paths; `skills/catalog.py` unchanged —
      `test_catalog.py::test_tier1_catalog_carries_no_resource_path_for_a_resource_bearing_skill`;
      `git status` shows `catalog.py` not modified
- [x] PASS — `make ci` green (696 passed), 0 warnings; `tests/unit/decode/skills/test_payload.py`
      mirrors `src/decode/skills/payload.py` (1:1 path)

**Evidence**
```
$ make ci
... uv lock --check + ruff format --check + ruff check ...
============================= 696 passed in 7.75s ==============================

$ uv run python scratchpad/adv033.py
==== SUMMARY ====
total=47 pass=47 fail=0
```

**Other issues found**
- None blocking. Note (not a defect): `format_skill_payload` trusts that `cwd` matches the tree
  `resource_dir` lives under — true for both real call sites (each passes the same `cwd` it gave
  `load_skills`). If they ever diverged, `os.path.relpath` would emit a `..` path; the `read` tool's
  `_resolve_in_cwd` containment check rejects such a path, so even that impossible case cannot escape
  the tree. Mentioning only for completeness; no change required.

**VERDICT: PASS**
