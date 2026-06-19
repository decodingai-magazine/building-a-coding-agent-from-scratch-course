---
id: 006-tools-file-read
feature: m1-vanilla-agent
status: done
---

# Tools: file read + glob + grep

## Scope
Read-only file tools and the shared output-truncation helper + the tool registry.

## Acceptance criteria
- [x] `tools/truncate.py`: dual cap (2000 lines OR 50 KB, snap to line boundary), overflow spilled to a temp file whose path is returned.
- [x] `tools/files.py`: `read` (line-paginated `offset`/`limit`, numbered lines, truncated), `glob`, `grep` — all honor `ctx.deps.cwd`, tagged `read_only=True`. **FIXED (SWE): `glob`/`grep` now reject `..`/absolute patterns up front and drop any match resolving outside cwd (incl. in-tree symlinks) — see SWE fix log.**
- [x] `tools/registry.py` registers tools on the agent (flat registry, each tagged read-only or not).
- [x] Missing path → model-readable `ModelRetry`.

## Out of scope
- Write/edit (task 007); parallel read-only execution (M3).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Truncation constants validated against pi.

### [SWE] 2026-06-19 20:05 — Implementation

**Files modified**
- `src/decode/tools/truncate.py` (new) — tool-agnostic dual-cap helper (`truncate()` + frozen `Truncated(text, truncated, full_path)`); caps at lines OR bytes, snaps to a line boundary, spills full content to a temp file on overflow. Reused by `bash` (008).
- `src/decode/tools/files.py` (new) — read-only file tools `read`/`glob`/`grep`; all gate via `raise ApprovalRequired`, resolve under `ctx.deps.cwd`, return `ModelRetry` on missing/unreadable/empty/bad-regex, tagged `read_only=True`.
- `src/decode/tools/registry.py` (new) — flat `TOOL_SPECS` list (the single source of truth) + `register_tools(agent)`; `TOOL_READ_ONLY` derived from it.
- `src/decode/tools/__init__.py` — `TOOL_READ_ONLY` / `is_read_only()` now sourced from the registry (no hand-built map).
- `src/decode/agent/factory.py` — `build_agent()` registers via `register_tools()` instead of `register_noop()` (folds in noop).
- `src/decode/tools/noop.py` — docstring only; `register_noop()` kept as a test helper for a minimal one-tool agent (its prod registration is now the registry).
- `tests/unit/decode/tools/test_truncate.py`, `test_files.py`, `test_registry.py` (new).

**Tests**
- Unit: 159 passing, 0 failing (`make pre-commit`). New: 9 (truncate) + 23 (files) + 5 (registry).
- Integration: N/A — no infra changes.

**Acceptance criteria**
- [x] dual-cap truncate + temp-file overflow — `tests/unit/decode/tools/test_truncate.py` (line cap, byte-cap snap-to-boundary, whichever-first, spill completeness).
- [x] `read`/`glob`/`grep` honor `cwd`, tagged read-only — `tests/unit/decode/tools/test_files.py` (offset/limit windowing, numbered 1-indexed output, glob/grep cwd-scoping + no-escape, truncation+spill notice).
- [x] flat registry registers tools each tagged — `tests/unit/decode/tools/test_registry.py` (spec list, read-only flags, `is_read_only`, agent registration).
- [x] missing path → `ModelRetry` — `test_files.py::test_read_missing_path_returns_model_retry` (+ directory, offset-past-end, missing grep path, no-match, bad regex).

**Evidence**
```
$ make pre-commit
... 159 passed in 1.81s ...
$ uv lock --check
Resolved 163 packages in 3ms

# e2e: real agent (registry-wired) + TestModel against a temp project tree
== registry ==
  noop   read_only=False  is_read_only()=False
  read   read_only=True  is_read_only()=True
  glob   read_only=True  is_read_only()=True
  grep   read_only=True  is_read_only()=True
== read app.py ==        -> 1\timport os / 2\tdef run(): / 3\t    return os.getcwd()
== read offset=2 limit=1 -> 2\tdef run():
== glob **/*.py ==       -> app.py / lib/util.py   (relative to cwd, sorted)
== grep 'def \w+' *.py   -> app.py:2:def run(): / lib/util.py:1:def helper():
== read missing file     -> ModelRetry: No such file: 'nope.txt'.
== read w/o approval     -> ApprovalRequired
== truncation overflow   -> 2 numbered lines + "[output truncated ...; full content at /var/.../decode-output-*.txt]"
spill file lines: 5000 (first: 1\trow 0, last: 5000\trow 4999)  # full content preserved
```

**Notes**
- `read`'s `limit` is the *caller's* window; the 2000-line/50 KB cap is the independent *safety* cap applied by `truncate` afterward (so a default `read` of a huge file still truncates + spills). Justified inline.
- Tools are sync (local FS, sequential v1 execution per ADR §7; Pydantic AI runs sync tools in a worker thread). Justified in the `files.py` module docstring.
- `grep`'s `glob` parameter shadows the module `glob` function name but never calls it (uses `Path.glob` method) — no behavioural risk; left as-is for a clean model-facing arg name.
- No new deps; `uv.lock` unchanged. **Not committed** — awaiting Tester.

### [Tester] 2026-06-19 21:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 53 files; `ruff check` clean)
- Unit tests: 159 passed / 0 failed (0 warnings; `filterwarnings=error`)
- Integration tests: 0 collected (N/A — no infra changes; expected)
- `uv lock --check`: PASS (resolved 163 packages, lockfile unchanged)

**E2E adversarial pass** (direct unit-level probe + real registry-wired agent via FunctionModel with approval)
- Happy path: `build_agent()` agent + approve → `read app.py` → `1\timport os\n2\tdef run():\n3\t    return os.getcwd()`; `read offset=2 limit=1` → `2\tdef run():`; `glob **/*.py` → `app.py\nlib/util.py`; `grep 'def \w+'` → `app.py:2:def run():\nlib/util.py:1:def helper():` (PASS)
- Break path 1 (security: path traversal via `read`) — `read ../secrets/creds.txt`, `read /etc/passwd`, `read ../../../../../../etc/passwd`, symlink-escape → all rejected with `ModelRetry` "resolves outside the working directory" (PASS — `read` routes through `_resolve_in_cwd`)
- Break path 2 (**security: path traversal via `glob` pattern**) — `glob ../secrets/*.env` → **`../secrets/creds.env` (ESCAPED cwd)**; `glob ../../*/secrets/*` → enumerated *sibling temp dirs two levels up* (**FAIL**)
- Break path 3 (**security: path traversal via `grep` glob**) — `grep API_KEY --glob ../secrets/*.env` → **`../secrets/creds.env:1:API_KEY=sk-do-not-leak` — leaked secret file CONTENTS into the model-facing tool result** (**FAIL**); reproduced end-to-end through the real `agent.iter` loop with approval, not just at unit level
- Break path 4 (boundary: read windowing) — offset=0, negative offset, offset==last, one-past-end→`ModelRetry`, negative limit/limit=0→empty window, absolute numbering preserved (`offset=4 limit=3` → `4\tv3\n5\tv4\n6\tv5`) (PASS)
- Break path 5 (malformed/binary) — binary/non-UTF8 file → `ModelRetry` "codec can't decode"; empty file → `ModelRetry` "no line 1"; no-trailing-newline → numbered correctly; unicode (日本語 🚀) → preserved; bad regex → `ModelRetry`; grep over tree with binary → binary skipped, no crash (PASS)
- Break path 6 (huge file >2000 lines AND >50 KB: 5000 lines / 343 890 bytes) — truncated to 697 lines / 49 965 bytes, **every kept line is a whole numbered line (snapped)**, notice appended, **spill temp file holds all 5000 numbered lines (first `1\t...`, last `5000\t...`) — complete, no content lost** (PASS)
- Break path 7 (gating) — `read`/`glob`/`grep` each raise `ApprovalRequired` when `not ctx.tool_call_approved`; all tagged `read_only=True` in the registry, `is_read_only()` derived correctly (PASS)
- `grep` explicit `path` escape (`path=../secrets/...`) IS correctly rejected (routes through `_resolve_in_cwd`); only the `glob`-pattern path is unguarded.

**Acceptance criteria**
- [x] PASS — `truncate.py` dual cap + snap-to-line + temp-file overflow — `test_truncate.py` (9 tests) all pass; probe: 5000-line/343 KB input → 697 lines/49 965 B kept, snapped, full content in spill file verified by read-back diff (`src/decode/tools/truncate.py:63-121`)
- [ ] FAIL — `files.py`: `read`/`glob`/`grep` all honor `ctx.deps.cwd`
      Expected: `glob`/`grep` reject `..`-escaping (and absolute-elsewhere) patterns, like `read` and `grep`'s explicit `path` already do — "all paths resolved under cwd, never allowed to escape" (module docstring; ADR §7; task path-safety guarantee)
      Actual: `glob(pattern="../secrets/*.env")` returns `../secrets/creds.env`; `grep(glob="../secrets/*.env")` returns `../secrets/creds.env:1:API_KEY=sk-do-not-leak` — leaks file contents OUTSIDE cwd straight to the model. `glob("../../*/secrets/*")` escapes two levels and enumerates unrelated sibling dirs. Reproduced end-to-end through the real agent loop.
      Root cause: `files.glob` calls `base.glob(pattern)` (`src/decode/tools/files.py:147`) and `_grep_candidates` calls `base.glob(glob or "**/*")` (`src/decode/tools/files.py:214`) with the raw user pattern — neither validates the results stay under `cwd` (no `_resolve_in_cwd` / `relative_to(base)` containment check). `read` (line 91) and grep's explicit `path` (line 210) DO guard, so the guarding is inconsistent.
      Also violates AGENTS.md invariant "Secrets never reach the model."
      Fix: after globbing, drop any match whose resolved path is not under `base` (e.g. `m.resolve()` then `base == p or base in p.parents`), or reject patterns containing `..` / leading `/` up front with `ModelRetry`. Add a regression test using a `../`-escaping pattern (the existing `test_glob_does_not_escape_cwd` only uses a non-`..` pattern `*.py`, so it never exercised escape — false confidence). `relative_to(base)` will also raise for escaped paths, so containment must be checked before rendering.
- [x] PASS — `registry.py` registers tools on the agent, each tagged — `test_registry.py` (5 tests); built agent exposes `{glob, grep, noop, read}`, `TOOL_READ_ONLY` derived from `TOOL_SPECS` single source of truth, `is_read_only` correct incl. unknown→False (`src/decode/tools/registry.py:47-67`)
- [x] PASS — missing path → model-readable `ModelRetry` — `test_files.py::test_read_missing_path_returns_model_retry`; probe: missing/dir/offset-past-EOF/missing-grep-path/no-match/bad-regex/binary all → `ModelRetry`, no crash (`src/decode/tools/files.py:92-99,107`)

**Evidence**
```
$ make pre-commit          → 159 passed in 1.71s, 0 warnings
$ uv lock --check          → Resolved 163 packages (unchanged)

# adversarial probe (direct):
[PASS] read ../escape rejected: ModelRetry ... resolves outside the working directory
[PASS] read /etc/passwd rejected: ModelRetry
[PASS] read huge spill is COMPLETE: spill has 5000 lines, first='1\tline number 0...' last='5000\t...'
[FAIL] glob ../ pattern does not leak: '../secrets/creds.txt'
[FAIL] grep ../ glob does not leak: '../secrets/creds.env:1:TOP-SECRET-API-KEY'

# e2e (real build_agent() agent, FunctionModel, approved through deferred path):
=== e2e SECURITY: grep ../secrets escape leaks secret contents ===
'../secrets/creds.env:1:API_KEY=sk-do-not-leak'
  *** SECURITY FAIL: grep leaked secret file contents through the agent loop ***
```

**Other issues found**
- (note, non-blocking) `read` of an empty file returns `ModelRetry("has no line 1: offset is past the end of the file")` because `_number_lines` treats `len(lines)=0 < start=1` as past-EOF. Arguably an empty file should read as empty (`""`) rather than retry; the SWE's docstring claims empty → `ModelRetry`, so behaviour matches the stated contract — flag for PA/PR Reviewer to confirm the intent. Not a FAIL.
- (note) `grep`'s `glob` parameter shadowing the module `glob` function is harmless as the SWE noted; confirmed no accidental call.
- (note) `code-review` plugin is enabled in `.claude/settings.json` but is a `/review` slash-command (interactive), not invokable from this QA context — manual checklist + adversarial pass used instead.

**VERDICT: FAIL** — 1 acceptance criterion fails (glob/grep cwd containment); it is a path-traversal / secret-leak security regression reproduced both at unit level and end-to-end through the real agent loop. Fix `files.py:147` and `files.py:214` to enforce cwd containment on glob results, add a `../`-escaping regression test for both tools, then re-run.

### [SWE] 2026-06-19 22:30 — Fixes (path-traversal / secret-leak containment)

**Files modified**
- `src/decode/tools/files.py` — contained ALL glob-pattern matches under `cwd` for both `glob` and `grep`. Added three helpers: `_is_within(base, candidate)` (shared containment predicate, also now used by `_resolve_in_cwd`); `_reject_escaping_pattern(pattern)` (refuses a leading `/` or any `..` segment up front with a model-readable `ModelRetry` — also sidesteps `Path.glob`'s `NotImplementedError` on absolute patterns); `_contain(base, matches)` (resolves each match — following symlinks — and keeps only files whose real path stays under `base`). `glob` and `_grep_candidates` now reject the pattern then route every match through `_contain`, so no out-of-tree path is listed and no out-of-tree file is ever read/returned. Output stays cwd-relative.
- `tests/unit/decode/tools/test_files.py` — 6 regression tests (the gap that let this ship): for BOTH `glob` and `grep`, a `../`-escaping pattern and an absolute pattern targeting a `tmp_path` file OUTSIDE cwd (asserts refused, never listed/leaked), plus a symlink INSIDE cwd pointing OUTSIDE cwd (asserts the escaping symlink + its secret are dropped while the genuine in-tree file is still returned).

**Tests**
- Unit: 165 passing, 0 failing (159 prior + 6 new), 0 warnings under `filterwarnings=["error"]` — `make pre-commit` output below.
- Integration: N/A — no infra changes.

**Acceptance criteria**
- [x] `files.py`: `read`/`glob`/`grep` all honor `ctx.deps.cwd` — glob-pattern path now contained for both tools; verified by `test_files.py::{test_glob_rejects_dotdot_escaping_pattern, test_glob_rejects_absolute_pattern_outside_cwd, test_glob_excludes_symlink_resolving_outside_cwd, test_grep_rejects_dotdot_escaping_glob_and_does_not_leak, test_grep_rejects_absolute_glob_outside_cwd_and_does_not_leak, test_grep_excludes_symlink_resolving_outside_cwd}` + the pre-existing `test_glob_does_not_escape_cwd`.

**Evidence**
```
$ make pre-commit
... 165 passed in 1.81s ... (0 warnings)
$ uv lock --check
Resolved 163 packages in 3ms

# e2e: real tool functions vs a temp tree with an out-of-tree secret + in-tree symlink to it
== glob **/*.py ==  'app.py'
== grep def \w+ ==  'app.py:2:def run():'
[OK refused]  glob ../secrets/*.env        -> ModelRetry: points outside the working directory
[OK refused]  glob ../../*/secrets/*        -> ModelRetry: points outside the working directory
[OK refused]  glob <abs>/secrets/creds.env  -> ModelRetry: points outside the working directory
[OK refused]  grep glob=../secrets/*.env    -> ModelRetry: points outside the working directory
[OK refused]  grep glob=<abs>/creds.env     -> ModelRetry: points outside the working directory
[OK]          grep glob=*.env (in-tree symlink -> out-of-tree secret) -> 'own.env:1:API_KEY=in-tree-ok'   (secret 'sk-do-not-leak' NOT present)
[OK]          glob *.env (in-tree symlink -> out-of-tree secret)       -> 'own.env'                         (escaping symlink dropped)
```

**Notes**
- Two-layer defence: reject the obviously-escaping pattern up front (clean `ModelRetry`, no `NotImplementedError`), AND re-check every glob match against `cwd` after resolving — the second layer is what catches an in-tree symlink whose target is outside `cwd` (the actual leak vector). Resolve happens before the containment check, per the fix brief.
- Non-blocking note (empty file → `ModelRetry`) left as-is: it matches the documented contract, per instruction.
- No new deps; `uv.lock` unchanged. **Not committed** — awaiting Tester re-review.

### [Tester] 2026-06-19 23:25 — Re-QA (path-traversal / secret-leak fix)

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 53 files clean; `ruff check` all checks passed)
- Unit tests: 165 passed / 0 failed (0 warnings under `filterwarnings=["error"]`)
- Integration tests: 0 collected (N/A — no infra changes; expected)
- `uv lock --check`: PASS (resolved 163 packages, lockfile unchanged)

**E2E adversarial pass** (re-ran my OWN probes, independent of the SWE's tests — direct unit-level AND end-to-end through the real `build_agent()`-shape agent via `FunctionModel` + the real deferred-approval resume path; secret string `sk-do-not-leak` in an out-of-tree sibling `secrets/creds.env`, plus an in-tree symlink `link.env` → that secret)
- Happy path (regression): `glob **/*.py` → `app.py, lib/util.py, sub/inner.py`; `grep 'def \w+'` → `app.py:2:def run():` + `lib/util.py:1:def helper():`; `read app.py` → numbered 1-3; `read offset=2 limit=1` → `2\tdef run():` (PASS)
- Break path 1 (security: `..`-escape via glob/grep) — `glob ../secrets/*.env`, `glob ../../*/secrets/*`, `grep glob=../secrets/*.env` → all `ModelRetry` "points outside the working directory"; secret path/contents absent (PASS)
- Break path 2 (security: absolute pattern) — `glob <abs>/secrets/creds.env`, `grep glob=<abs>/creds.env` → `ModelRetry` (NOT `NotImplementedError`; model-readable) (PASS)
- Break path 3 (**security: in-tree symlink → out-of-tree secret**, the actual leak vector the up-front check can't catch) — `glob *.env` → only `own.env` (escaping `link.env` dropped by `_contain`); `grep glob=*.env` AND `grep` default `**/*` → only `own.env:1:API_KEY=in-tree-ok`; `sk-do-not-leak` and `creds.env` NEVER present (PASS)
- Break path 4 (security: explicit-path escape) — `read ../secrets/creds.env`, `read link.env` (symlink), `grep path=../secrets/creds.env` → all `ModelRetry` (PASS — `_resolve_in_cwd` resolves the symlink before the containment check)
- Break path 5 (**E2E through the real agent loop**) — model issues all 4 attack tool-calls, user blindly approves every one; escaping-glob calls became `RetryPromptPart`s (model gets a correction, not data); symlink-vector calls returned only in-tree `own.env`. Scraped EVERY `ToolReturnPart` in `run.all_messages()`: secret contents and path reached the model on ZERO returns (PASS)
- Break path 6 (huge file >2000 lines AND >50 KB: 5000 lines / ~327 KB) — kept 687 whole numbered lines / 49932 B (both caps respected, snapped to line boundary), spill notice appended, spill temp file holds all 5000 numbered lines (`1\trow 0…` … `5000\trow 4999…`) — complete, nothing lost (PASS)
- False-negative judgement (asked for): the up-front `_reject_escaping_pattern` rejects ALL patterns containing a `..` segment, including ones that resolve back INSIDE cwd (e.g. `glob sub/../*.py`). Benign patterns with no `..` (`sub/*.py`, `**/*.py`) are unaffected and work. **Verdict: acceptable, not an over-block worth failing.** Rationale: (a) `..` is never required to reach any in-tree file — a cwd-relative pattern always exists; (b) the SWE's docstring explicitly documents "no `..`"; (c) it is the safe default for a security boundary (deny-by-shape, then a per-match resolve as the second layer). Logged below as a non-blocking note for PA/PR-Reviewer awareness only.

**Acceptance criteria**
- [x] PASS — `truncate.py` dual cap (2000 lines OR 50 KB, snap-to-line) + temp-file overflow — `test_truncate.py` (9 tests); probe: 5000-line/~327 KB input → 687 lines/49932 B kept, snapped, spill file read-back shows all 5000 numbered lines (`src/decode/tools/truncate.py:63-121`)
- [x] PASS — `files.py`: `read`/`glob`/`grep` all honor `ctx.deps.cwd`, tagged `read_only=True` — **the path-traversal/secret-leak blocker is closed.** Two-layer defence verified: `_reject_escaping_pattern` refuses `..`/absolute up front (`files.py:74-87`), `_contain` resolves each match and drops anything outside `base` incl. in-tree symlinks (`files.py:90-97`); `glob` (`files.py:181-186`) and `_grep_candidates` (`files.py:255-256`) route through both; `read`/explicit-`path` guard via `_resolve_in_cwd` (`files.py:59-71,122,250`). Evidence: 6 regression tests (`test_glob_rejects_dotdot_escaping_pattern`, `test_glob_rejects_absolute_pattern_outside_cwd`, `test_glob_excludes_symlink_resolving_outside_cwd`, `test_grep_rejects_dotdot_escaping_glob_and_does_not_leak`, `test_grep_rejects_absolute_glob_outside_cwd_and_does_not_leak`, `test_grep_excludes_symlink_resolving_outside_cwd`) + my independent unit + e2e probes above. Secret contents `sk-do-not-leak` never appeared in any tool result; secret path never listed.
- [x] PASS — `registry.py` registers tools each tagged — `test_registry.py` (5 tests); probe: `is_read_only` read/glob/grep=True, noop=False, unknown=False (`src/decode/tools/registry.py:47-67`)
- [x] PASS — missing path → model-readable `ModelRetry` — `test_files.py::test_read_missing_path_returns_model_retry`; probe: missing/dir/offset-past-EOF/missing-grep-path/escape all → `ModelRetry`, no crash (`src/decode/tools/files.py:122-138,250`)

**Evidence**
```
$ make pre-commit          → 53 files formatted, lint clean, 165 passed in 1.74s, 0 warnings
$ uv lock --check          → Resolved 163 packages (unchanged)

# independent adversarial probe — unit-level (excerpt):
[PASS] glob ../secrets/*.env -> ModelRetry: ... points outside the working directory ...
[PASS] glob <abs secret>     -> ModelRetry (NOT NotImplementedError)
[PASS] glob *.env (in-tree symlink to secret) -> contained ('own.env')  # escaping symlink dropped
[PASS] grep glob=*.env       -> 'own.env:1:API_KEY=in-tree-ok'          # secret NOT present
[PASS] read link.env (symlink to secret) -> ModelRetry (target outside cwd)

# e2e through the real agent (FunctionModel + blind approval of every gated call):
  [retry] Glob pattern '../secrets/*.env' points outside the working directory ...
  [grep return] own.env:1:API_KEY=in-tree-ok
  [glob return] own.env
[PASS] e2e: NO secret contents or path reached the model through any tool return
ALL PROBES PASSED — secret never listed, contents never leaked.
```

**Other issues found**
- (note, non-blocking) `_reject_escaping_pattern` rejects every `..`-containing glob pattern, even one resolving back inside cwd (`sub/../*.py`). Judged acceptable above — deny-by-shape is the safe default for a security boundary and no in-tree file is unreachable without `..`. Flag for PA/PR-Reviewer awareness, not a FAIL.
- (note, carried) empty-file `read` → `ModelRetry("has no line 1")` still matches the documented contract; left for PA/PR-Reviewer to confirm intent. Not a FAIL.
- (note) `code-review` plugin in `.claude/settings.json` is an interactive `/review` slash-command, not invokable from this QA context — manual checklist + independent adversarial probe used instead.

**VERDICT: PASS** — all 4 acceptance criteria verified with evidence; full suite green (165 passed, 0 warnings); the path-traversal / secret-leak blocker is independently confirmed closed at unit level AND end-to-end through the real agent loop (secret never listed, contents `sk-do-not-leak` never reached the model). No regressions in the happy paths, truncation/spill, gating, or read-only tags. Hand off for commit.
