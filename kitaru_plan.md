# Kitaru Migration Plan — Rescope to Replay-Based Evals

Status: analysis complete, not yet implemented. Branch: `feat/kitaru-migration`.
Source: full read of https://docs.zenml.io/llms.txt (Kitaru section) on 2026-08-21 + codebase usage map.

## 1. What happened upstream

Kitaru pivoted from a **durable execution engine** (ZenML-based flows, checkpoints, waits) to a
**replay-based eval framework**: record agent runs as *sessions* (every LLM call + tool call as
nodes), then observe → judge (investigations/annotations) → define (evaluators + frozen cohorts)
→ replay with overrides → compare (experiments). Architecture: one FastAPI + Postgres server;
**workers** claim tasks and run them in *your* environment ("Nothing in Kitaru executes on the
server"). Managed instances exist: this project uses the ZenML-hosted workspace
`https://f5ee9622-kitaru.cloudinfra.zenml.io` — no server to run ourselves; workers still run
locally/CI. Packages: `kitaru[cli,worker]` (+ `[mcp]`) and separate `kitaru-pydantic-ai`.

## 2. Old vs new — kept / changed / gone

| Old feature (0.18, what decode uses) | New status |
|---|---|
| `KitaruAgent` (PydanticAI adapter) | **Kept, repurposed.** `from kitaru_pydantic_ai import KitaruAgent` — transparent recording wrapper (`run`/`run_sync`/`iter` unchanged). Records session nodes; no checkpoints. Recording is in-band HTTP: server unreachable ⇒ run raises before agent executes. |
| Replay | **Kept, transformed.** Session replay **from the top only** — no `--from` checkpoint anchors, no mid-run forks. Overrides: `model`, `system_prompt`, `prompt`, `model_params`. Code change = register a new agent version. Tool policies (`history` / `static` / `passthrough` / `llm`) replace per-checkpoint cache config. Three-runs discipline survives: observed / reproduced (unchanged replay) / forked. |
| Secrets | **Kept, repurposed.** Encrypted named bundles on server; attached to agent versions (`--secret-id`), injected into replay subprocess env by workers. SDK: `client.secrets.create(...)`. Old `get_secret()` helper import — unconfirmed, likely gone. |
| Local stack | **Changed.** `kitaru login --local` provisions dockerized server + Postgres on `localhost:8000`. No offline mode for recording. |
| CLI | **Replaced.** `kitaru executions *` gone. New: `agent register`, `agent version register`, `session list/import/evaluate`, `cohort create`, `replay create`, `experiment create` / `experiment run start`, `worker start/list/get`, `evaluator scaffold/test/register`, `login`, `job watch`, `status`. |
| MCP + skills | **New surface.** `uv add "kitaru[mcp]"`; `kitaru-mcp` server in `.mcp.json` with `--mode read-only|standard|destructive`; skills via `npx skills add zenml-io/kitaru-skills` (kitaru-investigation, kitaru-replay-experiment, kitaru-guided-tour). Separate process ⇒ ADR-0009 mcp-extra conflict moot. |
| `@flow` / `@checkpoint` / `save` / artifacts / `current_execution_id` | **GONE.** |
| Crash recovery / resume / durable execution | **GONE.** Agent crash mid-run = full re-run from top. Only infra-level durability remains: worker heartbeats + task requeue, idempotency keys on API requests, Postgres persistence, immutable cohort/agent/evaluator versions. |
| `wait` / `wait_for_input` / HITL / durable `sleep` | **GONE.** No wait primitive anywhere. |
| ZenML stacks / Modal orchestrator / `ImageSettings` / flow containers | **GONE.** Workers replace orchestrators. |
| Checkpoint overrides, `--args`, `KitaruDivergenceError` family | **GONE.** |
| New, no old equivalent | Evaluators (versioned, typed verdicts; 9 built-ins: cost/latency/tool patterns), cohorts, experiments, investigations/annotations, trace importers (Langfuse, LangSmith, Braintrust, **Logfire**, Phoenix, JSONL, custom), workers, KITKEY auth, agent versions. |

## 3. Target architecture — one instrumentation, two consumers

Rescope Kitaru purely to eval replays. Opik stays the observability tool, fed via OTLP from
Pydantic Logfire instrumentation. Kitaru is fed **offline** via its Logfire importer.

```
decode (pydantic-ai agent)
   │  logfire.configure(send_to_logfire=False) + logfire.instrument_pydantic_ai()
   │  → OTel spans, gen_ai semconv, + session.id attribute = decode conversation id
   │
   ├─ SpanProcessor A: OTLP/HTTP exporter → Opik            (live observability)
   │     OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = <opik>/api/v1/private/otel/v1/traces
   │     headers: Authorization=<api-key>, projectName=<project>, Comet-Workspace=<ws>
   │     (HTTP transport only — no gRPC)
   │
   └─ SpanProcessor B: JSONL file exporter → .decode/traces/*.jsonl   (Kitaru feed)
         rows: trace_id, span_id, parent_span_id, span_name, timestamps,
               attributes (gen_ai.* conventions)

operator side (batch, never inside the agent process):
   kitaru login https://f5ee9622-kitaru.cloudinfra.zenml.io   # managed workspace
   kitaru agent register decode --command "decode run ..." [--secret-id <llm-keys>]
   # (reuse an existing `decode` agent registration/sessions if the workspace already has one)
   kitaru session import .decode/traces/xxx.jsonl \
       --importer kitaru/logfire@latest --agent decode@latest \
       --media-type application/x-ndjson --tag baseline --wait
   # investigations → cohorts → evaluators → experiments
   kitaru experiment run start <exp> --cohort-version <id> --agent decode@<v> \
       --evaluate-baselines --wait          # nonzero exit ⇒ CI gate
   kitaru worker start [--job-id <id> --timeout ...]   # laptop or CI, drain-and-exit
```

Why this shape:
- **Logfire importer fits exactly**: JSONL rows with `trace_id`/`span_id` + gen_ai attributes;
  detects PydanticAI from scope names / `gen_ai.agent.name`; preserves inputs/outputs, model,
  tokens, cost, timings. Multi-turn grouping via `attributes.session.id` / `session_id` /
  `conversation_id` / `gen_ai.conversation.id` (override with `--join-on` JSON Pointer). Stamp
  decode's conversation id ⇒ a whole REPL session imports as one Kitaru session. Dedup on
  re-import (`imported_from` + `external_id`) makes nightly imports safe. 50 MiB/file cap — rotate.
- **Opik officially ingests OTLP**; its pydantic-ai integration is logfire pointed at Opik.
- **Fully decouples Kitaru from runtime**: no in-band recording, server down ⇒ nothing breaks.
  Invariant "decode never imports kitaru" now holds in EVERY mode, not just `local`.

### Trace source — DECIDED (user, 2026-08-21): Opik stays the log store, hybrid feed
- **Opik remains the permanent trace store** (logfire OTLP exporter unchanged).
- **Backfill = ONE structure**: custom importer `opik@1` (registered, provider `opik`,
  `importers/opik_importer.py`) over the Opik REST export
  `{workspace, project, traces:[{trace, spans}]}`. 30-day window imported 2026-08-21:
  66 traces → 38 sessions, tag `opik-backfill-30d` (smoke tag to exclude:
  `kitaru-importer-smoke:opik-fixture-001`). Payloads >50 MiB split by thread group.
  Watch Opik 429 rate limits on span export (backoff + resume).
- **New runs**: ALSO wrap the pydantic-ai agent with `kitaru_pydantic_ai.KitaruAgent`
  (adapter) so fresh sessions record directly into Kitaru — richer replay fidelity
  (tool cache keys). Task: wire behind `KITARU_API_URL` presence in `decode run`.
- Historical context below kept for reference.

### Trace source — two supported paths (onboarding decides)
Trace provider hint is **Logfire**; verify, don't assume:
- **Path A — Logfire cloud as trace store:** `logfire.configure(send_to_logfire=True)` (token) in
  addition to the Opik OTLP processor; export the desired window (e.g. last 30 days) via Logfire's
  query API (NDJSON) and feed `kitaru session import --importer kitaru/logfire@latest`.
  Kitaru credentials do NOT grant Logfire access — a Logfire read token is a separate prerequisite.
- **Path B — local JSONL span exporter (no Logfire cloud):** SpanProcessor B above writes importer-
  compatible records under `.decode/traces/`; no third-party account, but no historical backfill —
  only traces captured after the exporter ships.
Smallest supported path wins: if a Logfire export (or read token) already exists, import it;
otherwise Path B going forward.

### Division of labor
- **Logfire SDK** — instrumentation (+ optionally Logfire cloud as trace store, Path A).
- **Opik** — live tracing, cost/latency dashboards, LLM-judge benchmark (`make eval-benchmark`
  stays on the Opik SDK in `evals/`).
- **Kitaru** — replay evals: sessions, investigations, cohorts, evaluators, experiments.
  `make eval-regression` migrates to a Kitaru experiment gate.

### Adapter vs importer decision
Importer path (above) is the default: simplest, fully decoupled. Trade-offs: imported sessions
are `source_completeness: query-dependent`, and replay requires registering the agent version
whose code produced the records. The `kitaru-pydantic-ai` adapter gives richer replay-grade
sessions (per-tool cache keys for the `history` tool policy — matters because decode's bash is
side-effectful) but reintroduces an in-band server dependency. **Start importer-only; add the
adapter behind `KITARU_API_URL` presence in `decode run` only if `history`-policy replay fidelity
proves lacking.**

### Tool-policy note for decode
Safe default for replays: `{"default":{"type":"history","scope":"baseline","on_miss":"fail"}}`.
Useful what-if forks of bash-heavy runs may need `passthrough` into a fresh isolated sandbox
Workspace — design decision when wiring experiments.

## 4. Concrete decode changes

1. **`src/decode/observability/`** — rework: settings-driven Logfire/OTel setup, two span
   processors (Opik OTLP exporter + JSONL file exporter). ADR-0014's "no global `OTEL_*` env"
   motive (kitaru→zenml OTel clash) dies with old kitaru; keep programmatic config anyway.
2. **`src/decode/runtime/`** — gut `flow.py` (605 lines): `decode run` becomes a plain
   `asyncio.run` headless runner around `build_agent()`. Delete `@flow`/`@checkpoint`, waits,
   artifact capture, `ImageSettings`, `modal_app.py` monkeypatch, `_durable_sleeper`.
3. **HITL** — `decode run --hitl` durable waits unimplementable on new Kitaru. Drop the feature
   (note removal in ADR) or hand-roll persistence later. `tools/sleep.py` reverts to plain
   `asyncio.sleep`.
4. **`decode replay` + `kitaru-replay-ops` skill** — rewrite onto session replay
   (`kitaru replay create <session-id>` with overrides + tool policy). Checkpoint anchors
   (`--from`), checkpoint overrides, wait re-ask: unrepresentable — document as removed.
5. **Environment Bucket (ADR-0015)** — old `get_secret` seam (`config/settings.py:171`,
   `scripts/sync_secrets.py`) breaks. Decouple app config from Kitaru (plain env / CI secrets);
   Kitaru's own secrets cover only replay injection via `--secret-id` on agent versions.
6. **`pyproject.toml`** — drop `kitaru[local,pydantic-ai,llm]>=0.18`; add `logfire` (runtime dep);
   `kitaru[cli,worker]` as dev/operator group (or run CLI via `uvx`); `kitaru-pydantic-ai` only if
   adapter path adopted. Try lifting `pydantic<2.13`, `click<8.3`, `pydantic-ai-slim<1.96` caps —
   they existed for zenml co-resolution, now out of the tree. Python 3.11+ required (we're 3.12).
7. **Infra** — delete `scripts/deploy.sh` GCP stack (VM/Caddy/GCS/Artifact Registry/ZenML stack),
   `KITARU_STACK` seam, `docker/flow.Dockerfile`, `scripts/demo-multiple-attempts.sh` kitaru
   parts, `scripts/kitaru_bootstrap_api_key.py`. Replacement: managed workspace
   `KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io` + `KITARU_API_KEY` (`KITKEY_...`);
   workers local/CI: `kitaru worker start` (image `zenmldocker/kitaru-worker`; env
   `KITARU_WORKER_*`; `--job-id --timeout` for CI drain-and-exit). No server of our own.
8. **Make targets** — new `kitaru-import` (rotate + import traces); `eval-regression` → cohort +
   experiment gate; `sync-secrets` retired with the bucket.
9. **Settings** — add OTLP endpoint/key/project/workspace + trace dir; drop
   `RUNTIME_CHECKPOINT_STRATEGY`, `RUNTIME_WAIT_TIMEOUT_S`, `KITARU_STACK`,
   `MODAL_TOKEN_SECRET_NAME` wiring.
10. **Tests** — `isolated_kitaru_store` fixture (ZenML store redirect) obsolete; wait-patching
    tests obsolete; `test_kitaru_dependency.py` rewritten. New seams: OTel in-memory exporter for
    instrumentation tests; `--local` server for integration.
11. **ADRs** — one new ADR: "Kitaru rescoped to replay evals; observability via Logfire OTLP into
    Opik". Supersedes ADR-0008 and ADR-0010 wholesale, the kitaru seam of ADR-0015, and amends
    ADR-0014 (OTel now first-class). Glossary: retire Durable Flow / Checkpoint / Wait /
    Checkpoint Override; add Session, Cohort, Evaluator, Experiment, Worker, Investigation.
12. **Skills/MCP for this repo** — DONE: `.mcp.json` carries `kitaru-mcp` (point `--server` at the
    managed workspace); new skills installed (`kitaru-investigation`, `kitaru-replay-experiment`,
    `kitaru-importer-builder`); old `.claude/skills/kitaru-replay-ops` removed. Old
    `kitaru@kitaru` plugin skills describe the dead API — disable the plugin.

## 5. Onboarding protocol (first execution step — `kitaru-investigation` skill)

Run the `kitaru-investigation` skill to connect this repo's agent to Kitaru and get it ready for
investigation. Ground rules baked into the operator prompt:

- **Inspect first**: repository + configured Kitaru environment (MCP `--server`, `kitaru status`,
  login state against the managed workspace) before creating anything.
- **Verify, don't assume, the onboarding context**: codebase = this existing repo; framework
  hint = PydanticAI; trace provider hint = Logfire; desired window = last 30 days; Kitaru
  server = `https://f5ee9622-kitaru.cloudinfra.zenml.io`.
- **Reuse before create**: any existing `decode` agent registration, agent versions, or sessions
  already in the workspace get reused, not duplicated.
- **Smallest supported trace path**: existing Logfire export/read token ⇒ import (Path A);
  else local exporter going forward (Path B). No custom importer unless built-ins can't fit.
- **Kitaru credentials ≠ trace-provider credentials**: never assume workspace access implies
  Logfire access. If an export, provider access, or another prerequisite is missing, stop and
  ask the user for the SINGLE smallest missing thing.
- Exit criterion: sessions usable in the workspace + one concrete next investigation step
  surfaced (e.g. review worklist / first evaluator sweep).

## 6. Open questions / verify before build

- Exact new kitaru version number; release state + pydantic-ai compat range of
  `kitaru-pydantic-ai` (docs silent; current pin is `pydantic-ai-slim[google,openai]>=1.95,<1.96`).
- Whether `get_secret`-style client read survives (only matters if keeping any bucket-like use).
- Logfire side: does a Logfire project/token exist for this repo already, and can it export the
  last-30-days window? (Prerequisite to ask the user for if missing.)
- JSONL shape the file exporter must emit (Logfire "records" schema vs raw OTel spans):
  scaffold the exporter, round-trip ONE session through `kitaru session import` first.
- Whether imported (non-adapter) sessions support `history` tool policy well enough for
  decode's bash-heavy runs.

## 7. Migration order (suggested tasks)

0. Onboarding via `kitaru-investigation` (section 5) — connect agent, bring in first sessions.
1. ADR + glossary + AGENTS.md rewrite (this plan distilled).
2. Dependency swap + cap-lifting experiment (`uv lock` proof).
3. Observability rework: logfire instrumentation, Opik OTLP processor, JSONL trace exporter,
   conversation-id stamping.
4. Runtime gutting: plain headless `decode run`; delete flow/HITL/sleep durability; retire
   `decode replay` (old form).
5. Settings/env cleanup + Environment Bucket retirement.
6. Infra deletion (deploy.sh etc.) + local server bootstrap docs (`running_the_code/`).
7. Kitaru eval wiring: agent registration, import target, first cohort + evaluator,
   experiment CI gate; new `eval-regression`.
8. Skills/MCP refresh; test-suite migration; docs (`running_the_code/03_runtime.md`, `05_evals.md`,
   `06_credentials.md`, `07_infra.md`).
