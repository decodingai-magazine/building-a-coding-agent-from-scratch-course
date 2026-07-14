# Evaluating decode

decode has tracing ([ADR-0014](adr/0014-opik-observability.md)) but tracing alone can't answer *"did
this change make the agent better or worse?"*. The **eval suite** ([ADR-0017](adr/0017-decode-eval-suite.md))
does, in four tracks over one shared Opik harness. It lives in top-level [`evals/`](../evals/) —
course material *about* the agent, never shipped in the wheel — and reads its config off the same
`Settings` surface as decode itself.

This page is the one-stop map: what each track answers, and the exact command to run it. Each track's
own README goes deeper; this page links, it doesn't duplicate.

| Track | Answers | How it's graded | Run it with |
|---|---|---|---|
| **Demo Skills** | "does it *impress*?" | a human watching | `/demo-N-...` in the REPL |
| **Benchmark** | "does it *work*?" | hidden `verify.sh` oracles | `make eval-benchmark` |
| **Regression probes** | "does it work the *way we designed*?" | code metrics + a threshold gate | `make eval-regression` |
| **Online eval** | "is *live* traffic still good?" | a judge over already-emitted traces | `python -m evals online` |

Demos are for a person; the benchmark proves outcomes; the probes prove *behavior* (right tool,
minimal diff, gate respected — the ADR-0002..0013 designs); online eval grades production traffic in
place. The `evals/` package overview is [`evals/README.md`](../evals/README.md).

> **Keys & cost.** The benchmark and regression tracks run the real agent and store an Opik
> experiment, so they need `OPIK_API_KEY` **plus the active provider's key** (`gemini` →
> `GEMINI_API_KEY`, `openrouter` → `OPENROUTER_API_KEY`, `modal` → `MODAL_ENDPOINT_URL`). They cost
> real money and are **never** part of `make ci` — the cadence is deliberately manual
> ([ADR-0017 §9](adr/0017-decode-eval-suite.md)). Without the keys every target **skips friendly**:
> one line naming what to set, exit 0, no traceback. Eval runs log under `EVAL_PROJECT_NAME`
> (`decode-evals`) so they never pollute the live-REPL tracing project.

## 1. Demo Skills — the human-judged showcase

Seven [Skills](glossary.md) under `.decode/skills/demo-N-*/`, each a scripted showcase you trigger by
name in the REPL (no Opik, no keys beyond your provider — a person is the judge):

```bash
decode                          # start the REPL in the repo root
/demo-1-implement-substack-summarizer   # then just type the skill name
```

| Skill | What it shows off |
|---|---|
| `/demo-1-implement-substack-summarizer` | build a Substack summarizer end to end |
| `/demo-2-bug-hunt` | hunt + fix two seeded bugs until the suite goes green |
| `/demo-3-terminal-arcade` | a playable stdlib-`curses` Snake game in one file |
| `/demo-4-data-detective` | clean a messy CSV, analyse it, write a report with charts |
| `/demo-5-review-swarm` | fan out three parallel Explore [Subagents](glossary.md) into one verdict |
| `/demo-6-sandbox-feature-pr` | the meta "decode improves decode" [Sandbox](glossary.md) + Hand-back → draft PR flow |
| `/demo-7-todoist-app` | a single-file vanilla-JS todo app, opened in the browser |

Each `SKILL.md` carries its own instructions; run one, watch the transcript, judge it yourself.

## 2. Benchmark — `make eval-benchmark`

The outcome benchmark: each task runs the real agent in a fresh isolated Workspace, then a **hidden**
`verify.sh` oracle grades PASS/FAIL (the agent can never grep its own grader). One `make eval-benchmark`
run is one Opik experiment under `EVAL_PROJECT_NAME`.

```bash
make eval-benchmark                                   # the whole suite, docker sandbox, 1 trial
make eval-benchmark ARGS='--difficulty easy'          # one difficulty tier
make eval-benchmark ARGS='--task 017-flaky-test-hunt' # a single task
make eval-benchmark ARGS='--trials 5'                 # 5 runs per task → reliability aggregates
make eval-benchmark ARGS='--sandbox modal'            # execute each run on the remote modal rung
make eval-benchmark ARGS='--trials 3 --sandbox modal --nb-samples 4'
```

Flags reach `python -m evals benchmark` verbatim through `ARGS=`:

| Flag | Effect |
|---|---|
| `--task <id>` | run only that benchmark task |
| `--difficulty easy\|medium\|hard` | run only that tier |
| `--sandbox docker\|modal` | which sandbox rung each run executes in (default `docker`) |
| `--nb-samples <n>` | cap the number of dataset items sampled |
| `--trials <k>` | runs per task (Opik `trial_count`) — drives the reliability aggregates |

**Trials + aggregates.** With `--trials k` the harness computes, as pure post-hoc functions over the
run's results, **pass@1** (single-shot success), **pass@k** (succeeds at least once in k), **pass^k**
(succeeds *every* one of k — the reliability bar), a **flakiness rate**, and **cost** figures
(success-per-dollar from recorded token usage). They print as a Rich summary table and attach to the
experiment row, tagged with the agent model, provider, and git sha ([ADR-0017 §8](adr/0017-decode-eval-suite.md)).
The task-folder format and the oracle-honesty harness are [`evals/benchmark/tasks/README.md`](../evals/benchmark/tasks/README.md).

## 3. Regression probes — `make eval-regression`

A probe asks *"did it work the way we designed?"* — the right tool, a minimal diff, the permission
gate respected, compaction survived. Probes run **host-native** (`none` mode, temp dirs — no docker),
so the suite is fast enough to be a **per-feature-branch pre-merge ritual**. The Makefile target is
exactly that ritual — sync the probe dataset, then run the threshold gate:

```bash
make eval-regression        # == python -m evals sync --regression && pytest evals/regression/test_thresholds.py
```

You can also drive the pieces directly (both need the keys above):

```bash
python -m evals regression                          # run every probe as one Opik experiment
python -m evals regression --probe smoke-read-tool  # run a single probe
```

**The threshold gate.** [`evals/regression/test_thresholds.py`](../evals/regression/test_thresholds.py)
is a pytest module kept **outside** `testpaths` on purpose — plain `pytest` and `make ci` never collect
it. It runs the probe suite once and enforces two things: an **absolute per-metric floor** as the *hard*
gate (tool-discipline ≥ 0.8, judges ≥ 0.7 — any metric below fails the run) and a **baseline compare**
as a *soft* signal (fetches the previous experiment by name and WARNs on per-metric regressions, never
fails — usable on day one with no baseline). It's a normal pytest file, so **pointing CI at it later is
a one-line workflow change** ([ADR-0017 §9](adr/0017-decode-eval-suite.md)); today it stays manual
because it costs money.

**Two regression surfaces, on purpose.** The contrast *is* the teaching point
([ADR-0017 §6](adr/0017-decode-eval-suite.md)):

- surface (a) — `python -m evals regression` — deterministic **code metrics** over a numeric threshold;
- surface (b) — `python -m evals suite` — an **Opik 2.0 Test Suite** of natural-language assertions
  (*"the response never invents a file that does not exist"*) an LLM judge checks, gated on `pass_rate`.

Deterministic numbers catch exact regressions cheaply; NL assertions catch "the answer got worse in a
way no single number captures". Neither replaces the other.

> **Version honesty.** The Test Suites API is **Opik 2.0**, but this repo is pinned to `opik==1.9.8`
> (Opik 2.x pulls a `litellm` whose Rust bridge needs a newer `rustc` than the build host has). So
> `python -m evals suite` is written against the documented 2.0 API and **guarded**: on the pinned
> Opik it exits with a clear version-gate message, and the surface activates unchanged the moment the
> `opik` pin is lifted. Full detail: [`evals/regression/README.md`](../evals/regression/README.md).

## 4. Online eval — `python -m evals online`

The production-eval story: every other track *drives* the agent, but online eval grades the
[Traces](glossary.md) decode **already emitted** from real REPL sessions and `decode run` invocations,
scored **in place** in the live Opik project (never `EVAL_PROJECT_NAME` — grading real traffic where it
lands is the whole point). Two complementary halves:

1. an **Opik online rule** — an LLM-as-judge Opik runs automatically on each new trace (set up once in
   the Opik UI);
2. a **scripted thread-level pass** — one conversation judge over recent [Threads](glossary.md), run on
   demand:

```bash
python -m evals online                                          # score every thread in the live project
python -m evals online --filter 'start_time > "2026-07-01T00:00:00Z"'   # scope to recent threads
```

It needs `OPIK_API_KEY` + the active provider's key; without them it **skips friendly** (prints what to
set, exits 0). The full walkthrough — writing the online rule's scoring prompt *qualitatively*, and why
this track inverts the "keep evals off the live project" rule — is the online section of
[`evals/README.md`](../evals/README.md).

## Config knobs

Both eval env vars live in the **Evals** block of [`.env.example`](../.env.example):

- `EVAL_JUDGE_MODEL` — the LiteLLM model string for the G-Eval / conversation judges; empty derives it
  from `LLM_PROVIDER` (so judges follow decode's own provider).
- `EVAL_PROJECT_NAME` — the Opik project eval runs log under (default `decode-evals`), kept apart from
  the live-REPL project so eval and production traces never mix.
