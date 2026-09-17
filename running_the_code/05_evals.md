# 05. Evals with Opik

Tracing shows what happened; it cannot say whether a change made the agent better or worse. The eval suite in [`evals/`](../evals/) does, on [Opik](https://www.comet.com/site/?utm_source=workshop&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course), with two tracks:

| Track            | Answers                           | Graded by                                                              | Run                   |
| ---------------- | --------------------------------- | ---------------------------------------------------------------------- | --------------------- |
| Benchmark        | does it work?                     | Terminal-Bench-style tasks run through `decode run`, a hidden test script writes `reward.txt` | `make eval-benchmark` |
| Regression Cases | does it work the way we designed? | deterministic code metrics + a threshold gate (`dataset`), or an LLM judge over English assertions (`suite`) | `make eval-regression-dataset` / `make eval-regression-suite` |

## 1. Setup

1. **decode installed and a provider key in `.env`** — [01 §1](01_install_and_usage.md#1-install) and [01 §2](01_install_and_usage.md#2-add-a-key) (`GEMINI_API_KEY` by default; or `OPENROUTER_API_KEY`). To run the agent on your own Modal endpoint instead, finish [02](02_modal_endpoints.md) first and set `LLM_PROVIDER=modal` + `MODAL_ENDPOINT_URL` as in [02 §4](02_modal_endpoints.md#4-point-decode-at-the-endpoint).
2. **`OPIK_API_KEY` in `.env`** — the optional tracing step at the end of [01 §2](01_install_and_usage.md#2-add-a-key) is required here.
3. **A sandbox for the benchmark** — Docker running, per [03 §1](03_sandboxing.md#1-work-on-any-repo-get-a-branch-back) (the default, `--sandbox docker`); or a Modal account, per [03 §6](03_sandboxing.md#6-run-remote-sandboxes-via-modal), for `--sandbox modal`.

Runs cost money and never run in `make ci`. Missing a key prints one line and exits 0. Everything logs to the Opik project `EVAL_PROJECT_NAME` (`decode-evals`), apart from live tracing.

## 2. The CLI

Every command lives under one entrypoint; the `make` targets wrap it with a key preflight:

```
$ uv run python -m evals --help
Usage: python -m evals [OPTIONS] COMMAND [ARGS]...

Commands:
  benchmark   Run the outcome benchmark as one Opik Experiment.
  mine        Mine the LIVE project for regressions worth turning into cases.
  regression  Run the behavior Regression Cases host-native as an Opik experiment.
  suite       Run the Opik Test Suite regression surface — natural-language assertions.
  sync        Upsert the eval tracks' Opik surfaces (datasets + Test Suite).
```

`benchmark`, `regression` and `suite` each upsert what they are about to grade before running, so `sync` is never a required step; it pushes task and case definitions to Opik without running anything.

## 3. Benchmark

Nineteen tasks (7 easy / 6 medium / 6 hard) in the Terminal-Bench layout: `task.toml`, `instruction.md`, an `environment/` seed, a hidden `tests/test.sh`, a `solution/solve.sh` oracle. Format: [`evals/benchmark/tasks/README.md`](../evals/benchmark/tasks/README.md).

One **Trial** = one `decode run` subprocess in a real sandbox. The seed becomes a fresh git repo, the agent works on it, Hand-back pushes its branch, and only then, **host-side**, `tests/test.sh` runs on a pristine clone of that branch and writes one float to `reward.txt`. The agent never sees its grader.

```bash
make eval-benchmark                                       # whole suite, docker sandbox, 1 trial/task
make eval-benchmark ARGS='--difficulty easy'              # easy | medium | hard
make eval-benchmark ARGS='--task 001-find-and-replace --task 002-regex-extraction'
make eval-benchmark ARGS='--trials 3'                     # pass@3, pass^3, flakiness
make eval-benchmark ARGS='--sandbox modal --threads 4'    # remote sandbox, 4 trials at a time
make eval-benchmark ARGS='--job-name nightly-3 --model gemini-3.1-pro-preview'
```

`--trials` = trials per task, `--threads` = trials in flight (default 1 on docker, 4 on modal). `--job-name` names both the Opik experiment and the on-disk run folder (default `bench-<UTC stamp>`). Each run first upserts the selected tasks into the `decode-benchmark` Opik dataset itself; there is no separate sync step to remember.

> ✅ **Working looks like:** a Rich table with one row per task (`n`, `pass@1`, at `k > 1` also `pass@k` / `pass^k` / `flaky`, `~$/trial`, `~s/trial`, `~tok/trial`, `infra`), a section per tier, a total row, then a `spend:` line (wall clock · agent seconds · tokens in/out) and one line naming the experiment and the trial dirs. The `infra` column must be ~0, or the row measures the harness, not the model. In Opik: Experiments → one row over the `decode-benchmark` dataset, tagged with model, provider, git sha, sandbox.

Keyless sanity check, in every `make ci`: the oracle gate proves each task's grader scores `solution/solve.sh` 1 and an untouched seed 0.

### Providers and models

Two knobs, one for the agent and one for the judge:

| Who        | Provider                                                        | Model                                                                          |
| ---------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| the agent  | `LLM_PROVIDER` (`gemini` \| `openrouter` \| `modal`)             | `--model <id>`, or `GEMINI_MODEL` / `OPENROUTER_MODEL` / `MODAL_ENDPOINT_MODEL` |
| the judge  | `EVAL_JUDGE_PROVIDER` (same values; empty follows the agent)     | `EVAL_JUDGE_MODEL` (a LiteLLM string; empty derives it from the provider)      |

The benchmark has no LLM judge (the grader is code), so only the agent knobs apply there. The regression track uses both; when the two providers differ, the preflight asks for both keys.

```bash
# agent on your Modal endpoint (02)
LLM_PROVIDER=modal make eval-benchmark ARGS='--threads 1'

# agent on OpenRouter, a specific model
LLM_PROVIDER=openrouter OPENROUTER_MODEL=qwen/qwen3.6-35b-a3b make eval-benchmark

# cheap gemini judge, whatever the agent runs on
EVAL_JUDGE_PROVIDER=gemini EVAL_JUDGE_MODEL=gemini/gemini-3.8-flash make eval-regression-suite

# all four knobs: Qwen agent on your Modal endpoint, Gemini 3.1 Pro as the judge
LLM_PROVIDER=modal MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8 \
  EVAL_JUDGE_PROVIDER=gemini EVAL_JUDGE_MODEL=gemini/gemini-3.1-pro-preview \
  make eval-regression-suite
```

Judge defaults when `EVAL_JUDGE_MODEL` is empty: `gemini` → `gemini/gemini-3.8-flash` (pinned, so scores stay comparable); `openrouter` → the agent's `OPENROUTER_MODEL`; `modal` → the agent's `MODAL_ENDPOINT_MODEL`, the model judging itself.

`--sandbox modal` is where the agent's **tools** run ([03](03_sandboxing.md)); `LLM_PROVIDER=modal` is which **model** it talks to ([02](02_modal_endpoints.md)). They combine freely. On a single-container endpoint keep it at min 1 container and use `--threads 1`.

⚠️ A `modal` judge runs without `logprobs` and with thinking off (the endpoint forces both), so its scores are not comparable with a gemini judge's. Compare a modal judge only against itself.

### Comparing runs

Every job is one Opik experiment; a comparison is two rows side by side (Experiments → select → Compare). Keep tasks, `--trials` and `--threads` the same and change one variable. The row carries three groups of numbers; you multiply by the price:

| What you want | Experiment scores                                              | Read it as                                                                 |
| ------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------- |
| performance   | `pass_at_1` (and `pass_at_k` / `pass_hat_k` / `flaky_rate`)     | macro-averaged over tasks; `infra_error_rate` must be ~0                   |
| time          | `wall_clock_seconds`, `run_seconds_total`, `run_seconds_mean`  | wall clock = what a warm GPU endpoint was billed for; compare at same `--threads` |
| tokens        | `input_tokens_total`, `output_tokens_total`, `tokens_mean`     | what a per-token provider bills; input and output priced apart             |
| dollars       | `mean_cost_usd`, `success_per_dollar`                          | only when the route reports a price; a self-hosted endpoint shows blank    |

```
Modal:       cost ≈ wall_clock_seconds / 3600 × $/GPU-hour
OpenRouter:  cost ≈ input_tokens_total / 1e6 × $/Mtok_in + output_tokens_total / 1e6 × $/Mtok_out
```

Two experiments the course runs this way — same tasks, same `--trials`, same `--threads`, one variable each.

> ⚠️ **Replace every `MODAL_ENDPOINT_URL` below with your own.** The `p-b-iusztin--…` URLs are the author's endpoints and will not answer your requests. Yours are the URLs `modal deploy` printed when you served the models in [02](02_modal_endpoints.md).

The two sides of an experiment are independent jobs, so run them **in parallel, one per terminal** — each needs its own `--job-name`, and they share nothing but the Opik dataset.

**1. Two models, both on Modal** — does the bigger one earn its GPU? Compare `pass_at_1` (performance) and `wall_clock_seconds × each endpoint's $/GPU-hour` (cost).

Terminal 1, gpt-oss-120b:

```bash
LLM_PROVIDER=modal \
  MODAL_ENDPOINT_URL=https://p-b-iusztin--ep-gpt-oss-120b-server.us-west.modal.direct \
  MODAL_ENDPOINT_MODEL=openai/gpt-oss-120b \
  make eval-benchmark ARGS='--threads 1 --job-name modal-gpt-oss-120b'
```

Terminal 2, Qwen3.6-35B:

```bash
LLM_PROVIDER=modal \
  MODAL_ENDPOINT_URL=https://p-b-iusztin--ep-qwen3-6-35b-a3b-fp8-server.us-west.modal.direct \
  MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8 \
  make eval-benchmark ARGS='--threads 1 --job-name modal-qwen36-35b'
```

**2. One model, two billing models** — GPU-hours on Modal vs tokens on OpenRouter. Compare `wall_clock_seconds × $/GPU-hour` against `input/output_tokens_total × $/Mtok`; `pass_at_1` should match.

Terminal 1, Qwen3.6-35B on your Modal endpoint:

```bash
LLM_PROVIDER=modal \
  MODAL_ENDPOINT_URL=https://p-b-iusztin--ep-qwen3-6-35b-a3b-fp8-server.us-west.modal.direct \
  MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8 \
  make eval-benchmark ARGS='--threads 1 --job-name qwen36-modal'
```

Terminal 2, the same Qwen3.6-35B on OpenRouter:

```bash
LLM_PROVIDER=openrouter \
  OPENROUTER_MODEL=qwen/qwen3.6-35b-a3b \
  make eval-benchmark ARGS='--threads 1 --job-name qwen36-openrouter'
```

Add `--task <id>` (repeatable) or `--difficulty easy` to both sides for a cheaper subset.

Tier ceilings (easy 15 steps / 600 s · medium 25 / 900 s · hard 40 / 1500 s) are calibrated to the open model the course serves on Modal; the gemini route reads higher on the same tasks, so compare runs on the same provider. One trial says nothing about spread; `--trials 3` gives pass@3 (capability), pass^3 (reliability) and the gap between them (flakiness).

### The Trial Dir

Every trial leaves its evidence on disk, even one that blew up:

```
.decode/evals/runs/<job>/<task>__<short-id>/
├── seed/                 # the repo the agent cloned, with its decode/<short-id> branch
├── home/                 # the throwaway Harness Home: sessions, logs, sandbox
├── pristine/             # the clone the grader ran on, tests/ copied in last
├── agent/                # stdout.txt · stderr.txt · summary.json (session_id, exit_reason, tokens…)
├── verifier/             # test-stdout.txt · test-stderr.txt · reward.txt
└── result.json           # reward, status, reason, per-phase timings, git sha, model
```

Start any post-mortem at `result.json`, then `agent/stderr.txt`. Three statuses:

| `result.json` status | When                                                                   | Score                                   |
| -------------------- | ---------------------------------------------------------------------- | --------------------------------------- |
| `agent_ok`           | reward 1                                                               | counts                                  |
| `agent_fail`         | wrong answer, step cap, timeout, or an error exit that wrote a summary | counts as a loss, with a named reason   |
| `infra_error`        | seed/clone failed, sandbox never started, grader crashed, unreadable reward | excluded from the score, shown in `infra` |

## 4. Regression Cases

A case checks design intent: the right tool, a minimal diff, the permission gate respected, compaction survived. Cases run host-native (no docker) in a fresh temp workspace, fast enough for a pre-merge ritual. Twenty-one cases tiered 5 easy / 8 medium / 8 hard, plus mined ones that carry the live trace they came from ([`evals/regression/README.md`](../evals/regression/README.md)).

**Two surfaces, on purpose.** One case definition in `evals/regression/cases/` registers twice in Opik: as a `decode-regression` **dataset** item scored by deterministic metrics, and as an item in the `decode-regression-suite` **Test Suite** whose English `assertion` an LLM judge checks. Numbers catch exact regressions cheaply; assertions catch "the answer got worse in a way no single number captures".

Two commands, one per surface. Each first upserts the cases it is about to grade from disk into Opik, then runs them, then gates. There is no separate sync step: an edited case is what gets graded on the next run.

**Dataset + experiment** — upsert the cases, run every one with deterministic metrics as one Opik experiment, apply the threshold gate:

```bash
make eval-regression-dataset
```

> ✅ **Working looks like:** an `evals sync:` line counting the upserted cases, then one table **per tier**, gated **globally**: tool discipline ≥ 0.8, judges ≥ 0.7. A drop against the previous experiment prints a WARN. The experiment is `decode-regression-gate`.

**Test Suite + validation** — upsert the cases, run every one, an LLM judge scores each case's English `assertion`, gated on pass rate ≥ 0.8. Opik's suite judge takes a bare model name, so it runs on `gemini` or `openrouter`, never on your Modal endpoint — with a Modal-served agent, route the judge explicitly:

```bash
EVAL_JUDGE_PROVIDER=gemini make eval-regression-suite
```

> ✅ **Working looks like:** Opik's suite report box (items passed, pass rate), then one line naming the `decode-regression-suite` version that ran, the pass rate against the 80% bar and the report file under `.decode/evals/suite-reports/`. A modal judge route stops up front with one line telling you to set `EVAL_JUDGE_PROVIDER`.

**Options.** Both take the same knobs: `ARGS='--difficulty easy|medium|hard'` slices to one tier (upserted and gated together, experiment `decode-regression-gate-<tier>`, so a tier's baseline stays its own), and the four provider/model variables from [Providers and models](#providers-and-models) pick the agent and the judge:

```bash
make eval-regression-dataset ARGS='--difficulty hard'
```

```bash
LLM_PROVIDER=modal MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8 \
  EVAL_JUDGE_PROVIDER=gemini EVAL_JUDGE_MODEL=gemini/gemini-3.8-flash \
  make eval-regression-suite ARGS='--difficulty medium'
```

**Mining new cases from live traffic.** `uv run python -m evals mine --preset errors --since <ISO>` (presets `errors` | `long` | `denied` | `all`) searches the live Opik project, clusters hits by signature and prints trace ids. Pick one, read it in Opik, write the case that fails on exactly that behaviour into `evals/regression/cases/` with its `source_trace_id`. From then on the gate owns it. Details: [`evals/README.md`](../evals/README.md).

---

**Next:** [06_evals_replays.md](06_evals_replays.md) — record real sessions and replay them with one change.
