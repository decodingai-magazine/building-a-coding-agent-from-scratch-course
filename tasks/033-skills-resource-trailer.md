---
id: 033-skills-resource-trailer
feature: skills-directory-convention
status: pending
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

- [ ] `format_skill_payload(skill, cwd=…)` returns the body **unchanged** when `skill.resource_dir is
      None`, and body **+ a trailer** when `resource_dir` is set. Unit-tested.
- [ ] The trailer names the resource directory as a **cwd-relative** path (directory only — no contents
      listing) and explains loading bundled files via `read` (and running `scripts/` via `bash`). The
      named path, joined under `cwd`, passes the `read` tool's containment check — a model can
      `read("<dir>/references/<file>")`. Unit-tested (assert `_resolve_in_cwd` accepts it, or perform a
      real gated `read`).
- [ ] The `skill(name)` dispatcher returns `format_skill_payload(...)`: a **built-in** (`commit`,
      `review-diff`) returns its body with **no** trailer; a project skill with bundled resources returns
      body **+ trailer**. The dispatcher stays **ungated** (no `ApprovalRequired`, no `PermissionRequested`)
      and unknown names still raise `ModelRetry` listing available skills. Unit-tested.
- [ ] The `/<skill-name>` TUI handler injects the **same** payload via the shared helper: built-in →
      body only; resource-bearing project skill → body + trailer; trailing user text is still appended
      after a blank line. Reserved-command precedence and the unknown-slash discovery line are unchanged.
      Unit-tested.
- [ ] The dispatcher and the TUI path produce an **identical** payload for the same skill + cwd (one
      helper, no divergence). Unit-tested (both call sites compared, or both asserted against the helper).
- [ ] The tier-1 catalog still contains **no** resource paths (paths stay out of the always-on prompt);
      `skills/catalog.py` is unchanged.
- [ ] `make ci` is green, 0 warnings; `tests/unit/decode/skills/test_payload.py` mirrors
      `src/decode/skills/payload.py`.

## Out of scope
- The integration capstone update + the tier-3 end-to-end proof (model `read`s a real bundled file) —
  task 034.
- Surfacing resources for **built-in** skills (ADR-0004 §3: built-ins are SKILL.md-only → always no
  trailer).
- Any change to the catalog, the loader, or `SkillDef` (landed in 032).

## Log
