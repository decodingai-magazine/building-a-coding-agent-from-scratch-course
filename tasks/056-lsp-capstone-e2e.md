---
id: 056-lsp-capstone-e2e
feature: lsp-integration
status: pending
---

# LSP capstone: active tool + passive enricher through the real stack (e2e)

Tags: `lsp`, `test`
Depends on: #052, #053
Blocks: —

This task implements ADR-0007's end-to-end proof, in the style of
`tests/integration/test_milestone1_capstone.py` and `test_compaction_capstone.py`: drive the **real**
`build_agent()` + `AgentTurnHandler` + `Runner` + `render_event` + `SessionLog` through a scripted
conversation that exercises BOTH LSP channels, swapping only the **LSP subprocess boundary** (the
task-051 service seam) for a fake — no real `ty`, no network. Plus an optional, guarded test that
exercises a REAL `ty server` when available.

## Scope

- Add `tests/integration/test_lsp_capstone.py` that, like the milestone-1 capstone, builds the real
  stack with a scripted `FunctionModel` and swaps **only** the LSP client seam (task 051's patchable
  spawn/service seam) for a **fake** returning canned `definition` + `diagnostics` responses (mirror
  how the M1 capstone swaps the model with `FunctionModel` and `web_fetch`'s transport with
  `MockTransport`). Redirect the session log + `.decode/` under `tmp_path`. No `GEMINI_API_KEY`, no
  network, no real subprocess.
- **Scripted conversation asserts both channels:**
  1. **Active:** the model calls `lsp` with `op=definition` → the tool **auto-allows** (READ_ONLY, no
     permission prompt) and the canned location comes back as a `path:line:column` tool result the
     model sees.
  2. **Passive (errors):** the model `write`s a `.py` file the fake server reports an error for → the
     `write` tool result is the **exact** `Wrote '…' (...).` base string **plus** the appended
     `LSP diagnostics (ty) — fix these:` block (errors-only).
  3. **Passive (clean):** the model `write`s/`edit`s a clean `.py` file (fake reports no errors) → the
     tool result is the base string **unchanged** (silent).
  4. **Non-`.py`:** a `write` to a non-`.py` file → base string unchanged (enricher never runs).
  5. **Unavailable:** with the fake seam reporting "unavailable", a buggy `.py` write still returns
     just the base string and an `lsp` tool call returns a model-readable unavailable `ModelRetry` —
     the turn never crashes.
- Assert the **real renderer** ran on every emitted event without raising (whole render path proven),
  and the session log writes/replays as in the other capstones.
- **Optional real-`ty` test:** a separate test guarded by `@pytest.mark.skipif` (skip when the `ty`
  binary is not importable/available on PATH) that spawns a **real** `ty server` against a tiny
  on-disk `.py` fixture and asserts a real `definition` and a real error diagnostic come back. It runs
  in CI **only if** `ty` is present (the dev group installs it via task 050, so `make ci` exercises
  it; a `ty`-less environment skips, never fails).

## Acceptance criteria

- [ ] Runs under `make integration-tests` / `make ci` with **no** `GEMINI_API_KEY` and **no** network;
      the hermetic capstone swaps only the LSP service seam for a fake (model is a `FunctionModel`).
- [ ] Active channel asserted: `lsp op=definition` auto-allows (no permission request recorded) and
      returns the canned `path:line:column` to the model.
- [ ] Passive-errors asserted: a buggy `.py` write result == exact base `Wrote …` string + the
      appended errors-only diagnostics block.
- [ ] Passive-clean asserted: a clean `.py` write/edit result == the base string unchanged.
- [ ] Non-`.py` asserted: enricher never runs; base string unchanged.
- [ ] Unavailable asserted: buggy `.py` write returns base only; `lsp` tool returns a `ModelRetry`;
      no crash; the turn completes.
- [ ] The real renderer runs on every event without raising; the JSONL session log writes and replays.
- [ ] The optional real-`ty` test runs when `ty` is available and is **skipped** (not failed) when it
      is not; when it runs it asserts a real definition + a real error diagnostic.
- [ ] `make ci` green, 0 warnings.

## User stories

### Story: The capstone proves both channels end-to-end
1. A maintainer runs `make integration-tests`.
2. The LSP capstone drives a scripted conversation through the real agent stack with a fake server,
   proving go-to-definition (active) and post-edit error diagnostics (passive) both reach the model,
   and that clean/non-`.py`/unavailable cases behave.
3. All assertions pass with no API key and no network.

### Story: CI with `ty` installed proves the real wire
1. CI (dev group installed, `ty` present) runs the guarded real-`ty` test.
2. A real `ty server` returns a real definition and a real diagnostic for a fixture `.py` file.
3. The test passes, proving the hand-rolled JSON-RPC framing/handshake work against the actual server.

### Story: A `ty`-less environment still passes
1. A contributor without `ty` runs `make ci`.
2. The hermetic capstone passes (fake seam); the real-`ty` test is skipped.
3. The suite is green — the feature degrades gracefully in test too.

## Out of scope
- Re-testing the units already covered by 051/052/053 (this is the integrated proof).
- A live Gemini run.

## Log
### [PA] 2026-06-27 — Grooming

**Summary**
The integrated proof: both LSP channels (active `lsp` tool + passive enricher) through the real
build_agent/handler/runner/render/log stack with a faked LSP service boundary, plus an optional
guarded real-`ty` test.

**Key decisions**
- Swap only the LSP service seam (mirrors the M1 capstone swapping the model + web transport) —
  hermetic, no real subprocess by default.
- A `skipif(ty unavailable)` real-`ty` test proves the wire when the dev binary is present; skips
  (never fails) otherwise.

**Dependencies**
- #052 (the tool), #053 (the enricher) — the surfaces under test.

**User stories**
- 3 stories: hermetic both-channels proof, real-`ty` wire proof, graceful skip without `ty`.

Ready for implementation.
