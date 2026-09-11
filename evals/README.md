# decode eval suite

Course material *about* the agent, not part of it — this `evals/` package never ships in the wheel
(ADR-0017 §1, rebuilt by ADR-0022). It carries four tracks (Demo Skills, Benchmark, Regression Cases,
and the online track) over the shared Opik harness. The four-track map is
[`running_the_code/05_evals.md`](../running_the_code/05_evals.md); the task format is
[`benchmark/tasks/README.md`](benchmark/tasks/README.md) and the case contract
[`regression/README.md`](regression/README.md). This README documents the **online eval** track
(ADR-0017 §10) and **Trace Mining** (ADR-0022 §8) — the two commands that read the LIVE project.

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
  online       Score decode's LIVE REPL threads with one...
  online-rule  Manage the Opik ONLINE RULE that scores live traces as...
  regression   Run the behavior Regression Cases host-native as an Opik...
  suite        Run the Opik Test Suite regression surface —...
  sync         Upsert the eval tracks' Opik surfaces (ADR-0022 §6,8).
```

## Online eval — scoring live REPL traffic

Every other track *drives* the agent and grades the run. The online track is the production-eval
story: it grades the [Traces](../docs/glossary.md) decode **already emitted** from real REPL
sessions and `decode run` invocations (ADR-0014), scored in place in the **live** Opik project
(`settings.opik_project_name` — `decode` / `decode-<env>`), never `EVAL_PROJECT_NAME`.

There are two halves, and they are complementary:

1. an **[Online Rule](../docs/glossary.md)** — a judge Opik runs automatically on new traces as they arrive, created
   once with `python -m evals online-rule create`;
2. a **scripted thread-level pass** — `python -m evals online`, a single
   [conversation Judge](../docs/glossary.md) over recent [Threads](../docs/glossary.md) you run on
   demand from the CLI.

### 1. The Online Rule (`python -m evals online-rule create`)

An [**Online Rule**](../docs/glossary.md) is an LLM-as-judge Opik evaluates on every incoming trace, attaching a
`response_quality` feedback score you can then filter and chart. One command creates it, idempotently:

```bash
# Create it on the LIVE project (settings.opik_project_name), sampling every trace:
python -m evals online-rule create

# Rehearse first — prints the exact create payload, writes nothing:
python -m evals online-rule create --dry-run

# Pick the project / judge model / sampling rate explicitly:
python -m evals online-rule create --project decode-prod --model gemini-2.5-flash --sampling 0.5
```

Run it twice and the second run prints `already exists: <id>` and writes nothing — the rule is
looked up by its exact name (`response_quality`) before anything is created.

**The judge model is Opik's, not LiteLLM's.** The rule runs *server-side*, on a provider your Opik
workspace has configured under **AI Providers**, so the id carries no route prefix: the gemini route
(`gemini/gemini-2.5-flash`, the harness default) becomes `gemini-2.5-flash`. The `openrouter` and
`modal` routes cannot be translated — the command refuses with one line asking for `--model <id>`,
because a rule naming a model the workspace cannot route is created happily and then silently never
scores.

**The scoring prompt** is one constant in [`evals/harness/online_rule.py`](harness/online_rule.py)
(`RESPONSE_QUALITY_PROMPT`), quoted here verbatim — it is phrased *qualitatively*, as qualities to
look for rather than numeric verdicts (a "Score 1.0 if grounded, 0.0 otherwise" instruction collides
with the judge's own 0-10 output scale and produces incoherent scores — the task-114 lesson):

```text
Judge how well the assistant's final answer addresses the user's request, grounded in the
files, tool outputs, and prompt it was given.

A high-scoring answer directly resolves what the user asked, cites only facts present in the
workspace or the prompt, and invents no file, function, or value. A low-scoring answer drifts
off the request, is vague, or asserts things nothing in the trace supports.

The user's request:
{{input}}

The assistant's answer:
{{output}}
```

`{{input}}` and `{{output}}` are mapped to the trace's input and output — the same two fields the
scripted pass below transforms. The rule's output schema is one `response_quality` INTEGER
(0-10), and its trigger scope is `production` (live traffic, not experiment traces).

New turns in the project pick up their `response_quality` score within a moment of arriving; on each
Trace you also see the judge's written reason. At the project level you can then filter
(`feedback_scores.response_quality < 5`) and chart the score over time — which is exactly what
`python -m evals mine --preset low-quality` does from the CLI.

**Fallback (the UI).** If you would rather click it together: in the project's **Online evaluation**
(a.k.a. **Rules / Automations**) tab, create a rule of type **LLM-as-judge**, sampling `1.0`, map
`{{input}}`/`{{output}}` to the trace input/output, paste the prompt above, and add one
`response_quality` INTEGER output. That is the same rule this command writes.

### 2. The scripted thread-level pass (`python -m evals online`)

Where the rule grades one trace at a time as it arrives, the scripted pass grades whole
**conversations**. It runs one conversation-level judge (Opik's `ConversationalCoherenceMetric`,
routed to decode's provider) over the recent Threads in the live project via `evaluate_threads`, and
logs each thread's score back onto that same thread.

```bash
# Score every thread in the live project:
python -m evals online

# Scope to recent threads with an Opik OQL filter:
python -m evals online --filter 'start_time > "2026-07-01T00:00:00Z"'
```

It prints one line per thread — `<thread_id>: conversation_coherence=<score>` — and a total. The
`thread_id` is the decode session id — of a REPL session or of one `decode run` — Opik's conversation
key (ADR-0014).

**Keys.** The pass needs `OPIK_API_KEY` (to reach the threads) and the active provider's judge key
(`GEMINI_API_KEY` by default). Without them it **skips friendly** — it prints which vars to set and
exits `0`, so `--help` and a keyless checkout never error:

```
evals online: skipped — set OPIK_API_KEY, GEMINI_API_KEY to score live threads.
```

**Why the live project, not `EVAL_PROJECT_NAME`.** The benchmark and regression tracks log under
`decode-evals` so they never pollute live tracing (ADR-0017 §9). Online eval inverts that on
purpose: grading real traffic *in place* is the whole point, so its scores attach to the live
threads (`eval_project_name=None`).

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

### The four presets

| Preset | What it finds | How |
|---|---|---|
| `errors` | runs that raised | Opik OQL `error_info is_not_empty` |
| `low-quality` | answers the online rule graded poorly | OQL `feedback_scores.response_quality < 5` (empty until the rule above exists) |
| `long` | the most expensive runs | top decile of `usage.total_tokens` **within the fetched window**, computed client-side (`ceil(n/10)`, at least one; a run with no recorded usage is never ranked) |
| `denied` | runs the permission gate blocked repeatedly | ≥ 2 gate-denied tool calls, read off the spans |
| `all` | all four, in one pass | one trace can appear under two presets — that is reporting, not duplication |

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

**Keys.** Both live-project commands need **`OPIK_API_KEY` and nothing else** — neither makes an
inference call (`mine` only reads traces; the online rule's judge runs on Opik's own configured
provider), so they run the shared eval preflight with `require_provider=False`. Without the key they
**skip friendly** — one line, exit `0`:

```
evals mine: skipped — set OPIK_API_KEY to search live traces.
```

On a non-gemini route `online-rule create` still needs an explicit `--model` ("The judge model is
Opik's, not LiteLLM's", step 1): with no inference key involved, the one thing it cannot guess is how
your Opik workspace spells the judge.
