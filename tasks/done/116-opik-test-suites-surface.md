---
id: 116
feature: evals
status: done
---

# Regression surface (b): Opik 2.0 Test Suites

Depends on: 111 (+ probes for material). Implements ADR-0017 §6.

## Scope

The second, contrasting regression surface — natural-language assertions instead of code metrics —
over a SUBSET (~5) of the most judge-flavored probes (17, 18, 19, and two others the SWE picks):

**`evals/harness/test_suite.py`** + `python -m evals suite`:

- `opik.Opik().get_or_create_test_suite("decode-regression-suite", global_assertions=[...])` with
  natural-language quality bars ("the response never invents a file that does not exist", "the
  response follows the requested template sections", …); item-level assertions where a probe needs
  its own.
- Task adapter: reuse `regression_task_fn` outputs shaped for `opik.run_tests(test_suite=suite,
  task=...)`; keep judge-visible `input`/`output` clean (the docs warn: leaking expected answers
  into `input` lets the judge cheat).
- Run: `result = opik.run_tests(...)`; print and assert `result.pass_rate` against one suite-level
  bar; exit non-zero below it.

Docs paragraph in `evals/regression/README.md` contrasting the two surfaces (deterministic code
metrics + thresholds vs natural-language assertions) — that contrast is the teaching point.

**Tests**: suite construction + adapter shaping with the opik client mocked; pass_rate gate logic.

## Acceptance Criteria

- [ ] `python -m evals suite` builds/reuses the suite, runs it, prints pass rate, and gates on the
      bar (spot-run with real keys; logged). — [BLOCKED on opik>=2.0] CLI + build/run/gate wired and
      unit-tested against the mocked 2.0 surface; the real spot-run needs opik>=2.0, which the
      litellm/rustc pin blocks on this host (see log). On pinned opik 1.9.8 the command exits non-zero
      with a clear version-gate message (verified live).
- [x] Assertions never receive expected answers via `input`. — verified by
      `tests/unit/evals/harness/test_test_suite.py::test_suite_task_fn_input_never_leaks_an_expected_answer`
      and `::test_suite_item_data_never_carries_the_prompt_or_an_expected_answer`.
- [x] Offline unit tests for construction/adapter; `make ci` green. — full unit suite 1894 passed;
      `uv lock --check`, format-check, lint-check all clean.

## Out of scope

- Migrating all 20 probes to Test Suites (5-ish is the point — two surfaces, one lesson).

## Log

### [SWE] 2026-07-14 — Implementation

**Upgrade investigation (the KNOWN RISK) — path (a) confirmed IMPOSSIBLE on this host**
- Installed `opik==1.9.8`; confirmed it has NEITHER `Opik.get_or_create_test_suite` NOR `opik.run_tests`
  (both are the Opik 2.0 surface).
- `uv pip compile 'opik>=2'` → resolves `opik==2.1.24` + `litellm==1.92.0`.
- PyPI: `litellm 1.92.0` ships ONLY manylinux (Linux aarch64/x86_64) wheels + an sdist — **no macOS
  wheel**. On this darwin/arm64 host `uv` must build from the sdist.
- Attempted the build; captured the exact failure:
  `error: rustc 1.85.1 is not supported ... icu_normalizer@2.2.0 requires rustc 1.86 ... maturin failed`.
  Host has `rustc 1.85.1`; the litellm Rust bridge (`litellm-rust/crates/python-bridge`) needs
  `rustc >= 1.86`. Upgrade blocked exactly as the task predicted.
- **Decision: took path (b)** — implement the surface behind a runtime capability guard, written against
  the documented Opik 2.0 API, unit tests mocking the 2.0 surface. **Flag for PA:** lift the
  `opik>=1.9.8` / `litellm<1.78` pins in `pyproject.toml` once a build host has `rustc>=1.86` (or a
  macOS litellm wheel ships); the surface then activates unchanged (no code edit needed).

**Files modified**
- `evals/harness/test_suite.py` (new) — the Opik 2.0 Test Suites surface: version guard
  (`suite_api_available` + `SuiteUnavailableError` with the live version/rustc reason), 5-probe subset
  selection, item/global NL assertions, `{"input","output"}` adapter reusing `regression_task_fn`
  (input kept to the prompt alone — no leaked expected answer), `build_suite`, `run_test_suite`,
  `assert_pass_rate` gate.
- `evals/run.py` — new `suite` subcommand: runs the suite, prints pass rate + project, gates non-zero
  below the bar, turns the version gate / empty selection into a friendly `ClickException`. Opik
  imported lazily (keeps `--help` keyless).
- `evals/regression/README.md` — "Two regression surfaces" section: code-metrics-vs-NL-assertions
  contrast table + the honest opik-2.0/rustc version note.
- `tests/unit/evals/harness/test_test_suite.py` (new) — 16 offline tests (2.0 surface mocked): guard,
  item shaping, adapter, input-never-leaks, suite wiring, pass-rate gate.
- `tests/unit/evals/test_run.py` — 3 new CLI tests (pass-rate report, below-bar gate, version-gate
  message) + `suite` added to the `--help` assertion.

**Probe subset picked** — `17-grounded-answer`, `18-no-hallucinated-files`, `19-template-compliance`
(the ADR-named judge probes) + my two picks `05-web-fetch-discipline` (judge-flavored) and
`20-json-output-contract`. All five grade a quality visible in the agent's ANSWER TEXT, which is exactly
what an NL judge reads (`input`+`output`); behavioral probes (tool-call / denied-mutation signal) stay
on surface (a).

**Tests**
- Unit: 1894 passing, 0 failing (`make pre-commit` = format-check + lint-check + full unit suite).
  New: 16 in `test_test_suite.py`, 3 in `test_run.py`.
- `uv lock --check` clean (no new deps); format-check + lint-check clean.
- Integration: N/A — no infra changes.

**Evidence**
```
$ uv run python -c "import opik; print(opik.__version__, hasattr(opik,'run_tests'))"
1.9.8 False

$ uv pip install 'litellm==1.92.0'   # (from sdist, macOS)
error: rustc 1.85.1 is not supported by the following packages:
  icu_normalizer@2.2.0 requires rustc 1.86 ...
💥 maturin failed

$ python -m evals suite            # pinned opik 1.9.8 → version gate
Error: Opik Test Suites need opik>=2.0, but opik 1.9.8 is installed. The repo pins litellm<1.78
because opik 2.x pulls litellm 1.92, whose Rust bridge needs rustc>=1.86 ... See tasks/116-...
$ echo $?
1

$ make pre-commit
... 1894 passed in 105.26s ...
```

**Notes**
- `python -m evals suite` (real build/run/pass_rate) NOT RUN end-to-end — needs opik>=2.0, blocked by
  the litellm/rustc pin above. Instead verified: keyless `--help`, and the live version-gate exit
  (non-zero + clear message) on the pinned opik. The build/run/gate path is unit-tested against the
  mocked 2.0 surface.
- `ty check evals/harness/test_suite.py` reports 2 `unresolved-attribute` diagnostics on
  `get_or_create_test_suite` / `run_tests` — EXPECTED: those are the 2.0 API absent from the pinned
  1.9.8 stubs, and are the whole reason for the runtime guard. `ty` is dev-only/best-effort (ADR-0007)
  and NOT in `make ci` (ruff-only); I kept the real 2.0 type hints because they document the intended
  surface for when the pin lifts. Named trade-off, flagged here for the Tester/PA.
- Serial-run assumption: `run_tests` runs items serially in this harness (each item drives the real
  agent through the process-global `bash` seam — same reason surface (a) uses `task_threads=1`). If
  2.0's `run_tests` defaults to parallel items, its concurrency knob must be pinned to 1 when the pin
  lifts — noted in a code comment on `run_test_suite`.

### [Tester] 2026-07-14 15:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit` all
  green; `uv lock --check` clean, no new deps)
- Unit tests: 1894 passed / 0 failed (`uv run pytest tests/unit -q`)
- Integration tests: N/A — diff touches only `evals/` + its tests; `tests/integration` has no
  eval-related tests (confirmed by `find tests/integration -iname "*eval*" -o -iname "*suite*"` →
  empty) and no `src/decode` file is touched
- Warnings: 0

**E2E adversarial pass**
- Happy path (keyless `--help`): `uv run python -m evals --help` → lists `benchmark`, `regression`,
  `suite`, `sync`, exit 0, no network/keys touched (PASS)
- Break path 1 (real-world impossibility claim, reproduced independently): `curl
  https://pypi.org/pypi/litellm/1.92.0/json` → only `manylinux_2_28_{aarch64,x86_64}` wheels + sdist,
  **no macOS wheel**; `uv pip install --no-cache litellm==1.92.0 --target /tmp/litellm-test-install`
  on this darwin/arm64 host with `rustc 1.85.1` → forced sdist/maturin build →
  `error: rustc 1.85.1 is not supported ... icu_normalizer@2.2.0 requires rustc 1.86 ... maturin
  failed` — byte-for-byte the same failure class the SWE logged (PASS — claim verified, not just
  trusted)
- Break path 2 (version-gate on the real pinned opik 1.9.8, live process): `uv run python -m evals
  suite` → `Error: Opik Test Suites need opik>=2.0, but opik 1.9.8 is installed. ...rustc>=1.86...`,
  exit code 1, **no raw traceback to the user** (confirmed via direct subprocess, not just
  `CliRunner`) (PASS)
- Break path 3 (pass-rate gate boundary): scripted `assert_pass_rate` at `bar` (0.8, no raise), just
  above (no raise), just below (raises), `0.0` (raises), `1.0` (no raise) — matches the documented
  Opik semantics ("pass rate 1.0 means every item passed; 0.0 means none did") fetched live from
  `https://www.comet.com/docs/opik/latest/evaluation/advanced/building-test-suites.md` (PASS, with a
  cosmetic note below)
- Break path 4 (malformed CLI input): `python -m evals suite --bogus-flag` → Click usage error, exit
  2, no traceback; `python -m evals bogus-subcommand` → Click usage error, exit 2 (PASS)
- Break path 5 (mock-fidelity cross-check against the real docs, not just the ADR's paraphrase):
  fetched `building-test-suites.md` live — `get_or_create_test_suite(name=, project_name=,
  global_assertions=[...], global_execution_policy={"runs_per_item":, "pass_threshold":})`,
  `suite.insert([{"data":..., "assertions":[...]}])`, `opik.run_tests(test_suite=, task=)`,
  `result.pass_rate` — every kwarg name in `evals/harness/test_suite.py` matches verbatim; no invented
  kwarg found (PASS)

**Acceptance criteria**
- [ ] BLOCKED (honestly tagged, not a FAIL) — `python -m evals suite` builds/reuses/runs/gates with a
      real spot-run. Tester independently reproduced the litellm/rustc blocker (see break path 1) and
      independently verified the live version-gate exit (break path 2) and the mocked-2.0 build/run/gate
      unit tests (`tests/unit/evals/harness/test_test_suite.py::test_run_test_suite_builds_the_suite_and_runs_it`,
      `::test_build_suite_creates_the_named_suite_with_global_assertions_and_inserts_items`). Left
      unchecked correctly — the real spot-run genuinely cannot happen on this host. Awaiting an
      rustc>=1.86 build host / macOS litellm wheel to re-verify live.
- [x] PASS — Assertions never receive expected answers via `input`. Evidence:
      `make_suite_task_fn`'s `suite_task` (`evals/harness/test_suite.py:178-181`) returns
      `{"input": {"prompt": probe.prompt}, "output": payload["output"]}` — never forwards
      `file_state`/`agent_error`/etc; `suite_items` (`test_suite.py:148-162`) keys item `data` on
      `probe_id` alone, never the prompt; read every one of the 5 selected probe source files
      (`grounded_answer.py`, `no_hallucinated_files.py`, `template_compliance.py`,
      `json_output_contract.py`, `web_fetch_discipline.py`) — none embed a literal expected answer
      (e.g. 05's `_RATE_LIMIT = "240 requests per minute"` lives only in the surface-(a) G-Eval judge
      criteria, never in `ITEM_ASSERTIONS["05-web-fetch-discipline"]`, which says only "the rate limit
      stated on the page"). `tests/unit/evals/harness/test_test_suite.py::test_suite_task_fn_input_never_leaks_an_expected_answer`
      and `::test_suite_item_data_never_carries_the_prompt_or_an_expected_answer` pass.
- [x] PASS — Offline unit tests for construction/adapter; `make ci` green. Evidence: 16 new tests in
      `test_test_suite.py` + 3 in `test_run.py`, all passing inside the 1894-green full unit run;
      `uv lock --check` clean; `make format-check` / `make lint-check` clean; `ty check
      evals/harness/test_suite.py` reproduces the claimed 2 `unresolved-attribute` diagnostics on
      `get_or_create_test_suite` / `run_tests` (expected — dev-only, not in `make ci`, ruff-only).

**Evidence**
```
$ curl -s https://pypi.org/pypi/litellm/1.92.0/json | python3 -c "..."
bdist_wheel litellm-1.92.0-cp312-cp312-manylinux_2_28_aarch64.whl
bdist_wheel litellm-1.92.0-cp312-cp312-manylinux_2_28_x86_64.whl
... (no macOS entries) ...
sdist litellm-1.92.0.tar.gz

$ uv pip install --no-cache litellm==1.92.0 --target /tmp/litellm-test-install
  Building litellm==1.92.0
  x Failed to build `litellm==1.92.0`
  error: rustc 1.85.1 is not supported by the following packages:
    icu_normalizer@2.2.0 requires rustc 1.86 ...
  maturin failed

$ uv run python -m evals suite
Error: Opik Test Suites need opik>=2.0, but opik 1.9.8 is installed. ...
$ echo $?
1

$ uv run pytest tests/unit -q
1894 passed in 102.21s (0:01:42)

$ make format-check && make lint-check
... All checks passed! ...

$ uv run ty check evals/harness/test_suite.py
error[unresolved-attribute]: Object of type `Opik` has no attribute `get_or_create_test_suite`
error[unresolved-attribute]: Module `opik` has no member `run_tests`
Found 2 diagnostics
```

**Other issues found**
- Cosmetic only, not a functional bug: `assert_pass_rate`'s message uses `{:.0%}` formatting for both
  `pass_rate` and `bar`; a pass rate that is below the bar only by float noise (e.g.
  `0.8 - 1e-9`) still displays as "pass rate 80% is below the bar 80%", which reads as
  self-contradictory. Not reachable via a real Opik `pass_rate` (a ratio over 5 items — 0/.2/.4/.6/.8/1.0
  — never lands on a float-noise boundary), so this is a non-blocking note, not a FAIL.
- The `code-review` plugin is enabled in `.claude/settings.json`, but the Tester's toolset in this
  session has no Task/Agent invocation to fire it; substituted a manual line-by-line review of both
  new/changed files (mock-kwarg cross-check against live Opik docs, leak-check against every selected
  probe's source, type-signature check) in its place. Flagging per the QA playbook rather than silently
  skipping it.

**VERDICT: PASS**
