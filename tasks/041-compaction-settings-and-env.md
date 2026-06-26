---
id: 041-compaction-settings-and-env
feature: context-compaction
status: pending
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

- [ ] `Settings` exposes `compaction_enabled` (`True`), `compaction_context_window_tokens` (`1_048_576`),
      `compaction_reserve_fraction` (`0.20`), `microcompaction_reserve_fraction` (`0.40`),
      `compaction_keep_recent_tokens` (`20_000`), `memory_compression_enabled` (`True`), via the singleton.
      The old `compaction_threshold_tokens` / `microcompaction_threshold_tokens` fields are **removed**.
- [ ] Each is overridable from the environment by its upper-cased name (`COMPACTION_ENABLED`,
      `COMPACTION_CONTEXT_WINDOW_TOKENS`, `COMPACTION_RESERVE_FRACTION`, `MICROCOMPACTION_RESERVE_FRACTION`,
      `COMPACTION_KEEP_RECENT_TOKENS`, `MEMORY_COMPRESSION_ENABLED`), proven by a unit test.
- [ ] The shipped defaults satisfy `microcompaction_reserve_fraction > compaction_reserve_fraction`
      (micro fires first); a unit test asserts the invariant on defaults.
- [ ] `.env.example` documents all (commented out) with concise window-relative-reserve guidance, the
      default-window note, and the memory-file switch, in the existing voice.
- [ ] `make ci` green, 0 warnings (`filterwarnings=["error"]`); no behaviour change anywhere else.

## Out of scope
- Any reader of these settings (tasks 042/044/046/047) or the logic itself.
- Auto-detecting the window from the model (pydantic-ai exposes none; the setting is the contract).
- New memory cap settings (reuse `memory_max_lines` / `memory_max_bytes`).

## Log
