---
id: 115
feature: evals
status: done
---

# Regression surface (a): pytest threshold gate + baseline compare

Depends on: 111–114. Implements ADR-0017 §6,9.

## Scope

**`evals/regression/test_thresholds.py`** — the pre-merge ritual module, DELIBERATELY outside
pytest's `testpaths` (never collected by `make test`/`make ci`; invoked explicitly by
`make eval-regression` → `uv run pytest evals/regression/test_thresholds.py`):

- Runs `run_regression()` once (session-scoped fixture), then asserts
  `evaluation.aggregate_evaluation_scores()` meets a per-metric threshold table (one readable
  constant dict at the top of the module — thresholds ARE the contract; start honest, e.g. tool-
  discipline ≥ 0.8, judges ≥ 0.7, tune from the first real runs and record the chosen numbers in
  the task log).
- Baseline compare: fetch the previous experiment via
  `opik.Opik().get_experiments_by_name(...)` and WARN (not fail) on regressions vs baseline while
  the absolute thresholds are the hard gate — keeps the ritual usable on day one.
- Skips gracefully (clear reason) when `GEMINI_API_KEY` / `OPIK_API_KEY` are absent.

**Tests** (`tests/unit/evals/`): threshold assertion logic + baseline-compare logic factored into
pure helpers and unit-tested with crafted score dicts (the pytest module itself stays thin).

## Acceptance Criteria

- [x] `uv run pytest evals/regression/test_thresholds.py` runs the suite and gates on thresholds;
      plain `pytest` / `make ci` never collects it.
- [x] Missing keys → skip with a friendly reason, exit 0.
- [x] Baseline compare surfaces per-metric deltas vs the last experiment by name.
- [x] Helper logic unit-tested offline; `make ci` green.

## Out of scope

- CI wiring (documented as "CI-pointable later", 120). Test Suites surface (116).

## Log

### [SWE] 2026-07-14 12:00 — Implementation

**Files modified**
- `evals/regression/thresholds.py` — NEW. Pure threshold-gate + baseline-compare logic (no Opik import):
  threshold table constants, `evaluate_thresholds`, `compare_to_baseline`, `latest_baseline`, the
  `scores_from_aggregation` / `baseline_scores_from_feedback` duck-typed extractors, and formatters.
- `evals/regression/test_thresholds.py` — NEW. The thin pytest ritual (OUTSIDE `testpaths`): session-scoped
  fixture runs `run_regression()` once, hard-gates on the absolute thresholds, WARNs (never fails) on
  baseline regressions, skips with a friendly reason when keys are absent.
- `evals/harness/regression.py` — `run_regression()` gained an `experiment_name` param forwarded to
  `evaluate()` so every gate run shares a stable name for `get_experiments_by_name` baseline lookup.
- `Makefile` — added `eval-regression` target (`uv run pytest evals/regression/test_thresholds.py`).
- `tests/unit/evals/regression/test_thresholds.py` — NEW. 18 offline unit tests for the pure helpers.
- `tests/unit/evals/harness/test_regression.py` — added a test that `experiment_name` reaches `evaluate`.

**Tests**
- Unit: 1873 passing, 0 failing (`make pre-commit` full suite). Helper module + `experiment_name`
  forwarding covered.
- Integration: N/A — no infra changes (the ritual is host-native + key-gated, excluded from CI).

**Threshold numbers chosen (honest first pass, ADR-0017 §6 — tune from first real runs)**
- Tool-discipline (all mechanical code metrics, the `DEFAULT_THRESHOLD`): **≥ 0.8**.
- Judges (every GEval reports under the single name `g_eval_metric`, which aggregates across all judged
  probes): **≥ 0.7**.
- Baseline WARN tolerance: **0.05** (absorbs judge noise before a dip is flagged).

**Design choices worth the Tester's eye**
- **Table shape.** All GEval judges share the metric name `g_eval_metric`, so aggregation collapses them
  under one key held to the judge floor; every other aggregated metric falls back to the discipline
  default. One readable dict (`THRESHOLDS = {g_eval_metric: 0.7}`, default 0.8) is the whole contract.
- **Absent-metric policy (probe 12 / skip-guarded).** A metric missing from the run's scores is NEVER
  gated — a behavior that did not run cannot regress. Probe 12's `tool_called_echo` is simply absent from
  the aggregation, so the gate neither fails nor vacuously passes it. The ritual asserts the report is
  non-empty (some probe DID run), so an all-absent suite can't slip through silently.
- **Baseline compare is a soft WARN.** Emitted via `warnings.warn` inside a `catch_warnings()` +
  `simplefilter("always")` block so `pyproject`'s `filterwarnings=["error"]` can't turn the soft signal
  into a gate failure (verified under `-W error`). Any Opik lookup hiccup degrades to "no baseline".
- **`run_regression(experiment_name=...)`.** Needed a stable experiment name for `get_experiments_by_name`
  to find prior runs; the default stays `None` (opik auto-names) so existing behavior is untouched.

**Evidence**
```
$ uv run pytest --collect-only -q | grep 'evals/regression/test_thresholds.py' | grep -v tests/unit | wc -l
0                       # plain pytest never collects the ritual

$ env -u GEMINI_API_KEY -u OPIK_API_KEY make eval-regression
evals/regression/test_thresholds.py ss                                   [100%]
SKIPPED [1] ...:81: regression gate needs GEMINI_API_KEY, OPIK_API_KEY — skipping (exit 0).
SKIPPED [1] ...:94: regression gate needs GEMINI_API_KEY, OPIK_API_KEY — skipping (exit 0).
2 skipped in 0.01s        # make exit: 0

$ # e2e against a REAL opik EvaluationResult (no keys needed) — proves the attribute wiring:
GREEN aggregated scores: {'tool_called_read': 1.0, 'max_steps': 0.95, 'g_eval_metric': 0.75}
  empty? False passed? True
RED passed? False
regression thresholds NOT met:
  - g_eval_metric: 0.600 < 0.7 (floor)
  - tool_called_read: 0.500 < 0.8 (floor)

$ make pre-commit
======================= 1873 passed in 101.16s (0:01:41) =======================
```

**Notes**
- Verified against installed `opik==1.9.8`: `EvaluationResult.aggregate_evaluation_scores()` →
  `.aggregated_scores` is `{name: ScoreStatistics(.mean)}`; `Opik.get_experiments_by_name(name)` exists
  and returns `Experiment`s; baseline per-metric averages come from
  `experiment.get_experiment_data().feedback_scores` (`FeedbackScoreAveragePublic.name/.value`) with
  `.created_at` for recency. The task's referenced `get_experiments_by_name` API is present as documented.
- The live hard-gate/baseline path (real agent + real Opik) is NOT runnable here (no keys); the gate
  logic is proven by unit tests + the real-`EvaluationResult` e2e above. First keyed run should confirm
  the chosen thresholds and record any tuning.

### [Tester] 2026-07-14 07:15 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check` both clean; `uv lock --check` clean)
- Unit tests: 1873 passed / 0 failed (`uv run pytest tests/unit -q`, 101.26s)
- Integration tests: N/A — no infra changes, confirmed correct per scope
- Warnings: 0 (full unit run had zero warnings; `filterwarnings=["error"]` in effect)

**E2E adversarial pass**
- Happy path: `uv run pytest evals/regression/test_thresholds.py` with keys unset → 2 skipped, exit 0, friendly reason naming missing keys (PASS)
- Break path 1 (collection isolation): `uv run pytest --collect-only -q | grep -i thresholds` → only `tests/unit/evals/regression/test_thresholds.py::*` (18 tests) collected; `evals/regression/test_thresholds.py` never appears. `grep eval-regression Makefile` shows it referenced only in its own recipe + `.PHONY`, never in `ci`/`test`/`pre-commit` targets (PASS)
- Break path 2 (hostile/malformed creds): `env GEMINI_API_KEY="FAKE_BOGUS_KEY_12345" OPIK_API_KEY="FAKE_BOGUS_OPIK_67890" uv run pytest evals/regression/test_thresholds.py` → fails fast (1.58s, exit 1) with a real Opik 401 `ApiError`, no hang, fake key values never echoed in output (`grep -c FAKE_BOGUS` on the log = 0) (PASS)
- Break path 3 (malformed/boundary scores): `evaluate_thresholds({"tool_called_read": None})` and `...({"tool_called_read": "high"})` → immediate `TypeError` (no silent pass, no hang); `evaluate_thresholds({})` → empty report, vacuously `.passed=True` but ritual's own `assert not report.empty` catches it (verified by calling `test_regression_meets_absolute_thresholds` directly against a real, empty `EvaluationResultAggregatedScoresView` → raises `AssertionError` naming the real cause) (PASS)
- Break path 4 (`-W error` does not escalate the WARN, using the REAL production functions, not just `_warn` in isolation): built a fake `opik.Opik` client returning a real-shaped `Experiment`-like baseline with a genuine 0.2 regression (> the 0.05 tolerance) and called the actual `test_baseline_compare_surfaces_deltas(FakeResult())` under `pytest -W error` → test PASSED, 2 `UserWarning`s captured in pytest's warnings summary, no escalation (PASS)
- Break path 5 (state edge: partial/malformed baseline data — **FOUND A BUG**): `evals/regression/test_thresholds.py::_load_baseline` (lines 119–149) wraps each candidate's `experiment.get_experiment_data()` call in its own `try/except Exception`, but the **final `latest_baseline(candidates)` call at line 149 is outside that guard**. `BaselineCandidate.created_at` (`evals/regression/thresholds.py:117`) is typed `datetime` (non-Optional) and is populated unguarded from `data.created_at` (`test_thresholds.py:143`) — but Opik's real `ExperimentPublic.created_at` is `Optional[datetime]` (confirmed via `ExperimentPublic.__annotations__` against the installed `opik==1.9.8`). Reproduced directly: `latest_baseline([BaselineCandidate("a", None, {"x": 0.5}), BaselineCandidate("b", datetime(2026,1,1,tzinfo=UTC), {"x": 0.6})])` raises `TypeError: '>' not supported between instances of 'datetime.datetime' and 'NoneType'`. If Opik ever returns one candidate experiment with a populated `created_at` and another with `None` (plausible: a partially-synced or legacy experiment under the same `EXPERIMENT_NAME`), this `TypeError` propagates uncaught out of `_load_baseline` and crashes `test_baseline_compare_surfaces_deltas` with an unhandled exception — directly contradicting the module's own documented invariant at `test_thresholds.py:126`: "Any Opik hiccup is swallowed to a `None` baseline (a soft signal must never break the gate)." (FAIL)

**Acceptance criteria**
- [x] PASS — `uv run pytest evals/regression/test_thresholds.py` runs the suite and gates on thresholds; plain `pytest` / `make ci` never collects it.
      Evidence: `uv run pytest --collect-only -q | grep -i thresholds` shows only `tests/unit/evals/regression/test_thresholds.py::*`; ritual run with real (mocked) `EvaluationResult` gates GREEN/RED correctly (see e2e above); `grep eval-regression Makefile` shows no reference from `ci`/`test`/`pre-commit`.
- [x] PASS — Missing keys → skip with a friendly reason, exit 0.
      Evidence: `env -u GEMINI_API_KEY -u OPIK_API_KEY make eval-regression` → `2 skipped in 0.01s`, `MAKE EXIT: 0`; partial-key case (`-u GEMINI_API_KEY` only) names only the missing key in the skip reason.
- [ ] FAIL — Baseline compare surfaces per-metric deltas vs the last experiment by name.
      Expected: baseline compare degrades gracefully to "no baseline" on any Opik data hiccup, per the module's own documented contract.
      Actual: a mixed `created_at`/`None` set of candidate experiments (a plausible real-world Opik response given `ExperimentPublic.created_at: Optional[datetime]`) crashes `latest_baseline()` with an unhandled `TypeError`, which is not caught by `_load_baseline`'s per-candidate `try/except` (it only wraps the loop body, not the final `latest_baseline(candidates)` call) — this turns the "soft WARN" ritual test into an unhandled ERROR instead of a graceful WARN/skip.
      Fix: guard `latest_baseline(candidates)` in `_load_baseline` (`evals/regression/test_thresholds.py:149`) with the same try/except-to-None pattern used for the per-candidate loop, or filter out / coerce candidates with `created_at is None` before calling `latest_baseline`, or make `BaselineCandidate.created_at` `datetime | None` and give `latest_baseline` an explicit, tested tie-break for `None` timestamps (`evals/regression/thresholds.py:117,186`). Add a unit test for `latest_baseline` with mixed `None`/real-datetime candidates.
- [x] PASS — Helper logic unit-tested offline; `make ci` green.
      Evidence: `uv run pytest tests/unit/evals/regression/test_thresholds.py -v` → 18/18 passed; `make format-check`, `make lint-check`, `uv run pytest tests/unit -q` all green (1873 passed, 0 warnings); `uv lock --check` clean.

**API-claims verification (against installed `opik==1.9.8`)**
- `EvaluationResult.aggregate_evaluation_scores() -> EvaluationResultAggregatedScoresView` with `.aggregated_scores: Dict[str, ScoreStatistics]` and `ScoreStatistics.mean: float` — confirmed via `__annotations__` introspection.
- `Opik.get_experiments_by_name(name: str) -> List[Experiment]` — confirmed via `inspect.signature`.
- `Experiment.get_experiment_data() -> ExperimentPublic`, with `.feedback_scores: Optional[List[FeedbackScoreAveragePublic]]` (`.name: str`, `.value: float`) and `.created_at: Optional[datetime]` — confirmed via `__annotations__` introspection. The `Optional` on `created_at` is exactly what produces the bug above.

**Evidence**
```
$ uv run pytest tests/unit -q
1873 passed in 101.26s (0:01:41)

$ uv run pytest --collect-only -q | grep -i thresholds
tests/unit/evals/regression/test_thresholds.py::test_threshold_for_uses_default_for_code_metrics
... (18 total, all under tests/unit/)

$ env GEMINI_API_KEY="FAKE_BOGUS_KEY_12345" OPIK_API_KEY="FAKE_BOGUS_OPIK_67890" uv run pytest evals/regression/test_thresholds.py -q
ERROR evals/regression/test_thresholds.py::test_regression_meets_absolute_thresholds
ERROR evals/regression/test_thresholds.py::test_baseline_compare_surfaces_deltas
2 errors in 1.58s   # real 401 ApiError, no hang, no leaked key value

$ uv run python -c "
from datetime import datetime, timezone
from evals.regression.thresholds import BaselineCandidate, latest_baseline
c1 = BaselineCandidate('a', None, {'x': 0.5})
c2 = BaselineCandidate('b', datetime(2026,1,1, tzinfo=timezone.utc), {'x': 0.6})
latest_baseline([c1, c2])
"
TypeError: '>' not supported between instances of 'datetime.datetime' and 'NoneType'
```

**Other issues found**
- None beyond the baseline-compare bug above. Threshold-table design, absent-metric policy, empty-report guard, and the `-W error` WARN-not-fail mechanism are all sound and match ADR-0017 §6,9.

**VERDICT: FAIL**

### [SWE] 2026-07-14 13:20 — Fixes (QA round 1)

**Issue addressed**
- `latest_baseline` crashed with `TypeError` on mixed `None`/`datetime` candidates because opik's
  `ExperimentPublic.created_at` is `Optional[datetime]` and `max(..., key=created_at)` compared `None`
  against a `datetime` — contradicting the "any Opik hiccup swallowed to a None baseline" invariant.

**Root-cause fix (regression-test-first)**
- `evals/regression/thresholds.py`:
  - `BaselineCandidate.created_at` is now `datetime | None` (matches opik's real type).
  - `latest_baseline` drops unscored candidates, then picks the newest among the DATED ones; an
    undated candidate sorts oldest (chosen only when NO scored candidate carries a timestamp, and even
    then a candidate is returned — never a `None`/`datetime` comparison). The helper is now total, so
    the gate can never crash on baseline lookup.
- `tests/unit/evals/regression/test_thresholds.py`: added the Tester's exact repro
  (`test_latest_baseline_handles_mixed_none_and_datetime_created_at`, both orderings) plus an
  all-undated best-effort case. Confirmed red before the fix (`TypeError: '>' not supported between
  instances of 'datetime.datetime' and 'NoneType'`), green after.

**Verification**
```
$ uv run pytest tests/unit/evals/regression/test_thresholds.py -q
20 passed

$ make pre-commit
======================= 1875 passed in 101.21s (0:01:41) =======================   # +2 vs round 1

$ uv run pytest --collect-only -q | grep 'evals/regression/test_thresholds.py' | grep -v tests/unit | wc -l
0                       # ritual still uncollected by plain pytest

$ env -u GEMINI_API_KEY -u OPIK_API_KEY make eval-regression
2 skipped in 0.01s      # exit 0

$ # mixed None/datetime baseline (former crash) now returns cleanly:
picked: dated
```

**Notes**
- format-fix / lint-fix / format-check / lint-check all clean. No commit — handing back to Tester.

### [Tester] 2026-07-14 14:05 — QA re-verification (round 2)

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check` both clean)
- Unit tests: 1875 passed / 0 failed (`uv run pytest tests/unit -q`, 100.92s) — matches SWE's claimed +2
- Integration tests: N/A — no infra changes, unchanged from round 1
- Warnings: 0

**Re-verified the round-1 blocker**
- Read the diff on `evals/regression/thresholds.py` and `evals/regression/test_thresholds.py`
  (untracked new files, so compared against my round-1 reads directly): `BaselineCandidate.created_at`
  is now `datetime | None`; `latest_baseline` filters to scored candidates, then to the DATED subset
  for the newest-by-time pick, falling back to `scored[-1]` only when no candidate carries a
  timestamp — no `None`/`datetime` comparison is possible anymore.
- Ran my exact round-1 repro directly against the fixed code:
  `latest_baseline([BaselineCandidate("a", None, {"x": 0.5}), BaselineCandidate("b", datetime(2026,1,1,tzinfo=UTC), {"x": 0.6})])`
  → no crash, returns `b` (the dated candidate). Reversed input order → same result (`b` wins
  regardless of position). All-undated case (`created_at=None` for every scored candidate) → returns
  the last discovered, no crash. (PASS — bug is fixed)
- Red-before claim: not cheaply re-creatable via git (the two files are untracked, no prior commit to
  diff against), but I personally reproduced the `TypeError` against the pre-fix code in round 1
  (`TypeError: '>' not supported between instances of 'datetime.datetime' and 'NoneType'`), so the
  red-before claim is corroborated by my own round-1 evidence, not just the SWE's say-so.
- New regression tests read and confirmed present: `test_latest_baseline_handles_mixed_none_and_datetime_created_at`
  (both orderings, asserts the dated candidate wins either way) and
  `test_latest_baseline_all_undated_returns_a_candidate_without_crashing` — both ran green as part of
  the 1875-passed full suite; spot-ran them in isolation too:
  `uv run pytest tests/unit/evals/regression/test_thresholds.py -v` → 20/20 passed (18 round-1 + 2 new).

**Re-confirmed unaffected behaviors**
- Collection isolation still holds: `uv run pytest --collect-only -q | grep -i thresholds | grep -v tests/unit` → 0 matches; `evals/regression/test_thresholds.py` never collected by plain `pytest`.
- Skip-without-keys unchanged: `env -u GEMINI_API_KEY -u OPIK_API_KEY make eval-regression` → `2 skipped in 0.01s`, exit 0, friendly reason.
- `Makefile` `eval-regression` target still isolated from `ci`/`test`/`pre-commit` (no diff on those targets since round 1).

**Acceptance criteria**
- [x] PASS — `uv run pytest evals/regression/test_thresholds.py` runs the suite and gates on thresholds; plain `pytest` / `make ci` never collects it. (unchanged from round 1, re-confirmed)
- [x] PASS — Missing keys → skip with a friendly reason, exit 0. (unchanged from round 1, re-confirmed)
- [x] PASS — Baseline compare surfaces per-metric deltas vs the last experiment by name.
      Evidence: round-1 crash repro (`latest_baseline` on mixed `None`/`datetime` candidates) now
      returns cleanly in both orderings; `test_latest_baseline_handles_mixed_none_and_datetime_created_at`
      + `test_latest_baseline_all_undated_returns_a_candidate_without_crashing` pass; full 1875-test
      suite green.
- [x] PASS — Helper logic unit-tested offline; `make ci` green.
      Evidence: `uv run pytest tests/unit -q` → 1875 passed, 0 warnings; `make format-check` / `make lint-check` clean.

**Evidence**
```
$ uv run python -c "
from datetime import datetime, timezone
from evals.regression.thresholds import BaselineCandidate, latest_baseline
c1 = BaselineCandidate('a', None, {'x': 0.5})
c2 = BaselineCandidate('b', datetime(2026,1,1, tzinfo=timezone.utc), {'x': 0.6})
print(latest_baseline([c1, c2]))
print(latest_baseline([c2, c1]))
"
BaselineCandidate(experiment_id='b', created_at=datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc), scores={'x': 0.6})
BaselineCandidate(experiment_id='b', created_at=datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc), scores={'x': 0.6})

$ uv run pytest tests/unit -q
1875 passed in 100.92s (0:01:40)

$ uv run pytest --collect-only -q | grep -i thresholds | grep -v tests/unit
(no output — ritual still uncollected by plain pytest)

$ env -u GEMINI_API_KEY -u OPIK_API_KEY make eval-regression
2 skipped in 0.01s      # exit 0
```

**Other issues found**
- None. The fix is total (no unguarded comparison remains) and directly targets the exact break path found in round 1. No new issues surfaced on re-review of the diff.

**VERDICT: PASS**
