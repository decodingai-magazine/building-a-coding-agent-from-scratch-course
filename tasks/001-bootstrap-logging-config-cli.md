---
id: 001-bootstrap-logging-config-cli
feature: m1-vanilla-agent
status: done
---

# Bootstrap: logging, settings, CLI, entities

## Scope
Foundational package wiring: structured logging, the pydantic-settings singleton, the `decode` Click entrypoint, and the shared-entities package marker.

## Acceptance criteria
- [x] `logging.py` exposes `init_logger()`, called at module level in `cli.py` before any other project import.
- [x] `config/settings.py` defines `Settings` + a module-level `settings` singleton; reads `GEMINI_API_KEY`, `gemini_model`, output/memory caps, `sessions_dir` from env/`.env`.
- [x] `decode` CLI launches and exits cleanly (`uv run decode`), accepts `--resume [SESSION]`.
- [x] New env vars mirrored in `.env.example`.
- [x] `make pre-commit` green (ruff format + lint + unit tests).

## Out of scope
- The interactive REPL / TUI (task 002) and any agent behaviour. The CLI is a bootstrap stub.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Foundational; no deps.
### [SWE] 2026-06-19 19:10 — Implemented
Wrote logging/settings/cli/entities + tests under `tests/unit/decode/`; added `--import-mode=importlib`; removed the placeholder smoke test. `make pre-commit` green (10 tests).
### [SWE] 2026-06-19 19:30 — Done
Committed in the scaffold + plan baseline (built inline before the pipeline took over). Status → done; the `/implement-night` run starts at 002.
