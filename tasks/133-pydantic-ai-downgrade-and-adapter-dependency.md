---
id: 133
feature: kitaru-replay-runtime
status: completed
---

# Dependency swap: pin pydantic-ai-slim >=2.22,<2.23 and add kitaru-pydantic-ai

Tags: `infra`, `refactor`
Depends on: 131, 132 (all dead-API surfaces gone first, so fallout here is ONLY the
pydantic-ai 2.23+ → 2.22 API delta)
Blocks: 134

This task implements ADR-0019 (§ Dependency). The `kitaru-pydantic-ai` adapter caps
`pydantic-ai <2.23`; decode currently pins `pydantic-ai-slim[google,openai]>=2.33.0`.
Human-approved: DOWNGRADE.

## Scope

- `pyproject.toml`: change `pydantic-ai-slim[google,openai]>=2.33.0` →
  `pydantic-ai-slim[google,openai]>=2.22,<2.23`; add `kitaru-pydantic-ai>=0.1.0` to runtime
  deps. `kitaru[cli,mcp,worker]>=0.22.2` stays. Re-examine the stale cap comments
  (`pydantic <2.13` / `click <8.3` cite "kitaru→zenml, ADR-0009" — zenml is out of the tree;
  lift a cap ONLY if `uv lock` proves it resolves AND the suite is green, otherwise keep the
  cap and fix the comment to cite ADR-0019).
- `uv lock` / `uv sync`; commit `uv.lock`.
- Fix every pydantic-ai 2.23+ API usage in `src/` and `tests/` that 2.22 breaks (agent loop,
  factory, deferred-tool surfaces, capstones — discovered by running the suite; this fallout
  is explicitly in-scope per the grilled decisions).
- Rewrite `tests/unit/decode/test_kitaru_dependency.py`: assert the NEW contract —
  `kitaru` importable, the old durability surface (`flow`/`checkpoint`/`wait`) absent,
  `from kitaru_pydantic_ai import KitaruAgent` importable, and its constructor accepts
  `(agent, agent_id=None, agent_version_id=None, session_name=None, batch_size=20)`
  (signature-inspection, no server).

## Acceptance Criteria

- [x] `uv lock` resolves with `pydantic-ai-slim` in `[2.22, 2.23)` and `kitaru-pydantic-ai>=0.1.0` alongside `kitaru 0.22.2` (if it cannot, STOP and escalate — do not improvise pins).
- [x] `python -c "from kitaru_pydantic_ai import KitaruAgent"` succeeds in the synced env.
- [x] Rewritten dependency smoke test green: old surface absent, new surface present.
- [x] `make ci` fully green (format, lint, unit, integration) — the feature-level "(a)" gate.
- [x] No `pydantic_ai` version-conditional shims left behind — code targets 2.22 cleanly.

## Out of scope

- Wrapping anything in `KitaruAgent` (134/135) — this task only makes it importable.
- Chasing newer pydantic/click majors beyond the lock-proof described above.

## Log

### [SWE] 2026-08-22 12:20 — Implementation

**Files modified**
- `pyproject.toml` — pin `pydantic-ai-slim[google,openai]>=2.22,<2.23`; add `kitaru-pydantic-ai>=0.1.0`; the dead zenml caps on `pydantic` / `click` replaced by plain major bounds (`<3` / `<9`).
- `uv.lock` — re-locked: pydantic-ai-slim 2.33.0 → 2.22.0, pydantic-graph 2.22.0, + kitaru-pydantic-ai 0.1.0, pydantic 2.12.5 → 2.13.4, click 8.2.1 → 8.4.2.
- `src/decode/agent/factory.py` — 1.x `output_retries=3, tool_retries=5` → the 2.x per-category dict `retries=AgentRetries(output=3, tools=5)` (the single root cause of 137 of the suite's errors).
- `src/decode/agent/loop.py`, `src/decode/tools/agent.py`, `src/decode/tools/registry.py` — stale "pydantic-ai 1.95 / 1.107" citations in comments corrected to 2.22; no behaviour change.
- `tests/unit/decode/test_kitaru_dependency.py` — rewritten to the new contract (kitaru importable; `flow`/`checkpoint`/`wait`/`save`/`ImageSettings` absent; `KitaruAgent` importable; constructor accepts `agent`, `agent_id=None`, `agent_version_id=None`, `session_name=None`, `batch_size=20` by inspection, no server).
- `tests/unit/decode/agent/test_factory.py` — `GoogleModel.system` is `"google"` on 2.x (was `"google-gla"`); Model-Override ids given the `vendor/model` shape real OpenRouter ids carry (`OpenRouterProvider.model_profile` splits on the prefix).
- `tests/unit/decode/agent/test_loop.py` — the below-both-tiers seed now pairs its `ToolReturnPart` with the `ToolCallPart` that produced it; 2.22 drops an orphan tool return when it cleans history.
- `tests/integration/test_opik_repl_trace.py`, `test_opik_headless_trace.py`, `test_observability_capstone.py` — tool spans are `execute_tool <name>` under 2.x instrumentation (default version 5), not `running tool`; the headless file's dual-name shim is gone (2.22 is the only target).
- `tests/integration/test_subagents_capstone.py`, `test_observability_capstone.py` — the live smokes now close the real provider HTTP client via pydantic-ai's own agent/provider lifecycle (`async with agent:`) instead of a `gc.collect()` that no longer collects anything: with `flow_mode`'s keep-alive-free client gone (ADR-0019 §1) a live turn parked a socket that pytest reported at SESSION teardown, failing a run in which every test passed.

**Tests**
- Unit: 2168 passing, 0 failing (`make unit-tests`) — was 101 failed / 78 errors before this task.
- Integration: 93 passing + 16 skipped (docker daemon unreachable) key-free; 96 passing with live keys.
- `make ci` (CI-equivalent, no provider keys — exactly what GitHub Actions runs): **exit 0**, 2261 passed, 19 skipped.

**Acceptance criteria**
- [x] `uv lock` resolves with pydantic-ai-slim 2.22.0 + kitaru-pydantic-ai 0.1.0 alongside kitaru 0.22.2 — verified by `uv lock --check` inside `make ci`.
- [x] `from kitaru_pydantic_ai import KitaruAgent` succeeds in the synced env — evidence below.
- [x] Rewritten dependency smoke test green — `tests/unit/decode/test_kitaru_dependency.py` (4 tests).
- [x] `make ci` fully green — exit 0 key-free (see Notes for the one live-key flake).
- [x] No version-conditional shims — the only one that existed (`test_opik_headless_trace._tool_spans` accepting both span names) is deleted; nothing branches on a pydantic-ai version.

**Evidence**

```
$ uv run python -c "..."
pydantic-ai-slim 2.22.0
kitaru 0.22.2
kitaru-pydantic-ai 0.1.0
pydantic 2.13.4 | click 8.4.2
KitaruAgent OK: KitaruAgent

$ make unit-tests
2168 passed in 36.19s

$ GEMINI_API_KEY= OPIK_API_KEY= OPENROUTER_API_KEY= make ci     # what CI runs (no secrets there)
uv lock --check
Resolved 181 packages in 3ms
uv run ruff format --check
299 files already formatted
uv run ruff check
All checks passed!
================= 2261 passed, 19 skipped in 189.79s (0:03:09) =================
exit=0

$ cd /tmp/e2e && printf 'launch code: 4127\n' > notes.txt
$ LLM_PROVIDER=gemini uv run decode run "Read notes.txt in the current directory and reply with just the launch code."
4127
```

**Notes**
- The caps were re-examined as instructed: with `pydantic` / `click` uncapped, `uv lock --upgrade-package …` resolves pydantic 2.13.4 + click 8.4.2 and the FULL suite stays green, so the dead "kitaru→zenml, ADR-0009" caps are gone. They were replaced with plain major bounds (`<3` / `<9`) rather than nothing, so a future `uv lock --upgrade` cannot silently pull a major.
- The 2.22 fallout was NOT a 2.23+ delta: the code was written against pydantic-ai 1.x, so the same 101 failed / 78 errors reproduced verbatim on 2.22 until `retries=AgentRetries(...)` landed. Everything after that was small: provider system name, tool span name, an orphan tool-return seed, unrealistic OpenRouter override ids.
- **One live-key flake, NOT a gate failure:** `tests/integration/test_subagents_capstone.py::test_live_gemini_fanout_smoke` asserts the real Gemini answer contains a text diagram; across 5 runs it passed 3 / failed 2, the failures being wrap-up answers ("compiled the prose and ASCII diagram…") where the diagram sat in an earlier response. It is skipped without `GEMINI_API_KEY` (i.e. always in CI), the harness side (fan-out, footer delivery, fold) passes every time, and the strictness is deliberate per its docstring — so I did not weaken it. Flagging for the Tester/PA rather than deciding it.
- `uv sync --all-groups` pulls the `remote` group (kfp/kubernetes/gcloud); plain `uv sync` is what `make install`/CI use and is what the reported runs used.
