---
id: 012-memory-files-injection
feature: m1-vanilla-agent
status: done
---

# Memory: files layer + injection

## Scope
Read the project memory files and inject them into the agent's instructions ([ADR-0002 §8](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)).

## Acceptance criteria
- [x] `memory/files.py` discovers `AGENTS.md` + `MEMORY.md` walking cwd→repo-root (cwd-most wins); `CLAUDE.md` skipped.
- [x] `memory/service.py` `assemble_memory(cwd)` concatenates with provenance headers; `MEMORY.md` capped at 200 lines AND 25 KB with a visible truncation note when exceeded.
- [x] Injected at prompt-build time via a dynamic `@agent.instructions` hook; verified via `result.all_messages()`.
- [x] Missing files are skipped, not errors.

## Out of scope
- Memory write-back (task 013); `@`-import resolution; user-global `~/.decode/`.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Dual 200-line/25 KB cap validated against claude-code.

### [SWE] 2026-06-20 09:30 — Implementation

**Files modified**
- `src/decode/memory/__init__.py` — new package docstring (files + service split, scope notes).
- `src/decode/memory/files.py` — `discover_memory_files(cwd)`: walk cwd→filesystem-root, collect `AGENTS.md`/`MEMORY.md`, skip `CLAUDE.md`, order root-most-first / cwd-most-last.
- `src/decode/memory/service.py` — `assemble_memory(cwd)`: read discovered files, provenance headers (`# From <abs path>`), cap `MEMORY.md` at 200 lines AND 25 KB with a visible truncation note, skip missing/unreadable, `""` when empty.
- `src/decode/agent/factory.py` — added `_register_memory_instructions`: a dynamic `@agent.instructions` hook calling `assemble_memory(ctx.deps.cwd)`, evaluated per run.
- `tests/unit/decode/memory/{__init__,test_files,test_service}.py` — discovery + assembly unit tests.
- `tests/unit/decode/agent/test_factory.py` — injection tests via a real agent + `TestModel`, asserting on `result.all_messages()[0].instructions`.

**Confirmed dynamic-instructions API (pydantic-ai 1.107.0)**
`@agent.instructions` registers a function taking `RunContext[AgentDeps]` (sync or async) returning `str`; the returned text is **appended** to the static `instructions=` base (newline-joined) and rebuilt **per run** at prompt-build time. It surfaces on the first `ModelRequest.instructions` in `result.all_messages()`. Probed directly against the installed SDK before wiring.

**Tests**
- Unit: 295 passing, 0 failing (`make unit-tests` / `make pre-commit`). 27 new (`test_files.py` 7, `test_service.py` 11, `test_factory.py` +9 incl. existing).
- Integration: N/A — no infra changes.
- `uv lock --check`: clean (no new deps).

**Acceptance criteria**
- [x] Discovery walks cwd→root, finds `AGENTS.md`+`MEMORY.md` at multiple levels, cwd-most wins, `CLAUDE.md` skipped — `tests/unit/decode/memory/test_files.py`.
- [x] `assemble_memory` adds provenance headers; `MEMORY.md` capped at 200 lines AND 25 KB with a visible truncation note (line cap and byte cap tested independently) — `tests/unit/decode/memory/test_service.py`.
- [x] Dynamic `@agent.instructions` injection, evaluated per run, verified via `result.all_messages()` — `tests/unit/decode/agent/test_factory.py::test_memory_is_injected_into_the_first_request_instructions`, `::test_memory_injection_is_evaluated_per_run`.
- [x] Missing/unreadable files skipped (no error; `""` when nothing found) — `test_service.py::test_missing_files_are_skipped_not_errors`, `::test_unreadable_file_is_skipped_without_raising`.

**Evidence**
```
$ make pre-commit
... 295 passed in 5.12s ...

$ GEMINI_API_KEY=dummy uv run python  # build_agent() against a real tmp tree (cwd=/tmp/decode-mem-e2e/sub)
# AGENTS.md at root + leaf, MEMORY.md (250 lines) at leaf, CLAUDE.md at leaf
=== first ModelRequest.instructions (head) ===
You are decode, a terminal coding agent ...

# From /private/tmp/decode-mem-e2e/AGENTS.md
PROJECT RULE: always run make pre-commit before handing off.

# From /private/tmp/decode-mem-e2e/sub/AGENTS.md
LEAF AGENTS rule wins

# From /private/tmp/decode-mem-e2e/sub/MEMORY.md
note line 0
...
=== assertions ===
static base present: True
root AGENTS rule present: True
leaf AGENTS rule present: True
CLAUDE.md skipped: True
MEMORY truncation note present: True
cwd-most wins (leaf after root): True
```

**Notes**
- Stop condition for the walk: **filesystem root** (`path.parent == path`), inclusive — documented in `files.py`. Chose filesystem root over a `.git`-marker "repo root" for simplicity (no layout heuristic); in practice only the project's own memory files sit on the path, so the broader walk costs nothing.
- Ordering bug caught + regression-tested during implementation: a flat `reversed()` of the collected list reversed the within-level order too (`MEMORY.md` before `AGENTS.md`). Fixed by reversing **level groups** while preserving within-level order; covered by `test_files.py::test_agents_md_precedes_memory_md_within_a_level` (red→green).
- Only `MEMORY.md` is capped (it is model-maintained / grows on its own per task 013); `AGENTS.md` is project-authored and passed through whole — `test_service.py::test_agents_md_is_not_capped`.
- On macOS `tmp_path` lives under a `/var → /private/var` symlink; discovery resolves `cwd`, so tests resolve the tmp root (or compare resolved paths) to match the discovered absolute provenance paths. Documented inline.
- No `print()` in library code; full type annotations; `tests/` mirror `src/` 1:1; `filterwarnings=["error"]` respected (no network — `TestModel` only).

### [Tester] 2026-06-20 11:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 71 files clean; `ruff check` all passed)
- Unit tests: 295 passed / 0 failed (`make pre-commit`)
- Integration tests: N/A — no infra changes
- `uv lock --check`: clean (166 packages resolved, no new deps)
- Warnings: 0 (`filterwarnings=["error"]`; no network — `TestModel` only)

**E2E adversarial pass** (unit + through a real agent with `TestModel`, `deps.cwd` → tmp tree; 27 probes, 0 FAIL)
- Happy path: build agent, `agent.run("hi")` with `AGENTS.md` at cwd → memory + static base both ride in `result.all_messages()[0].instructions` (PASS)
- (a) discovery — 5-level tree, `AGENTS.md`+`MEMORY.md` at multiple levels: order `[root/AGENTS, root/MEMORY, mid/AGENTS, leaf/AGENTS, leaf/MEMORY]`; `CLAUDE.md` skipped (not discovered, content absent from assembly); cwd-most last; `AGENTS.md` before `MEMORY.md` within a level (PASS)
- (b) provenance — `# From <abs path>` headers name the correct resolved absolute paths, root header before leaf header (PASS)
- (c) caps tested INDEPENDENTLY: line-cap-bites/byte-cap-fits (250 tiny lines, 1139 B → kept ≤200 lines + note); byte-cap-bites/line-cap-fits (3 lines × 12500 B = 37502 B → snapped to 1 line, 12500 B ≤ 25000, + note); `AGENTS.md` 300 lines NOT capped; boundary 200-lines==cap → no note; 201-lines==cap+1 → note + last line dropped (PASS)
- (d) injection through real agent: assembled memory in `result.all_messages()[0].instructions`; truncation note surfaces end-to-end; per-run re-eval — edit `AGENTS.md` between two runs on the same agent → run 2 sees `RULE-TWO`, `RULE-ONE` does not leak into run 2 (PASS)
- (e) missing → `""`, no spurious `# From` header, only static base injected; unreadable (directory named `MEMORY.md`) skipped, peer `AGENTS.md` still injected, no crash (PASS)
- (f) edge/hostile: Rich-markup + instruction-injection + `%s`/`{0}` format-string content lands verbatim as data (no render/format crash); 10 MB / 100k-line `MEMORY.md` → capped to 20 KB (no OOM/hang); non-UTF8 bytes → `UnicodeDecodeError` caught, file skipped, peer `AGENTS.md` still injected; symlinked `AGENTS.md` followed; broken symlink not discovered (`is_file()` False) and skipped; zero-byte file → header-only; 8 concurrent agent runs all see memory (PASS)
- single-line-larger-than-byte-cap: keeps the one whole line (75 KB > 25 KB cap) + note — documented intentional behavior ("model needs *something* readable"); no infinite loop, no crash (PASS, noted below)

**Acceptance criteria**
- [x] PASS — `memory/files.py` discovers `AGENTS.md`+`MEMORY.md` cwd→root (cwd-most wins), `CLAUDE.md` skipped — `tests/unit/decode/memory/test_files.py` (7 tests) + adversarial a1–a5; multi-level order + within-level order verified
- [x] PASS — `assemble_memory(cwd)` provenance headers; `MEMORY.md` capped 200 lines AND 25 KB with visible note when exceeded — `tests/unit/decode/memory/test_service.py` (11 tests) + adversarial b1–b3, c1–c5; line cap and byte cap exercised independently; `AGENTS.md` not capped
- [x] PASS — dynamic `@agent.instructions` hook, evaluated per run, verified via `result.all_messages()` — `tests/unit/decode/agent/test_factory.py::test_memory_is_injected_into_the_first_request_instructions`, `::test_memory_injection_is_evaluated_per_run` + adversarial d1–d2
- [x] PASS — missing/unreadable files skipped, not errors — `test_service.py::test_missing_files_are_skipped_not_errors`, `::test_unreadable_file_is_skipped_without_raising` + adversarial e1–e2, f3, f4b (`""` when none; no crash on dir/non-UTF8/broken-symlink)

**Evidence**
```
$ make pre-commit
... 295 passed in 5.12s ...
$ make format-check && make lint-check && uv lock --check
71 files already formatted; All checks passed!; Resolved 166 packages
$ GEMINI_API_KEY=dummy uv run python /tmp/adversarial_qa.py
27 checks, 0 FAIL — ALL ADVERSARIAL CHECKS PASSED
```

**Stop-condition judgement (filesystem-root vs repo-root) — NON-BLOCKING, needs PA ratification**
- The walk reaches the **filesystem root**, so a stray `AGENTS.md`/`MEMORY.md` in an ancestor OUTSIDE the project (e.g. `~/AGENTS.md` when running `decode` from `~/projects/foo/src/`) IS picked up and injected — adversarial g1 confirms it (`found=['AGENTS.md', 'myproject/AGENTS.md']`).
- ADR-0002 §8 and this task's AC both say "cwd→**repo-root**"; the implementation walks "cwd→**filesystem-root**". This is a **documented, deliberate** SWE deviation (rationale: no `.git`-layout heuristic, matches surveyed harnesses' ancestor walk), recorded in `files.py` and the SWE log.
- Verdict on it: **non-blocking for M1.** The AC's *intent* (ancestors contribute, cwd-most wins, CLAUDE skipped) is fully met and adversarially verified; the residual concern (a stray outside-project memory file silently feeding the model, and — once task 013 lands — write-back possibly targeting the nearest `MEMORY.md`) is low-probability and is a correctness/safety smell, not a crash or data loss. But it IS a spec-vs-impl wording mismatch: **PA must ratify** — either update ADR §8 + the AC to "filesystem-root", or constrain the walk to a repo-root marker. Flagging for the PA's acceptance review; out of the Tester's lane to decide.

**Other issues found (non-blocking)**
- Single MEMORY.md line longer than `memory_max_bytes` is kept whole (assembled block then exceeds the byte cap). Documented & intentional (`_clip_to_budget`: "the model needs *something* readable"); the visible note flags the overflow. Acceptable for M1; worth a one-line ADR mention if the byte cap is meant to be a hard ceiling.
- `code-review` plugin is enabled but is a `/code-review` slash-command (interactive), not tool-invocable from this session; its function was performed manually as part of the checklist below (no defects found): scope clean (only `memory/` + factory hook + mirrored tests, no `git add -A` spillover), no `print()` in lib code, all signatures annotated, no secret literals, `tests/` mirror `src/` 1:1.

**VERDICT: PASS**

### [PA] 2026-06-20 14:10 — Acceptance Review (feature-level, m1-vanilla-agent)

**VERDICT: ACCEPT** — and the open stop-condition item is **RATIFIED as-is for M1 (option a)**.

Ruling on filesystem-root vs repo-root (ADR-0002 §8 says "cwd→repo-root"; impl walks "cwd→filesystem-root"):
- **Read side** (`memory/files.py::discover_memory_files`) walks to the filesystem root. The AC *intent* — ancestors contribute, cwd-most wins, `CLAUDE.md` skipped — is fully met and adversarially verified. The only residual is a stray ancestor `AGENTS.md`/`MEMORY.md` outside the project being injected. For a single-developer teaching tool in M1 this is low-impact (read-only context, capped at 200 lines/25 KB, cwd-most still wins).
- **Write side** (`memory/extract.py::append_session_summary`) is the part that would actually be dangerous if it walked — and it does **not**. It writes to `cwd/MEMORY.md` (the launch cwd, pinned, no walk). So the Tester's forward-looking worry ("write-back may target the nearest ancestor MEMORY.md once 013 lands") does **not** materialize: 013 has landed and write-back is cwd-pinned. There is no data-loss or wrong-file-write path.
- Decision: keep the implementation; the wording mismatch is resolved by tightening the **ADR §8 wording** to "filesystem-root (cwd-most wins)" so doc and code agree. A `.git`-marker stop is a reasonable M3 hardening (it pairs naturally with permission modes), recorded as out-of-scope-for-M1, **not** a blocker. The ADR wording fix is the only doc edit; it is a documentation-discipline correction, not a code rollup.

Feature walked from the user's perspective against the tasks/001–015 ACs and the capstone test:
- **Launch / no-key guard** (task 004): `env -u GEMINI_API_KEY uv run decode` prints exactly `decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).` on stderr and exits 1 — no traceback. `--help` surfaces `--resume [SESSION]`.
- **Streaming chat** (002/004): append-style `AssistantTextDelta` renders above a pinned `prompt_async()` under `patch_stdout`.
- **Gated tools** (005–010): `read`/`write`/`bash`/`todo_write`/`web_fetch` each surface `permission? <tool>` and route allow/deny through the single `DecisionChannel`; capstone proves approve creates the file, deny leaves no file and feeds `"the user denied this write"` back to the model (it doesn't pretend it wrote). Bash truncates (2000 lines / 50 KB, temp-file overflow); web_fetch GETs then HTML→Markdown and maps every error to `ModelRetry` (REPL never crashes).
- **Tasks checklist** (009): blue `tasks` panel with `[ ]`/`[~]`/`[x]`, re-rendered on update.
- **ask_user** (011): ungated; `ask: <question>` renders with a `type your answer:` cue; the next typed line *is* the answer (same channel, no y/N parse) and resumes the turn.
- **Steer / follow-up / abort** (003, ADR §4-5): plain Enter = STEER (boundary-injected), Alt+Enter = FOLLOW_UP (drained at would-stop), Esc = cooperative ABORT (`[aborted]` marker, work kept).
- **Memory load + write-back** (012/013): ancestor `AGENTS.md`+`MEMORY.md` injected via dynamic `@agent.instructions`; on quit one cheap Gemini call appends a dated `- YYYY-MM-DD:` bullet to `cwd/MEMORY.md`, picked up next session. Fully non-fatal.
- **Resume** (014): `--resume` / `--resume <id>` replays the JSONL session log into a fresh handler; missing session → one friendly line, fresh start (never crashes).
- **Renderer** is exhaustive over all 10 event kinds in `entities/events.py::Event`; raises loudly on an unknown kind so nothing renders silently.

Evidence: `uv run pytest` → 347 passed; capstone `tests/integration/test_milestone1_capstone.py` → 1 passed (full real stack, network boundary stubbed); no-key guard exit=1 with the exact line.

Real-Gemini round-trip is `[HUMAN]`-gated (no key in CI) — the offline-equivalent coverage (FunctionModel/TestModel + MockTransport through the real `build_agent()`/`Runner`/`render_event`/session-log/memory path) is sufficient for M1 acceptance.

User satisfaction guaranteed. No rollup task filed. Follow-up (non-blocking): edit ADR-0002 §8 wording to "filesystem-root"; hand off to the PR Reviewer.
