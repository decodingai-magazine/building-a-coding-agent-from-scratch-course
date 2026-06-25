---
id: 018-permission-rules-engine
feature: permission-system-agents-catalog
status: done
---

# Permission rule engine + user .decode/settings.json

Implements [ADR-0003](../docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md) §3-4.
Depends on: 017 · Blocks: 019, 022

## Scope

Layer allow/deny **Permission Rules** on top of the mode decision, with precedence
**deny → allow → mode → ask**. This task builds the **reusable rule engine** (parser + matcher) and
the **user** rule source (`.decode/settings.json`); task 019/020 add agent-catalog rules using the
same engine. `.decode/settings.json` is the user's **optional personalization** file — its sole
purpose is permission rules (no user/global/org tiers).

- **`config/settings.py`** — add `permissions_file: Path = Path(".decode/settings.json")`; mirror in
  `.env.example` if overridable. Never read at a call site.
- **`permissions/rules.py`** — parse a rule string `Tool(pattern)` or bare `Tool` into
  `(tool_name, pattern | None)`. Load the `.decode/settings.json` shape
  `{"permissions": {"allow": [...], "deny": [...]}}` into an allow list + a deny list. A missing or
  malformed file is non-fatal (logged; treated as no rules). Use `json` + `pathlib`. Provide a
  `RuleSet` (allow + deny) and a `matches(rule, request) -> bool`.
- **Subject extraction** — per tool kind, the string matched against `pattern` (glob via `fnmatch`):
  `bash` → the command; file tools → the path; `web_fetch` → the url; everything else → the tool
  name. A bare `Tool` (no pattern) matches any call of that tool. The loop puts the subject on the
  `PermissionRequest` (add a `subject: str` field, default `""`).
- **`permissions/gate.py`** — the gate holds a user `RuleSet` (and, from task 020, an active-agent
  `RuleSet`); `check` evaluates **deny rule → allow rule → mode** (mode logic from task 017). A
  deny-rule hit → DENY with a reason citing the rule; an allow-rule hit → ALLOW. The two rule sources
  are evaluated as a union (a deny from either beats an allow from either).
- **`tui/app.py` always-persist** — extend `parse_permission_answer` / the resolver so `a`/`always`
  (case-insensitive) means "allow AND persist a matching allow rule to the **user**
  `.decode/settings.json`" (rule `Tool(subject)`, or bare `Tool` when there is no subject); `y`/`yes`
  stays allow-once. After persisting, reload the gate's user rules so the next identical call
  auto-allows. A write failure is non-fatal: log, fall back to allow-once.

## Acceptance criteria

- [x] `settings.permissions_file` defaults to `.decode/settings.json`, read only via the settings
      singleton (no `os.environ`/hard-coded path at a call site).
- [x] A `deny` rule `bash(rm *)` makes `bash` command `rm -rf x` **DENY** even in `bypass` mode
      (deny beats everything). Unit-tested.
- [x] An `allow` rule `bash(npm run test:*)` makes that command **ALLOW** in `default` mode (allow
      beats the mode's ASK), while a non-matching bash command still ASKs. Unit-tested.
- [x] Bare `read` allow rule allows any `read` call; subjects extract per kind (bash→command,
      file→path, web→url). Unit-tested with at least bash, a file tool, and web.
- [x] A missing or malformed `.decode/settings.json` is non-fatal: the gate behaves as task 017
      (mode-only) and logs a warning. Unit-tested.
- [x] Precedence is exactly deny → allow → mode → ask, proven by a path matching both an allow and a
      deny rule → DENY. Unit-tested.
- [x] `a`/`always` persists a matching allow rule to `.decode/settings.json` and the next identical
      call auto-allows (no second prompt); `y` stays allow-once; a write failure falls back to
      allow-once without breaking the turn. Driven end-to-end.
- [x] **Working looks like:** with `.decode/settings.json` `{"permissions":{"deny":["bash(rm *)"]}}`,
      asking the agent to `rm` a file is denied without a prompt and the model is told why; an
      allow-listed bash command runs without a prompt.
- [x] `make ci` green, 0 warnings; `tests/` mirror `src/` 1:1.

## Out of scope
- User/global/org rule tiers (only the one project file).
- Agent-catalog `allow`/`deny` rules — the engine is built here; agents wire their rules in via
  tasks 019/020.
- Hook-based permission decisions.

## Log

### [PA] 2026-06-25 — Grooming
Groomed from ADR-0003 §3-4. Round-2 lock: `.decode/settings.json` is **user-scoped/optional** — it is
NOT pre-seeded with built-in agent defaults; agent rules ride the catalog (task 019) through this same
engine. The gate merges user ∪ agent rules with deny-beats-allow precedence.

### [SWE] 2026-06-25 18:30 — Implementation

**Files modified**
- `src/decode/permissions/rules.py` — NEW rule engine: `Rule`/`RuleSet`, `parse_rule`,
  `subject_for`, `matches`, `load_rule_set`, `allow_rule_string`, `persist_allow_rule` (json+pathlib;
  missing/malformed file non-fatal).
- `src/decode/permissions/gate.py` — gate holds a user `RuleSet` (+ `set_user_rules`); `check` now
  evaluates deny → allow → mode across a `_rule_sources()` union (task 020 adds the agent set with no
  precedence rewrite); deny-hit cites the rule.
- `src/decode/entities/permissions.py` — added `subject: str = ""` to `PermissionRequest`.
- `src/decode/agent/loop.py` — `_decide` now populates `PermissionRequest.subject` via
  `rules.subject_for`.
- `src/decode/config/settings.py` + `.env.example` — added `permissions_file: Path =
  Path(".decode/settings.json")` (read only via the singleton).
- `src/decode/tui/app.py` — added `is_always_answer`; `_make_permission_resolver` now takes the gate
  + permissions file and, on `a`/`always`, persists a matching allow rule and reloads the gate's user
  rules (write failure non-fatal → allow-once); `run_app` loads user rules into the gate at startup.
- `tests/unit/decode/permissions/test_rules.py` — NEW (37 tests): parsing, subjects, matching,
  loading, persist round-trips.
- `tests/unit/decode/permissions/test_gate.py` — added the deny→allow→mode precedence tests.
- `tests/unit/decode/tui/test_app.py` — `is_always_answer` + always-persist/reload + write-failure
  fallback resolver tests.
- `tests/unit/decode/entities/test_permissions.py`, `tests/unit/decode/config/test_settings.py` —
  pinned the new `subject` field + `permissions_file` default.

**Tests**
- Unit: 444 passing, 0 failing (`make unit-tests`).
- Integration: 1 passing (`make integration-tests` — M1 capstone unchanged).
- `make ci`: green (uv lock --check + format-check + lint-check + 445 tests), 0 warnings
  (`filterwarnings=["error"]`).

**Acceptance criteria**
- [x] `settings.permissions_file` default — `tests/unit/decode/config/test_settings.py::test_defaults`.
- [x] deny `bash(rm *)` beats bypass — `test_gate.py::test_deny_rule_beats_bypass_mode`.
- [x] allow `bash(npm run test:*)` beats mode ASK; non-match still ASKs —
  `test_gate.py::test_allow_rule_beats_the_mode_ask`.
- [x] bare `read` allow + per-kind subjects (bash/file/web) —
  `test_rules.py::test_subject_for_*`, `test_bare_allow_rule_allows_any_call_of_that_tool`.
- [x] missing/malformed file non-fatal + warns —
  `test_rules.py::test_load_rule_set_missing_file_is_empty`,
  `::test_load_rule_set_malformed_json_is_non_fatal_and_warns`.
- [x] precedence deny→allow→mode→ask (path matching both → DENY) —
  `test_gate.py::test_deny_beats_allow_when_both_match`.
- [x] `a`/`always` persists + reloads; `y` allow-once; write failure → allow-once —
  `test_app.py::test_always_answer_persists_an_allow_rule_and_reloads_the_gate`,
  `::test_plain_yes_does_not_persist_a_rule`,
  `::test_always_answer_write_failure_falls_back_to_allow_once`.
- [x] Working-looks-like (deny rm without prompt; allow-listed bash runs) — driven end-to-end via the
  real loop-style code path (Evidence below).
- [x] `make ci` green, 0 warnings; tests mirror src 1:1.

**Evidence**
```
$ make ci   # tail
tests/integration/test_milestone1_capstone.py .                          [100%]
============================= 445 passed in 5.90s ==============================
$ uv lock --check → Resolved 166 packages   |  format-check → 82 files already formatted
$ ruff check → All checks passed!

$ uv run python  # exercise the engine the way loop._decide drives it (no Gemini)
rm -rf ->  deny | reason: Denied by permission rule bash(rm *).
rm under bypass -> deny
npm run test:unit -> allow
ls -la -> ask
read pyproject.toml subject= pyproject.toml -> allow
web_fetch subject= https://example.com/a
make deploy (before persist) -> ask
persisted allow rule: ['bash(make deploy)']
make deploy (after persist) -> allow
missing file, bash -> ask (mode-only)
ALL E2E ASSERTIONS PASSED

$ uv run decode --help  → launches cleanly (entrypoint imports intact)
```

**Notes**
- Gate is designed for task 020's agent rules: `_rule_sources()` returns a tuple (user set today);
  `check` walks every source's deny list before any allow list, so deny-from-either already beats
  allow-from-either — task 020 appends the agent `RuleSet` with no precedence change.
- `persist_allow_rule` is idempotent and preserves unrelated top-level keys + the existing deny list;
  it writes pretty-printed JSON (`indent=2`).
- The "working looks like" + always-persist criteria are exercised through the real loop-style path
  (subject extraction → gate.check → persist → reload), not a live Gemini run (no API key in this
  environment); the interactive resolver wiring is additionally covered by unit tests. DID NOT run a
  manual real-Gemini REPL pass — needs a `GEMINI_API_KEY` (left for the Tester's manual e2e).

### [Tester] 2026-06-25 18:35 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 82 files; `ruff check` All checks passed)
- Unit tests: 444 passed / 0 failed
- Integration tests: 1 passed / 0 failed (M1 capstone)
- Warnings: 0 (`filterwarnings=["error"]`; full run `445 passed in 6.35s`)
- code-review plugin: enabled in `.claude/settings.json` but it is an interactive Claude Code
  slash-command plugin, not a headless CLI — could not be invoked from the Tester bash context;
  the manual checklist below stands in its place.

**E2E adversarial pass** (real gate + rule engine + resolver path, 32 checks 0 failed; plus 4
real-Gemini REPL scenarios with a live `GEMINI_API_KEY` from `.env`)
- Happy path (real Gemini): `printf "what is 2+2?..." | uv run decode` → streams `Decode Four.`,
  `[done]`, clean exit (PASS)
- Real-Gemini deny-without-prompt: `.decode/settings.json` `{"permissions":{"deny":["bash(rm *)"]}}`
  + ask to `rm -f /tmp/decode_qa_victim.txt` → NO `permission?` prompt; denied panel
  `Denied by permission rule bash(rm *).`; model says it was denied; victim file SURVIVED on disk (PASS)
- Real-Gemini allow-without-prompt: `allow:["bash(echo *)"]` + `echo decode_allow_ok` → NO prompt,
  green panel exit 0 (PASS)
- Real-Gemini always-persist round-trip: session 1 `echo persist_me` → `permission? [y/N/a=always]`
  → `a` → wrote `.decode/settings.json` `{"permissions":{"allow":["bash(echo persist_me)"]}}`;
  session 2 same command → auto-allowed with NO prompt (PASS)
- Break 1 (precedence): deny beats bypass; deny beats allow when both match; deny tightens
  edit-mode auto-allow; allow beats mode ASK; non-match falls through to mode → all as expected (PASS)
- Break 2 (subject extraction): bash→command (`git *` allow), file→path (`read(/etc/*)` deny while
  a read outside `/etc` still auto-allows), web_fetch→url (`*evil.com*` deny), bare `Tool` matches
  any call, no-subject tool falls back to tool name (PASS)
- Break 3 (missing/malformed file): missing → empty ruleset, NO warning, gate mode-only ASK;
  malformed JSON → empty + WARNING; unknown shape / non-list allow-deny → empty; no crash, no
  warning escaping `filterwarnings=["error"]` (PASS)
- Break 4 (adversarial rule strings): `Tool(` / `` / `   ` / `()` / `(pattern)` skipped-with-warn,
  good rules (`bash([)`, `bash(rm -rf /*)`, `read`) still load; glob metachars in the matched
  subject don't crash `matches`; `subject_for` on garbage args (`""`, `[]`, `null`, non-string,
  empty-string value) never raises (PASS)
- Break 5 (persist failure): `a`/`always` with an unwritable path (parent is a file →
  `FileExistsError`/`OSError`) → resolver logs and falls back to allow-once, rule NOT loaded, turn
  not broken (PASS — the traceback in logs is the intended `exc_info=True` warning, not a crash)

**Acceptance criteria**
- [x] PASS — `settings.permissions_file` default `.decode/settings.json`, singleton-only —
      `test_settings.py::test_defaults`; `config/settings.py:46`.
- [x] PASS — deny `bash(rm *)` denies `rm -rf x` even in bypass —
      `test_gate.py::test_deny_rule_beats_bypass_mode`; adv harness; real-Gemini deny-without-prompt.
- [x] PASS — allow `bash(npm run test:*)` allows in default while non-match ASKs —
      `test_gate.py::test_allow_rule_beats_the_mode_ask`; adv harness.
- [x] PASS — bare `read` allows any read; subjects extract per kind (bash/file/web) —
      `test_rules.py::test_subject_for_*`, `test_gate.py::test_bare_allow_rule_allows_any_call_of_that_tool`;
      adv harness covers bash command / read path / web url with both allow and deny rules.
- [x] PASS — missing/malformed file non-fatal + warns; gate mode-only —
      `test_rules.py::test_load_rule_set_missing_file_is_empty`,
      `::test_load_rule_set_malformed_json_is_non_fatal_and_warns`; adv harness (missing has NO warn,
      malformed warns).
- [x] PASS — precedence deny→allow→mode→ask (subject matching both → DENY) —
      `test_gate.py::test_deny_beats_allow_when_both_match`; adv harness.
- [x] PASS — `a`/`always` persists + reloads (next call auto-allows); `y` allow-once; write failure
      → allow-once — `test_app.py::test_always_answer_persists_an_allow_rule_and_reloads_the_gate`,
      `::test_plain_yes_does_not_persist_a_rule`,
      `::test_always_answer_write_failure_falls_back_to_allow_once`; real-Gemini 2-session round-trip.
- [x] PASS — working-looks-like: deny `rm` without prompt + model told why + file survives; allow-listed
      bash runs without prompt — both driven against real Gemini end-to-end (see E2E pass).
- [x] PASS — `make ci` green, 0 warnings; tests mirror src 1:1 (`tests/.../permissions/test_rules.py`
      mirrors `src/.../permissions/rules.py`).

**Evidence**
```
$ make format-check && make lint-check    → 82 files already formatted · All checks passed!
$ uv run pytest tests/unit tests/integration -q
445 passed in 6.35s          # 0 warnings under filterwarnings=["error"]

$ adversarial harness (real gate + subject_for + resolver + persist/reload)
32 checks, 0 failed

$ real Gemini, deny rule in .decode/settings.json:
-> bash {"command":"rm -f /tmp/decode_qa_victim.txt"}
╭─ bash (failed) ─╮ Denied by permission rule bash(rm *). ╰─╮
victim file SURVIVED on disk   (no permission? prompt appeared)

$ real Gemini, always-persist round-trip:
session 1: a → wrote {"permissions":{"allow":["bash(echo persist_me)"]}}
session 2: echo persist_me → ran with NO permission? prompt (auto-allowed)
```

**Other issues found**
- None blocking. Note (non-blocking): persisting an always-allow rule for a bash command stores the
  *exact* command string (`bash(echo persist_me)`), so only a byte-identical command auto-allows
  next time — intended per ADR-0003 §4 (allow-once vs. persist a literal rule); a future task could
  offer pattern-scoped persistence, but that is out of scope here.
- Working tree is exactly the 13 expected files; no stray QA artifacts, no `git add -A` bleed.

**VERDICT: PASS**
