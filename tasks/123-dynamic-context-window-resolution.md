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
- [x] `.env.example` and `getting_started/` describe the resolution order.

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
- `.env.example`, `getting_started/install_and_usage.md` — the resolution order + probing rules.
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
