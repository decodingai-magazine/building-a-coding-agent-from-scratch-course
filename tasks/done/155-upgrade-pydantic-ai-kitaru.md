---
id: 155-upgrade-pydantic-ai-kitaru
feature: evals-v2
status: done
---

# Upgrade pydantic-ai 2.40, kitaru 0.26, kitaru-pydantic-ai 0.2.1

Tags: `deps`, `kitaru`, `runtime`
Depends on: None
Blocks: 156, 165

## Scope
Lift the `pydantic-ai <2.23` cap (ADR-0019 §2, amended by ADR-0022 §12) now that the adapter allows `<2.41`; move Kitaru to the 0.26 line; keep every existing surface green. First task of the feature PR, no feature work. Spike result: 2426/2434 unit tests pass; one real failure (a private-attribute assertion) and seven `DECODE_ENV=prod` leaks.

## Acceptance criteria
- [x] `pyproject.toml`: `pydantic-ai-slim[google,openai]>=2.40,<2.41`, `kitaru[cli,mcp,worker]>=0.26.0`, `kitaru-pydantic-ai>=0.2.1`; the "capped <2.23" comment replaced by one line pointing at ADR-0022 §12; `uv.lock` regenerated; `uv sync` clean.
- [x] `tests/unit/decode/agent/test_factory.py::test_build_agent_registers_a_single_instructions_hook` rewritten against a public surface (e.g. run the built agent on a `TestModel`/`FunctionModel` and assert the assembled system instructions appear exactly once in the first `ModelRequest`); no `agent._instructions` or other private pydantic-ai attribute anywhere under `tests/`. (Deviation ledger accepted by Tester round 2 — see Log: `_instrument_default` ×4, `_usage` ×2, both with no public equivalent; `_function_toolset.tools` ×0.)
- [x] `tests/unit/scripts/test_modal_kitaru_worker.py` passes with `DECODE_ENV` unset, with `DECODE_ENV=prod` exported in the shell, AND with `DECODE_ENV=prod` in the repo `.env`: the tests pin `settings.decode_env` themselves (monkeypatch on the singleton / a fresh `Settings(_env_file=None)`), never inherit it.
- [x] `make unit-tests` and `make integration-tests` green; `filterwarnings = ["error"]` unchanged — any new warning is fixed at the call site, or ignored with ONE message-scoped filter and a comment naming the upstream issue.
- [x] `importers/opik_importer.py` and `evaluators/decode_bad_request_400.py` import against 0.26 (`kitaru.task.importer`, `kitaru.task.evaluator`) and pass `kitaru importer test` / `kitaru evaluator test` locally; `MAX_PAYLOAD_BYTES` guard stays.
- [x] `runtime/recording.py` + `runtime/task_inputs.py` behave identically against adapter 0.2.1 (`KitaruAgent`, `get_task_inputs`); their unit tests green; a fresh `decode run` under `OPIK_API_KEY` still yields tool spans the importer recognises (`gen_ai.operation.name == "execute_tool"`, `logfire.msg` "running tool: <name>") — checked by the existing `tests/integration/test_opik_headless_trace.py`, extended with that assertion if absent.
- [x] Runbook drift fixed in `running_the_code/06_evals_replays.md` and `07_evals_replays_deploy.md`: `--evaluate-baselines` → `--baseline-evaluation-mode if_missing` (0.25); the `session list` no-payload note (0.24); no other prose changes (166 does the consistency pass).

## Out of scope
- Any new Kitaru feature (ephemeral workers, analyzers, provider-API imports).
- The local server bootstrap (165). Removing the `litellm<1.98` dev pin.

## Log
### [PA] 2026-09-10 — Grooming
Spiked in a throwaway worktree: 2426 passed / 8 failed / 1 skipped. One real failure (private attr); seven were `DECODE_ENV=prod` leaking from the developer's env into the worker-script tests — fix the isolation while here. pydantic-ai 2.23–2.40 release notes show no breaking change on decode's surfaces (`WrapperModel` forwards `model_id`/`base_url`; instrumentation emits tool results under `role: tool` — hence the importer-detection AC). ADR-0019's amendment line and ADR-0017's supersession header are written in the grooming commit, not here.

### [SWE] 2026-09-11 14:20 — Implementation

**Files modified**
- `pyproject.toml` — pins moved to `pydantic-ai-slim[google,openai]>=2.40,<2.41`, `kitaru[cli,mcp,worker]>=0.26.0`, `kitaru-pydantic-ai>=0.2.1`; the `<2.23` cap comment replaced by one line naming ADR-0022 §12.
- `uv.lock` — regenerated: pydantic-ai-slim/pydantic-graph 2.22→2.40, kitaru 0.22.2→0.26.0, kitaru-pydantic-ai 0.1.0→0.2.1, openai 2.54→3.13, genai-prices 0.1.4→0.1.6, litellm 1.97.0→**1.80.17** (a downgrade — the `litellm<1.98` dev pin now resolves lower under the new graph; still a pure-python wheel, so `make ci` needs no rustc), grpcio added. `uv lock --check` and `uv sync` clean; zenml and its 20-odd transitive deps drop out.
- `tests/unit/decode/agent/test_factory.py` — `test_build_agent_registers_a_single_instructions_hook` → `test_a_built_agent_sends_exactly_one_system_message_on_the_wire`, plus a `_recording_openai_model()` helper (an `OpenAIChatModel` over an `httpx.MockTransport`) that captures the request body offline; and 12 `model._provider…` accesses swapped to 2.40's public `model.provider…`.
- `tests/conftest.py` — `os.environ["DECODE_ENV"] = "local"` at import time (root-cause fix for the seven leaks), and one line in `_default_decode_env` saying it is the runtime half only.
- `tests/unit/scripts/test_modal_kitaru_worker.py` — `_configured_env()` derives `DECODE_ENV` from `mkw.DECODE_ENV` instead of hardcoding `"local"`, so the file is honest at any ambient environment.
- `tests/integration/test_opik_headless_trace.py` — `_tool_spans` now selects on the attribute the Opik importer reads (`gen_ai.operation.name == "execute_tool"`), and a new `test_a_tool_span_carries_what_the_opik_importer_reads` pins both that attribute and `logfire.msg == "running tool: read"`.
- `running_the_code/06_evals_replays.md`, `running_the_code/07_evals_replays_deploy.md` — `--evaluate-baselines` → `--baseline-evaluation-mode if-missing` (3 call sites + the prose), and the 0.24 `session list` no-payload note.

**Tests**
- Unit: 2434 passing, 0 failing, 1 skipped (`test_app.py:130`, no `fastapi` — pre-existing). Also green with `DECODE_ENV=prod` exported, and with `DECODE_ENV=prod` in the repo `.env` (the real repro is `pytest tests/unit/evals tests/unit/scripts/test_modal_kitaru_worker.py`: litellm's `load_dotenv()` is what puts `.env` into `os.environ`, so the worker file alone never reproduced it).
- Integration: **113 passing, 0 skipped** with the Docker daemon up (started Docker Desktop for the run); without a daemon the same suite is 97 passed / 16 skipped — the 16 are the `docker`-gated ones (`test_docker_executor`, `test_benchmark_docker`, 3 in `test_sandbox_capstone`, `test_sandbox_teardown`, 1 in `test_workspace_clone`). No test needed a provider key or a Kitaru/Opik key; `test_modal_executor.py` (11) runs against the fake backend, not real Modal.
- `filterwarnings = ["error"]` untouched — the upgrade introduced no new warning, so no filter was added.

**Acceptance criteria**
- [x] AC1 pins + lock + `uv sync` — `uv lock --check` clean.
- [x] AC2 rewrite — done, and stronger than the AC's suggestion (see Notes).
- [ ] AC2's second clause ("no other private pydantic-ai attribute anywhere under `tests/`") — **all but 6 sites cleared; the rest need a PA call.** `dir()` on 2.40 showed `provider` / `client` / `base_url` are now PUBLIC on both `Model` and every `Provider`, so all 12 `model._provider…` accesses in `test_factory.py` became `model.provider…` (the comment block's "verified against openai 2.43 / pydantic-ai 2.22" line updated to 3.13 / 2.40). What remains, verified against `dir()` as having no public equivalent: `Agent._instrument_default` × 5 (`test_cost.py`, `test_observability_capstone.py`, `test_opik_headless_trace.py`, `test_opik_repl_trace.py` — `Agent.instrument_all()` is a public *setter* with no public getter, so save/restore of the process-global has no public form) and `agent._function_toolset.tools` × 1 (`test_registry.py`; the nearest public form, `agent.toolsets[0].tools`, trades a private name for an unguaranteed list position). Both still work under 2.40 and removing them would delete real coverage — PA to accept, or file a follow-up. (`test_judges.py`'s `judge._model._completion_kwargs` is opik/litellm, not pydantic-ai, and `client._api_client` is google-genai's.)
- [x] AC3 worker-script isolation — verified in all three scenarios.
- [x] AC4 `make unit-tests` + `make integration-tests` green.
- [x] AC5 importer/evaluator against 0.26 — `kitaru importer test importers/opik_importer.py --entrypoint parse --payload importers/fixtures/opik-sample.json` → `ok: true, sessions 2, failures 1, items 3` (the fixture's 4th trace is a deliberately broken entry); `kitaru evaluator test evaluators/decode_bad_request_400.py --entrypoint evaluate` → `ok: true`. Neither needs a server. `MAX_PAYLOAD_BYTES` untouched.
- [x] AC6 recording seam + task inputs on adapter 0.2.1 — `KitaruAgent(agent, agent_id=…, session_name=…)` and `kitaru.task.get_task_inputs()` unchanged; their unit tests green; the importer-contract assertion added to the integration test.
- [x] AC7 runbook drift.

**Evidence**
```
$ uv lock
Resolved 146 packages in 1.25s
Updated genai-prices v0.1.4 -> v0.1.6 | Added grpcio v1.83.1
Updated kitaru v0.22.2 -> v0.26.0 | Updated kitaru-pydantic-ai v0.1.0 -> v0.2.1
Updated litellm v1.97.0 -> v1.80.17 | Updated openai v2.54.0 -> v3.13.0
Updated pydantic-ai-slim v2.22.0 -> v2.40.0 | Updated pydantic-graph v2.22.0 -> v2.40.0

$ make unit-tests
2434 passed, 1 skipped in 37.22s
$ DECODE_ENV=prod make unit-tests
2434 passed, 1 skipped in 37.80s
$ uv run pytest tests/unit/evals tests/unit/scripts/test_modal_kitaru_worker.py   # the .env-leak repro
501 passed in 8.45s

$ make integration-tests            # docker daemon up
113 passed in 373.29s (0:06:13)

$ make format-check && make lint-check && make pre-commit
313 files already formatted / All checks passed! / 2434 passed, 1 skipped

# red/green proof for the rewritten test — a second @agent.instructions hook added to factory.py:
$ uv run pytest tests/unit/decode/agent/test_factory.py -k one_system_message
E  AssertionError: [{'role': 'system', 'content': 'SECOND SOURCE'}, {'role': 'system', 'content': 'You are Decode…'}, …]
E  assert 2 == 1                                     # (hook reverted; suite green again)

# end-to-end, real provider, real tool call, tracing on (DECODE_ENV=local so prod's Opik project stays clean):
$ DECODE_ENV=local LLM_PROVIDER=gemini uv run decode run "read note.txt and reply with only the number it contains" --max-requests 5
42
EXIT=0
```

**Notes**
- **AC2's suggested assertion would not have caught the regression.** Under 2.40 `ModelRequest` still exposes only a *joined* `instructions: str` (no public instruction parts), so two separate instruction sources with different content join with `\n\n` and read as one block in the first `ModelRequest` — the anchor appears exactly once either way. The constraint that actually matters is the wire shape (`OpenAIChatModel` emits one `system` message per source; strict vLLM/OpenRouter servers reject the second), so the test drives the built agent against an `OpenAIChatModel` over an `httpx.MockTransport` and counts `role: "system"` entries in the captured payload. Verified red on a planted second hook (evidence above), and it confirmed 2.40 still emits one system message per source.
- **AC3's stated mechanism was not the actual one.** The leak is not `settings.decode_env`: `scripts/modal_kitaru_worker.py:82` runs `DECODE_ENV = deploy_time_decode_env(os.environ)` at **import** time, so by the time any fixture (or a fresh `Settings(_env_file=None)`) could pin anything, the module constant, `SECRET_NAME` and `APP_NAME` are already baked — monkeypatching the singleton cannot fix it. Fixed at the choke point instead: `tests/conftest.py` **pins** `os.environ["DECODE_ENV"] = "local"` at import, before any test module is imported. Pinned, not deleted, because importing litellm (opik pulls it in) runs `load_dotenv()`, which puts a deleted var straight back from the repo `.env` — that is exactly why the seven failures appeared under `.env` alone. `decode.remote.app` and the `settings` singleton bake the same var and are covered by the same line. The per-test `_default_decode_env` fixture still deletes it so spawned subprocesses stay clean.
- `--baseline-evaluation-mode` takes **`if-missing`** (hyphen), not `if_missing` as the task/ADR prose says — taken from `kitaru replay create --help` / `experiment run start --help` on 0.26 (`choices: none, if-missing, force`, default `if-missing`). The runbooks now use the CLI's own spelling.
- Two stale `--evaluate-baselines` mentions live **outside** this task's scope and were left alone for 166: `kitaru_plan.md:62` and `.agents/skills/kitaru-replay-experiment/references/experiment-contract.md:28`.
- The `litellm<1.98` dev pin and its comment were left as-is (out of scope, per the task). Worth a follow-up read: it now resolves to 1.80.17.
- The new wire-shape test pins `settings.llm_provider` to `gemini` as well as the key, matching the convention the rest of the file states at its own line 157 — without it, `build_agent()` would build whatever a developer's `.env` selects before `override` swaps the model out.
- Observation, NOT touched: with the repo's own `.env` (`LLM_PROVIDER=modal`), `decode run` dies on `ModelHTTPError: status_code: 503, model_name: Qwen/Qwen3.6-35B-A3B-FP8` with a full traceback. Checked, not assumed: `cli.py:524` re-raises anything that is not a usage-limit or a recording failure **by design** ("a failure the agent itself raised — a provider 503 inside a replay — must stay exactly what it is"), so this is the intended agent-error surface with a Modal endpoint that is down, not a regression from this upgrade (the same command on `LLM_PROVIDER=gemini` exits 0). Flagging it because it is the sibling of the `ModelHTTPError 400` failure task 164 goes mining for.

### [Tester] 2026-09-11 01:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`313 files already formatted`, `All checks passed!`, pre-commit's embedded unit run 2434 passed / 1 skipped)
- Unit tests: 2434 passed / 1 skipped (`fastapi` missing, pre-existing) — reproduced with `DECODE_ENV` unset AND with `DECODE_ENV=prod` exported
- Integration tests: 113 passed / 0 failed, Docker daemon up (373–403s runtime across two runs)
- Warnings: 0 new (`filterwarnings = ["error"]` single line, unchanged; verified via `grep -n filterwarnings pyproject.toml`)

**E2E adversarial pass**
- Happy path: `DECODE_ENV=local LLM_PROVIDER=gemini uv run --project <repo> decode run "read note.txt and reply with only the number it contains" --max-requests 5` (cwd = scratch dir containing `note.txt` = `42`) → stdout `42`, exit 0 (PASS)
- Break path 1 (state edge: request-ceiling hit mid-tool-use): `decode run "read note.txt, then read it again, then reply with the number" --max-requests 1` → `Decode: the run stopped at its request ceiling (...)`, exit 1, no traceback (PASS — pydantic-ai's `UsageLimitExceeded` still surfaces through `cli.py`'s special case after the 2.22→2.40 jump)
- Break path 2 (boundary: empty prompt): `decode run ""` → `Decode: decode run needs a TASK to run: ...`, exit 1, no traceback (PASS)
- Break path 3 (boundary: non-ASCII/emoji prompt): `decode run "réponds juste avec 你好 emoji 🎉" --max-requests 3` → stdout `你好 🎉`, exit 0 (PASS)
- Regression-guard proof (wire-shape test): planted a second `@agent.instructions` hook in `src/decode/agent/factory.py` (`_register_instructions`), ran `pytest tests/unit/decode/agent/test_factory.py -k one_system_message` → red, `assert 2 == 1` on two `role: "system"` entries; reverted (`git diff src/decode/agent/factory.py` clean afterward), re-ran → green (PASS, matches the SWE's claimed red/green proof exactly)
- AC3 isolation proof: removed the `os.environ["DECODE_ENV"] = "local"` line from `tests/conftest.py` and reverted `tests/unit/scripts/test_modal_kitaru_worker.py`'s `_configured_env()` to hardcode `"DECODE_ENV": "local"` (the pre-fix state), then ran `pytest tests/unit/evals tests/unit/scripts/test_modal_kitaru_worker.py` with `DECODE_ENV` unset (repo `.env` has `DECODE_ENV=prod`) → **7 failed, 494 passed**, exactly the spike's count and the same 7 test names implied by the grooming note; restored both files, re-ran clean → 46 passed (PASS)
- Note (accuracy correction, not a fail): the conftest module-level pin is currently a no-op by itself for this specific 7-failure repro — with the pin removed but `test_modal_kitaru_worker.py`'s fix (`mkw.DECODE_ENV`-derived) left in place, `DECODE_ENV=prod uv run pytest tests/unit -q` still passes 2434/1-skipped, because the test file now self-adapts to whatever baked at import instead of hardcoding "local". The actual fix for the 7 is the test-file change; the conftest pin is defensive cover named for `decode.remote.app` / the settings singleton, which (checked) already derive their own `DECODE_ENV` the same self-adapting way and don't currently need it either. Keeping the pin is reasonable hygiene, just don't read the conftest comment as "this line is what fixes the 7."

**Acceptance criteria**
- [x] PASS — AC1 pins + lock — `pyproject.toml` shows `pydantic-ai-slim[google,openai]>=2.40,<2.41`, `kitaru[cli,mcp,worker]>=0.26.0`, `kitaru-pydantic-ai>=0.2.1`, `<2.23` comment replaced by an ADR-0022 §12 pointer (confirmed §12 in `docs/adr/0022-evals-v2-own-harbor-on-opik.md:179-181` names this exact change); `uv lock --check` → `Resolved 146 packages`, clean, no diff
- [ ] FAIL — AC2 second clause ("no other private pydantic-ai attribute anywhere under `tests/`" / PA-visible deviation list) — see below
- [x] PASS — AC3 worker-script isolation — reproduced both the fix (3 scenarios green: unset / `DECODE_ENV=prod` exported / `DECODE_ENV=prod` in `.env`) and the original 7-failure break via revert (evidence above)
- [x] PASS — AC4 `make unit-tests` (2434 passed/1 skipped) + `make integration-tests` (113 passed) green, reproduced independently
- [x] PASS — AC5 importer/evaluator — `uv run kitaru importer test importers/opik_importer.py --entrypoint parse --payload importers/fixtures/opik-sample.json` → `"ok":true,...,"sessions":2,"failures":1,"items":3`; `uv run kitaru evaluator test evaluators/decode_bad_request_400.py --entrypoint evaluate` → `"ok":true`; `MAX_PAYLOAD_BYTES = 50 * 1024 * 1024` unchanged in `importers/opik_importer.py:56` (no diff to either file)
- [x] PASS — AC6 recording seam + task inputs — `tests/integration/test_opik_headless_trace.py` green (4 passed), new `test_a_tool_span_carries_what_the_opik_importer_reads` selection (`gen_ai.operation.name == "execute_tool"`, `logfire.msg == "running tool: read"`) matches `importers/opik_importer.py:123-124` verbatim
- [x] PASS — AC7 runbook drift — `--baseline-evaluation-mode if-missing` verified against `uv run kitaru replay create --help` / `experiment run start --help` (`choices: none, if-missing, force`, default `if-missing`, hyphenated); `session list --help` shows `--include-payloads` matching the added 0.24 note

**AC2 — FAIL, with the fix**

Grepped `tests/` for every private pydantic-ai attribute access (excluding google-genai's `_api_client` and opik/litellm's `_completion_kwargs`, correctly out of scope):

- `Agent._instrument_default` — **4** read sites (not 5 as reported): `tests/unit/decode/observability/test_cost.py:183`, `tests/integration/test_observability_capstone.py:112`, `tests/integration/test_opik_repl_trace.py:83`, `tests/integration/test_opik_headless_trace.py:57`. Verified no public equivalent: `inspect.getsource(Agent.instrument_all)` is a write-only `@staticmethod` (`Agent._instrument_default = instrument`); `dir(Agent)` has no getter. **Deviation justified, PASS-with-note for this half.**
- `_function_toolset.tools` — **5** sites, not 1, and in **two** files, not one: `tests/unit/decode/tools/test_registry.py:115,147` AND `tests/unit/decode/tools/test_agent.py:163,183,628` (this file was never mentioned in the SWE's report to PA). Verified a public equivalent DOES exist and works today: `build_agent()` returns exactly one entry in `agent.toolsets` (checked: `uv run python -c "... build_agent().toolsets"` → length 1, the `_AgentFunctionToolset`), and `agent.toolsets[0].tools` is the same public-surfaced dict (`{name: Tool}`) — `agent.toolsets[0].tools["agent"].max_retries` and `agent.toolsets[0].tools[AGENT_TOOL_NAME].description` read identically to today's private-attribute assertions. For the two membership-only checks in `test_registry.py`, an even more position-free alternative also works: after a `TestModel` run, `test_model.last_model_request_parameters.function_tools` is a public list of `ToolDefinition`s carrying `.name` (verified: `tm.last_model_request_parameters.function_tools` → `[ToolDefinition(name='foo2', ...)]`).
  Fix: replace `_function_toolset.tools` with `toolsets[0].tools` at all 5 sites (or the `TestModel` name-list technique for the 2 membership checks), and correct the deviation ledger in this task's Log to `_instrument_default` ×4 / no other private site — there is no PA-acceptable deviation left once the fix lands.

**Evidence**

```
$ DECODE_ENV=prod uv run pytest tests/unit -q
2434 passed, 1 skipped in 37.40s

$ uv lock --check
Resolved 146 packages in 3ms

$ uv run kitaru importer test importers/opik_importer.py --entrypoint parse --payload importers/fixtures/opik-sample.json
{"schema_version":"1","command":"importer.test","ok":true,...,"sessions":2,"failures":1,"items":3,...}

$ uv run kitaru evaluator test evaluators/decode_bad_request_400.py --entrypoint evaluate
{"schema_version":"1","command":"evaluator.test","ok":true,...}

$ make integration-tests
113 passed in 403.40s (0:06:43)

# 7-failure repro (conftest pin removed + test file reverted to hardcoded "local"):
$ uv run pytest tests/unit/evals tests/unit/scripts/test_modal_kitaru_worker.py -q
7 failed, 494 passed in 8.20s
FAILED tests/unit/scripts/test_modal_kitaru_worker.py::test_the_failed_harness_home_is_announced_in_exactly_one_line
FAILED tests/unit/scripts/test_modal_kitaru_worker.py::test_the_harness_home_exists_before_the_worker_claims_anything
FAILED tests/unit/scripts/test_modal_kitaru_worker.py::test_creating_an_existing_harness_home_is_not_an_error
FAILED tests/unit/scripts/test_modal_kitaru_worker.py::test_the_worker_runs_in_the_harness_home_with_the_scrubbed_env
FAILED tests/unit/scripts/test_modal_kitaru_worker.py::test_the_function_scrubs_the_agent_id_with_one_logged_line
FAILED tests/unit/scripts/test_modal_kitaru_worker.py::test_the_concurrency_and_scope_reach_the_subprocess
FAILED tests/unit/scripts/test_modal_kitaru_worker.py::test_a_dead_worker_reports_its_exit_code_instead_of_raising

# AC2 count discrepancy:
$ grep -rn "_function_toolset" tests/ --include="*.py"
tests/unit/decode/tools/test_registry.py:115
tests/unit/decode/tools/test_registry.py:147
tests/unit/decode/tools/test_agent.py:163
tests/unit/decode/tools/test_agent.py:183
tests/unit/decode/tools/test_agent.py:628
$ grep -rn "Agent\._instrument_default" tests/ --include="*.py" | grep -v '"""'
tests/unit/decode/observability/test_cost.py:183
tests/integration/test_observability_capstone.py:112
tests/integration/test_opik_repl_trace.py:83
tests/integration/test_opik_headless_trace.py:57

# live happy path + break paths:
$ DECODE_ENV=local LLM_PROVIDER=gemini uv run --project <repo> decode run "read note.txt and reply with only the number it contains" --max-requests 5
42
$ DECODE_ENV=local LLM_PROVIDER=gemini uv run --project <repo> decode run "read note.txt, then read it again, then reply with the number" --max-requests 1
Decode: the run stopped at its request ceiling (...) EXIT=1
$ DECODE_ENV=local LLM_PROVIDER=gemini uv run --project <repo> decode run "" --max-requests 3
Decode: decode run needs a TASK to run: ... EXIT=1
$ DECODE_ENV=local LLM_PROVIDER=gemini uv run --project <repo> decode run "réponds juste avec 你好 emoji 🎉" --max-requests 3
你好 🎉  EXIT=0
```

**Other issues found**
- Not stopped/restarted Docker Desktop to verify the docker-less `make integration-tests` skip path directly (risk of not reliably restarting it in this sandbox outweighed the check) — verified instead by reading the pre-existing, untouched skip mechanism (`tests/integration/test_docker_executor.py:41-52`, `_DOCKER_AVAILABLE` / `pytestmark = pytest.mark.skipif(...)`), which this task's diff does not touch, and by the SWE's own reported 97 passed / 16 skipped without a daemon.
- TUI streaming surface (REPL) was not manually exercised for this upgrade — only headless `decode run` and the mocked/offline test suites. Worth a follow-up manual pass if task 156+ doesn't already cover it.
- `litellm` downgraded 1.97.0 → 1.80.17 as a side effect of the resolver graph (per SWE's report) — out of this task's scope per its own "Out of scope" line, but worth PA's awareness for a security/changelog skim before merge.

**VERDICT: FAIL**

AC2's deviation ledger undercounts and misattributes the remaining private-attribute sites (reports 6, actual 9; omits `test_agent.py` entirely) and asks PA to accept a "no public equivalent" claim for `_function_toolset.tools` that is not true — `agent.toolsets[0].tools` (or `TestModel.last_model_request_parameters.function_tools` for name-only checks) is a working public path today. Every other acceptance criterion verified green with reproduced evidence (uv lock, unit/integration suites at 2434/113, both isolation scenarios reproduced by revert, importer/evaluator offline checks, runbook CLI-flag accuracy, live happy-path + 3 adversarial break paths on a real Gemini call). Fix: correct the 5 `_function_toolset.tools` sites to the public path, correct the ledger to the accurate 4 `_instrument_default` sites, then re-request QA.

### [SWE] 2026-09-11 01:20 — Fixes (Tester round 1)

Both Tester items addressed: all 5 `_function_toolset.tools` sites converted to the public path, ledger corrected.

**AC2's checkbox stays `- [ ]`, and this is the one thing I need back from you.** The general sweep the Tester's verdict implied (below) turned up a private pydantic-ai site nobody had counted yet — `StreamedResponse._usage` ×2 in `tests/integration/test_compaction_capstone.py`, pre-existing, untouched by this task. So the orchestrator's framing ("the only remaining private access is `Agent._instrument_default` ×4") is not accurate, and I am not going to tick a clause that reads "no ... private pydantic-ai attribute anywhere under `tests/`" over two sites it literally covers. `_instrument_default` ×4 is fine because the Tester adjudicated it in round 1 ("Deviation justified, PASS-with-note for this half"); `_usage` ×2 has had no such call, and granting myself one would be round 1's failure with the roles swapped. **Ask: adjudicate `StreamedResponse._usage` ×2 the same way, then AC2 closes.**

**Files modified**
- `tests/support/registered_tools.py` — **new**. `registered_tools(agent)` reads the registered tools through pydantic-ai's public surface: it asserts `len(agent.toolsets) == 1` **and** `isinstance(toolsets[0], FunctionToolset)`, then returns the public `FunctionToolset.tools` dict (`dict[str, Tool]`, declared field at `pydantic_ai/toolsets/function.py:57`). One helper instead of five copies of the position guard, so the guard is impossible to forget at a new call site and a Tester greps one name.
- `tests/unit/decode/tools/test_registry.py` — 2 sites (`:115` → `set(registered_tools(agent))`, `:147` → `set(registered_tools(bare))`).
- `tests/unit/decode/tools/test_agent.py` — 3 sites (`:163` `…["agent"].max_retries`, `:183` membership, `:628` `…[AGENT_TOOL_NAME].description`), all via `registered_tools(built)`.

`grep -rn "_function_toolset" tests/` now returns only the docstring line in the new helper that explains what it replaced.

**Why `toolsets[0].tools` at all 5 sites and not the `TestModel` path at the membership-only ones**

The Tester's primary instruction, and the semantically correct one: these five assertions are about **registration**, `TestModel.last_model_request_parameters.function_tools` is about **post-`prepare=` visibility**. Every decode tool registers with a `prepare=` callback that hides it when it is absent from `ctx.deps.active_agent.tools` (`registry.py:_restrict_to_active_agent`, ADR-0003 §6), so the offered set is a function of the active persona. It happens to coincide today — checked, `build` grants all 15 tool names — but that coincidence is exactly the coupling to avoid: a persona edit would then fail `test_register_tools_registers_every_spec_on_the_agent` for a reason that has nothing to do with the registry, and `"noop" not in registered` would weaken from *never registered* to *hidden for this run*. `test_registry.py:147` is worse still — a bare agent would need constructed `AgentDeps` and a run just to observe a dict it already exposes. The visibility property is separately and properly covered in `test_factory.py` (`test_plan_agent_run_omits_write_edit_and_bash_from_the_visible_tools` and friends), through exactly that `TestModel` path.

**Corrected deviation ledger — supersedes the AC2 bullet in the [SWE] 2026-09-11 14:20 entry**

That bullet said "6 sites, `_instrument_default` ×5 + `_function_toolset.tools` ×1 in one file". It was wrong on all three counts. Accurate, after this round and after a general AST sweep of `tests/` (not a grep for the two names already known — that is what missed `test_agent.py` the first time):

- `agent._function_toolset.tools` — **0 remaining**. Was 5 sites in 2 files; all converted. The public path is equivalent, not merely similar: `agent.toolsets[0].tools is agent._function_toolset.tools` → `True`, `len(agent.toolsets) == 1`, `type(toolsets[0]).__mro__` = `_AgentFunctionToolset → FunctionToolset → AbstractToolset`. No PA decision is needed here; there is no deviation left to accept.
- `Agent._instrument_default` — **4** sites, not 5: 1 unit (`tests/unit/decode/observability/test_cost.py:183`) + 3 integration (`tests/integration/test_observability_capstone.py:112`, `test_opik_repl_trace.py:83`, `test_opik_headless_trace.py:57`). All four save/restore the process-global around a test. Justified: `Agent.instrument_all()` is a write-only `@staticmethod` (`Agent._instrument_default = instrument`) and `dir(Agent)` has no getter — there is no public read path to save.
- `StreamedResponse._usage` — **2** sites, `tests/integration/test_compaction_capstone.py:192,543` (`_ScriptedModel.request_stream`, `_LongTurnModel.request_stream`), both pre-existing and untouched by this task; **the sweep found them, my earlier ledger did not, and neither did round 1 of QA** — disclosed rather than left for the next sweep. Both force a scripted `input_tokens` onto the streamed response so the last-response token source (ADR-0018 §2) sees the turn's tier target instead of `FunctionModel`'s fixed 50-token estimate. Checked for a public path in **both** directions, because "no public equivalent" is exactly the claim that failed round 1: (a) on the object — `StreamedResponse.usage` is a read-only `@property` (`pydantic_ai/models/__init__.py:1210-1213`), no setter, and `_usage` is `field(init=False)` (`:1005`); (b) one level up, at the model — `FunctionModel`'s stream function may yield only `str` / `DeltaToolCalls` / `DeltaThinkingCalls` / `BuiltinToolCallsReturns` (`models/function.py:321-334`) and `FunctionStreamedResponse` computes usage itself from those deltas (`:346-389`), so there is no argument, callback or override that carries an input-token count in. The only public-shaped alternative is hand-rolling a `StreamedResponse` subclass to replace `super().request_stream()` — which is where pydantic-ai's own docstring (`models/__init__.py:1174`) says an implementation "should update the `_usage` attributes as it goes" anyway, i.e. it trades two lines for a fake and lands on the same attribute. **Left in place pending your call**; if you want them converted anyway, say so and I will, and I will owe the 6-minute integration run with it (that file is integration).
- Out of AC2's scope, each category named rather than waved at — every other hit the sweep produced falls in one of three, none of them a pydantic-ai surface: **(a) other libraries** — `client._api_client` (google-genai, reached through the now-public `model.provider.client`, `test_factory.py:402,787`), `judge._model._completion_kwargs` / `metric._model` (opik + litellm, `test_judges.py`, `test_online.py`), `backend._sandbox` / `filesystem._root` / `fs._host` (modal); **(b) decode's own internals**, which tests may legitimately reach — `executor._backend` / `executor._created`, `handler._agent` / `handler._persisted_count` / `handler._last_input_tokens`, `tracing._active`, `lsp_service._CLIENTS`, `bash_module._render`, `compaction._estimate_tokens`, `hl._usage_limits`, `completer._meta`, `wrapper._wrapped`; **(c) test-local fakes** — `tests/support/kitaru_recording.py:103,114,162` (`self._stack._record`, `stack._wrap`) reach into a stub the tests themselves define.

**Tests**
- `uv run pytest tests/unit/decode/tools tests/unit/decode/agent -q` → **587 passed** (the two files the Tester named, plus their neighbours).
- `make unit-tests` → **2434 passed, 1 skipped** (`test_app.py:130`, no `fastapi` — pre-existing).
- Integration: N/A this round — the change is test-only, in two unit files plus a new `tests/support` helper; no `src/` line moved.

**Evidence**
```
$ grep -rn "_function_toolset" tests/
tests/support/registered_tools.py:7:is the public ``{name: Tool}`` dict, the same object the private ``agent._function_toolset.tools``

# the public path IS the same object, and the position is guarded not assumed:
$ uv run python -c "... a = build_agent(); print(len(a.toolsets), a.toolsets[0].tools is a._function_toolset.tools)"
1 True

# the guard fires rather than silently reading the wrong toolset:
$ uv run python -c "a = Agent(TestModel(), toolsets=[extra]); a.tool_plain(...); registered_tools(a)"
toolsets: 2
guard fired: expected exactly one toolset, got 2: [_AgentFunctionToolset(output_sch…

# the general sweep behind the ledger (AST over every tests/**.py: attribute reads whose name
# starts with a single underscore, cross-checked against the names present in pydantic_ai's source):
$ uv run python - <<'PY' ... PY
### _instrument_default
    tests/integration/test_observability_capstone.py:112 / test_opik_repl_trace.py:83 / test_opik_headless_trace.py:57
    tests/unit/decode/observability/test_cost.py:183
### _usage
    tests/integration/test_compaction_capstone.py:192, 543
### _api_client   (google-genai, out of scope)
    tests/unit/decode/agent/test_factory.py:402, 787
$ grep -rn "pydantic_ai" tests/ | grep -E "setattr|mocker.patch|monkeypatch"   # no string-target private patching
tests/unit/decode/observability/test_tracing.py:57:  ...logfire.instrument_pydantic_ai  (public)

# the two string-form escapes an AST walk over ast.Attribute cannot see:
$ grep -rnE 'getattr\([^,]+,\s*["'"'"']_' tests/ --include='*.py'      # nothing at all
$ grep -rnE 'setattr\([^,]+,\s*["'"'"']_' tests/ --include='*.py'      # 44 hits, every target a decode module
tests/unit/decode/tools/test_bash_sandbox_selection.py:164: monkeypatch.setattr(bash_mod, "_EXECUTOR", ...)
tests/unit/decode/runtime/test_headless.py:44:             monkeypatch.setattr(hl, "_build_headless_agent", ...)
tests/integration/test_opik_headless_trace.py:76:          monkeypatch.setattr(tracing, "_active", True)
...  (bash_mod / hl / tracing / session_log / cli_mod / context_window / dlog — no pydantic_ai target)

$ make format-fix && make lint-fix && make format-check && make lint-check
314 files left unchanged / Found 2 errors (2 fixed, 0 remaining) / 314 files already formatted / All checks passed!

$ make pre-commit
format check … 314 files already formatted
lint check   … All checks passed!
unit tests   … 2434 passed, 1 skipped in 37.60s

$ uv run pytest tests/unit/decode/tools tests/unit/decode/agent -q
587 passed in 11.60s
```

**Notes**
- `make lint-fix`'s 2 fixes were isort moving the new `from support.registered_tools import registered_tools` into the third-party block — the convention ruff already enforces on `from support.git_env import …` (`tests/conftest.py:29`). No other line changed.
- Nothing in `src/` was touched this round, so the earlier round's e2e evidence (real Gemini `decode run`, exit 0) still stands unchanged; not re-run.

### [Tester] 2026-09-11 15:05 — QA round 2

**Test summary**
- Format / lint / pre-commit: PASS (`make pre-commit` → `314 files already formatted`, `All checks passed!`, `2434 passed, 1 skipped in 37.08s`)
- Unit tests: `make unit-tests` → 2434 passed / 1 skipped (`test_app.py:130`, no `fastapi`, pre-existing) — reproduced independently
- Targeted subset: `uv run pytest tests/unit/decode/tools tests/unit/decode/agent -q` → 587 passed, matching the SWE's claim
- Integration tests: not re-run this round — `git diff --stat -- src/` is empty (confirmed), change is test-only + one new `tests/support` helper, per the orchestrator's scope note
- Warnings: 0 (pre-commit's embedded unit run shows no new warnings; `filterwarnings = ["error"]` unchanged, not touched this round)

**Adjudication applied (per orchestrator instruction)**
- `Agent._instrument_default` ×4 (`tests/unit/decode/observability/test_cost.py:183`, `tests/integration/test_observability_capstone.py:112`, `tests/integration/test_opik_repl_trace.py:83`, `tests/integration/test_opik_headless_trace.py:57`) — ACCEPTED deviation, carried over from round 1's adjudication ("Deviation justified, PASS-with-note for this half"). Re-verified this round: `Agent.instrument_all` is a write-only `@staticmethod` (`Agent._instrument_default = instrument`), no public getter exists on `Agent`.
- `StreamedResponse._usage` ×2 (`tests/integration/test_compaction_capstone.py:192,543`) — ACCEPTED deviation, new adjudication this round. Verified independently: `StreamedResponse.usage` is a read-only `@property` returning `self._usage` (`pydantic_ai/models/__init__.py`), no setter; `FunctionModel`'s stream function can only yield `str`/`DeltaToolCalls`/`DeltaThinkingCalls`/`BuiltinToolCallsReturns`, giving no public path to inject a scripted `input_tokens` count into a streamed response. Confirmed pre-existing and untouched by this task: `git diff -- tests/integration/test_compaction_capstone.py` is empty; `git log -1 -- tests/integration/test_compaction_capstone.py` → `8b6e02f fix: compaction never applied on single long agentic turns (#50)`, an unrelated prior commit.
- `agent._function_toolset.tools` ×0 remaining. Verified: `grep -rn "_function_toolset" tests/` returns only the explanatory line in the new helper's docstring (`tests/support/registered_tools.py:7`) — no live access anywhere.

AC2's checkbox flipped to `[x]` at the task's top; ledger in the SWE's round-1-fixes log entry (above) is accurate and matches this round's independent re-derivation exactly (`_function_toolset.tools` 0 / `_instrument_default` 4 / `_usage` 2), so no correction to the log was needed beyond checking the box.

**Verification of the fix itself**
- `tests/support/registered_tools.py` reads exactly as reported: `registered_tools(agent)` asserts `len(agent.toolsets) == 1` and `isinstance(toolsets[0], FunctionToolset)`, then returns the public `.tools` dict.
- All 5 conversions confirmed via `git diff`: `test_registry.py:115,147` and `test_agent.py:163,183,628` now call `registered_tools(...)` instead of `._function_toolset.tools`.
- Guard-fires check (manual, per the orchestrator's instruction): built a 2-toolset `Agent` (`TestModel` + an extra `FunctionToolset` alongside a directly-registered `tool_plain`) and called `registered_tools(a)` → `AssertionError: expected exactly one toolset, got 2: [...]`. A single-toolset agent still returns the tool dict correctly (`['foo']`).
- `git status` shows exactly the expected changed/untracked file set (`pyproject.toml`, `running_the_code/06_evals_replays.md`, `running_the_code/07_evals_replays_deploy.md`, `tasks/155-upgrade-pydantic-ai-kitaru.md`, `tests/conftest.py`, `tests/integration/test_opik_headless_trace.py`, `tests/unit/decode/agent/test_factory.py`, `tests/unit/decode/tools/test_agent.py`, `tests/unit/decode/tools/test_registry.py`, `tests/unit/scripts/test_modal_kitaru_worker.py`, `uv.lock`, plus new `tests/support/registered_tools.py`) — no stray files.

**Acceptance criteria (this round's re-check; AC1/AC3-AC7 unchanged from round 1's PASS, not re-litigated)**
- [x] PASS — AC1 pins + lock — unchanged since round 1, spot-checked: `pyproject.toml:33,40-41` shows the 2.40/0.26/0.2.1 pins and the ADR-0022 §12 comment.
- [x] PASS — AC2 — all 5 `_function_toolset.tools` sites converted to the public `registered_tools()` helper; guard verified to fire on a 2-toolset agent; ledger (`_instrument_default` ×4, `_usage` ×2, `_function_toolset.tools` ×0) independently re-derived and matches the SWE's; both remaining deviations adjudicated ACCEPTED (see above).
- [x] PASS — AC3 through AC7 — unchanged since round 1's PASS verdict (test-only diff this round did not touch `test_modal_kitaru_worker.py`'s isolation fix, `uv.lock`, the importer/evaluator files, `runtime/recording.py`/`task_inputs.py`, or the runbooks).
- [x] PASS — `make unit-tests` green (2434/1 skipped); `make integration-tests` not re-run this round (no `src/` change, per scope), round 1's 113-passed result stands.

**Evidence**
```
$ make unit-tests
2434 passed, 1 skipped in 37.00s

$ uv run pytest tests/unit/decode/tools tests/unit/decode/agent -q
587 passed in 11.57s

$ make pre-commit
314 files already formatted / All checks passed! / 2434 passed, 1 skipped in 37.08s

$ git diff --stat -- src/
(empty)

$ grep -rn "_function_toolset" tests/
tests/support/registered_tools.py:7:is the public ``{name: Tool}`` dict, the same object the private ``agent._function_toolset.tools``

$ grep -rn "_instrument_default" tests/
tests/unit/decode/observability/test_cost.py:183
tests/integration/test_observability_capstone.py:112
tests/integration/test_opik_headless_trace.py:57
tests/integration/test_opik_repl_trace.py:83

$ grep -rn "\._usage\b" tests/
tests/integration/test_compaction_capstone.py:192
tests/integration/test_compaction_capstone.py:543

$ git log -1 --format=%H -- tests/integration/test_compaction_capstone.py
8b6e02f  (fix: compaction never applied on single long agentic turns (#50) — pre-existing, untouched)

# guard-fires manual check:
toolsets: 2
guard fired: expected exactly one toolset, got 2: [...]
single toolset result: ['foo']
```

**Other issues found**
- None new this round. Round 1's non-blocking notes (TUI streaming surface not manually re-exercised, litellm downgrade worth a changelog skim) still stand and are unaffected by this round's test-only diff.

**VERDICT: PASS**

Both outstanding items from round 1 (5 `_function_toolset.tools` conversions, deviation-ledger accuracy) are fixed and independently verified; the orchestrator's adjudication of the two remaining private-attribute categories (`_instrument_default` ×4, `_usage` ×2) is applied and re-confirmed on the actual pydantic-ai source this round, not just accepted on the SWE's say-so. AC2 closes as `[x]`. Full unit suite green (2434/1 skipped), pre-commit clean, `git status` shows only the expected files, no `src/` change so the integration suite is unaffected and stands at round 1's 113-passed result.

### [PA] 2026-09-11 21:30 — Acceptance Review

**VERDICT: ACCEPT** — feature-level review of evals-v2 (tasks 155–167, PR #68); evidence and the
per-AC-group walk-through are in `tasks/done/167-regression-step-budgets-recalibration.md`'s log.
Hand off to the PR Reviewer.
