# 0024. Langfuse Cloud tracing and evaluation scores

**Status:** Accepted
**Date:** 2026-11-12

## Context

Runtime traces currently use Opik, which also owns the repository's evaluation datasets,
experiments, regression suite, and trace-mining commands. The requested migration is limited to
runtime tracing and evaluation-result reporting; replacing those Opik workflows is deferred.
Tracing must remain optional, preserve the current agent behavior when disabled, and avoid changing
the process-global OpenTelemetry environment used by other integrations.

## Decision

1. **Langfuse Cloud owns runtime traces.** When both `LANGFUSE_PUBLIC_KEY` and
   `LANGFUSE_SECRET_KEY` are configured, `init_tracing()` instruments pydantic-ai through logfire
   and exports OTLP spans to the configured `LANGFUSE_BASE_URL` (`LANGFUSE_HOST` remains a
   compatibility alias). The exporter uses Langfuse's Basic
   authentication and real-time ingestion header.
2. **Trace grouping follows decode sessions.** REPL turns and headless runs propagate the decode
   session id, trace metadata, input, and output using Langfuse's supported OTEL attributes.
3. **Evaluation orchestration stays on Opik.** Existing datasets, experiments, regression
   workflows, and trace mining are not replaced. After evaluation, numeric scores are additionally
   attached to the corresponding Langfuse sessions when Langfuse credentials are configured.
4. **No credentials means no reporting.** With neither Langfuse key set, tracing remains a no-op
   and evaluation commands report that Langfuse score reporting was skipped. Supplying only one key
   is an explicit configuration error.
5. **Credentials remain in Settings.** The OTLP exporter and score client are configured directly
   from `Settings`; global `OTEL_*` variables are not modified. Logfire's default scrubbing remains
   enabled; only the opaque `session.id` attribute is exempted so Langfuse can group decode sessions.

## Consequences

- Runtime traces and numeric evaluation scores are visible in Langfuse Cloud; existing Opik
  evaluation orchestration and trace-mining commands continue to require `OPIK_API_KEY`.
- Langfuse tracing is activated by both API keys, while eval workflows remain usable without
  Langfuse by explicitly skipping score reporting.
- Trace metadata uses Langfuse's trace metadata convention. Opik trace mining does not automatically
  migrate and remains a separate deferred workflow.
- Langfuse is a runtime dependency; Opik remains a development dependency for the top-level evals
  package.
