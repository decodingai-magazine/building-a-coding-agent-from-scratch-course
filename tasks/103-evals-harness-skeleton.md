---
id: 103
feature: evals
status: pending
---

# Evals harness skeleton: package, settings, in-process agent driver

Depends on: none. Implements ADR-0017 §1,4.

## Scope

Create the top-level `evals/` package (NOT shipped in the wheel — `[tool.hatch.build.targets.wheel]`
already limits packages to `src/decode`, so no build change is needed) and the one in-process agent
driver everything else reuses.

**Layout** (create only what this task fills; later tasks add their own subpackages):

```
evals/
├── __init__.py
├── __main__.py            # `python -m evals` — Click group, subcommands stubbed (benchmark/regression land later)
├── run.py                 # the CLI body __main__ delegates to
└── harness/
    ├── __init__.py
    └── driver.py          # run_agent_once(...) — the ONE way evals drives decode
```

**Driver** (`evals/harness/driver.py`) — the capstone pattern
(`tests/integration/test_milestone1_capstone.py`), made reusable:

- `run_agent_once(prompt, *, cwd, gate_mode=PermissionMode.BYPASS, permission_rules=None,
  resolve_permission=None, resolve_user_question=None, message_history=None, max_requests=None)
  -> EvalRunRecord` — async; builds the REAL `build_agent()`, real `AgentDeps` (headless emit sink,
  `harness_home=cwd` unless overridden, gate in `gate_mode` with optional rules), real
  `AgentTurnHandler` + `Runner`, submits one prompt, drives to idle. Default resolvers are headless
  auto-deny (mirror `runtime/flow.py::_deny_permission_resolver` / `deny_user_question_resolver`);
  probes override them. `message_history` pre-fills the handler (the compaction probe needs it —
  the capstone proves `AgentTurnHandler(agent, deps=deps, message_history=...)` works).
  `max_requests` hard-caps model requests so a runaway run cannot burn budget (graceful stop, not a
  crash; SWE picks the mechanism — pydantic-ai usage limits or a counting seam).
- `EvalRunRecord` (frozen dataclass): `output: str`, `messages: list[ModelMessage]`,
  `tool_calls: list[ToolCallRecord]` (name + args, extracted from `ToolCallPart`s in the message
  history — NEVER parsed from Opik traces), `steps: int` (model-request count),
  `input_tokens/output_tokens: int` (summed from each `ModelResponse.usage` — the message-history
  equivalent of `result.usage()`), `denied_tools: list[str]`.
- `run_agent_once_sync(...)` — `asyncio.run()` wrapper, because Opik `evaluate()` task fns cannot
  be async.

**Settings** (`src/decode/config/settings.py` + `.env.example`, new `--- Evals ---` block):

- `eval_judge_model: str = ""` — LiteLLM model string for G-Eval judges; empty = derive from the
  active `llm_provider` (task 104 implements the derivation).
- `eval_project_name: str = "decode-evals"` — the Opik project eval runs log under, so eval traces
  never mix into the live-REPL tracing project (ADR-0014 coexistence).

**Tests** — `tests/unit/evals/` mirroring `evals/` (decision recorded in ADR-0017 §1: one pytest
root, `pythonpath` already includes `"."` implicitly via repo-root runs — add `"."` to
`[tool.pytest.ini_options].pythonpath` if imports need it). Offline: drive `run_agent_once` with a
scripted `FunctionModel` via `agent.override(...)` (capstone pattern; fake `GEMINI_API_KEY`) and
assert the record's output/tool_calls/steps/tokens; deny-resolver path; `max_requests` cap;
`message_history` pre-fill. Settings fields covered in `test_settings.py` style.

## Acceptance Criteria

- [ ] `run_agent_once` drives the real `build_agent()` + `Runner` and returns a populated
      `EvalRunRecord`; tool calls come from `ToolCallPart`s, tokens from summed response usage.
- [ ] Gate mode, permission rules, custom resolvers, pre-filled history, and `max_requests` all
      work and are unit-tested offline (scripted model, no network, no keys).
- [ ] `python -m evals --help` prints the CLI skeleton without importing opik at module scope
      errors when opik is installed.
- [ ] `EVAL_JUDGE_MODEL` / `EVAL_PROJECT_NAME` land in `Settings` + `.env.example`.
- [ ] `opik` added via `uv add --group dev opik` (dev group, PEP 735 — evals is not shipped).
- [ ] Nothing under `evals/` is collected by plain `pytest` (`testpaths` already excludes it).
- [ ] `make ci` green.

## Out of scope

- Metrics, judges, datasets, sandbox lifecycle (104–106). Any change to `src/decode` beyond the
  two settings fields.

## Log
