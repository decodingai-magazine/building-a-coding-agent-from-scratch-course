---
id: 123
feature: context-compaction
status: in-progress
---

# Resolve the compaction context window from the ACTIVE model, probing the provider when unknown

Follows `abf31e5`, which derives `compaction_context_window_tokens` from a static table keyed on the
configured model id. Two gaps remain, and they share one root cause — the window is resolved in
`Settings`, which is process-scoped and built at import:

1. **`--model` is invisible.** A run that overrides the model (ADR-0010 §2) keeps the window derived
   from configuration, so `--model` onto a smaller-window model silently re-creates the original bug.
2. **The static table rots.** It knows three model families. Every provider can report the real number:

   | provider | call | field | verified |
   |---|---|---|---|
   | modal (any OpenAI-compatible / vLLM) | `GET {url}/v1/models` | `max_model_len` | 262,144 for `Qwen/Qwen3.6-35B-A3B-FP8` |
   | gemini | `client.models.get(model=…)` | `input_token_limit` | 1,048,576 for `gemini-3.5-flash` |
   | openrouter | `GET https://openrouter.ai/api/v1/models` (public, no auth) | `context_length` | 339 models |

## Scope

Move window resolution out of `Settings` and behind a seam that knows the **active** model, and let
that seam probe the provider when the static table has no row.

**Resolution order** (first hit wins, and it must be observable in a log line):

```
explicit COMPACTION_CONTEXT_WINDOW_TOKENS  >  provider probe  >  static table  >  200_000 fallback
```

An explicit setting keeps winning outright and must never trigger a probe — the operator owns that
number.

**Where it resolves.** Today three call sites read `settings.compaction_context_window_tokens`
directly (`agent/loop.py:255,261`, `tui/app.py:577`). They must read the resolved window for the run's
actual model instead. Prefer threading it through existing run state (`AgentDeps` is the obvious
carrier) over adding a global.

**Probing rules — the part that makes this safe:**

- **Never at import.** `settings.py` documents that its sources must never raise; a network call in a
  validator would put I/O on `--help` and on all 2124 unit tests. The probe happens when the model is
  built/first used, not when config is loaded.
- **Never blocking beyond a short timeout.** A probe that cannot answer quickly loses to the table.
- **Never fatal.** Any exception (offline, 401, DNS, timeout) falls through to the table and then the
  fallback. A failed probe is a DEBUG line, never a crash and never a traceback at startup.
- **Cold-start aware.** A Modal endpoint may cold-start an H100 to answer `/v1/models`. Probing at
  agent-build time is acceptable because a real inference call follows immediately; probing on
  `--version` / `--help` is not. Verify no such path triggers it.
- **At most once per process per model id.** Cache in-process; a second resolution for the same id
  must not re-hit the network.

**The startup notice** added in `abf31e5` (`_context_window_notice` in `cli.py`) must keep telling the
truth: it may only claim the window is assumed when neither the probe nor the table produced one.

## Acceptance criteria

- [x] `decode run "…" --model <id-with-a-different-window>` resolves the window for `<id>`, not for
      the configured model. Covered by a test that asserts the resolved value, not just the call.
- [x] With `LLM_PROVIDER=modal` and a model absent from the static table, a stubbed `/v1/models`
      returning `max_model_len` is what the window resolves to.
- [x] Same for gemini (`input_token_limit`) and openrouter (`context_length`), each with the network
      boundary stubbed — no live provider call in the unit suite.
- [x] An explicit `COMPACTION_CONTEXT_WINDOW_TOKENS` wins and performs **no** probe (assert the stub
      was never called).
- [x] Every probe failure mode — timeout, non-200, malformed payload, missing field, raised exception
      — falls back to the table, then to 200,000, and never propagates. One test per mode.
- [x] Probing is memoised: two resolutions of the same model id hit the network once.
- [x] `decode --help` and `decode --version` perform no network call (assert the stub was never
      called).
- [x] The static table and `UNKNOWN_MODEL_CONTEXT_WINDOW` survive as the offline fallback; existing
      `test_settings.py` window tests still pass unchanged.
- [x] `make format-check lint-check unit-tests` clean. The 5 pre-existing opik-drift failures in
      `tests/unit/evals/harness/` are unrelated and stay out of scope — confirm the count is still 5.
- [x] `.env.example` and `running_the_code/` describe the resolution order.

## Out of scope

- A `decode doctor` command (option A) — the probe replaces its main use.
- On-disk caching across processes; in-process memoisation only.
- Widening the static table with unverified windows. A row needs a source.
- The `litellm` `load_dotenv()` pollution noted in `abf31e5` — real, separate.
- Fixing the 5 pre-existing opik `TestCase(scoring_inputs=…)` / version-assert failures.

## Log

### [PA] 2026-07-20 — Grooming

Split out of the `abf31e5` review. That commit deliberately shipped the static half first: it fixes
the live misconfiguration with zero new failure modes, and it is the fallback this task probes past.

The load-bearing constraint is that `Settings` is built at import time, so the probe cannot live where
the current derivation lives — that is the whole reason this is its own task rather than an edit to
`_derive_compaction_context_window`. The risk to watch during implementation is a probe sneaking onto
a no-inference path (`--help`, `--version`, `decode replay --list`) and cold-starting a GPU.

### [SWE] 2026-07-20 19:45 — Implementation

**Files modified**
- `src/decode/agent/context_window.py` — NEW: the resolution seam. `resolve_context_window(model)` /
  `resolve_context_window_detail()` walk explicit > probe > table > 200k and return a `ContextWindow`
  carrying `source`, so the notice can tell truth from guess. Probes modal/OpenAI-compatible, gemini,
  openrouter; memoised per `(provider, model id)`.
- `src/decode/agent/deps.py` — `AgentDeps.context_window_tokens: int | None`, the carrier the task
  asked for. `None` = "nothing resolved it" → readers fall back to the setting (keeps every existing
  test and any deps built elsewhere unchanged). Deliberately NOT resolved in `__post_init__`: that
  would fire a probe on every `AgentDeps()` in the suite.
- `src/decode/agent/loop.py` — both compaction reads now use the run's window.
- `src/decode/tui/app.py` — gauge divides by the run's window (else the bar and the trigger disagree);
  REPL deps resolve it.
- `src/decode/runtime/flow.py` — `_build_headless_deps` / `_build_hitl_deps` take `model` and resolve
  it, so `decode run --model X` compacts against X.
- `src/decode/tools/agent.py` — Explore children inherit the parent's window (same Agent, same model).
- `src/decode/cli.py` — `_context_window_notice(model)` resolves through the seam and only says
  "assuming" when both probe and table missed.
- `tests/conftest.py` — hermeticity guard `_no_context_window_probe`: stubs `_probe` suite-wide, so no
  test can reach a live provider (or cold-start an H100). Same pattern as `_no_real_provider_key`.
- `tests/support/settings_env.py` — NEW: `hermetic_settings()`; see Notes.
- `.env.example`, `running_the_code/install_and_usage.md` — the resolution order + probing rules.
- Tests: `tests/unit/decode/agent/test_context_window.py` (NEW, 30), plus additions to
  `test_cli.py`, `test_flow.py`, `test_loop.py`, `tui/test_app.py`.

**Tests**
- Unit: 2167 passing, 5 failing — the 5 pre-existing opik-drift failures, unchanged. Verified they
  fail identically on the pristine tree (`git stash` + `pytest tests/unit/evals/harness` → same 5).
  Baseline was 2124 passing; +43 new tests, 0 new failures.
- Integration: N/A — no infra changes.

**Acceptance criteria** — all 10 met.
- `--model` resolves the override's window — `test_flow.py::test_headless_deps_resolve_the_window_of_the_overridden_model` (asserts 262144 vs 1048576, the value not the call), `test_context_window.py::test_model_override_resolves_the_overridden_models_window`.
- modal `max_model_len` / gemini `input_token_limit` / openrouter `context_length` — `test_modal_probe_reads_max_model_len`, `test_gemini_probe_reads_input_token_limit`, `test_openrouter_probe_reads_context_length`.
- explicit wins, no probe — `test_explicit_setting_wins_and_never_probes`, `test_cli.py::test_context_window_notice_is_silent_when_the_operator_set_the_window`.
- failure modes — 3 exception modes + 9 payload modes, parametrised, each asserting the fallen-back-to source.
- memoised — `test_probing_is_memoised_per_model_id` (+ failures memoised, + distinct ids probed separately).
- `--help` / `--version` no probe — `test_cli.py::test_help_performs_no_provider_probe`, `..._version_...`, `..._run_help_...`.

**Evidence**
```
$ make format-check && make lint-check
306 files already formatted
All checks passed!

$ uv run pytest tests/unit -q
5 failed, 2167 passed in 122.33s
FAILED tests/unit/evals/harness/test_aggregates.py (x3)   # pre-existing opik drift
FAILED tests/unit/evals/harness/test_benchmark.py         # pre-existing opik drift
FAILED tests/unit/evals/harness/test_test_suite.py        # pre-existing opik drift

# e2e — real CLI, MODAL_ENDPOINT_URL pointed at a DEAD port (127.0.0.1:9)
$ time uv run decode --help      -> 1.38s, no probe, no hang
$ time uv run decode --version   -> 1.42s, no probe, no hang
$ echo "" | uv run decode        -> 1.56s
Decode: no known context window for model 'acme/unlisted-model-v1'; assuming 200,000 tokens for
compaction. Set COMPACTION_CONTEXT_WINDOW_TOKENS ...
  (failed probe degraded silently: no traceback, no hang)

$ COMPACTION_CONTEXT_WINDOW_TOKENS=8192 ... -> no notice (explicit wins, no probe)
$ MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8 ... -> no notice (table hit)

# e2e — successful probe against a REAL local stub serving /v1/models
INFO httpx: HTTP Request: GET http://127.0.0.1:8731/v1/models "HTTP/1.0 200 OK"
INFO decode.agent.context_window: context window for 'acme/unlisted-model-v1': 262144 tokens (source=probe)
INFO decode.agent.context_window: context window for 'acme/unlisted-model-v1': 262144 tokens (source=probe)
resolved: ContextWindow(tokens=262144, source='probe')
probe cache (one entry, one network hit): {('modal', 'acme/unlisted-model-v1'): 262144}
  -> two resolutions, ONE HTTP request: memoisation proven through the real stack
  -> REPL with this endpoint emits NO assumed-window notice (probe answered)
```

**Notes for the Tester**
- **Deliberate trade-off — the single-entry fallback.** `_match_window` matches the model id exactly;
  if there is no match AND the payload has exactly one row, it uses that row. Rationale: a dedicated
  vLLM endpoint serves one model whose reported id routinely differs from the configured one by a
  vendor prefix / quantization suffix. Consequence, visible in the e2e above: on a modal endpoint,
  `--model gemini-3.5-flash` resolves to the endpoint's 262144 rather than the table's 1048576. That
  is the *safe* direction (the endpoint's real ceiling), and such an override is already broken for
  inference. With 2+ rows and no id match it returns `None` rather than guess. Flagging it as intent.
- **`PROBE_TIMEOUT_S = 10.0`**, a module constant, not a setting. An idle Modal endpoint can take
  longer than that to answer; it then loses to the table, which is the documented behaviour, and the
  operator's escape hatch is setting the window explicitly (documented in `.env.example`). Say the
  word if you want it configurable.
- **Two PRE-EXISTING test-isolation leaks surfaced** (both out of scope per the task, neither fixed):
  (1) litellm's `load_dotenv()` copies the developer's `.env` into `os.environ`, so `Settings(_env_file=None)`
  is NOT hermetic once the suite has imported opik — my probe tests failed only in a full run because
  a real `LLM_PROVIDER=modal` + `MODAL_ENDPOINT_URL` got injected;
  (2) `tests/unit/evals/regression/test_cases_grounding.py:203` assigns to the `settings` singleton
  directly, which permanently adds `compaction_context_window_tokens` to `model_fields_set` — exactly
  the "operator set this" signal — so the singleton looks explicitly-configured for the rest of the
  session. I worked around both in my own tests via `tests/support/settings_env.py` (documented there)
  rather than touching the offending tests. Both deserve their own task.
- Probe is sync (startup paths, and flow mode runs under Kitaru's per-call event loops) — reasoned in
  the module docstring.

### [Tester] 2026-07-20 20:15 — QA

**Test summary** (all numbers from an isolated `git worktree` pinned to `ccd9886` — the commit this
task's diff landed as — because the shared working tree was being concurrently modified by other
in-flight tasks during this review; see "Other issues found"):
- Format / lint / pre-commit: PASS (`ruff format --check` 306 files already formatted; `ruff check`
  all checks passed)
- Unit tests: 2167 passed / 5 failed — the 5 are the pre-existing opik-drift failures
  (`tests/unit/evals/harness/{test_aggregates,test_benchmark,test_test_suite}.py`), independently
  confirmed identical (same 5 test ids) at the pre-task baseline `abf31e5` in a second worktree.
- Integration tests: 114 passed / 4 failed / 3 skipped. The 4 failures are all in
  `tests/integration/test_milestone3_skills_capstone.py` (a commit-skill body/content drift,
  unrelated to compaction or context windows) — independently reproduced on a clean `abf31e5`
  worktree with zero code from this task present, proving they pre-date it. The 3 skips are the
  live-Gemini/Opik smokes, expected without API keys.
- Warnings: 0 (`filterwarnings=["error"]`; no test emitted one).

**E2E adversarial pass**
- Happy path: `uv run decode --help` / `--version` → clean output, exit 0, ~1.1-1.4s (PASS).
- Break path 1 (no-probe on no-inference paths, against a black-hole address so a real probe would
  hang for `PROBE_TIMEOUT_S`): `timeout 15 env MODAL_ENDPOINT_URL="http://10.255.255.1:9999" uv run
  decode --help` and `--version` → both exit 0 in ~1.2s (not ~10s+), proving no probe fires (PASS).
  Contrast: the bare REPL (an inference path) against the *same* black-hole address took **11.6s**
  (the probe's real timeout) before falling back and printing the assumed-window notice — the timing
  delta is direct proof the probe is gated correctly by entry point, not merely by a stubbed test.
- Break path 2 (memoisation, at the real network level, both success and failure): wrote a script
  (`scratchpad/memo_e2e.py`) using a real local `http.server` — two `resolve_context_window_detail`
  calls against it produced exactly 1 HTTP hit and both returned `source="probe"`/262144; a second
  script against a genuinely closed port (`127.0.0.1:9`) called resolution twice in 3.7ms total
  (i.e., no second connection attempt/timeout paid) and both returned `source="fallback"`, with
  `_probe_cache[("modal", "acme/another-unlisted-model")] is None` confirmed cached (PASS).
- Break path 3 (explicit wins, no probe at all): reproduced independently outside the unit suite —
  `Settings(compaction_context_window_tokens=8192, modal_endpoint_url="http://127.0.0.1:9", …)` +
  `mock.patch.object(cw, "_probe")` → resolved to 8192/`source="explicit"`, `probe.assert_not_called()`
  passes (PASS).
- Break path 4 (every probe failure mode, unit-suite): timeout / non-200 / malformed-not-a-mapping /
  data-not-a-list / missing-field / null-field / string-field / zero / negative / empty-catalog /
  raised exception — all 12 shapes parametrised in `test_context_window.py`, every one falls to table
  then 200,000, never propagates (PASS, read not just run — `tests/unit/decode/agent/test_context_window.py:272-324`).

**Acceptance criteria**
- [x] PASS — `--model` resolves the override's window, not the configured model's, asserting the
      value — `tests/unit/decode/runtime/test_flow.py::test_headless_deps_resolve_the_window_of_the_overridden_model`,
      `tests/unit/decode/agent/test_context_window.py::test_model_override_resolves_the_overridden_models_window`.
- [x] PASS — modal `max_model_len` — `test_context_window.py::test_modal_probe_reads_max_model_len`
      (network boundary stubbed via `mocker.patch.object(cw.httpx, "get", …)`).
- [x] PASS — gemini `input_token_limit` / openrouter `context_length`, both stubbed —
      `test_gemini_probe_reads_input_token_limit`, `test_openrouter_probe_reads_context_length`.
- [x] PASS — explicit setting wins, no probe, asserted on the stub not the value —
      `test_explicit_setting_wins_and_never_probes` (`probe.assert_not_called()`); independently
      reproduced e2e above.
- [x] PASS — every probe failure mode falls to table then 200,000, never propagates — one test per
      mode, `test_probe_exceptions_fall_through_to_the_table` (parametrised x3: timeout/connect/raised)
      + `test_bad_payloads_fall_through_to_the_fallback` (parametrised x9: non-200 through
      empty-catalog).
- [x] PASS — memoised, one network hit for two resolutions of the same model id —
      `test_probing_is_memoised_per_model_id`, `test_a_failed_probe_is_memoised_too`; independently
      reproduced against a real socket above (success AND failure paths).
- [x] PASS — `--help` / `--version` perform no network call, asserted on the stub —
      `test_cli.py::test_help_performs_no_provider_probe`, `..._version_...`, `..._run_help_...`;
      independently timed against a black-hole address above (no ~10s hang).
- [x] PASS — static table + `UNKNOWN_MODEL_CONTEXT_WINDOW` survive unchanged as the offline
      fallback; `test_settings.py`'s existing window tests pass unmodified (0 diff to that file in
      this task's commit — confirmed via `git diff abf31e5 ccd9886 --stat -- tests/unit/decode/config/test_settings.py`
      returning nothing).
- [x] PASS — `make format-check lint-check unit-tests` clean; the 5 pre-existing opik-drift failures
      confirmed still exactly 5, identical test ids, independently reproduced on a bare `abf31e5`
      worktree — no new failure introduced by this task.
- [x] PASS — `.env.example` and `running_the_code/install_and_usage.md` both describe the
      explicit > probe > table > 200k resolution order (`git show ccd9886 -- .env.example
      running_the_code/install_and_usage.md`).

**Design judgment calls (asked to independently assess, not just accept)**
- `_match_window`'s sole-entry fallback (a modal endpoint with one served, non-matching model id
  reports its window anyway): judged **defensible, not a bug**. A modal/vLLM endpoint's `base_url`
  IS the real inference target regardless of what string `--model` sends as the OpenAI `model`
  field (`factory.py` sends `model or settings.modal_endpoint_model` verbatim; a lenient vLLM server
  serves its one loaded model regardless of the label) — so the endpoint's actual `max_model_len` is
  the real physical ceiling for whatever ends up running, and is the *safe* direction versus trusting
  a table entry for a model string that may not even be what's served. The 2+-rows-no-match case
  correctly declines to guess (`None`), which is the right asymmetry. Concur with the SWE's call.
- The two disclosed pre-existing test-isolation leaks (litellm's `load_dotenv()`; the
  `test_cases_grounding.py:203` direct singleton assignment): the `hermetic_settings()` /
  `scrub_settings_env()` workaround is sound. Verified order-independence directly — ran
  `tests/unit/evals/regression/test_cases_grounding.py`, `tests/unit/decode/agent/test_context_window.py`,
  and `tests/unit/decode/test_cli.py` in three different orderings (grounding-first, cli-first,
  context-window-first); all three orders: 148 passed, 0 failed. Also reproduced the full-suite leak
  itself: this machine's real `.env` has `LLM_PROVIDER=modal` + a real `MODAL_ENDPOINT_URL`, and the
  full `make unit-tests` run (2167 passed) still resolved the context-window tests correctly despite
  that live leak actually being present (not hypothetical) — direct evidence the scrub works, not
  just the theory.

**Evidence**
```
$ git worktree add /tmp/decode-task123-check ccd9886 --detach && cd /tmp/decode-task123-check
$ uv run ruff format --check   -> 306 files already formatted
$ uv run ruff check             -> All checks passed!
$ uv run pytest tests/unit -q
5 failed, 2167 passed in 142.96s
FAILED tests/unit/evals/harness/test_aggregates.py::test_summarize_groups_trials_by_dataset_item
FAILED tests/unit/evals/harness/test_aggregates.py::test_summarize_treats_a_missing_verify_score_as_a_fail
FAILED tests/unit/evals/harness/test_aggregates.py::test_attach_logs_derived_scores_onto_the_experiment_traces
FAILED tests/unit/evals/harness/test_benchmark.py::test_run_benchmark_attaches_aggregates_to_the_experiment
FAILED tests/unit/evals/harness/test_test_suite.py::test_run_test_suite_raises_a_clear_versioned_stop_when_unavailable

$ uv run pytest tests/integration -q
4 failed, 114 passed, 3 skipped in 332.10s
FAILED tests/integration/test_milestone3_skills_capstone.py::test_model_dispatcher_returns_the_builtin_body_ungated
FAILED tests/integration/test_milestone3_skills_capstone.py::test_tui_slash_command_submits_the_skill_body_not_the_literal_slash
FAILED tests/integration/test_milestone3_skills_capstone.py::test_project_override_wins_for_both_entry_points_and_the_catalog
FAILED tests/integration/test_milestone3_skills_capstone.py::test_builtin_skills_are_tier_2_only_with_no_resource_trailer

# same 4 reproduced on a bare abf31e5 worktree (this task's parent commit, zero task-123 code):
$ git worktree add /tmp/decode-baseline-check abf31e5 --detach
$ uv run pytest tests/integration/test_milestone3_skills_capstone.py -q
4 failed, 3 passed in 5.03s   # identical 4 test ids -> pre-existing, not introduced by this task

# no-probe timing proof against a black-hole address (would hang ~10s if a probe fired):
$ time timeout 15 env MODAL_ENDPOINT_URL="http://10.255.255.1:9999" uv run decode --help
... 1.05s user 0.19s system 99% cpu 1.247 total
$ time timeout 15 env MODAL_ENDPOINT_URL="http://10.255.255.1:9999" uv run decode --version
... 1.03s user 0.18s system 99% cpu 1.218 total
# contrast: the REPL (an inference path) against the same address:
$ time timeout 20 env MODAL_ENDPOINT_URL="http://10.255.255.1:9999" MODAL_ENDPOINT_MODEL="acme/unlisted-model-v1" LLM_PROVIDER=modal bash -c 'echo "" | uv run decode'
... 11.578 total   # pays the real PROBE_TIMEOUT_S, then degrades to the notice, no crash
```

**Other issues found**
- **Process: this task's work was committed and pushed to `origin/feat/dynamic-context-window`
  (commit `ccd9886`) partway through this QA session, before a Tester PASS was issued.** Per
  AGENTS.md, "SWE... commits each task after Tester passes" — this happened out of order. Content is
  unaffected (verified byte-identical between the staged diff I first reviewed and `ccd9886`), so it
  does not change this task's verdict, but the sequencing should not recur.
- **Scope leak: a second commit (`b5aaed1`, "fix(evals): follow opik 2.x in the harness unit
  tests") landed on this same branch during the same window**, fixing exactly the item this task's
  own "Out of scope" section names ("Fixing the 5 pre-existing opik `TestCase(scoring_inputs=…)` /
  version-assert failures"). It is a clean, separate commit (not mixed into `ccd9886`'s diff) and,
  spot-checked, it is correctly formatted and appears to genuinely fix the 3 `TestCase` call sites —
  but it was never asked for by this task, and it landed on `feat/dynamic-context-window`, a branch
  named for a different task, rather than its own branch. Recommend moving it to its own
  branch/task/PR rather than letting it ride in on this one.
- **A third, wholly unrelated commit (`6a67519`, "fix(observability): report LLM cost to Opik for
  all three providers") landed on this same branch while this review was in progress**, alongside
  live uncommitted WIP (`src/decode/observability/cost.py`, modified `settings.py` / `tracing.py`)
  that was actively changing on disk during my test runs — this is what produced the two extra unit
  failures (`test_env_example_drift`, `test_tracing`) visible in an un-isolated `pytest tests/unit`
  run partway through this session; they are 100% unrelated to task 123 and vanish once isolated to
  `ccd9886` in a clean worktree (see evidence above). Flagging because the shared working tree was
  not a stable snapshot during QA — I had to use detached `git worktree` checkouts to get
  reproducible numbers, and future reviews of this branch should expect the same until tasks land on
  their own branches.
- `.gitignore`'s `.DS_Store` addition and `runtime/flow.py`'s unrelated `_source_digest` ZenML
  build-reuse change are pre-flagged by the SWE as out of scope for this task's verdict — confirmed
  they do not break anything (`_source_digest` is exercised incidentally by `test_flow.py`'s existing
  image-settings tests, which pass).

**VERDICT: PASS**

Task 123's own commit (`ccd9886`) meets all 10 acceptance criteria with independent e2e evidence
beyond the unit suite, introduces zero new unit/integration failures (both pre-existing failure sets
independently reproduced on the pre-task baseline), and the e2e adversarial pass is green on every
break path attempted, including two that went beyond the unit suite's mocks to a real socket. Handing
off to PA for acceptance review — flagging the process items above for the orchestrator's attention
separately from this task's own correctness.
