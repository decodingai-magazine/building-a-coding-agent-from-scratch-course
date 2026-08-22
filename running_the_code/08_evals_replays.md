# Kitaru Evals & Replays — from zero to a compared experiment

[03_runtime.md](03_runtime.md) covers the mechanics: `decode run`, the Recording Seam, one replay.
This file is the *operator journey* — setting Kitaru up from scratch and walking the full
evidence loop on your own traffic: **record → investigate → cohort → evaluator → replay →
compare**. Everything below was executed for real against the managed workspace; the pitfalls at
the end are the ones actually hit.

Budget: ~30 minutes, one provider key, Docker Desktop for the replay step.

## 0. What you're building

Kitaru (0.22.x) is a replay-based eval framework: agent runs are recorded as **Sessions**
(every LLM + tool call as nodes), humans judge them in **Investigations**, judged sessions
freeze into **Cohorts**, **Evaluators** turn criteria into repeatable verdicts, and **Replays**
re-execute a session from the top on a **Kitaru Worker** in *your* environment — optionally with
one change (model, prompt, params) — so you can compare before/after with the same evaluator on
both sides. Nothing executes on the server ([ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md)).

## 1. Set up the CLI and log in

The `kitaru` CLI ships with decode's deps (`kitaru[cli,mcp,worker]`), so `make install` already
gave it to you:

```bash
uv run kitaru login https://f5ee9622-kitaru.cloudinfra.zenml.io   # device flow in the browser
uv run kitaru status                                              # authenticated, compatible, dashboard URL
```

`kitaru status` is your first stop whenever anything looks off — it names the selected server,
credential state, and the dashboard URL.

CI / non-interactive machines use an API key instead of a login:

```bash
export KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io
export KITARU_API_KEY=KITKEY_...
```

## 2. Optional: wire your coding agent into Kitaru

The workspace is fully drivable from a coding agent (Claude Code, Cursor) via MCP + skills —
this is how the whole investigation flow in this repo was run:

```json
// .mcp.json (already in this repo)
{
  "mcpServers": {
    "kitaru": {
      "command": "uv",
      "args": ["run", "kitaru-mcp", "--server", "https://f5ee9622-kitaru.cloudinfra.zenml.io", "--mode", "standard"]
    }
  }
}
```

Modes: `read-only` → `standard` (create cohorts, investigations, runs) → `destructive`
(cancel/delete). The matching skills (`kitaru-investigation`, `kitaru-replay-experiment`,
`kitaru-importer-builder`) live under `.claude/skills/` — installed via
`npx skills add zenml-io/kitaru-skills`.

## 3. Get sessions in — record new, import old

**Record new runs** (the adapter path — full replay fidelity). One knob plus the connection:

```bash
export KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io
export KITARU_AGENT_ID=<uuid printed by `kitaru agent get decode`>
uv run decode run "say hi in exactly three words"
uv run kitaru session list --agent decode --origin recorded --size 3
```

REPL turns record too (grouped by decode session id). Details and failure modes in
[03_runtime.md](03_runtime.md#record-runs-as-kitaru-sessions-opt-in).

**Import history** (the importer path — backfill from Opik). This repo registered a custom
importer `opik@1` (`importers/opik_importer.py`) that converts an Opik REST export
(`{workspace, project, traces: [{trace, spans}]}`) into joined multi-turn sessions:

```bash
uv run kitaru importer list                       # opik@1 among the kitaru/* built-ins
uv run kitaru session import export.json \
  --importer opik@1 --agent decode@1 \
  --media-type application/json \
  --tag opik-backfill --wait
```

Payloads cap at 50 MiB — split by *thread group* (a conversation must never straddle two files).
Imports are deduplicated on `provider + external_id`, so re-running the same export skips
instead of duplicating. An import is a job executed by a **worker** (step 5) — start one first.

## 4. Judge, freeze, and encode a behavior

The evidence loop, driven from your coding agent (the `kitaru-investigation` skill walks it) or
by hand:

```bash
# pick a review worklist and open it in the browser UI
uv run kitaru investigation create my-discovery-1 --agent decode \
  --session <ID> --session-question '<ID>:observation=What do you notice? Did it match what should have happened?' \
  ...                                              # repeat per session; worklists are FIXED at creation
```

The create result carries a `links.review` URL — human verdicts (`acceptable` / `problematic`)
and free-text observations happen there, not in the terminal. When a behavior is accepted:

```bash
# freeze the reviewed positives into an immutable population
uv run kitaru cohort create decode-bad-request-400 --agent decode --session <ID> [...]

# encode the criterion as a versioned evaluator (deterministic Python, SessionView → EvaluationResult)
uv run kitaru evaluator scaffold my-check --path evaluators/my_check.py
uv run kitaru evaluator test evaluators/my_check.py --entrypoint evaluate
uv run kitaru evaluator register my-check --script evaluators/my_check.py --entrypoint evaluate

# baseline sweep: score every backfilled session, no replay involved
uv run kitaru session evaluate --tag opik-backfill --evaluator 'my-check@1' --wait
```

This repo's worked example: cohort `decode-bad-request-400@1` (a reviewed malformed-request
crash) + evaluator `decode-bad-request-400@1` (`evaluators/decode_bad_request_400.py`) — it
flagged exactly the 2 crash sessions out of 38, zero false positives.

## 5. Start a Worker (the thing that executes replays)

Nothing runs on the server. A Worker is a process *you* run — on your laptop, or on Modal (both
below) — that claims replay / evaluator / importer tasks and spawns them with your credentials:

```bash
cd <repo root>                       # careful: the checkout nests two same-named dirs
set -a && source .env && set +a      # provider keys must be IN the worker's env
unset KITARU_AGENT_ID                # see pitfall #3 below
uv run kitaru worker start --concurrency 10
```

The worker prints one `starting: {...}` line and then sits silent — that's it polling (2s
interval); output appears when it claims a task. Verify from another terminal:

```bash
uv run kitaru worker list            # yours, live: True
```

Replays of decode spawn **agent version 2**'s run spec: `decode run` under
`SANDBOX_MODE=docker` with a fresh repo clone in `~/.decode-kitaru-worker` — replayed tool calls
never touch your working tree. Docker Desktop must be up. The registration is reproducible:
`uv run python scripts/register_kitaru_agent.py --dry-run`.

**Or run the Worker on Modal instead of your laptop** ([ADR-0020 §5](../docs/adr/0020-remote-headless-on-modal.md)).
Same Worker, a gVisor container instead of your shell — replays keep going with the laptop closed. It
spawns **agent version 3**: `SANDBOX_MODE=none`, no repo clone, in-image paths — the container itself is
the isolation, so no Docker daemon is needed (and none exists there). Its env comes from the
`decode-kitaru-worker` Modal Secret; full setup, including the control plane `KITARU_API_KEY` it needs,
is in [07_infra.md §3](07_infra.md#3-the-modal-hosted-kitaru-worker):

```bash
uv run modal deploy scripts/modal_kitaru_worker.py
uv run modal run --detach scripts/modal_kitaru_worker.py --concurrency 4 \
  --agent-version-id 01a029bf-0ae3-7de1-b594-4bc71a7ba91a          # = agent decode@3
```

Then replay with `--agent decode@3` — pin the version, never "latest" (`latest_version` reads 4, an
immutable QA-accident duplicate of 3). The two Workers coexist, but each can only run **its own** Agent
Version, so scope their claims (`--agent-version-id` here, `--claim agent=<v2 id>` on the laptop) or
they will race for tasks neither can finish.

## 6. Replay, then compare

```bash
uv run kitaru replay create <BASELINE_SESSION_ID> \
  --agent decode@2 \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' \
  --evaluate-baselines
uv run kitaru job watch <JOB_ID>     # from the create output; settles in seconds-to-minutes
uv run kitaru replay get <REPLAY_ID>
```

- `--evaluator` is **required** — a replay without a metric is just a rerun.
- `--tool-policy history` answers tool calls from the recording; `on_miss: error_result` keeps a
  cache miss from executing live (`passthrough` would run real bash — never default to it).
- `--evaluate-baselines` scores the original too, giving the paired verdict.
- A what-if fork is one flag more: `--override '{"model": {"Qwen/...": "gemini-3.5-flash"}}'`.

**Compare in the UI** (dashboard from `kitaru status` → workspace → Agents → decode →
Sessions):

1. A replay session's page carries a `from <baseline>` **Compare** link — the canonical
   baseline-vs-replay diff (inputs, tool policy, outputs, per-step timeline).
2. Any two sessions: tick two checkboxes in the Sessions list → **Compare** in the bottom bar.
3. Direct URL: `<dashboard>/sessions/compare?left=<id>&right=<id>`.

Comparing two *replays* only means something when they share a baseline (baseline replay vs
fork). Different baselines = "two different sessions", and the page says so.

**At scale** — a whole cohort against one change, paired columns per evaluator:

```bash
uv run kitaru experiment create cheaper-model \
  --evaluator 'decode-bad-request-400@1' \
  --override '{"model": {"Qwen/Qwen3.6-35B-A3B-FP8": "gemini-3.5-flash"}}'
uv run kitaru experiment run start cheaper-model \
  --cohort-version <ID> --agent decode@2 --evaluate-baselines --wait
```

`--wait` exits non-zero on failure, so the same command is a CI gate.

## 7. Field notes — the pitfalls we actually hit

1. **`.env` sourced in the wrong directory.** The checkout nests two directories with the same
   name; `set -a && . .env` in the outer one fails silently-ish and the worker starts keyless.
   Symptom: replay fails with `Decode: set GEMINI_API_KEY in your environment`. `pwd` first.
2. **Worker env ≠ your shell env.** Provider keys reach the replayed decode only through the
   worker's own environment (agent v2 ships `secret_ids: []` —
   [ADR-0019 Amendments §2](../docs/adr/0019-kitaru-replay-runtime.md)). Export keys *before*
   `kitaru worker start`, or attach version secrets for shared workers.
3. **`KITARU_AGENT_ID` must NOT be in the worker's env.** The worker injects a task-scoped
   token; with the agent id set, the Recording Seam probes an agents route that task tokens
   can't use → `403: Task credentials are not accepted on this route` → the run hard-fails
   (correctly). `unset KITARU_AGENT_ID` in the worker shell; the adapter infers the agent from
   the task. (Tracked: `tasks/139`.) Same rule on Modal, enforced twice: the
   `decode-kitaru-worker` Secret deliberately omits `KITARU_AGENT_ID` (that composition is the rule
   — [07_infra.md §1](07_infra.md#1-secrets--two-deliberately-asymmetric)), and the worker Function
   scrubs the variable defensively at startup with one logged line if one ever shows up anyway.
4. **`Invalid arguments: --evaluator requires an argument`** on `replay create` means the flag
   is *missing*, not empty — it's required.
5. **`replay create` has no `--wait`** — that's `session import` / `experiment run start`. Use
   `kitaru job watch <job-id>` instead.
6. **Import payload over 50 MiB** → split by thread group, one conversation per file, never
   across two.
7. **A silent worker is a healthy worker.** It polls quietly; check `kitaru worker list`, not
   the terminal.
8. **A failed replay with an *agent-level* error is still a working pipe.** `ModelHTTPError:
   503` from a downed provider means the worker claimed, spawned, and ran decode — only the
   model was gone. A spawn/import error (`ModuleNotFoundError`, command-not-found) is the one
   that means your setup is broken.

## Go further

- [03_runtime.md](03_runtime.md) — the runtime surface itself (recording seam, degrade rules,
  worker task entry).
- [06_credentials.md](06_credentials.md) — Environment Bucket vs replay secrets.
- [ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md) — why decode is shaped this way (and
  what died: durable flows, checkpoints, HITL waits).
- Kitaru docs: https://docs.zenml.io/kitaru — concepts (sessions/replays/cohorts/experiments),
  tool policies, importers, workers in production.
