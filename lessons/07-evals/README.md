# Lesson 7 — Is the agent good? The eval stack

The suite is green — 1,800+ tests — and it still can't tell you the agent got
worse. Outcome benchmarks, regression probes, LLM-as-judge, and online evals
over live traffic.

## Run it

```bash
./lessons/07-evals/run.sh   # needs OPIK_API_KEY + the provider key; costs money
```

The two offline tracks: the outcome **benchmark** (real agent, fresh isolated
Workspace per task, hidden `verify.sh` oracle grades PASS/FAIL) and the
**regression probes** (code metrics + a threshold gate on *how* it works).
Without keys, each target skips with one friendly line — run it anyway and
observe that; skip-friendly is a designed behavior, not an accident.

## Playbook (interactive)

1. **The human-judged track.** In the REPL: `/demo-2-bug-hunt` — the agent
   hunts two seeded bugs until the suite goes green; you are the judge.
2. **Reliability, not luck.** `make eval-benchmark ARGS='--trials 5'` — pass@1
   vs pass@k vs pass^k, flakiness rate, success-per-dollar.
3. **Online evals.** `python -m evals online` — an LLM judge over traces decode
   *already emitted* from real sessions. Open the Opik project and read a trace
   span by span first.

## Deep dives

- `evals/` · `src/decode/observability/`
- [ADR-0014 — Opik observability](../../docs/adr/0014-opik-observability.md)
- [ADR-0017 — decode eval suite](../../docs/adr/0017-decode-eval-suite.md)
- [getting_started/evals.md](../../getting_started/evals.md) — the one-stop map

## Background reading

The 2026 AI Evals series maps almost one-to-one onto this lesson:

| Article | Why read it here |
|---|---|
| [The Ultimate Prompt Monitoring Pipeline](https://www.decodingai.com/p/the-ultimate-prompt-monitoring-pipeline) | Opik tracing plus online eval of sampled production traffic with LLM judges — no ground truth needed. |
| [LLMOps for Production Agentic RAG](https://www.decodingai.com/p/llmops-for-production-agentic-rag) | Opik prompt monitoring and built-in + custom judge metrics applied to a ReAct agent. |
| [Observability for RAG Agents](https://www.decodingai.com/p/observability-for-rag-agents) | Four observability pillars (monitoring, versioning, LLM-as-judge eval, feedback), online and offline. |
| [The Mirage of Generic AI Metrics](https://www.decodingai.com/p/the-mirage-of-generic-ai-metrics) | Why off-the-shelf metrics lie — build application-centric metrics from error analysis. |
| [The 5-Star Lie: You Are Doing AI Evals Wrong](https://www.decodingai.com/p/the-5-star-lie-you-are-doing-ai-evals) | Binary pass/fail tied to failure modes beats Likert scales — core judge-design principle. |
| [The Top 11 Ways to Easily Improve Your AI Applications](https://www.decodingai.com/p/the-top-11-ways-to-easily-improve) | The most common eval mistakes teams make — a data-driven approach to improving AI systems. |
| [Escaping POC Purgatory: Evaluation-Driven Development](https://www.decodingai.com/p/escaping-poc-purgatory-evaluation) | The EDD mindset: evals as the driving force of the AI SDLC, not an afterthought. |
| [Stop Launching AI Apps Without This Framework](https://www.decodingai.com/p/stop-launching-ai-apps-without-this) | Minimum Viable Evaluation before launch: synthetic queries, failure analysis, LLM-as-judge harness. |
| [Behind the Scenes of AI Observability in Production](https://www.decodingai.com/p/behind-the-scenes-of-ai-observability) | Real production observability with Opik: custom criteria from traces, annotation, continuous feedback loops. |
| [Integrating AI Evals Into Your AI App](https://www.decodingai.com/p/integrating-ai-evals-into-your-ai-app) | The three eval scenarios — dev optimization, CI regression gates, production monitoring — mirroring benchmark vs regression here. |
| [No Evals Dataset? Here's How to Build One from Scratch](https://www.decodingai.com/p/build-an-ai-evals-dataset-with-error-analysis) | Bootstrap an eval dataset from 20–50 real traces via binary labels and error clustering. |
| [Generate Synthetic Datasets for AI Evals](https://www.decodingai.com/p/generate-synthetic-datasets-for-ai-evals) | Dimension-based synthetic input generation (generate inputs, never fake outputs) for eval coverage. |
| [How to Design Evaluators That Catch What Actually Breaks](https://www.decodingai.com/p/how-to-design-ai-evaluators-that-catch-failures) | Code-based checks vs LLM judges with rubrics, incl. patterns for agentic workflows. |
| [How to Validate Your LLM Judge Against Experts](https://www.decodingai.com/p/how-to-evaluate-the-evaluator-validate-llm-judge) | Aligning LLM judges with human judgment via classification metrics and locked holdout sets. |
| [How Evaluation-Driven Development (EDD) Works](https://www.decodingai.com/p/how-evaluation-driven-development-works) | Every agent change as a before/after offline experiment — the regression-gate workflow in practice. |
