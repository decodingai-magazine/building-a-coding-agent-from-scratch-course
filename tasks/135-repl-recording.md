---
id: 135
feature: kitaru-replay-runtime
status: pending
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

- [ ] With recording unconfigured, the REPL path is byte-identical to today (no wrap, no import, no new output).
- [ ] With recording configured, the TUI agent is wrapped with `session_name` equal to the session log's `session_id`.
- [ ] With the server down, the REPL starts, prints exactly ONE degrade line, and every turn works on the bare agent.
- [ ] [HUMAN] Live proof (feature gate "(c)"): a REPL turn against the managed workspace shows up under `kitaru session list --agent decode --origin recorded`, with the decode session id visible in the session name; killing the server and starting a REPL degrades with one line.

## Out of scope

- Steering/follow-up/compaction behavior changes — the harness loop contract is untouched.
- Worker/replay concerns (REPL runs are never worker-spawned).

## Log
