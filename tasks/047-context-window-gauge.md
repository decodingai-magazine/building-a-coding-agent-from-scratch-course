---
id: 047-context-window-gauge
feature: context-compaction
status: pending
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

- [ ] `context_gauge` glyph buckets: `0.0 → "○"`, `0.25 → "◔"`, `0.5 → "◑"`, `0.75 → "◕"`, `1.0 → "●"`;
      `0.78 → "◕"` (round 3.12→3) — asserted across buckets incl. 0% and 100%.
- [ ] `context_gauge` colors: `green` below `warn_at`, `yellow` in `[warn_at, danger_at)`, `red` at/above
      `danger_at` — asserted at the default fill lines (0.60 / 0.80) including the exact boundaries.
- [ ] `label` formats as `"{glyph} {pct}%"` with `pct` the rounded clamped percentage; `fraction` outside
      `[0,1]` is clamped (e.g. `1.4 → "● 100%"`, red; `-0.1 → "○ 0%"`, green).
- [ ] The footer reads `handler.last_input_tokens` via the **public property** (never a private attr) and
      `settings.compaction_context_window_tokens`; before the first turn it renders `○ 0%` (green).
- [ ] `warn_at`/`danger_at` are derived from `microcompaction_reserve_fraction` /
      `compaction_reserve_fraction`, so the gauge colors track the actual compaction tiers (single source
      of truth) — verified by a unit test computing the fill lines from the settings defaults.
- [ ] `make ci` green, 0 warnings; unit-tested in `tests/unit/decode/tui/test_render.py` (+ footer
      assertion in the existing app test if practical). No new integration test required.

## Out of scope
- Any new compaction behaviour (tasks 042/044) — the gauge only *displays* `last_input_tokens / window`.
- A live/animated redraw beyond prompt_toolkit's normal per-render toolbar refresh.
- An alternate gauge style (bar/ring) — swap-able later in this one helper if the user prefers.

## Log
