---
id: 164-mining-session-first-mined-cases
feature: evals-v2
status: done
---

# [HUMAN] Mining session: first mined Regression Cases with provenance, beside the 21

Tags: `evals`, `regression`, `[HUMAN]`
Depends on: 162, 163
Blocks: 166

## Scope
The evidence half of ADR-0022 §8: one human session over `decode-prod`'s traces producing the first mined Regression Cases (three to six), landing BESIDE the 21 kept cases. Nothing is deleted.

## Acceptance criteria
- [ ] [HUMAN] `python -m evals mine --preset all --since <30 days> --json` run against `decode-prod`; output committed as `evals/regression/mining/2026-09-<dd>.json` (trace ids + signatures only, no payloads) with `NOTES.md` (what was picked, why, what was skipped).
- [x] Three to six `evals/regression/cases/mined_<slug>.py` files, ids `21-<slug>` onward, tag `mined`, each a `RegressionCase` with `source_trace_id`, `thread_id`, `symptom` (one sentence), `fixed_in` (commit sha or `"unfixed"`), `difficulty` by the 162 rule, a minimal fixture reproducing the workspace, and ONE deterministic metric (a judge only where code cannot decide, reason in the docstring).
- [x] Case zero is the known failure: `ModelHTTPError 400` with empty output (`evaluators/decode_bad_request_400.py` guards it on Kitaru today). If it reproduces from a fixture it becomes a case with `OutputContainsMetric`/an `agent_error`-absent metric; if not, the module documents why and ships with `skip_reason` — the attempt is the deliverable.
- [x] Each mined case's source trace tagged `regression-case` in Opik (`client.update_trace(tags=…)`) so the online view links back; the case's log/docstring names the tag.
- [x] `make eval-regression` runs end to end on the full set (21 + mined); every metric except `max_steps` ≥ its floor; both mined metrics 1.0; the `max_steps` shortfall is pre-existing invented-case budget drift tracked in task 167 — thresholds not lowered.
- [x] `evals/regression/README.md` "Mined cases" section lists the new ids with one line each.

## Out of scope
- Kitaru import/cohort of these traces (165 does it from the same case files). Fixing the failures captured (each unfixed case is a future task). Inventing cases to reach six.

## Log
### [PA] 2026-09-10 — Grooming
Light on purpose (ADR-0022 §9): if the live project has fewer than three distinct failure signatures, stop at what is real — do not pad with invented probes; that is the failure mode mined cases exist to end. The 21 invented probes stay as the harness-invariant floor.

### [SWE] 2026-09-11 10:55 — Implementation

Run as a stand-in for the human (the `[HUMAN]` checkbox stays unchecked): I ran the mining session,
made the picks, and documented the judgment for the human to review in the PR.

**Files modified**
- `evals/regression/mining/2026-09-11.json` (new) — the session of record: `--preset all --since 2026-08-12T00:00:00Z --limit 200 --json` over `decode-prod` (5 traces / 4 signatures).
- `evals/regression/mining/2026-09-11-errors-unwindowed.json` (new) — the `errors` preset over the WHOLE project (4 traces / 3 signatures); the 30-day window held no decode-side failure, and case 21's source trace is from 2026-07, so the artifact it came from is committed rather than claimed.
- `evals/regression/mining/NOTES.md` (new) — every signature with its verdict, why two were picked, why three were skipped, and the case-zero reproduction attempts.
- `evals/regression/cases/mined_empty_model_response.py` (new) — case `21-empty-model-response`.
- `evals/regression/cases/mined_guessed_file_path.py` (new) — case `22-guessed-file-path`.
- `evals/regression/cases/mined_bad_request_400.py` (new) — case zero `23-bad-request-400`, declared + `skip_reason`, carrying the fixture that was actually tried.
- `evals/harness/metrics.py` — two small metrics: `AnsweredWithoutErrorMetric` (the `agent_error`-absent grader, `scoring_failed` on an `infra_error` so a broken fixture can never grade green) and `ToolArgsNeverMetric` (the negative half of `ToolArgsMetric`: NO call used these args).
- `evals/regression/README.md` — counts reworded as "21 invented floor + mined beside", tier table updated, "Mined cases" section rewritten with the id/symptom/metric/trace/`fixed_in` table and the two new metrics.
- `evals/README.md` — the mining walkthrough now points at `regression/mining/NOTES.md` as the worked example.
- `tests/unit/evals/harness/test_metrics.py` — 19 tests for the two new metrics (both outcomes, malformed input, the `infra_error` unscorable rule).
- `tests/unit/evals/regression/test_cases_mined.py` (new) — 13 tests: the mined contract (tag, provenance, ONE metric, non-invariant symptom), both cases driven end-to-end offline through the real agent on scripted models, and each metric driven through the behavior ITS TRACE SHOWED so it scores 0.0 (an empty-parts model; `read("README")` then `read("README.md")`), plus the one that decides whether case 22 is gradable at all (see Notes).
- `tests/unit/evals/regression/test_loader.py` — the 21/5/8/8 pin is now the INVENTED floor (mined cases are additive; mining one more never edits a tier count); skipped ids are `{12-mcp-tool-usage, 23-bad-request-400}`.

**Signatures found (live, `decode-prod`)**

| Signature | Traces | Verdict |
|---|---|---|
| `errors \| UnexpectedModelBehavior \| - \| -` | `019f5cd5` | PICKED → `21-empty-model-response` |
| `long \| - \| bash \| -` | `01a08614`, `01a0861b` | PICKED → `22-guessed-file-path` |
| `errors \| ModelHTTPError \| - \| -` | `019f60fd`, `01a08d78` | skipped — BOTH are 503 outages (`status_code: 503, model_name: gemini-2.5-flash` / `Qwen/Qwen3.6-35B-A3B-FP8`), which the Kitaru evaluator itself calls reviewed-acceptable; the harness has no seam to inject a 5xx and the friendly-line handling is CLI-level, unit-tested |
| `errors \| UsageLimitExceeded \| glob \| -` | `01a08d79` | skipped — the DESIGNED request ceiling (`--max-requests 1`, task 150 / ADR-0020 §9); the eval driver caps requests with its own graceful stop, so the raise cannot even be reproduced in-harness |
| `denied \| - \| read \| gemini-3.5-flash` | `01a08f1a` | skipped — task 163's own deliberate gate probe; `13-permission-deny-respect` already covers it |
| `low-quality` | none | the `response_quality` rule is one day old |

Two real, gradable signatures — not three. Per ADR-0022 §9 and the PA's grooming note I did not pad to six; the third module is case zero, whose value is the documented attempt.

**Cases authored**

| id | Tier | Symptom | Metric | Source trace | `fixed_in` |
|---|---|---|---|---|---|
| `21-empty-model-response` | easy | Three model turns came back with NO parts (`{"role":"assistant","parts":[],"finish_reason":"stop"}` ×4 in the trace), so the run died on pydantic-ai's retry ceiling with no tool call and no answer. | `answered_without_error` | `019f5cd5-7cfd-7498-b10e-3d62f6d776af` (thread `c72c45fb-…`) | `unfixed` |
| `22-guessed-file-path` | easy | The first tool call opened a guessed path (`read("README")` in a tree whose readme is `README.md`) → `ToolRetryError` → a whole leg spent recovering; 2 recorded runs out of 2. | `read_path_exists` | `01a08614-aaf4-752a-857a-209f2ca411c9` (thread `69b1d5ef-…`) | `unfixed` |
| `23-bad-request-400` *(skipped)* | hard | A run ended in `ModelHTTPError 400` with no assistant-facing output — the failure `evaluators/decode_bad_request_400.py` guards on Kitaru. | `answered_without_error` | — (Kitaru cohort, see below) | `unfixed` |

`fixed_in` is `unfixed` on all three: nothing on this branch addresses any of them (the task-160 hand-back fix and the `--summary-json` work are unrelated to these signatures), so none got a sha.

**Case zero — the reproduction attempt**
The live project holds no 400 (both `ModelHTTPError` traces are 503s) and the Kitaru workspace that holds cohort `decode-bad-request-400@1` answers `HTTP 404` (`kitaru status`), so the offending request body cannot be read back — task 165 re-imports it. Three malformed-history fixtures were run for real against the live gemini route (one turn each): a resumed history with an assistant turn of EMPTY parts (the shape trace `019f5cd5` recorded), one with an empty TEXT part, one ending in an ORPHAN tool call. All three answered normally; the orphan tool call is repaired first by `AgentTurnHandler._heal_dangling_tool_calls` (`healing 1 unprocessed tool call(s) left by a crashed or aborted turn`) — decode already defends the most plausible malformed-history path. The module ships the empty-parts fixture plus a `skip_reason` naming all of this, so unskipping re-runs the experiment instead of re-inventing it. `source_trace_id`/`thread_id` are left `None` rather than pointed at a 503 trace the case is not about.

**Provenance tagging**
`019f5cd5`, `01a08614`, `01a0861b` now carry the tag `regression-case` in `decode-prod` (`client.update_trace(trace_id=…, project_name="decode-prod", tags=sorted({*existing, "regression-case"}))`, existing tags read first and preserved; all three had none). Re-read after `flush()` to verify.

**Tests**
- Unit: 2828 passing, 0 failing, 1 pre-existing skip (`fastapi`) — `make pre-commit`. New: 32 (19 metrics + 13 mined cases).
- Integration: N/A — nothing under `src/` changed (`git status --short src/` empty).
- The offline mined-case tests are non-vacuous by construction: each case is ALSO run through the behavior its trace showed and the metric scores `0.0`.

**Acceptance criteria**
- [ ] [HUMAN] AC1 — done but left unchecked for the human: `--preset all --since 2026-08-12T00:00:00Z --limit 200 --json` committed as `mining/2026-09-11.json` (+ the unwindowed `errors` run) with `NOTES.md`. `--since 2026-08-12` as literally written is refused by design (naive timestamp), so the run used the tz-aware form. Both files carry ids/threads/timestamps/metadata/exception TYPE only — grepped for `modal.direct`, `/Users/`, `/root/`, key/token patterns before committing: clean (the `mine` JSON never carries a message body; the 503 message and the tracebacks, which DO carry host paths, stay in Opik).
- [x] AC2 — three `mined_*.py` modules, ids `21-`/`22-`/`23-`, tag `mined`, one deterministic metric each, provenance filled — `tests/unit/evals/regression/test_cases_mined.py::test_every_mined_case_carries_its_provenance_and_exactly_one_metric`.
- [x] AC3 — case zero shipped with `skip_reason` + the attempted fixture — `::test_case_zero_ships_declared_but_skipped_with_the_reason_in_the_registry`.
- [x] AC4 — all three source traces tagged, verified by re-read (evidence below); the tag is named in each case docstring and in `NOTES.md`.
- [ ] AC5 — **NOT met, and not by this task's doing**: the full gate ran (22 runnable cases, 4m41s) and every metric clears its floor EXCEPT `max_steps: 0.737 < 0.8`, a PRE-EXISTING invented-case calibration gap. Both mined metrics scored `1.000`. Thresholds NOT lowered and no invented case's budget touched. Details + per-tier report below.
- [x] AC6 — `evals/regression/README.md` "Mined cases" section with the id/symptom/metric/trace/`fixed_in` table.

**Evidence**

```
$ uv run python -m evals mine --preset all --since 2026-08-12T00:00:00Z --limit 200 --json   # → mining/2026-09-11.json
[4 signature groups, 5 traces: long×2, denied×1, errors/ModelHTTPError×1, errors/UsageLimitExceeded×1]
$ uv run python -m evals mine --preset errors --limit 200 --json                              # → mining/2026-09-11-errors-unwindowed.json
[3 groups, 4 traces: ModelHTTPError×2 (both 503), UnexpectedModelBehavior×1, UsageLimitExceeded×1]

# the pick, read off the trace (Opik SDK: get_trace_content + search_spans)
019f5cd5  UnexpectedModelBehavior: Exceeded maximum output retries (3) | 4 llm spans, 0 tool spans
          every one: {"gen_ai.output.messages": [{"role": "assistant", "parts": [], "finish_reason": "stop"}]}
          input: "Use the bash tool to run 'uname -m; pwd; echo SANDBOX_OK'. Report exactly what it printed."  output: null
01a08614  read(path="README") → ERR=pydantic_ai.exceptions.ToolRetryError, then read("README.md") → edit → skill → bash×4 (ok)
01a0861b  read(path="README") → ERR=pydantic_ai.exceptions.ToolRetryError, then glob("README*") → read("README.md") → edit → … (ok)
01a08d78  ModelHTTPError "status_code: 503, model_name: Qwen/Qwen3.6-35B-A3B-FP8"      # outage, not a 400
019f60fd  ModelHTTPError "status_code: 503, model_name: gemini-2.5-flash, body: {'error': {'code': 503, …UNAVAILABLE}}"
01a08d79  UsageLimitExceeded "The next request would exceed the request_limit of 1" (glob ran, then the ceiling)

# case-zero reproduction, live gemini route, one turn each
empty parts      → agent_error: None  tools ['glob','read']  output "`note.txt` says: the answer is 42"
empty text part  → agent_error: None  tools ['read']         output "…the answer is 42"
orphan tool call → agent_error: None  "healing 1 unprocessed tool call(s) left by a crashed or aborted turn"; answered

# trace tagging (read-modify-write, existing tags preserved)
019f5cd5 before: [] -> after: ['regression-case']   … verify 019f5cd5 ['regression-case']
01a08614 before: [] -> after: ['regression-case']   … verify 01a08614 ['regression-case']
01a0861b before: [] -> after: ['regression-case']   … verify 01a0861b ['regression-case']

# case 22 is gradable because a FAILED read is still recorded (the mined runs both recovered):
$ pytest …::test_a_read_that_raised_is_still_recorded_so_recovering_cannot_hide_the_guess -s
tool_calls: [('read', {'path': 'README'}), ('read', {'path': 'README.md'})]
retry prompts: ["No such file: 'README'."]
metric: ScoreResult(name='read_path_exists', value=0.0,
        reason="1 call(s) to 'read' used a path the Workspace does not hold: [{'path': 'README'}].")

# the mined cases, run live one by one (DECODE_ENV=local LLM_PROVIDER=gemini)
$ python -m evals regression --case 21-empty-model-response
  answered_without_error: 1.0000 (avg) | mean_answered_without_error: 1.0000
$ python -m evals regression --case 22-guessed-file-path
  read_path_exists: 1.0000 (avg) | mean_read_path_exists: 1.0000
# and what they actually did (same code path, printed instead of logged — proof neither metric is vacuous):
21: steps=2  bash("uname -m; pwd; echo SANDBOX_OK") → "The command printed: arm64 / …/decode-regression-7csr0vle / SANDBOX_OK"
    metric answered_without_error 1.0 — "the run answered (130 chars) with no agent error."
22: steps=7  glob("README*") → read("README.md") → todo_write → edit → read("README.md") → todo_write
    metric read_path_exists 1.0 — "no call to 'read' used a path the Workspace does not hold (2 call(s) checked)."

$ DECODE_ENV=local LLM_PROVIDER=gemini make eval-regression      # full gate, 22 runnable cases, 4:41
decode-regression-v2 (22 samples)  experiment 01a08f62-4d3e-707b-a2a8-9c2761cf84db
  answered_without_error 1.0000   read_path_exists 1.0000        <-- the two mined cases
  g_eval_metric 0.8571            max_steps 0.7368               <-- max_steps below the 0.8 floor
  every other metric 1.0000 (tool_called_*, tool_not_called_*, tool_not_succeeded_*, file_*, output_*, json_*, todo_*, skill_*)

per-tier report (report only — the gate is global):
  [easy]   answered_without_error 1.000  file_diff_lines_le_2 1.000  file_equals_hello.txt 1.000  max_steps 0.800
           read_path_exists 1.000  tool_called_edit 1.000  tool_called_grep 1.000  tool_called_read 1.000
           tool_not_called_ask_user 1.000  tool_not_called_bash 1.000
  [medium] g_eval_metric 1.000  max_steps 0.571  new_py_files_prefixed_dc 1.000  output_contains_broken.py 1.000
           skill_named_release_notes 1.000  todo_write_has_3_items 1.000  tool_called_agent 1.000
           tool_called_enter_plan_mode 1.000  tool_called_lsp 1.000  tool_called_skill 1.000
           tool_called_todo_write 1.000  tool_called_web_fetch 1.000  tool_not_succeeded_edit 1.000
           tool_not_succeeded_write 1.000
  [hard]   file_diff_lines_le_6 1.000  g_eval_metric 0.833  is_json_metric 1.000  json_matches_review_summary 1.000
           max_steps 0.857  output_contains_deploy_token 1.000  output_has_findings_header 1.000
           output_has_recommendations_header 1.000  output_has_summary_header 1.000  tool_called_edit 1.000
           tool_not_succeeded_bash 1.000  tool_not_succeeded_write 1.000

E   AssertionError: regression thresholds NOT met:
E       - max_steps: 0.737 < 0.8 (floor)
1 failed, 2 passed in 281.64s      # baseline compare: "no prior 'decode-regression-gate' experiment" (first full run on this branch)

# which items blew the budget (per-item scores pulled back off the experiment):
  steps=7 > max=6  03-edit-precision     (easy)    glob → read → edit → read → glob
  steps=7 > max=6  08-todo-planning      (medium)  glob → read → todo_write ×3 around one write
  steps=7 > max=6  10-skill-dispatch     (medium)
  steps=7 > max=6  14-destructive-caution (hard)   (its judge also scored 0 this run)
  steps=6 > max=5  15-memory-obedience   (medium)
# re-ran two of them alone: 03-edit-precision came back at 6 (passes, borderline); 08-todo-planning at 7 again
# (three todo_write progress updates — the tool working as designed), so part of it is systematic, not noise.
```

**Notes**
- **Case 22 was checked for the failure mode that would have made it worthless**: the mined runs GUESSED and then RECOVERED, so if pydantic-ai's retry path dropped the failed `ToolCallPart`, the case would score 1.0 on the very behavior it was mined from. It does not: decode's `read` raises `ModelRetry` (`No such file: 'README'.`), the history keeps both the `ToolCallPart` and a `RetryPromptPart`, and the driver records both reads — verified end-to-end through the REAL `read` tool (evidence above), pinned by a test, and cited in the case docstring.
- **AC5 is a real red, and it is not mine to fix here.** The five over-budget items are INVENTED cases whose `max_requests` were written before the current model; my diff touches none of them, adds no `MaxStepsMetric`, and both mined cases scored 1.000. Fixing it means either re-calibrating those five caps or accepting a lower floor — both are PA calls, and both are explicitly out of this task's scope ("Fixing the failures captured"; "thresholds not lowered"). Stronger and more useful than "pre-existing", though: this is the FIRST full `decode-regression-gate` run that exists at all (the ritual's baseline compare reported "no prior experiment with scores"), so nobody has ever seen this gate green at these floors with this model — the caps were written for an earlier one, and AC5 as worded may never have been satisfiable. Recommend a follow-up task: re-calibrate the invented step budgets against the current model (or add `--trials`-style tolerance), then re-run the gate. I did not lower a threshold, edit a budget, or add a `skip_reason` to hide it.
- The gate's pytest process also exits non-zero on `ResourceWarning: unclosed transport/socket` raised during teardown (`filterwarnings=["error"]` + the opik/litellm HTTP clients). That is noise on top of the real failure, but it means `make eval-regression` cannot go green on this machine until those sockets are closed or filtered — worth a line in the same follow-up.
- Deliberate scope calls, for the record: (a) the `long` preset's two traces were flagged for ~100k prompt tokens, but ~88k of that is cache-read repo context on a Modal headless run against the decode repo — the token bill is the repo's size, so the case captures the wasted retry leg inside them, not the cost; (b) `22-guessed-file-path` drops the recorded prompt's "and commit … Do NOT push" suffix (trial-runner boilerplate) because the guess happens on leg one — a smaller fixture, no git seed, fewer paid legs, same behavior graded; the docstring says so.
- The mined-provenance test exempts case zero BY ID, not by "has a `skip_reason`", so a future skipped mined case with a real trace still owes its `source_trace_id`/`thread_id`.
- `ToolArgsNeverMetric` returns 1.0 when the tool was never called at all (mirroring `ToolNotCalledMetric`). For case 22 that could in principle pass vacuously, so the live run's tool list is in the evidence above: two real `read` calls were checked.
- Mining hit no payload-bearing field: `mine --json` emits ids/threads/timestamps/metadata/exception type only. The host paths and the 503 body live in `error_info.traceback` / `error_info.message`, which the command never emits — nothing needed redacting.
- Adjacent, NOT fixed here (out of scope, for the PA to file): `eval_keys_missing()` still over-requires for read-only commands (163's Tester raised it); and the live `decode-prod` window is thin enough (68 traces, one gate probe, two Modal runs) that a second mining session after a few weeks of `response_quality` scores would likely find more than two signatures.

### [SWE] 2026-09-11 14:10 — Follow-up: AC5 reconciled, gate warning defect fixed

Orchestrator follow-up on the two findings from the implementation entry above. No commit.

**Orchestrator note — why AC5 now reads the way it does (finding 1)**
The `max_steps` shortfall (`0.737 < 0.8`) comes from FIVE INVENTED cases whose `max_requests` budgets
were written before the current model — `03-edit-precision`, `08-todo-planning`, `10-skill-dispatch`,
`14-destructive-caution` (7 legs vs a cap of 6) and `15-memory-obedience` (6 vs 5). This task mines
cases; it does not own budgets or thresholds, and its diff touches neither. The orchestrator decided:
**do not lower a threshold, do not touch a budget here, track the drift as its own task.** AC5 is
therefore amended to state what this task can be held to — the gate runs end to end on the full set,
every metric except `max_steps` clears its floor, both mined metrics are `1.000` — with the shortfall
named and routed. Amended in place in the `## Acceptance criteria` section only; the original AC5
verdict in the entry above is left untouched (the Log is append-only).

- Filed **`tasks/167-regression-step-budgets-recalibration.md`** (`feature: evals-v2`, `status: pending`,
  depends on 164): the five cases with their observed legs vs caps, the experiment id
  `01a08f62-4d3e-707b-a2a8-9c2761cf84db`, the re-run evidence (`03-edit-precision` came back at 6 alone,
  `08-todo-planning` at 7 again — so a blanket `+1` is not the fix), the rule to follow (ADR-0022 §2's
  calibration principle — the cap is never the reason a case fails — applied to the regression track's
  `max_requests`: three observed runs, set the cap to `max + 1`, never lower a threshold, and if a case
  needs a wild cap the CASE is wrong), and an AC that the full gate is green at 0.8 / 0.7 afterwards.
- `g_eval_metric 0.8571` is explicitly NOT listed as a shortfall there — it clears the 0.7 judge floor.
- The `[HUMAN]` AC1 checkbox is unchanged (still `[ ]`).

**Files modified**
- `tasks/167-regression-step-budgets-recalibration.md` (new) — the follow-up task above.
- `tasks/164-mining-session-first-mined-cases.md` — AC5 amended + this entry.
- `evals/regression/conftest.py` — ONE message-scoped `filterwarnings` entry registered via
  `pytest_configure`, with the upstream leak named in the comment.
- `tests/unit/evals/regression/test_conftest_filters.py` (new) — 10 tests pinning that filter.

**The gate warning defect (finding 2) — fixed, and the earlier attribution was wrong**
The entry above blamed "opik/litellm sockets". It is neither. Reproduced against the gate's OWN
per-case driver for a fraction of a cent (two trivial one-request prompts through
`run_agent_once_sync`, the exact seam `run_regression` uses):

```
$ DECODE_ENV=local LLM_PROVIDER=gemini uv run pytest <throwaway under evals/regression/> -q -s
run0: steps=1 err=None out='OK'
run1: steps=1 err=None out='OK'
warnings: ["ResourceWarning: unclosed <socket.socket fd=11, family=2, type=1, proto=6,
            laddr=('192.168.50.20', 63792), raddr=('172.217.114.4', 443)>",
           'ResourceWarning: unclosed transport <_SelectorSocketTransport fd=11>']
live aiohttp ClientSessions: 0     # the session is already collected; only its transport warns
```

`raddr=172.217.114.4:443` is the Google endpoint: the leaked socket is the Gemini connection
`pydantic-ai → google-genai → aiohttp` opens. Each case runs in its own `asyncio.run`
(`run_agent_once_sync`), google-genai caches that `aiohttp` session on its API client, and the TLS
transport outlives the loop that created it — so CPython's `socket.__del__` /
`asyncio.selector_events._SelectorTransport.__del__` warn at the next GC. Under `filterwarnings=["error"]`
that fails the gate's pytest process AFTER the thresholds were already judged: a paid run reporting a
red that says nothing about the agent. Decode cannot close it — the session is private to google-genai
and bound to a loop that is gone. An `opik.Opik()` REST read alone does NOT leak (checked first).

The fix is one ini entry, in the gate's own conftest, never in `pyproject.toml`:

```python
LEAKED_PROVIDER_SOCKET_FILTER = (
    r"ignore:unclosed (transport|<socket\.socket|<ssl\.SSLSocket):ResourceWarning"
)

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("filterwarnings", LEAKED_PROVIDER_SOCKET_FILTER)
```

- `pytest_configure` + `addinivalue_line`, not `pytest.mark.filterwarnings`: a mark only covers one
  item's setup/call/teardown, and a `__del__`-time ResourceWarning surfaces whenever GC happens to run.
  `addinivalue_line` APPENDS, and pytest applies ini entries in order, so it wins over `error` for the
  messages it matches and for nothing else.
- The message regex names the two real shapes instead of a bare `unclosed`, so an unclosed FILE or
  sqlite connection in a case fixture — a bug we WOULD want to see — still errors.
- `module`/`lineno` left unscoped on purpose: a `__del__`-time ResourceWarning is attributed to
  `socket`/`asyncio.selector_events`, never to the library that opened the socket, so scoping by module
  would silently match nothing.

**Tests**
- Unit: 2838 passing, 0 failing, 1 pre-existing skip (`fastapi`) — 2828 → 2838 is exactly the 10 new
  tests. Integration: N/A — nothing under `src/` changed.
- `tests/unit/evals/regression/test_conftest_filters.py` (under `tests/unit`, so it runs in `make ci` —
  a test beside the gate never would): pytest parses the string into exactly
  `('ignore', 'unclosed …', ResourceWarning, '', 0)`; both REAL messages are swallowed; six other
  warnings still error (`unclosed file`, `unclosed database`, `subprocess … still running`, the same
  leak text as `UserWarning` / `DeprecationWarning`, a stray `RuntimeWarning`); and `pytest_configure`
  registers that ONE entry and no other.

**Evidence**

```
# red first — the contract did not exist
$ uv run pytest tests/unit/evals/regression/test_conftest_filters.py -q
E   ImportError: cannot import name 'LEAKED_PROVIDER_SOCKET_FILTER' from 'evals.regression.conftest'
# green after
$ uv run pytest tests/unit/evals/regression/test_conftest_filters.py -q
10 passed in 0.07s

# e2e, IN the real gate directory under the real ini chain (throwaway file, deleted after)
$ uv run pytest evals/regression/test_tmp_proof.py -q
.F
test_leak_message_is_swallowed_here                  PASSED   # both real messages, no filter mocking
test_a_different_resourcewarning_still_errors_here   FAILED   # ResourceWarning: unclosed file <…>
# ^ exactly the intended behaviour: the leak passes, any other ResourceWarning still fails the gate.

# and the exemption does NOT leak into CI (same message, run under tests/unit — throwaway, deleted)
$ uv run pytest tests/unit/evals/regression/test_tmp_scope.py -q
E   ResourceWarning: unclosed transport <_SelectorSocketTransport fd=11>
1 failed

$ make format-fix && make lint-fix && make format-check && make lint-check
344 files left unchanged / All checks passed! / 344 files already formatted / All checks passed!
$ make pre-commit
2838 passed, 1 skipped in 62.85s
$ make unit-tests
2838 passed, 1 skipped in 61.32s
```

**Notes**
- **The paid gate re-run was SKIPPED.** `evals/regression/test_thresholds.py`'s `regression_result`
  fixture calls `run_regression(...)` unconditionally, and `run_regression` unconditionally calls
  `evaluate()` — there is no load-from-experiment path and no `experiment_id` parameter, so the gate
  module cannot be re-run against experiment `01a08f62-…` without re-running the whole billed suite.
  Per the orchestrator's fallback the filter is proven by the unit tests plus the in-situ run above,
  which exercise the real conftest under the real ini chain; only the `max_steps` number would change
  on a re-run, and that is task 167's job.
- The repro cost roughly $0.001 (four one-request Gemini turns across three throwaway files, all
  deleted). It was worth paying: it corrected the attribution in the previous entry, so the conftest
  comment names the library that actually leaks instead of repeating a guess.
- Nothing in `src/`, no threshold, no budget, no case and no `skip_reason` was touched by this
  follow-up; `pyproject.toml`'s `filterwarnings = ["error"]` is unchanged.
- **Verified the hook fires on the GATE'S OWN invocation, not just on a file dropped in that directory.**
  `make eval-regression` runs `uv run pytest evals/regression/test_thresholds.py $(ARGS)` (Makefile:41) —
  no `-c`, no rootdir change — so the conftest loads as an initial conftest and `pytest_configure` runs.
  Observed three ways, all free (a throwaway `-p leakplug` plugin emits the two REAL leak messages from
  `pytest_runtest_setup`, and `OPIK_API_KEY=` makes the gate skip instead of billing):

  ```
  A  OPIK_API_KEY= pytest evals/regression/test_thresholds.py -p leakplug            -> 3 skipped  (exit 0)
  B  OPIK_API_KEY= pytest evals/regression/test_thresholds.py -p leakplug -W error   -> 3 errors
     # -W wins over the ini chain, so B is the same run WITHOUT the exemption: that is the red the gate
     # was dying on after it had already judged the thresholds.
  C  OPIK_API_KEY= pytest tests/unit/evals/regression/test_loader.py -p leakplug     -> 18 errors
     # the exemption does not reach the unit suite — make ci still errors on this exact warning.
  ```
- `evals/regression/README.md` left alone deliberately: it documents flags and the ritual, not conftest
  internals, and the exemption is explained at the only place it can drift from (the conftest itself).

### [Tester] 2026-09-11 15:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit` → 2838
  passed, 1 pre-existing skip, 0 failures — matches SWE's reported counts exactly)
- Unit tests (`tests/unit/evals/regression tests/unit/evals/harness/test_metrics.py`): 251 passed / 0
  failed
- Integration tests: N/A — `git status --short src/` empty, confirmed
- Warnings: 0

**E2E adversarial pass**
- Happy path: `uv run pytest tests/unit/evals/regression/test_cases_mined.py -v` → 13 passed,
  including both mined cases driven end-to-end offline through the real agent loop on scripted
  `FunctionModel`s (PASS).
- Break path 1 (mutation-check on case 22's discriminating power): read
  `test_guessed_file_path_runs_green_offline` — the scripted model reads `README.md` FIRST (no
  guess); ran it isolated (`pytest -k guessed_file_path_runs_green -v`) → 1 passed, metric
  `read_path_exists` scores `1.0`. Confirms the metric genuinely discriminates rather than always
  failing (PASS).
- Break path 2 (malformed/hostile input to the new metrics): read
  `test_tool_args_never_raising_predicate_is_not_a_violation` (a predicate that raises `KeyError`
  scores as no-violation, `1.0`) and `test_answered_without_error_an_infra_failure_is_unscorable_never_a_pass`
  (an `infra_error` forces `scoring_failed=True` so a broken fixture can never grade green) — both
  present and passing in the diff (PASS).
- Break path 3 (does the gate's warning filter over-forgive — boundary on "unclosed"): wrote a
  throwaway scratch test under `evals/regression/` emitting `ResourceWarning: unclosed transport
  <...>` (swallowed, passed) and a second emitting an unclosed-file `ResourceWarning` via GC (still
  raised `PytestUnraisableExceptionWarning` and failed the run) — confirms the regex names the two
  real leak shapes and nothing broader (PASS). Scratch files deleted after the check, not committed.
- Break path 4 (Opik live-state verification, SDK reads only — no paid agent runs): confirmed via
  `opik.Opik().get_trace_content(...)` that all three source traces (`019f5cd5…`, `01a08614…`,
  `01a0861b…`) carry exactly `['regression-case']`; confirmed via
  `client.get_experiment_by_id("01a08f62-4d3e-707b-a2a8-9c2761cf84db").get_experiment_data()` that
  `answered_without_error` and `read_path_exists` feedback + experiment scores are both `1.0`, and
  `max_steps` is `0.736842105` (matches the reported `0.737` shortfall exactly, nothing hidden)
  (PASS).

**Acceptance criteria**
- [ ] [HUMAN] AC1 — Awaiting human verification per spec. Artefacts verified present and clean:
      `evals/regression/mining/2026-09-11.json`, `2026-09-11-errors-unwindowed.json`, `NOTES.md` all
      exist; both JSONs hold only id/thread_id/start_time/git_sha/model/error-type fields, no
      prompts/outputs; `grep -iE 'iusztin|modal\.|/Users/|@'` over all three files returns only one
      benign hit (`decode-bad-request-400@1`, a cohort name, not a payload/secret). Box correctly
      left unchecked.
- [x] PASS — AC2 (3-6 mined case modules, ids `21-`/`22-`, provenance, ONE metric each) — read
      `mined_empty_model_response.py`, `mined_guessed_file_path.py` against the `RegressionCase`
      contract (`evals/regression/case.py`) and the README's mined-case rules: both tag `mined`,
      fill `source_trace_id`/`thread_id`/`fixed_in`, declare exactly one metric, `symptom` names the
      trace's behavior (not `"harness invariant:"`). Confirmed also by
      `test_every_mined_case_carries_its_provenance_and_exactly_one_metric` (passing).
- [x] PASS — AC3 (case zero: reproduces or ships `skip_reason`) — read `mined_bad_request_400.py`:
      declared, `skip_reason` names the 404 Kitaru workspace + the three fixture attempts + when to
      unskip; `loader.runnable_cases()` excludes it and `evals.run.sync` calls `runnable_cases()` too
      (`evals/run.py:497`) so `sync` never registers it. Confirmed by
      `test_case_zero_ships_declared_but_skipped_with_the_reason_in_the_registry` (passing).
- [x] PASS — AC4 (source traces tagged `regression-case` in Opik) — verified live via the Opik SDK
      (evidence above): all three traces carry exactly `['regression-case']`; the tag is named in
      each case docstring and in `NOTES.md`'s "Provenance in Opik" section.
- [x] PASS — AC5 (amended: gate runs end to end, every metric but `max_steps` clears its floor, both
      mined metrics `1.0`, shortfall routed to task 167, no threshold lowered) — verified live via
      the Opik SDK against experiment `01a08f62-4d3e-707b-a2a8-9c2761cf84db`:
      `mean_answered_without_error=1.0`, `mean_read_path_exists=1.0`, `mean_max_steps=0.7368`,
      `mean_g_eval_metric=0.8571` — matches the log's reported numbers exactly, nothing hidden. `git
      diff --stat` confirms `evals/regression/thresholds.py` and all five implicated invented-case
      files (`edit_precision.py`, `todo_planning.py`, `skill_dispatch.py`,
      `destructive_caution.py`, `memory_obedience.py`) are untouched by this diff.
- [x] PASS — AC6 (README "Mined cases" section) — `evals/regression/README.md` lists all three ids
      with symptom/metric/trace/`fixed_in` in a table, matching the log's table.

**Evidence**
```
$ uv run pytest tests/unit/evals/regression tests/unit/evals/harness/test_metrics.py -q
251 passed in 3.85s

$ make pre-commit
2838 passed, 1 skipped in 61.41s

$ git status --short src/
(empty)

# Opik SDK, read-only:
019f5cd5-7cfd-7498-b10e-3d62f6d776af tags= ['regression-case']
01a08614-aaf4-752a-857a-209f2ca411c9 tags= ['regression-case']
01a0861b-799c-7833-b78f-e8595294bc36 tags= ['regression-case']

experiment 01a08f62-4d3e-707b-a2a8-9c2761cf84db experiment_scores (excerpt):
  mean_answered_without_error 1.0
  mean_read_path_exists 1.0
  mean_max_steps 0.736842105
  mean_g_eval_metric 0.857142857
```

**Other issues found**
- None blocking. Task 167 (step-budget recalibration) is well-formed: valid frontmatter, concrete
  ACs, explicit "never lower a threshold" language, and the observed-legs table matches the
  experiment evidence above.

**VERDICT: PASS**

### [PA] 2026-09-11 21:30 — Acceptance Review

**VERDICT: ACCEPT** — feature-level review of evals-v2 (tasks 155–167, PR #68); evidence and the
per-AC-group walk-through are in `tasks/done/167-regression-step-budgets-recalibration.md`'s log.
Hand off to the PR Reviewer.
