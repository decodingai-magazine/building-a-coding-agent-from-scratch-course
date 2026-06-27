# 0008. Kitaru durable runtime — a headless flow as a second entry path, not a wrapper around the live REPL

**Status:** Accepted
**Date:** 2026-06-27

## Context

decode today is an **interactive streaming TUI**: the `Runner` drives `agent.iter()` and streams
tokens to the terminal (`agent/loop.py:366`), a human steers mid-turn / aborts with Esc / queues
follow-ups (the `Boundary` machine, ADR-0002), and **durability already exists** as the append-only
JSONL session log plus `--resume` (`context/session_log.py`, `tui/app.py:835`). The step-7 ask is a
**Kitaru runtime** giving four things: durability, human-in-the-loop (HITL), a credentials proxy, and
scheduling.

The naive reading — "wrap the agent loop in a Kitaru `@flow`" — is wrong, and Kitaru's own scoping
guidance says so: **streaming chat UIs and low-latency request/response serving are listed as signals
that durable execution is *unnecessary***. Wrapping the live loop would (a) duplicate the existing
JSONL durability, (b) put a second orchestrator on top of the harness queue + priority gate, and
(c) buy near-zero replay value for a human at the keyboard, who can just retype.

Where Kitaru **does** pay off is a **headless, unattended run** — one task in, the agent tool-loops
to completion, output out — which is exactly what the later milestones need: **step 12** (deploy to
Modal) and **step 13** (cloud GitHub PR review; implement-a-feature ×N). Those have *no human at a
terminal*, so an approval must pause the whole execution and be resolved out-of-band hours later, and
an expensive multi-tool run must survive a process restart. That is precisely Kitaru's model.

Facts confirmed while scoping (resolves the AGENTS.md "confirm package source" TODO):

- **`kitaru` is on PyPI, current `0.18.0`** (the installed Claude-Code *skills* plugin is a separate
  `0.7.0` artifact). Install: `uv add kitaru` with extras `local` (core), `pydantic-ai` (the adapter
  decode needs), `llm`, `mcp`.
- **PydanticAI adapter** `KitaruAgent` wraps an existing `pydantic_ai.Agent`; granular mode gives each
  model/tool/MCP call its own checkpoint. It **supports streaming** (`run_stream()`/`iter()`), with
  the documented caveat that *streamed turns can fall back from granular to per-turn checkpoint
  granularity*. So streaming and checkpointing coexist — streaming was never the blocker.
- **Kitaru has no native cron/scheduling.** Per ZenML docs, recurring scheduling is **external**:
  deploy the flow (`kitaru deploy`) and trigger it from an outside scheduler (system crontab, Modal
  `Cron`, K8s CronJob, or the generated `kitaru flow deployments curl`). A `wait(..., timeout=…)` is a
  native *one-shot* durable timer, but not a recurring schedule.
- **HITL is a flow-scope `wait()`**, resolved via `client.executions.input(...)` / `kitaru executions
  input` / MCP — `input` resolves, `resume` is the manual fallback.
- **Secrets** are first-class (`create_secret` / `get_secret` / `delete_secret`); `llm()` auto-resolves
  alias-linked secrets. This is the credentials-proxy seam.

Project constraints honored: infrastructure is imported/called directly until a second implementation
exists (AGENTS.md); `runtime/` is the reserved top-level module for this; **secrets never reach the
model or the sandbox payload** (existing invariant); async I/O for the loop.

This ADR records the **decision and flow architecture** for the Kitaru runtime feature
(`kitaru-runtime`, step 7). Groomed by `/plan` into tasks **057-062** (settings/dep/glossary →
durable flow + `decode run` → HITL → durable `sleep` → credentials proxy → offline capstone) and
**Accepted**. Three exploration findings are folded in below: the async/flow boundary is resolved
(sync `@flow` + `KitaruAgent.run_sync()`), local/offline execution is confirmed, and the
credentials-proxy surface is recorded as least-exampled (verify-first).

## Decision

1. **A headless durable flow as a SECOND entry path — the live REPL is untouched.** Add
   `src/decode/runtime/` housing a Kitaru `@flow` that runs the **same** `build_agent()`
   (`agent/factory.py:68`) autonomously. A new `decode run "<task>"` subcommand (and, later, a deployed
   endpoint) launches it. The interactive TUI keeps driving `agent.iter()` through the harness exactly
   as today. This is the textbook *second concrete caller* that earns the `runtime/` module, and it is
   the stepping stone to a future flow-backed streaming TUI (the "unified" topology) without rewriting
   anything now.

2. **Durability via the PydanticAI adapter, not hand-rolled checkpoints.** The flow wraps the agent in
   `KitaruAgent(build_agent_inner_agent, checkpoint_strategy=settings.runtime_checkpoint_strategy)` —
   confirmed against the installed adapter; `"turn"` (one checkpoint per `run_sync()` turn) is the MVP
   default, `"calls"` is per model/tool call. Each finished checkpoint is replayed from cache; a crash
   resumes near the failure instead of re-running tools. No `@checkpoint` is wrapped around the agent
   by hand.

3. **HITL = swap the single decision channel for a `wait()` bridge.** decode already routes
   `ask_user` *and* every permission approval (exit-plan-mode, write/bash gates) through **one**
   resolver, `resolve_user_question` (`agent/deps.py:81`). In **headless** mode that resolver is the
   only thing that changes: it becomes a bridge to flow-scope `kitaru.wait(name=…, question=…,
   schema=…)`, resolved out-of-band by `kitaru executions input`. Interactive mode keeps the console
   resolver. The single-channel design (one seam) is what makes this a clean swap rather than a
   rewrite. Tool-time waits use the adapter's `hitl_tool` / `wait_for_input`, or opt the waiting tool
   out of granular checkpoints (`tool_checkpoint_config_by_name={...: False}`), per the adapter rule
   that waits live at flow scope.

4. **`sleep` → a durable, resumable timer.** In flow mode the `sleep` tool (`tools/sleep.py:37`, today
   a bare `asyncio.sleep`) becomes `kitaru.wait(name="sleep", timeout=…)` — the execution can pause and
   the process exit, then resume. Interactive mode keeps `asyncio.sleep`. Same single-tool surface,
   mode-dependent implementation.

5. **Credentials proxy = Kitaru secrets at the model-construction seam.** In flow mode, replace the
   direct `settings.<provider>_api_key.get_secret_value()` at model construction (`agent/factory.py:115`)
   with Kitaru `get_secret(...)` / `llm()` alias resolution, so the **deployed flow payload carries
   handles, not raw keys** — satisfying the existing secrets-never-in-payload invariant for the case
   that actually matters (a remotely-executed flow). Interactive in-process runs keep reading
   `SecretStr` from settings. **This is the least-exampled Kitaru surface** — no Agent Harness
   Platform example wires the *model key* through secrets (the credential-proxy example injects
   *sandbox HTTP* headers), and because the PydanticAI adapter needs a concrete model at construction,
   the docs-backed path is env injection (`ImageSettings(secret_environment_from=[...])`). So task 061
   **verifies the secrets API against the installed SDK first** and ships env-injection as the
   documented fallback; the path is opt-in (`runtime_credentials_proxy_enabled`, default off).

6. **Scheduling/cron is external — decode ships the deployable entrypoint, not a scheduler.** Because
   Kitaru has no native cron, recurring runs are: `kitaru deploy` the flow + an outside trigger. Step 7
   delivers the deployable flow entrypoint and documents the trigger; the **real recurring schedule
   lands in step 12** via Modal's first-class `schedule=modal.Cron(...)`. We do not hand-roll a
   scheduler into decode.

7. **Operator surface (start local, remote later).** Launch via the flow object / `decode run` / MCP
   `kitaru_executions_run`; inspect + resolve waits + replay via **CLI** and **KitaruClient**; browse
   artifacts via KitaruClient/MCP. Begin on a **local stack** (`uv sync --extra local`; `kitaru init`
   marks the project root with `.kitaru/`) — **confirmed to run fully offline with no Kitaru server**
   (the server only stores metadata; flows run where executed). CI and the capstone (task 062) stay
   offline by patching the runtime seam and the model boundary, exactly as the LSP feature patches its
   service seam. Remote stacks (for step 12) are CLI/MCP-created later.

8. **Coexist with the JSONL session log — do not migrate it.** Interactive durability stays
   JSONL + `--resume`; headless durability is Kitaru checkpoints/replay. Two modes, two mechanisms, one
   `build_agent()`. Unifying them onto Kitaru is an explicit non-goal here (see Consequences).

## Diagram

```mermaid
flowchart TB
    subgraph shared["shared core (unchanged)"]
        ba["build_agent() — agent/factory.py:68<br/>pydantic-ai Agent + tools + instructions"]
    end

    subgraph interactive["INTERACTIVE path (untouched)"]
        tui["decode (TUI) → Runner → agent.iter()<br/>streaming · steer/abort/follow-up (Boundary)"]
        jsonl[("JSONL session log + --resume<br/>context/session_log.py")]
        consoleR["resolve_user_question → console prompt"]
        tui --> ba
        tui --> jsonl
        tui --- consoleR
    end

    subgraph headless["HEADLESS path (NEW: src/decode/runtime/)"]
        run["decode run \"task\"  /  deployed endpoint  /  MCP run"]
        flow["@flow run_agent_task(task)"]
        kagent["KitaruAgent(build_agent's Agent)<br/>checkpoints per model/tool call (per-turn if streaming)"]
        waitb["resolve_user_question → kitaru.wait()<br/>(ask_user + permission approvals + sleep-timeout)"]
        secrets["model creds via kitaru get_secret / llm() alias<br/>handles, not raw keys"]
        run --> flow --> kagent --> ba
        flow --- waitb
        kagent --- secrets
    end

    subgraph ops["operator surface"]
        cli["CLI: kitaru executions input / replay / logs"]
        client["KitaruClient: inspect · pending_waits · artifacts"]
        deploy["kitaru deploy + EXTERNAL trigger (cron/Modal Cron) → step 12"]
    end

    waitb -. paused .-> cli
    flow -.-> client
    flow -.-> deploy

    classDef core fill:#37474f,stroke:#102027,color:#ffffff;
    classDef inter fill:#1565c0,stroke:#0d47a1,color:#ffffff;
    classDef head fill:#2e7d32,stroke:#1b5e20,color:#ffffff;
    classDef opsc fill:#e65100,stroke:#bf360c,color:#ffffff;
    class ba core;
    class tui,jsonl,consoleR inter;
    class run,flow,kagent,waitb,secrets head;
    class cli,client,deploy opsc;
```

## Consequences

- **`runtime/` is earned, not premature.** The headless flow is a genuine second caller of
  `build_agent()`, so the new module satisfies AGENTS.md's "no abstraction without a second
  implementation." Kitaru is imported and called directly (no gateway wrapper) until a third surface
  forces one.
- **Unblocks steps 12 and 13.** Deploy-to-Modal and the cloud PR reviewer are unattended by nature;
  they need exactly this headless, replayable, wait-pausing flow. Building it now is the foundation for
  both.
- **The single decision channel pays off.** Because `ask_user` and permission approvals already share
  one `resolve_user_question` seam, HITL becomes a *resolver swap* per mode rather than a change spread
  across every gated tool.
- **Streaming is preserved; the "unified" TUI is a deliberate Phase-2 deferral.** Kitaru can stream
  (the `checkpoint_streaming` feature), so a later step can make the flow the single engine and turn
  the TUI into a live streaming client that resolves waits in-place — the server-side-state /
  stream-to-clients topology from the project research wiki (`comparisons/application-state-3way`). The
  headless path does not close that door; it opens it.
- **No scheduler is built — the platform's is borrowed.** Kitaru has no native cron, so we ship a
  deployable entrypoint and let Modal/cron trigger it. One-shot durable pauses (`sleep`, timeouts) are
  native `wait()`s.
- **Honest risks.** (a) **Resolved at grooming:** the async/flow boundary is handled by the adapter —
  Kitaru `@flow` is **sync** and `KitaruAgent.run_sync()` bridges the async pydantic-ai agent
  internally, so the flow body needs no manual asyncio/event loop. The remaining seam is the **async
  decision resolver → sync `kitaru.wait` / `wait_for_input`** bridge for HITL (task 059) and the
  durable `sleep` (task 060), confirmed against the installed SDK early
  (`allow_sync_tool_body_waits=True` where required); tasks 059/060 carry an explicit de-risk AC.
  (b) Kitaru `0.18` is **pre-1.0** — pin via `uv.lock`, accept churn, keep the runtime an isolated
  module. (c) Adapter streaming **coarsens** checkpoint granularity from per-call to per-turn —
  acceptable for an unattended run.
- **CI stays offline.** The Kitaru flow runs against a **local stack**; unit tests patch the runtime
  seam (no server, no network), mirroring how the LSP service is tested (ADR-0007). The capstone e2e
  swaps only the model boundary.
- **Glossary + settings grow.** Six ubiquitous-language terms (Headless Runtime, Durable Flow,
  Checkpoint, Replay, Wait (HITL), Credentials Proxy) go in `docs/glossary.md`, and five settings
  (`runtime_enabled`, `runtime_checkpoint_strategy`, `runtime_wait_timeout_s`,
  `runtime_credentials_proxy_enabled`, `runtime_secret_name`) in `config/settings.py` + `.env.example`
  — the glossary applied at the plan gate, the settings landed in task 057 ahead of their readers (the
  LSP/compaction settings-first pattern). The Kitaru stack is selected by `kitaru init` / Kitaru's own
  config, not a decode setting.
- **Non-goals (deliberate).** Wrapping the live REPL in a flow; migrating the JSONL session log onto
  Kitaru; native cron inside decode; a native durable key-value state API (use artifacts / external
  state); multi-flow orchestration; and remote stacks (deferred to step 12). The headless single-flow
  slice is the extension point if any is revisited.
