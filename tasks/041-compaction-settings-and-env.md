---
id: 041-compaction-settings-and-env
feature: context-compaction
status: done
---

# Compaction + memory-compression settings + .env.example surface (window-relative)

Implements [ADR-0006](../docs/adr/0006-conversation-compaction.md) §1-3 (config surface). No behaviour
yet, so it ships independently and leaves the codebase green. Follows the existing settings convention
(module-level `settings` singleton, every var mirrored in `.env.example`).
Depends on: None · Blocks: 042, 044, 046, 047

## Scope

Add to `Settings` in `src/decode/config/settings.py`, in a new `# --- Context compaction (ADR-0006) ---`
block near the persistence block:

- `compaction_enabled: bool = True` — master switch for the **automatic** conversation cascade (both
  tiers). Manual `/compact` (task 045) ignores it.
- `compaction_context_window_tokens: int = 1_048_576` — the active model's MAX **input** context window
  in tokens. Default = Gemini 2.5 Flash's input window (`1_048_576`); document "set this to YOUR active
  model's input window." **Single source of truth** also used by the TUI fill gauge (task 047).
  pydantic-ai 2.0.0 exposes no model context window (`ModelProfile` has no such field — verified), so
  this configurable number is the contract; no auto-detect.
- `compaction_reserve_fraction: float = 0.20` — **full** compaction fires when
  `input_tokens >= context_window * (1 - compaction_reserve_fraction)` (i.e. at 80% full). Configurable.
- `microcompaction_reserve_fraction: float = 0.40` — **micro**compaction fires EARLIER, at
  `context_window * (1 - microcompaction_reserve_fraction)` (60% full). Configurable.
  **INVARIANT:** `microcompaction_reserve_fraction > compaction_reserve_fraction` (micro reserves more →
  fires first); assert it on defaults (replaces the old flat `micro < full` invariant).
- `compaction_keep_recent_tokens: int = 20_000` — token budget of the recent tail kept verbatim by full
  compaction, and the cutoff microcompaction treats as "recent" (snapped to a turn boundary by task 042).
- `memory_compression_enabled: bool = True` — second level: when set, the on-exit `MEMORY.md` LLM
  compressor (task 046) runs at the `memory_max_lines` (200) cap instead of pure drop-oldest. Reuses the
  existing `memory_max_lines` / `memory_max_bytes` caps — no new memory cap settings.

Mirror all in `.env.example` under a `# --- Context compaction ---` block, commented out (defaults are
safe), each with a one-line note in the existing voice — including that thresholds are **window-relative
reserves** (`micro` reserves more so it fires first), that the window is your active model's input window
(default Gemini 2.5 Flash 1_048_576), that the trigger uses provider-reported input tokens, and that
`COMPACTION_ENABLED` gates only the automatic cascade while `MEMORY_COMPRESSION_ENABLED` governs only the
on-exit memory file.

## Acceptance criteria

- [x] `Settings` exposes `compaction_enabled` (`True`), `compaction_context_window_tokens` (`1_048_576`),
      `compaction_reserve_fraction` (`0.20`), `microcompaction_reserve_fraction` (`0.40`),
      `compaction_keep_recent_tokens` (`20_000`), `memory_compression_enabled` (`True`), via the singleton.
      The old `compaction_threshold_tokens` / `microcompaction_threshold_tokens` fields are **removed**.
- [x] Each is overridable from the environment by its upper-cased name (`COMPACTION_ENABLED`,
      `COMPACTION_CONTEXT_WINDOW_TOKENS`, `COMPACTION_RESERVE_FRACTION`, `MICROCOMPACTION_RESERVE_FRACTION`,
      `COMPACTION_KEEP_RECENT_TOKENS`, `MEMORY_COMPRESSION_ENABLED`), proven by a unit test.
- [x] The shipped defaults satisfy `microcompaction_reserve_fraction > compaction_reserve_fraction`
      (micro fires first); a unit test asserts the invariant on defaults.
- [x] `.env.example` documents all (commented out) with concise window-relative-reserve guidance, the
      default-window note, and the memory-file switch, in the existing voice.
- [x] `make ci` green, 0 warnings (`filterwarnings=["error"]`); no behaviour change anywhere else.

## Out of scope
- Any reader of these settings (tasks 042/044/046/047) or the logic itself.
- Auto-detecting the window from the model (pydantic-ai exposes none; the setting is the contract).
- New memory cap settings (reuse `memory_max_lines` / `memory_max_bytes`).

## Log

### [SWE] 2026-06-26 21:49 — Implementation

**Files modified**
- `src/decode/config/settings.py` — new `# --- Context compaction (ADR-0006) ---` block with the six
  fields (`compaction_enabled`, `compaction_context_window_tokens`, `compaction_reserve_fraction`,
  `microcompaction_reserve_fraction`, `compaction_keep_recent_tokens`, `memory_compression_enabled`).
- `.env.example` — new `# --- Context compaction ---` block, all six mirrored (commented out) with
  window-relative-reserve guidance, the default-window note, and the two switch scopes.
- `tests/unit/decode/config/test_settings.py` — added `_COMPACTION_ENV_VARS` (cleared-env style mirroring
  `_PROVIDER_ENV_VARS`) and four tests: defaults, the `micro > full` invariant on defaults, process-env
  overrides per var, and `.env` dotenv loading.

**Tests**
- Unit: 18 passing in `test_settings.py` (4 new), 0 failing — full `make ci`: 740 passing, 0 warnings.
- Integration: N/A — settings-only, no infra changes (capstone tests still pass under `make ci`).

**Acceptance criteria**
- [x] Six fields exposed via the singleton with stated defaults; no old `*_threshold_tokens` fields
      existed (grep confirmed clean slate — nothing to remove) — `test_compaction_defaults`.
- [x] Each overridable by upper-cased env name — `test_reads_compaction_vars_from_process_env`,
      `test_loads_compaction_vars_from_a_dotenv_file`.
- [x] `microcompaction_reserve_fraction > compaction_reserve_fraction` on defaults —
      `test_microcompaction_reserves_more_than_full_on_defaults`.
- [x] `.env.example` documents all six (commented out) in the existing voice.
- [x] `make ci` green, 0 warnings.

**Evidence**
```
$ uv run pytest tests/unit/decode/config/test_settings.py -q
..................                                                       [100%]
18 passed in 0.15s

$ make ci   (tail)
tests/integration/test_milestone1_capstone.py .                          [ 99%]
tests/integration/test_milestone3_skills_capstone.py .......             [100%]
============================= 740 passed in 7.85s ==============================

$ uv run python -c "from decode.config.settings import settings; ..."
  compaction_enabled               = True
  compaction_context_window_tokens = 1048576
  compaction_reserve_fraction      = 0.2
  microcompaction_reserve_fraction = 0.4
  compaction_keep_recent_tokens    = 20000
  memory_compression_enabled       = True
invariant micro>full holds: True
env override window = 200000 | enabled = False
```

**Notes**
- Settings-only as scoped — no readers/logic added (tasks 042/044/046/047 consume these).
- The "old fields removed" criterion was vacuously satisfied: `compaction_threshold_tokens` /
  `microcompaction_threshold_tokens` never existed in the tree (grep across `src/`, `tests/`,
  `.env.example` returned nothing), so this branch introduces the window-relative fields directly.
- NOT COMMITTED — handing to the Tester first.

### [Tester] 2026-06-26 22:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 112 files; `ruff check` all passed)
- Unit tests: 732 passed / 0 failed
- Integration tests: 8 passed / 0 failed
- `make ci`: PASS (740 passed, exit 0, `uv lock --check` clean)
- Warnings: 0 (`filterwarnings=["error"]`)

**E2E adversarial pass** (surface = `Settings` + env binding; settings-only task, no readers yet)
- Happy path: `python -c "from decode.config.settings import settings; ..."` → all six fields at
  stated defaults (`True / 1048576 / 0.2 / 0.4 / 20000 / True`); `micro>full` = True. PASS
- Break 1 (env override, float+int): `COMPACTION_RESERVE_FRACTION=0.33 COMPACTION_CONTEXT_WINDOW_TOKENS=128000`
  → `0.33` (float), `128000` (int), correctly typed. PASS
- Break 2 (malformed: `COMPACTION_RESERVE_FRACTION=notanumber`) → clean pydantic `ValidationError`
  naming `compaction_reserve_fraction` ("Input should be a valid number"); `COMPACTION_ENABLED=maybe`
  → `ValidationError` naming `compaction_enabled`. Fail-fast, field-named — identical to every other
  numeric/bool setting; no NEW unhelpful failure mode. PASS
- Break 2c (bool variants): `true/True/1/yes/on` → True; `false/0/no/off` → False. Predictable. PASS
- Break 3 (precedence): `.env` window=111111 + process `COMPACTION_CONTEXT_WINDOW_TOKENS=999999`
  → window=999999 (process wins), reserve=0.11 (from `.env`, no override). Correct precedence. PASS
- Break 4 (invariant under override): `COMPACTION_RESERVE_FRACTION=0.50 MICROCOMPACTION_RESERVE_FRACTION=0.10`
  → loads with `micro>full` = False, no runtime guard. By design — spec asserts the invariant on
  DEFAULTS only; no readers exist yet to break. PASS (see note below).
- Break 5 (boundary): negative fraction / negative keep-recent accepted as-is (no `ge/le` constraints,
  consistent with the rest of `settings.py`, e.g. `bash_timeout_s`). PASS (see note below).

**Acceptance criteria**
- [x] PASS — Six fields exposed via singleton with stated defaults; old `*_threshold_tokens` removed
      — `test_compaction_defaults` PASSED; `settings.py:70-85`; `grep -rn threshold_tokens src/ tests/
      .env.example docs/` → NO MATCHES (vacuously removed; never existed — clean slate confirmed).
- [x] PASS — Each overridable by upper-cased env name — `test_reads_compaction_vars_from_process_env`
      + `test_loads_compaction_vars_from_a_dotenv_file` PASSED; manual Break 1 & Break 3 confirm.
- [x] PASS — Defaults satisfy `microcompaction_reserve_fraction > compaction_reserve_fraction` —
      `test_microcompaction_reserves_more_than_full_on_defaults` PASSED; happy path shows 0.4 > 0.2.
- [x] PASS — `.env.example` documents all six commented, window-relative-reserve guidance + default-window
      note + memory switch, existing voice — `.env.example:71-87` (all six `# VAR=...` lines present).
- [x] PASS — `make ci` green, 0 warnings, no behaviour change elsewhere — `make ci` exit 0, 740 passed,
      0 warnings; `settings.py` diff purely additive (no removed lines); only `settings.py` +
      `test_settings.py` changed under `src/`/`tests/`.

**Evidence**
```
$ make unit-tests            → 732 passed in 7.60s   (exit 0)
$ make integration-tests     → 8 passed in 1.88s     (exit 0)
$ make ci                    → 740 passed in 7.95s   (exit 0; uv lock --check clean; 0 warnings)
$ uv run pytest tests/unit/decode/config/test_settings.py -v → 18 passed (4 new compaction tests)
```

**Other issues found** (non-blocking notes — none affect this settings-only task's ACs)
- `docs/architecture.png` is modified in the working tree but is UNRELATED to task 041 (last touched in
  the scaffold commit; not mentioned in the SWE report). It must be EXCLUDED from the task 041 commit —
  commit only `settings.py`, `.env.example`, `test_settings.py`, and this task file. Nothing is staged
  yet, so no sloppy commit has occurred; flagging for commit hygiene.
- No runtime validator enforces `micro>full` or fraction bounds `(0,1)` or non-negative token counts;
  invalid combos (Break 4/5) load silently. This is correct for THIS task (spec scopes the invariant to
  defaults; no readers exist yet). Suggested follow-up for the reader tasks (042/044): a `model_validator`
  to enforce `micro>full` and `0<reserve<1` at runtime, or defensive readers.

**VERDICT: PASS**
