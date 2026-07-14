---
id: 118
feature: evals
status: done
---

# Demo skills 2–4: bug-hunt, terminal-arcade, data-detective

Depends on: none (no Opik; independent of the harness). Implements ADR-0017 §2 (Track A).

## Scope

Three `.decode/skills/demo-N-<slug>/` dirs, same shape as the existing
`demo-1-implement-substack-summarizer` (SKILL.md frontmatter `name`+`description` + body prompt +
optional `references/`):

- **demo-2-bug-hunt** — `references/buggy_repo/`: a tiny package (e.g. `stats.py` with an
  off-by-one in `median` and a sign bug in `variance`) + `test_stats.py` where 2 tests fail. Body:
  copy `references/buggy_repo/` into `./bug-hunt/`, run `uv run pytest` to see the failures, hunt
  with grep/read (LSP diagnostics will surface on edits), fix, rerun green.
- **demo-3-terminal-arcade** — prompt-only: build a playable Snake in pure-stdlib Python `curses`
  (~100 lines, zero deps) at `snake.py`; iterate by running it (import/syntax checks + short
  headless runs — the human plays the final result).
- **demo-4-data-detective** — `references/messy_sales.csv` (duplicate rows, mixed date formats,
  currency strings, missing values). Body: clean it, analyze, emit `report.md` + matplotlib PNG
  charts under `charts/` (use `uv run --with pandas,matplotlib` so no project deps change).

**Tests** (`tests/unit/…` wherever decode's skills-loader tests live): each new SKILL.md parses
through decode's real skills loader (frontmatter `name` matches convention, non-empty
description/body); fixture files exist where the body references them.

## Acceptance Criteria

- [x] Three skills appear in the Skills Catalog and load via `/demo-2-bug-hunt` etc. (loader
      unit test + rendered `assemble_skills_catalog` — evidence in the SWE log).
- [ ] [HUMAN] Manual spot-run of one demo end-to-end (needs a real model — no `GEMINI_API_KEY`/`.env`
      present in this environment).
- [x] demo-2's seeded repo genuinely fails 2 tests as committed; demo-4's CSV carries every listed
      mess. — verified by `tests/unit/decode/skills/test_demo_skills.py`.
- [x] No new project dependencies. — `pyproject.toml` untouched; demo-2 uses `uv run pytest`, demo-4
      uses `uv run --with pandas,matplotlib`.
- [x] `make ci` green. — 2045 passed, 2 skipped (both skipped only for missing `GEMINI_API_KEY`/
      `OPIK_API_KEY`, unrelated to this task) in 429s. Verified by the Tester.

## Out of scope

- demo-1 (exists, untouched). Demos 5–7 (119). Opik anything.

## Log

### [SWE] 2026-07-14 — Implementation

**Files modified**
- `.decode/skills/demo-2-bug-hunt/SKILL.md` — bug-hunt demo body (copy repo, run pytest, fix, rerun green).
- `.decode/skills/demo-2-bug-hunt/references/buggy_repo/stats.py` — seeded off-by-one in `median` + sign flip in `variance`.
- `.decode/skills/demo-2-bug-hunt/references/buggy_repo/test_stats.py` — 6-test spec; exactly 2 fail as committed.
- `.decode/skills/demo-3-terminal-arcade/SKILL.md` — prompt-only Snake (pure-stdlib `curses`, ~100 lines) demo body.
- `.decode/skills/demo-4-data-detective/SKILL.md` — clean/analyse/report demo body (`uv run --with pandas,matplotlib`).
- `.decode/skills/demo-4-data-detective/references/messy_sales.csv` — dupes + mixed dates + currency strings + missing values.
- `tests/unit/decode/skills/test_demo_skills.py` — loads all three through the real loader; asserts fixtures + mess types.

**Tests**
- Unit: 13 new passing (`test_demo_skills.py`); full suite 1932 passing, 0 failing via `make pre-commit`.
- Integration: N/A — no infra changes (demos are static `.decode/skills/` assets).

**Acceptance criteria**
- [x] Skills appear in the Catalog + load via the real loader — `test_demo_skills.py` + rendered `assemble_skills_catalog` (evidence below).
- [ ] [HUMAN] Manual model-driven spot-run of a demo — no `GEMINI_API_KEY`/`.env` in this environment.
- [x] demo-2 fails exactly 2 tests; demo-4 CSV carries every mess type — `test_demo_skills.py`.
- [x] No new project dependencies — `pyproject.toml` untouched.
- [ ] `make ci` green — unit portion green via `make pre-commit`; full `make ci` for the Tester.

**Evidence**
```
$ uv run pytest tests/unit/decode/skills/test_demo_skills.py -q
13 passed in 2.14s

$ make pre-commit   # ruff format+check, ruff check, full unit suite
All checks passed!
1932 passed in 118.66s

# demo-2 seeded repo, run on a fresh copy as the body instructs:
$ uv run --no-project pytest test_stats.py -q -o addopts=
FAILED test_stats.py::test_median_odd_length - assert 2 == 3
FAILED test_stats.py::test_variance_is_non_negative - assert -2.0 == 2.0
2 failed, 4 passed in 0.12s

# Skills Catalog the agent injects (assemble_skills_catalog(cwd='.')):
- demo-2-bug-hunt — Demo skill that hunts and fixes two seeded bugs in a tiny stats package until its test suite goes green.
- demo-3-terminal-arcade — Demo skill that builds a playable terminal Snake game in a single pure-stdlib Python curses file.
- demo-4-data-detective — Demo skill that cleans a messy sales CSV, analyses it, and emits a written report with matplotlib charts.
```

**Notes**
- `git check-ignore` clears every new fixture (`.decode/skills/` is un-ignored source); no `.py`/`.csv` fixture is gitignored.
- demo-2's `test_stats.py` lives under `.decode/`, outside pytest `testpaths` (`tests/unit`, `tests/integration`), so its 2 seeded failures never enter `make ci`.
- Uncommitted — handing to the Tester first per the pipeline.

### [Tester] 2026-07-14 10:45 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 295 files already formatted; `ruff check`: all checks passed)
- Unit tests: 1932 passed / 0 failed (`make pre-commit`, includes the 13 new `test_demo_skills.py` tests)
- Full `make ci` (lockfile check + format-check + lint-check + unit + integration): 2045 passed, 2 skipped / 0 failed, 429.14s. The 2 skips are pre-existing, unrelated live-model smokes (`test_observability_capstone.py`, `test_subagents_capstone.py`) gated on `OPIK_API_KEY`/`GEMINI_API_KEY`, which are absent in this environment — not caused by this change.
- Warnings: 0 (`filterwarnings = ["error"]` project-wide; no warnings surfaced in any run)

**E2E adversarial pass**
- Happy path — render the real Skills Catalog: `uv run python -c "from decode.skills.catalog import assemble_skills_catalog; from pathlib import Path; print(assemble_skills_catalog(Path('.')))"` → all 3 demos listed with correct names/descriptions alongside the 7 pre-existing skills, no collisions (PASS)
- Break path 1 (bug-hunt genuineness): copied `references/buggy_repo/` to a fresh scratch dir exactly as the SKILL.md body instructs, ran `uv run --no-project pytest -q -o addopts=` → `2 failed, 4 passed` (`test_median_odd_length: assert 2 == 3`, `test_variance_is_non_negative: assert -2.0 == 2.0`), matching the SWE's claim exactly. Then hand-fixed both bugs (`ordered[mid]` instead of `ordered[mid - 1]`; dropped the `-` sign in the variance sum) and reran → `6 passed`. Confirms both seeded bugs are real, single-root-cause, and solvable with the smallest correct change per the body's own instruction (PASS)
- Break path 2 (fixture never leaks into CI): `uv run pytest --collect-only -q` from repo root and grepped for `buggy_repo`/`demo-2` → zero hits; `pyproject.toml` `testpaths = ["tests/unit", "tests/integration"]` confirms `.decode/skills/.../test_stats.py` structurally cannot be collected by `make ci` (PASS)
- Break path 3 (demo-4 tooling command as literally written): ran `uv run --with pandas,matplotlib python <trivial-import-script>` verbatim as in the SKILL.md body from repo root → resolved/installed pandas 3.0.3 + matplotlib 3.11.0 in an isolated ephemeral env, `pyproject.toml`/`uv.lock` unchanged after (`git status --porcelain` empty for both) (PASS)
- Break path 4 (payload/dispatch coherence): rendered `format_skill_payload` for all three demos through the real loader — demo-2 and demo-4 get a bundled-files trailer with exact cwd-relative paths (`.decode/skills/demo-2-bug-hunt/references/buggy_repo/{stats,test_stats}.py`, `.decode/skills/demo-4-data-detective/references/messy_sales.csv`); demo-3 gets no trailer (`resource_dir is None`, matches its prompt-only design). The body's relative snippets (`cp -r references/buggy_repo/ ./bug-hunt/`) resolve correctly against the trailer-supplied cwd-relative path — a student/agent following the body literally lands on real files (PASS)

**Acceptance criteria**
- [x] PASS — Three skills appear in the Skills Catalog and load via `/demo-N-*` — `test_authored_demos_appear_in_the_project_catalog` + `test_authored_demos_load_alongside_the_builtins` pass; manually rendered `assemble_skills_catalog(Path('.'))` lists all 3 with correct name/description, alongside the 7 pre-existing skills (`adr`, `commit`, `demo-1-implement-substack-summarizer`, `grill-me`, `repo-architecture`, `review-diff`, `summarize-substack`) — no name collisions.
- [ ] [HUMAN] Manual spot-run of one demo end-to-end — Awaiting human verification. Tag honesty confirmed: no `.env` file and no `GEMINI_API_KEY`/`OPENROUTER_API_KEY`/`MODAL_TOKEN` in this environment (`ls .env` → No such file; `env | grep -i gemini` → empty), so a live model-driven run genuinely cannot happen here.
- [x] PASS — demo-2's seeded repo genuinely fails 2 tests as committed; demo-4's CSV carries every listed mess — `tests/unit/decode/skills/test_demo_skills.py` (13/13 passed) plus my own fresh-copy manual run reproducing the exact same 2 failures, and a manual eyeball of `messy_sales.csv`: duplicate rows (order_id 1001 and 1007 each appear twice, byte-identical), mixed date formats (`2024-01-15` ISO, `01/17/2024` US-slash, `18-Jan-2024` named-month all present in `order_date`), currency strings (`$1,234.56`, `€99`, `$500.00`), missing values (blank `region` on rows 1004/1008, blank `amount` on rows 1005/1011).
- [x] PASS — No new project dependencies — `git status --porcelain pyproject.toml uv.lock` empty (both untouched); demo-2 uses `uv run pytest` (project's existing pytest), demo-4 uses `uv run --with pandas,matplotlib` (ephemeral, verified to actually work, see break path 3).
- [x] PASS — `make ci` green — 2045 passed, 2 skipped (pre-existing, unrelated, credential-gated), 0 failed, 429.14s. Evidence below.

**Evidence**
```
$ uv run pytest tests/unit/decode/skills/test_demo_skills.py -v
...
13 passed in 3.82s

$ make pre-commit
uv run ruff format --check
295 files already formatted
uv run ruff check
All checks passed!
... 1932 passed in 109.38s ...

$ make ci
...
tests/integration/test_workspace_clone.py ...                           [100%]
SKIPPED [1] tests/integration/test_observability_capstone.py:572: OPIK_API_KEY and GEMINI_API_KEY must both be set for the live Opik export smoke
SKIPPED [1] tests/integration/test_subagents_capstone.py:657: GEMINI_API_KEY is unset — the live Gemini fan-out smoke is skipped
================= 2045 passed, 2 skipped in 429.14s (0:07:09) ==================

# fresh-copy demo-2 run, exactly as SKILL.md instructs:
$ uv run --no-project pytest -q -o addopts=
FAILED test_stats.py::test_median_odd_length - assert 2 == 3
FAILED test_stats.py::test_variance_is_non_negative - assert -2.0 == 2.0
2 failed, 4 passed in 0.12s
# after hand-fixing both bugs:
6 passed in 0.01s

$ git check-ignore -v .decode/skills/demo-2-bug-hunt/SKILL.md .decode/skills/demo-2-bug-hunt/references/buggy_repo/stats.py .decode/skills/demo-2-bug-hunt/references/buggy_repo/test_stats.py .decode/skills/demo-3-terminal-arcade/SKILL.md .decode/skills/demo-4-data-detective/SKILL.md .decode/skills/demo-4-data-detective/references/messy_sales.csv
# (exit 1 — nothing ignored)
```

**Other issues found**
- Minor doc inaccuracy (not blocking): the SWE's log says "full `make ci` (incl. integration + build)" — `make ci` does not invoke `make build`; it's `uv lock --check` + format-check + lint-check + `test` (unit+integration). Doesn't affect correctness, just an imprecise parenthetical in the SWE's notes.
- The `code-review` plugin is enabled in `.claude/settings.json`, but this Tester session's tool set (Read/Edit/Write/Bash only) has no way to invoke it as a subagent/skill; a manual review pass was performed in its place (correctness, simplicity of the seeded bugs, fixture/test alignment, doc coherence) and found no issues beyond the note above.
- No stray large files: all new fixtures are small (`stats.py` 1.5K, `test_stats.py` 0.9K, `messy_sales.csv` 0.6K); none gitignored.

**VERDICT: PASS**
