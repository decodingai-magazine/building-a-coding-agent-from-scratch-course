---
id: 063-downgrade-pydantic-ai-for-kitaru
feature: kitaru-runtime
status: done
---

# Downgrade pydantic-ai 2.0 → 1.x (+ cap pydantic/click, add kitaru) and repair the agent loop

Tags: `infra`, `agent`, `deps`
Depends on: None
Blocks: #057, #058, #059, #060, #061, #062

This task implements **ADR-0009**. `kitaru` 0.18.0 (latest) cannot co-resolve with decode's core pins
(it drags in `zenml` + a pydantic-ai **1.x** adapter that cap `pydantic<=2.12.5`, `pydantic-ai` 1.x,
`click<=8.2.1`). The human chose **downgrade** at the `/implement-night` fork. This task lands the
kitaru-compatible pins + the `kitaru` dependency and repairs the agent loop for pydantic-ai 1.x so the
whole suite is green again — the prerequisite that unblocks 057's dependency and all of 058-062.

**Current working-tree state (from the spike — do not redo, verify):** `pyproject.toml` already has the
downgraded pins + `kitaru[local,pydantic-ai,llm]`; `uv.lock` is resolved; `uv sync` is done. The unit
suite is **51 failed / 872 passed**, all in the agent-loop / tool-through-agent / app-e2e paths.

## Scope

- **Pins (verify, already staged):** `pydantic>=2.0,<2.13`, `pydantic-ai>=1.89,<1.104`,
  `click>=8.1,<8.3`, and `kitaru[local,pydantic-ai,llm]>=0.18.0` in `pyproject.toml`; `uv.lock` green
  (`uv lock --check`). The `mcp` extra is intentionally dropped (ADR-0009 §2) — note it in the
  `pyproject.toml` comment.
- **Repair `agent/loop.py` for pydantic-ai 1.x, minimally and behind the stable public surface.** The
  dominant break is the usage API: 2.0's `run.result.usage.input_tokens` (property) is 1.x's
  `run.usage()` returning `RunUsage` with `request_tokens`/`response_tokens`. Map it so
  `self._last_input_tokens` / the public `last_input_tokens` property keeps its exact meaning and type
  (provider-reported input tokens), because ADR-0006 compaction triggers and the task-047 context
  gauge read it. Verify against the installed pydantic-ai 1.x (`uv run python -c` / its source) — do
  NOT guess the attribute name. Fix any sibling 1.x↔2.0 shims the suite surfaces (e.g. `agent.iter`
  node helpers, `DeferredToolRequests`/`DeferredToolResults` shape, `FunctionModel` usage in tests)
  **at the smallest blast radius** — production code first; only touch a test when the public behavior
  genuinely changed, never to paper over a regression.
- **Do not rewrite the loop or change its public surface.** The interactive turn behavior (streaming,
  boundaries, steering/abort, compaction, the gauge) must be byte-equivalent to before the downgrade —
  the only legitimate change is adapting to the 1.x SDK API.
- **Keep `agent/factory.py` + the LLM Gateway working** under pydantic-ai 1.x (`GoogleModel` /
  `OpenAIChatModel` construction, `output_type`, instructions hook) — repair only what 1.x requires.
- **ADR-0009** is already written and Accepted (`docs/adr/0009-downgrade-pydantic-ai-for-kitaru.md`);
  this task references it. If the repair reveals a materially different shim than ADR-0009 records,
  update the ADR's Decision/Consequences to match what shipped.

## Acceptance criteria

- [x] `uv lock --check` passes; `pyproject.toml` carries the four pin changes + `kitaru[local,pydantic-ai,llm]`
      with explaining comments referencing ADR-0009; `make install` resolves.
- [x] `import kitaru` and `from kitaru import flow, checkpoint, wait` succeed in the venv (smoke-asserted).
- [x] The agent loop is repaired for pydantic-ai 1.x: the **full unit suite is green** (the 51 spike
      failures pass; 0 regressions in the 872) under `make unit-tests`.
- [x] `last_input_tokens` still reports provider-reported **input** tokens (same meaning/type as under
      2.0); a unit test asserts the value is captured from a real turn (the existing
      `test_run_leg_captures_input_tokens...` passes, adjusted only if the public contract is unchanged).
- [x] ADR-0006 compaction (microcompaction + full) and the task-047 context gauge still work — their
      existing tests pass unchanged.
- [x] Interactive turn behavior is otherwise unchanged (streaming/boundary/steer/abort tests pass);
      no public API of `agent/loop.py` or `agent/factory.py` changed beyond the 1.x SDK adaptation.
- [x] `make ci` is green with 0 warnings (`filterwarnings=["error"]`); `uv lock --check` passes.
- [x] The diff is confined to deps + the agent-loop/factory 1.x shims (+ minimal test adjustments only
      where the SDK contract genuinely changed); no unrelated refactor.

## User stories

### Story: kitaru installs and the suite is green again
1. A developer runs `make install` then `make unit-tests`.
2. `kitaru` resolves and installs; the agent loop runs under pydantic-ai 1.x.
3. The full suite is green — the downgrade is absorbed with no behavior regression.

### Story: compaction and the gauge survive the SDK swap
1. A long conversation crosses the microcompaction and full-compaction lines.
2. The triggers fire exactly as under 2.0 (they read `last_input_tokens`, which still reports input
   tokens), and the footer gauge fills correctly.
3. No compaction/gauge test changed — only the loop's internal usage access did.

### Story: the change is auditable and reversible
1. A reviewer reads ADR-0009 + this task's diff.
2. The diff is pins + `uv.lock` + a confined `agent/loop.py` shim — no loop rewrite.
3. `git revert` of this commit + `uv sync` restores the pydantic-ai 2.0 stack cleanly.

## Out of scope
- The `runtime/` package, `decode run`, HITL, sleep, credentials (tasks 058-061) — this task only
  makes the project kitaru-compatible and green.
- The `runtime_*` settings + glossary (task 057 — its files may sit uncommitted in the tree alongside
  this work; commit only THIS task's files, never `git add -A`).
- The kitaru `mcp` extra and any MCP feature (step 15).
- Isolating kitaru in a separate env (the alternative fork — ADR-0009 non-goal).

## Log

### [SWE] 2026-06-28 01:21 — Implementation

**Files modified (task 063)**
- `pyproject.toml` — pre-staged downgraded pins (`pydantic<2.13`, `pydantic-ai>=1.89,<1.104`, `click<8.3`) + `kitaru[local,pydantic-ai,llm]>=0.18.0`; verified, not redone.
- `uv.lock` — pre-staged resolved tree (208 packages); `uv lock --check` passes.
- `src/decode/agent/loop.py` — the only production shim: `run.result.usage.input_tokens` (2.0 property) → `run.usage().input_tokens` (1.x method returning `RunUsage`). `RunUsage.input_tokens` is a real dataclass field in 1.x with the same meaning/`int` type, so `last_input_tokens` is byte-equivalent. Docstring/comment updated to cite ADR-0009. No loop rewrite; public surface unchanged.
- `tests/unit/decode/agent/test_factory.py` — test-only literal update for a genuine SDK contract change: `GoogleModel.system` is `"google-gla"` under pydantic-ai 1.x (was `"google"` under 2.0). decode does not set this value; the SDK does. Verified with `uv run python -c`.
- `tests/unit/decode/test_kitaru_dependency.py` — NEW smoke test: `import kitaru` + `from kitaru import flow, checkpoint, wait` resolve and are callable (the durability surface tasks 058-062 will use).
- `docs/adr/0009-downgrade-pydantic-ai-for-kitaru.md` — referenced (pre-written, Accepted). No amendment needed: the shipped shim is exactly the usage-API fix ADR-0009 §3 records, in fact narrower (one production line, not the hedged sibling shims).
- `tasks/063-downgrade-pydantic-ai-for-kitaru.md` — status `in-progress`; acceptance criteria checked.

**Decision verification (no guessing — checked the installed SDK)**
- pydantic-ai `1.94.0`. `AgentRun.usage()` is a method returning `self._graph_run.state.usage` (a `RunUsage`); `run.result.usage` is also a method (`# TODO (v2): Make this a property`). Used `run.usage()` per ADR-0009.
- `RunUsage.input_tokens` is a dataclass field (provider-reported input tokens). `request_tokens`/`response_tokens` are **deprecated** aliases — under `filterwarnings=["error"]` they would fail, so `input_tokens` is the correct (and identical-meaning) field.
- `run.result.output` and `run.result.new_messages()` are unchanged in 1.x — no other `_run_leg` accessor broke.

**Tests**
- Unit: 925 passing, 0 failing (`make unit-tests`).
- Integration: 12 passing (`make integration-tests`), incl. the M1 capstone driving a real turn through `build_agent()` + Runner + loop under 1.x.
- `make ci`: 937 passing, 0 warnings (`filterwarnings=["error"]`), `uv lock --check` clean.
- Baseline before fix: 58 failed / 877 passed — every failure traced to `loop.py:382` (usage) except the 2 factory `system`-literal failures.

**Acceptance criteria** — all met (none `[HUMAN]`):
- [x] pins + kitaru + `uv lock --check` + `make install` — verified.
- [x] kitaru imports — `tests/unit/decode/test_kitaru_dependency.py`.
- [x] full unit suite green — 925 passing, 0 regressions.
- [x] `last_input_tokens` reports input tokens — `test_loop.py::test_run_leg_captures_input_tokens_and_property_exposes_it` (unchanged, passes).
- [x] compaction + gauge tests pass unchanged.
- [x] streaming/boundary/steer/abort unchanged; no public-API change beyond 1.x adaptation.
- [x] `make ci` green, 0 warnings.
- [x] diff confined to deps + loop/factory 1.x shims + minimal test adjustments.

**Evidence**
```
$ make ci   (tail)
============================= 937 passed in 9.51s ==============================
$ uv lock --check
Resolved 208 packages in 3ms
$ uv run python -c "import kitaru; from kitaru import flow, checkpoint, wait; print(callable(flow), callable(checkpoint), callable(wait))"
True True True
$ uv run decode --help   (CLI boots under click 8.2)
Usage: decode [OPTIONS]
  Decode — a terminal coding agent you run in your terminal.
```

**Notes**
- No deviation from ADR-0009: the shim is the documented usage-API fix behind the stable public surface; ADR-0009 left unchanged.
- The `GoogleModel.system` `"google" → "google-gla"` rename is an orthogonal SDK contract change (a single test literal), well within ADR-0009's "a few 1.x↔2.0 API shims" — recorded here, not worth an ADR amendment.
- Manual TUI launch into a non-terminal stdin raises a `prompt_toolkit` non-TTY error (`fd 0 is not registered`) — a headless-invocation artifact, NOT a regression; the banner + `build_agent()` construct fine first. The automated whole-stack e2e is the green M1 capstone.
- File ownership respected: task 057's files (`.env.example`, `config/settings.py`, `tests/.../test_settings.py`, `tasks/057-*.md`, `docs/glossary.md`) were NOT touched.
- NOT committed — handing to the Tester first.

### [Tester] 2026-06-28 02:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 129 files clean; `ruff check` all passed)
- Unit tests: 925 passed / 0 failed (`make unit-tests`)
- Integration tests: 12 passed / 0 failed (`make integration-tests`) — incl. M1, compaction, LSP, skills capstones
- `make ci`: 937 passed / 0 failed; `uv lock --check` clean (208 pkgs)
- Warnings: 0 (`filterwarnings=["error"]` — any warning would have failed the run)

**SDK-contract verification (the shim is semantically correct, not just non-crashing)**
- Installed: pydantic-ai 1.94.0, pydantic 2.12.5, click 8.2.1, kitaru 0.18.0 — all inside the new pins.
- `RunUsage.input_tokens` is a real dataclass `int` field = provider-reported input tokens (introspected).
- `request_tokens`/`response_tokens` are DEPRECATED aliases that RAISE under `-W error` — the shim
  correctly uses `input_tokens`, so no `filterwarnings=["error"]` violation is possible. `grep`: zero
  uses of the deprecated aliases anywhere in `src/` or `tests/`.
- `AgentRun.usage` is a *method* (not a property) under 1.x → `run.usage().input_tokens` is the right call.
- `GoogleModel.system` genuinely resolves to `"google-gla"` (constructed the real model: `_provider.name`
  → `"google-gla"`) — the test literal change is a real SDK contract value, not papering over a regression.
- Whole input-token chain consistent on the non-deprecated field: `loop.py:385` writes it, `loop.py:273`
  + `compaction.py:111/113` read `input_tokens`, gauge reads it via `last_input_tokens` (`tui/app.py:560`).

**E2E adversarial pass**
- Happy path: `uv run decode --help` → usage/options render cleanly under click 8.2 (PASS)
- Break path 1 (boundary — missing secret, no `.env`): `env -u GEMINI_API_KEY uv run decode` from a
  dir without `.env` → one friendly stderr line + exit 1, NO traceback (PASS)
- Break path 2 (malformed — unknown CLI flag): `uv run decode --does-not-exist` → clean click usage
  error, non-zero, NO traceback (PASS)
- Break path 3 (state edge — co-import the whole point of this task): `import decode; import kitaru;
  from kitaru import flow, checkpoint, wait` under `-W error` → no version conflict, no deprecation
  leak at import; pins exactly pydantic-ai 1.94 / pydantic 2.12.5 / click 8.2.1 / kitaru 0.18 (PASS)
- Break path 4 (construction under 1.x): `build_agent()` constructs a real agent under `-W error`;
  `AgentRun.usage` confirmed a method (PASS)
- Break path 5 (FUNCTIONAL — threshold crossing via the shim): the compaction capstone drives a real
  multi-turn conversation where measured `input_tokens` grows 50→100→150 across accumulating legs and
  crosses the micro band ([90,120)) then the full band (≥120) THROUGH `run.usage().input_tokens`.
  This is decisive: were the shim returning output/total tokens the band crossings would miss and the
  capstone would fail. It passes (PASS)

**Acceptance criteria**
- [x] PASS — pins + `kitaru[local,pydantic-ai,llm]` + comments + `uv lock --check` + `make install` —
      Evidence: `pyproject.toml` diff (4 pins, ADR-0009 comments); `uv lock --check` → 208 pkgs; `make install` resolves + installs hooks.
- [x] PASS — `import kitaru` and `from kitaru import flow, checkpoint, wait` succeed — Evidence:
      `tests/unit/decode/test_kitaru_dependency.py` (2 tests pass); reproduced live under `-W error`.
- [x] PASS — agent loop repaired; full unit suite green — Evidence: 925 passed / 0 failed; the only
      production change is `loop.py:385` `run.result.usage.input_tokens` → `run.usage().input_tokens`.
- [x] PASS — `last_input_tokens` still reports provider INPUT tokens — Evidence:
      `test_run_leg_captures_input_tokens_and_property_exposes_it` (real turn, value > 0) +
      `test_full_tier_compacts_through_the_turn` (window=60 fires full → captured value ≥ 48, which only
      the input estimate ~50 can satisfy, not output ~2) pins the value to input tokens.
- [x] PASS — ADR-0006 compaction (micro + full) and task-047 gauge work unchanged — Evidence: all 3
      tier tests pass (`full`/`middle`/`below`), `test_zero_tokens_never_compacts` (0 fallback no-ops),
      compaction capstone crosses both bands live; gauge reads the unchanged public `last_input_tokens`.
- [x] PASS — interactive turn behavior unchanged; no public-API change beyond 1.x adaptation —
      Evidence: streaming/boundary/steer/abort tests in `test_loop.py` (34 pass); `last_input_tokens`
      property signature unchanged; diff is the docstring + one assignment line.
- [x] PASS — `make ci` green, 0 warnings, `uv lock --check` passes — Evidence: `make ci` → 937 passed.
- [x] PASS — diff confined to deps + loop/factory 1.x shims + minimal test adjustments — Evidence:
      `git status` shows only `pyproject.toml`, `uv.lock`, `loop.py`, `test_factory.py` (+ untracked
      ADR-0009, task 063, `test_kitaru_dependency.py`). No unrelated refactor; no `print()` added.

**File ownership** — VERIFIED. Task 063's logical diff touches only its own files. The working tree
also carries task 057's uncommitted sibling work (`.env.example`, `config/settings.py`,
`tests/.../test_settings.py`, `tasks/057-*.md`) — content-inspected and confirmed to be runtime-settings
(`RUNTIME_*`) content, NOT modified by 063; `docs/glossary.md` is untouched (absent from `git diff`).
This is exactly the "uncommitted-but-unchanged-by-063" state the spec describes. The SWE must commit
ONLY task 063's files (never `git add -A`).

**Evidence**
```
$ make ci   (tail)
============================= 937 passed in 9.71s ==============================
$ uv lock --check
Resolved 208 packages in 2ms
$ uv run python -W error -c "from pydantic_ai.usage import RunUsage; u=RunUsage(input_tokens=7); u.request_tokens"
DeprecationWarning: `request_tokens` is deprecated, use `input_tokens` instead   # (raises under -W error)
$ uv run python -c "...GoogleModel(...).system"
google-gla   # confirmed SDK contract
$ env -u GEMINI_API_KEY uv run decode   (from a dir without .env)
Decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).   # exit 1, no traceback
```

**Other issues found**
- Non-blocking, OUT OF SCOPE for 063: the startup guard prints `Decode:` (capitalized) while AGENTS.md
  documents `decode:` (lowercase). This lives in `cli.py` (task-004), which 063 does not touch — not a
  regression here; flag to the orchestrator/PA as a possible separate nit.
- Minor doc nit (non-blocking): the SWE log cites baseline "58 failed / 877 passed" while the task header
  cites "51 failed / 872 passed" (spike numbers from different points). Final state is 937 green; immaterial.

**VERDICT: PASS**
