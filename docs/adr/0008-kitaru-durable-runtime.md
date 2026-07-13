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

   > **Amendment (2026-06-28, task 058) — bypass runs decode's tools *inline*, it does not bypass the
   > whole loop.** §1's "headless replaces decode's loop" framing was incomplete. **Every** decode
   > tool — even read-only `read`/`glob`/`grep` — opens its body with `raise ApprovalRequired` until
   > approved; that deferral is resolved by decode's **loop** (`agent/loop.py`), which `run_sync` does
   > **not** run. And the Kitaru adapter converts *any* `ApprovalRequired` inside a checkpoint into a
   > flow-scope `kitaru.wait()` (a HITL pause) — so under `run_sync` the *first* tool call of an
   > unattended bypass run would raise `KitaruUsageError` ("waits must be at flow scope"), not return
   > a clean string. The premise "bypass → no `ApprovalRequired`" only holds if a tool consults the
   > mode. Fix (small, central): one predicate, `decode.tools.approval.needs_approval(ctx)` =
   > `not ctx.tool_call_approved and ctx.deps.gate.mode is not BYPASS`, replaces the inline
   > `not ctx.tool_call_approved` check at all 9 gated sites (`files.py` ×5, `web`, `tasks`, `bash`,
   > `lsp`). Under **BYPASS** a gated tool runs
   > **inline** (no deferral, no wait); under default/plan/edit it defers exactly as before and the
   > loop resolves it through the gate — **interactive behaviour is byte-unchanged**. So the headless
   > flow *keeps* the `KitaruAgent` adapter for durability and simply makes the shared tools
   > bypass-aware; it does not re-run the interactive loop and does not hand-drive
   > `DeferredToolResults`.

3. **HITL = swap the single decision channel for a `wait()` bridge.** decode already routes
   `ask_user` *and* every permission approval (exit-plan-mode, write/bash gates) through **one**
   resolver, `resolve_user_question` (`agent/deps.py:81`). In **headless** mode that resolver is the
   only thing that changes: it becomes a bridge to flow-scope `kitaru.wait(name=…, question=…,
   schema=…)`, resolved out-of-band by `kitaru executions input`. Interactive mode keeps the console
   resolver. The single-channel design (one seam) is what makes this a clean swap rather than a
   rewrite. Tool-time waits use the adapter's `hitl_tool` / `wait_for_input`, or opt the waiting tool
   out of granular checkpoints (`tool_checkpoint_config_by_name={...: False}`), per the adapter rule
   that waits live at flow scope.

   > **Amendment (2026-06-28, task 058).** With the §2 amendment in place, 059's headless HITL is the
   > natural complement: instead of forcing BYPASS, headless deps use a non-bypass mode so a gated
   > tool's `ApprovalRequired` *does* fire, and the adapter's `ApprovalRequired → kitaru.wait()` bridge
   > (with the gated tools opted out of granular checkpoints, `tool_checkpoint_config_by_name={tool:
   > False}`, and `allow_sync_tool_body_waits=True`) turns each approval into a durable wait resolved
   > out-of-band by `kitaru executions input`. 058 ships the BYPASS (no-human) slice; 059 layers the
   > durable approvals on top — no tool change, just the deps mode + adapter config.
   >
   > **Amendment (2026-06-28, task 059 — what shipped, verified against the installed adapter 0.18).**
   > 059 added a *second* flow, `runtime/flow.py::run_agent_task_hitl` (the bypass `run_agent_task`
   > stays untouched), launched by `decode run --hitl`. Five realities the pre-implementation framing
   > did not anticipate, each confirmed by spiking the real local stack offline:
   >
   > 1. **HITL forces `checkpoint_strategy="calls"`** — *not* `settings.runtime_checkpoint_strategy`.
   >    The per-tool checkpoint opt-out that hoists a wait to flow scope is only accepted under
   >    `"calls"`; under `"turn"` the single turn-checkpoint wraps the tool and the wait raises "must
   >    be at flow scope". So the AC's "under turn no opt-out is needed" does not hold for an
   >    *actually-waiting* HITL run — `"turn"` cannot host flow-scope waits at all. `runtime_checkpoint_strategy`
   >    therefore governs only the bypass run.
   > 2. **Read-only tools must run inline, or a HITL run pauses/crashes on its first `read`.** Under
   >    `run_sync` no decode loop applies the gate's read-only-allow floor, so a new deps flag
   >    `AgentDeps.headless_durable_waits` makes `needs_approval` apply it itself: read-only → inline,
   >    mutating → `ApprovalRequired` → durable wait. Interactive deps leave the flag `False`
   >    (byte-unchanged). The opt-out map is the *waiting* tools only — `write`/`edit`/`bash` +
   >    `ask_user`/`exit_plan_mode`.
   > 3. **`ask_user`/`exit_plan_mode` bridge via `resolve_user_question` → `wait_for_input`;
   >    approvals are the adapter's native `ApprovalRequired → wait`.** `resolve_permission` is *not*
   >    the approval bridge under `run_sync` (no loop calls it) — it stays the deny safety-net. The
   >    async resolver calls the **sync** `wait_for_input` directly (no `anyio.to_thread`): under
   >    `run_sync` + `allow_sync_tool_body_waits=True` the agent's event loop runs on Kitaru's
   >    workflow thread, which is where a flow-scope wait must be created.
   >    **Known limitation — `runtime_wait_timeout_s` scopes only the waits decode drives.** Because
   >    `wait_for_input` is decode's own call, it passes `timeout=runtime_wait_timeout_s`, so the
   >    `ask_user`/`exit_plan_mode` question waits honor the setting. The `write`/`edit`/`bash`
   >    **approval** waits are created *inside the adapter* from `ApprovalRequired` as
   >    `kitaru.wait(timeout=None)`, which falls back to ZenML's fixed `600s` default — they ignore
   >    `runtime_wait_timeout_s` (a live run with `RUNTIME_WAIT_TIMEOUT_S=6` still logs
   >    `timeout=600s`). Honoring the setting on approval waits would require forking the adapter's
   >    `_invoke_wait`; decode imports infrastructure rather than forking it, so the divergence is
   >    documented and deferred, not patched around.
   > 4. **A denied approval STOPS the run; it does not feed back to the model.** The adapter resolves
   >    a deny by raising `_ToolApprovalDenied` out of `run_sync` — there is no pydantic-ai
   >    deny-result round-trip, because the adapter intercepts every `ApprovalRequired` and never lets
   >    it surface as a `DeferredToolRequests` the flow could hand-drive. The flow catches it and
   >    finishes with a denial message (the denied tool never ran). This is the one place headless
   >    HITL **differs** from the interactive gate (which returns the deny reason and lets the model
   >    adapt); a feed-back-on-deny path would need decode's mutating tools to bridge approvals
   >    themselves rather than raise `ApprovalRequired` — deferred as a possible follow-up.
   > 5. **`"calls"` + opt-out breaks Kitaru's `.wait()` return-value extraction** (several terminal
   >    model-request checkpoints → `_MultipleTerminalStepsOutputError`). The flow stores its final
   >    text in a closing `@checkpoint` as a named artifact (`decode_runtime_output`) and the reader
   >    loads it back by name; a paused run (unresolved wait) returns no artifact and the reader
   >    reports the paused `exec_id`. **Offline tests** resolve each wait inline by patching Kitaru's
   >    local interactive-input seam (a background `KitaruClient.input` thread re-inits ZenML's
   >    per-thread store and races SQLite; post-timeout `resume` needs a deployed flow the in-process
   >    local stack lacks).

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
   documented fallback; the path is opt-in (`runtime_secret_store_model_key`, default off).

   > **Amendment (2026-06-29, task 064 — credential architecture corrected against the official Kitaru
   > credential-proxy doc (`agent-harness-platform/04-credential-proxy`) + the `agent_harness_platform`
   > example, Stage 4).** This §5 conflated two *distinct* Kitaru features and mis-scoped the
   > mechanism. Corrected, with the env-injection task that was drafted off the original §5 dropped:
   >
   > 1. **"Credentials Proxy" was a misnomer.** Kitaru's actual **Credential Proxy** is **HTTP header
   >    injection via a separate proxy container** — `SandboxProxyRule` (per-host header templates) +
   >    `build_credential_map` (host-side template resolver) + `DockerProxy` (a mitmproxy addon). The
   >    worker's outbound traffic is routed through the proxy, which attaches the `Authorization`
   >    header *after the request has left the worker*, so **"the worker never holds the secret."**
   >    What task 061 shipped is **not** that — it is a **secret-store lookup** (`get_secret(...)`) for
   >    the model API key at model construction. Both are legitimate but different; the model key is the
   >    right case for a *store lookup* (the **harness** consumes it to call the LLM — it is not a
   >    credential a model-chosen `bash` command uses). 061's "Credentials Proxy" label is renamed
   >    **model-key secret resolution** to free the term for the real proxy. (This is a *conceptual /
   >    docs* rename — the code identifiers `runtime_credentials_proxy_enabled` / `runtime_secret_name`
   >    keep their names until a dedicated rename, a documented conceptual-vs-identifier divergence.)
   >    **The dedicated rename has since landed — see the 2026-07-13 amendment below.**
   > 2. **Env-injection is rejected as the secret mechanism.** §5 named env injection
   >    (`ImageSettings(secret_environment_from=[...])`) as the fallback. The official doc argues
   >    against it directly: *"if a token lives in the agent's environment, the agent can leak it."*
   >    Putting secrets in the worker/process env is the exact leak vector the credential proxy exists
   >    to remove, so decode does **not** use `secret_environment_from` for secrets. (`secret_environment_from`
   >    is in any case a *transport/deployment* option — a no-op on the local in-process stack — so it
   >    bought nothing locally either.)
   > 3. **What task 064 ships instead — a secret-store *config source*.** Generalize 061's single-key
   >    lookup into a pydantic-settings custom source (`KitaruSecretSettingsSource`) that, **headless
   >    only** (`decode run`, gated by `runtime_secret_store_config`, default off), hydrates the whole
   >    `.env.example` surface from one Kitaru secret into the **`Settings` object** — **never
   >    `os.environ`, never a worker env** — so a model-chosen `bash` never inherits it. Precedence:
   >    **real env > Kitaru secret > `.env` > default**. The singleton is rebuilt in place for the flow
   >    span and **restored on exit** (the `_durable_sleeper` reset discipline); the REPL never imports
   >    kitaru (the source no-ops *and* skips the import when inactive). The governing rule, one line:
   >    **Kitaru secret → `Settings`, yes; → process/worker env, no.** (`get_secret`/`create_secret` is
   >    Kitaru's; decode only adds the read-into-`Settings` integration. The glossary's "Credentials
   >    Proxy" term splits the same way: *secret store* + *secret-store config source* now; *Credential
   >    Proxy* reserved for the header-injection feature.)
   > 4. **The real Credential Proxy is deferred to the sandbox milestone** — it needs an isolated
   >    worker to sit in front of (today `bash` runs in-process via `LocalExecutor`). Its integration
   >    design is fixed now in **"Future work — the Credential Proxy at the sandbox step"** below; it
   >    will get its own implementation ADR when built.

   > **Amendment (2026-07-13 — the dedicated identifier rename).** The 2026-06-29 amendment renamed
   > the *concept* but deliberately left the *identifiers* diverged, as an IOU. With the real
   > Credential Proxy now shipped (ADR-0011 §6, ADR-0012 §10), that divergence made the two features
   > genuinely indistinguishable by name in code, `.env.example`, and the README — readers repeatedly
   > mistook the secret-store lookup for header injection. The IOU is paid; the identifiers now match
   > the concept:
   >
   > | Was | Is |
   > |---|---|
   > | `RUNTIME_CREDENTIALS_PROXY_ENABLED` / `runtime_credentials_proxy_enabled` | `RUNTIME_SECRET_STORE_MODEL_KEY` / `runtime_secret_store_model_key` |
   > | `resolve_provider_key_via_proxy()` | `resolve_provider_key_from_secret_store()` |
   > | `PROXY_SECRET_KEY` | `SECRET_STORE_KEY` |
   > | `_uses_credentials_proxy()` / `_proxy_credential_error()` (cli) | `_uses_secret_store_model_key()` / `_model_key_secret_error()` |
   >
   > The two knobs now read as one family over the one secret named by `runtime_secret_name`:
   > `RUNTIME_SECRET_STORE_MODEL_KEY` takes **only the model key** from it,
   > `RUNTIME_SECRET_STORE_CONFIG` takes the **whole config** surface. Both stay opt-in and headless-only,
   > and both remain available — with both off the key comes from `.env`, which is the point of keeping
   > the narrower knob rather than folding it into the config source. **Breaking change:** an existing
   > `RUNTIME_CREDENTIALS_PROXY_ENABLED=true` in a `.env` is now silently ignored (pydantic-settings does
   > not error on unknown vars); rename it. No compatibility alias ships — a teaching codebase is better
   > served by one name than by two that both work. `docs/glossary.md` and
   > [`CREDENTIALS.md`](../../CREDENTIALS.md) (which walks an e2e test of both features, on and off)
   > carry the user-facing version.

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
- **Glossary + settings grow.** Ubiquitous-language terms go in `docs/glossary.md` (Headless Runtime,
  Durable Flow, Checkpoint, Replay, Wait (HITL); plus — per the 2026-06-29 §5 amendment —
  **Secret-Store Config** and a reserved **Credential Proxy**, which replace the original single
  "Credentials Proxy" term), and the runtime settings (`runtime_enabled`,
  `runtime_checkpoint_strategy`, `runtime_wait_timeout_s`, `runtime_secret_store_model_key`,
  `runtime_secret_name`, and `runtime_secret_store_config` added in task 064) in `config/settings.py`
  + `.env.example`
  — the glossary applied at the plan gate, the settings landed in task 057 ahead of their readers (the
  LSP/compaction settings-first pattern). The Kitaru stack is selected by `kitaru init` / Kitaru's own
  config, not a decode setting.
- **Non-goals (deliberate).** Wrapping the live REPL in a flow; migrating the JSONL session log onto
  Kitaru; native cron inside decode; a native durable key-value state API (use artifacts / external
  state); multi-flow orchestration; and remote stacks (deferred to step 12). The headless single-flow
  slice is the extension point if any is revisited.

## Future work — the Credential Proxy at the sandbox step (how it will integrate)

The §5 amendment renamed 061's model-key lookup and recorded that Kitaru's **real Credential Proxy**
is deferred. It is deferred for a structural reason: the proxy is a process that sits **between an
isolated worker and the network**, and decode has no isolated worker yet — `bash` runs in-process via
`LocalExecutor`. In the canonical `agent_harness_platform` example the proxy is **Stage 4, strictly
after Stage 2 (the Docker sandbox)**. So the proxy lands with **decode's sandbox milestone**, built the
canonical way. The integration is fixed here now (it gets its own implementation ADR when built) so the
secret-store work above slots into it without rework.

**The credential-boundary principle (the whole point).** *The side that runs model-chosen commands
holds no secret; the side that holds the secret runs no model-chosen command.* A prompt-injected agent
can read anything in its environment — so **no token ever enters the worker's env**. The worker sends
the request; a separate process attaches the credential *after the request has left the worker*.

**The three pieces, and how each integrates with existing components:**

- **`SandboxProxyRule` — a new config surface (integrates with the Profile/agent config).** Declares,
  per host, which header to inject with a secret template, e.g.
  `SandboxProxyRule(name="github-auth", hosts=["api.github.com"], headers={"Authorization": "Bearer {{ github-token.value }}"})`.
  This is a *list of rules* on the agent/profile — distinct from, and additional to, the secret-store
  **config source** (064). Config source = the harness's own settings; proxy rules = the tools'
  outbound-call credentials.
- **`build_credential_map(...)` — the integration with the secret store.** At flow start, on the host,
  it resolves each `{{ name.key }}` template by calling **`kitaru.get_secret(name).values`** — i.e. the
  proxy is *built on top of the same Kitaru secret store* that §5/064 already use. One store, multiple
  consumers; no new secret backend. The resolved `{host: {header: value}}` map is handed **only to the
  proxy container's environment — never the worker's.**
- **`DockerProxy` — the injection (integrates with the sandbox `run` seam).** A mitmproxy addon running
  alongside the `DockerSandbox`; the worker's `http_proxy`/`https_proxy` point at it and it trusts the
  proxy's CA. The addon matches each request's host against the credential map and injects the header in
  transit, so `curl https://api.github.com/...` from the worker succeeds though the worker never held a
  token. `DockerProxy` + `SandboxProxyRule` are the two swap points if mitmproxy is replaced.

**How it composes with the rest of decode:**

- **Secret store (§5 / 061 / 064):** *one* store, *three* host-side consumers — (a) 061 model-key
  resolution, (b) 064 secret-store config source (→ `Settings`), (c) `build_credential_map` (→ proxy
  container). None of the three puts a *tool* secret in the worker.
- **The sandbox `run` seam (AGENTS.md "the one real abstraction"):** the proxy requires the sandbox's
  container + network isolation, which is exactly why it cannot precede the sandbox step. The
  proxy-aware sandbox is wired at the `run` seam (local Docker/Firecracker, remote Modal), not above it.
- **`bash` today vs then.** Today `LocalExecutor` `bash` inherits `os.environ`, which is *why* the 064
  config source deliberately writes to `Settings` and **never** `os.environ`. Once `bash` runs in the
  sandbox worker, the worker holds **no** credentials and authenticated calls go through the proxy —
  closing the gap the "no `os.environ` write" rule protects in the interim.
- **Typed services as a second credential path (example Stage 5).** For structured calls, a host-side
  `exec_service` dispatch can hold credentials the worker never touches at all — bypassing both the
  shell and the proxy. A candidate for decode's `services/` layer when the need arises.
- **`modal`:** its dual proxy *tokens* (`MODAL_PROXY_TOKEN_ID`/`_SECRET`) are a request-header surface —
  the same shape this proxy injects — which is why §5/061 deliberately left `modal` out of the
  secret-store/model-key path. modal's credentials belong to this proxy step, not the model-key lookup.

This section is **design intent, not a built feature.** It ships with the sandbox milestone and will be
recorded in its own implementation ADR (mechanism, container teardown, tests) at that time.
