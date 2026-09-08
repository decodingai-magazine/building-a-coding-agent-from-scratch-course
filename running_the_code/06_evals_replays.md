# Kitaru Evals & Replays — run it locally, from zero to a compared experiment

Replay-based evals on your own traffic with [Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs), driven entirely from your laptop: **record → investigate → cohort → evaluator → replay → compare**. [05_evals.md](05_evals.md) covers the benchmarks and probes you drive yourself; [07_evals_replays_deploy.md](07_evals_replays_deploy.md) moves the replay Worker off your laptop.

Budget: ~30 minutes, one provider key, Docker Desktop for the replay step.

## 0. Concepts

Kitaru (0.22.x): agent runs are recorded as **Sessions** (every LLM + tool call as nodes); humans judge them in **Investigations**; judged sessions freeze into **Cohorts**; **Evaluators** turn criteria into repeatable verdicts; **Replays** re-execute a session from the top on a **Kitaru Worker** in your environment, optionally with one change (model, prompt, params), so the same evaluator scores before and after. Nothing executes on the server ([ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md)).

Managed workspace: `https://f5ee9622-kitaru.cloudinfra.zenml.io` — free for the course, nothing to host. Holds Sessions, Cohorts, Replays, registered Agent Versions.

## 1. Set up the CLI and log in

`kitaru` ships with decode's deps (`kitaru[cli,mcp,worker]`) — `make install` installed it.

```bash
uv run kitaru login https://f5ee9622-kitaru.cloudinfra.zenml.io   # device flow in the browser
uv run kitaru status                                              # authenticated, compatible, dashboard URL
```

`kitaru status` is the first check whenever anything looks off: server, credential state, dashboard URL.

Non-interactive machines use an API key. On a managed workspace it must be a control plane key (`ZENPROKEY_…`, minting in [07_evals_replays_deploy.md §2b](07_evals_replays_deploy.md#2b-mint-the-container-credential-a-zenprokey_)); a workspace-local `KITKEY_…` is rejected:

```bash
export KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io
export KITARU_API_KEY=ZENPROKEY_...
```

## 2. Optional: wire your coding agent into Kitaru

The workspace is drivable from Claude Code / Cursor via MCP + skills:

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

Modes: `read-only` → `standard` (create cohorts, investigations, runs) → `destructive` (cancel/delete). Skills (`kitaru-investigation`, `kitaru-replay-experiment`, `kitaru-importer-builder`) live under `.claude/skills/`, installed via `npx skills add zenml-io/kitaru-skills`.

## 3. Get sessions in — record new, import old

**Record new runs** (adapter path — full replay fidelity). Recording lives in one function, the Recording Seam (`src/decode/runtime/recording.py`). Two variables switch it on:

```bash
export KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io   # adapter-owned (or just `kitaru login`)
export KITARU_AGENT_ID=<uuid printed by `kitaru agent get decode`>  # decode's one recording knob
uv run decode run "say hi in exactly three words"
uv run kitaru session list --agent decode --origin recorded --size 3
uv run kitaru session get <SESSION_ID>                              # node by node
```

- **Both surfaces record.** REPL turns group under `session_name` = the decode session id. Remote runs on Modal record too when the same keys ride the `decode-headless` Secret ([04_deploy.md §2b](04_deploy.md#2b-the-decode-headless-secret)).
- **`KITARU_API_URL` must be exported, not just in `.env`.** decode never reads it; the adapter's client does (env, else the `kitaru login` store). `set -a && . .env && set +a`.
- **Off is byte-identical.** `KITARU_AGENT_ID` empty → no kitaru module imported.
- **Unreachable workspace degrades.** A user-launched run drops to the bare agent, prints one stderr line (`[kitaru] not recording this run: … continuing on the bare agent`), exits 0. A Worker-spawned run hard-fails instead — an unrecorded replay is a lying experiment.
- **A Worker hard-fail is one line** — `Decode: [kitaru] recording is unavailable for this Kitaru Worker Task: …`, non-zero exit, traceback in `.decode/logs/decode.log` only. Covers a 403 on the agents route (adds the `KITARU_AGENT_ID` diagnosis, §7.3), a 422 for an unknown task, a typo'd `KITARU_TASK_ID`. An agent failure (provider 503) is never reworded as a recording failure.

**Import history** (importer path — backfill from Opik). Custom importer `opik@1` (`importers/opik_importer.py`) converts an Opik REST export (`{workspace, project, traces: [{trace, spans}]}`) into joined multi-turn sessions:

```bash
uv run kitaru importer list                       # opik@1 among the kitaru/* built-ins
uv run kitaru session import export.json \
  --importer opik@1 --agent decode@1 \
  --media-type application/json \
  --tag opik-backfill --wait
```

Payloads cap at 50 MiB — split by thread group, never a conversation across two files. Deduplicated on `provider + external_id`. An import is a job a Worker executes (§5) — start one first.

## 4. Judge, freeze, and encode a behavior

From your coding agent (`kitaru-investigation` skill) or by hand:

```bash
# review worklist; opens in the browser UI (worklists are FIXED at creation)
uv run kitaru investigation create my-discovery-1 --agent decode \
  --session <ID> --session-question '<ID>:observation=What do you notice? Did it match what should have happened?' \
  ...
```

The create result carries a `links.review` URL — verdicts (`acceptable` / `problematic`) and observations happen there. When a behavior is accepted:

```bash
# freeze the reviewed positives into an immutable population
uv run kitaru cohort create decode-bad-request-400 --agent decode --session <ID> [...]

# encode the criterion as a versioned evaluator (deterministic Python, SessionView → EvaluationResult)
uv run kitaru evaluator scaffold my-check --path evaluators/my_check.py
uv run kitaru evaluator test evaluators/my_check.py --entrypoint evaluate
uv run kitaru evaluator register my-check --script evaluators/my_check.py --entrypoint evaluate

# baseline sweep, no replay involved
uv run kitaru session evaluate --tag opik-backfill --evaluator 'my-check@1' --wait
```

Worked example in this repo: cohort `decode-bad-request-400@1` + evaluator `decode-bad-request-400@1` (`evaluators/decode_bad_request_400.py`) — flagged exactly the 2 crash sessions out of 38, zero false positives.

## 5. Start a Worker on your laptop (the thing that executes replays)

A Worker is a process you run; it claims replay / evaluator / importer tasks and spawns them with your credentials. The server only schedules.

A Worker spawns an **Agent Version** — the immutable run spec registered on the workspace. Register once per machine (adds a version to the existing `decode` agent, never a second agent):

```bash
uv run python scripts/register_kitaru_agent.py --dry-run   # prints the exact `kitaru agent version register` argv
uv run python scripts/register_kitaru_agent.py             # registers agent version 2
uv run kitaru agent version list decode
```

**Agent version 2** = `decode run` with no inline prompt (task arrives in `KITARU_TASK_INPUTS`), `SANDBOX_MODE=docker`, a fresh clone of this repo as Workspace, Harness Home `~/.decode-kitaru-worker` (outside your working tree). Watch it: `docker ps`, `tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log`. Docker Desktop must be up.

Start the Worker from a shell that has your provider credentials:

```bash
cd <repo root>                       # the checkout nests two same-named dirs — check pwd
set -a && source .env && set +a      # provider keys must be IN the worker's env
unset KITARU_AGENT_ID                # pitfall #3
uv run kitaru worker start --concurrency 10
```

It prints one `starting: {...}` line, then polls silently (2s). Verify from another terminal:

```bash
uv run kitaru worker list            # yours, live: True
```

**A replay's secrets = the Worker's env.** decode's Agent Versions attach no secret (`--secret-id`); a Worker layers a task's env on top of its own, so the sourced `.env` is what the replayed `decode run` sees, and no live key is copied onto the workspace ([ADR-0019 Amendments §2](../docs/adr/0019-kitaru-replay-runtime.md)). Not the Environment Bucket ([01_install_and_usage.md §6](01_install_and_usage.md#6-environments--decode_env-and-the-environment-bucket-optional)) — that fills `Settings` at a remote `DECODE_ENV`; unrelated. Consequence: a baseline replay reproduces the recorded run only if the Worker shell has the same `LLM_PROVIDER` / model config it recorded against.

**Worker on Modal instead:** [07_evals_replays_deploy.md](07_evals_replays_deploy.md). It spawns agent version 3, and each Worker can only run its own version.

## 6. Replay, then compare

A **Baseline Replay** (no `--override`) is the control: it proves the Session reproduces on the current Agent Version, so a later what-if is attributable to the change, not drift.

```bash
uv run kitaru replay create <BASELINE_SESSION_ID> \
  --agent decode@2 \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' \
  --evaluate-baselines
uv run kitaru job watch <JOB_ID>     # from the create output; seconds-to-minutes
uv run kitaru replay get <REPLAY_ID> # status + result_session_id
uv run kitaru session get <RESULT_SESSION_ID>
```

- `--evaluator` is **required**.
- `--tool-policy history` answers tool calls from the recording; `on_miss: error_result` keeps a cache miss from executing live (`passthrough` runs real bash — never default to it).
- `--evaluate-baselines` scores the original too → paired verdict.
- What-if fork: `--override '{"model": {"Qwen/...": "gemini-3.5-flash"}}'`.

**Compare in the UI** (dashboard from `kitaru status` → Agents → decode → Sessions):

1. A replay session's page carries a `from <baseline>` **Compare** link — inputs, tool policy, outputs, per-step timeline.
2. Any two sessions: tick two checkboxes → **Compare** in the bottom bar.
3. Direct URL: `<dashboard>/sessions/compare?left=<id>&right=<id>`.

Comparing two replays only means something when they share a baseline.

**At scale** — a whole cohort against one change:

```bash
uv run kitaru experiment create cheaper-model \
  --evaluator 'decode-bad-request-400@1' \
  --override '{"model": {"Qwen/Qwen3.6-35B-A3B-FP8": "gemini-3.5-flash"}}'
uv run kitaru experiment run start cheaper-model \
  --cohort-version <ID> --agent decode@2 --evaluate-baselines --wait
```

`--wait` exits non-zero on failure — usable as a CI gate. Designing a what-if: `kitaru-replay-experiment` skill.

## 7. Field notes — the pitfalls we actually hit

1. **`.env` sourced in the wrong directory.** The checkout nests two same-named dirs; sourcing in the outer one starts the worker keyless. Symptom: `Decode: set GEMINI_API_KEY in your environment`. `pwd` first.
2. **Worker env ≠ your shell env.** Provider keys reach the replayed decode only through the worker's own environment (agent v2 ships `secret_ids: []`). Export keys *before* `kitaru worker start`.
3. **`KITARU_AGENT_ID` under a Worker Task.** The worker injects a task-scoped token; probing an agents route with it → `403: Task credentials are not accepted on this route`. The Recording Seam now ignores a configured agent id whenever `KITARU_TASK_ID` is set ([ADR-0020 §11](../docs/adr/0020-remote-headless-on-modal.md)), so a sourced `.env` or a bucket carrying it no longer breaks a replay. Still `unset KITARU_AGENT_ID` in the worker shell out of hygiene; on Modal the Worker Secret omits it ([07 §2c](07_evals_replays_deploy.md#2c-the-decode-kitaru-worker-secret)). A 403 now means the Worker's own credential was refused.
4. **`Invalid arguments: --evaluator requires an argument`** on `replay create` = the flag is missing; it's required.
5. **`replay create` has no `--wait`** — use `kitaru job watch <job-id>`. (`--wait` exists on `session import` / `experiment run start`.)
6. **Import payload over 50 MiB** → split by thread group.
7. **A silent worker is a healthy worker.** Check `kitaru worker list`, not the terminal.
8. **An agent-level failure is still a working pipe.** `ModelHTTPError: 503` = worker claimed, spawned, ran decode; only the model was gone. `ModuleNotFoundError` / command-not-found = setup broken.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `[kitaru] not recording this run: … is unavailable` | workspace unreachable, or `KITARU_AGENT_ID` is not an agent on it. `uv run kitaru status`; re-auth with `kitaru login <url>`. Run itself is fine. |
| Records nothing, says nothing | `KITARU_AGENT_ID` empty, or `KITARU_API_URL` in `.env` but not exported. |
| Replay stays queued | no live Worker (`kitaru worker list`), or wrong Agent Version: v2 needs the laptop Worker, v3 the Modal one ([07](07_evals_replays_deploy.md)). |
| Replay fails at the first model request | Worker shell had no provider key. `set -a && . .env && set +a && kitaru worker start`. |
| Replay fails before the agent starts | docker daemon down (v2 pins `SANDBOX_MODE=docker`) or stale `--command` path after `make install`. Re-register: `uv run python scripts/register_kitaru_agent.py`. |
| `403: Task credentials are not accepted on this route` | `unset KITARU_AGENT_ID` in the Worker shell (§7.3). |

## Go further

- [07_evals_replays_deploy.md](07_evals_replays_deploy.md) — the Worker on Modal.
- [04_deploy.md](04_deploy.md) — headless harness on Modal; its Secret can carry the recording keys.
- [01_install_and_usage.md §6](01_install_and_usage.md#6-environments--decode_env-and-the-environment-bucket-optional) — the Environment Bucket.
- [ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md).
- Kitaru docs: https://docs.zenml.io/kitaru — sessions/replays/cohorts/experiments, tool policies, importers, workers in production.
