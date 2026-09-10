---
id: 155-upgrade-pydantic-ai-kitaru
feature: evals-v2
status: pending
---

# Upgrade pydantic-ai 2.40, kitaru 0.26, kitaru-pydantic-ai 0.2.1

Tags: `deps`, `kitaru`, `runtime`
Depends on: None
Blocks: 156, 165

## Scope
Lift the `pydantic-ai <2.23` cap (ADR-0019 §2, amended by ADR-0022 §12) now that the adapter allows `<2.41`; move Kitaru to the 0.26 line; keep every existing surface green. First task of the feature PR, no feature work. Spike result: 2426/2434 unit tests pass; one real failure (a private-attribute assertion) and seven `DECODE_ENV=prod` leaks.

## Acceptance criteria
- [ ] `pyproject.toml`: `pydantic-ai-slim[google,openai]>=2.40,<2.41`, `kitaru[cli,mcp,worker]>=0.26.0`, `kitaru-pydantic-ai>=0.2.1`; the "capped <2.23" comment replaced by one line pointing at ADR-0022 §12; `uv.lock` regenerated; `uv sync` clean.
- [ ] `tests/unit/decode/agent/test_factory.py::test_build_agent_registers_a_single_instructions_hook` rewritten against a public surface (e.g. run the built agent on a `TestModel`/`FunctionModel` and assert the assembled system instructions appear exactly once in the first `ModelRequest`); no `agent._instructions` or other private pydantic-ai attribute anywhere under `tests/`.
- [ ] `tests/unit/scripts/test_modal_kitaru_worker.py` passes with `DECODE_ENV` unset, with `DECODE_ENV=prod` exported in the shell, AND with `DECODE_ENV=prod` in the repo `.env`: the tests pin `settings.decode_env` themselves (monkeypatch on the singleton / a fresh `Settings(_env_file=None)`), never inherit it.
- [ ] `make unit-tests` and `make integration-tests` green; `filterwarnings = ["error"]` unchanged — any new warning is fixed at the call site, or ignored with ONE message-scoped filter and a comment naming the upstream issue.
- [ ] `importers/opik_importer.py` and `evaluators/decode_bad_request_400.py` import against 0.26 (`kitaru.task.importer`, `kitaru.task.evaluator`) and pass `kitaru importer test` / `kitaru evaluator test` locally; `MAX_PAYLOAD_BYTES` guard stays.
- [ ] `runtime/recording.py` + `runtime/task_inputs.py` behave identically against adapter 0.2.1 (`KitaruAgent`, `get_task_inputs`); their unit tests green; a fresh `decode run` under `OPIK_API_KEY` still yields tool spans the importer recognises (`gen_ai.operation.name == "execute_tool"`, `logfire.msg` "running tool: <name>") — checked by the existing `tests/integration/test_opik_headless_trace.py`, extended with that assertion if absent.
- [ ] Runbook drift fixed in `running_the_code/06_evals_replays.md` and `07_evals_replays_deploy.md`: `--evaluate-baselines` → `--baseline-evaluation-mode if_missing` (0.25); the `session list` no-payload note (0.24); no other prose changes (166 does the consistency pass).

## Out of scope
- Any new Kitaru feature (ephemeral workers, analyzers, provider-API imports).
- The local server bootstrap (165). Removing the `litellm<1.98` dev pin.

## Log
### [PA] 2026-09-10 — Grooming
Spiked in a throwaway worktree: 2426 passed / 8 failed / 1 skipped. One real failure (private attr); seven were `DECODE_ENV=prod` leaking from the developer's env into the worker-script tests — fix the isolation while here. pydantic-ai 2.23–2.40 release notes show no breaking change on decode's surfaces (`WrapperModel` forwards `model_id`/`base_url`; instrumentation emits tool results under `role: tool` — hence the importer-detection AC). ADR-0019's amendment line and ADR-0017's supersession header are written in the grooming commit, not here.
