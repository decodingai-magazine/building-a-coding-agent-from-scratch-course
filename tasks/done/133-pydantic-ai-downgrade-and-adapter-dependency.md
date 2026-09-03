---
id: 133
feature: kitaru-replay-runtime
status: done
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

### [Tester] 2026-08-22 03:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` — 299 files already formatted; `ruff check` — all checks passed)
- Unit tests: 2168 passed / 0 failed
- Integration tests: 93 passed + 19 skipped key-free (docker daemon unreachable locally, 16; live-key gated, 3) — reproduced independently
- `make ci` (true key-free, `.env` moved aside so no fallback secrets leak in): **2261 passed, 19 skipped, exit 0** — exact match to SWE's reported numbers
- Warnings: 0 (`filterwarnings=["error"]` enforced by pytest config; a stray warning would have shown as a failure, not a skip)

**E2E adversarial pass**
- Happy path: `LLM_PROVIDER=gemini uv run decode run "Read <notes.txt> and reply with just the secret word."` → `banana-47` (real Gemini call through the 2.22-pinned agent) (PASS)
- Break path 1 (dependency-surface honesty — old API absent): `uv run python -c "import decode.runtime.flow"` → `ModuleNotFoundError: No module named 'decode.runtime.flow'` (PASS, matches ADR-0019 §1 "no stubs")
- Break path 2 (version-conditional shim sweep): `grep -rn "output_retries\|tool_retries" src/ tests/` → zero hits in `src/`; one hit in a test docstring prose ("...`build_agent` sets `output_retries` above the default 1") that is stale wording, not a functional shim or version branch — see Other issues found (PASS with note)
- Break path 3 (signature spot-check against the ACTUAL installed pydantic-ai 2.22, not docs): `inspect.signature(Agent.__init__)` → `retries: 'int | AgentRetries | None' = None`, and `'output_retries' in sig.parameters` / `'tool_retries' in sig.parameters` both `False` — confirms the 1.x kwargs genuinely don't exist on 2.22 and the factory.py fix (`retries=AgentRetries(output=3, tools=5)`) is the only valid shape; `AgentRetries` introspects as a non-total `TypedDict` with `output: int` / `tools: int` keys, matching the call site exactly (PASS)
- Break path 4 (env-leak trap — my first `make ci` run silently reproduced 2264 passed/16 skipped instead of the SWE's 2261/19: `.env` on this machine carries real `GEMINI_API_KEY`/`OPENROUTER_API_KEY`/`OPIK_API_KEY`, and pydantic-settings' `env_file=".env"` loading ignores `env -u` shell unsets — so "key-free" via env-unset alone silently ran 3 live-key-gated tests instead of skipping them, which is not what CI actually sees, since CI has no `.env` at all): re-ran with `.env` physically moved aside → `2261 passed, 19 skipped, exit 0`, exact match to the SWE's reported evidence, `.env` restored and diffed byte-identical against a backup afterward (PASS — but flagging as a note: local `make ci` claims of "key-free" should say "with a machine that has no `.env`" or CI-equivalence claims can be silently wrong on a dev machine with a populated `.env`)
- Break path 5 (headless-runner off-happy-path, unrelated to this task's scope but exercised as part of the same binary): `LLM_PROVIDER=gemini uv run decode run ""` (empty task) → after ~70-90s (a 60s timeout was too short — NOT a hang, just multi-round output-retry latency) raises `RuntimeError: the headless runner expected text output but the agent deferred a tool call; BYPASS mode must run every tool inline (ADR-0019 §1).` — a clean, informative error, not a crash or leaked stack-trace-to-user in the CLI sense (it's a Python traceback but a deliberate RuntimeError with a clear message); this code path (`runtime/headless.py`) is untouched by this task's diff, so it's a pre-existing behavior, not a regression (PASS with note — not blocking task 133)

**Acceptance criteria**
- [x] PASS — `uv lock` resolves with `pydantic-ai-slim` in `[2.22, 2.23)` and `kitaru-pydantic-ai>=0.1.0` alongside `kitaru 0.22.2` — `uv lock --check` → "Resolved 181 packages in 3ms"; `importlib.metadata.version()` confirms `pydantic-ai-slim 2.22.0`, `kitaru 0.22.2`, `kitaru-pydantic-ai 0.1.0` in the synced env
- [x] PASS — `python -c "from kitaru_pydantic_ai import KitaruAgent"` succeeds — reproduced directly; `inspect.signature(KitaruAgent.__init__)` shows `agent, *, agent_id=None, agent_version_id=None, session_name=None, batch_size=20, cost_calculator=None, estimate_costs=True` — matches the rewritten smoke test's asserted defaults exactly
- [x] PASS — Rewritten dependency smoke test green — `uv run pytest tests/unit/decode/test_kitaru_dependency.py -v` → 4/4 passed (`test_kitaru_is_importable`, `test_the_durable_execution_surface_is_gone_upstream`, `test_the_recording_adapter_is_importable`, `test_kitaru_agent_takes_the_constructor_arguments_the_recording_seam_passes`); contract matches the task's Scope spec (old surface absent, new surface + constructor shape present)
- [x] PASS — `make ci` fully green — independently reproduced TRUE key-free (`.env` physically absent, not just shell-unset): `2261 passed, 19 skipped, exit 0`, byte-for-byte matching the SWE's reported evidence; `make format-check` and `make lint-check` also independently green
- [x] PASS — No `pydantic_ai` version-conditional shims left behind — `grep -rn "output_retries\|tool_retries" src/ tests/` finds zero hits in `src/`; the sole `tests/` hit is prose in a docstring, not a branch on version (see note above); confirmed no `if pydantic_ai.__version__` / try-import fallback patterns anywhere in the diff

**Evidence**
```
$ uv lock --check
Resolved 181 packages in 3ms

$ env -u GEMINI_API_KEY -u OPIK_API_KEY -u OPENROUTER_API_KEY -u GEMINI_MODEL -u DECODE_SANDBOX_GIT_TOKEN make ci   # .env moved aside first
...
================= 2261 passed, 19 skipped in 188.54s (0:03:08) =================
[exited with code 0]

$ uv run python -c "from pydantic_ai import Agent; import inspect; print(inspect.signature(Agent.__init__).parameters['retries'])"
retries: 'int | AgentRetries | None' = None

$ LLM_PROVIDER=gemini uv run decode run "Read .../notes.txt and reply with just the secret word."
banana-47
```

**Other issues found**
- `tests/unit/decode/agent/test_factory.py:252` docstring says "`build_agent` sets `output_retries` above the default 1" — stale identifier from the 1.x kwarg; the actual field is now `retries=AgentRetries(output=3, ...)`. Cosmetic only (doesn't affect test behavior, doesn't gate CI), but worth a one-line fix so a future reader doesn't go hunting for a kwarg that no longer exists.
- `_close_live_gemini_client` (the client-lifecycle fix) is duplicated verbatim in `tests/integration/test_observability_capstone.py` and `tests/integration/test_subagents_capstone.py`. Both are live-key-gated smokes so it never bites in CI; a shared test helper would be tidier but this is a nit, not a defect.
- Ruling on the flagged live flake (`test_live_gemini_fanout_smoke`, 3/5 pass rate): recommend **leave it strict**. It is unconditionally skipped without `GEMINI_API_KEY` (verified: my key-free CI run shows it in the SKIPPED list), so it never gates CI or blocks a merge; the harness-side assertions (fan-out, footer delivery, fold) it shares with the docstring's own claim pass every time — only the wrap-up-prose placement of the diagram flakes, which is model behavior, not decode's. Weakening the assertion would reduce its value as a real usage proof for the sake of quieting an operator-run smoke that isn't in anyone's gate. If it becomes a recurring annoyance for whoever runs live smokes manually, that's a separate small follow-up task, not a reason to fail this one.

**VERDICT: PASS**

### [SWE] 2026-08-22 04:05 — Post-PASS polish + close-out

**Files modified**
- `tests/unit/decode/agent/test_factory.py` — the `test_build_agent_retries_empty_model_responses_before_giving_up` docstring cited the 1.x kwarg `output_retries`; now cites the 2.x reality, `retries=AgentRetries(output=3, ...)`. Prose only, no behaviour change.

**Tests**
- Unit: 2168 passing, 0 failing (`make pre-commit`, which runs the unit suite)
- Integration: N/A — docstring-only change, no infra touched
- `uv run pytest tests/unit/decode/agent/test_factory.py -q` → 42 passed

**Acceptance criteria** — unchanged, all still `[x]` (see the Tester entry above).

**Evidence**
```
$ make format-fix && make lint-fix && make format-check && make lint-check
299 files left unchanged / All checks passed! / 299 files already formatted / All checks passed!

$ uv run pytest tests/unit/decode/agent/test_factory.py -q
42 passed in 1.31s

$ make pre-commit
============================ 2168 passed in 36.34s =============================
```

**Notes**
- The Tester's second note (`_close_live_gemini_client` duplicated across the two live-key capstone smokes) is deliberately **left as-is** — two call sites, no third, so no shared helper per the "no abstraction without a second concrete caller" rule in AGENTS.md. Noted for whoever adds a third live smoke.
- The substantive diff for this task landed in `be2f3ba` ("build: pin pydantic-ai-slim to 2.22 and add kitaru-pydantic-ai"), which predates the Tester PASS and carries no task trailer. This commit closes the task properly: the docstring fix, `status: done`, and the `git mv` into `tasks/done/`, with `Closes-task: 133-pydantic-ai-downgrade-and-adapter-dependency`.
- The flagged live-key flake (`test_live_gemini_fanout_smoke`) is left strict per the Tester's ruling — it never gates CI. If it becomes an annoyance for manual live smokes, that is a separate task.
