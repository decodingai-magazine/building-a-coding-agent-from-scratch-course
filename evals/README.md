# decode eval suite

Course material *about* the agent; the `evals/` package is not included in the wheel (ADR-0017
§1). It contains two tracks: the outcome Benchmark and behavior Regression Cases. Both use
Langfuse datasets and experiments; the runbook is
[`running_the_code/05_evals.md`](../running_the_code/05_evals.md). The task format is
[`benchmark/tasks/README.md`](benchmark/tasks/README.md), and the case contract is
[`regression/README.md`](regression/README.md).

## CLI

Each track is a subcommand of `python -m evals`. Langfuse and the evaluation harness are imported
lazily, so `--help` does not require credentials or network access.

```text
benchmark    Run the outcome benchmark as a Langfuse experiment.
kitaru       Import Langfuse traces as Kitaru sessions.
mine         Find live Langfuse traces worth turning into cases.
regression   Run deterministic behavior cases as a Langfuse experiment.
suite        Run natural-language regression assertions as an experiment.
sync         Upsert the evaluation datasets.
```

## Judge provider

LLM judges use `EVAL_JUDGE_PROVIDER`, independently of the agent's `LLM_PROVIDER`. An empty value
follows the agent provider. `EVAL_JUDGE_MODEL` selects the model on that route.

```bash
EVAL_JUDGE_PROVIDER=gemini make eval-regression-suite
EVAL_JUDGE_PROVIDER=modal make eval-regression-dataset
```

When a judge is routed to a different provider than the agent, configure credentials for both.
Modal judges use decode's Modal endpoint settings. Their scores may not be directly comparable to
scores from a judge that supports token log probabilities.

## Trace mining

`python -m evals mine` reads the live Langfuse project without making inference calls or changing
traces. It groups results by signature and prints trace IDs for investigation. Use `--json` for
uncapped grouped output suitable for authoring a regression case.

```bash
python -m evals mine --preset errors --since 2026-09-04T00:00:00Z
python -m evals mine --preset all --limit 100 --json
```

Presets are `errors` (traces with error observations), `long` (the top decile by token usage in the
fetched window), `denied` (repeated permission-gate denials), and `all`. `--since` must be a
timezone-aware ISO timestamp. `--limit` bounds the traces fetched per preset. Mining requires
`LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`; without them it reports that it skipped.

To turn a trace into a case, inspect it in Langfuse, then add a prompt, fixture, and grading metrics
under `evals/regression/cases/`. Keep the source trace and session IDs in the case provenance.

## Kitaru imports

`python -m evals kitaru import` imports selected Langfuse traces as Kitaru Sessions. Historical
Opik traces are not imported. The standalone parser
[`importers/langfuse_importer.py`](../importers/langfuse_importer.py) is registered by
[`scripts/bootstrap_kitaru.py`](../scripts/bootstrap_kitaru.py).
