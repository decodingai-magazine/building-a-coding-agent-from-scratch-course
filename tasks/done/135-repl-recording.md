---
id: 135
feature: kitaru-replay-runtime
status: done
---

# REPL recording: wrap the TUI agent, session_name = decode session id

Tags: `tui`, `enhancement`
Depends on: 134
Blocks: —

This task implements ADR-0019 (§ Recording Seam). Same seam, second caller — the interactive
REPL records too, so real usage feeds the replay corpus.

## Scope

- `src/decode/tui/app.py` (`run_app`, around the `agent = build_agent()` site): pass the built
  agent through the 134 Recording Seam with `session_name` carrying decode's session id
  (`session_log.session_id` — the same id that names the JSONL log and the Hand-back Session
  Branch), so a multi-turn REPL conversation is groupable in Kitaru. Note the ordering
  constraint: the session log exists before/near the agent build — SWE re-sequences minimally
  if needed.
- Multi-turn: the adapter opens one Kitaru session per `run()`/`iter()` call and preserves
  multi-turn context via message-history projection — decode changes nothing in
  `agent/loop.py` beyond whatever the wrapped agent's `iter()` already satisfies. If the
  wrapped agent's `iter()` is NOT drop-in compatible with `AgentTurnHandler`, stop and
  escalate (architectural fork) rather than forking the loop.
- **Graceful degrade in the REPL:** unreachable server → ONE line through the existing TUI
  event/emit surface (like the Opik startup line), then the whole session runs unwrapped.
  Never a second warning per turn, never a broken REPL.
- Unit test at the TUI seam (faked adapter): wrap-with-session-name, degrade-one-line,
  bare-when-unconfigured (REPL byte-identical to today).

## Acceptance Criteria

- [x] With recording unconfigured, the REPL path is byte-identical to today (no wrap, no import, no new output).
- [x] With recording configured, the TUI agent is wrapped with `session_name` equal to the session log's `session_id`.
- [x] With the server down, the REPL starts, prints exactly ONE degrade line, and every turn works on the bare agent.
- [ ] [HUMAN] Live proof (feature gate "(c)"): a REPL turn against the managed workspace shows up under `kitaru session list --agent decode --origin recorded`, with the decode session id visible in the session name; killing the server and starting a REPL degrades with one line.

## Out of scope

- Steering/follow-up/compaction behavior changes — the harness loop contract is untouched.
- Worker/replay concerns (REPL runs are never worker-spawned).

## Log

### [SWE] 2026-08-22 10:20 — Implementation

**Files modified**
- `src/decode/tui/app.py` — route the built agent through the Recording Seam with
  `session_name=session_log.session_id`; surface a degrade notice as ONE line on the TUI's own
  event/emit surface, beside the Opik startup line.
- `src/decode/agent/loop.py` — widen `AgentTurnHandler(agent=...)` to `AbstractAgent` (typing only;
  the handler calls `iter()` and nothing else, and the REPL may now hand it a `WrapperAgent`).
- `src/decode/runtime/__init__.py` — docstring: the REPL now imports the seam (and, via the package
  `__init__`, the headless module) but still no kitaru.
- `tests/support/kitaru_recording.py` — `FakeKitaruAgent` gains a delegating `iter()` + a `model`
  proxy, mirroring the real `WrapperAgent` surface the REPL drives.
- `tests/unit/decode/tui/test_app_recording.py` — 9 tests through the real `run_app` on piped
  prompt_toolkit input with the kitaru stack faked at `sys.modules`.

**Tests**
- Unit: 2205 passing, 0 failing (`make unit-tests`; 2196 before → 9 new, no regressions).
- Integration: 96 passing, 16 skipped (docker daemon unreachable on this host) — unchanged.
- Lint/format: `format-fix → lint-fix → format-check → lint-check → pre-commit` all clean.

**Acceptance criteria**
- [x] Unconfigured REPL byte-identical — `test_app_recording.py::test_an_unconfigured_repl_drives_the_bare_built_agent`
  + `::test_an_unconfigured_repl_touches_no_kitaru_module_and_prints_nothing_new`; the fresh-interpreter
  kitaru-free import invariant still holds via `tests/unit/decode/test_cli.py::test_importing_the_cli_does_not_import_kitaru`.
- [x] Configured → wrapped with `session_name == session_log.session_id` —
  `::test_a_configured_repl_wraps_the_agent_with_the_session_log_id_as_session_name`,
  `::test_a_configured_repl_drives_its_turns_through_the_kitaru_wrapper` (the turn runs through the
  wrapper's `iter()`, the REPL surface), `::test_a_multi_turn_conversation_records_under_one_session_name`
  (one wrap per session, both turns under one name).
- [x] Server down → REPL starts, ONE line, every turn works —
  `::test_an_unreachable_workspace_costs_exactly_one_line_in_the_repl`,
  `::test_the_repl_still_answers_every_turn_after_a_degrade`,
  `::test_the_degrade_line_rides_the_tui_event_surface_not_stderr`.
- [ ] [HUMAN] Live proof against the managed workspace (`kitaru session list --agent decode --origin
  recorded`) — operator gate; NOT RUN (needs the real workspace + credentials).

**Evidence**
```
$ make unit-tests
============================ 2205 passed in 37.38s =============================

$ printf '/quit\n' | uv run decode            # recording unconfigured — unchanged output
Decode - Opik tracing on (project 'decode-local').
Decode - modal:Qwen/Qwen3.6-35B-A3B-FP8 - type a line; /quit exits.
Decode - bye.

$ KITARU_AGENT_ID=6f1d…0f0f KITARU_API_URL=http://127.0.0.1:9 \
    sh -c "printf '/quit\n' | uv run decode"  # workspace down — ONE line, REPL fine
Decode - Opik tracing on (project 'decode-local').
Decode - [kitaru] not recording this run: http://127.0.0.1:9 is unavailable
(ConnectError: All connection attempts failed); continuing on the bare agent
Decode - modal:Qwen/Qwen3.6-35B-A3B-FP8 - type a line; /quit exits.
Decode - bye.

$ LLM_PROVIDER=gemini KITARU_AGENT_ID=6f1d…0f0f KITARU_API_URL=http://127.0.0.1:9 \
    sh -c "printf 'reply with exactly: PONG\n/quit\n' | uv run decode"   # a real turn after the degrade
you "reply with exactly: PONG"
Decode PONG
[done]
Decode - bye.
```

**Notes**
- No architectural fork: `KitaruAgent.iter()` is an `@asynccontextmanager` that forwards
  `*args/**kwargs` to `WrapperAgent.iter` (adding its capability), so it is drop-in for
  `AgentTurnHandler._run_turn`'s `agent.iter(prompt, deps=…, message_history=…,
  deferred_tool_results=…)`. `agent/loop.py` is otherwise untouched — the widened annotation is the
  only edit and adds no behavior.
- `compaction_model=agent.model` now reads through the wrapper; `WrapperAgent.model` proxies to the
  wrapped agent, so the summarizer still rides the Provider Seam (the fake mirrors this).
- Ordering: nothing was re-sequenced. The wrap sits between the existing `SessionLog.create(...)`
  and the `AgentTurnHandler(...)` construction; the notice is emitted later, next to the Opik line,
  so all startup lines cluster at the banner.
- Cost when configured: ONE extra workspace probe at REPL startup (the seam's, not new here) before
  the sandbox block's progress lines. Unconfigured (the default) it is a single `settings` read.
- The live-proof AC stays unchecked for the operator gate.

### [Tester] 2026-08-22 00:39 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 303 files, `ruff check` clean, `make pre-commit` green including full unit run)
- Unit tests: 2205 passed / 0 failed (0 warnings, `-W error` re-run of the new file clean too)
- Integration tests: 96 passed / 16 skipped (docker daemon unreachable on this host — same as SWE's report)
- Warnings: 0

**E2E adversarial pass**
- Happy path: `printf '/quit\n' | uv run decode` (unconfigured) → `Decode - Opik tracing on...` / `Decode - modal:...` / `Decode - bye.`, stderr only the pre-existing prompt_toolkit "Input is not a terminal" warning (PASS — byte-identical to the SWE's pasted baseline)
- Break path 1 (state edge: server down, degrade): `KITARU_AGENT_ID=6f1d… KITARU_API_URL=http://127.0.0.1:9 sh -c "printf '/quit\n' | uv run decode"` → exactly one `[kitaru] not recording this run: ... continuing on the bare agent` line, exit 0, REPL fine (PASS)
- Break path 2 (malformed input: garbage agent id): `KITARU_AGENT_ID=not-a-uuid KITARU_API_URL=http://127.0.0.1:9 ...` → same one-line degrade (`ValueError: badly formed hexadecimal UUID string`), no traceback, no crash, exit 0 (PASS)
- Break path 3 (boundary: half-configured — URL set, agent id empty): `KITARU_API_URL=http://127.0.0.1:9 sh -c "printf '/quit\n' | uv run decode"` → byte-identical unconfigured output, no probe attempted, no kitaru text (PASS)
- Break path 4 (real turn after degrade, independently re-run not just trusted from SWE's paste): `LLM_PROVIDER=gemini KITARU_AGENT_ID=... KITARU_API_URL=http://127.0.0.1:9 sh -c "printf 'reply with exactly: PONG\n/quit\n' | uv run decode"` → degrade line, then `Decode PONG`, `[done]`, `Decode - bye.` (PASS)
- stderr capture on every piped run above: only the unrelated prompt_toolkit non-tty warning — no seam output ever landed on stderr (PASS)

**Acceptance criteria**
- [x] PASS — Unconfigured REPL byte-identical to today — `test_an_unconfigured_repl_drives_the_bare_built_agent` + `test_an_unconfigured_repl_touches_no_kitaru_module_and_prints_nothing_new` (both pass); independently re-ran the piped REPL myself (see happy path above) and it matches; `test_importing_the_cli_does_not_import_kitaru` (tightened to also import `decode.runtime.recording`) proves no kitaru module loads unconfigured.
- [x] PASS — Configured → wrapped with `session_name == session_log.session_id` — `test_a_configured_repl_wraps_the_agent_with_the_session_log_id_as_session_name` asserts `stack.wrapped[0].session_name == session_log.session_id`; code inspection confirms `src/decode/tui/app.py:865` passes `session_name=session_log.session_id` to `wrap_for_recording`, and wrap happens once (between `SessionLog.create` and `AgentTurnHandler` construction), not per turn.
- [x] PASS — Multi-turn stays under one session_name — `test_a_multi_turn_conversation_records_under_one_session_name`: `len(stack.wrapped) == 1` and both turns (`"first"`, `"second"`) recorded via `stack.wrapped[0].iters`, proving one wrap for the whole session.
- [x] PASS — Server down → REPL starts, ONE degrade line, every turn works — `test_an_unreachable_workspace_costs_exactly_one_line_in_the_repl`, `test_the_repl_still_answers_every_turn_after_a_degrade`, `test_the_degrade_line_rides_the_tui_event_surface_not_stderr` all pass; independently reproduced above (break paths 1, 2, 4) with a real Gemini turn completing after the degrade.
- [x] PASS — `iter()` drop-in compatible, no loop fork — verified against the installed package directly: `KitaruAgent(WrapperAgent)` and `WrapperAgent.model` both confirmed by reading the installed source (`inspect.getsource`) — `KitaruAgent.iter` is `@asynccontextmanager` forwarding `*args/**kwargs` to `super().iter()`, and `WrapperAgent.model` returns `self.wrapped.model`, matching `AgentTurnHandler._run_turn`'s call shape (`agent/loop.py:404`) and `compaction_model=agent.model` (`tui/app.py:880`) reading through the wrapper unchanged.
- [x] PASS — Steering/mid-turn/loop untouched — `agent/loop.py` diff is a type-annotation widening only (`Agent` → `AbstractAgent`), no behavior change; spot-ran `tests/unit/decode/tui/test_app.py`, `test_app_e2e.py`, `tests/unit/decode/agent/test_loop.py` — 222 passed, 0 failed.
- [ ] [HUMAN] Live proof against the managed workspace — awaiting human verification (operator gate, needs real workspace + credentials).

**Evidence**
```
$ printf '/quit\n' | uv run decode
Decode - Opik tracing on (project 'decode-local').
Decode - modal:Qwen/Qwen3.6-35B-A3B-FP8 - type a line; /quit exits.
Decode - bye.
(stderr: only "Warning: Input is not a terminal (fd=0).")

$ KITARU_AGENT_ID=not-a-uuid KITARU_API_URL=http://127.0.0.1:9 sh -c "printf '/quit\n' | uv run decode"
Decode - Opik tracing on (project 'decode-local').
Decode - [kitaru] not recording this run: http://127.0.0.1:9 is unavailable
(ValueError: badly formed hexadecimal UUID string); continuing on the bare agent
Decode - modal:Qwen/Qwen3.6-35B-A3B-FP8 - type a line; /quit exits.
Decode - bye.

$ uv run pytest tests/unit/decode/tui/test_app_recording.py -v -W error
9 passed in 1.38s

$ make unit-tests
============================ 2205 passed in 37.96s =============================

$ make integration-tests
================== 96 passed, 16 skipped in 308.74s (0:05:08) ==================
```

**Other issues found**
- None blocking. `git diff --stat` is scoped to exactly the 4 files the SWE's log claims (`agent/loop.py`, `runtime/__init__.py`, `tui/app.py`, `tests/support/kitaru_recording.py`) plus the new test file and the task file itself — no stray changes.

**VERDICT: PASS**
