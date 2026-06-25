---
id: 018-permission-rules-engine
feature: permission-system-agents-catalog
status: pending
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

- [ ] `settings.permissions_file` defaults to `.decode/settings.json`, read only via the settings
      singleton (no `os.environ`/hard-coded path at a call site).
- [ ] A `deny` rule `bash(rm *)` makes `bash` command `rm -rf x` **DENY** even in `bypass` mode
      (deny beats everything). Unit-tested.
- [ ] An `allow` rule `bash(npm run test:*)` makes that command **ALLOW** in `default` mode (allow
      beats the mode's ASK), while a non-matching bash command still ASKs. Unit-tested.
- [ ] Bare `read` allow rule allows any `read` call; subjects extract per kind (bash→command,
      file→path, web→url). Unit-tested with at least bash, a file tool, and web.
- [ ] A missing or malformed `.decode/settings.json` is non-fatal: the gate behaves as task 017
      (mode-only) and logs a warning. Unit-tested.
- [ ] Precedence is exactly deny → allow → mode → ask, proven by a path matching both an allow and a
      deny rule → DENY. Unit-tested.
- [ ] `a`/`always` persists a matching allow rule to `.decode/settings.json` and the next identical
      call auto-allows (no second prompt); `y` stays allow-once; a write failure falls back to
      allow-once without breaking the turn. Driven end-to-end.
- [ ] **Working looks like:** with `.decode/settings.json` `{"permissions":{"deny":["bash(rm *)"]}}`,
      asking the agent to `rm` a file is denied without a prompt and the model is told why; an
      allow-listed bash command runs without a prompt.
- [ ] `make ci` green, 0 warnings; `tests/` mirror `src/` 1:1.

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
