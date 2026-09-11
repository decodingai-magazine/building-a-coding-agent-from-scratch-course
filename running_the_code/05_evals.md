# 05 — Evals with Opik

Tracing shows what happened; it cannot say whether a change made the agent better or worse. The eval suite in [`evals/`](../evals/) ([ADR-0017](../docs/adr/0017-decode-eval-suite.md)) does, on one shared [Opik](https://www.comet.com/site/?utm_source=workshop&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course) harness.

Needs `OPIK_API_KEY` ([01 §2](01_install_and_usage.md#2-add-a-key)) plus your provider key; Docker for the benchmark. Runs cost money and are never in `make ci`. Missing a key: one line, exit 0. Eval runs log under `EVAL_PROJECT_NAME` (`decode-evals`), apart from the live project.

| Track | Answers | Graded by | Run |
|---|---|---|---|
| Demo Skills | does it impress? | you | `/demo-N-...` in the REPL |
| Benchmark | does it work? | hidden `tests/test.sh` Verifiers | `make eval-benchmark` |
| Regression probes | does it work the way we designed? | code metrics + threshold gate | `make eval-regression` |
| Online eval | is live traffic still good? | an LLM judge over emitted traces | `python -m evals online` |

## 1. Demo skills

Six skills under `.decode/skills/demo-N-*/`, run as in [01 §4](01_install_and_usage.md#4-try-a-skill), no Opik needed:

| Skill | Shows off |
|---|---|
| `/demo-1-terminal-arcade` | a playable `curses` Snake game in one file |
| `/demo-2-bug-hunt` | find and fix two seeded bugs until the suite is green |
| `/demo-3-repo-pulse` | live GitHub API data → single-file dashboard |
| `/demo-4-review-swarm` | three parallel Explore subagents → one verdict |
| `/demo-5-sandbox-feature-pr` | decode improves decode: sandbox + hand-back → draft PR |
| `/demo-6-article-kg` | web articles → interactive knowledge graph |

## 2. Benchmark

Each task runs the real agent in a fresh Workspace; a hidden `tests/test.sh` Verifier grades a reward in `[0, 1]`. One run = one Opik experiment.

```bash
make eval-benchmark                                   # whole suite, docker sandbox, 1 trial
make eval-benchmark ARGS='--difficulty easy'          # easy | medium | hard
make eval-benchmark ARGS='--task 017-flaky-test-hunt'
make eval-benchmark ARGS='--trials 5'                 # 5 runs per task → pass@1, pass@k, pass^k, flakiness, cost
make eval-benchmark ARGS='--sandbox modal --nb-samples 4'
```

> ✅ A Rich table of results, and an experiment row in Opik tagged with model, provider, git sha.

Task format: [`evals/benchmark/tasks/README.md`](../evals/benchmark/tasks/README.md).

## 3. Regression probes

A probe checks design intent: right tool, minimal diff, permission gate respected, compaction survived. Host-native, fast enough for a pre-merge ritual:

```bash
make eval-regression                                # every probe + the threshold gate
python -m evals regression --probe smoke-read-tool  # one probe
```

> ✅ `evals/regression/test_thresholds.py` passes: tool-discipline ≥ 0.8, judges ≥ 0.7; a regression vs the previous experiment WARNs.

`python -m evals suite` is the Opik 2.0 Test Suite variant (natural-language assertions, LLM-judged); this repo pins `opik==1.9.8`, so it exits with a version-gate message until the pin lifts ([`evals/regression/README.md`](../evals/regression/README.md)).

## 4. Online eval

Grades traces decode already emitted, in place, in the live project:

```bash
python -m evals online
python -m evals online --filter 'start_time > "2026-07-01T00:00:00Z"'
```

Writing the scoring rule: [`evals/README.md`](../evals/README.md).

Judge model: `EVAL_JUDGE_MODEL` (LiteLLM string; empty derives from `LLM_PROVIDER`).

---

**Next:** [06_evals_replays.md](06_evals_replays.md) — record real sessions and replay them with one change, with Kitaru.
