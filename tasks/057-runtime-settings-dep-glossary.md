---
id: 057-runtime-settings-dep-glossary
feature: kitaru-runtime
status: done
---

# Kitaru dependency, runtime settings, .env.example mirror, and glossary rows

Tags: `data`, `infra`, `runtime`, `docs`
Depends on: #063 (the kitaru dependency + the pydantic-ai downgrade land there per ADR-0009)
Blocks: #058, #059, #060, #061, #062

This task implements ADR-0008 (Kitaru durable runtime). It lands the configuration surface, the
`kitaru` dependency, and confirms the ubiquitous-language rows the rest of the feature reads — ahead
of any reader, exactly like task 050 (LSP) and 041 (compaction) added settings before their
consumers. It stays independently shippable: no `runtime/` code yet, the live TUI is untouched.
ADR-0008 was finalized to **Accepted** and the six glossary rows were applied to `docs/glossary.md`
**at the plan gate** (the design + vocabulary of record for 058-062); this task verifies the rows are
present and used, and adds the dependency + settings.

## Scope

- **Dependency:** `uv add 'kitaru[local,pydantic-ai,llm,mcp]'` (PyPI `0.18.0`, pre-1.0 — pinned by
  `uv.lock`; `requires-python >=3.11`, compatible with the project's 3.12+). Extras: `local` (core
  + local stack), `pydantic-ai` (the `KitaruAgent` adapter decode needs), `llm`, `mcp`. This is a
  **runtime** dependency (kitaru is imported by `runtime/`, unlike `ty` which is a dev-group
  subprocess) — but it is imported **lazily** by the `decode run` entry only (task 058), so the
  REPL path never imports it. Confirm `uv lock --check` stays green and `make install` resolves it.
- **Settings** (add to `Settings` in `config/settings.py`, defaults safe for tests, all
  type-annotated; `Literal`/`Field` as the file requires), under a new
  `# --- Kitaru durable runtime (ADR-0008) ---` block. No readers yet (readers land in 058/059/061):
  - `runtime_enabled: bool = True` — master gate for the whole headless feature. `False` → `decode
    run` exits with a friendly line and never builds a flow (task 058 reads it).
  - `runtime_checkpoint_strategy: Literal["turn", "calls"] = "turn"` — `KitaruAgent` checkpoint
    granularity; `"turn"` (one checkpoint per agent turn, coarse/simplest) is the MVP default,
    `"calls"` is per model/tool call (task 058 reads it).
  - `runtime_wait_timeout_s: float = Field(600.0, gt=0)` — the durable HITL wait poll timeout in
    flow mode (matches Kitaru's local 600s default; task 059 reads it).
  - `runtime_credentials_proxy_enabled: bool = False` — when `True`, flow-mode model construction
    resolves the provider API key through Kitaru secrets instead of reading `SecretStr` from
    settings. Default **`False`** because the secrets-proxy surface is the least-exampled in Kitaru
    (ADR-0008 §5) and must be verified first (task 061 reads it).
  - `runtime_secret_name: str = "decode-llm-creds"` — the Kitaru secret name the credentials proxy
    reads the provider key from when enabled (task 061 reads it).
- **`.env.example`:** add a `# --- Kitaru durable runtime ---` block mirroring all five vars
  (commented, explain-the-default voice matching the LSP/compaction blocks), noting the runtime is a
  second, non-interactive entry path that leaves the TUI untouched, that `local` is the starting
  stack, and that the credentials proxy is opt-in/verify-first.
- **Glossary** (`docs/glossary.md`): the six rows — **Headless Runtime**, **Durable Flow**,
  **Checkpoint**, **Replay**, **Wait (HITL)**, **Credentials Proxy** — were applied at the plan gate.
  This task **verifies** they are present in the table format, cross-reference the existing
  **Decision Channel** / **Sandbox** / **Services Interface** rows correctly, and are used verbatim
  in the code/docs the later tasks ship (no non-canonical synonym is introduced).

## Acceptance criteria

- [x] `kitaru[local,pydantic-ai,llm]` is present in `pyproject.toml` + pinned in `uv.lock` (landed by
      task #063 / ADR-0009 — the `mcp` extra was dropped for a resolution conflict); this task verifies
      `uv lock --check` passes and `make install` installs it. — verified post-063 (Log: `pyproject.toml:38`,
      `uv.lock:1746`; `uv lock --check` → `Resolved 208 packages`; kitaru `0.18.0` installed).
- [x] `import kitaru` / `from kitaru import flow` succeed in the venv (verified here; the dependency
      itself lands in #063). — verified; durability surface (`flow`/`checkpoint`/`wait`) all importable +
      callable. Covered by `tests/unit/decode/test_kitaru_dependency.py` (063) — not duplicated here.
- [x] `Settings` exposes `runtime_enabled` (True), `runtime_checkpoint_strategy` ("turn"),
      `runtime_wait_timeout_s` (600.0), `runtime_credentials_proxy_enabled` (False),
      `runtime_secret_name` ("decode-llm-creds") with the exact names/types/defaults above; a unit
      test asserts every default. — `tests/unit/decode/config/test_settings.py::test_runtime_defaults`
- [x] `runtime_checkpoint_strategy` rejects a value outside `{"turn","calls"}` (Literal validation);
      `runtime_wait_timeout_s` is constrained `> 0` (a `0`/negative env value fails validation); both
      unit-tested. — `test_runtime_checkpoint_strategy_rejects_unknown_value`,
      `test_rejects_a_non_positive_runtime_wait_timeout`
- [x] Each new var is env-overridable (e.g. `RUNTIME_ENABLED=false`,
      `RUNTIME_CHECKPOINT_STRATEGY=calls`); a unit test sets them via env/`monkeypatch` and asserts the
      parsed values. — `test_reads_runtime_vars_from_process_env`, `test_loads_runtime_vars_from_a_dotenv_file`
- [x] `.env.example` lists all five vars under a Kitaru runtime block; the existing env/settings drift
      test confirms every new setting has a matching `.env.example` line. —
      `test_env_example_lists_every_runtime_var`
- [x] `docs/glossary.md` carries the six rows (Headless Runtime, Durable Flow, Checkpoint, Replay,
      Wait (HITL), Credentials Proxy) in the existing table format; a grep confirms no non-canonical
      synonym ("kitaru wrapper", "durable job", "pause tool") is introduced. — verified by grep (Log).
- [x] `make ci` is green with 0 warnings (`filterwarnings=["error"]`); `uv lock --check` passes. —
      verified post-063 with the kitaru dep present: `make ci` → `937 passed`, `uv lock --check` →
      `Resolved 208 packages`, format-check + lint-check clean.

## User stories

### Story: A developer installs the runtime extra fresh
1. Developer runs `make install`.
2. `uv sync` resolves the pinned `kitaru[local,pydantic-ai,llm,mcp]` from `uv.lock`.
3. `uv run python -c "import kitaru; from kitaru import flow; print(kitaru.__version__)"` prints
   `0.18.0` — the SDK is importable for tasks 058-062 to build a flow.

### Story: A developer turns the headless runtime off
1. Developer sets `RUNTIME_ENABLED=false` in `.env`.
2. `settings.runtime_enabled` reads `False`; task 058's `decode run` will exit with a friendly line
   (its own AC covers the behavior) — the REPL is unaffected.

### Story: A developer tunes checkpoint granularity
1. Developer sets `RUNTIME_CHECKPOINT_STRATEGY=calls` in `.env`.
2. `settings.runtime_checkpoint_strategy` reads `"calls"`; task 058 will pass it to `KitaruAgent`.
3. A bogus `RUNTIME_CHECKPOINT_STRATEGY=hourly` fails validation at load with a clear error, not a
   late crash inside the flow.

## Out of scope
- Any reader of these settings (the flow/CLI land in 058; HITL in 059; sleep in 060; credentials in 061).
- The `runtime/` package and the `decode run` subcommand (task 058).
- The ADR-0008 finalization-to-Accepted edit and the glossary-row authoring (both done at the plan gate, not in this task — this task only verifies the rows).
- A deployable entrypoint / cron (deferred to step 12 per ADR-0008 §6).

## Log

### [SWE] 2026-06-28 00:49 — Implementation (settings + glossary done; dependency BLOCKED — escalated)

**Files modified**
- `src/decode/config/settings.py` — added the `# --- Kitaru durable runtime (ADR-0008) ---` block with the five `runtime_*` settings (exact names/types/defaults from the spec).
- `.env.example` — added the mirroring `# --- Kitaru durable runtime ---` block (all five vars, commented, explain-the-default voice; notes the second non-interactive entry path, `local` as the starting stack, and the verify-first credentials proxy).
- `tests/unit/decode/config/test_settings.py` — `_RUNTIME_ENV_VARS` + 8 tests (defaults, env override, dotenv, each valid Literal, bad-strategy rejection, non-positive wait-timeout rejection, `.env.example` drift guard).
- `pyproject.toml` / `uv.lock` — **NOT modified**: `uv add 'kitaru[...]'` does not resolve (see Blocker).

**Tests**
- Unit: 923 passing, 0 failing (`make pre-commit`); settings file alone 35 passing.
- Integration: N/A — no infra changes.

**Acceptance criteria**
- [x] Settings exposes the 5 `runtime_*` vars with exact defaults — `test_runtime_defaults`.
- [x] Literal + `gt=0` validation — `test_runtime_checkpoint_strategy_rejects_unknown_value`, `test_rejects_a_non_positive_runtime_wait_timeout`.
- [x] Env-overridable — `test_reads_runtime_vars_from_process_env`, `test_loads_runtime_vars_from_a_dotenv_file`.
- [x] `.env.example` drift guard — `test_env_example_lists_every_runtime_var`.
- [x] Glossary: six rows present in table format, cross-refs (Decision Channel / Sandbox / Services Interface) correct, no non-canonical synonym — verified by grep (Evidence).
- [ ] [BLOCKED] kitaru in `pyproject.toml`+`uv.lock`; `import kitaru` / `from kitaru import flow`.

**Evidence**
```
$ grep -rni "kitaru wrapper|durable job|pause tool" docs/glossary.md src/ tests/
(no matches)
$ grep -c "Headless Runtime|Durable Flow|Checkpoint|Replay|Wait (HITL)|Credentials Proxy" docs/glossary.md
6

$ make pre-commit
... 923 passed in 8.46s
$ uv lock --check
Resolved 143 packages in 2ms        # lock untouched — green

# end-to-end smoke (settings surface):
$ uv run python -c "Settings(_env_file=None) ..."
runtime_enabled = True | checkpoint_strategy = turn | wait_timeout_s = 600.0
  credentials_proxy_enabled = False | secret_name = decode-llm-creds
$ RUNTIME_ENABLED=false RUNTIME_CHECKPOINT_STRATEGY=calls uv run python -c ...
runtime_enabled = False | strategy = calls
$ RUNTIME_CHECKPOINT_STRATEGY=hourly uv run python -c ...
ValidationError: runtime_checkpoint_strategy — Input should be 'turn' or 'calls' (literal_error)  # fails fast at load
```

**Blocker — `kitaru[local,pydantic-ai,llm,mcp]==0.18.0` does not resolve (hard, irreconcilable).**

`uv add` was probed for every extras subset down to the bare core; ALL fail. Two independent conflicts,
both against the project's *own* pinned core deps (not the droppable `llm`/`mcp` extras the task
anticipated):

1. **Core (even bare `kitaru==0.18.0`):** `kitaru → zenml[local]>=0.95.1 → pydantic>=2.0,<=2.12.5`.
   The project requires `pydantic>=2.13.4` (locked `2.13.4`). `zenml 0.95.1` is the latest zenml, so the
   `<=2.12.5` ceiling cannot move. **Irreconcilable.**
2. **`pydantic-ai` extra:** `kitaru[pydantic-ai] → pydantic-ai-slim>=1.89.0,<1.104.0` (the 1.x line).
   The project requires `pydantic-ai>=2.0.0` (locked `2.0.0`, released 2026-06-23 — 5 days ago).
   **Irreconcilable.**

`kitaru==0.18.0` is the latest kitaru on PyPI (probed: 0.1.0 … 0.18.0); no newer release exists that
supports pydantic 2.13 / pydantic-ai 2.0. The task's documented fallback ("drop `llm`/`mcp`, keep
`local,pydantic-ai`") cannot help: the must-have `pydantic-ai` extra conflicts, AND the core conflicts
on `pydantic` regardless of extras. Landing kitaru therefore requires changing the project's own
`pydantic` (`>=2.13.4` → `<=2.12.5`) and `pydantic-ai` (`>=2.0.0` → `1.x`) pins — a project-wide
downgrade of the agent loop's core framework that ADR-0008 / this task did not decide.

**→ Escalated to the orchestrator/PA as an undocumented architectural fork** (see hand-off message).
The settings + `.env.example` + glossary verification are complete, green, and independent of the dep
landing; they are ready for the Tester once the fork is resolved (or for landing the dep when kitaru
ships a pydantic-2.13 / pydantic-ai-2.0-compatible release).

**Notes**
- pyproject.toml / uv.lock deliberately left unchanged — adding kitaru with `--frozen` would break `make install`.
- No `runtime/` code and no `decode run` subcommand (out of scope — task 058). The live TUI is untouched.

### [SWE] 2026-06-28 — Finalization (dependency unblocked by #063)

The architectural fork escalated above was resolved by ADR-0009 and landed in task **#063**: the
project pins were moved (`pydantic-ai 2.0 → >=1.89,<1.104`, `pydantic <2.13`, `click <8.3`) and
`kitaru[local,pydantic-ai,llm]>=0.18.0` was added (the `mcp` extra dropped for a resolution
conflict). #063 committed `pyproject.toml` / `uv.lock` / `loop.py` and added
`tests/unit/decode/test_kitaru_dependency.py`. This entry finalizes 057's previously-BLOCKED
dependency ACs and re-verifies the settings + glossary work under the **new** environment
(pydantic-ai 1.94.0, kitaru 0.18.0 installed).

**Files modified (this finalization)**
- `tasks/057-runtime-settings-dep-glossary.md` — checked off the two dependency ACs + the CI AC; this log.
- (057's `src/decode/config/settings.py`, `.env.example`, `tests/unit/decode/config/test_settings.py`
  are unchanged from the earlier pass — re-verified correct, not re-edited. `pyproject.toml`/`uv.lock`
  are #063's and were NOT touched.)

**Verification**
- Dependency present: `pyproject.toml:38` `kitaru[local,pydantic-ai,llm]>=0.18.0`; `uv.lock:1746`
  `name = "kitaru"` with extras `["llm","local","pydantic-ai"]`. `uv lock --check` → `Resolved 208 packages`.
- Imports succeed under the venv: `import kitaru` + `from kitaru import flow, checkpoint, wait`
  (all callable); installed `kitaru 0.18.0`, `pydantic-ai 1.94.0` (1.x line per ADR-0009). The import
  smoke is owned by `test_kitaru_dependency.py` (#063) — **not duplicated** in the settings tests.
- Settings unchanged + correct: the 5 `runtime_*` fields carry the exact names/types/defaults
  (`runtime_enabled=True`, `runtime_checkpoint_strategy="turn"`, `runtime_wait_timeout_s=600.0` `gt=0`,
  `runtime_credentials_proxy_enabled=False`, `runtime_secret_name="decode-llm-creds"`). 35 settings
  tests pass under pydantic-ai 1.x (settings are SDK-independent — confirmed).
- `.env.example` `# --- Kitaru durable runtime ---` block mirrors all five vars; drift guard
  `test_env_example_lists_every_runtime_var` green.
- Glossary: six rows present + cross-referenced (Decision Channel / Sandbox via `build_agent()` /
  Services Interface); grep for non-canonical synonyms (`kitaru wrapper|durable job|pause tool`) → none.

**Tests**
- `make ci` → **937 passed** (925 unit + 12 integration), 0 warnings (`filterwarnings=["error"]`).
- `make format-fix` / `make lint-fix` → clean (129 files unchanged; all checks passed).
- `make pre-commit` → 925 passed. Settings + kitaru subset: `test_settings.py` + `test_kitaru_dependency.py` → 37 passed.

**Evidence**
```
$ uv lock --check
Resolved 208 packages in 3ms
$ uv run python -c "import kitaru; from kitaru import flow, checkpoint, wait; print(callable(flow), callable(checkpoint), callable(wait))"
True True True
$ uv run python -c "from importlib.metadata import version; print('kitaru', version('kitaru'), '| pydantic-ai', version('pydantic-ai'))"
kitaru 0.18.0 | pydantic-ai 1.94.0
$ RUNTIME_CHECKPOINT_STRATEGY=hourly uv run python -c "import decode.config.settings"
ValidationError — rejected fast at load   # bogus Literal fails at module import, not a late crash
$ make ci
... 937 passed in 9.62s
```

**Notes**
- All 057 ACs now pass; nothing left BLOCKED/PARTIAL. The escalated fork is closed by ADR-0009/#063.
- Did NOT modify any #063-owned file (pyproject.toml, uv.lock, loop.py, test_kitaru_dependency.py) or any later task's files. NOT committed — handing to the Tester first.

### [Tester] 2026-06-28 02:14 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` → 129 files formatted; `ruff check` → all checks passed)
- Unit tests: 925 passed / 0 failed (`make pre-commit`)
- Integration tests: 12 passed / 0 failed (`make integration-tests`)
- Warnings: 0 (`filterwarnings=["error"]`)

**File ownership**
- `git status` shows 057 touches ONLY its 4 files (`.env.example`, `src/decode/config/settings.py`,
  `tests/unit/decode/config/test_settings.py`, `tasks/057-...md`). #063-owned files
  (`pyproject.toml`, `uv.lock`, `src/decode/agent/loop.py`, `tests/.../test_kitaru_dependency.py`) are
  clean — confirmed `git status --porcelain` on each returns empty.

**E2E adversarial pass** (settings surface — no readers yet, so the surface IS the load-time parse)
- Happy path: `Settings(_env_file=None)` → enabled=True, strategy='turn', wait=600.0 (float),
  proxy_enabled=False, secret_name='decode-llm-creds' (PASS)
- Real process-env override: `RUNTIME_ENABLED=false RUNTIME_CHECKPOINT_STRATEGY=calls
  RUNTIME_WAIT_TIMEOUT_S=42.5 Settings()` → enabled=False | strategy=calls | wait=42.5 (PASS)
- Break 1 (malformed Literal): `RUNTIME_CHECKPOINT_STRATEGY=hourly` → `ValidationError: Input should
  be 'turn' or 'calls' [literal_error]` fast at load, not a late flow crash (PASS)
- Break 2 (boundary: non-positive float): `RUNTIME_WAIT_TIMEOUT_S=0` and `=-5` → `ValidationError:
  Input should be greater than 0 [greater_than]` at load (PASS — `Field(gt=0)` fires)
- Break 3 (boundary: empty string Literal): `RUNTIME_CHECKPOINT_STRATEGY=""` → ValidationError (PASS)
- Break 4 (wrong type): `RUNTIME_WAIT_TIMEOUT_S=abc` → ValidationError (PASS)
- Security: secret-default scan over `model_dump()` runtime_* fields → none carries a key/token-looking
  default; `runtime_credentials_proxy_enabled` defaults OFF (safe). (PASS)

**Acceptance criteria**
- [x] PASS — kitaru in pyproject + uv.lock, `uv lock --check` green — `pyproject.toml:38`
      `kitaru[local,pydantic-ai,llm]>=0.18.0`; `uv lock --check` → `Resolved 208 packages`.
- [x] PASS — `import kitaru` / `from kitaru import flow` succeed — `kitaru 0.18.0 | pydantic-ai 1.94.0
      | flow callable True`; covered by `test_kitaru_dependency.py` (063), 2 passing.
- [x] PASS — 5 `runtime_*` settings with exact names/types/defaults —
      `test_settings.py::test_runtime_defaults`; `settings.py:126-139`; manual dump confirms.
- [x] PASS — Literal + `gt=0` validation fires at load —
      `test_runtime_checkpoint_strategy_rejects_unknown_value`, `test_rejects_a_non_positive_runtime_wait_timeout`
      (parametrized 0/-1.0); reproduced manually above.
- [x] PASS — env-overridable — `test_reads_runtime_vars_from_process_env`,
      `test_loads_runtime_vars_from_a_dotenv_file`; reproduced with real process env above.
- [x] PASS — `.env.example` lists all 5 vars; drift guard genuine — `test_env_example_lists_every_runtime_var`;
      verified the guard would raise if a var were dropped (not a trivially-passing stub).
- [x] PASS — 6 glossary rows present, well-formed table rows, cross-ref targets exist, no synonym —
      `grep` → 6 rows (`^| **Term** |`), Decision Channel/Sandbox/Services Interface rows all present;
      `kitaru wrapper|durable job|pause tool` absent from docs/src/tests.
- [x] PASS — `make ci` green, 0 warnings, `uv lock --check` passes — 925 unit + 12 integration = 937,
      format/lint clean.

**Evidence**
```
$ make pre-commit
... 925 passed in 9.25s
$ make integration-tests
... 12 passed in 1.88s
$ uv run pytest tests/unit/decode/config/test_settings.py tests/unit/decode/test_kitaru_dependency.py -q
37 passed in 0.89s
$ RUNTIME_CHECKPOINT_STRATEGY=hourly  → ValidationError: Input should be 'turn' or 'calls' [literal_error]
$ RUNTIME_WAIT_TIMEOUT_S=0 / -5       → ValidationError: Input should be greater than 0 [greater_than]
$ uv lock --check                     → Resolved 208 packages
```

**Other issues found**
- None. Settings comments, `.env.example` voice, and glossary rows all match the spec; no `print()`,
  full type annotations, proxy defaults safe-off, no secret defaults.

**VERDICT: PASS**
