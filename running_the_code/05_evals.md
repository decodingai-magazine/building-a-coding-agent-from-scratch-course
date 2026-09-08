# Evaluating decode

Tracing ([ADR-0014](../docs/adr/0014-opik-observability.md)) can't answer *"did this change make the agent better or worse?"*. The **eval suite** ([ADR-0017](../docs/adr/0017-decode-eval-suite.md)) does: four tracks over one shared [Opik](https://www.comet.com/site/?utm_source=workshop&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course) harness, in top-level [`evals/`](../evals/) (course material, never shipped in the wheel), reading config from the same `Settings` as decode.

| Track | Answers | Graded by | Run |
|---|---|---|---|
| **Demo Skills** | "does it *impress*?" | a human watching | `/demo-N-...` in the REPL |
| **Benchmark** | "does it *work*?" | hidden `verify.sh` oracles | `make eval-benchmark` |
| **Regression probes** | "does it work the *way we designed*?" | code metrics + threshold gate | `make eval-regression` |
| **Online eval** | "is *live* traffic still good?" | a judge over emitted traces | `python -m evals online` |

Package overview: [`evals/README.md`](../evals/README.md).

> **Keys & cost.** Benchmark and regression run the real agent and store an Opik experiment: need `OPIK_API_KEY` **plus the active provider's key** (`gemini` → `GEMINI_API_KEY`, `openrouter` → `OPENROUTER_API_KEY`, `modal` → `MODAL_ENDPOINT_URL`). They cost money and are **never** in `make ci` ([ADR-0017 §9](../docs/adr/0017-decode-eval-suite.md)). Without keys every target skips: one line, exit 0. Eval runs log under `EVAL_PROJECT_NAME` (`decode-evals`), apart from the live project.

## 1. Demo Skills — the human-judged showcase

Six [Skills](../docs/glossary.md) under `.decode/skills/demo-N-*/`, triggered by name in the REPL (no Opik, only your provider key):

```bash
decode                          # from the repo root
/demo-1-terminal-arcade
```

| Skill | Shows off |
|---|---|
| `/demo-1-terminal-arcade` | a playable stdlib-`curses` Snake game in one file |
| `/demo-2-bug-hunt` | hunt + fix two seeded bugs until the suite goes green |
| `/demo-3-repo-pulse` | live GitHub API data → a single-file dashboard with charts |
| `/demo-4-review-swarm` | three parallel Explore [Subagents](../docs/glossary.md) → one verdict |
| `/demo-5-sandbox-feature-pr` | "decode improves decode": [Sandbox](../docs/glossary.md) + Hand-back → draft PR |
| `/demo-6-article-kg` | web articles → interactive knowledge graph |

Each `SKILL.md` carries its own instructions.

## 2. Benchmark — `make eval-benchmark`

Each task runs the real agent in a fresh Workspace; a **hidden** `verify.sh` oracle grades PASS/FAIL (the agent can't grep its grader). One run = one Opik experiment under `EVAL_PROJECT_NAME`.

```bash
make eval-benchmark                                   # whole suite, docker sandbox, 1 trial
make eval-benchmark ARGS='--difficulty easy'
make eval-benchmark ARGS='--task 017-flaky-test-hunt'
make eval-benchmark ARGS='--trials 5'                 # 5 runs per task → reliability aggregates
make eval-benchmark ARGS='--sandbox modal'
make eval-benchmark ARGS='--trials 3 --sandbox modal --nb-samples 4'
```

`ARGS=` reaches `python -m evals benchmark` verbatim:

| Flag | Effect |
|---|---|
| `--task <id>` | only that task |
| `--difficulty easy\|medium\|hard` | only that tier |
| `--sandbox docker\|modal` | sandbox rung per run (default `docker`) |
| `--nb-samples <n>` | cap dataset items |
| `--trials <k>` | runs per task (Opik `trial_count`) |

**Aggregates** with `--trials k`: **pass@1**, **pass@k** (≥1 success in k), **pass^k** (all k succeed — the reliability bar), **flakiness rate**, **cost** (success-per-dollar from token usage). Printed as a Rich table, attached to the experiment row, tagged with model, provider, git sha ([ADR-0017 §8](../docs/adr/0017-decode-eval-suite.md)). Task format + oracle-honesty harness: [`evals/benchmark/tasks/README.md`](../evals/benchmark/tasks/README.md).

## 3. Regression probes — `make eval-regression`

A probe checks design intent — right tool, minimal diff, permission gate respected, compaction survived. Probes run host-native (`none` mode, temp dirs), fast enough for a per-branch pre-merge ritual:

```bash
make eval-regression        # == python -m evals sync --no-benchmark --regression && pytest evals/regression/test_thresholds.py
python -m evals regression                          # every probe as one Opik experiment
python -m evals regression --probe smoke-read-tool  # a single probe
```

**Threshold gate.** [`evals/regression/test_thresholds.py`](../evals/regression/test_thresholds.py) is a pytest module outside `testpaths` — plain `pytest` and `make ci` never collect it. Enforces an absolute per-metric floor as the hard gate (tool-discipline ≥ 0.8, judges ≥ 0.7) and a baseline compare as a soft signal (WARNs on per-metric regression vs the previous experiment, never fails). Pointing CI at it later is a one-line workflow change ([ADR-0017 §9](../docs/adr/0017-decode-eval-suite.md)); manual today because it costs money.

**Two regression surfaces** ([ADR-0017 §6](../docs/adr/0017-decode-eval-suite.md)):

- (a) `python -m evals regression` — deterministic **code metrics** over a numeric threshold;
- (b) `python -m evals suite` — an **Opik 2.0 Test Suite** of natural-language assertions (*"the response never invents a file that does not exist"*) checked by an LLM judge, gated on `pass_rate`.

> **Version honesty.** Test Suites is Opik 2.0; this repo pins `opik==1.9.8` (Opik 2.x pulls a `litellm` needing a newer `rustc`). `python -m evals suite` is written against the 2.0 API and guarded: on the pinned Opik it exits with a version-gate message; activates unchanged when the pin lifts. Detail: [`evals/regression/README.md`](../evals/regression/README.md).

## 4. Online eval — `python -m evals online`

Grades [Traces](../docs/glossary.md) decode **already emitted** from real REPL sessions and `decode run`, scored in place in the live Opik project (never `EVAL_PROJECT_NAME`):

```bash
python -m evals online                                          # every thread in the live project
python -m evals online --filter 'start_time > "2026-07-01T00:00:00Z"'
```

Writing the online rule's scoring prompt, and why this track inverts the "keep evals off the live project" rule: online section of [`evals/README.md`](../evals/README.md).

## Config knobs

**Evals** block of [`.env.example`](../.env.example):

- `EVAL_JUDGE_MODEL` — LiteLLM model string for the G-Eval / conversation judges; empty derives from `LLM_PROVIDER`.
- `EVAL_PROJECT_NAME` — Opik project for eval runs (default `decode-evals`), kept apart from the live project.
