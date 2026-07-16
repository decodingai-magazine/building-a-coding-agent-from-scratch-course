# decode eval suite

Course material *about* the agent, not part of it — this `evals/` package never ships in the wheel
(ADR-0017 §1). It carries four tracks (Demo Skills, Benchmark, Regression probes, and the online
track) over the shared Opik harness. The four-track map is [`getting_started/evals.md`](../getting_started/evals.md); this
README documents the **online eval** track (ADR-0017 §10, task 117).

## Online eval — scoring live REPL traffic

Every other track *drives* the agent and grades the run. The online track is the production-eval
story: it grades the [Traces](../docs/glossary.md) decode **already emitted** from real REPL
sessions and `decode run` invocations (ADR-0014), scored in place in the **live** Opik project
(`settings.opik_project_name` — `decode` / `decode-<env>`), never `EVAL_PROJECT_NAME`.

There are two halves, and they are complementary:

1. an **Opik online rule** — a judge Opik runs automatically on new traces as they arrive (set up
   once, in the Opik project UI);
2. a **scripted thread-level pass** — `python -m evals online`, a single
   [conversation Judge](../docs/glossary.md) over recent [Threads](../docs/glossary.md) you run on
   demand from the CLI.

### 1. The online rule (set up once, in the Opik UI)

An online rule is an LLM-as-judge Opik evaluates on every incoming trace, attaching a feedback score
you can then filter and chart. Set up **one** response-quality / groundedness rule:

1. **Pick the project.** Open the Opik project decode traces land in — `decode` locally, or
   `decode-<env>` for a remote `DECODE_ENV` (ADR-0014 / ADR-0015). This is the value
   `python -m evals online` prints as the live project, so run it once (below) to confirm the name.
2. **Add an online evaluation rule.** In the project's **Online evaluation** (a.k.a. **Rules /
   Automations**) tab, create a new rule of type **LLM-as-judge**.
3. **Scope it.** Sampling rate `1.0` while you are demoing (score every trace); a filter is optional
   — leave it empty to judge every turn, or filter to a `name` / `tag` if the project is busy.
4. **Choose the judge model.** Match decode's own provider so scores stay comparable to the scripted
   pass — e.g. `gemini/gemini-2.5-flash` (the harness default; the same string
   `EVAL_JUDGE_MODEL` / `judge_model()` resolves).
5. **Map the trace variables.** Point the rule's `{{input}}` at the trace input and `{{output}}` at
   the trace output — the same two fields the scripted transforms read.
6. **Write the scoring prompt — QUALITATIVELY.** Score a `response_quality` (0–10) feedback score
   from criteria phrased as *qualities to look for*, never as numeric verdicts:

   > Judge how well the assistant's final answer addresses the user's request, grounded in the
   > files, tool outputs, and prompt it was given.
   >
   > A high-scoring answer directly resolves what the user asked, cites only facts present in the
   > workspace or the prompt, and invents no file, function, or value. A low-scoring answer drifts
   > off the request, is vague, or asserts things nothing in the trace supports.

   **Do not** write "Score 1.0 if grounded, 0.0 otherwise." A binary/`1.0`/`0.0` instruction
   collides with the judge's own 0–10 output scale and produces incoherent scores (task-114 lesson).
   State the *qualities*; let the judge place them on its scale.
7. **Save, then watch the traces.** New turns in the project pick up a `response_quality` feedback
   score within a moment of arriving. On each Trace you'll see the score plus the judge's written
   reason; at the project level you can filter (`feedback_scores.response_quality < 5`) and chart the
   score over time — a regression in live answer quality becomes visible without any run.

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
`thread_id` is the session id (REPL) or Kitaru `exec_id` (`decode run`), Opik's conversation key
(ADR-0014).

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
