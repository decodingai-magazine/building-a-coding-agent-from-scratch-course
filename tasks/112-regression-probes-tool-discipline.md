---
id: 112
feature: evals
status: pending
---

# Author regression probes 01–07 (tool discipline)

Depends on: 111. Implements ADR-0017 §2,6.

## Scope

1. `01-read-vs-cat` — fixture `notes.txt`; prompt "show me the contents of notes.txt". C:
   `ToolCalledMetric("read")` + `ToolNotCalledMetric("bash")`.
2. `02-grep-vs-bash` — small src tree; "find where `parse_config` is defined". C: grep tool
   called, bash not.
3. `03-edit-precision` — `config.py` with `PORT = 8000`; "change the port to 9000". C: edit tool
   called + post-run diff of the file is exactly one changed line.
4. `04-diff-minimality` — small refactor ask on a seeded module. C: `DiffLinesMetric` ≤ threshold
   + J: G-Eval minimal-diff judge.
5. `05-web-fetch-discipline` — local stdlib http fixture serving a known page; prompt cites the
   URL. C: `web_fetch` called; J: answer grounded in the served content.
6. `06-lsp-diagnostics` — fixture file with a seeded type error; "check broken.py for type
   errors". C: `lsp` tool called + the seeded error named in the output.
7. `07-plan-mode-discipline` — "plan how to add feature X — do not change anything yet". C:
   `enter_plan_mode` called, zero successful write/edit calls.

Each probe: honest `max_requests`, tags, metric bindings; runs green against the current agent
under a real model (spot-run) and offline against a scripted model in unit tests where the
assertion is mechanical.

## Acceptance Criteria

- [ ] Seven probes registered, loadable, and unit-smoke-tested (fixture builds + metric binding).
- [ ] `python -m evals regression --probe 01-read-vs-cat` produces a scored Opik experiment
      (spot-run with a real key; result logged).
- [ ] Web probe never touches the real network (local http fixture).
- [ ] `make ci` green.

## Out of scope

- Probes 08–20 (113, 114). Threshold values (115 owns the gate).

## Log
