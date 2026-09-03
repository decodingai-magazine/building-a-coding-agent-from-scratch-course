---
id: 154-decode-remote-cli
feature: remote-headless
status: done
---

# `decode remote` — the Modal launcher under the decode CLI

## Scope
Move the Modal Headless App launcher from `scripts/modal_headless.py` (`modal run …::main` /
`::attempts`) under the `decode` CLI as `decode remote deploy|run|attempts|logs`, with the app
itself in `decode/remote/`. Drop the ephemeral-app path: every trigger targets the deployment.

## Acceptance criteria
- [x] `decode remote run "<task>" [--repo --sandbox-mode --model --timeout-seconds --max-requests]` runs one task on the deployed app, streams the answer, exits with the run's code.
- [x] `decode remote attempts "<task>" --repo <url> --attempts N [--detach]` = the former `::attempts`, same table / notes / diff tail / exit semantics.
- [x] `decode remote deploy` = `modal deploy -m decode.remote.app`, refused with one line outside a checkout; `DECODE_NIGHTLY_*` still read at deploy.
- [x] `decode remote logs` = `modal app logs decode-headless`.
- [x] Client-side guards unchanged (docker mode, fan-out without repo, zero attempts): one friendly line, no container. Missing deployment / missing Modal creds: one friendly line.
- [x] `import decode.cli` imports no `modal` (unit test pins it).
- [x] Worker script + agent registration import the image builder from `decode.remote.image`; both apps still share the layout constants.
- [x] Docs: 07_infra.md rewritten for the new verbs; ADR-0020 Amendment §10; glossary + AGENTS.md updated.
- [ ] [HUMAN] `uv run decode remote deploy` then `uv run decode remote run "print uname -a" --sandbox-mode none` reports a gVisor kernel.

## Out of scope
- Moving the Kitaru Worker under `decode remote worker` (no second caller).
- An `--ephemeral` flag re-creating `modal run`'s no-deploy path.

## Log
### [SWE] 2026-09-03 — Implemented
`src/decode/remote/{__init__,headless,image,app,cli}.py`; `scripts/modal_headless.py` +
`scripts/modal_image.py` removed; tests moved to `tests/unit/decode/remote/`; 351 affected unit
tests green. Human deploy gate left open.
