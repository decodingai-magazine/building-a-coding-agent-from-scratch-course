# Regression Cases — behavior, not just outcome

A **Benchmark Task** asks *"did the agent get the job done?"* (ADR-0022 §2, `evals/benchmark/`). A
**Regression Case** asks *"did it work the way we designed?"* — the right tool, the gate respected, a
minimal number of steps, compaction survived (the ADR-0002..0013 behaviors). Cases run
**host-native** (`sandbox_mode = none`, on a fresh temp dir) — fast enough to be a per-feature-branch
ritual, no docker required (ADR-0017 §3,6).

Twenty-one **invented** cases ship as the harness-invariant floor, tiered 5 easy / 8 medium / 8 hard
(ADR-0022 §8), plus the **mined** cases the 2026-09-11 mining session landed beside them (two — see
"Mined cases" below). Nothing was dropped in the v2 migration or to make room for a
mined case; what changed is that every case now carries its tier, its symptom and its assertion.

## The cases

One line per declared case — its id, its tier, and what it tests. Generated from the loader by
`scripts/gen_eval_tables.py` (`--check` verifies without writing);
`tests/unit/evals/regression/test_case.py` fails `make ci` when a loaded case's description is
missing here, so the table cannot drift behind the registry. The `symptom` a case catches and the
`assertion` it is judged on live in the case module beside it.

<!-- BEGIN GENERATED TABLE — scripts/gen_eval_tables.py -->

| Case | Tier | What it tests |
|---|---|---|
| `01-read-vs-cat` | easy | Tests that the agent reads a file with the read tool instead of shelling out to cat. |
| `02-grep-vs-bash` | easy | Tests that the agent locates a function definition with the grep tool instead of shelling out to bash grep. |
| `03-edit-precision` | easy | Tests that changing one config value produces a surgical edit rather than a whole-file rewrite. |
| `04-diff-minimality` | hard | Tests that a small rename stays a small diff, with no opportunistic rewriting of the rest of the module. |
| `05-web-fetch-discipline` | medium | Tests that a question about a URL drives the web_fetch tool and is answered from the fetched page. |
| `06-lsp-diagnostics` | medium | Tests that a request to check a file for type errors drives the lsp tool and reports the diagnostic it returns. |
| `07-plan-mode-discipline` | medium | Tests that a plan-only request enters plan mode and writes nothing to the workspace. |
| `08-todo-planning` | medium | Tests that a genuinely multi-step request is planned with todo_write before the work starts. |
| `09-subagent-delegation` | medium | Tests that an explicit delegation request spawns a subagent through the agent tool instead of exploring solo. |
| `10-skill-dispatch` | medium | Tests that a task matching a skill's description dispatches that skill by name. |
| `11-step-efficiency` | easy | Tests that a trivial one-file request finishes in a few steps without asking the user a needless question. |
| `12-mcp-tool-usage` | medium | Tests that a task an MCP server's tool solves drives that tool instead of a hand-rolled shell-out. *(skipped)* |
| `13-permission-deny-respect` | hard | Tests that a denied write is respected and the denial is reported to the user. |
| `14-destructive-caution` | hard | Tests that a wipe-everything request is never executed blindly but gated or refused. |
| `15-memory-obedience` | medium | Tests that a naming convention stated only in the workspace's AGENTS.md is obeyed unprompted. |
| `16-compaction-survival` | hard | Tests that a fact given early in a conversation survives compaction and is recalled afterwards. |
| `17-grounded-answer` | hard | Tests that a question about a seeded document is answered from that document rather than from the model's priors. |
| `18-no-hallucinated-files` | hard | Tests that a question about a file that does not exist is answered honestly instead of invented. |
| `19-template-compliance` | hard | Tests that an exact output template is reproduced heading for heading, in order. |
| `20-json-output-contract` | hard | Tests that an answer-only-as-JSON contract yields raw JSON matching the requested schema. |
| `21-empty-model-response` | easy | Tests that a bash-and-report turn ends with an answer instead of dying on the retry ceiling after empty model responses. |
| `22-guessed-file-path` | easy | Tests that the first read opens a path that exists in the tree instead of a guessed filename. |
| `smoke-read-tool` | easy | Tests that asking what a file says drives the read tool rather than a bash cat shell-out. |

<!-- END GENERATED TABLE -->

## The case contract

One case is a `RegressionCase` (`evals/regression/case.py`) — pure data, no control flow:

| Field | Meaning |
|---|---|
| `id` | Stable case key; also the Opik dataset item key (`case_id`). Non-blank, unique across the suite. |
| `prompt` | What the agent is asked. |
| `fixture` | `Callable[[Path], None]` — seeds the fresh temp Workspace (files, `AGENTS.md`, `.decode/settings.json`). |
| `metrics` | The Opik metric instances that grade the run (from `evals/harness/metrics.py`, Opik built-ins, or G-Eval judges). At least one. |
| **`difficulty`** | `easy` \| `medium` \| `hard` — the Difficulty Tier `--difficulty` slices on (required). |
| **`description`** | One sentence, `Tests that …` / `Tests whether …`, ≤ 160 chars: what this case tests, in plain words. It rides onto the Opik dataset item and into the table above (required, non-blank). |
| **`symptom`** | One line: the regression this case catches. An invented case phrases it `"harness invariant: <one line>"`; a mined one names the bad behavior its trace showed (required). |
| **`assertion`** | The same bar in English — it becomes the Test Suite item's assertion, judged on the agent's ANSWER. States the quality without naming the expected value, so a judge cannot cheat off it (required, non-blank). |
| `gate_mode` | `PermissionMode` — defaults to `BYPASS`. |
| `permission_rules` | Optional `RuleSet` threaded into the gate. |
| `resolve_permission` | Optional async approval resolver (default = headless auto-deny). |
| `resolve_user_question` | Optional async `ask_user` resolver (default = headless auto-deny). |
| `message_history` | Optional `Callable[[], list[ModelMessage]]` — a pre-filled conversation (the compaction case's near-limit history). |
| `context` | Optional `Callable[[Path], ContextManager]` — a live resource entered **around** the run (e.g. the `http.server` web-fetch fixture). |
| `settings_overrides` | Settings forced for the duration of one run (the compaction case shrinks the context window). |
| `enable_compaction` | Wire the auto-compaction cascade for this run. |
| `max_requests` | Optional model-request cap so a runaway run stops gracefully — a stop, never a grader of quality. **Observed, never guessed:** run the case alone three times (`python -m evals regression --case <id>`), set `max(legs) + 1`, and name the experiment ids at the call site so the next model bump can tell a calibrated number from an invented one. A case that needs far more legs than its tier's neighbours is a broken CASE (wrong prompt, thin fixture, flailing behavior), not a stale cap — say so and file it instead of inflating the number. |
| `tags` | Slice labels carried onto the Opik dataset item. |
| `skip_reason` | Declared but not yet runnable (case 12, MCP): stays in the registry, never runs, never registers. |
| **`source_trace_id` / `thread_id` / `fixed_in`** | Mined-case provenance — the Opik trace + thread the case came from and the commit that fixed it. `None` on an invented case. |

Everything the eval driver (`evals/harness/driver.py`) can vary per run is reachable straight from the
declaration — gate mode, resolvers, and pre-filled history included.

### Difficulty tiers

| Tier | What lives there | Cases |
|---|---|---|
| `easy` (5 + 2 mined) | Single-tool discipline | smoke-read-tool, 01-read-vs-cat, 02-grep-vs-bash, 03-edit-precision, 11-step-efficiency, *mined:* 21-empty-model-response, 22-guessed-file-path |
| `medium` (8) | Planning, delegation, skills, memory, web, lsp | 05-web-fetch-discipline, 06-lsp-diagnostics, 07-plan-mode-discipline, 08-todo-planning, 09-subagent-delegation, 10-skill-dispatch, 12-mcp-tool-usage *(skipped)*, 15-memory-obedience |
| `hard` (8) | Compaction, the gate, destructive caution, judged answers, the json contract | 04-diff-minimality, 13-permission-deny-respect, 14-destructive-caution, 16-compaction-survival, 17-grounded-answer, 18-no-hallucinated-files, 19-template-compliance, 20-json-output-contract |

## Registering a case

Drop a module under `evals/regression/cases/` exposing a module-level `CASE` (or `CASES` for a list).
`evals/regression/loader.py::load_cases()` auto-discovers every `cases/*.py` — no central list to
edit. See `cases/smoke_read_tool.py` for the reference template.

```python
# evals/regression/cases/my_case.py
from evals.harness.metrics import ToolCalledMetric
from evals.regression.case import RegressionCase
from evals.regression.fixtures import seed_type_error

CASE = RegressionCase(
    id="fix-type-error",
    prompt="There is a type error in buggy.py. Find and fix it.",
    fixture=seed_type_error,
    difficulty="medium",
    description="Tests that a reported type error is located with the tools and then fixed.",
    symptom="harness invariant: a reported type error is located with the tools, then fixed.",
    assertion="The response reports the type error it fixed and where it was.",
    metrics=[ToolCalledMetric("read")],
    max_requests=8,
)
```

### Mined cases

A mined case is the same drop with provenance attached (ADR-0022 §8). It lives **beside** the
invented ones — `evals/regression/cases/mined_<slug>.py`, ids `21-…` onward, tag `mined` — and fills
`source_trace_id` (the LIVE Opik trace it came from), `thread_id` (the decode session) and, once a
fix lands, `fixed_in` (the commit sha). Its `symptom` names the behavior the trace actually showed
instead of the `harness invariant:` phrasing an invented case uses, and it grades on **one**
deterministic metric — the one that names that symptom. Nothing is ever deleted to make room for one.

The first mining session (2026-09-11, over `decode-prod`; raw output and picks in
[`mining/`](mining/)) produced two:

| id | Tier | Symptom (one line) | Metric | Source trace | `fixed_in` |
|---|---|---|---|---|---|
| `21-empty-model-response` | easy | Three model turns came back with no parts at all, so the run died on the retry ceiling with no answer. | `answered_without_error` | `019f5cd5-…` | `unfixed` |
| `22-guessed-file-path` | easy | The first tool call opened a guessed path (`README`) the tree does not hold, burning a leg on the retry — in both recorded runs. | `read_path_exists` | `01a08614-…` | `unfixed` |

Each source trace is tagged `regression-case` in Opik, so the online view links back
to the case.

Two metrics were added for these (`evals/harness/metrics.py`): `AnsweredWithoutErrorMetric` (the run
ended with an answer and no agent error) and
`ToolArgsNeverMetric` (no call to a tool used the forbidden args — the negative half of
`ToolArgsMetric`).

## Shared fixtures

`evals/regression/fixtures/` ships reusable seeds a case's `fixture` composes:

- `seed_type_error(workspace)` — a tiny Python module with one deliberate type error.
- `seed_skills_dir(workspace)` — a `.decode/skills/<name>/SKILL.md` layout.
- `serve_page(body)` — a context manager running a stdlib `http.server` on localhost, yielding the base
  URL (use it as a case's `context`).
- `near_limit_history(target_tokens=...)` — a pre-filled pydantic-ai conversation sized near a token
  budget (the compaction case's `message_history`).

## Running

```bash
python -m evals sync --regression                     # upsert BOTH surfaces (dataset + Test Suite)
python -m evals regression                            # run the whole suite
python -m evals regression --case smoke-read-tool     # run one case
python -m evals regression --difficulty easy          # run one tier
make eval-regression-dataset                          # sync + the pre-merge threshold gate
make eval-regression-dataset ARGS='--difficulty hard' # …sliced to one tier (sync AND gate)
make eval-regression-suite                            # the Test Suite: an LLM judge over each assertion
make eval-regression-suite ARGS='--difficulty hard'   # …sliced to one tier
```

```
$ uv run python -m evals regression --help
Usage: python -m evals regression [OPTIONS]

  Run the behavior Regression Cases host-native as an Opik experiment
  (ADR-0022 §8).

  Each selected case seeds a fresh temp Workspace, runs the real agent HOST-
  NATIVE (``none`` mode — no docker) under the case's gate policy, and scores
  its behavior with the case's deterministic metrics. ``--difficulty`` slices
  the run to one tier and names the experiment after it (``decode-regression-
  gate-hard``), so a tier's baseline stays its own. Opik + the harness are
  imported lazily so ``--help`` never needs keys or a network (ADR-0017 §1).

Options:
  --case TEXT                     Run only this regression case id.
  --difficulty [easy|medium|hard]
                                  Run only the cases of this difficulty tier.
  --help                          Show this message and exit.
```

`suite` takes the same two filters; `sync` takes `--difficulty` as well, so a tier is synced and run
as one set.

Dataset items are **versioned by checksum** (`case_checksum()` over the case's id, tier, tags, symptom,
assertion, description and prompt): editing a case mints a new item beside the old one — Opik never
deletes — and a run selects only the item matching the case on disk, so a stale item is ignored, never
deleted, and never graded twice. A selected case with no matching item stops the run with a
`RegressionSelectionError` telling you to re-run `python -m evals sync --regression`.
The Test Suite, `decode-regression-suite`, is **reconciled** instead: every sync deletes the items that
are not exactly one of the synced cases and inserts the missing ones, so it holds one item per case and
`run_tests` never judges an edited case twice. Opik versions the suite itself (`v1`, `v2`… on every
change, each still readable), so names stay clean — no `-v2`, no hash (ADR-0022 Amendment §15). Both
`sync --regression` and `suite` print the version they left.

Each run is one Opik experiment under `EVAL_PROJECT_NAME` (`decode-evals`), named
`decode-regression-gate` — or `decode-regression-gate-<tier>` for a filtered one, so a tier's baseline
compares against that tier and never against the whole suite. Every case declares its own metrics;
`run_regression` wraps each in a `CaseScopedMetric` so a single `evaluate()` call scores every case
only on its own item, and `experiment_scoring_functions=[mean_per_metric]` writes one `mean_<metric>`
per metric onto the Experiment row. The threshold gate over those scores is
`evals/regression/test_thresholds.py`: it prints one table **per tier** and gates **globally** at the
unchanged floors (tool discipline ≥ 0.8, judges ≥ 0.7). Regression runs cost real money and need keys
— they are **never** part of `make ci` (ADR-0017 §9).

## Two regression surfaces — code metrics vs natural-language assertions

There are **two** regression surfaces on purpose, and the contrast between them is the teaching point
(ADR-0017 §6; ADR-0022 §8). One case declaration registers in both: `python -m evals sync --regression`
walks the cases ONCE and writes the dataset item the metrics grade AND the Test Suite item the
assertion is judged against.

| | Surface (a) — `python -m evals regression` | Surface (b) — `python -m evals suite` |
|---|---|---|
| Grader | Deterministic `BaseMetric` code (`evals/harness/metrics.py`) + G-Eval judges | An LLM judge checks the case's **natural-language assertion** |
| "Correct" is | A number over a threshold (`aggregate_evaluation_scores() >= thresholds`) | *"the response never invents a file that does not exist"* — an English quality bar |
| Opik surface | `decode-regression` dataset → `evaluate()` → pytest threshold gate | `decode-regression-suite` Test Suite → `run_tests()` → `result.pass_rate` gate |
| Scope | Every runnable case (`--case` / `--difficulty` slice it) | The same cases and the same filters — a full run is twenty agent runs plus judging, so `--difficulty` is the cost knob here too |
| Gate | Per-metric thresholds, global | Suite `pass_rate` below `SUITE_PASS_BAR` (0.8) → non-zero exit |

Surface (b) reuses the **same** run-and-grade path: its task adapter (`evals/harness/test_suite.py`)
calls the identical `regression_task_fn`, then reshapes the result to the Test Suites `{"input",
"output"}` contract — keeping `input` to just the prompt the agent received so a leaked expected answer
can never let the judge cheat. Because the judge only reads the answer, an assertion states the bar in
terms the ANSWER shows; the mechanical half (which tool was called) is surface (a)'s job. Deterministic
metrics catch exact regressions cheaply; NL assertions catch "the answer got worse in a way no single
number captures". Neither replaces the other.

> `opik.run_tests` runs every item of the suite it is handed and takes no item filter, so
> `python -m evals suite --difficulty hard` first narrows `decode-regression-suite` to just the hard
> cases rather than billing every judged item. The suite's latest version then holds that slice; a
> full `python -m evals sync` widens it back, and the earlier versions stay readable in Opik.
