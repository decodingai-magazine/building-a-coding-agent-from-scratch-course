---
id: 050-lsp-settings-and-dep
feature: lsp-integration
status: done
---

# LSP settings, .env.example mirror, and the `ty` dev dependency

Tags: `data`, `infra`, `lsp`
Depends on: None
Blocks: #051, #052, #053, #054

This task implements ADR-0007 (LSP integration) — it lands the configuration surface and the
language-server binary the rest of the feature reads. No readers yet (exactly like task 041 added
the compaction settings ahead of their readers); this stays independently shippable.

## Scope

Add the LSP configuration surface and pin the language-server binary. Follow the recent
compaction-block convention in `config/settings.py` (the block at settings.py:67-88) and mirror
every new variable in `.env.example` (the `# --- Context compaction ---` block at .env.example:71-87
is the pattern).

- **Dependency:** add `ty` (Astral's Rust type-checker / language server) to the **dev** group
  via `uv add --dev ty` (PEP 735 `[dependency-groups] dev` — AGENTS.md: dev tools via the dev
  group, never `[project.optional-dependencies]`). `ty` is invoked as a **subprocess** (`ty server`),
  never imported, so the dev group is the right home; `uv.lock` pins it (ty is pre-1.0 / preview —
  record that honestly per ADR-0007). Confirm `uv.lock` updates and `uv lock --check` stays green.
- **Settings** (add to `Settings`, defaults safe for tests, all timezone/type-annotated as the file
  requires), under a new `# --- LSP / code intelligence (ADR-0007) ---` block:
  - `lsp_enabled: bool = True` — master gate for the WHOLE feature (the `lsp` tool AND the enricher AND
    any server spawn). When `False`, no server is ever launched.
  - `lsp_server_command: str = "ty"` — the swappable Language Server executable.
  - `lsp_server_args: list[str] = ["server"]` — the args appended to the command (so the spawn is
    `[lsp_server_command, *lsp_server_args]`). Default launches `ty server` (stdio LSP). Documented
    drop-in fallback: `pylsp` / any stdio LSP server.
  - `lsp_diagnostics_on_edit: bool = True` — gates ONLY the passive Diagnostics Enricher (task 053).
  - `lsp_request_timeout_s: float = 10.0` — per-request best-effort wall-clock timeout (the server's
    initialize can be slow; the lazy/cached single spawn amortizes it). Use a `Field(..., gt=0)`
    bound consistent with the other float settings.
- **`.env.example`:** add a `# --- LSP / code intelligence ---` block mirroring all five vars
  (commented, with the same explain-the-default voice as the compaction block), noting the server is
  swappable and that the feature is best-effort (absent/old `ty` degrades silently).

## Acceptance criteria

- [x] `ty` appears in `pyproject.toml`'s `[dependency-groups] dev` and in `uv.lock`; `uv lock --check`
      passes and `make install` (`uv sync`) installs it.
- [x] `Settings` exposes `lsp_enabled` (True), `lsp_server_command` ("ty"), `lsp_server_args`
      (`["server"]`), `lsp_diagnostics_on_edit` (True), `lsp_request_timeout_s` (10.0) with the exact
      names/types/defaults above; a unit test asserts every default.
- [x] `lsp_request_timeout_s` is constrained `> 0` (a `0`/negative env value fails validation); unit-tested.
- [x] Each new var is overridable from the environment (e.g. `LSP_SERVER_COMMAND=pylsp`,
      `LSP_ENABLED=false`); a unit test sets them via `monkeypatch`/env and asserts the parsed values
      (including `lsp_server_args` parsing a list from the env per pydantic-settings).
- [x] `.env.example` lists all five vars under an LSP block; a test (or the existing env/settings
      drift test, if present) confirms every new setting has a matching `.env.example` line.
- [x] `make ci` is green with 0 warnings (`filterwarnings=["error"]`).

## User stories

### Story: A developer points decode at a different language server
1. Developer opens `.env`, uncomments `LSP_SERVER_COMMAND=pylsp` and `LSP_SERVER_ARGS=["-v"]` (or the
   pylsp invocation).
2. Developer runs `uv run python -c "from decode.config.settings import settings; print(settings.lsp_server_command, settings.lsp_server_args)"`.
3. The output is `pylsp ['-v']` — the swap is config-only, no code change.

### Story: A developer turns the whole feature off
1. Developer sets `LSP_ENABLED=false` in `.env`.
2. `settings.lsp_enabled` reads `False`; downstream tasks (052/053/054) treat this as "never spawn a
   server, no `lsp` tool effect, no enricher" (their own ACs cover the behavior).

### Story: A developer installs the project fresh
1. Developer runs `make install`.
2. `uv sync` resolves the pinned `ty` from `uv.lock` into the dev environment; `uv run ty --version`
   prints a version (proving the binary is on PATH for later tasks to spawn).

## Out of scope
- Any reader of these settings (the client/tool/enricher land in 051/052/053).
- Promoting `ty` to a runtime dependency (it stays dev-group; graceful degradation when absent is the
  feature's contract — ADR-0007).

## Log
### [PA] 2026-06-27 — Grooming

**Summary**
Lands the LSP config surface (5 settings) + the `ty` dev dependency ahead of any reader, mirroring
the compaction-settings-first pattern (task 041).

**Key decisions**
- `ty` goes in the dev group (invoked as a subprocess, never imported); pre-1.0, pinned by `uv.lock`.
- Master gate `lsp_enabled` gates the whole feature; `lsp_diagnostics_on_edit` gates only the enricher.
- Server is swappable via `lsp_server_command` + `lsp_server_args` (pylsp drop-in documented).

**Dependencies**
- None.

**User stories**
- 3 stories: swap the server, disable the feature, fresh install resolves `ty`.

Ready for implementation.

### [SWE] 2026-06-27 14:30 — Implementation

**Files modified**
- `pyproject.toml` — added `ty>=0.0.55` to `[dependency-groups] dev` (via `uv add --dev ty`; subprocess-only, never imported).
- `uv.lock` — pins `ty==0.0.55` (pre-1.0 / preview, per ADR-0007); `uv lock --check` green.
- `src/decode/config/settings.py` — new `# --- LSP / code intelligence (ADR-0007) ---` block with the 5 settings (`lsp_enabled`, `lsp_server_command`, `lsp_server_args`, `lsp_diagnostics_on_edit`, `lsp_request_timeout_s` with `Field(10.0, gt=0)`).
- `.env.example` — new `# --- LSP / code intelligence ---` block mirroring all 5 vars (commented, explain-the-default voice).
- `tests/unit/decode/config/test_settings.py` — added `_LSP_ENV_VARS` + 5 tests: defaults, process-env override (incl. `lsp_server_args` JSON-list parse), dotenv override, non-positive-timeout rejection (parametrized `0` / `-1.0`), and an `.env.example` drift guard.

**Tests**
- Unit: 840 passing, 0 failing (`make pre-commit`); the 26 settings tests include the 6 new LSP cases (5 functions, one parametrized x2).
- Integration: 9 passing (`make ci` full suite = 849). No infra changes — config-only.
- Red/green confirmed: the 6 new assertions failed first for the right reasons (AttributeError on missing fields; unknown env var ignored so timeout `DID NOT RAISE`; `LSP_ENABLED` missing from `.env.example`), then passed after implementing.

**Acceptance criteria**
- [x] `ty` in `[dependency-groups] dev` + `uv.lock`; `uv lock --check` passes; `uv sync` installs it — `uv run ty --version` → `ty 0.0.55`.
- [x] `Settings` exposes all 5 with exact names/types/defaults — `tests/unit/decode/config/test_settings.py::test_lsp_defaults`.
- [x] `lsp_request_timeout_s` constrained `> 0` — `::test_rejects_a_non_positive_lsp_request_timeout[0]` and `[-1.0]`.
- [x] Each var env-overridable incl. list parse — `::test_reads_lsp_vars_from_process_env`, `::test_loads_lsp_vars_from_a_dotenv_file`.
- [x] `.env.example` lists all 5 under an LSP block — `::test_env_example_lists_every_lsp_var`.
- [x] `make ci` green, 0 warnings (`filterwarnings=["error"]`) — 849 passed.

**Evidence**
```
$ make ci
... uv lock --check → Resolved 143 packages; ruff format --check → 115 already formatted;
    ruff check → All checks passed!; pytest → 849 passed in 8.38s

$ uv run pytest tests/unit/decode/config/test_settings.py -q
26 passed in 0.15s

# End-to-end, the three user stories:
$ LSP_SERVER_COMMAND=pylsp LSP_SERVER_ARGS='["-v"]' uv run python -c \
    "from decode.config.settings import Settings; s=Settings(_env_file=None); print(s.lsp_server_command, s.lsp_server_args)"
pylsp ['-v']                                  # Story 1: server swap is config-only
$ LSP_ENABLED=false uv run python -c \
    "from decode.config.settings import Settings; s=Settings(_env_file=None); print('lsp_enabled =', s.lsp_enabled)"
lsp_enabled = False                           # Story 2: master gate off
$ uv run python -c "from decode.config.settings import settings as s; \
    print(s.lsp_enabled, repr(s.lsp_server_command), s.lsp_server_args, s.lsp_diagnostics_on_edit, s.lsp_request_timeout_s)"
True 'ty' ['server'] True 10.0                # defaults via singleton
$ uv run ty --version
ty 0.0.55 (42869400d 2026-06-26)              # Story 3: binary on PATH for later tasks
```

**Notes**
- `lsp_server_args: list[str] = ["server"]` uses the plain mutable default (pydantic deep-copies per instance); `ruff check` is clean — no `RUF012` (ruff recognizes the pydantic-settings model), so no `noqa` needed.
- Config-only, no readers (matches the task scope and the task-041 compaction precedent). Readers land in 051/052/053/054.
- Not committed — handing off to the Tester first per the lifecycle.

### [Tester] 2026-06-27 15:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 115 files; `ruff check` All checks passed!; pre-commit 840 unit passed)
- Unit tests: 840 passed / 0 failed
- Integration tests: 9 passed / 0 failed
- `make ci`: 849 passed, exit 0; `filterwarnings=["error"]` (pyproject.toml:100) ⇒ 0 warnings
- `uv lock --check`: Resolved 143 packages, exit 0

**E2E adversarial pass** (config-only task; probed the settings/dependency surface)
- Happy path: `python -c "from decode.config.settings import settings as s; print(...)"` → `True 'ty' ['server'] True 10.0` (PASS)
- Break 1 (boundary: timeout `gt=0`): `LSP_REQUEST_TIMEOUT_S` = `0` / `-5` / `abc` → ValidationError (`greater_than` / `float_parsing`); `0.0001` / `1e9` → parse to `0.0001` / `1e9` (PASS — fails fast at load, no traceback leak)
- Break 2 (list-from-env + precedence): `LSP_SERVER_COMMAND=pylsp LSP_SERVER_ARGS='["-v","--check-parent-process"]'` → `pylsp ['-v', '--check-parent-process']`; `[]` → `[]`; process env overrides a dotenv (`from_process` wins over `from_dotenv`); malformed `LSP_SERVER_ARGS=server` (not JSON) → `SettingsError` naming the field (loud fail, not silent corruption) (PASS)
- Break 3 (.env.example drift, both directions): all 5 vars present in `.env.example:105-121`; orphan scan and missing scan both "none"; bool spellings `FALSE`/`0` → `False`, `maybe` → ValidationError (`bool_parsing`) (PASS)
- Dependency placement: `ty>=0.0.55` in `[dependency-groups] dev` (pyproject.toml:57); no `[project.optional-dependencies]` block exists; `uv run ty --version` → `ty 0.0.55 (42869400d 2026-06-26)` (PASS)

**Acceptance criteria**
- [x] PASS — `ty` in `[dependency-groups] dev` + `uv.lock`; `uv lock --check` passes; `uv sync` installs it — Evidence: pyproject.toml:57; uv.lock pins `ty==0.0.55`; `uv lock --check` exit 0; `uv run ty --version` → ty 0.0.55
- [x] PASS — `Settings` exposes all 5 with exact names/types/defaults; unit test asserts every default — Evidence: settings.py:109-120; `test_lsp_defaults` PASSED; singleton → `True 'ty' ['server'] True 10.0`
- [x] PASS — `lsp_request_timeout_s` constrained `> 0`; unit-tested — Evidence: `Field(10.0, gt=0)` settings.py:120; `test_rejects_a_non_positive_lsp_request_timeout[0]`/`[-1.0]` PASSED; adversarial 0/-5/abc → ValidationError
- [x] PASS — each var env-overridable incl. list parse — Evidence: `test_reads_lsp_vars_from_process_env`, `test_loads_lsp_vars_from_a_dotenv_file` PASSED; adversarial pylsp swap + JSON list + process-over-dotenv precedence confirmed
- [x] PASS — `.env.example` lists all 5 under an LSP block; drift test — Evidence: `test_env_example_lists_every_lsp_var` PASSED; .env.example:105-121; orphan + missing scans both "none"
- [x] PASS — `make ci` green, 0 warnings — Evidence: `make ci` → 849 passed, exit 0; `filterwarnings=["error"]`

**Evidence**
```
$ make ci ; echo exit=$?
... uv lock --check → Resolved 143 packages; ruff format --check → 115 files already formatted;
    ruff check → All checks passed!; pytest → 849 passed in 8.17s
exit=0

$ uv run pytest tests/unit/decode/config/test_settings.py -q
26 passed in 0.16s   # 6 new LSP cases (5 functions, timeout parametrized x2)
```

**Other issues found**
- None blocking. Note (PASS with note): a malformed `LSP_SERVER_ARGS` (non-JSON, e.g. `server`) raises `SettingsError` at load rather than coercing a bare string into a 1-element list — this is the documented contract (.env.example: "parsed as a JSON list") and fails loudly, so it's correct, not a defect. Readers in 051/052/053/054 should keep treating absent/old `ty` as best-effort silent degradation per ADR-0007.

**VERDICT: PASS**
