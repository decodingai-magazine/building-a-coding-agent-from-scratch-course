---
id: 047-context-window-gauge
feature: context-compaction
status: done
---

# TUI context-window fill gauge (footer progress circle)

Implements [ADR-0006](../docs/adr/0006-conversation-compaction.md) §9 — a small fill gauge in the TUI
footer showing how full the context window is, colored to warn as it approaches the compaction tiers.
Reads the SAME `compaction_context_window_tokens` setting (single source of truth) and the handler's
`last_input_tokens` (task 044). Pure render helper + footer wiring; no integration test needed.
Depends on: 041, 044 · Blocks: 048

## Scope

- **Pure helper in `src/decode/tui/render.py`** — `context_gauge(fraction, *, warn_at, danger_at) ->
  tuple[str, str]` returning `(label, color)`:
  - Clamp `fraction` to `[0.0, 1.0]`.
  - **Glyph** = `"○◔◑◕●"[round(clamped * 4)]` (0% → `○`, 25% → `◔`, 50% → `◑`, 75% → `◕`, 100% → `●`).
  - **`label`** = `f"{glyph} {round(clamped * 100)}%"` (e.g. `◕ 78%`).
  - **`color`** by the same tier lines: `"red"` if `clamped >= danger_at`, `"yellow"` if
    `clamped >= warn_at`, else `"green"`. (`warn_at`/`danger_at` are the *fill* fractions; the call site
    derives them from the reserve settings so the colors track the actual compaction tiers.)
  Returns plain data (`str`, color name common to Rich + prompt_toolkit) so it is fully unit-testable and
  decoupled from any toolkit. (A thin `render_context_gauge(...) -> Text` wrapper for Rich surfaces is
  optional; the footer uses the tuple.)
- **Footer wiring in `src/decode/tui/app.py`** — in `_bottom_toolbar` (the prompt_toolkit bottom toolbar):
  - Compute `window = settings.compaction_context_window_tokens`;
    `fraction = handler.last_input_tokens / window` if `window > 0` else `0.0`.
  - `warn_at = 1 - settings.microcompaction_reserve_fraction` (0.60 default);
    `danger_at = 1 - settings.compaction_reserve_fraction` (0.80 default).
  - `label, color = context_gauge(fraction, warn_at=warn_at, danger_at=danger_at)`.
  - Render the gauge alongside the existing `footer_hint`, mapping `color` to a prompt_toolkit color tag
    (e.g. `HTML(f'<style fg="{color}">{label}</style> | …')`). `_bottom_toolbar` now also takes the
    `handler` (passed via the existing `bottom_toolbar=lambda: _bottom_toolbar(deps, gate, handler)`
    closure — late-bound, invoked only during the prompt loop after the handler exists; construct the
    handler before the `PromptSession` if clearer).
  - **Before the first turn** `last_input_tokens` is `0` → the gauge shows `○ 0%` (empty, green).

## Acceptance criteria

- [x] `context_gauge` glyph buckets: `0.0 → "○"`, `0.25 → "◔"`, `0.5 → "◑"`, `0.75 → "◕"`, `1.0 → "●"`;
      `0.78 → "◕"` (round 3.12→3) — asserted across buckets incl. 0% and 100%.
- [x] `context_gauge` colors: `green` below `warn_at`, `yellow` in `[warn_at, danger_at)`, `red` at/above
      `danger_at` — asserted at the default fill lines (0.60 / 0.80) including the exact boundaries.
- [x] `label` formats as `"{glyph} {pct}%"` with `pct` the rounded clamped percentage; `fraction` outside
      `[0,1]` is clamped (e.g. `1.4 → "● 100%"`, red; `-0.1 → "○ 0%"`, green).
- [x] The footer reads `handler.last_input_tokens` via the **public property** (never a private attr) and
      `settings.compaction_context_window_tokens`; before the first turn it renders `○ 0%` (green).
- [x] `warn_at`/`danger_at` are derived from `microcompaction_reserve_fraction` /
      `compaction_reserve_fraction`, so the gauge colors track the actual compaction tiers (single source
      of truth) — verified by a unit test computing the fill lines from the settings defaults.
- [x] `make ci` green, 0 warnings; unit-tested in `tests/unit/decode/tui/test_render.py` (+ footer
      assertion in the existing app test if practical). No new integration test required.

## Out of scope
- Any new compaction behaviour (tasks 042/044) — the gauge only *displays* `last_input_tokens / window`.
- A live/animated redraw beyond prompt_toolkit's normal per-render toolbar refresh.
- An alternate gauge style (bar/ring) — swap-able later in this one helper if the user prefers.

## Log

### [SWE] 2026-06-26 23:43 — Implementation

**Files modified**
- `src/decode/tui/render.py` — added pure `context_gauge(fraction, *, warn_at, danger_at) -> (label, color)` helper (clamp → glyph bucket via `round(clamped*4)` → `"{glyph} {pct}%"` label → tier color), plus the `_GAUGE_GLYPHS` constant.
- `src/decode/tui/app.py` — `_bottom_toolbar` now takes `handler`, computes `fraction = last_input_tokens / window`, derives `warn_at`/`danger_at` from the reserve settings, and renders `<style fg="{color}">{label}</style>` ahead of the existing bold `footer_hint`; the toolbar closure passes the late-bound `handler`.
- `tests/unit/decode/tui/test_render.py` — `context_gauge` unit tests: glyph buckets, label format, tier colors (incl. exact 0.60/0.80 boundaries), clamping, and a single-source-of-truth test deriving the fill lines from the settings defaults.
- `tests/unit/decode/tui/test_app.py` — updated `test_bottom_toolbar_reads_the_live_agent_and_mode` to the new 3-arg signature; added footer tests for the empty-green gauge before the first turn (real handler property) and the red near-full gauge reading the public `last_input_tokens`.

**Tests**
- Unit: 831 passing, 0 failing (`make pre-commit`). The two touched files: 40 in `test_render.py`, 100 in `test_app.py`.
- Integration: N/A — no infra changes (full `make ci` still runs the 2 capstones; both pass, no network).

**Acceptance criteria**
- [x] glyph buckets incl. `0.78 → ◕` — `tests/unit/decode/tui/test_render.py::test_context_gauge_glyph_buckets`
- [x] colors green/yellow/red at default fill lines incl. exact 0.60/0.80 boundaries — `::test_context_gauge_colors_track_the_tier_lines`
- [x] label `"{glyph} {pct}%"` + clamping (1.4→`● 100%` red, -0.1→`○ 0%` green) — `::test_context_gauge_label_is_glyph_space_percent`, `::test_context_gauge_clamps_overflow_to_full_red`, `::test_context_gauge_clamps_underflow_to_empty_green`
- [x] footer reads the public `last_input_tokens` + window setting; `○ 0%` green before the first turn — `tests/unit/decode/tui/test_app.py::test_bottom_toolbar_shows_an_empty_green_gauge_before_the_first_turn`, `::test_bottom_toolbar_gauge_reads_the_public_property_and_colors_by_fill`
- [x] warn_at/danger_at derived from the reserve settings — `tests/unit/decode/tui/test_render.py::test_context_gauge_tiers_derive_from_the_reserve_settings`
- [x] `make ci` green, 0 warnings, no new integration test

**Evidence**
```
$ make ci
... ruff format --check + ruff check pass; uv lock --check passes ...
tests/integration/test_milestone1_capstone.py .
tests/integration/test_milestone3_skills_capstone.py .......
============================= 839 passed in 7.68s ==============================

$ uv run python scratchpad/e2e_gauge.py   # real _bottom_toolbar + real handler + real settings
derived fill lines: warn_at=0.6  danger_at=0.8
  fraction=  0.0  ->  ○ 0%      green
  fraction=  0.6  ->  ◑ 60%     yellow
  fraction=  0.8  ->  ◕ 80%     red
  fraction=  0.9  ->  ● 90%     red
  fraction=  1.4  ->  ● 100%    red
  fraction= -0.1  ->  ○ 0%      green
_bottom_toolbar BEFORE the first turn (last_input_tokens=0):
  raw markup: <style fg="green">○ 0%</style> <b>agent:build mode:default | Enter steer | ...</b>
  parsed fragments: FormattedText([('fg:green', '○ 0%'), ('', ' '), ('class:b', 'agent:build mode:default | ...')])
_bottom_toolbar at 90% full (last_input_tokens=943718):
  raw markup: <style fg="red">● 90%</style> <b>agent:build mode:default | ...</b>
  parsed fragments: FormattedText([('fg:red', '● 90%'), ('', ' '), ('class:b', 'agent:build mode:default | ...')])
```

**Notes**
- The gauge label uses no HTML-special characters (`<`/`>`/`&`), and neither does `footer_hint`, so the existing escaping/format is preserved — the emitted prompt_toolkit `HTML` parses cleanly (verified by the e2e `to_formatted_text` round-trip above).
- Skipped the optional `render_context_gauge(...) -> Text` Rich wrapper (no second caller — the footer uses the tuple).
- Did NOT commit; left `docs/architecture.png` untouched. Ready for Tester review.

### [Tester] 2026-06-26 23:58 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 114 files formatted; `ruff check` all passed)
- Unit tests: 831 passed / 0 failed
- Integration tests: 8 passed / 0 failed (`make ci` total 839 passed; `uv lock --check` clean)
- Warnings: 0 (`filterwarnings = ["error"]` in pyproject.toml — any warning would fail the run)

**E2E adversarial pass** (pure `context_gauge` helper + `_bottom_toolbar` footer, no network)
- Happy path: `_bottom_toolbar(deps, gate, handler)` before first turn (real `AgentTurnHandler`, `last_input_tokens==0`) → `<style fg="green">○ 0%</style> <b>agent:build mode:default | …</b>`, parses to `FormattedText([('fg:green','○ 0%'),…])` (PASS)
- Break path 1 (boundary: rounding edges / IndexError hunt): swept 1001 fractions 0.000→1.000 + edge probes (0.124, 0.125, 0.374, 0.375, 0.624, 0.625, 0.78, 0.874, 0.875, 0.999) → `round(clamped*4)` stayed in [0,4] for every value, zero out-of-range indices, no IndexError; `0.78 → ◕`, `0.875 → ●` (PASS)
- Break path 2 (boundary: clamping / extreme + NaN/inf): `1.4 / 99 / 1e9 → ● 100% red`; `-0.1 / -50 / -1e9 → ○ 0% green`; `nan → ○ 0% green`, `+inf → ● 100% red`, `-inf → ○ 0% green` — no crash on any (PASS)
- Break path 3 (state: `window == 0` guard): forced `compaction_context_window_tokens = 0`, footer with `last_input_tokens=123456` → `○ 0%` green, NO ZeroDivisionError (guard `if window > 0 else 0.0` holds) (PASS)
- Break path 4 (colors at exact tier boundaries): `0.5999999 → green`, `0.60 → yellow`, `0.7999999 → yellow`, `0.80 → red` (inclusive `>=` upper tier) (PASS)
- Break path 5 (security: HTML/markup injection): label uses only `○◔◑◕● ` + digits + `%` (no `<`/`>`/`&`); emitted prompt_toolkit `HTML` round-trips through `to_formatted_text` cleanly at fractions 0.0/0.6/0.8/1.4 — no XMLSyntaxError, no injection surface (PASS)
- Break path 6 (single-source-of-truth): moved reserves to `microcompaction_reserve_fraction=0.90` / `compaction_reserve_fraction=0.70` → `warn_at` fell to 0.10, and a 15%-full window flipped green→yellow — the fill line genuinely tracks the settings (PASS)

**Acceptance criteria**
- [x] PASS — glyph buckets `0.0→○ 0.25→◔ 0.5→◑ 0.75→◕ 1.0→●`, `0.78→◕` — `test_render.py::test_context_gauge_glyph_buckets` (6 params green) + adversarial sweep (no IndexError across 1001 values + bucket edges)
- [x] PASS — colors green/yellow/red at default 0.60/0.80 fill lines incl. exact boundaries — `test_render.py::test_context_gauge_colors_track_the_tier_lines` (7 params) + adversarial boundary probe
- [x] PASS — label `"{glyph} {pct}%"` + clamping (`1.4→● 100%` red, `-0.1→○ 0%` green) — `::test_context_gauge_label_is_glyph_space_percent`, `::test_context_gauge_clamps_overflow_to_full_red`, `::test_context_gauge_clamps_underflow_to_empty_green`
- [x] PASS — footer reads PUBLIC `handler.last_input_tokens` (zero private `_last_input_tokens` reads in `app.py:501`) + `settings.compaction_context_window_tokens`; `○ 0%` green before first turn — `test_app.py::test_bottom_toolbar_shows_an_empty_green_gauge_before_the_first_turn`, `::test_bottom_toolbar_gauge_reads_the_public_property_and_colors_by_fill`
- [x] PASS — `warn_at`/`danger_at` derived from `microcompaction_reserve_fraction`/`compaction_reserve_fraction` — `test_render.py::test_context_gauge_tiers_derive_from_the_reserve_settings` + adversarial reserve-move probe (fill line shifted with the setting)
- [x] PASS — `make ci` green, 0 warnings, no new integration test — 839 passed, lock/format/lint clean

**Evidence**
```
$ make ci
uv lock --check            → Resolved 142 packages
ruff format --check        → 114 files already formatted
ruff check                 → All checks passed!
============================= 839 passed in 7.65s ==============================

$ uv run python scratchpad/adv047.py   # pure helper, full-range adversarial sweep
  swept 1001 values, out-of-range indices: NONE
  nan -> '○ 0%' green | +inf -> '● 100%' red | 1.4 -> '● 100%' red | -0.1 -> '○ 0%' green

$ uv run python scratchpad/foot047.py  # real _bottom_toolbar + real handler + real settings
  before first turn -> <style fg="green">○ 0%</style> <b>agent:build …</b>  (parses clean)
  window=0          -> ○ 0% green   (NO ZeroDivisionError)
  reserve moved     -> 15% full flips green→yellow   (single source of truth holds)
  ALL FOOTER PROBES PASSED
```

**Other issues found**
- None blocking. Cosmetic note (banker's-rounding artifact, not a defect, no AC bearing): `round()` is half-to-even, so `0.125→○ 12%` (idx 0) and `0.375→◑ 38%` (idx 2) are slightly asymmetric at the eighth marks. Every spec-named bucket point (0/25/50/75/78/100%) is exact and the index never leaves [0,4], so this is purely a sub-glyph display nuance — leave as-is.
- Late-bound `handler` in the `bottom_toolbar` lambda (`app.py:759`) is safe: `handler` is assigned at `app.py:771`, before the prompt loop (`app.py:784`) ever invokes the toolbar — verified no premature call path.

**VERDICT: PASS**
