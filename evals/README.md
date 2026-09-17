# decode eval suite

Course material *about* the agent, not part of it — this `evals/` package never ships in the wheel
(ADR-0017 §1, rebuilt by ADR-0022). It carries two tracks — the Benchmark and the Regression Cases —
over the shared Opik harness. The runbook is
[`running_the_code/05_evals.md`](../running_the_code/05_evals.md); the task format is
[`benchmark/tasks/README.md`](benchmark/tasks/README.md) and the case contract
[`regression/README.md`](regression/README.md). This README documents the judge's provider knob and
**Trace Mining** (ADR-0022 §8) — the one command that reads the LIVE project.

## The CLI

Every track is one subcommand of one entrypoint (`evals/run.py`); each subcommand's own `--help`
carries its flags, and Opik is imported lazily so `--help` needs no key and no network:

```
$ uv run python -m evals --help
Usage: python -m evals [OPTIONS] COMMAND [ARGS]...

  decode eval suite — benchmark + regression harness (ADR-0017).

Options:
  --help  Show this message and exit.

Commands:
  benchmark    Run the outcome benchmark as one Opik Experiment (ADR-0022...
  kitaru       Join decode's Opik traces to Kitaru Sessions (ADR-0022 §11).
  mine         Mine the LIVE project for regressions worth turning into...
  regression   Run the behavior Regression Cases host-native as an Opik...
  suite        Run the Opik Test Suite regression surface —...
  sync         Upsert the eval tracks' Opik surfaces (ADR-0022 §6,8).
```

## Choosing the judge's provider

Every LLM judge in the suite — the G-Eval [Judges](../docs/glossary.md) in Regression Cases and the
Test Suite's assertion judge — runs on the provider `EVAL_JUDGE_PROVIDER` names, independently of the
agent's `LLM_PROVIDER`. Empty (the default) follows the agent, which is what every run before this
knob did. `EVAL_JUDGE_MODEL` still picks the model *on* that route.

```bash
EVAL_JUDGE_PROVIDER=gemini  make eval-regression-suite    # modal-served agent, gemini judge
EVAL_JUDGE_PROVIDER=modal   make eval-regression-dataset  # G-Eval judges on your own endpoint, no per-token cost
```

The Test Suite's assertion judge is the one place a `modal` judge cannot go: `opik.run_tests` hands
its `LLMJudge` a bare model name, with nowhere for the endpoint base url + proxy headers to ride, so
`make eval-regression-suite` refuses a modal judge route up front and asks for `EVAL_JUDGE_PROVIDER=gemini`
(or `openrouter`). Left unrouted, Opik would grade on its own default (`gpt-5-nano`, an `OPENAI_API_KEY`
this project never configures) — the suite always passes our judge model explicitly.

When the two providers differ, `make eval-benchmark` / `make eval-regression-dataset` want **both** keys —
one guard serves the benchmark (agent only) and the gate (agent *and* judge), so it asks for
everything the run might call.

**What a `modal` judge gives up.** decode's Modal Auto Endpoint (SGLang + DFLASH speculative
decoding) refuses `return_logprob`, so the judge model hides `logprobs`/`top_logprobs` and G-Eval
takes its non-logprob parse path — it reads the score out of the JSON the judge returns instead of
weighting it by token probabilities. It also switches Qwen's thinking off
(`chat_template_kwargs.enable_thinking=false`), because a thinking judge spends minutes restating
the rubric before the one line G-Eval parses, and runs under a 300 s timeout instead of opik's 60 s
default. Consequence worth remembering: **scores from a logprob judge and a non-logprob judge are
not directly comparable** — a modal judge's numbers are a baseline for *itself*, so don't read a
gemini-judged gate against a modal-judged one and call the delta a regression.

## Mining regressions — `python -m evals mine`

[**Trace Mining**](../docs/glossary.md) is the discovery half of the loop (ADR-0022 §8): it searches
the LIVE project for runs that went wrong, clusters them, and prints trace ids for you to pick from.
It is read-only — it never writes to Opik. A picked trace becomes a
[Regression Case](../docs/glossary.md) by hand, in the format `evals/regression/cases/` uses.

```bash
# What errored recently?
python -m evals mine --preset errors --since 2026-09-04T00:00:00Z

# Everything, as JSON, for case authoring:
python -m evals mine --preset all --limit 100 --json
```

### The three presets

| Preset | What it finds | How |
|---|---|---|
| `errors` | runs that raised | Opik OQL `error_info is_not_empty` |
| `long` | the most expensive runs | top decile of `usage.total_tokens` **within the fetched window**, computed client-side (`ceil(n/10)`, at least one; a run with no recorded usage is never ranked) |
| `denied` | runs the permission gate blocked repeatedly | ≥ 2 gate-denied tool calls, read off the spans |
| `all` | all three, in one pass | one trace can appear under two presets — that is reporting, not duplication |

`--since` takes a **timezone-aware** ISO timestamp (`2026-09-04T00:00:00Z`); a naive one is refused
rather than assumed to be UTC. `--limit` (default 50) is both the fetch ceiling per preset and the
window the client-side presets rank within.

**How a denial is detected.** A gated tool call raises `ApprovalRequired` from inside the tool body,
so pydantic-ai emits an `execute_tool` span marked `pydantic_ai.tool.deferral.name =
"ApprovalRequired"` with no result. An *approved* call then runs for real and emits a second,
completed span for the same tool + arguments; a *denied* one never does. So a denial is a deferred
leg with no completed twin. (The denial text itself never reaches a tool span — it rides the next
model request — which is why this reads the deferral marker instead.)

### What a signature is

A **signature** is the four facts that make two bad runs "the same bug":

```
<preset> | <error type, or the message's first line> | <last tool called> | <metadata.model>
```

Hits are grouped by signature, biggest group first, one Rich table each: the count and the
first/last-seen window in the caption, then up to five trace ids with their `thread_id`,
`metadata.git_sha` and `metadata.model`. Traces recorded before task 156 carry no `git_sha`/`model`
metadata and render as `-`.

```
errors | pydantic_ai.exceptions.UsageLimitExceeded | read | gemini-2.5-flash
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ trace id     ┃ thread id    ┃ started              ┃ git_sha      ┃ model            ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ 01a08d79-…   │ 1e1851f5-…   │ 2026-09-10 22:38:50Z │ -            │ -                │
└──────────────┴──────────────┴──────────────────────┴──────────────┴──────────────────┘
2 trace(s) · first 2026-09-10 22:38:50Z · last 2026-09-11 09:05:00Z
```

### Handing a hit to a case

`--json` emits the same groups — `[{"signature": …, "count": …, "traces": [{"id", "thread_id",
"start_time", "git_sha", "model", "error"}]}]` — **uncapped**, because the next step needs every id,
not the five a table shows. Absent metadata is `null` there, not `"-"`.

Worked example in this repo: the 2026-09-11 session over `decode-prod` —
[`regression/mining/NOTES.md`](regression/mining/NOTES.md) records every signature it found, the two
it turned into cases, the three it deliberately skipped and why, beside the raw `--json` output.

From a picked trace: open it in Opik (its `thread_id` is the decode session id), read what the agent
actually did, then write the case — a prompt, a seeded workspace, and deterministic metrics that
fail on the behaviour you just saw — into `evals/regression/cases/`, the format
[`evals/regression/`](regression/) documents. `--json`'s `signature` is a good `description` line,
and the trace id belongs in the case's provenance so the run it came from stays findable.

**Keys.** `mine` needs **`OPIK_API_KEY` and nothing else** — it makes no inference call, only
reads traces — so it runs the shared eval preflight with `require_provider=False`. Without the key
it **skips friendly** — one line, exit `0`:

```
evals mine: skipped — set OPIK_API_KEY to search live traces.
```
